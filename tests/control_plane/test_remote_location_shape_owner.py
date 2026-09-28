"""Refs #5136: one owner decides what "this text carries a raw remote location" means.

Three validators - the decision-context packet contract, the material-lifecycle
compaction helper, and the ML-experiment domain pack - each compiled the identical
scheme list `https?|file|s3|gs|tos|hdfs` under their own private name, while the
canonical public-safety owner in `loopx/control_plane/runtime/public_safety.py`
carried the sibling decisions (local path surfaces, credential-like surfaces) but had
no counterpart for this one. Two consequences followed: a fourth caller had to invent
a fifth spelling, and any new object-store scheme had to be found in three places that
nothing linked together.

The single owner is now the shared text-classification home
(`loopx/public_safe_text.py`, Refs #5136 direction 1); `public_safety` re-exports the
compiled pattern for its recursive payload validation, so the scheme list still lives
in one module. Each site keeps its own error text and its own threshold policy - the
sites reject at different lengths and one of them adds vendor-specific markers, which
is per-surface policy, not a duplicate decision. The literal scan below is what stops
the copies from growing back.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from loopx.capabilities.decision_context import packets
from loopx.capabilities.material_lifecycle import _validation
from loopx.control_plane.runtime import public_safety
from loopx.domain_packs import ml_experiment

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / "loopx"
SCHEME_LIST = "https?|file|s3|gs|tos|hdfs"

# (site label, entry point, the message that site raises for a raw location).
# Each row goes through the real entry point so the test proves the wiring, not just
# the pattern; the messages differ per site on purpose.
SITES: list[tuple[str, Callable[[str], str], str]] = [
    (
        "decision_context",
        lambda value: packets._compact_text(value, field="source_ref"),
        "must use an opaque source reference, not a raw URL",
    ),
    (
        "material_lifecycle",
        lambda value: _validation.compact_text(value, field="source_ref"),
        "must use an opaque reference, not a raw URL",
    ),
    (
        "ml_experiment",
        lambda value: ml_experiment._compact_public_text(value, field="dataset_ref"),
        "must use a public alias, not a raw URL or remote path",
    ),
]


def _source_text_spelling_the_scheme_list() -> list[str]:
    """Name every active module that writes this shape decision as its own literal."""
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if SCHEME_LIST in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPOSITORY_ROOT)))
    return offenders


def test_the_pattern_is_compiled_once_by_the_owner() -> None:
    # The negative control: a spelling that survives in any module other than the
    # owner is the exact regression this file exists to catch. Refs #5136
    # direction 1: the single owner is now the shared text-classification home
    # (loopx/public_safe_text.py). public_safety re-exports the compiled pattern
    # for its recursive payload validation, so the literal scheme list still
    # lives in exactly one module.
    assert _source_text_spelling_the_scheme_list() == [
        "loopx/public_safe_text.py"
    ]


@pytest.mark.parametrize("scheme", ["https", "http", "file", "s3", "gs", "tos", "hdfs"])
def test_owner_shape_recognises_every_scheme_the_copies_did(scheme: str) -> None:
    assert public_safety.REMOTE_LOCATION_SURFACE_PATTERN.search(
        f"{scheme}://bucket/key"
    )
    assert public_safety.REMOTE_LOCATION_SURFACE_PATTERN.search(
        f"{scheme.upper()}://B/K"
    )


def test_owner_shape_still_leaves_unlisted_schemes_alone() -> None:
    """The merge changed no coverage: `ftp` was outside all three copies, and stays out.

    Widening the scheme list is a separate decision from deduplicating it, and this
    row keeps the two from being confused later.
    """
    assert (
        public_safety.REMOTE_LOCATION_SURFACE_PATTERN.search("ftp://host/file") is None
    )


@pytest.mark.parametrize("label,call,message", SITES)
def test_each_site_rejects_a_raw_location_through_its_own_entry_point(
    label: str, call: Callable[[str], str], message: str
) -> None:
    for value in (
        "s3://loopx-artifacts/run-7/metrics.json",
        "file:///Users/dev/model.bin",
    ):
        with pytest.raises(ValueError) as caught:
            call(value)
        assert message in str(caught.value), (label, value)


@pytest.mark.parametrize("label,call,_message", SITES)
def test_each_site_still_accepts_an_opaque_reference(
    label: str, call: Callable[[str], str], _message: str
) -> None:
    """Positive control on the same entry point with no injected fault."""
    assert call("run-7/metrics.json") == "run-7/metrics.json"


def test_the_sites_keep_their_own_thresholds() -> None:
    """Per-surface policy stays per-surface: only the shared shape moved.

    The three entry points reject at different lengths and the domain pack adds
    vendor-specific marker terms, so a future reader must not "helpfully" fold those
    into the owner the way the scheme list was folded.
    """
    assert packets._compact_text("x" * 320, field="a").startswith("xxx")
    with pytest.raises(ValueError, match="at most 320"):
        packets._compact_text("x" * 321, field="a")
    assert ml_experiment._compact_public_text("y" * 160, field="a").startswith("yyy")
    with pytest.raises(ValueError, match="too long"):
        ml_experiment._compact_public_text("y" * 161, field="a")


def test_owner_pattern_is_the_object_each_site_consults() -> None:
    pattern = public_safety.REMOTE_LOCATION_SURFACE_PATTERN
    assert isinstance(pattern, re.Pattern)
    for module in (packets, _validation, ml_experiment):
        assert module.REMOTE_LOCATION_SURFACE_PATTERN is pattern, module.__name__
