import asyncio
import threading
from types import SimpleNamespace

import pytest

from server import oauth_state, redis_cache
from server.integrations import google, llm
from server.integrations.provider import BoundedSyncRunner, ProviderOperationTimeout
from server.integrations.redis import RedisAdapter, RedisAdapterError


@pytest.mark.asyncio
async def test_bounded_sync_runner_times_out_without_blocking_the_event_loop():
    release = threading.Event()
    runner = BoundedSyncRunner(name="test-provider", max_workers=1, default_timeout=0.02)
    try:
        operation = asyncio.create_task(runner.run(release.wait))
        await asyncio.sleep(0)
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.01)
        with pytest.raises(ProviderOperationTimeout):
            await operation
    finally:
        release.set()
        await asyncio.sleep(0)
        await runner.aclose()


class _UnavailableCacheAdapter:
    async def get(self, _key):
        raise RedisAdapterError()

    async def setex(self, *_args):
        raise RedisAdapterError()

    async def delete(self, *_args):
        raise RedisAdapterError()

    async def scan_delete(self, *_args):
        raise RedisAdapterError()


@pytest.mark.asyncio
async def test_mail_cache_fails_open_when_redis_is_unavailable(monkeypatch):
    monkeypatch.setattr(redis_cache, "get_redis_adapter", lambda: _UnavailableCacheAdapter())

    assert await redis_cache.cache_get("mail:key") is None
    assert await redis_cache.cache_set("mail:key", {"id": "message"}) is False
    assert await redis_cache.cache_delete("mail:key") is False
    assert await redis_cache.invalidate_user_mail_cache("user", "message") is False


class _AtomicStateAdapter:
    def __init__(self):
        self.values = {}
        self.lock = asyncio.Lock()

    async def setex(self, key, _ttl, value):
        self.values[key] = value

    async def consume(self, _script, key):
        async with self.lock:
            return self.values.pop(key, None)


@pytest.mark.asyncio
async def test_oauth_state_consume_is_atomic_under_concurrency(monkeypatch):
    store = _AtomicStateAdapter()
    monkeypatch.setattr(oauth_state, "get_redis_adapter", lambda: store)
    state = await oauth_state.create_oauth_state("user-id", "owner@example.com")

    consumed = await asyncio.gather(*[oauth_state.consume_oauth_state(state) for _ in range(20)])

    bindings = [binding for binding in consumed if binding is not None]
    assert len(bindings) == 1
    assert bindings[0].user_id == "user-id"


class _BlockingModels:
    def __init__(self, release):
        self.release = release

    def generate_content(self, **_kwargs):
        self.release.wait()
        return {"ok": True}


class _BlockingClient:
    release = threading.Event()
    http_options = None

    def __init__(self, **kwargs):
        self.models = _BlockingModels(self.release)
        type(self).http_options = kwargs.get("http_options")

    def close(self):
        self.release.set()


@pytest.mark.asyncio
async def test_llm_generation_isolated_from_the_event_loop(monkeypatch):
    await llm.close_llm_adapter()
    _BlockingClient.release.clear()
    monkeypatch.setattr(llm, "Client", _BlockingClient)
    provider = llm.GeminiProvider()
    operation = asyncio.create_task(provider.generate(model="test", contents=[], config=None))
    try:
        await asyncio.sleep(0)
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.01)
    finally:
        _BlockingClient.release.set()
    assert await operation == {"ok": True}
    assert _BlockingClient.http_options.timeout > 0
    await llm.close_llm_adapter()


class _BlockingStreamIterator:
    def __init__(self):
        self.started = threading.Event()
        self.closed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        self.started.set()
        self.closed.wait()
        raise StopIteration

    def close(self):
        self.closed.set()


class _StreamModels:
    def __init__(self, iterator):
        self.iterator = iterator

    def generate_content_stream(self, **_kwargs):
        return self.iterator


class _StreamClient:
    iterator = _BlockingStreamIterator()

    def __init__(self, **_kwargs):
        self.models = _StreamModels(self.iterator)

    def close(self):
        self.iterator.close()


