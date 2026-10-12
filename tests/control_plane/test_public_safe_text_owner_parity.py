"""One corpus, every real public-safe text owner.

The rule that decides whether control-plane text looks private is enforced by
four owners: `loopx.feedback`, `loopx.authority`, `loopx.boundary_authority`,
and the TypeScript Vision checkpoint reached through `build_vision_checkpoint`.
Testing one owner's helper cannot prove the contract, because the owners used
to disagree: the Vision path rejected ordinary "owner authorization" prose
while the Python patterns accepted a quoted-JSON credential header.

These tests drive the shared fixture through each owner's real entrypoint, so
any owner that drifts fails here instead of in a reviewer's manual probe. The
fixture has three buckets because the four owners are one tier: they validate
LoopX's own state, so they accept a bare credential word (Refs #5136 direction
2) while the stricter publication tier keeps rejecting it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from loopx.authority import validate_public_safe_text as validate_authority_text
from loopx.boundary_authority import build_checkpointed_boundary_authority_entry
from loopx.control_plane.goals.vision_checkpoint import build_vision_checkpoint
from loopx.feedback import validate_public_safe_text as validate_feedback_text
from loopx.public_safe_text import (
    ALL_CATEGORIES,
    CATEGORY_CREDENTIAL_WORD,
    TEXT_OWNER_CATEGORIES,
    classify_private_text,
    find_private_text_match,
)

CORPUS_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "public_safe_text_corpus.json"
)
PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z_]*)\}")


def _load_corpus() -> dict[str, Any]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert corpus["schema_version"] == "public_safe_text_corpus_v2"
    return corpus


def _render(template: str, tokens: dict[str, list[str]]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in tokens:
            return "".join(tokens[name])
        if name.endswith("_LOWER") and name[: -len("_LOWER")] in tokens:
            return "".join(tokens[name[: -len("_LOWER")]]).lower()
        raise AssertionError(f"corpus placeholder {name} has no token definition")

    return PLACEHOLDER_RE.sub(replace, template)


def _samples(group: str) -> list[tuple[str, str]]:
    corpus = _load_corpus()
    tokens = corpus["tokens"]
    return [
        (str(sample["id"]), _render(str(sample["template"]), tokens))
        for sample in corpus[group]
    ]


PUBLIC_SAFE_SAMPLES = _samples("public_safe")
PRIVATE_LOOKING_SAMPLES = _samples("private_looking")
INTERNAL_STATE_PROSE_SAMPLES = _samples("internal_state_prose")


def _shaped_samples(group: str) -> list[tuple[str, str, str]]:
    """The group's samples with the contract signal each row declares."""

    corpus = _load_corpus()
    tokens = corpus["tokens"]
    return [
        (str(sample["id"]), _render(str(sample["template"]), tokens), str(sample["shape"]))
        for sample in corpus[group]
    ]


# Which named reason the contract expects for a declared signal. Written from the
# contract prose in the fixture, so a row cannot pass by matching whichever
# implementation pattern happens to be first: the reason has to be the one that
# signal owns.
SIGNAL_REASONS: dict[str, frozenset[str]] = {
    "assignment_punctuation": frozenset(
        {"credential-word assignment shape", "authorization header/assignment shape"}
    ),
    "shaped_value_token": frozenset({"credential label carrying a shaped value"}),
    "quoted_value": frozenset({"credential label carrying a quoted value"}),
    "opaque_value_run": frozenset({"credential label carrying a shaped value"}),
    "unlabeled_secret_shape": frozenset({"credential-like value shape", "basic-auth credential value"}),
    "non_credential_local_path": frozenset({"absolute home-directory path", "local filesystem path"}),
    "credential_word_only": frozenset(
        {"bearer auth scheme word", "password word", "secret word"}
    ),
    "remote_location_undecided": frozenset({"raw remote location URL"}),
    "non_credential_prose": frozenset(),
}
CORPUS_GROUPS = ("public_safe", "private_looking", "internal_state_prose")


