from __future__ import annotations

import json
import sys
from pathlib import Path

from loopx.capabilities.goal_closeout.hooks import (
    GOAL_CLOSEOUT_LEDGER_FILENAME,
    run_goal_closeout,
)
from loopx.capabilities.goal_closeout.machine_defaults import (
    GOAL_CLOSEOUT_CONFIGURATION_SCHEMA,
)
from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (
    configure_machine_configuration,
)


def _configure(root: Path, *, command: list[str], timeout_seconds: int = 5) -> None:
    configuration = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "goal_closeout": {
                "schema_version": GOAL_CLOSEOUT_CONFIGURATION_SCHEMA,
                "command": command,
                "timeout_seconds": timeout_seconds,
            }
        },
    }
    registry = build_builtin_machine_configuration_registry()
    preview = configure_machine_configuration(
        runtime_root=root,
        configuration=configuration,
        registry=registry,
    )
    payload = configure_machine_configuration(
        runtime_root=root,
        configuration=configuration,
        registry=registry,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
    )
    assert payload["ok"], payload


def _ledger_rows(root: Path, goal_id: str) -> list[dict[str, object]]:
    path = root / "goals" / goal_id / GOAL_CLOSEOUT_LEDGER_FILENAME
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_unconfigured_closeout_runs_nothing_and_writes_no_ledger(tmp_path: Path) -> None:
    result = run_goal_closeout(
        runtime_root=tmp_path,
        goal_id="fixture",
        repo=None,
        state_file=None,
        terminal_reason="retired",
        trigger="retire",
    )
    assert result["status"] == "not_configured"
    assert result["ledger_recorded"] is False
    assert not (tmp_path / "goals" / "fixture" / GOAL_CLOSEOUT_LEDGER_FILENAME).exists()


def test_closeout_receives_bounded_payload_and_records_ledger(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    command = [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write(sys.stdin.read())", str(payload_path)]
    _configure(tmp_path, command=command)

    result = run_goal_closeout(
        runtime_root=tmp_path,
        goal_id="fixture",
        repo="/tmp/fixture-repo",
        state_file="/tmp/fixture-state.md",
        terminal_reason="retired",
        trigger="retire",
    )

    assert result["status"] == "success", result
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": "loopx_goal_closeout_v1",
        "goal_id": "fixture",
        "repo": "/tmp/fixture-repo",
        "state_file": "/tmp/fixture-state.md",
        "terminal_reason": "retired",
        "runtime_root": str(tmp_path),
        "trigger": "retire",
    }
    assert result["ledger_recorded"] is True
    rows = _ledger_rows(tmp_path, "fixture")
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert rows[0]["exit_code"] == 0


def test_closeout_timeout_is_bounded_and_never_raises(tmp_path: Path) -> None:
    _configure(
        tmp_path,
        command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=1,
    )
    result = run_goal_closeout(
        runtime_root=tmp_path,
        goal_id="fixture",
        repo=None,
        state_file=None,
        terminal_reason="stopped",
        trigger="stop",
    )
    assert result["status"] == "timeout"
    assert result["timed_out"] is True
    assert result["ledger_recorded"] is True
    assert 900 <= result["duration_ms"] < 10000


def test_closeout_command_failure_is_reported_without_blocking(tmp_path: Path) -> None:
    _configure(
        tmp_path,
        command=[sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(7)"],
    )
    result = run_goal_closeout(
        runtime_root=tmp_path,
        goal_id="fixture",
        repo=None,
        state_file=None,
        terminal_reason="archived",
        trigger="archive",
    )
    assert result["status"] == "failed"
    assert result["exit_code"] == 7
    assert "bad" in result["stderr"]
    assert result["ledger_recorded"] is True
