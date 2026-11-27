from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from heapq import merge
from itertools import chain, islice
from pathlib import Path

from .file_lock import exclusive_run_index_lock
from .control_plane.presentation.goal_metadata import goal_parent_fields
from .authority import goal_authority_registry_summary
from .control_plane import compact_control_plane_policy
from .control_plane.goals.activation import (
    GoalActivationState,
    goal_activation_state,
    normalize_goal_activation_state,
)
from .control_plane.goals.legacy_event_source import LEGACY_TODO_EVENT_SOURCE_FIELDS
from .control_plane.goals.path_resolution import resolve_goal_local_path
from .control_plane.progress_scope import AGENT_LANE_PROGRESS_SCOPE
from .control_plane.quota.monitor_poll import QUOTA_MONITOR_POLL_CLASSIFICATION
from .control_plane.quota.host_poll_receipts import read_host_worker_observations
from .control_plane.quota.ledger_readback import (
    QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION,
    relocated_run_artifact_path,
)
from .control_plane.projects.registry_codec import (
    decode_registry_snapshot as decode_registry_snapshot,
    load_registry as load_registry,
)
from .control_plane.runtime.run_artifacts import (
    next_run_artifact_paths,
    reserve_run_artifact_paths,
    run_file_stem,
)
from .control_plane.runtime.run_context_retention import (
    goal_semantic_history_from_runs,
    iter_goal_semantic_history_runs,
    latest_runs_with_agent_context,
)
from .control_plane.runtime.run_index_duplicates import (
    classify_index_duplicate_records,
    duplicate_repair_decision,
    index_identity,
)
from .control_plane.runtime.run_index_rebuild import (
    apply_reviewed_collision_rebuild,
    build_collision_rebuild_plan,
    collision_review_groups,
    split_index_lines,
    validate_reviewed_collision_plan,
)
from .control_plane.runtime.time import chronology_key, now_local_iso, parse_timestamp
from .doctor import PROMOTION_READINESS_CLASSIFICATIONS
from .execution_profile import compact_execution_profile
from .explore_graph import compact_explore_graph_policy
from .paths import (
    registry_project_root,
    resolve_runtime_root,
    validate_goal_id_path_segment as validate_goal_id_path_segment,
)
from .presentation.renderers.run_history import (
    render_history_markdown as render_history_markdown,
    render_index_collision_rebuild_markdown as render_index_collision_rebuild_markdown,
    render_index_duplicate_inspection_markdown as render_index_duplicate_inspection_markdown,
    render_index_duplicate_repair_markdown as render_index_duplicate_repair_markdown,
)
from .registry import registry_goals

STATUS_NEUTRAL_CLASSIFICATIONS = {
    QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION,
    QUOTA_MONITOR_POLL_CLASSIFICATION,
    *PROMOTION_READINESS_CLASSIFICATIONS,
}
REGISTRY_STATUS_FIELDS = (
    "waiting_on",
    "attention_status",
    "operator_question",
    "recommended_action",
    "next_handoff_condition",
    *LEGACY_TODO_EVENT_SOURCE_FIELDS,
)


