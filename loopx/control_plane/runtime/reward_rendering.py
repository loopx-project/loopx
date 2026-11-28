"""Pure validation, compaction, and Markdown rendering helpers for human reward records."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
import re

from ...public_safe_text import TEXT_OWNER_CATEGORIES, classify_private_text
from .run_compaction import HUMAN_REWARD_LESSON_STRUCTURED_SCOPE_FIELDS

REWARD_VALUES = {"positive", "negative", "mixed", "neutral"}
LESSON_KINDS = {
    "route",
    "priority",
    "benchmark_protocol",
    "safety_boundary",
    "operating_rule",
}


def now_utc() -> str:
    """Return UTC ISO-8601 timestamp truncated to whole seconds."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_local() -> str:
    """Return local ISO-8601 timestamp truncated to whole seconds."""
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def public_safe_text_guidance(label: str) -> str:
    """Return operator guidance tailored to the rejected control-plane field."""
    normalized = label.lower().replace("-", "_")
    if "next_action" in normalized or "recommended_action" in normalized:
        return (
            "this field is usually local-control state; use validate_local_control_text "
            "for private project routing, or a public-safe alias only on exported surfaces"
        )
    if "todo" in normalized:
        return (
            "use a compact public-safe todo alias or summary here; keep raw local "
            "paths, private URLs, task bodies, and logs in evidence/private payloads"
        )
    if "evidence" in normalized:
        return (
            "use a compact public-safe evidence pointer here; keep raw logs, local "
            "paths, and private URLs in private payloads"
        )
    return (
        "use a public-safe summary or alias here; keep raw local paths, private "
        "URLs, and raw evidence in private payloads"
    )


def validate_public_safe_text(label: str, value: str | None) -> None:
    """Reject text that carries private paths, URLs, or credential-shaped values."""
    # The named policy, not a bare pattern sweep: this is LoopX's own state, so a
    # mention of "Bearer"/"password"/"secret" is a fact about a credential, not
    # one. Value and assignment shapes stay rejected (Refs #5136, direction 2).
    if classify_private_text(value, categories=TEXT_OWNER_CATEGORIES) is not None:
        raise ValueError(
            f"{label} contains a private-looking value; "
            + public_safe_text_guidance(label)
        )


def _compact_reward_lesson_scope_value(
    field: str,
    value: object,
) -> str | list[str] | dict[str, object] | None:
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        validate_public_safe_text(f"lesson {field}", cleaned)
        return cleaned
    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            cleaned = str(entry or "").strip()
            if not cleaned:
                continue
            validate_public_safe_text(f"lesson {field}", cleaned)
            if cleaned not in items:
                items.append(cleaned)
        return items[:10] if items else None
    if isinstance(value, Mapping):
        nested: dict[str, object] = {}
        for key, entry in value.items():
            if not isinstance(key, str) or entry is None or entry == "":
                continue
            cleaned_entry = _compact_reward_lesson_scope_value(f"{field}.{key}", entry)
            if cleaned_entry is not None:
                nested[key] = cleaned_entry
        return nested or None
    return None


def compact_reward_lesson(lesson: Mapping[str, object]) -> dict[str, object]:
    """Validate and compact structured lesson guidance attached to a reward."""
    kind = str(lesson.get("kind") or "").strip()
    summary = str(lesson.get("summary") or "").strip()
    if kind not in LESSON_KINDS:
        raise ValueError(f"lesson kind must be one of: {', '.join(sorted(LESSON_KINDS))}")
    if not summary:
        raise ValueError("lesson summary is required when lesson metadata is provided")
    validate_public_safe_text("lesson summary", summary)
    payload: dict[str, object] = {
        "schema_version": "human_reward_lesson_v0",
        "kind": kind,
        "summary": summary,
    }
    for field in ("avoid", "prefer"):
        raw_value = lesson.get(field) or []
        if isinstance(raw_value, str):
            raw_items: Iterable[object] = (raw_value,)
        elif isinstance(raw_value, Iterable) and not isinstance(raw_value, (bytes, bytearray)):
            raw_items = raw_value
        else:
            raise ValueError(f"lesson {field} must be iterable")
        items: list[str] = []
        for raw_item in raw_items:
            item = str(raw_item or "").strip()
            if not item:
                continue
            validate_public_safe_text(f"lesson {field}", item)
            if item not in items:
                items.append(item)
        if items:
            payload[field] = items[:5]
    for field in HUMAN_REWARD_LESSON_STRUCTURED_SCOPE_FIELDS:
        if field in lesson:
            compacted = _compact_reward_lesson_scope_value(field, lesson[field])
            if compacted is not None:
                payload[field] = compacted
    return payload


