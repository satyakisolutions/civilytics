# Phase 3: VLM + Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a VIOLATION event fires, send frames to Qwen2.5-VL-7B for confirmation, then dispatch alerts to Telegram and WebSocket with VLM analysis results.

**Architecture:** VLM Worker consumes from `wastewatch.vlm.requests` (triggered by VIOLATION FSM state), calls vLLM OpenAI-compatible API, validates response with Pydantic, publishes result to `wastewatch.vlm.results`. Alert Dispatcher consumes VLM results and fans out to Telegram bot + WebSocket. Redis handles throttling/dedup. Frontend alert panel shows VLM details.

**Tech Stack:** vLLM (Qwen2.5-VL-7B AWQ), httpx (OpenAI-compatible client), python-telegram-bot, Redis (throttling), Pydantic (response validation)

**Builds on:** Phase 2 (`phase2/tracking-roi-engine`) — Event FSM, tracking, ROI

---

## File Structure

### New: VLM Worker (`/backend/app/vlm/`)

```
/backend/app/vlm/
├── __init__.py
├── client.py              # vLLM OpenAI-compatible HTTP client
├── prompts.py             # System prompt template + context enrichment
├── schemas.py             # VLM response Pydantic model + validation
└── worker.py              # Kafka consumer: vlm.requests → inference → vlm.results
```

### New: Alert System (`/backend/app/alerts/`)

```
/backend/app/alerts/
├── __init__.py
├── dispatcher.py          # Kafka consumer: vlm.results → fan-out to channels
├── telegram.py            # Telegram Bot API: send annotated snapshot + text
├── websocket.py           # Push alert to /ws/alerts subscribers
└── throttle.py            # Redis-based per-camera/per-track throttling
```

### New/Modified: Backend

```
/backend/
├── app/
│   ├── db/models.py           # MODIFIED: add VLMAnalysis, AlertDelivery models
│   ├── api/routes/alerts.py   # NEW: GET /api/alerts, PATCH acknowledge
│   ├── ws/alerts.py           # NEW: WebSocket /ws/alerts
│   ├── schemas/alert.py       # NEW: Alert schemas
│   ├── schemas/vlm.py         # NEW: VLM request/response schemas
│   ├── config/settings.py     # MODIFIED: add VLM + Telegram settings
│   └── main.py                # MODIFIED: register new routes + workers
├── alembic/versions/
│   └── 003_vlm_alerts.py      # NEW: vlm_analyses, alert_deliveries tables
└── tests/
    ├── test_vlm_schemas.py    # NEW: VLM response validation tests
    └── test_throttle.py       # NEW: Throttle logic tests
```

### Modified: Docker Compose

```
/docker-compose.yml            # MODIFIED: add vLLM service
```

### Modified: Pipeline

```
/pipeline/app/event_fsm.py    # MODIFIED: publish vlm.requests on VIOLATION
/pipeline/app/pipeline.py     # MODIFIED: capture frames for VLM on violation
```

---

## Task 1: VLM Response Schema + Prompt Templates

**Files:**
- Create: `/backend/app/vlm/__init__.py`
- Create: `/backend/app/vlm/schemas.py`
- Create: `/backend/app/vlm/prompts.py`
- Create: `/backend/tests/test_vlm_schemas.py`

- [ ] **Step 1: Create VLM response Pydantic model**

