# Full System Build — Endpoints, Settings, Intelligence, Dispatch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 5 missing POST endpoints, wire settings frontend to config API, add ReID/VLM intelligence services, implement 3-tier alert dispatch, add CrewAI report composition, and channel onboarding flows.

**Architecture:** Workers already exist and call these endpoints — we just need the backend route handlers. Settings sections use the existing generic config API (`GET/PUT /api/config/{key}`). Intelligence services (ReID, VLM) are new microservice endpoints. Dispatch workers are new Conductor task handlers. CrewAI compose runs on CPU via BitNet.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL (JSONB), Kafka (aiokafka), Conductor OSS, CLIP ViT-B/16 ONNX, SmolVLM2-500M, BitNet b1.58, CrewAI, Qdrant, React/TypeScript frontend.

---

## Phase 1: Unblock Workers — 5 POST Endpoints

### Task 1: POST /api/evidence/extract

**Files:**
- Modify: `backend/app/api/routes/evidence.py`

Worker sends: `{event_id, video_ref, frame_ref, workspace_id, camera_id}`
Worker expects: `{evidence_refs: [...], timestamps: [...], integrity: str}`

- [ ] **Step 1: Add the endpoint to evidence.py**

```python
from pydantic import BaseModel

class EvidenceExtractRequest(BaseModel):
    event_id: str
    video_ref: str
    frame_ref: str
    workspace_id: str | None = None
    camera_id: str | None = None

@router.post("/extract")
async def extract_evidence(
    body: EvidenceExtractRequest,
    db: AsyncSession = Depends(get_db),
):
    """Organise pipeline-produced evidence files and register them in the DB.

    The pipeline already writes frames/clips to disk under EVIDENCE_ROOT.
    This endpoint scans for files matching the event_id and registers them
    as EvidenceFile rows, returning refs the workflow can pass downstream.
    """
    import hashlib
    from datetime import datetime, timezone

    evidence_refs = []
    timestamps = []
    event_uuid = None

    # Try to resolve event_id to a ViolationEvent UUID
    from app.db.models import ViolationEvent
    result = await db.execute(
        select(ViolationEvent).where(
            ViolationEvent.id == uuid.UUID(body.event_id)
        )
    )
    event_row = result.scalar_one_or_none()
    if event_row:
        event_uuid = event_row.id

    # Scan evidence directory for files matching this event
    for category in ("violations", "suspicious"):
        base = EVIDENCE_ROOT / category
        if not base.exists():
            continue
        for path in base.rglob(f"*{body.event_id[:8]}*"):
            if path.suffix not in (".jpg", ".jpeg", ".png", ".mp4", ".json"):
                continue
            # Compute checksum
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()

            file_type = "video_clip" if path.suffix == ".mp4" else "snapshot"
            if path.suffix == ".json":
                file_type = "metadata_json"

            # Check if already registered
            existing = await db.execute(
                select(EvidenceFile).where(
                    EvidenceFile.local_path == str(path)
                )
            )
            if existing.scalar_one_or_none():
                evidence_refs.append(str(path))
                continue

            ef = EvidenceFile(
                event_id=event_uuid,
                file_type=file_type,
                local_path=str(path),
                checksum=checksum,
            )
            db.add(ef)
            evidence_refs.append(str(path))
            timestamps.append(datetime.now(timezone.utc).isoformat())

    await db.commit()

    integrity = "verified" if evidence_refs else "no_evidence"
    return {
        "evidence_refs": evidence_refs,
        "timestamps": timestamps,
        "integrity": integrity,
    }
```

- [ ] **Step 2: Test the endpoint**

```bash
curl -X POST http://localhost:8080/api/evidence/extract \
  -H "Content-Type: application/json" \
  -d '{"event_id": "test-123", "video_ref": "", "frame_ref": ""}'
```

Expected: 200 with `{"evidence_refs": [], "timestamps": [], "integrity": "no_evidence"}`

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/routes/evidence.py
git commit -m "feat(api): add POST /api/evidence/extract endpoint for workflow worker"
```

### Task 2: POST /api/clustering/analyze (new route file)

**Files:**
- Create: `backend/app/api/routes/clustering.py`
- Modify: `backend/app/main.py` (register router)

Worker sends: `{event_id, evidence_refs: [...], detection_type, workspace_id}`
Worker expects: `{match_groups: [...], similarity_ranges: [...]}`

- [ ] **Step 1: Create clustering.py**

```python
# backend/app/api/routes/clustering.py
"""Clustering analysis — stub for visual similarity grouping."""
import uuid
from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import ViolationEvent

router = APIRouter(prefix="/clustering", tags=["clustering"])


class ClusterAnalyzeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    detection_type: str | None = None
    workspace_id: str | None = None


