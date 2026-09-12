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
