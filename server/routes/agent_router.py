import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from googleapiclient.discovery import Resource
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agents import ExecutiveAgent, GeneralAgent, GeneralAgentStreamer
from server.database import get_agent_db
from server.schemas import (
    AgentErrorResponse,
    AgentStreamEvent,
    AgentSuccessResponse,
    PendingActionResponse,
)
from server.integrations.google import GoogleProviderError
from server.integrations.llm import (
    LlmOperationInternal,
    LlmOperationTimeout,
    LlmOperationUnavailable,
)
from server.agent_policy import INTERACTIVE_EXECUTIVE_POLICY, INTERACTIVE_STREAM_POLICY
from server.config import settings
from server.services.pending_actions import (
    PendingActionInvalidState,
    PendingActionNotFound,
    claim_pending_action,
    execute_claimed_action,
    finalize_pre_dispatch_failure,
    get_pending_action,
    get_pending_action_for_reconciliation,
    reconcile_pending_action,
    reject_pending_action,
)
from server.services.credentials import (
    CredentialEncryptionUnavailable,
    GoogleConnection,
    GoogleCredentialsUnavailable,
    GoogleReconnectRequired,
    build_google_api_service,
    load_connected_google_connection,
)
from server.services.setup_google import (
    get_calendar_service,
    get_connected_google_connection,
    get_current_user_info,
    get_gmail_service,
    get_tasks_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["AI Agents"])


class AgentQuery(BaseModel):
    input: str = Field(min_length=1, max_length=8_000)


def _agent_provider_http_error(error: Exception) -> HTTPException:
    if isinstance(error, LlmOperationTimeout):
        status_code, code, detail = 504, "llm_timeout", "The agent response timed out. Please try again."
    elif isinstance(error, LlmOperationUnavailable):
        status_code, code, detail = 503, "llm_unavailable", "The agent is temporarily unavailable."
    else:
        status_code, code, detail = 502, "llm_internal", "The agent could not complete this request."
    return HTTPException(
        status_code=status_code,
        detail=AgentErrorResponse(error=code, detail=detail).model_dump(),
    )


async def _action_provider_services(
    action: dict,
    user_id: UUID,
    db: AsyncSession,
) -> dict[str, Resource | None]:
    providers = {
        "send_email": ("gmail", "v1", "gmail_service"),
        "send_reply": ("gmail", "v1", "gmail_service"),
        "create_event": ("calendar", "v3", "calendar_service"),
        "create_task": ("tasks", "v1", "tasks_service"),
    }
    selected = providers.get(action["action_type"])
    if selected is None:
        return {
            "gmail_service": None,
            "calendar_service": None,
            "tasks_service": None,
        }

    try:
        connection = await load_connected_google_connection(user_id, db)
        service = await build_google_api_service(connection, selected[0], selected[1])
    except GoogleReconnectRequired:
        raise HTTPException(status_code=409, detail="Google account reconnection is required") from None
    except (CredentialEncryptionUnavailable, GoogleCredentialsUnavailable, GoogleProviderError):
        raise HTTPException(status_code=503, detail="Google credentials are temporarily unavailable") from None

    services: dict[str, Resource | None] = {
        "gmail_service": None,
        "calendar_service": None,
        "tasks_service": None,
    }
    services[selected[2]] = service
    return services


async def require_pending_action_operator(
    user_info: dict = Depends(get_current_user_info),
) -> dict:
    """Fail closed unless the authenticated principal is explicitly configured."""
    operators = settings.pending_action_operator_ids
    if not operators:
        logger.error("Pending-action reconciliation operator identities are not configured")
        raise HTTPException(status_code=503, detail="Pending-action reconciliation is not configured")
    if user_info["user_id"] not in operators:
        logger.warning("Denied pending-action reconciliation to non-operator")
        raise HTTPException(status_code=403, detail="Not authorized to reconcile pending actions")
    return user_info


