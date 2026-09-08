"""Centralized lifecycle for persisted per-user Google OAuth credentials."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import Resource, build
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.models import AgentToken
from server.oauth_state import normalize_email
from server.security.oauth_credentials import CredentialCodecError, OAuthCredentialCodec

logger = logging.getLogger(__name__)

GOOGLE_OAUTH_SCOPES = (
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
)
_CONNECTION_PENDING = "pending"
_CONNECTION_CONNECTED = "connected"
_CONNECTION_RECONNECT_REQUIRED = "reconnect_required"
_CONNECTION_QUARANTINED = "quarantined"


class CredentialLifecycleError(RuntimeError):
    """Base class for safe credential lifecycle failures."""


class CredentialEncryptionUnavailable(CredentialLifecycleError):
    pass


class GoogleReconnectRequired(CredentialLifecycleError):
    pass


class GoogleCredentialsUnavailable(CredentialLifecycleError):
    pass


class OAuthExchangeFailed(CredentialLifecycleError):
    pass


class OAuthAccountMismatch(CredentialLifecycleError):
    pass


class OAuthConnectionAlreadyExists(CredentialLifecycleError):
    pass


@dataclass(frozen=True)
class GoogleConnection:
    """A verified connected account with credentials safe to build named clients from."""

    user_id: UUID
    google_email: str
    credentials: Credentials


@dataclass(frozen=True)
class OAuthExchangeResult:
    """A verified OAuth exchange result without logging/returning raw provider details."""

    google_email: str
    normalized_google_email: str
    token_payload: dict[str, Any]


@lru_cache
def get_client_config() -> dict[str, Any]:
    """Load OAuth client metadata once from the configured read-only mount."""
    with settings.GOOGLE_OAUTH_CLIENT_SECRETS.open("r", encoding="utf-8") as client_file:
        loaded = json.load(client_file)
    if not isinstance(loaded, dict):
        raise ValueError("OAuth client configuration must be a JSON object")
    return loaded


@lru_cache
def _load_codec(keyring_path: str, active_key_id: str) -> OAuthCredentialCodec:
    return OAuthCredentialCodec.from_keyring_file(Path(keyring_path), active_key_id)


async def get_credential_codec() -> OAuthCredentialCodec:
    """Load the configured keyring off the event loop, failing closed when absent."""
    if not settings.oauth_token_encryption_configured:
        raise CredentialEncryptionUnavailable()
    assert settings.OAUTH_TOKEN_KEYRING_PATH is not None
    assert settings.OAUTH_TOKEN_ACTIVE_KEY_ID is not None
    try:
        return await asyncio.to_thread(
            _load_codec,
            str(settings.OAUTH_TOKEN_KEYRING_PATH),
            settings.OAUTH_TOKEN_ACTIVE_KEY_ID,
        )
    except CredentialCodecError as exc:
        raise CredentialEncryptionUnavailable() from exc


def merge_oauth_token_payload(
    existing_token: Mapping[str, Any] | None,
    returned_token: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve an existing refresh token only when Google omits a replacement."""
    if not isinstance(returned_token, Mapping):
        raise GoogleReconnectRequired()
    merged = dict(returned_token)
    existing_refresh_token = (
        existing_token.get("refresh_token")
        if isinstance(existing_token, Mapping)
        else None
    )
    if not merged.get("refresh_token") and isinstance(existing_refresh_token, str):
        merged["refresh_token"] = existing_refresh_token
    return merged


def _granted_scopes(token_payload: Mapping[str, Any]) -> set[str]:
    raw_scopes = token_payload.get("scopes", token_payload.get("scope"))
    if isinstance(raw_scopes, str):
        return set(raw_scopes.split())
    if isinstance(raw_scopes, (list, tuple)) and all(isinstance(scope, str) for scope in raw_scopes):
        return set(raw_scopes)
    return set()


