"""cluster_subject_sightings — visual similarity clustering."""
import httpx
from workers.http_client import service_client
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_cluster_sightings(input_data: dict, task: dict) -> dict:
    event = input_data.get("validated_event", input_data.get("event", {}))
    evidence_refs = input_data.get("evidence_refs", [])
    event_id = event.get("event_id", "unknown")

    if not evidence_refs:
        return {"cluster_status": "SKIPPED", "match_groups": []}

    log.info("clustering.analyzing", event_id=event_id)

    try:
        async with service_client(timeout=60) as client:
            resp = await client.post(
                f"{settings.clustering_service_url}/api/clustering/analyze",
                json={
                    "event_id": event_id,
                    "evidence_refs": evidence_refs,
                    "detection_type": event.get("detection_type"),
                    "workspace_id": event.get("workspace_id"),
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Clustering failed: {e}") from e

    groups = result.get("match_groups", [])
    log.info("clustering.completed", event_id=event_id, group_count=len(groups))

    return {
        "cluster_status": "COMPLETED",
        "match_groups": groups,
        "similarity_ranges": result.get("similarity_ranges", []),
    }
