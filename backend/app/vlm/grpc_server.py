"""gRPC VLMService — replaces Kafka `wastewatch.vlm.requests` as the trigger
path. Results still go out on Kafka `wastewatch.vlm.results` so downstream
consumers (alert_consumer) are unchanged.

Reuses VLMClient.analyze + _crop_to_bbox helpers from worker.py.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid

import grpc
import orjson
from aiokafka import AIOKafkaProducer

from app.config.kafka import TOPIC_VLM_RESULTS
from app.config.settings import settings
from app.vlm.client import VLMClient
from app.vlm.schemas import VLMRequest
from app.vlm.worker import VLMWorker
from app.vlm.grpc_gen import vlm_pb2, vlm_pb2_grpc

logger = logging.getLogger(__name__)


class VLMServicer(vlm_pb2_grpc.VLMServiceServicer):
    def __init__(self, max_concurrent: int = 2):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._inflight = 0
        self._client: VLMClient | None = None
        self._producer: AIOKafkaProducer | None = None
        # One long-lived worker for its frame-grabber helpers. Reused across
        # requests so its persistent ffmpeg grabbers are not leaked/respawned.
        self._grab_helper: VLMWorker | None = None

    async def start(self):
        self._client = VLMClient()
        self._grab_helper = VLMWorker(bootstrap_servers=settings.kafka_bootstrap_servers)
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: orjson.dumps(v),
        )
        await self._producer.start()
        logger.info("VLMServicer started — will publish to %s", TOPIC_VLM_RESULTS)

    async def stop(self):
        if self._client:
            await self._client.close()
        if self._producer:
            await self._producer.stop()
        if self._grab_helper:
            # terminates any persistent ffmpeg grabbers it spawned
            await self._grab_helper.stop()

    async def Analyze(self, request, context):
        self._inflight += 1
        t0 = time.monotonic()
        try:
            async with self._sem:
                # Build VLMRequest matching the existing pydantic schema
                bbox = None
                if request.trigger_bbox and (request.trigger_bbox.w > 0 or request.trigger_bbox.h > 0):
                    bbox = {
                        "x": request.trigger_bbox.x,
                        "y": request.trigger_bbox.y,
                        "w": request.trigger_bbox.w,
                        "h": request.trigger_bbox.h,
                    }
                frames_b64 = [base64.b64encode(b).decode("ascii") for b in request.frames_jpeg]
                vreq = VLMRequest(
                    camera_id=request.camera_id,
                    track_id=request.track_id,
                    track_session_id=request.track_session_id or f"{request.camera_id}_{request.track_id}",
                    zone_name=request.zone_name or request.camera_id,
                    dwell_time_s=request.dwell_time_s,
                    class_name=request.class_name or "unknown",
                    path_summary=request.path_summary or "grpc",
                    trigger_bbox=bbox,
                    frames_base64=frames_b64,
                    frame_timestamps=list(request.frame_timestamps),
                )

                # If only a full frame was supplied + bbox present, crop
                if frames_b64 and bbox and len(frames_b64) == 1:
                    cropped = VLMWorker._crop_to_bbox(frames_b64[0], bbox)
                    vreq.frames_base64 = [cropped, frames_b64[0]]
                    vreq.frame_timestamps = [0.0, 0.0]

                # If no frames at all, fall back to persistent RTSP grabber
                if not vreq.frames_base64:
                    # Reuse the shared long-lived helper so its persistent
                    # ffmpeg grabbers are not leaked/respawned per request.
                    helper = self._grab_helper
                    frame = await asyncio.get_event_loop().run_in_executor(
                        None, helper._grab_rtsp_frame, vreq.camera_id
                    )
                    if frame:
                        if bbox:
                            cropped = helper._crop_to_bbox(frame, bbox)
                            vreq.frames_base64 = [cropped, frame]
                            vreq.frame_timestamps = [0.0, 0.0]
                        else:
                            vreq.frames_base64 = [frame]
                            vreq.frame_timestamps = [0.0]

                logger.info("Analyze start: cam=%s track=%d class=%s ps=%s",
                            vreq.camera_id, vreq.track_id, vreq.class_name, vreq.path_summary[:80])
                result = await self._client.analyze(vreq)
                if result is None:
                    logger.warning("Analyze returned None for cam=%s track=%d", vreq.camera_id, vreq.track_id)
                    context.set_code(grpc.StatusCode.INTERNAL)
                    context.set_details("VLM returned no result")
                    return vlm_pb2.AnalyzeResponse()
                logger.info("Analyze done: cam=%s track=%d is_dumping=%s waste=%s conf=%.2f t=%.0fms",
                            vreq.camera_id, vreq.track_id, result.response.is_dumping,
                            result.response.waste_type, result.response.confidence,
                            (time.monotonic() - t0) * 1000)

                # Publish to Kafka so alert_consumer sees it as before.
                # Inject track_context so downstream gating (speed/area) can
                # work without round-tripping back to the pipeline.
                payload = result.model_dump()
                payload["track_context"] = {
                    "path_summary": vreq.path_summary,
                    "dwell_time_s": vreq.dwell_time_s,
                    "class_name": vreq.class_name,
                }
                await self._producer.send_and_wait(
                    TOPIC_VLM_RESULTS, value=payload
                )

                resp = result.response
                return vlm_pb2.AnalyzeResponse(
                    camera_id=request.camera_id,
                    track_id=request.track_id,
                    track_session_id=vreq.track_session_id,
                    person_description=resp.person_description or "",
                    action=resp.action or "",
                    is_dumping=bool(resp.is_dumping),
                    waste_type=str(resp.waste_type) if resp.waste_type else "none",
                    severity=str(resp.severity) if resp.severity else "info",
                    confidence=float(resp.confidence or 0),
                    reasoning=resp.reasoning or "",
                    command=resp.command or "",
                    deterrent_message=resp.deterrent_message or "",
                    inference_time_ms=float((time.monotonic() - t0) * 1000),
                    model_version=settings.vlm_model or "qwen2.5-vl-7b-awq",
                    frames_analyzed=len(vreq.frames_base64),
                )
        except Exception as exc:
            logger.exception("Analyze failed")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))
            return vlm_pb2.AnalyzeResponse()
        finally:
            self._inflight -= 1

    async def Ping(self, request, context):
        return vlm_pb2.PingResponse(status="ok", inflight=self._inflight)


async def serve(port: int = 50061):
    servicer = VLMServicer()
    await servicer.start()
    server = grpc.aio.server(options=[
        ("grpc.max_receive_message_length", 16 * 1024 * 1024),
        ("grpc.max_send_message_length", 16 * 1024 * 1024),
    ])
    vlm_pb2_grpc.add_VLMServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    logger.info("gRPC VLMService listening on :%d", port)
    try:
        await server.wait_for_termination()
    finally:
        await servicer.stop()
