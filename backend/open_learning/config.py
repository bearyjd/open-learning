from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OL_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    api_token: str = Field(min_length=32)
    database_url: str = "postgresql+psycopg2://openlearning:openlearning@db:5432/openlearning"
    ai_base_url: str = "http://host.docker.internal:8080/v1"
    ai_api_key: str = "local"
    ai_model: str = "local-model"
    embedding_model: str = ""
    ai_timeout_seconds: float = Field(default=90, gt=0, le=180)
    fetch_max_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    fetch_timeout_seconds: float = Field(default=15, gt=0, le=30)
    max_document_chars: int = Field(default=60_000, ge=1000, le=200_000)
    job_lease_seconds: int = Field(default=300, ge=240)

    @field_validator("api_token")
    @classmethod
    def strong_token(cls, value: str) -> str:
        if value != value.strip() or len(set(value)) < 8 or value.lower().startswith("change"):
            raise ValueError("Use a randomly generated API token of at least 32 characters")
        return value

    @field_validator("ai_base_url")
    @classmethod
    def model_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("AI base URL must be an HTTP(S) /v1 endpoint")
        if parsed.username or parsed.password or not parsed.path.rstrip("/").endswith("/v1"):
            raise ValueError("AI base URL must end in /v1 without credentials")
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
