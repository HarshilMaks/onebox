from __future__ import annotations

from datetime import datetime, timezone

from server.config import settings
from server.database import database_schema_ready
from server.integrations.redis import RedisAdapterError, get_redis_adapter
from server.services.notification_jobs import automation_status


async def readiness_status() -> tuple[bool, str]:
    """Return a safe readiness verdict without live Google provider calls."""
    if not await database_schema_ready():
        return False, "database_or_schema_unavailable"

    if settings.requires_redis:
        try:
            if not await get_redis_adapter().ping():
                return False, "redis_unavailable"
        except RedisAdapterError:
            return False, "redis_unavailable"

    if not settings.runs_automation_worker:
        return True, "ready"

    status = await automation_status(settings.AUTOMATION_OWNER_ID)
    if status is None or not status["watch_valid"] or status["watch_last_error_code"] is not None:
        return False, "automation_watch_unavailable"
    heartbeat = status["worker_heartbeat_at"]
    if heartbeat is None or (datetime.now(timezone.utc) - heartbeat).total_seconds() > settings.GMAIL_WORKER_LIVENESS_SECONDS:
        return False, "automation_worker_stale"
    if status["resync_state"] != "idle" or status["failure_count"]:
        return False, "automation_recovery_required"
    return True, "ready"
