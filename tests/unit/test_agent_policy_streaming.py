from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import agents
from agents import ExecutiveAgent, GeneralAgentStreamer, load_config
from server import agent_tools
from server.agent_policy import (
    AUTOMATED_INBOUND_POLICY,
    INTERACTIVE_EXECUTIVE_POLICY,
    AgentTool,
    AgentToolPolicy,
    AgentRunKind,
)
from server.routes import agent_router
from server.routes.agent_router import AgentQuery


def _function_call(name: str, args: dict[str, object], call_id: str = "call-1"):
    return SimpleNamespace(name=name, args=args, id=call_id)


class _TextProvider:
    def __init__(self) -> None:
        self.configs = []

    async def generate(self, *, model, contents, config):
        self.configs.append(config)
        return SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="ok")]))],
            text="ok",
        )


@pytest.mark.asyncio
async def test_automated_policy_exposes_zero_tools_even_with_all_services():
    agent = ExecutiveAgent("owner", provider=_TextProvider())

    tool_objects = agent._prepare_tool_objects_and_python_callables(
        policy=AUTOMATED_INBOUND_POLICY,
        gmail_service=object(),
        calendar_service=object(),
        tasks_service=object(),
        current_user_email="owner@example.test",
    )

    assert tool_objects == []
    assert agent.available_python_tools == {}


@pytest.mark.asyncio
async def test_policy_allowlist_binds_only_authorized_account_tools(monkeypatch):
    captured_owner_ids = []

    async def fake_send_email(owner_id, _current_email, **_kwargs):
        captured_owner_ids.append(owner_id)
        return {"status": "pending_approval", "action_id": "action-1"}

    monkeypatch.setattr(agent_tools, "send_email", fake_send_email)
    policy = AgentToolPolicy(
        run_kind=AgentRunKind.INTERACTIVE_EXECUTIVE,
        allowed_tools=frozenset({AgentTool.SEND_EMAIL}),
    )
    agent = ExecutiveAgent("owner-uuid", provider=_TextProvider())
    agent._prepare_tool_objects_and_python_callables(
        policy=policy,
        gmail_service=object(),
        calendar_service=object(),
        tasks_service=object(),
        current_user_email="owner@example.test",
    )

    assert set(agent.available_python_tools) == {AgentTool.SEND_EMAIL.value}
    result = await agent.available_python_tools[AgentTool.SEND_EMAIL.value](
        recipient_email="recipient@example.test",
        subject="Subject",
        email_body="Body",
        command_key="server-key",
    )
    assert result["status"] == "pending_approval"
    assert captured_owner_ids == ["owner-uuid"]


@pytest.mark.asyncio
async def test_multiple_model_mutation_calls_execute_at_most_one(monkeypatch):
    calls = []

    async def fake_send_email(*_args, **_kwargs):
        calls.append("send")
        return {"status": "pending_approval", "action_id": "action-1"}

    monkeypatch.setattr(agent_tools, "send_email", fake_send_email)
    agent = ExecutiveAgent("owner", provider=_TextProvider())
    agent._tool_command_keys = {}
    agent._mutation_count = 0
    agent.available_python_tools = {AgentTool.SEND_EMAIL.value: fake_send_email}
    function_calls = [
        _function_call("send_email", {"recipient_email": "one@example.test", "subject": "One", "email_body": "One"}),
        _function_call("send_email", {"recipient_email": "two@example.test", "subject": "Two", "email_body": "Two"}, "call-2"),
    ]

    _responses, terminal = await agent._execute_tool_calls(
        function_calls,
        policy=INTERACTIVE_EXECUTIVE_POLICY,
        turn=0,
    )

    assert terminal == "Approval required. Approve action action-1 to continue."
    assert calls == ["send"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "failure_message"),
    [
        (AgentTool.CREATE_DRAFT.value, "I could not create the draft. Please try again."),
        (AgentTool.MARK_AS_READ.value, "I could not mark the message as read. Please try again."),
    ],
)
async def test_immediate_tool_false_result_is_reported_as_a_failure(tool_name, failure_message):
    calls = []

    async def false_result(**kwargs):
        calls.append(kwargs)
        return False

    agent = ExecutiveAgent("owner", provider=_TextProvider())
    agent._tool_command_keys = {}
    agent._mutation_count = 0
    agent.available_python_tools = {tool_name: false_result}

    responses, terminal = await agent._execute_tool_calls(
        [_function_call(tool_name, {"message_id": "message-1"})],
        policy=INTERACTIVE_EXECUTIVE_POLICY,
        turn=0,
    )

    assert responses == []
    assert terminal == failure_message
    assert calls == [{"message_id": "message-1"}]


