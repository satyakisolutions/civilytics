from pydantic_settings import BaseSettings


class PipelineSettings(BaseSettings):
    kafka_bootstrap_servers: str = "kafka:9092"
    mediamtx_rtsp_url: str = "rtsp://mediamtx:8554"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/wastewatch"
    batch_size: int = 4
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

    # Dwell thresholds
    dwell_enter_threshold_s: float = 5.0
    dwell_observe_threshold_s: float = 15.0

    model_config = {"env_prefix": "WW_"}


pipeline_settings = PipelineSettings()
