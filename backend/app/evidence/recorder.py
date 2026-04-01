# backend/app/evidence/recorder.py
"""Evidence recording manager — manages pre/post event clip extraction."""
import logging
import os
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EVIDENCE_DIR = Path("/tmp/wastewatch/evidence")

# SECURITY: Validate camera_id to prevent path traversal
_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


def _validate_camera_id(camera_id: str) -> str:
    """Validate camera_id is safe for use in file paths."""
    if not _SAFE_ID.match(camera_id):
        raise ValueError(f"Invalid camera_id: {camera_id!r}")
    return camera_id


class EvidenceRecorder:
    def __init__(self, pre_buffer_s: int = 15, post_buffer_s: int = 30):
        self._pre_buffer_s = pre_buffer_s
        self._post_buffer_s = post_buffer_s
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    def save_snapshot(self, camera_id: str, frame_data: bytes, timestamp: datetime | None = None) -> str:
        """Save a snapshot frame to disk. Returns the file path."""
        _validate_camera_id(camera_id)
        ts = timestamp or datetime.now()
        date_dir = EVIDENCE_DIR / "snapshots" / camera_id / ts.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{ts.strftime('%H-%M-%S')}_snapshot.jpg"
        filepath = date_dir / filename
        filepath.write_bytes(frame_data)
        logger.info("Saved snapshot: %s", filepath)
        return str(filepath)

    def save_annotated_snapshot(self, camera_id: str, frame_data: bytes, timestamp: datetime | None = None) -> str:
        _validate_camera_id(camera_id)
        ts = timestamp or datetime.now()
        date_dir = EVIDENCE_DIR / "snapshots" / camera_id / ts.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{ts.strftime('%H-%M-%S')}_annotated.jpg"
        filepath = date_dir / filename
        filepath.write_bytes(frame_data)
        return str(filepath)

    def save_metadata(self, camera_id: str, metadata: dict, timestamp: datetime | None = None) -> str:
        import json
        _validate_camera_id(camera_id)
        ts = timestamp or datetime.now()
        date_dir = EVIDENCE_DIR / "metadata" / camera_id / ts.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{ts.strftime('%H-%M-%S')}_metadata.json"
        filepath = date_dir / filename
        filepath.write_text(json.dumps(metadata, indent=2, default=str))
        return str(filepath)

    def get_evidence_path(self, camera_id: str, evidence_type: str, timestamp: datetime | None = None) -> Path:
        ts = timestamp or datetime.now()
        return EVIDENCE_DIR / evidence_type / camera_id / ts.strftime("%Y-%m-%d")
