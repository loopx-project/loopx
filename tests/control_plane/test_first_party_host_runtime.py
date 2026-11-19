from __future__ import annotations

import json

import threading
import time
from pathlib import Path

import pytest

from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
    FirstPartyHostRuntimeRejected,
    capture_first_party_host_goal_ref,
)
from loopx.control_plane.goals.source_session_registry_state import guard_path
from loopx.control_plane.projects.registry_codec import (
    load_project_registry,
    project_registry_transaction,
    source_session_registry_transaction,
)
from loopx.file_lock import exclusive_cross_runtime_file_lock


INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _source_registry(instance_id: str) -> dict[str, object]:
    return {
        "schema_version": "0.2",
        "registry_role": "project-local",
        "profile_id": "source_session_v1",
        "common_runtime_root": "/tmp/loopx-runtime",
        "projects": [],
        "goals": [
            {
                "id": "release",
                "goal_instance_id": instance_id,
                "status": "active",
                "execution_authority": False,
            }
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
        "retired_goal_instances": [],
    }


def _write_source_registry(path: Path, instance_id: str) -> None:
    create = None if path.exists() else lambda: _source_registry(instance_id)
    with source_session_registry_transaction(
        path,
        operation="first_party_host_runtime_test",
        create=create,
    ) as transaction:
        payload = transaction.payload_copy()
        payload["goals"] = _source_registry(instance_id)["goals"]
        transaction.commit(payload)


def _replace_goal_instance(path: Path, instance_id: str) -> None:
    with exclusive_cross_runtime_file_lock(
        guard_path(path, "release"),
        operation="first_party_host_runtime_test_recreate",
    ):
        _write_source_registry(path, instance_id)


def test_legacy_profile_preserves_callbacks_and_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text('{"schema_version":"0.1","goals":[]}\n', encoding="utf-8")
    before = registry.read_bytes()
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref=None,
    )
    monkeypatch.setattr(
        "loopx.control_plane.goals.first_party_host_admission.effect_runtime_result",
        lambda *_args, **_kwargs: pytest.fail("legacy flow must not call TypeScript"),
    )
    state = {"schema_version": "legacy", "goal_id": "release"}
    callbacks: list[str] = []

    assert (
        admission.select_state(
            read_state=lambda: state,
            goal_ref_of=lambda value: value,
        )
        is state
    )
    assert admission.accept_result(lambda: callbacks.append("commit")) is None
    assert callbacks == ["commit"]
    assert registry.read_bytes() == before


def test_legacy_launch_stale_registry_requires_fresh_plan_without_rewriting_identity(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    with project_registry_transaction(registry, operation="fixture_create", create=lambda: {
        "schema_version": "0.2", "goals": [{"id": "release"}],
    }) as transaction:
        transaction.commit(transaction.payload_copy())
    original = registry.read_bytes()
    def plan():
        return FirstPartyHostGoalAdmission.for_plan(
            registry_path=registry, goal_id="release", planned_goal_ref=None,
        )

    admitted = plan()
    with admitted.launch_lifetime(operation="fixture_launch"):
        assert registry.read_bytes() == original
    assert not admitted.enabled and admitted.planned_goal_ref is None
    with project_registry_transaction(registry, operation="fixture_independent_configuration") as transaction:
        payload = transaction.payload_copy()
        payload["goals"].append({"id": "unrelated"})
        transaction.commit(payload)
    changed = registry.read_bytes()
    with pytest.raises(FirstPartyHostRuntimeRejected, match="stale_goal_authority"):
        with admitted.launch_lifetime(operation="fixture_stale_launch"):
            pytest.fail("stale registry cannot launch")
    with plan().launch_lifetime(operation="fixture_fresh_launch"):
        assert registry.read_bytes() == changed
    assert registry.read_bytes() == changed


def test_legacy_launch_serializes_with_the_activation_registry_writer(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    with project_registry_transaction(registry, operation="fixture_create", create=lambda: {
        "schema_version": "0.2", "goals": [{"id": "release"}],
    }) as transaction:
        transaction.commit(transaction.payload_copy())
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry, goal_id="release", planned_goal_ref=None,
    )
    writer_started, stopped = threading.Event(), threading.Event()

    def stop_goal():
        writer_started.set()
        with project_registry_transaction(registry, operation="fixture_stop") as transaction:
            payload = transaction.payload_copy()
            payload["goals"][0]["activation"] = {
                "schema_version": "loopx_goal_activation_v1", "state": "stopped",
                "updated_at": "2026-10-10T12:00:00Z", "reason": "Owner stopped this Goal",
            }
            transaction.commit(payload)
        stopped.set()

    writer = threading.Thread(target=stop_goal)
    try:
        with admission.launch_lifetime(operation="fixture_launch"):
            writer.start()
            assert writer_started.wait(2)
            assert not stopped.wait(0.1)
        assert stopped.wait(2)
        with pytest.raises(FirstPartyHostRuntimeRejected, match="goal_stopped"):
            with admission.launch_lifetime(operation="fixture_stopped_launch"):
                pytest.fail("stopped Goal cannot launch")
    finally:
        if writer.ident is not None:
            writer.join(timeout=3)
    assert not writer.is_alive()


def test_source_profile_initializes_absent_state_with_exact_goal_ref(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    _write_source_registry(registry, INSTANCE_A)
    goal_ref = capture_first_party_host_goal_ref(
        registry_path=registry,
        goal_id="release",
    )
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref=goal_ref,
    )

    selected = admission.select_state(
        read_state=lambda: None,
        goal_ref_of=lambda value: value,
        initialize_state=lambda exact: {"goal_ref": exact},
    )

    assert selected == {
        "goal_ref": {
            "goal_id": "release",
            "goal_instance_id": INSTANCE_A,
        }
    }


def test_source_lifetime_does_not_nest_legacy_launch_quota_reader(tmp_path: Path) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    _write_source_registry(registry, INSTANCE_A)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry, goal_id="release",
        planned_goal_ref={"goal_id": "release", "goal_instance_id": INSTANCE_A},
    ).with_launch_decision(
        lambda _registry: pytest.fail("legacy quota admission nested inside source guard"),
    )
    with admission.launch_lifetime(operation="fixture_exact_source_lifetime"):
        assert admission.planned_goal_ref["goal_instance_id"] == INSTANCE_A
    _replace_goal_instance(registry, INSTANCE_B)
    with pytest.raises(FirstPartyHostRuntimeRejected, match="stale_goal_instance"):
        with admission.launch_lifetime(operation="fixture_stale_exact_source_lifetime"):
            pytest.fail("the exact source lifetime check must remain active")


