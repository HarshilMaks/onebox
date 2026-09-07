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
from server.oauth_state import create_oauth_state, consume_oauth_state
from server.schemas import AgentStatusResponse, OAuthStartResponse, VerifyAndCreateEntryResponse
from server.logging_config import setup_logging
from urllib.parse import urlencode # Import urlencode for building redirect URL
setup_logging()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent auth"])


class _OAuthAccountMismatch(Exception):
    """Raised when the Google account returned by OAuth doesn't match
    the account already connected for this user."""

    def __init__(self, existing_email: str, new_email: str):
        super().__init__(f"{existing_email} != {new_email}")
        self.existing_email = existing_email
        self.new_email = new_email


@router.get("/oauth/start", response_model=OAuthStartResponse)
async def start_oauth(
    user_info: dict = Depends(get_current_user_info),
):
    """Start the OAuth flow with user's email pre-filled to enforce identity."""
    user_id = user_info["user_id"]  
    email = user_info["email"] 

    logger.info(f"Initiating Google OAuth flow for user_id: {user_id} (email: {email})")

    # `state` is a random, single-use token bound server-side to this
    # user_id (see server/oauth_state.py). It must NOT be derived from
    # user_id directly, since the callback is public and a predictable
    # state would let an attacker bind their own Google account to a
    # different user's row.
    state = create_oauth_state(str(user_id))
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
        login_hint=email  # Hints the account in Google's UI; not an identity guarantee.
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

    # Resolve the authenticated user_id from the one-time state token
    # created in /oauth/start. This callback has no JWT of its own, so
    # the state token is the only trustworthy link back to who started
    # the flow. A missing/expired/reused state is rejected outright.
    state_user_id = consume_oauth_state(state)
    if state_user_id is None:
        logger.error(f"OAuth state not found, expired, or already used: {state}")
        error_params = {"status": "failure", "error_message": "Invalid or expired OAuth state"}
        redirect_url = f"{settings.FRONTEND_OAUTH_CALLBACK_URI}?{urlencode(error_params)}"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

    try:
        user_id = UUID(state_user_id)
    except ValueError:
        logger.error(f"Stored OAuth state resolved to an invalid user_id: {state_user_id}")
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

        # ---- Fetch user info (especially email) from Google ----
        from googleapiclient.discovery import build
        userinfo_service = build('oauth2', 'v2', credentials=creds)
        userinfo = userinfo_service.userinfo().get().execute()
        user_email = userinfo.get("email")

        if not user_email:
            raise HTTPException(status_code=400, detail="Failed to fetch user email from Google.")

        logger.info(f"Fetched user_email from Google: {user_email}")

        # ---- Save or Update database ----
        try:
            async with db.begin():
                existing = await db.get(AgentToken, user_id)

                # Account-ownership check: if this user already has a
                # connected Google account, the account returned by this
                # OAuth flow must match it. This stops a user (or an
                # attacker who obtained a valid state) from silently
                # re-pointing an existing row at a different Google account.
                if existing and existing.user_email and existing.user_email != user_email:
                    raise _OAuthAccountMismatch(existing.user_email, user_email)

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
        except _OAuthAccountMismatch as mismatch:
            logger.error(
                f"OAuth account mismatch for user_id {user_id}: "
                f"existing account {mismatch.existing_email} != returned account {mismatch.new_email}"
            )
            error_params = {
                "status": "failure",
                "error_message": "This Google account does not match the account already connected.",
            }
            redirect_url = f"{settings.FRONTEND_OAUTH_CALLBACK_URI}?{urlencode(error_params)}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

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
@router.post("/verify_and_create_entry", response_model=VerifyAndCreateEntryResponse)
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


@router.get("/status", response_model=AgentStatusResponse)
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