"""Shared telemetry, rollout metric extraction, and Base/Head comparison."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
import re

from benchmark.runtime.session_observation import (
    CodexSessionObservationError,
    observe_codex_session_tail,
)
from loopx.control_plane.quota.codex_session_usage import (
    CodexSessionUsageError,
    read_codex_session_usage,
)
from loopx.control_plane.runtime.public_safety import (
    LOCAL_PATH_SURFACE_PATTERN,
    SECRET_LIKE_SURFACE_PATTERN,
)
from loopx.control_plane.turn_driver.epoch_prefetch import (
    DEFAULT_TOKENIZER_ENCODING,
    count_verifiable_tokens,
)

MICROBENCH_RECEIPT_SCHEMA_VERSION = "loopx_microbench_receipt_v0"
MICROBENCH_DIFF_SCHEMA_VERSION = "loopx_microbench_differential_v0"

_EXTRA_SECRET_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{12,})"
)
_USER_HOME_RE = re.compile(r"(?:/Users/|/home/|C:\\Users\\)[^\s\"',;]+")


@dataclass(frozen=True)
class BehaviorConstraintResult:
    """Typed outcome of all required M1/M2/M3 behavior constraints for an arm."""

    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    violations: tuple[str, ...] = ()

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 1.0 if self.passed else 0.0
        ok = sum(1 for value in self.checks.values() if value)
        return round(ok / len(self.checks), 4)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "checks": dict(self.checks),
            "violations": list(self.violations),
        }


@dataclass(frozen=True)
class EfficiencyMetrics:
    """Explicit token, wire-byte, turn, and latency metrics (None = unknown)."""

    wire_bytes: int | None = None
    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    tool_calls: int | None = None
    round_trips: int | None = None
    wall_seconds: float | None = None

    def to_dict(self) -> dict[str, int | float | None]:
        return asdict(self)


def measure_text_tokens(
    text: str,
    *,
    encoding_name: str = DEFAULT_TOKENIZER_ENCODING,
) -> tuple[int | None, dict[str, object] | None]:
    """Delegate verifiable prompt token counting to LoopX's shared tokenizer owner."""
    counted = count_verifiable_tokens(text, encoding_name=encoding_name)
    if counted is None:
        return None, None
    tokens, metadata = counted
    return tokens, dict(metadata)


def collect_codex_rollout_metrics(
    sessions_root: Path,
    *,
    harbor_trajectories: Iterable[object] | None = None,
) -> dict[str, object]:
    """Extract usage metrics using ``codex_session_usage``, ``session_observation``, and Harbor."""
    if harbor_trajectories is not None:
        try:
            from benchmark.runtime.harbor import BenchmarkCodex

            totals = BenchmarkCodex._totals(harbor_trajectories)
            p_tot = totals.get("prompt")
            c_tot = totals.get("completion")
            total_tok = (
                p_tot + c_tot
                if isinstance(p_tot, int) and not isinstance(p_tot, bool)
                and isinstance(c_tot, int) and not isinstance(c_tot, bool)
                else None
            )
            return {
                "source": "harbor_trajectory_totals",
                "prompt_tokens": p_tot,
                "completion_tokens": c_tot,
                "cached_tokens": totals.get("cached"),
                "total_tokens": total_tok,
                "rollout_count": None,
                "error": None,
            }
        except ImportError:
            pass

    rollouts = sorted(sessions_root.rglob("*.jsonl")) if sessions_root.is_dir() else []
    if not rollouts:
        return {
            "source": "codex_session_usage",
            "prompt_tokens": None,
            "completion_tokens": None,
            "cached_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "rollout_count": 0,
            "error": "missing_rollout",
        }

    prompt_total = 0
    completion_total = 0
    cached_total = 0
    reasoning_total: int | None = 0
    grand_total: int | None = 0
    complete_records = 0

    for rollout in rollouts:
        try:
            usage = read_codex_session_usage(rollout)
            tail = observe_codex_session_tail(rollout)
        except (CodexSessionUsageError, CodexSessionObservationError, OSError, ValueError) as exc:
            return {
                "source": "codex_session_usage",
                "prompt_tokens": None,
                "completion_tokens": None,
                "cached_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
                "rollout_count": len(rollouts),
                "error": type(exc).__name__,
            }
        prompt_val = usage.get("input_tokens")
        comp_val = usage.get("output_tokens")
        cache_val = usage.get("cache_tokens")
        if (
            not isinstance(prompt_val, int) or isinstance(prompt_val, bool) or prompt_val < 0
            or not isinstance(comp_val, int) or isinstance(comp_val, bool) or comp_val < 0
            or not isinstance(cache_val, int) or isinstance(cache_val, bool) or cache_val < 0
        ):
            return {
                "source": "codex_session_usage",
                "prompt_tokens": None,
                "completion_tokens": None,
                "cached_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": None,
                "rollout_count": len(rollouts),
                "error": "incomplete_usage_fields",
            }
        prompt_total += prompt_val
        completion_total += comp_val
        cached_total += cache_val
        raw_rec = tail.get("complete_record_count")
        if isinstance(raw_rec, int) and not isinstance(raw_rec, bool) and raw_rec > 0:
            complete_records += raw_rec
        raw_tail_usage = tail.get("usage_cumulative")
        tail_usage: Mapping[str, object] = raw_tail_usage if isinstance(raw_tail_usage, Mapping) else {}
        r_val = tail_usage.get("reasoning_output_tokens")
        if isinstance(r_val, int) and not isinstance(r_val, bool) and r_val >= 0 and reasoning_total is not None:
            reasoning_total += r_val
        else:
            reasoning_total = None
        t_val = tail_usage.get("total_tokens")
        if isinstance(t_val, int) and not isinstance(t_val, bool) and t_val >= 0 and grand_total is not None:
            grand_total += t_val
        else:
            grand_total = None

    return {
        "source": "codex_session_usage",
        "prompt_tokens": prompt_total,
        "completion_tokens": completion_total,
        "cached_tokens": cached_total,
        "reasoning_tokens": reasoning_total,
        "total_tokens": grand_total,
        "rollout_count": len(rollouts),
        "complete_record_count": complete_records,
        "error": None,
    }


