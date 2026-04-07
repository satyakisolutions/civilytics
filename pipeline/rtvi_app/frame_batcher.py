# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Frame batcher -- collects sampled frames into per-stream batches for VLM.

Each RTSP stream has its own batcher. Frames are NEVER mixed across streams.
When a batcher accumulates enough frames (batch_duration / sample_interval),
it yields the batch for VLM processing.

Example: interval=10s, duration=60s -> batch of 6 frames per stream
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyservicemaker.utils import VideoFrame

logger = logging.getLogger(__name__)


class FrameBatcher:
    """Per-stream frame batcher.

    Accumulates VideoFrame objects from a single camera and yields a
    complete batch once ``batch_size`` frames have been collected.
    """

    def __init__(self, camera_id: str, batch_size: int = 6) -> None:
        self._camera_id = camera_id
        self._batch_size = batch_size
        self._buffer: list[VideoFrame] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_frame(self, frame: VideoFrame) -> list[VideoFrame] | None:
        """Add a VideoFrame to the buffer.

        Returns the complete batch (list of frames) when ``batch_size``
        is reached, otherwise returns ``None``.  The internal buffer is
        cleared after yielding a batch.
        """
        self._buffer.append(frame)
        logger.debug(
            "%s: buffered frame %d/%d (ts=%d ns)",
            self._camera_id,
            len(self._buffer),
            self._batch_size,
            frame.timestamp,
        )

        if len(self._buffer) >= self._batch_size:
            batch = self._buffer
            self._buffer = []
            logger.info(
                "%s: batch ready (%d frames, span %.1fs)",
                self._camera_id,
                len(batch),
                (batch[-1].timestamp - batch[0].timestamp) / 1e9,
            )
            return batch

        return None

    def flush(self) -> list[VideoFrame] | None:
        """Return whatever frames are buffered (partial batch).

        Returns ``None`` if the buffer is empty.
        """
        if not self._buffer:
            return None

        batch = self._buffer
        self._buffer = []
        logger.info(
            "%s: flushing partial batch (%d frames)",
            self._camera_id,
            len(batch),
        )
        return batch

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)
