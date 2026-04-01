# backend/app/api/routes/evidence.py
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import EvidenceFile

router = APIRouter(prefix="/evidence", tags=["evidence"])

@router.get("", response_model=list[dict])
async def list_evidence(
    event_id: uuid.UUID | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(EvidenceFile).order_by(desc(EvidenceFile.created_at)).limit(limit)
    if event_id:
        query = query.where(EvidenceFile.event_id == event_id)
    result = await db.execute(query)
    files = result.scalars().all()
    return [
        {"id": str(f.id), "event_id": str(f.event_id) if f.event_id else None,
         "file_type": f.file_type, "local_path": f.local_path,
         "sync_status": f.sync_status, "created_at": f.created_at.isoformat()}
        for f in files
    ]
