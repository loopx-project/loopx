from __future__ import annotations

import fnmatch
import hashlib
import ipaddress
import re
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from ...control_plane.runtime.public_safety import public_safe_compact_text


ISSUE_FIX_REVIEWER_RECOMMENDATION_SCHEMA_VERSION = (
    "issue_fix_reviewer_recommendation_v0"
)
ISSUE_FIX_REVIEWER_SOURCES_INPUT_SCHEMA_VERSION = "issue_fix_reviewer_sources_input_v0"
CODEOWNERS_LOCATIONS = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)
GITHUB_NOREPLY_PATTERN = re.compile(
    r"^(?:\d+\+)?(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))@users\.noreply\.github\.com$",
    re.IGNORECASE,
)
REVIEWER_HANDLE_PATTERN = re.compile(
    r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:/[A-Za-z0-9_.-]+)?$"
)
SAFE_GIT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/~^{}-]{0,119}$")
AUTOMATED_HISTORY_IDENTITY_PATTERN = re.compile(
    r"(?:^|[-_.\s])bot(?:$|[-_.\s])|\[bot\]$",
    re.IGNORECASE,
)
REVIEWER_SOURCE_KINDS = {"maintainer_map"}
REVIEWER_SOURCE_TRUST_LEVELS = {"authoritative", "verified", "advisory"}
REVIEWER_SOURCE_FRESHNESS_STATES = {"current", "stale", "unknown"}
REVIEWER_SOURCE_MATCH_KINDS = {
    "path_prefix",
    "path_glob",
    "repository_fallback",
}
REVIEWER_SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
REVIEWER_SOURCE_INPUT_FIELDS = {"schema_version", "sources"}
REVIEWER_SOURCE_FIELDS = {
    "source_id",
    "source_kind",
    "reference",
    "trust",
    "freshness",
    "observed_at",
    "routes",
}
REVIEWER_SOURCE_ROUTE_FIELDS = {
    "route_id",
    "match_kind",
    "pattern",
    "primary_reviewers",
    "fallback_reviewers",
}
MAX_REVIEWER_SOURCES = 12
MAX_REVIEWER_SOURCE_ROUTES = 100


