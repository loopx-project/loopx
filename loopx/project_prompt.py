from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from .bootstrap import default_goal_id
from .control_plane.projects.registry_codec import load_registry
from .paths import (
    registered_goal_state_file,
    rel_or_abs,
    resolve_runtime_root,
    shell_selected_global_registry,
)
from .control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    SchedulerRuntimeProfile,
    render_scheduler_execution_args,
    scheduler_runtime_profile_for_execution_context,
)
from .control_plane.todos.contract import normalize_required_capabilities
from .install_contract import NO_CLONE_INSTALL_URL

DEFAULT_HANDOFF_OBJECTIVE = "<OBJECTIVE_FROM_GOAL_DOC>"
DEFAULT_HANDOFF_DOMAIN = "<DOMAIN>"
DEFAULT_HANDOFF_ADAPTER_KIND = "read_only_project_map_v0"
DEFAULT_HANDOFF_ADAPTER_STATUS = "connected-read-only"
DEFAULT_HANDOFF_NEXT_PROBE = "(omit --next-probe until a read-only pre-tick command exists)"
CODEX_CLI_VISIBLE_SCHEDULER_CONTEXT = {
    "host_surface": "codex_cli",
    "scheduler_owner": "agent_cli_loop",
    "execution_mode": "interactive",
}


def shell_arg(value: str) -> str:
    return shlex.quote(value)


def render_optional_cli_arg(flag: str, value: str | None) -> str:
    if not value:
        return ""
    return f" {flag} {shell_arg(value)}"


def render_cli_command_prefix(
    *,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
) -> str:
    prefix = shell_arg(cli_bin)
    if runtime_root is not None:
        prefix += f" --runtime-root {shell_arg(str(runtime_root))}"
    return prefix


def _render_global_registry_arg(runtime_root: str | Path | None) -> str:
    if runtime_root is not None:
        return ""
    return f"--registry {shell_selected_global_registry()} "


def render_goal_start_bootstrap_command(
    *,
    project: str,
    goal_id: str,
    goal_text: str | None,
    cli_bin: str,
    runtime_root: str | None,
    fine_grained: bool,
    display_name: str | None = None,
) -> str:
    objective = goal_text or "<exact /loopx goal text>"
    lines = [
        f"cd {shell_arg(project)}",
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} bootstrap \\",
        "  --project . \\",
        f"  --goal-id {shell_arg(goal_id)} \\",
        f"  --objective {shell_arg(objective)} \\",
        f"  --adapter-kind {shell_arg(DEFAULT_HANDOFF_ADAPTER_KIND)} \\",
        f"  --adapter-status {shell_arg(DEFAULT_HANDOFF_ADAPTER_STATUS)}",
    ]
    if display_name:
        lines.insert(-1, f"  --display-name {shell_arg(display_name)} \\")
    if fine_grained:
        lines[-1] += " \\"
        lines.append("  --fine-grained")
    return "\n".join(lines)


def render_register_agent_command(
    goal_id: str,
    *,
    agent_id: str,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
) -> str:
    return (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} "
        f"register-agent --goal-id {shell_arg(goal_id)} "
        f"--agent-id {shell_arg(agent_id)} --require-new"
    )


def shell_arg_or_placeholder(value: str) -> str:
    text = str(value)
    if text.startswith("<") and text.endswith(">"):
        return text
    return shell_arg(text)


def render_available_capability_args(values: Any) -> str:
    return "".join(
        f" --available-capability {shell_arg(capability)}"
        for capability in normalize_required_capabilities(values)
    )


def render_cli_preflight(*, cli_bin: str = "loopx") -> str:
    cli_bin_arg = shell_arg(cli_bin)
    return f"""export PATH="$HOME/.local/bin:$PATH"
install_script="$HOME/loopx/scripts/install-local.sh"
if ! command -v {cli_bin_arg} >/dev/null 2>&1; then
  if [ -x "$install_script" ]; then
    "$install_script"
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "loopx is not on PATH; clone the LoopX repo and run scripts/install-local.sh" >&2
    exit 1
  fi
fi
{cli_bin_arg} doctor >/dev/null"""


def render_codex_cli_install_preflight(
    *,
    cli_bin: str = "loopx",
    doctor_agent_type: str | None = None,
) -> str:
    cli_bin_arg = shell_arg(cli_bin)
    doctor_agent_arg = (
        f" --agent-type {shell_arg(doctor_agent_type)}"
        if doctor_agent_type
        else ""
    )
    return f"""export PATH="$HOME/.local/bin:$PATH"
if ! command -v {cli_bin_arg} >/dev/null 2>&1; then
  if command -v python3 >/dev/null 2>&1 && python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install --upgrade loopx
    if command -v {cli_bin_arg} >/dev/null 2>&1; then
      {cli_bin_arg} workflow-skills --install --cli-bin {cli_bin_arg}
    fi
  fi
  if ! command -v {cli_bin_arg} >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    curl -fsSL {NO_CLONE_INSTALL_URL} | bash
    export PATH="$HOME/.local/bin:$PATH"
  fi
  if ! command -v {cli_bin_arg} >/dev/null 2>&1; then
    echo "loopx is not on PATH; use a Python 3.11+ environment, the archive fallback, or a contributor checkout" >&2
    exit 1
  fi
fi
{cli_bin_arg} doctor{doctor_agent_arg} >/dev/null"""


def render_quota_guard_command(
    goal_id: str,
    *,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
    agent_id: str | None = None,
    available_capabilities: Any = None,
    runtime_profile: str | None = None,
    scheduler_execution_context: dict[str, Any] | None = None,
    heartbeat_turn_receipt: bool = False,
    begin_turn: bool = False,
    include_shared_registry: bool = True,
) -> str:
    if not heartbeat_turn_receipt and agent_id and (
        runtime_profile == SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE.value
        or (
            runtime_profile is None
            and scheduler_runtime_profile_for_execution_context(
                scheduler_execution_context
            )
            is SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE
        )
    ):
        begin_turn = True
    if heartbeat_turn_receipt and begin_turn:
        raise ValueError(
            "quota guard cannot both begin a Turn and reuse a heartbeat Turn identity"
        )
    agent_arg = f" --agent-id {shell_arg(agent_id)}" if agent_id else ""
    capability_args = render_available_capability_args(available_capabilities)
    scheduler_args = render_scheduler_execution_args(
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
    )
    if heartbeat_turn_receipt:
        turn_arg = ' --turn-instance-id "${LOOPX_TURN:?}"'
    elif begin_turn:
        turn_arg = " --begin-turn"
    else:
        turn_arg = ""
    registry_arg = (
        _render_global_registry_arg(runtime_root) if include_shared_registry else ""
    )
    return (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} --format json "
        f"{registry_arg}"
        f"quota should-run --goal-id {shell_arg(goal_id)}{agent_arg}"
        f"{capability_args}{scheduler_args}{turn_arg}"
    )


