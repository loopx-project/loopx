from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import loopx.history as history_module
from loopx.contract import check_contract
from loopx.history import collect_history, collect_status_history
from loopx.status import collect_status


REGISTERED_GOAL_ID = "registered-goal"
SECOND_REGISTERED_GOAL_ID = "registered-goal-b"
RUNTIME_ONLY_GOAL_ID = "runtime-only-goal"


def _write_run_index(
    runtime_root: Path,
    goal_id: str,
    runs: list[dict[str, Any]],
) -> None:
    runs_dir = runtime_root / "goals" / goal_id / "runs"
    runs_dir.mkdir(parents=True)
    artifact = runs_dir / "artifact.json"
    artifact.write_text("{}\n", encoding="utf-8")
    rows = []
    for run in runs:
        rows.append(
            json.dumps(
                {
                    **run,
                    "goal_id": goal_id,
                    "json_path": str(artifact),
                    "markdown_path": str(artifact),
                }
            )
        )
    (runs_dir / "index.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    goals = []
    for goal_id in (REGISTERED_GOAL_ID, SECOND_REGISTERED_GOAL_ID):
        state_path = project_root / f"{goal_id}.md"
        state_path.write_text(
            "---\nstatus: active\n---\n\n"
            f"# {goal_id}\n\n"
            "## Agent Todo\n\n"
            "- [ ] Continue work.\n"
            f"  <!-- loopx:todo todo_id=continue-{goal_id} status=open "
            "task_class=advancement_task -->\n",
            encoding="utf-8",
        )
        goals.append(
            {
                "id": goal_id,
                "domain": "status-history-reuse",
                "status": "active",
                "repo": str(project_root),
                "state_file": state_path.name,
                "adapter": {
                    "kind": "test_v0",
                    "status": "connected-read-only",
                },
                "authority_sources": [],
            }
        )
    registry_path = project_root / ".loopx" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": goals,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_run_index(
        runtime_root,
        REGISTERED_GOAL_ID,
        [
            {
                "classification": "registered-tied",
                "generated_at": "2026-09-17T00:00:00Z",
                "agent_id": "agent-a",
            },
            {
                "classification": "registered-malformed",
                "generated_at": "not-a-time",
                "agent_id": "agent-a",
            },
        ],
    )
    _write_run_index(
        runtime_root,
        SECOND_REGISTERED_GOAL_ID,
        [
            {
                "classification": "second-registered-tied",
                "generated_at": "2026-09-17T00:00:00+00:00",
            },
            {
                "classification": "second-registered-older",
                "generated_at": "2026-09-16T00:00:00Z",
            },
        ],
    )
    _write_run_index(
        runtime_root,
        RUNTIME_ONLY_GOAL_ID,
        [
            {
                "classification": "runtime-newer",
                "generated_at": "2026-09-18T00:00:00Z",
            }
        ],
    )
    return registry_path, runtime_root


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_status_history_projection_matches_direct_registry_history(
    tmp_path: Path,
    limit: int,
) -> None:
    registry_path, runtime_root = _write_fixture(tmp_path)

    snapshot = collect_status_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=None,
        limit=limit,
        status_include_runtime_goals=False,
        agent_lane_id="agent-a",
    )
    direct = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=None,
        limit=limit,
        include_runtime_goals=False,
        agent_lane_id="agent-a",
    )

    assert snapshot.status_history == direct
    assert snapshot.contract_audit.goal_count == 3
    assert snapshot.contract_audit.run_count == 5
    assert [goal.goal_id for goal in snapshot.contract_audit.goals] == [
        REGISTERED_GOAL_ID,
        SECOND_REGISTERED_GOAL_ID,
        RUNTIME_ONLY_GOAL_ID,
    ]


@pytest.mark.parametrize(
    ("status_include_runtime_goals", "goal_id", "activation_state_filter"),
    [
        (True, None, None),
        (False, REGISTERED_GOAL_ID, None),
        (False, None, "active"),
    ],
)
def test_status_history_keeps_existing_nonproject_scopes(
    tmp_path: Path,
    status_include_runtime_goals: bool,
    goal_id: str | None,
    activation_state_filter: str | None,
) -> None:
    registry_path, runtime_root = _write_fixture(tmp_path)

    snapshot = collect_status_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        limit=1,
        status_include_runtime_goals=status_include_runtime_goals,
        activation_state_filter=activation_state_filter,
    )
    direct = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        limit=1,
        include_runtime_goals=status_include_runtime_goals,
        activation_state_filter=activation_state_filter,
    )

    assert snapshot.status_history == direct


def test_status_reuses_one_history_scan_without_hiding_runtime_only_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, runtime_root = _write_fixture(tmp_path)
    loaded_goal_ids: list[str] = []
    original_load_index_snapshot = history_module.load_index_snapshot

    def record_load_index_snapshot(path: Path, **kwargs: Any):
        loaded_goal_ids.append(path.parts[-3])
        return original_load_index_snapshot(path, **kwargs)

    monkeypatch.setattr(
        history_module,
        "load_index_snapshot",
        record_load_index_snapshot,
    )

    payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        limit=2,
        agent_lane_id="agent-a",
    )

    assert payload["goal_count"] == 2
    assert [goal["id"] for goal in payload["run_history"]["goals"]] == [
        REGISTERED_GOAL_ID,
        SECOND_REGISTERED_GOAL_ID,
    ]
    assert "run-history goals=3 runs=5" in payload["contract"]["checks"]
    assert loaded_goal_ids == [
        REGISTERED_GOAL_ID,
        SECOND_REGISTERED_GOAL_ID,
        RUNTIME_ONLY_GOAL_ID,
    ]


def test_status_projects_the_exact_run_index_digest(tmp_path: Path) -> None:
    registry_path, runtime_root = _write_fixture(tmp_path)
    index_path = (
        runtime_root / "goals" / REGISTERED_GOAL_ID / "runs" / "index.jsonl"
    )
    index_path.write_bytes(index_path.read_bytes() + b"\n")
    expected_digest = f"sha256:{hashlib.sha256(index_path.read_bytes()).hexdigest()}"

    payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        limit=2,
    )

    goal = next(
        item
        for item in payload["run_history"]["goals"]
        if item["id"] == REGISTERED_GOAL_ID
    )
    assert goal["index_digest"] == expected_digest


def test_contract_rejects_a_history_audit_from_another_runtime(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root = _write_fixture(tmp_path)
    snapshot = collect_status_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=None,
        limit=2,
        status_include_runtime_goals=False,
    )

    mismatched = replace(
        snapshot.contract_audit,
        runtime_root=tmp_path / "other-runtime",
    )
    with pytest.raises(ValueError, match="history audit scope"):
        check_contract(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root),
            scan_roots=[tmp_path],
            limit=2,
            include_public_boundary_scan=False,
            history_audit=mismatched,
        )
