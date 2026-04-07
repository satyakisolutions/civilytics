# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Per-camera RTSP output stream manager.

After DeepStream processes all cameras through the shared nvinfer/nvtracker
pipeline, this module demuxes the annotated frames back to individual
per-camera streams and publishes each to MediaMTX as separate RTSP paths.

Input:  Batched annotated frames from nvosd (via tee branch)
Output: Per-camera RTSP streams on MediaMTX:
        rtsp://mediamtx:8554/{camera_id}-out

The frontend WebRTC player connects to MediaMTX to view each camera's
annotated inference output independently.

Architecture:
    tee → queue → nvstreamdemux → [per camera: nvvidconv → caps → h264enc → rtppay → udpsink]
                                   cam-01 → rtsp://mediamtx:8554/cam-01-out
                                   cam-02 → rtsp://mediamtx:8554/cam-02-out
                                   ...

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""

import logging
import time
from dataclasses import dataclass, field

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class OutputBranch:
    """Tracks a single per-camera output encoding chain."""

    camera_id: str
    source_id: int
    rtp_port: int
    elements: list = field(default_factory=list)  # all GStreamer elements in the branch
    rtsp_url: str = ""
    webrtc_url: str = ""
    created_at: float = 0.0


class OutputStreamManager:
    """Manages per-camera RTSP output streams via nvstreamdemux.

    After the shared inference pipeline processes all cameras in a single
    batch, this manager splits the annotated output back into individual
    per-camera streams using nvstreamdemux.  Each camera gets its own
    H.264 encoding chain that publishes RTP to a unique UDP port on
    MediaMTX, which in turn serves the stream as RTSP and WebRTC.
    """

    def __init__(
        self,
        pipeline: Gst.Pipeline,
        mediamtx_host: str = "mediamtx",
        mediamtx_rtp_base_port: int = 20000,
    ) -> None:
        self._pipeline = pipeline
        self._mediamtx_host = mediamtx_host
        self._rtp_base_port = mediamtx_rtp_base_port
        self._demux: Gst.Element | None = None
        self._branches: dict[str, OutputBranch] = {}

    # ------------------------------------------------------------------
    # Demux setup
    # ------------------------------------------------------------------

    def setup_demux(self) -> Gst.Element:
        """Create the nvstreamdemux element and add it to the pipeline.

        Returns the element so the caller can link:
            tee → queue → nvstreamdemux
        """
        self._demux = Gst.ElementFactory.make("nvstreamdemux", "stream_demux")
        if self._demux is None:
            raise RuntimeError(
                "Failed to create nvstreamdemux — is DeepStream installed?"
            )
        self._pipeline.add(self._demux)
        logger.info("Created nvstreamdemux element 'stream_demux'")
        return self._demux

    # ------------------------------------------------------------------
    # Branch management
    # ------------------------------------------------------------------

    def add_output_branch(self, camera_id: str, source_id: int) -> bool:
        """Create a per-camera output encoding chain.

        Requests a src pad from nvstreamdemux for the given source_id,
        builds the encoding pipeline (nvvideoconvert → capsfilter →
        nvv4l2h264enc → rtph264pay → udpsink), links everything, and
        syncs the element states with the running pipeline.

        Args:
            camera_id: Logical camera identifier (e.g. "cam-01").
            source_id: The DeepStream source/pad index assigned by
                       SourceManager when the camera was added.

        Returns:
            True on success, False on failure.
        """
        if self._demux is None:
            logger.error(
                "add_output_branch: nvstreamdemux not initialised — "
                "call setup_demux() first"
            )
            return False

        if camera_id in self._branches:
            logger.warning(
                "Output branch for %s already exists — skipping", camera_id
            )
            return False

        rtp_port = self._rtp_base_port + source_id
        rtsp_url = self.get_output_url(camera_id)
        webrtc_url = self.get_webrtc_url(camera_id)

        try:
            # --- Create elements ------------------------------------------
            queue = Gst.ElementFactory.make("queue", f"queue_out_{camera_id}")
            nvvidconv = Gst.ElementFactory.make(
                "nvvideoconvert", f"nvvidconv_out_{camera_id}"
            )
            capsfilter = Gst.ElementFactory.make(
                "capsfilter", f"caps_out_{camera_id}"
            )
            caps = Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420")
            capsfilter.set_property("caps", caps)

            h264enc = Gst.ElementFactory.make(
                "nvv4l2h264enc", f"h264enc_{camera_id}"
            )
            h264enc.set_property("bitrate", 4_000_000)

            rtppay = Gst.ElementFactory.make("rtph264pay", f"rtppay_{camera_id}")
            rtppay.set_property("config-interval", 1)
            rtppay.set_property("pt", 96)

            udpsink = Gst.ElementFactory.make("udpsink", f"udpsink_{camera_id}")
            udpsink.set_property("host", self._mediamtx_host)
            udpsink.set_property("port", rtp_port)
            udpsink.set_property("sync", False)
            udpsink.set_property("async", False)

            elements = [queue, nvvidconv, capsfilter, h264enc, rtppay, udpsink]

            # Verify all elements were created
            for elem in elements:
                if elem is None:
                    raise RuntimeError(
                        f"Failed to create GStreamer element in output branch "
                        f"for {camera_id}"
                    )

            # --- Add to pipeline ------------------------------------------
            for elem in elements:
                self._pipeline.add(elem)

            # --- Link: queue → nvvidconv → caps → h264enc → rtppay → udpsink
            if not queue.link(nvvidconv):
                raise RuntimeError(
                    f"Failed to link queue → nvvidconv for {camera_id}"
                )
            if not nvvidconv.link(capsfilter):
                raise RuntimeError(
                    f"Failed to link nvvidconv → capsfilter for {camera_id}"
                )
            if not capsfilter.link(h264enc):
                raise RuntimeError(
                    f"Failed to link capsfilter → h264enc for {camera_id}"
                )
            if not h264enc.link(rtppay):
                raise RuntimeError(
                    f"Failed to link h264enc → rtppay for {camera_id}"
                )
            if not rtppay.link(udpsink):
                raise RuntimeError(
                    f"Failed to link rtppay → udpsink for {camera_id}"
                )

            # --- Link demux src pad → queue sink pad ----------------------
            demux_src_pad = self._demux.get_request_pad(f"src_{source_id}")
            if demux_src_pad is None:
                raise RuntimeError(
                    f"Failed to get src_{source_id} pad from nvstreamdemux"
                )

            queue_sink_pad = queue.get_static_pad("sink")
            if queue_sink_pad is None:
                raise RuntimeError(
                    f"Failed to get sink pad from queue_out_{camera_id}"
                )

            link_ret = demux_src_pad.link(queue_sink_pad)
            if link_ret != Gst.PadLinkReturn.OK:
                raise RuntimeError(
                    f"Failed to link demux src_{source_id} → queue_out_{camera_id}: "
                    f"{link_ret}"
                )

            # --- Sync state with the running pipeline ---------------------
            for elem in elements:
                elem.sync_state_with_parent()

            # --- Track the branch -----------------------------------------
            branch = OutputBranch(
                camera_id=camera_id,
                source_id=source_id,
                rtp_port=rtp_port,
                elements=elements,
                rtsp_url=rtsp_url,
                webrtc_url=webrtc_url,
                created_at=time.monotonic(),
            )
            self._branches[camera_id] = branch

            logger.info(
                "Output stream added: %s -> %s (RTP port %d)",
                camera_id,
                rtsp_url,
                rtp_port,
            )
            return True

        except Exception:
            logger.exception(
                "Failed to add output branch for %s", camera_id
            )
            # Best-effort cleanup: remove any elements we already added
            self._cleanup_partial_branch(camera_id, locals())
            return False

    def remove_output_branch(self, camera_id: str) -> bool:
        """Remove a per-camera output branch from the pipeline.

        Sets all elements to NULL, unlinks them, removes from the
        pipeline, and releases the demux src pad.

        Returns:
            True on success, False if the branch was not found.
        """
        branch = self._branches.pop(camera_id, None)
        if branch is None:
            logger.warning(
                "remove_output_branch: no branch for %s", camera_id
            )
            return False

        try:
            # Set all elements to NULL state first
            for elem in reversed(branch.elements):
                elem.set_state(Gst.State.NULL)

            # Unlink demux src pad from queue sink pad
            if self._demux is not None:
                demux_src_pad = self._demux.get_static_pad(
                    f"src_{branch.source_id}"
                )
                if demux_src_pad is not None:
                    queue = branch.elements[0]  # First element is the queue
                    queue_sink_pad = queue.get_static_pad("sink")
                    if queue_sink_pad is not None and demux_src_pad.is_linked():
                        demux_src_pad.unlink(queue_sink_pad)
                    self._demux.release_request_pad(demux_src_pad)

            # Remove elements from pipeline
            for elem in branch.elements:
                self._pipeline.remove(elem)

            logger.info(
                "Output stream removed: %s (was on RTP port %d)",
                camera_id,
                branch.rtp_port,
            )
            return True

        except Exception:
            logger.exception(
                "Error removing output branch for %s", camera_id
            )
            return False

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def get_output_url(self, camera_id: str) -> str:
        """Return the RTSP URL for a camera's annotated output stream."""
        return f"rtsp://mediamtx:8554/{camera_id}-out"

    def get_webrtc_url(self, camera_id: str) -> str:
        """Return the WebRTC WHEP URL for frontend playback."""
        return f"http://mediamtx:8889/{camera_id}-out/whep"

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    def get_active_outputs(self) -> dict[str, dict]:
        """Return status information for all active output branches.

        Returns:
            Dict mapping camera_id to a status dict containing:
            - camera_id, source_id, rtp_port
            - rtsp_url, webrtc_url
            - state (GStreamer element state of the udpsink)
            - created_at (monotonic timestamp)
        """
        result: dict[str, dict] = {}
        for camera_id, branch in self._branches.items():
            # Query the state of the udpsink (last element) as a proxy
            # for the overall branch health.
            state_str = "unknown"
            if branch.elements:
                udpsink = branch.elements[-1]
                success, state, _pending = udpsink.get_state(
                    Gst.CLOCK_TIME_NONE // 1000  # 1 ms timeout
                )
                if success != Gst.StateChangeReturn.FAILURE:
                    state_str = state.value_nick

            result[camera_id] = {
                "camera_id": camera_id,
                "source_id": branch.source_id,
                "rtp_port": branch.rtp_port,
                "rtsp_url": branch.rtsp_url,
                "webrtc_url": branch.webrtc_url,
                "state": state_str,
                "created_at": branch.created_at,
            }
        return result

    @property
    def active_branch_count(self) -> int:
        """Number of currently active output branches."""
        return len(self._branches)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cleanup_partial_branch(
        self, camera_id: str, local_vars: dict
    ) -> None:
        """Best-effort cleanup after a failed add_output_branch.

        Attempts to remove any elements that were successfully added to
        the pipeline before the failure occurred.
        """
        element_names = [
            f"queue_out_{camera_id}",
            f"nvvidconv_out_{camera_id}",
            f"caps_out_{camera_id}",
            f"h264enc_{camera_id}",
            f"rtppay_{camera_id}",
            f"udpsink_{camera_id}",
        ]
        for name in element_names:
            elem = self._pipeline.get_by_name(name)
            if elem is not None:
                try:
                    elem.set_state(Gst.State.NULL)
                    self._pipeline.remove(elem)
                except Exception:
                    pass  # Best effort

        # Release demux pad if we got one
        if self._demux is not None:
            source_id = local_vars.get("source_id")
            if source_id is not None:
                pad = self._demux.get_static_pad(f"src_{source_id}")
                if pad is not None:
                    try:
                        self._demux.release_request_pad(pad)
                    except Exception:
                        pass
