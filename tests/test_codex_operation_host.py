"""Non-financial managed transport qualification; no live user/account effects."""

from __future__ import annotations

import os
import time
import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.codex_cli import (
    _lineage,
    load_codex_cli_session,
    run_codex_cli_host,
)
from loopx.control_plane.turn_driver.codex_operation_host import (
    run_codex_operation_host,
)
from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
from loopx.control_plane.turn_driver.executor import validate_loopx_turn_host_result
from test_loopx_turn_codex_cli import _request
from test_loopx_turn_driver import _write_live_fixture


FAKE_SERVER = """#!/usr/bin/env python3
import json, sys
thread = "owned-app-server-thread"
import os
turn = os.environ.get("FAKE_OPERATION_TURN_ID", "native-app-server-turn")
key = None
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    row = json.loads(line)
    method = row.get("method")
    if method == "initialize":
        emit({"id": row["id"], "result": {}})
    elif method in {"thread/start", "thread/resume"}:
        assert row["params"]["sandbox"] == "read-only"
        if method == "thread/start":
            assert row["params"]["dynamicTools"][0]["name"] == "loopx_operation"
        emit({"id": row["id"], "result": {"thread": {"id": thread}, "model": "test-model", "reasoningEffort": "xhigh"}})
    elif method == "turn/start":
        assert row["params"]["outputSchema"]["type"] == "object"
        properties = row["params"]["outputSchema"]["properties"]
        import os, pathlib, subprocess, time
        marker = os.environ.get("FAKE_OPERATION_CHILD_MARKER")
        if marker:
            child = subprocess.Popen([sys.executable, "-c",
                "import pathlib,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "p=pathlib.Path(" + repr(marker) + ");n=0\\n"
                "while True:\\n p.write_text(str(n));n+=1;time.sleep(.01)"])
            pathlib.Path(marker + ".pid").write_text(str(child.pid))
            while not pathlib.Path(marker).exists(): time.sleep(.01)
        if os.environ.get("FAKE_OPERATION_HANG") == "1":
            while True: time.sleep(.1)
        text = row["params"]["input"][0]["text"]
        if os.environ.get("FAKE_OPERATION_PROMPT_FILE"):
            pathlib.Path(os.environ["FAKE_OPERATION_PROMPT_FILE"]).write_text(text)
        import re
        key = re.search(r'"turn_key":"([^"]+)"', text).group(1)
        emit({"id": row["id"], "result": {"turn": {"id": turn}}})
        emit({"id": 50, "method": "item/tool/call", "params": {"threadId": "forged-thread", "turnId": turn, "tool": "loopx_operation", "arguments": {"action": "context"}}})
    elif row.get("id") == 50:
        assert row["result"]["success"] is False
        emit({"id": 51, "method": "item/tool/call", "params": {"threadId": thread, "turnId": turn, "tool": "loopx_operation", "arguments": {"action": "context"}}})
    elif row.get("id") == 51:
        result = json.loads(row["result"]["contentItems"][0]["text"])
        assert result["executor"]["session_id"] == thread
        assert result["authority"] == "approval_and_first_consumption_required"
        assert row["result"]["success"] is True
        answer = {name: "" for name, schema in properties.items() if schema["type"] == "string"}
        answer.update({"schema_version": "loopx_turn_result_v0", "turn_key": key, "result_kind": "wait", "completed_phases": ["host_execute", "typed_result"], "classification": "awaiting_operation_confirmation", "recommended_action": "Wait for actual human approval", "summary": "Owned native context retrieved; no approval or effect"})
        emit({"method": "item/agentMessage/delta", "params": {"threadId": thread, "turnId": turn, "delta": json.dumps(answer)}})
        emit({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": turn, "status": "completed"}}})
"""


