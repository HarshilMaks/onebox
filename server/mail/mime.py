"""Bounded Gmail MIME extraction and safe HTML rendering helpers.

Mail bodies are untrusted.  Plain text is preferred; HTML is exposed only after
strict sanitization in a separately named field so API clients cannot mistake it
for ordinary text.
"""

from __future__ import annotations

import base64
import binascii
import html
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urlsplit


MAX_BODY_BYTES = 256 * 1024
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_INLINE_IMAGE_BYTES = 1 * 1024 * 1024
MAX_HEADER_VALUE_CHARS = 4_096
MAX_MIME_PART_DEPTH = 32
MAX_MIME_PARTS = 512

_ALLOWED_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_VOID_TAGS = frozenset({"br"})
_DROP_CONTENT_TAGS = frozenset(
    {"form", "iframe", "math", "object", "script", "style", "svg", "template"}
)
_TEXT_BREAK_TAGS = frozenset({"br", "div", "li", "p", "tr"})


@dataclass(frozen=True)
class MailContent:
    """Safe API representation of a decoded mail body."""

    body: str
    sanitized_html: str | None = None


class _StrictHtmlSanitizer(HTMLParser):
    """Emit a deliberately small, non-networked HTML subset.

    No style attributes, images, forms, SVG, data URLs, or remote resources are
    retained.  The only permitted attribute is an ``https`` or ``mailto`` link
    target on ``a`` elements.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._output: list[str] = []
        self._text: list[str] = []
        self._open_tags: list[str] = []
        self._drop_depth = 0

    @staticmethod
    def _safe_href(value: str) -> str | None:
        parsed = urlsplit(value.strip())
        if parsed.scheme == "https" and parsed.netloc:
            return value
        if parsed.scheme == "mailto" and parsed.path:
            return value
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._drop_depth:
            if tag in _DROP_CONTENT_TAGS:
                self._drop_depth += 1
            return
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return

        rendered_attrs = ""
        if tag == "a":
            href = next((value for name, value in attrs if name.lower() == "href" and value), None)
            safe_href = self._safe_href(href) if href else None
            if safe_href:
                rendered_attrs = (
                    f' href="{html.escape(safe_href, quote=True)}" rel="noopener noreferrer"'
                )
        self._output.append(f"<{tag}{rendered_attrs}>")
        if tag not in _VOID_TAGS:
            self._open_tags.append(tag)
        if tag in _TEXT_BREAK_TAGS:
            self._text.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._drop_depth:
            if tag in _DROP_CONTENT_TAGS:
                self._drop_depth -= 1
            return
        if tag not in _ALLOWED_TAGS or tag in _VOID_TAGS or tag not in self._open_tags:
            return
        while self._open_tags:
            current = self._open_tags.pop()
            self._output.append(f"</{current}>")
            if current == tag:
                break
        if tag in _TEXT_BREAK_TAGS:
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth or not data:
            return
        self._output.append(html.escape(data, quote=False))
        self._text.append(data)

    def rendered_html(self) -> str:
        while self._open_tags:
            self._output.append(f"</{self._open_tags.pop()}>")
        return "".join(self._output)

    def rendered_text(self) -> str:
        return "".join(self._text).strip()


def decode_base64url_text(data: object, *, max_bytes: int) -> str | None:
    """Decode bounded Gmail base64url content without allocating unbounded data."""
    if not isinstance(data, str) or not data:
        return None
    # Base64 is at least 3/4 decoded size.  The small padding allowance accepts
    # normal Gmail values while rejecting oversized payloads before decoding.
    max_encoded = ((max_bytes + 2) // 3) * 4 + 4
    if len(data) > max_encoded:
        return None
    try:
        padded = data + "=" * (-len(data) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(decoded) > max_bytes:
        return None
    return decoded.decode("utf-8", errors="replace")


def _walk_parts(payload: object) -> Iterable[dict[str, Any]]:
    """Yield depth-first MIME parts within bounded untrusted structure limits."""
    if not isinstance(payload, dict):
        return

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
        if visited_parts > MAX_MIME_PARTS:
            return
        if not isinstance(part, dict):
            continue

        yield part
        children = part.get("parts")
        if depth < MAX_MIME_PART_DEPTH and isinstance(children, list):
            stack.append((iter(children), depth + 1))


def extract_mail_content(payload: object) -> MailContent:
    """Extract the first bounded plain body, otherwise bounded sanitized HTML."""
    plain_body: str | None = None
    html_body: str | None = None
    for part in _walk_parts(payload):
        mime_type = part.get("mimeType")
        if not isinstance(mime_type, str):
            continue
        body = part.get("body")
        data = body.get("data") if isinstance(body, dict) else None
        decoded = decode_base64url_text(data, max_bytes=MAX_BODY_BYTES)
        if decoded is None:
            continue
        if mime_type.lower() == "text/plain" and plain_body is None:
            plain_body = decoded
        elif mime_type.lower() == "text/html" and html_body is None:
            html_body = decoded

    if plain_body is not None:
        return MailContent(body=plain_body)
    if html_body is None:
        return MailContent(body="")

    sanitizer = _StrictHtmlSanitizer()
    sanitizer.feed(html_body)
    sanitizer.close()
    sanitized_html = sanitizer.rendered_html()
    return MailContent(body=sanitizer.rendered_text(), sanitized_html=sanitized_html or None)


def parse_recipient_addresses(value: object) -> list[str]:
    """Parse mailbox headers with the standard library and omit malformed entries."""
    if not isinstance(value, str):
        return []
    addresses: list[str] = []
    for _display_name, address in getaddresses([value[:MAX_HEADER_VALUE_CHARS]]):
        local, separator, domain = address.rpartition("@")
        if separator and local and domain and not any(character.isspace() for character in address):
            addresses.append(address)
    return addresses


def first_recipient_address(value: object, *, fallback: str) -> str:
    addresses = parse_recipient_addresses(value)
    return addresses[0] if addresses else fallback


def parse_message_date(value: object, internal_date: object = None) -> str:
    """Return an ISO-8601 time with an aware UTC fallback for malformed headers."""
    if isinstance(value, str):
        try:
            parsed = parsedate_to_datetime(value[:MAX_HEADER_VALUE_CHARS])
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    try:
        return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc).isoformat()