def _ids(samples: list[tuple[str, str]]) -> list[str]:
    return [sample_id for sample_id, _ in samples]


def _boundary_authority_text(text: str) -> None:
    """Drive boundary_authority through its public builder, not its helper."""

    build_checkpointed_boundary_authority_entry(
        write_scopes=["goal_state"],
        source=text,
    )


def _vision_text(text: str) -> None:
    """Drive the real TypeScript Vision owner through the Python entrypoint."""

    build_vision_checkpoint(
        agent_id="kiro-cli",
        agent_vision=None,
        existing_agent_vision=None,
        vision_unchanged_reason=text,
        delivery_outcome="outcome_progress",
        active_state_next_action_update=None,
    )


OWNERS = (
    ("feedback", lambda text: validate_feedback_text("agent_vision.summary", text)),
    ("authority", lambda text: validate_authority_text("project_material.note", text)),
    ("boundary_authority", _boundary_authority_text),
    ("vision_checkpoint_ts", _vision_text),
)
OWNER_IDS = [owner_id for owner_id, _ in OWNERS]

EXTENDED_WINDOWS_PATHS = (
    r"\\?\UNC\fileserver\share\a.md",
    r"\\?\Volume{01234567-89ab-cdef-0123-456789abcdef}\notes\a.md",
    r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\notes\a.md",
)


@pytest.mark.parametrize("owner", [check for _, check in OWNERS], ids=OWNER_IDS)
@pytest.mark.parametrize("sample", PUBLIC_SAFE_SAMPLES, ids=_ids(PUBLIC_SAFE_SAMPLES))
def test_public_safe_corpus_is_accepted_by_every_owner(
    owner: Any,
    sample: tuple[str, str],
) -> None:
    owner(sample[1])


@pytest.mark.parametrize("owner", [check for _, check in OWNERS], ids=OWNER_IDS)
@pytest.mark.parametrize(
    "sample", PRIVATE_LOOKING_SAMPLES, ids=_ids(PRIVATE_LOOKING_SAMPLES)
)
def test_private_looking_corpus_is_rejected_by_every_owner(
    owner: Any,
    sample: tuple[str, str],
) -> None:
    with pytest.raises(ValueError, match="private-looking value"):
        owner(sample[1])


@pytest.mark.parametrize("owner", [check for _, check in OWNERS], ids=OWNER_IDS)
@pytest.mark.parametrize("path", EXTENDED_WINDOWS_PATHS)
def test_extended_windows_paths_remain_accepted_by_state_owners(
    owner: Any,
    path: str,
) -> None:
    owner(path)


def test_corpus_covers_the_reviewed_credential_shapes() -> None:
    """Guard the corpus itself: the shapes that motivated this contract."""

    required = {
        "raw_header_basic",
        "assignment_bearer",
        "quoted_json_key_basic",
    }
    assert required <= set(_ids(PRIVATE_LOOKING_SAMPLES))
    assert "governance_prose_needs_owner_authorization" in _ids(PUBLIC_SAFE_SAMPLES)
    # Direction 2's boundary is part of the contract, so the corpus keeps a sample
    # for each signal, for the ordinary word the retired length floor used to
    # reject, and for the residual the contract cannot recognize.
    private_ids = set(_ids(PRIVATE_LOOKING_SAMPLES))
    assert {
        "bearer_value_at_named_floor",
        "bearer_digit_value_below_old_floor",
        "token_space_digit_value",
        "copula_password_digit_value",
        "copula_token_single_digit_value",
        "composite_copula_password_digit_value",
        "composite_copula_secret_quoted_value",
        "composite_copula_bearer_opaque_value",
        "space_secret_opaque_value",
        "comma_bearer_opaque_value",
        "dash_secret_opaque_value",
        "quoted_password_passphrase",
        "quoted_password_single_letter_value",
        "quoted_password_key_single_digit_value",
        "quoted_secret_key_single_letter_string",
        "single_quoted_password_key_short_value",
        "quoted_secret_key_empty_assignment",
        "bearer_opaque_letter_run_at_ceiling",
        "bearer_assignment_colon_bare_word",
        "password_assignment_short_value",
        "secret_assignment_colon",
        "token_assignment_colon",
    } <= private_ids
    prose_ids = set(_ids(INTERNAL_STATE_PROSE_SAMPLES))
    assert {
        "quoted_password_word_in_prose",
        "bearer_word_in_prose",
        "bearer_before_long_ordinary_word",
        "password_copula_ordinary_word",
        "password_composite_copula_ordinary_word",
        "disclosed_residual_short_letter_value",
    } <= prose_ids


