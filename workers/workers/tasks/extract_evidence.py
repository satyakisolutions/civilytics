"""extract_evidence — calls evidence service for frames and clips."""
import httpx
from workers.http_client import service_client
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_extract_evidence(input_data: dict, task: dict) -> dict:
    event = input_data.get("validated_event", input_data.get("event", {}))
    event_id = event.get("event_id", "unknown")

    log.info("evidence.extracting", event_id=event_id)

    try:
        async with service_client(timeout=120) as client:
            resp = await client.post(
                f"{settings.evidence_service_url}/api/evidence/extract",
                json={
                    "event_id": event_id,
                    "video_ref": event.get("source_video_ref", ""),
                    "frame_ref": event.get("source_frame_ref", ""),
                    "workspace_id": event.get("workspace_id"),
                    "camera_id": event.get("camera_id"),
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as e:
        raise Exception(f"Evidence extraction failed: {e}")

    evidence_refs = result.get("evidence_refs", [])
    log.info("evidence.extracted", event_id=event_id, count=len(evidence_refs))

    return {
        "evidence_refs": evidence_refs,
        "evidence_count": len(evidence_refs),
        "capture_timestamps": result.get("timestamps", []),
        "integrity_status": result.get("integrity", "unknown"),
    }
