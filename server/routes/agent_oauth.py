# server/routes/agent_oauth.py
from fastapi import APIRouter, Depends, HTTPException, status
from google_auth_oauthlib.flow import Flow
from typing import Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID # <-- Import UUID again
import json
import logging
from fastapi.responses import RedirectResponse # Import RedirectResponse
from googleapiclient.errors import HttpError as GoogleHttpError
from server.config import settings
from server.services.setup_google import get_client_config, _SCOPES, get_current_user_info
from server.database import get_agent_db
from server.models import AgentToken
from server.logging_config import setup_logging
from urllib.parse import urlencode # Import urlencode for building redirect URL
setup_logging()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent auth"])


@router.get("/oauth/start")
async def start_oauth(
    user_info: dict = Depends(get_current_user_info),
):
    """Start the OAuth flow with user's email pre-filled to enforce identity."""
    user_id = user_info["user_id"]  
    email = user_info["email"] 

    logger.info(f"Initiating Google OAuth flow for user_id: {user_id} (email: {email})")

    state = str(user_id)
    final_redirect_uri = str(settings.OAUTH_REDIRECT_URI)

    flow = Flow.from_client_config(
        get_client_config(),
        scopes=_SCOPES,
        redirect_uri=final_redirect_uri
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=state,
        prompt="consent",
        login_hint=email  # <- This enforces the email hint!
    )

    return {"authorization_url": auth_url, "state": state}


@router.get("/oauth/callback")
async def oauth_callback(
    code: str,
    state: str,
    scope: str = None,
    db: AsyncSession = Depends(get_agent_db)
):
    """
    Handles the callback from Google OAuth. Exchanges code for tokens and saves them.
    Redirects to the frontend callback URI on success or failure.
    """
    logger.info(f"Received callback with code: {code[:5]}... state: {state}, scope: {scope}")

    try:
        user_id = UUID(state)
    except ValueError:
        logger.error(f"Invalid state parameter received from Google: {state}")
        error_params = {"status": "failure", "error_message": "Invalid state parameter"}
        redirect_url = f"{settings.FRONTEND_OAUTH_CALLBACK_URI}?{urlencode(error_params)}"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

    logger.info(f"Handling Google OAuth callback for user_id: {user_id}")

    try:
        flow = Flow.from_client_config(
            get_client_config(),
            scopes=_SCOPES,
            redirect_uri=str(settings.OAUTH_REDIRECT_URI)
        )

        flow.fetch_token(code=code)
        creds = flow.credentials
        token_dict = json.loads(creds.to_json())

        # ---- NEW: Fetch user info (especially email) from Google ----
        from googleapiclient.discovery import build
        userinfo_service = build('oauth2', 'v2', credentials=creds)
        userinfo = userinfo_service.userinfo().get().execute()
        user_email = userinfo.get("email")

        if not user_email:
            raise HTTPException(status_code=400, detail="Failed to fetch user email from Google.")

        logger.info(f"Fetched user_email from Google: {user_email}")

        # ---- Save or Update database ----
        async with db.begin():
            existing = await db.get(AgentToken, user_id)
            if existing:
                logger.info(f"Updating existing AgentToken for user_id: {user_id}")
                existing.token_json = token_dict
                existing.user_email = user_email  # <-- Update email
                if creds.refresh_token:
                    token_dict['refresh_token'] = creds.refresh_token
                    existing.token_json = token_dict
            else:
                logger.info(f"Creating new AgentToken for user_id: {user_id}")
                new_token = AgentToken(
                    user_id=user_id,
                    user_email=user_email,  # <-- Save email
                    token_json=token_dict
                )
                db.add(new_token)
            await db.commit()  # <-- this is the fix!

        # Success redirect
        success_params = {"status": "success", "user_id": str(user_id)}
        redirect_url = f"{settings.FRONTEND_OAUTH_CALLBACK_URI}?{urlencode(success_params)}"
        logger.info(f"OAuth callback success, redirecting to frontend: {redirect_url}")
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

    except Exception as e:
        logger.exception(f"Unexpected error during Google OAuth callback for user_id {user_id}: {str(e)}")
        error_params = {"status": "failure", "error_message": "Authentication failed"}
        redirect_url = f"{settings.FRONTEND_OAUTH_CALLBACK_URI}?{urlencode(error_params)}"
        logger.error(f"OAuth callback failed, redirecting to frontend: {redirect_url}")
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
    
    
# --- NEW ROUTE TO DEMONSTRATE CREATING A RECORD WITH TOKEN USER ID ---
@router.post("/verify_and_create_entry")
async def verify_and_create_agent_entry(
    user_info: dict = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_agent_db)
):
    user_id = user_info["user_id"]  
    email = user_info["email"] 

    logger.info(f"Attempting to create entry for user_id: {user_id}, email: {email} based on JWT.")

    existing_entry = await db.get(AgentToken, user_id)

    if existing_entry:
        logger.info(f"Entry already exists for user_id: {user_id}")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Entry already exists for user_id: {user_id}")

    logger.info(f"Creating new entry for user_id: {user_id}")
    new_entry = AgentToken(
        user_id=user_id,
        user_email=email,  # <-- Use real email from JWT, not dummy
        token_json={"status": "pending_oauth"}
    )
    db.add(new_entry)
    await db.commit()
    await db.refresh(new_entry)

    logger.info(f"Successfully created entry for user_id: {user_id}")
    return {"message": "Entry created successfully", "user_id": str(user_id), "email": email}


@router.get("/status")
async def get_agent_status(
    user_info: dict = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_agent_db)
):
    user_id = user_info["user_id"]  
    email = user_info["email"] 

    logger.info(f"Checking agent status for user_id: {user_id}, email: {email}")
    try:
        agent_token = await db.get(AgentToken, user_id)

        if agent_token and agent_token.token_json.get("refresh_token"):
            logger.info(f"Agent connected for user_id: {user_id}")
            return {"user_id": str(user_id), "email": email, "is_gmail_connected": True, "status": "connected"}
        else:
            logger.info(f"Agent not connected for user_id: {user_id}")
            return {"user_id": str(user_id), "email": email, "is_gmail_connected": False, "status": "not_connected"}

    except Exception as e:
        logger.exception(f"Error checking agent status for user_id {user_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to check agent status: {e}")