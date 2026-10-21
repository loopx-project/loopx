from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.goals.goal_channel import attach_goal_channel_projection
from loopx.control_plane.goals.goal_channel_projection import (
    build_goal_channel_projection,
)
from loopx.control_plane.work_items.task_lease import (
    isoformat,
    now_utc,
    task_lease_dir,
    task_lease_path,
)


GOAL_ID = "goal-hard-lease-visibility"
TODO_ID = "todo_lease_visibility"


def _status_item(*, claimed_by: str | None) -> dict[str, Any]:
    todo: dict[str, Any] = {
        "title": "ship the lease visibility fix",
        "status": "open",
        "todo_id": TODO_ID,
    }
    if claimed_by:
        todo["claimed_by"] = claimed_by
    return {
        "goal_id": GOAL_ID,
        "status": "working",
        "project_asset": {
            "agent_todos": {"first_open_items": [todo]},
        },
    }


def _write_active_lease(
    runtime_root: Path,
    *,
    owner: str,
    todo_id: str = TODO_ID,
    version: int = 3,
    ttl_seconds: int = 600,
) -> dict[str, Any]:
    at = now_utc()
    lease = {
        "schema_version": "task_lease_v0",
        "status": "active",
        "goal_id": GOAL_ID,
        "todo_id": todo_id,
        "owner": owner,
        "idempotency_key": f"{owner}-instance-1",
        "write_scopes": ["src/"],
        "acquire_ttl_seconds": ttl_seconds,
        "version": version,
        "lease_epoch": 1,
        "acquired_at": isoformat(at),
        "updated_at": isoformat(at),
        "expires_at": isoformat(at + timedelta(seconds=ttl_seconds)),
    }
    path = task_lease_path(runtime_root=runtime_root, goal_id=GOAL_ID, todo_id=todo_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lease), encoding="utf-8")
    return lease


def _active_leases(projection: dict[str, Any]) -> list[dict[str, Any]]:
    leases = projection.get("active_leases")
    assert isinstance(leases, list)
    return leases


def _entries_with_status(
    projection: dict[str, Any], status: str
) -> list[dict[str, Any]]:
    return [item for item in _active_leases(projection) if item.get("status") == status]


def test_divergent_hard_lease_is_surfaced_with_conflict_reason(tmp_path: Path) -> None:
    lease = _write_active_lease(tmp_path, owner="agent-a")
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
        runtime_root=tmp_path,
    )

    soft = _entries_with_status(projection, "soft_claim")
    assert soft == [
        {"todo_id": TODO_ID, "owner_agent": "agent-b", "status": "soft_claim"}
    ]
    hard = _entries_with_status(projection, "hard_lease")
    assert hard == [
        {
            "todo_id": TODO_ID,
            "status": "hard_lease",
            "owner_agent": "agent-a",
            "lease_version": 3,
            "lease_epoch": 1,
            "expires_at": lease["expires_at"],
            "reason": "owner_conflicts_with_claim",
            "claimed_by": "agent-b",
        }
    ]


def test_matching_hard_lease_is_surfaced_without_conflict(tmp_path: Path) -> None:
    lease = _write_active_lease(tmp_path, owner="agent-a")
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-a"),
        runtime_root=tmp_path,
    )

    hard = _entries_with_status(projection, "hard_lease")
    assert hard == [
        {
            "todo_id": TODO_ID,
            "status": "hard_lease",
            "owner_agent": "agent-a",
            "lease_version": 3,
            "lease_epoch": 1,
            "expires_at": lease["expires_at"],
        }
    ]


def test_soft_only_goal_preserves_previous_projection_shape(tmp_path: Path) -> None:
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
        runtime_root=tmp_path,
    )

    assert _active_leases(projection) == [
        {"todo_id": TODO_ID, "owner_agent": "agent-b", "status": "soft_claim"}
    ]


def test_missing_runtime_root_preserves_previous_projection_shape() -> None:
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
    )

    assert _active_leases(projection) == [
        {"todo_id": TODO_ID, "owner_agent": "agent-b", "status": "soft_claim"}
    ]


def test_expired_lease_is_not_surfaced(tmp_path: Path) -> None:
    at = now_utc()
    lease = {
        "schema_version": "task_lease_v0",
        "status": "active",
        "goal_id": GOAL_ID,
        "todo_id": TODO_ID,
        "owner": "agent-a",
        "idempotency_key": "agent-a-instance-1",
        "write_scopes": [],
        "acquire_ttl_seconds": 60,
        "version": 1,
        "lease_epoch": 1,
        "acquired_at": isoformat(at - timedelta(seconds=120)),
        "updated_at": isoformat(at - timedelta(seconds=120)),
        "expires_at": isoformat(at - timedelta(seconds=60)),
    }
    path = task_lease_path(runtime_root=tmp_path, goal_id=GOAL_ID, todo_id=TODO_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lease), encoding="utf-8")

    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
        runtime_root=tmp_path,
    )

    assert _entries_with_status(projection, "hard_lease") == []


