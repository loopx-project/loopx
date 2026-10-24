"""CLI coverage for canonical handoff mode and refusal of unpromoted state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.todos.handoff_mode import (
    HANDOFF_MODE_HARD_LEASE,
    HANDOFF_MODE_SOFT_CLAIM,
)
from loopx.control_plane.work_items.task_lease import acquire_task_lease
from loopx.todos import add_goal_todo
from tests.control_plane.canonical_authority_fixture import promoted_create_fixture

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


def _write_canonical_workspace(
    tmp_path: Path,
    *,
    handoff_mode: str,
) -> tuple[Path, Path]:
    registry, _runtime, state = promoted_create_fixture(
        tmp_path,
        goal_id=GOAL_ID,
        handoff_mode=handoff_mode,
        registered_agents=[AGENT_A, AGENT_B],
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


# ---------------------------------------------------------------------------
# (e) mode switch CLI: show / set with quiescence enforcement.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("handoff_mode", [None, "banana"])
def test_unpromoted_show_requires_canonical_authority_without_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    handoff_mode: str | None,
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=handoff_mode)
    before = state.read_bytes()

    exit_code, payload = _run_cli(
        capsys, registry, "handoff-mode", "show", "--goal-id", GOAL_ID
    )

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error_code"] == "canonical_authority_required"
    assert payload["decision_read_from_provider"] is False
    assert payload["legacy_fallback_used"] is False
    assert payload["recovery"] == {"action": "migrate_goal_authority", "backup_required": True}
    assert state.read_bytes() == before


def test_unpromoted_legacy_set_is_retired_without_mutation(tmp_path, capsys):
    registry, state = _write_workspace(tmp_path)
    before = state.read_bytes()
    code, payload = _run_cli(capsys, registry, "handoff-mode", "set", "--goal-id", GOAL_ID,
                             "--mode", "legacy")
    assert code == 1 and payload["error_code"] == "canonical_authority_required", payload
    assert payload["operation"] == "handoff_mode_set"
    assert payload["write_admitted"] is False
    assert payload["decision_read_from_provider"] is False
    assert payload["legacy_fallback_used"] is False
    assert payload["recovery"] == {
        "action": "migrate_goal_authority",
        "backup_required": True,
    }
    assert state.read_bytes() == before


def test_handoff_mode_set_on_quiescent_goal_updates_provider(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, state = _write_canonical_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_SOFT_CLAIM,
    )
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
    assert payload["previous_mode"] == HANDOFF_MODE_SOFT_CLAIM
    assert payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert payload["decision_read_from_provider"] is True
    assert payload["legacy_fallback_used"] is False
    assert state.read_bytes() == before

    show_code, show_payload = _run_cli(
        capsys, registry, "handoff-mode", "show", "--goal-id", GOAL_ID
    )
    assert show_code == 0
    assert show_payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert show_payload["source"] == "canonical_provider"


def test_handoff_mode_set_refused_when_open_claimed_todo_exists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, state = _write_canonical_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
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


def test_handoff_mode_set_refused_when_time_active_lease_exists(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_canonical_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
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
        HANDOFF_MODE_SOFT_CLAIM,
    )

    assert exit_code == 1
    assert payload["error_code"] == "handoff_mode_not_quiescent"
    leases = payload["active_leases"]
    assert [item["todo_id"] for item in leases] == [todo["todo_id"]]
    assert leases[0]["owner"] == AGENT_A


def test_handoff_mode_set_same_value_is_idempotent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_canonical_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_SOFT_CLAIM,
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
    assert payload["changed"] is False


# ---------------------------------------------------------------------------
# Typed error-code mapping through the existing CLI surfaces.
# ---------------------------------------------------------------------------


def test_cli_todo_claim_in_hard_mode_maps_typed_error_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _state = _write_canonical_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
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
    registry, _state = _write_canonical_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_SOFT_CLAIM,
    )
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
    registry, _state = _write_canonical_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
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
    inspect_code, inspect_payload = _run_cli(
        capsys,
        registry,
        "task-lease",
        "inspect",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        todo["todo_id"],
    )
    assert inspect_code == 0
    assert inspect_payload["lease"]["status"] == "released"
