# server/schemas.py
from uuid import UUID
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime

class Email(BaseModel):
    id: str
    subject: Optional[str] = None
    sender: Optional[str] = None
    to: Optional[List[str]] = None
    snippet: Optional[str] = None
    is_read: bool
    is_starred: bool
    labels: List[str]

class EmailDraft(BaseModel):
    to: List[EmailStr]
    subject: str
    body: str
    draft_id: Optional[str] = None

class EmailPage(BaseModel):
    emails: List[Email]
    next_page_token: Optional[str] = None

class OAuthCallback(BaseModel):
    code: str
    state: str | None = None

class TokenInfo(BaseModel):
    access_token: str
    refresh_token: str
    scope: str
    token_type: str
    expires_in: int

class AgentTokenOut(BaseModel):
    user_id:  UUID  # <-- Changed
    token: TokenInfo
    updated_at: datetime