@router.post("/analyze")
async def analyze_clusters(
    body: ClusterAnalyzeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Find visually similar past events for repeat-offender detection.

    Phase 1: time+camera proximity grouping from DB.
    Phase 2 (future): CLIP embeddings + Qdrant similarity search.
    """
    match_groups = []
    similarity_ranges = []

    if not body.evidence_refs:
        return {"match_groups": [], "similarity_ranges": []}

    # Phase 1: find recent events from same camera with same detection_type
    try:
        event_uuid = uuid.UUID(body.event_id)
        result = await db.execute(
            select(ViolationEvent).where(
                ViolationEvent.id == event_uuid
            )
        )
        current = result.scalar_one_or_none()
        if current:
            from datetime import timedelta
            window_start = current.timestamp - timedelta(hours=24)
            similar = await db.execute(
                select(ViolationEvent).where(
                    and_(
                        ViolationEvent.camera_id == current.camera_id,
                        ViolationEvent.timestamp >= window_start,
                        ViolationEvent.id != event_uuid,
                    )
                ).limit(20)
            )
            rows = similar.scalars().all()
            if rows:
                match_groups.append({
                    "group_id": str(uuid.uuid4()),
                    "type": "temporal_proximity",
                    "event_ids": [str(r.id) for r in rows],
                    "count": len(rows),
                })
                similarity_ranges.append({
                    "metric": "time_proximity_hours",
                    "min": 0,
                    "max": 24,
                })
    except (ValueError, Exception):
        pass

    return {
        "match_groups": match_groups,
        "similarity_ranges": similarity_ranges,
    }
```

- [ ] **Step 2: Register in main.py**

Add to imports in `backend/app/main.py`:
```python
from app.api.routes import clustering
```

Add router registration:
```python
app.include_router(clustering.router, prefix="/api")
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/routes/clustering.py backend/app/main.py
git commit -m "feat(api): add POST /api/clustering/analyze endpoint for workflow worker"
```

### Task 3: POST /api/lpr/analyze

**Files:**
- Modify: `backend/app/api/routes/lpr.py`

Worker sends: `{event_id, evidence_refs: [...], workspace_id}`
Worker expects: `{plates: [{plate_number, confidence, ...}, ...]}`

- [ ] **Step 1: Add the endpoint to lpr.py**

```python
from pydantic import BaseModel

class LPRAnalyzeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    workspace_id: str | None = None

@router.post("/analyze")
async def analyze_lpr(
    body: LPRAnalyzeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run license plate recognition on evidence frames.

    Phase 1: return existing plate reads for the event from DB.
    Phase 2 (future): invoke ONNX LPR model on evidence frames.
    """
    plates = []

    if not body.evidence_refs:
        return {"plates": []}

    # Check if plates already extracted for this event's track sessions
    try:
        event_uuid = uuid.UUID(body.event_id)
        from app.db.models import ViolationEvent
        evt_result = await db.execute(
            select(ViolationEvent).where(ViolationEvent.id == event_uuid)
        )
        event = evt_result.scalar_one_or_none()
        if event and event.track_id:
            from app.db.models import TrackSession
            ts_result = await db.execute(
                select(TrackSession).where(
                    TrackSession.camera_id == event.camera_id,
                    TrackSession.track_id == event.track_id,
                )
            )
            track = ts_result.scalar_one_or_none()
            if track:
                plate_result = await db.execute(
                    select(PlateRead).where(
                        PlateRead.track_session_id == track.id
                    )
                )
                for p in plate_result.scalars().all():
                    plates.append({
                        "plate_number": p.plate_number,
                        "plate_normalised": p.plate_normalised,
                        "confidence": p.confidence,
                        "timestamp": p.timestamp.isoformat(),
                    })
    except (ValueError, Exception):
        pass

    return {"plates": plates}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/routes/lpr.py
git commit -m "feat(api): add POST /api/lpr/analyze endpoint for workflow worker"
```

### Task 4: POST /api/analytics/upsert

**Files:**
- Modify: `backend/app/api/routes/analytics.py`

Worker sends: `{event_id, workspace_id, zone_id, camera_id, detection_type, occurred_at, confidence, priority, lpr_result, cluster_result}`
Worker expects: `{version: int}`

- [ ] **Step 1: Add the endpoint to analytics.py**

```python
from pydantic import BaseModel
from datetime import datetime

class AnalyticsUpsertRequest(BaseModel):
    event_id: str
    workspace_id: str | None = None
    zone_id: str | None = None
    camera_id: str | None = None
    detection_type: str | None = None
    occurred_at: str | None = None
    confidence: float | None = None
    priority: str | None = None
    lpr_result: str | None = None
    cluster_result: str | None = None

@router.post("/upsert")
async def upsert_analytics(
    body: AnalyticsUpsertRequest,
    db: AsyncSession = Depends(get_db),
):
    """Idempotent analytics aggregate update.

    Bumps Redis counters for real-time dashboard and ensures the event
    is reflected in aggregate queries. Returns a version number for
    downstream workflow steps.
    """
    import redis.asyncio as aioredis
    from app.config.settings import settings as app_settings

    version = 1
    now_key = datetime.utcnow().strftime("%Y-%m-%d")

    try:
        r = aioredis.from_url(app_settings.redis_url)
        pipe = r.pipeline()
        pipe.hincrby(f"analytics:daily:{now_key}", "total", 1)
        if body.detection_type:
            pipe.hincrby(f"analytics:daily:{now_key}", body.detection_type, 1)
        if body.zone_id:
            pipe.hincrby(f"analytics:by_zone:{now_key}", body.zone_id, 1)
        if body.camera_id:
            pipe.hincrby(f"analytics:by_camera:{now_key}", body.camera_id, 1)
        pipe.incr("analytics:version")
        results = await pipe.execute()
        version = results[-1]
        await r.aclose()
    except Exception:
        pass

    return {"version": version}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/routes/analytics.py
git commit -m "feat(api): add POST /api/analytics/upsert endpoint for workflow worker"
```

### Task 5: POST /api/events/publish

**Files:**
- Modify: `backend/app/api/routes/events.py`

Worker sends: `{event_id, workflow_id, workspace_id, zone_id, camera_id, detection_type, confidence, priority, evidence_refs, lpr_result, cluster_result, analytics_status, published_at}`
Worker expects: `{id: str}`

- [ ] **Step 1: Add the endpoint to events.py**

```python
from pydantic import BaseModel as PydanticBaseModel

class EventPublishRequest(PydanticBaseModel):
    event_id: str
    workflow_id: str | None = None
    workspace_id: str | None = None
    zone_id: str | None = None
    camera_id: str | None = None
    detection_type: str | None = None
    confidence: float | None = None
    priority: str | None = None
    evidence_refs: list[str] = []
    lpr_result: list | None = None
    cluster_result: list | None = None
    analytics_status: str | None = None
    published_at: str | None = None

@router.post("/publish")
async def publish_event(
    body: EventPublishRequest,
    db: AsyncSession = Depends(get_db),
):
    """Persist final enriched event record for UI screens.

    Updates the ViolationEvent with workflow results (LPR plates,
    cluster matches) so the dashboard shows complete information.
    """
    try:
        event_uuid = uuid.UUID(body.event_id)
        result = await db.execute(
            select(ViolationEvent).where(ViolationEvent.id == event_uuid)
        )
        event = result.scalar_one_or_none()
        if event:
            if body.lpr_result:
                event.relation_result = {
                    **(event.relation_result or {}),
                    "lpr_plates": body.lpr_result,
                }
            if body.cluster_result:
                event.relation_result = {
                    **(event.relation_result or {}),
                    "cluster_matches": body.cluster_result,
                }
            if body.evidence_refs:
                event.video_clip_url = event.video_clip_url or next(
                    (r for r in body.evidence_refs if r.endswith(".mp4")), None
                )
                event.snapshot_url = event.snapshot_url or next(
                    (r for r in body.evidence_refs if r.endswith((".jpg", ".png"))), None
                )
            await db.commit()
            return {"id": str(event.id)}
    except (ValueError, Exception):
        pass

    return {"id": body.event_id}
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/api/routes/events.py
git commit -m "feat(api): add POST /api/events/publish endpoint for workflow worker"
```

---

## Phase 2: Wire Settings Frontend to Config API

### Task 6: Wire GeneralSection to config API

**Files:**
- Modify: `frontend/src/components/views/SettingsView.tsx` (GeneralSection, lines 123-145)

Config key: `workspace_settings` → `{name, org_name, timezone, date_format, language}`

- [ ] **Step 1: Replace GeneralSection with config-backed version**

Replace the GeneralSection function (lines 123-145) with a version that:
1. Fetches `GET /api/config/workspace_settings` on mount
2. Populates form fields from response
3. PUTs to `/api/config/workspace_settings` on save

```tsx
function GeneralSection({ onSave }: { onSave: () => void }) {
  const [ws, setWs] = useState('');
  const [org, setOrg] = useState('');
  const [tz, setTz] = useState('IST (UTC+05:30)');
  const [df, setDf] = useState('DD MMM YYYY');
  const [lang, setLang] = useState('English');
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/config/workspace_settings`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.value) {
          setWs(data.value.name || '');
          setOrg(data.value.org_name || '');
          setTz(data.value.timezone || 'IST (UTC+05:30)');
          setDf(data.value.date_format || 'DD MMM YYYY');
          setLang(data.value.language || 'English');
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const mark = (fn: (v: string) => void) => (v: string) => { fn(v); setDirty(true); };

  const save = async () => {
    setSaving(true);
    await fetch(`${API_BASE}/api/config/workspace_settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        value: { name: ws, org_name: org, timezone: tz, date_format: df, language: lang },
      }),
    });
    setSaving(false);
    setDirty(false);
    onSave();
  };

  if (loading) return <p className="text-xs" style={{ color: CV_COLORS.muted }}>Loading…</p>;

  return <>
    <SectionTitle title="Workspace details" subtitle="Organisation and operating defaults." />
    <Panel>
      <Field label="Workspace name"><Inp value={ws} onChange={mark(setWs)} /></Field>
      <Field label="Organisation name"><Inp value={org} onChange={mark(setOrg)} /></Field>
      <Field label="Time zone"><Sel value={tz} options={['IST (UTC+05:30)']} onChange={mark(setTz)} /></Field>
      <Field label="Default date format"><Sel value={df} options={['DD MMM YYYY', 'YYYY-MM-DD', 'MM/DD/YYYY']} onChange={mark(setDf)} /></Field>
      <Field label="Default evidence language"><Sel value={lang} options={['English', 'Kannada', 'Hindi']} onChange={mark(setLang)} /></Field>
      <div className="flex justify-end gap-2 mt-4">
        <Btn onClick={() => setDirty(false)}>Cancel</Btn>
        <Btn primary disabled={!dirty} onClick={save}>{saving ? 'Saving…' : 'Save changes'}</Btn>
      </div>
    </Panel>
  </>;
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/views/SettingsView.tsx
git commit -m "feat(settings): wire GeneralSection to config API"
```

### Task 7: Wire CamerasSection to config API + real detection_config

**Files:**
- Modify: `frontend/src/components/views/SettingsView.tsx` (CamerasSection + CameraThresholds)

Workspace-level: config key `detection_settings` → `{person_dump, vehicle_dump, person_match, vehicle_plate, auto_mark, req_confirm, escalate, midnight_window, daytime_window}`
Per-camera: `GET /api/cameras` then `PATCH /api/cameras/{id}` with `detection_config` JSONB

- [ ] **Step 1: Replace CamerasSection with config-backed version**

Wire workspace-level detection toggles to `detection_settings` config key.
Replace CameraThresholds to fetch real cameras from `/api/cameras` and show real `detection_config` fields: `dwell_violation_threshold_s`, `waste_proximity_alert_window_s`, `waste_proximity_px`.

The CamerasSection saves workspace toggles to `PUT /api/config/detection_settings`.
The CameraThresholds fetches `GET /api/cameras`, shows real fields, and PATCHes `PATCH /api/cameras/{id}` with updated `detection_config`.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/views/SettingsView.tsx
git commit -m "feat(settings): wire CamerasSection to config API with real detection_config fields"
```

