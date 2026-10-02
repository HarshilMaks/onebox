"""ASGI request-body limits enforced before FastAPI parses untrusted payloads."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Buffer at most a configured number of request bytes before dispatching.

    FastAPI's field validation runs after JSON decoding, so endpoint-level
    string limits cannot protect the process from oversized HTTP bodies. This
    middleware accepts and replays only bounded body chunks to the application.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int) or max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive integer")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared_size = self._content_length(scope)
        if declared_size is not None and declared_size > self.max_body_bytes:
            await self._reject(scope, receive, send)
            return

        messages: list[Message] = []
        received_size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            body = message.get("body", b"")
            if not isinstance(body, bytes):
                await self._reject(scope, receive, send)
                return
            received_size += len(body)
            if received_size > self.max_body_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        iterator = iter(messages)

        async def replay_receive() -> Message:
            return next(iterator, {"type": "http.disconnect"})

        await self.app(scope, replay_receive, send)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None
        return None

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={
                "error": "payload_too_large",
                "detail": "Request body exceeds the configured size limit.",
            },
        )
        await response(scope, receive, send)
