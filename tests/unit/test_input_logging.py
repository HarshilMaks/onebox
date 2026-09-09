from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from pydantic import ValidationError

from server import main

from server.logging_config import RedactingCorrelationFilter, bind_correlation_id, reset_correlation_id, setup_logging
from server.routes.agent_router import AgentQuery
from server.schemas import EmailDraft, OAuthCallback


def _record(message, args=()):
    return logging.LogRecord(
        name="onebox.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_agent_and_mail_request_bounds_fail_before_dispatch():
    assert AgentQuery(input="x" * 8_000).input
    with pytest.raises(ValidationError):
        AgentQuery(input="x" * 8_001)

    valid = EmailDraft(to=["owner@example.com"], subject="Hello", body="Body")
    assert valid.subject == "Hello"
    with pytest.raises(ValidationError):
        EmailDraft(to=["owner@example.com"] * 51, subject="Hello", body="Body")
    with pytest.raises(ValidationError):
        EmailDraft(to=["owner@example.com"], subject="x" * 256, body="Body")
    with pytest.raises(ValidationError):
        EmailDraft(to=["owner@example.com"], subject="Hello", body="x" * 20_001)
    with pytest.raises(ValidationError):
        OAuthCallback(code="x" * 4_097, state="valid-state")


def test_log_filter_redacts_secrets_content_and_email_addresses():
    sentinel = "SENTINEL_SECRET_EMAIL_BODY"
    record = _record("provider token=%s query=%s", (sentinel, "from:owner@example.com"))
    token = bind_correlation_id("request:test-correlation")
    try:
        assert RedactingCorrelationFilter().filter(record) is True
    finally:
        reset_correlation_id(token)

    rendered = record.getMessage()
    assert sentinel not in rendered
    assert "owner@example.com" not in rendered
    assert "request:test-correlation" == record.correlation_id


def test_log_filter_redacts_sensitive_structured_tool_arguments():
    sentinel = "SENTINEL_TOOL_BODY"
    record = _record("tool event", {"subject": "private", "email_body": sentinel, "count": 1})
    RedactingCorrelationFilter().filter(record)
    assert record.args["subject"] == "[redacted]"
    assert record.args["email_body"] == "[redacted]"
    assert record.args["count"] == 1


def test_logging_bootstrap_is_idempotent():
    setup_logging()
    setup_logging()


@pytest.mark.asyncio
async def test_safe_error_handlers_use_stable_public_envelopes():
    request = Request({"type": "http", "method": "GET", "path": "/test", "headers": []})
    provider_body = "SENTINEL_PROVIDER_BODY"
    response = await main.http_error_handler(
        request,
        HTTPException(status_code=502, detail=provider_body),
    )
    assert response.status_code == 502
    assert b'"error":"provider_error"' in response.body
    assert provider_body.encode() not in response.body

    validation = RequestValidationError([])
    response = await main.validation_error_handler(request, validation)
    assert response.status_code == 422
    assert b'"error":"validation_error"' in response.body
