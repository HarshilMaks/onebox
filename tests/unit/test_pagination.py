import base64
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from server.pagination import InvalidPageToken, decode_page_token, encode_page_token


def _token(payload) -> str:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def test_page_token_round_trips_position():
    created_at = datetime(2026, 10, 20, 12, 0, 0, 123456, tzinfo=timezone.utc)
    action_id = uuid4()

    token = encode_page_token(created_at, action_id)

    assert "=" not in token
    assert decode_page_token(token) == (created_at, action_id)


@pytest.mark.parametrize(
    "token",
    [
        "!!!not-base64!!!",
        _token("not json"),
        _token([]),
        _token({"c": 5, "i": "x"}),
        _token({"c": "2026-10-20T12:00:00+00:00"}),
        _token({"c": "yesterday", "i": str(uuid4())}),
        _token({"c": "2026-10-20T12:00:00+00:00", "i": "not-a-uuid"}),
        # A timestamp without a timezone would compare ambiguously with timestamptz.
        _token({"c": "2026-10-20T12:00:00", "i": str(uuid4())}),
    ],
)
def test_malformed_page_tokens_are_rejected(token):
    with pytest.raises(InvalidPageToken):
        decode_page_token(token)
