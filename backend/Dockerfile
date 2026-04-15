FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml .
RUN uv pip install --system -e .
COPY . .

RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /home/appuser/.cache /tmp/wastewatch/recordings /tmp/wastewatch/evidence \
    && chown -R appuser:appuser /home/appuser /tmp/wastewatch /app

ENV HOME=/home/appuser \
    HF_HOME=/home/appuser/.cache/huggingface \
    TRANSFORMERS_CACHE=/home/appuser/.cache/huggingface

USER appuser

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
