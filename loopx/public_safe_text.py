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

The four owners validate LoopX's own state, so they share one policy,
`TEXT_OWNER_CATEGORIES`, which recognizes a bare credential word without
rejecting it. Repository-publication surfaces keep the full set
(`ALL_CATEGORIES`, reached through `find_private_text_match`), because a PR-time
scan of development code is held to the stricter bar the maintainer asked for in
#5136: internal state may be looser, publication may not.

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
CATEGORY_CREDENTIAL_WORD = "credential_word"
CATEGORY_LOCAL_PATH = "local_path"
CATEGORY_REMOTE_LOCATION = "remote_location"
CATEGORY_ORG_MARKER = "org_marker"

ALL_CATEGORIES: frozenset[str] = frozenset(
    {
        CATEGORY_CREDENTIAL,
        CATEGORY_CREDENTIAL_WORD,
        CATEGORY_LOCAL_PATH,
        CATEGORY_REMOTE_LOCATION,
        CATEGORY_ORG_MARKER,
    }
)

# Refs #5136, direction 2: a *mention* of a credential word is not a credential.
# ``credential_word`` exists so a surface can recognize these words without
# rejecting them; the value and assignment forms stay in ``credential``, so
# narrowing one policy can never release a short password value along with the
# prose.
#
# The credential-value contract has exactly four independent signals. Each one
# is a shape fact about the text next to the label, which is what lets the two
# directions be decided by different rules instead of by one length:
#
#   assignment_punctuation -- ``label:`` or ``label=`` carries whatever follows,
#                             including when the label is a quoted object key;
#                             the label already asserts an assignment, so no
#                             value floor applies (``LABELED_CREDENTIAL_...``).
#   shaped_value_token -- a connector (whitespace, comma, semicolon, dash) or a
#                             copula ("is", "set to") followed by a token
#                             containing a digit or one of the base64-only
#                             characters ``+ / =``.
#   quoted_value          -- the same connector followed by a quoted run.
#   opaque_value_run      -- the same connector followed by an unbroken
#                             letter-only run of ``OPAQUE_VALUE_MIN_LENGTH`` or
#                             more, which is how an assembled token with no digit
#                             is still caught.
#
# ``OPAQUE_VALUE_MIN_LENGTH`` is the only length in this contract, and it is a
# *word-length* ceiling, not a token floor: it exists to keep an ordinary English
# word beside the label out of the value class. Fifteen is above the operational
# prose seen in this repository ("authentication", "administrator",
# "responsibilit" plus a suffix) and the value arms no longer depend on it for
# short assembled tokens -- those are caught by shape at any length.
#
# The residual is stated rather than hidden: a value written with no digit, no
# ``+/=``, no quotes, and fifteen letters or fewer is prose to this owner. That is
# why the internal-state tier documents itself as not a credential-storage
# exemption, and why the publication tier keeps the bare words.
OPAQUE_VALUE_MIN_LENGTH = 16

CREDENTIAL_LABEL_PATTERN_SOURCE = (
    "(?:" + "Bear" + r"er|tok" + r"en|pass" + r"word|sec" + r"ret)"
)
# One connector definition, shared by the two connector arms so the Python and
# TypeScript owners cannot drift on which separators and copulas count. `:` and
# `=` are deliberately absent: the assignment arm already carries anything after
# them, so leaving them here would give one spelling two owners and make the named
# reason depend on list order.
CREDENTIAL_VALUE_CONNECTOR_PATTERN_SOURCE = (
    r"(?:\s*[,;-]\s*|\s+(?:set\s+to|is|are|was|were|set|to|of|with)\b|\s+)\s*"
)
# A run that carries a digit or a base64-only character. The signal has no
# length floor: a one-character value still carries a credential value, and the
# assignment arm already makes the same length-independent decision.
_SHAPE_VALUE_TOKEN_SOURCE = (
    r"(?=[A-Za-z0-9._~+/=-]*[0-9+/=])[A-Za-z0-9._~+/=-]+"
)
_OPAQUE_VALUE_RUN_SOURCE = r"[A-Za-z]{%d,}" % OPAQUE_VALUE_MIN_LENGTH

# A credential label reached through a connector, with a token next to it that
# looks assembled. This is the arm that replaced the bearer-only length floor: it
# covers the copula and punctuation spellings a bare `\s+` never reached, and it
# no longer rejects an ordinary English word just because it is long-ish.
CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN = re.compile(
    r"\b"
    + CREDENTIAL_LABEL_PATTERN_SOURCE
    + CREDENTIAL_VALUE_CONNECTOR_PATTERN_SOURCE
    + r"(?:"
    + _SHAPE_VALUE_TOKEN_SOURCE
    + "|"
    + _OPAQUE_VALUE_RUN_SOURCE
    + r")",
    re.IGNORECASE,
)

