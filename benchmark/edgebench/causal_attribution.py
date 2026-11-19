"""Cross-task causal attribution and trajectory efficiency analysis for EdgeBench.

Analyzes five worker profiles (Official, Native-Goal, Explore Off, Explore On,
Stride) across benchmark domains (Portfolio risk calibration, Graph, Ad).
Identifies causal inflection points where feedback or mechanisms changed outcomes,
quantifies ineffective replan overhead in long-horizon agent execution, and
evaluates theoretical component ceilings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class InflectionPoint:
    """A decisive inflection point in an agent's execution trajectory."""
    task: str
    round_id: str
    elapsed_hours: float
    score: float
    score_delta: float
    metric_name: str
    causal_mechanism: str
    driver_type: str  # e.g. "architecture_selection", "calibration_speedup", "proposal_ranking"
    evidence_ref: str


@dataclass(frozen=True)
class ReplanOverhead:
    """Quantitative measurement of ineffective post-peak replan waste."""
    task: str
    arm_id: str
    peak_round: str
    peak_elapsed_hours: float
    total_elapsed_hours: float
    post_peak_rounds: int
    post_peak_score_delta: float
    post_peak_responses: int
    post_peak_input_tokens: int
    post_peak_cached_input_tokens: int
    post_peak_output_tokens: int
    post_peak_reasoning_tokens: int
    token_waste_ratio: float  # post_peak_input_tokens / total_input_tokens
    engineering_exceptions: int
    theoretical_ceiling: Optional[float] = None


@dataclass
class ArmPerformance:
    """Summary metrics of a designated benchmark arm."""
    arm_id: str
    profile: str
    feedback_mode: str
    evaluator: str
    best_score: float
    last_score: float
    runtime_minutes: float
    agent_evaluations: int
    auto_evaluations: int
    stride_strategy: Optional[str] = None
    explore_enabled: bool = False
    grounding_retention_ratio: float = 1.0  # last_score / best_score


def compute_sha256(data: bytes) -> str:
    """Return lowercase hex SHA-256 digest of data bytes."""
    return hashlib.sha256(data).hexdigest()


def compute_grounding_gap(
    native_best: float,
    native_last: float,
    blind_best: float,
    blind_last: float,
) -> Dict[str, float]:
    """Compute the feedback grounding gap and late-stage retention delta."""
    native_retention = (native_last / native_best) if native_best > 0 else 0.0
    blind_retention = (blind_last / blind_best) if blind_best > 0 else 0.0
    grounding_advantage = native_best - blind_best
    terminal_divergence = native_last - blind_last
    return {
        "native_retention": round(native_retention, 4),
        "blind_retention": round(blind_retention, 4),
        "retention_gap": round(native_retention - blind_retention, 4),
        "grounding_advantage": round(grounding_advantage, 4),
        "terminal_divergence": round(terminal_divergence, 4),
    }


def compute_fixed_classification_ceiling(
    macro_f1: float,
    micro_f1: float,
    balanced_acc: float,
    weights: Tuple[float, float, float, float] = (0.4, 0.3, 0.2, 0.1),
) -> float:
    """Calculate the theoretical score upper bound under fixed classification.

    Assumes zero calibration error (1 - ECE = 1.0) and zero latency penalty.
    Formula: w0 * macro_f1 + w1 * micro_f1 + w2 * balanced_acc + w3 * (1.0).
    """
    w_macro, w_micro, w_bal, w_calib = weights
    return w_macro * macro_f1 + w_micro * micro_f1 + w_bal * balanced_acc + w_calib * 1.0


def generate_ascii_sparkline(values: List[float], width: int = 20) -> str:
    """Render a sequence of numeric values as a compact ASCII sparkline."""
    if not values:
        return ""
    min_v, max_v = min(values), max(values)
    range_v = max_v - min_v
    if range_v == 0:
        return "―" * min(len(values), width)

    ticks = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
    if len(values) > width:
        # Sample evenly across width
        indices = [int(i * (len(values) - 1) / (width - 1)) for i in range(width)]
        sampled = [values[i] for i in indices]
    else:
        sampled = values

    line = []
    for v in sampled:
        idx = int((v - min_v) / range_v * (len(ticks) - 1))
        idx = max(0, min(len(ticks) - 1, idx))
        line.append(ticks[idx])
    return "".join(line)


def load_cross_task_data(data_dir: Path) -> Dict[str, Any]:
    """Load and validate all JSON files in the cross-task study data directory."""
    files = {
        "summary": data_dir / "summary.json",
        "inflection_points": data_dir / "inflection_points.json",
        "replan_overhead": data_dir / "replan_overhead.json",
        "five_arms_matrix": data_dir / "five_arms_matrix.json",
        "index": data_dir / "index.json",
    }
    loaded = {}
    for key, path in files.items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing required study data file: {path}")
        with path.open("r", encoding="utf-8") as f:
            loaded[key] = json.load(f)
    return loaded


