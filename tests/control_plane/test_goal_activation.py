from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

from loopx import chat_goal_lifecycle_actions
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.cli_commands import registry_admin
from loopx.control_plane.goals import activation_service
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


def _preview_delete_action(
    *,
    global_registry: Path,
    action_store: Path,
    idempotency_key: str,
) -> tuple[ChatActionService, dict[str, object]]:
    set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )
    service = ChatActionService(
        store=ChatActionStore(action_store),
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
            "idempotency_key": idempotency_key,
        }
    )
    return service, proposal


def _interrupt_goal_deletion(
    *,
    global_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after_write: int,
) -> dict[str, object]:
    assert set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        actor_kind="owner",
        execute=True,
    )["ok"] is True
    preview = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=False,
    )
    original_write = deletion_service._write_locked_registry
    write_count = 0

    def interrupt_commit(**kwargs: object) -> None:
        nonlocal write_count
        original_write(**kwargs)
        write_count += 1
        if write_count == interrupt_after_write:
            raise KeyboardInterrupt("simulated process termination")

    monkeypatch.setattr(
        deletion_service,
        "_write_locked_registry",
        interrupt_commit,
    )
    with pytest.raises(KeyboardInterrupt, match="simulated process termination"):
        delete_stopped_goal(
            registry_path=global_registry,
            goal_id="goal-one",
            execute=True,
            expected_state_fingerprint=preview["observed_state_fingerprint"],
            expected_source_basis=preview["source_basis"],
        )
    monkeypatch.setattr(
        deletion_service,
        "_write_locked_registry",
        original_write,
    )
    return preview


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
    assert (
        result["source_fingerprint_schema_version"]
        == "loopx_goal_activation_source_fingerprint_v1"
    )
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


