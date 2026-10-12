from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from loopx.agent_onboarding import _skill_delivery_contract
from loopx.canary.premerge import (
    apply_change_quality_verification,
    build_premerge_validation_gate,
    render_premerge_validation_gate_markdown,
)
from loopx.capabilities.change_quality.policy import change_quality_goal_policy
from loopx.capabilities.change_quality.receipt import (
    CHANGE_QUALITY_RESULT_SCHEMA_VERSION,
    build_change_quality_prepare_packet,
    record_change_quality_receipt,
    verify_change_quality_receipt,
)
from loopx.capabilities.change_quality.result import (
    SIMPLIFY_GUARDRAIL_LENS_IDS,
    derive_change_quality_guardrails,
    normalize_change_quality_result,
)
from loopx.cli import main
from loopx.configure_goal import configure_goal
from loopx.capabilities.project_skill_delivery import (
    install_project_skill,
    inspect_project_skill,
)
from loopx.skill_install_readback import ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS


GOAL_ID = "change-quality-fixture"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "change-quality@example.invalid")
    _git(repo, "config", "user.name", "Change Quality Fixture")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "fixture")
    runtime_root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(repo),
                        "control_plane": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return repo, registry, runtime_root


def _enable(
    registry: Path,
    *,
    safe_fix: bool = True,
    strict_receipt: bool = True,
) -> None:
    configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        change_quality_enabled=True,
        change_quality_safe_fix=safe_fix,
        change_quality_strict_receipt=strict_receipt,
        execute=True,
    )


def _result(path: Path, fingerprint: str, **overrides: object) -> Path:
    payload = {
        "schema_version": CHANGE_QUALITY_RESULT_SCHEMA_VERSION,
        "scope_fingerprint": fingerprint,
        "reviewed_final_scope": True,
        "reuse": {
            "outcome": "retained",
            "summary": "The exact fixture already uses its direct owner.",
            "evidence_refs": ["path:app.py"],
        },
        "simplification": {
            "outcome": "retained",
            "summary": "The direct assignment is already cohesive.",
            "evidence_refs": ["path:app.py"],
            "safe_fix_applied": False,
        },
        "risks": [],
        "validation": [
            {
                "validator": "fixture",
                "status": "passed",
                "scope": "focused change-quality contract",
                "required": True,
            }
        ],
        **overrides,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_policy_defaults_off_and_configures_independent_controls(
    tmp_path: Path,
) -> None:
    _repo, registry, _runtime = _fixture(tmp_path)
    stored = json.loads(registry.read_text(encoding="utf-8"))["goals"][0]
    assert change_quality_goal_policy(stored) == {
        "schema_version": "change_quality_qualification_policy_v0",
        "enabled": False,
        "safe_fix": False,
        "strict_receipt": False,
    }

    preview = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        change_quality_enabled=True,
        change_quality_strict_receipt=True,
        execute=False,
    )

    assert preview["after"]["change_quality_qualification"] == {
        "enabled": True,
        "safe_fix": False,
        "strict_receipt": True,
    }


