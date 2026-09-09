from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.config import ServiceRole, settings
from server.integrations.google import close_google_adapter
from server.integrations.llm import close_llm_adapter
from server.integrations.redis import close_redis_adapter
from server.logging_config import setup_logging
from server.routes import agent_oauth, agent_router, google_mail, push_router
from server.schemas import ReadinessResponse
from server.workers.mail_notifications import run_notification_worker

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start durable enabled workers and release local adapter resources safely."""
    stop_event: asyncio.Event | None = None
    worker_task: asyncio.Task | None = None
    try:
        if settings.runs_automation_worker and settings.SERVICE_ROLE is ServiceRole.COMBINED:
            logger.info("Starting local combined Gmail notification worker")
            stop_event = asyncio.Event()
            worker_task = asyncio.create_task(
                run_notification_worker(stop_event, settings.AUTOMATION_OWNER_ID),
                name="gmail-notification-worker",
            )
        else:
            logger.info("Starting API service with Gmail automation disabled")
        yield
    finally:
        if stop_event is not None:
            stop_event.set()
        if worker_task is not None:
            try:
                await asyncio.wait_for(worker_task, timeout=settings.GMAIL_NOTIFICATION_LEASE_SECONDS)
            except TimeoutError:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            except Exception:
                logger.exception("Gmail notification worker exited unexpectedly")
        # Deliberately do not call Gmail users.stop here. The persisted watch
        # belongs to the mailbox and must survive rolling API/worker restarts.
        await close_redis_adapter()
        await close_google_adapter()
        await close_llm_adapter()


app = FastAPI(
    title="Hexel Onebox Server",
    lifespan=lifespan,
    debug=False,
)

cors_allowed_origins = list(settings.cors_allowed_origins)
if not cors_allowed_origins:
    logger.warning("No CORS origins configured; cross-origin browser access is disabled")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins,
    allow_credentials=bool(cors_allowed_origins),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/", response_model=ReadinessResponse)
async def root():
    """Report process health and whether the durable automation worker is enabled."""
    return {
        "status": "ok",
        "global_gmail_service": "durable_worker_enabled" if settings.runs_automation else "disabled",
    }


app.include_router(google_mail.router)
app.include_router(push_router.router)
app.include_router(agent_router.router)
app.include_router(agent_oauth.router)
