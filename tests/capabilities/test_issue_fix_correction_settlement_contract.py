from __future__ import annotations

import ast
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from loopx.capabilities.issue_fix import (
    correction_settlement,
    correction_settlement_gates,
    correction_settlement_input,
    github_public,
)
from loopx.capabilities.issue_fix.correction_settlement import (
    ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION,
    ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1,
    _resolve_origin_request_identity,
    attach_correction_todo_intent,
    evaluate_issue_fix_maintainer_correction_admission,
    maintainer_correction_transition,
    normalise_issue_fix_maintainer_correction_input,
    normalise_issue_fix_pr_source_events,
    recheck_issue_fix_maintainer_correction_effect_gates,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_IGNORE_MARKER = "type" + ": ignore"


def _any_ast_refs(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            count += 1
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            count += 1
        elif isinstance(node, ast.alias) and node.name == "Any":
            count += 1
    return count


def _sample_event(*, updated_at: str = "2026-06-20T10:00:00Z") -> dict[str, object]:
    return {
        "kind": "issue_comment",
        "id": "12345",
        "html_url": "https://github.com/example-org/example-repo/pull/10#issuecomment-12345",
        "author_login": "maintainer-a",
        "author_association": "MEMBER",
        "created_at": "2026-06-20T09:59:00Z",
        "updated_at": updated_at,
    }


def _sample_correction_v1(event: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1,
        "correction_kind": "actionable_patch",
        "source_kind": "maintainer_comment",
        "source_ref": "https://github.com/example-org/example-repo/pull/10#issuecomment-12345",
        "summary": "Tighten null check and cover invalid input",
        "verification_plan": "pytest tests/unit/test_guard.py",
        "pr_update_path": "git push origin codex/fix-guard",
        "source_event": event,
    }


def test_correction_settlement_modules_zero_any_and_under_700_loc() -> None:
    issue_fix_dir = REPO_ROOT / "loopx" / "capabilities" / "issue_fix"
    for filename in (
        "correction_settlement.py",
        "correction_settlement_input.py",
        "correction_settlement_gates.py",
    ):
        path = issue_fix_dir / filename
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) < 700, f"{filename} exceeded 700 LOC ({len(lines)})"
        assert _any_ast_refs(path) == 0, f"{filename} contains Any"
        assert _IGNORE_MARKER not in text, f"{filename} contains {_IGNORE_MARKER}"


def test_correction_settlement_preserves_owners_and_reexports() -> None:
    assert (
        correction_settlement.MAINTAINER_ASSOCIATIONS
        is github_public.MAINTAINER_ASSOCIATIONS
    )
    assert (
        correction_settlement_gates.MAINTAINER_ASSOCIATIONS
        is github_public.MAINTAINER_ASSOCIATIONS
    )
    for symbol in (
        "ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION",
        "ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1",
        "ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION",
        "normalise_issue_fix_maintainer_correction_input",
        "normalise_issue_fix_pr_source_events",
        "build_issue_fix_transition_preview",
        "issue_fix_maintainer_correction_operation_id",
    ):
        assert getattr(correction_settlement, symbol) is getattr(
            correction_settlement_input, symbol
        )
    for symbol in (
        "evaluate_issue_fix_maintainer_correction_admission",
        "recheck_issue_fix_maintainer_correction_effect_gates",
        "maintainer_correction_transition",
    ):
        assert getattr(correction_settlement, symbol) is getattr(
            correction_settlement_gates, symbol
        )


def test_normalization_accepts_mapping_proxy_and_rejects_local_or_corrupt_inputs() -> None:
    event = _sample_event()
    normalized = normalise_issue_fix_maintainer_correction_input(
        MappingProxyType(_sample_correction_v1(event))
    )
    assert normalized["admission_basis"] == "provider_verified"
    assert normalized["source_event"] is not None

    with pytest.raises(ValueError, match="compact and public-safe"):
        normalise_issue_fix_maintainer_correction_input(
            {**_sample_correction_v1(event), "source_ref": "/var/tmp/private/note.md"}
        )

    with pytest.raises(ValueError, match="local path"):
        normalise_issue_fix_maintainer_correction_input(
            {**_sample_correction_v1(event), "source_ref": "~/note.md"}
        )

    transition = maintainer_correction_transition(
        normalized,
        pr_permalink="https://github.com/example-org/example-repo/pull/10",
    )
    admission_stub: dict[str, object] = {"entries": [{}]}
    attach_correction_todo_intent(admission_stub, transition)
    entries_stub = admission_stub["entries"]
    assert isinstance(entries_stub, list) and isinstance(entries_stub[0], dict)
    assert entries_stub[0]["todo_intent"]["required_write_scopes"] == [
        "write",
        "publish",
    ]

    missing_auth = {
        "schema_version": ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1,
        "correction_kind": "missing_authority",
        "source_kind": "maintainer_comment",
        "source_ref": "docs/guide.md",
        "summary": "Need write scope",
        "missing_authority_scopes": ["write"],
        "source_event": event,
    }
    bad_transition = normalise_issue_fix_maintainer_correction_input(missing_auth)
    bad_transition["missing_authority_scopes"] = "not-a-sequence"
    with pytest.raises(TypeError, match="missing_authority_scopes must be a sequence"):
        maintainer_correction_transition(
            bad_transition, pr_permalink="https://github.com/example-org/example-repo/pull/10"
        )


def test_admission_and_effect_recheck_enforce_provider_and_revision_gates() -> None:
    event = _sample_event()
    correction = normalise_issue_fix_maintainer_correction_input(
        _sample_correction_v1(event)
    )
    provider_events = normalise_issue_fix_pr_source_events(
        {
            "comments": [
                {
                    "id": 12345,
                    "author": {"login": "maintainer-a"},
                    "author_association": "MEMBER",
                    "created_at": "2026-06-20T09:59:00Z",
                    "updated_at": "2026-06-20T10:00:00Z",
                }
            ]
        }
    )
    observation: dict[str, object] = {
        "repo": "example-org/example-repo",
        "pr_ref": "pull_10",
        "state": "OPEN",
        "head_commit_ref": "a" * 40,
    }
    admission = evaluate_issue_fix_maintainer_correction_admission(
        correction,
        observation=observation,
        source_events=provider_events,
        prior_entries=[],
        prior_ledger_read=True,
        recorded_at="2026-06-20T10:01:00Z",
    )
    assert admission["schema_version"] == ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION
    assert admission["disposition"] == "accepted"
    entries = admission["entries"]
    assert isinstance(entries, list) and len(entries) == 1
    assert (
        recheck_issue_fix_maintainer_correction_effect_gates(
            entries[0],
            observation=observation,
            ledger_entries=entries,
            goal_stopped=False,
            source_events=provider_events,
        )
        is None
    )
    assert (
        recheck_issue_fix_maintainer_correction_effect_gates(
            entries[0],
            observation={**observation, "state": "MERGED"},
            ledger_entries=entries,
            goal_stopped=False,
            source_events=provider_events,
        )
        == "terminal_pr_state"
    )


def test_resolve_origin_request_identity_rejects_missing_corrupt_and_recipient(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="origin_request_not_found"):
        _resolve_origin_request_identity(tmp_path, "req123")

    entries_dir = tmp_path / ".local" / "manager-context" / "entries" / "goal-1"
    entries_dir.mkdir(parents=True)
    entry_file = entries_dir / "req123.json"
    entry_file.write_text(
        json.dumps({"recipient": {"agent_id": "other"}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="origin_request_unsupported_target"):
        _resolve_origin_request_identity(tmp_path, "req123")

    entry_file.write_text(
        json.dumps(
            {
                "goal_id": "origin-goal",
                "agent_id": "origin-agent",
                "goal_ref": {"goal_id": "origin-goal", "goal_instance_id": "inst-1"},
            }
        ),
        encoding="utf-8",
    )
    identity = _resolve_origin_request_identity(tmp_path, "req123")
    assert identity == {
        "goal_id": "origin-goal",
        "agent_id": "origin-agent",
        "goal_ref": {"goal_id": "origin-goal", "goal_instance_id": "inst-1"},
    }
