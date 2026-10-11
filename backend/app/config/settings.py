from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = ""  # SECURITY: Must be set via WW_DATABASE_URL — no default credentials
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    mediamtx_api_url: str = "http://localhost:9997"
    mediamtx_rtsp_url: str = "rtsp://localhost:8554"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    vlm_model: str = "HuggingFaceTB/SmolVLM2-2.2B-Instruct"
    vlm_max_tokens: int = 512
    vlm_temperature: float = 0.1

    telegram_bot_token: str = ""
    telegram_chat_ids: list[str] = []

    # IP Speaker
    ip_speaker_configs: list[dict] = []  # List of SpeakerConfig dicts
    ip_speaker_enabled: bool = False

    # SIP Gateway
    sip_server: str = ""
    sip_username: str = ""
    sip_password: str = ""
    sip_domain: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    sip_escalation_contacts: list[dict] = []
    sip_enabled: bool = False

    # SMTP (Email reports)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "wastewatch@satyaki.co.in"
    smtp_tls: bool = True
    smtp_enabled: bool = False

    # Keycloak
    keycloak_url: str = "http://localhost:8180"
    keycloak_realm: str = "wastewatch"
    keycloak_client_id: str = "wastewatch-api"
    keycloak_client_secret: str = ""
    # Cookie-based session for the branded login form (see api/routes/auth.py).
    # auth_cookie_secure MUST be true in production -- without it the browser
    # will send the session cookie over plain HTTP.
    auth_cookie_secure: bool = True
    auth_cookie_samesite: str = "lax"
    auth_cookie_domain: str = ""

    # Conductor OSS
    conductor_server_url: str = "http://localhost:8080/api"
    conductor_auth_mode: str = ""
    conductor_auth_token: str = ""

    # Static file roots. Defaults are the in-container paths; overridable so
    # the test suite can import the app outside Docker, where /app is not
    # writable.
    evidence_dir: str = "/app/evidence"
    tts_dir: str = "/tmp/wastewatch/tts"

    # Conductor OSS
    conductor_server_url: str = "http://localhost:8080/api"
    conductor_auth_mode: str = "none"
    conductor_auth_token: str = ""

    # Workflow Operations
    evidence_service_url: str = "http://localhost:8090"
    lpr_service_url: str = "http://localhost:8091"
    clustering_service_url: str = "http://localhost:8092"
    analytics_service_url: str = "http://localhost:8093"
    alert_webhook_url: str = ""
    app_base_url: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_prefix="WW_", env_file=".env")


settings = Settings()
