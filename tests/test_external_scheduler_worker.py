from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import time

import pytest
from loopx import paths


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import external_scheduler_worker as worker  # noqa: E402


def test_default_registry_follows_single_runtime_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    current_root = tmp_path / ".loopx"
    legacy_root = tmp_path / ".codex" / "loopx"
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current_root)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy_root)
    selected: list[Path] = []
    monkeypatch.setattr(
        worker,
        "run_worker",
        lambda args: selected.append(Path(args.registry)) or 0,
    )
    argv = ["--goal-id", "goal", "--agent-id", "agent"]

    assert worker.main(argv) == 0
    assert selected[-1] == current_root / paths.GLOBAL_REGISTRY_FILENAME

    legacy_root.mkdir(parents=True)
    (legacy_root / paths.GLOBAL_REGISTRY_FILENAME).write_text("{}", encoding="utf-8")
    assert worker.main(argv) == 0
    assert selected[-1] == legacy_root / paths.GLOBAL_REGISTRY_FILENAME

    current_root.mkdir()
    (current_root / paths.GLOBAL_REGISTRY_FILENAME).write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as conflict:
        worker.main(argv)
    assert conflict.value.code == 2
    assert "Both default LoopX registries exist" in capsys.readouterr().err
    assert len(selected) == 2

    assert worker.main(
        [*argv, "--registry", str(legacy_root / paths.GLOBAL_REGISTRY_FILENAME)]
    ) == 0
    assert selected[-1] == legacy_root / paths.GLOBAL_REGISTRY_FILENAME


def _write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _hint_payload(
    *,
    should_run: bool,
    action: str | None = None,
    local_scheduler_directive: str | None = None,
) -> dict[str, object]:
    resolved_action = action or (
        "run_now" if should_run else "stop_until_explicit_resume"
    )
    scheduler_hint: dict[str, object] = {
        "action": resolved_action,
        "cadence_class": "active_work" if should_run else "terminal_no_followup",
        "reason": "fixture",
        "reset_policy": {"reset_token": "fixture-token"},
        "cold_path_detail": {
            "local_scheduler": {
                "recommended_interval_minutes": 1,
                "example_progression_minutes": [1],
                "unchanged_poll_limit": None,
                "after_limit": "continue",
                "final_quota_replan_check": {"enabled": False},
            }
        },
    }
    if local_scheduler_directive is not None:
        scheduler_hint["unchanged_poll"] = {
            "local_scheduler": local_scheduler_directive
        }
    return {
        "should_run": should_run,
        "effective_action": resolved_action,
        "scheduler_hint": scheduler_hint,
    }


def _waiting_payload(
    *,
    reset_token: str = "waiting-token",
    limit: int | None = None,
    final_probe_enabled: bool = False,
) -> dict[str, object]:
    return {
        "should_run": False,
        "effective_action": "backoff_until_state_change",
        "scheduler_hint": {
            "action": "backoff_until_state_change",
            "cadence_class": "quiet_wait",
            "reason": "fixture waiting",
            "reset_policy": {"reset_token": reset_token},
            "cold_path_detail": {
                "local_scheduler": {
                    "recommended_interval_minutes": 1,
                    "example_progression_minutes": [1, 2, 4],
                    "unchanged_poll_limit": limit,
                    "after_limit": "stop_tick_loop" if limit is not None else "continue",
                    "final_quota_replan_check": {
                        "enabled": final_probe_enabled,
                        "trigger": "before_unchanged_poll_after_limit",
                        "action": "rerun_quota_should_run_once",
                        "if_changed": "follow_new_scheduler_hint",
                        "if_run_now": "execute_new_quota_contract",
                        "if_unchanged": "apply_after_limit_without_spend",
                    },
                }
            },
        },
    }


def _args(
    *,
    fake_cli: Path,
    state_file: Path,
    wake_cmd: str | None = None,
    quota_timeout_seconds: float = 30.0,
    wake_timeout_seconds: float = 0.1,
) -> argparse.Namespace:
    return argparse.Namespace(
        cli_bin=str(fake_cli),
        registry=str(state_file.parent / "registry"),
        runtime_root=None,
        runtime_profile="generic_cli",
        goal_id="goal",
        agent_id="agent",
        state_file=str(state_file),
        wake_cmd=wake_cmd,
        once=True,
        error_backoff_seconds=5.0,
        quota_timeout_seconds=quota_timeout_seconds,
        wake_timeout_seconds=wake_timeout_seconds,
    )


