"""Stable request and response schemas exposed by the OneBox HTTP API."""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class AgentStreamEventType(str, Enum):
    """Terminal and non-terminal event names emitted in agent SSE frames."""

    TOKEN = "token"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    DONE = "done"


class PendingActionType(str, Enum):
    """Provider effects that are always represented by a pending action."""

    SEND_EMAIL = "send_email"
    SEND_REPLY = "send_reply"
    CREATE_EVENT = "create_event"
    CREATE_TASK = "create_task"


class PendingActionStatus(str, Enum):
    """Persisted pending-action lifecycle states."""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class MailMutationStatus(str, Enum):
    MARKED_READ = "marked as read"
    MARKED_UNREAD = "marked as unread"
    MOVED_TO_TRASH = "moved to trash"
    RESTORED_FROM_TRASH = "restored from trash"
    PERMANENTLY_DELETED = "permanently deleted"
    STARRED = "starred"
    UNSTARRED = "unstarred"


class MailMutationAction(str, Enum):
    SET = "set"


class SendEmailStatus(str, Enum):
    SENT = "sent"


class SaveDraftStatus(str, Enum):
    SAVED = "draft saved"
    UPDATED = "draft updated"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"


class GlobalGmailHealthStatus(str, Enum):
    UNAVAILABLE = "unavailable"
    HEALTHY = "healthy"
    DEGRADED = "degraded"


class GlobalGmailServiceStatus(str, Enum):
    UNAVAILABLE = "unavailable"
    READY = "ready"
    DEGRADED = "degraded"


class InboxCheckStatus(str, Enum):
    CHECKED = "checked"


class RootStatus(str, Enum):
    OK = "ok"


class AutomationAvailability(str, Enum):
    DURABLE_WORKER_ENABLED = "durable_worker_enabled"
    DISABLED = "disabled"


class AgentConnectionStatus(str, Enum):
    CONNECTED = "connected"
    NOT_CONNECTED = "not_connected"


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


class AgentSuccessResponse(BaseModel):
    """Standard envelope for a successful non-streaming agent response."""

    result: str


class AgentStreamEvent(BaseModel):
    """One JSON payload emitted in a general-agent SSE data frame.

    ``error_code`` is present only for a terminal ``error`` event and is safe
    for client-side branching. ``content`` is always safe to display.
    """

    event: AgentStreamEventType
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
    status: MailMutationStatus
    action: Optional[MailMutationAction] = None


class StarStateUpdate(BaseModel):
    """Desired final star state for an idempotent Gmail mutation."""

    starred: bool


class SendEmailResponse(BaseModel):
    id: Optional[str] = None
    status: SendEmailStatus


class SaveDraftResponse(BaseModel):
    id: str
    status: SaveDraftStatus
    draft_id: str


class HealthResponse(BaseModel):
    status: HealthStatus


class GlobalGmailHealthResponse(BaseModel):
    status: GlobalGmailHealthStatus
    detail: str
    gmail_service_status: GlobalGmailServiceStatus


class CheckInboxResponse(BaseModel):
    status: InboxCheckStatus
    inbox_message_count_estimate: int


class ReadinessResponse(BaseModel):
    status: RootStatus
    global_gmail_service: AutomationAvailability


class OAuthStartResponse(BaseModel):
    authorization_url: str
    state: str


class AgentStatusResponse(BaseModel):
    user_id: str
    email: str
    is_gmail_connected: bool
    status: AgentConnectionStatus


class VerifyAndCreateEntryResponse(BaseModel):
    message: str
    user_id: str
    email: str


class PendingActionResponse(BaseModel):
    """Immutable, owner-bound external action awaiting or reflecting approval."""

    id: UUID
    action_type: PendingActionType
    payload: dict
    payload_hash: str
    summary: str
    status: PendingActionStatus
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
