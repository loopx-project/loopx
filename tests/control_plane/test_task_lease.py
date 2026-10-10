from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.work_items import task_lease, task_lease_acquire_adapter
from loopx.control_plane.work_items.local_lease_record import read_lease
from loopx.control_plane.work_items.task_lease import (
    MAX_TASK_LEASE_TTL_SECONDS,
    TaskLeaseError,
    assert_expected_version,
    normalize_idempotency_key,
    normalize_ttl_seconds,
    release_task_lease,
    renew_task_lease,
    task_lease_owner_constraint,
    transfer_task_lease,
    write_scopes_overlap,
)
from loopx.todos import add_goal_todo


@pytest.mark.parametrize("raw", [None, b'{"status":"active"}', b"{broken", b"\xff"],
                         ids=["missing", "valid", "invalid-json", "invalid-utf8"])
def test_local_lease_reader_preserves_bytes_and_types_corruption(tmp_path: Path, raw: bytes | None) -> None:
    path = tmp_path / "lease.json"
    if raw is not None:
        path.write_bytes(raw)
        before_mtime = path.stat().st_mtime_ns

    if raw in (b"{broken", b"\xff"):
        with pytest.raises(TaskLeaseError) as error:
            read_lease(path)
        assert error.value.code == "corrupt_lease"
    else:
        assert read_lease(path) == ({"status": "active"} if raw is not None else None)

    if raw is None:
        assert not path.exists()
    else:
        assert path.read_bytes() == raw
        assert path.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (["docs/**"], ["docs/a.md"], True),
        (["docs/sub/**"], ["docs/sub/a.md"], True),
        (["docs/a*.md"], ["docs/ab.md"], True),
        (["docs/**"], ["docs/sub/**"], True),
        (["**"], ["loopx/cli.py"], True),
        (["docs/a.md"], ["docs/b.md"], False),
        ([], ["docs/a.md"], False),
    ],
)
def test_write_scopes_overlap(
    left: list[str],
    right: list[str],
    expected: bool,
) -> None:
    assert write_scopes_overlap(left, right) is expected


@pytest.mark.parametrize(
    ("todo", "owner", "registered_agents", "reason"),
    [
        (None, "agent-a", ["agent-a"], "todo_not_found"),
        ({"status": "done"}, "agent-a", ["agent-a"], "todo_not_open"),
        ({"status": "open"}, "", ["agent-a"], "invalid_owner"),
        ({"status": "open"}, "agent-b", ["agent-a"], "owner_not_registered"),
        (
            {"status": "open", "excluded_agents": ["agent-a"]},
            "agent-a",
            ["agent-a"],
            "owner_excluded_from_todo",
        ),
        (
            {"status": "open", "claimed_by": "agent-b"},
            "agent-a",
            ["agent-a", "agent-b"],
            "owner_conflicts_with_claim",
        ),
    ],
)
def test_task_lease_owner_constraint_rejects_ineligible_owner(
    todo: dict[str, Any] | None,
    owner: str,
    registered_agents: list[str],
    reason: str,
) -> None:
    constraint = task_lease_owner_constraint(
        todo,
        owner=owner,
        registered_agents=registered_agents,
    )

    assert constraint["effective"] is False
    assert constraint["reason"] == reason


def test_task_lease_owner_constraint_accepts_matching_claim() -> None:
    constraint = task_lease_owner_constraint(
        {
            "status": "open",
            "claimed_by": "agent-a",
            "excluded_agents": ["agent-b"],
        },
        owner="agent-a",
        registered_agents=["agent-a", "agent-b"],
    )

    assert constraint == {"effective": True}


@pytest.mark.parametrize("ttl", [0, -1, MAX_TASK_LEASE_TTL_SECONDS + 1])
def test_normalize_ttl_seconds_rejects_out_of_range(ttl: int) -> None:
    with pytest.raises(TaskLeaseError, match="ttl seconds") as error:
        normalize_ttl_seconds(ttl)

    assert error.value.code == "invalid_ttl"


@pytest.mark.parametrize("key", ["", "contains space", "bad$key"])
def test_normalize_idempotency_key_rejects_non_token(key: str) -> None:
    with pytest.raises(TaskLeaseError) as error:
        normalize_idempotency_key(key)

    assert error.value.code == "invalid_idempotency_key"


def test_expected_version_is_compare_and_swap_guard() -> None:
    with pytest.raises(TaskLeaseError) as error:
        assert_expected_version({"version": 3}, 2)

    assert error.value.code == "version_mismatch"
    assert error.value.payload == {"expected_version": 2, "actual_version": 3}


