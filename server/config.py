from pydantic import HttpUrl
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str

    # Auth Service JWT verification
    SECRET_KEY: str
    ALGORITHM: str = "HS256"

    # Google OAuth
    GOOGLE_OAUTH_CLIENT_SECRETS: str
    OAUTH_REDIRECT_URI: str
    FRONTEND_OAUTH_CALLBACK_URI: HttpUrl # The frontend URI backend redirects to

    # Google Pub/Sub
    PUBSUB_TOPIC: str
    PUBSUB_SUBSCRIPTION: str
    GOOGLE_APPLICATION_CREDENTIALS: str
    # Google-signed OIDC values for the /mail/notifications push receiver.
    # Empty values intentionally disable that endpoint until deployment configures it.
    PUBSUB_PUSH_AUDIENCE: str = ""
    PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "forbid"  # Optional, it's default now — keep it strict

settings = Settings()