def test_resume_retry_rejects_shared_activation_mismatch(
    connected_registries: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry, global_registry = connected_registries
    source = load_registry(source_registry)
    registry_goals(source)[0]["status"] = "active"
    _write_json(source_registry, source)
    real_sync = activation_service.sync_project_registry_to_global

    def corrupt_shared_activation(**kwargs):
        result = real_sync(**kwargs)
        shared = load_registry(global_registry)
        registry_goals(shared)[0]["activation"] = {
            "schema_version": "loopx_goal_activation_v1", "state": "stopped",
        }
        _write_json(global_registry, shared)
        return result

    monkeypatch.setattr(activation_service, "sync_project_registry_to_global", corrupt_shared_activation)
    result = set_goal_activation_state(registry_path=source_registry, goal_id="goal-one",
        state="active", actor_kind="owner", execute=True)
    assert not result["ok"] and not result["readback"]["verified"], result
    assert not result["written"], "An already active source needs no new source mutation"
    assert _goal(source_registry)["status"] == "active"
    assert goal_activation_state(_goal(global_registry)) is GoalActivationState.STOPPED


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


def test_activation_and_agent_registration_share_source_to_global_lock_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "a-runtime"
    source_registry = tmp_path / "z-project" / ".loopx" / "registry.json"
    source_payload: dict[str, object] = {
        "schema_version": "0.1",
        "common_runtime_root": str(runtime_root),
        "goals": [
            {
                "id": "goal-one",
                "display_name": "A public Goal",
                "repo": str(source_registry.parent.parent),
                "quota": {"compute": 1, "allowed_slots": 4, "spent_slots": 0},
                "coordination": {
                    "registered_agents": ["codex-existing"],
                    "agent_model": "peer_v1",
                },
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
    global_registry = runtime_root / "registry.global.json"
    assert str(global_registry) < str(source_registry)

    source_lock_held = threading.Event()
    activation_source_lock_attempted = threading.Event()
    original_configure_goal = registry_admin.configure_goal
    original_project_registry_transaction = (
        activation_service.project_registry_transaction
    )

    def delayed_configure_goal(*args: object, **kwargs: object) -> dict[str, object]:
        source_lock_held.set()
        assert activation_source_lock_attempted.wait(timeout=5)
        return original_configure_goal(*args, **kwargs)

    @contextmanager
    def observed_activation_source_transaction(*args: object, **kwargs: object):
        activation_source_lock_attempted.set()
        with original_project_registry_transaction(*args, **kwargs) as transaction:
            yield transaction

    monkeypatch.setattr(registry_admin, "configure_goal", delayed_configure_goal)
    monkeypatch.setattr(
        activation_service,
        "project_registry_transaction",
        observed_activation_source_transaction,
    )

    def register_agent() -> dict[str, object]:
        return registry_admin.register_agent_via_source_registry(
            runtime_root_arg=str(runtime_root),
            goal_id="goal-one",
            agent_ids=["codex-fresh"],
            require_new=True,
            execute=True,
        )

    def stop_goal() -> dict[str, object]:
        return set_goal_activation_state(
            registry_path=global_registry,
            goal_id="goal-one",
            state="stopped",
            actor_kind="owner",
            execute=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        registration_future = executor.submit(register_agent)
        assert source_lock_held.wait(timeout=5)
        activation_future = executor.submit(stop_goal)
        registration = registration_future.result(timeout=10)
        activation = activation_future.result(timeout=10)

    assert registration["ok"] is True
    assert registration["registration_readback"]["verified"] is True
    assert activation["ok"] is True
    assert activation["readback"]["verified"] is True
    for registry in (source_registry, global_registry):
        goal = _goal(registry)
        assert goal_activation_state(goal) is GoalActivationState.STOPPED
        assert goal["coordination"]["registered_agents"] == [
            "codex-existing",
            "codex-fresh",
        ]


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


def test_upgrade_retires_stored_activation_alias_before_stop(
    connected_registries: tuple[Path, Path],
) -> None:
    source_registry, global_registry = connected_registries
    source_payload = load_registry(source_registry)
    registry_goals(source_payload)[0]["activation_state"] = "active"
    _write_json(source_registry, source_payload)
    from loopx.control_plane.goals.activation_service import upgrade_goal_activation_registries

    before = source_registry.read_bytes()
    # The existing fail-safe Stop can still stop the shared projection while
    # its source awaits upgrade. It must not silently migrate that source.
    fallback = set_goal_activation_state(registry_path=global_registry, goal_id="goal-one",
                                         state="stopped", actor_kind="owner", execute=True)
    assert fallback["ok"] and fallback["readback"]["verified"]
    assert source_registry.read_bytes() == before
    upgrade_goal_activation_registries([source_registry, global_registry], execute=True)

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


@pytest.mark.parametrize("interruption", ["stop", "upgrade"])
def test_http_resume_rejects_stale_confirmation_after_stop_or_upgrade(
    connected_registries: tuple[Path, Path], tmp_path: Path, interruption: str,
) -> None:
    import http.client
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.control_plane.goals.activation_service import upgrade_goal_activation_registries

    source, shared = connected_registries
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = shared
    server.runtime_root = shared.parent
    server.runtime_root_override = str(shared.parent)
    server.action_store = ChatActionStore(tmp_path / "http-actions")
    server.action_service = ChatActionService(store=server.action_store, registry_path=shared)
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(path, body):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{server.server_port}"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def preview(key):
        code, result = post("/api/actions/preview", {"action_kind": "goal.lifecycle",
            "summary": "Resume the Goal", "normalized_parameters": {"goal_id": "goal-one", "operation": "resume"},
            "context": {"kind": "goal_directory"}, "idempotency_key": key})
        assert code == 201, result
        return result["proposal"]["proposal_id"]

    try:
        old = preview("before-upgrade")
        if interruption == "stop":
            stopped = set_goal_activation_state(registry_path=shared, goal_id="goal-one",
                state="stopped", actor_kind="owner", execute=True)
            assert stopped["ok"], stopped
        else:
            registry = load_registry(source)
            registry["goals"][0]["status"] = "paused"
            _write_json(source, registry)
            upgrade_goal_activation_registries([source, shared], execute=True)
        before = source.read_bytes(), shared.read_bytes()
        code, refused = post(f"/api/actions/{old}/apply", {})
        assert code == 409 and refused["error_code"] == "action_stale", refused
        assert (source.read_bytes(), shared.read_bytes()) == before
        fresh = preview("after-upgrade")
        code, applied = post(f"/api/actions/{fresh}/apply", {})
        assert code == 200 and applied["proposal"]["status"] == "applied", applied
        assert goal_activation_state(_goal(source)) is goal_activation_state(_goal(shared)) is GoalActivationState.ACTIVE
        after = source.read_bytes(), shared.read_bytes()
        code, replay = post(f"/api/actions/{fresh}/apply", {})
        assert code == 200 and replay["proposal"]["receipt"] == applied["proposal"]["receipt"], replay
        assert (source.read_bytes(), shared.read_bytes()) == after
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    ("operation", "initial_state"),
    [
        ("stop", GoalActivationState.ACTIVE),
        ("resume", GoalActivationState.STOPPED),
    ],
)
def test_owner_confirmed_lifecycle_action_rejects_a_changed_source_registry(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
    operation: str,
    initial_state: GoalActivationState,
) -> None:
    source_registry, global_registry = connected_registries
    if initial_state is GoalActivationState.STOPPED:
        stopped = set_goal_activation_state(
            registry_path=global_registry,
            goal_id="goal-one",
            state=GoalActivationState.STOPPED,
            actor_kind="owner",
            execute=True,
        )
        assert stopped["ok"] is True
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": f"{operation.title()} a Goal",
            "normalized_parameters": {
                "goal_id": "goal-one",
                "operation": operation,
                "reason": "Owner confirmed from the workspace",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": f"{operation}-goal-one-before-source-change",
        }
    )
    source_payload = load_registry(source_registry)
    registry_goals(source_payload)[0]["display_name"] = "Changed after confirmation"
    _write_json(source_registry, source_payload)

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert (
        goal_activation_state(_goal(source_registry))
        is initial_state
    )
    assert (
        goal_activation_state(_goal(global_registry))
        is initial_state
    )


def test_owner_confirmed_lifecycle_action_rejects_equal_content_source_route_change(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source_a, global_registry = connected_registries
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
            "idempotency_key": "stop-goal-one-before-source-route-change",
        }
    )
    source_b = tmp_path / "project-b" / ".loopx" / "registry.json"
    source_b.parent.mkdir(parents=True)
    source_b.write_bytes(source_a.read_bytes())
    global_payload = load_registry(global_registry)
    registry_goals(global_payload)[0]["source_registry"] = str(source_b)
    _write_json(global_registry, global_payload)
    before = source_a.read_bytes(), source_b.read_bytes(), global_registry.read_bytes()

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert (
        source_a.read_bytes(),
        source_b.read_bytes(),
        global_registry.read_bytes(),
    ) == before
    assert goal_activation_state(_goal(source_a)) is GoalActivationState.ACTIVE
    assert goal_activation_state(_goal(source_b)) is GoalActivationState.ACTIVE


def test_owner_confirmed_lifecycle_action_rechecks_source_inside_write_lock(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry, global_registry = connected_registries
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
            "idempotency_key": "stop-goal-one-before-lock-race",
        }
    )
    original = chat_goal_lifecycle_actions.set_goal_activation_state
    source_changed = False

    def change_source_before_execute(**kwargs: object) -> dict[str, object]:
        nonlocal source_changed
        if kwargs.get("execute") is True and not source_changed:
            source_payload = load_registry(source_registry)
            registry_goals(source_payload)[0]["display_name"] = (
                "Changed before lock acquisition"
            )
            _write_json(source_registry, source_payload)
            source_changed = True
        return original(**kwargs)

    monkeypatch.setattr(
        chat_goal_lifecycle_actions,
        "set_goal_activation_state",
        change_source_before_execute,
    )

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert (
        goal_activation_state(_goal(source_registry))
        is GoalActivationState.ACTIVE
    )
    assert (
        goal_activation_state(_goal(global_registry))
        is GoalActivationState.ACTIVE
    )


def test_owner_confirmed_lifecycle_action_rechecks_route_before_execute(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_a, global_registry = connected_registries
    source_b = tmp_path / "project-b" / ".loopx" / "registry.json"
    source_b.parent.mkdir(parents=True)
    source_b.write_bytes(source_a.read_bytes())
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
            "idempotency_key": "stop-goal-one-before-execute-route-race",
        }
    )
    original = chat_goal_lifecycle_actions.set_goal_activation_state
    switched_global_bytes: bytes | None = None

    def change_route_before_execute(**kwargs: object) -> dict[str, object]:
        nonlocal switched_global_bytes
        if kwargs.get("execute") is True and switched_global_bytes is None:
            global_payload = load_registry(global_registry)
            registry_goals(global_payload)[0]["source_registry"] = str(source_b)
            _write_json(global_registry, global_payload)
            switched_global_bytes = global_registry.read_bytes()
        return original(**kwargs)

    monkeypatch.setattr(
        chat_goal_lifecycle_actions,
        "set_goal_activation_state",
        change_route_before_execute,
    )
    before_sources = source_a.read_bytes(), source_b.read_bytes()

    applied = service.apply(str(proposal["proposal_id"]))

    assert switched_global_bytes is not None
    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert (source_a.read_bytes(), source_b.read_bytes()) == before_sources
    assert global_registry.read_bytes() == switched_global_bytes
    assert goal_activation_state(_goal(source_a)) is GoalActivationState.ACTIVE
    assert goal_activation_state(_goal(source_b)) is GoalActivationState.ACTIVE


