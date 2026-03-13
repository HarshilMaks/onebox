import redis
import json
import os

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

redis_client = redis.StrictRedis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True  # UTF-8 strings
)

def cache_set(key: str, value: dict, ttl: int = 300):
    redis_client.setex(key, ttl, json.dumps(value))

def cache_get(key: str):
    data = redis_client.get(key)
    return json.loads(data) if data else None

def cache_delete(key: str):
    redis_client.delete(key)