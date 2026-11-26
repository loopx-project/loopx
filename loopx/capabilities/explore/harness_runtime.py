from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import concurrent.futures
from enum import Enum
from functools import partial
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Protocol, runtime_checkable

from .episode_runtime import (
    RECORD_KIND_STANDALONE,
    build_episode_execution_units,
    build_router_probes,
    combine_execution_metrics,
    execute_episode_group,
    item_concurrency_keys,
    recoverable_episode_mode,
    summarize_execution_records,
)
from .harness_checkpoint import (
    HARNESS_CHECKPOINT_SCHEMA_VERSION,
    build_arm_checkpoint,
    load_arm_checkpoint,
    write_arm_checkpoint,
)
from .router_state import (
    advance_epoch,
    initial_router_state,
    observe_epoch,
)
from .worker_branch_plan import build_explore_worker_branch_plan

"""Software-agnostic exploration harness runtime.

This module is the generic half of a two-layer design:

- **This layer (LoopX)** owns scheduling and bookkeeping: independent
  wall-clock budget arms, a shared work queue with concurrency keys, the
  first-seen novelty ledger with adapter-injected flag weights, router-state
  feedback, frontier *scheduling* (when to spend speculative slots and on
  which seed families), anytime checkpoints, and generic metric aggregation.

- **The experiment layer** (driven by a Claude/Codex session) owns every
  domain fact: how to execute a work item, what the observation keys mean,
  how failures are classified, and -- crucially -- WHAT the speculative
  variants are. Variant strategy is authored by the agent as a data file
  (the variant catalog), never generated here.

Hard rules, in the spirit of the wider LoopX design:
- this module never calls an LLM and never imports domain vocabulary;
- variant intelligence travels through files: the harness EMITS
  ``frontier_requests_<arm>.json`` (seed text + seen-observation digest) and
  CONSUMES ``variant_catalog.json`` entries the agent wrote after reading
  those requests -- the same packet-out / decision-in contract the LoopX
  planner uses;
- novelty is only ever credited from observation keys and weighted flags the
  adapter reports; specs carry intent, not novelty.

Adapter protocol (duck-typed; see the experiment layer for a reference
implementation):

- ``list_seed_items() -> list[dict]``: fresh seed work items each epoch,
  each with ``item_id``, ``text``, ``family``; optional ``payload``.
- ``execute(item, *, run_root, arm, epoch, branch_id) -> dict``: run one work
  item and return an observation record with ``item_id``, ``family``,
  ``observation_keys`` (list[str]), ``weighted_flags`` ({name: weight} for
  true flags), ``accepted`` (bool), ``duration_minutes`` (float),
  ``retryable_infra_error`` (bool), plus any adapter-private fields.
- ``compile_variant(spec, seed_item) -> dict``: turn an agent-authored
  variant spec into an executable work item (``is_variant`` True,
  ``variant_spec`` attached, own ``concurrency_key``).
- ``flag_weights`` is implicit: the adapter reports weights per record, so
  the harness never holds a domain weight table.
- ``item_failure_policy`` may be set to ``"fatal"`` on the adapter to make
  execution exceptions propagate; the default ``"record"`` policy emits a
  zero-value structured observation and keeps independent lanes running.
- Adapters that can checkpoint an expensive shared execution prefix may
  optionally implement all three recoverable-episode methods:
  ``prepare_episode_group(seed_item, episode_items, **context)`` returns
  ``{handle, prefix_record, checkpoint_ref?}``, ``execute_episode(handle,
  item, **context)`` returns a suffix-only observation, and
  ``release_episode_group(handle, **context)`` releases adapter-owned state.
  Returning ``None`` from prepare is an explicit, side-effect-free fallback
  to legacy ``execute`` for that group (the only case where an episode
  adapter needs ``execute`` at all). The adapter owns all restore mechanics.
  Distinct groups run concurrently on separate workers, so the three methods
  must be thread-safe across groups; concurrently active handles must not alias
  mutable state, while safe sequential or immutable reuse remains valid.
  Malformed grouping inputs (empty or duplicate seed/variant ids, id
  collisions, or variants naming an unknown seed) degrade through the item
  failure policy like any execution error: structured records under ``record``,
  or a pre-dispatch raised error under ``fatal``.
"""


HARNESS_ARM_SCHEMA_VERSION = "loopx_explore_harness_arm_v0"
VARIANT_CATALOG_SCHEMA_VERSION = "loopx_explore_variant_catalog_v0"
FRONTIER_REQUESTS_SCHEMA_VERSION = "loopx_explore_frontier_requests_v0"
HARNESS_RUNTIME_POLICY_SCHEMA_VERSION = "loopx_explore_harness_runtime_policy_v0"

