from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.goals.activation import (
    GoalActivationState,
    build_goal_activation,
    goal_activation_state,
)
from loopx.control_plane.goals.activation_service import set_goal_activation_state
from loopx.control_plane.goals import deletion_service
from loopx.control_plane.goals.deletion_service import delete_stopped_goal
from loopx.control_plane.scheduler.execution_context import (
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.global_registry import sync_project_registry_to_global
from loopx.history import load_registry
from loopx.quota import build_quota_should_run, quota_status
from loopx.registry import registry_goals


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _goal(path: Path, goal_id: str = "goal-one") -> dict[str, object]:
    return next(
        goal
        for goal in registry_goals(load_registry(path))
        if goal.get("id") == goal_id
    )


@pytest.fixture()
def connected_registries(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    source_registry = tmp_path / "project" / ".loopx" / "registry.json"
    source_payload: dict[str, object] = {
        "schema_version": "0.1",
        "common_runtime_root": str(runtime_root),
        "goals": [
            {
                "id": "goal-one",
                "display_name": "A public Goal",
                "repo": str(tmp_path / "project"),
                "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
            }
        ],
    }
    _write_json(source_registry, source_payload)
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(runtime_root),
        goal_id="goal-one",
        dry_run=False,
    )
    assert synced["ok"] is True
    return source_registry, runtime_root / "registry.global.json"


def _orphaned_global_registry(
    tmp_path: Path,
    *,
    source_status: str = "registry_missing",
    activation_state: str = "active",
) -> tuple[Path, Path]:
    source_registry = tmp_path / "removed-project" / ".loopx" / "registry.json"
    if source_status == "goal_missing":
        _write_json(source_registry, {"schema_version": "0.1", "goals": []})
    elif source_status == "registry_unreadable":
        source_registry.parent.mkdir(parents=True, exist_ok=True)
        source_registry.write_text("not-json\n", encoding="utf-8")
    elif source_status != "registry_missing":
        raise ValueError(f"unsupported source status fixture: {source_status}")
    global_registry = tmp_path / "runtime" / "registry.global.json"
    _write_json(
        global_registry,
        {
            "schema_version": "0.1",
            "registry_role": "global-local",
            "goals": [
                {
                    "id": "orphaned-goal",
                    "display_name": "Orphaned Goal",
                    "source_registry": str(source_registry),
                    "activation": build_goal_activation(
                        state=activation_state,
                        updated_at="2026-08-20T00:00:00+00:00",
                        reason="Fixture setup",
                    ),
                }
            ],
        },
    )
    return source_registry, global_registry


def test_activation_contract_defaults_active_and_rejects_unknown_state() -> None:
    assert goal_activation_state({}) is GoalActivationState.ACTIVE
    assert goal_activation_state({"activation_state": "stopped"}) is GoalActivationState.STOPPED
    with pytest.raises(ValueError, match="active or stopped"):
        goal_activation_state({"activation_state": "archived"})


def test_stop_preview_is_zero_write(connected_registries: tuple[Path, Path]) -> None:
    source_registry, global_registry = connected_registries
    before_source = source_registry.read_bytes()
    before_global = global_registry.read_bytes()

    result = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        execute=False,
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["changed"] is True
    assert result["written"] is False
    assert source_registry.read_bytes() == before_source
    assert global_registry.read_bytes() == before_global


@pytest.mark.parametrize(
    "source_status",
    ["registry_missing", "registry_unreadable", "goal_missing"],
)
def test_stop_orphaned_global_goal_uses_fail_safe_fallback(
    tmp_path: Path,
    source_status: str,
) -> None:
    source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        source_status=source_status,
    )

    result = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="orphaned-goal",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )

    assert result["ok"] is True
    assert result["written"] is True
    assert result["readback"]["verified"] is True
    assert result["authority_route"] == {
        "schema_version": "loopx_goal_activation_authority_route_v1",
        "mode": "orphaned_global_stop_fallback",
        "source_status": source_status,
        "resume_requires_source_repair": True,
    }
    assert (
        goal_activation_state(_goal(global_registry, "orphaned-goal"))
        is GoalActivationState.STOPPED
    )
    if source_status == "registry_missing":
        assert source_registry.exists() is False