def test_every_corpus_row_is_declared_by_one_contract_signal() -> None:
    # The fixture is the contract, so coverage goes both ways: every row names a
    # declared signal, and every declared signal has at least one row. An arm added
    # without a sample, or a sample whose verdict its own signal does not explain,
    # fails here instead of in a reviewer's manual probe.
    signals = set(_load_corpus()["contract_signals"])
    declared: set[str] = set()
    for group in CORPUS_GROUPS:
        for sample_id, text, shape in _shaped_samples(group):
            assert shape in signals, sample_id
            declared.add(shape)
            strict = classify_private_text(text, categories=ALL_CATEGORIES)
            internal = classify_private_text(text, categories=TEXT_OWNER_CATEGORIES)
            if group == "public_safe":
                assert internal is None, sample_id
                if shape == "remote_location_undecided":
                    assert strict is not None, sample_id
                    assert strict.reason in SIGNAL_REASONS[shape], (sample_id, strict.reason)
                else:
                    assert strict is None, sample_id
            elif group == "private_looking":
                assert strict is not None and internal is not None, sample_id
                assert internal.reason == strict.reason, sample_id
                assert internal.reason in SIGNAL_REASONS[shape], (sample_id, internal.reason)
            else:
                assert internal is None, sample_id
                assert strict is not None, sample_id
                assert strict.category == CATEGORY_CREDENTIAL_WORD, sample_id
                assert strict.reason in SIGNAL_REASONS[shape], (sample_id, strict.reason)
    assert declared == signals, signals ^ declared


@pytest.mark.parametrize("owner", [check for _, check in OWNERS], ids=OWNER_IDS)
@pytest.mark.parametrize(
    "sample", INTERNAL_STATE_PROSE_SAMPLES, ids=_ids(INTERNAL_STATE_PROSE_SAMPLES)
)
def test_internal_state_prose_is_accepted_by_every_text_owner(
    owner: Any,
    sample: tuple[str, str],
) -> None:
    owner(sample[1])


def test_internal_state_prose_stays_rejected_by_the_publication_tier() -> None:
    # The tier difference is the whole of direction 2, so it has to be visible
    # from the stricter surface as well: everything the four owners now let
    # through is still recognized by the full category set, and by the legacy
    # helper the repository-publication scan still reaches.
    for sample_id, value in INTERNAL_STATE_PROSE_SAMPLES:
        assert find_private_text_match(value) is not None, sample_id
        assert classify_private_text(value, categories=ALL_CATEGORIES) is not None, (
            sample_id
        )
        assert classify_private_text(value, categories=TEXT_OWNER_CATEGORIES) is None, (
            sample_id
        )


def test_no_value_bearing_form_left_the_internal_state_policy() -> None:
    # A category set is a blunt instrument: dropping `credential_word` must not
    # drop an assignment or a scheme-with-value. This walks every private-looking
    # sample and proves the narrower policy still rejects all of them, so the
    # only values the split can release are the prose samples above.
    for sample_id, value in PRIVATE_LOOKING_SAMPLES:
        match = classify_private_text(value, categories=TEXT_OWNER_CATEGORIES)
        assert match is not None, sample_id
        assert match.category != CATEGORY_CREDENTIAL_WORD, sample_id
