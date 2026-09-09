"""Nonblocking Gemini adapter with bounded synchronous execution and streaming."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from google.genai import Client
from google.genai.types import HttpOptions

from server.config import settings
from server.integrations.provider import (
    BoundedSyncRunner,
    ProviderCapacityExceeded,
    ProviderOperationTimeout,
)


logger = logging.getLogger(__name__)


class LlmProviderError(RuntimeError):
    """Base class for safe, stable LLM provider errors."""


class LlmOperationTimeout(LlmProviderError):
    pass


class LlmOperationUnavailable(LlmProviderError):
    pass


class LlmOperationInternal(LlmProviderError):
    pass


_llm_runner: BoundedSyncRunner | None = None


def _runner() -> BoundedSyncRunner:
    global _llm_runner
    if _llm_runner is None:
        _llm_runner = BoundedSyncRunner(
            name="onebox-llm",
            max_workers=settings.PROVIDER_MAX_CONCURRENCY,
            default_timeout=settings.PROVIDER_TIMEOUT_SECONDS,
        )
    return _llm_runner


def _close_sync(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.debug("Provider resource close failed", exc_info=True)


def _new_client() -> Client:
    settings.configure_google_application_credentials()
    return Client(
        vertexai=True,
        project=settings.GOOGLE_PROJECT_ID,
        location=settings.GOOGLE_LOCATION,
        http_options=HttpOptions(timeout=int(settings.PROVIDER_TIMEOUT_SECONDS * 1000)),
    )


def _translate_error(error: BaseException) -> LlmProviderError:
    if isinstance(error, ProviderOperationTimeout):
        return LlmOperationTimeout()
    if isinstance(error, ProviderCapacityExceeded):
        return LlmOperationUnavailable()
    if isinstance(error, (OSError, TimeoutError)):
        return LlmOperationUnavailable()
    return LlmOperationInternal()


def _observe_background_producer(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        logger.debug("Detached LLM stream producer exited with an error", exc_info=True)


class _GeminiStreamBridge(AsyncIterator[Any]):
    """Bridge one synchronous Gemini iterator to an async, bounded consumer."""

    def __init__(self, *, model: str, contents: Any, config: Any) -> None:
        self._model = model
        self._contents = contents
        self._config = config
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(
            maxsize=settings.LLM_STREAM_QUEUE_SIZE
        )
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._producer_task: asyncio.Task[None] | None = None
        self._iterator: Any = None
        self._client: Any = None
        self._deadline: float | None = None
        self._closed = False

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._deadline = self._loop.time() + settings.LLM_STREAM_TIMEOUT_SECONDS
        self._producer_task = asyncio.create_task(self._run_producer())

    def _enqueue_from_worker(self, kind: str, value: Any = None) -> None:
        if self._stop.is_set() or self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._queue.put((kind, value)), self._loop)
        try:
            future.result(timeout=settings.PROVIDER_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            future.cancel()
            raise ProviderCapacityExceeded("LLM stream consumer is not draining") from exc

    def _produce(self) -> None:
        """Run entirely on one bounded worker thread, including iterator iteration."""
        client: Any = None
        iterator: Any = None
        try:
            if self._stop.is_set():
                return
            client = _new_client()
            if self._stop.is_set():
                return
            self._client = client
            if self._stop.is_set():
                return
            iterator = client.models.generate_content_stream(
                model=self._model,
                contents=self._contents,
                config=self._config,
            )
            if self._stop.is_set():
                return
            self._iterator = iterator
            for chunk in iterator:
                if self._stop.is_set():
                    return
                self._enqueue_from_worker("chunk", chunk)
            self._enqueue_from_worker("end")
        except BaseException as exc:
            if not self._stop.is_set():
                self._enqueue_from_worker("error", _translate_error(exc))
        finally:
            _close_sync(iterator)
            _close_sync(client)
            self._iterator = None
            self._client = None

    async def _run_producer(self) -> None:
        try:
            await _runner().run(
                self._produce,
                timeout=settings.LLM_STREAM_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except LlmProviderError as exc:
            if not self._stop.is_set():
                await self._queue.put(("error", exc))
        except BaseException as exc:
            if not self._stop.is_set():
                await self._queue.put(("error", _translate_error(exc)))

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        assert self._loop is not None and self._deadline is not None
        remaining = self._deadline - self._loop.time()
        if remaining <= 0:
            await self.aclose()
            raise LlmOperationTimeout()
        try:
            kind, value = await asyncio.wait_for(
                self._queue.get(),
                timeout=min(remaining, settings.LLM_STREAM_IDLE_TIMEOUT_SECONDS),
            )
        except TimeoutError as exc:
            await self.aclose()
            raise LlmOperationTimeout() from exc
        if kind == "chunk":
            return value
        if kind == "end":
            await self.aclose()
            raise StopAsyncIteration
        await self.aclose()
        raise value

    async def aclose(self) -> None:
        """Stop production after a disconnect, deadline, or completed stream."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            self._queue.put_nowait(("end", None))
        except asyncio.QueueFull:
            pass
        # SDK iterator/client close methods are expected to unblock a cooperative
        # transport; the worker slot remains accounted for until its thread exits.
        _close_sync(self._iterator)
        _close_sync(self._client)
        if self._producer_task is not None and not self._producer_task.done():
            # Do not make client disconnect wait for an uncooperative SDK read.
            # The bounded runner keeps its worker slot reserved until that read
            # exits; configured SDK transport deadlines and the close request
            # above provide eventual cleanup.
            self._producer_task.add_done_callback(_observe_background_producer)


class GeminiProvider:
    """Async facade over the synchronous google-genai client."""

    async def generate(self, *, model: str, contents: Any, config: Any) -> Any:
        def generate_sync() -> Any:
            client = _new_client()
            try:
                return client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            finally:
                _close_sync(client)

        try:
            return await _runner().run(generate_sync)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            raise _translate_error(exc) from None

    @asynccontextmanager
    async def stream(self, *, model: str, contents: Any, config: Any):
        bridge = _GeminiStreamBridge(model=model, contents=contents, config=config)
        await bridge.start()
        try:
            yield bridge
        finally:
            await bridge.aclose()

    async def iter_stream(self, *, model: str, contents: Any, config: Any) -> AsyncIterator[Any]:
        """Yield synchronous provider chunks without blocking the event loop."""
        async with self.stream(model=model, contents=contents, config=config) as bridge:
            async for chunk in bridge:
                yield chunk


async def close_llm_adapter() -> None:
    global _llm_runner
    if _llm_runner is not None:
        await _llm_runner.aclose()
        _llm_runner = None
