# backend/app/evidence/worker.py
"""Kafka consumer: wastewatch.evidence → save + sync evidence files."""
import asyncio
import logging

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import TOPIC_EVIDENCE
from app.evidence.recorder import EvidenceRecorder
from app.evidence.dropbox_sync import DropboxSync

logger = logging.getLogger(__name__)


class EvidenceWorker:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._recorder = EvidenceRecorder()
        self._dropbox = DropboxSync()

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_EVIDENCE,
            bootstrap_servers=self._bootstrap_servers,
            group_id="wastewatch.group.evidence-worker",
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("EvidenceWorker started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        await self._dropbox.close()

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                await self._process(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("EvidenceWorker error")

    async def _process(self, data: dict):
        try:
            camera_id = data.get("camera_id", "unknown")
            evidence_type = data.get("type", "metadata")

            if evidence_type == "metadata":
                self._recorder.save_metadata(camera_id, data.get("metadata", {}))
            logger.info("Evidence processed: camera=%s type=%s", camera_id, evidence_type)
        except Exception:
            logger.exception("Evidence processing failed")
