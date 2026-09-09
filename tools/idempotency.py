"""Canonical payload hashing and command-record keys for durable side effects."""
import hashlib
import json
from typing import Any, Mapping


def canonical_payload(payload: Mapping[str, Any]) -> str:
    """Serialize an approved action payload deterministically for integrity checks."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_command_record_key(user_id: str, command_key: str) -> str:
    """Hash an owner-scoped server command key for the legacy compatibility column.

    This is deliberately independent of payload content: a new command key may
    request the same side effect later, while one reused key is deduplicated by
    the `(user_id, command_key)` database constraint.
    """
    material = f"{user_id}:{command_key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def make_payload_hash(payload: Mapping[str, Any]) -> str:
    """Return the stable hash used to verify immutable approved payloads."""
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()
