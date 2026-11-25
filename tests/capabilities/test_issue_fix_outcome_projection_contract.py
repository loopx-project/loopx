from __future__ import annotations

import ast
import contextlib
import io
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

import pytest

from loopx.capabilities.issue_fix import outcome_case, outcome_collection, outcome_projection
from loopx.capabilities.issue_fix.feasibility import (
    build_issue_fix_feasibility_packet,
)
from loopx.capabilities.issue_fix.outcome_case import (
    DELIVERY_OUTCOME_STATUSES,
    DELIVERY_VALIDATION_STATUSES,
    ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION,
    ISSUE_FIX_OUTCOME_CASE_SCHEMA_VERSION,
    ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION,
    ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION,
    ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION,
    ISSUE_FIX_REUSABLE_KNOWLEDGE_INPUT_SCHEMA_VERSION,
    render_issue_fix_outcome_projection_markdown,
    validate_issue_fix_outcome_collection_projection,
    validate_issue_fix_outcome_projection,
)
from loopx.capabilities.issue_fix.outcome_collection import (
    IssueFixOutcomeCollectionProjection,
)
from loopx.capabilities.issue_fix.outcome_projection import (
    ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION,
    build_issue_fix_outcome_collection_from_domain_state,
    build_issue_fix_outcome_projection,
    compact_issue_fix_delivery_evidence,
)
from loopx.capabilities.issue_fix.pr_lifecycle import (
    build_issue_fix_pr_lifecycle_monitor_packet,
)
from loopx.cli import main as cli_main
from loopx.control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from loopx.domain_packs.issue_fix import (
    default_issue_fix_domain_state_ledger_path,
    default_issue_fix_feasibility_ledger_path,
    upsert_issue_fix_feasibility_ledger_jsonl,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from loopx.extensions.lark.presentation.kanban import (
    LarkKanbanConfig,
    sync_loopx_todos_to_lark_kanban,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_AT = "2026-11-26T00:00:00Z"
COMMIT_OID = "0123456789abcdef0123456789abcdef01234567"
FINGERPRINT = "sha256:" + ("a" * 64)


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze_value(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(mapping: Mapping[str, object]) -> MappingProxyType[str, object]:
    return MappingProxyType({str(k): _freeze_value(v) for k, v in mapping.items()})


def _freeze_dicts_only_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(k): _freeze_dicts_only_value(v) for k, v in value.items()}
        )
    if isinstance(value, list):
        return [_freeze_dicts_only_value(item) for item in value]
    return value


def _freeze_dicts_only(mapping: Mapping[str, object]) -> MappingProxyType[str, object]:
    return MappingProxyType(
        {str(k): _freeze_dicts_only_value(v) for k, v in mapping.items()}
    )


def _imported_module_targets(path: Path, *, top_level_only: bool = False) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes: list[ast.AST] = list(tree.body) if top_level_only else list(ast.walk(tree))
    targets: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.append(node.module or "")
    return targets


def _sample_feasibility_packet(issue_ref: str = "issues_42") -> dict[str, object]:
    return {
        "schema_version": "issue_fix_feasibility_v0",
        "observation": {
            "repo": "example/project",
            "issue_ref": issue_ref,
            "number": 42,
            "permalink": "https://github.com/example/project/issues/42",
            "reproduction_status": "reproduced",
            "repository_context": {
                "repository_fingerprint": FINGERPRINT,
                "repository_revision": COMMIT_OID,
            },
        },
        "decision": {"route": "fix_pr"},
        "transition": {"decision": "advance"},
        "repository_context_effect": {},
    }


def _sample_pr_lifecycle_packet(
    *, issue_ref: str = "issues_42", pr_ref: str = "pulls_101", pr_number: int = 101
) -> dict[str, object]:
    return {
        "schema_version": "issue_fix_pr_lifecycle_monitor_v0",
        "generated_at": GENERATED_AT,
        "observation": {
            "repo": "example/project",
            "issue_ref": issue_ref,
            "pr_ref": pr_ref,
            "number": pr_number,
            "permalink": f"https://github.com/example/project/pull/{pr_number}",
            "state": "OPEN",
            "merge_state_status": "CLEAN",
            "updated_at": GENERATED_AT,
        },
        "transition": {"decision": "continue"},
    }


def _sample_reusable_knowledge() -> dict[str, object]:
    return {
        "schema_version": ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION,
        "symptom_signature": "Malformed delivery inputs bypassed validation",
        "reproduction_contract": "Pass non-sequence outputs or MappingProxyType inputs",
        "root_cause": "Implicit truthiness fallback skipped sequence type guard",
        "violated_invariant": "Malformed collection inputs must fail closed",
        "repair_pattern": "Normalize Mapping trees and check Sequence before building",
        "validation_contract": "Compare dict vs MappingProxyType parity and negative errors",
        "applicability": "issue-fix delivery evidence inputs",
        "non_applicability": "non-issue-fix freeform logs",
        "verification_references": [
            "tests/capabilities/test_issue_fix_outcome_projection_contract.py"
        ],
        "confidence": "high",
        "affected_modules": ["loopx/capabilities/issue_fix/outcome_projection.py"],
        "invalidation_conditions": ["Delivery schema replaces sequence fields"],
        "revalidation_contract": "Re-run pytest contract suite on schema changes",
        "current_checkout_verification_required": True,
    }


def _sample_delivery_evidence() -> dict[str, object]:
    return {
        "schema_version": ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION,
        "outcome_status": "completed",
        "validation_status": "passed",
        "validation_label": "Contract suite passed",
        "changed_files": ["loopx/capabilities/issue_fix/outcome_projection.py"],
        "commit_ref": "codex/issue-42-fix",
        "repository_commit_evidence": {
            "schema_version": ISSUE_FIX_REPOSITORY_COMMIT_EVIDENCE_SCHEMA_VERSION,
            "status": "verified",
            "repo": "example/project",
            "repository_fingerprint": FINGERPRINT,
            "declared_repository_revision": COMMIT_OID,
            "repository_revision": COMMIT_OID,
            "declared_commit_ref": "codex/issue-42-fix",
            "commit_oid": COMMIT_OID,
            "recovery_ref": "refs/heads/codex/issue-42-fix",
            "recovery_ref_oid": COMMIT_OID,
            "commit_is_ancestor": True,
            "verified_at": GENERATED_AT,
            "repo_path_captured": False,
            "remote_urls_captured": False,
            "raw_git_output_captured": False,
        },
        "outputs": [{"kind": "pr", "url": "https://github.com/example/project/pull/101"}],
        "risks": ["Watch post-split imports"],
        "recorded_at": GENERATED_AT,
        "reusable_knowledge": _sample_reusable_knowledge(),
    }


def test_outcome_projection_modules_meet_size_and_zero_any_budgets() -> None:
    type_ignore_marker = "# type:" + " ignore"
    paths = [
        REPO_ROOT / "loopx/capabilities/issue_fix/outcome_case.py",
        REPO_ROOT / "loopx/capabilities/issue_fix/outcome_collection.py",
        REPO_ROOT / "loopx/capabilities/issue_fix/outcome_projection.py",
        Path(__file__).resolve(),
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        assert len(lines) < 700, f"{path.name} exceeded 700 LOC ({len(lines)})"
        assert type_ignore_marker not in source, f"{path.name} contains type ignore"
        tree = ast.parse(source)
        any_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "Any"
        ]
        assert not any_nodes, f"{path.name} references typing.Any"


def test_outcome_projection_preserves_acyclic_split_and_content_digest_owner() -> None:
    case_path = REPO_ROOT / "loopx/capabilities/issue_fix/outcome_case.py"
    collection_path = REPO_ROOT / "loopx/capabilities/issue_fix/outcome_collection.py"
    case_imports = _imported_module_targets(case_path)
    collection_top_imports = _imported_module_targets(collection_path, top_level_only=True)
    collection_all_imports = _imported_module_targets(collection_path)

    assert not any("outcome_collection" in name for name in case_imports)
    assert not any("outcome_projection" in name for name in case_imports)
    assert not any("content_digest" in name for name in case_imports)
    assert not any("outcome_projection" in name for name in collection_top_imports)
    assert not any("content_digest" in name for name in collection_all_imports)
    assert outcome_projection._REPOSITORY_FINGERPRINT_PATTERN is ENVELOPED_SHA256_PATTERN


def test_outcome_projection_reexport_identity_parity() -> None:
    pairs = (
        (outcome_projection.ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION, ISSUE_FIX_OUTCOME_PROJECTION_SCHEMA_VERSION),
        (outcome_projection.ISSUE_FIX_OUTCOME_CASE_SCHEMA_VERSION, ISSUE_FIX_OUTCOME_CASE_SCHEMA_VERSION),
        (outcome_projection.ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION, ISSUE_FIX_OUTCOME_COLLECTION_PROJECTION_SCHEMA_VERSION),
        (outcome_projection.ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION, ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION),
        (outcome_projection.ISSUE_FIX_REUSABLE_KNOWLEDGE_INPUT_SCHEMA_VERSION, ISSUE_FIX_REUSABLE_KNOWLEDGE_INPUT_SCHEMA_VERSION),
        (outcome_projection.ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION, ISSUE_FIX_REPOSITORY_LEARNING_CARD_INPUT_SCHEMA_VERSION),
        (outcome_projection.DELIVERY_VALIDATION_STATUSES, DELIVERY_VALIDATION_STATUSES),
        (outcome_projection.DELIVERY_OUTCOME_STATUSES, DELIVERY_OUTCOME_STATUSES),
        (outcome_projection.IssueFixOutcomeCollectionProjection, IssueFixOutcomeCollectionProjection),
        (outcome_projection.validate_issue_fix_outcome_projection, validate_issue_fix_outcome_projection),
        (outcome_projection.validate_issue_fix_outcome_collection_projection, validate_issue_fix_outcome_collection_projection),
        (outcome_projection.render_issue_fix_outcome_projection_markdown, render_issue_fix_outcome_projection_markdown),
    )
    for reexport, owner in pairs:
        assert reexport is owner
    assert outcome_case is not outcome_projection
    assert outcome_collection is not outcome_projection


def test_mapping_proxy_parity_for_delivery_outcome_and_markdown() -> None:
    feasibility = _sample_feasibility_packet()
    lifecycle = _sample_pr_lifecycle_packet()
    delivery = _sample_delivery_evidence()

    frozen_delivery = _freeze_mapping(delivery)
    frozen_feasibility = _freeze_mapping(feasibility)
    frozen_lifecycle = _freeze_mapping(lifecycle)

    plain_compact = compact_issue_fix_delivery_evidence(delivery, recorded_at=GENERATED_AT)
    proxy_compact = compact_issue_fix_delivery_evidence(
        frozen_delivery, recorded_at=GENERATED_AT
    )
    assert plain_compact == proxy_compact
    json.dumps(proxy_compact)

    plain_projection = build_issue_fix_outcome_projection(
        goal_id="goal-42",
        feasibility_packet=feasibility,
        pr_lifecycle_packet=lifecycle,
        delivery_evidence_input=delivery,
        agent_id="agent-alpha",
        generated_at=GENERATED_AT,
    )
    proxy_projection = build_issue_fix_outcome_projection(
        goal_id="goal-42",
        feasibility_packet=frozen_feasibility,
        pr_lifecycle_packet=frozen_lifecycle,
        delivery_evidence_input=frozen_delivery,
        agent_id="agent-alpha",
        generated_at=GENERATED_AT,
    )
    assert plain_projection == proxy_projection
    assert plain_projection["ok"] is True
    json.dumps(proxy_projection)

    frozen_result = _freeze_dicts_only(proxy_projection)
    assert validate_issue_fix_outcome_projection(frozen_result)["ok"] is True
    assert render_issue_fix_outcome_projection_markdown(
        plain_projection
    ) == render_issue_fix_outcome_projection_markdown(frozen_result)

    failure_md = render_issue_fix_outcome_projection_markdown(
        MappingProxyType({"ok": False, "error": "bad_input"})
    )
    assert "- ok: `false`" in failure_md
    assert "bad_input" in failure_md


def test_fail_closed_validation_rejects_malformed_and_falsy_inputs() -> None:
    build_untyped: Callable[..., dict[str, object]] = build_issue_fix_outcome_projection
    validate_outcome_untyped: Callable[..., dict[str, object]] = (
        validate_issue_fix_outcome_projection
    )
    validate_collection_untyped: Callable[..., dict[str, object]] = (
        validate_issue_fix_outcome_collection_projection
    )
    render_untyped: Callable[..., str] = render_issue_fix_outcome_projection_markdown

    with pytest.raises(ValueError, match="feasibility packet must be an object"):
        build_untyped(goal_id="goal-42", feasibility_packet=["not-a-mapping"])

    with pytest.raises(ValueError, match="PR lifecycle packet must be an object"):
        build_untyped(
            goal_id="goal-42",
            feasibility_packet=_sample_feasibility_packet(),
            pr_lifecycle_packet="bad-lifecycle",
        )

    with pytest.raises(ValueError, match="delivery evidence must be an object"):
        build_untyped(
            goal_id="goal-42",
            feasibility_packet=_sample_feasibility_packet(),
            delivery_evidence_input=123,
        )

    with pytest.raises(ValueError, match="outcome projection must be an object"):
        validate_outcome_untyped("bad")

    with pytest.raises(ValueError, match="outcome collection projection must be an object"):
        validate_collection_untyped(["bad"])

    with pytest.raises(ValueError, match="outcome projection payload must be an object"):
        render_untyped("bad")

    for legacy_field in ("outputs", "changed_files", "risks"):
        legacy_delivery = _sample_delivery_evidence()
        legacy_delivery[legacy_field] = ""
        legacy_compact = compact_issue_fix_delivery_evidence(legacy_delivery)
        assert legacy_compact[legacy_field] == []
        legacy_projection = build_issue_fix_outcome_projection(
            goal_id="goal-42",
            feasibility_packet=_sample_feasibility_packet(),
            delivery_evidence_input=legacy_delivery,
            generated_at=GENERATED_AT,
        )
        assert legacy_projection["ok"] is True

    bad_optional_sequences: tuple[object, ...] = ({}, "invalid", " ", 0, False, b"")
    for bad_seq in bad_optional_sequences:
        bad_outputs = _sample_delivery_evidence()
        bad_outputs["outputs"] = bad_seq
        with pytest.raises(ValueError, match="delivery evidence outputs must be a list"):
            compact_issue_fix_delivery_evidence(bad_outputs)

        bad_files = _sample_delivery_evidence()
        bad_files["changed_files"] = bad_seq
        with pytest.raises(ValueError, match="changed_files must be a list"):
            compact_issue_fix_delivery_evidence(bad_files)

        bad_risks = _sample_delivery_evidence()
        bad_risks["risks"] = bad_seq
        with pytest.raises(ValueError, match="risks must be a list"):
            compact_issue_fix_delivery_evidence(bad_risks)

    bad_required_sequences: tuple[object, ...] = ({}, "", "invalid", 0, False)
    for bad_seq in bad_required_sequences:
        bad_knowledge = _sample_reusable_knowledge()
        bad_knowledge["affected_modules"] = bad_seq
        bad_delivery_knowledge = _sample_delivery_evidence()
        bad_delivery_knowledge["reusable_knowledge"] = bad_knowledge
        with pytest.raises(ValueError, match="changed_files must be a list"):
            compact_issue_fix_delivery_evidence(bad_delivery_knowledge)

        bad_invalidation = _sample_reusable_knowledge()
        bad_invalidation["invalidation_conditions"] = bad_seq
        bad_delivery_invalidation = _sample_delivery_evidence()
        bad_delivery_invalidation["reusable_knowledge"] = bad_invalidation
        with pytest.raises(ValueError, match="invalidation_conditions must be a list"):
            compact_issue_fix_delivery_evidence(bad_delivery_invalidation)


def test_collection_projection_typed_container_and_domain_state_coalescing(
    tmp_path: Path,
) -> None:
    goal_id = "goal-collection-test"
    feasibility_path = default_issue_fix_feasibility_ledger_path(
        project=tmp_path, goal_id=goal_id
    )
    lifecycle_path = default_issue_fix_domain_state_ledger_path(
        project=tmp_path, goal_id=goal_id
    )
    feasibility_path.parent.mkdir(parents=True, exist_ok=True)
    feasibility_path.write_text(
        json.dumps(_sample_feasibility_packet("issues_42")) + "\n",
        encoding="utf-8",
    )

    linked_pr = _sample_pr_lifecycle_packet(
        issue_ref="issues_42", pr_ref="pulls_101", pr_number=101
    )
    unlinked_older = _sample_pr_lifecycle_packet(
        issue_ref="", pr_ref="pulls_202", pr_number=202
    )
    unlinked_older_obs = unlinked_older["observation"]
    assert isinstance(unlinked_older_obs, dict)
    unlinked_older_obs["issue_ref"] = "issues_99"
    unlinked_older_obs["updated_at"] = "2026-11-25T00:00:00Z"

    unlinked_newer = _sample_pr_lifecycle_packet(
        issue_ref="", pr_ref="pulls_203", pr_number=203
    )
    unlinked_newer_obs = unlinked_newer["observation"]
    assert isinstance(unlinked_newer_obs, dict)
    unlinked_newer_obs["issue_ref"] = "issues_99"
    unlinked_newer_obs["updated_at"] = "2026-11-26T01:00:00Z"

    missing_id_pr = {
        "schema_version": "issue_fix_pr_lifecycle_monitor_v0",
        "observation": {"repo": "example/project"},
    }
    invalid_schema_pr = {
        "schema_version": "bad_schema_v0",
        "observation": {"repo": "example/project", "pr_ref": "pulls_999"},
    }
    lifecycle_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                linked_pr,
                unlinked_older,
                unlinked_newer,
                missing_id_pr,
                invalid_schema_pr,
            )
        )
        + "\n",
        encoding="utf-8",
    )

    collection = build_issue_fix_outcome_collection_from_domain_state(
        goal_id=goal_id,
        project=tmp_path,
        agent_id="agent-alpha",
        generated_at=GENERATED_AT,
    )
    assert isinstance(collection, IssueFixOutcomeCollectionProjection)
    assert isinstance(collection, dict)
    assert collection["ok"] is True

    outcomes = collection.get("issue_fix_outcomes")
    warnings = collection.get("warnings")
    assert isinstance(outcomes, list) and len(outcomes) == 2
    assert isinstance(warnings, list)
    assert any("skipped example/project:pulls_999" in msg for msg in warnings)

    counts = collection["source_counts"]
    assert isinstance(counts, dict)
    assert counts["feasibility"] == 1
    assert counts["pr_lifecycle"] == 5
    assert counts["outcomes"] == 2
    assert counts["lifecycle_only_outcomes"] == 1
    assert counts["coalesced_pr_lifecycle"] == 1
    assert counts["unprojected_pr_lifecycle"] == 2

    assert validate_issue_fix_outcome_collection_projection(
        _freeze_dicts_only(collection)
    )["ok"] is True


