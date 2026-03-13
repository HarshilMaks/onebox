# gmail_fetch.py
import logging
import email.utils
from datetime import datetime # Still needed by parse_email_time indirectly
from typing import Iterable, List, Optional

# Removed timedelta and pytz as they were specific to fetch_unanswered_emails
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from pydantic import ValidationError

# Project imports - corrected paths based on previous refactoring
from tools.email.gmail_models import EmailData
from tools.utils import extract_message_body, parse_email_time, get_header_value
from tools.logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

def fetch_emails(
    gmail_service: Resource,
    query: str = "is:unread",
    max_results: int = 10,
    fetch_body: bool = True
) -> Iterable[EmailData]:
    """
    Fetches emails matching a given Gmail query.

    Args:
        gmail_service: Authenticated Gmail API service object.
        query: Gmail search query (e.g., 'is:unread', 'from:example.com', 'subject:"Important Update"').
        max_results: Maximum number of emails to fetch.
        fetch_body: Whether to fetch the full email body (can be slower).

    Yields:
        EmailData: An object containing details of each fetched email.
    """
    if not gmail_service:
        logger.error("Gmail service is not available for fetch_emails.")
        return

    logger.info(f"Fetching emails with query: '{query}', max_results={max_results}")
    try:
        # Request a list of messages matching the query
        list_request = gmail_service.users().messages().list(
            userId='me',
            q=query,
            maxResults=max_results
        )
        response = list_request.execute()
        messages = response.get('messages', []) # List of {'id': '...', 'threadId': '...'}

        if not messages:
            logger.info("No messages found matching the query.")
            return

        logger.info(f"Found {len(messages)} message references. Fetching details...")
        count = 0
        # Iterate through each message reference
        for msg_ref in messages:
            msg_id = msg_ref['id']
            try:
                # Determine format needed (metadata is faster if body not required)
                msg_format = 'full' if fetch_body else 'metadata'
                # Get the full message details by ID
                msg = gmail_service.users().messages().get(
                    userId='me', id=msg_id, format=msg_format
                ).execute()

                payload = msg.get('payload', {})
                headers = payload.get('headers', [])
                thread_id = msg.get('threadId') # Get thread ID

                # Extract common headers using helper function
                subject = get_header_value(headers, 'Subject')
                from_email_raw = get_header_value(headers, 'From')
                to_email_raw = get_header_value(headers, 'To')
                date_header = get_header_value(headers, 'Date')

                # Parse 'From' and 'To' addresses
                # email.utils.parseaddr splits "Name <email>" into ('Name', 'email')
                from_email = email.utils.parseaddr(from_email_raw)[1] if from_email_raw else None
                # email.utils.getaddresses handles multiple addresses in 'To'/'Cc'
                to_emails = [addr[1] for addr in email.utils.getaddresses([to_email_raw]) if to_email_raw and addr[1]]

                # Parse the date header using helper function
                send_time = parse_email_time(date_header) if date_header else None

                # Extract body if requested, using helper function
                body = None
                if fetch_body and payload: # Ensure payload exists before extracting body
                    body = extract_message_body(payload)

                # Create EmailData object using Pydantic model for validation
                email_data = EmailData(
                    id=msg_id,
                    thread_id=thread_id,
                    from_email=from_email,
                    to_emails=to_emails,
                    subject=subject,
                    body=body,
                    # Store send time as ISO string for consistency
                    send_time_iso=send_time.isoformat() if send_time else None,
                    # user_responded field is not relevant in this simplified fetch
                    # It will default to False as defined in the model
                )
                # Yield the processed email data
                yield email_data
                count += 1

            except HttpError as error:
                # Log errors fetching individual messages but continue loop
                logger.error(f"Failed to fetch details for message ID {msg_id}: {error}")
            except ValidationError as e:
                 # Log Pydantic validation errors
                 logger.error(f"Data validation error for message ID {msg_id}: {e}")
            except Exception as e:
                # Log any other unexpected errors during message processing
                logger.exception(f"An unexpected error occurred processing message ID {msg_id}: {e}")

        logger.info(f"Successfully processed {count} emails from query.")

    except HttpError as error:
        # Log errors related to the initial message listing
        logger.error(f"An API error occurred while listing messages: {error}")
    except Exception as e:
        # Log any other unexpected errors during the fetch operation
        logger.exception(f"An unexpected error occurred during email fetch: {e}")

