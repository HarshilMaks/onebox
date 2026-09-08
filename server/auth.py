"""Immutable authenticated-principal types and claim validation."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID


def _freeze_claim_value(value: Any) -> Any:
    """Recursively freeze decoded JWT claim values before exposing them."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_claim_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_claim_value(item) for item in value)
    return value


def _normalize_email(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("JWT email claim must be a string")
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError("JWT email claim must not be blank")
    return normalized


def _parse_subject(value: object) -> UUID:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("JWT sub claim must be a nonblank UUID string")
    try:
        return UUID(value.strip())
    except ValueError as exc:
        raise ValueError("JWT sub claim must be a UUID") from exc


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Verified application identity exposed to authenticated dependencies."""

    user_id: UUID
    email: str
    claims: Mapping[str, Any]

    @classmethod
    def from_verified_claims(cls, claims: Mapping[str, Any]) -> "AuthenticatedPrincipal":
        return cls(
            user_id=_parse_subject(claims.get("sub")),
            email=_normalize_email(claims.get("email")),
            claims=_freeze_claim_value(claims),
        )

    def __getitem__(self, key: str) -> UUID | str:
        """Provide narrow compatibility for existing route keyed accesses."""
        if key == "user_id":
            return self.user_id
        if key == "email":
            return self.email
        raise KeyError(key)
