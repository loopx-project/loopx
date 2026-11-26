"""Contract, zero-Any, and malformed-input tests for semantic inventory modules."""

from __future__ import annotations

import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tokenize

import pytest

from loopx.canary.maintainability_ratchet import (
    DECISION_POINT_LIMIT,
    DECISION_STATEMENT_LIMIT,
    _FunctionMetrics,
    _iter_functions,
)
from loopx.semantics import inventory, inventory_reports

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ast_any_count(source: str) -> int:
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "Any")
        or (isinstance(node, ast.Attribute) and node.attr == "Any")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "Any" for alias in node.names)
        )
    )


def _comment_ignore_or_noqa_count(source: str) -> int:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return sum(
        1
        for tok in tokens
        if tok.type == tokenize.COMMENT
        and ("type: ignore" in tok.string or "noqa" in tok.string)
    )


def test_inventory_modules_meet_zero_any_size_and_reexport_contracts() -> None:
    inv_path = REPO_ROOT / "loopx" / "semantics" / "inventory.py"
    rep_path = REPO_ROOT / "loopx" / "semantics" / "inventory_reports.py"
    for path in (inv_path, rep_path, Path(__file__)):
        source = path.read_text(encoding="utf-8")
        assert _ast_any_count(source) == 0, path
        assert _comment_ignore_or_noqa_count(source) == 0, path

    assert len(inv_path.read_text(encoding="utf-8").splitlines()) <= 600
    assert len(rep_path.read_text(encoding="utf-8").splitlines()) < 750

    for path in (inv_path, rep_path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for qname, fn in _iter_functions(tree.body):
            metrics = _FunctionMetrics(fn)
            metrics.visit(fn)
            assert metrics.statement_count <= DECISION_STATEMENT_LIMIT, (path, qname)
            assert metrics.decision_points <= DECISION_POINT_LIMIT, (path, qname)

    for symbol in (
        "SourceFile",
        "_word_token_mentions",
        "consumer_ranking",
        "divergent_value_sets",
        "merge_candidate_groups",
        "registered_owner_symbol_sets",
        "render_inventory",
    ):
        assert getattr(inventory, symbol) is getattr(inventory_reports, symbol), symbol


def test_load_sources_rejects_tracked_symlinks(tmp_path: Path) -> None:
    loopx_dir = tmp_path / "loopx"
    loopx_dir.mkdir()
    target = loopx_dir / "real.py"
    target.write_text('NAME = "ok"\n', encoding="utf-8")
    link = loopx_dir / "linked.py"
    link.symlink_to(target)

    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "loopx/real.py", "loopx/linked.py"], cwd=tmp_path, check=True)

    with pytest.raises(ValueError, match="tracked source must not be a symlink: loopx/linked.py"):
        inventory.load_sources(tmp_path)


def test_python_and_typescript_facts_handle_boundaries_and_invalid_syntax() -> None:
    single_literal = inventory.SourceFile(
        path="loopx/single_literal.py",
        suffix=".py",
        text=(
            "from enum import Enum\n"
            "from typing import Literal\n"
            "class CustomEnum(str, Enum):\n"
            "    _IGNORED = 'priv'\n"
            "    A = 'alpha'\n"
            "class BrokenEnum(Enum):\n"
            "    A = 'alpha'\n"
            "    B = 42\n"
            "SoloLit = Literal['only_one']\n"
            "MixedLit = Literal['a', 1]\n"
            "SINGLE_SET = {'only'}\n"
            "PAIR_FROZEN = frozenset({'b', 'a'})\n"
            "lower_case = ('a', 'b')\n"
        ),
    )
    facts = inventory.python_facts(single_literal)
    assert facts["enums"] == [{"name": "CustomEnum", "module": "loopx/single_literal.py", "values": ["alpha"]}]
    assert facts["literal_aliases"] == [
        {"name": "SoloLit", "module": "loopx/single_literal.py", "values": ["only_one"]}
    ]
    assert facts["closed_sets"] == [
        {
            "name": "PAIR_FROZEN",
            "module": "loopx/single_literal.py",
            "container": "frozenset",
            "values": ["b", "a"],
        }
    ]

    ts_src = inventory.SourceFile(
        path="loopx/sample.ts",
        suffix=".ts",
        text=(
            'export const ACTIONS = ["run", "wait"] as const;\n'
            'export const EMPTY_LIST = [] as const;\n'
            'export const SAMPLE_SCHEMA_VERSION: string = "sample_v1";\n'
        ),
    )
    collected = inventory.collect_string_constants([single_literal, ts_src])
    assert collected == [
        {"name": "SAMPLE_SCHEMA_VERSION", "module": "loopx/sample.ts", "value": "sample_v1"}
    ]

    broken = inventory.SourceFile(path="loopx/broken.py", suffix=".py", text="X = (\n")
    with pytest.raises(ValueError, match="cannot inventory invalid Python source: loopx/broken.py:1"):
        inventory.parse_python(broken)


