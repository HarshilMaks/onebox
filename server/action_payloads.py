"""Validated, versioned payloads for durable pending actions.

Payloads are persisted as JSON and are therefore both the approval display and
execution contract.  These models deliberately reject unknown keys so a model
or caller cannot smuggle mutable execution instructions into an approved
command.
"""
from __future__ import annotations

import re
from datetime import datetime
from email.utils import parseaddr
from typing import Annotated, Any, Literal, Mapping, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PAYLOAD_SCHEMA_VERSION = 1
_MAX_TEXT_LENGTH = 20_000
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def _clean_text(value: str, field_name: str, *, maximum: int = _MAX_TEXT_LENGTH) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be blank")
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field_name} is invalid")
    return value


def _canonical_email(value: str) -> str:
    value = _clean_text(value, "email", maximum=320)
    _display_name, address = parseaddr(value)
    if address != value or address.count("@") != 1 or any(char.isspace() for char in address):
        raise ValueError("must be a plain, valid email address")
    local, domain = address.rsplit("@", 1)
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("must be a valid email address")
    return address.casefold()


def _provider_id(value: str) -> str:
    value = _clean_text(value, "provider ID", maximum=256)
    if not _PROVIDER_ID_RE.fullmatch(value):
        raise ValueError("provider ID contains unsupported characters")
    return value


def _rfc_message_id(value: str) -> str:
    value = _clean_text(value, "Message-ID", maximum=998)
    if not (value.startswith("<") and value.endswith(">")) or any(char.isspace() for char in value):
        raise ValueError("Message-ID must be an RFC-style bracketed identifier")
    return value


class ActionPayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[PAYLOAD_SCHEMA_VERSION] = PAYLOAD_SCHEMA_VERSION


class SendEmailPayload(ActionPayloadBase):
    action_type: Literal["send_email"]
    sender_email: str
    recipient_email: str
    subject: str = Field(max_length=998)
    email_body: str = Field(max_length=_MAX_TEXT_LENGTH)

    _sender = field_validator("sender_email")(_canonical_email)
    _recipient = field_validator("recipient_email")(_canonical_email)

    @field_validator("subject", "email_body")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _clean_text(value, info.field_name, maximum=998 if info.field_name == "subject" else _MAX_TEXT_LENGTH)


class SendReplyPayload(ActionPayloadBase):
    """A reply target fully resolved before the user approves it."""

    action_type: Literal["send_reply"]
    sender_email: str
    # This is the actual Reply-To/From target resolved from Gmail, not an LLM query.
    recipient_email: str
    original_message_id: str
    thread_id: str
    original_rfc_message_id: str
    original_references: str = Field(default="", max_length=4_000)
    original_subject: str = Field(max_length=998)
    reply_message: str = Field(max_length=_MAX_TEXT_LENGTH)

    _sender = field_validator("sender_email")(_canonical_email)
    _recipient = field_validator("recipient_email")(_canonical_email)
    _message_id = field_validator("original_message_id")(_provider_id)
    _thread_id = field_validator("thread_id")(_provider_id)
    _rfc_id = field_validator("original_rfc_message_id")(_rfc_message_id)

    @field_validator("original_subject", "reply_message")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _clean_text(value, info.field_name, maximum=998 if info.field_name == "original_subject" else _MAX_TEXT_LENGTH)

    @field_validator("original_references")
    @classmethod
    def validate_references(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("original_references is invalid")
        return value.strip()


class CreateEventPayload(ActionPayloadBase):
    action_type: Literal["create_event"]
    title: str = Field(max_length=1_024)
    start_time_iso: datetime
    end_time_iso: datetime
    event_timezone: str = Field(max_length=128)
    description: str = Field(default="", max_length=_MAX_TEXT_LENGTH)
    location: str = Field(default="", max_length=1_024)
    attendee_emails: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _clean_text(value, "title", maximum=1_024)

    @field_validator("description", "location")
    @classmethod
    def validate_optional_text(cls, value: str, info) -> str:
        if "\x00" in value:
            raise ValueError(f"{info.field_name} is invalid")
        return value.strip()

    @field_validator("event_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = _clean_text(value, "event_timezone", maximum=128)
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("event_timezone must be an IANA timezone") from exc
        return value

    @field_validator("attendee_emails")
    @classmethod
    def validate_attendees(cls, values: list[str]) -> list[str]:
        canonical = sorted({_canonical_email(value) for value in values})
        if len(canonical) != len(values):
            raise ValueError("attendee_emails must not contain duplicates")
        return canonical

    @model_validator(mode="after")
    def validate_interval(self) -> "CreateEventPayload":
        for field_name, value in (("start_time_iso", self.start_time_iso), ("end_time_iso", self.end_time_iso)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must include a UTC offset")
        if self.end_time_iso <= self.start_time_iso:
            raise ValueError("end_time_iso must be after start_time_iso")
        return self


class CreateTaskPayload(ActionPayloadBase):
    action_type: Literal["create_task"]
    title: str = Field(max_length=1_024)
    notes: str = Field(max_length=_MAX_TEXT_LENGTH)

    @field_validator("title", "notes")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        return _clean_text(value, info.field_name, maximum=1_024 if info.field_name == "title" else _MAX_TEXT_LENGTH)


ActionPayload = Annotated[
    Union[SendEmailPayload, SendReplyPayload, CreateEventPayload, CreateTaskPayload],
    Field(discriminator="action_type"),
]
_PAYLOAD_MODELS: dict[str, type[ActionPayloadBase]] = {
    "send_email": SendEmailPayload,
    "send_reply": SendReplyPayload,
    "create_event": CreateEventPayload,
    "create_task": CreateTaskPayload,
}


def canonicalize_action_payload(action_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return the exact JSON representation that is approved/executed."""
    model = _PAYLOAD_MODELS.get(action_type)
    if model is None:
        raise ValueError(f"Unsupported pending action type: {action_type}")
    candidate = dict(payload)
    # The action type is server-owned. It is included in the JSON discriminator
    # rather than trusting an LLM-provided field.
    supplied_type = candidate.pop("action_type", action_type)
    if supplied_type != action_type:
        raise ValueError("payload action_type does not match action type")
    return model(action_type=action_type, **candidate).model_dump(mode="json")
