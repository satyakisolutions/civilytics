# backend/app/api/routes/vlm.py
"""VLM — scene description via SmolVLM2-500M."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.auth.middleware import require_admin

router = APIRouter(prefix="/vlm", tags=["vlm"])


class VLMDescribeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    workspace_id: str | None = None


@router.post("/describe")
async def describe_scene(
    body: VLMDescribeRequest,
    _user: dict = Depends(require_admin),
):
    # ponytail: stub until SmolVLM2-500M model downloaded
    return {
        "description": "",
        "model": "SmolVLM2-500M-Video-Instruct",
        "status": "stub",
    }
