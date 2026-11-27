from __future__ import annotations

import json
import os
import re
import subprocess
import sys as _sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_registry import registered_agent_ids_for_goal
from .control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    CanonicalTodoSnapshot,
    read_canonical_todos_if_promoted,
)
from .control_plane.goals.contract_health import (
    contract_error_diagnostic,
    contract_error_views,
    project_contract_health_for_goal,
)
from .control_plane.goals.source_integrity import verify_authority_source_digests
from .control_plane.goals.activation import (
    GoalActivationState, goal_activation_state, normalize_goal_activation_state,
)
from .control_plane.runtime.run_index_duplicates import classify_index_duplicate_records, index_identity
from .control_plane.runtime.file_reads import iter_utf8_file_reads
from .control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
from .control_plane.projects.registry_codec import load_registry
from .control_plane.todos.active_state_editing import COMPLETED_WORK_ARCHIVE_HEADING
from .control_plane.todos.active_state_todo_parser import active_state_incomplete_warning
from .control_plane.todos.authoring_scope import todo_contract_diagnostics
from .history import RunHistoryAudit, build_run_history_audit, collect_history
from .paths import (
    DEFAULT_RUNTIME_ROOT,
    LEGACY_RUNTIME_ROOT,
    display_path_text,
    display_report_text,
    rel_or_abs,
    resolve_runtime_root,
)
from .public_safe_text import PROVIDER_TOKEN_SHAPE_PATTERN_SOURCE
from .registry import inspect_registry, inspect_registry_boundary, registry_goals, resolve_state_file
from .state_projection import state_projection_gap_warning
from .control_plane.todos.contract import (
    TODO_STATUS_DEFERRED,
    TODO_STATUS_DONE,
    TODO_METADATA_PATTERN,
    TODO_TASK_CLASS_USER_ACTION,
    TODO_TASK_CLASS_USER_GATE,
    TODO_TASK_PATTERN,
    decode_metadata_value,
    normalize_todo_bound_agent,
    normalize_todo_claimed_by,
    normalize_todo_excluded_agents,
    normalize_todo_goal_bound,
    normalize_removed_todo_continuation_policy,
    normalize_todo_status,
    parse_todo_metadata_line,
    parse_todo_metadata_tokens,
    require_todo_excluded_agents,
    todo_status_from_marker,
)


# Keep the historical exports and caller patch points, but select the OS
# transport only when used. Decisions and deadlines remain with the caller.
_PROCESS_RUNTIME_EXPORTS = ("terminate_process_tree",)


def __getattr__(name: str) -> Any:
    if name not in _PROCESS_RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .extensions import process_runtime

    for export in _PROCESS_RUNTIME_EXPORTS:
        globals().setdefault(export, getattr(process_runtime, export))
    return globals()[name]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_PROCESS_RUNTIME_EXPORTS))


def _export(name: str) -> Any:
    return getattr(_sys.modules[__name__], name)


# An already selected owner retains the former import-time binding, including
# owner replacements installed before this module was imported.
if "loopx.extensions.process_runtime" in _sys.modules:
    for _name in _PROCESS_RUNTIME_EXPORTS:
        globals()[_name] = getattr(_sys.modules["loopx.extensions.process_runtime"], _name)


@dataclass(frozen=True)
class LeakRule:
    pattern: re.Pattern[str]
    required_literals: tuple[str, ...]
    # A rule match is evidence for review, not necessarily a secret. Keep
    # severity with the existing classification rule, not the runtime gate.
    blocking: bool = True

    def is_candidate(self, folded_text: str) -> bool:
        return any(literal in folded_text for literal in self.required_literals)


# Python's Unicode re.IGNORECASE matches ASCII I/i against both dotted and
# dotless I. casefold() alone does not: it expands U+0130 to i + combining dot.
# Fold those two regex-equivalent characters first. The other special matches
# (long s and Kelvin sign) are already covered by casefold().
_REGEX_IGNORECASE_ASCII_TRANSLATION = str.maketrans({"\u0130": "i", "\u0131": "i"})


def _prefilter_fold(text: str) -> str:
    if text.isascii():
        return text.lower()
    if "\u0130" in text or "\u0131" in text:
        text = text.translate(_REGEX_IGNORECASE_ASCII_TRANSLATION)
    return text.casefold()


# Required literals are only a cheap necessary condition for running a rule.
# The regular expressions remain the sole authority for classifying a leak.
LEAK_RULES = {
    "private_doc_url": LeakRule(
        pattern=re.compile(
            "|".join(["la" + "rk" + "office", "docs" + r"\." + "internal"]),
            re.I,
        ),
        required_literals=("lark" + "office", "docs" + ".internal"),
    ),
    "credential": LeakRule(
        pattern=re.compile(
            "|".join(
                [
                    "Bear" + "er" + r"\s+[A-Za-z0-9._-]+",
                    "AK" + "IA" + r"[0-9A-Z]{16}",
                    r"(?<![A-Za-z0-9_])" + "tok" + "en=",
                    r"(?<![A-Za-z0-9_])" + "pass" + "word=",
                    "Author" + "ization:",
                    # A provider token carries its issuer in the prefix, so it
                    # needs no label. The shapes come from the canonical
                    # public-safety owner rather than a second copy here: this
                    # scan gates repository publication, and a credential list
                    # that recognized only some issuers certified the rest as
                    # public-safe (Refs #5136, #7635).
                    PROVIDER_TOKEN_SHAPE_PATTERN_SOURCE,
                ]
            ),
            re.I,
        ),
        required_literals=(
            "bear" + "er",
            "ak" + "ia",
            "tok" + "en=",
            "pass" + "word=",
            "author" + "ization:",
            "ghp_",
            "gho_",
            "ghu_",
            "ghs_",
            "ghr_",
            "github_pat_",
            "akia",
            "asia",
            "xox",
            "aiza",
            "sk_live_",
            "sk_test_",
            "rk_live_",
            "rk_test_",
            "sk-proj-",
            "sk-svcacct-",
            "sk-admin-",
            "sk-ant-",
            "npm_",
            "pypi-",
            "eyj",
        ),
    ),
    # The private roots take the host's own separator, so a Windows host
    # writes the same path class natively with backslash separators. Allowing
    # a run of separators also covers serialized text, where a JSON or source
    # string literal escapes each backslash into two.
    "local_private_path": LeakRule(
        pattern=re.compile(
            r"([\\/]+"
            + "Users"
            + r"[\\/]+[^\\/\s]+[\\/]+(?:Documents|code"
            + "-"
            + r"reading)|[\\/]+ext"
            + "_data"
            + r"[\\/]+)"
        ),
        # Cheap necessary condition: every private root is followed by at
        # least one separator, whatever the host or serialization chose.
        required_literals=(
            "users/",
            "users\\",
            "ext" + "_data/",
            "ext" + "_data\\",
        ),
    ),
    "internal_task_id": LeakRule(
        pattern=re.compile(r"\bt-" + r"20\d{12}-[a-z0-9]+\b"),
        required_literals=("t-20",),
    ),
    "private_ip": LeakRule(
        pattern=re.compile(
            r"\b10\.\d+\.\d+\.\d+\b"
            r"|\b172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+\b"
            r"|\b192\.168\.\d+\.\d+\b"
        ),
        required_literals=("10.", "172.", "192.168."),
        blocking=False,
    ),
}
LEAK_PATTERNS = {name: rule.pattern for name, rule in LEAK_RULES.items()}

_PUBLIC_NPM_REGISTRY_PREFIX = "https://registry.npmjs.org/"

# The Lark developer console is a public product surface, even though its
# hostname shares the tenant-document marker used by private workspaces.
# Keep this exception host-specific so tenant URLs on sibling hosts remain
# blocked by the public/private boundary scan.
_PUBLIC_LARK_DEVELOPER_CONSOLE_HOST = re.compile(
    r"\bopen\." + "lark" + r"office\.com\b",
    re.I,
)