@router.get("/actions/{action_id}", response_model=PendingActionResponse)
async def get_pending_action_endpoint(
    action_id: UUID,
    user_info: dict = Depends(get_current_user_info),
):
    try:
        return await get_pending_action(action_id, user_info["user_id"])
    except PendingActionNotFound:
        raise HTTPException(status_code=404, detail="Action not found")


@router.post("/actions/{action_id}/approve", response_model=PendingActionResponse)
async def approve_pending_action_endpoint(
    action_id: UUID,
    user_info: dict = Depends(get_current_user_info),
    db: AsyncSession = Depends(get_agent_db),
):
    """Approve and execute exactly one immutable action owned by this JWT user."""
    try:
        action, claimed = await claim_pending_action(action_id, user_info["user_id"])
        if not claimed:
            return action
        try:
            services = await _action_provider_services(action, user_info["user_id"], db)
        except HTTPException as exc:
            # Credentials/services are resolved before constructing or dispatching
            # the provider write, so this is a confirmed safe failure.
            code = "google_reconnect_required" if exc.status_code == 409 else "credentials_unavailable_before_dispatch"
            return await finalize_pre_dispatch_failure(action, code)
        return await execute_claimed_action(action, **services)
    except PendingActionNotFound:
        raise HTTPException(status_code=404, detail="Action not found")
    except PendingActionInvalidState:
        raise HTTPException(status_code=409, detail="Action cannot be approved in its current state")


@router.post("/actions/{action_id}/reject", response_model=PendingActionResponse)
async def reject_pending_action_endpoint(
    action_id: UUID,
    user_info: dict = Depends(get_current_user_info),
):
    try:
        return await reject_pending_action(action_id, user_info["user_id"])
    except PendingActionNotFound:
        raise HTTPException(status_code=404, detail="Action not found")
    except PendingActionInvalidState:
        raise HTTPException(status_code=409, detail="Action cannot be rejected in its current state")


@router.post("/actions/{action_id}/reconcile", response_model=PendingActionResponse)
async def reconcile_pending_action_endpoint(
    action_id: UUID,
    operator: dict = Depends(require_pending_action_operator),
    db: AsyncSession = Depends(get_agent_db),
):
    """Reconcile an ambiguous write using deterministic provider markers only."""
    try:
        action = await get_pending_action_for_reconciliation(action_id)
        try:
            services = await _action_provider_services(action, action["user_id"], db)
        except HTTPException:
            # Preserve the explicit ambiguous state and audit that lookup could
            # not start; a service-build failure never triggers a provider write.
            services = {"gmail_service": None, "calendar_service": None, "tasks_service": None}
        return await reconcile_pending_action(action_id, operator["user_id"], **services)
    except PendingActionNotFound:
        raise HTTPException(status_code=404, detail="Action not found")
    except PendingActionInvalidState:
        raise HTTPException(status_code=409, detail="Action cannot be reconciled in its current state")


@router.post("/executive/", response_model=AgentSuccessResponse)
async def invoke_executive_agent_endpoint(
    query: AgentQuery,
    user_info: dict = Depends(get_current_user_info),
    gmail_service: Resource = Depends(get_gmail_service),
    calendar_service: Resource = Depends(get_calendar_service),
    tasks_service: Resource = Depends(get_tasks_service),
    google_connection: GoogleConnection = Depends(get_connected_google_connection),
):
    try:
        agent = ExecutiveAgent(user_id=str(user_info["user_id"]))
        result = await agent.run(
            input_query=query.input,
            gmail_service=gmail_service,
            calendar_service=calendar_service,
            tasks_service=tasks_service,
            current_user_email=google_connection.google_email,
            policy=INTERACTIVE_EXECUTIVE_POLICY,
        )
        return {"result": result}
    except HTTPException:
        raise
    except (LlmOperationInternal, LlmOperationTimeout, LlmOperationUnavailable) as exc:
        raise _agent_provider_http_error(exc) from None
    except Exception:
        logger.exception("Error in executive agent endpoint for user %s", user_info.get("user_id"))
        raise HTTPException(
            status_code=500,
            detail=AgentErrorResponse(
                error="executive_agent_failed",
                detail="The executive agent could not complete this request.",
            ).model_dump(),
        )


