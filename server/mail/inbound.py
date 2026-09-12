"""Stateless inbound Gmail parsing and analysis-only triage filtering."""

from __future__ import annotations

import logging
import re
from typing import Any

from server.mail.mime import MAX_BODY_BYTES, decode_base64url_text


logger = logging.getLogger(__name__)

MAX_INBOUND_MIME_PART_DEPTH = 32
MAX_INBOUND_MIME_PARTS = 512


def should_process_email(email_content: dict[str, Any]) -> bool:
    """Apply the analysis-only skip policy without mutating mailbox state."""
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
    """Extract bounded fields; malformed provider payloads are terminal errors."""
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
    """Return the first nonempty text leaf within bounded, untrusted MIME structure."""
    if not isinstance(payload, dict):
        raise ValueError("invalid MIME part")

    stack = [(iter((payload,)), 0)]
    visited_parts = 0
    while stack:
        siblings, depth = stack[-1]
        try:
            part = next(siblings)
        except StopIteration:
            stack.pop()
            continue

        visited_parts += 1
        if visited_parts > MAX_INBOUND_MIME_PARTS:
            return ""
        if not isinstance(part, dict):
            raise ValueError("invalid MIME part")

        children = part.get("parts")
        if children is not None:
            if not isinstance(children, list):
                raise ValueError("invalid MIME parts")
            if children:
                if depth < MAX_INBOUND_MIME_PART_DEPTH:
                    stack.append((iter(children), depth + 1))
                continue

        mime_type = part.get("mimeType")
        if not isinstance(mime_type, str):
            raise ValueError("invalid MIME type")
        if mime_type.lower() not in {"text/plain", "text/html"}:
            continue

        body = part.get("body")
        if body is None:
            continue
        if not isinstance(body, dict):
            raise ValueError("invalid body")
        encoded = body.get("data")
        if encoded is None:
            continue
        if not isinstance(encoded, str):
            raise ValueError("invalid body data")
        if not encoded:
            continue
        extracted = decode_base64url_text(encoded, max_bytes=MAX_BODY_BYTES)
        if extracted is None:
            raise ValueError("invalid or oversized body data")
        if extracted:
            return extracted
    return ""
