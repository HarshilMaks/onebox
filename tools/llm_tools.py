import asyncio
import logging
from datetime import datetime, time as datetime_time, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from googleapiclient.discovery import Resource
from server.integrations.google import (
    GoogleProviderError,
    execute_google_idempotent_request,
    execute_google_read_request,
    execute_google_request,
)
from server.services.pending_actions import (
    ACTION_CREATE_EVENT,
    ACTION_CREATE_TASK,
    ACTION_SEND_EMAIL,
    ACTION_SEND_REPLY,
    create_pending_action,
    resolve_reply_target,
)
from tools.utils import create_raw_message, format_datetime_with_timezone

logger = logging.getLogger(__name__)


def _action_request_response(action: Dict[str, object]) -> Dict[str, object]:
    """Format a server-owned action record for an agent tool response."""
    status = "pending_approval" if action["status"] == "pending" else action["status"]
    return {
        "status": status,
        "action_id": str(action["id"]),
        "action_type": action["action_type"],
        "summary": action["summary"],
        "expires_at": action["expires_at"].isoformat(),
    }


async def create_event(
    user_id: str,
    title: str,
    start_time_iso: str,
    end_time_iso: str,
    event_timezone: str,
    description: str = "",
    location: str = "",
    attendee_emails: Optional[List[str]] = None,
    *,
    command_key: str,
) -> Dict[str, object]:
    """Create a server-owned approval request; this tool never creates an event."""
    payload = {
        "title": title,
        "start_time_iso": start_time_iso,
        "end_time_iso": end_time_iso,
        "event_timezone": event_timezone,
        "description": description,
        "location": location,
        "attendee_emails": sorted(attendee_emails or []),
    }
    action = await create_pending_action(user_id, ACTION_CREATE_EVENT, payload, command_key=command_key)
    return _action_request_response(action)


async def create_task(
    user_id: str,
    title: str,
    notes: str,
    *,
    command_key: str,
) -> Dict[str, object]:
    """Create a server-owned approval request; this tool never creates a task."""
    action = await create_pending_action(
        user_id,
        ACTION_CREATE_TASK,
        {"title": title, "notes": notes},
        command_key=command_key,
    )
    return _action_request_response(action)


async def send_email(
    user_id: str,
    current_user_email: str,
    recipient_email: str,
    subject: str,
    email_body: str,
    *,
    command_key: str,
) -> Dict[str, object]:
    """Create a server-owned approval request; this tool never sends email."""
    action = await create_pending_action(
        user_id,
        ACTION_SEND_EMAIL,
        {
            "sender_email": current_user_email,
            "recipient_email": recipient_email,
            "subject": subject,
            "email_body": email_body,
        },
        command_key=command_key,
    )
    return _action_request_response(action)


async def send_reply_to_user(
    gmail_service: Resource,
    user_id: str,
    current_user_email: str,
    recipient_email: str,
    subject_filter: str,
    reply_message: str,
    *,
    command_key: str,
) -> Dict[str, object]:
    """Resolve a reply target now, then create a server-owned approval request."""
    if not gmail_service:
        raise ValueError("Gmail service is required to resolve the reply target.")

    frozen_target = await resolve_reply_target(
        gmail_service,
        recipient_email,
        subject_filter,
    )
    action = await create_pending_action(
        user_id,
        ACTION_SEND_REPLY,
        {
            "sender_email": current_user_email,
            **frozen_target,
            "reply_message": reply_message,
        },
        command_key=command_key,
    )
    return _action_request_response(action)


async def create_draft(
    gmail_service: Resource,
    current_user_email: str,
    recipient_email: str,
    subject: str,
    email_body: str,
) -> bool:
    """Create a reviewable Gmail draft through the bounded Google adapter."""
    if not gmail_service or not current_user_email:
        return False
    message = create_raw_message(
        current_user_email,
        [recipient_email],
        subject,
        email_body,
    )
    request = gmail_service.users().drafts().create(
        userId="me",
        body={"message": message},
    )
    try:
        draft_result = await execute_google_request(request, resource=gmail_service)
    except GoogleProviderError:
        logger.warning("Gmail draft creation failed", exc_info=True)
        return False
    return bool(draft_result and draft_result.get("id"))


async def mark_as_read(gmail_service: Resource, message_id: str) -> bool:
    if not gmail_service:
        return False
    request = gmail_service.users().messages().modify(
        userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
    )
    try:
        await execute_google_idempotent_request(request, resource=gmail_service)
        return True
    except GoogleProviderError:
        logger.warning("Unable to mark %s as read", message_id, exc_info=True)
        return False


async def mark_as_unread(gmail_service: Resource, message_id: str) -> bool:
    if not gmail_service:
        return False
    request = gmail_service.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": ["UNREAD"]}
    )
    try:
        await execute_google_idempotent_request(request, resource=gmail_service)
        return True
    except GoogleProviderError:
        logger.warning("Unable to mark %s as unread", message_id, exc_info=True)
        return False


async def get_calendar_events(
    calendar_service: Resource,
    date_strs: List[str],
    target_timezone: str = "UTC",
) -> Dict[str, str]:
    if not calendar_service:
        return {date_str: "Calendar service unavailable" for date_str in date_strs}

    try:
        tz = ZoneInfo(target_timezone)
    except ZoneInfoNotFoundError:
        return {date_str: "Invalid timezone" for date_str in date_strs}

    results: Dict[str, str] = {}
    for date_str in date_strs:
        try:
            day = datetime.strptime(date_str, "%d-%m-%Y").date()
        except ValueError:
            results[date_str] = "Invalid date format. Use 'dd-mm-yyyy'."
            continue

        start_local = datetime.combine(day, datetime_time.min, tzinfo=tz)
        end_local = datetime.combine(day, datetime_time.max, tzinfo=tz)
        request = calendar_service.events().list(
            calendarId="primary",
            timeMin=start_local.astimezone(timezone.utc).isoformat(),
            timeMax=end_local.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        try:
            events_result = await execute_google_read_request(request, resource=calendar_service)
        except GoogleProviderError:
            logger.warning("Calendar lookup failed for %s", date_str, exc_info=True)
            results[date_str] = "Calendar service is temporarily unavailable."
            continue

        events = events_result.get("items", [])
        if not events:
            results[date_str] = "No events found for this day."
            continue

        lines = [f"Events for {date_str}:"]
        for event in events:
            summary = event.get("summary", "No Title")
            start_data = event.get("start", {})
            end_data = event.get("end", {})
            if "dateTime" in start_data:
                start = format_datetime_with_timezone(start_data["dateTime"], target_timezone)
                end = format_datetime_with_timezone(end_data["dateTime"], target_timezone)
                lines.append(f"- {summary} (from {start} to {end})")
            elif "date" in start_data:
                lines.append(f"- {summary} (All day on {start_data['date']})")
            else:
                lines.append(f"- {summary} (Time information unavailable)")
        results[date_str] = "\n".join(lines)
    return results
