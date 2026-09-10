"""Server-governed Gemini agents for interactive and automated workflows."""

from __future__ import annotations

from asyncio import CancelledError
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from google.genai.types import Content, FunctionDeclaration, GenerateContentConfig, Part, Schema, Tool, Type
from googleapiclient.discovery import Resource

from clients.base import Agent
from clients.prompt import EXECUTIVE_AGENT_PROMPT, build_general_agent_prompt
from server.agent_policy import (
    INTERACTIVE_EXECUTIVE_POLICY,
    INTERACTIVE_STREAM_POLICY,
    AgentTool,
    AgentToolPolicy,
)
from server.config import PROJECT_ROOT
from server.integrations.llm import LlmOperationInternal, LlmOperationTimeout, LlmOperationUnavailable
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


logger = __import__("logging").getLogger(__name__)

_PENDING_ACTION_TOOLS = frozenset(
    {AgentTool.SEND_EMAIL.value, AgentTool.SEND_REPLY.value, AgentTool.CREATE_EVENT.value, AgentTool.CREATE_TASK.value}
)


@dataclass(frozen=True)
class UserProfile:
    full_name: str
    title: str
    timezone_name: str
    priority_contacts: tuple[str, ...]
    background: str
    schedule_preferences: str
    response_preferences: str


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load the single-instance deployment profile independently of process CWD."""
    path = Path(config_path) if config_path is not None else PROJECT_ROOT / "user_config.yaml"
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        with path.open(encoding="utf-8") as file:
            raw = yaml.safe_load(file)
    except FileNotFoundError:
        logger.warning("Agent profile is unavailable")
        return {}
    except yaml.YAMLError:
        logger.warning("Agent profile is invalid", exc_info=True)
        return {}
    return raw if isinstance(raw, dict) else {}


def _text(value: object, *, fallback: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _profile_from_config(config: dict[str, Any]) -> UserProfile:
    contacts = config.get("important_contacts")
    return UserProfile(
        full_name=_text(config.get("full_name"), fallback="Account owner"),
        title=_text(config.get("title"), fallback=""),
        timezone_name=_text(config.get("timezone"), fallback="UTC"),
        priority_contacts=tuple(value for value in contacts if isinstance(value, str)) if isinstance(contacts, list) else (),
        background=_text(config.get("background"), fallback="No profile background provided."),
        schedule_preferences=_text(config.get("schedule_preferences"), fallback="No scheduling preferences provided."),
        response_preferences=_text(config.get("response_preferences"), fallback="Be concise and professional."),
    )


def _profile_timezone(profile: UserProfile) -> ZoneInfo:
    try:
        return ZoneInfo(profile.timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("Agent profile has an invalid timezone; using UTC")
        return ZoneInfo("UTC")


def _now_in_timezone(clock: Callable[[], datetime], zone: ZoneInfo) -> datetime:
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(zone)


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
    parameters=Schema(
        type=Type.OBJECT,
        properties={"message_id": Schema(type=Type.STRING)},
        required=["message_id"],
    ),
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
_TOOL_DECLARATIONS: dict[str, FunctionDeclaration] = {
    AgentTool.SEND_EMAIL.value: send_email_func_decl,
    AgentTool.CREATE_DRAFT.value: create_draft_func_decl,
    AgentTool.CREATE_EVENT.value: create_event_func_decl,
    AgentTool.CREATE_TASK.value: create_task_func_decl,
    AgentTool.MARK_AS_READ.value: mark_as_read_func_decl,
    AgentTool.SEND_REPLY.value: send_reply_to_user_func_decl,
    AgentTool.GET_CALENDAR_EVENTS.value: get_calendar_events_func_decl,
}


def _command_key_for_tool_call(command_keys: dict[str, str], *, scope: str, function_call: Any) -> str:
    provider_call_id = getattr(function_call, "id", None)
    identity = f"provider:{provider_call_id}" if isinstance(provider_call_id, str) and provider_call_id else scope
    return command_keys.setdefault(identity, issue_command_key())


def _tool_response_part(name: str, response: dict[str, object]) -> Part:
    return Part(function_response={"name": name, "response": response})


def _candidate_function_calls(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) or []
    return [part.function_call for part in parts if getattr(part, "function_call", None)]


def _candidate_text(response: Any) -> str:
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        text = "".join(part.text for part in parts if isinstance(getattr(part, "text", None), str))
        if text:
            return text
    text = getattr(response, "text", None)
    return text if isinstance(text, str) and text else "I could not generate a response."


class ExecutiveAgent(Agent):
    def __init__(
        self,
        user_id: str,
        model_name: str | None = None,
        *,
        provider: Any = None,
        config_path: str | Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        super().__init__(model_name, provider=provider)
        self.user_id = user_id
        self._config_path = config_path
        self._clock = clock
        self.available_python_tools: Dict[str, Callable[..., Any]] = {}
        self._tool_command_keys: dict[str, str] = {}

    def _prepare_tool_objects_and_python_callables(
        self,
        *,
        policy: AgentToolPolicy,
        gmail_service: Optional[Resource],
        calendar_service: Optional[Resource],
        tasks_service: Optional[Resource],
        current_user_email: Optional[str],
    ) -> list[Tool]:
        """Bind only server-policy tools whose required account services exist."""
        self.available_python_tools = {}
        bindings: dict[str, Callable[..., Any]] = {}
        if gmail_service and current_user_email:
            bindings.update(
                {
                    AgentTool.CREATE_DRAFT.value: partial(create_draft, gmail_service, current_user_email),
                    AgentTool.MARK_AS_READ.value: partial(mark_as_read, gmail_service),
                    AgentTool.SEND_EMAIL.value: partial(send_email, self.user_id, current_user_email),
                    AgentTool.SEND_REPLY.value: partial(send_reply_to_user, gmail_service, self.user_id, current_user_email),
                }
            )
        if calendar_service:
            bindings.update(
                {
                    AgentTool.CREATE_EVENT.value: partial(create_event, self.user_id),
                    AgentTool.GET_CALENDAR_EVENTS.value: partial(get_calendar_events, calendar_service),
                }
            )
        if tasks_service:
            bindings[AgentTool.CREATE_TASK.value] = partial(create_task, self.user_id)

        declarations: list[FunctionDeclaration] = []
        for tool in policy.allowed_tools:
            name = tool.value
            if name in bindings:
                self.available_python_tools[name] = bindings[name]
                declarations.append(_TOOL_DECLARATIONS[name])
        return [Tool(function_declarations=declarations)] if declarations else []

    async def _execute_tool_calls(
        self,
        function_calls: Iterable[Any],
        *,
        policy: AgentToolPolicy,
        turn: int,
    ) -> tuple[list[Part], str | None]:
        responses: list[Part] = []
        mutation_count = getattr(self, "_mutation_count", 0)
        for index, function_call in enumerate(function_calls):
            name = getattr(function_call, "name", "")
            if not isinstance(name, str) or not policy.allows(name) or name not in self.available_python_tools:
                responses.append(_tool_response_part(name if isinstance(name, str) else "unknown", {"error": "tool_not_authorized"}))
                continue
            if policy.is_primary_mutation(name):
                if mutation_count >= policy.max_primary_mutations:
                    responses.append(_tool_response_part(name, {"error": "mutation_limit_reached"}))
                    continue
                mutation_count += 1
                self._mutation_count = mutation_count

            args = dict(getattr(function_call, "args", None) or {})
            args.pop("command_key", None)
            hidden_kwargs: dict[str, str] = {}
            if name in _PENDING_ACTION_TOOLS:
                hidden_kwargs["command_key"] = _command_key_for_tool_call(
                    self._tool_command_keys,
                    scope=f"executive:{turn}:{index}:{name}",
                    function_call=function_call,
                )
            try:
                result = self.available_python_tools[name](**args, **hidden_kwargs)
                if not hasattr(result, "__await__"):
                    raise RuntimeError("Agent tool must be asynchronous")
                result = await result
            except CancelledError:
                raise
            except Exception:
                logger.exception("Agent tool execution failed")
                responses.append(_tool_response_part(name, {"error": "tool_unavailable"}))
                continue

            if isinstance(result, dict) and result.get("status") == "pending_approval":
                action_id = result.get("action_id")
                return responses, f"Approval required. Approve action {action_id} to continue."
            if name == AgentTool.CREATE_DRAFT.value:
                return responses, "Draft created for your review."
            if name == AgentTool.MARK_AS_READ.value:
                return responses, "Message marked as read."
            responses.append(_tool_response_part(name, {"result": result}))
        return responses, None

    async def run(
        self,
        input_query: str,
        gmail_service: Optional[Resource] = None,
        calendar_service: Optional[Resource] = None,
        tasks_service: Optional[Resource] = None,
        current_user_email: Optional[str] = None,
        *,
        policy: AgentToolPolicy = INTERACTIVE_EXECUTIVE_POLICY,
    ) -> str:
        self._tool_command_keys = {}
        self._mutation_count = 0
        profile = _profile_from_config(load_config(self._config_path))
        now = _now_in_timezone(self._clock, _profile_timezone(profile))
        prompt = EXECUTIVE_AGENT_PROMPT.format(
            user_full_name=profile.full_name,
            user_title=profile.title,
            current_date_time=now.isoformat(),
            user_timezone=now.tzinfo.key if isinstance(now.tzinfo, ZoneInfo) else profile.timezone_name,
            priority_contacts_str=", ".join(profile.priority_contacts) or "None",
            user_background=profile.background,
            user_schedule_preferences=profile.schedule_preferences,
            user_response_preferences=profile.response_preferences,
        )
        tools = self._prepare_tool_objects_and_python_callables(
            policy=policy,
            gmail_service=gmail_service,
            calendar_service=calendar_service,
            tasks_service=tasks_service,
            current_user_email=current_user_email,
        )
        history: list[Content] = [Content(parts=[Part(text=input_query)], role="user")]
        config = GenerateContentConfig(
            temperature=0.0,
            tools=tools or None,
            system_instruction=Content(parts=[Part(text=prompt)]),
        )
        for turn in range(5):
            response = await self.provider.generate(model=self.model_name, contents=history, config=config)
            function_calls = _candidate_function_calls(response)
            if not function_calls:
                return _candidate_text(response)
            candidate = response.candidates[0]
            history.append(candidate.content)
            function_responses, terminal_message = await self._execute_tool_calls(function_calls, policy=policy, turn=turn)
            if terminal_message is not None:
                return terminal_message
            history.append(Content(parts=function_responses, role="tool"))
        return "I could not complete the requested action. Please try again with one clear request."


class GeneralAgent(Agent):
    def __init__(
        self,
        user_id: str,
        model_name: str | None = None,
        *,
        provider: Any = None,
        config_path: str | Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        super().__init__(model_name, provider=provider)
        self.user_id = user_id
        self._config_path = config_path
        self._clock = clock

    async def run(self, input_query: str, system_prompt: str | None = None) -> str:
        profile = _profile_from_config(load_config(self._config_path))
        now = _now_in_timezone(self._clock, _profile_timezone(profile))
        prompt = system_prompt or build_general_agent_prompt(current_time=now, timezone_name=profile.timezone_name)
        response = await self.provider.generate(
            model=self.model_name,
            contents=[Content(parts=[Part(text=input_query)], role="user")],
            config=GenerateContentConfig(temperature=0.0, system_instruction=Content(parts=[Part(text=prompt)])),
        )
        return _candidate_text(response)


class GeneralAgentStreamer(Agent):
    def __init__(
        self,
        user_id: str,
        model_name: str | None = None,
        *,
        provider: Any = None,
        config_path: str | Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        super().__init__(model_name, provider=provider)
        self.user_id = user_id
        self._config_path = config_path
        self._clock = clock
        self.available_python_tools: Dict[str, Callable[..., Any]] = {}
        self._tool_command_keys: dict[str, str] = {}

    def _prepare_tool_objects_and_python_callables(
        self, *, policy: AgentToolPolicy, current_user_email: Optional[str]
    ) -> list[Tool]:
        self.available_python_tools = {}
        bindings: dict[str, Callable[..., Any]] = {
            AgentTool.CREATE_TASK.value: partial(create_task, self.user_id),
        }
        if current_user_email:
            bindings[AgentTool.SEND_EMAIL.value] = partial(send_email, self.user_id, current_user_email)
        declarations: list[FunctionDeclaration] = []
        for tool in policy.allowed_tools:
            name = tool.value
            if name in bindings:
                self.available_python_tools[name] = bindings[name]
                declarations.append(_TOOL_DECLARATIONS[name])
        return [Tool(function_declarations=declarations)] if declarations else []

    async def run(
        self,
        input_query: str,
        current_user_email: Optional[str] = None,
        *,
        policy: AgentToolPolicy = INTERACTIVE_STREAM_POLICY,
        system_prompt: str | None = None,
    ) -> AsyncGenerator[tuple[str, str, Optional[str]], None]:
        self._tool_command_keys = {}
        profile = _profile_from_config(load_config(self._config_path))
        now = _now_in_timezone(self._clock, _profile_timezone(profile))
        prompt = system_prompt or build_general_agent_prompt(current_time=now, timezone_name=profile.timezone_name)
        tools = self._prepare_tool_objects_and_python_callables(policy=policy, current_user_email=current_user_email)
        history: list[Content] = [Content(parts=[Part(text=input_query)], role="user")]
        config = GenerateContentConfig(
            temperature=0.0,
            tools=tools or None,
            system_instruction=Content(parts=[Part(text=prompt)]),
        )
        try:
            function_calls: list[Any] = []
            async for chunk in self.provider.iter_stream(model=self.model_name, contents=history, config=config):
                candidates = getattr(chunk, "candidates", None) or []
                content = getattr(candidates[0], "content", None) if candidates else None
                for part in getattr(content, "parts", None) or []:
                    if getattr(part, "function_call", None):
                        function_calls.append(part.function_call)
                if not function_calls:
                    text = getattr(chunk, "text", None)
                    if isinstance(text, str) and text:
                        yield "token", text, None

            if not function_calls:
                return
            # A streamed model response may contain more than one function call;
            # execute only the first policy-authorized call. Later calls cannot
            # broaden authorization or produce a second mutation.
            function_call = function_calls[0]
            name = getattr(function_call, "name", "")
            if not isinstance(name, str) or not policy.allows(name) or name not in self.available_python_tools:
                yield "error", "The requested tool is not available for this run.", "tool_not_authorized"
                return
            if not policy.is_primary_mutation(name) or policy.max_primary_mutations < 1:
                yield "error", "The requested tool is not available for this run.", "tool_not_authorized"
                return
            args = dict(getattr(function_call, "args", None) or {})
            args.pop("command_key", None)
            hidden_kwargs = {
                "command_key": _command_key_for_tool_call(
                    self._tool_command_keys,
                    scope=f"stream:{name}",
                    function_call=function_call,
                )
            }
            try:
                result = self.available_python_tools[name](**args, **hidden_kwargs)
                if not hasattr(result, "__await__"):
                    raise RuntimeError("Agent tool must be asynchronous")
                result = await result
            except CancelledError:
                raise
            except Exception:
                logger.exception("Streamed tool execution failed")
                yield "error", "The requested tool could not complete. Please try again.", "tool_unavailable"
                return

            if isinstance(result, dict) and result.get("status") == "pending_approval":
                action_id = result.get("action_id")
                yield "tool_result", f"Approval required. Approve action {action_id} to continue.", None
                return
            yield "tool_result", "The requested tool completed.", None
        except CancelledError:
            raise
        except LlmOperationTimeout:
            yield "error", "The agent response timed out. Please try again.", "llm_timeout"
        except LlmOperationUnavailable:
            yield "error", "The agent is temporarily unavailable.", "llm_unavailable"
        except LlmOperationInternal:
            logger.exception("General agent stream failed")
            yield "error", "The agent could not complete the stream. Please try again.", "llm_internal"
