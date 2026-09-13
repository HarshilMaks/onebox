"""Persist Gmail watch-renewal retry backoff state.

Revision ID: f5a3c9d8e271
Revises: e84c0a9b6d21
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f5a3c9d8e271"
down_revision: Union[str, None] = "e84c0a9b6d21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "gmail_mailbox_states",
        sa.Column("watch_renewal_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "gmail_mailbox_states",
        sa.Column("watch_renewal_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_gmail_mailbox_states_watch_renewal_attempt_count",
        "gmail_mailbox_states",
        "watch_renewal_attempt_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_gmail_mailbox_states_watch_renewal_attempt_count",
        "gmail_mailbox_states",
        type_="check",
    )
    op.drop_column("gmail_mailbox_states", "watch_renewal_next_attempt_at")
    op.drop_column("gmail_mailbox_states", "watch_renewal_attempt_count")