CREDENTIAL_KEYWORD_PATTERN = re.compile(
    "|".join(
        [
            "Bear" + "er" + r"\s+",
            r"(?<![A-Za-z0-9_])" + "tok" + "en=",
            r"(?<![A-Za-z0-9_])" + "pass" + "word=",
            "Author" + "ization:" + r"\s*",
        ]
    ),
    re.I,
)
CREDENTIAL_BEARER_KEYWORD_PATTERN = re.compile("Bear" + "er" + r"\s+", re.I)
CREDENTIAL_ENVIRONMENT_REFERENCE_PATTERN = re.compile(
    "|".join(
        [
            # Any interpolation template is a runtime expression, including
            # `${(await issue()).token}` where the expression starts with a call.
            r"\$\{",
            r"\$[A-Za-z_]",
            r"\$\(",
            r"%[A-Za-z_][A-Za-z0-9_]*%",
            r"os\.environ",
            r"os\.getenv",
            r"process\.env",
            r"Deno\.env\.get",
            r"System\.getenv",
            r"(?<![A-Za-z0-9_])getenv\(",
        ]
    )
)
CREDENTIAL_PLACEHOLDER_PATTERN = re.compile(
    r"<[^>]{1,64}>|\{\{|example|dummy|placeholder|changeme|redacted|your[-_]",
    re.I,
)
# A provider token has no keyword that separates its value from surrounding
# text, so only the exact access-key id published in the AWS documentation may
# be treated as an example. A placeholder word inside an opaque token body is
# part of the value, not an external reference (Refs #7713 review).
CREDENTIAL_DOCUMENTED_EXAMPLE_PATTERN = re.compile(r"AKIAIOSFODNN7EXAMPLE")
CREDENTIAL_HIGH_ENTROPY_VALUE_PATTERN = re.compile(r"[A-Za-z0-9._-]{20,}")
CREDENTIAL_VARIABLE_REFERENCE_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\s*[,)\]}]"
)
CREDENTIAL_BASIC_VARIABLE_REFERENCE_PATTERN = re.compile(
    r"Basic\s+\{[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\}"
    r"(?:\\[rnt])*[\"'`]?\s*$",
    re.I,
)


# These are source expressions, not arbitrary text with a low entropy score.
# Keep the original quotes until after this check: a quoted member path is a
# literal, while a bare member access or a braced lookup reads a runtime value.
_CREDENTIAL_MEMBER_PATH = (
    r"[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\['[A-Za-z_][A-Za-z0-9_]*'\]"
    r'|\["[A-Za-z_][A-Za-z0-9_]*"\])*'
)
_CREDENTIAL_BRACED_REFERENCE = re.compile(
    r"\{\s*" + _CREDENTIAL_MEMBER_PATH + r"\s*\}"
    r"(?=$|[ \t]|/?>|[\"']\s*[,)}\]])"
)
_CREDENTIAL_OBJECT_REFERENCE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
    r"\s*(?=$|[,)}\]])"
)
_CREDENTIAL_QUOTED_TEXT = re.compile(r"(['\"`])(?:\\.|(?!\1)[^\\])*\1")


def _credential_source_reference(line: str, match: re.Match[str]) -> bool:
    keyword = match.group(0).lower()
    value = line[match.end():]
    if keyword not in ("token=", "password=", "authorization:"):
        return False
    containing_span = next((
        span for span in _CREDENTIAL_QUOTED_TEXT.finditer(line)
        if span.start() < match.start() < span.end()
    ), None)
    # A field name ending its containing string/code span has no value.
    # An opening quote on an assignment's value does not satisfy this rule.
    if containing_span is not None and containing_span.end() == match.end() + 1:
        return True
    if keyword in ("token=", "password="):
        # A standalone query-parameter term in prose has no value. Do not
        # apply this to a URL string whose value starts with a space.
        prefix = line[:match.start()]
        if re.search(r"\s[?&#]$", prefix) and value[:1].isspace():
            return True
        if containing_span is not None and not re.search(
            r"(?i)(?<![a-z0-9_])(?:f|fr|rf)$", line[:containing_span.start()]
        ):
            return False
        return bool(_CREDENTIAL_BRACED_REFERENCE.match(value.lstrip()))
    if keyword == "authorization:":
        # A dotted literal in an HTTP header is not a source member access.
        return bool(
            containing_span is None
            and re.search(r"[{,]\s*$", line[:match.start()])
            and _CREDENTIAL_OBJECT_REFERENCE.match(value.lstrip())
        )
    return False


def _credential_match_is_reference(line: str, match: re.Match[str]) -> bool:
    if _credential_source_reference(line, match):
        return True
    value = line[match.start() :]
    last_keyword: str | None = None
    while True:
        keyword = CREDENTIAL_KEYWORD_PATTERN.match(value)
        if keyword is None:
            break
        last_keyword = keyword.group(0)
        value = value[keyword.end() :].lstrip(" \t\"'`")
    if last_keyword is None:
        return bool(CREDENTIAL_DOCUMENTED_EXAMPLE_PATTERN.fullmatch(match.group(0)))
    return _credential_value_is_reference(
        value, bearer=CREDENTIAL_BEARER_KEYWORD_PATTERN.fullmatch(last_keyword) is not None,
    )


def _credential_value_is_reference(value: str, *, bearer: bool = False) -> bool:
    """Classify a credential field value for source and distribution scanners."""
    value = value.lstrip(" \t\"'`")
    if not value:
        return True
    if CREDENTIAL_ENVIRONMENT_REFERENCE_PATTERN.match(value):
        return True
    if CREDENTIAL_PLACEHOLDER_PATTERN.match(value):
        return True
    # A whole Basic variable template contains no encoded credential. Anchoring
    # the complete value keeps mixed templates and literal payloads blocked.
    if CREDENTIAL_BASIC_VARIABLE_REFERENCE_PATTERN.fullmatch(value):
        return True
    if CREDENTIAL_HIGH_ENTROPY_VALUE_PATTERN.match(value):
        return False
    if bearer:
        return True
    return bool(CREDENTIAL_VARIABLE_REFERENCE_PATTERN.match(value))


def _credential_hits_are_all_references(line: str) -> bool:
    matches = list(LEAK_RULES["credential"].pattern.finditer(line))
    if not matches:
        return False
    return all(_credential_match_is_reference(line, match) for match in matches)


# A selected directory owns every file in it, so only paths whose suffix names
# a binary media or container format are dropped before reading. A seven-suffix
# allow-list used to make that decision silently: every other text file -- on
# this repository the whole TypeScript control plane plus .mjs, .tsx, .html,
# .txt, .svg and extensionless files -- was certified without ever being
# opened. Selection is suffix-based, so a text payload under a media suffix
# (for example `notes.png`) is skipped; this repository has no such file, and a
# directory walk cannot learn otherwise without opening every binary.
#
# `public_safe_outbound.scan_cli.BINARY_SUFFIXES` is the closest existing list,
# but the two answer different questions: that outbound candidate set also
# prunes build output (.map, .tsbuildinfo) and opaque packages (.bin, .war),
# while this publication boundary reads every file not listed here as UTF-8 and
# reports a decode failure as unreadable. Keeping source maps and .tsbuildinfo
# readable here means embedded source text is still examined, and .bin/.war
# stay fail-closed instead of disappearing. The shared media/container suffixes
# are common vocabulary, not a second authority: this list only decides whether
# a selected file is opened, and every content verdict still comes from the
# text rules above.
BINARY_MEDIA_SUFFIXES = {
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".avif",
    ".tif", ".tiff", ".heic", ".psd",
    # audio and video
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus",
    ".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".wmv", ".flv",
    # archives and packages
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar",
    ".whl", ".jar", ".egg", ".dmg", ".iso",
    # document containers
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # fonts
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # executables and compiled artifacts
    ".so", ".dll", ".dylib", ".exe", ".pyd", ".pyc", ".pyo",
    ".class", ".wasm", ".o", ".obj", ".a", ".lib",
    # databases
    ".db", ".sqlite", ".sqlite3", ".mdb",
}
# A normal checkout keeps Finder's .DS_Store and AppleDouble ._* files
# untracked (often gitignored), and they are not publishable input. Only the
# formats' own magic headers prove such a file is OS metadata rather than a
# text file that happens to share the name, so a directory walk skips an
# untracked file only when its header matches. A tracked file is repository
# content and stays scanned even then.
LOCAL_METADATA_HEADERS = {
    ".DS_Store": (b"\x00\x00\x00\x01Bud1",),
    "._": (b"\x00\x05\x16\x07",),
}
DEFAULT_SKIP_DIRS = {
    ".git",
    ".goal-harness",
    ".loopx",
    ".goal-wrapper.local",
    ".local",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "node_modules",
    "runtime",
}
LOCAL_PRIVATE_STATE_PARTS = {
    ".codex",
    ".goal-harness",
    ".goal-wrapper.local",
    ".local",
    ".loopx",
}
LOCAL_PRIVATE_STATE_FILE_NAMES = {"ACTIVE_GOAL_STATE.md", "ACTIVE_GOAL_STATE.md.lock"}
TERMINAL_TODO_STATUSES = {TODO_STATUS_DONE, TODO_STATUS_DEFERRED, "completed", "closed", "archived"}


# A scan reads a checkout the operator may not control, so a Git that blocks on
# a stale index lock, a network mount or an oversized index must not hold the
# scan open. Git runs helpers of its own -- an fsmonitor hook is the common one
# on `ls-files` -- so a budget that stops the caller has to stop that work too.
GIT_PROBE_TIMEOUT_SECONDS = 5.0


class GitProbeUnavailable(RuntimeError):
    """Git did not answer an ownership question inside the scan budget.

    A missing answer is not a negative one. Callers of this module decide
    whether an unanswered question lets them pass; they cannot read it as
    "not tracked", because a tracked private file would then be published.
    """


