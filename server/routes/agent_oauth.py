import json
import logging
from uuid import UUID
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.database import get_agent_db
from server.logging_config import setup_logging
from server.models import AgentToken
from server.oauth_state import (
    OAuthStateStoreUnavailable,
    consume_oauth_state,
    create_oauth_state,
    merge_oauth_token_payload,
    normalize_email,
)
from server.schemas import AgentStatusResponse, OAuthStartResponse, VerifyAndCreateEntryResponse
from server.services.setup_google import _SCOPES, get_client_config, get_current_user_info

setup_logging()
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
        state = create_oauth_state(str(user_id), str(user_info["email"]))
    except (OAuthStateStoreUnavailable, ValueError):
        logger.error("Unable to create OAuth state for user %s", user_id)
        raise HTTPException(status_code=503, detail="OAuth is temporarily unavailable. Please try again.")

    flow = Flow.from_client_config(
        get_client_config(),
        scopes=_SCOPES,
        redirect_uri=str(settings.OAUTH_REDIRECT_URI),
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=state,
        prompt="consent",
        login_hint=str(user_info["email"]),
    )
    return {"authorization_url": auth_url, "state": state}


@router.get("/oauth/callback")
async def oauth_callback(
    code: str,
    state: str,
    scope: str | None = None,
    db: AsyncSession = Depends(get_agent_db),
):
    """Exchange a one-time, owner-bound OAuth callback for stored credentials."""
    del scope  # Google may return this query parameter; it is not trusted input.
    try:
        state_binding = consume_oauth_state(state)
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
        flow = Flow.from_client_config(
            get_client_config(),
            scopes=_SCOPES,
            redirect_uri=str(settings.OAUTH_REDIRECT_URI),
        )
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_dict = json.loads(creds.to_json())

        userinfo_service = build("oauth2", "v2", credentials=creds)
        returned_email = userinfo_service.userinfo().get().execute().get("email")
        if not returned_email:
            return _frontend_redirect("failure", "Failed to verify the selected Google account")

        normalized_returned_email = normalize_email(returned_email)
        if normalized_returned_email != state_binding.expected_email:
            logger.warning("OAuth account did not match the initiating identity for user %s", user_id)
            return _frontend_redirect(
                "failure",
                "The selected Google account does not match the signed-in account.",
            )

        try:
            async with db.begin():
                existing = await db.get(AgentToken, user_id)
                if existing and existing.user_email:
                    if normalize_email(existing.user_email) != normalized_returned_email:
                        raise _OAuthAccountMismatch()

                if existing:
                    existing.token_json = merge_oauth_token_payload(
                        existing.token_json,
                        token_dict,
                    )
                    existing.user_email = returned_email.strip()
                else:
                    db.add(
                        AgentToken(
                            user_id=user_id,
                            user_email=returned_email.strip(),
                            token_json=token_dict,
                        )
                    )
        except _OAuthAccountMismatch:
            logger.warning("OAuth account did not match the existing connection for user %s", user_id)
            return _frontend_redirect(
                "failure",
                "This Google account does not match the account already connected.",
            )

        return _frontend_redirect("success", user_id=user_id)
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
    existing_entry = await db.get(AgentToken, user_id)
    if existing_entry:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Entry already exists")

    db.add(
        AgentToken(
            user_id=user_id,
            user_email=email,
            token_json={"status": "pending_oauth"},
        )
    )
    await db.commit()
    return {"message": "Entry created successfully", "user_id": str(user_id), "email": email}


@router.get("/status", response_model=AgentStatusResponse)
async def get_agent_status(
    user_info: dict = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_agent_db),
):
    user_id = user_info["user_id"]
    agent_token = await db.get(AgentToken, user_id)
    is_connected = bool(agent_token and agent_token.token_json.get("refresh_token"))
    return {
        "user_id": str(user_id),
        "email": str(user_info["email"]),
        "is_gmail_connected": is_connected,
        "status": "connected" if is_connected else "not_connected",
    }
