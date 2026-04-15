FROM nvcr.io/nvidia/deepstream:7.0-triton-multiarch
# python3-gst-1.0 ships /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstpython.so,
# which is the loader that discovers Python-based GStreamer plugins (e.g. the
# wastewatchvlmpublisher sink). Without it, Gst.parse_launch() cannot resolve
# the element factory even after the plugin file is placed on GST_PLUGIN_PATH.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-dev libgstreamer1.0-dev \
    python3-gst-1.0 python3-gi python3-gi-cairo \
    gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
    gstreamer1.0-python3-plugin-loader gstreamer1.0-libav \
    curl ca-certificates \
 && apt-get install -y --reinstall \
    libavcodec58 libavformat58 libavutil56 libswscale5 libswresample3 \
    libvpx7 libx264-163 libx265-199 libmpg123-0 \
 && rm -rf /var/lib/apt/lists/* \
 && printf "/usr/lib/x86_64-linux-gnu\n" > /etc/ld.so.conf.d/ubuntu-multiarch.conf \
 && ldconfig

# DeepStream 7.0 base image doesn't ship pyds (Python bindings for DeepStream);
# it has to be pulled from NVIDIA-AI-IOT/deepstream_python_apps releases.
# v1.2.0 is the DS 7.0-compatible wheel built against cp310 (Ubuntu 22.04).
RUN curl -fsSL -o /tmp/pyds-1.2.0-cp310-cp310-linux_x86_64.whl \
    https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/download/v1.2.0/pyds-1.2.0-cp310-cp310-linux_x86_64.whl \
 && pip3 install --no-cache-dir /tmp/pyds-1.2.0-cp310-cp310-linux_x86_64.whl \
 && rm /tmp/pyds-1.2.0-cp310-cp310-linux_x86_64.whl
# pyds links against libcuda.so.1 which is only visible at runtime via
# the nvidia-container-toolkit device mounts, so we don't verify the
# import during the build step — trying to `python3 -c "import pyds"`
# here fails with "libcuda.so.1: cannot open shared object file".
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . .

# SECURITY: Run as non-root user (GPU access via device mapping, not root)
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

CMD ["python3", "-u", "entrypoint.py"]
