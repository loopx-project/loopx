"""A binding inspection must use the actual Turn without launching or spending."""

import json
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from loopx.control_plane.turn_driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import (
    LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
)
from loopx.control_plane.turn_driver.journal_store import turn_journal_path
from test_delegation_cli import cli
from test_local_delegation import service as delegation_service

service = delegation_service


@pytest.mark.parametrize("workspace_state", ["missing", "not_directory"])
def test_real_cli_workspace_fault_is_typed_without_authority_or_launch(
    service, workspace_state
):
    root, runner = service
    config = json.loads(runner.config.read_text())
    unavailable = root / "unavailable-worker"
    if workspace_state == "not_directory":
        unavailable.write_text("not a workspace")
    config["bindings"][0]["workspace"] = str(unavailable)
    runner.config.write_text(json.dumps(config))
    before = runner.registry.read_bytes(), runner.config.read_bytes()

    status, result = cli(runner, "inspect", "--binding-id", "analysis")

    assert status == 0, result
    assert result["state"] == "workspace_unavailable"
    assert result["workspace_state"] == workspace_state
    assert result["workspace_next_action"] == "review_operator_workspace_binding"
    assert result["authority_ready"] is None
    assert result["authority_state"] == "uninspected"
    assert result["authority_next_action"] == "none"
    assert not result["turn_eligible"] and not result["acceptance_ready"]
    assert result["executor"] is None
    assert not any(result["effects"].values())
    assert str(unavailable) not in json.dumps(result)
    assert (runner.registry.read_bytes(), runner.config.read_bytes()) == before
    assert not (root / "host-started").exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))
    assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))
    if workspace_state == "not_directory":
        assert unavailable.is_file()
    else:
        assert not unavailable.exists()


