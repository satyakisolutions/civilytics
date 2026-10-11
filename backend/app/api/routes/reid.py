# backend/app/api/routes/reid.py
"""ReID — person/vehicle re-identification via CLIP-ReID (body) + SCRFD/ArcFace (face) + Qdrant."""
import logging
import os
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.middleware import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reid", tags=["reid"])

_QDRANT_URL = "http://qdrant:6333"
_CLIP_MODEL_NAME = "ViT-B/16"
_EMBEDDING_DIM = 512
_FACE_EMBEDDING_DIM = 512
_CLIP_REID_CHECKPOINT = os.getenv("CLIP_REID_CHECKPOINT", "/app/models/reid/clip_reid.pth")
_SCRFD_MODEL = os.getenv("SCRFD_MODEL_PATH", "/app/models/face/det_10g.onnx")
_ARCFACE_MODEL = os.getenv("ARCFACE_MODEL_PATH", "/app/models/face/w600k_r50.onnx")

_clip_model = None
_clip_preprocess = None
_qdrant_client = None
_face_detector = None
_face_embedder = None


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
            if isinstance(state, dict):
                state = state.get("model", state.get("state_dict", state))
            visual_keys = {k: v for k, v in state.items() if k.startswith("visual.") or k.startswith("model.visual.")}
            if visual_keys:
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


def _get_face_models():
    """Lazy-load SCRFD detector + ArcFace embedder from subjects module."""
    global _face_detector, _face_embedder
    if _face_detector is not None:
        return _face_detector, _face_embedder
    if not Path(_SCRFD_MODEL).exists() or not Path(_ARCFACE_MODEL).exists():
        logger.info("Face models not found at %s / %s", _SCRFD_MODEL, _ARCFACE_MODEL)
        return None, None
    try:
        from app.subjects.detector import ScrfdDetector
        from app.subjects.embedder import ArcFaceEmbedder, align_face
        _face_detector = ScrfdDetector(_SCRFD_MODEL)
        _face_embedder = ArcFaceEmbedder(_ARCFACE_MODEL)
        logger.info("Face ReID models loaded (SCRFD + ArcFace)")
        return _face_detector, _face_embedder
    except Exception as e:
        logger.warning("Face models failed to load: %s", e)
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


def _embed_face(image_path: str) -> list[float] | None:
    """Detect face in image → align → ArcFace embed. Returns None if no face found."""
    detector, embedder = _get_face_models()
    if detector is None:
        return None
    try:
        from app.subjects.embedder import align_face
        img_bgr = cv2.imread(image_path)
        if img_bgr is None:
            return None
        detection = detector.detect_best(img_bgr)
        if detection is None or detection.score < 0.5:
            return None
        aligned = align_face(img_bgr, detection.kps)
        embedding = embedder.embed(aligned)
        return embedding.tolist()
    except Exception as e:
        logger.warning("Face embed failed for %s: %s", image_path, e)
        return None


def _ensure_collection(qdrant, name: str, dim: int):
    try:
        from qdrant_client.models import Distance, VectorParams
        names = [c.name for c in qdrant.get_collections().collections]
        if name not in names:
            qdrant.create_collection(collection_name=name,
                                     vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
    except Exception as e:
        logger.warning("Qdrant collection check failed for %s: %s", name, e)
        raise


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
    is_person = body.detection_type != "vehicle_dump"
    body_collection = f"civilytics_{'persons' if is_person else 'vehicles'}"
    face_collection = "civilytics_faces"

    qdrant = _get_qdrant()
    if qdrant is None:
        return {"matches": [], "face_matches": [], "status": "qdrant_unavailable",
                "message": "Qdrant connection failed"}

    # Body ReID (CLIP)
    model, _ = _get_clip()
    body_available = model is not None
    if body_available:
        try:
            _ensure_collection(qdrant, body_collection, _EMBEDDING_DIM)
        except Exception as e:
            return {"matches": [], "face_matches": [], "status": "qdrant_error", "message": str(e)[:200]}

    # Face ReID (SCRFD + ArcFace) — only for person events
    face_available = is_person and _get_face_models()[0] is not None
    if face_available:
        try:
            _ensure_collection(qdrant, face_collection, _FACE_EMBEDDING_DIM)
        except Exception:
            face_available = False

    if not body_available and not face_available:
        return {"matches": [], "face_matches": [], "status": "model_not_available",
                "message": "Neither CLIP nor face models loaded"}

    image_refs = [r for r in body.evidence_refs if r.endswith((".jpg", ".jpeg", ".png"))]
    body_upserted = 0
    face_upserted = 0
    body_query = None
    face_query = None

    for ref in image_refs:
        if not Path(ref).exists():
            continue

        # Body embedding
        if body_available:
            embedding = _embed_image(ref)
            if embedding is not None:
                if body_query is None:
                    body_query = embedding
                try:
                    from qdrant_client.models import PointStruct
                    qdrant.upsert(collection_name=body_collection, points=[PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, ref)), vector=embedding,
                        payload={"event_id": body.event_id, "evidence_ref": ref,
                                 "detection_type": body.detection_type or "", "match_type": "body"},
                    )])
                    body_upserted += 1
                except Exception as e:
                    logger.warning("Body upsert failed for %s: %s", ref, e)

        # Face embedding
        if face_available:
            face_emb = _embed_face(ref)
            if face_emb is not None:
                if face_query is None:
                    face_query = face_emb
                try:
                    from qdrant_client.models import PointStruct
                    qdrant.upsert(collection_name=face_collection, points=[PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"face:{ref}")), vector=face_emb,
                        payload={"event_id": body.event_id, "evidence_ref": ref,
                                 "detection_type": body.detection_type or "", "match_type": "face"},
                    )])
                    face_upserted += 1
                except Exception as e:
                    logger.warning("Face upsert failed for %s: %s", ref, e)

    # Search body matches
    matches = []
    if body_query is not None:
        try:
            results = qdrant.search(collection_name=body_collection, query_vector=body_query,
                                     limit=10, score_threshold=0.7)
            matches = [{"event_id": r.payload.get("event_id", ""), "evidence_ref": r.payload.get("evidence_ref", ""),
                         "similarity": round(r.score, 4), "match_type": "body"}
                        for r in results if r.payload.get("event_id") != body.event_id]
        except Exception as e:
            logger.warning("Body search failed: %s", e)

    # Search face matches
    face_matches = []
    if face_query is not None:
        try:
            results = qdrant.search(collection_name=face_collection, query_vector=face_query,
                                     limit=10, score_threshold=0.6)
            face_matches = [{"event_id": r.payload.get("event_id", ""), "evidence_ref": r.payload.get("evidence_ref", ""),
                              "similarity": round(r.score, 4), "match_type": "face"}
                             for r in results if r.payload.get("event_id") != body.event_id]
        except Exception as e:
            logger.warning("Face search failed: %s", e)

    if matches or face_matches:
        logger.info("ReID %s: %d body + %d face matches", body.event_id, len(matches), len(face_matches))

    return {
        "matches": matches,
        "face_matches": face_matches,
        "embedding_count": body_upserted,
        "face_embedding_count": face_upserted,
        "collection": body_collection,
        "status": "ok",
    }


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
