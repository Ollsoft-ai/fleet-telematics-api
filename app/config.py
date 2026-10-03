"""Application settings, read from ``FLEET_*`` environment variables."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from fastapi import Depends
from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLEET_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: Literal["development", "staging", "production"] = "production"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)

    jwt_secret: SecretStr
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "fleet-telematics-api"
    access_token_ttl_minutes: int = Field(default=30, ge=5, le=720)

    login_max_failed_attempts: int = Field(default=5, ge=1, le=20)
    login_lockout_minutes: int = Field(default=15, ge=1)

    telemetry_retention_days: int = Field(default=180, ge=7)

    cors_allow_origins: list[str] = Field(default_factory=list)

    dealer_webhook_url: AnyHttpUrl | None = None
    dealer_webhook_secret: SecretStr | None = None
    dealer_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    dealer_max_retries: int = Field(default=3, ge=0, le=10)

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_is_strong(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("jwt_secret must be at least 32 characters")
        return value

    @field_validator("dealer_webhook_url")
    @classmethod
    def _dealer_url_uses_https(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is not None and value.scheme != "https":
            raise ValueError("dealer_webhook_url must use https")
        return value

    @model_validator(mode="after")
    def _dealer_secret_is_set(self) -> Self:
        if self.dealer_webhook_url is not None and self.dealer_webhook_secret is None:
            raise ValueError("dealer_webhook_secret is required when dealer_webhook_url is set")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
