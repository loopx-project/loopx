from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence


ROUTER_STATE_SCHEMA_VERSION = "loopx_explore_router_state_v0"

# Every first-seen observation key is worth one unit; anything richer (domain
# flags and their weights) is the adapter's business and arrives per record as
# ``weighted_flags`` -- this module must stay free of domain vocabulary.
OBSERVATION_WEIGHT = 1.0

DEFAULT_ROUTER_CONFIG: dict[str, Any] = {
    "value_rate_alpha": 0.3,
    "duration_alpha": 0.3,
    "accept_rate_alpha": 0.3,
    "infra_alpha": 0.5,
    "bias_gamma": 0.1,
    "bias_decay": 0.95,
    "bias_clamp": 0.5,
    "coverage_horizon_epochs": 4,
    "surplus_share_factor": 2.0,
    "surplus_novelty_ceiling": 0.1,
    "ucb_coefficient": 0.6,
    "ucb_cap": 1.5,
    "coverage_bonus_cap": 0.5,
    "novelty_floor": 0.05,
    "novelty_debt_threshold": 0.05,
    "infra_penalty_weight": 0.5,
    "cold_start_value_rate": 1.0,
    "cold_start_duration_minutes": 1.0,
    "cold_start_accept_rate": 0.8,
    "routing_multiplier_floor": 0.05,
}

_MIN_DURATION_MINUTES = 0.05


def scope_family_key(family: str) -> str:
    """Canonical router family id for a task family.

    Executing harnesses declare a work item's family through its write scope
    (``artifacts/<family>/**``), which the planner's ``_affinity_key``
    collapses to ``scope:artifacts/<family>``. Keeping this mapping in one
    exported helper ties the harness's family taxonomy and the planner's
    affinity buckets into a single expert id space.
    """

    clean = str(family or "").strip().strip("/")
    return f"scope:artifacts/{clean}" if clean else "topic:general"


# Deprecated alias kept for existing callers; prefer scope_family_key.
family_key_for_probe_task = scope_family_key


