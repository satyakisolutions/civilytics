"""run_lpr_analysis — license plate recognition for vehicle events.

OCR itself runs continuously elsewhere: backend/app/lpr/worker.py consumes
wastewatch.lpr and writes PlateRead rows to Postgres as plates are read.
This task's job is only to look up whatever PlateRead rows already exist
for the event's track session, which used to mean an HTTP round trip to
api's /lpr/analyze (a pure DB-read route with no OCR of its own) — see
backend/app/api/routes/lpr.py, unchanged, for the UI's own copy of this
same lookup. Querying Postgres directly here removes that hop.
"""
import json
import uuid

import structlog
from aiokafka import AIOKafkaProducer
from sqlalchemy import text

from workers.config import settings
from workers.db import get_session_factory

log = structlog.get_logger()

_producer: AIOKafkaProducer | None = None

_PLATES_QUERY = text("""
    SELECT p.plate_number, p.plate_normalised, p.confidence, p.timestamp
    FROM plate_reads p
    JOIN track_sessions t ON t.id = p.track_session_id
    JOIN violation_events v ON v.camera_id = t.camera_id AND v.track_id = t.track_id
    WHERE v.id = :event_id
""")


async def _get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_brokers,
            value_serializer=lambda v: json.dumps(v).encode(),
        )
        await _producer.start()
    return _producer


async def handle_run_lpr(input_data: dict, task: dict) -> dict:
    event = input_data.get("validated_event", input_data.get("event", {}))
    evidence_refs = input_data.get("evidence_refs", [])
    event_id = event.get("event_id", "unknown")

    if not evidence_refs:
        log.info("lpr.skipped_no_evidence", event_id=event_id)
        return {"lpr_status": "SKIPPED", "plates": []}

    log.info("lpr.analyzing", event_id=event_id, evidence_count=len(evidence_refs))

    plates: list[dict] = []
    try:
        event_uuid = uuid.UUID(event_id)
        session_factory = get_session_factory()
        async with session_factory() as db:
            result = await db.execute(_PLATES_QUERY, {"event_id": event_uuid})
            plates = [
                {
                    "plate_number": row.plate_number,
                    "plate_normalised": row.plate_normalised,
                    "confidence": row.confidence,
                    "timestamp": row.timestamp.isoformat(),
                }
                for row in result
            ]
    except ValueError:
        log.warning("lpr.invalid_event_id", event_id=event_id)
    except Exception as e:
        raise Exception(f"LPR analysis failed: {e}")

    log.info("lpr.completed", event_id=event_id, plate_count=len(plates))

    output = {
        "lpr_status": "COMPLETED",
        "plates": plates,
        "plate_count": len(plates),
    }

    producer = await _get_producer()
    await producer.send(
        "wastewatch.lpr",
        value={
            "event_id": event_id,
            "workspace_id": event.get("workspace_id"),
            "camera_id": event.get("camera_id"),
            "plate_count": len(plates),
            "plates": [p.get("plate_number", "") for p in plates],
            "source": "workflow_worker",
        },
    )

    return output
