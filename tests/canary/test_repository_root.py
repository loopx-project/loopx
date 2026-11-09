"""A selected checkout owns canary discovery, execution and write protection."""

from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.canary.planner import DEFAULT_CATALOG_PATH
from loopx.canary.premerge import build_premerge_validation_gate
from loopx.canary.runner import build_canary_smoke_suite_run, normalize_canary_command
from loopx.cli import main
from tests.source_cli import run_source_cli


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _checkout(
    path: Path,
    *,
    mutate: bool = False,
    tracked_name: str = "tracked.txt",
    quote_path: bool = True,
) -> Path:
    examples = path / "examples/canary"
    examples.mkdir(parents=True)
    (path / tracked_name).write_text("original", encoding="utf-8")
    (examples / "catalog-root-smoke.py").write_text(
        "from pathlib import Path\n"
        "assert Path.cwd() == Path(__file__).resolve().parents[2]\n"
        + (
            f"Path({tracked_name!r}).write_text('changed')\n"
            if mutate else "print(Path.cwd())\n"
        ),
        encoding="utf-8",
    )
    catalog = path / "docs/concepts/interaction-pattern-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_bytes(DEFAULT_CATALOG_PATH.read_bytes())
    _git(path, "init")
    _git(path, "config", "user.name", "Canary Fixture")
    _git(path, "config", "user.email", "canary@example.invalid")
    if not quote_path:
        _git(path, "config", "core.quotePath", "false")
    _git(path, "add", tracked_name, "examples", "docs")
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


def _selected_checks(payload: dict) -> list[dict]:
    checks = [check for check in payload.get("selected_checks") or [] if isinstance(check, dict)]
    for section in ("catalog_run", "risk_profile_run"):
        run = payload.get(section)
        if isinstance(run, dict):
            checks.extend(check for check in run.get("selected_checks") or [] if isinstance(check, dict))
    return checks


def _assert_canonical_path(rendered: str) -> None:
    """A repository-relative path keeps the canonical POSIX spelling.

    ``PurePosixPath`` would leave a host separator untouched, so the stray
    backslash is rejected explicitly rather than through a normalization that
    already agrees with the value.
    """

    assert "\\" not in rendered, rendered
    assert rendered == Path(rendered).as_posix(), rendered


def test_plan_renders_repository_paths_in_canonical_posix_form(tmp_path):
    """A plan's repository-relative paths keep the canonical spelling everywhere.

    The catalog, the suite checks, the shipped contract smokes and the public
    docs all declare ``python3 examples/<...>.py``. Rendering the same path with
    the host separator makes one check read differently per host, which fails
    those smokes on Windows and records host-dependent premerge evidence.
    """

    repo = _checkout(tmp_path / "repo")
    suite = build_canary_smoke_suite_run(repo_root=repo)
    gate = build_premerge_validation_gate(
        repo_root=repo, changed_files=["loopx/canary/runner.py"],
        base_ref="HEAD", execute=False,
    )
    checks = _selected_checks(suite) + _selected_checks(gate)
    rendered = []
    for check in checks:
        normalized = check.get("normalized") or {}
        if normalized.get("ok") is not True:
            continue
        rendered.append(str(normalized.get("script") or ""))
        rendered.extend(
            str(token) for token in normalized.get("display_argv") or [] if "examples" in str(token)
        )
    commands = [str(item) for item in gate["validation_summary"]["selected_commands"]]
    assert rendered and commands
    for item in [*rendered, *commands]:
        _assert_canonical_path(item)


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


@pytest.mark.skipif(os.name != "posix", reason="NEL in a pathname is POSIX-only")
def test_write_guard_restores_a_path_with_a_raw_unicode_separator(tmp_path):
    tracked_name = "tracked\x85name.txt"
    repo = _checkout(
        tmp_path / "repo",
        mutate=True,
        tracked_name=tracked_name,
        quote_path=False,
    )

    payload = build_canary_smoke_suite_run(repo_root=repo, parallel_jobs=1)

    assert payload["ok"] is False
    assert payload["tracked_side_effect_failure_count"] == 1
    assert payload["side_effect_guard"]["auto_restored"] is True
    failure = next(
        check for check in payload["selected_checks"] if not check.get("ok")
    )
    assert failure["tracked_side_effects"] == [tracked_name]
    assert (repo / tracked_name).read_text(encoding="utf-8") == "original"


