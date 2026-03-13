# gmail_models.py
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, validator

class EmailData(BaseModel):
    """Represents the essential data extracted from an email message."""
    id: str
    thread_id: str
    from_email: Optional[EmailStr] = None
    to_emails: List[EmailStr] = []
    subject: Optional[str] = None
    body: Optional[str] = None
    send_time_iso: Optional[str] = None # Store as ISO string for consistency
    user_responded: bool = False # Flag if the user was the last sender in the thread

    @validator('send_time_iso', pre=True, always=True)
    def format_send_time(cls, v):
        if isinstance(v, datetime):
            return v.isoformat()
        return v 