def test_resume_orphaned_global_goal_fails_closed(tmp_path: Path) -> None:
    _source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        activation_state="stopped",
    )

    with pytest.raises(
        ValueError,
        match="repair the source route before resuming",
    ):
        set_goal_activation_state(
            registry_path=global_registry,
            goal_id="orphaned-goal",
            state="active",
            actor_kind="owner",
            execute=True,
        )

    assert (
        goal_activation_state(_goal(global_registry, "orphaned-goal"))
        is GoalActivationState.STOPPED
    )


def test_stop_does_not_fallback_for_non_global_registry(tmp_path: Path) -> None:
    _source_registry, projected_registry = _orphaned_global_registry(tmp_path)
    project_registry = tmp_path / "project" / ".loopx" / "registry.json"
    payload = load_registry(projected_registry)
    payload.pop("registry_role", None)
    _write_json(project_registry, payload)

    with pytest.raises(
        ValueError,
        match="repair the source route before changing",
    ):
        set_goal_activation_state(
            registry_path=project_registry,
            goal_id="orphaned-goal",
            state="stopped",
            actor_kind="owner",
            execute=True,
        )

    assert (
        goal_activation_state(_goal(project_registry, "orphaned-goal"))
        is GoalActivationState.ACTIVE
    )


def test_stop_and_resume_sync_source_global_and_quota(
    connected_registries: tuple[Path, Path],
) -> None:
    source_registry, global_registry = connected_registries

    stopped = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        reason="Owner is reducing the active workspace",
        actor_kind="owner",
        execute=True,
    )

    assert stopped["ok"] is True
    assert stopped["written"] is True
    assert stopped["readback"]["verified"] is True
    assert _goal(source_registry)["activation"]["actor_kind"] == "owner"
    assert goal_activation_state(_goal(source_registry)) is GoalActivationState.STOPPED
    assert goal_activation_state(_goal(global_registry)) is GoalActivationState.STOPPED
    stopped_quota = quota_status(_goal(global_registry), waiting_on="codex")
    assert stopped_quota["state"] == "paused"
    assert stopped_quota["compute"] == 0
    assert stopped_quota["allowed_slots"] == 0
    assert stopped_quota["configured_compute"] == 1
    assert stopped_quota["configured_allowed_slots"] == 4
    assert stopped_quota["goal_activation_state"] == "stopped"
    assert stopped_quota["pause_cause"] == "goal_stopped"
    assert stopped_quota["blocked_action_scope"] == "automatic_agent_turns"

    resumed = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="active",
        actor_kind="owner",
        execute=True,
    )

    assert resumed["ok"] is True
    assert resumed["readback"]["verified"] is True
    assert goal_activation_state(_goal(source_registry)) is GoalActivationState.ACTIVE
    assert goal_activation_state(_goal(global_registry)) is GoalActivationState.ACTIVE
    resumed_quota = quota_status(_goal(global_registry), waiting_on="codex")
    assert resumed_quota["state"] == "eligible"
    assert resumed_quota["compute"] == 1
    assert resumed_quota["allowed_slots"] == 4


def test_stopped_goal_and_zero_compute_keep_distinct_resume_authority() -> None:
    stopped_status = quota_status_payload(
        goal_id="goal-one",
        status="active",
        recommended_action="Continue the selected work.",
        quota_extra={"compute": 1},
        goal_extra={"activation_state": "stopped"},
    )
    # Stopped Goals are absent from active attention by design. The quota plan
    # must still derive the lifecycle pause from run history.
    stopped_status["attention_queue"]["items"] = []

    stopped = build_quota_should_run(
        stopped_status,
        goal_id="goal-one",
        codex_app_current_rrule="FREQ=MINUTELY;INTERVAL=30",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT
        ),
    )

    assert stopped["should_run"] is False
    assert stopped["quota"]["compute"] == 0
    assert stopped["quota"]["configured_compute"] == 1
    assert stopped["pause_cause"] == "goal_stopped"
    assert stopped["heartbeat_recommendation"]["recommended_mode"] == "goal_stopped"
    assert stopped["automation_liveness"]["automation_action"] == "stop_goal_stopped"
    assert stopped["scheduler_hint"]["reason_code"] == "goal_stopped"
    assert (
        stopped["scheduler_hint"]["codex_app"]["resume_trigger"]
        == "explicit Goal lifecycle resume"
    )

    zero_compute = quota_status(
        {"id": "goal-one", "quota": {"compute": 0}},
        waiting_on="codex",
    )
    assert zero_compute["pause_cause"] == "compute_quota_zero"
    assert "goal_activation_state" not in zero_compute

    stopped_with_zero_compute = quota_status(
        {
            "id": "goal-one",
            "activation_state": "stopped",
            "quota": {"compute": 0},
        },
        waiting_on="codex",
    )
    assert stopped_with_zero_compute["pause_cause"] == "goal_stopped"
    assert stopped_with_zero_compute["configured_compute"] == 0

    resumed_with_zero_compute = quota_status(
        {
            "id": "goal-one",
            "activation_state": "active",
            "quota": {"compute": 0},
        },
        waiting_on="codex",
    )
    assert resumed_with_zero_compute["pause_cause"] == "compute_quota_zero"


