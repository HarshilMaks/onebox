"""Typed runtime configuration for every supported OneBox process role."""

from enum import Enum
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ServiceRole(str, Enum):
    API = "api"
    AUTOMATION_WORKER = "automation_worker"
    COMBINED = "combined"


def _require_absolute_http_url(value: str, setting_name: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{setting_name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{setting_name} must not contain credentials")
    return value.rstrip("/")


def _parse_cors_origins(raw_origins: str) -> tuple[str, ...]:
    origins: list[str] = []
    for raw_origin in raw_origins.split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("CORS_ALLOWED_ORIGINS entries must be exact HTTP(S) origins")
        if origin in origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must not contain duplicate origins")
        origins.append(origin)
    return tuple(origins)


class Settings(BaseSettings):
    """Validated process configuration loaded from the repository-root `.env` file.

    Credential values are paths to read-only files mounted by the deployment; this
    application deliberately does not implement a separate `*_FILE` indirection.
    Relative paths resolve from the repository root rather than the caller's CWD.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="forbid",
        populate_by_name=True,
    )

    # Process topology. `combined` is a local-development convenience only.
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    SERVICE_ROLE: ServiceRole = ServiceRole.API
    AUTOMATION_ENABLED: bool = False
    AUTOMATION_OWNER_ID: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("AUTOMATION_OWNER_ID", "AGENT_USER_ID_FOR_SERVICE"),
    )
    # Comma-separated UUIDs allowed to reconcile ambiguous external writes.
    # An empty list intentionally disables the operator endpoint.
    PENDING_ACTION_OPERATOR_IDS: str = ""

    # Persistent services.
    DATABASE_URL: str
    REDIS_URL: str

    # External provider isolation. Every synchronous SDK operation has a finite
    # caller deadline and bounded admission; streaming also has an idle limit.
    PROVIDER_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0, le=300)
    PROVIDER_MAX_CONCURRENCY: int = Field(default=8, ge=1, le=64)
    REDIS_MAX_CONCURRENCY: int = Field(default=16, ge=1, le=256)
    LLM_STREAM_TIMEOUT_SECONDS: float = Field(default=90.0, gt=0, le=600)
    LLM_STREAM_IDLE_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0, le=300)
    LLM_STREAM_QUEUE_SIZE: int = Field(default=32, ge=1, le=1024)

    # JWT contract. Priority 3 will enforce issuer and audience at verification.
    SECRET_KEY: str
    ALGORITHM: Literal["HS256"] = "HS256"
    JWT_ISSUER: str
    JWT_AUDIENCE: str

    # OAuth paths are deployment-mounted, read-only credential files.
    GOOGLE_OAUTH_CLIENT_SECRETS: Path
    OAUTH_REDIRECT_URI: str
    FRONTEND_OAUTH_CALLBACK_URI: str

    # Per-user OAuth tokens are encrypted with the active key from this
    # read-only JSON keyring. Leave both unset only when no credential operation
    # is enabled; OAuth connection/refresh requests then fail closed.
    OAUTH_TOKEN_KEYRING_PATH: Path | None = None
    OAUTH_TOKEN_ACTIVE_KEY_ID: str | None = None

    # Vertex/GenAI configuration. Project, location, and model have no defaults.
    GOOGLE_PROJECT_ID: str
    GOOGLE_LOCATION: str
    GOOGLE_MODEL: str
    # Omit this only when the runtime has Application Default Credentials.
    GOOGLE_APPLICATION_CREDENTIALS: Path | None = None

    # Gmail automation and authenticated Pub/Sub push configuration.
    PUBSUB_TOPIC: str | None = None
    PUBSUB_SUBSCRIPTION: str | None = None
    PUBSUB_PUSH_AUDIENCE: str | None = None
    PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL: str | None = None

    # Comma-separated exact browser origins. An empty value disables cross-origin access.
    CORS_ALLOWED_ORIGINS: str = ""

    @field_validator(
        "DATABASE_URL",
        "SECRET_KEY",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "GOOGLE_PROJECT_ID",
        "GOOGLE_LOCATION",
        "GOOGLE_MODEL",
        mode="before",
    )
    @classmethod
    def require_nonblank_string(cls, value: object, info) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be configured")
        normalized = value.strip()
        if info.field_name == "SECRET_KEY" and len(normalized.encode("utf-8")) < 32:
            raise ValueError("SECRET_KEY must be at least 32 bytes for HS256")
        return normalized

    @field_validator("PENDING_ACTION_OPERATOR_IDS", mode="before")
    @classmethod
    def validate_pending_action_operator_ids(cls, value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("PENDING_ACTION_OPERATOR_IDS must be comma-separated UUIDs")
        identities: list[str] = []
        for raw_identity in value.split(","):
            normalized = raw_identity.strip()
            if not normalized:
                continue
            try:
                parsed = UUID(normalized)
            except ValueError as exc:
                raise ValueError("PENDING_ACTION_OPERATOR_IDS entries must be UUIDs") from exc
            canonical = str(parsed)
            if canonical in identities:
                raise ValueError("PENDING_ACTION_OPERATOR_IDS must not contain duplicates")
            identities.append(canonical)
        return ",".join(identities)

    @property
    def pending_action_operator_ids(self) -> frozenset[UUID]:
        return frozenset(UUID(value) for value in self.PENDING_ACTION_OPERATOR_IDS.split(",") if value)

    @field_validator(
        "PUBSUB_TOPIC",
        "PUBSUB_SUBSCRIPTION",
        "PUBSUB_PUSH_AUDIENCE",
        "PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL",
        mode="before",
    )
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("must be a string")
        normalized = value.strip()
        return normalized or None

    @field_validator("OAUTH_REDIRECT_URI", "FRONTEND_OAUTH_CALLBACK_URI", mode="before")
    @classmethod
    def validate_redirect_url(cls, value: object, info) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be configured")
        return _require_absolute_http_url(value.strip(), info.field_name)

    @field_validator("PUBSUB_PUSH_AUDIENCE")
    @classmethod
    def validate_push_audience(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_absolute_http_url(value, "PUBSUB_PUSH_AUDIENCE")

    @field_validator("PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL")
    @classmethod
    def validate_push_service_account_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(character.isspace() for character in value) or value.count("@") != 1:
            raise ValueError("PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL must be an email address")
        return value.casefold()

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "postgresql+asyncpg" or not parsed.hostname:
            raise ValueError("DATABASE_URL must be a postgresql+asyncpg URL with a host")
        return value

    @field_validator("REDIS_URL", mode="before")
    @classmethod
    def validate_redis_url(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("REDIS_URL must be configured")
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("REDIS_URL must be a redis:// or rediss:// URL with a host")
        return normalized

    @field_validator(
        "GOOGLE_OAUTH_CLIENT_SECRETS",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "OAUTH_TOKEN_KEYRING_PATH",
        mode="before",
    )
    @classmethod
    def resolve_credential_path(cls, value: object) -> Path | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if not isinstance(value, (str, Path)):
            raise ValueError("credential path must be a path string")
        path = Path(value).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    @field_validator("OAUTH_TOKEN_ACTIVE_KEY_ID", mode="before")
    @classmethod
    def validate_oauth_token_active_key_id(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or any(character.isspace() for character in value):
            raise ValueError("OAUTH_TOKEN_ACTIVE_KEY_ID must be nonblank text without whitespace")
        return value.strip()

    @field_validator("GOOGLE_OAUTH_CLIENT_SECRETS")
    @classmethod
    def require_oauth_client_secrets_path(cls, value: Path | None) -> Path:
        if value is None:
            raise ValueError("GOOGLE_OAUTH_CLIENT_SECRETS must be configured")
        return value

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def validate_cors_origins(cls, value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("CORS_ALLOWED_ORIGINS must be comma-separated text")
        return ",".join(_parse_cors_origins(value))

    @model_validator(mode="after")
    def validate_role_requirements(self) -> "Settings":
        if (self.OAUTH_TOKEN_KEYRING_PATH is None) != (self.OAUTH_TOKEN_ACTIVE_KEY_ID is None):
            raise ValueError(
                "OAUTH_TOKEN_KEYRING_PATH and OAUTH_TOKEN_ACTIVE_KEY_ID must be configured together"
            )

        if self.SERVICE_ROLE is ServiceRole.COMBINED and self.ENVIRONMENT not in {
            Environment.DEVELOPMENT,
            Environment.TEST,
        }:
            raise ValueError("SERVICE_ROLE=combined is allowed only in development or test")

        if self.SERVICE_ROLE is ServiceRole.API and self.AUTOMATION_ENABLED:
            raise ValueError("AUTOMATION_ENABLED requires SERVICE_ROLE=automation_worker or combined")

        if self.SERVICE_ROLE is ServiceRole.AUTOMATION_WORKER and not self.AUTOMATION_ENABLED:
            raise ValueError("SERVICE_ROLE=automation_worker requires AUTOMATION_ENABLED=true")

        if self.AUTOMATION_ENABLED:
            missing = [
                name
                for name, value in (
                    ("AUTOMATION_OWNER_ID", self.AUTOMATION_OWNER_ID),
                    ("PUBSUB_TOPIC", self.PUBSUB_TOPIC),
                    ("PUBSUB_SUBSCRIPTION", self.PUBSUB_SUBSCRIPTION),
                    ("PUBSUB_PUSH_AUDIENCE", self.PUBSUB_PUSH_AUDIENCE),
                    ("PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL", self.PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    "Automation is enabled but these required settings are missing: "
                    + ", ".join(missing)
                )
            if not self.PUBSUB_TOPIC.startswith("projects/") or "/topics/" not in self.PUBSUB_TOPIC:
                raise ValueError("PUBSUB_TOPIC must be a full projects/{project}/topics/{topic} name")
            if (
                not self.PUBSUB_SUBSCRIPTION.startswith("projects/")
                or "/subscriptions/" not in self.PUBSUB_SUBSCRIPTION
            ):
                raise ValueError(
                    "PUBSUB_SUBSCRIPTION must be a full projects/{project}/subscriptions/{subscription} name"
                )

        if self.ENVIRONMENT is Environment.PRODUCTION:
            for setting_name, url in (
                ("OAUTH_REDIRECT_URI", self.OAUTH_REDIRECT_URI),
                ("FRONTEND_OAUTH_CALLBACK_URI", self.FRONTEND_OAUTH_CALLBACK_URI),
            ):
                if urlsplit(url).scheme != "https":
                    raise ValueError(f"{setting_name} must use HTTPS in production")
            if any(urlsplit(origin).scheme != "https" for origin in self.cors_allowed_origins):
                raise ValueError("CORS_ALLOWED_ORIGINS must use HTTPS in production")

        return self

    def configure_google_application_credentials(self) -> None:
        """Expose the configured read-only credential path to Google ADC clients."""
        if self.GOOGLE_APPLICATION_CREDENTIALS is not None:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(
                self.GOOGLE_APPLICATION_CREDENTIALS
            )

    @property
    def oauth_token_encryption_configured(self) -> bool:
        return (
            self.OAUTH_TOKEN_KEYRING_PATH is not None
            and self.OAUTH_TOKEN_ACTIVE_KEY_ID is not None
        )

    @property
    def cors_allowed_origins(self) -> tuple[str, ...]:
        return tuple(filter(None, self.CORS_ALLOWED_ORIGINS.split(",")))

    @property
    def runs_automation(self) -> bool:
        return self.AUTOMATION_ENABLED and self.SERVICE_ROLE in {
            ServiceRole.AUTOMATION_WORKER,
            ServiceRole.COMBINED,
        }


settings = Settings()
