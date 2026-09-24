from __future__ import annotations

import json
from types import SimpleNamespace

import httplib2
import pytest
from googleapiclient.errors import HttpError

from server.integrations import google
from server.routes import google_mail
from server.schemas import StarStateUpdate


def _retry_settings(*, attempts: int = 3, deadline: float = 30.0, provider_timeout: float = 20.0):
    return SimpleNamespace(
        PROVIDER_TIMEOUT_SECONDS=provider_timeout,
        PROVIDER_RETRY_MAX_ATTEMPTS=attempts,
        PROVIDER_RETRY_INITIAL_SECONDS=1.0,
        PROVIDER_RETRY_MAX_SECONDS=4.0,
        PROVIDER_RETRY_DEADLINE_SECONDS=deadline,
    )


def _google_http_error(*, status_code: int, reason: str) -> HttpError:
    response = httplib2.Response({"status": str(status_code)})
    content = json.dumps({"error": {"code": status_code, "errors": [{"reason": reason}]}}).encode()
    return HttpError(response, content)


@pytest.mark.parametrize("reason", ["quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded"])
def test_structured_403_quota_and_rate_limit_errors_are_retryable(reason):
    translated = google._translate_google_error(_google_http_error(status_code=403, reason=reason))

    assert isinstance(translated, google.GoogleOperationQuota)
    classification = google.classify_google_error(translated, safety=google.GoogleOperationSafety.READ)
    assert classification.category is google.GoogleErrorCategory.QUOTA
    assert classification.retryable is True


def test_structured_403_permission_error_remains_terminal():
    translated = google._translate_google_error(
        _google_http_error(status_code=403, reason="insufficientPermissions")
    )

    assert isinstance(translated, google.GoogleOperationAuthentication)
    classification = google.classify_google_error(translated, safety=google.GoogleOperationSafety.READ)
    assert classification.category is google.GoogleErrorCategory.AUTHENTICATION
    assert classification.retryable is False


