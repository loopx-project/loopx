from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loopx.cli import main as cli_main
from loopx.control_plane.scheduler.execution_context import SchedulerRuntimeProfile
from loopx.control_plane.testing.cli_output_budget import (
    CLI_OUTPUT_BUDGET_BY_ID,
    CLI_OUTPUT_BUDGET_SPECS,
    CLI_OUTPUT_COMMAND_CLASSIFICATION_BY_ID,
    CLI_OUTPUT_COMMAND_CLASSIFICATIONS,
    CLI_OUTPUT_MODE_VARIANT_BY_ID,
    CLI_OUTPUT_MODE_VARIANT_SPECS,
    assert_cli_output_baseline,
    assert_cli_output_mode_variant,
    measure_cli_output,
    public_manifest,
)
from loopx.heartbeat_prompt import build_heartbeat_prompt
from loopx.help_surface import COMMAND_GROUPS
from loopx.rollout_event_log import rollout_event_log_path

GOAL_ID = "cli-output-budget-goal"
AGENT_IDS = ("codex-alpha", "codex-beta", "codex-gamma")
GOAL_TEXT = "Qualify agent-facing CLI output budgets before changing production output."


@dataclass(frozen=True)
class Scenario:
    name: str
    todo_count: int
    agent_count: int
    run_count: int


SCENARIOS = (
    Scenario("small", todo_count=1, agent_count=1, run_count=1),
    Scenario("crowded", todo_count=36, agent_count=1, run_count=12),
    Scenario("multi_agent", todo_count=18, agent_count=3, run_count=12),
)


def _write_fixture(
    root: Path,
    scenario: Scenario,
    *,
    include_agent_vision: bool = False,
) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_relative = Path(".codex") / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file = project / state_relative
    state_file.parent.mkdir(parents=True)
    agents = AGENT_IDS[: scenario.agent_count]
    lines = [
        "---",
        "status: active",
        "updated_at: 2026-01-01T00:00:00+00:00",
        "---",
        "",
        "# CLI Output Budget Fixture",
        "",
        "## Agent Todo",
        "",
    ]
    for index in range(scenario.todo_count):
        agent_id = agents[index % len(agents)]
        priority = f"P{index % 3}"
        lines.extend(
            [
                f"- [ ] [{priority}] Validate public fixture lane {index:02d} without reading archival detail.",
                (
                    "  <!-- loopx:todo "
                    f"todo_id=todo_fixture_{index:03d} status=open "
                    "task_class=advancement_task "
                    f"action_kind=fixture_{index % 4} claimed_by={agent_id} "
                    f"priority={priority} -->"
                ),
            ]
        )
    state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "cli-output-budget-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_relative),
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "quota": {"compute": 1.0, "window_hours": 24},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": list(agents),
                            "agent_profiles": {
                                agent_id: {
                                    "schema_version": "agent_profile_v1",
                                    "profile_role": "fixture",
                                    "scope": "public qualification",
                                }
                                for agent_id in agents
                            },
                            "write_scope": ["docs/**"],
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_run_history(
        runtime,
        agents=agents,
        run_count=scenario.run_count,
        include_agent_vision=include_agent_vision,
    )
    _write_rollout_event(runtime, agent_id=agents[0])
    return project, runtime, registry_path, state_file


def _write_run_history(
    runtime: Path,
    *,
    agents: tuple[str, ...],
    run_count: int,
    include_agent_vision: bool = False,
) -> None:
    runs_dir = runtime / "goals" / GOAL_ID / "runs"
    runs_dir.mkdir(parents=True)
    index_rows = []
    for index in range(run_count):
        json_path = runs_dir / f"fixture-run-{index:02d}.json"
        markdown_path = json_path.with_suffix(".md")
        agent_id = agents[index % len(agents)]
        record = {
            "generated_at": f"2026-01-01T00:{index:02d}:00+00:00",
            "goal_id": GOAL_ID,
            "classification": "fixture_progress",
            "recommended_action": f"Continue fixture step {index}.",
            "health_check": "public fixture healthy",
            "agent_id": agent_id,
            "progress_scope": "agent_lane",
        }
        if include_agent_vision and agent_id == agents[0]:
            record["agent_vision"] = {
                "schema_version": "goal_vision_replan_contract_v0",
                "agent_id": agent_id,
                "state": "vision_active",
                "vision_patch": {
                    "acceptance_summary": (
                        "Keep the public CLI contract within its declared output "
                        "budget while preserving every executable decision signal."
                    ),
                    "replan_trigger_summary": (
                        "Create a successor when an agent-facing default exceeds "
                        "its characterized hot-path budget."
                    ),
                    "advancement_policy": "repeat_until_closed",
                },
            }
        json_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        markdown_path.write_text("# Public fixture run\n", encoding="utf-8")
        index_rows.append(
            {
                **record,
                "json_path": str(json_path),
                "markdown_path": str(markdown_path),
            }
        )
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in index_rows),
        encoding="utf-8",
    )


