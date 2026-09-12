from __future__ import annotations

import json
from pathlib import Path

from server.main import app


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_ARTIFACT = REPOSITORY_ROOT / "docs" / "openapi.json"


def test_committed_openapi_contract_matches_the_live_application():
    assert json.loads(OPENAPI_ARTIFACT.read_text(encoding="utf-8")) == app.openapi()


def test_openapi_documents_sse_enums_and_the_actual_validation_error_shape():
    schema = app.openapi()
    components = schema["components"]["schemas"]

    stream_response = schema["paths"]["/generate-stream/"]["post"]["responses"]["200"]
    assert set(stream_response["content"]) == {"text/event-stream"}
    assert stream_response["content"]["text/event-stream"]["schema"]["format"] == "event-stream"

    assert components["AgentStreamEvent"]["properties"]["event"]["$ref"].endswith("AgentStreamEventType")
    assert components["AgentStreamEventType"]["enum"] == ["token", "tool_result", "error", "done"]
    assert components["PendingActionType"]["enum"] == [
        "send_email",
        "send_reply",
        "create_event",
        "create_task",
    ]
    assert components["PendingActionStatus"]["enum"] == [
        "pending",
        "processing",
        "succeeded",
        "failed",
        "rejected",
        "expired",
        "reconciliation_required",
    ]

    validation_response = schema["paths"]["/mail/emails"]["get"]["responses"]["422"]
    validation_schema = validation_response["content"]["application/json"]["schema"]
    assert validation_schema["$ref"].endswith("PublicErrorResponse")
    assert "HTTPValidationError" not in components


def test_runtime_commands_do_not_reference_the_deleted_logging_ini():
    for relative_path in ("Dockerfile", "Makefile"):
        source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert "server/logging.ini" not in source


def test_compose_mounts_the_oauth_token_keyring_for_all_runtime_roles():
    compose = (REPOSITORY_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "OAUTH_TOKEN_KEYRING_PATH: /run/secrets/onebox-oauth-token-keyring.json" in compose
    assert "OAUTH_TOKEN_ACTIVE_KEY_ID: ${OAUTH_TOKEN_ACTIVE_KEY_ID:?Set OAUTH_TOKEN_ACTIVE_KEY_ID" in compose
    assert "${OAUTH_TOKEN_KEYRING_HOST_PATH:?Set OAUTH_TOKEN_KEYRING_HOST_PATH}" in compose


def test_checked_in_search_client_contract_matches_the_paginated_backend_response():
    schema = app.openapi()
    search_operation = schema["paths"]["/mail/search"]["get"]
    search_response = search_operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert search_response["$ref"].endswith("EmailPage")
    assert any(parameter["name"] == "page_token" for parameter in search_operation["parameters"])

    client_contract = (REPOSITORY_ROOT / "frontend-client-types.ts").read_text(encoding="utf-8")
    assert "page_token?: string | null;" in client_contract
    assert "searchEmails: EmailPage;" in client_contract


def test_checked_in_star_client_contract_matches_the_required_state_request():
    schema = app.openapi()
    star_operation = schema["paths"]["/mail/emails/{email_id}/star"]["post"]
    star_request = star_operation["requestBody"]["content"]["application/json"]["schema"]
    assert star_request["$ref"].endswith("StarStateUpdate")
    assert schema["components"]["schemas"]["StarStateUpdate"]["required"] == ["starred"]

    client_contract = (REPOSITORY_ROOT / "frontend-client-types.ts").read_text(encoding="utf-8")
    assert "export interface StarStateUpdate" in client_contract
    assert "starred: boolean;" in client_contract
    assert "toggleStar: StarStateUpdate;" in client_contract
