import re
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OL_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
        env_ignore_empty=True,
    )
    api_token: str = Field(min_length=32)
    database_url: str = "postgresql+psycopg2://openlearning:openlearning@db:5432/openlearning"
    ai_base_url: str = "http://host.docker.internal:8080/v1"
    ai_api_key: str = "local"
    ai_model: str = "local-model"
    ai_backend: Literal["local", "openrouter"] = "local"
    cloud_opt_in: bool = False
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str | None = None
    openrouter_provider: str | None = None
    openrouter_request_budget_usd: Decimal | None = None
    openrouter_lifetime_budget_usd: Decimal | None = None
    openrouter_key_limit_usd: Decimal | None = None
    openrouter_request_fee_cap_usd: Decimal | None = None
    openrouter_output_tokens: int = Field(default=3000, ge=1, le=3000)
    embedding_model: str = ""
    ai_timeout_seconds: float = Field(default=90, gt=0, le=180)
    fetch_max_bytes: int = Field(default=2_000_000, ge=1024, le=10_000_000)
    fetch_timeout_seconds: float = Field(default=15, gt=0, le=30)
    max_document_chars: int = Field(default=60_000, ge=1000, le=200_000)
    job_lease_seconds: int = Field(default=300, ge=240)
    note_max_pending_jobs: int = Field(default=20, ge=1, le=1000)
    note_max_failed_payloads: int = Field(default=20, ge=0, le=1000)
    note_failed_payload_hours: int = Field(default=168, ge=1, le=8760)

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

    @model_validator(mode="after")
    def validate_cloud_profile(self):
        if self.ai_backend == "local":
            return self
        if (
            not self.cloud_opt_in
            or self.openrouter_api_key is None
            or not self.openrouter_api_key.get_secret_value()
        ):
            raise ValueError("OpenRouter requires explicit opt-in and a dedicated API key")
        # Provider tags are exact routing identifiers, never display names or inferred slugs.
        component = r"[a-z0-9][a-z0-9._-]*"
        if not self.openrouter_model or not re.fullmatch(
            component + "/" + component, self.openrouter_model
        ):
            raise ValueError("OpenRouter model must be an exact two-component model ID")
        if not self.openrouter_provider or not re.fullmatch(
            component + r"(?:/" + component + r")*", self.openrouter_provider
        ):
            raise ValueError("OpenRouter provider must be an exact provider tag")
        names = (
            "openrouter_request_budget_usd",
            "openrouter_lifetime_budget_usd",
            "openrouter_key_limit_usd",
            "openrouter_request_fee_cap_usd",
        )
        for name in names:
            value = getattr(self, name)
            if (
                value is None
                or not value.is_finite()
                or value < 0
                or (value == 0 and name != "openrouter_request_fee_cap_usd")
                or value.as_tuple().exponent < -12
                or value >= Decimal("1e18")
            ):
                raise ValueError(f"{name} must be a bounded finite Decimal with at most 12 places")
        if self.openrouter_request_budget_usd > self.openrouter_lifetime_budget_usd:
            raise ValueError("OpenRouter request budget exceeds lifetime budget")
        if self.openrouter_key_limit_usd > self.openrouter_lifetime_budget_usd:
            raise ValueError("OpenRouter key limit exceeds lifetime budget")
        return self

    @property
    def effective_ai_model(self) -> str:
        return self.openrouter_model if self.ai_backend == "openrouter" else self.ai_model

    @property
    def effective_ai_endpoint(self) -> str:
        return (
            "https://openrouter.ai/api/v1" if self.ai_backend == "openrouter" else self.ai_base_url
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
