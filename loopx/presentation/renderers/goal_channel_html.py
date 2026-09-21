from __future__ import annotations

from html import escape
from typing import Any, Mapping, Sequence

from ...control_plane.goals.goal_channel_projection import (
    GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION,
    compact_goal_channel_text,
)


def _as_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_mappings(values: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if not values:
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _html_text(value: Any, *, fallback: str = "") -> str:
    text = compact_goal_channel_text(value, limit=500)
    return escape(text if text is not None else fallback)


def _html_attr(value: Any) -> str:
    return escape(str(value), quote=True)


def _html_dict_list(items: Sequence[Mapping[str, Any]], *, keys: Sequence[str]) -> str:
    rows: list[str] = []
    for item in items:
        cells = []
        for key in keys:
            value = item.get(key)
            if value in (None, "", []):
                continue
            cells.append(
                f"<dt>{_html_text(key.replace('_', ' ').title())}</dt>"
                f"<dd>{_html_text(value)}</dd>"
            )
        if cells:
            rows.append("<li><dl>" + "".join(cells) + "</dl></li>")
    return "\n".join(rows)


def _html_panel(
    *,
    panel: str,
    title: str,
    body: str,
    tone: str = "neutral",
) -> str:
    return (
        f'<section class="panel panel-{_html_attr(tone)}" data-panel="{_html_attr(panel)}">'
        f"<h2>{_html_text(title)}</h2>"
        f"{body}"
        "</section>"
    )


def _html_kv_panel(panel: str, title: str, values: Mapping[str, Any], *, tone: str = "neutral") -> str:
    pairs = []
    for key, value in values.items():
        if value in (None, "", []):
            continue
        pairs.append(
            f"<dt>{_html_text(str(key).replace('_', ' ').title())}</dt>"
            f"<dd>{_html_text(value)}</dd>"
        )
    body = "<dl>" + "".join(pairs) + "</dl>" if pairs else '<p class="empty">No compact fields.</p>'
    return _html_panel(panel=panel, title=title, body=body, tone=tone)


def _html_item_panel(
    panel: str,
    title: str,
    items: Sequence[Mapping[str, Any]],
    *,
    keys: Sequence[str],
    empty: str,
    tone: str = "neutral",
) -> str:
    if items:
        body = "<ol>" + _html_dict_list(items, keys=keys) + "</ol>"
    else:
        body = f'<p class="empty">{_html_text(empty)}</p>'
    return _html_panel(panel=panel, title=title, body=body, tone=tone)


def render_goal_channel_projection_html(projection: Mapping[str, Any]) -> str:
    """Render a read-only static HTML fixture for a channel projection.

    This renderer is intentionally presentation-only. It accepts an already
    compact projection, exposes source warnings, and avoids form/button/write
    controls so dashboard prototypes can validate the frontstage shape without
    becoming a second source of truth.
    """

    if projection.get("schema_version") != GOAL_CHANNEL_PROJECTION_SCHEMA_VERSION:
        raise ValueError("unsupported goal channel projection schema_version")
    if projection.get("mode") != "read_only":
        raise ValueError("goal channel projection renderer only accepts read_only mode")

    goal_id = projection.get("goal_id") or "goal"
    display_name = projection.get("display_name") or goal_id
    decision_frame = _as_mapping(projection.get("decision_frame"))
    quota = _as_mapping(projection.get("quota"))
    source_refs = _as_mapping(projection.get("source_refs"))
    truth_contract = _as_mapping(projection.get("truth_contract"))
    user_todos = _as_mappings(projection.get("user_todos"))
    agent_todos = _as_mappings(projection.get("agent_todos"))
    open_gates = _as_mappings(projection.get("open_gates"))
    artifacts = _as_mappings(projection.get("artifacts"))
    active_leases = _as_mappings(projection.get("active_leases"))
    coordination_authority = _as_mapping(projection.get("coordination_authority"))
    coordination_observation = _as_mapping(projection.get("coordination_observation"))
    ownership_unavailable = coordination_observation.get("status") == "unavailable"
    coordination_state = coordination_authority.get("state")
    coordination_next_action = {
        "promoted": "inspect_managed_delegation",
        "promotion_required": "preview_reviewed_goal_authority_promotion",
        "unavailable": (
            "repair_canonical_authority"
            if coordination_observation.get("source_authority") == "canonical_unavailable"
            else "repair_authority_observation"
        ),
    }.get(coordination_state)
    coordination_authority_panel = dict(coordination_authority)
    if coordination_next_action is not None:
        coordination_authority_panel["next_action"] = coordination_next_action
    recent_events = _as_mappings(projection.get("recent_events"))
    source_warnings = _as_mappings(projection.get("source_warnings"))

    panels = [
        _html_kv_panel("decision-frame", "Decision Frame", decision_frame, tone="blue"),
        _html_kv_panel(
            "coordination-authority",
            "Coordination Authority",
            coordination_authority_panel,
            tone=(
                "green"
                if coordination_authority.get("state") == "promoted"
                else "red"
                if coordination_authority.get("state") == "unavailable"
                else "orange"
            ),
        ),
        _html_kv_panel("quota", "Quota Guard", quota, tone="purple"),
        _html_item_panel(
            "user-todos",
            "User Todos",
            user_todos,
            keys=(
                "priority",
                "status",
                "bound_agent",
                "goal_bound",
                "todo_id",
                "title",
            ),
            empty="No open user todo projected.",
            tone="orange",
        ),
        _html_item_panel(
            "agent-todos",
            "Agent Todos",
            agent_todos,
            keys=(
                "priority",
                "status",
                "claimed_by",
                "todo_id",
                "title",
            ),
            empty="No open agent todo projected.",
            tone="green",
        ),
        _html_item_panel(
            "open-gates",
            "Open Gates",
            open_gates,
            keys=("kind", "status", "gate_id", "blocks"),
            empty="No open gate projected.",
            tone="orange",
        ),
        _html_item_panel(
            "active-leases",
            "Claims And Leases",
            active_leases,
            keys=(
                "status",
                "owner_agent",
                "todo_id",
                "lease_until",
                "write_scope",
                "lease_version",
                "expires_at",
                "reason",
                "claimed_by",
            ),
            empty="Task ownership is unavailable; see Source Warnings." if ownership_unavailable else "No active claim or lease projected.",
            tone="red" if ownership_unavailable else "green",
        ),
        _html_item_panel(
            "artifacts",
            "Artifacts",
            artifacts,
            keys=("kind", "label", "path", "run_id"),
            empty="No public-safe artifact projected.",
        ),
        _html_item_panel(
            "recent-events",
            "Recent Events",
            recent_events,
            keys=("generated_at", "classification", "summary"),
            empty="No compact run event projected.",
        ),
        _html_item_panel(
            "source-warnings",
            "Source Warnings",
            source_warnings,
            keys=("kind", "key_names", "message"),
            empty="No source warning.",
            tone="red",
        ),
        _html_kv_panel("source-refs", "Source References", source_refs),
        _html_kv_panel("truth-contract", "Truth Contract", truth_contract, tone="blue"),
    ]

    css = """
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f8fafc;
      color: #111827;
    }
    body { margin: 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 40px 24px 56px; }
    header { border-bottom: 1px solid #d7dde8; padding-bottom: 22px; margin-bottom: 24px; }
    h1 { font-size: 34px; line-height: 1.15; margin: 0 0 10px; letter-spacing: 0; }
    h2 { font-size: 18px; line-height: 1.25; margin: 0 0 16px; letter-spacing: 0; }
    p { color: #4b5563; margin: 0; line-height: 1.55; }
    code { background: #eef2ff; border-radius: 4px; padding: 2px 5px; }
    .meta { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
    .pill { border: 1px solid #cfd8e7; border-radius: 999px; color: #334155; padding: 6px 10px; font-size: 13px; }
    .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .panel { background: #ffffff; border: 1px solid #d8dee9; border-radius: 8px; padding: 18px; min-height: 160px; }
    .panel-blue { border-color: #3b82f6; background: #eff6ff; }
    .panel-green { border-color: #22c55e; background: #f0fdf4; }
    .panel-orange { border-color: #f59e0b; background: #fffbeb; }
    .panel-purple { border-color: #8b5cf6; background: #f5f3ff; }
    .panel-red { border-color: #f43f5e; background: #fff1f2; }
    ol { margin: 0; padding-left: 20px; display: grid; gap: 12px; }
    dl { display: grid; grid-template-columns: minmax(92px, 0.35fr) 1fr; gap: 7px 12px; margin: 0; }
    dt { color: #64748b; font-size: 13px; font-weight: 700; }
    dd { margin: 0; color: #1f2937; font-size: 14px; line-height: 1.45; overflow-wrap: anywhere; }
    .empty { color: #64748b; }
    @media (max-width: 720px) {
      main { padding: 28px 16px 40px; }
      h1 { font-size: 28px; }
      dl { grid-template-columns: 1fr; }
    }
    """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html_text(display_name)} - Goal Channel Projection</title>
  <style>{css}</style>
</head>
<body>
  <main
    data-schema="{_html_attr(projection.get('schema_version'))}"
    data-mode="{_html_attr(projection.get('mode'))}"
    data-goal-id="{_html_attr(goal_id)}"
  >
    <header>
      <h1>{_html_text(display_name)}</h1>
      <p>{_html_text(projection.get('next_action'), fallback='No next action projected.')}</p>
      <div class="meta">
        <span class="pill">goal: <code>{_html_text(goal_id)}</code></span>
        <span class="pill">status: {_html_text(projection.get('latest_status'), fallback='unknown')}</span>
        <span class="pill">waiting on: {_html_text(projection.get('waiting_on'), fallback='unknown')}</span>
        <span class="pill">generated: {_html_text(projection.get('generated_at'), fallback='unknown')}</span>
      </div>
    </header>
    <div class="grid">
      {"".join(panels)}
    </div>
  </main>
</body>
</html>
"""