@dataclass(frozen=True)
class _GitAnswer:
    returncode: int
    stdout: str


@dataclass(frozen=True)
class _GitBytesAnswer:
    returncode: int
    stdout: bytes
    stderr: bytes


def _retire_owned_probe(process: subprocess.Popen[str] | subprocess.Popen[bytes]) -> None:
    """Stop this probe's own process group, then drop its pipes unread.

    A descendant that inherited the pipes keeps them open after the leader
    dies, so draining them would wait on work this call no longer owns.
    """

    try:
        _export("terminate_process_tree")(process, grace_seconds=0.2)
    except (OSError, RuntimeError):
        pass
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass


# Git takes its pathspec mode from the environment as well as from argv, and
# these variables are resolved after `--literal-pathspecs`, so an inherited
# `GIT_GLOB_PATHSPECS=1` turns every literal directory this module asks for
# back into a glob. Git then rejects a name that is not a valid pattern, which
# a scan would otherwise read as a clean empty selection.
_GIT_PATHSPEC_ENVIRONMENT_KEYS = (
    "GIT_GLOB_PATHSPECS",
    "GIT_NOGLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
    "GIT_LITERAL_PATHSPECS",
)


def _literal_git_environment() -> dict[str, str]:
    """The caller's environment without a pathspec mode of its own.

    Nothing else is removed: hooks, credential helpers and the repository
    discovery below keep the environment the operator set up.
    """

    environment = dict(os.environ)
    for key in _GIT_PATHSPEC_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    # Discovery failures are told apart from "there is no repository here" by
    # the message Git prints, so ask for that message in one fixed language.
    # Porcelain output this module reads is not localized either way.
    environment["LC_ALL"] = "C"
    environment["LANGUAGE"] = "C"
    return environment


