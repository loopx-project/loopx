from __future__ import annotations

from typing import Any
import json


def work_context_lines(context: dict[str, Any]) -> list[str]:
    """Complete source content once; arbitrary source text stays JSON-escaped."""
    if set(context) <= {"complete", "selected_todo_ref", "selected_todo_authority", "instruction"}:
        lines = [
            "- work_context: "
            f"complete={context.get('complete')} "
            f"selected_todo_ref={context.get('selected_todo_ref')}"
            + (f" selected_todo_authority={context['selected_todo_authority']}"
                if context.get("selected_todo_authority") else "")
        ]
        instruction = context.get("instruction")
        if isinstance(instruction, str) and instruction:
            lines.append(f"- work_context_instruction: {instruction}")
        return lines
    return ["", "## Current work context", "", context.get("instruction", ""),
        "", "```json", json.dumps(context, ensure_ascii=False, indent=2), "```", ""]


def turn_envelope_budget_warning_lines(payload: dict[str, Any]) -> list[str]:
    compaction = payload.get("compaction") or {}
    warning = compaction.get("warning") or {}
    if warning.get("code") != "turn_envelope_budget_exceeded":
        return []
    sections = warning.get("section_bytes") or {}
    return [
        "- WARNING: TurnEnvelope exceeds its performance target by "
        f"{warning.get('excess_bytes')} UTF-8 bytes; Turn routing is unchanged.",
        "- section_bytes: "
        + ", ".join(f"{key}={value}" for key, value in sections.items()),
        "- Review over-target sections: "
        + ", ".join(warning.get("over_target_sections") or []),
        "- Compress duplicate presentation or move detail to cold reads; "
        "do not truncate authority or raise the target to hide growth.",
    ]


def render_turn_envelope_markdown(payload: dict[str, Any]) -> str:
    action_value = payload.get("action")
    user_value = payload.get("user")
    writeback_value = payload.get("writeback")
    scheduler_value = payload.get("scheduler")
    compaction_value = payload.get("compaction")
    action = action_value if isinstance(action_value, dict) else {}
    user = user_value if isinstance(user_value, dict) else {}
    writeback = writeback_value if isinstance(writeback_value, dict) else {}
    scheduler = scheduler_value if isinstance(scheduler_value, dict) else {}
    compaction = compaction_value if isinstance(compaction_value, dict) else {}
    lines = [
        "# LoopX Turn Envelope",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- agent_id: `{payload.get('agent_id')}`",
        f"- decision: `{payload.get('decision')}`",
        f"- should_run: `{payload.get('should_run')}`",
        f"- effective_action: `{payload.get('effective_action')}`",
        f"- action: {action.get('primary_action') or action.get('recommended_action') or ''}",
        f"- user_action_required: `{user.get('action_required')}`",
        f"- spend_policy: {writeback.get('spend_policy') or ''}",
        f"- scheduler: `{scheduler.get('action')}`",
        f"- envelope_bytes: `{compaction.get('envelope_json_bytes')}`",
        f"- within_budget: `{compaction.get('within_budget')}`",
        *turn_envelope_budget_warning_lines(payload),
    ]
    if isinstance(payload.get("work_context"), dict):
        lines.extend(work_context_lines(payload["work_context"]))
    return "\n".join(lines)
