#!/usr/bin/env python3
"""Generate the RFC status index from each RFC's own status header.

The index is derived, never hand-edited: an RFC changes state by editing its
English header and its Chinese mirror, then running ``--write``;
``--check`` fails while ``STATUS.md`` / ``STATUS.zh-CN.md`` no longer match.
Merged active RFCs are Accepted and claimable; historical dispositions are
Superseded, Retired and Rejected.

The script also reports the header rules the index depends on: an unparseable
lifecycle value, a Chinese lifecycle that disagrees with the canonical header, a supersession
declaration that does not name a real RFC (or that its successor does not
mirror), and dated checkpoint headings still in an RFC body instead of
``ledger/<rfc-slug>/``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

REPO_ROOT = Path(__file__).resolve().parents[1]
RFC_DIR = REPO_ROOT / "docs" / "architecture" / "rfcs"
LEDGER_DIR = RFC_DIR / "ledger"
INDEX_EN = RFC_DIR / "STATUS.md"
INDEX_ZH = RFC_DIR / "STATUS.zh-CN.md"
NON_RFC_FILES = {"README.md", "TEMPLATE.md", "STATUS.md"}
HEADER_SCAN_LINES = 40


class LifecycleState(str, Enum):
    ACCEPTED = "Accepted"
    SUPERSEDED = "Superseded"
    RETIRED = "Retired"
    REJECTED = "Rejected"


LIFECYCLE_STATES = tuple(state.value for state in LifecycleState)
BUCKETS = (
    ("Accepted", ("Accepted",)),
    ("Superseded", ("Superseded",)),
    ("Retired", ("Retired", "Rejected")),
)
BUCKET_LABEL_ZH = {
    "Accepted": "已接受",
    "Superseded": "已被替代",
    "Retired": "已退役（Retired 或 Rejected）",
}
STATE_LABEL_ZH = {
    "Accepted": "已接受",
    "Superseded": "已被替代",
    "Retired": "已退役",
    "Rejected": "已拒绝",
}

_STATUS_LINE_PATTERNS = (
    re.compile(r"\*\*RFC 状态[:：]?\*\*[:：]?\s*(?P<value>.+?)\s*$"),
    re.compile(r"^\s*-\s*状态[:：]\s*(?P<value>.+?)\s*$"),
    re.compile(r"^\|\s*状态\s*\|\s*(?P<value>[^|]+?)\s*\|"),
    re.compile(r"\*\*RFC status[:：]?\*\*[:：]?\s*(?P<value>.+?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*-\s*Status[:：]\s*(?P<value>.+?)\s*$"),
    re.compile(r"^\|\s*Status\s*\|\s*(?P<value>[^|]+?)\s*\|", re.IGNORECASE),
)
# The lifecycle value is the exact state word, optionally followed by a
# descriptive tail the maintainer wrote: `Accepted (Stage A shipped in #4094)`,
# `Accepted, with remaining implementation milestones`. A
# prefix match alone would read `Acceptedness` as
# `Accepted`, so the word has to end at a delimiter instead of any other text.
_STATE_RE = re.compile(
    r"(?P<state>" + "|".join(re.escape(state.value) for state in LifecycleState) + r")"
    r"(?=$|[\s]*[(\[,;:：\-—（，；])",
    re.IGNORECASE,
)
_STATE_CANONICAL = {state.value.casefold(): state.value for state in LifecycleState}
# Accepted forms: `- **Supersedes / closes:** none`, `- Supersedes / closes: none`,
# `| Supersedes / closes | none |` (and the zh mirrors with `替代 / 关闭`).
SUPERSEDES_RE = re.compile(
    r"(?:\*\*Supersedes / closes:\*\*|^\s*-\s*Supersedes / closes:|^\|\s*Supersedes / closes\s*\|)"
    r"\s*(?P<value>[^|]+?)\s*\|?\s*$"
)
SUPERSEDES_ZH_RE = re.compile(
    r"(?:\*\*替代 / 关闭[:：]\*\*|^\s*-\s*替代 / 关闭[:：]|^\|\s*替代 / 关闭\s*\|)"
    r"\s*(?P<value>[^|]+?)\s*\|?\s*$"
)
SUPERSEDED_BY_RE = re.compile(
    r"(?:\*\*Superseded by:\*\*|^\s*-\s*Superseded by:|^\|\s*Superseded by\s*\|)"
    r"\s*(?P<value>[^|]+?)\s*\|?\s*$"
)
# Dated progress belongs in ledger/<rfc>/YYYY-MM-DD-slug.md, not in the body of
# an RFC. A heading is a dated log when the date leads it, or when it names a
# checkpoint and carries a date. Two classes of heading are legitimate and must
# keep passing: a normative one that happens to say "checkpoint"
# (`## Checkpoint persistence contract`), and dated history kept inside an
# appendix (`## Appendix A: Execution ledger (non-normative)`, zh
# `## 附录 A：执行台账（非规范性）`). So the guard only inspects the text above
# the first appendix heading.
DATED_LOG_HEADING_RE = re.compile(
    r"(?:^#{1,6}\s*(?:\*\*)?(?:19|20)\d{2}-\d{2}-\d{2}\b"
    r"|^#{1,6}\s.*(?:checkpoint|检查点).*(?:19|20)\d{2}-\d{2}-\d{2})",
    re.IGNORECASE | re.MULTILINE,
)
APPENDIX_HEADING_RE = re.compile(r"^##\s+(?:Appendix\b|附录)", re.MULTILINE)
INDEX_ENTRY_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]\((?P<file>[a-z0-9-]+\.md)\)", re.MULTILINE
)
INDEX_STATUS_RE = re.compile(r"\*\*RFC status:\*\*\s*(?P<value>.+?)\s*$")
RFC_REFERENCE_RE = re.compile(r"[a-z0-9][a-z0-9.-]*\.md")


@dataclass
class RfcRecord:
    path: Path
    title: str
    title_zh: str | None
    status_raw: str | None
    state: str | None
    supersedes: str | None
    supersedes_zh: str | None
    superseded_by: str | None
    ledger_entries: int
    checkpoint_headings: list[str] = field(default_factory=list)
    checkpoint_headings_zh: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.path.stem


def normalize_state(raw: str) -> str | None:
    """Return the canonical lifecycle state, or None when the value is untyped."""
    normalized = raw.strip().lstrip("*_` ").rstrip(".")
    for state, label in STATE_LABEL_ZH.items():
        if normalized == label or re.match(rf"^{re.escape(label)}[（，；]", normalized):
            return state
    match = _STATE_RE.match(normalized)
    if match is None:
        return None
    return _STATE_CANONICAL[match.group("state").lower()]


def declared_references(value: str | None) -> list[str]:
    """Return the RFC files a supersession declaration names, without repeats."""
    if value is None:
        return []
    return list(dict.fromkeys(RFC_REFERENCE_RE.findall(value)))


def declares_none(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().strip("`*_").rstrip(".").strip().lower() in {"none", "无"}


def parse_header(text: str) -> tuple[str | None, str | None, str | None]:
    """Return (status_raw, supersedes, superseded_by) from the header block."""
    status_raw = supersedes = superseded_by = None
    for line in text.splitlines()[:HEADER_SCAN_LINES]:
        if status_raw is None:
            for pattern in _STATUS_LINE_PATTERNS:
                match = pattern.search(line)
                if match:
                    status_raw = match.group("value").rstrip(".")
                    break
        if supersedes is None:
            match = SUPERSEDES_RE.search(line)
            if match:
                supersedes = match.group("value")
        if superseded_by is None:
            match = SUPERSEDED_BY_RE.search(line)
            if match:
                superseded_by = match.group("value")
    return status_raw, supersedes, superseded_by


def parse_zh_header(text: str) -> tuple[str | None, str | None]:
    title = None
    supersedes = None
    for line in text.splitlines()[:HEADER_SCAN_LINES]:
        if title is None and line.startswith("# "):
            title = line[2:].strip()
        match = SUPERSEDES_ZH_RE.search(line)
        if match and supersedes is None:
            supersedes = match.group("value")
    return title, supersedes


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "<untitled>"


def dated_checkpoint_headings(text: str) -> list[str]:
    appendix = APPENDIX_HEADING_RE.search(text)
    body = text[: appendix.start()] if appendix else text
    return [match.group(0).strip() for match in DATED_LOG_HEADING_RE.finditer(body)]


def rfc_files() -> list[Path]:
    return sorted(
        path
        for path in RFC_DIR.glob("*.md")
        if path.name not in NON_RFC_FILES and not path.name.endswith(".zh-CN.md")
    )


def collect() -> list[RfcRecord]:
    records = []
    for path in rfc_files():
        text = path.read_text(encoding="utf-8")
        status_raw, supersedes, superseded_by = parse_header(text)
        zh_path = path.with_name(f"{path.stem}.zh-CN.md")
        title_zh = supersedes_zh = None
        headings_zh: list[str] = []
        if zh_path.exists():
            zh_text = zh_path.read_text(encoding="utf-8")
            title_zh, supersedes_zh = parse_zh_header(zh_text)
            headings_zh = dated_checkpoint_headings(zh_text)
        ledger_scope = LEDGER_DIR / path.stem
        ledger_entries = (
            len(
                [
                    entry
                    for entry in ledger_scope.glob("*.md")
                    if not entry.name.endswith(".zh-CN.md")
                ]
            )
            if ledger_scope.is_dir()
            else 0
        )
        records.append(
            RfcRecord(
                path=path,
                title=first_heading(text),
                title_zh=title_zh,
                status_raw=status_raw,
                state=normalize_state(status_raw) if status_raw else None,
                supersedes=supersedes,
                supersedes_zh=supersedes_zh,
                superseded_by=superseded_by,
                ledger_entries=ledger_entries,
                checkpoint_headings=dated_checkpoint_headings(text),
                checkpoint_headings_zh=headings_zh,
            )
        )
    return records


def index_entry_states(readme_text: str) -> dict[str, str | None]:
    """Map each RFC file listed in README.md to the lifecycle state it states."""
    states: dict[str, str | None] = {}
    lines = readme_text.splitlines()
    for number, line in enumerate(lines):
        match = INDEX_ENTRY_RE.match(line)
        if not match:
            continue
        stated = None
        for follow in lines[number + 1 : number + 8]:
            status = INDEX_STATUS_RE.search(follow)
            if status:
                stated = normalize_state(status.group("value"))
                break
            if INDEX_ENTRY_RE.match(follow):
                break
        states[match.group("file")] = stated
    return states


def validate(records: list[RfcRecord]) -> list[str]:
    problems: list[str] = []
    readme_states = index_entry_states(
        (RFC_DIR / "README.md").read_text(encoding="utf-8")
    )
    by_name = {record.path.name: record for record in records}
    for record in records:
        name = record.path.name
        zh_path = record.path.with_name(f"{record.slug}.zh-CN.md")
        if zh_path.exists():
            zh_status, _, _ = parse_header(zh_path.read_text(encoding="utf-8"))
            if normalize_state(zh_status or "") != record.state:
                problems.append(
                    f"{name}: Chinese mirror lifecycle differs or is missing"
                )
        if record.status_raw is None:
            problems.append(
                f"{name}: no status header found in the first {HEADER_SCAN_LINES} lines"
            )
        elif record.state is None:
            problems.append(
                f"{name}: status {record.status_raw!r} does not begin with a lifecycle state "
                f"({', '.join(LIFECYCLE_STATES)}); a descriptive tail needs a delimiter like `,` or `;`"
            )
        if name not in readme_states:
            problems.append(f"{name}: not listed in README.md index")
        if record.supersedes is None:
            problems.append(
                f"{name}: missing `**Supersedes / closes:**` declaration (none | <RFC links>)"
            )
        if (
            record.supersedes is not None
            and record.title_zh is not None
            and record.supersedes_zh is None
        ):
            problems.append(
                f"{record.slug}.zh-CN.md: missing `**替代 / 关闭：**` mirror of the supersession line"
            )
        if (
            record.supersedes is not None
            and not declares_none(record.supersedes)
            and not declared_references(record.supersedes)
        ):
            problems.append(
                f"{name}: `Supersedes / closes` must be `none` or name the RFCs it replaces or closes, "
                f"got {record.supersedes!r}"
            )
        problems.extend(_supersession_problems(record, by_name))
        for heading in record.checkpoint_headings:
            problems.append(
                f"{name}: dated log heading belongs in ledger/{record.slug}/: {heading}"
            )
        for heading in record.checkpoint_headings_zh:
            problems.append(
                f"{record.slug}.zh-CN.md: dated log heading belongs in ledger/{record.slug}/: {heading}"
            )
    if re.search(
        r"^  - \*\*RFC status:\*\*",
        (RFC_DIR / "README.md").read_text(encoding="utf-8"),
        re.MULTILINE,
    ):
        problems.append("README.md must not cache lifecycle states; use STATUS.md")
    for listed in readme_states:
        if not (RFC_DIR / listed).exists():
            problems.append(f"README.md lists {listed}, which does not exist")
    return problems


def _supersession_problems(
    record: RfcRecord, by_name: dict[str, RfcRecord]
) -> list[str]:
    """A supersession declaration has to be true in both directions.

    `Supersedes / closes: <rfc>` claims the named RFC was replaced or closed, so
    the successor is only well-formed when that RFC exists and names this RFC in
    `Superseded by`. The mirror rule keeps a retired RFC from pointing at a
    successor that does not acknowledge it, and keeps `Superseded by: none` from
    passing as a closed supersession.
    """
    name = record.path.name
    problems: list[str] = []
    if record.state == "Superseded" and (
        record.superseded_by is None or declares_none(record.superseded_by)
    ):
        problems.append(
            f"{name}: Superseded RFCs must name `**Superseded by:**`; `none` is not a successor"
        )
    if record.superseded_by is not None and not declares_none(record.superseded_by):
        if record.state != "Superseded":
            problems.append(
                f"{name}: `Superseded by` is declared but the header state is "
                f"{record.state or 'missing'}, not Superseded"
            )
        for reference in declared_references(record.superseded_by):
            successor = by_name.get(reference)
            if successor is None:
                problems.append(
                    f"{name}: `Superseded by` names {reference}, which is not an RFC in this directory"
                )
            elif name not in declared_references(successor.supersedes):
                problems.append(
                    f"{name}: `Superseded by: {reference}` but {reference} does not name it in "
                    "`Supersedes / closes`"
                )
    if record.supersedes is not None and not declares_none(record.supersedes):
        for reference in declared_references(record.supersedes):
            predecessor = by_name.get(reference)
            if predecessor is None:
                problems.append(
                    f"{name}: `Supersedes / closes` names {reference}, which is not an RFC in this directory"
                )
            elif name not in declared_references(predecessor.superseded_by):
                problems.append(
                    f"{name}: `Supersedes / closes` names {reference}, but {reference} does not "
                    f"declare `Superseded by: {name}`"
                )
    return problems


def _ledger_cell(record: RfcRecord, *, zh: bool) -> str:
    if record.ledger_entries == 0:
        return "—"
    noun = "条" if zh else ("entry" if record.ledger_entries == 1 else "entries")
    return f"[{record.ledger_entries} {noun}](ledger/{record.slug}/)"


def render(records: list[RfcRecord], *, zh: bool) -> str:
    by_state: dict[str, list[RfcRecord]] = {state: [] for state in LIFECYCLE_STATES}
    for record in records:
        by_state.setdefault(record.state or "", []).append(record)
    lines: list[str] = []
    if zh:
        lines += [
            "# RFC 状态索引",
            "",
            "<!-- 由 scripts/generate_rfc_status_index.py 生成；不要手工编辑。 -->",
            "",
            "本索引从本目录每个 RFC 自己的状态头生成。改变一个 RFC 的状态只需要改它的头部，",
            "及其中文镜像的头部；README 不缓存生命周期状态。",
            "然后运行 `python3 scripts/generate_rfc_status_index.py --write`；"
            "`--check` 在索引过期时失败，`examples/docs-governance-smoke.py` 会调用它。",
            "",
            "合入的有效 RFC 即 **已接受**（Accepted），设计合格、可认领；",
            "**已被替代**（Superseded，必须写明 `Superseded by`）、**已退役**（Retired、Rejected）。",
            "合入不证明实现、真实资格验证或晋升完成；交付成熟度见 [README 索引](README.md)。",
            "新 RFC 必须在头部声明 `**替代 / 关闭：**`（`无` 或所替代 / 关闭的旧 RFC 链接）。",
            "带日期的交付记录写进 [ledger/](ledger/README.zh-CN.md)；附录里可以留历史，",
            "但附录之前的正文不允许再出现带日期的记录标题。",
            "",
            "[English](STATUS.md) 与本文互为语义镜像。",
        ]
    else:
        lines += [
            "# RFC Status Index",
            "",
            "<!-- generated by scripts/generate_rfc_status_index.py; do not edit by hand -->",
            "",
            "This index is derived from the status header of every RFC in this directory.",
            "Change an RFC's state by editing its header and the matching `**RFC status:**`",
            "in its Chinese mirror, then run",
            "`python3 scripts/generate_rfc_status_index.py --write`; `--check` fails while the",
            "index is stale and `examples/docs-governance-smoke.py` runs it.",
            "",
            "Merged active RFCs are **Accepted**: qualified design bases, available to claim.",
            "**Superseded** (must name `Superseded by`); **Retired** (Retired, Rejected).",
            "Merge does not prove implementation, live qualification or promotion; delivery lives in the",
            "[README index](README.md) `Delivery on main` lines. Every new RFC declares",
            "`**Supersedes / closes:**` in its header (`none` or links to the RFCs it",
            "replaces or closes). Dated delivery logs go to [ledger/](ledger/README.md); an",
            "appendix may keep dated history, but no dated log heading may precede it.",
            "",
            "[中文版](STATUS.zh-CN.md) is the semantic mirror of this file.",
        ]
    for bucket, states in BUCKETS:
        members = sorted(
            (record for state in states for record in by_state.get(state, [])),
            key=lambda record: record.slug,
        )
        label = BUCKET_LABEL_ZH[bucket] if zh else bucket
        lines += ["", f"## {label} ({len(members)})", ""]
        if not members:
            lines.append("_无_" if zh else "_none_")
            continue
        if zh:
            lines += [
                "| RFC | 头部状态 | 替代 / 关闭 | Ledger |",
                "| --- | --- | --- | --- |",
            ]
        else:
            lines += [
                "| RFC | Header status | Supersedes / closes | Ledger |",
                "| --- | --- | --- | --- |",
            ]
        for record in members:
            if zh:
                target = (
                    f"{record.slug}.zh-CN.md" if record.title_zh else record.path.name
                )
                title = record.title_zh or record.title
                status = STATE_LABEL_ZH.get(record.state or "", record.state or "?")
                supersedes = record.supersedes_zh or (
                    "—" if record.supersedes is None else record.supersedes
                )
            else:
                target = record.path.name
                title = record.title
                status = record.state or "?"
                supersedes = record.supersedes or "—"
            if record.superseded_by:
                supersedes = f"{supersedes}; superseded by {record.superseded_by}"
            title = title.replace("|", "\\|")
            lines.append(
                f"| [{title}]({target}) | {status} | {supersedes} | {_ledger_cell(record, zh=zh)} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write", action="store_true", help="Rewrite STATUS.md and STATUS.zh-CN.md."
    )
    mode.add_argument(
        "--check", action="store_true", help="Fail when the checked-in index is stale."
    )
    parser.add_argument(
        "--report", action="store_true", help="Print header problems without failing."
    )
    args = parser.parse_args(argv)

    records = collect()
    problems = validate(records)
    if problems and args.write:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    rendered_en = render(records, zh=False)
    rendered_zh = render(records, zh=True)

    if args.report or not (args.write or args.check):
        for problem in problems:
            print(problem)
        print(f"{len(records)} RFCs; {len(problems)} problems")
        if not (args.write or args.check):
            return 0
    if problems and not args.report:
        for problem in problems:
            print(problem, file=sys.stderr)
    if args.write:
        INDEX_EN.write_text(rendered_en, encoding="utf-8")
        INDEX_ZH.write_text(rendered_zh, encoding="utf-8")
        print(
            f"wrote {INDEX_EN.relative_to(REPO_ROOT)} and {INDEX_ZH.relative_to(REPO_ROOT)}"
        )
        return 1 if problems else 0
    stale = []
    for path, rendered in ((INDEX_EN, rendered_en), (INDEX_ZH, rendered_zh)):
        if not path.exists() or path.read_text(encoding="utf-8") != rendered:
            stale.append(path.relative_to(REPO_ROOT))
    if stale:
        print(
            "stale RFC status index: "
            + ", ".join(map(str, stale))
            + "; run python3 scripts/generate_rfc_status_index.py --write",
            file=sys.stderr,
        )
    return 1 if (stale or problems) else 0


if __name__ == "__main__":
    raise SystemExit(main())
