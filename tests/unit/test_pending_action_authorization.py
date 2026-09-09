from uuid import uuid4

import pytest
from fastapi import HTTPException

from server.routes import agent_router


@pytest.mark.asyncio
async def test_reconciliation_operator_allowlist_is_authenticated_and_fail_closed(monkeypatch):
    operator_id = uuid4()
    monkeypatch.setattr(agent_router.settings, "PENDING_ACTION_OPERATOR_IDS", str(operator_id))

    operator = {"user_id": operator_id}
    assert await agent_router.require_pending_action_operator(operator) is operator

    with pytest.raises(HTTPException) as denied:
        await agent_router.require_pending_action_operator({"user_id": uuid4()})
    assert denied.value.status_code == 403

    monkeypatch.setattr(agent_router.settings, "PENDING_ACTION_OPERATOR_IDS", "")
    with pytest.raises(HTTPException) as unconfigured:
        await agent_router.require_pending_action_operator(operator)
    assert unconfigured.value.status_code == 503



def test_server_command_context_reuses_provider_function_call_identity():
    from types import SimpleNamespace

    first_call = SimpleNamespace(id="provider-call-1")
    retry_call = SimpleNamespace(id="provider-call-1")
    another_call = SimpleNamespace(id="provider-call-2")
    command_keys: dict[str, str] = {}

    from agents import _command_key_for_tool_call

    key = _command_key_for_tool_call(command_keys, scope="first", function_call=first_call)
    assert _command_key_for_tool_call(command_keys, scope="retry", function_call=retry_call) == key
    assert _command_key_for_tool_call(command_keys, scope="second", function_call=another_call) != key
