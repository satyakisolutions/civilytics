# Face-ID lab (standalone)

Checks the face subject-ID feature on your own machine, against your RTSP
camera, **before** running the full stack. It uses the feature's own code from
`backend/app/subjects` for detection (SCRFD), the quality gate, embeddings
(ArcFace) and the new / repeat / review decision. It needs no Docker,
DeepStream, Kafka, Postgres, Qdrant or GPU.

What you see on screen:

| Box | Label | Meaning |
|---|---|---|
| yellow | `collecting 1/3: blurry` | face found; gathering good shots (reason shown if the shot was rejected) |
| green | `S-001 - New` | first time this face is seen |
| red | `S-001 - Repeat x2 (0.71)` | same face came back; number is the similarity |
| orange | `S-004 - maybe S-001? (0.47)` | uncertain match; in the real system this goes to operator review |
| grey | `Face not captured` | a face was in view but never good enough (too small / blurry / turned away) |

Each **visit** is matched once. A visit ends when the face has been out of view
for `--gone-s` (default 3 s); coming back after that is a new visit, which is
how "Repeat" happens. This mirrors the real rule: one violation episode per
person is one incident. The standalone test enrols **every** face it sees; the
real feature only enrols people at a violation.

## Setup (Windows, PowerShell or Git Bash)

```bash
git lfs pull                                   # face models: backend/models/face/*.onnx (~190 MB)
cd tools/face_lab
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python
```

The lab uses its own venv on purpose: it needs the GUI build of OpenCV
(`opencv-python`), and the backend uses the headless one. **Always run it with
`.venv/Scripts/python`**, not the system `python`.

### Use the GPU (recommended: 30 fps video, ~20 fps face processing)

```bash
.venv/Scripts/python -m pip uninstall -y onnxruntime
.venv/Scripts/python -m pip install -r requirements-gpu.txt
```

This installs ONNX Runtime with DirectML, which works on any Windows GPU and needs no CUDA install.
`--device auto` (the default) then uses the GPU, and video is decoded on the GPU too.
The startup log shows `Inference on GPU` and `Video decoding on GPU`; the stats bar shows both rates.

Measured on this project's laptop (GTX 1650 Ti) with the Axis camera at 1080p:

| Setup | Video (window / HTTP stream) | Face processing |
|---|---|---|
| CPU only | ~5 fps | ~3 fps |
| GPU inference + GPU decoding | ~25–30 fps | ~20 fps |

The picture always runs at the camera's rate. Face processing runs on its own thread, and its latest
boxes are drawn on every frame. The slow part, the face fingerprint, runs only while a visit is still
collecting its first good shots.

If 1080p decoding is still slow on a weaker machine, ask the camera for 720p. For Axis, append
`?resolution=1280x720&fps=30` to the URL. Faces become smaller, so distant faces may show `too small`.

## Run

```bash
# RTSP camera (use your camera's URL; quote it)
.venv/Scripts/python run_face_lab.py --source "rtsp://USER:PASS@192.168.1.64:554/Streaming/Channels/101"

# keep IDs across restarts
.venv/Scripts/python run_face_lab.py --source "rtsp://..." --gallery gallery

# a recorded clip without a window, or a laptop webcam
.venv/Scripts/python run_face_lab.py --source clip.mp4 --no-display
.venv/Scripts/python run_face_lab.py --source 0
```

Common RTSP URL shapes (check your camera's manual):
Axis `rtsp://user:pass@IP/axis-media/media.amp?fps=30`;
Hikvision `rtsp://user:pass@IP:554/Streaming/Channels/101` (main) or `/102` (sub-stream);
Dahua / CP Plus `rtsp://user:pass@IP:554/cam/realmonitor?channel=1&subtype=0`.
Use the **main stream** for face tests; sub-streams are usually too low resolution.
If the picture smears or freezes, the default is already RTSP over TCP; try `--udp` only if TCP fails.

Keys in the window: `q` quit, `r` reset all IDs, `s` save a snapshot.

### Watch the annotated view in VLC or a browser (HTTP)

```bash
.venv/Scripts/python run_face_lab.py --source "rtsp://..." --no-display --serve-port 8090
```

Open `http://localhost:8090/stream.mjpg`:

- **VLC:** Media → Open Network Stream.
- **Browser:** paste the URL into the address bar.

`http://localhost:8090/snapshot.jpg` gives the current frame. The stream is only reachable
from this machine by default. To watch from another PC, add `--serve-host 0.0.0.0` and open
`http://<this-PC's-IP>:8090/stream.mjpg` there. Anyone on the network can then see the faces,
so use this only on a trusted network. You can drop `--no-display` to have the OpenCV window
and the HTTP stream at the same time.

Every run writes a folder under `runs/<date-time>/` with `results.csv` (one row
per visit) and `faces/` (the best face per visit, named by subject and status).
These are faces of real people: they stay local and are git-ignored.

## Test routine

1. One person walks in, looks toward the camera for 2–3 s, and leaves. Expect a **green** `S-001 - New`.
2. Wait until they have been out of view for more than 3 s, then come back. Expect **red** `S-001 - Repeat x2`.
3. A second person does the same. Expect a **new** ID (`S-002`), not a repeat of `S-001`.
4. Repeat steps 1–3 with a cap, from further away, and under the site's lighting. Note the similarity values in `results.csv`.
5. Look at `runs/.../faces/`. Every file with the same `S-00x` should be the same person.

What to look for: the same person should score above `--auto` (0.55) and
different people below `--review` (0.40). Many **orange** labels for the same
person means the camera angle, distance or light is marginal. Different people
getting the same ID means `--auto` is too low for this camera.

## Tuning flags

| Flag | Default | When to change |
|---|---|---|
| `--min-px` | 40 | faces are far away, so everything shows `too small` (try 30) |
| `--det-size` | 640 | small faces in a 1080p/4K frame are missed (try 960; slower) |
| `--auto` / `--review` | 0.55 / 0.40 | the match thresholds (the real stack starts at 0.60 for its first week) |
| `--need-samples` / `--collect-s` | 3 / 2.0 | how many good shots are collected before matching a visit |
| `--gone-s` | 3.0 | how long someone must be out of view before a return counts as a new visit |
| `--process-fps` | 0 (unlimited) | cap the face-processing rate to save CPU/GPU |
| `--device` | auto | `gpu` to require the GPU, `cpu` to compare |
| `--no-hw-decode` | off | decode video on the CPU if GPU decoding misbehaves |

## Tests

```bash
.venv/Scripts/python -m pytest tests -q
```

## Notes

- The model weights (InsightFace buffalo_l) are licensed for **non-commercial research only**.
- The same code path (`backend/app/subjects/*`) runs in the real `subject-worker`. If results are good here, the remaining risk in the full stack is the DeepStream capture (clean pre-overlay crops), which the live GPU checklist covers.
