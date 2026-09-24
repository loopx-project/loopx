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


def test_release_qualification_cli_matches_clean_git_source_and_is_read_only(
    tmp_path: Path,
) -> None:
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
