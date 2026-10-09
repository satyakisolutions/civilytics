"""run_lpr_analysis — license plate recognition for vehicle events."""
import httpx
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_run_lpr(input_data: dict, task: dict) -> dict:
    event = input_data.get("validated_event", input_data.get("event", {}))
    evidence_refs = input_data.get("evidence_refs", [])
    event_id = event.get("event_id", "unknown")

    if not evidence_refs:
        log.info("lpr.skipped_no_evidence", event_id=event_id)
        return {"lpr_status": "SKIPPED", "plates": []}

    log.info("lpr.analyzing", event_id=event_id, evidence_count=len(evidence_refs))

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.lpr_service_url}/api/lpr/analyze",
                json={
                    "event_id": event_id,
                    "evidence_refs": evidence_refs,
                    "workspace_id": event.get("workspace_id"),
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as e:
        raise Exception(f"LPR analysis failed: {e}")

    plates = result.get("plates", [])
    log.info("lpr.completed", event_id=event_id, plate_count=len(plates))

    return {
        "lpr_status": "COMPLETED",
        "plates": plates,
        "plate_count": len(plates),
    }