@pytest.mark.parametrize("fault", ["missing", "not_directory", "unavailable"])
def test_workspace_fault_skips_authority_and_preserves_the_original_binding(
    service, monkeypatch, fault
):
    from loopx import collaboration_mcp as delegation

    _, runner = service
    workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    original_stat = Path.stat
    faults = {"missing": FileNotFoundError, "not_directory": NotADirectoryError,
              "unavailable": PermissionError}
    calls = []

    def unavailable(path, *args, **kwargs):
        if path == workspace:
            raise faults[fault]("private filesystem details must not be exported")
        return original_stat(path, *args, **kwargs)

    def no_inspection(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("unavailable cwd must not inspect authority or a Turn")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", unavailable)
        patch.setattr(delegation.delegation_validation, "capture", no_inspection)
        patch.setattr(runner, "_cli", no_inspection)
        result = runner.inspect("analysis")
    assert result["state"] == "workspace_unavailable"
    assert result["workspace_state"] == fault
    assert "private filesystem" not in json.dumps(result)
    assert calls == []
    # Restoring the original filesystem fact re-enters the ordinary preflight;
    # no persisted fault, replacement operation or retargeted work is introduced.
    restored = runner.inspect("analysis")
    assert restored["state"] == "runtime_unverified"
    assert restored["binding"] == result["binding"]
    assert restored["turn_eligible"] and not any(restored["effects"].values())


def test_workspace_fault_cannot_hide_denied_caller_or_changed_binding(service, monkeypatch):
    from loopx.collaboration_mcp import Delegations
    from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError

    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["workspace"] = str(root / "missing-worker")
    runner.config.write_text(json.dumps(config))
    denied = Delegations(runner.root, runner.registry, runner.goal_id, "reviewer", runner.config)
    with pytest.raises(EffectRuntimeRemoteError, match="no delegation grant"):
        denied.inspect("analysis")

    original_binding = runner.binding
    calls = 0

    def changed_binding(binding_id, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            config["bindings"][0]["workspace"] = str(root / "another-missing-worker")
            runner.config.write_text(json.dumps(config))
        return original_binding(binding_id, **kwargs)

    monkeypatch.setattr(runner, "binding", changed_binding)
    with pytest.raises(ValueError, match="source changed"):
        runner.inspect("analysis")


def test_workspace_removed_during_acceptance_is_typed_before_turn_preview(
    service, monkeypatch
):
    from loopx import collaboration_mcp as delegation

    root, runner = service
    workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    original_capture = delegation.delegation_validation.capture

    def capture_then_remove(*args, **kwargs):
        result = original_capture(*args, **kwargs)
        workspace.rename(root / "relocated-worker")
        return result

    def no_turn(*args, **kwargs):
        raise AssertionError("a changed workspace must not reach Turn preview")

    monkeypatch.setattr(delegation.delegation_validation, "capture", capture_then_remove)
    monkeypatch.setattr(runner, "_cli", no_turn)
    result = runner.inspect("analysis")
    assert result["state"] == "workspace_unavailable"
    assert result["workspace_state"] == "missing"
    assert result["authority_ready"] is None
    assert not any(result["effects"].values())
    assert str(workspace) not in json.dumps(result)
    assert not list(runner.path("inventory").parent.glob("*.json"))


def test_workspace_symlink_retargeted_during_acceptance_is_typed(
    service, monkeypatch
):
    from loopx import collaboration_mcp as delegation

    root, runner = service
    original_workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    replacement_workspace = root / "replacement-worker"
    replacement_workspace.mkdir()
    workspace_link = root / "bound-worker-link"
    workspace_link.symlink_to(original_workspace, target_is_directory=True)
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["workspace"] = str(workspace_link)
    runner.config.write_text(json.dumps(config))
    original_capture = delegation.delegation_validation.capture

    def capture_then_retarget(*args, **kwargs):
        result = original_capture(*args, **kwargs)
        workspace_link.unlink()
        workspace_link.symlink_to(replacement_workspace, target_is_directory=True)
        return result

    def no_turn(*args, **kwargs):
        raise AssertionError("a retargeted workspace must not reach Turn preview")

    monkeypatch.setattr(delegation.delegation_validation, "capture", capture_then_retarget)
    monkeypatch.setattr(runner, "_cli", no_turn)
    result = runner.inspect("analysis")
    assert result["state"] == "workspace_unavailable"
    assert result["workspace_state"] == "unavailable"
    assert result["authority_ready"] is None
    assert not result["turn_eligible"] and not any(result["effects"].values())
    assert str(workspace_link) not in json.dumps(result)
    assert str(replacement_workspace) not in json.dumps(result)


@pytest.mark.parametrize("replacement", [False, True])
def test_workspace_changed_during_real_turn_preview_is_not_reported_ready(
    service, monkeypatch, replacement
):
    root, runner = service
    workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    original_cli = runner._cli

    def preview_then_change(*args, **kwargs):
        preview = original_cli(*args, **kwargs)
        workspace.rename(root / "relocated-worker")
        if replacement:
            workspace.mkdir()
        return preview

    monkeypatch.setattr(runner, "_cli", preview_then_change)
    result = runner.inspect("analysis")
    assert result["state"] == "workspace_unavailable"
    assert result["workspace_state"] == ("unavailable" if replacement else "missing")
    assert result["workspace_next_action"] == "review_operator_workspace_binding"
    assert result["authority_ready"] is None
    assert not result["turn_eligible"] and not any(result["effects"].values())
    assert str(workspace) not in json.dumps(result)
    assert not (root / "host-started").exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))


@pytest.mark.parametrize("failure_type", [ValueError, subprocess.TimeoutExpired, OSError])
@pytest.mark.parametrize("workspace_change", ["missing", "replacement", "unchanged"])
def test_preview_failure_rechecks_workspace_and_preserves_unrelated_errors(
    service, monkeypatch, failure_type, workspace_change
):
    root, runner = service
    workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    original_cli = runner._cli
    failure = (
        subprocess.TimeoutExpired(["turn-preview"], 1, stderr="private child details")
        if failure_type is subprocess.TimeoutExpired
        else failure_type("private child details")
    )
    before = runner.registry.read_bytes(), runner.config.read_bytes()

    def preview_then_fail(*args, **kwargs):
        original_cli(*args, **kwargs)
        if workspace_change != "unchanged":
            workspace.rename(root / "relocated-worker")
            if workspace_change == "replacement":
                workspace.mkdir()
        raise failure

    monkeypatch.setattr(runner, "_cli", preview_then_fail)
    if workspace_change == "unchanged":
        with pytest.raises(failure_type) as caught:
            runner.inspect("analysis")
        assert caught.value is failure
    else:
        result = runner.inspect("analysis")
        assert result["state"] == "workspace_unavailable"
        assert result["workspace_state"] == (
            "missing" if workspace_change == "missing" else "unavailable"
        )
        assert result["workspace_next_action"] == "review_operator_workspace_binding"
        assert result["authority_ready"] is None
        assert not result["turn_eligible"] and not any(result["effects"].values())
        assert "private child details" not in json.dumps(result)
        assert str(workspace) not in json.dumps(result)
    assert (runner.registry.read_bytes(), runner.config.read_bytes()) == before
    assert not (root / "host-started").exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))


