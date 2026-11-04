"""A warm replacement retains work, not just research prose in a new Goal."""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest

from benchmark.runtime import sforge_warm_start as warm
from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.cli import main
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.control_plane.turn_driver.codex_agent_session import CodexAgentSession
from loopx.control_plane.turn_driver.codex_sessions import _store_codex_cli_session, load_codex_cli_session
from loopx.hosts.codex_checkpoint import admit_native_checkpoint_session, native_session_checkpoint
from loopx.hosts.research_checkpoint import _loopx_state_components, research_context, verify_restored_loopx_state


@pytest.fixture
def release_source_lease():
    return False


@pytest.fixture
def retained_point(tmp_path, monkeypatch, capsys, request, release_source_lease):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    workspace, runtime, registry = tmp_path / "workspace", tmp_path / "runtime", tmp_path / "control/registry.json"
    workspace.mkdir()
    registry.parent.mkdir()
    monkeypatch.chdir(workspace)

    def cli(*args):
        code = main(["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime), *args])
        result = json.loads(capsys.readouterr().out)
        assert code == 0, result
        return result

    variant = getattr(request, "param", ("sqlite", False))
    provider, real_host = variant if isinstance(variant, tuple) else ("sqlite", variant)
    if provider == "file":
        defaults = tmp_path / "storage.json"
        defaults.write_text(json.dumps({"schema_version": "loopx_goal_storage_defaults_v1", "new_goal_provider": "file",
            "canonical_creation": True, "new_goal_handoff_mode": "hard_lease"}))
        plan = cli("machine-config", "preview", "--namespace", "goal_storage", "--config-json", str(defaults))
        cli("machine-config", "apply", "--namespace", "goal_storage", "--config-json", str(defaults),
            "--expected-plan-revision", plan["plan_revision"], "--execute")
    cli("bootstrap", "--project", str(workspace), "--goal-id", "research", "--objective", "Improve verified outcomes.",
        "--adapter-kind", "read_only_project_map_v0", "--adapter-status", "connected-read-only",
        "--write-scope", "**", "--no-global-sync")
    cli("configure-goal", "--goal-id", "research", "--registered-agent", "researcher",
        "--agent-work-mode", "researcher=active", "--execution-replan-after-turns", "6", "--execute")
    todo = cli("todo", "add", "--goal-id", "research", "--role", "agent", "--claimed-by", "researcher",
        "--task-class", "advancement_task", "--priority", "P0",
        "--text", "Continue complete experiments until acceptance or a real stop.")["todo_id"]
    cli("todo", "add", "--goal-id", "research", "--role", "agent", "--claimed-by", "researcher",
        "--task-class", "advancement_task", "--priority", "P2", "--text", "Qualify the final outcome independently.")
    code = workspace / "candidate.txt"
    code.write_text("retained candidate")
    stack = ExitStack()
    request.addfinalizer(stack.close)
    if real_host:
        binary = os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN")
        if not binary:
            pytest.skip("Requires explicit real native Host; inference is a deterministic local fixture")
        from tests.control_plane.codex_checkpoint_fixture import real_native_source
        native_env, source_session, _, calls = stack.enter_context(real_native_source(
            tmp_path / "native", binary, workspace, close_source=True))
        home = Path(native_env["CODEX_HOME"])
    else:
        home = tmp_path / "native-home"
        home.mkdir()
        (home / "config.toml").write_text('model="fixture"\nmodel_reasoning_effort="high"\n[features]\ngoals=false\n')
        native_env, source_session, calls = {"CODEX_BIN": sys.executable, "CODEX_HOME": str(home)}, "source-session", []
    env = {**os.environ, **native_env, "LOOPX_PROJECT": str(workspace), "LOOPX_RUNTIME_ROOT": str(runtime),
        "LOOPX_REGISTRY": str(registry), "LOOPX_GOAL_ID": "research", "LOOPX_AGENT_ID": "researcher",
        "MODEL_NAME": "fixture", "REASONING_EFFORT": "high", "LOOPX_CODEX_SANDBOX": "read-only"}
    wake = CodexAgentSession(env, context="fresh", sandbox="read-only", receipt={})
    wake.admission.accept_result(lambda: _store_codex_cli_session(runtime, lineage=wake.lineage,
        session_scope="agent", session_id=source_session, goal_ref=wake.goal_ref, session_profile_digest=wake.digest))
    from loopx.capabilities.explore.result_log import append_explore_result_event, build_explore_node_event, explore_result_log_path
    append_explore_result_event(explore_result_log_path(runtime, "research"), build_explore_node_event(
        goal_id="research", node_id="rejected", title="Retained counterexample", status="dead_end"))
    # Keep a real current lease in the point. Restoration does not mint or
    # extend it; the ordinary task owner must inspect its current validity.
    lease = cli("task-lease", "acquire", "--goal-id", "research", "--todo-id", todo,
        "--owner", "researcher", "--idempotency-key", "original-work")["lease"]
    actor = ["--goal-id", "research", "--agent-id", "researcher", "--todo-id", todo,
             "--turn-instance-id", "source-work"]
    cli("quota", "should-run", *actor, "--runtime-profile", "generic_cli", "--context-delivery", "off",
        "--available-capability", "shell")
    vision = tmp_path / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Continue validated complete experiments until the original acceptance."},
        "path_delta": {"outcome": "continue", "prior_assumption": "Retain counterexamples while improving candidates.",
            "observed_reality": "The first complete experiment retained a validated candidate.",
            "retained": ["Complete experiments and original acceptance"], "evidence_refs": ["validation:fixture"]}}))
    cli("refresh-state", *actor, "--classification", "validated_experiment", "--delivery-outcome", "outcome_progress",
        "--delivery-batch-scale", "multi_surface", "--agent-vision-json", str(vision),
        "--suppress-external-sinks", "--no-global-sync")
    cli("quota", "spend-slot", *actor, "--source", "heartbeat", "--execute")
    settled = read_heartbeat_settlement(runtime, goal_id="research", agent_id="researcher", todo_id=todo,
        turn_instance_id="source-work", registry_path=registry)
    assert settled.progress["state"] == "settled"
    if release_source_lease:
        cli("task-lease", "release", "--goal-id", "research", "--todo-id", todo, "--owner", "researcher",
            "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]))
    metadata = {"worker_file_snapshot": {"components": [{"id": "workspace", "source_path": str(workspace)}]},
                "session": {"session_id": source_session, "session_profile_digest": wake.digest}}
    if real_host:
        native = native_session_checkpoint(env, source_session, timeout=30)
        components, metadata["native_session"] = native["components"], native["metadata"]
    else:
        components = []
        metadata["native_session"] = {"session_id": source_session, "guest_workspace": str(workspace),
            "guest_home": str(home), "config_sha256": hashlib.sha256((home / "config.toml").read_bytes()).hexdigest()}
    components = [*components, {"id": "workspace", "source_path": str(workspace)}]
    point = tmp_path / "point"
    components, metadata["loopx_state"] = _loopx_state_components(env, components, point)
    context = research_context(env, deadline=time.time() + 60)
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context))
    components.append({"id": "context", "source_path": str(context_path)})
    metadata["research_context"] = {"component_id": "context", "member_path": "",
        "sha256": hashlib.sha256(context_path.read_bytes()).hexdigest()}
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components})
    sealed = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "capture", "components": components,
        "point_metadata": metadata, "expected_source_sha256": probe["source_sha256"], "destination": str(point)})
    config = tmp_path / "source-config.toml"
    config.write_bytes((home / "config.toml").read_bytes())
    monkeypatch.setattr(warm, "_REGISTRY", str(registry))
    monkeypatch.setattr(warm, "_LOOPX_RUNTIME", str(runtime))
    monkeypatch.setattr(warm, "_CODEX_HOME", str(home))
    start = warm.WarmStart(point, sealed["sha256"], config, "research", "researcher", todo,
        int(time.time()) + 600)
    declaration = {"root": str(point), "sha256": sealed["sha256"], "deadline_epoch": start.deadline_epoch}
    return locals()


