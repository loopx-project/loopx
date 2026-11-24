"""Find registered materials through the real CLI without opening their sources."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


def run(registry: Path, *args: str, format: str = "json") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
         "--format", format, *args], capture_output=True, text=True, timeout=45,
    )


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"schema_version": "0.1", "goals": [
        {"id": "materials", "repo": str(tmp_path), "authority_registry": {}},
        {"id": "empty", "repo": str(tmp_path)},
    ]}), encoding="utf-8")
    for source_id, role, topic in [
        ("a-guide", "Installation guide", "setup"),
        ("b-paper", "Café 研究", "research"),
        ("c-review", "CAFE\u0301 研究 review", "research-review"),
    ]:
        result = run(path, "register-authority-source", "--goal-id", "materials",
                     "--source-id", source_id, "--source-kind", "doc", "--role", role,
                     "--freshness", "current", "--revision", "edition-2", "--topic", topic,
                     "--gate-status", "needs_access", "--source-ref", str(tmp_path / "absent.md"),
                     "--no-global-sync")
        assert result.returncode == 0, result.stdout + result.stderr
    return path


def read(registry: Path, *args: str) -> dict:
    result = run(registry, "authority-sources", "--goal-id", "materials", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_search_filters_before_paging_and_preserves_registered_facts(registry: Path):
    before = registry.read_bytes()
    first = read(registry, "--query", "CAFÉ 研究", "--limit", "1")
    assert first["source_count"] == 3
    assert first["matched_count"] == 2
    assert first["returned_count"] == 1
    assert first["next_offset"] == 1
    assert first["query"] == "CAFÉ 研究"
    assert first["sources"][0]["id"] == "b-paper"
    assert first["sources"][0]["gate_status"] == "needs_access"
    assert first["sources"][0]["source_ref_kind"] == "local_path"
    assert first["sources"][0]["source_ref_redacted"] is True
    assert len(first["sources"][0]["source_ref_sha256"]) == 64
    second = read(registry, "--query", "CAFÉ 研究", "--limit", "1", "--offset", "1")
    assert [row["id"] for row in second["sources"]] == ["c-review"]
    assert second["next_offset"] is None
    assert registry.read_bytes() == before


def test_exact_topic_combines_with_query_and_empty_results_are_explicit(registry: Path):
    result = read(registry, "--query", "edition-2 current", "--topic", "research")
    assert [row["id"] for row in result["sources"]] == ["b-paper"]
    assert result["topic"] == "research"
    for args in [("--query", "absent"), ("--topic", "rese"), ("--offset", "10")]:
        result = read(registry, *args)
        assert result["returned_count"] == 0
        assert result["sources"] == []
        assert result["next_offset"] is None
    empty = run(registry, "authority-sources", "--goal-id", "empty")
    assert empty.returncode == 0
    assert json.loads(empty.stdout)["source_count"] == 0


def test_markdown_retains_recovery_metadata_and_does_not_dereference(registry: Path):
    before = registry.read_bytes()
    result = run(registry, "authority-sources", "--goal-id", "materials",
                 "--query", "Café", "--limit", "1", format="markdown")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "needs_access" in result.stdout
    assert "edition-2" in result.stdout
    assert "--offset 1" in result.stdout
    assert "current accessibility were not checked" in result.stdout
    assert "absent.md" not in result.stdout
    assert registry.read_bytes() == before


@pytest.mark.parametrize("args", [("--limit", "0"), ("--limit", "101"), ("--offset", "-1")])
def test_invalid_page_does_not_write(registry: Path, args: tuple[str, str]):
    before = registry.read_bytes()
    result = run(registry, "authority-sources", "--goal-id", "materials", *args)
    assert result.returncode != 0
    assert json.loads(result.stdout)["ok"] is False
    assert registry.read_bytes() == before


def test_missing_goal_and_unreadable_registry_fail_without_creating_state(tmp_path: Path):
    registry = tmp_path / "missing.json"
    result = run(registry, "authority-sources", "--goal-id", "missing")
    assert result.returncode != 0
    assert not registry.exists()
    registry.write_text('{"goals": []}', encoding="utf-8")
    result = run(registry, "authority-sources", "--goal-id", "missing")
    assert result.returncode != 0
    assert "goal id not found" in result.stdout
    registry.write_text("not json", encoding="utf-8")
    result = run(registry, "authority-sources", "--goal-id", "missing")
    assert result.returncode != 0
    assert registry.read_text() == "not json"
