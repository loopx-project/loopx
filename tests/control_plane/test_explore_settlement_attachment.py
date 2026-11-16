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
@pytest.mark.parametrize("context_delivery", ["off", "on"])
def test_real_cli_settlement_exposes_optional_attachment(tmp_path, monkeypatch, mode, profile, provider, context_delivery):
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
    command = ["quota", "should-run", *profile_args, *binding,
               "--context-delivery", context_delivery]
    if context_delivery == "on":
        (tmp_path / "captures").mkdir(mode=0o700)
        command.extend(("--defer-context-presentation", "--decision-output-root", str(tmp_path / "captures")))
    rc, guard = _run_cli(path, runtime, *command, cwd=project)
    if context_delivery == "on" and profile == "generic_cli":
        assert rc != 0 and guard["ok"] is False
        assert "supported declared run mode" in guard["reason"]
        assert not explore_result_log_path(runtime, GOAL_ID).exists()
        return  # Unsupported native Hosts must not enter the deferred capture path.
    assert rc == 0, json.dumps(guard, ensure_ascii=False)
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["schema_version"] == "quota_settlement_plan_v1"
    writeback = plan["ordered_steps"][1]
    assert writeback["optional_attachments"][0]["required"] is False
    assert "--explore-result-json" not in writeback["command_template"]
    rc, compact = _run_cli(path, runtime, *command, "--turn-envelope", cwd=project)
    assert rc == 0, compact
    if context_delivery == "off":
        assert compact["schema_version"] == "loopx_turn_envelope_v0"
        assert compact["writeback"]["settlement_plan"] == plan
    else:
        assert compact["schema_version"] == "loopx_turn_envelope_v1"
        assert compact["interaction_contract"]["cli_channel"]["settlement_plan"] == plan
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
    assert packet["schema_version"] == "loopx_turn_plan_v1"
    assert "turn_envelope" not in packet
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