# The same connector followed by a quoted run. Quoting is its own signal: the
# value class does not depend on the quoted text looking like a token, so a
# letter-only passphrase written as a quoted value is still rejected.
QUOTED_CREDENTIAL_VALUE_SHAPE_PATTERN = re.compile(
    r"\b"
    + CREDENTIAL_LABEL_PATTERN_SOURCE
    + CREDENTIAL_VALUE_CONNECTOR_PATTERN_SOURCE
    + r"[\"'][^\"'\n]+[\"']",
    re.IGNORECASE,
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

# An assignment form of the four words: an optional closing quote around the key,
# either separator, and any value. It carries no value-length floor on purpose,
# because naming a credential label next to an operator already states an
# assignment. For the password and secret words this arm is no tightening -- the
# old word-only rule rejected every mention of them, assignments included -- while
# the colon spellings of token and bearer are the two forms it did not know.
# Direction 2 asks that an assignment never depend on a length accident.
LABELED_CREDENTIAL_ASSIGNMENT_PATTERN = re.compile(
    r"\b(?:" + "Bear" + r"er|tok" + r"en|pass" + r"word|sec" + r"ret)[\"']?\s*[:=]",
    re.I,
)

# A credential label reached inside a field name rather than as a free-standing
# word: ``db_password = 'S3cret!value'``, ``password_hash=Qwerty1234567890``,
# ``client_secret: abcdef123456``. Every label arm above anchors the label with
# ``\\b``, and ``_`` is a word character, so a label glued to a field-name prefix
# or suffix is invisible to all of them -- the two capability faces caught that
# spelling with a substring rule of their own (Refs #5136, direction 1).
#
# The value carries no test, which is what the free-standing assignment arm above
# already does: an operator beside a credential label states an assignment, so a
# short or quoted value such as ``client_secret="hunter"`` cannot be released by a
# digit or word-length accident (Refs #5136, direction 2; those rows are the
# direction-4 counterexamples the migrated callers still lacked). The residual
# runs the other way: the field-name suffix also absorbs prose that ends on the
# label's plural before an operator, ``secrets:`` included, which the
# free-standing arm's ``\\b`` does not reach. That is why this arm stays an
# opt-in rather than a member of the ``credential`` category.
_COMPOUND_LABEL_SOURCE = "pass" + r"word|sec" + r"ret|api" + r"[_-]?key"
COMPOUND_CREDENTIAL_FIELD_ASSIGNMENT_PATTERN = re.compile(
    r"[A-Za-z0-9_]*(?:" + _COMPOUND_LABEL_SOURCE + r")[A-Za-z0-9_]*[\"']?\s*[:=]",
    re.IGNORECASE,
)
# The credential half of the rule, on its own, named so the two capability faces
# that ask it share one definition instead of each restating a category pair. Both
# categories are listed rather than subtracted from `ALL_CATEGORIES`:
# `credential_word` is one of the two, and a policy written as a difference would
# drop it -- loosening a face that never asked to be loosened -- the next time a
# category is added.
CREDENTIAL_CATEGORIES: frozenset[str] = frozenset(
    {CATEGORY_CREDENTIAL, CATEGORY_CREDENTIAL_WORD}
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
# Typed public exports also reject file URLs, including host-qualified ones.
# The internal text-owner classifier and legacy compactor retain their separate
# policies; recognizing a locator here does not change those destinations.
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
FILE_URL_LOCAL_PATH_PATTERN = re.compile(r"\bfile://", re.IGNORECASE)
PUBLIC_SAFE_LOCAL_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    LOCAL_PATH_SURFACE_PATTERN,
    HOME_RELATIVE_PATH_PATTERN,
    PATH_PREFIX_LOCAL_PATTERN,
    LOCAL_PATH_BOUNDARY_REFERENCE_PATTERN,
    FILE_URL_LOCAL_PATH_PATTERN,
)
# Presentation redaction keeps its historical Unix-root boundary behavior (it
# catches paths even after a colon), consumes the shared absolute, drive-letter
# and UNC detector, and recognizes extended Windows device paths for display
# only. Those extended forms remain outside the shared state-owner contract.
# Keeping these definitions here lets presentation choose its own redaction
# policy without changing other callers (Refs #5136, direction 3).
PRESENTATION_COLON_PREFIXED_WINDOWS_PATH_PATTERN = re.compile(
    r"(?<=:)(?:"
    r"[A-Za-z]:[\\/][^\s`|,)]+|"
    r"\\\\\?\\(?:(?i:UNC)\\[A-Za-z0-9_.-]+\\|(?i:Volume)\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}\\)[^\s`|,)]+|"
    r"\\\\\?\\(?i:GLOBALROOT\\Device\\)[^\s`|,)]+|"
    r"\\\\[A-Za-z0-9_.-]+\\[^\s`|,)]+"
    r")"
)
PRESENTATION_EXTENDED_WINDOWS_PATH_PATTERN = re.compile(
    r"\\\\\?\\(?:(?i:UNC)\\[A-Za-z0-9_.-]+\\|"
    r"(?i:Volume)\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}\\|"
    r"(?i:GLOBALROOT\\Device\\))[^\s`|,)]+",
    re.IGNORECASE,
)
PRESENTATION_LOCAL_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/(?:Users|home|private|tmp|var)/[^\s`|,)]+"),
    LOCAL_PATH_SURFACE_PATTERN,
    PRESENTATION_EXTENDED_WINDOWS_PATH_PATTERN,
    PRESENTATION_COLON_PREFIXED_WINDOWS_PATH_PATTERN,
)
PRESENTATION_PUBLIC_BOUNDARY_PATTERNS: tuple[
    tuple[str, re.Pattern[str]], ...
] = (
    (
        "absolute local path",
        re.compile(
            r"/(?:Users|home|private|tmp|var)/[^\s`\"'<>]+|"
            + "(?:"
            + LOCAL_PATH_SURFACE_PATTERN.pattern
            + "|"
            + PRESENTATION_EXTENDED_WINDOWS_PATH_PATTERN.pattern
            + ")|"
            + PRESENTATION_COLON_PREFIXED_WINDOWS_PATH_PATTERN.pattern
        ),
    ),
    (
        "private key material",
        re.compile(r"BEGIN (?:RSA |OPENSSH |EC |)PRIVATE KEY"),
    ),
    (
        "credential assignment",
        re.compile(
            r"\b(?:api[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]",
            re.IGNORECASE,
        ),
    ),
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

