import asyncio
import logging
from datetime import datetime, time as datetime_time
from typing import Dict, List, Optional

import pytz
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from server.services.pending_actions import (
    ACTION_CREATE_EVENT,
    ACTION_CREATE_TASK,
    ACTION_SEND_EMAIL,
    ACTION_SEND_REPLY,
    create_pending_action,
    resolve_reply_target,
)
from tools.email.send_gmail import create_gmail_draft
from tools.logging_config import setup_logging
from tools.utils import format_datetime_with_timezone

setup_logging()
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
    action = await create_pending_action(user_id, ACTION_CREATE_EVENT, payload)
    return _action_request_response(action)


async def create_task(user_id: str, title: str, notes: str) -> Dict[str, object]:
    """Create a server-owned approval request; this tool never creates a task."""
    action = await create_pending_action(
        user_id,
        ACTION_CREATE_TASK,
        {"title": title, "notes": notes},
    )
    return _action_request_response(action)


async def send_email(
    user_id: str,
    current_user_email: str,
    recipient_email: str,
    subject: str,
    email_body: str,
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
    )
    return _action_request_response(action)


async def send_reply_to_user(
    gmail_service: Resource,
    user_id: str,
    current_user_email: str,
    recipient_email: str,
    subject_filter: str,
    reply_message: str,
) -> Dict[str, object]:
    """Resolve a reply target now, then create a server-owned approval request."""
    if not gmail_service:
        raise ValueError("Gmail service is required to resolve the reply target.")

    original_message_id = await resolve_reply_target(
        gmail_service,
        recipient_email,
        subject_filter,
    )
    action = await create_pending_action(
        user_id,
        ACTION_SEND_REPLY,
        {
            "sender_email": current_user_email,
            "recipient_email": recipient_email,
            "subject_filter": subject_filter,
            "original_message_id": original_message_id,
            "reply_message": reply_message,
        },
    )
    return _action_request_response(action)


def create_draft(
    gmail_service: Resource,
    current_user_email: str,
    recipient_email: str,
    subject: str,
    email_body: str,
) -> bool:
    """Create a reviewable Gmail draft; drafts do not send external communication."""
    logger.info("Creating draft from %s to %s", current_user_email, recipient_email)
    if not gmail_service or not current_user_email:
        return False
    try:
        draft_result = create_gmail_draft(
            gmail_service=gmail_service,
            sender_email=current_user_email,
            to=[recipient_email],
            subject=subject,
            body=email_body,
        )
        return bool(draft_result and draft_result.get("id"))
    except HttpError as error:
        logger.error("API HTTP error creating draft: %s", error)
        return False
    except Exception:
        logger.exception("Error creating draft")
        return False


def mark_as_read(gmail_service: Resource, message_id: str) -> bool:
    if not gmail_service:
        return False
    try:
        gmail_service.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        return True
    except HttpError as error:
        logger.error("API HTTP error marking %s as read: %s", message_id, error)
        return False
    except Exception:
        logger.exception("Error marking %s as read", message_id)
        return False


def mark_as_unread(gmail_service: Resource, message_id: str) -> bool:
    if not gmail_service:
        return False
    try:
        gmail_service.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": ["UNREAD"]}
        ).execute()
        return True
    except HttpError as error:
        logger.error("API HTTP error marking %s as unread: %s", message_id, error)
        return False
    except Exception:
        logger.exception("Error marking %s as unread", message_id)
        return False


def get_calendar_events(
    calendar_service: Resource,
    date_strs: List[str],
    target_timezone: str = "Asia/Kolkata",
) -> Dict[str, str]:
    if not calendar_service:
        return {date_str: "Error: Calendar service unavailable" for date_str in date_strs}

    try:
        tz = pytz.timezone(target_timezone)
    except Exception:
        return {date_str: f"Error: Invalid timezone '{target_timezone}'" for date_str in date_strs}

    results: Dict[str, str] = {}
    for date_str in date_strs:
        try:
            day = datetime.strptime(date_str, "%d-%m-%Y").date()
            start_local = tz.localize(datetime.combine(day, datetime_time.min))
            end_local = tz.localize(datetime.combine(day, datetime_time.max))
            events_result = calendar_service.events().list(
                calendarId="primary",
                timeMin=start_local.astimezone(pytz.utc).isoformat(),
                timeMax=end_local.astimezone(pytz.utc).isoformat(),
                singleEvents=True,
                orderBy="startTime",
            ).execute()
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
        except ValueError:
            results[date_str] = "Error: Invalid date format. Use 'dd-mm-yyyy'."
        except HttpError as error:
            results[date_str] = f"Error: API error ({error.resp.status})."
        except Exception:
            logger.exception("Unexpected calendar lookup error for %s", date_str)
            results[date_str] = "Error: An unexpected error occurred."
    return results