def initial_router_state(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(DEFAULT_ROUTER_CONFIG)
    for key, value in dict(config or {}).items():
        if key in merged:
            merged[key] = value
    return {
        "schema_version": ROUTER_STATE_SCHEMA_VERSION,
        "config": merged,
        "updated_epoch": 0,
        "totals": {"dispatches": 0, "observed_epochs": 0},
        "seen_observation_keys": [],
        "families": {},
        "last_epoch": {"epoch": 0, "minutes_by_family": {}, "novel_value_by_family": {}},
    }


def _config(state: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(DEFAULT_ROUTER_CONFIG)
    for key, value in dict(state.get("config") or {}).items():
        if key in merged:
            merged[key] = value
    return merged


def _blank_family() -> dict[str, Any]:
    return {
        "value_rate_ema": None,
        "duration_ema": None,
        "accept_rate_ema": None,
        "novelty_rate": None,
        "infra_ema": 0.0,
        "runs": 0,
        "last_run_epoch": None,
        "bias": 0.0,
    }


def _ema(previous: float | None, sample: float, alpha: float) -> float:
    if previous is None:
        return float(sample)
    return (1.0 - alpha) * float(previous) + alpha * float(sample)


def _flag_pseudo_key(family: str, flag: str) -> str:
    # Flags are family-scoped facts (the same flag raised in family X and
    # family Y is two different signals); raw observation keys are global so
    # that splitting a family can never re-mint an already-seen key as novel.
    return f"flag:{family}:{flag}"


def observe_epoch(
    state: Mapping[str, Any],
    *,
    epoch: int,
    probes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Fold one epoch of probe outcomes into a new router state.

    Each probe mapping carries: ``family`` (canonical family id), ``duration_minutes``,
    ``observation_keys`` (list), ``accepted`` (bool), ``retryable_infra_error``
    (bool), and ``weighted_flags``: ``{flag_name: weight}`` for value-bearing
    flags that are TRUE. A probe that folds several sibling executions (an
    episode group) may additionally carry ``accepted_count`` and
    ``attempt_count`` so acceptance is weighted by the number of suffix
    attempts. Invalid or absent counts fall back to the legacy ``accepted``
    boolean. The adapter owns both flag names and weights; this module never
    hardcodes domain vocabulary.

    Value-rate EMAs use RAW value per minute (not novelty-discounted) so the
    estimator measures the environment, not the router's own rerun policy;
    depletion is tracked separately as ``novelty_rate`` from the global
    first-seen ledger. Bias is deliberately NOT touched here -- it only moves
    in :func:`advance_epoch`, and it must never feed back into these value
    statistics (aux-loss-free invariant: bias steers routing order only).
    """

    next_state = deepcopy(dict(state))
    config = _config(next_state)
    families: dict[str, Any] = next_state.setdefault("families", {})
    seen: set[str] = set(next_state.get("seen_observation_keys") or [])

    minutes_by_family: dict[str, float] = {}
    novel_value_by_family: dict[str, float] = {}
    per_family_samples: dict[str, dict[str, list[float]]] = {}

    for probe in probes:
        family = str(probe.get("family") or "topic:general")
        duration = max(_MIN_DURATION_MINUTES, float(probe.get("duration_minutes") or 0.0))
        observation_keys = [str(key) for key in probe.get("observation_keys") or []]
        weighted = probe.get("weighted_flags")
        true_flags = (
            {str(name): float(weight) for name, weight in weighted.items()}
            if isinstance(weighted, Mapping)
            else {}
        )

        raw_value = float(len(observation_keys)) * OBSERVATION_WEIGHT
        raw_value += sum(true_flags.values())

        novel_value = 0.0
        for key in observation_keys:
            if key not in seen:
                seen.add(key)
                novel_value += OBSERVATION_WEIGHT
        for flag, weight in true_flags.items():
            pseudo = _flag_pseudo_key(family, flag)
            if pseudo not in seen:
                seen.add(pseudo)
                novel_value += weight

        samples = per_family_samples.setdefault(
            family,
            {
                "value_rate": [],
                "duration": [],
                "novelty": [],
                "accept": [],
                "accept_weight": [],
                "infra": [],
            },
        )
        samples["value_rate"].append(raw_value / duration)
        samples["duration"].append(duration)
        samples["novelty"].append(novel_value / raw_value if raw_value > 0 else 0.0)
        accept_sample = 1.0 if probe.get("accepted") else 0.0
        accept_weight = 1.0
        accepted_count = probe.get("accepted_count")
        attempt_count = probe.get("attempt_count")
        if accepted_count is not None and attempt_count is not None:
            try:
                accepted_total = float(accepted_count)
                attempt_total = float(attempt_count)
            except (OverflowError, TypeError, ValueError):
                pass
            else:
                counts_are_valid = (
                    not isinstance(accepted_count, bool)
                    and not isinstance(attempt_count, bool)
                    and math.isfinite(accepted_total)
                    and math.isfinite(attempt_total)
                    and accepted_total.is_integer()
                    and attempt_total.is_integer()
                    and attempt_total > 0
                    and 0 <= accepted_total <= attempt_total
                )
                if counts_are_valid:
                    accept_sample = accepted_total / attempt_total
                    accept_weight = attempt_total
        samples["accept"].append(accept_sample)
        samples["accept_weight"].append(accept_weight)
        samples["infra"].append(1.0 if probe.get("retryable_infra_error") else 0.0)

        minutes_by_family[family] = minutes_by_family.get(family, 0.0) + duration
        novel_value_by_family[family] = novel_value_by_family.get(family, 0.0) + novel_value

    for family, samples in per_family_samples.items():
        record = families.setdefault(family, _blank_family())
        run_count = len(samples["value_rate"])
        mean = lambda values: sum(values) / max(1, len(values))  # noqa: E731
        record["value_rate_ema"] = round(
            _ema(record.get("value_rate_ema"), mean(samples["value_rate"]), config["value_rate_alpha"]), 4
        )
        record["duration_ema"] = round(
            _ema(record.get("duration_ema"), mean(samples["duration"]), config["duration_alpha"]), 4
        )
        accept_weight_total = sum(samples["accept_weight"])
        accept_mean = sum(
            value * weight
            for value, weight in zip(samples["accept"], samples["accept_weight"])
        ) / max(1.0, accept_weight_total)
        record["accept_rate_ema"] = round(
            _ema(record.get("accept_rate_ema"), accept_mean, config["accept_rate_alpha"]), 4
        )
        record["infra_ema"] = round(
            _ema(record.get("infra_ema") or 0.0, mean(samples["infra"]), config["infra_alpha"]), 4
        )
        record["novelty_rate"] = round(mean(samples["novelty"]), 4)
        record["runs"] = int(record.get("runs") or 0) + run_count
        record["last_run_epoch"] = int(epoch)

    totals = next_state.setdefault("totals", {"dispatches": 0, "observed_epochs": 0})
    totals["dispatches"] = int(totals.get("dispatches") or 0) + len(list(probes))
    totals["observed_epochs"] = int(totals.get("observed_epochs") or 0) + 1
    next_state["seen_observation_keys"] = sorted(seen)
    next_state["last_epoch"] = {
        "epoch": int(epoch),
        "minutes_by_family": {key: round(value, 4) for key, value in minutes_by_family.items()},
        "novel_value_by_family": {key: round(value, 4) for key, value in novel_value_by_family.items()},
    }
    return next_state


def predicted_novelty(state: Mapping[str, Any], family: str) -> float:
    """Predicted fraction of the next run's value that would be first-seen."""

    config = _config(state)
    record = (state.get("families") or {}).get(family)
    if not record or not int(record.get("runs") or 0):
        return 1.0
    last = record.get("novelty_rate")
    if last is None:
        return 1.0
    return max(float(config["novelty_floor"]), float(last))


def advance_epoch(
    state: Mapping[str, Any],
    *,
    epoch: int,
    eligible_families: Sequence[str],
) -> dict[str, Any]:
    """Update per-family routing bias at an epoch boundary (aux-loss-free analog).

    DeepSeek-V3 updates a per-expert bias by +/-gamma from over/underload and
    applies it to top-k selection only. The harness analog replaces "load
    equality" (worthless here -- rerunning a depleted family at fair share is
    pure waste) with coverage/novelty debt:

    - debt: family has eligible work, has not run for > coverage_horizon
      epochs, and is still predicted to yield novel value;
    - surplus: family consumed > surplus_share_factor x fair share of the last
      epoch's dispatched minutes while returning near-pure duplicates.

    The bias decays toward zero (windup guard on frozen todo pools) and is
    clamped. It must only ever be read for routing ORDER -- never mixed into
    value estimates, plan evidence, or reported metrics.
    """

    next_state = deepcopy(dict(state))
    config = _config(next_state)
    families: dict[str, Any] = next_state.setdefault("families", {})
    eligible = [str(family) for family in eligible_families]
    for family in eligible:
        families.setdefault(family, _blank_family())

    last_epoch = next_state.get("last_epoch") or {}
    minutes_by_family = {
        str(key): float(value)
        for key, value in (last_epoch.get("minutes_by_family") or {}).items()
    }
    total_minutes = sum(minutes_by_family.values())
    fair_share = total_minutes / max(1, len(eligible)) if eligible else 0.0

    horizon = max(1, int(config["coverage_horizon_epochs"]))
    gamma = float(config["bias_gamma"])
    decay = float(config["bias_decay"])
    clamp = abs(float(config["bias_clamp"]))

    for family, record in families.items():
        debt = 0
        surplus = 0
        if family in eligible:
            last_run = record.get("last_run_epoch")
            epochs_since = (
                int(epoch) - int(last_run) if last_run is not None else horizon + 1
            )
            if epochs_since > horizon and predicted_novelty(next_state, family) > float(
                config["novelty_debt_threshold"]
            ):
                debt = 1
            spent = minutes_by_family.get(family, 0.0)
            novelty = record.get("novelty_rate")
            if (
                fair_share > 0
                and spent > float(config["surplus_share_factor"]) * fair_share
                and novelty is not None
                and float(novelty) < float(config["surplus_novelty_ceiling"])
            ):
                surplus = 1
        bias = decay * float(record.get("bias") or 0.0) + gamma * (debt - surplus)
        record["bias"] = round(max(-clamp, min(clamp, bias)), 4)

    next_state["updated_epoch"] = int(epoch)
    return next_state


def family_routing_terms(
    state: Mapping[str, Any],
    family: str,
) -> dict[str, Any]:
    """Routing terms for one family, split into value vs routing-only parts.

    ``expected_value_rate`` (value_rate x predicted novelty) is the unbiased
    bookkeeping quantity: it may feed plan evidence, bundle sizing, and
    admission. ``routing_multiplier`` additionally folds in exploration
    (UCB), coverage recency, bias, and infra penalty -- it may only reorder
    candidates. Keeping the split explicit is the DeepSeek-V3 invariant that
    the old hard coverage floor violated.
    """

    config = _config(state)
    families = state.get("families") or {}
    record = families.get(family) or {}
    runs = int(record.get("runs") or 0)

    known_rates = [
        float(item.get("value_rate_ema"))
        for item in families.values()
        if item.get("value_rate_ema") is not None
    ]
    cold_start_rate = (
        sum(known_rates) / len(known_rates)
        if known_rates
        else float(config["cold_start_value_rate"])
    )
    value_rate = (
        float(record["value_rate_ema"])
        if record.get("value_rate_ema") is not None
        else cold_start_rate
    )
    novelty = predicted_novelty(state, family)

    total_dispatches = int((state.get("totals") or {}).get("dispatches") or 0)
    ucb = min(
        float(config["ucb_cap"]),
        float(config["ucb_coefficient"])
        * math.sqrt(math.log(1.0 + total_dispatches) / (runs + 1.0)),
    )

    last_run = record.get("last_run_epoch")
    updated_epoch = int(state.get("updated_epoch") or 0)
    horizon = max(1, int(config["coverage_horizon_epochs"]))
    if runs and last_run is not None:
        epochs_since = max(0, updated_epoch - int(last_run))
    else:
        epochs_since = horizon
    coverage_bonus = float(config["coverage_bonus_cap"]) * min(1.0, epochs_since / horizon)

    bias = float(record.get("bias") or 0.0)
    infra_penalty = float(config["infra_penalty_weight"]) * float(record.get("infra_ema") or 0.0)

    expected_value_rate = value_rate * novelty
    unbiased_multiplier = max(
        float(config["routing_multiplier_floor"]),
        1.0 + ucb + coverage_bonus - infra_penalty,
    )
    routing_multiplier = max(
        float(config["routing_multiplier_floor"]),
        1.0 + ucb + coverage_bonus + bias - infra_penalty,
    )
    duration = (
        float(record["duration_ema"])
        if record.get("duration_ema") is not None
        else float(config["cold_start_duration_minutes"])
    )
    accept_rate = (
        float(record["accept_rate_ema"])
        if record.get("accept_rate_ema") is not None
        else float(config["cold_start_accept_rate"])
    )
    return {
        "family": family,
        "runs": runs,
        "value_rate": round(value_rate, 4),
        "novelty": round(novelty, 4),
        "expected_value_rate": round(expected_value_rate, 4),
        "duration_minutes": round(duration, 4),
        "duration_observed": record.get("duration_ema") is not None,
        "accept_rate": round(accept_rate, 4),
        "ucb": round(ucb, 4),
        "coverage_bonus": round(coverage_bonus, 4),
        "bias": round(bias, 4),
        "infra_penalty": round(infra_penalty, 4),
        "routing_multiplier": round(routing_multiplier, 4),
        "routing_value": round(expected_value_rate * routing_multiplier, 4),
        "unbiased_routing_value": round(expected_value_rate * unbiased_multiplier, 4),
    }


def is_router_state(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and str(value.get("schema_version") or "").startswith("loopx_explore_router_state")
    )
