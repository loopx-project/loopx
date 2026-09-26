from __future__ import annotations

from .cli_commands.automation_cadence import (
    register_automation_cadence_command, handle_automation_cadence_command,
)

import argparse
import sys

from .cli_commands.agent_capabilities import register_agent_capabilities, handle_agent_capabilities
from .cli_commands.agent_directory import register_agent_directory, handle_agent_directory
from .cli_commands.agent_context import register_agent_context, handle_agent_context
from .cli_commands.todo_continuation import register_todo_continuation, handle_todo_continuation
from .cli_commands.manager_inbox import register_manager_inbox, handle_manager_inbox
from .cli_commands.delegation import register_delegation, handle_delegation
from .capabilities.content_ops.cli import (
    handle_content_ops_command,
    register_content_ops_commands,
)
from .capabilities.agent_turn_recall.cli import (
    handle_agent_turn_recall_command,
    register_agent_turn_recall_commands,
)
from .capabilities.change_quality.cli import (
    handle_change_quality_command,
    register_change_quality_commands,
)
from .capabilities.integration_branch.cli import (
    handle_integration_branch_command,
    register_integration_branch_commands,
)
from .capabilities.repository_change_window.cli import (
    handle_repository_change_window_command,
    register_repository_change_window_commands,
)
from .capabilities.decision_context.cli import (
    handle_decision_context_command,
    register_decision_context_commands,
)
from .capabilities.material_lifecycle.cli import (
    handle_material_lifecycle_command,
    register_material_lifecycle_commands,
)
from .capabilities.issue_fix.cli import (
    handle_issue_fix_command,
    register_issue_fix_commands,
)
from .capabilities.reward_memory.cli import (
    handle_reward_memory_command,
    register_reward_memory_commands,
)
from .capabilities.periodic_report.cli import (
    handle_periodic_report_command,
    register_periodic_report_commands,
)
from .capabilities.machine_configuration.cli import (
    handle_machine_configuration_command,
    register_machine_configuration_commands,
)
from .capabilities.periodic_report.post_writeback_hook import (
    build_periodic_report_post_writeback_projection,
    periodic_report_post_writeback_hooks_for_goal,
)
from .control_plane.coordination.local_authority_shadow_adapter import (
    effective_runtime_root,
)
from .capabilities.semantic_preference.cli import (
    handle_semantic_preference_command,
    register_semantic_preference_commands,
)
from .capabilities.value_connectors.cli import (
    handle_value_connector_command,
    register_value_connector_commands,
)
from .capabilities.connector_registry.cli import (
    handle_connector_command,
    register_connector_commands,
)
from .capabilities.external_research.cli import (
    handle_external_evidence_command,
    register_external_evidence_commands,
)
from .cli_commands import (
    handle_turn_command,
    handle_benchmark_command,
    handle_bootstrap_connect_command,
    handle_canary_command,
    handle_coordination_shadow_command,
    handle_authority_archive_command,
    handle_capability_command,
    handle_doctor_command,
    handle_dreaming_command,
    handle_evidence_log_command,
    handle_extension_command,
    handle_explore_command,
    handle_first_run_report_command,
    handle_goal_channel_command,
    handle_history_command,
    handle_lark_inbox_command,
    handle_lark_kanban_command,
    handle_ml_experiment_command,
    handle_preset_command,
    handle_presentation_command,
    handle_dash_command,
    handle_project_command,
    handle_project_lifecycle_command,
    handle_pr_review_command,
    handle_deepresearch_command,
    handle_ready_score_command,
    handle_review_batch_command,
    handle_registry_admin_command,
    handle_slash_commands_command,
    handle_starter_command,
    handle_summary_all_command,
    handle_support_control_command,
    handle_handoff_mode_command,
    handle_task_lease_command,
    handle_authority_shadow_command,
    handle_version_command,
    handle_host_mode_plan_command,
    handle_worker_bridge_command,
    handle_workflow_skills_command,
    register_benchmark_command_group,
    register_turn_commands,
    register_bootstrap_connect_command,
    register_canary_commands,
    register_coordination_shadow_command,
    register_authority_archive_command,
    register_capability_commands,
    register_doctor_command,
    register_dreaming_commands,
    register_evidence_log_command,
    register_extension_commands,
    register_explore_commands,
    register_goal_channel_commands,
    register_first_run_report_command,
    register_history_command,
    register_lark_inbox_commands,
    build_lark_issue_fix_reviewer_provider_hooks,
    register_lark_kanban_commands,
    register_ml_experiment_commands,
    register_preset_commands,
    register_presentation_commands,
    register_dash_commands,
    register_project_commands,
    register_project_lifecycle_commands,
    register_pr_review_command,
    register_deepresearch_command,
    register_quota_command,
    register_ready_score_command,
    register_review_batch_commands,
    register_registry_admin_commands,
    register_slash_commands_command,
    register_starter_commands,
    register_status_commands,
    register_summary_all_command,
    register_support_control_commands,
    register_handoff_mode_command,
    register_task_lease_command,
    register_authority_shadow_command,
    register_todo_command,
    register_version_command,
    register_host_mode_plan_command,
    register_worker_bridge_commands,
    register_workflow_skills_command,
)
from .cli_commands.opencode2_goal_worker import (
    handle_opencode2_goal_worker_command,
    register_opencode2_goal_worker_command,
)
from .cli_commands.shared_goal_alignment import (
    handle_shared_goal_alignment_command,
    register_shared_goal_alignment_command,
)
from .cli_commands.goal_acceptance import (
    handle_goal_acceptance_command,
    register_goal_acceptance_command,
)
from .cli_commands.goal_amendment_proposal import (
    handle_goal_amendment_proposal_command,
    register_goal_amendment_proposal_command,
)
from .cli_commands.reliability_diagnostics import (
    handle_reliability_diagnostics_command,
    register_reliability_diagnostics_commands,
)
from .cli_rollout import append_cli_rollout_event
from .capabilities.project_skill_delivery.cli import (
    handle_project_skill_command,
    register_project_skill_commands,
)
from .extensions.lark.periodic_report_cli import (
    handle_lark_periodic_report_command,
    register_lark_periodic_report_commands,
)
from .help_surface import (
    build_command_reference_payload,
    render_command_reference_markdown,
    render_concise_help,
    top_level_help_requested,
)
from .cli_runtime import (
    LoopXArgumentParser,
    add_subcommand_format,
    build_cli_parser,
    dispatch_common_command,
    enforce_native_controller_guard,
    output_format,
    print_payload,
    resolve_cli_registry,
    resolve_global_output_format,
    user_supplied_registry,
)


