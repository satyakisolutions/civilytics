import asyncio
import logging
import orjson
from aiokafka import AIOKafkaConsumer
from app.config.kafka import GROUP_ALERT_DISPATCHER, TOPIC_VLM_RESULTS
from app.config.settings import settings
from app.alerts.throttle import AlertThrottle
from app.alerts.telegram import TelegramAlert
from app.alerts.websocket import push_alert_ws

logger = logging.getLogger(__name__)

# Severities that should always generate alerts when active dumping is detected
_ALWAYS_ALERT_SEVERITIES = {"critical", "high", "medium"}


class AlertDispatcher:
    def __init__(self, bootstrap_servers: str | None = None):
        self._bootstrap_servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._throttle = AlertThrottle()
        self._telegram = TelegramAlert()

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_VLM_RESULTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_ALERT_DISPATCHER,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("AlertDispatcher started — consuming %s", TOPIC_VLM_RESULTS)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        await self._throttle.close()
        await self._telegram.close()
        logger.info("AlertDispatcher stopped")

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                try:
                    await self._handle_message(msg.value)
                except Exception:
                    logger.exception("Error handling VLM result message")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("AlertDispatcher consumer loop error")

    async def _handle_message(self, payload: dict):
        camera_id: str = payload.get("camera_id", "unknown")
        severity: str = payload.get("severity", "info").lower()
        is_dumping: bool = payload.get("is_dumping", False)
        track_id: int | None = payload.get("track_id")
        dwell_time_s: float | None = payload.get("dwell_time_s")
        vlm_summary: str | None = payload.get("vlm_summary")
        plate_number: str | None = payload.get("plate_number")
        snapshot_url: str | None = payload.get("snapshot_url")
        message: str = payload.get("message", f"Suspicious activity detected at camera {camera_id}")

        # Skip low-signal "info" events unless active dumping is confirmed
        if severity == "info" and not is_dumping:
            logger.debug("Skipping info-level non-dumping event: camera=%s", camera_id)
            return

        # Throttle check
        cooldown_s: int = payload.get("cooldown_s", 60)
        max_per_hour: int = payload.get("max_per_hour", 10)
        if not await self._throttle.should_alert(
            camera_id, track_id=track_id, cooldown_s=cooldown_s, max_per_hour=max_per_hour
        ):
            logger.info("Alert throttled for camera=%s", camera_id)
            return

        # Record the alert before fan-out so concurrent messages are also throttled
        await self._throttle.record_alert(camera_id, cooldown_s=cooldown_s)

        logger.info(
            "Dispatching alert: camera=%s severity=%s is_dumping=%s",
            camera_id,
            severity,
            is_dumping,
        )

        # Fan-out: Telegram + WebSocket (run concurrently, failures are isolated)
        await asyncio.gather(
            self._telegram.send_alert(
                camera_id=camera_id,
                severity=severity,
                message=message,
                snapshot_url=snapshot_url,
                dwell_time_s=dwell_time_s,
                vlm_summary=vlm_summary,
                plate_number=plate_number,
            ),
            push_alert_ws(
                camera_id=camera_id,
                severity=severity,
                message=message,
                track_id=track_id,
                dwell_time_s=dwell_time_s,
                vlm_summary=vlm_summary,
                plate_number=plate_number,
                snapshot_url=snapshot_url,
            ),
            return_exceptions=True,
        )