### Task 8: Wire EvidenceSection to config API

**Files:**
- Modify: `frontend/src/components/views/SettingsView.tsx` (EvidenceSection, lines 328-348)

Config key: `retention_settings` → `{meta_months, frame_days, clip_days}`

- [ ] **Step 1: Replace EvidenceSection with config-backed version**

1. Fetch `GET /api/config/retention_settings` on mount
2. Populate retention selects from response
3. PUT to `/api/config/retention_settings` on save
4. Fetch real storage usage from evidence API if available

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/views/SettingsView.tsx
git commit -m "feat(settings): wire EvidenceSection to config API"
```

### Task 9: Wire AuditSection to logs API

**Files:**
- Modify: `frontend/src/components/views/SettingsView.tsx` (AuditSection, lines 738-774)

- [ ] **Step 1: Replace AuditSection with live data**

Fetch from `GET /api/logs?limit=50` on mount (log_routes.py exists).
Replace AUDIT_DATA hardcoded array with live results.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/views/SettingsView.tsx
git commit -m "feat(settings): wire AuditSection to logs API"
```

### Task 10: Wire WorkflowsSection to ops API

**Files:**
- Modify: `frontend/src/components/views/SettingsView.tsx` (WorkflowsSection)

- [ ] **Step 1: Replace WorkflowsSection with live data**