```python
# backend/app/vlm/schemas.py
from pydantic import BaseModel, Field
from enum import Enum


class WasteType(str, Enum):
    plastic_bag = "plastic_bag"
    construction_debris = "construction_debris"
    household_waste = "household_waste"
    organic = "organic"
    unknown = "unknown"
    none = "none"


class VLMSeverity(str, Enum):
    critical = "critical"
    warning = "warning"
    info = "info"


class VLMResponse(BaseModel):
    person_description: str
    action: str
    is_dumping: bool
    waste_type: WasteType
    severity: VLMSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    command: str


class VLMRequest(BaseModel):
    camera_id: str
    track_id: int
    track_session_id: str | None = None
    zone_name: str
    dwell_time_s: float
    class_name: str
    path_summary: str
    frame_timestamps: list[float]
    # frames are passed as base64 in a separate field or via reference
    frames_base64: list[str] | None = None


class VLMResult(BaseModel):
    camera_id: str
    track_id: int
    track_session_id: str | None = None
    response: VLMResponse
    inference_time_ms: int
    model_version: str
    frames_analyzed: int


def parse_vlm_response(raw_text: str) -> VLMResponse | None:
    """Parse VLM raw text output into validated VLMResponse.
    
    Returns None if parsing fails after cleanup attempts.
    """
    import json
    import re

    # Try to extract JSON from markdown code blocks or raw text
    text = raw_text.strip()

    # Remove markdown code fences
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
        return VLMResponse.model_validate(data)
    except (json.JSONDecodeError, Exception):
        return None
```

- [ ] **Step 2: Create prompt templates**

```python
# backend/app/vlm/prompts.py

SYSTEM_PROMPT = """You are a waste dumping surveillance analyst. Analyze the provided frames from camera {camera_id} in {zone_name}.

A {class_name} (Track ID: {track_id}) has been in the monitored zone for {dwell_time}s. Their movement path: {path_summary}.

Analyze the frames and respond ONLY with this JSON:
{{
  "person_description": "physical appearance, clothing, distinguishing features",
  "action": "specific action observed across frames",
  "is_dumping": true/false,
  "waste_type": "plastic_bag|construction_debris|household_waste|organic|unknown|none",
  "severity": "critical|warning|info",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of why this is/isn't dumping",
  "command": "warning message to speak aloud if violation confirmed"
}}

Rules:
- "critical": active dumping with clear evidence
- "warning": suspicious behavior, possible dumping preparation
- "info": person present but no violation indicators
- If unsure, set severity to "info" — false negatives are better than false alerts
- "command" should be in English, firm but polite, under 15 words"""


CONTEXT_TEMPLATE = """
Additional context:
- Time of day: {time_of_day}
- This location has had {recent_violations} violations in the last 24h"""


def build_prompt(
    camera_id: str,
    track_id: int,
    zone_name: str,
    dwell_time_s: float,
    class_name: str = "person",
    path_summary: str = "stationary",
    time_of_day: str | None = None,
    recent_violations: int = 0,
) -> str:
    prompt = SYSTEM_PROMPT.format(
        camera_id=camera_id,
        zone_name=zone_name,
        track_id=track_id,
        dwell_time=round(dwell_time_s, 1),
        class_name=class_name,
        path_summary=path_summary,
    )
    if time_of_day:
        prompt += CONTEXT_TEMPLATE.format(
            time_of_day=time_of_day,
            recent_violations=recent_violations,
        )
    return prompt
```

- [ ] **Step 3: Write tests**

```python
# backend/tests/test_vlm_schemas.py
from app.vlm.schemas import parse_vlm_response, VLMResponse, VLMSeverity, WasteType
from app.vlm.prompts import build_prompt


def test_parse_valid_json():
    raw = '{"person_description": "Male, blue shirt", "action": "dropping bag", "is_dumping": true, "waste_type": "plastic_bag", "severity": "critical", "confidence": 0.9, "reasoning": "clearly dropping waste", "command": "Stop. You are being recorded."}'
    result = parse_vlm_response(raw)
    assert result is not None
    assert result.is_dumping is True
    assert result.severity == VLMSeverity.critical


def test_parse_json_in_markdown():
    raw = '```json\n{"person_description": "Woman", "action": "walking", "is_dumping": false, "waste_type": "none", "severity": "info", "confidence": 0.2, "reasoning": "just passing", "command": ""}\n```'
    result = parse_vlm_response(raw)
    assert result is not None
    assert result.is_dumping is False


def test_parse_invalid_json():
    result = parse_vlm_response("not json at all")
    assert result is None


def test_parse_missing_fields():
    result = parse_vlm_response('{"person_description": "test"}')
    assert result is None


def test_confidence_bounds():
    raw = '{"person_description": "x", "action": "x", "is_dumping": false, "waste_type": "none", "severity": "info", "confidence": 1.5, "reasoning": "x", "command": "x"}'
    result = parse_vlm_response(raw)
    assert result is None  # confidence > 1.0 fails validation


def test_build_prompt():
    prompt = build_prompt("cam-01", 42, "Zone A North", 25.3)
    assert "cam-01" in prompt
    assert "Track ID: 42" in prompt
    assert "25.3s" in prompt
```