def test_corrupt_lease_degrades_to_typed_note(tmp_path: Path) -> None:
    lease_dir = task_lease_dir(runtime_root=tmp_path, goal_id=GOAL_ID)
    lease_dir.mkdir(parents=True, exist_ok=True)
    (lease_dir / f"{TODO_ID}.json").write_text("{not json", encoding="utf-8")

    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
        runtime_root=tmp_path,
    )

    assert _entries_with_status(projection, "hard_lease_unreadable") == [
        {
            "todo_id": TODO_ID,
            "status": "hard_lease_unreadable",
            "reason": "corrupt_lease",
        }
    ]
    # soft claim entry is still present alongside the typed note
    assert _entries_with_status(projection, "soft_claim")


def test_attach_goal_channel_projection_forwards_runtime_root(tmp_path: Path) -> None:
    _write_active_lease(tmp_path, owner="agent-a")
    item = _status_item(claimed_by="agent-b")
    attach_goal_channel_projection(
        item,
        goal={"id": GOAL_ID},
        goal_latest_runs=[],
        build_goal_channel_projection=build_goal_channel_projection,
        runtime_root=tmp_path,
    )

    projection = item["goal_channel_projection"]
    hard = _entries_with_status(projection, "hard_lease")
    assert len(hard) == 1
    assert hard[0]["owner_agent"] == "agent-a"
    assert hard[0]["reason"] == "owner_conflicts_with_claim"


def test_lease_released_between_glob_and_read_is_skipped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A lease unlinked after the directory listing is not labeled corrupt."""

    from loopx.control_plane.work_items import task_lease as task_lease_module

    _write_active_lease(tmp_path, owner="agent-a")

    def _raise_file_not_found(path: Path) -> dict[str, Any] | None:
        raise FileNotFoundError(path)

    monkeypatch.setattr(task_lease_module, "read_lease", _raise_file_not_found)
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
        runtime_root=tmp_path,
    )

    assert _entries_with_status(projection, "hard_lease") == []
    assert _entries_with_status(projection, "hard_lease_unreadable") == []
    # soft claim entry is unaffected by the vanished lease file
    assert _entries_with_status(projection, "soft_claim")


def test_html_renderer_shows_hard_lease_conflict_fields(tmp_path: Path) -> None:
    from loopx.presentation.renderers.goal_channel_html import (
        render_goal_channel_projection_html,
    )

    _write_active_lease(tmp_path, owner="agent-a")
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
        runtime_root=tmp_path,
    )

    html = render_goal_channel_projection_html(projection)
    assert "hard_lease" in html
    assert "owner_conflicts_with_claim" in html
    assert "Claimed By" in html


def _write_status_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Registry + markdown workspace mirroring the live split-brain e2e."""

    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-08-10T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    runtime_root = tmp_path / "runtime"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
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
                            "registered_agents": ["agent-a", "agent-b"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry, repo, runtime_root


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_status_attention_queue_surfaces_hard_lease_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    """Regression pin for the real delivery surface.

    Drives loopx.status.collect_status (the same path the status CLI uses)
    over an on-disk workspace, so the runtime_root forwarding hunks in
    loopx/status.py and loopx/control_plane/work_items/attention_queue.py are
    both load-bearing: reverting either one makes the hard_lease entry vanish
    from the attention item's goal_channel_projection and fails this test.
    """

    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.todo_summary import canonical_todo_read_record, structured_todo_item
    from loopx.status import collect_status

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, repo, runtime_root = _write_status_workspace(tmp_path)
    todo_id = TODO_ID
    lease = _write_active_lease(runtime_root, owner="agent-a", todo_id=todo_id)
    # Import the conflicting historical facts together. A fresh canonical
    # acquire would correctly refuse this owner/claim mismatch.
    initialize_canonical_authority(
        runtime_root, GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, handoff_mode="hard_lease", leases=[lease],
            todos=[canonical_todo_read_record(structured_todo_item({
                "todo_id": todo_id, "role": "agent", "status": "open",
                "text": "Deliver one bounded control-plane change.",
                "task_class": "advancement_task", "claimed_by": "agent-b",
            }, role="agent", source_section="Agent Todo"))],
        ),
        state_path=repo / "ACTIVE_GOAL_STATE.md", provider=provider,
    )

    payload = collect_status(
        registry_path=registry,
        runtime_root_override=None,
        limit=5,
        goal_id=GOAL_ID,
    )

    queue = payload["attention_queue"]
    items = [
        item for item in queue.get("items") or [] if item.get("goal_id") == GOAL_ID
    ]
    assert items, "expected an attention item for the fixture goal"
    projection = items[0].get("goal_channel_projection")
    assert isinstance(projection, dict)
    assert projection["coordination_observation"]["source_authority"] == f"{provider}_v0"
    assert projection["coordination_observation"]["legacy_fallback_used"] is False
    assert _entries_with_status(projection, "soft_claim") == [
        {"todo_id": todo_id, "owner_agent": "agent-b", "status": "soft_claim"}
    ]
    assert _entries_with_status(projection, "hard_lease") == [
        {
            "todo_id": todo_id,
            "status": "hard_lease",
            "owner_agent": "agent-a",
            "lease_version": 3,
            "lease_epoch": 1,
            "expires_at": lease["expires_at"],
            "reason": "owner_conflicts_with_claim",
            "claimed_by": "agent-b",
        }
    ]
