from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest

from loopx.cli import main
from loopx.claude_goal_mode.scripts import connect as claude_connect
from loopx.control_plane.goals import (
    source_session_recreation,
    source_session_registration,
)
from loopx.control_plane.projects import registry_codec


def _registration_arguments(registry_path: Path, knowledge_root: Path) -> list[str]:
    return [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "register",
        "--goal-instance-profile",
        "source_session_v1",
        "--operation-id",
        "create-atlas-import",
        "--project-id",
        "atlas",
        "--project-kind",
        "work",
        "--knowledge-root",
        str(knowledge_root),
        "--goal-id",
        "atlas-import",
        "--objective",
        "Deliver the Atlas import pipeline.",
        "--acceptance",
        "A verified import report is produced.",
        "--next-effect",
        "Inspect Atlas.",
        "--stop-condition",
        "Stop while Atlas is unavailable.",
    ]


def _binding_arguments(
    registry_path: Path,
    *,
    operation: str,
    goal_instance_id: str,
    operation_id: str,
    session_id: str = "session-a",
) -> list[str]:
    return [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        f"{operation}-session",
        "--session-id",
        session_id,
        "--goal-id",
        "atlas-import",
        "--goal-instance-id",
        goal_instance_id,
        "--operation-id",
        operation_id,
    ]


def _recreation_arguments(
    registry_path: Path,
    *,
    goal_instance_id: str,
    operation_id: str = "recreate-atlas-import",
) -> list[str]:
    return [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "recreate-goal",
        "--goal-id",
        "atlas-import",
        "--goal-instance-id",
        goal_instance_id,
        "--operation-id",
        operation_id,
        "--execute",
    ]


def _register(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> tuple[Path, Path, dict[str, object]]:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    assert main(_registration_arguments(registry_path, knowledge_root)) == 0
    return knowledge_root, registry_path, json.loads(capsys.readouterr().out)


def _registry_payload(registry_path: Path) -> dict[str, object]:
    return json.loads(registry_path.read_text(encoding="utf-8"))[1]


def test_registration_publishes_fresh_v2_without_global_sync(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    runtime_root = tmp_path / "runtime"
    global_registry = runtime_root / "registry.global.json"
    global_registry.parent.mkdir(parents=True)
    global_registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": []}),
        encoding="utf-8",
    )
    global_before = global_registry.read_bytes()
    arguments = _registration_arguments(registry_path, knowledge_root)
    arguments[4:4] = ["--runtime-root", str(runtime_root)]

    assert main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)
    envelope = json.loads(registry_path.read_text(encoding="utf-8"))
    registry = envelope[1]
    goal = registry["goals"][0]

    assert payload["changed"] is True
    assert payload["replayed"] is False
    assert payload["execution_authority"] is False
    assert payload["goal_ref"] == {
        "goal_id": "atlas-import",
        "goal_instance_id": goal["goal_instance_id"],
    }
    assert envelope[0]["schema_version"] == "loopx_project_registry_envelope_v2"
    assert envelope[0]["minimum_writer_protocol"] == "goal_instance_v2"
    assert registry["profile_id"] == "source_session_v1"
    assert re.fullmatch(r"ginst_[0-9a-f]{32}", goal["goal_instance_id"])
    assert goal["status"] == "active"
    assert registry["session_bindings"] == []
    assert registry["session_receipts"] == []
    assert registry["lifetime_receipts"] == [
        {
            "schema_version": "loopx_goal_creation_receipt_v1",
            "operation_id": "create-atlas-import",
            "request_digest": payload["request_digest"],
            "goal_ref": payload["goal_ref"],
            "created_at": registry["updated_at"],
        }
    ]
    assert payload["receipt"] == registry["lifetime_receipts"][0]
    assert global_registry.read_bytes() == global_before


def test_registration_rejects_goal_id_outside_exact_reference_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    arguments = _registration_arguments(registry_path, knowledge_root)
    arguments[arguments.index("--goal-id") + 1] = "g" * 201

    assert main(arguments) == 1
    rejection = json.loads(capsys.readouterr().out)

    assert "source-session goal_id" in rejection["error"]
    assert not registry_path.exists()


