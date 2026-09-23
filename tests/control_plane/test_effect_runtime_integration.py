from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.coordination.runtime_shadow import (
    RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
    bootstrap_coordination_runtime_shadow,
    build_runtime_shadow_source_snapshot,
    dispatch_coordination_runtime_shadow,
    inspect_coordination_runtime_shadow,
    read_coordination_runtime_shadow_todo_candidate,
    rollback_coordination_runtime_shadow,
)


_TURN_KEY = "sha256:" + "a" * 64
_TODO_ID = "todo_fixture0001"


def _effect_id(label: str) -> str:
    return f"fixture-goal:fixture-agent:{_TODO_ID}:{label}"


def _journal(effect_id: str) -> dict[str, object]:
    goal_id, agent_id, todo_id, turn_instance_id = effect_id.split(":", 3)
    return {
        "schema_version": "loopx_turn_journal_v0",
        "goal_id": goal_id,
        "turn_key": _TURN_KEY,
        "status": "in_progress",
        "completed_phases": [],
        "plan": {
            "turn_envelope": {
                "goal_id": goal_id,
                "agent_id": agent_id,
                "action": {"selected_todo": {"todo_id": todo_id}},
            },
            "transaction": {
                "turn_key": _TURN_KEY,
                "turn_instance_id": turn_instance_id,
                "settlement_plan": {
                    "schema_version": "quota_settlement_plan_v1",
                    "identity": {
                        "schema_version": "quota_settlement_identity_v0",
                        "effect_id": effect_id,
                        "goal_id": goal_id,
                        "agent_id": agent_id,
                        "todo_id": todo_id,
                        "turn_instance_id": turn_instance_id,
                    },
                },
            },
        },
    }


def _raw_runtime_response(
    info: dict[str, object],
    payload: bytes,
) -> dict[str, object]:
    response = b""
    with socket.create_connection(
        (str(info["host"]), int(info["port"])), timeout=2
    ) as connection:
        connection.settimeout(2)
        connection.sendall(payload)
        while b"\n" not in response:
            chunk = connection.recv(64 * 1024)
            if not chunk:
                break
            response += chunk
    decoded = json.loads(response.split(b"\n", 1)[0])
    assert isinstance(decoded, dict)
    return decoded


def test_runtime_pid_liveness_delegates_to_shared_non_signaling_probe(
    monkeypatch,
) -> None:
    calls: list[object] = []

    def probe(pid: object) -> bool:
        calls.append(pid)
        return True

    monkeypatch.setattr(effect_runtime, "process_is_alive", probe)

    assert effect_runtime._pid_is_alive(1234) is True
    assert calls == [1234]


def test_managed_runtime_is_reused_and_restart_safe_for_typed_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")

    first = effect_runtime.effect_runtime_result("runtime.ping", {})
    second = effect_runtime.effect_runtime_result("runtime.ping", {})
    assert first["pid"] == second["pid"]

    journal_path = tmp_path / "turn.json"
    effect_id = "goal:agent:todo:turn"
    payload = _journal(effect_id)
    committed = effect_runtime.effect_runtime_result(
        "turn_journal.write",
        {
            "path": str(journal_path),
            "journal": payload,
            "expected_effect_id": effect_id,
        },
    )
    assert committed["appended"] is True
    assert committed["replayed"] is False
    assert committed["effect_id"] == effect_id
    assert json.loads(journal_path.read_text(encoding="utf-8")) == payload

    effect_runtime.effect_runtime_result(
        "runtime.shutdown",
        {},
        retry_safe=False,
    )
    deadline = time.monotonic() + 2
    while list(runtime_dir.glob("runtime-*.json")) and time.monotonic() < deadline:
        time.sleep(0.025)

    replayed = effect_runtime.effect_runtime_result(
        "turn_journal.write",
        {
            "path": str(journal_path),
            "journal": payload,
            "expected_effect_id": effect_id,
        },
    )
    assert replayed["appended"] is False
    assert replayed["replayed"] is True
    assert json.loads(journal_path.read_text(encoding="utf-8")) == payload

    effect_runtime.effect_runtime_result(
        "runtime.shutdown",
        {},
        retry_safe=False,
    )


