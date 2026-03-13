# utils.py
import logging
import base64
import email.utils
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import List, Dict, Any, Optional

import pytz
from dateutil import parser

logger = logging.getLogger(__name__)

def extract_message_body(msg_payload: Dict[str, Any]) -> str:
    """
    Recursively walks through email parts to find and decode the message body.
    Prefers plain text over HTML.
    """
    body = "No message body available."
    if not msg_payload:
        return body

    mime_type = msg_payload.get("mimeType", "")

    # Check current level body
    if mime_type == "text/plain":
        data = msg_payload.get("body", {}).get("data")
        if data:
            try:
                decoded_body = base64.urlsafe_b64decode(data).decode("utf-8")
                return decoded_body # Prioritize plain text
            except Exception as e:
                logger.warning(f"Could not decode base64 plain text body: {e}")
    elif mime_type == "text/html":
        data = msg_payload.get("body", {}).get("data")
        if data:
            try:
                # Keep HTML as fallback if plain text isn't found later
                body = base64.urlsafe_b64decode(data).decode("utf-8")
            except Exception as e:
                logger.warning(f"Could not decode base64 html body: {e}")

    # Recurse if parts exist
    if "parts" in msg_payload:
        plain_body_found = None
        html_body_found = None
        for part in msg_payload.get("parts", []):
            part_body = extract_message_body(part) # Recursive call
            part_mime_type = part.get("mimeType", "")

            if part_mime_type == "text/plain" and part_body != "No message body available.":
                plain_body_found = part_body
                break # Found plain text in parts, prioritize this
            elif part_mime_type == "text/html" and part_body != "No message body available.":
                html_body_found = part_body # Keep track of HTML as fallback

        if plain_body_found:
            return plain_body_found
        elif html_body_found:
            return html_body_found # Use HTML from parts if no plain text found

    # Return body found at the current level (HTML) or default if nothing found
    return body


def parse_email_time(date_header: str) -> Optional[datetime]:
    """Parses the 'Date' header string into a timezone-aware datetime object."""
    if not date_header:
        return None
    try:
        # Use dateutil.parser for robust parsing
        parsed_dt = parser.parse(date_header)
        # Ensure it's timezone-aware (if not, assume UTC as a fallback)
        if parsed_dt.tzinfo is None or parsed_dt.tzinfo.utcoffset(parsed_dt) is None:
             logger.debug(f"Parsed datetime '{date_header}' lacks timezone info. Assuming UTC.")
             return parsed_dt.replace(tzinfo=pytz.utc)
        return parsed_dt
    except (ValueError, TypeError, OverflowError) as e:
        logger.error(f"Error parsing time string '{date_header}': {e}")
        return None

def create_raw_message(sender: str, to: List[str], subject: str, message_text: str, cc: List[str] = None, bcc: List[str] = None) -> Dict[str, str]:
    """Creates a new MIME email message and returns it base64 encoded."""
    message = MIMEMultipart()
    message['to'] = ", ".join(to)
    message['from'] = sender
    message['subject'] = subject
    if cc:
        message['cc'] = ", ".join(cc)
    if bcc:
        message['bcc'] = ", ".join(bcc) # Note: BCC usually handled by API, not header
    message['Message-ID'] = email.utils.make_msgid()

    msg = MIMEText(message_text, 'plain') # Default to plain text
    message.attach(msg)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw}

def create_raw_reply_message(sender: str, to: List[str], subject: str, message_text: str, thread_id: str, original_message_id: str, original_references: Optional[str]) -> Dict[str, str]:
    """Creates a MIME reply email message and returns it base64 encoded."""
    message = MIMEMultipart()
    message['to'] = ", ".join(to)
    message['from'] = sender
    # Prepend "Re: " unless already present
    if not subject.lower().startswith("re:"):
        message['subject'] = f"Re: {subject}"
    else:
        message['subject'] = subject

    message['In-Reply-To'] = original_message_id
    # Append the original message ID to the references header
    references = original_references if original_references else ""
    if original_message_id not in references:
        references = f"{references} {original_message_id}".strip()
    message['References'] = references
    message['Message-ID'] = email.utils.make_msgid()

    msg = MIMEText(message_text, 'plain') # Default to plain text
    message.attach(msg)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    # Include threadId for sending replies correctly
    return {"raw": raw, "threadId": thread_id}

def get_header_value(headers: List[Dict[str, str]], name: str) -> Optional[str]:
    """Safely retrieves a header value by name (case-insensitive)."""
    if not headers: return None
    name_lower = name.lower()
    for header in headers:
        if header.get("name", "").lower() == name_lower:
            return header.get("value")
    return None

def format_datetime_with_timezone(dt_str: str, timezone: str = "Asia/Kolkata") -> str:
    """Formats an ISO datetime string into a more readable format in the specified timezone."""
    try:
        # Handle potential 'Z' for UTC
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        tz = pytz.timezone(timezone)
        dt_localized = dt.astimezone(tz)
        # Example format: 2023-10-27 03:00 PM PST
        return dt_localized.strftime("%Y-%m-%d %I:%M %p %Z")
    except Exception as e:
        logger.warning(f"Could not format datetime string '{dt_str}' with timezone '{timezone}': {e}")
        return dt_str # Return original string if formatting fails