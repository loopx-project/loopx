"""Explicit public GitHub source-inspection method; no credentials or raw persistence.

This bundled provider owns HTTP access. External evidence's TypeScript contract
owns exact-plan validation, parent admission and retirement.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PROVIDER_ID = "method:public-github"
MAX_SOURCE_BYTES = 1_000_000
SOURCE = re.compile(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/blob/([0-9a-f]{40})/(.+)")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read(url: str) -> bytes:
    # No provider credentials, cookies, user-configured proxy credentials or
    # caller-selected origins enter the request. Redirects fail closed.
    request = Request(url, headers={"User-Agent": "LoopX-public-evidence",
        "Accept": "application/vnd.github+json" if url.startswith("https://api.github.com/") else "text/plain"})
    with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=15) as response:
        result = response.read(MAX_SOURCE_BYTES + 1)
    if len(result) > MAX_SOURCE_BYTES:
        raise ValueError("public source exceeds the bounded read limit")
    return result


def _source(ref: str) -> tuple[str, str, str, str]:
    parsed = urlsplit(ref)
    match = SOURCE.fullmatch(parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query
        or parsed.fragment or match is None):
        raise ValueError("public GitHub sources require https://github.com/OWNER/REPO/blob/FULL_COMMIT_SHA/PATH")
    owner, repo, revision, path = match.groups()
    decoded = unquote(path)
    if (any(part in {"", ".", ".."} for part in decoded.split("/")) or "\\" in decoded
        or any(ord(char) < 32 for char in decoded) or "%" in decoded
        or owner in {".", ".."} or repo in {".", ".."}):
        raise ValueError("public GitHub source path is invalid")
    return owner, repo, revision, decoded


def _public_repository(owner: str, repo: str) -> None:
    value = json.loads(_read(f"https://api.github.com/repos/{owner}/{repo}"))
    if not isinstance(value, dict) or value.get("private") is not False:
        raise ValueError("provider only reads currently public GitHub repositories")


def inspect_provider(source_refs: list[str]) -> dict:
    """Current opt-in readiness, not registry inventory or execution proof."""
    sources = [_source(ref) for ref in source_refs]
    if not sources:
        raise ValueError("public GitHub inspection requires at least one pinned source")
    reason = None
    try:
        for owner, repo in sorted({(source[0], source[1]) for source in sources}):
            _public_repository(owner, repo)
    except (OSError, ValueError, URLError):
        reason = "public_github_readiness_unavailable"
    return {"provider_id": PROVIDER_ID, "provider_kind": "method",
        "protocol": "external_evidence_research_v0", "declared": True,
        "installed": True, "enabled": True, "ready": reason is None,
        "unavailable_reason": reason}


def execute_public_github(plan: dict) -> dict:
    """Read only exact-plan sources, with fresh public-visibility checks.

    Caller validates the canonical plan through the typed owner before entry.
    Findings are retrieval and literal-match facts, not autonomous conclusions.
    """
    selected = plan["selected_provider"]
    if selected["provider_id"] != PROVIDER_ID or selected["provider_kind"] != "method":
        raise ValueError("this executor requires the selected public GitHub method")
    request = plan["request"]
    refs = request.get("source_refs", [])
    sources = [_source(ref) for ref in refs]
    if not sources or len(sources) > 8:
        raise ValueError("public GitHub execution requires one to eight pinned sources")
    terms = request.get("search_terms", [])
    records, failures = [], []
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for index, (ref, (owner, repo, revision, path)) in enumerate(zip(refs, sources), 1):
        try:
            _public_repository(owner, repo)
            raw = _read(f"https://raw.githubusercontent.com/{owner}/{repo}/{revision}/{quote(path, safe='/')}")
            text = raw.decode("utf-8")
            if "\x00" in text:
                raise ValueError("source is not UTF-8 text")
            if not text.strip():
                failures.append(f"Source {index}: empty source")
                continue
            matches = []
            for term in terms:
                lines = [str(index) for index, line in enumerate(text.splitlines(), 1) if term in line]
                matches.append(f"Literal term {json.dumps(term)}: " +
                    ("observed at lines " + ", ".join(lines[:16]) if lines else "not observed") +
                    (" (additional matches omitted)" if len(lines) > 16 else ""))
            finding = f"Read pinned file {path}; {len(raw)} UTF-8 bytes. " + " ".join(matches)
            if len(finding) > 4096:
                finding = finding[:4040] + " (additional match metadata omitted)"
            records.append({"source_ref": ref, "source_family": "github_repository_file",
                "basis": "observed", "finding": finding,
                "limitation": "Retrieval/literal matches only; no semantic conclusion, execution test or completeness claim.",
                "publication_date": None, "accessed_at": now,
                "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest()})
        except HTTPError as error:
            failures.append(f"Source {index}: HTTP {error.code}")
        except (OSError, ValueError, UnicodeError, URLError):
            failures.append(f"Source {index}: source read unavailable")
    status = "succeeded" if records else "no_evidence" if all("empty source" in item for item in failures) else "failed"
    receipt = {"schema_version": "loopx_external_evidence_receipt_v0",
        "plan_id": plan["plan_id"], "request_id": request["request_id"],
        "provider_id": PROVIDER_ID, "provider_kind": "method", "status": status,
        "sources": records, "summary": f"Read {len(records)} of {len(refs)} requested public sources.",
        "limitations": ["Original-source fallback remains available; parent admission is required.", *failures],
        "completed_at": now}
    return {"receipt": receipt, "execution": {"provider_id": PROVIDER_ID,
        "source_reads_observed": len(records), "requested_source_count": len(refs),
        "raw_content_persisted": False, "credentials_used": False,
        "automatic_admission": False, "evidence_coverage_observed": False}}
