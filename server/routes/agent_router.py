import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from googleapiclient.discovery import Resource
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agents import ExecutiveAgent, GeneralAgent, GeneralAgentStreamer
from server.database import get_agent_db
from server.schemas import (
    AgentErrorResponse,
    AgentStreamEvent,
    AgentSuccessResponse,
    PendingActionResponse,
)
from server.services.pending_actions import (
    PendingActionInvalidState,
    PendingActionNotFound,
    claim_pending_action,
    execute_claimed_action,
    get_pending_action,
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
    input: str


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
    except (CredentialEncryptionUnavailable, GoogleCredentialsUnavailable):
        raise HTTPException(status_code=503, detail="Google credentials are temporarily unavailable") from None

    services: dict[str, Resource | None] = {
        "gmail_service": None,
        "calendar_service": None,
        "tasks_service": None,
    }
    services[selected[2]] = service
    return services


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
        services = await _action_provider_services(action, user_info["user_id"], db)
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
        )
        return {"result": result}
    except HTTPException:
        raise
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


@router.post("/generate-stream/")
async def invoke_general_agent_stream_endpoint(
    query: AgentQuery,
    user_info: dict = Depends(get_current_user_info),
    gmail_service: Resource = Depends(get_gmail_service),
    tasks_service: Resource = Depends(get_tasks_service),
    google_connection: GoogleConnection = Depends(get_connected_google_connection),
):
    """Stream structured, user-safe agent events as JSON SSE data frames."""
    try:
        agent = GeneralAgentStreamer(user_id=str(user_info["user_id"]))
        user_email = google_connection.google_email

        async def stream_response_generator():
            try:
                async for event_type, content, error_code in agent.run(
                    input_query=query.input,
                    gmail_service=gmail_service,
                    tasks_service=tasks_service,
                    current_user_email=user_email,
                ):
                    yield _format_stream_event(event_type, content, error_code)
                    if event_type == "error":
                        return
                yield _format_stream_event("done", "")
            except Exception:
                logger.exception("Error while streaming agent response for user %s", user_info.get("user_id"))
                yield _format_stream_event(
                    "error",
                    "The agent stream failed unexpectedly.",
                    "stream_execution_failed",
                )

        return StreamingResponse(stream_response_generator(), media_type="text/event-stream")
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
