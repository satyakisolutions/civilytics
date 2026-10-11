"""Tiny MJPEG-over-HTTP server so the annotated view can be watched in VLC or a browser.

  http://HOST:PORT/stream.mjpg   live multipart JPEG stream (VLC: Media -> Open Network Stream)
  http://HOST:PORT/snapshot.jpg  the latest frame
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

logger = logging.getLogger(__name__)
BOUNDARY = "facelabframe"


class _ExclusiveHTTPServer(ThreadingHTTPServer):
    # http.server enables SO_REUSEADDR, which on Windows lets a second process bind the
    # SAME port silently (clients then hit either one). Fail loudly instead.
    allow_reuse_address = False


class MjpegServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8090, quality: int = 80, max_width: int = 1280):
        self.quality = quality
        self.max_width = max_width
        self._jpeg: bytes | None = None
        self._seq = 0
        self._cond = threading.Condition()
        self._closed = False
        try:
            self._httpd = _ExclusiveHTTPServer((host, port), self._handler_class())
        except OSError as exc:
            raise RuntimeError(
                f"Port {port} is already in use (another face-lab run?). Stop it or use a different --serve-port."
            ) from exc
        self._httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="mjpeg-server", daemon=True)
        self._thread.start()

    def publish(self, frame_bgr: np.ndarray) -> None:
        if frame_bgr.shape[1] > self.max_width:
            scale = self.max_width / frame_bgr.shape[1]
            frame_bgr = cv2.resize(frame_bgr, (self.max_width, int(frame_bgr.shape[0] * scale)))
        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            return
        with self._cond:
            self._jpeg = buf.tobytes()
            self._seq += 1
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._httpd.shutdown()
        self._httpd.server_close()

    def _next_frame(self, last_seq: int, timeout: float = 5.0) -> tuple[bytes | None, int]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq != last_seq or self._closed, timeout=timeout)
            return (None if self._closed else self._jpeg), self._seq

    def _handler_class(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # keep the console for face results
                logger.debug("%s - " + fmt, self.address_string(), *args)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path in ("/", "/stream", "/stream.mjpg"):
                    self._stream()
                elif path == "/snapshot.jpg":
                    self._snapshot()
                else:
                    self.send_error(404)

            def _snapshot(self):
                with server._cond:
                    jpeg = server._jpeg
                if jpeg is None:
                    self.send_error(503, "no frame yet")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(jpeg)

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                seq = -1
                try:
                    while True:
                        jpeg, seq = server._next_frame(seq)
                        if server._closed:
                            return
                        if jpeg is None:
                            continue
                        self.wfile.write(
                            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                        )
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

        return Handler
