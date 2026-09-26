from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from loopx.attached_session import (
    bind_attached_agent_session,
    claim_attached_agent_turn,
    complete_attached_agent_turn,
    select_current_attached_session,
)
from loopx.chat_agent import CodexChatAgentError
from loopx.cli import main
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.goals.source_session_recreation import (
    RecreateGoalRequest,
    recreate_goal_instance,
)
from loopx.control_plane.goals.source_session_registration import (
    FreshSourceSessionRegistration,
    register_fresh_source_session_project,
)
from loopx.control_plane.projects.registry_codec import load_project_registry


GOAL_ID = "release"
AGENT_ID = "release-worker"
HOST_SURFACE = "codex-app-ssh"
HOST_SESSION_ID = "host-thread"


def _register(tmp_path: Path) -> tuple[Path, Path, ChatSessionStore, str]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    registry_path = project_root / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    result = register_fresh_source_session_project(
        FreshSourceSessionRegistration(
            registry_path=registry_path,
            runtime_root=runtime_root,
            operation_id="register-release",
            project_id="project",
            goal_id=GOAL_ID,
            objective="Ship the release.",
            non_goals=[],
            acceptance=["The release is verified."],
            unknowns=[],
            next_effect="Inspect the release.",
            stop_condition="Stop when authority is absent.",
            project_record={
                "id": "project",
                "kind": "work",
                "path": str(project_root),
            },
            goal_record={
                "id": GOAL_ID,
                "project_id": "project",
                "title": "Release",
                "status": "active",
                "repo": str(project_root),
                "coordination": {
                    "registered_agents": [AGENT_ID],
                    "thread_agent_bindings": [
                        {
                            "host_surface": HOST_SURFACE,
                            "thread_id": HOST_SESSION_ID,
                            "agent_id": AGENT_ID,
                        }
                    ],
                },
            },
            state_file=project_root / "GOAL.md",
        )
    )
    return (
        registry_path,
        runtime_root,
        ChatSessionStore(runtime_root),
        str(result["goal_ref"]["goal_instance_id"]),
    )


def _bind(
    store: ChatSessionStore,
    registry_path: Path,
) -> dict[str, object]:
    return bind_attached_agent_session(
        store=store,
        registry=load_project_registry(registry_path),
        registry_path=registry_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        host_surface=HOST_SURFACE,
        host_session_id=HOST_SESSION_ID,
        executor_endpoint_id="codex",
        execute=True,
    )


def _recreate(registry_path: Path, instance_id: str) -> str:
    result = recreate_goal_instance(
        RecreateGoalRequest(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            goal_instance_id=instance_id,
            operation_id=f"recreate-{instance_id}",
        )
    )
    return str(result["goal_ref"]["goal_instance_id"])