def _as_dict(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _int_field(mapping: Mapping[str, object], key: str, default: int = 0) -> int:
    value = mapping.get(key)
    if isinstance(value, bool):
        return int(value) if value else default
    return int(value) if isinstance(value, (int, float, str)) and value != "" else default


def _normalized_activation(state: GoalActivationState | str | None) -> str | None:
    return normalize_goal_activation_state(state).value if state is not None else None


def _goal_index_paths(
    registry_path: Path, runtime_root_override: str | None, goal_id: str | None,
) -> tuple[dict[str, object], Path, list[tuple[str, Path]]]:
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
    goal_ids = discover_goal_ids(runtime_root, registry, goal_id)
    return registry, runtime_root, [
        (gid, runtime_root / "goals" / gid / "runs" / "index.jsonl") for gid in goal_ids
    ]


def _index_command_payload(
    registry_path: Path, runtime_root: Path, goal_id: str | None,
    checked_goal_count: int, **extra: object,
) -> dict[str, object]:
    return {
        "ok": True, "registry": str(registry_path), "runtime_root": str(runtime_root),
        "goal_filter": goal_id, "checked_goal_count": checked_goal_count, **extra,
    }


def _group_index_lines(
    lines: Iterable[str],
) -> tuple[int, dict[tuple[str, str, str], list[tuple[int, dict[str, object]]]]]:
    raw_count = 0
    grouped: dict[tuple[str, str, str], list[tuple[int, dict[str, object]]]] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        raw_count += 1
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            grouped.setdefault(index_identity(item), []).append((line_number, item))
    return raw_count, grouped


def goal_registry_digest(goal: Mapping[str, object]) -> str:
    encoded = json.dumps(
        goal if isinstance(goal, dict) else dict(goal),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def host_worker_goal_observation_digest(goal: Mapping[str, object], *, registry_path: Path) -> str | None:
    """Bind a driver observation to its actual source Goal, not sync metadata.

    A global row must still match the existing sanitizer/merge projection of
    that source. Sync timestamps and consumed merge directives are incidental;
    changed source routes, Goal lifetimes or configuration must not reuse the
    old observation. This readback check never changes execution admission.
    """
    from .global_registry import preserve_attention_override, sanitize_goal_for_global

    try:
        selected_registry = registry_path.expanduser().resolve()
        source_route = goal.get("source_registry")
        source_registry = selected_registry
        source_goal: Mapping[str, object] = goal
        if source_route:
            if not isinstance(source_route, str) or not Path(source_route).is_absolute():
                return None
            source_registry = Path(source_route).expanduser().resolve()
            if source_registry != selected_registry:
                candidates = [row for row in registry_goals(load_registry(source_registry))
                              if row.get("id") == goal.get("id")]
                if len(candidates) != 1:
                    return None
                candidate = candidates[0]
                # A global row has one owning source. Do not accept an
                # unverified chain of projections as that source.
                if candidate.get("source_registry"):
                    return None
                expected = preserve_attention_override({}, sanitize_goal_for_global(
                    candidate, source_registry=source_registry, synced_at=""))
                if preserve_attention_override({}, {**goal, "synced_at": ""}) != expected:
                    return None
                source_goal = candidate
        return goal_registry_digest({"source_registry": str(source_registry), "goal": source_goal})
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class RunIndexAudit:
    goal_id: str
    index_path: Path
    raw_index_records: int
    unique_runs: int
    legacy_runtime_goal: bool


@dataclass(frozen=True, slots=True)
class RunHistoryAudit:
    registry_path: Path
    runtime_root: Path
    goal_id: str | None
    activation_state_filter: str | None
    include_runtime_goals: bool
    goal_count: int
    run_count: int
    goals: tuple[RunIndexAudit, ...]

    def matches(
        self, *, registry_path: Path, runtime_root: Path, goal_id: str | None,
        activation_state_filter: GoalActivationState | str | None, include_runtime_goals: bool,
    ) -> bool:
        return (
            self.registry_path == registry_path.expanduser().resolve()
            and self.runtime_root == runtime_root.expanduser().resolve()
            and self.goal_id == (str(goal_id or "").strip() or None)
            and self.activation_state_filter == _normalized_activation(activation_state_filter)
            and self.include_runtime_goals is include_runtime_goals
        )


@dataclass(frozen=True, slots=True)
class StatusHistoryCollection:
    status_history: dict[str, object]
    contract_audit: RunHistoryAudit


@dataclass(frozen=True, slots=True)
class RunIndexSnapshot:
    records: list[dict[str, object]]
    raw_count: int
    digest: str | None
    invalid_count: int = 0
    # Rows that are not text at all, so even their goal and kind are unknown.
    undecodable_count: int = 0


def now_local() -> str:
    return now_local_iso()


_chronology_key = chronology_key


def unique_run_paths(runs_dir: Path, generated_at: str) -> tuple[Path, Path]:
    return next_run_artifact_paths(runs_dir, run_file_stem(generated_at))


def reserve_unique_run_paths(runs_dir: Path, generated_at: str) -> tuple[Path, Path]:
    """Atomically reserve a run JSON path for concurrent append writers."""
    return reserve_run_artifact_paths(runs_dir, run_file_stem(generated_at))


def append_run_index_record(
    index_path: Path,
    record: Mapping[str, object],
    *,
    allow_nan: bool = True,
) -> None:
    """Append one readable JSONL record after any unterminated final row."""

    encoded = (
        json.dumps(
            record if isinstance(record, dict) else dict(record),
            ensure_ascii=False,
            allow_nan=allow_nan,
        ).encode("utf-8")
        + b"\n"
    )
    with index_path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() > 0:
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write(encoded)


def write_reserved_run_artifacts(
    *,
    runs_dir: Path,
    generated_at: str,
    record: dict[str, object],
    index_record: dict[str, object],
    payload: dict[str, object],
    render_markdown: Callable[[dict[str, object]], str],
) -> None:
    from .control_plane.quota.usage_collector import ingest_usage_into_run_record
    from .control_plane.work_items.delivery_history import (
        require_consistent_delivery_claim,
    )

    require_consistent_delivery_claim(record)
    require_consistent_delivery_claim(index_record)

    # Producer call site for GH-C95: normalize any compact usage measurement onto
    # the durable run record and its index row before append. Fail closed here so
    # malformed or negative usage never enters run history.
    ingest_usage_into_run_record(record, index_record=index_record)
    # GH-C07: one lock per goal history index, shared with the repair path.
    index_path = runs_dir / "index.jsonl"
    with exclusive_run_index_lock(index_path, operation="history_run_append"):
        json_path, markdown_path = reserve_unique_run_paths(runs_dir, generated_at)
        index_record["json_path"] = str(json_path)
        index_record["markdown_path"] = str(markdown_path)
        payload["json_path"] = str(json_path)
        payload["markdown_path"] = str(markdown_path)
        payload["index_path"] = str(index_path)
        usage = record.get("usage")
        if isinstance(usage, dict):
            payload["usage"] = dict(usage)
        json_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        markdown_path.write_text(render_markdown(payload) + "\n", encoding="utf-8")
        append_run_index_record(index_path, index_record, allow_nan=False)


def discover_goal_ids(
    runtime_root: Path,
    registry: Mapping[str, object],
    requested_goal: str | None,
    *,
    include_runtime_goals: bool = True,
) -> list[str]:
    if requested_goal:
        return [requested_goal]

    raw_registry = registry if isinstance(registry, dict) else dict(registry)
    ids = {str(goal.get("id")) for goal in registry_goals(raw_registry)}
    goals_dir = runtime_root / "goals"
    if include_runtime_goals and goals_dir.exists():
        ids.update(path.name for path in goals_dir.iterdir() if path.is_dir())
    return sorted(ids)


def _goal_run_index_context(index_path: Path) -> tuple[Path, str] | None:
    """Runs directory and goal id for ``goals/<goalId>/runs/index.jsonl``."""

    if index_path.name != "index.jsonl":
        return None
    runs_dir = index_path.parent
    goal_dir = runs_dir.parent
    if runs_dir.name != "runs" or goal_dir.parent.name != "goals":
        return None
    goal_id = goal_dir.name
    if not goal_id or goal_id in {".", ".."}:
        return None
    return runs_dir, goal_id


def _indexed_artifact_exists(
    value: object,
    *,
    artifact_root: Path | None,
    runs_dir: Path | None = None,
    goal_id: str | None = None,
    extension: str | None = None,
) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        path = Path(text).expanduser()
    except (OSError, RuntimeError):
        # An unknown ``~user`` cannot be observed. Keep the run and report
        # the artifact missing instead of aborting the history read.
        return False
    if not path.is_absolute() and artifact_root is not None:
        path = artifact_root / path
    try:
        if path.exists():
            return True
    except OSError:
        # Availability is a best-effort observation, not run-index authority.
        # Match pathlib's Python 3.14 behavior on older supported interpreters:
        # inaccessible or invalid artifact paths must not hide durable history.
        return False
    if runs_dir is None or not goal_id or not extension:
        return False
    # Read-time only: a copied same-named regular file stands in for the
    # absolute path the index still records. The index bytes stay unchanged.
    return (
        relocated_run_artifact_path(runs_dir, goal_id, text, extension) is not None
    )


def _observe_run_artifacts(
    records: Iterable[dict[str, object]],
    *,
    artifact_root: Path | None,
    runs_dir: Path | None = None,
    goal_id: str | None = None,
) -> None:
    """Fresh request-local observations, shared by full and selected reads."""
    observed: dict[tuple[str, str], bool] = {}
    for record in records:
        for source, target, extension in (
            ("json_path", "json_exists", ".json"),
            ("markdown_path", "markdown_exists", ".md"),
        ):
            path = str(record.get(source) or "").strip()
            key = (path, extension)
            if key not in observed:
                observed[key] = _indexed_artifact_exists(
                    path,
                    artifact_root=artifact_root,
                    runs_dir=runs_dir,
                    goal_id=goal_id,
                    extension=extension,
                )
            record[target] = observed[key]


def _append_index_item(
    item: dict[str, object],
    records: list[dict[str, object]],
    positions: dict[tuple[str, str, str], int],
) -> None:
    key = index_identity(item)
    # These are observations, never persisted index authority.
    item.pop("json_exists", None)
    item.pop("markdown_exists", None)
    if key in positions:
        records[positions[key]].update(item)
    else:
        positions[key] = len(records)
        records.append(item)


def _decode_index_lines_per_line(
    raw_lines: Iterable[bytes],
    *,
    tolerate_undecodable: bool,
    skip_invalid_encoding: bool,
    records: list[dict[str, object]],
    positions: dict[tuple[str, str, str], int],
) -> tuple[int, int, int]:
    raw_count = 0
    invalid_count = 0
    undecodable_count = 0
    for encoded_line in raw_lines:
        try:
            line = encoded_line.decode("utf-8")
        except UnicodeDecodeError:
            if not (tolerate_undecodable or skip_invalid_encoding):
                raise
            # A write torn inside a multibyte character leaves bytes that
            # are not text. The caller asked to see the damage as a count.
            raw_count += 1
            invalid_count += 1
            if tolerate_undecodable:
                undecodable_count += 1
            continue
        if not line.strip():
            continue
        raw_count += 1
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            invalid_count += 1
            continue
        if not isinstance(item, dict):
            invalid_count += 1
            continue
        _append_index_item(item, records, positions)
    return raw_count, invalid_count, undecodable_count


def _decode_index_text_records(
    text: str,
    records: list[dict[str, object]],
    positions: dict[tuple[str, str, str], int],
) -> tuple[int, int] | None:
    nonempty = [ln for ln in text.split("\n") if ln.strip()]
    if not nonempty:
        return 0, 0
    # Pigeonhole invariant: every non-empty JSON line yields >=1 array element
    # when joined cleanly, so len(parsed) == len(nonempty) with all-dict items
    # proves every line was a single valid JSON object.
    try:
        parsed = json.loads("[" + ",".join(nonempty) + "]")
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(parsed, list)
        or len(parsed) != len(nonempty)
        or not all(type(item) is dict for item in parsed)
    ):
        return None
    for item in parsed:
        _append_index_item(item, records, positions)
    return len(nonempty), 0


def load_index_snapshot(
    path: Path,
    *,
    artifact_root: Path | None = None,
    include_artifact_status: bool = True,
    tolerate_undecodable: bool = False,
    skip_invalid_encoding: bool = False,
    runs_dir: Path | None = None,
    goal_id: str | None = None,
) -> RunIndexSnapshot:
    """Decode the complete index; derived artifact status is optional internally.

    Full readers keep fresh status by default. History collection defers only
    filesystem observation until its complete semantic selection is known.
    A row cut inside a multibyte character is not text, so a caller cannot tell
    whether it was a spend, a Vision or a replan ack. By default that raises, so
    a reader that gates a write or an admission never proceeds on a list with a
    hole in it. A reader that reports the damage itself passes
    ``tolerate_undecodable=True`` and reads ``undecodable_count``.
    An incomplete internal presentation may instead pass
    ``skip_invalid_encoding=True`` and consume ``invalid_count``. Strict readers
    still receive the decoding error.
    """
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError:
        return RunIndexSnapshot(records=[], raw_count=0, digest=None)

    records: list[dict[str, object]] = []
    positions: dict[tuple[str, str, str], int] = {}
    file_digest = hashlib.sha256(raw_bytes).hexdigest()
    raw_count = 0
    invalid_count = 0
    undecodable_count = 0

    try:
        decoded_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        if not (tolerate_undecodable or skip_invalid_encoding):
            raise
        raw_count, invalid_count, undecodable_count = _decode_index_lines_per_line(
            raw_bytes.split(b"\n"),
            tolerate_undecodable=tolerate_undecodable,
            skip_invalid_encoding=skip_invalid_encoding,
            records=records,
            positions=positions,
        )
    else:
        fast_result = _decode_index_text_records(decoded_text, records, positions)
        if fast_result is not None:
            raw_count, invalid_count = fast_result
        else:
            records.clear()
            positions.clear()
            raw_count, invalid_count, undecodable_count = _decode_index_lines_per_line(
                raw_bytes.split(b"\n"),
                tolerate_undecodable=tolerate_undecodable,
                skip_invalid_encoding=skip_invalid_encoding,
                records=records,
                positions=positions,
            )

    if include_artifact_status:
        observed_runs = runs_dir
        observed_goal = goal_id
        if observed_runs is None or not observed_goal:
            inferred = _goal_run_index_context(path)
            if inferred is not None:
                if observed_runs is None:
                    observed_runs = inferred[0]
                if not observed_goal:
                    observed_goal = inferred[1]
        _observe_run_artifacts(
            records,
            artifact_root=artifact_root,
            runs_dir=observed_runs,
            goal_id=observed_goal,
        )
    return RunIndexSnapshot(
        records=records,
        raw_count=raw_count,
        digest=f"sha256:{file_digest}",
        invalid_count=invalid_count,
        undecodable_count=undecodable_count,
    )


def load_index(
    path: Path,
    *,
    artifact_root: Path | None = None,
    runs_dir: Path | None = None,
    goal_id: str | None = None,
) -> tuple[list[dict[str, object]], int]:
    snapshot = load_index_snapshot(
        path,
        artifact_root=artifact_root,
        runs_dir=runs_dir,
        goal_id=goal_id,
    )
    return snapshot.records, snapshot.raw_count


def latest_status_run(runs: Sequence[dict[str, object]]) -> dict[str, object] | None:
    for run in runs:
        if str(run.get("classification") or "") in STATUS_NEUTRAL_CLASSIFICATIONS:
            continue
        if str(run.get("progress_scope") or "") == AGENT_LANE_PROGRESS_SCOPE:
            continue
        return run
    return None


def collect_history(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str | None,
    limit: int,
    include_runtime_goals: bool = True,
    activation_state_filter: GoalActivationState | str | None = None,
    agent_lane_id: str | None = None,
    scoped_agent_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    registry: Mapping[str, object] | None = None,
    tolerate_undecodable: bool = False,
) -> dict[str, object]:
    from .capabilities.machine_configuration.builtins import (
        read_builtin_goal_machine_configuration,
        project_goal_with_builtin_machine_configuration,
    )
    from .quota import goal_quota_with_spend_ledger, quota_with_unreadable_index_rows

    start, end = parse_timestamp(since), parse_timestamp(until)
    for label, value, parsed in (("since", since, start), ("until", until, end)):
        if value is not None and parsed is None:
            raise ValueError(f"--{label} must be an ISO timestamp")
    if start is not None and end is not None and start >= end:
        raise ValueError("--since must be earlier than --until")
    time_filtered = since is not None or until is not None
    undated_run_count = 0

    resolved_registry = (
        load_registry(registry_path) if registry is None
        else (registry if isinstance(registry, dict) else dict(registry))
    )
    machine_configuration = read_builtin_goal_machine_configuration(runtime_root)
    registry_goal_meta = {str(goal.get("id")): goal for goal in registry_goals(resolved_registry)}
    registry_goal_digests = {gid: goal_registry_digest(goal) for gid, goal in registry_goal_meta.items()}
    goal_meta = {
        gid: project_goal_with_builtin_machine_configuration(goal, machine_configuration)
        for gid, goal in registry_goal_meta.items()
    }
    activation_filter = (
        normalize_goal_activation_state(activation_state_filter)
        if activation_state_filter is not None else None
    )
    goals: list[dict[str, object]] = []
    recent_runs: list[dict[str, object]] = []
    run_count = 0
    recent_limit = max(0, limit)

    for current_goal_id in discover_goal_ids(
        runtime_root, resolved_registry, goal_id,
        include_runtime_goals=include_runtime_goals and activation_filter is None,
    ):
        registry_member = current_goal_id in goal_meta
        meta = goal_meta.get(current_goal_id) or {}
        activation_state = goal_activation_state(meta) if registry_member else GoalActivationState.ACTIVE
        if activation_filter is not None and activation_state is not activation_filter:
            continue
        index_path = runtime_root / "goals" / current_goal_id / "runs" / "index.jsonl"
        index_snapshot = load_index_snapshot(
            index_path, include_artifact_status=False, tolerate_undecodable=tolerate_undecodable,
        )
        raw_count = index_snapshot.raw_count
        runs = [
            run for _, run in sorted(
                enumerate(index_snapshot.records),
                key=lambda item: (*_chronology_key(item[1].get("generated_at")), item[0]),
                reverse=True,
            )
        ]
        for run in runs:
            run["goal_id"] = str(run.get("goal_id") or current_goal_id)
        # Explicit history drill-down scopes the complete source before its
        # requested cap. Status/quota retain their separate Goal-wide source
        # and bounded lane decision window; quota accounting is always Goal-wide.
        goal_runs = runs
        if scoped_agent_id:
            runs = [run for run in goal_runs if run.get("agent_id") == scoped_agent_id]
        retry_evidence = runs
        if time_filtered:
            dated_runs = [(run, parse_timestamp(run.get("generated_at"))) for run in runs]
            undated_run_count += sum(stamp is None for _, stamp in dated_runs)
            runs = [run for run, stamp in dated_runs if stamp is not None
                    and (start is None or stamp >= start) and (end is None or stamp < end)]
        run_count += len(runs)
        if not recent_runs:
            recent_runs = runs[:recent_limit]
        else:
            recent_runs = list(islice(
                merge(recent_runs, runs, key=lambda item: _chronology_key(item.get("generated_at")), reverse=True),
                recent_limit,
            ))

        adapter = _as_mapping(meta.get("adapter"))
        raw_spawn_policy = meta.get("spawn_policy")
        spawn_policy = _as_dict(raw_spawn_policy) if isinstance(raw_spawn_policy, dict) else None
        explore_harness = (spawn_policy or {}).get("explore_harness")
        raw_guards = meta.get("guards")
        quota = goal_quota_with_spend_ledger(meta, goal_runs, runs_dir=index_path.parent) if registry_member else None
        if quota is not None and index_snapshot.undecodable_count:
            # The undecodable rows may be the spend or void events the projection
            # just summed without them; report that instead of an exact count.
            quota = quota_with_unreadable_index_rows(quota, index_snapshot.undecodable_count)
        status_run = latest_status_run(runs)
        latest_runs = latest_runs_with_agent_context(
            runs,
            limit=limit,
            agent_lane_id=None if scoped_agent_id else agent_lane_id,
        )
        semantic_history = goal_semantic_history_from_runs(runs)
        if time_filtered:
            # Active retry is a current fact, not an as-of-window state. Later
            # progress outside the display range must still supersede an old
            # blocker. Qualify the complete relevant lane before filtering its
            # references; keep the old row itself available for investigation.
            visible_runs = {id(run) for run in runs}
            current_retries = goal_semantic_history_from_runs(retry_evidence)["active_blocked_retry_runs"]
            semantic_history["active_blocked_retry_runs"] = [
                run for run in current_retries if id(run) in visible_runs
            ]
        goal_record: dict[str, object] = {
            "id": current_goal_id,
            **goal_parent_fields(meta),
            "activation_state": activation_state.value,
            "registry_goal_digest": registry_goal_digests.get(current_goal_id),
            "host_worker_observations": read_host_worker_observations(
                meta, resolve_goal_local_path(meta.get("state_file"), meta, fallback_base=registry_path.parent),
                registry_goal_digest=registry_goal_digests[current_goal_id], runtime_root=runtime_root,
                source_goal_observation_digest=lambda: host_worker_goal_observation_digest(
                    registry_goal_meta[current_goal_id], registry_path=registry_path),
            ) if registry_member else None,
            "display_name": meta.get("display_name") if registry_member else None,
            "domain": meta.get("domain"),
            "status": meta.get("status") if registry_member else "legacy-runtime",
            "repo": meta.get("repo") if registry_member else None,
            "state_file": meta.get("state_file") if registry_member else None,
            "registry_member": registry_member,
            "legacy_runtime_goal": not registry_member,
            "adapter_kind": adapter.get("kind"),
            "adapter_status": adapter.get("status"),
            "coordination": _as_dict(meta.get("coordination")) if isinstance(meta.get("coordination"), dict) else None,
            "explore_graph": (
                compact_explore_graph_policy(meta.get("explore_graph"), explore_harness)
                if meta.get("explore_graph") is not None or explore_harness
                else None
            ),
            "spawn_policy": spawn_policy,
            "execution_profile": compact_execution_profile(meta.get("execution_profile")) if registry_member else None,
            "control_plane": compact_control_plane_policy(meta.get("control_plane")) if registry_member else None,
            "guards": list(raw_guards) if isinstance(raw_guards, list) else [],
            "next_probe": meta.get("next_probe") if registry_member else None,
            "authority_registry": goal_authority_registry_summary(meta) if registry_member else None,
            "quota": quota,
            "index_path": str(index_path),
            "index_digest": index_snapshot.digest,
            "index_exists": index_path.exists(),
            "raw_index_records": raw_count,
            # Rows that are not text were skipped, not read: say so for every Goal,
            # including a runtime-only one that has no quota projection to carry it.
            **(
                {"unreadable_index_rows": index_snapshot.undecodable_count}
                if index_snapshot.undecodable_count
                else {}
            ),
            "unique_runs": len(runs),
            "latest_status_run": status_run,
            "latest_runs": latest_runs,
            "semantic_history": semantic_history,
        }
        # Reduce the full index first. Old semantic evidence and lane windows
        # may survive outside --limit; every returned Run still needs fresh
        # flags. These references also cover the global recent_runs window.
        _observe_run_artifacts(
            chain(
                latest_runs,
                [status_run] if status_run is not None else [],
                iter_goal_semantic_history_runs(semantic_history),
            ),
            artifact_root=registry_project_root(registry_path),
            runs_dir=index_path.parent,
            goal_id=current_goal_id,
        )
        if registry_member:
            for field in REGISTRY_STATUS_FIELDS:
                if meta.get(field):
                    goal_record[field] = meta.get(field)
        goals.append(goal_record)

    return {
        "ok": True,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "goal_filter": goal_id,
        **({"time_filter": {
            "since_inclusive": start.isoformat() if start is not None else None,
            "until_exclusive": end.isoformat() if end is not None else None,
            "excluded_undated_runs": undated_run_count,
            "quota_scope": "all_goal_runs",
        }} if time_filtered else {}),
        "goal_count": len(goals),
        "run_count": run_count,
        "goals": goals,
        "runs": recent_runs,
    }


def build_run_history_audit(
    history: Mapping[str, object], *, registry_path: Path, runtime_root: Path,
    goal_id: str | None, activation_state_filter: GoalActivationState | str | None,
    include_runtime_goals: bool,
) -> RunHistoryAudit:
    return RunHistoryAudit(
        registry_path=registry_path.expanduser().resolve(),
        runtime_root=runtime_root.expanduser().resolve(),
        goal_id=str(goal_id or "").strip() or None,
        activation_state_filter=_normalized_activation(activation_state_filter),
        include_runtime_goals=include_runtime_goals,
        goal_count=_int_field(history, "goal_count"),
        run_count=_int_field(history, "run_count"),
        goals=tuple(
            RunIndexAudit(
                goal_id=str(item.get("id") or ""),
                index_path=Path(str(item.get("index_path") or "")),
                raw_index_records=_int_field(item, "raw_index_records"),
                unique_runs=_int_field(item, "unique_runs"),
                legacy_runtime_goal=bool(item.get("legacy_runtime_goal")),
            )
            for item in _mapping_list(history.get("goals"))
        ),
    )


def history_for_registry_members(history: Mapping[str, object], *, limit: int) -> dict[str, object]:
    goals = [goal for goal in _mapping_list(history.get("goals")) if goal.get("registry_member") is True]
    recent_limit = max(0, limit)
    recent_runs = list(islice(
        merge(*(_mapping_list(goal.get("latest_runs"))[:recent_limit] for goal in goals),
              key=lambda item: _chronology_key(item.get("generated_at")), reverse=True),
        recent_limit,
    ))
    return {
        **history, "goal_count": len(goals),
        "run_count": sum(_int_field(goal, "unique_runs") for goal in goals),
        "goals": goals, "runs": recent_runs,
    }


def collect_status_history(
    *, registry_path: Path, runtime_root: Path, goal_id: str | None, limit: int,
    status_include_runtime_goals: bool,
    activation_state_filter: GoalActivationState | str | None = None,
    agent_lane_id: str | None = None, registry: Mapping[str, object] | None = None,
) -> StatusHistoryCollection:
    history = collect_history(
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id, limit=limit,
        include_runtime_goals=True, activation_state_filter=activation_state_filter,
        agent_lane_id=agent_lane_id, registry=registry,
    )
    audit = build_run_history_audit(
        history, registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        activation_state_filter=activation_state_filter, include_runtime_goals=True,
    )
    status_history = history
    if not status_include_runtime_goals and not str(goal_id or "").strip() and activation_state_filter is None:
        status_history = history_for_registry_members(history, limit=limit)
    return StatusHistoryCollection(status_history=status_history, contract_audit=audit)


def inspect_index_duplicates(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None,
    limit: int,
) -> dict[str, object]:
    _registry, runtime_root, goal_indexes = _goal_index_paths(
        registry_path, runtime_root_override, goal_id
    )
    artifact_root = registry_project_root(registry_path)
    groups: list[dict[str, object]] = []
    raw_index_records = 0
    duplicate_row_count = 0

    for current_goal_id, index_path in goal_indexes:
        if not index_path.exists():
            continue
        with index_path.open(encoding="utf-8") as f:
            file_raw_count, grouped = _group_index_lines(f)
        raw_index_records += file_raw_count

        for key, records in grouped.items():
            if len(records) <= 1:
                continue
            duplicate_row_count += len(records) - 1
            generated_at, json_path, markdown_path = key
            raw_records = [record for _, record in records]
            duplicate_classification = classify_index_duplicate_records(raw_records)
            groups.append({
                "goal_id": current_goal_id,
                "index_path": str(index_path),
                "generated_at": generated_at,
                "json_path": json_path,
                "markdown_path": markdown_path,
                "json_exists": _indexed_artifact_exists(
                    json_path,
                    artifact_root=artifact_root,
                    runs_dir=index_path.parent,
                    goal_id=current_goal_id,
                    extension=".json",
                ),
                "markdown_exists": _indexed_artifact_exists(
                    markdown_path,
                    artifact_root=artifact_root,
                    runs_dir=index_path.parent,
                    goal_id=current_goal_id,
                    extension=".md",
                ),
                "line_numbers": [line_number for line_number, _ in records],
                "raw_rows": len(records),
                "duplicate_rows": len(records) - 1,
                "duplicate_kind": duplicate_classification.get("duplicate_kind"),
                "severity": duplicate_classification.get("severity"),
                "classifications": sorted({str(record.get("classification") or "") for record in raw_records}),
                "health_checks": sorted({str(record.get("health_check") or "") for record in raw_records if record.get("health_check")}),
                "reward_overlay_rows": sum(1 for record in raw_records if isinstance(record.get("human_reward"), dict)),
                "repair_hint": duplicate_classification.get("repair_hint"),
            })

    severity_priority = {"warning": 0, "info": 1}
    groups.sort(key=lambda item: (
        severity_priority.get(str(item.get("severity")), 99),
        str(item.get("goal_id")),
        str(item.get("generated_at")),
    ))
    limited_groups = groups[: max(0, limit)]
    return _index_command_payload(
        registry_path, runtime_root, goal_id, len(goal_indexes),
        raw_index_records=raw_index_records, duplicate_group_count=len(groups),
        duplicate_row_count=duplicate_row_count, groups=limited_groups,
        truncated=len(groups) > len(limited_groups), limit=limit,
    )


def repair_index_duplicates(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None,
    limit: int,
    execute: bool,
) -> dict[str, object]:
    _registry, runtime_root, goal_indexes = _goal_index_paths(
        registry_path, runtime_root_override, goal_id
    )
    raw_index_records = 0
    removed_row_count = 0
    preserved_reward_overlay_rows = 0
    unrepaired_group_count = 0
    groups: list[dict[str, object]] = []

    for current_goal_id, index_path in goal_indexes:
        if not index_path.exists():
            continue
        # GH-C07: read and rewrite the index under the same lock the append
        # path takes. A dry run only reports, so it must not block writers.
        lock = (
            exclusive_run_index_lock(index_path, operation="history_index_repair")
            if execute else nullcontext()
        )
        with lock:
            raw_lines = split_index_lines(index_path.read_text(encoding="utf-8"))
            file_raw_count, grouped = _group_index_lines(raw_lines)
            raw_index_records += file_raw_count

            remove_lines: set[int] = set()
            for records in grouped.values():
                if len(records) <= 1:
                    continue
                decision = duplicate_repair_decision(records)
                removed_lines = [int(ln) for ln in (decision.get("removed_line_numbers") or [])]
                if decision.get("action") == "preserve_reward_overlay":
                    preserved_reward_overlay_rows += len(records) - 1
                elif decision.get("repairable"):
                    remove_lines.update(removed_lines)
                    removed_row_count += len(removed_lines)
                else:
                    unrepaired_group_count += 1

                first_record = records[0][1]
                groups.append({
                    "goal_id": current_goal_id,
                    "index_path": str(index_path),
                    "generated_at": first_record.get("generated_at"),
                    "json_path": first_record.get("json_path"),
                    "markdown_path": first_record.get("markdown_path"),
                    "action": decision.get("action"),
                    "repairable": decision.get("repairable"),
                    "line_numbers": decision.get("line_numbers"),
                    "kept_line_numbers": decision.get("kept_line_numbers"),
                    "removed_line_numbers": removed_lines,
                    "reason": decision.get("reason"),
                })

            if execute and remove_lines:
                rewritten = [line for ln, line in enumerate(raw_lines, start=1) if ln not in remove_lines]
                tmp_path = index_path.with_suffix(index_path.suffix + ".tmp")
                tmp_path.write_text("".join(line + "\n" for line in rewritten), encoding="utf-8")
                tmp_path.replace(index_path)

    limited_groups = groups[: max(0, limit)]
    return _index_command_payload(
        registry_path, runtime_root, goal_id, len(goal_indexes),
        dry_run=not execute, repaired=bool(execute and removed_row_count),
        raw_index_records=raw_index_records, removed_row_count=removed_row_count,
        preserved_reward_overlay_rows=preserved_reward_overlay_rows,
        unrepaired_group_count=unrepaired_group_count, groups=limited_groups,
        truncated=len(groups) > len(limited_groups), limit=limit,
    )


def rebuild_index_artifact_collisions(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str | None,
    limit: int,
    reviewed_plan: Mapping[str, object] | None,
    execute: bool,
) -> dict[str, object]:
    _registry, runtime_root, goal_indexes = _goal_index_paths(
        registry_path, runtime_root_override, goal_id
    )
    all_groups: list[dict[str, object]] = []
    for current_goal_id, index_path in goal_indexes:
        if index_path.exists():
            all_groups.extend(collision_review_groups(index_path, current_goal_id))

    groups = all_groups[: max(0, limit)]
    truncated = len(groups) < len(all_groups)
    current_plan = build_collision_rebuild_plan(
        groups, goal_filter=goal_id, total_collision_group_count=len(all_groups), truncated=truncated,
    )
    if execute:
        if reviewed_plan is None:
            raise ValueError(
                "rebuild-index-collisions --execute requires --review-plan-json from a reviewed dry-run"
            )
        plan_sha256 = validate_reviewed_collision_plan(
            reviewed_plan if isinstance(reviewed_plan, dict) else dict(reviewed_plan),
            current_plan,
        )
        rebuilt_indexes = apply_reviewed_collision_rebuild(current_plan, plan_sha256=plan_sha256)
    else:
        rebuilt_indexes = []

    return _index_command_payload(
        registry_path, runtime_root, goal_id, len(goal_indexes),
        dry_run=not execute, rebuilt=bool(execute and rebuilt_indexes),
        collision_group_count=len(groups), total_collision_group_count=len(all_groups),
        truncated=truncated, review_required=not execute and bool(groups),
        review_plan=current_plan, rebuilt_indexes=rebuilt_indexes,
        destructive_row_deletion=False,
    )
