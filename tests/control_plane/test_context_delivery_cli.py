"""Real quota sources share an exact, effect-free full/delta presentation owner.

These tests qualify the CLI and RPC, not native Host retention observation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope, prepare_context_delivery, restore_context_delivery,
)
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID,
    _heartbeat_receipt_count, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("profile", ["codex_app", "generic_cli"])
def test_cli_capture_exact_delta_and_same_capture_recovery(tmp_path: Path, monkeypatch, provider, profile):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    body = "Preserve every source row and cancellation condition. " * 500
    body += "FINAL CONDITION: stop before publication."
    todos = read_fixture_todos(state)
    todos[0].update(text=body, title=body)
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    before_source = state.read_bytes()
    host = ["--codex-app"] if profile == "codex_app" else ["--runtime-profile", profile]
    rc, guard = _run_cli(registry, runtime, "quota", "should-run", *host,
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
        "--turn-instance-id", "context-presentation", cwd=project)
    assert rc == 0 and guard["should_run"] is True, guard
    context = guard["interaction_contract"]["agent_channel"]["work_context"]
    selected = next(s["content"]["todo"] for s in context["sources"] if s.get("source") == "selected_todo")
    assert selected["text"] == body
    assert selected.get("title") != body  # only the exact duplicate is elided
    turn_context = build_turn_envelope(guard)["work_context"]
    assert turn_context["sources"] == context["sources"]
    assert turn_context["instruction"] == context["instruction"]
    original = deepcopy(guard)
    scope = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, session_id=profile, context_epoch="epoch-a")
    full = prepare_context_delivery(context, scope=scope, capture_id="capture-a")
    assert full["mode"] == "full" and full["context"] == context
    warm = prepare_context_delivery(context, scope=scope, capture_id="capture-b",
        baseline=full, retained={"complete": True, "target": full["target"]})
    assert warm["mode"] == "delta"
    assert len(json.dumps(warm).encode()) < len(json.dumps(full).encode()) / 2
    current = prepare_context_delivery(context, scope=scope, capture_id="capture-b")
    assert restore_context_delivery(warm, baseline=full, current=current)["context"] == context
    turn_full = prepare_context_delivery(turn_context, scope=scope, capture_id="turn-capture")
    turn_delta = prepare_context_delivery(turn_context, scope=scope, capture_id="turn-capture",
        baseline=full, retained={"complete": True, "target": full["target"]})
    assert turn_delta["mode"] == "delta"
    assert restore_context_delivery(turn_delta, baseline=full, current=turn_full)["context"] == turn_context
    for baseline, delivery in [(None, warm), (full, {**warm, "changes": None})]:
        recovered = restore_context_delivery(delivery, baseline=baseline, current=current)
        assert recovered["mode"] == "full" and recovered["context"] == context
        assert recovered["target"] == current["target"]
    reset = prepare_context_delivery(context, scope={**scope, "context_epoch": "epoch-b"},
        capture_id="capture-b", baseline=full, retained={"complete": True, "target": full["target"]})
    assert reset["mode"] == "full" and reset["context"] == context
    # The same RPC now transports the whole ordinary quota packet and whole
    # Turn envelope, rather than assuming the common work_context is the packet.
    for surface, projection in [("quota_packet", guard), ("turn_envelope", build_turn_envelope(guard))]:
        first = prepare_context_delivery(projection, scope=scope, capture_id="whole-a", surface=surface)
        next_capture = prepare_context_delivery(projection, scope=scope, capture_id="whole-b", surface=surface)
        delta = prepare_context_delivery(projection, scope=scope, capture_id="whole-b", surface=surface,
            baseline=first, retained={"complete": True, "target": first["target"]})
        assert delta["mode"] == "delta"
        restored = restore_context_delivery(delta, baseline=first, current=next_capture)
        assert restored["mode"] == "delta" and restored["context"] == projection
        missing = restore_context_delivery(delta, baseline=None, current=next_capture)
        assert missing["mode"] == "full" and missing["context"] == projection
    # Neither native source state, the signed decision nor settlement is changed
    # by presentation, warm reuse or recovery. A new capture is not an admission.
    assert state.read_bytes() == before_source
    assert guard == original
    assert _spend_run_count(runtime) == 0
    rc, readback = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID, "--todo-id", TODO_ID)
    assert rc == 0 and readback["todo"]["text"] == body, readback
    assert readback["todo"]["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("surface", ["quota_packet", "turn_envelope"])
@pytest.mark.skipif(os.name != "posix", reason="Unix local Host transport")
def test_real_cli_host_rendezvous_sends_delta_and_keeps_same_capture_full(tmp_path, monkeypatch, provider, surface):
    """The ordinary CLI really uses the codec; the listener models a Host,
    not a claim that a particular installed Codex has observed retention."""
    import hashlib
    import socket
    import tempfile
    from concurrent.futures import ThreadPoolExecutor
    from test_quota_settlement_cli import _heartbeat_receipt_count

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    body = "Retain full task evidence and permission boundaries. " * 800 + "Stop before publication."
    todos = read_fixture_todos(state)
    todos[0].update(text=body, title=body)
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    before_source = state.read_bytes()
    root = tmp_path / "captures"
    root.mkdir()
    native_directory = tmp_path / "unobserved-native-state"
    native_directory.mkdir(mode=0o700)
    scope = dict(goal_id=GOAL_ID, agent_id=AGENT_ID, session_id="host-session", context_epoch="epoch-1")
    saved = []

    def invoke(host_request, *, opt_in=True, native_context=False, setting="on"):
        with tempfile.TemporaryDirectory(prefix="context-host-", dir="/tmp") as short:
            endpoint = Path(short).resolve() / "host.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(endpoint))
                server.listen(1)
                server.settimeout(30)
                def release():
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(30)
                        with connection.makefile("rb") as stream:
                            observed = json.loads(stream.readline(4096))
                        path = Path(observed["path"])
                        raw = path.read_bytes()
                        assert hashlib.sha256(raw).hexdigest() == observed["sha256"]
                        if opt_in:
                            request = path.parent / "delivery-request.json"
                            request.write_text(host_request if isinstance(host_request, str) else json.dumps(host_request))
                            request.chmod(0o600)
                        response = {"released": True, "sha256": observed["sha256"]}
                        if opt_in:
                            response["context_delivery"] = True
                        connection.sendall(json.dumps(response).encode() + b"\n")
                        return path, raw
                with ThreadPoolExecutor(max_workers=1) as workers:
                    future = workers.submit(release)
                    code, wire = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
                        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
                        "--turn-instance-id", "host-context-delivery",
                        "--context-delivery", setting,
                        "--decision-output-root", str(root), "--decision-handoff-socket", str(endpoint),
                        *(["--codex-context-dir", str(native_directory)] if native_context else []),
                        *(["--turn-envelope"] if surface == "turn_envelope" else []), cwd=project)
                    path, raw = future.result(timeout=30)
        assert code == 0, wire
        assert path.read_bytes() == raw
        saved.append((path, raw))
        return wire, path.parent

    cold, first = invoke({"scope": scope})
    assert cold["mode"] == "full"
    assert json.loads((first / "projection.json").read_bytes()) == cold["context"]
    warm, second = invoke({"scope": scope, "baseline": cold,
        "retained": {"complete": True, "target": cold["target"]}})
    assert warm["mode"] == "delta"
    projection = json.loads((second / "projection.json").read_bytes())
    current = prepare_context_delivery(projection, scope=scope,
        capture_id=warm["target"]["capture_id"], surface=surface)
    assert restore_context_delivery(warm, baseline=cold, current=current)["context"] == projection
    assert len(json.dumps(warm)) < len(json.dumps(current))
    assert warm["recovery"]["path"] == str(second / "projection.json")
    assert warm["target"]["capture_id"] == hashlib.sha256((second / "decision.json").read_bytes()).hexdigest()
    assert warm["recovery"]["sha256"] == hashlib.sha256((second / "projection.json").read_bytes()).hexdigest()
    # Two consumers cannot compose nested envelopes or transfer retention
    # authority. The explicitly selected native consumer has no proof here.
    no_native_proof, _ = invoke({"scope": scope, "baseline": cold,
        "retained": {"complete": True, "target": cold["target"]}}, native_context=True)
    assert "target" not in no_native_proof and "context" not in no_native_proof
    assert no_native_proof["ok"] and no_native_proof.get("schema_version") != "context_delivery_v1"
    before_receipts = _heartbeat_receipt_count(runtime, "host-context-delivery")
    # Compaction between preparation and dispatch: the Host can select the exact
    # full projection without a second CLI call, admission or state mutation.
    recovered = restore_context_delivery(warm, baseline=None, current=current)
    assert recovered["mode"] == "full" and recovered["context"] == projection
    assert _heartbeat_receipt_count(runtime, "host-context-delivery") == before_receipts
    reset, _ = invoke({"scope": {**scope, "context_epoch": "compacted"}, "baseline": cold,
        "retained": {"complete": True, "target": cold["target"]}})
    assert reset["mode"] == "full"
    unobserved, directory = invoke({"scope": scope, "baseline": cold,
        "retained": {"complete": True, "target": cold["target"]}, "native_input": {}})
    assert unobserved["mode"] == "full" and unobserved["reason"] == "baseline_unavailable"
    assert unobserved["context"] == json.loads((directory / "projection.json").read_bytes())
    for malformed in ["{truncated", "[" * 2000 + "]" * 2000,
                      {"scope": {**scope, "agent_id": "another-agent"}}]:
        full, directory = invoke(malformed)
        assert "target" not in full  # Automatic ordinary full output, not a gate.
        assert full == json.loads((directory / "projection.json").read_bytes())
    # A host-side request cannot enable this feature when the caller is off.
    disabled, disabled_directory = invoke({"scope": scope, "baseline": cold,
        "retained": {"complete": True, "target": cold["target"]}}, setting="off")
    assert "target" not in disabled and not (disabled_directory / "projection.json").exists()
    ordinary, directory = invoke(None, opt_in=False)
    assert "target" not in ordinary and not (directory / "projection.json").exists()
    assert state.read_bytes() == before_source and _spend_run_count(runtime) == 0
    assert all(path.read_bytes() == raw for path, raw in saved)


def test_optional_capture_projection_failure_falls_back_without_hiding_source(tmp_path, monkeypatch):
    from loopx.control_plane.turn_driver.decision_capture import project_captured_context
    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.effect_runtime import EffectRuntimeStartupError
    payload = {"ok": True, "goal_id": "fixture", "requirements": "Keep late stop conditions."}
    (tmp_path / "decision.json").write_text(json.dumps(payload))
    def unavailable(*args, **kwargs):
        raise EffectRuntimeStartupError("synthetic unavailable codec")
    monkeypatch.setattr(turn_envelope, "prepare_context_delivery", unavailable)
    assert project_captured_context(payload, directory=tmp_path, request={"scope": {
        "goal_id": "fixture", "agent_id": "agent", "session_id": "session", "context_epoch": "epoch"}},
        goal_id="fixture", agent_id="agent", surface="quota_packet", capture_id="fixture-capture") is payload
    assert json.loads((tmp_path / "projection.json").read_bytes()) == payload


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_context_cli_without_retention_stays_full_and_preserves_receipt(tmp_path, monkeypatch, provider):
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook
    from test_quota_settlement_cli import _heartbeat_receipt_count

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_THREAD_ID", "native-session")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=read_fixture_todos(state), handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    original = state.read_bytes()
    root, captures = tmp_path / "context", tmp_path / "captures"
    root.mkdir(mode=0o700)
    captures.mkdir()
    event = dict(session_id="native-session", cwd=str(project), hook_event_name="SessionStart", source="startup")
    codex_context_hook(str(root), event)
    for _ in range(2):
        rc, wire = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "native-full",
            "--decision-output-root", str(captures), "--context-delivery", "on", "--codex-context-dir", str(root), cwd=project)
        assert rc == 0 and wire["mode"] == "full", wire
        # Consume the advertised first page and continuation, not another guard.
        from loopx.cli import build_parser
        from loopx.cli_commands.quota_capture import read_context_capture
        import shlex
        page, observed = wire, b""
        while True:
            assert page["offset"] == len(observed)
            observed += page["content"].encode()
            if page["eof"]:
                break
            args = build_parser().parse_args(shlex.split(page["next_command"])[1:])
            page = read_context_capture(args)
            assert page["ok"]
        assert hashlib.sha256(observed).hexdigest() == wire["sha256"]
        assert json.loads(observed) == json.loads(Path(wire["recovery"]["path"]).read_text())
        assert json.loads(observed)["heartbeat_receipt"]["turn_instance_id"] == "native-full"
        assert len(wire["content"].encode()) <= 3072 and "context" not in wire
    assert _heartbeat_receipt_count(runtime, "native-full") == 1
    assert _spend_run_count(runtime) == 0 and state.read_bytes() == original


@pytest.mark.parametrize("native_failure", [None, "projection", "directory"])
def test_native_projection_never_layers_host_projection(tmp_path, monkeypatch, native_failure):
    from loopx.cli import build_parser
    from loopx.cli_commands import quota
    from loopx.control_plane.turn_driver import codex_context

    args = build_parser().parse_args(["--format", "json", "quota", "should-run",
        "--goal-id", "fixture", "--agent-id", "worker", "--context-delivery", "on", "--codex-app", "--codex-context-dir", str(tmp_path)])
    original = {"ok": True, "goal_id": "fixture", "agent_identity": {"agent_id": "worker"},
        "heartbeat_receipt": {"turn_instance_id": "original-turn", "status": "committed"}}
    monkeypatch.setattr(quota, "handoff_decision", lambda *args: ({"scope": "host-request"}, "digest"))
    monkeypatch.setattr(quota, "_project_quota_cli_payload", lambda payload, *args, **kwargs: payload)

    def competing(*args, **kwargs):
        pytest.fail("explicit native observation must not compose two projection owners")

    def native(payload, **kwargs):
        assert payload == original
        if native_failure == "directory":
            pytest.fail("failed native directory preparation must not reach projection")
        if native_failure == "projection":
            raise OSError("native observation unavailable")
        return {"native_projection": payload}

    def unavailable_directory(**kwargs):
        raise OSError("native directory unavailable")

    monkeypatch.setattr(quota, "project_captured_context", competing)
    monkeypatch.setattr(codex_context, "project_codex_context", native)
    if native_failure == "directory":
        monkeypatch.setattr(codex_context, "codex_context_directory", unavailable_directory)
    printed = []
    assert quota._emit_quota_result(original, args, context=None, registry_path=tmp_path / "absent.json",
        heartbeat_turn_id=None, usage_quota_started=0, capture_directory=tmp_path,
        detail_sections=frozenset(), print_payload=lambda value, *args: printed.append(value)) == (1 if native_failure else 0)
    if native_failure:
        assert len(printed) == 1 and printed[0]["ok"] is False
        assert printed[0]["error_code"] == "quota_decision_transport_failed"
        assert printed[0]["heartbeat_receipt"] == original["heartbeat_receipt"]
        assert printed[0]["captured_decision"] == {"path": str(tmp_path / "decision.json")}
    else:
        assert len(printed) == 1 and printed[0]["eof"] is True
        assert json.loads(printed[0]["content"]) == {"native_projection": original}
    assert json.loads((tmp_path / "decision.json").read_text()) == original


@pytest.mark.parametrize("scenario", [None, "off"])
def test_plain_quota_context_default_does_not_touch_transport(tmp_path, monkeypatch, scenario):
    from loopx.control_plane.turn_driver import codex_context
    from loopx.cli import build_parser

    def unexpected(*args, **kwargs):
        pytest.fail("off must not observe native context or create transport state")
    monkeypatch.setattr(codex_context, "project_codex_context", unexpected)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=read_fixture_todos(state), handoff_mode="soft_claim"),
        state_path=state, provider="file")
    args = ["quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "off-scenario"]
    if scenario:
        args += ["--context-delivery", scenario]
    code, result = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and result["ok"] and result.get("schema_version") != "context_delivery_v1", result
    # Absence is distinct from an explicit off so a configured machine default
    # can be inherited. The ordinary output above still proves default-off.
    assert build_parser().parse_args(["quota", "should-run"]).context_delivery is None


@pytest.mark.parametrize("activation", [[], ["--context-delivery", "off"], ["--context-delivery", "on"]])
def test_off_keeps_full_but_explicit_unsupported_on_errors_before_admission(tmp_path, monkeypatch, activation):
    monkeypatch.delenv("LOOPX_CONTEXT_DELIVERY", raising=False)
    monkeypatch.delenv("LOOPX_EXECUTION_MODE", raising=False)
    project, runtime, registry = _write_fixture(tmp_path)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=read_fixture_todos(state), handoff_mode="soft_claim"),
        state_path=state, provider="file")
    root = tmp_path / "never-create"
    code, result = _run_cli(registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "unsupported",
        "--codex-context-dir", str(root), *activation, cwd=project)
    assert not root.exists()
    if activation == ["--context-delivery", "on"]:
        assert code != 0 and result["ok"] is False, result
        assert "supported declared run mode" in result["reason"]
        assert _heartbeat_receipt_count(runtime, "unsupported") == 0
        assert _spend_run_count(runtime) == 0
    else:
        assert code == 0 and result["ok"] and result.get("schema_version") != "context_delivery_v1", result


@pytest.mark.parametrize("command", ["context-config", "context-hook", "context-project", "should-run"])
@pytest.mark.parametrize("host", [[], ["--host-surface", "codex_app"], ["--runtime-profile", "claude_code"]])
def test_unsupported_native_commands_fail_without_creating_transport_or_authority(tmp_path, command, host):
    # A missing registry/capture must not hide this explicit configuration error
    # or turn it into an optional-transport full fallback (including hooks).
    runtime, registry = tmp_path / "runtime", tmp_path / "missing-registry.json"
    native, captures = tmp_path / "native", tmp_path / "captures"
    code, result = _run_cli(registry, runtime, "quota", command, *host, "--context-delivery", "on",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "unqualified",
        "--codex-context-dir", str(native), "--decision-output-root", str(captures), cwd=tmp_path)
    assert code != 0 and result["ok"] is False, result
    assert "supported declared run mode" in result["reason"]
    assert not native.exists() and not captures.exists() and not registry.exists()
    assert _heartbeat_receipt_count(runtime, "unqualified") == 0
    assert _spend_run_count(runtime) == 0
