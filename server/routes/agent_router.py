import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from googleapiclient.discovery import Resource
from pydantic import BaseModel

from agents import ExecutiveAgent, GeneralAgent, GeneralAgentStreamer
from server.schemas import AgentErrorResponse, AgentSuccessResponse, PendingActionResponse
from server.services.pending_actions import (
    PendingActionInvalidState,
    PendingActionNotFound,
    claim_pending_action,
    execute_claimed_action,
    get_pending_action,
    reject_pending_action,
)
from server.services.setup_google import (
    get_calendar_service,
    get_current_user_info,
    get_gmail_service,
    get_tasks_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["AI Agents"])


class AgentQuery(BaseModel):
    input: str


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
    gmail_service: Resource = Depends(get_gmail_service),
    calendar_service: Resource = Depends(get_calendar_service),
    tasks_service: Resource = Depends(get_tasks_service),
):
    """Approve and execute exactly one immutable action owned by this JWT user."""
    try:
        action, claimed = await claim_pending_action(action_id, user_info["user_id"])
        if not claimed:
            return action
        return await execute_claimed_action(
            action,
            gmail_service=gmail_service,
            calendar_service=calendar_service,
            tasks_service=tasks_service,
        )
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
):
    try:
        agent = ExecutiveAgent(user_id=str(user_info["user_id"]))
        result = await agent.run(
            input_query=query.input,
            gmail_service=gmail_service,
            calendar_service=calendar_service,
            tasks_service=tasks_service,
            current_user_email=str(user_info["email"]),
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


@router.post("/generate-stream/")
async def invoke_general_agent_stream_endpoint(
    query: AgentQuery,
    user_info: dict = Depends(get_current_user_info),
    gmail_service: Resource = Depends(get_gmail_service),
    tasks_service: Resource = Depends(get_tasks_service),
):
    """Stream agent events as JSON payloads in Server-Sent Event data frames."""
    try:
        agent = GeneralAgentStreamer(user_id=str(user_info["user_id"]))
        user_email = str(user_info["email"])

        async def stream_response_generator():
            try:
                async for event_type, content in agent.run(
                    input_query=query.input,
                    gmail_service=gmail_service,
                    tasks_service=tasks_service,
                    current_user_email=user_email,
                ):
                    yield f"data: {json.dumps({'event': event_type, 'content': content})}\n\n"
                    if event_type == "error":
                        return
                yield f"data: {json.dumps({'event': 'done', 'content': ''})}\n\n"
            except Exception:
                logger.exception("Error while streaming agent response for user %s", user_info.get("user_id"))
                yield f"data: {json.dumps({'event': 'error', 'content': 'The agent stream failed unexpectedly.'})}\n\n"

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
