import base64
import json
import logging
import os
import time
from typing import Dict, Any, Optional
from uuid import UUID # Ensure UUID is imported

from googleapiclient.discovery import build as build_google_service # Alias to avoid name clashes
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request as GoogleAuthRequest 

from server.database import AsyncSessionLocal as SessionLocal
from server.models import AgentToken
from server.services.setup_google import build_credentials # Reusable function


from agents import ExecutiveAgent
import re
from server.logging_config import setup_logging
from dotenv import load_dotenv

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)
load_dotenv()

# Global variables
GMAIL_SERVICE = None
PUBSUB_TOPIC = os.environ.get("PUBSUB_TOPIC", "projects/agents-456517/topics/gmail-notifications")
# This AGENT_USER_ID_FOR_SERVICE should be the UUID of the user whose emails this service will manage
AGENT_USER_ID_FOR_SERVICE = os.environ.get("AGENT_USER_ID_FOR_SERVICE")

LAST_PROCESSED_MESSAGE_ID = None
LAST_PROCESSED_TIME = 0
PROCESSING_COOLDOWN = 5  # Seconds

def should_process_email(email_content: Dict[str, Any]) -> bool:
    """Filter logic to skip spam, no-reply, and marketing emails."""
    from_field = email_content.get('from', '').lower()
    subject = email_content.get('subject', '').lower()
    labels = email_content.get('labels', [])

    if re.search(r'no[-_.]?reply|donotreply|noreply', from_field):
        logger.info(f"Skipping no-reply email from: {from_field}")
        return False
    spam_labels = {'SPAM', 'CATEGORY_PROMOTIONS', 'CATEGORY_FORUMS'}
    if any(label in spam_labels for label in labels):
        logger.info(f"Skipping email with labels: {labels}")
        return False
    spammy_subject_keywords = ['unsubscribe', 'newsletter', 'promotion', 'deal', 'discount']
    if any(keyword in subject for keyword in spammy_subject_keywords):
        logger.info(f"Skipping email with subject: {subject}")
        return False
    return True

async def initialize_gmail_service(): # Made async
    """Initialize the Gmail service for the designated agent user and set up watch."""
    global GMAIL_SERVICE

    if not AGENT_USER_ID_FOR_SERVICE:
        logger.error("AGENT_USER_ID_FOR_SERVICE environment variable is not set. Cannot initialize Gmail service.")
        return False

    try:
        user_uuid = UUID(AGENT_USER_ID_FOR_SERVICE)
    except ValueError:
        logger.error(f"Invalid AGENT_USER_ID_FOR_SERVICE: '{AGENT_USER_ID_FOR_SERVICE}'. Must be a valid UUID.")
        return False

    logger.info(f"Initializing Gmail service for user ID: {user_uuid}")

    async with SessionLocal() as db: # Create an async db session
        try:
            token_row: Optional[AgentToken] = await db.get(AgentToken, user_uuid)
        except Exception as e:
            logger.exception(f"Database error fetching AgentToken for user_id {user_uuid}: {e}")
            return False

    if not token_row:
        logger.error(f"No AgentToken found in DB for user_id {user_uuid}")
        return False

    if not token_row.token_json:
        logger.error(f"AgentToken for user_id {user_uuid} has no token_json data.")
        return False

    try:
        # Reuse build_credentials from setup_google.py
        creds = build_credentials(token_row.token_json)

        if creds.expired and creds.refresh_token:
            logger.info(f"Credentials for user {user_uuid} expired, attempting refresh.")
            creds.refresh(GoogleAuthRequest())
            # Persist the refreshed token back to the database
            logger.info(f"Credentials for user {user_uuid} refreshed. Saving new token to DB.")
            async with SessionLocal() as db_for_update: # New session for update
                refreshed_token_row = await db_for_update.get(AgentToken, user_uuid)
                if refreshed_token_row:
                    refreshed_token_row.token_json = json.loads(creds.to_json())
                    await db_for_update.commit()
                    await db_for_update.refresh(refreshed_token_row)
                    logger.info(f"Successfully saved refreshed token for user {user_uuid} to DB.")
                else:
                    logger.error(f"Could not find token row for user {user_uuid} to save refreshed token.")
        
        # Use googleapiclient.discovery.build directly
        GMAIL_SERVICE = build_google_service('gmail', 'v1', credentials=creds)
        logger.info(f"Gmail service successfully built for user {user_uuid}")

    except Exception as e:
        logger.exception(f"Failed to build or refresh Gmail service for user {user_uuid}: {e}")
        return False

    if GMAIL_SERVICE:
        # setup_gmail_watch will now be called with a proper service object
        watch_response = setup_gmail_watch(GMAIL_SERVICE) # This call is synchronous
        if watch_response:
            logger.info("Gmail watch setup initiated successfully.")
            return True
        else:
            logger.error("Failed to set up Gmail watch after service initialization.")
            return False
    else:
        logger.error(f"Failed to initialize Gmail service for user {user_uuid}")
    return False

