"""Tests for EdgeBench Sol 6.1 five-arm causal attribution study and chart generator."""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STUDY_DIR = ROOT / "benchmark/edgebench/studies/five-arm-sol61-causal-attribution"
DATA_DIR = STUDY_DIR / "data"
ASSETS_DIR = STUDY_DIR / "assets"


def test_study_dataset_schema_and_integrity():
    """Verify data files exist, parse correctly, and satisfy structural invariants."""
    metrics_path = DATA_DIR / "summary_metrics.json"
    replan_path = DATA_DIR / "replan_causal_breakdown.json"
    readme_path = STUDY_DIR / "README.md"

    assert metrics_path.exists(), "summary_metrics.json must exist"
    assert replan_path.exists(), "replan_causal_breakdown.json must exist"
    assert readme_path.exists(), "README.md must exist"

    with open(metrics_path, encoding="utf-8") as f:
        metrics = json.load(f)
    with open(replan_path, encoding="utf-8") as f:
        replan = json.load(f)

    # Invariants for metrics
    assert metrics["schema_version"] == "edgebench_five_arm_study_metrics_v1"
    assert metrics["model"] == "gpt-6.1-sol"
    assert metrics["reasoning_effort"] == "xhigh"

    expected_arms = {"official", "native-goal", "heartbeat-explore-off", "heartbeat-explore-on", "stride"}
    assert set(metrics["arms"].keys()) == expected_arms

    expected_tasks = {"portfolio_risk_calibration", "grid_turing_robot", "ad_placement_optimization"}
    assert set(metrics["tasks"].keys()) == expected_tasks

    for task_id, task_data in metrics["tasks"].items():
        assert "metric_direction" in task_data
        for arm_id in expected_arms:
            res = task_data["results"][arm_id]
            assert res["runtime_minutes"] > 0
            assert res["total_turns"] >= res["effective_work_turns"]
            assert res["best_score"] is not None
            assert res["last_observed_score"] is not None
            assert res["uncached_input_tokens"] > 0

    # Invariants for replan breakdown
    assert replan["schema_version"] == "replan_causal_breakdown_v1"
    assert replan["total_replans_observed"] == 86
    assert "valid_jump" in replan["taxonomy"]
    assert "trivial_microtune" in replan["taxonomy"]
    assert "stride" in replan["per_arm_breakdown"]
    assert replan["per_arm_breakdown"]["stride"]["effective_jump_ratio"] > 0.5


def test_chart_generator_execution_and_svg_validity(tmp_path):
    """Verify generate_charts.py executes cleanly and outputs valid SVG graphics."""
    script_path = STUDY_DIR / "generate_charts.py"
    assert script_path.exists(), "generate_charts.py must exist"

    result = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
    assert result.returncode == 0, f"Chart generation failed: {result.stderr}"

    expected_charts = [
        "five_arm_trajectory_comparison.svg",
        "causal_action_attribution.svg",
        "replan_efficiency_frontier.svg",
    ]

    for chart_name in expected_charts:
        svg_file = ASSETS_DIR / chart_name
        assert svg_file.exists(), f"Expected chart {chart_name} to be generated"
        content = svg_file.read_text(encoding="utf-8")
        assert content.startswith("<svg"), f"{chart_name} must start with <svg tag"
        assert content.endswith("</svg>"), f"{chart_name} must end with </svg> tag"
        assert 'xmlns="http://www.w3.org/2000/svg"' in content
        assert len(content) > 1000, f"{chart_name} must have non-trivial SVG content"
