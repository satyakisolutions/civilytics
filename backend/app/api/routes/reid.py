# backend/app/api/routes/reid.py
"""ReID — person/vehicle re-identification via CLIP-ReID + Qdrant."""
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reid", tags=["reid"])

_QDRANT_URL = "http://qdrant:6333"
_CLIP_MODEL_NAME = "ViT-B/16"
_EMBEDDING_DIM = 512
_CLIP_REID_CHECKPOINT = os.getenv("CLIP_REID_CHECKPOINT", "/app/models/reid/clip_reid.pth")

_clip_model = None
_clip_preprocess = None
_qdrant_client = None


def _get_qdrant():
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client
    try:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(url=_QDRANT_URL, timeout=10)
        return _qdrant_client
    except Exception as e:
        logger.warning("Qdrant unavailable: %s", e)
        return None


def _get_clip():
    global _clip_model, _clip_preprocess
    if _clip_model is not None:
        return _clip_model, _clip_preprocess
    try:
        import clip
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _clip_model, _clip_preprocess = clip.load(_CLIP_MODEL_NAME, device=device)
        checkpoint_path = Path(_CLIP_REID_CHECKPOINT)
        if checkpoint_path.exists():
            state = torch.load(checkpoint_path, map_location=device, weights_only=True)
            # CLIP-ReID checkpoints store model state under 'model' or 'state_dict' key
            if isinstance(state, dict):
                state = state.get("model", state.get("state_dict", state))
            # Filter to only keys matching the visual encoder
            visual_keys = {k: v for k, v in state.items() if k.startswith("visual.") or k.startswith("model.visual.")}
            if visual_keys:
                # Strip 'model.' prefix if present
                visual_keys = {k.replace("model.", "", 1) if k.startswith("model.") else k: v for k, v in visual_keys.items()}
                missing, unexpected = _clip_model.load_state_dict(visual_keys, strict=False)
                logger.info("CLIP-ReID checkpoint loaded on %s (%d keys, %d missing, %d unexpected)",
                            device, len(visual_keys), len(missing), len(unexpected))
            else:
                _clip_model.load_state_dict(state, strict=False)
                logger.info("CLIP-ReID checkpoint loaded on %s (full state)", device)
        else:
            logger.info("CLIP-ReID checkpoint not found at %s, using vanilla %s on %s",
                        _CLIP_REID_CHECKPOINT, _CLIP_MODEL_NAME, device)
        return _clip_model, _clip_preprocess
    except Exception as e:
        logger.warning("CLIP unavailable: %s", e)
        return None, None


def _embed_image(image_path: str) -> list[float] | None:
    model, preprocess = _get_clip()
    if model is None:
        return None
    try:
        import torch
        from PIL import Image
        img = Image.open(image_path).convert("RGB")
        tensor = preprocess(img).unsqueeze(0)
        if next(model.parameters()).is_cuda:
            tensor = tensor.cuda()
        with torch.no_grad():
            features = model.encode_image(tensor)
            features /= features.norm(dim=-1, keepdim=True)
        return features.squeeze().cpu().tolist()
    except Exception as e:
        logger.warning("Failed to embed %s: %s", image_path, e)
        return None


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
    collection = f"civilytics_{'persons' if body.detection_type != 'vehicle_dump' else 'vehicles'}"

    model, _ = _get_clip()
    if model is None:
        return {"matches": [], "embedding_count": 0, "collection": collection,
                "status": "model_not_available", "message": "CLIP not loaded — install openai-clip and torch"}

    qdrant = _get_qdrant()
    if qdrant is None:
        return {"matches": [], "embedding_count": 0, "collection": collection,
                "status": "qdrant_unavailable", "message": "Qdrant connection failed"}

    try:
        from qdrant_client.models import Distance, VectorParams
        names = [c.name for c in qdrant.get_collections().collections]
        if collection not in names:
            qdrant.create_collection(collection_name=collection,
                                     vectors_config=VectorParams(size=_EMBEDDING_DIM, distance=Distance.COSINE))
    except Exception as e:
        logger.warning("Qdrant collection check failed: %s", e)
        return {"matches": [], "embedding_count": 0, "collection": collection,
                "status": "qdrant_error", "message": str(e)[:200]}

    image_refs = [r for r in body.evidence_refs if r.endswith((".jpg", ".jpeg", ".png"))]
    upserted = 0
    query_vector = None

    for ref in image_refs:
        if not Path(ref).exists():
            continue
        embedding = _embed_image(ref)
        if embedding is None:
            continue
        if query_vector is None:
            query_vector = embedding
        try:
            from qdrant_client.models import PointStruct
            qdrant.upsert(collection_name=collection, points=[PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, ref)), vector=embedding,
                payload={"event_id": body.event_id, "evidence_ref": ref, "detection_type": body.detection_type or ""},
            )])
            upserted += 1
        except Exception as e:
            logger.warning("Qdrant upsert failed for %s: %s", ref, e)

    matches = []
    if query_vector is not None:
        try:
            results = qdrant.search(collection_name=collection, query_vector=query_vector,
                                     limit=10, score_threshold=0.7)
            matches = [{"event_id": r.payload.get("event_id", ""), "evidence_ref": r.payload.get("evidence_ref", ""),
                         "similarity": round(r.score, 4)}
                        for r in results if r.payload.get("event_id") != body.event_id]
            if matches:
                logger.info("ReID %s: %d matches, scores %.4f–%.4f", body.event_id,
                            len(matches), matches[-1]["similarity"], matches[0]["similarity"])
        except Exception as e:
            logger.warning("Qdrant search failed: %s", e)

    return {"matches": matches, "embedding_count": upserted, "collection": collection, "status": "ok"}


class ReIDCleanupRequest(BaseModel):
    before: str


@router.delete("/cleanup")
async def cleanup_reid(
    body: ReIDCleanupRequest,
    _user: dict = Depends(require_admin),
):
    qdrant = _get_qdrant()
    if qdrant is None:
        return {"deleted": 0, "status": "qdrant_unavailable"}
    deleted = 0
    for coll in ("civilytics_persons", "civilytics_vehicles"):
        try:
            from qdrant_client.models import Filter, FieldCondition, Range
            qdrant.delete(collection_name=coll, points_selector=Filter(must=[
                FieldCondition(key="created_at", range=Range(lt=body.before))
            ]))
        except Exception:
            pass
    return {"deleted": deleted, "status": "ok"}
