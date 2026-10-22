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


def _seed_observation(runtime: Path, *, claimed_by: str, leases: list[dict[str, Any]]) -> None:
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.todo_summary import canonical_todo_read_record, structured_todo_item

    state = runtime / "source.md"
    state.write_text("# Goal\n\n## Agent Todo\n")
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=[
        canonical_todo_read_record(structured_todo_item({
            "todo_id": TODO_ID, "status": "open", "claimed_by": claimed_by,
            "text": "Observe canonical ownership", "task_class": "advancement_task",
        }, role="agent", source_section="Agent Todo")),
    ])
    projection["leases"] = leases
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state)


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
    _seed_observation(tmp_path, claimed_by="agent-b", leases=[lease])
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
    _seed_observation(tmp_path, claimed_by="agent-a", leases=[lease])
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
    _seed_observation(tmp_path, claimed_by="agent-b", leases=[])
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
        runtime_root=tmp_path,
    )

    assert _active_leases(projection) == [
        {"todo_id": TODO_ID, "owner_agent": "agent-b", "status": "soft_claim"}
    ]


def test_missing_runtime_root_never_promotes_display_claims_to_ownership() -> None:
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
    )

    assert _active_leases(projection) == []
    assert projection["coordination_observation"]["status"] == "unavailable"


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

    _seed_observation(tmp_path, claimed_by="agent-b", leases=[lease])

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
    _seed_observation(tmp_path, claimed_by="agent-b", leases=[{
        "todo_id": TODO_ID, "goal_id": GOAL_ID, "schema_version": "unknown",
        "status": "active", "expires_at": "2099-01-01T00:00:00Z",
    }])

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
    lease = _write_active_lease(tmp_path, owner="agent-a")
    _seed_observation(tmp_path, claimed_by="agent-b", leases=[lease])
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


def test_canonical_observation_never_opens_local_lease_files(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Canonical ownership has no file-listing race or local read fallback."""

    from loopx.control_plane.work_items import task_lease as task_lease_module

    lease = _write_active_lease(tmp_path, owner="agent-a")
    _seed_observation(tmp_path, claimed_by="agent-b", leases=[lease])

    def _raise_file_not_found(path: Path) -> dict[str, Any] | None:
        pytest.fail("canonical observation opened a legacy lease file")

    monkeypatch.setattr(task_lease_module, "read_lease", _raise_file_not_found)
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=_status_item(claimed_by="agent-b"),
        runtime_root=tmp_path,
    )

    assert len(_entries_with_status(projection, "hard_lease")) == 1
    assert _entries_with_status(projection, "hard_lease_unreadable") == []
    # The claim and lease come from the same provider snapshot.
    assert _entries_with_status(projection, "soft_claim")


def test_html_renderer_shows_hard_lease_conflict_fields(tmp_path: Path) -> None:
    from loopx.presentation.renderers.goal_channel_html import (
        render_goal_channel_projection_html,
    )

    lease = _write_active_lease(tmp_path, owner="agent-a")
    _seed_observation(tmp_path, claimed_by="agent-b", leases=[lease])
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
