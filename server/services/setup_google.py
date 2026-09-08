# server/setup_google.py
import json
from functools import lru_cache
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build, Resource
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import AuthenticatedPrincipal
from server.config import settings
from server.database import get_agent_db
from server.models import AgentToken

logger = logging.getLogger(__name__)

# Scopes
_SCOPES = [
    "https://mail.google.com/",  
     "https://www.googleapis.com/auth/gmail.modify",  # Add this line# Read, compose, send, modify Gmail
    "https://www.googleapis.com/auth/calendar",       # Read/write access to Calendars
    "https://www.googleapis.com/auth/tasks" ,    
    "https://www.googleapis.com/auth/userinfo.email",  # <--- REQUIRED!!!
    "openid"# Read/write access to Tasks
]

@lru_cache()
def get_client_config():
    with open(settings.GOOGLE_OAUTH_CLIENT_SECRETS, 'r') as f:
        return json.load(f)

def build_credentials(token_info: dict, refresh_if_expired: bool = True) -> Credentials:
    creds = Credentials.from_authorized_user_info(token_info, _SCOPES)
    if refresh_if_expired and creds.expired and creds.refresh_token:
        logger.info("Refreshing expired credentials for user")
        creds.refresh(Request())
    return creds

@lru_cache()
def _gmail_builder():
    return build

security = HTTPBearer(auto_error=False)
_AUTHENTICATION_ERROR_DETAIL = "Could not validate credentials"


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_AUTHENTICATION_ERROR_DETAIL,
    )


def get_current_user_info(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
) -> AuthenticatedPrincipal:
    """Return a verified, immutable application principal for a bearer JWT."""
    if creds is None or not isinstance(creds.credentials, str) or not creds.credentials.strip():
        raise _authentication_error()

    try:
        payload = jwt.decode(
            creds.credentials,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require_exp": True},
        )
        return AuthenticatedPrincipal.from_verified_claims(payload)
    except HTTPException:
        raise
    except (jwt.ExpiredSignatureError, JWTError, TypeError, ValueError):
        logger.warning("JWT authentication failed")
        raise _authentication_error() from None
    except Exception:
        logger.exception("Unexpected JWT authentication failure")
        raise _authentication_error() from None


async def get_agent_token_row(
    principal: AuthenticatedPrincipal = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_agent_db),
) -> AgentToken:
    """Retrieve the agent credential row for the authenticated principal."""
    row = await db.get(AgentToken, principal.user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Agent credentials not found for user")
    
    return row

async def get_gmail_service(
    token_row: AgentToken = Depends(get_agent_token_row)
) -> Resource:
    creds = build_credentials(token_row.token_json)
    try:
        service = _gmail_builder()('gmail', 'v1', credentials=creds)
        return service
    except Exception as e:
        logger.exception(f"Failed to build Gmail service: {e}")
        raise HTTPException(status_code=500, detail="Could not initialize Gmail service")

async def get_calendar_service(
    token_row: AgentToken = Depends(get_agent_token_row)
) -> Resource:
    creds = build_credentials(token_row.token_json)
    try:
        service = _gmail_builder()('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        logger.exception(f"Failed to build Calendar service: {e}")
        raise HTTPException(status_code=500, detail="Could not initialize Calendar service")

async def get_tasks_service(
    token_row: AgentToken = Depends(get_agent_token_row)
) -> Resource:
    creds = build_credentials(token_row.token_json)
    try:
        service = _gmail_builder()('tasks', 'v1', credentials=creds)
        return service
    except Exception as e:
        logger.exception(f"Failed to build Tasks service: {e}")
        raise HTTPException(status_code=500, detail="Could not initialize Tasks service")

async def get_user_email(
    gmail_service: Resource = Depends(get_gmail_service)
) -> str:
    try:
        profile = gmail_service.users().getProfile(userId='me').execute()
        return profile.get('emailAddress')
    except Exception as e:
        logger.exception(f"Error fetching user email: {e}")
        raise HTTPException(status_code=500, detail="Could not retrieve user email")
