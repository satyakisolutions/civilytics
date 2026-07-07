import asyncio
import logging
import orjson
from aiokafka import AIOKafkaConsumer
from app.config.kafka import GROUP_ALERT_DISPATCHER, TOPIC_VLM_RESULTS
from app.config.settings import settings
from app.alerts.throttle import AlertThrottle
from app.alerts.telegram import TelegramAlert
from app.alerts.websocket import push_alert_ws
from app.alerts.ip_speaker import IPSpeakerManager
from app.alerts.pipeline_notifier import PipelineNotifier

logger = logging.getLogger(__name__)

# Severities that should always generate alerts when active dumping is detected
_ALWAYS_ALERT_SEVERITIES = {"critical", "high", "medium"}


class AlertDispatcher:
    def __init__(
        self,
        bootstrap_servers: str | None = None,
        ip_speaker_manager: IPSpeakerManager | None = None,
        pipeline_notifier: PipelineNotifier | None = None,
    ):
        self._bootstrap_servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._throttle = AlertThrottle()
        self._telegram = TelegramAlert()
        self._ip_speaker = ip_speaker_manager or IPSpeakerManager()
        self._pipeline = pipeline_notifier or PipelineNotifier(settings.redis_url)

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
        await self._ip_speaker.close()
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
        # VLM result payload nests detection fields under `response` —
        # read from there first, fall back to top-level for legacy senders.
        _resp = payload.get("response") or {}
        camera_id: str = payload.get("camera_id", "unknown")
        severity: str = str(_resp.get("severity") or payload.get("severity", "info")).lower()
        is_dumping: bool = bool(_resp.get("is_dumping", payload.get("is_dumping", False)))
        track_id: int | None = payload.get("track_id")
        dwell_time_s: float | None = (payload.get("track_context") or {}).get("dwell_time_s") or payload.get("dwell_time_s")
        vlm_summary: str | None = _resp.get("reasoning") or payload.get("vlm_summary")
        plate_number: str | None = payload.get("plate_number")
        snapshot_url: str | None = payload.get("snapshot_url")
        message: str = _resp.get("deterrent_message") or payload.get("message") or f"Suspicious activity detected at camera {camera_id}"

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

        # --- Stage 3 enrichment: push VLM result to WebSocket metadata stream ---
        # The pipeline (stage 1+2) fires recording, VLM request, SIP, etc.
        # The backend (stage 3) receives VLM results HERE and pushes enrichment
        # to the frontend so the bbox overlay shows VLM confirmation + waste type.
        vlm_enrichment = {
            "type": "vlm_enrichment",
            "camera_id": camera_id,
            "track_id": track_id,
            "is_dumping": is_dumping,
            "severity": severity,
            "waste_type": payload.get("waste_type", "unknown"),
            "vlm_confidence": payload.get("confidence", 0.0),
            "vlm_summary": vlm_summary,
            "plate_number": plate_number,
            "reasoning": payload.get("reasoning", ""),
        }

        # Fan-out: Telegram + WebSocket alert + WebSocket enrichment + IP Speaker
        # SIP calls are NOT in the immediate fan-out — handled by EscalationWorker
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
            # Push VLM enrichment to the metadata WebSocket so the frontend
            # bbox overlay can show VLM result per detection (stage 3)
            push_alert_ws(
                camera_id=camera_id,
                severity=severity,
                message="vlm_enrichment",
                track_id=track_id,
                **vlm_enrichment,
            ),
            self._ip_speaker.play_alert(
                camera_id=camera_id,
                text=message,
                severity=severity,
            ),
            return_exceptions=True,
        )

        # --- Notify pipeline to stop recording if VLM says NOT dumping ---
        # This is the feedback loop: backend → Redis pub/sub → pipeline
        # Pipeline receives the command and: stops recording, resets FSM,
        # saves evidence metadata, fades bbox to black
        if not is_dumping and track_id is not None:
            self._pipeline.clear_by_vlm(
                camera_id=camera_id,
                track_id=track_id,
                vlm_confidence=payload.get("confidence", 0.0),
                waste_type=payload.get("waste_type", "unknown"),
            )
            logger.info(
                "VLM cleared: %s track=%s — pipeline notified to stop recording",
                camera_id, track_id,
            )
