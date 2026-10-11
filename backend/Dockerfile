FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY pyproject.toml .
RUN uv pip install --system -e .
COPY . .

RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /home/appuser/.cache /tmp/wastewatch/recordings /tmp/wastewatch/evidence /app/faces_private/incoming /app/faces_private/subjects \
    && chown -R appuser:appuser /home/appuser /tmp/wastewatch /app \
    && chmod -R 0777 /app/faces_private

ENV HOME=/home/appuser \
    HF_HOME=/home/appuser/.cache/huggingface \
    TRANSFORMERS_CACHE=/home/appuser/.cache/huggingface

USER appuser

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
