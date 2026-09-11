# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""GPU scheduler -- distributes RTSP streams across available GPUs.

For N streams and G GPUs, assigns ceil(N/G) streams per GPU.
Each MediaExtractor instance is pinned to a specific GPU via
CUDA_VISIBLE_DEVICES.
"""
from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def _detect_gpu_count() -> int:
    """Auto-detect the number of NVIDIA GPUs via nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            count = len(result.stdout.strip().splitlines())
            logger.info("Auto-detected %d GPU(s)", count)
            return max(count, 1)
    except Exception:
        logger.warning("nvidia-smi failed; defaulting to 1 GPU")
    return 1


class GPUScheduler:
    """Round-robin GPU assignment for RTSP camera streams.

    Args:
        num_gpus: Number of GPUs to use.  If 0, auto-detect via
                  ``nvidia-smi``.
    """

    def __init__(self, num_gpus: int = 0) -> None:
        self._num_gpus = num_gpus if num_gpus > 0 else _detect_gpu_count()
        self._assignments: dict[str, int] = {}
        self._next_gpu = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def num_gpus(self) -> int:
        return self._num_gpus

    def assign(self, camera_id: str) -> int:
        """Return the GPU index for *camera_id* (round-robin)."""
        if camera_id in self._assignments:
            return self._assignments[camera_id]

        gpu = self._next_gpu % self._num_gpus
        self._assignments[camera_id] = gpu
        self._next_gpu += 1
        logger.info("Assigned %s -> GPU %d", camera_id, gpu)
        return gpu

    def get_assignment_map(self, camera_ids: list[str]) -> dict[int, list[str]]:
        """Return ``{gpu_id: [camera_ids]}`` for all supplied cameras."""
        gpu_map: dict[int, list[str]] = {g: [] for g in range(self._num_gpus)}
        for cam_id in camera_ids:
            gpu = self.assign(cam_id)
            gpu_map[gpu].append(cam_id)
        return gpu_map