@pytest.mark.parametrize("admission", ["false", "true", 1, 0, None, [], {"allowed": True}, "missing"])
def test_malformed_admission_never_launches_wake(tmp_path, admission):
    payload = _hint_payload(should_run=True)
    payload["should_run"] = admission
    if admission == "missing":
        payload.pop("should_run")
    cli = tmp_path / "quota"
    _write_executable(cli, f"#!{sys.executable}\nprint({json.dumps(payload)!r})\n")
    marker = tmp_path / "model-called"
    args = _args(fake_cli=cli, state_file=tmp_path / "state.json",
        wake_cmd=shlex.join([sys.executable, "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()"]),
        wake_timeout_seconds=5)
    assert worker.run_worker(args) == 2
    assert not marker.exists()


def test_failed_decision_cannot_authorize_wake(tmp_path, monkeypatch):
    payload = _hint_payload(should_run=True) | {"ok": False}
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: payload)
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: pytest.fail("failed decision woke"))
    args = _args(fake_cli=tmp_path / "unused", state_file=tmp_path / "state.json", wake_cmd="unused")
    assert worker.run_worker(args) == 2


def test_explicit_stop_precedes_even_malformed_admission(tmp_path, monkeypatch):
    payload = _hint_payload(should_run=True, local_scheduler_directive="stop")
    payload["should_run"] = "false"
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: payload)
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: pytest.fail("stopped worker woke"))
    args = _args(fake_cli=tmp_path / "unused", state_file=tmp_path / "state.json", wake_cmd="unused")
    assert worker.run_worker(args) == 0


@pytest.mark.parametrize("stop_code,expected", [(None, 75), (75, 0), (76, 75)])
def test_wake_terminal_code_requires_explicit_matching_opt_in(tmp_path, stop_code, expected):
    cli = tmp_path / "quota"
    _write_executable(cli, f"#!{sys.executable}\nprint({json.dumps(_hint_payload(should_run=True))!r})\n")
    args = _args(fake_cli=cli, state_file=tmp_path / "state.json",
                 wake_cmd=shlex.join([sys.executable, "-c", "raise SystemExit(75)"]),
                 wake_timeout_seconds=5)
    args.wake_stop_exit_code = stop_code
    assert worker.run_worker(args) == expected


@pytest.mark.parametrize("failure_kind", ["timeout", "output_limit"])
def test_wake_terminal_code_does_not_mask_transport_failure(tmp_path, monkeypatch, failure_kind):
    from loopx.extensions.process_runtime import CappedProcessResult

    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(should_run=True))
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: CappedProcessResult(
        returncode=75, stdout=b"", failure_kind=failure_kind,
    ))
    args = _args(fake_cli=tmp_path / "unused", state_file=tmp_path / "state.json", wake_cmd="unused")
    args.wake_stop_exit_code = 75
    assert worker.run_worker(args) == 2
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["last_wake_failure_kind"] == failure_kind


@pytest.mark.parametrize("code", ["0", "-1", "256"])
def test_wake_terminal_code_rejects_invalid_codes(code):
    with pytest.raises(SystemExit) as error:
        worker.main(["--goal-id", "goal", "--agent-id", "agent", "--wake-stop-exit-code", code])
    assert error.value.code == 2


