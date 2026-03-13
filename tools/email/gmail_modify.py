# tools.email.gmail_modify.py
import logging
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from tools.logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

def mark_as_read(gmail_service: Resource, message_id: str) -> bool:
    """Marks a specific email message as read (removes the UNREAD label)."""
    if not gmail_service:
        logger.error("Gmail service is not available for mark_as_read.")
        return False
    logger.info(f"Marking message {message_id} as read.")
    try:
        gmail_service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'removeLabelIds': ['UNREAD']}
        ).execute()
        logger.info(f"Message {message_id} marked as read.")
        return True
    except HttpError as error:
        logger.error(f"Failed to mark message {message_id} as read: {error}")
        return False
    except Exception as e:
        logger.exception(f"An unexpected error occurred while marking as read: {e}")
        return False

def mark_as_unread(gmail_service: Resource, message_id: str) -> bool:
    """Marks a specific email message as unread (adds the UNREAD label)."""
    if not gmail_service:
        logger.error("Gmail service is not available for mark_as_unread.")
        return False
    logger.info(f"Marking message {message_id} as unread.")
    try:
        gmail_service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'addLabelIds': ['UNREAD']}
        ).execute()
        logger.info(f"Message {message_id} marked as unread.")
        return True
    except HttpError as error:
        logger.error(f"Failed to mark message {message_id} as unread: {error}")
        return False
    except Exception as e:
        logger.exception(f"An unexpected error occurred while marking as unread: {e}")
        return False

