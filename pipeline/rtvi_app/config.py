# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""RTVI App configuration."""
from pydantic_settings import BaseSettings


class RTVISettings(BaseSettings):
    # RTSP sources — comma-separated: cam-01=rtsp://...,cam-02=rtsp://...
    sources: str = ""

    # Frame sampling
    sample_interval_s: float = 10.0  # 1 frame every 10 seconds
    batch_duration_s: float = 60.0  # Collect frames over 60 seconds
    # -> batch_size = batch_duration_s / sample_interval_s = 6 frames per batch

    # Scaling
    frame_width: int = 1280
    frame_height: int = 720

    # VLM
    vlm_base_url: str = "http://localhost:8000/v1"
    vlm_model: str = "Qwen/Qwen2.5-VL-7B-Instruct-AWQ"
    vlm_max_tokens: int = 512
    vlm_temperature: float = 0.3
    vlm_timeout_s: int = 60

    # Kafka
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_topic: str = "wastewatch.vlm.summaries"

    # Multi-GPU
    num_gpus: int = 1  # Auto-detected if 0

    # Threading
    extractor_threads: int = 4  # MediaExtractor worker threads
    vlm_workers: int = 2  # Concurrent VLM requests

    model_config = {"env_prefix": "RTVI_"}


rtvi_settings = RTVISettings()
