from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .bootstrap import default_goal_id
from .paths import shell_selected_global_registry
from .codex_cli_probe_markdown import (
    render_codex_cli_bounded_visible_pilot_adapter_markdown as render_codex_cli_bounded_visible_pilot_adapter_markdown,
    render_codex_cli_local_driver_plan_markdown as render_codex_cli_local_driver_plan_markdown,
    render_codex_cli_local_scheduler_executor_markdown as render_codex_cli_local_scheduler_executor_markdown,
    render_codex_cli_local_scheduler_tick_markdown as render_codex_cli_local_scheduler_tick_markdown,
    render_codex_cli_one_message_loop_pilot_markdown as render_codex_cli_one_message_loop_pilot_markdown,
    render_codex_cli_runtime_idle_detector_markdown as render_codex_cli_runtime_idle_detector_markdown,
    render_codex_cli_session_probe_markdown as render_codex_cli_session_probe_markdown,
    render_codex_cli_visible_attach_acceptance_markdown as render_codex_cli_visible_attach_acceptance_markdown,
    render_codex_cli_visible_driver_plan_markdown as render_codex_cli_visible_driver_plan_markdown,
    render_codex_cli_visible_driver_run_packet_markdown as render_codex_cli_visible_driver_run_packet_markdown,
    render_codex_cli_visible_first_response_capture_plan_markdown as render_codex_cli_visible_first_response_capture_plan_markdown,
    render_codex_cli_visible_local_driver_pilot_markdown as render_codex_cli_visible_local_driver_pilot_markdown,
    render_codex_cli_visible_session_proof_markdown as render_codex_cli_visible_session_proof_markdown,
)
from .codex_cli_runtime_probe import (
    DEFAULT_CODEX_BIN as DEFAULT_CODEX_BIN,
    DEFAULT_EXECUTOR_TIMEOUT_SECONDS as DEFAULT_EXECUTOR_TIMEOUT_SECONDS,
    DEFAULT_MIN_HUMAN_INPUT_IDLE_SECONDS as DEFAULT_MIN_HUMAN_INPUT_IDLE_SECONDS,
    DEFAULT_TIMEOUT_SECONDS as DEFAULT_TIMEOUT_SECONDS,
    HELP_COMMANDS as HELP_COMMANDS,
    FIRST_RESPONSE_REQUIRED_FALSE_CHECKS,
    FIRST_RESPONSE_REQUIRED_TRUE_CHECKS,
    RUNTIME_IDLE_REQUIRED_FALSE_CHECKS,
    RUNTIME_IDLE_REQUIRED_TRUE_CHECKS,
    _nested_bool,
    _nested_false,
    _shell_arg,
    build_codex_cli_runtime_idle_detector as build_codex_cli_runtime_idle_detector,
    build_codex_cli_runtime_idle_observation_payload as build_codex_cli_runtime_idle_observation_payload,
    build_codex_cli_visible_session_proof as build_codex_cli_visible_session_proof,
    classify_codex_cli_session_surface as classify_codex_cli_session_surface,
    load_codex_cli_first_response_fixture as load_codex_cli_first_response_fixture,
    load_codex_cli_probe_fixture as load_codex_cli_probe_fixture,
    load_codex_cli_runtime_idle_fixture as load_codex_cli_runtime_idle_fixture,
    load_codex_cli_visible_session_proof_fixture as load_codex_cli_visible_session_proof_fixture,
    probe_human_input_idle_seconds as probe_human_input_idle_seconds,
    run_codex_cli_session_probe as run_codex_cli_session_probe,
)
from .project_prompt import (
    CODEX_CLI_VISIBLE_SCHEDULER_CONTEXT,
    build_codex_cli_bootstrap_message,
    render_scheduler_execution_args,
)


CODEX_CLI_SCHEDULER_ARGS = render_scheduler_execution_args(
    scheduler_execution_context=CODEX_CLI_VISIBLE_SCHEDULER_CONTEXT
)


