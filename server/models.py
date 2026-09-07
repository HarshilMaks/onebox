from uuid import uuid4

from sqlalchemy import Column, DateTime, Index, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.declarative import declarative_base


Base = declarative_base()


class AgentToken(Base):
    __tablename__ = "onebox_tokens"

    user_id = Column(PG_UUID(as_uuid=True), primary_key=True)
    user_email = Column(String, nullable=False)
    token_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PendingAction(Base):
    """An owner-approved, immutable request for an external side effect."""

    __tablename__ = "pending_actions"
    __table_args__ = (
        Index("ix_pending_actions_user_status", "user_id", "status"),
    )

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    action_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(64), nullable=False, unique=True)
    summary = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    result = Column(JSON, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
