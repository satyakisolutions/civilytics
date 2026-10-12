"""reid_analyze — CLIP person/vehicle re-identification against Qdrant.

Moved here from backend/app/api/routes/reid.py as part of the Conductor
restructuring: this is the task the dumping_event_processing_v2 workflow's
fork_analysis branch "reid" expects (taskReferenceName "reid", task name
"reid_analyze"). Before this file existed, no worker in workers/main.py's
TASK_HANDLERS claimed that task type, so any real workflow run would hang
at join_analysis forever waiting for a reid result that no poller ever
produced. backend/app/api/routes/reid.py is UNCHANGED and still live --
the frontend (HistoryView.tsx) calls it directly for the same analysis on
demand, so the CLIP+Qdrant logic is intentionally duplicated here rather
than shared, since workers and backend are separate deployable packages
with no shared import path.

Pre-existing gap, not introduced here: the `clip` (OpenAI CLIP) import has
no declared PyPI dependency in backend/pyproject.toml either -- it has
always silently failed to "model_not_available" there. Declared properly
here (see workers/pyproject.toml) since this task relies on it for real.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import structlog

from workers.config import settings

log = structlog.get_logger()
logger = logging.getLogger(__name__)

_QDRANT_URL = settings.qdrant_url
_CLIP_MODEL_NAME = "ViT-B/16"
_EMBEDDING_DIM = 512
_CLIP_REID_CHECKPOINT = settings.clip_reid_checkpoint

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
        log.warning("reid.qdrant_unavailable", error=str(e))
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
                _clip_model.load_state_dict(visual_keys, strict=False)
            else:
                _clip_model.load_state_dict(state, strict=False)
            log.info("reid.checkpoint_loaded", device=device)
        else:
            log.info("reid.checkpoint_not_found", path=str(checkpoint_path), device=device)
        return _clip_model, _clip_preprocess
    except Exception as e:
        log.warning("reid.clip_unavailable", error=str(e))
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
        log.warning("reid.embed_failed", path=image_path, error=str(e))
        return None


async def handle_reid_analyze(input_data: dict, task: dict) -> dict:
    event = input_data.get("validated_event", input_data.get("event", {}))
    evidence_refs = input_data.get("evidence_refs", [])
    event_id = event.get("event_id", "unknown")
    detection_type = event.get("detection_type")

    collection = f"civilytics_{'persons' if detection_type != 'vehicle_dump' else 'vehicles'}"

    if not evidence_refs:
        log.info("reid.skipped_no_evidence", event_id=event_id)
        return {"matches": [], "embedding_count": 0, "collection": collection, "status": "SKIPPED"}

    model, _ = _get_clip()
    if model is None:
        return {"matches": [], "embedding_count": 0, "collection": collection,
                 "status": "model_not_available"}

    qdrant = _get_qdrant()
    if qdrant is None:
        return {"matches": [], "embedding_count": 0, "collection": collection,
                 "status": "qdrant_unavailable"}

    try:
        from qdrant_client.models import Distance, VectorParams
        names = [c.name for c in qdrant.get_collections().collections]
        if collection not in names:
            qdrant.create_collection(collection_name=collection,
                                      vectors_config=VectorParams(size=_EMBEDDING_DIM, distance=Distance.COSINE))
    except Exception as e:
        log.warning("reid.qdrant_collection_error", error=str(e))
        return {"matches": [], "embedding_count": 0, "collection": collection, "status": "qdrant_error"}

    image_refs = [r for r in evidence_refs if r.endswith((".jpg", ".jpeg", ".png"))]
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
                payload={"event_id": event_id, "evidence_ref": ref, "detection_type": detection_type or ""},
            )])
            upserted += 1
        except Exception as e:
            log.warning("reid.qdrant_upsert_failed", ref=ref, error=str(e))

    matches = []
    if query_vector is not None:
        try:
            results = qdrant.search(collection_name=collection, query_vector=query_vector,
                                     limit=10, score_threshold=0.7)
            matches = [{"event_id": r.payload.get("event_id", ""), "evidence_ref": r.payload.get("evidence_ref", ""),
                        "similarity": round(r.score, 4)}
                       for r in results if r.payload.get("event_id") != event_id]
        except Exception as e:
            log.warning("reid.qdrant_search_failed", error=str(e))

    log.info("reid.completed", event_id=event_id, match_count=len(matches), embedding_count=upserted)
    return {"matches": matches, "embedding_count": upserted, "collection": collection,
            "match_count": len(matches), "status": "COMPLETED"}