@pytest.mark.asyncio
async def test_llm_stream_close_stops_the_blocking_producer(monkeypatch):
    await llm.close_llm_adapter()
    _StreamClient.iterator = _BlockingStreamIterator()
    monkeypatch.setattr(llm, "Client", _StreamClient)
    provider = llm.GeminiProvider()

    async with provider.stream(model="test", contents=[], config=None) as stream:
        next_chunk = asyncio.create_task(anext(stream))
        for _ in range(20):
            if _StreamClient.iterator.started.is_set():
                break
            await asyncio.sleep(0.005)
        assert _StreamClient.iterator.started.is_set()
        await stream.aclose()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(next_chunk, timeout=0.1)

    assert _StreamClient.iterator.closed.is_set()
    await llm.close_llm_adapter()


class _MutableGoogleResource:
    pass


@pytest.mark.asyncio
async def test_google_adapter_serializes_one_mutable_resource():
    await google.close_google_adapter()
    resource = _MutableGoogleResource()
    release = threading.Event()
    first_started = threading.Event()
    second_started = threading.Event()

    def operation(started):
        started.set()
        release.wait()
        return "ok"

    first = asyncio.create_task(google.run_google_operation(operation, first_started, resource=resource))
    for _ in range(20):
        if first_started.is_set():
            break
        await asyncio.sleep(0.005)
    assert first_started.is_set()

    second = asyncio.create_task(google.run_google_operation(operation, second_started, resource=resource))
    await asyncio.sleep(0.02)
    assert not second_started.is_set()

    release.set()
    assert await first == "ok"
    assert await second == "ok"
    await google.close_google_adapter()


class _SlowAsyncRedis:
    def __init__(self):
        self.started = threading.Event()
        self.release = asyncio.Event()

    async def get(self, _key):
        self.started.set()
        await self.release.wait()
        return None


@pytest.mark.asyncio
async def test_redis_adapter_bounds_inflight_operations():
    client = _SlowAsyncRedis()
    adapter = RedisAdapter(client=client)
    adapter._slots = asyncio.BoundedSemaphore(1)

    first = asyncio.create_task(adapter.get("first"))
    for _ in range(20):
        if client.started.is_set():
            break
        await asyncio.sleep(0.005)
    assert client.started.is_set()

    second = asyncio.create_task(adapter.get("second"))
    await asyncio.sleep(0.02)
    assert not second.done()

    client.release.set()
    assert await first is None
    assert await second is None


@pytest.mark.asyncio
async def test_google_auth_request_clamps_sdk_transport_timeout(monkeypatch):
    observed = {}

    def fake_request_call(_self, *args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(google.GoogleAuthRequest, "__call__", fake_request_call)
    request = google.google_auth_request()
    request("https://example.invalid", "GET", None, None, 999)

    assert observed["args"][4] < 999


class _ImmediateDisconnectClient:
    instances = 0

    def __init__(self, **_kwargs):
        type(self).instances += 1
        self.models = _StreamModels(_BlockingStreamIterator())

    def close(self):
        pass


@pytest.mark.asyncio
async def test_llm_stream_immediate_close_does_not_start_provider(monkeypatch):
    await llm.close_llm_adapter()
    _ImmediateDisconnectClient.instances = 0
    monkeypatch.setattr(llm, "Client", _ImmediateDisconnectClient)
    provider = llm.GeminiProvider()

    async with provider.stream(model="test", contents=[], config=None) as stream:
        await stream.aclose()

    assert _ImmediateDisconnectClient.instances == 0
    await llm.close_llm_adapter()


@pytest.mark.asyncio
async def test_pubsub_provider_outage_is_reported_as_safe_503(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    from server.integrations.google import GoogleOperationUnavailable
    from server.routes import push_router

    async def unavailable(*_args, **_kwargs):
        raise GoogleOperationUnavailable()

    monkeypatch.setattr(push_router, "run_google_operation", unavailable)
    monkeypatch.setattr(
        push_router,
        "settings",
        SimpleNamespace(
            PUBSUB_PUSH_AUDIENCE="https://api.example.invalid/mail/notifications",
            PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL="push@example.invalid",
        ),
    )
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer signed-token")],
        }
    )

    with pytest.raises(HTTPException) as raised:
        await push_router.require_pubsub_push_auth(request)

    assert raised.value.status_code == 503
    assert raised.value.detail == "Pub/Sub push authentication is temporarily unavailable"