def _write_rollout_event(runtime: Path, *, agent_id: str) -> None:
    path = rollout_event_log_path(runtime, GOAL_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_rollout_event_v0",
                "event_id": "fixture-event",
                "event_kind": "todo_updated",
                "recorded_at": "2026-01-01T01:00:00Z",
                "goal_id": GOAL_ID,
                "agent_id": agent_id,
                "todo_id": "todo_fixture_000",
                "status": "appended",
                "summary": "Public fixture evidence.",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_checkpointed_boundary_authority(registry_path: Path) -> None:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    coordination = registry["goals"][0]["coordination"]
    coordination["checkpointed_boundary_authority"] = [
        {
            "schema_version": "checkpointed_boundary_authority_v0",
            "status": "active",
            "decision": "approve",
            "write_scope": ["docs/**"],
            "source": "public_fixture_owner_approval",
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "decision_id": "todo_fixture_boundary_001",
        }
    ]
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_user_todo_fixture(state_file: Path) -> None:
    state_text = state_file.read_text(encoding="utf-8")
    user_section = "\n".join(
        [
            "## User Todo",
            "",
            "- [ ] [P0-user] Approve the scoped output release.",
            (
                "  <!-- loopx:todo todo_id=todo_user_gate_001 status=open "
                "task_class=user_gate action_kind=approve_output_release "
                "blocks_agent=codex-alpha "
                "decision_scope=release:action:quota-output priority=P0-USER -->"
            ),
            "- [ ] [P1] Review the other agent output notes.",
            (
                "  <!-- loopx:todo todo_id=todo_user_action_001 status=open "
                "task_class=user_action action_kind=review_output_notes "
                "bound_agent=codex-beta priority=P1 -->"
            ),
            "",
        ]
    )
    state_file.write_text(
        state_text.replace("## Agent Todo", f"{user_section}\n## Agent Todo"),
        encoding="utf-8",
    )


def _write_todo_list_limit_stress_fixture(state_file: Path) -> None:
    lines = [
        "---",
        "status: active",
        "updated_at: 2026-01-01T00:00:00+00:00",
        "---",
        "",
        "# Todo List Limit Stress Fixture",
        "",
        "## Agent Todo",
        "",
    ]
    for index in range(100):
        lines.extend(
            [
                f"- [ ] Observe bounded public monitor {index:03d}.",
                (
                    "  <!-- loopx:todo "
                    f"todo_id=todo_monitor_{index:03d} status=open "
                    "task_class=continuous_monitor "
                    f"action_kind=observe_{index % 4} claimed_by=codex-alpha -->"
                ),
            ]
        )
    for index in range(100):
        lines.extend(
            [
                f"- [ ] Resolve bounded public blocker {index:03d}.",
                (
                    "  <!-- loopx:todo "
                    f"todo_id=todo_blocker_{index:03d} status=blocked "
                    "task_class=blocker "
                    f"action_kind=resolve_{index % 4} claimed_by=codex-alpha -->"
                ),
            ]
        )
    state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")




def _invoke_cli(args: list[str]) -> tuple[int, str]:
    output = io.StringIO()
    ambient_thread_id = os.environ.pop("CODEX_THREAD_ID", None)
    try:
        with contextlib.redirect_stdout(output):
            exit_code = cli_main(args)
    finally:
        if ambient_thread_id is not None:
            os.environ["CODEX_THREAD_ID"] = ambient_thread_id
    return exit_code, output.getvalue()


def _quota_payload_without_rollout_receipt(text: str) -> dict[str, object]:
    payload = json.loads(text)
    payload.pop("rollout_event", None)
    return payload


@contextlib.contextmanager
def _stable_budget_fixture_root(root: Path):
    """Keep absolute-path fields stable across pytest and xdist temp layouts."""

    root.mkdir(parents=True, exist_ok=True)
    suffix = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    alias = Path("/tmp") / f"loopx-cli-budget-{suffix}"
    if alias.exists() or alias.is_symlink():
        if not alias.is_symlink():
            raise RuntimeError(f"refusing to replace non-symlink fixture root: {alias}")
        alias.unlink()
    alias.symlink_to(root, target_is_directory=True)
    try:
        yield alias
    finally:
        alias.unlink(missing_ok=True)


def _surface_commands(
    *,
    project: Path,
    runtime: Path,
    registry_path: Path,
    state_file: Path,
    output_format: str,
) -> dict[str, list[str]]:
    common = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
        "--format",
        output_format,
    ]
    return {
        "start_goal_guided": [
            "--format",
            output_format,
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--host-surface",
            "codex-app",
            "--goal-text",
            GOAL_TEXT,
        ],
        "bootstrap_command_pack": [
            "--format",
            output_format,
            "bootstrap-command-pack",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--host-surface",
            "codex-app",
            "--goal-text",
            GOAL_TEXT,
        ],
        "quota_should_run": common
        + [
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
        ],
        "loopx_turn_plan": common
        + [
            "turn",
            "plan",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
        ],
        "status": common
        + [
            "status",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--limit",
            "5",
        ],
        "diagnose": common
        + [
            "diagnose",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--limit",
            "5",
        ],
        "review_packet_handoff_only": common
        + [
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--handoff-only",
            "--scan-root",
            str(project),
            "--limit",
            "5",
        ],
        "heartbeat_prompt_thin": common
        + [
            "heartbeat-prompt",
            "--thin",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(state_file),
            "--agent-id",
            AGENT_IDS[0],
            "--agent-scope",
            "Public CLI output qualification.",
        ],
        "todo_list": common
        + ["todo", "list", "--goal-id", GOAL_ID, "--agent-id", AGENT_IDS[0]],
        "history_limited": common
        + ["history", "--goal-id", GOAL_ID, "--limit", "5"],
        "evidence_log_thin": common
        + [
            "evidence-log",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--limit",
            "5",
            "--history-limit",
            "10",
            "--rollout-limit",
            "20",
            "--thin",
        ],
    }


def _measure_scenario(root: Path, scenario: Scenario) -> dict[str, dict[str, dict]]:
    with _stable_budget_fixture_root(root) as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            scenario,
        )
        results: dict[str, dict[str, dict]] = {}
        for output_format in ("json", "markdown"):
            commands = _surface_commands(
                project=project,
                runtime=runtime,
                registry_path=registry_path,
                state_file=state_file,
                output_format=output_format,
            )
            for surface_id, command in commands.items():
                exit_code, text = _invoke_cli(command)
                assert exit_code == 0, (surface_id, output_format, text)
                measurement = measure_cli_output(  # type: ignore[arg-type]
                    text,
                    output_format=output_format,
                )
                spec = CLI_OUTPUT_BUDGET_BY_ID[surface_id]
                assert_cli_output_baseline(
                    spec,
                    scenario=scenario.name,
                    output_format=output_format,  # type: ignore[arg-type]
                    text=text,
                    measurement=measurement,
                )
                results.setdefault(surface_id, {})[output_format] = measurement
    return results