def test_lifecycle_execute_rechecks_route_before_source_commit(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_a, global_registry = connected_registries
    preview = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state=GoalActivationState.STOPPED,
        execute=False,
    )
    source_b = tmp_path / "project-b" / ".loopx" / "registry.json"
    source_b.parent.mkdir(parents=True)
    source_b.write_bytes(source_a.read_bytes())
    original_transaction = activation_service.project_registry_transaction
    switched_global_bytes: bytes | None = None

    @contextmanager
    def change_route_before_source_lock(*args: object, **kwargs: object):
        nonlocal switched_global_bytes
        global_payload = load_registry(global_registry)
        registry_goals(global_payload)[0]["source_registry"] = str(source_b)
        _write_json(global_registry, global_payload)
        switched_global_bytes = global_registry.read_bytes()
        with original_transaction(*args, **kwargs) as transaction:
            yield transaction

    monkeypatch.setattr(
        activation_service,
        "project_registry_transaction",
        change_route_before_source_lock,
    )
    before_sources = source_a.read_bytes(), source_b.read_bytes()

    result = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state=GoalActivationState.STOPPED,
        actor_kind="owner",
        expected_state_fingerprint=preview["observed_state_fingerprint"],
        execute=True,
    )

    assert switched_global_bytes is not None
    assert result["ok"] is False
    assert result["error_kind"] == "goal_action_stale"
    assert result["written"] is False
    assert (source_a.read_bytes(), source_b.read_bytes()) == before_sources
    assert global_registry.read_bytes() == switched_global_bytes


