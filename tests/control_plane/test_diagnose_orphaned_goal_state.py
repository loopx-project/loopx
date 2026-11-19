from __future__ import annotations

import json
from pathlib import Path

from loopx.cli import main


GOAL_ID = "reset-goal"


def _project(root: Path, *, orphaned_state: bool) -> tuple[Path, Path]:
    project = root / "project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": []}) + "\n",
        encoding="utf-8",
    )
    readme = project / "README.md"
    readme.write_text("# Diagnose orphan fixture\n", encoding="utf-8")
    if orphaned_state:
        state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        state.parent.mkdir(parents=True)
        state.write_text("# State left by a retired Goal\n", encoding="utf-8")
    return registry, readme


def _diagnose(
    tmp_path: Path,
    *,
    orphaned_state: bool,
    capsys,
) -> tuple[int, dict[str, object]]:
    registry, readme = _project(tmp_path, orphaned_state=orphaned_state)
    exit_code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--format",
            "json",
            "diagnose",
            "--goal-id",
            GOAL_ID,
        ]
    )
    return exit_code, json.loads(capsys.readouterr().out)


def test_diagnose_blocks_an_explicit_goal_with_orphaned_state(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code, payload = _diagnose(
        tmp_path,
        orphaned_state=True,
        capsys=capsys,
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["status_ok"] is True
    assert payload["diagnosis_blocked_by"] == "orphaned_goal_state"
    selected = payload["selected"]
    assert selected["machine_signal"] == "orphaned_goal_state"
    assert selected["status"] == "blocked"
    assert selected["waiting_on"] == "operator"
    assert selected["severity"] == "high"
    gate = selected["orphaned_goal_state"]
    assert gate["state_file_routes"] == [f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"]
    assert gate["forbidden_until_resolved"] == [
        "bootstrap",
        "agent_registration",
        "todo_write",
        "quota_spend",
        "host_loop_activation",
    ]
    quota = selected["quota_signals"]
    assert quota["should_run"] is False
    assert quota["state"] == "orphaned_goal_state"
    assert "effective_action" not in quota
    assert quota["action_required"] is True
    assert selected["agent_commands"] == [
        route["command"] for route in gate["resolution_routes"]
    ]
    assert all("--execute" not in command for command in selected["agent_commands"])
    assert all(
        " quota should-run " not in command for command in selected["agent_commands"]
    )


def test_diagnose_keeps_plain_registry_absence_non_orphaned(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code, payload = _diagnose(
        tmp_path,
        orphaned_state=False,
        capsys=capsys,
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert "diagnosis_blocked_by" not in payload
    selected = payload["selected"]
    assert selected["machine_signal"] == "not_connected_or_not_projected"
    assert "orphaned_goal_state" not in selected
    assert any(
        " quota should-run " in command for command in selected["agent_commands"]
    )


def test_diagnose_markdown_exposes_the_orphan_without_mutation_commands(
    tmp_path: Path,
    capsys,
) -> None:
    registry, readme = _project(tmp_path, orphaned_state=True)

    exit_code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "diagnose",
            "--goal-id",
            GOAL_ID,
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "## Orphaned Goal State" in output
    assert f"`.codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md`" in output
    assert "`quota_spend`" in output
    assert " quota should-run " not in output
    assert "--execute" not in output
