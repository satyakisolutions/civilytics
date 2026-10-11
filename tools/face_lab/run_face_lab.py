#!/usr/bin/env python3
"""Face-ID lab: live face detection + anonymous subject IDs on an RTSP camera.

Every face that passes the feature's quality gate is matched once per visit:
  S-001 - New                 first time this face is seen
  S-001 - Repeat x2 (0.71)    same face came back (similarity >= --auto)
  S-004 - maybe S-001? (0.47) uncertain (--review <= similarity < --auto)
Leaving the view for longer than --gone-s and coming back is a new visit.

Live sources: the picture (window / HTTP stream) runs at the camera's frame rate;
face processing runs on its own thread as fast as the device allows (GPU with
onnxruntime-directml, see README) and its latest boxes are drawn on every frame.

Examples:
  python run_face_lab.py --source "rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101"
  python run_face_lab.py --source "rtsp://..." --no-display --serve-port 8090
  python run_face_lab.py --source clip.mp4 --no-display
  python run_face_lab.py --source 0          # laptop webcam

Keys (display window): q = quit, r = reset gallery, s = save snapshot.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from facelab import DEFAULT_MODELS_DIR  # noqa: E402
from facelab.faces import Embedder, FaceAnalyzer, MultiFaceDetector, resolve_providers  # noqa: E402
from facelab.gallery import Gallery  # noqa: E402
from facelab.mjpeg import MjpegServer  # noqa: E402
from facelab.overlay import OverlayItem, draw_items, overlay_items  # noqa: E402
from facelab.session import Session, Visit  # noqa: E402
from facelab.source import FrameSource  # noqa: E402
from app.subjects.config import SubjectSettings  # noqa: E402

logger = logging.getLogger("face_lab")
DEFAULTS = SubjectSettings()
WINDOW = "Face-ID lab (q quit, r reset, s snapshot)"


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, help="RTSP URL, video file path, or webcam index (e.g. 0)")
    p.add_argument("--udp", action="store_true", help="use RTSP over UDP (default TCP)")
    p.add_argument("--no-hw-decode", action="store_true",
                   help="decode video on the CPU (default: GPU decoding when available)")
    p.add_argument("--device", choices=["auto", "gpu", "cpu"], default="auto",
                   help="inference device: auto = GPU if onnxruntime-directml/CUDA is installed, else CPU")
    p.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR, help="folder with det_10g.onnx and w600k_r50.onnx")
    p.add_argument("--det-size", type=int, default=640, help="detector input size (GPU/DirectML supports 640 only)")
    p.add_argument("--det-thresh", type=float, default=0.5)
    p.add_argument("--process-fps", type=float, default=0.0,
                   help="cap on face-processing rate (0 = as fast as the device allows)")
    p.add_argument("--auto", type=float, default=DEFAULTS.match_auto, help="similarity for automatic repeat match")
    p.add_argument("--review", type=float, default=DEFAULTS.match_review, help="similarity for 'maybe same person'")
    p.add_argument("--min-px", type=float, default=DEFAULTS.min_px, help="minimum face width in pixels")
    p.add_argument("--min-sharpness", type=float, default=DEFAULTS.min_sharpness)
    p.add_argument("--min-det", type=float, default=DEFAULTS.min_det)
    p.add_argument("--max-yaw", type=float, default=DEFAULTS.max_yaw)
    p.add_argument("--need-samples", type=int, default=3, help="good faces to collect before matching a visit")
    p.add_argument("--collect-s", type=float, default=2.0, help="match anyway after this many seconds of good faces")
    p.add_argument("--gone-s", type=float, default=3.0, help="absence that ends a visit (coming back = new visit)")
    p.add_argument("--gallery", type=Path, default=None, help="folder to load/save subjects so IDs survive restarts")
    p.add_argument("--out", type=Path, default=HERE / "runs", help="where run logs and face crops are written")
    p.add_argument("--no-display", action="store_true", help="no window; print results only")
    p.add_argument("--serve-port", type=int, default=0,
                   help="also stream the annotated view as MJPEG over HTTP on this port (e.g. 8090; 0 = off)")
    p.add_argument("--serve-host", default="127.0.0.1",
                   help="address to serve on; 0.0.0.0 lets other machines on the network watch (shows faces!)")
    p.add_argument("--max-seconds", type=float, default=0.0, help="stop after this many seconds (0 = run until q / end of file)")
    return p.parse_args(argv)


def check_models(models_dir: Path) -> tuple[Path, Path]:
    det, rec = models_dir / "det_10g.onnx", models_dir / "w600k_r50.onnx"
    for path in (det, rec):
        if not path.exists() or path.stat().st_size < 1_000_000:
            sys.exit(
                f"Model missing or only a Git LFS pointer: {path}\n"
                "Run `git lfs pull` in the repo (or `python scripts/download_face_models.py`)."
            )
    return det, rec


class RunLog:
    """CSV of every visit result + the best aligned face per visit (private, local only)."""

    FIELDS = ["time", "visit", "status", "subject", "incident_count", "similarity", "candidate", "good_faces", "best_quality"]

    def __init__(self, out_dir: Path):
        self.dir = out_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
        (self.dir / "faces").mkdir(parents=True, exist_ok=True)
        self._file = open(self.dir / "results.csv", "w", newline="", encoding="utf-8")
        self._csv = csv.DictWriter(self._file, fieldnames=self.FIELDS)
        self._csv.writeheader()

    def record(self, visit: Visit) -> None:
        r = visit.result
        row = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "visit": visit.visit_id,
            "status": visit.status,
            "subject": r.code if r else "",
            "incident_count": r.incident_count if r else "",
            "similarity": f"{r.similarity:.3f}" if r and r.similarity is not None else "",
            "candidate": (r.candidate_code or "") if r else "",
            "good_faces": len(visit.samples),
            "best_quality": f"{visit.samples[0][0]:.3f}" if visit.samples else "",
        }
        self._csv.writerow(row)
        self._file.flush()
        if visit.samples:
            name = f"{r.code}_visit{visit.visit_id:04d}_{visit.status}.jpg"
            cv2.imwrite(str(self.dir / "faces" / name), visit.samples[0][2])
        print(
            f"[visit {visit.visit_id}] {visit.status.upper():8s} {row['subject']:6s} "
            f"count={row['incident_count']} sim={row['similarity'] or '-'} "
            f"candidate={row['candidate'] or '-'} faces={row['good_faces']}",
            flush=True,
        )

    def close(self) -> None:
        self._file.close()


class RateMeter:
    def __init__(self):
        self.fps, self._n, self._t0 = 0.0, 0, time.monotonic()

    def tick(self) -> None:
        self._n += 1
        elapsed = time.monotonic() - self._t0
        if elapsed >= 1.0:
            self.fps, self._n, self._t0 = self._n / elapsed, 0, time.monotonic()


class Processor(threading.Thread):
    """Face processing on its own thread: newest frame in, overlay items out."""

    def __init__(self, source: FrameSource, analyzer: FaceAnalyzer, session: Session, need_samples: int, min_gap: float):
        super().__init__(name="face-processor", daemon=True)
        self.source, self.analyzer, self.session = source, analyzer, session
        self.need_samples, self.min_gap = need_samples, min_gap
        self.meter = RateMeter()
        self.reset_requested = threading.Event()
        self.error: BaseException | None = None
        self.last_ts = 0.0
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._items: list[OverlayItem] = []
        self._in_view = 0

    def snapshot(self) -> tuple[list[OverlayItem], float, int]:
        with self._lock:
            return self._items, self.meter.fps, self._in_view

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        seq, last_processed = -1, -1e9
        try:
            while not self._stop_event.is_set():
                frame, ts, seq = self.source.wait_new(seq, timeout=1.0)
                if frame is None or ts - last_processed < self.min_gap:
                    continue
                last_processed = self.last_ts = ts
                if self.reset_requested.is_set():
                    self.session.finish(ts)
                    self.session.gallery.subjects.clear()
                    self.reset_requested.clear()
                    logger.info("Gallery reset")
                visits = self.session.step(self.analyzer.analyze(frame), ts)
                items = overlay_items(visits, self.need_samples)
                with self._lock:
                    self._items, self._in_view = items, len(visits)
                self.meter.tick()
        except BaseException as exc:  # surface to the main loop instead of dying silently
            self.error = exc
            logger.exception("Face processing stopped")


def present(frame, items, stats, args, server, log) -> str:
    """Draw, stream and show one frame. Returns the key pressed ("q", "r", ...) or ""."""
    shown = draw_items(frame, items, stats)
    if server is not None:
        server.publish(shown)
    if args.no_display:
        return ""
    if shown.shape[1] > 1600:
        shown = cv2.resize(shown, (1600, int(shown.shape[0] * 1600 / shown.shape[1])))
    cv2.imshow(WINDOW, shown)
    code = cv2.waitKey(1) & 0xFF
    key = chr(code) if code != 255 else ""
    if key == "s":
        path = log.dir / f"snapshot_{datetime.now().strftime('%H%M%S')}.jpg"
        cv2.imwrite(str(path), shown)
        logger.info("Saved %s", path)
    return key


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    det_path, rec_path = check_models(args.models_dir)
    settings = SubjectSettings(
        min_det=args.min_det, min_px=args.min_px, min_sharpness=args.min_sharpness, max_yaw=args.max_yaw,
        match_auto=args.auto, match_review=args.review,
    )
    providers = resolve_providers(args.device)
    logger.info("Loading models from %s", args.models_dir)
    detector = MultiFaceDetector(str(det_path), input_size=(args.det_size, args.det_size),
                                 det_thresh=args.det_thresh, providers=providers)
    analyzer = FaceAnalyzer(detector, Embedder(str(rec_path), providers=providers), settings)
    device = "GPU" if detector.provider != "CPUExecutionProvider" else "CPU"
    logger.info("Inference on %s (%s)", device, detector.provider)

    gallery_kwargs = dict(match_auto=args.auto, match_review=args.review, max_faces=settings.max_faces)
    gallery = Gallery.load(args.gallery, **gallery_kwargs) if args.gallery else Gallery(**gallery_kwargs)
    if gallery.subjects:
        logger.info("Loaded %d subjects from %s", len(gallery.subjects), args.gallery)

    log = RunLog(args.out)
    session = Session(
        gallery, need_samples=args.need_samples, collect_s=args.collect_s, gone_after_s=args.gone_s,
        on_result=log.record, embed=analyzer.embed,
    )
    source = FrameSource(args.source, rtsp_tcp=not args.udp, hw_decode=not args.no_hw_decode)
    source.start()
    logger.info("Run folder: %s", log.dir)
    server = None
    if args.serve_port:
        server = MjpegServer(args.serve_host, args.serve_port)
        server.start()
        shown_host = "localhost" if args.serve_host in ("127.0.0.1", "0.0.0.0") else args.serve_host
        logger.info("Streaming annotated view: http://%s:%d/stream.mjpg (open in VLC or a browser)",
                    shown_host, server.port)
        if args.serve_host == "0.0.0.0":
            logger.warning("Stream is reachable from other machines on this network and shows faces.")

    min_gap = 1.0 / args.process_fps if args.process_fps > 0 else 0.0
    started = time.monotonic()
    now = 0.0
    processor = None
    try:
        if source.live:
            processor = Processor(source, analyzer, session, args.need_samples, min_gap)
            processor.start()
            video = RateMeter()
            seq = -1
            while True:
                if args.max_seconds and time.monotonic() - started > args.max_seconds:
                    break
                if processor.error is not None:
                    break
                if args.no_display and server is None:
                    time.sleep(0.2)
                    continue
                frame, _, seq = source.wait_new(seq, timeout=1.0)
                if frame is None:
                    continue
                video.tick()
                items, face_fps, in_view = processor.snapshot()
                stats = (f"video {video.fps:.0f} fps | faces {face_fps:.1f} fps ({device}) | "
                         f"subjects {len(gallery.subjects)} | in view {in_view}")
                key = present(frame, items, stats, args, server, log)
                if key == "q":
                    break
                if key == "r":
                    processor.reset_requested.set()
        else:
            last_processed, items, meter = -1e9, [], RateMeter()
            while True:
                if args.max_seconds and time.monotonic() - started > args.max_seconds:
                    break
                frame, now = source.read()
                if frame is None:
                    break
                if now - last_processed >= min_gap:
                    last_processed = now
                    visits = session.step(analyzer.analyze(frame), now)
                    items = overlay_items(visits, args.need_samples)
                    meter.tick()
                if args.no_display and server is None:
                    continue
                stats = f"faces {meter.fps:.1f} fps ({device}) | subjects {len(gallery.subjects)}"
                key = present(frame, items, stats, args, server, log)
                if key == "q":
                    break
                if key == "r":
                    session.finish(now)
                    gallery.subjects.clear()
                    items = []
                    logger.info("Gallery reset")
    except KeyboardInterrupt:
        pass
    finally:
        if processor is not None:
            processor.stop()
            processor.join(timeout=5)
            now = processor.last_ts
        session.finish(now)
        source.stop()
        if server is not None:
            server.stop()
        log.close()
        if not args.no_display:
            cv2.destroyAllWindows()
        if args.gallery:
            gallery.save(args.gallery)
            logger.info("Saved %d subjects to %s", len(gallery.subjects), args.gallery)
    print("\nSummary:")
    for s in sorted(gallery.subjects.values(), key=lambda s: s.code_num):
        print(f"  {s.code}: {s.incident_count} visit(s), {len(s.embeddings)} stored face(s)")
    print(f"Results: {log.dir / 'results.csv'}")
    return 1 if processor is not None and processor.error is not None else 0


if __name__ == "__main__":
    sys.exit(main())