def test_owner_confirmed_lifecycle_action_does_not_recover_across_route_change(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source_a, global_registry = connected_registries
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
            "idempotency_key": "stop-goal-one-before-route-recovery",
        }
    )
    source_b = tmp_path / "project-b" / ".loopx" / "registry.json"
    source_b_payload = load_registry(source_a)
    registry_goals(source_b_payload)[0]["activation"] = build_goal_activation(
        state=GoalActivationState.STOPPED,
        updated_at="2026-09-24T00:00:00+00:00",
        reason="Stopped through another source",
        actor_kind="owner",
    )
    _write_json(source_b, source_b_payload)
    global_payload = load_registry(global_registry)
    registry_goals(global_payload)[0]["source_registry"] = str(source_b)
    _write_json(global_registry, global_payload)
    before = source_a.read_bytes(), source_b.read_bytes(), global_registry.read_bytes()

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert (
        source_a.read_bytes(),
        source_b.read_bytes(),
        global_registry.read_bytes(),
    ) == before


def test_owner_confirmed_lifecycle_action_recovers_after_committed_stop(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
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
            "idempotency_key": "stop-goal-one-response-loss",
        }
    )
    committed = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state=GoalActivationState.STOPPED,
        reason="Owner confirmed from the workspace",
        actor_kind="owner",
        execute=True,
    )
    assert committed["ok"] is True

    recovered = service.apply(str(proposal["proposal_id"]))

    assert recovered["proposal"]["status"] == "applied"
    assert recovered["proposal"]["receipt"]["outcome"] == "goal_already_stopped"
    assert (
        goal_activation_state(_goal(global_registry))
        is GoalActivationState.STOPPED
    )


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


@pytest.mark.parametrize(
    ("interrupt_after_write", "retry_from"),
    [(1, "source"), (1, "global"), (2, "source"), (2, "global")],
)
def test_delete_stopped_goal_recovers_interrupted_registry_commit(
    connected_registries: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after_write: int,
    retry_from: str,
) -> None:
    source_registry, global_registry = connected_registries
    preview = _interrupt_goal_deletion(
        global_registry=global_registry,
        monkeypatch=monkeypatch,
        interrupt_after_write=interrupt_after_write,
    )
    retry_registry = source_registry if retry_from == "source" else global_registry

    recovery_preview = delete_stopped_goal(
        registry_path=retry_registry,
        goal_id="goal-one",
        execute=False,
        expected_state_fingerprint=preview["observed_state_fingerprint"],
        expected_source_basis=preview["source_basis"],
    )
    recovered = delete_stopped_goal(
        registry_path=retry_registry,
        goal_id="goal-one",
        execute=True,
        expected_state_fingerprint=preview["observed_state_fingerprint"],
        expected_source_basis=preview["source_basis"],
    )

    assert recovery_preview["ok"] is True
    assert recovery_preview["recovery_pending"] is True
    assert (
        recovery_preview["observed_state_fingerprint"]
        == preview["observed_state_fingerprint"]
    )
    assert recovered["ok"] is True
    assert recovered["recovered"] is True
    assert recovered["readback"]["verified"] is True
    assert registry_goals(load_registry(source_registry)) == []
    assert registry_goals(load_registry(global_registry)) == []
    with pytest.raises(ValueError, match="goal id not found in registry"):
        delete_stopped_goal(
            registry_path=retry_registry,
            goal_id="goal-one",
            execute=False,
        )


