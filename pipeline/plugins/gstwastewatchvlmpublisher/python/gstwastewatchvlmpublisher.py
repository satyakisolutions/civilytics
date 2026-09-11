# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Custom GStreamer sink element: wastewatchvlmpublisher.

Consumes a stream of JPEG buffers (one frame per buffer), samples N
keyframes at configurable interval, base64-encodes them, and on EOS
publishes a single `wastewatch.vlm.requests` Kafka message conforming to
backend.app.vlm.schemas.VLMRequest.

Designed to be plugged into a decode pipeline, e.g.:

    filesrc location=/videos/clip.mp4
      ! decodebin
      ! videoconvert
      ! videoscale
      ! video/x-raw,width=512
      ! jpegenc
      ! wastewatchvlmpublisher
          camera-id=<uuid>
          track-id=101
          session-id=<uuid>
          zone-name="Lake Shore Gardens - Block 1"
          dwell-time=37
          class-name=person
          path-summary="Person walks along eastern fence"
          kafka-bootstrap=kafka:9092
          topic=wastewatch.vlm.requests
          sample-count=6

This element does not carry any DeepStream dependency and is pure
GStreamer 1.x, so it can run from a plain DeepStream container or any
image with gst-python.

Registration: `register_element()` is called by the import-side harness
(pipeline/app/history_publisher.py); we also register on module import
so the element works under `gst-launch` as well, provided the module
is on `GST_PLUGIN_PATH/python/` or on `PYTHONPATH` and imported.
"""
from __future__ import annotations

import base64
import json
import logging
import os

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstBase", "1.0")

from gi.repository import GObject, Gst, GstBase  # noqa: E402

Gst.init(None)

logger = logging.getLogger("wastewatch.gstvlmpublisher")

ELEMENT_NAME = "wastewatchvlmpublisher"


class GstWasteWatchVlmPublisher(GstBase.BaseSink):
    """Sink element: collects JPEG buffers and flushes a VLMRequest to Kafka on EOS."""

    __gstmetadata__ = (
        "WasteWatch VLM Publisher",
        "Sink/Video",
        "Publishes sampled video frames to Kafka as VLMRequest messages",
        "Satyaki Solutions Pvt Ltd <engineering@satyaki.co.in>",
    )

    __gsttemplates__ = (
        Gst.PadTemplate.new(
            "sink",
            Gst.PadDirection.SINK,
            Gst.PadPresence.ALWAYS,
            Gst.Caps.from_string("image/jpeg"),
        ),
    )

    __gproperties__ = {
        "camera-id": (
            str, "camera-id", "UUID of the camera this clip belongs to",
            "", GObject.ParamFlags.READWRITE,
        ),
        "track-id": (
            int, "track-id", "Synthetic track id (int)",
            0, 2**31 - 1, 0, GObject.ParamFlags.READWRITE,
        ),
        "session-id": (
            str, "session-id", "UUID of the track session",
            "", GObject.ParamFlags.READWRITE,
        ),
        "zone-name": (
            str, "zone-name", "Zone label shown in VLM prompt",
            "", GObject.ParamFlags.READWRITE,
        ),
        "dwell-time": (
            float, "dwell-time", "Dwell time in seconds",
            0.0, 1e6, 0.0, GObject.ParamFlags.READWRITE,
        ),
        "class-name": (
            str, "class-name", "Detected class name (e.g. person)",
            "person", GObject.ParamFlags.READWRITE,
        ),
        "path-summary": (
            str, "path-summary", "Short textual path summary for the VLM prompt",
            "", GObject.ParamFlags.READWRITE,
        ),
        "sample-count": (
            int, "sample-count", "Number of frames to sample evenly across the stream",
            1, 32, 6, GObject.ParamFlags.READWRITE,
        ),
        "kafka-bootstrap": (
            str, "kafka-bootstrap", "Kafka bootstrap servers",
            "kafka:9092", GObject.ParamFlags.READWRITE,
        ),
        "topic": (
            str, "topic", "Kafka topic to publish VLMRequest to",
            "wastewatch.vlm.requests", GObject.ParamFlags.READWRITE,
        ),
    }

    def __init__(self):
        super().__init__()
        self.set_sync(False)
        self._camera_id = ""
        self._track_id = 0
        self._session_id = ""
        self._zone_name = ""
        self._dwell_time = 0.0
        self._class_name = "person"
        self._path_summary = ""
        self._sample_count = 6
        self._kafka_bootstrap = "kafka:9092"
        self._topic = "wastewatch.vlm.requests"

        self._buffered: list[tuple[float, bytes]] = []
        self._frame_index = 0
        self._published = False

    # ── GObject property plumbing ──────────────────────────────────────
    def do_get_property(self, prop):
        name = prop.name
        return {
            "camera-id": self._camera_id,
            "track-id": self._track_id,
            "session-id": self._session_id,
            "zone-name": self._zone_name,
            "dwell-time": self._dwell_time,
            "class-name": self._class_name,
            "path-summary": self._path_summary,
            "sample-count": self._sample_count,
            "kafka-bootstrap": self._kafka_bootstrap,
            "topic": self._topic,
        }.get(name)

    def do_set_property(self, prop, value):
        name = prop.name
        setters = {
            "camera-id": lambda v: setattr(self, "_camera_id", v),
            "track-id": lambda v: setattr(self, "_track_id", int(v)),
            "session-id": lambda v: setattr(self, "_session_id", v),
            "zone-name": lambda v: setattr(self, "_zone_name", v),
            "dwell-time": lambda v: setattr(self, "_dwell_time", float(v)),
            "class-name": lambda v: setattr(self, "_class_name", v),
            "path-summary": lambda v: setattr(self, "_path_summary", v),
            "sample-count": lambda v: setattr(self, "_sample_count", int(v)),
            "kafka-bootstrap": lambda v: setattr(self, "_kafka_bootstrap", v),
            "topic": lambda v: setattr(self, "_topic", v),
        }
        setter = setters.get(name)
        if setter is None:
            raise AttributeError(f"Unknown property {name!r}")
        setter(value)

    # ── Frame handling ────────────────────────────────────────────────
    def do_render(self, buffer):
        success, info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR
        try:
            data = bytes(info.data)
        finally:
            buffer.unmap(info)

        # Use buffer PTS if present (ns -> s), otherwise synthesise from index.
        pts_ns = buffer.pts
        if pts_ns is None or pts_ns == Gst.CLOCK_TIME_NONE:
            pts_s = float(self._frame_index) / 25.0
        else:
            pts_s = pts_ns / 1e9

        self._buffered.append((pts_s, data))
        self._frame_index += 1

        # Cap memory: if we're already past 4x the sample count, drop oldest.
        if len(self._buffered) > self._sample_count * 8:
            self._buffered = self._buffered[-self._sample_count * 4:]

        return Gst.FlowReturn.OK

    # ── EOS -> publish ────────────────────────────────────────────────
    def do_event(self, event):
        if event.type == Gst.EventType.EOS:
            self._publish_locked()
        return GstBase.BaseSink.do_event(self, event)

    def do_stop(self):
        # Belt-and-braces: if EOS never came (pipeline torn down early),
        # still try to publish whatever we have.
        self._publish_locked()
        return True

    def _publish_locked(self):
        if self._published:
            return
        self._published = True
        if not self._buffered:
            logger.warning("wastewatchvlmpublisher: no frames buffered, nothing to publish")
            return

        n = min(self._sample_count, len(self._buffered))
        stride = max(1, len(self._buffered) // n)
        sampled = self._buffered[::stride][:n]

        frame_timestamps = [round(ts, 3) for ts, _ in sampled]
        frames_b64 = [base64.b64encode(data).decode("ascii") for _, data in sampled]

        payload = {
            "camera_id": self._camera_id,
            "track_id": self._track_id,
            "track_session_id": self._session_id,
            "zone_name": self._zone_name,
            "dwell_time_s": self._dwell_time,
            "class_name": self._class_name,
            "path_summary": self._path_summary,
            "frame_timestamps": frame_timestamps,
            "frames_base64": frames_b64,
        }

        try:
            from confluent_kafka import Producer
        except ImportError as e:
            logger.error("confluent_kafka not importable inside plugin: %s", e)
            return

        producer = Producer({"bootstrap.servers": self._kafka_bootstrap})
        body = json.dumps(payload).encode("utf-8")
        producer.produce(self._topic, value=body)
        producer.flush(timeout=15)

        logger.info(
            "wastewatchvlmpublisher: published track_id=%d camera=%s frames=%d topic=%s (buffered=%d)",
            self._track_id, self._camera_id[:8], len(frames_b64), self._topic, len(self._buffered),
        )

        # Clear state so the element can be reused if the harness runs
        # multiple clips through a single instance.
        self._buffered = []
        self._frame_index = 0
        self._published = False  # allow reuse


GObject.type_register(GstWasteWatchVlmPublisher)

_registered = False


def register_element() -> None:
    """Register the element with the global GStreamer registry."""
    global _registered
    if _registered:
        return
    Gst.Element.register(None, ELEMENT_NAME, Gst.Rank.NONE, GstWasteWatchVlmPublisher)
    _registered = True
    logger.info("Registered Gst element: %s", ELEMENT_NAME)


# Auto-register on import so tools that just import this module (e.g. the
# harness) get the element without needing an explicit call.
register_element()

# gst-python plugin loader entrypoint — if this file is discovered via
# GST_PLUGIN_PATH/python/, the loader will look for __gstelementfactory__.
__gstelementfactory__ = (ELEMENT_NAME, Gst.Rank.NONE, GstWasteWatchVlmPublisher)
