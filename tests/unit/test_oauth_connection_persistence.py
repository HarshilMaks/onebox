import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from server.security.oauth_credentials import OAuthCredentialCodec
from server.services import credentials


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def begin(self):
        return _Transaction()

    async def execute(self, statement):
        self.executed.append(statement)
        return SimpleNamespace(rowcount=1)

    async def scalar(self, _statement):
        return self.row


def _codec() -> OAuthCredentialCodec:
    return OAuthCredentialCodec(keyring={"active": b"a" * 32}, active_key_id="active")


def _token_payload(refresh_token="refresh-token") -> dict:
    return {
        "token": "access-token",
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": list(credentials.GOOGLE_OAUTH_SCOPES),
    }


def _row(user_id, *, email=None, token_json=None):
    return SimpleNamespace(
        user_id=user_id,
        user_email=email,
        google_email_normalized=email.casefold() if email else None,
        token_json=token_json,
        encrypted_token_payload=None,
        credential_key_id=None,
        credential_format_version=None,
        connection_status="pending",
    )


def test_oauth_persistence_writes_encrypted_payload_and_preserves_omitted_refresh_token(monkeypatch):
    user_id = uuid4()
    row = _row(user_id, email="Owner@Example.com", token_json=_token_payload("existing-refresh"))
    session = FakeSession(row)

    async def get_codec():
        return _codec()

    monkeypatch.setattr(credentials, "get_credential_codec", get_codec)
    exchange = credentials.OAuthExchangeResult(
        google_email="Owner@Example.com",
        normalized_google_email="owner@example.com",
        token_payload=_token_payload(refresh_token=""),
    )

    asyncio.run(
        credentials.persist_oauth_connection(
            session,
            user_id=user_id,
            expected_google_email="owner@example.com",
            exchange=exchange,
        )
    )

    assert row.connection_status == "connected"
    assert row.google_email_normalized == "owner@example.com"
    assert row.token_json is None
    assert row.encrypted_token_payload
    decrypted = _codec().decrypt(
        row.encrypted_token_payload,
        key_id=row.credential_key_id,
        format_version=row.credential_format_version,
        user_id=user_id,
        normalized_google_email="owner@example.com",
    )
    assert decrypted["refresh_token"] == "existing-refresh"


def test_oauth_persistence_rejects_selected_account_mismatch_before_overwrite(monkeypatch):
    user_id = uuid4()
    row = _row(user_id, email="other@example.com", token_json=_token_payload())
    session = FakeSession(row)

    async def get_codec():
        return _codec()

    monkeypatch.setattr(credentials, "get_credential_codec", get_codec)
    exchange = credentials.OAuthExchangeResult(
        google_email="Owner@Example.com",
        normalized_google_email="owner@example.com",
        token_payload=_token_payload(),
    )

    with pytest.raises(credentials.OAuthAccountMismatch):
        asyncio.run(
            credentials.persist_oauth_connection(
                session,
                user_id=user_id,
                expected_google_email="owner@example.com",
                exchange=exchange,
            )
        )

    assert row.token_json["refresh_token"] == "refresh-token"
    assert row.encrypted_token_payload is None
