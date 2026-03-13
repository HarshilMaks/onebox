from server.logging_config import setup_logging
import logging  
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

# Import routers
from server.routes import agent_oauth, google_mail, push_router, agent_router
# Import services
from server.services.mail import initialize_gmail_service, stop_gmail_watch, get_gmail_service_instance
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
    # Run Redis setup shell script
    logger.info("Starting Gmail AI Agent service...")
    initialize_gmail_service()
    logger.info("Gmail AI Agent service started")
    
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

@app.get("/")
async def root():
    """Root endpoint."""
    return {"status": "active"}

# Include routers
app.include_router(google_mail.router)
app.include_router(push_router.router)
app.include_router(agent_router.router)
app.include_router(agent_oauth.router)




