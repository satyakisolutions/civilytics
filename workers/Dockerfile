FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml .
RUN uv pip install --system -e .
COPY . .
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser:appuser /app
USER appuser
CMD ["python3", "-m", "workers.main"]