def test_source_profile_rejects_legacy_host_state_without_rewriting_it(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    _write_source_registry(registry, INSTANCE_A)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref={
            "goal_id": "release",
            "goal_instance_id": INSTANCE_A,
        },
    )
    state = {"goal_id": "release"}
    initialized: list[dict[str, str]] = []

    with pytest.raises(FirstPartyHostRuntimeRejected) as exc_info:
        admission.select_state(
            read_state=lambda: state,
            goal_ref_of=lambda value: value,
            initialize_state=lambda exact: initialized.append(exact) or state,
        )

    assert exc_info.value.code == "legacy_host_state"
    assert initialized == []


def test_late_result_is_rejected_after_goal_recreation(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    _write_source_registry(registry, INSTANCE_A)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref=capture_first_party_host_goal_ref(
            registry_path=registry,
            goal_id="release",
        ),
    )
    _replace_goal_instance(registry, INSTANCE_B)
    commits: list[str] = []

    with pytest.raises(FirstPartyHostRuntimeRejected) as exc_info:
        admission.accept_result(lambda: commits.append("accepted"))

    assert exc_info.value.code == "stale_goal_instance"
    assert commits == []


def test_post_writeback_scope_rejects_a_recreated_goal(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    _write_source_registry(registry, INSTANCE_A)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref=capture_first_party_host_goal_ref(
            registry_path=registry,
            goal_id="release",
        ),
    )
    _replace_goal_instance(registry, INSTANCE_B)
    callbacks: list[str] = []

    with pytest.raises(FirstPartyHostRuntimeRejected) as exc_info:
        with admission.current_lifetime(operation="test_post_writeback"):
            callbacks.append("dispatched")

    assert exc_info.value.code == "stale_goal_instance"
    assert callbacks == []


def test_post_writeback_scope_blocks_goal_recreation_until_dispatch_finishes(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    _write_source_registry(registry, INSTANCE_A)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref=capture_first_party_host_goal_ref(
            registry_path=registry,
            goal_id="release",
        ),
    )
    dispatch_started = threading.Event()
    allow_dispatch = threading.Event()
    recreation_finished = threading.Event()

    def dispatch() -> None:
        with admission.current_lifetime(operation="test_post_writeback"):
            dispatch_started.set()
            assert allow_dispatch.wait(timeout=5)

    dispatch_thread = threading.Thread(target=dispatch)
    recreate_thread = threading.Thread(
        target=lambda: (
            _replace_goal_instance(registry, INSTANCE_B),
            recreation_finished.set(),
        ),
    )
    dispatch_thread.start()
    assert dispatch_started.wait(timeout=5)
    recreate_thread.start()
    time.sleep(0.05)
    assert recreation_finished.is_set() is False

    allow_dispatch.set()
    dispatch_thread.join(timeout=5)
    recreate_thread.join(timeout=5)

    assert dispatch_thread.is_alive() is False
    assert recreate_thread.is_alive() is False
    assert recreation_finished.is_set() is True


