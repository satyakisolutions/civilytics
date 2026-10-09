# backend/app/analytics/worker.py
"""Kafka consumer: wastewatch.events → pre-compute analytics counters in Redis.

Consumes every event and maintains rolling counters so the /api/analytics/*
endpoints read from Redis (O(1)) instead of scanning the DB on every request.

Redis keys (all under analytics: prefix):
  analytics:events_today        — counter, reset daily via TTL
  analytics:events_this_week    — counter, reset weekly via TTL
  analytics:critical_today      — counter, reset daily via TTL
  analytics:resolved            — counter (lifetime)
  analytics:pending             — counter (lifetime)
  analytics:total               — counter (lifetime)
  analytics:zone:{zone_id}      — sorted set member score = event count
  analytics:hourly:{YYYYMMDDHH} — counter per hour bucket, 48h TTL
  analytics:daily:{YYYYMMDD}    — counter per day bucket, 10d TTL
  analytics:response_times      — list of response seconds (capped at 1000)
  analytics:repeat:{person_id}  — counter per person, 30d TTL
"""
import asyncio
import logging
import signal

import orjson
import redis.asyncio as redis
from aiokafka import AIOKafkaConsumer

from app.config.kafka import TOPIC_EVENTS, GROUP_ANALYTICS
from app.config.settings import settings

logger = logging.getLogger(__name__)

# ponytail: single Redis hash would be tidier but individual keys let us set per-key TTLs without a cron


class AnalyticsWorker:
    def __init__(self, bootstrap_servers: str, redis_url: str | None = None):
        self._bootstrap_servers = bootstrap_servers
        self._redis = redis.from_url(redis_url or settings.redis_url)
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_EVENTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_ANALYTICS,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume())
        logger.info("AnalyticsWorker started, consuming %s", TOPIC_EVENTS)

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._consumer:
            await self._consumer.stop()
        await self._redis.aclose()
        logger.info("AnalyticsWorker stopped")

    async def _consume(self):
        async for msg in self._consumer:
            try:
                await self._handle(msg.value)
            except Exception:
                logger.exception("Failed to handle analytics event")

    async def _handle(self, event: dict):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        pipe = self._redis.pipeline()

        # Total events
        pipe.incr("analytics:total")

        # Today counter (TTL = seconds until midnight + 1h buffer)
        seconds_to_midnight = (
            (24 - now.hour) * 3600 - now.minute * 60 - now.second
        )
        pipe.incr("analytics:events_today")
        pipe.expire("analytics:events_today", seconds_to_midnight + 3600)

        # This week counter (TTL = 7 days)
        pipe.incr("analytics:events_this_week")
        pipe.expire("analytics:events_this_week", 7 * 86400)

        # Critical today
        severity = event.get("severity", "")
        if severity == "critical":
            pipe.incr("analytics:critical_today")
            pipe.expire("analytics:critical_today", seconds_to_midnight + 3600)

        # Status counters
        status = event.get("status", "active")
        if status == "resolved":
            pipe.incr("analytics:resolved")
        elif status in ("active", "acknowledged"):
            pipe.incr("analytics:pending")

        # Hourly bucket
        hour_key = f"analytics:hourly:{now.strftime('%Y%m%d%H')}"
        pipe.incr(hour_key)
        pipe.expire(hour_key, 48 * 3600)

        # Daily bucket
        day_key = f"analytics:daily:{now.strftime('%Y%m%d')}"
        pipe.incr(day_key)
        pipe.expire(day_key, 10 * 86400)

        # Zone tracking
        zone = event.get("zone_name") or event.get("zone_id")
        if zone:
            pipe.zincrby("analytics:zones", 1, str(zone))

        # Repeat offender tracking
        person_id = event.get("person_id")
        if person_id:
            pipe.incr(f"analytics:repeat:{person_id}")
            pipe.expire(f"analytics:repeat:{person_id}", 30 * 86400)

        # Response time (if acknowledged)
        ack_at = event.get("acknowledged_at")
        ts = event.get("timestamp")
        if ack_at and ts:
            try:
                t_ack = datetime.fromisoformat(ack_at)
                t_event = datetime.fromisoformat(ts)
                delta_s = (t_ack - t_event).total_seconds()
                if delta_s > 0:
                    pipe.lpush("analytics:response_times", str(delta_s))
                    pipe.ltrim("analytics:response_times", 0, 999)
            except (ValueError, TypeError):
                pass

        await pipe.execute()