def _run_git(repo_path: Path, args: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed with exit code {result.returncode}")
    return result.stdout


def _normalise_changed_file(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if not text or path.as_posix() == "." or path.is_absolute() or ".." in path.parts:
        raise ValueError("changed files must be non-empty repo-relative paths")
    return path.as_posix()


def _normalise_changed_files(values: Sequence[Any]) -> list[str]:
    files: list[str] = []
    for raw in values:
        path = _normalise_changed_file(raw)
        if path not in files:
            files.append(path)
    if len(files) > 100:
        raise ValueError("reviewer recommendation accepts at most 100 changed files")
    return files


def _normalise_base_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not SAFE_GIT_REF_PATTERN.fullmatch(text):
        raise ValueError("base_ref must be a compact non-option git revision")
    return text


def _normalise_reviewer_handle(value: Any) -> str | None:
    text = str(value or "").strip()
    if text and not text.startswith("@"):
        text = f"@{text}"
    return text.lower() if REVIEWER_HANDLE_PATTERN.fullmatch(text) else None


def _normalise_author_name(value: Any) -> str | None:
    text = public_safe_compact_text(value, limit=80)
    return " ".join(text.lower().split()) if text else None


def _normalise_resolved_identities(
    values: Mapping[str, Any] | None,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for raw_name, raw_handle in (values or {}).items():
        name = _normalise_author_name(raw_name)
        handle = _normalise_reviewer_handle(raw_handle)
        if not name or not handle:
            raise ValueError(
                "resolved reviewer identities require a public-safe git display "
                "name and normalized GitHub handle"
            )
        resolved[name] = handle
    if len(resolved) > 50:
        raise ValueError("at most 50 reviewer identities may be resolved per request")
    return resolved


def _normalise_source_reference(value: Any, *, field: str) -> str:
    reference = public_safe_compact_text(value, limit=300)
    if not reference:
        raise ValueError(f"{field} must be compact and public-safe")
    if "://" in reference:
        parsed = urlsplit(reference)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
        ):
            raise ValueError(
                f"{field} URL must be public https without user info or query"
            )
        hostname = (parsed.hostname or "").lower()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
            raise ValueError(f"{field} URL must not target a local host")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            raise ValueError(f"{field} URL must target a public host")
        return reference
    if reference.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", reference):
        raise ValueError(f"{field} must not be an absolute or home-relative path")
    path = PurePosixPath(reference.replace("\\", "/"))
    if path.as_posix() == "." or ".." in path.parts:
        raise ValueError(f"{field} must be a repo-relative public reference")
    return path.as_posix()


def _normalise_source_handle_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a list of GitHub handles")
    handles: list[str] = []
    for raw in value:
        handle = _normalise_reviewer_handle(raw)
        if not handle:
            raise ValueError(f"{field} must contain normalized GitHub handles")
        if handle not in handles:
            handles.append(handle)
    if len(handles) > 10:
        raise ValueError(f"{field} accepts at most 10 reviewer handles")
    return handles


def _normalise_source_observed_at(value: Any, *, field: str) -> str:
    observed_at = public_safe_compact_text(value, limit=50)
    if not observed_at:
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp")
    return observed_at


def _normalise_source_route_pattern(value: Any, *, field: str) -> str:
    pattern = public_safe_compact_text(value, limit=220).replace("\\", "/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    if not pattern or pattern.startswith(("/", "~")):
        raise ValueError(f"{field} must be a repo-relative path pattern")
    path = PurePosixPath(pattern)
    if ".." in path.parts:
        raise ValueError(f"{field} must not traverse outside the repository")
    return pattern.rstrip("/")


def _normalise_reviewer_source_route(raw: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"reviewer source routes[{index}] must be an object")
    unknown = sorted(set(raw) - REVIEWER_SOURCE_ROUTE_FIELDS)
    if unknown:
        raise ValueError(
            f"reviewer source routes[{index}] has unsupported fields: {unknown}"
        )
    route_id = public_safe_compact_text(raw.get("route_id"), limit=120)
    if not route_id or not REVIEWER_SOURCE_ID_PATTERN.fullmatch(route_id):
        raise ValueError(f"reviewer source routes[{index}].route_id has invalid shape")
    match_kind = str(raw.get("match_kind") or "").strip()
    if match_kind not in REVIEWER_SOURCE_MATCH_KINDS:
        raise ValueError(
            f"reviewer source routes[{index}].match_kind must be one of "
            f"{sorted(REVIEWER_SOURCE_MATCH_KINDS)}"
        )
    pattern = None
    if match_kind != "repository_fallback":
        pattern = _normalise_source_route_pattern(
            raw.get("pattern"), field=f"reviewer source routes[{index}].pattern"
        )
    elif raw.get("pattern") not in {None, ""}:
        raise ValueError("repository_fallback routes must not declare a pattern")
    primary = _normalise_source_handle_list(
        raw.get("primary_reviewers"),
        field=f"reviewer source routes[{index}].primary_reviewers",
    )
    fallback = _normalise_source_handle_list(
        raw.get("fallback_reviewers"),
        field=f"reviewer source routes[{index}].fallback_reviewers",
    )
    if not primary and not fallback:
        raise ValueError(
            "reviewer source routes require a primary or fallback reviewer"
        )
    return {
        "schema_version": "issue_fix_reviewer_source_route_v0",
        "route_id": route_id,
        "match_kind": match_kind,
        "pattern": pattern,
        "primary_reviewers": primary,
        "fallback_reviewers": fallback,
    }


def _normalise_reviewer_sources(
    value: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, Mapping):
        raise ValueError("reviewer sources input must be an object")
    if value.get("schema_version") != ISSUE_FIX_REVIEWER_SOURCES_INPUT_SCHEMA_VERSION:
        raise ValueError(
            "reviewer sources schema_version must be "
            "issue_fix_reviewer_sources_input_v0"
        )
    unknown = sorted(set(value) - REVIEWER_SOURCE_INPUT_FIELDS)
    if unknown:
        raise ValueError(f"reviewer sources input has unsupported fields: {unknown}")
    raw_sources = value.get("sources") or []
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        raise ValueError("reviewer sources must be a list")
    if len(raw_sources) > MAX_REVIEWER_SOURCES:
        raise ValueError(
            f"reviewer sources accepts at most {MAX_REVIEWER_SOURCES} sources"
        )

    sources: list[dict[str, Any]] = []
    total_routes = 0
    for index, raw in enumerate(raw_sources):
        if not isinstance(raw, Mapping):
            raise ValueError(f"reviewer sources[{index}] must be an object")
        unknown = sorted(set(raw) - REVIEWER_SOURCE_FIELDS)
        if unknown:
            raise ValueError(
                f"reviewer sources[{index}] has unsupported fields: {unknown}"
            )
        source_id = public_safe_compact_text(raw.get("source_id"), limit=120)
        if not source_id or not REVIEWER_SOURCE_ID_PATTERN.fullmatch(source_id):
            raise ValueError(f"reviewer sources[{index}].source_id has invalid shape")
        source_kind = str(raw.get("source_kind") or "").strip()
        if source_kind not in REVIEWER_SOURCE_KINDS:
            raise ValueError(
                f"reviewer sources[{index}].source_kind must be one of "
                f"{sorted(REVIEWER_SOURCE_KINDS)}"
            )
        trust = str(raw.get("trust") or "").strip()
        if trust not in REVIEWER_SOURCE_TRUST_LEVELS:
            raise ValueError(
                f"reviewer sources[{index}].trust must be one of "
                f"{sorted(REVIEWER_SOURCE_TRUST_LEVELS)}"
            )
        freshness = str(raw.get("freshness") or "unknown").strip()
        if freshness not in REVIEWER_SOURCE_FRESHNESS_STATES:
            raise ValueError(
                f"reviewer sources[{index}].freshness must be one of "
                f"{sorted(REVIEWER_SOURCE_FRESHNESS_STATES)}"
            )
        raw_routes = raw.get("routes") or []
        if not isinstance(raw_routes, Sequence) or isinstance(raw_routes, (str, bytes)):
            raise ValueError(f"reviewer sources[{index}].routes must be a list")
        if not raw_routes:
            raise ValueError(f"reviewer sources[{index}].routes must not be empty")
        total_routes += len(raw_routes)
        if total_routes > MAX_REVIEWER_SOURCE_ROUTES:
            raise ValueError(
                f"reviewer sources accepts at most {MAX_REVIEWER_SOURCE_ROUTES} routes"
            )
        routes = [
            _normalise_reviewer_source_route(route, index=route_index)
            for route_index, route in enumerate(raw_routes)
        ]
        route_ids = [route["route_id"] for route in routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError(
                f"reviewer sources[{index}] route_id values must be unique"
            )
        sources.append(
            {
                "schema_version": "issue_fix_reviewer_source_v0",
                "source_id": source_id,
                "source_kind": source_kind,
                "reference": _normalise_source_reference(
                    raw.get("reference"),
                    field=f"reviewer sources[{index}].reference",
                ),
                "trust": trust,
                "freshness": freshness,
                "observed_at": _normalise_source_observed_at(
                    raw.get("observed_at"),
                    field=f"reviewer sources[{index}].observed_at",
                ),
                "routes": routes,
                "raw_content_captured": False,
            }
        )
    source_ids = [source["source_id"] for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("reviewer source_id values must be unique")
    return sources


def _codeowners_tokens(line: str) -> tuple[str, list[str]] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split()
    if len(parts) < 2:
        return None
    owners = [
        handle
        for handle in (_normalise_reviewer_handle(item) for item in parts[1:])
        if handle
    ]
    return (parts[0], owners) if owners else None


def _codeowners_pattern_matches(pattern: str, path: str) -> bool:
    pattern = pattern.strip()
    if not pattern or pattern.startswith("!"):
        return False
    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    if pattern.endswith("/"):
        prefix = pattern.rstrip("/")
        return path == prefix or path.startswith(f"{prefix}/")
    if "/" not in pattern:
        return any(fnmatch.fnmatchcase(part, pattern) for part in path.split("/"))
    if anchored:
        return fnmatch.fnmatchcase(path, pattern)
    return fnmatch.fnmatchcase(path, pattern) or PurePosixPath(path).match(pattern)


def _load_codeowners(repo_path: Path) -> tuple[str | None, list[tuple[str, list[str]]]]:
    for relative in CODEOWNERS_LOCATIONS:
        candidate = repo_path / relative
        if not candidate.is_file():
            continue
        rows: list[tuple[str, list[str]]] = []
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            parsed = _codeowners_tokens(line)
            if parsed is not None:
                rows.append(parsed)
        return relative, rows
    return None, []


def _owners_for_path(
    rules: Sequence[tuple[str, list[str]]],
    path: str,
) -> tuple[str | None, list[str]]:
    matched_pattern: str | None = None
    owners: list[str] = []
    for pattern, rule_owners in rules:
        if _codeowners_pattern_matches(pattern, path):
            matched_pattern = pattern
            owners = list(rule_owners)
    return matched_pattern, owners


def _github_handle_from_email(value: str) -> str | None:
    match = GITHUB_NOREPLY_PATTERN.fullmatch(value.strip())
    return f"@{match.group('login').lower()}" if match else None


def _looks_like_automated_history_identity(
    *,
    display_name: str,
    handle: str | None,
) -> bool:
    values = [display_name, (handle or "").lstrip("@")]
    return any(AUTOMATED_HISTORY_IDENTITY_PATTERN.search(value) for value in values)


def _candidate_key(*, handle: str | None, display_name: str) -> str:
    if handle:
        return f"handle:{handle}"
    normalised_name = " ".join(display_name.lower().split())
    digest = hashlib.sha256(normalised_name.encode("utf-8")).hexdigest()[:12]
    return f"git-author:{digest}"


def _candidate_id(key: str) -> str:
    return "reviewer_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _new_candidate(
    *,
    key: str,
    handle: str | None,
    display_name: str,
) -> dict[str, Any]:
    return {
        "candidate_id": _candidate_id(key),
        "reviewer_handle": handle,
        "display_name": display_name,
        "requestable": bool(handle),
        "score": 0,
        "source_kinds": [],
        "reason_codes": [],
        "matched_files": [],
        "codeowners_patterns": [],
        "source_refs": [],
        "reviewer_source_evidence": [],
        "history_commit_count": 0,
        "most_recent_history_rank": None,
    }


def _append_unique(row: dict[str, Any], field: str, value: str, *, limit: int = 20) -> None:
    values = row[field]
    if value not in values and len(values) < limit:
        values.append(value)


def _reviewer_source_route_matches(route: Mapping[str, Any], path: str) -> bool:
    match_kind = route.get("match_kind")
    pattern = str(route.get("pattern") or "")
    if match_kind == "path_prefix":
        return path == pattern or path.startswith(f"{pattern}/")
    if match_kind == "path_glob":
        return fnmatch.fnmatchcase(path, pattern) or PurePosixPath(path).match(pattern)
    return False


def _reviewer_source_route_specificity(route: Mapping[str, Any]) -> int:
    pattern = str(route.get("pattern") or "")
    return len(re.sub(r"[*?\[\]]", "", pattern))


def _reviewer_source_routes_for_path(
    source: Mapping[str, Any], path: str
) -> list[Mapping[str, Any]]:
    routes = source.get("routes")
    routes = routes if isinstance(routes, list) else []
    scoped = [
        route
        for route in routes
        if isinstance(route, Mapping) and _reviewer_source_route_matches(route, path)
    ]
    if scoped:
        max_specificity = max(
            _reviewer_source_route_specificity(route) for route in scoped
        )
        return [
            route
            for route in scoped
            if _reviewer_source_route_specificity(route) == max_specificity
        ]
    return [
        route
        for route in routes
        if isinstance(route, Mapping)
        and route.get("match_kind") == "repository_fallback"
    ]


def _reviewer_source_score(*, role: str, trust: str, freshness: str) -> int:
    base = 950 if role == "primary" else 250
    trust_factor = {
        "authoritative": 1.0,
        "verified": 0.9,
        "advisory": 0.5,
    }[trust]
    freshness_factor = {"current": 1.0, "unknown": 0.75, "stale": 0.4}[freshness]
    return max(1, int(base * trust_factor * freshness_factor))


def _append_reviewer_source_evidence(
    row: dict[str, Any], evidence: Mapping[str, Any]
) -> None:
    values = row["reviewer_source_evidence"]
    identity = (
        evidence.get("source_id"),
        evidence.get("route_id"),
        evidence.get("role"),
        evidence.get("matched_file"),
    )
    if any(
        (
            item.get("source_id"),
            item.get("route_id"),
            item.get("role"),
            item.get("matched_file"),
        )
        == identity
        for item in values
        if isinstance(item, Mapping)
    ):
        return
    if len(values) < 30:
        values.append(dict(evidence))


def _apply_reviewer_sources_for_path(
    *,
    candidates: dict[str, dict[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    changed_file: str,
    excluded: set[str],
) -> None:
    for source in sources:
        reference = str(source["reference"])
        trust = str(source["trust"])
        freshness = str(source["freshness"])
        observed_at = str(source["observed_at"])
        for route in _reviewer_source_routes_for_path(source, changed_file):
            match_kind = str(route["match_kind"])
            for role, field in (
                ("primary", "primary_reviewers"),
                ("fallback", "fallback_reviewers"),
            ):
                for handle in route.get(field) or []:
                    if handle in excluded:
                        continue
                    key = _candidate_key(handle=handle, display_name=handle)
                    row = candidates.setdefault(
                        key,
                        _new_candidate(key=key, handle=handle, display_name=handle),
                    )
                    row["score"] += _reviewer_source_score(
                        role=role,
                        trust=trust,
                        freshness=freshness,
                    )
                    _append_unique(row, "source_kinds", "repository_maintainer_map")
                    _append_unique(row, "source_refs", reference)
                    _append_unique(row, "matched_files", changed_file)
                    _append_unique(
                        row,
                        "reason_codes",
                        (
                            "repository_declared_primary_contact"
                            if role == "primary"
                            else "repository_declared_fallback_contact"
                        ),
                    )
                    if match_kind == "repository_fallback":
                        _append_unique(
                            row,
                            "reason_codes",
                            "repository_declared_cross_module_fallback",
                        )
                    _append_reviewer_source_evidence(
                        row,
                        {
                            "schema_version": "issue_fix_reviewer_source_evidence_v0",
                            "source_id": source["source_id"],
                            "source_kind": source["source_kind"],
                            "reference": reference,
                            "trust": trust,
                            "freshness": freshness,
                            "observed_at": observed_at,
                            "route_id": route["route_id"],
                            "match_kind": match_kind,
                            "pattern": route.get("pattern"),
                            "role": role,
                            "matched_file": changed_file,
                            "raw_content_captured": False,
                        },
                    )


def _derive_changed_files(repo_path: Path, base_ref: str) -> list[str]:
    output = _run_git(repo_path, ["diff", "--name-only", f"{base_ref}...HEAD"])
    # Deliberately left on `splitlines()`: `git diff --name-only` quotes
    # non-ASCII pathnames as octal escapes (`core.quotePath` defaults to true),
    # so this stream cannot carry a raw U+0085/U+2028 that would tear a record.
    # Streams that emit substituted values verbatim are framed on LF instead;
    # see `_collect_history`.
    return _normalise_changed_files(output.splitlines())


def _collect_history(
    repo_path: Path,
    path: str,
    *,
    revision: str,
    history_limit: int,
) -> list[tuple[str, str]]:
    output = _run_git(
        repo_path,
        [
            "log",
            f"--max-count={history_limit}",
            "--format=%aN%x1f%aE",
            revision,
            "--",
            path,
        ],
    )
    rows: list[tuple[str, str]] = []
    # `git log --format=` writes one record per LF and substitutes `%aN`/`%aE`
    # verbatim, so a name may contain U+0085, U+2028, U+2029 or the ASCII
    # separators U+000B/U+000C/U+001C-U+001E. `str.splitlines()` treats every
    # one of those as a line break and would tear one record into fragments,
    # which the `if not separator: continue` guard below would then swallow.
    for line in output.split("\n"):
        name, separator, email = line.partition("\x1f")
        if not separator:
            continue
        safe_name = public_safe_compact_text(name, limit=80)
        if safe_name:
            rows.append((safe_name, email.strip()))
    return rows


def _confidence(candidate: Mapping[str, Any]) -> str:
    sources = set(candidate.get("source_kinds") or [])
    coverage = len(candidate.get("matched_files") or [])
    if "git_history" in sources and sources.intersection(
        {"codeowners", "repository_maintainer_map"}
    ):
        return "high"
    if (
        "codeowners" in sources
        or "repository_declared_primary_contact"
        in set(candidate.get("reason_codes") or [])
        or coverage >= 2
    ):
        return "medium"
    return "low"


def _recommendation_status(candidates: Sequence[Mapping[str, Any]]) -> str:
    if any(candidate.get("requestable") for candidate in candidates):
        return "candidates_ready"
    if candidates:
        return "identity_resolution_required"
    return "no_candidates"


def build_issue_fix_reviewer_recommendation_packet(
    *,
    repo_path: str | Path,
    repo: str = "approved_local_repo",
    changed_files: Sequence[str] = (),
    base_ref: str = "origin/main",
    history_limit: int = 40,
    max_candidates: int = 5,
    exclude_reviewers: Sequence[str] = (),
    exclude_author_names: Sequence[str] = (),
    resolved_identities: Mapping[str, Any] | None = None,
    reviewer_sources_input: Mapping[str, Any] | None = None,
    execute: bool = False,
    generated_at: str | None = "2026-07-10T00:00:00Z",
) -> dict[str, Any]:
    """Recommend reviewers from caller-approved repository ownership evidence."""

    safe_repo = public_safe_compact_text(repo, limit=120)
    if not safe_repo:
        raise ValueError("repo must be a compact public-safe label")
    safe_base_ref = _normalise_base_ref(
        public_safe_compact_text(base_ref, limit=120)
    )
    history_limit = min(max(int(history_limit), 1), 200)
    max_candidates = min(max(int(max_candidates), 1), 20)
    explicit_files = _normalise_changed_files(changed_files)
    excluded = {
        handle
        for handle in (_normalise_reviewer_handle(item) for item in exclude_reviewers)
        if handle
    }
    excluded_author_names = {
        name
        for name in (_normalise_author_name(item) for item in exclude_author_names)
        if name
    }
    excluded_author_names.update(handle.lstrip("@").lower() for handle in excluded)
    resolved = _normalise_resolved_identities(resolved_identities)
    reviewer_sources = _normalise_reviewer_sources(reviewer_sources_input)
    reviewer_source_refs = list(
        dict.fromkeys(str(source["reference"]) for source in reviewer_sources)
    )

    packet: dict[str, Any] = {
        "ok": True,
        "schema_version": ISSUE_FIX_REVIEWER_RECOMMENDATION_SCHEMA_VERSION,
        "mode": "issue-fix-reviewer-recommendation",
        "generated_at": generated_at,
        "repo": safe_repo,
        "execute": execute,
        "recommendation_status": "preview_only",
        "changed_files": explicit_files,
        "changed_file_count": len(explicit_files),
        "candidates": [],
        "excluded_reviewer_handles": sorted(excluded),
        "excluded_author_name_count": len(excluded_author_names),
        "resolved_identity_count": len(resolved),
        "reviewer_source_count": len(reviewer_sources),
        "reviewer_source_refs": reviewer_source_refs,
        "evidence_summary": {
            "schema_version": "issue_fix_reviewer_evidence_summary_v0",
            "authority_order": [
                "repository_codeowners",
                "repository_declared_primary_contact",
                "changed_path_git_history",
                "changed_module_git_history_fallback",
                "repository_declared_fallback_contact",
            ],
            "codeowners_source": None,
            "codeowners_pattern_support": "common_subset",
            "history_limit_per_file": history_limit,
            "history_revision": safe_base_ref,
            "module_history_fallback": True,
            "reviewer_source_count": len(reviewer_sources),
            "reviewer_source_refs": reviewer_source_refs,
            "automated_history_identities_excluded": True,
            "raw_reviewer_source_input_captured": False,
            "raw_codeowners_captured": False,
            "raw_git_output_captured": False,
            "commit_emails_captured": False,
        },
        "policy": {
            "schema_version": "issue_fix_reviewer_policy_v0",
            "recommendation_is_assignment": False,
            "automatic_review_request_allowed": True,
            "automatic_request_policy": "request_top_requestable_when_authorized",
            "external_review_request_authority_required": True,
            "default_max_review_requests": 1,
            "repository_policy_still_applies": True,
            "current_author_should_be_excluded_by_request_command": True,
            "identity_resolution_required_for_non_handle_candidates": True,
            "next_action": (
                "After PR creation, run reviewer-request under external-review-request "
                "authority; it excludes the live PR author and requests the top "
                "requestable candidate."
            ),
        },
        "external_reads_performed": False,
        "external_writes_performed": False,
        "review_request_performed": False,
        "private_repo_state_read": False,
        "local_paths_captured": False,
        "raw_git_output_captured": False,
        "commit_emails_captured": False,
        "raw_reviewer_source_input_captured": False,
    }
    if not execute:
        packet["next_safe_action"] = (
            "Rerun with --execute only for the caller-approved local repository."
        )
        packet["validation"] = validate_issue_fix_reviewer_recommendation_packet(packet)
        return packet

    path = Path(repo_path).expanduser()
    if not path.is_dir():
        raise ValueError("repo_path must be an existing caller-approved directory")
    _run_git(path, ["rev-parse", "--is-inside-work-tree"])
    files = explicit_files or _derive_changed_files(path, safe_base_ref)
    if not files:
        raise ValueError("reviewer recommendation requires at least one changed file")

    codeowners_source, codeowners_rules = _load_codeowners(path)
    candidates: dict[str, dict[str, Any]] = {}
    for changed_file in files:
        pattern, owners = _owners_for_path(codeowners_rules, changed_file)
        for handle in owners:
            if handle in excluded:
                continue
            key = _candidate_key(handle=handle, display_name=handle)
            row = candidates.setdefault(
                key,
                _new_candidate(key=key, handle=handle, display_name=handle),
            )
            row["score"] += 1000
            _append_unique(row, "source_kinds", "codeowners")
            _append_unique(row, "reason_codes", "repository_codeowners_match")
            _append_unique(row, "matched_files", changed_file)
            if pattern:
                _append_unique(row, "codeowners_patterns", pattern)

        _apply_reviewer_sources_for_path(
            candidates=candidates,
            sources=reviewer_sources,
            changed_file=changed_file,
            excluded=excluded,
        )

        history_rows = _collect_history(
            path,
            changed_file,
            revision=safe_base_ref,
            history_limit=history_limit,
        )
        history_reason = "changed_path_commit_history"
        usable_history_rows = [
            row
            for row in history_rows
            if (
                _github_handle_from_email(row[1])
                or resolved.get(_normalise_author_name(row[0]) or "")
                or ""
            )
            not in excluded
            and _normalise_author_name(row[0]) not in excluded_author_names
            and not _looks_like_automated_history_identity(
                display_name=row[0],
                handle=(
                    _github_handle_from_email(row[1])
                    or resolved.get(_normalise_author_name(row[0]) or "")
                ),
            )
        ]
        if not usable_history_rows:
            parent = PurePosixPath(changed_file).parent.as_posix()
            if parent not in {"", "."}:
                history_rows = _collect_history(
                    path,
                    parent,
                    revision=safe_base_ref,
                    history_limit=history_limit,
                )
                history_reason = "changed_module_commit_history"
        for rank, (name, email) in enumerate(history_rows, start=1):
            resolved_handle = resolved.get(_normalise_author_name(name) or "")
            handle = _github_handle_from_email(email) or resolved_handle
            if handle and handle in excluded:
                continue
            if _normalise_author_name(name) in excluded_author_names:
                continue
            if _looks_like_automated_history_identity(
                display_name=name,
                handle=handle,
            ):
                continue
            key = _candidate_key(handle=handle, display_name=name)
            row = candidates.setdefault(
                key,
                _new_candidate(key=key, handle=handle, display_name=name),
            )
            row["score"] += max(1, history_limit - rank + 1)
            row["history_commit_count"] += 1
            recent_rank = row["most_recent_history_rank"]
            if recent_rank is None or rank < recent_rank:
                row["most_recent_history_rank"] = rank
            _append_unique(row, "source_kinds", "git_history")
            if resolved_handle:
                _append_unique(row, "source_kinds", "verified_identity_mapping")
                _append_unique(row, "reason_codes", "caller_verified_github_identity")
            _append_unique(row, "reason_codes", history_reason)
            if rank <= 5:
                _append_unique(row, "reason_codes", "recent_changed_path_contributor")
            if handle is None:
                _append_unique(row, "reason_codes", "github_identity_resolution_required")
            _append_unique(row, "matched_files", changed_file)

    ranked = sorted(
        candidates.values(),
        key=lambda row: (
            -int(row["score"]),
            -len(row["matched_files"]),
            str(row.get("reviewer_handle") or row.get("display_name") or ""),
        ),
    )[:max_candidates]
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
        row["confidence"] = _confidence(row)
        row["path_coverage_count"] = len(row["matched_files"])

    packet.update(
        {
            "recommendation_status": _recommendation_status(ranked),
            "changed_files": files,
            "changed_file_count": len(files),
            "candidates": ranked,
            "private_repo_state_read": True,
            "next_safe_action": (
                "Run reviewer-request after PR creation when external-review-request "
                "authority is active."
            ),
        }
    )
    packet["evidence_summary"]["codeowners_source"] = codeowners_source
    validation = validate_issue_fix_reviewer_recommendation_packet(packet)
    packet["ok"] = validation["ok"]
    packet["validation"] = validation
    return packet


def validate_issue_fix_reviewer_recommendation_packet(
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if packet.get("schema_version") != ISSUE_FIX_REVIEWER_RECOMMENDATION_SCHEMA_VERSION:
        errors.append("schema_version must be issue_fix_reviewer_recommendation_v0")
    for key in (
        "external_reads_performed",
        "external_writes_performed",
        "review_request_performed",
        "local_paths_captured",
        "raw_git_output_captured",
        "commit_emails_captured",
        "raw_reviewer_source_input_captured",
    ):
        if packet.get(key) is not False:
            errors.append(f"{key} must be false")
    execute = packet.get("execute") is True
    if packet.get("private_repo_state_read") is not execute:
        errors.append("private_repo_state_read must match execute")
    changed_files = packet.get("changed_files")
    if not isinstance(changed_files, list):
        errors.append("changed_files must be a list")
        changed_files = []
    for path in changed_files:
        try:
            _normalise_changed_file(path)
        except ValueError:
            errors.append("changed_files must contain only repo-relative paths")
            break
    reviewer_source_refs = packet.get("reviewer_source_refs")
    if not isinstance(reviewer_source_refs, list):
        errors.append("reviewer_source_refs must be a list")
        reviewer_source_refs = []
    for reference in reviewer_source_refs:
        try:
            _normalise_source_reference(reference, field="reviewer_source_refs")
        except ValueError:
            errors.append("reviewer_source_refs must contain public-safe references")
            break
    if not isinstance(packet.get("reviewer_source_count"), int):
        errors.append("reviewer_source_count must be an integer")
    candidates = packet.get("candidates")
    if not isinstance(candidates, list):
        errors.append("candidates must be a list")
        candidates = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            errors.append("reviewer candidate must be an object")
            continue
        handle = candidate.get("reviewer_handle")
        if handle is not None and _normalise_reviewer_handle(handle) != handle:
            errors.append("reviewer_handle must be a normalized GitHub handle")
        if candidate.get("requestable") is not bool(handle):
            errors.append("requestable must reflect reviewer_handle availability")
        source_refs = candidate.get("source_refs")
        if not isinstance(source_refs, list):
            errors.append("candidate source_refs must be a list")
        evidence = candidate.get("reviewer_source_evidence")
        if not isinstance(evidence, list):
            errors.append("candidate reviewer_source_evidence must be a list")
            evidence = []
        for item in evidence:
            if not isinstance(item, Mapping):
                errors.append("reviewer source evidence must be an object")
                continue
            if item.get("raw_content_captured") is not False:
                errors.append("reviewer source evidence must not capture raw content")
            try:
                _normalise_source_reference(
                    item.get("reference"), field="reviewer source evidence reference"
                )
                _normalise_source_observed_at(
                    item.get("observed_at"),
                    field="reviewer source evidence observed_at",
                )
            except ValueError:
                errors.append("reviewer source evidence must have safe provenance")
    policy = packet.get("policy")
    if not isinstance(policy, Mapping):
        errors.append("policy is required")
    elif policy.get("automatic_review_request_allowed") is not True:
        errors.append("reviewer policy must allow the authority-gated default request")
    elif policy.get("external_review_request_authority_required") is not True:
        errors.append("automatic reviewer requests require external-write authority")
    return {
        "ok": not errors,
        "schema_version": "issue_fix_reviewer_recommendation_validation_v0",
        "errors": errors,
    }


def render_issue_fix_reviewer_recommendation_markdown(
    payload: Mapping[str, Any],
) -> str:
    lines = [
        "# LoopX Issue-Fix Reviewer Recommendation",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- repo: `{payload.get('repo')}`",
        f"- execute: `{payload.get('execute')}`",
        f"- recommendation_status: `{payload.get('recommendation_status')}`",
        f"- changed_file_count: `{payload.get('changed_file_count')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- review_request_performed: `{payload.get('review_request_performed')}`",
        "",
        "## Candidates",
        "",
    ]
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        lines.append("- none")
    else:
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            identity = candidate.get("reviewer_handle") or candidate.get("display_name")
            lines.append(
                f"{candidate.get('rank')}. `{identity}` score=`{candidate.get('score')}` "
                f"confidence=`{candidate.get('confidence')}` "
                f"sources=`{','.join(candidate.get('source_kinds') or [])}` "
                f"refs=`{','.join(candidate.get('source_refs') or [])}`"
            )
    lines.extend(["", f"Next: {payload.get('next_safe_action')}"])
    return "\n".join(lines)
