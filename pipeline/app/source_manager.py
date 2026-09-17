import logging
from urllib.parse import urlparse

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

logger = logging.getLogger(__name__)

# SECURITY: Only allow RTSP scheme for camera sources
_ALLOWED_SCHEMES = {"rtsp", "rtsps"}


def _validate_rtsp_uri(uri: str) -> None:
    """Validate that a URI is a safe RTSP source. Rejects file://, http://, etc."""
    parsed = urlparse(uri)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError(f"Unsupported URI scheme: {parsed.scheme!r} (only rtsp:// allowed)")
    if not parsed.hostname:
        raise ValueError(f"Missing hostname in URI: {uri!r}")
    # Block metadata endpoints and loopback abuse
    hostname = parsed.hostname.lower()
    if hostname in ("169.254.169.254", "metadata.google.internal", "localhost", "127.0.0.1", "::1"):
        raise ValueError(f"Blocked hostname: {hostname}")


class SourceManager:
    """Manages dynamic addition and removal of RTSP sources in a DeepStream pipeline."""

    def __init__(self, pipeline: Gst.Pipeline, streammux: Gst.Element) -> None:
        self._pipeline = pipeline
        self._streammux = streammux
        # camera_id -> (uri, pad_id)
        self._sources: dict[str, tuple[str, int]] = {}
        self._next_pad_id: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_source(self, camera_id: str, uri: str) -> int:
        """Add an RTSP source to the running pipeline.

        Creates a uridecodebin element, connects the pad-added callback so that
        decoded video pads are linked to the stream-muxer, adds and syncs the
        element state with the pipeline, then returns the assigned pad id.
        """
        _validate_rtsp_uri(uri)
        pad_id = self._next_pad_id
        self._next_pad_id += 1

        src_elem = Gst.ElementFactory.make("uridecodebin", f"src_{camera_id}")
        if src_elem is None:
            raise RuntimeError(f"Failed to create uridecodebin for camera {camera_id}")

        # uridecodebin creates the actual rtspsrc internally. Configure it
        # through source-setup before playback starts; otherwise rtspsrc's
        # defaults retain a 2000ms jitter buffer and allow it to grow when
        # downstream inference briefly falls behind, which turns into 5-8s
        # of stale-frame latency instead of dropping old live video.
        src_elem.connect("source-setup", self._on_source_setup, camera_id)
        src_elem.set_property("uri", uri)
        src_elem.connect(
            "pad-added",
            self._on_pad_added,
            camera_id,
            pad_id,
        )

        self._pipeline.add(src_elem)
        src_elem.sync_state_with_parent()

        self._sources[camera_id] = (uri, pad_id)
        logger.info("Added source %s (pad %d) → %s", camera_id, pad_id, uri)
        return pad_id

    def remove_source(self, camera_id: str) -> None:
        """Remove a source from the running pipeline.

        Sets the source element to NULL state, releases the streammux sink pad,
        and removes the element from the pipeline.
        """
        if camera_id not in self._sources:
            logger.warning("remove_source: unknown camera_id %s", camera_id)
            return

        _uri, pad_id = self._sources.pop(camera_id)
        src_elem = self._pipeline.get_by_name(f"src_{camera_id}")

        if src_elem is not None:
            src_elem.set_state(Gst.State.NULL)
            self._pipeline.remove(src_elem)

        mux_sink_pad = self._streammux.get_static_pad(f"sink_{pad_id}")
        if mux_sink_pad is not None:
            self._streammux.release_request_pad(mux_sink_pad)

        logger.info("Removed source %s (pad %d)", camera_id, pad_id)

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _on_source_setup(
        self,
        _decodebin: Gst.Element,
        source: Gst.Element,
        camera_id: str,
    ) -> None:
        """Bound RTSP buffering so inference stays close to real time."""
        factory = source.get_factory()
        factory_name = factory.get_name() if factory is not None else ""
        if factory_name != "rtspsrc":
            return

        properties = {prop.name for prop in source.list_properties()}
        low_latency_values = {
            # Bumped 100ms -> 200ms once cam-03/cam-04 were added: the
            # rtsp-streamer container now runs 6 concurrent software x264
            # encodes on one host CPU (~315% CPU measured via `docker
            # stats`), which stutters the encode/publish loop under load.
            # 100ms wasn't enough slack to absorb that jitter and the
            # aggressive drop-on-latency was discarding partial GOPs,
            # reading as corrupted frames on the annotated output.
            "latency": 200,          # milliseconds of network jitter tolerance
            "drop-on-latency": True, # discard stale frames instead of growing backlog
            "buffer-mode": 0,        # none: use RTP timestamps without extra buffering
            "protocols": 0x4,        # TCP: predictable transport on the local LAN
            "ntp-sync": False,
        }
        for name, value in low_latency_values.items():
            if name in properties:
                source.set_property(name, value)

        logger.info(
            "Configured low-latency RTSP source for %s (latency=200ms, drop-on-latency=true)",
            camera_id,
        )

    def _on_pad_added(
        self,
        src: Gst.Element,
        new_pad: Gst.Pad,
        camera_id: str,
        pad_id: int,
    ) -> None:
        """Handle pad-added signal from uridecodebin.

        Only video pads are linked; audio/subtitle pads are silently ignored.
        """
        caps = new_pad.get_current_caps()
        if caps is None:
            caps = new_pad.query_caps(None)

        structure = caps.get_structure(0)
        media_type = structure.get_name() if structure else ""

        if not media_type.startswith("video/"):
            logger.debug(
                "_on_pad_added: skipping non-video pad for %s (%s)",
                camera_id,
                media_type,
            )
            return

        sink_pad = self._streammux.get_request_pad(f"sink_{pad_id}")
        if sink_pad is None:
            logger.error(
                "_on_pad_added: could not get streammux sink_%d for %s",
                pad_id,
                camera_id,
            )
            return

        if sink_pad.is_linked():
            logger.debug("_on_pad_added: sink_%d already linked", pad_id)
            return

        ret = new_pad.link(sink_pad)
        if ret != Gst.PadLinkReturn.OK:
            logger.error(
                "_on_pad_added: failed to link %s → streammux sink_%d: %s",
                camera_id,
                pad_id,
                ret,
            )
        else:
            logger.info("Linked %s → streammux sink_%d", camera_id, pad_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active_sources(self) -> dict[str, str]:
        """Return a mapping of camera_id → uri for all active sources."""
        return {cam_id: uri for cam_id, (uri, _) in self._sources.items()}

    @property
    def pad_to_camera(self) -> dict[int, str]:
        """Return a mapping of pad_id → camera_id for reverse lookup."""
        return {pad_id: cam_id for cam_id, (_, pad_id) in self._sources.items()}
