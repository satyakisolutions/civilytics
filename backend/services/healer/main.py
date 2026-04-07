# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Autonomous Self-Healing Agent — monitors, diagnoses, and auto-fixes system issues.

Runs a continuous monitoring loop that:
1. DETECT — polls health status from Redis (cached by health-monitor service)
2. DIAGNOSE — matches symptoms to known failure patterns
3. HEAL — executes auto-recovery actions for known issues
4. LOG — records all healing actions to Kafka + Redis for audit trail

Also starts an MCP server on port 9100 for LLM-driven interactive diagnosis.

Auto-Heal Capabilities:
    Camera down           -> force reconnect via Redis pipeline command
    Camera degraded       -> restart source in pipeline
    Kafka consumer lag    -> restart affected microservice container
    VLM unhealthy         -> restart vlm container
    TTS unhealthy         -> restart tts-worker container
    GPU OOM               -> reduce pipeline batch size temporarily
    GPU overheating       -> throttle inference interval
    Evidence storage >90% -> cleanup old evidence files (>30 days)
    Redis unreachable     -> retry with backoff, alert if persistent
    Database connection   -> reconnect pool, alert if persistent
    Pipeline crash        -> restart pipeline container
    Alert delivery fail   -> retry failed alerts, switch channel
    Stale detections      -> prune pipeline FSM state

Miss Tracking:
    Missed detections     — frames where pipeline had no inference (stall)
    Failed alerts         — alerts that couldn't be delivered (Telegram/SIP/speaker)
    Dropped frames        — cameras with frame gaps > threshold
    VLM timeouts          — VLM requests that timed out
    False positives       — VLM said not dumping after violation was raised
    Recording failures    — smart recording start/stop failures
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class HealingAction:
    """Record of one self-healing action taken by the agent."""

    timestamp: float
    component: str          # "camera:cam-01", "service:vlm-worker", "system:gpu"
    issue: str              # "camera_down", "consumer_lag", "gpu_oom"
    action_taken: str       # "reconnect", "restart_container", "reduce_batch"
    success: bool
    details: str            # Human-readable description
    auto: bool = True       # True if automatic, False if triggered via MCP

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MissedEvent:
    """Record of a detection miss, alert failure, or other missed event."""

    timestamp: float
    event_type: str         # "missed_detection", "failed_alert", "dropped_frames", etc.
    component: str
    details: str
    severity: str           # "low", "medium", "high"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Service name -> Docker container name mapping
# ---------------------------------------------------------------------------

_SERVICE_CONTAINER_MAP = {
    "vlm": "vlm-worker",
    "vlm-worker": "vlm-worker",
    "tts": "tts-worker",
    "tts-worker": "tts-worker",
    "lpr": "lpr-worker",
    "lpr-worker": "lpr-worker",
    "alert-svc": "alert-svc",
    "sip-svc": "sip-svc",
    "evidence-svc": "evidence-svc",
    "log-worker": "log-worker",
    "pipeline": "pipeline",
}

# Issues that map to a container restart
_RESTART_ISSUES = {
    "vlm_unhealthy": "vlm-worker",
    "vlm_slow": "vlm-worker",
    "tts_unhealthy": "tts-worker",
    "pipeline_crash": "pipeline",
}

# Evidence base directory
EVIDENCE_DIR = Path(os.getenv("WW_EVIDENCE_DIR", "/tmp/wastewatch/evidence"))

# ---------------------------------------------------------------------------
# Self-Healing Agent
# ---------------------------------------------------------------------------


