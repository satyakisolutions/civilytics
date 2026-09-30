import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

from facelab.mjpeg import BOUNDARY, MjpegServer


@pytest.fixture
def server():
    s = MjpegServer("127.0.0.1", 0)  # ephemeral port
    s.start()
    yield s
    s.stop()


def test_snapshot_503_before_first_frame(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/snapshot.jpg", timeout=5)
    assert exc.value.code == 503


def test_snapshot_returns_latest_jpeg_downscaled(server):
    server.publish(np.zeros((1080, 1920, 3), np.uint8))
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/snapshot.jpg", timeout=5) as resp:
        assert resp.headers["Content-Type"] == "image/jpeg"
        data = resp.read()
    assert data[:2] == b"\xff\xd8"
    import cv2
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] == 1280


def test_stream_delivers_multipart_frames(server):
    def feed():
        for _ in range(20):
            server.publish(np.full((240, 320, 3), 128, np.uint8))
            time.sleep(0.05)

    threading.Thread(target=feed, daemon=True).start()
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/stream.mjpg", timeout=5) as resp:
        assert resp.headers["Content-Type"] == f"multipart/x-mixed-replace; boundary={BOUNDARY}"
        chunk = resp.read(4000)
    assert chunk.startswith(f"--{BOUNDARY}".encode())
    assert b"Content-Type: image/jpeg" in chunk
    assert b"\xff\xd8" in chunk


def test_unknown_path_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/nope", timeout=5)
    assert exc.value.code == 404
