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

    vlm_base_url: str = "http://localhost:8000/v1"
    vlm_model: str = "Qwen/Qwen2.5-VL-7B-Instruct-AWQ"
    vlm_max_tokens: int = 512
    vlm_temperature: float = 0.1
    vlm_timeout_s: int = 30

    telegram_bot_token: str = ""
    telegram_chat_ids: list[str] = []

    # Keycloak
    keycloak_url: str = "http://localhost:8180"
    keycloak_realm: str = "wastewatch"
    keycloak_client_id: str = "wastewatch-api"
    keycloak_client_secret: str = ""

    model_config = SettingsConfigDict(env_prefix="WW_", env_file=".env")


settings = Settings()
