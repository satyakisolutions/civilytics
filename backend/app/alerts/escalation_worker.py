# backend/app/alerts/escalation_worker.py
"""Alert escalation worker -- manages the time-based alert cascade.

Monitors unacknowledged violations and triggers escalation:
    T+0s    -> IP Speaker + WebSocket + Telegram (instant)
    T+60s   -> SIP call to primary operator
    T+120s  -> SIP call to zone supervisor
    T+300s  -> SIP call to admin / emergency contact

Consumes from wastewatch.alerts Kafka topic and tracks acknowledgment
status via Redis.

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import orjson
import redis.asyncio as redis
from aiokafka import AIOKafkaConsumer

from app.config.kafka import GROUP_ESCALATION, TOPIC_ALERTS
from app.config.settings import settings
from app.alerts.sip_gateway import EscalationLevel, SIPConfig, SIPGateway
from app.tts.engine import TTSEngine

logger = logging.getLogger(__name__)

# Redis key prefixes
_REDIS_ACK_PREFIX = "escalation:ack:"       # escalation:ack:{camera_id}:{track_id}
_REDIS_ACTIVE_PREFIX = "escalation:active:"  # escalation:active:{camera_id}:{track_id}

# How often the escalation loop checks for unacknowledged violations
_CHECK_INTERVAL_S = 10


@dataclass
class _ActiveViolation:
    """In-memory state for a violation being tracked for escalation."""

    camera_id: str
    track_id: int
    violation_time: float  # time.monotonic() when first seen
    severity: str
    message: str
    escalation_levels: list[EscalationLevel] = field(default_factory=list)


class EscalationWorker:
    """Kafka consumer + periodic loop that manages alert escalation.

    Lifecycle matches other workers in ``main.py``::

        worker = EscalationWorker(bootstrap_servers)
        await worker.start()
        ...
        await worker.stop()
    """

    def __init__(self, bootstrap_servers: str | None = None) -> None:
        self._bootstrap_servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._consume_task: asyncio.Task | None = None
        self._check_task: asyncio.Task | None = None
        self._redis = redis.from_url(settings.redis_url)

        # Active violations keyed by (camera_id, track_id)
        self._active: dict[tuple[str, int], _ActiveViolation] = {}

        # SIP gateway for placing escalation calls
        tts_engine = TTSEngine()
        sip_cfg = SIPConfig(
            sip_server=settings.sip_server,
            sip_username=settings.sip_username,
            sip_password=settings.sip_password,
            sip_domain=settings.sip_domain,
            twilio_account_sid=settings.twilio_account_sid,
            twilio_auth_token=settings.twilio_auth_token,
            twilio_from_number=settings.twilio_from_number,
            escalation_contacts=settings.sip_escalation_contacts,
            enabled=settings.sip_enabled,
        )
        self._sip_gateway = SIPGateway(config=sip_cfg, tts_engine=tts_engine)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start Kafka consumer and periodic escalation check loop."""
        self._consumer = AIOKafkaConsumer(
            TOPIC_ALERTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_ESCALATION,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._consume_task = asyncio.create_task(self._consume_loop())
        self._check_task = asyncio.create_task(self._escalation_check_loop())
        logger.info(
            "EscalationWorker started -- consuming %s, checking every %ds",
            TOPIC_ALERTS,
            _CHECK_INTERVAL_S,
        )

    async def stop(self) -> None:
        """Cancel tasks, close consumer, Redis, and SIP gateway."""
        for task in (self._consume_task, self._check_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._consumer:
            await self._consumer.stop()
        await self._redis.aclose()
        await self._sip_gateway.close()
        logger.info("EscalationWorker stopped")

    # ------------------------------------------------------------------
    # Kafka consume loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Read alert messages and register them for escalation tracking."""
        try:
            async for msg in self._consumer:  # type: ignore[union-attr]
                try:
                    await self._handle_alert(msg.value)
                except Exception:
                    logger.exception("Error handling alert message")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("EscalationWorker consume loop error")

    async def _handle_alert(self, payload: dict) -> None:
        """Register a new violation for escalation tracking.

        Expected payload keys (subset of VLM result / alert envelope):
        - camera_id: str
        - track_id: int | None
        - severity: str
        - message: str
        - is_dumping: bool
        """
        camera_id: str = payload.get("camera_id", "unknown")
        track_id: int = payload.get("track_id") or 0
        severity: str = payload.get("severity", "info").lower()
        message: str = payload.get("message", f"Violation at {camera_id}")
        is_dumping: bool = payload.get("is_dumping", False)

        # Only track medium+ severity events with active dumping
        if severity not in ("critical", "high", "medium") and not is_dumping:
            return

        key = (camera_id, track_id)
        if key in self._active:
            logger.debug("Violation already tracked: camera=%s track=%d", camera_id, track_id)
            return

        # Build fresh escalation levels for this violation
        escalation_levels = self._sip_gateway.get_escalation_levels()

        violation = _ActiveViolation(
            camera_id=camera_id,
            track_id=track_id,
            violation_time=time.monotonic(),
            severity=severity,
            message=message,
            escalation_levels=escalation_levels,
        )
        self._active[key] = violation

        # Mark active in Redis (with 10-minute TTL for crash recovery)
        redis_key = f"{_REDIS_ACTIVE_PREFIX}{camera_id}:{track_id}"
        await self._redis.set(redis_key, str(time.time()), ex=600)

        logger.info(
            "Tracking violation for escalation: camera=%s track=%d severity=%s",
            camera_id, track_id, severity,
        )

    # ------------------------------------------------------------------
    # Periodic escalation check
    # ------------------------------------------------------------------

    async def _escalation_check_loop(self) -> None:
        """Every ``_CHECK_INTERVAL_S`` seconds, check for unacknowledged violations."""
        try:
            while True:
                await asyncio.sleep(_CHECK_INTERVAL_S)
                await self._check_all_violations()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Escalation check loop error")

    async def _check_all_violations(self) -> None:
        """Iterate active violations and escalate where needed."""
        # Snapshot keys to avoid mutation during iteration
        keys = list(self._active.keys())
        to_remove: list[tuple[str, int]] = []

        for key in keys:
            violation = self._active.get(key)
            if violation is None:
                continue

            # Check acknowledgment in Redis
            ack_key = f"{_REDIS_ACK_PREFIX}{violation.camera_id}:{violation.track_id}"
            acknowledged = await self._redis.exists(ack_key)

            if acknowledged:
                logger.info(
                    "Violation acknowledged -- stopping escalation: camera=%s track=%d",
                    violation.camera_id, violation.track_id,
                )
                to_remove.append(key)
                continue

            # Check if all levels have been exhausted
            all_triggered = all(lv.triggered for lv in violation.escalation_levels)
            if all_triggered and violation.escalation_levels:
                elapsed = time.monotonic() - violation.violation_time
                # Keep for 10 more minutes after last escalation, then clean up
                max_delay = max(lv.delay_s for lv in violation.escalation_levels)
                if elapsed > max_delay + 600:
                    logger.info(
                        "Violation expired (all escalations sent): camera=%s track=%d",
                        violation.camera_id, violation.track_id,
                    )
                    to_remove.append(key)
                    continue

            # Run escalation check via SIP gateway
            try:
                result = await self._sip_gateway.check_escalation(
                    camera_id=violation.camera_id,
                    track_id=violation.track_id,
                    violation_time=violation.violation_time,
                    acknowledged=False,
                    escalation_state=violation.escalation_levels,
                )
                if result.get("escalated"):
                    logger.warning(
                        "Escalation fired: camera=%s track=%d level=%s contact=%s",
                        violation.camera_id,
                        violation.track_id,
                        result.get("level"),
                        result.get("contact"),
                    )
            except Exception:
                logger.exception(
                    "Escalation check failed: camera=%s track=%d",
                    violation.camera_id, violation.track_id,
                )

        # Clean up resolved/expired violations
        for key in to_remove:
            self._active.pop(key, None)
            redis_key = f"{_REDIS_ACTIVE_PREFIX}{key[0]}:{key[1]}"
            await self._redis.delete(redis_key)

    # ------------------------------------------------------------------
    # Public: acknowledge an alert (called from REST API / WebSocket)
    # ------------------------------------------------------------------

    @staticmethod
    async def acknowledge_alert(
        camera_id: str,
        track_id: int,
        redis_url: str | None = None,
    ) -> bool:
        """Mark a violation as acknowledged in Redis.

        This is a static helper so that API routes can call it without
        holding a reference to the worker instance.
        """
        r = redis.from_url(redis_url or settings.redis_url)
        try:
            ack_key = f"{_REDIS_ACK_PREFIX}{camera_id}:{track_id}"
            await r.set(ack_key, "1", ex=3600)  # 1-hour TTL
            logger.info("Alert acknowledged: camera=%s track=%d", camera_id, track_id)
            return True
        finally:
            await r.aclose()
