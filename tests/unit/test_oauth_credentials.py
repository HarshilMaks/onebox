import base64
from uuid import uuid4

import pytest

from server.security.oauth_credentials import CredentialCodecError, OAuthCredentialCodec


def _key(seed: bytes) -> bytes:
    return (seed * 32)[:32]


def _payload() -> dict:
    return {
        "token": "access-token-sentinel",
        "refresh_token": "refresh-token-sentinel",
        "scopes": ["openid"],
    }


def test_encrypt_decrypt_round_trip_uses_unique_nonces_and_safe_metadata():
    codec = OAuthCredentialCodec(keyring={"active": _key(b"a")}, active_key_id="active")
    user_id = uuid4()

    first = codec.encrypt(_payload(), user_id=user_id, normalized_google_email="owner@example.com")
    second = codec.encrypt(_payload(), user_id=user_id, normalized_google_email="owner@example.com")

    assert first.key_id == "active"
    assert first.format_version == "v1"
    assert first.ciphertext != second.ciphertext
    assert codec.decrypt(
        first.ciphertext,
        key_id=first.key_id,
        format_version=first.format_version,
        user_id=user_id,
        normalized_google_email="owner@example.com",
    ) == _payload()


@pytest.mark.parametrize(
    ("user_id", "email"),
    [
        (uuid4(), "owner@example.com"),
        (None, "other@example.com"),
    ],
)
def test_ciphertext_cannot_be_moved_between_users_or_google_accounts(user_id, email):
    codec = OAuthCredentialCodec(keyring={"active": _key(b"a")}, active_key_id="active")
    owner_id = uuid4()
    encrypted = codec.encrypt(_payload(), user_id=owner_id, normalized_google_email="owner@example.com")

    with pytest.raises(CredentialCodecError) as exc_info:
        codec.decrypt(
            encrypted.ciphertext,
            key_id=encrypted.key_id,
            format_version=encrypted.format_version,
            user_id=owner_id if user_id is None else user_id,
            normalized_google_email=email,
        )

    message = str(exc_info.value)
    assert "refresh-token-sentinel" not in message
    assert encrypted.ciphertext not in message


def test_unknown_key_and_key_rotation_fail_safely_then_reencrypt_with_active_key():
    user_id = uuid4()
    old_codec = OAuthCredentialCodec(keyring={"old": _key(b"o")}, active_key_id="old")
    encrypted = old_codec.encrypt(_payload(), user_id=user_id, normalized_google_email="owner@example.com")

    rotated_codec = OAuthCredentialCodec(
        keyring={"old": _key(b"o"), "new": _key(b"n")}, active_key_id="new"
    )
    decrypted = rotated_codec.decrypt(
        encrypted.ciphertext,
        key_id="old",
        format_version="v1",
        user_id=user_id,
        normalized_google_email="owner@example.com",
    )
    rewritten = rotated_codec.encrypt(
        decrypted,
        user_id=user_id,
        normalized_google_email="owner@example.com",
    )

    assert rewritten.key_id == "new"
    with pytest.raises(CredentialCodecError):
        OAuthCredentialCodec(keyring={"new": _key(b"n")}, active_key_id="new").decrypt(
            encrypted.ciphertext,
            key_id="old",
            format_version="v1",
            user_id=user_id,
            normalized_google_email="owner@example.com",
        )


def test_keyring_file_rejects_invalid_key_material_without_echoing_it(tmp_path):
    keyring_path = tmp_path / "oauth-keyring.json"
    bad_material = base64.urlsafe_b64encode(b"too-short").decode("ascii")
    keyring_path.write_text('{"active": "' + bad_material + '"}', encoding="utf-8")

    with pytest.raises(CredentialCodecError) as exc_info:
        OAuthCredentialCodec.from_keyring_file(keyring_path, "active")

    assert bad_material not in str(exc_info.value)