def _mode_variant_commands(
    *,
    project: Path,
    runtime: Path,
    registry_path: Path,
    state_file: Path,
    output_format: str,
) -> dict[str, list[str]]:
    common = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime),
        "--format",
        output_format,
    ]
    heartbeat = [
        "--goal-id",
        GOAL_ID,
        "--active-state",
        str(state_file),
        "--agent-id",
        AGENT_IDS[0],
        "--agent-scope",
        "Public CLI output qualification.",
    ]
    return {
        "start_goal_guided_command_pack_detail": [
            "--format",
            output_format,
            "start-goal",
            "--guided",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--host-surface",
            "codex-app",
            "--goal-text",
            GOAL_TEXT,
            "--include-command-pack-detail",
        ],
        "bootstrap_command_pack_message_only": [
            "--format",
            output_format,
            "bootstrap-command-pack",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--goal-text",
            GOAL_TEXT,
            "--message-only",
        ],
        "quota_should_run_scheduler_detail": common
        + [
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--include-detail",
            "scheduler",
        ],
        "quota_should_run_todo_summary_detail": common
        + [
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--include-detail",
            "agent-todos",
        ],
        "quota_should_run_user_todo_summary_detail": common
        + [
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--include-detail",
            "user-todos",
        ],
        "quota_should_run_goal_boundary_detail": common
        + [
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--include-detail",
            "goal-boundary",
        ],
        "quota_should_run_vision_detail": common
        + [
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--include-detail",
            "vision",
        ],
        "quota_should_run_all_detail": common
        + [
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--include-detail",
            "all",
        ],
        "quota_should_run_turn_envelope": common
        + [
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--turn-envelope",
        ],
        "loopx_turn_plan_transaction_detail": common
        + [
            "turn",
            "plan",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--include-transaction-detail",
        ],
        "loopx_turn_run_once_preview": common
        + [
            "turn",
            "run-once",
            "--host",
            "generic-cli",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--project",
            str(project),
            "--host-command-json",
            '["python3","-c","raise SystemExit(9)"]',
            "--scan-root",
            str(project),
            "--no-global-sync",
        ],
        "status_task_graph_detail": common
        + [
            "status",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--limit",
            "5",
            "--include-task-graph",
        ],
        "review_packet_full": common
        + [
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--limit",
            "5",
        ],
        "heartbeat_prompt_brief": common + ["heartbeat-prompt", "--brief", *heartbeat],
        "heartbeat_prompt_compact": common + ["heartbeat-prompt", "--compact", *heartbeat],
        "heartbeat_prompt_full": common + ["heartbeat-prompt", "--full", *heartbeat],
        "todo_list_limited": common
        + ["todo", "list", "--goal-id", GOAL_ID, "--limit", "3"],
        "todo_list_thin": common
        + ["todo", "list", "--goal-id", GOAL_ID, "--thin"],
    }


def _agent_facing_help_command_ids() -> set[str]:
    command_ids: set[str] = set()
    for group in COMMAND_GROUPS:
        title = str(group.get("title") or "")
        for row in group.get("commands", []):
            if not isinstance(row, dict):
                continue
            command = str(row.get("command") or "")
            selected = title in {"Start here", "Daily operator commands"}
            selected = selected or command == "loopx heartbeat-prompt"
            if not selected or not command.startswith("loopx "):
                continue
            parts = shlex.split(command)
            assert len(parts) >= 2, command
            command_ids.add(parts[1])
    return command_ids


def test_manifest_covers_the_declared_agent_facing_surface_set() -> None:
    expected = {
        "start_goal_guided",
        "bootstrap_command_pack",
        "quota_should_run",
        "loopx_turn_plan",
        "status",
        "diagnose",
        "review_packet_handoff_only",
        "heartbeat_prompt_thin",
        "todo_list",
        "history_limited",
        "evidence_log_thin",
    }
    manifest = public_manifest()
    assert set(CLI_OUTPUT_BUDGET_BY_ID) == expected
    assert manifest["surface_count"] == len(expected)
    assert {row["surface_id"] for row in manifest["surfaces"]} == expected
    assert all(spec.owner and spec.consumer_action and spec.cold_path for spec in CLI_OUTPUT_BUDGET_SPECS)
    expected_variants = {
        "start_goal_guided_command_pack_detail",
        "bootstrap_command_pack_message_only",
        "quota_should_run_scheduler_detail",
        "quota_should_run_todo_summary_detail",
        "quota_should_run_user_todo_summary_detail",
        "quota_should_run_goal_boundary_detail",
        "quota_should_run_vision_detail",
        "quota_should_run_all_detail",
        "quota_should_run_turn_envelope",
        "loopx_turn_plan_transaction_detail",
        "loopx_turn_run_once_preview",
        "status_task_graph_detail",
        "review_packet_full",
        "heartbeat_prompt_brief",
        "heartbeat_prompt_compact",
        "heartbeat_prompt_full",
        "todo_list_limited",
        "todo_list_thin",
    }
    assert set(CLI_OUTPUT_MODE_VARIANT_BY_ID) == expected_variants
    assert manifest["mode_variant_count"] == len(expected_variants)
    assert {row["variant_id"] for row in manifest["mode_variants"]} == expected_variants
    assert all(
        spec.parent_surface_id in expected for spec in CLI_OUTPUT_MODE_VARIANT_SPECS
    )
    help_command_ids = _agent_facing_help_command_ids()
    assert set(CLI_OUTPUT_COMMAND_CLASSIFICATION_BY_ID) == help_command_ids
    assert manifest["command_classification_count"] == len(help_command_ids)
    assert {
        row["command_id"] for row in manifest["command_classifications"]
    } == help_command_ids
    for classification in CLI_OUTPUT_COMMAND_CLASSIFICATIONS:
        assert classification.rationale
        if classification.qualification == "qualified_default":
            assert classification.surface_id in expected
        else:
            assert classification.qualification == "explicit_cold_path_exception"
            assert classification.surface_id is None


def test_real_cli_output_stays_inside_the_characterized_baseline(
    tmp_path: Path,
) -> None:
    for scenario in SCENARIOS:
        results = _measure_scenario(tmp_path / scenario.name, scenario)
        for formats in results.values():
            assert formats["json"]["json_parseable"] is True
            assert formats["json"]["pretty_print_overhead_chars"] > 0
            assert formats["markdown"]["json_parseable"] is False


