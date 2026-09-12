"""Typed, server-owned Gemini tool registry and trusted callable bindings."""

from __future__ import annotations

from functools import partial
from typing import Any, Callable, Optional

from google.genai.types import FunctionDeclaration, Schema, Tool, Type
from googleapiclient.discovery import Resource

from server.agent_policy import AgentTool, AgentToolPolicy
from server.services.pending_actions import issue_command_key
from tools.llm_tools import (
    create_draft,
    create_event,
    create_task,
    get_calendar_events,
    mark_as_read,
    send_email,
    send_reply_to_user,
)


PENDING_ACTION_TOOL_NAMES = frozenset(
    {AgentTool.SEND_EMAIL.value, AgentTool.SEND_REPLY.value, AgentTool.CREATE_EVENT.value, AgentTool.CREATE_TASK.value}
)


send_email_func_decl = FunctionDeclaration(
    name=AgentTool.SEND_EMAIL.value,
    description="Create one typed email approval action. This tool never sends mail.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "recipient_email": Schema(type=Type.STRING),
            "subject": Schema(type=Type.STRING),
            "email_body": Schema(type=Type.STRING),
        },
        required=["recipient_email", "subject", "email_body"],
    ),
)
create_draft_func_decl = FunctionDeclaration(
    name=AgentTool.CREATE_DRAFT.value,
    description="Create one interactive Gmail draft for review. This tool never sends mail.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "recipient_email": Schema(type=Type.STRING),
            "subject": Schema(type=Type.STRING),
            "email_body": Schema(type=Type.STRING),
        },
        required=["recipient_email", "subject", "email_body"],
    ),
)
create_event_func_decl = FunctionDeclaration(
    name=AgentTool.CREATE_EVENT.value,
    description="Create one typed calendar-event approval action. This tool never creates an event.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "title": Schema(type=Type.STRING),
            "start_time_iso": Schema(type=Type.STRING),
            "end_time_iso": Schema(type=Type.STRING),
            "event_timezone": Schema(type=Type.STRING),
            "description": Schema(type=Type.STRING),
            "location": Schema(type=Type.STRING),
            "attendee_emails": Schema(type=Type.ARRAY, items=Schema(type=Type.STRING)),
        },
        required=["title", "start_time_iso", "end_time_iso", "event_timezone"],
    ),
)
create_task_func_decl = FunctionDeclaration(
    name=AgentTool.CREATE_TASK.value,
    description="Create one typed task approval action. This tool never creates a task.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={"title": Schema(type=Type.STRING), "notes": Schema(type=Type.STRING)},
        required=["title", "notes"],
    ),
)
mark_as_read_func_decl = FunctionDeclaration(
    name=AgentTool.MARK_AS_READ.value,
    description="Mark one Gmail message as read during an interactive run.",
    parameters=Schema(type=Type.OBJECT, properties={"message_id": Schema(type=Type.STRING)}, required=["message_id"]),
)
send_reply_to_user_func_decl = FunctionDeclaration(
    name=AgentTool.SEND_REPLY.value,
    description="Create one typed reply approval action. This tool never sends a reply.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "recipient_email": Schema(type=Type.STRING),
            "subject_filter": Schema(type=Type.STRING),
            "reply_message": Schema(type=Type.STRING),
        },
        required=["recipient_email", "subject_filter", "reply_message"],
    ),
)
get_calendar_events_func_decl = FunctionDeclaration(
    name=AgentTool.GET_CALENDAR_EVENTS.value,
    description="Read calendar events for supplied dates without modifying the calendar.",
    parameters=Schema(
        type=Type.OBJECT,
        properties={
            "date_strs": Schema(type=Type.ARRAY, items=Schema(type=Type.STRING)),
            "target_timezone": Schema(type=Type.STRING),
        },
        required=["date_strs"],
    ),
)
TOOL_DECLARATIONS: dict[str, FunctionDeclaration] = {
    AgentTool.SEND_EMAIL.value: send_email_func_decl,
    AgentTool.CREATE_DRAFT.value: create_draft_func_decl,
    AgentTool.CREATE_EVENT.value: create_event_func_decl,
    AgentTool.CREATE_TASK.value: create_task_func_decl,
    AgentTool.MARK_AS_READ.value: mark_as_read_func_decl,
    AgentTool.SEND_REPLY.value: send_reply_to_user_func_decl,
    AgentTool.GET_CALENDAR_EVENTS.value: get_calendar_events_func_decl,
}


def command_key_for_tool_call(command_keys: dict[str, str], *, scope: str, function_call: Any) -> str:
    """Assign one opaque server command key to a provider-delivered call."""
    provider_call_id = getattr(function_call, "id", None)
    identity = f"provider:{provider_call_id}" if isinstance(provider_call_id, str) and provider_call_id else scope
    return command_keys.setdefault(identity, issue_command_key())


def _tool_objects(policy: AgentToolPolicy, bindings: dict[str, Callable[..., Any]]) -> tuple[dict[str, Callable[..., Any]], list[Tool]]:
    allowed: dict[str, Callable[..., Any]] = {}
    declarations: list[FunctionDeclaration] = []
    for tool in policy.allowed_tools:
        name = tool.value
        if name in bindings:
            allowed[name] = bindings[name]
            declarations.append(TOOL_DECLARATIONS[name])
    return allowed, [Tool(function_declarations=declarations)] if declarations else []


def bind_executive_tools(
    *,
    policy: AgentToolPolicy,
    user_id: str,
    gmail_service: Optional[Resource],
    calendar_service: Optional[Resource],
    tasks_service: Optional[Resource],
    current_user_email: Optional[str],
) -> tuple[dict[str, Callable[..., Any]], list[Tool]]:
    """Bind only route-authorized tools whose account services are available."""
    bindings: dict[str, Callable[..., Any]] = {}
    if gmail_service and current_user_email:
        bindings.update(
            {
                AgentTool.CREATE_DRAFT.value: partial(create_draft, gmail_service, current_user_email),
                AgentTool.MARK_AS_READ.value: partial(mark_as_read, gmail_service),
                AgentTool.SEND_EMAIL.value: partial(send_email, user_id, current_user_email),
                AgentTool.SEND_REPLY.value: partial(send_reply_to_user, gmail_service, user_id, current_user_email),
            }
        )
    if calendar_service:
        bindings.update(
            {
                AgentTool.CREATE_EVENT.value: partial(create_event, user_id),
                AgentTool.GET_CALENDAR_EVENTS.value: partial(get_calendar_events, calendar_service),
            }
        )
    if tasks_service:
        bindings[AgentTool.CREATE_TASK.value] = partial(create_task, user_id)
    return _tool_objects(policy, bindings)


def bind_stream_tools(
    *, policy: AgentToolPolicy, user_id: str, current_user_email: Optional[str]
) -> tuple[dict[str, Callable[..., Any]], list[Tool]]:
    """Bind the deliberately smaller interactive streaming registry."""
    bindings: dict[str, Callable[..., Any]] = {AgentTool.CREATE_TASK.value: partial(create_task, user_id)}
    if current_user_email:
        bindings[AgentTool.SEND_EMAIL.value] = partial(send_email, user_id, current_user_email)
    return _tool_objects(policy, bindings)
