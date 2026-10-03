"""The production half of the digest owner (Refs #5336).

`tests/architecture/test_content_digest_single_owner.py` pins who may *state* the
shape of a stored SHA-256. Its own docstring names the other half and leaves it
open: "producers that concatenate `\"sha256:\"` by hand are the other half of the
decision and are deliberately unchanged." This file starts changing them, one
surface at a time, and guards what it has converted.

Two layers, mirroring the existing guard's discipline:

1. a **production scan**: inside a converted surface, no module may build the
   envelope except through `loopx.control_plane.digest_envelope`. It judges the
   value a node denotes, so a module-level constant holding the prefix is the
   same offender as a literal;
2. **behavioural cases** that enter through the migrated helpers themselves and
   compare each one against the expression it replaced, so a migration that
   silently changed a digest fails here rather than in a reader's comparison.

A surface is only added to `CONVERTED_SURFACES` after every producer in that
package delegates envelope construction to the shared owner.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.periodic_report import (
    archive,
    audience,
    bindings,
    cadence_journal,
    incremental,
    machine_defaults,
    pending_intent,
    post_writeback_hook,
    request_action,
    runtime_producer,
    workspace,
)
from loopx.control_plane import digest_envelope
from loopx.control_plane.content_digest import (
    BARE_SHA256_PATTERN,
    ENVELOPED_SHA256_PATTERN,
)
from tests.architecture.test_content_digest_single_owner import (
    _collect_scopes,
    _fold_text,
    _scope_of,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_OWNER = "loopx/control_plane/digest_envelope.py"
ENVELOPE = "sha256:"

CONVERTED_SURFACES = ("loopx/capabilities/periodic_report",)


def _fold_joined_prefix(node: ast.JoinedStr, scope: Any) -> str | None:
    """Fold only the statically known leading portion of an f-string."""

    prefix = ""
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            folded = part.value
        elif (
            isinstance(part, ast.FormattedValue)
            and part.conversion == -1
            and part.format_spec is None
        ):
            folded = _fold_text(part.value, scope)
        else:
            folded = None
        if folded is None:
            return None
        prefix += folded
        if prefix == ENVELOPE:
            return prefix
        if not ENVELOPE.startswith(prefix):
            return None
    return prefix


def _hand_built_envelopes(source: str) -> list[str]:
    tree = ast.parse(source)
    root = _collect_scopes(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        scope = _scope_of(node, root)
        if isinstance(node, ast.JoinedStr):
            if _fold_joined_prefix(node, scope) == ENVELOPE:
                hits.append(f"f-string envelope at line {node.lineno}")
        elif isinstance(node, ast.BinOp):
            left = _fold_text(node.left, scope)
            if isinstance(node.op, ast.Add) and left == ENVELOPE:
                hits.append(f"concatenated envelope at line {node.lineno}")
            elif (
                isinstance(node.op, ast.Mod)
                and isinstance(left, str)
                and left.startswith(ENVELOPE)
                and left != ENVELOPE
            ):
                hits.append(f"percent-formatted envelope at line {node.lineno}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            receiver = _fold_text(node.func.value, scope)
            if (
                node.func.attr == "format"
                and isinstance(receiver, str)
                and receiver.startswith(ENVELOPE)
            ):
                hits.append(f"format envelope at line {node.lineno}")
            elif node.func.attr == "join" and receiver == "" and node.args:
                values = node.args[0]
                if isinstance(values, (ast.List, ast.Tuple)) and values.elts:
                    if _fold_text(values.elts[0], scope) == ENVELOPE:
                        hits.append(f"joined envelope at line {node.lineno}")
    return hits


def _scan_source(relative: str, source: str) -> list[str]:
    if relative == PRODUCTION_OWNER:
        return []
    return _hand_built_envelopes(source)


def _scan_surface(directory: str) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    root = REPOSITORY_ROOT / directory
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        hits = _scan_source(relative, path.read_text(encoding="utf-8"))
        if hits:
            offenders[relative] = hits
    return offenders


def test_converted_surfaces_build_the_envelope_only_through_the_owner() -> None:
    for directory in CONVERTED_SURFACES:
        offenders = _scan_surface(directory)
        assert not offenders, f"hand-built digest envelope remains: {offenders}"


# Each case is (label, source, expected). A scan that only matches one spelling is
# the failure mode the existing guard already documents, so the bypass forms are
# asserted here rather than assumed away.
BYPASS_CORPUS = (
    (
        "literal concatenation, the obvious form",
        'import hashlib\n\ndef d(v):\n    return "sha256:" + hashlib.sha256(v).hexdigest()\n',
        1,
    ),
    (
        "f-string interpolation",
        'import hashlib\n\ndef d(v):\n    return f"sha256:{hashlib.sha256(v).hexdigest()}"\n',
        1,
    ),
    (
        "prefix moved into a function-local constant",
        'import hashlib\n\ndef d(v):\n    p = "sha256:"\n    return p + hashlib.sha256(v).hexdigest()\n',
        1,
    ),
    (
        "function-local prefix folded through an f-string",
        'import hashlib\n\ndef d(v):\n    p = "sha256:"\n    return f"{p}{hashlib.sha256(v).hexdigest()}"\n',
        1,
    ),
    (
        "annotated function-local prefix folded through an f-string",
        'import hashlib\n\ndef d(v):\n    p: str = "sha256:"\n    return f"{p}{hashlib.sha256(v).hexdigest()}"\n',
        1,
    ),
    (
        "prefix moved into a module constant first",
        'import hashlib\n\nP = "sha256:"\n\n\ndef d(v):\n    return P + hashlib.sha256(v).hexdigest()\n',
        1,
    ),
    (
        "annotated module prefix folded through an f-string",
        'import hashlib\n\nP: str = "sha256:"\n\n\ndef d(v):\n    return f"{P}{hashlib.sha256(v).hexdigest()}"\n',
        1,
    ),
    (
        "annotated local prefix stays local to its function",
        'def d(prefix):\n    return prefix + "value"\n\ndef other():\n    prefix: str = "sha256:"\n',
        0,
    ),
    (
        "an argument shadows a module prefix inside an f-string",
        'P = "sha256:"\n\ndef d(P, value):\n    return f"{P}{value}"\n',
        0,
    ),
    (
        "a local f-string prefix does not leak into another function",
        'def d(prefix, value):\n    return f"{prefix}{value}"\n\ndef other():\n    prefix = "sha256:"\n',
        0,
    ),
    (
        "percent formatting",
        'import hashlib\n\ndef d(v):\n    return "sha256:%s" % hashlib.sha256(v).hexdigest()\n',
        1,
    ),
    (
        "format method",
        'def d(value):\n    return "sha256:{}".format(value)\n',
        1,
    ),
    (
        "join method",
        'def d(value):\n    return "".join(["sha256:", value])\n',
        1,
    ),
    (
        "reading an existing digest is not producing one",
        'def strip(value):\n    return value.removeprefix("sha256:")\n',
        0,
    ),
    (
        "stating the shape is the other guard's question",
        'import re\n\nP = re.compile("^sha256:[0-9a-f]{64}$")\n',
        0,
    ),
    (
        "an unrelated constant with the prefix inside prose",
        'MESSAGE = "artifact digest must use sha256:<64 lowercase hex>"\n',
        0,
    ),
    (
        "the production owner itself",
        'PREFIX = "sha256:"\n\n\ndef build(digest_hex):\n    return f"{PREFIX}{digest_hex}"\n',
        1,
    ),
)


@pytest.mark.parametrize(
    "label,source,expected", BYPASS_CORPUS, ids=[case[0] for case in BYPASS_CORPUS]
)
def test_the_production_scan_names_every_form_and_leaves_the_others(
    label: str, source: str, expected: int
) -> None:
    assert len(_hand_built_envelopes(source)) == expected, label


def test_the_production_owner_exemption_is_bound_to_its_file() -> None:
    source = 'PREFIX = "sha256:"\n\ndef build(value):\n    return f"{PREFIX}{value}"\n'
    assert _scan_source(PRODUCTION_OWNER, source) == []
    assert len(_scan_source("loopx/another_producer.py", source)) == 1


def test_the_owner_is_the_only_module_that_states_the_prefix_rule() -> None:
    # The scan above is per-surface; this one is the whole-tree fact that makes
    # the staging meaningful: the prefix literal lives in exactly two modules,
    # the generated shape owner and the production owner.
    holders: dict[str, list[str]] = {}
    for path in sorted((REPOSITORY_ROOT / "loopx").rglob("*.py")):
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        if relative in {PRODUCTION_OWNER, "loopx/control_plane/content_digest.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prefixed = [
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value == ENVELOPE
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
        if prefixed:
            holders[relative] = prefixed
    assert not holders, f"a second module binds the digest prefix: {holders}"


# --- layer 2: the migrated helpers still emit what they emitted before ------------------


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _reference(data: bytes) -> str:
    # The expression each migrated site used, kept here so the comparison is
    # against the previous spelling rather than against the new builder.
    return "sha256:" + hashlib.sha256(data).hexdigest()


SAMPLE = {"goal_id": "goal-7", "route": ["a", "b"], "count": 3}


@pytest.mark.parametrize(
    "helper,expected",
    [
        (bindings._sha256, _reference(_canonical(SAMPLE))),
        (audience._digest, _reference(_canonical(SAMPLE))),
        (machine_defaults._digest, _reference(_canonical(SAMPLE))),
        (workspace._canonical_digest, _reference(_canonical(SAMPLE))),
        (incremental._canonical_digest, _reference(_canonical(SAMPLE))),
        (cadence_journal._digest, _reference(_canonical(SAMPLE))),
        (pending_intent._canonical_digest, _reference(_canonical(SAMPLE))),
        (request_action._digest, _reference(_canonical(SAMPLE))),
        (
            archive._content_digest,
            _reference("report body".encode("utf-8")),
        ),
    ],
)
def test_each_migrated_helper_returns_the_previous_digest(
    helper: Any, expected: str
) -> None:
    value = "report body" if helper is archive._content_digest else SAMPLE
    assert helper(value) == expected


def test_the_event_digest_helper_keeps_its_own_canonicalization() -> None:
    # runtime_producer sorts and uses ensure_ascii=True, unlike its neighbours; the
    # envelope moved to the owner, the byte recipe did not, and this pins both.
    # A non-ASCII id, because that is the only input on which ensure_ascii is
    # observable: with ASCII alone the two recipes produce identical bytes.
    event_ids = ["\u4e8b\u4ef6-2", "evt-1"]
    encoded = json.dumps(
        sorted(event_ids), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    assert runtime_producer._event_digest(event_ids) == _reference(encoded)


def test_post_writeback_digest_recipes_keep_their_existing_bytes() -> None:
    request = {"request_id": "request-1", "goal_id": "goal-7", "agent_id": "agent-a"}
    assert post_writeback_hook.sha256_envelope(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ) == _reference(json.dumps(request, sort_keys=True, separators=(",", ":")).encode())


def test_the_two_envelope_kinds_a_conversion_must_not_mix_up() -> None:
    # A reader that accepts the bare shape and a writer that emits the enveloped
    # one are different questions; the builder is only allowed the second.
    data = _canonical({"blocks": [1, 2, 3]})
    built = digest_envelope.sha256_envelope(data)
    assert ENVELOPED_SHA256_PATTERN.fullmatch(built)
    assert not BARE_SHA256_PATTERN.fullmatch(built)
    assert BARE_SHA256_PATTERN.fullmatch(hashlib.sha256(data).hexdigest())


# --- the builder's own contract ---------------------------------------------------------


def test_the_builder_returns_the_owner_shape_and_borrows_its_pattern() -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    assert digest_envelope.enveloped_sha256(digest) == f"sha256:{digest}"
    # Not a restatement: the guard's `holds the owner object` rule applies here too.
    assert digest_envelope.ENVELOPED_SHA256_PATTERN is ENVELOPED_SHA256_PATTERN


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sha256:" + hashlib.sha256(b"x").hexdigest(),
        hashlib.sha256(b"x").hexdigest().upper(),
        hashlib.sha256(b"x").hexdigest()[:63],
        hashlib.sha256(b"x").hexdigest()[:63] + "z",
        " " + hashlib.sha256(b"x").hexdigest(),
        64 * "0" + "\n",
    ],
    ids=[
        "empty",
        "already-enveloped",
        "uppercase-hex",
        "one-char-short",
        "non-hex-last-char",
        "leading-space",
        "trailing-newline",
    ],
)
def test_the_builder_refuses_a_value_the_owner_would_not_recognize(value: str) -> None:
    with pytest.raises(ValueError, match="sha256:<64 lowercase hex"):
        digest_envelope.enveloped_sha256(value)


def test_the_builder_states_no_shape_of_its_own() -> None:
    # Identity with the owner's object is not enough to prove borrowing: `re.compile`
    # returns a cached object for a pattern compiled earlier, so a restated copy can
    # compare identical. The load-bearing fact is that this module states no pattern.
    source = (REPOSITORY_ROOT / PRODUCTION_OWNER).read_text(encoding="utf-8")
    tree = ast.parse(source)
    compiled = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compile"
    ]
    assert not compiled, f"the production owner compiles a pattern at {compiled}"

    def denotes_shape(value: str) -> bool:
        return ENVELOPED_SHA256_PATTERN.fullmatch(value.strip("^$")) is not None

    stated = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and denotes_shape(node.value)
    ]
    assert not stated, f"the production owner states a whole-value digest: {stated}"


def test_a_valid_bare_digest_still_matches_the_bare_shape() -> None:
    # Positive control: the rejection above is about the envelope, not about the
    # digest alphabet being mis-validated.
    digest = hashlib.sha256(b"x").hexdigest()
    assert BARE_SHA256_PATTERN.fullmatch(digest)
    assert digest_envelope.sha256_envelope(b"x") == f"sha256:{digest}"