def test_quota_should_run_no_format_uses_machine_contract_json(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "quota-default-json") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[0],
        )
        explicit_json_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run"]
        no_format_command = list(explicit_json_command)
        format_index = no_format_command.index("--format")
        del no_format_command[format_index : format_index + 2]
        explicit_markdown_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="markdown",
        )["quota_should_run"]

        no_format_exit_code, no_format_text = _invoke_cli(no_format_command)
        markdown_exit_code, markdown_text = _invoke_cli(explicit_markdown_command)

    assert no_format_exit_code == 0, no_format_text
    assert json.loads(no_format_text)["goal_id"] == GOAL_ID
    assert markdown_exit_code == 0, markdown_text
    assert markdown_text.startswith("# LoopX Quota Should Run")
    assert not markdown_text.lstrip().startswith("{")


def test_quota_cli_keeps_full_agent_todo_diagnostics_on_explicit_cold_path(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "quota-todo-detail") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
        )
        default_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run"]
        detail_command = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run_todo_summary_detail"]

        default_exit_code, default_text = _invoke_cli(default_command)
        detail_exit_code, detail_text = _invoke_cli(detail_command)

    assert default_exit_code == 0, default_text
    assert detail_exit_code == 0, detail_text
    default_payload = json.loads(default_text)
    detail_payload = json.loads(detail_text)
    default_summary = default_payload["agent_todo_summary"]
    detail_summary = detail_payload["agent_todo_summary"]
    assert "agent_todo_planning_inventory" not in default_payload
    detail_inventory = detail_payload["agent_todo_planning_inventory"]
    assert detail_inventory["schema_version"] == (
        "todo_planning_inventory_detail_v0"
    )
    assert detail_inventory["item_detail_ref"] == "$.agent_todo_summary"
    assert detail_inventory["items"]
    assert default_summary["payload_compaction"]["schema_version"] == (
        "quota_cli_todo_summary_compaction_v0"
    )
    assert default_payload["todo_summary_projection"]["detail_ref"] == (
        "quota should-run --include-detail agent-todos"
    )
    assert "backlog_items" not in default_summary
    assert detail_summary["backlog_items"]
    assert "todo_summary_projection" not in detail_payload
    for key in ("interaction_contract", "scheduler_hint", "selected_todo"):
        assert default_payload.get(key) == detail_payload.get(key)


def test_malformed_todo_state_fails_closed_without_dropping_bounded_detail(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "malformed-todo-state") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
        )
        state_text = state_file.read_text(encoding="utf-8")
        state_file.write_text(
            state_text.replace("status=open", "status=stuck", 1),
            encoding="utf-8",
        )
        commands = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )
        detail_command = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run_todo_summary_detail"]

        status_exit_code, status_text = _invoke_cli(commands["status"])
        default_exit_code, default_text = _invoke_cli(commands["quota_should_run"])
        detail_exit_code, detail_text = _invoke_cli(detail_command)

    assert status_exit_code == 1, status_text
    status_measurement = measure_cli_output(status_text, output_format="json")
    assert_cli_output_baseline(
        CLI_OUTPUT_BUDGET_BY_ID["status"],
        scenario="crowded",
        output_format="json",
        text=status_text,
        measurement=status_measurement,
    )
    status_payload = json.loads(status_text)
    error_codes = {
        row.get("code")
        for row in status_payload["contract"]["error_diagnostics"]
        if isinstance(row, dict)
    }
    assert "todo_status_invalid" in error_codes

    assert default_exit_code == 1, default_text
    default_measurement = measure_cli_output(default_text, output_format="json")
    assert_cli_output_baseline(
        CLI_OUTPUT_BUDGET_BY_ID["quota_should_run"],
        scenario="crowded",
        output_format="json",
        text=default_text,
        measurement=default_measurement,
    )
    default_payload = json.loads(default_text)
    assert default_payload["status_health_ok"] is False
    assert default_payload["should_run"] is False
    assert default_payload["effective_action"] == "quota_skip"
    default_summary = default_payload["agent_todo_summary"]
    omitted_backlog = default_summary["payload_compaction"]["omitted_lanes"][
        "backlog_items"
    ]
    assert omitted_backlog > 0
    assert "backlog_items" not in default_summary
    assert default_payload["todo_summary_projection"]["detail_ref"] == (
        "quota should-run --include-detail agent-todos"
    )

    assert detail_exit_code == 1, detail_text
    detail_measurement = measure_cli_output(detail_text, output_format="json")
    assert_cli_output_mode_variant(
        CLI_OUTPUT_MODE_VARIANT_BY_ID["quota_should_run_todo_summary_detail"],
        output_format="json",
        text=detail_text,
        measurement=detail_measurement,
    )
    detail_payload = json.loads(detail_text)
    assert detail_payload["status_health_ok"] is False
    assert detail_payload["should_run"] is False
    assert detail_payload["effective_action"] == "quota_skip"
    detail_summary = detail_payload["agent_todo_summary"]
    backlog_budget = detail_summary["payload_compaction"]["compacted_lanes"][
        "backlog_items"
    ]
    assert backlog_budget["total"] > backlog_budget["shown"] > 0
    assert len(detail_summary["backlog_items"]) == backlog_budget["shown"]
    assert detail_summary["backlog_items"][0]["todo_id"] == "todo_fixture_000"
    assert str(project) not in json.dumps(detail_summary["backlog_items"])
    for key in ("decision", "should_run", "effective_action", "selected_todo"):
        assert default_payload.get(key) == detail_payload.get(key)


