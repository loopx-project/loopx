"""Point-of-use optional guidance must preserve the owning settlement contract."""
from copy import deepcopy
import json

import pytest

from loopx.capabilities.explore.result_log import explore_result_log_path
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_explore_result_writeback import fixture


@pytest.mark.parametrize("mode", ["off", "evidence", "planning"])
def test_settlement_attachment_preserves_commands_and_off_plan(tmp_path, mode):
    from loopx.capabilities.explore.turn_context import project_settlement_attachment
    from loopx.configure_goal import configure_goal
    from loopx.control_plane.quota.effect_program import build_turn_scoped_cli_settlement_plan

    args = fixture(tmp_path, enabled=False)
    configure_goal(registry_path=args["registry_path"], goal_id="research",
                   execute=True, explore_mode=mode)
    plan = build_turn_scoped_cli_settlement_plan(
        goal_id="research", agent_id="worker", todo_id=args["todo_id"],
        turn_instance_id="attachment-plan", scoped_cli_args="", lifecycle_actor_args="",
    ).as_dict()
    before = deepcopy(plan)
    projected = project_settlement_attachment(plan, registry_path=args["registry_path"])
    assert plan == before
    if mode == "off":
        assert projected is plan
    else:
        writeback = projected["ordered_steps"][1]
        attachment, = writeback.pop("optional_attachments")
        assert attachment["required"] is False
        assert attachment["option"] == "--explore-result-json <result.json>"
        assert attachment["inline_option"] == "--agent-vision-json <vision.json>"
        assert attachment["inline_field"] == "explore_result"
        assert attachment["attachment_schema"] == "explore_result_attachment_v0"
        assert "Routine work needs no attachment" in attachment["guidance"]
        assert "open Todo" in attachment["guidance"]
        assert "end that execution only after explore_result_delivery.ok=true" in attachment["guidance"]
        assert "readback-only and needs no new claim" in attachment["guidance"]
        assert "unfinished delivery still requires current ownership and execution checks" in attachment["guidance"]
        assert attachment["path_delta_attachment_schema"] == "explore_result_from_path_delta_v0"
        assert "observation" not in attachment["path_delta_attachment_template"]
        assert projected == before  # Including command, order, receipts and gates.
    # Unbound planning cannot consume a Todo-scoped result attachment.
    unbound = {**plan, "identity": {**plan["identity"], "todo_id": None}}
    assert project_settlement_attachment(unbound, registry_path=args["registry_path"]) is unbound