def _run_issue_fix_outcome_cli(
    args: list[str], *, expected_rc: int = 0
) -> dict[str, object]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = cli_main(args)
    assert rc == expected_rc, output.getvalue()
    payload = json.loads(output.getvalue())
    assert isinstance(payload, dict)
    return {str(k): v for k, v in payload.items()}


def _setup_v0_project(
    project: Path, goal_id: str, issue_numbers: tuple[int, ...] = (42,)
) -> tuple[Path, Path, Path, str]:
    project.mkdir(parents=True, exist_ok=True)
    state_path = project / "active-state.md"
    state_path.write_text("## User Todo\n\n## Agent Todo\n", encoding="utf-8")
    registry_path = project / "registry.json"
    registry_path.write_text(
        json.dumps(
            {"goals": [{"id": goal_id, "repo": str(project), "state_file": state_path.name}]}
        ),
        encoding="utf-8",
    )
    feasibility_path = default_issue_fix_feasibility_ledger_path(project=project, goal_id=goal_id)
    lifecycle_path = default_issue_fix_domain_state_ledger_path(project=project, goal_id=goal_id)
    for num in issue_numbers:
        packet = build_issue_fix_feasibility_packet(
            url=f"https://github.com/example/project/issues/{num}",
            reproduction_status="confirmed",
            scope_class="bounded",
            reproduction_label=f"focused legacy repro {num}",
            validation_label=f"focused legacy validation {num}",
            repository_context_input={
                "schema_version": "issue_fix_repository_context_input_v0",
                "repository_revision": COMMIT_OID,
                "sources": [],
            },
        )
        upsert_issue_fix_feasibility_ledger_jsonl(feasibility_path, packet)
    merged_pr = build_issue_fix_pr_lifecycle_monitor_packet(
        url="https://github.com/example/project/pull/101",
        issue_ref="#42",
        provider_payload={
            "state": "MERGED",
            "mergedAt": GENERATED_AT,
            "reviewDecision": "APPROVED",
            "mergeStateStatus": "CLEAN",
            "statusCheckRollup": [{"name": "focused", "conclusion": "SUCCESS"}],
        },
    )
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(lifecycle_path, merged_pr)
    return registry_path, feasibility_path, lifecycle_path, str(merged_pr["observation"]["pr_ref"])


