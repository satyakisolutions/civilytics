#!/bin/sh
# LSG RTSP streamer — publishes one live cam-01 source in three
# transcoded tiers so the frontend's StreamQualityToggle can serve
# S / M / L variants with one real-time re-encode pass:
#
#   rtsp://mediamtx:8554/cam-01        large  : 1280x720 @ 25fps, ~3500 kbps
#   rtsp://mediamtx:8554/cam-01-md     medium :  854x480 @ 20fps, ~1200 kbps
#   rtsp://mediamtx:8554/cam-01-sm     small  :  426x240 @ 15fps, ~ 500 kbps
#
# Re-encoded from the source MP4 (not -c copy) to normalise stream
# parameters — the WhatsApp-exported clips have non-standard headers
# that break RTSP mux.

set -eu

MEDIAMTX_HOST="${MEDIAMTX_HOST:-mediamtx}"
MEDIAMTX_PORT="${MEDIAMTX_PORT:-8554}"
VIDEO_DIR="${VIDEO_DIR:-/videos}"

# ffmpeg params per tier: "width:height fps v_bitrate v_bufsize"
#
# IMPORTANT — fps note: the LSG RF-DETR FP16 engine runs at ~16 fps on
# the deployment 3060. With multiple cameras competing for that single
# inference slot (engine batch=1 today, capped by Roboflow's static-
# batch ONNX export), each camera should publish at ~8 fps to keep
# inference 1:1 with frames. Going higher just queues frames that
# never get fresh detections — perceived as bbox flicker on the
# annotated cam-XX-out streams.
# Dropped to 8fps across the board (from 12.5) once cam-03/cam-04 were
# added -- 4 sources x 3-tier/1-tier ffmpeg software x264 encodes on one
# host CPU (6 concurrent processes) was measured at ~315% CPU via
# `docker stats`, and visible frame corruption on the annotated output
# only started after going from 1 to 3 registered cameras. This matches
# the documented cam-02 history below (multi-stream CPU contention on
# software x264, not a DeepStream/streammux issue -- nvinfer batch-size
# is pinned to 1 for a real, separate reason, see config.py, don't touch
# it to "fix" this).
TIER_LARGE="1280:720 8 3500k 7000k"
TIER_MEDIUM="854:480 8 1200k 2400k"
TIER_SMALL="426:240 8 500k 1000k"
# cam-02's subject moves fast (vehicle). History on this host: 30fps was
# tried and reverted (destabilized the ffmpeg encode/publish loop @
# 3500k/1280x720, starved the RTSP source read, camera marked DEGRADED,
# output stream froze). 16fps was tried next, also corrupted under this
# host's GPU load. 15fps was the last confirmed-stable baseline; dropped
# back to 8fps here to match the other tiers now that 4 sources share
# the same host CPU for software encoding.
TIER_LARGE_FAST="1280:720 8 3500k 7000k"

publish_tier() {
    cam="$1"
    file="$2"
    label="$3"
    params="$4"
    dest="rtsp://${MEDIAMTX_HOST}:${MEDIAMTX_PORT}/${cam}"
    # shellcheck disable=SC2086
    set -- $params
    width_height="$1"; fps="$2"; vbr="$3"; vbuf="$4"
    echo "[stream.sh] publishing ${file} -> ${dest} (${label}: ${width_height}@${fps}fps ${vbr})"
    while : ; do
        ffmpeg \
            -hide_banner -loglevel warning \
            -re -stream_loop -1 -r 25 \
            -i "${VIDEO_DIR}/${file}" \
            -vf "scale=${width_height}:force_original_aspect_ratio=decrease,pad=${width_height}:(ow-iw)/2:(oh-ih)/2,fps=${fps}" \
            -c:v libx264 -preset veryfast -tune zerolatency \
            -b:v "${vbr}" -maxrate "${vbr}" -bufsize "${vbuf}" \
            -pix_fmt yuv420p -g 50 -keyint_min 25 \
            -an \
            -f rtsp -rtsp_transport tcp \
            "${dest}" 2>&1 || true
        echo "[stream.sh] ${cam} ffmpeg exited, restarting in 2s"
        sleep 2
    done
}

# cam-01 — daytime colour feed (1st Cross). Three transcoded tiers
# for the frontend quality toggle on the raw input path; the
# DeepStream pipeline's annotated `cam-01-out` stream is separate
# and always served at the large tier.
SRC_FILE="${LSG_LIVE_SRC:-VID-20260131-WA0002.mp4}"

publish_tier "cam-01"    "${SRC_FILE}" "large"  "${TIER_LARGE}"  &
publish_tier "cam-01-md" "${SRC_FILE}" "medium" "${TIER_MEDIUM}" &
publish_tier "cam-01-sm" "${SRC_FILE}" "small"  "${TIER_SMALL}"  &

# cam-02 — native black-and-white IR CCTV feed (2nd Cross, night
# mode; WA0002 is already monochrome so no extra ffmpeg filtering
# needed). Single tier only — quality toggle falls through.
SRC_FILE_2="${LSG_LIVE_SRC_2:-VID-20260131-WA0002.mp4}"
publish_tier "cam-02" "${SRC_FILE_2}" "large" "${TIER_LARGE_FAST}" &

# cam-03 / cam-04 — new feeds, single tier each.
SRC_FILE_3="${LSG_LIVE_SRC_3:-VID-20260131-WA0012.mp4}"
publish_tier "cam-03" "${SRC_FILE_3}" "large" "${TIER_LARGE}" &

SRC_FILE_4="${LSG_LIVE_SRC_4:-VID-20260131-WA0013.mp4}"
publish_tier "cam-04" "${SRC_FILE_4}" "large" "${TIER_LARGE}" &

echo "[stream.sh] tiers launched for cam-01 (3) + cam-02 (1) + cam-03 (1) + cam-04 (1), waiting"
wait
