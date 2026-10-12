"""Worker module settings — loaded from WW_-prefixed env vars."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    conductor_server_url: str = "http://conductor:8080/api"
    conductor_auth_mode: str = "none"
    conductor_auth_token: str = ""

    kafka_brokers: str = "kafka:9092"
    kafka_dumping_events_topic: str = "civilytics.dumping.events"
    kafka_dumping_events_group_id: str = "civilytics.group.workflow-intake"
    kafka_dlq_topic: str = "civilytics.dumping.events.dlq"

    database_url: str = ""
    redis_url: str = "redis://localhost:6379/0"
    evidence_service_url: str = "http://api:8080"
    clustering_service_url: str = "http://api:8080"
    analytics_service_url: str = "http://api:8080"
    reports_service_url: str = "http://api:8080"
    alert_webhook_url: str = ""

    # ReID (moved in-process here from backend/app/api/routes/reid.py —
    # see workers/workers/tasks/run_reid.py).
    qdrant_url: str = "http://qdrant:6333"
    clip_reid_checkpoint: str = "/app/models/reid/clip_reid.pth"

    # Shared secret for worker-to-api calls — see app/auth/middleware.py.
    # Must be set (and match WW_INTERNAL_SERVICE_TOKEN on the api container)
    # for admin-gated routes (/lpr/analyze, /reports/incident-narrative, ...)
    # to work once WW_AUTH_DEV_MODE=0.
    internal_service_token: str = ""

    worker_poll_interval_ms: int = 1000
    worker_batch_size: int = 1
    worker_instance_id: str = "worker-1"

    model_config = SettingsConfigDict(env_prefix="WW_", env_file=".env")


settings = WorkerSettings()