- [ ] **Step 4: Run tests**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && .venv/bin/python -m pytest tests/test_vlm_schemas.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/app/vlm/ backend/tests/test_vlm_schemas.py
git commit -m "feat(backend): VLM response schema, prompt templates, and validation with tests"
```

---

## Task 2: VLM Client (vLLM OpenAI-compatible)

**Files:**
- Create: `/backend/app/vlm/client.py`
- Modify: `/backend/app/config/settings.py`

- [ ] **Step 1: Add VLM settings**

Add to Settings in settings.py:
```python
    # VLM (vLLM)
    vlm_base_url: str = "http://localhost:8000/v1"
    vlm_model: str = "Qwen/Qwen2.5-VL-7B-Instruct-AWQ"
    vlm_max_tokens: int = 512
    vlm_temperature: float = 0.1
    vlm_timeout_s: int = 30
```

- [ ] **Step 2: Create VLM client**

```python
# backend/app/vlm/client.py
"""OpenAI-compatible client for vLLM (Qwen2.5-VL-7B)."""
import base64
import logging
import time

import httpx

from app.config.settings import settings
from app.vlm.prompts import build_prompt
from app.vlm.schemas import VLMRequest, VLMResponse, VLMResult, parse_vlm_response

logger = logging.getLogger(__name__)


class VLMClient:
    def __init__(self):
        self._base_url = settings.vlm_base_url
        self._model = settings.vlm_model
        self._client = httpx.AsyncClient(timeout=settings.vlm_timeout_s)

    async def analyze(self, request: VLMRequest) -> VLMResult | None:
        """Send frames to VLM and return validated result."""
        prompt_text = build_prompt(
            camera_id=request.camera_id,
            track_id=request.track_id,
            zone_name=request.zone_name,
            dwell_time_s=request.dwell_time_s,
            class_name=request.class_name,
            path_summary=request.path_summary,
        )

        # Build messages with image content
        content = [{"type": "text", "text": prompt_text}]
        if request.frames_base64:
            for frame_b64 in request.frames_base64[:4]:  # max 4 frames
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
                })

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": settings.vlm_max_tokens,
            "temperature": settings.vlm_temperature,
        }

        start = time.monotonic()
        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
        except Exception:
            logger.exception("VLM request failed for camera=%s track=%d", request.camera_id, request.track_id)
            return None

        elapsed_ms = int((time.monotonic() - start) * 1000)
        data = resp.json()
        raw_text = data["choices"][0]["message"]["content"]

        vlm_response = parse_vlm_response(raw_text)
        if vlm_response is None:
            # Retry once with stricter prompt
            logger.warning("VLM response parse failed, retrying: %s", raw_text[:200])
            vlm_response = await self._retry_parse(prompt_text, request)
            if vlm_response is None:
                # Fallback to warning
                vlm_response = VLMResponse(
                    person_description="Unable to analyze",
                    action="unknown",
                    is_dumping=False,
                    waste_type="unknown",
                    severity="warning",
                    confidence=0.0,
                    reasoning="VLM response could not be parsed",
                    command="",
                )

        return VLMResult(
            camera_id=request.camera_id,
            track_id=request.track_id,
            track_session_id=request.track_session_id,
            response=vlm_response,
            inference_time_ms=elapsed_ms,
            model_version=self._model,
            frames_analyzed=len(request.frames_base64 or []),
        )

    async def _retry_parse(self, original_prompt: str, request: VLMRequest) -> VLMResponse | None:
        """Retry with explicit JSON instruction."""
        content = [{"type": "text", "text": original_prompt + "\n\nIMPORTANT: Respond with ONLY valid JSON, no markdown formatting."}]
        if request.frames_base64:
            for frame_b64 in request.frames_base64[:4]:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
                })

        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": content}],
                    "max_tokens": settings.vlm_max_tokens,
                    "temperature": 0.0,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
            return parse_vlm_response(raw)
        except Exception:
            logger.exception("VLM retry also failed")
            return None

    async def close(self):
        await self._client.aclose()
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/vlm/client.py backend/app/config/settings.py
git commit -m "feat(backend): VLM client for vLLM OpenAI-compatible API with retry logic"
```

---

## Task 3: VLM Worker (Kafka Consumer)

**Files:**
- Create: `/backend/app/vlm/worker.py`

- [ ] **Step 1: Create VLM worker**

```python
# backend/app/vlm/worker.py
"""Kafka consumer: wastewatch.vlm.requests → VLM inference → wastewatch.vlm.results"""
import asyncio
import logging

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from app.config.kafka import TOPIC_VLM_REQUESTS, TOPIC_VLM_RESULTS, GROUP_VLM_WORKER
from app.vlm.client import VLMClient
from app.vlm.schemas import VLMRequest

