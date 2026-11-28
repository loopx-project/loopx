"""Markdown presentation of the verified TypeScript comparison, never a diff owner."""

from __future__ import annotations

import json
import re


def _literal(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    # JSON can contain runs of backticks inside strings as well as whole lines.
    fence = "`" * max(
        [3, *(len(match.group()) + 1 for match in re.finditer(r"`+", text))]
    )
    return f"{fence}json\n{text}\n{fence}"


def render_configuration_comparison(payload: dict[str, object]) -> str:
    lines = [
        "# Configuration snapshot comparison",
        "",
        "Read-only comparison of stored snapshots. Goals present in only one snapshot may reflect a selected backup cohort, not a live creation or deletion.",
        "Live settings and activation are unchanged. This output retains private configuration values.",
    ]
    for key in (
        "before_sha256",
        "after_sha256",
        "before_goal_ids",
        "after_goal_ids",
        "only_in_before",
        "only_in_after",
        "before_machine_configuration_present",
        "after_machine_configuration_present",
        "change_count",
    ):
        lines.extend([f"## {key}", _literal(payload[key])])
    changes = payload.get("changes")
    for index, change in enumerate(changes if isinstance(changes, list) else []):
        lines.extend([f"## Change {index + 1}", _literal(change)])
    if not changes:
        lines.append(
            "No stored configuration differences. Goal declaration order is ignored; array order is preserved."
        )
    return "\n\n".join(lines) + "\n"
