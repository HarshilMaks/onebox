# server/setup_google.py
import json
from functools import lru_cache
import logging
from uuid import UUID # <-- Import UUID again
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build, Resource
from typing import Dict, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt

from server.models import AgentToken
from server.database import get_agent_db
from server.config import settings

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

def build_credentials(token_info: dict) -> Credentials:
    creds = Credentials.from_authorized_user_info(token_info, _SCOPES)
    if creds.expired and creds.refresh_token:
        logger.info("Refreshing expired credentials for user")
        creds.refresh(Request())
    return creds

@lru_cache()
def _gmail_builder():
    return build

security = HTTPBearer()

def get_current_user_info(
    creds: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, str]:  # Change return type to Dict
    """
    Extracts and validates the JWT from the Authorization header
    and returns a dictionary containing user ID and email.
    """
    try:
        payload = jwt.decode(
            creds.credentials,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )

        auth_user_id_str = payload.get('sub')
        email = payload.get('email')

        if auth_user_id_str is None:
            logger.warning("JWT payload missing 'sub' claim")
            raise HTTPException(status_code=401, detail="Could not validate credentials (user ID missing)")

        if email is None:
            logger.warning("JWT payload missing 'email' claim")
            raise HTTPException(status_code=401, detail="Could not validate credentials (email missing)")

        try:
            auth_user_id = UUID(auth_user_id_str)
        except ValueError:
            logger.warning(f"JWT 'sub' claim is not a valid UUID: {auth_user_id_str}")
            raise HTTPException(status_code=401, detail="Could not validate credentials (invalid user ID format)")

        return {"user_id": auth_user_id, "email": email}  # Return as a dictionary

    except jwt.ExpiredSignatureError:
        logger.warning("JWT expired")
        raise HTTPException(status_code=401, detail="Token expired. Please log in again.")
    except jwt.JWTError as e:
        logger.error(f"Invalid JWT token: {e}")
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    except Exception as e:
        logger.exception("Error decoding/validating JWT")
        raise HTTPException(status_code=500, detail="Internal server error during authentication")
    
    
async def get_agent_token_row(
    user_info: dict = Depends(get_current_user_info),  # <-- Expecting a dictionary now
    db: AsyncSession = Depends(get_agent_db)
) -> AgentToken:
    """
    Retrieves the AgentToken row for the given auth user ID (UUID).
    """
    user_id = user_info["user_id"]  # <-- Access 'user_id' from the dictionary
    email = user_info["email"]  # <-- Access 'email' from the dictionary
    
    row = await db.get(AgentToken, user_id)
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
