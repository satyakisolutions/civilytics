#!/bin/sh
# RTSP camera simulator — publishes every *.mp4 found under $VIDEO_DIR
# (and its subdirectories) into THIS deployment's mediamtx as a looping
# "live" camera feed, one stream path per file.
#
# WHY IT LIVES IN THE REPO
# This used to run from a script outside the repo on one developer machine,
# which made every other deployment depend on that machine being switched
# on and network-reachable -- a camera pointed at rtsp://<that-laptop>:8555
# fails everywhere else. Keeping it here means `--sim` produces identical
# test footage on whatever host the stack is deployed to, with no VPN, no
# cross-subnet routing and no external dependency.
#
# IT USES THE REAL CAMERA PATH
# Publishing by RTSP ANNOUNCE into mediamtx is exactly what a physical
# camera does when it publishes to nginx on :554 (nginx.conf's stream
# block forwards to mediamtx:8554). So the pipeline reads a simulated
# camera through the same code path as a real one -- the only difference
# is who originates the stream.
#
# Path name is derived from the filename: lowercased, extension stripped,
# non-alphanumeric runs collapsed to a single hyphen. E.g.
#   VID-20260131-WA0002.mp4        -> rtsp://mediamtx:8554/vid-20260131-wa0002
#
# Re-encodes (not -c copy) because some source clips (WhatsApp exports)
# carry non-standard headers that break RTSP muxing when copied verbatim.

set -eu

MEDIAMTX_HOST="${MEDIAMTX_HOST:-mediamtx}"
MEDIAMTX_PORT="${MEDIAMTX_PORT:-8554}"
VIDEO_DIR="${VIDEO_DIR:-/videos}"
FPS="${SIM_FPS:-15}"
RES="${SIM_RES:-1280:720}"
BITRATE="${SIM_BITRATE:-2500k}"

slugify() {
    # basename, strip extension, lowercase, collapse non-alnum to '-'
    base="$(basename "$1")"
    stem="${base%.*}"
    echo "$stem" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g'
}

publish() {
    file="$1"
    path="$2"
    dest="rtsp://${MEDIAMTX_HOST}:${MEDIAMTX_PORT}/${path}"
    echo "[stream.sh] publishing ${file} -> ${dest}"
    while : ; do
        ffmpeg \
            -hide_banner -loglevel warning \
            -re -stream_loop -1 \
            -i "${file}" \
            -vf "scale=${RES}:force_original_aspect_ratio=decrease,pad=${RES}:(ow-iw)/2:(oh-ih)/2,fps=${FPS}" \
            -c:v libx264 -preset veryfast -tune zerolatency \
            -b:v "${BITRATE}" -maxrate "${BITRATE}" -bufsize "${BITRATE}" \
            -pix_fmt yuv420p -g 50 -keyint_min 25 \
            -an \
            -f rtsp -rtsp_transport tcp \
            "${dest}" 2>&1 || true
        echo "[stream.sh] ${path} ffmpeg exited, restarting in 2s"
        sleep 2
    done
}

found=0
for f in $(find "${VIDEO_DIR}" -type f \( -iname '*.mp4' -o -iname '*.mov' \) | sort); do
    found=1
    path="$(slugify "$f")"
    publish "$f" "$path" &
done

if [ "$found" -eq 0 ]; then
    echo "[stream.sh] no .mp4/.mov files found under ${VIDEO_DIR} -- nothing to publish"
    exit 1
fi

echo "[stream.sh] all streams launched, waiting"
wait