def test_delete_stopped_goal_recovery_rejects_concurrent_registry_change(
    connected_registries: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry, global_registry = connected_registries
    preview = _interrupt_goal_deletion(
        global_registry=global_registry,
        monkeypatch=monkeypatch,
        interrupt_after_write=1,
    )

    remaining = next(
        path
        for path in (source_registry, global_registry)
        if any(
            goal.get("id") == "goal-one" for goal in registry_goals(load_registry(path))
        )
    )
    concurrent = load_registry(remaining)
    concurrent["concurrent_marker"] = "must survive"
    _write_json(remaining, concurrent)
    before = source_registry.read_bytes(), global_registry.read_bytes()

    with pytest.raises(
        ValueError,
        match="Goal deletion recovery conflicts with current registry state",
    ):
        delete_stopped_goal(
            registry_path=global_registry,
            goal_id="goal-one",
            execute=True,
            expected_state_fingerprint=preview["observed_state_fingerprint"],
            expected_source_basis=preview["source_basis"],
        )

    assert (source_registry.read_bytes(), global_registry.read_bytes()) == before


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


@pytest.mark.parametrize(
    "source_status",
    ["registry_missing", "registry_unreadable", "goal_missing"],
)
def test_delete_orphaned_stopped_global_goal(
    tmp_path: Path,
    source_status: str,
) -> None:
    _source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        source_status=source_status,
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


def test_delete_orphaned_stopped_goal_fails_when_source_parent_is_not_a_directory(
    tmp_path: Path,
) -> None:
    source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        activation_state="stopped",
    )
    source_registry.parent.parent.mkdir(parents=True)
    source_registry.parent.write_text("not-a-directory\n", encoding="utf-8")

    deleted = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="orphaned-goal",
        execute=True,
    )

    assert deleted["ok"] is False
    assert deleted["error_kind"] == "goal_source_lock_unavailable"
    assert _goal(global_registry, "orphaned-goal")["id"] == "orphaned-goal"
    assert not list(tmp_path.rglob("*.goal-delete-*.bak"))


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


def test_owner_confirmed_typed_action_rejects_orphan_source_restored_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        activation_state="stopped",
    )
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": "Delete an orphaned stopped Goal",
            "normalized_parameters": {
                "goal_id": "orphaned-goal",
                "operation": "delete",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": "delete-orphaned-goal-before-source-restore",
        }
    )
    original_updated_payloads = deletion_service._updated_payloads
    source_restored = False

    def restore_source_before_write(
        current_payloads: dict[Path, dict[str, object]],
        goal_id: str,
    ) -> dict[Path, dict[str, object]]:
        nonlocal source_restored
        if not source_restored:
            source_goal = dict(_goal(global_registry, "orphaned-goal"))
            source_goal.pop("source_registry", None)
            _write_json(
                source_registry,
                {
                    "schema_version": "0.1",
                    "common_runtime_root": str(global_registry.parent),
                    "goals": [source_goal],
                },
            )
            source_restored = True
        return original_updated_payloads(current_payloads, goal_id)

    monkeypatch.setattr(
        deletion_service,
        "_updated_payloads",
        restore_source_before_write,
    )

    applied = service.apply(str(proposal["proposal_id"]))

    assert source_restored is True
    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert _goal(source_registry, "orphaned-goal")["id"] == "orphaned-goal"
    assert _goal(global_registry, "orphaned-goal")["id"] == "orphaned-goal"
    assert not list(tmp_path.rglob("*.goal-delete-*.bak"))


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires privileges")
def test_owner_confirmed_typed_action_rejects_symlinked_orphan_source_lock(
    tmp_path: Path,
) -> None:
    source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        activation_state="stopped",
    )
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": "Delete an orphaned stopped Goal",
            "normalized_parameters": {
                "goal_id": "orphaned-goal",
                "operation": "delete",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": "delete-orphaned-goal-with-unsafe-lock",
        }
    )
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged\n", encoding="utf-8")
    source_registry.parent.mkdir(parents=True)
    source_registry.with_name(f"{source_registry.name}.lock").symlink_to(victim)

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "failed"
    assert applied["proposal"]["receipt"] is None
    assert victim.read_text(encoding="utf-8") == "unchanged\n"
    assert _goal(global_registry, "orphaned-goal")["id"] == "orphaned-goal"


