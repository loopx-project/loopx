from __future__ import annotations

from contextlib import nullcontext

from .file_lock import exclusive_cross_runtime_file_lock
from .control_plane.coordination.runtime_shadow_writer_adapter import require_prose_state_write_allowed
from .control_plane.todos.active_state_editing import atomic_write_state_text

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .history import _chronology_key, load_index, load_registry
from .paths import resolve_runtime_root
from .public_safe_text import (
    PRIVATE_TEXT_PATTERNS as SHARED_PRIVATE_TEXT_PATTERNS,
    find_private_text_match,
)
from .registry import registry_goals, resolve_state_file
from .control_plane.actor_identity import normalize_owner_controller_actor


GOAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def validate_goal_id(goal_id: str) -> str:
    """Return a goal id that is safe to use as a single path segment.

    Rejects empty ids, path separators, ``..``, and shell metacharacters so a
    caller-supplied goal id cannot escape the runtime goals directory.
    """

    if not GOAL_ID_RE.match(goal_id):
        raise ValueError(
            f"invalid goal_id {goal_id!r}: must match {GOAL_ID_RE.pattern}"
        )
    return goal_id


REWARD_VALUES = {"positive", "negative", "mixed", "neutral"}
LESSON_KINDS = {
    "route",
    "priority",
    "benchmark_protocol",
    "safety_boundary",
    "operating_rule",
}
# Owned by loopx.public_safe_text so every real validator owner shares one
# contract; re-exported here because callers already import this name.
PRIVATE_TEXT_PATTERNS = SHARED_PRIVATE_TEXT_PATTERNS
LOCAL_CONTROL_TEXT_PATTERNS = (
    re.compile(r"\b" + "Bear" + r"er\s+[A-Za-z0-9._~+/=-]+\b", re.I),
    re.compile(r"\b" + "Author" + r"ization\s*:", re.I),
    re.compile(r"\b(?:tok" + r"en|pass" + r"word|sec" + r"ret)\s*[:=]", re.I),
    re.compile(
        r"\b(?:access[_-]?key(?:[_-]?id)?|accessKeyId|ak)\s*[:=]\s*"
        r"[A-Za-z0-9._~+/=-]{8,}\b",
        re.I,
    ),
    re.compile(
        r"\b(?:secret[_-]?access[_-]?key|secretAccessKey|secret[_-]?key|sk)"
        r"\s*[:=]\s*[A-Za-z0-9._~+/=-]{8,}\b",
        re.I,
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)
HUMAN_REWARD_FIELDS = (
    "recorded_at",
    "decision",
    "reward",
    "reason_summary",
    "follow_up",
    "lesson",
)
RUN_OVERLAY_FIELDS = (
    "generated_at",
    "goal_id",
    "classification",
    "recommended_action",
    "health_check",
    "active_task_count",
    "active_priorities",
    "cache_check",
    "controller_readiness",
    "json_path",
    "markdown_path",
)


def public_safe_text_guidance(label: str) -> str:
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


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_local() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def validate_public_safe_text(label: str, value: str | None) -> None:
    if find_private_text_match(value) is not None:
        raise ValueError(
            f"{label} contains a private-looking value; "
            + public_safe_text_guidance(label)
        )


def validate_local_control_text(label: str, value: str | None) -> None:
    """Validate text for local control-plane state, not public export.

    Local routing fields such as recommended_action are allowed to mention
    private/local material, stable control-plane refs, and ordinary governance
    words. They still must not carry credentials or auth headers.
    """

    if not value:
        return
    for pattern in LOCAL_CONTROL_TEXT_PATTERNS:
        if pattern.search(value):
            raise ValueError(
                f"{label} contains a secret-looking value; keep AK/SK, tokens, auth headers, "
                "passwords, and inline credentials out of local control-plane text"
            )


def compact_reward(
    *,
    recorded_at: str | None,
    decision: str,
    reward: str,
    reason_summary: str,
    follow_up: str | None,
    lesson: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if reward not in REWARD_VALUES:
        raise ValueError(f"reward must be one of: {', '.join(sorted(REWARD_VALUES))}")
    validate_public_safe_text("decision", decision)
    validate_public_safe_text("reason_summary", reason_summary)
    validate_public_safe_text("follow_up", follow_up)
    payload = {
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


def compact_reward_lesson(lesson: dict[str, Any]) -> dict[str, Any]:
    kind = str(lesson.get("kind") or "").strip()
    summary = str(lesson.get("summary") or "").strip()
    if kind not in LESSON_KINDS:
        raise ValueError(f"lesson kind must be one of: {', '.join(sorted(LESSON_KINDS))}")
    if not summary:
        raise ValueError("lesson summary is required when lesson metadata is provided")
    validate_public_safe_text("lesson summary", summary)
    payload: dict[str, Any] = {
        "schema_version": "human_reward_lesson_v0",
        "kind": kind,
        "summary": summary,
    }
    for field in ("avoid", "prefer"):
        raw_items = lesson.get(field) or []
        if isinstance(raw_items, str):
            raw_items = [raw_items]
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
    return payload


def select_run(runs: list[dict[str, Any]], run_generated_at: str | None) -> dict[str, Any]:
    if not runs:
        raise ValueError("no compact run records found for goal")
    if run_generated_at:
        for run in runs:
            if str(run.get("generated_at") or "") == run_generated_at:
                return run
        raise ValueError(f"run not found for generated_at={run_generated_at}")
    return max(
        enumerate(runs),
        key=lambda item: (
            *_chronology_key(item[1].get("generated_at")),
            item[0],
        ),
    )[1]


def find_registry_goal(registry: dict[str, Any], goal_id: str) -> dict[str, Any] | None:
    for goal in registry_goals(registry):
        if str(goal.get("id") or "") == goal_id:
            return goal
    return None


def resolve_reward_state_file(
    *,
    registry: dict[str, Any],
    goal_id: str,
    state_file_override: Path | None,
) -> Path | None:
    goal = find_registry_goal(registry, goal_id)
    repo = Path(str(goal.get("repo") or "")).expanduser() if goal and goal.get("repo") else None
    if state_file_override:
        state_file = state_file_override.expanduser()
        if state_file.is_absolute():
            return state_file
        return (repo or Path.cwd()) / state_file
    if not goal or not repo:
        return None
    return resolve_state_file(repo, goal.get("state_file"))


def build_reward_coordination(
    *,
    goal_id: str,
    selected_run: dict[str, Any],
    reward: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
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
    lesson = reward.get("lesson") if isinstance(reward.get("lesson"), dict) else {}
    if lesson:
        summary = (
            f"{summary} lesson[{lesson.get('kind')}]: "
            f"{lesson.get('summary')}"
        )
    visibility = {
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
    reward: dict[str, Any],
    active_state_summary: str,
    project_agent_visibility: dict[str, Any],
) -> str:
    recorded_at = str(reward.get("recorded_at") or now_local())
    history_command = project_agent_visibility.get("history_command")
    lines = [f"- {recorded_at}: {active_state_summary}"]
    if history_command:
        lines.append(f"  - project_agent_visibility: `{history_command}`")
    return "\n".join(lines)


def update_frontmatter_timestamp(text: str, updated_at: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end < 0:
        return text
    frontmatter = text[:end]
    body = text[end:]
    if re.search(r"^updated_at:\s*.*$", frontmatter, flags=re.M):
        frontmatter = re.sub(r"^updated_at:\s*.*$", f"updated_at: {updated_at}", frontmatter, flags=re.M)
    else:
        frontmatter = f"{frontmatter}\nupdated_at: {updated_at}"
    return frontmatter + body


def insert_progress_ledger_entry(text: str, entry: str, updated_at: str) -> tuple[str, bool]:
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
        lines[insert_at:insert_at] = entry.splitlines() + [""]
        return "\n".join(lines).rstrip() + "\n", True

    separator = "" if text.endswith("\n") else "\n"
    return f"{text}{separator}\n## Progress Ledger\n\n{entry}\n", True


def plan_active_state_update(
    *,
    registry: dict[str, Any],
    goal_id: str,
    state_file_override: Path | None,
    active_state_summary: str,
    project_agent_visibility: dict[str, Any],
    reward: dict[str, Any],
    requested: bool,
    dry_run: bool,
) -> tuple[dict[str, Any], Path | None, str | None]:
    update: dict[str, Any] = {
        "requested": requested,
        "written": False,
        "would_write": False,
    }
    if not requested:
        return update, None, None

    state_file = resolve_reward_state_file(
        registry=registry,
        goal_id=goal_id,
        state_file_override=state_file_override,
    )
    if state_file is None:
        raise ValueError("active state summary write requested, but the registry goal has no state_file")
    if not state_file.exists():
        raise FileNotFoundError(f"active state file does not exist: {state_file}")

    entry = build_active_state_entry(
        reward=reward,
        active_state_summary=active_state_summary,
        project_agent_visibility=project_agent_visibility,
    )
    current_text = state_file.read_text(encoding="utf-8")
    updated_text, changed = insert_progress_ledger_entry(
        current_text,
        entry,
        updated_at=str(reward.get("recorded_at") or now_local()),
    )
    update.update(
        {
            "state_file": str(state_file),
            "section": "Progress Ledger",
            "entry": entry,
            "already_present": not changed,
            "would_write": bool(dry_run and changed),
        }
    )
    return update, state_file, updated_text if changed and not dry_run else None


class HumanRewardSummaryWriteError(RuntimeError):
    """The reward overlay committed, but its optional summary did not settle."""

    code = "active_state_summary_write_failed"
    payload = {"appended": True, "active_state_summary_written": False}


def append_human_reward(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str,
    run_generated_at: str | None,
    reward: dict[str, Any],
    actor_kind: str | None = None,
    dry_run: bool = False,
    state_file_override: Path | None = None,
    write_active_state_summary: bool = False,
) -> dict[str, Any]:
    actor = normalize_owner_controller_actor(actor_kind, required=not dry_run)
    validate_goal_id(goal_id)
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    runs, raw_count = load_index(index_path)
    selected = select_run(runs, run_generated_at)

    missing_paths = [
        field
        for field in ("json_path", "markdown_path")
        if not selected.get(field)
    ]
    if missing_paths:
        raise ValueError(f"selected run is missing required index path fields: {', '.join(missing_paths)}")

    index_record = {
        field: selected[field]
        for field in RUN_OVERLAY_FIELDS
        if field in selected
    }
    index_record["goal_id"] = str(index_record.get("goal_id") or goal_id)
    index_record["human_reward"] = {
        field: reward[field]
        for field in HUMAN_REWARD_FIELDS
        if field in reward
    }
    if actor is not None:
        index_record["human_reward"]["actor_kind"] = actor.value

    selected_run = {
        "generated_at": selected.get("generated_at"),
        "classification": selected.get("classification"),
        "recommended_action": selected.get("recommended_action"),
        "json_exists": selected.get("json_exists"),
        "markdown_exists": selected.get("markdown_exists"),
    }
    coordination = build_reward_coordination(
        goal_id=goal_id,
        selected_run=selected_run,
        reward=index_record["human_reward"],
        dry_run=dry_run,
    )
    project_agent_visibility = (
        coordination.get("project_agent_visibility")
        if isinstance(coordination.get("project_agent_visibility"), dict)
        else {}
    )
    active_state_update, state_file_to_write, state_text_to_write = plan_active_state_update(
        registry=registry,
        goal_id=goal_id,
        state_file_override=state_file_override,
        active_state_summary=str(coordination.get("active_state_summary") or ""),
        project_agent_visibility=project_agent_visibility,
        reward=index_record["human_reward"],
        requested=write_active_state_summary,
        dry_run=dry_run,
    )

    if not dry_run:
        state_lock = (
            exclusive_cross_runtime_file_lock(state_file_to_write, operation="reward_summary")
            if state_file_to_write is not None else nullcontext()
        )
        with state_lock:
            if state_file_to_write is not None:
                original = state_file_to_write.read_text(encoding="utf-8")
                planned, changed = insert_progress_ledger_entry(
                    original, str(active_state_update["entry"]),
                    updated_at=str(reward.get("recorded_at") or now_local()),
                )
                require_prose_state_write_allowed(
                    registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
                    state_path=state_file_to_write, original_text=original, planned_text=planned,
                )
                state_text_to_write = planned if changed else None
            index_path.parent.mkdir(parents=True, exist_ok=True)
            with index_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(index_record, ensure_ascii=False) + "\n")
            if state_file_to_write and state_text_to_write is not None:
                try:
                    atomic_write_state_text(state_file_to_write, state_text_to_write)
                except OSError as error:
                    raise HumanRewardSummaryWriteError(str(error)) from error
                active_state_update["written"] = True
                active_state_update["would_write"] = False

    return {
        "ok": True,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "goal_id": goal_id,
        "index_path": str(index_path),
        "raw_index_records_before": raw_count,
        "dry_run": dry_run,
        "selected_run": selected_run,
        "human_reward": index_record["human_reward"],
        "actor_kind": actor.value if actor is not None else None,
        **coordination,
        "active_state_update": active_state_update,
        "index_record": index_record,
        "appended": not dry_run,
    }


def describe_active_state_effect(state_update: dict[str, Any], *, dry_run: bool) -> str:
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


def describe_run_overlay_effect(*, appended: bool, dry_run: bool, index_path: str | None) -> str:
    target = f"`{index_path}`" if index_path else "run index"
    if dry_run:
        return f"预览：不会写 {target}"
    if appended:
        return f"已追加 human_reward overlay 到 {target}"
    return f"未写 {target}"


def render_reward_markdown(payload: dict[str, Any]) -> str:
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

    selected = payload.get("selected_run") if isinstance(payload.get("selected_run"), dict) else {}
    reward = payload.get("human_reward") if isinstance(payload.get("human_reward"), dict) else {}
    state_update = payload.get("active_state_update") if isinstance(payload.get("active_state_update"), dict) else {}
    visibility = payload.get("project_agent_visibility") if isinstance(payload.get("project_agent_visibility"), dict) else {}
    lines.extend(
        [
            "",
            "## Write Effect",
            f"- selected_run: `{selected.get('generated_at')}`",
            f"- run_overlay: {describe_run_overlay_effect(appended=bool(payload.get('appended')), dry_run=bool(payload.get('dry_run')), index_path=payload.get('index_path'))}",
            f"- active_state: {describe_active_state_effect(state_update, dry_run=bool(payload.get('dry_run')))}",
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
    lesson = reward.get("lesson") if isinstance(reward.get("lesson"), dict) else {}
    if lesson:
        lines.append(f"- lesson_kind: `{lesson.get('kind')}`")
        lines.append(f"- lesson_summary: {lesson.get('summary')}")
        for field in ("avoid", "prefer"):
            values = lesson.get(field) if isinstance(lesson.get(field), list) else []
            if values:
                lines.append(f"- lesson_{field}: {', '.join(str(value) for value in values[:5])}")
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
