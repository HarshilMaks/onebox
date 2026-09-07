import json
import logging
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from googleapiclient.discovery import Resource

from agents import ExecutiveAgent, GeneralAgent, GeneralAgentStreamer
from server.schemas import AgentErrorResponse, AgentSuccessResponse
from server.services.setup_google import (
    get_current_user_info,
    get_gmail_service,
    get_calendar_service,
    get_tasks_service
)

logger = logging.getLogger(__name__)
router = APIRouter( tags=["AI Agents"]) # Example prefix

class AgentQuery(BaseModel):
    input: str

@router.post("/executive/", response_model=AgentSuccessResponse)
async def invoke_executive_agent_endpoint(
    query: AgentQuery,
    user_info: dict = Depends(get_current_user_info),
    gmail_service: Resource = Depends(get_gmail_service),
    calendar_service: Resource = Depends(get_calendar_service),
    tasks_service: Resource = Depends(get_tasks_service)
):
    try:
        user_id_str = str(user_info["user_id"])
        email_str = str(user_info["email"])
        agent = ExecutiveAgent(user_id=user_id_str)
        result = await agent.run(
            input_query=query.input,
            gmail_service=gmail_service,
            calendar_service=calendar_service,
            tasks_service=tasks_service,
            current_user_email=email_str
        )
        return {"result": result}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception(f"Error in executive agent endpoint for user {user_info.get('user_id')}: {e}")
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
    user_info: dict = Depends(get_current_user_info)
):
    try:
        user_id_str = str(user_info["user_id"])
        agent = GeneralAgent(user_id=user_id_str)
        result = await agent.run(input_query=query.input)
        return {"result": result}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception(f"Error in general agent endpoint for user {user_info.get('user_id')}: {e}")
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
    tasks_service: Resource = Depends(get_tasks_service)
):
    """Streams the agent's response as Server-Sent Events.

    Each event is a JSON-encoded line prefixed with `data: `, containing
    {"event": <token|tool_result|error|done>, "content": <str>}. Clients
    should stop reading after an `error` or `done` event.
    """
    try:
        user_id_str = str(user_info["user_id"])
        email_str = str(user_info["email"])
        agent = GeneralAgentStreamer(user_id=user_id_str)

        async def stream_response_generator():
            try:
                async for event_type, content in agent.run(
                    input_query=query.input,
                    gmail_service=gmail_service,
                    tasks_service=tasks_service,
                    current_user_email=email_str
                ):
                    yield f"data: {json.dumps({'event': event_type, 'content': content})}\n\n"
                    if event_type == "error":
                        return
                yield f"data: {json.dumps({'event': 'done', 'content': ''})}\n\n"
            except Exception as e:
                logger.exception(f"Error while streaming agent response for user {user_info.get('user_id')}: {e}")
                yield f"data: {json.dumps({'event': 'error', 'content': 'The agent stream failed unexpectedly.'})}\n\n"

        return StreamingResponse(stream_response_generator(), media_type="text/event-stream")
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception(f"Error in general agent stream endpoint for user {user_info.get('user_id')}: {e}")
        raise HTTPException(
            status_code=500,
            detail=AgentErrorResponse(
                error="stream_start_failed",
                detail="Could not start the agent stream.",
            ).model_dump(),
        )