def _claimed_native_fixture(tmp_path: Path):
    from examples import operation_action_fixtures as fixtures
    from loopx.control_plane.turn_driver.codex_operation_host import operation_tool_handler

    service, store = fixtures.service(tmp_path, goal_id="fixture-goal")
    executable = tmp_path / "fake-codex-operation"
    executable.write_text(FAKE_SERVER)
    executable.chmod(0o700)
    request = _request()
    request["turn_envelope"]["agent_id"] = "finance-fixture-agent"
    request["turn_envelope"]["action"]["selected_todo"]["todo_id"] = "todo-managed"
    options = dict(runtime_root=store.root.parent.parent, registry_path=service.registry_path,
                   project=service.registry_path.parent.parent, codex_bin=str(executable),
                   model="test-model", reasoning_effort="xhigh", timeout_seconds=5)
    run_codex_operation_host(request, **options)
    binding = load_codex_cli_session(options["runtime_root"], lineage=_lineage(request))
    handler = operation_tool_handler(
        runtime_root=options["runtime_root"], registry_path=service.registry_path,
        lineage=_lineage(request), session_id=binding["session_id"],
        profile_digest=binding["operation_profile_digest"], model="test-model", reasoning_effort="xhigh",
    )
    operation_request = fixtures.request(goal_id="fixture-goal",
        payload={"schema_version": "qualification_v0", "marker": "synthetic"})
    terms = operation_request["normalized_parameters"]
    terms.pop("executor")
    terms.update(domain="qualification", operation_kind="qualification.observe", operation_schema="qualification_v0")
    terms["projection"].update(simulated=False, title="Synthetic qualification", focus="No external effect")
    prepared = handler("loopx_operation", {"action": "prepare", "request": operation_request},
                       {"thread_id": binding["session_id"], "host_turn_id": "preparing-native-turn"})
    assert prepared["ok"], prepared
    proposal = prepared["proposal"]
    delivered = store.record_operation_delivery(proposal["proposal_id"], delivery=fixtures.delivery(proposal))
    claimed = store.decide_operation(proposal["proposal_id"], decision="confirm", confirmation=fixtures.confirmation(delivered))
    request["session"]["action"] = "resume"
    request["turn_key"] = "sha256:" + "b" * 64
    return store, claimed, request, options


def test_confirmed_operation_is_automatically_carried_into_accepted_native_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, claimed, request, options = _claimed_native_fixture(tmp_path)
    prompt = tmp_path / "native-prompt.txt"
    monkeypatch.setenv("FAKE_OPERATION_PROMPT_FILE", str(prompt))
    result = run_codex_operation_host(request, **options)
    assert result["result_kind"] == "wait"
    stored = store.load(claimed["proposal_id"])
    receipt = stored["operation"]["host_start"]
    assert claimed["proposal_id"] in prompt.read_text()
    assert claimed["operation"]["claim"]["claim_id"] in prompt.read_text()
    assert receipt["confirmation_event_id"] == claimed["operation"]["confirmation"]["event_id"]
    assert receipt["claim_id"] == claimed["operation"]["claim"]["claim_id"]
    assert receipt["host_turn_id"] == "native-app-server-turn"
    assert receipt["turn_key"] == request["turn_key"]
    assert receipt["confirmed_at"] <= receipt["accepted_at"]
    assert receipt["execution_allowed"] is False
    assert stored["operation"].get("agent_handoff") is None
    assert stored["operation"]["outcome"] is None
    monkeypatch.setenv("FAKE_OPERATION_TURN_ID", "later-native-turn")
    request["turn_key"] = "sha256:" + "c" * 64
    run_codex_operation_host(request, **options)
    assert store.load(claimed["proposal_id"])["operation"]["host_start"] == receipt


def test_confirmed_callback_launch_fence_resumes_only_the_original_native_session(tmp_path: Path) -> None:
    store, claimed, request, options = _claimed_native_fixture(tmp_path)
    run_codex_operation_host(request, confirmed_operation_id=claimed["proposal_id"], **options)
    assert store.load(claimed["proposal_id"])["operation"]["host_start"]["turn_key"] == request["turn_key"]
    assert store.load(claimed["proposal_id"])["operation"].get("agent_handoff") is None
    # A callback/recovery cannot start another native Turn after acceptance.
    with pytest.raises(Exception, match="unstarted"):
        run_codex_operation_host(request, confirmed_operation_id=claimed["proposal_id"], **options)


