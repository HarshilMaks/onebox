"""Canonical payload hashing for durable side-effect idempotency."""
import hashlib
import json
from typing import Any, Mapping


def canonical_payload(payload: Mapping[str, Any]) -> str:
    """Serialize an action payload deterministically before it is persisted."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_idempotency_key(user_id: str, action_type: str, payload: Mapping[str, Any]) -> str:
    """Return a per-user key for one exact, immutable action payload."""
    canonical = canonical_payload(payload)
    material = f"{user_id}:{action_type}:{canonical}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def make_payload_hash(payload: Mapping[str, Any]) -> str:
    """Return the stable hash displayed with an action for audit/debugging."""
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()
