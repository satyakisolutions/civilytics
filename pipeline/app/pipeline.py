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
from app.event_fsm import EventFSMManager
from app.health import CameraHealth
from app.kafka_producer import PipelineKafkaProducer
from app.preprocessor import ROIPreprocessor
from app.roi import ROIManager
from app.source_manager import SourceManager
from app.stream_watcher import StreamWatcher
from app.tracking import TrackStateManager

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
    ctx: dict,
) -> Gst.PadProbeReturn:
    """GStreamer pad probe that extracts NvDsBatchMeta and publishes to Kafka."""
    kafka_producer: PipelineKafkaProducer = ctx["kafka_producer"]
    health: CameraHealth = ctx["health"]
    track_manager: TrackStateManager = ctx["track_manager"]
    roi_manager: ROIManager = ctx["roi_manager"]
    fsm_manager: EventFSMManager = ctx["fsm_manager"]

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
                class_name = COCO_CLASS_MAP[class_id]
                track_id = None if int(obj_meta.object_id) == 0xFFFFFFFFFFFFFFFF else int(obj_meta.object_id)
                bbox_dict = {
                    "x": round(rect.left / frame_width, 6),
                    "y": round(rect.top / frame_height, 6),
                    "w": round(rect.width / frame_width, 6),
                    "h": round(rect.height / frame_height, 6),
                }
                detection_dict: dict = {
                    "class_id": class_id,
                    "label": class_name,
                    "confidence": round(float(obj_meta.confidence), 4),
                    "bbox": bbox_dict,
                    "track_id": track_id,
                }

                if track_id is not None:
                    # bbox center (normalized)
                    bbox_center_x = bbox_dict["x"] + bbox_dict["w"] / 2
                    bbox_center_y = bbox_dict["y"] + bbox_dict["h"] / 2
                    in_roi = roi_manager.is_in_any_roi(camera_id, bbox_center_x, bbox_center_y)

                    track_info = track_manager.update_track(camera_id, track_id, class_name, bbox_dict, in_roi)

                    fsm_event = fsm_manager.update(
                        camera_id, track_id, class_name,
                        in_roi=in_roi,
                        dwell_time_s=track_info.dwell_time_s,
                        bbox=bbox_dict,
                    )

                    if fsm_event:
                        kafka_producer.send_event(fsm_event)
                        if fsm_event.state.value == "violation":
                            kafka_producer.send_vlm_request(
                                camera_id=camera_id, track_id=track_id,
                                zone_name=camera_id, dwell_time_s=track_info.dwell_time_s,
                                class_name=class_name,
                                path_summary="stationary" if track_info.dwell_time_s > 10 else "moving",
                            )

                            # Trigger LPR for vehicles
                            if class_name in ("car", "motorcycle", "bus", "truck"):
                                kafka_producer.send_lpr_request(
                                    camera_id=camera_id,
                                    track_id=track_id,
                                    vehicle_bbox=bbox_dict,
                                    class_name=class_name,
                                )

                    # Enrich detection with tracking data
                    detection_dict["dwell_time_s"] = round(track_info.dwell_time_s, 1)
                    detection_dict["in_roi"] = in_roi
                    detection_dict["fsm_state"] = fsm_manager.get_fsm_state(camera_id, track_id)

                detections.append(detection_dict)

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


