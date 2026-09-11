# WasteWatch RTVI -- Real-Time Video Intelligence

**Satyaki Solutions Pvt Ltd** | https://satyaki.co.in

VLM-powered video frame summarization using DeepStream 9.0 + Qwen2.5-VL-7B.

## Architecture

```
Per RTSP stream (independent, never mixed):

  RTSP Camera
      |
      v
  MediaExtractor (pyservicemaker)
  MediaChunk(source=rtsp_url, interval=10s)
      |
      v  VideoFrame (1 frame every 10 seconds)
      |
  FrameBatcher (collects 6 frames over 60s window)
      |
      v  Batch of 6 frames (single stream only)
      |
  VLM Client (Qwen2.5-VL-7B via vLLM)
  "Summarize what's happening in these frames"
      |
      v  Text summary
      |
  Kafka Producer -> wastewatch.vlm.summaries
```

Each camera has its own MediaChunk, FrameBatcher, and processing thread.
Frames are **never** mixed across streams.

## Prerequisites

- NVIDIA DeepStream SDK 9.0
- Python 3.12+ with pyservicemaker
- vLLM running Qwen2.5-VL-7B-Instruct-AWQ
- Apache Kafka
- PyTorch, Pillow, httpx, confluent-kafka, orjson, pydantic-settings

## Setup

### 1. Kafka Server

Using Docker Compose (recommended -- see project root `docker-compose.yml`):

```bash
docker compose up -d kafka
```

Or standalone:

```bash
docker run -d --name kafka \
  -p 9092:9092 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
  apache/kafka:3.7.0
```

Create the topic:

```bash
kafka-topics.sh --create --topic wastewatch.vlm.summaries \
  --bootstrap-server localhost:9092 \
  --partitions 4 --replication-factor 1
```

### 2. vLLM

Start vLLM with the AWQ-quantized model:

```bash
vllm serve Qwen/Qwen2.5-VL-7B-Instruct-AWQ \
  --host 0.0.0.0 --port 8000 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --dtype half
```

### 3. Install Dependencies

```bash
cd pipeline
python3 -m venv venv
source venv/bin/activate
pip install /opt/nvidia/deepstream/deepstream/service-maker/python/pyservicemaker*.whl pyyaml
pip install torch pillow httpx confluent-kafka orjson pydantic-settings
```

### 4. Run

```bash
export RTVI_SOURCES="cam-01=rtsp://192.168.1.10:554/stream,cam-02=rtsp://192.168.1.11:554/stream"
python3 -m rtvi_app
```

## Configuration

All settings are controlled via `RTVI_` prefixed environment variables:

| Variable | Default | Description |
|---|---|---|
| `RTVI_SOURCES` | `""` | Comma-separated `cam-id=rtsp://...` pairs |
| `RTVI_SAMPLE_INTERVAL_S` | `10.0` | Seconds between sampled frames |
| `RTVI_BATCH_DURATION_S` | `60.0` | Seconds of video per VLM batch |
| `RTVI_FRAME_WIDTH` | `1280` | Scaled frame width |
| `RTVI_FRAME_HEIGHT` | `720` | Scaled frame height |
| `RTVI_VLM_BASE_URL` | `http://localhost:8000/v1` | vLLM endpoint |
| `RTVI_VLM_MODEL` | `Qwen/Qwen2.5-VL-7B-Instruct-AWQ` | Model name |
| `RTVI_VLM_MAX_TOKENS` | `512` | Max response tokens |
| `RTVI_VLM_TEMPERATURE` | `0.3` | Sampling temperature |
| `RTVI_VLM_TIMEOUT_S` | `60` | HTTP request timeout |
| `RTVI_KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Kafka broker address |
| `RTVI_KAFKA_TOPIC` | `wastewatch.vlm.summaries` | Output Kafka topic |
| `RTVI_NUM_GPUS` | `1` | Number of GPUs (0 = auto-detect) |
| `RTVI_EXTRACTOR_THREADS` | `4` | MediaExtractor worker threads |
| `RTVI_VLM_WORKERS` | `2` | Concurrent VLM request threads |

## Multi-GPU Scaling

When multiple GPUs are available, the GPUScheduler distributes cameras
across GPUs using round-robin assignment:

- **2 cameras, 2 GPUs**: 1 camera per GPU
- **8 cameras, 2 GPUs**: 4 cameras per GPU
- **12 cameras, 4 GPUs**: 3 cameras per GPU

Each GPU gets its own MediaExtractor instance pinned via
`CUDA_VISIBLE_DEVICES`. All processing threads for cameras on the same
GPU share a single extractor.

Set `RTVI_NUM_GPUS=0` to auto-detect GPU count via `nvidia-smi`.

## Kafka Output Schema

Messages on `wastewatch.vlm.summaries` are keyed by `camera_id` and
contain:

```json
{
  "camera_id": "cam-01",
  "activity_summary": "Two people walking near dumpster area",
  "people_count": 2,
  "vehicle_count": 0,
  "vehicle_types": [],
  "suspicious_activity": false,
  "waste_detected": false,
  "waste_description": "",
  "environment": "daytime, residential area",
  "risk_level": "none",
  "recommended_action": "no action",
  "timestamps_ns": [1700000000000, 1700010000000, ...],
  "inference_time_ms": 1823.4,
  "frames_analyzed": 6,
  "published_at": 1700000060.123
}
```
