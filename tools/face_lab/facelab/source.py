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
    """Live sources (RTSP / webcam) are read on a background thread that keeps only
    the newest frame. Several consumers (display loop, face-processing thread) each
    call wait_new(last_seq) and always get the newest frame without stealing it from
    one another, so slow face processing never builds up camera lag.

    Video files are read sequentially with read() and timestamped by their own clock.
    """

    def __init__(self, source: str, rtsp_tcp: bool = True, reconnect_s: float = 2.0, hw_decode: bool = True):
        self.source = source
        self.hw_decode = hw_decode
        self._hw_logged = False
        self.is_file = Path(source).is_file()
        self.is_webcam = source.isdigit()
        self.reconnect_s = reconnect_s
        self.ended = False
        if source.lower().startswith("rtsp") and rtsp_tcp:
            # TCP avoids the smeared/grey frames UDP gives on lossy networks.
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        self._cap: cv2.VideoCapture | None = None
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._ts = 0.0
        self._seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def live(self) -> bool:
        return not self.is_file

    def _open(self) -> cv2.VideoCapture:
        if self.is_webcam:
            return cv2.VideoCapture(int(self.source))
        if self.hw_decode:
            # GPU video decoding (D3D11 on Windows): 1080p at 30 fps without loading the CPU.
            cap = cv2.VideoCapture(
                self.source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY]
            )
            if cap.isOpened():
                if not self._hw_logged:
                    active = cap.get(cv2.CAP_PROP_HW_ACCELERATION) > 0
                    logger.info("Video decoding on %s", "GPU" if active else "CPU (no hardware decoder)")
                    self._hw_logged = True
                return cap
            cap.release()
        return cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)

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
                logger.warning("Cannot open %s -- retrying in %.0fs", self.safe_name(), self.reconnect_s)
                self._stop.wait(self.reconnect_s)
                continue
            logger.info("Connected to %s", self.safe_name())
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    logger.warning("Stream read failed -- reconnecting")
                    break
                with self._cond:
                    self._frame, self._ts = frame, time.monotonic()
                    self._seq += 1
                    self._cond.notify_all()
            cap.release()
            self._stop.wait(self.reconnect_s)

    def wait_new(self, last_seq: int, timeout: float = 1.0) -> tuple[np.ndarray | None, float, int]:
        """Live sources: newest frame newer than last_seq -> (frame, timestamp, seq); frame None on timeout."""
        with self._cond:
            fresh = lambda: self._frame is not None and self._seq != last_seq  # noqa: E731
            if not self._cond.wait_for(lambda: fresh() or self._stop.is_set(), timeout) or not fresh():
                return None, time.monotonic(), last_seq
            return self._frame, self._ts, self._seq

    def read(self) -> tuple[np.ndarray | None, float]:
        """Video files: next frame in order -> (frame, video time s); (None, 0) at the end."""
        ok, frame = self._cap.read()
        if not ok:
            self.ended = True
            return None, 0.0
        return frame, self._cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._cap is not None:
            self._cap.release()

    def safe_name(self) -> str:
        """Source name without credentials (rtsp://user:pass@host -> rtsp://***@host)."""
        if "@" in self.source and "://" in self.source:
            scheme, rest = self.source.split("://", 1)
            return f"{scheme}://***@{rest.split('@', 1)[1]}"
        return self.source
