# backend/app/evidence/dropbox_sync.py
"""Dropbox file sync with resumable upload and retry."""
import logging
import time

import httpx

logger = logging.getLogger(__name__)

DROPBOX_UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"


class DropboxSync:
    def __init__(self, access_token: str = ""):
        self._token = access_token
        self._client = httpx.AsyncClient(timeout=60)

    async def upload_file(
        self, local_path: str, dropbox_path: str, max_retries: int = 3
    ) -> dict:
        """Upload a file to Dropbox with retry. Returns upload metadata."""
        if not self._token:
            logger.warning("Dropbox token not configured, skipping upload")
            return {"status": "skipped", "reason": "no_token"}

        for attempt in range(max_retries):
            try:
                with open(local_path, "rb") as f:
                    resp = await self._client.post(
                        DROPBOX_UPLOAD_URL,
                        headers={
                            "Authorization": f"Bearer {self._token}",
                            "Dropbox-API-Arg": f'{{"path":"{dropbox_path}","mode":"overwrite"}}',
                            "Content-Type": "application/octet-stream",
                        },
                        content=f.read(),
                    )
                    resp.raise_for_status()
                    return {"status": "synced", "metadata": resp.json()}
            except Exception as e:
                wait = 2 ** attempt
                logger.warning("Dropbox upload attempt %d failed: %s, retrying in %ds", attempt + 1, e, wait)
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(wait)

        return {"status": "failed", "error": "max_retries_exceeded"}

    def build_dropbox_path(self, camera_id: str, zone: str, filename: str, date_str: str) -> str:
        safe_zone = zone.replace(" ", "-")
        return f"/WasteWatch/{safe_zone}/{camera_id}/{date_str}/{filename}"

    async def close(self):
        await self._client.aclose()
