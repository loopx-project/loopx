from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.codex_execution_preparation import (
    ExecutionPreparationClaimFailed,
    builtin_execution_preparation_claim,
)


GOAL = "goal-claim-preparation"
TODO = "todo_claim_preparation"
AGENT = "agent-a"


def _decision(*, state: str = "claim_required", host_execution_allowed: bool = True,
              cli_args: list[str] | None = None, should_run: bool = True,
              todo_id: str = TODO, normal_delivery_allowed: bool | None = None,
              decision_agent_id: str = AGENT) -> dict[str, object]:
    advertised = ["todo", "claim", "--goal-id", GOAL, "--todo-id", TODO,
                  "--agent-id", AGENT, "--claimed-by", AGENT]
    return {
        "ok": True,
        "schema_version": "quota_should_run_decision_v0",
        "goal_id": GOAL,
        "agent_id": decision_agent_id,
        "should_run": should_run,
        "normal_delivery_allowed": (
            state == "ready" if normal_delivery_allowed is None
            else normal_delivery_allowed
        ),
        "selected_todo": {"todo_id": TODO},
        "execution_preparation": {
            "schema_version": "execution_preparation_v0",
            "todo_id": todo_id,
            "agent_id": AGENT,
            "handoff_mode": "hard_lease",
            "state": state,
            "delivery_allowed": state == "ready",
            "reason_code": "task_lease_missing",
            "claim": {
                "command": "loopx todo claim",
                "command_line": "loopx todo claim --goal-id " + GOAL + " --thin",
                "cli_args": advertised if cli_args is None else cli_args,
                "host_execution_allowed": host_execution_allowed,
                "postcondition": (
                    "rerun quota should-run and require "
                    "execution_preparation.state=ready"
                ),
            },
        },
    }


class _Completed:
    returncode = 0

    def __init__(self, payload: dict[str, object]) -> None:
        self.stdout = json.dumps(payload, ensure_ascii=False)
        self.stderr = ""