def test_workspace_removed_during_final_acceptance_recheck_is_typed(
    service, monkeypatch
):
    from loopx import collaboration_mcp as delegation

    root, runner = service
    workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    original_capture = delegation.delegation_validation.capture
    calls = 0

    def capture_then_remove(*args, **kwargs):
        nonlocal calls
        result = original_capture(*args, **kwargs)
        calls += 1
        if calls == 2:
            workspace.rename(root / "relocated-worker")
        return result

    monkeypatch.setattr(delegation.delegation_validation, "capture", capture_then_remove)
    result = runner.inspect("analysis")
    assert calls == 2
    assert result["state"] == "workspace_unavailable"
    assert result["workspace_state"] == "missing"
    assert result["authority_ready"] is None
    assert not any(result["effects"].values())
    assert not (root / "host-started").exists()


def test_mcp_workspace_fault_is_a_read_only_observation(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["workspace"] = str(root / "missing-worker")
    runner.config.write_text(json.dumps(config))
    before = runner.registry.read_bytes(), runner.config.read_bytes()

    async def inspect_mcp():
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "loopx.collaboration_mcp", "--registry", str(runner.registry),
            "--runtime-root", str(runner.root), "--goal-id", runner.goal_id,
            "--agent-id", runner.agent_id, "--workspace", str(root / "lead"),
            "--execution-config", str(runner.config),
        ])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                inspected = await session.call_tool("inspect_execution_binding", {"binding_id": "analysis"})
                assert not inspected.isError
                return json.loads(inspected.content[0].text)

    result = asyncio.run(inspect_mcp())
    assert result["state"] == "workspace_unavailable"
    assert result["authority_ready"] is None and result["executor"] is None
    assert not any(result["effects"].values())
    assert (runner.registry.read_bytes(), runner.config.read_bytes()) == before
    assert not list(runner.path("inventory").parent.glob("*.json"))


@pytest.mark.parametrize("health_repair", [False, True])
def test_actual_workspace_scan_refusal_is_typed_and_effect_free(service, health_repair):
    root, runner = service
    if health_repair:
        registry = json.loads(runner.registry.read_text())
        registry["goals"][0]["control_plane"] = {"self_repair": {
            "enabled": True, "allow_health_blocker_repair": True,
        }}
        runner.registry.write_text(json.dumps(registry))
    workspace = Path(json.loads(runner.config.read_text())["bindings"][0]["workspace"])
    # A synthetic literal exercises the real scanner; never exempt test files.
    (workspace / "unsafe-fixture.py").write_text("fixture = " + repr("tok" + "en=" + "abcdefghijklmnop1234"))
    before = runner.registry.read_bytes()
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    assert result["state"] == "turn_blocked"
    assert result["authority_ready"] and result["acceptance_ready"]
    assert result["turn_eligible"] is False and result["executor"] is None
    refusal = result["turn_blocker"]
    assert refusal["requested_todo_id"] == "todo_analyst-initial"
    assert refusal["state"] == "deferred"
    assert refusal["reason_code"] == ("control_repair" if health_repair else "delivery_not_allowed")
    assert refusal["status_health_ok"] is False and refusal["contract_error_count"] >= 1
    assert not any(result["effects"].values())
    if health_repair:
        async def inspect_mcp():
            params = StdioServerParameters(command=sys.executable, args=[
                "-m", "loopx.collaboration_mcp", "--registry", str(runner.registry),
                "--runtime-root", str(runner.root), "--goal-id", runner.goal_id,
                "--agent-id", runner.agent_id, "--workspace", str(root / "lead"),
                "--execution-config", str(runner.config),
            ])
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    inspected = await session.call_tool("inspect_execution_binding", {"binding_id": "analysis"})
                    assert not inspected.isError
                    return json.loads(inspected.content[0].text)
        mcp_result = asyncio.run(inspect_mcp())
        assert mcp_result["turn_blocker"] == refusal
        assert mcp_result["state"] == "turn_blocked"
        assert not any(mcp_result["effects"].values())
    assert runner.registry.read_bytes() == before
    assert not (root / "host-started").exists()
    assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))
    # Removing this exact synthetic input restores ordinary inspection, rather
    # than leaving a persisted repair route or choosing a different Todo.
    (workspace / "unsafe-fixture.py").unlink()
    status, restored = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0 and restored["turn_eligible"], restored
    assert not any(restored["effects"].values())


