import asyncio
import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BASE_SETTINGS = {
    "DATABASE_URL": "postgresql+asyncpg://onebox:test-password@localhost:5432/onebox",
    "REDIS_URL": "redis://localhost:6379/0",
    "SECRET_KEY": "a" * 32,
    "JWT_ISSUER": "https://issuer.example.invalid",
    "JWT_AUDIENCE": "onebox-api",
    "GOOGLE_OAUTH_CLIENT_SECRETS": "test-oauth-client.json",
    "OAUTH_REDIRECT_URI": "http://localhost:8000/agent/oauth/callback",
    "FRONTEND_OAUTH_CALLBACK_URI": "http://localhost:3000/mail/inbox",
    "GOOGLE_PROJECT_ID": "onebox-test-project",
    "GOOGLE_LOCATION": "us-central1",
    "GOOGLE_MODEL": "gemini-test-model",
}


@pytest.fixture
def settings_class(monkeypatch):
    for key, value in {
        **BASE_SETTINGS,
        "ENVIRONMENT": "development",
        "SERVICE_ROLE": "api",
        "AUTOMATION_ENABLED": "false",
        "ALGORITHM": "HS256",
        "CORS_ALLOWED_ORIGINS": "",
    }.items():
        monkeypatch.setenv(key, value)
    for key in (
        "AUTOMATION_ENABLED",
        "AUTOMATION_OWNER_ID",
        "AGENT_USER_ID_FOR_SERVICE",
        "PUBSUB_TOPIC",
        "PUBSUB_SUBSCRIPTION",
        "PUBSUB_PUSH_AUDIENCE",
        "PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL",
    ):
        monkeypatch.delenv(key, raising=False)

    sys.modules.pop("server.config", None)
    return importlib.import_module("server.config").Settings


def build_settings(settings_class, **overrides):
    values = dict(BASE_SETTINGS)
    values.update(overrides)
    return settings_class(_env_file=None, **values)


def test_api_role_does_not_require_automation_settings(settings_class):
    settings = build_settings(
        settings_class,
        CORS_ALLOWED_ORIGINS="http://localhost:3000/, https://admin.example.invalid",
    )

    assert settings.runs_automation is False
    assert settings.AUTOMATION_OWNER_ID is None
    assert settings.cors_allowed_origins == (
        "http://localhost:3000",
        "https://admin.example.invalid",
    )


def test_worker_fails_closed_when_automation_configuration_is_incomplete(settings_class):
    with pytest.raises(ValidationError, match="Automation is enabled but these required settings are missing"):
        build_settings(
            settings_class,
            SERVICE_ROLE="automation_worker",
            AUTOMATION_ENABLED=True,
        )


@pytest.mark.parametrize("role", ("automation_worker", "combined"))
def test_automation_roles_accept_complete_legacy_owner_setting(settings_class, role):
    owner_id = "ec875654-f1c4-4512-a47d-f82a8b4765fd"
    settings = build_settings(
        settings_class,
        SERVICE_ROLE=role,
        AUTOMATION_ENABLED=True,
        AGENT_USER_ID_FOR_SERVICE=owner_id,
        PUBSUB_TOPIC="projects/onebox-test-project/topics/gmail-notifications",
        PUBSUB_SUBSCRIPTION="projects/onebox-test-project/subscriptions/gmail-notifications",
        PUBSUB_PUSH_AUDIENCE="https://api.example.invalid/mail/notifications",
        PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL="pubsub-push@onebox-test-project.iam.gserviceaccount.com",
    )

    assert settings.runs_automation is True
    assert settings.AUTOMATION_OWNER_ID == UUID(owner_id)


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"AUTOMATION_OWNER_ID": "not-a-uuid"}, "valid UUID"),
        ({"REDIS_URL": "not-a-redis-url"}, "REDIS_URL must be a redis"),
        ({"CORS_ALLOWED_ORIGINS": "https://example.invalid/path"}, "exact HTTP"),
        ({"ALGORITHM": "RS256"}, "HS256"),
    ],
)
def test_malformed_runtime_values_are_rejected(settings_class, overrides, error):
    with pytest.raises(ValidationError, match=error):
        build_settings(settings_class, **overrides)


def test_unknown_environment_file_values_are_rejected(settings_class, tmp_path):
    environment_file = tmp_path / ".env"
    environment_file.write_text("UNDOCUMENTED_SETTING=unexpected\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="UNDOCUMENTED_SETTING"):
        settings_class(_env_file=environment_file, **BASE_SETTINGS)


def test_production_requires_secure_redirects_and_a_strong_secret(settings_class):
    with pytest.raises(ValidationError, match="SECRET_KEY must be at least 32 characters"):
        build_settings(
            settings_class,
            ENVIRONMENT="production",
            SECRET_KEY="too-short",
            OAUTH_REDIRECT_URI="https://api.example.invalid/agent/oauth/callback",
            FRONTEND_OAUTH_CALLBACK_URI="https://app.example.invalid/mail/inbox",
            CORS_ALLOWED_ORIGINS="https://app.example.invalid",
        )


def test_configuration_import_uses_repository_root_not_current_directory(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            **BASE_SETTINGS,
            "ENVIRONMENT": "development",
            "SERVICE_ROLE": "api",
            "AUTOMATION_ENABLED": "false",
            "ALGORITHM": "HS256",
            "CORS_ALLOWED_ORIGINS": "",
        }
    )
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from server.config import PROJECT_ROOT, settings; "
            "assert PROJECT_ROOT == settings.GOOGLE_OAUTH_CLIENT_SECRETS.parent",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_api_lifespan_does_not_initialize_gmail_automation(settings_class, monkeypatch):
    # Import only after the fixture has supplied a complete API configuration.
    main = importlib.import_module("server.main")
    initialize = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "initialize_gmail_service", initialize)
    monkeypatch.setattr(main, "settings", SimpleNamespace(runs_automation=False))

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            pass

    asyncio.run(exercise_lifespan())
    initialize.assert_not_awaited()
