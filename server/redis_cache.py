"""Fail-open asynchronous Redis cache policy for mail responses."""

from __future__ import annotations

import json
import logging
from typing import Any

from server.integrations.redis import RedisAdapterError, get_redis_adapter


logger = logging.getLogger(__name__)


async def cache_set(key: str, value: Any, ttl: int = 300) -> bool:
    """Best-effort cache write that never makes a mail request fail."""
    try:
        serialized = json.dumps(value)
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
    """Best-effort cache delete that never makes a mail mutation fail."""
    try:
        await get_redis_adapter().delete(key)
        return True
    except RedisAdapterError:
        logger.warning("Mail cache delete failed", exc_info=True)
        return False


async def invalidate_user_mail_cache(user_id: str, email_id: str) -> bool:
    """Best-effort removal of entries affected by a mail mutation."""
    patterns = (
        f"user:{user_id}:email_v*:{email_id}",
        f"user:{user_id}:emails_v*",
        f"user:{user_id}:search*",
    )
    try:
        await get_redis_adapter().scan_delete(patterns)
        return True
    except RedisAdapterError:
        logger.warning(
            "Mail cache invalidation failed for user %s and message %s",
            user_id,
            email_id,
            exc_info=True,
        )
        return False
