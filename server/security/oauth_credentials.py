"""Versioned authenticated encryption for persisted Google OAuth credentials."""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_CREDENTIAL_AAD_DOMAIN = b"onebox.oauth-credentials"
_NONCE_BYTES = 12
_KEY_BYTES = 32
FORMAT_VERSION = "v1"


class CredentialCodecError(ValueError):
    """A safe failure while loading, encrypting, or decrypting credentials."""


@dataclass(frozen=True)
class EncryptedCredentialPayload:
    """Database-ready encrypted credential fields with no plaintext representation."""

    ciphertext: str
    key_id: str
    format_version: str = FORMAT_VERSION


def _b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise CredentialCodecError("OAuth credential key material is invalid") from exc


def _load_keyring(path: Path) -> dict[str, bytes]:
    try:
        raw_keyring = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CredentialCodecError("OAuth credential keyring is unavailable") from exc

    if not isinstance(raw_keyring, dict) or not raw_keyring:
        raise CredentialCodecError("OAuth credential keyring is invalid")

    keyring: dict[str, bytes] = {}
    for key_id, encoded_key in raw_keyring.items():
        if not isinstance(key_id, str) or not key_id.strip() or not isinstance(encoded_key, str):
            raise CredentialCodecError("OAuth credential keyring is invalid")
        key = _b64decode(encoded_key)
        if len(key) != _KEY_BYTES:
            raise CredentialCodecError("OAuth credential key material is invalid")
        keyring[key_id] = key
    return keyring


def _canonical_payload(payload: Mapping[str, Any]) -> bytes:
    if not isinstance(payload, Mapping):
        raise CredentialCodecError("OAuth credential payload is invalid")
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CredentialCodecError("OAuth credential payload is invalid") from exc


def _aad(user_id: UUID, normalized_google_email: str, format_version: str) -> bytes:
    return b"\x00".join(
        (
            _CREDENTIAL_AAD_DOMAIN,
            format_version.encode("ascii"),
            str(user_id).encode("ascii"),
            normalized_google_email.encode("utf-8"),
        )
    )


class OAuthCredentialCodec:
    """Encrypt/decrypt OAuth payloads using AES-256-GCM with row-bound AAD."""

    def __init__(self, *, keyring: Mapping[str, bytes], active_key_id: str):
        if active_key_id not in keyring:
            raise CredentialCodecError("OAuth credential active key is unavailable")
        if any(not isinstance(key, bytes) or len(key) != _KEY_BYTES for key in keyring.values()):
            raise CredentialCodecError("OAuth credential key material is invalid")
        self._keyring = dict(keyring)
        self._active_key_id = active_key_id

    @classmethod
    def from_keyring_file(cls, path: Path, active_key_id: str) -> "OAuthCredentialCodec":
        return cls(keyring=_load_keyring(path), active_key_id=active_key_id)

    def encrypt(
        self,
        payload: Mapping[str, Any],
        *,
        user_id: UUID,
        normalized_google_email: str,
    ) -> EncryptedCredentialPayload:
        plaintext = _canonical_payload(payload)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(self._keyring[self._active_key_id]).encrypt(
            nonce,
            plaintext,
            _aad(user_id, normalized_google_email, FORMAT_VERSION),
        )
        return EncryptedCredentialPayload(
            ciphertext=base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii"),
            key_id=self._active_key_id,
        )

    def decrypt(
        self,
        ciphertext: str,
        *,
        key_id: str,
        format_version: str,
        user_id: UUID,
        normalized_google_email: str,
    ) -> dict[str, Any]:
        if format_version != FORMAT_VERSION or key_id not in self._keyring or not isinstance(ciphertext, str):
            raise CredentialCodecError("OAuth credential payload is unavailable")
        try:
            encrypted = base64.b64decode(
                ciphertext.encode("ascii"), altchars=b"-_", validate=True
            )
            if len(encrypted) <= _NONCE_BYTES:
                raise ValueError("missing ciphertext")
            plaintext = AESGCM(self._keyring[key_id]).decrypt(
                encrypted[:_NONCE_BYTES],
                encrypted[_NONCE_BYTES:],
                _aad(user_id, normalized_google_email, format_version),
            )
            payload = json.loads(plaintext)
        except (UnicodeEncodeError, binascii.Error, InvalidTag, ValueError, json.JSONDecodeError) as exc:
            raise CredentialCodecError("OAuth credential payload is unavailable") from exc
        if not isinstance(payload, dict):
            raise CredentialCodecError("OAuth credential payload is invalid")
        return payload
