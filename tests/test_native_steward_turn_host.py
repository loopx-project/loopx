"""Original Chat + native kernel + File/SQLite; only the model/provider are synthetic."""
import contextlib
import io
import json
import sys

import pytest

from test_native_steward_managed_mode import original, storage
from test_native_steward_private import steward as steward_fixture, finish
from loopx.cli import main
from loopx.capabilities.native_chat.steward_turn import StewardTurnHost
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

steward = steward_fixture


def test_native_task_validator_cannot_replace_a_cli_host(tmp_path):
    from loopx.cli import build_parser
    from loopx.cli_commands.turn import handle_turn_command
    args = build_parser().parse_args(["turn", "run-once", "--goal-id", "example", "--agent-id", "worker",
        "--host", "codex-cli", "--project", str(tmp_path), "--execute"])
    payload, calls = {}, []
    handle_turn_command(args, registry_path=tmp_path / "registry.json",
        runtime_root_arg=str(tmp_path / "runtime"), output_format=lambda *_a, **_kw: "json",
        print_payload=lambda value, *_a: payload.update(value),
        native_task_validator=lambda *_a: calls.append("called"))
    assert not payload["ok"] and "requires its original host connection" in payload["error"]
    assert calls == [] and not (tmp_path / "registry.json").exists()


