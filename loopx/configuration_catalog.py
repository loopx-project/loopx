from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from typing import Any

from .capabilities.configuration_ui import build_capability_configuration_catalog
from .control_plane.agent_context import agent_context_descriptor
from .control_plane.goals.goal_vision_policy import completed_todo_replan_threshold

DEFAULT_MULTI_SUBAGENT_MAX_CHILDREN = 2


def build_configuration_capability_descriptors() -> list[dict[str, Any]]:
    """Discover the same features as Goal settings without inventing Goal state.

    Only static descriptor fields cross this boundary: no current values,
    Goal-specific commands, or synthetic effective configuration.
    """
    catalog = build_goal_configuration_catalog(
        goal_id="",
        settings={},
        feature_summary={},
        default_multi_subagent_max_children=DEFAULT_MULTI_SUBAGENT_MAX_CHILDREN,
        explore_harness_profiles=(),
    )
    fields = ("feature_id", "display_name", "availability", "effect", "documentation")
    return [
        {key: feature[key] for key in fields if key in feature}
        for feature in catalog["features"]
    ]


def _configure_command(
    goal_id: str,
    *arguments: str,
    execute: bool = False,
) -> str:
    parts = ["loopx", "configure-goal", "--goal-id", goal_id, *arguments]
    if execute:
        parts.append("--execute")
    return shlex.join(parts)


