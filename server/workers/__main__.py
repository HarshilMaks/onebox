"""Dedicated entrypoint for the durable Gmail notification worker."""
from __future__ import annotations

import asyncio
import signal

from server.config import ServiceRole, settings
from server.integrations.google import close_google_adapter
from server.integrations.llm import close_llm_adapter
from server.integrations.redis import close_redis_adapter
from server.logging_config import setup_logging
from server.workers.mail_notifications import run_notification_worker


async def main() -> None:
    if not settings.runs_automation_worker or settings.SERVICE_ROLE is not ServiceRole.AUTOMATION_WORKER:
        raise RuntimeError("server.workers requires SERVICE_ROLE=automation_worker and AUTOMATION_ENABLED=true")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_event.set)
    try:
        await run_notification_worker(stop_event, settings.AUTOMATION_OWNER_ID)
    finally:
        await close_redis_adapter()
        await close_google_adapter()
        await close_llm_adapter()


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
