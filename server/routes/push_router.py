import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from google.auth import exceptions as google_auth_exceptions
from google.oauth2 import id_token

from server.config import settings
from server.integrations.google import GoogleProviderError, google_auth_request, run_google_operation
from server.logging_config import setup_logging
from server.schemas import GlobalGmailHealthResponse
from server.services.credentials import (
    CredentialEncryptionUnavailable,
    GoogleCredentialsUnavailable,
    GoogleReconnectRequired,
)
from server.services.notification_jobs import (
    AutomationBaselineUnavailable,
    NotificationValidationError,
    automation_status,
    enqueue_notification,
    parse_notification_envelope,
)
from server.services.setup_google import get_current_user_info
from server.workers.mail_notifications import renew_automation_watch

setup_logging()
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mail", tags=["mail"])


async def require_pubsub_push_auth(request: Request) -> dict:
    """Verify a Google-signed OIDC token from the configured Pub/Sub push SA."""
    expected_audience = (settings.PUBSUB_PUSH_AUDIENCE or "").strip()
    expected_email = (settings.PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL or "").strip().casefold()
    if not expected_audience or not expected_email:
        logger.error("Pub/Sub push authentication is not configured")
        raise HTTPException(status_code=503, detail="Pub/Sub push authentication is not configured")

    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token:
        logger.warning("Rejected Pub/Sub push request without a bearer token")
        raise HTTPException(status_code=401, detail="Invalid Pub/Sub push authentication")

    try:
        claims = await run_google_operation(
            id_token.verify_oauth2_token,
            token,
            google_auth_request(),
            expected_audience,
            passthrough=(
                ValueError,
                google_auth_exceptions.InvalidType,
                google_auth_exceptions.InvalidValue,
                google_auth_exceptions.MalformedError,
            ),
        )
    except GoogleProviderError:
        logger.warning("Pub/Sub push token verification is temporarily unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="Pub/Sub push authentication is temporarily unavailable") from None
    except Exception:
        logger.warning("Rejected Pub/Sub push request with an invalid OIDC token")
        raise HTTPException(status_code=401, detail="Invalid Pub/Sub push authentication") from None

    email = claims.get("email")
    if (
        claims.get("email_verified") is not True
        or not isinstance(email, str)
        or email.strip().casefold() != expected_email
    ):
        logger.warning("Rejected Pub/Sub push request from an unexpected principal")
        raise HTTPException(status_code=403, detail="Pub/Sub push principal is not authorized")
    return claims


async def require_global_gmail_operator(
    user_info: dict = Depends(get_current_user_info),
) -> dict:
    """Authorize only the JWT owner of the configured automation mailbox."""
    global_owner_id = settings.AUTOMATION_OWNER_ID
    if global_owner_id is None:
        logger.error("Global Gmail operator identity is not configured")
        raise HTTPException(status_code=503, detail="Global Gmail operator is not configured")
    if user_info["user_id"] != global_owner_id:
        logger.warning("Denied global Gmail operator request from a non-owner")
        raise HTTPException(status_code=403, detail="Not authorized for global Gmail operations")
    return user_info


async def _bounded_body(request: Request) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length:
        try:
            if int(raw_length) > settings.PUBSUB_MAX_ENVELOPE_BYTES:
                raise HTTPException(status_code=413, detail="Notification body is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid notification body") from None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.PUBSUB_MAX_ENVELOPE_BYTES:
            raise HTTPException(status_code=413, detail="Notification body is too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/notifications", status_code=204)
async def receive_gmail_notification(
    request: Request,
    _push_claims: dict = Depends(require_pubsub_push_auth),
):
    """Validate and commit one notification job before acknowledging Pub/Sub."""
    if not settings.AUTOMATION_ENABLED or settings.AUTOMATION_OWNER_ID is None:
        raise HTTPException(status_code=503, detail="Gmail automation is disabled")
    try:
        envelope = parse_notification_envelope(await _bounded_body(request), settings.PUBSUB_SUBSCRIPTION)
        inserted = await enqueue_notification(envelope, settings.AUTOMATION_OWNER_ID)
        logger.info("Persisted Gmail notification job inserted=%s", inserted)
    except NotificationValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid Gmail notification") from exc
    except AutomationBaselineUnavailable:
        raise HTTPException(status_code=503, detail="Gmail notification baseline is not ready") from None
    except (CredentialEncryptionUnavailable, GoogleCredentialsUnavailable, GoogleReconnectRequired):
        # No durable enqueue occurred; Pub/Sub must retry this delivery.
        raise HTTPException(status_code=503, detail="Gmail notification persistence is unavailable") from None
    except Exception:
        logger.exception("Unable to persist Gmail notification job")
        # Non-2xx is intentional: acknowledge only after transaction commit.
        raise HTTPException(status_code=503, detail="Gmail notification persistence is unavailable") from None


@router.get("/agent/health", response_model=GlobalGmailHealthResponse)
async def health_check(
    _operator: dict = Depends(require_global_gmail_operator),
):
    """Expose durable automation state without mailbox content."""
    status = await automation_status(settings.AUTOMATION_OWNER_ID)
    if status is None:
        return {
            "status": "unavailable",
            "detail": "No durable Gmail mailbox state exists yet.",
            "gmail_service_status": "unavailable",
        }
    healthy = not status["resync_required"] and status["failure_count"] == 0
    return {
        "status": "healthy" if healthy else "degraded",
        "detail": "Durable Gmail automation status is available.",
        "gmail_service_status": "ready" if healthy else "degraded",
    }


@router.get("/agent/status")
async def automation_status_endpoint(
    _operator: dict = Depends(require_global_gmail_operator),
):
    """Return safe cursor/watch/queue observability for the configured operator."""
    status = await automation_status(settings.AUTOMATION_OWNER_ID)
    if status is None:
        raise HTTPException(status_code=404, detail="Gmail automation state not found")
    return status


@router.post("/renew-watch")
async def renew_watch(
    _operator: dict = Depends(require_global_gmail_operator),
):
    """Attempt singleton-leased watch renewal; no mailbox-wide stop is issued."""
    renewed = await renew_automation_watch(settings.AUTOMATION_OWNER_ID)
    return {"status": "renewed" if renewed else "not_due_or_unavailable"}


@router.post("/agent/check-inbox")
async def check_inbox(
    _operator: dict = Depends(require_global_gmail_operator),
):
    """Retire process-local manual processing in favor of durable history jobs."""
    raise HTTPException(
        status_code=409,
        detail="Manual inbox processing is disabled; use durable Pub/Sub/history processing.",
    )