logger = logging.getLogger(__name__)


class VLMWorker:
    def __init__(self, bootstrap_servers: str, max_concurrent: int = 2):
        self._bootstrap_servers = bootstrap_servers
        self._max_concurrent = max_concurrent
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._client: VLMClient | None = None
        self._task: asyncio.Task | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def start(self):
        self._client = VLMClient()
        self._consumer = AIOKafkaConsumer(
            TOPIC_VLM_REQUESTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_VLM_WORKER,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda v: orjson.dumps(v),
        )
        await self._consumer.start()
        await self._producer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("VLMWorker started (max_concurrent=%d)", self._max_concurrent)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        if self._producer:
            await self._producer.stop()
        if self._client:
            await self._client.close()
        logger.info("VLMWorker stopped")

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                asyncio.create_task(self._process(msg.value))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("VLMWorker consume error")

    async def _process(self, data: dict):
        async with self._semaphore:
            try:
                request = VLMRequest.model_validate(data)
                result = await self._client.analyze(request)
                if result:
                    await self._producer.send_and_wait(
                        topic=TOPIC_VLM_RESULTS,
                        key=result.camera_id.encode("utf-8"),
                        value=result.model_dump(),
                    )
                    logger.info(
                        "VLM result: camera=%s track=%d severity=%s dumping=%s (%dms)",
                        result.camera_id, result.track_id,
                        result.response.severity.value, result.response.is_dumping,
                        result.inference_time_ms,
                    )
            except Exception:
                logger.exception("VLM processing error")
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/vlm/worker.py
git commit -m "feat(backend): VLM Kafka worker consuming vlm.requests and publishing vlm.results"
```

---

## Task 4: Alert Throttle (Redis)

**Files:**
- Create: `/backend/app/alerts/__init__.py`
- Create: `/backend/app/alerts/throttle.py`
- Create: `/backend/tests/test_throttle.py`

- [ ] **Step 1: Create throttle module**

```python
# backend/app/alerts/throttle.py
"""Redis-based alert throttling and deduplication."""
import logging
import time

import redis.asyncio as redis

from app.config.settings import settings

logger = logging.getLogger(__name__)


