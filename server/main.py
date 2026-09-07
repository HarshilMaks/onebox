from server.logging_config import setup_logging
import logging  
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Import routers
from server.routes import agent_oauth, google_mail, push_router, agent_router
# Import services
from server.services.mail import initialize_gmail_service, stop_gmail_watch, get_gmail_service_instance
from server.schemas import ReadinessResponse
from dotenv import load_dotenv
# Load environment variables
load_dotenv()

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

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
        stop_gmail_watch(gmail_service)
    logger.info("Gmail AI Agent service stopped")

# Create the FastAPI app with lifespan handler
app = FastAPI(
    title="Hexel Onebox Server",
    lifespan=lifespan,
    debug=False
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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




