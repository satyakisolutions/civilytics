#!/usr/bin/env python3
"""Download SCRFD (det_10g) + ArcFace (w600k_r50) from InsightFace's buffalo_l pack.

LICENCE: InsightFace pretrained weights are for NON-COMMERCIAL RESEARCH ONLY.
A paid deployment needs a commercial licence from InsightFace or a different
model (see docs/superpowers/specs/2026-09-29-face-subject-id-design.md §13).
"""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
WANTED = ("det_10g.onnx", "w600k_r50.onnx")
DEST = Path(__file__).resolve().parent.parent / "backend" / "models" / "face"


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    if all((DEST / name).exists() for name in WANTED):
        print(f"Face models already present in {DEST}")
        return 0
    print("NOTE: InsightFace weights are licensed for non-commercial research use only.")
    print(f"Downloading {URL} ...")
    with urllib.request.urlopen(URL, timeout=300) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if name in WANTED:
                (DEST / name).write_bytes(zf.read(member))
                print(f"  wrote {DEST / name}")
    missing = [n for n in WANTED if not (DEST / n).exists()]
    if missing:
        print(f"ERROR: missing after download: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
