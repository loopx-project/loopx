"""Refs #4447: one definition for the issue-fix metrics supplement vocabulary.

`_SUPPLEMENT_FIELDS` (19 counter names) and `SUPPLEMENT_SCHEMA_VERSION` were each
defined twice with identical values: once in `metrics_supplement`, which builds the
document and validates its schema, and once in `metrics_projection`, which reads that
document and rejects a supplement whose schema name differs. The projection's copy was
therefore load-bearing in both directions — it decides which fields are rendered *and*
which document shape is accepted — while the supplement decided the same two things from
its own literals.

`metrics_supplement` is the owner: `repository_snapshot` already imports
`SUPPLEMENT_SCHEMA_VERSION` from it instead of restating it, so this only extends a
direction the package already uses.

What this deliberately does not merge: `PROJECTION_SCHEMA_VERSION` names the
projection's own document and reads `issue_fix_metrics_projection_v0`, while the
supplement module's `PROJECTION_SCHEMA_VERSION` names the *supplement's* projection and
reads `issue_fix_metrics_supplement_projection_v0`. Same name, two different documents,
so collapsing them would change a wire value.
"""

from __future__ import annotations

import ast
import inspect

from loopx.capabilities.issue_fix import metrics_projection, metrics_supplement

SHARED_NAMES = frozenset({"_SUPPLEMENT_FIELDS", "SUPPLEMENT_SCHEMA_VERSION"})
EXPECTED_FIELDS = (
    "human_interventions",
    "automatic_terminal_closeouts",
    "duplicate_external_writes",
    "loopx_capability_gaps_found",
    "loopx_capability_gaps_fixed",
    "loopx_capability_gaps_real_callsite_verified",
    "memory_retrievals",
    "memory_verified_decision_influence",
    "memory_verified_patch_influence",
    "memory_stale_results",
    "useful_public_comments",
    "triage_outcomes",
    "issues_screened",
    "issue_close_recommendations",
    "issue_close_requests_published",
    "issue_closes_observed",
    "issue_reopens_observed",
    "first_push_ci_passed",
    "first_push_ci_total",
)
EXPECTED_SUPPLEMENT_SCHEMA = "issue_fix_metrics_supplement_v0"


def test_vocabulary_is_unchanged() -> None:
    """Merging the copies must not change a value or a position."""
    # Order is part of the meaning: the projection renders these names as rows.
    assert metrics_supplement._SUPPLEMENT_FIELDS == EXPECTED_FIELDS
    assert list(metrics_supplement._SUPPLEMENT_FIELDS) == list(EXPECTED_FIELDS)
    assert metrics_supplement.SUPPLEMENT_SCHEMA_VERSION == EXPECTED_SUPPLEMENT_SCHEMA


def test_the_projection_reads_the_owner_values() -> None:
    """The projection still accepts and renders exactly what it did before."""
    assert (
        metrics_projection._SUPPLEMENT_FIELDS is metrics_supplement._SUPPLEMENT_FIELDS
    )
    assert (
        metrics_projection.SUPPLEMENT_SCHEMA_VERSION
        == metrics_supplement.SUPPLEMENT_SCHEMA_VERSION
        == EXPECTED_SUPPLEMENT_SCHEMA
    )


def test_the_two_projection_schema_names_stay_separate() -> None:
    """Named the same, owned by different documents: merging would change a wire value."""
    assert (
        metrics_projection.PROJECTION_SCHEMA_VERSION
        == "issue_fix_metrics_projection_v0"
    )
    assert (
        metrics_supplement.PROJECTION_SCHEMA_VERSION
        == "issue_fix_metrics_supplement_projection_v0"
    )


def test_the_projection_defines_no_second_copy() -> None:
    """A module-level re-assignment here would fork the vocabulary again."""
    tree = ast.parse(inspect.getsource(metrics_projection))
    bound = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert not bound & SHARED_NAMES, sorted(bound & SHARED_NAMES)
