from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...control_plane.effect_program import effect_program_from_ordered_steps
from ...control_plane.goals.orphaned_goal_state import render_guided_lines
from ...control_plane.goals.start_goal_todo_delta import append_todo_delta_render_line


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

    transaction = payload.get("guided_transaction")
    transaction = transaction if isinstance(transaction, dict) else {}
    command_pack = payload.get("command_pack")
    command_pack = command_pack if isinstance(command_pack, dict) else {}
    commands = command_pack.get("commands")
    commands = commands if isinstance(commands, dict) else {}
    selected_route = transaction.get("selected_capability_route")
    selected_route = selected_route if isinstance(selected_route, dict) else {}
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
    preserve = transaction.get("preserve_todos_policy")
    preserve = preserve if isinstance(preserve, dict) else {}
    identity_gate = transaction.get("identity_selection_gate")
    identity_gate = identity_gate if isinstance(identity_gate, dict) else {}
    identity_gate_lines = ""
    if identity_gate:
        fresh_registration = identity_gate.get("fresh_agent_registration")
        fresh_lines = []
        if isinstance(fresh_registration, dict):
            fresh_lines = [
                "- **default: register a fresh agent**",
                f"  - preview: `{fresh_registration.get('preview_command')}`",
                f"  - apply: `{fresh_registration.get('execute_command')}`",
                (
                    "  - rerun: "
                    f"`{fresh_registration.get('rerun_start_goal_command')}`"
                ),
            ]
        choices = [
            f"- explicit takeover `{choice.get('agent_id')}`: "
            f"`{choice.get('rerun_start_goal_command')}`"
            for choice in identity_gate.get("choices") or []
            if isinstance(choice, dict)
        ]
        identity_gate_lines = (
            "\n## Agent Identity Gate\n\n"
            f"{identity_gate.get('reason')}\n\n"
            + "\n".join([*fresh_lines, *choices])
            + "\n"
        )
    goal_gate = transaction.get("goal_selection_gate")
    goal_gate = goal_gate if isinstance(goal_gate, dict) else {}
    goal_gate_lines = ""
    if goal_gate:
        choices = [
            f"- `{choice.get('goal_id')}` status=`{choice.get('status')}`: "
            f"`{choice.get('rerun_command')}`"
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
    host_gate = transaction.get("host_surface_selection_gate")
    host_gate = host_gate if isinstance(host_gate, dict) else {}
    host_gate_lines = ""
    if host_gate:
        choices = [
            f"- `{choice.get('host_surface')}`: {choice.get('description')}  "
            f"\n  `{choice.get('rerun_command')}`"
            for choice in host_gate.get("choices") or []
            if isinstance(choice, dict)
        ]
        host_gate_lines = (
            "\n## Host Surface Gate\n\n"
            f"{host_gate.get('reason')}\n\n"
            + "\n".join(choices)
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

- bootstrap: `{preserve.get("force_bootstrap_default")}`; reconnect: {preserve.get("before_destructive_reconnect")}; scope change: {preserve.get("preferred_scope_change")}
"""