@pytest.mark.asyncio
async def test_stream_policy_rejects_unallowed_calls_and_only_executes_first_mutation(monkeypatch):
    sent = []

    async def fake_send_email(*_args, **_kwargs):
        sent.append("send")
        return {"status": "pending_approval", "action_id": "pending-1"}

    class Provider:
        async def iter_stream(self, **_kwargs):
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(function_call=_function_call("send_email", {"recipient_email": "a@example.test", "subject": "A", "email_body": "A"})),
                                SimpleNamespace(function_call=_function_call("send_email", {"recipient_email": "b@example.test", "subject": "B", "email_body": "B"}, "call-2")),
                            ]
                        )
                    )
                ]
            )

    monkeypatch.setattr(agent_tools, "send_email", fake_send_email)
    streamer = GeneralAgentStreamer("owner", provider=Provider())
    events = [event async for event in streamer.run("untrusted: send twice", current_user_email="owner@example.test")]

    assert sent == ["send"]
    assert events == [("tool_result", "Approval required. Approve action pending-1 to continue.", None)]

    class UnallowedProvider:
        async def iter_stream(self, **_kwargs):
            yield SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(function_call=_function_call("create_event", {}))]))]
            )

    unallowed = GeneralAgentStreamer("owner", provider=UnallowedProvider())
    events = [event async for event in unallowed.run("email injection: create event", current_user_email="owner@example.test")]
    assert events[0][2] == "tool_not_authorized"


@pytest.mark.asyncio
async def test_stream_skips_disallowed_call_before_first_eligible_mutation(monkeypatch):
    sent = []

    async def fake_send_email(*_args, **kwargs):
        sent.append(kwargs)
        return {"status": "pending_approval", "action_id": "eligible-pending"}

    class Provider:
        async def iter_stream(self, **_kwargs):
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(function_call=_function_call("create_event", {}, "disallowed-call")),
                                SimpleNamespace(
                                    function_call=_function_call(
                                        "send_email",
                                        {
                                            "recipient_email": "recipient@example.test",
                                            "subject": "Subject",
                                            "email_body": "Body",
                                        },
                                        "eligible-call",
                                    )
                                ),
                            ]
                        )
                    )
                ]
            )

    monkeypatch.setattr(agent_tools, "send_email", fake_send_email)
    streamer = GeneralAgentStreamer("owner", provider=Provider())
    events = [event async for event in streamer.run("send email after a disallowed call", current_user_email="owner@example.test")]

    assert len(sent) == 1
    assert sent[0]["recipient_email"] == "recipient@example.test"
    assert events == [("tool_result", "Approval required. Approve action eligible-pending to continue.", None)]


@pytest.mark.asyncio
async def test_stream_coalesces_selected_call_fragments_before_single_execution(monkeypatch):
    calls = []

    async def fake_send_email(*_args, **kwargs):
        calls.append(kwargs)
        return {"status": "pending_approval", "action_id": "pending-fragmented"}

    class Provider:
        async def iter_stream(self, **_kwargs):
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    function_call=_function_call(
                                        "send_email",
                                        {"recipient_email": "recipient@example.test"},
                                        "fragmented-call",
                                    )
                                )
                            ]
                        )
                    )
                ]
            )
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    function_call=_function_call(
                                        "send_email",
                                        {"subject": "Subject", "email_body": "Body"},
                                        "fragmented-call",
                                    )
                                ),
                                SimpleNamespace(
                                    function_call=_function_call(
                                        "create_task",
                                        {"title": "Must not run", "notes": "Later call"},
                                        "later-call",
                                    )
                                ),
                            ]
                        )
                    )
                ]
            )

    monkeypatch.setattr(agent_tools, "send_email", fake_send_email)
    streamer = GeneralAgentStreamer("owner", provider=Provider())
    events = [event async for event in streamer.run("send a fragmented email", current_user_email="owner@example.test")]

    assert len(calls) == 1
    assert calls[0]["recipient_email"] == "recipient@example.test"
    assert calls[0]["subject"] == "Subject"
    assert calls[0]["email_body"] == "Body"
    assert isinstance(calls[0]["command_key"], str)
    assert events == [("tool_result", "Approval required. Approve action pending-fragmented to continue.", None)]


