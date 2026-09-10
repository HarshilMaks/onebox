# server/schemas.py
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class EmailListItem(BaseModel):
    """Summary data returned by inbox, folder, and search endpoints."""

    id: str
    threadId: Optional[str] = None
    subject: str
    sender: str
    to: List[str] = Field(default_factory=list)
    snippet: str = ""
    is_read: bool
    is_starred: bool
    labels: List[str] = Field(default_factory=list)
    date: Optional[str] = None


class EmailDetail(EmailListItem):
    """Complete mail detail with plain text and optional sanitized HTML.

    ``body`` is always text. Clients may render ``sanitized_html`` only when
    they intentionally opt into the strict server-side sanitization policy.
    """

    cc: List[str] = Field(default_factory=list)
    body: str
    sanitized_html: Optional[str] = None


class EmailDraft(BaseModel):
    to: List[EmailStr] = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20_000)
    draft_id: Optional[str] = Field(default=None, min_length=1, max_length=256)


class EmailPage(BaseModel):
    emails: List[EmailListItem]
    next_page_token: Optional[str] = None


class OAuthCallback(BaseModel):
    code: str = Field(min_length=1, max_length=4_096)
    state: str | None = Field(default=None, min_length=1, max_length=512)


class TokenInfo(BaseModel):
    access_token: str
    refresh_token: str
    scope: str
    token_type: str
    expires_in: int


class AgentTokenOut(BaseModel):
    user_id: UUID
    token: TokenInfo
    updated_at: datetime


class AgentSuccessResponse(BaseModel):
    """Standard envelope for a successful non-streaming agent response."""

    result: str


class AgentStreamEvent(BaseModel):
    """One JSON payload emitted in a general-agent SSE data frame.

    ``error_code`` is present only for a terminal ``error`` event and is safe
    for client-side branching. ``content`` is always safe to display.
    """

    event: str
    content: str
    error_code: Optional[str] = None


class AgentErrorResponse(BaseModel):
    """Standard envelope for failed agent requests."""

    error: str
    detail: str


class PublicErrorResponse(AgentErrorResponse):
    """Stable safe HTTP error envelope shared by all routes."""


class MailMutationResponse(BaseModel):
    """Stable response for a single-email mutation."""

    id: str
    status: str
    action: Optional[str] = None


class StarStateUpdate(BaseModel):
    """Desired final star state for an idempotent Gmail mutation."""

    starred: bool


class SendEmailResponse(BaseModel):
    id: Optional[str] = None
    status: str


class SaveDraftResponse(BaseModel):
    id: str
    status: str
    draft_id: str


class HealthResponse(BaseModel):
    status: str


class GlobalGmailHealthResponse(BaseModel):
    status: str
    detail: str
    gmail_service_status: str


class CheckInboxResponse(BaseModel):
    status: str
    inbox_message_count_estimate: int


class ReadinessResponse(BaseModel):
    status: str
    global_gmail_service: str


class OAuthStartResponse(BaseModel):
    authorization_url: str
    state: str


class AgentStatusResponse(BaseModel):
    user_id: str
    email: str
    is_gmail_connected: bool
    status: str


class VerifyAndCreateEntryResponse(BaseModel):
    message: str
    user_id: str
    email: str


class PendingActionResponse(BaseModel):
    """Immutable, owner-bound external action awaiting or reflecting approval."""

    id: UUID
    action_type: str
    payload: dict
    payload_hash: str
    summary: str
    status: str
    result: Optional[dict] = None
    error_code: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    approved_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    attempt_count: int = 0
    lease_expires_at: Optional[datetime] = None
    reconciliation_reason: Optional[str] = None
    reconciliation_required_at: Optional[datetime] = None
