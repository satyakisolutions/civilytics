"""Anonymous subject IDs from violation-time faces (admin only, access-logged).

See docs/superpowers/specs/2026-09-29-face-subject-id-design.md §9, §11.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.auth.middleware import require_admin
from app.schemas.subject import ActionResult, ReviewOut, SubjectDetail, SubjectSummary
from app.subjects.config import SubjectSettings
from app.subjects.queries import list_reviews, list_subjects, pending_review_count, subject_detail
from app.subjects.service import Conflict, NotFound, SubjectService

router = APIRouter(prefix="/subjects", tags=["subjects"])

_service: SubjectService | None = None


def get_subject_service() -> SubjectService:
    global _service
    if _service is None:
        from qdrant_client import QdrantClient

        from app.db.engine import async_session
        from app.subjects.vector_store import FaceVectorStore

        settings = SubjectSettings.from_env()
        store = FaceVectorStore(QdrantClient(url=settings.qdrant_url), settings.collection)
        _service = SubjectService(async_session, store, settings)
    return _service


def _actor(user: dict) -> str:
    return str(user.get("preferred_username") or user.get("sub") or "unknown")


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _log(service: SubjectService, subject_id, action: str, user: dict, request: Request, detail=None) -> None:
    async with service.session_factory() as s:
        await service.log_access(s, subject_id, action, _actor(user), _ip(request), detail)
        await s.commit()


@router.get("", response_model=list[SubjectSummary])
async def get_subjects(
    request: Request,
    repeat_only: bool = False,
    sort: Literal["incidents", "last_seen"] = "incidents",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    async with service.session_factory() as s:
        items = await list_subjects(s, repeat_only, sort, limit, offset)
    await _log(service, None, "list", user, request, {"count": len(items), "repeat_only": repeat_only})
    return items


@router.get("/reviews/count")
async def get_review_count(
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
) -> dict:
    """Pending-review count for the sidebar badge. No personal data, so not access-logged."""
    async with service.session_factory() as s:
        return {"pending": await pending_review_count(s)}


@router.get("/reviews", response_model=list[ReviewOut])
async def get_reviews(
    request: Request,
    status: Literal["pending", "merged", "rejected", "obsolete"] = "pending",
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    async with service.session_factory() as s:
        items = await list_reviews(s, status)
    await _log(service, None, "list_reviews", user, request, {"count": len(items), "status": status})
    return items


@router.get("/faces/{face_id}")
async def get_face_image(
    face_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    from app.db.models import SubjectFace

    async with service.session_factory() as s:
        face = await s.get(SubjectFace, face_id)
    if face is None:
        raise HTTPException(status_code=404, detail="Face not found")
    path = Path(face.crop_path).resolve()
    allowed = (Path(service.settings.faces_dir) / "subjects").resolve()
    if not path.is_relative_to(allowed) or not path.is_file():
        raise HTTPException(status_code=404, detail="Face not found")
    await _log(service, face.subject_id, "face_view", user, request, {"face_id": str(face_id)})
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/{subject_id}", response_model=SubjectDetail)
async def get_subject(
    subject_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    async with service.session_factory() as s:
        detail = await subject_detail(s, subject_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Subject not found")
    await _log(service, subject_id, "view", user, request)
    return detail


@router.post("/reviews/{review_id}/merge", response_model=ActionResult)
async def merge_review(
    review_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    try:
        target = await service.merge_review(review_id, _actor(user), _ip(request))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ActionResult(subject_id=target)


@router.post("/reviews/{review_id}/reject", response_model=ActionResult)
async def reject_review(
    review_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    try:
        await service.reject_review(review_id, _actor(user), _ip(request))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ActionResult()


@router.post("/{subject_id}/incidents/{incident_id}/detach", response_model=ActionResult)
async def detach_incident(
    subject_id: uuid.UUID,
    incident_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    try:
        new_id = await service.detach_incident(subject_id, incident_id, _actor(user), _ip(request))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ActionResult(subject_id=new_id)


@router.delete("/{subject_id}", response_model=ActionResult)
async def delete_subject(
    subject_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    try:
        await service.delete_subject(subject_id, _actor(user), _ip(request))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ActionResult(subject_id=subject_id)
