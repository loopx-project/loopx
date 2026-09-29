"""Refs #5136 direction 3: one owner decides whether text carries a local path.

`loopx/capabilities/decision_context/packets.py` and
`loopx/capabilities/material_lifecycle/_validation.py` each carried the same
private copy, `(^|[\\s:=])(?:/Users/|/private/|/tmp/|~/)`, next to credential and
remote-location rules they already imported from the shared owner. These tests
pin the migration and its boundaries:

* both sites answer through `loopx/public_safe_text.find_public_safe_local_path`
  and no longer declare a local-path regex of their own;
* the union the owner decides is `LOCAL_PATH_SURFACE_PATTERN`, the two
  direction-3 gap shapes and the historical `:`/`=` boundary form, in that
  order, so dropping any arm is caught;
* nothing the sites rejected before is accepted now (no loosening), and the
  widening is exactly the direction-3 shapes -- both halves are measured against
  the shared corpus, so "no silent loosening" is enforced rather than claimed;
* each site keeps its own rejection message and length limits;
* `file://` is unchanged here (both sites still reject it through the
  raw-remote-location rule), and the general runtime export gate is *not*
  tightened by this PR, which `test_runtime_export_gate_is_unchanged` pins.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from loopx import public_safe_text as owner
from loopx.capabilities.decision_context import build_decision_evidence_packet
from loopx.capabilities.decision_context import packets as decision_packets
from loopx.capabilities.material_lifecycle import _validation as material_validation
from loopx.capabilities.material_lifecycle.intake import (
    build_material_candidate_intake_proposal,
)
from loopx.control_plane.runtime import public_safety

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "public_safe_text_corpus.json"
_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z_]*)\}")

# The rule these two sites shipped before this PR, kept here only so the
# widening and the preserved half are both machine-checked.
LEGACY_SITE_COPY = re.compile(r"(^|[\s:=])(?:/Users/|/private/|/tmp/|~/)")

OBSERVED_AT = "2026-07-25T13:30:00+00:00"

LOCAL_PATH_MESSAGE = "must not contain a local path"


def _shapes_the_owner_recognizes() -> list[str]:
    """Every home-relative, prefixed, drive, UNC and absolute shape direction 3 names."""
    return [
        "~/notes.md",
        "~\\notes.md",
        "path:/srv/data/goal.json",
        "PATH:\\\\fileserver\\share",
        "/Users/alex/notes.md",
        "/home/alex/.codex/auth.json",
        "/var/folders/9x/private/notes",
        "/private/tmp/x/lease.log",
        "/tmp/build/out.log",
        "/etc/loopx/registry.json",
        "/opt/secrets/token",
        "/mnt/nas/private.log",
        "/root/.ssh/id_rsa",
        "/workspace/private/notes.md",
        "/data/goal-evidence/dump",
        "C:\\Users\\alex\\notes.md",
        "D:/notes/private.md",
        "\\\\fileserver\\share\\notes.md",
        # The boundary form: introduced by a colon or equals sign, which the
        # absolute-root pattern's lookbehind intentionally skips.
        "lookup :/Users/alex/notes.md",
        "dump=/private/tmp/x/lease.log",
    ]


def _corpus_samples() -> list[str]:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    def render(template: str) -> str:
        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name.endswith("_LOWER"):
                return "".join(corpus["tokens"][name[: -len("_LOWER")]]).lower()
            return "".join(corpus["tokens"][name])

        return _PLACEHOLDER_RE.sub(substitute, template)

    return [
        render(sample["template"])
        for kind in ("public_safe", "private_looking")
        for sample in corpus[kind]
    ]


def test_both_sites_answer_through_the_owner_object() -> None:
    assert (
        public_safety.find_public_safe_local_path is owner.find_public_safe_local_path
    )
    assert (
        decision_packets.find_public_safe_local_path
        is owner.find_public_safe_local_path
    )
    assert (
        material_validation.find_public_safe_local_path
        is owner.find_public_safe_local_path
    )


def test_neither_site_declares_a_local_path_regex_any_more() -> None:
    assert not hasattr(decision_packets, "_LOCAL_PATH_RE")
    assert not hasattr(material_validation, "_LOCAL_PATH_RE")
    for module in (decision_packets, material_validation):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "compile"
            ):
                continue
            literal = " ".join(
                part.value for part in node.args if isinstance(part, ast.Constant)
            )
            assert "/Users/" not in literal and "~/)" not in literal, (
                f"{module.__name__} declares a second local-path rule: {literal!r}"
            )


def test_owner_recognizes_the_absolute_roots_the_gaps_and_the_boundary_form() -> None:
    assert owner.PUBLIC_SAFE_LOCAL_PATH_PATTERNS == (
        owner.LOCAL_PATH_SURFACE_PATTERN,
        owner.HOME_RELATIVE_PATH_PATTERN,
        owner.PATH_PREFIX_LOCAL_PATTERN,
        owner.LOCAL_PATH_BOUNDARY_REFERENCE_PATTERN,
    )


@pytest.mark.parametrize("value", _shapes_the_owner_recognizes())
def test_owner_recognizes_every_local_path_shape(value: str) -> None:
    assert owner.find_public_safe_local_path(value) is not None


@pytest.mark.parametrize("value", _corpus_samples())
def test_owner_never_loosens_what_the_shared_corpus_already_established(
    value: str,
) -> None:
    # Anything the replaced private copy flagged must still be flagged. This is
    # the enforced half of "no silent loosening" over the corpus both runtimes
    # are pinned to.
    if LEGACY_SITE_COPY.search(value) is not None:
        assert owner.find_public_safe_local_path(value) is not None


def test_owner_covers_the_whole_legacy_equivalence_class() -> None:
    # Enumerated rather than hand-picked: every boundary character x every root
    # the copies knew or direction 3 names x a trailing segment. The claim this
    # PR makes is one-directional -- the owner must reject everything the copies
    # rejected -- so the whole product is checked, not a sample of it.
    boundaries = ["", " ", ":", "=", "(", ",", '"', "'", "/", "x", "-", "@", "\t", "\n"]
    roots = [
        "/Users/",
        "/private/",
        "/tmp/",
        "~/",
        "~\\",
        "/home/",
        "/var/folders/",
        "/etc/",
        "/opt/",
        "/srv/",
        "/mnt/",
        "/root/",
        "/data/",
        "/workspace/",
        "/workspaces/",
        "/Volumes/",
        "C:\\",
        "D:/",
        "\\\\fileserver\\share\\",
        "path:/",
        "PATH:\\",
        "//Users/",
    ]
    loosened: list[str] = []
    widened: set[str] = set()
    for boundary in boundaries:
        for root in roots:
            for value in (
                f"note {boundary}{root}segment.md",
                f"{boundary}{root}segment.md",
            ):
                legacy = LEGACY_SITE_COPY.search(value) is not None
                current = owner.find_public_safe_local_path(value) is not None
                if legacy and not current:
                    loosened.append(repr(value))
                elif current and not legacy:
                    widened.add(root)
    assert loosened == []
    # The widening half: roots the copies simply did not know.
    assert "/home/" in widened
    assert "path:/" in widened
    assert "\\\\fileserver\\share\\" in widened
    assert "/var/folders/" in widened


def test_the_widening_is_the_direction_three_shapes_not_the_corpus() -> None:
    newly_recognized = [
        value
        for value in _shapes_the_owner_recognizes()
        if LEGACY_SITE_COPY.search(value) is None
    ]
    # Home-relative Windows form, `path:` prefix, drive letter and UNC share were
    # all accepted by the private copies; ordinary absolute roots are unchanged.
    assert newly_recognized == [
        "~\\notes.md",
        "path:/srv/data/goal.json",
        "PATH:\\\\fileserver\\share",
        "/home/alex/.codex/auth.json",
        "/var/folders/9x/private/notes",
        "/etc/loopx/registry.json",
        "/opt/secrets/token",
        "/mnt/nas/private.log",
        "/root/.ssh/id_rsa",
        "/workspace/private/notes.md",
        "/data/goal-evidence/dump",
        "C:\\Users\\alex\\notes.md",
        "D:/notes/private.md",
        "\\\\fileserver\\share\\notes.md",
    ]
    assert not [
        value
        for value in _corpus_samples()
        if LEGACY_SITE_COPY.search(value) is None
        and owner.find_public_safe_local_path(value) is not None
    ]


def test_decision_context_rejects_a_nested_local_path_with_its_own_message() -> None:
    # `path:`-prefixed was outside the private copy's alternation, so this value
    # only reaches the site through the owner.
    with pytest.raises(ValueError, match=LOCAL_PATH_MESSAGE) as matched:
        build_decision_evidence_packet(
            goal_id="goal:decision-advisor",
            decision_id="decision:20260725:priority",
            observed_at=OBSERVED_AT,
            changed_facts=[
                {
                    "fact_id": "fact:adoption-stage",
                    "summary": "deploy notes at path:/srv/data/goal.json",
                    "source_ref": "authority:collaboration-ledger",
                    "source_revision": "revision:42",
                    "observed_at": OBSERVED_AT,
                    "freshness": "current",
                    "authority": "first_party_receipt",
                }
            ],
        )
    assert "changed_facts[0].summary" in str(matched.value)


def test_material_lifecycle_rejects_a_local_path_with_its_own_message() -> None:
    # A home-relative path with a Windows separator: the private copy only knew
    # `~/`, so rejecting this one proves the site reads the owner.
    with pytest.raises(ValueError, match=LOCAL_PATH_MESSAGE) as matched:
        build_material_candidate_intake_proposal(
            goal_id="goal:material-intake",
            proposal_id="proposal:20260725:1",
            store_id="store:project",
            source_authority_revision="revision:7",
            material_ref="~\\notes.md",
            source_ref="authority:exact-read",
            source_revision="revision:8",
            exact_read_ref="exact:read-1",
            content_digest="sha256:0" * 8,
            content_size_bytes=128,
            observed_at=OBSERVED_AT,
        )
    assert "material_ref" in str(matched.value)


def test_both_sites_keep_their_length_limits_and_clean_verdicts() -> None:
    assert decision_packets._compact_text("public alias v3", field="summary") == (
        "public alias v3"
    )
    assert material_validation.compact_text("public alias v3", field="summary") == (
        "public alias v3"
    )
    with pytest.raises(ValueError, match="must be at most 320 characters"):
        decision_packets._compact_text("x" * 321, field="summary")
    with pytest.raises(ValueError, match="must be at most 320 characters"):
        material_validation.compact_text("x" * 321, field="summary")


@pytest.mark.parametrize(
    "value",
    ["file:///Users/alex/notes.md", "file://./notes.md"],
)
def test_file_url_verdicts_are_unchanged_and_still_named_as_raw_urls(
    value: str,
) -> None:
    # The direction-3 `file://` decision is deferred, so these must keep failing
    # through the raw-remote-location rule with the site's own wording.
    with pytest.raises(ValueError, match="raw URL"):
        material_validation.compact_text(value, field="source_ref")
    with pytest.raises(ValueError, match="raw URL"):
        decision_packets._compact_text(value, field="source_ref")


def test_runtime_export_gate_is_unchanged() -> None:
    # Disclosed non-change, pinned so the later tightening has to be a deliberate
    # edit here: `validate_public_safe_value` still accepts a home-relative
    # reference. Direction 3 decides that per destination, and how many of the
    # gate's call sites would newly reject was not measured in this PR.
    public_safety.validate_public_safe_value(
        {"target_layout": "~/.agents/skills", "note": "path: is fine without a path"}
    )