def test_prepare_projects_repository_context_and_required_review_lenses(
    tmp_path: Path,
) -> None:
    repo, registry, _runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        (
            "[project]\n"
            "name='fixture'\n"
            "[tool.poe.tasks]\n"
            "format='ruff format --check .'\n"
            "lint='ruff check .'\n"
            "typecheck='mypy src'\n"
            "test='pytest -q'\n"
        ),
        encoding="utf-8",
    )
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text("* @fixture\n", encoding="utf-8")
    source = repo / "src"
    source.mkdir()
    (source / "AGENTS.md").write_text("# Source rules\n", encoding="utf-8")
    (source / "app.py").write_text("value = 2\n", encoding="utf-8")

    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )

    context = prepared["repository_context"]
    assert context["content_included"] is False
    assert context["instruction_refs"] == ["AGENTS.md", "src/AGENTS.md"]
    assert context["ownership_refs"] == [".github/CODEOWNERS"]
    assert context["build_manifest_refs"] == ["pyproject.toml"]
    assert context["language_hints"] == ["python"]
    assert context["validation_plan"]["required_reads"] == [
        "AGENTS.md",
        "src/AGENTS.md",
    ]
    assert {item["category"] for item in context["validation_plan"]["candidates"]} == {
        "format",
        "lint",
        "typecheck",
        "test",
    }
    assert {item["runner"] for item in context["validation_plan"]["candidates"]} == {
        "poe_task"
    }
    assert context["validation_plan"]["unresolved_categories"] == []
    assert context["validation_plan"]["auto_execute"] is False
    assert context["changed_surface_roots"] == [
        ".github",
        "AGENTS.md",
        "pyproject.toml",
        "src",
    ]
    contract = prepared["agent_contract"]
    assert contract["primary_result_fields"] == ["reuse", "simplification"]
    assert contract["sparse_result_fields"] == ["risks", "validation"]
    assert contract["guardrail_status_owner"] == "loopx_derived"
    assert [item["lens_id"] for item in contract["guardrail_catalog"]] == list(
        SIMPLIFY_GUARDRAIL_LENS_IDS
    )
    assert set(contract["result_template"]) == {
        "schema_version",
        "scope_fingerprint",
        "reviewed_final_scope",
        "reuse",
        "simplification",
        "risks",
        "validation",
    }


def test_result_rejects_legacy_lens_table(tmp_path: Path) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "legacy-table.json",
        prepared["scope"]["scope_fingerprint"],
        lens_reviews=[],
    )

    with pytest.raises(ValueError, match="unsupported fields"):
        record_change_quality_receipt(
            registry_path=registry,
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            repo_path=repo,
            result_path=result_path,
            base_ref="HEAD",
            execute=False,
        )


def test_sparse_result_derives_inactive_guardrails_without_agent_rows(
    tmp_path: Path,
) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "sparse.json",
        prepared["scope"]["scope_fingerprint"],
    )
    recorded = record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref="HEAD",
        execute=True,
    )

    assert recorded["decision"] == "pass"
    guardrails = recorded["receipt"]["guardrails"]
    assert guardrails["derived"] is True
    assert guardrails["blocking_codes"] == []
    assert {
        item["guardrail_id"]: item["status"] for item in guardrails["states"]
    } == {
        "type_api_boundary": "not_triggered",
        "configuration": "not_triggered",
        "runtime_ownership": "not_triggered",
        "efficiency": "not_triggered",
        "error_supervision": "not_triggered",
        "test_validation": "satisfied",
        "documentation_comments": "not_triggered",
        "security_release": "not_triggered",
    }


def test_result_evidence_must_reference_projected_scope(tmp_path: Path) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "AGENTS.md").write_text("# Review the exact diff.\n", encoding="utf-8")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "unknown-evidence.json",
        prepared["scope"]["scope_fingerprint"],
        reuse={
            "outcome": "retained",
            "summary": "Grounded in an instruction outside the projected scope.",
            "evidence_refs": ["instruction:docs/UNKNOWN.md"],
        },
    )

    with pytest.raises(ValueError, match="unknown evidence"):
        record_change_quality_receipt(
            registry_path=registry,
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            repo_path=repo,
            result_path=result_path,
            base_ref="HEAD",
            execute=False,
        )


