from fastapi import APIRouter, Depends, HTTPException, Query
from server.schemas import EmailDraft, Email as EmailSchema, EmailPage
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from email.mime.text import MIMEText
import base64
import logging
from server.logging_config import setup_logging
from typing import List, Dict, Any, Optional
from uuid import UUID
from server.services.setup_google import get_current_user_info, get_gmail_service
from server.redis_cache import cache_get, cache_set
from email.utils import parsedate_to_datetime
from datetime import datetime
import re 
setup_logging()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mail", tags=["Email-Operations"])


def get_attachment_data(service: Resource, user_id: str, message_id: str, attachment_id: str) -> Optional[str]:
    """Fetches attachment data (base64 encoded)."""
    try:
        attachment = service.users().messages().attachments().get(
            userId=user_id, messageId=message_id, id=attachment_id
        ).execute()
        return attachment.get('data')
    except HttpError as e:
        logger.error(f"Error fetching attachment {attachment_id} for message {message_id}: {e}")
        return None

def process_email_parts(
    service: Resource,
    user_id: str, # 'me' or actual user ID
    message_id: str,
    parts: List[Dict[str, Any]],
    cid_map: Dict[str, Dict[str, str]]
) -> Optional[str]:
    """
    Recursively processes MIME parts to find HTML body and build CID map for inline images.
    Returns the HTML body content if found.
    """
    html_body_content = None
    for part in parts:
        mime_type = part.get('mimeType', '').lower()
        content_id_header = next((h['value'] for h in part.get('headers', []) if h['name'].lower() == 'content-id'), None)
        
        if mime_type == 'text/html' and not html_body_content: # Take the first HTML part
            if part.get('body', {}).get('data'):
                try:
                    html_body_content = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
                except Exception as e:
                    logger.error(f"Error decoding HTML part for message {message_id}: {e}")
        
        elif mime_type.startswith('image/') and content_id_header:
            cid = content_id_header.strip('<>') # Remove < > brackets
            if cid not in cid_map: # Only process if not already mapped (e.g. from multipart/alternative)
                image_data_b64 = None
                if part.get('body', {}).get('data'):
                    image_data_b64 = part['body']['data']
                elif part.get('body', {}).get('attachmentId'):
                    attachment_id = part['body']['attachmentId']
                    logger.info(f"Fetching inline attachment {attachment_id} for CID {cid} in message {message_id}")
                    image_data_b64 = get_attachment_data(service, user_id, message_id, attachment_id)
                
                if image_data_b64:
                    cid_map[cid] = {
                        'mimeType': mime_type,
                        'data': image_data_b64 # Already base64url encoded from API
                    }

        if part.get('parts'): # Recurse for nested parts (e.g., multipart/related, multipart/alternative)
            nested_html = process_email_parts(service, user_id, message_id, part['parts'], cid_map)
            if nested_html and not html_body_content: # Prioritize nested HTML if found and top-level not yet
                html_body_content = nested_html
                
    return html_body_content