def get_gmail_service_instance(): # This might need to be async if called outside of mail.py's initial setup
    """Return the Gmail service instance. Assumes initialize_gmail_service has been awaited."""
    global GMAIL_SERVICE
    if GMAIL_SERVICE is None:
        # This is tricky if called from synchronous code after async init.
        # Best to ensure initialize_gmail_service() is awaited at app startup.
        logger.warning("get_gmail_service_instance called when GMAIL_SERVICE is None. Initialization might have failed or not run.")
    return GMAIL_SERVICE

def setup_gmail_watch(gmail_service): # This function itself can remain synchronous
    """Set up Gmail API watch for the user's inbox."""
    if not gmail_service:
        logger.error("Cannot set up Gmail watch: No Gmail service available (was None)")
        return None
    
    try:
        topic_name = os.environ.get("PUBSUB_TOPIC")
        if not topic_name:
            logger.error("PUBSUB_TOPIC environment variable is not set")
            # Optionally: logger.error("Please run setup_pubsub.py to create the topic and set the environment variable")
            return None
        
        watch_request = {
            'labelIds': ['INBOX'],
            'topicName': topic_name,
            'labelFilterAction': 'include'
        }
        
        logger.info(f"Setting up Gmail watch with topic: {topic_name}")
        # This is a synchronous call from google-api-python-client
        watch_response = gmail_service.users().watch(userId='me', body=watch_request).execute()
        
        logger.info(f"Gmail watch set up successfully. Expiration: {watch_response.get('expiration')}")
        logger.info(f"Watch will send notifications to: {topic_name}")
        
        return watch_response
    except HttpError as error:
        logger.error(f"Failed to set up Gmail watch: {error}")
        if hasattr(error, 'resp') and error.resp:
             if error.resp.status == 404:
                logger.error("The Pub/Sub topic does not exist or is not properly configured.")
             elif error.resp.status == 403:
                logger.error("Permission denied. Make sure your OAuth credentials have the required scopes (gmail.modify).")
        return None
    except Exception as e:
        # This is where your original error was caught because `gmail_service` was a coroutine
        logger.error(f"Unexpected error setting up Gmail watch: {e}", exc_info=True)
        return None

def stop_gmail_watch(gmail_service): # Can remain synchronous
    """Stop the Gmail API watch."""
    if not gmail_service:
        logger.error("Cannot stop Gmail watch: No Gmail service available")
        return False
    
    try:
        gmail_service.users().stop(userId='me').execute()
        logger.info("Gmail watch stopped successfully")
        return True
    except HttpError as error:
        logger.error(f"Failed to stop Gmail watch: {error}")
        return False

