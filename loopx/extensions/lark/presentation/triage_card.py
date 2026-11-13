"""Lark interactive card for the read-only Steward portfolio triage digest.

The card is a pure presentation of ``steward_portfolio_triage_v0``. It never
embeds callback values that mutate Goal state: each open User Gate carries the
exact ``loopx todo list`` inspection command so the owner can review the full
record before deciding through the existing governed path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .message_card import compact_markdown, compact_plain_text

TRIAGE_CARD_TITLE = "LoopX Steward Triage"
DEFAULT_SECTION_MAX_CHARS = 1400


def _rows(projection: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = projection.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _section(title: str, lines: list[str]) -> dict[str, Any]:
    body = "\n".join([f"**{title}**", *lines])
    return {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": compact_markdown(body, max_chars=DEFAULT_SECTION_MAX_CHARS),
        },
    }


def _badge(item: Mapping[str, Any]) -> str:
    tags: list[str] = []
    if item.get("global_gate"):
        tags.append("global")
    if item.get("priority"):
        tags.append(str(item["priority"]))
    if item.get("status") == "blocked":
        tags.append("blocked")
    return f"[{'/'.join(tags)}] " if tags else ""


def _inspect_command(item: Mapping[str, Any]) -> str:
    return (
        f"loopx todo list --goal-id {item.get('goal_id')} "
        f"--todo-id {item.get('todo_id')}"
    )


def build_steward_triage_card(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Render a bounded, mobile-scannable triage card from a triage projection."""

    if projection.get("schema_version") != "steward_portfolio_triage_v0":
        raise ValueError("triage card requires a steward_portfolio_triage_v0 projection")

    counts = projection.get("counts")
    counts = counts if isinstance(counts, Mapping) else {}
    gates = _rows(projection, "open_user_gates")
    todos = _rows(projection, "attention_todos")
    progress = _rows(projection, "recent_progress")
    unavailable = _rows(projection, "unavailable_goals")
    quiet = projection.get("quiet") is True

    summary = (
        f"Goals `{counts.get('scoped_goals', 0)}` · "
        f"Open gates `{counts.get('open_user_gates', 0)}` · "
        f"P0/P1 & blocked `{counts.get('attention_todos', 0)}`"
    )
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
    ]
    if quiet:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "All clear: nothing needs your decision right now.",
                },
            }
        )

    if gates:
        lines = []
        for gate in gates:
            unblocks = (
                f" → unblocks `{gate['unblocks_todo_id']}`"
                if gate.get("unblocks_todo_id")
                else ""
            )
            lines.append(
                f"- {_badge(gate)}{gate.get('text')}{unblocks}\n"
                f"  `{_inspect_command(gate)}`"
            )
        omitted = int(counts.get("omitted_user_gates") or 0)
        if omitted:
            lines.append(f"- +{omitted} more")
        elements.extend([{"tag": "hr"}, _section("Needs your decision", lines)])

    if todos:
        lines = [
            f"- {_badge(item)}`{item.get('goal_id')}` · {item.get('text')}"
            for item in todos
        ]
        omitted = int(counts.get("omitted_attention_todos") or 0)
        if omitted:
            lines.append(f"- +{omitted} more")
        elements.extend([{"tag": "hr"}, _section("P0/P1 & blocked work", lines)])

    if progress:
        lines = [
            f"- `{entry.get('goal_id')}` · {entry.get('summary')}" for entry in progress
        ]
        elements.extend([{"tag": "hr"}, _section("Recent progress", lines)])

    footer_bits = ["Read-only digest · no lease, model call or quota spent"]
    if unavailable:
        footer_bits.append(f"{len(unavailable)} goal(s) unavailable")
    elements.extend(
        [
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": compact_plain_text(" · ".join(footer_bits), max_chars=120),
                    }
                ],
            },
        ]
    )

    if quiet:
        template = "green"
    elif gates:
        template = "orange"
    else:
        template = "blue"
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": TRIAGE_CARD_TITLE},
        },
        "elements": elements,
    }


__all__ = ["TRIAGE_CARD_TITLE", "build_steward_triage_card"]