@pytest.mark.parametrize("restored_source_kind", ["goal", "unreadable"])
def test_owner_confirmed_typed_action_rolls_back_when_orphan_source_restores_after_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restored_source_kind: str,
) -> None:
    source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        activation_state="stopped",
    )
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "actions"),
        registry_path=global_registry,
    )
    proposal = service.preview(
        {
            "action_kind": "goal.lifecycle",
            "summary": "Delete an orphaned stopped Goal",
            "normalized_parameters": {
                "goal_id": "orphaned-goal",
                "operation": "delete",
            },
            "context": {"kind": "goal_directory"},
            "idempotency_key": "delete-orphaned-goal-after-source-restore",
        }
    )
    original_atomic_write = deletion_service.atomic_write_json
    restored_source_goal = dict(_goal(global_registry, "orphaned-goal"))
    restored_source_goal.pop("source_registry", None)
    source_restored = False

    def restore_source_after_global_write(
        path: Path,
        payload: dict[str, object],
        **kwargs: object,
    ) -> None:
        nonlocal source_restored
        original_atomic_write(path, payload, **kwargs)
        if (
            not source_restored
            and path == global_registry
            and not registry_goals(payload)
        ):
            if restored_source_kind == "goal":
                _write_json(
                    source_registry,
                    {
                        "schema_version": "0.1",
                        "common_runtime_root": str(global_registry.parent),
                        "goals": [restored_source_goal],
                    },
                )
            else:
                source_registry.mkdir()
            source_restored = True

    monkeypatch.setattr(
        deletion_service,
        "atomic_write_json",
        restore_source_after_global_write,
    )

    proposal_id = str(proposal["proposal_id"])
    with pytest.raises(ValueError, match="Goal deletion readback did not verify"):
        service.apply(proposal_id)

    assert source_restored is True
    persisted = ChatActionStore(tmp_path / "actions").load(proposal_id)
    assert persisted is not None
    assert persisted["status"] == "applying"
    assert persisted["failure"] is None
    assert persisted["receipt"] is None
    if restored_source_kind == "goal":
        assert _goal(source_registry, "orphaned-goal")["id"] == "orphaned-goal"
    else:
        assert source_registry.is_dir()
    assert _goal(global_registry, "orphaned-goal")["id"] == "orphaned-goal"


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


def test_owner_confirmed_typed_action_rejects_changed_source_before_delete(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source_registry, global_registry = connected_registries
    service, proposal = _preview_delete_action(
        global_registry=global_registry,
        action_store=tmp_path / "actions",
        idempotency_key="delete-goal-one-before-source-change",
    )
    source_basis = proposal["canonical_update_basis"]
    activation_preview = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="stopped",
        execute=False,
    )
    assert source_basis == {
        "schema_version": "loopx_goal_deletion_source_basis_v1",
        "source_identity": activation_preview["source_identity"],
        "source_content_sha256": hashlib.sha256(
            source_registry.read_bytes()
        ).hexdigest(),
        "route_mode": "source_to_global",
    }
    source_payload = load_registry(source_registry)
    registry_goals(source_payload)[0]["display_name"] = "Changed after confirmation"
    _write_json(source_registry, source_payload)
    before = source_registry.read_bytes(), global_registry.read_bytes()

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert (source_registry.read_bytes(), global_registry.read_bytes()) == before
    assert _goal(source_registry)["display_name"] == "Changed after confirmation"
    assert _goal(global_registry)["id"] == "goal-one"
    assert not list(tmp_path.rglob("*.goal-delete-*.bak"))


