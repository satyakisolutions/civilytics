#!/usr/bin/env python3
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Historical clip VLM publisher.

Replays recorded MP4 clips through a GStreamer pipeline ending in our
custom `wastewatchvlmpublisher` sink element. Each clip produces exactly
one `wastewatch.vlm.requests` Kafka message, which the VLM worker then
picks up and turns into a VLMAnalysis row + VLMResult Kafka message.

Intended to run as a one-shot container (`restart: no`) alongside the
rest of the LSG-WasteWatch stack.

All configuration is environment-driven — no host installs, no CLI
parameters needed beyond the clip manifest embedded below.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Make the custom gst plugin importable. The plugin directory lives in
# pipeline/plugins/gstwastewatchvlmpublisher/python/; we insert it onto
# sys.path so the module is loadable by name.
PLUGIN_DIR = Path("/app/plugins/gstwastewatchvlmpublisher/python")
if PLUGIN_DIR.is_dir():
    sys.path.insert(0, str(PLUGIN_DIR))

# Importing the module auto-registers the `wastewatchvlmpublisher`
# element with the GStreamer registry.
import gstwastewatchvlmpublisher  # noqa: F401,E402

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.history-publisher")

Gst.init(None)

# ── Configuration from env ─────────────────────────────────────────
VIDEO_DIR = Path(os.environ.get("HISTORY_VIDEO_DIR", "/videos"))
KAFKA_BOOTSTRAP = os.environ.get("HISTORY_KAFKA_BOOTSTRAP", "kafka:9092")
TOPIC = os.environ.get("HISTORY_VLM_TOPIC", "wastewatch.vlm.requests")
CAMERA_ID = os.environ.get("HISTORY_CAMERA_ID", "")
ZONE_NAME = os.environ.get("HISTORY_ZONE_NAME", "Lake Shore Gardens - Block 1")
SAMPLE_COUNT = int(os.environ.get("HISTORY_SAMPLE_COUNT", "6"))
FRAME_WIDTH = int(os.environ.get("HISTORY_FRAME_WIDTH", "512"))
SESSION_MAP_PATH = Path(os.environ.get("HISTORY_SESSION_MAP", "/config/session-map.json"))

if not CAMERA_ID:
    logger.error("HISTORY_CAMERA_ID is required (UUID of cam-01)")
    sys.exit(2)

# (track_id, filename, dwell_s, path_summary)
CLIPS = [
    (101, "VID-20260131-WA0003.mp4", 37.0, "Person walks along eastern fence"),
    (102, "VID-20260131-WA0004.mp4", 33.1, "Person loiters near main entrance"),
    (103, "VID-20260131-WA0005.mp4", 43.0, "Person approaches the community bin"),
    (104, "VID-20260131-WA0006.mp4", 22.4, "Person crosses driveway carrying a bag"),
    (105, "VID-20260131-WA0007.mp4", 94.2, "Extended dwell near bin corner"),
]


def load_session_map() -> dict[int, str]:
    if not SESSION_MAP_PATH.exists():
        logger.warning(
            "session map %s missing — falling back to empty UUIDs",
            SESSION_MAP_PATH,
        )
        return {}
    raw = json.loads(SESSION_MAP_PATH.read_text())
    return {int(k): str(v) for k, v in raw.items()}


def build_pipeline(
    video: Path,
    camera_id: str,
    track_id: int,
    session_id: str,
    zone: str,
    dwell: float,
    path_summary: str,
) -> Gst.Pipeline:
    # We deliberately use uridecodebin so hardware-accel decoders (nvv4l2decoder,
    # nvvideoconvert) can be picked up inside a DeepStream container without
    # forcing any specific decoder chain here.
    desc = (
        f'uridecodebin name=src uri=file://{video.as_posix()} '
        f'! queue '
        f'! videoconvert '
        f'! videoscale '
        f'! video/x-raw,width={FRAME_WIDTH} '
        f'! jpegenc quality=85 '
        f'! wastewatchvlmpublisher name=vlmsink '
        f'    camera-id="{camera_id}" '
        f'    track-id={track_id} '
        f'    session-id="{session_id}" '
        f'    zone-name="{zone}" '
        f'    dwell-time={dwell} '
        f'    path-summary="{path_summary}" '
        f'    sample-count={SAMPLE_COUNT} '
        f'    kafka-bootstrap="{KAFKA_BOOTSTRAP}" '
        f'    topic="{TOPIC}"'
    )
    logger.debug("pipeline: %s", desc)
    return Gst.parse_launch(desc)


def run_pipeline(pipeline: Gst.Pipeline, label: str) -> bool:
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    success = {"ok": True}

    def on_eos(_bus, _msg):
        logger.info("[%s] EOS", label)
        loop.quit()

    def on_error(_bus, msg):
        err, dbg = msg.parse_error()
        logger.error("[%s] ERROR: %s  (%s)", label, err.message, dbg)
        success["ok"] = False
        loop.quit()

    bus.connect("message::eos", on_eos)
    bus.connect("message::error", on_error)

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
    return success["ok"]


def main() -> int:
    session_map = load_session_map()
    logger.info(
        "history-publisher starting: video_dir=%s camera_id=%s clips=%d",
        VIDEO_DIR, CAMERA_ID[:8] + "...", len(CLIPS),
    )

    published = 0
    for track_id, fname, dwell, path_summary in CLIPS:
        video = VIDEO_DIR / fname
        if not video.exists():
            logger.warning("[skip] %s not found", video)
            continue
        session_id = session_map.get(track_id, "")
        if not session_id:
            logger.warning("[%s] no session UUID for track_id=%d", fname, track_id)
            continue

        logger.info("[%s] track_id=%d dwell=%.1fs session=%s...", fname, track_id, dwell, session_id[:8])
        pipeline = build_pipeline(
            video=video,
            camera_id=CAMERA_ID,
            track_id=track_id,
            session_id=session_id,
            zone=ZONE_NAME,
            dwell=dwell,
            path_summary=path_summary,
        )
        ok = run_pipeline(pipeline, label=fname)
        if ok:
            published += 1

    logger.info("history-publisher done — %d/%d clips published", published, len(CLIPS))
    return 0 if published == len(CLIPS) else 1


if __name__ == "__main__":
    sys.exit(main())
