"""Actual local socket, native-event pairing and canonical one-shot consumer."""

import json
import os
import socket
from types import SimpleNamespace

import pytest

from examples import operation_action_fixtures as fixtures
from loopx.control_plane.turn_driver.codex_operation_host import operation_tool_handler
from loopx.control_plane.turn_driver.codex_sessions import (
    _store_codex_cli_session,
    load_codex_cli_session,
)
from loopx.control_plane.turn_driver.dsh_operation_connection import (
    DshOperationConnection,
)

from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)


PROFILE = {
    "provider": "deepseek-official",
    "model": "fixture-model",
    "reasoning_effort": "high",
}


def event(connection, kind, data, seq=4, session="owned-dsh"):
    connection.observe(
        SimpleNamespace(
            method="session.event",
            payload={
                "sessionId": session,
                "event": {"type": kind, "seq": seq, "data": data},
            },
        )
    )


def envelope(arguments, call_id="native-call"):
    return {
        "schema_version": "loopx_dsh_operation_call_v0",
        "call_id": call_id,
        "arguments": arguments,
        "native": {
            "schema_version": "loopx_dsh_native_context_v0",
            "session_id": "owned-dsh",
            "native_turn_number": 1,
            "turn_start_seq": 4,
            "profile_source": "agent_options",
            "profile": PROFILE,
            "wire_profile_verified": False,
            "execution_allowed": False,
        },
    }


def exchange(connection, row):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(10)
        client.connect(str(connection.path))
        client.sendall(json.dumps(row).encode() + b"\n")
        data = bytearray()
        while b"\n" not in data:
            data.extend(client.recv(8192))
        return json.loads(data)


def opened(connection):
    event(connection, "turn/start", {"turn": 1})
    event(
        connection,
        "request/header",
        {
            "header": {
                "config": {
                    "provider": PROFILE["provider"],
                    "model": PROFILE["model"],
                    "reasoningEffort": "high",
                }
            }
        },
    )


def call(connection, args, call_id):
    event(
        connection,
        "tool/call",
        {
            "turn": 1,
            "callId": call_id,
            "name": "loopx_operation",
            "arguments": json.dumps(args),
        },
    )
    return exchange(connection, envelope(args, call_id))


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="native Unix transport")
@pytest.mark.parametrize("authority_provider", ["file", "sqlite"])
def test_native_socket_prepares_consumes_once_and_reports_to_original_route(
    tmp_path, monkeypatch, authority_provider
):
    service, store = fixtures.service(tmp_path)
    runtime = store.root.parent.parent
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    projection = build_todo_runtime_shadow_projection(
        goal_id=fixtures.GOAL_ID, todos=[], handoff_mode="soft_claim"
    )
    initialize_canonical_authority(
        runtime,
        fixtures.GOAL_ID,
        projection,
        state_path=service.registry_path.parent.parent / "ACTIVE_GOAL_STATE.md",
        provider=authority_provider,
    )
    lineage = {
        "goal_id": fixtures.GOAL_ID,
        "agent_id": "finance-fixture-agent",
        "todo_id": "todo-managed",
    }
    options = {
        "lineage": lineage,
        "session_id": "owned-dsh",
        "host": "dsh",
        "operation_profile_digest": "d" * 64,
        "operation_model": "fixture-model",
        "operation_reasoning_effort": "high",
    }
    _store_codex_cli_session(runtime, **options)
    assert (
        load_codex_cli_session(runtime, lineage=lineage) is None
    )  # No Codex cross-host resume.
    handler = operation_tool_handler(
        runtime_root=runtime,
        registry_path=service.registry_path,
        lineage=lineage,
        session_id="owned-dsh",
        profile_digest="d" * 64,
        model="fixture-model",
        reasoning_effort="high",
        host="dsh",
    )
    with DshOperationConnection("owned-dsh", PROFILE) as connection:
        connection.bind(os.getpid(), handler)
        opened(connection)
        request = fixtures.request(
            payload={"schema_version": "qualification_v0", "marker": "synthetic"}
        )
        terms = request["normalized_parameters"]
        terms.pop("executor")
        terms.update(
            domain="qualification",
            operation_kind="qualification.observe",
            operation_schema="qualification_v0",
        )
        terms["projection"]["simulated"] = False
        prepared = call(
            connection, {"action": "prepare", "request": request}, "prepare"
        )
        assert prepared["ok"], prepared
        proposal = prepared["proposal"]
        assert proposal["normalized_parameters"]["executor"]["host"] == "dsh"
        consume = {
            "action": "consume",
            "proposal_id": proposal["proposal_id"],
            "consumption_id": "synthetic-consume",
        }
        assert call(connection, consume, "unapproved")["execution_allowed"] is False
        delivered = store.record_operation_delivery(
            proposal["proposal_id"], delivery=fixtures.delivery(proposal)
        )
        confirmed = store.decide_operation(
            proposal["proposal_id"],
            decision="confirm",
            confirmation=fixtures.confirmation(delivered),
        )
        assert call(connection, consume, "first")["execution_allowed"] is True
        assert call(connection, consume, "retry")["execution_allowed"] is False
        outcome = fixtures.agent_result(
            confirmed, "synthetic-consume", result="not_executed"
        )
        reported = call(
            connection,
            {
                "action": "report",
                "proposal_id": proposal["proposal_id"],
                "outcome": outcome,
            },
            "report",
        )
        assert reported["ok"], reported
        stored = store.load(proposal["proposal_id"])
        assert stored["operation"]["outcome"]["outcome"] == "not_executed"
        assert (
            stored["operation"]["outcome_report"]["original_route"]["host_surface"]
            == "loopx-managed-dsh"
        )
        # Replacement revokes the original native connection without a new approval store.
        _store_codex_cli_session(
            runtime, **{**options, "session_id": "replacement-dsh"}
        )
        assert call(connection, consume, "revoked")["execution_allowed"] is False


