"""cleanup_retention — purge evidence, embeddings, and logs past retention window."""
from datetime import datetime, timedelta, timezone
from workers.http_client import service_client
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_cleanup_retention(input_data: dict, task: dict) -> dict:
    async with service_client(timeout=10) as client:
        resp = await client.get(
            f"{settings.evidence_service_url}/api/config/evidence_retention"
        )
        if resp.status_code == 200:
            config = resp.json().get("value", {})
        else:
            config = {}

    retention_days = int(config.get("retention_days", 90))
    cleanup_embeddings = config.get("cleanup_embeddings", True)
    cleanup_logs = config.get("cleanup_logs", True)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    log.info("cleanup.starting", cutoff=cutoff, retention_days=retention_days)

    deleted_evidence = 0
    deleted_embeddings = 0
    deleted_logs = 0

    async with service_client(timeout=120) as client:
        try:
            resp = await client.request(
                "DELETE",
                f"{settings.evidence_service_url}/api/evidence/cleanup",
                json={"before": cutoff, "dry_run": False},
            )
            resp.raise_for_status()
            deleted_evidence = resp.json().get("deleted", 0)
        except Exception as e:
            log.warning("cleanup.evidence_failed", error=str(e))

        if cleanup_embeddings:
            try:
                resp = await client.request(
                    "DELETE",
                    f"{settings.evidence_service_url}/api/reid/cleanup",
                    json={"before": cutoff},
                )
                resp.raise_for_status()
                deleted_embeddings = resp.json().get("deleted", 0)
            except Exception as e:
                log.warning("cleanup.embeddings_failed", error=str(e))

        if cleanup_logs:
            try:
                resp = await client.request(
                    "DELETE",
                    f"{settings.evidence_service_url}/api/system/logs/cleanup",
                    json={"before": cutoff},
                )
                resp.raise_for_status()
                deleted_logs = resp.json().get("deleted", 0)
            except Exception as e:
                log.warning("cleanup.logs_failed", error=str(e))

    log.info(
        "cleanup.completed",
        deleted_evidence=deleted_evidence,
        deleted_embeddings=deleted_embeddings,
        deleted_logs=deleted_logs,
    )
    return {
        "status": "COMPLETED",
        "deleted_evidence": deleted_evidence,
        "deleted_embeddings": deleted_embeddings,
        "deleted_logs": deleted_logs,
        "cutoff": cutoff,
    }
