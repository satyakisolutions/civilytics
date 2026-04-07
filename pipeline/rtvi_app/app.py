# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""WasteWatch RTVI -- Real-Time Video Intelligence Application.

Uses DeepStream pyservicemaker MediaExtractor to sample frames from
N RTSP streams, batch them per-stream, and summarize via VLM.

Architecture:
    Per stream (independent, never mixed):
        MediaExtractor(interval=10s) -> FrameBatcher(6 frames/60s) -> VLM -> Kafka

    Multi-GPU: streams distributed across GPUs via GPUScheduler

Usage:
    RTVI_SOURCES="cam-01=rtsp://...,cam-02=rtsp://..." python3 -m rtvi_app.app
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from pyservicemaker.utils import MediaChunk, MediaExtractor

from rtvi_app.config import rtvi_settings as settings
from rtvi_app.frame_batcher import FrameBatcher
from rtvi_app.gpu_scheduler import GPUScheduler
from rtvi_app.kafka_output import KafkaOutput
from rtvi_app.vlm_summarizer import VLMSummarizer

logger = logging.getLogger(__name__)

# Graceful shutdown flag
_shutdown = threading.Event()


# ------------------------------------------------------------------
# Source parsing
# ------------------------------------------------------------------

def parse_sources(raw: str) -> dict[str, str]:
    """Parse ``RTVI_SOURCES`` into ``{camera_id: rtsp_url}``.

    Format: ``cam-01=rtsp://host/path,cam-02=rtsp://host2/path``
    """
    sources: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            logger.warning("Ignoring malformed source entry: %s", entry)
            continue
        cam_id, url = entry.split("=", 1)
        sources[cam_id.strip()] = url.strip()
    return sources


# ------------------------------------------------------------------
# Per-stream processing loop
# ------------------------------------------------------------------

def _process_stream(
    camera_id: str,
    queue,
    batcher: FrameBatcher,
    summarizer: VLMSummarizer,
    kafka_out: KafkaOutput,
) -> None:
    """Consume frames from a MediaExtractor queue, batch, and summarize.

    Runs in a dedicated thread per camera stream.  Exits when the queue
    yields ``None`` (end of stream) or when the global shutdown event is
    set.
    """
    logger.info("%s: stream processing thread started", camera_id)
    try:
        while not _shutdown.is_set():
            frame = queue.get(timeout=5)
            if frame is None:
                logger.info("%s: end of stream", camera_id)
                break

            batch = batcher.add_frame(frame)
            if batch is not None:
                _summarize_batch(camera_id, batch, summarizer, kafka_out)

    except Exception:
        if not _shutdown.is_set():
            logger.exception("%s: stream processing error", camera_id)
    finally:
        # Flush any remaining partial batch
        partial = batcher.flush()
        if partial:
            logger.info("%s: processing partial batch on exit (%d frames)", camera_id, len(partial))
            _summarize_batch(camera_id, partial, summarizer, kafka_out)
        logger.info("%s: stream processing thread exited", camera_id)


def _summarize_batch(
    camera_id: str,
    batch: list,
    summarizer: VLMSummarizer,
    kafka_out: KafkaOutput,
) -> None:
    """Send a frame batch through VLM and publish the result to Kafka."""
    try:
        summary = summarizer.summarize(
            camera_id=camera_id,
            frames=[f.tensor for f in batch],
            timestamps=[f.timestamp for f in batch],
        )
        kafka_out.send_summary(camera_id, summary)
        logger.info(
            "%s: summary published (risk=%s, inference=%.0fms)",
            camera_id,
            summary.get("risk_level", "?"),
            summary.get("inference_time_ms", 0),
        )
    except Exception:
        logger.exception("%s: failed to summarize batch", camera_id)


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ---- Parse sources ----
    sources = parse_sources(settings.sources)
    if not sources:
        logger.error(
            "No sources configured.  Set RTVI_SOURCES env var, e.g.:\n"
            '  RTVI_SOURCES="cam-01=rtsp://host/stream1,cam-02=rtsp://host/stream2"'
        )
        sys.exit(1)

    logger.info("RTVI starting with %d camera(s): %s", len(sources), list(sources.keys()))

    # ---- Compute derived settings ----
    batch_size = max(1, int(settings.batch_duration_s / settings.sample_interval_s))
    interval_ns = int(settings.sample_interval_s * 1e9)

    logger.info(
        "Frame sampling: every %.0fs | Batch: %d frames (%.0fs window)",
        settings.sample_interval_s,
        batch_size,
        settings.batch_duration_s,
    )

    # ---- GPU scheduling ----
    scheduler = GPUScheduler(num_gpus=settings.num_gpus)
    camera_ids = list(sources.keys())
    gpu_map = scheduler.get_assignment_map(camera_ids)

    for gpu_id, cams in gpu_map.items():
        if cams:
            logger.info("GPU %d: %s", gpu_id, cams)

    # ---- Shared resources ----
    summarizer = VLMSummarizer(
        base_url=settings.vlm_base_url,
        model=settings.vlm_model,
        max_tokens=settings.vlm_max_tokens,
        temperature=settings.vlm_temperature,
        timeout_s=settings.vlm_timeout_s,
    )
    kafka_out = KafkaOutput(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        topic=settings.kafka_topic,
    )

    # ---- Build extractors per GPU and collect threads ----
    extractors: list[MediaExtractor] = []
    threads: list[threading.Thread] = []

    for gpu_id, gpu_cameras in gpu_map.items():
        if not gpu_cameras:
            continue

        # Pin this extractor to its GPU
        os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

        chunks = []
        for cam_id in gpu_cameras:
            chunk = MediaChunk(
                source=sources[cam_id],
                interval=interval_ns,
            )
            chunks.append(chunk)

        extractor = MediaExtractor(
            chunks=chunks,
            scaling=(settings.frame_width, settings.frame_height),
            n_thread=settings.extractor_threads,
            q_size=10,
        )
        extractors.append(extractor)

        # Start extraction — returns one queue per chunk
        queues = extractor()

        # Spawn a processing thread for each camera on this GPU
        for cam_id, queue in zip(gpu_cameras, queues):
            batcher = FrameBatcher(camera_id=cam_id, batch_size=batch_size)
            t = threading.Thread(
                target=_process_stream,
                name=f"rtvi-{cam_id}",
                args=(cam_id, queue, batcher, summarizer, kafka_out),
                daemon=True,
            )
            t.start()
            threads.append(t)

    logger.info("All %d stream thread(s) started", len(threads))

    # ---- Wait for shutdown ----
    def _handle_signal(signum, _frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — shutting down", sig_name)
        _shutdown.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        _shutdown.wait()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — shutting down")
        _shutdown.set()

    # ---- Cleanup ----
    logger.info("Waiting for stream threads to finish ...")
    for t in threads:
        t.join(timeout=10)

    summarizer.close()
    kafka_out.close()
    logger.info("RTVI shutdown complete")


if __name__ == "__main__":
    main()
