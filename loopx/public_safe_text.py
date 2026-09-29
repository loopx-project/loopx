"""Canonical private-looking-text rules for public-safe control-plane fields.

This module is the single owner of "does this string look private?" for the
control plane. It answers two questions that used to be conflated:

* **Detection** -- recognize a credential shape, a local path, a raw remote
  location, or an internal organizational marker, and return an explicit
  *category* plus a *reason* so a caller can decide what to do.
* **Permission** -- a named *policy* selects which categories a given surface
  rejects. Detection recognizing a value never implies every surface must
  reject it: owner-private operational state and repository/PR publication have
  different disclosure boundaries (Refs #5136).

Four real validator owners enforce the same text contract: `feedback`,
`authority`, `boundary_authority`, and the TypeScript Vision checkpoint. The
rule set used to be copied into each owner, and the copies drifted: one still
rejected the ordinary English word "authorization" while another accepted a
quoted-JSON credential header. The three Python owners now share this module,
and the TypeScript owner mirrors the same semantics; the shared corpus in
`tests/fixtures/public_safe_text_corpus.json` pins both runtimes to one
contract.

`control_plane/runtime/public_safety.py` also consumes the shape definitions
here (`SECRET_LIKE_SURFACE_PATTERN`, `LOCAL_PATH_SURFACE_PATTERN`,
`REMOTE_LOCATION_SURFACE_PATTERN`) instead of owning a competing set, so a
caller such as `artifact_lifecycle` can make one policy-aware call rather than
OR-ing independent detectors.

Each owner keeps its own error message and guidance, because those describe
the owning surface, not the shared rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Explicit categories. A caller names a policy (a set of categories) rather
# than reaching for a bare regex, so "recognized" and "rejected here" stay
# separate decisions (Refs #5136, direction 2).
# ---------------------------------------------------------------------------
CATEGORY_CREDENTIAL = "credential"
CATEGORY_LOCAL_PATH = "local_path"
CATEGORY_REMOTE_LOCATION = "remote_location"
CATEGORY_ORG_MARKER = "org_marker"

ALL_CATEGORIES: frozenset[str] = frozenset(
    {
        CATEGORY_CREDENTIAL,
        CATEGORY_LOCAL_PATH,
        CATEGORY_REMOTE_LOCATION,
        CATEGORY_ORG_MARKER,
    }
)


# Credential shape, not the plain English word. LoopX governance prose says
# "owner authorization" constantly, so the trigger is the header/assignment
# shape. The optional quote covers the JSON key form
# `{"Authorization": "Basic ..."}`, which a bare `name\s*[:=]` rule misses.
_AUTHORIZATION_CREDENTIAL_SHAPE = re.compile(
    r"\b" + "Author" + r"ization" + "[\"']?" + r"\s*[:=]",
    re.I,
)

# A credential value can also appear without its header name. Require a
# contiguous base64-shaped run so ordinary prose cannot match: no spaces or
# hyphens, at least 16 characters, and both cases present. "basic
# control-plane contract" fails every one of those conditions.
_BASIC_CREDENTIAL_VALUE = re.compile(
    "[Bb]" + "as" + r"ic\s+"
    r"(?=[A-Za-z0-9+/=]*[a-z])"
    r"(?=[A-Za-z0-9+/=]*[A-Z])"
    r"[A-Za-z0-9+/=]{16,}",
)

# Refs #5136: relocated here from control_plane/runtime/public_safety.py so a
# single owner defines each shape. public_safety re-exports these names, so its
# ~8 direct importers and 30+ recursive-validation callers are unchanged. This
# pattern is byte-identical to the one public_safety enforced before the move:
# slice A is a behavior-preserving consolidation, so no existing consumer's
# verdict changes.
LOCAL_PATH_SURFACE_PATTERN = re.compile(
    r"(?<![:/A-Za-z0-9])(?:"
    r"/(?:Users|home|Volumes|private|tmp|var|etc|opt|srv|mnt|root|data|workspace|workspaces)/"
    r"[^\s`'\"<>]+|"
    r"[A-Za-z]:[\\/][^\s`'\"<>]+|"
    r"\\\\[A-Za-z0-9_.-]+\\[^\s`'\"<>]+"
    r")",
    re.IGNORECASE,
)
# Refs #5136, direction 3: the local-path shapes this owner recognizes.
# `LOCAL_PATH_SURFACE_PATTERN` carries the absolute roots, Windows drive letters
# and UNC shares; the gap pair adds the home-relative `~/...` form and a local
# path behind an explicit `path:` prefix. Whether a surface *rejects* what this
# owner recognizes stays the caller's named policy, so a surface can still opt
# into the narrower legacy set by asking for `LOCAL_PATH_SURFACE_PATTERN` alone.
# `file://` is deliberately not in this set: direction 3 does classify it as a
# local path, but every surface that has to stop carrying one already rejects it
# here as a raw remote location, and the surfaces that keep ordinary URLs would
# need a per-surface decision rather than a shared-pattern change.
HOME_RELATIVE_PATH_PATTERN = re.compile(r"(?<![\w~])~[\\/][^\s`'\"<>]+")
PATH_PREFIX_LOCAL_PATTERN = re.compile(
    r"(?<![\w:])path:[\\/][^\s`'\"<>]+", re.IGNORECASE
)
LOCAL_PATH_GAP_PATTERNS: tuple[re.Pattern[str], ...] = (
    HOME_RELATIVE_PATH_PATTERN,
    PATH_PREFIX_LOCAL_PATTERN,
)
# The boundary form one pair of migrating surfaces already enforced: a local
# reference introduced by `:` or `=`. `LOCAL_PATH_SURFACE_PATTERN`'s lookbehind
# deliberately skips a preceding colon, so without this arm a surface moving
# onto the shared decision would start accepting `:/Users/...` -- a loosening
# no migration is allowed to introduce.
LOCAL_PATH_BOUNDARY_REFERENCE_PATTERN = re.compile(
    r"(?:^|[\s:=])(?:/Users/|/private/|/tmp/|~[/\\])",
    re.IGNORECASE,
)
PUBLIC_SAFE_LOCAL_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    LOCAL_PATH_SURFACE_PATTERN,
    HOME_RELATIVE_PATH_PATTERN,
    PATH_PREFIX_LOCAL_PATTERN,
    LOCAL_PATH_BOUNDARY_REFERENCE_PATTERN,
)
# Refs #5136: one definition for "this string carries a raw remote location".
# Three validators each restated the same scheme list, and the canonical
# public-safety owner had no counterpart, so a fourth caller had to invent one.
REMOTE_LOCATION_SURFACE_PATTERN = re.compile(r"(?i)\b(?:https?|file|s3|gs|tos|hdfs)://")
SECRET_LIKE_SURFACE_PATTERN = re.compile(
    r"(?i)(?:\bbearer\s+[a-z0-9._~+/=-]{16,}|"
    r"\b(?:access|api|secret)[_-]?key[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]+|"
    r"\b(?:ak|sk)[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]+|"
    r"(?<![a-z0-9_])(?:ak|sk)[-_=:][a-z0-9_=-]{10,}|"
    r"\bgh[pousr]_[a-z0-9]{16,}\b|"
    r"\bgithub_pat_[a-z0-9_]{20,}|"
    r"\b(?:akia|asia)[a-z0-9]{16}\b|"
    r"\bxox[baprs]-[a-z0-9-]{10,}|"
    r"\baiza[a-z0-9_-]{20,}|"
    r"\b(?:sk|rk)_(?:live|test)_[a-z0-9]{12,}|"
    r"\bnpm_[a-z0-9]{20,}|"
    r"\bpypi-[a-z0-9_-]{20,}|"
    r"\beyj[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\b|"
    r"\b(?:access|refresh)[_-]?token[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]{12,}|"
    r"\b(?:password|secret)[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]{12,}|"
    r"-{3,}\s*BEGIN (?:[A-Z]+ )?PRIVATE KEY|"
    r"\btoken[\"']?\s*[=:]\s*[\"']?[^\s`'\"<>]{12,})"
)


# The text-owner rule set (feedback / authority / boundary_authority / the
# TypeScript Vision checkpoint). Each entry carries an explicit category and a
# stable reason so `classify_private_text` can hand a caller a named verdict
# instead of a bare regex object. Order is significant: `find_private_text_match`
# returns the first match, and the shared corpus pins that first-match contract.
@dataclass(frozen=True)
class _CategorizedPattern:
    pattern: re.Pattern[str]
    category: str
    reason: str


_CATEGORIZED_PRIVATE_TEXT_PATTERNS: tuple[_CategorizedPattern, ...] = (
    _CategorizedPattern(
        re.compile(r"/" + r"Users/"), CATEGORY_LOCAL_PATH, "absolute home-directory path"
    ),
    _CategorizedPattern(
        re.compile(r"/" + r"ext_data/"), CATEGORY_ORG_MARKER, "internal ext_data path"
    ),
    _CategorizedPattern(
        re.compile("la" + "rk" + "office", re.I),
        CATEGORY_ORG_MARKER,
        "internal Lark/Feishu office marker",
    ),
    _CategorizedPattern(
        re.compile("docs" + r"\." + "internal", re.I),
        CATEGORY_ORG_MARKER,
        "internal docs host marker",
    ),
    _CategorizedPattern(
        re.compile(r"\bt-20\d{12}-[a-z0-9]+\b"),
        CATEGORY_ORG_MARKER,
        "internal ticket identifier",
    ),
    _CategorizedPattern(
        re.compile(r"\b" + "Bear" + r"er\b", re.I),
        CATEGORY_CREDENTIAL,
        "bearer auth scheme word",
    ),
    _CategorizedPattern(
        _AUTHORIZATION_CREDENTIAL_SHAPE,
        CATEGORY_CREDENTIAL,
        "authorization header/assignment shape",
    ),
    _CategorizedPattern(
        _BASIC_CREDENTIAL_VALUE, CATEGORY_CREDENTIAL, "basic-auth credential value"
    ),
    _CategorizedPattern(
        re.compile(r"\b" + "tok" + r"en\s*=", re.I),
        CATEGORY_CREDENTIAL,
        "token assignment shape",
    ),
    _CategorizedPattern(
        re.compile(r"\b" + "pass" + r"word\b", re.I),
        CATEGORY_CREDENTIAL,
        "password word",
    ),
    _CategorizedPattern(
        re.compile(r"\b" + "sec" + r"ret\b", re.I),
        CATEGORY_CREDENTIAL,
        "secret word",
    ),
)

# Kept as the plain pattern tuple so `find_private_text_match` and every
# existing importer see byte-identical behavior (same patterns, same order).
PRIVATE_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    entry.pattern for entry in _CATEGORIZED_PRIVATE_TEXT_PATTERNS
)


# ---------------------------------------------------------------------------
# Named policies. A policy is the set of categories a surface rejects. Keeping
# these named (rather than inline regex ORs at each caller) is what lets one
# detection owner serve surfaces with different disclosure boundaries.
# ---------------------------------------------------------------------------
# The four text owners reject every recognized category (their historical
# behavior): credential shapes, local paths, remote locations, org markers.
TEXT_OWNER_CATEGORIES: frozenset[str] = ALL_CATEGORIES
# artifact_lifecycle historically OR-ed find_private_text_match (the text-owner
# set) with SECRET_LIKE_SURFACE_PATTERN, downstream of a validate_public_safe_value
# call that already rejected the local-path and credential shapes. That union
# covers every category *except* a raw remote location: this projection has
# always let an ordinary http(s) URL through. The policy preserves that exactly
# rather than silently tightening it; widening it to remote_location is a
# separate, disclosed decision (Refs #5136, direction 2).
ARTIFACT_LIFECYCLE_CATEGORIES: frozenset[str] = frozenset(
    {CATEGORY_CREDENTIAL, CATEGORY_LOCAL_PATH, CATEGORY_ORG_MARKER}
)


@dataclass(frozen=True)
class PrivateTextMatch:
    """An explicit, categorized private-text detection result."""

    category: str
    reason: str
    pattern: re.Pattern[str]


def find_private_text_match(value: str | None) -> re.Pattern[str] | None:
    """Return the first matching private-text pattern, or None when clean.

    Preserved verbatim for the four text owners and the shared corpus parity
    test; `classify_private_text` is the category-aware successor.
    """

    if not value:
        return None
    for pattern in PRIVATE_TEXT_PATTERNS:
        if pattern.search(value):
            return pattern
    return None


# The shape-based detectors, categorized. These supplement the text-owner
# patterns so a single call can cover both owners that artifact_lifecycle used
# to OR together.
_SHAPE_DETECTORS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (SECRET_LIKE_SURFACE_PATTERN, CATEGORY_CREDENTIAL, "credential-like value shape"),
    (LOCAL_PATH_SURFACE_PATTERN, CATEGORY_LOCAL_PATH, "local filesystem path"),
    (
        REMOTE_LOCATION_SURFACE_PATTERN,
        CATEGORY_REMOTE_LOCATION,
        "raw remote location URL",
    ),
)


def find_public_safe_local_path(value: str | None) -> re.Pattern[str] | None:
    """Return the local-path shape ``value`` carries, or None when it carries none.

    This is the single answer to "is there a local path in this text" for
    surfaces that publish outside the runtime (Refs #5136, direction 3): the
    absolute roots, the two gap shapes `classify_private_text` reaches only when
    a caller opts into `include_path_gaps`, and the colon/equals boundary form
    the migrated surfaces already enforced. Recognition is still not permission:
    a caller that must keep a narrower historical verdict asks for
    `LOCAL_PATH_SURFACE_PATTERN` directly, and each surface keeps its own
    rejection message and length limit.
    """

    if not value:
        return None
    for pattern in PUBLIC_SAFE_LOCAL_PATH_PATTERNS:
        if pattern.search(value):
            return pattern
    return None


def classify_private_text(
    value: str | None,
    *,
    categories: frozenset[str] = ALL_CATEGORIES,
    include_path_gaps: bool = False,
) -> PrivateTextMatch | None:
    """Return the first recognized private-text match within ``categories``.

    Detection only: recognizing a value does not decide whether a given surface
    may publish it. Callers pass the named policy (category set) for their
    destination. The text-owner patterns are checked first, in their pinned
    order, then the relocated shape detectors, so a value that both owners used
    to flag still resolves to a single explicit category and reason.

    ``include_path_gaps`` opts a surface into the direction-3 recognition of
    home-relative (``~/``) and ``path:``-prefixed local references. It defaults
    to False so this consolidation does not silently tighten any surface that
    has not chosen the wider policy.
    """

    if not value:
        return None
    for entry in _CATEGORIZED_PRIVATE_TEXT_PATTERNS:
        if entry.category in categories and entry.pattern.search(value):
            return PrivateTextMatch(entry.category, entry.reason, entry.pattern)
    for pattern, category, reason in _SHAPE_DETECTORS:
        if category in categories and pattern.search(value):
            return PrivateTextMatch(category, reason, pattern)
    if include_path_gaps and CATEGORY_LOCAL_PATH in categories:
        for pattern in LOCAL_PATH_GAP_PATTERNS:
            if pattern.search(value):
                return PrivateTextMatch(
                    CATEGORY_LOCAL_PATH, "local path behind a relative/prefixed form", pattern
                )
    return None


def matches_private_text_policy(
    value: str | None,
    *,
    categories: frozenset[str] = ALL_CATEGORIES,
    include_path_gaps: bool = False,
) -> bool:
    """True when ``value`` is recognized within the named policy's categories."""

    return (
        classify_private_text(
            value, categories=categories, include_path_gaps=include_path_gaps
        )
        is not None
    )