def _incomplete_metrics_from_coverage(coverage: Mapping[str, object] | None) -> set[str]:
    if not isinstance(coverage, Mapping):
        return set()
    incomplete: set[str] = set()
    raw_metrics = coverage.get("metrics")
    metric_map = raw_metrics if isinstance(raw_metrics, Mapping) else coverage
    for metric, info in metric_map.items():
        if isinstance(info, Mapping) and info.get("status") in {"partial", "missing"}:
            incomplete.add(str(metric))
    return incomplete


def _numeric_delta(base_value: int | float | None, head_value: int | float | None) -> int | float | None:
    if base_value is None or head_value is None:
        return None
    if isinstance(base_value, bool) or isinstance(head_value, bool):
        return None
    if not (math.isfinite(float(base_value)) and math.isfinite(float(head_value))):
        return None
    diff = head_value - base_value
    if isinstance(base_value, int) and isinstance(head_value, int):
        return int(diff)
    return round(float(diff), 4)


def compare_base_vs_head_efficiency(
    base_eff: EfficiencyMetrics,
    head_eff: EfficiencyMetrics,
    *,
    base_behavior: BehaviorConstraintResult,
    head_behavior: BehaviorConstraintResult,
    base_telemetry_coverage: Mapping[str, object] | None = None,
    head_telemetry_coverage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Compare Base vs Head on Behavior Constraint x Efficiency without masking regressions."""
    excluded_partial = sorted(
        _incomplete_metrics_from_coverage(base_telemetry_coverage)
        | _incomplete_metrics_from_coverage(head_telemetry_coverage)
    )
    excluded_set = set(excluded_partial)
    deltas: dict[str, int | float | None] = {}
    pct_changes: dict[str, float | None] = {}
    for field_name in (
        "wire_bytes",
        "prompt_tokens",
        "cached_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "total_tokens",
        "tool_calls",
        "round_trips",
        "wall_seconds",
    ):
        base_val = getattr(base_eff, field_name)
        head_val = getattr(head_eff, field_name)
        if field_name in excluded_set:
            deltas[field_name] = None
            pct_changes[field_name] = None
            continue
        delta = _numeric_delta(base_val, head_val)
        deltas[field_name] = delta
        if delta is not None and isinstance(base_val, (int, float)) and base_val > 0:
            pct_changes[field_name] = round((float(delta) / float(base_val)) * 100.0, 2)
        else:
            pct_changes[field_name] = None

    behavior_preserved = head_behavior.passed and (
        not base_behavior.passed or head_behavior.pass_rate >= base_behavior.pass_rate
    )
    return {
        "behavior_preserved": behavior_preserved,
        "base_passed": base_behavior.passed,
        "head_passed": head_behavior.passed,
        "base_pass_rate": base_behavior.pass_rate,
        "head_pass_rate": head_behavior.pass_rate,
        "head_violations": list(head_behavior.violations),
        "excluded_partial_metrics": excluded_partial,
        "deltas": deltas,
        "pct_changes": pct_changes,
    }


def build_scenario_source_attribution(
    *,
    base_source: str,
    head_source: str = "candidate_head",
    comparison_kind: str,
    production_hot_path_covered: bool,
    note: str | None = None,
) -> dict[str, object]:
    """Record explicit source checkout & settlement attribution per scenario."""
    cross_commit = base_source == "external_base_checkout" and head_source == "candidate_head"
    return {
        "schema_version": "microbench_source_attribution_v0",
        "base_source": base_source,
        "head_source": head_source,
        "comparison_kind": comparison_kind,
        "production_hot_path_covered": production_hot_path_covered,
        "excluded_from_cross_commit_claims": not cross_commit,
        "excluded_from_production_hot_path_claims": not production_hot_path_covered,
        **({"note": note} if note else {}),
    }


def summarize_claim_scope(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Summarize which scenarios support cross-commit or production hot-path conclusions."""
    cross_commit: list[str] = []
    same_commit_mechanism: list[str] = []
    prod_covered: list[str] = []
    excluded_cross: list[str] = []
    excluded_prod: list[str] = []
    seen_cross: set[str] = set()
    seen_same: set[str] = set()
    seen_prod: set[str] = set()
    seen_ex_cross: set[str] = set()
    seen_ex_prod: set[str] = set()

    for row in rows:
        sid = str(row.get("scenario_id") or "")
        attr = row.get("source_attribution")
        if not sid or not isinstance(attr, Mapping):
            continue
        if attr.get("excluded_from_cross_commit_claims") is True:
            if sid not in seen_same:
                same_commit_mechanism.append(sid)
                seen_same.add(sid)
            if sid not in seen_ex_cross:
                excluded_cross.append(sid)
                seen_ex_cross.add(sid)
        elif sid not in seen_cross:
            cross_commit.append(sid)
            seen_cross.add(sid)
        if attr.get("excluded_from_production_hot_path_claims") is True:
            if sid not in seen_ex_prod:
                excluded_prod.append(sid)
                seen_ex_prod.add(sid)
        elif sid not in seen_prod:
            prod_covered.append(sid)
            seen_prod.add(sid)

    return {
        "schema_version": "microbench_claim_scope_v0",
        "cross_commit_scenarios": cross_commit,
        "same_commit_mechanism_scenarios": same_commit_mechanism,
        "production_hot_path_scenarios": prod_covered,
        "excluded_from_cross_commit_claims": excluded_cross,
        "excluded_from_production_hot_path_claims": excluded_prod,
    }


def _scan_text_violations(text: str) -> list[str]:
    findings: list[str] = []
    if _USER_HOME_RE.search(text):
        findings.append("local_home_path")
    if LOCAL_PATH_SURFACE_PATTERN.search(text):
        findings.append("local_path_surface")
    if SECRET_LIKE_SURFACE_PATTERN.search(text) or _EXTRA_SECRET_RE.search(text):
        findings.append("secret_like_token")
    return findings


def assert_public_safe_payload(payload: object, *, label: str = "microbench_receipt") -> None:
    """Fail closed if any serialized payload leaks local paths or credentials."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    violations = sorted(set(_scan_text_violations(encoded)))
    if violations:
        raise ValueError(f"{label} failed public safety boundary: {', '.join(violations)}")


def summarize_artifact_boundary(paths: Sequence[str] | Iterable[str] = ()) -> dict[str, object]:
    """Emit explicit boundary attestations matching LoopX benchmark contracts."""
    return {
        "raw_task_prompts_persisted": False,
        "raw_provider_bodies_persisted": False,
        "credentials_persisted": False,
        "local_paths_persisted": False,
        "synthetic_fixtures_only": True,
        "recorded_paths": list(paths),
    }
