"""Add durable pending actions for server-authorized external effects.

Revision ID: 620d1f8b1e34
Revises: 20025ef7fbcc
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "620d1f8b1e34"
down_revision: Union[str, None] = "20025ef7fbcc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_pending_actions_user_id", "pending_actions", ["user_id"], unique=False)
    op.create_index("ix_pending_actions_user_status", "pending_actions", ["user_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_pending_actions_user_status", table_name="pending_actions")
    op.drop_index("ix_pending_actions_user_id", table_name="pending_actions")
    op.drop_table("pending_actions")
