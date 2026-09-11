# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""System Health Monitor — tracks all service/device connections and system resources.

Runs periodic health checks every 10 seconds and publishes status to:
- Kafka: wastewatch.health (for dashboard consumption)
- Redis: ww:health:{component} (for fast API queries)

Checks:
    Input streams    — RTSP probe per camera, frame rate from pipeline metadata
    Output streams   — MediaMTX API query per camera output path
    IP speakers      — HTTP/TCP probe per configured speaker
    VLM service      — vLLM /health endpoint + inference latency
    TTS service      — Piper health check
    LPR service      — PaddleOCR readiness
    System           — CPU, RAM, GPU (nvidia-smi), disk
    Kafka            — broker connectivity, consumer lag
    Redis            — PING + latency
    PostgreSQL       — connection test
    Qdrant           — collection status
    Pipeline         — active sources, FPS, GStreamer state (via Redis)
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import psutil
import redis.asyncio as aioredis

from app.config.kafka import TOPIC_HEALTH
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.health-monitor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHECK_INTERVAL_S = 10
PROBE_TIMEOUT_S = 5
REDIS_HEALTH_PREFIX = "ww:health"
REDIS_HEALTH_TTL_S = 30  # keys expire if monitor stops

# Status values
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_DOWN = "down"
STATUS_UNKNOWN = "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# HealthMonitor
# ---------------------------------------------------------------------------
class HealthMonitor:
    """Runs periodic health checks and publishes results to Kafka + Redis."""

    def __init__(self) -> None:
        self._running = False
        self._redis: aioredis.Redis | None = None
        self._http: httpx.AsyncClient | None = None
        self._kafka_producer = None

    async def _init_clients(self) -> None:
        """Initialise Redis, HTTP, and Kafka clients."""
        # Redis
        try:
            self._redis = aioredis.from_url(
                settings.redis_url, decode_responses=True,
            )
            await self._redis.ping()
            logger.info("Redis connected")
        except Exception:
            logger.warning("Redis not reachable at startup — will retry")
            self._redis = None

        # httpx
        self._http = httpx.AsyncClient(timeout=PROBE_TIMEOUT_S)

        # Kafka producer (aiokafka)
        try:
            from aiokafka import AIOKafkaProducer
            self._kafka_producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode(),
            )
            await self._kafka_producer.start()
            logger.info("Kafka producer started")
        except Exception:
            logger.warning("Kafka producer failed to start — health events will be Redis-only")
            self._kafka_producer = None

    async def _close_clients(self) -> None:
        if self._http:
            await self._http.aclose()
        if self._kafka_producer:
            await self._kafka_producer.stop()
        if self._redis:
            await self._redis.aclose()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Start the periodic health check loop."""
        self._running = True
        await self._init_clients()
        logger.info("Health monitor running — interval=%ds", CHECK_INTERVAL_S)

        while self._running:
            try:
                status = await self.check_all()
                await self._publish_kafka(status)
                await self._cache_redis(status)
            except Exception:
                logger.exception("Health check cycle failed")

            await asyncio.sleep(CHECK_INTERVAL_S)

        await self._close_clients()

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Aggregate check
    # ------------------------------------------------------------------
    async def check_all(self) -> dict[str, Any]:
        """Run all checks concurrently and return full status dict."""
        results = await asyncio.gather(
            self.check_input_streams(),
            self.check_output_streams(),
            self.check_ip_speakers(),
            self.check_vlm(),
            self.check_tts(),
            self.check_lpr(),
            self.check_system(),
            self.check_kafka(),
            self.check_redis(),
            self.check_postgres(),
            self.check_qdrant(),
            self.check_pipeline(),
            return_exceptions=True,
        )

        labels = [
            "input_streams", "output_streams", "ip_speakers",
            "vlm", "tts", "lpr",
            "system", "kafka", "redis", "postgres", "qdrant", "pipeline",
        ]

        status: dict[str, Any] = {"timestamp": _now_iso()}
        for label, result in zip(labels, results):
            if isinstance(result, BaseException):
                logger.warning("Check %s raised: %s", label, result)
                status[label] = {"status": STATUS_DOWN, "error": str(result)}
            else:
                status[label] = result

        # Compute overall status
        component_statuses = []
        for label in labels:
            entry = status.get(label)
            if isinstance(entry, dict):
                component_statuses.append(entry.get("status", STATUS_UNKNOWN))
            elif isinstance(entry, list):
                for item in entry:
                    if isinstance(item, dict):
                        component_statuses.append(item.get("status", STATUS_UNKNOWN))

        if STATUS_DOWN in component_statuses:
            status["overall"] = "critical"
        elif STATUS_DEGRADED in component_statuses:
            status["overall"] = STATUS_DEGRADED
        elif STATUS_UNKNOWN in component_statuses:
            # A genuinely-unknown subsystem (e.g. check_postgres when
            # unconfigured, or an ambiguous check_lpr/check_pipeline
            # failure) is not the same as healthy — don't let it hide
            # silently behind an all-clear overall status.
            status["overall"] = STATUS_DEGRADED
        else:
            status["overall"] = STATUS_HEALTHY

        return status

    # ------------------------------------------------------------------
    # Input streams (RTSP)
    # ------------------------------------------------------------------
    async def check_input_streams(self) -> list[dict[str, Any]]:
        """Check each camera's RTSP input via TCP probe + last-frame metadata."""
        cameras = await self._get_cameras_from_redis()
        if not cameras:
            return []

        async def _probe_one(cam: dict) -> dict[str, Any]:
            cam_id = cam.get("id", "")
            uri = cam.get("stream_url", "")
            result: dict[str, Any] = {
                "camera_id": cam_id,
                "name": cam.get("name", ""),
                "uri": uri,
                "status": STATUS_UNKNOWN,
            }

            # TCP probe to RTSP port
            if uri:
                try:
                    host, port = self._parse_rtsp_host_port(uri)
                    reachable = await self._tcp_probe(host, port)
                    result["rtsp_reachable"] = reachable
                except Exception as exc:
                    result["rtsp_reachable"] = False
                    result["error"] = str(exc)

            # Last frame timestamp from Redis
            if self._redis:
                try:
                    ts = await self._redis.get(f"ww:camera:{cam_id}:last_frame_ts")
                    if ts:
                        last_ts = float(ts)
                        age = time.time() - last_ts
                        result["last_frame_age_s"] = round(age, 2)
                        result["fps"] = await self._get_camera_fps(cam_id)
                except Exception:
                    pass

            # Determine status
            rtsp_ok = result.get("rtsp_reachable", False)
            age = result.get("last_frame_age_s")
            if rtsp_ok and age is not None and age < 5:
                result["status"] = STATUS_HEALTHY
            elif rtsp_ok:
                result["status"] = STATUS_DEGRADED
            else:
                result["status"] = STATUS_DOWN

            return result

        return await asyncio.gather(*[_probe_one(c) for c in cameras])

    # ------------------------------------------------------------------
    # Output streams (MediaMTX)
    # ------------------------------------------------------------------
    async def check_output_streams(self) -> list[dict[str, Any]]:
        """Query MediaMTX API for output path status per camera."""
        results: list[dict[str, Any]] = []
        try:
            resp = await self._http.get(
                f"{settings.mediamtx_api_url}/v3/paths/list",
            )
            if resp.status_code != 200:
                return [{"status": STATUS_DOWN, "error": f"MediaMTX API returned {resp.status_code}"}]

            data = resp.json()
            items = data.get("items", [])
            for item in items:
                path_name = item.get("name", "")
                readers = item.get("readers", [])
                source = item.get("source")
                results.append({
                    "path": path_name,
                    "source_ready": source is not None,
                    "reader_count": len(readers) if readers else 0,
                    "status": STATUS_HEALTHY if source else STATUS_DOWN,
                })
        except httpx.TimeoutException:
            results.append({"status": STATUS_DOWN, "error": "MediaMTX API timeout"})
        except Exception as exc:
            results.append({"status": STATUS_DOWN, "error": str(exc)})

        return results

    # ------------------------------------------------------------------
    # IP speakers
    # ------------------------------------------------------------------
    async def check_ip_speakers(self) -> list[dict[str, Any]]:
        """Probe each configured IP speaker via HTTP HEAD or TCP."""
        speakers = settings.ip_speaker_configs
        if not speakers:
            return []

        async def _probe_speaker(spk: dict) -> dict[str, Any]:
            endpoint = spk.get("endpoint", spk.get("url", ""))
            name = spk.get("name", endpoint)
            result: dict[str, Any] = {"name": name, "endpoint": endpoint}
            try:
                if endpoint.startswith("http"):
                    resp = await self._http.head(endpoint)
                    result["status"] = STATUS_HEALTHY if resp.status_code < 500 else STATUS_DOWN
                    result["status_code"] = resp.status_code
                else:
                    host, port = endpoint.rsplit(":", 1)
                    reachable = await self._tcp_probe(host.strip("/"), int(port))
                    result["status"] = STATUS_HEALTHY if reachable else STATUS_DOWN
            except Exception as exc:
                result["status"] = STATUS_DOWN
                result["error"] = str(exc)
            return result

        return await asyncio.gather(*[_probe_speaker(s) for s in speakers])

    # ------------------------------------------------------------------
    # VLM (vLLM)
    # ------------------------------------------------------------------
    async def check_vlm(self) -> dict[str, Any]:
        """Check vLLM /health endpoint and measure response latency."""
        base = settings.vlm_base_url.rstrip("/v1").rstrip("/")
        t0 = time.monotonic()
        try:
            resp = await self._http.get(f"{base}/health")
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            if resp.status_code == 200:
                status = STATUS_DEGRADED if latency_ms > 5000 else STATUS_HEALTHY
            else:
                status = STATUS_DOWN
            return {
                "status": status,
                "latency_ms": latency_ms,
                "http_status": resp.status_code,
            }
        except Exception as exc:
            return {"status": STATUS_DOWN, "error": str(exc)}

    # ------------------------------------------------------------------
    # TTS (Piper)
    # ------------------------------------------------------------------
    async def check_tts(self) -> dict[str, Any]:
        """Check TTS service responsiveness."""
        try:
            resp = await self._http.get("http://tts:8100/")
            if resp.status_code < 500:
                return {"status": STATUS_HEALTHY, "http_status": resp.status_code}
            return {"status": STATUS_DOWN, "http_status": resp.status_code}
        except Exception as exc:
            return {"status": STATUS_DOWN, "error": str(exc)}

    # ------------------------------------------------------------------
    # LPR (PaddleOCR)
    # ------------------------------------------------------------------
    async def check_lpr(self) -> dict[str, Any]:
        """Check PaddleOCR / LPR service readiness."""
        try:
            resp = await self._http.get("http://lpr-worker:8200/health")
            if resp.status_code == 200:
                return {"status": STATUS_HEALTHY}
            return {"status": STATUS_DEGRADED, "http_status": resp.status_code}
        except Exception as exc:
            # LPR may not expose an HTTP endpoint; check via Redis marker
            if self._redis:
                try:
                    last_ts = await self._redis.get("ww:lpr:last_active")
                    if last_ts and (time.time() - float(last_ts)) < 60:
                        return {"status": STATUS_HEALTHY, "last_active_age_s": round(time.time() - float(last_ts), 1)}
                except Exception:
                    pass
            return {"status": STATUS_UNKNOWN, "error": str(exc)}

    # ------------------------------------------------------------------
    # System resources
    # ------------------------------------------------------------------
    async def check_system(self) -> dict[str, Any]:
        """Check CPU, RAM, GPU, and disk usage."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._check_system_sync)

    def _check_system_sync(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": STATUS_HEALTHY}

        # CPU
        try:
            # interval=None (delta since the last call) instead of
            # interval=1 — this already runs off the event loop via
            # run_in_executor, but interval=1 still blocked that executor
            # thread for a full second every cycle for no reason.
            result["cpu_percent"] = psutil.cpu_percent(interval=None)
        except Exception:
            result["cpu_percent"] = None

        # RAM
        try:
            ram = psutil.virtual_memory()
            result["ram_total_gb"] = round(ram.total / (1024 ** 3), 1)
            result["ram_used_gb"] = round(ram.used / (1024 ** 3), 1)
            result["ram_percent"] = ram.percent
        except Exception:
            pass

        # Disk
        try:
            disk = psutil.disk_usage("/")
            result["disk_total_gb"] = round(disk.total / (1024 ** 3), 1)
            result["disk_used_gb"] = round(disk.used / (1024 ** 3), 1)
            result["disk_percent"] = disk.percent
        except Exception:
            pass

        # GPU via nvidia-smi
        try:
            gpu_result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,nounits,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if gpu_result.returncode == 0:
                gpus = []
                for line in gpu_result.stdout.strip().split("\n"):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        gpus.append({
                            "utilization_percent": int(parts[0]),
                            "vram_used_mb": int(parts[1]),
                            "vram_total_mb": int(parts[2]),
                            "temperature_c": int(parts[3]),
                        })
                result["gpus"] = gpus
            else:
                result["gpus"] = []
                result["gpu_error"] = gpu_result.stderr.strip()
        except FileNotFoundError:
            result["gpus"] = []
            result["gpu_error"] = "nvidia-smi not found"
        except Exception as exc:
            result["gpus"] = []
            result["gpu_error"] = str(exc)

        # Degrade if resources are strained
        cpu = result.get("cpu_percent", 0) or 0
        ram_pct = result.get("ram_percent", 0) or 0
        if cpu > 90 or ram_pct > 90:
            result["status"] = STATUS_DEGRADED
        for gpu in result.get("gpus", []):
            if gpu.get("temperature_c", 0) > 85:
                result["status"] = STATUS_DEGRADED

        return result

    # ------------------------------------------------------------------
    # Kafka
    # ------------------------------------------------------------------
    async def check_kafka(self) -> dict[str, Any]:
        """Check Kafka broker connectivity."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._check_kafka_sync)

    def _check_kafka_sync(self) -> dict[str, Any]:
        try:
            host, port_str = settings.kafka_bootstrap_servers.split(":")
            port = int(port_str)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(PROBE_TIMEOUT_S)
            sock.connect((host, port))
            sock.close()
            return {"status": STATUS_HEALTHY, "broker": settings.kafka_bootstrap_servers}
        except Exception as exc:
            return {"status": STATUS_DOWN, "error": str(exc)}

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    async def check_redis(self) -> dict[str, Any]:
        """PING Redis and measure RTT."""
        if not self._redis:
            try:
                self._redis = aioredis.from_url(
                    settings.redis_url, decode_responses=True,
                )
            except Exception as exc:
                return {"status": STATUS_DOWN, "error": str(exc)}

        t0 = time.monotonic()
        try:
            pong = await asyncio.wait_for(self._redis.ping(), timeout=PROBE_TIMEOUT_S)
            latency_ms = round((time.monotonic() - t0) * 1000, 1)
            return {
                "status": STATUS_HEALTHY if pong else STATUS_DOWN,
                "latency_ms": latency_ms,
            }
        except Exception as exc:
            return {"status": STATUS_DOWN, "error": str(exc)}

    # ------------------------------------------------------------------
    # PostgreSQL
    # ------------------------------------------------------------------
    async def check_postgres(self) -> dict[str, Any]:
        """Try a simple SELECT 1 against the database.

        Uses asyncpg directly rather than a sync SQLAlchemy engine --
        the sync path defaults to the psycopg2 driver, which isn't
        installed anywhere in this codebase (everything else uses the
        asyncpg driver exclusively, via `+asyncpg` URLs), so it always
        failed with "No module named 'psycopg2'" instead of reporting
        real postgres status.
        """
        if not settings.database_url:
            return {"status": STATUS_UNKNOWN, "error": "database_url not configured"}

        try:
            import asyncpg
            dsn = settings.database_url.replace("postgresql+asyncpg", "postgresql").replace("postgresql+psycopg", "postgresql")
            conn = await asyncpg.connect(dsn, timeout=5)
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
            return {"status": STATUS_HEALTHY}
        except Exception as exc:
            return {"status": STATUS_DOWN, "error": str(exc)}

    # ------------------------------------------------------------------
    # Qdrant
    # ------------------------------------------------------------------
    async def check_qdrant(self) -> dict[str, Any]:
        """GET /collections from Qdrant."""
        try:
            resp = await self._http.get("http://qdrant:6333/collections")
            if resp.status_code == 200:
                data = resp.json()
                collections = data.get("result", {}).get("collections", [])
                return {
                    "status": STATUS_HEALTHY,
                    "collection_count": len(collections),
                }
            return {"status": STATUS_DOWN, "http_status": resp.status_code}
        except Exception as exc:
            return {"status": STATUS_DOWN, "error": str(exc)}

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------
    async def check_pipeline(self) -> dict[str, Any]:
        """Read pipeline status from Redis keys published by the pipeline."""
        if not self._redis:
            return {"status": STATUS_UNKNOWN, "error": "Redis not available"}

        try:
            pipeline_status = await self._redis.get("ww:pipeline:status")
            active_sources = await self._redis.get("ww:pipeline:active_sources")
            fps = await self._redis.get("ww:pipeline:fps")

            if pipeline_status:
                data = json.loads(pipeline_status) if isinstance(pipeline_status, str) else {}
                state = data.get("state", STATUS_UNKNOWN)
            else:
                state = STATUS_UNKNOWN

            result: dict[str, Any] = {
                "state": state,
                "active_sources": int(active_sources) if active_sources else 0,
                "fps": float(fps) if fps else 0.0,
            }

            if state in ("running", STATUS_HEALTHY):
                result["status"] = STATUS_HEALTHY
            elif state == STATUS_DEGRADED:
                result["status"] = STATUS_DEGRADED
            elif state == STATUS_UNKNOWN:
                result["status"] = STATUS_UNKNOWN
            else:
                result["status"] = STATUS_DOWN

            return result
        except Exception as exc:
            return {"status": STATUS_DOWN, "error": str(exc)}

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------
    async def _publish_kafka(self, status: dict[str, Any]) -> None:
        """Publish full status snapshot to Kafka."""
        if not self._kafka_producer:
            return
        try:
            await self._kafka_producer.send_and_wait(TOPIC_HEALTH, status)
        except Exception:
            logger.warning("Failed to publish health to Kafka", exc_info=True)

    async def _cache_redis(self, status: dict[str, Any]) -> None:
        """Cache component statuses in Redis for fast API reads."""
        if not self._redis:
            return
        try:
            pipe = self._redis.pipeline()
            for component, data in status.items():
                if component in ("timestamp", "overall"):
                    continue
                key = f"{REDIS_HEALTH_PREFIX}:{component}"
                pipe.set(key, json.dumps(data), ex=REDIS_HEALTH_TTL_S)

            # Also store the overall status and timestamp
            pipe.set(
                f"{REDIS_HEALTH_PREFIX}:overall",
                json.dumps({"status": status.get("overall", STATUS_UNKNOWN), "timestamp": status.get("timestamp")}),
                ex=REDIS_HEALTH_TTL_S,
            )
            await pipe.execute()
        except Exception:
            logger.warning("Failed to cache health in Redis", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _get_cameras_from_redis(self) -> list[dict[str, Any]]:
        """Get camera list from Redis cache (set by the API server)."""
        if not self._redis:
            return []
        try:
            data = await self._redis.get("ww:cameras:list")
            if data:
                return json.loads(data)
        except Exception:
            pass
        return []

    async def _get_camera_fps(self, camera_id: str) -> float | None:
        """Read FPS from pipeline metadata stored in Redis."""
        if not self._redis:
            return None
        try:
            fps = await self._redis.get(f"ww:camera:{camera_id}:fps")
            return float(fps) if fps else None
        except Exception:
            return None

    @staticmethod
    def _parse_rtsp_host_port(uri: str) -> tuple[str, int]:
        """Extract host and port from an RTSP URI (default port 554)."""
        # rtsp://user:pass@host:port/path
        stripped = uri.split("://", 1)[-1]
        # Remove credentials
        if "@" in stripped:
            stripped = stripped.split("@", 1)[-1]
        # Remove path
        host_port = stripped.split("/", 1)[0]
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            return host, int(port_str)
        return host_port, 554

    @staticmethod
    async def _tcp_probe(host: str, port: int) -> bool:
        """Async TCP connect probe with timeout."""
        loop = asyncio.get_running_loop()
        try:
            fut = loop.create_connection(asyncio.Protocol, host, port)
            transport, _ = await asyncio.wait_for(fut, timeout=PROBE_TIMEOUT_S)
            transport.close()
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
async def main() -> None:
    logger.info("Starting System Health Monitor")
    monitor = HealthMonitor()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    # Run monitor in background, wait for shutdown signal
    monitor_task = asyncio.create_task(monitor.run())

    await shutdown.wait()
    logger.info("Shutting down health monitor")
    monitor.stop()
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass
    logger.info("Health monitor stopped")


if __name__ == "__main__":
    asyncio.run(main())
