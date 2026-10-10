#!/usr/bin/env python3
"""Qualify N LoopX Turn transactions with a Codex CLI host."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as cli_main  # noqa: E402
from loopx.control_plane.turn_driver import (  # noqa: E402
    load_loopx_turn_plan_from_journal,
)


GOAL_ID = "loopx-turn-real-cli-e2e"
AGENT_ID = "codex-turn-e2e"
TODO_ID = "todo_turnreale2e01"
MARKER_NAME = "docs/turn-e2e-marker.txt"
MARKER_PREFIX = "loopx-turn-real-e2e-step-"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("turn count must be at least 1")
    return parsed


def _marker_value(turn_number: int) -> str:
    return f"{MARKER_PREFIX}{turn_number}"


def _write_fixture(root: Path, *, turn_count: int) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    workspace = project
    runtime.mkdir(parents=True)
    (workspace / "docs").mkdir(parents=True)
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
                "# LoopX Turn Real CLI E2E",
                "",
                "## Agent Todo",
                "",
                (
                    f"- [ ] [P0] Advance `{MARKER_NAME}` by exactly one numbered "
                    f"step per Turn: missing -> `{_marker_value(1)}`; step-k -> "
                    f"step-(k+1). Stop after `{_marker_value(turn_count)}` and report "
                    "validated progress after each step. Name the completed and next "
                    "numbered step in the typed result so LoopX can plan the next action."
                ),
                (
                    f"  <!-- loopx:todo todo_id={TODO_ID} status=open "
                    "task_class=advancement_task action_kind=real_cli_e2e "
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
                        "domain": "loopx-turn-public-fixture",
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


def _write_fake_codex(root: Path) -> Path:
    executable = root / "fake-codex"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import re
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
turn_key = re.search(r'"turn_key":"([^"]+)"', prompt).group(1)
marker = pathlib.Path({MARKER_NAME!r})
turn_number = 1
if marker.is_file():
    current = marker.read_text(encoding="utf-8").strip()
    match = re.fullmatch(re.escape({MARKER_PREFIX!r}) + r"([1-9][0-9]*)", current)
    if match is None:
        raise SystemExit("unexpected marker value")
    turn_number = int(match.group(1)) + 1
marker.write_text({MARKER_PREFIX!r} + str(turn_number), encoding="utf-8")
print(json.dumps({{
    "type": "thread.started",
    "thread_id": "session-fixture-0001",
}}), flush=True)
output_path = pathlib.Path(args[args.index("--output-last-message") + 1])
output_path.write_text(json.dumps({{
    "schema_version": "loopx_turn_result_v0",
    "turn_key": turn_key,
    "result_kind": "validated_progress",
    "completed_phases": ["host_execute", "typed_result"],
    "classification": f"real_cli_e2e_step_{{turn_number}}_progress",
    "recommended_action": f"Advance the marker to step {{turn_number + 1}}.",
    "next_action": f"Run the independently validated step {{turn_number + 1}} Turn.",
    "delivery_batch_scale": "single_surface",
    "delivery_outcome": "outcome_progress",
    "vision_unchanged_reason": "The fixture objective remains unchanged.",
    "summary": f"The isolated public marker reached step {{turn_number}}.",
}}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def _validator_command(expected_marker: str) -> list[str]:
    program = (
        "import json,pathlib,sys; "
        "json.load(sys.stdin); "
        f"p=pathlib.Path({MARKER_NAME!r}); "
        "raise SystemExit(0 if p.is_file() and "
        f"p.read_text(encoding='utf-8').strip() == {expected_marker!r} else 9)"
    )
    return [sys.executable, "-c", program]


def _run_cli(argv: list[str]) -> tuple[int, dict[str, Any]]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(argv)
    payload = json.loads(output.getvalue())
    assert isinstance(payload, dict), payload
    return exit_code, payload


def _base_argv(
    *,
    registry: Path,
    runtime: Path,
    workspace: Path,
    codex_bin: Path,
    model: str | None,
    timeout_seconds: float,
    expected_marker: str,
    turn_instance_id: str,
) -> list[str]:
    argv = [
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
        turn_instance_id,
        "--host",
        "codex-cli",
        "--execution-mode",
        "isolated-headless",
        "--project",
        str(workspace),
        "--codex-bin",
        str(codex_bin),
        "--codex-sandbox",
        "workspace-write",
        "--validation-command-json",
        json.dumps(_validator_command(expected_marker)),
        "--scan-root",
        str(registry.parent.parent),
        "--no-global-sync",
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    if model:
        argv.extend(["--codex-model", model])
    return argv


def _quota_spend_count(runtime: Path) -> int:
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    if not index.is_file():
        return 0
    return sum(
        1
        for line in index.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("classification") == "quota_slot_spent"
    )


def _marker_matches(workspace: Path, expected: str) -> bool:
    marker = workspace / MARKER_NAME
    return marker.is_file() and marker.read_text(encoding="utf-8").strip() == expected


def _session_action(runtime: Path, turn_key: object) -> str | None:
    if not isinstance(turn_key, str):
        return None
    plan = load_loopx_turn_plan_from_journal(
        runtime,
        goal_id=GOAL_ID,
        turn_key=turn_key,
    )
    session = plan.get("session")
    return str(session.get("action")) if isinstance(session, dict) else None


def _turn_summary(
    *,
    turn_number: int,
    exit_code: int,
    payload: dict[str, Any],
    marker_valid: bool,
    runtime: Path,
) -> dict[str, Any]:
    validation = payload.get("validation")
    effects = payload.get("effects")
    receipt = payload.get("receipt")
    return {
        "turn_number": turn_number,
        "exit_code": exit_code,
        "session_action": _session_action(runtime, payload.get("resume_turn_key")),
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "result_kind": payload.get("result_kind"),
        "receipt_status": receipt.get("status") if isinstance(receipt, dict) else None,
        "validation_status": (
            validation.get("status") if isinstance(validation, dict) else None
        ),
        "effects": effects if isinstance(effects, dict) else {},
        "marker_valid": marker_valid,
    }


def _summary(
    *,
    real_codex_cli: bool,
    turn_count: int,
    turns: list[dict[str, Any]],
    replay_exit_code: int | None,
    replay: dict[str, Any] | None,
    runtime: Path,
    workspace: Path,
    model_explicit: bool,
) -> dict[str, Any]:
    final_turn = turns[-1] if turns else {}
    session_actions = [turn.get("session_action") for turn in turns]
    return {
        "schema_version": "loopx_turn_real_cli_e2e_v1",
        "real_codex_cli_invoked": real_codex_cli,
        "model_explicit": model_explicit,
        "requested_turn_count": turn_count,
        "observed_turn_count": len(turns),
        "committed_turn_count": sum(
            turn.get("status") == "committed" for turn in turns
        ),
        "turns": turns,
        "session_actions": session_actions,
        "session_resumed": turn_count > 1 and session_actions == [
            "start_new",
            *(["resume"] * (turn_count - 1)),
        ],
        "status": final_turn.get("status"),
        "reason": final_turn.get("reason"),
        "result_kind": final_turn.get("result_kind"),
        "receipt_status": final_turn.get("receipt_status"),
        "validation_status": final_turn.get("validation_status"),
        "effects": final_turn.get("effects", {}),
        "marker_valid": _marker_matches(workspace, _marker_value(turn_count)),
        "quota_slot_spend_count": _quota_spend_count(runtime),
        "replay_exit_code": replay_exit_code,
        "replay_effects": replay.get("effects") if isinstance(replay, dict) else None,
        "loopx_raw_host_output_recorded": False,
        "global_registry_synced": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-codex-cli",
        action="store_true",
        help="Invoke the real Codex CLI host instead of the no-model fixture binary.",
    )
    parser.add_argument("--codex-bin", type=Path)
    parser.add_argument("--codex-model")
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--turn-count",
        type=_positive_int,
        default=1,
        metavar="N",
        help=(
            "Run N separately validated transactions on one opaque Codex CLI "
            "session, then replay the final transaction idempotently (default: 1)."
        ),
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="loopx-turn-real-cli-e2e-") as directory:
        root = Path(directory)
        project, runtime, workspace, registry = _write_fixture(
            root,
            turn_count=args.turn_count,
        )
        if args.real_codex_cli:
            candidate = args.codex_bin or (
                Path(found) if (found := shutil.which("codex")) else None
            )
            if candidate is None or not candidate.exists():
                raise SystemExit("codex CLI is required for --real-codex-cli")
            codex_bin = candidate
        else:
            codex_bin = _write_fake_codex(root)

        turns: list[dict[str, Any]] = []
        turn_payloads: list[dict[str, Any]] = []
        turn_bases: list[list[str]] = []
        for turn_number in range(1, args.turn_count + 1):
            base = _base_argv(
                registry=registry,
                runtime=runtime,
                workspace=workspace,
                codex_bin=codex_bin,
                model=args.codex_model,
                timeout_seconds=args.timeout_seconds,
                expected_marker=_marker_value(turn_number),
                turn_instance_id=f"qualification-turn-{turn_number}",
            )
            exit_code, payload = _run_cli([*base, "--execute"])
            turns.append(
                _turn_summary(
                    turn_number=turn_number,
                    exit_code=exit_code,
                    payload=payload,
                    marker_valid=_marker_matches(
                        workspace,
                        _marker_value(turn_number),
                    ),
                    runtime=runtime,
                )
            )
            turn_payloads.append(payload)
            turn_bases.append(base)
            if exit_code != 0:
                break
        replay_exit_code: int | None = None
        replay: dict[str, Any] | None = None
        final_payload = turn_payloads[-1] if turn_payloads else {}
        turn_key = final_payload.get("resume_turn_key")
        if turns and turns[-1]["exit_code"] == 0 and isinstance(turn_key, str):
            replay_base = list(turn_bases[-1])
            instance_index = replay_base.index("--turn-instance-id")
            del replay_base[instance_index : instance_index + 2]
            replay_exit_code, replay = _run_cli(
                [*replay_base, "--resume-turn-key", turn_key, "--execute"]
            )
        summary = _summary(
            real_codex_cli=bool(args.real_codex_cli),
            turn_count=args.turn_count,
            turns=turns,
            replay_exit_code=replay_exit_code,
            replay=replay,
            runtime=runtime,
            workspace=workspace,
            model_explicit=bool(args.codex_model),
        )

    print(json.dumps(summary, indent=2, sort_keys=True))
    expected_effects = {
        "host_invoked": True,
        "state_written": True,
        "quota_spent": True,
        "scheduler_acknowledged": False,
    }
    replay_effects = {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    expected_session_actions = [
        "start_new",
        *(["resume"] * (args.turn_count - 1)),
    ]
    turns_ok = (
        summary["observed_turn_count"] == args.turn_count
        and summary["committed_turn_count"] == args.turn_count
        and summary["session_actions"] == expected_session_actions
        and all(
            turn["turn_number"] == index
            and turn["exit_code"] == 0
            and turn["status"] == "committed"
            and turn["receipt_status"] == "committed"
            and turn["validation_status"] == "passed"
            and turn["effects"] == expected_effects
            and turn["marker_valid"] is True
            for index, turn in enumerate(summary["turns"], start=1)
        )
    )
    return 0 if (
        turns_ok
        and summary["status"] == "committed"
        and summary["receipt_status"] == "committed"
        and summary["validation_status"] == "passed"
        and summary["effects"] == expected_effects
        and summary["marker_valid"] is True
        and summary["quota_slot_spend_count"] == args.turn_count
        and summary["replay_exit_code"] == 0
        and summary["replay_effects"] == replay_effects
        and summary["loopx_raw_host_output_recorded"] is False
        and summary["global_registry_synced"] is False
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
