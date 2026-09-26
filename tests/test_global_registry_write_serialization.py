from __future__ import annotations

import errno
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from loopx import global_registry
from loopx import project_uninstall as project_uninstall_module
from loopx.file_lock import exclusive_cross_runtime_file_lock, fcntl
from loopx.global_registry import (
    global_registry_path,
    retire_global_registry_goals,
    sync_project_registry_to_global,
)
from loopx.project_uninstall import uninstall_project


GOAL_COUNT = 6
SYNC_ROUNDS = 3


def _project_registry(root: Path, name: str, runtime_root: Path) -> Path:
    project = root / name
    (project / ".loopx").mkdir(parents=True, exist_ok=True)
    (project / "ACTIVE_GOAL_STATE.md").write_text("# state\n", encoding="utf-8")
    registry_path = project / ".loopx" / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime_root),
                "projects": [
                    {
                        "project_id": f"project-{name}",
                        "project_kind": "work",
                        "knowledge_root": str(project),
                        "repository_bindings": [],
                        "external_locator_bindings": [],
                    }
                ],
                "goals": [
                    {
                        "id": f"goal-{name}",
                        "project_id": f"project-{name}",
                        "domain": "write-serialization-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "adapter": {"kind": "fixture", "status": "connected-read-only"},
                        # Widen the write so the read-modify-write window is not
                        # narrow enough to hide a lost update by chance.
                        "objective": "x" * 20_000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry_path


def test_sync_reads_and_writes_inside_the_global_registry_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authoritative merge and the write must share one lock hold.

    Reading outside the lock and writing inside it still loses a concurrent
    sync, so assert the ordering directly instead of relying on timing.
    """

    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)
    global_path = global_registry_path(runtime_root)

    held: list[Path] = []
    locked_paths: list[Path] = []
    events: list[str] = []
    real_load = global_registry._load_global_registry
    real_write = global_registry.write_json

    @contextmanager
    def recording_lock(path: Path, **kwargs: Any) -> Iterator[Path]:
        held.append(path)
        locked_paths.append(path)
        events.append("lock-acquired")
        try:
            yield path
        finally:
            held.pop()
            events.append("lock-released")

    def recording_load(path: Path) -> dict[str, Any]:
        if path == global_path:
            events.append(f"read:{'locked' if held else 'unlocked'}")
        return real_load(path)

    def recording_write(path: Path, payload: dict[str, Any]) -> None:
        if path == global_path:
            events.append(f"write:{'locked' if held else 'unlocked'}")
        real_write(path, payload)

    monkeypatch.setattr(global_registry, "exclusive_cross_runtime_file_lock", recording_lock)
    monkeypatch.setattr(global_registry, "_load_global_registry", recording_load)
    monkeypatch.setattr(global_registry, "write_json", recording_write)

    result = sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        dry_run=False,
    )

    assert result["ok"] is True, result
    assert result["wrote"] is True, result
    assert held == [], "lock must be released"
    assert locked_paths == [global_path], "the lock must cover the global registry"
    assert "write:locked" in events, events
    assert events.index("lock-acquired") < events.index("write:locked"), events

    # The read that produced the written payload must be inside the same hold.
    locked_reads = [event for event in events if event == "read:locked"]
    assert locked_reads, f"authoritative merge read outside the lock: {events}"
    assert events.index("read:locked") < events.index("write:locked"), events


@pytest.mark.parametrize("denied_errno", [errno.EACCES, errno.EPERM, errno.EROFS])
def test_sync_lock_write_denial_is_terminal_even_when_probe_is_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied_errno: int
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)
    original = registry_path.read_bytes()
    failure = OSError(denied_errno, "lock write denied")

    @contextmanager
    def denied_lock(path: Path, **kwargs: Any) -> Iterator[Path]:
        raise failure
        yield path

    monkeypatch.setattr(global_registry, "exclusive_cross_runtime_file_lock", denied_lock)
    result = sync_project_registry_to_global(
        registry_path=registry_path, runtime_root_override=str(runtime_root)
    )

    assert result["ok"] is False
    assert result["error_kind"] == "global_registry_write_denied"
    assert result["errno"] == denied_errno
    assert result["wrote"] is False
    assert result["synced_goal_ids"] == []
    assert result["requires_global_registry_repair"] is True
    assert result["requires_host_permission"] is True
    assert result["global_registry_writability"]["ok"] is True
    assert registry_path.read_bytes() == original
    assert not global_registry_path(runtime_root).exists()


def test_sync_lock_other_io_error_is_not_permission_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)
    failure = OSError(errno.ENOSPC, "device full")

    @contextmanager
    def failed_lock(path: Path, **kwargs: Any) -> Iterator[Path]:
        raise failure
        yield path

    monkeypatch.setattr(global_registry, "exclusive_cross_runtime_file_lock", failed_lock)
    with pytest.raises(OSError) as caught:
        sync_project_registry_to_global(
            registry_path=registry_path, runtime_root_override=str(runtime_root)
        )
    assert caught.value is failure
    assert not global_registry_path(runtime_root).exists()


def test_dry_run_sync_does_not_take_the_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)

    acquired: list[Path] = []

    @contextmanager
    def recording_lock(path: Path, **kwargs: Any) -> Iterator[Path]:
        acquired.append(path)
        yield path

    monkeypatch.setattr(global_registry, "exclusive_cross_runtime_file_lock", recording_lock)

    result = sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        dry_run=True,
    )

    assert result["ok"] is True, result
    assert result["wrote"] is False, result
    assert acquired == [], "a read-only preview must not block concurrent writers"
    assert not global_registry_path(runtime_root).exists()


def test_retire_reads_and_writes_inside_the_global_registry_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)
    sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        dry_run=False,
    )
    global_path = global_registry_path(runtime_root)

    # Retirement refuses goals whose source registry or state file still exists.
    for path in (registry_path, tmp_path / "alpha" / "ACTIVE_GOAL_STATE.md"):
        path.unlink()

    held: list[Path] = []
    events: list[str] = []
    real_write = global_registry.write_json
    real_load = global_registry._load_global_registry

    @contextmanager
    def recording_lock(path: Path, **kwargs: Any) -> Iterator[Path]:
        held.append(path)
        try:
            yield path
        finally:
            held.pop()

    def recording_load(path: Path) -> dict[str, Any]:
        if path == global_path:
            events.append(f"read:{'locked' if held else 'unlocked'}")
        return real_load(path)

    def recording_write(path: Path, payload: dict[str, Any]) -> None:
        if path == global_path:
            events.append(f"write:{'locked' if held else 'unlocked'}")
        elif path.suffix == ".bak":
            events.append(f"backup:{'locked' if held else 'unlocked'}")
        real_write(path, payload)

    monkeypatch.setattr(global_registry, "exclusive_cross_runtime_file_lock", recording_lock)
    monkeypatch.setattr(global_registry, "_load_global_registry", recording_load)
    monkeypatch.setattr(global_registry, "write_json", recording_write)

    result = retire_global_registry_goals(
        runtime_root_override=str(runtime_root),
        goal_ids=["goal-alpha"],
        execute=True,
    )

    assert result["ok"] is True, result
    assert result["wrote"] is True, result
    assert held == []
    assert "read:locked" in events, events
    assert "backup:locked" in events, events
    assert "write:locked" in events, events
    assert events.index("read:locked") < events.index("write:locked"), events
    assert events.index("backup:locked") < events.index("write:locked"), events
    remaining = json.loads(global_path.read_text(encoding="utf-8"))["goals"]
    assert [goal.get("id") for goal in remaining] == []


def test_retire_rechecks_live_route_inside_the_global_registry_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)
    state_path = tmp_path / "alpha" / "ACTIVE_GOAL_STATE.md"
    sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        dry_run=False,
    )
    registry_path.unlink()
    state_path.unlink()
    global_path = global_registry_path(runtime_root)
    real_lock = global_registry.exclusive_cross_runtime_file_lock
    restored: list[Path] = []

    @contextmanager
    def restore_live_route_before_lock(path: Path, **kwargs: Any) -> Iterator[Path]:
        if path == global_path and not restored:
            registry_path.write_text(
                '{"schema_version":"0.1","goals":[]}', encoding="utf-8"
            )
            state_path.write_text("# active again\n", encoding="utf-8")
            restored.append(path)
        with real_lock(path, **kwargs) as locked:
            yield locked

    monkeypatch.setattr(
        global_registry,
        "exclusive_cross_runtime_file_lock",
        restore_live_route_before_lock,
    )

    with pytest.raises(ValueError, match="live source_registry or state_file"):
        retire_global_registry_goals(
            runtime_root_override=str(runtime_root),
            goal_ids=["goal-alpha"],
            execute=True,
        )

    assert restored == [global_path]
    remaining = json.loads(global_path.read_text(encoding="utf-8"))["goals"]
    assert [goal.get("id") for goal in remaining] == ["goal-alpha"]


def test_sync_preserves_a_goal_committed_between_preview_and_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync must not write back a merge preview taken before it held the lock.

    `probe_registry_write_path` runs after the unlocked preview merge and before
    the write, so committing another project's goal there reproduces the real
    interleaving deterministically, without depending on process scheduling.
    """

    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)
    global_path = global_registry_path(runtime_root)
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(
        json.dumps(
            {"schema_version": "0.1", "registry_role": "global-local", "goals": []}
        ),
        encoding="utf-8",
    )

    real_probe = global_registry.probe_registry_write_path
    injected = {
        "id": "goal-beta",
        "domain": "write-serialization-fixture",
        "repo": str(tmp_path / "beta"),
        "state_file": "ACTIVE_GOAL_STATE.md",
        "source_registry": str(tmp_path / "beta" / ".loopx" / "registry.json"),
    }
    calls: list[Path] = []

    def probe_then_commit_concurrent_goal(path: Path, **kwargs: Any) -> dict[str, Any]:
        result = real_probe(path, **kwargs)
        if path == global_path and not calls:
            calls.append(path)
            payload = json.loads(global_path.read_text(encoding="utf-8"))
            payload["goals"] = [*payload["goals"], injected]
            global_path.write_text(json.dumps(payload), encoding="utf-8")
        return result

    monkeypatch.setattr(
        global_registry, "probe_registry_write_path", probe_then_commit_concurrent_goal
    )

    result = sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        dry_run=False,
    )

    assert calls, "the concurrent commit never ran; adjust the interleaving point"
    assert result["ok"] is True, result
    synced = sorted(
        str(goal.get("id"))
        for goal in json.loads(global_path.read_text(encoding="utf-8"))["goals"]
    )
    assert synced == ["goal-alpha", "goal-beta"], (
        f"a concurrently synced goal was overwritten by a stale merge preview: {synced}"
    )


