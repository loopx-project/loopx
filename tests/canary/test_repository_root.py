"""A selected checkout owns canary discovery, execution and write protection."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.canary.planner import DEFAULT_CATALOG_PATH
from loopx.canary.premerge import build_premerge_validation_gate
from loopx.canary.runner import build_canary_smoke_suite_run, normalize_canary_command
from loopx.cli import main


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _checkout(path: Path, *, mutate: bool = False) -> Path:
    examples = path / "examples/canary"
    examples.mkdir(parents=True)
    (path / "tracked.txt").write_text("original", encoding="utf-8")
    (examples / "catalog-root-smoke.py").write_text(
        "from pathlib import Path\n"
        "assert Path.cwd() == Path(__file__).resolve().parents[2]\n"
        + ("Path('tracked.txt').write_text('changed')\n" if mutate else "print(Path.cwd())\n"),
        encoding="utf-8",
    )
    catalog = path / "docs/concepts/interaction-pattern-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_bytes(DEFAULT_CATALOG_PATH.read_bytes())
    _git(path, "init")
    _git(path, "config", "user.name", "Canary Fixture")
    _git(path, "config", "user.email", "canary@example.invalid")
    _git(path, "add", "tracked.txt", "examples", "docs")
    _git(path, "commit", "-m", "fixture")
    return path


def test_premerge_uses_selected_checkout_for_nested_plans(tmp_path):
    repo = _checkout(tmp_path / "repo")
    payload = build_premerge_validation_gate(
        repo_root=repo, changed_files=["loopx/canary/runner.py"],
        base_ref="HEAD", execute=False,
    )
    for section in ("catalog_run", "risk_profile_run"):
        run = payload[section]
        assert run["repo_root"] == str(repo)
        assert run["selected_checks"]
        for check in run["selected_checks"]:
            assert Path(check["normalized"]["argv"][1]).is_relative_to(repo / "examples")


@pytest.mark.parametrize("jobs", [1, 2])
def test_target_checkout_write_guard_detects_and_restores(tmp_path, jobs):
    repo = _checkout(tmp_path / "repo", mutate=True)
    # Two scripts exercise the parallel as well as the sequential guard.
    second = repo / "examples/second-smoke.py"
    second.write_text("print('second')\n", encoding="utf-8")
    _git(repo, "add", "examples/second-smoke.py")
    _git(repo, "commit", "-m", "second")
    payload = build_canary_smoke_suite_run(repo_root=repo, parallel_jobs=jobs)
    assert not payload["ok"]
    assert payload["tracked_side_effect_failure_count"] == 1
    assert payload["side_effect_guard"]["auto_restored"]
    assert (repo / "tracked.txt").read_text() == "original"


def test_concurrent_checkout_roots_do_not_share_process_state(tmp_path):
    repos = [_checkout(tmp_path / name) for name in ("one", "two")]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda repo: build_canary_smoke_suite_run(repo_root=repo), repos,
        ))
    for repo, result in zip(repos, results):
        assert result["ok"]
        assert str(repo) in result["selected_checks"][0]["stdout_tail"]


@pytest.mark.parametrize("command", [
    "python examples/../../escape.py", "python examples/root-smoke.py ; echo unsafe",
    "python ../elsewhere/examples/root-smoke.py",
])
def test_explicit_root_keeps_command_confinement(tmp_path, command):
    repo = _checkout(tmp_path / "repo")
    assert not normalize_canary_command(command, repo_root=repo)["ok"]


def test_example_symlink_cannot_escape_selected_checkout(tmp_path):
    repo = _checkout(tmp_path / "repo")
    outside = tmp_path / "outside.py"
    outside.write_text("raise AssertionError('must not execute')\n", encoding="utf-8")
    (repo / "examples/escape.py").symlink_to(outside)
    assert not normalize_canary_command("python examples/escape.py", repo_root=repo)["ok"]


def test_catalog_override_is_forwarded_from_cli_without_install_mutation(tmp_path, monkeypatch, capsys):
    repo = _checkout(tmp_path / "repo")
    (repo / "docs/concepts/interaction-pattern-catalog.md").unlink()
    monkeypatch.chdir(repo / "examples")
    code = main([
        "--format", "json", "canary", "premerge", "--changed-file", "app.py",
        "--git-diff-base", "HEAD", "--catalog", str(DEFAULT_CATALOG_PATH), "--no-execute",
    ])
    assert code == 0, capsys.readouterr().out
    assert not (repo / "docs/concepts/interaction-pattern-catalog.md").exists()


def test_missing_checkout_inputs_fail_instead_of_empty_success(tmp_path):
    with pytest.raises(ValueError, match="catalog.*--catalog"):
        build_premerge_validation_gate(repo_root=tmp_path, changed_files=["app.py"], execute=False)
    with pytest.raises(ValueError, match="examples.*checkout"):
        build_canary_smoke_suite_run(repo_root=tmp_path)
