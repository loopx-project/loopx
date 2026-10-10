"""The credential question at the two capability faces is one named policy call.

`decision_context` packets and `material_lifecycle` validation each kept their own
alternation list beside the owner's shape detector, and the two lists were
byte-identical. Their comments claimed "local threshold policy only", but a list
of words and header shapes is a shape owner, not a threshold: it decided, at two
sites, which English spellings count as a credential.

This suite is the cover for the migration onto `classify_private_text` with the
named `CREDENTIAL_CATEGORIES` policy plus the owner's
`include_compound_field_assignment` opt-in. It answers the three questions a
reviewer of a consolidation PR asks: what still cannot pass, what now can, and
what the new arm in the owner is actually for.

Every "rejects" case below goes through the face's real entrypoint, and every
"loosened" case is asserted accepted rather than merely not-asserted, so the
disclosed behavior change is pinned instead of implied.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any, Callable

import pytest

from loopx.capabilities.decision_context.packets import _compact_text as decision_text
from loopx.capabilities.decision_context.packets import build_decision_evidence_packet
from loopx.capabilities.material_lifecycle._validation import (
    compact_text as material_text,
)
from loopx.capabilities.material_lifecycle.decision_planning import (
    build_material_explore_intent,
)
from loopx.public_safe_text import (
    CATEGORY_CREDENTIAL,
    CREDENTIAL_CATEGORIES,
    TEXT_OWNER_CATEGORIES,
    classify_private_text,
    find_private_text_match,
    matches_private_text_policy,
)
from loopx.public_safe_text import (
    COMPOUND_CREDENTIAL_FIELD_ASSIGNMENT_PATTERN as COMPOUND_ARM,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/public_safe_text.py"
_OBSERVED_AT = "2026-10-08T12:00:00+00:00"

FACES: list[tuple[str, Callable[[str], Any]]] = [
    ("decision_context", lambda text: decision_text(text, field="summary")),
    ("material_lifecycle", lambda text: material_text(text, field="summary")),
]
FACE_IDS = [name for name, _ in FACES]

# A credential label glued into a field name is invisible to every category arm,
# because they anchor the label with `\b` and `_` is a word character. This is the
# single class the two private lists reached and the owner did not, so it is the
# whole justification for the opt-in.
COMPOUND_FIELD_ASSIGNMENTS = [
    "password_hash=Qwerty1234567890",
    'db_password = "S3cret!value"',
    "client_secret: abcdef123456",
    "userPassword=Hunter2Please",
    "API_KEY_ID: 9f8a7b6c5d4e",
    "secrets_vault=Abc123456789xyz",
    '{"password_reset_token":"Abc12345678901"}',
]

# The rows the owner's value floor used to let through: the label is still glued
# into a field name, but the value carries no digit, no base64 character, and
# fifteen letters or fewer -- two of them quoted. An operator beside a credential
# label already states an assignment, so these must read the same verdict as the
# rows above rather than be released by a length accident (Refs #5136, direction
# 2). Folded from two pieces for the same scan-path reason stated below.
COMPOUND_SHORT_VALUE_ASSIGNMENTS = [
    "client_sec" + 'ret="hunter"',
    "db_pass" + "word=x",
    "password_hash" + "='hunter'",
    "password_reset_token" + ": short",
]

# Spellings the union of the two faces' lists accepted before this change and the
# owner's arms accept now. Listed by class, and pinned, so a later edit that drops
# an arm here fails instead of quietly loosening a validation face.
STILL_REJECTED = [
    # Spelled in two pieces the way the sibling owner suite does: `loopx check
    # --scan-path` reads a literal `label=value` in a test file as a credential
    # reference, and the concatenation folds to the same string.
    "pass" + "word=hunter2hunter2",
    "api_key: qwertyuiopasdfghjkl",
    "secret=abcdefghijklmn",
    "Bearer " + "z" * 20,
    "-----BEGIN RSA PRIVATE KEY-----",
    "ghp_" + "a" * 36,
]

# Newly rejected: the assignment arms carry no value-length floor and the word arm
# covers a bare mention the old substring list did not spell out.
NEWLY_REJECTED = [
    "authorization=abc123",
    "tok" + "en=abcdefgh",
    "Bearer, as configured",
    "password is hunter2hunter2x",
    "Basic Q2xpZW50MTpTZWNyZXQxMjM0NTY3ODkw",
]

# Newly accepted: every one of these is a mention with no value beside it, which is
# the false-positive class #5136 direction 2 asked to move out of the rule. The
# PEM row is the one shape the old lists spelled more loosely than the owner.
NEWLY_ACCEPTED = [
    "rotate the api_key daily",
    "the api_key column",
    "passwords",
    "no secrets here",
    "BEGIN RSA PRIVATE KEY",
]

BENIGN = [
    "weekly cadence digest rendered for goal_42",
    "token budget left for this stage: 1200",
    "the operator rotated the deploy credentials yesterday",
]


def _rejects(face: Callable[[str], Any], text: str) -> bool:
    try:
        face(text)
    except ValueError:
        return True
    return False


@pytest.mark.parametrize("face", FACE_IDS, ids=FACE_IDS)
@pytest.mark.parametrize(
    "text", COMPOUND_FIELD_ASSIGNMENTS + COMPOUND_SHORT_VALUE_ASSIGNMENTS
)
def test_both_faces_reject_a_credential_label_glued_into_a_field_name(
    face: str, text: str
) -> None:
    assert _rejects(dict(FACES)[face], text), (face, text)


@pytest.mark.parametrize("face", FACE_IDS, ids=FACE_IDS)
@pytest.mark.parametrize("text", STILL_REJECTED + NEWLY_REJECTED)
def test_both_faces_reject_every_assignment_the_owner_recognizes(
    face: str, text: str
) -> None:
    assert _rejects(dict(FACES)[face], text), (face, text)


@pytest.mark.parametrize("face", FACE_IDS, ids=FACE_IDS)
@pytest.mark.parametrize("text", NEWLY_ACCEPTED)
def test_both_faces_accept_a_bare_mention_with_no_value_beside_it(
    face: str, text: str
) -> None:
    # Pinned as accepted, not left unasserted: this is the disclosed loosening, and
    # a face that re-widens it without a decision fails here.
    assert dict(FACES)[face](text) == text, (face, text)


@pytest.mark.parametrize("face", FACE_IDS, ids=FACE_IDS)
@pytest.mark.parametrize("text", BENIGN)
def test_benign_prose_still_passes_both_faces(face: str, text: str) -> None:
    assert dict(FACES)[face](text) == text, (face, text)


def test_the_two_real_builders_reject_a_short_or_quoted_assignment() -> None:
    # The helpers are half the claim: a packet and an intent are what reach a public
    # surface. Every row goes through a legal build of both, so hardening only the
    # helper while a builder still carries the value fails here.
    for text in COMPOUND_SHORT_VALUE_ASSIGNMENTS:
        with pytest.raises(ValueError, match="changed_facts"):
            build_decision_evidence_packet(
                goal_id="goal:decision-advisor",
                decision_id="decision:20261008:credential-faces",
                observed_at=_OBSERVED_AT,
                changed_facts=[
                    {
                        "fact_id": "fact:adoption-stage",
                        "summary": text,
                        "source_ref": "authority:collaboration-ledger",
                        "source_revision": "revision:42",
                        "observed_at": _OBSERVED_AT,
                        "freshness": "current",
                        "authority": "first_party_receipt",
                    }
                ],
            )
        with pytest.raises(ValueError, match="stop_condition"):
            build_material_explore_intent(
                goal_id="goal:material-example",
                intent_id="intent:credential-faces",
                inventory_ref="material-inventory-0123456789abcdef",
                decision_evidence_ref="decision-evidence-0123456789abcdef",
                policy_ref="policy:material-fit",
                observed_at=_OBSERVED_AT,
                max_explore_topics=1,
                max_provider_calls=1,
                max_new_candidates=2,
                stop_condition=text,
                topics=[
                    {
                        "topic_ref": "topic:runtime",
                        "reason_code": "evidence_gap",
                    }
                ],
            )


def test_the_compound_arm_is_the_only_reason_the_glued_class_is_rejected() -> None:
    # Without the opt-in the owner's category arms miss every row in the class, so
    # the flag earns its place. If a future category arm covers them, this test
    # fails and the opt-in can be retired rather than left as dead weight.
    for text in COMPOUND_FIELD_ASSIGNMENTS + COMPOUND_SHORT_VALUE_ASSIGNMENTS:
        assert COMPOUND_ARM.search(text), text
        assert classify_private_text(text, categories=CREDENTIAL_CATEGORIES) is None, (
            text
        )
        assert (
            classify_private_text(
                text,
                categories=CREDENTIAL_CATEGORIES,
                include_compound_field_assignment=True,
            )
            is not None
        ), text


def test_the_opt_in_does_not_widen_the_surfaces_that_did_not_choose_it() -> None:
    # Blast radius, asserted on the owner rather than argued: the four migrated text
    # owners and the repository-publication tier see no new verdict from this slice.
    for text in COMPOUND_FIELD_ASSIGNMENTS + COMPOUND_SHORT_VALUE_ASSIGNMENTS:
        assert not matches_private_text_policy(text, categories=TEXT_OWNER_CATEGORIES)
        assert find_private_text_match(text) is None


def test_the_named_policy_lists_both_credential_categories() -> None:
    # A policy written as `ALL_CATEGORIES - {...}` would drop `credential_word` the
    # next time a category is added, loosening both faces by absence.
    assert CREDENTIAL_CATEGORIES == {CATEGORY_CREDENTIAL, "credential_word"}
    assert "credential_word" in CREDENTIAL_CATEGORIES
    assert CREDENTIAL_CATEGORIES <= TEXT_OWNER_CATEGORIES | {"credential_word"}


_LABEL_WORDS = ("password", "secret", "api", "token", "authorization", "bearer")

# Modules that still decide a credential-text question for themselves, with the
# number of constructions allowed there and why this slice did not move them.
# Converting one deletes its entry; an entry whose site is already converted fails.
DECLARED_OPEN_SITES: dict[str, str] = {
    # Its rule rejects an assignment with a value as short as one character for the
    # `api_key` and `access_token` labels. The owner's named arms carry no such
    # verdict: direction 2 asks for an explicit, tested short-assignment policy and
    # does not say which way, so this face stays where it is until that is decided.
    "loopx/extensions/presentation.py": "short-assignment verdict undecided",
    # Beyond the labels this rule also names vendor forms the owner does not
    # (`ak`/`sk`, `access_key_id`, `secret_key`). Route those to the owner first,
    # then this site is a plain policy call.
    "loopx/control_plane/todos/handoff_note.py": "names vendor forms the owner lacks",
    # The public-boundary contract is the publication tier: it refuses a label with
    # no value at all -- a trailing scheme word, an assignment operator with an
    # empty right side -- which is exactly what the owner's publication helper
    # `find_private_text_match` already answers. Moving it is one call with the
    # publication categories, but it decides the tier for every surface that passes
    # the contract, so it needs the corpus parity evidence behind it rather than two
    # capability faces' tables.
    "loopx/contract.py": "publication tier, needs its own corpus parity slice",
}


def _fold(node: ast.AST, module_constants: dict[str, str]) -> str | None:
    """Fold a module-level expression to the string it evaluates to."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return module_constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold(node.left, module_constants)
        right = _fold(node.right, module_constants)
        return None if left is None or right is None else left + right
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
        and len(node.args) == 1
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    ):
        parts = [_fold(element, module_constants) for element in node.args[0].elts]
        return (
            None
            if any(part is None for part in parts)
            else node.func.value.value.join(parts)  # type: ignore[arg-type]
        )
    return None


