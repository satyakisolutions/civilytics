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

    # Dwell thresholds — tuned for narrow-road LSG deployment
    dwell_enter_threshold_s: float = 3.0
    dwell_observe_threshold_s: float = 10.0
    dwell_violation_threshold_s: float = 15.0

    model_config = {"env_prefix": "WW_"}


pipeline_settings = PipelineSettings()