def test_five_pr_calibration_replays_substantive_simplify_receipts() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "change_quality"
        / "five_pr_calibration.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = fixture["cases"]

    assert {case["source"]["pull_request"] for case in cases} == {
        2590,
        2593,
        2594,
        2597,
        2602,
    }
    assert {
        case["source"]["pull_request"] for case in cases if case["manual_holds"]
    } == {2593}
    assert [
        case["source"]["pull_request"]
        for case in cases
        if case["simplification"]["safe_fix_applied"]
    ] == [2594]

    for case in cases:
        fingerprint = hashlib.sha256(
            json.dumps(case, sort_keys=True).encode("utf-8")
        ).hexdigest()
        result = {
            "schema_version": CHANGE_QUALITY_RESULT_SCHEMA_VERSION,
            "scope_fingerprint": fingerprint,
            "reviewed_final_scope": True,
            "reuse": case["reuse"],
            "simplification": case["simplification"],
            "risks": case["risks"],
            "validation": case["validation"],
        }

        normalized = normalize_change_quality_result(
            result,
            expected_fingerprint=fingerprint,
            safe_fix_allowed=True,
            expected_changed_files=case["changed_files"],
            expected_instruction_refs=[],
        )

        assert set(normalized) == {
            "schema_version",
            "scope_fingerprint",
            "reviewed_final_scope",
            "reuse",
            "simplification",
            "risks",
            "validation",
        }
        assert derive_change_quality_guardrails(normalized)["derived"] is True


def test_failed_validation_cannot_produce_passing_receipt(tmp_path: Path) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
        validation=[
            {
                "validator": "pytest-focused",
                "status": "failed",
                "scope": "changed capability tests",
                "reason": "One contract assertion failed.",
                "required": True,
            }
        ],
    )

    recorded = record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref="HEAD",
        execute=False,
    )
    assert recorded["decision"] == "fail"
    assert recorded["unresolved_blockers"] == ["validator:pytest-focused"]


def test_skipped_required_validation_is_derived_as_blocking(tmp_path: Path) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
        validation=[
            {
                "validator": "required-typecheck",
                "status": "skipped",
                "scope": "changed public schema",
                "required": True,
                "reason": "The checker was unavailable.",
            }
        ],
    )

    recorded = record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref="HEAD",
        execute=False,
    )

    assert recorded["decision"] == "fail"
    assert recorded["unresolved_blockers"] == ["validator:required-typecheck"]
    validation_guardrail = next(
        item
        for item in recorded["receipt"]["guardrails"]["states"]
        if item["guardrail_id"] == "test_validation"
    )
    assert validation_guardrail["status"] == "blocked"


def test_verification_revalidates_stored_result_semantics(tmp_path: Path) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
    )
    recorded = record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref="HEAD",
        execute=True,
    )
    receipt_path = Path(recorded["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    del receipt["result"]["reuse"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    verified = verify_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    assert verified["status"] == "invalid_receipt"
    assert verified["ok"] is False


def test_verification_rejects_v1_receipt(tmp_path: Path) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
    )
    recorded = record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref="HEAD",
        execute=True,
    )
    legacy_receipt = dict(recorded["receipt"])
    legacy_receipt["schema_version"] = "change_quality_receipt_v1"
    legacy_receipt["result"]["schema_version"] = "change_quality_agent_result_v1"
    Path(recorded["receipt_path"]).write_text(
        json.dumps(legacy_receipt),
        encoding="utf-8",
    )

    verified = verify_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )

    assert verified["status"] == "invalid_receipt"
    assert verified["ok"] is False


def test_exact_scope_receipt_becomes_stale_after_any_diff_change(
    tmp_path: Path,
) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
    )

    recorded = record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref="HEAD",
        execute=True,
    )
    verified = verify_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    assert recorded["decision"] == "pass"
    assert verified["status"] == "valid"
    assert verified["ok"] is True
    assert verified["pr_evidence"]["state"] == "valid"
    assert verified["pr_evidence"]["receipt_id"] == recorded["receipt_id"]
    assert verified["pr_evidence"]["requalification_required"] is False

    (repo / "app.py").write_text("value = 3\n", encoding="utf-8")
    stale = verify_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    assert stale["status"] == "stale_receipt"
    assert stale["ok"] is False
    assert stale["pr_evidence"] == {
        "schema_version": "change_quality_pr_evidence_v0",
        "state": "stale",
        "summary": (
            "A prior receipt exists, but the exact base or diff changed; "
            "requalification is required."
        ),
        "blocking": True,
        "requalification_required": True,
        "scope_fingerprint": stale["scope"]["scope_fingerprint"],
        "base_ref": "HEAD",
        "base_commit": stale["scope"]["base_commit"],
        "head_commit": stale["scope"]["head_commit"],
        "receipt_id": None,
        "previous_receipt_id": recorded["receipt_id"],
        "previous_scope_fingerprint": prepared["scope"]["scope_fingerprint"],
        "previous_base_commit": prepared["scope"]["base_commit"],
        "required_action": (
            "rerun change-quality prepare against the current base and diff, "
            "review that exact scope, and record a new passing receipt"
        ),
    }


