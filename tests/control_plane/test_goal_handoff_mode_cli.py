"""CLI surface for the per-goal handoff mode: show/set plus error-code mapping.

``loopx handoff-mode show|set`` reads and writes the ``handoff_mode`` key in
the goal's ACTIVE_GOAL_STATE.md YAML front-matter. Setting requires quiescence
(no open claimed todo, no time-active lease) and refuses with a typed offender
list otherwise; there is no --force in v0. Gate rejections raised by todo and
task-lease verbs must surface their typed error codes through the existing
CLI error_code mapping.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.todos.handoff_mode import (
    HANDOFF_MODE_HARD_LEASE,
    HANDOFF_MODE_LEGACY,
    HANDOFF_MODE_SOFT_CLAIM,
)
from loopx.control_plane.work_items.task_lease import acquire_task_lease
from loopx.todos import add_goal_todo, update_goal_todo

GOAL_ID = "handoff-mode-cli"
AGENT_A = "agent-a"
AGENT_B = "agent-b"


def _write_workspace(
    tmp_path: Path,
    *,
    handoff_mode: str | None = None,
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    state = repo / "ACTIVE_GOAL_STATE.md"
    front_matter = [
        "---",
        f"goal_id: {GOAL_ID}",
        "updated_at: 2026-08-01T00:00:00+00:00",
    ]
    if handoff_mode is not None:
        front_matter.append(f"handoff_mode: {handoff_mode}")
    front_matter.append("---")
    state.write_text(
        "\n".join([*front_matter, "", "## Agent Todo", ""]) + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_A, AGENT_B],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry, state


def _run_cli(
    capsys: pytest.CaptureFixture[str],
    registry: Path,
    *argv: str,
) -> tuple[int, dict[str, Any]]:
    exit_code = cli_main(
        ["--registry", str(registry), "--format", "json", *argv]
    )
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)


def _add_todo(registry: Path, *, claimed_by: str | None = None) -> dict[str, Any]:
    return add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver one bounded control-plane change.",
        task_class="advancement_task",
        claimed_by=claimed_by,
    )


def _set_frontmatter_mode(state: Path, mode: str) -> None:
    lines = state.read_text(encoding="utf-8").splitlines()
    close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    for index in range(1, close):
        if lines[index].split(":", 1)[0].strip() == "handoff_mode":
            lines[index] = f"handoff_mode: {mode}"
            break
    else:
        lines.insert(close, f"handoff_mode: {mode}")
    state.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# (e) mode switch CLI: show / set with quiescence enforcement.
# ---------------------------------------------------------------------------


def test_handoff_mode_show_defaults_to_legacy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_workspace(tmp_path)

    exit_code, payload = _run_cli(
        capsys, registry, "handoff-mode", "show", "--goal-id", GOAL_ID
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["handoff_mode"] == HANDOFF_MODE_LEGACY
    assert payload["source"] == "default"


def test_unpromoted_legacy_set_is_retired_without_mutation(tmp_path, capsys):
    registry, state = _write_workspace(tmp_path)
    before = state.read_bytes()
    code, payload = _run_cli(capsys, registry, "handoff-mode", "set", "--goal-id", GOAL_ID,
                             "--mode", "legacy")
    assert code == 1 and payload["error_code"] == "handoff_mode_retired", payload
    assert state.read_bytes() == before


def test_handoff_mode_show_rejects_invalid_frontmatter_without_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode="banana")
    before = state.read_bytes()

    exit_code, payload = _run_cli(
        capsys, registry, "handoff-mode", "show", "--goal-id", GOAL_ID
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "invalid_handoff_mode"
    assert payload["handoff_mode"] == "banana"
    assert state.read_bytes() == before


def test_handoff_mode_set_repairs_invalid_frontmatter_on_quiescent_goal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode="banana")
    before = state.read_bytes()

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "handoff-mode",
        "set",
        "--goal-id",
        GOAL_ID,
        "--mode",
        HANDOFF_MODE_HARD_LEASE,
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["changed"] is True
    assert payload["previous_mode"] == "banana"
    assert payload["previous_mode_valid"] is False
    assert payload["previous_mode_error_code"] == "invalid_handoff_mode"
    assert payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert state.read_bytes() == before.replace(
        b"handoff_mode: banana", b"handoff_mode: hard_lease"
    )


def test_handoff_mode_set_on_quiescent_goal_updates_frontmatter(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, state = _write_workspace(tmp_path)

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "handoff-mode",
        "set",
        "--goal-id",
        GOAL_ID,
        "--mode",
        HANDOFF_MODE_HARD_LEASE,
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["changed"] is True
    assert payload["previous_mode"] == HANDOFF_MODE_LEGACY
    assert payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    text = state.read_text(encoding="utf-8")
    assert "handoff_mode: hard_lease" in text.split("---")[1]
    assert f"goal_id: {GOAL_ID}" in text

    show_code, show_payload = _run_cli(
        capsys, registry, "handoff-mode", "show", "--goal-id", GOAL_ID
    )
    assert show_code == 0
    assert show_payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert show_payload["source"] == "frontmatter"


def test_handoff_mode_set_invalid_previous_mode_still_refuses_claimed_todo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _set_frontmatter_mode(state, "banana")
    before = state.read_bytes()

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "handoff-mode",
        "set",
        "--goal-id",
        GOAL_ID,
        "--mode",
        HANDOFF_MODE_SOFT_CLAIM,
    )

    assert exit_code == 1
    assert payload["error_code"] == "handoff_mode_not_quiescent"
    assert payload["previous_mode"] == "banana"
    assert payload["previous_mode_valid"] is False
    assert payload["previous_mode_error_code"] == "invalid_handoff_mode"
    assert [item["todo_id"] for item in payload["claimed_todos"]] == [
        todo["todo_id"]
    ]
    assert state.read_bytes() == before


def test_handoff_mode_set_refused_when_open_claimed_todo_exists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    before = state.read_text(encoding="utf-8")

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "handoff-mode",
        "set",
        "--goal-id",
        GOAL_ID,
        "--mode",
        HANDOFF_MODE_SOFT_CLAIM,
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "handoff_mode_not_quiescent"
    claimed = payload["claimed_todos"]
    assert [item["todo_id"] for item in claimed] == [todo["todo_id"]]
    assert claimed[0]["claimed_by"] == AGENT_A
    assert state.read_text(encoding="utf-8") == before


def test_handoff_mode_set_invalid_previous_mode_still_refuses_active_lease(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AGENT_A,
        idempotency_key="turn-invalid-mode-lease",
        ttl_seconds=600,
    )
    _set_frontmatter_mode(state, "banana")
    before = state.read_bytes()

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "handoff-mode",
        "set",
        "--goal-id",
        GOAL_ID,
        "--mode",
        HANDOFF_MODE_HARD_LEASE,
    )

    assert exit_code == 1
    assert payload["error_code"] == "handoff_mode_not_quiescent"
    assert payload["previous_mode"] == "banana"
    assert payload["previous_mode_valid"] is False
    assert payload["previous_mode_error_code"] == "invalid_handoff_mode"
    assert [item["todo_id"] for item in payload["active_leases"]] == [
        todo["todo_id"]
    ]
    assert state.read_bytes() == before


def test_handoff_mode_set_refused_when_time_active_lease_exists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AGENT_A,
        idempotency_key="turn-lease-1",
        ttl_seconds=600,
    )

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "handoff-mode",
        "set",
        "--goal-id",
        GOAL_ID,
        "--mode",
        HANDOFF_MODE_HARD_LEASE,
    )

    assert exit_code == 1
    assert payload["error_code"] == "handoff_mode_not_quiescent"
    leases = payload["active_leases"]
    assert [item["todo_id"] for item in leases] == [todo["todo_id"]]
    assert leases[0]["owner"] == AGENT_A


def test_handoff_mode_set_allowed_after_goal_becomes_quiescent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_workspace(tmp_path)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        clear_claim=True,
    )

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "handoff-mode",
        "set",
        "--goal-id",
        GOAL_ID,
        "--mode",
        HANDOFF_MODE_SOFT_CLAIM,
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["handoff_mode"] == HANDOFF_MODE_SOFT_CLAIM


def test_handoff_mode_set_same_value_is_idempotent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_SOFT_CLAIM)

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "handoff-mode",
        "set",
        "--goal-id",
        GOAL_ID,
        "--mode",
        HANDOFF_MODE_SOFT_CLAIM,
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["changed"] is False


# ---------------------------------------------------------------------------
# Typed error-code mapping through the existing CLI surfaces.
# ---------------------------------------------------------------------------


def test_cli_todo_claim_in_hard_mode_maps_typed_error_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "todo",
        "claim",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        todo["todo_id"],
        "--claimed-by",
        AGENT_B,
        "--agent-id",
        AGENT_B,
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "handoff_mode_requires_lease"
    assert payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE


def test_cli_task_lease_acquire_in_soft_mode_maps_typed_error_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_SOFT_CLAIM)
    todo = _add_todo(registry)

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "task-lease",
        "acquire",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        todo["todo_id"],
        "--owner",
        AGENT_A,
        "--idempotency-key",
        "turn-lease-1",
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "handoff_mode_forbids_lease"
    assert payload["handoff_mode"] == HANDOFF_MODE_SOFT_CLAIM


# ---------------------------------------------------------------------------
# (f) supersede crosses the same lease fence as complete through the CLI.
# ---------------------------------------------------------------------------


def test_todo_supersede_cli_requires_key_in_hard_lease_and_accepts_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AGENT_A,
        idempotency_key="turn-lease-1",
        ttl_seconds=600,
    )

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "todo",
        "supersede",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        todo["todo_id"],
        "--agent-id",
        AGENT_A,
        "--reason",
        "keyless supersede must be fenced",
    )
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "lease_fence_required"

    exit_code, payload = _run_cli(
        capsys,
        registry,
        "todo",
        "supersede",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        todo["todo_id"],
        "--agent-id",
        AGENT_A,
        "--reason",
        "keyed supersede",
        "--task-lease-idempotency-key",
        "turn-lease-1",
        "--task-lease-expected-version",
        "1",
    )
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert payload["task_lease_fence"]["execution_instance_verified"] is True
    assert payload["task_lease_fence"]["released"] is True