@pytest.mark.parametrize(
    "mutation", ["identity", "profile", "turn", "arguments", "replay", "closed", "peer"]
)
def test_native_connection_rejects_forgery_replay_and_closed_turn(mutation):
    calls = []
    with DshOperationConnection("owned-dsh", PROFILE) as connection:
        connection.bind(
            os.getpid() + (1 if mutation == "peer" else 0),
            lambda *args: calls.append(args) or {"ok": True},
        )
        opened(connection)
        arguments = {"action": "context"}
        event(
            connection,
            "tool/call",
            {
                "turn": 1,
                "callId": "native-call",
                "name": "loopx_operation",
                "arguments": '{"action":"context"}',
            },
        )
        row = envelope(arguments)
        if mutation == "identity":
            row["native"]["session_id"] = "foreign"
        if mutation == "profile":
            row["native"]["profile"] = {**PROFILE, "model": "drift"}
        if mutation == "turn":
            row["native"]["native_turn_number"] = True
        if mutation == "arguments":
            row["arguments"] = {"action": "consume"}
        if mutation == "closed":
            event(connection, "turn/end", {"turn": 1})
        if mutation == "replay":
            assert exchange(connection, row)["ok"]
        result = exchange(connection, row)
        assert result == {
            "ok": False,
            "error": "operation_native_binding_rejected",
            "execution_allowed": False,
        }
        assert len(calls) == (1 if mutation == "replay" else 0)


def test_observed_header_survives_only_this_connection_and_profile_drift_revokes_it():
    with DshOperationConnection("owned-dsh", PROFILE) as connection:
        connection.bind(os.getpid(), lambda *_args: {"ok": True})
        opened(connection)
        assert call(connection, {"action": "context"}, "first")["ok"]
        event(connection, "turn/end", {"turn": 1})
        event(connection, "turn/start", {"turn": 2}, seq=32)
        args = {"action": "context"}
        event(
            connection,
            "tool/call",
            {
                "turn": 2,
                "callId": "second",
                "name": "loopx_operation",
                "arguments": json.dumps(args),
            },
        )
        row = envelope(args, "second")
        row["native"].update(native_turn_number=2, turn_start_seq=32)
        assert exchange(connection, row)["ok"]
        event(
            connection,
            "request/header",
            {
                "header": {
                    "config": {
                        "provider": "foreign",
                        "model": "fixture-model",
                        "reasoningEffort": "high",
                    }
                }
            },
        )
        event(
            connection,
            "tool/call",
            {
                "turn": 2,
                "callId": "drift",
                "name": "loopx_operation",
                "arguments": json.dumps(args),
            },
        )
        row["call_id"] = "drift"
        assert exchange(connection, row)["execution_allowed"] is False
    with DshOperationConnection("owned-dsh", PROFILE) as replacement:
        replacement.bind(os.getpid(), lambda *_args: {"ok": True})
        event(replacement, "turn/start", {"turn": 1})
        assert call(replacement, args, "unobserved")["execution_allowed"] is False
