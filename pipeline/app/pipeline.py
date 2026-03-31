"""WasteWatch DeepStream pipeline.

Reads RTSP streams from MediaMTX, runs RF-DETR object detection via nvinfer,
re-streams annotated video back to MediaMTX over RTP/UDP, and publishes
per-frame detection metadata to Kafka.

Environment variables (all prefixed WW_):
    WW_SOURCES            Comma-separated list of camera_id=rtsp://... pairs.
                          Example: "cam-01=rtsp://mediamtx:8554/cam-01,cam-02=rtsp://..."
    WW_KAFKA_BOOTSTRAP_SERVERS
    WW_MEDIAMTX_RTSP_URL  Base RTSP URL for the MediaMTX server (default rtsp://mediamtx:8554)
    WW_BATCH_SIZE         nvinfer batch size (default 4)
    WW_FRAME_SKIP         nvinfer inference interval (default 2)
    WW_MUXER_OUTPUT_WIDTH / WW_MUXER_OUTPUT_HEIGHT
"""

import logging
import os
import sys
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gst  # noqa: E402

import pyds  # noqa: E402 — provided by the DeepStream Docker image

from app.config import pipeline_settings
from app.health import CameraHealth
from app.kafka_producer import PipelineKafkaProducer
from app.source_manager import SourceManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Subset of COCO classes that WasteWatch cares about.
COCO_CLASS_MAP: dict[int, str] = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_element(factory_name: str, name: str) -> Gst.Element:
    """Create a GStreamer element; exit the process if the factory is missing."""
    elem = Gst.ElementFactory.make(factory_name, name)
    if elem is None:
        logger.critical("Could not create GStreamer element '%s' (factory: %s)", name, factory_name)
        sys.exit(1)
    return elem


# ---------------------------------------------------------------------------
# Pad probe — metadata extraction
# ---------------------------------------------------------------------------


def extract_metadata(
    pad: Gst.Pad,
    info: Gst.PadProbeInfo,
    kafka_producer: PipelineKafkaProducer,
    health: CameraHealth,
) -> Gst.PadProbeReturn:
    """GStreamer pad probe that extracts NvDsBatchMeta and publishes to Kafka."""
    gst_buffer = info.get_buffer()
    if gst_buffer is None:
        return Gst.PadProbeReturn.OK

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    if batch_meta is None:
        return Gst.PadProbeReturn.OK

    frame_meta_list = batch_meta.frame_meta_list
    while frame_meta_list is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(frame_meta_list.data)
        except StopIteration:
            break

        camera_id = f"cam-{frame_meta.source_id:02d}"
        frame_width = frame_meta.source_frame_width or pipeline_settings.input_width
        frame_height = frame_meta.source_frame_height or pipeline_settings.input_height

        detections: list[dict] = []
        obj_meta_list = frame_meta.obj_meta_list
        while obj_meta_list is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(obj_meta_list.data)
            except StopIteration:
                break

            class_id: int = obj_meta.class_id
            if class_id in COCO_CLASS_MAP:
                rect = obj_meta.rect_params
                detections.append(
                    {
                        "class_id": class_id,
                        "label": COCO_CLASS_MAP[class_id],
                        "confidence": round(float(obj_meta.confidence), 4),
                        "bbox": {
                            "x": round(rect.left / frame_width, 6),
                            "y": round(rect.top / frame_height, 6),
                            "w": round(rect.width / frame_width, 6),
                            "h": round(rect.height / frame_height, 6),
                        },
                        "track_id": int(obj_meta.object_id),
                    }
                )

            try:
                obj_meta_list = obj_meta_list.next
            except StopIteration:
                break

        metadata: dict = {
            "camera_id": camera_id,
            "frame_num": frame_meta.frame_num,
            "timestamp_ms": int(time.monotonic() * 1000),
            "detections": detections,
        }

        kafka_producer.send_metadata(camera_id, metadata)
        kafka_producer.poll()
        health.on_frame(camera_id)

        try:
            frame_meta_list = frame_meta_list.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------


