from types import SimpleNamespace
from uuid import uuid4

from server.commands.backfill_oauth_credentials import backfill_legacy_row
from server.security.oauth_credentials import OAuthCredentialCodec
from server.services.credentials import GOOGLE_OAUTH_SCOPES


def _codec() -> OAuthCredentialCodec:
    return OAuthCredentialCodec(keyring={"active": b"a" * 32}, active_key_id="active")


def _legacy_row(*, user_email="Owner@Example.com", token_json=None):
    return SimpleNamespace(
        user_id=uuid4(),
        user_email=user_email,
        google_email_normalized=None,
        token_json=token_json,
        encrypted_token_payload=None,
        credential_key_id=None,
        credential_format_version=None,
        connection_status="connected",
    )


def _valid_payload() -> dict:
    return {
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": list(GOOGLE_OAUTH_SCOPES),
    }


def test_backfill_encrypts_valid_legacy_row_and_removes_plaintext():
    row = _legacy_row(token_json=_valid_payload())

    result = backfill_legacy_row(row, _codec())

    assert result == "encrypted"
    assert row.connection_status == "connected"
    assert row.google_email_normalized == "owner@example.com"
    assert row.encrypted_token_payload
    assert row.credential_key_id == "active"
    assert row.credential_format_version == "v1"
    assert row.token_json is None


def test_backfill_converts_legacy_pending_sentinel_without_retaining_fake_credential_json():
    row = _legacy_row(token_json={"status": "pending_oauth"})

    assert backfill_legacy_row(row, _codec()) == "pending"
    assert row.connection_status == "pending"
    assert row.token_json is None
    assert row.encrypted_token_payload is None


def test_backfill_quarantines_rows_without_a_valid_identity_or_credential_payload():
    null_email = _legacy_row(user_email=None, token_json=_valid_payload())
    malformed = _legacy_row(token_json={"refresh_token": "refresh-token", "scopes": []})

    assert backfill_legacy_row(null_email, _codec()) == "quarantined"
    assert null_email.connection_status == "quarantined"
    assert null_email.token_json is None

    assert backfill_legacy_row(malformed, _codec()) == "quarantined"
    assert malformed.connection_status == "quarantined"
    assert malformed.token_json is None
