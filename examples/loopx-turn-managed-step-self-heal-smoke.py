#!/usr/bin/env python3
"""Managed-step demonstration: fail once on capacity, then self-heal same-Turn.

This is the first production consumer of the controller's typed
``retry_continuation``. It drives the public CLI end to end, with no model and
no DeepSeek Harness SDK required:

1. ``turn run-once`` against a hermetic fake dsh runner whose first attempt
   raises ``provider_capacity``. The Turn must fail with a typed, retryable
   host failure and must not spend quota.
2. ``turn managed-step`` on that exact Turn key. It must answer ``wait`` with
   the bounded continuation the journal proves (30s, attempt 1/3), and must
   itself write nothing and spend nothing.
3. ``turn run-once --retry-failed-turn --resume-turn-key ...`` with the fake
   runner now succeeding. The same Turn key must commit, the independent
   validator must pass, and the goal must have spent exactly one quota slot.

The point is the accounting: a retryable failure costs nothing, the retry costs
exactly one slot, and both facts come from the journal rather than from the
caller's bookkeeping.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as cli_main  # noqa: E402

GOAL_ID = "loopx-turn-managed-step"
AGENT_ID = "managed-step-agent"
TODO_ID = "todo_managedstep01"
MARKER_NAME = "docs/managed-step-marker.txt"
MARKER_VALUE = "loopx-managed-step-self-healed"

# The fake runner reads this state file to decide whether to fail. It is the
# only thing that changes between the first attempt and the retry.
ATTEMPTS_FILE = "attempts.json"


def _write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    workspace = project
    runtime.mkdir(parents=True)
    workspace.mkdir(parents=True)
    (workspace / "docs").mkdir()
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "\n".join(
            [
                "---",
                "status: active",
                "updated_at: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                "# Managed Step Self-Heal",
                "",
                "## Agent Todo",
                "",
                (
                    f"- [ ] [P0] Produce the managed-step marker; a capacity "
                    f"failure must self-heal on the same Turn. `{MARKER_NAME}`"
                ),
                (
                    f"  <!-- loopx:todo todo_id={TODO_ID} status=open "
                    "task_class=advancement_task action_kind=managed_step_self_heal "
                    f"claimed_by={AGENT_ID} priority=P0 -->"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "loopx-turn-managed-step-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "adapter": {
                            "kind": "fixture_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {"compute": 1.0, "window_hours": 24},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                            "agent_profiles": {
                                AGENT_ID: {
                                    "schema_version": "agent_profile_v1",
                                    "profile_role": "fixture",
                                    "scope": "public qualification",
                                }
                            },
                            "write_scope": ["docs/**"],
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, runtime, workspace, registry


def _write_fake_runner(root: Path, workspace: Path) -> Path:
    """A runner hook that fails the first attempt and succeeds afterwards.

    ``run_dsh_host`` classifies the raised error through the real
    ``classify_dsh_failure`` path, so this exercises the production failure
    channel rather than a test-only shortcut.
    """

    runner = root / "fake_dsh_runner.py"
    runner.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import json",
                "from pathlib import Path",
                "",
                f"ATTEMPTS = Path({str(workspace / ATTEMPTS_FILE)!r})",
                "",
                "",
                "def run_dsh_turn(*, prompt, session_id=None, **kwargs):  # noqa: ANN001,ARG001",
                "    attempts = (",
                "        int(json.loads(ATTEMPTS.read_text(encoding='utf-8'))['count'])",
                "        if ATTEMPTS.is_file()",
                "        else 0",
                "    )",
                "    attempts += 1",
                "    ATTEMPTS.write_text(json.dumps({'count': attempts}), encoding='utf-8')",
                "    if attempts == 1:",
                "        error = RuntimeError('provider at capacity')",
                "        error.code = 'insufficient_capacity'",
                "        raise error",
                f"    marker = Path(kwargs['workspace']) / {MARKER_NAME!r}",
                "    marker.parent.mkdir(parents=True, exist_ok=True)",
                f"    marker.write_text({MARKER_VALUE!r}, encoding='utf-8')",
                "    payload = {",
                "        'result_kind': 'validated_progress',",
                "        'classification': 'managed_step_fake_runner',",
                "        'summary': 'Same-Turn retry produced the marker.',",
                "        'recommended_action': 'Inspect the marker.',",
                "        'next_action': 'Confirm exactly one quota slot was spent.',",
                "        'vision_unchanged_reason': 'The objective path is unchanged.',",
                "    }",
                "    return json.dumps(payload)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return runner


def _validator_command() -> list[str]:
    program = (
        "import json,pathlib,sys; "
        "json.load(sys.stdin); "
        f"p=pathlib.Path({MARKER_NAME!r}); "
        "raise SystemExit(0 if p.is_file() and "
        f"p.read_text(encoding='utf-8').strip() == {MARKER_VALUE!r} else 9)"
    )
    return [sys.executable, "-c", program]


def _run_cli(argv: list[str]) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(argv)
    payload = json.loads(output.getvalue())
    assert isinstance(payload, dict), payload
    return exit_code, payload


def _resume_argv(base_argv: list[str], turn_key: str) -> list[str]:
    """Resume the exact journaled Turn: the plan is not recomputed.

    ``--turn-instance-id`` identifies a *new* logical Turn, so it must be
    dropped once the caller is replaying a journaled one.
    """

    argv: list[str] = []
    skip_next = False
    for item in base_argv:
        if skip_next:
            skip_next = False
            continue
        if item == "--turn-instance-id":
            skip_next = True
            continue
        argv.append(item)
    return [
        *argv,
        "--retry-failed-turn",
        "--resume-turn-key",
        turn_key,
    ]


def _base_argv(
    *,
    registry: Path,
    runtime: Path,
    workspace: Path,
    runner: Path,
    dsh_home: Path,
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
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "managed-step-self-heal-turn-1",
        "--host",
        "dsh",
        "--execution-mode",
        "isolated-headless",
        "--project",
        str(workspace),
        "--dsh-runner",
        str(runner),
        "--dsh-home",
        str(dsh_home),
        "--dsh-model",
        "mock-model",
        "--validation-command-json",
        json.dumps(_validator_command()),
        "--validation-failure-kind",
        "repair_required",
        "--scan-root",
        str(registry.parent.parent),
        "--no-global-sync",
        "--timeout-seconds",
        "60",
        "--execute",
    ]


def _quota_spend_count(runtime: Path) -> int:
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    if not index.is_file():
        return 0
    return sum(
        1
        for line in index.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("classification") == "quota_slot_spent"
    )


def _journal_path(runtime: Path, turn_key: str) -> Path:
    return (
        runtime
        / "goals"
        / GOAL_ID
        / "turns"
        / f"{turn_key.removeprefix('sha256:')}.json"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="loopx-managed-step-") as directory:
        root = Path(directory)
        _project, runtime, workspace, registry = _write_fixture(root)
        runner = _write_fake_runner(root, workspace)
        dsh_home = root / "dsh-home"
        dsh_home.mkdir(parents=True)
        old_env = {
            key: os.environ.get(key)
            for key in ("DSH_CWD", "DSH_HOME", "DSH_SESSION_ROOT")
        }
        try:
            for key in ("DSH_CWD", "DSH_HOME", "DSH_SESSION_ROOT"):
                os.environ.pop(key, None)

            base = _base_argv(
                registry=registry,
                runtime=runtime,
                workspace=workspace,
                runner=runner,
                dsh_home=dsh_home,
            )

            # 1. The capacity failure must be typed and must not spend.
            exit_code, failed = _run_cli(base)
            assert exit_code == 1, (exit_code, failed)
            assert failed["ok"] is False, failed
            assert failed["status"] == "failed", failed
            assert failed["result_kind"] == "host_failure", failed
            host_failure = failed["host_failure"]
            assert host_failure["kind"] == "provider_capacity", host_failure
            assert host_failure["retryable"] is True, host_failure
            assert host_failure["attempt"] == 1, host_failure
            assert failed["effects"]["quota_spent"] is False, failed
            assert failed["effects"]["state_written"] is False, failed
            assert _quota_spend_count(runtime) == 0, "the failure spent a slot"
            turn_key = failed["resume_turn_key"]
            assert _journal_path(runtime, turn_key).is_file(), turn_key
            spends_after_failure = _quota_spend_count(runtime)

            # 2. The managed step must answer wait without touching anything.
            step_argv = [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    "turn",
                    "managed-step",
                    "--goal-id",
                    GOAL_ID,
                    "--agent-id",
                    AGENT_ID,
                    "--turn-key",
                    turn_key,
                    "--host",
                    "dsh",
                    "--execution-mode",
                    "isolated-headless",
                    "--scan-root",
                    str(registry.parent.parent),
                    "--observed-attempt",
                    "1",
                    "--observed-max-attempts",
                    "3",
                ]
            journal_path = _journal_path(runtime, turn_key)
            journal_before = journal_path.read_bytes()
            _, step = _run_cli(step_argv)
            assert journal_path.read_bytes() == journal_before
            assert step["ok"] is True, step
            assert step["disposition"] == "wait", step
            continuation = step["retry_continuation"]
            assert continuation["same_turn"] is True, continuation
            assert continuation["retry_failed_turn"] is True, continuation
            assert continuation["strategy"] == "same_configuration", continuation
            assert continuation["retry_after_seconds"] == 30, continuation
            assert continuation["attempt"] == 1, continuation
            assert continuation["max_attempts"] == 3, continuation
            assert continuation["fresh_envelope_required"] is True, continuation
            assert continuation["model_fallback_allowed"] is False, continuation
            assert _quota_spend_count(runtime) == spends_after_failure, (
                "the managed step changed the spend ledger"
            )

            # A previous audit is not proof that the current journal is safe.
            # Mutate only this disposable fixture, then restore it for the real
            # successful retry below. Never substitute the checker result.
            from loopx.control_plane.turn_driver import inspect_loopx_turn_journal
            corrupt = json.loads(journal_before)
            corrupt["completed_phases"] = ["quota_spend"]
            try:
                journal_path.write_text(json.dumps(corrupt), encoding="utf-8")
                inspection = inspect_loopx_turn_journal(
                    runtime, goal_id=GOAL_ID, agent_id=AGENT_ID,
                    turn_key=turn_key, retry_failed=True,
                )
                assert inspection["recovery_decision"]["action"] == "blocked"
                code, rejected = _run_cli(step_argv)
                assert code == 1 and rejected["ok"] is False, rejected
                assert "completed_phases_not_ordered_prefix" in rejected["error"]
                assert "retry_continuation" not in rejected
                assert _quota_spend_count(runtime) == spends_after_failure
            finally:
                journal_path.write_bytes(journal_before)

            # 3. Replaying the same Turn must self-heal and spend exactly once.
            exit_code, healed = _run_cli(_resume_argv(base, turn_key))
            assert exit_code == 0, (exit_code, healed)
            assert healed["ok"] is True, healed
            assert healed["resume_turn_key"] == turn_key, healed
            assert healed["status"] == "committed", healed
            assert healed["result_kind"] == "validated_progress", healed
            assert healed["effects"]["quota_spent"] is True, healed
            assert healed["effects"]["state_written"] is True, healed
            assert healed["quota_slot_spend_count"] == 1, healed
            assert _quota_spend_count(runtime) == 1, "the retry did not spend once"
            assert (workspace / MARKER_NAME).read_text(encoding="utf-8").strip() == (
                MARKER_VALUE
            )

            # 4. Idempotent replay: the healed Turn must not spend again.
            exit_code, replayed = _run_cli(_resume_argv(base, turn_key))
            assert exit_code == 0, (exit_code, replayed)
            assert replayed["replayed"] is True, replayed
            assert _quota_spend_count(runtime) == 1, "the replay double-spent"
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    print(
        "managed-step self-heal: provider_capacity -> wait(30s, 1/3) -> "
        "same-Turn validated_progress, exactly one quota slot"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