@pytest.mark.parametrize("mode", ["evidence", "planning"])
@pytest.mark.parametrize("profile", ["--codex-app", "generic_cli"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_cli_settlement_exposes_optional_attachment(tmp_path, monkeypatch, mode, profile, provider):
    from loopx.configure_goal import configure_goal
    from tests.control_plane.test_quota_settlement_cli import (
        _write_fixture, _initialize_fixture_authority, _run_cli, GOAL_ID, AGENT_ID, TODO_ID, TURN_ID,
    )
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, path = _write_fixture(tmp_path)
    configure_goal(registry_path=path, goal_id=GOAL_ID, execute=True, explore_mode=mode)
    _initialize_fixture_authority(project, runtime, provider=provider)
    profile_args = [profile] if profile.startswith("--") else ["--runtime-profile", profile]
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
               "--turn-instance-id", TURN_ID]
    command = ["quota", "should-run", *profile_args, *binding, ]
    rc, guard = _run_cli(path, runtime, *command, cwd=project)
    assert rc == 0, guard
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    writeback = plan["ordered_steps"][1]
    assert writeback["optional_attachments"][0]["required"] is False
    assert "--explore-result-json" not in writeback["command_template"]
    rc, compact = _run_cli(path, runtime, *command, "--turn-envelope", cwd=project)
    assert rc == 0, compact
    assert compact["writeback"]["settlement_plan"] == plan
    rc, completed = _run_cli(path, runtime, "todo", "complete", *binding,
                            "--evidence", "Synthetic deliverable validated", "--no-follow-up", cwd=project)
    assert rc != 0 and completed["settlement_blocked_completion"], completed
    # Premature terminal closeout returns the original recovery command and
    # affordance; evidence attachment never bypasses the ordinary receipt gate.
    def plans(value):
        if isinstance(value, dict):
            if value.get("schema_version") == "quota_settlement_plan_v1":
                yield value
            for child in value.values():
                yield from plans(child)
        elif isinstance(value, list):
            for child in value:
                yield from plans(child)
    completion_plans = list(plans(completed))
    assert completion_plans, completed
    assert any(p["ordered_steps"][1].get("optional_attachments") == writeback["optional_attachments"]
               for p in completion_plans)
    assert not explore_result_log_path(runtime, GOAL_ID).exists()


@pytest.mark.parametrize("mode", ["off", "evidence", "planning"])
def test_driver_owned_turn_plan_does_not_invent_cli_attachment_command(tmp_path, mode):
    import subprocess
    import sys
    from tests.test_loopx_turn_driver import (
        _write_live_fixture, _completion_host_and_validation_scripts, _turn_run_once_completion_argv,
    )
    from loopx.configure_goal import configure_goal

    project, runtime, path = _write_live_fixture(tmp_path)
    configure_goal(registry_path=path, goal_id="loopx-turn-fixture", execute=True, explore_mode=mode)
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(path),
        "--runtime-root", str(runtime), "--format", "json", "turn", "plan",
        "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    packet = json.loads(result.stdout)
    assert "settlement_plan" not in packet["work_contract"]["interaction_contract"]["cli_channel"]
    # Inspection is pure. Execute the original driver-owned path to observe its
    # activation-bound hook and the exact input actually received by the Host.
    assert "turn_start_capability_hook_dispatch" not in packet["work_contract"]
    workspace = project / "host-workspace"
    workspace.mkdir()
    host, validate = _completion_host_and_validation_scripts()
    host = host.replace('request = json.load(sys.stdin)',
        'request = json.load(sys.stdin)\npathlib.Path("received.json").write_text(json.dumps(request))')
    executed = subprocess.run([
        sys.executable, "-m", "loopx.cli",
        *_turn_run_once_completion_argv(workspace, runtime, path, host, validate),
    ], capture_output=True, text=True, timeout=90)
    assert executed.returncode == 0, executed.stdout + executed.stderr
    request = json.loads((workspace / "received.json").read_text())
    source = request["work_contract"]
    assert "settlement_plan" not in source["interaction_contract"]["cli_channel"]
    hooks = source.get("turn_start_capability_hook_dispatch", {}).get("results", [])
    assert any(h.get("capability_id") == "explore" for h in hooks) == (mode != "off")


def _write_attachment_turn_fixture(tmp_path, provider, kind):
    from tests.test_loopx_turn_driver import _write_live_fixture

    extra = {}
    if kind == "terminal":
        extra = {"todo_metadata_extra": "no_followup=true"}
    elif kind == "successor":
        extra = {
            "todo_metadata_extra": "successor_todo_ids=todo_fixture0002",
            "extra_agent_todo_lines": (
                "- [ ] [P2] Continue the public fixture.",
                "  <!-- loopx:todo todo_id=todo_fixture0002 status=open task_class=advancement_task priority=P2 -->",
            ),
        }
    return _write_live_fixture(tmp_path, provider=provider, **extra)


@pytest.mark.parametrize("kind", ["progress", "completion", "terminal", "successor"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode,attachment", [("evidence", "valid"), ("off", "omitted"),
                                           ("off", "valid"), ("evidence", "invalid")])
def test_turn_inline_result_uses_existing_writeback_owner(tmp_path, provider, mode, attachment, kind):
    import hashlib
    import subprocess
    import sys
    from tests.control_plane.test_quota_settlement_cli import _spend_run_count
    from loopx.configure_goal import configure_goal
    from loopx.control_plane.todos.list_readback import list_goal_todos
    from loopx.capabilities.explore.result_log import (
        build_explore_result_projection, load_explore_result_events_strict,
    )
    from tests.test_loopx_turn_driver import (
        _completion_host_and_validation_scripts, _turn_run_once_completion_argv,
    )

    project, runtime, registry = _write_attachment_turn_fixture(tmp_path, provider, kind)
    configure_goal(registry_path=registry, goal_id="loopx-turn-fixture", execute=True, explore_mode=mode)
    workspace = project / "host-workspace"
    workspace.mkdir()
    digest = hashlib.sha256(b"completed").hexdigest()
    vision = {
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "One artifact is validated; method benefit remains unqualified."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
                       "prior_assumption": "Use the original writeback owner.",
                       "observed_reality": "One independently checked artifact was produced.",
                       "retained": ["Original task and acceptance"],
                       "evidence_refs": ["result:inline-execution"]},
    }
    if attachment != "omitted":
        vision["explore_result"] = {
            "schema_version": "explore_result_attachment_v0",
            "question": "Did the declared check produce the validated artifact?",
            "applicability": "This fixture and artifact only.", "input_revision": "fixture-v1",
            "observation": "The original artifact validator accepted the completed file.",
            "interpretation": "Execution correspondence is observed; method benefit is unqualified.",
            "status": "confirmed", "evidence_refs": ["result:inline-execution"],
            "execution_observation": {
                "schema_version": "explore_execution_observation_v0", "candidate_sha256": digest,
                "result_candidate_sha256": digest, "input_revision": "fixture-v1",
                "planned_method_id": "method:artifact-check", "executed_method_id": "method:artifact-check",
                "change_reason": None, "elapsed_seconds": None, "limit_seconds": None,
                "search_iterations": 1, "stop_reason": "artifact validated", "exit_code": 0,
                "evidence_refs": ["result:inline-execution"], "result_ref": "result:inline-execution",
            },
        }
        if attachment == "invalid":
            vision["explore_result"]["input_revision"] = ""
    host, validator = _completion_host_and_validation_scripts()
    if kind == "progress":
        host = host.replace('"result_kind": "validated_completion"', '"result_kind": "validated_progress"')
    host = host.replace('"vision_unchanged_reason": "The active goal may have further work.",',
        '"vision_unchanged_reason": "", "path_delta_mode": "unchanged",\n'
        f'    "agent_vision_json": {json.dumps(vision)!r},\n'
        '    "work_evidence": ["completion-artifact.txt"],\n'
        '    "validation_evidence": ["check:completion-artifact"],')
    argv = _turn_run_once_completion_argv(workspace, runtime, registry, host, validator)
    host_path = workspace / "host.py"
    host_path.write_text(host)
    command = argv.index("--host-adapter-command-json") + 1
    argv[command] = json.dumps([sys.executable, str(host_path)])
    state = project / ".codex/goals/loopx-turn-fixture/ACTIVE_GOAL_STATE.md"
    before_state = state.read_bytes()
    result = subprocess.run([sys.executable, "-m", "loopx.cli", *argv],
                            capture_output=True, text=True, timeout=120)
    packet = json.loads(result.stdout)
    log = explore_result_log_path(runtime, "loopx-turn-fixture")
    todo = list_goal_todos(
        registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture", todo_id="todo_fixture0001",
    )["todo"]
    if attachment == "invalid" or mode == "off" and attachment != "omitted":
        assert result.returncode != 0, packet
        assert isinstance(packet.get("error"), str) and packet["error"]
        assert state.read_bytes() == before_state
        assert todo["status"] == "open" and todo["claimed_by"] == "codex-fixture"
        assert _spend_run_count(runtime, goal_id="loopx-turn-fixture") == 0
        runs = runtime / "goals/loopx-turn-fixture/runs"
        assert not any(json.loads(path.read_text()).get("classification") == "fixture_completion"
                       for path in runs.glob("*.json"))
        assert not log.exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert packet["status"] == "committed" and packet["quota_slot_spend_count"] == 1
    assert todo["status"] == ("open" if kind == "progress" else "done")
    if attachment == "omitted":
        assert not log.exists()
        return
    events = load_explore_result_events_strict(log, goal_id="loopx-turn-fixture")
    findings = build_explore_result_projection(events, goal_id="loopx-turn-fixture")["findings"]
    assert len(findings) == 1
    observation = findings[0]["execution_observation"]
    assert observation["candidate_sha256"] == hashlib.sha256((workspace / "completion-artifact.txt").read_bytes()).hexdigest()
    assert observation["executed_method_id"] == "method:artifact-check"
    before = log.read_bytes()
    replay = subprocess.run([sys.executable, "-m", "loopx.cli", *argv,
                             "--resume-turn-key", packet["resume_turn_key"]],
                            capture_output=True, text=True, timeout=120)
    recovered = json.loads(replay.stdout)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert not any(recovered["effects"].values()) and log.read_bytes() == before


