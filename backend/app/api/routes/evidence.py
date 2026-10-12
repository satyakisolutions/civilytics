# backend/app/api/routes/evidence.py
import logging
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.auth.middleware import require_admin, require_viewer
from app.db.models import EvidenceFile

router = APIRouter(prefix="/evidence", tags=["evidence"])

EVIDENCE_ROOT = Path(os.environ.get("WW_EVIDENCE_DIR", "/app/evidence"))


class EvidenceExtractRequest(BaseModel):
    event_id: str
    video_ref: str = ""
    frame_ref: str = ""
    workspace_id: str | None = None
    camera_id: str | None = None


@router.post("/extract")
async def extract_evidence(
    body: EvidenceExtractRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    evidence_refs: list[str] = []
    timestamps: list[str] = []

    event_uuid = None
    try:
        event_uuid = uuid.UUID(body.event_id)
    except ValueError:
        logging.getLogger(__name__).warning("Invalid event_id UUID: %s", body.event_id)

    for category in ("violations", "suspicious"):
        base = EVIDENCE_ROOT / category
        if not base.exists():
            continue
        for path in base.rglob(f"*{body.event_id[:8]}*"):
            if path.suffix not in (".jpg", ".jpeg", ".png", ".mp4", ".json"):
                continue
            existing = await db.execute(
                select(EvidenceFile).where(EvidenceFile.local_path == str(path))
            )
            if existing.scalar_one_or_none():
                evidence_refs.append(str(path))
                continue
            file_type = "video_clip" if path.suffix == ".mp4" else "metadata_json" if path.suffix == ".json" else "snapshot"
            ef = EvidenceFile(
                event_id=event_uuid,
                file_type=file_type,
                local_path=str(path),
                checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            db.add(ef)
            evidence_refs.append(str(path))
            timestamps.append(datetime.now(timezone.utc).isoformat())

    await db.commit()
    return {
        "evidence_refs": evidence_refs,
        "timestamps": timestamps,
        "integrity": "verified" if evidence_refs else "no_evidence",
    }


class EvidenceCleanupRequest(BaseModel):
    before: str
    dry_run: bool = True


@router.delete("/cleanup")
async def cleanup_evidence(
    body: EvidenceCleanupRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    cutoff = datetime.fromisoformat(body.before)
    result = await db.execute(
        select(EvidenceFile).where(EvidenceFile.created_at < cutoff)
    )
    files = result.scalars().all()
    count = len(files)

    if not body.dry_run:
        for f in files:
            if f.local_path:
                try:
                    Path(f.local_path).unlink(missing_ok=True)
                except OSError:
                    pass
            await db.delete(f)
        await db.commit()

    return {"deleted": count, "dry_run": body.dry_run}


@router.get("", response_model=list[dict])
async def list_evidence(
    event_id: uuid.UUID | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
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


@router.get("/files", response_model=list[dict])
async def list_evidence_files(
    limit: int = Query(default=100, le=500),
    _user: dict = Depends(require_viewer),
):
    """Scan evidence dir for JSON sidecar files produced by pipeline evidence_writer.

    Layout: /app/evidence/{violations|suspicious}/{camera_name}/{YYYY-MM-DD}/evt_*.json
    Returns newest-first. Also resolves the pipeline camera name ("cam-01") to
    its DB UUID so the frontend can join evidence records with alerts
    (alerts store UUID, evidence JSON stores name).
    """
    if not EVIDENCE_ROOT.exists():
        return []

    # Build name → uuid lookup via a fresh session (bypasses per-request
    # dependency scope issues when the endpoint was wired without auth).
    from app.db.engine import async_session
    from app.db.models import Camera
    import sys as _sys
    name_to_uuid: dict[str, str] = {}
    try:
        async with async_session() as session:
            result = await session.execute(select(Camera.id, Camera.name))
            for row in result.all():
                name_to_uuid[row.name] = str(row.id)
        print(f"[evidence/files] name_to_uuid={name_to_uuid}", file=_sys.stderr, flush=True)
    except Exception as exc:
        print(f"[evidence/files] camera lookup failed: {exc}", file=_sys.stderr, flush=True)

    records: list[dict] = []
    for category in ("violations", "suspicious"):
        base = EVIDENCE_ROOT / category
        if not base.exists():
            continue
        for json_path in base.rglob("evt_*.json"):
            try:
                rec = json.loads(json_path.read_text())
                rec["_category"] = category
                rec["_mtime"] = json_path.stat().st_mtime
                name = rec.get("camera_id")
                if name and name in name_to_uuid:
                    rec["camera_name"] = name
                    rec["camera_id"] = name_to_uuid[name]
                records.append(rec)
            except Exception:
                continue
    records.sort(key=lambda r: r.get("_mtime", 0), reverse=True)
    return records[:limit]


@router.get("/file")
async def get_evidence_file(
    path: str,
    _user: dict = Depends(require_viewer),
):
    """Serve a single evidence file (keyframe JPEG or MP4) by absolute path.

    Guards against path traversal — path must live under EVIDENCE_ROOT.
    """
    try:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(EVIDENCE_ROOT.resolve()):
            raise HTTPException(status_code=403, detail="Path outside evidence root")
        if not resolved.exists():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(resolved))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
