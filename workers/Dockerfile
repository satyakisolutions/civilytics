FROM python:3.12-slim
WORKDIR /app
# git: a dependency (clip) installs from a git URL; build-essential: native wheels
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY pyproject.toml .
RUN uv pip install --system -e .
COPY . .
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser
CMD ["python3", "-m", "workers.main"]
