"""Make pending actions recoverable commands with reconciliation evidence.

Revision ID: b4c6d8e0f2a4
Revises: 8f8a10d8c4f1
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b4c6d8e0f2a4"
down_revision: Union[str, None] = "8f8a10d8c4f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTION_STATES = "'pending', 'processing', 'succeeded', 'failed', 'rejected', 'expired', 'reconciliation_required'"


def upgrade() -> None:
    # Expand before constraining so existing actions keep a stable, explicitly
    # legacy command identity.  Historic processing actions are not replayed.
    op.add_column("pending_actions", sa.Column("command_key", sa.String(length=128), nullable=True))
    op.add_column("pending_actions", sa.Column("attempt_token", sa.String(length=128), nullable=True))
    op.add_column(
        "pending_actions",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("pending_actions", sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pending_actions", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("pending_actions", sa.Column("reconciliation_reason", sa.String(length=128), nullable=True))
    op.add_column("pending_actions", sa.Column("reconciliation_evidence", sa.JSON(), nullable=True))
    op.add_column(
        "pending_actions", sa.Column("reconciliation_required_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE pending_actions SET command_key = 'legacy:' || id::text WHERE command_key IS NULL")
    # A historic processing row has no attempt token/lease and may have
    # dispatched before a crash. Preserve it as an explicit operator task;
    # never treat it as pending or re-run it.
    op.execute(
        """
        UPDATE pending_actions
        SET status = 'reconciliation_required',
            error_code = 'legacy_processing_requires_reconciliation',
            reconciliation_reason = 'legacy_processing_requires_reconciliation',
            reconciliation_required_at = now(),
            processed_at = coalesce(processed_at, now())
        WHERE status = 'processing'
        """
    )
    op.alter_column("pending_actions", "command_key", nullable=False)
    op.alter_column("pending_actions", "attempt_count", server_default=None)

    # PostgreSQL names an unnamed single-column UNIQUE constraint this way.
    op.drop_constraint("pending_actions_idempotency_key_key", "pending_actions", type_="unique")
    op.create_unique_constraint("uq_pending_actions_user_command_key", "pending_actions", ["user_id", "command_key"])
    op.create_check_constraint(
        "ck_pending_actions_status",
        "pending_actions",
        f"status IN ({_ACTION_STATES})",
    )
    op.create_check_constraint(
        "ck_pending_actions_processing_attempt",
        "pending_actions",
        "status <> 'processing' OR (attempt_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
    )

    op.create_table(
        "pending_action_audit_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("action_id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("old_status", sa.String(length=32), nullable=True),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("attempt_token", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["action_id"], ["pending_actions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pending_action_audit_events_action_created",
        "pending_action_audit_events",
        ["action_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pending_action_audit_events_action_created", table_name="pending_action_audit_events")
    op.drop_table("pending_action_audit_events")
    op.drop_constraint("ck_pending_actions_processing_attempt", "pending_actions", type_="check")
    op.drop_constraint("ck_pending_actions_status", "pending_actions", type_="check")
    op.drop_constraint("uq_pending_actions_user_command_key", "pending_actions", type_="unique")
    # Priority 6 intentionally permits identical payloads under different
    # command keys. Give rollback's legacy unique column one stable unique
    # compatibility value per row before reintroducing its old constraint.
    op.execute("UPDATE pending_actions SET idempotency_key = md5(id::text)")
    op.create_unique_constraint("pending_actions_idempotency_key_key", "pending_actions", ["idempotency_key"])
    op.drop_column("pending_actions", "reconciliation_required_at")
    op.drop_column("pending_actions", "reconciliation_evidence")
    op.drop_column("pending_actions", "reconciliation_reason")
    op.drop_column("pending_actions", "lease_expires_at")
    op.drop_column("pending_actions", "attempt_started_at")
    op.drop_column("pending_actions", "attempt_count")
    op.drop_column("pending_actions", "attempt_token")
    op.drop_column("pending_actions", "command_key")