def test_idempotent_execute_reconciles_drifted_global_projection(
    connected_registries: tuple[Path, Path],
) -> None:
    source_registry, global_registry = connected_registries
    source_payload = load_registry(source_registry)
    registry_goals(source_payload)[0]["activation"] = build_goal_activation(
        state="stopped",
        updated_at="2026-08-20T00:00:00+00:00",
        reason="Owner stopped the Goal",
    )
    _write_json(source_registry, source_payload)

    result = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )

    assert result["ok"] is True
    assert result["changed"] is False
    assert result["written"] is False
    assert result["projection_reconciled"] is True
    assert result["readback"]["verified"] is True
    assert goal_activation_state(_goal(global_registry)) is GoalActivationState.STOPPED


def test_stop_migrates_legacy_projected_activation_state(
    connected_registries: tuple[Path, Path],
) -> None:
    source_registry, global_registry = connected_registries
    source_payload = load_registry(source_registry)
    registry_goals(source_payload)[0]["activation_state"] = "active"
    _write_json(source_registry, source_payload)
    sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=None,
        goal_id="goal-one",
        dry_run=False,
    )

    stopped = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )

    assert stopped["ok"] is True
    source_goal = _goal(source_registry)
    global_goal = _goal(global_registry)
    assert "activation_state" not in source_goal
    assert "activation_state" not in global_goal
    assert goal_activation_state(source_goal) is GoalActivationState.STOPPED
    assert goal_activation_state(global_goal) is GoalActivationState.STOPPED


def test_owner_confirmed_typed_action_stops_goal(
    connected_registries: tuple[Path, Path], tmp_path: Path
) -> None:
    _source_registry, global_registry = connected_registries
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": "Stop a Goal",
            "normalized_parameters": {
                "goal_id": "goal-one",
                "operation": "stop",
                "reason": "Owner confirmed from the workspace",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": "stop-goal-one",
        }
    )

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "applied"
    assert applied["proposal"]["receipt"]["projection_verified"] is True
    assert applied["proposal"]["receipt"]["outcome"] == "goal_stopped"
    assert goal_activation_state(_goal(global_registry)) is GoalActivationState.STOPPED


def test_delete_stopped_goal_removes_source_and_global(
    connected_registries: tuple[Path, Path],
) -> None:
    source_registry, global_registry = connected_registries
    set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )

    preview = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=False,
    )
    assert preview["dry_run"] is True
    assert preview["written"] is False
    assert _goal(source_registry)["id"] == "goal-one"
    assert _goal(global_registry)["id"] == "goal-one"

    deleted = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=True,
    )

    assert deleted["ok"] is True
    assert deleted["readback"]["verified"] is True
    assert registry_goals(load_registry(source_registry)) == []
    assert registry_goals(load_registry(global_registry)) == []
    assert len(deleted["backup_paths"]) == 2


def test_delete_active_goal_fails_closed(
    connected_registries: tuple[Path, Path],
) -> None:
    _source_registry, global_registry = connected_registries

    with pytest.raises(ValueError, match="stop the Goal before deleting it"):
        delete_stopped_goal(
            registry_path=global_registry,
            goal_id="goal-one",
            execute=True,
        )


