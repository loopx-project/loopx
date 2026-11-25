"""Novelty ledger, variant catalog scheduling, and frontier planning."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
from pathlib import Path
from typing import cast

from .episode_runtime import build_router_probes
from .harness_queue import (
    DEFAULT_OBSERVATION_WEIGHT,
    HarnessLaneRecord,
    _as_float,
    _collection_or_empty,
    _mapping_or_empty,
    _read_json,
    _write_json,
)
from .router_state import advance_epoch, initial_router_state, observe_epoch
from .worker_branch_plan import build_explore_worker_branch_plan

VARIANT_CATALOG_SCHEMA_VERSION = "loopx_explore_variant_catalog_v0"
FRONTIER_REQUESTS_SCHEMA_VERSION = "loopx_explore_frontier_requests_v0"


class NoveltyLedger:
    """First-seen ledger: raw observation keys global, flags family-scoped.

    Raw keys are ledgered globally so relabeling an item under a different
    family can never re-mint an already-seen key; weighted flags are
    family-scoped facts. Weights come from the record itself (adapter-owned).
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def raw_value(self, record: Mapping[str, object]) -> float:
        keys = _collection_or_empty(
            record.get("observation_keys"), field_name="observation_keys"
        )
        flags = _mapping_or_empty(
            record.get("weighted_flags"), field_name="weighted_flags"
        )
        return DEFAULT_OBSERVATION_WEIGHT * len(keys) + sum(
            _as_float(weight) for weight in flags.values()
        )

    def credit(self, record: Mapping[str, object]) -> dict[str, float]:
        family = str(record.get("family") or "general")
        novel_value = 0.0
        novel_keys = 0
        keys = _collection_or_empty(
            record.get("observation_keys"), field_name="observation_keys"
        )
        flags = _mapping_or_empty(
            record.get("weighted_flags"), field_name="weighted_flags"
        )
        for raw_key in keys:
            key = str(raw_key)
            if key not in self.seen:
                self.seen.add(key)
                novel_value += DEFAULT_OBSERVATION_WEIGHT
                novel_keys += 1
        for flag, weight in flags.items():
            pseudo = f"flag:{family}:{flag}"
            if pseudo not in self.seen:
                self.seen.add(pseudo)
                novel_value += _as_float(weight)
        return {"novel_value": novel_value, "novel_observation_keys": novel_keys}


def _frontier_gain(
    router_state: Mapping[str, object] | None,
    family: str,
    *,
    epoch: int,
) -> float:
    """Expected value of spending a speculative slot near this family.

    Generic signals only: whether the base loop has probed the family yet,
    its residual novelty pressure, and a small age bonus. Field names track
    the router_state family schema; the lookup accepts both the raw family
    id and adapter-prefixed forms (e.g. 'scope:artifacts/<family>').
    """

    stats: Mapping[object, object] = {}
    families = (
        router_state.get("families") if isinstance(router_state, Mapping) else None
    )
    if isinstance(families, Mapping):
        exact = families.get(family)
        if isinstance(exact, Mapping):
            stats = exact
        else:
            suffix = "/" + str(family)
            for key, value in families.items():
                if isinstance(value, Mapping) and str(key).endswith(suffix):
                    stats = value
                    break
    score = 1.0
    runs = _as_float(stats.get("runs") or 0.0)
    if runs <= 0:
        score += 1.25
    else:
        novelty_rate = stats.get("novelty_rate")
        if novelty_rate is not None:
            score += min(1.25, max(0.0, _as_float(novelty_rate)) * 1.25)
        accept_rate = stats.get("accept_rate_ema")
        if accept_rate is not None and _as_float(accept_rate) <= 0.5:
            score += 0.25
    score += min(0.5, max(0, int(epoch) - 1) * 0.03)
    return round(score, 4)


