"""Development-time vocabulary prompts arrive before the full-tree check."""

from __future__ import annotations

from pathlib import Path
import subprocess

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
        "staged_and_working_tree",
        "explicit_untracked",
    ]
    assert report["candidate_count"] == 2
    tracked, untracked = report["candidates"]
    assert tracked["symbol"] == "Mode"
    assert tracked["added_values"] == ["safe", "turbo"]
    assert tracked["reuse_hints"][0] == {
        "vocabulary": "mode",
        "match": "overlapping_values",
        "shared_values": ["fast", "safe", "slow"],
        "owners": {"python": "loopx/owner.py::Mode"},
    }
    assert untracked["scope"] == "explicit_untracked"
    assert untracked["change"] == "new_carrier"
    assert "Should this reuse an existing owner" in untracked["decision_question"]


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
