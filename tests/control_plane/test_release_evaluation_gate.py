from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from loopx import __version__
from loopx.cli_commands.canary_release_qualification import (
    build_canary_release_qualification_payload,
    register_canary_release_qualification_command,
)
from loopx.control_plane.testing.actual_default_model_behavior_portfolio import (
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_CONTRAST_COUNT,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_REPEAT_ATTEMPTS,
    ACTUAL_DEFAULT_MODEL_BEHAVIOR_SCENARIO_COUNT,
)
from loopx.control_plane.testing.release_commit_qualification import (
    EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION,
    REQUIRED_QUALIFICATION_IDS,
    build_exact_release_commit_qualification,
)
from loopx.control_plane.testing.release_evaluation_gate import (
    classify_semver_transition,
    validate_evaluation_document,
    validate_evaluation_ledger_tree,
    validate_release_evaluation_gate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "docs" / "product" / "releases" / "evaluations"
DIGEST = "sha256:" + "4" * 64


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_ledger_subset(tmp_path: Path) -> Path:
    dest = tmp_path / "evaluations"
    hist = dest / "history"
    hist.mkdir(parents=True)
    (dest / "horizon.json").write_text(
        (EVAL_DIR / "horizon.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (dest / "current.json").write_text(
        (EVAL_DIR / "current.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for name in ("v2.3.0.json", "v2.4.0.json"):
        (hist / name).write_text((EVAL_DIR / "history" / name).read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def _recompute_scales(payload: dict[str, object], horizon: dict[str, object], *, candidate_major: int) -> None:
    weights = {d["id"]: float(d["weight"]) for d in horizon["domains"]}
    meta: dict[str, tuple[str, int]] = {}
    domain_lines: dict[str, list[str]] = {}
    for d in horizon["domains"]:
        did = str(d["id"])
        domain_lines[did] = []
        for b in d["battlelines"]:
            bid = str(b["id"])
            meta[bid] = (did, int(b["target_generation_major"]))
            domain_lines[did].append(bid)

    scores: dict[str, float] = {}
    for line in payload["battlelines"]:
        dims = line["dimensions"]
        score = round(sum(float(dims[k]) for k in ("A", "B", "C", "D")), 1)
        line["score"] = score
        scores[str(line["id"])] = score

    a_domains: dict[str, float] = {}
    b_domains: dict[str, float] = {}
    b_count = 0
    for did, ids in domain_lines.items():
        a_domains[did] = round(sum(scores[i] for i in ids) / len(ids), 1)
        core_ids = [i for i in ids if meta[i][1] <= candidate_major]
        b_count += len(core_ids)
        b_domains[did] = round(sum(scores[i] for i in core_ids) / len(core_ids), 1)

    a_total = round(sum(a_domains[did] * weights[did] for did in weights), 1)
    b_total = round(sum(b_domains[did] * weights[did] for did in weights), 1)
    payload["a_scale"]["domain_scores"] = a_domains
    payload["a_scale"]["score"] = a_total
    payload["b_scale"] = {
        "generation_major": candidate_major,
        "battleline_count": b_count,
        "score": b_total,
        "delta_from_prior": None,
        "domain_scores": b_domains,
    }


def _valid_manifest_for_source(source: dict[str, object]) -> dict[str, object]:
    summaries: dict[str, dict[str, object]] = {
        "pytest": {"passed_count": 100, "failed_count": 0, "skipped_count": 0},
        "ruff": {"violation_count": 0},
        "mypy": {"error_count": 0},
        "risk_canary": {"selected_check_count": 5, "failure_count": 0, "manual_hold_count": 0},
        "full_public": {"ready": True, "shard_count": 2, "failure_count": 0, "timeout_count": 0},
        "install_upgrade_host": {
            "install_passed": True,
            "upgrade_passed": True,
            "host_passed": True,
            "packaged_frontend_passed": True,
        },
        "public_boundary": {"scanned_path_count": 4, "violation_count": 0},
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
    checks = {
        qid: {
            "schema_version": "exact_release_check_receipt_v0",
            "status": "passed",
            "result_schema_version": EXPECTED_RESULT_SCHEMA_BY_QUALIFICATION[qid],
            "completed_at": "2026-11-22T06:00:00+00:00",
            "source": dict(source),
            "result_digest": DIGEST,
            "summary": summaries[qid],
        }
        for qid in REQUIRED_QUALIFICATION_IDS
    }
    return {
        "schema_version": "exact_release_commit_qualification_manifest_v0",
        "candidate": dict(source),
        "claims": {},
        "qualifications": checks,
    }


def test_repository_evaluation_ledger_closes_with_verified_git_tags() -> None:
    receipt = validate_evaluation_ledger_tree(
        EVAL_DIR,
        repo_root=REPO_ROOT,
        verify_git_tags=True,
    )
    assert receipt["ready"] is True
    assert receipt["history_count"] == 15
    assert receipt["issues_by_file"] == {}
    assert receipt["checkout_version"] == __version__

    v240 = _load_json(EVAL_DIR / "history" / "v2.4.0.json")
    gate = validate_release_evaluation_gate(
        evaluations_dir=EVAL_DIR,
        candidate_source=v240["source"],
        prior_stable_version="2.3.0",
        repo_root=REPO_ROOT,
        verify_git_tags=True,
    )
    assert gate["ready"] is True
    assert gate["transition_kind"] == "minor"
    assert gate["a_scale_score"] == 54.6
    assert gate["b_scale_score"] == 63.8

    horizon = _load_json(EVAL_DIR / "horizon.json")
    v013 = _load_json(EVAL_DIR / "history" / "v0.1.3.json")
    assert v013["evaluation_mode"] == "retrospective_git_audit"
    assert v013["generation_status"] == "eol"
    assert (
        validate_evaluation_document(
            v013,
            horizon=horizon,
            expected_version="0.1.3",
            repo_root=REPO_ROOT,
            verify_git_tags=True,
            enforce_clean_gate=False,
        )
        == []
    )


def test_patch_transition_inherits_parent_evaluation_without_rescoring(tmp_path: Path) -> None:
    assert classify_semver_transition("2.4.1", "2.4.0") == "patch"
    assert classify_semver_transition("2.4.0", "2.4.0") == "same"

    dest = _copy_ledger_subset(tmp_path)
    (dest / "history" / "v0.1.3.json").write_text(
        (EVAL_DIR / "history" / "v0.1.3.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    hotfix_source = {
        "git_commit": "a" * 40,
        "git_tree": "b" * 40,
        "git_dirty": False,
        "package_version": "2.4.1",
        "version_tag": "v2.4.1",
    }
    patch_gate = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=hotfix_source,
        prior_stable_version="2.4.0",
        verify_git_tags=False,
    )
    assert patch_gate["ready"] is True
    assert patch_gate["transition_kind"] == "patch"
    assert patch_gate["inherited_from"] == "v2.4.0"
    assert patch_gate["a_scale_score"] == 54.6

    legacy_patch = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source={**hotfix_source, "package_version": "0.1.5", "version_tag": "v0.1.5"},
        prior_stable_version="0.1.3",
        verify_git_tags=False,
    )
    assert legacy_patch["ready"] is True
    assert legacy_patch["inherited_from"] == "v0.1.3"

    missing_parent = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source={**hotfix_source, "package_version": "2.9.1", "version_tag": "v2.9.1"},
        prior_stable_version="2.9.0",
        verify_git_tags=False,
    )
    assert missing_parent["ready"] is False
    assert missing_parent["issues"] == ["missing_parent_evaluation:v2.9.0"]


def test_minor_transition_enforces_arithmetic_caps_and_git_identity(tmp_path: Path) -> None:
    dest = _copy_ledger_subset(tmp_path)
    horizon = _load_json(dest / "horizon.json")
    v240 = _load_json(dest / "history" / "v2.4.0.json")

    candidate_source = {
        "git_commit": "c" * 40,
        "git_tree": "d" * 40,
        "git_dirty": False,
        "package_version": "2.5.0",
        "version_tag": "v2.5.0",
    }

    missing = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=candidate_source,
        prior_stable_version="2.4.0",
    )
    assert missing["ready"] is False
    assert missing["issues"] == ["missing_release_evaluation:v2.5.0"]

    v250 = copy.deepcopy(v240)
    v250["generation_status"] = "candidate"
    v250["semver"] = {"major": 2, "minor": 5, "patch": 0}
    v250["source"] = dict(candidate_source)
    v250["battlelines"][0]["dimensions"]["A"] = 22
    _recompute_scales(v250, horizon, candidate_major=2)
    v250["a_scale"]["delta_from_prior"] = round(
        float(v250["a_scale"]["score"]) - float(v240["a_scale"]["score"]), 1
    )
    eval_path = dest / "history" / "v2.5.0.json"
    eval_path.write_text(json.dumps(v250, indent=2), encoding="utf-8")

    passed = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=candidate_source,
        prior_stable_version="2.4.0",
    )
    assert passed["ready"] is True
    assert passed["evaluation_path"] == "v2.5.0.json"

    # Negative 1: retrospective mode rejected under clean gate
    bad_mode = copy.deepcopy(v250)
    bad_mode["evaluation_mode"] = "retrospective_git_audit"
    assert "release_gate_requires_qualified_mode" in validate_evaluation_document(
        bad_mode,
        horizon=horizon,
        expected_version="2.5.0",
        candidate_source=candidate_source,
        prior_evaluation=v240,
    )

    # Negative 2: C-cap without paired public evidence + benchmark uplift claim inconsistency
    bad_cap_c = copy.deepcopy(v250)
    for row in bad_cap_c["battlelines"]:
        if row["id"] == "7.2":
            row["dimensions"]["C"] = 18
    _recompute_scales(bad_cap_c, horizon, candidate_major=2)
    bad_cap_c["a_scale"]["delta_from_prior"] = round(
        float(bad_cap_c["a_scale"]["score"]) - float(v240["a_scale"]["score"]), 1
    )
    issues_c = validate_evaluation_document(
        bad_cap_c,
        horizon=horizon,
        expected_version="2.5.0",
        candidate_source=candidate_source,
        prior_evaluation=v240,
    )
    assert "c_cap_exceeded_without_paired_evidence:7.2" in issues_c
    assert "benchmark_uplift_claim_inconsistent:7.2" in issues_c

    # Negative 3: D-cap when default_off
    bad_cap_d = copy.deepcopy(v250)
    for row in bad_cap_d["battlelines"]:
        if row["id"] == "2.3":
            row["dimensions"]["D"] = 15
    _recompute_scales(bad_cap_d, horizon, candidate_major=2)
    bad_cap_d["a_scale"]["delta_from_prior"] = round(
        float(bad_cap_d["a_scale"]["score"]) - float(v240["a_scale"]["score"]), 1
    )
    issues_d = validate_evaluation_document(
        bad_cap_d,
        horizon=horizon,
        expected_version="2.5.0",
        candidate_source=candidate_source,
        prior_evaluation=v240,
    )
    assert "d_cap_exceeded_when_default_off:2.3" in issues_d

    # Negative 4: arithmetic & delta mismatches
    bad_math = copy.deepcopy(v250)
    bad_math["a_scale"]["score"] = 99.9
    bad_math["a_scale"]["delta_from_prior"] = 99.0
    bad_math["battlelines"][0]["score"] = 1.0
    bad_math["b_scale"]["battleline_count"] = 29
    issues_math = validate_evaluation_document(
        bad_math,
        horizon=horizon,
        expected_version="2.5.0",
        candidate_source=candidate_source,
        prior_evaluation=v240,
    )
    assert any(i.startswith("a_scale_weighted_total_mismatch:") for i in issues_math)
    assert any(i.startswith("delta_from_prior_mismatch:") for i in issues_math)
    assert "battleline_sum_mismatch:1.1" in issues_math
    assert "domain_a_average_mismatch:D1" in issues_math
    assert "b_scale_battleline_count_mismatch" in issues_math

    # Negative 5: real git tag mismatch detection
    repo = tmp_path / "git-repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "tag", "v2.5.0"], check=True)
    tag_issues = validate_evaluation_document(
        v250,
        horizon=horizon,
        expected_version="2.5.0",
        candidate_source=candidate_source,
        prior_evaluation=v240,
        repo_root=repo,
        verify_git_tags=True,
    )
    assert "git_tag_commit_mismatch:v2.5.0" in tag_issues
    assert "git_tag_tree_mismatch:v2.5.0" in tag_issues

    # Negative 6: clean gate rejects domain_aggregate scope and empty battlelines
    bad_scope = copy.deepcopy(v250)
    bad_scope["battleline_breakdown_scope"] = "domain_aggregate"
    bad_scope["battlelines"] = []
    bad_scope["b_scale"] = {"generation_major": 2, "score": None}
    eval_path.write_text(json.dumps(bad_scope, indent=2), encoding="utf-8")
    scope_gate = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=candidate_source,
        prior_stable_version="2.4.0",
    )
    assert scope_gate["ready"] is False
    assert "clean_gate_requires_full_41_battlelines" in scope_gate["issues"]

    # Negative 7: patch prior_stable_version resolves parent a.b.0 for delta check & rejects missing prior
    bad_delta_v250 = copy.deepcopy(v250)
    bad_delta_v250["a_scale"]["delta_from_prior"] = 999.0
    eval_path.write_text(json.dumps(bad_delta_v250, indent=2), encoding="utf-8")
    patch_prior_gate = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=candidate_source,
        prior_stable_version="2.4.1",
    )
    assert patch_prior_gate["ready"] is False
    assert any(
        issue.startswith("delta_from_prior_mismatch:")
        for issue in patch_prior_gate["issues"]
    )

    eval_path.write_text(json.dumps(v250, indent=2), encoding="utf-8")
    # Patch prior version (2.4.99) falls back cleanly to v2.4.0.json; missing series prior fails closed:
    missing_minor_prior = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=candidate_source,
        prior_stable_version="2.4.99",
    )
    assert missing_minor_prior["ready"] is True
    missing_series_prior = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=candidate_source,
        prior_stable_version="2.2.0",
    )
    assert missing_series_prior["ready"] is False
    assert missing_series_prior["issues"] == ["missing_prior_stable_evaluation:v2.2.0"]


