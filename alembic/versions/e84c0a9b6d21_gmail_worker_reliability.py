"""Harden durable Gmail worker retry, recovery, and liveness state.

Revision ID: e84c0a9b6d21
Revises: d7e9f1a3b5c7
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e84c0a9b6d21"
down_revision: Union[str, None] = "d7e9f1a3b5c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("gmail_notification_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("gmail_triage_work", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_gmail_notification_jobs_state_next_attempt_received",
        "gmail_notification_jobs",
        ["state", "next_attempt_at", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_gmail_triage_work_state_next_attempt",
        "gmail_triage_work",
        ["state", "next_attempt_at"],
        unique=False,
    )
    op.execute(
        "UPDATE gmail_notification_jobs "
        "SET next_attempt_at = now() "
        "WHERE state = 'pending' AND next_attempt_at IS NULL"
    )
    op.execute(
        "UPDATE gmail_triage_work "
        "SET next_attempt_at = now() "
        "WHERE state = 'processing' AND lease_token IS NULL AND next_attempt_at IS NULL"
    )

    op.add_column(
        "gmail_mailbox_states",
        sa.Column("resync_state", sa.String(length=32), nullable=False, server_default="idle"),
    )
    op.add_column(
        "gmail_mailbox_states",
        sa.Column("resync_generation", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("gmail_mailbox_states", sa.Column("resync_lease_token", sa.String(length=128), nullable=True))
    op.add_column(
        "gmail_mailbox_states",
        sa.Column("resync_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "gmail_mailbox_states",
        sa.Column("resync_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "gmail_mailbox_states",
        sa.Column("resync_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "gmail_mailbox_states",
        sa.Column("worker_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "gmail_mailbox_states",
        sa.Column("watch_last_error_code", sa.String(length=128), nullable=True),
    )
    op.execute("UPDATE gmail_mailbox_states SET resync_state = 'required' WHERE resync_required")
    op.create_check_constraint(
        "ck_gmail_mailbox_states_resync_state",
        "gmail_mailbox_states",
        "resync_state IN ('idle', 'required', 'processing', 'manual_required')",
    )
    op.create_check_constraint(
        "ck_gmail_mailbox_states_resync_lease",
        "gmail_mailbox_states",
        "(resync_state = 'processing') = "
        "(resync_lease_token IS NOT NULL AND resync_lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_gmail_mailbox_states_resync_counts",
        "gmail_mailbox_states",
        "resync_generation >= 0 AND resync_attempt_count >= 0 AND resync_message_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_gmail_mailbox_states_resync_counts", "gmail_mailbox_states", type_="check")
    op.drop_constraint("ck_gmail_mailbox_states_resync_lease", "gmail_mailbox_states", type_="check")
    op.drop_constraint("ck_gmail_mailbox_states_resync_state", "gmail_mailbox_states", type_="check")
    op.drop_column("gmail_mailbox_states", "watch_last_error_code")
    op.drop_column("gmail_mailbox_states", "worker_heartbeat_at")
    op.drop_column("gmail_mailbox_states", "resync_next_attempt_at")
    op.drop_column("gmail_mailbox_states", "resync_attempt_count")
    op.drop_column("gmail_mailbox_states", "resync_lease_expires_at")
    op.drop_column("gmail_mailbox_states", "resync_lease_token")
    op.drop_column("gmail_mailbox_states", "resync_generation")
    op.drop_column("gmail_mailbox_states", "resync_state")
    op.drop_index("ix_gmail_triage_work_state_next_attempt", table_name="gmail_triage_work")
    op.drop_index(
        "ix_gmail_notification_jobs_state_next_attempt_received",
        table_name="gmail_notification_jobs",
    )
    op.drop_column("gmail_triage_work", "next_attempt_at")
    op.drop_column("gmail_notification_jobs", "next_attempt_at")
