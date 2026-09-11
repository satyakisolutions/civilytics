# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
import logging
import time

import orjson
from confluent_kafka import Producer

logger = logging.getLogger(__name__)


class PipelineKafkaProducer:
    def __init__(self) -> None:
        from app.config import pipeline_settings

        self._producer = Producer(
            {
                "bootstrap.servers": pipeline_settings.kafka_bootstrap_servers,
                "linger.ms": 5,
                "batch.num.messages": 100,
            }
        )

    def send_metadata(self, camera_id: str, metadata: dict) -> None:
        try:
            self._producer.produce(
                topic="wastewatch.metadata",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps(metadata),
            )
        except Exception:
            logger.exception("Failed to send metadata for %s", camera_id)

    def send_event(self, event) -> None:
        """Publish an FSM state change event to wastewatch.events."""
        try:
            self._producer.produce(
                topic="wastewatch.events",
                key=event.camera_id.encode("utf-8"),
                value=orjson.dumps({
                    "camera_id": event.camera_id,
                    "track_id": event.track_id,
                    "state": event.state.value,
                    "prev_state": event.prev_state.value,
                    "dwell_time_s": round(event.dwell_time_s, 1),
                    "class_name": event.class_name,
                    "bbox": event.bbox,
                    "timestamp": event.timestamp,
                }),
            )
        except Exception:
            logger.exception("Failed to send event for track %d", event.track_id)

    def send_vlm_request(self, camera_id, track_id, zone_name, dwell_time_s, class_name, path_summary, frames_base64: list[str] | None = None, trigger_bbox: dict | None = None):
        try:
            self._producer.produce(
                topic="wastewatch.vlm.requests",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps({
                    "camera_id": camera_id, "track_id": track_id,
                    "track_session_id": f"{camera_id}_{track_id}",
                    "zone_name": zone_name, "dwell_time_s": round(dwell_time_s, 1),
                    "class_name": class_name, "path_summary": path_summary,
                    "trigger_bbox": trigger_bbox,
                    "frame_timestamps": [time.time()] if frames_base64 else [],
                    "frames_base64": frames_base64 or [],
                }),
            )
        except Exception:
            logger.exception("Failed to send VLM request")

    def send_lpr_request(self, camera_id: str, track_id: int, vehicle_bbox: dict, class_name: str):
        """Request plate recognition for a vehicle in ROI."""
        try:
            self._producer.produce(
                topic="wastewatch.lpr",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps({
                    "camera_id": camera_id,
                    "track_id": track_id,
                    "vehicle_bbox": vehicle_bbox,
                    "class_name": class_name,
                    "image_path": "",
                }),
            )
        except Exception:
            logger.exception("Failed to send LPR request")

    def send_tts_request(
        self,
        camera_id: str,
        track_id: int,
        text: str,
        priority: str = "normal",
        timestamp: float | None = None,
    ) -> None:
        """Publish a TTS audio generation request to wastewatch.tts."""
        try:
            self._producer.produce(
                topic="wastewatch.tts",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps({
                    "camera_id": camera_id,
                    "track_id": track_id,
                    "text": text,
                    "priority": priority,
                    "timestamp": timestamp or time.time(),
                }),
            )
        except Exception:
            logger.exception("Failed to send TTS request for %s", camera_id)

    def send_alert(
        self,
        camera_id: str,
        track_id: int,
        severity: str,
        message: str,
        **kwargs,
    ) -> None:
        """Publish an alert event to wastewatch.alerts for WebSocket + Telegram dispatch."""
        try:
            payload = {
                "camera_id": camera_id,
                "track_id": track_id,
                "severity": severity,
                "message": message,
                **kwargs,
            }
            self._producer.produce(
                topic="wastewatch.alerts",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps(payload),
            )
        except Exception:
            logger.exception("Failed to send alert for %s", camera_id)

    def produce(self, topic: str, key: str, value: dict) -> None:
        """Generic produce helper -- serialises value as JSON and publishes.

        This is used by the ViolationDispatcher for topics that don't have
        a dedicated send_* method (e.g. wastewatch.evidence).
        """
        try:
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=orjson.dumps(value),
            )
        except Exception:
            logger.exception("Failed to produce to %s (key=%s)", topic, key)

    def send_event_dict(self, event: dict) -> None:
        """Publish a raw event dict to wastewatch.events (used by CommandHandler)."""
        camera_id = event.get("camera_id", "unknown")
        try:
            self._producer.produce(
                topic="wastewatch.events",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps(event),
            )
        except Exception:
            logger.exception("Failed to send event dict for %s", camera_id)

    def flush(self) -> None:
        self._producer.flush(timeout=5)

    def poll(self) -> None:
        self._producer.poll(0)