def test_default_invocation_persists_backoff_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "default-state"
    fake_cli = root / "fake-loopx"
    payload = json.dumps(_waiting_payload())
    _write_executable(
        fake_cli,
        "#!/usr/bin/env python3\n"
        f"print({payload!r})\n",
    )
    state_home = root / "state-home"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    args = _args(
        fake_cli=fake_cli,
        state_file=root / "ignored-explicit.json",
    )
    args.state_file = None

    assert worker.run_worker(args) == 0
    assert worker.run_worker(args) == 0

    state_files = list((state_home / "loopx" / "external-worker").glob("*.json"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text(encoding="utf-8"))
    assert state["reset_token"] == "waiting-token"
    assert state["unchanged_count"] == 2


def test_unchanged_limit_runs_final_quota_probe_before_stop(
    tmp_path: Path,
) -> None:
    root = tmp_path / "final-probe"
    fake_cli = root / "fake-loopx"
    counter = root / "quota-count"
    payload = json.dumps(
        _waiting_payload(limit=2, final_probe_enabled=True)
    )
    _write_executable(
        fake_cli,
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        f"counter = Path({str(counter)!r})\n"
        "try:\n"
        "    count = int(counter.read_text(encoding='utf-8'))\n"
        "except (OSError, ValueError):\n"
        "    count = 0\n"
        "counter.write_text(str(count + 1), encoding='utf-8')\n"
        f"print({payload!r})\n",
    )
    requested_sleeps: list[float] = []
    args = _args(
        fake_cli=fake_cli,
        state_file=root / "state.json",
    )
    args.once = False

    assert worker.run_worker(args, sleep=requested_sleeps.append) == 0
    assert counter.read_text(encoding="utf-8") == "3"
    assert requested_sleeps == [60]


def test_quota_probe_timeout_enters_tick_error(tmp_path: Path) -> None:
    fake_cli = tmp_path / "quota-timeout" / "fake-loopx"
    payload = json.dumps(_hint_payload(should_run=False))
    _write_executable(
        fake_cli,
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(1)\n"
        f"print({payload!r})\n",
    )

    started = time.monotonic()
    result = worker.run_worker(
        _args(
            fake_cli=fake_cli,
            state_file=tmp_path / "quota-timeout" / "state.json",
            quota_timeout_seconds=0.1,
        )
    )

    assert result == 2
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize("expected_sleeps,wake_exit,observe_only,next_state", [
    ([], 0, False, "terminal"),
    ([], 0, False, "active"),
    ([60], 0, False, "waiting"),
    ([5], 1, False, "terminal"),
    ([60], 0, True, "terminal"),
])
def test_completed_wake_rechecks_admission_without_active_delay(
    tmp_path: Path,
    expected_sleeps: list[float], wake_exit: int, observe_only: bool, next_state: str,
) -> None:
    # Real worker and child commands, with requested waits captured.
    # The second quota response revokes admission: a completed wake must never
    # cause another wake using its old should_run decision.
    fake_cli = tmp_path / "quota"
    counter = tmp_path / "quota-count"
    active = _hint_payload(should_run=True)
    terminal = _hint_payload(should_run=False, local_scheduler_directive="stop")
    packets = [active]
    if next_state == "active":
        packets.append(active)
    elif next_state == "waiting":
        packets.append(_waiting_payload())
    packets.append(terminal)
    _write_executable(fake_cli, "#!/usr/bin/env python3\n"
        "import json\nfrom pathlib import Path\n"
        f"counter = Path({str(counter)!r})\n"
        "n = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(n + 1))\n"
        f"print(json.dumps({packets!r}[n]))\n")
    marker = tmp_path / "wake-count"
    wake_code = ("from pathlib import Path; "
        f"p=Path({str(marker)!r}); "
        "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1'); "
        f"raise SystemExit({wake_exit})")
    args = _args(fake_cli=fake_cli, state_file=tmp_path / "state.json",
        wake_cmd=shlex.join([sys.executable, "-c", wake_code]),
        quota_timeout_seconds=5.0, wake_timeout_seconds=5.0)
    args.once = False
    if observe_only:
        args.wake_cmd = None
    sleeps: list[float] = []

    assert worker.run_worker(args, sleep=sleeps.append) == 0
    assert sleeps == expected_sleeps
    assert counter.read_text() == str(len(packets))
    if observe_only:
        assert not marker.exists()
    else:
        assert marker.read_text() == ("2" if next_state == "active" else "1")


@pytest.mark.parametrize(
    "action",
    ["stop_until_explicit_resume", "return_to_owner_until_material_change"],
)
def test_stop_directive_does_not_require_cold_path_scheduler_detail(
    tmp_path: Path,
    action: str,
) -> None:
    root = tmp_path / action
    fake_cli = root / "fake-loopx"
    payload = _hint_payload(
        should_run=False,
        action=action,
        local_scheduler_directive="stop",
    )
    del payload["scheduler_hint"]["cold_path_detail"]  # type: ignore[index]
    decision = worker.parse_tick(payload)
    assert decision.terminal is True
    assert decision.action == action
    assert decision.after_limit == "stop_tick_loop"
    assert decision.unchanged_limit is None

    _write_executable(
        fake_cli,
        "#!/usr/bin/env python3\n"
        f"print({json.dumps(payload)!r})\n",
    )

    result = worker.run_worker(
        _args(
            fake_cli=fake_cli,
            state_file=root / "state.json",
        )
    )

    assert result == 0
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    assert state == {"reset_token": "fixture-token", "unchanged_count": 0}


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
def test_wake_timeout_enters_failed_backoff_and_kills_descendants(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wake-timeout"
    fake_cli = root / "fake-loopx"
    payload = json.dumps(_hint_payload(should_run=True))
    _write_executable(
        fake_cli,
        "#!/usr/bin/env python3\n"
        f"print({payload!r})\n",
    )
    marker = root / "late-write"
    child_code = (
        "from pathlib import Path; import time; "
        "time.sleep(0.5); "
        f"Path({str(marker)!r}).write_text('late', encoding='utf-8')"
    )
    wake = root / "wake"
    _write_executable(
        wake,
        "#!/usr/bin/env python3\n"
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(1)\n",
    )
    state_file = root / "state.json"

    started = time.monotonic()
    result = worker.run_worker(
        _args(
            fake_cli=fake_cli,
            state_file=state_file,
            wake_cmd=shlex.join([sys.executable, str(wake)]),
            quota_timeout_seconds=5.0,
        )
    )

    assert result == 2
    assert time.monotonic() - started < 2.0
    time.sleep(0.6)
    assert not marker.exists()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_wake_status"] == "wake_failed"
    assert state["last_wake_failure_kind"] == "timeout"


def test_wake_default_has_no_execution_deadline(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(worker, "run_worker", lambda args: seen.append(args) or 0)
    assert worker.main(["--goal-id", "fixture", "--agent-id", "agent",
                        "--registry", str(tmp_path / "registry.json")]) == 0
    assert seen[0].wake_timeout_seconds is None
    assert seen[0].quota_timeout_seconds == 30.0
    result = worker._run_wake(
        shlex.join([sys.executable, "-c", "import time;time.sleep(.1);print('done')"]),
        timeout_seconds=None,
    )
    assert result.returncode == 0
    assert result.failure_kind is None


def _guarded_host(tmp_path, *, kind="quota_exhausted", recover_at=None, session="session-a"):
    """A real local process, with independently specified Host policy fixtures."""
    retry = {"strategy": "same_configuration", "max_attempts": 3, "backoff_seconds": 30}
    failure = {"schema_version": "loopx_turn_host_failure_v0", "kind": kind,
               "attempt": 1, "retryable": kind == "provider_capacity"}
    if failure["retryable"]:
        failure["retry"] = retry
    receipt = {"ok": False, "return_code": 1, "timed_out": False, "host_failure": failure,
               "private_diagnostic": "PRIVATE-SENTINEL"}
    if session:
        receipt["session"] = {"binding_scope": "agent", "session_id": session}
    count = tmp_path / "invocations"
    binary = tmp_path / "host.py"
    _write_executable(binary, f"#!{sys.executable}\n" + f"""
import json,os,pathlib,sys
counter=pathlib.Path({str(count)!r})
n=int(counter.read_text())+1 if counter.exists() else 1
counter.write_text(str(n))
if n>1:
    assert os.environ.get('LOOPX_HOST_RETRY_SESSION_ID') == {session!r}
result={receipt!r}
if {recover_at!r} is not None and n=={recover_at!r}:
    result.pop('host_failure');result.update(ok=True,return_code=0)
print(json.dumps(result))
sys.exit(result['return_code'])
""")
    args = _args(fake_cli=tmp_path / "unused", state_file=tmp_path / "state.json",
                 wake_cmd=shlex.join([sys.executable, str(binary)]), wake_timeout_seconds=5)
    args.once = False
    args.wake_host_failure = True
    return args, count


@pytest.mark.parametrize("change", ["restart", "todo_and_token", "config", "disable", "corrupt", "forged_budget"])
def test_hard_failure_cannot_be_reset_by_restart_or_new_work(tmp_path, monkeypatch, change):
    args, count = _guarded_host(tmp_path)
    payload = _hint_payload(should_run=True)
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: payload)
    assert worker.run_worker(args) == 2
    state = json.loads(Path(args.state_file).read_text())
    assert state["host_attempt"]["host_failure"]["kind"] == "quota_exhausted"
    assert "PRIVATE-SENTINEL" not in json.dumps(state)
    if change == "todo_and_token":
        payload["selected_todo"] = {"todo_id": "different-todo"}
        payload["scheduler_hint"]["reset_policy"]["reset_token"] = "new-token"
    elif change == "config":
        args.wake_cmd += " --changed-config"
    elif change == "disable":
        args.wake_host_failure = False
    elif change == "corrupt":
        Path(args.state_file).write_text("broken-state")
    elif change == "forged_budget":
        state["host_attempt"]["host_failure"]["retry"] = {"max_attempts": 999}
        Path(args.state_file).write_text(json.dumps(state))
    assert worker.run_worker(args) == 2
    assert count.read_text() == "1"


@pytest.mark.parametrize("recover_at,expected_calls,expected_result", [(None, 3, 2), (2, 2, 0)])
def test_transient_retry_is_bounded_and_resumes_original_session(
    tmp_path, monkeypatch, recover_at, expected_calls, expected_result,
):
    args, count = _guarded_host(tmp_path, kind="provider_capacity", recover_at=recover_at)
    now = [1000.0]
    delays = []
    def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds
    def quota(*a, **kw):
        finished = recover_at is not None and count.exists() and int(count.read_text()) >= recover_at
        return _hint_payload(should_run=not finished, local_scheduler_directive="stop" if finished else None)
    monkeypatch.setattr(worker, "run_quota_should_run", quota)
    assert worker.run_worker(args, sleep=sleep, clock=lambda: now[0]) == expected_result
    assert count.read_text() == str(expected_calls)
    assert delays == ([30.0, 60.0] if recover_at is None else [30.0])
    state = json.loads(Path(args.state_file).read_text())
    if recover_at is None:
        assert state["host_attempt"]["attempt"] == 3
        assert worker.run_worker(args, sleep=sleep, clock=lambda: now[0]) == 2
        assert count.read_text() == "3"
    else:
        assert "host_attempt" not in state
        assert state["last_wake_status"] == "wake_ok"


def test_transient_without_session_proof_is_held(tmp_path, monkeypatch):
    args, count = _guarded_host(tmp_path, kind="provider_capacity", session=None)
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(should_run=True))
    assert worker.run_worker(args) == 2
    assert count.read_text() == "1"


def test_interruption_is_durable_before_wake_and_core_stop_still_wins(tmp_path, monkeypatch):
    args, count = _guarded_host(tmp_path)
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(should_run=True))
    def interrupt(*a, **kw):
        assert json.loads(Path(args.state_file).read_text())["host_attempt"]["status"] == "started"
        raise KeyboardInterrupt
    monkeypatch.setattr(worker, "_run_wake", interrupt)
    with pytest.raises(KeyboardInterrupt):
        worker.run_worker(args)
    assert worker.run_worker(args) == 2
    assert not count.exists()
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(should_run=False, local_scheduler_directive="stop"))
    assert worker.run_worker(args) == 0
    assert json.loads(Path(args.state_file).read_text())["host_attempt"]["status"] == "started"


