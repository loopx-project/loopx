"""Public-safe outbound scanner: fail-closed pre-submit scrubbing.

Detects company-internal info, credentials, private absolute paths, internal
links, and raw benchmark evidence in a set of candidate paths (or text blobs)
before they are pushed to an external repository / PR.

The scanner is deliberately conservative: any hit fails closed with a compact
receipt. The receipt never carries an absolute path -- a hit is labelled by its
path relative to the scan root -- and never carries scanned content: a hit is
located by its line number, and `sample_masked` is always fully redacted.
It does not modify files.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping


SCHEMA_VERSION = "public_safe_outbound_scan_v0"

# --- rule table (ordered; first matching rule wins per file) ----------------
RULES: tuple[dict[str, str], ...] = (
    # credentials
    {"id": "ark_api_key", "label": "Ark/ARK API key", "pattern": r"\b" + "ark" + r"-[0-9a-f]{8,}-[0-9a-f-]{20,}"},
    {"id": "sk_bearer", "label": "sk-/Bearer secret", "pattern": r"\b(" + "sk" + r"-[A-Za-z0-9_-]{8,}|Bearer [A-Za-z0-9._-]{16,})\b"},
    {"id": "aklt_access_key", "label": "AK/SK access key", "pattern": r"\b" + "AKLT" + r"[A-Za-z0-9]{10,}\b"},
    {"id": "private_key_block", "label": "private key block", "pattern": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"},
    {"id": "api_key_assignment", "label": "api key assignment", "pattern": r"(?i)\b(api[_-]?key|access[_-]?key|secret)\b\s*[=:]\s*['\"][^'\"]{8,}['\"]"},
    # Private absolute paths: the private roots under whatever separator the
    # host or a serialized string chose. A separator run at every junction
    # covers the POSIX spelling (/Users/alice/), the native Windows spellings
    # (\Users\alice\ and C:\Users\alice\) and the mixed or escaped spellings of
    # serialized text (\\Users\\alice\\), matching the boundary scan's
    # separator-run class (#6161). The account segment is Unicode-aware and
    # need not be followed by another separator: environment variables and
    # config values commonly end at the account name. Matching is
    # case-insensitive because Windows path comparison is case-insensitive.
    {
        "id": "private_abs_path",
        "label": "private absolute path",
        "pattern": r"(?i)[\\/]+(?:users|home|data00|root)[\\/]+[^\s\\/\"'<>]+",
    },
)


def _load_config() -> tuple[dict, str | None]:
    """Load company-internal markers from a LOCAL config file (never shipped in
    this public source). Point PUBLIC_SAFE_CONFIG at a JSON file with optional
    keys: internal_domains, internal_url_patterns, private_path_prefixes,
    credential_patterns.

    An unset variable is the documented no-op: only the generic rules run. A
    variable that is set but cannot be read or parsed is different -- the
    operator asked for company markers and did not get them -- so it reports
    ``config_unreadable`` and the scan fails closed instead of silently
    downgrading to the generic rule set.
    """
    path = os.environ.get("PUBLIC_SAFE_CONFIG")
    if not path:
        return {}, None
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, "config_unreadable"
    if not isinstance(loaded, dict):
        return {}, "config_unreadable"
    return loaded, None


# The only value a public receipt ever shows for a hit's content. A context
# window is not safe to publish: the bytes beside a match are often more of the
# same secret (a private key body after its header, a URL path after an internal
# host, a second credential on the same line), and no rule set can prove the
# neighbourhood clean. The line number locates the hit instead.
REDACTED_SAMPLE = "***"


@dataclass(frozen=True)
class ScanHit:
    rule_id: str
    label: str
    location: str
    # 1-based line of the match in the scanned text, or None when the match
    # cannot be mapped back to an original line. A count, never content.
    line: int | None = None


@dataclass
class ScanResult:
    ok: bool
    hits: list[ScanHit] = field(default_factory=list)
    # Content-free reason codes for input the scan was asked to check but could
    # not read. A caller that requested a file and got `ok` because it was
    # skipped would otherwise treat unscanned content as public-safe.
    unscanned: list[str] = field(default_factory=list)

    def to_public_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": self.ok,
            "hit_count": len(self.hits),
            "unscanned_count": len(self.unscanned),
            "unscanned_reasons": sorted(set(self.unscanned)),
            "hits": [
                {
                    "rule_id": h.rule_id,
                    "label": h.label,
                    "location": h.location,
                    "line": h.line,
                    "sample_masked": REDACTED_SAMPLE,
                }
                for h in self.hits
            ],
        }


def _location(path: Path, root: Path | None) -> str:
    """A caller-facing label that is never an absolute path.

    The receipt is public-safe, so a hit may name a file only relative to the
    scan root; an absolute path with no root is reduced to its final
    component rather than echoed back. When the root *is* the file under
    scan, the label is the file's own name -- ``relative_to`` would only say
    ``.``, which names nothing an operator can act on.
    """
    if root is not None and root.is_dir():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            pass
    if not path.is_absolute():
        return path.as_posix()
    return path.name


_WHITESPACE_RUN = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    # collapse whitespace so multi-line evidence still matches
    return _WHITESPACE_RUN.sub(" ", text)


def _raw_line(raw: str, normalized_index: int | None) -> int | None:
    """1-based line in ``raw`` of a character found in ``_normalize_text(raw)``.

    Rules match the whitespace-collapsed text, so its indexes are not offsets
    into ``raw``: each collapsed run of n whitespace characters shifts every
    later character by n - 1. The shift is undone run by run so the line is
    counted in the original text.
    """
    if normalized_index is None:
        return None
    shift = 0
    for run in _WHITESPACE_RUN.finditer(raw):
        if normalized_index <= run.start() - shift:
            break
        shift += len(run.group(0)) - 1
    return raw.count("\n", 0, normalized_index + shift) + 1


def _lowered_index(text: str, lowered: str, needle: str) -> int | None:
    """Index in ``text`` of ``needle`` found in ``lowered = text.lower()``.

    ``str.lower`` maps each character to one or more characters, so an index
    into ``lowered`` names the same character of ``text`` only when no
    character expanded, i.e. when both have the same length. Otherwise the hit
    is still reported, only without a line.
    """
    index = lowered.find(needle)
    if index < 0 or len(lowered) != len(text):
        return None
    return index


@dataclass(frozen=True)
class _Markers:
    """Company markers from the local config, normalized once per scan."""

    domains: tuple[str, ...] = ()
    url_patterns: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    credential_patterns: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, cfg: Mapping) -> "_Markers":
        return cls(
            domains=tuple(str(d).strip().lower().lstrip(".") for d in cfg.get("internal_domains", [])),
            url_patterns=tuple(str(p).lower() for p in cfg.get("internal_url_patterns", [])),
            path_prefixes=tuple(str(p) for p in cfg.get("private_path_prefixes", [])),
            credential_patterns=tuple(str(c).lower() for c in cfg.get("credential_patterns", [])),
        )


def _scan_one(location: str, raw: str, markers: _Markers) -> list[ScanHit]:
    """Every rule that matches one input, at most one hit per rule id."""
    text = _normalize_text(raw)
    lowered = text.lower()
    hits: list[ScanHit] = []
    # collect ALL hits (generic rules + configured markers), deduped
    seen: set[str] = set()
    for rule in RULES:
        match = re.search(rule["pattern"], text)
        if match and rule["id"] not in seen:
            seen.add(rule["id"])
            hits.append(ScanHit(rule["id"], rule["label"], location, _raw_line(raw, match.start())))
    for domain in markers.domains:
        if ("." + domain) in lowered and "internal_domain" not in seen:
            seen.add("internal_domain")
            line = _raw_line(raw, _lowered_index(text, lowered, "." + domain))
            hits.append(ScanHit("internal_domain", "company-internal domain (local config)", location, line))
    for pattern in markers.url_patterns:
        if pattern in lowered and "internal_url" not in seen:
            seen.add("internal_url")
            line = _raw_line(raw, _lowered_index(text, lowered, pattern))
            hits.append(ScanHit("internal_url", "company-internal link (local config)", location, line))
    for prefix in markers.path_prefixes:
        if prefix in text and "private_path" not in seen:
            seen.add("private_path")
            line = _raw_line(raw, text.find(prefix))
            hits.append(ScanHit("private_path", "private absolute path (local config)", location, line))
    for pattern in markers.credential_patterns:
        if pattern in lowered and "credential" not in seen:
            seen.add("credential")
            line = _raw_line(raw, _lowered_index(text, lowered, pattern))
            hits.append(ScanHit("credential", "credential pattern (local config)", location, line))
    return hits


def _start_scan() -> tuple[ScanResult, _Markers]:
    result = ScanResult(ok=True)
    cfg, config_error = _load_config()
    if config_error:
        result.unscanned.append(config_error)
    return result, _Markers.from_config(cfg)


def _finish(result: ScanResult) -> ScanResult:
    result.ok = not result.hits and not result.unscanned
    return result


def scan_texts(texts: Mapping[str, str]) -> ScanResult:
    result, markers = _start_scan()
    for location, raw in texts.items():
        result.hits.extend(_scan_one(location, raw, markers))
    return _finish(result)


def scan_paths(
    paths: Iterable[Path],
    *,
    root: Path | None = None,
    limit_bytes: int = 1_000_000,
) -> ScanResult:
    """Scan the given files; a file that cannot be read fails the whole scan.

    ``root`` labels each hit by its path relative to that root. Without one, a
    relative input keeps its relative spelling and an absolute input is
    reduced to its final component, so the public receipt never echoes an
    absolute private path back to the caller -- the very thing this scanner
    exists to catch.

    The label is for display only and never identifies an input. Each file is
    scanned on its own as soon as it is read, so two different files that
    reduce to the same label (two ``entry.md`` in different directories) each
    keep their hits; a path repeated with the same spelling is scanned once.

    A path that is not a file (a directory, or a glob that matched nothing) is
    skipped: it names no content to check. A file that exists but cannot be
    read, or that exceeds ``limit_bytes``, is content the caller asked to check
    and did not get, so it is reported as unscanned and ``ok`` stays False.
    Skip reasons are content-free, like the rest of the receipt.
    """
    result, markers = _start_scan()
    seen: set[str] = set()
    for path in paths:
        p = Path(path)
        if str(p) in seen:
            continue
        seen.add(str(p))
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            result.unscanned.append("content_unreadable")
            continue
        if size > limit_bytes:
            result.unscanned.append("content_too_large")
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            result.unscanned.append("content_unreadable")
            continue
        result.hits.extend(_scan_one(_location(p, root), text, markers))
    return _finish(result)


def scan_git_diff(diff: str) -> ScanResult:
    return scan_texts({"<git-diff>": diff})


def compact_json(result: ScanResult) -> str:
    return json.dumps(result.to_public_dict(), ensure_ascii=False, sort_keys=True)
