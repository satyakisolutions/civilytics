"""Frame sources: RTSP camera (threaded, latest frame, auto-reconnect), video file, webcam."""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class FrameSource:
    """read() -> (frame, timestamp_s) or (None, now) when no new frame yet.

    Live sources (RTSP / webcam) are read on a background thread that keeps only
    the newest frame, so slow face processing never builds up camera lag.
    Video files are read sequentially and timestamped by their own clock.
    """

    def __init__(self, source: str, rtsp_tcp: bool = True, reconnect_s: float = 2.0):
        self.source = source
        self.is_file = Path(source).is_file()
        self.is_webcam = source.isdigit()
        self.reconnect_s = reconnect_s
        self.ended = False
        if source.lower().startswith("rtsp") and rtsp_tcp:
            # TCP avoids the smeared/grey frames UDP gives on lossy networks.
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        self._cap: cv2.VideoCapture | None = None
        self._lock = threading.Lock()
        self._latest: tuple[np.ndarray, float] | None = None
        self._fresh = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _open(self) -> cv2.VideoCapture:
        target = int(self.source) if self.is_webcam else self.source
        cap = cv2.VideoCapture(target) if self.is_webcam else cv2.VideoCapture(target, cv2.CAP_FFMPEG)
        return cap

    def start(self) -> None:
        if self.is_file:
            self._cap = self._open()
            if not self._cap.isOpened():
                raise RuntimeError(f"Cannot open video file {self.source}")
            return
        self._thread = threading.Thread(target=self._reader, name="frame-reader", daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                logger.warning("Cannot open %s -- retrying in %.0fs", self._safe_name(), self.reconnect_s)
                self._stop.wait(self.reconnect_s)
                continue
            logger.info("Connected to %s", self._safe_name())
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    logger.warning("Stream read failed -- reconnecting")
                    break
                with self._lock:
                    self._latest = (frame, time.monotonic())
                self._fresh.set()
            cap.release()
            self._stop.wait(self.reconnect_s)

    def read(self, timeout: float = 1.0) -> tuple[np.ndarray | None, float]:
        if self.is_file:
            ok, frame = self._cap.read()
            if not ok:
                self.ended = True
                return None, 0.0
            return frame, self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if not self._fresh.wait(timeout):
            return None, time.monotonic()
        self._fresh.clear()
        with self._lock:
            frame, ts = self._latest
        return frame, ts

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._cap is not None:
            self._cap.release()

    def _safe_name(self) -> str:
        """Source name without credentials (rtsp://user:pass@host -> rtsp://***@host)."""
        if "@" in self.source and "://" in self.source:
            scheme, rest = self.source.split("://", 1)
            return f"{scheme}://***@{rest.split('@', 1)[1]}"
        return self.source
