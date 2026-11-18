from __future__ import annotations

import shlex
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import loopx.canary.planner as canary_planner
import loopx.cli_commands.canary as canary_command
from loopx.canary.planner import CURRENT_REPO_PROFILES
from loopx.canary.quality_surface_catalog import (
    QUALITY_SURFACE_CATALOG,
    build_quality_surface_catalog_audit,
)


def test_canary_python_script_checks_exist() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    profiles = canary_planner.build_catalog_canary_profiles()
    assert profiles["ok"] is True
    checks = [check for profile in profiles["profiles"] for check in profile["candidate_checks"]]
    checks.extend(check for profile in profiles["domain_profiles"] for check in profile["checks"])
    missing = []
    for check in checks:
        args = shlex.split(check["command"])
        if len(args) > 1 and args[0] in {"python", "python3"} and args[1].endswith(".py"):
            if not (repo_root / args[1]).is_file():
                missing.append(check["command"])
    assert not missing, f"Canary scripts missing: {missing}"


def test_current_high_risk_surfaces_have_no_catalog_drift() -> None:
    audit = build_quality_surface_catalog_audit(
        CURRENT_REPO_PROFILES,
        repo_root=Path(__file__).resolve().parents[2],
    )

    assert audit["ok"] is True
    assert audit["drift_count"] == 0
    assert audit["repository_reference_validation"] == "performed"
    assert audit["classified_surface_count"] == audit["high_risk_profile_count"]
    assert audit["gaps"] == []


def test_packaged_audit_keeps_classification_without_source_checkout() -> None:
    audit = build_quality_surface_catalog_audit(CURRENT_REPO_PROFILES)

    assert audit["ok"] is True
    assert audit["repository_reference_validation"] == "source_checkout_unavailable"


def test_planner_quality_audit_prefers_explicit_source_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    packaged_root = tmp_path / "release"
    (packaged_root / "loopx" / "canary").mkdir(parents=True)
    (packaged_root / "pyproject.toml").write_text("", encoding="utf-8")
    (packaged_root / "loopx" / "canary" / "quality_surface_catalog.py").write_text(
        "",
        encoding="utf-8",
    )
    monkeypatch.setattr(canary_planner, "REPO_ROOT", packaged_root)

    packaged_audit = canary_planner.build_quality_surface_catalog_audit()
    checkout_audit = canary_planner.build_quality_surface_catalog_audit(
        repo_root=Path(__file__).resolve().parents[2]
    )

    assert packaged_audit["ok"] is True
    assert (
        packaged_audit["repository_reference_validation"]
        == "source_checkout_unavailable"
    )
    assert checkout_audit["ok"] is True
    assert checkout_audit["drift_count"] == 0
    assert checkout_audit["repository_reference_validation"] == "performed"


def test_quality_audit_cli_passes_invoking_checkout_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, Path] = {}

    def build_audit(*, repo_root: Path) -> dict[str, bool]:
        captured["repo_root"] = repo_root
        return {"ok": True}

    monkeypatch.setattr(
        canary_command,
        "_resolve_git_repo_root",
        lambda candidate: tmp_path,
    )
    monkeypatch.setattr(
        canary_command,
        "build_quality_surface_catalog_audit",
        build_audit,
    )

    result = canary_command.handle_canary_command(
        SimpleNamespace(command="canary", canary_command="quality-audit"),
        output_format=lambda args: "json",
        print_payload=lambda payload, output_format, renderer: None,
    )

    assert result == 0
    assert captured["repo_root"] == tmp_path


def test_unclassified_high_risk_profile_is_drift() -> None:
    profiles = [*CURRENT_REPO_PROFILES, {"id": "new-risk", "quality_risk": "high"}]

    audit = build_quality_surface_catalog_audit(profiles)

    assert audit["ok"] is False
    assert {
        (item["code"], item.get("canary_profile_id")) for item in audit["drift"]
    } >= {("unclassified_high_risk_profile", "new-risk")}


def test_oracle_cannot_reuse_product_source_as_expected_truth() -> None:
    catalog = deepcopy(QUALITY_SURFACE_CATALOG)
    catalog[0]["semantic_oracle"]["refs"] = [catalog[0]["owner_paths"][0]]

    audit = build_quality_surface_catalog_audit(
        CURRENT_REPO_PROFILES,
        catalog=catalog,
    )

    assert audit["ok"] is False
    assert any(
        item["code"] == "circular_oracle_uses_product_source"
        for item in audit["drift"]
    )


def test_not_applicable_layer_requires_a_reason_but_is_not_a_gap() -> None:
    catalog = deepcopy(QUALITY_SURFACE_CATALOG)
    catalog[0]["layers"]["model_behavior"] = {"status": "not_applicable"}

    invalid = build_quality_surface_catalog_audit(
        CURRENT_REPO_PROFILES,
        catalog=catalog,
    )
    assert any(
        item["code"] == "not_applicable_without_rationale"
        for item in invalid["drift"]
    )

    catalog[0]["layers"]["model_behavior"]["rationale"] = (
        "Scheduler precedence is deterministic."
    )
    valid = build_quality_surface_catalog_audit(
        CURRENT_REPO_PROFILES,
        catalog=catalog,
    )
    assert valid["ok"] is True
    assert not any(
        gap["surface_id"] == "interaction-scheduler-authority"
        for gap in valid["gaps"]
    )


def test_same_evidence_cannot_stand_in_for_multiple_layers() -> None:
    catalog = deepcopy(QUALITY_SURFACE_CATALOG)
    shared_ref = catalog[0]["layers"]["durable_smoke"]["refs"][0]
    catalog[0]["layers"]["release_gate"] = {
        "status": "covered",
        "refs": [shared_ref],
    }

    audit = build_quality_surface_catalog_audit(
        CURRENT_REPO_PROFILES,
        catalog=catalog,
    )

    assert any(
        item["code"] == "duplicate_evidence_across_layers"
        for item in audit["drift"]
    )