def test_response_timeout_keeps_live_runtime_locator_and_never_replays_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    fingerprint = "a" * 64
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setattr(
        effect_runtime, "_runtime_fingerprint_for_request", lambda: fingerprint
    )
    monkeypatch.setattr(
        effect_runtime, "_start_runtime",
        lambda **_kwargs: pytest.fail("a response timeout must not start another server"),
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)
        info_path = effect_runtime._runtime_info_path(fingerprint)
        info = {
            "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "pid": os.getpid(),
            "host": "127.0.0.1",
            "port": listener.getsockname()[1],
            "token": "fixture-token",
        }
        info_path.write_text(json.dumps(info), encoding="utf-8")

        def serve_one() -> int:
            with listener.accept()[0] as connection:
                request = b""
                while b"\n" not in request:
                    request += connection.recv(4096)
                time.sleep(0.1)
                try:
                    connection.sendall(b'{}\n')
                except OSError:
                    pass
                return 1

        with ThreadPoolExecutor(max_workers=1) as executor:
            served = executor.submit(serve_one)
            with pytest.raises(effect_runtime.EffectRuntimeResponseAmbiguous) as error:
                effect_runtime.effect_runtime_request(
                    "coordination.local_authority.todo_terminal",
                    {"operation_id": "fixture-operation"},
                    timeout=0.02,
                )
            assert error.value.diagnostic_code == "runtime_response_ambiguous"
            assert served.result(timeout=2) == 1
        assert json.loads(info_path.read_text(encoding="utf-8")) == info


def test_old_server_close_cannot_remove_replacement_runtime_locator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "60000")
    effect_runtime.effect_runtime_result("runtime.ping", {})
    info_path = effect_runtime._runtime_info_path(effect_runtime._runtime_fingerprint())
    original = json.loads(info_path.read_text(encoding="utf-8"))
    replacement = {**original, "pid": os.getpid(), "token": "replacement-token"}
    info_path.write_text(json.dumps(replacement), encoding="utf-8")

    effect_runtime._request_with_info(
        original,
        request_id="shutdown-old-server",
        method="runtime.shutdown",
        params={},
        timeout=2,
    )
    deadline = time.monotonic() + 2
    while effect_runtime._pid_is_alive(original["pid"]) and time.monotonic() < deadline:
        time.sleep(0.025)
    assert json.loads(info_path.read_text(encoding="utf-8")) == replacement


