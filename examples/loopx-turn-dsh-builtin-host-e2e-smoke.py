#!/usr/bin/env python3
"""End-to-end qualification for the built-in ``--host dsh`` Turn path.

Reuses the generic-cli e2e fixture but drives ``loopx turn run-once`` with the
in-process dsh host instead of a subprocess adapter, proving two legs without
a DeepSeek Harness SDK, network access, or credentials:

1. success: planner and scheduler accept the ``dsh`` host, one bounded fake
   attempt passes independent validation, writeback commits, quota spends
   exactly once, and a replay stays idempotent;
2. terminal provider failure: the fake runner returns an SDK-compatible
   envelope (``finish_reason="error"`` plus a ``turn/end`` reason carrying the
   provider-specific ``MODEL_AT_CAPACITY`` code), the Turn journal records
   attempts 1/2/3 with the bounded backoff contract, and an exhausted fourth
   retry invokes no Host; no failure attempt writes state, spends quota, or
   persists provider prose.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_BASE_PATH = REPO_ROOT / "examples" / "loopx-turn-dsh-e2e-smoke.py"
_SPEC = importlib.util.spec_from_file_location("loopx_turn_dsh_e2e_smoke", _BASE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_base = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _base
_SPEC.loader.exec_module(_base)


def _builtin_argv(
    *,
    registry: Path,
    runtime: Path,
    workspace: Path,
    runner: Path,
    turn_instance_id: str,
    timeout_seconds: float,
) -> list[str]:
    return [
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "turn",
        "run-once",
        "--goal-id",
        _base.GOAL_ID,
        "--agent-id",
        _base.AGENT_ID,
        "--turn-instance-id",
        turn_instance_id,
        "--host",
        "dsh",
        "--execution-mode",
        "isolated-headless",
        "--project",
        str(workspace),
        "--dsh-runner",
        str(runner),
        "--validation-command-json",
        json.dumps(_base._validator_command()),
        "--validation-failure-kind",
        "repair_required",
        "--scan-root",
        str(registry.parent.parent),
        "--no-global-sync",
        "--timeout-seconds",
        str(timeout_seconds),
    ]


def _write_capacity_runner(root: Path) -> tuple[Path, Path]:
    outcome: dict[str, Any] = {
        "final_response": "",
        "finish_reason": "error",
        "events": [
            {
                "type": "turn/end",
                "data": {
                    "reason": {
                        "kind": "error",
                        "error": {
                            "code": "MODEL_AT_CAPACITY",
                            "status": 503,
                            "message": "selected model is at capacity",
                        },
                    }
                },
            }
        ],
    }
    runner = root / "capacity_dsh_runner.py"
    counter = root / "capacity-dsh-invocations.txt"
    runner.write_text(
        "from pathlib import Path\n"
        f"COUNTER = Path({str(counter)!r})\n"
        "def run_dsh_turn(**kwargs):\n"
        "    previous = int(COUNTER.read_text() or '0') if COUNTER.exists() else 0\n"
        "    COUNTER.write_text(str(previous + 1), encoding='utf-8')\n"
        f"    return {outcome!r}\n",
        encoding="utf-8",
    )
    return runner, counter


def _contains_text(roots: list[Path], needle: str) -> bool:
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if needle in text:
                return True
    return False


def _success_leg(timeout_seconds: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="loopx-turn-dsh-builtin-") as directory:
        root = Path(directory)
        _project, runtime, workspace, registry = _base._write_fixture(root)
        runner = _base._write_fake_dsh_runner(root, workspace)
        base = _builtin_argv(
            registry=registry,
            runtime=runtime,
            workspace=workspace,
            runner=runner,
            turn_instance_id="builtin-turn-1",
            timeout_seconds=timeout_seconds,
        )
        exit_code, payload = _base._run_cli([*base, "--execute"])
        marker = workspace / _base.MARKER_NAME
        marker_valid = (
            marker.is_file()
            and marker.read_text(encoding="utf-8").strip() == _base.MARKER_VALUE
        )
        turn_key = payload.get("resume_turn_key")
        replay_exit_code, replay = (None, None)
        if exit_code == 0 and isinstance(turn_key, str):
            replay_base = list(base)
            index = replay_base.index("--turn-instance-id")
            del replay_base[index : index + 2]
            replay_exit_code, replay = _base._run_cli(
                [*replay_base, "--resume-turn-key", turn_key, "--execute"]
            )
        return {
            "exit_code": exit_code,
            "status": payload.get("status"),
            "result_kind": payload.get("result_kind"),
            "validation": payload.get("validation"),
            "effects": payload.get("effects"),
            "marker_valid": marker_valid,
            "quota_slot_spend_count": _base._quota_spend_count(runtime),
            "replay_exit_code": replay_exit_code,
            "replay_effects": (
                replay.get("effects") if isinstance(replay, dict) else None
            ),
        }


def _capacity_leg(timeout_seconds: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="loopx-turn-dsh-capacity-") as directory:
        root = Path(directory)
        project, runtime, workspace, registry = _base._write_fixture(root)
        runner, counter = _write_capacity_runner(root)
        argv = _builtin_argv(
            registry=registry,
            runtime=runtime,
            workspace=workspace,
            runner=runner,
            turn_instance_id="builtin-capacity-1",
            timeout_seconds=timeout_seconds,
        )
        initial_exit_code, initial_payload = _base._run_cli([*argv, "--execute"])
        attempt_payloads = [initial_payload]
        turn_key = initial_payload.get("resume_turn_key")
        exhausted_exit_code = None
        exhausted_payload: dict[str, Any] = {}
        if isinstance(turn_key, str):
            retry_argv = list(argv)
            index = retry_argv.index("--turn-instance-id")
            del retry_argv[index : index + 2]
            for _attempt in (2, 3):
                retry_exit_code, retry_payload = _base._run_cli(
                    [
                        *retry_argv,
                        "--resume-turn-key",
                        turn_key,
                        "--retry-failed-turn",
                        "--execute",
                    ]
                )
                attempt_payloads.append(retry_payload)
                if retry_exit_code != 1:
                    break
            exhausted_exit_code, exhausted_payload = _base._run_cli(
                [
                    *retry_argv,
                    "--resume-turn-key",
                    turn_key,
                    "--retry-failed-turn",
                    "--execute",
                ]
            )

        return {
            "initial_exit_code": initial_exit_code,
            "initial_result_kind": initial_payload.get("result_kind"),
            "failure_records": [
                payload.get("host_failure") for payload in attempt_payloads
            ],
            "failure_effects": [
                payload.get("effects") for payload in attempt_payloads
            ],
            "quota_slot_spend_count": _base._quota_spend_count(runtime),
            "exhausted_exit_code": exhausted_exit_code,
            "exhausted_effects": exhausted_payload.get("effects"),
            "exhausted_effects_scope": exhausted_payload.get("effects_scope"),
            "exhausted_journal_observation": exhausted_payload.get("journal_observation"),
            "exhausted_recovery_decision": exhausted_payload.get(
                "recovery_decision"
            ),
            "host_invocation_count": (
                int(counter.read_text(encoding="utf-8")) if counter.is_file() else 0
            ),
            "raw_provider_prose_persisted": _contains_text(
                [project, runtime, workspace],
                "selected model is at capacity",
            ),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()

    success = _success_leg(args.timeout_seconds)
    capacity = _capacity_leg(args.timeout_seconds)
    summary = {
        "schema_version": "loopx_turn_dsh_builtin_host_e2e_v0",
        "real_dsh_invoked": False,
        "success_leg": success,
        "capacity_leg": capacity,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    expected_effects = {
        "host_invoked": True,
        "state_written": True,
        "quota_spent": True,
        "scheduler_acknowledged": False,
    }
    expected_replay_effects = {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    expected_failure_effects = {
        "host_invoked": True,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    expected_exhausted_effects = {
        "host_invoked": None,
        "state_written": None,
        "quota_spent": None,
        "scheduler_acknowledged": None,
    }
    expected_failure_records = [
        {
            "schema_version": "loopx_turn_host_failure_v0",
            "kind": "provider_capacity",
            "attempt": attempt,
            "retryable": True,
            "retry": {
                "strategy": "same_configuration",
                "max_attempts": 3,
                "backoff_seconds": backoff,
            },
        }
        for attempt, backoff in ((1, 30), (2, 60), (3, 120))
    ]
    exhausted_decision = capacity["exhausted_recovery_decision"] or {}
    ok = (
        success["exit_code"] == 0
        and success["status"] == "committed"
        and success["result_kind"] == "validated_progress"
        and (success["validation"] or {}).get("status") == "passed"
        and success["effects"] == expected_effects
        and success["marker_valid"]
        and success["quota_slot_spend_count"] == 1
        and success["replay_exit_code"] == 0
        and success["replay_effects"] == expected_replay_effects
        and capacity["initial_exit_code"] == 1
        and capacity["initial_result_kind"] == "host_failure"
        and capacity["failure_records"] == expected_failure_records
        and capacity["failure_effects"]
        == [expected_failure_effects] * len(expected_failure_records)
        and capacity["quota_slot_spend_count"] == 0
        and capacity["exhausted_exit_code"] == 1
        and capacity["exhausted_effects"] == expected_exhausted_effects
        and capacity["exhausted_effects_scope"] == "current_invocation"
        and capacity["exhausted_journal_observation"]["scope"] == "original_turn"
        and capacity["exhausted_journal_observation"]["recorded_effects"]["quota_spent"] is False
        and exhausted_decision.get("reason") == "host_retry_budget_exhausted"
        and exhausted_decision.get("reinvoke_host") is False
        and capacity["host_invocation_count"] == 3
        and capacity["raw_provider_prose_persisted"] is False
    )
    if not ok:
        raise SystemExit(f"built-in dsh host e2e failed: {summary}")
    print("built-in dsh host e2e passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
