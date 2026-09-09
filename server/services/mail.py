"""Stateless inbound Gmail parsing/filtering helpers for durable workers."""
from __future__ import annotations

import base64
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def should_process_email(email_content: dict[str, Any]) -> bool:
    """Apply the current analysis-only skip policy without mutating mailbox state."""
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
    """Extract boundedly useful message fields for analysis; no provider calls occur here."""
    try:
        payload = email_data.get("payload")
        if not isinstance(payload, dict):
            return {"error": "Missing payload"}
        raw_headers = payload.get("headers")
        if not isinstance(raw_headers, list):
            return {"error": "Missing headers"}
        headers = {
            item.get("name"): item.get("value")
            for item in raw_headers
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
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
    except Exception:
        logger.exception("Unable to extract inbound Gmail content")
        return {"error": "Invalid message payload"}


def get_email_body(payload: dict[str, Any]) -> str:
    """Prefer the first available text body; malformed data is treated as absent."""
    body = payload.get("body")
    if isinstance(body, dict) and isinstance(body.get("data"), str):
        try:
            return base64.urlsafe_b64decode(body["data"]).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return ""
    parts = payload.get("parts")
    if not isinstance(parts, list):
        return ""
    for part in parts:
        if not isinstance(part, dict) or part.get("mimeType") not in {"text/plain", "text/html"}:
            continue
        extracted = get_email_body(part)
        if extracted:
            return extracted
    return ""