@pytest.mark.parametrize("drift", ["session", "todo", "profile", "fresh", "stopped", "missing"])
def test_callback_launch_fence_rejects_drift_before_native_process_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    from loopx.control_plane.turn_driver import codex_operation_host as owner
    from loopx.control_plane.turn_driver.codex_cli import _store_codex_cli_session

    store, claimed, request, options = _claimed_native_fixture(tmp_path)
    if drift == "session":
        original = load_codex_cli_session(options["runtime_root"], lineage=_lineage(request))
        _store_codex_cli_session(options["runtime_root"], lineage=_lineage(request), session_id="replacement",
            operation_profile_digest=original["operation_profile_digest"], operation_model="test-model",
            operation_reasoning_effort="xhigh")
    elif drift == "todo":
        request["turn_envelope"]["action"]["selected_todo"]["todo_id"] = "other"
    elif drift == "profile":
        options["reasoning_effort"] = "high"
    elif drift == "fresh":
        request["session"]["context_policy"] = {"mode": "fresh"}
    elif drift == "stopped":
        data = json.loads(options["registry_path"].read_text())
        data["goals"][0]["status"] = "stopped"
        options["registry_path"].write_text(json.dumps(data))
    def no_start(*args, **kwargs):
        pytest.fail("the fenced callback must not start/rebind a native process")
    monkeypatch.setattr(owner.CodexChatAgentSession, "start", no_start)
    before = store.path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        run_codex_operation_host(request, confirmed_operation_id="missing" if drift == "missing" else claimed["proposal_id"], **options)
    assert store.path.read_bytes() == before


def test_dynamic_pending_filters_other_managed_tasks_before_pagination(
    tmp_path: Path,
) -> None:
    from examples import operation_action_fixtures as fixtures

    service, store = fixtures.service(tmp_path, goal_id="fixture-goal")
    current = fixtures.managed_handler(service, store, goal_id="fixture-goal")
    other = fixtures.managed_handler(
        service, store, goal_id="fixture-goal", todo_id="todo-other",
        session_id="other-managed-thread",
    )

    def prepare_and_confirm(handler, index: int, thread_id: str):
        request = fixtures.request(
            goal_id="fixture-goal",
            payload={"schema_version": "qualification_v0", "marker": "synthetic"},
        )
        request["idempotency_key"] = f"pending-fixture-{index}"
        terms = request["normalized_parameters"]
        terms.pop("executor")
        terms.update(domain="qualification", operation_kind="qualification.observe", operation_schema="qualification_v0")
        terms["projection"].update(simulated=False, title="Synthetic qualification", focus="No external effect")
        prepared = handler(
            "loopx_operation", {"action": "prepare", "request": request},
            {"thread_id": thread_id, "host_turn_id": "fixture-native-turn"},
        )
        assert prepared["ok"], prepared
        proposal = prepared["proposal"]
        delivered = store.record_operation_delivery(
            proposal["proposal_id"], delivery=fixtures.delivery(proposal),
        )
        return store.decide_operation(
            proposal["proposal_id"], decision="confirm",
            confirmation=fixtures.confirmation(delivered),
        )["proposal_id"]

    # All these are current, approved canonical operations for the SAME Agent.
    # They must neither leak into this task nor exhaust its first page.
    other_ids = {
        prepare_and_confirm(other, index, "other-managed-thread")
        for index in range(25)
    }
    own_id = prepare_and_confirm(current, 25, "owned-managed-thread")
    pending = current(
        "loopx_operation", {"action": "pending"},
        {"thread_id": "owned-managed-thread", "host_turn_id": "fixture-native-turn"},
    )
    assert pending["ok"], pending
    assert pending["pending_count"] == 1
    assert {item["operation_id"] for item in pending["items"]} == {own_id}
    assert pending["next_cursor"] is None
    assert not other_ids.intersection(item["operation_id"] for item in pending["items"])

    # A task-local page token is not reusable by another execution subject.
    for index in range(26, 50):
        prepare_and_confirm(current, index, "owned-managed-thread")
    page = current(
        "loopx_operation", {"action": "pending"},
        {"thread_id": "owned-managed-thread", "host_turn_id": "fixture-native-turn"},
    )
    assert page["next_cursor"]
    rejected = other(
        "loopx_operation", {"action": "pending", "cursor": page["next_cursor"]},
        {"thread_id": "other-managed-thread", "host_turn_id": "fixture-native-turn"},
    )
    assert rejected["ok"] is False
    assert rejected["execution_allowed"] is False


def test_process_launch_without_native_acceptance_cannot_record_operation_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, claimed, request, options = _claimed_native_fixture(tmp_path)
    monkeypatch.setenv("FAKE_OPERATION_HANG", "1")
    with pytest.raises(BuiltInHostError):
        run_codex_operation_host(request, **{**options, "timeout_seconds": 0.5})
    assert store.load(claimed["proposal_id"])["operation"].get("host_start") is None