def test_major_promotion_gate_and_version_regression_rejection(tmp_path: Path) -> None:
    dest = _copy_ledger_subset(tmp_path)
    horizon = _load_json(dest / "horizon.json")
    v240 = _load_json(dest / "history" / "v2.4.0.json")

    with pytest.raises(ValueError, match="regresses behind prior stable"):
        classify_semver_transition("2.3.0", "2.4.0")

    v300_src = {
        "git_commit": "e" * 40,
        "git_tree": "f" * 40,
        "git_dirty": False,
        "package_version": "3.0.0",
        "version_tag": "v3.0.0",
    }
    v300 = copy.deepcopy(v240)
    v300["generation_status"] = "candidate"
    v300["semver"] = {"major": 3, "minor": 0, "patch": 0}
    v300["source"] = dict(v300_src)
    target_gen3 = next(g for g in horizon["generations"] if g["major"] == 3)
    v300["battlelines"] = copy.deepcopy(target_gen3["target_battlelines"])
    v300["qualification_evidence"]["claims_net_benchmark_uplift"] = True
    _recompute_scales(v300, horizon, candidate_major=3)
    v300["a_scale"]["delta_from_prior"] = round(
        float(v300["a_scale"]["score"]) - float(v240["a_scale"]["score"]), 1
    )

    gate_ids = [g["id"] for g in horizon["generations"][2]["exit_gates"]]
    v300["major_promotion_gate"] = {
        "from_major": 2,
        "to_major": 3,
        "breaking_migration_contract": {"verified": True, "doc": "docs/product/release-readiness.md"},
        "exit_gates": [
            {"id": gid, "met": True, "evidence_ref": f"proof:{gid}"}
            for gid in gate_ids
        ],
    }
    v300_path = dest / "history" / "v3.0.0.json"
    v300_path.write_text(json.dumps(v300, indent=2), encoding="utf-8")

    ok = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=v300_src,
        prior_stable_version="2.4.0",
    )
    assert ok["ready"] is True
    assert ok["transition_kind"] == "major"
    assert ok["a_scale_score"] == 76.1

    bad_major = copy.deepcopy(v300)
    bad_major["major_promotion_gate"]["breaking_migration_contract"]["verified"] = False
    bad_major["major_promotion_gate"]["exit_gates"][1]["met"] = False
    bad_major["major_promotion_gate"]["exit_gates"][0]["evidence_ref"] = ""
    v300_path.write_text(json.dumps(bad_major, indent=2), encoding="utf-8")
    failed = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=v300_src,
        prior_stable_version="2.4.0",
    )
    assert failed["ready"] is False
    assert "breaking_migration_contract_unverified" in failed["issues"]
    assert f"major_promotion_gate_unmet:{gate_ids[1]}" in failed["issues"]
    assert f"major_promotion_gate_missing_evidence:{gate_ids[0]}" in failed["issues"]

    # Negative 8: major transition enforces exit gates even when prior_stable_version is None
    no_prior_major = copy.deepcopy(v300)
    no_prior_major.pop("major_promotion_gate")
    v300_path.write_text(json.dumps(no_prior_major, indent=2), encoding="utf-8")
    unprotected_major = validate_release_evaluation_gate(
        evaluations_dir=dest,
        candidate_source=v300_src,
        prior_stable_version=None,
    )
    assert unprotected_major["ready"] is False
    assert unprotected_major["transition_kind"] == "major"
    assert "missing_major_promotion_gate" in unprotected_major["issues"]


