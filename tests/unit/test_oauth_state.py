import json

import pytest

from server import oauth_state
from server.integrations.redis import RedisAdapterError


class FakeRedisAdapter:
    def __init__(self):
        self.values = {}
        self.consume_calls = 0

    async def setex(self, key, _ttl, value):
        self.values[key] = value

    async def consume(self, _script, key):
        self.consume_calls += 1
        return self.values.pop(key, None)


class UnavailableRedisAdapter:
    async def setex(self, *_args):
        raise RedisAdapterError()

    async def consume(self, *_args):
        raise RedisAdapterError()


@pytest.mark.asyncio
async def test_oauth_state_is_email_bound_and_consumed_once(monkeypatch):
    store = FakeRedisAdapter()
    monkeypatch.setattr(oauth_state, "get_redis_adapter", lambda: store)

    state = await oauth_state.create_oauth_state("user-id", "Owner@Example.com")
    first = await oauth_state.consume_oauth_state(state)

    assert first is not None
    assert first.user_id == "user-id"
    assert first.expected_email == "owner@example.com"
    assert await oauth_state.consume_oauth_state(state) is None
    assert store.consume_calls == 2


@pytest.mark.asyncio
async def test_expired_or_malformed_oauth_state_is_rejected(monkeypatch):
    store = FakeRedisAdapter()
    monkeypatch.setattr(oauth_state, "get_redis_adapter", lambda: store)

    assert await oauth_state.consume_oauth_state("missing") is None
    store.values["oauth_state:malformed"] = json.dumps({"user_id": "user-id"})
    assert await oauth_state.consume_oauth_state("malformed") is None


@pytest.mark.asyncio
async def test_oauth_state_store_outage_fails_closed(monkeypatch):
    monkeypatch.setattr(oauth_state, "get_redis_adapter", lambda: UnavailableRedisAdapter())

    with pytest.raises(oauth_state.OAuthStateStoreUnavailable):
        await oauth_state.create_oauth_state("user-id", "owner@example.com")
    with pytest.raises(oauth_state.OAuthStateStoreUnavailable):
        await oauth_state.consume_oauth_state("state")
