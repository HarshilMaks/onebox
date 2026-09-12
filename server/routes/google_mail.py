from fastapi import APIRouter, Depends, HTTPException, Path, Query
from server.schemas import EmailDetail, EmailDraft, EmailPage, MailMutationResponse, SendEmailResponse, SaveDraftResponse, HealthResponse, CheckInboxResponse, StarStateUpdate
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from email.mime.text import MIMEText
import base64
import hashlib
import logging
from server.services.setup_google import get_current_user_info, get_gmail_service
from server.redis_cache import (
    MAIL_DETAIL_CACHE_TTL_SECONDS,
    MAIL_PAGE_CACHE_TTL_SECONDS,
    cache_get,
    cache_set,
    invalidate_user_mail_cache,
    user_mail_cache_key,
)
from server.integrations.gmail import execute_gmail_request as _gmail_execute
from server.integrations.google import GoogleOperationSafety
from server.services.mailbox import fetch_message_page as _fetch_message_page
from server.services.mailbox import parse_message, search_emails


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mail", tags=["Email-Operations"])


# ---------- Endpoints ----------
@router.get("/emails", response_model=EmailPage)
async def fetch_emails(
    folder: str = Query("inbox", min_length=1, max_length=32, description="Folder: inbox, sent, spam, trash, starred, all"),
    limit: int = Query(20, ge=1, le=100, description="Max number of emails per page"),
    page_token: str | None = Query(default=None, min_length=1, max_length=512, description="Gmail pagination token"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service),
):
    user_id = str(user_info["user_id"])
    label_map = {
        "inbox": ["INBOX"],
        "sent": ["SENT"],
        "spam": ["SPAM"],
        "trash": ["TRASH"],
        "starred": ["STARRED"],
        "all": [],
    }
    if folder not in label_map:
        raise HTTPException(status_code=400, detail="Invalid folder")

    token_hash = hashlib.sha256((page_token or "first").encode("utf-8")).hexdigest()
    cache_key = await user_mail_cache_key(user_id, f"page:{folder}:{limit}:{token_hash}")
    cached_data = await cache_get(cache_key)
    if isinstance(cached_data, dict):
        return cached_data

    try:
        result = await _fetch_message_page(
            service,
            user_id="me",
            limit=limit,
            page_token=page_token,
            label_ids=label_map[folder],
        )
        await cache_set(cache_key, result, ttl=MAIL_PAGE_CACHE_TTL_SECONDS)
        return result
    except HTTPException:
        raise
    except HttpError as exc:
        raise HTTPException(status_code=exc.resp.status, detail="Gmail request failed. Please try again.") from None
    except Exception:
        logger.exception("Unexpected Gmail page fetch failure")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.") from None


