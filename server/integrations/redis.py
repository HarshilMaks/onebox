"""Async, bounded Redis transport shared by cache and OAuth-state policy modules."""

from __future__ import annotations

import asyncio

import redis.asyncio as redis

from server.config import settings


class RedisAdapterError(RuntimeError):
    """Redis is unavailable, timed out, or returned an unusable response."""


class RedisAdapter:
    """Own one lazy async Redis pool and bound every complete operation."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._client = client
        self._slots = asyncio.BoundedSemaphore(settings.REDIS_MAX_CONCURRENCY)

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            pool = redis.BlockingConnectionPool.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=settings.PROVIDER_TIMEOUT_SECONDS,
                socket_timeout=settings.PROVIDER_TIMEOUT_SECONDS,
                health_check_interval=30,
                max_connections=settings.REDIS_MAX_CONCURRENCY,
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
            )
            self._client = redis.Redis(connection_pool=pool)
        return self._client

    async def _acquire_slot(self) -> None:
        try:
            await asyncio.wait_for(
                self._slots.acquire(),
                timeout=settings.PROVIDER_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise RedisAdapterError("Redis operation capacity is exhausted") from exc

    async def _run(self, awaitable):
        await self._acquire_slot()
        try:
            async with asyncio.timeout(settings.PROVIDER_TIMEOUT_SECONDS):
                return await awaitable
        except asyncio.CancelledError:
            raise
        except (TimeoutError, redis.RedisError) as exc:
            raise RedisAdapterError() from exc
        finally:
            self._slots.release()

    async def ping(self) -> bool:
        return bool(await self._run(self.client.ping()))

    async def get(self, key: str) -> str | None:
        return await self._run(self.client.get(key))

    async def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        await self._run(self.client.setex(key, ttl_seconds, value))

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return await self._run(self.client.delete(*keys))

    async def consume(self, script: str, key: str) -> str | None:
        return await self._run(self.client.eval(script, 1, key))

    async def scan_delete(self, patterns: tuple[str, ...], *, max_keys: int = 1_000) -> int:
        """Delete matching keys with a complete-operation deadline and hard cap."""
        keys: list[str] = []
        await self._acquire_slot()
        try:
            async with asyncio.timeout(settings.PROVIDER_TIMEOUT_SECONDS):
                for pattern in patterns:
                    async for key in self.client.scan_iter(match=pattern):
                        keys.append(key)
                        if len(keys) > max_keys:
                            raise RedisAdapterError("cache invalidation key cap exceeded")
                if keys:
                    await self.client.delete(*keys)
        except asyncio.CancelledError:
            raise
        except RedisAdapterError:
            raise
        except (TimeoutError, redis.RedisError) as exc:
            raise RedisAdapterError() from exc
        finally:
            self._slots.release()
        return len(keys)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_redis_adapter = RedisAdapter()


def get_redis_adapter() -> RedisAdapter:
    return _redis_adapter


async def close_redis_adapter() -> None:
    await _redis_adapter.aclose()
