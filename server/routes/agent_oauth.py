import logging
from uuid import UUID
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.database import get_agent_db
from server.models import AgentToken
from server.oauth_state import OAuthStateStoreUnavailable, consume_oauth_state, create_oauth_state
from server.schemas import AgentStatusResponse, OAuthStartResponse, VerifyAndCreateEntryResponse
from server.services.credentials import (
    OAuthAccountMismatch,
    OAuthConnectionAlreadyExists,
    OAuthExchangeFailed,
    create_oauth_authorization_url,
    create_pending_oauth_connection,
    exchange_oauth_code,
    persist_oauth_connection,
)
from server.services.setup_google import get_current_user_info

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["Agent auth"])


class _OAuthAccountMismatch(Exception):
    pass


def _frontend_redirect(status_value: str, error_message: str | None = None, user_id: UUID | None = None) -> RedirectResponse:
    params = {"status": status_value}
    if error_message:
        params["error_message"] = error_message
    if user_id:
        params["user_id"] = str(user_id)
    return RedirectResponse(
        url=f"{settings.FRONTEND_OAUTH_CALLBACK_URI}?{urlencode(params)}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/oauth/start", response_model=OAuthStartResponse)
async def start_oauth(user_info: dict = Depends(get_current_user_info)):
    """Start a one-time OAuth flow bound to the authenticated user and email."""
    user_id = user_info["user_id"]
    try:
        state = await create_oauth_state(str(user_id), str(user_info["email"]))
    except (OAuthStateStoreUnavailable, ValueError):
        logger.error("Unable to create OAuth state for user %s", user_id)
        raise HTTPException(status_code=503, detail="OAuth is temporarily unavailable. Please try again.")

    try:
        auth_url = await create_oauth_authorization_url(state, str(user_info["email"]))
    except Exception:
        logger.error("Unable to create OAuth authorization URL for user %s", user_id)
        raise HTTPException(status_code=503, detail="OAuth is temporarily unavailable. Please try again.")
    return {"authorization_url": auth_url, "state": state}


@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(..., min_length=1, max_length=4_096),
    state: str = Query(..., min_length=1, max_length=512),
    scope: str | None = Query(default=None, max_length=2_048),
    db: AsyncSession = Depends(get_agent_db),
):
    """Exchange a one-time, owner-bound OAuth callback for stored credentials."""
    del scope  # Google may return this query parameter; it is not trusted input.
    try:
        state_binding = await consume_oauth_state(state)
    except OAuthStateStoreUnavailable:
        logger.error("OAuth state store unavailable during callback")
        return _frontend_redirect("failure", "OAuth is temporarily unavailable. Please try again.")

    if state_binding is None:
        logger.warning("OAuth callback had an invalid, expired, or already-used state")
        return _frontend_redirect("failure", "Invalid or expired OAuth state")

    try:
        user_id = UUID(state_binding.user_id)
    except ValueError:
        logger.error("OAuth state binding contained an invalid user identifier")
        return _frontend_redirect("failure", "Invalid OAuth state")

    try:
        exchange = await exchange_oauth_code(code)
        if exchange.normalized_google_email != state_binding.expected_email:
            logger.warning("OAuth account did not match the initiating identity for user %s", user_id)
            return _frontend_redirect(
                "failure",
                "The selected Google account does not match the signed-in account.",
            )

        try:
            await persist_oauth_connection(
                db,
                user_id=user_id,
                expected_google_email=state_binding.expected_email,
                exchange=exchange,
            )
        except OAuthAccountMismatch:
            logger.warning("OAuth account did not match the existing connection for user %s", user_id)
            return _frontend_redirect(
                "failure",
                "This Google account does not match the account already connected.",
            )

        return _frontend_redirect("success", user_id=user_id)
    except OAuthExchangeFailed:
        logger.warning("OAuth callback exchange failed for user %s", user_id)
        return _frontend_redirect("failure", "Authentication failed")
    except Exception:
        logger.exception("OAuth callback failed for user %s", user_id)
        return _frontend_redirect("failure", "Authentication failed")


@router.post("/verify_and_create_entry", response_model=VerifyAndCreateEntryResponse)
async def verify_and_create_agent_entry(
    user_info: dict = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_agent_db),
):
    """Create an optional pending token row for the authenticated user."""
    user_id = user_info["user_id"]
    email = str(user_info["email"])
    try:
        await create_pending_oauth_connection(db, user_id)
    except OAuthConnectionAlreadyExists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Entry already exists")
    return {"message": "Entry created successfully", "user_id": str(user_id), "email": email}


@router.get("/status", response_model=AgentStatusResponse)
async def get_agent_status(
    user_info: dict = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_agent_db),
):
    user_id = user_info["user_id"]
    agent_token = await db.get(AgentToken, user_id)
    is_connected = bool(agent_token and agent_token.connection_status == "connected")
    return {
        "user_id": str(user_id),
        "email": str(user_info["email"]),
        "is_gmail_connected": is_connected,
        "status": "connected" if is_connected else "not_connected",
    }
