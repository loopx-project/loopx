"""Per-value meaning ratchet for the ``cross_runtime`` vocabulary tier.

``tests/architecture/test_semantic_vocabulary_drift.py`` already requires a
``value_notes`` entry for every value of the live kernel vocabularies (#4625 for
the four canonical Turn vocabularies, #4626 for ``effective_action``).
M4 retired the unused Python ``lease_action`` input and registered the live
native lifecycle request owner; its values carry the same note obligation here.

This file is the same obligation for the other tier, kept separate on purpose:
the kernel ratchet sits at the end of a file that several open branches already
edit, and a shared tail is where same-diff rules get lost in a merge.

The bar a note has to meet is the one #4625/#4626 set, and it is not "a
sentence exists". A note says **which condition produces the value** — what has
to be true at runtime for the code to choose it.

What this file enforces is narrower than that bar, and the difference matters
when reading a green run. It proves that an entry exists, that a blank entry
does not count as one, and that an entry reporting the condition as unresolved
names the evidence that would settle it. It cannot tell a note that states the
producing condition from one that rephrases its own identifier or records only
the disposition that follows, and it cannot check that a stated condition was
ever true or still matches the code after the code moves. Whether the prose is
true, and whether it still matches the code, stays a review obligation; a green
run does not certify it.

Earlier revisions of this file tried to close part of that gap with a character
floor and a count of the note's non-stopword words. Both are gone. A word count
cannot show that a note names the producing condition, and the behaviour it
does reliably change is to reward padding. Capping how many values may say
"unresolved" fails the same way from the other side: a budget on honesty
pressures the next author to invent a producing condition rather than record
that the evidence is missing, which is the outcome the RFC's evidence rules
exist to prevent.

Where the producing condition genuinely cannot be established from the code,
the honest note is the one those evidence rules require: say it is unresolved
and say what evidence is missing. Those are spelled
``Unresolved: ... Missing evidence: ...``, so the shape is checkable here and
the count stays readable from the registry for anyone who wants to track it.
"""

from __future__ import annotations

import copy
import runpy
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE = REPO_ROOT / "examples" / "semantic-vocabulary-drift-smoke.py"

TIER = "cross_runtime"

# The registry is read through the smoke's own loader, so this ratchet sees the
# same validated shape the drift check does instead of a second JSON reader
# that could disagree with it.
_SMOKE = runpy.run_path(str(SMOKE))
_REGISTRY = _SMOKE["load_registry"]()
_VOCABULARIES = _REGISTRY["vocabularies"]

CROSS_RUNTIME_VOCABULARIES = sorted(
    name for name, entry in _VOCABULARIES.items() if entry["tier"] == TIER
)

# An unresolved note is a legitimate outcome, not a loophole: it must name the
# evidence that would settle the value.
UNRESOLVED_PREFIX = "unresolved:"
MISSING_EVIDENCE_MARKER = "missing evidence:"


def _note(name: str, value: str) -> str:
    return str((_VOCABULARIES[name].get("value_notes") or {}).get(value) or "").strip()


def _undocumented(vocabulary: dict) -> list[str]:
    notes = vocabulary.get("value_notes", {})
    return [
        value
        for value in vocabulary["values"]
        if not str(notes.get(value) or "").strip()
    ]


@pytest.mark.parametrize("name", CROSS_RUNTIME_VOCABULARIES)
def test_every_cross_runtime_value_carries_a_note(name: str) -> None:
    """A ``cross_runtime`` value with no note sends every reader back to the code.

    The registry already settles who owns a vocabulary and which values are
    legal. It did not say what any of them mean, so a reader had to recover the
    producing condition from the generated rule table. Requiring the note in the
    diff that adds the value keeps that case reviewable at review time.
    """
    vocabulary = _VOCABULARIES[name]
    undocumented = _undocumented(vocabulary)
    assert not undocumented, f"{name}: values with no value_notes entry: {undocumented}"


def test_a_new_value_without_a_note_fails_the_ratchet() -> None:
    """The ratchet has to bite, not merely pass on a tree that is already clean.

    A green assertion over documented values proves nothing about the diff that
    adds an undocumented one, so the failure path is exercised directly.
    """
    vocabulary = copy.deepcopy(_VOCABULARIES[CROSS_RUNTIME_VOCABULARIES[0]])
    vocabulary["values"].append("probe_value_added_without_a_note")
    assert _undocumented(vocabulary) == ["probe_value_added_without_a_note"]


def test_an_empty_or_whitespace_note_does_not_count_as_coverage() -> None:
    """A present-but-blank note must not satisfy the ratchet."""
    name = CROSS_RUNTIME_VOCABULARIES[0]
    vocabulary = copy.deepcopy(_VOCABULARIES[name])
    value = vocabulary["values"][0]
    for blank in ("", "   ", "\n\t"):
        vocabulary["value_notes"][value] = blank
        assert _undocumented(vocabulary) == [value], blank


@pytest.mark.parametrize("name", CROSS_RUNTIME_VOCABULARIES)
def test_an_unresolved_note_must_name_the_missing_evidence(name: str) -> None:
    """"Unresolved" is an allowed answer only when it says what would settle it.

    The RFC's evidence rules forbid inventing a meaning to fill the table. They
    equally forbid an unresolved marker that is just a shrug: the note has to
    name the evidence whose absence blocks the reading, so a later diff knows
    what to go and find. Nothing here caps how many values may be unresolved —
    a cap would buy a smaller count by making the next author guess.
    """
    vocabulary = _VOCABULARIES[name]
    unnamed = []
    for value in vocabulary["values"]:
        note = _note(name, value).lower()
        if note.startswith(UNRESOLVED_PREFIX) and MISSING_EVIDENCE_MARKER not in note:
            unnamed.append(value)
    assert not unnamed, (
        f"{name}: unresolved notes must say what evidence is missing, spelled "
        f"'Missing evidence: ...': {unnamed}"
    )


def test_the_two_tier_ratchets_together_cover_every_registered_vocabulary() -> None:
    """No value may fall between the kernel ratchet and this one.

    The kernel tier is covered by ``test_semantic_vocabulary_drift.py`` and this
    tier by the parametrization above. ``TIERS`` in the smoke also admits
    ``cross_module``, so a vocabulary registered under a third tier would carry
    no per-value obligation at all. It fails here until someone extends one of
    the two ratchets to reach it.
    """
    covered = {
        name
        for name, entry in _VOCABULARIES.items()
        if entry["tier"] in {"kernel", TIER}
    }
    uncovered = sorted(set(_VOCABULARIES) - covered)
    assert not uncovered, (
        "vocabularies in a tier no per-value ratchet walks: "
        f"{[(name, _VOCABULARIES[name]['tier']) for name in uncovered]}"
    )


def test_the_parametrized_population_is_derived_from_the_registry() -> None:
    """The ratchet's population must be counted, never typed in.

    A hand-listed set of vocabulary names is the failure this whole file exists
    to prevent: a new ``cross_runtime`` vocabulary would be outside the list and
    the tier would look covered.
    """
    assert CROSS_RUNTIME_VOCABULARIES == sorted(
        name for name, entry in _VOCABULARIES.items() if entry["tier"] == TIER
    )
    assert CROSS_RUNTIME_VOCABULARIES, "the cross_runtime tier is not empty"
