from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
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
