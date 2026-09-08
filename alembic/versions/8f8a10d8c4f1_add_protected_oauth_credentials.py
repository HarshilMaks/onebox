"""Add encrypted OAuth credential storage and explicit connection state.

Revision ID: 8f8a10d8c4f1
Revises: 620d1f8b1e34
Create Date: 2026-09-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8f8a10d8c4f1"
down_revision: Union[str, None] = "620d1f8b1e34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONNECTION_STATES = "'pending', 'connected', 'reconnect_required', 'quarantined'"


def upgrade() -> None:
    """Expand token storage for encrypted dual-read/new-write credential rollout."""
    op.alter_column(
        "onebox_tokens",
        "token_json",
        existing_type=sa.JSON(),
        nullable=True,
    )
    op.add_column("onebox_tokens", sa.Column("encrypted_token_payload", sa.Text(), nullable=True))
    op.add_column("onebox_tokens", sa.Column("credential_key_id", sa.String(length=128), nullable=True))
    op.add_column(
        "onebox_tokens", sa.Column("credential_format_version", sa.String(length=16), nullable=True)
    )
    op.add_column("onebox_tokens", sa.Column("google_email_normalized", sa.String(), nullable=True))
    op.add_column(
        "onebox_tokens",
        sa.Column(
            "connection_status",
            sa.String(length=32),
            nullable=False,
            server_default="reconnect_required",
        ),
    )
    op.create_index(
        "ix_onebox_tokens_google_email_normalized",
        "onebox_tokens",
        ["google_email_normalized"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_onebox_tokens_connection_status",
        "onebox_tokens",
        f"connection_status IN ({_CONNECTION_STATES})",
    )

    # Existing rows remain a temporary dual-read path. Rows that cannot prove a
    # usable persisted account stay reconnect-required until the owner-run
    # backfill validates and encrypts them.
    op.execute(
        """
        UPDATE onebox_tokens
        SET connection_status = 'pending'
        WHERE token_json::jsonb ->> 'status' = 'pending_oauth'
        """
    )
    op.execute(
        """
        UPDATE onebox_tokens
        SET connection_status = 'connected',
            google_email_normalized = lower(trim(user_email))
        WHERE token_json::jsonb ? 'refresh_token'
          AND coalesce(trim(user_email), '') <> ''
        """
    )
    op.alter_column("onebox_tokens", "connection_status", server_default=None)


def downgrade() -> None:
    """Remove only expand-phase columns; data rollback requires the documented runbook."""
    op.drop_constraint("ck_onebox_tokens_connection_status", "onebox_tokens", type_="check")
    op.drop_index("ix_onebox_tokens_google_email_normalized", table_name="onebox_tokens")
    op.drop_column("onebox_tokens", "connection_status")
    op.drop_column("onebox_tokens", "google_email_normalized")
    op.drop_column("onebox_tokens", "credential_format_version")
    op.drop_column("onebox_tokens", "credential_key_id")
    op.drop_column("onebox_tokens", "encrypted_token_payload")
