"""Persist Gmail notification jobs, cursor/watch state, and triage work.

Revision ID: d7e9f1a3b5c7
Revises: b4c6d8e0f2a4
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d7e9f1a3b5c7"
down_revision: Union[str, None] = "b4c6d8e0f2a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gmail_mailbox_states",
        sa.Column("mailbox_email", sa.String(length=320), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("history_cursor", sa.BigInteger(), nullable=True),
        sa.Column("watch_history_id", sa.BigInteger(), nullable=True),
        sa.Column("watch_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watch_lease_token", sa.String(length=128), nullable=True),
        sa.Column("watch_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resync_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resync_page_token", sa.String(length=512), nullable=True),
        sa.Column("resync_message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "watch_lease_expires_at IS NULL OR watch_lease_token IS NOT NULL",
            name="ck_gmail_mailbox_states_watch_lease",
        ),
        sa.PrimaryKeyConstraint("mailbox_email"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_table(
        "gmail_notification_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pubsub_message_id", sa.String(length=255), nullable=False),
        sa.Column("mailbox_email", sa.String(length=320), nullable=False),
        sa.Column("history_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'succeeded', 'dead_letter')",
            name="ck_gmail_notification_jobs_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pubsub_message_id", name="uq_gmail_notification_jobs_pubsub_message_id"),
    )
    op.create_index(
        "ix_gmail_notification_jobs_state_received",
        "gmail_notification_jobs",
        ["state", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_gmail_notification_jobs_mailbox_history",
        "gmail_notification_jobs",
        ["mailbox_email", "history_id"],
        unique=False,
    )
    op.create_table(
        "gmail_triage_work",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mailbox_email", sa.String(length=320), nullable=False),
        sa.Column("message_id", sa.String(length=256), nullable=False),
        sa.Column("source_history_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("triage_summary", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "state IN ('processing', 'succeeded', 'noop', 'dead_letter')",
            name="ck_gmail_triage_work_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mailbox_email", "message_id", name="uq_gmail_triage_work_mailbox_message"),
    )
    op.create_index("ix_gmail_triage_work_state", "gmail_triage_work", ["state"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_gmail_triage_work_state", table_name="gmail_triage_work")
    op.drop_table("gmail_triage_work")
    op.drop_index("ix_gmail_notification_jobs_mailbox_history", table_name="gmail_notification_jobs")
    op.drop_index("ix_gmail_notification_jobs_state_received", table_name="gmail_notification_jobs")
    op.drop_table("gmail_notification_jobs")
    op.drop_table("gmail_mailbox_states")