class AlertThrottle:
    def __init__(self, redis_url: str | None = None):
        self._redis = redis.from_url(redis_url or settings.redis_url)

    async def should_alert(
        self,
        camera_id: str,
        track_id: int | None = None,
        cooldown_s: int = 60,
        max_per_hour: int = 10,
    ) -> bool:
        """Check if an alert should be sent based on throttling rules."""
        now = int(time.time())

        # Per-camera cooldown
        camera_key = f"throttle:camera:{camera_id}"
        last_alert = await self._redis.get(camera_key)
        if last_alert and (now - int(last_alert)) < cooldown_s:
            logger.debug("Throttled: camera %s cooldown (%ds remaining)", camera_id, cooldown_s - (now - int(last_alert)))
            return False

        # Per-camera hourly limit
        hourly_key = f"throttle:hourly:{camera_id}"
        count = await self._redis.get(hourly_key)
        if count and int(count) >= max_per_hour:
            logger.warning("Throttled: camera %s exceeded %d alerts/hour", camera_id, max_per_hour)
            return False

        return True

    async def record_alert(self, camera_id: str, cooldown_s: int = 60):
        """Record that an alert was sent for throttle tracking."""
        now = int(time.time())

        # Set camera cooldown
        camera_key = f"throttle:camera:{camera_id}"
        await self._redis.set(camera_key, str(now), ex=cooldown_s)

        # Increment hourly counter
        hourly_key = f"throttle:hourly:{camera_id}"
        pipe = self._redis.pipeline()
        pipe.incr(hourly_key)
        pipe.expire(hourly_key, 3600)
        await pipe.execute()

    async def close(self):
        await self._redis.aclose()
```

- [ ] **Step 2: Write tests (with mock Redis)**

```python
# backend/tests/test_throttle.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_should_alert_no_prior():
    with patch("app.alerts.throttle.redis") as mock_redis_mod:
        mock_client = AsyncMock()
        mock_client.get.return_value = None
        mock_redis_mod.from_url.return_value = mock_client

        from app.alerts.throttle import AlertThrottle
        throttle = AlertThrottle(redis_url="redis://localhost:6379/0")
        throttle._redis = mock_client

        result = await throttle.should_alert("cam-01", cooldown_s=60)
        assert result is True


@pytest.mark.asyncio
async def test_should_alert_within_cooldown():
    import time
    with patch("app.alerts.throttle.redis") as mock_redis_mod:
        mock_client = AsyncMock()
        mock_client.get.return_value = str(int(time.time()) - 10)  # 10s ago
        mock_redis_mod.from_url.return_value = mock_client

        from app.alerts.throttle import AlertThrottle
        throttle = AlertThrottle(redis_url="redis://localhost:6379/0")
        throttle._redis = mock_client

        result = await throttle.should_alert("cam-01", cooldown_s=60)
        assert result is False  # within 60s cooldown
```

- [ ] **Step 3: Run tests and commit**

```bash
cd /home/subhadra/Projects/LSG-Wastewatch/backend && .venv/bin/python -m pytest tests/test_throttle.py tests/test_vlm_schemas.py -v
git add backend/app/alerts/ backend/tests/test_throttle.py
git commit -m "feat(backend): Redis-based alert throttling with per-camera cooldown and hourly limits"
```

---

## Task 5: Telegram Alert Channel

**Files:**
- Create: `/backend/app/alerts/telegram.py`
- Modify: `/backend/app/config/settings.py`

- [ ] **Step 1: Add Telegram settings**

Add to Settings:
```python
    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_ids: list[str] = []  # comma-separated in env
```

- [ ] **Step 2: Create Telegram alert sender**

```python
# backend/app/alerts/telegram.py
"""Telegram bot alert channel."""
import logging