def _kanban_issue_fix_cards(registry_path: Path, goal_id: str) -> tuple[dict[str, object], list[str]]:
    cfg = LarkKanbanConfig(base_token="base_public_fixture", table_id="tbl_public_fixture")
    sync = sync_loopx_todos_to_lark_kanban(cfg, registry_path=registry_path, goal_id=goal_id, execute=False)
    statuses = [
        str(row["values"].get("Status") or "")
        for row in sync["records"]
        if isinstance(row, dict) and isinstance(row.get("values"), dict)
        and row["values"].get("Work Item Type") == "Issue Fix"
    ]
    return {str(k): v for k, v in sync.items()}, statuses


def test_legacy_empty_string_delivery_fields_cli_writeback_and_kanban_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for legacy_field in ("changed_files", "outputs", "risks"):
        project = tmp_path / f"legacy-{legacy_field}"
        goal_id = f"goal-legacy-{legacy_field}"
        registry_path, feasibility_path, _, pr_ref = _setup_v0_project(project, goal_id)
        legacy_delivery = _sample_delivery_evidence()
        legacy_delivery.pop("commit_ref", None)
        legacy_delivery.pop("repository_commit_evidence", None)
        legacy_delivery[legacy_field] = ""
        delivery_path = project / "delivery.json"
        delivery_path.write_text(json.dumps(legacy_delivery), encoding="utf-8")

        seeded = json.loads(feasibility_path.read_text(encoding="utf-8").strip())
        seeded["delivery_evidence"] = {**legacy_delivery, "validation_label": "Preexisting row"}
        feasibility_path.write_text(json.dumps(seeded) + "\n", encoding="utf-8")
        legacy_sync, legacy_cards = _kanban_issue_fix_cards(registry_path, goal_id)
        assert legacy_sync["ok"] is True and legacy_sync["issue_fix_outcome_count"] == 1
        assert legacy_sync["warnings"] == [] and legacy_cards == ["Done"]

        base_args = [
            "--format", "json", "issue-fix", "outcome", "--goal-id", goal_id,
            "--project", str(project), "--repo", "example/project",
            "--issue-ref", "issues_42", "--pr-ref", pr_ref,
        ]
        before_bytes = feasibility_path.read_bytes()
        assert _run_issue_fix_outcome_cli([*base_args, "--delivery-evidence-json", str(delivery_path)])["ok"] is True
        assert feasibility_path.read_bytes() == before_bytes
        written = _run_issue_fix_outcome_cli(
            [*base_args, "--delivery-evidence-json", str(delivery_path), "--write-delivery-evidence"]
        )
        assert isinstance(written["domain_state_write"], dict) and written["domain_state_write"]["status"] == "updated"
        repeated = _run_issue_fix_outcome_cli(
            [*base_args, "--delivery-evidence-json", str(delivery_path), "--write-delivery-evidence"]
        )
        assert isinstance(repeated["domain_state_write"], dict) and repeated["domain_state_write"]["status"] == "unchanged"
        readback = _run_issue_fix_outcome_cli(base_args)
        outcomes = readback["issue_fix_outcomes"]
        assert isinstance(outcomes, list) and len(outcomes) == 1
        assert isinstance(outcomes[0], dict) and outcomes[0]["stage"] == "merged"
        sync, cards = _kanban_issue_fix_cards(registry_path, goal_id)
        assert sync["ok"] is True and sync["issue_fix_outcome_count"] == 1
        assert sync["warnings"] == [] and cards == ["Done"]


