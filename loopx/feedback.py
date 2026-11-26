"""Public-safe text validators and human reward overlay recording."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from importlib import import_module as _import_module
from pathlib import Path
import re

from .control_plane.actor_identity import normalize_owner_controller_actor
from .control_plane.runtime.reward_rendering import (
    LESSON_KINDS as LESSON_KINDS,
    REWARD_VALUES as REWARD_VALUES,
    _compact_reward_lesson_scope_value as _compact_reward_lesson_scope_value,
    build_active_state_entry as build_active_state_entry,
    build_reward_coordination as build_reward_coordination,
    compact_reward as compact_reward,
    compact_reward_lesson as compact_reward_lesson,
    describe_active_state_effect as describe_active_state_effect,
    describe_run_overlay_effect as describe_run_overlay_effect,
    insert_progress_ledger_entry as insert_progress_ledger_entry,
    now_local as now_local,
    now_utc as now_utc,
    public_safe_text_guidance as public_safe_text_guidance,
    render_reward_markdown as render_reward_markdown,
    update_frontmatter_timestamp as update_frontmatter_timestamp,
    validate_public_safe_text as validate_public_safe_text,
)
from .control_plane.runtime.time import chronology_key
from .file_lock import exclusive_cross_runtime_file_lock
from .paths import resolve_runtime_root
from .public_safe_text import PRIVATE_TEXT_PATTERNS as SHARED_PRIVATE_TEXT_PATTERNS
from .registry import registry_goals, resolve_state_file


_MUTATION_ADAPTERS = {
    "require_prose_state_write_allowed": "loopx.control_plane.coordination.legacy_writer_fence",
    "atomic_write_state_text": "loopx.control_plane.runtime.document_io",
}
_LAZY_IMPORTS = {
    **_MUTATION_ADAPTERS,
    "append_run_index_record": "loopx.history",
    "load_index": "loopx.history",
    "load_registry": "loopx.history",
}


def __getattr__(name: str) -> object:
    owner = _LAZY_IMPORTS.get(name)
    if owner is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value: object = getattr(_import_module(owner), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_IMPORTS))


def _mutation_adapter(name: str) -> object:
    # Preserve the released module-level patch points and original objects.
    return globals()[name] if name in globals() else __getattr__(name)


def _callable_adapter(name: str) -> Callable[..., object]:
    adapter = _mutation_adapter(name)
    if not callable(adapter):
        raise TypeError(f"feedback adapter {name!r} must be callable")
    return adapter


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


def select_run(
    runs: Sequence[Mapping[str, object]],
    run_generated_at: str | None,
) -> dict[str, object]:
    """Select the requested run or latest chronological run from compacted history."""
    if not runs:
        raise ValueError("no compact run records found for goal")
    if run_generated_at:
        for run in runs:
            if str(run.get("generated_at") or "") == run_generated_at:
                return run if isinstance(run, dict) else dict(run)
        raise ValueError(f"run not found for generated_at={run_generated_at}")
    selected = max(
        enumerate(runs),
        key=lambda item: (
            *chronology_key(item[1].get("generated_at")),
            item[0],
        ),
    )[1]
    return selected if isinstance(selected, dict) else dict(selected)


def find_registry_goal(
    registry: Mapping[str, object],
    goal_id: str,
) -> dict[str, object] | None:
    """Return the matching goal entry from a registry mapping when registered."""
    normalized = registry if isinstance(registry, dict) else dict(registry)
    for goal in registry_goals(normalized):
        if str(goal.get("id") or "") == goal_id:
            return dict(goal)
    return None


def resolve_reward_state_file(
    *,
    registry: Mapping[str, object],
    goal_id: str,
    state_file_override: Path | None,
) -> Path | None:
    """Resolve the active-state file path for optional reward summary writeback."""
    goal = find_registry_goal(registry, goal_id)
    raw_repo = goal.get("repo") if goal else None
    repo = Path(str(raw_repo)).expanduser() if raw_repo else None
    if state_file_override:
        state_file = state_file_override.expanduser()
        if state_file.is_absolute():
            return state_file
        return (repo or Path.cwd()) / state_file
    if not goal or not repo:
        return None
    raw_state_file = goal.get("state_file")
    return resolve_state_file(
        repo,
        raw_state_file if isinstance(raw_state_file, str) else None,
    )


def plan_active_state_update(
    *,
    registry: Mapping[str, object],
    goal_id: str,
    state_file_override: Path | None,
    active_state_summary: str,
    project_agent_visibility: Mapping[str, object],
    reward: Mapping[str, object],
    requested: bool,
    dry_run: bool,
) -> tuple[dict[str, object], Path | None, str | None]:
    """Plan optional Progress Ledger writeback without bypassing prose-write guards."""
    update: dict[str, object] = {
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
        raise ValueError(
            "active state summary write requested, but the registry goal has no state_file"
        )
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


def _select_reward_run(
    index_path: Path,
    run_generated_at: str | None,
) -> tuple[dict[str, object], int]:
    """Select one target run without stat'ing unselected historical artifacts."""
    history_mod = _import_module("loopx.history")
    patched_loader = globals().get("load_index")
    original_loader: object = getattr(history_mod, "load_index", None)
    observe_selected = False
    if patched_loader is not None and patched_loader is not original_loader:
        if not callable(patched_loader):
            raise TypeError("feedback adapter 'load_index' must be callable")
        loaded = patched_loader(index_path)
        if not isinstance(loaded, tuple) or len(loaded) != 2:
            raise ValueError("load_index adapter must return (runs, raw_count)")
        raw_runs, raw_count_obj = loaded
        if not isinstance(raw_runs, Sequence) or isinstance(raw_runs, (str, bytes)):
            raise ValueError("load_index returned invalid runs")
        runs: Sequence[Mapping[str, object]] = [
            row for row in raw_runs if isinstance(row, Mapping)
        ]
        raw_count = int(raw_count_obj)
    else:
        snapshot_loader: object = getattr(history_mod, "load_index_snapshot", None)
        if not callable(snapshot_loader):
            raise AttributeError("loopx.history has no callable 'load_index_snapshot'")
        snapshot = snapshot_loader(index_path, include_artifact_status=False)
        raw_records: object = getattr(snapshot, "records", ())
        if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
            raise ValueError("load_index_snapshot returned invalid records")
        runs = [row for row in raw_records if isinstance(row, Mapping)]
        raw_count = int(getattr(snapshot, "raw_count", 0))
        observe_selected = True

    selected = select_run(runs, run_generated_at)
    missing_paths = [
        field for field in ("json_path", "markdown_path") if not selected.get(field)
    ]
    if missing_paths:
        raise ValueError(
            f"selected run is missing required index path fields: {', '.join(missing_paths)}"
        )

    if observe_selected:
        context_fn: object = getattr(history_mod, "_goal_run_index_context", None)
        observe_fn: object = getattr(history_mod, "_observe_run_artifacts", None)
        inferred = context_fn(index_path) if callable(context_fn) else None
        if callable(observe_fn):
            runs_dir = inferred[0] if isinstance(inferred, tuple) and len(inferred) >= 1 else None
            goal_id = inferred[1] if isinstance(inferred, tuple) and len(inferred) >= 2 else None
            observe_fn(
                [selected],
                artifact_root=None,
                runs_dir=runs_dir,
                goal_id=goal_id,
            )
    return selected, raw_count


