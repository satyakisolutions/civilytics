"""Standalone face-ID lab.

Runs the face subject-ID feature's own detection, quality gate, embedding and
matching code (backend/app/subjects) against an RTSP camera, a video file or a
webcam -- without Kafka, Postgres, Qdrant, Docker or DeepStream -- so face
detection and repeat matching can be checked on a local machine first.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_MODELS_DIR = BACKEND_DIR / "models" / "face"

# Reuse the feature code as-is: app.subjects.{detector,embedder,quality,matcher,config}
# only need numpy/cv2/onnxruntime, none of the backend services.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
