from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
from typing import Any

import pytest
from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

from loopx.control_plane.coordination.local_authority_shadow_projection import (
    canonical_bytes,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.goals import source_session_recreation
from loopx.control_plane.goals.acceptance import (
    configure_goal_acceptance,
    inspect_goal_acceptance,
)
from loopx.control_plane.goals.source_session_recreation import (
    RecreateGoalRequest,
    recreate_goal_instance,
)
from loopx.control_plane.projects.registry_codec import (
    load_project_registry,
    source_session_registry_transaction,
)
from loopx.control_plane.testing.canary_harness import write_fixture_registry


GOAL_ID = "goal-acceptance"
INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _acceptance_document() -> dict[str, Any]:
    return {
        "scope": {"kind": "all_advancement"},
        "objective": "Keep acceptance bound to its originating Goal instance",
        "non_goals": [],
        "criteria": [
            {
                "id": "proof",
                "description": "The independent check passes",
                "validation_argv": ["true"],
                "validation_timeout_seconds": 5,
                "validation_files": [],
            }
        ],
        "bindings": [],
    }


def _canonical_projection(*, legacy_acceptance: bool) -> dict[str, Any]:
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL_ID,
        todos=[],
    )
    if legacy_acceptance:
        document = _acceptance_document()
        projection["goal_acceptance"] = {
            "schema_version": "loopx_goal_acceptance_v0",
            "enabled": True,
            "revision": 1,
            "digest": hashlib.sha256(canonical_bytes(document)).hexdigest(),
            "document": document,
            "bindings": [],
            "verification": None,
        }
    return projection


