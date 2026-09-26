"""Search the packaged repair reference without loading LoopX or any Goal state."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re


CATALOG = Path(__file__).resolve().parents[1] / "references" / "repair-patterns.md"
FIELDS = ("pattern", "symptoms", "evidence", "likely_root", "durable_repair")

# The installer/wheel bundles the canonical stdlib-only module beside this
# script. Source checkout execution reads that same file from the package.
_scorer_path = Path(__file__).with_name("lexical_retrieval.py")
if not _scorer_path.is_file():
    _scorer_path = Path(__file__).resolve().parents[3] / "loopx" / "lexical_retrieval.py"
_spec = importlib.util.spec_from_file_location("repair_lexical_retrieval", _scorer_path)
assert _spec is not None and _spec.loader is not None
_scorer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scorer)


def read_patterns(path: Path) -> list[dict[str, str]]:
    """Keep complete table cells and prose appendices; never silently drop a row."""
    patterns: list[dict[str, str]] = []
    section: dict[str, str] | None = None
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("| `"):
            # Delimiters have spaces; a code span such as `merged|all` is content.
            cells = re.split(r"\s+\|\s+", line.removeprefix("| ").removesuffix(" |"))
            if len(cells) != len(FIELDS) or not re.fullmatch(r"`[a-z0-9_]+`", cells[0]):
                raise ValueError(f"invalid pattern row at line {line_number}")
            row = dict(zip(FIELDS, cells))
            row["pattern"] = row["pattern"].strip("`")
            patterns.append(row)
        elif line.startswith(("## ", "### ")):
            title = line.lstrip("# ")
            section = {
                "pattern": "note_" + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"),
                "symptoms": title,
                "guidance": "",
            }
            patterns.append(section)
        elif section is not None:
            section["guidance"] += line + "\n"
        elif line.startswith("|") and not line.startswith(("| Pattern |", "| --- |")):
            raise ValueError(f"unrecognized catalog row at line {line_number}")
    ids = [row["pattern"] for row in patterns]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("catalog must contain unique pattern ids")
    return patterns


def search_patterns(
    patterns: list[dict[str, str]], query: str, *, offset: int, limit: int
) -> dict[str, object]:
    texts = ["\n".join(row.values()) for row in patterns]
    ranking = _scorer.score_bm25(texts, query)
    scored = []
    for row, text, hit in zip(patterns, texts, ranking.documents):
        score = hit.score
        matched_terms = list(hit.matched_terms)
        exact_id = row["pattern"].casefold() == query.strip().casefold()
        exact_code = any(query.strip().casefold() == code.casefold()
                         for code in re.findall(r"`([^`\n]+)`", text))
        if score > 0 or exact_id or not query:
            scored.append((exact_id, exact_code, score, row, matched_terms))
    # Stable id tie-break keeps pagination reproducible; scores are not confidence.
    if query:
        scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]["pattern"]))
    matched = [{"pattern": row["pattern"], "symptoms": row["symptoms"],
                "score": round(score, 6), "matched_terms": tokens,
                "exact_match": "pattern_id" if exact_id else "code" if exact_code else None}
               for exact_id, exact_code, score, row, tokens in scored]
    page = matched[offset:offset + limit]
    end = offset + len(page)
    return {
        "ok": True,
        "query": query,
        "match_mode": "bm25_exact_first" if query else "catalog_order",
        "unmatched_terms": list(ranking.unmatched_terms),
        "total_matches": len(matched),
        "offset": offset,
        "next_offset": end if end < len(matched) else None,
        "patterns": page,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query", help="BM25 lexical search; exact id/code first, no regex or embeddings.")
    mode.add_argument("--id", help="Return the complete guidance for one exact pattern id.")
    mode.add_argument("--list", action="store_true", help="Browse a page of ids and symptoms.")
    parser.add_argument("--limit", type=int, default=5, help="Search page size, 1–20 (default: 5).")
    parser.add_argument("--offset", type=int, default=0, help="Search offset from next_offset.")
    args = parser.parse_args()
    if not 1 <= args.limit <= 20 or args.offset < 0:
        parser.error("--limit must be 1–20 and --offset must be nonnegative")
    if args.query is not None and not args.query.strip():
        parser.error("--query must contain a term; use --list to browse")
    if args.query is not None and not re.search(r"[^\W_]+", args.query):
        parser.error("--query must contain a word or identifier")
    if args.id is not None and not args.id.strip():
        parser.error("--id must name a pattern")
    try:
        patterns = read_patterns(CATALOG)
        if args.id is not None:
            row = next((row for row in patterns if row["pattern"] == args.id), None)
            payload = {"ok": row is not None, "pattern": row}
            if row is None:
                payload["error"] = "unknown pattern id; search with --query or --list"
        else:
            payload = search_patterns(patterns, args.query or "", offset=args.offset, limit=args.limit)
    except (OSError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