def parse_message(service: Resource, msg: Dict[str, Any], user_id_for_attachments: str = 'me') -> Dict[str, Any]:
    headers_list = msg.get('payload', {}).get('headers', [])
    headers = {h['name'].lower(): h['value'] for h in headers_list}

    to_header = headers.get('to', '')
    to_list = [addr.strip() for addr in to_header.split(',') if addr.strip()] if isinstance(to_header, str) else []
    cc_header = headers.get('cc', '')
    cc_list = [addr.strip() for addr in cc_header.split(',') if addr.strip()] if isinstance(cc_header, str) else []
    
    date_str = headers.get('date')
    date_iso = datetime.utcnow().isoformat() # Fallback
    if date_str:
        try:
            dt_obj = parsedate_to_datetime(date_str)
            date_iso = dt_obj.isoformat()
        except Exception:
            internal_date_ms_str = msg.get('internalDate')
            if internal_date_ms_str:
                try: date_iso = datetime.fromtimestamp(int(internal_date_ms_str) / 1000).isoformat()
                except: pass

    html_body = None
    cid_data_map: Dict[str, Dict[str,str]] = {} # Maps CID to {'mimeType': 'image/png', 'data': 'base64string'}
    
    payload = msg.get('payload', {})
    if payload.get('parts'):
        html_body = process_email_parts(service, user_id_for_attachments, msg['id'], payload['parts'], cid_data_map)
    elif payload.get('mimeType', '').lower() == 'text/html' and payload.get('body', {}).get('data'):
        # Handle non-multipart email that is just HTML
        try:
            html_body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
        except Exception as e:
            logger.error(f"Error decoding simple HTML body for message {msg['id']}: {e}")

    # If HTML body was found and there are CIDs to replace
    if html_body and cid_data_map:
        for cid, image_info in cid_data_map.items():
            # Regex to find cid: anystring including the cid value, case insensitive for "cid"
            # It looks for src="cid:..." or src='cid:...'
            # Ensure cid is escaped for regex if it contains special characters (though unlikely for CIDs)
            escaped_cid = re.escape(cid)
            # The data from Gmail API is base64url, convert to standard base64 if needed for data URI
            # Standard base64 uses + and /, base64url uses - and _
            # Padding (=) might also be an issue, data URIs generally expect standard base64 padding.
            standard_b64_data = image_info['data'].replace('-', '+').replace('_', '/')
            # Add padding if necessary
            missing_padding = len(standard_b64_data) % 4
            if missing_padding:
                standard_b64_data += '=' * (4 - missing_padding)

            data_url = f"data:{image_info['mimeType']};base64,{standard_b64_data}"
            
            # More robust regex to handle quotes and potential spaces
            html_body = re.sub(
                rf"""src\s*=\s*['"]\s*cid:{re.escape(cid)}\s*['"]""",
                f'src="{data_url}"',
                html_body,
                flags=re.IGNORECASE
            )
    elif not html_body and payload.get('mimeType','').lower() == 'text/plain' and payload.get('body', {}).get('data'):
        # Fallback to plain text if no HTML
        try:
            plain_text_body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')
            # Convert plain text to basic HTML (e.g., wrap in <pre> and escape)
            import html
            html_body = f"<pre>{html.escape(plain_text_body)}</pre>"
        except Exception as e:
            logger.error(f"Error decoding plain text body for message {msg['id']}: {e}")


    return {
        'id': msg.get('id'),
        'threadId': msg.get('threadId'),
        'subject': headers.get('subject', "(No Subject)"),
        'sender': headers.get('from', "Unknown Sender"),
        'to': to_list,
        'cc': cc_list,
        'snippet': msg.get('snippet', ''),
        'body': html_body or msg.get('snippet', ''), # THIS IS THE IMPORTANT PART
        'is_read': 'UNREAD' not in msg.get('labelIds', []),
        'is_starred': 'STARRED' in msg.get('labelIds', []),
        'labels': msg.get('labelIds', []),
        'date': date_iso,
    }
    
def search_emails(service: Resource, query: str, user_id: str = 'me') -> List[dict]:
    results = []
    try:
        logger.info(f"Searching emails with query: {query} for user: {user_id}")
        request = service.users().messages().list(userId=user_id, q=query, maxResults=100) # Consider pagination for search if needed
        while request:
            response = request.execute()
            messages_metadata = response.get('messages', [])
            if not messages_metadata:
                break

            batch = service.new_batch_http_request()
            message_details_temp = {} # Use a temporary dict for batch results

            def parse_batch_response(request_id, batch_response, exception):
                if exception is not None:
                    logger.error(f"Batch get error for message ID {request_id} for user {user_id}: {exception}")
                else:
                    # Using the full parse_message here for search results as well
                    message_details_temp[batch_response['id']] = parse_message(batch_response)

            for msg_meta in messages_metadata:
                 batch.add(
                     service.users().messages().get(userId=user_id, id=msg_meta['id'], format='full'), # Fetch full for search too
                     callback=parse_batch_response,
                     request_id=msg_meta['id']
                 )

            if messages_metadata:
                logger.info(f"Executing batch get for {len(messages_metadata)} search result messages for user {user_id}.")
                batch.execute()
            
            # Ensure order is maintained from messages_metadata
            for msg_meta in messages_metadata:
                if msg_meta['id'] in message_details_temp:
                    results.append(message_details_temp[msg_meta['id']])


            request = service.users().messages().list_next(request, response)

    except HttpError as e:
        logger.exception(f"Failed during email search with query '{query}' for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API search error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred during search with query '{query}' for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error during search: {e}")

    return results