def _run_git_probe(*arguments: str) -> _GitBytesAnswer | None:
    """Start one bounded local Git read; retire its process group on timeout or cancel.

    Git is started in a session of its own so an exhausted budget retires the
    helpers that read would have started along with it. That session also keeps
    a terminal Ctrl-C away from Git, so an interrupted wait retires the same
    helpers before the cancellation keeps travelling.
    """

    try:
        process = subprocess.Popen(
            ["git", *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_literal_git_environment(),
            start_new_session=os.name == "posix",
        )
    except OSError:
        return None
    try:
        stdout, stderr = process.communicate(timeout=GIT_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _retire_owned_probe(process)
        return None
    except BaseException:
        # The operator cancelled the scan. An exhausted budget keeps answering
        # "unavailable", while Ctrl-C retires this read's helpers and stays the
        # cancellation the caller raised.
        _retire_owned_probe(process)
        raise
    return _GitBytesAnswer(returncode=process.returncode, stdout=stdout, stderr=stderr)


def _git_query(*arguments: str) -> _GitAnswer | None:
    """One bounded local Git read, or None when it cannot answer in budget."""

    answer = _run_git_probe(*arguments)
    if answer is None:
        return None
    return _GitAnswer(returncode=answer.returncode, stdout=answer.stdout.decode("utf-8", "replace"))


def _git_query_bytes(*arguments: str) -> _GitBytesAnswer | None:
    """One bounded local Git read that keeps the raw output bytes.

    Pathname listings carry their record identity in these bytes. A text read
    translates a CR inside a legal name into LF and replaces an undecodable
    record with U+FFFD, so a pathname rebuilt from it can name a file that
    does not exist and the tracked file would leave the scan.
    """

    return _run_git_probe(*arguments)


_GIT_NO_REPOSITORY_DIAGNOSTIC = (
    b"fatal: not a git repository (or any of the parent directories): .git"
)


def _git_reports_missing_repository(stderr: bytes) -> bool:
    """Whether Git's whole answer is its canonical "no repository" diagnostic.

    Discovery has exactly this answer outside a repository.  A broken linked
    worktree answers ``not a git repository: <target>`` instead, and a refusal
    may quote any path - including one that itself contains this phrase - so a
    substring match would turn a repository Git could not read into a clean
    empty scan.  The probes run with a fixed C locale, so the canonical text is
    the only form Git can answer here.
    """

    return stderr.strip() == _GIT_NO_REPOSITORY_DIAGNOSTIC


def _git_worktree_root(path: Path) -> Path | None:
    """Return the worktree root, or None when Git says this is not one.

    Raises :class:`GitProbeUnavailable` when Git could not answer at all, so
    callers cannot mistake silence for "no repository here".
    """

    path = path.resolve()
    target = path if path.is_dir() else path.parent
    # The root is a directory name like any other: it may carry a CR, an LF or
    # bytes UTF-8 cannot encode. A text read turns those bytes into LF/U+FFFD,
    # the rebuilt root then names no directory, and every tracked file below it
    # silently leaves the scan. Read the answer as bytes and remove only the
    # terminator Git appends, so the root keeps its own identity.
    answer = _git_query_bytes("-C", str(target), "rev-parse", "--show-toplevel")
    if answer is None:
        raise GitProbeUnavailable("git did not answer the worktree probe")
    if answer.returncode != 0:
        # Only Git's own "this is not a repository" is a negative answer. A
        # refusal - unsafe ownership, a blocked index, a corrupt repository, a
        # broken linked-worktree metadata link - also exits nonzero, and reading
        # it as "not a worktree" drops every tracked file below the target: an
        # explicit scan of a pruned directory then reports a clean empty
        # boundary instead of an unknown one.
        if not _git_reports_missing_repository(answer.stderr):
            raise GitProbeUnavailable(
                "git refused the worktree probe: "
                + answer.stderr.decode("utf-8", "replace").strip()[:200]
            )
        return None
    record = answer.stdout
    if record.endswith(b"\n"):
        record = record[: -1]
    if not record:
        return None
    return Path(os.fsdecode(record)).resolve()


def _git_probe(path: Path) -> dict[str, Any]:
    """Report Git ownership of one path; ``None`` marks a question Git did not answer."""

    path = path.resolve()
    try:
        repo_root = _git_worktree_root(path)
    except GitProbeUnavailable:
        return {"inside_worktree": None, "tracked": None, "ignored": None}
    if repo_root is None:
        return {"inside_worktree": False, "tracked": False, "ignored": False}
    try:
        rel_path = str(path.relative_to(repo_root))
    except ValueError:
        rel_path = str(path)

    def _ask(*arguments: str) -> bool | None:
        """Git's yes, its no, or a failed query no caller may read as "no".

        Both probes below answer with 0 or 1; any other status is Git refusing
        the question, which must stay distinguishable from a negative answer.
        """

        answer = _git_query(*arguments)
        if answer is None or answer.returncode not in (0, 1):
            return None
        return answer.returncode == 0

    tracked = _ask(
        "--literal-pathspecs", "-C", str(repo_root),
        "ls-files", "--error-unmatch", "--", rel_path,
    )
    if tracked is None:
        # Ownership is already unproven, and no caller can act on the ignore
        # flag of a file whose ownership is unknown; the second read is wasted.
        return {
            "inside_worktree": True,
            "root": str(repo_root),
            "tracked": None,
            "ignored": None,
        }
    # `check-ignore` rejects pathspec magic outright -- the literal global
    # option makes it exit 128 -- so the leading `./` is what keeps a name that
    # starts with `:` from being read as magic here.
    ignored = _ask(
        "-C", str(repo_root), "check-ignore", "-q", "--", f"./{rel_path}",
    )
    return {
        "inside_worktree": True,
        "root": str(repo_root),
        "tracked": tracked,
        "ignored": ignored,
    }


def _is_local_private_state_path(path: Path, scan_root: Path) -> bool:
    try:
        relative = path.relative_to(scan_root)
    except ValueError:
        relative = path
    parts = set(relative.parts) | {scan_root.name}
    return bool(parts & LOCAL_PRIVATE_STATE_PARTS) or path.name in LOCAL_PRIVATE_STATE_FILE_NAMES


def _public_boundary_policy(registry: Mapping[str, object]) -> dict[str, str]:
    policy = registry.get("public_boundary") if isinstance(registry, dict) else None
    if not isinstance(policy, dict):
        policy = registry.get("privacy") if isinstance(registry, dict) else None
    if not isinstance(policy, dict):
        policy = {}

    tracked_private_doc_urls = "block"
    raw_mode = policy.get("tracked_private_doc_urls") or policy.get("private_doc_urls_in_tracked_files")
    if str(raw_mode).lower() == "allow" or policy.get("allow_tracked_private_doc_urls") is True:
        tracked_private_doc_urls = "allow"

    return {
        "schema_version": "loopx_public_boundary_policy_v0",
        "tracked_private_doc_urls": tracked_private_doc_urls,
    }


def _index_duplicate_summary(index_path: Path) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    if not index_path.exists():
        return {
            "duplicate_rows": 0,
            "reward_overlay_rows": 0,
            "unexpected_duplicate_rows": 0,
            "repairable_duplicate_rows": 0,
            "artifact_identity_collision_rows": 0,
            "artifact_identity_collision_groups": 0,
        }

    with index_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            groups.setdefault(index_identity(item), []).append(item)

    reward_overlay_rows = 0
    unexpected_duplicate_rows = 0
    repairable_duplicate_rows = 0
    artifact_identity_collision_rows = 0
    artifact_identity_collision_groups = 0
    for records in groups.values():
        if len(records) <= 1:
            continue
        duplicate_rows = len(records) - 1
        duplicate_classification = classify_index_duplicate_records(records)
        duplicate_kind = duplicate_classification.get("duplicate_kind")
        if duplicate_kind == "reward_overlay":
            reward_overlay_rows += duplicate_rows
        elif duplicate_classification.get("repairable"):
            unexpected_duplicate_rows += duplicate_rows
            repairable_duplicate_rows += duplicate_rows
        else:
            unexpected_duplicate_rows += duplicate_rows
            artifact_identity_collision_rows += duplicate_rows
            artifact_identity_collision_groups += 1

    return {
        "duplicate_rows": reward_overlay_rows + unexpected_duplicate_rows,
        "reward_overlay_rows": reward_overlay_rows,
        "unexpected_duplicate_rows": unexpected_duplicate_rows,
        "repairable_duplicate_rows": repairable_duplicate_rows,
        "artifact_identity_collision_rows": artifact_identity_collision_rows,
        "artifact_identity_collision_groups": artifact_identity_collision_groups,
    }


def _index_duplicate_warning(
    goal_id: object,
    raw: int,
    unique: int,
    duplicate_summary: dict[str, Any] | None = None,
) -> str:
    safe_goal_id = str(goal_id)
    summary = duplicate_summary or {}
    detail_parts = [
        f"{label}={value}"
        for key, label in (
            ("unexpected_duplicate_rows", "unexpected"),
            ("repairable_duplicate_rows", "auto_repairable"),
            ("artifact_identity_collision_groups", "artifact_identity_collisions"),
            ("artifact_identity_collision_rows", "artifact_collision_rows"),
            ("reward_overlay_rows", "reward_overlays"),
        )
        if (value := int(summary.get(key) or 0))
    ]
    if not detail_parts and raw > unique:
        detail_parts.append(f"duplicates={raw - unique}")
    detail = f" {' '.join(detail_parts)}" if detail_parts else ""
    inspect_cmd = f"`loopx history --goal-id {safe_goal_id} inspect-index-duplicates`"
    repair_cmd = f"`loopx history --goal-id {safe_goal_id} repair-index-duplicates`"
    if int(summary.get("artifact_identity_collision_rows") or 0):
        action = f"inspect with {inspect_cmd}; artifact identity collisions need reviewed merge semantics"
        if int(summary.get("repairable_duplicate_rows") or 0):
            action += f"; preview safe repair for auto-repairable rows with {repair_cmd}"
    else:
        action = f"inspect with {inspect_cmd}; preview repair with {repair_cmd}"
    return f"{safe_goal_id}: duplicate index rows raw={raw} unique={unique}{detail}; {action}"


def _iter_filtered_goals(
    registry: dict[str, Any],
    *,
    goal_id_filter: str | None = None,
    activation_state_filter: GoalActivationState | str | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    normalized_activation = (
        normalize_goal_activation_state(activation_state_filter)
        if activation_state_filter is not None
        else None
    )
    for goal in registry_goals(registry):
        goal_id = str(goal.get("id") or "")
        if goal_id_filter and goal_id != goal_id_filter:
            continue
        if normalized_activation is not None and goal_activation_state(goal) is not normalized_activation:
            continue
        yield goal_id, goal


def _resolve_existing_state_file(goal: Mapping[str, object]) -> Path | None:
    repo_text = str(goal.get("repo") or "").strip()
    if not repo_text:
        return None
    raw_state = goal.get("state_file")
    state_file = resolve_state_file(
        Path(repo_text).expanduser(),
        raw_state if isinstance(raw_state, str) or raw_state is None else str(raw_state),
    )
    return state_file if state_file and state_file.exists() else None


def _parse_markdown_todo_metadata(
    lines: list[str], start_index: int
) -> tuple[dict[str, object], bool, bool, int]:
    metadata: dict[str, object] = {}
    malformed_excluded_agents = False
    malformed_status = False
    next_index = start_index + 1
    while next_index < len(lines):
        raw_line = lines[next_index]
        if raw_line.startswith("## ") or TODO_TASK_PATTERN.match(raw_line):
            break
        metadata_match = TODO_METADATA_PATTERN.match(raw_line)
        if metadata_match:
            for raw_field in metadata_match.group("body").split():
                if raw_field.startswith("status=") and normalize_todo_status(
                    decode_metadata_value(raw_field.split("=", 1)[1])
                ) is None:
                    malformed_status = True
        for key, value in parse_todo_metadata_tokens(raw_line) or []:
            if key == "status" and normalize_todo_status(value) is None:
                malformed_status = True
            elif key in {"excluded_agent", "excluded_agents"}:
                try:
                    require_todo_excluded_agents(value)
                except ValueError:
                    malformed_excluded_agents = True
        if parsed := parse_todo_metadata_line(raw_line):
            metadata.update(parsed)
        next_index += 1
    return metadata, malformed_excluded_agents, malformed_status, next_index


def _markdown_user_todo_scope_violation(
    *,
    todo_id: str,
    task_class: str,
    metadata: Mapping[str, object],
    blocks_agent: object,
    claimed_by: str | None,
    registered_agents: list[str],
    location: str,
) -> tuple[str, str] | None:
    global_gate = metadata.get("global_gate") is True
    bound_agent = normalize_todo_bound_agent(metadata.get("bound_agent"))
    goal_bound = normalize_todo_goal_bound(metadata.get("goal_bound")) is True
    is_gate = task_class == TODO_TASK_CLASS_USER_GATE
    effective_bound_agent = bound_agent or (blocks_agent if is_gate else None) or claimed_by
    effective_goal_bound = bool(goal_bound or (global_gate and is_gate))
    if task_class not in {TODO_TASK_CLASS_USER_ACTION, TODO_TASK_CLASS_USER_GATE}:
        return "user_todo_task_class_missing", (
            f"open user todo {todo_id} requires task_class=user_gate or task_class=user_action ({location})"
        )
    if task_class == TODO_TASK_CLASS_USER_ACTION and (blocks_agent or global_gate):
        return "user_action_blocking_scope_invalid", (
            f"open user_action todo {todo_id} is non-blocking and cannot set blocks_agent or global_gate=true ({location})"
        )
    if is_gate and blocks_agent and global_gate:
        return "user_gate_scope_conflict", (
            f"open user_gate todo {todo_id} in multi-agent goal cannot set both blocks_agent and global_gate=true ({location})"
        )
    if bound_agent and goal_bound:
        return "user_todo_response_scope_conflict", (
            f"open user todo {todo_id} cannot set both bound_agent and goal_bound=true ({location})"
        )
    if is_gate and global_gate and bound_agent:
        return "goal_user_gate_agent_binding_invalid", (
            f"goal-wide user_gate todo {todo_id} cannot set bound_agent; its response binding is goal_bound=true ({location})"
        )
    if is_gate and blocks_agent and goal_bound:
        return "agent_user_gate_goal_binding_invalid", (
            f"agent-scoped user_gate todo {todo_id} cannot set goal_bound=true ({location})"
        )
    if is_gate and blocks_agent and bound_agent and bound_agent != blocks_agent:
        return "agent_user_gate_response_binding_mismatch", (
            f"agent-scoped user_gate todo {todo_id} must bind to blocks_agent={blocks_agent!r}, "
            f"not bound_agent={bound_agent!r} ({location})"
        )
    if bound_agent and registered_agents and bound_agent not in registered_agents:
        return "user_todo_bound_agent_unregistered", (
            f"open user todo {todo_id} has bound_agent={bound_agent!r}, which is not registered for this goal "
            f"(registered_agents={', '.join(registered_agents)}, {location})"
        )
    if len(registered_agents) > 1 and not effective_bound_agent and not effective_goal_bound:
        return "multi_agent_user_todo_missing_response_scope", (
            f"open user todo {todo_id} in multi-agent goal requires bound_agent=<registered-agent> or goal_bound=true ({location})"
        )
    if is_gate and blocks_agent and registered_agents and blocks_agent not in registered_agents:
        return "user_gate_blocks_unregistered_agent", (
            f"open user_gate todo {todo_id} has blocks_agent={blocks_agent!r}, which is not registered for this goal "
            f"(registered_agents={', '.join(registered_agents)}, {location})"
        )
    if is_gate and len(registered_agents) > 1 and not blocks_agent and not global_gate:
        return "multi_agent_user_gate_missing_scope", (
            f"open user_gate todo {todo_id} in multi-agent goal requires blocks_agent=<registered-agent> or global_gate=true ({location})"
        )
    return None


def _markdown_todo_contract_diagnostics(
    *,
    goal_id: str,
    state_file: Path,
    lines: list[str],
    registered_agents: list[str],
) -> tuple[list[dict[str, Any]], int]:
    diagnostics: list[dict[str, Any]] = []
    checked = 0
    role: str | None = None
    in_completed_work_archive = False
    index = 0

    def add_error(code: str, message: str) -> None:
        diagnostics.append(contract_error_diagnostic(code=code, message=f"{goal_id}: {message}", goal_id=goal_id))

    while index < len(lines):
        line = lines[index]
        if line.startswith("## "):
            heading_text = line.lstrip("#").strip()
            heading = heading_text.lower()
            in_completed_work_archive = heading_text.casefold() == COMPLETED_WORK_ARCHIVE_HEADING.casefold()
            if "user todo" in heading or "owner review" in heading:
                role = "user"
            elif "agent todo" in heading:
                role = "agent"
            else:
                role = None
            index += 1
            continue
        match = TODO_TASK_PATTERN.match(line)
        if not match:
            index += 1
            continue
        marker, text = match.groups()
        metadata, malformed_excluded_agents, malformed_status, next_index = _parse_markdown_todo_metadata(
            lines, index
        )
        removed_continuation_policy = normalize_removed_todo_continuation_policy(
            metadata.get("removed_continuation_policy")
        )
        status = str(metadata.get("status") or todo_status_from_marker(marker) or "").lower()
        task_class = str(metadata.get("task_class") or "")
        todo_id = str(metadata.get("todo_id") or f"line:{index + 1}")
        if malformed_excluded_agents:
            add_error(
                "todo_excluded_agents_invalid",
                f"todo {todo_id} has malformed excluded_agents metadata; "
                "replace it with --excluded-agent <registered-agent> or remove it with --clear-excluded-agents",
            )
        if malformed_status:
            add_error(
                "todo_status_invalid",
                f"todo {todo_id} has malformed status metadata; "
                "replace it with status=open, status=done, status=blocked, or status=deferred",
            )
        if role == "agent" and removed_continuation_policy:
            add_error(
                "agent_todo_removed_continuation_policy",
                f"agent todo {todo_id} uses removed continuation_policy={removed_continuation_policy}; "
                "use continuation_policy=independent_handoff with an explicit review action_kind and "
                "excluded_agents=<author> only when executor separation is required",
            )
        blocks_agent = metadata.get("blocks_agent")
        if role == "agent" and blocks_agent:
            add_error(
                "agent_todo_blocks_agent_invalid",
                f"agent todo {todo_id} uses removed blocks_agent routing; "
                "blocks_agent is reserved for user gates, while agent executor constraints use excluded_agents",
            )
        excluded_agents = normalize_todo_excluded_agents(metadata.get("excluded_agents"))
        claimed_by = normalize_todo_claimed_by(metadata.get("claimed_by"))
        if claimed_by and claimed_by in excluded_agents:
            add_error(
                "todo_claimed_by_excluded_agent",
                f"agent todo {todo_id} has claimed_by={claimed_by!r} also listed in excluded_agents",
            )
        archived_terminal_todo = (
            in_completed_work_archive
            and todo_status_from_marker(marker) in TERMINAL_TODO_STATUSES
            and status in TERMINAL_TODO_STATUSES
        )
        if role != "agent" and excluded_agents and not archived_terminal_todo:
            add_error(
                "todo_executor_exclusion_scope_invalid",
                f"{role or 'unscoped'} todo {todo_id} uses excluded_agents; "
                "executor exclusions are only valid for agent todos",
            )
        unknown_excluded_agents = [
            agent_id for agent_id in excluded_agents if registered_agents and agent_id not in registered_agents
        ]
        if role == "agent" and unknown_excluded_agents:
            add_error(
                "todo_excludes_unregistered_agent",
                f"agent todo {todo_id} excludes unregistered agents: {', '.join(unknown_excluded_agents)}",
            )
        if role == "user" and status not in TERMINAL_TODO_STATUSES:
            checked += 1
            violation = _markdown_user_todo_scope_violation(
                todo_id=todo_id,
                task_class=task_class,
                metadata=metadata,
                blocks_agent=blocks_agent,
                claimed_by=claimed_by,
                registered_agents=registered_agents,
                location=f"state_file={state_file}, line={index + 1}, text={text}",
            )
            if violation is not None:
                add_error(*violation)
        index = next_index
    return diagnostics, checked


def _todo_contract_diagnostics(
    registry: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id_filter: str | None = None,
    activation_state_filter: GoalActivationState | str | None = None,
    todo_snapshot: CanonicalTodoSnapshot | None = None,
) -> tuple[list[dict[str, Any]], int]:
    diagnostics: list[dict[str, Any]] = []
    checked = 0
    source_snapshots: dict[Path, bytes | None] = {}
    for goal_id, goal in _iter_filtered_goals(
        registry,
        goal_id_filter=goal_id_filter,
        activation_state_filter=activation_state_filter,
    ):
        def add_error(code: str, message: str) -> None:
            diagnostics.append(contract_error_diagnostic(code=code, message=message, goal_id=goal_id))

        try:
            route = resolve_goal_source_runtime_route(
                registry_path=registry_path, goal_id=goal_id, registry=registry,
                runtime_root_override=runtime_root_override,
                source_snapshots=source_snapshots,
            )
            if route["routed_to_source_registry"]:
                source_registry = load_registry(
                    Path(route["source_registry"]), source_snapshots=source_snapshots,
                )
                goal = next(item for item in registry_goals(source_registry) if str(item.get("id") or "") == goal_id)
            goal_runtime = Path(route["source_runtime_root"])
        except (OSError, ValueError, StopIteration) as exc:
            add_error(
                "canonical_todo_contract_diagnostics_unavailable",
                f"{goal_id}: canonical Todo source route unavailable: {exc}",
            )
            continue

        # The durable fence selects the authority for diagnostics as well as
        # Todo display. Reuse the TS read-model/record validator: the Markdown
        # copy cannot invalidate or rescue a promoted collection.
        try:
            canonical_reader = (
                todo_snapshot.read_contract_rows
                if todo_snapshot is not None
                else read_canonical_todos_if_promoted
            )
            canonical = canonical_reader(runtime_root=goal_runtime, goal_id=goal_id)
        except LocalCoordinationAuthorityUnavailable as exc:
            add_error(exc.code, f"{goal_id}: canonical Todo contract unavailable: {exc}")
            continue
        if canonical is not None:
            # Evaluate provider rows without reading display.
            try:
                canonical_diagnostics = todo_contract_diagnostics(
                    todos=canonical["todo_contract_rows" if todo_snapshot is not None else "todos"],
                    registered_agents=registered_agent_ids_for_goal(goal),
                    terminal_statuses=TERMINAL_TODO_STATUSES,
                )
            except RuntimeError as exc:
                add_error(
                    "canonical_todo_contract_diagnostics_unavailable",
                    f"{goal_id}: canonical Todo contract diagnostics unavailable: {exc}",
                )
                continue
            checked += canonical_diagnostics["checked"]
            for row in canonical_diagnostics["diagnostics"]:
                add_error(row["code"], f"{goal_id}: canonical todo {row['todo_id']} {row['detail']}")
            continue

        state_file = _resolve_existing_state_file(goal)
        if state_file is None:
            continue
        try:
            state_text = state_file.read_text(encoding="utf-8")
            lines = state_text.splitlines()
        except (OSError, UnicodeError) as exc:
            add_error("active_state_read_failed", f"{goal_id}: cannot read active state for todo contract check: {exc}")
            continue
        try:
            state_incomplete = active_state_incomplete_warning(state_text)
        except ValueError as exc:
            add_error("active_state_read_failed", f"{goal_id}: cannot parse active state for todo contract check: {exc}")
            continue
        if state_incomplete:
            add_error(
                "state_incomplete",
                f"{goal_id}: active state is structurally incomplete; missing required "
                f"Todo section(s): {', '.join(state_incomplete['missing_sections'])} "
                f"(state_file={state_file}); {state_incomplete['recommended_action']}",
            )
            continue

        file_diagnostics, file_checked = _markdown_todo_contract_diagnostics(
            goal_id=goal_id,
            state_file=state_file,
            lines=lines,
            registered_agents=registered_agent_ids_for_goal(goal),
        )
        diagnostics.extend(file_diagnostics)
        checked += file_checked
    return diagnostics, checked


def _goal_source_and_projection_diagnostics(
    registry: dict[str, Any],
    *,
    goal_id_filter: str | None = None,
    activation_state_filter: GoalActivationState | str | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    diagnostics: list[dict[str, Any]] = []
    checks: list[str] = []
    warnings: list[str] = []
    for goal_id, goal in _iter_filtered_goals(
        registry,
        goal_id_filter=goal_id_filter,
        activation_state_filter=activation_state_filter,
    ):
        goal_diags, goal_checks = verify_authority_source_digests(goal)
        diagnostics.extend(goal_diags)
        checks.extend(goal_checks)
        state_file = _resolve_existing_state_file(goal)
        if state_file is None:
            continue
        try:
            state_text = state_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        projection_gap = state_projection_gap_warning(state_text)
        if not projection_gap:
            continue
        raw_roles = projection_gap.get("target_roles")
        target_roles = ",".join(str(role) for role in raw_roles) if isinstance(raw_roles, (list, tuple)) else ""
        warnings.append(
            f"{goal_id}: active state has a state_projection_gap for {target_roles or 'todo'}; "
            "expand executable Next Action work into an open Agent Todo or user wait into an open User Todo"
        )
    return diagnostics, checks, warnings


def _is_binary_media_path(path: Path) -> bool:
    return path.suffix.lower() in BINARY_MEDIA_SUFFIXES


def _local_metadata_headers(name: str) -> tuple[bytes, ...]:
    """Return the magic headers a known OS metadata name may begin with."""

    if name == ".DS_Store":
        return LOCAL_METADATA_HEADERS[".DS_Store"]
    if name.startswith("._") and len(name) > len("._"):
        return LOCAL_METADATA_HEADERS["._"]
    return ()


def _has_local_metadata_header(path: Path) -> bool:
    """Return whether the file's own bytes prove a known OS metadata format.

    The name alone never decides: a text file named ``._README.md`` or
    ``.DS_Store`` is scanned like any other text. An unreadable header also
    keeps the file, so the scan reader reports it instead of dropping it.
    """

    headers = _local_metadata_headers(path.name)
    if not headers:
        return False
    try:
        with path.open("rb") as handle:
            header = handle.read(8)
    except OSError:
        return False
    return header.startswith(headers)


def _resolve_scannable_file(path: Path) -> Path | None:
    # An alias name never decides eligibility: a text target stays readable
    # behind a .png alias, and a binary target stays unreadable behind a .md
    # alias. Only the resolved target suffix and local-state name decide.
    if _is_binary_media_path(path) and not path.is_symlink():
        return None
    resolved = path.resolve()
    if resolved.name.endswith(".local.json") or not resolved.is_file():
        return None
    return None if _is_binary_media_path(resolved) else resolved


def _is_skipped_dir_name(name: str) -> bool:
    return name in DEFAULT_SKIP_DIRS or name.endswith(".egg-info")


def _root_sort_key(path: Path) -> tuple[int, str]:
    return (len(path.parts), str(path))


def _tracked_scan_files(scan_root: Path) -> list[Path]:
    """Enumerate the Git-owned files under one scan root.

    Raises :class:`GitProbeUnavailable` when Git did not answer: an incomplete
    enumeration would silently drop the files a pruned directory hides, which
    is exactly what the caller cannot afford to miss.
    """

    scan_root = scan_root.resolve()
    repo_root = _git_worktree_root(scan_root)
    if repo_root is None:
        return []
    try:
        rel_root = str(scan_root.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return []
    if not rel_root:
        rel_root = "."

    # A selected directory is a literal Git path, not a pathspec. Names such
    # as ``:(missing-magic)`` or ``:`` otherwise change which files Git reads.
    tracked = _git_query_bytes(
        "--literal-pathspecs", "-C", str(repo_root), "ls-files", "-z", "--", rel_root
    )
    if tracked is None:
        raise GitProbeUnavailable("git did not answer the tracked-file enumeration")
    if tracked.returncode != 0:
        # Git answered and refused the enumeration. Reporting no files here
        # would hand the caller a clean scan of a directory it never read.
        raise GitProbeUnavailable("git refused the tracked-file enumeration")

    files: list[Path] = []
    for record in tracked.stdout.split(b"\0"):
        if record:
            if resolved := _resolve_scannable_file(repo_root / os.fsdecode(record)):
                files.append(resolved)
    return files


def _record_unreadable_directory(
    error: OSError,
    scan_root: Path,
    unreadable_directories: list[tuple[Path, str]],
) -> None:
    """Keep a directory whose contents could not be listed visible to the caller.

    ``os.walk`` swallows a listing failure by default, so a scan that cannot
    enter a directory contributes no file and reports the same clean boundary
    as a tree that has nothing to find. Keep the reason short via ``strerror``
    (or the error class name) so ``str(error)`` cannot leak an absolute path.
    """
    directory = Path(error.filename) if error.filename else scan_root
    reason = str(error.strerror) if error.strerror else type(error).__name__
    unreadable_directories.append((directory, reason[:120]))


def _display_scan_path(path: Path, root: Path) -> str:
    """Label a scanned path so even an undecodable name reaches the report.

    ``rel_or_abs`` returns ``os.fsdecode``'s text, which keeps a name that is
    not valid UTF-8 as lone surrogates. A report that carried those verbatim
    wrote bytes no strict UTF-8 reader accepts, and the JSON consumer could not
    read the finding that named the file. The escape keeps the record's exact
    bytes readable and distinct from every other name.
    """

    return display_path_text(rel_or_abs(path, root))


def _render_unreadable_directories(
    unreadable_directories: list[tuple[Path, str]],
    scan_roots: Mapping[Path, Path],
) -> list[str]:
    """Label each unreadable directory relative to its outermost root, numbering collisions."""

    labeled: list[tuple[str, str]] = []
    for directory, reason in dict.fromkeys(unreadable_directories):
        containing = [root for root in scan_roots if directory.is_relative_to(root)]
        if not containing:
            label = display_path_text(directory)
        else:
            root = min(containing, key=_root_sort_key)
            label = display_path_text(directory.relative_to(scan_roots[root])) or "."
        labeled.append((label, reason))
    totals: dict[str, int] = {}
    for label, _ in labeled:
        totals[label] = totals.get(label, 0) + 1
    seen: dict[str, int] = {}
    rendered: list[str] = []
    for label, reason in labeled:
        seen[label] = seen.get(label, 0) + 1
        suffix = f" ({seen[label]})" if totals[label] > 1 and seen[label] > 1 else ""
        rendered.append(f"{label}{suffix}: {reason}")
    return rendered


def iter_scan_files(
    scan_root: Path,
    *,
    unreadable_directories: list[tuple[Path, str]] | None = None,
    unavailable_git_probes: list[str] | None = None,
) -> list[Path]:
    scan_root = scan_root.resolve()
    if scan_root.is_file():
        return [scan_root]
    files: list[Path] = []
    unreadable = unreadable_directories if unreadable_directories is not None else []
    try:
        tracked_files = _tracked_scan_files(scan_root)
    except GitProbeUnavailable:
        # Walk what is reachable, but remember that the Git side of the
        # enumeration is missing so the scan cannot be called clean.
        if unavailable_git_probes is not None:
            unavailable_git_probes.append(str(scan_root))
        tracked_files = []
    tracked_paths = set(tracked_files)
    # The root is probed even when dependency pruning skips the walk, so an
    # unreadable scan target cannot pass because of how it was named.
    try:
        with os.scandir(scan_root):
            pass
    except OSError as error:
        _record_unreadable_directory(error, scan_root, unreadable)
    # Dependency pruning never overrides tracked repository ownership.
    if (
        any(_is_skipped_dir_name(part) for part in scan_root.parts)
        or os.path.isfile(scan_root / "pyvenv.cfg")
    ):
        return sorted(tracked_files)

    for dir_path, dir_names, file_names in os.walk(
        scan_root,
        onerror=lambda error: _record_unreadable_directory(error, scan_root, unreadable),
    ):
        current_dir = Path(dir_path)
        dir_names[:] = [
            name
            for name in dir_names
            if not _is_skipped_dir_name(name)
            and not os.path.isfile(current_dir / name / "pyvenv.cfg")
        ]
        for file_name in file_names:
            resolved = _resolve_scannable_file(current_dir / file_name)
            if resolved is None:
                continue
            # A normal checkout keeps OS metadata such as .DS_Store untracked;
            # Git ownership plus the format's own header separate checkout
            # noise from a text file this repository would actually publish.
            if resolved not in tracked_paths and _has_local_metadata_header(resolved):
                continue
            files.append(resolved)
    return sorted(set(files + tracked_files))


def _scan_package_lock_hits(path: Path, root: Path, text: str) -> list[str]:
    try:
        lockfile = json.loads(text)
    except json.JSONDecodeError:
        return []
    packages = lockfile.get("packages") if isinstance(lockfile, dict) else None
    if not isinstance(packages, dict):
        return []
    display_path = _display_scan_path(path, root)
    lock_hits: list[str] = []
    for package_path, package in packages.items():
        if not isinstance(package, dict):
            continue
        resolved = package.get("resolved")
        if (
            isinstance(resolved, str)
            and resolved.startswith(("http://", "https://"))
            and not resolved.startswith(_PUBLIC_NPM_REGISTRY_PREFIX)
        ):
            lock_hits.append(
                f"{display_path}: non_public_package_registry ({package_path or '<root>'})"
            )
    return lock_hits


def scan_public_boundary(
    scan_roots: list[Path], *, registry: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Check selected outgoing content, preserving findings and unread inputs.

    ``hits`` retains rule locations; ``blocking_hits`` excludes ambiguous IP
    advisories. ``ok`` requires no blocker or unread input. It
    never certifies an unrequested file, generated text or future publication.
    """
    hits: list[str] = []
    blocking_hits: list[str] = []
    advisory_hits: list[str] = []
    allowed_hits: list[str] = []
    private_state_git_warnings: list[str] = []
    skipped_private_state_files: list[str] = []
    credential_reference_hits: list[str] = []
    unreadable_files: list[str] = []
    unreadable_directories: list[tuple[Path, str]] = []
    unavailable_git_probes: list[str] = []
    missing_scan_roots: list[str] = []
    files: list[Path] = []
    file_roots: dict[Path, Path] = {}
    directory_roots: dict[Path, Path] = {}
    private_files: set[Path] = set()

    def record_hit(hit: str, *, blocking: bool = True) -> None:
        hits.append(hit)
        (blocking_hits if blocking else advisory_hits).append(hit)

    for scan_root in scan_roots:
        resolved_scan_root = scan_root.resolve()
        if not resolved_scan_root.exists():
            # A scan root that is not there contributes no files, so a typo in
            # the caller's path would otherwise report a clean empty boundary.
            missing_scan_roots.append(display_path_text(scan_root))
            continue
        display_root = resolved_scan_root.parent if resolved_scan_root.is_file() else resolved_scan_root
        # `file_roots` renders a file relative to the root that asked for it and
        # is read for every hit, so an unreadable directory is labelled from its
        # own map instead of adding a non-file key to that one.
        directory_roots[resolved_scan_root] = display_root
        classification_root = display_root
        if set(resolved_scan_root.parts) & LOCAL_PRIVATE_STATE_PARTS:
            # A narrow explicit input cannot erase private ancestry. A real
            # worktree is its own namespace, even beneath a private parent;
            # outside Git, retain the complete canonical ancestry.
            try:
                classification_root = (
                    _git_worktree_root(resolved_scan_root)
                    or Path(resolved_scan_root.anchor)
                )
            except GitProbeUnavailable:
                # The canonical ancestry is the wider answer: it can only add
                # private state, never hide it.
                classification_root = Path(resolved_scan_root.anchor)
                unavailable_git_probes.append(str(resolved_scan_root))
        if _is_local_private_state_path(resolved_scan_root, classification_root):
            # Explicit outgoing selection cannot turn an ignored private file
            # or a pruned private directory into a successful empty scan.
            record_hit(f"{_display_scan_path(resolved_scan_root, classification_root)}: local_private_state")
            continue
        for file_path in iter_scan_files(
            resolved_scan_root,
            unreadable_directories=unreadable_directories,
            unavailable_git_probes=unavailable_git_probes,
        ):
            files.append(file_path)
            if _is_local_private_state_path(file_path, classification_root):
                private_files.add(file_path)
            previous_root = file_roots.get(file_path)
            if previous_root is None or _root_sort_key(display_root) < _root_sort_key(previous_root):
                file_roots[file_path] = display_root
    files = sorted(set(files))
    policy = _public_boundary_policy(registry or {})
    private_file_git: dict[Path, dict[str, Any]] = {}

    def public_files() -> Iterator[Path]:
        # Filtering precedes submission: a worker must never open untracked
        # local-private state. Policy, Git ownership and content classification
        # stay here, in the existing scan owner, not in the I/O adapter.
        ownership_unavailable = False
        for path in files:
            root = file_roots.get(path, path)
            if path in private_files:
                rel_path = rel_or_abs(path, root)
                if ownership_unavailable or (git := _git_probe(path)).get("tracked") is None:
                    # Git did not answer, so ownership is unproven; stop asking
                    # once one answer is missing and never open private state.
                    ownership_unavailable = True
                    unavailable_git_probes.append(rel_path)
                    skipped_private_state_files.append(rel_path)
                    continue
                if not git.get("tracked"):
                    skipped_private_state_files.append(rel_path)
                    ignored_flag = git.get("ignored")
                    if ignored_flag is None:
                        # Git refused the ignore question: the file is still
                        # skipped, but a clean scan would claim it is protected.
                        ownership_unavailable = True
                        unavailable_git_probes.append(rel_path)
                    elif git.get("inside_worktree") and ignored_flag is False:
                        private_state_git_warnings.append(
                            f"{_display_scan_path(path, root)}: private state should be gitignored"
                        )
                    continue
                private_file_git[path] = git
                record_hit(f"{_display_scan_path(path, root)}: local_private_state")
            yield path

    for read in iter_utf8_file_reads(public_files()):
        path = read.path
        root = file_roots.get(path, path)
        git: dict[str, Any] | None = private_file_git.pop(path, None)
        if read.error is not None:
            detail = (
                read.error.strerror or type(read.error).__name__
                if isinstance(read.error, OSError) else "invalid UTF-8"
            )
            unreadable_files.append(f"{_display_scan_path(path, root)}: {detail}")
            continue
        text = read.text
        assert text is not None
        if path.name == "package-lock.json":
            for lock_hit in _scan_package_lock_hits(path, root, text):
                record_hit(lock_hit)
        folded_text = _prefilter_fold(text)
        candidate_rules = [
            (name, rule) for name, rule in LEAK_RULES.items() if rule.is_candidate(folded_text)
        ]
        if not candidate_rules:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            folded_line = _prefilter_fold(line)
            for name, rule in candidate_rules:
                if not rule.is_candidate(folded_line):
                    continue
                scan_line = (
                    _PUBLIC_LARK_DEVELOPER_CONSOLE_HOST.sub("", line)
                    if name == "private_doc_url"
                    else line
                )
                if rule.pattern.search(scan_line):
                    hit = f"{_display_scan_path(path, root)}:{line_no}: {name}"
                    if name == "credential" and _credential_hits_are_all_references(line):
                        credential_reference_hits.append(hit)
                        continue
                    if (
                        name == "private_doc_url"
                        and policy.get("tracked_private_doc_urls") == "allow"
                        and (git := git or _git_probe(path)).get("tracked")
                    ):
                        allowed_hits.append(hit)
                    else:
                        record_hit(hit, blocking=rule.blocking)
    return {
        "ok": not (
            blocking_hits
            or missing_scan_roots
            or unreadable_files
            or unreadable_directories
            or unavailable_git_probes
        ),
        "scan_roots": [display_path_text(path) for path in scan_roots],
        "files": len(files),
        "scanned_files": len(files) - len(skipped_private_state_files),
        "skipped_private_state_files": [display_path_text(item) for item in skipped_private_state_files],
        "credential_reference_hits": credential_reference_hits,
        "unreadable_files": unreadable_files,
        # Identity is the absolute directory, so two roots that each hold an
        # unlistable `notes/` are both reported; colliding labels are numbered.
        "unreadable_directories": _render_unreadable_directories(unreadable_directories, directory_roots),
        "missing_scan_roots": missing_scan_roots,
        # A Git question that was not answered leaves the boundary unknown:
        # this list keeps a partial answer from being reported as clean.
        "unavailable_git_probes": sorted({display_path_text(item) for item in unavailable_git_probes}),
        "allowed_hits": allowed_hits,
        "private_state_git_warnings": private_state_git_warnings,
        "policy": policy,
        "hits": hits,
        "blocking_hits": blocking_hits,
        "warnings": advisory_hits + credential_reference_hits,
    }


_BOUNDARY_FAILURE_FIELDS = (
    ("missing_scan_roots", "public_boundary_scan_root_missing", "scan root does not exist: "),
    ("unreadable_directories", "public_boundary_scan_directory_unreadable", "scan directory could not be listed: "),
    ("unreadable_files", "public_boundary_scan_file_unreadable", "scan file could not be read: "),
    ("unavailable_git_probes", "public_boundary_scan_git_unavailable", "Git could not answer ownership for: "),
)


def check_contract(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    scan_roots: list[Path],
    limit: int,
    allow_missing_registry: bool = False,
    goal_id_filter: str | None = None,
    activation_state_filter: GoalActivationState | str | None = None,
    include_public_boundary_scan: bool = True,
    history_audit: RunHistoryAudit | None = None,
    registry: dict[str, Any] | None = None,
    todo_snapshot: CanonicalTodoSnapshot | None = None,
) -> dict[str, Any]:
    error_diagnostics: list[dict[str, Any]] = []
    warnings: list[str] = []
    checks: list[str] = []

    def add_global_error(code: str, message: str) -> None:
        error_diagnostics.append(contract_error_diagnostic(code=code, message=message))

    registry_payload = inspect_registry(registry_path)
    if registry_payload.get("ok"):
        checks.append(f"registry goals checked: {registry_payload.get('goal_count')}")
    else:
        if registry_payload.get("error"):
            error = str(registry_payload.get("error"))
            if allow_missing_registry and error == "registry file does not exist":
                warnings.append(f"{error}: {registry_path}")
            else:
                add_global_error("registry_unavailable", error)
        problem_diagnostics = registry_payload.get("problem_diagnostics")
        if isinstance(problem_diagnostics, list):
            error_diagnostics.extend(item for item in problem_diagnostics if isinstance(item, dict))
        else:
            for problem in registry_payload.get("problems") or []:
                add_global_error("registry_problem", str(problem))

    boundary_payload = inspect_registry_boundary(registry_path)
    if boundary_payload.get("ok"):
        raw_git = boundary_payload.get("git")
        git = raw_git if isinstance(raw_git, dict) else {}
        checks.append(
            "registry boundary: "
            f"{boundary_payload.get('classification')} "
            f"push_allowed={boundary_payload.get('github_push_allowed')} "
            f"tracked={git.get('tracked')} ignored={git.get('ignored')}"
        )
        if boundary_payload.get("should_be_gitignored") and git.get("inside_worktree"):
            if not git.get("ignored") and not git.get("tracked"):
                warnings.append(f"registry should be gitignored: {registry_path}")
    else:
        if boundary_payload.get("error"):
            warnings.append(f"registry boundary unavailable: {boundary_payload.get('error')}")
        for risk in boundary_payload.get("risks") or []:
            add_global_error("registry_boundary_risk", f"registry boundary risk: {risk}")

    if registry is None:
        registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
    todo_contract_diagnostics, checked_user_gates = _todo_contract_diagnostics(
        registry,
        todo_snapshot=todo_snapshot,
        registry_path=registry_path,
        runtime_root_override=runtime_root_override,
        goal_id_filter=goal_id_filter,
        activation_state_filter=activation_state_filter,
    )
    if checked_user_gates:
        checks.append(f"user-gate scopes checked: {checked_user_gates} open multi-agent gates")
    error_diagnostics.extend(todo_contract_diagnostics)
    source_diagnostics, source_checks, projection_warnings = _goal_source_and_projection_diagnostics(
        registry, goal_id_filter=goal_id_filter, activation_state_filter=activation_state_filter,
    )
    error_diagnostics.extend(source_diagnostics)
    checks.extend(source_checks)
    warnings.extend(projection_warnings)

    if runtime_root in {DEFAULT_RUNTIME_ROOT, LEGACY_RUNTIME_ROOT} or runtime_root.exists():
        checks.append(f"runtime root resolved: {runtime_root}")
    else:
        warnings.append(f"runtime root does not exist yet: {runtime_root}")

    if history_audit is None:
        history = collect_history(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id_filter,
            limit=limit,
            activation_state_filter=activation_state_filter,
        )
        history_audit = build_run_history_audit(
            history,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id_filter,
            activation_state_filter=activation_state_filter,
            include_runtime_goals=True,
        )
    elif not history_audit.matches(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id_filter,
        activation_state_filter=activation_state_filter,
        include_runtime_goals=True,
    ):
        raise ValueError("history audit scope does not match contract request")
    checks.append(f"run-history goals={history_audit.goal_count} runs={history_audit.run_count}")
    for item in history_audit.goals:
        raw, unique = item.raw_index_records, item.unique_runs
        if raw <= unique:
            continue
        if item.legacy_runtime_goal:
            checks.append(f"{item.goal_id}: legacy runtime goal has duplicate rows raw={raw} unique={unique}")
            continue
        duplicate_summary = _index_duplicate_summary(item.index_path)
        overlay_rows = duplicate_summary.get("reward_overlay_rows")
        if not duplicate_summary.get("unexpected_duplicate_rows") and overlay_rows:
            checks.append(f"{item.goal_id}: reward overlay rows raw={raw} unique={unique} overlays={overlay_rows}")
        else:
            warnings.append(_index_duplicate_warning(item.goal_id, raw, unique, duplicate_summary))

    if include_public_boundary_scan:
        boundary = scan_public_boundary(scan_roots, registry=registry)
        boundary_lists = {
            key: [str(item) for item in boundary.get(key) or []]
            for key, _, _ in _BOUNDARY_FAILURE_FIELDS
        }
        boundary_warnings = [str(item) for item in boundary.get("warnings") or []]
        public_boundary_scan = {
            "state": "completed",
            "ok": bool(boundary.get("ok")) and not any(boundary_lists.values()),
            "scanned_files": int(boundary.get("scanned_files") or 0),
            **boundary_lists,
            "warnings": boundary_warnings,
        }
        for key, code, prefix in _BOUNDARY_FAILURE_FIELDS:
            for entry in boundary_lists[key]:
                add_global_error(code, f"{prefix}{entry}")
        if public_boundary_scan["ok"]:
            label = "completed with warnings" if boundary_warnings else "clean"
            checks.append(f"public boundary scan {label}: {boundary.get('scanned_files')} files")
        else:
            for hit in boundary.get("blocking_hits", boundary.get("hits")) or []:
                add_global_error("public_boundary_violation", str(hit))
        warnings.extend(boundary_warnings)
        warnings.extend(str(item) for item in boundary.get("private_state_git_warnings") or [])
        for key, template in (
            ("skipped_private_state_files", "private state scan skipped: {} local-private files"),
            ("allowed_hits", "public boundary policy allowed: {} private_doc_url hits"),
            ("credential_reference_hits", "credential references downgraded: {} non-literal hits"),
        ):
            if items := boundary.get(key):
                checks.append(template.format(len(items)))
    else:
        public_boundary_scan = {
            "state": "deferred",
            "reason": "status_hot_path",
            "recommended_action": "run `loopx check` before publishing",
        }
        checks.append("public boundary scan deferred for status hot path")
    error_views = contract_error_views(error_diagnostics)
    errors = error_views["errors"]

    payload = {
        "ok": not errors,
        "registry": display_path_text(registry_path),
        "runtime_root": display_path_text(runtime_root),
        "scan_roots": [display_path_text(path) for path in scan_roots],
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "checks": len(checks),
        },
        **error_views,
        "warnings": warnings,
        "checks": checks,
        "public_boundary_scan": public_boundary_scan,
    }
    return project_contract_health_for_goal(payload, goal_id=goal_id_filter)


# Report members that name a Goal, Agent or Turn are identities, not prose.
# Rewriting them would fold two distinct identities into one spelling - a Goal
# whose id holds an undecodable byte and a Goal literally named like its escape
# would share a key and one set of errors.  Identities keep their own spelling;
# the JSON writer escapes only the code points a UTF-8 stream cannot carry.
REPORT_IDENTITY_MEMBERS = frozenset(
    {"goal_id", "goal_ids", "agent_id", "turn_instance_id", "goal_ref"}
)


def display_report_value(value: Any) -> Any:
    """Project the display strings of a check report onto a UTF-8 spelling.

    Findings name Git paths, warnings quote the registry and runtime roots, and
    the boundary scan carries its own path strings.  One report boundary keeps
    all of them decodable without changing ordinary text; see
    :func:`loopx.paths.display_report_text`.

    Identity members and mapping keys are left exactly as the producer wrote
    them: a display label is not an identity, and recovering the original Goal
    id has to stay possible from the structured report.
    """

    if isinstance(value, str):
        return display_report_text(value)
    if isinstance(value, (list, tuple)):
        return [display_report_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                item
                if isinstance(key, str) and key in REPORT_IDENTITY_MEMBERS
                else display_report_value(item)
            )
            for key, item in value.items()
        }
    return value


def render_contract_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Contract Check",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- scan_roots: `{payload.get('scan_roots')}`",
    ]
    summary = payload.get("summary") or {}
    lines.append(
        f"- summary: errors={summary.get('errors')}, warnings={summary.get('warnings')}, checks={summary.get('checks')}"
    )
    for title, key in (("Errors", "errors"), ("Warnings", "warnings"), ("Checks", "checks")):
        items = payload.get(key) or []
        if items:
            lines.extend(["", f"## {title}"])
            lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


# Star imports retain every former public name, including the lazy reexports.
__all__ = sorted(
    {name for name in globals() if not name.startswith("_")} | set(_PROCESS_RUNTIME_EXPORTS)
)