def test_quota_cli_bounds_real_scale_vision_audit_and_keeps_cold_detail(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "quota-vision-detail") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
            include_agent_vision=True,
        )
        default_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run"]
        detail_command = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run_vision_detail"]

        default_exit_code, default_text = _invoke_cli(default_command)
        detail_exit_code, detail_text = _invoke_cli(detail_command)

    assert default_exit_code == 0, default_text
    assert detail_exit_code == 0, detail_text
    assert len(default_text) <= 40_000
    default_payload = json.loads(default_text)
    detail_payload = json.loads(detail_text)
    compact_audit = default_payload["vision_continuation_audit"]
    detailed_audit = detail_payload["vision_continuation_audit"]
    assert compact_audit["required"] is True
    assert compact_audit["vision_gap_judge"]["decision"] == "continue"
    assert compact_audit["payload_compaction"]["full_detail_cold_path"] == (
        "quota should-run --include-detail vision"
    )
    assert "registry_read_instruction" not in compact_audit["vision_gap_judge"]
    assert "registry_read_instruction" in detailed_audit["vision_gap_judge"]
    compact_replan = default_payload["replan_action_packet"]
    detailed_replan = detail_payload["replan_action_packet"]
    assert compact_replan["payload_compaction"] == {
        "schema_version": "quota_cli_replan_action_compaction_v0",
        "mode": "compact_hot_path",
        "compacted_fields": ["writeback_contract.vision_authoring"],
        "full_detail_cold_path": "quota should-run --include-detail vision",
    }
    assert "vision_authoring" not in compact_replan["writeback_contract"]
    assert compact_replan["writeback_contract"]["vision_authoring_detail_ref"] == (
        "quota should-run --include-detail vision"
    )
    assert detailed_replan["writeback_contract"]["vision_authoring"][
        "schema_version"
    ] == "goal_vision_replan_contract_v0"
    assert "payload_compaction" not in detailed_replan
    assert default_payload["goal_frontier_projection"][
        "vision_continuation_audit"
    ]["projection_ref"] == "$.vision_continuation_audit"
    for key in (
        "decision",
        "should_run",
        "effective_action",
        "recommended_action",
        "selected_todo",
        "scheduler_hint",
        "user_todo_summary",
    ):
        assert default_payload.get(key) == detail_payload.get(key)
    assert default_payload["goal_frontier_projection"]["replan_required"] == (
        detail_payload["goal_frontier_projection"]["replan_required"]
    )
    assert default_payload["goal_frontier_projection"]["acceptance_gaps"] == (
        detail_payload["goal_frontier_projection"]["acceptance_gaps"]
    )
    default_interaction = default_payload["interaction_contract"]
    detail_interaction = detail_payload["interaction_contract"]
    assert default_interaction["mode"] == detail_interaction["mode"]
    assert default_interaction["user_channel"] == detail_interaction["user_channel"]
    assert default_interaction["agent_channel"]["must_attempt"] == (
        detail_interaction["agent_channel"]["must_attempt"]
    )
    assert default_interaction["agent_channel"]["primary_action"] == (
        detail_interaction["agent_channel"]["primary_action"]
    )
    assert default_interaction["cli_channel"]["next_cli_actions"] == (
        detail_interaction["cli_channel"]["next_cli_actions"]
    )


def test_crowded_turn_plan_budget_preserves_executable_vision_authoring(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "turn-plan-vision") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
        )
        command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["loopx_turn_plan"]
        exit_code, text = _invoke_cli(command)

    assert exit_code == 0, text
    payload = json.loads(text)
    writeback = payload["turn_envelope"]["replan_action_packet"][
        "writeback_contract"
    ]
    assert "path_delta.evidence_refs" in writeback["required_fields"]
    assert writeback["vision_authoring"]["schema_version"] == (
        "goal_vision_replan_contract_v0"
    )
    # This fixed executable schema legitimately crosses the old 12k/320
    # ceiling; retain bounded headroom without relaxing Todo-scale growth.
    assert 12_000 < len(text) <= 14_500
    assert len(text.splitlines()) <= 400


def test_quota_cli_keeps_full_user_todo_diagnostics_on_explicit_cold_path(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "quota-user-todo-detail") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[2],
        )
        _write_user_todo_fixture(state_file)
        default_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run"]
        detail_command = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run_user_todo_summary_detail"]

        default_exit_code, default_text = _invoke_cli(default_command)
        detail_exit_code, detail_text = _invoke_cli(detail_command)

    assert default_exit_code == 0, default_text
    assert detail_exit_code == 0, detail_text
    default_payload = json.loads(default_text)
    detail_payload = json.loads(detail_text)
    default_summary = default_payload["user_todo_summary"]
    detail_summary = detail_payload["user_todo_summary"]
    assert default_summary["payload_compaction"]["schema_version"] == (
        "quota_cli_user_todo_summary_compaction_v0"
    )
    assert default_summary["payload_compaction"]["full_detail_cold_path"] == (
        "quota should-run --include-detail user-todos"
    )
    assert default_summary["gate_open_items"][0]["todo_id"] == "todo_user_gate_001"
    assert default_summary["gate_open_items"][0]["blocks_agent"] == "codex-alpha"
    assert "other_agent_bound_user_action_items" not in default_summary
    assert detail_summary["other_agent_bound_user_action_items"][0]["todo_id"] == (
        "todo_user_action_001"
    )
    assert detail_summary["payload_compaction"]["schema_version"] == (
        "quota_todo_summary_payload_compaction_v0"
    )
    assert detail_payload["todo_summary_projection"]["compacted_roles"] == ["agent"]
    for key in ("interaction_contract", "scheduler_hint", "selected_todo"):
        assert default_payload[key] == detail_payload[key]


def test_quota_cli_keeps_goal_boundary_authority_on_explicit_cold_path(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(
        tmp_path / "quota-goal-boundary-detail"
    ) as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
        )
        _write_checkpointed_boundary_authority(registry_path)
        default_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run"]
        detail_command = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run_goal_boundary_detail"]

        default_exit_code, default_text = _invoke_cli(default_command)
        detail_exit_code, detail_text = _invoke_cli(detail_command)

    assert default_exit_code == 0, default_text
    assert detail_exit_code == 0, detail_text
    default_payload = json.loads(default_text)
    detail_payload = json.loads(detail_text)
    default_boundary = default_payload["goal_boundary"]
    detail_boundary = detail_payload["goal_boundary"]
    default_authority = default_boundary["checkpointed_boundary_authority"]
    detail_authority = detail_boundary["checkpointed_boundary_authority"]
    assert default_authority["payload_compaction"] == {
        "schema_version": "quota_cli_goal_boundary_compaction_v0",
        "omitted_entry_count": 1,
        "full_detail_cold_path": "quota should-run --include-detail goal-boundary",
    }
    assert "entries" not in default_authority
    assert detail_authority["entries"]
    for key in ("active_count", "inactive_count", "active_write_scope"):
        assert default_authority[key] == detail_authority[key]
    expected_boundary = dict(detail_boundary)
    expected_authority = dict(detail_authority)
    expected_authority.pop("entries")
    expected_authority["payload_compaction"] = default_authority[
        "payload_compaction"
    ]
    expected_boundary["checkpointed_boundary_authority"] = expected_authority
    assert default_boundary == expected_boundary
    for key in ("interaction_contract", "scheduler_hint", "selected_todo"):
        assert default_payload[key] == detail_payload[key]


