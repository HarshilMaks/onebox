from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

import pytest

from server import redis_cache
from server.mail.mime import (
    MAX_BODY_BYTES,
    MAX_MIME_PART_DEPTH,
    MAX_MIME_PARTS,
    extract_mail_content,
    parse_message_date,
    parse_recipient_addresses,
)
from server.routes import google_mail
from server.services import mailbox
from server.schemas import EmailDetail


def _encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def test_mime_prefers_plain_text_and_strips_unsafe_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _encoded("<script>bad()</script><p>HTML</p>")}},
            {"mimeType": "text/plain", "body": {"data": _encoded("Plain body")}},
        ],
    }

    content = extract_mail_content(payload)

    assert content.body == "Plain body"
    assert content.sanitized_html is None


def test_html_only_mime_is_explicitly_sanitized_and_never_loads_remote_content():
    hostile_html = """
        <p onclick="steal()">Hello <a href="https://example.test/path">safe</a>
        <a href="javascript:alert(1)">unsafe</a></p>
        <img src="https://tracker.test/pixel" onerror="steal()">
        <img src="cid:logo"><img src="data:image/png;base64,AA==">
        <form action="https://attacker.test"><input value="secret"></form>
        <svg><script>alert(1)</script></svg><style>p { background: url(https://x.test) }</style>
    """

    content = extract_mail_content({"mimeType": "text/html", "body": {"data": _encoded(hostile_html)}})

    assert content.body.startswith("Hello safe")
    assert content.sanitized_html is not None
    sanitized = content.sanitized_html.lower()
    assert '<a href="https://example.test/path" rel="noopener noreferrer">safe</a>' in sanitized
    for forbidden in ("script", "onclick", "javascript:", "<img", "tracker", "cid:", "data:", "<form", "<svg", "<style", "url("):
        assert forbidden not in sanitized


@pytest.mark.asyncio
async def test_parse_message_handles_malformed_headers_base64_and_dates_safely():
    parsed = await google_mail.parse_message(
        None,
        {
            "id": "message-1",
            "snippet": "fallback snippet",
            "internalDate": "0",
            "payload": {
                "headers": [
                    {"name": "To", "value": "Display <first@example.test>, second@example.test"},
                    {"name": "Date", "value": "not a valid mail date"},
                    {"name": "From", "value": "Sender <sender@example.test>"},
                    {"name": "Subject", "value": "Subject"},
                    {"wrong": "header"},
                ],
                "mimeType": "text/html",
                "body": {"data": "%%%not-base64%%%"},
            },
        },
    )

    assert parsed["body"] == "fallback snippet"
    assert parsed["sanitized_html"] is None
    assert parsed["to"] == ["first@example.test", "second@example.test"]
    assert parsed["sender"] == "sender@example.test"
    assert datetime.fromisoformat(parsed["date"]).tzinfo == timezone.utc
    assert EmailDetail.model_validate(parsed).body == "fallback snippet"


def test_mime_decoding_is_bounded_and_address_date_helpers_are_safe():
    oversized = _encoded("x" * (MAX_BODY_BYTES + 1))
    assert extract_mail_content({"mimeType": "text/plain", "body": {"data": oversized}}).body == ""
    assert parse_recipient_addresses("broken, valid@example.test") == ["valid@example.test"]
    assert datetime.fromisoformat(parse_message_date("invalid date")).tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_deep_mime_payload_stops_at_depth_limit_and_uses_snippet_fallback():
    payload: dict[str, Any] = {"mimeType": "multipart/mixed", "parts": []}
    current = payload
    for _ in range(MAX_MIME_PART_DEPTH + 1):
        child: dict[str, Any] = {"mimeType": "multipart/mixed", "parts": []}
        current["parts"] = [child]
        current = child
    current.update({"mimeType": "text/plain", "body": {"data": _encoded("too deep")}})

    parsed = await mailbox.parse_message(None, {"id": "deep", "snippet": "safe snippet", "payload": payload})

    assert parsed["body"] == "safe snippet"


def test_broad_mime_payload_stops_at_total_part_limit():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [{"mimeType": "application/octet-stream"} for _ in range(MAX_MIME_PARTS)]
        + [{"mimeType": "text/plain", "body": {"data": _encoded("too broad")}}],
    }

    content = extract_mail_content(payload)

    assert content.body == ""


class _MemoryRedisAdapter:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.increments: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.values.pop(key, None)
        return len(keys)

    async def incr(self, key: str) -> int:
        self.increments.append(key)
        value = int(self.values.get(key, "0")) + 1
        self.values[key] = str(value)
        return value