def test_retired_coordination_snapshot_mirror_is_rejected_across_runtime_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path / "runtime")
    goal = {
        "id": "shadow-goal",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }
    request = {
        "goal": goal,
        "runtime_root": tmp_path / "state",
        "goal_id": "shadow-goal",
        "operation_id": "todo-shadow:cross-runtime",
        "event_kind": "todo_claim",
        "source_version": "state:1",
        "projection": {
            "schema_version": "loopx_coordination_runtime_shadow_projection_v0",
            "goal_id": "shadow-goal",
            "todos": [
                {
                    "todo_id": "todo_cross_runtime",
                    "status": "open",
                    "claimed_by": "agent-a",
                }
            ],
            "leases": [],
        },
    }

    applied = dispatch_coordination_runtime_shadow(**request)
    replayed = dispatch_coordination_runtime_shadow(**request)
    read_candidate = read_coordination_runtime_shadow_todo_candidate(
        goal=goal,
        runtime_root=tmp_path / "state",
        goal_id="shadow-goal",
        todo_id="todo_cross_runtime",
        projection=request["projection"],
    )

    assert applied["status"] == "failed"
    assert applied["reason_code"] == "legacy_lineage_read_only"
    assert replayed["status"] == "failed"
    assert replayed["reason_code"] == "legacy_lineage_read_only"
    assert read_candidate["read_candidate_qualified"] is False
    assert read_candidate["decision_read_from_shadow"] is False
    assert not (tmp_path / "state/authority-shadow/file-v0").exists()

    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def test_coordination_runtime_shadow_bootstrap_crosses_python_typescript_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path / "runtime")
    goal = {
        "id": "shadow-bootstrap-goal",
        "repo": str(tmp_path),
        "state_file": "ACTIVE_GOAL_STATE.md",
        "coordination": {
            "runtime_shadow": {
                "enabled": True,
                "schema_version": RUNTIME_SHADOW_CONFIG_SCHEMA_VERSION,
                "provider": "file_v0",
            }
        },
    }
    state_path = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_path.write_text("---\ngoal_id: shadow-bootstrap-goal\nhandoff_mode: hard_lease\n---\n\n## Agent Todo\n\n"
        "- [ ] Preserve the existing Todo.\n"
        "  <!-- loopx:todo todo_id=todo_existing status=open -->\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"common_runtime_root": str(tmp_path / "state"), "goals": [goal]}), encoding="utf-8")
    projection, source_snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=tmp_path / "state",
        state_path=state_path, registry_path=registry_path)
    request = {
        "goal": goal,
        "runtime_root": tmp_path / "state",
        "goal_id": "shadow-bootstrap-goal",
        "operation_id": "bootstrap:shadow-bootstrap-goal:state-1",
        "source_version": "state:1",
        "projection": projection,
        "source_snapshot": source_snapshot,
    }

    applied = bootstrap_coordination_runtime_shadow(**request)
    replayed = bootstrap_coordination_runtime_shadow(**request)
    inspected = inspect_coordination_runtime_shadow(
        goal=goal,
        runtime_root=tmp_path / "state",
        goal_id="shadow-bootstrap-goal",
        projection=projection,
        source_snapshot=source_snapshot,
    )

    assert applied["status"] == "applied"
    assert applied["bootstrap_receipts_empty"] is True
    assert applied["cursor"] == "1"
    assert replayed["status"] == "replayed"
    assert inspected["status"] == "matched"
    assert inspected["decision_read_from_shadow"] is False

    rollback_request = {
        "goal": goal,
        "runtime_root": tmp_path / "state",
        "goal_id": "shadow-bootstrap-goal",
        "operation_id": (
            f"rollback:shadow-bootstrap-goal:{applied['provider_revision']}"
        ),
        "expected_provider_revision": str(applied["provider_revision"]),
        "source_snapshot": source_snapshot,
    }
    rolled_back = rollback_coordination_runtime_shadow(**rollback_request)
    rollback_replay = rollback_coordination_runtime_shadow(**rollback_request)
    after_rollback = inspect_coordination_runtime_shadow(
        goal=goal,
        runtime_root=tmp_path / "state",
        goal_id="shadow-bootstrap-goal",
        projection=projection,
        source_snapshot=source_snapshot,
    )

    assert rolled_back["status"] == "applied"
    assert rolled_back["archive_retained"] is True
    assert rollback_replay["status"] == "replayed"
    assert after_rollback["status"] == "missing"

    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def test_runtime_decode_change_rotates_identity_and_starts_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = Path(effect_runtime.__file__).resolve().parent
    staged_root = tmp_path / "control_plane"
    for relative in effect_runtime._runtime_source_files(source_root):
        source = source_root / relative
        target = staged_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_control_plane_root", lambda: staged_root)
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "250")

    original_fingerprint = effect_runtime._runtime_fingerprint()
    original = effect_runtime.effect_runtime_result("runtime.ping", {})
    decoder = staged_root / "runtime_decode.ts"
    decoder.write_bytes(decoder.read_bytes() + b"\n// decoder-fingerprint-probe\n")
    replacement_fingerprint = effect_runtime._runtime_fingerprint()
    replacement = effect_runtime.effect_runtime_result("runtime.ping", {})

    assert replacement_fingerprint != original_fingerprint
    assert int(replacement["pid"]) != int(original["pid"])
    original_info = effect_runtime._runtime_info_path(original_fingerprint)
    replacement_info = effect_runtime._runtime_info_path(replacement_fingerprint)
    assert original_info != replacement_info
    assert replacement_info.exists()

    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)
    deadline = time.monotonic() + 2
    while (
        original_info.exists() or replacement_info.exists()
    ) and time.monotonic() < deadline:
        time.sleep(0.025)
    assert not original_info.exists()
    assert not replacement_info.exists()


def test_typed_write_rejects_cross_effect_overwrite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")
    journal_path = tmp_path / "turn.json"
    first_effect_id = _effect_id("effect-one")
    second_effect_id = _effect_id("effect-two")
    first = _journal(first_effect_id)
    effect_runtime.effect_runtime_result(
        "turn_journal.write",
        {
            "path": str(journal_path),
            "journal": first,
            "expected_effect_id": first_effect_id,
        },
    )

    try:
        effect_runtime.effect_runtime_result(
            "turn_journal.write",
            {
                "path": str(journal_path),
                "journal": _journal(second_effect_id),
                "expected_effect_id": second_effect_id,
            },
        )
    except effect_runtime.EffectRuntimeConflict as exc:
        assert "belongs to another settlement effect" in str(exc)
        assert exc.error_kind == "conflict"
        assert exc.diagnostic_code == "journal_effect_conflict"
    else:
        raise AssertionError("cross-effect overwrite must fail closed")
    assert json.loads(journal_path.read_text(encoding="utf-8")) == first

    effect_runtime.effect_runtime_result(
        "runtime.shutdown",
        {},
        retry_safe=False,
    )


