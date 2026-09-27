from __future__ import annotations

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
