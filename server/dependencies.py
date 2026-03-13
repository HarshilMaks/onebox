from tools.setup_tools import get_credentials, get_gmail_service
from googleapiclient.discovery import Resource
from server.logging_config import setup_logging
import logging
from fastapi import HTTPException


# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

# ---------- Dependencies ----------
def gmail_service() -> Resource:
    creds = get_credentials()
    if not creds:
        logger.warning("Authentication failed: No credentials")
        raise HTTPException(status_code=401, detail="Authentication required")
    service = get_gmail_service(creds)
    if not service:
        logger.error("Could not connect to Gmail API")
        raise HTTPException(status_code=500, detail="Could not connect to Gmail API")
    return service