def test_queued_sync_refreshes_source_after_acquiring_global_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)
    global_path = global_registry_path(runtime_root)
    sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        dry_run=False,
    )

    worker_loaded_source = threading.Event()
    worker_results: list[dict[str, Any]] = []
    worker_errors: list[BaseException] = []
    worker: threading.Thread
    real_load_registry = global_registry.load_registry

    def recording_load_registry(path: Path) -> dict[str, Any]:
        payload = real_load_registry(path)
        if threading.current_thread() is worker and path == registry_path:
            worker_loaded_source.set()
        return payload

    monkeypatch.setattr(global_registry, "load_registry", recording_load_registry)

    def queued_sync() -> None:
        try:
            worker_results.append(
                sync_project_registry_to_global(
                    registry_path=registry_path,
                    runtime_root_override=str(runtime_root),
                    dry_run=False,
                )
            )
        except BaseException as exc:
            worker_errors.append(exc)

    worker = threading.Thread(target=queued_sync)
    with exclusive_cross_runtime_file_lock(
        global_path,
        operation="test_queued_sync_refreshes_source",
    ):
        worker.start()
        assert worker_loaded_source.wait(timeout=2)
        source_payload = json.loads(registry_path.read_text(encoding="utf-8"))
        source_payload["goals"][0]["objective"] = "new source value"
        registry_path.write_text(json.dumps(source_payload), encoding="utf-8")
        sync_project_registry_to_global(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root),
            dry_run=False,
            _global_registry_lock_held=True,
        )

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert worker_errors == []
    assert worker_results and worker_results[0]["ok"] is True
    projected_goal = json.loads(global_path.read_text(encoding="utf-8"))["goals"][0]
    assert projected_goal["objective"] == "new source value"


