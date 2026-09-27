"""Stage 1 read-only shared goal alignment projection tests.

Every fixture Todo metadata line must only use tokens that exist in
``_TODO_METADATA_FIELD_SCHEMA`` (``todos/contract.py``): the parser silently
drops unknown keys, so an invented token would make the fixture lie. The
builder below asserts each ``todo_id`` actually parsed before any projection
runs, so a silently-ignored metadata line fails the fixture itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import (
    EffectRuntimeRejected,
    effect_runtime_result,
)
from loopx.control_plane.goals import shared_goal_alignment
from loopx.control_plane.goals.shared_goal_alignment import (
    project_shared_goal_alignment,
)
from loopx.control_plane.todos.active_state_todo_parser import (
    parse_active_state_todos,
)

GOAL_ID = "goal-stage1"
AGENTS = ("agent-a", "agent-b")
EVENT_LOG_NAME = "events.jsonl"


def test_excluded_unclaimed_work_is_not_offered_to_the_agent(tmp_path):
    specs = _default_todo_specs()
    specs[1]["excluded_agents"] = "agent-a"
    fixture = _write_fixture(tmp_path, todo_specs=specs)
    result = project_shared_goal_alignment(goal_id=GOAL_ID, agent_id="agent-a",
        project=fixture["project"], registry_path=fixture["registry"], runtime_root=fixture["runtime"])
    assert result["frontier_counts"]["unclaimed_advancement_count"] == 0
    assert result["unclaimed_eligible_work"] == []


def test_corrupt_legacy_lease_only_blocks_a_selected_claim(tmp_path):
    from loopx.control_plane.work_items.task_lease import task_lease_path

    fixture = _write_fixture(tmp_path, todo_specs=_default_todo_specs())
    for todo_id in ("todo_blocked", "todo_lane_a"):
        path = task_lease_path(runtime_root=fixture["runtime"], goal_id=GOAL_ID, todo_id=todo_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken")
        if todo_id == "todo_blocked":
            result = project_shared_goal_alignment(goal_id=GOAL_ID, agent_id="agent-a",
                project=fixture["project"], registry_path=fixture["registry"], runtime_root=fixture["runtime"])
            assert result["frontier_counts"]["current_agent_claimed_advancement_count"] == 1
        else:
            with pytest.raises(ValueError, match="cannot read selected claim lease"):
                project_shared_goal_alignment(goal_id=GOAL_ID, agent_id="agent-a",
                    project=fixture["project"], registry_path=fixture["registry"], runtime_root=fixture["runtime"])


@pytest.mark.parametrize("display", ["missing", "stale", "empty"])
def test_promoted_alignment_does_not_use_the_display(tmp_path, display):
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    fixture = _write_fixture(tmp_path, todo_specs=_default_todo_specs())
    record = {"schema_version": "todo_item_v0", "todo_id": "todo_canonical", "role": "agent",
        "status": "open", "done": False, "text": "Canonical work", "task_class": "advancement_task",
        "archive_state": "active", "source_section": "Agent Todo", "index": 1}
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, todos=[record], handoff_mode="soft_claim")
    initialize_canonical_authority(fixture["runtime"], GOAL_ID, projection, state_path=fixture["state_file"])
    if display == "missing":
        fixture["state_file"].unlink()
    elif display == "empty":
        fixture["state_file"].write_text("")
    result = project_shared_goal_alignment(goal_id=GOAL_ID, agent_id="agent-a",
        project=fixture["project"], registry_path=fixture["registry"], runtime_root=fixture["runtime"])
    assert result["unclaimed_eligible_work"] == [{"todo_id": "todo_canonical", "claim_required_before_work": True}]
    assert fixture["state_file"].exists() is (display != "missing")

STATE_HEADER_LINES = [
    "---",
    "status: active",
    "updated_at: 2026-09-01T00:00:00+00:00",
    "---",
    "",
    "# Stage 1 Alignment Fixture",
    "",
    "## Next Action",
    "",
    "Shared compatibility prose; it must never enter the projection digest.",
    "",
    "## Agent Todo",
    "",
]


def _todo_lines(specs: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for spec in specs:
        tokens = " ".join(f"{key}={value}" for key, value in spec.items())
        lines.append(f"- [ ] [{spec.get('priority', 'P1')}] {spec['text']}")
        lines.append(f"  <!-- loopx:todo {tokens} -->")
    return lines


def _write_fixture(
    root: Path,
    *,
    todo_specs: list[dict[str, str]],
    leases: dict[str, dict[str, object]] | None = None,
    agents: tuple[str, ...] = AGENTS,
    goal_id: str = GOAL_ID,
) -> dict[str, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_relative = Path(".codex") / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
    state_file = project / state_relative
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "\n".join([*STATE_HEADER_LINES, *_todo_lines(todo_specs)]) + "\n",
        encoding="utf-8",
    )

    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": goal_id,
                        "domain": "shared-goal-alignment-stage1",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_relative),
                        "quota": {"compute": 1.0, "window_hours": 24},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": list(agents),
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    runtime.mkdir(parents=True, exist_ok=True)

    if leases:
        for todo_id, lease in leases.items():
            lease_path = (
                runtime / "goals" / goal_id / "task-leases" / f"{todo_id}.json"
            )
            lease_path.parent.mkdir(parents=True, exist_ok=True)
            lease_path.write_text(
                json.dumps(lease, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
    else:
        runtime.mkdir(parents=True, exist_ok=True)

    _assert_fixture_todos_parsed(state_file, todo_specs)
    return {
        "project": project,
        "runtime": runtime,
        "registry": registry_path,
        "state_file": state_file,
    }


def _assert_fixture_todos_parsed(
    state_file: Path,
    todo_specs: list[dict[str, str]],
) -> None:
    parsed = parse_active_state_todos(
        state_file.read_text(encoding="utf-8"),
        item_limit=None,
    )
    items = parsed.get("agent_todos", {}).get("items", [])
    parsed_ids = {item.get("todo_id") for item in items}
    for spec in todo_specs:
        assert spec.get("todo_id") in parsed_ids, (
            f"fixture todo {spec.get('todo_id')} was silently ignored by the "
            "parser; check every metadata token exists in the schema"
        )


def _default_todo_specs() -> list[dict[str, str]]:
    return [
        {
            "todo_id": "todo_lane_a",
            "text": "Continue the agent-a claimed advancement slice.",
            "status": "open",
            "task_class": "advancement_task",
            "action_kind": "run",
            "claimed_by": "agent-a",
            "priority": "P0",
        },
        {
            "todo_id": "todo_unclaimed",
            "text": "Pick up unclaimed work only after claiming it.",
            "status": "open",
            "task_class": "advancement_task",
            "action_kind": "test",
            "priority": "P1",
        },
        {
            "todo_id": "todo_blocked",
            "text": "Blocked slice stays out of the frontier.",
            "status": "blocked",
            "task_class": "advancement_task",
            "action_kind": "fix",
            "claimed_by": "agent-a",
            "priority": "P1",
        },
        {
            "todo_id": "todo_monitor",
            "text": "Monitor work never enters the advancement frontier.",
            "status": "open",
            "task_class": "continuous_monitor",
            "action_kind": "watch",
            "priority": "P2",
        },
    ]




def test_projects_basis_binding_and_unclaimed_work(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert projection["schema_version"] == "shared_goal_alignment_v0"
    assert projection["goal_id"] == GOAL_ID
    assert projection["agent_id"] == "agent-a"
    assert projection["read_only"] is True
    basis = projection["source_basis"]
    assert basis["revision_basis"] == "markdown_active_state"
    assert basis["state_event_basis_sequence"] == 0
    assert basis["source_basis_digest"].startswith("sha256:")
    assert basis["state_updated_at"] == "2026-09-01T00:00:00+00:00"
    frontier = projection["frontier_basis"]
    assert frontier["based_on_state_event_sequence"] is None
    assert frontier["basis_source"] == "unbound"
    assert frontier["last_agent_event_id"] is None
    assert projection["frontier_counts"] == {
        "current_agent_claimed_advancement_count": 1,
        "unclaimed_advancement_count": 1,
        "other_agent_claimed_advancement_count": 0,
    }
    assert [item["todo_id"] for item in projection["unclaimed_eligible_work"]] == [
        "todo_unclaimed"
    ]
    assert all(
        item["claim_required_before_work"] is True
        for item in projection["unclaimed_eligible_work"]
    )
    assert projection["drift_facts"] == []
    assert projection["conflict_facts"] == ["frontier_basis_unverifiable"]


def test_a_goal_id_without_the_goal_prefix_projects(
    tmp_path: Path,
) -> None:
    # The repository Goal-ID contract (validate_goal_id_path_segment) is any
    # safe single path segment: real goal ids such as "loopx-meta" carry no
    # "goal-" prefix and must survive the TypeScript decoder.
    paths = _write_fixture(
        tmp_path,
        goal_id="loopx-meta",
        todo_specs=_default_todo_specs(),
    )

    projection = project_shared_goal_alignment(
        goal_id="loopx-meta",
        agent_id="agent-a",
        project=paths["project"],
    )

    assert projection["goal_id"] == "loopx-meta"
    assert projection["source_basis"]["state_event_basis_sequence"] == 0
    assert projection["drift_facts"] == []
    assert projection["conflict_facts"] == ["frontier_basis_unverifiable"]






def test_without_an_event_log_the_basis_is_unverifiable_not_behind(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert projection["source_basis"]["revision_basis"] == (
        "markdown_active_state"
    )
    assert projection["source_basis"]["state_event_basis_sequence"] == 0
    assert projection["frontier_basis"] == {
        "based_on_state_event_sequence": None,
        "basis_source": "unbound",
        "last_agent_event_id": None,
    }
    assert projection["drift_facts"] == []
    assert projection["conflict_facts"] == ["frontier_basis_unverifiable"]


def test_next_action_prose_never_changes_the_source_basis_digest(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    first = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )
    state_text = paths["state_file"].read_text(encoding="utf-8")
    paths["state_file"].write_text(
        state_text.replace(
            "Shared compatibility prose; it must never enter the projection digest.",
            "A completely different shared Next Action written by a peer.",
        ),
        encoding="utf-8",
    )
    second = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert first["source_basis"]["source_basis_digest"] == (
        second["source_basis"]["source_basis_digest"]
    )


def test_blocked_and_monitor_todos_stay_out_of_unclaimed_work(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    unclaimed_ids = [
        item["todo_id"] for item in projection["unclaimed_eligible_work"]
    ]
    assert unclaimed_ids == ["todo_unclaimed"]
    assert "todo_blocked" not in unclaimed_ids
    assert "todo_monitor" not in unclaimed_ids


def test_lease_owner_mismatch_projects_a_conflict_fact(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
        leases={
            "todo_lane_a": {
                "schema_version": "task_lease_v0",
                "goal_id": GOAL_ID,
                "todo_id": "todo_lane_a",
                "status": "active",
                "expires_at": "2099-01-01T00:00:00Z",
                "owner": "agent-b",
                "lease_epoch": 2,
                "version": 1,
            },
        },
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert projection["conflict_facts"] == ["frontier_basis_unverifiable", "lease_owner_mismatch"]
    assert projection["drift_facts"] == []


def test_matching_lease_owner_is_not_a_conflict(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
        leases={
            "todo_lane_a": {
                "schema_version": "task_lease_v0",
                "goal_id": GOAL_ID,
                "todo_id": "todo_lane_a",
                "status": "active",
                "expires_at": "2099-01-01T00:00:00Z",
                "owner": "agent-a",
                "lease_epoch": 1,
                "version": 1,
            },
        },
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert "lease_owner_mismatch" not in projection["conflict_facts"]


def test_corrupt_lease_epoch_fails_closed(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
        leases={
            "todo_lane_a": {
                "schema_version": "task_lease_v0",
                "status": "active",
                "expires_at": "2099-01-01T00:00:00Z",
                "owner": "agent-a",
                "lease_epoch": 0,
                "version": 1,
            },
        },
    )

    with pytest.raises(ValueError, match="lease epoch"):
        project_shared_goal_alignment(
            goal_id=GOAL_ID,
            agent_id="agent-a",
            project=paths["project"],
        )


def test_open_lane_replan_obligation_projects_a_conflict_fact(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
        status_item={
            "autonomous_replan_obligations_by_agent": {
                "agent-a": {
                    "schema_version": "autonomous_replan_obligation_v0",
                    "required": True,
                },
            },
        },
    )

    assert "open_lane_replan_obligation" in projection["conflict_facts"]


def test_peer_claimed_bound_todo_projects_a_conflict_fact(
    tmp_path: Path,
) -> None:
    specs = [
        *_default_todo_specs(),
        {
            "todo_id": "todo_taken_over",
            "text": "Previously bound to agent-a, now claimed by agent-b.",
            "status": "open",
            "task_class": "advancement_task",
            "action_kind": "fix",
            "claimed_by": "agent-b",
            "bound_agent": "agent-a",
            "priority": "P1",
        },
    ]
    paths = _write_fixture(
        tmp_path,
        todo_specs=specs,
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert "peer_claimed_lane_conflict" in projection["conflict_facts"]


def test_unregistered_agent_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    with pytest.raises(ValueError, match="not registered"):
        project_shared_goal_alignment(
            goal_id=GOAL_ID,
            agent_id="agent-z",
            project=paths["project"],
        )


def test_unknown_goal_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    with pytest.raises(ValueError, match="not registered"):
        project_shared_goal_alignment(
            goal_id="goal-unknown",
            agent_id="agent-a",
            project=paths["project"],
        )






def test_non_numeric_lease_epoch_fails_closed(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
        leases={
            "todo_lane_a": {
                "schema_version": "task_lease_v0",
                "status": "active",
                "expires_at": "2099-01-01T00:00:00Z",
                "owner": "agent-a",
                "lease_epoch": "two",
                "version": 1,
            },
        },
    )

    with pytest.raises(ValueError, match="lease epoch"):
        project_shared_goal_alignment(
            goal_id=GOAL_ID,
            agent_id="agent-a",
            project=paths["project"],
        )


def test_released_lease_record_does_not_project_ownership_conflict(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
        leases={
            "todo_lane_a": {
                "schema_version": "task_lease_v0",
                "goal_id": GOAL_ID,
                "todo_id": "todo_lane_a",
                "status": "released",
                "expires_at": "2099-01-01T00:00:00Z",
                "owner": "agent-b",
                "lease_epoch": 2,
                "version": 1,
            },
        },
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert "lease_owner_mismatch" not in projection["conflict_facts"]
    assert projection["conflict_facts"] == ["frontier_basis_unverifiable"]


def test_expired_lease_record_does_not_project_ownership_conflict(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
        leases={
            "todo_lane_a": {
                "schema_version": "task_lease_v0",
                "goal_id": GOAL_ID,
                "todo_id": "todo_lane_a",
                "status": "active",
                "expires_at": "2020-01-01T00:00:00Z",
                "owner": "agent-b",
                "lease_epoch": 2,
                "version": 1,
            },
        },
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert "lease_owner_mismatch" not in projection["conflict_facts"]
    assert projection["conflict_facts"] == ["frontier_basis_unverifiable"]


def test_active_lease_owner_mismatch_projects_conflict_fact(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
        leases={
            "todo_lane_a": {
                "schema_version": "task_lease_v0",
                "goal_id": GOAL_ID,
                "todo_id": "todo_lane_a",
                "status": "active",
                "expires_at": "2099-01-01T00:00:00Z",
                "owner": "agent-b",
                "lease_epoch": 2,
                "version": 1,
            },
        },
    )

    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert projection["conflict_facts"] == ["frontier_basis_unverifiable", "lease_owner_mismatch"]


@pytest.mark.parametrize(
    "lease_owner",
    [
        pytest.param(None, id="missing-owner"),
        pytest.param("", id="empty-owner"),
        pytest.param("Not A Valid Agent Id!!", id="malformed-owner"),
    ],
)
def test_active_lease_without_a_valid_owner_fails_closed(
    tmp_path: Path,
    lease_owner: str | None,
) -> None:
    # An active hard lease that survives lease_is_active() but carries no
    # normalizable owner is corrupt authority: the projection must fail
    # closed instead of silently reporting the claim as conflict-free.
    lease: dict[str, object] = {
        "schema_version": "task_lease_v0",
        "goal_id": GOAL_ID,
        "todo_id": "todo_lane_a",
        "status": "active",
        "expires_at": "2098-03-04T00:00:00Z",
        "lease_epoch": 3,
        "version": 1,
    }
    if lease_owner is not None:
        lease["owner"] = lease_owner
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
        leases={"todo_lane_a": lease},
    )

    with pytest.raises(ValueError, match="no valid owner"):
        project_shared_goal_alignment(
            goal_id=GOAL_ID,
            agent_id="agent-a",
            project=paths["project"],
        )


def test_projection_is_deterministic_across_repeated_calls(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )

    first = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )
    second = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert json.dumps(first, sort_keys=True) == json.dumps(
        second, sort_keys=True
    )
    assert first["source_basis"]["source_basis_digest"] == (
        second["source_basis"]["source_basis_digest"]
    )
    assert first["drift_facts"] == second["drift_facts"]
    assert first["conflict_facts"] == second["conflict_facts"]


def test_adapter_sends_typed_facts_only(monkeypatch, tmp_path: Path) -> None:
    paths = _write_fixture(
        tmp_path,
        todo_specs=_default_todo_specs(),
    )
    captured: dict[str, object] = {}

    def call(method: str, params: dict[str, object]) -> dict[str, object]:
        captured["method"] = method
        captured["params"] = params
        return effect_runtime_result(method, params)

    monkeypatch.setattr(shared_goal_alignment, "effect_runtime_result", call)
    projection = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )

    assert captured["method"] == "goal.shared_goal_alignment.project"
    request = captured["params"]
    assert isinstance(request, dict)
    # Typed-facts invariant: no prose field ever enters the request.
    assert "prose" not in json.dumps(request)
    assert request["goal_id"] == GOAL_ID
    assert request["agent_id"] == "agent-a"
    assert request["source_basis"]["state_event_basis_sequence"] == 0
    assert request["frontier_basis"]["based_on_state_event_sequence"] is None
    assert "claims" not in request  # Selection now belongs to TS, not the adapter.
    own = next(item for item in request["work_items"] if item["todo_id"] == "todo_lane_a")
    assert own["claimed_by"] == "agent-a"
    assert own["lease"] is None
    assert "text" not in own
    assert request["open_lane_replan_obligation_required"] is False
    assert projection["schema_version"] == "shared_goal_alignment_v0"


def test_registered_method_rejects_an_illegal_request() -> None:
    # A claim attributed to another agent is a contract violation the TS
    # validator must reject at the registered runtime method.
    digest = "sha256:" + "a" * 64
    with pytest.raises(EffectRuntimeRejected) as excinfo:
        effect_runtime_result(
            "goal.shared_goal_alignment.project",
            {
                "schema_version": "shared_goal_alignment_request_v0",
                "goal_id": GOAL_ID,
                "agent_id": "agent-a",
                "source_basis": {
                    "state_event_basis_sequence": 3,
                    "source_basis_digest": digest,
                    "revision_basis": "state_event_log",
                    "state_updated_at": None,
                },
                "frontier_basis": {
                    "based_on_state_event_sequence": 3,
                    "basis_source": "state_event_log",
                    "last_agent_event_id": "evt_stage1_003",
                },
                "frontier_counts": {
                    "current_agent_claimed_advancement_count": 1,
                    "unclaimed_advancement_count": 0,
                    "other_agent_claimed_advancement_count": 0,
                },
                "claims": [
                    {
                        "todo_id": "todo_lane_a",
                        "claimed_by": "agent-b",
                        "lease_epoch": None,
                        "lease_owner": None,
                    }
                ],
                "unclaimed_eligible": [],
                "peer_claimed_bound_todo_ids": [],
                "open_lane_replan_obligation_required": False,
            },
        )
    assert excinfo.value.error_kind == "request_rejected"