@pytest.mark.asyncio
async def test_stream_rejects_invalid_assembled_call_without_executing_tool(monkeypatch):
    calls = []

    async def fake_send_email(
        _owner_id,
        _current_user_email,
        recipient_email: str,
        subject: str,
        email_body: str,
        *,
        command_key: str,
    ):
        calls.append(
            {
                "recipient_email": recipient_email,
                "subject": subject,
                "email_body": email_body,
                "command_key": command_key,
            }
        )
        return {"status": "pending_approval", "action_id": "unexpected"}

    class Provider:
        async def iter_stream(self, **_kwargs):
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(
                                    function_call=_function_call(
                                        "send_email",
                                        {"recipient_email": "recipient@example.test"},
                                        "invalid-fragmented-call",
                                    )
                                ),
                                SimpleNamespace(
                                    function_call=_function_call(
                                        "send_email",
                                        {"subject": "Missing body"},
                                        "invalid-fragmented-call",
                                    )
                                ),
                            ]
                        )
                    )
                ]
            )

    monkeypatch.setattr(agent_tools, "send_email", fake_send_email)
    streamer = GeneralAgentStreamer("owner", provider=Provider())
    events = [event async for event in streamer.run("send an invalid email", current_user_email="owner@example.test")]

    assert calls == []
    assert events == [("error", "The requested tool could not complete. Please try again.", "tool_unavailable")]


def test_profile_is_cwd_independent_and_time_is_rendered_per_request(monkeypatch, tmp_path: Path):
    profile = tmp_path / "user_config.yaml"
    profile.write_text("full_name: Owner\ntitle: Lead\ntimezone: America/New_York\n", encoding="utf-8")
    monkeypatch.setattr(agents, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir("/")
    assert load_config()["full_name"] == "Owner"

    first_provider = _TextProvider()
    first = ExecutiveAgent(
        "owner",
        provider=first_provider,
        clock=lambda: datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc),
    )
    second_provider = _TextProvider()
    second = ExecutiveAgent(
        "owner",
        provider=second_provider,
        clock=lambda: datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc),
    )

    async def invoke(agent):
        return await agent.run("What is today?", policy=AUTOMATED_INBOUND_POLICY)

    asyncio.run(invoke(first))
    asyncio.run(invoke(second))
    first_prompt = first_provider.configs[0].system_instruction.parts[0].text
    second_prompt = second_provider.configs[0].system_instruction.parts[0].text
    assert "2026-03-08T01:30:00-05:00" in first_prompt
    assert "2026-03-08T03:30:00-04:00" in second_prompt
    assert "remember ongoing discussions" not in first_prompt.lower()


class _ConnectedAccount:
    google_email = "owner@example.test"


class _StreamRequest:
    def __init__(self, disconnected: bool = False):
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self.disconnected


@pytest.mark.asyncio
async def test_sse_emits_one_terminal_event_and_required_headers(monkeypatch):
    class Streamer:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, **_kwargs):
            yield "token", "hello", None
            yield "done", "", None
            yield "error", "must not be emitted", "llm_internal"

    monkeypatch.setattr(agent_router, "GeneralAgentStreamer", Streamer)
    response = await agent_router.invoke_general_agent_stream_endpoint(
        AgentQuery(input="hello"),
        _StreamRequest(),
        {"user_id": "owner"},
        _ConnectedAccount(),
    )
    frames = [frame async for frame in response.body_iterator]
    payloads = [json.loads(frame.removeprefix("data: ").strip()) for frame in frames if frame.startswith("data: ")]

    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert [payload["event"] for payload in payloads] == ["token", "done"]