def test_quota_cli_deprecated_scheduler_detail_flag_matches_canonical_selector(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(
        tmp_path / "quota-detail-alias-scheduler"
    ) as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[2],
        )
        _write_user_todo_fixture(state_file)
        _write_checkpointed_boundary_authority(registry_path)
        default_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run"]
        canonical_exit_code, canonical_text = _invoke_cli(
            default_command + ["--include-detail", "scheduler"]
        )
        legacy_exit_code, legacy_text = _invoke_cli(
            default_command + ["--include-scheduler-detail"]
        )

    assert canonical_exit_code == 0, canonical_text
    assert legacy_exit_code == 0, legacy_text
    assert _quota_payload_without_rollout_receipt(
        canonical_text
    ) == _quota_payload_without_rollout_receipt(legacy_text)


def test_quota_cli_all_detail_matches_repeated_canonical_selectors(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "quota-detail-all") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[2],
        )
        _write_user_todo_fixture(state_file)
        _write_checkpointed_boundary_authority(registry_path)
        default_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["quota_should_run"]
        repeated_command = list(default_command)
        for section in ("scheduler", "agent-todos", "user-todos", "goal-boundary"):
            repeated_command.extend(["--include-detail", section])
        repeated_exit_code, repeated_text = _invoke_cli(repeated_command)
        all_exit_code, all_text = _invoke_cli(
            default_command + ["--include-detail", "all"]
        )

    assert repeated_exit_code == 0, repeated_text
    assert all_exit_code == 0, all_text
    all_payload = json.loads(all_text)
    assert _quota_payload_without_rollout_receipt(
        all_text
    ) == _quota_payload_without_rollout_receipt(repeated_text)
    assert all_payload["agent_todo_summary"]["backlog_items"]
    assert all_payload["user_todo_summary"]["other_agent_bound_user_action_items"]
    assert all_payload["goal_boundary"]["checkpointed_boundary_authority"]["entries"]


def test_agent_scoped_status_keeps_whole_goal_todo_index_on_cold_path(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "agent-status") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
        )
        command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["status"]
        agent_flag = command.index("--agent-id")
        operator_command = command[:agent_flag] + command[agent_flag + 2 :]

        exit_code, text = _invoke_cli(command)
        operator_exit_code, operator_text = _invoke_cli(operator_command)

    assert exit_code == 0, text
    payload = json.loads(text)
    todo_index = payload["todo_index"]
    assert todo_index["total_count"] > 0
    assert todo_index["items"] == []
    assert todo_index["payload_compaction"] == {
        "schema_version": "agent_lane_status_todo_index_compaction_v0",
        "omitted_item_count": todo_index["total_count"],
        "reason": (
            "status --agent-id uses the attention queue for current work and "
            "keeps the whole-goal todo index on a cold path"
        ),
        "full_detail_cold_path": "status without --agent-id or todo list",
    }
    assert payload["attention_queue"]["items"][0]["agent_todos"]["open_count"] == (
        SCENARIOS[1].todo_count
    )
    assert operator_exit_code == 0, operator_text
    operator_todo_index = json.loads(operator_text)["todo_index"]
    assert len(operator_todo_index["items"]) == operator_todo_index["total_count"]
    assert "payload_compaction" not in operator_todo_index


def test_status_and_quota_json_ignore_compatibility_reexport_bindings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "compat") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[0],
        )
        commands = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )

        def semantic_receipts() -> dict[str, dict[str, object]]:
            receipts: dict[str, dict[str, object]] = {}
            for surface_id in ("status", "quota_should_run"):
                exit_code, text = _invoke_cli(commands[surface_id])
                assert exit_code == 0, (surface_id, text)
                measurement = measure_cli_output(text, output_format="json")
                payload = measurement["payload"]
                assert isinstance(payload, dict)
                receipts[surface_id] = {
                    "top_level_keys": sorted(payload),
                    "json_shape_paths": measurement["json_shape_paths"],
                    "action_signature_sha256": measurement[
                        "action_signature_sha256"
                    ],
                }
            return receipts

        baseline = semantic_receipts()

        import loopx.quota as quota_facade
        import loopx.status as status_facade

        for facade in (status_facade, quota_facade):
            for export_name in facade._PUBLIC_COMPAT_REEXPORTS:
                monkeypatch.setattr(facade, export_name, object())

        assert semantic_receipts() == baseline


