# backend/app/evidence/worker.py
"""Kafka consumer: wastewatch.evidence → save + sync evidence files.

Also consumes wastewatch.events for smart recording:
- VIOLATION state → start recording inference output stream
- EXITED state → stop recording, save clip, sync to Dropbox
"""
import asyncio
import logging

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import TOPIC_EVIDENCE, TOPIC_EVENTS
from app.evidence.recorder import EvidenceRecorder
from app.evidence.stream_recorder import StreamRecorder
from app.evidence.dropbox_sync import DropboxSync

logger = logging.getLogger(__name__)


class EvidenceWorker:
    def __init__(self, bootstrap_servers: str, mediamtx_rtsp_url: str = "rtsp://localhost:8554"):
        self._bootstrap_servers = bootstrap_servers
        self._evidence_consumer: AIOKafkaConsumer | None = None
        self._events_consumer: AIOKafkaConsumer | None = None
        self._tasks: list[asyncio.Task] = []
        self._recorder = EvidenceRecorder()
        self._stream_recorder = StreamRecorder(
            mediamtx_rtsp_url=mediamtx_rtsp_url,
            pre_buffer_s=10,
            post_buffer_s=15,
            max_concurrent=4,
        )
        self._dropbox = DropboxSync()
        self._cleanup_task: asyncio.Task | None = None

    async def start(self):
        # Consumer for evidence file requests (snapshots, metadata)
        self._evidence_consumer = AIOKafkaConsumer(
            TOPIC_EVIDENCE,
            bootstrap_servers=self._bootstrap_servers,
            group_id="wastewatch.group.evidence-worker",
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._evidence_consumer.start()
        self._tasks.append(asyncio.create_task(self._consume_evidence()))

        # Consumer for FSM events (smart recording triggers)
        self._events_consumer = AIOKafkaConsumer(
            TOPIC_EVENTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id="wastewatch.group.evidence-recording",
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._events_consumer.start()
        self._tasks.append(asyncio.create_task(self._consume_events()))

        # Periodic cleanup of expired recordings
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        logger.info("EvidenceWorker started (smart recording enabled)")

    async def stop(self):
        # Stop all active recordings
        await self._stream_recorder.stop_all()

        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        if self._evidence_consumer:
            await self._evidence_consumer.stop()
        if self._events_consumer:
            await self._events_consumer.stop()
        await self._dropbox.close()

    async def _consume_evidence(self):
        """Process evidence file requests (snapshots, metadata)."""
        try:
            async for msg in self._evidence_consumer:
                await self._process_evidence(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Evidence consumer error")

    async def _consume_events(self):
        """Process FSM events for smart recording triggers."""
        try:
            async for msg in self._events_consumer:
                await self._process_event(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Events consumer error (recording)")

    async def _process_evidence(self, data: dict):
        try:
            camera_id = data.get("camera_id", "unknown")
            evidence_type = data.get("type", "metadata")

            if evidence_type == "snapshot":
                frame_data = data.get("frame_data")
                if frame_data:
                    import base64
                    raw = base64.b64decode(frame_data)
                    path = self._recorder.save_snapshot(camera_id, raw)
                    await self._dropbox.upload(path, camera_id)
            elif evidence_type == "metadata":
                path = self._recorder.save_metadata(camera_id, data.get("metadata", {}))
                await self._dropbox.upload(path, camera_id)

            logger.info("Evidence processed: camera=%s type=%s", camera_id, evidence_type)
        except Exception:
            logger.exception("Evidence processing failed")

    async def _process_event(self, data: dict):
        """Handle FSM state transitions for smart recording.

        - violation → start recording inference output stream
        - exited → stop recording, sync clip to Dropbox
        """
        try:
            state = data.get("state", "")
            camera_id = data.get("camera_id", "")
            track_id = data.get("track_id", 0)

            if state == "violation":
                clip_path = await self._stream_recorder.start_recording(camera_id, track_id)
                if clip_path:
                    logger.info(
                        "Smart recording STARTED: %s/track-%d → %s",
                        camera_id, track_id, clip_path,
                    )

            elif state == "exited":
                clip_path = await self._stream_recorder.stop_recording(camera_id, track_id)
                if clip_path:
                    logger.info(
                        "Smart recording STOPPED: %s/track-%d → %s",
                        camera_id, track_id, clip_path,
                    )
                    # Sync clip to Dropbox
                    await self._dropbox.upload(clip_path, camera_id)

        except Exception:
            logger.exception("Smart recording event processing failed")

    async def _cleanup_loop(self):
        """Periodically clean up expired recordings."""
        try:
            while True:
                await asyncio.sleep(30)
                saved = await self._stream_recorder.cleanup_expired()
                for path in saved:
                    logger.info("Expired recording saved: %s", path)
        except asyncio.CancelledError:
            pass