def _is_credential_text_rule(pattern_source: str, label_count: int) -> bool:
    """A credential alternation that reaches past the word for a value.

    The whitespace test is the discriminator: a rule with no ``\\s`` is matching an
    environment-variable or field *name*, which is a different decision with a
    different owner. Its limit is stated in the probe test below -- a word list that
    never looks past the word is not caught here.
    """

    return label_count >= 3 and "|" in pattern_source and "\\s" in pattern_source


def _credential_alternations_outside_the_owner() -> list[str]:
    """Name every module that still decides "text carries a credential" itself.

    The criterion is a credential alternation that also reaches for whitespace --
    three or more labels joined by ``|``, plus ``\\s``. A rule with no ``\\s`` is
    matching an environment-variable name or a field name, which is a different
    question with a different owner; three such rules exist today and are named in
    the PR body rather than silently excluded.
    """

    offenders: list[str] = []
    seen: set[str] = set()
    for path in sorted(REPO_ROOT.glob("loopx/**/*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == OWNER_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            name = getattr(node.targets[0], "id", "")
            pattern_source = _regex_pattern_source(node.value, constants)
            if pattern_source is None:
                continue
            constants[name] = pattern_source
            lowered = pattern_source.lower()
            labels = sum(word in lowered for word in _LABEL_WORDS)
            if _is_credential_text_rule(pattern_source, labels):
                seen.add(relative)
                if relative not in DECLARED_OPEN_SITES:
                    offenders.append(f"{relative}:{node.lineno}")
    # A declaration that no longer describes reality is its own failure: the site was
    # converted, and leaving the entry would hide a reintroduced copy.
    offenders.extend(
        f"{item} is declared open but compiles no such rule"
        for item in sorted(set(DECLARED_OPEN_SITES) - seen)
    )
    return offenders


def _regex_pattern_source(node: ast.AST, constants: dict[str, str]) -> str | None:
    """Fold the pattern a module-level ``re.compile(...)`` is handed.

    The assignment value is the *call*, so folding it directly would return None
    for every construction the scan exists to find; the probe test below holds
    that distinction.
    """

    if not isinstance(node, ast.Call):
        return None
    if "compile" not in ast.dump(node.func):
        return None
    for argument in node.args:
        folded = _fold(argument, constants)
        if folded is not None:
            return folded
    return None


def test_no_module_outside_the_owner_keeps_a_credential_alternation_list() -> None:
    assert _credential_alternations_outside_the_owner() == []


@pytest.mark.parametrize(
    ("label", "source", "caught"),
    [
        (
            "the historical spelling, obfuscated labels folded by the scan",
            'import re\n\n_C = re.compile(\n    "(?i)(" + "|".join(["Author" + "ization:", '
            '"Bear" + r"er\\s+[A-Za-z0-9._-]+", "api" + r"[_-]?key", "pass" + "word", '
            '"sec" + "ret"]) + ")"\n)\n',
            True,
        ),
        (
            "a plain alternation that also tests for a value",
            'import re\n\n_C = re.compile(\n    "(?i)(" + "|".join(["Author" + "ization", '
            '"pass" + "word", "sec" + "ret"]) + r")\\s*[:=]\\s*\\S"\n)\n',
            True,
        ),
        (
            "one label only, which is a field-name decision not a shape list",
            'import re\n\n_C = re.compile(r"^todo_[A-Za-z0-9_-]{6,80}$")\n',
            False,
        ),
        (
            "a local path rule that happens to name one label",
            'import re\n\n_C = re.compile(r"/Users/")\n',
            False,
        ),
        (
            "stated limit: a word-only list with no whitespace test is not caught",
            'import re\n\n_C = re.compile(r"(?i)(authorization|password|secret)")\n',
            False,
        ),
    ],
)
def test_the_census_finds_the_spelling_it_exists_to_find(
    label: str, source: str, caught: bool
) -> None:
    # A scan that silently folds nothing would report a clean repository forever; the
    # first row is what the copies deleted by this PR actually looked like.
    tree = ast.parse(source)
    constants: dict[str, str] = {}
    found: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        pattern_source = _regex_pattern_source(node.value, constants)
        if pattern_source is None:
            continue
        constants[getattr(node.targets[0], "id", "")] = pattern_source
        lowered = pattern_source.lower()
        if _is_credential_text_rule(
            pattern_source, sum(w in lowered for w in _LABEL_WORDS)
        ):
            found.append(pattern_source)
    assert bool(found) is caught, label