def test_collection_growth_and_bootstrap_duplication_are_explicit(tmp_path: Path) -> None:
    small = _measure_scenario(tmp_path / "small", SCENARIOS[0])
    crowded = _measure_scenario(tmp_path / "crowded", SCENARIOS[1])
    added_todos = SCENARIOS[1].todo_count - SCENARIOS[0].todo_count
    added_runs = SCENARIOS[1].run_count - SCENARIOS[0].run_count
    for spec in CLI_OUTPUT_BUDGET_SPECS:
        if spec.max_json_growth_chars_per_unit is None:
            continue
        units = (
            added_runs
            if spec.scale_axis in {"returned_run_count", "returned_evidence_count"}
            else added_todos
        )
        growth = (
            crowded[spec.surface_id]["json"]["chars"]
            - small[spec.surface_id]["json"]["chars"]
        )
        fixed_semantic_growth = spec.max_json_fixed_semantic_growth_chars
        if fixed_semantic_growth:
            assert spec.surface_id == "loopx_turn_plan"
            small_packet = small[spec.surface_id]["json"]["payload"][
                "turn_envelope"
            ].get("replan_action_packet")
            crowded_packet = crowded[spec.surface_id]["json"]["payload"][
                "turn_envelope"
            ]["replan_action_packet"]
            small_writeback = (
                small_packet.get("writeback_contract")
                if isinstance(small_packet, dict)
                else None
            )
            assert not isinstance(small_writeback, dict) or not isinstance(
                small_writeback.get("vision_authoring"), dict
            )
            assert isinstance(
                crowded_packet["writeback_contract"]["vision_authoring"], dict
            )
        assert growth <= (
            spec.max_json_growth_chars_per_unit * units + fixed_semantic_growth
        ), (
            spec.surface_id,
            growth,
            units,
            fixed_semantic_growth,
        )

    start_payload = small["start_goal_guided"]["json"]["payload"]
    bootstrap_payload = small["bootstrap_command_pack"]["json"]["payload"]
    start_duplication = start_payload["packet_summary"]["duplication_measurement"]
    bootstrap_duplication = bootstrap_payload["packet_summary"]["duplication_measurement"]
    assert start_duplication["objective_content"]["duplicate_occurrences"] <= 11
    assert start_duplication["command_content"]["duplicate_occurrences"] <= 13
    assert bootstrap_duplication["objective_content"]["duplicate_occurrences"] <= 8
    assert bootstrap_duplication["command_content"]["duplicate_occurrences"] <= 9
    assert start_duplication["objective_content"]["duplicate_occurrences"] > 0
    assert bootstrap_duplication["objective_content"]["duplicate_occurrences"] > 0


def test_explicit_compact_and_detail_modes_are_characterized(tmp_path: Path) -> None:
    # Match the other budget scenarios: runner/xdist path length is not a
    # prompt revision. Exercise real long paths separately below.
    with _stable_budget_fixture_root(tmp_path / "variants") as root:
        _assert_mode_variant_budgets(root)


def _assert_mode_variant_budgets(root: Path, *, only: str | None = None) -> None:
    project, runtime, registry_path, state_file = _write_fixture(root, SCENARIOS[0])
    for output_format in ("json", "markdown"):
        commands = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format=output_format,
        )
        for variant_id, command in commands.items():
            if only is not None and variant_id != only:
                continue
            spec = CLI_OUTPUT_MODE_VARIANT_BY_ID[variant_id]
            if output_format not in spec.output_formats:
                continue
            exit_code, text = _invoke_cli(command)
            assert exit_code == 0, (variant_id, output_format, text)
            measurement = measure_cli_output(text, output_format=output_format)  # type: ignore[arg-type]
            assert_cli_output_mode_variant(
                spec,
                output_format=output_format,  # type: ignore[arg-type]
                text=text,
                measurement=measurement,
            )


def test_brief_budget_retains_full_commands_on_real_long_paths() -> None:
    # A reproducible 128-character absolute root, independent of pytest's
    # ever-growing temp/worker prefix. Do not shorten rendered paths or raise
    # the absolute output ceiling to make this case pass.
    parent = Path(tempfile.gettempdir()).resolve()
    # tempfile contributes an eight-character random suffix. Hold input size
    # constant across Linux /tmp and macOS's longer temporary-directory root.
    prefix = "loopx-brief-".ljust(128 - len(str(parent)) - 1 - 8, "p")
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as directory:
        root = Path(directory).resolve()
        assert len(str(root)) == 128
        _assert_mode_variant_budgets(root, only="heartbeat_prompt_brief")


def test_todo_list_explicit_limit_stays_bounded_and_default_path_unchanged(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "todo-list-limit") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
        )
        limited_command = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["todo_list_limited"]
        cold_default_command = [
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
        ]
        default_command = _surface_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["todo_list"]

        limited_exit_code, limited_text = _invoke_cli(limited_command)
        cold_default_exit_code, cold_default_text = _invoke_cli(cold_default_command)
        default_exit_code, default_text = _invoke_cli(default_command)

    assert limited_exit_code == 0, limited_text
    assert cold_default_exit_code == 0, cold_default_text
    assert default_exit_code == 0, default_text
    spec = CLI_OUTPUT_MODE_VARIANT_BY_ID["todo_list_limited"]
    measurement = measure_cli_output(limited_text, output_format="json")
    assert_cli_output_mode_variant(
        spec,
        output_format="json",
        text=limited_text,
        measurement=measurement,
    )
    limited_payload = json.loads(limited_text)
    assert limited_payload["explicit_limit"] == 3
    assert limited_payload["todo_list_projection"]["view"] == (
        "explicit_limit_cold_path"
    )
    assert limited_payload["todo_list_projection"]["matched_todo_count"] == 36
    assert limited_payload["returned_todo_count"] == 3
    assert len(limited_payload["agent_todos"]["items"]) == 3
    assert limited_payload["agent_todos"]["total_count"] == 36
    assert int(measurement["chars"]) < len(cold_default_text)

    cold_default_payload = json.loads(cold_default_text)
    assert cold_default_payload["todo_count"] == 36
    assert len(cold_default_payload["todos"]) == 36
    for absent_key in ("explicit_limit", "returned_todo_count", "todo_list_projection"):
        assert absent_key not in cold_default_payload

    default_payload = json.loads(default_text)
    assert "explicit_limit" not in default_payload
    assert "returned_todo_count" in default_payload
    assert default_payload["todo_list_projection"]["view"] == "agent_lane_hot_path"
    assert_cli_output_baseline(
        CLI_OUTPUT_BUDGET_BY_ID["todo_list"],
        scenario="crowded",
        output_format="json",
        text=default_text,
        measurement=measure_cli_output(default_text, output_format="json"),
    )


