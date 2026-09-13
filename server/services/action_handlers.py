"""Provider-specific command dispatch and reconciliation for pending actions.

The state service owns leases and durable transitions.  This module owns the
provider protocol: marker construction, a single write, and narrow lookup of a
previously dispatched command.  A write that may have reached Google is never
reported as safe to retry.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Any, Mapping

from server.action_payloads import canonicalize_action_payload
from server.integrations.google import (
    GoogleOperationRejected,
    GoogleProviderError,
    execute_google_read_request,
    execute_google_request,
    run_google_operation,
)
from tools.utils import create_raw_message, create_raw_reply_message, get_header_value


class ConfirmedActionFailure(RuntimeError):
    """A failure known to have occurred before external acceptance."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AmbiguousActionOutcome(RuntimeError):
    """A dispatched operation whose provider acceptance cannot be proved."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: str  # succeeded, failed, reconciliation_required
    result: dict[str, Any] | None
    error_code: str | None
    evidence: dict[str, Any]


def provider_for_action(action_type: str) -> str:
    providers = {
        "send_email": "gmail",
        "send_reply": "gmail",
        "create_event": "calendar",
        "create_task": "tasks",
    }
    try:
        return providers[action_type]
    except KeyError as exc:
        raise ConfirmedActionFailure("unsupported_action") from exc


def gmail_message_id(command_key: str) -> str:
    # 128 deterministic bits avoid MIME header folding while keeping collision
    # probability negligible for a command marker.
    digest = hashlib.sha256(command_key.encode("utf-8")).hexdigest()[:32]
    return f"<onebox.{digest}@onebox.invalid>"


def calendar_event_id(command_key: str) -> str:
    # Calendar event IDs accept lower-case hex characters; keep a stable prefix
    # and a deterministic size comfortably inside Google's documented bounds.
    return "ob" + hashlib.sha256(command_key.encode("utf-8")).hexdigest()[:40]


def task_marker(command_key: str) -> str:
    return f"[onebox-command-key:{command_key}]"


def _marker_headers(command_key: str) -> dict[str, str]:
    return {"X-OneBox-Command-Key": command_key}


def _is_provider_rejection(error: GoogleOperationRejected, status: int | None = None) -> bool:
    return status is None or error.status_code == status


async def execute_action(
    action: Mapping[str, Any],
    *,
    gmail_service: Any = None,
    calendar_service: Any = None,
    tasks_service: Any = None,
) -> dict[str, Any]:
    """Dispatch one write once.  Any transport uncertainty becomes ambiguity."""
    action_type = str(action["action_type"])
    try:
        payload = canonicalize_action_payload(action_type, action["payload"])
    except (TypeError, ValueError) as exc:
        raise ConfirmedActionFailure("payload_invalid") from exc

    try:
        if action_type == "send_email":
            if gmail_service is None:
                raise ConfirmedActionFailure("gmail_unavailable_before_dispatch")
            message = create_raw_message(
                payload["sender_email"],
                [payload["recipient_email"]],
                payload["subject"],
                payload["email_body"],
                message_id=gmail_message_id(action["command_key"]),
                extra_headers=_marker_headers(action["command_key"]),
            )
            request = gmail_service.users().messages().send(userId="me", body=message)
            sent = await execute_google_request(request, resource=gmail_service)
            if not sent or not sent.get("id"):
                raise AmbiguousActionOutcome("gmail_unconfirmed")
            return {"external_id": sent["id"], "provider": "gmail"}

        if action_type == "send_reply":
            if gmail_service is None:
                raise ConfirmedActionFailure("gmail_unavailable_before_dispatch")
            message = create_raw_reply_message(
                payload["sender_email"],
                [payload["recipient_email"]],
                payload["original_subject"],
                payload["reply_message"],
                payload["thread_id"],
                payload["original_rfc_message_id"],
                payload["original_references"],
                message_id=gmail_message_id(action["command_key"]),
                extra_headers=_marker_headers(action["command_key"]),
            )
            request = gmail_service.users().messages().send(userId="me", body=message)
            sent = await execute_google_request(request, resource=gmail_service)
            if not sent or not sent.get("id"):
                raise AmbiguousActionOutcome("gmail_unconfirmed")
            return {
                "external_id": sent["id"],
                "original_message_id": payload["original_message_id"],
                "provider": "gmail",
            }

        if action_type == "create_event":
            if calendar_service is None:
                raise ConfirmedActionFailure("calendar_unavailable_before_dispatch")
            event_id = calendar_event_id(action["command_key"])
            attendees = [{"email": email} for email in payload["attendee_emails"]]
            body = {
                "id": event_id,
                "summary": payload["title"],
                "location": payload["location"],
                "description": payload["description"],
                "start": {"dateTime": payload["start_time_iso"], "timeZone": payload["event_timezone"]},
                "end": {"dateTime": payload["end_time_iso"], "timeZone": payload["event_timezone"]},
                "attendees": attendees,
                "reminders": {"useDefault": True},
                "extendedProperties": {"private": {"oneboxCommandKey": action["command_key"]}},
                "conferenceData": {
                    "createRequest": {
                        "requestId": event_id,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
            }
            request = calendar_service.events().insert(
                calendarId="primary",
                body=body,
                conferenceDataVersion=1,
                sendUpdates="all" if attendees else "none",
            )
            event = await execute_google_request(request, resource=calendar_service)
            if not event or not event.get("id"):
                raise AmbiguousActionOutcome("calendar_unconfirmed")
            return {"external_id": event["id"], "provider": "calendar"}

        if action_type == "create_task":
            if tasks_service is None:
                raise ConfirmedActionFailure("tasks_unavailable_before_dispatch")
            return await run_google_operation(
                _create_task_sync,
                tasks_service,
                payload,
                action["command_key"],
                resource=tasks_service,
                passthrough=(ConfirmedActionFailure, AmbiguousActionOutcome),
            )
    except ConfirmedActionFailure:
        raise
    except GoogleOperationRejected as exc:
        # A deterministic Calendar ID returning conflict means the event may
        # already exist.  Reconcile it instead of declaring the command failed.
        if action_type == "create_event" and _is_provider_rejection(exc, 409):
            raise AmbiguousActionOutcome("calendar_id_conflict") from exc
        raise ConfirmedActionFailure(f"provider_rejected_{exc.status_code or 'unknown'}") from exc
    except GoogleProviderError as exc:
        raise AmbiguousActionOutcome("provider_outcome_unknown") from exc

    raise ConfirmedActionFailure("unsupported_action")


def _create_task_sync(tasks_service: Any, payload: Mapping[str, Any], command_key: str) -> dict[str, Any]:
    """Create a marked task in one bounded worker operation.

    Task list creation is itself a write; if the bounded call times out the
    caller cannot know which of list creation/task insertion reached Google and
    must reconcile instead of retrying.
    """
    tasklists = _list_all_tasklists_sync(tasks_service)
    matching_lists = [item for item in tasklists if item.get("title") == "Executive Agent Tasks"]
    if len(matching_lists) > 1:
        # No write has been issued, and choosing an arbitrary same-title list
        # could place an approved task in the wrong destination.
        raise ConfirmedActionFailure("task_list_ambiguous")
    selected = matching_lists[0] if matching_lists else None
    created_task_list = selected is None
    if created_task_list:
        selected = tasks_service.tasklists().insert(body={"title": "Executive Agent Tasks"}).execute()
    task_list_id = selected.get("id") if selected else None
    if not task_list_id:
        if created_task_list:
            raise AmbiguousActionOutcome("task_list_unconfirmed")
        raise ConfirmedActionFailure("task_list_not_confirmed")
    notes = f"{payload['notes']}\n\n{task_marker(command_key)}"
    task = tasks_service.tasks().insert(
        tasklist=task_list_id,
        body={"title": payload["title"], "notes": notes},
    ).execute()
    if not task or not task.get("id"):
        raise AmbiguousActionOutcome("task_unconfirmed")
    return {"external_id": task["id"], "task_list_id": task_list_id, "provider": "tasks"}


async def reconcile_action(
    action: Mapping[str, Any],
    *,
    gmail_service: Any = None,
    calendar_service: Any = None,
    tasks_service: Any = None,
) -> ReconciliationResult:
    """Look up an ambiguous command without issuing another provider write."""
    action_type = str(action["action_type"])
    try:
        payload = canonicalize_action_payload(action_type, action["payload"])
    except (TypeError, ValueError):
        return ReconciliationResult(
            "reconciliation_required", None, "payload_invalid", {"lookup": "payload_invalid"}
        )

    try:
        if action_type in {"send_email", "send_reply"}:
            if gmail_service is None:
                return ReconciliationResult(
                    "reconciliation_required", None, "gmail_lookup_unavailable", {"lookup": "not_started"}
                )
            return await _reconcile_gmail(action, payload, gmail_service)
        if action_type == "create_event":
            if calendar_service is None:
                return ReconciliationResult(
                    "reconciliation_required", None, "calendar_lookup_unavailable", {"lookup": "not_started"}
                )
            return await _reconcile_calendar(action, calendar_service)
        if action_type == "create_task":
            if tasks_service is None:
                return ReconciliationResult(
                    "reconciliation_required", None, "tasks_lookup_unavailable", {"lookup": "not_started"}
                )
            return await _reconcile_task(action, tasks_service)
    except GoogleOperationRejected as exc:
        # Gmail searches and Calendar gets have a provider-confirmed 404.  A
        # task scan is intentionally handled below as conservative/ambiguous.
        if action_type != "create_task" and _is_provider_rejection(exc, 404):
            return ReconciliationResult("failed", None, "provider_not_found", {"lookup": "not_found"})
        return ReconciliationResult(
            "reconciliation_required", None, "reconciliation_lookup_rejected", {"status": exc.status_code}
        )
    except GoogleProviderError:
        return ReconciliationResult(
            "reconciliation_required", None, "reconciliation_lookup_unavailable", {"lookup": "unavailable"}
        )

    return ReconciliationResult("reconciliation_required", None, "unsupported_action", {"lookup": "invalid"})


async def _reconcile_gmail(
    action: Mapping[str, Any], payload: Mapping[str, Any], gmail_service: Any
) -> ReconciliationResult:
    message_id = gmail_message_id(action["command_key"])
    query = f'rfc822msgid:"{message_id}"'
    matches: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"userId": "me", "q": query, "maxResults": 10}
        if page_token:
            kwargs["pageToken"] = page_token
        response = await execute_google_read_request(
            gmail_service.users().messages().list(**kwargs),
            resource=gmail_service,
        )
        matches.extend((response or {}).get("messages", []))
        if len(matches) > 1:
            return ReconciliationResult(
                "reconciliation_required", None, "gmail_marker_conflict", {"match_count": len(matches)}
            )
        page_token = (response or {}).get("nextPageToken")
        if not page_token:
            break
    if not matches:
        return ReconciliationResult("failed", None, "provider_not_found", {"lookup": "not_found"})
    # A second result would have returned above; only exact uniqueness can be
    # accepted as proof of external command acceptance.
    message = await execute_google_read_request(
        gmail_service.users().messages().get(userId="me", id=matches[0]["id"], format="metadata"),
        resource=gmail_service,
    )
    headers = message.get("payload", {}).get("headers", [])
    marker = get_header_value(headers, "X-OneBox-Command-Key")
    actual_message_id = "".join((get_header_value(headers, "Message-ID") or "").split())
    recipients = {address.casefold() for _name, address in getaddresses([get_header_value(headers, "To") or ""]) if address}
    valid = (
        marker == action["command_key"]
        and actual_message_id == message_id
        and payload["recipient_email"] in recipients
    )
    if action["action_type"] == "send_reply":
        valid = valid and message.get("threadId") == payload["thread_id"]
    if not valid:
        return ReconciliationResult(
            "reconciliation_required", None, "gmail_marker_mismatch", {"lookup": "marker_mismatch"}
        )
    return ReconciliationResult(
        "succeeded", {"external_id": message["id"], "provider": "gmail"}, None, {"lookup": "exact_marker"}
    )


async def _reconcile_calendar(action: Mapping[str, Any], calendar_service: Any) -> ReconciliationResult:
    event_id = calendar_event_id(action["command_key"])
    event = await execute_google_read_request(
        calendar_service.events().get(calendarId="primary", eventId=event_id), resource=calendar_service
    )
    private = event.get("extendedProperties", {}).get("private", {}) if event else {}
    if event and event.get("id") == event_id and private.get("oneboxCommandKey") == action["command_key"]:
        return ReconciliationResult(
            "succeeded", {"external_id": event_id, "provider": "calendar"}, None, {"lookup": "exact_marker"}
        )
    return ReconciliationResult(
        "reconciliation_required", None, "calendar_marker_mismatch", {"lookup": "marker_mismatch"}
    )


async def _reconcile_task(action: Mapping[str, Any], tasks_service: Any) -> ReconciliationResult:
    marker = task_marker(action["command_key"])
    matches = await run_google_operation(_scan_task_marker_sync, tasks_service, marker, resource=tasks_service)
    # Google Tasks has no server-side metadata/indexed idempotency facility. A
    # zero result can be delayed visibility, and a plural result is unsafe.
    if len(matches) != 1:
        return ReconciliationResult(
            "reconciliation_required",
            None,
            "task_marker_not_unique" if matches else "task_marker_not_found",
            {"match_count": len(matches)},
        )
    task, task_list_id = matches[0]
    return ReconciliationResult(
        "succeeded",
        {"external_id": task["id"], "task_list_id": task_list_id, "provider": "tasks"},
        None,
        {"lookup": "exact_marker"},
    )


def _list_all_tasklists_sync(tasks_service: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        kwargs = {"pageToken": page_token} if page_token else {}
        response = tasks_service.tasklists().list(**kwargs).execute() or {}
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def _list_all_tasks_sync(tasks_service: Any, task_list_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"tasklist": task_list_id, "showCompleted": True}
        if page_token:
            kwargs["pageToken"] = page_token
        response = tasks_service.tasks().list(**kwargs).execute() or {}
        items.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def _scan_task_marker_sync(tasks_service: Any, marker: str) -> list[tuple[dict[str, Any], str]]:
    matches: list[tuple[dict[str, Any], str]] = []
    for task_list in _list_all_tasklists_sync(tasks_service):
        task_list_id = task_list.get("id")
        if not task_list_id:
            continue
        for task in _list_all_tasks_sync(tasks_service, task_list_id):
            if marker in (task.get("notes") or ""):
                matches.append((task, task_list_id))
    return matches
