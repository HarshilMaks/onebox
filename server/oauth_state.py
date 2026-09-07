"""Server-side storage for one-time, owner-bound OAuth state tokens."""
import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import redis

from server.redis_cache import redis_client

_STATE_KEY_PREFIX = "oauth_state:"
_STATE_TTL_SECONDS = 600
_GETDEL_LUA = """
local value = redis.call('GET', KEYS[1])
if value then
    redis.call('DEL', KEYS[1])
end
return value
"""


class OAuthStateStoreUnavailable(RuntimeError):
    """The state store cannot safely create or consume OAuth bindings."""


@dataclass(frozen=True)
class OAuthStateBinding:
    user_id: str
    expected_email: str


def normalize_email(email: str) -> str:
    """Normalize an email address for case-insensitive account ownership checks."""
    if not isinstance(email, str):
        raise ValueError("Email address must be a string")
    normalized = email.strip().casefold()
    if not normalized:
        raise ValueError("Email address must not be blank")
    return normalized


def merge_oauth_token_payload(
    existing_token: Mapping[str, Any] | None,
    returned_token: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep an existing refresh token when a reauthorization omits one."""
    merged = dict(returned_token)
    if not merged.get("refresh_token") and existing_token and existing_token.get("refresh_token"):
        merged["refresh_token"] = existing_token["refresh_token"]
    return merged


def create_oauth_state(user_id: str, expected_email: str) -> str:
    """Persist an opaque, one-time state bound to the initiating user and email."""
    state = secrets.token_urlsafe(32)
    binding = {
        "user_id": str(user_id),
        "expected_email": normalize_email(expected_email),
    }
    try:
        redis_client.setex(
            f"{_STATE_KEY_PREFIX}{state}",
            _STATE_TTL_SECONDS,
            json.dumps(binding, separators=(",", ":")),
        )
    except redis.RedisError as exc:
        raise OAuthStateStoreUnavailable() from exc
    return state


def consume_oauth_state(state: str) -> Optional[OAuthStateBinding]:
    """Atomically retrieve and delete a one-time OAuth state binding.

    Legacy state values that stored only a user ID are rejected because they
    cannot prove the Google account selected in this flow belongs to the
    initiating application identity.
    """
    if not state:
        return None

    try:
        raw_binding = redis_client.eval(
            _GETDEL_LUA,
            1,
            f"{_STATE_KEY_PREFIX}{state}",
        )
    except redis.RedisError as exc:
        raise OAuthStateStoreUnavailable() from exc

    if not raw_binding:
        return None

    try:
        binding = json.loads(raw_binding)
        user_id = binding["user_id"]
        expected_email = normalize_email(binding["expected_email"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(user_id, str) or not user_id:
        return None
    return OAuthStateBinding(user_id=user_id, expected_email=expected_email)