def test_the_advertised_claim_runs_once_before_delivery(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    calls: list[tuple[list[str], Path]] = []

    def run(command, *, cwd, timeout):
        calls.append((list(command), cwd))
        return _Completed({"ok": True})

    prepared = builtin_execution_preparation_claim(
        _decision(), registry_path=registry, runtime_root=runtime_root,
        cwd=project, agent_id=AGENT, runner=run,
    )

    assert prepared == TODO
    assert len(calls) == 1
    command, cwd = calls[0]
    assert cwd == project
    assert command[-16:] == [
        "--registry", str(registry), "--runtime-root", str(runtime_root),
        "--format", "json", "todo", "claim", "--goal-id", GOAL,
        "--todo-id", TODO, "--agent-id", AGENT, "--claimed-by", AGENT,
    ]


def test_powershell_launcher_is_invoked_through_pwsh(
    tmp_path: Path, monkeypatch,
) -> None:
    from loopx.command_invocation import shutil as invocation_shutil
    from loopx.control_plane.turn_driver import codex_execution_preparation

    launcher = tmp_path / "loopx.ps1"
    monkeypatch.setattr(
        codex_execution_preparation,
        "current_loopx_cli_argv",
        lambda: [str(launcher), "--cli-bin", str(launcher)],
    )
    monkeypatch.setattr(
        invocation_shutil,
        "which",
        lambda command: "pwsh" if command == "pwsh" else None,
    )
    calls = []

    def run(command, *, cwd, timeout):
        calls.append(list(command))
        return _Completed({"ok": True})

    assert builtin_execution_preparation_claim(
        _decision(), agent_id=AGENT,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime", cwd=tmp_path, runner=run,
    ) == TODO
    assert calls == [[
        "pwsh", "-NoLogo", "-NoProfile", "-File", str(launcher),
        "--cli-bin", str(launcher), "--registry", str(tmp_path / "registry.json"),
        "--runtime-root", str(tmp_path / "runtime"), "--format", "json",
        "todo", "claim", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", AGENT, "--claimed-by", AGENT,
    ]]


@pytest.mark.parametrize(
    "state, host_execution_allowed",
    [("ready", True), ("claim_required", False), ("selection_required", True)],
)
def test_preparation_this_adapter_cannot_own_stays_a_typed_wait(
    tmp_path: Path, state: str, host_execution_allowed: bool,
) -> None:
    def run(command, *, cwd, timeout):  # pragma: no cover - must not run
        raise AssertionError(f"unexpected claim: {command}")

    assert builtin_execution_preparation_claim(
        _decision(state=state, host_execution_allowed=host_execution_allowed),
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        cwd=tmp_path, agent_id=AGENT, runner=run,
    ) is None


@pytest.mark.parametrize(
    "cli_args, should_run, todo_id",
    [
        (["todo", "claim", "--goal-id", GOAL, "--todo-id", TODO,
          "--agent-id", "agent-b", "--claimed-by", "agent-b"], True, TODO),
        (["todo", "claim"], True, TODO),
        (None, False, TODO),
        (None, True, "todo_other"),
    ],
)
def test_only_the_exact_advertised_claim_is_executed(
    tmp_path: Path, cli_args: list[str] | None, should_run: bool, todo_id: str,
) -> None:
    def run(command, *, cwd, timeout):  # pragma: no cover - must not run
        raise AssertionError(f"unexpected claim: {command}")

    assert builtin_execution_preparation_claim(
        _decision(cli_args=cli_args, should_run=should_run, todo_id=todo_id),
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        cwd=tmp_path, agent_id=AGENT, runner=run,
    ) is None


@pytest.mark.parametrize(
    "normal_delivery_allowed, decision_agent_id",
    [(True, AGENT), (False, "agent-b")],
)
def test_inconsistent_decision_identity_is_not_claimed(
    tmp_path: Path, normal_delivery_allowed: bool, decision_agent_id: str,
) -> None:
    def run(command, *, cwd, timeout):  # pragma: no cover - must not run
        raise AssertionError(f"unexpected claim: {command}")

    assert builtin_execution_preparation_claim(
        _decision(
            normal_delivery_allowed=normal_delivery_allowed,
            decision_agent_id=decision_agent_id,
        ),
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        cwd=tmp_path, agent_id=AGENT, runner=run,
    ) is None


def test_a_timed_out_claim_surfaces_as_preparation_failure(tmp_path: Path) -> None:
    def run(command, *, cwd, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    with pytest.raises(ExecutionPreparationClaimFailed) as refused:
        builtin_execution_preparation_claim(
                _decision(), registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime", cwd=tmp_path, agent_id=AGENT, runner=run,
        )

    assert refused.value.code == "turn_execution_preparation_claim_failed"
    assert TODO in str(refused.value)


def test_a_refused_claim_surfaces_the_atomic_authority_reason(tmp_path: Path) -> None:
    def run(command, *, cwd, timeout):
        return _Completed({
            "ok": False,
            "reason": "Todo claimed_by does not grant this actor update authority",
            "reason_code": "update_owner_mismatch",
        })

    with pytest.raises(ExecutionPreparationClaimFailed) as refused:
        builtin_execution_preparation_claim(
                _decision(), registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime", cwd=tmp_path, agent_id=AGENT, runner=run,
        )

    assert refused.value.code == "turn_execution_preparation_claim_failed"
    assert "Todo claimed_by does not grant this actor update authority" in str(refused.value)
    assert TODO in str(refused.value)


def _identity_decision(**changes: object) -> dict[str, object]:
    decision = _decision()
    preparation = decision["execution_preparation"]
    selected = decision["selected_todo"]
    assert isinstance(preparation, dict) and isinstance(selected, dict)
    decision["goal_id"] = changes.get("goal_id", GOAL)
    decision["agent_id"] = changes.get("decision_agent_id", AGENT)
    preparation["agent_id"] = changes.get("preparation_agent_id", AGENT)
    preparation["todo_id"] = changes.get("todo_id", TODO)
    selected["todo_id"] = changes.get("selected_todo_id", TODO)
    if "selected_agent_id" in changes:
        selected["agent_id"] = changes["selected_agent_id"]
    if "identity_agent_id" in changes:
        decision["agent_identity"] = {"agent_id": changes["identity_agent_id"]}
    return decision


@pytest.mark.parametrize(
    "changes",
    [
        {"goal_id": f" {GOAL}"},
        {"preparation_agent_id": f" {AGENT}"},
        {"todo_id": f" {TODO}"},
        {"selected_todo_id": f"{TODO} "},
        {"decision_agent_id": f" {AGENT}"},
        {"selected_agent_id": f"{AGENT} "},
        {"identity_agent_id": "agent-b"},
        {"decision_agent_id": "agent-b"},
        {"goal_id": 7},
        {"todo_id": None},
    ],
)
def test_an_identity_that_would_change_the_claim_is_not_normalized(
    tmp_path: Path, changes: dict[str, object],
) -> None:
    def run(command, *, cwd, timeout):  # pragma: no cover - must not run
        raise AssertionError(f"unexpected claim: {command}")

    assert builtin_execution_preparation_claim(
        _identity_decision(**changes), agent_id=AGENT,
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        cwd=tmp_path, runner=run,
    ) is None


def test_a_call_for_another_agent_does_not_claim(tmp_path: Path) -> None:
    def run(command, *, cwd, timeout):  # pragma: no cover - must not run
        raise AssertionError(f"unexpected claim: {command}")

    assert builtin_execution_preparation_claim(
        _decision(), agent_id="agent-b",
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        cwd=tmp_path, runner=run,
    ) is None


def test_matching_identity_contracts_still_run_the_exact_claim(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(command, *, cwd, timeout):
        calls.append(list(command))
        return _Completed({"ok": True})

    assert builtin_execution_preparation_claim(
        _identity_decision(selected_agent_id=AGENT, identity_agent_id=AGENT),
        agent_id=AGENT, registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime", cwd=tmp_path, runner=run,
    ) == TODO
    assert calls[0][-10:] == [
        "todo", "claim", "--goal-id", GOAL, "--todo-id", TODO,
        "--agent-id", AGENT, "--claimed-by", AGENT,
    ]


def test_a_legacy_unstarted_cadence_reservation_recovers_without_a_second_start(tmp_path: Path) -> None:
    from loopx.cli_commands.turn_cadence import managed_cadence_start
    from loopx.control_plane.effect_runtime import effect_runtime_result

    runtime = tmp_path / "cadence-runtime"
    effect_runtime_result("quota.automation_cadence.manage", {
        "runtime_root": str(runtime), "goal_id": GOAL, "agent_id": AGENT,
        "operation": "configure", "min_interval_minutes": 1440, "expected_revision": 0,
        "owner_reference": "fixture-owner", "execute": True,
    })
    old = managed_cadence_start(runtime_root=runtime, goal_id=GOAL, agent_id=AGENT,
                               automation_id=None, manual_reason=None)
    attempt = {"turn_key": "sha256:" + "a" * 64, "attempt": 1}
    first = old.admit(attempt)
    assert first["admitted"] is True and first["reserved"] is True
    migrated = managed_cadence_start(runtime_root=runtime, goal_id=GOAL, agent_id=AGENT,
        automation_id=None, manual_reason=None, request_id=f"{GOAL}:{AGENT}:{TODO}:original-turn")
    recovered = migrated.admit(attempt)
    assert recovered["admitted"] is True and recovered["resumed"] is True
    migrated.confirm()
    duplicate = migrated.admit(attempt)
    assert duplicate["admitted"] is False and duplicate["reason"] == "duplicate_or_stale_trigger"


def test_unconsumed_preparation_refusal_names_the_advertised_claim() -> None:
    from loopx.control_plane.turn_driver.codex_execution_preparation import (
        unconsumed_preparation_refusal,
    )

    refusal = unconsumed_preparation_refusal(_decision())
    assert refusal is not None
    assert refusal["reason_code"] == "turn_execution_preparation_claim_not_consumed"
    assert refusal["todo_id"] == TODO
    assert refusal["claim_command"].startswith("loopx todo claim")


def test_unconsumed_preparation_refusal_leaves_other_states_alone() -> None:
    from loopx.control_plane.turn_driver.codex_execution_preparation import (
        unconsumed_preparation_refusal,
    )

    assert unconsumed_preparation_refusal(_decision(state="ready")) is None
    assert unconsumed_preparation_refusal({"ok": True}) is None
    without_command = _decision()
    without_command["execution_preparation"]["claim"] = {"host_execution_allowed": False}
    refusal = unconsumed_preparation_refusal(without_command)
    assert refusal is not None and "claim_command" not in refusal


def test_run_once_refuses_typed_when_the_builtin_claim_is_not_consumed(
    tmp_path: Path, monkeypatch,
) -> None:
    import contextlib
    import io

    from loopx.cli import main
    from loopx.control_plane.turn_driver import codex_execution_preparation
    from test_loopx_turn_codex_cli import _fake_codex
    from test_loopx_turn_driver import _write_live_fixture

    project, runtime, registry = _write_live_fixture(tmp_path, handoff_mode="hard_lease")
    native, marker = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(marker))
    declined: list[int] = []
    monkeypatch.setattr(
        codex_execution_preparation,
        "builtin_execution_preparation_claim",
        lambda *args, **kwargs: declined.append(1) or None,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            "--host", "codex-cli", "--project", str(project), "--codex-bin", str(native),
            "--codex-sandbox", "read-only", "--no-global-sync", "--iteration-context", "fresh",
            "--timeout-seconds", "15", "--execute",
        ])
    packet = json.loads(output.getvalue())
    assert code == 1, packet
    assert declined == [1], packet
    assert packet.get("status") == "execution_preparation_required", packet
    assert packet["reason_code"] == "turn_execution_preparation_claim_not_consumed"
    assert "todo claim" in packet["claim_command"]
    effects = packet.get("effects") or {}
    assert not effects.get("quota_spent") and not effects.get("state_written")
    assert not marker.exists(), "the Host must not be invoked on a refused preparation"