class ItemFailurePolicy(str, Enum):
    RECORD = "record"
    FATAL = "fatal"


ITEM_FAILURE_POLICY_RECORD = ItemFailurePolicy.RECORD.value
ITEM_FAILURE_POLICY_FATAL = ItemFailurePolicy.FATAL.value
ITEM_FAILURE_POLICIES = {policy.value for policy in ItemFailurePolicy}

DEFAULT_OBSERVATION_WEIGHT = 1.0
_MIN_DURATION_MINUTES = 0.05

Payload = dict[str, object]
PayloadView = Mapping[str, object]
ExecuteFn = Callable[..., PayloadView | Sequence[PayloadView]]


@runtime_checkable
class _SeedAdapter(Protocol):
    def list_seed_items(self) -> Sequence[PayloadView]: ...


@runtime_checkable
class _VariantAdapter(Protocol):
    def compile_variant(self, spec: Payload, seed_item: Payload) -> PayloadView: ...


@runtime_checkable
class _LegacyExecutor(Protocol):
    def execute(
        self, item: Payload, *, run_root: Path, arm: str, epoch: int, branch_id: str
    ) -> PayloadView | Sequence[PayloadView]: ...


def _missing_attr(adapter: object, attr: str) -> AttributeError:
    return AttributeError(f"{type(adapter).__name__!r} object has no attribute {attr!r}")


def _mapping(value: object) -> PayloadView:
    return value if isinstance(value, Mapping) else {}


def _optional_payload(value: object) -> Payload | None:
    return dict(value) if isinstance(value, Mapping) else None


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _payload_list(value: object) -> list[Payload]:
    return [dict(item) for item in _sequence(value) if isinstance(item, Mapping)]


def _as_float(value: object, default: float = 0.0) -> float:
    if not value:
        return default
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")


def _first_seed_by_family(seed_items: Sequence[PayloadView]) -> dict[str, PayloadView]:
    by_family: dict[str, PayloadView] = {}
    for item in seed_items:
        family = str(item.get("family") or "")
        if family and family not in by_family:
            by_family[family] = item
    return by_family


def _standalone_metric_records(
    records: Sequence[Payload], episode_mode: str | None
) -> list[Payload]:
    if episode_mode is not None:
        return list(records)
    return [
        {**record, "execution_group_id": None, "record_kind": RECORD_KIND_STANDALONE}
        for record in records
    ]


def _now_perf_minutes(start: float) -> float:
    return (time.perf_counter() - start) / 60.0


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> object:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return None


def _normalize_item_failure_policy(value: object) -> str:
    if isinstance(value, ItemFailurePolicy):
        return value.value
    policy = str(value or ITEM_FAILURE_POLICY_RECORD).strip().lower().replace("_", "-")
    if policy not in ITEM_FAILURE_POLICIES:
        raise ValueError(
            "item_failure_policy must be one of: "
            + ", ".join(sorted(ITEM_FAILURE_POLICIES))
        )
    return policy


def _adapter_error_record(
    item: PayloadView,
    error: Exception,
    *,
    duration_minutes: float,
) -> Payload:
    message = " ".join(str(error).split())
    spec = item.get("variant_spec")
    return {
        "item_id": item.get("item_id"),
        "family": item.get("family"),
        "is_variant": bool(item.get("is_variant")),
        # The error factory must never raise itself: a malformed variant_spec
        # would otherwise escalate one recordable failure into a lost lane.
        "variant_spec_id": (
            spec.get("spec_id")
            if item.get("is_variant") and isinstance(spec, Mapping)
            else None
        ),
        "execution_status": "adapter_error",
        "item_failure_policy": ITEM_FAILURE_POLICY_RECORD,
        "accepted": False,
        "observation_keys": [],
        "weighted_flags": {},
        "duration_minutes": max(_MIN_DURATION_MINUTES, float(duration_minutes)),
        "retryable_infra_error": bool(getattr(error, "retryable_infra_error", False)),
        "adapter_error": {
            "schema_version": "loopx_explore_adapter_error_v0",
            "type": type(error).__name__,
            "message": message[:240],
        },
    }