# ---------- Endpoints ----------
@router.get("/emails", response_model=EmailPage)
async def fetch_emails(
    folder: str = Query("inbox", description="Folder: inbox, sent, spam, trash, starred, all"),
    limit: int = Query(20, ge=1, description="Max number of emails per page"),
    page_token: str = Query(None, description="Token for pagination"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"] # This is your application's internal user_id
    gmail_user_id_param = 'me' # Use 'me' for Gmail API calls for the authenticated user
    logger.info(f"Fetching emails from folder: {folder} for app user: {user_id} (Gmail user: {gmail_user_id_param}) with limit: {limit}, page_token: {page_token}")
    
    cache_key = f"user:{user_id}:emails_v2:{folder}:{limit}:{page_token}" # Added _v2 to key for new format
    cached_data = cache_get(cache_key)
    if cached_data:
        logger.info(f"Serving cached emails for user {user_id} from {folder} (key: {cache_key})")
        return cached_data

    label_map = {
        'inbox': ['INBOX'], 'sent': ['SENT'], 'spam': ['SPAM'],
        'trash': ['TRASH'], 'starred': ['STARRED'], 'all': [],
    }
    if folder not in label_map and folder != 'all':
        raise HTTPException(status_code=400, detail="Invalid folder")
    label_ids = label_map.get(folder, [])

    emails_data: List[dict] = []
    next_page_token_val = None

    try:
        if folder == 'starred': # Starred still needs search then full fetch
            all_starred_msgs_parsed = search_emails(service, 'is:starred', user_id=gmail_user_id_param) # search_emails calls parse_message internally
            start_index = 0
            if page_token:
                idx = next((i for i, msg in enumerate(all_starred_msgs_parsed) if msg['id'] == page_token), -1)
                if idx != -1: start_index = idx + 1
            emails_data = all_starred_msgs_parsed[start_index : start_index + limit]
            if start_index + limit < len(all_starred_msgs_parsed):
                next_page_token_val = all_starred_msgs_parsed[start_index + limit]['id']
        else:
            request_params = {'userId': gmail_user_id_param, 'maxResults': limit, 'labelIds': label_ids}
            if page_token: request_params['pageToken'] = page_token
            
            request = service.users().messages().list(**request_params)
            response = request.execute()
            next_page_token_val = response.get('nextPageToken')
            messages_metadata = response.get('messages', [])

            if messages_metadata:
                batch = service.new_batch_http_request()
                message_details_temp = {}
                def parse_batch_list_response(request_id, batch_response, exception):
                    if exception: logger.error(f"Batch list get error for msg ID {request_id}: {exception}")
                    else: message_details_temp[batch_response['id']] = parse_message(service, batch_response, user_id_for_attachments=gmail_user_id_param)
                
                for msg_meta in messages_metadata:
                     batch.add(service.users().messages().get(userId=gmail_user_id_param, id=msg_meta['id'], format='full'),
                               callback=parse_batch_list_response, request_id=msg_meta['id'])
                batch.execute()
                emails_data = [message_details_temp[msg_meta['id']] for msg_meta in messages_metadata if msg_meta['id'] in message_details_temp]

        result = {"emails": emails_data, "next_page_token": next_page_token_val}
        cache_set(cache_key, result, ttl=60)
        return result
    except HttpError as e: # Catch HttpError specifically
        content = e.content.decode() if e.content else str(e)
        logger.exception(f"Gmail API error fetching emails from {folder} for user {user_id}: {content}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API list error: {content}")
    except Exception as e:
        logger.exception(f"Unexpected error fetching emails from {folder} for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.get("/emails/{email_id}", response_model=EmailSchema)
async def fetch_email_by_id(
    email_id: str,
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    gmail_user_id_param = 'me'
    logger.info(f"Fetching email with ID: {email_id} for app user {user_id}")
    
    cache_key = f"user:{user_id}:email_v2:{email_id}"
    cached_email = cache_get(cache_key)
    if cached_email:
        logger.info(f"Serving cached email for ID {email_id}, user {user_id} (key: {cache_key})")
        return cached_email

    try:
        msg = service.users().messages().get(userId=gmail_user_id_param, id=email_id, format='full').execute()
        parsed_email = parse_message(service, msg, user_id_for_attachments=gmail_user_id_param)
        cache_set(cache_key, parsed_email, ttl=300)
        return parsed_email
    except HttpError as e:
        content = e.content.decode() if e.content else str(e)
        logger.exception(f"Gmail API error fetching email ID {email_id} for user {user_id}: {content}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API fetch error: {content}")
    except Exception as e:
        logger.exception(f"Unexpected error fetching email ID {email_id} for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


# Other endpoints (mark_as_read, unread, trash, etc.) remain largely the same but ensure logging includes user_id if relevant
# Example for mark_as_read:
@router.post("/emails/{email_id}/read")
async def mark_as_read(
    email_id: str,
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Marking email as read: {email_id} for user {user_id}")
    try:
        service.users().messages().modify(
            userId='me', id=email_id,
            body={'removeLabelIds': ['UNREAD']}
        ).execute()
        # Invalidate cache for this email and relevant lists
        cache_key_email = f"user:{user_id}:email:{email_id}"
        cache_get(cache_key_email, delete=True) # Simple delete, more sophisticated invalidation might be needed
        # Potentially invalidate list caches too, or update the specific item in list caches
        return {"id": email_id, "status": "marked as read"}
    except HttpError as e:
        logger.exception(f"Failed to mark email {email_id} as read for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API modify error: {e.content.decode() if e.content else str(e)}")

@router.post("/emails/{email_id}/unread")
async def mark_as_unread(
    email_id: str,
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Marking email as unread: {email_id} for user: {user_id}")
    try:
        service.users().messages().modify(
            userId='me', id=email_id,
            body={'addLabelIds': ['UNREAD']}
        ).execute()
        cache_key_email = f"user:{user_id}:email:{email_id}"
        cache_get(cache_key_email, delete=True)
        return {"id": email_id, "status": "marked as unread"}
    except HttpError as e:
        logger.exception(f"Failed to mark email {email_id} as unread for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API modify error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while marking email {email_id} as unread for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.post("/emails/{email_id}/trash")
async def move_to_trash(
    email_id: str,
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Moving email to trash: {email_id} for user: {user_id}")
    try:
        service.users().messages().trash(userId='me', id=email_id).execute()
        cache_key_email = f"user:{user_id}:email:{email_id}"
        cache_get(cache_key_email, delete=True) # Invalidate single email cache
        # Also consider invalidating/updating list caches from which this email was removed
        return {"id": email_id, "status": "moved to trash"}
    except HttpError as e:
        logger.exception(f"Failed to move email {email_id} to trash for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API trash error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while moving email {email_id} to trash for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

@router.post("/emails/{email_id}/restore")
async def restore_from_trash(
    email_id: str,
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Restoring email from trash: {email_id} for user: {user_id}")
    try:
        service.users().messages().untrash(userId='me', id=email_id).execute()
        cache_key_email = f"user:{user_id}:email:{email_id}"
        cache_get(cache_key_email, delete=True) # Invalidate single email cache
        # Also consider invalidating/updating list caches to which this email was added
        return {"id": email_id, "status": "restored from trash"}
    except HttpError as e:
        logger.exception(f"Failed to restore email {email_id} from trash for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API untrash error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while restoring email {email_id} from trash for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.delete("/emails/{email_id}")
async def delete_email(
    email_id: str,
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Permanently deleting email: {email_id} for user: {user_id}")
    try:
        service.users().messages().delete(userId='me', id=email_id).execute()
        cache_key_email = f"user:{user_id}:email:{email_id}"
        cache_get(cache_key_email, delete=True)
        # Also invalidate from trash list cache
        return {"id": email_id, "status": "permanently deleted"}
    except HttpError as e:
        logger.exception(f"Failed to delete email {email_id} for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API delete error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while deleting email {email_id} for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.post("/emails/{email_id}/star")
async def toggle_star(
    email_id: str,
    # star_status: bool, # If you want to set specific status, not just toggle
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Toggling star for email: {email_id} for user: {user_id}")
    try:
        msg = service.users().messages().get(userId='me', id=email_id, format='minimal').execute()
        labels = msg.get('labelIds', [])
        is_starred = 'STARRED' in labels
        
        # If you want to set specific status based on a param:
        # desired_starred_state = star_status 
        # add_labels = ['STARRED'] if desired_starred_state and not is_starred else []
        # remove_labels = ['STARRED'] if not desired_starred_state and is_starred else []
        
        # For simple toggle:
        add_labels = [] if is_starred else ['STARRED']
        remove_labels = ['STARRED'] if is_starred else []

        body_mod = {}
        if add_labels: body_mod['addLabelIds'] = add_labels
        if remove_labels: body_mod['removeLabelIds'] = remove_labels

        if not body_mod: # No change needed
            new_status = "starred" if is_starred else "unstarred"
            logger.info(f"No label change needed for email {email_id}, current status: {new_status}")
            return {"id": email_id, "status": new_status, "action": "no_change"}

        service.users().messages().modify(userId='me', id=email_id, body=body_mod).execute()
        new_status = "unstarred" if is_starred else "starred" # This is the old status, after toggle it's reversed
        
        cache_key_email = f"user:{user_id}:email:{email_id}"
        cache_get(cache_key_email, delete=True) # Invalidate cache

        return {"id": email_id, "status": "starred" if not is_starred else "unstarred"} # Return new status
    except HttpError as e:
        logger.exception(f"Failed to toggle star for email {email_id} for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API modify error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while toggling star for email {email_id} for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

@router.post("/send")
async def send_email_api( # Renamed to avoid conflict
    email: EmailDraft,
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"] # For logging or other user-specific logic if needed
    logger.info(f"User {user_id} sending email to: {email.to} | Subject: {email.subject}")
    try:
        msg = MIMEText(email.body)
        msg['to'] = ', '.join(email.to)
        msg['subject'] = email.subject
        # Add from header, typically your own email
        # user_email = user_info.get("email") # Assuming get_current_user_info provides it
        # if user_email:
        #    msg['from'] = user_email
        # else:
        #    logger.warning(f"User email not found in user_info for user {user_id} when sending email.")


        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        message = service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return {"id": message.get('id'), "status": "sent"}
    except HttpError as e:
        logger.exception(f"Failed to send email for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API send error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while sending email for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.post("/drafts")
async def save_draft_api( # Renamed
    email: EmailDraft,
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"User {user_id} saving draft for: {email.to} | Subject: {email.subject}")
    try:
        msg = MIMEText(email.body)
        msg['to'] = ', '.join(email.to)
        msg['subject'] = email.subject
        # if user_info.get("email"): msg['from'] = user_info.get("email")

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft_body = {'message': {'raw': raw}}
        if email.draft_id: # If updating an existing draft
            draft = service.users().drafts().update(userId='me', id=email.draft_id, body=draft_body).execute()
            status_msg = "draft updated"
        else: # Creating a new draft
            draft = service.users().drafts().create(userId='me', body=draft_body).execute()
            status_msg = "draft saved"
        
        # Invalidate draft list cache if any
        return {"id": draft['id'], "status": status_msg, "draft_id": draft['id']}
    except HttpError as e:
        logger.exception(f"Failed to save draft for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API draft error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while saving draft for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.get("/search", response_model=List[EmailSchema]) # Use EmailSchema
async def search_endpoint(
    q: str = Query(..., description="Gmail search query string"),
    limit: int = Query(20, ge=1, description="Max number of results to return"), # Reduced default for search
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"User {user_id} performing search query: '{q}' with limit {limit}")
    
    # Cache key for search results might be complex if q is very dynamic. Consider not caching or short TTL.
    cache_key = f"user:{user_id}:search:{q[:50]}:{limit}" # Truncate q for key length
    cached_results = cache_get(cache_key)
    if cached_results:
        logger.info(f"Serving cached search results for user {user_id}, query '{q}' (key: {cache_key})")
        return cached_results

    try:
        results = search_emails(service, q, user_id='me') # search_emails already uses parse_message
        limited_results = results[:limit]
        cache_set(cache_key, limited_results, ttl=60) # Cache search results for 1 minute
        logger.info(f"Search returned {len(results)} results for user {user_id}, endpoint limit {limit}.")
        return limited_results
    except HTTPException: # Re-raise HTTPExceptions from search_emails
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred in the search endpoint for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")


@router.get("/health")
def health_check():
    logger.debug("Health check hit")
    return {"status": "healthy"}

@router.post("/check-inbox")
async def check_inbox_api( # Renamed
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"User {user_id} checking inbox count")
    try:
        # This only gets an estimate, doesn't trigger actual new mail pull
        response = service.users().labels().get(userId='me', id='INBOX').execute()
        count = response.get('messagesTotal', 0) # messagesUnread might also be useful
        logger.info(f"Inbox total messages estimate for user {user_id}: {count}")
        return {"status": "checked", "inbox_message_count_estimate": count}
    except HttpError as e:
        logger.exception(f"Failed to check inbox count for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail=f"Gmail API count error: {e.content.decode() if e.content else str(e)}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred while checking inbox count for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")