def test_reports_handle_malformed_containers_non_word_tokens_and_roundtrip() -> None:
    with pytest.raises(ValueError, match="vocabularies must be a mapping"):
        inventory.registered_owner_symbol_sets({"vocabularies": "not-a-map"})
    with pytest.raises(ValueError, match="vocabulary entry must be a mapping"):
        inventory.registered_owner_symbol_sets({"vocabularies": {"broken": "not-a-map"}})
    with pytest.raises(ValueError, match="owners must be a mapping"):
        inventory.registered_owner_symbol_sets(
            {"vocabularies": {"broken": {"owners": "not-a-map"}}}
        )
    assert inventory.registered_owner_symbol_sets({"vocabularies": {"no_owners": {}}}) == set()
    assert (
        inventory.registered_owner_symbol_sets({"vocabularies": {"none_owners": {"owners": None}}})
        == set()
    )
    with pytest.raises(KeyError):
        inventory.registered_owner_symbol_sets({})

    empty_duplicates = {
        "duplicate_definitions": "malformed",
    }
    assert inventory.divergent_value_sets(empty_duplicates) == []

    inv: dict[str, object] = {
        "python_enums": [
            {"name": "FOO-BAR", "module": "loopx/a.py", "values": ["x", "y"]},
            {"name": "TOO_SHORT", "module": "loopx/a.py", "values": ["only"]},
        ],
        "python_closed_sets": [],
        "python_literal_aliases": [],
        "typescript_const_arrays": [
            {"name": "TS_KIND", "module": "loopx/b.ts", "values": ["y", "x"]},
        ],
        "duplicate_definitions": {
            "cross_runtime_twins": [],
            "multi_value_forks": [],
        },
        "summary": {"source_files": 2},
    }
    sources = [
        inventory.SourceFile(path="loopx/a.py", suffix=".py", text="FOO-BAR"),
        inventory.SourceFile(path="loopx/c.py", suffix=".py", text="# mentions FOO-BAR"),
        inventory.SourceFile(path="loopx/c.py", suffix=".py", text="TS_KIND"),
    ]
    ranking = inventory.consumer_ranking(inv, sources)
    assert [row["name"] for row in ranking] == ["FOO-BAR", "TS_KIND", "TOO_SHORT"]
    assert ranking[0]["external_consumer_modules"] == 1
    assert ranking[1]["external_consumer_modules"] == 1

    groups = inventory.merge_candidate_groups(inv)
    assert groups == [
        {
            "names": ["FOO-BAR", "TS_KIND"],
            "values": ["x", "y"],
            "modules": ["loopx/a.py", "loopx/b.ts"],
            "cross_runtime": True,
        }
    ]

    rendered = inventory.render_inventory(inv)
    assert json.loads(rendered) == inv
    assert rendered.endswith("\n")


_REPORT_CLI_FILES = (
    "scripts/generate_semantic_inventory.py",
    "loopx/__init__.py",
    "loopx/semantics/__init__.py",
    "loopx/semantics/inventory.py",
    "loopx/semantics/inventory_reports.py",
    "loopx/semantics/development_probe.py",
    "loopx/semantics/consumer_report.py",
    "loopx/semantics/field_use.py",
    "loopx/semantics/production.py",
    "loopx/semantics/python_production.py",
)


def _run_report_cli(repo_root: Path) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COV_CORE_", "COVERAGE_"))
    }
    return subprocess.run(
        [sys.executable, "scripts/generate_semantic_inventory.py", "--report"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_report_cli_fails_closed_on_malformed_registry(tmp_path: Path) -> None:
    for relative in _REPORT_CLI_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative).read_bytes())

    sample = tmp_path / "loopx" / "sample_vocab.py"
    sample.write_text('SAMPLE_STATES = ("alpha", "beta")\n', encoding="utf-8")
    registry_path = tmp_path / "loopx" / "semantics" / "vocabulary_v0.json"
    registry_path.write_text(
        json.dumps(
            {
                "vocabularies": {
                    "sample_state": {
                        "owners": {"python": "loopx/sample_vocab.py::SAMPLE_STATES"}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    valid = _run_report_cli(tmp_path)
    assert valid.returncode == 0, valid.stderr
    assert "merge candidates (advisory, never auto-merged):" in valid.stdout

    for payload, expected_diagnostic in (
        ({"vocabularies": "not-a-map"}, "vocabularies map"),
        ({"vocabularies": {"broken": "not-a-map"}}, "vocabulary entry must be a mapping"),
        ({"vocabularies": {"broken": {"owners": "not-a-map"}}}, "owners must be a mapping"),
    ):
        registry_path.write_text(json.dumps(payload), encoding="utf-8")
        failed = _run_report_cli(tmp_path)
        assert failed.returncode != 0
        assert "0 explained" not in failed.stdout
        assert expected_diagnostic in failed.stderr