def render_quota_spend_command(
    goal_id: str,
    *,
    source: str = "adapter",
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
    agent_id: str | None = None,
    available_capabilities: Any = None,
) -> str:
    agent_arg = f" --agent-id {shell_arg(agent_id)}" if agent_id else ""
    capability_args = render_available_capability_args(available_capabilities)
    return (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} --format json "
        f"{_render_global_registry_arg(runtime_root)}"
        "quota spend-slot "
        f"--goal-id {shell_arg(goal_id)} "
        f"--slots 1 --source {shell_arg(source)} --execute{agent_arg}{capability_args}"
    )


def render_refresh_state_command(
    goal_id: str,
    *,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
    project: str | None = None,
    agent_id: str | None = None,
    progress_scope: str | None = None,
    classification: str | None = None,
    delivery_batch_scale: str | None = None,
    delivery_outcome: str | None = None,
) -> str:
    project_arg = f" --project {shell_arg(project)}" if project else ""
    agent_arg = f" --agent-id {shell_arg(agent_id)}" if agent_id else ""
    scope_arg = f" --progress-scope {shell_arg(progress_scope)}" if progress_scope else ""
    classification_arg = (
        f" --classification {shell_arg_or_placeholder(classification)}"
        if classification
        else ""
    )
    scale_arg = (
        f" --delivery-batch-scale {shell_arg_or_placeholder(delivery_batch_scale)}"
        if delivery_batch_scale
        else ""
    )
    outcome_arg = (
        f" --delivery-outcome {shell_arg_or_placeholder(delivery_outcome)}"
        if delivery_outcome
        else ""
    )
    return (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} "
        f"refresh-state --goal-id {shell_arg(goal_id)}"
        f"{project_arg}{classification_arg}{scale_arg}{outcome_arg}{agent_arg}{scope_arg}"
    )


def render_accountable_progress_refresh_command(
    goal_id: str,
    *,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
    agent_id: str | None = None,
    progress_scope: str | None = None,
    classification: str = "<PUBLIC_SAFE_PROGRESS_CLASSIFICATION>",
    delivery_batch_scale: str = "<ACTUAL_DELIVERY_BATCH_SCALE>",
    delivery_outcome: str = "<ACTUAL_DELIVERY_OUTCOME>",
) -> str:
    return render_refresh_state_command(
        goal_id,
        cli_bin=cli_bin,
        runtime_root=runtime_root,
        agent_id=agent_id,
        progress_scope=progress_scope,
        classification=classification,
        delivery_batch_scale=delivery_batch_scale,
        delivery_outcome=delivery_outcome,
    )


def render_heartbeat_prompt_command(
    goal_id: str,
    *,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
    agent_id: str | None = None,
    agent_scope: str = "Codex CLI /goal visible TUI loop",
    body: str = "thin",
    available_capabilities: Any = None,
    runtime_profile: str | None = None,
    scheduler_execution_context: dict[str, Any] | None = None,
    visible_goal_host: str | None = None,
) -> str:
    agent_arg = f" --agent-id {shell_arg(agent_id)}" if agent_id else ""
    scope_arg = f" --agent-scope {shell_arg(agent_scope)}" if agent_id else ""
    capability_args = render_available_capability_args(available_capabilities)
    scheduler_args = render_scheduler_execution_args(
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
    )
    visible_goal_arg = (
        f" --visible-goal-host {shell_arg(visible_goal_host)}"
        if visible_goal_host
        else ""
    )
    return (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} "
        f"heartbeat-prompt --{shell_arg(body)} "
        f"--goal-id {shell_arg(goal_id)}{agent_arg}{scope_arg}{capability_args}"
        f"{scheduler_args}{visible_goal_arg}"
    )


def render_heartbeat_prompt_json_command(
    goal_id: str,
    *,
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
    agent_id: str | None = None,
    agent_scope: str = "Codex CLI /goal visible TUI loop",
    body: str = "thin",
    available_capabilities: Any = None,
    runtime_profile: str | None = None,
    scheduler_execution_context: dict[str, Any] | None = None,
    visible_goal_host: str | None = None,
) -> str:
    agent_arg = f" --agent-id {shell_arg(agent_id)}" if agent_id else ""
    scope_arg = f" --agent-scope {shell_arg(agent_scope)}" if agent_id else ""
    capability_args = render_available_capability_args(available_capabilities)
    scheduler_args = render_scheduler_execution_args(
        runtime_profile=runtime_profile,
        scheduler_execution_context=scheduler_execution_context,
    )
    visible_goal_arg = (
        f" --visible-goal-host {shell_arg(visible_goal_host)}"
        if visible_goal_host
        else ""
    )
    return (
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} "
        f"--format json heartbeat-prompt --{shell_arg(body)} "
        f"--goal-id {shell_arg(goal_id)}{agent_arg}{scope_arg}{capability_args}"
        f"{scheduler_args}{visible_goal_arg}"
    )