def _empty_receiver(case):
    for name in ("runtime", "registry", "home"):
        path = case[name]
        shutil.move(path, path.with_name(path.name + ".source"))
    case["runtime"].mkdir()  # Ordinary installer creates an empty directory.


@pytest.mark.parametrize("retained_point", [("file", False), ("sqlite", False), ("sqlite", True)], indirect=True)
def test_full_restore_preserves_canonical_work_and_requires_exact_binding_cas(retained_point, monkeypatch):
    c = retained_point
    assert c["start"].verify(str(c["workspace"]))
    _empty_receiver(c)
    warm.restore_worker(c["declaration"], c["workspace"])
    verify_restored_loopx_state(c["env"], c["point"], c["metadata"], deadline=time.time() + 60)
    before = research_context(c["env"], deadline=time.time() + 60)
    assert before == c["context"]
    assert before["snapshot"]["source_authority"] == c["provider"] + "_v0"
    if not c["real_host"]:
        def fork(*args, **kwargs):
            c["home"].mkdir()
            (c["home"] / "config.toml").write_bytes(c["config"].read_bytes())
            return {"session_id": "verified-fork", "model_calls": 0}
        monkeypatch.setattr("loopx.hosts.codex_checkpoint.fork_native_checkpoint", fork)
    admitted = admit_native_checkpoint_session(c["env"], c["point"], c["sealed"]["sha256"], c["config"],
        sandbox="read-only", timeout=60, restore_loopx_state=True)
    assert admitted["session_binding_admitted"] and admitted["model_calls"] == 0
    binding = load_codex_cli_session(c["runtime"], lineage=c["wake"].lineage, session_scope="agent")
    assert binding["session_id"] != c["source_session"] and binding.get("goal_ref") == c["wake"].goal_ref
    assert research_context(c["env"], deadline=time.time() + 60) == before
    lease = c["cli"]("task-lease", "inspect", "--goal-id", "research", "--todo-id", c["todo"])["lease"]
    assert lease["version"] == c["lease"]["version"] and lease["expires_at"] == c["lease"]["expires_at"]
    todos = c["cli"]("todo", "list", "--goal-id", "research")["todos"]
    assert len(todos) == 2 and c["todo"] in {row["todo_id"] for row in todos}
    assert not any(row.get("title", "").startswith("Read the full retained checkpoint") for row in todos)
    assert c["code"].read_text() == "retained candidate"
    settled = read_heartbeat_settlement(c["runtime"], goal_id="research", agent_id="researcher", todo_id=c["todo"],
        turn_instance_id="source-work", registry_path=c["registry"])
    assert settled.identity.value == c["settled"].identity.value and settled.progress == c["settled"].progress
    guard = c["cli"]("quota", "should-run", "--goal-id", "research", "--agent-id", "researcher",
        "--turn-instance-id", "continued-work", "--runtime-profile", "generic_cli", "--context-delivery", "off",
        "--available-capability", "shell")
    assert guard["selected_todo"]["todo_id"] == c["todo"]
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert len(c["calls"]) == (1 if c["real_host"] else 0)