Fetch from `GET /api/ops/health`, `GET /api/ops/workers`, `GET /api/ops/workflows` on mount (operations.py exists).
Replace hardcoded metrics/workers with live data, fallback to current hardcoded values on fetch failure.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/views/SettingsView.tsx
git commit -m "feat(settings): wire WorkflowsSection to ops API"
```

---

## Phase 3: Intelligence Layer

### Task 11: ReID Service — CLIP ViT-B/16 + Qdrant

**Files:**
- Create: `backend/services/reid/service.py`
- Create: `backend/services/reid/clip_embedder.py`
- Create: `backend/app/api/routes/reid.py`
- Modify: `backend/app/main.py` (register router)

- [ ] **Step 1: Create CLIP embedder**

```python
# backend/services/reid/clip_embedder.py
"""CLIP ViT-B/16 ONNX embedding extractor for ReID."""
import numpy as np
import onnxruntime as ort
from PIL import Image

class CLIPEmbedder:
    def __init__(self, model_path: str = "/models/clip-vit-b16-visual.onnx"):
        self.session = ort.InferenceSession(
            model_path,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = (224, 224)

    def preprocess(self, image: Image.Image) -> np.ndarray:
        img = image.convert("RGB").resize(self.input_size)
        arr = np.array(img, dtype=np.float32) / 255.0
        mean = np.array([0.48145466, 0.4578275, 0.40821073])
        std = np.array([0.26862954, 0.26130258, 0.27577711])
        arr = (arr - mean) / std
        return arr.transpose(2, 0, 1)[np.newaxis].astype(np.float32)

    def embed(self, image: Image.Image) -> np.ndarray:
        inp = self.preprocess(image)
        outputs = self.session.run(None, {self.input_name: inp})
        embedding = outputs[0][0]
        embedding = embedding / np.linalg.norm(embedding)
        return embedding  # 512-dim L2-normalised
```

- [ ] **Step 2: Create ReID route**

```python
# backend/app/api/routes/reid.py
"""ReID — person/vehicle re-identification via CLIP + Qdrant."""
import uuid
from fastapi import APIRouter, Depends, UploadFile, File
from pydantic import BaseModel

router = APIRouter(prefix="/reid", tags=["reid"])

class ReIDAnalyzeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    detection_type: str | None = None
    workspace_id: str | None = None

class ReIDMatch(BaseModel):
    event_id: str
    score: float
    timestamp: str | None = None

@router.post("/analyze")
async def analyze_reid(body: ReIDAnalyzeRequest):
    """Embed evidence crops via CLIP and search Qdrant for matches.

    Returns top-k matches with similarity scores.
    Phase 1: stub that returns empty matches.
    Phase 2: CLIP + Qdrant integration.
    """
    # ponytail: stub until CLIP ONNX model is downloaded and Qdrant deployed
    return {
        "matches": [],
        "embedding_count": 0,
        "collection": f"civilytics_{'persons' if body.detection_type != 'vehicle_dump' else 'vehicles'}",
    }
```

- [ ] **Step 3: Register in main.py and commit**

```bash
git add backend/services/reid/ backend/app/api/routes/reid.py backend/app/main.py
git commit -m "feat(reid): add CLIP ViT-B/16 embedder and ReID route stub"
```

### Task 12: VLM Service — SmolVLM2-500M

**Files:**
- Create: `backend/services/vlm/service.py`
- Create: `backend/app/api/routes/vlm.py`
- Modify: `backend/app/main.py` (register router)

- [ ] **Step 1: Create VLM service**

```python
# backend/services/vlm/service.py
"""SmolVLM2-500M scene description service."""
from transformers import AutoModelForVision2Seq, AutoProcessor
from PIL import Image
import torch

class VLMService:
    def __init__(self, model_id: str = "HuggingFaceTB/SmolVLM2-500M-Video-Instruct"):
        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = AutoModelForVision2Seq.from_pretrained(
            model_id,
            torch_dtype=torch.float16,
            device_map="auto",
        )

    def describe(self, image: Image.Image, prompt: str = "Describe this surveillance scene in detail, focusing on any waste dumping activity.") -> str:
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": prompt},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(self.model.device)
        with torch.no_grad():
            output_ids = self.model.generate(**inputs, max_new_tokens=256)
        return self.processor.batch_decode(output_ids, skip_special_tokens=True)[0]
```

- [ ] **Step 2: Create VLM route**

```python
# backend/app/api/routes/vlm.py
"""VLM — scene description via SmolVLM2-500M."""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/vlm", tags=["vlm"])

class VLMDescribeRequest(BaseModel):
    event_id: str
    evidence_refs: list[str] = []
    workspace_id: str | None = None

@router.post("/describe")
async def describe_scene(body: VLMDescribeRequest):
    """Generate scene description from evidence frames.

    Phase 1: stub. Phase 2: SmolVLM2-500M inference.
    """
    # ponytail: stub until model downloaded
    return {
        "description": "",
        "model": "SmolVLM2-500M-Video-Instruct",
        "status": "stub",
    }
```

- [ ] **Step 3: Register and commit**

```bash
git add backend/services/vlm/ backend/app/api/routes/vlm.py backend/app/main.py
git commit -m "feat(vlm): add SmolVLM2-500M service and scene description route stub"
```

---

## Phase 4: Dispatch Workers

### Task 13: Telegram dispatch worker

**Files:**
- Create: `workers/workers/tasks/dispatch_telegram.py`
- Modify: `workers/workers/main.py` (register handler)
- Modify: `workers/workers/config.py` (add telegram settings)

- [ ] **Step 1: Create telegram dispatch**

```python
# workers/workers/tasks/dispatch_telegram.py
"""dispatch_telegram — send alert to Telegram chats."""
import httpx
import structlog
from workers.config import settings

log = structlog.get_logger()

async def handle_dispatch_telegram(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    # Fetch Telegram config from backend config API
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.evidence_service_url}/api/config/delivery_channel_telegram"
        )
        if resp.status_code != 200:
            return {"status": "SKIPPED", "reason": "telegram_not_configured"}
        config = resp.json().get("value", {})

    bot_token = config.get("tg_bot_token")
    chat_ids = [c.strip() for c in config.get("tg_chat_ids", "").split(",") if c.strip()]

    if not bot_token or not chat_ids:
        return {"status": "SKIPPED", "reason": "telegram_incomplete_config"}

    detection_type = event.get("detection_type", "Unknown event")
    camera_id = event.get("camera_id", "unknown")
    confidence = event.get("confidence", 0)
    priority = event.get("priority", "medium")

    message = (
        f"🚨 *Civilytics Alert*\n\n"
        f"*Type:* {detection_type.replace('_', ' ').title()}\n"
        f"*Camera:* {camera_id}\n"
        f"*Confidence:* {confidence:.0%}\n"
        f"*Priority:* {priority.upper()}\n"
        f"*Event:* `{event_id}`"
    )

    results = []
    async with httpx.AsyncClient(timeout=30) as client:
        for chat_id in chat_ids:
            try:
                resp = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": message,
                        "parse_mode": "Markdown",
                    },
                )
                resp.raise_for_status()
                results.append({"chat_id": chat_id, "status": "sent"})
            except Exception as e:
                results.append({"chat_id": chat_id, "status": "failed", "error": str(e)[:200]})

    log.info("telegram.dispatched", event_id=event_id, results=len(results))
    return {"status": "COMPLETED", "deliveries": results}
