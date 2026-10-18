"""Thin bounded-wait parity contract for shell_worker and Pi consumers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import external_scheduler_worker as worker  # noqa: E402
from loopx.control_plane.scheduler.execution_context import (  # noqa: E402
    scheduler_execution_context_for_runtime_profile,
)
from tests.control_plane.scheduler_arbitration_fixture import (  # noqa: E402
    build_native_scheduler_hint as build_scheduler_hint,
)


class _WaitScheduled(Exception):
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds


def _monitor_wait_payload() -> dict[str, object]:
    return {
        "goal_id": "scheduler-parity-fixture",
        "agent_identity": {"agent_id": "parity-agent"},
        "should_run": False,
        "effective_action": "monitor_quiet_skip",
        "recommended_action": "Wait for a material transition.",
        "heartbeat_recommendation": {
            "recommended_mode": "monitor_quiet_until_material_transition",
            "spend_policy": "no spend for quiet monitor waits",
        },
        "execution_obligation": {
            "must_attempt_work": False,
            "spend_policy": "no spend for quiet monitor waits",
        },
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": "monitor_quiet_skip",
            "user_channel": {
                "action_required": False,
                "notify": "DONT_NOTIFY",
            },
            "agent_channel": {
                "must_attempt": False,
                "delivery_allowed": False,
                "quiet_noop_allowed": True,
            },
            "cli_channel": {
                "next_cli_actions": [],
                "spend_allowed_now": False,
            },
        },
    }


@pytest.fixture
def canonical_wait_payload() -> dict[str, object]:
    hint = build_scheduler_hint(
        _monitor_wait_payload(),
        include_detail=True,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "generic_cli"
        ),
    )
    unchanged = hint["unchanged_poll"]
    assert unchanged["limits"]["local_scheduler"] == 3
    assert unchanged["after_limits"]["local_scheduler"] == "stop_tick_loop"
    assert unchanged["final_quota_replan_check_enabled"] is True
    assert (
        unchanged["final_quota_replan_check_action"]
        == "rerun_quota_should_run_once"
    )
    return {
        "should_run": False,
        "effective_action": "monitor_quiet_skip",
        "scheduler_hint": hint,
    }


def _write_fake_cli(path: Path, payload: dict[str, object]) -> None:
    source = "#!/usr/bin/env python3\n" f"print({json.dumps(payload)!r})\n"
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _worker_args(*, cli: Path, state: Path) -> argparse.Namespace:
    return argparse.Namespace(
        cli_bin=str(cli),
        registry=str(state.parent / "registry"),
        runtime_root=None,
        runtime_profile="generic_cli",
        goal_id="scheduler-parity-fixture",
        agent_id="parity-agent",
        state_file=str(state),
        wake_cmd=None,
        once=False,
        error_backoff_seconds=5.0,
        quota_timeout_seconds=5.0,
        wake_timeout_seconds=5.0,
    )


def _shell_plan(
    payload: dict[str, object],
    *,
    prior: int,
    prior_token: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    cli = tmp_path / "fake-loopx"
    state = tmp_path / "state.json"
    _write_fake_cli(cli, payload)
    state.write_text(
        json.dumps({"reset_token": prior_token, "unchanged_count": prior}),
        encoding="utf-8",
    )

    def capture_sleep(seconds: float) -> None:
        raise _WaitScheduled(seconds)

    # Inject this worker invocation's wait without mutating the process-global
    # ``time`` module, which may be in use by another full-suite thread.
    try:
        return_code = worker.run_worker(
            _worker_args(cli=cli, state=state), sleep=capture_sleep
        )
    except _WaitScheduled as scheduled:
        return {"stop": False, "minutes": int(scheduled.seconds / 60)}
    assert return_code == 0
    return {"stop": True}


def _pi_plan(
    payload: dict[str, object], *, prior: int, prior_token: str
) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for Pi scheduler-consumer parity")
    runtime_uri = (REPO_ROOT / "loopx/pi_goal_mode/pi-goal-loop-runtime.mjs").as_uri()
    probe = f"""
import {{ waitPlan }} from {json.dumps(runtime_uri)}
let raw = ""
for await (const chunk of process.stdin) raw += chunk
const input = JSON.parse(raw)
process.stdout.write(JSON.stringify(waitPlan(input.decision, input.binding)))
"""
    completed = subprocess.run(
        [node, "--input-type=module", "--eval", probe],
        input=json.dumps(
            {
                "decision": payload,
                "binding": {
                    "schedulerToken": prior_token,
                    "unchangedPolls": prior,
                },
            }
        ),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    plan = json.loads(completed.stdout)
    if plan["stop"]:
        return {"stop": True}
    return {"stop": False, "minutes": plan["minutes"]}


@pytest.mark.parametrize("prior", [0, 1, 2, 3, 4])
def test_shell_worker_and_pi_share_bounded_wait_plan(
    canonical_wait_payload: dict[str, object],
    prior: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hint = canonical_wait_payload["scheduler_hint"]
    assert isinstance(hint, dict)
    reset_policy = hint["reset_policy"]
    assert isinstance(reset_policy, dict)
    token = str(reset_policy["reset_token"])
    shell = _shell_plan(
        canonical_wait_payload,
        prior=prior,
        prior_token=token,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    pi = _pi_plan(canonical_wait_payload, prior=prior, prior_token=token)

    assert pi == shell


def test_shell_worker_and_pi_reset_changed_scheduler_identity(
    canonical_wait_payload: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = _shell_plan(
        canonical_wait_payload,
        prior=2,
        prior_token="stale-token",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    pi = _pi_plan(canonical_wait_payload, prior=2, prior_token="stale-token")

    assert shell == pi
    assert shell["stop"] is False