def test_guarded_worker_single_consumer_lock_refuses_second_dispatch(tmp_path, monkeypatch):
    from loopx.file_lock import try_exclusive_file_lock
    args, count = _guarded_host(tmp_path)
    with try_exclusive_file_lock(Path(args.state_file)) as held:
        assert held is not None
        assert worker.run_worker(args) == 2
    assert not count.exists()


@pytest.mark.parametrize("failure_kind", ["timeout", "output_limit"])
def test_capped_transport_failure_never_becomes_retryable_provider_result(tmp_path, monkeypatch, failure_kind):
    from loopx.extensions.process_runtime import CappedProcessResult
    args, _ = _guarded_host(tmp_path, kind="provider_capacity")
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(should_run=True))
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: CappedProcessResult(1, b'{}', failure_kind))
    assert worker.run_worker(args) == 2
    failure = json.loads(Path(args.state_file).read_text())["host_attempt"]["host_failure"]
    assert failure["kind"] == "unknown" and failure["retryable"] is False


@pytest.mark.parametrize("state_text", ["broken-state", "[]", '{"host_attempt":null}'])
@pytest.mark.parametrize("terminal", [False, True])
def test_invalid_host_state_never_prevents_core_stop_readback(tmp_path, monkeypatch, state_text, terminal):
    args, count = _guarded_host(tmp_path)
    original = Path(args.state_file)
    original.write_text(state_text)
    reads = []
    def quota(*a, **kw):
        reads.append(True)
        return _hint_payload(should_run=not terminal, local_scheduler_directive="stop" if terminal else None)
    monkeypatch.setattr(worker, "run_quota_should_run", quota)
    assert worker.run_worker(args) == (0 if terminal else 2)
    assert reads == [True] and not count.exists()
    assert original.read_text() == state_text


