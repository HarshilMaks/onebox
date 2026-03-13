import logging
from typing import List, Optional, Callable, Dict
from tools.calender.calendar_tool import format_datetime_with_timezone
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from functools import partial

from tools.email.send_gmail import send_new_email, reply_to_latest_email, create_gmail_draft
from tools.tasks.tasks_tool import get_or_create_task_list, insert_task
from datetime import datetime, time # Keep datetime and time for type hints or other uses if needed
import time # <--- ADDED: Import the 'time' module for time.time()
import pytz
from tools.logging_config import setup_logging
from tools.utils import format_datetime_with_timezone # Assuming this exists and is configured


setup_logging()
logger = logging.getLogger(__name__)

timezone = pytz.timezone('Asia/Kolkata') # Or your desired default timezone

def create_event(
    calendar_service: Resource,
    title: str,
    start_time_iso: str,
    end_time_iso: str,
    event_timezone: str,
    description: str = "",
    location: str = "",
    attendee_emails: Optional[List[str]]=None
) -> bool:
    logger.info(f"Tool: Creating event '{title}' from {start_time_iso} to {end_time_iso}")
    if not calendar_service:
        logger.error("Calendar service not provided for create_event.")
        return False

    if attendee_emails is None:
        attendee_emails = []

    # Use time.time() from the 'time' module to get a unique timestamp
    # The original error "type object 'datetime.time' has no attribute 'time'" occurred because
    # 'time' in the original import `from datetime import datetime, time` referred to the
    # `datetime.time` class, not the `time` module.
    unique_request_id = f"{title}-{start_time_iso}-{int(time.time())}" 

    event_body = {
        'summary': title,
        'location': location,
        'description': description,
        'start': {'dateTime': start_time_iso, 'timeZone': event_timezone},
        'end': {'dateTime': end_time_iso, 'timeZone': event_timezone},
        'attendees': [{'email': email} for email in attendee_emails],
        'reminders': {'useDefault': True},
        'conferenceData': {
            'createRequest': {
                'requestId': unique_request_id, # <--- FIXED: Used the correctly imported 'time' module
                'conferenceSolutionKey': { 'type': 'hangoutsMeet' }
            }
        }
    }
    try:
        created_event = calendar_service.events().insert(
            calendarId='primary',
            body=event_body,
            conferenceDataVersion=1,
            sendNotifications=True
        ).execute()
        if created_event and 'id' in created_event:
            logger.info(f"Event '{title}' created successfully. ID: {created_event.get('id')}")
            return True
        logger.error(f"Event creation for '{title}' failed or returned unexpected result: {created_event}")
        return False
    except HttpError as error:
        logger.error(f"API HTTP error creating event '{title}': {error}")
        return False
    except Exception as e:
        logger.exception(f"Unexpected error creating event '{title}': {e}")
        return False

def create_task(tasks_service: Resource, title: str, notes: str):
    logger.info(f"Tool: Creating task '{title}'")
    if not tasks_service:
        logger.error("Tasks service not provided for create_task.")
        return None
    try:
        task_list_title = "Executive Agent Tasks"
        tasklist_id = get_or_create_task_list(tasks_service, task_list_title) # Assuming get_or_create_task_list is async or runs in executor
        if not tasklist_id:
             logger.error(f"Could not get/create task list '{task_list_title}'.")
             return None
        new_task_body = {"title": title, "notes": notes}
        inserted_task = insert_task(tasks_service, tasklist_id, new_task_body) # Assuming insert_task is async or runs in executor
        if inserted_task and 'id' in inserted_task:
             logger.info(f"Task '{title}' created. ID: {inserted_task.get('id')}")
             return inserted_task
        logger.warning(f"Task insertion for '{title}' failed or no ID returned. Result: {inserted_task}")
        return None
    except HttpError as error:
        logger.error(f"API HTTP error creating task '{title}': {error}")
        return None
    except Exception as e:
        logger.exception(f"Error creating task '{title}': {e}")
        return None

def send_email(
    gmail_service: Resource,
    current_user_email: str,
    recipient_email: str,
    subject: str,
    email_body: str
):
    logger.info(f"Tool: Sending email from {current_user_email} to {recipient_email}, Subject: '{subject}'")
    if not gmail_service:
        logger.error("Gmail service not provided for send_email.")
        return None
    if not current_user_email:
        logger.error("Current user email not provided for send_email.")
        return None
    try:
        sent_message = send_new_email(
            gmail_service=gmail_service, sender_email=current_user_email,
            to=[recipient_email], subject=subject, body=email_body
        )
        if sent_message and 'id' in sent_message:
             logger.info(f"Email sent. ID: {sent_message.get('id')}")
             return sent_message
        logger.warning(f"Email sending failed or no ID returned. Result: {sent_message}")
        return None
    except HttpError as error:
        logger.error(f"API HTTP error sending email: {error}")
        return None
    except Exception as e:
        logger.exception(f"Error sending email: {e}")
        return None

def send_reply_to_user(
    gmail_service: Resource,
    current_user_email: str,
    recipient_email: str, # This is the original sender to reply to
    subject_filter: str,
    reply_message: str
):
    logger.info(f"Tool: Replying from {current_user_email} to {recipient_email} with subject filter '{subject_filter}'")
    if not gmail_service:
        logger.error("Gmail service not provided for send_reply_to_user.")
        return None
    if not current_user_email:
        logger.error("Current user email not provided for send_reply_to_user.")
        return None
    try:
        sent_reply = reply_to_latest_email(
            gmail_service=gmail_service, target_sender_email=recipient_email,
            user_email=current_user_email, reply_body=reply_message,
            subject_filter=subject_filter, reply_to_all=False
        )
        if sent_reply and 'id' in sent_reply:
             logger.info(f"Reply sent. ID: {sent_reply.get('id')}")
             return sent_reply
        logger.warning(f"Reply sending failed or no ID returned. Result: {sent_reply}")
        return None
    except HttpError as error:
        logger.error(f"API HTTP error sending reply: {error}")
        return None
    except Exception as e:
        logger.exception(f"Error sending reply: {e}")
        return None

