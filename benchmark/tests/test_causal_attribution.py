"""Tests for EdgeBench cross-task causal attribution module and data archive."""

import shutil
from pathlib import Path


from benchmark.edgebench.causal_attribution import (
    compute_fixed_classification_ceiling,
    compute_grounding_gap,
    format_markdown_report,
    generate_ascii_sparkline,
    load_cross_task_data,
    main,
    verify_study_integrity,
)


STUDY_DATA_DIR = Path(__file__).resolve().parents[2] / "benchmark/edgebench/studies/cross-task-causal-attribution-20261103/data"


def test_study_archive_exists():
    assert STUDY_DATA_DIR.is_dir(), f"Study data directory does not exist: {STUDY_DATA_DIR}"
    assert (STUDY_DATA_DIR / "index.json").is_file()
    assert (STUDY_DATA_DIR / "summary.json").is_file()
    assert (STUDY_DATA_DIR / "five_arms_matrix.json").is_file()
    assert (STUDY_DATA_DIR / "inflection_points.json").is_file()
    assert (STUDY_DATA_DIR / "replan_overhead.json").is_file()


def test_verify_study_integrity_passes():
    result = verify_study_integrity(STUDY_DATA_DIR)
    assert result["verified"] is True
    assert result["verified_count"] == 4
    assert len(result["mismatches"]) == 0
    assert result["study_id"] == "cross-task-causal-attribution-20261103"


def test_verify_study_integrity_detects_tampering(tmp_path):
    dest = tmp_path / "study_data"
    shutil.copytree(STUDY_DATA_DIR, dest)

    # Corrupt one byte in summary.json
    summary_path = dest / "summary.json"
    content = summary_path.read_text(encoding="utf-8")
    summary_path.write_text(content + " ", encoding="utf-8")

    result = verify_study_integrity(dest)
    assert result["verified"] is False
    assert len(result["mismatches"]) == 1
    assert result["mismatches"][0]["path"] == "summary.json"
    assert result["mismatches"][0]["error"] == "hash_mismatch"


def test_verify_study_integrity_detects_missing_file(tmp_path):
    dest = tmp_path / "study_data"
    shutil.copytree(STUDY_DATA_DIR, dest)

    # Delete one bound file
    (dest / "replan_overhead.json").unlink()

    result = verify_study_integrity(dest)
    assert result["verified"] is False
    assert len(result["mismatches"]) == 1
    assert result["mismatches"][0]["path"] == "replan_overhead.json"
    assert result["mismatches"][0]["error"] == "file_missing"


def test_compute_grounding_gap():
    # Official Native (53.87, 53.87) vs Official Blind (11.58, 0.00)
    gap = compute_grounding_gap(
        native_best=53.87,
        native_last=53.87,
        blind_best=11.58,
        blind_last=0.00,
    )
    assert gap["native_retention"] == 1.0
    assert gap["blind_retention"] == 0.0
    assert gap["retention_gap"] == 1.0
    assert gap["grounding_advantage"] == round(53.87 - 11.58, 4)
    assert gap["terminal_divergence"] == 53.87


def test_compute_fixed_classification_ceiling():
    # Weights: 0.4 macro_f1 + 0.3 micro_f1 + 0.2 balanced_acc + 0.1 calibration
    # agent-10 in graph: macro=0.70879, micro=0.72512, bal_acc=0.71120
    macro = 0.7087937688575766
    micro = 0.7251241984210000
    bal_acc = 0.7112041984210000
    ceiling = compute_fixed_classification_ceiling(macro, micro, bal_acc)
    # 0.4 * 0.70879 + 0.3 * 0.72512 + 0.2 * 0.71120 + 0.1 * 1.0 = ~0.7432
    assert 0.73 <= ceiling <= 0.75


def test_generate_ascii_sparkline():
    assert generate_ascii_sparkline([]) == ""
    assert generate_ascii_sparkline([5.0, 5.0, 5.0], width=3) == "―――"
    increasing = generate_ascii_sparkline([1.0, 2.0, 3.0, 4.0, 5.0], width=5)
    assert len(increasing) == 5
    assert increasing[0] == " "
    assert increasing[-1] == "█"


def test_cross_task_data_schema_and_values():
    data = load_cross_task_data(STUDY_DATA_DIR)
    assert data["summary"]["schema_version"] == "edgebench_cross_task_causal_study_v0"
    assert data["summary"]["model"] == "gpt-6.1-sol"
    assert data["summary"]["effort"] == "xhigh"

    # Arms matrix
    arms = data["five_arms_matrix"]["arms"]
    assert len(arms) == 11
    official_native = next(a for a in arms if a["arm_id"] == "portfolio-official-native")
    official_blind = next(a for a in arms if a["arm_id"] == "portfolio-official-blind")
    assert official_native["best_score"] == 53.87
    assert official_native["last_score"] == 53.87
    assert official_blind["best_score"] == 11.58
    assert official_blind["last_score"] == 0.00

    # Inflection points
    pts = data["inflection_points"]["inflection_points"]
    assert len(pts) >= 5
    graph_pt2 = next(p for p in pts if p["task"] == "graph_clustering_calibration" and p["round_id"] == "agent-2")
    assert graph_pt2["score"] == 0.7194
    assert graph_pt2["driver_type"] == "architecture_selection"

    # Replan overhead
    ov = data["replan_overhead"]["replan_overhead"]
    graph_ov = next(o for o in ov if o["task"] == "graph_clustering_calibration")
    assert graph_ov["post_peak_rounds"] == 7
    assert graph_ov["token_waste_ratio"] > 0.45
    assert graph_ov["post_peak_input_tokens"] > 150_000_000


def test_format_markdown_report():
    data = load_cross_task_data(STUDY_DATA_DIR)
    report = format_markdown_report(data)
    assert "# EdgeBench Sol 6.1-xhigh Cross-Task Five-Arm Causal Attribution" in report
    assert "portfolio-official-native" in report
    assert "agent-2" in report
    assert "token_waste_ratio" not in report.lower() or "46.0%" in report


def test_cli_invocation_verify(capsys):
    ret = main(["--data", str(STUDY_DATA_DIR), "--verify"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Verified 4 files" in captured.out


def test_cli_invocation_report(capsys):
    ret = main(["--data", str(STUDY_DATA_DIR), "--report"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "# EdgeBench Sol 6.1-xhigh Cross-Task Five-Arm Causal Attribution" in captured.out


def test_cli_invocation_missing_dir(tmp_path, capsys):
    ret = main(["--data", str(tmp_path / "nonexistent"), "--verify"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: Data directory not found" in captured.err