def test_persisted_empty_container_v0_ledger_read_replace_and_kanban_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "empty-containers"
    goal_id = "goal-empty-container-v0"
    registry_path, feasibility_path, _, pr_ref = _setup_v0_project(project, goal_id, (42, 43))
    rows = [json.loads(line) for line in feasibility_path.read_text(encoding="utf-8").splitlines()]
    base_delivery = _sample_delivery_evidence()
    base_delivery.pop("commit_ref", None)
    base_delivery.pop("repository_commit_evidence", None)
    rows[0]["delivery_evidence"] = {**base_delivery, "outputs": {}, "changed_files": {}, "risks": {}}
    rows[1]["delivery_evidence"] = {**base_delivery, "outputs": [], "changed_files": [], "risks": []}
    for row in rows:
        upsert_issue_fix_feasibility_ledger_jsonl(feasibility_path, row)

    base_args = [
        "--format", "json", "issue-fix", "outcome", "--goal-id", goal_id,
        "--project", str(project), "--repo", "example/project",
        "--issue-ref", "issues_42", "--pr-ref", pr_ref,
    ]
    default_read = _run_issue_fix_outcome_cli(base_args)
    assert default_read["ok"] is True
    read_outcomes = default_read["issue_fix_outcomes"]
    assert isinstance(read_outcomes, list) and isinstance(read_outcomes[0], dict)
    assert isinstance(read_outcomes[0]["validation"], dict)
    assert read_outcomes[0]["validation"]["status"] == "passed" and read_outcomes[0]["stage"] == "merged"

    sync_before, cards_before = _kanban_issue_fix_cards(registry_path, goal_id)
    assert sync_before["ok"] is True and sync_before["issue_fix_outcome_count"] == 2
    assert sync_before["warnings"] == [] and len(cards_before) == 2 and "Done" in cards_before

    bad_submit = project / "bad-submit.json"
    bad_submit.write_text(json.dumps({**base_delivery, "outputs": {}}), encoding="utf-8")
    before_bytes = feasibility_path.read_bytes()
    rejected = _run_issue_fix_outcome_cli(
        [*base_args, "--delivery-evidence-json", str(bad_submit), "--write-delivery-evidence"], expected_rc=1
    )
    assert rejected["ok"] is False and "delivery evidence outputs must be a list" in str(rejected["error"])
    assert feasibility_path.read_bytes() == before_bytes

    equiv_submit = project / "equiv-submit.json"
    equiv_submit.write_text(json.dumps({**base_delivery, "outputs": [], "changed_files": [], "risks": []}), encoding="utf-8")
    unchanged = _run_issue_fix_outcome_cli(
        [*base_args, "--delivery-evidence-json", str(equiv_submit), "--write-delivery-evidence"]
    )
    assert unchanged["ok"] is True and isinstance(unchanged["domain_state_write"], dict)
    assert unchanged["domain_state_write"]["status"] == "unchanged"

    replacement_path = project / "replacement.json"
    replacement_path.write_text(json.dumps(base_delivery), encoding="utf-8")
    replaced = _run_issue_fix_outcome_cli(
        [*base_args, "--delivery-evidence-json", str(replacement_path), "--write-delivery-evidence"]
    )
    assert replaced["ok"] is True and isinstance(replaced["domain_state_write"], dict)
    assert replaced["domain_state_write"]["status"] == "updated"
    assert _run_issue_fix_outcome_cli(base_args)["ok"] is True
    sync_after, cards_after = _kanban_issue_fix_cards(registry_path, goal_id)
    assert sync_after["ok"] is True and sync_after["issue_fix_outcome_count"] == 2
    assert sync_after["warnings"] == [] and len(cards_after) == 2 and "Done" in cards_after


