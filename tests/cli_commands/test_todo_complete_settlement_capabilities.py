from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading

import pytest

from loopx.cli_commands import todo as todo_command
from loopx.control_plane.goals.first_party_host_admission import (
    FirstPartyHostGoalAdmission,
)
from loopx.control_plane.goals.source_session_registry_state import guard_path
from loopx.control_plane.projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    source_session_registry_transaction,
)
from loopx.file_lock import exclusive_cross_runtime_file_lock

from settlement_capability_dispatch_fixture import (
    AGENT_ID,
    COMPLETED_TODO,
    GATED_TODO,
    GOAL_ID,
    PLAIN_TODO,
    complete_todo_via_cli,
    run_todo_completion_via_cli,
)

GOAL_INSTANCE_ID = "ginst_" + "a" * 32


def _write_stage_state(project: Path) -> None:
    project.joinpath("goal.md").write_text(
        f"""# Goal

## User Todo

## Agent Todo

- [ ] Ship the localized weekly report slice.
  <!-- loopx:todo todo_id={COMPLETED_TODO} status=open task_class=advancement_task claimed_by={AGENT_ID} continuation_policy=same_agent_non_delivery -->
- [ ] Retry the outbound channel sync once network capacity returns.
  <!-- loopx:todo todo_id={GATED_TODO} status=open task_class=advancement_task claimed_by={AGENT_ID} action_kind=gated_work resume_when=capacity_available:network -->
- [ ] Draft the follow-up frontier analysis.
  <!-- loopx:todo todo_id={PLAIN_TODO} status=open task_class=advancement_task claimed_by={AGENT_ID} -->
""",
        encoding="utf-8",
    )


def test_todo_complete_does_not_replay_superseded_successor_milestone(
    tmp_path: Path,
) -> None:
    captured, _registry, _runtime = complete_todo_via_cli(
        tmp_path,
        journal_capabilities=["network"],
        write_state=_write_stage_state,
    )

    assert captured["available_capabilities"] == ["network"]
    assert captured["post_writeback_hooks"]["intent_count"] == 0
    assert captured["post_writeback_hooks"]["intents"] == []


def test_todo_complete_without_capabilities_does_not_replay_milestone(
    tmp_path: Path,
) -> None:
    captured, _registry, _runtime = complete_todo_via_cli(
        tmp_path,
        journal_capabilities=[],
        write_state=_write_stage_state,
    )

    assert "available_capabilities" not in captured
    assert captured["post_writeback_hooks"]["intent_count"] == 0
    assert captured["post_writeback_hooks"]["intents"] == []