@pytest.mark.asyncio
async def test_mail_cache_supports_list_payloads_and_generation_invalidation(monkeypatch):
    adapter = _MemoryRedisAdapter()
    monkeypatch.setattr(redis_cache, "get_redis_adapter", lambda: adapter)

    first_key = await redis_cache.user_mail_cache_key("user-1", "search:query")
    assert await redis_cache.cache_set(first_key, [{"id": "one"}], ttl=60)
    assert await redis_cache.cache_get(first_key) == [{"id": "one"}]

    assert await redis_cache.invalidate_user_mail_cache("user-1", "one")
    second_key = await redis_cache.user_mail_cache_key("user-1", "search:query")
    assert first_key != second_key
    assert await redis_cache.cache_get(second_key) is None
    # A stale in-flight write can remain physically present but cannot be read by
    # requests using the new generation.
    assert await redis_cache.cache_get(first_key) == [{"id": "one"}]
    assert adapter.increments == ["user:user-1:mail:cache-generation"]

    adapter.values[second_key] = "not-json"
    assert await redis_cache.cache_get(second_key) is None




@pytest.mark.asyncio
async def test_search_page_cache_round_trips_without_a_second_provider_call(monkeypatch):
    adapter = _MemoryRedisAdapter()
    service = _PagedGmailService()
    monkeypatch.setattr(redis_cache, "get_redis_adapter", lambda: adapter)

    async def execute(_service, request, **_kwargs):
        return request.run()

    monkeypatch.setattr(mailbox, "execute_gmail_request", execute)
    user = {"user_id": "user-1"}
    first = await google_mail.search_endpoint(
        q="from:owner@example.test",
        limit=2,
        page_token=None,
        user_info=user,
        service=service,
    )
    second = await google_mail.search_endpoint(
        q="from:owner@example.test",
        limit=2,
        page_token=None,
        user_info=user,
        service=service,
    )

    assert second == first
    assert len(service.list_calls) == 1
class _Request:
    def __init__(self, callback):
        self._callback = callback

    def run(self):
        return self._callback()


class _Batch:
    def __init__(self) -> None:
        self.items: list[tuple[str, _Request, Any]] = []

    def add(self, request: _Request, callback, request_id: str) -> None:
        self.items.append((request_id, request, callback))

    def run(self):
        for request_id, request, callback in self.items:
            callback(request_id, request.run(), None)
        return None


class _PagedGmailService:
    def __init__(self) -> None:
        self.list_calls: list[dict[str, Any]] = []
        self.details = {message_id: self._message(message_id) for message_id in ("one", "two", "three", "four")}

    @staticmethod
    def _message(message_id: str) -> dict[str, Any]:
        return {
            "id": message_id,
            "threadId": f"thread-{message_id}",
            "payload": {"headers": [{"name": "Subject", "value": message_id}]},
        }

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        page_token = kwargs.get("pageToken")
        if kwargs.get("labelIds") == ["STARRED"]:
            pages = {
                None: ({"messages": [{"id": "one"}, {"id": "two"}], "nextPageToken": "starred-next"}),
                "starred-next": ({"messages": [{"id": "three"}], "nextPageToken": None}),
            }
        else:
            pages = {
                None: ({"messages": [{"id": "one"}, {"id": "two"}], "nextPageToken": "search-next"}),
                "search-next": ({"messages": [{"id": "four"}], "nextPageToken": None}),
            }
        return _Request(lambda: pages[page_token])

    def get(self, *, id: str, **_kwargs):
        return _Request(lambda: self.details[id])

    def new_batch_http_request(self):
        return _Batch()


@pytest.fixture
def bypass_mail_cache(monkeypatch):
    async def no_cache_key(_user_id: str, namespace: str) -> str:
        return namespace

    async def cache_miss(_key: str):
        return None

    async def ignored_cache_write(*_args, **_kwargs):
        return True

    monkeypatch.setattr(google_mail, "user_mail_cache_key", no_cache_key)
    monkeypatch.setattr(google_mail, "cache_get", cache_miss)
    monkeypatch.setattr(google_mail, "cache_set", ignored_cache_write)

    async def execute(_service, request, **_kwargs):
        return request.run()

    monkeypatch.setattr(mailbox, "execute_gmail_request", execute)