@pytest.mark.skipif(os.name != "posix", reason="non-UTF-8 Git path bytes are POSIX-only")
def test_write_guard_restores_distinct_paths_with_the_same_lossy_text(tmp_path):
    tracked_name = "lossy-\ufffd.txt"
    repo = _checkout(tmp_path / "repo", tracked_name=tracked_name)
    smoke = repo / "examples/canary/catalog-root-smoke.py"
    smoke.write_text(
        "import subprocess\n"
        "from pathlib import Path\n"
        "repo = Path.cwd()\n"
        "objects = []\n"
        "for content in (b'one\\n', b'two\\n'):\n"
        "    completed = subprocess.run(\n"
        "        ['git', 'hash-object', '-w', '--stdin'],\n"
        "        input=content, stdout=subprocess.PIPE, check=True,\n"
        "    )\n"
        "    objects.append(completed.stdout.strip())\n"
        "records = (\n"
        "    b'100644 ' + objects[0] + b' 0\\tlossy-\\xfe.txt\\0'\n"
        "    + b'100644 ' + objects[1] + b' 0\\tlossy-\\xff.txt\\0'\n"
        ")\n"
        "subprocess.run(\n"
        "    ['git', 'update-index', '-z', '--index-info'],\n"
        "    input=records, check=True,\n"
        ")\n",
        encoding="utf-8",
    )
    _git(repo, "add", "examples/canary/catalog-root-smoke.py")
    _git(repo, "commit", "-m", "stage two distinct non-UTF-8 paths")

    payload = build_canary_smoke_suite_run(repo_root=repo, parallel_jobs=1)
    failure = next(check for check in payload["selected_checks"] if not check.get("ok"))
    expected_names = {repr(b"lossy-\xfe.txt"), repr(b"lossy-\xff.txt")}

    assert payload["ok"] is False
    assert payload["tracked_side_effect_failure_count"] == 1
    assert payload["side_effect_guard"]["auto_restored"] is True
    assert set(failure["tracked_side_effects"]) == expected_names
    restore = failure["tracked_side_effect_restore"]
    assert restore["ok"] is True
    assert restore["verified_by_git"] is True
    assert restore["remaining_paths"] == []
    assert (repo / tracked_name).read_text(encoding="utf-8") == "original"
    raw = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    assert raw == b""


def test_concurrent_checkout_roots_do_not_share_process_state(tmp_path):
    repos = [_checkout(tmp_path / name) for name in ("one", "two")]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda repo: build_canary_smoke_suite_run(repo_root=repo), repos,
        ))
    for repo, result in zip(repos, results):
        assert result["ok"]
        assert str(repo) in result["selected_checks"][0]["stdout_tail"]


_CLI_BUDGET_SCRIPT = "examples/control_plane/cli-output-budget-regression-smoke.py"
_CLI_BUDGET_ENV = ("LOOPX_CLI_OUTPUT_BASE_REF", "LOOPX_CLI_OUTPUT_MAIN_REF")
_NEIGHBOR_SCRIPT = "examples/semantic-vocabulary-drift-smoke.py"
_AMBIENT_REFS = ("ambient-base", "ambient-main")