def test_base_advance_projects_visible_requalification_prompt(tmp_path: Path) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    base_branch = _git(repo, "branch", "--show-current")
    _git(repo, "switch", "--detach", "HEAD")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref=base_branch,
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
    )
    recorded = record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref=base_branch,
        execute=True,
    )

    new_base = _git(
        repo,
        "commit-tree",
        "HEAD^{tree}",
        "-p",
        "HEAD",
        "-m",
        "advance base",
    )
    _git(repo, "update-ref", f"refs/heads/{base_branch}", new_base)
    stale = verify_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref=base_branch,
    )

    assert stale["status"] == "stale_receipt"
    assert stale["ok"] is False
    assert stale["pr_evidence"]["state"] == "stale"
    assert stale["pr_evidence"]["requalification_required"] is True
    assert stale["pr_evidence"]["previous_receipt_id"] == recorded["receipt_id"]
    assert stale["pr_evidence"]["previous_base_commit"] != new_base
    assert "current base and diff" in stale["pr_evidence"]["required_action"]


def test_receipt_identity_is_stable_from_repository_subdirectories(
    tmp_path: Path,
) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    nested = repo / "src"
    nested.mkdir()
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
    )
    record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref="HEAD",
        execute=True,
    )

    verified = verify_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=nested,
        base_ref="HEAD",
    )
    assert verified["status"] == "valid"
    assert verified["ok"] is True


def test_safe_fix_result_is_rejected_when_policy_forbids_mutation(
    tmp_path: Path,
) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry, safe_fix=False, strict_receipt=False)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
        simplification={
            "outcome": "fixed",
            "summary": "Applied one bounded simplification.",
            "evidence_refs": ["path:app.py"],
            "safe_fix_applied": True,
        },
    )

    with pytest.raises(ValueError, match="policy forbids safe fixes"):
        record_change_quality_receipt(
            registry_path=registry,
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            repo_path=repo,
            result_path=result_path,
            base_ref="HEAD",
            execute=False,
        )


def test_unresolved_blocker_cannot_produce_passing_receipt(
    tmp_path: Path,
) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    prepared = build_change_quality_prepare_packet(
        registry_path=registry,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    result_path = _result(
        tmp_path / "result.json",
        prepared["scope"]["scope_fingerprint"],
        risks=[
            {
                "category": "test_validation",
                "severity": "blocker",
                "code": "required-validation-failed",
                "message": "The required fixture validation failed.",
                "resolved": False,
                "evidence_refs": ["path:app.py"],
                "path": "app.py",
                "line": 1,
            }
        ],
    )

    recorded = record_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        result_path=result_path,
        base_ref="HEAD",
        execute=True,
    )
    assert recorded["ok"] is False
    assert recorded["decision"] == "fail"
    assert recorded["unresolved_blockers"] == ["required-validation-failed"]


def test_strict_verification_failure_overrides_premerge_gate(
    tmp_path: Path,
) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    verification = verify_change_quality_receipt(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        repo_path=repo,
        base_ref="HEAD",
    )
    gate = build_premerge_validation_gate(
        catalog_path=Path(__file__).resolve().parents[2] / "docs/concepts/interaction-pattern-catalog.md",
        changed_files=["app.py"],
        base_ref="HEAD",
        execute=False,
        repo_root=repo,
    )

    apply_change_quality_verification(gate, verification)

    assert gate["ok"] is False
    assert gate["gate"]["status"] == "quality_receipt_missing"
    assert gate["gate"]["merge_gate_passed"] is False
    assert gate["validation_summary"]["policy_failure_count"] == 1
    assert "receipt_path" not in gate["change_quality_qualification"]
    assert gate["change_quality_qualification"]["pr_evidence"]["state"] == "missing"
    markdown = render_premerge_validation_gate_markdown(gate)
    assert "- state: `missing`" in markdown
    assert "- requalification_required: `false`" in markdown
    assert str(runtime_root) not in markdown


