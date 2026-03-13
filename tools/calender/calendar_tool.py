# calendar_ops.py
import logging
import email.utils
from datetime import datetime, time, timedelta
from typing import List, Dict
import pytz
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from tools.utils import format_datetime_with_timezone

from tools.logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)
def create_calendar_event(
    calendar_service: Resource,
    title: str,
    start_time_iso: str,
    end_time_iso: str,
    event_timezone: str,
    description: str = "",
    location: str = "",
    attendee_emails: List[str] = None,
    add_meet_link: bool = False,
    send_notifications: bool = False
) -> bool:
    """
    Creates a new Google Calendar event.

    Args:
        calendar_service: Authenticated Google Calendar API service object.
        title: The title of the event.
        start_time_iso: The event start time as an ISO 8601 string with timezone offset.
        end_time_iso: The event end time as an ISO 8601 string with timezone offset.
        event_timezone: The IANA timezone identifier (e.g., "America/Los_Angeles").
        description: (Optional) A description for the event.
        location: (Optional) The location of the event.
        attendee_emails: (Optional) A list of attendee email addresses.
        add_meet_link: Whether to automatically include a Google Meet conferencing link.
        send_notifications: Whether to send email notifications to attendees.

    Returns:
        True if the event was created successfully, False otherwise.
    """
    if not calendar_service:
        logger.error("Calendar service is not available for create_calendar_event.")
        return False
    if not all([title, start_time_iso, end_time_iso, event_timezone]):
        logger.error("Missing required arguments: title, start_time_iso, end_time_iso, or event_timezone.")
        return False

    logger.info(f"Creating event '{title}' scheduled from {start_time_iso} to {end_time_iso} in timezone {event_timezone}")

    # Build up the event details
    event_body = {
        'summary': title,
        'location': location,
        'description': description,
        'start': {
            'dateTime': start_time_iso,
            'timeZone': event_timezone,
        },
        'end': {
            'dateTime': end_time_iso,
            'timeZone': event_timezone,
        },
        'reminders': {
            'useDefault': True
        }
    }

    # Optionally add attendees if provided
    if attendee_emails:
        event_body['attendees'] = [{'email': email.strip()} for email in attendee_emails if email.strip()]

    conference_data_version = 0
    if add_meet_link:
        # Generate a unique request ID for idempotency
        request_id = f"{title.replace(' ', '_')}-{start_time_iso}-{email.utils.make_msgid()}"
        event_body['conferenceData'] = {
            'createRequest': {
                'requestId': request_id[:1024],
                'conferenceSolutionKey': {'type': 'hangoutsMeet'}
            }
        }
        conference_data_version = 1

    try:
        created_event = calendar_service.events().insert(
            calendarId='primary',
            body=event_body,
            sendNotifications=send_notifications,
            conferenceDataVersion=conference_data_version
        ).execute()
        logger.info(f"Event created successfully. Event ID: {created_event.get('id')}, Link: {created_event.get('htmlLink')}")
        return True
    except HttpError as error:
        logger.error(f"Failed to create event: {error}")
        try:
            error_details = error.resp.reason
            if error.content:
                error_details += f" - {error.content.decode()}"
            logger.error(f"API Error Details: {error_details}")
        except Exception:
            pass
        return False
    except Exception as e:
        logger.exception(f"Unexpected error when creating event: {e}")
        return False

