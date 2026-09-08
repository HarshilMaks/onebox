import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from server.auth import AuthenticatedPrincipal
from server.services import credentials
from server.services import setup_google


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _token_payload(**overrides) -> dict:
    payload = {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": list(credentials.GOOGLE_OAUTH_SCOPES),
    }
    payload.update(overrides)
    return payload


def test_refresh_token_merge_preserves_only_omitted_token_and_accepts_replacement():
    existing = _token_payload(refresh_token="existing-refresh")

    assert credentials.merge_oauth_token_payload(existing, _token_payload(refresh_token=""))["refresh_token"] == "existing-refresh"
    assert credentials.merge_oauth_token_payload(
        existing, _token_payload(refresh_token="replacement-refresh")
    )["refresh_token"] == "replacement-refresh"


@pytest.mark.parametrize(
    "payload",
    [
        _token_payload(refresh_token=""),
        _token_payload(scopes=[]),
        _token_payload(scopes=["openid"]),
        {"refresh_token": "refresh-token"},
    ],
)
def test_malformed_or_incomplete_persisted_credentials_require_reconnection(payload):
    with pytest.raises(credentials.GoogleReconnectRequired):
        credentials._validate_token_payload(payload)


def test_full_mail_scope_satisfies_the_gmail_grant_requirement():
    payload = _token_payload(
        scopes=[
            "https://mail.google.com/",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ]
    )

    assert credentials._validate_token_payload(payload)["refresh_token"] == "refresh-token"


def test_reconnect_dependency_maps_to_a_stable_non_500_response(monkeypatch):
    principal = AuthenticatedPrincipal(user_id=uuid4(), email="user@example.com", claims={})

    async def reconnect(*_args, **_kwargs):
        raise credentials.GoogleReconnectRequired()

    monkeypatch.setattr(setup_google, "load_connected_google_connection", reconnect)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(setup_google.get_connected_google_connection(principal, object()))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Google account reconnection is required"


def test_routes_and_provider_dependencies_do_not_parse_raw_token_json_or_use_jwt_sender_email():
    oauth_route = (REPOSITORY_ROOT / "server/routes/agent_oauth.py").read_text(encoding="utf-8")
    provider_dependencies = (REPOSITORY_ROOT / "server/services/setup_google.py").read_text(encoding="utf-8")
    worker = (REPOSITORY_ROOT / "server/services/mail.py").read_text(encoding="utf-8")
    agent_route = (REPOSITORY_ROOT / "server/routes/agent_router.py").read_text(encoding="utf-8")

    assert "token_json" not in oauth_route
    assert "token_json" not in provider_dependencies
    assert "token_json" not in worker
    assert "current_user_email=str(user_info[\"email\"])" not in agent_route
    assert agent_route.count("google_connection.google_email") == 2