```

- [ ] **Step 2: Register handler in main.py**

Add import and handler to TASK_HANDLERS list.

- [ ] **Step 3: Commit**

```bash
git add workers/workers/tasks/dispatch_telegram.py workers/workers/main.py
git commit -m "feat(dispatch): add Telegram alert dispatch worker"
```

### Task 14: Email dispatch worker

**Files:**
- Create: `workers/workers/tasks/dispatch_email.py`
- Modify: `workers/workers/main.py` (register handler)

- [ ] **Step 1: Create email dispatch**

```python
# workers/workers/tasks/dispatch_email.py
"""dispatch_email — send alert report via SMTP."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx
import structlog
from workers.config import settings

log = structlog.get_logger()

async def handle_dispatch_email(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")
    report = input_data.get("report", "")

    # Fetch email config
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.evidence_service_url}/api/config/delivery_channel_email"
        )
        if resp.status_code != 200:
            return {"status": "SKIPPED", "reason": "email_not_configured"}
        config = resp.json().get("value", {})

    smtp_host = config.get("smtp_host")
    smtp_port = int(config.get("smtp_port", 587))
    smtp_user = config.get("smtp_user")
    smtp_password = config.get("smtp_password")
    smtp_from = config.get("smtp_from", smtp_user)

    if not all([smtp_host, smtp_user, smtp_password]):
        return {"status": "SKIPPED", "reason": "email_incomplete_config"}

    # Fetch notification rules for recipients
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.evidence_service_url}/api/config/notification_rules"
        )
        rules = resp.json().get("value", []) if resp.status_code == 200 else []

    detection_type = event.get("detection_type", "Unknown")
    camera_id = event.get("camera_id", "unknown")
    confidence = event.get("confidence", 0)

    subject = f"Civilytics Alert: {detection_type.replace('_', ' ').title()} — {event_id[:8]}"

    body_html = f"""
    <h2>Civilytics Incident Report</h2>
    <table style="border-collapse: collapse; width: 100%;">
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Event ID</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{event_id}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Type</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{detection_type}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Camera</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{camera_id}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Confidence</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{confidence:.0%}</td></tr>
    </table>
    {"<h3>Intelligence Report</h3><p>" + report + "</p>" if report else ""}
    <p style="color: #888; font-size: 12px;">— Civilytics by Satyaki Solutions</p>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = smtp_user  # default to self, override with rules
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    except Exception as e:
        raise Exception(f"Email dispatch failed: {e}")

    log.info("email.dispatched", event_id=event_id)
    return {"status": "COMPLETED"}