def render_connect_command(
    *,
    project: str,
    goal_doc: str,
    goal_id: str,
    objective: str,
    domain: str,
    adapter_kind: str,
    adapter_status: str,
    next_probe: str | None,
    spawn_allowed: bool,
    allowed_domains: list[str],
    write_scope: list[str],
    cli_bin: str = "loopx",
    runtime_root: str | Path | None = None,
) -> str:
    lines = [
        f"cd {shell_arg(project)}",
        f"{render_cli_command_prefix(cli_bin=cli_bin, runtime_root=runtime_root)} connect \\",
        f"  --goal-id {shell_arg(goal_id)} \\",
        f"  --objective {shell_arg(objective)} \\",
        f"  --domain {shell_arg(domain)} \\",
        f"  --goal-doc {shell_arg(goal_doc)} \\",
        f"  --adapter-kind {shell_arg(adapter_kind)} \\",
        f"  --adapter-status {shell_arg(adapter_status)}",
    ]
    if next_probe:
        lines[-1] += " \\"
        lines.append(f"  --next-probe {shell_arg(next_probe)}")
    if spawn_allowed:
        lines[-1] += " \\"
        lines.append("  --spawn-allowed")
        for allowed_domain in allowed_domains:
            lines[-1] += " \\"
            lines.append(f"  --allowed-domain {shell_arg(allowed_domain)}")
        for scope in write_scope:
            lines[-1] += " \\"
            lines.append(f"  --write-scope {shell_arg(scope)}")
    return "\n".join(lines)


def build_new_project_prompt(
    *,
    project: Path,
    goal_doc: Path,
    goal_id: str | None,
    objective: str | None,
    domain: str | None,
    adapter_kind: str,
    adapter_status: str,
    next_probe: str | None,
    spawn_allowed: bool,
    allowed_domains: list[str] | None,
    write_scope: list[str] | None,
) -> dict[str, Any]:
    project_text = str(project.expanduser())
    goal_doc_text = str(goal_doc.expanduser())
    resolved_goal_id = goal_id or default_goal_id(project)
    resolved_objective = objective or DEFAULT_HANDOFF_OBJECTIVE
    resolved_domain = domain or DEFAULT_HANDOFF_DOMAIN
    resolved_next_probe = next_probe or DEFAULT_HANDOFF_NEXT_PROBE
    project_registry_path = project.expanduser() / ".loopx" / "registry.json"
    existing_registry = (
        load_registry(project_registry_path)
        if project_registry_path.is_file()
        else None
    )
    declared_runtime_root = (
        str(resolve_runtime_root(existing_registry, registry_path=project_registry_path))
        if isinstance(existing_registry, dict) and existing_registry.get("common_runtime_root")
        else None
    )
    active_state_path = rel_or_abs(
        registered_goal_state_file(project.expanduser(), resolved_goal_id, existing_registry),
        project.expanduser(),
    )
    allowed_domains = allowed_domains or []
    write_scope = write_scope or []
    connect_command = render_connect_command(
        project=project_text,
        goal_doc=goal_doc_text,
        goal_id=resolved_goal_id,
        objective=resolved_objective,
        domain=resolved_domain,
        adapter_kind=adapter_kind,
        adapter_status=adapter_status,
        next_probe=next_probe,
        spawn_allowed=spawn_allowed,
        allowed_domains=allowed_domains,
        write_scope=write_scope,
        cli_bin="loopx",
        runtime_root=declared_runtime_root,
    )
    quota_guard_command = render_quota_guard_command(
        resolved_goal_id,
        runtime_root=declared_runtime_root,
        scheduler_execution_context=(
            GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
        ),
    )
    quota_spend_command = render_quota_spend_command(resolved_goal_id, runtime_root=declared_runtime_root)
    refresh_command = render_refresh_state_command(resolved_goal_id, runtime_root=declared_runtime_root)
    progress_refresh_command = render_accountable_progress_refresh_command(
        resolved_goal_id, runtime_root=declared_runtime_root
    )
    prompt = render_prompt_text(
        project=project_text,
        goal_doc=goal_doc_text,
        goal_id=resolved_goal_id,
        active_state_path=active_state_path,
        objective=resolved_objective,
        domain=resolved_domain,
        adapter_kind=adapter_kind,
        adapter_status=adapter_status,
        next_probe=resolved_next_probe,
        cli_preflight=render_cli_preflight(),
        connect_command=connect_command,
        quota_guard_command=quota_guard_command,
        quota_spend_command=quota_spend_command,
        refresh_command=refresh_command,
        progress_refresh_command=progress_refresh_command,
        cli_bin=render_cli_command_prefix(runtime_root=declared_runtime_root),
        spawn_allowed=spawn_allowed,
        allowed_domains=allowed_domains,
        write_scope=write_scope,
    )
    return {
        "ok": True,
        "project": project_text,
        "goal_doc": goal_doc_text,
        "goal_id": resolved_goal_id,
        "objective": resolved_objective,
        "domain": resolved_domain,
        "adapter_kind": adapter_kind,
        "adapter_status": adapter_status,
        "next_probe": resolved_next_probe,
        "spawn_allowed": spawn_allowed,
        "allowed_domains": allowed_domains,
        "write_scope": write_scope,
        "connect_command": connect_command,
        "quota_guard_command": quota_guard_command,
        "quota_spend_command": quota_spend_command,
        "refresh_command": refresh_command,
        "progress_refresh_command": progress_refresh_command,
        "cli_preflight": render_cli_preflight(),
        "prompt": prompt,
    }


def render_codex_cli_bootstrap_connect_command(
    *,
    project: str,
    goal_id: str,
    cli_bin: str,
) -> str:
    return "\n".join(
        [
            f"cd {shell_arg(project)}",
            f"{shell_arg(cli_bin)} bootstrap \\",
            "  --project . \\",
            f"  --goal-id {shell_arg(goal_id)} \\",
            f"  --adapter-kind {shell_arg(DEFAULT_HANDOFF_ADAPTER_KIND)} \\",
            f"  --adapter-status {shell_arg(DEFAULT_HANDOFF_ADAPTER_STATUS)}",
        ]
    )