def build_pipeline() -> tuple[Gst.Pipeline, Gst.Element, Gst.Element, Gst.Element]:
    """Construct and return the DeepStream GStreamer pipeline.

    Returns:
        (pipeline, streammux, fakesink, nvdsanalytics)  — the caller needs
        streammux to hand to SourceManager, fakesink to attach the metadata pad
        probe, and nvdsanalytics for ROI/dwell queries.
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

    # --- Object tracker (ByteTrack via nvtracker) ---------------------------
    nvtracker = make_element("nvtracker", "tracker")
    nvtracker.set_property("tracker-width", 640)
    nvtracker.set_property("tracker-height", 384)
    nvtracker.set_property("ll-lib-file", cfg.tracker_ll_lib)
    nvtracker.set_property("ll-config-file", cfg.tracker_config_path)
    nvtracker.set_property("gpu-id", 0)
    nvtracker.set_property("enable-batch-process", 1)
    pipeline.add(nvtracker)

    # --- Analytics (ROI / line-crossing via nvdsanalytics) ------------------
    nvdsanalytics = make_element("nvdsanalytics", "analytics")
    nvdsanalytics.set_property("config-file", cfg.analytics_config_path)
    pipeline.add(nvdsanalytics)

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

    # --- Link main chain: streammux → nvinfer → nvtracker → nvdsanalytics → nvosd → tee
    if not streammux.link(nvinfer):
        logger.critical("Failed to link streammux → nvinfer")
        sys.exit(1)
    if not nvinfer.link(nvtracker):
        logger.critical("Failed to link nvinfer → nvtracker")
        sys.exit(1)
    if not nvtracker.link(nvdsanalytics):
        logger.critical("Failed to link nvtracker → nvdsanalytics")
        sys.exit(1)
    if not nvdsanalytics.link(nvosd):
        logger.critical("Failed to link nvdsanalytics → nvosd")
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

    return pipeline, streammux, fakesink, nvdsanalytics


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    Gst.init(None)

    pipeline, streammux, fakesink, nvdsanalytics = build_pipeline()  # noqa: F841

    kafka_producer = PipelineKafkaProducer()
    health = CameraHealth(timeout_s=30.0, max_retries=5)
    source_manager = SourceManager(pipeline, streammux)
    track_manager = TrackStateManager(lost_timeout_s=pipeline_settings.dwell_observe_threshold_s * 2)
    roi_manager = ROIManager()
    fsm_manager = EventFSMManager(
        enter_threshold_s=pipeline_settings.dwell_enter_threshold_s,
        observe_threshold_s=pipeline_settings.dwell_observe_threshold_s,
    )
    roi_preprocessor = ROIPreprocessor(
        roi_manager,
        frame_width=pipeline_settings.input_width,
        frame_height=pipeline_settings.input_height,
    )

    # Attach metadata probe to fakesink's sink pad
    fakesink_sink_pad = fakesink.get_static_pad("sink")
    if fakesink_sink_pad is None:
        logger.critical("Could not get fakesink sink pad")
        sys.exit(1)

    probe_ctx = {
        "kafka_producer": kafka_producer,
        "health": health,
        "track_manager": track_manager,
        "roi_manager": roi_manager,
        "fsm_manager": fsm_manager,
        "roi_preprocessor": roi_preprocessor,
    }
    fakesink_sink_pad.add_probe(
        Gst.PadProbeType.BUFFER,
        extract_metadata,
        probe_ctx,
    )

    # Stream watcher — dynamic source addition with polling and auto-reconnect
    stream_watcher = StreamWatcher(
        source_manager=source_manager,
        health=health,
        poll_interval_s=10.0,
        probe_timeout_s=5.0,
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
                stream_watcher.register_source(cam_id, uri)
    else:
        logger.warning(
            "No WW_SOURCES configured — pipeline will run with no input sources."
        )

    # Initial ROI preprocessor update after sources are registered
    active_cams = list(source_manager.active_sources.keys())
    if active_cams:
        crops = roi_preprocessor.update_crops(active_cams)
        if crops:
            savings = roi_preprocessor.get_gpu_savings_estimate()
            for cam_id, pct in savings.items():
                logger.info("Camera %s: ROI crop saves ~%.0f%% GPU inference", cam_id, pct)

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
        # Stream watcher handles reconnection with exponential backoff
        stream_watcher.poll()

        stale_events = fsm_manager.prune_stale()
        for evt in stale_events:
            kafka_producer.send_event(evt)
        track_manager.prune_lost()

        # Update ROI preprocessor crops if sources changed
        current_cams = list(source_manager.active_sources.keys())
        roi_preprocessor.update_crops(current_cams)

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