@pytest.mark.parametrize("release_source_lease", [True])
@pytest.mark.parametrize("retained_point", [("file", False), ("sqlite", False)], indirect=True)
def test_restored_released_lease_requires_claim_for_same_work(retained_point):
    c = retained_point
    _empty_receiver(c)
    warm.restore_worker(c["declaration"], c["workspace"])
    cli = c["cli"]
    binding = ("--goal-id", "research", "--agent-id", "researcher", "--todo-id", c["todo"],
               "--turn-instance-id", "resumed-work")
    args = ("quota", "should-run", *binding, "--runtime-profile", "generic_cli", "--context-delivery", "off")
    pending = cli(*args)
    assert pending["execution_preparation"]["reason_code"] == "task_lease_released"
    assert pending["normal_delivery_allowed"] is False
    assert cli(*pending["execution_preparation"]["claim"]["cli_args"])["ok"]
    ready = cli(*args)
    assert ready["execution_preparation"]["state"] == "ready" and ready["normal_delivery_allowed"]
    assert ready["selected_todo"]["todo_id"] == c["todo"]
    assert ready["heartbeat_receipt"]["turn_instance_id"] == "resumed-work"
    assert len(cli("todo", "list", "--goal-id", "research")["todos"]) == 2


def test_full_restore_cannot_replace_a_binding_changed_during_native_fork(retained_point, monkeypatch):
    c = retained_point
    _empty_receiver(c)
    warm.restore_worker(c["declaration"], c["workspace"])
    def fork(*args, **kwargs):
        c["home"].mkdir()
        (c["home"] / "config.toml").write_bytes(c["config"].read_bytes())
        _store_codex_cli_session(c["runtime"], lineage=c["wake"].lineage, session_scope="agent",
            session_id="concurrent-session", goal_ref=c["wake"].goal_ref, session_profile_digest=c["wake"].digest)
        return {"session_id": "verified-fork", "model_calls": 0}
    monkeypatch.setattr("loopx.hosts.codex_checkpoint.fork_native_checkpoint", fork)
    with pytest.raises(ValueError, match="binding changed"):
        admit_native_checkpoint_session(c["env"], c["point"], c["sealed"]["sha256"], c["config"],
            sandbox="read-only", timeout=60, restore_loopx_state=True)
    binding = load_codex_cli_session(c["runtime"], lineage=c["wake"].lineage, session_scope="agent")
    assert binding["session_id"] == "concurrent-session"


def test_restored_work_keeps_current_user_gate_and_does_not_change_cadence(retained_point):
    c = retained_point
    _empty_receiver(c)
    warm.restore_worker(c["declaration"], c["workspace"])
    c["cli"]("todo", "add", "--goal-id", "research", "--role", "user", "--task-class", "user_gate",
        "--global-gate", "--text", "Authorize the next operation.")
    guard = c["cli"]("quota", "should-run", "--goal-id", "research", "--agent-id", "researcher",
        "--turn-instance-id", "current-gate", "--runtime-profile", "generic_cli", "--context-delivery", "off")
    assert guard["should_run"] is False
    profile = c["cli"]("configure-goal", "--goal-id", "research")
    assert (profile.get("after") or profile["before"])["execution_profile"]["replan_after_effective_turns"] == 6


@pytest.mark.parametrize("collision", ["registry", "runtime"])
def test_full_restore_never_overwrites_a_live_or_partial_receiver(retained_point, collision):
    c = retained_point
    if collision == "runtime":
        c["registry"].unlink()
    else:
        shutil.rmtree(c["runtime"])
    with pytest.raises(ValueError, match="already has state"):
        warm.restore_worker(c["declaration"], c["workspace"])
    assert c["code"].read_text() == "retained candidate"
    assert not c["workspace"].with_name("workspace.before-checkpoint").exists()


@pytest.mark.parametrize("change", [{"goal_id": "fresh"}, {"agent_id": "fresh"}])
def test_full_state_cannot_be_substituted_for_research_import(retained_point, change):
    c = retained_point
    with pytest.raises(ValueError):
        replace(c["start"], **change).verify(str(c["workspace"]))
