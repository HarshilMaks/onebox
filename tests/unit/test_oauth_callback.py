from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi.testclient import TestClient

from server.database import get_agent_db
from server.main import app
from server.oauth_state import OAuthStateBinding
from server.routes import agent_oauth


def test_oauth_provider_denial_redirects_to_frontend_after_consuming_state(monkeypatch):
    user_id = uuid4()
    consumed_states: list[str] = []

    async def consume_state(state: str):
        consumed_states.append(state)
        return OAuthStateBinding(user_id=str(user_id), expected_email="owner@example.test")

    async def database_override():
        yield None

    monkeypatch.setattr(agent_oauth, "consume_oauth_state", consume_state)
    app.dependency_overrides[get_agent_db] = database_override
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/agent/oauth/callback?error=access_denied&state=one-time-state",
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_agent_db, None)

    assert response.status_code == 302
    callback = urlparse(response.headers["location"])
    assert callback.path == "/mail/inbox"
    assert parse_qs(callback.query) == {
        "status": ["failure"],
        "error_message": ["OAuth authorization was not completed."],
    }
    assert consumed_states == ["one-time-state"]