class VariantCatalog:
    """Agent-authored variant specs + per-arm consumption bookkeeping.

    Catalog file schema (written by the experiment agent, never by code):
    ``{"schema_version": ..., "specs": [{"spec_id", "seed_family",
    "intent", "ops": [...adapter-defined...], "key_prefix",
    "seed_item_id"?, "priority"?}]}``. The harness treats ``ops`` as opaque.
    """

    def __init__(self, catalog_path: Path, consumption_path: Path) -> None:
        self.catalog_path = Path(catalog_path)
        self.consumption_path = Path(consumption_path)
        consumed = _read_json(self.consumption_path)
        items = _collection_or_empty(consumed, field_name="variant_consumption")
        self.consumed: set[str] = {str(item) for item in items}

    def _specs(self) -> list[dict[str, object]]:
        payload = _read_json(self.catalog_path)
        specs = payload.get("specs") if isinstance(payload, Mapping) else None
        raw_specs = _collection_or_empty(specs, field_name="variant_catalog.specs")
        return [
            dict(spec)
            for spec in raw_specs
            if isinstance(spec, Mapping) and spec.get("spec_id")
        ]

    def pending_rows_snapshot(self) -> list[dict[str, object]]:
        """Read ``catalog_path`` once and return all unconsumed specs."""

        return [
            spec
            for spec in self._specs()
            if str(spec.get("spec_id")) not in self.consumed
        ]

    def pending_snapshot(self) -> tuple[dict[str, list[dict[str, object]]], int]:
        """Read ``catalog_path`` once and index pending specs by ``seed_family``."""

        rows = self.pending_rows_snapshot()
        return _index_specs_by_family(rows), len(rows)

    def pending_for(self, family: str) -> list[dict[str, object]]:
        return [
            spec
            for spec in self._specs()
            if str(spec.get("seed_family") or "") == str(family)
            and str(spec.get("spec_id")) not in self.consumed
        ]

    def pending_count(self) -> int:
        return len(self.pending_rows_snapshot())

    def consume(self, spec_id: str) -> None:
        self.consumed.add(str(spec_id))
        _write_json(self.consumption_path, sorted(self.consumed))

    def restore_consumed(self, spec_ids: Sequence[object]) -> None:
        self.consumed = {str(spec_id) for spec_id in spec_ids if str(spec_id).strip()}
        _write_json(self.consumption_path, sorted(self.consumed))


