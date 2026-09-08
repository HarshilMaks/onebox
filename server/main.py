from contextlib import asynccontextmanager
import logging
from urllib.parse import urlsplit

from dotenv import load_dotenv
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

load_dotenv()
setup_logging()
logger = logging.getLogger(__name__)


def get_cors_allowed_origins(raw_origins: str) -> list[str]:
    """Parse a comma-separated allowlist of exact HTTP(S) browser origins."""
    origins: list[str] = []
    for raw_origin in raw_origins.split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        try:
            parsed = urlsplit(origin)
        except ValueError:
            logger.warning("Ignoring invalid configured CORS origin")
            continue
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            logger.warning("Ignoring invalid configured CORS origin")
            continue
        if origin not in origins:
            origins.append(origin)
    return origins

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and clean up resources using FastAPI lifespan events."""
    # Startup logic
    logger.info("Starting Gmail AI Agent service...")
    initialized = await initialize_gmail_service()
    if initialized:
        logger.info("Gmail AI Agent service started")
    else:
        logger.warning(
            "Gmail AI Agent service did not start (global Gmail service unavailable). "
            "Push/automated mail endpoints will report unavailable until this is resolved."
        )
    
    # Yield control to application
    yield
    
    # Shutdown logic
    logger.info("Shutting down Gmail AI Agent service...")
    gmail_service = get_gmail_service_instance()
    if gmail_service:
        await stop_gmail_watch(gmail_service)
    logger.info("Gmail AI Agent service stopped")

# Create the FastAPI app with lifespan handler
app = FastAPI(
    title="Hexel Onebox Server",
    lifespan=lifespan,
    debug=False
)

cors_allowed_origins = get_cors_allowed_origins(settings.CORS_ALLOWED_ORIGINS)
if not cors_allowed_origins:
    logger.warning("No valid CORS origins configured; cross-origin browser access is disabled")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins,
    allow_credentials=bool(cors_allowed_origins),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.get("/", response_model=ReadinessResponse)
async def root():
    """Application readiness endpoint.

    Reports whether the process is up and whether the globally
    initialized Gmail service (used for Pub/Sub push processing) is
    available. This does not indicate per-user Gmail connectivity,
    which is checked per-request via each user's stored OAuth token.
    """
    gmail_service = get_gmail_service_instance()
    global_gmail_ready = gmail_service is not None

    return {
        "status": "ok",
        "global_gmail_service": "ready" if global_gmail_ready else "unavailable",
    }

# Include routers
app.include_router(google_mail.router)
app.include_router(push_router.router)
app.include_router(agent_router.router)
app.include_router(agent_oauth.router)