def test_lease_epoch_migrates_legacy_records_and_rejects_corruption() -> None:
    assert task_lease.lease_epoch(None) == 0
    assert (
        task_lease.lease_epoch({"schema_version": "task_lease_v0", "version": 7}) == 1
    )

    for raw_epoch in (0, -1, "not-an-epoch"):
        with pytest.raises(TaskLeaseError) as error:
            task_lease.lease_epoch({"lease_epoch": raw_epoch})
        assert error.value.code == "corrupt_lease"


def test_bool_disguised_lease_integers_fail_closed() -> None:
    """bool is an int subclass: a JSON ``true`` must not become epoch/version/
    TTL ``1`` silently.  The shared Stage 2 head codec already rejects bool
    (head._is_count); the local record codec is the migration source for
    ``last_lease_epoch`` seeding, so the same typed corrupt_lease applies
    before any local-to-shared migration."""

    for raw in (True, False):
        with pytest.raises(TaskLeaseError) as error:
            task_lease.lease_epoch({"lease_epoch": raw})
        assert error.value.code == "corrupt_lease"
        with pytest.raises(TaskLeaseError) as error:
            task_lease.lease_version({"version": raw})
        assert error.value.code == "corrupt_lease"
        with pytest.raises(TaskLeaseError) as error:
            task_lease.lease_acquire_ttl_seconds({"acquire_ttl_seconds": raw})
        assert error.value.code == "corrupt_lease"


def test_retained_task_lease_lifecycle_preserves_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(task_lease, "now_utc", lambda: now)
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\n"
        "goal_id: goal-a\n"
        "updated_at: 2026-07-13T00:00:00+00:00\n"
        "---\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    runtime_root = tmp_path / "runtime"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": "goal-a",
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
    todo = add_goal_todo(
        registry_path=registry_path,
        goal_id="goal-a",
        role="agent",
        text="Exercise the retained task-lease lifecycle.",
        task_class="advancement_task",
    )
    todo_id = str(todo["todo_id"])
    lease_path = task_lease.task_lease_path(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=todo_id,
    )
    task_lease.write_lease(
        lease_path,
        task_lease.build_lease(
            goal_id="goal-a",
            todo_id=todo_id,
            owner="agent-a",
            idempotency_key="turn-1",
            write_scopes=["loopx/**"],
            acquire_ttl_seconds=120,
            version=1,
            lease_epoch=1,
            acquired_at=now.isoformat().replace("+00:00", "Z"),
            updated_at=now.isoformat().replace("+00:00", "Z"),
            expires_at=(now + timedelta(seconds=120))
            .isoformat()
            .replace("+00:00", "Z"),
        ),
    )

    with pytest.raises(TaskLeaseError) as renew_without_version:
        renew_task_lease(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id="goal-a",
            todo_id=todo_id,
            owner="agent-a",
            idempotency_key="turn-1",
        )
    assert renew_without_version.value.code == "version_required"

    with pytest.raises(TaskLeaseError) as transfer_without_version:
        transfer_task_lease(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id="goal-a",
            todo_id=todo_id,
            owner="agent-a",
            idempotency_key="turn-1",
            new_owner="agent-b",
            new_idempotency_key="turn-2",
        )
    assert transfer_without_version.value.code == "version_required"

    renewed = renew_task_lease(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=todo_id,
        owner="agent-a",
        idempotency_key="turn-1",
        expected_version=1,
    )
    assert renewed["lease"]["version"] == 2
    assert renewed["lease"]["lease_epoch"] == 1

    transferred = transfer_task_lease(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=todo_id,
        owner="agent-a",
        idempotency_key="turn-1",
        new_owner="agent-b",
        new_idempotency_key="turn-2",
        expected_version=2,
    )
    assert transferred["lease"]["owner"] == "agent-b"
    assert transferred["lease"]["version"] == 3
    assert transferred["lease"]["lease_epoch"] == 2

    released = release_task_lease(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=todo_id,
        owner="agent-b",
        idempotency_key="turn-2",
        expected_version=3,
    )
    assert released["released"] is True
    assert released["lease"]["status"] == "released"
    assert released["lease"]["released_at"] == now.isoformat().replace("+00:00", "Z")
    assert released["lease"]["updated_at"] == released["lease"]["released_at"]
    assert task_lease.lease_is_active(released["lease"], at=now) is False
    persisted_path = Path(str(released["lease_path"]))
    assert persisted_path.exists()
    assert task_lease.read_lease(persisted_path) == released["lease"]

    replayed_release = release_task_lease(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id=todo_id,
        owner="agent-b",
        idempotency_key="turn-2",
        expected_version=3,
    )
    assert replayed_release["released"] is True
    assert replayed_release["idempotent"] is True
    assert replayed_release["lease"] == released["lease"]

    with pytest.raises(TaskLeaseError) as missing_version:
        release_task_lease(
            runtime_root=runtime_root,
            goal_id="goal-a",
            todo_id=todo_id,
            owner="agent-b",
            idempotency_key="turn-2",
        )
    assert missing_version.value.code == "version_required"