@pytest.mark.asyncio
async def test_quota_retry_honors_retry_after_then_succeeds(monkeypatch):
    monkeypatch.setattr(google, "settings", _retry_settings())
    outcomes = [google.GoogleOperationQuota(429, retry_after_seconds=3.0), "success"]
    delays = []

    async def fake_run(operation, *_args, **_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(google, "run_google_operation", fake_run)
    result = await google.run_google_retryable_operation(
        lambda: None,
        safety=google.GoogleOperationSafety.READ,
        sleep=sleep,
        random_value=lambda: 0.5,
    )

    assert result == "success"
    assert delays == [3.0]


@pytest.mark.asyncio
async def test_retryable_503_read_then_success(monkeypatch):
    monkeypatch.setattr(google, "settings", _retry_settings())
    outcomes = [google.GoogleOperationUnavailable(503), "success"]
    delays = []

    async def fake_run(operation, *_args, **_kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(google, "run_google_operation", fake_run)
    result = await google.run_google_retryable_operation(
        lambda: None,
        safety=google.GoogleOperationSafety.READ,
        sleep=sleep,
        random_value=lambda: 0.5,
    )

    assert result == "success"
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_retryable_503_is_bounded_and_permanent_error_is_not_retried(monkeypatch):
    monkeypatch.setattr(google, "settings", _retry_settings(attempts=3))
    attempts = 0

    async def always_unavailable(operation, *_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise google.GoogleOperationUnavailable(503)

    monkeypatch.setattr(google, "run_google_operation", always_unavailable)
    with pytest.raises(google.GoogleOperationUnavailable):
        await google.run_google_retryable_operation(
            lambda: None,
            safety=google.GoogleOperationSafety.READ,
            sleep=lambda _delay: _immediate(),
            random_value=lambda: 0.5,
        )
    assert attempts == 3

    attempts = 0

    async def permanent(operation, *_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise google.GoogleOperationRejected(400)

    monkeypatch.setattr(google, "run_google_operation", permanent)
    with pytest.raises(google.GoogleOperationRejected):
        await google.run_google_retryable_operation(
            lambda: None,
            safety=google.GoogleOperationSafety.READ,
            sleep=lambda _delay: _immediate(),
        )
    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_attempt_timeout_is_capped_by_remaining_deadline(monkeypatch):
    class _Clock:
        now = 0.0

        def time(self) -> float:
            return self.now

    clock = _Clock()
    monkeypatch.setattr(google, "settings", _retry_settings(attempts=2, deadline=5.0, provider_timeout=20.0))
    monkeypatch.setattr(google.asyncio, "get_running_loop", lambda: clock)
    attempt_timeouts = []

    async def deadline_consuming_attempt(_operation, *_args, timeout, **_kwargs):
        attempt_timeouts.append(timeout)
        clock.now += timeout
        raise google.GoogleOperationUnavailable(503)

    monkeypatch.setattr(google, "run_google_operation", deadline_consuming_attempt)

    with pytest.raises(google.GoogleOperationUnavailable):
        await google.run_google_retryable_operation(
            lambda: None,
            safety=google.GoogleOperationSafety.READ,
            random_value=lambda: 0.5,
        )

    assert attempt_timeouts == [5.0]


async def _immediate() -> None:
    return None


@pytest.mark.asyncio
async def test_ambiguous_write_timeout_never_retries(monkeypatch):
    monkeypatch.setattr(google, "settings", _retry_settings(attempts=5))
    attempts = 0

    async def timeout(operation, *_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise google.GoogleOperationTimeout()

    monkeypatch.setattr(google, "run_google_operation", timeout)
    with pytest.raises(google.GoogleOperationTimeout):
        await google.run_google_retryable_operation(
            lambda: None,
            safety=google.GoogleOperationSafety.AMBIGUOUS_WRITE,
        )
    assert attempts == 1
    assert google.classify_google_error(
        google.GoogleOperationTimeout(), safety=google.GoogleOperationSafety.AMBIGUOUS_WRITE
    ).category is google.GoogleErrorCategory.AMBIGUOUS_WRITE


class _Request:
    pass


class _Messages:
    def __init__(self) -> None:
        self.modify_calls = []

    def modify(self, **kwargs):
        self.modify_calls.append(kwargs)
        return _Request()


class _Users:
    def __init__(self, messages: _Messages) -> None:
        self._messages = messages

    def messages(self):
        return self._messages


class _Gmail:
    def __init__(self) -> None:
        self.messages_api = _Messages()

    def users(self):
        return _Users(self.messages_api)


@pytest.mark.asyncio
async def test_explicit_star_state_is_idempotent_and_never_reads_before_modify(monkeypatch):
    service = _Gmail()
    observed_safety = []

    async def execute(_service, _request, *, safety):
        observed_safety.append(safety)
        return {}

    async def invalidate(*_args):
        return True

    monkeypatch.setattr(google_mail, "_gmail_execute", execute)
    monkeypatch.setattr(google_mail, "invalidate_user_mail_cache", invalidate)
    user = {"user_id": "owner"}

    starred = await google_mail.set_star_state(
        StarStateUpdate(starred=True), email_id="message", user_info=user, service=service
    )
    repeated = await google_mail.set_star_state(
        StarStateUpdate(starred=True), email_id="message", user_info=user, service=service
    )
    unstarred = await google_mail.set_star_state(
        StarStateUpdate(starred=False), email_id="message", user_info=user, service=service
    )

    assert starred["status"] == repeated["status"] == "starred"
    assert unstarred["status"] == "unstarred"
    assert [call["body"] for call in service.messages_api.modify_calls] == [
        {"addLabelIds": ["STARRED"]},
        {"addLabelIds": ["STARRED"]},
        {"removeLabelIds": ["STARRED"]},
    ]
    assert observed_safety == [google.GoogleOperationSafety.IDEMPOTENT_WRITE] * 3


@pytest.mark.asyncio
async def test_mark_as_read_unexpected_error_returns_500(monkeypatch):
    """mark_as_read must log and return 500 when an unexpected error occurs.

    Previously the endpoint only caught HttpError, so a non-Google exception
    would surface as an unlogged opaque 500 with no audit trail.
    """
    from fastapi import HTTPException

    service = _Gmail()

    async def explode(_service, _request, **_kwargs):
        raise RuntimeError("transient serialisation failure")

    async def noop_invalidate(*_args):
        return True

    monkeypatch.setattr(google_mail, "_gmail_execute", explode)
    monkeypatch.setattr(google_mail, "invalidate_user_mail_cache", noop_invalidate)

    with pytest.raises(HTTPException) as raised:
        await google_mail.mark_as_read(
            email_id="msg-42",
            user_info={"user_id": "owner"},
            service=service,
        )

    assert raised.value.status_code == 500
    assert "Mail operation failed" in raised.value.detail


@pytest.mark.asyncio
async def test_mark_as_read_handles_non_utf8_http_error_content_safely(monkeypatch):
    """Non-UTF-8 bytes in HttpError content must not raise UnicodeDecodeError during logging."""
    from fastapi import HTTPException
    import httplib2

    service = _Gmail()
    corrupt_content = b"\x80\xff\xfe\xfd invalid utf-8"
    resp = httplib2.Response({"status": "502"})
    http_error = HttpError(resp, corrupt_content)

    async def explode(_service, _request, **_kwargs):
        raise http_error

    monkeypatch.setattr(google_mail, "_gmail_execute", explode)

    with pytest.raises(HTTPException) as raised:
        await google_mail.mark_as_read(
            email_id="msg-corrupt",
            user_info={"user_id": "owner"},
            service=service,
        )

    assert raised.value.status_code == 502
    assert raised.value.detail == "Gmail request failed. Please try again."


def test_format_http_error_decodes_safely():
    import httplib2
    resp = httplib2.Response({"status": "400"})
    error = HttpError(resp, b"corrupted: \xff\xfe")
    formatted = google_mail._format_http_error(error)
    assert "\ufffd" in formatted