def _index_specs_by_family(
    specs: Sequence[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    by_family: dict[str, list[dict[str, object]]] = {}
    for spec in specs:
        by_family.setdefault(str(spec.get("seed_family") or ""), []).append(spec)
    return by_family


def _consumed_spec_ids(catalog: object) -> set[str]:
    raw = getattr(catalog, "consumed", ())
    if isinstance(raw, (set, frozenset, list, tuple)):
        return {str(spec_id) for spec_id in raw}
    return set()


def _catalog_pending_index(
    catalog: VariantCatalog,
    families: Sequence[str],
) -> tuple[dict[str, list[dict[str, object]]], list[dict[str, object]] | None, int]:
    """Read catalog state once when snapshot helpers exist; fall back for mocks."""

    rows_fn = getattr(catalog, "pending_rows_snapshot", None)
    if callable(rows_fn):
        rows = cast(Callable[[], list[dict[str, object]]], rows_fn)()
        return _index_specs_by_family(rows), rows, len(rows)
    snapshot_fn = getattr(catalog, "pending_snapshot", None)
    if callable(snapshot_fn):
        typed_snapshot = cast(
            Callable[[], tuple[dict[str, list[dict[str, object]]], int]],
            snapshot_fn,
        )
        by_family, total_pending = typed_snapshot()
        return by_family, None, total_pending
    by_family = {family: catalog.pending_for(family) for family in families}
    return by_family, None, catalog.pending_count()


def select_frontier_work(
    seed_items: Sequence[Mapping[str, object]],
    *,
    catalog: VariantCatalog,
    adapter: object,
    router_state: Mapping[str, object] | None,
    epoch: int,
    max_lanes: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Pick up to ``max_lanes`` agent-authored variant specs to execute.

    Seeds are ranked by the generic frontier gain with an epoch-rotating
    tie-break (multiplicative permutation over a prime modulus) so equal
    gains spread across families over epochs instead of freezing on an
    alphabetical prefix. Spec choice within a family follows the agent's
    ``priority`` then catalog order. The harness only ever schedules specs;
    it cannot invent them.
    """

    def _hash(value: str) -> int:
        return int(hashlib.sha1(value.encode("utf-8")).hexdigest()[:8], 16)

    by_family: dict[str, Mapping[str, object]] = {}
    for item in seed_items:
        family = str(item.get("family") or "")
        if family and family not in by_family:
            by_family[family] = item
    gain_by_family = {
        family: _frontier_gain(router_state, family, epoch=epoch)
        for family in by_family
    }
    ranked = sorted(
        by_family.items(),
        key=lambda pair: (
            -gain_by_family[pair[0]],
            (_hash(pair[0]) * (2 * int(epoch) + 1)) % 1_000_003,
            pair[0],
        ),
    )
    pending_by_family, snapshot_rows, catalog_pending_total = _catalog_pending_index(
        catalog, [family for family, _ in ranked]
    )
    consumed_ids = _consumed_spec_ids(catalog)
    newly_consumed: set[str] = set()
    selected: list[dict[str, object]] = []
    audit_specs: list[dict[str, object]] = []
    compile_variant_raw = getattr(adapter, "compile_variant", None)
    for family, seed in ranked:
        if len(selected) >= max(0, int(max_lanes)):
            break
        pending = sorted(
            (
                spec
                for spec in pending_by_family.get(family, ())
                if str(spec.get("spec_id")) not in consumed_ids
            ),
            key=lambda spec: (
                -_as_float(spec.get("priority") or 0.0),
                str(spec.get("spec_id")),
            ),
        )
        if not pending:
            continue
        spec = pending[0]
        if not callable(compile_variant_raw):
            raise AttributeError(
                f"{type(adapter).__name__!r} object has no attribute 'compile_variant'"
            )
        compile_fn = cast(
            Callable[[dict[str, object], dict[str, object]], Mapping[str, object]],
            compile_variant_raw,
        )
        compiled = dict(compile_fn(spec, dict(seed)))
        compiled.setdefault("is_variant", True)
        compiled.setdefault("variant_spec", spec)
        compiled.setdefault("family", family)
        compiled.setdefault("seed_item_id", seed.get("item_id"))
        compiled.setdefault(
            "concurrency_key", f"{family}:variant:{spec.get('spec_id')}"
        )
        spec_id = str(spec.get("spec_id"))
        catalog.consume(spec_id)
        consumed_ids.add(spec_id)
        newly_consumed.add(spec_id)
        selected.append(compiled)
        audit_specs.append(
            {
                "spec_id": spec.get("spec_id"),
                "seed_family": family,
                "intent": spec.get("intent"),
                "gain": gain_by_family[family],
            }
        )
    if snapshot_rows is not None:
        pending_after = sum(
            1 for row in snapshot_rows if str(row.get("spec_id")) not in consumed_ids
        )
    elif callable(getattr(catalog, "pending_snapshot", None)):
        dropped_rows = sum(
            1
            for fam_specs in pending_by_family.values()
            for row in fam_specs
            if str(row.get("spec_id")) in newly_consumed
        )
        pending_after = max(0, catalog_pending_total - dropped_rows)
    else:
        pending_after = catalog.pending_count()
    audit: dict[str, object] = {
        "requested_lanes": int(max_lanes),
        "admitted": len(selected),
        "catalog_pending_after": pending_after,
        "specs": audit_specs,
    }
    return selected, audit


def emit_frontier_requests(
    path: Path,
    *,
    arm_key: str,
    epoch: int,
    seed_items: Sequence[Mapping[str, object]],
    ledger: NoveltyLedger,
    catalog: VariantCatalog,
    router_state: Mapping[str, object] | None,
    per_family_key_cap: int = 24,
) -> None:
    """Publish what the harness NEEDS from the experiment agent.

    The agent (a Claude/Codex session driving the experiment) reads this
    packet -- seed text plus a digest of already-seen observation keys and
    exhausted specs -- and appends new specs to the variant catalog. This is
    the LoopX interaction pattern: packet out, decision in, no LLM calls
    from code.
    """

    first_seed_by_family: dict[str, Mapping[str, object]] = {}
    for item in seed_items:
        family = str(item.get("family") or "")
        if family and family not in first_seed_by_family:
            first_seed_by_family[family] = item
    pending_by_family, _, catalog_pending_total = _catalog_pending_index(
        catalog, list(first_seed_by_family)
    )
    families: list[dict[str, object]] = []
    for family, item in first_seed_by_family.items():
        seen_for_family = sorted(
            key
            for key in ledger.seen
            if not key.startswith("flag:") or key.startswith(f"flag:{family}:")
        )[: max(1, per_family_key_cap)]
        families.append(
            {
                "family": family,
                "seed_item_id": item.get("item_id"),
                "seed_text": item.get("text"),
                "pending_spec_count": len(pending_by_family.get(family, ())),
                "frontier_gain": _frontier_gain(router_state, family, epoch=epoch),
                "seen_keys_sample": seen_for_family,
            }
        )
    _write_json(
        path,
        {
            "schema_version": FRONTIER_REQUESTS_SCHEMA_VERSION,
            "arm": arm_key,
            "epoch": int(epoch),
            "total_seen_keys": len(ledger.seen),
            "catalog_pending_total": catalog_pending_total,
            "families": sorted(
                families, key=lambda entry: -_as_float(entry["frontier_gain"])
            ),
            "how_to_respond": (
                "Append specs to the variant catalog file: {spec_id, seed_family, "
                "intent, ops (adapter-defined), key_prefix, priority}. The harness "
                "schedules them by frontier gain; it never invents specs."
            ),
        },
    )


def plan_round_robin(
    seed_items: Sequence[Mapping[str, object]], **_: object
) -> list[dict[str, object]]:
    """Baseline planner: every seed, catalog untouched, no ordering signal."""

    return [dict(item) for item in seed_items]


def plan_router_no_prune(
    seed_items: Sequence[Mapping[str, object]],
    *,
    goal_id: str,
    agent_id: str,
    worker_count: int,
    router_state: Mapping[str, object] | None,
    load_profile: Mapping[str, object] | None,
    harness_profile: str = "moe-router",
    branch_fill_policy: str = "confident-prefix",
) -> list[dict[str, object]]:
    """Router-ordered no-prune planner over generic seed items.

    Uses the LoopX worker-branch planner for VALUE ORDERING only, then
    appends every unselected seed as a ranked tail -- sorting without
    pruning: an executable seed never loses its slot to low confidence.
    """

    by_id: dict[str, dict[str, object]] = {}
    duplicate_items: list[dict[str, object]] = []
    for item in seed_items:
        item_id = str(item.get("item_id"))
        if item_id in by_id:
            duplicate_items.append(dict(item))
        else:
            by_id[item_id] = dict(item)
    todos = [
        {
            "todo_id": item_id,
            "text": str(item.get("text") or item.get("item_id")),
            "status": "open",
            "task_class": "advancement_task",
            "required_write_scopes": [f"artifacts/{item.get('family')}/**"],
            "index": index,
        }
        for index, (item_id, item) in enumerate(by_id.items())
    ]
    plan = build_explore_worker_branch_plan(
        goal_id=goal_id,
        todos=todos,
        agent_id=agent_id,
        # This standalone benchmark arm opts in explicitly and consumes value
        # ordering only; spawn stays disallowed, so the plan carries no
        # suggested claim/lease commands.
        orchestration={"explore_harness": {"enabled": True}},
        worker_width=worker_count,
        harness_profile=harness_profile,
        branch_fill_policy=branch_fill_policy,
        router_state=router_state,
        load_profile=load_profile,
    )
    ordered: list[dict[str, object]] = []
    used: set[str] = set()
    for lane_key in ("selected_worker_branches", "rejected_worker_branches"):
        raw_branches = plan.get(lane_key)
        if not isinstance(raw_branches, Sequence) or isinstance(
            raw_branches, (str, bytes, bytearray)
        ):
            continue
        for branch in raw_branches:
            if not isinstance(branch, Mapping):
                continue
            raw_todo_ids = branch.get("todo_ids")
            if not isinstance(raw_todo_ids, Sequence) or isinstance(
                raw_todo_ids, (str, bytes, bytearray)
            ):
                continue
            for raw_todo_id in raw_todo_ids:
                todo_id = str(raw_todo_id)
                if todo_id in by_id and todo_id not in used:
                    ordered.append(by_id[todo_id])
                    used.add(todo_id)
    for item_id, item in by_id.items():
        if item_id not in used:
            ordered.append(item)
            used.add(item_id)
    # Preserve malformed duplicates for the episode grouping boundary. The
    # planner still reasons over unique ids, but no-prune means it must not
    # silently erase an input that record/fatal policy needs to classify.
    ordered.extend(duplicate_items)
    return ordered


def initial_arm_router_state() -> dict[str, object]:
    """Return a mutable initial router state dictionary for one harness arm."""

    return dict(initial_router_state())


def update_router_epoch_feedback(
    *,
    current_router_state: Mapping[str, object] | None,
    records: Sequence[Mapping[str, object]],
    seed_items: Sequence[Mapping[str, object]],
    lanes: Sequence[HarnessLaneRecord],
    episode_mode: str | None,
    epoch: int,
    epoch_wall: float,
    run_root: Path,
    arm_key: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Observe completed epoch probes, persist updated router state, and compute load profile."""

    if episode_mode is not None:
        probes: list[Mapping[str, object]] = list(build_router_probes(records))
    else:
        probes = [
            {
                "family": str(record.get("family") or "general"),
                "duration_minutes": record.get("duration_minutes"),
                "observation_keys": record.get("observation_keys") or [],
                "weighted_flags": record.get("weighted_flags") or {},
                "accepted": bool(record.get("accepted")),
                "retryable_infra_error": bool(record.get("retryable_infra_error")),
            }
            for record in records
        ]
    current_router = (
        dict(current_router_state)
        if current_router_state is not None
        else initial_arm_router_state()
    )
    observed = observe_epoch(current_router, epoch=epoch, probes=probes)
    next_router = dict(
        advance_epoch(
            observed,
            epoch=epoch,
            eligible_families=sorted(
                {str(item.get("family")) for item in seed_items if item.get("family")}
            ),
        )
    )
    _write_json(run_root / f"router_state_{arm_key}.json", next_router)
    max_lane_wall = max(
        (_as_float(lane.get("wall_minutes") or 0.0) for lane in lanes), default=0.0
    )
    load_profile: dict[str, object] = {
        "source": f"observed_epoch_{epoch:02d}_{arm_key}",
        "parallel_wall_minutes": epoch_wall,
        "max_branch_minutes": max_lane_wall,
        "branch_count": len(lanes),
    }
    return next_router, load_profile
