# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Redis command receiver — feedback channel from backend to pipeline.

The pipeline is a producer-only GStreamer process. When the backend clears
a violation (operator acknowledges, VLM says not dumping, admin force-clears),
it publishes a command via Redis pub/sub. This module receives those commands
and acts on the pipeline state.

Commands:
    clear_violation  — stop recording, reset FSM to EXITED, clear alert
    pause_camera     — temporarily disable inference for a camera
    resume_camera    — re-enable inference for a camera
    update_roi       — reload ROI polygons for a camera
    force_stop_all   — emergency stop all recordings and alerts
    add_camera       — register a new RTSP source in the running pipeline
    update_camera    — reconfigure an existing camera (remove + re-add)
    remove_camera    — unregister a camera and clean up pipeline state

Data flow:
    Backend → Redis PUBLISH ww:pipeline:commands → CommandReceiver → pipeline state
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

COMMAND_CHANNEL = "ww:pipeline:commands"


class CommandType(str, Enum):
    CLEAR_VIOLATION = "clear_violation"
    PAUSE_CAMERA = "pause_camera"
    RESUME_CAMERA = "resume_camera"
    UPDATE_ROI = "update_roi"
    FORCE_STOP_ALL = "force_stop_all"
    ADD_CAMERA = "add_camera"
    UPDATE_CAMERA = "update_camera"
    REMOVE_CAMERA = "remove_camera"


@dataclass
class PipelineCommand:
    action: CommandType
    camera_id: str = ""
    track_id: int | None = None
    reason: str = ""
    data: dict | None = None
    timestamp: float = 0.0


class CommandReceiver:
    """Receives control commands from the backend via Redis pub/sub.

    Runs a subscriber thread that listens on `ww:pipeline:commands`.
    Commands are queued and drained from the GLib main loop callback
    to avoid thread-safety issues with GStreamer elements.

    Usage in pipeline.py::

        receiver = CommandReceiver(redis_url)
        receiver.start()

        # In GLib periodic callback:
        def health_check_cb():
            for cmd in receiver.drain():
                handle_command(cmd)
            return True
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis_url = redis_url
        self._queue: list[PipelineCommand] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._pubsub = None

    def start(self) -> None:
        """Start the Redis subscriber thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._subscribe_loop,
            name="ww-command-receiver",
            daemon=True,
        )
        self._thread.start()
        logger.info("CommandReceiver started — listening on %s", COMMAND_CHANNEL)

    def stop(self) -> None:
        """Stop the subscriber thread."""
        self._running = False
        if self._pubsub:
            try:
                self._pubsub.unsubscribe()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("CommandReceiver stopped")

    def drain(self) -> list[PipelineCommand]:
        """Drain all queued commands. Call from the GLib main loop thread.

        Returns a list of commands received since the last drain.
        Thread-safe — the subscriber thread appends, the GLib thread drains.
        """
        with self._lock:
            commands = list(self._queue)
            self._queue.clear()
        return commands

    def _subscribe_loop(self) -> None:
        """Redis subscriber loop — runs in a background thread."""
        try:
            import redis
        except ImportError:
            logger.error("redis package not available — CommandReceiver disabled")
            return

        while self._running:
            try:
                client = redis.Redis.from_url(self._redis_url, decode_responses=True)
                self._pubsub = client.pubsub()
                self._pubsub.subscribe(COMMAND_CHANNEL)
                logger.info("Subscribed to Redis channel: %s", COMMAND_CHANNEL)

                for message in self._pubsub.listen():
                    if not self._running:
                        break
                    if message["type"] != "message":
                        continue

                    try:
                        data = json.loads(message["data"])
                        cmd = PipelineCommand(
                            action=CommandType(data.get("action", "")),
                            camera_id=data.get("camera_id", ""),
                            track_id=data.get("track_id"),
                            reason=data.get("reason", ""),
                            data=data.get("data"),
                            timestamp=data.get("timestamp", time.time()),
                        )
                        with self._lock:
                            self._queue.append(cmd)
                        logger.info(
                            "Command received: %s camera=%s track=%s reason=%s",
                            cmd.action, cmd.camera_id, cmd.track_id, cmd.reason,
                        )
                    except (json.JSONDecodeError, ValueError) as e:
                        logger.warning("Invalid command message: %s — %s", message["data"], e)

            except Exception:
                logger.exception("CommandReceiver connection error — reconnecting in 5s")
                time.sleep(5)

        if self._pubsub:
            try:
                self._pubsub.close()
            except Exception:
                pass


