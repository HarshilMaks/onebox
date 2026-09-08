from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.config import settings
from server.logging_config import setup_logging
from server.routes import agent_oauth, agent_router, google_mail, push_router
from server.schemas import ReadinessResponse
from server.services.mail import (
    get_gmail_service_instance,
    initialize_gmail_service,
    stop_gmail_watch,
)

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start only the services enabled for this validated process role."""
    if settings.runs_automation:
        logger.info("Starting Gmail automation service")
        initialized = await initialize_gmail_service()
        if initialized:
            logger.info("Gmail automation service started")
        else:
            logger.warning("Gmail automation service did not start")
    else:
        logger.info("Starting API service with Gmail automation disabled")

    yield

    if settings.runs_automation:
        logger.info("Stopping Gmail automation service")
        gmail_service = get_gmail_service_instance()
        if gmail_service:
            await stop_gmail_watch(gmail_service)
        logger.info("Gmail automation service stopped")


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
    """Report process health and the optional global Gmail automation state."""
    gmail_service = get_gmail_service_instance()
    return {
        "status": "ok",
        "global_gmail_service": "ready" if gmail_service is not None else "unavailable",
    }


app.include_router(google_mail.router)
app.include_router(push_router.router)
app.include_router(agent_router.router)
app.include_router(agent_oauth.router)
