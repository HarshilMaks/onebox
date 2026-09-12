from __future__ import annotations

from types import SimpleNamespace

import pytest

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
