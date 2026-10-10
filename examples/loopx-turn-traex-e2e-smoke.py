#!/usr/bin/env python3
"""End-to-end qualification for the TraeX generic-cli Turn host adapter.

Mirrors the proven codex-cli e2e fixture but drives the generic-cli host through
the real ``scripts/traex_turn_host_adapter.py`` against a fake ``traex`` binary.
This exercises the full governed chain without a model call:

    loopx turn run-once -> adapter -> fake traex -> independent validator
    -> writeback -> one quota spend -> idempotent replay

The fake host writes the public marker file and the native structured result
file while emitting unrelated TraeX-style stdout noise. Pass
``--real-traex`` to invoke the installed ``traex`` binary for one real model
turn instead of the fake.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.cli import main as cli_main  # noqa: E402

GOAL_ID = "loopx-turn-traex-e2e"
AGENT_ID = "traex-turn-e2e"
TODO_ID = "todo_traexe2e01"
MARKER_NAME = "docs/turn-e2e-marker.txt"
MARKER_VALUE = "loopx-turn-traex-e2e-step-1"
ADAPTER = REPO_ROOT / "scripts" / "traex_turn_host_adapter.py"


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
                "# LoopX Turn TraeX E2E",
                "",
                "## Agent Todo",
                "",
                (
                    f"- [ ] [P0] Write `{MARKER_NAME}` containing exactly "
                    f"`{MARKER_VALUE}`, then report validated progress."
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


def _write_fake_traex(root: Path, workspace: Path) -> Path:
    executable = root / "fake-traex"
    block = json.dumps(
        {
            "result_kind": "validated_progress",
            "classification": "traex_e2e_marker_written",
            "summary": "Wrote the public e2e marker file.",
            "next_action": "Replay idempotently; no further work.",
        }
    )
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"pathlib.Path({str(workspace)!r}).joinpath({MARKER_NAME!r}).write_text("
        f"{MARKER_VALUE!r}, encoding='utf-8')\n"
        "output_path = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
        f"output_path.write_text({block!r}, encoding='utf-8')\n"
        "print('hook: pre-tool')\n"
        "print('INFO transport')\n"
        "print('wrote the marker')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IEXEC)
    return executable


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


def _base_argv(
    *,
    registry: Path,
    runtime: Path,
    workspace: Path,
    traex_bin: Path,
    timeout_seconds: float,
) -> list[str]:
    host_command = json.dumps(
        [
            sys.executable,
            str(ADAPTER),
            "--traex-bin",
            str(traex_bin),
            "--workspace",
            str(workspace),
        ]
    )
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
        "qualification-turn-1",
        "--host",
        "generic-cli",
        "--execution-mode",
        "isolated-headless",
        "--project",
        str(workspace),
        "--host-command-json",
        host_command,
        "--validation-command-json",
        json.dumps(_validator_command()),
        "--validation-failure-kind",
        "repair_required",
        "--scan-root",
        str(registry.parent.parent),
        "--no-global-sync",
        "--timeout-seconds",
        str(timeout_seconds),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-traex",
        action="store_true",
        help="Invoke the installed traex binary instead of the fake host.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="loopx-turn-traex-e2e-") as directory:
        root = Path(directory)
        project, runtime, workspace, registry = _write_fixture(root)
        if args.real_traex:
            import shutil

            found = shutil.which("traex")
            if not found:
                raise SystemExit("traex CLI is required for --real-traex")
            traex_bin = Path(found)
        else:
            traex_bin = _write_fake_traex(root, workspace)

        base = _base_argv(
            registry=registry,
            runtime=runtime,
            workspace=workspace,
            traex_bin=traex_bin,
            timeout_seconds=args.timeout_seconds,
        )
        exit_code, payload = _run_cli([*base, "--execute"])

        marker = workspace / MARKER_NAME
        marker_valid = (
            marker.is_file() and marker.read_text(encoding="utf-8").strip() == MARKER_VALUE
        )

        turn_key = payload.get("resume_turn_key")
        replay_exit_code, replay = (None, None)
        if exit_code == 0 and isinstance(turn_key, str):
            replay_base = list(base)
            idx = replay_base.index("--turn-instance-id")
            del replay_base[idx : idx + 2]
            replay_exit_code, replay = _run_cli(
                [*replay_base, "--resume-turn-key", turn_key, "--execute"]
            )

        summary = {
            "schema_version": "loopx_turn_traex_e2e_v1",
            "real_traex_invoked": bool(args.real_traex),
            "exit_code": exit_code,
            "status": payload.get("status"),
            "reason": payload.get("reason"),
            "result_kind": payload.get("result_kind"),
            "validation": payload.get("validation"),
            "effects": payload.get("effects"),
            "marker_valid": marker_valid,
            "quota_slot_spend_count": _quota_spend_count(runtime),
            "replay_exit_code": replay_exit_code,
            "replay_effects": replay.get("effects") if isinstance(replay, dict) else None,
            "global_registry_synced": False,
        }

    print(json.dumps(summary, indent=2, sort_keys=True))

    effects = summary["effects"] or {}
    expected_effects = {
        "host_invoked": True,
        "state_written": True,
        "quota_spent": True,
        "scheduler_acknowledged": False,
    }
    replay_effects = summary["replay_effects"] or {}
    expected_replay_effects = {
        "host_invoked": False,
        "state_written": False,
        "quota_spent": False,
        "scheduler_acknowledged": False,
    }
    ok = (
        exit_code == 0
        and summary["status"] == "committed"
        and summary["result_kind"] == "validated_progress"
        and (summary["validation"] or {}).get("status") == "passed"
        and effects == expected_effects
        and marker_valid
        and summary["quota_slot_spend_count"] == 1
        and replay_exit_code == 0
        and replay_effects == expected_replay_effects
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