@pytest.mark.parametrize("kind", ["progress", "completion", "terminal", "successor"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("recovery", ["turn", "refresh", "combined_refresh"])
def test_turn_recovers_requested_delivery_from_original_run(tmp_path, monkeypatch, provider, kind, recovery):
    import contextlib
    import io
    import sys
    from loopx.cli import main
    from loopx.configure_goal import configure_goal
    from loopx.control_plane.todos.list_readback import list_goal_todos
    from loopx.capabilities.explore import result_writeback
    from loopx.capabilities.explore.result_log import (
        build_explore_result_projection, load_explore_result_events_strict,
    )
    from loopx.rollout_event_log import rollout_event_log_path
    from tests.control_plane.test_quota_settlement_cli import _spend_run_count
    from tests.test_loopx_turn_driver import (
        _completion_host_and_validation_scripts, _turn_run_once_completion_argv,
    )

    project, runtime, registry = _write_attachment_turn_fixture(tmp_path, provider, kind)
    configure_goal(registry_path=registry, goal_id="loopx-turn-fixture", execute=True, explore_mode="evidence")
    workspace = project / "host-workspace"
    workspace.mkdir()
    vision = {
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "The artifact passes; requested evidence delivery is required."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "continue",
                       "prior_assumption": "The original result can be delivered.",
                       "observed_reality": "The original artifact was checked.", "retained": ["Original work"],
                       "evidence_refs": ["result:original-attachment"]},
        "explore_result": {"schema_version": "explore_result_attachment_v0",
                           "question": "Can this original result recover its requested delivery?",
                           "applicability": "This fixture only.", "input_revision": "fixture-v1",
                           "observation": "The artifact passed its independent validator.",
                           "interpretation": "Recover the original evidence; method benefit is unqualified.",
                           "status": "confirmed", "evidence_refs": ["result:original-attachment"]},
    }
    host, validator = _completion_host_and_validation_scripts()
    if kind == "progress":
        host = host.replace('"result_kind": "validated_completion"', '"result_kind": "validated_progress"')
    host = host.replace('request = json.load(sys.stdin)',
        'request = json.load(sys.stdin)\n'
        'counter = pathlib.Path("host-count.txt")\n'
        'counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))')
    host = host.replace('"vision_unchanged_reason": "The active goal may have further work.",',
        f'"vision_unchanged_reason": "", "path_delta_mode": "unchanged", "agent_vision_json": {json.dumps(vision)!r},\n'
        '    "work_evidence": ["completion-artifact.txt"], "validation_evidence": ["check:artifact"],')
    host_path = workspace / "host.py"
    host_path.write_text(host)
    argv = _turn_run_once_completion_argv(workspace, runtime, registry, host, validator)
    argv[argv.index("--host-adapter-command-json") + 1] = json.dumps([sys.executable, str(host_path)])
    original_delivery = result_writeback.deliver_result_attachment
    available = False
    deliveries = []

    def deliver(**kwargs):
        if not available:
            return {"ok": False, "error": "Fixture delivery is unavailable"}
        result = original_delivery(**kwargs)
        deliveries.append(result)
        return result

    monkeypatch.setattr(result_writeback, "deliver_result_attachment", deliver)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = main(argv)
    failed = json.loads(output.getvalue())
    assert rc != 0 and failed["status"] == "failed", failed
    assert _spend_run_count(runtime, goal_id="loopx-turn-fixture") == 0
    runs = runtime / "goals/loopx-turn-fixture/runs"
    original, = [path for path in runs.glob("*.json")
                 if json.loads(path.read_text()).get("classification") == "fixture_completion"]
    original_bytes = original.read_bytes()
    assert json.loads(original_bytes)["explore_result"]["evidence_refs"] == ["result:original-attachment"]
    assert not explore_result_log_path(runtime, "loopx-turn-fixture").exists()
    # The public quota entrypoint must observe the same pending delivery as
    # the Turn driver, rather than treating its primary Run as settled.
    from tests.control_plane.test_quota_settlement_cli import _run_cli
    original_run = json.loads(original_bytes)
    identity = original_run["settlement_identity"]
    spend_rc, premature = _run_cli(registry, runtime, "quota", "spend-slot",
        "--goal-id", identity["goal_id"], "--agent-id", identity["agent_id"],
        "--todo-id", identity["todo_id"], "--turn-instance-id", identity["turn_instance_id"],
        "--source", "heartbeat", "--slots", "1", "--execute", cwd=workspace)
    assert spend_rc != 0 and not premature.get("appended"), premature
    assert _spend_run_count(runtime, goal_id="loopx-turn-fixture") == 0
    assert original.read_bytes() == original_bytes
    event_log = rollout_event_log_path(runtime, identity["goal_id"])

    def writeback_receipts():
        return [row for line in event_log.read_text().splitlines()
                if (row := json.loads(line))["event_kind"] == "refresh_state"]

    assert writeback_receipts() == []
    refresh_argv = ["--registry", str(registry), "--runtime-root", str(runtime),
                    "--format", "json", "refresh-state",
                    "--goal-id", identity["goal_id"], "--agent-id", identity["agent_id"],
                    "--todo-id", identity["todo_id"], "--turn-instance-id", identity["turn_instance_id"],
                    "--classification", original_run["classification"],
                    "--delivery-outcome", original_run["delivery_outcome"],
                    "--delivery-batch-scale", original_run["delivery_batch_scale"]]
    if recovery == "combined_refresh":
        refresh_argv.append("--settle-turn")
    if recovery != "turn":
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(refresh_argv)
        failed_refresh = json.loads(output.getvalue())
        assert rc != 0 and not failed_refresh["ok"], json.dumps(failed_refresh, indent=2)
        assert failed_refresh.get("refresh_recovery", {}).get("decision") == "repair_receipt", json.dumps(failed_refresh, indent=2)
        assert not failed_refresh.get("receipt_repaired"), failed_refresh
        assert writeback_receipts() == []
        assert _spend_run_count(runtime, goal_id=identity["goal_id"]) == 0
        assert not explore_result_log_path(runtime, identity["goal_id"]).exists()
        assert original.read_bytes() == original_bytes
        assert (workspace / "host-count.txt").read_text() == "1"
    available = True
    if recovery != "turn":
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = main(refresh_argv)
        repaired = json.loads(output.getvalue())
        assert rc == 0 and repaired["ok"] and repaired["receipt_repaired"], json.dumps(repaired, indent=2)
        assert len(writeback_receipts()) == 1
        assert original.read_bytes() == original_bytes
        if recovery == "refresh":
            spend_rc, spent = _run_cli(registry, runtime, "quota", "spend-slot",
                "--goal-id", identity["goal_id"], "--agent-id", identity["agent_id"],
                "--todo-id", identity["todo_id"], "--turn-instance-id", identity["turn_instance_id"],
                "--source", "heartbeat", "--slots", "1", "--execute", cwd=workspace)
            assert spend_rc == 0 and spent["settlement_progress"]["state"] == "settled", spent
        assert _spend_run_count(runtime, goal_id=identity["goal_id"]) == 1
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = main([*argv, "--resume-turn-key", failed["resume_turn_key"], "--retry-failed-turn"])
    recovered = json.loads(output.getvalue())
    assert rc == 0 and recovered["status"] == "committed", json.dumps({"turn": recovered, "deliveries": deliveries}, indent=2)
    assert (workspace / "host-count.txt").read_text() == "1"
    assert original.read_bytes() == original_bytes
    assert _spend_run_count(runtime, goal_id="loopx-turn-fixture") == 1
    todo = list_goal_todos(
        registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="loopx-turn-fixture", todo_id="todo_fixture0001",
    )["todo"]
    assert todo["status"] == ("open" if kind == "progress" else "done")
    log = explore_result_log_path(runtime, "loopx-turn-fixture")
    events = load_explore_result_events_strict(log, goal_id="loopx-turn-fixture")
    findings = build_explore_result_projection(events, goal_id="loopx-turn-fixture")["findings"]
    assert len(findings) == 1 and findings[0]["evidence_refs"] == ["result:original-attachment"]


@pytest.mark.parametrize("mode", ["evidence", "planning"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_ordinary_cli_settlement_without_evidence_does_not_create_graph(tmp_path, monkeypatch, mode, provider):
    from loopx.configure_goal import configure_goal
    from tests.control_plane.test_quota_settlement_cli import (
        _write_fixture, _configure_read_only_todo, _initialize_fixture_authority, _run_cli, _run_generated_cli,
        _spend_run_count, GOAL_ID, AGENT_ID, TODO_ID, TURN_ID,
    )
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, path = _write_fixture(tmp_path)
    _configure_read_only_todo(project)
    configure_goal(registry_path=path, goal_id=GOAL_ID, execute=True, explore_mode=mode)
    _initialize_fixture_authority(project, runtime, provider=provider)
    rc, guard = _run_cli(path, runtime, "quota", "should-run", "--codex-app",
                         "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
                         "--turn-instance-id", TURN_ID, )
    assert rc == 0, guard
    steps = guard["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"]
    assert steps[1]["optional_attachments"][0]["required"] is False
    command = steps[1]["command_template"].replace("<validated_progress>", "validated_change").replace(
        "<scale>", "single_surface").replace("<outcome>", "outcome_progress")
    rc, refreshed = _run_generated_cli(command + " --progress-result-class advanced"
        " --progress-evidence-id validation:ordinary-result --delivery-boundary in_flight_continuation"
        " --no-global-sync --suppress-external-sinks", registry_path=path)
    assert rc == 0, refreshed
    rc, spent = _run_generated_cli(steps[2]["command_template"], registry_path=path)
    assert rc == 0, spent
    assert _spend_run_count(runtime) == 1
    assert not explore_result_log_path(runtime, GOAL_ID).exists()