import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramAlert:
    def __init__(self):
        self._token = settings.telegram_bot_token
        self._chat_ids = settings.telegram_chat_ids
        self._client = httpx.AsyncClient(timeout=15)

    async def send_alert(
        self,
        camera_id: str,
        severity: str,
        message: str,
        snapshot_url: str | None = None,
        dwell_time_s: float = 0,
        vlm_summary: str = "",
        plate_number: str | None = None,
    ) -> list[dict]:
        """Send alert to all configured Telegram chat IDs."""
        if not self._token or not self._chat_ids:
            logger.warning("Telegram not configured, skipping alert")
            return []

        text = self._format_message(camera_id, severity, message, dwell_time_s, vlm_summary, plate_number)
        results = []

        for chat_id in self._chat_ids:
            try:
                if snapshot_url:
                    result = await self._send_photo(chat_id, snapshot_url, text)
                else:
                    result = await self._send_message(chat_id, text)
                results.append({"chat_id": chat_id, "status": "sent", "message_id": result.get("message_id")})
            except Exception as e:
                logger.exception("Failed to send Telegram alert to %s", chat_id)
                results.append({"chat_id": chat_id, "status": "failed", "error": str(e)})

        return results

    async def _send_message(self, chat_id: str, text: str) -> dict:
        url = f"{TELEGRAM_API.format(token=self._token)}/sendMessage"
        resp = await self._client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        })
        resp.raise_for_status()
        return resp.json().get("result", {})

    async def _send_photo(self, chat_id: str, photo_url: str, caption: str) -> dict:
        url = f"{TELEGRAM_API.format(token=self._token)}/sendPhoto"
        resp = await self._client.post(url, json={
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption[:1024],  # Telegram caption limit
            "parse_mode": "HTML",
        })
        resp.raise_for_status()
        return resp.json().get("result", {})

    def _format_message(
        self, camera_id: str, severity: str, message: str,
        dwell_time_s: float, vlm_summary: str, plate_number: str | None,
    ) -> str:
        icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(severity, "📢")
        lines = [
            f"{icon} <b>WasteWatch Alert — {severity.upper()}</b>",
            f"📷 Camera: <code>{camera_id}</code>",
            f"⏱ Dwell: {dwell_time_s:.1f}s",
            f"\n{message}",
        ]
        if vlm_summary:
            lines.append(f"\n🤖 AI Analysis: {vlm_summary}")
        if plate_number:
            lines.append(f"🚗 Plate: <code>{plate_number}</code>")
        return "\n".join(lines)

    async def close(self):
        await self._client.aclose()
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/alerts/telegram.py backend/app/config/settings.py
git commit -m "feat(backend): Telegram alert channel with photo + formatted message support"
```

---

## Task 6: Alert Dispatcher (Kafka Fan-out)

**Files:**
- Create: `/backend/app/alerts/dispatcher.py`
- Create: `/backend/app/alerts/websocket.py`
- Create: `/backend/app/ws/alerts.py`

- [ ] **Step 1: Create alert WebSocket endpoint**

```python
# backend/app/ws/alerts.py
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()

_subscribers: set[WebSocket] = set()


async def broadcast_alert(data: dict):
    dead = []
    for ws in _subscribers:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _subscribers.discard(ws)


@router.websocket("/alerts")
async def ws_alerts(websocket: WebSocket):
    await websocket.accept()
    _subscribers.add(websocket)
    logger.info("Alerts WebSocket connected, total=%d", len(_subscribers))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers.discard(websocket)
```

- [ ] **Step 2: Create WebSocket alert pusher**

```python
# backend/app/alerts/websocket.py
"""Push alerts to WebSocket subscribers."""
import time
from app.ws.alerts import broadcast_alert


async def push_alert_ws(
    alert_id: str,
    camera_id: str,
    severity: str,
    title: str,
    message: str,
    vlm_response: dict | None = None,
    dwell_time_s: float = 0,
    snapshot_url: str | None = None,
    plate_number: str | None = None,
):
    await broadcast_alert({
        "type": "alert",
        "alert_id": alert_id,
        "camera_id": camera_id,
        "severity": severity,
        "title": title,
        "message": message,
        "snapshot_url": snapshot_url,
        "vlm_response": vlm_response,
        "plate_number": plate_number,
        "dwell_time_s": dwell_time_s,
        "timestamp": time.time(),
    })
```

- [ ] **Step 3: Create alert dispatcher**

```python
# backend/app/alerts/dispatcher.py
"""Kafka consumer: wastewatch.vlm.results → fan-out to alert channels."""
import asyncio
import logging
import uuid

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import TOPIC_VLM_RESULTS, GROUP_ALERT_DISPATCHER
from app.alerts.throttle import AlertThrottle
from app.alerts.telegram import TelegramAlert
from app.alerts.websocket import push_alert_ws

logger = logging.getLogger(__name__)


