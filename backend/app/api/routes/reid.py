# backend/app/api/routes/reid.py
"""ReID — person/vehicle re-identification via CLIP + Qdrant."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.auth.middleware import require_admin

router = APIRouter(prefix="/reid", tags=["reid"])


class ReIDAnalyzeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    detection_type: str | None = None
    workspace_id: str | None = None


@router.post("/analyze")
async def analyze_reid(
    body: ReIDAnalyzeRequest,
    _user: dict = Depends(require_admin),
):
    # ponytail: stub until CLIP ONNX model downloaded and Qdrant deployed
    collection = f"civilytics_{'persons' if body.detection_type != 'vehicle_dump' else 'vehicles'}"
    return {
        "matches": [],
        "embedding_count": 0,
        "collection": collection,
    }
