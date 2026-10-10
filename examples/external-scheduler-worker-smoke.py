#!/usr/bin/env python3
"""Smoke for the scheduler-hint-aware external shell_worker wrapper.

Guards the reusable contract without launching cron/launchd or invoking the
LoopX CLI: parsing a quota should-run payload, advancing the unchanged-poll
ladder, resetting on a new reset token, and stopping at terminal actions.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import shlex
import stat
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
for path in (str(REPO_ROOT), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import external_scheduler_worker as worker  # noqa: E402
from external_scheduler_worker import parse_tick, run_worker, select_interval  # noqa: E402


def _hint_payload(
    *,
    action: str,
    initial: int,
    progression: list[int],
    limit: int | None,
    after_limit: str = "stop_tick_loop",
    reset_token: str = "tok-1",
    should_run: bool = False,
    cadence_class: str = "quiet_wait",
    reason: str = "synthetic",
    local_scheduler_directive: str | None = None,
) -> dict:
    scheduler_hint = {
        "action": action,
        "cadence_class": cadence_class,
        "reason": reason,
        "reset_policy": {"reset_token": reset_token},
        "cold_path_detail": {
            "local_scheduler": {
                "recommended_interval_minutes": initial,
                "example_progression_minutes": progression,
                "unchanged_poll_limit": limit,
                "after_limit": after_limit,
                "final_quota_replan_check": {
                    "enabled": limit is not None,
                    "trigger": "before_unchanged_poll_after_limit",
                    "action": "rerun_quota_should_run_once",
                    "if_changed": "follow_new_scheduler_hint",
                    "if_run_now": "execute_new_quota_contract",
                    "if_unchanged": "apply_after_limit_without_spend",
                },
            }
        },
    }
    if local_scheduler_directive is not None:
        scheduler_hint["unchanged_poll"] = {
            "local_scheduler": local_scheduler_directive
        }
    return {
        "should_run": should_run,
        "effective_action": action,
        "scheduler_hint": scheduler_hint,
    }


def test_active_observation_uses_initial_interval() -> None:
    payload = _hint_payload(
        action="run_now",
        initial=3,
        progression=[3, 10],
        limit=None,
        should_run=True,
        cadence_class="active_work",
    )
    decision = parse_tick(payload)
    assert decision.should_run is True
    interval, phase = select_interval(decision, unchanged_count=0)
    assert interval == 3
    assert phase == "active"


def test_unchanged_poll_advances_ladder_then_stops() -> None:
    payload = _hint_payload(
        action="backoff_until_state_change",
        initial=30,
        progression=[30, 60, 120],
        limit=3,
    )
    decision = parse_tick(payload)
    assert decision.should_run is False
    assert decision.unchanged_limit == 3
    assert decision.after_limit == "stop_tick_loop"

    seen = [
        select_interval(decision, unchanged_count=i)[0] for i in range(3)
    ]
    assert seen == [30, 60, 120]


def test_new_reset_token_resets_progression() -> None:
    first = parse_tick(_hint_payload(action="backoff", initial=10, progression=[10, 20], limit=3, reset_token="a"))
    second = parse_tick(_hint_payload(action="backoff", initial=10, progression=[10, 20], limit=3, reset_token="b"))
    assert first.reset_token == "a"
    assert second.reset_token == "b"
    assert second.reset_token != first.reset_token
    # Caller resets unchanged_count to 0 when the token changes; verify index 0.
    interval, _ = select_interval(second, unchanged_count=0)
    assert interval == 10


def test_terminal_action_is_terminal() -> None:
    for action in (
        "stop_until_explicit_resume",
        "return_to_owner_until_material_change",
    ):
        payload = _hint_payload(
            action=action,
            initial=0,
            progression=[0],
            limit=None,
            cadence_class="terminal_no_followup",
            local_scheduler_directive="stop",
        )
        del payload["scheduler_hint"]["cold_path_detail"]
        decision = parse_tick(payload)
        assert decision.terminal is True
        assert decision.action == action
        assert decision.after_limit == "stop_tick_loop"
        assert decision.unchanged_limit is None


def test_missing_detail_fails_closed() -> None:
    payload = {
        "should_run": False,
        "scheduler_hint": {"action": "backoff", "cadence_class": "quiet_wait"},
    }
    try:
        parse_tick(payload)
    except ValueError as exc:
        assert "include-detail scheduler" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("parse_tick must fail closed without scheduler detail")


def test_stop_limit_without_final_probe_fails_closed() -> None:
    payload = _hint_payload(
        action="backoff",
        initial=10,
        progression=[10, 20],
        limit=2,
    )
    del payload["scheduler_hint"]["cold_path_detail"]["local_scheduler"][
        "final_quota_replan_check"
    ]
    try:
        parse_tick(payload)
    except ValueError as exc:
        assert "final_quota_replan_check" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("stop limit must require its final quota/replan probe")


def _write_fake_cli(path: Path, payloads: list[dict]) -> None:
    data = json.dumps(payloads)
    script = (
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"payloads = json.loads({data!r})\n"
        "state_path = sys.argv[sys.argv.index('--registry') + 1]\n"
        "try:\n"
        "    with open(state_path) as f:\n"
        "        idx = int(f.read().strip() or '0')\n"
        "except (OSError, ValueError):\n"
        "    idx = 0\n"
        "payload = payloads[min(idx, len(payloads) - 1)]\n"
        "with open(state_path, 'w') as f:\n"
        "    f.write(str(idx + 1))\n"
        "print(json.dumps(payload))\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_launchd_program_arguments_match_worker_entrypoint(tmp_path: Path) -> None:
    terminal = _hint_payload(
        action="stop_until_explicit_resume",
        initial=0,
        progression=[0],
        limit=None,
        cadence_class="terminal_no_followup",
        local_scheduler_directive="stop",
    )
    del terminal["scheduler_hint"]["cold_path_detail"]
    fake_cli = tmp_path / "launchd" / "fake-loopx"
    _write_fake_cli(fake_cli, [terminal])

    replacements = {
        "__LABEL__": "com.loopx.external-worker-smoke",
        "__WORKER__": str(REPO_ROOT / "scripts" / "external_scheduler_worker.py"),
        "__LOOPX_BIN__": str(fake_cli),
        "__REGISTRY__": str(tmp_path / "launchd" / "registry"),
        "__RUNTIME_ROOT__": str(tmp_path / "launchd" / "runtime"),
        "__GOAL_ID__": "goal-launchd",
        "__AGENT_ID__": "agent-launchd",
        "__STATE__": str(tmp_path / "launchd" / "worker-state.json"),
        "__LOG__": str(tmp_path / "launchd" / "worker.log"),
    }
    template_path = REPO_ROOT / "examples" / "external-scheduler-worker.launchd.plist"
    template = plistlib.loads(template_path.read_bytes())

    def replace_placeholders(value: str) -> str:
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        return value

    template["Label"] = replace_placeholders(template["Label"])
    template["StandardOutPath"] = replace_placeholders(template["StandardOutPath"])
    template["StandardErrorPath"] = replace_placeholders(template["StandardErrorPath"])
    program_arguments = [
        replace_placeholders(argument) for argument in template["ProgramArguments"]
    ]
    template["ProgramArguments"] = program_arguments

    assert b"__" not in plistlib.dumps(template)
    assert program_arguments[:3] == [
        "/usr/bin/env",
        "python3",
        replacements["__WORKER__"],
    ]
    assert template["KeepAlive"] == {"SuccessfulExit": False}
    assert worker.main(program_arguments[3:]) == 0


def test_end_to_end_loop_stops_after_limit(tmp_path: Path) -> None:
    """Drives run_worker with a fake CLI; proves CLI call, state file, and stop."""

    def waiting(token: str) -> dict:
        return _hint_payload(
            action="backoff_until_state_change",
            initial=30,
            progression=[30, 60, 120],
            limit=2,
            reset_token=token,
        )

    registry = tmp_path / "limit" / "registry"
    state_file = tmp_path / "limit" / "worker-state.json"
    fake_cli = tmp_path / "limit" / "fake-loopx"
    _write_fake_cli(fake_cli, [waiting("tok-a"), waiting("tok-a"), waiting("tok-a")])

    slept: list[float] = []
    args = argparse.Namespace(
        cli_bin=str(fake_cli),
        registry=str(registry),
        runtime_root=None,
        runtime_profile="generic_cli",
        goal_id="g",
        agent_id="a",
        state_file=str(state_file),
        wake_cmd=None,
        once=False,
        error_backoff_seconds=5.0,
    )
    rc = run_worker(args, sleep=slept.append)

    assert rc == 0
    # Tick 1 waits at progression[0]; tick 2 reaches the limit and stops before
    # sleeping, so only the first interval is actually slept.
    assert slept == [30 * 60]
    persisted = json.loads(state_file.read_text())
    assert persisted["unchanged_count"] == 2
    assert persisted["reset_token"] == "tok-a"


def test_end_to_end_token_change_resets_count(tmp_path: Path) -> None:
    def waiting(token: str) -> dict:
        return _hint_payload(
            action="backoff",
            initial=10,
            progression=[10, 20],
            limit=None,
            reset_token=token,
        )

    registry = tmp_path / "token" / "registry"
    state_file = tmp_path / "token" / "worker-state.json"
    fake_cli = tmp_path / "token" / "fake-loopx"
    _write_fake_cli(
        fake_cli,
        [waiting("tok-a"), waiting("tok-b"), waiting("tok-b"), waiting("tok-b")],
    )

    slept: list[float] = []
    args = argparse.Namespace(
        cli_bin=str(fake_cli),
        registry=str(registry),
        runtime_root=None,
        runtime_profile="generic_cli",
        goal_id="g",
        agent_id="a",
        state_file=str(state_file),
        wake_cmd=None,
        once=False,
        error_backoff_seconds=5.0,
    )
    # Stop after 4 real ticks by raising once sleep budget is exhausted.
    def stop_after_four(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= 4:
            raise KeyboardInterrupt
    try:
        run_worker(args, sleep=stop_after_four)
    except KeyboardInterrupt:
        pass

    # Tick1 marker a -> 10; tick2 marker b resets -> 10; later ticks -> 20.
    assert slept == [10 * 60, 10 * 60, 20 * 60, 20 * 60]


def test_end_to_end_terminal_stops_immediately(tmp_path: Path) -> None:
    for action in (
        "stop_until_explicit_resume",
        "return_to_owner_until_material_change",
    ):
        terminal = _hint_payload(
            action=action,
            initial=0,
            progression=[0],
            limit=None,
            cadence_class="terminal_no_followup",
            local_scheduler_directive="stop",
        )
        del terminal["scheduler_hint"]["cold_path_detail"]
        root = tmp_path / action
        registry = root / "registry"
        state_file = root / "worker-state.json"
        fake_cli = root / "fake-loopx"
        _write_fake_cli(fake_cli, [terminal])

        slept: list[float] = []
        args = argparse.Namespace(
            cli_bin=str(fake_cli),
            registry=str(registry),
            runtime_root=None,
            runtime_profile="generic_cli",
            goal_id="g",
            agent_id="a",
            state_file=str(state_file),
            wake_cmd=None,
            once=False,
            error_backoff_seconds=5.0,
        )
        rc = run_worker(args, sleep=slept.append)

        assert rc == 0
        assert slept == []
        persisted = json.loads(state_file.read_text())
        assert persisted["unchanged_count"] == 0


def test_end_to_end_should_run_invokes_wake_cmd(tmp_path: Path) -> None:
    active = _hint_payload(
        action="run_now",
        initial=3,
        progression=[3],
        limit=None,
        should_run=True,
        cadence_class="active_work",
        reset_token="tok-run",
    )
    registry = tmp_path / "wake" / "registry"
    state_file = tmp_path / "wake" / "worker-state.json"
    fake_cli = tmp_path / "wake" / "fake-loopx"
    marker = tmp_path / "wake" / "marker.txt"
    terminal = _hint_payload(
        action="stop_until_explicit_resume",
        initial=3, progression=[3], limit=None,
        local_scheduler_directive="stop",
    )
    _write_fake_cli(fake_cli, [active, terminal])
    slept: list[float] = []
    args = argparse.Namespace(
        cli_bin=str(fake_cli),
        registry=str(registry),
        runtime_root=None,
        runtime_profile="generic_cli",
        goal_id="g",
        agent_id="a",
        state_file=str(state_file),
        wake_cmd=f"echo woke > {shlex.quote(str(marker))}",
        once=False,
        error_backoff_seconds=5.0,
    )
    assert run_worker(args, sleep=slept.append) == 0
    assert slept == []

    assert marker.exists()
    assert marker.read_text().strip() == "woke"
    persisted = json.loads(state_file.read_text())
    assert persisted["unchanged_count"] == 0


def test_end_to_end_failed_wake_returns_nonzero_once(tmp_path: Path) -> None:
    """A non-zero wake_cmd is a failed delivery, not a healthy tick."""
    active = _hint_payload(
        action="run_now",
        initial=3,
        progression=[3],
        limit=None,
        should_run=True,
        cadence_class="active_work",
        reset_token="tok-run",
    )
    registry = tmp_path / "wakefail" / "registry"
    state_file = tmp_path / "wakefail" / "worker-state.json"
    fake_cli = tmp_path / "wakefail" / "fake-loopx"
    _write_fake_cli(fake_cli, [active])

    slept: list[float] = []
    args = argparse.Namespace(
        cli_bin=str(fake_cli),
        registry=str(registry),
        runtime_root=None,
        runtime_profile="generic_cli",
        goal_id="g",
        agent_id="a",
        state_file=str(state_file),
        wake_cmd="exit 17",
        once=True,
        error_backoff_seconds=5.0,
    )
    rc = run_worker(args, sleep=slept.append)

    assert slept == []
    assert rc == 17  # propagate the wake command's non-zero exit
    persisted = json.loads(state_file.read_text())
    # The failed wake must not be counted as successful progress.
    assert persisted["last_wake_status"] == "wake_failed"
    assert persisted["last_wake_rc"] == 17
    assert persisted["unchanged_count"] == 0


def main() -> int:
    tests = [
        test_active_observation_uses_initial_interval,
        test_unchanged_poll_advances_ladder_then_stops,
        test_new_reset_token_resets_progression,
        test_terminal_action_is_terminal,
        test_missing_detail_fails_closed,
        test_stop_limit_without_final_probe_fails_closed,
    ]
    e2e_tests = [
        test_launchd_program_arguments_match_worker_entrypoint,
        test_end_to_end_loop_stops_after_limit,
        test_end_to_end_token_change_resets_count,
        test_end_to_end_terminal_stops_immediately,
        test_end_to_end_should_run_invokes_wake_cmd,
        test_end_to_end_failed_wake_returns_nonzero_once,
    ]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for test in tests:
            test()
            print(f"ok {test.__name__}")
        for test in e2e_tests:
            test(tmp_path)
            print(f"ok {test.__name__}")
    total = len(tests) + len(e2e_tests)
    print(f"{total} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