@pytest.mark.parametrize("registry_state", ["missing", "unreadable"])
def test_source_result_fails_closed_when_registry_is_unavailable(
    tmp_path: Path,
    registry_state: str,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    if registry_state == "unreadable":
        registry.parent.mkdir(parents=True)
        registry.write_text("{not-json", encoding="utf-8")
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref={
            "goal_id": "release",
            "goal_instance_id": INSTANCE_A,
        },
    )
    commits: list[str] = []

    with pytest.raises(FirstPartyHostRuntimeRejected) as exc_info:
        admission.accept_result(lambda: commits.append("accepted"))

    assert exc_info.value.code == "goal_authority_unavailable"
    assert commits == []


def test_old_source_plan_without_instance_id_fails_closed(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    _write_source_registry(registry, INSTANCE_A)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref=None,
    )
    callbacks: list[str] = []

    with pytest.raises(FirstPartyHostRuntimeRejected) as exc_info:
        admission.accept_result(lambda: callbacks.append("accepted"))

    assert exc_info.value.code == "goal_instance_id_missing"
    assert callbacks == []


def test_result_commit_and_recreation_are_serialized_by_the_lifetime_guard(
    tmp_path: Path,
) -> None:
    registry = tmp_path / ".loopx" / "registry.json"
    _write_source_registry(registry, INSTANCE_A)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry,
        goal_id="release",
        planned_goal_ref=capture_first_party_host_goal_ref(
            registry_path=registry,
            goal_id="release",
        ),
    )
    commit_started = threading.Event()
    allow_commit = threading.Event()
    recreation_finished = threading.Event()

    def commit_result() -> None:
        commit_started.set()
        assert allow_commit.wait(timeout=5)

    commit_thread = threading.Thread(
        target=lambda: admission.accept_result(commit_result),
    )
    recreate_thread = threading.Thread(
        target=lambda: (
            _replace_goal_instance(registry, INSTANCE_B),
            recreation_finished.set(),
        ),
    )
    commit_thread.start()
    assert commit_started.wait(timeout=5)
    recreate_thread.start()
    time.sleep(0.05)
    assert recreation_finished.is_set() is False

    allow_commit.set()
    commit_thread.join(timeout=5)
    recreate_thread.join(timeout=5)

    assert commit_thread.is_alive() is False
    assert recreate_thread.is_alive() is False
    assert recreation_finished.is_set() is True
    goal = load_project_registry(registry)["goals"][0]
    assert goal["goal_instance_id"] == INSTANCE_B


@pytest.mark.parametrize("invalid_source", ["missing", "self", "cycle"])
def test_legacy_mirror_launch_refuses_invalid_source_route_without_nested_lock(
    tmp_path: Path, invalid_source: str,
) -> None:
    mirror, source = tmp_path / "global.json", tmp_path / "source.json"
    target = mirror if invalid_source == "self" else source
    mirror.write_text(json.dumps({"schema_version": "0.2", "goals": [
        {"id": "release", "source_registry": str(target)},
    ]}))
    if invalid_source == "cycle":
        source.write_text(json.dumps({"schema_version": "0.2", "goals": [
            {"id": "release", "source_registry": str(mirror)},
        ]}))
    before = mirror.read_bytes()
    with pytest.raises(FirstPartyHostRuntimeRejected, match="goal_authority_unavailable"):
        FirstPartyHostGoalAdmission.for_plan(
            registry_path=mirror, goal_id="release", planned_goal_ref=None,
        )
    assert mirror.read_bytes() == before
    with project_registry_transaction(mirror, operation="fixture_route_lock_released"):
        pass


@pytest.mark.parametrize("padding", ["", " "])
def test_legacy_mirror_source_witness_requires_fresh_plan_after_independent_change(tmp_path: Path, padding: str) -> None:
    mirror, source = tmp_path / "global.json", tmp_path / "source.json"
    source.write_text(json.dumps({"schema_version": "0.2", "goals": [{"id": "release"}]}))
    mirror.write_text(json.dumps({"schema_version": "0.2", "goals": [
        {"id": "release", "source_registry": padding + str(source) + padding},
    ]}))
    def plan():
        return FirstPartyHostGoalAdmission.for_plan(
            registry_path=mirror, goal_id="release", planned_goal_ref=None,
        )
    admitted = plan()
    before = mirror.read_bytes()
    with admitted.launch_lifetime(operation="fixture_initial_mirror_launch"):
        assert not admitted.enabled and admitted.planned_goal_ref is None
    with project_registry_transaction(source, operation="fixture_unrelated_source_edit") as transaction:
        payload = transaction.payload_copy()
        payload["goals"].append({"id": "unrelated"})
        transaction.commit(payload)
    with pytest.raises(FirstPartyHostRuntimeRejected, match="stale_goal_authority"):
        with admitted.launch_lifetime(operation="fixture_stale_source_launch"):
            pytest.fail("a mirror cannot override a changed source witness")
    with plan().launch_lifetime(operation="fixture_fresh_source_launch"):
        assert mirror.read_bytes() == before