@pytest.mark.asyncio
async def test_starred_pages_use_gmail_tokens_without_gaps_or_full_mailbox_scans(bypass_mail_cache):
    service = _PagedGmailService()
    user = {"user_id": "user-1"}

    first = await google_mail.fetch_emails(
        folder="starred",
        limit=2,
        page_token=None,
        user_info=user,
        service=service,
    )
    second = await google_mail.fetch_emails(
        folder="starred",
        limit=2,
        page_token=first["next_page_token"],
        user_info=user,
        service=service,
    )

    assert [item["id"] for item in first["emails"] + second["emails"]] == ["one", "two", "three"]
    assert [call["labelIds"] for call in service.list_calls] == [["STARRED"], ["STARRED"]]
    assert [call["maxResults"] for call in service.list_calls] == [2, 2]


@pytest.mark.asyncio
async def test_search_pages_are_cached_as_dicts_and_follow_gmail_tokens(bypass_mail_cache):
    service = _PagedGmailService()
    user = {"user_id": "user-1"}

    first = await google_mail.search_endpoint(
        q="from:owner@example.test",
        limit=2,
        page_token=None,
        user_info=user,
        service=service,
    )
    second = await google_mail.search_endpoint(
        q="from:owner@example.test",
        limit=2,
        page_token=first["next_page_token"],
        user_info=user,
        service=service,
    )

    assert [item["id"] for item in first["emails"] + second["emails"]] == ["one", "two", "four"]
    assert [call["q"] for call in service.list_calls] == ["from:owner@example.test", "from:owner@example.test"]
    assert len(service.list_calls) == 2


class _PartiallyFailingBatch(_Batch):
    def __init__(self, failed_message_ids: set[str]) -> None:
        super().__init__()
        self.failed_message_ids = failed_message_ids

    def run(self):
        for request_id, request, callback in self.items:
            if request_id in self.failed_message_ids:
                callback(request_id, None, RuntimeError("batch detail failure"))
            else:
                callback(request_id, request.run(), None)
        return None


class _DetailFailureService(_PagedGmailService):
    def __init__(self, *, batch_failure_ids: set[str], individual_failure_ids: set[str]) -> None:
        super().__init__()
        self.batch_failure_ids = batch_failure_ids
        self.individual_failure_ids = individual_failure_ids
        self.detail_get_calls: list[str] = []

    def get(self, *, id: str, **kwargs):
        self.detail_get_calls.append(id)
        if id in self.individual_failure_ids and self.detail_get_calls.count(id) > 1:
            return _Request(lambda: _raise_detail_unavailable())
        return super().get(id=id, **kwargs)

    def new_batch_http_request(self):
        return _PartiallyFailingBatch(self.batch_failure_ids)


def _raise_detail_unavailable():
    raise google_mail.HTTPException(status_code=503, detail="Gmail is temporarily unavailable. Please try again.")


@pytest.mark.asyncio
async def test_page_retries_missing_batch_detail_as_safe_read_before_returning_token(monkeypatch):
    service = _DetailFailureService(batch_failure_ids={"two"}, individual_failure_ids=set())
    safeties = []

    async def execute(_service, request, **kwargs):
        safeties.append(kwargs["safety"])
        return request.run()

    monkeypatch.setattr(mailbox, "execute_gmail_request", execute)

    page = await mailbox.fetch_message_page(service, user_id="me", limit=2)

    assert [email["id"] for email in page["emails"]] == ["one", "two"]
    assert page["next_page_token"] == "search-next"
    assert service.detail_get_calls == ["one", "two", "two"]
    assert safeties == [
        mailbox.GoogleOperationSafety.READ,
        mailbox.GoogleOperationSafety.READ,
        mailbox.GoogleOperationSafety.READ,
    ]


@pytest.mark.asyncio
async def test_page_with_unavailable_detail_is_not_cached_or_exposed_with_next_token(monkeypatch):
    service = _DetailFailureService(batch_failure_ids={"two"}, individual_failure_ids={"two"})
    cache_writes = []

    async def cache_key(_user_id: str, namespace: str) -> str:
        return namespace

    async def cache_miss(_key: str):
        return None

    async def cache_set(*args, **kwargs):
        cache_writes.append((args, kwargs))
        return True

    async def execute(_service, request, **_kwargs):
        return request.run()

    monkeypatch.setattr(google_mail, "user_mail_cache_key", cache_key)
    monkeypatch.setattr(google_mail, "cache_get", cache_miss)
    monkeypatch.setattr(google_mail, "cache_set", cache_set)
    monkeypatch.setattr(mailbox, "execute_gmail_request", execute)

    with pytest.raises(google_mail.HTTPException) as raised:
        await google_mail.fetch_emails(
            folder="inbox",
            limit=2,
            page_token=None,
            user_info={"user_id": "user-1"},
            service=service,
        )

    assert raised.value.status_code == 503
    assert cache_writes == []
    assert service.detail_get_calls == ["one", "two", "two"]