def cli(runtime, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(["--registry", str(runtime.registry_path), "--runtime-root",
            str(runtime.coordination_runtime_root), "--format", "json", *args])
    result = json.loads(out.getvalue())
    assert code == 0 and result.get("ok"), json.dumps(result, ensure_ascii=False)
    return result


def prepare(steward, provider, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    store, runtime, _, transport, binding, capture, _ = steward
    sid, managed = original(steward)
    storage(runtime, provider)
    plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
    actor = managed.prepare(binding["binding_id"], session_id=sid,
        expected_plan_id=plan["plan_id"], execute=True)["actor"]
    added = cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "agent",
        "--text", "Inspect the synthetic monitor source and retain its evidence.",
        "--claimed-by", "steward", "--action-kind", "observe",
        "--continuation-policy", "same_agent_non_delivery", "--execute")
    return sid, actor, added


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("material", [False, True])
def test_native_turn_uses_original_app_server_and_kernel_without_a_new_session(steward, tmp_path, monkeypatch, provider, material):
    sid, actor, added = prepare(steward, provider, tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    todo_id = added.get("todo_id") or added.get("todo", {})["todo_id"]
    fake = tmp_path / "codex"
    candidate = {"result_kind": "wait", "classification": "synthetic_monitor_wait",
        "summary": "The synthetic source is unchanged; retain its due work.",
        "next_action": "Wait for its native deadline."}
    if material:
        candidate = {"result_kind": "validated_progress", "classification": "implementation",
            "summary": "Read the synthetic source and wrote its observation artifact.",
            "next_action": "Continue the original monitor integration.",
            "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "The original authorized monitor remains the current route.",
            "work_evidence": ["native-observation.json records the synthetic source"],
            "validation_evidence": ["The native host fixture independently reads the artifact after completion"]}
    text = fake.read_text().replace('"message": "Runtime response."', '"message": ' + repr(json.dumps(candidate)))
    if material:
        from loopx.agent_registry import load_goal_from_registry
        from pathlib import Path
        observation = Path(load_goal_from_registry(runtime.registry_path, actor["goal_id"])["repo"]) / "native-observation.json"
        text = text.replace('        turn_count += 1',
            f'        open({str(observation)!r}, "w").write("source observed")\n        turn_count += 1')
    fake.write_text(text)
    # Original history and exact native thread are present before native work.
    before = store.messages(sid)
    before_sessions = set(store.root.rglob(store._session_path(sid).name))
    assert store._session_path(sid) in before_sessions
    thread = store.load_session(sid)["upstream_thread_id"]
    runtime.adapters.pop(sid).close_session()  # Reopen the same synthetic native thread with this response.
    calls_before_native = capture.read_bytes()
    host = StewardTurnHost(runtime)
    validator = ([sys.executable, "-c", "from pathlib import Path; assert Path('native-observation.json').read_text() == 'source observed'"] if material else None)
    accepted, created = host.submit(binding["binding_id"], turn_instance_id="native-fixture-1", todo_id=todo_id,
        validation_command=validator)
    assert created
    completed = finish(runtime, accepted)
    assert completed["status"] == "completed", completed
    result = completed["response"]["native_loopx"]
    assert result["ok"] and result["status"] == ("committed" if material else "stopped"), json.dumps(result)
    assert result["host"]["kind"] == "codex-app-server"
    assert result["effects"]["host_invoked"] and result["effects"]["quota_spent"] is material
    if material:
        assert observation.read_text() == "source observed"
    assert store.messages(sid)[:len(before)] == before
    assert store.load_session(sid)["upstream_thread_id"] == thread
    assert set(store.root.rglob(store._session_path(sid).name)) == before_sessions
    calls = capture.read_bytes()
    native_calls = [json.loads(line) for line in calls[len(calls_before_native):].decode().splitlines()]
    assert any(row["method"] == "thread/resume" for row in native_calls)
    assert not [row for row in native_calls if row["method"] in {"thread/start", "thread/inject_items", "thread/goal/set"}]
    replay, created = host.submit(binding["binding_id"], turn_instance_id="native-fixture-1", todo_id=todo_id,
        validation_command=validator)
    assert not created and replay["turn_id"] == accepted["turn_id"]
    with pytest.raises(ValueError, match="identity conflict"):
        host.submit(binding["binding_id"], turn_instance_id="native-fixture-1", todo_id="todo_other_work")
    assert capture.read_bytes() == calls


def test_unselected_or_unavailable_work_does_not_invoke_the_model(steward, tmp_path, monkeypatch):
    sid, actor, _ = prepare(steward, "sqlite", tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    before = capture.read_bytes()
    accepted, _ = StewardTurnHost(runtime).submit(binding["binding_id"],
        turn_instance_id="missing-work", todo_id="todo_no_such_work")
    completed = finish(runtime, accepted)
    result = completed["response"]["native_loopx"]
    assert not result["ok"]
    # Ensuring an adapter may read/resume the existing thread; it must never
    # start model work or change the selected Todo when native admission fails.
    calls = [json.loads(line) for line in capture.read_text()[len(before.decode()):].splitlines()]
    assert not [row for row in calls if row["method"] == "turn/start"]
    assert not result.get("effects", {}).get("quota_spent")


@pytest.mark.parametrize("fault", ["busy", "queued", "revoked", "stopped_goal", "wrong_actor", "host_closed"])
def test_native_dispatch_refuses_before_model_or_kernel_when_original_context_is_unavailable(steward, tmp_path, monkeypatch, fault):
    sid, actor, _ = prepare(steward, "file", tmp_path, monkeypatch)
    store, runtime, provider, transport, binding, capture, _ = steward
    if fault == "busy":
        store.update_session(sid, active_turn_id="f" * 32)
    elif fault == "queued":
        store.create_queued_turn(sid, client_turn_id="human-followup", message="Keep the original user first", origin="web")
    elif fault == "revoked":
        provider.owner_ready = False
    elif fault == "stopped_goal":
        data = json.loads(runtime.registry_path.read_text())
        data["goals"][0]["status"] = "stopped"
        runtime.registry_path.write_text(json.dumps(data))
    elif fault == "host_closed":
        runtime.close()
    else:
        data = transport.bindings.read()
        data["bindings"][-1]["managed_actor"]["source_ref"] = "0" * 24
        transport.bindings.path.write_text(json.dumps(data))
    before = capture.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        StewardTurnHost(runtime).submit(binding["binding_id"], turn_instance_id="native-rejected")
    assert capture.read_bytes() == before


@pytest.mark.parametrize("fault", ["revoked", "retargeted", "closed_turn"])
def test_late_model_result_cannot_commit_after_the_original_source_changes(steward, tmp_path, monkeypatch, fault):
    sid, actor, added = prepare(steward, "sqlite", tmp_path, monkeypatch)
    store, runtime, provider, transport, binding, capture, _ = steward
    todo_id = added.get("todo_id") or added.get("todo", {})["todo_id"]
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    original_start = CodexAppServerAdapter.start_turn

    def late(adapter, message, sink):
        response = original_start(adapter, message, sink)
        if fault == "revoked":
            provider.owner_ready = False
        elif fault == "retargeted":
            current = transport.bindings.read()
            current["bindings"][-1]["managed_actor"]["source_ref"] = "0" * 24
            transport.bindings.path.write_text(json.dumps(current))
        else:
            active = store.load_session(sid)["active_turn_id"]
            store.update_turn(sid, active, status="interrupted")
        return response

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", late)
    host = StewardTurnHost(runtime)
    accepted, _ = host.submit(binding["binding_id"], turn_instance_id="late-original-source", todo_id=todo_id)
    completed = finish(runtime, accepted)
    result = completed.get("response", {}).get("native_loopx") if completed.get("response") else None
    if result:
        assert not result["ok"] and not result["effects"]["state_written"]
        assert not result["effects"]["quota_spent"]
    else:
        assert completed["status"] == "interrupted"
    before = capture.read_bytes()
    if fault == "retargeted":
        with pytest.raises(ValueError):
            host.submit(binding["binding_id"], turn_instance_id="late-original-source", todo_id=todo_id)
    else:
        assert not host.submit(binding["binding_id"], turn_instance_id="late-original-source", todo_id=todo_id)[1]
    assert capture.read_bytes() == before


def test_new_native_user_gate_wins_immediately_before_app_server_dispatch(steward, tmp_path, monkeypatch):
    sid, actor, added = prepare(steward, "sqlite", tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    todo_id = added.get("todo_id") or added.get("todo", {})["todo_id"]
    from loopx.capabilities.native_chat.codex_adapter import CodexAppServerAdapter
    original_start = CodexAppServerAdapter.start_turn

    def gated(adapter, message, sink):
        cli(runtime, "todo", "add", "--goal-id", actor["goal_id"], "--role", "user",
            "--text", "Synthetic owner gate before model dispatch.", "--task-class", "user_gate",
            "--goal-bound", "--execute")
        return original_start(adapter, message, sink)

    monkeypatch.setattr(CodexAppServerAdapter, "start_turn", gated)
    before = capture.read_bytes()
    accepted, _ = StewardTurnHost(runtime).submit(binding["binding_id"],
        turn_instance_id="gate-before-native-dispatch", todo_id=todo_id)
    completed = finish(runtime, accepted)
    result = completed["response"]["native_loopx"]
    assert not result["ok"] and not result.get("effects", {}).get("quota_spent"), result
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].decode().splitlines()]
    assert not [row for row in calls if row["method"] == "turn/start"]


def test_inventory_replacement_during_context_read_never_forks_the_original_native_thread(steward, tmp_path, monkeypatch):
    sid, actor, _ = prepare(steward, "file", tmp_path, monkeypatch)
    store, runtime, _, _, binding, capture, _ = steward
    import loopx.chat_coordination as coordination
    prepare_context = coordination.prepare_turn_context

    def changed(*args, **kwargs):
        current = json.loads(runtime.registry_path.read_text())
        current["goals"][0]["creation_operation_id"] += ".replacement"
        runtime.registry_path.write_text(json.dumps(current))
        return prepare_context(*args, **kwargs)

    monkeypatch.setattr(coordination, "prepare_turn_context", changed)
    before = capture.read_bytes()
    thread = store.load_session(sid)["upstream_thread_id"]
    accepted, _ = StewardTurnHost(runtime).submit(binding["binding_id"], turn_instance_id="context-source-replaced")
    completed = finish(runtime, accepted)
    assert completed["status"] == "failed" and completed["error_code"] == "manager_authorization_unavailable"
    calls = [json.loads(line) for line in capture.read_bytes()[len(before):].decode().splitlines()]
    assert not [row for row in calls if row["method"] in {"thread/start", "turn/start", "thread/inject_items"}]
    assert store.load_session(sid)["upstream_thread_id"] == thread