def verify_study_integrity(data_dir: Path) -> Dict[str, Any]:
    """Verify cryptographic SHA-256 bindings in index.json against on-disk files."""
    index_file = data_dir / "index.json"
    if not index_file.is_file():
        raise FileNotFoundError(f"index.json not found in {data_dir}")

    with index_file.open("r", encoding="utf-8") as f:
        index_data = json.load(f)

    bindings = index_data.get("bindings", {})
    verified_files = []
    mismatches = []

    for rel_path, expected_hash in bindings.items():
        target = data_dir / rel_path
        if not target.is_file():
            mismatches.append({"path": rel_path, "error": "file_missing"})
            continue
        actual_hash = compute_sha256(target.read_bytes())
        if actual_hash != expected_hash:
            mismatches.append({
                "path": rel_path,
                "error": "hash_mismatch",
                "expected": expected_hash,
                "actual": actual_hash,
            })
        else:
            verified_files.append(rel_path)

    return {
        "verified": len(mismatches) == 0,
        "verified_count": len(verified_files),
        "mismatches": mismatches,
        "schema_version": index_data.get("schema_version"),
        "study_id": index_data.get("study_id"),
    }


def format_markdown_report(data: Dict[str, Any]) -> str:
    """Format a comprehensive summary report from loaded study data."""
    summary = data["summary"]
    inflections = data["inflection_points"]["inflection_points"]
    overhead = data["replan_overhead"]["replan_overhead"]
    arms = data["five_arms_matrix"]["arms"]

    lines = [
        f"# {summary['title']}",
        "",
        f"**Study ID**: `{summary['study_id']}` | **Model**: `{summary['model']}` | **Effort**: `{summary['effort']}`",
        "",
        "## 1. Five-Arm Profile Performance Matrix",
        "",
        "| Arm | Profile | Feedback | Evaluator | Best Score | Last Score | Retention | Runtime (min) | Agent/Auto Evals |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]

    for arm in arms:
        retention = f"{round(arm['last_score'] / arm['best_score'] * 100, 1)}%" if arm['best_score'] > 0 else "0.0%"
        lines.append(
            f"| `{arm['arm_id']}` | {arm['profile']} | {arm['feedback']} | {arm['evaluator']} "
            f"| {arm['best_score']:.2f} | {arm['last_score']:.2f} | {retention} "
            f"| {arm['runtime_minutes']:.1f} | {arm['agent_evaluations']} / {arm['auto_evaluations']} |"
        )

    lines.extend([
        "",
        "## 2. Decisive Causal Inflection Points",
        "",
        "| Domain | Round | Elapsed (h) | Score | Delta | Driver Type | Mechanism & Evidence |",
        "|---|---|---:|---:|---:|---|---|",
    ])

    for pt in inflections:
        delta_str = f"+{pt['score_delta']:.4f}" if pt['score_delta'] > 0 else f"{pt['score_delta']:.4f}"
        lines.append(
            f"| **{pt['task']}** | `{pt['round_id']}` | {pt['elapsed_hours']:.2f} | {pt['score']:.4f} "
            f"| {delta_str} | `{pt['driver_type']}` | {pt['causal_mechanism']} (`{pt['evidence_ref']}`) |"
        )

    lines.extend([
        "",
        "## 3. Ineffective Replan Waste & Cost Post-Peak",
        "",
        "| Domain | Arm | Peak (h) | Total (h) | Fruitless Rounds | Token Waste Ratio | Post-Peak Input Tokens | Post-Peak Output Tokens | Theoretical Ceiling |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])

    for ov in overhead:
        waste_pct = f"{ov['token_waste_ratio'] * 100:.1f}%"
        in_tokens = f"{ov['post_peak_input_tokens'] / 1e6:.1f}M"
        out_tokens = f"{ov['post_peak_output_tokens'] / 1e3:.1f}k"
        ceil_str = f"{ov['theoretical_ceiling']:.4f}" if ov.get("theoretical_ceiling") else "N/A"
        lines.append(
            f"| **{ov['task']}** | `{ov['arm_id']}` | {ov['peak_elapsed_hours']:.2f} | {ov['total_elapsed_hours']:.2f} "
            f"| {ov['post_peak_rounds']} | **{waste_pct}** | {in_tokens} | {out_tokens} | {ceil_str} |"
        )

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for causal attribution and verification."""
    parser = argparse.ArgumentParser(description="EdgeBench cross-task causal attribution tool")
    parser.add_argument("--data", type=Path, default=Path("benchmark/edgebench/studies/cross-task-causal-attribution-20261103/data"),
                        help="Path to study data directory")
    parser.add_argument("--verify", action="store_true", help="Verify cryptographic file bindings in index.json")
    parser.add_argument("--report", action="store_true", help="Print structured Markdown analysis report")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    args = parser.parse_args(argv)

    if not args.data.exists():
        sys.stderr.write(f"Error: Data directory not found: {args.data}\n")
        return 1

    if args.verify:
        result = verify_study_integrity(args.data)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            if result["verified"]:
                print(f"Verified {result['verified_count']} files in {args.data} successfully against index.json.")
            else:
                sys.stderr.write(f"Verification failed: {len(result['mismatches'])} mismatches detected.\n")
                for m in result["mismatches"]:
                    sys.stderr.write(f"  - {m['path']}: {m['error']}\n")
                return 1

    if args.report:
        data = load_cross_task_data(args.data)
        if args.format == "json":
            print(json.dumps(data, indent=2))
        else:
            print(format_markdown_report(data))

    return 0


if __name__ == "__main__":
    sys.exit(main())