def test_recreated_goal_cannot_reuse_or_claim_from_attached_session(
    tmp_path: Path,
) -> None:
    registry_path, _runtime_root, store, instance_a = _register(tmp_path)
    bound_a = _bind(store, registry_path)
    session_a = str(bound_a["session"]["session_id"])  # type: ignore[index]
    persisted_a = store.load_session(session_a)
    assert persisted_a is not None
    assert persisted_a["goal_instance_id"] == instance_a
    assert "goal_instance_id" not in bound_a["session"]  # type: ignore[operator]

    runtime = ChatRuntimeController(
        store=store,
        codex_bin="missing-codex",
        registry_path=registry_path,
    )
    first, created = runtime.submit_turn(
        session_id=session_a,
        client_turn_id="first",
        message="first message",
        work_dir=tmp_path,
        objective="Ship the release.",
    )
    second, queued = runtime.enqueue_turn(
        session_id=session_a,
        client_turn_id="second",
        message="second message",
        work_dir=tmp_path,
        objective="Ship the release.",
    )
    assert created and queued
    assert first["goal_instance_id"] == instance_a
    assert second["goal_instance_id"] == instance_a

    claimed = claim_attached_agent_turn(
        store=store,
        registry_path=registry_path,
        session_id=session_a,
        host_surface=HOST_SURFACE,
        host_session_id=HOST_SESSION_ID,
        claim_id="claim-a",
    )
    assert claimed["turn"]["turn_id"] == first["turn_id"]
    admitted = store.load_turn(session_a, str(first["turn_id"]))
    assert admitted is not None
    assert admitted["admitted_goal_instance_id"] == instance_a

    instance_b = _recreate(registry_path, instance_a)
    assert instance_b != instance_a
    bound_b = _bind(store, registry_path)
    session_b = str(bound_b["session"]["session_id"])  # type: ignore[index]
    assert session_b != session_a
    persisted_b = store.load_session(session_b)
    assert persisted_b is not None
    assert persisted_b["goal_instance_id"] == instance_b
    before_b = persisted_b.copy()

    replay = claim_attached_agent_turn(
        store=store,
        registry_path=registry_path,
        session_id=session_a,
        host_surface=HOST_SURFACE,
        host_session_id=HOST_SESSION_ID,
        claim_id="claim-a",
    )
    assert replay["turn"]["turn_id"] == first["turn_id"]

    completed = complete_attached_agent_turn(
        store=store,
        registry_path=registry_path,
        session_id=session_a,
        turn_id=str(first["turn_id"]),
        host_surface=HOST_SURFACE,
        host_session_id=HOST_SESSION_ID,
        claim_id="claim-a",
        completion_id="completion-a",
        response={"message": "historical A result"},
    )
    assert completed["created"] is True
    assert store.load_session(session_b) == before_b
    assert store.load_turn(session_a, str(first["turn_id"]))["status"] == "completed"  # type: ignore[index]

    with pytest.raises(ValueError, match="stale_goal_instance"):
        claim_attached_agent_turn(
            store=store,
            registry_path=registry_path,
            session_id=session_a,
            host_surface=HOST_SURFACE,
            host_session_id=HOST_SESSION_ID,
            claim_id="claim-second",
        )
    with pytest.raises(ValueError, match="stale_goal_instance"):
        runtime.resume_session(
            session_id=session_a,
            work_dir=tmp_path,
            objective="Ship the release.",
        )
    assert store.load_turn(session_a, str(second["turn_id"]))["status"] == "queued"  # type: ignore[index]


def test_stale_attached_enqueue_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    registry_path, _runtime_root, store, instance_a = _register(tmp_path)
    session_a = str(_bind(store, registry_path)["session"]["session_id"])  # type: ignore[index]
    _recreate(registry_path, instance_a)
    runtime = ChatRuntimeController(
        store=store,
        codex_bin="missing-codex",
        registry_path=registry_path,
    )
    before = store.load_session(session_a)

    with pytest.raises(ValueError, match="stale_goal_instance"):
        runtime.submit_turn(
            session_id=session_a,
            client_turn_id="late",
            message="must not enter A",
            work_dir=tmp_path,
            objective="Ship the release.",
        )

    assert store.load_session(session_a) == before
    assert store.turn_for_client(session_a, "late") is None


def test_claim_wait_does_not_hold_goal_lifetime_guard(tmp_path: Path) -> None:
    registry_path, _runtime_root, store, instance_a = _register(tmp_path)
    session_a = str(_bind(store, registry_path)["session"]["session_id"])  # type: ignore[index]
    finished = threading.Event()
    errors: list[BaseException] = []

    def wait_for_claim() -> None:
        try:
            claim_attached_agent_turn(
                store=store,
                registry_path=registry_path,
                session_id=session_a,
                host_surface=HOST_SURFACE,
                host_session_id=HOST_SESSION_ID,
                claim_id="waiting-claim",
                wait_seconds=0.5,
            )
        except ValueError as exc:
            if "stale_goal_instance" not in str(exc):
                errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=wait_for_claim)
    thread.start()
    time.sleep(0.1)
    started = time.monotonic()
    _recreate(registry_path, instance_a)
    elapsed = time.monotonic() - started
    thread.join(timeout=2)

    assert elapsed < 0.4
    assert finished.is_set()
    assert errors == []


