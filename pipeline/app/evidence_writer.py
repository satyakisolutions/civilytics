"""Evidence lifecycle manager.

Handles JSON metadata, keyframe capture, and recording retention
based on VLM outcome (CLEAR → delete video, keep keyframe+JSON;
VIOLATION → keep everything).

Video + keyframe come from rtsp://mediamtx:8554/{cam}-out via ffmpeg
subprocesses spawned on start_evidence and stopped on finalize.
"""

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

EVIDENCE_BASE = Path(os.environ.get("WW_EVIDENCE_DIR", "/app/evidence"))
RETENTION_DAYS = int(os.environ.get("WW_EVIDENCE_RETENTION_DAYS", "7"))
MEDIAMTX_HOST = os.environ.get("WW_MEDIAMTX_HOST", "mediamtx")
MEDIAMTX_PORT = os.environ.get("WW_MEDIAMTX_PORT", "8554")
MAX_RECORDING_SECS = int(os.environ.get("WW_EVIDENCE_MAX_RECORDING_S", "120"))
TEMP_DIR = Path(os.environ.get("WW_EVIDENCE_TMP_DIR", "/tmp/wastewatch/evidence_tmp"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)
LATEST_FRAMES_DIR = Path(os.environ.get("WW_LATEST_FRAMES_DIR", "/tmp/wastewatch/latest_frames"))
LATEST_FRAMES_DIR.mkdir(parents=True, exist_ok=True)
# Per-camera segmented ring buffer — one 10s segment per file, retained for
# 300s. Finalize concatenates the segments covering the event window.
SEGMENT_DIR = Path(os.environ.get("WW_SEGMENT_DIR", "/tmp/wastewatch/segments"))
SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
SEGMENT_SECS = int(os.environ.get("WW_SEGMENT_SECS", "5"))
SEGMENT_RETENTION_S = int(os.environ.get("WW_SEGMENT_RETENTION_S", "300"))


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
        # Per-camera persistent keyframe grabber (writes latest JPEG at 2fps).
        # Short-duration OBSERVE→EXIT events finalize faster than ffmpeg can
        # grab a fresh frame, so we keep a rolling cache and copy on demand.
        self._latest_procs: dict[str, subprocess.Popen] = {}
        # Per-camera persistent segmented recorder (10s .ts chunks).
        self._segment_procs: dict[str, subprocess.Popen] = {}
        for sub in ("suspicious", "violations"):
            try:
                (self._base / sub).mkdir(parents=True, exist_ok=True)
            except PermissionError:
                logger.warning("Evidence subdir perm denied: %s", self._base / sub)

    def _ensure_latest_grabber(self, camera_id: str) -> None:
        proc = self._latest_procs.get(camera_id)
        if proc is not None and proc.poll() is None:
            return
        rtsp = f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_PORT}/{camera_id}-out"
        out = LATEST_FRAMES_DIR / f"{camera_id}.jpg"
        try:
            new_proc = subprocess.Popen(
                ["ffmpeg", "-y", "-loglevel", "error", "-rtsp_transport", "tcp",
                 "-i", rtsp, "-vf", "fps=2,scale=1280:-1", "-q:v", "4",
                 "-update", "1", str(out)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._latest_procs[camera_id] = new_proc
            logger.info("Spawned persistent keyframe grabber for %s → %s", camera_id, out)
        except Exception:
            logger.exception("Failed to spawn keyframe grabber for %s", camera_id)

    def _ensure_segmenter(self, camera_id: str) -> None:
        """One persistent ffmpeg per camera writing 10s .ts segments.
        Finalize cherry-picks segments overlapping the event's time window."""
        proc = self._segment_procs.get(camera_id)
        if proc is not None and proc.poll() is None:
            return
        rtsp = f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_PORT}/{camera_id}-out"
        cam_dir = SEGMENT_DIR / camera_id
        cam_dir.mkdir(parents=True, exist_ok=True)
        pattern = str(cam_dir / "seg_%04d.ts")
        try:
            new_proc = subprocess.Popen(
                ["ffmpeg", "-y", "-loglevel", "error", "-rtsp_transport", "tcp",
                 "-i", rtsp, "-c", "copy",
                 "-f", "segment", "-segment_time", str(SEGMENT_SECS),
                 "-segment_wrap", "40",   # ring buffer: wraps after 40 segs (~7 min)
                 "-reset_timestamps", "1", "-segment_format", "mpegts",
                 pattern],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self._segment_procs[camera_id] = new_proc
            logger.info("Spawned segmented recorder for %s → %s", camera_id, pattern)
        except Exception:
            logger.exception("Failed to spawn segmenter for %s", camera_id)

    def _collect_segments(self, camera_id: str, start_ts: float, end_ts: float) -> list[Path]:
        """Return segment files whose mtime overlaps [start_ts, end_ts]."""
        cam_dir = SEGMENT_DIR / camera_id
        if not cam_dir.exists():
            return []
        # Pad window: 2s pre-roll (ffmpeg segment write lag) + 2s post-roll
        lo = start_ts - 2
        hi = end_ts + 2
        matches: list[tuple[float, Path]] = []
        for p in cam_dir.glob("seg_*.ts"):
            try:
                mt = p.stat().st_mtime
                # Segment's mtime = its end time; duration ≈ SEGMENT_SECS
                seg_start = mt - SEGMENT_SECS
                if seg_start <= hi and mt >= lo:
                    matches.append((mt, p))
            except OSError:
                continue
        matches.sort(key=lambda t: t[0])
        return [p for _, p in matches]

    def start_evidence(
        self, camera_id: str, track_id: int, class_name: str,
        roi_name: str = "Restricted Zone",
    ) -> str:
        """Called on OBSERVE — begin tracking evidence for this event.

        Spawns two ffmpeg subprocesses:
          1. One-shot keyframe grab (1 JPEG, dies after ~3s).
          2. Bounded video recorder (`-t MAX_RECORDING_SECS`, copies codec).
        Both pull from rtsp://mediamtx:8554/{camera_id}-out so the recorded
        footage includes the annotated overlays.
        """
        event_id = uuid.uuid4().hex[:12]
        tmp_keyframe = TEMP_DIR / f"evt_{event_id}_keyframe.jpg"

        # Ensure persistent grabbers (idempotent, one per camera).
        self._ensure_latest_grabber(camera_id)
        self._ensure_segmenter(camera_id)

        # Keyframe grab deferred to VIOLATION (red-bbox) transition —
        # pre-escalation frames aren't informative for evidence.

        self._active[(camera_id, track_id)] = {
            "event_id": event_id,
            "camera_id": camera_id,
            "track_id": track_id,
            "class_name": class_name,
            "roi_name": roi_name,
            "timestamp_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "start_ts_mono": time.time(),
            "fsm_states": ["observe"],
            "bbox_path": [],
            "best_confidence": 0.0,
            "tmp_keyframe": tmp_keyframe,
        }
        logger.info("Evidence started: %s for %s track=%d", event_id, camera_id, track_id)
        return event_id

    def capture_violation_keyframe(self, camera_id: str, track_id: int, jpeg_bytes: bytes | None = None) -> None:
        """Store the exact red-bbox frame as the evidence keyframe.

        Called from the pipeline at the VIOLATION transition. The caller
        passes the JPEG bytes that were also sent to VLM — that is the
        frame showing the correct target with the red bbox drawn on it.
        Using the RTSP cache instead would race with newer entrants: by
        the time the cache JPEG is read (~0.5s later), another person
        may already be in frame and the wrong target gets recorded.
        """
        ctx = self._active.get((camera_id, track_id))
        if not ctx:
            logger.warning("capture_violation_keyframe: no active ctx for %s track=%d", camera_id, track_id)
            return
        tmp_kf = ctx.get("tmp_keyframe")
        if not tmp_kf:
            return
        if jpeg_bytes and len(jpeg_bytes) > 1000:
            try:
                tmp_kf.write_bytes(jpeg_bytes)
                logger.info("Violation keyframe stored (inline jpeg): %s track=%d bytes=%d",
                            camera_id, track_id, len(jpeg_bytes))
                return
            except Exception:
                logger.exception("Violation keyframe write failed (inline) for %s", camera_id)
        # Fallback only — last-resort copy from rolling cache
        cached_kf = LATEST_FRAMES_DIR / f"{camera_id}.jpg"
        if cached_kf.exists() and cached_kf.stat().st_size > 1000:
            try:
                shutil.copy2(str(cached_kf), str(tmp_kf))
                logger.warning("Violation keyframe from cache fallback: %s track=%d (inline jpeg missing)",
                               camera_id, track_id)
            except Exception:
                logger.exception("Violation keyframe copy failed for %s", camera_id)

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

        # Warnings (outcome=="clear") save nothing — user requested that
        # only confirmed violations produce evidence (video + keyframe + JSON).
        if outcome == "clear":
            tmp_kf = ctx.get("tmp_keyframe")
            if tmp_kf and tmp_kf.exists():
                try:
                    tmp_kf.unlink()
                except OSError:
                    pass
            logger.info("Evidence skipped (clear outcome): track=%d on %s", track_id, camera_id)
            return None

        timestamp_end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        event_id = ctx["event_id"]
        date_str = time.strftime("%Y-%m-%d")

        subdir = self._base / "violations" / camera_id / date_str
        subdir.mkdir(parents=True, exist_ok=True)

        # Keyframe — prefer inline bytes, else pick up copied cached frame.
        tmp_kf = ctx.get("tmp_keyframe")
        keyframe_path = subdir / f"evt_{event_id}_keyframe.jpg"
        keyframe_url = ""
        if keyframe_data:
            keyframe_path.write_bytes(keyframe_data)
            keyframe_url = str(keyframe_path)
        elif tmp_kf and tmp_kf.exists() and tmp_kf.stat().st_size > 1000:
            try:
                shutil.move(str(tmp_kf), str(keyframe_path))
                keyframe_url = str(keyframe_path)
            except OSError:
                logger.exception("Failed to move keyframe %s", tmp_kf)

        # Video — VIOLATION only. Event window spans ENTER→EXIT so the
        # clip covers the full time the target is in frame. Wait at least
        # SEGMENT_SECS+2 for the current segment to rotate so the tail
        # of the event is included.
        video_url = ""
        if outcome not in ("clear",):
            start_ts = ctx.get("start_ts_mono", time.time() - dwell_time_s)
            end_ts = time.time()
            time.sleep(SEGMENT_SECS + 2)  # ensure trailing segment is flushed
            segs = self._collect_segments(camera_id, start_ts, end_ts)
            if segs:
                concat_list = subdir / f"evt_{event_id}_concat.txt"
                try:
                    concat_list.write_text("".join(f"file '{p}'\n" for p in segs))
                    dest = subdir / f"evt_{event_id}.mp4"
                    result = subprocess.run(
                        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                         "-safe", "0", "-i", str(concat_list),
                         "-c", "copy", "-movflags", "+faststart", str(dest)],
                        capture_output=True, timeout=20,
                    )
                    if result.returncode == 0 and dest.exists() and dest.stat().st_size > 8000:
                        video_url = str(dest)
                    else:
                        logger.warning("Segment concat failed for %s: rc=%d stderr=%s",
                                       event_id, result.returncode, result.stderr[:200])
                    concat_list.unlink(missing_ok=True)
                except Exception:
                    logger.exception("Segment concat exception for %s", event_id)

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

    def rotate(self, retention_days: int | None = None) -> int:
        """Delete evidence directories older than retention_days.

        Scans both evidence/suspicious/ and evidence/violations/ for
        date-stamped subdirectories (YYYY-MM-DD). Any directory whose
        date is older than the cutoff is removed entirely.

        Called periodically from the pipeline's health_check_cb (every
        10s is fine — the scan is cheap since it only reads directory
        names, not file contents).

        Returns the number of directories removed.
        """
        days = retention_days if retention_days is not None else RETENTION_DAYS
        cutoff = datetime.utcnow().date() - timedelta(days=days)
        removed = 0

        for category in ("suspicious", "violations"):
            category_dir = self._base / category
            if not category_dir.exists():
                continue
            # Structure: evidence/{category}/{camera_id}/{YYYY-MM-DD}/
            for camera_dir in category_dir.iterdir():
                if not camera_dir.is_dir():
                    continue
                for date_dir in camera_dir.iterdir():
                    if not date_dir.is_dir():
                        continue
                    try:
                        dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d").date()
                    except ValueError:
                        continue  # Not a date directory
                    if dir_date < cutoff:
                        try:
                            shutil.rmtree(date_dir)
                            removed += 1
                            logger.info(
                                "Rotated evidence: %s (%s, %d days old)",
                                date_dir, category, (datetime.utcnow().date() - dir_date).days,
                            )
                        except OSError:
                            logger.exception("Failed to rotate %s", date_dir)
                # Clean up empty camera directories
                try:
                    if camera_dir.exists() and not any(camera_dir.iterdir()):
                        camera_dir.rmdir()
                except OSError:
                    pass

        if removed:
            logger.info("Evidence rotation complete: removed %d directories (retention=%d days)", removed, days)
        return removed