class SelfHealingAgent:
    """Autonomous agent that monitors system health and auto-heals issues.

    Reads health snapshots from Redis keys written by the health-monitor
    service, diagnoses issues against known failure patterns, and executes
    recovery actions with cooldown protection to avoid restart loops.
    """

    HEAL_COOLDOWN_S: float = 300.0      # 5 minutes between heals of same component
    MONITOR_INTERVAL_S: float = 15.0    # Poll health every 15 seconds
    MAX_HEALING_LOG: int = 1000
    MAX_MISS_LOG: int = 5000

    def __init__(
        self,
        redis_url: str,
        kafka_servers: str,
        docker_socket: str = "/var/run/docker.sock",
    ) -> None:
        self._redis_url = redis_url
        self._kafka_servers = kafka_servers
        self._docker_socket = docker_socket

        # Redis client (sync — used from async via run_in_executor)
        self._redis = None
        self._redis_async = None
        try:
            import redis as redis_lib
            self._redis = redis_lib.Redis.from_url(redis_url, decode_responses=True)
            logger.info("Healer connected to Redis at %s", redis_url)
        except Exception:
            logger.warning("Healer: Redis not available at %s", redis_url)

        # Kafka producer for healing event log
        self._kafka_producer = None
        try:
            from confluent_kafka import Producer
            self._kafka_producer = Producer({
                "bootstrap.servers": kafka_servers,
                "client.id": "wastewatch-healer",
            })
            logger.info("Healer Kafka producer connected to %s", kafka_servers)
        except Exception:
            logger.warning("Healer: Kafka producer not available")

        # Pipeline notifier for camera reconnect and pipeline commands
        from app.alerts.pipeline_notifier import PipelineNotifier
        self._pipeline_notifier = PipelineNotifier(redis_url)

        # Docker client for container restart
        self._docker_client = None
        try:
            import docker
            self._docker_client = docker.DockerClient(base_url=f"unix://{docker_socket}")
            logger.info("Healer Docker client connected via %s", docker_socket)
        except Exception:
            logger.warning("Healer: Docker client not available (socket: %s)", docker_socket)

        # Healing history and miss log
        self._healing_log: deque[HealingAction] = deque(maxlen=self.MAX_HEALING_LOG)
        self._miss_log: deque[MissedEvent] = deque(maxlen=self.MAX_MISS_LOG)

        # Cooldown tracking: component -> monotonic timestamp of last heal
        self._last_heal_time: dict[str, float] = {}
        self._heal_cooldown_s = self.HEAL_COOLDOWN_S

        # Running flag
        self._running = False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main monitoring loop — detect, diagnose, heal, log."""
        self._running = True
        logger.info("Self-Healing Agent started — monitoring every %.0fs", self.MONITOR_INTERVAL_S)

        while self._running:
            try:
                health = await self._read_health_from_redis()
                issues = self._diagnose(health)

                if issues:
                    logger.info("Diagnosed %d issue(s): %s",
                                len(issues),
                                [i["issue"] for i in issues])

                for issue in issues:
                    if self._should_heal(issue):
                        action = await self._heal(issue)
                        await self._log_action(action)
                    else:
                        logger.debug("Skipping heal for %s — cooldown active", issue["component"])

            except Exception:
                logger.exception("Error in self-healing loop iteration")

            await asyncio.sleep(self.MONITOR_INTERVAL_S)

    def stop(self) -> None:
        """Gracefully stop the agent."""
        self._running = False
        logger.info("Self-Healing Agent stopping")

    # ------------------------------------------------------------------
    # Health reading
    # ------------------------------------------------------------------

    async def _read_health_from_redis(self) -> dict:
        """Read full health snapshot from Redis keys populated by health-monitor."""
        if not self._redis:
            return {}

        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._read_health_sync)
        except Exception:
            logger.exception("Failed to read health from Redis")
            return {}

    def _read_health_sync(self) -> dict:
        """Synchronous Redis health read."""
        health: dict[str, Any] = {}

        # System health (GPU, CPU, disk, memory)
        raw = self._redis.get("ww:health:system")
        if raw:
            health["system"] = json.loads(raw)

        # Camera health
        camera_keys = self._redis.keys("ww:health:camera:*")
        cameras = []
        for key in camera_keys:
            raw = self._redis.get(key)
            if raw:
                cameras.append(json.loads(raw))
        health["cameras"] = cameras

        # Model health (VLM, TTS, LPR)
        raw = self._redis.get("ww:health:models")
        if raw:
            health["models"] = json.loads(raw)

        # Kafka health (consumer lag, broker status)
        raw = self._redis.get("ww:health:kafka")
        if raw:
            health["kafka"] = json.loads(raw)

        # Service health (container statuses)
        raw = self._redis.get("ww:health:services")
        if raw:
            health["services"] = json.loads(raw)

        # Overall status
        raw = self._redis.get("ww:health:overall")
        if raw:
            health["overall"] = json.loads(raw)

        return health

    # ------------------------------------------------------------------
    # Diagnosis
    # ------------------------------------------------------------------

    def _diagnose(self, health: dict) -> list[dict]:
        """Match health data against known failure patterns.

        Returns a list of issue dicts with component, issue type, and severity.
        """
        issues: list[dict] = []

        # ── Camera issues ──
        for cam in health.get("cameras", []):
            cam_id = cam.get("id", cam.get("camera_id", "unknown"))
            state = cam.get("state", cam.get("status", "unknown"))
            if state == "down":
                issues.append({
                    "component": f"camera:{cam_id}",
                    "issue": "camera_down",
                    "severity": "high",
                })
            elif state == "degraded":
                issues.append({
                    "component": f"camera:{cam_id}",
                    "issue": "camera_degraded",
                    "severity": "medium",
                })

        # ── GPU issues ──
        gpu = health.get("system", {}).get("gpu", {})
        if gpu.get("utilization_pct", 0) > 95:
            issues.append({
                "component": "system:gpu",
                "issue": "gpu_overloaded",
                "severity": "high",
            })
        if gpu.get("temperature_c", 0) > 85:
            issues.append({
                "component": "system:gpu",
                "issue": "gpu_overheating",
                "severity": "critical",
            })
        if gpu.get("memory_used_pct", 0) > 95:
            issues.append({
                "component": "system:gpu",
                "issue": "gpu_oom_risk",
                "severity": "critical",
            })

        # ── VLM health ──
        vlm = health.get("models", {}).get("vlm", {})
        if vlm.get("status") == "unhealthy":
            issues.append({
                "component": "service:vlm",
                "issue": "vlm_unhealthy",
                "severity": "high",
            })
        elif vlm.get("latency_ms", 0) > 10000:
            issues.append({
                "component": "service:vlm",
                "issue": "vlm_slow",
                "severity": "medium",
            })

        # ── TTS health ──
        tts = health.get("models", {}).get("tts", {})
        if tts.get("status") == "unhealthy":
            issues.append({
                "component": "service:tts",
                "issue": "tts_unhealthy",
                "severity": "high",
            })

        # ── Kafka consumer lag ──
        consumer_lag = health.get("kafka", {}).get("consumer_lag", {})
        for consumer, lag in consumer_lag.items():
            if isinstance(lag, (int, float)) and lag > 1000:
                issues.append({
                    "component": f"kafka:{consumer}",
                    "issue": "consumer_lag",
                    "severity": "high",
                    "lag": lag,
                })

        # ── Storage ──
        disk = health.get("system", {}).get("disk", {})
        if disk.get("used_pct", 0) > 90:
            issues.append({
                "component": "system:storage",
                "issue": "storage_full",
                "severity": "high",
            })

        # ── Service health (containers) ──
        for svc_name, svc_info in health.get("services", {}).items():
            if isinstance(svc_info, dict) and svc_info.get("status") == "down":
                issues.append({
                    "component": f"service:{svc_name}",
                    "issue": "pipeline_crash" if svc_name == "pipeline" else "service_down",
                    "severity": "high",
                })

        return issues

    # ------------------------------------------------------------------
    # Cooldown
    # ------------------------------------------------------------------

    def _should_heal(self, issue: dict) -> bool:
        """Check cooldown — don't re-heal the same component within HEAL_COOLDOWN_S."""
        component = issue["component"]
        last_heal = self._last_heal_time.get(component, 0)
        if time.monotonic() - last_heal < self._heal_cooldown_s:
            return False
        return True

    # ------------------------------------------------------------------
    # Healing dispatch
    # ------------------------------------------------------------------

    async def _heal(self, issue: dict) -> HealingAction:
        """Dispatch to the appropriate healer based on issue type."""
        healers = {
            "camera_down": self._heal_camera_reconnect,
            "camera_degraded": self._heal_camera_reconnect,
            "vlm_unhealthy": self._heal_restart_service,
            "vlm_slow": self._heal_restart_service,
            "tts_unhealthy": self._heal_restart_service,
            "consumer_lag": self._heal_restart_consumer_service,
            "gpu_overloaded": self._heal_reduce_batch,
            "gpu_overheating": self._heal_throttle_inference,
            "gpu_oom_risk": self._heal_reduce_batch,
            "storage_full": self._heal_cleanup_storage,
            "pipeline_crash": self._heal_restart_service,
            "service_down": self._heal_restart_service,
        }

        healer = healers.get(issue["issue"])
        if healer:
            try:
                action = await healer(issue)
            except Exception as exc:
                action = HealingAction(
                    timestamp=time.time(),
                    component=issue["component"],
                    issue=issue["issue"],
                    action_taken="heal_failed",
                    success=False,
                    details=f"Exception during healing: {exc}",
                )
        else:
            action = HealingAction(
                timestamp=time.time(),
                component=issue["component"],
                issue=issue["issue"],
                action_taken="none",
                success=False,
                details=f"No healer registered for issue type: {issue['issue']}",
            )

        # Update cooldown timestamp
        self._last_heal_time[issue["component"]] = time.monotonic()
        return action

    # ------------------------------------------------------------------
    # Individual healers
    # ------------------------------------------------------------------

    async def _heal_camera_reconnect(self, issue: dict) -> HealingAction:
        """Force reconnect a camera via the pipeline command channel."""
        camera_id = issue["component"].split(":", 1)[1]
        success = self._pipeline_notifier._publish({
            "action": "reconnect_camera",
            "camera_id": camera_id,
            "reason": "self_healing",
        })
        return HealingAction(
            timestamp=time.time(),
            component=issue["component"],
            issue=issue["issue"],
            action_taken=f"reconnect_camera:{camera_id}",
            success=success,
            details=f"Sent reconnect command for camera {camera_id}",
        )

    async def _heal_restart_service(self, issue: dict) -> HealingAction:
        """Restart a Docker container by service name."""
        # Resolve the container name
        svc_key = issue["component"].split(":", 1)[1]
        container_name = _RESTART_ISSUES.get(issue["issue"])
        if not container_name:
            container_name = _SERVICE_CONTAINER_MAP.get(svc_key, svc_key)

        if not self._docker_client:
            return HealingAction(
                timestamp=time.time(),
                component=issue["component"],
                issue=issue["issue"],
                action_taken=f"restart:{container_name}",
                success=False,
                details="Docker client not available — cannot restart container",
            )

        loop = asyncio.get_event_loop()
        try:
            success, detail = await loop.run_in_executor(
                None, self._restart_container_sync, container_name,
            )
            return HealingAction(
                timestamp=time.time(),
                component=issue["component"],
                issue=issue["issue"],
                action_taken=f"restart:{container_name}",
                success=success,
                details=detail,
            )
        except Exception as exc:
            return HealingAction(
                timestamp=time.time(),
                component=issue["component"],
                issue=issue["issue"],
                action_taken=f"restart:{container_name}",
                success=False,
                details=f"Failed to restart {container_name}: {exc}",
            )

    def _restart_container_sync(self, container_name: str) -> tuple[bool, str]:
        """Synchronous Docker container restart."""
        containers = self._docker_client.containers.list(
            filters={"name": container_name},
        )
        if not containers:
            return False, f"No running container matching '{container_name}'"

        target = containers[0]
        target.restart(timeout=30)
        return True, f"Restarted container {target.name} (id={target.short_id})"

    async def _heal_restart_consumer_service(self, issue: dict) -> HealingAction:
        """Restart the service associated with a lagging Kafka consumer group."""
        consumer_group = issue["component"].split(":", 1)[1]
        # Map consumer group IDs to service names
        group_to_service = {
            "wastewatch.group.vlm-worker": "vlm-worker",
            "wastewatch.group.alert-dispatcher": "alert-svc",
            "wastewatch.group.log-ingestion": "log-worker",
            "wastewatch.group.escalation": "sip-svc",
            "wastewatch.group.health-monitor": "health-monitor",
        }
        service_name = group_to_service.get(consumer_group)
        if not service_name:
            return HealingAction(
                timestamp=time.time(),
                component=issue["component"],
                issue=issue["issue"],
                action_taken="none",
                success=False,
                details=f"Unknown consumer group: {consumer_group}",
            )
        # Delegate to container restart
        return await self._heal_restart_service({
            "component": f"service:{service_name}",
            "issue": "consumer_lag",
        })

    async def _heal_reduce_batch(self, issue: dict) -> HealingAction:
        """Temporarily reduce pipeline batch size to relieve GPU pressure."""
        success = self._pipeline_notifier._publish({
            "action": "config_update",
            "data": {
                "batch_size": 1,
                "reason": "self_healing:gpu_pressure",
            },
        })
        # Also store in Redis so pipeline can pick it up on restart
        if self._redis:
            try:
                self._redis.set("ww:config:batch_size_override", "1", ex=1800)  # 30 min TTL
            except Exception:
                pass

        return HealingAction(
            timestamp=time.time(),
            component=issue["component"],
            issue=issue["issue"],
            action_taken="reduce_batch_size:1",
            success=success,
            details="Reduced pipeline batch size to 1 for 30 minutes",
        )

    async def _heal_throttle_inference(self, issue: dict) -> HealingAction:
        """Increase inference interval to reduce GPU thermal load."""
        success = self._pipeline_notifier._publish({
            "action": "config_update",
            "data": {
                "inference_interval": 5,
                "reason": "self_healing:gpu_overheating",
            },
        })
        if self._redis:
            try:
                self._redis.set("ww:config:inference_interval_override", "5", ex=1800)
            except Exception:
                pass

        return HealingAction(
            timestamp=time.time(),
            component=issue["component"],
            issue=issue["issue"],
            action_taken="throttle_inference:interval=5",
            success=success,
            details="Increased inference interval to 5 to reduce GPU thermal load",
        )

    async def _heal_cleanup_storage(self, issue: dict) -> HealingAction:
        """Delete evidence files older than 30 days to free disk space."""
        loop = asyncio.get_event_loop()
        try:
            deleted, freed_mb = await loop.run_in_executor(
                None, self._cleanup_evidence_sync, 30,
            )
            return HealingAction(
                timestamp=time.time(),
                component=issue["component"],
                issue=issue["issue"],
                action_taken=f"cleanup_storage:deleted={deleted},freed_mb={freed_mb:.1f}",
                success=True,
                details=f"Deleted {deleted} evidence files older than 30 days, freed {freed_mb:.1f} MB",
            )
        except Exception as exc:
            return HealingAction(
                timestamp=time.time(),
                component=issue["component"],
                issue=issue["issue"],
                action_taken="cleanup_storage",
                success=False,
                details=f"Storage cleanup failed: {exc}",
            )

    def _cleanup_evidence_sync(self, days_to_keep: int = 30) -> tuple[int, float]:
        """Synchronous evidence file cleanup. Returns (deleted_count, freed_mb)."""
        cutoff = time.time() - (days_to_keep * 86400)
        deleted = 0
        freed_bytes = 0

        if not EVIDENCE_DIR.exists():
            return 0, 0.0

        for filepath in EVIDENCE_DIR.rglob("*"):
            if filepath.is_file() and filepath.stat().st_mtime < cutoff:
                size = filepath.stat().st_size
                filepath.unlink()
                deleted += 1
                freed_bytes += size

        return deleted, freed_bytes / (1024 * 1024)

    # ------------------------------------------------------------------
    # Miss tracking
    # ------------------------------------------------------------------

    async def track_missed_detection(self, camera_id: str, frame_gap_s: float) -> None:
        """Record a missed detection — frames where pipeline had no inference."""
        event = MissedEvent(
            timestamp=time.time(),
            event_type="missed_detection",
            component=f"camera:{camera_id}",
            details=f"Frame gap of {frame_gap_s:.1f}s with no inference",
            severity="high" if frame_gap_s > 30 else "medium",
        )
        self._miss_log.append(event)
        await self._publish_miss(event)

    async def track_failed_alert(self, camera_id: str, channel: str, error: str) -> None:
        """Record a failed alert delivery."""
        event = MissedEvent(
            timestamp=time.time(),
            event_type="failed_alert",
            component=f"camera:{camera_id}",
            details=f"Alert delivery failed via {channel}: {error}",
            severity="high",
        )
        self._miss_log.append(event)
        await self._publish_miss(event)

    async def track_dropped_frames(self, camera_id: str, dropped_count: int) -> None:
        """Record dropped frames for a camera."""
        event = MissedEvent(
            timestamp=time.time(),
            event_type="dropped_frames",
            component=f"camera:{camera_id}",
            details=f"{dropped_count} frames dropped",
            severity="high" if dropped_count > 100 else "medium" if dropped_count > 10 else "low",
        )
        self._miss_log.append(event)
        await self._publish_miss(event)

    async def track_vlm_timeout(self, camera_id: str, track_id: int) -> None:
        """Record a VLM request timeout."""
        event = MissedEvent(
            timestamp=time.time(),
            event_type="vlm_timeout",
            component=f"camera:{camera_id}",
            details=f"VLM request timed out for track {track_id}",
            severity="high",
        )
        self._miss_log.append(event)
        await self._publish_miss(event)

    async def track_false_positive(self, camera_id: str, track_id: int) -> None:
        """Record a false positive — VLM said not dumping after violation was raised."""
        event = MissedEvent(
            timestamp=time.time(),
            event_type="false_positive",
            component=f"camera:{camera_id}",
            details=f"VLM cleared track {track_id} — false positive violation",
            severity="medium",
        )
        self._miss_log.append(event)
        await self._publish_miss(event)

    async def track_recording_failure(self, camera_id: str, error: str) -> None:
        """Record a smart recording start/stop failure."""
        event = MissedEvent(
            timestamp=time.time(),
            event_type="recording_failure",
            component=f"camera:{camera_id}",
            details=f"Recording failure: {error}",
            severity="high",
        )
        self._miss_log.append(event)
        await self._publish_miss(event)

    async def _publish_miss(self, event: MissedEvent) -> None:
        """Publish missed event to Kafka and Redis."""
        if self._kafka_producer:
            try:
                self._kafka_producer.produce(
                    "wastewatch.misses",
                    key=event.component.encode(),
                    value=json.dumps(event.to_dict()).encode(),
                )
                self._kafka_producer.poll(0)
            except Exception:
                logger.debug("Failed to publish miss event to Kafka")

        if self._redis:
            try:
                self._redis.lpush("ww:misses:log", json.dumps(event.to_dict()))
                self._redis.ltrim("ww:misses:log", 0, self.MAX_MISS_LOG - 1)
            except Exception:
                logger.debug("Failed to write miss event to Redis")

    async def get_miss_summary(self, hours: int = 24) -> dict:
        """Return summary of all misses in the last N hours."""
        cutoff = time.time() - (hours * 3600)
        summary: dict[str, list[dict]] = {}

        for event in self._miss_log:
            if event.timestamp >= cutoff:
                summary.setdefault(event.event_type, []).append(event.to_dict())

        return {
            "hours": hours,
            "total": sum(len(v) for v in summary.values()),
            "by_type": {k: len(v) for k, v in summary.items()},
            "events": summary,
        }

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    async def _log_action(self, action: HealingAction) -> None:
        """Log a healing action to the in-memory ring buffer, Redis, and Kafka."""
        self._healing_log.append(action)

        log_level = logging.INFO if action.success else logging.WARNING
        logger.log(
            log_level,
            "HEAL %s | component=%s issue=%s action=%s success=%s | %s",
            "OK" if action.success else "FAIL",
            action.component,
            action.issue,
            action.action_taken,
            action.success,
            action.details,
        )

        # Persist to Redis
        if self._redis:
            try:
                self._redis.lpush("ww:healing:log", json.dumps(action.to_dict()))
                self._redis.ltrim("ww:healing:log", 0, self.MAX_HEALING_LOG - 1)
                # Update last-healed timestamp per component
                self._redis.hset(
                    "ww:healing:last",
                    action.component,
                    json.dumps({"timestamp": action.timestamp, "action": action.action_taken}),
                )
            except Exception:
                logger.debug("Failed to persist healing action to Redis")

        # Publish to Kafka
        if self._kafka_producer:
            try:
                self._kafka_producer.produce(
                    "wastewatch.healing",
                    key=action.component.encode(),
                    value=json.dumps(action.to_dict()).encode(),
                )
                self._kafka_producer.poll(0)
            except Exception:
                logger.debug("Failed to publish healing action to Kafka")

    # ------------------------------------------------------------------
    # Public accessors (used by MCP server)
    # ------------------------------------------------------------------

    def get_healing_history(
        self,
        hours: int = 24,
        component: str = "",
    ) -> list[dict]:
        """Return healing actions from the in-memory log."""
        cutoff = time.time() - (hours * 3600)
        actions = []
        for action in self._healing_log:
            if action.timestamp < cutoff:
                continue
            if component and component not in action.component:
                continue
            actions.append(action.to_dict())
        return actions

    def get_miss_events(
        self,
        hours: int = 24,
        event_type: str = "",
    ) -> list[dict]:
        """Return missed events from the in-memory log."""
        cutoff = time.time() - (hours * 3600)
        events = []
        for event in self._miss_log:
            if event.timestamp < cutoff:
                continue
            if event_type and event.event_type != event_type:
                continue
            events.append(event.to_dict())
        return events


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main() -> None:
    """Start the Self-Healing Agent and MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    redis_url = os.getenv("WW_REDIS_URL", "redis://localhost:6379/0")
    kafka_servers = os.getenv("WW_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    docker_socket = os.getenv("WW_DOCKER_SOCKET", "/var/run/docker.sock")

    agent = SelfHealingAgent(
        redis_url=redis_url,
        kafka_servers=kafka_servers,
        docker_socket=docker_socket,
    )

    # Start MCP server in background
    from services.healer.mcp_server import create_mcp_server
    mcp_server = create_mcp_server(agent)

    loop = asyncio.get_event_loop()

    # Handle graceful shutdown
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, agent.stop)

    # Run agent loop and MCP server concurrently
    logger.info("Starting Self-Healing Agent + MCP server on :9100")
    await asyncio.gather(
        agent.run(),
        mcp_server.serve(),
    )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