# Shared identifier shapes stay here with the sibling public-safe text shapes.
# They express syntax only: each consuming contract retains its own field names,
# error text, and any additional validation policy.
PUBLIC_SAFE_REFERENCE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$"
)
COMPACT_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# One definition for "a compact, path-safe opaque identifier". Four contracts each
# compiled this body under their own name -- the Chat action surface, the Chat
# action store, a Goal reference validator and the BotMux runtime -- and a fifth
# waits in the goal-deletion service, so a bound fix had five places to land. Each
# consumer keeps its own field names and error text; this states syntax only, and
# a value this shape accepts is not yet a claim that the destination may publish
# it.
OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
MODULE_QUALIFIED_SURFACE_PATTERN = re.compile(
    r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$"
)


# One definition for "this identifier is a lowercase public-safe slug". Four
# periodic-report surfaces and one hand-off validator each compiled the same
# shape locally; the four report files are being edited by another branch of
# mine right now, so this slice converts the three free ones and the guard
# declares the other four by file and count.
PUBLIC_SAFE_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


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
        CONNECTED_CREDENTIAL_VALUE_SHAPE_PATTERN,
        CATEGORY_CREDENTIAL,
        "credential label carrying a shaped value",
    ),
    _CategorizedPattern(
        QUOTED_CREDENTIAL_VALUE_SHAPE_PATTERN,
        CATEGORY_CREDENTIAL,
        "credential label carrying a quoted value",
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
        LABELED_CREDENTIAL_ASSIGNMENT_PATTERN,
        CATEGORY_CREDENTIAL,
        "credential-word assignment shape",
    ),
    # The three word arms are the false-positive set direction 2 asked to move
    # out of the rejection rule. They stay recognized -- under their own category
    # -- so a surface that wants the older, stricter verdict opts back in by name.
    _CategorizedPattern(
        re.compile(r"\b" + "Bear" + r"er\b", re.I),
        CATEGORY_CREDENTIAL_WORD,
        "bearer auth scheme word",
    ),
    _CategorizedPattern(
        re.compile(r"\b" + "pass" + r"word\b", re.I),
        CATEGORY_CREDENTIAL_WORD,
        "password word",
    ),
    _CategorizedPattern(
        re.compile(r"\b" + "sec" + r"ret\b", re.I),
        CATEGORY_CREDENTIAL_WORD,
        "secret word",
    ),
)