def test_project_uninstall_preserves_a_goal_committed_after_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_root = tmp_path / "runtime"
    registry_path = _project_registry(tmp_path, "alpha", runtime_root)
    sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        dry_run=False,
    )
    global_path = global_registry_path(runtime_root)
    real_copy_backup = project_uninstall_module._copy_backup
    injected: list[Path] = []

    def backup_then_commit_concurrent_goal(
        path: Path, *, label: str, dry_run: bool
    ) -> str | None:
        result = real_copy_backup(path, label=label, dry_run=dry_run)
        if path == registry_path and not dry_run and not injected:
            payload = json.loads(global_path.read_text(encoding="utf-8"))
            payload["goals"].append(
                {
                    "id": "goal-beta",
                    "domain": "write-serialization-fixture",
                    "repo": str(tmp_path / "beta"),
                    "state_file": "ACTIVE_GOAL_STATE.md",
                    "source_registry": str(
                        tmp_path / "beta" / ".loopx" / "registry.json"
                    ),
                }
            )
            global_path.write_text(json.dumps(payload), encoding="utf-8")
            injected.append(path)
        return result

    monkeypatch.setattr(
        project_uninstall_module,
        "_copy_backup",
        backup_then_commit_concurrent_goal,
    )

    result = uninstall_project(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_ids=["goal-alpha"],
        archive_state=False,
        remove_empty_registry=False,
        execute=True,
    )

    assert injected == [registry_path]
    assert result["global_registry_removed_goal_ids"] == ["goal-alpha"]
    assert result["global_registry_goal_count_before"] == 2
    assert result["global_registry_goal_count_after"] == 1
    remaining = json.loads(global_path.read_text(encoding="utf-8"))["goals"]
    assert [goal.get("id") for goal in remaining] == ["goal-beta"]


