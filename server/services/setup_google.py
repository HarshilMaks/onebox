"""JWT authentication and thin FastAPI dependencies for Google providers."""

import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from googleapiclient.discovery import Resource
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth import AuthenticatedPrincipal
from server.config import settings
from server.database import get_agent_db
from server.services.credentials import (
    CredentialEncryptionUnavailable,
    GoogleConnection,
    GoogleCredentialsUnavailable,
    GoogleReconnectRequired,
    build_google_api_service,
    load_connected_google_connection,
)

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)
_AUTHENTICATION_ERROR_DETAIL = "Could not validate credentials"
_RECONNECT_REQUIRED_DETAIL = "Google account reconnection is required"
_CREDENTIALS_UNAVAILABLE_DETAIL = "Google credentials are temporarily unavailable"


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


def _credential_http_error(error: Exception) -> HTTPException:
    if isinstance(error, GoogleReconnectRequired):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_RECONNECT_REQUIRED_DETAIL)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_CREDENTIALS_UNAVAILABLE_DETAIL,
    )


async def get_connected_google_connection(
    principal: AuthenticatedPrincipal = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_agent_db),
) -> GoogleConnection:
    """Resolve one persisted Google account once per FastAPI request."""
    try:
        return await load_connected_google_connection(principal.user_id, db)
    except (
        CredentialEncryptionUnavailable,
        GoogleReconnectRequired,
        GoogleCredentialsUnavailable,
    ) as exc:
        raise _credential_http_error(exc) from None


async def _build_service(
    connection: GoogleConnection,
    service_name: str,
    version: str,
) -> Resource:
    try:
        return await build_google_api_service(connection, service_name, version)
    except GoogleCredentialsUnavailable as exc:
        raise _credential_http_error(exc) from None


async def get_gmail_service(
    connection: GoogleConnection = Depends(get_connected_google_connection),
) -> Resource:
    return await _build_service(connection, "gmail", "v1")


async def get_calendar_service(
    connection: GoogleConnection = Depends(get_connected_google_connection),
) -> Resource:
    return await _build_service(connection, "calendar", "v3")


async def get_tasks_service(
    connection: GoogleConnection = Depends(get_connected_google_connection),
) -> Resource:
    return await _build_service(connection, "tasks", "v1")