def _validate_token_payload(token_payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(token_payload, Mapping):
        raise GoogleReconnectRequired()
    payload = dict(token_payload)
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise GoogleReconnectRequired()

    scopes = _granted_scopes(payload)
    required_scope_groups = (
        {"https://mail.google.com/", "https://www.googleapis.com/auth/gmail.modify"},
        {"https://www.googleapis.com/auth/calendar"},
        {"https://www.googleapis.com/auth/tasks"},
        {"https://www.googleapis.com/auth/userinfo.email"},
        {"openid"},
    )
    if any(not (scopes & accepted_scopes) for accepted_scopes in required_scope_groups):
        raise GoogleReconnectRequired()
    return payload


def _credentials_from_payload(token_payload: Mapping[str, Any]) -> Credentials:
    return Credentials.from_authorized_user_info(dict(token_payload), GOOGLE_OAUTH_SCOPES)


def _legacy_email(row: AgentToken) -> str | None:
    if row.google_email_normalized:
        try:
            return normalize_email(row.google_email_normalized)
        except ValueError:
            return None
    if row.user_email:
        try:
            return normalize_email(row.user_email)
        except ValueError:
            return None
    return None


def _read_token_payload(
    row: AgentToken,
    codec: OAuthCredentialCodec,
    normalized_google_email: str,
) -> tuple[dict[str, Any], bool]:
    if row.encrypted_token_payload:
        try:
            payload = codec.decrypt(
                row.encrypted_token_payload,
                key_id=row.credential_key_id or "",
                format_version=row.credential_format_version or "",
                user_id=row.user_id,
                normalized_google_email=normalized_google_email,
            )
        except CredentialCodecError as exc:
            raise GoogleReconnectRequired() from exc
        return _validate_token_payload(payload), False

    if row.token_json:
        return _validate_token_payload(row.token_json), True
    raise GoogleReconnectRequired()


def _write_encrypted_token(
    row: AgentToken,
    codec: OAuthCredentialCodec,
    token_payload: Mapping[str, Any],
    normalized_google_email: str,
) -> None:
    encrypted = codec.encrypt(
        token_payload,
        user_id=row.user_id,
        normalized_google_email=normalized_google_email,
    )
    row.encrypted_token_payload = encrypted.ciphertext
    row.credential_key_id = encrypted.key_id
    row.credential_format_version = encrypted.format_version
    row.google_email_normalized = normalized_google_email
    row.connection_status = _CONNECTION_CONNECTED
    row.token_json = None


def _mark_reconnect_required(row: AgentToken) -> None:
    row.connection_status = _CONNECTION_RECONNECT_REQUIRED


async def load_connected_google_connection(
    user_id: UUID,
    db: AsyncSession,
) -> GoogleConnection:
    """Load, validate, refresh once under a row lock, and persist encrypted tokens."""
    codec = await get_credential_codec()
    reconnect_required = False
    unavailable = False
    connection: GoogleConnection | None = None

    async with db.begin():
        row = await db.scalar(
            select(AgentToken).where(AgentToken.user_id == user_id).with_for_update()
        )
        if row is None or row.connection_status != _CONNECTION_CONNECTED:
            reconnect_required = True
        else:
            normalized_google_email = _legacy_email(row)
            if normalized_google_email is None:
                _mark_reconnect_required(row)
                reconnect_required = True
            else:
                try:
                    token_payload, used_legacy_payload = _read_token_payload(
                        row, codec, normalized_google_email
                    )
                    credentials = _credentials_from_payload(token_payload)
                except (GoogleReconnectRequired, ValueError, TypeError):
                    _mark_reconnect_required(row)
                    reconnect_required = True
                else:
                    refreshed = False
                    if credentials.expired:
                        try:
                            await asyncio.to_thread(credentials.refresh, Request())
                            token_payload = _validate_token_payload(
                                merge_oauth_token_payload(
                                    token_payload,
                                    json.loads(credentials.to_json()),
                                )
                            )
                            credentials = _credentials_from_payload(token_payload)
                            refreshed = True
                        except RefreshError:
                            _mark_reconnect_required(row)
                            reconnect_required = True
                        except (GoogleReconnectRequired, ValueError, TypeError, json.JSONDecodeError):
                            _mark_reconnect_required(row)
                            reconnect_required = True
                        except Exception:
                            unavailable = True
                    if not reconnect_required and not unavailable:
                        if used_legacy_payload or refreshed:
                            _write_encrypted_token(
                                row, codec, token_payload, normalized_google_email
                            )
                        connection = GoogleConnection(
                            user_id=row.user_id,
                            google_email=normalized_google_email,
                            credentials=credentials,
                        )

    if reconnect_required:
        raise GoogleReconnectRequired()
    if unavailable or connection is None:
        raise GoogleCredentialsUnavailable()
    return connection


def _build_oauth_authorization_url(state: str, login_hint: str) -> str:
    flow = Flow.from_client_config(
        get_client_config(),
        scopes=GOOGLE_OAUTH_SCOPES,
        redirect_uri=str(settings.OAUTH_REDIRECT_URI),
    )
    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=state,
        prompt="consent",
        login_hint=login_hint,
    )
    return authorization_url


