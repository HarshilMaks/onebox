"""Backfill legacy plaintext OAuth credentials into encrypted storage.

Run only after deploying the Priority 4 dual-read/new-write application code to all
API and automation-worker instances and supplying the mounted token keyring:

    python -m server.commands.backfill_oauth_credentials

The command prints aggregate counts only. It never prints user IDs, emails,
credential JSON, ciphertext, or key identifiers.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from typing import Mapping

from sqlalchemy import select

from server.database import AsyncSessionLocal
from server.models import AgentToken
from server.security.oauth_credentials import OAuthCredentialCodec
from server.services.credentials import (
    _CONNECTION_PENDING,
    _CONNECTION_QUARANTINED,
    _legacy_email,
    _validate_token_payload,
    _write_encrypted_token,
    get_credential_codec,
)


def backfill_legacy_row(row: AgentToken, codec: OAuthCredentialCodec) -> str:
    """Convert one legacy row, retaining no plaintext payload after classification."""
    if row.token_json == {"status": "pending_oauth"}:
        row.connection_status = _CONNECTION_PENDING
        row.token_json = None
        return "pending"

    normalized_google_email = _legacy_email(row)
    if normalized_google_email is None or not isinstance(row.token_json, Mapping):
        row.connection_status = _CONNECTION_QUARANTINED
        row.token_json = None
        return "quarantined"

    try:
        token_payload = _validate_token_payload(row.token_json)
    except Exception:
        row.connection_status = _CONNECTION_QUARANTINED
        row.token_json = None
        return "quarantined"

    _write_encrypted_token(row, codec, token_payload, normalized_google_email)
    return "encrypted"


async def backfill_legacy_oauth_credentials(batch_size: int = 100) -> Counter[str]:
    """Process plaintext rows in bounded transactions until no rows remain."""
    if batch_size < 1 or batch_size > 1_000:
        raise ValueError("batch_size must be between 1 and 1000")

    codec = await get_credential_codec()
    results: Counter[str] = Counter()
    while True:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                rows = list(
                    (
                        await db.scalars(
                            select(AgentToken)
                            .where(AgentToken.token_json.is_not(None))
                            .order_by(AgentToken.user_id)
                            .limit(batch_size)
                            .with_for_update(skip_locked=True)
                        )
                    ).all()
                )
                for row in rows:
                    results[backfill_legacy_row(row, codec)] += 1
        if not rows:
            return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100)
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    results = await backfill_legacy_oauth_credentials(args.batch_size)
    print(
        "OAuth credential backfill complete: "
        f"encrypted={results['encrypted']} "
        f"pending={results['pending']} "
        f"quarantined={results['quarantined']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
