from __future__ import annotations

import copy
import json
import os
import runpy
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx import __version__
from loopx.control_plane.testing.actual_default_model_behavior_portfolio import (
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT,
)
from loopx.control_plane.testing.release_commit_qualification import (
    EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION,
    REQUIRED_QUALIFICATION_IDS,
    build_exact_release_commit_qualification,
    collect_release_source_identity,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
COMMIT = "1" * 40
TREE = "2" * 40
DIGEST = "sha256:" + "3" * 64


def _source(*, commit: str = COMMIT, tree: str = TREE, dirty: bool = False) -> dict[str, object]:
    return {
        "git_commit": commit,
        "git_tree": tree,
        "git_dirty": dirty,
        "package_version": __version__,
        "version_tag": f"v{__version__}",
    }


def _summary(qualification_id: str, *, commit: str = COMMIT) -> dict[str, object]:
    summaries: dict[str, dict[str, object]] = {
        "pytest": {"passed_count": 634, "failed_count": 0, "skipped_count": 2},
        "ruff": {"violation_count": 0},
        "mypy": {"error_count": 0},
        "risk_canary": {
            "selected_check_count": 13,
            "failure_count": 0,
            "manual_hold_count": 0,
        },
        "full_public": {
            "ready": True,
            "shard_count": 6,
            "failure_count": 0,
            "timeout_count": 0,
        },
        "install_upgrade_host": {
            "install_passed": True,
            "upgrade_passed": True,
            "host_passed": True,
            "packaged_frontend_passed": True,
        },
        "public_boundary": {"scanned_path_count": 8, "violation_count": 0},
        "doubao_actual_default": {
            "model_id": "doubao-seed-1.6",
            "topology": "actual_default_one_arm",
            "scenario_count": ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT,
            "contrast_count": ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT,
            "contrast_failure_count": 0,
            "repeats_per_scenario": ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
            "actor_call_count": (
                ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT
                * ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS
            ),
            "failure_count": 0,
            "skip_count": 0,
            "qualification_passed": True,
        },
    }
    return summaries[qualification_id]


def _check(
    qualification_id: str,
    *,
    source: dict[str, object] | None = None,
    commit: str = COMMIT,
) -> dict[str, object]:
    return {
        "schema_version": "exact_release_check_receipt_v0",
        "source": copy.deepcopy(source or _source(commit=commit)),
        "status": "passed",
        "result_schema_version": EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION[
            qualification_id
        ],
        "result_digest": DIGEST,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summary(qualification_id, commit=commit),
    }


def _manifest(
    *,
    source: dict[str, object] | None = None,
) -> dict[str, object]:
    candidate = source or _source()
    checks = {
        qualification_id: _check(qualification_id, source=candidate)
        for qualification_id in REQUIRED_QUALIFICATION_IDS
    }
    return {
        "schema_version": "exact_release_commit_qualification_manifest_v0",
        "candidate": candidate,
        "claims": {},
        "qualifications": checks,
    }


def test_exact_release_manifest_qualifies_required_release_checks() -> None:
    receipt = build_exact_release_commit_qualification(
        _manifest(),
        observed_source=_source(),
    )

    assert receipt["ready_for_release"] is True
    assert receipt["decision"] == "ready_for_owner_release"
    assert receipt["automatic_release_promotion_allowed"] is False
    assert receipt["claims"] == {}
    assert receipt["required_qualification_ids"] == list(REQUIRED_QUALIFICATION_IDS)
    assert receipt["qualification_failures"] == []
    assert receipt["source_mismatches"] == []
    assert receipt["read_boundary"]["model_api_invoked"] is False
    assert receipt["read_boundary"]["release_mutation_invoked"] is False


def test_source_identity_drift_and_dirty_checkout_fail_closed() -> None:
    manifest = _manifest()
    manifest["qualifications"]["full_public"]["source"]["git_tree"] = "4" * 40
    receipt = build_exact_release_commit_qualification(
        manifest,
        observed_source=_source(commit="5" * 40, dirty=True),
    )

    assert receipt["ready_for_release"] is False
    assert receipt["decision"] == "hold_source_mismatch"
    assert set(receipt["source_mismatches"]) >= {
        "full_public:git_tree",
        "observed_source:git_commit",
        "observed_source:git_dirty",
    }

    dirty_candidate = _manifest(source=_source(dirty=True))
    receipt = build_exact_release_commit_qualification(dirty_candidate)
    assert "candidate:git_dirty" in receipt["source_mismatches"]


def test_installation_success_cannot_qualify_a_failed_packaged_frontend() -> None:
    manifest = _manifest()
    manifest["qualifications"]["install_upgrade_host"]["summary"][
        "packaged_frontend_passed"
    ] = False
    receipt = build_exact_release_commit_qualification(manifest, observed_source=_source())

    assert receipt["ready_for_release"] is False
    assert "install_upgrade_host_failed" in receipt["qualification_failures"]

    del manifest["qualifications"]["install_upgrade_host"]["summary"][
        "packaged_frontend_passed"
    ]
    with pytest.raises(ValueError, match="packaged_frontend_passed"):
        build_exact_release_commit_qualification(manifest, observed_source=_source())


def test_older_installation_summary_does_not_qualify_the_packaged_frontend() -> None:
    manifest = _manifest()
    check = manifest["qualifications"]["install_upgrade_host"]
    check["result_schema_version"] = "release_install_upgrade_host_summary_v0"
    del check["summary"]["packaged_frontend_passed"]

    with pytest.raises(ValueError, match="release_install_upgrade_host_summary_v1"):
        build_exact_release_commit_qualification(manifest, observed_source=_source())


def test_failed_skipped_and_semantically_invalid_checks_do_not_qualify() -> None:
    manifest = _manifest()
    manifest["qualifications"]["ruff"]["status"] = "skipped"
    manifest["qualifications"]["doubao_actual_default"]["summary"][
        "contrast_failure_count"
    ] = 1
    receipt = build_exact_release_commit_qualification(manifest, observed_source=_source())

    assert receipt["ready_for_release"] is False
    assert receipt["decision"] == "hold_failed_qualification"
    assert set(receipt["qualification_failures"]) == {
        "doubao_actual_default_failed",
        "ruff_skipped",
    }


def test_manifest_rejects_unknown_or_noncompact_evidence() -> None:
    unknown = _manifest()
    unknown["qualifications"]["ad_hoc_smoke"] = _check("pytest")
    with pytest.raises(ValueError, match="unknown ids"):
        build_exact_release_commit_qualification(unknown)

    raw = _manifest()
    raw["qualifications"]["pytest"]["raw_log"] = "must not enter the manifest"
    with pytest.raises(ValueError, match="unknown fields"):
        build_exact_release_commit_qualification(raw)

    stale_claim = _manifest()
    stale_claim["claims"] = {"unsupported_claim": True}
    with pytest.raises(ValueError, match="unknown fields"):
        build_exact_release_commit_qualification(stale_claim)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def release_source_repo(tmp_path: Path) -> Path:
    source_repo = tmp_path / "source"
    (source_repo / "loopx").mkdir(parents=True)
    _git(source_repo, "init")
    _git(source_repo, "config", "user.email", "loopx@example.invalid")
    _git(source_repo, "config", "user.name", "LoopX Test")
    (source_repo / "source.txt").write_text("release source\n", encoding="utf-8")
    (source_repo / "pyproject.toml").write_text(
        '[project]\nname = "loopx"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    (source_repo / "loopx" / "__init__.py").write_text(
        '__version__ = "9.8.7"\n',
        encoding="utf-8",
    )
    _git(source_repo, "add", "source.txt", "pyproject.toml", "loopx/__init__.py")
    _git(source_repo, "commit", "-m", "release source")
    return source_repo


@pytest.mark.parametrize("module", [
    '__version__ = "9.8.7"\n',
    '__version__: str = "9.8.7"\n',
    '__version__ = "9.8.7"\nraise RuntimeError("candidate must not execute")\n',
])
def test_release_qualification_cli_matches_clean_git_source_and_is_read_only(
    tmp_path: Path, release_source_repo: Path, module: str,
) -> None:
    source_repo = release_source_repo
    (source_repo / "loopx/__init__.py").write_text(module, encoding="utf-8")
    _git(source_repo, "add", "loopx/__init__.py")
    _git(source_repo, "commit", "--allow-empty", "-m", "literal source identity")
    source = collect_release_source_identity(source_repo)
    manifest = _manifest(source=source)
    manifest_path = tmp_path / "qualification.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "release-qualification",
            "--manifest-json",
            str(manifest_path),
            "--repo-root",
            str(source_repo),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["ready_for_release"] is True
    assert payload["observed_source"] == source
    assert payload["candidate"]["package_version"] == "9.8.7"
    assert str(tmp_path) not in result.stdout

    (source_repo / "source.txt").write_text("dirty release source\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "release-qualification",
            "--manifest-json",
            str(manifest_path),
            "--repo-root",
            str(source_repo),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["decision"] == "hold_source_mismatch"
    assert payload["source_mismatches"] == ["observed_source:git_dirty"]


@pytest.mark.parametrize("metadata", [
    '[project]\nname = "loopx"\nversion = "9.8.6"\n',
    '[project]\nname = "loopx"\n',
    '[project]\nname = "loopx"\nversion = true\n',
    '[project\n',
])
def test_release_qualification_refuses_invalid_build_version(
    tmp_path: Path, release_source_repo: Path, metadata: str,
) -> None:
    """A clean commit and matching receipts cannot excuse invalid wheel metadata."""
    repo = release_source_repo
    (repo / "pyproject.toml").write_text(metadata, encoding="utf-8")
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-m", "inconsistent build metadata")
    source = {
        **_source(commit=_git(repo, "rev-parse", "HEAD"), tree=_git(repo, "rev-parse", "HEAD^{tree}")),
        "package_version": "9.8.7", "version_tag": "v9.8.7",
    }
    manifest = tmp_path / "qualification.json"
    manifest.write_text(json.dumps(_manifest(source=source)), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in (repo / "pyproject.toml", manifest)}
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--format", "json", "canary",
        "release-qualification", "--manifest-json", str(manifest), "--repo-root", str(repo),
    ], cwd=REPO_ROOT, capture_output=True, text=True, check=False)

    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ready_for_release"] is False
    assert payload["decision"] == "invalid_input"
    assert payload["error"] == "release package version is unavailable from the checkout"
    assert str(tmp_path) not in result.stdout
    assert payload["read_boundary"]["model_api_invoked"] is False
    assert payload["read_boundary"]["release_mutation_invoked"] is False
    assert _git(repo, "status", "--porcelain") == ""
    assert before == {path.name: path.read_bytes() for path in (repo / "pyproject.toml", manifest)}