def test_delete_orphaned_stopped_global_goal(tmp_path: Path) -> None:
    _source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        activation_state="stopped",
    )

    deleted = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="orphaned-goal",
        execute=True,
    )

    assert deleted["ok"] is True
    assert deleted["readback"]["verified"] is True
    assert registry_goals(load_registry(global_registry)) == []


def test_owner_confirmed_typed_action_deletes_stopped_goal(
    connected_registries: tuple[Path, Path], tmp_path: Path,
) -> None:
    source_registry, global_registry = connected_registries
    set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": "Delete a stopped Goal",
            "normalized_parameters": {
                "goal_id": "goal-one",
                "operation": "delete",
                "reason": "Owner confirmed from the workspace",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": "delete-goal-one",
        }
    )

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "applied"
    assert applied["proposal"]["receipt"]["outcome"] == "goal_deleted"
    assert registry_goals(load_registry(source_registry)) == []
    assert registry_goals(load_registry(global_registry)) == []


def test_owner_confirmed_typed_action_rejects_stale_delete_without_writing(
    connected_registries: tuple[Path, Path], tmp_path: Path,
) -> None:
    source_registry, global_registry = connected_registries
    set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": "Delete a stopped Goal",
            "normalized_parameters": {
                "goal_id": "goal-one",
                "operation": "delete",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": "stale-delete-goal-one",
        }
    )
    global_payload = load_registry(global_registry)
    global_payload["goals"].append({"id": "new-goal"})
    _write_json(global_registry, global_payload)

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert _goal(source_registry)["id"] == "goal-one"
    assert _goal(global_registry)["id"] == "goal-one"
    assert not list(global_registry.parent.glob("*.goal-delete-*.bak"))


def test_goal_deletion_backups_are_unique_and_preserve_preimages(
    connected_registries: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry, global_registry = connected_registries
    source_payload = load_registry(source_registry)
    source_payload["goals"].append({
        "id": "goal-two",
        "display_name": "Another Goal",
        "repo": str(source_registry.parent.parent),
    })
    _write_json(source_registry, source_payload)
    synced = sync_project_registry_to_global(
        registry_path=source_registry,
        runtime_root_override=str(global_registry.parent),
        goal_id="goal-two",
        dry_run=False,
    )
    assert synced["ok"] is True
    for goal_id in ("goal-one", "goal-two"):
        assert set_goal_activation_state(
            registry_path=global_registry,
            goal_id=goal_id,
            state="stopped",
            actor_kind="owner",
            execute=True,
        )["ok"] is True

    monkeypatch.setattr(
        deletion_service,
        "now_local_iso",
        lambda: "2026-08-25T12:00:00+00:00",
    )
    first = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=True,
    )
    second = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-two",
        execute=True,
    )

    backup_paths = first["backup_paths"] + second["backup_paths"]
    assert len(backup_paths) == 4
    assert len(set(backup_paths)) == 4
    first_preimages = [
        set(item.get("id") for item in json.loads(Path(path).read_text())["goals"])
        for path in first["backup_paths"]
    ]
    second_preimages = [
        set(item.get("id") for item in json.loads(Path(path).read_text())["goals"])
        for path in second["backup_paths"]
    ]
    assert all({"goal-one", "goal-two"}.issubset(ids) for ids in first_preimages)
    assert all("goal-one" not in ids and "goal-two" in ids for ids in second_preimages)


def test_owner_confirmed_typed_action_stops_orphaned_global_goal(
    tmp_path: Path,
) -> None:
    _source_registry, global_registry = _orphaned_global_registry(tmp_path)
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": "Stop an orphaned Goal",
            "normalized_parameters": {
                "goal_id": "orphaned-goal",
                "operation": "stop",
                "reason": "Owner confirmed from the workspace",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": "stop-orphaned-goal",
        }
    )

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "applied"
    assert applied["proposal"]["receipt"]["projection_verified"] is True
    assert applied["proposal"]["receipt"]["outcome"] == "goal_stopped"
    assert (
        goal_activation_state(_goal(global_registry, "orphaned-goal"))
        is GoalActivationState.STOPPED
    )