def test_collection_preserves_linked_merged_pr_when_feasibility_row_is_malformed(
    tmp_path: Path,
) -> None:
    goal_id = "goal-linked-merged-fallback"
    feasibility_path = default_issue_fix_feasibility_ledger_path(
        project=tmp_path, goal_id=goal_id
    )
    lifecycle_path = default_issue_fix_domain_state_ledger_path(
        project=tmp_path, goal_id=goal_id
    )
    feasibility_path.parent.mkdir(parents=True, exist_ok=True)
    bad_feasibility = _sample_feasibility_packet("issues_42")
    bad_feasibility["delivery_evidence"] = {
        "schema_version": ISSUE_FIX_DELIVERY_EVIDENCE_INPUT_SCHEMA_VERSION,
        "outputs": {"unexpected": 1},
    }
    feasibility_path.write_text(json.dumps(bad_feasibility) + "\n", encoding="utf-8")

    merged_pr = _sample_pr_lifecycle_packet(
        issue_ref="issues_42", pr_ref="pulls_101", pr_number=101
    )
    merged_obs = merged_pr["observation"]
    assert isinstance(merged_obs, dict)
    merged_obs["state"] = "MERGED"
    merged_obs["merged_at"] = GENERATED_AT
    lifecycle_path.write_text(json.dumps(merged_pr) + "\n", encoding="utf-8")

    collection = build_issue_fix_outcome_collection_from_domain_state(
        goal_id=goal_id,
        project=tmp_path,
        generated_at=GENERATED_AT,
    )
    assert collection["ok"] is True
    warnings = collection.get("warnings") or []
    assert any("skipped example/project:issues_42" in msg for msg in warnings)
    outcomes = collection.get("issue_fix_outcomes") or []
    assert len(outcomes) == 1
    assert outcomes[0]["stage"] == "merged"
    assert outcomes[0]["issue_ref"] == "issues_42"
