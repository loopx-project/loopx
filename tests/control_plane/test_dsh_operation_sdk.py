"""Real pinned SDK/runtime + native tools + Core; loopback scripted provider.

Opt in with DSH_NATIVE_OPERATION_MODULE=/absolute/built/native-operation.js.
No model charge, genuine credential, human approval or venue effect is involved.
Confirmation is an authenticated *fixture*, never live transaction authority.
"""

import json
import os
import threading
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from examples import operation_action_fixtures as fixtures
from loopx.control_plane.quota.turn_envelope import (
    turn_envelope_action_signature_document,
)
from loopx.control_plane.turn_driver.codex_sessions import (
    _lineage,
    load_codex_cli_session,
)
from loopx.control_plane.turn_driver.dsh_operation_host import (
    OwnedDshOperationHost,
    run_dsh_operation_host,
)
from loopx.control_plane.turn_driver.host_candidate import _canonical_hash
from loopx.control_plane.turn_driver.executor import validate_loopx_turn_host_result
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)


def request():
    envelope = {
        "schema_version": "loopx_turn_envelope_v0",
        "goal_id": fixtures.GOAL_ID,
        "agent_id": "finance-fixture-agent",
        "action": {
            "primary_action": "Synthetic no-effect operation qualification",
            "selected_todo": {
                "todo_id": "todo-managed",
                "text": "Qualify owned DSH transport",
            },
        },
        "boundary": {"write_scope": [], "workspace_guard": {}},
        "required_reads": [],
    }
    digest = _canonical_hash(turn_envelope_action_signature_document(envelope))
    envelope["action_signature"] = {
        "source_hash": digest,
        "envelope_hash": digest,
        "matches": True,
    }
    return {
        "schema_version": "loopx_turn_host_request_v0",
        "turn_key": "sha256:" + "a" * 64,
        "turn_envelope": envelope,
        "session": {"action": "start_new"},
        "result_contract": {"completed_phases": ["host_execute", "typed_result"]},
    }


def tool_results(notifications):
    results = []
    for notification in notifications:
        event = notification.payload.get("event") or {}
        if event.get("type") == "tool/result":
            for block in event.get("data", {}).get("message", {}).get("content") or []:
                for part in block.get("content") or [block]:
                    if part.get("type") == "text":
                        try:
                            results.append(json.loads(part["text"]))
                        except (KeyError, ValueError):
                            pass
    return results


