"""Opaque keyset page tokens for list endpoints that sort newest-first by (timestamp, id)."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID


class InvalidPageToken(Exception):
    pass


def encode_page_token(created_at: datetime, row_id: UUID) -> str:
    """Opaque position marker: "continue after this (created_at, id) row"."""
    raw = json.dumps({"c": created_at.isoformat(), "i": str(row_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_page_token(token: str) -> tuple[datetime, UUID]:
    try:
        padded = token + "=" * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(data, dict) or not isinstance(data.get("c"), str) or not isinstance(data.get("i"), str):
            raise ValueError("unexpected cursor shape")
        created_at = datetime.fromisoformat(data["c"])
        row_id = UUID(data["i"])
    except ValueError:
        raise InvalidPageToken() from None
    if created_at.tzinfo is None:
        raise InvalidPageToken()
    return created_at, row_id
