"""Bounded Google adapters with classified safe retry behavior.

Only complete reads and explicitly idempotent writes may retry.  Any ordinary
write is dispatched once: timeout or transport uncertainty must flow into the
pending-action reconciliation path rather than a blind retry.
"""

from __future__ import annotations

import asyncio
import logging
import random
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
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
    """Base class for stable Google provider failures."""


class GoogleOperationTimeout(GoogleProviderError):
    pass


class GoogleOperationUnavailable(GoogleProviderError):
    def __init__(self, status_code: int | None = None, retry_after_seconds: float | None = None) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Google is temporarily unavailable")


class GoogleOperationQuota(GoogleOperationUnavailable):
    pass


class GoogleOperationRejected(GoogleProviderError):
    def __init__(self, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__("Google rejected the request")


class GoogleOperationAuthentication(GoogleOperationRejected):
    pass


class GoogleOperationInternal(GoogleProviderError):
    pass


class GoogleOperationSafety(str, Enum):
    READ = "read"
    IDEMPOTENT_WRITE = "idempotent_write"
    AMBIGUOUS_WRITE = "ambiguous_write"


class GoogleErrorCategory(str, Enum):
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    AUTHENTICATION = "authentication"
    QUOTA = "quota"
    AMBIGUOUS_WRITE = "ambiguous_write"
    INTERNAL = "internal"


@dataclass(frozen=True)
class GoogleFailureClassification:
    category: GoogleErrorCategory
    retry_after_seconds: float | None = None

    @property
    def retryable(self) -> bool:
        return self.category in {GoogleErrorCategory.RETRYABLE, GoogleErrorCategory.QUOTA}


def classify_google_error(
    error: GoogleProviderError,
    *,
    safety: GoogleOperationSafety,
) -> GoogleFailureClassification:
    """Classify a provider error without ever authorizing ambiguous-write retry."""
    if safety is GoogleOperationSafety.AMBIGUOUS_WRITE and isinstance(
        error, (GoogleOperationTimeout, GoogleOperationUnavailable)
    ):
        return GoogleFailureClassification(GoogleErrorCategory.AMBIGUOUS_WRITE)
    if isinstance(error, GoogleOperationAuthentication):
        return GoogleFailureClassification(GoogleErrorCategory.AUTHENTICATION)
    if isinstance(error, GoogleOperationQuota):
        return GoogleFailureClassification(GoogleErrorCategory.QUOTA, error.retry_after_seconds)
    if isinstance(error, (GoogleOperationTimeout, GoogleOperationUnavailable)):
        return GoogleFailureClassification(
            GoogleErrorCategory.RETRYABLE,
            getattr(error, "retry_after_seconds", None),
        )
    if isinstance(error, GoogleOperationRejected):
        return GoogleFailureClassification(GoogleErrorCategory.PERMANENT)
    return GoogleFailureClassification(GoogleErrorCategory.INTERNAL)


def retry_delay_seconds(
    *,
    attempt: int,
    retry_after_seconds: float | None = None,
    random_value: float | None = None,
) -> float:
    """Return capped exponential backoff with full jitter and Retry-After floor."""
    exponential = min(
        settings.PROVIDER_RETRY_INITIAL_SECONDS * (2 ** max(0, attempt - 1)),
        settings.PROVIDER_RETRY_MAX_SECONDS,
    )
    jittered = exponential * (random.random() if random_value is None else random_value)
    return max(jittered, retry_after_seconds or 0.0)


def _retry_after_seconds(response: Any) -> float | None:
    headers = getattr(response, "headers", None)
    raw_value = headers.get("Retry-After") if headers is not None else None
    if not isinstance(raw_value, str):
        return None
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        try:
            value = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(0.0, (value - datetime.now(timezone.utc)).total_seconds())


class DeadlineGoogleAuthRequest(GoogleAuthRequest):
    """Clamp google-auth requests to the configured provider transport deadline."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        configured_timeout = settings.PROVIDER_TIMEOUT_SECONDS
        positional = list(args)
        if len(positional) >= 5:
            requested_timeout = positional[4]
            positional[4] = configured_timeout if requested_timeout is None else min(float(requested_timeout), configured_timeout)
        else:
            requested_timeout = kwargs.get("timeout")
            kwargs["timeout"] = configured_timeout if requested_timeout is None else min(float(requested_timeout), configured_timeout)
        return super().__call__(*positional, **kwargs)


def google_auth_request() -> GoogleAuthRequest:
    return DeadlineGoogleAuthRequest()


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
        retry_after = _retry_after_seconds(getattr(error, "resp", None))
        if status_code in {401, 403}:
            return GoogleOperationAuthentication(status_code)
        if status_code == 429:
            return GoogleOperationQuota(status_code, retry_after)
        if isinstance(status_code, int) and status_code >= 500:
            return GoogleOperationUnavailable(status_code, retry_after)
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
    """Run one complete Google/OAuth SDK operation with no implicit retry."""
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


async def run_google_retryable_operation(
    operation: Callable[..., T],
    *args: Any,
    resource: Resource | None = None,
    safety: GoogleOperationSafety,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    random_value: Callable[[], float] = random.random,
    **kwargs: Any,
) -> T:
    """Retry only classified read/idempotent provider operations within a deadline."""
    if safety is GoogleOperationSafety.AMBIGUOUS_WRITE:
        return await run_google_operation(operation, *args, resource=resource, **kwargs)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.PROVIDER_RETRY_DEADLINE_SECONDS
    for attempt in range(1, settings.PROVIDER_RETRY_MAX_ATTEMPTS + 1):
        remaining_duration = deadline - loop.time()
        if remaining_duration <= 0:
            raise GoogleOperationTimeout()
        attempt_timeout = min(settings.PROVIDER_TIMEOUT_SECONDS, remaining_duration)
        try:
            return await run_google_operation(
                operation,
                *args,
                resource=resource,
                timeout=attempt_timeout,
                **kwargs,
            )
        except asyncio.CancelledError:
            raise
        except GoogleProviderError as exc:
            failure = classify_google_error(exc, safety=safety)
            if not failure.retryable or attempt >= settings.PROVIDER_RETRY_MAX_ATTEMPTS:
                raise
            delay = retry_delay_seconds(
                attempt=attempt,
                retry_after_seconds=failure.retry_after_seconds,
                random_value=random_value(),
            )
            if loop.time() + delay >= deadline:
                raise
            logger.info(
                "Retrying safe Google operation category=%s attempt=%s delay_seconds=%.3f",
                failure.category.value,
                attempt + 1,
                delay,
            )
            await sleep(delay)
    raise GoogleOperationInternal()


async def execute_google_request(
    request: Any,
    *,
    resource: Resource | None = None,
    timeout: float | None = None,
) -> Any:
    """Dispatch a potentially ambiguous provider request exactly once."""
    return await run_google_operation(request.execute, resource=resource, timeout=timeout)


async def execute_google_read_request(request: Any, *, resource: Resource | None = None) -> Any:
    """Execute a safe provider read with bounded retry classification."""
    return await run_google_retryable_operation(
        request.execute,
        resource=resource,
        safety=GoogleOperationSafety.READ,
    )


async def execute_google_idempotent_request(request: Any, *, resource: Resource | None = None) -> Any:
    """Execute an explicitly idempotent provider mutation with bounded retries."""
    return await run_google_retryable_operation(
        request.execute,
        resource=resource,
        safety=GoogleOperationSafety.IDEMPOTENT_WRITE,
    )


def _build_google_service(service_name: str, version: str, credentials: Any) -> Resource:
    http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=settings.PROVIDER_TIMEOUT_SECONDS))
    return build(service_name, version, http=http, cache_discovery=False)


async def build_google_service(service_name: str, version: str, credentials: Any) -> Resource:
    return await run_google_operation(_build_google_service, service_name, version, credentials)


async def close_google_adapter() -> None:
    global _google_runner
    if _google_runner is not None:
        await _google_runner.aclose()
        _google_runner = None
    _resource_locks.clear()