class AlertDispatcher:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._throttle = AlertThrottle()
        self._telegram = TelegramAlert()

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_VLM_RESULTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_ALERT_DISPATCHER,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("AlertDispatcher started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        await self._throttle.close()
        await self._telegram.close()

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                await self._dispatch(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("AlertDispatcher error")

    async def _dispatch(self, data: dict):
        response = data.get("response", {})
        camera_id = data.get("camera_id", "unknown")
        severity = response.get("severity", "info")

        # Only alert on warning or critical
        if severity == "info" and not response.get("is_dumping", False):
            return

        # Throttle check
        should_send = await self._throttle.should_alert(camera_id)
        if not should_send:
            logger.info("Alert throttled for camera %s", camera_id)
            return

        alert_id = str(uuid.uuid4())
        title = "Dumping Detected" if response.get("is_dumping") else "Suspicious Activity"
        message = response.get("reasoning", "Activity detected in monitored zone")

        # Fan-out to all channels
        await asyncio.gather(
            self._send_telegram(camera_id, severity, message, data),
            push_alert_ws(
                alert_id=alert_id,
                camera_id=camera_id,
                severity=severity,
                title=title,
                message=message,
                vlm_response=response,
                dwell_time_s=data.get("dwell_time_s", 0),
            ),
            return_exceptions=True,
        )

        await self._throttle.record_alert(camera_id)
        logger.info("Alert dispatched: camera=%s severity=%s title=%s", camera_id, severity, title)

    async def _send_telegram(self, camera_id: str, severity: str, message: str, data: dict):
        response = data.get("response", {})
        try:
            await self._telegram.send_alert(
                camera_id=camera_id,
                severity=severity,
                message=message,
                dwell_time_s=data.get("dwell_time_s", 0),
                vlm_summary=f"{response.get('action', '')} — {response.get('reasoning', '')}",
            )
        except Exception:
            logger.exception("Telegram alert failed for camera %s", camera_id)
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/alerts/dispatcher.py backend/app/alerts/websocket.py backend/app/ws/alerts.py
git commit -m "feat(backend): alert dispatcher with Telegram + WebSocket fan-out and throttling"
```

---

## Task 7: Database Migration — vlm_analyses + alert_deliveries

**Files:**
- Modify: `/backend/app/db/models.py`
- Create: `/backend/alembic/versions/003_vlm_alerts.py`

- [ ] **Step 1: Add VLMAnalysis and AlertDelivery models**

```python
class VLMAnalysis(Base):
    __tablename__ = "vlm_analyses"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    track_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("track_sessions.id"))
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    frames_analyzed: Mapped[int] = mapped_column(Integer)
    prompt_used: Mapped[str | None] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSONB)
    inference_time_ms: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id"))
    channel: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="pending")
    payload: Mapped[dict | None] = mapped_column(JSONB)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: Create migration 003**

Creates vlm_analyses and alert_deliveries tables.

- [ ] **Step 3: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions/003_vlm_alerts.py
git commit -m "feat(backend): vlm_analyses and alert_deliveries tables"
```

---

## Task 8: Alerts API Route

**Files:**
- Create: `/backend/app/schemas/alert.py`
- Create: `/backend/app/api/routes/alerts.py`
- Modify: `/backend/app/main.py`

- [ ] **Step 1: Create alert schemas**

```python
# backend/app/schemas/alert.py
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.db.models import AlertSeverity, AlertStatus


