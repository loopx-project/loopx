"""Public-safe outbound scanner: fail-closed pre-submit scrubbing.

Detects company-internal info, credentials, private absolute paths, internal
links, and raw benchmark evidence in a set of candidate paths (or text blobs)
before they are pushed to an external repository / PR.

The scanner is deliberately conservative: any hit fails closed with a compact,
path-free receipt. It does not modify files.
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


def _load_config() -> dict:
    """Load company-internal markers from a LOCAL config file (never shipped in
    this public source). Point PUBLIC_SAFE_CONFIG at a JSON file with optional
    keys: internal_domains, internal_url_patterns, private_path_prefixes,
    credential_patterns."""
    path = os.environ.get("PUBLIC_SAFE_CONFIG")
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


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

    def to_public_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": self.ok,
            "hit_count": len(self.hits),
            "hits": [
                {
                    "rule_id": h.rule_id,
                    "label": h.label,
                    "location": h.location,
                    "sample_masked": _mask(h.sample),
                }
                for h in self.hits
            ],
        }


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def _match_rule(text: str) -> tuple[str, str] | None:
    for rule in RULES:
        m = re.search(rule["pattern"], text)
        if m:
            return rule["id"], rule["label"]
    return None


def _normalize_text(text: str) -> str:
    # collapse whitespace so multi-line evidence still matches
    return re.sub(r"\s+", " ", text)


def scan_texts(texts: Mapping[str, str]) -> ScanResult:
    result = ScanResult(ok=True)
    cfg = _load_config()
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
            if re.search(rule["pattern"], text):
                if rule["id"] not in seen:
                    seen.add(rule["id"])
                    result.hits.append(ScanHit(rule["id"], rule["label"], location, raw[:120]))
        for domain in domains:
            if ("." + domain) in lowered and "internal_domain" not in seen:
                seen.add("internal_domain")
                result.hits.append(
                    ScanHit("internal_domain", "company-internal domain (local config)", location, raw[:120])
                )
        for pattern in url_patterns:
            if pattern in lowered and "internal_url" not in seen:
                seen.add("internal_url")
                result.hits.append(
                    ScanHit("internal_url", "company-internal link (local config)", location, raw[:120])
                )
        for prefix in path_prefixes:
            if prefix in text and "private_path" not in seen:
                seen.add("private_path")
                result.hits.append(
                    ScanHit("private_path", "private absolute path (local config)", location, raw[:120])
                )
        for pattern in credential_patterns:
            if pattern in lowered and "credential" not in seen:
                seen.add("credential")
                result.hits.append(
                    ScanHit("credential", "credential pattern (local config)", location, raw[:120])
                )
    result.ok = not result.hits
    return result


def scan_paths(paths: Iterable[Path], *, limit_bytes: int = 1_000_000) -> ScanResult:
    texts: dict[str, str] = {}
    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        if p.stat().st_size > limit_bytes:
            continue
        try:
            texts[str(p)] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return scan_texts(texts)


def scan_git_diff(diff: str) -> ScanResult:
    return scan_texts({"<git-diff>": diff})


def compact_json(result: ScanResult) -> str:
    return json.dumps(result.to_public_dict(), ensure_ascii=False, sort_keys=True)
