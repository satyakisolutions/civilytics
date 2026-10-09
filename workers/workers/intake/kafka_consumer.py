"""Kafka intake — consumes dumping events, starts Conductor workflows."""
import asyncio
import json
import uuid
import httpx
import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from workers.config import settings

log = structlog.get_logger()


class KafkaIntakeService:
    def __init__(self):
        self._running = False
        self._consumer = None
        self._producer = None
        # ponytail: in-memory dedup, DB unique constraint is authoritative
        self._seen_events: set[str] = set()
        self.stats = {
            "consumed": 0,
            "started": 0,
            "duplicates": 0,
            "dlq": 0,
            "last_success_at": None,
        }

    async def start(self):
        self._running = True
        self._consumer = AIOKafkaConsumer(
            settings.kafka_dumping_events_topic,
            bootstrap_servers=settings.kafka_brokers,
            group_id=settings.kafka_dumping_events_group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v),
        )
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_brokers,
            value_serializer=lambda v: json.dumps(v).encode(),
        )

        await self._consumer.start()
        await self._producer.start()
        log.info("intake.started", topic=settings.kafka_dumping_events_topic)

        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                await self._process_message(msg)
        finally:
            await self._consumer.stop()
            await self._producer.stop()
            log.info("intake.stopped")

    async def _process_message(self, msg):
        self.stats["consumed"] += 1
        event_id = None
        try:
            payload = msg.value
            event_id = payload.get("event_id")
            if not event_id:
                raise ValueError("terminal: missing event_id")

            if event_id in self._seen_events:
                self.stats["duplicates"] += 1
                await self._consumer.commit()
                return

            required = [
                "event_id",
                "event_occurred_at",
                "workspace_id",
                "zone_id",
                "camera_id",
                "detection_type",
                "confidence",
                "priority",
                "source_video_ref",
                "source_frame_ref",
            ]
            missing = [f for f in required if not payload.get(f)]
            if missing:
                raise ValueError(
                    f"terminal: missing fields: {', '.join(missing)}"
                )

            correlation_id = payload.get("correlation_id") or str(
                uuid.uuid5(uuid.NAMESPACE_DNS, event_id)
            )
            payload["correlation_id"] = correlation_id

            workflow_id = await self._start_workflow(
                event_id, correlation_id, payload
            )

            self._seen_events.add(event_id)
            self.stats["started"] += 1
            self.stats["last_success_at"] = msg.timestamp

            await self._consumer.commit()

            log.info(
                "intake.workflow_started",
                event_id=event_id,
                workflow_id=workflow_id,
                partition=msg.partition,
                offset=msg.offset,
            )

        except Exception as e:
            error_msg = str(e)
            log.error(
                "intake.failed",
                event_id=event_id,
                error=error_msg,
                partition=msg.partition,
                offset=msg.offset,
            )

            if "terminal:" in error_msg:
                await self._send_to_dlq(msg, error_msg)
                await self._consumer.commit()
            else:
                await asyncio.sleep(5)

    async def _start_workflow(
        self, event_id: str, correlation_id: str, payload: dict
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if settings.conductor_auth_mode == "token" and settings.conductor_auth_token:
            headers["X-Authorization"] = settings.conductor_auth_token

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.conductor_server_url.rstrip('/')}/workflow",
                headers=headers,
                json={
                    "name": "dumping_event_processing",
                    "version": 1,
                    "correlationId": correlation_id,
                    "input": {"event": payload},
                },
            )
            resp.raise_for_status()
            return resp.text.strip('"')

    async def _send_to_dlq(self, msg, error: str):
        self.stats["dlq"] += 1
        dlq_payload = {
            "original_topic": msg.topic,
            "partition": msg.partition,
            "offset": msg.offset,
            "error": error[:500],
            "payload": msg.value,
        }
        await self._producer.send(settings.kafka_dlq_topic, value=dlq_payload)
        log.warning(
            "intake.dlq", topic=msg.topic, offset=msg.offset, error=error[:200]
        )

    def stop(self):
        self._running = False