def test_real_cli_preflight_preserves_unknown_runtime_and_state(service):
    root, runner = service
    before = runner.registry.read_bytes()
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    assert result["state"] == "runtime_unverified"
    assert result["turn_eligible"] and result["acceptance_ready"]
    assert result["executor"]["host"] == "generic-cli"
    assert result["executor"]["available"] is None
    assert result["executor"]["runtime_probe"] is None
    assert result["executor"]["unavailable_remediation"] == []
    assert not any(result["effects"].values())
    assert runner.registry.read_bytes() == before
    assert not (root / "host-started").exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))
    assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))


def test_real_cli_dsh_preflight_preserves_interpreter_probe_without_launch(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = ["--host", "dsh"]
    runner.config.write_text(json.dumps(config))
    before = runner.registry.read_bytes()
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    executor = result["executor"]
    probe = executor["runtime_probe"]
    assert probe["schema_version"] == "managed_runtime_probe_v0"
    assert probe["scope"] == "probing_interpreter"
    assert probe["module"] == "deepseek_harness"
    assert isinstance(probe["available"], bool)
    if not probe["available"]:
        assert executor["available"] is False
        assert executor["reason"] == "dsh_runtime_unavailable"
        assert executor["unavailable_remediation"] == ["configure_dsh_runtime", "select_individual_host"]
    assert "credential_env" not in executor and "endpoint_env" not in executor
    assert not any(result["effects"].values())
    assert runner.registry.read_bytes() == before
    assert not (root / "host-started").exists()
    assert not list(runner.path("inventory").parent.glob("*.json"))
    assert not list((root / "runtime" / "goals").glob("*/turns/*.json"))


def test_preflight_rejects_execution_and_retargeting_before_subprocess(
    service, monkeypatch
):
    _, runner = service
    config = json.loads(runner.config.read_text())
    original = list(config["bindings"][0]["host_args"])
    calls = []
    monkeypatch.setattr(runner, "_cli", lambda *args: calls.append(args))
    for flags in (
        ["--execute"],
        ["--exec"],
        ["--todo-id", "other"],
        ["--agent-id", "other"],
        ["--project", "other"],
        ["--scan-root", "other"],
        ["--resume-turn-key", "other"],
    ):
        config["bindings"][0]["host_args"] = [*original, *flags]
        runner.config.write_text(json.dumps(config))
        with pytest.raises(ValueError):
            runner.inspect("analysis")
    assert calls == []


def test_preflight_scope_and_incompatible_cli_arguments(service):
    _, runner = service
    for arguments in [
        (),
        ("--binding-id", "analysis", "--execute"),
        ("--binding-id", "analysis", "--operation-id", "wrong"),
        ("--binding-id", "analysis", "--limit", "2"),
    ]:
        status, result = cli(runner, "inspect", *arguments)
        assert status == 1 and not result["ok"]
    status, result = cli(
        runner, "inspect", "--binding-id", "analysis", actor="reviewer"
    )
    assert status == 1 and not result["ok"]


def test_preflight_surfaces_actual_turn_rejection_without_launch(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = [
        "--host", "generic-cli", "--host-command-json", "[]",
    ]
    runner.config.write_text(json.dumps(config))
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 1
    assert "delegation Turn preflight unavailable" in result["error"]
    assert not (root / "host-started").exists()


def test_real_runtime_decodes_only_string_selection_states():
    """The live managed runtime rejects a JSON-array refusal enum instead of echoing it."""
    from loopx.control_plane.effect_runtime import (
        EffectRuntimeRemoteError,
        effect_runtime_result,
    )

    binding = {"id": "review", "agent_id": "reviewer", "todo_id": "todo_review"}
    effects = {"host_invoked": False, "state_written": False,
               "quota_spent": False, "scheduler_acknowledged": False}

    def params(state):
        return {
            "binding": binding,
            "authority": {"ready": True, "reason": None},
            "preview": {
                "ok": False, "effects_scope": "current_invocation", "effects": effects,
                "error_code": "turn_todo_selection_deferred",
                "selection_rejection": {
                    "schema_version": "loopx_turn_selection_rejection_v0",
                    "source": "quota.should-run", "requested_todo_id": binding["todo_id"],
                    "state": state, "reason_code": "control_repair",
                    "delivery_preemptions": ["control_repair"],
                    "recovery_action": "reenter_guard_without_selection",
                    "status_health_ok": False, "contract_error_count": 2,
                },
            },
            "acceptance": None, "validation_files_current": False,
        }

    accepted = effect_runtime_result("collaboration.delegation.preflight", params("deferred"))
    assert accepted["state"] == "turn_blocked"
    assert accepted["turn_blocker"]["state"] == "deferred"
    assert accepted["turn_eligible"] is False and accepted["executor"] is None
    assert not any(accepted["effects"].values())
    for raw in (["deferred"], ["rejected"], ["unavailable"]):
        with pytest.raises(EffectRuntimeRemoteError):
            effect_runtime_result("collaboration.delegation.preflight", params(raw))


def test_preflight_projects_unavailable_authority_without_turn_or_provider(
    service, monkeypatch
):
    from loopx import collaboration_mcp as delegation

    root, runner = service
    calls = []

    def unavailable(**_kwargs):
        raise ValueError(
            "Goal acceptance requires an existing canonical authority; "
            "activation never promotes a provider"
        )

    monkeypatch.setattr(delegation.delegation_validation, "inspect_goal_acceptance", unavailable)
    monkeypatch.setattr(runner, "_cli", lambda *args, **kwargs: calls.append(args))
    result = runner.inspect("analysis")
    assert result["state"] == "authority_unavailable"
    assert result["authority_ready"] is False
    assert result["authority_state"] == "unavailable"
    assert result["authority_next_action"] == "repair_canonical_authority"
    assert result["promotion_from_surface_allowed"] is False
    assert "canonical authority" in result["authority_reason"]
    assert not any(result["effects"].values())
    assert calls == []
    assert not (root / "host-started").exists()


def test_dispatch_preserves_run_once_rejection_before_host_launch(
    service, monkeypatch
):
    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "rejected-plan", {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Exercise a rejected Turn plan",
        "context": "The provider must remain unstarted.",
        "constraints": ["No external actions"],
        "inputs": [],
        "acceptance": ["Preserve the canonical rejection"],
        "return_requirement": "Return no model result",
    })
    calls = []

    def rejected_turn(_binding, *args, **_kwargs):
        calls.append(args)
        return {
            "ok": False,
            "schema_version": "loopx_turn_execution_v0",
            "mode": "run_once",
            "error": "Requested Turn Todo is not accepted by canonical authority",
            "effects": {"host_invoked": False, "state_written": False,
                        "scheduler_acknowledged": False, "quota_spent": False},
        }

    monkeypatch.setattr(runner, "_cli", rejected_turn)
    runner.execute("rejected-plan")
    result = runner.read("rejected-plan")
    assert result["status"] == "rejected"
    assert result["error"] == (
        "Requested Turn Todo is not accepted by canonical authority"
    )
    assert len(calls) == 1 and calls[0][:2] == ("turn", "run-once")
    assert "--execute" in calls[0]
    assert "turn_key" not in result
    assert not (root / "host-started").exists()


def test_hard_lease_delegation_claims_before_host_launch(service, monkeypatch):
    from loopx import collaboration_mcp as delegation

    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    monkeypatch.setattr(
        delegation,
        "show_goal_handoff_mode",
        lambda **_kwargs: {"handoff_mode": "hard_lease"},
    )
    runner.start("analysis", "leased-dispatch", brief={
        "schema_version": "collaboration_brief_v0",
        "purpose": "Exercise an atomic hard-lease dispatch",
        "context": "The lease must precede the managed host.",
        "constraints": ["No external actions"],
        "inputs": [],
        "acceptance": ["Preserve canonical lease identity"],
        "return_requirement": "Return no model result",
    })
    calls = []

    def canonical(_binding, *args, **_kwargs):
        calls.append(args)
        if args[:2] == ("todo", "claim"):
            key = args[args.index("--task-lease-idempotency-key") + 1]
            return {
                "ok": True,
                "lease": {
                    "owner": "analyst",
                    "idempotency_key": key,
                    "status": "active",
                    "version": 7,
                },
            }
        return {
            "ok": False,
            "schema_version": "loopx_turn_execution_v0",
            "mode": "run_once",
            "error": "stop after lease evidence",
            "effects": {
                "host_invoked": False,
                "state_written": False,
                "scheduler_acknowledged": False,
                "quota_spent": False,
            },
        }

    monkeypatch.setattr(runner, "_cli", canonical)
    runner.execute("leased-dispatch")
    result = runner.read("leased-dispatch")
    row = json.loads(runner.path("leased-dispatch").read_text())

    assert [call[:2] for call in calls] == [("todo", "claim"), ("turn", "run-once")]
    assert row["task_lease"]["required"] is True
    assert row["task_lease"]["lease"]["idempotency_key"] == row["turn_instance_id"]
    assert row["task_lease"]["lease"]["version"] == 7
    # The canonical contract permits an older lease without an acquisition
    # TTL. Let the shared TS lease owner resolve its default rather than
    # inventing a Python default or rejecting this still-current execution.
    assert runner._delegated_lease_context(row, runner.binding("analysis"))["ttl_seconds"] is None
    assert result["status"] == "rejected"
    assert result["error"] == "stop after lease evidence"


def test_exact_validated_turn_can_reopen_a_false_terminal_observation(
    service, monkeypatch
):
    _, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "recover-settlement", {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Recover one validated settlement",
        "context": "The model result and independent validation already exist.",
        "constraints": ["Never rerun model work"],
        "inputs": [],
        "acceptance": ["Resume only the exact Turn"],
        "return_requirement": "Return the validated artifact",
    })
    path = runner.path("recover-settlement")
    row = json.loads(path.read_text())
    turn_instance_id = "delegation-" + row["identity"]["request_id"][:32]
    row.update(
        status="rejected",
        turn_instance_id=turn_instance_id,
        error="legacy false terminal observation",
    )
    path.write_text(json.dumps(row))
    plan = build_loopx_turn_plan(
        {
            "ok": True,
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": runner.goal_id,
            "agent_id": "analyst",
            "should_run": True,
            "effective_action": "normal_run",
            "action": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "selected_todo": {"todo_id": "todo_analyst-initial"},
            },
            "user": {"action_required": False, "open_count": 0},
            "writeback": {"spend_after_validation": True},
            "scheduler": {"action": "run_now"},
            "action_signature": {
                "matches": True,
                "source_hash": "sha256:fixture",
                "envelope_hash": "sha256:fixture",
            },
            "compaction": {"within_budget": True},
        },
        host="generic-cli",
        execution_mode="isolated-headless",
        turn_instance_id=turn_instance_id,
        iteration_context_policy="fresh",
    )
    transaction = plan["transaction"]
    turn_key = transaction["turn_key"]
    journal_path = turn_journal_path(
        runner.root, goal_id=runner.goal_id, turn_key=turn_key
    )
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    host_result = {
        "schema_version": "loopx_turn_result_v0",
        "turn_key": turn_key,
        "result_kind": "validated_progress",
        "completed_phases": ["host_execute", "typed_result"],
    }
    journal_path.write_text(json.dumps({
        "schema_version": LOOPX_TURN_JOURNAL_SCHEMA_VERSION,
        "goal_id": runner.goal_id,
        "turn_key": turn_key,
        "status": "in_progress",
        "result_kind": "validated_progress",
        "completed_phases": ["host_execute", "typed_result", "validation"],
        "plan": plan,
        "host_result": host_result,
        "task_validation": {"ok": True, "status": "passed"},
    }))

    binding = runner.binding("analysis", require_active=True)
    assert runner._recover_validated_settlement(path, row, binding) is True
    recovered = json.loads(path.read_text())
    assert recovered["status"] == "turn_returned"
    assert recovered["turn_key"] == turn_key
    assert recovered["turn_result"]["resume_turn_key"] == turn_key


