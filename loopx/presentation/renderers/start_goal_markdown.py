from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...control_plane.effect_program import effect_program_from_ordered_steps
from ...control_plane.goals.orphaned_goal_state import (
    ORPHANED_GOAL_STATE_CONNECTION,
    fenced_standalone_message,
    render_guided_lines,
)
from ...control_plane.goals.start_goal_todo_delta import append_todo_delta_render_line

CANONICAL_SLASH_COMMAND = "/loopx"


def _dict_field(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def _guided_markdown_rpc_step(step: Mapping[str, Any]) -> dict[str, Any]:
    """Send only the fields the effect program uses to normalize display steps."""
    fields = ("id", "kind", "purpose", "command", "command_template", "prompt")
    projected = {key: step[key] for key in fields if key in step}
    step_id = projected.get("id")
    if isinstance(step_id, str) and step_id != "connect_if_needed":
        for key in ("command", "command_template", "prompt"):
            value = projected.get(key)
            if isinstance(value, str) and value:
                # An empty first line must keep the original truthiness so
                # command normalization cannot select a different fallback.
                projected[key] = value.splitlines()[0] or value
    return projected


def render_start_goal_guided_markdown(payload: dict[str, Any]) -> str:
    def command_summary(value: Any) -> str:
        parts = str(value or "").split()
        return " ".join(parts[:3]) + (" ..." if len(parts) > 3 else "")

    def actionable_shell_command(value: Any) -> str:
        lines = [
            line.removesuffix("\\").strip()
            for line in str(value or "").splitlines()
            if line.strip()
        ]
        if len(lines) > 1 and lines[0].startswith("cd "):
            return f"{lines[0]} && {' '.join(lines[1:])}"
        return " ".join(lines)

    transaction = _dict_field(payload, "guided_transaction")
    command_pack = _dict_field(payload, "command_pack")
    commands = _dict_field(command_pack, "commands")
    selected_route = _dict_field(transaction, "selected_capability_route")
    ordered_steps = transaction.get("ordered_steps")
    ordered_steps = ordered_steps if isinstance(ordered_steps, list) else []
    step_sources = [step for step in ordered_steps if isinstance(step, Mapping)]
    # The typed program preserves one normalized step per source in order.
    # Keep the complete source here for the renderer and the public JSON.
    program = effect_program_from_ordered_steps(
        [_guided_markdown_rpc_step(step) for step in step_sources],
        include_raw=False,
    )
    step_lines: list[str] = []
    for index, (step, raw_step) in enumerate(
        zip(program.steps, step_sources, strict=True), start=1
    ):
        command = (
            step.command
            or raw_step.get("command_source")
            or raw_step.get("prompt")
        )
        if step.kind == "capability_guard":
            entry_key = str(selected_route.get("entry_command_key") or "")
            admission_key = str(selected_route.get("admission_command_key") or "")
            entry_command = commands.get(entry_key)
            admission_command = commands.get(admission_key)
            discovery_command = raw_step.get("candidate_discovery_command_template")
            authority = (
                "open public issue; source clues are evidence only"
                if raw_step.get("candidate_authority") == "public_open_tracker_issue"
                else raw_step.get("candidate_authority")
            )
            step_lines.extend(
                [
                    f"{index}. `{step.step_id}` ({step.kind})",
                    f"   - authority: {authority}",
                ]
            )
            if discovery_command:
                step_lines.append(
                    f"   - discover: `{str(discovery_command).splitlines()[0]}`"
                )
            preflight = raw_step.get("candidate_preflight")
            if isinstance(preflight, dict):
                required_evidence = " + ".join(
                    str(field)
                    for field in preflight.get("required_evidence_fields") or []
                )
                step_lines.append(
                    "   - preflight: refresh "
                    f"{raw_step.get('authority_refresh_required')}; "
                    f"provide {required_evidence}; "
                    f"{preflight.get('decision_rule')}"
                )
            step_lines.extend(
                [
                    f"   - qualify: `{command_summary(entry_command)}`",
                    f"   - proceed: `{command_summary(admission_command)}`",
                ]
            )
            continue
        step_label = f"{index}. `{step.step_id}` ({step.kind}): "
        step_lines.append(step_label + str(step.purpose))
        append_todo_delta_render_line(raw_step, step_lines)
        if command:
            rendered_command = (
                actionable_shell_command(command)
                if step.step_id == "connect_if_needed"
                else str(command).splitlines()[0]
            )
            step_lines.append(f"   - `{rendered_command}`")
    preserve = _dict_field(transaction, "preserve_todos_policy")
    identity_gate = _dict_field(transaction, "identity_selection_gate")
    identity_gate_lines = ""
    if identity_gate:
        fresh_registration = identity_gate.get("fresh_agent_registration")
        fresh_lines = []
        if isinstance(fresh_registration, dict):
            fresh_lines = [
                "- **default: register a fresh agent**",
                f"  - preview: `{fresh_registration.get('preview_command')}`",
                f"  - apply: `{fresh_registration.get('execute_command')}`",
                f"  - rerun: `{fresh_registration.get('rerun_start_goal_command')}`",
            ]
        choices = [
            f"- explicit takeover `{choice.get('agent_id')}`: `{choice.get('rerun_start_goal_command')}`"
            for choice in identity_gate.get("choices") or []
            if isinstance(choice, dict)
        ]
        identity_gate_lines = (
            "\n## Agent Identity Gate\n\n"
            f"{identity_gate.get('reason')}\n\n"
            + "\n".join([*fresh_lines, *choices])
            + "\n"
        )
    goal_gate = _dict_field(transaction, "goal_selection_gate")
    goal_gate_lines = ""
    if goal_gate:
        choices = [
            f"- `{choice.get('goal_id')}` status=`{choice.get('status')}`: `{choice.get('rerun_command')}`"
            for choice in goal_gate.get("choices") or []
            if isinstance(choice, dict)
        ]
        goal_gate_lines = (
            "\n## Goal Selection Gate\n\n"
            f"{goal_gate.get('reason')}\n\n"
            + "\n".join(choices)
            + "\n"
        )
    orphan_gate_lines = render_guided_lines(transaction)
    host_gate = _dict_field(transaction, "host_surface_selection_gate")
    host_gate_lines = ""
    if host_gate:
        detail_command = host_gate.get("full_choices_detail_command")
        omitted_count = host_gate.get("omitted_choice_count")
        if isinstance(detail_command, str) and detail_command.strip():
            detail_line = (
                "\n\nNeed another host"
                + (
                    f" ({omitted_count} more)"
                    if isinstance(omitted_count, int) and omitted_count > 0
                    else ""
                )
                + f"? Run `{detail_command}`."
            )
        else:
            detail_line = ""
        choices = [
            f"- `{choice.get('host_surface')}`: {choice.get('description')}  \n  `{choice.get('rerun_command')}`"
            for choice in host_gate.get("choices") or []
            if isinstance(choice, dict)
        ]
        host_gate_lines = (
            "\n## Choose Where This Agent Runs\n\n"
            f"{host_gate.get('reason')}\n\n"
            + "\n".join(choices)
            + detail_line
            + "\n"
        )
    return f"""# Guided Start Goal

- project: `{payload.get("project")}`
- goal_id: `{payload.get("goal_id")}`
- goal_text: `{payload.get("goal_text")}`

Preview only; follow ordered commands to mutate.

## Ordered Transaction

{chr(10).join(step_lines)}
{host_gate_lines}
{goal_gate_lines}
{orphan_gate_lines}
{identity_gate_lines}

## Todo Preservation

- `bootstrap --force`: `{preserve.get("force_bootstrap_default")}`; reconnect: {preserve.get("before_destructive_reconnect")}; scope change: {preserve.get("preferred_scope_change")}
"""


def render_loopx_bootstrap_command_pack_message(payload: dict[str, Any]) -> str:
    connection = _dict_field(payload, "project_connection")
    if connection.get("connection_state") == ORPHANED_GOAL_STATE_CONNECTION:
        return fenced_standalone_message(payload)
    commands = _dict_field(payload, "commands")
    next_step = _dict_field(payload, "recommended_next_step")
    requires_confirmation = bool(next_step.get("requires_user_confirmation"))
    project = payload.get("project")
    goal_id = payload.get("goal_id")
    goal_text = payload.get("goal_text")
    state = connection.get("connection_state")
    reason = connection.get("reason")
    alias = _dict_field(connection, "canonical_project_alias")
    alias_note = (
        f"\nInput project: `{connection.get('input_project')}`\n"
        f"Canonical route: `{alias.get('canonical_project')}` via `{alias.get('source_registry')}`\n"
        if alias.get("applied")
        else ""
    )
    goal_start_contract = _dict_field(payload, "goal_start_contract")
    selected_capability_route = _dict_field(goal_start_contract, "selected_capability_route")
    issue_fix_action = ""
    if selected_capability_route.get("capability_id") == "issue-fix":
        issue_fix_action = f"""

The explicit capability route selects issue-fix. Preview its capability-owned admission:

```bash
{commands.get("issue_fix_workflow_plan_template", "")}
```

Persist candidate preflight. Non-proceed: write its successor/no-follow-up; skip feasibility. Proceed: run feasibility and write its successor:

```bash
{commands.get("issue_fix_feasibility_template", "")}
```

Private repro material, issue body/comment reads, external comments, PR creation, merge, publish, destructive git, and production actions stay explicit gates.

After a PR exists and external-review-request authority is active, request the default top requestable non-author reviewer and require post-write verification:

```bash
{commands.get("issue_fix_reviewer_request_template", "")}
```

After a PR exists, reconcile compact public PR state through the capability-owned lifecycle command:

```bash
{commands.get("issue_fix_pr_lifecycle_template", "")}
```"""
    onboarding = _dict_field(payload, "onboarding_hint")
    activation = _dict_field(payload, "host_loop_activation")
    identity_gate = _dict_field(activation, "identity_selection_gate")

    if identity_gate:
        choices = "\n".join(
            f"- explicit takeover `{choice.get('agent_id')}`: rerun with "
            f"`--agent-id {choice.get('agent_id')}`"
            for choice in identity_gate.get("choices") or []
            if isinstance(choice, dict)
        )
        fresh_registration = _dict_field(identity_gate, "fresh_agent_registration")
        if fresh_registration:
            action = f"""Resolve agent identity before planning writes or host-loop activation.
No unscoped heartbeat or quota command is advertised for a new agent connection.

Reason: {identity_gate.get("reason")}

Default fresh-agent registration:
```bash
{fresh_registration.get("preview_command") or ""}
{fresh_registration.get("execute_command") or ""}
```

Only with explicit takeover intent, choose one existing identity:
{choices}

Rerun `{payload.get("canonical_cli_command")} --agent-id <selected-agent-id>`
after fresh registration or exact takeover selection before continuing."""
        else:
            action = f"""Select one registered agent lane before planning writes or host-loop activation.
No unscoped heartbeat or quota command is advertised for this multi-agent goal.

Reason: {identity_gate.get("reason")}

Identity-aware choices:
{choices}

Rerun `{payload.get("canonical_cli_command")} --agent-id <registered-agent-id>`
with the selected identity before continuing."""
    elif goal_text:
        action = f"""This is an explicit goal-start invocation. Connect project-local LoopX state if needed:

```bash
{commands.get("goal_start_connect_if_needed", "")}
```

Then plan before writing todos. Preserve relative priority by write order:

````text
{commands.get("goal_start_plan_prompt", "")}
````

Write the planned todos with `loopx todo add` in the exact planned order. Same-priority items use that write order as the tie-breaker.
{issue_fix_action}

After todo writeback:

```bash
{commands.get("goal_start_refresh_state", "")}
{commands.get("goal_start_host_loop_activation", "")}
{commands.get("goal_start_quota_should_run", "")}
```

Host loop activation is part of setup, not a nice-to-have:
- agent_type: `{payload.get("agent_type")}`
- host_surface: `{activation.get("host_surface")}`
- activation_method: `{activation.get("activation_method")}`

If the host loop is already proven current, skip the mutation. If it is missing,
unknown, or stale, use the command above to obtain `task_body` and activate the
right host loop: Codex App automation, Codex CLI `/goal <task_body>`, Claude
Code `/loop`, OpenCode bridge, or the custom host-loop gate.
If this session cannot mutate that
host surface, report the exact gate; do not claim autonomous setup complete.
Use `{commands.get("goal_start_agent_onboard_recheck", "")}` only when
activation state is missing/unknown/stale or the agent type changed."""
    elif requires_confirmation:
        action = f"""First show this dry-run preview, then ask me before running the mutation:

```bash
{commands.get("bootstrap_dry_run_preview", "")}
```

If I confirm, run:

```bash
{commands.get("bootstrap_after_user_confirmation", "")}
```"""
    else:
        action = f"""Start with the current LoopX status and do not reconnect:

```bash
{commands.get("status", "")}
```

Only after I ask for a recurring loop surface, generate the heartbeat body:

```bash
{commands.get("heartbeat_prompt", "")}
```"""

    return f"""Handle `{CANONICAL_SLASH_COMMAND}` for this project with explicit mutation boundaries.

Project: `{project}`
Goal id: `{goal_id}`
Detected state: `{state}` ({reason})
{alias_note}

Rules:
- This command pack preview is read-only. Do not run bootstrap/connect, create heartbeat automation, or spend quota while only previewing it.
- Bare `/loopx` is read/status-first: if the project is not fully connected, ask for explicit user confirmation before any command that writes `.loopx/` (or legacy `.codex/goals/`).
- `/loopx <goal text>` is explicit goal-start intent: it may create project-local LoopX state, but it must run the profile-appropriate planning checkpoint before writing todos, then activate the correct host loop if missing/stale.
- Same-priority todos are ranked by planner order, then by `todo add` write order; preserve the order exactly.
- If the project is connected, reuse the existing state and show the status/gate/todo snapshot.

Goal-start contract: `{goal_start_contract.get("schema_version")}`

Suggested new-user note:

```text
{onboarding.get("suggested_user_note", "")}
```

{action}

For ongoing work after the project is connected, use the quota guard:

```bash
{commands.get("quota_guard", "")}
```
"""


def render_loopx_bootstrap_command_pack_markdown(payload: dict[str, Any]) -> str:
    connection = _dict_field(payload, "project_connection")
    next_step = _dict_field(payload, "recommended_next_step")
    safety = _dict_field(payload, "safety_contract")
    commands = _dict_field(payload, "commands")
    onboarding = _dict_field(payload, "onboarding_hint")
    slash_catalog = _dict_field(payload, "available_slash_commands")
    goal_start = _dict_field(payload, "goal_start_contract")
    activation = _dict_field(payload, "host_loop_activation")
    ordering = _dict_field(goal_start, "priority_ordering")
    return f"""# /loopx Bootstrap Command Pack

Canonical slash command: `{payload.get("slash_command")}`
Supported forms: `/loopx`, `/loopx <goal text>`

## Detected Project State

- project: `{payload.get("project")}`
- input_project: `{connection.get("input_project")}`
- goal_id: `{payload.get("goal_id")}`
- agent_type: `{payload.get("agent_type")}`
- goal_text: `{payload.get("goal_text") or ""}`
- connection_state: `{connection.get("connection_state")}`
- reason: `{connection.get("reason")}`
- registry: `{connection.get("registry")}`
- state_file: `{connection.get("state_file")}`
- canonical_project_alias: `{(connection.get("canonical_project_alias") or {}).get("kind") if isinstance(connection.get("canonical_project_alias"), dict) else None}`

## Recommended Next Step

- kind: `{next_step.get("kind")}`
- requires_user_confirmation: `{next_step.get("requires_user_confirmation")}`
- summary: {next_step.get("summary")}

## New User Command Hint

````text
{onboarding.get("suggested_user_note", "")}
````

- CLI help: `{(slash_catalog.get("help") or {}).get("cli_command") if isinstance(slash_catalog.get("help"), dict) else "loopx slash-commands"}`

## Paste Message

````text
{payload.get("message", "")}
````

## Goal Start Contract

- schema: `{goal_start.get("schema_version")}`
- planner_required_before_todo_write: `{(goal_start.get("planner") or {}).get("required_before_todo_write") if isinstance(goal_start.get("planner"), dict) else None}`
- same_priority_tie_breaker: `{ordering.get("same_priority_tie_breaker")}`
- prompt_constraint: {ordering.get("prompt_constraint")}
- host_loop_required_after_todo_writeback: `{(goal_start.get("activation") or {}).get("host_loop_required_after_todo_writeback") if isinstance(goal_start.get("activation"), dict) else None}`

## Host Loop Activation

- host_surface: `{activation.get("host_surface")}`
- activation_method: `{activation.get("activation_method")}`
- entry_command_hint: `{activation.get("entry_command_hint")}`
- activation_input_command: `{activation.get("activation_input_command")}`
- recheck_command: `{commands.get("goal_start_agent_onboard_recheck")}`

## Key Commands

```bash
{commands.get("status", "")}
```

```bash
{commands.get("bootstrap_dry_run_preview", "")}
```

```bash
{commands.get("goal_start_connect_if_needed", "")}
```

```bash
{commands.get("issue_fix_workflow_plan_template", "")}
```

## Safety Contract

- read_only: `{payload.get("read_only")}`
- writes_registry: `{safety.get("writes_registry")}`
- writes_state_file: `{safety.get("writes_state_file")}`
- creates_heartbeat: `{safety.get("creates_heartbeat")}`
- spends_quota: `{safety.get("spends_quota")}`
- explicit_goal_start_may_write_project_local_state: `{safety.get("explicit_goal_start_may_write_project_local_state")}`
- explicit_goal_start_must_activate_host_loop: `{safety.get("explicit_goal_start_must_activate_host_loop")}`
"""