async def create_oauth_authorization_url(state: str, login_hint: str) -> str:
    return await asyncio.to_thread(_build_oauth_authorization_url, state, login_hint)


def _exchange_oauth_code(code: str) -> OAuthExchangeResult:
    flow = Flow.from_client_config(
        get_client_config(),
        scopes=GOOGLE_OAUTH_SCOPES,
        redirect_uri=str(settings.OAUTH_REDIRECT_URI),
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials
    token_payload = _validate_token_payload(json.loads(credentials.to_json()))
    userinfo_service = build("oauth2", "v2", credentials=credentials)
    returned_email = userinfo_service.userinfo().get().execute().get("email")
    normalized_google_email = normalize_email(returned_email)
    return OAuthExchangeResult(
        google_email=returned_email.strip(),
        normalized_google_email=normalized_google_email,
        token_payload=token_payload,
    )


async def exchange_oauth_code(code: str) -> OAuthExchangeResult:
    """Exchange the code and obtain verified Google identity off the event loop."""
    try:
        return await asyncio.to_thread(_exchange_oauth_code, code)
    except Exception as exc:
        logger.warning("Google OAuth exchange failed")
        raise OAuthExchangeFailed() from exc


async def persist_oauth_connection(
    db: AsyncSession,
    *,
    user_id: UUID,
    expected_google_email: str,
    exchange: OAuthExchangeResult,
) -> None:
    """Atomically persist a verified OAuth result as encrypted new-write storage."""
    if exchange.normalized_google_email != expected_google_email:
        raise OAuthAccountMismatch()
    codec = await get_credential_codec()

    async with db.begin():
        await db.execute(
            insert(AgentToken)
            .values(
                user_id=user_id,
                user_email=None,
                token_json=None,
                connection_status=_CONNECTION_PENDING,
            )
            .on_conflict_do_nothing(index_elements=[AgentToken.user_id])
        )
        row = await db.scalar(
            select(AgentToken).where(AgentToken.user_id == user_id).with_for_update()
        )
        if row is None:
            raise GoogleCredentialsUnavailable()

        existing_email = _legacy_email(row)
        if existing_email is not None and existing_email != exchange.normalized_google_email:
            raise OAuthAccountMismatch()

        existing_payload: Mapping[str, Any] | None = None
        if row.encrypted_token_payload:
            try:
                existing_payload, _ = _read_token_payload(
                    row, codec, exchange.normalized_google_email
                )
            except GoogleReconnectRequired:
                if not exchange.token_payload.get("refresh_token"):
                    raise
        elif row.token_json:
            try:
                existing_payload = _validate_token_payload(row.token_json)
            except GoogleReconnectRequired:
                existing_payload = None

        token_payload = _validate_token_payload(
            merge_oauth_token_payload(existing_payload, exchange.token_payload)
        )
        row.user_email = exchange.google_email
        _write_encrypted_token(row, codec, token_payload, exchange.normalized_google_email)


async def create_pending_oauth_connection(db: AsyncSession, user_id: UUID) -> None:
    """Create an explicit pending OAuth state without fake credential JSON."""
    async with db.begin():
        result = await db.execute(
            insert(AgentToken)
            .values(
                user_id=user_id,
                user_email=None,
                token_json=None,
                connection_status=_CONNECTION_PENDING,
            )
            .on_conflict_do_nothing(index_elements=[AgentToken.user_id])
        )
        if result.rowcount != 1:
            raise OAuthConnectionAlreadyExists()


async def build_google_api_service(
    connection: GoogleConnection,
    service_name: str,
    version: str,
) -> Resource:
    """Build exactly one named Google client off the event loop."""
    try:
        return await asyncio.to_thread(
            build,
            service_name,
            version,
            credentials=connection.credentials,
        )
    except Exception as exc:
        logger.warning("Google API client construction failed for %s", service_name)
        raise GoogleCredentialsUnavailable() from exc
