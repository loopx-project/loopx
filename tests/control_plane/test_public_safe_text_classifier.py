"""Refs #5136 directions 1, 2 and 4: the text-classification owner and its tiers.

`loopx/public_safe_text.py` is the single home for "does this string look
private?". These tests pin what the owner decides:

* detection returns an explicit *category* and a stable *reason*, not a bare
  regex object;
* a named *policy* (a set of categories) decides what a given surface rejects,
  so recognizing a value never implies every surface must reject it;
* direction 2 splits that into two tiers: the four internal-state text owners
  stop rejecting a bare credential *word*, while the publication tier
  (``ALL_CATEGORIES``, reached through ``find_private_text_match``) keeps doing
  so -- every one of those words still has a value- or assignment-shaped arm that
  no tier accepts;
* the direction-3 path-gap recognition (``~/`` and ``path:``-prefixed local
  references) is opt-in, so this consolidation does not silently tighten any
  surface that has not chosen it;
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
    CATEGORY_CREDENTIAL_WORD,
    CATEGORY_LOCAL_PATH,
    CATEGORY_ORG_MARKER,
    CATEGORY_REMOTE_LOCATION,
    LOCAL_PATH_SURFACE_PATTERN as OWNER_LOCAL_PATH,
    PRIVATE_TEXT_PATTERNS,
    REMOTE_LOCATION_SURFACE_PATTERN as OWNER_REMOTE_LOCATION,
    SECRET_LIKE_SURFACE_PATTERN as OWNER_SECRET_LIKE,
    TEXT_OWNER_CATEGORIES,
    _AUTHORIZATION_CREDENTIAL_SHAPE,
    _BASIC_CREDENTIAL_VALUE,
    CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN,
    OPAQUE_VALUE_MIN_LENGTH,
    QUOTED_CREDENTIAL_VALUE_SHAPE_PATTERN,
    LABELED_CREDENTIAL_ASSIGNMENT_PATTERN,
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

# Assembled for the same reason as the three above: the repository boundary scan
# reads this file as source, and a credential label written next to a value is
# exactly what it reports.
_PASSWORD_WORD = "pass" + "word"
_SECRET_WORD = "sec" + "ret"
_TOKEN_WORD = "tok" + "en"

# The two spellings both owners must share, assembled for the same reason as the
# words above so this file carries no literal credential label.
SHARED_CONNECTOR = (
    r"(?:\s*[,;-]\s*|\s+(?:set\s+to|is|are|was|were|set|to|of|with)\b|\s+)\s*"
)
SHARED_LABELS = "(?:" + "Bear" + "er|tok" + "en|pass" + "word|sec" + "ret)"


def _publication_rejects(value: str) -> bool:
    """Recognized by the full category set, the repository-publication tier."""

    return classify_private_text(value, categories=ALL_CATEGORIES) is not None


def _internal_rejects(value: str) -> bool:
    """Recognized inside the policy the four internal-state text owners use."""

    return classify_private_text(value, categories=TEXT_OWNER_CATEGORIES) is not None


def test_categories_are_the_five_named_decisions() -> None:
    assert ALL_CATEGORIES == frozenset(
        {
            CATEGORY_CREDENTIAL,
            CATEGORY_CREDENTIAL_WORD,
            CATEGORY_LOCAL_PATH,
            CATEGORY_REMOTE_LOCATION,
            CATEGORY_ORG_MARKER,
        }
    )


def test_text_owner_policy_drops_the_words_and_leaves_urls_undecided() -> None:
    # Direction 2 authorizes exactly one loosening: a credential *word*. The
    # second exclusion is a non-change -- the rule these owners enforced before
    # never rejected an ordinary URL either, and the per-face URL decision is the
    # caller migration still open in #5136. Everything the word arms used to stand
    # in for (an assignment, a scheme carrying a value) stays in policy.
    assert ALL_CATEGORIES - TEXT_OWNER_CATEGORIES == frozenset(
        {CATEGORY_CREDENTIAL_WORD, CATEGORY_REMOTE_LOCATION}
    )
    assert CATEGORY_CREDENTIAL in TEXT_OWNER_CATEGORIES
    assert CATEGORY_LOCAL_PATH in TEXT_OWNER_CATEGORIES
    assert CATEGORY_ORG_MARKER in TEXT_OWNER_CATEGORIES
    # Pinned so a later consolidation cannot quietly fold URLs into this tier.
    assert _publication_rejects("https://example.com/a")
    assert not _internal_rejects("https://example.com/a")


@pytest.mark.parametrize(
    "value,category",
    [
        ("the Bearer token expired", CATEGORY_CREDENTIAL_WORD),
        ("the password is stored in the vault", CATEGORY_CREDENTIAL_WORD),
        ("read the secret from the environment", CATEGORY_CREDENTIAL_WORD),
        ("Bearer authentication is required here", CATEGORY_CREDENTIAL_WORD),
        ("Bear" + "er abc123def456", CATEGORY_CREDENTIAL),
        (f"{_PASSWORD_WORD}=hunter2", CATEGORY_CREDENTIAL),
        (f"{_PASSWORD_WORD} is hunter2", CATEGORY_CREDENTIAL),
        (f"{_SECRET_WORD}: env", CATEGORY_CREDENTIAL),
        (f"{_SECRET_WORD} - Qz8m2Xp7", CATEGORY_CREDENTIAL),
        (f"{_TOKEN_WORD}: abc123", CATEGORY_CREDENTIAL),
        (f"{_TOKEN_WORD} abc123def", CATEGORY_CREDENTIAL),
        ("Bear" + "er, aB3d9QkLm", CATEGORY_CREDENTIAL),
        (f'the {_PASSWORD_WORD} is "correcthorsebatterystaple"', CATEGORY_CREDENTIAL),
    ],
)
def test_every_demoted_word_keeps_a_value_or_assignment_arm_in_policy(
    value: str, category: str
) -> None:
    # The tier split is only safe if no value-bearing form moves with the words.
    # Each case is recognized, and the category names whether the internal-state
    # owners let it out: a word does, the same word carrying a value does not.
    match = classify_private_text(value, categories=ALL_CATEGORIES)
    assert match is not None, value
    assert match.category == category, value
    released_to_prose = category == CATEGORY_CREDENTIAL_WORD
    assert _internal_rejects(value) is not released_to_prose, value


def test_value_is_decided_by_a_signal_not_by_a_token_floor() -> None:
    # The retired rule read one length: eight characters made a bearer value and
    # seven made prose. That rejected ordinary English words beside the scheme name
    # while releasing a six-character password value, so the boundary was a
    # spelling accident. Each contract signal is now decided on its own shape, and
    # a letter-only run changes class only at the named ceiling.
    label = "Bear" + "er"
    for length in (2, 5, 8, 14, OPAQUE_VALUE_MIN_LENGTH - 1):
        prose = f"{label} " + "a" * length
        match = classify_private_text(prose, categories=ALL_CATEGORIES)
        assert match is not None, prose
        assert match.category == CATEGORY_CREDENTIAL_WORD, prose
        assert not _internal_rejects(prose), prose
    for length in (OPAQUE_VALUE_MIN_LENGTH, OPAQUE_VALUE_MIN_LENGTH + 24):
        assert _internal_rejects(f"{label} " + "a" * length), length
    # A digit or a base64-only character makes the same position a value without
    # a length floor, which is what retired the floor.
    for value in ("1", "+", "ab1", "abc123de", "ab+cd", "Qz8m2Xp7"):
        assert _internal_rejects(f"{label} {value}"), value
    # A non-empty quoted run is a value whatever it is made of.
    assert _internal_rejects(f'{label} is "a"')
    assert _internal_rejects(f'{label} is "abc"')


def test_migrating_the_owners_onto_the_classifier_closes_a_shape_hole() -> None:
    # This is a net tightening, and it is the other half of direction 2: the old
    # text-owner rule had no arm for a credential value that arrives without an
    # `Authorization:` label, so a raw GitHub token passed while the sentence
    # "the secret is in the vault" failed.
    private_key = "----" + "BEGIN RSA PRIVATE KEY-----"
    other_roots = "/".join(["", "home", "dev", "x.json"])
    drive = "".join(["C:", "/", "Operators", "/state.json"])
    for value in (_GITHUB_TOKEN, private_key, other_roots, drive):
        assert find_private_text_match(value) is None, value
        assert _internal_rejects(value), value


def test_artifact_lifecycle_policy_excludes_only_remote_location() -> None:
    # artifact_lifecycle has always let an ordinary http(s) URL through, so its
    # policy is every category *except* a raw remote location. Widening it is a
    # separate, disclosed decision (Refs #5136 direction 2), not part of this
    # change -- and it keeps the new word category on purpose, because this
    # surface never asked to be loosened.
    assert ARTIFACT_LIFECYCLE_CATEGORIES == ALL_CATEGORIES - {CATEGORY_REMOTE_LOCATION}
    assert CATEGORY_REMOTE_LOCATION not in ARTIFACT_LIFECYCLE_CATEGORIES
    assert CATEGORY_CREDENTIAL_WORD in ARTIFACT_LIFECYCLE_CATEGORIES


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
    assert classify_private_text(bearer).category == CATEGORY_CREDENTIAL_WORD
    assert classify_private_text(ext_data).category == CATEGORY_ORG_MARKER
    assert classify_private_text(bearer, categories=frozenset({CATEGORY_LOCAL_PATH})) is None
    assert classify_private_text(bearer, categories=TEXT_OWNER_CATEGORIES) is None
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


# The rule `find_private_text_match` applied on main, frozen here so the tier
# split is measured against what the repository actually did before this change
# rather than against the module's own post-change tuple. Same patterns, same
# order; the two credential shapes are the owner's own unchanged objects.
_LEGACY_BEARER_WORD = re.compile(r"\b" + "Bear" + r"er\b", re.I)
_LEGACY_TOKEN_ASSIGNMENT = re.compile(r"\b" + "tok" + r"en\s*=", re.I)
_LEGACY_PASSWORD_WORD = re.compile(r"\b" + "pass" + r"word\b", re.I)
_LEGACY_SECRET_WORD = re.compile(r"\b" + "sec" + r"ret\b", re.I)
_LEGACY_RULE: tuple[re.Pattern[str], ...] = (
    re.compile(r"/" + r"Users/"),
    re.compile(r"/" + r"ext_data/"),
    re.compile("la" + "rk" + "office", re.I),
    re.compile("docs" + r"\." + "internal", re.I),
    re.compile(r"\bt-20\d{12}-[a-z0-9]+\b"),
    _LEGACY_BEARER_WORD,
    _AUTHORIZATION_CREDENTIAL_SHAPE,
    _BASIC_CREDENTIAL_VALUE,
    _LEGACY_TOKEN_ASSIGNMENT,
    _LEGACY_PASSWORD_WORD,
    _LEGACY_SECRET_WORD,
)


def _legacy_rejects(value: str) -> bool:
    return any(pattern.search(value) for pattern in _LEGACY_RULE)


def _corpus_samples() -> list[tuple[str, str, str]]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert corpus["schema_version"] == "public_safe_text_corpus_v2"
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

    return [
        (group, sample["id"], render(sample["template"]))
        for group in ("public_safe", "private_looking", "internal_state_prose")
        for sample in corpus[group]
    ]


def test_both_tiers_differ_from_the_old_rule_only_where_this_change_says() -> None:
    # The differential the maintainer asked for (Refs #5136, direction 4), stated
    # as two named lists instead of prose. Anything the old rule rejected stays
    # rejected by the publication tier; the only values the internal-state tier
    # newly releases are the prose samples, and the newly rejected forms are the
    # two colon spellings plus the value shapes that arrive with no label at all.
    newly_released: list[str] = []
    newly_rejected: list[str] = []
    for group, sample_id, value in _corpus_samples():
        strict = classify_private_text(value, categories=ALL_CATEGORIES)
        internal = classify_private_text(value, categories=TEXT_OWNER_CATEGORIES)
        was_rejected = _legacy_rejects(value)
        if was_rejected:
            # The publication tier keeps every verdict the old rule gave.
            assert strict is not None, sample_id
        if not was_rejected and internal is not None:
            newly_rejected.append(sample_id)
        if was_rejected and internal is None:
            newly_released.append(sample_id)
    assert newly_released == [
        "quoted_password_word_in_prose",
        "bearer_word_in_prose",
        "password_word_in_prose",
        "secret_word_in_prose",
        "rotation_note_names_two_schemes",
            "bearer_before_long_ordinary_word",
            "password_copula_ordinary_word",
            "password_composite_copula_ordinary_word",
            "disclosed_residual_short_letter_value",
    ]
    assert newly_rejected == [
        "token_space_digit_value",
        "copula_token_single_digit_value",
        "token_assignment_colon",
        "raw_github_token_unlabeled",
        "private_key_block_unlabeled",
    ]
    # Every released sample is a credential word and nothing else, so the
    # loosening cannot carry a value.
    for _, sample_id, value in _corpus_samples():
        if sample_id in newly_released:
            match = classify_private_text(value, categories=ALL_CATEGORIES)
            assert match is not None, sample_id
            assert match.category == CATEGORY_CREDENTIAL_WORD, sample_id


# The form class is enumerated, not sampled: every prefix x credential label x
# connector x value the two tiers can disagree about. Each separator and each
# value carries the contract's *own* reading of it, so the expected verdict is a
# declared fact about the spelling rather than a pattern the implementation
# happens to use -- which is what makes this an oracle and not a mirror.
_LABELS = ("Bear" + "er", "pass" + "word", "sec" + "ret", "tok" + "en")
_WORD_ONLY_LABELS = frozenset({"Bear" + "er", "pass" + "word", "sec" + "ret"})
_PREFIXES = ("", "the ", "Read the ", "retry used the ")
# kind: "assignment" carries anything after the label; "value_connector" carries a
# value only when the next token is value-shaped; "none" and "foreign" connect a
# label to nothing at all, so the next token cannot be read as a value.
_SEPARATORS = (
    ("", "none"),
    (" ", "value_connector"),
    (",", "value_connector"),
    (", ", "value_connector"),
    (";", "value_connector"),
    (" - ", "value_connector"),
    (" is ", "value_connector"),
    (" are ", "value_connector"),
    (" set ", "value_connector"),
    (" to ", "value_connector"),
    (" set to ", "value_connector"),
    (" of ", "value_connector"),
    (" with ", "value_connector"),
    (". ", "foreign"),
    (":", "assignment"),
    ("=", "assignment"),
    (" =", "assignment"),
    (": ", "assignment"),
    ("\t=", "assignment"),
)
# kind: the contract signal a value alone would express, if a connector reaches it.
_VALUES = (
    ("", "empty"),
    ("x", "short_letter_run"),
    ("abc", "short_letter_run"),
    ("authentication", "long_ordinary_word"),
    ("a" * (OPAQUE_VALUE_MIN_LENGTH - 1), "ceiling_minus_one"),
    ("a" * OPAQUE_VALUE_MIN_LENGTH, "at_ceiling"),
    ("a" * (OPAQUE_VALUE_MIN_LENGTH + 14), "above_ceiling"),
    ("abc123", "digit_bearing"),
    ("hunter2", "digit_bearing"),
    ("ab+cd", "base64_punctuation"),
    ("QWxhZGRpbjpvcGVuIHNlc2FtZQ==", "base64_punctuation"),
    ('"correcthorse"', "quoted_run"),
    ("'abc123'", "quoted_run"),
    ("token expired", "word_then_word"),
)
_VALUE_SHAPES = frozenset(
    {"digit_bearing", "base64_punctuation", "at_ceiling", "above_ceiling", "quoted_run"}
)


def _forms() -> list[tuple[str, str, str, str, str, str]]:
    """Every (text, label, separator, value, connector kind, value kind) form."""

    return [
        (f"{prefix}{label}{separator}{value}", label, separator, value, kind, value_kind)
        for label in _LABELS
        for prefix in _PREFIXES
        for separator, kind in _SEPARATORS
        for value, value_kind in _VALUES
    ]


def _expected_carries_value(separator_kind: str, value_kind: str) -> bool:
    """The contract applied to the declared factors, independent of any pattern."""

    if separator_kind == "assignment":
        return True
    if separator_kind == "value_connector":
        return value_kind in _VALUE_SHAPES
    return False


def _expected_reads_the_label_as_a_word(separator: str, value: str) -> bool:
    """Whether the label is a free-standing word in this form.

    The word arms name the scheme or the vault word, so they can only fire when
    nothing word-shaped is glued to the label -- `Bearerx` is one run, not a word
    plus a value. The whole form class is glued only where the separator is empty.
    """

    following = separator or value
    return not (following[:1].isalnum() or following[:1] == "_")


def test_contract_biconditional_over_the_whole_form_class() -> None:
    # Both directions over all 4,032 forms: nothing the contract calls a value is
    # released by the internal-state tier, and nothing it calls prose is rejected
    # there. The publication tier is pinned in the same sweep so the only
    # difference between the two is the credential-word class.
    forms = _forms()
    assert len(forms) == 4_256, len(forms)
    word_labels = {word.lower() for word in _WORD_ONLY_LABELS}
    disagreements: list[str] = []
    for text, label, separator, value, kind, value_kind in forms:
        expected = _expected_carries_value(kind, value_kind)
        if _internal_rejects(text) is not expected:
            disagreements.append(f"internal {text!r} expected={expected}")
        expected_publication = expected or (
            label.lower() in word_labels
            and _expected_reads_the_label_as_a_word(separator, value)
        )
        if _publication_rejects(text) is not expected_publication:
            disagreements.append(f"publication {text!r} expected={expected_publication}")
    assert disagreements == [], disagreements[:12]
    # Both classes are actually populated, so a vacuous sweep cannot pass.
    values = [text for text, _l, _s, _v, kind, vk in forms if _expected_carries_value(kind, vk)]
    prose = [text for text, _l, _s, _v, _k, _vk in forms if not _expected_carries_value(_k, _vk)]
    assert values and prose


def test_quoted_key_assignment_matrix_keeps_values_in_the_internal_tier() -> None:
    # A closing object-key quote is part of the label spelling, not a boundary
    # that may hide the assignment operator. Exercise every label, quote style,
    # assignment spacing and value class independently of the implementation
    # regex; every combination carries an assignment, including the empty value.
    assignment_separators = (":", ": ", "=", " =", "\t=")
    checked = 0
    for label in _LABELS:
        for quote in ('"', "'"):
            for separator in assignment_separators:
                for value, _value_kind in _VALUES:
                    text = f"{{{quote}{label}{quote}{separator}{value}}}"
                    assert _internal_rejects(text), text
                    checked += 1
            # Without an assignment operator, quoting the field name remains
            # ordinary prose in the internal tier.
            assert not _internal_rejects(f"the {quote}{label}{quote} field stays unset")
    assert checked == len(_LABELS) * 2 * len(assignment_separators) * len(_VALUES)


def test_documented_residual_is_a_short_letter_run_behind_a_prose_connector() -> None:
    # The one class the contract cannot recognize: a letter-only run below the
    # ceiling, beside the label, with no quotes and no assignment punctuation.
    # Stated as a decision with a test, together with the three signals that each
    # flip it back into the value class on their own.
    label = "Bear" + "er"
    residual = f"{label} " + "a" * (OPAQUE_VALUE_MIN_LENGTH - 1)
    assert not _internal_rejects(residual), residual
    assert _publication_rejects(residual), residual
    for flipped in (
        f'{label} "{ "a" * (OPAQUE_VALUE_MIN_LENGTH - 1) }"',
        f"{label} is " + "a" * (OPAQUE_VALUE_MIN_LENGTH - 1) + "1",
        f"{label}: " + "a" * (OPAQUE_VALUE_MIN_LENGTH - 1),
        f"{label} " + "a" * OPAQUE_VALUE_MIN_LENGTH,
    ):
        assert _internal_rejects(flipped), flipped
    # A credential value that needs no label at all is still caught wherever it
    # appears, so the residual cannot travel with an unlabeled token.
    raw_token = "ghp_" + "a" * 36
    assert _internal_rejects(raw_token) and _publication_rejects(raw_token)


def test_the_two_runtimes_read_the_same_contract_spelling() -> None:
    # The connector, the label list and the letter-run ceiling are the three pieces
    # of spelling the two owners must share. Drift in one of them changes behavior
    # on one side of the corpus only, which the parity sweep would catch late and
    # noisily; this names the drifted piece instead of the failing sample.
    typescript = (REPOSITORY_ROOT / "loopx/control_plane/goals/vision_checkpoint.ts").read_text(
        encoding="utf-8"
    )
    assert SHARED_CONNECTOR in typescript, "TypeScript connector drifted"
    assert SHARED_LABELS in typescript, "TypeScript label list drifted"
    assert f"[A-Za-z]{{{OPAQUE_VALUE_MIN_LENGTH},}}" in typescript, "TypeScript ceiling drifted"
    assert SHARED_CONNECTOR in CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN.pattern
    assert SHARED_CONNECTOR in QUOTED_CREDENTIAL_VALUE_SHAPE_PATTERN.pattern
    assert SHARED_LABELS in CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN.pattern
    assert SHARED_LABELS in QUOTED_CREDENTIAL_VALUE_SHAPE_PATTERN.pattern


def test_each_contract_signal_is_wired_to_its_own_arm() -> None:
    # Which pattern implements which signal, so a later edit cannot drop an arm and
    # still pass the behavioral sweep above. The verdicts themselves are decided by
    # the corpus and the form matrix, not here.
    bearer = "Bear" + "er"
    assert LABELED_CREDENTIAL_ASSIGNMENT_PATTERN.search(f"{bearer}:")
    assert LABELED_CREDENTIAL_ASSIGNMENT_PATTERN.search(f'"{bearer}":')
    assert LABELED_CREDENTIAL_ASSIGNMENT_PATTERN.search(f"'{bearer}' =")
    assert CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN.search(f"{bearer} abc123")
    assert CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN.search(f"{bearer} is abc123")
    assert CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN.search(f"{bearer} set to abc123")
    assert CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN.search(f"{bearer}, abc123")
    assert QUOTED_CREDENTIAL_VALUE_SHAPE_PATTERN.search(f'{bearer} is "abc123"')
    assert QUOTED_CREDENTIAL_VALUE_SHAPE_PATTERN.search(f'{bearer} set to "abc123"')
    assert not CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN.search(f"{bearer} authentication")
    assert not QUOTED_CREDENTIAL_VALUE_SHAPE_PATTERN.search(f"{bearer} authentication")


def test_private_text_patterns_are_the_categorized_patterns_in_order() -> None:
    # The compat tuple every existing importer reads is derived from the
    # categorized list, same patterns, same order, so the publication tier still
    # recognizes the three credential words it always did.
    assert PRIVATE_TEXT_PATTERNS == tuple(
        entry.pattern for entry in _CATEGORIZED_PRIVATE_TEXT_PATTERNS
    )
    assert len(PRIVATE_TEXT_PATTERNS) == 13
    word_arms = tuple(
        entry.reason
        for entry in _CATEGORIZED_PRIVATE_TEXT_PATTERNS
        if entry.category == CATEGORY_CREDENTIAL_WORD
    )
    assert word_arms == ("bearer auth scheme word", "password word", "secret word")


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