def build_codex_cli_bootstrap_message(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
) -> dict[str, Any]:
    resolved_project = str(project.expanduser())
    resolved_goal_id = goal_id or default_goal_id(project)
    connect_command = render_codex_cli_bootstrap_connect_command(
        project=resolved_project,
        goal_id=resolved_goal_id,
        cli_bin=cli_bin,
    )
    quota_guard_command = render_quota_guard_command(
        resolved_goal_id,
        cli_bin=cli_bin,
        agent_id=agent_id,
        scheduler_execution_context=CODEX_CLI_VISIBLE_SCHEDULER_CONTEXT,
    )
    quota_spend_command = render_quota_spend_command(
        resolved_goal_id,
        source="heartbeat",
        cli_bin=cli_bin,
        agent_id=agent_id,
    )
    heartbeat_prompt_command = render_heartbeat_prompt_command(
        resolved_goal_id,
        cli_bin=cli_bin,
        agent_id=agent_id,
        scheduler_execution_context=CODEX_CLI_VISIBLE_SCHEDULER_CONTEXT,
    )
    heartbeat_prompt_json_command = render_heartbeat_prompt_json_command(
        resolved_goal_id,
        cli_bin=cli_bin,
        agent_id=agent_id,
        scheduler_execution_context=CODEX_CLI_VISIBLE_SCHEDULER_CONTEXT,
    )
    install_repair_command = render_codex_cli_install_preflight(cli_bin=cli_bin)
    refresh_command = render_refresh_state_command(
        resolved_goal_id,
        cli_bin=cli_bin,
        agent_id=agent_id,
        progress_scope="agent_lane" if agent_id else None,
    )
    progress_refresh_command = render_accountable_progress_refresh_command(
        resolved_goal_id,
        cli_bin=cli_bin,
        agent_id=agent_id,
        progress_scope="agent_lane" if agent_id else None,
    )
    first_run_validation_checklist = [
        f"{cli_bin} doctor passed after PyPI install repair or an existing install",
        "repo bootstrap/connect completed conservatively or a concrete install/connect blocker was shown",
        f"thin heartbeat task_body generated from {cli_bin} heartbeat-prompt --thin, not hand-written",
        "host loop surface activated from the thin task_body: Codex CLI /goal or Codex App heartbeat automation initially at max(3 minutes, owner cadence floor), then following quota scheduler_hint",
        "setup was not reported complete from registry/quota identity alone; missing host-loop mutation was reported as a concrete gate",
        "quota/status guard checked with the registered agent id when available after bootstrap/connect",
        "current goal, concrete user gate or none, top todos, and next safe action shown in the TUI",
        "no raw Codex transcripts, session files, credentials, private paths, stdout, or stderr persisted",
    ]
    message = render_codex_cli_bootstrap_message_text(
        project=resolved_project,
        goal_id=resolved_goal_id,
        agent_id=agent_id,
        cli_preflight=install_repair_command,
        existing_goal_probe_command=quota_guard_command,
        connect_command=connect_command,
        heartbeat_prompt_json_command=heartbeat_prompt_json_command,
        heartbeat_prompt_command=heartbeat_prompt_command,
        quota_guard_command=quota_guard_command,
        refresh_command=refresh_command,
        progress_refresh_command=progress_refresh_command,
        quota_spend_command=quota_spend_command,
        first_run_validation_checklist=first_run_validation_checklist,
    )
    return {
        "ok": True,
        "schema_version": "codex_cli_bootstrap_message_v1",
        "invocation_mode": "codex_cli_setup_then_goal_mode",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "install_repair_command": install_repair_command,
        "existing_goal_probe_command": quota_guard_command,
        "connect_command": connect_command,
        "heartbeat_prompt_command": heartbeat_prompt_command,
        "heartbeat_prompt_json_command": heartbeat_prompt_json_command,
        "codex_cli_goal_prefix": "/goal ",
        "codex_app_loop_surface": "heartbeat automation task_body",
        "codex_app_default_heartbeat_cadence": "initially 3 minutes, then follow quota scheduler_hint",
        "quota_guard_command": quota_guard_command,
        "refresh_command": refresh_command,
        "progress_refresh_command": progress_refresh_command,
        "quota_spend_command": quota_spend_command,
        "first_run_validation_checklist": first_run_validation_checklist,
        "message": message,
    }


def build_codex_cli_tui_bootstrap_smoke_bundle(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
) -> dict[str, Any]:
    bootstrap = build_codex_cli_bootstrap_message(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
    )
    resolved_project = str(bootstrap["project"])
    resolved_goal_id = str(bootstrap["goal_id"])
    agent_arg = f" --agent-id {shell_arg(agent_id)}" if agent_id else ""
    message_only_command = (
        f"{shell_arg(cli_bin)} codex-cli-bootstrap-message "
        f"--project {shell_arg(resolved_project)} "
        f"--goal-id {shell_arg(resolved_goal_id)}{agent_arg} --message-only"
    )
    review_packet_command = (
        f"{shell_arg(cli_bin)} codex-cli-bootstrap-message "
        f"--project {shell_arg(resolved_project)} "
        f"--goal-id {shell_arg(resolved_goal_id)}{agent_arg}"
    )
    boundary = {
        "runs_codex": False,
        "reads_raw_transcripts": False,
        "reads_session_files": False,
        "reads_credentials": False,
        "mutates_codex_session": False,
        "spends_loopx_quota": False,
        "requires_loopx_repo_clone": False,
    }
    validation_checklist = [
        "archive installer works from a temporary GitHub-style tarball or existing install",
        "message-only command prints only the setup paste block, not the Markdown review packet",
        "paste block includes no-clone install repair, project bootstrap/connect, thin heartbeat generation, loop activation, and quota guard",
        "accountable progress refresh then quota spend remain later post-delivery commands inside the paste block, not setup or smoke side effects",
        "no raw Codex transcripts, session files, credentials, stdout, stderr, or private paths are read",
    ]
    return {
        "ok": True,
        "schema_version": "codex_cli_tui_bootstrap_smoke_bundle_v0",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "install_repair_command": bootstrap["install_repair_command"],
        "review_packet_command": review_packet_command,
        "message_only_command": message_only_command,
        "heartbeat_prompt_command": bootstrap["heartbeat_prompt_command"],
        "heartbeat_prompt_json_command": bootstrap["heartbeat_prompt_json_command"],
        "quota_guard_command": bootstrap["quota_guard_command"],
        "refresh_command": bootstrap["refresh_command"],
        "progress_refresh_command": bootstrap["progress_refresh_command"],
        "quota_spend_command": bootstrap["quota_spend_command"],
        "validation_checklist": validation_checklist,
        "boundary": boundary,
    }