@router.post("/generate-content/", response_model=AgentSuccessResponse)
async def invoke_general_agent_endpoint(
    query: AgentQuery,
    user_info: dict = Depends(get_current_user_info),
):
    try:
        agent = GeneralAgent(user_id=str(user_info["user_id"]))
        return {"result": await agent.run(input_query=query.input)}
    except HTTPException:
        raise
    except (LlmOperationInternal, LlmOperationTimeout, LlmOperationUnavailable) as exc:
        raise _agent_provider_http_error(exc) from None
    except Exception:
        logger.exception("Error in general agent endpoint for user %s", user_info.get("user_id"))
        raise HTTPException(
            status_code=500,
            detail=AgentErrorResponse(
                error="general_agent_failed",
                detail="The agent could not generate a response.",
            ).model_dump(),
        )


def _format_stream_event(
    event: str,
    content: str,
    error_code: str | None = None,
) -> str:
    """Serialize one safe, structured Server-Sent Event data frame."""
    payload = AgentStreamEvent(
        event=event,
        content=content,
        error_code=error_code,
    ).model_dump(exclude_none=True)
    return f"data: {json.dumps(payload)}\n\n"


@router.post(
    "/generate-stream/",
    response_model=AgentStreamEvent,
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "A text/event-stream response. Each non-comment data frame is JSON matching AgentStreamEvent.",
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string", "format": "event-stream"},
                    "example": 'data: {"event":"token","content":"Hello"}\\n\\n',
                }
            },
        }
    },
)
async def invoke_general_agent_stream_endpoint(
    query: AgentQuery,
    request: Request,
    user_info: dict = Depends(get_current_user_info),
    google_connection: GoogleConnection = Depends(get_connected_google_connection),
):
    """Stream structured, user-safe agent events as JSON SSE data frames."""
    try:
        agent = GeneralAgentStreamer(user_id=str(user_info["user_id"]))
        user_email = google_connection.google_email

        async def stream_response_generator():
            iterator = agent.run(
                input_query=query.input,
                current_user_email=user_email,
                policy=INTERACTIVE_STREAM_POLICY,
            ).__aiter__()
            terminal_sent = False
            next_event: asyncio.Task | None = None
            heartbeat_seconds = min(5.0, settings.LLM_STREAM_IDLE_TIMEOUT_SECONDS)
            try:
                next_event = asyncio.create_task(anext(iterator))
                while True:
                    done, _ = await asyncio.wait({next_event}, timeout=heartbeat_seconds)
                    if not done:
                        if await request.is_disconnected():
                            return
                        yield ": heartbeat\n\n"
                        continue

                    try:
                        event_type, content, error_code = next_event.result()
                    except StopAsyncIteration:
                        break
                    next_event = asyncio.create_task(anext(iterator))

                    if await request.is_disconnected():
                        return
                    yield _format_stream_event(event_type, content, error_code)
                    if event_type in {"done", "error"}:
                        terminal_sent = True
                        return

                if not terminal_sent and not await request.is_disconnected():
                    terminal_sent = True
                    yield _format_stream_event("done", "")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error while streaming agent response for user %s", user_info.get("user_id"))
                if not terminal_sent and not await request.is_disconnected():
                    terminal_sent = True
                    yield _format_stream_event(
                        "error",
                        "The agent stream failed unexpectedly.",
                        "llm_internal",
                    )
            finally:
                if next_event is not None and not next_event.done():
                    next_event.cancel()
                    await asyncio.gather(next_event, return_exceptions=True)
                await iterator.aclose()

        return StreamingResponse(
            stream_response_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error starting agent stream for user %s", user_info.get("user_id"))
        raise HTTPException(
            status_code=500,
            detail=AgentErrorResponse(
                error="stream_start_failed",
                detail="Could not start the agent stream.",
            ).model_dump(),
        )
