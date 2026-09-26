"""One owner decides what a credential-shaped value looks like.

Before this change five surfaces each compiled their own credential test, and no
two agreed: `control_plane.runtime.public_safety` recognized a GitHub token or a
JWT while `decision_context`, `material_lifecycle`, `periodic_report` and
`extensions.presentation` did not, and two of those four carried the same
alternation list copied byte for byte. A value that one boundary treated as a
secret therefore reached a published surface through another boundary that never
heard of that shape.

`SECRET_LIKE_SURFACE_PATTERN` is now the single shape owner, widened to the union
of the shapes every site already guarded. Each site keeps only its own threshold
policy (how short a value still counts), which is a per-surface judgement, and
consults the owner for the shapes. These tests pin the wiring, not just the
pattern, so a site that quietly stops consulting the owner goes red.
"""

import pathlib
from collections.abc import Callable

import pytest

from loopx.capabilities.decision_context.packets import _compact_text as decision_text
from loopx.capabilities.material_lifecycle._validation import (
    compact_text as material_text,
)
from loopx.capabilities.periodic_report.core import (
    _reject_raw_keys as reject_report_keys,
)
from loopx.control_plane.runtime.public_safety import (
    SECRET_LIKE_SURFACE_PATTERN,
    validate_public_safe_value,
)
from loopx.extensions.presentation import _plain_text as presentation_text

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_MODULE = "loopx/control_plane/runtime/public_safety.py"
# One consumer decides a different question: it rejects a goal id whose *whole*
# value is a provider token, so its list is anchored and cannot be reused as a
# surface scan. Named here so a new surface copy still fails this test.
ANCHORED_WHOLE_VALUE_ID_RULES = frozenset(
    {"loopx/extensions/openviking_semantic_preference/history_export.py"}
)

CREDENTIAL_SHAPES = {
    "github token": "ghp_" + "a" * 36,
    "jwt": "eyJ" + "h" * 12 + "." + "a" * 12 + "." + "s" * 12,
    "password assignment": "password=hunter2hunter2",
    "api key colon": "api_key: qwertyuiopasdfghjkl",
    "secret assignment": "secret=abcdefghijklmn",
    "token assignment": "token=abcdefghijklmn",
    "access token assignment": "access_token=abcdefghijklmn",
    "bearer value": "Bearer " + "z" * 20,
    "signed key pair": "sk-" + "a" * 30,
    "private key material": "-----BEGIN RSA PRIVATE KEY-----",
    "openssh key material": "---- BEGIN OPENSSH PRIVATE KEY ----",
    "fine-grained pat": "github_pat_" + "a1B2" * 8,
    "aws access id": "AKIA" + "A1B2C3D4E5F6G7H8",
    "slack token": "xoxb-" + "a1B2c3D4e5F6g7H8",
    "google api key": "AIza" + "a1B2c3D4e5F6g7H8i9J0K1L2",
    "stripe live key": "sk_live_" + "a1B2c3D4e5F6g7H8",
    "npm token": "npm_" + "a1B2c3D4e5F6g7H8i9J0K1L2",
}

TEXT_SITES: list[tuple[str, Callable[[str], object], str]] = [
    (
        "decision_context",
        lambda text: decision_text(text, field="summary"),
        "contains a credential-like value",
    ),
    (
        "material_lifecycle",
        lambda text: material_text(text, field="summary"),
        "contains a credential-like value",
    ),
    (
        "extensions.presentation",
        lambda text: presentation_text(text, context="label"),
        "credential material",
    ),
]


@pytest.mark.parametrize("shape", sorted(CREDENTIAL_SHAPES))
def test_the_owner_recognizes_every_shape_a_site_used_to_guard(shape: str) -> None:
    assert SECRET_LIKE_SURFACE_PATTERN.search(CREDENTIAL_SHAPES[shape])


@pytest.mark.parametrize(
    "site,call,reason", TEXT_SITES, ids=[site for site, _, _ in TEXT_SITES]
)
@pytest.mark.parametrize("shape", sorted(CREDENTIAL_SHAPES))
def test_every_text_site_rejects_a_shape_the_owner_recognizes(
    site: str, call: Callable[[str], object], reason: str, shape: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        call(CREDENTIAL_SHAPES[shape])


@pytest.mark.parametrize("shape", sorted(CREDENTIAL_SHAPES))
def test_public_payload_values_cannot_carry_a_credential_shape(shape: str) -> None:
    with pytest.raises(ValueError, match="credential-like value"):
        validate_public_safe_value({"note": CREDENTIAL_SHAPES[shape]})


@pytest.mark.parametrize("shape", sorted(CREDENTIAL_SHAPES))
def test_periodic_report_rejects_a_shape_the_owner_recognizes(shape: str) -> None:
    with pytest.raises(ValueError, match="credential-like value"):
        reject_report_keys({"summary": CREDENTIAL_SHAPES[shape]}, "report")


def test_benign_text_still_passes_the_owner() -> None:
    for text in (
        "weekly cadence digest rendered for goal_42",
        "token budget left for this stage: 1200",
        "the operator rotated the deploy credentials yesterday",
    ):
        assert not SECRET_LIKE_SURFACE_PATTERN.search(text), text


@pytest.mark.parametrize(
    "identity_marker",
    [r"gh[pousr]_", r"\beyj"],
)
def test_an_identity_shape_is_declared_in_one_owner_only(identity_marker: str) -> None:
    sources = [
        path
        for path in REPOSITORY_ROOT.glob("loopx/**/*.py")
        if path.relative_to(REPOSITORY_ROOT).as_posix() != OWNER_MODULE
        and path.relative_to(REPOSITORY_ROOT).as_posix()
        not in ANCHORED_WHOLE_VALUE_ID_RULES
        and identity_marker in path.read_text(encoding="utf-8")
    ]

    assert [path.relative_to(REPOSITORY_ROOT).as_posix() for path in sources] == []
    # Guard the guard: the owner must still declare the marker literally, or this
    # test would pass even after the owner lost the shape entirely.
    owner_source = (REPOSITORY_ROOT / OWNER_MODULE).read_text(encoding="utf-8")
    assert identity_marker in owner_source
