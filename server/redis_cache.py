import json
import logging

import redis

from server.config import settings


logger = logging.getLogger(__name__)

redis_client = redis.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
)


def cache_set(key: str, value: dict, ttl: int = 300) -> bool:
    """Best-effort cache write that never makes a mail request fail."""
    try:
        redis_client.setex(key, ttl, json.dumps(value))
        return True
    except (redis.RedisError, TypeError, ValueError):
        logger.warning("Mail cache write failed", exc_info=True)
        return False


def cache_get(key: str):
    """Return a cached mail object or treat unavailable/corrupt data as a miss."""
    try:
        data = redis_client.get(key)
        if not data:
            return None
        value = json.loads(data)
        if not isinstance(value, dict):
            logger.warning("Mail cache entry has an unexpected shape")
            return None
        return value
    except (redis.RedisError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Mail cache read failed", exc_info=True)
        return None


def cache_delete(key: str) -> bool:
    """Best-effort cache delete that never makes a mail mutation fail."""
    try:
        redis_client.delete(key)
        return True
    except redis.RedisError:
        logger.warning("Mail cache delete failed", exc_info=True)
        return False


def invalidate_user_mail_cache(user_id: str, email_id: str) -> bool:
    """Best-effort removal of cache entries affected by a mail mutation."""
    patterns = (
        f"user:{user_id}:email_v*:{email_id}",
        f"user:{user_id}:emails_v*",
        f"user:{user_id}:search*",
    )

    try:
        keys = [
            key
            for pattern in patterns
            for key in redis_client.scan_iter(match=pattern)
        ]
        if keys:
            redis_client.delete(*keys)
        return True
    except redis.RedisError:
        logger.warning(
            "Mail cache invalidation failed for user %s and message %s",
            user_id,
            email_id,
            exc_info=True,
        )
        return False