def test_registration_persists_an_absolute_runtime_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    arguments = _registration_arguments(registry_path, knowledge_root)
    arguments[4:4] = ["--runtime-root", "runtime"]

    assert main(arguments) == 0
    capsys.readouterr()

    assert _registry_payload(registry_path)["common_runtime_root"] == str(
        (tmp_path / "runtime").resolve()
    )


def test_registration_reuses_reserved_instance_after_interruption(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    arguments = _registration_arguments(registry_path, knowledge_root)
    original_commit = registry_codec.ProjectRegistryTransaction.commit

    def interrupt_before_publication(
        _transaction: registry_codec.ProjectRegistryTransaction,
        _payload: dict[str, object],
    ) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        interrupt_before_publication,
    )
    with pytest.raises(KeyboardInterrupt):
        main(arguments)

    journals = list(
        (registry_path.parent / ".loopx" / "lifecycle" / "goal-instance").glob(
            "journals/*/*.json"
        )
    )
    assert len(journals) == 1
    reserved = json.loads(journals[0].read_text(encoding="utf-8"))["goal_ref"]
    assert state_file.exists()
    assert not registry_path.exists()

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        original_commit,
    )
    assert main(arguments) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["goal_ref"] == reserved
    before_replay = registry_path.read_bytes()

    assert main(arguments) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["changed"] is False
    assert replay["goal_ref"] == reserved
    assert registry_path.read_bytes() == before_replay

    conflicting = list(arguments)
    conflicting[conflicting.index("--operation-id") + 1] = "create-other"
    assert main(conflicting) == 1
    conflict = json.loads(capsys.readouterr().out)
    assert "requires an absent registry" in conflict["error"]
    assert registry_path.read_bytes() == before_replay


def test_registration_replays_original_receipt_after_goal_recreation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, registry_path, registration = _register(tmp_path, capsys)
    instance_a = str(registration["goal_ref"]["goal_instance_id"])
    assert main(
        _recreation_arguments(
            registry_path,
            goal_instance_id=instance_a,
        )
    ) == 0
    recreated = json.loads(capsys.readouterr().out)
    assert recreated["goal_ref"]["goal_instance_id"] != instance_a
    registry_before_replay = registry_path.read_bytes()
    state_file = Path(str(registration["state_file"]))
    state_before_replay = state_file.read_bytes()
    journal = next(
        (
            registry_path.parent / ".loopx" / "lifecycle" / "goal-instance" / "journals"
        ).glob("*/*.json")
    )
    journal_before_replay = journal.read_bytes()

    def reject_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("completed registration replay must not write")

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        reject_write,
    )
    monkeypatch.setattr(source_session_registration, "write_journal", reject_write)
    monkeypatch.setattr(
        source_session_registration,
        "atomic_write_state_text",
        reject_write,
    )
    assert main(_registration_arguments(registry_path, tmp_path / "atlas")) == 0
    replay = json.loads(capsys.readouterr().out)

    assert replay["changed"] is False
    assert replay["replayed"] is True
    assert replay["goal_ref"] == registration["goal_ref"]
    assert replay["goal"] == registration["goal"]
    assert replay["receipt"] == registration["receipt"]
    assert registry_path.read_bytes() == registry_before_replay
    assert state_file.read_bytes() == state_before_replay
    assert journal.read_bytes() == journal_before_replay


def test_registration_replay_preserves_later_goal_state_progress(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root, registry_path, registration = _register(tmp_path, capsys)
    state_file = Path(str(registration["state_file"]))
    state_file.write_text(
        f"{state_file.read_text(encoding='utf-8')}\nProgress: imported 10 records.\n",
        encoding="utf-8",
    )
    registry_before_replay = registry_path.read_bytes()
    state_before_replay = state_file.read_bytes()
    journal = next(
        (
            registry_path.parent / ".loopx" / "lifecycle" / "goal-instance" / "journals"
        ).glob("*/*.json")
    )
    journal_before_replay = journal.read_bytes()

    def reject_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("completed registration replay must not write")

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        reject_write,
    )
    monkeypatch.setattr(source_session_registration, "write_journal", reject_write)
    monkeypatch.setattr(
        source_session_registration,
        "atomic_write_state_text",
        reject_write,
    )
    assert main(_registration_arguments(registry_path, knowledge_root)) == 0
    replay = json.loads(capsys.readouterr().out)

    assert replay["changed"] is False
    assert replay["replayed"] is True
    assert replay["goal_ref"] == registration["goal_ref"]
    assert replay["goal"] == registration["goal"]
    assert replay["receipt"] == registration["receipt"]
    assert registry_path.read_bytes() == registry_before_replay
    assert state_file.read_bytes() == state_before_replay
    assert journal.read_bytes() == journal_before_replay