def build_codex_cli_exec_handoff(
    *,
    project: Path,
    goal_id: str | None,
    agent_id: str | None,
    cli_bin: str,
    codex_bin: str,
) -> dict[str, Any]:
    bootstrap = build_codex_cli_bootstrap_message(
        project=project,
        goal_id=goal_id,
        agent_id=agent_id,
        cli_bin=cli_bin,
    )
    resolved_project = str(project.expanduser())
    resolved_goal_id = str(bootstrap["goal_id"])
    agent_arg = f" --agent-id {shell_arg(agent_id)}" if agent_id else ""
    message_only_command = (
        f"{shell_arg(cli_bin)} codex-cli-bootstrap-message "
        f"--project {shell_arg(resolved_project)} "
        f"--goal-id {shell_arg(resolved_goal_id)}{agent_arg} --message-only"
    )
    return {
        "ok": True,
        "schema_version": "codex_cli_exec_handoff_v0",
        "mode": "headless_fallback_disabled_for_goal_mode_bootstrap",
        "primary_experience": "codex_cli_tui_goal_bootstrap",
        "project": resolved_project,
        "goal_id": resolved_goal_id,
        "agent_id": agent_id,
        "cli_bin": cli_bin,
        "codex_bin": codex_bin,
        "disabled_reason": (
            "Default Codex CLI LoopX bootstrap must stay visible in the TUI; "
            "headless codex exec handoff is disabled to avoid accidental hidden execution."
        ),
        "session_probe_command": f"{shell_arg(cli_bin)} codex-cli-session-probe --codex-bin {shell_arg(codex_bin)}",
        "quota_guard_command": bootstrap["quota_guard_command"],
        "progress_refresh_command": bootstrap["progress_refresh_command"],
        "refresh_command": bootstrap["refresh_command"],
        "quota_spend_command": bootstrap["quota_spend_command"],
        "heartbeat_prompt_json_command": bootstrap["heartbeat_prompt_json_command"],
        "message_only_command": message_only_command,
        "handoff_command": "",
        "boundary": {
            "runs_codex": False,
            "reads_raw_transcripts": False,
            "reads_credentials": False,
            "reads_session_files": False,
            "mutates_codex_session": False,
            "spends_loopx_quota": False,
            "headless_execution_disabled": True,
            "provides_executable_headless_command": False,
        },
    }


def render_codex_cli_bootstrap_message_text(
    *,
    project: str,
    goal_id: str,
    agent_id: str | None,
    cli_preflight: str,
    existing_goal_probe_command: str,
    connect_command: str,
    heartbeat_prompt_json_command: str,
    heartbeat_prompt_command: str,
    quota_guard_command: str,
    refresh_command: str,
    progress_refresh_command: str,
    quota_spend_command: str,
    first_run_validation_checklist: list[str],
) -> str:
    agent_line = f"Use registered LoopX agent id `{agent_id}` when claiming or spending." if agent_id else (
        "If this project has registered agents, inspect the registry and use the correct registered agent id before claiming todos."
    )
    checklist = "\n".join(f"- {item}" for item in first_run_validation_checklist)
    return f"""Install and connect LoopX for this repo from this same Codex CLI TUI session.

Goal: one-message setup, then loop activation. Keep this TUI as the visible
place where I can watch, steer, review, and take over. Do not use hidden
headless `codex exec` for this bootstrap path.

This first message is the setup/bootstrap instruction, not the reusable
heartbeat prompt. Complete the setup in order: install or repair LoopX
if needed; probe whether this goal already exists in the shared global
registry; only bootstrap/connect this project when the goal is absent; then
configure the current loop surface from the generated thin heartbeat prompt.
For Codex CLI, set the current TUI goal to `/goal ` plus the thin `task_body`.
For Codex App, set or refresh the heartbeat automation to start at 3 minutes
with that same thin `task_body`, then follow quota `scheduler_hint` for
backoff. If the current surface cannot be mutated from this session, show the
exact pasteable `/goal` or automation body and report that as a concrete user
gate; do not claim setup success.

Project: `{project}`
Goal id: `{goal_id}`
{agent_line}

Success criteria for this first setup turn:
- I should not need to inspect registry paths, runtime roots, JSON payloads, or
  hidden session files.
- If LoopX is already installed and this goal is already present in the shared
  global registry, reuse the existing `source_registry` route and do not
  duplicate bootstrap from this worktree.
- Configure the loop target after bootstrap: Codex CLI `/goal <thin task_body>`
  or Codex App heartbeat automation starting at 3 minutes with
  `<thin task_body>`, then follow quota `scheduler_hint`.
- Do not claim setup success from registry/quota identity alone. Setup is
  complete only when the host loop surface is active, or when you report the
  exact host-tool gate that prevented setting Codex CLI `/goal` or creating the
  Codex App heartbeat automation.
- Show me the current goal id, concrete user gate if any, top user todo if any,
  top agent todo, and next safe action.
- Do not do longer delivery work in the setup turn unless I explicitly ask; the
  configured thin loop owns later quota/spend delivery turns.

1. Ensure the LoopX CLI works. Prefer the no-clone GitHub archive
installer; do not ask me to clone the LoopX repo just to try the first
run:

```bash
{cli_preflight}
```

2. Before bootstrap/connect, probe the shared global registry for this goal:

```bash
{existing_goal_probe_command}
```

If this command returns a known goal, even with a normal delivery gate or
workspace guard, treat the goal as already connected and reuse the existing
global route/source_registry. In an independent peer-agent worktree, do not
run `loopx bootstrap` for the same `goal_id`; that can create a route collision
between the primary checkout and the worktree. Only run bootstrap when the
probe clearly says the goal is absent from the registered quota plan.

3. If the goal is absent and this repo is not connected to LoopX yet, connect
it conservatively:

```bash
{connect_command}
```

`connect` only registers the goal and its active state. It does not create
first-connect todos, so choose the first delivery todo explicitly (for example
with `todo add`) instead of waiting for onboarding candidates.

4. Generate the thin loop prompt after route reuse or bootstrap/connect, not before. Do not
hand-write or copy an old heartbeat body:

```bash
{heartbeat_prompt_json_command}
```

Read `task_body` from the JSON result. Then configure the current surface:
- Codex CLI TUI: set the current goal to `/goal ` followed by that `task_body`.
- Codex App: create or update the heartbeat automation to start at 3 minutes
  with that `task_body`, then follow quota `scheduler_hint`. If this session
  cannot mutate Codex App automations, report that exact gate and do not say
  LoopX automation is enabled.

For review, the Markdown form is:

```bash
{heartbeat_prompt_command}
```

5. After install/connect and loop activation, run the quota/status guard for
the first control-plane snapshot:

```bash
{quota_guard_command}
```

Follow `interaction_contract` exactly:
- If `user_channel.action_required=true` or open user todos exist, ask only the
  concrete user gate or todo payload.
- If `workspace_guard` blocks delivery, move to an independent worktree and
  rerun the same guard before editing.
- If delivery is allowed, choose one runnable agent todo after a short steering
  audit, preferably a current-agent claimed advancement todo.

6. Report the current goal/gate/todo/next-action snapshot in this TUI. Stop
after setup unless I explicitly asked for delivery in this same turn. Do not
store raw Codex transcripts, credentials, private paths, raw logs, or
production artifacts in public docs or LoopX state.

7. Before writeback, use this transcript-free validation checklist:

{checklist}

8. After setup writeback, refresh state if needed. Do not spend quota for a
setup-only turn. The configured thin loop spends only after a later validated
delivery writeback. If I explicitly asked you to do delivery in this same turn
and you completed a validated delivery segment, replace all three placeholders
with this turn's actual validated classification, batch scale, and outcome.
Never default or upgrade them to `multi_surface` / `outcome_progress`. Run the
rendered commands without pipes or filters: write one accountable progress
refresh, then spend exactly once. If spend output is ambiguous, use read-only
quota status; never rerun it:

```bash
{progress_refresh_command}
{quota_spend_command}
```

If the dashboard or controller needs another update after spend, run the plain
state-only refresh below. It is quota-neutral and must not carry a delivery
outcome:

```bash
{refresh_command}
```

Do not append another accountable progress refresh after spend; it would become
a new unspent delivery record.

End with changed files, validation result, current gate/todo state, and next
safe action. Later automation may add visible steering turns to this session
only after public-safe visible proof, runtime idle evidence, a fresh guard, and
explicit execution bounds. Keep optional automation checks such as
local-driver-plan or visible-session-proof as follow-up diagnostics, not
first-run prerequisites.
"""

