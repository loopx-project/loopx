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
        cwd=project, runner=run,
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
        cwd=tmp_path, runner=run,
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
        cwd=tmp_path, runner=run,
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
        cwd=tmp_path, runner=run,
    ) is None


def test_a_timed_out_claim_surfaces_as_preparation_failure(tmp_path: Path) -> None:
    def run(command, *, cwd, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    with pytest.raises(ExecutionPreparationClaimFailed) as refused:
        builtin_execution_preparation_claim(
            _decision(), registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime", cwd=tmp_path, runner=run,
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
            runtime_root=tmp_path / "runtime", cwd=tmp_path, runner=run,
        )

    assert refused.value.code == "turn_execution_preparation_claim_failed"
    assert "Todo claimed_by does not grant this actor update authority" in str(refused.value)
    assert TODO in str(refused.value)
