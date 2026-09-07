"""Server-side storage for one-time OAuth state tokens.

The OAuth `state` parameter must be an unguessable, single-use value that
is bound to the authenticated session which initiated the flow. Storing
it here (rather than deriving it from the user's UUID) prevents an
attacker from forging a `state` value to bind their own Google account to
someone else's application account.
"""
import logging
import secrets

from server.redis_cache import redis_client

logger = logging.getLogger(__name__)

_STATE_KEY_PREFIX = "oauth_state:"
_STATE_TTL_SECONDS = 600  # OAuth flows must complete within 10 minutes.


def create_oauth_state(user_id: str) -> str:
    """Generate and persist a one-time state token bound to `user_id`."""
    state = secrets.token_urlsafe(32)
    redis_client.setex(f"{_STATE_KEY_PREFIX}{state}", _STATE_TTL_SECONDS, user_id)
    return state


def consume_oauth_state(state: str) -> str | None:
    """Atomically retrieve and delete the user_id bound to `state`.

    Returns None if the state is missing, expired, or already used.
    Consuming (deleting) on read prevents replay of the same state value.
    """
    key = f"{_STATE_KEY_PREFIX}{state}"
    try:
        user_id = redis_client.getdel(key)
    except AttributeError:
        # Older redis-py without GETDEL support: fall back to GET + DELETE.
        user_id = redis_client.get(key)
        if user_id is not None:
            redis_client.delete(key)
    return user_id
