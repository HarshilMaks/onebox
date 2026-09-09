import importlib
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from pydantic import ValidationError


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
def auth_module(monkeypatch):
    for key, value in {
        **BASE_SETTINGS,
        "ENVIRONMENT": "test",
        "SERVICE_ROLE": "api",
        "AUTOMATION_ENABLED": "false",
        "ALGORITHM": "HS256",
        "CORS_ALLOWED_ORIGINS": "",
    }.items():
        monkeypatch.setenv(key, value)
    for key in (
        "AUTOMATION_OWNER_ID",
        "AGENT_USER_ID_FOR_SERVICE",
        "PUBSUB_TOPIC",
        "PUBSUB_SUBSCRIPTION",
        "PUBSUB_PUSH_AUDIENCE",
        "PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(key, raising=False)

    sys.modules.pop("server.services.setup_google", None)
    sys.modules.pop("server.auth", None)
    sys.modules.pop("server.config", None)
    return importlib.import_module("server.services.setup_google")


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _token(auth_module, **overrides) -> tuple[str, UUID]:
    user_id = overrides.pop("user_id", uuid4())
    payload = {
        "sub": str(user_id),
        "email": " Person@Example.COM ",
        "iss": auth_module.settings.JWT_ISSUER,
        "aud": auth_module.settings.JWT_AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    payload.update(overrides)
    return (
        jwt.encode(payload, auth_module.settings.SECRET_KEY, algorithm=auth_module.settings.ALGORITHM),
        user_id,
    )


def _assert_unauthorized(auth_module, token: str | None) -> None:
    credentials = _credentials(token) if token is not None else None
    with pytest.raises(HTTPException) as raised:
        auth_module.get_current_user_info(credentials)
    assert raised.value.status_code == 401
    assert raised.value.detail == "Could not validate credentials"


def test_valid_token_returns_immutable_normalized_principal(auth_module):
    token, user_id = _token(auth_module)

    principal = auth_module.get_current_user_info(_credentials(token))

    assert principal.user_id == user_id
    assert principal.email == "person@example.com"
    assert principal["user_id"] == user_id
    assert principal["email"] == "person@example.com"
    assert principal.claims["sub"] == str(user_id)
    with pytest.raises(FrozenInstanceError):
        principal.email = "other@example.com"
    with pytest.raises(TypeError):
        principal.claims["sub"] = str(uuid4())


def test_missing_exp_claim_returns_stable_401(auth_module):
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "email": "person@example.com",
            "iss": auth_module.settings.JWT_ISSUER,
            "aud": auth_module.settings.JWT_AUDIENCE,
        },
        auth_module.settings.SECRET_KEY,
        algorithm="HS256",
    )

    _assert_unauthorized(auth_module, token)


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": None},
        {"exp": datetime.now(timezone.utc) - timedelta(minutes=5)},
        {"sub": None},
        {"sub": ""},
        {"sub": 1},
        {"sub": "not-a-uuid"},
        {"email": None},
        {"email": "   "},
        {"email": 1},
    ],
)
def test_missing_or_malformed_required_claims_return_stable_401(auth_module, overrides):
    token, _ = _token(auth_module, **overrides)

    _assert_unauthorized(auth_module, token)


def test_missing_bearer_credentials_return_stable_401(auth_module):
    _assert_unauthorized(auth_module, None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://wrong-issuer.example.invalid"},
        {"aud": "wrong-audience"},
    ],
)
def test_wrong_issuer_or_audience_returns_stable_401(auth_module, overrides):
    token, _ = _token(auth_module, **overrides)

    _assert_unauthorized(auth_module, token)


def test_wrong_algorithm_and_signature_return_stable_401(auth_module):
    token, _ = _token(auth_module)
    wrong_algorithm_token = jwt.encode(
        {
            "sub": str(uuid4()),
            "email": "person@example.com",
            "iss": auth_module.settings.JWT_ISSUER,
            "aud": auth_module.settings.JWT_AUDIENCE,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        auth_module.settings.SECRET_KEY,
        algorithm="HS384",
    )
    wrong_signature_token = jwt.encode(
        {
            "sub": str(uuid4()),
            "email": "person@example.com",
            "iss": auth_module.settings.JWT_ISSUER,
            "aud": auth_module.settings.JWT_AUDIENCE,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        "b" * 32,
        algorithm="HS256",
    )

    _assert_unauthorized(auth_module, wrong_algorithm_token)
    _assert_unauthorized(auth_module, wrong_signature_token)
    header_and_payload, signature = token.rsplit(".", 1)
    _assert_unauthorized(auth_module, f"{header_and_payload}.{'A' * len(signature)}")


def test_http_exception_is_not_rewritten_to_500(auth_module, monkeypatch):
    expected = HTTPException(status_code=401, detail="Could not validate credentials")

    def raise_unauthorized(*_args, **_kwargs):
        raise expected

    monkeypatch.setattr(auth_module.jwt, "decode", raise_unauthorized)
    with pytest.raises(HTTPException) as raised:
        auth_module.get_current_user_info(_credentials("synthetic-token"))

    assert raised.value is expected


def test_short_hs256_secret_is_rejected(auth_module):
    config = importlib.import_module("server.config")

    with pytest.raises(ValidationError, match="SECRET_KEY must be at least 32 bytes"):
        config.Settings(_env_file=None, **{**BASE_SETTINGS, "SECRET_KEY": "too-short"})