def build_pipeline() -> tuple[Gst.Pipeline, Gst.Element, Gst.Element]:
    """Construct and return the DeepStream GStreamer pipeline.

    Returns:
        (pipeline, streammux, fakesink)  — the caller needs streammux to hand to
        SourceManager and fakesink to attach the metadata pad probe.
    """
    cfg = pipeline_settings

    pipeline: Gst.Pipeline = Gst.Pipeline.new("wastewatch-pipeline")

    # --- Stream muxer -------------------------------------------------------
    streammux = make_element("nvstreammux", "stream_muxer")
    streammux.set_property("batch-size", cfg.batch_size)
    streammux.set_property("width", cfg.muxer_output_width)
    streammux.set_property("height", cfg.muxer_output_height)
    streammux.set_property("batched-push-timeout", 4_000_000)
    pipeline.add(streammux)

    # --- Inference (RF-DETR via nvinfer) ------------------------------------
    nvinfer = make_element("nvinfer", "primary_infer")
    nvinfer.set_property("config-file-path", "configs/nvinfer_rfdetr.txt")
    pipeline.add(nvinfer)

    # --- On-screen display --------------------------------------------------
    nvosd = make_element("nvdsosd", "on_screen_display")
    pipeline.add(nvosd)

    # --- Tee — splits stream to RTSP output branch and metadata branch ------
    tee = make_element("tee", "stream_tee")
    pipeline.add(tee)

    # --- Branch 1: RTSP re-stream to MediaMTX via UDP -----------------------
    queue_rtsp = make_element("queue", "queue_rtsp")
    nvvidconv = make_element("nvvideoconvert", "nvvidconv_rtsp")
    capsfilter = make_element("capsfilter", "caps_rtsp")
    caps = Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420")
    capsfilter.set_property("caps", caps)
    encoder = make_element("nvv4l2h264enc", "h264_encoder")
    encoder.set_property("bitrate", 4_000_000)
    rtppay = make_element("rtph264pay", "rtp_pay")
    rtppay.set_property("config-interval", 1)
    rtppay.set_property("pt", 96)
    udpsink = make_element("udpsink", "udp_out")
    udpsink.set_property("host", "mediamtx")
    udpsink.set_property("port", 8554)
    udpsink.set_property("sync", False)

    for elem in (queue_rtsp, nvvidconv, capsfilter, encoder, rtppay, udpsink):
        pipeline.add(elem)

    # --- Branch 2: metadata extraction (fakesink) ---------------------------
    queue_meta = make_element("queue", "queue_meta")
    fakesink = make_element("fakesink", "fakesink_meta")
    fakesink.set_property("sync", False)
    fakesink.set_property("async", False)

    for elem in (queue_meta, fakesink):
        pipeline.add(elem)

    # --- Link main chain: streammux → nvinfer → nvosd → tee ----------------
    if not streammux.link(nvinfer):
        logger.critical("Failed to link streammux → nvinfer")
        sys.exit(1)
    if not nvinfer.link(nvosd):
        logger.critical("Failed to link nvinfer → nvosd")
        sys.exit(1)
    if not nvosd.link(tee):
        logger.critical("Failed to link nvosd → tee")
        sys.exit(1)

    # --- Link branch 1: tee → queue_rtsp → nvvidconv → capsfilter → encoder → rtppay → udpsink
    tee_src_rtsp = tee.get_request_pad("src_%u")
    queue_rtsp_sink = queue_rtsp.get_static_pad("sink")
    if tee_src_rtsp.link(queue_rtsp_sink) != Gst.PadLinkReturn.OK:
        logger.critical("Failed to link tee → queue_rtsp")
        sys.exit(1)

    for src_elem, dst_elem in (
        (queue_rtsp, nvvidconv),
        (nvvidconv, capsfilter),
        (capsfilter, encoder),
        (encoder, rtppay),
        (rtppay, udpsink),
    ):
        if not src_elem.link(dst_elem):
            logger.critical("Failed to link %s → %s", src_elem.get_name(), dst_elem.get_name())
            sys.exit(1)

    # --- Link branch 2: tee → queue_meta → fakesink -------------------------
    tee_src_meta = tee.get_request_pad("src_%u")
    queue_meta_sink = queue_meta.get_static_pad("sink")
    if tee_src_meta.link(queue_meta_sink) != Gst.PadLinkReturn.OK:
        logger.critical("Failed to link tee → queue_meta")
        sys.exit(1)

    if not queue_meta.link(fakesink):
        logger.critical("Failed to link queue_meta → fakesink")
        sys.exit(1)

    return pipeline, streammux, fakesink


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    Gst.init(None)

    pipeline, streammux, fakesink = build_pipeline()

    kafka_producer = PipelineKafkaProducer()
    health = CameraHealth(timeout_s=30.0, max_retries=5)
    source_manager = SourceManager(pipeline, streammux)

    # Attach metadata probe to fakesink's sink pad
    fakesink_sink_pad = fakesink.get_static_pad("sink")
    if fakesink_sink_pad is None:
        logger.critical("Could not get fakesink sink pad")
        sys.exit(1)

    fakesink_sink_pad.add_probe(
        Gst.PadProbeType.BUFFER,
        extract_metadata,
        kafka_producer,
        health,
    )

    # Load initial sources from WW_SOURCES env var
    # Format: "cam-01=rtsp://mediamtx:8554/cam-01,cam-02=rtsp://mediamtx:8554/cam-02"
    sources_env = os.environ.get("WW_SOURCES", "")
    if sources_env:
        for entry in sources_env.split(","):
            entry = entry.strip()
            if "=" not in entry:
                logger.warning("Skipping malformed source entry: %r", entry)
                continue
            cam_id, _, uri = entry.partition("=")
            cam_id = cam_id.strip()
            uri = uri.strip()
            if cam_id and uri:
                try:
                    source_manager.add_source(cam_id, uri)
                except Exception:
                    logger.exception("Failed to add source %s", cam_id)
    else:
        logger.warning(
            "No WW_SOURCES configured — pipeline will run with no input sources."
        )

    # GLib main loop
    loop = GLib.MainLoop()

    # Bus message handler
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_bus_message(bus: Gst.Bus, message: Gst.Message) -> bool:  # noqa: ARG001
        msg_type = message.type
        if msg_type == Gst.MessageType.EOS:
            logger.info("End-of-stream received — stopping")
            loop.quit()
        elif msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error("GStreamer error: %s | debug: %s", err.message, debug)
            loop.quit()
        elif msg_type == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            logger.warning("GStreamer warning: %s | debug: %s", warn.message, debug)
        return True

    bus.connect("message", on_bus_message)

    # Periodic health check — runs every 10 seconds
    def health_check_cb() -> bool:
        timed_out = health.check_health()
        for cam_id in timed_out:
            uri = source_manager.active_sources.get(cam_id)
            if uri:
                logger.warning("Camera %s timed out — reconnecting", cam_id)
                try:
                    source_manager.remove_source(cam_id)
                    source_manager.add_source(cam_id, uri)
                except Exception:
                    logger.exception("Failed to reconnect camera %s", cam_id)
        return True  # Keep the timeout alive

    GLib.timeout_add_seconds(10, health_check_cb)

    # Start pipeline
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        logger.critical("Failed to set pipeline to PLAYING state")
        sys.exit(1)

    logger.info("Pipeline running — press Ctrl-C to stop")
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        logger.info("Shutting down pipeline")
        pipeline.set_state(Gst.State.NULL)
        kafka_producer.flush()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
