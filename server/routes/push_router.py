import json
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from googleapiclient.errors import HttpError

from server.logging_config import setup_logging
from server.schemas import GlobalGmailHealthResponse
from server.services.mail import (
    AGENT_USER_ID_FOR_SERVICE,
    execute_google_request,
    extract_and_decode_message,
    fetch_and_process_email,
    get_gmail_service_instance,
    process_email_notification,
    setup_gmail_watch,
)
from server.services.setup_google import get_current_user_info

setup_logging()
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mail", tags=["mail"])


async def require_global_gmail_operator(
    user_info: dict = Depends(get_current_user_info),
) -> dict:
    """Authorize only the JWT owner of the configured global Gmail account.

    Global automation routes operate on a single configured mailbox, not the
    caller's per-user mailbox. They must therefore fail closed unless the
    caller is exactly that configured account owner.
    """
    if not AGENT_USER_ID_FOR_SERVICE:
        logger.error("Global Gmail operator identity is not configured")
        raise HTTPException(status_code=503, detail="Global Gmail operator is not configured")

    try:
        global_owner_id = UUID(AGENT_USER_ID_FOR_SERVICE)
    except (TypeError, ValueError):
        logger.error("Global Gmail operator identity is invalid")
        raise HTTPException(status_code=503, detail="Global Gmail operator is not configured")

    if user_info["user_id"] != global_owner_id:
        logger.warning("Denied global Gmail operator request from a non-owner")
        raise HTTPException(status_code=403, detail="Not authorized for global Gmail operations")

    return user_info

async def get_active_gmail_service(
    gmail_service = Depends(get_gmail_service_instance)
):
    """
    Dependency that retrieves the globally initialized Gmail service.
    Raises HTTPException if the service is not available (e.g., failed initialization).
    """
    if gmail_service is None:
        logger.error(
            "Attempted to use Gmail service, but it's not initialized. "
            "This likely means AGENT_USER_ID_FOR_SERVICE was not set or token fetch failed at startup."
        )
        raise HTTPException(
            status_code=503, # Service Unavailable
            detail="Gmail service is not available. Please check server logs for initialization errors."
        )
    return gmail_service

@router.post("/notifications")
async def receive_gmail_notification(
    request: Request, 
    background_tasks: BackgroundTasks,
    gmail_service = Depends(get_active_gmail_service) # Use the robust dependency
):
    """
    Endpoint to receive Gmail push notifications from Pub/Sub.
    These notifications are for the globally configured AGENT_USER_ID_FOR_SERVICE.
    """
    try:
        payload = await request.json()
        logger.info(f"Received /mail/notifications payload for processing.") # Avoid logging full payload by default if sensitive
        
        notification_data = extract_and_decode_message(payload)
        
        if notification_data:
            logger.info(f"Valid notification data extracted. Adding to background tasks for email processing.")
            # process_email_notification is async, suitable for background_tasks
            background_tasks.add_task(
                process_email_notification, 
                notification_data, 
                gmail_service # Pass the active service
            )
            return {"status": "processing_initiated", "detail": "Email notification processing started in background."}
        
        logger.info("No actionable data in notification payload after extraction.")
        return {"status": "no_action_needed", "detail": "Notification received but no message data to process."}
    
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in notification payload: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}")
    except Exception as e:
        logger.exception(f"Error processing /mail/notifications: {e}")
        raise HTTPException(status_code=500, detail="Internal server error processing notification.")

@router.get("/agent/health", response_model=GlobalGmailHealthResponse)
async def health_check(
    _operator: dict = Depends(require_global_gmail_operator),
):
    """
    Health check endpoint. Checks if the global Gmail service instance is initialized.
    """
    # Directly get the instance to report status without failing if it's None
    gmail_service = get_gmail_service_instance() 
    is_healthy = gmail_service is not None
    
    status_message = "Gmail service is connected and operational." if is_healthy \
                     else "Gmail service is not initialized or unavailable. Check server startup logs."
    
    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "detail": status_message,
        "gmail_service_status": "connected" if is_healthy else "disconnected"
    }

@router.post("/renew-watch")
async def renew_watch(
    _operator: dict = Depends(require_global_gmail_operator),
    gmail_service = Depends(get_active_gmail_service),
):
    """
    Manually renew the Gmail watch for the globally configured AGENT_USER_ID_FOR_SERVICE.
    """
    try:
        logger.info("Attempting to manually renew Gmail watch...")
        watch_response = await setup_gmail_watch(gmail_service)
        
        if watch_response and watch_response.get('expiration'):
            expiration_time = watch_response.get('expiration')
            logger.info(f"Gmail watch renewed successfully. New expiration (epoch ms): {expiration_time}")
            return {"status": "success", "message": "Gmail watch renewed successfully.", "expiration_epoch_ms": expiration_time}
        else:
            logger.error(f"Failed to renew watch. Response from setup_gmail_watch: {watch_response}")
            raise HTTPException(status_code=500, detail="Failed to renew Gmail watch. Check server logs.")
    except HttpError as e:
        logger.exception(f"Google API HttpError renewing watch: {e.resp.status} - {e.content}")
        raise HTTPException(status_code=e.resp.status, detail=f"Google API error during watch renewal: {e.content.decode() if isinstance(e.content, bytes) else e.content}")
    except Exception as e:
        logger.exception(f"Unexpected error renewing Gmail watch: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while renewing watch: {str(e)}")
    
@router.post("/agent/check-inbox")
async def check_inbox(
    background_tasks: BackgroundTasks,
    _operator: dict = Depends(require_global_gmail_operator),
    gmail_service = Depends(get_active_gmail_service),
):
    """
    Manually check for unread emails for AGENT_USER_ID_FOR_SERVICE and process them.
    """
    try:
        logger.info("Manually checking inbox for unread emails...")
        request = gmail_service.users().messages().list(
            userId='me',
            q='is:unread in:inbox -category:promotions -category:social -from:noreply',
            maxResults=10,
        )
        results = await execute_google_request(request)
        
        messages = results.get('messages', [])
        
        if not messages:
            logger.info("No unread emails found during manual check.")
            return {"status": "success", "message": "No unread emails found matching criteria."}
        
        logger.info(f"Found {len(messages)} unread emails. Adding to background processing.")
        processed_ids = []
        for message_summary in messages:
            message_id = message_summary['id']
            processed_ids.append(message_id)
            # fetch_and_process_email is async and is awaited by BackgroundTasks
            background_tasks.add_task(
                fetch_and_process_email,
                gmail_service, # Pass the active service
                message_id
            )
        
        return {
            "status": "processing_initiated", 
            "message": f"Processing {len(messages)} unread emails in the background.",
            "processed_message_ids_queued": processed_ids
        }
    
    except HttpError as e:
        logger.exception(f"Google API HttpError checking inbox: {e.resp.status} - {e.content}")
        raise HTTPException(status_code=e.resp.status, detail=f"Google API error: {e.content.decode() if isinstance(e.content, bytes) else e.content}")
    except Exception as e:
        logger.exception(f"Error checking inbox: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while checking inbox: {str(e)}")