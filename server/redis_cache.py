"""Fail-open, JSON-typed Redis cache policy for mail responses.

Every user's mail cache is namespaced by a monotonically increasing generation.
Mutations advance that generation instead of scanning/deleting keys, so a stale
in-flight response can only write to an unreachable older generation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from server.integrations.redis import RedisAdapterError, get_redis_adapter


logger = logging.getLogger(__name__)

MAIL_PAGE_CACHE_TTL_SECONDS = 60
MAIL_DETAIL_CACHE_TTL_SECONDS = 300


def _generation_key(user_id: str) -> str:
    return f"user:{user_id}:mail:cache-generation"


async def cache_set(key: str, value: Any, ttl: int = MAIL_DETAIL_CACHE_TTL_SECONDS) -> bool:
    """Best-effort JSON cache write for dictionary, list, or scalar payloads."""
    try:
        serialized = json.dumps(value, separators=(",", ":"))
        await get_redis_adapter().setex(key, ttl, serialized)
        return True
    except (RedisAdapterError, TypeError, ValueError):
        logger.warning("Mail cache write failed", exc_info=True)
        return False


async def cache_get(key: str) -> Any | None:
    """Return decoded cache content or treat unavailable/corrupt data as a miss."""
    try:
        data = await get_redis_adapter().get(key)
        if not data:
            return None
        return json.loads(data)
    except (RedisAdapterError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Mail cache read failed", exc_info=True)
        return None


async def cache_delete(key: str) -> bool:
    """Best-effort cache delete that never makes a mail request fail."""
    try:
        await get_redis_adapter().delete(key)
        return True
    except RedisAdapterError:
        logger.warning("Mail cache delete failed", exc_info=True)
        return False


async def get_user_mail_cache_generation(user_id: str) -> int:
    """Return the active cache generation, treating Redis failure as generation zero.

    A Redis outage consequently becomes a cache miss.  It never prevents a mail
    request from reaching Gmail.
    """
    try:
        value = await get_redis_adapter().get(_generation_key(user_id))
        if value is None:
            return 0
        generation = int(value)
        return generation if generation >= 0 else 0
    except (RedisAdapterError, TypeError, ValueError):
        logger.warning("Mail cache generation read failed", exc_info=True)
        return 0


async def user_mail_cache_key(user_id: str, namespace: str) -> str:
    """Build an opaque per-user, generation-qualified cache key."""
    generation = await get_user_mail_cache_generation(user_id)
    return f"user:{user_id}:mail:g{generation}:{namespace}"


async def invalidate_user_mail_cache(user_id: str, email_id: str | None = None) -> bool:
    """Advance one user's cache generation without wildcard Redis scans.

    The optional message id remains for compatibility with existing mutation
    callers; all mail views for the user must be invalidated together.
    """
    try:
        await get_redis_adapter().incr(_generation_key(user_id))
        return True
    except (AttributeError, RedisAdapterError, TypeError, ValueError):
        logger.warning(
            "Mail cache generation invalidation failed for user %s",
            user_id,
            exc_info=True,
        )
        return False
