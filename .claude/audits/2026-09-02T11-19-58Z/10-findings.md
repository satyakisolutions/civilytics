# Findings

## F-01 — Pipeline is single-camera-capacity despite UI/API allowing N active cameras
**Severity**: High
**Personas affected**: Operator/admin adding a second camera expecting real-time detection on it.
**Preconditions**: RF-DETR TensorRT engine built as static batch=1 (current shipped
`.engine`/`.onnx` in `pipeline/models/rfdetr/`); more than one camera source registered active.

**Evidence [CODE]**:
- `pipeline/app/config.py:15` — `batch_size: int = 1`, with a comment (`config.py:13-14`) stating
  it must stay 1 "until this pod's engine is rebuilt with a dynamic batch axis" (paraphrased from
  file comment referencing rebuilding with dynamic-batch ONNX export).
- `pipeline/app/pipeline.py:975-977` — `streammux = make_element("nvstreammux", ...)`;
  `streammux.set_property("batch-size", cfg.batch_size)` — the DeepStream muxer's batch size is
  wired directly from `cfg.batch_size` (i.e. 1).
- `pipeline/configs/nvinfer_rfdetr.txt:31` — `batch-size=1` on the primary detector (RF-DETR)
  nvinfer element, independently confirming the inference engine only accepts 1 frame per batch.
- `scripts/onnx_to_engine.py:106-126` — explicit build-time comments: RF-DETR's exported ONNX has
  fixed-shape outputs (`dets [1,300,4]`, `labels [1,300,91]`) so "trtexec cannot stretch the
  engine past batch=1 here"; `min_batch=1, opt_batch=1, max_batch=1` is hard-pinned for both the
  RF-DETR and (per line ~120-126) the action-classifier engine builds, with a note that
  `nvinfer_action_sgie.txt`'s `batch-size=16` is silently unhonorable given a static engine.

**Mechanism**: `nvstreammux` batches frames from all linked/requested source pads into batches of
size `batch-size` before handing them to `nvinfer`. With `batch-size=1` and 2+ active RTSP
sources feeding pads into the same muxer, the muxer can only forward one frame per batch cycle
system-wide — not one-per-camera — so with 2 cameras registered, each camera receives at most
roughly half the effective inference throughput of a single-camera setup, and adding more active
cameras degrades every camera's real per-stream frame rate through the detector proportionally
(not "camera 2 gets zero", but "all cameras collectively share one batch slot"). The task brief's
characterization ("second camera registers as ACTIVE but its video never reaches the muxer / no
real detection throughput") could not be independently reproduced live (no browser tool — see
blockers) but the root cause and its severity are corroborated directly in code: the system is
architecturally provisioned for exactly 1 concurrent inference slot, while the UI/API camera CRUD
(`/api/cameras`, confirmed live via `GET http://localhost:8082/api/cameras`) has no such
constraint and lets an operator mark N cameras active with no warning.

**Business impact**: An operator can add a second, third, ... camera through the UI, see it show
as "active"/healthy at the connection layer, and reasonably believe it is being monitored for
dumping events — while it is either starved of inference cycles or (per the task brief's more
specific claim) never reaches the muxer at all. This is a silent monitoring gap in a
safety/compliance-oriented product, which is high business impact even though it is not a
security/data-loss defect.

**Root cause / fix area**: `pipeline/app/config.py` (`batch_size`), `scripts/onnx_to_engine.py`
(engine export — would need a rebuilt engine with a real dynamic or multi-batch profile),
`pipeline/configs/nvinfer_rfdetr.txt` (`batch-size=`). Secondary: no camera-activation guard in
backend/API/UI to prevent or warn about exceeding pipeline capacity.

**Recommended next action**: rebuild the RF-DETR export/engine with a dynamic or fixed N-batch
profile sized to the number of cameras the product is meant to support, or add an explicit
capacity check/warning at camera-activation time (API + UI) so operators are not told a camera is
"ACTIVE" when it cannot receive real detection throughput.

---

## F-02 — Auth fully bypassed on the live dev stack (WW_AUTH_DEV_MODE=true)
**Severity**: Low/Informational (by design for local dev, but worth recording as confirmed live
state; would be Critical if this env var were ever set in a non-local deployment).

**Evidence [CODE + RUNTIME]**:
- `backend/app/auth/middleware.py:12-32` — `_is_dev_mode()` returns true only if
  `WW_AUTH_DEV_MODE` env var is `1/true/yes`; when true and no `Authorization` header is present,
  `get_current_user()` returns a synthetic user with `realm_access.roles = ["owner","admin","viewer"]`
  — i.e. full privilege, no token.
- [RUNTIME] `docker exec lsg-wastewatch-api-1 env | grep -i auth` → `WW_AUTH_DEV_MODE=true` is set
  on the actual running API container.
- [RUNTIME] `GET http://localhost:8082/api/cameras` (no Authorization header) returned live camera
  data (200, one camera record) — confirming the bypass is in effect on this stack right now, not
  just in theory.
- Keycloak container (`lsg-wastewatch-keycloak-1`) is up and reachable
  (`GET http://localhost:8180/` → 302, i.e. serving) but is not actually being asked to validate
  anything for this API traffic given the dev-mode short-circuit.
- Frontend: `frontend/.env` still points `VITE_SUPABASE_*` at a Supabase project id
  (`fpqajyyjxnidupsaimmw`), but `frontend/src/integrations/supabase/client.ts` explicitly
  hardcodes that exact project id into a blocklist (comment: "the original Lovable-generated URL
  points at a hosted project that no longer exists") and returns a stub client — so the frontend
  is not using Keycloak or Supabase for auth in this deployment as configured.

**Assessment**: this looks like intentional, guarded dev-mode design (default is auth *required*;
the flag must be explicitly set), not a defect. Flagging because the audit brief specifically
asked to verify enforcement status, and the confirmed state is "fully open" on the currently
running stack.

**Recommended next action**: add a boot-time log line/metric when the API starts with
`WW_AUTH_DEV_MODE=true` so it's visible in ops dashboards, and confirm CI/staging/prod compose
files never set this var (not verified in this pass — see coverage/blockers).

---

## F-03 — Dead Supabase client/config left in the frontend
**Severity**: Low (maintainability only).
**Evidence [CODE]**: `frontend/.env:1-3` sets `VITE_SUPABASE_PROJECT_ID`/`_PUBLISHABLE_KEY`/`_URL`
pointing at a Supabase project confirmed dead; `frontend/src/integrations/supabase/client.ts`
explicitly special-cases and blocks that exact URL, always returning a stub/no-op client. This is
functioning as designed (no user-facing defect) but is a foot-gun: a future contributor changing
`.env` to a *real* reachable Supabase project would silently reactivate a data path the rest of
the app (which talks to the FastAPI backend at 8082) does not expect. Recommend removing the
Supabase env vars and client entirely, or renaming/gating it much more explicitly.