class AlertResponse(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID | None
    camera_id: uuid.UUID | None
    severity: AlertSeverity
    title: str
    message: str | None
    zone: str | None
    channel: str | None
    tts_audio_url: str | None
    status: AlertStatus
    timestamp: datetime
    created_at: datetime
    model_config = {"from_attributes": True}


class AlertAcknowledge(BaseModel):
    status: AlertStatus = AlertStatus.acknowledged
```

- [ ] **Step 2: Create alerts route**

```python
# backend/app/api/routes/alerts.py
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import Alert
from app.schemas.alert import AlertResponse, AlertAcknowledge

router = APIRouter(prefix="/alerts", tags=["alerts"])

@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    camera_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(Alert).order_by(desc(Alert.timestamp)).limit(limit)
    if camera_id:
        query = query.where(Alert.camera_id == camera_id)
    if status:
        query = query.where(Alert.status == status)
    result = await db.execute(query)
    return result.scalars().all()

@router.patch("/{alert_id}", response_model=AlertResponse)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    body: AlertAcknowledge,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = body.status
    await db.commit()
    await db.refresh(alert)
    return alert
```

- [ ] **Step 3: Register in main.py**

Add alerts router, ws/alerts router, VLMWorker, AlertDispatcher to main.py.

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/alert.py backend/app/api/routes/alerts.py backend/app/ws/alerts.py backend/app/main.py
git commit -m "feat(backend): alerts REST API, alerts WebSocket, VLM worker and alert dispatcher in lifespan"
```

---

## Task 9: vLLM Docker Service

**Files:**
- Modify: `/docker-compose.yml`

- [ ] **Step 1: Add vLLM service**

```yaml
  vlm:
    image: vllm/vllm-openai:latest
    command: >
      --model Qwen/Qwen2.5-VL-7B-Instruct-AWQ
      --quantization awq
      --max-model-len 4096
      --gpu-memory-utilization 0.6
      --port 8000
    ports:
      - "8000:8000"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
```

Add `WW_VLM_BASE_URL: http://vlm:8000/v1` to api service environment.

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add vLLM service (Qwen2.5-VL-7B AWQ) to Docker Compose"
```

---

## Task 10: Pipeline — Trigger VLM on VIOLATION

**Files:**
- Modify: `/pipeline/app/pipeline.py`
- Modify: `/pipeline/app/kafka_producer.py`

- [ ] **Step 1: Add send_vlm_request to Kafka producer**

```python
def send_vlm_request(self, camera_id: str, track_id: int, zone_name: str,
                     dwell_time_s: float, class_name: str, path_summary: str):
    """Publish a VLM analysis request when VIOLATION state is reached."""
    try:
        self._producer.produce(
            topic="wastewatch.vlm.requests",
            key=camera_id.encode("utf-8"),
            value=orjson.dumps({
                "camera_id": camera_id,
                "track_id": track_id,
                "zone_name": zone_name,
                "dwell_time_s": round(dwell_time_s, 1),
                "class_name": class_name,
                "path_summary": path_summary,
                "frame_timestamps": [],
                "frames_base64": [],
            }),
        )
    except Exception:
        logger.exception("Failed to send VLM request")
```

- [ ] **Step 2: In pipeline.py, trigger VLM request on VIOLATION transitions**

In the extract_metadata function, after `fsm_event = fsm_manager.update(...)`:

```python
if fsm_event and fsm_event.state.value == "violation":
    kafka_producer.send_vlm_request(
        camera_id=camera_id,
        track_id=track_id,
        zone_name=camera_id,  # use camera_id as zone until zone lookup
        dwell_time_s=track_info.dwell_time_s,
        class_name=class_name,
        path_summary="stationary" if track_info.dwell_time_s > 10 else "moving",
    )
```

- [ ] **Step 3: Commit**

```bash
git add pipeline/app/pipeline.py pipeline/app/kafka_producer.py
git commit -m "feat(pipeline): trigger VLM request on VIOLATION state transition"
```

---

## Summary

| Task | Component | Deliverable |
|------|-----------|-------------|
| 1 | Backend | VLM response schema, prompt templates, validation + tests |
| 2 | Backend | VLM client for vLLM OpenAI-compatible API |
| 3 | Backend | VLM Kafka worker (vlm.requests → vlm.results) |
| 4 | Backend | Redis-based alert throttling + tests |
| 5 | Backend | Telegram alert channel |
| 6 | Backend | Alert dispatcher with Telegram + WebSocket fan-out |
| 7 | Backend | vlm_analyses + alert_deliveries DB tables |
| 8 | Backend | Alerts REST API + WebSocket + workers in lifespan |
| 9 | Infra | vLLM Docker service |
| 10 | Pipeline | Trigger VLM request on VIOLATION FSM state |

**Phase 3 Deliverable:** VIOLATION → VLM confirms dumping → Alert dispatched to Telegram + WebSocket with VLM analysis details, throttled per-camera.