def _cli_budget_checkout(path: Path) -> Path:
    repo = _checkout(path)
    probe = (
        "import json, os\n"
        f"print(json.dumps([os.environ.get(name) for name in {_CLI_BUDGET_ENV!r}]))\n"
    )
    for script in (
        _NEIGHBOR_SCRIPT,
        "examples/control_plane/control-plane-maintainability-ratchet-smoke.py",
        _CLI_BUDGET_SCRIPT,
    ):
        child = repo / script
        child.parent.mkdir(parents=True, exist_ok=True)
        fail = (
            'if os.environ.get("CANARY_FIXTURE_FAIL") == "1":\n'
            "    raise SystemExit(7)\n"
            if script == _CLI_BUDGET_SCRIPT else ""
        )
        child.write_text(probe + fail, encoding="utf-8")
    metrics = Path("loopx/canary/module_metric_baseline.json")
    (repo / metrics).parent.mkdir(parents=True)
    (repo / metrics).write_bytes((DEFAULT_CATALOG_PATH.parents[2] / metrics).read_bytes())
    _git(repo, "add", "examples", "loopx")
    _git(repo, "commit", "-m", "install synthetic validation probes")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "commit", "--allow-empty", "-m", "integration target")
    _git(repo, "branch", "integration/base")
    changed = repo / "loopx/control_plane/testing/cli_output_budget.py"
    changed.parent.mkdir(parents=True)
    changed.write_text("# Exercise CLI output budget selection.\n", encoding="utf-8")
    _git(repo, "add", "loopx")
    _git(repo, "commit", "-m", "select CLI output budget check")
    return repo


def _check_for_script(run: dict, script: str) -> dict:
    return next(item for item in run["selected_checks"] if item["normalized"]["script"] == script)


@pytest.mark.parametrize(
    ("base_args", "expected_base", "child_exit"),
    [
        (["--git-diff-base", "integration/base"], "integration/base", 0),
        ([], "origin/main", 0),
        (["--git-diff-base", "integration/base"], "integration/base", 7),
    ],
)
def test_premerge_scopes_cli_budget_base_to_its_child(tmp_path, base_args, expected_base, child_exit):
    repo = _cli_budget_checkout(tmp_path / "repo")
    env = {**os.environ, **dict(zip(_CLI_BUDGET_ENV, _AMBIENT_REFS)), "CANARY_FIXTURE_FAIL": str(int(child_exit != 0))}
    response = run_source_cli(
        ["--format", "json", "canary", "premerge", "--from-git-diff", "--tier", "quick",
         "--no-progress", *base_args],
        cwd=repo, env=env, check=False, timeout=60,
    )
    assert response.stdout, response.stderr
    payload = json.loads(response.stdout)
    assert payload["base_ref"] == expected_base
    budget = _check_for_script(payload["catalog_run"], _CLI_BUDGET_SCRIPT)
    neighbor = _check_for_script(payload["catalog_run"], _NEIGHBOR_SCRIPT)
    assert json.loads(budget["stdout_tail"]) == [expected_base, expected_base]
    assert json.loads(neighbor["stdout_tail"]) == list(_AMBIENT_REFS)
    assert budget["returncode"] == child_exit
    assert budget["ok"] is (child_exit == 0)
    assert payload["validation_summary"]["failure_count"] == int(child_exit != 0)
    if child_exit:
        assert response.returncode != 0 and payload["gate"]["merge_gate_passed"] is False


def test_cli_budget_children_keep_independent_bases_and_standalone_defaults(tmp_path, monkeypatch):
    repo = _cli_budget_checkout(tmp_path / "repo")
    for name, value in zip(_CLI_BUDGET_ENV, _AMBIENT_REFS):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CANARY_FIXTURE_FAIL", raising=False)

    def run(base: str | None) -> dict:
        return build_canary_smoke_suite_run(
            repo_root=repo, scripts=[_CLI_BUDGET_SCRIPT, _NEIGHBOR_SCRIPT],
            parallel_jobs=2, cli_output_base_ref=base,
        )

    bases = ("integration/base", "origin/main", None)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(run, bases))
    for base, result in zip(bases, results):
        assert result["ok"] and result["effective_parallel_jobs"] == 2
        budget = _check_for_script(result, _CLI_BUDGET_SCRIPT)
        assert json.loads(budget["stdout_tail"]) == ([base, base] if base else list(_AMBIENT_REFS))
        neighbor = _check_for_script(result, _NEIGHBOR_SCRIPT)
        assert json.loads(neighbor["stdout_tail"]) == list(_AMBIENT_REFS)
    assert tuple(os.environ[name] for name in _CLI_BUDGET_ENV) == _AMBIENT_REFS


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
