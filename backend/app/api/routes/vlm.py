# backend/app/api/routes/vlm.py
"""VLM — scene description via vLLM-hosted vision model."""
import base64
import logging
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import require_admin
from app.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vlm", tags=["vlm"])

_vlm_client = None


def _get_vlm():
    global _vlm_client
    if _vlm_client is not None:
        return _vlm_client
    try:
        from app.vlm.client import VLMClient
        _vlm_client = VLMClient()
        return _vlm_client
    except Exception as e:
        logger.warning("VLM client init failed: %s", e)
        return None


class VLMDescribeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    workspace_id: str | None = None


@router.post("/describe")
async def describe_scene(
    body: VLMDescribeRequest,
    _user: dict = Depends(require_admin),
):
    image_refs = [r for r in body.evidence_refs if r.endswith((".jpg", ".jpeg", ".png"))]
    if not image_refs:
        return {"description": "", "model": settings.vlm_model, "status": "no_images"}

    frames_b64 = []
    for ref in image_refs[:4]:
        p = Path(ref)
        if not p.exists():
            continue
        frames_b64.append(base64.b64encode(p.read_bytes()).decode())

    if not frames_b64:
        return {"description": "", "model": settings.vlm_model, "status": "files_not_found"}

    client = _get_vlm()
    if client is None:
        return {"description": "", "model": settings.vlm_model, "status": "service_unavailable"}

    try:
        from app.vlm.schemas import VLMRequest
        request = VLMRequest(
            camera_id="api", track_id=0, track_session_id=body.event_id,
            zone_name="", dwell_time_s=0, class_name="unknown",
            path_summary="", frame_timestamps=[0.0] * len(frames_b64),
            frames_base64=frames_b64,
        )
        result = await client.analyze(request)
        if result and result.response:
            return {
                "description": result.response.person_description,
                "action": result.response.action,
                "is_dumping": result.response.is_dumping,
                "waste_type": result.response.waste_type.value,
                "severity": result.response.severity.value,
                "confidence": result.response.confidence,
                "reasoning": result.response.reasoning,
                "model": settings.vlm_model,
                "status": "ok",
            }
        return {"description": "", "model": settings.vlm_model, "status": "no_response"}
    except Exception as e:
        logger.error("VLM describe failed: %s", e)
        return {"description": "", "model": settings.vlm_model, "status": "error", "message": str(e)[:200]}
