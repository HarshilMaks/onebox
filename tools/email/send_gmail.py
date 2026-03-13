# gmail_send.py
import logging
import email.utils
from typing import List, Dict, Any, Optional

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from tools.email.fetch_gmails import fetch_emails

# Project imports - corrected path
from tools.utils import create_raw_message, create_raw_reply_message, get_header_value

from tools.logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

def send_new_email(
    gmail_service: Resource,
    sender_email: str,
    to: List[str],
    subject: str,
    body: str,
    cc: List[str] = None,
    bcc: List[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Sends a completely new email (not a reply).

    Args:
        gmail_service: Authenticated Gmail API service object.
        sender_email: The email address of the sender (should match authenticated user).
        to: List of recipient email addresses.
        subject: Email subject line.
        body: Plain text body of the email.
        cc: List of CC recipient email addresses (optional).
        bcc: List of BCC recipient email addresses (optional).

    Returns:
        The sent message resource dictionary from the API, or None on failure.
    """
    if not gmail_service:
        logger.error("Gmail service is not available for send_new_email.")
        return None
    # Check for essential arguments
    if not all([sender_email, to, subject is not None, body is not None]):
         logger.error("Missing required arguments for sending email (sender, to, subject, body).")
         return None

    logger.info(f"Preparing to send new email from {sender_email} to: {to} with subject: '{subject}'")
    try:
        # Use helper to create message body
        message_body = create_raw_message(sender_email, to, subject, body, cc, bcc)
        # Send the message
        sent_message = gmail_service.users().messages().send(
            userId='me',
            body=message_body
        ).execute()
        logger.info(f"Email sent successfully. Message ID: {sent_message.get('id')}")
        return sent_message
    except HttpError as error:
        logger.error(f"Failed to send email: {error}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred while sending email: {e}")
        return None

def send_reply_email(
    gmail_service: Resource,
    user_email: str,
    original_email_id: str,
    reply_body: str,
    reply_to_all: bool = True,
    additional_recipients: List[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Sends a reply to a specific email.

    Args:
        gmail_service: Authenticated Gmail API service object.
        user_email: The email address of the authenticated user (sender of the reply).
        original_email_id: The ID of the email message being replied to.
        reply_body: The plain text body of the reply.
        reply_to_all: If True, includes original To and CC recipients. If False, replies only to the sender/Reply-To.
        additional_recipients: List of extra email addresses to include in the reply (optional).

    Returns:
        The sent message resource dictionary from the API, or None on failure.
    """
    if not gmail_service:
        logger.error("Gmail service is not available for send_reply_email.")
        return None
    # Check for essential arguments
    if not all([user_email, original_email_id, reply_body is not None]):
         logger.error("Missing required arguments for sending reply (user_email, original_email_id, reply_body).")
         return None

    logger.info(f"Preparing reply from {user_email} to email ID: {original_email_id}")
    try:
        # 1. Fetch the original message metadata to get headers and thread ID
        original_msg = gmail_service.users().messages().get(
            userId='me', id=original_email_id, format='metadata'
        ).execute()

        headers = original_msg.get('payload', {}).get('headers', [])
        thread_id = original_msg.get('threadId')

        # Extract necessary headers using helper function
        original_subject = get_header_value(headers, 'Subject') or ""
        original_sender_raw = get_header_value(headers, 'From')
        original_to_raw = get_header_value(headers, 'To')
        original_cc_raw = get_header_value(headers, 'Cc')
        original_message_id_header = get_header_value(headers, 'Message-ID')
        original_references_header = get_header_value(headers, 'References')
        reply_to_header = get_header_value(headers, 'Reply-To') # Check for specific Reply-To address

        # Ensure we have the necessary headers to construct a valid reply
        if not original_message_id_header or not thread_id:
            logger.error(f"Could not find Message-ID or Thread ID in original email {original_email_id}. Cannot construct reply.")
            return None

        # 2. Determine recipients for the reply
        # Start with any explicitly added recipients
        recipients = set(addr.strip() for addr in additional_recipients if addr.strip()) if additional_recipients else set()
        # Parse the original sender's email address
        sender_email = email.utils.parseaddr(original_sender_raw)[1] if original_sender_raw else None

        # Determine the primary reply target: use Reply-To header if present, otherwise use From header
        reply_target = email.utils.parseaddr(reply_to_header)[1] if reply_to_header else sender_email
        if reply_target:
            recipients.add(reply_target) # Add the primary target

        # If reply_to_all is True, add original To and Cc recipients
        if reply_to_all:
            if original_to_raw:
                # Use getaddresses to handle multiple recipients and names correctly
                recipients.update(addr[1] for addr in email.utils.getaddresses([original_to_raw]) if addr[1])
            if original_cc_raw:
                recipients.update(addr[1] for addr in email.utils.getaddresses([original_cc_raw]) if addr[1])

        # Remove the sender's own email address from the final recipient list (case-insensitive check)
        user_email_lower = user_email.lower()
        recipients = {r for r in recipients if user_email_lower not in r.lower()}

        # Check if any recipients remain after filtering
        if not recipients:
            logger.warning(f"Could not determine any recipients for the reply to {original_email_id} (excluding self). Attempting fallback to primary reply target.")
            # Fallback: If primary target exists and isn't the user, use only that
            if reply_target and user_email_lower not in reply_target.lower():
                recipients = {reply_target}
            else:
                 logger.error("No valid recipients found for reply, even after fallback.")
                 return None # Cannot send reply with no recipients

        final_recipients = list(recipients)
        logger.info(f"Replying to: {final_recipients}")

        # 3. Create the raw reply message using helper function
        reply_message_body = create_raw_reply_message(
            sender=user_email, # Reply comes from the authenticated user
            to=final_recipients,
            subject=original_subject, # Subject usually gets "Re:" prepended in helper
            message_text=reply_body,
            thread_id=thread_id, # Important for threading
            original_message_id=original_message_id_header, # For In-Reply-To
            original_references=original_references_header # For References header
        )

        # 4. Send the reply message
        sent_message = gmail_service.users().messages().send(
            userId='me',
            body=reply_message_body
        ).execute()
        logger.info(f"Reply sent successfully for thread {thread_id}. Message ID: {sent_message.get('id')}")
        return sent_message

    except HttpError as error:
        logger.error(f"Failed to send reply for email ID {original_email_id}: {error}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred while sending reply: {e}")
        return None


def create_gmail_draft(
    gmail_service: Resource,
    sender_email: str,
    to: List[str],
    subject: str,
    body: str,
    cc: List[str] = None,
    bcc: List[str] = None,
    reply_to_message_id: Optional[str] = None # If set, creates a reply draft
) -> Optional[Dict[str, Any]]:
    """
    Creates a draft email in Gmail. Can be a new draft or a reply draft.

    Args:
        gmail_service: Authenticated Gmail API service object.
        sender_email: The email address of the sender.
        to: List of recipient email addresses (required by API, even if overridden by reply).
        subject: Email subject line.
        body: Plain text body of the email.
        cc: List of CC recipient email addresses (optional).
        bcc: List of BCC recipient email addresses (optional).
        reply_to_message_id: If provided, creates a draft reply to this message ID.

    Returns:
        The created draft resource dictionary from the API, or None on failure.
    """
    if not gmail_service:
        logger.error("Gmail service is not available for create_draft.")
        return None

    logger.info(f"Preparing to create draft from {sender_email}. Reply to: {reply_to_message_id or 'New Email'}")
    try:
        message_data: Dict[str, str]
        thread_id_for_draft: Optional[str] = None

        if reply_to_message_id:
            # 1. Fetch original message metadata for reply context
            original_msg = gmail_service.users().messages().get(
                userId='me', id=reply_to_message_id, format='metadata'
            ).execute()
            headers = original_msg.get('payload', {}).get('headers', [])
            thread_id_for_draft = original_msg.get('threadId') # Store thread ID for draft body
            original_subject = get_header_value(headers, 'Subject') or subject # Use original subject
            original_message_id_header = get_header_value(headers, 'Message-ID')
            original_references_header = get_header_value(headers, 'References')

            if not original_message_id_header or not thread_id_for_draft:
                 logger.error(f"Cannot create reply draft: Missing Message-ID or Thread ID for {reply_to_message_id}")
                 return None

            # 2. Use helper to create reply message structure (without sending threadId yet)
            # Note: 'to' list here is mainly a placeholder for create_raw_reply_message structure,
            # the actual recipients will be set when the draft is edited/sent.
            message_data = create_raw_reply_message(
                sender=sender_email, to=to, subject=original_subject, message_text=body,
                thread_id=thread_id_for_draft, # Pass thread_id to helper for context if needed
                original_message_id=original_message_id_header,
                original_references=original_references_header
            )
            # Remove threadId from top level, it goes inside draft_body['message'] later
            if 'threadId' in message_data: del message_data['threadId']

        else:
            # Create a new draft message structure using helper
             message_data = create_raw_message(sender_email, to, subject, body, cc, bcc)

        # 3. Prepare the body for the drafts.create API call
        # API expects the raw message data within a 'message' key
        draft_body = {'message': message_data}
        # For reply drafts, explicitly set the threadId within the message object
        if reply_to_message_id and thread_id_for_draft:
             draft_body['message']['threadId'] = thread_id_for_draft

        # 4. Create the draft
        created_draft = gmail_service.users().drafts().create(
            userId='me',
            body=draft_body
        ).execute()
        logger.info(f"Draft created successfully. Draft ID: {created_draft.get('id')}")
        return created_draft

    except HttpError as error:
        logger.error(f"Failed to create draft: {error}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred while creating draft: {e}")
        return None


def reply_to_latest_email(
    gmail_service,
    target_sender_email: str,
    user_email: str,
    reply_body: str,
    subject_filter: Optional[str] = None,
    reply_to_all: bool = True,
    additional_recipients: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Finds the latest email from the specified target sender that matches an optional subject filter 
    and sends a reply to it.
    
    Args:
        gmail_service: Authenticated Gmail API service object.
        target_sender_email: The email address of the sender whose latest email should be replied to.
        user_email: The authenticated user's email address (the sender of the reply).
        reply_body: The plain text body of the reply message.
        subject_filter: Optional subject keyword or phrase that must be present in the email's subject.
        reply_to_all: If True, the reply will include the original To and Cc recipients.
        additional_recipients: Optional list of extra email addresses to include in the reply.

    Returns:
        The sent message resource dictionary from the Gmail API, or None if the operation fails.
    """
    try:
        # Build query to find emails from the target sender.
        query = f"from:{target_sender_email}"
        
        # If a subject filter is provided, add it to the query.
        if subject_filter:
            query += f" subject:({subject_filter})"
        
        logger.info(f"Searching for latest email from {target_sender_email} with query: {query}")
        
        # Fetch the latest email from the given sender and subject filter (fetch_body is False for a lighter payload)
        emails = list(fetch_emails(gmail_service, query=query, max_results=1, fetch_body=False))
        if not emails:
            logger.error(f"No emails found from {target_sender_email} with subject matching '{subject_filter}'. Aborting reply.")
            return None

        # Use the ID of the latest email for sending the reply
        original_email_id = emails[0].id
        logger.info(f"Found latest email to reply: ID {original_email_id} (Subject: {emails[0].subject})")
        
        # Call the existing reply function to send the reply message
        sent_reply = send_reply_email(
            gmail_service=gmail_service,
            user_email=user_email,
            original_email_id=original_email_id,
            reply_body=reply_body,
            reply_to_all=reply_to_all,
            additional_recipients=additional_recipients
        )
        
        if sent_reply:
            logger.info(f"Reply sent successfully for email ID {original_email_id}.")
        else:
            logger.error(f"Failed to send reply for email ID {original_email_id}.")
        return sent_reply

    except Exception as e:
        logger.exception(f"An unexpected error occurred while processing the reply: {e}")
        return None