_CONCURRENT_SYNC_SCRIPT = """
import sys
from pathlib import Path

from loopx.global_registry import sync_project_registry_to_global

registry_path = Path(sys.argv[1])
runtime_root = sys.argv[2]
for _ in range({rounds}):
    sync_project_registry_to_global(
        registry_path=registry_path,
        runtime_root_override=runtime_root,
        dry_run=False,
    )
""".format(rounds=SYNC_ROUNDS)


@pytest.mark.skipif(fcntl is None, reason="POSIX flock is required")
def test_concurrent_project_syncs_do_not_drop_goals(tmp_path: Path) -> None:
    """Every project syncing one shared runtime root must survive.

    Each `loopx` invocation is its own process, so exercise real cross-process
    contention rather than threads in one interpreter.
    """

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[1]
    names = [f"p{index:02d}" for index in range(GOAL_COUNT)]
    registries = [_project_registry(tmp_path, name, runtime_root) for name in names]

    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CONCURRENT_SYNC_SCRIPT,
                str(registry_path),
                str(runtime_root),
            ],
            cwd=repo_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for registry_path in registries
    ]
    failures = []
    for process in processes:
        _, stderr = process.communicate(timeout=180)
        if process.returncode != 0:
            failures.append(stderr)
    assert not failures, failures

    payload = json.loads(global_registry_path(runtime_root).read_text(encoding="utf-8"))
    synced = sorted(str(goal.get("id")) for goal in payload["goals"])
    assert synced == sorted(f"goal-{name}" for name in names)
    synced_projects = sorted(
        str(project.get("project_id")) for project in payload["projects"]
    )
    assert synced_projects == sorted(f"project-{name}" for name in names)
    assert {
        str(goal.get("id")): str(goal.get("project_id")) for goal in payload["goals"]
    } == {f"goal-{name}": f"project-{name}" for name in names}
