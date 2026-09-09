from contextlib import asynccontextmanager
import asyncio
import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.config import ServiceRole, settings
from server.database import close_database
from server.integrations.google import close_google_adapter
from server.integrations.llm import close_llm_adapter
from server.integrations.redis import close_redis_adapter
from server.logging_config import bind_correlation_id, reset_correlation_id, setup_logging
from server.routes import agent_oauth, agent_router, google_mail, push_router
from server.schemas import PublicErrorResponse, ReadinessResponse
from server.services.readiness import readiness_status
from server.workers.mail_notifications import run_notification_worker

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start durable enabled workers and release local adapter resources safely."""
    stop_event: asyncio.Event | None = None
    worker_task: asyncio.Task | None = None
    try:
        if settings.runs_automation_worker and settings.SERVICE_ROLE is ServiceRole.COMBINED:
            logger.info("Starting local combined Gmail notification worker")
            stop_event = asyncio.Event()
            worker_task = asyncio.create_task(
                run_notification_worker(stop_event, settings.AUTOMATION_OWNER_ID),
                name="gmail-notification-worker",
            )
        else:
            logger.info("Starting API service with Gmail automation disabled")
        yield
    finally:
        if stop_event is not None:
            stop_event.set()
        if worker_task is not None:
            try:
                await asyncio.wait_for(worker_task, timeout=settings.GMAIL_NOTIFICATION_LEASE_SECONDS)
            except TimeoutError:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            except Exception:
                logger.exception("Gmail notification worker exited unexpectedly")
        # Deliberately do not call Gmail users.stop here. The persisted watch
        # belongs to the mailbox and must survive rolling API/worker restarts.
        await close_redis_adapter()
        await close_google_adapter()
        await close_llm_adapter()
        await close_database()


app = FastAPI(
    title="Hexel Onebox Server",
    lifespan=lifespan,
    debug=False,
)

_STATUS_ERROR_CODES = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "too_many_requests",
    502: "provider_error",
    503: "service_unavailable",
    504: "request_timeout",
}


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, _error: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=PublicErrorResponse(
            error="validation_error",
            detail="Request validation failed.",
        ).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, error: HTTPException):
    if isinstance(error.detail, dict) and {"error", "detail"}.issubset(error.detail):
        content = error.detail
    else:
        content = PublicErrorResponse(
            error=_STATUS_ERROR_CODES.get(error.status_code, "request_failed"),
            detail="Request could not be completed.",
        ).model_dump()
    return JSONResponse(status_code=error.status_code, content=content, headers=error.headers)


@app.exception_handler(Exception)
async def internal_error_handler(_request: Request, _error: Exception):
    logger.exception("Unhandled HTTP request failure")
    return JSONResponse(
        status_code=500,
        content=PublicErrorResponse(
            error="internal_error",
            detail="Request could not be completed.",
        ).model_dump(),
    )


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "")
    correlation_id = supplied if supplied.isascii() and 1 <= len(supplied) <= 64 else str(uuid4())
    token = bind_correlation_id(f"request:{correlation_id}")
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = correlation_id
        return response
    finally:
        reset_correlation_id(token)


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


@app.get("/livez")
async def livez():
    """Dependency-free process liveness probe."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Dependency/schema-aware readiness probe; never calls Google APIs."""
    ready, reason = await readiness_status()
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "unready", "reason": reason},
    )


@app.get("/", response_model=ReadinessResponse)
async def root():
    """Report process health and whether the durable automation worker is enabled."""
    return {
        "status": "ok",
        "global_gmail_service": "durable_worker_enabled" if settings.runs_automation else "disabled",
    }


app.include_router(google_mail.router)
app.include_router(push_router.router)
app.include_router(agent_router.router)
app.include_router(agent_oauth.router)