def test_resume_writeback_holds_goal_lifetime_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, _runtime_root, store, instance_a = _register(tmp_path)
    session_a = str(_bind(store, registry_path)["session"]["session_id"])  # type: ignore[index]
    runtime = ChatRuntimeController(
        store=store,
        codex_bin="missing-codex",
        registry_path=registry_path,
    )
    update_entered = threading.Event()
    release_update = threading.Event()
    recreation_finished = threading.Event()
    resumed: list[dict[str, object]] = []
    recreated: list[str] = []
    errors: list[BaseException] = []
    original_update = store.update_session

    def blocked_update(session_id: str, **changes: object) -> dict[str, object]:
        update_entered.set()
        if not release_update.wait(timeout=2):
            raise TimeoutError("resume writeback was not released")
        return original_update(session_id, **changes)

    monkeypatch.setattr(store, "update_session", blocked_update)

    def resume() -> None:
        try:
            resumed.append(
                runtime.resume_session(
                    session_id=session_a,
                    work_dir=tmp_path,
                    objective="Ship the release.",
                )
            )
        except BaseException as exc:
            errors.append(exc)

    def recreate() -> None:
        try:
            recreated.append(_recreate(registry_path, instance_a))
        except BaseException as exc:
            errors.append(exc)
        finally:
            recreation_finished.set()

    resume_thread = threading.Thread(target=resume)
    recreate_thread = threading.Thread(target=recreate)
    resume_thread.start()
    assert update_entered.wait(timeout=2)
    recreate_thread.start()
    try:
        assert not recreation_finished.wait(timeout=0.1)
    finally:
        release_update.set()
    resume_thread.join(timeout=2)
    recreate_thread.join(timeout=2)

    assert not resume_thread.is_alive()
    assert not recreate_thread.is_alive()
    assert errors == []
    assert resumed[0]["session_id"] == session_a
    assert recreated[0] != instance_a


def test_strict_managed_open_fails_before_provider_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, _runtime_root, store, _instance_a = _register(tmp_path)
    runtime = ChatRuntimeController(
        store=store,
        codex_bin="missing-codex",
        registry_path=registry_path,
    )
    monkeypatch.setattr(
        runtime,
        "_start_adapter",
        lambda **_kwargs: pytest.fail("provider start must remain fenced"),
    )

    with pytest.raises(CodexChatAgentError) as raised:
        runtime.open_session(
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            work_dir=tmp_path,
            objective="Ship the release.",
            mode="new",
        )

    assert raised.value.error_code == "source_session_managed_chat_unsupported"


def test_strict_session_does_not_fall_back_when_registry_disappears(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, _runtime_root, store, _instance_a = _register(tmp_path)
    _bind(store, registry_path)
    registry_path.unlink()
    runtime = ChatRuntimeController(
        store=store,
        codex_bin="missing-codex",
        registry_path=registry_path,
    )
    monkeypatch.setattr(
        runtime,
        "_start_adapter",
        lambda **_kwargs: pytest.fail("provider start must remain fenced"),
    )

    with pytest.raises(FileNotFoundError):
        runtime.open_session(
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            work_dir=tmp_path,
            objective="Ship the release.",
            mode="resume_latest",
        )


def test_missing_registry_without_exact_history_preserves_legacy_selection(
    tmp_path: Path,
) -> None:
    selected, strict = select_current_attached_session(
        store=ChatSessionStore(tmp_path / "runtime"),
        registry_path=tmp_path / "missing-registry.json",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        channel_id=f"goal.{GOAL_ID}",
    )

    assert selected is None
    assert strict is False


def test_worker_bridge_cli_binds_source_session_without_identity_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry_path, runtime_root, _store, instance_a = _register(tmp_path)

    assert (
        main(
            [
                "--registry",
                str(registry_path),
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "worker-bridge",
                "attached-session-bind",
                "--goal-id",
                GOAL_ID,
                "--agent-id",
                AGENT_ID,
                "--host-surface",
                HOST_SURFACE,
                "--host-session-id",
                HOST_SESSION_ID,
                "--executor-endpoint-id",
                "codex",
                "--execute",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    session_id = str(payload["session"]["session_id"])
    persisted = ChatSessionStore(runtime_root).load_session(session_id)
    assert persisted is not None
    assert persisted["goal_instance_id"] == instance_a
    assert "goal_instance_id" not in payload["session"]


def test_legacy_writers_omit_goal_instance_fields(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="legacy",
        agent_id="worker",
        adapter_kind="attached_host_session",
        upstream_thread_id="host",
        session_mode="attached_host",
        host_surface="host",
    )
    turn, _created = store.create_queued_turn(
        str(session["session_id"]),
        client_turn_id="legacy-turn",
        message="legacy",
    )

    assert "goal_instance_id" not in session
    assert "goal_instance_id" not in turn
    assert "admitted_goal_instance_id" not in turn