def test_typed_write_serializes_conflicting_transitions_without_caller_cas(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")
    journal_path = tmp_path / "turn.json"
    effect_id = "goal:agent:todo:turn"
    initial = _journal(effect_id)
    effect_runtime.effect_runtime_result(
        "turn_journal.write",
        {
            "path": str(journal_path),
            "journal": initial,
            "expected_effect_id": effect_id,
        },
    )
    advanced = {
        **initial,
        "completed_phases": ["host_execute", "typed_result"],
    }
    effect_runtime.effect_runtime_result(
        "turn_journal.write",
        {
            "path": str(journal_path),
            "journal": advanced,
            "expected_effect_id": effect_id,
        },
    )
    first = {
        **advanced,
        "status": "stopped",
        "completed_phases": ["host_execute", "typed_result", "validation"],
    }
    second = {
        **advanced,
        "status": "failed",
        "receipt": {"turn_key": _TURN_KEY, "failed_phase": "validation"},
    }

    def commit(payload: dict[str, object]) -> dict[str, object] | RuntimeError:
        try:
            return effect_runtime.effect_runtime_result(
                "turn_journal.write",
                {
                    "path": str(journal_path),
                    "journal": payload,
                    "expected_effect_id": effect_id,
                },
            )
        except RuntimeError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(commit, (first, second)))

    assert sum(isinstance(outcome, RuntimeError) for outcome in outcomes) == 1
    failure = next(outcome for outcome in outcomes if isinstance(outcome, RuntimeError))
    assert isinstance(failure, effect_runtime.EffectRuntimeConflict)
    assert failure.error_kind == "conflict"
    assert failure.diagnostic_code == "journal_transition_conflict"
    assert json.loads(journal_path.read_text(encoding="utf-8")) in (first, second)
    success = next(outcome for outcome in outcomes if isinstance(outcome, dict))
    assert success["appended"] is True
    assert success["replayed"] is False

    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def test_successive_same_effect_checkpoints_receive_distinct_operation_ids(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")
    journal_path = tmp_path / "turn.json"
    effect_id = "goal:agent:todo:turn"
    initial = _journal(effect_id)
    first = effect_runtime.effect_runtime_result(
        "turn_journal.write",
        {
            "path": str(journal_path),
            "journal": initial,
            "expected_effect_id": effect_id,
        },
    )
    successor = {
        **initial,
        "completed_phases": ["host_execute", "typed_result"],
    }
    second = effect_runtime.effect_runtime_result(
        "turn_journal.write",
        {
            "path": str(journal_path),
            "journal": successor,
            "expected_effect_id": effect_id,
        },
    )

    assert first["operation_id"] != second["operation_id"]
    assert json.loads(journal_path.read_text(encoding="utf-8")) == successor
    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def test_retry_safe_typed_write_recovers_after_unexpected_runtime_exit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")

    original = effect_runtime.effect_runtime_result("runtime.ping", {})
    original_pid = int(original["pid"])
    os.kill(original_pid, signal.SIGTERM)
    time.sleep(0.1)

    journal_path = tmp_path / "turn.json"
    effect_id = "goal:agent:todo:crash-recovery"
    payload = _journal(effect_id)
    committed = effect_runtime.effect_runtime_result(
        "turn_journal.write",
        {
            "path": str(journal_path),
            "journal": payload,
            "expected_effect_id": effect_id,
        },
        retry_safe=True,
    )
    replacement = effect_runtime.effect_runtime_result("runtime.ping", {})

    assert committed["appended"] is True
    assert committed["replayed"] is False
    assert committed["effect_id"] == effect_id
    assert json.loads(journal_path.read_text(encoding="utf-8")) == payload
    assert replacement["pid"] != original_pid

    effect_runtime.effect_runtime_result(
        "runtime.shutdown",
        {},
        retry_safe=False,
    )


def test_dead_runtime_metadata_is_replaced_after_host_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")
    runtime_dir.mkdir()
    fingerprint = effect_runtime._runtime_fingerprint()
    info_path = effect_runtime._runtime_info_path(fingerprint)
    info_path.write_text(
        json.dumps(
            {
                "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
                "fingerprint": fingerprint,
                "pid": 99_999_999,
                "host": "127.0.0.1",
                "port": 9,
                "token": "stale-after-reboot",
            }
        ),
        encoding="utf-8",
    )

    replacement = effect_runtime.effect_runtime_result("runtime.ping", {})

    assert replacement["ready"] is True
    assert int(replacement["pid"]) != 99_999_999
    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def test_dead_startup_lock_is_reclaimed_without_waiting_for_age_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")
    runtime_dir.mkdir()
    fingerprint = effect_runtime._runtime_fingerprint()
    lock = runtime_dir / f"start-{fingerprint[:16]}.lock"
    lock.write_text("99999999\n", encoding="utf-8")

    started_at = time.monotonic()
    replacement = effect_runtime.effect_runtime_result("runtime.ping", {})

    assert replacement["ready"] is True
    assert time.monotonic() - started_at < 3
    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def test_runtime_ready_budget_starts_after_start_lock_acquisition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    fingerprint = "f" * 64
    info_path = runtime_dir / "runtime.json"
    lock = runtime_dir / f"start-{fingerprint[:16]}.lock"
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    clock = {"monotonic": 0.0}
    ready_info = {
        "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "pid": os.getpid(),
        "host": "127.0.0.1",
        "port": 1,
        "token": "fixture-token",
    }

    def sleep(seconds: float) -> None:
        clock["monotonic"] += seconds
        lock.unlink(missing_ok=True)

    def read_info(_path: Path, *, fingerprint: str):
        assert fingerprint == "f" * 64
        return ready_info if clock["monotonic"] >= 1.5 else None

    class _RunningProcess:
        def poll(self):
            return None

        def terminate(self) -> None:
            raise AssertionError("the runtime still has a fresh ready budget")

    monkeypatch.setattr(effect_runtime, "_read_info", read_info)
    monkeypatch.setattr(effect_runtime, "_node_executable", lambda: "node")
    monkeypatch.setattr(
        effect_runtime.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _RunningProcess(),
    )
    monkeypatch.setattr(effect_runtime, "STARTUP_LOCK_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(effect_runtime, "STARTUP_READY_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(effect_runtime, "STARTUP_POLL_SECONDS", 0.75)
    monkeypatch.setattr(
        effect_runtime,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock["monotonic"],
            sleep=sleep,
            time=time.time,
        ),
    )

    assert (
        effect_runtime._start_runtime(
            fingerprint=fingerprint,
            info_path=info_path,
        )
        == ready_info
    )
    assert clock["monotonic"] == 1.5


def test_early_runtime_exit_surfaces_stable_startup_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setattr(effect_runtime, "_node_executable", lambda: "node")

    class _ExitedProcess:
        def poll(self) -> int:
            return 23

    launch: dict[str, object] = {}

    def popen(*_args, **kwargs):
        launch.update(kwargs)
        return _ExitedProcess()

    monkeypatch.setattr(
        effect_runtime.subprocess,
        "Popen",
        popen,
    )

    try:
        effect_runtime.effect_runtime_result("runtime.ping", {})
    except effect_runtime.EffectRuntimeStartupError as exc:
        assert exc.diagnostic_code == "runtime_exited_before_ready"
        assert "exit_code=23" in str(exc)
    else:
        raise AssertionError("an early runtime exit must fail with a stable diagnostic")
    assert launch["close_fds"] is True


@pytest.mark.parametrize(
    ("retry_safe", "successful_attempt", "expected_attempts"),
    [
        (False, None, 1),
        (True, None, 2),
        (True, 2, 2),
    ],
)
def test_request_startup_retry_boundary(
    tmp_path: Path,
    monkeypatch,
    retry_safe: bool,
    successful_attempt: int | None,
    expected_attempts: int,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    attempts = {"count": 0}
    ready_info = {
        "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
        "fingerprint": effect_runtime._runtime_fingerprint(),
        "pid": os.getpid(),
        "host": "127.0.0.1",
        "port": 1,
        "token": "fixture-token",
    }

    def start_runtime(*, fingerprint: str, info_path: Path):
        attempts["count"] += 1
        assert fingerprint == ready_info["fingerprint"]
        assert info_path.parent == runtime_dir
        if attempts["count"] == successful_attempt:
            return ready_info
        raise effect_runtime.EffectRuntimeStartupError(
            "runtime exited before ready",
            diagnostic_code="runtime_exited_before_ready",
        )

    monkeypatch.setattr(effect_runtime, "_start_runtime", start_runtime)
    monkeypatch.setattr(
        effect_runtime,
        "_request_with_info",
        lambda info, **_kwargs: {"result": {"pid": info["pid"]}},
    )

    if successful_attempt is not None:
        assert effect_runtime.effect_runtime_result(
            "runtime.ping",
            {},
            retry_safe=retry_safe,
        ) == {"pid": os.getpid()}
    else:
        with pytest.raises(
            effect_runtime.EffectRuntimeStartupError,
            match="runtime exited before ready",
        ) as exc_info:
            effect_runtime.effect_runtime_result(
                "turn_journal.write",
                {},
                retry_safe=retry_safe,
            )
        assert exc_info.value.diagnostic_code == "runtime_exited_before_ready"

    assert attempts["count"] == expected_attempts


def test_managed_runtime_releases_memory_after_idle_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "150")

    original = effect_runtime.effect_runtime_result("runtime.ping", {})
    original_pid = int(original["pid"])
    deadline = time.monotonic() + 2
    while list(runtime_dir.glob("runtime-*.json")) and time.monotonic() < deadline:
        time.sleep(0.025)

    assert list(runtime_dir.glob("runtime-*.json")) == []
    replacement = effect_runtime.effect_runtime_result("runtime.ping", {})
    assert int(replacement["pid"]) != original_pid

    effect_runtime.effect_runtime_result(
        "runtime.shutdown",
        {},
        retry_safe=False,
    )


@pytest.mark.parametrize(
    "raw_idle_ms",
    [
        "",
        "not-a-number",
        "0",
        "-1",
        "1.5",
        "1e3",
        "0x10",
        " 150",
        "150 ",
        "2147483648",
        "9007199254740993",
    ],
)
def test_invalid_idle_timeout_configuration_fails_closed(
    tmp_path: Path,
    monkeypatch,
    raw_idle_ms: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", raw_idle_ms)

    with pytest.raises(
        effect_runtime.EffectRuntimeStartupError,
        match="LOOPX_EFFECT_RUNTIME_IDLE_MS",
    ) as exc_info:
        effect_runtime.effect_runtime_result("runtime.ping", {})

    assert exc_info.value.diagnostic_code == "invalid_idle_timeout"
    assert list(runtime_dir.glob("runtime-*.json")) == []


@pytest.mark.parametrize("raw_idle_ms", [None, "150", "2147483647"])
def test_valid_idle_timeout_configuration_serves_requests(
    tmp_path: Path,
    monkeypatch,
    raw_idle_ms: str | None,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    if raw_idle_ms is None:
        monkeypatch.delenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", raising=False)
    else:
        monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", raw_idle_ms)

    try:
        result = effect_runtime.effect_runtime_result("runtime.ping", {})
        assert int(result["pid"]) > 0
    finally:
        try:
            effect_runtime.effect_runtime_result(
                "runtime.shutdown",
                {},
                retry_safe=False,
            )
        except Exception:
            pass


def test_oversized_request_is_rejected_before_runtime_dispatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")

    original = effect_runtime.effect_runtime_result("runtime.ping", {})
    original_pid = int(original["pid"])
    oversized = "x" * (effect_runtime.MAX_REQUEST_BYTES + 1)

    try:
        effect_runtime.effect_runtime_result(
            "effect.interpret_turn_result",
            {"packet": {"oversized": oversized}, "identity": {}},
        )
    except effect_runtime.EffectRuntimeRejected as exc:
        assert "request is oversized" in str(exc)
    else:
        raise AssertionError("oversized request must fail before dispatch")

    assert (
        effect_runtime.effect_runtime_result("runtime.ping", {})["pid"] == original_pid
    )
    effect_runtime.effect_runtime_result(
        "runtime.shutdown",
        {},
        retry_safe=False,
    )


@pytest.mark.parametrize("unit", [b"x", "界".encode()])
def test_oversized_raw_socket_request_returns_typed_error(
    tmp_path: Path,
    monkeypatch,
    unit: bytes,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")

    ping = effect_runtime.effect_runtime_result("runtime.ping", {})
    fingerprint = effect_runtime._runtime_fingerprint()
    info = effect_runtime._read_info(
        effect_runtime._runtime_info_path(fingerprint),
        fingerprint=fingerprint,
    )
    assert info is not None
    repeats = effect_runtime.MAX_REQUEST_BYTES // len(unit) + 1

    response = _raw_runtime_response(info, unit * repeats)

    assert response == {
        "schema_version": effect_runtime.EFFECT_RUNTIME_RESPONSE_SCHEMA_VERSION,
        "request_id": "unknown",
        "ok": False,
        "error": {
            "kind": "request_rejected",
            "code": "request_too_large",
            "message": "Effect runtime request exceeds the 2 MiB limit",
        },
    }
    assert (
        effect_runtime.effect_runtime_result("runtime.ping", {})["pid"]
        == ping["pid"]
    )
    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def test_non_object_json_request_is_rejected_at_the_socket_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")

    ping = effect_runtime.effect_runtime_result("runtime.ping", {})
    fingerprint = effect_runtime._runtime_fingerprint()
    info = effect_runtime._read_info(
        effect_runtime._runtime_info_path(fingerprint),
        fingerprint=fingerprint,
    )
    assert info is not None

    response = _raw_runtime_response(info, b"null\n")

    assert response["request_id"] == "unknown"
    assert response["ok"] is False
    assert response["error"] == {
        "kind": "request_rejected",
        "code": "invalid_request",
        "message": "Effect runtime request must be an object",
    }
    assert (
        effect_runtime.effect_runtime_result("runtime.ping", {})["pid"] == ping["pid"]
    )
    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


def test_corrupt_persisted_journal_maps_to_bounded_internal_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: runtime_dir)
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "1000")
    journal_path = tmp_path / "corrupt-turn-journal.json"
    journal_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(effect_runtime.EffectRuntimeInternalError) as caught:
        effect_runtime.effect_runtime_result(
            "turn_journal.write",
            {
                "path": str(journal_path),
                "journal": _journal(_effect_id("effect-one")),
                "expected_effect_id": _effect_id("effect-one"),
            },
        )

    assert caught.value.diagnostic_code == "unexpected_handler_error"
    assert str(caught.value) == "Effect runtime handler failed unexpectedly"
    assert str(journal_path) not in str(caught.value)
    effect_runtime.effect_runtime_result("runtime.shutdown", {}, retry_safe=False)


@pytest.mark.parametrize(
    ("payload", "exception_type", "transient"),
    [
        (
            {"kind": "request_rejected", "code": "invalid_request", "message": "bad"},
            effect_runtime.EffectRuntimeRejected,
            None,
        ),
        (
            {"kind": "conflict", "code": "state_conflict", "message": "stale"},
            effect_runtime.EffectRuntimeConflict,
            None,
        ),
        (
            {"kind": "io_transient", "code": "io_busy", "message": "busy"},
            effect_runtime.EffectRuntimeTransientIOError,
            True,
        ),
        (
            {"kind": "io_permanent", "code": "io_not_found", "message": "gone"},
            effect_runtime.EffectRuntimePermanentIOError,
            False,
        ),
        (
            {
                "kind": "lock_timeout",
                "code": "mutation_lock_timeout",
                "message": "waited",
            },
            effect_runtime.EffectRuntimeLockTimeout,
            None,
        ),
        (
            {
                "kind": "internal_failure",
                "code": "unexpected_handler_error",
                "message": "failed",
            },
            effect_runtime.EffectRuntimeInternalError,
            None,
        ),
    ],
)
def test_remote_error_envelope_preserves_typed_exception(
    payload: dict[str, str],
    exception_type: type[effect_runtime.EffectRuntimeRemoteError],
    transient: bool | None,
) -> None:
    error = effect_runtime._remote_runtime_error(payload)

    assert isinstance(error, exception_type)
    assert error.error_kind == payload["kind"]
    assert error.diagnostic_code == payload["code"]
    if transient is not None:
        assert isinstance(error, effect_runtime.EffectRuntimeIOError)
        assert error.transient is transient


def test_invalid_remote_error_envelope_fails_as_bounded_internal_error() -> None:
    error = effect_runtime._remote_runtime_error(
        {"kind": "internal_failure", "message": "/private/path\nstack"}
    )

    assert isinstance(error, effect_runtime.EffectRuntimeInternalError)
    assert error.diagnostic_code == "invalid_error_envelope"
    assert "/private/path" not in str(error)
