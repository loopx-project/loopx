from __future__ import annotations

import json
import shlex
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .bootstrap_contract import (
    auto_research_contract_command_text,
    auto_research_start_command_text,
    build_auto_research_contract_acceptance,
    build_auto_research_live_worker_proof,
)
from .demo_supervisor import build_auto_research_demo_supervisor_plan
from .defaults import AUTO_RESEARCH_DEFAULT_GOAL_ID
from .live_evidence import load_live_codex_e2e_evidence
from .preset import (
    auto_research_seed_action_for_role,
    auto_research_seed_title,
    default_auto_research_agent_specs,
)
from .rollout_append import append_auto_research_rollout_events
from .user_contract import build_auto_research_user_contract
from .worker_loop import run_auto_research_worker_loop
from .worker_runtime import load_auto_research_worker_frontier
from ..multi_agent.collective_round_ledger import (
    build_multi_agent_collective_round_ledger,
)
from loopx.control_plane.todos.contract import normalize_required_write_scopes


AppendEvidence = Callable[[str], dict[str, object]]
VisibleLauncher = Callable[..., dict[str, object]]
VisibleWake = Callable[[str, Sequence[str]], dict[str, object]]

AUTO_RESEARCH_DEMO_E2E_SCHEMA_VERSION = "auto_research_demo_e2e_result_v0"
AUTO_RESEARCH_SEED_ACTION_CHAIN = (
    "write_research_contract",
    "propose_hypothesis",
    "run_dev_eval",
    "summarize_evidence",
)
AUTO_RESEARCH_SEED_ACTION_ORDER = {
    action: index for index, action in enumerate(AUTO_RESEARCH_SEED_ACTION_CHAIN)
}
AUTO_RESEARCH_SEED_PREREQUISITE_ACTION_BY_ACTION = dict(
    zip(AUTO_RESEARCH_SEED_ACTION_CHAIN[1:], AUTO_RESEARCH_SEED_ACTION_CHAIN)
)
AUTO_RESEARCH_DEMO_CONTROL_WRITE_SCOPE = ("examples/**", "experiments/**", ".local/**")
AUTO_RESEARCH_DEMO_AVAILABLE_CAPABILITIES = ("benchmark_runner",)


def _prepare_visible_demo_workspace_route(
    *,
    control_project: Path,
    lanes: list[dict[str, object]],
) -> dict[str, object]:
    """Keep visible Codex TUIs off demo-local git worktrees.

    The demo still shares one LoopX state surface. Mutating attempts can claim
    their own execution boundary from inside the role, but the first visible TUI
    screen must not start in a freshly-created git worktree because Codex will
    stop on a trust prompt before the user sees the auto-research flow.
    """

    (control_project / ".gitignore").write_text(".local/\n", encoding="utf-8")

    peer_count = 0
    for lane in lanes:
        agent_id = str(lane.get("agent_id") or "").strip()
        if not agent_id:
            continue
        lane["workspace_role"] = "shared_visible_tui_workspace"
        peer_count += 1

    return {
        "schema_version": "auto_research_visible_demo_workspace_route_v0",
        "shared_goal_surface": "demo_local_loopx_registry_and_runtime",
        "peer_workspace": "visible_codex_tui_workspace",
        "default_visible_workspace": "demo_owned_clean_workspace",
        "peer_lane_workspace": "visible_codex_tui_workspace",
        "peer_lane_worktree_count": 0,
        "peer_lane_count": peer_count,
        "trust_prompt_avoidance": (
            "demo_owned_clean_workspace_with_persisted_codex_trust_config"
        ),
        "mutation_isolation_policy": (
            "mutating attempts claim an execution boundary from inside the role; "
            "the first visible TUI uses the demo-owned clean workspace unless "
            "the operator passes --workspace"
        ),
        "absolute_paths_recorded": False,
    }


def _visible_demo_goal_write_scope(
    preset_context: dict[str, object] | None,
) -> list[str]:
    scopes = list(AUTO_RESEARCH_DEMO_CONTROL_WRITE_SCOPE)
    if isinstance(preset_context, dict):
        scopes.extend(
            normalize_required_write_scopes(preset_context.get("editable_scope"))
        )
    return list(dict.fromkeys(scopes))