def test_premerge_cli_enforces_goal_receipt_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    _enable(registry)
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    exit_code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "canary",
            "premerge",
            "--from-git-diff",
            "--git-diff-base",
            "HEAD",
            "--goal-id",
            GOAL_ID,
            "--no-execute",
            "--catalog",
            str(Path(__file__).resolve().parents[2] / "docs/concepts/interaction-pattern-catalog.md"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["change_quality_qualification"]["status"] == "receipt_missing"
    assert payload["gate"]["merge_gate_passed"] is False
    assert payload["gate"]["self_merge_allowed"] is False


def test_premerge_cli_resolves_global_registry_after_successful_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, registry, runtime_root = _fixture(tmp_path)
    runtime_root.mkdir()
    global_registry = runtime_root / "registry.global.json"
    global_registry.write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    def successful_gate(**kwargs: object) -> dict[str, object]:
        assert kwargs["execute"] is True
        return {
            "ok": True,
            "gate": {
                "status": "passed",
                "merge_gate_passed": True,
                "self_merge_allowed": True,
            },
            "validation_summary": {
                "selected_check_count": 1,
                "executed_check_count": 1,
                "failure_count": 0,
            },
            "recommended_pr_comment_fields": [],
        }

    monkeypatch.setattr(
        "loopx.cli_commands.canary.build_premerge_validation_gate",
        successful_gate,
    )

    exit_code = main(
        [
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "canary",
            "premerge",
            "--from-git-diff",
            "--git-diff-base",
            "HEAD",
            "--goal-id",
            GOAL_ID,
            "--no-progress",
            "--catalog",
            str(Path(__file__).resolve().parents[2] / "docs/concepts/interaction-pattern-catalog.md"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["gate"]["merge_gate_passed"] is True
    assert payload["change_quality_qualification"]["status"] == "disabled"
    assert not (repo / ".loopx" / "registry.json").exists()


def test_quality_skill_delivery_is_policy_conditional_and_host_neutral(
    tmp_path: Path,
) -> None:
    inactive = _skill_delivery_contract("other-agent")
    assert inactive["required_skill_ids"] == ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS

    active_custom = _skill_delivery_contract(
        "other-agent",
        active_project_skill_ids=["loopx-change-quality"],
    )
    assert "loopx-change-quality" in active_custom["required_skill_ids"]
    assert "skills/loopx-change-quality" in active_custom["source_directories"]

    active_codex = _skill_delivery_contract(
        "codex-cli",
        project=str(tmp_path / "project"),
        active_project_skill_ids=["loopx-change-quality"],
    )
    commands = active_codex["project_skill_commands"][0]
    assert commands["status"].startswith("loopx project-skill status ")
    assert commands["preview_install"].startswith("loopx project-skill install ")
    assert commands["apply_install"].endswith(" --execute")


def test_quality_skill_uses_managed_project_delivery(tmp_path: Path) -> None:
    project = tmp_path / "project"
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text("{}\n", encoding="utf-8")

    preview = install_project_skill(
        project,
        "loopx-change-quality",
        surfaces=("codex",),
        execute=False,
    )
    assert preview["status"] == "missing"
    assert preview["changed"] is True

    applied = install_project_skill(
        project,
        "loopx-change-quality",
        surfaces=("codex",),
        execute=True,
    )
    assert applied["status"] == "current"
    readback = inspect_project_skill(
        project,
        "loopx-change-quality",
        surfaces=("codex",),
    )
    assert readback["status"] == "current"
    assert readback["managed"] is True