@pytest.mark.parametrize("authority_provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "connection_lifetime", ["retained", "restarted", "missing", "unreadable"]
)
def test_real_sdk_native_prepare_confirmation_consume_report_and_profile_revocation(
    tmp_path, monkeypatch, authority_provider, connection_lifetime
):
    pytest.importorskip("deepseek_harness")
    module = os.environ.get("DSH_NATIVE_OPERATION_MODULE")
    if not module:
        pytest.skip("explicit built native-operation module required")
    runtime_bin = os.environ.get("DSH_NATIVE_OPERATION_RUNTIME")
    if connection_lifetime != "retained" and not runtime_bin:
        pytest.skip("explicit resume-capable runtime required")
    service, store = fixtures.service(tmp_path)
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    projection = build_todo_runtime_shadow_projection(
        goal_id=fixtures.GOAL_ID, todos=[], handoff_mode="soft_claim"
    )
    initialize_canonical_authority(
        store.root.parent.parent,
        fixtures.GOAL_ID,
        projection,
        state_path=service.registry_path.parent.parent / "ACTIVE_GOAL_STATE.md",
        provider=authority_provider,
    )
    operation_request = fixtures.request(
        payload={"schema_version": "qualification_v0", "marker": "no-external-effect"}
    )
    terms = operation_request["normalized_parameters"]
    terms.pop("executor")
    terms.update(
        domain="qualification",
        operation_kind="qualification.observe",
        operation_schema="qualification_v0",
    )
    terms["projection"]["simulated"] = False
    steps = []
    errors = []

    def operation():
        rows = store.list(goal_id=fixtures.GOAL_ID)
        assert len(rows) == 1
        return rows[0]

    def arguments(index):
        if index == 0:
            return {"action": "context"}
        if index == 1:
            return {"action": "prepare", "request": operation_request}
        proposal = operation()
        if index == 4:
            return {"action": "inspect", "proposal_id": proposal["proposal_id"]}
        if index == 7:
            return {
                "action": "report",
                "proposal_id": proposal["proposal_id"],
                "outcome": fixtures.agent_result(
                    proposal, "native-sdk-consumption", result="not_executed"
                ),
            }
        return {
            "action": "consume",
            "proposal_id": proposal["proposal_id"],
            "consumption_id": "native-sdk-consumption",
        }

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                assert body["model"] == "fixture-model"
                index = len(steps)
                assert index < 9, "bounded scripted request budget exceeded"
                steps.append(index)
                if index in {3, 8}:
                    delta = {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "result_kind": "wait",
                                "classification": "synthetic_native_operation",
                                "summary": "Original no-effect result retained",
                                "recommended_action": "Continue separate live qualification",
                            }
                        ),
                    }
                    finish = "stop"
                else:
                    delta = {
                        "role": "assistant",
                        "reasoning_content": "",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"sdk-native-call-{index}",
                                "type": "function",
                                "function": {
                                    "name": "loopx_operation",
                                    "arguments": json.dumps(arguments(index)),
                                },
                            }
                        ],
                    }
                    finish = "tool_calls"
                chunks = [
                    {
                        "id": f"fixture-{index}",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "fixture-model",
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": None}
                        ],
                    },
                    {
                        "id": f"fixture-{index}",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "fixture-model",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    },
                ]
                data = (
                    "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n"
                ).encode()
                if self.path.endswith("/messages"):
                    # 0.2.1's official adapter uses Messages SSE; 0.1.5 uses
                    # Chat Completions. Both are actual loopback wire paths.
                    block = (
                        {"type": "text", "text": delta["content"]}
                        if finish == "stop"
                        else {
                            "type": "tool_use",
                            "id": f"sdk-native-call-{index}",
                            "name": "loopx_operation",
                            "input": arguments(index),
                        }
                    )
                    events = [
                        {
                            "type": "message_start",
                            "message": {
                                "id": f"fixture-{index}",
                                "model": "fixture-model",
                                "usage": {"input_tokens": 1, "output_tokens": 0},
                            },
                        },
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": block,
                        },
                        {"type": "content_block_stop", "index": 0},
                        {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": "end_turn"
                                if finish == "stop"
                                else "tool_use"
                            },
                            "usage": {"output_tokens": 1},
                        },
                        {"type": "message_stop"},
                    ]
                    data = "".join(
                        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                        for event in events
                    ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                errors.append(repr(exc))
                self.send_error(500)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "loopback-fixture-placeholder")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    # No built-in account/shell tools exposed in this synthetic qualification.
    patch_module = tmp_path / "restrict.mjs"
    patch_module.write_text(
        "export const name='fixture-operation-only';\nexport function apply(ctx){"
        "ctx.on('agent/created',({agent})=>{agent.ctx.tools.presentAs('native');"
        "agent.ctx.tools.restrict({allow:['loopx_operation']})})}\n"
    )
    patch = tmp_path / "restrict.patch.yml"
    patch.write_text(
        "- insert:\n    - id: fixture-operation-only\n      name: "
        + json.dumps(patch_module.as_uri())
        + "\n"
    )
    options = {
        "runtime_root": store.root.parent.parent,
        "registry_path": service.registry_path,
        "project": service.registry_path.parent.parent,
        "dsh_home": tmp_path / "sdk-home",
        "operation_module": Path(module),
        "provider": "deepseek-official",
        "model": "fixture-model",
        "reasoning_effort": "high",
        "max_tokens": 1024,
        "timeout_seconds": 45,
        "cordis": patch,
    }
    if runtime_bin:
        options["runtime_bin"] = Path(runtime_bin)
    notifications = []
    turn = request()
    try:
        with ExitStack() as hosts:
            host = hosts.enter_context(OwnedDshOperationHost())
            first = host.run(turn, **options, on_notification=notifications.append)
            assert first["result_kind"] == "wait", (first, errors)
            assert len(steps) == 4, (steps, errors)
            proposal = operation()
            assert proposal["operation"].get("agent_handoff") is None
            delivered = store.record_operation_delivery(
                proposal["proposal_id"], delivery=fixtures.delivery(proposal)
            )
            store.decide_operation(
                proposal["proposal_id"],
                decision="confirm",
                confirmation=fixtures.confirmation(delivered),
            )
            if connection_lifetime != "retained":
                first_process = host.harness.client._proc
                host.close()
                assert first_process.poll() is not None
                host = hosts.enter_context(OwnedDshOperationHost())
            turn["session"]["action"] = "resume"
            turn["turn_key"] = "sha256:" + "b" * 64
            if connection_lifetime in {"missing", "unreadable"}:
                original_binding = load_codex_cli_session(
                    options["runtime_root"], lineage=_lineage(turn), host="dsh"
                )
                sessions = options["dsh_home"] / "sessions"
                if connection_lifetime == "missing":
                    sessions.rename(options["dsh_home"] / "saved-sessions")
                else:
                    persisted = next(sessions.rglob("session.*.jsonl*"))
                    persisted.rename(persisted.with_suffix(persisted.suffix + ".saved"))
                    persisted.write_bytes(b"not a session stream")
                from deepseek_harness.errors import JsonRpcError

                with pytest.raises(JsonRpcError):
                    host.run(
                        turn, **options, confirmed_operation_id=proposal["proposal_id"]
                    )
                assert not host.connection.path.exists()
                with pytest.raises(ValueError, match="closed"):
                    host.run(turn, **options)
                assert (
                    load_codex_cli_session(
                        options["runtime_root"], lineage=_lineage(turn), host="dsh"
                    )
                    == original_binding
                )
                assert len(steps) == 4 and not errors
                assert operation()["operation"].get("agent_handoff") is None
                if connection_lifetime == "missing":
                    assert not list(sessions.rglob("session.*.jsonl*"))
                return
            second = host.run(
                turn,
                **options,
                confirmed_operation_id=proposal["proposal_id"],
                on_notification=notifications.append,
            )
            assert second["result_kind"] == "wait", (second, errors)
            validation = validate_loopx_turn_host_result(
                {"transaction": {"turn_key": turn["turn_key"]}}, second
            )
            assert validation["ok"], validation["errors"]
            assert len(steps) == 9 and not errors, (steps, errors)
            stored = operation()
            (tmp_path / "native-events.json").write_text(
                json.dumps(
                    [
                        {"method": row.method, "payload": row.payload}
                        for row in notifications
                    ]
                )
            )
            assert stored["operation"]["outcome"] is not None, json.dumps(
                tool_results(notifications)
            )
            assert stored["operation"]["outcome"]["outcome"] == "not_executed"
            assert stored["operation"]["host_start"]["turn_key"] == turn["turn_key"]
            assert stored["operation"]["agent_handoff"]["host_turn_id"].startswith(
                "dsh-2-"
            )
            results = tool_results(notifications)
            assert sum(row.get("execution_allowed") is True for row in results) == 1, (
                results
            )
            assert any(row.get("status") == "already_consumed" for row in results), (
                results
            )
            binding = load_codex_cli_session(
                options["runtime_root"], lineage=_lineage(turn), host="dsh"
            )
            assert (
                binding["session_id"]
                == stored["normalized_parameters"]["executor"]["session_id"]
            )
            with pytest.raises(ValueError, match="profile changed"):
                host.run(turn, **{**options, "model": "replacement-model"})
            assert len(steps) == 9  # Profile drift refused before a provider request.
            if not runtime_bin:
                with pytest.raises(
                    ValueError, match="cannot resume a persisted session"
                ):
                    run_dsh_operation_host(turn, **options)
            assert len(steps) == 9
    finally:
        (tmp_path / "native-events.json").write_text(
            json.dumps(
                [
                    {"method": row.method, "payload": row.payload}
                    for row in notifications
                ]
            )
        )
        (tmp_path / "provider-steps.json").write_text(
            json.dumps({"steps": steps, "errors": errors})
        )
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_callback_failure_quiesces_owned_sdk_before_host_can_be_reused(
    tmp_path, monkeypatch
):
    pytest.importorskip("deepseek_harness")
    module = os.environ.get("DSH_NATIVE_OPERATION_MODULE")
    if not module:
        pytest.skip("explicit built native-operation module required")
    service, store = fixtures.service(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "loopback-fixture-placeholder")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://127.0.0.1:9")
    processes = []
    with OwnedDshOperationHost() as host:

        def fail(notification):
            if notification.payload.get("event", {}).get("type") == "turn/start":
                processes.append(host.harness.client._proc)
                raise RuntimeError("fixture callback aborted")

        options = {
            "runtime_root": store.root.parent.parent,
            "registry_path": service.registry_path,
            "project": service.registry_path.parent.parent,
            "dsh_home": tmp_path / "sdk-home",
            "operation_module": Path(module),
            "provider": "deepseek-official",
            "model": "fixture-model",
            "reasoning_effort": "high",
            "max_tokens": 128,
            "timeout_seconds": 15,
        }
        if os.environ.get("DSH_NATIVE_OPERATION_RUNTIME"):
            options["runtime_bin"] = Path(os.environ["DSH_NATIVE_OPERATION_RUNTIME"])
        with pytest.raises(RuntimeError, match="fixture callback aborted"):
            host.run(request(), **options, on_notification=fail)
        assert len(processes) == 1 and processes[0].poll() is not None
        assert not host.connection.path.exists()
        with pytest.raises(ValueError, match="closed"):
            host.run(request(), **options)