class NoveltyLedger:
    """First-seen ledger: raw observation keys global, flags family-scoped.

    Raw keys are ledgered globally so relabeling an item under a different
    family can never re-mint an already-seen key; weighted flags are
    family-scoped facts. Weights come from the record itself (adapter-owned).
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def raw_value(self, record: PayloadView) -> float:
        return DEFAULT_OBSERVATION_WEIGHT * len(_sequence(record.get("observation_keys"))) + sum(
            _as_float(weight) for weight in _mapping(record.get("weighted_flags")).values()
        )

    def credit(self, record: PayloadView) -> dict[str, float]:
        family = str(record.get("family") or "general")
        novel_value = 0.0
        novel_keys = 0
        for raw_key in _sequence(record.get("observation_keys")):
            key = str(raw_key)
            if key not in self.seen:
                self.seen.add(key)
                novel_value += DEFAULT_OBSERVATION_WEIGHT
                novel_keys += 1
        for flag, weight in _mapping(record.get("weighted_flags")).items():
            pseudo = f"flag:{family}:{flag}"
            if pseudo not in self.seen:
                self.seen.add(pseudo)
                novel_value += _as_float(weight)
        return {"novel_value": novel_value, "novel_observation_keys": novel_keys}


def _normalize_queue_outcome(outcome: object, item: PayloadView) -> list[Payload]:
    if isinstance(outcome, Mapping):
        batch = [dict(outcome)]
    elif isinstance(outcome, Sequence) and not isinstance(outcome, (str, bytes)):
        batch = []
        for candidate in outcome:
            if not isinstance(candidate, Mapping):
                raise TypeError(
                    "queue execute sequences must contain dict observation records"
                )
            batch.append(dict(candidate))
    else:
        raise TypeError(
            "queue execute must return a dict or sequence of dict observations"
        )
    if not batch:
        raise TypeError("queue execute must return at least one observation")
    for record in batch:
        record.setdefault("item_id", item.get("item_id"))
        record.setdefault("family", item.get("family"))
        record.setdefault("is_variant", bool(item.get("is_variant")))
        if item.get("is_variant"):
            record.setdefault(
                "variant_spec_id", _mapping(item.get("variant_spec")).get("spec_id")
            )
    return batch


def run_queue_epoch(
    items: Sequence[PayloadView],
    *,
    execute: ExecuteFn,
    worker_count: int,
    run_root: Path,
    arm: str,
    epoch: int,
    stagger_seconds: float = 0.0,
    item_failure_policy: str = ITEM_FAILURE_POLICY_RECORD,
) -> list[Payload]:
    """Drain one epoch's work items through a shared pull queue.

    Items may declare one ``concurrency_key`` or several ``concurrency_keys``.
    Any overlap with an in-flight item waits until all keys free up (the generic
    form of "same write scope must serialize"); disjoint items run concurrently
    up to ``worker_count`` pullers. Returns per-worker lane records.
    """

    failure_policy = _normalize_item_failure_policy(item_failure_policy)
    lock = threading.Lock()
    pending: list[Payload] = [dict(item) for item in items]
    in_flight: set[str] = set()

    def pull_next() -> Payload | None:
        with lock:
            for index, item in enumerate(pending):
                keys = item_concurrency_keys(item)
                if any(key in in_flight for key in keys):
                    continue
                pending.pop(index)
                in_flight.update(keys)
                return item
            return None

    def worker(worker_index: int) -> Payload:
        branch_id = f"queue_worker_{worker_index + 1:02d}"
        started_at = time.perf_counter()
        if worker_index and stagger_seconds > 0:
            time.sleep(min(30.0, worker_index * stagger_seconds))
        results: list[Payload] = []
        while True:
            item = pull_next()
            if item is None:
                with lock:
                    if not pending:
                        break
                time.sleep(0.5)
                continue
            keys = item_concurrency_keys(item)
            item_started = time.perf_counter()
            try:
                outcome = execute(
                    item, run_root=run_root, arm=arm, epoch=epoch, branch_id=branch_id
                )
                batch = _normalize_queue_outcome(outcome, item)
            except Exception as error:
                if failure_policy == ITEM_FAILURE_POLICY_FATAL:
                    raise
                batch = [
                    _adapter_error_record(
                        item, error, duration_minutes=_now_perf_minutes(item_started)
                    )
                ]
            finally:
                if keys:
                    with lock:
                        in_flight.difference_update(keys)
            results.extend(batch)
        return {
            "branch_id": branch_id,
            "launch_index": worker_index,
            "wall_minutes": round(_now_perf_minutes(started_at), 3),
            "item_ids": [r.get("item_id") for r in results],
            "families": [r.get("family") for r in results],
            "results": results,
        }

    thread_count = max(1, min(int(worker_count), len(pending) or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=thread_count) as pool:
        futures = [pool.submit(worker, index) for index in range(thread_count)]
        lanes = [future.result() for future in concurrent.futures.as_completed(futures)]
    lanes.sort(key=lambda lane: str(lane.get("branch_id")))
    return lanes


def _frontier_gain(
    router_state: PayloadView | None, family: str, *, epoch: int
) -> float:
    """Expected value of spending a speculative slot near this family.

    Generic signals only: whether the base loop has probed the family yet,
    its residual novelty pressure, and a small age bonus. Field names track
    the router_state family schema; the lookup accepts both the raw family
    id and adapter-prefixed forms (e.g. 'scope:artifacts/<family>').
    """

    stats: PayloadView = {}
    families = _mapping(router_state).get("families") if isinstance(router_state, Mapping) else None
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
    runs = _as_float(stats.get("runs"))
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
        self.consumed: set[str] = {str(spec_id) for spec_id in _sequence(consumed)}

    def _specs(self) -> list[Payload]:
        payload = _mapping(_read_json(self.catalog_path))
        return [
            dict(spec)
            for spec in _sequence(payload.get("specs"))
            if isinstance(spec, Mapping) and spec.get("spec_id")
        ]

    def pending_for(self, family: str) -> list[Payload]:
        return [
            spec
            for spec in self._specs()
            if str(spec.get("seed_family") or "") == str(family)
            and str(spec.get("spec_id")) not in self.consumed
        ]

    def pending_count(self) -> int:
        return len([s for s in self._specs() if str(s.get("spec_id")) not in self.consumed])

    def consume(self, spec_id: str) -> None:
        self.consumed.add(str(spec_id))
        _write_json(self.consumption_path, sorted(self.consumed))

    def restore_consumed(self, spec_ids: Sequence[object]) -> None:
        self.consumed = {str(spec_id) for spec_id in spec_ids if str(spec_id).strip()}
        _write_json(self.consumption_path, sorted(self.consumed))


def select_frontier_work(
    seed_items: Sequence[PayloadView],
    *,
    catalog: VariantCatalog,
    adapter: _VariantAdapter,
    router_state: PayloadView | None,
    epoch: int,
    max_lanes: int,
) -> tuple[list[Payload], Payload]:
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

    by_family = _first_seed_by_family(seed_items)
    ranked = sorted(
        (
            (
                _frontier_gain(router_state, family, epoch=epoch),
                (_hash(family) * (2 * int(epoch) + 1)) % 1_000_003,
                family,
                seed,
            )
            for family, seed in by_family.items()
        ),
        key=lambda entry: (-entry[0], entry[1], entry[2]),
    )
    selected: list[Payload] = []
    audit_specs: list[Payload] = []
    for gain, _, family, seed in ranked:
        if len(selected) >= max(0, int(max_lanes)):
            break
        pending = sorted(
            catalog.pending_for(family),
            key=lambda spec: (-_as_float(spec.get("priority")), str(spec.get("spec_id"))),
        )
        if not pending:
            continue
        spec = pending[0]
        item = dict(adapter.compile_variant(spec, dict(seed)))
        item.setdefault("is_variant", True)
        item.setdefault("variant_spec", spec)
        item.setdefault("family", family)
        item.setdefault("seed_item_id", seed.get("item_id"))
        item.setdefault("concurrency_key", f"{family}:variant:{spec.get('spec_id')}")
        catalog.consume(str(spec.get("spec_id")))
        selected.append(item)
        audit_specs.append(
            {
                "spec_id": spec.get("spec_id"),
                "seed_family": family,
                "intent": spec.get("intent"),
                "gain": gain,
            }
        )
    return selected, {
        "requested_lanes": int(max_lanes),
        "admitted": len(selected),
        "catalog_pending_after": catalog.pending_count(),
        "specs": audit_specs,
    }


def emit_frontier_requests(
    path: Path,
    *,
    arm_key: str,
    epoch: int,
    seed_items: Sequence[PayloadView],
    ledger: NoveltyLedger,
    catalog: VariantCatalog,
    router_state: PayloadView | None,
    per_family_key_cap: int = 24,
) -> None:
    """Publish what the harness NEEDS from the experiment agent.

    The agent (a Claude/Codex session driving the experiment) reads this
    packet -- seed text plus a digest of already-seen observation keys and
    exhausted specs -- and appends new specs to the variant catalog. This is
    the LoopX interaction pattern: packet out, decision in, no LLM calls
    from code.
    """

    families: list[Payload] = []
    for family, item in _first_seed_by_family(seed_items).items():
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
                "pending_spec_count": len(catalog.pending_for(family)),
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
            "catalog_pending_total": catalog.pending_count(),
            "families": sorted(families, key=lambda f: -_as_float(f["frontier_gain"])),
            "how_to_respond": (
                "Append specs to the variant catalog file: {spec_id, seed_family, "
                "intent, ops (adapter-defined), key_prefix, priority}. The harness "
                "schedules them by frontier gain; it never invents specs."
            ),
        },
    )


def plan_round_robin(seed_items: Sequence[PayloadView], **_: object) -> list[Payload]:
    """Baseline planner: every seed, catalog untouched, no ordering signal."""

    return [dict(item) for item in seed_items]


def plan_router_no_prune(
    seed_items: Sequence[PayloadView],
    *,
    goal_id: str,
    agent_id: str,
    worker_count: int,
    router_state: PayloadView | None,
    load_profile: PayloadView | None,
    harness_profile: str = "moe-router",
    branch_fill_policy: str = "confident-prefix",
) -> list[Payload]:
    """Router-ordered no-prune planner over generic seed items.

    Uses the LoopX worker-branch planner for VALUE ORDERING only, then
    appends every unselected seed as a ranked tail -- sorting without
    pruning: an executable seed never loses its slot to low confidence.
    """

    by_id: dict[str, Payload] = {}
    duplicate_items: list[Payload] = []
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
    ordered: list[Payload] = []
    used: set[str] = set()
    for branch_key in ("selected_worker_branches", "rejected_worker_branches"):
        for branch in _sequence(plan.get(branch_key)):
            for raw_id in _sequence(_mapping(branch).get("todo_ids")):
                todo_id = str(raw_id)
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


def _arm_runtime_signature(
    adapter: object,
    *,
    arm_key: str,
    worker_count: int,
    use_router: bool,
    frontier_max_lanes: int,
    variant_catalog_enabled: bool,
    goal_id: str,
    agent_id: str,
    item_failure_policy: str,
    episode_mode: str | None,
) -> Payload:
    adapter_type = f"{type(adapter).__module__}.{type(adapter).__qualname__}"
    return {
        "arm_key": str(arm_key),
        "adapter_type": adapter_type,
        "worker_count": max(1, int(worker_count)),
        "use_router": bool(use_router),
        "frontier_max_lanes": max(0, int(frontier_max_lanes)),
        "variant_catalog_enabled": bool(variant_catalog_enabled),
        "goal_id": str(goal_id),
        "agent_id": str(agent_id),
        "item_failure_policy": item_failure_policy,
        # Missing keys in legacy manifests read as None, so old standalone arms
        # remain compatible while a recoverable arm cannot resume as standalone.
        "episode_mode": episode_mode,
    }


def run_budget_arm(
    adapter: object,
    *,
    arm_key: str,
    run_root: Path,
    budget_minutes: float,
    worker_count: int,
    max_epochs: int = 1000,
    use_router: bool = False,
    frontier_max_lanes: int = 0,
    variant_catalog_path: Path | None = None,
    goal_id: str = "exploration",
    agent_id: str = "harness",
    stagger_seconds: float = 0.0,
    duration_guard_factor: float = 1.15,
    duration_guard_recent: int = 3,
    resumable: bool = False,
    resume: bool = False,
    checkpoint_path: Path | None = None,
    item_failure_policy: str | None = None,
) -> Payload:
    """Run ONE arm alone against its wall-clock budget; return arm payload.

    ``use_router=False`` is the blind baseline (round-robin sweep, no
    variants). ``use_router=True`` adds router-ordered no-prune planning,
    router-state feedback, and -- when a variant catalog path is given --
    agent-authored frontier variants appended to each epoch's queue. Restart
    is opt-in and only trusts the validated epoch-boundary checkpoint manifest;
    rolling progress remains an observability surface.
    """

    run_root = Path(run_root)
    failure_policy = _normalize_item_failure_policy(
        item_failure_policy
        if item_failure_policy is not None
        else getattr(adapter, "item_failure_policy", None)
    )
    episode_mode = recoverable_episode_mode(adapter)
    checkpoint_enabled = bool(resumable or resume or checkpoint_path is not None)
    checkpoint_file = (
        Path(checkpoint_path)
        if checkpoint_path is not None
        else run_root / f"arm_checkpoint_{arm_key}.json"
    )
    runtime_signature = _arm_runtime_signature(
        adapter,
        arm_key=arm_key,
        worker_count=worker_count,
        use_router=use_router,
        frontier_max_lanes=frontier_max_lanes,
        variant_catalog_enabled=variant_catalog_path is not None,
        goal_id=goal_id,
        agent_id=agent_id,
        item_failure_policy=failure_policy,
        episode_mode=episode_mode,
    )
    ledger = NoveltyLedger()
    router_state: Payload | None = initial_router_state() if use_router else None
    load_profile: Payload | None = None
    catalog = (
        VariantCatalog(Path(variant_catalog_path), run_root / f"variant_consumption_{arm_key}.json")
        if variant_catalog_path
        else None
    )
    epochs: list[Payload] = []
    checkpoints: list[Payload] = []
    coverage_first_seen: dict[str, float] = {}
    raw_cum = 0.0
    novel_cum = 0.0
    variant_records_cum = 0
    next_epoch = 1
    elapsed_offset = 0.0
    if resume:
        state = load_arm_checkpoint(checkpoint_file, expected_signature=runtime_signature)
        epochs = _payload_list(state.get("epochs"))
        checkpoints = _payload_list(state.get("checkpoints"))
        ledger.seen = {str(key) for key in _sequence(state.get("novelty_seen"))}
        router_state = _optional_payload(state.get("router_state"))
        load_profile = _optional_payload(state.get("load_profile"))
        coverage_first_seen = {
            str(family): _as_float(minutes)
            for family, minutes in _mapping(state.get("coverage_first_seen")).items()
        }
        raw_cum = _as_float(state.get("raw_value_total"))
        novel_cum = _as_float(state.get("novel_value_total"))
        variant_records_cum = int(_as_float(state.get("variant_records_total")))
        next_epoch = int(_as_float(state.get("next_epoch")))
        elapsed_offset = _as_float(state.get("elapsed_minutes"))
        if catalog is not None:
            catalog.restore_consumed(_sequence(state.get("catalog_consumed")))
    elif checkpoint_enabled and catalog is not None:
        # Reusing a run_root is not an implicit restart contract. A fresh arm
        # starts with fresh consumption; only resume restores prior attempts.
        catalog.restore_consumed([])
    if not resume:
        checkpoint_file.unlink(missing_ok=True)
    execution_metric_fields = set(combine_execution_metrics([]))
    historical_execution_metrics: list[Payload] = []
    for completed_epoch in epochs:
        stored_metrics = completed_epoch.get("execution_metrics")
        if isinstance(stored_metrics, Mapping) and execution_metric_fields.issubset(stored_metrics):
            historical_execution_metrics.append(dict(stored_metrics))
            continue
        historical_records = [
            dict(record)
            for lane in _sequence(completed_epoch.get("lanes"))
            for record in _sequence(_mapping(lane).get("results"))
            if isinstance(record, Mapping)
        ]
        historical_execution_metrics.append(
            summarize_execution_records(_standalone_metric_records(historical_records, episode_mode))
        )
    execution_metrics_cum: Payload = dict(combine_execution_metrics(historical_execution_metrics))
    stop_reason = "max_epochs"
    started = time.perf_counter()
    for epoch in range(next_epoch, int(max_epochs) + 1):
        elapsed = elapsed_offset + _now_perf_minutes(started)
        remaining = float(budget_minutes) - elapsed
        if epochs and remaining <= 0:
            stop_reason = "budget_exhausted"
            break
        if epochs and duration_guard_factor > 0:
            recent = epochs[-max(1, int(duration_guard_recent)) :]
            estimated = max(
                _as_float(entry.get("epoch_wall_minutes")) for entry in recent
            ) * float(duration_guard_factor)
            if remaining < estimated:
                stop_reason = "duration_guard"
                break
        if not isinstance(adapter, _SeedAdapter):
            raise _missing_attr(adapter, "list_seed_items")
        seed_items = [dict(item) for item in adapter.list_seed_items()]
        if not seed_items:
            stop_reason = "seed_queue_empty"
            break
        if episode_mode is not None and failure_policy == ITEM_FAILURE_POLICY_FATAL:
            # Router planning keys by item_id and variant compilation calls
            # adapter code, so fatal structural validation must see the raw
            # seeds before either operation can collapse ids or cause effects.
            build_episode_execution_units(seed_items, arm=arm_key, epoch=epoch, fatal_preflight=True)
        if use_router:
            queue_items = plan_router_no_prune(
                seed_items,
                goal_id=goal_id,
                agent_id=agent_id,
                worker_count=worker_count,
                router_state=router_state,
                load_profile=load_profile,
            )
        else:
            queue_items = plan_round_robin(seed_items)
        frontier_audit: Payload | None = None
        if use_router and catalog is not None and int(frontier_max_lanes) > 0:
            if not isinstance(adapter, _VariantAdapter):
                raise _missing_attr(adapter, "compile_variant")
            variant_items, frontier_audit = select_frontier_work(
                seed_items,
                catalog=catalog,
                adapter=adapter,
                router_state=router_state,
                epoch=epoch,
                max_lanes=frontier_max_lanes,
            )
            queue_items = queue_items + variant_items
            emit_frontier_requests(
                run_root / f"frontier_requests_{arm_key}.json",
                arm_key=arm_key,
                epoch=epoch,
                seed_items=seed_items,
                ledger=ledger,
                catalog=catalog,
                router_state=router_state,
            )
        epoch_start = time.perf_counter()
        execution_items = queue_items
        if episode_mode is not None:
            execution_items = build_episode_execution_units(
                queue_items,
                arm=arm_key,
                epoch=epoch,
                fatal_preflight=failure_policy == ITEM_FAILURE_POLICY_FATAL,
            )
            execute_epoch: ExecuteFn = partial(
                execute_episode_group,
                adapter=adapter,
                failure_policy=failure_policy,
                fatal_failure_policy=ITEM_FAILURE_POLICY_FATAL,
                error_record_factory=_adapter_error_record,
            )
        else:
            if not isinstance(adapter, _LegacyExecutor):
                raise _missing_attr(adapter, "execute")
            # Accessed lazily so an episode-only adapter (no legacy execute)
            # is valid as long as prepare_episode_group never returns None.
            execute_epoch = adapter.execute

        lanes = run_queue_epoch(
            execution_items,
            execute=execute_epoch,
            worker_count=worker_count,
            run_root=run_root,
            arm=arm_key,
            epoch=epoch,
            stagger_seconds=stagger_seconds,
            item_failure_policy=failure_policy,
        )
        epoch_wall = _now_perf_minutes(epoch_start)
        epoch_raw = 0.0
        epoch_novel = 0.0
        records: list[Payload] = []
        for lane in lanes:
            for raw_record in _sequence(lane.get("results")):
                if not isinstance(raw_record, dict):
                    continue
                credit = ledger.credit(raw_record)
                raw_value = ledger.raw_value(raw_record)
                raw_record["novel_value"] = credit["novel_value"]
                raw_record["raw_value"] = raw_value
                epoch_raw += raw_value
                epoch_novel += credit["novel_value"]
                if raw_record.get("is_variant"):
                    variant_records_cum += 1
                records.append(raw_record)
        raw_cum += epoch_raw
        novel_cum += epoch_novel
        metric_records = _standalone_metric_records(records, episode_mode)
        epoch_execution_metrics = summarize_execution_records(metric_records)
        execution_metrics_cum = dict(
            combine_execution_metrics([execution_metrics_cum, epoch_execution_metrics])
        )
        elapsed_after = elapsed_offset + _now_perf_minutes(started)
        for record in records:
            family = str(record.get("family") or "")
            if family:
                coverage_first_seen.setdefault(family, round(elapsed_after, 3))
        if use_router:
            assert router_state is not None
            probes = build_router_probes(metric_records)
            router_state = observe_epoch(router_state, epoch=epoch, probes=probes)
            router_state = advance_epoch(
                router_state,
                epoch=epoch,
                eligible_families=sorted(
                    {str(item.get("family")) for item in seed_items if item.get("family")}
                ),
            )
            _write_json(run_root / f"router_state_{arm_key}.json", router_state)
            max_lane_wall = max(
                (_as_float(lane.get("wall_minutes")) for lane in lanes), default=0.0
            )
            load_profile = {
                "source": f"observed_epoch_{epoch:02d}_{arm_key}",
                "parallel_wall_minutes": epoch_wall,
                "max_branch_minutes": max_lane_wall,
                "branch_count": len(lanes),
            }
        checkpoints.append(
            {
                "epoch": epoch,
                "elapsed_minutes": round(elapsed_after, 3),
                "raw_value_cum": round(raw_cum, 3),
                "novel_value_cum": round(novel_cum, 3),
                "coverage_count_cum": len(coverage_first_seen),
                "variant_records_cum": variant_records_cum,
                "requested_worker_minutes_cum": round(elapsed_after * worker_count, 3),
                "execution_metrics_cum": dict(execution_metrics_cum),
            }
        )
        epochs.append(
            {
                "schema_version": HARNESS_ARM_SCHEMA_VERSION,
                "epoch": epoch,
                "epoch_wall_minutes": round(epoch_wall, 3),
                "queue_size": len(queue_items),
                "execution_unit_count": len(execution_items),
                "execution_metrics": epoch_execution_metrics,
                "frontier": frontier_audit,
                "lanes": lanes,
            }
        )
        if checkpoint_enabled:
            write_arm_checkpoint(
                checkpoint_file,
                build_arm_checkpoint(
                    arm_key=arm_key,
                    runtime_signature=runtime_signature,
                    next_epoch=epoch + 1,
                    elapsed_minutes=elapsed_after,
                    epochs=epochs,
                    checkpoints=checkpoints,
                    novelty_seen=sorted(ledger.seen),
                    router_state=router_state,
                    load_profile=load_profile,
                    catalog_consumed=sorted(catalog.consumed) if catalog else [],
                    coverage_first_seen=coverage_first_seen,
                    raw_value_total=raw_cum,
                    novel_value_total=novel_cum,
                    variant_records_total=variant_records_cum,
                ),
            )
        _write_json(
            run_root / f"rolling_progress_{arm_key}.json",
            {"arm": arm_key, "completed_epochs": len(epochs), "last_checkpoint": checkpoints[-1]},
        )
    return {
        "schema_version": HARNESS_ARM_SCHEMA_VERSION,
        "arm_key": arm_key,
        "budget_minutes": float(budget_minutes),
        "elapsed_minutes": round(elapsed_offset + _now_perf_minutes(started), 3),
        "stop_reason": stop_reason,
        "epoch_count": len(epochs),
        "raw_value_total": round(raw_cum, 3),
        "novel_value_total": round(novel_cum, 3),
        "variant_records_total": variant_records_cum,
        "execution_metrics": execution_metrics_cum,
        "coverage_count": len(coverage_first_seen),
        "coverage_first_seen_minutes": coverage_first_seen,
        "checkpoints": checkpoints,
        "epochs": epochs,
        "resume": {
            "schema_version": "loopx_explore_harness_resume_v0",
            "enabled": checkpoint_enabled,
            "resumed": bool(resume),
            "checkpoint_schema_version": HARNESS_CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_path": str(checkpoint_file) if checkpoint_enabled else None,
            "restored_epoch_count": next_epoch - 1 if resume else 0,
        },
        "runtime_policy": {
            "schema_version": HARNESS_RUNTIME_POLICY_SCHEMA_VERSION,
            "item_failure_policy": failure_policy,
            "item_exception_isolation": failure_policy == ITEM_FAILURE_POLICY_RECORD,
            "episode_execution": {
                "mode": episode_mode or "standalone",
                "adapter_owned_restore": episode_mode is not None,
            },
            "planner_guidance": {
                "retry_backoff": {"enforced": False, "owner": "external_runner"},
                "infra_cooldown": {"enforced": False, "owner": "external_runner"},
            },
        },
    }


def step_auc(
    checkpoints: Sequence[PayloadView], *, value_key: str, horizon_minutes: float
) -> float:
    """Step-integrate a cumulative anytime curve to the budget horizon."""

    auc = 0.0
    prev_t = 0.0
    prev_v = 0.0
    for checkpoint in checkpoints:
        t = min(_as_float(checkpoint.get("elapsed_minutes")), float(horizon_minutes))
        if t > prev_t:
            auc += prev_v * (t - prev_t)
            prev_t = t
        prev_v = _as_float(checkpoint.get(value_key))
    if float(horizon_minutes) > prev_t:
        auc += prev_v * (float(horizon_minutes) - prev_t)
    return round(auc, 3)


def aggregate_arms(
    arms: Mapping[str, PayloadView],
    *,
    budget_minutes: float,
    worker_count: int,
    coverage_denominator: int | None = None,
) -> Payload:
    """Generic two-arm (or N-arm) comparison on novelty-first metrics."""

    arm_summaries: Payload = {}
    for arm_key, arm in arms.items():
        checkpoints = _payload_list(arm.get("checkpoints"))
        execution_metrics = _optional_payload(arm.get("execution_metrics")) or {}
        effective_compute = _as_float(execution_metrics.get("effective_compute_minutes"))
        novel_total = _as_float(arm.get("novel_value_total"))
        arm_summaries[arm_key] = {
            "novel_value_endpoint": arm.get("novel_value_total"),
            "novel_value_auc": step_auc(
                checkpoints, value_key="novel_value_cum", horizon_minutes=budget_minutes
            ),
            "raw_value_total": arm.get("raw_value_total"),
            "coverage_count": arm.get("coverage_count"),
            "variant_records_total": arm.get("variant_records_total"),
            "epoch_count": arm.get("epoch_count"),
            "elapsed_minutes": arm.get("elapsed_minutes"),
            "stop_reason": arm.get("stop_reason"),
            "execution_metrics": execution_metrics,
            "novel_value_per_requested_worker_minute": round(
                novel_total / max(0.001, float(budget_minutes) * worker_count), 4
            ),
            "novel_value_per_effective_compute_minute": (
                round(novel_total / effective_compute, 4) if effective_compute > 0 else None
            ),
            "anytime_curve": checkpoints,
        }
    return {
        "schema_version": "loopx_explore_harness_score_v0",
        "budget_minutes_per_arm": float(budget_minutes),
        "worker_count": int(worker_count),
        "coverage_denominator": coverage_denominator,
        "arms": arm_summaries,
    }