def extract_and_decode_message(payload: Dict) -> Optional[Dict]:
    """Extract and decode the message data from a Pub/Sub notification."""
    try:
        logger.debug(f"Received raw payload for decoding: {json.dumps(payload)}")
        if 'message' in payload:
            message_data = payload['message'].get('data', '')
            if message_data:
                decoded_data = base64.b64decode(message_data).decode('utf-8')
                logger.debug(f"Decoded Pub/Sub notification data: {decoded_data}")
                decoded_json = json.loads(decoded_data)
                
                if 'messageId' in decoded_json: 
                    decoded_json['direct_message_id'] = decoded_json['messageId']
                    logger.info(f"Found direct messageId in notification: {decoded_json['direct_message_id']}")
                elif 'emailId' in decoded_json:
                    decoded_json['direct_message_id'] = decoded_json['emailId']
                    logger.info(f"Found direct emailId in notification: {decoded_json['direct_message_id']}")
                return decoded_json
            else: logger.warning("Payload has 'message' but no 'data' field")
        else: logger.warning("Payload missing 'message' field")
    except Exception as e:
        logger.exception(f"Error extracting message data: {e}")
    return None

async def process_email_notification(notification_data: Dict[str, Any], gmail_service_param): # Pass service explicitly
    """Process Gmail notification by getting only the latest unread email."""
    global LAST_PROCESSED_MESSAGE_ID, LAST_PROCESSED_TIME
    
    if not gmail_service_param: # Use the passed parameter
        logger.error("Cannot process notification: No Gmail service available (was None when passed)")
        return
    
    current_time = time.time()
    if (current_time - LAST_PROCESSED_TIME) < PROCESSING_COOLDOWN:
        logger.info(f"Cooldown period active. Skipping processing for {PROCESSING_COOLDOWN - (current_time - LAST_PROCESSED_TIME):.1f}s.")
        return
    
    try:
        logger.info("Looking for the single latest unread email...")
        results = gmail_service_param.users().messages().list(
            userId='me',
            q='is:unread in:inbox -category:promotions -category:social -from:noreply',
            maxResults=1
        ).execute()
        
        messages = results.get('messages', [])
        if not messages:
            logger.info("No new unread messages found.")
            return
            
        latest_message_id = messages[0]['id']
        if latest_message_id == LAST_PROCESSED_MESSAGE_ID:
            logger.info(f"Message {latest_message_id} was already processed recently. Skipping.")
            return
            
        logger.info(f"Processing latest unread message: {latest_message_id}")
        # Pass gmail_service_param
        response = fetch_and_process_email(gmail_service_param, latest_message_id)
        
        LAST_PROCESSED_MESSAGE_ID = latest_message_id
        LAST_PROCESSED_TIME = time.time()
        logger.info(f"Completed processing. Next processing allowed in {PROCESSING_COOLDOWN}s.")
        
    except Exception as e:
        logger.exception(f"Error processing email notification: {e}")

def fetch_and_process_email(gmail_service_param, message_id: str): # Pass service explicitly
    """Fetch a specific email and process it."""
    try:
        logger.info(f"Fetching email ID: {message_id}")
        message = gmail_service_param.users().messages().get(
            userId='me', id=message_id, format='full'
        ).execute()
        logger.info(f"Fetched email ID: {message_id}")
        
        email_content = extract_email_content(message)
        if not should_process_email(email_content):
            logger.info(f"Email {message_id} skipped by filter.")
            return None
        
        logger.info(f"Email details - ID: {message_id}, From: {email_content.get('from')}, Subject: {email_content.get('subject')}")
        body_length = len(email_content.get('body', ''))
        preview = email_content.get('body', '')[:100] + "..." if body_length > 100 else email_content.get('body', '')
        logger.info(f"Email body preview (len {body_length}): {preview}")
        
        logger.info("Handing off email to AI agent...")
        response = handle_email_with_ai_agent(email_content)
        
        if response: logger.info("AI agent processed email and generated response.")
        else: logger.warning("AI agent processing completed; no response generated.")
        return response
        
    except HttpError as error:
        logger.error(f"Gmail API error fetching email {message_id}: {error.resp.status} {error.resp.reason}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error fetching/processing email {message_id}: {e}")
        return None