```

- [ ] **Step 2: Register and commit**

```bash
git add workers/workers/tasks/dispatch_email.py workers/workers/main.py
git commit -m "feat(dispatch): add email alert dispatch worker via SMTP"
```

### Task 15: IP Speaker dispatch worker

**Files:**
- Create: `workers/workers/tasks/dispatch_speaker.py`
- Modify: `workers/workers/main.py`

- [ ] **Step 1: Create speaker dispatch**

POST TTS audio URL to speaker's HTTP endpoint. Falls back to text if no audio.

- [ ] **Step 2: Register and commit**

### Task 16: WhatsApp dispatch worker

**Files:**
- Create: `workers/workers/tasks/dispatch_whatsapp.py`
- Modify: `workers/workers/main.py`

- [ ] **Step 1: Create WhatsApp dispatch**

Uses WhatsApp Cloud API (graph.facebook.com) with configured access token.

- [ ] **Step 2: Register and commit**

### Task 17: SIP/IVR call dispatch worker

**Files:**
- Create: `workers/workers/tasks/dispatch_sip.py`
- Modify: `workers/workers/main.py`

- [ ] **Step 1: Create SIP dispatch**

Initiates SIP call via configured SIP server using pjsua2 or Twilio API.

- [ ] **Step 2: Register and commit**

---

## Phase 5: CrewAI Report Composition

### Task 18: CrewAI compose report worker

**Files:**
- Create: `workers/workers/tasks/crewai_compose.py`
- Create: `workers/workers/crews/incident_crew.py`
- Modify: `workers/workers/main.py`
- Modify: `workers/workers/config.py` (add bitnet_server_url)

- [ ] **Step 1: Create incident crew**

```python
# workers/workers/crews/incident_crew.py
"""CrewAI incident report crew — 3 agents composing a professional report."""
from crewai import Agent, Task, Crew
from langchain_openai import ChatOpenAI