def build_codex_cli_visible_attach_acceptance(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
    probe_payload: dict[str, Any],
    proof_payload: dict[str, Any] | None = None,
    idle_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide whether evidence is strong enough for same-TUI visible attach.

    This packet is an acceptance gate, not an executor. It deliberately keeps
    Codex CLI `resume` / `remote-control` surfaces in a spike lane unless a
    public-safe proof shows a visible same-TUI attach and a fresh idle detector
    proves the later turn is not racing the user or an existing turn.
    """

    local_plan = build_codex_cli_local_driver_plan(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        probe_payload=probe_payload,
    )
    visible_plan = (
        local_plan.get("visible_driver_plan")
        if isinstance(local_plan.get("visible_driver_plan"), dict)
        else {}
    )
    proof = build_codex_cli_visible_session_proof(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        proof_payload=proof_payload,
    )
    idle_detector = build_codex_cli_runtime_idle_detector(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        idle_payload=idle_payload,
    )
    resolved_project = str(local_plan["project"])
    resolved_goal_id = str(local_plan["goal_id"])
    agent_arg = f" --agent-id {_shell_arg(agent_id)}" if agent_id else ""
    common_args = (
        f"--project {_shell_arg(resolved_project)} "
        f"--goal-id {_shell_arg(resolved_goal_id)}{agent_arg} "
        f"--codex-bin {_shell_arg(codex_bin)}"
    )
    proof_approved = proof.get("approved_for_same_session_automation") is True
    idle_approved = idle_detector.get("approved_for_visible_later_turn") is True
    observed_surface = str(proof.get("observed_surface") or "unknown")
    driver_mode = str(local_plan.get("driver_mode") or "tui_bootstrap_only")
    same_tui_proof = proof_approved and observed_surface == "same_tui_visible_attach"
    accepted_same_tui = same_tui_proof and idle_approved
    visible_later_turn_candidate = proof_approved and idle_approved

    blockers: list[str] = []
    if accepted_same_tui:
        decision = "same_tui_visible_attach_accepted"
        acceptance_action = "allow_opt_in_same_tui_visible_turn_after_fresh_quota_guard"
        next_safe_step = (
            "wire the proven same-TUI visible attach primitive behind a fresh "
            "quota guard, runtime idle detector, and explicit command boundary"
        )
    elif proof_approved and idle_approved:
        decision = "visible_surface_spike_passed_not_same_tui"
        acceptance_action = "keep_visible_surface_as_spike_candidate_not_same_tui_attach"
        blockers.append("same_tui_visible_attach_not_proven")
        next_safe_step = (
            "keep one-message TUI bootstrap primary; treat the proven visible "
            "resume/remote-control surface as an opt-in spike until same-TUI "
            "attachment is demonstrated"
        )
    elif proof_approved:
        decision = "runtime_idle_evidence_required"
        acceptance_action = "capture_runtime_idle_evidence_before_later_visible_turn"
        blockers.extend(idle_detector.get("failures") or ["runtime_idle_evidence_missing"])
        next_safe_step = (
            "capture a public-safe runtime idle fixture or local observation "
            "before any later visible Codex CLI prompt"
        )
    elif driver_mode in {"session_attached_visible_turn", "visible_resume_or_remote_control_spike"}:
        decision = "visible_session_proof_required"
        acceptance_action = "capture_public_safe_visible_session_proof"
        if proof_payload is None:
            blockers.append("visible_session_proof_missing")
        else:
            blockers.extend(proof.get("failures") or ["visible_session_proof_incomplete"])
        next_safe_step = (
            "do not call this accepted automation yet; first prove visibility, "
            "interruptibility, boundaries, and compact writeback planning"
        )
    else:
        decision = "tui_bootstrap_only"
        acceptance_action = "ask_user_to_start_inside_codex_cli_tui"
        blockers.append("codex_cli_attach_surface_not_exposed_by_probe")
        next_safe_step = (
            "ask the user to start in Codex CLI TUI and paste the bootstrap message; "
            "headless codex exec is disabled for this product path"
        )

    commands = (
        local_plan.get("commands")
        if isinstance(local_plan.get("commands"), dict)
        else {}
    )
    return {
        "ok": True,
        "schema_version": "codex_cli_visible_attach_acceptance_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "codex_bin": codex_bin,
        "decision": decision,
        "acceptance_action": acceptance_action,
        "accepted_for_same_tui_automation": accepted_same_tui,
        "accepted_for_visible_later_turn": visible_later_turn_candidate,
        "observed_surface": observed_surface,
        "driver_mode": driver_mode,
        "next_safe_step": next_safe_step,
        "blockers": blockers,
        "requirements": {
            "help_probe_required": True,
            "public_safe_visible_session_proof_required": True,
            "runtime_idle_detector_required": True,
            "same_tui_surface_required_for_same_tui_acceptance": True,
            "fresh_quota_guard_required_before_execution": True,
            "headless_execution_disabled": True,
        },
        "probe": {
            "schema_version": probe_payload.get("schema_version"),
            "source": probe_payload.get("source"),
            "recommended_mode": probe_payload.get("recommended_mode"),
            "capabilities": probe_payload.get("capabilities"),
            "warnings": probe_payload.get("warnings") or [],
        },
        "visible_driver_plan": {
            "schema_version": visible_plan.get("schema_version"),
            "driver_mode": visible_plan.get("driver_mode"),
            "automation_action": visible_plan.get("automation_action"),
            "next_step": visible_plan.get("next_step"),
        },
        "visible_session_proof": {
            "supplied": proof_payload is not None,
            "approved": proof_approved,
            "decision": proof.get("decision"),
            "observed_surface": proof.get("observed_surface"),
            "failures": proof.get("failures") or [],
        },
        "runtime_idle_detector": {
            "supplied": idle_payload is not None,
            "approved": idle_approved,
            "decision": idle_detector.get("decision"),
            "failures": idle_detector.get("failures") or [],
            "source": idle_detector.get("source"),
        },
        "commands": {
            "session_probe": f"{_shell_arg(cli_bin)} codex-cli-session-probe --codex-bin {_shell_arg(codex_bin)}",
            "local_driver_plan": commands.get("local_driver_plan"),
            "visible_driver_plan": commands.get("visible_driver_plan"),
            "visible_attach_acceptance": (
                f"{_shell_arg(cli_bin)} codex-cli-visible-attach-acceptance {common_args} "
                "--proof-fixture <public-visible-proof.json> --idle-fixture <public-runtime-idle.json>"
            ),
            "visible_session_proof": (
                f"{_shell_arg(cli_bin)} codex-cli-visible-session-proof "
                f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
                f"{agent_arg} --proof-fixture <public-visible-proof.json>"
            ),
            "runtime_idle_detector_fixture": (
                f"{_shell_arg(cli_bin)} codex-cli-runtime-idle-detector "
                f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
                f"{agent_arg} --idle-fixture <public-runtime-idle.json>"
            ),
            "tui_bootstrap_message": commands.get("tui_bootstrap_message"),
            "explicit_headless_fallback": None,
            "headless_fallback_disabled": commands.get("headless_fallback_disabled"),
        },
        "boundary": {
            "acceptance_packet_only": True,
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
            "writes_loopx_state": False,
            "requires_fresh_quota_guard_before_execution": True,
            "headless_execution_disabled": True,
        },
        "warnings": list(probe_payload.get("warnings") or []),
    }


def build_codex_cli_visible_driver_plan(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
    probe_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a public-safe plan for a visible Codex CLI automation driver.

    The plan is intentionally read-only. It decides whether a future local
    driver should attempt a visible session attachment, run a resume/remote
    control spike, or keep the one-message TUI bootstrap as the product path.
    """

    resolved_project = str(project.expanduser())
    resolved_goal_id = goal_id or default_goal_id(project)
    capabilities = probe_payload.get("capabilities") if isinstance(probe_payload.get("capabilities"), dict) else {}
    safe_injection_supported = bool(capabilities.get("safe_injection_supported"))
    visible_resume_supported = bool(capabilities.get("visible_resume_supported"))
    remote_control_supported = bool(capabilities.get("remote_control_surface_detected"))
    agent_arg = f" --agent-id {_shell_arg(agent_id)}" if agent_id else ""
    quota_guard_command = (
        f"{_shell_arg(cli_bin)} --format json quota should-run "
        f"--goal-id {_shell_arg(resolved_goal_id)}{agent_arg}"
        f"{CODEX_CLI_SCHEDULER_ARGS}"
    )
    bootstrap_command = (
        f"{_shell_arg(cli_bin)} codex-cli-bootstrap-message "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}{agent_arg}"
    )
    probe_command = f"{_shell_arg(cli_bin)} codex-cli-session-probe --codex-bin {_shell_arg(codex_bin)}"

    if safe_injection_supported:
        driver_mode = "session_attached_visible_turn"
        automation_action = "try_visible_session_attach_with_idle_guard"
        next_step = "wire the detected visible attach primitive behind idle guard and quota guard"
    elif visible_resume_supported or remote_control_supported:
        driver_mode = "visible_resume_or_remote_control_spike"
        automation_action = "prototype_visible_resume_or_remote_control_with_idle_guard"
        next_step = "run an explicit proof that resume or remote-control creates a visible interruptible turn"
    else:
        driver_mode = "tui_bootstrap_only"
        automation_action = "ask_user_to_start_inside_codex_cli_tui"
        next_step = "ask the user to start in Codex CLI TUI and paste the bootstrap message; headless fallback is disabled"

    driver_steps = [
        "run the session probe and quota guard before any delivery turn",
        "if user_channel.action_required=true, surface only the concrete user gate",
        "if delivery is allowed, verify idle_guard before any visible prompt",
        "prefer a visible same-TUI turn; otherwise keep the one-message TUI bootstrap as the product path",
        "write back compact evidence and spend quota only after validation",
    ]
    if driver_mode == "visible_resume_or_remote_control_spike":
        driver_steps.insert(
            3,
            "treat resume [PROMPT] or remote-control as unproven until a visible interruptible turn is observed",
        )
    if driver_mode == "session_attached_visible_turn":
        driver_steps.insert(3, "use only the detected visible attach primitive; do not write hidden session state")

    return {
        "ok": True,
        "schema_version": "codex_cli_visible_driver_plan_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "codex_bin": codex_bin,
        "probe_source": probe_payload.get("source"),
        "probe_recommended_mode": probe_payload.get("recommended_mode"),
        "driver_mode": driver_mode,
        "automation_action": automation_action,
        "next_step": next_step,
        "capabilities": capabilities,
        "commands": {
            "probe": probe_command,
            "quota_guard": quota_guard_command,
            "tui_bootstrap_message": bootstrap_command,
            "explicit_headless_fallback": None,
            "headless_fallback_disabled": "headless codex exec is disabled for the default LoopX setup-then-goal bootstrap path",
        },
        "driver_steps": driver_steps,
        "boundary": {
            "dry_run_plan_only": True,
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
            "requires_idle_guard_before_visible_prompt": True,
            "requires_user_gate_stop": True,
            "headless_execution_disabled": True,
        },
        "warnings": list(probe_payload.get("warnings") or []),
    }