def compact_reward(
    *,
    recorded_at: str | None,
    decision: str,
    reward: str,
    reason_summary: str,
    follow_up: str | None,
    lesson: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Validate and compact a human reward input payload."""
    if reward not in REWARD_VALUES:
        raise ValueError(f"reward must be one of: {', '.join(sorted(REWARD_VALUES))}")
    validate_public_safe_text("decision", decision)
    validate_public_safe_text("reason_summary", reason_summary)
    validate_public_safe_text("follow_up", follow_up)
    payload: dict[str, object] = {
        "recorded_at": recorded_at or now_utc(),
        "decision": decision,
        "reward": reward,
        "reason_summary": reason_summary,
    }
    if follow_up:
        payload["follow_up"] = follow_up
    if lesson:
        payload["lesson"] = compact_reward_lesson(lesson)
    return payload


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def build_reward_coordination(
    *,
    goal_id: str,
    selected_run: Mapping[str, object],
    reward: Mapping[str, object],
    dry_run: bool,
) -> dict[str, object]:
    """Build active-state coordination summary and readback pointer metadata."""
    run_generated_at = str(selected_run.get("generated_at") or "unknown-run")
    reward_value = str(reward.get("reward") or "unknown")
    decision = str(reward.get("decision") or "unknown_decision")
    verb = "dry-run：将记录" if dry_run else "已记录"
    reason_summary = reward.get("reason_summary")
    summary = (
        f"{verb}目标 `{goal_id}` 的 run `{run_generated_at}` "
        f"human_reward={reward_value}，decision=`{decision}`。"
        "权威来源是 run-bound `human_reward` overlay；"
        "active state 只摘要这个指针和下一步。"
    )
    if reason_summary:
        summary = f"{summary} reason_summary：{reason_summary}"
    follow_up = reward.get("follow_up")
    if follow_up:
        summary = f"{summary} follow_up：{follow_up}"
    lesson = _as_mapping(reward.get("lesson"))
    if lesson:
        summary = f"{summary} lesson[{lesson.get('kind')}]: {lesson.get('summary')}"
    visibility: dict[str, object] = {
        "source_of_truth": "run_bound_human_reward_overlay",
        "history_command": f"loopx history --goal-id {goal_id} --limit 3",
        "active_state_role": "summary_only",
        "review_packet_role": "optional_handoff_only",
    }
    if lesson:
        visibility["lesson_role"] = "route_warning_only_not_write_control"
    return {
        "active_state_summary": summary,
        "project_agent_visibility": visibility,
    }


def build_active_state_entry(
    *,
    reward: Mapping[str, object],
    active_state_summary: str,
    project_agent_visibility: Mapping[str, object],
) -> str:
    """Format one Progress Ledger entry summarizing a human reward decision."""
    recorded_at = str(reward.get("recorded_at") or now_local())
    history_command = project_agent_visibility.get("history_command")
    lines = [f"- {recorded_at}: {active_state_summary}"]
    if history_command:
        lines.append(f"  - project_agent_visibility: `{history_command}`")
    return "\n".join(lines)


def update_frontmatter_timestamp(text: str, updated_at: str) -> str:
    """Update or append ``updated_at`` inside YAML frontmatter when present."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end < 0:
        return text
    frontmatter = text[:end]
    body = text[end:]
    if re.search(r"^updated_at:\s*.*$", frontmatter, flags=re.M):
        frontmatter = re.sub(
            r"^updated_at:\s*.*$",
            f"updated_at: {updated_at}",
            frontmatter,
            flags=re.M,
        )
    else:
        frontmatter = f"{frontmatter}\nupdated_at: {updated_at}"
    return frontmatter + body


def insert_progress_ledger_entry(
    text: str,
    entry: str,
    updated_at: str,
) -> tuple[str, bool]:
    """Insert ``entry`` under ``## Progress Ledger`` unless already present."""
    text = update_frontmatter_timestamp(text, updated_at)
    if entry in text:
        return text, False

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "## Progress Ledger":
            continue
        insert_at = index + 1
        while insert_at < len(lines) and not lines[insert_at].strip():
            insert_at += 1
        lines[insert_at:insert_at] = [*entry.splitlines(), ""]
        return "\n".join(lines).rstrip() + "\n", True

    separator = "" if text.endswith("\n") else "\n"
    return f"{text}{separator}\n## Progress Ledger\n\n{entry}\n", True


def describe_active_state_effect(
    state_update: Mapping[str, object],
    *,
    dry_run: bool,
) -> str:
    """Describe the active-state writeback outcome in user-facing Markdown."""
    if not state_update or not state_update.get("requested"):
        return "未请求 active-state 摘要写回"
    state_file = state_update.get("state_file") or "unknown state file"
    if state_update.get("already_present"):
        return f"摘要已存在，未重复写入 `{state_file}`"
    if dry_run and state_update.get("would_write"):
        return f"预览：会把摘要写入 `{state_file}` 的 Progress Ledger"
    if state_update.get("written"):
        return f"已把摘要写入 `{state_file}` 的 Progress Ledger"
    return f"未写入 active state；检查 `{state_file}`"


def describe_run_overlay_effect(
    *,
    appended: bool,
    dry_run: bool,
    index_path: str | None,
) -> str:
    """Describe the run-index overlay write effect."""
    target = f"`{index_path}`" if index_path else "run index"
    if dry_run:
        return f"预览：不会写 {target}"
    if appended:
        return f"已追加 human_reward overlay 到 {target}"
    return f"未写 {target}"


def render_reward_markdown(payload: Mapping[str, object]) -> str:
    """Render a compact Markdown report for ``loopx reward``."""
    lines = [
        "# LoopX Human Reward",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- goal: `{payload.get('goal_id')}`",
        f"- index: `{payload.get('index_path')}`",
        f"- appended: `{payload.get('appended')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
    ]
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)

    selected = _as_mapping(payload.get("selected_run"))
    reward = _as_mapping(payload.get("human_reward"))
    state_update = _as_mapping(payload.get("active_state_update"))
    visibility = _as_mapping(payload.get("project_agent_visibility"))
    raw_index_path = payload.get("index_path")
    index_path = raw_index_path if isinstance(raw_index_path, str) else None
    dry_run = bool(payload.get("dry_run"))
    lines.extend(
        [
            "",
            "## Write Effect",
            f"- selected_run: `{selected.get('generated_at')}`",
            (
                "- run_overlay: "
                + describe_run_overlay_effect(
                    appended=bool(payload.get("appended")),
                    dry_run=dry_run,
                    index_path=index_path,
                )
            ),
            f"- active_state: {describe_active_state_effect(state_update, dry_run=dry_run)}",
        ]
    )
    if visibility.get("history_command"):
        lines.append(f"- project_agent_visibility: `{visibility.get('history_command')}`")
    lines.extend(
        [
            "",
            "## Selected Run",
            f"- generated_at: `{selected.get('generated_at')}`",
            f"- classification: `{selected.get('classification')}`",
            f"- artifacts: `{selected.get('json_exists')}/{selected.get('markdown_exists')}`",
            "",
            "## Reward",
            f"- recorded_at: `{reward.get('recorded_at')}`",
            f"- actor_kind: `{reward.get('actor_kind')}`",
            f"- decision: `{reward.get('decision')}`",
            f"- reward: `{reward.get('reward')}`",
            f"- reason_summary: {reward.get('reason_summary')}",
        ]
    )
    if reward.get("follow_up"):
        lines.append(f"- follow_up: {reward.get('follow_up')}")
    lesson = _as_mapping(reward.get("lesson"))
    if lesson:
        lines.append(f"- lesson_kind: `{lesson.get('kind')}`")
        lines.append(f"- lesson_summary: {lesson.get('summary')}")
        for field in ("avoid", "prefer"):
            values = lesson.get(field)
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
                rendered = ", ".join(str(value) for value in list(values)[:5])
                if rendered:
                    lines.append(f"- lesson_{field}: {rendered}")
    if payload.get("active_state_summary"):
        lines.extend(["", "## Active-State Summary", str(payload.get("active_state_summary"))])
    if state_update:
        lines.extend(
            [
                "",
                "## Active-State Writeback",
                f"- requested: `{state_update.get('requested')}`",
                f"- state_file: `{state_update.get('state_file')}`",
                f"- would_write: `{state_update.get('would_write')}`",
                f"- written: `{state_update.get('written')}`",
                f"- already_present: `{state_update.get('already_present')}`",
            ]
        )
    if visibility:
        lines.extend(
            [
                "",
                "## Project-Agent Visibility",
                f"- source_of_truth: `{visibility.get('source_of_truth')}`",
                f"- history_command: `{visibility.get('history_command')}`",
                f"- active_state_role: `{visibility.get('active_state_role')}`",
                f"- review_packet_role: `{visibility.get('review_packet_role')}`",
            ]
        )
    return "\n".join(lines)
