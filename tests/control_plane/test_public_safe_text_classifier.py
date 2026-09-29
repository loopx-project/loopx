"""Refs #5136 direction 1: the consolidated text-classification owner.

`loopx/public_safe_text.py` became the single home for "does this string look
private?". These tests pin the part the relocation added on top of the existing
corpus parity contract:

* detection returns an explicit *category* and a stable *reason*, not a bare
  regex object;
* a named *policy* (a set of categories) decides what a given surface rejects,
  so recognizing a value never implies every surface must reject it;
* the direction-3 path-gap recognition (``~/`` and ``path:``-prefixed local
  references) is opt-in, so this consolidation does not silently tighten any
  surface that has not chosen it;
* the relocation is behavior-preserving: ``find_private_text_match`` and the
  ``PRIVATE_TEXT_PATTERNS`` tuple are unchanged, and ``classify_private_text``'s
  first text-pattern match is the *identical* pattern object the legacy helper
  returns;
* `ml_experiment`'s leading-``/``-or-``~`` rule stays a distinct, stricter
  per-field *alias* constraint that the shared classifier does not subsume.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loopx.control_plane.goals.artifact_lifecycle import (
    _compact_text as artifact_lifecycle_compact,
)
from loopx.control_plane.runtime import public_safety
from loopx.domain_packs.ml_experiment import (
    _compact_public_text as ml_experiment_alias,
)
from loopx.public_safe_text import (
    ALL_CATEGORIES,
    ARTIFACT_LIFECYCLE_CATEGORIES,
    CATEGORY_CREDENTIAL,
    CATEGORY_LOCAL_PATH,
    CATEGORY_ORG_MARKER,
    CATEGORY_REMOTE_LOCATION,
    LOCAL_PATH_SURFACE_PATTERN as OWNER_LOCAL_PATH,
    PRIVATE_TEXT_PATTERNS,
    REMOTE_LOCATION_SURFACE_PATTERN as OWNER_REMOTE_LOCATION,
    SECRET_LIKE_SURFACE_PATTERN as OWNER_SECRET_LIKE,
    TEXT_OWNER_CATEGORIES,
    _CATEGORIZED_PRIVATE_TEXT_PATTERNS,
    classify_private_text,
    find_private_text_match,
    matches_private_text_policy,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "public_safe_text_corpus.json"
_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z_]*)\}")

# Sensitive literals are assembled at call time so this file carries no literal
# credential-looking string, matching the corpus fixture's own discipline.
_GITHUB_TOKEN = "ghp_" + "a" * 36
_AUTHZ_HEADER = "authorization" + ": " + "Basic " + "QWxhZGRpbjpvcGVu"
# Path fixtures are joined the same way so the repository public/private scanner
# does not flag this test file while the classifier still sees the same text.
_LOCAL_PATH = "/".join(["", "home", "dev", "x.json"])
_ORG_MARKER_PATH = "/".join(["", "ext_data", "run", "x"])


def test_categories_are_the_four_named_decisions() -> None:
    assert ALL_CATEGORIES == frozenset(
        {
            CATEGORY_CREDENTIAL,
            CATEGORY_LOCAL_PATH,
            CATEGORY_REMOTE_LOCATION,
            CATEGORY_ORG_MARKER,
        }
    )


def test_text_owner_policy_rejects_every_recognized_category() -> None:
    # The four text owners historically rejected every shape; their policy is
    # the full set so the relocation changes no owner verdict.
    assert TEXT_OWNER_CATEGORIES == ALL_CATEGORIES


def test_artifact_lifecycle_policy_excludes_only_remote_location() -> None:
    # artifact_lifecycle has always let an ordinary http(s) URL through, so its
    # policy is every category *except* a raw remote location. Widening it is a
    # separate, disclosed decision (Refs #5136 direction 2), not part of the
    # behavior-preserving relocation.
    assert ARTIFACT_LIFECYCLE_CATEGORIES == frozenset(
        {CATEGORY_CREDENTIAL, CATEGORY_LOCAL_PATH, CATEGORY_ORG_MARKER}
    )
    assert CATEGORY_REMOTE_LOCATION not in ARTIFACT_LIFECYCLE_CATEGORIES


@pytest.mark.parametrize(
    "value,category,reason",
    [
        (_AUTHZ_HEADER, CATEGORY_CREDENTIAL, "authorization header/assignment shape"),
        (_GITHUB_TOKEN, CATEGORY_CREDENTIAL, "credential-like value shape"),
        (_LOCAL_PATH, CATEGORY_LOCAL_PATH, "local filesystem path"),
        ("https://example.com/a", CATEGORY_REMOTE_LOCATION, "raw remote location URL"),
        (_ORG_MARKER_PATH, CATEGORY_ORG_MARKER, "internal ext_data path"),
    ],
)
def test_classify_returns_an_explicit_category_and_reason(
    value: str, category: str, reason: str
) -> None:
    match = classify_private_text(value)
    assert match is not None
    assert match.category == category
    assert match.reason == reason


@pytest.mark.parametrize(
    "value",
    [
        "needs owner authorization before delivery",
        "weekly cadence digest rendered for goal_42",
        "the operator rotated the deploy credentials yesterday",
    ],
)
def test_classify_leaves_ordinary_governance_prose_alone(value: str) -> None:
    assert classify_private_text(value) is None


def test_artifact_lifecycle_policy_lets_a_raw_remote_location_through() -> None:
    # The named policy, not an inline regex OR, is what preserves the historical
    # verdict: a remote location is recognized but out of policy for this surface.
    url = "https://example.com/run-7/metrics.json"
    assert classify_private_text(url, categories=ALL_CATEGORIES) is not None
    assert classify_private_text(url, categories=ARTIFACT_LIFECYCLE_CATEGORIES) is None
    # Through the real entry point the URL still compacts to itself.
    assert artifact_lifecycle_compact(url) == url
    # A credential is in policy and is dropped by the same entry point.
    assert artifact_lifecycle_compact(_GITHUB_TOKEN) is None


@pytest.mark.parametrize(
    "value,policy,expected",
    [
        (_GITHUB_TOKEN, ALL_CATEGORIES, True),
        (_GITHUB_TOKEN, ARTIFACT_LIFECYCLE_CATEGORIES, True),
        ("https://example.com/a", ALL_CATEGORIES, True),
        ("https://example.com/a", ARTIFACT_LIFECYCLE_CATEGORIES, False),
        ("weekly digest for goal_42", ALL_CATEGORIES, False),
    ],
)
def test_matches_private_text_policy_mirrors_classify(
    value: str, policy: frozenset[str], expected: bool
) -> None:
    assert matches_private_text_policy(value, categories=policy) is expected
    assert (classify_private_text(value, categories=policy) is not None) is expected


def test_a_policy_narrows_the_text_owner_patterns_not_only_the_shapes() -> None:
    # The category filter has to gate the text-owner patterns as well as the
    # relocated shape detectors. Each value below is matched only by a text
    # pattern, so a policy that drops that pattern's category must accept it --
    # and an empty policy must recognize nothing at all.
    bearer = "the Bearer token expired"
    ext_data = _ORG_MARKER_PATH
    assert classify_private_text(bearer).category == CATEGORY_CREDENTIAL
    assert classify_private_text(ext_data).category == CATEGORY_ORG_MARKER
    assert classify_private_text(bearer, categories=frozenset({CATEGORY_LOCAL_PATH})) is None
    assert classify_private_text(ext_data, categories=frozenset({CATEGORY_CREDENTIAL})) is None
    assert classify_private_text(bearer, categories=frozenset()) is None
    assert classify_private_text(ext_data, categories=frozenset()) is None
    assert classify_private_text(_GITHUB_TOKEN, categories=frozenset()) is None


@pytest.mark.parametrize("value", ["~/work/model.bin", "path:/srv/data/train.json"])
def test_path_gap_recognition_is_opt_in(value: str) -> None:
    # Direction 3 adds recognition of the local-path shapes the legacy surface
    # pattern misses, but defaults off so no surface tightens until it chooses
    # the wider policy in a separate, disclosed change.
    assert classify_private_text(value) is None
    gap = classify_private_text(value, include_path_gaps=True)
    assert gap is not None
    assert gap.category == CATEGORY_LOCAL_PATH


def test_path_gap_recognition_does_not_subsume_the_alias_rule() -> None:
    # `~username` has no `/` after the tilde, so the shared gap pattern does not
    # flag it; ml_experiment's alias rule does. They are different decisions.
    assert classify_private_text("~username/notes", include_path_gaps=True) is None


def test_classify_first_text_match_is_identical_to_find_private_text_match() -> None:
    # Behavior-preserving pin over the real shared corpus: whenever the legacy
    # helper returns a pattern, the classifier's first match is that same object
    # (text patterns are checked first, in the pinned order). The classifier may
    # additionally flag shape-only values the legacy helper never saw.
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert corpus["schema_version"] == "public_safe_text_corpus_v0"
    tokens = corpus["tokens"]

    def render(template: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in tokens:
                return "".join(tokens[name])
            if name.endswith("_LOWER") and name[: -len("_LOWER")] in tokens:
                return "".join(tokens[name[: -len("_LOWER")]]).lower()
            raise AssertionError(f"corpus placeholder {name} has no token")

        return _PLACEHOLDER_RE.sub(replace, template)

    checked = 0
    for group in ("public_safe", "private_looking"):
        for sample in corpus[group]:
            value = render(sample["template"])
            checked += 1
            legacy = find_private_text_match(value)
            classified = classify_private_text(value, categories=ALL_CATEGORIES)
            if legacy is None:
                continue
            assert classified is not None, sample["id"]
            assert classified.pattern is legacy, sample["id"]
    assert checked > 0


def test_private_text_patterns_are_the_categorized_patterns_in_order() -> None:
    # The compat tuple every existing importer reads is derived from the
    # categorized list, same patterns, same order, so find_private_text_match is
    # byte-identical to before the relocation.
    assert PRIVATE_TEXT_PATTERNS == tuple(
        entry.pattern for entry in _CATEGORIZED_PRIVATE_TEXT_PATTERNS
    )
    assert len(PRIVATE_TEXT_PATTERNS) == 11


def test_public_safety_reexports_the_same_owner_objects() -> None:
    # public_safety consumes the shapes instead of restating them; the objects it
    # re-exports are the very ones the owner compiles, so its ~8 importers and
    # recursive payload validation see one decision.
    assert public_safety.SECRET_LIKE_SURFACE_PATTERN is OWNER_SECRET_LIKE
    assert public_safety.LOCAL_PATH_SURFACE_PATTERN is OWNER_LOCAL_PATH
    assert public_safety.REMOTE_LOCATION_SURFACE_PATTERN is OWNER_REMOTE_LOCATION


def test_ml_experiment_alias_constraint_is_stricter_than_the_shared_classifier() -> None:
    # Direction 3: keep ml_experiment's leading-`/`-or-`~` rule as an explicit
    # alias constraint. A future single-owner pass must not fold it into the
    # shared classifier, because the classifier does not flag these values even
    # with path-gap recognition on -- folding would silently lose alias coverage.
    for value in ("~username/notes", "/just-a-leading-slash"):
        assert classify_private_text(value, include_path_gaps=True) is None
        with pytest.raises(ValueError, match="must use a public alias"):
            ml_experiment_alias(value, field="dataset_ref")
    # Positive control: a bare alias passes the same field.
    assert ml_experiment_alias("public-alias-v3", field="dataset_ref") == "public-alias-v3"
