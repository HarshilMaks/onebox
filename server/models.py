from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.declarative import declarative_base


Base = declarative_base()


class AgentToken(Base):
    __tablename__ = "onebox_tokens"

    user_id = Column(PG_UUID(as_uuid=True), primary_key=True)
    # Legacy migrations created this column nullable. It remains a display-only
    # compatibility field while pending/reconnect rows exist; normalized Google
    # identity below is the authoritative sender/account context.
    user_email = Column(String, nullable=True)
    # Temporary dual-read storage. New and refreshed credentials are encrypted
    # and clear this field; a later contraction migration will remove it.
    token_json = Column(JSON, nullable=True)
    encrypted_token_payload = Column(Text, nullable=True)
    credential_key_id = Column(String(128), nullable=True)
    credential_format_version = Column(String(16), nullable=True)
    google_email_normalized = Column(String, nullable=True, index=True)
    connection_status = Column(String(32), nullable=False, default="reconnect_required")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PendingAction(Base):
    """An owner-approved, immutable request for an external side effect."""

    __tablename__ = "pending_actions"
    __table_args__ = (
        Index("ix_pending_actions_user_status", "user_id", "status"),
        UniqueConstraint("user_id", "command_key", name="uq_pending_actions_user_command_key"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'succeeded', 'failed', 'rejected', 'expired', "
            "'reconciliation_required')",
            name="ck_pending_actions_status",
        ),
        CheckConstraint(
            "status <> 'processing' OR (attempt_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_pending_actions_processing_attempt",
        ),
    )

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    action_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    # Kept during the expand phase so old deployments/rollback tooling can
    # inspect historic values.  It is no longer an intent identity or unique.
    idempotency_key = Column(String(64), nullable=False)
    command_key = Column(String(128), nullable=False)
    summary = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    result = Column(JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    attempt_token = Column(String(128), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    attempt_started_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_reason = Column(String(128), nullable=True)
    reconciliation_evidence = Column(JSON, nullable=True)
    reconciliation_required_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)


class PendingActionAuditEvent(Base):
    """Append-only audited state transition/evidence record for an action."""

    __tablename__ = "pending_action_audit_events"
    __table_args__ = (Index("ix_pending_action_audit_events_action_created", "action_id", "created_at"),)

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    action_id = Column(PG_UUID(as_uuid=True), ForeignKey("pending_actions.id", ondelete="CASCADE"), nullable=False)
    actor_user_id = Column(PG_UUID(as_uuid=True), nullable=True)
    event_type = Column(String(64), nullable=False)
    old_status = Column(String(32), nullable=True)
    new_status = Column(String(32), nullable=False)
    attempt_token = Column(String(128), nullable=True)
    reason = Column(String(128), nullable=True)
    evidence = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