def test_observe_only_can_read_stop_without_erasing_unknown_attempt(tmp_path, monkeypatch):
    args, count = _guarded_host(tmp_path)
    args.wake_cmd = None
    prior = {"host_attempt": {"status": "started", "scope": "old-scope", "attempt": 1}}
    Path(args.state_file).write_text(json.dumps(prior))
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(
        should_run=False, local_scheduler_directive="stop"))
    assert worker.run_worker(args) == 0
    assert json.loads(Path(args.state_file).read_text())["host_attempt"] == prior["host_attempt"]
    assert not count.exists()


def test_core_deadline_during_retry_backoff_does_not_launch_again(tmp_path, monkeypatch):
    args, count = _guarded_host(tmp_path, kind="provider_capacity")
    now = [1000.0]
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(
        should_run=now[0] < 1010, local_scheduler_directive="stop" if now[0] >= 1010 else None))
    assert worker.run_worker(args, clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)) == 0
    assert count.read_text() == "1"
    assert json.loads(Path(args.state_file).read_text())["host_attempt"]["attempt"] == 1


@pytest.mark.parametrize("stdout,returncode", [(b'{"ok":true}', 0), (b'{"ok":false}', 1),
                                               (b'{"ok":true,"session":', 0), (b'{}', 0)])