def append_human_reward(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str,
    run_generated_at: str | None,
    reward: Mapping[str, object],
    actor_kind: str | None = None,
    dry_run: bool = False,
    state_file_override: Path | None = None,
    write_active_state_summary: bool = False,
) -> dict[str, object]:
    """Validate and append one run-bound human reward overlay (with optional dry-run)."""
    # Resolve before registry reads or any append. An unavailable adapter must
    # fail before the reward index can commit; TS still owns the write check.
    _callable_adapter("require_prose_state_write_allowed")
    _callable_adapter("atomic_write_state_text")
    actor = normalize_owner_controller_actor(actor_kind, required=not dry_run)
    validate_goal_id(goal_id)
    raw_registry = _callable_adapter("load_registry")(registry_path)
    if not isinstance(raw_registry, Mapping):
        raise ValueError("registry payload must be an object")
    registry = raw_registry if isinstance(raw_registry, dict) else dict(raw_registry)
    runtime_root = resolve_runtime_root(
        registry, runtime_root_override, registry_path=registry_path
    )
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    selected, raw_count = _select_reward_run(index_path, run_generated_at)

    index_record: dict[str, object] = {
        field: selected[field] for field in RUN_OVERLAY_FIELDS if field in selected
    }
    index_record["goal_id"] = str(index_record.get("goal_id") or goal_id)
    human_reward: dict[str, object] = {
        field: reward[field] for field in HUMAN_REWARD_FIELDS if field in reward
    }
    if actor is not None:
        human_reward["actor_kind"] = actor.value
    index_record["human_reward"] = human_reward

    selected_run: dict[str, object] = {
        "generated_at": selected.get("generated_at"),
        "classification": selected.get("classification"),
        "recommended_action": selected.get("recommended_action"),
        "json_exists": selected.get("json_exists"),
        "markdown_exists": selected.get("markdown_exists"),
    }
    coordination = build_reward_coordination(
        goal_id=goal_id,
        selected_run=selected_run,
        reward=human_reward,
        dry_run=dry_run,
    )
    raw_visibility = coordination.get("project_agent_visibility")
    project_agent_visibility: Mapping[str, object] = (
        raw_visibility if isinstance(raw_visibility, Mapping) else {}
    )
    active_state_update, state_file_to_write, state_text_to_write = (
        plan_active_state_update(
            registry=registry,
            goal_id=goal_id,
            state_file_override=state_file_override,
            active_state_summary=str(coordination.get("active_state_summary") or ""),
            project_agent_visibility=project_agent_visibility,
            reward=human_reward,
            requested=write_active_state_summary,
            dry_run=dry_run,
        )
    )

    if not dry_run:
        state_lock = (
            exclusive_cross_runtime_file_lock(
                state_file_to_write, operation="reward_summary"
            )
            if state_file_to_write is not None
            else nullcontext()
        )
        # Match refresh/history: index first, then the source state lock.
        from .file_lock import exclusive_run_index_lock

        with exclusive_run_index_lock(index_path, operation="reward_append"), state_lock:
            if state_file_to_write is not None:
                original = state_file_to_write.read_text(encoding="utf-8")
                planned, changed = insert_progress_ledger_entry(
                    original,
                    str(active_state_update["entry"]),
                    updated_at=str(human_reward.get("recorded_at") or now_local()),
                )
                _callable_adapter("require_prose_state_write_allowed")(
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    goal_id=goal_id,
                    state_path=state_file_to_write,
                    original_text=original,
                    planned_text=planned,
                )
                state_text_to_write = planned if changed else None
            index_path.parent.mkdir(parents=True, exist_ok=True)
            _callable_adapter("append_run_index_record")(index_path, index_record)
            if state_file_to_write and state_text_to_write is not None:
                try:
                    _callable_adapter("atomic_write_state_text")(
                        state_file_to_write, state_text_to_write
                    )
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
        "human_reward": human_reward,
        "actor_kind": actor.value if actor is not None else None,
        **coordination,
        "active_state_update": active_state_update,
        "index_record": index_record,
        "appended": not dry_run,
    }


# Keep the historical star-import surface, including the deferred adapters.
__all__ = [name for name in globals() if not name.startswith("_")] + list(_LAZY_IMPORTS)
