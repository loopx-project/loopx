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
    quota_timeout_seconds: float = 0.1,
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
        quota_timeout_seconds=1.0,
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
        quota_timeout_seconds=1.0,
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
        )
    )

    assert result == 2
    assert time.monotonic() - started < 2.0


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
            quota_timeout_seconds=1.0,
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