def _seed_visible_demo_control_plane(
    *,
    demo_root: Path,
    goal_id: str,
    objective: str,
    supervisor: dict[str, object],
    preset_context: dict[str, object] | None = None,
) -> tuple[dict[str, object], Path, str]:
    """Create a tiny demo-local LoopX queue for visible workers."""

    from loopx.bootstrap import bootstrap_project
    from loopx.configure_goal import configure_goal
    from loopx.todos import add_goal_todo

    control_project = demo_root / "visible-control-plane"
    control_registry = demo_root / "visible-control-plane.registry.json"
    control_runtime = demo_root / "visible-control-plane.runtime"
    bootstrap_project(
        project=control_project,
        registry_path=control_registry,
        runtime_root=control_runtime,
        goal_id=goal_id,
        objective=objective,
        domain="auto-research-demo",
        role="agent",
        parent_goal_id=None,
        state_file=None,
        goal_doc=None,
        adapter_kind="auto_research_demo_local_queue",
        adapter_status="connected",
        next_probe=None,
        spawn_allowed=True,
        max_children=4,
        allowed_domains=["auto-research-demo"],
        write_scope=_visible_demo_goal_write_scope(preset_context),
        force=False,
        dry_run=False,
        sync_global=False,
    )
    lanes = [lane for lane in supervisor.get("lanes") or [] if isinstance(lane, dict)]
    agents = sorted(
        {
            str(lane.get("agent_id") or "").strip()
            for lane in lanes
            if str(lane.get("agent_id") or "").strip()
        }
    )
    configure_goal(
        registry_path=control_registry,
        goal_id=goal_id,
        registered_agents=agents,
        agent_model="peer_v1",
        waiting_on="codex",
        orchestration_mode="multi_subagent",
        spawn_allowed=True,
        execute=True,
    )
    registry_payload = json.loads(control_registry.read_text(encoding="utf-8"))
    for goal in registry_payload.get("goals", []):
        if isinstance(goal, dict) and str(goal.get("id")) == goal_id:
            coordination = goal.setdefault("coordination", {})
            if isinstance(coordination, dict):
                capabilities = [
                    str(value).strip()
                    for value in coordination.get("available_capabilities", [])
                    if str(value).strip()
                ]
                for capability in AUTO_RESEARCH_DEMO_AVAILABLE_CAPABILITIES:
                    if capability not in capabilities:
                        capabilities.append(capability)
                coordination["available_capabilities"] = capabilities
            goal["workspace_guard_policy"] = {
                "schema_version": "loopx_workspace_guard_policy_v0",
                "peer_independent_worktree_required": False,
                "reason": (
                    "auto_research_demo_local_queue uses a demo-owned workspace and "
                    "writes only demo-local LoopX state/evidence; repository edits still "
                    "require an explicit lane-owned execution boundary"
                ),
            }
            break
    control_registry.write_text(
        json.dumps(registry_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    seeded_todos: list[dict[str, object]] = []
    seeded_todo_ids_by_action: dict[str, str] = {}
    seed_rows: list[tuple[str, str, str, str]] = []
    for lane in lanes:
        agent_id = str(lane.get("agent_id") or "").strip()
        role_id = str(lane.get("role_id") or "").strip()
        lane_id = str(lane.get("lane_id") or "").strip()
        action_kind = auto_research_seed_action_for_role(role_id)
        seed_rows.append((action_kind, agent_id, role_id, lane_id))
    seed_rows.sort(
        key=lambda row: (
            AUTO_RESEARCH_SEED_ACTION_ORDER.get(
                row[0], len(AUTO_RESEARCH_SEED_ACTION_ORDER)
            ),
            row[1],
        )
    )
    for action_kind, agent_id, role_id, lane_id in seed_rows:
        title = auto_research_seed_title(
            action_kind=action_kind,
            role_id=role_id,
            lane_id=lane_id,
        )
        prerequisite_action = AUTO_RESEARCH_SEED_PREREQUISITE_ACTION_BY_ACTION.get(
            action_kind
        )
        prerequisite_todo_id = (
            seeded_todo_ids_by_action.get(prerequisite_action)
            if prerequisite_action
            else None
        )
        resume_when = (
            f"todo_done:{prerequisite_todo_id}"
            if prerequisite_todo_id
            else None
        )
        result = add_goal_todo(
            registry_path=control_registry,
            goal_id=goal_id,
            role="agent",
            text=f"[P0-auto-research-live] {title}",
            task_class="advancement_task",
            action_kind=action_kind,
            claimed_by=agent_id or None,
            resume_when=resume_when,
            project=control_project,
        )
        todo_id = result.get("todo_id")
        if isinstance(todo_id, str) and todo_id:
            seeded_todo_ids_by_action.setdefault(action_kind, todo_id)
        seeded_todos.append(
            {
                "todo_id": todo_id,
                "agent_id": agent_id,
                "lane_id": lane_id,
                "role_id": role_id,
                "action_kind": action_kind,
                "resume_when": result.get("resume_when"),
            }
        )

    workspace_route = _prepare_visible_demo_workspace_route(
        control_project=control_project,
        lanes=lanes,
    )

    summary = {
        "schema_version": "auto_research_visible_demo_control_plane_v0",
        "mode": "demo_local_loopx_queue",
        "goal_id": goal_id,
        "registry_scope": "demo_local_runtime",
        "registered_agent_count": len(agents),
        "seeded_todo_count": len(seeded_todos),
        "seeded_todos": seeded_todos,
        "state_route": "visible_lanes_use_LOOPX_REGISTRY_and_LOOPX_RUNTIME_ROOT",
        "workspace_route": workspace_route,
        "workspace_guard_policy": {
            "peer_independent_worktree_required": False,
            "repository_edits_still_require_lane_boundary": True,
        },
        "absolute_paths_recorded": False,
        "private_artifacts_recorded": False,
    }
    return summary, control_registry, str(control_runtime)


def _command_text(
    *,
    cli_bin: str,
    goal_id: str,
    agent_id: str,
    execute: bool,
    run_worker_loop: bool = False,
    launch_visible: bool = False,
    headless: bool = False,
    attach: bool = False,
    no_attach: bool = False,
    live_evidence: bool = False,
    wake_visible_after_launch: bool = False,
    configure_visible_worker_turn: bool = False,
    tracking_goal_id: str | None = None,
    output_language: str = "en",
) -> str:
    parts = [
        shlex.quote(cli_bin),
        "--format",
        "json",
        "auto-research",
        "demo-e2e",
        "--goal-id",
        shlex.quote(goal_id),
        "--agent-id",
        shlex.quote(agent_id),
    ]
    if tracking_goal_id:
        parts.extend(["--tracking-goal-id", shlex.quote(tracking_goal_id)])
    if output_language and output_language != "en":
        parts.extend(["--language", shlex.quote(output_language)])
    if execute:
        parts.append("--execute")
    if run_worker_loop:
        parts.append("--run-worker-loop")
    if headless:
        parts.append("--headless")
    if launch_visible:
        parts.append("--launch-visible")
    if attach:
        parts.append("--attach")
    if no_attach:
        parts.append("--no-attach")
    if live_evidence:
        parts.extend(["--live-evidence", "<public-safe-live-evidence.json>"])
    if wake_visible_after_launch:
        parts.append("--wake-visible-after-launch")
    if configure_visible_worker_turn:
        parts.append("--configure-visible-worker-turn")
    return " ".join(parts)


def _build_demo_frontier_route_contract(
    *,
    goal_id: str,
    tracking_goal: str,
    preset_context: dict[str, object] | None,
    goal_surface_mode: str,
    reuses_default_internal_goal: bool,
) -> dict[str, object]:
    return {
        "schema_version": "auto_research_demo_frontier_route_v0",
        "goal_surface_mode": goal_surface_mode,
        "frontier_goal_id": goal_id,
        "tracking_goal_id": tracking_goal or None,
        "preset_id": preset_context.get("preset_id") if preset_context else None,
        "preset_baseline_source": (
            preset_context.get("baseline_source") if preset_context else None
        ),
        "tracking_goal_drives_frontier": False,
        "visible_lanes_read_goal_id": goal_id,
        "fresh_goal_default": goal_surface_mode == "fresh_demo_goal",
        "inherits_default_goal": goal_surface_mode == "inherited_default_goal",
        "reuses_default_internal_goal": reuses_default_internal_goal,
        "default_internal_goal_id": AUTO_RESEARCH_DEFAULT_GOAL_ID,
        "dedicated_positive_demo_frontier": not reuses_default_internal_goal,
        "reason": (
            "Omitting --goal-id creates an isolated demo goal surface. "
            "Use --goal-id to target a specific research frontier, or --inherit-default-goal "
            "to intentionally reuse the internal shared demo goal. --tracking-goal-id is metadata "
            "for the parent productization goal and must not reroute panes."
        ),
    }


def _build_demo_e2e_commands(
    *,
    cli_bin: str,
    objective: str,
    preset_id: str | None,
    output_language: str,
    goal_id: str,
    agent_id: str,
    tracking_goal: str,
    session_name: str,
) -> dict[str, str]:
    tracking_goal_id = tracking_goal or None
    return {
        "one_question_contract": auto_research_contract_command_text(
            cli_bin=cli_bin,
            objective=objective,
        ),
        "one_question_start": auto_research_start_command_text(
            cli_bin=cli_bin,
            objective=objective,
            preset_id=preset_id,
            execute=True,
            output_language=output_language,
        ),
        "one_question_start_preview": auto_research_start_command_text(
            cli_bin=cli_bin,
            objective=objective,
            preset_id=preset_id,
            output_language=output_language,
        ),
        "one_question_start_with_visible_wake": auto_research_start_command_text(
            cli_bin=cli_bin,
            objective=objective,
            preset_id=preset_id,
            execute=True,
            output_language=output_language,
        ),
        "one_command_worker_loop": _command_text(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
            execute=True,
            run_worker_loop=True,
            tracking_goal_id=tracking_goal_id,
            output_language=output_language,
        ),
        "headless_worker_loop": _command_text(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
            execute=True,
            run_worker_loop=True,
            headless=True,
            tracking_goal_id=tracking_goal_id,
            output_language=output_language,
        ),
        "start_visible_lanes_without_attach": _command_text(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
            execute=True,
            launch_visible=True,
            no_attach=True,
            tracking_goal_id=tracking_goal_id,
            output_language=output_language,
        ),
        "one_command_visible_wake_demo": _command_text(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
            execute=True,
            launch_visible=True,
            no_attach=True,
            wake_visible_after_launch=True,
            tracking_goal_id=tracking_goal_id,
            output_language=output_language,
        ),
        "one_command_visible_worker_turn_validation": _command_text(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
            execute=True,
            launch_visible=True,
            no_attach=True,
            configure_visible_worker_turn=True,
            tracking_goal_id=tracking_goal_id,
            output_language=output_language,
        ),
        "one_command_worker_loop_with_visible_lanes": _command_text(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
            execute=True,
            run_worker_loop=True,
            launch_visible=True,
            attach=True,
            tracking_goal_id=tracking_goal_id,
            output_language=output_language,
        ),
        "load_live_worker_evidence": _command_text(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
            execute=True,
            live_evidence=True,
            tracking_goal_id=tracking_goal_id,
            output_language=output_language,
        ),
        "wake_visible_lanes": (
            f"{shlex.quote(cli_bin)} --format json multi-agent wake --session-name "
            f"{shlex.quote(session_name)} --execute"
        ),
    }


def _supervisor_summary(supervisor: dict[str, object]) -> dict[str, object]:
    product_spec = (
        supervisor.get("product_spec")
        if isinstance(supervisor.get("product_spec"), dict)
        else {}
    )
    runner_contract = (
        supervisor.get("runner_contract")
        if isinstance(supervisor.get("runner_contract"), dict)
        else {}
    )
    pane_local_a2a = (
        runner_contract.get("pane_local_a2a")
        if isinstance(runner_contract.get("pane_local_a2a"), dict)
        else {}
    )
    decentralized_a2a_driver = (
        runner_contract.get("decentralized_a2a_driver")
        if isinstance(runner_contract.get("decentralized_a2a_driver"), dict)
        else {}
    )
    cli_contract = (
        supervisor.get("cli_contract")
        if isinstance(supervisor.get("cli_contract"), dict)
        else {}
    )
    auto_research = (
        supervisor.get("auto_research")
        if isinstance(supervisor.get("auto_research"), dict)
        else {}
    )
    coordination_model = (
        supervisor.get("coordination_model")
        if isinstance(supervisor.get("coordination_model"), dict)
        else {}
    )
    return {
        "schema_version": supervisor.get("schema_version"),
        "mode": supervisor.get("mode"),
        "lane_count": len(supervisor.get("lanes") or []),
        "reasoning_contract": supervisor.get("reasoning_contract"),
        "uses_generic_runner": bool(
            product_spec.get("uses_generic_runner")
            or auto_research.get("uses_generic_runner")
        ),
        "generic_spec_schema": product_spec.get("schema_version"),
        "runner_contract_schema": runner_contract.get("schema_version"),
        "pane_local_a2a": {
            "tick_command": pane_local_a2a.get("tick_command"),
            "machine_json_policy": pane_local_a2a.get("machine_json_policy"),
            "machine_json_destination": pane_local_a2a.get("machine_json_destination"),
            "status_artifact": pane_local_a2a.get("status_artifact"),
            "human_default": pane_local_a2a.get("human_default"),
        },
        "decentralized_a2a_driver": {
            "schema_version": decentralized_a2a_driver.get("schema_version"),
            "owner_layer": decentralized_a2a_driver.get("owner_layer"),
            "driver_model": decentralized_a2a_driver.get("driver_model"),
            "coordination_pattern": decentralized_a2a_driver.get("coordination_pattern"),
            "broadcaster_decides_work": (
                decentralized_a2a_driver.get("broadcaster", {}).get("decides_work")
                if isinstance(decentralized_a2a_driver.get("broadcaster"), dict)
                else None
            ),
            "pane_decision_owner": (
                decentralized_a2a_driver.get("pane", {}).get("decision_owner")
                if isinstance(decentralized_a2a_driver.get("pane"), dict)
                else None
            ),
            "user_and_preset_do_not_own_tick_driver": (
                decentralized_a2a_driver.get("acceptance", {}).get(
                    "user_and_preset_do_not_own_tick_driver"
                )
                if isinstance(decentralized_a2a_driver.get("acceptance"), dict)
                else None
            ),
        },
        "machine_json_policy": cli_contract.get("machine_json_policy"),
        "domain_specific_runner_logic": bool(product_spec.get("domain_specific")),
        "kernel_boundary": {
            "state_bus": auto_research.get("state_bus"),
            "presentation_layers_in_kernel": bool(
                auto_research.get("presentation_layers_in_kernel")
            ),
            "coordination_pattern": coordination_model.get("pattern"),
        },
    }


def _compact_live_worker_evidence(evidence: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "auto_research_live_worker_evidence_summary_v0",
        "loaded": True,
        "source": evidence.get("source"),
        "goal_id": evidence.get("goal_id"),
        "agent_id": evidence.get("agent_id"),
        "lane_count": evidence.get("lane_count"),
        "evidence_event_count": evidence.get("evidence_event_count"),
        "result_status": evidence.get("result_status"),
        "protected_scope_clean": bool(evidence.get("protected_scope_clean")),
        "dev_metric": evidence.get("dev_metric"),
        "holdout_metric": evidence.get("holdout_metric"),
        "public_boundary": evidence.get("public_boundary"),
    }


def _compact_visible_pane_a2a_status(artifacts: list[dict[str, object]]) -> dict[str, object]:
    lane_outcomes: list[dict[str, object]] = []
    status_check_count = 0
    for raw in artifacts:
        checks = raw.get("checks") if isinstance(raw.get("checks"), list) else []
        status_check_count += len(checks)
        last = checks[-1] if checks and isinstance(checks[-1], dict) else {}
        lane_outcomes.append(
            {
                "agent_id": raw.get("agent_id"),
                "role_id": raw.get("role_id"),
                "status": raw.get("status"),
                "worker_configured": raw.get("worker_configured") is True,
                "worker_label": raw.get("worker_label"),
                "selected_todo_id": last.get("selected_todo_id"),
                "selected_action": last.get("selected_action"),
                "worker_status": last.get("worker_status"),
                "counts_as_research_round": False,
            }
        )
    return {
        "schema_version": "auto_research_visible_pane_a2a_status_summary_v0",
        "loaded": bool(artifacts),
        "source": "visible_pane_a2a_status_artifacts",
        "lane_count": len(artifacts),
        "status_check_count": status_check_count,
        "workflow_driver": False,
        "counts_as_collective_research_round": False,
        "lane_outcomes": lane_outcomes,
        "public_boundary": {
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
            "credentials_recorded": False,
            "local_workspace_path_redacted": True,
        },
    }


def _build_collective_round_summary(
    *,
    source: str,
    agent_count: int | None,
    collective_round_count: int,
    dev_metric: float | None,
    holdout_metric: float | None,
    dev_metric_sequence: list[float] | None = None,
    holdout_metric_sequence: list[float] | None = None,
    holdout_improvement_count: int | None = None,
    evidence_event_count: int | None = None,
    expected_lanes: list[dict[str, object]] | None = None,
    lane_outcomes: list[dict[str, object]] | None = None,
    role_declared_successor_todos: list[dict[str, object]] | None = None,
    visible_role_participation_verified: bool = False,
    visible_role_participation_basis: str | None = None,
) -> dict[str, object]:
    baseline = 1.0
    integrated_evidence: dict[str, object] = {
        "dev_metric": dev_metric,
        "holdout_metric": holdout_metric,
        "evidence_event_count": evidence_event_count,
    }
    if dev_metric_sequence is not None:
        integrated_evidence["dev_metric_sequence"] = list(dev_metric_sequence)
    if holdout_metric_sequence is not None:
        integrated_evidence["holdout_metric_sequence"] = list(holdout_metric_sequence)
    if holdout_improvement_count is not None:
        integrated_evidence["holdout_improvement_count"] = holdout_improvement_count
    kernel_ledger = build_multi_agent_collective_round_ledger(
        source=source,
        expected_lanes=expected_lanes,
        lane_outcomes=lane_outcomes,
        integrated_evidence=integrated_evidence,
        role_declared_successor_todos=role_declared_successor_todos,
        baseline_metric=baseline,
        required_full_participation_round_count=4,
        required_holdout_improvement_count=2,
    )
    kernel_evidence = (
        kernel_ledger.get("integrated_evidence")
        if isinstance(kernel_ledger.get("integrated_evidence"), dict)
        else {}
    )
    verification = (
        kernel_ledger.get("collective_research_verification")
        if isinstance(kernel_ledger.get("collective_research_verification"), dict)
        else {}
    )
    dev_metric = _numeric_metric(kernel_evidence.get("dev_metric"))
    holdout_metric = _numeric_metric(kernel_evidence.get("holdout_metric"))
    dev_sequence = _numeric_sequence(kernel_evidence.get("dev_metric_sequence"))
    holdout_sequence = _numeric_sequence(kernel_evidence.get("holdout_metric_sequence"))
    holdout_improvements = (
        int(kernel_evidence.get("holdout_improvement_count"))
        if isinstance(kernel_evidence.get("holdout_improvement_count"), int)
        and not isinstance(kernel_evidence.get("holdout_improvement_count"), bool)
        else 0
    )
    full_participation_round_count = kernel_ledger.get("full_participation_round_count")
    if not isinstance(full_participation_round_count, int) or isinstance(
        full_participation_round_count, bool
    ):
        full_participation_round_count = 0
    full_participation_verified = kernel_ledger.get("full_participation_verified") is True
    full_participation_gap = (
        kernel_ledger.get("full_participation_requirement_gap")
        if isinstance(kernel_ledger.get("full_participation_requirement_gap"), dict)
        else {}
    )
    full_participation_count_basis = str(
        kernel_ledger.get("full_participation_count_basis") or ""
    )
    multi_round_research_verified = (
        verification.get("verified") is True
        and verification.get("dev_metric_over_baseline") is True
        and holdout_metric is not None
        and visible_role_participation_verified
    )
    return {
        "schema_version": "auto_research_collective_round_summary_v0",
        "loaded": collective_round_count > 0 or dev_metric is not None or holdout_metric is not None,
        "source": source,
        "claim_source": source,
        "round_unit": "collective_agent_pass",
        "kernel_ledger": kernel_ledger,
        "definition": (
            "one collective research round means each configured research lane has "
            "one quota/frontier/worker-turn opportunity; pane-local tick loops are "
            "reported separately and do not by themselves prove multi-round research"
        ),
        "visible_role_participation_required": True,
        "visible_role_participation_verified": bool(visible_role_participation_verified),
        "visible_role_participation_basis": visible_role_participation_basis
        or (
            "visible_pane_artifacts_plus_live_evidence"
            if visible_role_participation_verified
            else "not_visible_pane_verified"
        ),
        "claim_boundary": (
            "worker-loop summaries are control-plane plumbing evidence only; "
            "visible multi-role research claims require visible pane artifacts "
            "plus lane-authored public evidence"
        ),
        "agent_count": agent_count,
        "required_collective_round_count": 4,
        "required_holdout_improvement_count": 2,
        "collective_round_count": collective_round_count,
        "full_participation_round_count": full_participation_round_count,
        "full_participation_count_basis": full_participation_count_basis,
        "full_participation_requirement_gap": full_participation_gap,
        "full_participation_verified": full_participation_verified,
        "evidence_stage_count": len(dev_sequence) + len(holdout_sequence),
        "multi_round_research_verified": multi_round_research_verified,
        "improvement_over_rounds": multi_round_research_verified,
        "holdout_improvement_verified": holdout_improvements >= 2,
        "baseline_metric": baseline,
        "dev_metric": dev_metric,
        "holdout_metric": holdout_metric,
        "dev_metric_sequence": dev_sequence,
        "holdout_metric_sequence": holdout_sequence,
        "holdout_improvement_count": holdout_improvements,
        "holdout_delta_over_dev": (
            holdout_metric - dev_metric
            if holdout_metric is not None and dev_metric is not None
            else None
        ),
        "evidence_event_count": evidence_event_count,
        "public_boundary": {
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
            "credentials_recorded": False,
            "local_workspace_path_redacted": True,
        },
    }


def _collective_summary_from_worker_loop(
    worker_loop: dict[str, object],
    *,
    agent_count: int,
) -> dict[str, object]:
    turns = worker_loop.get("turns") if isinstance(worker_loop.get("turns"), list) else []
    round_indexes = {
        turn.get("round")
        for turn in turns
        if isinstance(turn, dict)
        and turn.get("executed") is True
        and isinstance(turn.get("round"), int)
        and not isinstance(turn.get("round"), bool)
    }
    dev_metrics = [
        metric
        for turn in turns
        if isinstance(turn, dict)
        for metric in [_numeric_metric(turn.get("dev_metric"))]
        if metric is not None
    ]
    holdout_metrics = [
        metric
        for turn in turns
        if isinstance(turn, dict)
        for metric in [_numeric_metric(turn.get("holdout_metric"))]
        if metric is not None
    ]
    previous_holdout = 1.0
    holdout_improvement_count = 0
    for metric in holdout_metrics:
        if metric > previous_holdout:
            holdout_improvement_count += 1
        previous_holdout = metric
    successors = [
        successor
        for turn in turns
        if isinstance(turn, dict) and isinstance(turn.get("successor_todos"), list)
        for successor in turn.get("successor_todos", [])
        if isinstance(successor, dict)
    ]
    return _build_collective_round_summary(
        source="worker_loop_collective_agent_passes",
        agent_count=agent_count,
        collective_round_count=len(round_indexes),
        dev_metric=dev_metrics[-1] if dev_metrics else None,
        holdout_metric=holdout_metrics[-1] if holdout_metrics else None,
        dev_metric_sequence=dev_metrics,
        holdout_metric_sequence=holdout_metrics,
        holdout_improvement_count=holdout_improvement_count,
        expected_lanes=[
            {"agent_id": turn.get("agent_id"), "role_id": turn.get("role_id")}
            for turn in turns
            if isinstance(turn, dict) and turn.get("round") == 1
        ],
        lane_outcomes=[turn for turn in turns if isinstance(turn, dict)],
        role_declared_successor_todos=successors,
        visible_role_participation_verified=False,
        visible_role_participation_basis="headless_worker_loop_summary_only",
    )


def _collective_summary_from_visible_evidence(
    *,
    pane_status: dict[str, object],
    evidence: dict[str, object],
    agent_count: int | None,
    expected_lanes: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    lane_outcomes = (
        pane_status.get("lane_outcomes")
        if isinstance(pane_status.get("lane_outcomes"), list)
        else []
    )
    return _build_collective_round_summary(
        source="visible_lane_authored_evidence",
        agent_count=agent_count,
        collective_round_count=0,
        dev_metric=_numeric_metric(evidence.get("dev_metric")),
        holdout_metric=_numeric_metric(evidence.get("holdout_metric")),
        evidence_event_count=(
            int(evidence.get("evidence_event_count"))
            if isinstance(evidence.get("evidence_event_count"), int)
            and not isinstance(evidence.get("evidence_event_count"), bool)
            else None
        ),
        expected_lanes=expected_lanes
        or (
            pane_status.get("lanes") if isinstance(pane_status.get("lanes"), list) else None
        ),
        lane_outcomes=lane_outcomes,
        visible_role_participation_verified=True,
        visible_role_participation_basis=(
            "visible_status_artifacts_plus_lane_authored_evidence"
        ),
    )


def _discover_visible_pane_a2a_status(
    *,
    runtime_root_arg: str | None,
    session_name: str,
    goal_id: str,
    wait_seconds: float,
) -> dict[str, object] | None:
    if not runtime_root_arg:
        return None
    runtime_root = Path(runtime_root_arg)
    artifact_root = runtime_root / "visible-launcher-artifacts" / session_name

    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        artifacts = []
        if artifact_root.is_dir():
            for candidate in sorted(artifact_root.glob("*/pane-a2a-status.public.json")):
                try:
                    raw = json.loads(candidate.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(raw, dict):
                    continue
                if raw.get("schema_version") != "pane_local_a2a_status_check_v0":
                    continue
                if raw.get("source") != "pane_local_a2a_status_check":
                    continue
                if raw.get("goal_id") != goal_id:
                    continue
                boundary = raw.get("public_boundary") if isinstance(raw.get("public_boundary"), dict) else {}
                if any(
                    boundary.get(key) is not False
                    for key in (
                        "raw_logs_recorded",
                        "private_artifacts_recorded",
                        "absolute_paths_recorded",
                        "credentials_recorded",
                    )
                ):
                    continue
                artifacts.append(raw)
        if artifacts:
            return _compact_visible_pane_a2a_status(artifacts)
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def _discover_visible_live_evidence(
    *,
    runtime_root_arg: str | None,
    session_name: str,
    goal_id: str,
    wait_seconds: float,
) -> dict[str, object] | None:
    if not runtime_root_arg:
        return None
    runtime_root = Path(runtime_root_arg)
    artifact_root = runtime_root / "visible-launcher-artifacts" / session_name

    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        candidates = []
        if artifact_root.is_dir():
            for artifact_name in (
                "live-codex-e2e-evidence.public.json",
                "live_codex_e2e_evidence.public.json",
            ):
                candidates.extend(artifact_root.glob(f"*/{artifact_name}"))
            candidates = sorted(set(candidates))
        loaded: list[dict[str, object]] = []
        for candidate in candidates:
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(raw, dict) or raw.get("goal_id") != goal_id:
                continue
            lane_agent_id = str(raw.get("agent_id") or "").strip()
            if not lane_agent_id:
                continue
            try:
                loaded.append(
                    load_live_codex_e2e_evidence(
                        evidence_path=str(candidate),
                        goal_id=goal_id,
                        agent_id=lane_agent_id,
                    )
                )
            except ValueError:
                continue
        for evidence in loaded:
            if evidence.get("holdout_metric") is not None:
                merged = dict(evidence)
                if merged.get("dev_metric") is None:
                    for other in loaded:
                        if other.get("dev_metric") is not None:
                            merged["dev_metric"] = other.get("dev_metric")
                            break
                return merged
        if loaded and time.monotonic() >= deadline:
            return loaded[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def _load_live_worker_evidence_into_payload(
    *,
    payload: dict[str, object],
    evidence: dict[str, object],
    evidence_source: str,
) -> None:
    payload["live_worker_evidence"] = _compact_live_worker_evidence(evidence)
    visible_proof = payload["visible_worker_proof"]
    if isinstance(visible_proof, dict):
        visible_proof["lane_authored_evidence_loaded"] = True
        visible_proof["visible_lanes_launched"] = True
        visible_proof["visible_lanes_accepted"] = True
        visible_proof["evidence_source"] = evidence_source


def _load_visible_pane_a2a_status_into_payload(
    *,
    payload: dict[str, object],
    status: dict[str, object],
) -> None:
    payload["visible_pane_a2a_status"] = status
    visible_proof = payload["visible_worker_proof"]
    if isinstance(visible_proof, dict):
        visible_proof["pane_local_a2a_status_loaded"] = bool(status.get("loaded"))
        visible_proof["pane_local_a2a_status_check_count"] = status.get(
            "status_check_count"
        )
        visible_proof["decentralized_a2a_rounds_verified"] = False


def _load_collective_research_rounds_into_payload(
    *,
    payload: dict[str, object],
    rounds: dict[str, object],
) -> None:
    payload["collective_research_rounds"] = rounds
    visible_proof = payload["visible_worker_proof"]
    if isinstance(visible_proof, dict):
        visible_proof["collective_research_rounds_loaded"] = bool(rounds.get("loaded"))
        visible_proof["collective_research_rounds_source"] = rounds.get("source")
        visible_proof["visible_role_participation_required"] = (
            rounds.get("visible_role_participation_required") is not False
        )
        visible_proof["visible_role_participation_verified"] = (
            rounds.get("visible_role_participation_verified") is True
        )
        visible_proof["visible_role_participation_basis"] = rounds.get(
            "visible_role_participation_basis"
        )
        visible_proof["collective_research_round_count"] = rounds.get(
            "collective_round_count"
        )
        visible_proof["holdout_improvement_count"] = rounds.get(
            "holdout_improvement_count"
        )
        visible_proof["decentralized_a2a_rounds_verified"] = bool(
            rounds.get("multi_round_research_verified")
            and rounds.get("visible_role_participation_verified") is True
        )


def _load_visible_wake_into_payload(
    *,
    payload: dict[str, object],
    wake: dict[str, object],
) -> None:
    driver = (
        wake.get("driver_contract")
        if isinstance(wake.get("driver_contract"), dict)
        else {}
    )
    prompt_submit_checks = wake.get("prompt_submit_checks") or []
    pane_input_ready_checks = wake.get("pane_input_ready_checks") or []
    not_ready_reasons = sorted(
        {
            str(check.get("not_ready_reason"))
            for check in pane_input_ready_checks
            if isinstance(check, dict) and check.get("not_ready_reason")
        }
    )
    prompt_delivered = bool(prompt_submit_checks)
    payload["visible_wake"] = {
        "schema_version": wake.get("schema_version"),
        "mode": wake.get("mode"),
        "session_name": wake.get("session_name"),
        "target_lanes": wake.get("target_lanes"),
        "prompt_hash": wake.get("prompt_hash"),
        "coordination_model": wake.get("coordination_model"),
        "wakeup_model": wake.get("wakeup_model"),
        "workflow_driver": bool(wake.get("workflow_driver")),
        "broadcaster_reads_frontier": bool(wake.get("broadcaster_reads_frontier")),
        "broadcaster_reads_todo_readiness": bool(
            wake.get("broadcaster_reads_todo_readiness")
        ),
        "broadcaster_selects_todo": bool(wake.get("broadcaster_selects_todo")),
        "pane_decision_owner": wake.get("pane_decision_owner"),
        "pane_input_ready_verified": wake.get("pane_input_ready_verified") is True,
        "pane_input_ready_checks": pane_input_ready_checks,
        "pane_input_ready_timeout_seconds": wake.get("pane_input_ready_timeout_seconds"),
        "ready_lanes": wake.get("ready_lanes") or [],
        "not_ready_lanes": wake.get("not_ready_lanes") or [],
        "not_ready_reasons": not_ready_reasons,
        "prompt_submit_checks": prompt_submit_checks,
        "prompt_delivered": prompt_delivered,
        "prompt_delivery": wake.get("prompt_delivery"),
        "auto_wake_backoff_recommended": wake.get("auto_wake_backoff_recommended") is True,
        "driver_contract_schema": driver.get("schema_version"),
        "driver_owner_layer": driver.get("owner_layer"),
        "boundary": wake.get("boundary"),
    }
    visible_proof = payload["visible_worker_proof"]
    if isinstance(visible_proof, dict):
        visible_proof["cadence_wake_loaded"] = True
        prompt_delivery = wake.get("prompt_delivery")
        cadence_wake_pending_reason = None
        if not prompt_delivered:
            if prompt_delivery == "skipped_no_input_ready_panes":
                cadence_wake_pending_reason = "pane_not_ready"
            elif prompt_delivery == "skipped_terminal_pane_backoff":
                cadence_wake_pending_reason = "terminal_backoff"
        visible_proof["cadence_wake_verified"] = (
            wake.get("mode") == "execute"
            and wake.get("coordination_model") == "decentralized_state_a2a"
            and wake.get("wakeup_model") == "fixed_prompt_broadcast"
            and wake.get("workflow_driver") is False
            and wake.get("broadcaster_reads_frontier") is False
            and wake.get("broadcaster_selects_todo") is False
            and prompt_delivery
            in {
                "tmux_paste_buffer_after_codex_tui_first_turn_ready",
                "tmux_paste_buffer_after_ready_subset",
            }
            and prompt_delivered
        )
        visible_proof["cadence_wake_prompt_delivered"] = prompt_delivered
        visible_proof["cadence_wake_pending_reason"] = cadence_wake_pending_reason
        visible_proof["cadence_wake_not_ready_reasons"] = not_ready_reasons


def _numeric_metric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _numeric_sequence(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    metrics: list[float] = []
    for item in value:
        metric = _numeric_metric(item)
        if metric is not None:
            metrics.append(metric)
    return metrics


def _metric_improvement_count(metrics: list[float], *, baseline: float) -> int:
    previous = baseline
    count = 0
    for metric in metrics:
        if metric > previous:
            count += 1
        previous = metric
    return count


def _build_visible_readiness(payload: dict[str, object]) -> dict[str, object]:
    proof = (
        payload.get("visible_worker_proof")
        if isinstance(payload.get("visible_worker_proof"), dict)
        else {}
    )
    pane_status = (
        payload.get("visible_pane_a2a_status")
        if isinstance(payload.get("visible_pane_a2a_status"), dict)
        else {}
    )
    collective_rounds = (
        payload.get("collective_research_rounds")
        if isinstance(payload.get("collective_research_rounds"), dict)
        else {}
    )
    evidence = (
        payload.get("live_worker_evidence")
        if isinstance(payload.get("live_worker_evidence"), dict)
        else {}
    )
    wake = payload.get("visible_wake") if isinstance(payload.get("visible_wake"), dict) else {}
    supervisor = payload.get("supervisor") if isinstance(payload.get("supervisor"), dict) else {}
    contract_acceptance = (
        payload.get("contract_acceptance")
        if isinstance(payload.get("contract_acceptance"), dict)
        else {}
    )
    driver = (
        supervisor.get("decentralized_a2a_driver")
        if isinstance(supervisor.get("decentralized_a2a_driver"), dict)
        else {}
    )
    baseline = 1.0
    dev_sequence = _numeric_sequence(evidence.get("dev_metric_sequence")) or _numeric_sequence(
        collective_rounds.get("dev_metric_sequence")
    )
    holdout_sequence = _numeric_sequence(evidence.get("holdout_metric_sequence")) or _numeric_sequence(
        collective_rounds.get("holdout_metric_sequence")
    )
    dev_metric = dev_sequence[-1] if dev_sequence else _numeric_metric(evidence.get("dev_metric"))
    holdout_metric = (
        holdout_sequence[-1] if holdout_sequence else _numeric_metric(evidence.get("holdout_metric"))
    )
    if dev_metric is None:
        dev_metric = _numeric_metric(collective_rounds.get("dev_metric"))
    if holdout_metric is None:
        holdout_metric = _numeric_metric(collective_rounds.get("holdout_metric"))
    holdout_improvement_count = (
        collective_rounds.get("holdout_improvement_count")
        if isinstance(collective_rounds.get("holdout_improvement_count"), int)
        and not isinstance(collective_rounds.get("holdout_improvement_count"), bool)
        else _metric_improvement_count(holdout_sequence, baseline=baseline)
    )
    best_metric = holdout_metric if holdout_metric is not None else dev_metric
    best_source = (
        "final_holdout"
        if holdout_metric is not None
        else "final_dev"
        if dev_metric is not None
        else None
    )
    protected_scope_clean = evidence.get("protected_scope_clean") is True
    checks = {
        "user_contract_accepted": contract_acceptance.get("accepted") is True,
        "visible_lanes_accepted": proof.get("visible_lanes_accepted") is True,
        "cadence_wake_verified": proof.get("cadence_wake_verified") is True,
        "pane_local_status_loaded": pane_status.get("loaded") is True,
        "collective_research_multi_round_verified": (
            collective_rounds.get("multi_round_research_verified") is True
        ),
        "visible_role_participation_verified": (
            proof.get("visible_role_participation_verified") is True
        ),
        "lane_authored_evidence_loaded": proof.get("lane_authored_evidence_loaded") is True,
        "protected_scope_clean": protected_scope_clean,
        "positive_metric_over_baseline": (
            best_metric is not None and best_metric > baseline
        ),
        "workflow_driver_false": (
            pane_status.get("workflow_driver") is False
            and wake.get("workflow_driver") is False
        ),
        "kernel_driver_contract_loaded": (
            driver.get("owner_layer") == "generic_multi_agent_kernel"
            and driver.get("user_and_preset_do_not_own_tick_driver") is True
        ),
    }
    ready = all(checks.values())
    return {
        "schema_version": "auto_research_visible_readiness_v0",
        "ready": ready,
        "readiness_level": "ready" if ready else "collecting_evidence",
        "one_command": payload.get("commands", {}).get("one_question_start_with_visible_wake")
        or payload.get("commands", {}).get("one_command_visible_wake_demo")
        if isinstance(payload.get("commands"), dict)
        else None,
        "coordination_pattern": "decentralized_state_a2a",
        "wake_model": wake.get("wakeup_model"),
        "workflow_model": driver.get("driver_model")
        or "todo_readiness_edge_plus_fixed_retry",
        "driver_owner_layer": driver.get("owner_layer"),
        "auto_research_preset_role": "thin_domain_defaults_only",
        "user_contract_invocation": contract_acceptance.get("canonical_invocation"),
        "leader_agent_required": False,
        "manual_artifact_inspection_required": not ready,
        "checks": checks,
        "missing_requirements": [key for key, passed in checks.items() if not passed],
        "contract_acceptance": {
            "accepted": contract_acceptance.get("accepted") is True,
            "required_outputs": contract_acceptance.get("required_outputs"),
            "present_outputs": contract_acceptance.get("present_outputs"),
            "one_click_start": contract_acceptance.get("checks", {}).get("one_click_start_present")
            if isinstance(contract_acceptance.get("checks"), dict)
            else False,
        },
        "pane_status": {
            "scope": "pane_local_state_check",
            "status_check_count": pane_status.get("status_check_count"),
            "lane_count": pane_status.get("lane_count"),
            "counts_as_collective_research_round": False,
        },
        "collective_research_rounds": {
            "scope": collective_rounds.get("round_unit") or "collective_agent_pass",
            "count": collective_rounds.get("collective_round_count"),
            "multi_round_verified": collective_rounds.get(
                "multi_round_research_verified"
            )
            is True,
            "visible_role_participation_verified": collective_rounds.get(
                "visible_role_participation_verified"
            )
            is True,
            "claim_source": collective_rounds.get("claim_source")
            or collective_rounds.get("source"),
            "full_participation_round_count": collective_rounds.get(
                "full_participation_round_count"
            ),
            "full_participation_count_basis": collective_rounds.get(
                "full_participation_count_basis"
            ),
            "full_participation_requirement_gap": collective_rounds.get(
                "full_participation_requirement_gap"
            ),
            "stages": collective_rounds.get("stages") or [],
            "holdout_improvement_count": holdout_improvement_count,
        },
        "improvement_summary": {
            "baseline_metric": baseline,
            "dev_metric_sequence": dev_sequence or ([] if dev_metric is None else [dev_metric]),
            "holdout_metric_sequence": holdout_sequence
            or ([] if holdout_metric is None else [holdout_metric]),
            "holdout_improvement_count": holdout_improvement_count,
            "final_dev_metric": dev_metric,
            "final_holdout_metric": holdout_metric,
            "best_metric": best_metric,
            "best_metric_source": best_source,
            "improved_over_baseline": best_metric is not None and best_metric > baseline,
            "holdout_delta_over_dev": (
                holdout_metric - dev_metric
                if holdout_metric is not None and dev_metric is not None
                else None
            ),
        },
        "public_boundary": {
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
            "credentials_recorded": False,
        },
    }


def _load_visible_readiness_into_payload(payload: dict[str, object]) -> None:
    payload["visible_readiness"] = _build_visible_readiness(payload)


def run_auto_research_demo_e2e(
    *,
    agent_id: str,
    goal_id: str,
    tracking_goal_id: str | None,
    objective: str,
    preset_id: str | None = None,
    output_dir: str,
    execute: bool,
    launch_visible: bool,
    keep_workspace: bool,
    registry_path: Path,
    runtime_root_arg: str | None,
    session_name: str,
    cli_bin: str,
    codex_bin: str,
    tmux_bin: str,
    reasoning_effort: str,
    output_language: str,
    live_evidence_path: str | None,
    append_evidence: AppendEvidence,
    visible_launcher: VisibleLauncher | None = None,
    visible_wake: VisibleWake | None = None,
    wake_visible_after_launch: bool = False,
    goal_surface_mode: str = "explicit_goal",
    agent_specs: Sequence[str] | None = None,
    run_worker_loop: bool = False,
    worker_loop_rounds: int = 4,
    configure_visible_worker_turn: bool = False,
    visible_live_evidence_wait_seconds: float = 30.0,
) -> dict[str, object]:
    if launch_visible and not execute:
        raise ValueError("--launch-visible requires --execute")
    if run_worker_loop and not execute:
        raise ValueError("--run-worker-loop requires --execute")
    if worker_loop_rounds < 1:
        raise ValueError("--worker-loop-rounds must be >= 1")
    if launch_visible and visible_launcher is None:
        raise ValueError("--launch-visible requires a visible launcher callback")
    if wake_visible_after_launch and not launch_visible:
        raise ValueError("--wake-visible-after-launch requires --launch-visible")
    if wake_visible_after_launch and visible_wake is None:
        raise ValueError("--wake-visible-after-launch requires a visible wake callback")
    if live_evidence_path and not execute:
        raise ValueError("--live-evidence requires --execute")

    tracking_goal = tracking_goal_id.strip() if isinstance(tracking_goal_id, str) else ""
    if tracking_goal == goal_id:
        tracking_goal = ""
    reuses_default_internal_goal = goal_id == AUTO_RESEARCH_DEFAULT_GOAL_ID
    effective_agent_specs = list(agent_specs or [])
    if (run_worker_loop or launch_visible) and not effective_agent_specs:
        effective_agent_specs = default_auto_research_agent_specs()
    user_contract = build_auto_research_user_contract(
        objective,
        output_language=output_language,
        preset_id=preset_id,
    )
    preset_context = (
        user_contract.get("preset_context")
        if isinstance(user_contract.get("preset_context"), dict)
        else None
    )
    contract_acceptance = build_auto_research_contract_acceptance(user_contract)
    supervisor = build_auto_research_demo_supervisor_plan(
        goal_id=goal_id,
        open_question=objective,
        preset_context=preset_context,
        agent_specs=effective_agent_specs,
        session_name=session_name,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        tmux_bin=tmux_bin,
        reasoning_effort=reasoning_effort,
        output_language=output_language,
        configure_visible_worker_turn=configure_visible_worker_turn,
    )
    execution_kind = (
        "loopx_worker_loop"
        if execute and run_worker_loop
        else "visible_worker_launch"
        if execute and launch_visible
        else "worker_loop_preview"
    )
    result_source = (
        "loopx_worker_loop_public_evidence"
        if execute and run_worker_loop
        else "visible_worker_launcher"
        if execute and launch_visible
        else "dry_run_preview"
    )
    payload: dict[str, object] = {
        "ok": True,
        "schema_version": AUTO_RESEARCH_DEMO_E2E_SCHEMA_VERSION,
        "mode": "execute" if execute else "dry_run",
        "execution_kind": execution_kind,
        "result_source": result_source,
        "goal_id": goal_id,
        "tracking_goal_id": tracking_goal or None,
        "route_contract": _build_demo_frontier_route_contract(
            goal_id=goal_id,
            tracking_goal=tracking_goal,
            preset_context=preset_context,
            goal_surface_mode=goal_surface_mode,
            reuses_default_internal_goal=reuses_default_internal_goal,
        ),
        "agent_id": agent_id,
        "reasoning_effort": reasoning_effort,
        "output_language": output_language,
        "user_contract": user_contract,
        "preset_context": preset_context,
        "contract_acceptance": contract_acceptance,
        "commands": _build_demo_e2e_commands(
            cli_bin=cli_bin,
            objective=objective,
            preset_id=preset_id,
            output_language=output_language,
            goal_id=goal_id,
            agent_id=agent_id,
            tracking_goal=tracking_goal,
            session_name=session_name,
        ),
        "supervisor": _supervisor_summary(supervisor),
        "public_boundary": {
            "raw_logs_recorded": False,
            "private_artifacts_recorded": False,
            "absolute_paths_recorded": False,
            "credentials_recorded": False,
            "local_workspace_path_redacted": True,
            "writes_loopx_state": bool(execute),
            "launches_visible_lanes": bool(launch_visible),
            "visible_codex_sessions_recorded": False,
        },
        "visible_worker_proof": build_auto_research_live_worker_proof(
            launch_visible=launch_visible,
        ),
        "visible_worker_turn_validation": {
            "schema_version": "auto_research_visible_worker_turn_validation_v0",
            "configured": bool(configure_visible_worker_turn),
            "counts_as_research_evidence_without_role_output": False,
            "manual_research_required_is_blocker_not_progress": True,
        },
    }
    if not execute:
        payload["worker_loop_preview"] = {
            "executed": False,
            "result_source": "dry_run_preview",
            "expected_steps": (
                "seed a demo-local LoopX queue, let role-compatible workers claim "
                "frontier todos, write public-safe evidence, append rollout events, "
                "and read compact evidence from state"
            ),
            "coordination_pattern": "decentralized_state_a2a",
        }
        return payload

    tmp_obj: tempfile.TemporaryDirectory[str] | None = None
    if keep_workspace or launch_visible:
        demo_root = Path(tempfile.mkdtemp(prefix="loopx-auto-research-demo-e2e."))
    else:
        tmp_obj = tempfile.TemporaryDirectory(prefix="loopx-auto-research-demo-e2e.")
        demo_root = Path(tmp_obj.name)
    try:
        visible_control: dict[str, object] | None = None
        visible_registry_path: Path | None = None
        visible_runtime_root_arg: str | None = None

        def ensure_visible_control_plane() -> tuple[dict[str, object], Path, str | None]:
            nonlocal visible_control, visible_registry_path, visible_runtime_root_arg
            if visible_control is None or visible_registry_path is None:
                (
                    visible_control,
                    visible_registry_path,
                    visible_runtime_root_arg,
                ) = _seed_visible_demo_control_plane(
                    demo_root=demo_root,
                    goal_id=goal_id,
                    objective=objective,
                    supervisor=supervisor,
                    preset_context=preset_context,
                )
                payload["visible_control_plane"] = visible_control
            return visible_control, visible_registry_path, visible_runtime_root_arg

        visible_control, visible_registry_path, visible_runtime_root_arg = ensure_visible_control_plane()
        if run_worker_loop:
            worker_agent_ids = [
                str(lane.get("agent_id") or "").strip()
                for lane in supervisor.get("lanes") or []
                if isinstance(lane, dict) and str(lane.get("agent_id") or "").strip()
            ]

            def append_worker_evidence(packet_path: str) -> dict[str, object]:
                return append_auto_research_rollout_events(
                    packet_path=packet_path,
                    registry_path=visible_registry_path,
                    runtime_root_arg=visible_runtime_root_arg,
                    dry_run=False,
                )

            worker_loop = run_auto_research_worker_loop(
                registry_path=visible_registry_path,
                runtime_root_arg=visible_runtime_root_arg,
                goal_id=goal_id,
                agent_ids=worker_agent_ids,
                objective=objective,
                workspace=demo_root / "visible-control-plane",
                output_dir=output_dir,
                execute=True,
                append_evidence=append_worker_evidence,
                lane_count=len(worker_agent_ids),
                visible_lanes_accepted=True,
                complete_selected_todo=True,
                max_rounds=worker_loop_rounds,
            )
            payload["worker_loop"] = worker_loop
            collective_rounds = _collective_summary_from_worker_loop(
                worker_loop,
                agent_count=len(worker_agent_ids),
            )
            _load_collective_research_rounds_into_payload(
                payload=payload,
                rounds=collective_rounds,
            )
            turns = worker_loop.get("turns") if isinstance(worker_loop.get("turns"), list) else []
            dev_metrics = [
                metric
                for turn in turns
                if isinstance(turn, dict)
                for metric in [_numeric_metric(turn.get("dev_metric"))]
                if metric is not None
            ]
            holdout_metrics = [
                metric
                for turn in turns
                if isinstance(turn, dict)
                for metric in [_numeric_metric(turn.get("holdout_metric"))]
                if metric is not None
            ]
            dev_metric = dev_metrics[-1] if dev_metrics else None
            holdout_metric = holdout_metrics[-1] if holdout_metrics else None
            positive_result = holdout_metric is not None or dev_metric is not None
            payload["tonight_experience"] = {
                "schema_version": "auto_research_tonight_experience_v0",
                "ready": positive_result,
                "one_command": payload["commands"]["one_command_worker_loop"],
                "goal_id": goal_id,
                "goal_surface_mode": goal_surface_mode,
                "coordination_pattern": "decentralized_state_a2a",
                "workflow_model": "state_projected_frontier_not_dynamic_workflow",
                "driver_role": "polling_driver_only",
                "leader_agent_required": False,
                "worker_loop_round_count": worker_loop.get("round_count"),
                "collective_research_round_count": collective_rounds.get(
                    "collective_round_count"
                ),
                "collective_multi_round_verified": collective_rounds.get(
                    "multi_round_research_verified"
                ),
                "executed_turn_count": worker_loop.get("executed_turn_count"),
                "completed_turn_count": worker_loop.get("completed_turn_count"),
                "selected_actions": worker_loop.get("selected_actions"),
                "dev_metric_sequence": dev_metrics,
                "holdout_metric_sequence": holdout_metrics,
                "holdout_improvement_count": collective_rounds.get(
                    "holdout_improvement_count"
                ),
                "dev_metric": dev_metric,
                "holdout_metric": holdout_metric,
                "positive_result": positive_result,
                "positive_result_basis": (
                    "public_safe_dev_and_holdout_evidence"
                    if holdout_metric is not None
                    else "public_safe_dev_evidence"
                    if dev_metric is not None
                    else "requires_visible_lane_authored_evidence"
                ),
                "state_surfaces": [
                    "demo-local LoopX registry",
                    "quota/frontier selection",
                    "todo completion",
                    "rollout event log",
                ],
                "worker_contract": (
                    "Each role reads the shared LoopX state surface, accepts only its projected "
                    "frontier item, writes public-safe evidence, and completes the selected todo."
                ),
                "public_boundary": {
                    "raw_logs_recorded": False,
                    "private_artifacts_recorded": False,
                    "absolute_paths_recorded": False,
                    "visible_codex_lane_authored": False,
                },
            }
        if launch_visible and visible_launcher is not None:
            (
                visible_control,
                visible_registry_path,
                visible_runtime_root_arg,
            ) = ensure_visible_control_plane()
            visible_payload = visible_launcher(
                dict(supervisor),
                visible_registry_path,
                visible_runtime_root_arg,
                demo_root,
            )
            launch_result = (
                visible_payload.get("launch_result")
                if isinstance(visible_payload.get("launch_result"), dict)
                else {}
            )
            visible_acceptance = (
                launch_result.get("visible_acceptance")
                if isinstance(launch_result.get("visible_acceptance"), dict)
                else {}
            )
            payload["visible_launch"] = {
                "mode": visible_payload.get("mode"),
                "launch_result": launch_result,
                "boundary": visible_payload.get("boundary"),
            }
            visible_proof = payload["visible_worker_proof"]
            if isinstance(visible_proof, dict):
                visible_proof["visible_lanes_launched"] = True
                visible_proof["visible_lanes_accepted"] = bool(visible_acceptance.get("accepted"))
                visible_proof["evidence_source"] = "visible_launcher"
            if wake_visible_after_launch and visible_wake is not None:
                session = str(launch_result.get("session_name") or session_name)
                workspace = demo_root / "visible-control-plane"

                # Build lane_id → agent_id mapping from the supervisor lanes.
                lane_agent_lookup: dict[str, str] = {}
                for lane in supervisor.get("lanes") or []:
                    if not isinstance(lane, dict):
                        continue
                    lid = str(lane.get("lane_id") or "").strip()
                    aid = str(lane.get("agent_id") or "").strip()
                    if lid and aid:
                        lane_agent_lookup[lid] = aid

                # Filter: only wake lanes that have a selected runnable todo and
                # are not in quiet-completion.
                ready_lanes: list[str] = []
                skipped_lanes: list[dict[str, object]] = []
                for raw_lane in launch_result.get("started_lanes") or []:
                    lane_id = str(raw_lane).strip() if raw_lane else ""
                    if not lane_id:
                        continue
                    agent_id = lane_agent_lookup.get(lane_id)
                    if not agent_id:
                        skipped_lanes.append({
                            "lane_id": lane_id,
                            "reason": "no_agent_mapping",
                        })
                        continue
                    try:
                        frontier = load_auto_research_worker_frontier(
                            registry_path=visible_registry_path,
                            runtime_root_arg=visible_runtime_root_arg,
                            goal_id=goal_id,
                            agent_id=agent_id,
                            workspace=workspace,
                        )
                    except Exception:
                        skipped_lanes.append({
                            "lane_id": lane_id,
                            "agent_id": agent_id,
                            "reason": "frontier_load_failed",
                            "error_code": "FRONTIER_LOAD_FAILED",
                        })
                        continue
                    frontier_data = (
                        frontier.get("frontier")
                        if isinstance(frontier.get("frontier"), dict)
                        else {}
                    )
                    selected = frontier_data.get("selected")
                    completion = (
                        frontier_data.get("completion")
                        if isinstance(frontier_data.get("completion"), dict)
                        else None
                    )
                    quota = (
                        frontier.get("quota")
                        if isinstance(frontier.get("quota"), dict)
                        else {}
                    )
                    if completion and completion.get("quiet_completion_allowed") is True:
                        skipped_lanes.append({
                            "lane_id": lane_id,
                            "agent_id": agent_id,
                            "reason": "quiet_completion_allowed",
                        })
                        continue
                    if not selected:
                        skipped_lanes.append({
                            "lane_id": lane_id,
                            "agent_id": agent_id,
                            "reason": "no_selected_todo",
                            "runnable_count": frontier_data.get("runnable_count"),
                            "blocked_count": frontier_data.get("blocked_count"),
                        })
                        continue
                    if quota.get("should_run") is False:
                        skipped_lanes.append({
                            "lane_id": lane_id,
                            "agent_id": agent_id,
                            "reason": "quota_should_run_false",
                            "quota_state": quota.get("state"),
                        })
                        continue
                    ready_lanes.append(lane_id)

                if not ready_lanes:
                    wake_payload: dict[str, object] = {
                        "ok": True,
                        "schema_version": "multi_agent_pane_a2a_wakeup_v0",
                        "mode": "no_op_all_filtered",
                        "session_name": session,
                        "target_lanes": [],
                        "prompt": "",
                        "prompt_hash": "",
                        "coordination_model": "decentralized_state_a2a",
                        "wakeup_model": "state_aware_filter_no_ready_lanes",
                        "workflow_driver": False,
                        "broadcaster_reads_frontier": False,
                        "broadcaster_reads_todo_readiness": False,
                        "broadcaster_selects_todo": False,
                        "prompt_delivery": "skipped_no_ready_lanes",
                        "prompt_delivered": False,
                        "auto_wake_backoff_recommended": False,
                    }
                else:
                    wake_payload = visible_wake(session, ready_lanes)
                if skipped_lanes:
                    wake_payload = dict(wake_payload)
                    wake_payload["state_aware_filter"] = {
                        "schema_version": "auto_research_state_aware_wake_filter_v0",
                        "total_started_lanes": len(
                            [
                                s for s in (launch_result.get("started_lanes") or [])
                                if str(s).strip()
                            ]
                        ),
                        "ready_lane_count": len(ready_lanes),
                        "skipped_lane_count": len(skipped_lanes),
                        "skipped_lanes": skipped_lanes,
                    }
                _load_visible_wake_into_payload(
                    payload=payload,
                    wake=wake_payload,
                )
            pane_status = _discover_visible_pane_a2a_status(
                runtime_root_arg=visible_runtime_root_arg,
                session_name=str(launch_result.get("session_name") or session_name),
                goal_id=goal_id,
                wait_seconds=visible_live_evidence_wait_seconds,
            )
            if pane_status is not None:
                _load_visible_pane_a2a_status_into_payload(
                    payload=payload,
                    status=pane_status,
                )
            collective_rounds = None
            live_evidence = _discover_visible_live_evidence(
                runtime_root_arg=visible_runtime_root_arg,
                session_name=str(launch_result.get("session_name") or session_name),
                goal_id=goal_id,
                wait_seconds=visible_live_evidence_wait_seconds,
            )
            if live_evidence is not None:
                _load_live_worker_evidence_into_payload(
                    payload=payload,
                    evidence=live_evidence,
                    evidence_source="visible_launcher_artifact",
                )
                if collective_rounds is None and pane_status is not None:
                    collective_rounds = _collective_summary_from_visible_evidence(
                        pane_status=pane_status,
                        evidence=payload["live_worker_evidence"],
                        agent_count=(
                            int(pane_status.get("lane_count"))
                            if isinstance(pane_status.get("lane_count"), int)
                            and not isinstance(pane_status.get("lane_count"), bool)
                            else None
                        ),
                        expected_lanes=[
                            {
                                "agent_id": lane.get("agent_id"),
                                "lane_id": lane.get("lane_id"),
                                "role_id": lane.get("role_id"),
                            }
                            for lane in supervisor.get("lanes") or []
                            if isinstance(lane, dict)
                        ],
                    )
                    _load_collective_research_rounds_into_payload(
                        payload=payload,
                        rounds=collective_rounds,
                    )
            _load_visible_readiness_into_payload(payload)
        payload["workspace_retained"] = keep_workspace or launch_visible
        if live_evidence_path:
            live_evidence = load_live_codex_e2e_evidence(
                evidence_path=live_evidence_path,
                goal_id=goal_id,
                agent_id=agent_id,
            )
            _load_live_worker_evidence_into_payload(
                payload=payload,
                evidence=live_evidence,
                evidence_source="live_worker_evidence",
            )
        return payload
    finally:
        if tmp_obj is not None:
            tmp_obj.cleanup()