def _demo_not_available_message(command: str) -> str:
    return (
        f"`{command}` is a LoopX **demo** showcase that ships only with a "
        "source checkout (demo/), not in installed builds. Clone the loopx "
        "repository to run it."
    )


def _register_demo_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format,
) -> None:
    """Register demo-only commands backed by the non-shipped demo/ package.

    In a source checkout the full demo command surface is wired; in an
    installed build the demo package is absent, so only a stub is registered and
    dispatch reports the demo is not available.
    """

    for demo_command, module, register_name in (
        ("auto-research", "demo.auto_research.cli", "register_auto_research_commands"),
        ("multi-agent", "demo.multi_agent_cli", "register_multi_agent_commands"),
    ):
        try:
            register = getattr(
                __import__(module, fromlist=[register_name]),
                register_name,
            )
            register(subparsers, add_subcommand_format)
        except Exception:
            stub = subparsers.add_parser(
                demo_command,
                help=(
                    f"(demo) {demo_command} showcase — requires a loopx "
                    "source checkout"
                ),
            )
            add_subcommand_format(stub)


def build_parser() -> LoopXArgumentParser:
    parser, sub = build_cli_parser()

    register_version_command(sub, add_subcommand_format)

    commands_parser = sub.add_parser(
        "commands",
        help="Show grouped LoopX command reference for operators and contributors.",
    )
    add_subcommand_format(commands_parser)

    register_bootstrap_connect_command(sub)

    register_starter_commands(sub)

    register_doctor_command(sub, add_subcommand_format)

    register_first_run_report_command(sub)

    register_opencode2_goal_worker_command(sub)

    register_worker_bridge_commands(sub, add_subcommand_format)

    register_support_control_commands(sub, add_subcommand_format)

    register_canary_commands(sub, add_subcommand_format)

    register_capability_commands(sub, add_subcommand_format)

    register_reliability_diagnostics_commands(sub, add_subcommand_format)

    register_extension_commands(sub, add_subcommand_format)

    register_change_quality_commands(sub, add_subcommand_format)

    register_integration_branch_commands(sub, add_subcommand_format)

    register_repository_change_window_commands(sub, add_subcommand_format)

    register_content_ops_commands(sub, add_subcommand_format)

    register_decision_context_commands(sub, add_subcommand_format)

    register_material_lifecycle_commands(sub, add_subcommand_format)

    register_project_skill_commands(sub, add_subcommand_format)

    register_issue_fix_commands(sub, add_subcommand_format)

    register_reward_memory_commands(sub, add_subcommand_format)

    register_agent_turn_recall_commands(sub, add_subcommand_format)

    register_review_batch_commands(sub, add_subcommand_format)

    register_periodic_report_commands(
        sub,
        add_subcommand_format,
        provider_command_registrars=(register_lark_periodic_report_commands,),
    )

    register_machine_configuration_commands(sub, add_subcommand_format)

    register_semantic_preference_commands(sub, add_subcommand_format)

    register_value_connector_commands(sub, add_subcommand_format)

    register_connector_commands(sub, add_subcommand_format)

    register_external_evidence_commands(sub, add_subcommand_format)

    register_ml_experiment_commands(sub, add_subcommand_format)

    _register_demo_commands(sub, add_subcommand_format)

    register_turn_commands(sub, add_subcommand_format)
    register_host_mode_plan_command(sub, add_subcommand_format)
    register_preset_commands(sub, add_subcommand_format)
    register_presentation_commands(sub, add_subcommand_format)
    register_dash_commands(sub, add_subcommand_format)
    register_project_commands(sub, add_subcommand_format)
    register_ready_score_command(sub, add_subcommand_format)

    register_registry_admin_commands(sub)

    register_history_command(sub)

    register_benchmark_command_group(sub, add_subcommand_format)

    register_project_lifecycle_commands(sub, add_subcommand_format)
    register_goal_channel_commands(sub, add_subcommand_format)
    register_manager_inbox(sub, add_subcommand_format)
    register_delegation(sub, add_subcommand_format)
    register_agent_capabilities(sub, add_subcommand_format)
    register_agent_context(sub, add_subcommand_format)
    register_agent_directory(sub, add_subcommand_format)
    register_lark_inbox_commands(sub, add_subcommand_format)
    register_lark_kanban_commands(sub, add_subcommand_format)

    register_status_commands(sub, add_subcommand_format)
    register_summary_all_command(sub, add_subcommand_format)
    register_pr_review_command(sub, add_subcommand_format)
    register_deepresearch_command(sub, add_subcommand_format)
    register_slash_commands_command(sub, add_subcommand_format)
    register_workflow_skills_command(sub, add_subcommand_format)
    register_dreaming_commands(sub, add_subcommand_format)
    register_evidence_log_command(sub, add_subcommand_format)
    register_explore_commands(sub, add_subcommand_format)
    register_todo_command(sub, add_subcommand_format)
    register_coordination_shadow_command(sub, add_subcommand_format)
    register_automation_cadence_command(sub, add_subcommand_format)
    register_authority_archive_command(sub, add_subcommand_format)
    register_task_lease_command(sub, add_subcommand_format)
    register_authority_shadow_command(sub, add_subcommand_format)
    register_todo_continuation(sub, add_subcommand_format)
    register_handoff_mode_command(sub, add_subcommand_format)
    register_shared_goal_alignment_command(sub, add_subcommand_format)
    register_goal_acceptance_command(sub, add_subcommand_format)
    register_goal_amendment_proposal_command(sub, add_subcommand_format)
    register_quota_command(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    if top_level_help_requested(raw_argv):
        print(render_concise_help(sys.argv[0] if argv is None else "loopx"), end="")
        return 0
    try:
        from demo.auto_research.cli import rewrite_auto_research_question_argv

        raw_argv = rewrite_auto_research_question_argv(raw_argv)
    except Exception:
        pass  # demo package absent in installed builds; no question rewrite
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    args.format = resolve_global_output_format(args)
    guard_result = enforce_native_controller_guard(args)
    if guard_result is not None:
        return guard_result
    registry_path, registry_was_configured = resolve_cli_registry(args, raw_argv)

    common_command_result = dispatch_common_command(
        args,
        registry_path=registry_path,
        allow_missing_registry=not user_supplied_registry(raw_argv),
    )
    if common_command_result is not None:
        return common_command_result

    version_result = handle_version_command(args, output_format=output_format, print_payload=print_payload)
    if version_result is not None:
        return version_result

    if args.command == "commands":
        print_payload(
            build_command_reference_payload(),
            output_format(args),
            render_command_reference_markdown,
        )
        return 0

    bootstrap_connect_result = handle_bootstrap_connect_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
    )
    if bootstrap_connect_result is not None:
        return bootstrap_connect_result

    starter_result = handle_starter_command(args, print_payload)
    if starter_result is not None:
        return starter_result

    if args.command == "doctor":
        return handle_doctor_command(args, print_payload)

    workflow_skills_result = handle_workflow_skills_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if workflow_skills_result is not None:
        return workflow_skills_result

    if args.command == "first-run-report":
        return handle_first_run_report_command(args, print_payload)

    if args.command == "opencode2-goal-worker":
        return handle_opencode2_goal_worker_command(args, print_payload)

    worker_bridge_result = handle_worker_bridge_command(
        args,
        print_payload=print_payload,
        output_format=output_format,
        registry_path=registry_path,
    )
    if worker_bridge_result is not None:
        return worker_bridge_result

    support_control_result = handle_support_control_command(
        args,
        registry_path=registry_path,
        registry_was_supplied=user_supplied_registry(raw_argv),
        print_payload=print_payload,
        output_format=output_format,
    )
    if support_control_result is not None:
        return support_control_result

    canary_result = handle_canary_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if canary_result is not None:
        return canary_result

    capability_result = handle_capability_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if capability_result is not None:
        return capability_result

    reliability_diagnostics_result = handle_reliability_diagnostics_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if reliability_diagnostics_result is not None:
        return reliability_diagnostics_result

    extension_result = handle_extension_command(
        args,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if extension_result is not None:
        return extension_result

    change_quality_result = handle_change_quality_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if change_quality_result is not None:
        return change_quality_result

    integration_branch_result = handle_integration_branch_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if integration_branch_result is not None:
        return integration_branch_result

    repository_change_window_result = handle_repository_change_window_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if repository_change_window_result is not None:
        return repository_change_window_result

    if args.command == "ml-experiment":
        return handle_ml_experiment_command(args, output_format=output_format, print_payload=print_payload)

    turn_result = handle_turn_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if turn_result is not None:
        return turn_result

    host_mode_plan_result = handle_host_mode_plan_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if host_mode_plan_result is not None:
        return host_mode_plan_result

    preset_result = handle_preset_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if preset_result is not None:
        return preset_result

    presentation_result = handle_presentation_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if presentation_result is not None:
        return presentation_result

    dash_result = handle_dash_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        print_payload=print_payload,
        output_format=output_format,
    )
    if dash_result is not None:
        return dash_result

    project_result = handle_project_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if project_result is not None:
        return project_result

    ready_score_result = handle_ready_score_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if ready_score_result is not None:
        return ready_score_result

    if args.command == "content-ops":
        return handle_content_ops_command(args, output_format=output_format, print_payload=print_payload)

    if args.command == "issue-fix":
        return handle_issue_fix_command(
            args,
            registry_path=registry_path,
            runtime_root_arg=args.runtime_root,
            reviewer_provider_hooks_factory=(
                lambda: build_lark_issue_fix_reviewer_provider_hooks(
                    runtime_root_arg=args.runtime_root
                )
            ),
            output_format=output_format,
            print_payload=print_payload,
        )

    reward_memory_result = handle_reward_memory_command(
        args,
        registry_path=registry_path,
        output_format=output_format,
        print_payload=print_payload,
    )
    if reward_memory_result is not None:
        return reward_memory_result

    agent_turn_recall_result = handle_agent_turn_recall_command(
        args,
        registry_path=registry_path,
        output_format=output_format,
        print_payload=print_payload,
    )
    if agent_turn_recall_result is not None:
        return agent_turn_recall_result

    decision_context_result = handle_decision_context_command(
        args,
        runtime_root=(
            effective_runtime_root(registry_path, args.runtime_root)
            if args.command == "decision-context"
            and args.decision_context_command
            in {"recall-context", "prepare-evidence", "prepare-review", "capture"}
            else None
        ),
        output_format=output_format,
        print_payload=print_payload,
    )
    if decision_context_result is not None:
        return decision_context_result

    material_lifecycle_result = handle_material_lifecycle_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if material_lifecycle_result is not None:
        return material_lifecycle_result

    project_skill_result = handle_project_skill_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if project_skill_result is not None:
        return project_skill_result

    review_batch_result = handle_review_batch_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if review_batch_result is not None:
        return review_batch_result

    periodic_report_result = handle_periodic_report_command(
        args,
        runtime_root_arg=args.runtime_root,
        registry_path=registry_path,
        output_format=output_format,
        print_payload=print_payload,
        provider_command_handlers=(handle_lark_periodic_report_command,),
    )
    if periodic_report_result is not None:
        return periodic_report_result

    machine_configuration_result = handle_machine_configuration_command(
        args,
        runtime_root_arg=args.runtime_root,
        registry_path=registry_path,
        output_format=output_format,
        print_payload=print_payload,
    )
    if machine_configuration_result is not None:
        return machine_configuration_result

    semantic_preference_result = handle_semantic_preference_command(
        args,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if semantic_preference_result is not None:
        return semantic_preference_result

    if args.command == "value-connectors":
        return handle_value_connector_command(args, output_format=output_format, print_payload=print_payload)

    connector_result = handle_connector_command(
        args, output_format=output_format, print_payload=print_payload,
    )
    if connector_result is not None:
        return connector_result

    external_evidence_result = handle_external_evidence_command(
        args, output_format=output_format, print_payload=print_payload,
    )
    if external_evidence_result is not None:
        return external_evidence_result

    registry_admin_result = handle_registry_admin_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
    )
    if registry_admin_result is not None:
        return registry_admin_result

    benchmark_result = handle_benchmark_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
        output_format=output_format,
    )
    if benchmark_result is not None:
        return benchmark_result
    if args.command == "history":
        return handle_history_command(
            args,
            registry_path=registry_path,
            runtime_root_arg=args.runtime_root,
            print_payload=print_payload,
        )

    project_lifecycle_result = handle_project_lifecycle_command(
        args,
        registry_path=registry_path,
        print_payload=print_payload,
        output_format=output_format,
        append_cli_rollout_event=append_cli_rollout_event,
        post_writeback_hooks=(
            periodic_report_post_writeback_hooks_for_goal(
                registry_path=registry_path,
                goal_id=args.goal_id,
                runtime_root=effective_runtime_root(
                    registry_path,
                    args.runtime_root,
                ),
            )
            if args.command == "refresh-state"
            else ()
        ),
        post_writeback_projection_builder=(
            build_periodic_report_post_writeback_projection
            if args.command == "refresh-state"
            else None
        ),
    )
    if project_lifecycle_result is not None:
        return project_lifecycle_result

    goal_channel_result = handle_goal_channel_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        print_payload=print_payload,
        output_format=output_format,
    )
    if goal_channel_result is not None:
        return goal_channel_result

    lark_kanban_result = handle_lark_kanban_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        print_payload=print_payload,
        output_format=output_format,
    )
    if lark_kanban_result is not None:
        return lark_kanban_result

    if args.command == "agent-capabilities":
        return handle_agent_capabilities(
            args, registry_path, effective_runtime_root(registry_path, args.runtime_root),
            print_payload, output_format,
        )

    if args.command == "agent-context":
        return handle_agent_context(
            args,
            registry_path,
            effective_runtime_root(registry_path, args.runtime_root),
            print_payload,
            output_format,
        )

    if args.command == "agent-directory":
        return handle_agent_directory(
            args, registry_path, effective_runtime_root(registry_path, args.runtime_root),
            print_payload, output_format,
        )

    if args.command == "manager-inbox":
        return handle_manager_inbox(args, registry_path, effective_runtime_root(registry_path, args.runtime_root))
    if args.command == "delegation":
        return handle_delegation(args, registry_path, effective_runtime_root(registry_path, args.runtime_root))

    lark_inbox_result = handle_lark_inbox_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if lark_inbox_result is not None:
        return lark_inbox_result

    summary_all_result = handle_summary_all_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if summary_all_result is not None:
        return summary_all_result

    pr_review_result = handle_pr_review_command(
        args,
        registry_path=registry_path,
        output_format=output_format,
        print_payload=print_payload,
        runtime_root=(
            effective_runtime_root(registry_path, args.runtime_root)
            if registry_path.exists()
            else None
        ),
    )
    if pr_review_result is not None:
        return pr_review_result

    deepresearch_result = handle_deepresearch_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if deepresearch_result is not None:
        return deepresearch_result

    slash_commands_result = handle_slash_commands_command(
        args,
        output_format=output_format,
        print_payload=print_payload,
    )
    if slash_commands_result is not None:
        return slash_commands_result

    if args.command == "dreaming":
        return handle_dreaming_command(
            args,
            registry_path=registry_path,
            runtime_root_arg=args.runtime_root,
            output_format=output_format,
            print_payload=print_payload,
        )

    evidence_log_result = handle_evidence_log_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
        append_cli_rollout_event=append_cli_rollout_event,
    )
    if evidence_log_result is not None:
        return evidence_log_result

    explore_result = handle_explore_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        print_payload=print_payload,
        output_format=output_format,
    )
    if explore_result is not None:
        return explore_result

    cadence_result = handle_automation_cadence_command(
        args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
        print_payload=print_payload, output_format=output_format,
    )
    if cadence_result is not None:
        return cadence_result
    authority_archive_result = handle_authority_archive_command(
        args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
        output_format=output_format, print_payload=print_payload,
    )
    if authority_archive_result is not None:
        return authority_archive_result

    coordination_shadow_result = handle_coordination_shadow_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if coordination_shadow_result is not None:
        return coordination_shadow_result

    task_lease_result = handle_task_lease_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if task_lease_result is not None:
        return task_lease_result

    authority_shadow_result = handle_authority_shadow_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if authority_shadow_result is not None:
        return authority_shadow_result

    continuation_result = handle_todo_continuation(
        args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
        output_format=output_format, print_payload=print_payload,
    )
    if continuation_result is not None:
        return continuation_result

    handoff_mode_result = handle_handoff_mode_command(
        args,
        registry_path=registry_path,
        output_format=output_format,
        print_payload=print_payload,
        runtime_root_arg=args.runtime_root,
    )
    if handoff_mode_result is not None:
        return handoff_mode_result

    goal_acceptance_result = handle_goal_acceptance_command(
        args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
        output_format=output_format, print_payload=print_payload,
    )
    if goal_acceptance_result is not None:
        return goal_acceptance_result

    shared_goal_alignment_result = handle_shared_goal_alignment_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if shared_goal_alignment_result is not None:
        return shared_goal_alignment_result

    goal_amendment_proposal_result = handle_goal_amendment_proposal_command(
        args,
        registry_path=registry_path,
        runtime_root_arg=args.runtime_root,
        output_format=output_format,
        print_payload=print_payload,
    )
    if goal_amendment_proposal_result is not None:
        return goal_amendment_proposal_result

    if args.command == "auto-research":
        try:
            from demo.auto_research.cli import handle_auto_research_command

            return handle_auto_research_command(
                args,
                registry_path=registry_path,
                runtime_root_arg=args.runtime_root,
                output_format=output_format,
                print_payload=print_payload,
            )
        except Exception:
            print(_demo_not_available_message("auto-research"))
            return 1

    if args.command == "multi-agent":
        try:
            from demo.multi_agent_cli import handle_multi_agent_command

            return handle_multi_agent_command(
                args,
                registry_path=registry_path,
                runtime_root_arg=args.runtime_root,
                output_format=output_format,
                print_payload=print_payload,
            )
        except Exception:
            print(_demo_not_available_message("multi-agent"))
            return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