@router.get("/emails/{email_id}", response_model=EmailDetail)
async def fetch_email_by_id(
    email_id: str = Path(..., min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    gmail_user_id_param = 'me'
    logger.info(f"Fetching email with ID: {email_id} for app user {user_id}")
    
    cache_key = await user_mail_cache_key(str(user_id), f"detail:{email_id}")
    cached_email = await cache_get(cache_key)
    if cached_email:
        logger.info(f"Serving cached email for ID {email_id}, user {user_id} (key: {cache_key})")
        return cached_email

    try:
        msg = await _gmail_execute(
            service,
            service.users().messages().get(userId=gmail_user_id_param, id=email_id, format='full'),
            safety=GoogleOperationSafety.READ,
        )
        parsed_email = await parse_message(service, msg, user_id_for_attachments=gmail_user_id_param)
        await cache_set(cache_key, parsed_email, ttl=MAIL_DETAIL_CACHE_TTL_SECONDS)
        return parsed_email
    except HttpError as e:
        content = e.content.decode() if e.content else str(e)
        logger.exception(f"Gmail API error fetching email ID {email_id} for user {user_id}: {content}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error fetching email ID {email_id} for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.")


# Other endpoints (mark_as_read, unread, trash, etc.) remain largely the same but ensure logging includes user_id if relevant
# Example for mark_as_read:
@router.post("/emails/{email_id}/read", response_model=MailMutationResponse)
async def mark_as_read(
    email_id: str = Path(..., min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Marking email as read: {email_id} for user {user_id}")
    try:
        await _gmail_execute(
            service,
            service.users().messages().modify(
                userId='me', id=email_id, body={'removeLabelIds': ['UNREAD']}
            ),
            safety=GoogleOperationSafety.IDEMPOTENT_WRITE,
        )
        # Invalidate cache for this email and relevant lists
        await invalidate_user_mail_cache(str(user_id), email_id)
        # Potentially invalidate list caches too, or update the specific item in list caches
        return {"id": email_id, "status": "marked as read"}
    except HttpError as e:
        logger.exception(f"Failed to mark email {email_id} as read for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")

@router.post("/emails/{email_id}/unread", response_model=MailMutationResponse)
async def mark_as_unread(
    email_id: str = Path(..., min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Marking email as unread: {email_id} for user: {user_id}")
    try:
        await _gmail_execute(
            service,
            service.users().messages().modify(
                userId='me', id=email_id, body={'addLabelIds': ['UNREAD']}
            ),
            safety=GoogleOperationSafety.IDEMPOTENT_WRITE,
        )
        await invalidate_user_mail_cache(str(user_id), email_id)
        return {"id": email_id, "status": "marked as unread"}
    except HttpError as e:
        logger.exception(f"Failed to mark email {email_id} as unread for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred while marking email {email_id} as unread for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.")


@router.post("/emails/{email_id}/trash", response_model=MailMutationResponse)
async def move_to_trash(
    email_id: str = Path(..., min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Moving email to trash: {email_id} for user: {user_id}")
    try:
        await _gmail_execute(service, service.users().messages().trash(userId='me', id=email_id))
        await invalidate_user_mail_cache(str(user_id), email_id)
        # Also consider invalidating/updating list caches from which this email was removed
        return {"id": email_id, "status": "moved to trash"}
    except HttpError as e:
        logger.exception(f"Failed to move email {email_id} to trash for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred while moving email {email_id} to trash for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.")

@router.post("/emails/{email_id}/restore", response_model=MailMutationResponse)
async def restore_from_trash(
    email_id: str = Path(..., min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Restoring email from trash: {email_id} for user: {user_id}")
    try:
        await _gmail_execute(service, service.users().messages().untrash(userId='me', id=email_id))
        await invalidate_user_mail_cache(str(user_id), email_id)
        # Also consider invalidating/updating list caches to which this email was added
        return {"id": email_id, "status": "restored from trash"}
    except HttpError as e:
        logger.exception(f"Failed to restore email {email_id} from trash for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred while restoring email {email_id} from trash for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.")


@router.delete("/emails/{email_id}", response_model=MailMutationResponse)
async def delete_email(
    email_id: str = Path(..., min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"Permanently deleting email: {email_id} for user: {user_id}")
    try:
        await _gmail_execute(service, service.users().messages().delete(userId='me', id=email_id))
        await invalidate_user_mail_cache(str(user_id), email_id)
        return {"id": email_id, "status": "permanently deleted"}
    except HttpError as e:
        logger.exception(f"Failed to delete email {email_id} for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred while deleting email {email_id} for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.")


@router.post("/emails/{email_id}/star", response_model=MailMutationResponse)
async def set_star_state(
    state: StarStateUpdate,
    email_id: str = Path(..., min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service),
):
    """Set the desired star label exactly; no read-then-toggle race exists."""
    user_id = user_info["user_id"]
    body = {"addLabelIds": ["STARRED"]} if state.starred else {"removeLabelIds": ["STARRED"]}
    try:
        await _gmail_execute(
            service,
            service.users().messages().modify(userId="me", id=email_id, body=body),
            safety=GoogleOperationSafety.IDEMPOTENT_WRITE,
        )
        await invalidate_user_mail_cache(str(user_id), email_id)
        return {
            "id": email_id,
            "status": "starred" if state.starred else "unstarred",
            "action": "set",
        }
    except HttpError as exc:
        raise HTTPException(status_code=exc.resp.status, detail="Gmail request failed. Please try again.") from None
    except HTTPException:
        raise
    except Exception:
        logger.exception("Gmail star-state update failed")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.") from None

@router.post("/send", response_model=SendEmailResponse)
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
        message = await _gmail_execute(
            service,
            service.users().messages().send(userId='me', body={'raw': raw}),
        )
        await invalidate_user_mail_cache(str(user_id), message.get('id', ''))
        return {"id": message.get('id'), "status": "sent"}
    except HttpError as e:
        logger.exception(f"Failed to send email for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred while sending email for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.")


@router.post("/drafts", response_model=SaveDraftResponse)
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
            draft = await _gmail_execute(
                service,
                service.users().drafts().update(userId='me', id=email.draft_id, body=draft_body),
            )
            status_msg = "draft updated"
        else: # Creating a new draft
            draft = await _gmail_execute(
                service,
                service.users().drafts().create(userId='me', body=draft_body),
            )
            status_msg = "draft saved"
        
        # Invalidate draft list cache if any
        return {"id": draft['id'], "status": status_msg, "draft_id": draft['id']}
    except HttpError as e:
        logger.exception(f"Failed to save draft for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred while saving draft for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.")


@router.get("/search", response_model=EmailPage)
async def search_endpoint(
    q: str = Query(..., min_length=1, max_length=512, description="Gmail search query string"),
    limit: int = Query(20, ge=1, le=100, description="Max number of results per page"),
    page_token: str | None = Query(default=None, min_length=1, max_length=512, description="Gmail pagination token"),
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service),
):
    user_id = str(user_info["user_id"])
    query_hash = hashlib.sha256(q.encode("utf-8")).hexdigest()
    token_hash = hashlib.sha256((page_token or "first").encode("utf-8")).hexdigest()
    cache_key = await user_mail_cache_key(user_id, f"search:{query_hash}:{limit}:{token_hash}")
    cached_results = await cache_get(cache_key)
    if isinstance(cached_results, dict):
        return cached_results

    result = await search_emails(service, q, user_id="me", limit=limit, page_token=page_token)
    await cache_set(cache_key, result, ttl=MAIL_PAGE_CACHE_TTL_SECONDS)
    return result


@router.get("/health", response_model=HealthResponse)
def health_check():
    logger.debug("Health check hit")
    return {"status": "healthy"}

@router.post("/check-inbox", response_model=CheckInboxResponse)
async def check_inbox_api( # Renamed
    user_info: dict = Depends(get_current_user_info),
    service: Resource = Depends(get_gmail_service)
):
    user_id = user_info["user_id"]
    logger.info(f"User {user_id} checking inbox count")
    try:
        # This only gets an estimate, doesn't trigger actual new mail pull
        response = await _gmail_execute(
            service,
            service.users().labels().get(userId='me', id='INBOX'),
            safety=GoogleOperationSafety.READ,
        )
        count = response.get('messagesTotal', 0) # messagesUnread might also be useful
        logger.info(f"Inbox total messages estimate for user {user_id}: {count}")
        return {"status": "checked", "inbox_message_count_estimate": count}
    except HttpError as e:
        logger.exception(f"Failed to check inbox count for user {user_id}: {e.content.decode() if e.content else str(e)}")
        raise HTTPException(status_code=e.resp.status, detail="Gmail request failed. Please try again.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred while checking inbox count for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Mail operation failed. Please try again.")