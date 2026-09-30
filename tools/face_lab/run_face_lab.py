#!/usr/bin/env python3
"""Face-ID lab: live face detection + anonymous subject IDs on an RTSP camera.

Every face that passes the feature's quality gate is matched once per visit:
  S-001 - New                 first time this face is seen
  S-001 - Repeat x2 (0.71)    same face came back (similarity >= --auto)
  S-004 - maybe S-001? (0.47) uncertain (--review <= similarity < --auto)
Leaving the view for longer than --gone-s and coming back is a new visit.

Examples:
  python run_face_lab.py --source "rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101"
  python run_face_lab.py --source clip.mp4 --no-display
  python run_face_lab.py --source 0          # laptop webcam

Keys (display window): q = quit, r = reset gallery, s = save snapshot.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from facelab import DEFAULT_MODELS_DIR  # noqa: E402
from facelab.faces import FaceAnalyzer, MultiFaceDetector  # noqa: E402
from facelab.gallery import Gallery  # noqa: E402
from facelab.mjpeg import MjpegServer  # noqa: E402
from facelab.overlay import draw  # noqa: E402
from facelab.session import Session, Visit  # noqa: E402
from facelab.source import FrameSource  # noqa: E402
from app.subjects.config import SubjectSettings  # noqa: E402
from app.subjects.embedder import ArcFaceEmbedder  # noqa: E402

logger = logging.getLogger("face_lab")
DEFAULTS = SubjectSettings()


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, help="RTSP URL, video file path, or webcam index (e.g. 0)")
    p.add_argument("--udp", action="store_true", help="use RTSP over UDP (default TCP)")
    p.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR, help="folder with det_10g.onnx and w600k_r50.onnx")
    p.add_argument("--det-size", type=int, default=640, help="detector input size (640 for whole frames; larger finds smaller faces, slower)")
    p.add_argument("--det-thresh", type=float, default=0.5)
    p.add_argument("--process-fps", type=float, default=5.0, help="max frames per second sent to face processing")
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


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    det_path, rec_path = check_models(args.models_dir)
    settings = SubjectSettings(
        min_det=args.min_det, min_px=args.min_px, min_sharpness=args.min_sharpness, max_yaw=args.max_yaw,
        match_auto=args.auto, match_review=args.review,
    )
    logger.info("Loading models from %s", args.models_dir)
    analyzer = FaceAnalyzer(
        MultiFaceDetector(str(det_path), input_size=(args.det_size, args.det_size), det_thresh=args.det_thresh),
        ArcFaceEmbedder(str(rec_path)),
        settings,
    )
    gallery_kwargs = dict(match_auto=args.auto, match_review=args.review, max_faces=settings.max_faces)
    gallery = Gallery.load(args.gallery, **gallery_kwargs) if args.gallery else Gallery(**gallery_kwargs)
    if gallery.subjects:
        logger.info("Loaded %d subjects from %s", len(gallery.subjects), args.gallery)

    log = RunLog(args.out)
    session = Session(
        gallery, need_samples=args.need_samples, collect_s=args.collect_s, gone_after_s=args.gone_s,
        on_result=log.record,
    )
    source = FrameSource(args.source, rtsp_tcp=not args.udp)
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

    window = "Face-ID lab (q quit, r reset, s snapshot)"
    min_gap = 1.0 / args.process_fps if args.process_fps > 0 else 0.0
    started = time.monotonic()
    last_processed = -1e9
    visits: list[Visit] = []
    now = 0.0
    fps_frames, fps_t0, fps = 0, time.monotonic(), 0.0
    try:
        while True:
            if args.max_seconds and time.monotonic() - started > args.max_seconds:
                break
            frame, now = source.read()
            if frame is None:
                if source.ended:
                    break
                continue
            if now - last_processed >= min_gap:
                last_processed = now
                visits = session.step(analyzer.analyze(frame), now)
                fps_frames += 1
                if time.monotonic() - fps_t0 >= 1.0:
                    fps, fps_frames, fps_t0 = fps_frames / (time.monotonic() - fps_t0), 0, time.monotonic()
            if args.no_display and server is None:
                continue
            stats = f"proc {fps:.1f} fps | subjects {len(gallery.subjects)} | in view {len(visits)}"
            shown = draw(frame, visits, args.need_samples, stats)
            if server is not None:
                server.publish(shown)
            if args.no_display:
                continue
            if shown.shape[1] > 1600:
                scale = 1600 / shown.shape[1]
                shown = cv2.resize(shown, (1600, int(shown.shape[0] * scale)))
            cv2.imshow(window, shown)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                session.finish(now)
                gallery.subjects.clear()
                logger.info("Gallery reset")
            if key == ord("s"):
                path = log.dir / f"snapshot_{datetime.now().strftime('%H%M%S')}.jpg"
                cv2.imwrite(str(path), shown)
                logger.info("Saved %s", path)
    except KeyboardInterrupt:
        pass
    finally:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