def render_prompt_text(
    *,
    project: str,
    goal_doc: str,
    goal_id: str,
    active_state_path: str,
    objective: str,
    domain: str,
    adapter_kind: str,
    adapter_status: str,
    next_probe: str,
    cli_preflight: str,
    connect_command: str,
    quota_guard_command: str,
    quota_spend_command: str,
    refresh_command: str,
    progress_refresh_command: str,
    cli_bin: str,
    spawn_allowed: bool,
    allowed_domains: list[str],
    write_scope: list[str],
) -> str:
    spawn_note = "本项目初始不需要主控拆 sub-agent；除非目标文档另有授权，先保持单 controller read-only 接入。"
    if spawn_allowed:
        spawn_note = (
            "本项目允许主控拆 sub-agent；子 agent 只能进入已声明 allowed-domain，"
            "写入范围只能落在已声明 write-scope。"
        )
    allowed_domains_text = ", ".join(allowed_domains) if allowed_domains else "(none)"
    write_scope_text = ", ".join(write_scope) if write_scope else "(none)"
    return f"""我有一个新项目要接入 LoopX。

项目文件夹：
{project}

项目目标文档：
{goal_doc}

请你按下面步骤推进，不要停在方案讨论；如果信息缺失，先从目标文档和项目结构中做保守抽取，并在最后说明假设。
重要：`{cli_bin} connect` 只登记 goal 和 active state，不会生成首连 onboarding todo。
接入后先只读核对状态与目标文档，把第一个交付 todo 的候选写给我确认，再开始 delivery。

0. 先确认当前 shell 能调用 LoopX CLI；如果提示 `loopx` 不在 PATH，运行本机安装脚本再继续：

```bash
{cli_preflight}
```

1. 再只读检查项目文件夹和目标文档，抽取：
   - stable goal id；
   - 一句话 objective；
   - domain；
   - authority sources；
   - work clusters；
   - validation surfaces；
   - private/public boundary；
   - 第一个 recommended_action。
2. 用这些初始参数作为默认值；如果目标文档给出更好的命名或边界，可以修正：
   - stable goal id: `{goal_id}`
   - objective: `{objective}`
   - domain: `{domain}`
   - adapter_kind: `{adapter_kind}`
   - adapter_status: `{adapter_status}`
   - goal_doc: `{goal_doc}`
   - next_probe: `{next_probe}`
   - spawn_allowed: `{spawn_allowed}`
   - allowed_domains: `{allowed_domains_text}`
   - write_scope: `{write_scope_text}`
3. 运行 LoopX 接入命令：

```bash
{connect_command}
```

4. 确认 `.loopx/registry.json` 和 `{active_state_path}` 已创建或更新。
   接入输出里不再有 onboarding 扫描、候选 todo 或自主推进选择项；首连之后状态里
   没有可执行的 agent todo。请只读核对目标文档和 registry 的 `execution_profile`，
   用中文给出 1-3 个第一个交付 todo 的候选，问用户确认后，用
   `{cli_bin} todo add ...` 写入被接受的条目，再运行 `{refresh_command}` 并汇报。
   在用户确认前不要开始 delivery。
   如果目标状态包含私有证据，把 `.loopx/` 加入该项目 `.gitignore`；旧项目仍需忽略 `.codex/goals/`。
   `{cli_bin} connect` 默认会同步到共享全局 registry；不要手动编辑其他项目的 registry。
   接入后检查 registry 里的 `execution_profile`：它是本项目后续 heartbeat / adapter 的执行画像。
   默认 cadence 是 `bounded_progress_segment`，连续小步达到阈值后，下一轮必须扩展到
   `minimum_scale` 并包含 `must_include` 里的真实 artifact、targeted validation、state writeback；
   如果做不到，先报 blocker，不 append quota spend。
5. 在任何 heartbeat、scheduled tick、long-running adapter 或自主 delivery 前，先问 compute guard：

```bash
{quota_guard_command}
```

   如果返回 `state=operator_gate`，把它当成人/控制器交互，而不是安静 skip：优先读取 payload 里的
   `gate_prompt`、`operator_question`、`recommended_action`、`next_handoff_condition`、`missing_gates`、
   `user_todo_summary` 和 `agent_todo_summary`，用中文主动告诉用户当前卡在哪个 gate、期望怎样回复；
   同时把 `agent_todo_summary` 当作项目 agent 自己的安全后续清单。不要执行任何
   `agent_command`、adapter work、write-control、生产动作或该 gated action。
   如果同一个未决 gate 最近已经问过，且返回 `safe_bypass_allowed=true`，该 gate 只阻塞被 gate 覆盖的
   delivery path；可以从 active state / Priority Stack 里选择一个不依赖该 gate 的 bounded 只读分析、
   steering、文档或 P0/P1 工作。若实际完成 safe-bypass 工作，仍需验证、写回进展，并 append 一次
   quota spend。
   如果 payload 返回 `notify_user_on_open_todo=true`，把开放 user todo 当作 blocker-push，而不是
   静默 skip：用中文最多列 3 个开放项和期望回复格式，并且本轮不做 delivery、不 append quota spend，
   除非同一个 blocker 最近已经问过。
   无论 `should_run` 是 true 还是 false，都先看 `execution_obligation`、
   `effective_action`、`recovery_delivery_allowed`、`safe_bypass_kind` 和
   `heartbeat_recommendation`。`heartbeat_recommendation.notify` 只是用户通知策略，
   不是执行 gate；如果 `execution_obligation.must_attempt_work=true`，即使
   `notify=DONT_NOTIFY` 也要尝试一个 bounded segment，只有
   `must_attempt_work=false` 才能 quiet no-op。
   如果是 `outcome_floor_recovery`，这是 Codex 可执行 recovery turn：只允许做一次所需
   ranker/cross-domain evidence recovery，或写回阻止该 evidence 的具体 blocker；
   不做 surface-only / synthetic-only 循环，验证并写回后才能 append 一次 quota spend。
   如果不是 operator gate / blocker-push / outcome-floor recovery / 明确 safe-bypass，本轮不要做实现或 adapter 工作，
   只记录 public-safe reason；不要执行任何 `agent_command`，即使 status 或 review packet 里提到过命令。
   只有当返回 `should_run=true` 且 payload 里包含 `agent_command` 时，才执行该命令。
   如果 `should_run=true` 但没有 `agent_command`，按
   `execution_obligation` / `recommended_action` / `goal_boundary` 选择下一个
   安全 bounded 动作；只读目标保持只读，delivery 目标按已授权 write scope 执行。
   如果命令非零，fail closed，先修 `{cli_bin} doctor` / `{cli_bin} status`。
   这个 guard 不等于写权限、不绕过 operator gate、也不替代 human reward。
   任何时候，如果你通过 read-only 分析、review doc、gate checklist 或 P0/P1 steering 发现新的
   用户/owner 待办，不要只写在 `Next Action`、外部 review 文档或聊天里。立刻把它写进 active state
   的 user todo 权威区：

```bash
{cli_bin} todo add --goal-id {goal_id} --role user --task-class user_gate --blocks-agent <agent-id> --text "<public-safe blocking user/owner decision>"
{cli_bin} todo add --goal-id {goal_id} --role user --task-class user_action --text "<public-safe non-blocking user/owner todo>"
```

   agent 自己的后续动作写成 `--role agent`。写入后如果 dashboard 需要看到最新状态，运行
   `{refresh_command}`。
   完整契约见 LoopX 仓库里的 `docs/project-agent-todo-contract.md`。
6. 如果需要把当前 packet 或已批准命令交给项目 agent，优先生成最小 handoff，不要从旧聊天、
   旧 review packet 或 `run_history.latest_runs` 拼当前状态。当前权威状态来自
   `attention_queue.items` / `project_asset`；如果缺少 `project_asset` 或标记为
   `legacy/raw fallback`，不要把 raw queue 字段当作 owner/gate/stop authority：

```bash
{cli_bin} review-packet --goal-id {goal_id} --handoff-only
```

   只把输出的 handoff 交给目标项目 agent；完整 review packet 留给 operator view / evidence drill-down。
7. 如果要给这个项目设置 recurring Codex App heartbeat，默认每 3 分钟一次，后续跟随 `quota should-run.scheduler_hint` 降频；不要手抄 guard 和 spend 协议；先生成 task body，再把输出复制进 automation：

```bash
{cli_bin} heartbeat-prompt --goal-id {goal_id} --active-state {shell_arg(active_state_path)}
```

8. 生成一个 read-only project map 或 first pre-tick run。不要启动线上任务、不同步外部系统、不要写生产状态，除非目标文档明确授权。通用接入优先跑：

```bash
{cli_bin} read-only-map --goal-id {goal_id}
```

9. 如果本轮只更新了 active state、ledger 或外部规划文档，没有产生新的
   adapter run，追加一个 state-only refresh run，然后停止且不记 quota spend。
   如果本轮实际消耗了 automatic delivery compute，先不要运行这个 state-only
   refresh；把它留到第 11 步记账之后：

```bash
{refresh_command}
```

这个命令也会自动同步全局 registry。

10. 跑验证：
   - `{cli_bin} registry`
   - `{cli_bin} status`（在没有项目局部 registry 的目录里也应自动读共享全局 registry）
   - `{cli_bin} check --scan-path <PUBLIC_SAFE_FILE_OR_DIR>`
11. 如果本轮实际花了 automatic delivery compute（例如 read-only map、adapter
   tick、实现推进或验证推进），在 validation / writeback 完成后，把命令中的
   三个 placeholder 替换为本轮实际验证过的 classification、batch scale 和
   outcome；不要默认或拔高成 `multi_surface` / `outcome_progress`。然后记录本轮
   accountable delivery：

```bash
{progress_refresh_command}
```

   这是 `spend-slot` 将消费的因果记录；普通 state-only refresh 不能替代它。
   然后原样（不加管道或过滤）append 一次 quota spend；输出为空或不明确时用只读
   quota status 核对，不能重跑：

```bash
{quota_spend_command}
```

   如果 dashboard 或 controller 在 spend 后仍需状态更新，再运行第 9 步的
   `{refresh_command}`。不要在 spend 后追加另一个 accountable progress
   refresh，否则它会成为新的未记账 delivery。

   不要为 quiet `should_run=false` skip、preflight 失败、或纯 dry-run preview 记账；如果
   `should_run=false` 但实际完成了 `safe_bypass_allowed=true` 的 bounded safe-bypass 工作，要记一次账。
   accountable `refresh-state` 应从实际交付 worktree 运行；若 registry/state 投影只能从另一个
   checkout 执行，显式加 `--delivery-workspace-path <delivery-worktree>`，不要让 canonical checkout
   覆盖交付归因。
   不要重复执行。
12. 最后用中文汇报：
   - changed files；
   - validation output；
   - 当前 goal 在 dashboard / attention queue 里会怎么显示；
   - next safe action；
   - 如果还不能接入 decision-advisor，明确缺哪些 gates。

并行/权限边界：
{spawn_note}
"""


