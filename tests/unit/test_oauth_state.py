import json

import pytest
import redis

from server import oauth_state


class FakeRedis:
    def __init__(self):
        self.values = {}

    def setex(self, key, _ttl, value):
        self.values[key] = value

    def eval(self, _script, _keys, key):
        return self.values.pop(key, None)


class UnavailableRedis:
    def setex(self, *_args):
        raise redis.RedisError("unavailable")

    def eval(self, *_args):
        raise redis.RedisError("unavailable")


def test_oauth_state_is_email_bound_and_consumed_once(monkeypatch):
    store = FakeRedis()
    monkeypatch.setattr(oauth_state, "redis_client", store)

    state = oauth_state.create_oauth_state("user-id", "Owner@Example.com")
    first = oauth_state.consume_oauth_state(state)

    assert first is not None
    assert first.user_id == "user-id"
    assert first.expected_email == "owner@example.com"
    assert oauth_state.consume_oauth_state(state) is None


def test_expired_or_malformed_oauth_state_is_rejected(monkeypatch):
    store = FakeRedis()
    monkeypatch.setattr(oauth_state, "redis_client", store)

    assert oauth_state.consume_oauth_state("missing") is None
    store.values["oauth_state:malformed"] = json.dumps({"user_id": "user-id"})
    assert oauth_state.consume_oauth_state("malformed") is None


def test_oauth_state_store_outage_fails_closed(monkeypatch):
    monkeypatch.setattr(oauth_state, "redis_client", UnavailableRedis())

    with pytest.raises(oauth_state.OAuthStateStoreUnavailable):
        oauth_state.create_oauth_state("user-id", "owner@example.com")
    with pytest.raises(oauth_state.OAuthStateStoreUnavailable):
        oauth_state.consume_oauth_state("state")
