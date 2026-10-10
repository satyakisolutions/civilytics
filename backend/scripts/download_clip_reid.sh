#!/usr/bin/env bash
# Download CLIP-ReID ViT-B/16 pretrained weights (optional upgrade over vanilla CLIP).
# Usage: bash backend/scripts/download_clip_reid.sh [output_path]
# Default output: /app/models/clip_reid.pth (matches CLIP_REID_CHECKPOINT env default)
set -euo pipefail

DEST="${1:-/app/models/reid/clip_reid.pth}"
mkdir -p "$(dirname "$DEST")"

# CLIP-ReID market1501 ViT-B/16 checkpoint from syliz517/clip-reid releases
URL="https://github.com/syliz517/CLIP-ReID/releases/download/v1.0/ViT-B-16_60.pth"

echo "Downloading CLIP-ReID ViT-B/16 → $DEST"
curl -fSL --retry 3 -o "$DEST" "$URL"
echo "Done. Set CLIP_REID_CHECKPOINT=$DEST if non-default path."
