import logging
import time


class CameraHealth:
    def __init__(self, timeout_s: float = 30.0, max_retries: int = 5) -> None:
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._last_frame: dict[str, float] = {}
        self._retry_count: dict[str, int] = {}

    def on_frame(self, camera_id: str) -> None:
        """Record a successful frame receipt for the given camera."""
        self._last_frame[camera_id] = time.monotonic()
        self._retry_count[camera_id] = 0

    def check_health(self) -> list[str]:
        """Return a list of camera IDs that have timed out and should be retried.

        Cameras that have exceeded max_retries are logged as errors and excluded
        from the returned list (no further retry attempts will be made).
        """
        now = time.monotonic()
        timed_out: list[str] = []
        for camera_id, last in self._last_frame.items():
            if now - last > self._timeout_s:
                retries = self._retry_count.get(camera_id, 0)
                if retries < self._max_retries:
                    timed_out.append(camera_id)
                    self._retry_count[camera_id] = retries + 1
                else:
                    logging.getLogger(__name__).error(
                        "Camera %s exceeded max retries", camera_id
                    )
        return timed_out
