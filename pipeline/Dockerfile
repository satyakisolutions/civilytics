FROM nvcr.io/nvidia/deepstream:7.0-triton-multiarch
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-dev libgstreamer1.0-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . .

# SECURITY: Run as non-root user (GPU access via device mapping, not root)
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

CMD ["python3", "-u", "entrypoint.py"]