def test_owner_confirmed_typed_action_marks_resumed_goal_stale(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source_registry, global_registry = connected_registries
    service, proposal = _preview_delete_action(
        global_registry=global_registry,
        action_store=tmp_path / "actions",
        idempotency_key="delete-goal-one-before-resume",
    )
    resumed = set_goal_activation_state(
        registry_path=global_registry,
        goal_id="goal-one",
        state="active",
        actor_kind="owner",
        execute=True,
    )
    assert resumed["ok"] is True

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert goal_activation_state(_goal(source_registry)) is GoalActivationState.ACTIVE
    assert goal_activation_state(_goal(global_registry)) is GoalActivationState.ACTIVE
    assert not list(tmp_path.rglob("*.goal-delete-*.bak"))


def test_owner_confirmed_typed_action_recovers_already_committed_delete(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    source_registry, global_registry = connected_registries
    service, proposal = _preview_delete_action(
        global_registry=global_registry,
        action_store=tmp_path / "actions",
        idempotency_key="delete-goal-one-before-receipt",
    )
    deleted = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=True,
        expected_state_fingerprint=proposal["expected_state_fingerprint"],
        expected_source_basis=proposal["canonical_update_basis"],
    )
    assert deleted["ok"] is True

    applied = service.apply(str(proposal["proposal_id"]))

    assert applied["proposal"]["status"] == "applied"
    assert applied["proposal"]["receipt"]["outcome"] == "goal_deleted"
    assert registry_goals(load_registry(source_registry)) == []
    assert registry_goals(load_registry(global_registry)) == []


def test_owner_confirmed_typed_action_recovers_concurrent_same_operation_delete(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry, global_registry = connected_registries
    service, proposal = _preview_delete_action(
        global_registry=global_registry,
        action_store=tmp_path / "actions",
        idempotency_key="delete-goal-one-concurrently",
    )
    original_delete = chat_goal_lifecycle_actions.delete_stopped_goal
    delete_calls = 0

    def delete_between_preflight_and_execute(**kwargs: object) -> dict[str, object]:
        nonlocal delete_calls
        delete_calls += 1
        current = original_delete(**kwargs)
        if delete_calls == 1:
            deleted = original_delete(
                registry_path=global_registry,
                goal_id="goal-one",
                execute=True,
                expected_state_fingerprint=proposal["expected_state_fingerprint"],
                expected_source_basis=proposal["canonical_update_basis"],
            )
            assert deleted["ok"] is True
        return current

    monkeypatch.setattr(
        chat_goal_lifecycle_actions,
        "delete_stopped_goal",
        delete_between_preflight_and_execute,
    )

    applied = service.apply(str(proposal["proposal_id"]))

    assert delete_calls == 2
    assert applied["proposal"]["status"] == "applied"
    assert applied["proposal"]["receipt"]["outcome"] == "goal_deleted"
    assert registry_goals(load_registry(source_registry)) == []
    assert registry_goals(load_registry(global_registry)) == []


def test_owner_confirmed_typed_action_marks_locked_concurrent_delete_stale(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry, global_registry = connected_registries
    service, proposal = _preview_delete_action(
        global_registry=global_registry,
        action_store=tmp_path / "actions",
        idempotency_key="delete-goal-one-during-lock",
    )
    original_transaction = deletion_service.project_registry_transaction
    concurrent_delete_completed = False

    @contextmanager
    def delete_before_source_lock(*args: object, **kwargs: object):
        nonlocal concurrent_delete_completed
        if not concurrent_delete_completed:
            for registry in (source_registry, global_registry):
                payload = load_registry(registry)
                payload["goals"] = []
                _write_json(registry, payload)
            concurrent_delete_completed = True
        with original_transaction(*args, **kwargs) as transaction:
            yield transaction

    monkeypatch.setattr(
        deletion_service,
        "project_registry_transaction",
        delete_before_source_lock,
    )

    applied = service.apply(str(proposal["proposal_id"]))

    assert concurrent_delete_completed is True
    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["receipt"] is None
    assert not list(tmp_path.rglob("*.goal-delete-*.bak"))


@pytest.mark.parametrize(
    ("failure_call", "expected_status"),
    [(1, "failed"), (2, "applying")],
)
def test_owner_confirmed_typed_action_records_delete_failures(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
    expected_status: str,
) -> None:
    source_registry, global_registry = connected_registries
    service, proposal = _preview_delete_action(
        global_registry=global_registry,
        action_store=tmp_path / "actions",
        idempotency_key=f"delete-goal-one-lock-failure-{failure_call}",
    )
    original_delete = chat_goal_lifecycle_actions.delete_stopped_goal
    delete_calls = 0

    def fail_delete(**kwargs: object) -> dict[str, object]:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == failure_call:
            raise PermissionError("simulated registry lock failure")
        return original_delete(**kwargs)

    monkeypatch.setattr(
        chat_goal_lifecycle_actions,
        "delete_stopped_goal",
        fail_delete,
    )

    proposal_id = str(proposal["proposal_id"])
    if failure_call == 1:
        applied = service.apply(proposal_id)["proposal"]
    else:
        with pytest.raises(
            PermissionError,
            match="simulated registry lock failure",
        ):
            service.apply(proposal_id)
        applied = ChatActionStore(tmp_path / "actions").load(proposal_id)

    assert delete_calls == failure_call
    assert applied is not None
    assert applied["status"] == expected_status
    if failure_call == 1:
        assert applied["failure"]["error_code"] == "goal_delete_unavailable"
        assert applied["failure"]["retry_safe"] is True
    else:
        assert applied["failure"] is None
    assert _goal(source_registry)["id"] == "goal-one"
    assert _goal(global_registry)["id"] == "goal-one"


def test_owner_confirmed_typed_action_does_not_mark_committed_delete_retry_safe(
    connected_registries: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry, global_registry = connected_registries
    action_store = tmp_path / "actions"
    service, proposal = _preview_delete_action(
        global_registry=global_registry,
        action_store=action_store,
        idempotency_key="delete-goal-one-before-receipt-write-failure",
    )
    proposal_id = str(proposal["proposal_id"])
    original_write = service.store._write

    def fail_applied_receipt(payload: dict[str, object]) -> None:
        proposals = payload.get("proposals")
        stored = proposals.get(proposal_id) if isinstance(proposals, dict) else None
        if isinstance(stored, dict) and stored.get("status") == "applied":
            raise PermissionError("simulated receipt write failure")
        original_write(payload)

    monkeypatch.setattr(service.store, "_write", fail_applied_receipt)

    with pytest.raises(PermissionError, match="simulated receipt write failure"):
        service.apply(proposal_id)

    persisted = ChatActionStore(action_store).load(proposal_id)
    assert persisted is not None
    assert persisted["status"] == "applying"
    assert persisted["failure"] is None
    assert registry_goals(load_registry(source_registry)) == []
    assert registry_goals(load_registry(global_registry)) == []


def test_delete_stopped_goal_rechecks_source_inside_write_lock(
    connected_registries: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
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
    original_transaction = deletion_service.project_registry_transaction
    source_changed = False

    @contextmanager
    def change_source_before_source_lock(*args: object, **kwargs: object):
        nonlocal source_changed
        if not source_changed:
            source_payload = load_registry(source_registry)
            registry_goals(source_payload)[0]["display_name"] = (
                "Changed before source lock"
            )
            _write_json(source_registry, source_payload)
            source_changed = True
        with original_transaction(*args, **kwargs) as transaction:
            yield transaction

    monkeypatch.setattr(
        deletion_service,
        "project_registry_transaction",
        change_source_before_source_lock,
    )
    before_global = global_registry.read_bytes()

    result = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="goal-one",
        execute=True,
        expected_state_fingerprint=preview["observed_state_fingerprint"],
        expected_source_basis=preview["source_basis"],
    )

    assert source_changed is True
    assert result["ok"] is False
    assert result["stale"] is True
    assert result["written"] is False
    assert _goal(source_registry)["display_name"] == "Changed before source lock"
    assert global_registry.read_bytes() == before_global
    assert not list(global_registry.parent.glob("*.goal-delete-*.bak"))


def test_delete_stopped_goal_rejects_restored_orphan_source(
    tmp_path: Path,
) -> None:
    source_registry, global_registry = _orphaned_global_registry(
        tmp_path,
        activation_state="stopped",
    )
    preview = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="orphaned-goal",
        execute=False,
    )
    source_goal = dict(_goal(global_registry, "orphaned-goal"))
    source_goal.pop("source_registry", None)
    _write_json(
        source_registry,
        {
            "schema_version": "0.1",
            "common_runtime_root": str(global_registry.parent),
            "goals": [source_goal],
        },
    )
    before_global = global_registry.read_bytes()

    result = delete_stopped_goal(
        registry_path=global_registry,
        goal_id="orphaned-goal",
        execute=True,
        expected_state_fingerprint=preview["observed_state_fingerprint"],
        expected_source_basis=preview["source_basis"],
    )

    assert result["ok"] is False
    assert result["stale"] is True
    assert result["written"] is False
    assert _goal(source_registry, "orphaned-goal")["id"] == "orphaned-goal"
    assert global_registry.read_bytes() == before_global
    assert not list(tmp_path.rglob("*.goal-delete-*.bak"))


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
