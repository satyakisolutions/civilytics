FROM nvcr.io/nvidia/deepstream:9.1-triton-multiarch
# python3-gst-1.0 ships /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstpython.so,
# which is the loader that discovers Python-based GStreamer plugins (e.g. the
# wastewatchvlmpublisher sink). Without it, Gst.parse_launch() cannot resolve
# the element factory even after the plugin file is placed on GST_PLUGIN_PATH.
#
# UPGRADE NOTE (DS 7.0 -> 9.1): the base OS jumped Ubuntu 22.04 -> 24.04.
# The old DS7-era block that force-reinstalled exact libav*/libx26*/libvpx*
# package+SONAME versions (pinned to Ubuntu 22.04's ffmpeg 4.x branch) has
# been REMOVED, not just re-pinned -- verified empirically that on this
# base image, the `ffmpeg`/`gstreamer1.0-libav` install below already pulls
# in a correct, working libav stack (libavcodec60, libavformat60, etc, all
# Ubuntu 24.04 / ffmpeg 6.1.1 versions) on its own. A wildcard reinstall
# attempt actively broke the build here: `libavcodec*` also matches the
# `libavcodec-extra60` package, which apt reports as an unmet-dependency
# conflict with the already-installed `libavcodec60` ("held broken
# packages"). If a real ffmpeg/gstreamer ABI mismatch resurfaces later,
# diagnose it directly rather than reintroducing a blind reinstall step.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-dev libgstreamer1.0-dev \
    python3-gst-1.0 python3-gi python3-gi-cairo \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-python3-plugin-loader gstreamer1.0-libav \
    ffmpeg \
    curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && printf "/usr/lib/x86_64-linux-gnu\n" > /etc/ld.so.conf.d/ubuntu-multiarch.conf \
 && ldconfig