def test_release_qualification_cli_redacts_manifest_path_errors(tmp_path: Path) -> None:
    missing = tmp_path / "private-release-name.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "release-qualification",
            "--manifest-json",
            str(missing),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error"] == "manifest_unreadable"
    assert str(tmp_path) not in result.stdout


def test_live_doubao_script_prefers_candidate_checkout_over_pythonpath(
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "shadow"
    shadow_loopx = shadow / "loopx"
    shadow_loopx.mkdir(parents=True)
    (shadow_loopx / "__init__.py").write_text(
        'raise RuntimeError("loaded shadow loopx")\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "qualify-doubao-model-behavior-live.py"),
            "--help",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(shadow)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Run the actual-default behavior portfolio" in result.stdout
    assert "loaded shadow loopx" not in result.stderr


@pytest.mark.parametrize(
    ("extra_args", "ordinary_timeout", "vision_timeout"),
    [([], 90.0, 180.0), (["--timeout-seconds", "12", "--required-vision-timeout-seconds", "34"], 12.0, 34.0)],
)
def test_live_doubao_timeout_extension_is_scoped_to_required_vision(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra_args: list[str],
    ordinary_timeout: float,
    vision_timeout: float,
) -> None:
    script = runpy.run_path(str(REPO_ROOT / "scripts" / "qualify-doubao-model-behavior-live.py"))
    main = script["main"]
    globals_ = main.__globals__
    observed: dict[str, float] = {}
    actor_names = (
        "DoubaoModelBehaviorActor",
        "DoubaoOnboardingModelBehaviorActor",
        "DoubaoSelectedTodoToolBehaviorActor",
        "DoubaoReplanSemanticActionBehaviorActor",
        "DoubaoScopedGateSuccessorToolBehaviorActor",
        "DoubaoCapabilityMonitorRepairToolBehaviorActor",
        "DoubaoTerminalSettlementToolBehaviorActor",
    )
    for name in actor_names:
        def make_actor(actor_name: str) -> SimpleNamespace:
            def from_environment(**kwargs: object) -> object:
                observed[actor_name] = float(kwargs["timeout_seconds"])
                return object()
            return SimpleNamespace(from_environment=from_environment)
        monkeypatch.setitem(globals_, name, make_actor(name))
    monkeypatch.setitem(globals_, "collect_release_source_identity", lambda _root: {"git_dirty": False})
    monkeypatch.setitem(globals_, "build_actual_default_model_behavior_scenario_inputs", lambda _root: ({}, {}))
    monkeypatch.setitem(globals_, "run_actual_default_model_behavior_portfolio", lambda *args, **kwargs: {"qualification_passed": True})
    monkeypatch.setattr(sys, "argv", ["qualify-doubao-model-behavior-live.py", "--qualification-id", "timeout-scope", *extra_args])

    assert main() == 0
    assert json.loads(capsys.readouterr().out)["qualification_passed"] is True
    assert observed["DoubaoReplanSemanticActionBehaviorActor"] == vision_timeout
    assert all(observed[name] == ordinary_timeout for name in actor_names if name != "DoubaoReplanSemanticActionBehaviorActor")


def test_pyproject_pytest_norecursedirs_prunes_dotfiles_and_node_modules() -> None:
    import tomllib

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    norecursedirs = config["tool"]["pytest"]["ini_options"]["norecursedirs"]
    assert ".*" in norecursedirs
    assert "node_modules" in norecursedirs
    assert "packages/loopx-ark-turn" in norecursedirs
    assert "packages/loopx-memt-turn" in norecursedirs


def test_live_doubao_script_defaults_max_workers_to_six_and_allows_serial(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = runpy.run_path(str(REPO_ROOT / "scripts" / "qualify-doubao-model-behavior-live.py"))
    main = script["main"]
    globals_ = main.__globals__
    observed_workers: list[int] = []
    actor_names = (
        "DoubaoModelBehaviorActor",
        "DoubaoOnboardingModelBehaviorActor",
        "DoubaoSelectedTodoToolBehaviorActor",
        "DoubaoReplanSemanticActionBehaviorActor",
        "DoubaoScopedGateSuccessorToolBehaviorActor",
        "DoubaoCapabilityMonitorRepairToolBehaviorActor",
        "DoubaoTerminalSettlementToolBehaviorActor",
    )
    for name in actor_names:
        monkeypatch.setitem(
            globals_,
            name,
            SimpleNamespace(from_environment=lambda **_: SimpleNamespace(qualify=lambda **__: {})),
        )
    monkeypatch.setitem(globals_, "collect_release_source_identity", lambda _root: {"git_dirty": False})
    monkeypatch.setitem(globals_, "build_actual_default_model_behavior_scenario_inputs", lambda _root: ({}, {}))

    def fake_portfolio(*_args: object, **kwargs: object) -> dict[str, object]:
        observed_workers.append(int(kwargs["max_workers"]))
        return {"qualification_passed": True}

    monkeypatch.setitem(globals_, "run_actual_default_model_behavior_portfolio", fake_portfolio)

    monkeypatch.setattr(sys, "argv", ["qualify-doubao-model-behavior-live.py", "--qualification-id", "default-workers"])
    assert main() == 0
    capsys.readouterr()

    monkeypatch.setattr(
        sys,
        "argv",
        ["qualify-doubao-model-behavior-live.py", "--qualification-id", "serial-workers", "--max-workers", "1"],
    )
    assert main() == 0
    capsys.readouterr()
    assert observed_workers == [6, 1]


def test_qualify_release_commit_parallelism_and_preflight_selection(tmp_path: Path) -> None:
    runner = runpy.run_path(str(REPO_ROOT / "scripts" / "qualify_release_commit.py"))
    parallelism = runner["default_pytest_parallelism"]
    assert parallelism(4) == 3
    assert parallelism(12) == 8
    assert parallelism(18) == 12
    assert parallelism(32) == 12
    assert parallelism(1) == 1

    repo = tmp_path / "repo"
    for rel in (
        "tests/capabilities/test_reliability_diagnostics_dsh_provider.py",
        "tests/control_plane/test_public_safe_local_path_owner.py",
        "tests/test_chat_path_redaction.py",
        "tests/test_custom.py",
    ):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_ok(): pass\n", encoding="utf-8")

    selected = runner["select_preflight_tests"](
        [
            "packages/dsh-loopx-plugin/src/observer.ts",
            "tests/test_custom.py",
            "tests/test_missing.py",
        ],
        repo_root=repo,
    )
    assert selected == [
        "tests/capabilities/test_reliability_diagnostics_dsh_provider.py",
        "tests/control_plane/test_public_safe_local_path_owner.py",
        "tests/test_chat_path_redaction.py",
        "tests/test_custom.py",
    ]

    release_repo = tmp_path / "release-repo"
    for rel in (
        "tests/test_dsh_plugin_publish_workflow.py",
        "tests/desktop/test_desktop_signing_workflow.py",
        "tests/desktop/test_update_feed.py",
        "tests/control_plane/test_release_multi_surface_preflight.py",
    ):
        target = release_repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_ok(): pass\n", encoding="utf-8")

    selected_release = runner["select_preflight_tests"](
        [
            ".github/workflows/dsh-plugin-publish.yml",
            ".github/workflows/desktop-release-artifacts.yml",
            "docs/product/release-readiness.md",
        ],
        repo_root=release_repo,
    )
    assert selected_release == [
        "tests/test_dsh_plugin_publish_workflow.py",
        "tests/desktop/test_desktop_signing_workflow.py",
        "tests/desktop/test_update_feed.py",
        "tests/control_plane/test_release_multi_surface_preflight.py",
    ]


def test_qualify_release_commit_resume_requires_exact_source_and_reduces_junit(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "qualify_release_commit", REPO_ROOT / "scripts" / "qualify_release_commit.py"
    )
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    summary_path = tmp_path / "pytest-shard-1-process-summary.json"
    xml_path = tmp_path / "pytest-shard-1.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites>'
        '<testsuite name="pytest" tests="10" errors="0" failures="0" skipped="2" time="1.0"/>'
        "</testsuites>\n",
        encoding="utf-8",
    )
    expected = _source()
    selection_fp = runner.shard_selection_fingerprint(
        shards=12, use_pytest_split=True, durations_path=None, extra_pytest_args=[]
    )
    summary_path.write_text(
        json.dumps(
            {
                "source": expected,
                "current_source": expected,
                "selection_fingerprint": selection_fp,
                "exit_code": 0,
                "groups": 12,
            }
        ),
        encoding="utf-8",
    )
    assert (
        runner.can_reuse_shard_receipt(
            summary_path,
            xml_path,
            expected_source=expected,
            expected_groups=12,
            expected_selection_fingerprint=selection_fp,
        )
        is True
    )
    assert (
        runner.can_reuse_shard_receipt(
            summary_path,
            xml_path,
            expected_source=expected,
            expected_groups=12,
            expected_selection_fingerprint="sha256:" + "0" * 64,
        )
        is False
    )
    assert runner.can_reuse_shard_receipt(summary_path, xml_path, expected_source=expected, expected_groups=6) is False
    assert (
        runner.can_reuse_shard_receipt(
            summary_path, xml_path, expected_source=_source(commit="9" * 40), expected_groups=12
        )
        is False
    )
    assert (
        runner.can_reuse_shard_receipt(
            summary_path, xml_path, expected_source=_source(tree="9" * 40), expected_groups=12
        )
        is False
    )
    assert (
        runner.can_reuse_shard_receipt(
            summary_path, xml_path, expected_source=_source(dirty=True), expected_groups=12
        )
        is False
    )

    xml_two = tmp_path / "pytest-shard-2.xml"
    xml_two.write_text(
        '<testsuite name="pytest" tests="5" errors="0" failures="0" skipped="1" time="0.5"/>\n',
        encoding="utf-8",
    )
    counts = runner.summarize_junit_xml_files([xml_path, xml_two])
    assert counts == {"passed_count": 12, "failed_count": 0, "skipped_count": 3}

    receipt_check = {
        "schema_version": "exact_release_check_receipt_v0",
        "source": expected,
        "status": "passed",
        "result_schema_version": "pytest_summary_v0",
        "result_digest": runner._canonical_sha256(counts),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "summary": counts,
    }
    manifest = _manifest(source=expected)
    manifest["qualifications"]["pytest"] = receipt_check  # type: ignore[index]
    evaluated = build_exact_release_commit_qualification(manifest, observed_source=expected)
    assert evaluated["ready_for_release"] is True
    assert evaluated["qualifications"]["pytest"]["summary"] == counts


def test_qualify_release_commit_runs_end_to_end_on_clean_repo(tmp_path: Path) -> None:
    repo = tmp_path / "clean-repo"
    (repo / "loopx").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(f'[project]\nname = "loopx"\nversion = "{__version__}"\n', encoding="utf-8")
    (repo / "loopx" / "__init__.py").write_text(f'__version__ = "{__version__}"\n', encoding="utf-8")
    (repo / "tests" / "test_sample.py").write_text(
        "import pytest\ndef test_pass_a(): assert 1 + 1 == 2\ndef test_skip_b(): pytest.skip('ok')\n",
        encoding="utf-8",
    )
    for args in (
        ("init",),
        ("config", "user.email", "loopx@example.invalid"),
        ("config", "user.name", "LoopX Test"),
        ("add", "."),
        ("commit", "-m", "init"),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    out_dir = tmp_path / "out"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "qualify_release_commit.py"),
            "--repo-root",
            str(repo),
            "--output-dir",
            str(out_dir),
            "--shards",
            "1",
            "--no-pytest-split",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    receipt = json.loads((out_dir / "receipt-pytest.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "passed"
    assert receipt["summary"] == {"passed_count": 1, "failed_count": 0, "skipped_count": 1}
    assert str(tmp_path) not in json.dumps(receipt)


def test_select_preflight_tests_honors_pyproject_norecursedirs(tmp_path: Path) -> None:
    runner = runpy.run_path(str(REPO_ROOT / "scripts" / "qualify_release_commit.py"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\n'
        'norecursedirs = [".*", "*.egg", ".local", "build", "dist", "deprecate", '
        '"node_modules", "venv", "packages/loopx-ark-turn", "packages/loopx-memt-turn"]\n',
        encoding="utf-8",
    )
    candidates = [
        "packages/loopx-ark-turn/tests/test_host.py",
        "packages/loopx-memt-turn/tests/test_memt.py",
        "packages/loopx-ark-turn-other/tests/test_other.py",
        ".venv/lib/test_hidden.py",
        "tests/.local/test_private.py",
        "tests/pkg.egg/test_egg.py",
        "build/test_build.py",
        "node_modules/pkg/test_node.py",
        "tests/test_core.py",
    ]
    for rel in candidates:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_ok(): pass\n", encoding="utf-8")

    selected = runner["select_preflight_tests"](candidates, repo_root=repo)
    assert selected == [
        "packages/loopx-ark-turn-other/tests/test_other.py",
        "tests/test_core.py",
    ]


def test_qualify_release_commit_clean_durations_and_resume_seed_merge(tmp_path: Path) -> None:
    repo = tmp_path / "clean-repo"
    (repo / "loopx").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "loopx"\nversion = "{__version__}"\n',
        encoding="utf-8",
    )
    (repo / "loopx" / "__init__.py").write_text(
        f'__version__ = "{__version__}"\n',
        encoding="utf-8",
    )
    (repo / "tests" / "test_alpha.py").write_text(
        "def test_alpha(): assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_beta.py").write_text(
        "def test_beta(): assert 2 + 2 == 4\n",
        encoding="utf-8",
    )
    for args in (
        ("init",),
        ("config", "user.email", "loopx@example.invalid"),
        ("config", "user.name", "LoopX Test"),
        ("add", "."),
        ("commit", "-m", "init"),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)

    seed_path = tmp_path / "seed-durations.json"
    seed_path.write_text(
        json.dumps(
            {
                "tests/test_alpha.py::test_alpha": 99.0,
                "tests/test_beta.py::test_beta": 88.0,
                "tests/test_removed.py::test_removed": 77.0,
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    merged_path = tmp_path / "merged-durations.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "qualify_release_commit.py"),
            "--repo-root",
            str(repo),
            "--output-dir",
            str(out_dir),
            "--shards",
            "2",
            "--durations-path",
            str(seed_path),
            "--durations-out",
            str(merged_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    merged = json.loads(merged_path.read_text(encoding="utf-8"))
    assert set(merged) == {
        "tests/test_alpha.py::test_alpha",
        "tests/test_beta.py::test_beta",
    }
    assert 0.0 <= merged["tests/test_alpha.py::test_alpha"] < 10.0
    assert 0.0 <= merged["tests/test_beta.py::test_beta"] < 10.0
    assert not (out_dir / ".shard-1-durations.json").exists()
    assert not (out_dir / ".shard-2-durations.json").exists()

    # Now simulate resuming shard 1 while re-running shard 2 fresh: shard 1 keeps seed,
    # shard 2 writes fresh timing without overwriting shard 1's timing back to seed.
    shard1_xml = (out_dir / "pytest-shard-1.xml").read_text(encoding="utf-8")
    shard1_key = (
        "tests/test_alpha.py::test_alpha"
        if "test_alpha" in shard1_xml
        else "tests/test_beta.py::test_beta"
    )
    shard2_key = (
        "tests/test_beta.py::test_beta"
        if shard1_key == "tests/test_alpha.py::test_alpha"
        else "tests/test_alpha.py::test_alpha"
    )
    (out_dir / "pytest-shard-2-process-summary.json").unlink()
    (out_dir / "pytest-shard-2.xml").unlink()
    resumed_out = tmp_path / "resumed-durations.json"
    resumed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "qualify_release_commit.py"),
            "--repo-root",
            str(repo),
            "--output-dir",
            str(out_dir),
            "--shards",
            "2",
            "--resume",
            "--durations-path",
            str(seed_path),
            "--durations-out",
            str(resumed_out),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    resumed_merged = json.loads(resumed_out.read_text(encoding="utf-8"))
    expected_shard1_seed = 99.0 if shard1_key == "tests/test_alpha.py::test_alpha" else 88.0
    assert resumed_merged[shard1_key] == expected_shard1_seed
    assert 0.0 <= resumed_merged[shard2_key] < 10.0


def test_remove_tree_tolerant_reclaims_readonly_and_skips_links(tmp_path: Path) -> None:
    runner = runpy.run_path(str(REPO_ROOT / "scripts" / "qualify_release_commit.py"))
    remove_tree = runner["_remove_tree_tolerant"]

    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    outside_file = outside_dir / "keep.txt"
    outside_file.write_text("keep\n", encoding="utf-8")
    hard_source = tmp_path / "hard-source.txt"
    hard_source.write_text("hard\n", encoding="utf-8")

    tree = tmp_path / "tree"
    readonly_sub = tree / "ro-sub"
    readonly_sub.mkdir(parents=True)
    readonly_file = readonly_sub / "ro.txt"
    readonly_file.write_text("ro\n", encoding="utf-8")
    (tree / "dir-link").symlink_to(outside_dir)
    (tree / "file-link").symlink_to(outside_file)
    os.link(hard_source, tree / "hard-link.txt")

    os.chmod(outside_file, 0o444)
    os.chmod(outside_dir, 0o555)
    os.chmod(hard_source, 0o444)
    os.chmod(readonly_file, 0o444)
    os.chmod(readonly_sub, 0o555)
    try:
        remove_tree(tree)
        assert not tree.exists()
        assert (outside_dir.stat().st_mode & 0o777) == 0o555
        assert (outside_file.stat().st_mode & 0o777) == 0o444
        assert (hard_source.stat().st_mode & 0o777) == 0o444

        top_link = tmp_path / "top-link"
        top_link.symlink_to(outside_dir)
        remove_tree(top_link)
        assert not top_link.is_symlink()
        assert outside_dir.exists()
    finally:
        os.chmod(outside_dir, 0o755)
        os.chmod(outside_file, 0o644)
        os.chmod(hard_source, 0o644)


def test_only_preflight_requires_preflight_since_and_isolated_node_compile_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = runpy.run_path(str(REPO_ROOT / "scripts" / "qualify_release_commit.py"))
    main = runner["main"]
    isolated_env = runner["_isolated_env"]

    with pytest.raises(SystemExit) as exc:
        main(["--output-dir", str(tmp_path / "out"), "--only-preflight"])
    assert exc.value.code == 2
    assert "--only-preflight requires --preflight-since" in capsys.readouterr().err

    monkeypatch.delenv("NODE_DISABLE_COMPILE_CACHE", raising=False)
    monkeypatch.delenv("NODE_V8_COVERAGE", raising=False)
    monkeypatch.delenv("NODE_COMPILE_CACHE", raising=False)
    enabled_root = tmp_path / "env-enabled"
    enabled_env = isolated_env(enabled_root, python_bin=Path(sys.executable), extra_bin_dirs=[])
    cache_dir = enabled_root / "node-compile-cache"
    assert enabled_env["NODE_COMPILE_CACHE"] == str(cache_dir)
    assert cache_dir.is_dir()
    if hasattr(os, "getuid"):
        assert (cache_dir.stat().st_mode & 0o777) == 0o700

    monkeypatch.setenv("NODE_DISABLE_COMPILE_CACHE", "1")
    disabled_root = tmp_path / "env-disabled"
    disabled_env = isolated_env(disabled_root, python_bin=Path(sys.executable), extra_bin_dirs=[])
    assert "NODE_COMPILE_CACHE" not in disabled_env
    assert not (disabled_root / "node-compile-cache").exists()

    monkeypatch.delenv("NODE_DISABLE_COMPILE_CACHE", raising=False)
    monkeypatch.setenv("NODE_V8_COVERAGE", str(tmp_path / "cov"))
    cov_root = tmp_path / "env-cov"
    cov_env = isolated_env(cov_root, python_bin=Path(sys.executable), extra_bin_dirs=[])
    assert "NODE_COMPILE_CACHE" not in cov_env
    assert not (cov_root / "node-compile-cache").exists()