def test_lifecycle_mutation_fails_closed_without_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(task_lease, "now_utc", lambda: now)
    runtime_root = tmp_path / "runtime"
    lease_path = task_lease.task_lease_path(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id="todo_missing_registry",
    )
    task_lease.write_lease(
        lease_path,
        task_lease.build_lease(
            goal_id="goal-a",
            todo_id="todo_missing_registry",
            owner="agent-a",
            idempotency_key="turn-1",
            write_scopes=[],
            acquire_ttl_seconds=120,
            version=1,
            lease_epoch=1,
            acquired_at=now.isoformat().replace("+00:00", "Z"),
            updated_at=now.isoformat().replace("+00:00", "Z"),
            expires_at=(now + timedelta(seconds=120))
            .isoformat()
            .replace("+00:00", "Z"),
        ),
    )
    before = task_lease.read_lease(lease_path)

    with pytest.raises(TaskLeaseError) as error:
        renew_task_lease(
            registry_path=tmp_path / "missing-registry.json",
            runtime_root=runtime_root,
            goal_id="goal-a",
            todo_id="todo_missing_registry",
            owner="agent-a",
            idempotency_key="turn-1",
            expected_version=1,
        )

    assert error.value.code == "todo_not_found"
    assert task_lease.read_lease(lease_path) == before


def test_legacy_generation_and_unrelated_ttl_do_not_block_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(task_lease, "now_utc", lambda: now)
    runtime_root = tmp_path / "runtime"
    lease_path = task_lease.task_lease_path(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id="todo_legacy",
    )
    task_lease.write_lease(
        lease_path,
        {
            "schema_version": "task_lease_v0",
            "goal_id": "goal-a",
            "todo_id": "todo_legacy",
            "owner": "agent-a",
            "idempotency_key": "legacy-key",
            "write_scopes": [],
            "acquire_ttl_seconds": "unrelated-corrupt-legacy-field",
            "version": 7,
            "status": "active",
            "acquired_at": now.isoformat().replace("+00:00", "Z"),
            "updated_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(seconds=120))
            .isoformat()
            .replace("+00:00", "Z"),
        },
    )

    released = release_task_lease(
        runtime_root=runtime_root,
        goal_id="goal-a",
        todo_id="todo_legacy",
        owner="agent-a",
        idempotency_key="legacy-key",
        expected_version=7,
    )
    assert released["lease"]["lease_epoch"] == 1