# Silence gst-plugin-scanner's "Failed to load plugin ... cannot open
# shared object file" warnings on startup — DS9.1's base image ships
# GStreamer plugins (mp3, DVD, DTS, fax modem, Intel QSV, libav muxing,
# rivermax NIC offload) this pipeline never uses (RTSP H264 in, H264
# out only), so their transitive codec libs were never installed.
# Purely cosmetic (the scanner just skips the plugin and moves on) but
# noisy in every container log. Installs only whatever candidate names
# actually resolve on this base image/Ubuntu release, so a stale or
# wrong guess here can never fail the build.
RUN apt-get update \
 && for pkg in libmp3lame0 libdvdnav4 libdvdread8 flac libspandsp2 \
               libfluidsynth3 libvpx9 libmpg123-0t64 libmpg123-0 libdca0 \
               libopenmpt-modplug1 mjpegtools; do \
      apt-get install -y --no-install-recommends "$pkg" 2>/dev/null || true; \
    done \
 && rm -rf /var/lib/apt/lists/* \
 && ldconfig

# UPGRADE NOTE (DS 7.0 -> 9.1): DS 7.0's base image didn't bundle pyds
# (classic DeepStream Python bindings) at all, hence a manual GitHub-wheel
# curl previously. Confirmed empirically that the 9.1-triton-multiarch
# image doesn't bundle *built* pyds either, but DOES ship NVIDIA's own
# installer script (user_deepstream_python_apps_install.sh) plus the
# deepstream_python_apps source tree under
# /opt/nvidia/deepstream/deepstream/sources/deepstream_python_apps. Using
# --build-bindings (compile from source against this exact image's
# TensorRT/CUDA/DeepStream libraries) instead of downloading a prebuilt
# wheel -- guarantees ABI correctness instead of hoping a GitHub release
# wheel (the latest, v1.2.2, targets DS 8.0, not confirmed for DS 9.1's
# libnvds_*.so) happens to be compatible. The script self-installs its own
# build dependencies (meson, cmake, ninja, g++, etc) via apt, so no
# preceding RUN step is needed for those.
RUN /opt/nvidia/deepstream/deepstream/user_deepstream_python_apps_install.sh --build-bindings
# The script's own `pip3 install` step lands pyds under a non-standard
# prefix (/opt/nvidia/deepstream/deepstream-9.1/sources/deepstream_python_apps/pyds/lib/python3.12/site-packages/),
# which is not on sys.path at runtime -- confirmed via
# `python3 -c "import pyds"` failing with ModuleNotFoundError even though
# the build log reports "Successfully installed pyds-1.2.3". Root cause
# not fully identified (no PYTHONUSERBASE/PIP_TARGET env var found to
# explain it), but the built wheel is left at a stable, known path
# afterward -- reinstalling it explicitly into the normal system
# site-packages is a reliable fix regardless of why pip redirected it the
# first time.
RUN pip3 install --no-cache-dir --force-reinstall \
    /opt/nvidia/deepstream/deepstream-9.1/sources/deepstream_python_apps/bindings/dist/pyds-*.whl
# pyds links against libcuda.so.1 which is only visible at runtime via
# the nvidia-container-toolkit device mounts, so we don't verify the
# import during the build step — trying to `python3 -c "import pyds"`
# here fails with "libcuda.so.1: cannot open shared object file".

# We need g++ + the DeepStream TensorRT headers to compile
# marcoslucianops' custom bbox parser library
# (nvdsinfer_custom_impl_Yolo), and gstreamer1.0-rtsp for the
# `rtspclientsink` used by pipeline/app/output_manager.py.
#
# The deepstream:7.0-triton base image added a `librealsense.intel.com`
# apt source with an expired GPG key, which broke `apt-get update` on
# this layer. UPGRADE NOTE (DS 7.0 -> 9.1): unverified whether the
# 9.1-triton base image has the same stale source -- this cleanup is a
# harmless no-op if it doesn't (find/grep just match nothing), so kept
# defensively rather than removed outright.
RUN find /etc/apt/sources.list.d -maxdepth 1 -type f \
        \( -name '*realsense*' -o -name '*librealsense*' \) -delete \
 && grep -rl librealsense /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null | xargs --no-run-if-empty rm -f \
 && apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gstreamer1.0-rtsp \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . .

# Kannada-capable font for nvosd -- the container only ships DejaVu (Latin)
# by default, so a Kannada display_text renders as tofu boxes without this.
RUN mkdir -p /usr/share/fonts/truetype/noto \
 && cp /app/fonts/NotoSansKannada.ttf /usr/share/fonts/truetype/noto/ \
 && fc-cache -f /usr/share/fonts/truetype/noto

# Compile marcoslucianops' custom bbox parser
# (nvdsinfer_custom_impl_Yolo) into a .so that DeepStream's nvinfer
# loads via `custom-lib-path`. The lib ships a `NvDsInferParseYolo`
# C function that handles the YOLO-compatible [num_queries, 6]
# (x1, y1, x2, y2, score, classId) layout — exactly what our
# scripts/onnx_to_engine.py DeepStreamOutput head appends to the
# raw RF-DETR ONNX.
#
# UPGRADE NOTE (DS 7.0 -> 9.1): DS 7.0 bundled CUDA 12.2 under
# /usr/local/cuda-12.2, hence CUDA_VER=12.2 previously. Confirmed
# empirically (not the deepstream-dev skill's assumed 13.1, which was
# DS 9.0's documented version, not 9.1's actual one) that the
# 9.1-triton-multiarch image bundles CUDA under /usr/local/cuda-13.2
# (verified via `find / -iname cuda_runtime_api.h` and `ls -d /usr/local/cuda*`
# inside the image), hence CUDA_VER=13.2.
RUN make -C nvdsinfer_custom_impl_Yolo CUDA_VER=13.2 \
 && ls -la nvdsinfer_custom_impl_Yolo/libnvdsinfer_custom_impl_Yolo.so

# Compile the RF-DETR-Seg instance-mask parser
# (nvdsinfer_custom_impl_Seg) — same CUDA_VER as above, see note.
# Loaded via parse-bbox-instance-mask-func-name in
# nvinfer_rfdetr_seg.txt when WW_USE_SEG_MODEL=1.
RUN make -C nvdsinfer_custom_impl_Seg CUDA_VER=13.2 \
 && ls -la nvdsinfer_custom_impl_Seg/libnvdsinfer_custom_impl_Seg.so

# SECURITY: Run as non-root user (GPU access via device mapping, not root)
# /app/models needs to be writable too — nvinfer serializes freshly-built
# TensorRT engines (e.g. action_classifier.onnx_b16_gpu0_fp16.engine) back
# next to the source .onnx on first run against a new TensorRT version.
RUN useradd --no-create-home --shell /bin/false appuser \
 && mkdir -p /app/evidence /tmp/wastewatch /tmp/wastewatch/.cache /app/faces_private/incoming /app/faces_private/subjects \
 && chown -R appuser:appuser /app/evidence /tmp/wastewatch /app/models /app/faces_private \
 && chmod -R 0777 /app/faces_private
USER appuser
# --no-create-home leaves $HOME unset/pointing at a nonexistent
# /home/appuser — GLib (used by GStreamer plugins) falls back to
# ~/.cache for its dconf settings store and warns
# "unable to create directory '/home/appuser/.cache/dconf': Permission
# denied" on every plugin load. Harmless (dconf isn't used for anything
# this pipeline reads), but point it at the one dir appuser can write.
ENV XDG_CACHE_HOME=/tmp/wastewatch/.cache

CMD ["python3", "-u", "entrypoint.py"]
