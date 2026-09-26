"""Bounded discovery, complete guidance and real installed skill execution."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "loopx-self-repair"
SCRIPT = SKILL / "scripts" / "find_pattern.py"
spec = importlib.util.spec_from_file_location("repair_lookup", SCRIPT)
assert spec is not None and spec.loader is not None
lookup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lookup)


def test_search_paginates_without_losing_or_expanding_guidance():
    rows = [{
        "pattern": f"pattern_{n}", "symptoms": "State [invalid]", "durable_repair": "Keep CAS proof."
    } for n in range(8)]
    first = lookup.search_patterns(rows, "[INVALID] proof", offset=0, limit=5)
    second = lookup.search_patterns(rows, "[INVALID] proof", offset=first["next_offset"], limit=5)
    assert first["total_matches"] == second["total_matches"] == 8
    assert second["next_offset"] is None
    assert [r["pattern"] for r in first["patterns"] + second["patterns"]] == [r["pattern"] for r in rows]
    assert all("durable_repair" not in r for r in first["patterns"])
    assert lookup.search_patterns(rows, "missing", offset=0, limit=5)["total_matches"] == 0


def test_bm25_handles_partial_terms_and_keeps_exact_ids_first():
    rows = [
        {"pattern": "lease_recovery", "symptoms": "Refresh the released lease proof: `stale_proof`"},
        {"pattern": "other", "symptoms": "lease recovery lease recovery"},
        {"pattern": "display", "symptoms": "Turn display repair"},
    ]
    result = lookup.search_patterns(rows, "lease_recovery", offset=0, limit=5)
    assert result["patterns"][0]["pattern"] == "lease_recovery"
    result = lookup.search_patterns(rows, "released unavailableword", offset=0, limit=5)
    assert [r["pattern"] for r in result["patterns"]] == ["lease_recovery"]
    assert result["unmatched_terms"] == ["unavailableword"]
    assert result["patterns"][0]["matched_terms"] == ["released"]
    result = lookup.search_patterns(rows, "stale_proof", offset=0, limit=5)
    assert result["patterns"][0]["exact_match"] == "code"
    assert result["patterns"][0]["pattern"] == "lease_recovery"


def test_labeled_repair_queries_improve_over_all_terms_filter():
    rows = lookup.read_patterns(lookup.CATALOG)
    cases = json.loads((ROOT / "tests/fixtures/self_repair_queries.json").read_text())
    baseline_hits = candidate_hits = 0
    for case in cases:
        assert any(row["pattern"] == case["relevant"] for row in rows)
        baseline = [row["pattern"] for row in rows if all(
            term in "\n".join(row.values()).casefold() for term in case["query"].casefold().split()
        )][:3]
        candidate = [row["pattern"] for row in lookup.search_patterns(
            rows, case["query"], offset=0, limit=3)["patterns"]]
        baseline_hits += case["relevant"] in baseline
        candidate_hits += case["relevant"] in candidate
    # Preserve a deliberately ambiguous case; this is not a broad precision claim.
    assert candidate_hits > baseline_hits


def test_catalog_preserves_every_table_row_and_prose_appendix():
    text = lookup.CATALOG.read_text()
    patterns = lookup.read_patterns(lookup.CATALOG)
    by_id = {row["pattern"]: row for row in patterns}
    for line in text.splitlines():
        if line.startswith("| `"):
            pattern_id = line.split("`", 2)[1]
            row = by_id[pattern_id]
            # Whole original cells, including code pipes and safety constraints.
            assert "| `" + pattern_id + "` | " + " | ".join(row[f] for f in lookup.FIELDS[1:]) + " |" == line
    assert "--state merged|all" in by_id["pr_review_default_lifecycle_overreach"]["durable_repair"]
    assert "wait_for_ci=false" in by_id["note_review_ignores_a_goal_s_ci_waiting_configuration"]["guidance"]
    assert "caches automation rows" in by_id["note_minimal_evidence_packet"]["guidance"]


@pytest.mark.parametrize("body", [
    "| `broken` | missing columns |\n",
    "| silently discarded row | other cells |\n",
    "| `same` | s | e | r | d |\n| `same` | s | e | r | d |\n",
])
def test_invalid_catalog_is_reported_instead_of_omitted(tmp_path, body):
    path = tmp_path / "catalog.md"
    path.write_text(body)
    with pytest.raises(ValueError):
        lookup.read_patterns(path)


@pytest.mark.parametrize("args", [
    ["--query", " "], ["--list", "--limit", "0"], ["--list", "--offset", "-1"],
    ["--list", "--limit", "21"], ["--id", "does_not_exist"], ["--id", ""], ["--query", "[]"],
])
def test_cli_rejects_invalid_requests(tmp_path, args):
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0


def test_real_install_delivers_lookup_and_runs_without_loopx_on_path(tmp_path):
    destination = tmp_path / "host-skills"
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--format", "json", "workflow-skills",
        "--install", "--skills-dir", str(destination),
    ], cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["after"]["ready"]
    from loopx.doctor import installed_skill_summary

    assert installed_skill_summary((destination,))["loopx-self-repair"]["required_phrases"]
    installed = destination / "loopx-self-repair" / "scripts" / "find_pattern.py"
    bundled = installed.with_name("lexical_retrieval.py")
    assert bundled.read_bytes() == (ROOT / "loopx/lexical_retrieval.py").read_bytes()
    again = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--format", "json", "workflow-skills",
        "--install", "--skills-dir", str(destination),
    ], cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(again.stdout)["installed"]["loopx-self-repair"] == "unchanged"
    result = subprocess.run([
        sys.executable, "-I", str(installed), "--query", "closeout recovery",
    ], cwd=tmp_path, env={"PATH": str(tmp_path)}, capture_output=True, text=True, check=True)
    page = json.loads(result.stdout)
    assert page["total_matches"] > 0
    assert len(page["patterns"]) <= 5
    result = subprocess.run([
        sys.executable, "-I", str(installed), "--id", "host_closeout_presentation_lookup_gap",
    ], cwd=tmp_path, env={"PATH": str(tmp_path)}, capture_output=True, text=True, check=True)
    row = json.loads(result.stdout)["pattern"]
    assert row == next(r for r in lookup.read_patterns(lookup.CATALOG) if r["pattern"] == row["pattern"])
    # Wheel data must deliver the same resources that source installation copied.
    with (ROOT / "pyproject.toml").open("rb") as stream:
        data = tomllib.load(stream)["tool"]["setuptools"]["data-files"]
    included = {file for files in data.values() for file in files}
    assert "loopx/lexical_retrieval.py" in included
    for path in SKILL.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            assert str(path.relative_to(ROOT)) in included