def test_unconfirmed_receipt_never_clears_original_session(tmp_path, monkeypatch, stdout, returncode):
    from loopx.extensions.process_runtime import CappedProcessResult
    args, count = _guarded_host(tmp_path, kind="provider_capacity")
    now = [1000.0]
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(should_run=True))
    original = worker._run_wake
    def response(*a, **kw):
        if not count.exists():
            return original(*a, **kw)
        return CappedProcessResult(returncode, stdout, None)
    monkeypatch.setattr(worker, "_run_wake", response)
    assert worker.run_worker(args, clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds)) == 2
    state = json.loads(Path(args.state_file).read_text())["host_attempt"]
    assert state["host_failure"]["kind"] == "unknown"
    assert state["session_id"] == "session-a" and state["attempt"] == 2


def test_guarded_cli_restart_keeps_hard_failure_and_public_safe_liveness(tmp_path):
    args, count = _guarded_host(tmp_path)
    payload = json.dumps(_hint_payload(should_run=True))
    _write_executable(Path(args.cli_bin), f"#!{sys.executable}\nprint({payload!r})\n")
    command = [sys.executable, str(SCRIPTS / "external_scheduler_worker.py"),
               "--cli-bin", args.cli_bin, "--registry", args.registry,
               "--goal-id", args.goal_id, "--agent-id", args.agent_id,
               "--state-file", args.state_file, "--wake-cmd", args.wake_cmd,
               "--wake-host-failure", "--once"]
    first = subprocess.run(command, capture_output=True, text=True, timeout=15)
    second = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert first.returncode == 1 and second.returncode == 2, first.stderr + second.stderr
    assert "host_failure_hold" in second.stdout
    assert count.read_text() == "1"
    assert "PRIVATE-SENTINEL" not in first.stdout + second.stdout + Path(args.state_file).read_text()


def test_explicit_host_stop_is_readable_on_restart_without_new_dispatch(tmp_path, monkeypatch, capsys):
    from loopx.extensions.process_runtime import CappedProcessResult
    args, _ = _guarded_host(tmp_path)
    args.wake_stop_exit_code = 75
    calls = []
    def stopped(*a, **kw):
        calls.append(True)
        return CappedProcessResult(75, b'{"ok":true,"budget_exhausted":true,"host_invoked":false}', None)
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _hint_payload(should_run=True))
    monkeypatch.setattr(worker, "_run_wake", stopped)
    assert worker.run_worker(args) == 0
    assert worker.run_worker(args) == 0
    assert calls == [True]
    assert "goal_complete=false" in capsys.readouterr().out
    # A changed command/stop policy is not permission to discard the old result.
    args.wake_stop_exit_code = None
    assert worker.run_worker(args) == 2
    assert calls == [True]