@pytest.mark.asyncio
async def test_pubsub_invalid_google_auth_token_remains_401(monkeypatch):
    from fastapi import HTTPException
    from google.auth import exceptions as google_auth_exceptions
    from starlette.requests import Request

    from server.routes import push_router

    def invalid_token(*_args, **_kwargs):
        raise google_auth_exceptions.InvalidValue("invalid issuer")

    await google.close_google_adapter()
    monkeypatch.setattr(push_router.id_token, "verify_oauth2_token", invalid_token)
    monkeypatch.setattr(
        push_router,
        "settings",
        SimpleNamespace(
            PUBSUB_PUSH_AUDIENCE="https://api.example.invalid/mail/notifications",
            PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL="push@example.invalid",
            PROVIDER_MAX_CONCURRENCY=1,
            PROVIDER_TIMEOUT_SECONDS=1.0,
        ),
    )
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer invalid-token")],
        }
    )

    with pytest.raises(HTTPException) as raised:
        await push_router.require_pubsub_push_auth(request)

    assert raised.value.status_code == 401
    assert raised.value.detail == "Invalid Pub/Sub push authentication"
    await google.close_google_adapter()


@pytest.mark.asyncio
async def test_google_resource_lock_survives_caller_timeout():
    await google.close_google_adapter()
    resource = _MutableGoogleResource()
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()

    def blocked_operation():
        first_started.set()
        release_first.wait()
        return "first"

    def second_operation():
        second_started.set()
        return "second"

    first = asyncio.create_task(
        google.run_google_operation(
            blocked_operation,
            resource=resource,
            timeout=0.01,
        )
    )
    for _ in range(20):
        if first_started.is_set():
            break
        await asyncio.sleep(0.005)
    assert first_started.is_set()
    with pytest.raises(google.GoogleOperationTimeout):
        await first

    with pytest.raises(google.GoogleOperationTimeout):
        await google.run_google_operation(second_operation, resource=resource, timeout=0.01)
    assert not second_started.is_set()

    release_first.set()
    assert await google.run_google_operation(second_operation, resource=resource) == "second"
    assert second_started.is_set()
    await google.close_google_adapter()


class _UncooperativeStreamIterator:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        self.started.set()
        self.release.wait()
        raise StopIteration

    def close(self):
        # Simulate an SDK close that cannot interrupt an in-flight blocking read.
        pass


class _UncooperativeStreamClient:
    iterator = _UncooperativeStreamIterator()

    def __init__(self, **_kwargs):
        self.models = _StreamModels(self.iterator)

    def close(self):
        self.iterator.close()


@pytest.mark.asyncio
async def test_llm_stream_close_returns_immediately_for_uncooperative_provider(monkeypatch):
    await llm.close_llm_adapter()
    _UncooperativeStreamClient.iterator = _UncooperativeStreamIterator()
    monkeypatch.setattr(llm, "Client", _UncooperativeStreamClient)
    provider = llm.GeminiProvider()

    async with provider.stream(model="test", contents=[], config=None) as stream:
        next_chunk = asyncio.create_task(anext(stream))
        for _ in range(20):
            if _UncooperativeStreamClient.iterator.started.is_set():
                break
            await asyncio.sleep(0.005)
        assert _UncooperativeStreamClient.iterator.started.is_set()
        await asyncio.wait_for(stream.aclose(), timeout=0.05)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(next_chunk, timeout=0.05)
        _UncooperativeStreamClient.iterator.release.set()

    await asyncio.sleep(0.02)
    await llm.close_llm_adapter()