def build_goal_configuration_catalog(
    *,
    goal_id: str,
    settings: Mapping[str, Any],
    feature_summary: Mapping[str, Any],
    default_multi_subagent_max_children: int,
    explore_harness_profiles: Sequence[str],
    machine_inheritable_goal_overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the on-demand configuration read model for optional features."""

    orchestration = (
        settings.get("orchestration")
        if isinstance(settings.get("orchestration"), Mapping)
        else {}
    )
    graph = (
        feature_summary.get("explore_graph")
        if isinstance(feature_summary.get("explore_graph"), Mapping)
        else {}
    )
    harness = (
        feature_summary.get("explore_harness")
        if isinstance(feature_summary.get("explore_harness"), Mapping)
        else {}
    )
    lark_event_inbox = (
        feature_summary.get("lark_event_inbox")
        if isinstance(feature_summary.get("lark_event_inbox"), Mapping)
        else {}
    )
    lark_kanban_heartbeat_sync = (
        feature_summary.get("lark_kanban_heartbeat_sync")
        if isinstance(feature_summary.get("lark_kanban_heartbeat_sync"), Mapping)
        else {}
    )
    reward_memory = (
        feature_summary.get("reward_memory")
        if isinstance(feature_summary.get("reward_memory"), Mapping)
        else {}
    )
    change_quality = (
        feature_summary.get("change_quality_qualification")
        if isinstance(feature_summary.get("change_quality_qualification"), Mapping)
        else {}
    )
    inspect_command = _configure_command(goal_id)
    multi_enable_args = (
        "--multi-subagent-feature",
        "enabled",
        "--max-children",
        str(default_multi_subagent_max_children),
        "--align-codex-subagent-capacity",
    )
    peer_coordination = (
        feature_summary.get("peer_task_coordination")
        if isinstance(feature_summary.get("peer_task_coordination"), Mapping)
        else {}
    )
    local_authority_shadow = (
        feature_summary.get("local_authority_shadow")
        if isinstance(feature_summary.get("local_authority_shadow"), Mapping)
        else {}
    )
    coordination_runtime_shadow = (
        feature_summary.get("coordination_runtime_shadow")
        if isinstance(feature_summary.get("coordination_runtime_shadow"), Mapping)
        else {}
    )
    graph_enable_args = ("--explore-graph-enabled",)
    harness_enable_args = (
        "--explore-harness-enabled",
        "--explore-harness-profile",
        "generic",
    )

    catalog: dict[str, Any] = {
        "schema_version": "loopx_goal_configuration_catalog_v0",
        "scope": "default_off_optional_capabilities",
        "all_settings_help_command": "loopx configure-goal --help",
        "disclosure_policy": {
            "mode": "on_demand",
            "first_run_configuration_required": False,
            "inspect_command": inspect_command,
            "mutation_policy": "preview_without_execute_then_apply_explicitly",
            "agent_rule": (
                "Do not enable optional features during onboarding or merely because they "
                "exist. Inspect this catalog only when the task needs the capability, then "
                "preview and explain the boundary change before apply."
            ),
        },
        "features": [
            {
                "feature_id": "todo_replan_cadence",
                "display_name": "Goal review cadence",
                "availability": "supported_opt_in",
                "default": {"completed_todos": 5},
                "current": {
                    "completed_todos": completed_todo_replan_threshold(
                        settings.get("execution_profile")
                    ),
                },
                "consider_when": "A short Todo chain needs earlier review against the Goal.",
                "effect": (
                    "Requires review after 1–5 same-agent advancement Todo completions "
                    "without a covering outcome checkpoint; default 5 in standard and fine modes."
                ),
                "does_not": [
                    "create host continuation turns or interrupt running work",
                    "count open Todos, protocol steps, or another Agent's completions",
                    "bypass quota, permissions, or outcome evidence requirements",
                ],
                "commands": {
                    "preview_enable": _configure_command(
                        goal_id, "--execution-replan-after-todos", "3"
                    ),
                    "apply_enable": _configure_command(
                        goal_id, "--execution-replan-after-todos", "3", execute=True
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--clear-execution-replan-after-todos"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--clear-execution-replan-after-todos", execute=True
                    ),
                    "verify": [inspect_command],
                },
                "documentation": {
                    "path": "docs/quota-allocation.md#completed-todo-review-cadence",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "docs/quota-allocation.md#completed-todo-review-cadence"
                    ),
                },
            },
            {
                "feature_id": "local_authority_shadow",
                "display_name": "Local post-commit authority observation",
                "availability": "experimental_opt_in",
                "default": {"enabled": False},
                "current": {
                    "enabled": local_authority_shadow.get("enabled") is True,
                    "mode": local_authority_shadow.get("mode"),
                    "status": local_authority_shadow.get("status", "disabled"),
                },
                "consider_when": (
                    "A Goal needs to exercise the first Stage 2C observation "
                    "plumbing while legacy local writers remain authoritative."
                ),
                "effect": (
                    "Captures a best-effort post-commit snapshot of Todo and "
                    "task-lease state through the FileAuthorityStore contract."
                ),
                "does_not": [
                    "read the candidate for lifecycle decisions",
                    "write candidate state back into Markdown or task-lease files",
                    "promote shared authority or fence legacy writers",
                    "bind the snapshot to the exact primary transaction",
                    "guarantee delivery through a durable outbox",
                    "compare source and candidate or issue a parity verdict",
                ],
                "commands": {
                    "preview_enable": _configure_command(
                        goal_id, "--local-authority-shadow-file"
                    ),
                    "apply_enable": _configure_command(
                        goal_id, "--local-authority-shadow-file", execute=True
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--clear-local-authority-shadow"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--clear-local-authority-shadow", execute=True
                    ),
                    "verify": [inspect_command],
                },
                "documentation": {
                    "path": "docs/architecture/rfcs/shared-goal-authority-state-provider-v0.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "docs/architecture/rfcs/shared-goal-authority-state-provider-v0.md"
                    ),
                },
            },
            {
                "feature_id": "coordination_runtime_shadow",
                "display_name": "Transaction-bound coordination shadow",
                "availability": "experimental_opt_in",
                "default": {"enabled": False},
                "current": {
                    "enabled": coordination_runtime_shadow.get("enabled") is True,
                    "provider": coordination_runtime_shadow.get("provider"),
                    "status": coordination_runtime_shadow.get("status", "configuration_absent"),
                },
                "consider_when": (
                    "A legacy Goal is being qualified for an explicit reviewed "
                    "cutover to canonical local authority."
                ),
                "effect": (
                    "Captures transaction-bound Todo and task-lease mutations in "
                    "the file-v0 shadow used by parity qualification."
                ),
                "does_not": [
                    "promote the Goal or fence legacy writers",
                    "start a managed worker",
                    "make the shadow authoritative for lifecycle decisions",
                ],
                "commands": {
                    "preview_enable": _configure_command(
                        goal_id, "--coordination-runtime-shadow-file"
                    ),
                    "apply_enable": _configure_command(
                        goal_id, "--coordination-runtime-shadow-file", execute=True
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--clear-coordination-runtime-shadow"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--clear-coordination-runtime-shadow", execute=True
                    ),
                    "verify": [
                        inspect_command,
                        f"loopx --format json coordination-shadow inspect --goal-id {goal_id}",
                    ],
                },
                "documentation": {
                    "path": "docs/architecture/rfcs/shared-goal-authority-state-provider-v0.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "docs/architecture/rfcs/shared-goal-authority-state-provider-v0.md"
                    ),
                },
            },
            {
                "feature_id": "multi_subagent",
                "context_contribution": agent_context_descriptor(),
                "display_name": "Adaptive child capacity",
                "availability": "supported_opt_in",
                "default": {
                    "enabled": False,
                    "max_children": default_multi_subagent_max_children,
                    "allowed_domains": [],
                },
                "current": {
                    "enabled": feature_summary.get("multi_subagent") == "enabled",
                    "max_children": orchestration.get("max_children"),
                    "allowed_domains": list(orchestration.get("allowed_domains") or []),
                    **(
                        dict(orchestration["model_config"])
                        if "model_config" in orchestration
                        else {}
                    ),
                },
                "required_inputs": {},
                "consider_when": (
                    "The goal has at least two independent, non-overlapping work items and "
                    "the host can run child agents."
                ),
                "effect": (
                    "Sets the hard capacity boundary for adaptive child orchestration. "
                    "The preview also reads Codex host capacity; the one-click apply may "
                    "raise that host limit without lowering an existing higher value. "
                    "Optional --allowed-domain values narrow eligible Todo lanes; without "
                    "them, the task coordinator still decides whether, what, and how to "
                    "parallelize within every other admission boundary."
                ),
                "does_not": [
                    "force single-agent or multi-agent execution",
                    "create an agent hierarchy or durable authority",
                    "bypass todo claims, quota, gates, capabilities, or write scope",
                ],
                "commands": {
                    "preview_enable": _configure_command(goal_id, *multi_enable_args),
                    "apply_enable": _configure_command(
                        goal_id, *multi_enable_args, execute=True
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--multi-subagent-feature", "off"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--multi-subagent-feature", "off", execute=True
                    ),
                    "verify": [
                        inspect_command,
                        shlex.join(
                            ["loopx", "quota", "should-run", "--goal-id", goal_id]
                        ),
                    ],
                },
                "documentation": {
                    "path": "docs/integrations/codex-subagent-orchestration.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "docs/integrations/codex-subagent-orchestration.md"
                    ),
                },
            },
            {
                "feature_id": "peer_task_coordination",
                "display_name": "Registered-peer task coordination",
                "availability": "supported_explicit_opt_in",
                "default": {"enabled": False},
                "current": {
                    "enabled": peer_coordination.get("enabled") is True,
                    "coordinator_agent_id": peer_coordination.get(
                        "coordinator_agent_id"
                    ),
                },
                "required_inputs": {
                    "registered-agent-id": (
                        "Select one already registered peer explicitly; registration "
                        "alone does not grant coordination authority."
                    )
                },
                "consider_when": (
                    "The host can activate or resume durable peer runtimes and one "
                    "peer must coordinate an explicit task bundle."
                ),
                "effect": (
                    "Projects peer-owned lanes only to the selected coordinator, "
                    "subject to per-turn peer_agent_activation capability admission."
                ),
                "does_not": [
                    "auto-elect a coordinator",
                    "allow cross-owner todo mutation",
                    "make dormant or non-resumable peer lanes executable",
                ],
                "commands": {
                    "preview_enable": _configure_command(
                        goal_id,
                        "--peer-task-coordinator",
                        "<registered-agent-id>",
                    ),
                    "apply_enable": _configure_command(
                        goal_id,
                        "--peer-task-coordinator",
                        "<registered-agent-id>",
                        execute=True,
                    ),
                    "preview_disable": _configure_command(
                        goal_id,
                        "--clear-peer-task-coordinator",
                    ),
                    "apply_disable": _configure_command(
                        goal_id,
                        "--clear-peer-task-coordinator",
                        execute=True,
                    ),
                    "verify": [inspect_command],
                },
                "documentation": {
                    "path": "docs/integrations/codex-subagent-orchestration.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "docs/integrations/codex-subagent-orchestration.md"
                    ),
                },
            },
            {
                "feature_id": "explore_graph",
                "display_name": "Explore Graph",
                "availability": "supported_opt_in",
                "default": {"enabled": False},
                "current": {"enabled": graph.get("enabled") is True},
                "consider_when": (
                    "The goal needs a durable topology of hypotheses, evidence, decisions, "
                    "or an already configured operator-facing graph sink."
                ),
                "effect": "Projects durable Explore evidence after material refreshes.",
                "does_not": [
                    "enable Explore Harness",
                    "spawn workers, claim todos, or spend quota by itself",
                ],
                "commands": {
                    "preview_enable": _configure_command(goal_id, *graph_enable_args),
                    "apply_enable": _configure_command(
                        goal_id, *graph_enable_args, execute=True
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--no-explore-graph-enabled"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--no-explore-graph-enabled", execute=True
                    ),
                    "verify": [
                        inspect_command,
                        shlex.join(
                            [
                                "loopx",
                                "explore",
                                "graph",
                                "--goal-id",
                                goal_id,
                                "--graph-format",
                                "mermaid",
                            ]
                        ),
                    ],
                },
                "documentation": {
                    "path": "loopx/capabilities/explore/README.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "loopx/capabilities/explore/README.md"
                    ),
                },
            },
            {
                "feature_id": "explore_harness",
                "display_name": "Explore Harness",
                "availability": "supported_opt_in",
                "default": {"enabled": False, "profile": "generic"},
                "current": {
                    "enabled": harness.get("enabled") is True,
                    "profile": harness.get("profile"),
                },
                "profiles": list(explore_harness_profiles),
                "consider_when": (
                    "The goal benefits from comparing alternative branches with explicit "
                    "evaluation criteria and guardrails."
                ),
                "effect": "Enables read-only Explore branch and worker-lane planning.",
                "does_not": [
                    "enable Explore Graph",
                    "launch workers, claim todos, acquire leases, mutate state, or spend quota",
                ],
                "commands": {
                    "preview_enable": _configure_command(goal_id, *harness_enable_args),
                    "apply_enable": _configure_command(
                        goal_id, *harness_enable_args, execute=True
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--no-explore-harness-enabled"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--no-explore-harness-enabled", execute=True
                    ),
                    "verify": [
                        inspect_command,
                        shlex.join(
                            [
                                "loopx",
                                "explore",
                                "worker-branch-plan",
                                "--goal-id",
                                goal_id,
                                "--harness-profile",
                                str(harness.get("profile") or "generic"),
                            ]
                        ),
                    ],
                },
                "documentation": {
                    "path": "loopx/capabilities/explore/README.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "loopx/capabilities/explore/README.md"
                    ),
                },
            },
            {
                "feature_id": "pull_request_review",
                "display_name": "Pull-request review",
                "availability": "supported",
                "default": {"wait_for_ci": True, "review_priority": "other-developers-first"},
                "current": feature_summary.get("pull_request_review") or {},
                "effect": "Choose CI waiting and review priority for this Goal; clear the complete override to restore machine defaults.",
                "documentation": {"path": "loopx/capabilities/pr_review_queue/README.md"},
            },
            {
                "feature_id": "change_quality_qualification",
                "display_name": "Change quality qualification",
                "availability": "supported_opt_in",
                "default": {
                    "enabled": False,
                    "safe_fix": False,
                    "strict_receipt": False,
                },
                "current": {
                    "enabled": change_quality.get("enabled") is True,
                    "safe_fix": change_quality.get("safe_fix") is True,
                    "strict_receipt": change_quality.get("strict_receipt") is True,
                },
                "consider_when": (
                    "A goal should review every non-trivial final diff and optionally "
                    "require exact-scope evidence before merge."
                ),
                "effect": (
                    "Prepares a provider-neutral review packet, permits at most one "
                    "policy-authorized safe-fix pass, and can enforce an exact-diff receipt."
                ),
                "does_not": [
                    "change files unless safe_fix is explicitly enabled",
                    "make subjective style preferences blocking findings",
                    "grant authority, expand permissions, or bypass repository validation",
                    "delegate merge authority to Turn or the reviewing model",
                ],
                "commands": {
                    "preview_skill_install": (
                        "loopx project-skill install --project . --skill "
                        "loopx-change-quality --surface codex"
                    ),
                    "apply_skill_install": (
                        "loopx project-skill install --project . --skill "
                        "loopx-change-quality --surface codex --execute"
                    ),
                    "preview_enable": _configure_command(
                        goal_id,
                        "--change-quality-enabled",
                        "--change-quality-safe-fix",
                        "--change-quality-strict-receipt",
                    ),
                    "apply_enable": _configure_command(
                        goal_id,
                        "--change-quality-enabled",
                        "--change-quality-safe-fix",
                        "--change-quality-strict-receipt",
                        execute=True,
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--no-change-quality-enabled"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--no-change-quality-enabled", execute=True
                    ),
                    "verify": [
                        inspect_command,
                        (
                            "loopx project-skill status --project . --skill "
                            "loopx-change-quality --surface codex"
                        ),
                        shlex.join(
                            [
                                "loopx",
                                "change-quality",
                                "prepare",
                                "--goal-id",
                                goal_id,
                                "--repo-path",
                                ".",
                            ]
                        ),
                    ],
                },
                "documentation": {
                    "path": "loopx/capabilities/change_quality/README.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "loopx/capabilities/change_quality/README.md"
                    ),
                },
            },
            {
                "feature_id": "reward_memory",
                "display_name": "Reward Memory experiment",
                "availability": "experimental_opt_in",
                "default": {"enabled": False},
                "current": {
                    "enabled": reward_memory.get("enabled") is True,
                    "experimental": reward_memory.get("experimental") is True,
                    "config_pointer_registered": reward_memory.get(
                        "config_pointer_registered"
                    )
                    is True,
                    "binding_revision": str(
                        reward_memory.get("binding_revision") or ""
                    ),
                    "enabled_agents": list(reward_memory.get("enabled_agents") or []),
                    "enablement_verified_agents": list(
                        reward_memory.get("enablement_verified_agents") or []
                    ),
                    "automatic_ingest": reward_memory.get("automatic_ingest"),
                    "automatic_recall": reward_memory.get("automatic_recall"),
                    "automation_intent": dict(
                        reward_memory.get("automation_intent") or {}
                    ),
                    "host_coverage": list(reward_memory.get("host_coverage") or []),
                },
                "required_inputs": {
                    "ignored-reward-memory-config": (
                        "Replace the placeholder with a repo-relative ignored JSON "
                        "config under .loopx/config/."
                    ),
                    "agent-id": (
                        "Replace the placeholder with one registered Agent lane. "
                        "A private config binds exactly one Goal-scoped Agent."
                    ),
                },
                "consider_when": (
                    "A named agent lane should trial reviewed, scoped operating "
                    "lessons through a configured context provider."
                ),
                "effect": (
                    "Allows only the named agent lanes to resolve the ignored "
                    "provider binding for explicit Reward Memory ingest and recall."
                ),
                "does_not": [
                    "make any provider a global LoopX feature or dependency",
                    "install, authenticate, or configure the selected provider",
                    "enable every agent or treat an unvalidated Turn summary as evidence",
                    "bypass scope, authority, freshness, conflict, or exact-readback guards",
                ],
                "commands": {
                    "preview_enable": _configure_command(
                        goal_id,
                        "--reward-memory-config",
                        "<ignored-reward-memory-config>",
                        "--reward-memory-agent",
                        "<agent-id>",
                    ),
                    "apply_enable": _configure_command(
                        goal_id,
                        "--reward-memory-config",
                        "<ignored-reward-memory-config>",
                        "--reward-memory-agent",
                        "<agent-id>",
                        execute=True,
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--clear-reward-memory-config"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--clear-reward-memory-config", execute=True
                    ),
                    "verify": [
                        inspect_command,
                        shlex.join(
                            [
                                "loopx",
                                "reward-memory",
                                "experiment-status",
                                "--goal-id",
                                goal_id,
                                "--agent-id",
                                "<agent-id>",
                            ]
                        ),
                    ],
                },
                "documentation": {
                    "path": "loopx/capabilities/reward_memory/README.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "loopx/capabilities/reward_memory/README.md"
                    ),
                },
            },
            {
                "feature_id": "lark_event_inbox",
                "display_name": "Lark event inbox",
                "availability": "supported_opt_in",
                "default": {"enabled": False},
                "current": {
                    "enabled": lark_event_inbox.get("enabled") is True,
                    "config_pointer_registered": lark_event_inbox.get(
                        "config_pointer_registered"
                    )
                    is True,
                },
                "required_inputs": {
                    "ignored-inbox-config": (
                        "Replace the placeholder with a repo-relative ignored JSON "
                        "config under .loopx/config/."
                    )
                },
                "consider_when": (
                    "A goal should consume durable Lark feedback without keeping an "
                    "agent process or hand-editing its heartbeat prompt."
                ),
                "effect": (
                    "Projects a goal-configured generic inbox into quota and generated "
                    "heartbeat drain behavior."
                ),
                "does_not": [
                    "install lark-cli, authenticate a user, or configure bot credentials",
                    "send Lark messages or turn inbound feedback into an automatic write",
                ],
                "commands": {
                    "preview_enable": _configure_command(
                        goal_id,
                        "--lark-event-inbox-config",
                        "<ignored-inbox-config>",
                    ),
                    "apply_enable": _configure_command(
                        goal_id,
                        "--lark-event-inbox-config",
                        "<ignored-inbox-config>",
                        execute=True,
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--clear-lark-event-inbox-config"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--clear-lark-event-inbox-config", execute=True
                    ),
                    "verify": [
                        inspect_command,
                        shlex.join(
                            [
                                "loopx",
                                "lark-inbox",
                                "drain",
                                "--goal-id",
                                goal_id,
                                "--project",
                                ".",
                            ]
                        ),
                    ],
                },
                "documentation": {
                    "path": "loopx/extensions/lark/docs/lark-event-inbox.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "loopx/extensions/lark/docs/lark-event-inbox.md"
                    ),
                },
            },
            {
                "feature_id": "lark_kanban_heartbeat_sync",
                "display_name": "Generic Lark Kanban heartbeat sync",
                "availability": "supported_opt_in",
                "default": {"enabled": False},
                "current": {
                    "enabled": lark_kanban_heartbeat_sync.get("enabled") is True,
                },
                "consider_when": (
                    "A goal already has a reviewed generic Kanban binding and should "
                    "best-effort refresh it after material heartbeat progress."
                ),
                "effect": (
                    "Projects one nonblocking generic Kanban action through quota/status "
                    "after material state changes."
                ),
                "does_not": [
                    "create or bind a board",
                    "authenticate Lark or make an existing local binding active",
                    "make sink success a delivery gate or let it preempt runnable P0 work",
                ],
                "commands": {
                    "preview_enable": _configure_command(
                        goal_id, "--lark-kanban-heartbeat-sync"
                    ),
                    "apply_enable": _configure_command(
                        goal_id, "--lark-kanban-heartbeat-sync", execute=True
                    ),
                    "preview_disable": _configure_command(
                        goal_id, "--no-lark-kanban-heartbeat-sync"
                    ),
                    "apply_disable": _configure_command(
                        goal_id, "--no-lark-kanban-heartbeat-sync", execute=True
                    ),
                    "verify": [
                        inspect_command,
                        shlex.join(
                            [
                                "loopx",
                                "--format",
                                "json",
                                "quota",
                                "should-run",
                                "--goal-id",
                                goal_id,
                            ]
                        ),
                    ],
                },
                "documentation": {
                    "path": "docs/integrations/lark-kanban-control-plane-adapter.md",
                    "url": (
                        "https://github.com/loopx-project/loopx/blob/main/"
                        "docs/integrations/lark-kanban-control-plane-adapter.md"
                    ),
                },
            },
        ],
    }
    periodic_report = settings.get("periodic_report")
    catalog["features"].append(
        {
            "feature_id": "periodic_report",
            "display_name": "Periodic reports",
            "availability": "supported_explicit_override",
            "default": {"enabled": False, "timezone": "UTC"},
            **(
                {"current": dict(periodic_report)}
                if isinstance(periodic_report, Mapping)
                else {}
            ),
            "consider_when": (
                "This Goal needs a fixed report route that must not follow the live "
                "machine default."
            ),
            "effect": (
                "Stores one complete Goal-specific periodic-report override; no fields "
                "are inherited while the override is present."
            ),
            "does_not": [
                "merge individual Goal fields with machine defaults",
                "replace credentials or provider bindings with public configuration",
            ],
            "commands": {"verify": [inspect_command]},
            "documentation": {},
        }
    )
    overrides = machine_inheritable_goal_overrides or {}
    for feature in catalog["features"]:
        feature_id = str(feature.get("feature_id") or "")
        if feature_id not in {
            "todo_replan_cadence",
            "change_quality_qualification",
            "pull_request_review",
        }:
            continue
        explicit = overrides.get(feature_id)
        if isinstance(explicit, Mapping):
            feature["current"] = dict(explicit)
        else:
            feature.pop("current", None)
    catalog["capability_catalog"] = build_capability_configuration_catalog(
        goal_features=catalog["features"],
        explore_harness_profiles=explore_harness_profiles,
    )
    return catalog