def test_horizon_validation_rejects_bad_weights_or_battleline_count(tmp_path: Path) -> None:
    horizon = _load_json(EVAL_DIR / "horizon.json")
    v240 = _load_json(EVAL_DIR / "history" / "v2.4.0.json")

    bad_weights = copy.deepcopy(horizon)
    bad_weights["domains"][0]["weight"] = 0.99
    with pytest.raises(ValueError, match="weights must sum to 1.0"):
        validate_evaluation_document(v240, horizon=bad_weights, expected_version="2.4.0")

    bad_count = copy.deepcopy(horizon)
    bad_count["domains"][0]["battlelines"].pop()
    with pytest.raises(ValueError, match="must define 41 battlelines"):
        validate_evaluation_document(v240, horizon=bad_count, expected_version="2.4.0")


def test_release_commit_qualification_and_cli_integration_with_evaluations_dir(
    tmp_path: Path,
) -> None:
    import argparse

    dest = _copy_ledger_subset(tmp_path)
    repo = tmp_path / "pkg-repo"
    (repo / "loopx").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "loopx"\nversion = "2.4.1"\n', encoding="utf-8")
    (repo / "loopx" / "__init__.py").write_text('__version__ = "2.4.1"\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml", "loopx/__init__.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "patch 2.4.1"], check=True, capture_output=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], text=True).strip()

    source = {
        "git_commit": commit,
        "git_tree": tree,
        "git_dirty": False,
        "package_version": "2.4.1",
        "version_tag": "v2.4.1",
    }
    manifest = _valid_manifest_for_source(source)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Programmatic integration
    receipt = build_exact_release_commit_qualification(
        manifest,
        observed_source=source,
        evaluations_dir=dest,
        prior_stable_version="2.4.0",
        repo_root=repo,
    )
    assert receipt["ready_for_release"] is True
    assert receipt["decision"] == "ready_for_owner_release"
    assert receipt["evaluation_gate"]["inherited_from"] == "v2.4.0"

    # CLI argparse + handler integration
    root_parser = argparse.ArgumentParser(prog="loopx")
    canary_sub = root_parser.add_subparsers(dest="subcommand")
    register_canary_release_qualification_command(
        canary_sub, lambda p: p.add_argument("--format", default="json")
    )
    args = root_parser.parse_args(
        [
            "release-qualification",
            "--manifest-json",
            str(manifest_path),
            "--repo-root",
            str(repo),
            "--evaluations-dir",
            str(dest),
            "--prior-stable-version",
            "2.4.0",
        ]
    )
    cli_payload = build_canary_release_qualification_payload(args)
    assert cli_payload["ok"] is True
    assert cli_payload["ready_for_release"] is True
    assert cli_payload["evaluation_gate"]["a_scale_score"] == 54.6

    # Failing evaluation gate blocks release readiness
    broken_dest = tmp_path / "broken-eval"
    broken_dest.mkdir()
    (broken_dest / "horizon.json").write_text((dest / "horizon.json").read_text(encoding="utf-8"), encoding="utf-8")
    (broken_dest / "history").mkdir()
    fail_receipt = build_exact_release_commit_qualification(
        manifest,
        observed_source=source,
        evaluations_dir=broken_dest,
        prior_stable_version="2.4.0",
    )
    assert fail_receipt["ready_for_release"] is False
    assert fail_receipt["decision"] == "hold_evaluation_gate_failed"
    assert fail_receipt["evaluation_gate"]["ready"] is False
    assert fail_receipt["evaluation_gate"]["issues"] == ["missing_parent_evaluation:v2.4.0"]