def render_new_project_prompt_markdown(payload: dict[str, Any]) -> str:
    return f"""# New Project Codex Handoff Prompt

Copy the block below into the Codex session that can access the target project.

````text
{payload.get("prompt", "")}
````

## Generator Inputs

- project: `{payload.get("project")}`
- goal_doc: `{payload.get("goal_doc")}`
- goal_id: `{payload.get("goal_id")}`
- domain: `{payload.get("domain")}`
- adapter: `{payload.get("adapter_kind")}:{payload.get("adapter_status")}`
"""


def render_codex_cli_bootstrap_message_markdown(payload: dict[str, Any]) -> str:
    checklist = "\n".join(
        f"- {item}" for item in payload.get("first_run_validation_checklist", [])
    )
    goal_mode_note = ""
    if payload.get("invocation_mode") == "codex_cli_setup_then_goal_mode":
        goal_mode_note = (
            "\nThe generated block is a setup message, not the reusable heartbeat body. "
            "After install/bootstrap, it tells the agent to set Codex CLI goal mode "
            "to `/goal <thin task_body>` or Codex App automation starting at 3 minutes "
            "with `<thin task_body>`.\n"
        )
    return f"""# Codex CLI LoopX Bootstrap Message

Copy the block below into Codex CLI TUI from the project repo.
{goal_mode_note}

````text
{payload.get("message", "")}
````

## Fresh Repo Install Repair

The generated message uses the no-clone GitHub archive installer before asking
for a contributor clone:

```bash
{payload.get("install_repair_command", "")}
```

## Post-Bootstrap Thin Loop Prompt

Generate the thin task body after the project is connected:

```bash
{payload.get("heartbeat_prompt_json_command", "")}
```

- Codex CLI TUI loop: set `/goal <task_body>`.
- Codex App loop: set heartbeat automation initially every 3 minutes with
  `<task_body>`, then follow quota `scheduler_hint`.

## Transcript-Free Validation Checklist

{checklist}

## Generator Inputs

- project: `{payload.get("project")}`
- goal_id: `{payload.get("goal_id")}`
- agent_id: `{payload.get("agent_id") or "(not provided)"}`
- cli_bin: `{payload.get("cli_bin")}`
- heartbeat_prompt_drift_check: `{payload.get("heartbeat_prompt_command")}`
"""