def test_todo_list_thin_reduces_crowded_output_and_keeps_default_unchanged(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "todo-list-thin") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
        )
        thin_command = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["todo_list_thin"]
        cold_default_command = [
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
        ]
        thin_exit_code, thin_text = _invoke_cli(thin_command)
        default_exit_code, default_text = _invoke_cli(cold_default_command)

    assert thin_exit_code == 0, thin_text
    assert default_exit_code == 0, default_text
    spec = CLI_OUTPUT_MODE_VARIANT_BY_ID["todo_list_thin"]
    measurement = measure_cli_output(thin_text, output_format="json")
    assert_cli_output_mode_variant(
        spec,
        output_format="json",
        text=thin_text,
        measurement=measurement,
    )
    payload = json.loads(thin_text)
    assert payload["thin"] is True
    assert payload["todo_count"] == payload["matched_todo_count"] == 36
    assert payload["returned_todo_count"] == len(payload["todos"]) == 2
    assert payload["omitted_todo_count"] == 34
    assert payload["todo_list_field_projection"]["view"] == (
        "thin_explicit_view"
    )
    assert payload["todo_list_field_projection"]["item_limit_per_role"] == 2
    assert "state_file" not in payload
    assert "project" not in payload
    assert len(thin_text) < len(default_text) * 0.45

    default_payload = json.loads(default_text)
    assert "thin" not in default_payload
    assert "todo_list_field_projection" not in default_payload
    assert default_payload["state_file"]
    assert default_payload["project"]


def test_todo_list_explicit_limit_bounds_monitor_and_blocker_lanes(
    tmp_path: Path,
) -> None:
    with _stable_budget_fixture_root(tmp_path / "todo-list-limit-lanes") as stable_root:
        project, runtime, registry_path, state_file = _write_fixture(
            stable_root,
            SCENARIOS[1],
        )
        _write_todo_list_limit_stress_fixture(state_file)
        command = _mode_variant_commands(
            project=project,
            runtime=runtime,
            registry_path=registry_path,
            state_file=state_file,
            output_format="json",
        )["todo_list_limited"]
        exit_code, text = _invoke_cli(command)

    assert exit_code == 0, text
    spec = CLI_OUTPUT_MODE_VARIANT_BY_ID["todo_list_limited"]
    measurement = measure_cli_output(text, output_format="json")
    assert_cli_output_mode_variant(
        spec,
        output_format="json",
        text=text,
        measurement=measurement,
    )
    payload = json.loads(text)
    summary = payload["agent_todos"]
    assert summary["total_count"] == 200
    assert len(summary["items"]) == 3
    assert len(summary["monitor_open_items"]) == 3
    assert len(summary["blocker_items"]) == 3
    compaction = summary["payload_compaction"]
    assert compaction["view"] == "explicit_limit_cold_path"
    assert compaction["item_limit"] == 3
    assert compaction["compacted_lanes"]["monitor_open_items"] == {
        "shown": 3,
        "total": 100,
    }
    assert compaction["compacted_lanes"]["blocker_items"] == {
        "shown": 3,
        "total": 100,
    }




def test_turn_envelope_cli_preserves_codex_app_scheduler_binding(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path, state_file = _write_fixture(
        tmp_path,
        SCENARIOS[0],
    )
    command = _mode_variant_commands(
        project=project,
        runtime=runtime,
        registry_path=registry_path,
        state_file=state_file,
        output_format="json",
    )["quota_should_run_turn_envelope"]

    exit_code, text = _invoke_cli([*command, "--codex-app"])

    assert exit_code == 0, text
    payload = json.loads(text)
    assert payload["detail_ref"]["full_decision"] == (
        "loopx --format json quota should-run "
        f"--goal-id {GOAL_ID} --agent-id {AGENT_IDS[0]} --codex-app"
    )


def test_quota_should_run_cli_actions_keep_explicit_runtime_root(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path, _state_file = _write_fixture(
        tmp_path,
        SCENARIOS[0],
    )

    exit_code, text = _invoke_cli(
        [
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_IDS[0],
            "--scan-root",
            str(project),
            "--runtime-profile",
            "codex_cli",
            "--turn-instance-id",
            "turn-runtime-root-contract",
        ]
    )

    assert exit_code == 0, text
    payload = json.loads(text)
    command_prefix = f"loopx --runtime-root {runtime}"
    cli_channel = payload["interaction_contract"]["cli_channel"]
    assert cli_channel["next_cli_actions"]
    assert all(
        action.startswith(command_prefix)
        for action in cli_channel["next_cli_actions"]
    )
    settlement_plan = cli_channel["settlement_plan"]
    assert all(
        step["command_template"].startswith(command_prefix)
        for step in settlement_plan["ordered_steps"]
        if "command_template" in step
    )


def test_first_class_runtime_profiles_fit_thin_prompt_budget_and_cli_round_trip(
    tmp_path: Path,
) -> None:
    cases = (
        (SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT, "--codex-app"),
        (SchedulerRuntimeProfile.CODEX_CLI_VISIBLE, "--runtime-profile codex_cli"),
        (SchedulerRuntimeProfile.CLAUDE_CODE_VISIBLE, "--runtime-profile claude_code"),
        (SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP, "--runtime-profile generic_cli"),
        (
            SchedulerRuntimeProfile.GENERIC_CLI_OUTER_CONTROLLER,
            "--runtime-profile outer_controller",
        ),
    )
    for index, (profile, expected_binding) in enumerate(cases):
        with _stable_budget_fixture_root(tmp_path / f"profile-{index}") as root:
            project, runtime, registry_path, state_file = _write_fixture(
                root,
                SCENARIOS[0],
            )
            prompt = build_heartbeat_prompt(
                goal_id=GOAL_ID,
                active_state=state_file,
                agent_id=AGENT_IDS[0],
                thin=True,
                runtime_profile=profile.value,
            )

            assert prompt["interface_budget"]["within_budget"] is True
            assert expected_binding in prompt["quota_guard_command"]
            assert expected_binding in prompt["task_body"]
            assert " -H " not in prompt["quota_guard_command"]
            assert " -O " not in prompt["quota_guard_command"]
            assert " -M " not in prompt["quota_guard_command"]

            exit_code, text = _invoke_cli(
                [
                    "--registry",
                    str(registry_path),
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    "quota",
                    "should-run",
                    "--goal-id",
                    GOAL_ID,
                    "--agent-id",
                    AGENT_IDS[0],
                    "--scan-root",
                    str(project),
                    "--runtime-profile",
                    profile.value,
                ]
            )

            assert exit_code == 0, text
            payload = json.loads(text)
            assert payload["scheduler_hint"].get("action") != (
                "repair_scheduler_execution_context"
            )
