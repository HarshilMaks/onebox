# utils.py
import base64
import logging
import email.utils
import pytz
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import List, Dict, Any, Mapping, Optional

from server.mail.mime import extract_mail_content, parse_message_date

logger = logging.getLogger(__name__)

def extract_message_body(msg_payload: Dict[str, Any]) -> str:
    """Return the bounded plain-text representation of an untrusted MIME payload."""
    return extract_mail_content(msg_payload).body


def parse_email_time(date_header: str) -> Optional[datetime]:
    """Parse a mail date with a timezone-aware UTC fallback."""
    if not date_header:
        return None
    return datetime.fromisoformat(parse_message_date(date_header))

def create_raw_message(
    sender: str,
    to: List[str],
    subject: str,
    message_text: str,
    cc: List[str] = None,
    bcc: List[str] = None,
    *,
    message_id: Optional[str] = None,
    extra_headers: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Creates a new MIME email message and returns it base64 encoded."""
    message = MIMEMultipart()
    message['to'] = ", ".join(to)
    message['from'] = sender
    message['subject'] = subject
    if cc:
        message['cc'] = ", ".join(cc)
    if bcc:
        message['bcc'] = ", ".join(bcc) # Note: BCC usually handled by API, not header
    message['Message-ID'] = message_id or email.utils.make_msgid()
    for header, value in (extra_headers or {}).items():
        message[header] = value

    msg = MIMEText(message_text, 'plain') # Default to plain text
    message.attach(msg)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {"raw": raw}

def create_raw_reply_message(
    sender: str,
    to: List[str],
    subject: str,
    message_text: str,
    thread_id: str,
    original_message_id: str,
    original_references: Optional[str],
    *,
    message_id: Optional[str] = None,
    extra_headers: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
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
    message['Message-ID'] = message_id or email.utils.make_msgid()
    for header, value in (extra_headers or {}).items():
        message[header] = value

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