import json
import logging
import os

import redis

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

logger = logging.getLogger(__name__)

redis_client = redis.StrictRedis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
)


def cache_set(key: str, value: dict, ttl: int = 300):
    redis_client.setex(key, ttl, json.dumps(value))


def cache_get(key: str):
    data = redis_client.get(key)
    return json.loads(data) if data else None


def cache_delete(key: str):
    redis_client.delete(key)


def invalidate_user_mail_cache(user_id: str, email_id: str) -> None:
    """Remove cached detail, list, and search data affected by a mail mutation."""
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
    except redis.RedisError:
        logger.warning(
            "Mail cache invalidation failed for user %s and message %s",
            user_id,
            email_id,
            exc_info=True,
        )