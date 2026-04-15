#!/bin/sh
# LSG RTSP streamer (inspired by StreamingScripts/rtsp-streamer).
# Publishes 6 sample MP4 files into the LSG mediamtx service as RTSP streams.
# Each stream loops forever via -stream_loop -1.
# Re-encode to H.264 (not -c copy) to normalise stream parameters — some of the
# downloaded WhatsApp clips have non-standard headers that break RTSP mux.

set -eu

MEDIAMTX_HOST="${MEDIAMTX_HOST:-mediamtx}"
MEDIAMTX_PORT="${MEDIAMTX_PORT:-8554}"
VIDEO_DIR="${VIDEO_DIR:-/videos}"

publish_one() {
    cam="$1"
    file="$2"
    dest="rtsp://${MEDIAMTX_HOST}:${MEDIAMTX_PORT}/${cam}"
    echo "[stream.sh] publishing ${file} -> ${dest}"
    while : ; do
        ffmpeg \
            -hide_banner -loglevel warning \
            -re -stream_loop -1 \
            -i "${VIDEO_DIR}/${file}" \
            -c:v libx264 -preset veryfast -tune zerolatency \
            -pix_fmt yuv420p -g 50 -keyint_min 25 \
            -an \
            -f rtsp -rtsp_transport tcp \
            "${dest}" 2>&1 || true
        echo "[stream.sh] ${cam} ffmpeg exited, restarting in 2s"
        sleep 2
    done
}

# Only cam-01 is a LIVE RTSP source.
# The other videos (VID-20260131-WA0003..WA0007) are used as historical
# recorded footage by the VLM worker, NOT streamed live.
publish_one "cam-01" "VID-20260131-WA0002.mp4" &

echo "[stream.sh] live publisher launched for cam-01, waiting"
wait