def test_registration_recovers_same_instance_after_process_kill(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    ready = tmp_path / "registration-ready"
    resume = tmp_path / "registration-resume"
    arguments = _registration_arguments(registry_path, knowledge_root)
    script = """
import json
import os
import time
from pathlib import Path

from loopx.cli import main
from loopx.control_plane.projects import registry_codec

ready = Path(os.environ["LOOPX_TEST_READY"])
resume = Path(os.environ["LOOPX_TEST_RESUME"])
real_commit = registry_codec.ProjectRegistryTransaction.commit

def paused_commit(transaction, payload):
    ready.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 10
    while not resume.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test registration pause timed out")
        time.sleep(0.01)
    return real_commit(transaction, payload)

registry_codec.ProjectRegistryTransaction.commit = paused_commit
raise SystemExit(main(json.loads(os.environ["LOOPX_TEST_ARGS"])))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "LOOPX_TEST_READY": str(ready),
            "LOOPX_TEST_RESUME": str(resume),
            "LOOPX_TEST_ARGS": json.dumps(arguments),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            raise TimeoutError("registration process did not reach the barrier")
        time.sleep(0.01)
    assert ready.exists()
    journals = list(
        (registry_path.parent / ".loopx" / "lifecycle" / "goal-instance").glob(
            "journals/*/*.json"
        )
    )
    assert len(journals) == 1
    reserved = json.loads(journals[0].read_text(encoding="utf-8"))["goal_ref"]
    assert state_file.exists()
    assert not registry_path.exists()

    process.kill()
    process.communicate(timeout=10)
    assert process.returncode != 0

    assert main(arguments) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["goal_ref"] == reserved
    assert _registry_payload(registry_path)["goals"][0]["goal_instance_id"] == (
        reserved["goal_instance_id"]
    )


def test_registration_rejects_state_without_its_reservation_journal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    arguments = _registration_arguments(registry_path, knowledge_root)
    monkeypatch.setattr(
        source_session_registration,
        "now_local_iso",
        lambda: "2026-09-23T12:00:00+00:00",
    )
    original_commit = registry_codec.ProjectRegistryTransaction.commit

    def interrupt_before_publication(
        _transaction: registry_codec.ProjectRegistryTransaction,
        _payload: dict[str, object],
    ) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        interrupt_before_publication,
    )
    with pytest.raises(KeyboardInterrupt):
        main(arguments)

    journals = list(
        (registry_path.parent / ".loopx" / "lifecycle" / "goal-instance").glob(
            "journals/*/*.json"
        )
    )
    assert len(journals) == 1
    assert state_file.exists()
    journals[0].unlink()
    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        original_commit,
    )

    assert main(arguments) == 1
    rejection = json.loads(capsys.readouterr().out)
    assert "reservation journal" in rejection["error"]
    assert not registry_path.exists()
    assert not list(
        (registry_path.parent / ".loopx" / "lifecycle" / "goal-instance").glob(
            "journals/*/*.json"
        )
    )


def test_registration_rejects_a_competing_reserved_operation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    state_file = (
        knowledge_root / ".loopx" / "goals" / "atlas-import" / "ACTIVE_GOAL_STATE.md"
    )
    arguments = _registration_arguments(registry_path, knowledge_root)
    original_commit = registry_codec.ProjectRegistryTransaction.commit

    def interrupt_before_publication(
        _transaction: registry_codec.ProjectRegistryTransaction,
        _payload: dict[str, object],
    ) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        interrupt_before_publication,
    )
    with pytest.raises(KeyboardInterrupt):
        main(arguments)

    state_file.unlink()
    monkeypatch.setattr(
        registry_codec.ProjectRegistryTransaction,
        "commit",
        original_commit,
    )
    competing = list(arguments)
    competing[competing.index("--operation-id") + 1] = "competing-create"

    assert main(competing) == 1
    rejection = json.loads(capsys.readouterr().out)
    assert "reservation journal" in rejection["error"]
    assert not registry_path.exists()
    assert len(
        list(
            (registry_path.parent / ".loopx" / "lifecycle" / "goal-instance").glob(
                "journals/*/*.json"
            )
        )
    ) == 1


def test_bind_and_unbind_commit_exact_receipts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, registry_path, registration = _register(tmp_path, capsys)
    goal_instance_id = str(registration["goal_ref"]["goal_instance_id"])
    bind_arguments = _binding_arguments(
        registry_path,
        operation="bind",
        goal_instance_id=goal_instance_id,
        operation_id="bind-session-a",
    )

    assert main(bind_arguments) == 0
    bound = json.loads(capsys.readouterr().out)
    registry = _registry_payload(registry_path)
    assert bound["changed"] is True
    assert bound["replayed"] is False
    assert registry["session_bindings"] == [
        {
            "session_id": "session-a",
            "foreground_goal_ref": registration["goal_ref"],
        }
    ]
    assert registry["session_receipts"] == [bound["receipt"]]

    before_replay = registry_path.read_bytes()
    assert main(bind_arguments) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["replayed"] is True
    assert replay["receipt"] == bound["receipt"]
    assert registry_path.read_bytes() == before_replay

    unbind_arguments = _binding_arguments(
        registry_path,
        operation="unbind",
        goal_instance_id=goal_instance_id,
        operation_id="unbind-session-a",
    )
    assert main(unbind_arguments) == 0
    unbound = json.loads(capsys.readouterr().out)
    registry = _registry_payload(registry_path)
    assert unbound["changed"] is True
    assert unbound["replayed"] is False
    assert registry["session_bindings"] == []
    assert registry["session_receipts"] == [
        bound["receipt"],
        unbound["receipt"],
    ]


def test_recreation_retires_bindings_and_fences_stale_bind(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, registry_path, registration = _register(tmp_path, capsys)
    instance_a = str(registration["goal_ref"]["goal_instance_id"])
    bind_arguments = _binding_arguments(
        registry_path,
        operation="bind",
        goal_instance_id=instance_a,
        operation_id="bind-session-a",
    )
    assert main(bind_arguments) == 0
    capsys.readouterr()
    recreate_arguments = _recreation_arguments(
        registry_path,
        goal_instance_id=instance_a,
    )

    assert main(recreate_arguments) == 0
    recreated = json.loads(capsys.readouterr().out)
    instance_b = recreated["goal_ref"]["goal_instance_id"]
    registry = _registry_payload(registry_path)
    assert instance_b != instance_a
    assert registry["goals"][0]["goal_instance_id"] == instance_b
    assert registry["session_bindings"] == []
    assert registry["retired_goal_instances"] == [
        {
            "goal_ref": {
                "goal_id": "atlas-import",
                "goal_instance_id": instance_a,
            },
            "successor_goal_ref": recreated["goal_ref"],
            "operation_id": "recreate-atlas-import",
            "retired_at": recreated["receipt"]["committed_at"],
        }
    ]
    assert registry["lifetime_receipts"][-1] == recreated["receipt"]
    assert registry["session_receipts"][-1]["operation"] == "retire_bindings"
    assert registry["session_receipts"][-1]["session_ids"] == ["session-a"]

    before_replay = registry_path.read_bytes()
    assert main(recreate_arguments) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["replayed"] is True
    assert replay["goal_ref"]["goal_instance_id"] == instance_b
    assert registry_path.read_bytes() == before_replay

    competing_recreation = _recreation_arguments(
        registry_path,
        goal_instance_id=instance_a,
        operation_id="competing-recreation",
    )
    assert main(competing_recreation) == 1
    rejection = json.loads(capsys.readouterr().out)
    assert "stale_goal_instance" in rejection["error"]
    assert registry_path.read_bytes() == before_replay
    recreation_journals = list(
        (
            registry_path.parent
            / ".loopx"
            / "lifecycle"
            / "goal-instance"
            / "recreations"
        ).glob("*/*.json")
    )
    assert len(recreation_journals) == 1

    stale_bind = list(bind_arguments)
    stale_bind[stale_bind.index("--operation-id") + 1] = "bind-after-recreate"
    assert main(stale_bind) == 1
    stale_bind_rejection = json.loads(capsys.readouterr().out)
    assert "stale_goal_instance" in stale_bind_rejection["error"]
    assert registry_path.read_bytes() == before_replay


def test_paused_bind_cannot_cross_recreation_aba(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, registry_path, registration = _register(tmp_path, capsys)
    instance_a = str(registration["goal_ref"]["goal_instance_id"])
    ready = tmp_path / "bind-ready"
    resume = tmp_path / "bind-resume"
    bind_arguments = _binding_arguments(
        registry_path,
        operation="bind",
        goal_instance_id=instance_a,
        operation_id="paused-bind-a",
        session_id="paused-session",
    )
    script = """
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from loopx.cli import main
from loopx.control_plane.goals import source_session_binding

ready = Path(os.environ["LOOPX_TEST_READY"])
resume = Path(os.environ["LOOPX_TEST_RESUME"])
real_lock = source_session_binding.exclusive_cross_runtime_file_lock

@contextmanager
def paused_lock(path, **kwargs):
    if kwargs.get("operation") == "source_session_goal_lifetime":
        ready.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 10
        while not resume.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("test bind pause timed out")
            time.sleep(0.01)
    with real_lock(path, **kwargs):
        yield

source_session_binding.exclusive_cross_runtime_file_lock = paused_lock
raise SystemExit(main(json.loads(os.environ["LOOPX_TEST_ARGS"])))
"""
    environment = {
        **os.environ,
        "LOOPX_TEST_READY": str(ready),
        "LOOPX_TEST_RESUME": str(resume),
        "LOOPX_TEST_ARGS": json.dumps(bind_arguments),
    }
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            raise TimeoutError("paused bind process did not reach the barrier")
        time.sleep(0.01)
    assert ready.exists()

    assert (
        main(
            _recreation_arguments(
                registry_path,
                goal_instance_id=instance_a,
                operation_id="recreate-before-bind",
            )
        )
        == 0
    )
    recreated = json.loads(capsys.readouterr().out)
    resume.write_text("resume", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=15)
    stale = json.loads(stdout)
    registry = _registry_payload(registry_path)

    assert process.returncode == 1, stderr
    assert "stale_goal_instance" in stale["error"]
    assert (
        registry["goals"][0]["goal_instance_id"]
        == (recreated["goal_ref"]["goal_instance_id"])
    )
    assert registry["session_bindings"] == []
    assert not any(
        receipt.get("operation_id") == "paused-bind-a"
        for receipt in registry["session_receipts"]
    )


def test_recreation_waits_for_an_admitted_bind_commit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, registry_path, registration = _register(tmp_path, capsys)
    instance_a = str(registration["goal_ref"]["goal_instance_id"])
    ready = tmp_path / "guard-held"
    resume = tmp_path / "release-guard"
    bind_arguments = _binding_arguments(
        registry_path,
        operation="bind",
        goal_instance_id=instance_a,
        operation_id="bind-before-recreate",
        session_id="serialized-session",
    )
    script = """
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from loopx.cli import main
from loopx.control_plane.goals import source_session_binding

ready = Path(os.environ["LOOPX_TEST_READY"])
resume = Path(os.environ["LOOPX_TEST_RESUME"])
real_lock = source_session_binding.exclusive_cross_runtime_file_lock

@contextmanager
def paused_lock(path, **kwargs):
    with real_lock(path, **kwargs):
        if kwargs.get("operation") == "source_session_goal_lifetime":
            ready.write_text("ready", encoding="utf-8")
            deadline = time.monotonic() + 10
            while not resume.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("test lifetime guard pause timed out")
                time.sleep(0.01)
        yield

source_session_binding.exclusive_cross_runtime_file_lock = paused_lock
raise SystemExit(main(json.loads(os.environ["LOOPX_TEST_ARGS"])))
"""
    bind_process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "LOOPX_TEST_READY": str(ready),
            "LOOPX_TEST_RESUME": str(resume),
            "LOOPX_TEST_ARGS": json.dumps(bind_arguments),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and bind_process.poll() is None:
        if time.monotonic() >= deadline:
            bind_process.kill()
            raise TimeoutError("bind process did not acquire the lifetime guard")
        time.sleep(0.01)
    assert ready.exists()

    recreate_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            *_recreation_arguments(
                registry_path,
                goal_instance_id=instance_a,
                operation_id="recreate-after-bind",
            ),
        ],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.2)
    assert recreate_process.poll() is None
    assert _registry_payload(registry_path)["goals"][0]["goal_instance_id"] == instance_a

    resume.write_text("resume", encoding="utf-8")
    bind_stdout, bind_stderr = bind_process.communicate(timeout=15)
    recreate_stdout, recreate_stderr = recreate_process.communicate(timeout=15)
    assert bind_process.returncode == 0, bind_stderr
    assert recreate_process.returncode == 0, recreate_stderr
    bound = json.loads(bind_stdout)
    recreated = json.loads(recreate_stdout)
    registry = _registry_payload(registry_path)

    assert bound["changed"] is True
    assert recreated["retired_session_ids"] == ["serialized-session"]
    assert registry["session_bindings"] == []
    assert registry["goals"][0]["goal_instance_id"] == (
        recreated["goal_ref"]["goal_instance_id"]
    )


def test_resolve_classifies_current_and_retired_exact_refs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, registry_path, registration = _register(tmp_path, capsys)
    instance_a = str(registration["goal_ref"]["goal_instance_id"])
    assert (
        main(
            _binding_arguments(
                registry_path,
                operation="bind",
                goal_instance_id=instance_a,
                operation_id="bind-session-a",
            )
        )
        == 0
    )
    capsys.readouterr()
    resolve_arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "resolve",
        "--session-id",
        "session-a",
        "--goal-id",
        "atlas-import",
        "--goal-instance-id",
        instance_a,
    ]

    assert main(resolve_arguments) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["resolution"] == "current"
    assert current["goal_ref"] == registration["goal_ref"]
    assert current["execution_authority"] is False

    assert (
        main(
            _recreation_arguments(
                registry_path,
                goal_instance_id=instance_a,
            )
        )
        == 0
    )
    capsys.readouterr()
    retired_arguments = [
        argument
        for argument in resolve_arguments
        if argument not in {"--session-id", "session-a"}
    ]
    assert main(retired_arguments) == 1
    retired = json.loads(capsys.readouterr().out)
    assert retired["resolution"] == "retired"
    assert retired["goal_ref"] == registration["goal_ref"]
    assert retired["execution_authority"] is False


def test_registration_repairs_reserved_journal_after_v2_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    arguments = _registration_arguments(registry_path, knowledge_root)
    real_write_journal = source_session_registration.write_journal
    writes = 0

    def fail_published_journal(
        path: Path,
        payload: dict[str, object],
    ) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected registration response failure")
        real_write_journal(path, payload)

    monkeypatch.setattr(
        source_session_registration,
        "write_journal",
        fail_published_journal,
    )

    assert main(arguments) == 1
    failed = json.loads(capsys.readouterr().out)
    committed_registry = json.loads(registry_path.read_text(encoding="utf-8"))[1]
    instance_a = committed_registry["goals"][0]["goal_instance_id"]

    assert "injected registration response failure" in failed["error"]
    assert committed_registry["lifetime_receipts"][0]["goal_ref"] == {
        "goal_id": "atlas-import",
        "goal_instance_id": instance_a,
    }

    monkeypatch.setattr(
        source_session_registration,
        "write_journal",
        real_write_journal,
    )
    assert main(arguments) == 0
    repaired = json.loads(capsys.readouterr().out)
    journal = next(
        (
            registry_path.parent / ".loopx" / "lifecycle" / "goal-instance" / "journals"
        ).glob("*/*.json")
    )

    assert repaired["changed"] is False
    assert repaired["goal_ref"]["goal_instance_id"] == instance_a
    assert json.loads(journal.read_text(encoding="utf-8"))["phase"] == "published"


def test_recreation_repairs_reserved_journal_after_b_publication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    assert main(_registration_arguments(registry_path, knowledge_root)) == 0
    registration = json.loads(capsys.readouterr().out)
    instance_a = registration["goal_ref"]["goal_instance_id"]
    arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "recreate-goal",
        "--goal-id",
        "atlas-import",
        "--goal-instance-id",
        instance_a,
        "--operation-id",
        "recreate-atlas-import",
        "--execute",
    ]
    real_write_journal = source_session_recreation.write_journal
    writes = 0

    def fail_published_journal(
        path: Path,
        payload: dict[str, object],
    ) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected recreation response failure")
        real_write_journal(path, payload)

    monkeypatch.setattr(
        source_session_recreation,
        "write_journal",
        fail_published_journal,
    )

    assert main(arguments) == 1
    failed = json.loads(capsys.readouterr().out)
    committed_registry = json.loads(registry_path.read_text(encoding="utf-8"))[1]
    instance_b = committed_registry["goals"][0]["goal_instance_id"]

    assert "injected recreation response failure" in failed["error"]
    assert instance_b != instance_a
    assert committed_registry["lifetime_receipts"][-1]["new_goal_ref"] == {
        "goal_id": "atlas-import",
        "goal_instance_id": instance_b,
    }

    monkeypatch.setattr(
        source_session_recreation,
        "write_journal",
        real_write_journal,
    )
    assert main(arguments) == 0
    repaired = json.loads(capsys.readouterr().out)
    journal = next(
        (
            registry_path.parent
            / ".loopx"
            / "lifecycle"
            / "goal-instance"
            / "recreations"
        ).glob("*/*.json")
    )

    assert repaired["replayed"] is True
    assert repaired["goal_ref"]["goal_instance_id"] == instance_b
    assert json.loads(journal.read_text(encoding="utf-8"))["phase"] == "published"


@pytest.mark.parametrize("operation", ["bind", "unbind"])
def test_session_replacement_failure_restores_exact_v2_bytes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    assert main(_registration_arguments(registry_path, knowledge_root)) == 0
    registration = json.loads(capsys.readouterr().out)
    goal_instance_id = registration["goal_ref"]["goal_instance_id"]
    if operation == "unbind":
        assert (
            main(
                _binding_arguments(
                    registry_path,
                    operation="bind",
                    goal_instance_id=goal_instance_id,
                    operation_id="prepare-session-a",
                )
            )
            == 0
        )
        capsys.readouterr()
    before = registry_path.read_bytes()
    real_read = registry_codec._read_document
    reads = 0

    def fail_first_readback(
        candidate: Path,
    ) -> registry_codec._ProjectRegistryDocument:
        nonlocal reads
        reads += 1
        if reads == 2:
            raise OSError("injected v2 readback failure")
        return real_read(candidate)

    monkeypatch.setattr(registry_codec, "_read_document", fail_first_readback)
    arguments = _binding_arguments(
        registry_path,
        operation=operation,
        goal_instance_id=goal_instance_id,
        operation_id=f"{operation}-session-a",
    )

    assert main(arguments) == 1
    failed = json.loads(capsys.readouterr().out)

    assert "exact preimage restored" in failed["error"]
    assert registry_path.read_bytes() == before

    monkeypatch.setattr(registry_codec, "_read_document", real_read)
    assert main(arguments) == 0
    committed = json.loads(capsys.readouterr().out)
    assert committed["changed"] is True


def test_session_receipt_capacity_preserves_exact_replay_and_rejects_new_work(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    assert main(_registration_arguments(registry_path, knowledge_root)) == 0
    registration = json.loads(capsys.readouterr().out)
    goal_instance_id = registration["goal_ref"]["goal_instance_id"]
    replay_arguments = _binding_arguments(
        registry_path,
        operation="bind",
        goal_instance_id=goal_instance_id,
        operation_id="bind-session-a",
    )
    assert main(replay_arguments) == 0
    committed = json.loads(capsys.readouterr().out)

    with registry_codec.source_session_registry_transaction(
        registry_path,
        operation="test_fill_session_receipt_capacity",
    ) as transaction:
        registry = transaction.payload_copy()
        registry["session_receipts"] = [
            committed["receipt"],
            *[
                {"operation_id": f"occupied-session-receipt-{index}"}
                for index in range(4095)
            ],
        ]
        transaction.commit(registry)
    at_capacity = registry_path.read_bytes()

    assert main(replay_arguments) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["replayed"] is True
    assert replay["receipt"] == committed["receipt"]
    assert registry_path.read_bytes() == at_capacity

    new_arguments = _binding_arguments(
        registry_path,
        operation="bind",
        goal_instance_id=goal_instance_id,
        operation_id="bind-session-b",
        session_id="session-b",
    )
    assert main(new_arguments) == 1
    rejection = json.loads(capsys.readouterr().out)
    assert "history_capacity_exhausted" in rejection["error"]
    assert registry_path.read_bytes() == at_capacity


def test_operation_id_cannot_be_reused_across_session_and_lifetime_receipts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, registry_path, registration = _register(tmp_path, capsys)
    instance_a = str(registration["goal_ref"]["goal_instance_id"])
    before_bind = registry_path.read_bytes()
    conflicting_bind = _binding_arguments(
        registry_path,
        operation="bind",
        goal_instance_id=instance_a,
        operation_id="create-atlas-import",
    )

    assert main(conflicting_bind) == 1
    bind_rejection = json.loads(capsys.readouterr().out)
    assert "operation_id was reused" in bind_rejection["error"]
    assert registry_path.read_bytes() == before_bind

    valid_bind = _binding_arguments(
        registry_path,
        operation="bind",
        goal_instance_id=instance_a,
        operation_id="bind-session-a",
    )
    assert main(valid_bind) == 0
    capsys.readouterr()
    before_recreation = registry_path.read_bytes()
    conflicting_recreation = _recreation_arguments(
        registry_path,
        goal_instance_id=instance_a,
        operation_id="bind-session-a",
    )

    assert main(conflicting_recreation) == 1
    recreation_rejection = json.loads(capsys.readouterr().out)
    assert "operation_id was reused" in recreation_rejection["error"]
    assert registry_path.read_bytes() == before_recreation


def test_claude_adapter_stops_before_installing_for_source_session_profile(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    knowledge_root, registry_path, _registration = _register(tmp_path, capsys)

    def unexpected_install(*_args: object, **_kwargs: object) -> None:
        pytest.fail("adapter install ran after source-session denial")

    monkeypatch.setattr(claude_connect.subprocess, "run", unexpected_install)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "connect.py",
            "--project",
            str(knowledge_root),
            "--goal-id",
            "atlas-import",
            "--registry",
            str(registry_path),
        ],
    )

    with pytest.raises(SystemExit) as stopped:
        claude_connect.main()

    assert stopped.value.code == 1
    assert "lifecycle-only profile" in capsys.readouterr().out


def test_lifetime_receipt_capacity_preserves_exact_replay_and_rejects_new_recreation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    knowledge_root = tmp_path / "atlas"
    registry_path = knowledge_root / ".loopx" / "registry.json"
    assert main(_registration_arguments(registry_path, knowledge_root)) == 0
    registration = json.loads(capsys.readouterr().out)
    instance_a = registration["goal_ref"]["goal_instance_id"]
    replay_arguments = [
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "project",
        "recreate-goal",
        "--goal-id",
        "atlas-import",
        "--goal-instance-id",
        instance_a,
        "--operation-id",
        "recreate-atlas-import",
        "--execute",
    ]
    assert main(replay_arguments) == 0
    committed = json.loads(capsys.readouterr().out)
    instance_b = committed["goal_ref"]["goal_instance_id"]

    with registry_codec.source_session_registry_transaction(
        registry_path,
        operation="test_fill_lifetime_receipt_capacity",
    ) as transaction:
        registry = transaction.payload_copy()
        registry["lifetime_receipts"] = [
            committed["receipt"],
            *[
                {"operation_id": f"occupied-lifetime-receipt-{index}"}
                for index in range(1023)
            ],
        ]
        transaction.commit(registry)
    at_capacity = registry_path.read_bytes()

    assert main(replay_arguments) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["replayed"] is True
    assert replay["goal_ref"]["goal_instance_id"] == instance_b
    assert registry_path.read_bytes() == at_capacity

    new_arguments = list(replay_arguments)
    new_arguments[new_arguments.index("--goal-instance-id") + 1] = instance_b
    new_arguments[new_arguments.index("--operation-id") + 1] = "recreate-again"
    assert main(new_arguments) == 1
    rejection = json.loads(capsys.readouterr().out)
    assert "history_capacity_exhausted" in rejection["error"]
    assert registry_path.read_bytes() == at_capacity
