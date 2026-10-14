"""Public-safe outbound scanner: fail-closed pre-submit scrubbing.

Detects company-internal info, credentials, private absolute paths, internal
links, and raw benchmark evidence in a set of candidate paths (or text blobs)
before they are pushed to an external repository / PR.

The scanner is deliberately conservative: any hit fails closed with a compact
receipt. The receipt never carries an absolute path -- a hit is labelled by its
path relative to the scan root -- and a hit's `sample_masked` is a bounded
window around the match with the matched span redacted, never the file head.
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
    # separator-run class (#6161).
    {
        "id": "private_abs_path",
        "label": "private absolute path",
        "pattern": r"[\\/]+(?:Users|home|data00|root)[\\/]+[A-Za-z0-9_.-]+[\\/]+",
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


@dataclass(frozen=True)
class ScanHit:
    rule_id: str
    label: str
    location: str
    sample: str


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
                    "sample_masked": h.sample,
                }
                for h in self.hits
            ],
        }


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def _redact(text: str, needle: str, *, radius: int = 40) -> str:
    """Bounded context around a match, with the matched span itself redacted.

    The public receipt must let an operator find the hit without repeating the
    matched bytes. Returning the file head instead would both hide the match
    and disclose unrelated leading content, so an unfindable needle falls back
    to a masked prefix of the text.
    """
    if not needle:
        return _mask(text[:120])
    index = text.casefold().find(needle.casefold())
    if index < 0:
        return _mask(text[:120])
    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    window = text[start:end]
    offset = index - start
    return window[:offset] + "***" + window[offset + len(needle):]


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


def _normalize_text(text: str) -> str:
    # collapse whitespace so multi-line evidence still matches
    return re.sub(r"\s+", " ", text)


def scan_texts(texts: Mapping[str, str]) -> ScanResult:
    result = ScanResult(ok=True)
    cfg, config_error = _load_config()
    if config_error:
        result.unscanned.append(config_error)
    domains = [str(d).strip().lower().lstrip(".") for d in cfg.get("internal_domains", [])]
    url_patterns = [str(p).lower() for p in cfg.get("internal_url_patterns", [])]
    path_prefixes = [str(p) for p in cfg.get("private_path_prefixes", [])]
    credential_patterns = [str(c).lower() for c in cfg.get("credential_patterns", [])]
    for location, raw in texts.items():
        text = _normalize_text(raw)
        lowered = text.lower()
        # collect ALL hits (generic rules + configured markers), deduped
        seen: set[str] = set()
        for rule in RULES:
            match = re.search(rule["pattern"], text)
            if match and rule["id"] not in seen:
                seen.add(rule["id"])
                result.hits.append(
                    ScanHit(
                        rule["id"],
                        rule["label"],
                        location,
                        _redact(raw, match.group(0)),
                    )
                )
        for domain in domains:
            if ("." + domain) in lowered and "internal_domain" not in seen:
                seen.add("internal_domain")
                result.hits.append(
                    ScanHit("internal_domain", "company-internal domain (local config)", location, _redact(raw, "." + domain))
                )
        for pattern in url_patterns:
            if pattern in lowered and "internal_url" not in seen:
                seen.add("internal_url")
                result.hits.append(
                    ScanHit("internal_url", "company-internal link (local config)", location, _redact(raw, pattern))
                )
        for prefix in path_prefixes:
            if prefix in text and "private_path" not in seen:
                seen.add("private_path")
                result.hits.append(
                    ScanHit("private_path", "private absolute path (local config)", location, _redact(raw, prefix))
                )
        for pattern in credential_patterns:
            if pattern in lowered and "credential" not in seen:
                seen.add("credential")
                result.hits.append(
                    ScanHit("credential", "credential pattern (local config)", location, _redact(raw, pattern))
                )
    result.ok = not result.hits and not result.unscanned
    return result


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

    A path that is not a file (a directory, or a glob that matched nothing) is
    skipped: it names no content to check. A file that exists but cannot be
    read, or that exceeds ``limit_bytes``, is content the caller asked to check
    and did not get, so it is reported as unscanned and ``ok`` stays False.
    Skip reasons are content-free, like the rest of the receipt.
    """
    texts: dict[str, str] = {}
    unscanned: list[str] = []
    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            unscanned.append("content_unreadable")
            continue
        if size > limit_bytes:
            unscanned.append("content_too_large")
            continue
        label = _location(p, root)
        try:
            texts[label] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            unscanned.append("content_unreadable")
    result = scan_texts(texts)
    if unscanned:
        result.unscanned.extend(unscanned)
        result.ok = False
    return result


def scan_git_diff(diff: str) -> ScanResult:
    return scan_texts({"<git-diff>": diff})


def compact_json(result: ScanResult) -> str:
    return json.dumps(result.to_public_dict(), ensure_ascii=False, sort_keys=True)
