"""Gmail-specific provider adapter built on the shared Google transport."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from googleapiclient.discovery import Resource

from server.integrations.google import (
    GoogleOperationInternal,
    GoogleOperationRejected,
    GoogleOperationSafety,
    GoogleOperationTimeout,
    GoogleOperationUnavailable,
    execute_google_idempotent_request,
    execute_google_read_request,
    execute_google_request,
)


def mail_provider_http_error(error: Exception) -> HTTPException:
    if isinstance(error, GoogleOperationTimeout):
        return HTTPException(status_code=504, detail="Gmail request timed out. Please try again.")
    if isinstance(error, GoogleOperationUnavailable):
        return HTTPException(status_code=503, detail="Gmail is temporarily unavailable. Please try again.")
    if isinstance(error, GoogleOperationRejected):
        status_code = error.status_code if error.status_code and 400 <= error.status_code < 500 else 502
        return HTTPException(status_code=status_code, detail="Gmail request failed. Please try again.")
    return HTTPException(status_code=502, detail="Gmail request failed. Please try again.")


async def execute_gmail_request(
    service: Resource,
    request: Any,
    *,
    safety: GoogleOperationSafety = GoogleOperationSafety.AMBIGUOUS_WRITE,
) -> Any:
    """Execute one Gmail request; retry only the explicitly safe operation kinds."""
    try:
        if safety is GoogleOperationSafety.READ:
            return await execute_google_read_request(request, resource=service)
        if safety is GoogleOperationSafety.IDEMPOTENT_WRITE:
            return await execute_google_idempotent_request(request, resource=service)
        return await execute_google_request(request, resource=service)
    except (
        GoogleOperationInternal,
        GoogleOperationRejected,
        GoogleOperationTimeout,
        GoogleOperationUnavailable,
    ) as exc:
        raise mail_provider_http_error(exc) from None