def test_holder_verify_transport_retry_reuses_one_fence_operation_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("{}", encoding="utf-8")
    authority = {
        "handoff_mode": "hard_lease",
        "registered_agent_candidates": [["agent-a"]],
        "todos": [
            {
                "todo_id": "todo_retry",
                "status": "open",
                "claimed_by": "agent-a",
                "excluded_agents": [],
            }
        ],
        "todo_projection_error": None,
        "source_receipts": [],
    }
    monkeypatch.setattr(
        task_lease_acquire_adapter,
        "task_lease_acquire_authority_facts",
        lambda **_kwargs: authority,
    )
    seen_operation_ids: list[str] = []

    def retrying_effect_runtime_result(
        _method: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        assert timeout == 15.0
        operation_id = params.get("fence_operation_id")
        assert isinstance(operation_id, str)
        seen_operation_ids.append(operation_id)
        if len(seen_operation_ids) == 1:
            return {
                "ok": False,
                "schema_version": "task_lease_v0",
                "action": "holder_verify",
                "error": "authority changed while the response was in flight",
                "error_code": "authority_source_changed",
            }
        return {
            "ok": True,
            "schema_version": "task_lease_v0",
            "action": "holder_verify",
            "fence": {
                "schema_version": "task_lease_v0",
                "checked": True,
                "active": True,
                "owner": "agent-a",
                "version": 1,
                "lease_epoch": 1,
                "lock_token": "held-token",
                "fence_operation_id": operation_id,
            },
        }

    import loopx.control_plane.effect_runtime as effect_runtime

    monkeypatch.setattr(
        effect_runtime,
        "effect_runtime_result",
        retrying_effect_runtime_result,
    )

    first = task_lease_acquire_adapter.execute_native_task_lease_lifecycle(
        runtime_root=tmp_path / "runtime",
        registry_path=registry_path,
        goal_id="goal-a",
        todo_id="todo_retry",
        operation="holder_verify",
        owner="agent-a",
    )
    second = task_lease_acquire_adapter.execute_native_task_lease_lifecycle(
        runtime_root=tmp_path / "runtime",
        registry_path=registry_path,
        goal_id="goal-a",
        todo_id="todo_retry",
        operation="holder_verify",
        owner="agent-a",
    )

    assert len(seen_operation_ids) == 3
    assert seen_operation_ids[0] == seen_operation_ids[1]
    assert seen_operation_ids[0] != seen_operation_ids[2]
    assert first["fence"]["fence_operation_id"] == seen_operation_ids[0]
    assert second["fence"]["fence_operation_id"] == seen_operation_ids[2]


def test_failed_fence_close_abandons_its_owned_cross_runtime_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    token = "held-fence-token"
    lock_target = task_lease.task_lease_lock_path(
        runtime_root=runtime_root,
        goal_id="goal-a",
    )
    effect_lock = Path(f"{lock_target}.ts-effect.lock")
    close_calls = 0

    monkeypatch.setattr(
        task_lease,
        "runtime_root_from_registry",
        lambda _registry_path, _override: runtime_root,
    )

    def failing_lifecycle(**kwargs: Any) -> dict[str, Any]:
        nonlocal close_calls
        if kwargs["operation"] == "terminal_verify":
            effect_lock.parent.mkdir(parents=True, exist_ok=True)
            effect_lock.write_text(
                json.dumps({"pid": os.getpid(), "token": token}),
                encoding="utf-8",
            )
            return {
                "ok": True,
                "schema_version": "task_lease_v0",
                "action": "terminal_verify",
                "fence": {
                    "schema_version": "task_lease_v0",
                    "required": True,
                    "active": True,
                    "owner": "agent-a",
                    "version": 1,
                    "lease_epoch": 1,
                    "execution_instance_verified": True,
                    "lock_token": token,
                    "fence_operation_id": "a" * 64,
                },
            }
        close_calls += 1
        raise RuntimeError("simulated managed-runtime transport loss")

    monkeypatch.setattr(
        task_lease,
        "_execute_native_task_lease_lifecycle",
        failing_lifecycle,
    )

    with task_lease.hold_task_lease_mutation_fence(
        registry_path=tmp_path / "registry.json",
        goal_id="goal-a",
        todo_id="todo_target",
        todo={"todo_id": "todo_target", "status": "open"},
        actor_agent_id="agent-a",
        idempotency_key="lease-a",
        expected_version=1,
    ) as fence:
        task_lease.release_verified_task_lease_fence(fence, committed=True)
        assert fence["released"] is False

    assert close_calls == 1
    assert not effect_lock.exists()


@pytest.mark.parametrize(
    ("expected_version", "expected_forwarded"),
    [("2", 2), (2.5, 2)],
)
def test_native_lifecycle_expected_version_keeps_legacy_numeric_coercion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_version: Any,
    expected_forwarded: int,
) -> None:
    import loopx.control_plane.effect_runtime as effect_runtime
    forwarded: list[Any] = []

    def rejecting_effect_runtime_result(
        _method: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        assert timeout == 15.0
        forwarded.append(params["expected_version"])
        return {
            "ok": False,
            "schema_version": "task_lease_v0",
            "action": params["operation"],
            "error": "expected_version must be a safe integer or null",
            "error_code": "invalid_request",
        }

    monkeypatch.setattr(
        effect_runtime,
        "effect_runtime_result",
        rejecting_effect_runtime_result,
    )

    with pytest.raises(TaskLeaseError) as error:
        task_lease_acquire_adapter.execute_native_task_lease_lifecycle(
            runtime_root=tmp_path / "runtime",
            goal_id="goal-a",
            todo_id="todo_target",
            operation="release",
            owner="agent-a",
            idempotency_key="lease-a",
            expected_version=expected_version,
        )

    assert error.value.code == "invalid_request"
    assert "expected_version" in str(error.value)
    assert forwarded == [expected_forwarded]


def test_native_lifecycle_expected_version_bool_is_rejected_before_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import loopx.control_plane.effect_runtime as effect_runtime

    def unexpected_effect_runtime_result(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("bool expected_version must be rejected at the adapter edge")

    monkeypatch.setattr(
        effect_runtime,
        "effect_runtime_result",
        unexpected_effect_runtime_result,
    )

    with pytest.raises(TaskLeaseError) as error:
        task_lease_acquire_adapter.execute_native_task_lease_lifecycle(
            runtime_root=tmp_path / "runtime",
            goal_id="goal-a",
            todo_id="todo_target",
            operation="release",
            owner="agent-a",
            idempotency_key="lease-a",
            expected_version=True,
        )

    assert error.value.code == "version_required"
