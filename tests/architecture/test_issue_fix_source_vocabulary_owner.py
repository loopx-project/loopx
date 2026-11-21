"""The issue_fix source trust/freshness vocabularies are stated once.

`repository_context.py` owns the closed sets every issue_fix source payload is
validated against. `reviewer_recommendation.py` used to restate both under its
own names, so a vocabulary change had two places to land and a drift would let
the two validators disagree about the same `trust`/`freshness` fields.

`REVIEWER_SOURCE_KINDS` stays local on purpose: reviewer sources accept only
`maintainer_map`, a deliberately narrower set than the repository-context one.
The same freshness triple also appears in `reward_memory/candidate_review.py`,
a different capability outside this guard's scope.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix import repository_context, reviewer_recommendation

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/capabilities/issue_fix/repository_context.py"

VOCABULARIES = {
    "TRUST_LEVELS": repository_context.TRUST_LEVELS,
    "FRESHNESS_STATES": repository_context.FRESHNESS_STATES,
}


def _string_set(node: ast.expr) -> frozenset[str] | None:
    if not isinstance(node, ast.Set):
        return None
    values = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        values.append(element.value)
    return frozenset(values)


def test_the_vocabularies_are_stated_only_by_the_owner() -> None:
    # A census by value, not by name: a renamed local copy is the same drift.
    targets = {frozenset(values) for values in VOCABULARIES.values()}
    found: dict[str, list[str]] = {}
    package = REPOSITORY_ROOT / "loopx/capabilities/issue_fix"
    for path in sorted(package.glob("*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if _string_set(node.value) in targets:
                found.setdefault(relative, []).append(target.id)
    assert {f: sorted(names) for f, names in found.items()} == {
        OWNER_MODULE: sorted(VOCABULARIES)
    }


def test_reviewer_sources_read_the_owner_objects() -> None:
    assert (
        reviewer_recommendation.REVIEWER_SOURCE_TRUST_LEVELS
        is repository_context.TRUST_LEVELS
    )
    assert (
        reviewer_recommendation.REVIEWER_SOURCE_FRESHNESS_STATES
        is repository_context.FRESHNESS_STATES
    )


def _context_input(*, trust: str, freshness: str) -> dict:
    return {
        "schema_version": "issue_fix_repository_context_input_v0",
        "repository_revision": "abc123",
        "sources": [
            {
                "source_id": "agents-map",
                "source_kind": "maintainer_map",
                "reference": "AGENTS.md",
                "trust": trust,
                "freshness": freshness,
                "supports": ["change_scope"],
            }
        ],
    }


def _reviewer_input(*, trust: str, freshness: str) -> dict:
    return {
        "schema_version": "issue_fix_reviewer_sources_input_v0",
        "sources": [
            {
                "source_id": "codeowners-map",
                "source_kind": "maintainer_map",
                "reference": ".github/CODEOWNERS",
                "trust": trust,
                "freshness": freshness,
                "observed_at": "2026-11-15T00:00:00Z",
                "routes": [
                    {
                        "route_id": "core-route",
                        "match_kind": "path_prefix",
                        "pattern": "loopx/",
                        "primary_reviewers": ["@maintainer"],
                        "fallback_reviewers": [],
                    }
                ],
            }
        ],
    }


def test_repository_context_accepts_every_owner_value() -> None:
    for trust in sorted(repository_context.TRUST_LEVELS):
        for freshness in sorted(repository_context.FRESHNESS_STATES):
            packet = repository_context.build_issue_fix_repository_context_packet(
                repo="loopx",
                issue_ref="issue-1",
                context_input=_context_input(trust=trust, freshness=freshness),
            )
            assert packet["ok"] is True
            assert packet["sources"][0]["trust"] == trust
            assert packet["sources"][0]["freshness"] == freshness


def test_reviewer_sources_accept_every_owner_value() -> None:
    for trust in sorted(repository_context.TRUST_LEVELS):
        for freshness in sorted(repository_context.FRESHNESS_STATES):
            packet = (
                reviewer_recommendation.build_issue_fix_reviewer_recommendation_packet(
                    repo_path="/nonexistent",
                    reviewer_sources_input=_reviewer_input(
                        trust=trust, freshness=freshness
                    ),
                )
            )
            assert packet["ok"] is True
            assert packet["reviewer_source_count"] == 1


@pytest.mark.parametrize("field", ["trust", "freshness"])
def test_repository_context_rejects_values_outside_the_owner_set(field: str) -> None:
    payload = _context_input(trust="verified", freshness="stale")
    payload["sources"][0][field] = "bogus"
    with pytest.raises(ValueError, match=field):
        repository_context.build_issue_fix_repository_context_packet(
            repo="loopx",
            issue_ref="issue-1",
            context_input=payload,
        )


@pytest.mark.parametrize("field", ["trust", "freshness"])
def test_reviewer_sources_reject_values_outside_the_owner_set(field: str) -> None:
    payload = _reviewer_input(trust="verified", freshness="stale")
    payload["sources"][0][field] = "bogus"
    with pytest.raises(ValueError, match=field):
        reviewer_recommendation.build_issue_fix_reviewer_recommendation_packet(
            repo_path="/nonexistent",
            reviewer_sources_input=payload,
        )
