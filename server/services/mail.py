"""Stateless inbound Gmail parsing/filtering helpers for durable workers."""
from __future__ import annotations

import base64
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def should_process_email(email_content: dict[str, Any]) -> bool:
    """Apply the current analysis-only skip policy without mutating mailbox state."""
    if "error" in email_content:
        return False
    from_field = str(email_content.get("from", "")).lower()
    subject = str(email_content.get("subject", "")).lower()
    labels = email_content.get("labels", [])
    if re.search(r"no[-_.]?reply|donotreply|noreply", from_field):
        return False
    spam_labels = {"SPAM", "CATEGORY_PROMOTIONS", "CATEGORY_FORUMS"}
    if any(label in spam_labels for label in labels):
        return False
    return not any(keyword in subject for keyword in ("unsubscribe", "newsletter", "promotion", "deal", "discount"))


def extract_email_content(email_data: dict[str, Any]) -> dict[str, Any]:
    """Extract bounded message fields; malformed provider payloads stay terminal errors."""
    try:
        if not isinstance(email_data, dict):
            raise ValueError("message must be an object")
        payload = email_data.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("missing payload")
        raw_headers = payload.get("headers")
        if not isinstance(raw_headers, list):
            raise ValueError("missing headers")
        headers: dict[str, str] = {}
        for item in raw_headers:
            if not isinstance(item, dict):
                raise ValueError("invalid header")
            name = item.get("name")
            value = item.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError("invalid header")
            headers[name] = value
        return {
            "id": email_data.get("id", ""),
            "threadId": email_data.get("threadId", ""),
            "subject": headers.get("Subject", "(No Subject)"),
            "from": headers.get("From", "Unknown"),
            "to": headers.get("To", "Unknown"),
            "date": headers.get("Date", "Unknown"),
            "labels": email_data.get("labelIds", []),
            "body": get_email_body(payload),
        }
    except (TypeError, ValueError, UnicodeDecodeError):
        logger.warning("Unable to extract malformed inbound Gmail content")
        return {"error": "Invalid message payload"}
    except Exception:
        logger.exception("Unable to extract inbound Gmail content")
        return {"error": "Invalid message payload"}


def get_email_body(payload: dict[str, Any]) -> str:
    """Extract text safely; malformed nested Gmail payloads are never coerced to empty text."""
    if not isinstance(payload, dict):
        raise ValueError("invalid MIME part")
    body = payload.get("body")
    if body is not None:
        if not isinstance(body, dict):
            raise ValueError("invalid body")
        encoded = body.get("data")
        if encoded is not None:
            if not isinstance(encoded, str):
                raise ValueError("invalid body data")
            padded = encoded + "=" * (-len(encoded) % 4)
            return base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
    parts = payload.get("parts")
    if parts is None:
        return ""
    if not isinstance(parts, list):
        raise ValueError("invalid MIME parts")
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("invalid MIME part")
        mime_type = part.get("mimeType")
        if not isinstance(mime_type, str):
            raise ValueError("invalid MIME type")
        if mime_type not in {"text/plain", "text/html"}:
            continue
        extracted = get_email_body(part)
        if extracted:
            return extracted
    return ""
