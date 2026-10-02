"""Development-time vocabulary prompts arrive before the full-tree check."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.semantics.development_probe import (
    DevelopmentProbeError,
    build_development_probe,
    collect_changed_sources,
    render_development_probe,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Fixture"],
        check=True,
    )
    _write(
        tmp_path,
        "loopx/a.py",
        'from typing import Literal\nMode = Literal["fast", "slow"]\n',
    )
    _write(
        tmp_path,
        "loopx/semantics/vocabulary_v0.json",
        '{"vocabularies":{"mode":{"values":["fast","slow","safe"],'
        '"owners":{"python":"loopx/owner.py::Mode"}}}}\n',
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "loopx"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "baseline"], check=True
    )
    return tmp_path


def _registry() -> dict:
    return {
        "vocabularies": {
            "mode": {
                "values": ["fast", "slow", "safe"],
                "owners": {"python": "loopx/owner.py::Mode"},
            }
        }
    }


def test_probe_reads_staged_working_and_explicit_untracked_sources(
    repository: Path,
) -> None:
    path = repository / "loopx/a.py"
    path.write_text(
        'from typing import Literal\nMode = Literal["fast", "slow", "safe"]\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repository), "add", "loopx/a.py"], check=True)
    path.write_text(
        'from typing import Literal\nMode = Literal["fast", "slow", "safe", "turbo"]\n',
        encoding="utf-8",
    )
    _write(
        repository,
        "loopx/new.ts",
        'export const OTHER_MODES = ["fast", "safe"] as const;\n',
    )

    baseline, changes = collect_changed_sources(
        repository,
        baseline="HEAD",
        explicit_untracked=["loopx/new.ts"],
    )
    report = build_development_probe(
        baseline=baseline,
        changes=changes,
        registry=_registry(),
    )

    assert [change.scope for change in changes] == [
        "staged",
        "working_tree",
        "explicit_untracked",
    ]
    assert report["changed_path_count"] == 2
    assert report["changed_source_count"] == 3
    assert report["candidate_count"] == 3
    staged, working, untracked = report["candidates"]
    assert (staged["path"], staged["scope"]) == ("loopx/a.py", "staged")
    assert staged["symbol"] == "Mode"
    assert staged["added_values"] == ["safe"]
    assert staged["reuse_hints"][0]["match"] == "exact_values"
    assert (working["path"], working["scope"]) == ("loopx/a.py", "working_tree")
    assert working["added_values"] == ["safe", "turbo"]
    assert working["reuse_hints"][0] == {
        "vocabulary": "mode",
        "match": "overlapping_values",
        "shared_values": ["fast", "safe", "slow"],
        "owners": {"python": "loopx/owner.py::Mode"},
    }
    assert untracked["scope"] == "explicit_untracked"
    assert untracked["change"] == "new_carrier"
    assert "Should this reuse an existing owner" in untracked["decision_question"]


def _mode_source(*values: str) -> str:
    listed = ", ".join(f'"{value}"' for value in values)
    return f'from typing import Literal\nMode = Literal[{listed}]\n'


def _stage(repository: Path, relative: str) -> None:
    subprocess.run(["git", "-C", str(repository), "add", relative], check=True)


def test_index_value_survives_when_the_worktree_is_restored(repository: Path) -> None:
    path = repository / "loopx/a.py"
    path.write_text(_mode_source("fast", "slow", "staged_only"), encoding="utf-8")
    _stage(repository, "loopx/a.py")
    path.write_text(_mode_source("fast", "slow"), encoding="utf-8")

    baseline, changes = collect_changed_sources(repository, baseline="HEAD")
    report = build_development_probe(
        baseline=baseline,
        changes=changes,
        registry=_registry(),
    )

    assert [change.scope for change in changes] == ["staged"]
    assert changes[0].after.text == _mode_source("fast", "slow", "staged_only")
    assert [item["added_values"] for item in report["candidates"]] == [["staged_only"]]


def test_disjoint_index_and_worktree_values_are_both_inspected(
    repository: Path,
) -> None:
    path = repository / "loopx/a.py"
    path.write_text(_mode_source("fast", "slow", "staged_only"), encoding="utf-8")
    _stage(repository, "loopx/a.py")
    path.write_text(_mode_source("fast", "slow", "working_only"), encoding="utf-8")

    baseline, changes = collect_changed_sources(repository, baseline="HEAD")
    report = build_development_probe(
        baseline=baseline,
        changes=changes,
        registry=_registry(),
    )

    assert [(change.scope, change.after.text) for change in changes] == [
        ("staged", _mode_source("fast", "slow", "staged_only")),
        ("working_tree", _mode_source("fast", "slow", "working_only")),
    ]
    assert [item["added_values"] for item in report["candidates"]] == [
        ["staged_only"],
        ["working_only"],
    ]


def test_one_path_shared_by_index_and_worktree_is_reported_once(
    repository: Path,
) -> None:
    path = repository / "loopx/a.py"
    path.write_text(_mode_source("fast", "slow", "safe"), encoding="utf-8")
    _stage(repository, "loopx/a.py")

    _, changes = collect_changed_sources(repository, baseline="HEAD")

    assert [change.scope for change in changes] == ["staged"]
    assert changes[0].after.text == _mode_source("fast", "slow", "safe")
    assert changes[0].before.text == _mode_source("fast", "slow")


def test_new_staged_source_is_read_from_the_index(repository: Path) -> None:
    _write(repository, "loopx/new.py", _mode_source("one", "two"))
    _stage(repository, "loopx/new.py")

    baseline, changes = collect_changed_sources(repository, baseline="HEAD")
    report = build_development_probe(
        baseline=baseline,
        changes=changes,
        registry=_registry(),
    )

    assert [change.scope for change in changes] == ["staged"]
    assert report["candidates"][0]["path"] == "loopx/new.py"
    assert report["candidates"][0]["change"] == "new_carrier"


def test_committed_change_is_read_as_the_index_snapshot(repository: Path) -> None:
    path = repository / "loopx/a.py"
    path.write_text(_mode_source("fast", "slow", "safe"), encoding="utf-8")
    _stage(repository, "loopx/a.py")
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "extend"], check=True)

    baseline, changes = collect_changed_sources(repository, baseline="HEAD~1")

    assert [change.scope for change in changes] == ["committed_since_baseline"]
    assert changes[0].after.text == _mode_source("fast", "slow", "safe")


def test_staged_deletion_is_never_read_as_a_source(repository: Path) -> None:
    subprocess.run(["git", "-C", str(repository), "rm", "-q", "loopx/a.py"], check=True)

    _, changes = collect_changed_sources(repository, baseline="HEAD")

    assert changes == []


def test_disjoint_index_and_worktree_values_are_both_inspected_for_typescript(
    repository: Path,
) -> None:
    committed = 'export const MODES = ["fast"] as const;\n'
    _write(repository, "loopx/modes.ts", committed)
    _stage(repository, "loopx/modes.ts")
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-qm", "add carrier"], check=True
    )

    path = repository / "loopx/modes.ts"
    path.write_text('export const MODES = ["fast", "safe"] as const;\n', encoding="utf-8")
    _stage(repository, "loopx/modes.ts")
    path.write_text('export const MODES = ["fast", "turbo"] as const;\n', encoding="utf-8")

    baseline, changes = collect_changed_sources(repository, baseline="HEAD")
    report = build_development_probe(
        baseline=baseline,
        changes=changes,
        registry=_registry(),
    )

    assert [(change.scope, change.after.text) for change in changes] == [
        ("staged", 'export const MODES = ["fast", "safe"] as const;\n'),
        ("working_tree", 'export const MODES = ["fast", "turbo"] as const;\n'),
    ]
    assert [(item["carrier_kind"], item["added_values"]) for item in report["candidates"]] == [
        ("typescript_const_array", ["safe"]),
        ("typescript_const_array", ["turbo"]),
    ]


def test_candidate_findings_render_as_advice_with_scope_limits(
    repository: Path,
) -> None:
    _write(
        repository,
        "loopx/a.py",
        'from typing import Literal\nMode = Literal["fast", "slow", "safe"]\n',
    )
    baseline, changes = collect_changed_sources(repository, baseline="HEAD")
    rendered = render_development_probe(
        build_development_probe(
            baseline=baseline,
            changes=changes,
            registry=_registry(),
        )
    )

    assert "advisory; findings do not fail the command" in rendered
    assert "loopx/a.py::Mode" in rendered
    assert "reuse hint: mode (exact_values" in rendered
    assert "Dynamic construction" in rendered


def test_removal_does_not_claim_that_the_supported_scan_proved_no_semantics(
    repository: Path,
) -> None:
    _write(repository, "loopx/a.py", "VALUE = 1\n")
    baseline, changes = collect_changed_sources(repository, baseline="HEAD")
    report = build_development_probe(
        baseline=baseline,
        changes=changes,
        registry=_registry(),
    )

    assert report["candidate_count"] == 0
    rendered = render_development_probe(report)
    assert "No supported new vocabulary carriers" in rendered
    assert "not proof that no new semantics" in rendered


def test_untracked_input_must_be_explicit_visible_source(repository: Path) -> None:
    _write(repository, ".gitignore", "loopx/ignored.py\n")
    _write(repository, "loopx/ignored.py", 'STATES = ("one", "two")\n')

    with pytest.raises(DevelopmentProbeError, match="missing, ignored, or not untracked"):
        collect_changed_sources(
            repository,
            baseline="HEAD",
            explicit_untracked=["loopx/ignored.py"],
        )


@pytest.mark.parametrize("path", ["../private.py", "/tmp/private.py", "docs/example.py"])
def test_explicit_source_cannot_escape_the_loopx_root(
    repository: Path, path: str
) -> None:
    with pytest.raises(DevelopmentProbeError, match="repository-relative|outside loopx"):
        collect_changed_sources(
            repository,
            baseline="HEAD",
            explicit_untracked=[path],
        )


def test_missing_baseline_is_a_tool_error(repository: Path) -> None:
    with pytest.raises(DevelopmentProbeError):
        collect_changed_sources(repository, baseline="missing-revision")


CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
PROBE_CLI_FILES = (
    "scripts/generate_semantic_inventory.py",
    "loopx/__init__.py",
    "loopx/semantics/__init__.py",
    "loopx/semantics/inventory.py",
    "loopx/semantics/development_probe.py",
    "loopx/semantics/consumer_report.py",
    "loopx/semantics/python_production.py",
)


@pytest.fixture
def probe_cli(repository: Path) -> Path:
    """Copy the unmodified development entrypoint into an isolated repository."""
    for relative in PROBE_CLI_FILES:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((CHECKOUT_ROOT / relative).read_bytes())
    return repository


def _run_probe_cli(repository: Path) -> subprocess.CompletedProcess[str]:
    # The copied CLI runs in a disposable repository. Do not merge its coverage
    # into the source checkout: pytest removes that repository before CI reports.
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COV_CORE_", "COVERAGE_"))
    }
    return subprocess.run(
        [
            sys.executable,
            "scripts/generate_semantic_inventory.py",
            "--changed-from",
            "HEAD",
        ],
        cwd=repository,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_keeps_the_index_value_after_the_worktree_is_restored(
    probe_cli: Path,
) -> None:
    path = probe_cli / "loopx/a.py"
    path.write_text(_mode_source("fast", "slow", "staged_only"), encoding="utf-8")
    _stage(probe_cli, "loopx/a.py")
    path.write_text(_mode_source("fast", "slow"), encoding="utf-8")

    completed = _run_probe_cli(probe_cli)

    assert completed.returncode == 0, completed.stderr
    assert "changed sources: 1 paths, 1 snapshots" in completed.stdout
    assert "loopx/a.py::Mode [python_literal_alias; staged]" in completed.stdout
    assert "added values: staged_only" in completed.stdout


def test_cli_inspects_disjoint_index_and_worktree_values(probe_cli: Path) -> None:
    path = probe_cli / "loopx/a.py"
    path.write_text(_mode_source("fast", "slow", "staged_only"), encoding="utf-8")
    _stage(probe_cli, "loopx/a.py")
    path.write_text(_mode_source("fast", "slow", "working_only"), encoding="utf-8")

    completed = _run_probe_cli(probe_cli)

    assert completed.returncode == 0, completed.stderr
    assert "changed sources: 1 paths, 2 snapshots" in completed.stdout
    assert "loopx/a.py::Mode [python_literal_alias; staged]" in completed.stdout
    assert "loopx/a.py::Mode [python_literal_alias; working_tree]" in completed.stdout
    assert "added values: staged_only" in completed.stdout
    assert "added values: working_only" in completed.stdout
