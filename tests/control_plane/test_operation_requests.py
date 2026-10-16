"""Frozen request IO changes preparation, never native identity or approval."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

import pytest

from examples import operation_action_fixtures as fixtures
from loopx.control_plane.turn_driver.codex_operation_host import operation_tool_handler
from loopx.control_plane.turn_driver.codex_sessions import _store_codex_cli_session
from loopx.control_plane.turn_driver.operation_requests import (
    OperationRequestSource, operation_request_source,
)
from tests.control_plane.test_dsh_operation_connection import (
    DshOperationConnection, PROFILE, call, envelope, event, exchange, opened,
)


def intent():
    value = fixtures.request(payload={"schema_version": "qualification_v0", "marker": "frozen-input"})
    terms = value["normalized_parameters"]
    terms.pop("executor")
    terms.update(domain="qualification", operation_kind="qualification.observe",
                 operation_schema="qualification_v0")
    terms["projection"]["simulated"] = False
    return value


def write_source(tmp_path, value):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(value, ensure_ascii=False))
    return operation_request_source(path, hashlib.sha256(path.read_bytes()).hexdigest())


def setup(tmp_path, *, host="dsh", value=None, configured=True):
    service, store = fixtures.service(tmp_path)
    lineage = {"goal_id": fixtures.GOAL_ID, "agent_id": "finance-fixture-agent", "todo_id": "todo-managed"}
    _store_codex_cli_session(store.root.parent.parent, lineage=lineage, host=host,
        session_id="owned-dsh", operation_profile_digest="d" * 64,
        operation_model="fixture-model", operation_reasoning_effort="high")
    source = write_source(tmp_path, intent() if value is None else value)
    turn = {"turn_key": "sha256:" + "a" * 64, "goal_ref": {"instance": "one"}}
    bound = source.bind(turn, lineage=lineage, session_id="owned-dsh", profile_digest="d" * 64)
    handler = operation_tool_handler(runtime_root=store.root.parent.parent,
        registry_path=service.registry_path, lineage=lineage, session_id="owned-dsh",
        profile_digest="d" * 64, model="fixture-model", reasoning_effort="high",
        host=host, frozen_request=bound if configured else None)
    native = {"thread_id": "owned-dsh", "host_turn_id": "native-turn"}
    return store, source, bound, handler, native, turn, lineage


@pytest.mark.parametrize("host", ["codex-cli", "dsh"])
def test_ref_prepares_exact_terms_once_without_approval_and_survives_lost_ack(tmp_path, host):
    store, source, bound, handler, native, *_ = setup(tmp_path, host=host)
    before = source.path.read_bytes()
    context = handler("loopx_operation", {"action": "context"}, native)
    assert context["prepared_request"] == {"ref": bound.ref}
    assert str(source.path) not in json.dumps(context)
    assert "frozen-input" not in json.dumps(context)
    args = {"action": "prepare", "request": context["prepared_request"]}
    first = handler("loopx_operation", args, native)
    assert first["ok"] and first["execution_allowed"] is False
    # Simulate a committed preview whose caller lost the response.
    second = handler("loopx_operation", args, {**native, "host_turn_id": "next-native-turn"})
    assert second["ok"] and second["proposal"]["proposal_id"] == first["proposal"]["proposal_id"]
    assert len(store.list(goal_id=fixtures.GOAL_ID)) == 1
    proposal = store.load(first["proposal"]["proposal_id"])
    assert proposal["operation"]["lifecycle_state"] == "awaiting_confirmation"
    assert proposal["operation"].get("confirmation") is None
    assert proposal["operation"].get("agent_handoff") is None
    assert proposal["normalized_parameters"]["payload"] == intent()["normalized_parameters"]["payload"]
    assert proposal["normalized_parameters"]["executor"]["session_id"] == "owned-dsh"
    refused = handler("loopx_operation", {"action": "consume",
        "proposal_id": proposal["proposal_id"], "consumption_id": "not-confirmed"}, native)
    assert refused.get("execution_allowed") is False
    assert source.path.read_bytes() == before


@pytest.mark.parametrize("shape", ["wrong_ref", "path", "extra", "inline"])
def test_model_cannot_replace_frozen_terms_or_select_a_file(tmp_path, shape):
    store, source, bound, handler, native, *_ = setup(tmp_path)
    values = {"wrong_ref": {"ref": "request-unknown"}, "path": {"ref": str(source.path)},
              "extra": {"ref": bound.ref, "path": str(source.path)}, "inline": intent()}
    result = handler("loopx_operation", {"action": "prepare", "request": values[shape]}, native)
    assert result == {"ok": False, "error": "operation_admission_rejected", "execution_allowed": False}
    assert store.list() == []


def test_content_tamper_after_context_is_refused_without_proposal(tmp_path):
    store, source, bound, handler, native, *_ = setup(tmp_path)
    handler("loopx_operation", {"action": "context"}, native)
    source.path.write_bytes(source.path.read_bytes() + b" ")
    result = handler("loopx_operation", {"action": "prepare", "request": {"ref": bound.ref}}, native)
    assert result.get("execution_allowed") is False and result["ok"] is False
    assert store.list() == []


@pytest.mark.parametrize("drift", ["goal", "agent", "todo", "session", "profile", "turn", "instance"])
def test_locator_from_another_native_scope_is_not_authority(tmp_path, drift):
    store, source, bound, handler, native, turn, lineage = setup(tmp_path)
    kwargs = {"lineage": dict(lineage), "session_id": "owned-dsh", "profile_digest": "d" * 64}
    turn = dict(turn)
    if drift in {"goal", "agent", "todo"}:
        kwargs["lineage"][drift + "_id"] = "other"
    elif drift in {"session", "profile"}:
        kwargs["session_id" if drift == "session" else "profile_digest"] = "other"
    else:
        turn["turn_key" if drift == "turn" else "goal_ref"] = "other"
    foreign = source.bind(turn, **kwargs)
    assert foreign.ref != bound.ref
    result = handler("loopx_operation", {"action": "prepare", "request": {"ref": foreign.ref}}, native)
    assert result["ok"] is False and store.list() == []


@pytest.mark.parametrize("drift", ["goal", "agent", "executor", "audience", "expired"])
def test_ref_reuses_canonical_prepare_subject_audience_and_expiry_gates(tmp_path, drift):
    value = intent()
    terms = value["normalized_parameters"]
    if drift in {"goal", "agent"}:
        terms[drift + "_id"] = "other"
    elif drift == "executor":
        terms["executor"] = {"session_id": "other"}
    elif drift == "audience":
        terms["source_route"] = {"host_surface": "codex-app", "thread_id": "other"}
    else:
        terms["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store, _, bound, handler, native, *_ = setup(tmp_path, value=value)
    result = handler("loopx_operation", {"action": "prepare", "request": {"ref": bound.ref}}, native)
    assert result["ok"] is False and store.list() == []


def test_unconfigured_handler_preserves_inline_context_and_prepare(tmp_path):
    store, _, bound, handler, native, *_ = setup(tmp_path, configured=False)
    assert "prepared_request" not in handler("loopx_operation", {"action": "context"}, native)
    assert handler("loopx_operation", {"action": "prepare", "request": {"ref": bound.ref}}, native)["ok"] is False
    assert handler("loopx_operation", {"action": "prepare", "request": intent()}, native)["ok"] is True
    assert len(store.list()) == 1


def test_native_socket_matches_original_short_arguments_before_resolution(tmp_path, monkeypatch):
    store, _, bound, handler, _, *_ = setup(tmp_path)
    resolved = []
    original = type(bound).resolve
    def observe(self, value):
        resolved.append(value)
        return original(self, value)
    monkeypatch.setattr(type(bound), "resolve", observe)
    args = {"action": "prepare", "request": {"ref": bound.ref}}
    with DshOperationConnection("owned-dsh", PROFILE) as connection:
        connection.bind(os.getpid(), handler)
        opened(connection)
        event(connection, "tool/call", {"turn": 1, "callId": "paired",
            "name": "loopx_operation", "arguments": json.dumps(args)})
        wrong = envelope({"action": "prepare", "request": {"ref": "changed"}}, "paired")
        assert exchange(connection, wrong)["ok"] is False
        assert resolved == [] and store.list() == []
        first = exchange(connection, envelope(args, "paired"))
        assert first["ok"] and len(resolved) == 1
        assert exchange(connection, envelope(args, "paired"))["ok"] is False
        # New authenticated call after losing a reply reuses canonical idempotency.
        second = call(connection, args, "after-lost-ack")
        assert second["proposal"]["proposal_id"] == first["proposal"]["proposal_id"]
        assert len(store.list()) == 1


@pytest.mark.parametrize("raw", [b'[]', b'{"action_kind":"operation.execute"} }',
    b'{"action_kind":"operation.execute","x":NaN}',
    b'{"action_kind":"operation.execute","x":1e309}',
    b'{"action_kind":"operation.execute","x":1,"x":2}', b'x' * 262145])
def test_operator_file_requires_strict_bounded_json(tmp_path, raw):
    path = tmp_path / "request.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        operation_request_source(path, hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("digest", [
    "", "a" * 63, "a" * 65, "A" * 64, "g" * 64,
    "sha256:" + "a" * 64, "a" * 64 + "\n", "\n" + "a" * 64,
    "a" * 64 + " ", " " + "a" * 64, b"a" * 64, 64,
])
def test_invalid_digest_shape_is_refused_before_frozen_file_io(tmp_path, monkeypatch, digest):
    monkeypatch.setattr(OperationRequestSource, "read",
        lambda *_: pytest.fail("invalid digest must not read the frozen file"))
    with pytest.raises(ValueError, match="requires an absolute file and SHA-256"):
        operation_request_source(tmp_path / "request.json", digest)


@pytest.mark.parametrize("path,digest", [(None, "a" * 64), ("relative.json", "a" * 64),
    ("/absent.json", None), ("/absent.json", "bad")])
def test_source_configuration_cannot_be_partial_or_model_selected(path, digest):
    from pathlib import Path
    with pytest.raises(ValueError):
        operation_request_source(Path(path) if path else None, digest)


def test_default_off_source_adds_no_context_or_profile():
    assert operation_request_source(None, None) is None


@pytest.mark.parametrize("drift", ["stopped", "recreated"])
def test_current_goal_lifetime_is_checked_before_frozen_request_io(tmp_path, drift, monkeypatch):
    from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction
    from tests.control_plane.test_first_party_host_runtime import _source_registry, INSTANCE_A, INSTANCE_B

    store, _, bound, _, native, _, lineage = setup(tmp_path)
    registry = tmp_path / "source" / "registry.json"
    payload = _source_registry(INSTANCE_A)
    payload["common_runtime_root"] = str(tmp_path / "runtime")
    payload["goals"][0]["id"] = fixtures.GOAL_ID
    with source_session_registry_transaction(registry, operation="fixture_create", create=lambda: payload) as txn:
        txn.commit(payload)
    admission = FirstPartyHostGoalAdmission.for_plan(registry_path=registry,
        goal_id=fixtures.GOAL_ID, planned_goal_ref={"goal_id": fixtures.GOAL_ID, "goal_instance_id": INSTANCE_A})
    handler = operation_tool_handler(runtime_root=store.root.parent.parent,
        registry_path=tmp_path / "project/.loopx/registry.json", lineage=lineage,
        session_id="owned-dsh", profile_digest="d" * 64, model="fixture-model",
        reasoning_effort="high", host="dsh", frozen_request=bound, goal_admission=admission)
    assert handler("loopx_operation", {"action": "context"}, native)["ok"]
    with source_session_registry_transaction(registry, operation="fixture_revoke") as txn:
        value = txn.payload_copy()
        if drift == "stopped":
            value["goals"][0]["status"] = "stopped"
        else:
            value["goals"][0]["goal_instance_id"] = INSTANCE_B
        txn.commit(value)
    monkeypatch.setattr(type(bound), "resolve", lambda *_: pytest.fail("revoked Goal must not read frozen file"))
    assert handler("loopx_operation", {"action": "prepare", "request": {"ref": bound.ref}}, native)["ok"] is False
    assert store.list() == []


@pytest.mark.parametrize("transport,file_option,digest", [
    (False, "absolute", "a" * 64), (True, "absolute", None),
    (True, None, "a" * 64), (True, "relative", "a" * 64),
])
def test_cli_rejects_unbound_file_options_before_host_launch(tmp_path, capsys, transport, file_option, digest):
    from loopx.cli import main
    service, _ = fixtures.service(tmp_path)
    args = ["--registry", str(service.registry_path), "--runtime-root", str(tmp_path / "runtime"),
        "--format", "json", "turn", "run-once", "--goal-id", fixtures.GOAL_ID,
        "--agent-id", "finance-fixture-agent", "--host", "codex-cli", "--project", str(tmp_path / "project")]
    if transport:
        args += ["--codex-operation-tools"]
    if file_option:
        args += ["--operation-request-file", str(tmp_path / "missing.json") if file_option == "absolute" else "relative.json"]
    if digest:
        args += ["--operation-request-sha256", digest]
    assert main(args) != 0
    output = capsys.readouterr().out
    assert ("requires an owned operation transport" if not transport
            else "requires an absolute file and SHA-256") in output


def test_codex_app_server_resolves_ref_and_freezes_profile_while_inline_remains_compatible(tmp_path):
    from test_codex_operation_host import FAKE_SERVER
    from tests.control_plane.test_dsh_operation_sdk import request
    from loopx.control_plane.turn_driver.codex_operation_host import run_codex_operation_host

    service, store = fixtures.service(tmp_path)
    source = write_source(tmp_path, intent())
    executable = tmp_path / "codex-fixture"
    server = FAKE_SERVER.replace(
        '        answer = {name: "" for name, schema in properties.items()',
        '        emit({"id": 52, "method": "item/tool/call", "params": {"threadId": thread, "turnId": turn, "tool": "loopx_operation", "arguments": {"action": "prepare", "request": result["prepared_request"]}}})\n'
        '    elif row.get("id") == 52:\n'
        '        prepared = json.loads(row["result"]["contentItems"][0]["text"])\n'
        '        assert prepared["ok"] and prepared["execution_allowed"] is False\n'
        '        answer = {name: "" for name, schema in properties.items()',
    )
    assert server != FAKE_SERVER
    executable.write_text(server)
    executable.chmod(0o700)
    options = dict(runtime_root=store.root.parent.parent, registry_path=service.registry_path,
        project=tmp_path / "project", codex_bin=str(executable), model="test-model", reasoning_effort="xhigh",
        timeout_seconds=5, operation_request_file=source.path, operation_request_sha256=source.sha256)
    first = run_codex_operation_host(request(), **options)
    assert first["result_kind"] == "wait" and len(store.list()) == 1
    assert store.list()[0]["operation"].get("confirmation") is None
    resumed = request()
    resumed["session"]["action"] = "resume"
    resumed["turn_key"] = "sha256:" + "b" * 64
    assert run_codex_operation_host(resumed, **options)["result_kind"] == "wait"
    assert len(store.list()) == 1
    for changed in [{"source_route": {"host_surface": "codex-app", "thread_id": "elsewhere"}},
                    {"operation_request_file": None, "operation_request_sha256": None}]:
        with pytest.raises(ValueError, match="profile changed"):
            run_codex_operation_host(resumed, **{**options, **changed})
    source.path.write_bytes(source.path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="content changed"):
        run_codex_operation_host(resumed, **options)
