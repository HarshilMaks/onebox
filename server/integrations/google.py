"""Bounded adapters for synchronous Google client libraries.

All Google API I/O crosses this module.  Services are built with a finite HTTP
socket timeout and complete synchronous request units run in an isolated,
bounded executor rather than FastAPI's event loop or default executor.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import Callable
from typing import Any, TypeVar

import httplib2
from google.auth.transport.requests import Request as GoogleAuthRequest
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from server.config import settings
from server.integrations.provider import (
    BoundedSyncRunner,
    OperationLifetime,
    ProviderCapacityExceeded,
    ProviderOperationTimeout,
)


logger = logging.getLogger(__name__)
T = TypeVar("T")


class GoogleProviderError(RuntimeError):
    """Base class for safe Google provider failures."""


class GoogleOperationTimeout(GoogleProviderError):
    pass


class GoogleOperationUnavailable(GoogleProviderError):
    pass


class GoogleOperationRejected(GoogleProviderError):
    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__("Google rejected the request")


class DeadlineGoogleAuthRequest(GoogleAuthRequest):
    """Clamp google-auth requests to the configured provider transport deadline."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        configured_timeout = settings.PROVIDER_TIMEOUT_SECONDS
        positional = list(args)
        if len(positional) >= 5:
            requested_timeout = positional[4]
            positional[4] = (
                configured_timeout
                if requested_timeout is None
                else min(float(requested_timeout), configured_timeout)
            )
        else:
            requested_timeout = kwargs.get("timeout")
            kwargs["timeout"] = (
                configured_timeout
                if requested_timeout is None
                else min(float(requested_timeout), configured_timeout)
            )
        return super().__call__(*positional, **kwargs)


def google_auth_request() -> GoogleAuthRequest:
    """Return a google-auth transport with the shared finite deadline."""
    return DeadlineGoogleAuthRequest()


class GoogleOperationInternal(GoogleProviderError):
    pass


_google_runner: BoundedSyncRunner | None = None
_resource_locks: weakref.WeakKeyDictionary[Resource, asyncio.Lock] = weakref.WeakKeyDictionary()


def _runner() -> BoundedSyncRunner:
    global _google_runner
    if _google_runner is None:
        _google_runner = BoundedSyncRunner(
            name="onebox-google",
            max_workers=settings.PROVIDER_MAX_CONCURRENCY,
            default_timeout=settings.PROVIDER_TIMEOUT_SECONDS,
        )
    return _google_runner


def _resource_lock(resource: Resource | None) -> asyncio.Lock | None:
    if resource is None:
        return None
    lock = _resource_locks.get(resource)
    if lock is None:
        lock = asyncio.Lock()
        _resource_locks[resource] = lock
    return lock


def _translate_google_error(error: BaseException) -> GoogleProviderError:
    if isinstance(error, ProviderOperationTimeout):
        return GoogleOperationTimeout()
    if isinstance(error, ProviderCapacityExceeded):
        return GoogleOperationUnavailable()
    if isinstance(error, HttpError):
        status_code = getattr(getattr(error, "resp", None), "status", None)
        if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
            return GoogleOperationUnavailable()
        return GoogleOperationRejected(status_code)
    if isinstance(error, (OSError, TimeoutError, httplib2.HttpLib2Error)):
        return GoogleOperationUnavailable()
    return GoogleOperationInternal()


async def run_google_operation(
    operation: Callable[..., T],
    *args: Any,
    resource: Resource | None = None,
    timeout: float | None = None,
    passthrough: tuple[type[BaseException], ...] = (),
    **kwargs: Any,
) -> T:
    """Run one complete Google/OAuth SDK operation with stable safe errors."""
    lock = _resource_lock(resource)
    operation_timeout = settings.PROVIDER_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        if lock is None:
            return await _runner().run(operation, *args, timeout=operation_timeout, **kwargs)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + operation_timeout
        try:
            await asyncio.wait_for(lock.acquire(), timeout=operation_timeout)
        except TimeoutError as exc:
            raise GoogleOperationTimeout() from exc

        lifetime = OperationLifetime()
        try:
            remaining_timeout = deadline - loop.time()
            if remaining_timeout <= 0:
                raise GoogleOperationTimeout()
            return await _runner().run(
                operation,
                *args,
                timeout=remaining_timeout,
                deadline=deadline,
                lifetime=lifetime,
                on_detached_completion=lock.release,
                **kwargs,
            )
        finally:
            if not lifetime.detached:
                lock.release()
    except asyncio.CancelledError:
        raise
    except passthrough:
        raise
    except GoogleProviderError:
        raise
    except BaseException as exc:
        raise _translate_google_error(exc) from None


async def execute_google_request(
    request: Any,
    *,
    resource: Resource | None = None,
    timeout: float | None = None,
) -> Any:
    """Execute an already-built google-api-python-client request safely."""
    return await run_google_operation(
        request.execute,
        resource=resource,
        timeout=timeout,
    )


def _build_google_service(
    service_name: str,
    version: str,
    credentials: Any,
) -> Resource:
    http = AuthorizedHttp(
        credentials,
        http=httplib2.Http(timeout=settings.PROVIDER_TIMEOUT_SECONDS),
    )
    return build(
        service_name,
        version,
        http=http,
        cache_discovery=False,
    )


async def build_google_service(
    service_name: str,
    version: str,
    credentials: Any,
) -> Resource:
    """Build a request-local Google service with a deadline-aware transport."""
    return await run_google_operation(
        _build_google_service,
        service_name,
        version,
        credentials,
    )


async def close_google_adapter() -> None:
    """Close bounded worker resources during FastAPI lifespan shutdown."""
    global _google_runner
    if _google_runner is not None:
        await _google_runner.aclose()
        _google_runner = None
    _resource_locks.clear()
