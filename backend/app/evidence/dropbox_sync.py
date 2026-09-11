# backend/app/evidence/dropbox_sync.py
"""Dropbox file sync with resumable upload and retry."""
import asyncio
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

DROPBOX_UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"

# Reject path components with traversal attempts or null bytes
_SAFE_PATH_COMPONENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


def _sanitize_path_component(value: str) -> str:
    """Sanitize a single path component — reject traversal and special chars."""
    clean = value.replace(" ", "-").replace("..", "").replace("/", "").replace("\x00", "")
    if not _SAFE_PATH_COMPONENT.match(clean):
        raise ValueError(f"Unsafe path component: {value!r}")
    return clean


class DropboxSync:
    def __init__(self, access_token: str = ""):
        self._token = access_token
        self._client = httpx.AsyncClient(timeout=60)

    async def upload(self, local_path: str, camera_id: str) -> dict:
        """Convenience upload with path derived from camera_id."""
        if not self._token:
            return {"status": "skipped", "reason": "no_token"}
        dropbox_path = f"/WasteWatch/{camera_id}/{local_path.split('/')[-1]}"
        return await self.upload_file(local_path, dropbox_path)

    async def upload_file(
        self, local_path: str, dropbox_path: str, max_retries: int = 3
    ) -> dict:
        """Upload a file to Dropbox with retry. Returns upload metadata."""
        if not self._token:
            logger.warning("Dropbox token not configured, skipping upload")
            return {"status": "skipped", "reason": "no_token"}

        # SECURITY: Use json.dumps to prevent JSON injection in header
        api_arg = json.dumps({"path": dropbox_path, "mode": "overwrite"})

        for attempt in range(max_retries):
            try:
                with open(local_path, "rb") as f:
                    resp = await self._client.post(
                        DROPBOX_UPLOAD_URL,
                        headers={
                            "Authorization": f"Bearer {self._token}",
                            "Dropbox-API-Arg": api_arg,
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
                    await asyncio.sleep(wait)

        return {"status": "failed", "error": "max_retries_exceeded"}

    def build_dropbox_path(self, camera_id: str, zone: str, filename: str, date_str: str) -> str:
        """Build a safe Dropbox path. Validates all components against traversal."""
        safe_zone = _sanitize_path_component(zone)
        safe_cam = _sanitize_path_component(camera_id)
        safe_date = _sanitize_path_component(date_str)
        # Filename: strip directory separators, keep extension
        safe_name = filename.replace("/", "").replace("..", "").replace("\x00", "")
        return f"/WasteWatch/{safe_zone}/{safe_cam}/{safe_date}/{safe_name}"

    async def close(self):
        await self._client.aclose()
