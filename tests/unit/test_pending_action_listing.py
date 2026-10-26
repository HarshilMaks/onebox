from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.pagination import InvalidPageToken
from server.routes import agent_router
from server.services.setup_google import get_current_user_info


@pytest.fixture
def caller(monkeypatch):
    """Authenticate as a known principal and record what the service receives."""
    user_id = uuid4()
    calls: list[dict] = []

    async def fake_list(owner_id, **kwargs):
        calls.append({"owner_id": owner_id, **kwargs})
        return {"actions": [], "next_page_token": None}

    monkeypatch.setattr(agent_router, "list_pending_actions", fake_list)
    app.dependency_overrides[get_current_user_info] = lambda: {"user_id": user_id, "email": "owner@example.test"}
    try:
        yield TestClient(app, raise_server_exceptions=False), user_id, calls
    finally:
        app.dependency_overrides.pop(get_current_user_info, None)


def test_listing_uses_the_authenticated_owner_and_ignores_a_user_id_parameter(caller):
    client, user_id, calls = caller

    response = client.get(f"/actions?status=pending&limit=5&user_id={uuid4()}")

    assert response.status_code == 200
    assert response.json() == {"actions": [], "next_page_token": None}
    assert calls == [{"owner_id": user_id, "status": "pending", "limit": 5, "page_token": None}]


def test_listing_defaults_to_twenty_per_page(caller):
    client, _, calls = caller

    assert client.get("/actions?status=succeeded").status_code == 200
    assert calls[0]["limit"] == 20


@pytest.mark.parametrize(
    "query",
    [
        "",  # status is required
        "?status=banana",
        "?status=PENDING",
        "?status=pending&limit=0",
        "?status=pending&limit=101",
        "?status=pending&limit=abc",
        "?status=pending&page_token=",
    ],
)
def test_invalid_listing_requests_are_rejected_before_the_service_runs(caller, query):
    client, _, calls = caller

    response = client.get(f"/actions{query}")

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert calls == []


def test_invalid_page_token_from_the_service_is_a_safe_422(caller, monkeypatch):
    client, _, _ = caller

    async def reject(*_args, **_kwargs):
        raise InvalidPageToken()

    monkeypatch.setattr(agent_router, "list_pending_actions", reject)

    response = client.get("/actions?status=pending&page_token=garbage")

    assert response.status_code == 422
    assert response.json() == {"error": "validation_error", "detail": "Invalid page token."}


def test_listing_requires_authentication():
    response = TestClient(app, raise_server_exceptions=False).get("/actions?status=pending")

    assert response.status_code == 401