def test_start_receipt_failure_aborts_before_operation_tool_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.chat_action_store import ChatActionStore
    from loopx.control_plane.turn_driver import codex_operation_host
    store, claimed, request, options = _claimed_native_fixture(tmp_path)
    before = store.path.read_bytes()
    calls = []
    original = codex_operation_host.operation_tool_handler

    def observe(**options):
        handler = original(**options)

        def record(*args):
            calls.append(args)
            return handler(*args)

        return record

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic receipt write failure")

    monkeypatch.setattr(codex_operation_host, "operation_tool_handler", observe)
    monkeypatch.setattr(ChatActionStore, "record_agent_operation_host_start", fail)
    with pytest.raises(BuiltInHostError) as error:
        run_codex_operation_host(request, **options)
    assert str(error.value) == "codex_operation_host_start_unrecorded"
    assert calls == []
    assert store.path.read_bytes() == before
    assert store.load(claimed["proposal_id"])["operation"].get("agent_handoff") is None


def test_owned_app_server_process_authenticates_native_metadata_and_resumes_same_profile(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "fake-codex-operation"
    executable.write_text(FAKE_SERVER)
    executable.chmod(0o700)
    options = {
        "runtime_root": tmp_path / "runtime",
        "registry_path": tmp_path / "registry.json",
        "project": tmp_path,
        "codex_bin": str(executable),
        "model": "test-model",
        "reasoning_effort": "xhigh",
        "timeout_seconds": 5,
    }
    first = run_codex_operation_host(_request(), **options)
    assert first["result_kind"] == "wait"
    validation = validate_loopx_turn_host_result(
        {"transaction": {"turn_key": _request()["turn_key"]}}, first
    )
    assert validation["ok"], validation["errors"]
    binding = load_codex_cli_session(
        options["runtime_root"], lineage=_lineage(_request())
    )
    assert binding["operation_transport"] == "app-server-operation-tools-v0"
    second = run_codex_operation_host(
        _request(session_action="resume", turn_key="sha256:" + "b" * 64), **options
    )
    assert second["turn_key"] == "sha256:" + "b" * 64
    assert (
        load_codex_cli_session(options["runtime_root"], lineage=_lineage(_request()))
        == binding
    )
    with pytest.raises(ValueError, match="profile changed"):
        run_codex_operation_host(
            _request(session_action="resume"), **{**options, "reasoning_effort": "high"}
        )
    with pytest.raises(ValueError, match="original managed transport"):
        run_codex_cli_host(
            _request(session_action="resume"),
            **{key: value for key, value in options.items() if key != "registry_path"},
        )


def test_admitted_turn_cli_launches_owned_transport_without_plain_cli_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from loopx.cli import main as cli_main

    project, runtime, registry = _write_live_fixture(tmp_path)
    executable = tmp_path / "fake-codex-operation"
    executable.write_text(FAKE_SERVER)
    executable.chmod(0o700)

    def forbidden_plain_cli(*args, **kwargs):
        pytest.fail("Operation opt-in must not downgrade to plain Codex exec")

    monkeypatch.setattr("loopx.cli_commands.turn.run_codex_cli_host", forbidden_plain_cli)
    arguments = [
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--host", "codex-cli", "--project", str(project), "--scan-root", str(project),
        "--no-global-sync", "--codex-operation-tools", "--codex-bin", str(executable),
        "--codex-model", "test-model", "--codex-reasoning-effort", "xhigh",
        "--codex-sandbox", "read-only", "--timeout-seconds", "5",
        "--validation-command-json", json.dumps([sys.executable, "-c", "import json,sys; json.load(sys.stdin)"]),
    ]
    lineage = {"goal_id": "loopx-turn-fixture", "agent_id": "codex-fixture", "todo_id": "todo_fixture0001"}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        dry_exit = cli_main(arguments)
    dry = json.loads(output.getvalue())
    assert dry_exit == 0, dry
    assert load_codex_cli_session(runtime, lineage=lineage) is None
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main([*arguments, "--execute"])
    payload = json.loads(output.getvalue())
    assert exit_code == 0, json.dumps(payload, indent=2)
    assert payload["host"] == {"executable": "built-in", "kind": "codex-cli"}
    binding = load_codex_cli_session(runtime, lineage=lineage)
    assert binding["operation_transport"] == "app-server-operation-tools-v0"
    assert binding["session_id"] == "owned-app-server-thread"
    assert payload["effects"]["quota_spent"] is False


@pytest.mark.parametrize(
    "change",
    [{"model": None}, {"reasoning_effort": None}, {"sandbox": "danger-full-access"}],
)
def test_operation_host_refuses_unpinned_or_widened_profile_before_launch(
    tmp_path: Path, change: dict
) -> None:
    with pytest.raises(ValueError):
        run_codex_operation_host(
            _request(),
            runtime_root=tmp_path / "runtime",
            registry_path=tmp_path / "registry.json",
            project=tmp_path,
            **{"model": "test-model", "reasoning_effort": "xhigh", **change},
        )
    assert not (tmp_path / "runtime").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group qualification")
@pytest.mark.parametrize("hang", [False, True])
def test_owned_operation_host_reaps_descendants_on_success_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hang: bool
) -> None:
    executable = tmp_path / "fake-codex-operation"
    executable.write_text(FAKE_SERVER)
    executable.chmod(0o700)
    marker = tmp_path / "owned-child"
    monkeypatch.setenv("FAKE_OPERATION_CHILD_MARKER", str(marker))
    if hang:
        monkeypatch.setenv("FAKE_OPERATION_HANG", "1")
    options = dict(
        runtime_root=tmp_path / "runtime",
        registry_path=tmp_path / "registry.json",
        project=tmp_path,
        codex_bin=str(executable),
        model="test-model",
        reasoning_effort="xhigh",
        timeout_seconds=1 if hang else 5,
    )
    if hang:
        with pytest.raises(BuiltInHostError, match="timeout"):
            run_codex_operation_host(_request(), **options)
    else:
        assert run_codex_operation_host(_request(), **options)["result_kind"] == "wait"
    assert marker.exists()
    modified = marker.stat().st_mtime_ns
    time.sleep(0.1)
    assert marker.stat().st_mtime_ns == modified


