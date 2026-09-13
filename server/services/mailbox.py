"""Gmail mail-detail and paginated read use cases shared by HTTP routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from server.integrations.gmail import execute_gmail_request
from server.integrations.google import GoogleOperationSafety
from server.mail.mime import (
    MAX_HEADER_VALUE_CHARS,
    extract_mail_content,
    first_recipient_address,
    parse_message_date,
    parse_recipient_addresses,
)


logger = logging.getLogger(__name__)


async def parse_message(
    _service: Resource | None,
    msg: dict[str, Any],
    user_id_for_attachments: str = "me",
) -> dict[str, Any]:
    """Return a bounded, rendering-safe mail detail from a Gmail message."""
    del user_id_for_attachments
    payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
    headers: dict[str, str] = {}
    for header in payload.get("headers", []):
        if not isinstance(header, dict):
            continue
        name = header.get("name")
        value = header.get("value")
        if isinstance(name, str) and isinstance(value, str):
            headers[name.lower()] = value[:MAX_HEADER_VALUE_CHARS]

    content = extract_mail_content(payload)
    snippet = msg.get("snippet")
    safe_snippet = snippet[:MAX_HEADER_VALUE_CHARS] if isinstance(snippet, str) else ""
    sender_header = headers.get("from")
    sender_fallback = sender_header or "Unknown Sender"
    return {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "subject": headers.get("subject", "(No Subject)"),
        "sender": first_recipient_address(sender_header, fallback=sender_fallback),
        "to": parse_recipient_addresses(headers.get("to")),
        "cc": parse_recipient_addresses(headers.get("cc")),
        "snippet": safe_snippet,
        "body": content.body or safe_snippet,
        "sanitized_html": content.sanitized_html,
        "is_read": "UNREAD" not in msg.get("labelIds", []),
        "is_starred": "STARRED" in msg.get("labelIds", []),
        "labels": msg.get("labelIds", []),
        "date": parse_message_date(headers.get("date"), msg.get("internalDate")),
    }


def to_email_list_item(email: dict[str, Any]) -> dict[str, Any]:
    """Return the stable summary contract used by list and search endpoints."""
    return {
        "id": email["id"],
        "threadId": email.get("threadId"),
        "subject": email.get("subject", "(No Subject)"),
        "sender": email.get("sender", "Unknown Sender"),
        "to": email.get("to", []),
        "snippet": email.get("snippet", ""),
        "is_read": email.get("is_read", False),
        "is_starred": email.get("is_starred", False),
        "labels": email.get("labels", []),
        "date": email.get("date"),
    }


async def fetch_message_page(
    service: Resource,
    *,
    user_id: str,
    limit: int,
    page_token: str | None = None,
    label_ids: list[str] | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Fetch exactly one Gmail provider page and its bounded detail batch."""
    request_params: dict[str, Any] = {"userId": user_id, "maxResults": limit}
    if page_token:
        request_params["pageToken"] = page_token
    if label_ids:
        request_params["labelIds"] = label_ids
    if query:
        request_params["q"] = query

    response = await execute_gmail_request(
        service,
        service.users().messages().list(**request_params),
        safety=GoogleOperationSafety.READ,
    )
    messages_metadata = response.get("messages", [])
    if not isinstance(messages_metadata, list) or not messages_metadata:
        return {"emails": [], "next_page_token": response.get("nextPageToken")}

    requested_message_ids: list[str] = []
    for metadata in messages_metadata[:limit]:
        if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str):
            logger.warning("Gmail message page contained invalid message metadata")
            raise HTTPException(status_code=502, detail="Gmail returned an incomplete message page. Please try again.")
        requested_message_ids.append(metadata["id"])

    batch = service.new_batch_http_request()
    message_details: dict[str, dict[str, Any]] = {}
    failed_batch_detail_ids: set[str] = set()

    def parse_batch_response(request_id: str, batch_response: Any, exception: Exception | None) -> None:
        if exception is not None or not isinstance(batch_response, dict):
            failed_batch_detail_ids.add(request_id)
            return
        message_id = batch_response.get("id")
        if isinstance(message_id, str):
            message_details[message_id] = batch_response
        else:
            failed_batch_detail_ids.add(request_id)

    for message_id in requested_message_ids:
        batch.add(
            service.users().messages().get(userId=user_id, id=message_id, format="full"),
            callback=parse_batch_response,
            request_id=message_id,
        )

    try:
        await execute_gmail_request(service, batch, safety=GoogleOperationSafety.READ)
    except HTTPException:
        logger.warning("Gmail message detail batch request failed; retrying details individually")

    for message_id in requested_message_ids:
        if message_id in message_details:
            continue
        if message_id in failed_batch_detail_ids:
            logger.warning("Gmail message detail batch item failed; retrying individually")
        try:
            detail = await execute_gmail_request(
                service,
                service.users().messages().get(userId=user_id, id=message_id, format="full"),
                safety=GoogleOperationSafety.READ,
            )
        except HTTPException:
            logger.warning("Gmail message detail retry failed")
            raise
        if not isinstance(detail, dict) or detail.get("id") != message_id:
            logger.warning("Gmail message detail retry returned an invalid response")
            raise HTTPException(status_code=502, detail="Gmail returned an incomplete message page. Please try again.")
        message_details[message_id] = detail

    emails = [
        to_email_list_item(await parse_message(service, message_details[message_id], user_id))
        for message_id in requested_message_ids
    ]
    return {"emails": emails, "next_page_token": response.get("nextPageToken")}


async def search_emails(
    service: Resource,
    query: str,
    *,
    user_id: str = "me",
    limit: int = 20,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search one provider page; opaque Gmail tokens prevent gaps and duplicates."""
    try:
        return await fetch_message_page(
            service,
            user_id=user_id,
            limit=limit,
            page_token=page_token,
            query=query,
        )
    except HTTPException:
        raise
    except HttpError as exc:
        raise HTTPException(status_code=exc.resp.status, detail="Gmail request failed. Please try again.") from None
    except Exception:
        logger.exception("Unexpected Gmail search failure")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.") from None