def extract_email_content(email_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract relevant content from the email data."""
    # ... (no changes needed in this function's logic)
    try:
        logger.debug("Starting to extract email content from raw data...")
        if 'payload' not in email_data:
            logger.error("Email data missing 'payload' field")
            return {'error': 'Missing payload'}
        if 'headers' not in email_data['payload']:
            logger.error("Email payload missing 'headers' field")
            return {'error': 'Missing headers'}
        
        headers = {header['name']: header['value'] for header in email_data['payload']['headers']}
        logger.debug(f"Successfully extracted {len(headers)} headers")
        
        email_content = {
            'id': email_data.get('id', 'N/A'),
            'threadId': email_data.get('threadId', 'N/A'),
            'subject': headers.get('Subject', '(No Subject)'),
            'from': headers.get('From', 'Unknown'),
            'to': headers.get('To', 'Unknown'),
            'date': headers.get('Date', 'Unknown'),
            'labels': email_data.get('labelIds', [])
        }
        
        logger.debug("Extracting email body...")
        email_content['body'] = get_email_body(email_data['payload'])
        
        if not email_content['body']: logger.warning("Extracted empty email body")
        else: logger.debug(f"Extracted email body ({len(email_content['body'])} chars)")
        
        logger.debug("Email content extraction completed.")
        return email_content
    
    except KeyError as e:
        logger.exception(f"KeyError extracting email content, missing key: {e}")
        return {'error': f'Missing key: {str(e)}'}
    except Exception as e:
        logger.exception(f"Error extracting email content: {e}")
        return {'error': str(e)}


def get_email_body(payload: Dict[str, Any]) -> str:
    """Recursively extract the email body from the payload."""
    # ... (no changes needed in this function's logic)
    logger.debug(f"Extracting body from part with mimeType: {payload.get('mimeType', 'unknown')}")
    if 'body' in payload and payload['body'].get('data'):
        logger.debug("Found body data to decode")
        try:
            data = payload['body']['data']
            decoded_data = base64.urlsafe_b64decode(data).decode('utf-8')
            logger.debug(f"Successfully decoded body data ({len(decoded_data)} characters)")
            return decoded_data
        except Exception as e:
            logger.error(f"Error decoding body data: {e}")
            return ""
    
    if 'parts' in payload:
        logger.debug(f"Found {len(payload['parts'])} child parts, searching for text content")
        for i, part in enumerate(payload['parts']):
            logger.debug(f"Checking part {i+1}/{len(payload['parts'])}, mimeType: {part.get('mimeType', 'unknown')}")
            if part['mimeType'] == 'text/plain' or part['mimeType'] == 'text/html':
                logger.debug(f"Found {part['mimeType']} part, extracting content")
                body = get_email_body(part)
                if body:
                    logger.debug(f"Successfully extracted content from {part['mimeType']} part")
                    return body
    
    logger.debug("No text content found in this part or its children")
    return ""

def handle_email_with_ai_agent(email_content: dict):
    """Process the email directly with the ExecutiveAgent."""
    # ... (no changes needed in this function's logic, assuming ExecutiveAgent is synchronous)
    logger.info("Started handling email with AI agent (ExecutiveAgent)...")
    if not email_content or 'id' not in email_content:
        logger.error("Invalid email content received by handle_email_with_ai_agent.")
        return None

    message_id = email_content['id']
    logger.info(f"Processing email ID: {message_id} using ExecutiveAgent.")

    try:
        executive_agent = ExecutiveAgent()
        agent_input = json.dumps(email_content, indent=2)
        logger.debug(f"Input for Executive Agent (Email Content JSON truncated):\n{agent_input[:500]}...")

        logger.info(f"Calling ExecutiveAgent for email {message_id}...")
        agent_response_text = executive_agent.run(input_query=agent_input)
        logger.info(f"ExecutiveAgent finished processing for email {message_id}.")
        logger.info(f"Executive Agent Final Response Text: {agent_response_text}")
        return agent_response_text

    except Exception as e:
        logger.exception(f"Error occurred while handling email {message_id} with ExecutiveAgent: {e}")
        return None