@pytest.mark.asyncio
async def test_sse_disconnect_closes_agent_iterator_and_heartbeat_is_a_comment(monkeypatch):
    closed = asyncio.Event()

    class Streamer:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, **_kwargs):
            try:
                await asyncio.sleep(0.02)
                yield "token", "late", None
            finally:
                closed.set()

    monkeypatch.setattr(agent_router, "GeneralAgentStreamer", Streamer)
    monkeypatch.setattr(agent_router, "settings", SimpleNamespace(LLM_STREAM_IDLE_TIMEOUT_SECONDS=0.001))
    response = await agent_router.invoke_general_agent_stream_endpoint(
        AgentQuery(input="hello"),
        _StreamRequest(),
        {"user_id": "owner"},
        _ConnectedAccount(),
    )
    iterator = response.body_iterator
    assert await anext(iterator) == ": heartbeat\n\n"
    await iterator.aclose()
    assert closed.is_set()

    disconnected_response = await agent_router.invoke_general_agent_stream_endpoint(
        AgentQuery(input="hello"),
        _StreamRequest(disconnected=True),
        {"user_id": "owner"},
        _ConnectedAccount(),
    )
    with pytest.raises(StopAsyncIteration):
        await anext(disconnected_response.body_iterator)


@pytest.mark.asyncio
async def test_executive_calendar_calls_default_to_profile_timezone_and_allow_override(monkeypatch, tmp_path: Path):
    profile = tmp_path / "user_config.yaml"
    profile.write_text("timezone: America/New_York\n", encoding="utf-8")
    calls = []

    async def fake_get_calendar_events(_calendar_service, date_strs, target_timezone):
        calls.append((date_strs, target_timezone))
        return {date_str: "No events found for this day." for date_str in date_strs}

    class Provider:
        def __init__(self) -> None:
            self.generate_calls = 0

        async def generate(self, **_kwargs):
            self.generate_calls += 1
            if self.generate_calls == 1:
                return SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            content=SimpleNamespace(
                                parts=[
                                    SimpleNamespace(
                                        function_call=_function_call(
                                            AgentTool.GET_CALENDAR_EVENTS.value,
                                            {"date_strs": ["13-09-2026"]},
                                            "profile-timezone",
                                        )
                                    ),
                                    SimpleNamespace(
                                        function_call=_function_call(
                                            AgentTool.GET_CALENDAR_EVENTS.value,
                                            {
                                                "date_strs": ["14-09-2026"],
                                                "target_timezone": "Europe/London",
                                            },
                                            "explicit-timezone",
                                        )
                                    ),
                                ]
                            )
                        )
                    ]
                )
            return SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="ok")]))],
                text="ok",
            )

    monkeypatch.setattr(agent_tools, "get_calendar_events", fake_get_calendar_events)
    policy = AgentToolPolicy(
        run_kind=AgentRunKind.INTERACTIVE_EXECUTIVE,
        allowed_tools=frozenset({AgentTool.GET_CALENDAR_EVENTS}),
    )
    agent = ExecutiveAgent("owner", provider=Provider(), config_path=profile)

    assert await agent.run("Show my calendar", calendar_service=object(), policy=policy) == "ok"
    assert calls == [
        (["13-09-2026"], "America/New_York"),
        (["14-09-2026"], "Europe/London"),
    ]


@pytest.mark.asyncio
async def test_generate_content_uses_email_agent_prompt(monkeypatch):
    captured = {}

    class EmailAgent:
        def __init__(self, *, user_id):
            captured["user_id"] = user_id

        async def run(self, *, input_query, system_prompt):
            captured["input_query"] = input_query
            captured["system_prompt"] = system_prompt
            return "Generated email body."

    monkeypatch.setattr(agent_router, "GeneralAgent", EmailAgent)

    response = await agent_router.invoke_general_agent_endpoint(
        AgentQuery(input="Write a project update"),
        {"user_id": "owner"},
    )

    assert response == {"result": "Generated email body."}
    assert captured == {
        "user_id": "owner",
        "input_query": "Write a project update",
        "system_prompt": agent_router.EMAIL_AGENT_PROMPT,
    }
