import pytest

from server.request_limits import RequestBodyLimitMiddleware


def _scope(headers=()):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/test",
        "raw_path": b"/test",
        "query_string": b"",
        "headers": list(headers),
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


async def _invoke(middleware, *, headers=(), messages=()):
    pending = iter(messages)
    sent = []

    async def receive():
        return next(pending)

    async def send(message):
        sent.append(message)

    await middleware(_scope(headers), receive, send)
    return sent


def _response_body(sent):
    return b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_before_receiving_or_dispatch():
    dispatched = False

    async def app(_scope, _receive, _send):
        nonlocal dispatched
        dispatched = True

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=8)
    sent = await _invoke(middleware, headers=((b"content-length", b"9"),))

    assert dispatched is False
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert b'"error":"payload_too_large"' in _response_body(sent)


@pytest.mark.asyncio
async def test_chunked_oversized_body_is_rejected_before_dispatch():
    dispatched = False

    async def app(_scope, _receive, _send):
        nonlocal dispatched
        dispatched = True

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=8)
    sent = await _invoke(
        middleware,
        messages=(
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"56789", "more_body": False},
        ),
    )

    assert dispatched is False
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_bounded_body_is_replayed_to_the_application():
    received = []

    async def app(_scope, receive, send):
        received.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=8)
    sent = await _invoke(
        middleware,
        messages=({"type": "http.request", "body": b"12345678", "more_body": False},),
    )

    assert received == [{"type": "http.request", "body": b"12345678", "more_body": False}]
    assert sent[0]["status"] == 204