def create_incident_crew(event: dict, evidence: dict, reid: dict, vlm: dict, bitnet_url: str) -> Crew:
    llm = ChatOpenAI(
        model="bitnet-2b",
        base_url=bitnet_url,
        api_key="not-needed",
    )

    evidence_analyst = Agent(
        role="Evidence Analyst",
        goal="Analyse all available evidence for the incident",
        backstory="Senior forensic analyst specialising in surveillance evidence.",
        llm=llm,
        verbose=False,
    )

    severity_assessor = Agent(
        role="Severity Assessor",
        goal="Assess severity and recommend enforcement action",
        backstory="Municipal enforcement specialist with regulatory knowledge.",
        llm=llm,
        verbose=False,
    )

    report_writer = Agent(
        role="Report Writer",
        goal="Compose a professional, diplomatic incident report",
        backstory="Technical writer who produces clear, authoritative reports.",
        llm=llm,
        verbose=False,
    )

    analyze_task = Task(
        description=f"Analyze evidence for event {event.get('event_id')}: {vlm.get('description', 'No description')}. ReID matches: {reid.get('matches', [])}. Evidence refs: {evidence.get('evidence_refs', [])}.",
        expected_output="Evidence summary with key findings",
        agent=evidence_analyst,
    )

    assess_task = Task(
        description="Based on the evidence analysis, assess severity and recommend action.",
        expected_output="Severity assessment with recommended actions",
        agent=severity_assessor,
    )

    compose_task = Task(
        description="Compose a professional diplomatic email report combining evidence analysis and severity assessment. Include event details, timestamp, location, and recommended next steps.",
        expected_output="Professional incident report email body",
        agent=report_writer,
    )

    return Crew(
        agents=[evidence_analyst, severity_assessor, report_writer],
        tasks=[analyze_task, assess_task, compose_task],
        verbose=False,
    )
```

- [ ] **Step 2: Create compose worker**

```python
# workers/workers/tasks/crewai_compose.py
"""crewai_compose_report — generate professional incident report via CrewAI."""
import structlog
from workers.config import settings

log = structlog.get_logger()

