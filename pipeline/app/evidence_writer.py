"""Evidence lifecycle manager.

Handles JSON metadata, keyframe capture, and recording retention
based on VLM outcome (CLEAR → delete video, keep keyframe+JSON;
VIOLATION → keep everything).
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

EVIDENCE_BASE = Path(os.environ.get("WW_EVIDENCE_DIR", "/app/evidence"))


@dataclass
class EvidenceRecord:
    event_id: str
    camera_id: str
    track_id: int
    timestamp_start: str
    timestamp_end: str
    duration_s: float
    outcome: str  # "violation" | "suspicious" | "clear"
    fsm_states: list[str]
    class_name: str
    action: str
    dwell_time_s: float
    in_roi: bool
    roi_name: str
    vlm_result: dict | None
    bbox_path: list[dict]
    keyframe_url: str
    video_url: str
    tts_sent: bool
    alert_dispatched: bool


class EvidenceWriter:
    """Manages evidence files based on VLM outcome."""

    def __init__(self, base_dir: Path | None = None):
        self._base = base_dir or EVIDENCE_BASE
        self._active: dict[tuple[str, int], dict] = {}

    def start_evidence(
        self, camera_id: str, track_id: int, class_name: str,
        roi_name: str = "Restricted Zone",
    ) -> str:
        """Called on OBSERVE — begin tracking evidence for this event."""
        event_id = uuid.uuid4().hex[:12]
        self._active[(camera_id, track_id)] = {
            "event_id": event_id,
            "camera_id": camera_id,
            "track_id": track_id,
            "class_name": class_name,
            "roi_name": roi_name,
            "timestamp_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fsm_states": ["observe"],
            "bbox_path": [],
            "best_confidence": 0.0,
        }
        logger.info("Evidence started: %s for %s track=%d", event_id, camera_id, track_id)
        return event_id

    def add_frame(
        self, camera_id: str, track_id: int,
        bbox: dict, confidence: float, fsm_state: str,
    ) -> None:
        """Record bbox path and track best keyframe candidate."""
        ctx = self._active.get((camera_id, track_id))
        if not ctx:
            return
        ctx["bbox_path"].append({"t": len(ctx["bbox_path"]), **bbox})
        if fsm_state not in ctx["fsm_states"]:
            ctx["fsm_states"].append(fsm_state)
        if confidence > ctx["best_confidence"]:
            ctx["best_confidence"] = confidence

    def finalize(
        self, camera_id: str, track_id: int,
        outcome: str,
        dwell_time_s: float,
        action: str,
        vlm_result: dict | None = None,
        video_path: str | None = None,
        keyframe_data: bytes | None = None,
        tts_sent: bool = False,
        alert_dispatched: bool = False,
    ) -> EvidenceRecord | None:
        """Called on VLM result or VIOLATION — finalize evidence."""
        ctx = self._active.pop((camera_id, track_id), None)
        if not ctx:
            logger.warning("finalize: no active evidence for %s track=%d", camera_id, track_id)
            return None

        timestamp_end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        event_id = ctx["event_id"]
        date_str = time.strftime("%Y-%m-%d")

        if outcome == "clear":
            subdir = self._base / "suspicious" / camera_id / date_str
        else:
            subdir = self._base / "violations" / camera_id / date_str
        subdir.mkdir(parents=True, exist_ok=True)

        keyframe_path = subdir / f"evt_{event_id}_keyframe.jpg"
        keyframe_url = str(keyframe_path)
        if keyframe_data:
            keyframe_path.write_bytes(keyframe_data)

        video_url = ""
        if outcome == "clear":
            if video_path and os.path.exists(video_path):
                try:
                    os.remove(video_path)
                    logger.info("Deleted recording for cleared event %s", event_id)
                except OSError:
                    logger.exception("Failed to delete recording %s", video_path)
        else:
            if video_path and os.path.exists(video_path):
                dest = subdir / f"evt_{event_id}.mp4"
                try:
                    os.rename(video_path, str(dest))
                    video_url = str(dest)
                except OSError:
                    video_url = video_path

        record = EvidenceRecord(
            event_id=event_id,
            camera_id=camera_id,
            track_id=track_id,
            timestamp_start=ctx["timestamp_start"],
            timestamp_end=timestamp_end,
            duration_s=dwell_time_s,
            outcome=outcome,
            fsm_states=ctx["fsm_states"],
            class_name=ctx["class_name"],
            action=action,
            dwell_time_s=dwell_time_s,
            in_roi=True,
            roi_name=ctx["roi_name"],
            vlm_result=vlm_result,
            bbox_path=ctx["bbox_path"],
            keyframe_url=keyframe_url,
            video_url=video_url,
            tts_sent=tts_sent,
            alert_dispatched=alert_dispatched,
        )

        json_path = subdir / f"evt_{event_id}.json"
        json_path.write_text(json.dumps(asdict(record), indent=2))
        logger.info("Evidence finalized: %s outcome=%s → %s", event_id, outcome, json_path)

        return record