def _write_source_registry(path: Path, runtime: Path) -> None:
    payload = {
        "schema_version": "0.2",
        "registry_role": "project-local",
        "profile_id": SOURCE_SESSION_PROFILE_ID,
        "common_runtime_root": str(runtime),
        "projects": [],
        "goals": [
            {
                "id": GOAL_ID,
                "goal_instance_id": GOAL_INSTANCE_ID,
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
        operation="todo_complete_post_writeback_test",
        create=lambda: payload,
    ) as transaction:
        transaction.commit(transaction.payload_copy())


def _use_admission(
    monkeypatch: pytest.MonkeyPatch,
    admission: object,
) -> None:
    class AdmissionFactory:
        @staticmethod
        def for_plan(**_kwargs: object) -> object:
            return admission

    monkeypatch.setattr(todo_command, "FirstPartyHostGoalAdmission", AdmissionFactory)


def test_todo_complete_isolates_lifetime_lock_timeout_and_recovers_on_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_registry = tmp_path / "source" / ".loopx" / "registry.json"
    source_runtime = tmp_path / "source-runtime"
    _write_source_registry(source_registry, source_runtime)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=source_registry,
        goal_id=GOAL_ID,
        planned_goal_ref={
            "goal_id": GOAL_ID,
            "goal_instance_id": GOAL_INSTANCE_ID,
        },
    )
    _use_admission(monkeypatch, admission)
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_lifetime_lock() -> None:
        with exclusive_cross_runtime_file_lock(
            guard_path(source_registry, GOAL_ID),
            operation="competing_goal_lifetime",
        ):
            lock_acquired.set()
            assert release_lock.wait(timeout=10)

    holder = threading.Thread(target=hold_lifetime_lock)
    holder.start()
    assert lock_acquired.wait(timeout=5)
    try:
        failed, registry_path, runtime = complete_todo_via_cli(
            tmp_path,
            journal_capabilities=["network"],
            write_state=_write_stage_state,
        )
    finally:
        release_lock.set()
        holder.join(timeout=5)

    assert holder.is_alive() is False
    assert failed["changed"] is True
    assert "available_capabilities" not in failed
    assert failed["post_writeback_hooks"]["invoked_count"] == 0
    assert failed["post_writeback_hooks"]["intent_count"] == 0
    assert {
        failure["error_code"]
        for failure in failed["post_writeback_hooks"]["failures"]
    } == {"source_projection_failed"}
    committed_state = Path(str(failed["state_file"])).read_bytes()
    completion_receipt_id = failed["completion_receipt_id"]

    replay = run_todo_completion_via_cli(registry_path, runtime)

    assert replay["changed"] is False
    assert replay["completion_receipt_id"] == completion_receipt_id
    assert Path(str(replay["state_file"])).read_bytes() == committed_state
    assert replay["post_writeback_hooks"]["intent_count"] == 0


def test_todo_complete_isolates_admission_codec_failure_and_recovers_on_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "repo" / "registry.json"
    original_registry = b""

    class CorruptingAdmissionFactory:
        @staticmethod
        def for_plan(**kwargs: object) -> FirstPartyHostGoalAdmission:
            nonlocal original_registry
            requested_registry = kwargs["registry_path"]
            assert isinstance(requested_registry, Path)
            original_registry = requested_registry.read_bytes()
            requested_registry.write_bytes(b"{")
            return FirstPartyHostGoalAdmission.for_plan(**kwargs)

    monkeypatch.setattr(
        todo_command,
        "FirstPartyHostGoalAdmission",
        CorruptingAdmissionFactory,
    )

    failed, _registry, runtime = complete_todo_via_cli(
        tmp_path,
        journal_capabilities=["network"],
        write_state=_write_stage_state,
    )

    assert original_registry
    assert failed["changed"] is True
    assert "available_capabilities" not in failed
    assert failed["post_writeback_hooks"]["invoked_count"] == 0
    assert failed["post_writeback_hooks"]["intent_count"] == 0
    assert {
        failure["error_code"]
        for failure in failed["post_writeback_hooks"]["failures"]
    } == {"source_projection_failed"}
    assert "Expecting property name" not in str(failed)
    committed_state = Path(str(failed["state_file"])).read_bytes()
    completion_receipt_id = failed["completion_receipt_id"]

    registry_path.write_bytes(original_registry)
    monkeypatch.setattr(
        todo_command,
        "FirstPartyHostGoalAdmission",
        FirstPartyHostGoalAdmission,
    )
    replay = run_todo_completion_via_cli(registry_path, runtime)

    assert replay["changed"] is False
    assert replay["completion_receipt_id"] == completion_receipt_id
    assert Path(str(replay["state_file"])).read_bytes() == committed_state
    assert replay["post_writeback_hooks"]["intent_count"] == 0


def test_todo_complete_isolates_lifetime_transport_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableAdmission:
        @contextmanager
        def current_lifetime(self, *, operation: str):
            del operation
            raise RuntimeError("private transport detail")
            yield

    _use_admission(monkeypatch, UnavailableAdmission())

    captured, _registry, _runtime = complete_todo_via_cli(
        tmp_path,
        journal_capabilities=["network"],
        write_state=_write_stage_state,
    )

    assert captured["changed"] is True
    assert "available_capabilities" not in captured
    assert captured["post_writeback_hooks"]["invoked_count"] == 0
    assert captured["post_writeback_hooks"]["intent_count"] == 0
    assert "private transport detail" not in str(captured)
