"""Real CLI discovery selects after full inspection without hiding diagnostics."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.registry import inspect_registry


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    goals = []
    for index in range(31):
        repo = tmp_path / f"project-{index:02d}"
        repo.mkdir()
        (repo / "STATE.md").write_text("# Synthetic Goal\n", encoding="utf-8")
        goals.append(
            {
                "id": f"{'target' if index in (3, 17, 28) else 'other'}-{index:02d}",
                "repo": str(repo),
                "state_file": "STATE.md",
                "domain": "software",
                "status": "active",
                "adapter": {"kind": "read_only_project_map_v0"},
            }
        )
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": goals,
                "common_runtime_root": str(tmp_path / "runtime"),
            }
        ),
        encoding="utf-8",
    )
    return path


def read_registry(path, *arguments, markdown=False):
    before = {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.entrypoint",
            "--registry",
            str(path),
            "--format",
            "markdown" if markdown else "json",
            "registry",
            *arguments,
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "LOOPX_USAGE_PING": "0"},
    )
    assert {p: p.read_bytes() for p in path.parent.rglob("*") if p.is_file()} == before
    return result, result.stdout if markdown else json.loads(result.stdout)


def test_query_filters_before_paging_and_defaults_remain_complete(registry_path):
    result, full = read_registry(registry_path)
    assert result.returncode == 0, result.stderr
    assert full == inspect_registry(registry_path)
    assert len(full["goals"]) == 31 and "selection" not in full
    result, first = read_registry(registry_path, "--query", "TARGET", "--limit", "2")
    assert result.returncode == 0
    assert [g["id"] for g in first["goals"]] == ["target-03", "target-17"]
    assert first["goal_count"] == 31
    assert first["selection"] == {
        "goal_id": None,
        "query": "TARGET",
        "offset": 0,
        "limit": 2,
        "matched_goal_count": 3,
        "returned_goal_count": 2,
        "next_offset": 2,
    }
    _, second = read_registry(
        registry_path, "--query", "TARGET", "--limit", "2", "--offset", "2"
    )
    assert [g["id"] for g in second["goals"]] == ["target-28"]
    assert second["selection"]["next_offset"] is None
    _, beyond = read_registry(registry_path, "--query", "TARGET", "--offset", "50")
    assert beyond["goals"] == [] and beyond["selection"]["matched_goal_count"] == 3


def test_exact_goal_and_query_compose_without_prefix_match(registry_path):
    _, payload = read_registry(
        registry_path, "--goal-id", "target-17", "--query", "PROJECT-17"
    )
    assert [g["id"] for g in payload["goals"]] == ["target-17"]
    _, empty = read_registry(
        registry_path, "--goal-id", "target-17", "--query", "project-03"
    )
    assert empty["ok"] and empty["goals"] == []
    result, missing = read_registry(registry_path, "--goal-id", "target-1")
    assert result.returncode == 1 and missing["ok"] is False


def test_selection_keeps_unrelated_registry_problems_visible(registry_path):
    raw = json.loads(registry_path.read_text())
    raw["goals"][0].pop("state_file")
    registry_path.write_text(json.dumps(raw))
    _, full = read_registry(registry_path)
    result, selected = read_registry(registry_path, "--goal-id", "target-17")
    assert result.returncode == 1 and selected["ok"] is False
    assert [g["id"] for g in selected["goals"]] == ["target-17"]
    for field in ("problems", "problem_diagnostics", "status_counts", "goal_count"):
        assert selected[field] == full[field]
    result, markdown = read_registry(
        registry_path, "--goal-id", "target-17", markdown=True
    )
    assert result.returncode == 1
    assert "entire registry" in markdown and "missing state_file" in markdown


@pytest.mark.parametrize(
    "arguments",
    [
        ["--query", "  "],
        ["--goal-id", ""],
        ["--limit", "0"],
        ["--limit", "-1"],
        ["--offset", "-1"],
    ],
)
def test_invalid_selection_reports_failure(registry_path, arguments):
    result, payload = read_registry(registry_path, *arguments)
    assert result.returncode == 1 and payload["ok"] is False and payload["error"]


def test_unicode_and_literal_search_and_default_page_size(registry_path):
    raw = json.loads(registry_path.read_text())
    raw["goals"][17]["domain"] = "研究[候选]"
    registry_path.write_text(json.dumps(raw, ensure_ascii=False))
    _, payload = read_registry(registry_path, "--query", "研究[")
    assert [g["id"] for g in payload["goals"]] == ["target-17"]
    _, normalized = read_registry(registry_path, "--query", "ＴＡＲＧＥＴ")
    assert normalized["selection"]["matched_goal_count"] == 3
    _, default_page = read_registry(registry_path, "--offset", "0")
    assert len(default_page["goals"]) == 20
    assert default_page["selection"]["next_offset"] == 20


def test_unavailable_registry_keeps_original_error(tmp_path):
    result, payload = read_registry(tmp_path / "absent.json", "--query", "target")
    assert result.returncode == 1 and payload["error"] == "registry file does not exist"
    assert "selection" not in payload


def test_global_registry_selection_does_not_follow_source_and_keeps_duplicate_diagnostics(
    registry_path,
):
    raw = json.loads(registry_path.read_text())
    raw["registry_role"] = "global-local"
    source = registry_path.parent / "source.json"
    source.write_text("not a registry")
    raw["goals"][17]["source_registry"] = str(source)
    raw["goals"].append(dict(raw["goals"][17]))
    registry_path.write_text(json.dumps(raw))
    _, full = read_registry(registry_path)
    result, selected = read_registry(
        registry_path, "--goal-id", "target-17", "--limit", "1"
    )
    assert result.returncode == 1
    assert selected["problems"] == full["problems"]
    assert selected["selection"]["matched_goal_count"] == 2
    assert selected["selection"]["next_offset"] == 1
    assert len(selected["goals"]) == 1
    assert selected["goal_count"] == 32