def build_codex_cli_local_driver_plan(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
    probe_payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a dry-run-first local driver plan for Codex CLI.

    This does not launch Codex or mutate any session. It composes the existing
    one-message TUI bootstrap, visible-driver plan, quota guard, and the
    headless-disabled boundary into one operator-facing decision packet.
    """

    visible_plan = build_codex_cli_visible_driver_plan(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        probe_payload=probe_payload,
    )
    resolved_project = visible_plan["project"]
    resolved_goal_id = visible_plan["goal_id"]
    agent_arg = f" --agent-id {_shell_arg(agent_id)}" if agent_id else ""
    visible_driver_plan_command = (
        f"{_shell_arg(cli_bin)} codex-cli-visible-driver-plan "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}{agent_arg} "
        f"--codex-bin {_shell_arg(codex_bin)}"
    )
    quota_guard_command = (
        f"{_shell_arg(cli_bin)} --format json "
        f"--registry {shell_selected_global_registry()} "
        f"quota should-run --goal-id {_shell_arg(resolved_goal_id)}{agent_arg}"
        f"{CODEX_CLI_SCHEDULER_ARGS}"
    )
    bootstrap_command = (
        f"{_shell_arg(cli_bin)} codex-cli-bootstrap-message "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}{agent_arg}"
    )
    driver_mode = str(visible_plan.get("driver_mode") or "tui_bootstrap_only")

    if driver_mode == "session_attached_visible_turn":
        decision = "attempt_visible_session_attach_after_idle_guard"
        operator_instruction = (
            "Run quota guard, verify idle guard, then attempt only the detected visible attach primitive."
        )
    elif driver_mode == "visible_resume_or_remote_control_spike":
        decision = "run_visible_resume_or_remote_control_proof"
        operator_instruction = (
            "Treat resume or remote-control as a proof target, not production session attachment, until the turn is visible and interruptible."
        )
    else:
        decision = "ask_user_to_start_from_tui"
        operator_instruction = (
            "Ask the user to start inside Codex CLI TUI and paste the bootstrap message; "
            "headless codex exec is disabled for this product path."
        )

    return {
        "ok": True,
        "schema_version": "codex_cli_local_driver_plan_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "codex_bin": codex_bin,
        "driver_phase": "dry_run_plan",
        "driver_mode": driver_mode,
        "decision": decision,
        "operator_instruction": operator_instruction,
        "visible_driver_plan": {
            "schema_version": visible_plan.get("schema_version"),
            "driver_mode": visible_plan.get("driver_mode"),
            "automation_action": visible_plan.get("automation_action"),
            "next_step": visible_plan.get("next_step"),
        },
        "commands": {
            "quota_guard": quota_guard_command,
            "local_driver_plan": (
                f"{_shell_arg(cli_bin)} codex-cli-local-driver-plan "
                f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}{agent_arg} "
                f"--codex-bin {_shell_arg(codex_bin)}"
            ),
            "visible_driver_plan": visible_driver_plan_command,
            "tui_bootstrap_message": bootstrap_command,
            "explicit_headless_fallback": None,
            "headless_fallback_disabled": "headless codex exec is disabled for the default LoopX setup-then-goal bootstrap path",
        },
        "driver_steps": [
            "run quota_guard and stop when user_channel.action_required=true",
            "run visible_driver_plan to classify TUI, resume, or remote-control mode",
            "verify idle_guard before any visible resume or remote-control prompt",
            "prefer one-message TUI bootstrap until visible attach is proven",
            "do not offer headless codex exec from the default LoopX setup-then-goal bootstrap path",
            "write back compact evidence or a precise blocker before quota spend",
        ],
        "idle_guard": {
            "required": True,
            "implemented": False,
            "placeholder": "future driver must prove no active human typing and no running turn before visible resume or remote-control prompt",
        },
        "execution_policy": {
            "tui_bootstrap_primary": True,
            "headless_execution_disabled": True,
            "same_session_attachment_requires_visible_proof": True,
            "quota_guard_required": True,
            "spend_after_validated_writeback_only": True,
        },
        "boundary": {
            "dry_run_plan_only": True,
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
            "requires_idle_guard_before_visible_prompt": True,
            "requires_user_gate_stop": True,
            "headless_execution_disabled": True,
        },
        "warnings": list(visible_plan.get("warnings") or []),
    }


def build_codex_cli_visible_driver_run_packet(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
    probe_payload: dict[str, Any],
    proof_payload: dict[str, Any] | None = None,
    allow_headless_fallback: bool = False,
) -> dict[str, Any]:
    """Build the v0 runner packet for one visible Codex CLI driver turn.

    The packet is deliberately not an executor. It converts the dry-run local
    driver plan and optional visible-session proof into the next safe command
    boundary for a local scheduler or human operator. Headless fallback remains
    disabled for this default LoopX setup-then-goal bootstrap path.
    """

    local_plan = build_codex_cli_local_driver_plan(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        probe_payload=probe_payload,
    )
    resolved_project = str(local_plan["project"])
    resolved_goal_id = str(local_plan["goal_id"])
    driver_mode = str(local_plan.get("driver_mode") or "tui_bootstrap_only")
    commands = local_plan.get("commands") if isinstance(local_plan.get("commands"), dict) else {}
    proof = build_codex_cli_visible_session_proof(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        proof_payload=proof_payload,
    ) if proof_payload is not None else None
    proof_approved = bool(proof and proof.get("approved_for_same_session_automation") is True)
    proof_command = (
        f"{_shell_arg(cli_bin)} codex-cli-visible-session-proof "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
        f"{' --agent-id ' + _shell_arg(agent_id) if agent_id else ''} "
        "--proof-fixture <public-visible-proof.json>"
    )

    if proof_approved:
        decision = "visible_session_turn_candidate"
        next_driver_action = "run_visible_session_turn_after_quota_and_idle_guard"
        recommended_command = proof_payload.get("recommended_command") if isinstance(proof_payload, dict) else None
        if not recommended_command:
            recommended_command = "use the proven visible Codex CLI surface; do not read transcripts or hidden session files"
    elif driver_mode in {"session_attached_visible_turn", "visible_resume_or_remote_control_spike"}:
        decision = "visible_session_proof_required"
        next_driver_action = "capture_public_safe_visible_session_proof"
        recommended_command = proof_command
    else:
        decision = "tui_bootstrap_only"
        next_driver_action = "ask_user_to_start_inside_codex_cli_tui"
        recommended_command = commands.get("tui_bootstrap_message")

    driver_steps = [
        "run quota_guard and stop if user_channel.action_required=true",
        "stop or relocate if workspace_guard blocks the current checkout",
        "use a visible session only when proof_approved=true and an idle guard passes",
        "do not use headless codex exec from the default LoopX setup-then-goal bootstrap path",
        "after the Codex turn, validate evidence or blocker before refresh-state",
        "spend quota exactly once after validated writeback, never for this packet alone",
    ]
    warnings = list(local_plan.get("warnings") or [])
    if allow_headless_fallback:
        warnings.append(
            "allow_headless_fallback was ignored because headless fallback is disabled for the default LoopX setup-then-goal bootstrap path."
        )

    return {
        "ok": True,
        "schema_version": "codex_cli_visible_driver_run_packet_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "codex_bin": codex_bin,
        "driver_phase": "run_packet_no_execution",
        "driver_mode": driver_mode,
        "decision": decision,
        "next_driver_action": next_driver_action,
        "recommended_command": recommended_command,
        "allow_headless_fallback": allow_headless_fallback,
        "visible_session_proof": {
            "supplied": proof is not None,
            "approved": proof_approved,
            "decision": proof.get("decision") if proof else None,
            "failures": proof.get("failures") if proof else [],
        },
        "local_driver_plan": {
            "schema_version": local_plan.get("schema_version"),
            "driver_mode": local_plan.get("driver_mode"),
            "decision": local_plan.get("decision"),
            "operator_instruction": local_plan.get("operator_instruction"),
        },
        "commands": {
            "quota_guard": commands.get("quota_guard"),
            "tui_bootstrap_message": commands.get("tui_bootstrap_message"),
            "visible_session_proof": proof_command,
            "explicit_headless_fallback": None,
            "headless_fallback_disabled": commands.get("headless_fallback_disabled"),
        },
        "driver_steps": driver_steps,
        "execution_policy": {
            "tui_bootstrap_primary": True,
            "same_session_attachment_requires_visible_proof": True,
            "headless_execution_disabled": True,
            "quota_guard_required": True,
            "idle_guard_required_before_visible_prompt": True,
            "spend_after_validated_writeback_only": True,
        },
        "boundary": {
            "run_packet_only": True,
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
            "requires_user_gate_stop": True,
            "headless_execution_disabled": True,
        },
        "warnings": warnings,
    }


SchedulerCommandRunner = Callable[..., dict[str, Any]]


def build_codex_cli_local_scheduler_tick(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
    probe_payload: dict[str, Any],
    quota_payload: dict[str, Any] | None = None,
    proof_payload: dict[str, Any] | None = None,
    idle_payload: dict[str, Any] | None = None,
    allow_headless_fallback: bool = False,
) -> dict[str, Any]:
    from .codex_cli_scheduler import build_codex_cli_local_scheduler_tick as _build

    return _build(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        probe_payload=probe_payload,
        quota_payload=quota_payload,
        proof_payload=proof_payload,
        idle_payload=idle_payload,
        allow_headless_fallback=allow_headless_fallback,
    )


def execute_codex_cli_local_scheduler_tick_result(
    tick_payload: dict[str, Any],
    *,
    execute_candidate: bool = False,
    execute_blocker_writeback: bool = False,
    guard_checked: bool = False,
    candidate_command_prefixes: list[str] | None = None,
    executor_timeout_seconds: float = DEFAULT_EXECUTOR_TIMEOUT_SECONDS,
    runner: SchedulerCommandRunner | None = None,
) -> dict[str, Any]:
    from .codex_cli_scheduler import execute_codex_cli_local_scheduler_tick_result as _execute

    return _execute(
        tick_payload,
        execute_candidate=execute_candidate,
        execute_blocker_writeback=execute_blocker_writeback,
        guard_checked=guard_checked,
        candidate_command_prefixes=candidate_command_prefixes,
        executor_timeout_seconds=executor_timeout_seconds,
        runner=runner,
    )


def build_codex_cli_local_scheduler_executor(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
    probe_payload: dict[str, Any],
    quota_payload: dict[str, Any] | None = None,
    proof_payload: dict[str, Any] | None = None,
    idle_payload: dict[str, Any] | None = None,
    allow_headless_fallback: bool = False,
    execute_candidate: bool = False,
    execute_blocker_writeback: bool = False,
    guard_checked: bool = False,
    candidate_command_prefixes: list[str] | None = None,
    executor_timeout_seconds: float = DEFAULT_EXECUTOR_TIMEOUT_SECONDS,
    runner: SchedulerCommandRunner | None = None,
) -> dict[str, Any]:
    from .codex_cli_scheduler import build_codex_cli_local_scheduler_executor as _build

    return _build(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        probe_payload=probe_payload,
        quota_payload=quota_payload,
        proof_payload=proof_payload,
        idle_payload=idle_payload,
        allow_headless_fallback=allow_headless_fallback,
        execute_candidate=execute_candidate,
        execute_blocker_writeback=execute_blocker_writeback,
        guard_checked=guard_checked,
        candidate_command_prefixes=candidate_command_prefixes,
        executor_timeout_seconds=executor_timeout_seconds,
        runner=runner,
    )


def build_codex_cli_one_message_loop_pilot(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
    probe_payload: dict[str, Any],
    proof_payload: dict[str, Any] | None = None,
    idle_payload: dict[str, Any] | None = None,
    allow_headless_fallback: bool = False,
) -> dict[str, Any]:
    """Compose the first-message TUI path with the safe scheduler bridge."""

    bootstrap = build_codex_cli_bootstrap_message(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
    )
    resolved_project = str(bootstrap["project"])
    resolved_goal_id = str(bootstrap["goal_id"])
    agent_arg = f" --agent-id {_shell_arg(agent_id)}" if agent_id else ""
    common_args = (
        f"--project {_shell_arg(resolved_project)} "
        f"--goal-id {_shell_arg(resolved_goal_id)}{agent_arg} "
        f"--codex-bin {_shell_arg(codex_bin)}"
    )
    scheduler_executor = build_codex_cli_local_scheduler_executor(
        project=project,
        goal_id=resolved_goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        probe_payload=probe_payload,
        proof_payload=proof_payload,
        idle_payload=idle_payload,
        allow_headless_fallback=allow_headless_fallback,
        execute_candidate=False,
        execute_blocker_writeback=False,
    )
    scheduler_action = str(scheduler_executor.get("scheduler_action") or "")
    scheduler_tick = (
        scheduler_executor.get("scheduler_tick")
        if isinstance(scheduler_executor.get("scheduler_tick"), dict)
        else {}
    )
    scheduler_blocker = (
        scheduler_tick.get("precise_blocker")
        if isinstance(scheduler_tick.get("precise_blocker"), dict)
        else {}
    )
    scheduler_blocker_reason = str(scheduler_blocker.get("reason") or "")
    if scheduler_action == "external_visible_command_candidate":
        pilot_decision = "first_message_then_candidate_available"
        followup_mode = "local scheduler can show the candidate, but execution still requires guard and prefix opt-in"
    elif scheduler_action == "write_precise_blocker" and scheduler_blocker_reason.startswith("runtime_idle_"):
        pilot_decision = "first_message_then_runtime_idle_required"
        followup_mode = (
            "local scheduler must capture public-safe runtime idle observation before later visible automation"
        )
    elif scheduler_action == "write_precise_blocker":
        pilot_decision = "first_message_then_visible_blocker_writeback"
        followup_mode = "local scheduler can write the precise blocker after explicit guard-checked opt-in"
    else:
        pilot_decision = "first_message_tui_bootstrap_only"
        followup_mode = "keep the TUI bootstrap as the product path until a visible proof exists"

    bootstrap_command = (
        f"{_shell_arg(cli_bin)} codex-cli-bootstrap-message "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}{agent_arg}"
    )
    scheduler_exec_dry_run_command = (
        f"{_shell_arg(cli_bin)} codex-cli-local-scheduler-exec {common_args}"
        " --observe-local-runtime --observed-surface visible_resume_prompt "
        "--turn-state idle --probe-human-input-idle --checked-before-prompt "
        "--visible-to-user --user-can-interrupt --manual-takeover-available"
    )
    candidate_execute_template = (
        f"{scheduler_exec_dry_run_command} --guard-checked --execute-candidate "
        "--candidate-command-prefix <allowed-prefix>"
    )
    blocker_execute_template = (
        f"{scheduler_exec_dry_run_command} --guard-checked --execute-blocker-writeback"
    )
    return {
        "ok": True,
        "schema_version": "codex_cli_one_message_loop_pilot_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "codex_bin": codex_bin,
        "pilot_decision": pilot_decision,
        "followup_mode": followup_mode,
        "start_surface": "codex_cli_tui_one_message",
        "first_turn": {
            "user_action": "paste_bootstrap_message_into_codex_cli_tui",
            "autostarts_loopx_loop": True,
            "setup_then_loop_activation": True,
            "preserve_tui": True,
            "loop_activation": {
                "source_command": bootstrap.get("heartbeat_prompt_json_command"),
                "codex_cli": "/goal <thin task_body>",
                "codex_app": "<thin task_body> heartbeat automation",
            },
            "stop_only_for": [
                "concrete_user_gate",
                "workspace_guard",
                "missing_capability",
                "missing_installation_primitive",
                "unsafe_boundary",
            ],
            "message": bootstrap.get("message"),
            "snapshot_required": [
                "current goal id",
                "concrete user gate or none",
                "top user todo or none",
                "top agent todo",
                "next safe action",
            ],
        },
        "later_turn_contract": {
            "preserve_visible_tui": True,
            "visible_steering_requires": [
                "public_safe_visible_session_proof",
                "runtime_idle_evidence",
                "fresh_quota_guard",
                "guard_checked",
                "explicit_execution_bounds",
            ],
            "default_without_proof": "write_blocker_or_keep_tui_bootstrap_primary",
        },
        "automation_bridge": {
            "command": "codex-cli-local-scheduler-exec",
            "default_executes": False,
            "scheduler_action": scheduler_action,
            "executor_reason": (scheduler_executor.get("execution") or {}).get("reason")
            if isinstance(scheduler_executor.get("execution"), dict)
            else None,
            "followup_mode": followup_mode,
        },
        "commands": {
            "bootstrap_message": bootstrap_command,
            "scheduler_exec_dry_run": scheduler_exec_dry_run_command,
            "scheduler_exec_candidate_template": candidate_execute_template,
            "scheduler_exec_blocker_template": blocker_execute_template,
        },
        "bootstrap_message": {
            "schema_version": bootstrap.get("schema_version"),
            "invocation_mode": bootstrap.get("invocation_mode"),
            "heartbeat_prompt_json_command": bootstrap.get("heartbeat_prompt_json_command"),
            "quota_guard_command": bootstrap.get("quota_guard_command"),
            "progress_refresh_command": bootstrap.get("progress_refresh_command"),
            "refresh_command": bootstrap.get("refresh_command"),
            "quota_spend_command": bootstrap.get("quota_spend_command"),
        },
        "scheduler_executor": scheduler_executor,
        "boundary": {
            "pilot_packet_only": True,
            "runs_codex": False,
            "runs_scheduler_result": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
            "requires_user_visible_start": True,
            "headless_execution_disabled": True,
            "candidate_execution_requires_guard_and_prefix": True,
        },
        "warnings": list(scheduler_executor.get("warnings") or []),
    }


def build_codex_cli_visible_local_driver_pilot(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
    probe_payload: dict[str, Any],
    proof_payload: dict[str, Any] | None = None,
    idle_payload: dict[str, Any] | None = None,
    allow_headless_fallback: bool = False,
) -> dict[str, Any]:
    """Prototype the visible local driver loop without executing Codex.

    This composes the first visible TUI bootstrap, a no-execution scheduler
    wrapper, and the public-safe proof boundary a returning user would need
    before LoopX can steer later visible turns.
    """

    one_message = build_codex_cli_one_message_loop_pilot(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        codex_bin=codex_bin,
        probe_payload=probe_payload,
        proof_payload=proof_payload,
        idle_payload=idle_payload,
        allow_headless_fallback=allow_headless_fallback,
    )
    scheduler_executor = (
        one_message.get("scheduler_executor")
        if isinstance(one_message.get("scheduler_executor"), dict)
        else {}
    )
    scheduler_tick = (
        scheduler_executor.get("scheduler_tick")
        if isinstance(scheduler_executor.get("scheduler_tick"), dict)
        else {}
    )
    execution = (
        scheduler_executor.get("execution")
        if isinstance(scheduler_executor.get("execution"), dict)
        else {}
    )
    resolved_project = str(one_message["project"])
    resolved_goal_id = str(one_message["goal_id"])
    scheduler_action = str(scheduler_executor.get("scheduler_action") or "")
    scheduler_blocker = (
        scheduler_tick.get("precise_blocker")
        if isinstance(scheduler_tick.get("precise_blocker"), dict)
        else {}
    )
    scheduler_blocker_reason = str(scheduler_blocker.get("reason") or "")
    proof = (
        scheduler_tick.get("visible_session_proof")
        if isinstance(scheduler_tick.get("visible_session_proof"), dict)
        else {}
    )
    proof_approved = bool(proof.get("approved") is True)
    idle_detector = build_codex_cli_runtime_idle_detector(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        idle_payload=idle_payload,
    )
    idle_approved = bool(idle_detector.get("approved_for_visible_later_turn") is True)
    idle_detector_command = (
        f"{_shell_arg(cli_bin)} codex-cli-runtime-idle-detector "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
        f"{' --agent-id ' + _shell_arg(agent_id) if agent_id else ''} "
        "--observe-local-runtime --observed-surface visible_resume_prompt "
        "--turn-state idle --probe-human-input-idle --checked-before-prompt "
        "--visible-to-user --user-can-interrupt --manual-takeover-available"
    )
    idle_fixture_detector_command = (
        f"{_shell_arg(cli_bin)} codex-cli-runtime-idle-detector "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
        f"{' --agent-id ' + _shell_arg(agent_id) if agent_id else ''} "
        "--idle-fixture <public-runtime-idle.json>"
    )

    if proof_approved and scheduler_action == "external_visible_command_candidate" and idle_approved:
        loop_decision = "visible_candidate_ready_for_guarded_execution"
        next_driver_action = "run_scheduler_exec_candidate_after_fresh_guard_and_prefix"
    elif proof_approved and (
        scheduler_action == "external_visible_command_candidate"
        or scheduler_blocker_reason.startswith("runtime_idle_")
    ):
        loop_decision = "runtime_idle_detector_required"
        next_driver_action = "capture_public_safe_runtime_idle_observation"
    elif scheduler_action == "write_precise_blocker":
        loop_decision = "visible_loop_blocker_writeback_ready"
        next_driver_action = "write_precise_blocker_after_fresh_guard"
    elif scheduler_action == "surface_tui_bootstrap":
        loop_decision = "surface_tui_bootstrap_only"
        next_driver_action = "keep_first_message_tui_path_visible"
    else:
        loop_decision = "review_scheduler_packet_before_execution"
        next_driver_action = "inspect_scheduler_executor_packet"

    commands = one_message.get("commands") if isinstance(one_message.get("commands"), dict) else {}
    scheduler_commands = (
        scheduler_executor.get("commands")
        if isinstance(scheduler_executor.get("commands"), dict)
        else {}
    )
    visible_loop_steps = [
        "start from the visible Codex CLI TUI with the one-message bootstrap",
        "run quota should-run with the registered agent id before each scheduler tick",
        "stop on interaction_contract.user_channel.action_required=true and show the concrete user todo",
        "require public-safe local idle observation or a fixture before any visible resume, remote-control, or same-TUI prompt",
        "run codex-cli-local-scheduler-exec as dry-run unless guard and explicit execution flags are present",
        "for a visible candidate, require guard_checked plus an allowed candidate command prefix",
        "for a blocker, write compact LoopX state only after guard_checked",
        "never read raw transcripts, session files, credentials, stdout, or stderr for this pilot",
    ]

    return {
        "ok": True,
        "schema_version": "codex_cli_visible_local_driver_pilot_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "codex_bin": codex_bin,
        "pilot_phase": "visible_local_driver_loop_no_execution",
        "start_surface": "codex_cli_tui_one_message",
        "loop_decision": loop_decision,
        "next_driver_action": next_driver_action,
        "one_message_pilot": {
            "schema_version": one_message.get("schema_version"),
            "pilot_decision": one_message.get("pilot_decision"),
            "start_surface": one_message.get("start_surface"),
            "followup_mode": one_message.get("followup_mode"),
        },
        "scheduler_executor": {
            "schema_version": scheduler_executor.get("schema_version"),
            "scheduler_action": scheduler_action,
            "decision": scheduler_executor.get("decision"),
            "executor_reason": execution.get("reason"),
            "executed": execution.get("executed"),
        },
        "visible_session_proof": {
            "supplied": bool(proof.get("supplied")),
            "approved": proof_approved,
            "decision": proof.get("decision"),
            "failures": proof.get("failures") or [],
        },
        "runtime_idle_detector": {
            "supplied": idle_payload is not None,
            "approved": idle_approved,
            "decision": idle_detector.get("decision"),
            "failures": idle_detector.get("failures") or [],
            "command": idle_detector_command,
        },
        "idle_guard_contract": {
            "required_before_visible_prompt": True,
            "fixture_keys": [
                "idle_guard.no_active_human_typing",
                "idle_guard.no_running_turn",
                "idle_guard.checked_before_prompt",
                "turn_visibility.visible_to_user",
                "interruptibility.user_can_interrupt",
                "interruptibility.manual_takeover_available",
            ],
            "current_pilot_implements_runtime_idle_detection": True,
            "fixture_backed_runtime_idle_detector": True,
            "runtime_sensor_implemented": True,
            "public_safe_fixture_supported": True,
            "local_observation_adapter_supported": True,
            "no_private_state_observation": True,
        },
        "visible_loop_steps": visible_loop_steps,
        "commands": {
            "bootstrap_message": commands.get("bootstrap_message"),
            "scheduler_exec_dry_run": commands.get("scheduler_exec_dry_run"),
            "scheduler_exec_candidate_template": commands.get("scheduler_exec_candidate_template"),
            "scheduler_exec_blocker_template": commands.get("scheduler_exec_blocker_template"),
            "scheduler_tick": scheduler_commands.get("scheduler_tick"),
            "candidate_command": scheduler_commands.get("candidate_command"),
            "blocker_writeback": scheduler_commands.get("blocker_writeback"),
            "runtime_idle_detector": idle_detector_command,
            "runtime_idle_detector_fixture": idle_fixture_detector_command,
        },
        "execution_policy": {
            "tui_bootstrap_primary": True,
            "later_turns_visible_to_user": True,
            "user_can_interrupt_or_take_over": True,
            "same_session_attachment_requires_visible_proof": True,
            "headless_execution_disabled": True,
            "quota_guard_required_each_tick": True,
            "spend_after_validated_writeback_only": True,
        },
        "boundary": {
            "pilot_packet_only": True,
            "runs_codex": False,
            "runs_scheduler_result": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "mutates_codex_session": False,
            "writes_loopx_state": False,
            "spends_loopx_quota": False,
            "requires_fresh_guard_before_execution": True,
            "candidate_execution_requires_guard_and_prefix": True,
            "blocker_writeback_requires_guard_checked": True,
            "headless_execution_disabled": True,
        },
        "warnings": [
            *list(one_message.get("warnings") or []),
            *(
                ["Runtime idle detector must pass before a later visible Codex CLI turn can run."]
                if proof_approved and not idle_approved
                else []
            ),
        ],
    }


def build_codex_cli_visible_first_response_capture_plan(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    first_response_path: str = "public-first-response.json",
    idle_path: str = "public-runtime-idle.json",
) -> dict[str, Any]:
    """Describe the safest public fixture capture path for visible TUI bootstrap.

    This is deliberately a plan packet. It does not start Codex, read terminal
    buffers, inspect session files, or write fixtures. A human-visible TUI run
    supplies the observations, and the bounded visible adapter validates the
    resulting public-safe JSON before any success claim or quota spend.
    """

    adapter = build_codex_cli_bounded_visible_pilot_adapter(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
    )
    resolved_project = str(adapter["project"])
    resolved_goal_id = str(adapter["goal_id"])
    agent_arg = f" --agent-id {_shell_arg(agent_id)}" if agent_id else ""
    first_response_fixture = {
        "observed_surface": "codex_cli_tui_visible_window",
        "prompt_delivery": {
            "manual_or_visible_delivery": True,
            "prompt_public_safe": True,
            "argv_prompt_used": False,
        },
        "first_response": {
            "goal_id_visible": True,
            "user_gate_or_none_visible": True,
            "top_user_todo_or_none_visible": True,
            "top_agent_todo_visible": True,
            "next_safe_action_visible": True,
            "bounded_segment_started_or_blocker_written": True,
        },
        "interruptibility": {
            "user_can_interrupt": True,
            "manual_takeover_available": True,
        },
        "writeback": {
            "compact_evidence_planned": True,
            "quota_spend_after_writeback_only": True,
        },
        "boundary": {
            "reads_raw_transcripts": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "reads_credentials": False,
            "mutates_hidden_session_state": False,
            "spends_quota_before_writeback": False,
        },
    }
    runtime_idle_fixture = {
        "observed_surface": "codex_cli_tui_visible_window",
        "idle_guard": {
            "no_active_human_typing": True,
            "no_running_turn": True,
            "checked_before_prompt": True,
        },
        "turn_visibility": {"visible_to_user": True},
        "interruptibility": {
            "user_can_interrupt": True,
            "manual_takeover_available": True,
        },
        "boundary": {
            "reads_raw_transcripts": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "reads_credentials": False,
            "mutates_hidden_session_state": False,
        },
    }
    first_response_checklist = [
        {
            "key": key,
            "required": True,
            "description": description,
            "path": ".".join(path),
        }
        for key, path, description in FIRST_RESPONSE_REQUIRED_TRUE_CHECKS
    ] + [
        {
            "key": key,
            "required": False,
            "description": description,
            "path": ".".join(path),
        }
        for key, path, description in FIRST_RESPONSE_REQUIRED_FALSE_CHECKS
    ]
    runtime_idle_checklist = [
        {
            "key": key,
            "required": True,
            "description": description,
            "path": ".".join(path),
        }
        for key, path, description in RUNTIME_IDLE_REQUIRED_TRUE_CHECKS
    ] + [
        {
            "key": key,
            "required": False,
            "description": description,
            "path": ".".join(path),
        }
        for key, path, description in RUNTIME_IDLE_REQUIRED_FALSE_CHECKS
    ]
    capture_steps = [
        "Run quota should-run for the goal and stop if a concrete user gate blocks this path.",
        "Open Codex CLI TUI yourself in the project repo; do not pass the bootstrap message as argv.",
        "Paste the generated LoopX bootstrap message into the visible TUI.",
        "Observe only whether the first response exposes the required public-safe fields; do not copy raw text.",
        f"Write those booleans to {first_response_path}.",
        "After the first response or blocker is visible, confirm the TUI is idle and the user is not typing.",
        f"Write those idle booleans to {idle_path}.",
        "Run the bounded visible pilot adapter with both fixtures before claiming success or spending quota.",
    ]
    stop_conditions = [
        "the first response contains private paths, internal project names, credentials, or raw logs",
        "the bootstrap message would have to be passed as argv",
        "the user is typing or Codex is already running a visible turn",
        "the response does not show goal/todo/gate/next-action status clearly enough to fill the fixture",
        "any required boundary boolean would be false",
    ]
    commands = {
        "quota_guard": (
            f"{_shell_arg(cli_bin)} --format json quota should-run "
            f"--goal-id {_shell_arg(resolved_goal_id)}{agent_arg}"
            f"{CODEX_CLI_SCHEDULER_ARGS}"
        ),
        "bootstrap_message": (
            f"{_shell_arg(cli_bin)} codex-cli-bootstrap-message "
            f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
            f"{agent_arg} --message-only"
        ),
        "runtime_idle_detector": (
            f"{_shell_arg(cli_bin)} codex-cli-runtime-idle-detector "
            f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
            f"{agent_arg} --idle-fixture {_shell_arg(idle_path)}"
        ),
        "bounded_visible_pilot_adapter": (
            f"{_shell_arg(cli_bin)} codex-cli-bounded-visible-pilot-adapter "
            f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
            f"{agent_arg} --first-response-fixture {_shell_arg(first_response_path)} "
            f"--idle-fixture {_shell_arg(idle_path)}"
        ),
        "capture_plan": (
            f"{_shell_arg(cli_bin)} codex-cli-visible-first-response-capture-plan "
            f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
            f"{agent_arg} --first-response-path {_shell_arg(first_response_path)} "
            f"--idle-path {_shell_arg(idle_path)}"
        ),
    }
    return {
        "ok": True,
        "schema_version": "codex_cli_visible_first_response_capture_plan_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "decision": "manual_visible_capture_plan_ready",
        "start_surface": "codex_cli_tui_manual_paste",
        "next_safe_step": "paste the bootstrap message into a visible Codex CLI TUI and record only public-safe fixture booleans",
        "output_artifacts": {
            "first_response_fixture": first_response_path,
            "runtime_idle_fixture": idle_path,
        },
        "capture_steps": capture_steps,
        "stop_conditions": stop_conditions,
        "first_response_checklist": first_response_checklist,
        "runtime_idle_checklist": runtime_idle_checklist,
        "sample_first_response_fixture": first_response_fixture,
        "sample_runtime_idle_fixture": runtime_idle_fixture,
        "commands": commands,
        "adapter_decision_without_fixtures": adapter.get("decision"),
        "boundary": {
            "capture_plan_only": True,
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "reads_credentials": False,
            "mutates_codex_session": False,
            "writes_loopx_state": False,
            "spends_loopx_quota": False,
            "requires_visible_delivery": True,
            "manual_paste_primary": True,
            "argv_prompt_rejected": True,
            "success_claim_requires_bounded_adapter": True,
        },
    }


def build_codex_cli_bounded_visible_pilot_adapter(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    first_response_payload: dict[str, Any] | None = None,
    idle_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the bounded evidence needed before claiming live TUI success.

    The adapter is intentionally a packet builder and fixture validator. It
    does not start Codex, inspect transcripts, read session files, or capture
    stdout/stderr. A separate visible/manual run must supply a public-safe
    first-response fixture and a runtime-idle fixture before this packet can
    approve a live-TUI first-message success claim.
    """

    bootstrap = build_codex_cli_bootstrap_message(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
    )
    resolved_project = str(bootstrap["project"])
    resolved_goal_id = str(bootstrap["goal_id"])
    agent_arg = f" --agent-id {_shell_arg(agent_id)}" if agent_id else ""
    required_first_response_shape = {
        "observed_surface": "codex_cli_tui_visible_window | visible_paste_adapter",
        "prompt_delivery": {
            "manual_or_visible_delivery": True,
            "prompt_public_safe": True,
            "argv_prompt_used": False,
        },
        "first_response": {
            "goal_id_visible": True,
            "user_gate_or_none_visible": True,
            "top_user_todo_or_none_visible": True,
            "top_agent_todo_visible": True,
            "next_safe_action_visible": True,
            "bounded_segment_started_or_blocker_written": True,
        },
        "interruptibility": {
            "user_can_interrupt": True,
            "manual_takeover_available": True,
        },
        "writeback": {
            "compact_evidence_planned": True,
            "quota_spend_after_writeback_only": True,
        },
        "boundary": {
            "reads_raw_transcripts": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "reads_credentials": False,
            "mutates_hidden_session_state": False,
            "spends_quota_before_writeback": False,
        },
    }

    first_response_checks: list[dict[str, Any]] = []
    first_response_failures: list[str] = []
    observed_surface = "missing"
    if first_response_payload is None:
        first_response_failures.append("missing_first_response_evidence")
    else:
        observed_surface = str(first_response_payload.get("observed_surface") or "unknown")
        for key, path, description in FIRST_RESPONSE_REQUIRED_TRUE_CHECKS:
            passed = _nested_bool(first_response_payload, path)
            first_response_checks.append(
                {"key": key, "required": True, "passed": passed, "description": description}
            )
            if not passed:
                first_response_failures.append(key)
        for key, path, description in FIRST_RESPONSE_REQUIRED_FALSE_CHECKS:
            passed = _nested_false(first_response_payload, path)
            first_response_checks.append(
                {"key": key, "required": False, "passed": passed, "description": description}
            )
            if not passed:
                first_response_failures.append(key)
        supported_surface = observed_surface in {
            "codex_cli_tui_visible_window",
            "visible_paste_adapter",
        }
        first_response_checks.append(
            {
                "key": "supported_observed_surface",
                "required": sorted(["codex_cli_tui_visible_window", "visible_paste_adapter"]),
                "actual": observed_surface,
                "passed": supported_surface,
                "description": "first response was observed on a visible Codex CLI surface",
            }
        )
        if not supported_surface:
            first_response_failures.append("unsupported_observed_surface")
        if _nested_bool(first_response_payload, ("prompt_delivery", "argv_prompt_used")):
            first_response_failures.append("argv_prompt_leakage_risk")

    idle_detector = build_codex_cli_runtime_idle_detector(
        project=project,
        goal_id=resolved_goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
        idle_payload=idle_payload,
    )
    idle_approved = idle_detector.get("approved_for_visible_later_turn") is True
    first_response_approved = bool(first_response_payload is not None and not first_response_failures)
    blockers: list[str] = []
    blockers.extend(first_response_failures)
    if not idle_approved:
        blockers.extend(idle_detector.get("failures") or ["runtime_idle_evidence_required"])

    if first_response_approved and idle_approved:
        decision = "bounded_visible_pilot_ready_for_success_claim"
        approved = True
        next_safe_step = (
            "write compact success evidence, refresh state with outcome_progress, "
            "then spend exactly one quota slot"
        )
    elif first_response_payload is None:
        decision = "bounded_visible_completion_evidence_required"
        approved = False
        next_safe_step = (
            "capture the first visible TUI response as the public-safe fixture shape "
            "below, then rerun this adapter before spending quota"
        )
    elif not first_response_approved:
        decision = "bounded_visible_first_response_incomplete"
        approved = False
        next_safe_step = (
            "treat the live TUI pilot as blocked and write the precise blocker; "
            "do not claim first-message success"
        )
    else:
        decision = "bounded_visible_runtime_idle_required"
        approved = False
        next_safe_step = (
            "capture runtime idle evidence proving no active typing or running turn "
            "before marking the visible pilot complete"
        )

    bounded_adapter_command = (
        f"{_shell_arg(cli_bin)} codex-cli-bounded-visible-pilot-adapter "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
        f"{agent_arg} --first-response-fixture <public-first-response.json> "
        "--idle-fixture <public-runtime-idle.json>"
    )
    runtime_idle_command = (
        f"{_shell_arg(cli_bin)} codex-cli-runtime-idle-detector "
        f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
        f"{agent_arg} --idle-fixture <public-runtime-idle.json>"
    )
    blocker_summary = blockers[0] if blockers else "none"
    blocker_writeback = (
        f"{_shell_arg(cli_bin)} refresh-state --goal-id {_shell_arg(resolved_goal_id)} "
        "--classification codex_cli_bounded_visible_pilot_blocker "
        "--delivery-batch-scale implementation --delivery-outcome outcome_gap "
        f"--recommended-action {_shell_arg('Codex CLI bounded visible pilot blocked: ' + blocker_summary)}"
    )
    success_writeback = (
        f"{_shell_arg(cli_bin)} refresh-state --goal-id {_shell_arg(resolved_goal_id)} "
        "--classification codex_cli_bounded_visible_pilot_success "
        "--delivery-batch-scale implementation --delivery-outcome outcome_progress "
        "--recommended-action "
        f"{_shell_arg('Promote Codex CLI one-message TUI bootstrap only after documented release-path validation.')}"
    )

    return {
        "ok": True,
        "schema_version": "codex_cli_bounded_visible_pilot_adapter_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "decision": decision,
        "approved_for_live_tui_success_claim": approved,
        "observed_surface": observed_surface,
        "next_safe_step": next_safe_step,
        "blockers": blockers,
        "first_response": {
            "supplied": first_response_payload is not None,
            "approved": first_response_approved,
            "checks": first_response_checks,
            "failures": first_response_failures,
        },
        "runtime_idle_detector": {
            "supplied": idle_payload is not None,
            "approved": idle_approved,
            "decision": idle_detector.get("decision"),
            "failures": idle_detector.get("failures") or [],
            "source": idle_detector.get("source"),
        },
        "commands": {
            "bootstrap_message": (
                f"{_shell_arg(cli_bin)} codex-cli-bootstrap-message "
                f"--project {_shell_arg(resolved_project)} --goal-id {_shell_arg(resolved_goal_id)}"
                f"{agent_arg} --message-only"
            ),
            "bounded_visible_pilot_adapter": bounded_adapter_command,
            "runtime_idle_detector": runtime_idle_command,
            "blocker_writeback": blocker_writeback,
            "success_writeback": success_writeback,
        },
        "required_first_response_shape": required_first_response_shape,
        "required_runtime_idle_shape": idle_detector.get("required_fixture_shape"),
        "boundary": {
            "adapter_packet_only": True,
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "reads_stdout_stderr": False,
            "mutates_codex_session": False,
            "writes_loopx_state": False,
            "spends_loopx_quota": False,
            "requires_visible_delivery": True,
            "argv_prompt_rejected": True,
            "success_claim_requires_first_response_and_idle": True,
        },
    }
