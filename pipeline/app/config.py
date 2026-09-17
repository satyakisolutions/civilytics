from pydantic_settings import BaseSettings


class PipelineSettings(BaseSettings):
    kafka_bootstrap_servers: str = "kafka:9092"
    mediamtx_rtsp_url: str = "rtsp://mediamtx:8554"
    database_url: str = ""  # Must be set via WW_DATABASE_URL
    # Pinned to 1 because the RF-DETR ONNX is exported with a static
    # batch axis (Roboflow's release does not expose a dynamic dim 0).
    # The TRT engine is therefore batch=1 only, and any streammux
    # batch>1 makes nvinfer silently process only frame[0] of each
    # batched buffer — every additional source after cam-01 ends up
    # with an empty obj_meta_list. Override via WW_BATCH_SIZE only
    # after rebuilding the engine with a dynamic-batch ONNX export.
    batch_size: int = 1
    frame_skip: int = 2
    input_width: int = 1920
    input_height: int = 1080
    processing_width: int = 640
    processing_height: int = 640
    muxer_output_width: int = 1920
    muxer_output_height: int = 1080

    # Tracking
    tracker_config_path: str = "configs/nvtracker_bytetrack.yml"
    tracker_ll_lib: str = "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"

    # ROI
    analytics_config_path: str = "configs/nvdsanalytics_config.txt"

    # Dwell thresholds — narrow-road deployment
    dwell_enter_threshold_s: float = 1.5      # 1.5s in ROI — suspicious/yellow, fires earlier
    dwell_observe_threshold_s: float = 5.0    # 5s — suspicious, start VLM
    dwell_violation_threshold_s: float = 3.0  # 3s in ROI before VIOLATION/alert — fast-moving subjects (e.g. vehicles) don't linger 15s
    # Person-only dwell-violation threshold, separate from the generic one
    # above -- a person needs a real loitering window (not a 3s flash)
    # before dwell time alone turns them red without bag/waste evidence.
    person_dwell_violation_threshold_s: float = 10.0

    # How long a "waste" detection must persist before it's treated as an
    # actually-dumped object (vs. a person's carried bag briefly clipping
    # the waste class mid-motion, or a one-frame false positive) and
    # triggers the blinking OSD highlight + dumper-attribution alert.
    # Independent of the person-in-ROI thresholds above -- waste tracking
    # isn't ROI-gated, it's presence-based.
    # Runtime clamps per-camera overrides to <=1.0s so paired waste/person
    # identification can never fall back to a long dwell delay.
    waste_dump_alert_threshold_s: float = 0.5
    # Maximum edge-to-edge gap between the new waste bbox and the person
    # bbox on the waste object's first frame, measured in source-image pixels
    # after compensating for streammux resizing. Center distance is unsuitable
    # because a person's center is their torso, far above an item at their feet.
    waste_proximity_px: float = 60.0
    # Maximum edge-to-edge gap between a carried-item (bag) bbox and a
    # person bbox, same source-pixel measurement as waste_proximity_px,
    # used to decide if a person is "carrying" that item this frame.
    bag_proximity_px: float = 15.0

    # A waste object still in VIOLATION/alerting/escalated state keeps
    # re-alerting at this interval instead of firing once and going
    # silent forever -- real, unresolved dumping should keep reminding
    # operators; also matters for looping test video sources (DeepStream's
    # tracker doesn't treat a video loop as a scene change, so the same
    # track_id can persist indefinitely across every loop iteration).
    waste_repeat_alert_interval_s: float = 37.0

    model_config = {"env_prefix": "WW_"}


pipeline_settings = PipelineSettings()
