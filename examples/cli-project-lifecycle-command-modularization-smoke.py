#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "loopx" / "cli.py"
MODULE = ROOT / "loopx" / "cli_commands" / "project_lifecycle.py"
PROJECT_SKILL = ROOT / "skills" / "loopx-project" / "SKILL.md"
# The `refresh-state` command owns its own module since #4521, so the markers
# that belong to that command are required there instead of in the dispatcher.
REFRESH_MODULE = ROOT / "loopx" / "cli_commands" / "project_lifecycle_refresh_state.py"
INIT = ROOT / "loopx" / "cli_commands" / "__init__.py"
GOAL_ID = "project-lifecycle-smoke"


def run_cli(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_success(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode != 0:
        raise AssertionError(
            f"expected success, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
    return result.stdout


def require_json_success(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    stdout = require_success(result)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"expected JSON output, got:\n{stdout}") from exc
    require(payload.get("ok") is True, f"payload was not ok: {payload}")
    return payload


def write_fixture(project: Path) -> tuple[Path, Path, Path]:
    runtime_root = project / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    runs_dir = runtime_root / "goals" / GOAL_ID / "runs"
    run_json = runs_dir / "2026-06-22T00-00-00-smoke.json"
    run_md = runs_dir / "2026-06-22T00-00-00-smoke.md"
    index_path = runs_dir / "index.jsonl"

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    (project / "README.md").write_text("# Project Lifecycle Smoke\n", encoding="utf-8")
    state_file.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-06-22T00:00:00+00:00",
                "---",
                "",
                "## Authority Sources",
                "",
                "- public fixture registry",
                "",
                "## Operating Contract",
                "",
                "- keep lifecycle command previews dry-run only",
                "",
                "## Work Clusters",
                "",
                "- CLI modularization smoke",
                "",
                "## Validation Surfaces",
                "",
                "- project lifecycle command smoke",
                "",
                "## Private/Public Boundary",
                "",
                "- fixture contains only public-safe synthetic values",
                "",
                "## Next Action",
                "",
                "- Continue project lifecycle command modularization smoke.",
                "",
                "## Agent Todo",
                "",
                "- [ ] [P1] Continue project lifecycle command modularization smoke.",
                "  <!-- loopx:todo todo_id=todo_project_lifecycle_smoke role=agent status=open priority=P1 task_class=advancement_task -->",
                "",
                "## Progress Ledger",
                "",
                "- 2026-06-22T00:00:00+00:00: Fixture initialized.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "registry_role": "project-local",
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "objective": "Validate project lifecycle CLI command modularization.",
                        "domain": "smoke",
                        "repo": str(project),
                        "state_file": ".codex/goals/project-lifecycle-smoke/ACTIVE_GOAL_STATE.md",
                        "status": "connected-read-only",
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["codex-product-capability"],
                        },
                        "guards": ["dry-run smoke fixture only"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    run_record = {
        "generated_at": "2026-06-22T00:00:00+00:00",
        "goal_id": GOAL_ID,
        "classification": "state_refreshed",
        "recommended_action": "Continue project lifecycle command modularization smoke.",
        "health_check": "fixture run",
        "json_path": str(run_json),
        "markdown_path": str(run_md),
    }
    run_json.write_text(json.dumps(run_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_md.write_text("# Fixture Run\n", encoding="utf-8")
    index_path.write_text(json.dumps(run_record, ensure_ascii=False) + "\n", encoding="utf-8")
    return registry_path, runtime_root, index_path


def main() -> None:
    cli_source = CLI.read_text(encoding="utf-8")
    module_source = MODULE.read_text(encoding="utf-8")
    init_source = INIT.read_text(encoding="utf-8")
    project_skill = PROJECT_SKILL.read_text(encoding="utf-8")
    reward_skill = project_skill.split("## Record Human Reward", 1)[1].split(
        "## Multi-Project Status", 1
    )[0]

    for marker in (
        "--dry-run",
        "--actor-kind owner",
        "--actor-kind controller",
        "Never infer",
    ):
        require(marker in reward_skill, f"project skill reward flow omitted {marker}")

    forbidden_cli_markers = [
        "refresh_state_parser = sub.add_parser",
        "read_only_map_parser = sub.add_parser",
        "reward_parser = sub.add_parser",
        'gate_parser = sub.add_parser(\n        "operator-gate"',
        "refresh_state_run(",
        "read_only_project_map_run(",
        "append_human_reward(",
        "record_operator_gate(",
        'if args.command == "refresh-state":',
        'if args.command == "read-only-map":',
        'if args.command == "reward":',
        'if args.command == "operator-gate":',
    ]
    for marker in forbidden_cli_markers:
        require(marker not in cli_source, f"project lifecycle marker leaked into cli.py: {marker}")

    for marker in (
        "PROJECT_LIFECYCLE_COMMANDS",
        "register_project_lifecycle_commands",
        "handle_project_lifecycle_command",
        "refresh-state",
        "read-only-map",
        "reward",
        "operator-gate",
    ):
        require(marker in module_source, f"project lifecycle module missing {marker}")
    refresh_source = REFRESH_MODULE.read_text(encoding="utf-8")
    for marker in (
        "retry it before delivery",
        "refresh-state",
    ):
        require(marker in refresh_source, f"refresh-state module missing {marker}")
    sink_source = (MODULE.parent / "project_lifecycle_sinks.py").read_text(encoding="utf-8")
    for marker in (
        "apply_external_sink_postcondition",
        "delivery_postcondition",
        "blocks_delivery",
    ):
        require(marker in sink_source, f"project lifecycle sink module missing {marker}")
    require("register_project_lifecycle_commands" in cli_source, "cli.py did not register project lifecycle commands")
    require("handle_project_lifecycle_command" in cli_source, "cli.py did not dispatch project lifecycle commands")
    require("register_project_lifecycle_commands" in init_source, "__init__ did not export project lifecycle registration")
    require("handle_project_lifecycle_command" in init_source, "__init__ did not export project lifecycle handler")

    for command, options in {
        "refresh-state": (
            "--delivery-outcome",
            "--agent-id",
            "--suppress-external-sinks",
            "--dry-run",
        ),
        "read-only-map": ("--recommended-action", "--dry-run"),
        "reward": ("--actor-kind", "--write-active-state-summary", "--lesson-kind", "--lesson-avoid", "--dry-run"),
        "operator-gate": ("--agent-command", "--no-global-sync"),
    }.items():
        help_text = require_success(run_cli(command, "--help"))
        for option in options:
            require(option in help_text, f"{command} help omitted {option}")

    with tempfile.TemporaryDirectory(prefix="loopx-project-lifecycle-cli-") as tmp:
        project = Path(tmp)
        registry_path, runtime_root, index_path = write_fixture(project)
        before_index = index_path.read_text(encoding="utf-8")
        command_prefix = (
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
        )

        refresh_payload = require_json_success(
            run_cli(
                *command_prefix,
                "refresh-state",
                "--goal-id",
                GOAL_ID,
                "--dry-run",
                "--no-global-sync",
                "--suppress-external-sinks",
                "--delivery-batch-scale",
                "implementation",
                "--delivery-outcome",
                "outcome_progress",
                "--format",
                "json",
            )
        )
        require(refresh_payload.get("dry_run") is True, "refresh-state should stay dry-run")
        require(refresh_payload.get("appended") is False, "refresh-state dry-run should not append")
        require(refresh_payload.get("delivery_outcome") == "outcome_progress", "refresh-state outcome changed")
        require(
            refresh_payload.get("external_sink_delivery_authorized") is False,
            "refresh-state did not project the external sink suppression boundary",
        )

        map_payload = require_json_success(
            run_cli(
                *command_prefix,
                "read-only-map",
                "--goal-id",
                GOAL_ID,
                "--dry-run",
                "--no-global-sync",
                "--format",
                "json",
            )
        )
        require(map_payload.get("dry_run") is True, "read-only-map should stay dry-run")
        require(map_payload.get("appended") is False, "read-only-map dry-run should not append")
        require(
            (map_payload.get("project_map") or {}).get("adapter_kind") == "read_only_project_map_v0",
            "read-only-map compact payload changed",
        )

        reward_payload = require_json_success(
            run_cli(
                *command_prefix,
                "reward",
                "--goal-id",
                GOAL_ID,
                "--decision",
                "continue_route",
                "--reward",
                "positive",
                "--reason-summary",
                "synthetic dry-run reward",
                "--follow-up",
                "continue modularization smoke",
                "--lesson-kind",
                "route",
                "--lesson-summary",
                "Keep modularization dry-run checks ahead of broad lifecycle rewrites.",
                "--lesson-avoid",
                "broad lifecycle rewrite before dry-run check",
                "--lesson-prefer",
                "modularization dry-run check",
                "--dry-run",
                "--format",
                "json",
            )
        )
        require(reward_payload.get("dry_run") is True, "reward should stay dry-run")
        require(reward_payload.get("appended") is False, "reward dry-run should not append")
        require(
            (reward_payload.get("human_reward") or {}).get("reward") == "positive",
            "reward payload polarity changed",
        )
        reward_lesson = (reward_payload.get("human_reward") or {}).get("lesson") or {}
        require(reward_lesson.get("kind") == "route", "reward lesson kind changed")
        require(
            reward_lesson.get("avoid") == ["broad lifecycle rewrite before dry-run check"],
            "reward lesson avoid changed",
        )

        rejected_reward = run_cli(
            *command_prefix,
            "reward",
            "--goal-id",
            GOAL_ID,
            "--decision",
            "continue_route",
            "--reward",
            "positive",
            "--reason-summary",
            "synthetic durable reward",
            "--format",
            "json",
        )
        require(rejected_reward.returncode == 1, "anonymous durable reward should fail")
        rejected_payload = json.loads(rejected_reward.stdout)
        require(
            "actor kind is required" in str(rejected_payload.get("error") or ""),
            f"anonymous durable reward returned the wrong error: {rejected_payload}",
        )
        require(index_path.read_text(encoding="utf-8") == before_index, "rejected reward mutated run index")

        durable_reward = require_json_success(
            run_cli(
                *command_prefix,
                "reward",
                "--goal-id",
                GOAL_ID,
                "--actor-kind",
                "owner",
                "--decision",
                "continue_route",
                "--reward",
                "positive",
                "--reason-summary",
                "synthetic durable reward",
                "--format",
                "json",
            )
        )
        require(durable_reward.get("actor_kind") == "owner", "durable reward lost its actor")
        persisted_reward = json.loads(index_path.read_text(encoding="utf-8").splitlines()[-1])
        require(
            (persisted_reward.get("human_reward") or {}).get("actor_kind") == "owner",
            "durable reward index row lost its actor",
        )

        gate_payload = require_json_success(
            run_cli(
                *command_prefix,
                "operator-gate",
                "--goal-id",
                GOAL_ID,
                "--decision",
                "defer",
                "--reason-summary",
                "synthetic dry-run gate",
                "--follow-up",
                "collect more smoke evidence",
                "--dry-run",
                "--no-global-sync",
                "--format",
                "json",
            )
        )
        require(gate_payload.get("dry_run") is True, "operator-gate should stay dry-run")
        require(gate_payload.get("appended") is False, "operator-gate dry-run should not append")
        require(
            (gate_payload.get("operator_gate") or {}).get("decision") == "defer",
            "operator-gate decision changed",
        )
        require(
            len(index_path.read_text(encoding="utf-8").splitlines()) == 2,
            "project lifecycle smoke wrote an unexpected number of run rows",
        )

    print("cli-project-lifecycle-command-modularization-smoke: ok")


if __name__ == "__main__":
    main()