async def handle_crewai_compose(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    log.info("crewai.composing", event_id=event_id)

    evidence = {
        "evidence_refs": input_data.get("evidence_refs", []),
    }
    reid = {
        "matches": input_data.get("reid_matches", []),
    }
    vlm = {
        "description": input_data.get("scene_description", ""),
    }

    try:
        from workers.crews.incident_crew import create_incident_crew

        crew = create_incident_crew(
            event=event,
            evidence=evidence,
            reid=reid,
            vlm=vlm,
            bitnet_url=settings.bitnet_server_url,
        )
        result = crew.kickoff()
        report = str(result)
    except Exception as e:
        log.warning("crewai.fallback", error=str(e)[:200])
        report = f"Incident Report for {event_id}: {vlm.get('description', 'No details available')}"

    log.info("crewai.completed", event_id=event_id, report_len=len(report))

    return {
        "report": report,
        "status": "COMPLETED",
    }
```

- [ ] **Step 3: Add bitnet_server_url to config**

Add to `workers/workers/config.py`:
```python
bitnet_server_url: str = "http://bitnet:8000/v1"
```

- [ ] **Step 4: Register and commit**

```bash
git add workers/workers/tasks/crewai_compose.py workers/workers/crews/ workers/workers/main.py workers/workers/config.py
git commit -m "feat(crewai): add incident report composition worker with 3-agent crew"
```

---

## Phase 6: Channel Onboarding & Logo Upload

### Task 19: Add test buttons to notification channel cards

**Files:**
- Modify: `frontend/src/components/views/SettingsView.tsx` (NotificationsSection channel cards)

- [ ] **Step 1: Add "Send test" button to each enabled channel card**

When clicked, POST to `/api/config/test_channel_{id}` which triggers a test alert through the dispatch worker.

- [ ] **Step 2: Commit**

### Task 20: Logo upload endpoint

**Files:**
- Create: `backend/app/api/routes/branding.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create branding route**

```python
# backend/app/api/routes/branding.py
"""Branding — customer logo upload for reports."""
import shutil
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from app.auth.middleware import require_admin

router = APIRouter(prefix="/branding", tags=["branding"])

LOGO_DIR = Path("/app/static/branding")

@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    _user: dict = Depends(require_admin),
):
    if file.content_type not in ("image/png", "image/jpeg", "image/svg+xml"):
        raise HTTPException(400, "Only PNG, JPEG, or SVG allowed")

    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    ext = file.filename.rsplit(".", 1)[-1] if file.filename else "png"
    dest = LOGO_DIR / f"logo.{ext}"

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return {"url": f"/static/branding/logo.{ext}"}

@router.get("/logo")
async def get_logo():
    for ext in ("png", "jpeg", "jpg", "svg"):
        p = LOGO_DIR / f"logo.{ext}"
        if p.exists():
            return {"url": f"/static/branding/logo.{ext}"}
    return {"url": None}
```

- [ ] **Step 2: Register and commit**

### Task 21: Logo upload UI in GeneralSection

**Files:**
- Modify: `frontend/src/components/views/SettingsView.tsx`

- [ ] **Step 1: Add logo upload field to GeneralSection**

Add a file input that POSTs to `/api/branding/logo` and shows current logo preview.

- [ ] **Step 2: Commit**

---

## Phase 7: Conductor Workflow Update

### Task 22: Update workflow definition with new tasks

**Files:**
- Modify: `backend/app/workflow/task_definitions.py`

- [ ] **Step 1: Add new task definitions**

Add to TASK_DEFINITIONS: `reid_analyze`, `vlm_describe`, `crewai_compose_report`, `dispatch_immediate`, `dispatch_messaging`, `dispatch_email`.

- [ ] **Step 2: Update workflow to v2 with new fork structure**

```
validate → evidence → FORK(reid, vlm, lpr, clustering) → JOIN
  → FORK(dispatch_immediate, dispatch_messaging, crewai_compose) → JOIN
  → dispatch_email → update_analytics → publish_event
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/workflow/task_definitions.py
git commit -m "feat(workflow): add v2 workflow with ReID, VLM, dispatch, and CrewAI tasks"
```

---

## Phase 8: Cleanup Worker

### Task 23: Add cleanup worker

**Files:**
- Create: `workers/workers/tasks/cleanup.py`
- Modify: `workers/workers/main.py`

- [ ] **Step 1: Create cleanup handler**

Deletes evidence files older than retention policy, prunes Qdrant embeddings, archives Conductor history.

- [ ] **Step 2: Register and commit**

---

## Commit Budget

Each task produces 1 commit. 23 tasks = 23 commits. Each commit stays under 800 lines per the project rule.

Phase 1 (Tasks 1-5): ~5 commits, ~400 lines total
Phase 2 (Tasks 6-10): ~5 commits, ~500 lines total
Phase 3 (Tasks 11-12): ~2 commits, ~200 lines total
Phase 4 (Tasks 13-17): ~5 commits, ~500 lines total
Phase 5 (Task 18): ~1 commit, ~200 lines total
Phase 6 (Tasks 19-21): ~3 commits, ~300 lines total
Phase 7 (Task 22): ~1 commit, ~200 lines total
Phase 8 (Task 23): ~1 commit, ~100 lines total
