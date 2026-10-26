from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.pagination import InvalidPageToken
from server.routes import triage as triage_routes
from server.services.setup_google import get_current_user_info
from server.services.triage_results import TriageResultNotFound


@pytest.fixture
def caller(monkeypatch):
    """Authenticate as a known principal and record what the services receive."""
    user_id = uuid4()
    calls: list[tuple] = []

    async def fake_list(owner_id, **kwargs):
        calls.append(("list", owner_id, kwargs))
        return {"results": [], "next_page_token": None}

    async def fake_get(result_id, owner_id):
        calls.append(("get", result_id, owner_id))
        raise TriageResultNotFound()

    monkeypatch.setattr(triage_routes, "list_triage_results", fake_list)
    monkeypatch.setattr(triage_routes, "get_triage_result", fake_get)
    app.dependency_overrides[get_current_user_info] = lambda: {"user_id": user_id, "email": "owner@example.test"}
    try:
        yield TestClient(app, raise_server_exceptions=False), user_id, calls
    finally:
        app.dependency_overrides.pop(get_current_user_info, None)


def test_listing_uses_the_authenticated_owner_and_ignores_user_or_mailbox_parameters(caller):
    client, user_id, calls = caller

    response = client.get(f"/mail/triage?state=succeeded&limit=5&user_id={uuid4()}&mailbox=victim@example.com")

    assert response.status_code == 200
    assert response.json() == {"results": [], "next_page_token": None}
    assert calls == [("list", user_id, {"state": "succeeded", "limit": 5, "page_token": None})]


def test_listing_defaults_to_twenty_per_page(caller):
    client, _, calls = caller

    assert client.get("/mail/triage?state=noop").status_code == 200
    assert calls[0][2]["limit"] == 20


@pytest.mark.parametrize(
    "query",
    [
        "",  # state is required
        "?state=banana",
        "?state=SUCCEEDED",
        "?state=succeeded&limit=0",
        "?state=succeeded&limit=101",
        "?state=succeeded&limit=abc",
        "?state=succeeded&page_token=",
    ],
)
def test_invalid_listing_requests_are_rejected_before_the_service_runs(caller, query):
    client, _, calls = caller

    response = client.get(f"/mail/triage{query}")

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert calls == []


def test_invalid_page_token_from_the_service_is_a_safe_422(caller, monkeypatch):
    client, _, _ = caller

    async def reject(*_args, **_kwargs):
        raise InvalidPageToken()

    monkeypatch.setattr(triage_routes, "list_triage_results", reject)

    response = client.get("/mail/triage?state=succeeded&page_token=garbage")

    assert response.status_code == 422
    assert response.json() == {"error": "validation_error", "detail": "Invalid page token."}


def test_detail_is_owner_scoped_and_an_unknown_or_foreign_result_is_a_plain_404(caller):
    client, user_id, calls = caller
    result_id = uuid4()

    response = client.get(f"/mail/triage/{result_id}")

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert calls == [("get", result_id, user_id)]


def test_detail_rejects_a_malformed_id_before_the_service_runs(caller):
    client, _, calls = caller

    assert client.get("/mail/triage/not-a-uuid").status_code == 422
    assert calls == []


def test_both_routes_require_authentication():
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/mail/triage?state=succeeded").status_code == 401
    assert client.get(f"/mail/triage/{uuid4()}").status_code == 401


def test_the_api_docs_warn_that_summaries_are_untrusted_and_retention_is_limited():
    schema = app.openapi()

    for path in ("/mail/triage", "/mail/triage/{triage_id}"):
        description = schema["paths"][path]["get"]["description"]
        assert "plain text" in description
    assert "retention" in schema["paths"]["/mail/triage"]["get"]["description"]
    components = schema["components"]["schemas"]
    assert "plain text" in components["TriageListItem"]["properties"]["summary_preview"]["description"]
    assert "plain text" in components["TriageDetail"]["properties"]["summary"]["description"]
    assert "summary" not in components["TriageListItem"]["properties"]  # the list never carries the full text
