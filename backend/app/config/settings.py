from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/wastewatch"
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    mediamtx_api_url: str = "http://localhost:9997"
    mediamtx_rtsp_url: str = "rtsp://localhost:8554"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    model_config = SettingsConfigDict(env_prefix="WW_", env_file=".env")


settings = Settings()