class CommandHandler:
    """Handles pipeline commands by acting on FSM, recording, and alert state.

    Wired into the GLib main loop — drains commands from CommandReceiver
    and applies them to the pipeline components.
    """

    def __init__(
        self,
        fsm_manager=None,
        smart_record_mgr=None,
        alert_router=None,
        kafka_producer=None,
        connection_monitor=None,
        frame_batcher=None,
    ) -> None:
        self._fsm = fsm_manager
        self._sr = smart_record_mgr
        self._router = alert_router
        self._kafka = kafka_producer
        self._connection_monitor = connection_monitor
        self._frame_batcher = frame_batcher

    def handle(self, cmd: PipelineCommand) -> None:
        """Dispatch a command to the appropriate handler."""
        handlers = {
            CommandType.CLEAR_VIOLATION: self._handle_clear,
            CommandType.PAUSE_CAMERA: self._handle_pause,
            CommandType.RESUME_CAMERA: self._handle_resume,
            CommandType.UPDATE_ROI: self._handle_update_roi,
            CommandType.FORCE_STOP_ALL: self._handle_force_stop,
            CommandType.ADD_CAMERA: self._handle_add_camera,
            CommandType.UPDATE_CAMERA: self._handle_update_camera,
            CommandType.REMOVE_CAMERA: self._handle_remove_camera,
        }
        handler = handlers.get(cmd.action)
        if handler:
            handler(cmd)
        else:
            logger.warning("Unknown command: %s", cmd.action)

    def _handle_clear(self, cmd: PipelineCommand) -> None:
        """Clear a violation — stop recording, reset FSM, resolve alert.

        Triggered by:
        - Operator acknowledges via dashboard
        - VLM analysis says NOT dumping
        - Admin force-clears from monitoring view
        """
        camera_id = cmd.camera_id
        track_id = cmd.track_id

        logger.info(
            "CLEAR VIOLATION: %s track=%s reason=%s",
            camera_id, track_id, cmd.reason,
        )

        # 1. Stop smart recording
        if self._sr and track_id is not None:
            try:
                self._sr.stop_recording(camera_id, track_id)
                logger.info("Recording stopped: %s/track-%s", camera_id, track_id)
            except Exception:
                logger.exception("Failed to stop recording for %s/track-%s", camera_id, track_id)

        # 2. Reset FSM to EXITED (so nvosd shows black → fade)
        if self._fsm and track_id is not None:
            # For the Python FSM fallback
            if hasattr(self._fsm, '_cleanup'):
                self._fsm._cleanup((camera_id, track_id))
            # For ActionAwareFSM
            elif hasattr(self._fsm, '_fsm') and hasattr(self._fsm._fsm, '_cleanup'):
                self._fsm._fsm._cleanup((camera_id, track_id))

        # 3. Clear alert router state
        if self._router and track_id is not None:
            self._router.on_acknowledge(camera_id, track_id, operator_id=cmd.reason)

        # 4. Publish cleared event to Kafka (for backend logging + frontend update)
        if self._kafka:
            self._kafka.send_event_dict({
                "camera_id": camera_id,
                "track_id": track_id,
                "state": "exited",
                "prev_state": "violation",
                "reason": f"cleared:{cmd.reason}",
                "timestamp": cmd.timestamp or time.time(),
            })

    def _handle_pause(self, cmd: PipelineCommand) -> None:
        """Pause inference for a camera (maintenance, false positive suppression)."""
        logger.info("PAUSE camera: %s reason=%s", cmd.camera_id, cmd.reason)
        # Would disable the source in SourceManager or skip in pad probe
        # Implementation depends on whether we want to stop the stream entirely
        # or just suppress detections

    def _handle_resume(self, cmd: PipelineCommand) -> None:
        """Resume inference for a paused camera."""
        logger.info("RESUME camera: %s", cmd.camera_id)

    def _handle_update_roi(self, cmd: PipelineCommand) -> None:
        """Reload ROI polygons for a camera (edited in frontend)."""
        logger.info("UPDATE ROI: %s", cmd.camera_id)
        # ROI data would be in cmd.data
        # Update the ROIManager and regenerate nvdsanalytics config

    def _handle_force_stop(self, cmd: PipelineCommand) -> None:
        """Emergency: stop all recordings and clear all alerts."""
        logger.warning("FORCE STOP ALL — clearing all violations and recordings")
        if self._sr:
            try:
                self._sr.stop_all()
            except Exception:
                logger.exception("Error in force stop all recordings")
        if self._router:
            self._router.cleanup_stale()

    # ------------------------------------------------------------------
    # Camera CRUD commands (from REST API via Redis)
    # ------------------------------------------------------------------

    def _handle_add_camera(self, cmd: PipelineCommand) -> None:
        """Add a new RTSP source to the running pipeline."""
        uri = (cmd.data or {}).get("uri", "")
        logger.info("ADD CAMERA: %s uri=%s", cmd.camera_id, uri)
        if self._connection_monitor:
            try:
                self._connection_monitor.register_camera(cmd.camera_id, uri)
                logger.info("Camera registered in connection monitor: %s", cmd.camera_id)
            except Exception:
                logger.exception("Failed to register camera %s", cmd.camera_id)
        else:
            logger.warning("No connection_monitor — cannot add camera %s", cmd.camera_id)

    def _handle_update_camera(self, cmd: PipelineCommand) -> None:
        """Update an RTSP source — remove old, add new."""
        uri = (cmd.data or {}).get("uri", "")
        logger.info("UPDATE CAMERA: %s uri=%s", cmd.camera_id, uri)
        if self._connection_monitor:
            try:
                self._connection_monitor.unregister_camera(cmd.camera_id)
                self._connection_monitor.register_camera(cmd.camera_id, uri)
                logger.info("Camera updated in connection monitor: %s", cmd.camera_id)
            except Exception:
                logger.exception("Failed to update camera %s", cmd.camera_id)
        else:
            logger.warning("No connection_monitor — cannot update camera %s", cmd.camera_id)

    def _handle_remove_camera(self, cmd: PipelineCommand) -> None:
        """Remove an RTSP source from the running pipeline."""
        logger.info("REMOVE CAMERA: %s", cmd.camera_id)
        if self._connection_monitor:
            try:
                self._connection_monitor.unregister_camera(cmd.camera_id)
                logger.info("Camera unregistered from connection monitor: %s", cmd.camera_id)
            except Exception:
                logger.exception("Failed to unregister camera %s", cmd.camera_id)
        if self._frame_batcher:
            try:
                self._frame_batcher.on_camera_removed(cmd.camera_id)
                logger.info("Camera removed from frame batcher: %s", cmd.camera_id)
            except Exception:
                logger.exception("Failed to remove camera %s from frame batcher", cmd.camera_id)