def create_draft(
    gmail_service: Resource,
    current_user_email: str,
    recipient_email: str,
    subject: str,
    email_body: str
):
    logger.info(f"Tool: Creating draft from {current_user_email} to {recipient_email}, Subject: '{subject}'")
    if not gmail_service:
        logger.error("Gmail service not provided for create_draft.")
        return False
    if not current_user_email:
        logger.error("Current user email not provided for create_draft.")
        return False
    try:
        draft_result = create_gmail_draft(
            gmail_service=gmail_service, sender_email=current_user_email,
            to=[recipient_email], subject=subject, body=email_body
        )
        if draft_result and 'id' in draft_result:
             logger.info(f"Draft created. ID: {draft_result['id']}")
             return True # Consistent with create_event returning bool for success
        logger.warning(f"Draft creation failed or no ID returned. Result: {draft_result}")
        return False
    except HttpError as error:
        logger.error(f"API HTTP error creating draft: {error}")
        return False
    except Exception as e:
        logger.exception(f"Error creating draft: {e}")
        return False

def mark_as_read(gmail_service: Resource, message_id: str) -> bool:
    logger.info(f"Tool: Marking message {message_id} as read.")
    if not gmail_service:
        logger.error("Gmail service not provided for mark_as_read.")
        return False
    try:
        gmail_service.users().messages().modify(
            userId='me', id=message_id, body={'removeLabelIds': ['UNREAD']}
        ).execute()
        logger.info(f"Message {message_id} marked as read.")
        return True
    except HttpError as error:
        logger.error(f"API HTTP error marking {message_id} as read: {error}")
        return False
    except Exception as e:
        logger.exception(f"Error marking {message_id} as read: {e}")
        return False

def mark_as_unread(gmail_service: Resource, message_id: str) -> bool:
    logger.info(f"Tool: Marking message {message_id} as unread.")
    if not gmail_service:
        logger.error("Gmail service not provided for mark_as_unread.")
        return False
    try:
        gmail_service.users().messages().modify(
            userId='me', id=message_id, body={'addLabelIds': ['UNREAD']}
        ).execute()
        logger.info(f"Message {message_id} marked as unread.")
        return True
    except HttpError as error:
        logger.error(f"API HTTP error marking {message_id} as unread: {error}")
        return False
    except Exception as e:
        logger.exception(f"Error marking {message_id} as unread: {e}")
        return False
    
    
def get_calendar_events(
    calendar_service: Resource,
    date_strs: List[str],
    target_timezone: str = "Asia/Kolkata"
) -> Dict[str, str]:
    """
    Retrieves calendar events for a list of specified dates.

    Args:
        calendar_service: Authenticated Google Calendar API service object.
        date_strs: List of dates to check, each in "dd-mm-yyyy" format.
        target_timezone: A pytz-compatible timezone string (e.g., "Asia/Kolkata").

    Returns:
        A dictionary where keys are the input date strings and values are
        strings describing the events for that day (or error messages).
    """
    if not calendar_service:
        logger.error("Calendar service is not available.")
        return {date_str: "Error: Calendar service unavailable" for date_str in date_strs}

    results = {}
    logger.info(f"Fetching calendar events for {date_strs} in timezone {target_timezone}")

    try:
        tz = pytz.timezone(target_timezone)
    except Exception as e:
        logger.error(f"Invalid timezone '{target_timezone}': {e}")
        return {date_str: f"Error: Invalid timezone '{target_timezone}'" for date_str in date_strs}

    for date_str in date_strs:
        try:
            day = datetime.strptime(date_str, "%d-%m-%Y").date()
            start_local = tz.localize(datetime.combine(day, time.min))
            end_local = tz.localize(datetime.combine(day, time.max))

            start_utc = start_local.astimezone(pytz.utc).isoformat()
            end_utc = end_local.astimezone(pytz.utc).isoformat()

            events_result = calendar_service.events().list(
                calendarId='primary',
                timeMin=start_utc,
                timeMax=end_utc,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            if not events:
                results[date_str] = "No events found for this day."
                logger.info(f"No events found for {date_str}")
                continue

            lines = [f"Events for {date_str}:"]
            for event in events:
                summary = event.get('summary', 'No Title')
                start_data = event.get('start', {})
                end_data = event.get('end', {})

                if 'dateTime' in start_data:
                    start_str = format_datetime_with_timezone(start_data['dateTime'], target_timezone)
                    end_str = format_datetime_with_timezone(end_data['dateTime'], target_timezone)
                    lines.append(f"- {summary} (from {start_str} to {end_str})")
                elif 'date' in start_data:
                    lines.append(f"- {summary} (All day on {start_data['date']})")
                else:
                    lines.append(f"- {summary} (Time information unavailable)")

            results[date_str] = "\n".join(lines)

        except ValueError:
            logger.error(f"Invalid date format: '{date_str}'")
            results[date_str] = "Error: Invalid date format. Use 'dd-mm-yyyy'."
        except HttpError as error:
            logger.error(f"API error for {date_str}: {error}")
            results[date_str] = f"Error: API error ({error.resp.status})."
        except Exception as e:
            logger.exception(f"Unexpected error for {date_str}: {e}")
            results[date_str] = "Error: An unexpected error occurred."

    return results