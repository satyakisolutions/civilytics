# backend/app/api/routes/logs.py
from fastapi import APIRouter, Depends, Query
from app.auth.middleware import require_admin
from app.logs.qdrant_store import QdrantLogStore

router = APIRouter(prefix="/system/logs", tags=["logs"])

_qdrant = QdrantLogStore()


@router.get("")
async def search_logs(
    q: str = Query(default="", description="Search query (semantic search)"),
    service: str | None = None,
    limit: int = Query(default=20, le=100),
    _user: dict = Depends(require_admin),
):
    if not q:
        return {"results": [], "message": "Provide a search query via ?q="}
    results = await _qdrant.search(q, limit=limit, service=service)
    return {"results": results, "count": len(results)}