# The publication-tier tuple, derived from the categorized list so the two can
# never drift. It is not the pre-#5136 list: the two value arms were added and
# the three word arms moved last, so a first match over an assignment form names
# the shape rather than the word. `PRIVATE_TEXT_PATTERNS` stays byte-for-byte
# what a surface rejects when it asks for every category.
PRIVATE_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    entry.pattern for entry in _CATEGORIZED_PRIVATE_TEXT_PATTERNS
)


# ---------------------------------------------------------------------------
# Named policies. A policy is the set of categories a surface rejects. Keeping
# these named (rather than inline regex ORs at each caller) is what lets one
# detection owner serve surfaces with different disclosure boundaries.
# ---------------------------------------------------------------------------
# The four text owners validate LoopX's own operational state, and the
# maintainer set their strictness below the repository-publication surface:
# "the Bearer token expired" or "read the secret from the environment" describes
# a fact, it does not carry one (Refs #5136, direction 2). Two categories stay
# out of their policy. `credential_word` is the loosening direction 2 asked for.
# `remote_location` is not a loosening but a non-change: the word-only rule these
# owners enforced never rejected an ordinary URL, and deciding per face whether
# an internal-state field may carry one is the remaining caller-migration work in
# #5136, not a verdict this PR is authorized to add.
# The migration onto the classifier is still a net tightening where direction 2
# asked for one: these owners now reject credential *values* that arrive with no
# label at all -- a raw GitHub token, a Slack token, a private key block -- which
# the word arms never covered, plus every local-path root rather than only
# `/Users/`.
TEXT_OWNER_CATEGORIES: frozenset[str] = ALL_CATEGORIES - {
    CATEGORY_CREDENTIAL_WORD,
    CATEGORY_REMOTE_LOCATION,
}
# artifact_lifecycle historically OR-ed find_private_text_match (the text-owner
# set) with SECRET_LIKE_SURFACE_PATTERN, downstream of a validate_public_safe_value
# call that already rejected the local-path and credential shapes. That union
# covers every category *except* a raw remote location: this projection has
# always let an ordinary http(s) URL through. The policy preserves that exactly
# rather than silently tightening it; widening it to remote_location is a
# separate, disclosed decision (Refs #5136, direction 2). It keeps
# ``credential_word`` on purpose: this surface never asked to be loosened, and a
# new category must not widen an existing named policy by absence.
ARTIFACT_LIFECYCLE_CATEGORIES: frozenset[str] = ALL_CATEGORIES - {
    CATEGORY_REMOTE_LOCATION
}


@dataclass(frozen=True)
class PrivateTextMatch:
    """An explicit, categorized private-text detection result."""

    category: str
    reason: str
    pattern: re.Pattern[str]


def find_private_text_match(value: str | None) -> re.Pattern[str] | None:
    """Return the first matching private-text pattern, or None when clean.

    The publication tier: this recognizes every category, including a bare
    credential word. The four internal-state owners ask the category-aware
    `classify_private_text` for their narrower policy instead, so this helper is
    what a repository-publication surface and the corpus parity test hold.
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
    the migrated surfaces already enforced, plus file URLs. Recognition is still
    not permission:
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
    include_compound_field_assignment: bool = False,
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

    ``include_compound_field_assignment`` opts a surface into
    ``COMPOUND_CREDENTIAL_FIELD_ASSIGNMENT_PATTERN``. It is an opt-in rather than
    a ``credential`` category member for the reason stated on that constant: a
    category addition would widen the four migrated text owners and the
    publication tier by absence, which is a per-face decision nobody has made.
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
    if (
        include_compound_field_assignment
        and CATEGORY_CREDENTIAL in categories
        and COMPOUND_CREDENTIAL_FIELD_ASSIGNMENT_PATTERN.search(value)
    ):
        return PrivateTextMatch(
            CATEGORY_CREDENTIAL,
            "credential field name behind an assignment operator",
            COMPOUND_CREDENTIAL_FIELD_ASSIGNMENT_PATTERN,
        )
    return None


def matches_private_text_policy(
    value: str | None,
    *,
    categories: frozenset[str] = ALL_CATEGORIES,
    include_path_gaps: bool = False,
    include_compound_field_assignment: bool = False,
) -> bool:
    """True when ``value`` is recognized within the named policy's categories."""

    return (
        classify_private_text(
            value,
            categories=categories,
            include_path_gaps=include_path_gaps,
            include_compound_field_assignment=include_compound_field_assignment,
        )
        is not None
    )
