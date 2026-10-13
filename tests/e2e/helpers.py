"""Shared E2E helpers: Kafka publish, WS collect, config, maps-link parser.

Kept deliberately small. All network timeouts are generous; the point of the
suite is to exercise + measure, not to be flaky.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass

# ── config ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Config:
    base_url: str
    kafka: str
    username: str
    password: str
    verify_tls: bool
    live: bool            # True = run against an already-running stack
    # latency thresholds
    frame_latency_ms: float
    alert_latency_s: float
    dashboard_latency_s: float


def load_config() -> Config:
    base = os.environ.get("CIVILYTICS_BASE_URL")
    live = base is not None
    base = base or "https://192.168.1.8"
    # https → ws(s) base is derived per-call; kafka defaults to the host port
    kafka = os.environ.get("CIVILYTICS_KAFKA", "127.0.0.1:9092")
    return Config(
        base_url=base.rstrip("/"),
        kafka=kafka,
        username=os.environ.get("CIVILYTICS_USER", "admin"),
        password=os.environ.get("CIVILYTICS_PASS", "Civilytics5829!"),
        verify_tls=os.environ.get("CIVILYTICS_VERIFY_TLS", "0") in ("1", "true", "yes"),
        live=live,
        frame_latency_ms=float(os.environ.get("E2E_FRAME_LATENCY_MS", "5000")),
        alert_latency_s=float(os.environ.get("E2E_ALERT_LATENCY_S", "15")),
        dashboard_latency_s=float(os.environ.get("E2E_DASHBOARD_LATENCY_S", "20")),
    )


def ws_base(http_base: str) -> str:
    return http_base.replace("https://", "wss://").replace("http://", "ws://")


def ssl_ctx(verify: bool):
    if verify:
        return True
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── kafka ──────────────────────────────────────────────────────────────────
# The single-broker stack advertises its listener as `kafka:9092`, which does
# not resolve from the host. Map it to the bootstrap host in-process only — no
# /etc/hosts or stack change. Idempotent.

_ADV_HOST = os.environ.get("CIVILYTICS_KAFKA_ADV_HOST", "kafka")
_dns_patched = False


def patch_kafka_dns(target: str = "127.0.0.1") -> None:
    global _dns_patched
    if _dns_patched:
        return
    _orig = socket.getaddrinfo

    def _patched(host, *a, **k):
        if host == _ADV_HOST:
            host = target
        return _orig(host, *a, **k)

    socket.getaddrinfo = _patched
    _dns_patched = True


def publish(topic: str, message: dict, bootstrap: str) -> None:
    """Publish one JSON message to Kafka (sync wrapper)."""
    import aiokafka

    patch_kafka_dns(bootstrap.split(":")[0])

    async def _go():
        p = aiokafka.AIOKafkaProducer(
            bootstrap_servers=bootstrap,
            value_serializer=lambda v: json.dumps(v).encode(),
            request_timeout_ms=10000,
        )
        await p.start()
        try:
            await p.send_and_wait(topic, value=message)
        finally:
            await p.stop()

    asyncio.run(_go())


def kafka_reachable(bootstrap: str, timeout: float = 4.0) -> bool:
    host, _, port = bootstrap.partition(":")
    try:
        with socket.create_connection((host, int(port or 9092)), timeout=timeout):
            return True
    except OSError:
        return False


# ── websocket ────────────────────────────────────────────────────────────

def collect_ws(path: str, token: str, cfg: Config, duration: float,
               match_type: str | None = None) -> list[dict]:
    """Connect to /ws<path>, collect JSON frames for `duration`s, return dicts.

    Opens with subprotocols ["bearer", token] (the backend contract). Stops
    early once a frame with type==match_type arrives.
    """
    import websockets

    url = f"{ws_base(cfg.base_url)}/ws{path}"

    async def _go():
        out: list[dict] = []
        deadline = time.monotonic() + duration
        try:
            async with websockets.connect(
                url, subprotocols=["bearer", token],
                ssl=ssl_ctx(cfg.verify_tls) if url.startswith("wss") else None,
                open_timeout=10,
            ) as ws:
                while time.monotonic() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    out.append(msg)
                    if match_type and msg.get("type") == match_type:
                        break
        except Exception:
            pass
        return out

    return asyncio.run(_go())


# ── google maps link parser (port of frontend/src/lib/google-maps-link.ts) ──
# Backend has no equivalent, so we port the pure-function parser here to derive
# a latlng from a shared Maps URL and feed it to camera-create.

def _in_range(lat: float, lng: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lng <= 180


def parse_google_maps_latlng(url: str):
    url = (url or "").strip()
    patterns = [
        r"@(-?\d+\.\d+),(-?\d+\.\d+)",            # @lat,lng,<z>z
        r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",        # place form
        r"[?&](?:q|query|ll)=(-?\d+\.\d+),(-?\d+\.\d+)",  # query params
        r"^(-?\d+\.\d+),\s*(-?\d+\.\d+)$",        # bare "lat,lng"
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if _in_range(lat, lng):
                return {"lat": lat, "lng": lng}
    return None


def is_google_maps_short_link(url: str) -> bool:
    return bool(re.search(r"(maps\.app\.goo\.gl|goo\.gl/maps)", url or ""))