def test_selected_dsh_profile_is_not_replaced_by_the_default(service):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = [
        "--host",
        "dsh",
        "--dsh-provider",
        "openai",
        "--dsh-model",
        "synthetic-model",
        "--dsh-reasoning-effort",
        "high",
    ]
    runner.config.write_text(json.dumps(config))
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    assert result["executor"]["host"] == "dsh"
    assert "synthetic-model" in result["executor"]["profile"]
    assert "high" in result["executor"]["profile"]
    assert result["state"] in {"launchable", "runtime_unavailable"}
    assert not any(result["effects"].values())
    assert not (root / "host-started").exists()


@pytest.mark.parametrize("operation_tools", [False, True])
def test_selected_codex_managed_agent_profile_is_projected_exactly(service, operation_tools):
    root, runner = service
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["host_args"] = [
        "--host",
        "codex-cli",
        "--codex-model",
        "gpt-5.6-sol",
        "--codex-reasoning-effort",
        "xhigh",
    ]
    if operation_tools:
        config["bindings"][0]["host_args"].append("--codex-operation-tools")
    runner.config.write_text(json.dumps(config))

    status, result = cli(runner, "inspect", "--binding-id", "analysis")

    assert status == 0, result
    expected = {
        "host": "codex-cli",
        "available": None,
        "reason": None,
        "profile": "gpt-5.6-sol@xhigh",
        "runtime_probe": None,
        "unavailable_remediation": [],
    }
    if operation_tools:
        from loopx.control_plane.turn_driver.host_binding import managed_executor_binding_from_host_args
        expected["operation_transport"] = managed_executor_binding_from_host_args(
            config["bindings"][0]["host_args"]
        )["operation_transport"]
    assert result["executor"] == expected
    assert result["state"] == "runtime_unverified"
    assert not any(result["effects"].values())
    assert not (root / "host-started").exists()

    binding = runner.binding("analysis", require_active=True)
    execution = runner._execution_arguments(binding, "native-tool-inspection")
    assert ("--codex-operation-tools" in execution) is operation_tools
    encoded = execution[execution.index("--codex-mcp-server-json") + 1]
    native = json.loads(encoded)
    assert native["schema_version"] == "codex_stdio_mcp_server_v0"
    assert native["name"] == "loopx_delegation"
    command = native["command"]
    assert command[:3] == [sys.executable, "-P", "-c"]
    assert "loopx.collaboration_mcp" in command
    assert str(Path(__file__).resolve().parents[1]) in command
    assert command[command.index("--agent-id") + 1] == "analyst"
    assert command[command.index("--workspace") + 1] == binding["workspace"]
    assert command[command.index("--execution-config") + 1] == str(runner.config)
    assert "lead" not in command

    validator = json.loads(execution[execution.index("--validation-command-json") + 1])
    assert validator[:3] == [sys.executable, "-P", "-c"]
    assert "loopx.collaboration_mcp" in validator
    assert str(Path(__file__).resolve().parents[1]) in validator

    shadow = Path(binding["workspace"]) / "loopx"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "collaboration_mcp.py").write_text(
        "raise RuntimeError('stale workspace MCP must not be imported')\n",
        encoding="utf-8",
    )
    clean_environment = os.environ.copy()
    clean_environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        command,
        cwd=binding["workspace"],
        input="",
        text=True,
        capture_output=True,
        env=clean_environment,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert "stale workspace MCP" not in completed.stderr


