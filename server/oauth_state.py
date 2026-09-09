"""Server-side storage for one-time, owner-bound OAuth state tokens."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from server.integrations.redis import RedisAdapterError, get_redis_adapter


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


async def create_oauth_state(user_id: str, expected_email: str) -> str:
    """Persist an opaque, one-time state bound to the initiating user and email."""
    state = secrets.token_urlsafe(32)
    binding = {
        "user_id": str(user_id),
        "expected_email": normalize_email(expected_email),
    }
    try:
        await get_redis_adapter().setex(
            f"{_STATE_KEY_PREFIX}{state}",
            _STATE_TTL_SECONDS,
            json.dumps(binding, separators=(",", ":")),
        )
    except RedisAdapterError as exc:
        raise OAuthStateStoreUnavailable() from exc
    return state


async def consume_oauth_state(state: str) -> OAuthStateBinding | None:
    """Atomically retrieve and delete a one-time OAuth state binding.

    A timeout or Redis failure is fail-closed: callers must not retry an
    ambiguous consume operation or proceed to token exchange.
    """
    if not state:
        return None

    try:
        raw_binding = await get_redis_adapter().consume(
            _GETDEL_LUA,
            f"{_STATE_KEY_PREFIX}{state}",
        )
    except RedisAdapterError as exc:
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
