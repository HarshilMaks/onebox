# server/models.py
from sqlalchemy import Column, JSON, DateTime, String, func # Keep these imports
from sqlalchemy.dialects.postgresql import UUID as PG_UUID # <-- Import PG_UUID again
# Remove Integer import

from sqlalchemy.ext.declarative import declarative_base # Or Base from your database.py if preferred

# Assuming Base is defined here or imported from database.py as you did before
Base = declarative_base()

class AgentToken(Base):
    __tablename__ = 'onebox_tokens'
    user_id    = Column(PG_UUID(as_uuid=True), primary_key=True)
    user_email = Column(String, nullable=False)  # <-- ADD THIS
    token_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())