def get_calendar_events(
    calendar_service: Resource,
    date_strs: List[str],
    target_timezone: str = "IST" 
) -> Dict[str, str]:
    """
    Retrieves calendar events for a list of specified dates.

    Args:
        calendar_service: Authenticated Google Calendar API service object.
        date_strs: List of dates to check, each in "dd-mm-yyyy" format.
        target_timezone: The timezone to display event times in (e.g., "America/Los_Angeles").

    Returns:
        A dictionary where keys are the input date strings and values are
        strings describing the events for that day (or "No events found.").
    """
    if not calendar_service:
        logger.error("Calendar service is not available for get_calendar_events.")
        return {date_str: "Error: Calendar service unavailable" for date_str in date_strs}

    results = {}
    logger.info(f"Fetching calendar events for dates: {date_strs} in timezone {target_timezone}")

    for date_str in date_strs:
        try:
            day = datetime.strptime(date_str, "%d-%m-%Y").date()
            start_of_day_dt = datetime.combine(day, time.min)
            end_of_day_dt = datetime.combine(day, time.max)
            start_of_day_utc = pytz.utc.localize(start_of_day_dt).isoformat()
            end_of_day_utc = pytz.utc.localize(end_of_day_dt).isoformat()
            events_result = calendar_service.events().list(
                calendarId='primary', # Use the primary calendar
                timeMin=start_of_day_utc,
                timeMax=end_of_day_utc,
                singleEvents=True,      # Expand recurring events
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])

            if not events:
                results[date_str] = "No events found for this day."
                logger.info(f"No events found for {date_str}.")
                continue

            # Build summary string
            day_summary_lines = [f"Events for {date_str}:"]
            for event in events:
                summary = event.get('summary', 'No Title')
                start_data = event.get('start', {})
                end_data = event.get('end', {})

                # Handle all-day events vs timed events
                if 'dateTime' in start_data: # Timed event
                    # Use helper to format time
                    start_str = format_datetime_with_timezone(start_data['dateTime'], target_timezone)
                    end_str = format_datetime_with_timezone(end_data['dateTime'], target_timezone)
                    day_summary_lines.append(f"- {summary} (from {start_str} to {end_str})")
                elif 'date' in start_data: # All-day event
                     day_summary_lines.append(f"- {summary} (All day on {start_data['date']})")
                else:
                     day_summary_lines.append(f"- {summary} (Time information unavailable)")

            results[date_str] = "\n".join(day_summary_lines)
            logger.info(f"Found {len(events)} events for {date_str}.")

        except ValueError:
            logger.error(f"Invalid date format: '{date_str}'. Please use 'dd-mm-yyyy'.")
            results[date_str] = "Error: Invalid date format provided."
        except HttpError as error:
            logger.error(f"API error fetching events for {date_str}: {error}")
            results[date_str] = f"Error: API error fetching events ({error.resp.status})."
        except Exception as e:
            logger.exception(f"Unexpected error fetching events for {date_str}: {e}")
            results[date_str] = "Error: An unexpected error occurred."

    return results


def send_calendar_invite(
    calendar_service: Resource,
    attendee_emails: List[str],
    title: str,
    start_time_iso: str, 
    end_time_iso: str,  
    event_timezone: str,
    description: str = "",
    location: str = "",
    send_notifications: bool = True,
    add_meet_link: bool = True
) -> bool:
    """
    Creates a Google Calendar event and invites attendees.

    Args:
        calendar_service: Authenticated Google Calendar API service object.
        attendee_emails: List of email addresses to invite.
        title: Title of the calendar event.
        start_time_iso: Event start time in ISO 8601 format with timezone offset.
        end_time_iso: Event end time in ISO 8601 format with timezone offset.
        event_timezone: The IANA timezone ID for the event (e.g., "America/New_York").
        description: Optional description for the event.
        location: Optional location for the event.
        send_notifications: Whether to send email invitations to attendees.
        add_meet_link: Whether to automatically add a Google Meet link.

    Returns:
        True if the event was created successfully, False otherwise.
    """
    if not calendar_service:
        logger.error("Calendar service is not available for send_calendar_invite.")
        return False
    if not all([attendee_emails, title, start_time_iso, end_time_iso, event_timezone]):
        logger.error("Missing required arguments for sending calendar invite.")
        return False

    logger.info(f"Creating calendar event '{title}' for {attendee_emails} in timezone {event_timezone}")

    event_body = {
        'summary': title,
        'location': location,
        'description': description,
        'start': {
            'dateTime': start_time_iso,
            'timeZone': event_timezone,
        },
        'end': {
            'dateTime': end_time_iso,
            'timeZone': event_timezone,
        },
        'attendees': [{'email': email.strip()} for email in attendee_emails if email.strip()],
        'reminders': { # Default reminders
            'useDefault': False,
            'overrides': [
                {'method': 'email', 'minutes': 24 * 60}, # 1 day before
                {'method': 'popup', 'minutes': 10},     # 10 mins before
            ],
        },
    }

    # Add Google Meet conferencing if requested
    conference_data_version = 0
    if add_meet_link:
        # Generate a unique request ID for idempotency
        request_id = f"{title.replace(' ', '_')}-{start_time_iso}-{email.utils.make_msgid()}"
        event_body['conferenceData'] = {
            'createRequest': {
                'requestId': request_id[:1024], # Limit request ID length
                'conferenceSolutionKey': {'type': 'hangoutsMeet'}
            }
        }
        conference_data_version = 1 # Required when adding conference data

    try:
        created_event = calendar_service.events().insert(
            calendarId='primary',
            body=event_body,
            sendNotifications=send_notifications,
            conferenceDataVersion=conference_data_version
        ).execute()
        logger.info(f"Calendar event created successfully. Event ID: {created_event.get('id')}, Link: {created_event.get('htmlLink')}")
        return True
    except HttpError as error:
        logger.error(f"Failed to create calendar event: {error}")
        # Log more details from the error if available
        try:
            error_details = error.resp.reason
            if error.content:
                error_details += f" - {error.content.decode()}"
            logger.error(f"API Error Details: {error_details}")
        except Exception:
            pass # Ignore errors during error reporting
        return False
    except Exception as e:
        logger.exception(f"An unexpected error occurred while creating calendar event: {e}")
        return False