def render_codex_cli_tui_bootstrap_smoke_bundle_markdown(payload: dict[str, Any]) -> str:
    boundary = payload.get("boundary") or {}
    checklist = "\n".join(f"- {item}" for item in payload.get("validation_checklist", []))
    return f"""# Codex CLI TUI Bootstrap Smoke Bundle

This packet is for validating the first-run Codex CLI path without launching or
mutating a real Codex session.

## Commands

```bash
{payload.get("message_only_command", "")}
```

- review_packet: `{payload.get("review_packet_command")}`
- heartbeat_prompt: `{payload.get("heartbeat_prompt_command")}`
- heartbeat_prompt_json: `{payload.get("heartbeat_prompt_json_command")}`
- quota_guard: `{payload.get("quota_guard_command")}`
- accountable_progress_refresh: `{payload.get("progress_refresh_command")}`
- quota_spend_after_validation: `{payload.get("quota_spend_command")}`
- optional_state_only_refresh_after_spend: `{payload.get("refresh_command")}`

## Fresh Repo Install Repair

```bash
{payload.get("install_repair_command", "")}
```

## Transcript-Free Boundary

- runs_codex: `{boundary.get("runs_codex")}`
- reads_raw_transcripts: `{boundary.get("reads_raw_transcripts")}`
- reads_session_files: `{boundary.get("reads_session_files")}`
- reads_credentials: `{boundary.get("reads_credentials")}`
- mutates_codex_session: `{boundary.get("mutates_codex_session")}`
- spends_loopx_quota: `{boundary.get("spends_loopx_quota")}`
- requires_loopx_repo_clone: `{boundary.get("requires_loopx_repo_clone")}`

## Validation Checklist

{checklist}
"""


def render_codex_cli_exec_handoff_markdown(payload: dict[str, Any]) -> str:
    boundary = payload.get("boundary") or {}
    return f"""# Codex CLI Exec Handoff Disabled

The default Codex CLI LoopX bootstrap is visible setup, then thin
`/goal <task_body>` activation. Headless `codex exec` handoff is disabled here
to avoid accidental hidden execution. Use the message-only command below inside
the visible Codex CLI TUI.

````bash
{payload.get("message_only_command", "")}
````

## Guard Commands

- session_probe: `{payload.get("session_probe_command")}`
- quota_guard: `{payload.get("quota_guard_command")}`
- accountable_progress_refresh: `{payload.get("progress_refresh_command")}`
- quota_spend: `{payload.get("quota_spend_command")}`
- optional_state_only_refresh_after_spend: `{payload.get("refresh_command")}`
- disabled_reason: {payload.get("disabled_reason")}

## Boundary

- runs_codex: `{boundary.get("runs_codex")}`
- reads_raw_transcripts: `{boundary.get("reads_raw_transcripts")}`
- reads_credentials: `{boundary.get("reads_credentials")}`
- reads_session_files: `{boundary.get("reads_session_files")}`
- mutates_codex_session: `{boundary.get("mutates_codex_session")}`
- spends_loopx_quota: `{boundary.get("spends_loopx_quota")}`
- headless_execution_disabled: `{boundary.get("headless_execution_disabled")}`
- provides_executable_headless_command: `{boundary.get("provides_executable_headless_command")}`
"""
