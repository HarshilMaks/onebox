"""Idempotency and confirmation helpers for agent-triggered side effects.

Two independent concerns are handled here:

1. Idempotency: the same logical tool call (same tool + same arguments)
   should not perform its external side effect twice within a short
   window, even if the LLM retries it (e.g. after a transient error or
   a max-turns loop). This is enforced with a short-lived Redis lock
   keyed by a deterministic hash of the tool name and arguments.

2. Confirmation: tools that send external, hard-to-reverse
   communication (sending email, replying to email, creating a
   calendar event that notifies attendees) default to requiring an
   explicit confirmation step rather than executing immediately. This
   is enforced in the tool functions themselves, not only in the
   prompt, so a model instruction cannot bypass it.
"""
import hashlib
import json
import logging

from server.redis_cache import redis_client

logger = logging.getLogger(__name__)

_IDEMPOTENCY_KEY_PREFIX = "agent_side_effect:"
_IDEMPOTENCY_TTL_SECONDS = 120  # Covers retries within the same agent run.


def make_idempotency_key(tool_name: str, **kwargs) -> str:
    """Build a deterministic key for a tool call from its name and arguments."""
    payload = json.dumps({"tool": tool_name, "args": kwargs}, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{_IDEMPOTENCY_KEY_PREFIX}{digest}"


def acquire_idempotency_lock(key: str) -> bool:
    """Attempt to claim a side-effect key.

    Returns True if this call successfully claimed the key (i.e. it is
    the first attempt and should proceed). Returns False if the key was
    already claimed recently (i.e. this looks like a duplicate/retry of
    the same side effect, and the caller should not repeat it).

    Fails open (returns True) on Redis errors, since blocking a
    legitimate action due to an unrelated infrastructure failure is
    worse than a rare missed duplicate check.
    """
    try:
        # NX: only set if not already present. Ensures only the first
        # caller for a given key proceeds within the TTL window.
        claimed = redis_client.set(key, "1", nx=True, ex=_IDEMPOTENCY_TTL_SECONDS)
        return bool(claimed)
    except Exception:
        logger.warning("Idempotency check failed for key %s; allowing the call through.", key, exc_info=True)
        return True