def _write_source_registry(path: Path) -> None:
    payload = {
        "schema_version": "0.2",
        "registry_role": "project-local",
        "profile_id": "source_session_v1",
        "common_runtime_root": str(path.parent),
        "projects": [],
        "goals": [
            {
                "id": GOAL_ID,
                "goal_instance_id": INSTANCE_A,
                "status": "active",
                "execution_authority": False,
            }
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
        "retired_goal_instances": [],
    }
    with source_session_registry_transaction(
        path,
        operation="acceptance_source_recreation_fixture",
        create=lambda: payload,
    ) as transaction:
        transaction.commit(transaction.payload_copy())


def _promote(
    *,
    tmp_path: Path,
    registry_path: Path,
    provider: str,
    legacy_acceptance: bool,
) -> Path:
    runtime_root = Path(
        str(load_project_registry(registry_path)["common_runtime_root"])
    )
    state_path = tmp_path / "acceptance-source-state.md"
    state_path.write_text("# Acceptance source fixture\n", encoding="utf-8")
    initialize_canonical_authority(
        runtime_root,
        GOAL_ID,
        _canonical_projection(legacy_acceptance=legacy_acceptance),
        state_path=state_path,
        provider=provider,
    )
    return runtime_root


def _inspect(runtime_root: Path, goal_ref: dict[str, str]) -> dict[str, Any]:
    result = effect_runtime_result(
        "goal.acceptance.inspect",
        {
            "runtime_root": str(runtime_root.resolve()),
            "goal_id": GOAL_ID,
            "goal_ref": goal_ref,
        },
    )
    assert isinstance(result, dict)
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_python_acceptance_routing_uses_registered_goal_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    state_path = project / "state.md"
    state_path.write_text("# Goal\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    write_fixture_registry(
        project=project,
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="acceptance",
        adapter_kind="generic_project_goal_v0",
        state_file=str(state_path),
        extra_goal_fields={"goal_instance_id": INSTANCE_A},
    )
    initialize_canonical_authority(
        runtime_root,
        GOAL_ID,
        _canonical_projection(legacy_acceptance=False),
        state_path=state_path,
        provider=provider,
    )

    before = inspect_goal_acceptance(
        registry_path=registry_path,
        goal_id=GOAL_ID,
    )
    applied = configure_goal_acceptance(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        expected_provider_revision=str(before["provider_revision"]),
        document=_acceptance_document(),
        operation_id="configure-exact-acceptance",
        execute=True,
    )

    assert applied["status"] == "applied"
    assert applied["goal_acceptance_contract"]["goal_ref"] == {
        "goal_id": GOAL_ID,
        "goal_instance_id": INSTANCE_A,
    }
    assert applied["goal_acceptance_contract"]["lifecycle_state"] == "active"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_promoted_source_recreation_fences_old_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    _write_source_registry(registry_path)
    runtime_root = _promote(
        tmp_path=tmp_path,
        registry_path=registry_path,
        provider=provider,
        legacy_acceptance=True,
    )
    request = RecreateGoalRequest(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        goal_instance_id=INSTANCE_A,
        operation_id="recreate-acceptance-a-to-b",
    )

    recreated = recreate_goal_instance(request)
    goal_ref = recreated["goal_ref"]
    current = _inspect(runtime_root, goal_ref)

    assert recreated["ok"] is True
    assert current["status"] == "loaded"
    assert current["contract"] is None
    assert current["goal_acceptance_contract"] == {
        "enabled": False,
        "lifecycle_state": "active",
        "goal_ref": goal_ref,
    }


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_recreation_retry_repairs_acceptance_after_source_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    _write_source_registry(registry_path)
    runtime_root = _promote(
        tmp_path=tmp_path,
        registry_path=registry_path,
        provider=provider,
        legacy_acceptance=True,
    )
    request = RecreateGoalRequest(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        goal_instance_id=INSTANCE_A,
        operation_id="recreate-with-activation-retry",
    )
    transition = source_session_recreation.transition_goal_acceptance_lifecycle
    lock = source_session_recreation.exclusive_cross_runtime_file_lock
    held_operations: list[str] = []
    activation_lock_snapshots: list[tuple[str, ...]] = []
    activation_failed = False

    @contextmanager
    def track_lock(path: Path, **kwargs: Any):
        operation = str(kwargs.get("operation"))
        with lock(path, **kwargs) as lock_path:
            held_operations.append(operation)
            try:
                yield lock_path
            finally:
                held_operations.remove(operation)

    def fail_first_activation(**kwargs: Any) -> dict[str, Any] | None:
        nonlocal activation_failed
        if kwargs["transition"]["kind"] == "reconcile_recreated":
            activation_lock_snapshots.append(tuple(held_operations))
            if not activation_failed:
                activation_failed = True
                raise RuntimeError("injected acceptance activation failure")
        return transition(**kwargs)

    monkeypatch.setattr(
        source_session_recreation,
        "exclusive_cross_runtime_file_lock",
        track_lock,
    )
    monkeypatch.setattr(
        source_session_recreation,
        "transition_goal_acceptance_lifecycle",
        fail_first_activation,
    )
    with pytest.raises(RuntimeError, match="injected acceptance activation failure"):
        recreate_goal_instance(request)

    registry = load_project_registry(registry_path)
    goal_ref = {
        "goal_id": GOAL_ID,
        "goal_instance_id": registry["goals"][0]["goal_instance_id"],
    }
    assert goal_ref["goal_instance_id"] != INSTANCE_A
    retiring = _inspect(
        runtime_root,
        {"goal_id": GOAL_ID, "goal_instance_id": INSTANCE_A},
    )
    assert retiring["goal_acceptance_contract"]["lifecycle_state"] == "retiring"

    replayed = recreate_goal_instance(request)
    current = _inspect(runtime_root, goal_ref)

    assert replayed["replayed"] is True
    assert activation_lock_snapshots == [
        ("source_session_goal_lifetime_publish",),
        ("source_session_goal_lifetime_close",),
    ]
    assert current["status"] == "loaded"
    assert current["goal_acceptance_contract"] == {
        "enabled": False,
        "lifecycle_state": "active",
        "goal_ref": goal_ref,
    }


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_historical_recreation_migrates_legacy_acceptance_to_retired_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    _write_source_registry(registry_path)
    request = RecreateGoalRequest(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        goal_instance_id=INSTANCE_A,
        operation_id="historical-recreation-a-to-b",
    )
    recreated = recreate_goal_instance(request)
    goal_ref = recreated["goal_ref"]
    runtime_root = _promote(
        tmp_path=tmp_path,
        registry_path=registry_path,
        provider=provider,
        legacy_acceptance=True,
    )

    replayed = recreate_goal_instance(request)
    current = _inspect(runtime_root, goal_ref)

    assert replayed["replayed"] is True
    assert current["status"] == "loaded"
    assert current["contract"] is None
    assert current["goal_acceptance_contract"] == {
        "enabled": False,
        "lifecycle_state": "active",
        "goal_ref": goal_ref,
    }


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_historical_replay_does_not_replace_a_later_acceptance_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry_path = tmp_path / "project" / ".loopx" / "registry.json"
    _write_source_registry(registry_path)
    runtime_root = _promote(
        tmp_path=tmp_path,
        registry_path=registry_path,
        provider=provider,
        legacy_acceptance=True,
    )
    first_request = RecreateGoalRequest(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        goal_instance_id=INSTANCE_A,
        operation_id="recreate-a-to-b",
    )
    first = recreate_goal_instance(first_request)
    goal_b = first["goal_ref"]
    second = recreate_goal_instance(
        RecreateGoalRequest(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            goal_instance_id=goal_b["goal_instance_id"],
            operation_id="recreate-b-to-c",
        )
    )
    goal_c = second["goal_ref"]

    replayed = recreate_goal_instance(first_request)
    current = _inspect(runtime_root, goal_c)

    assert replayed["replayed"] is True
    assert replayed["goal_ref"] == goal_b
    assert load_project_registry(registry_path)["goals"][0]["goal_instance_id"] == (
        goal_c["goal_instance_id"]
    )
    assert current["status"] == "loaded"
    assert current["goal_acceptance_contract"] == {
        "enabled": False,
        "lifecycle_state": "active",
        "goal_ref": goal_c,
    }
