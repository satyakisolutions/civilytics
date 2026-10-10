# backend/app/api/routes/clustering.py
import logging
import uuid
from datetime import timedelta
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_admin
from app.db.models import ViolationEvent

router = APIRouter(prefix="/clustering", tags=["clustering"])


class ClusterAnalyzeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    detection_type: str | None = None
    workspace_id: str | None = None


@router.post("/analyze")
async def analyze_clusters(
    body: ClusterAnalyzeRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    # ponytail: temporal proximity only; CLIP+Qdrant when ReID service lands
    match_groups = []
    similarity_ranges = []

    if not body.evidence_refs:
        return {"match_groups": [], "similarity_ranges": []}

    try:
        event_uuid = uuid.UUID(body.event_id)
        result = await db.execute(
            select(ViolationEvent).where(ViolationEvent.id == event_uuid)
        )
        current = result.scalar_one_or_none()
        if current:
            window_start = current.timestamp - timedelta(hours=24)
            similar = await db.execute(
                select(ViolationEvent).where(
                    and_(
                        ViolationEvent.camera_id == current.camera_id,
                        ViolationEvent.timestamp >= window_start,
                        ViolationEvent.id != event_uuid,
                    )
                ).limit(20)
            )
            rows = similar.scalars().all()
            if rows:
                match_groups.append({
                    "group_id": str(uuid.uuid4()),
                    "type": "temporal_proximity",
                    "event_ids": [str(r.id) for r in rows],
                    "count": len(rows),
                })
                similarity_ranges.append({
                    "metric": "time_proximity_hours",
                    "min": 0,
                    "max": 24,
                })
    except (ValueError, Exception) as e:
        logging.getLogger(__name__).warning("Cluster analysis failed for %s: %s", body.event_id, e)

    return {"match_groups": match_groups, "similarity_ranges": similarity_ranges}