@pytest.mark.skipif(
    not os.environ.get("LOOPX_QUALIFY_CODEX_OPERATION_HOST"),
    reason="explicit live-host release qualification only",
)
@pytest.mark.parametrize(
    ("model", "effort"), [("gpt-6-sol", "xhigh"), ("gpt-6-luna", "max")]
)
def test_live_owned_app_server_native_tool_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, effort: str
) -> None:
    from loopx.control_plane.turn_driver import codex_operation_host

    original = codex_operation_host.operation_tool_handler
    calls = []

    def observe(**options):
        handler = original(**options)

        def record(tool, arguments, native):
            result = handler(tool, arguments, native)
            calls.append((arguments.get("action"), native, result.get("ok")))
            return result

        return record

    monkeypatch.setattr(codex_operation_host, "operation_tool_handler", observe)
    request = _request()
    request["turn_envelope"]["action"]["selected_todo"]["text"] = (
        "Non-financial transport qualification only. Call loopx_operation context once. "
        "Do not use shell, create proposals, send messages or perform any external effect. "
        "Return result_kind wait with the exact supplied Turn key, using only the existing result schema. "
        "Report the context retrieval in summary; leave non-applicable material-work fields empty."
    )
    options = dict(
        runtime_root=tmp_path / "runtime",
        registry_path=tmp_path / "registry.json",
        project=tmp_path,
        model=model,
        reasoning_effort=effort,
        timeout_seconds=120,
    )
    result = run_codex_operation_host(request, **options)
    validation = validate_loopx_turn_host_result(
        {"transaction": {"turn_key": request["turn_key"]}}, result
    )
    assert validation["ok"], validation["errors"]
    assert result["turn_key"] == request["turn_key"]
    assert result["result_kind"] == "wait"
    assert calls and all(
        action == "context" and ok and native["host_turn_id"]
        for action, native, ok in calls
    )
    first_thread = calls[0][1]["thread_id"]
    first_turn = calls[0][1]["host_turn_id"]
    calls.clear()
    request["session"]["action"] = "resume"
    request["turn_key"] = "sha256:" + "b" * 64
    continued = run_codex_operation_host(request, **options)
    validation = validate_loopx_turn_host_result(
        {"transaction": {"turn_key": request["turn_key"]}}, continued
    )
    assert validation["ok"], validation["errors"]
    assert (
        continued["result_kind"] == "wait"
        and continued["turn_key"] == request["turn_key"]
    )
    assert calls and all(
        action == "context" and ok and native["thread_id"] == first_thread
        and native["host_turn_id"] != first_turn
        for action, native, ok in calls
    )