def test_preflight_ignores_stale_loopx_checkout_in_worker_workspace(service):
    root, runner = service
    workspace = Path(runner.binding("analysis", require_active=True)["workspace"])
    shadow = workspace / "loopx"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "cli.py").write_text(
        "raise RuntimeError('stale workspace LoopX must not be imported')\n",
        encoding="utf-8",
    )

    status, result = cli(runner, "inspect", "--binding-id", "analysis")

    assert status == 0, result
    assert result["turn_eligible"] is True
    assert not any(result["effects"].values())


def test_preflight_does_not_call_an_invalidated_acceptance_ready(service):
    root, runner = service
    from loopx.agent_registry import load_goal_from_registry
    from pathlib import Path

    workspace = Path(load_goal_from_registry(runner.registry, runner.goal_id)["repo"])
    pin = workspace / "validation" / "acceptance.py"
    pin.write_text(pin.read_text() + "\n# revised validator\n")
    status, result = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0, result
    assert not result["acceptance_ready"]
    assert result["state"] in {"turn_blocked", "acceptance_unavailable"}


@pytest.mark.parametrize("validation_basis", ["goal_acceptance", "independent", "missing_workspace", "independent_missing", "validation_files_unavailable"])
def test_http_team_readback_uses_original_scope_without_a_new_turn(service, validation_basis):
    import http.client
    import threading
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    root, runner = service
    if validation_basis in {"independent", "independent_missing"}:
        from test_independent_delegation_validation import independent_binding

        independent_binding(service, declared=validation_basis == "independent")
    elif validation_basis == "missing_workspace":
        binding_config = json.loads(runner.config.read_text())
        binding_config["bindings"][0]["workspace"] = str(root / "missing-worker")
        runner.config.write_text(json.dumps(binding_config))
    from loopx.agent_registry import load_goal_from_registry
    from pathlib import Path

    workspace = Path(load_goal_from_registry(runner.registry, runner.goal_id)["repo"])
    if validation_basis == "validation_files_unavailable":
        pin = workspace / "validation" / "acceptance.py"
        pin.write_text(pin.read_text() + "\n# revised validator\n")
    config = workspace / ".loopx" / "config" / "delegations.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_bytes(runner.config.read_bytes())
    registry_payload = json.loads(runner.registry.read_text())
    goal = next(
        item for item in registry_payload["goals"] if item["id"] == runner.goal_id
    )
    goal.setdefault("spawn_policy", {})["execution_config"] = (
        ".loopx/config/delegations.json"
    )
    runner.registry.write_text(json.dumps(registry_payload))
    store = ChatSessionStore(root / "runtime")
    controller = ChatRuntimeController(
        store=store, codex_bin="codex", registry_path=runner.registry
    )
    session = store.create_session(
        goal_id=runner.goal_id,
        agent_id="codex",
        channel_id="goal." + runner.goal_id,
        upstream_thread_id="fixture",
        upstream_mode="chat",
        adapter_kind="codex_app_server",
    )
    # Existing settings owner, with no native Goal or new executor.
    controller.loopx_mode.apply(
        session["session_id"],
        {
            "operation": "configure",
            "settings": {
                "agent_id": "lead",
                "token_budget": 1000,
            },
        },
        work_dir=root,
        objective="Inspect existing work",
    )
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.registry_path, server.chat_store, server.runtime_controller = (
        runner.registry,
        store,
        controller,
    )
    server.runtime_root_override, server.scan_roots, server.limit = (
        str(runner.root),
        [],
        20,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for body, expected in [
            ({"operation": "inspect", "binding_id": "analysis"}, 200),
            ({"operation": "operations"}, 200),
            ({"operation": "operations", "agent_id": "other"}, 409),
        ]:
            conn = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=45
            )
            conn.request(
                "POST",
                f"/api/chat/sessions/{session['session_id']}/loopx",
                json.dumps(body),
                {
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                },
            )
            response = conn.getresponse()
            result = json.loads(response.read())
            conn.close()
            assert response.status == expected, result
            if body["operation"] == "inspect":
                expected_state = {
                    "missing_workspace": "workspace_unavailable",
                    "independent_missing": "acceptance_unavailable",
                    "validation_files_unavailable": "acceptance_unavailable",
                }.get(validation_basis, "runtime_unverified")
                if validation_basis == "validation_files_unavailable":
                    assert result["state"] in {expected_state, "turn_blocked"}
                else:
                    assert result["state"] == expected_state
                assert not any(result["effects"].values())
                if validation_basis in {"independent_missing", "validation_files_unavailable"}:
                    assert result["acceptance_reason_code"] == (
                        "independent_delegation_validation_required" if validation_basis == "independent_missing"
                        else "validation_files_unavailable"
                    )
                    assert result["acceptance_next_action"] == (
                        "review_original_todo_validation" if validation_basis == "independent_missing"
                        else "restore_original_validation_files"
                    )
                    assert result["acceptance_ready"] is False
                    assert str(workspace) not in json.dumps(result)
                if validation_basis == "missing_workspace":
                    assert result["authority_ready"] is None
                    assert result["workspace_next_action"] == "review_operator_workspace_binding"
                    assert str(root / "missing-worker") not in json.dumps(result)
        assert store.load_session(session["session_id"]).get("active_turn_id") is None
        assert not (root / "host-started").exists()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        controller.close()
