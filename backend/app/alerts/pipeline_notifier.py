# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Pipeline notifier — sends control commands to the DeepStream pipeline.

When a violation is resolved (acknowledged, VLM-cleared, or timed out),
this module notifies the pipeline to stop recording, reset FSM state,
and finalize evidence metadata.

Uses Redis pub/sub for low-latency command delivery to the pipeline process.

Resolution triggers:
    1. Operator acknowledges alert via REST API or WebSocket
    2. VLM analysis returns is_dumping=False
    3. Alert auto-stop (track left ROI, stagnant dwell)
    4. Admin force-clears from monitoring dashboard
    5. Escalation timeout (no response after all levels exhausted)

On resolution, the pipeline must:
    - Stop NvDsSR recording → finalize MP4 with moov atom
    - Save evidence metadata JSON (detections, VLM result, timestamps)
    - Reset FSM to EXITED → nvosd shows black bbox fading out
    - Clear PriorityAlertRouter state
    - Optionally play "all clear" on IP speaker
"""

import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

COMMAND_CHANNEL = "ww:pipeline:commands"


class PipelineNotifier:
    """Publishes control commands to the pipeline via Redis pub/sub.

    The pipeline's CommandReceiver subscribes to the same channel and
    drains commands in its GLib main loop callback.
    """

    def __init__(self, redis_url: str = "") -> None:
        self._redis = None
        self._redis_url = redis_url
        if redis_url:
            try:
                import redis as redis_lib
                self._redis = redis_lib.Redis.from_url(redis_url, decode_responses=True)
                logger.info("PipelineNotifier connected to Redis")
            except Exception:
                logger.warning("PipelineNotifier: Redis not available — commands will be logged only")

    def _publish(self, command: dict) -> bool:
        """Publish a command to the pipeline channel. Returns True on success.

        This is called from async FastAPI route handlers (cameras.py) and
        the async alert dispatcher loop, but redis-py's sync client makes a
        blocking network round-trip. Offload it to the default executor
        thread pool so it never blocks the event loop — every camera
        create/update/delete and every dispatched alert used to stall the
        whole event loop for one Redis publish.
        """
        command.setdefault("timestamp", time.time())
        if self._redis:
            try:
                loop = asyncio.get_running_loop()
                future = loop.run_in_executor(
                    None, self._redis.publish, COMMAND_CHANNEL, json.dumps(command)
                )
                # The future is fire-and-forget (this method returns True
                # before the publish actually completes), but a failure
                # inside the executor thread was previously discarded
                # silently -- the caller kept seeing "success" even when
                # the publish never reached Redis. Log it when it happens.
                def _log_publish_failure(fut, _action=command.get("action")):
                    exc = fut.exception()
                    if exc is not None:
                        logger.error("Pipeline command failed to publish (action=%s): %s", _action, exc)
                future.add_done_callback(_log_publish_failure)
                logger.info("Pipeline command sent: %s", command.get("action"))
                return True
            except RuntimeError:
                # No running event loop (e.g. a sync test) — publish inline.
                try:
                    self._redis.publish(COMMAND_CHANNEL, json.dumps(command))
                    return True
                except Exception:
                    logger.exception("Failed to publish pipeline command")
                    return False
            except Exception:
                logger.exception("Failed to publish pipeline command")
                return False
        else:
            logger.warning("Pipeline command (no Redis): %s", command)
            return False

    # ------------------------------------------------------------------
    # Resolution commands
    # ------------------------------------------------------------------

    def clear_violation(
        self,
        camera_id: str,
        track_id: int | None,
        reason: str,
    ) -> bool:
        """Notify pipeline to stop recording and reset FSM for a track.

        Called when a violation is resolved by any means:
        - operator_acknowledged
        - vlm_not_dumping
        - auto_stop_left_roi
        - auto_stop_stagnant
        - admin_force_clear
        - escalation_timeout
        """
        return self._publish({
            "action": "clear_violation",
            "camera_id": camera_id,
            "track_id": track_id,
            "reason": reason,
        })

    def clear_by_vlm(
        self,
        camera_id: str,
        track_id: int | None,
        vlm_confidence: float,
        waste_type: str,
    ) -> bool:
        """Notify pipeline that VLM says NOT dumping — clear the violation.

        The pipeline will:
        1. Stop recording (finalize MP4)
        2. Save metadata with VLM result
        3. Reset FSM → EXITED
        4. Bbox turns black → fade out
        """
        return self._publish({
            "action": "clear_violation",
            "camera_id": camera_id,
            "track_id": track_id,
            "reason": "vlm_not_dumping",
            "data": {
                "vlm_confidence": vlm_confidence,
                "waste_type": waste_type,
            },
        })

    # ------------------------------------------------------------------
    # Camera control commands
    # ------------------------------------------------------------------

    def pause_camera(self, camera_id: str, reason: str = "maintenance") -> bool:
        """Temporarily disable inference for a camera."""
        return self._publish({
            "action": "pause_camera",
            "camera_id": camera_id,
            "reason": reason,
        })

    def resume_camera(self, camera_id: str) -> bool:
        """Re-enable inference for a paused camera."""
        return self._publish({
            "action": "resume_camera",
            "camera_id": camera_id,
        })

    def update_roi(self, camera_id: str, polygons: list[dict]) -> bool:
        """Push updated ROI polygons to the pipeline."""
        return self._publish({
            "action": "update_roi",
            "camera_id": camera_id,
            "data": {"polygons": polygons},
        })

    def force_stop_all(self, reason: str = "admin") -> bool:
        """Emergency: stop all recordings and clear all alerts."""
        return self._publish({
            "action": "force_stop_all",
            "reason": reason,
        })

    async def close(self) -> None:
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass
