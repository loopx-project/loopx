#!/usr/bin/env python3
"""Generate the next public-safe biweekly LoopX update note.

This script is intentionally deterministic and source-limited: it reads the
public repository history plus docs/update-notes/README.md, writes a draft note,
and updates the archive pointers. It does not read LoopX runtime state, chat
history, local private files, raw benchmark traces, or credentials.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTES_DIR = ROOT / "docs" / "update-notes"
NOTES_INDEX = NOTES_DIR / "README.md"
WINDOW_RE = re.compile(r"(?P<since>\d{4}-\d{2}-\d{2})-to-(?P<until>\d{4}-\d{2}-\d{2})\.md$")
SQUASH_PR_RE = re.compile(r"^(?P<title>.+?)\s+\(#(?P<pr>\d+)\)$")
MERGE_PR_RE = re.compile(r"^Merge pull request #(?P<pr>\d+)\b")
FIELD_SEPARATOR = "\x1f"
RECORD_SEPARATOR = "\x1e"
PULL_URL = "https://github.com/loopx-project/loopx/pull"


@dataclass(frozen=True)
class Window:
    since: date
    until: date

    @property
    def label(self) -> str:
        return f"{self.since.isoformat()} to {self.until.isoformat()}"

    @property
    def filename(self) -> str:
        return f"{self.since.isoformat()}-to-{self.until.isoformat()}.md"

    @property
    def slug(self) -> str:
        return f"{self.since.isoformat()}-to-{self.until.isoformat()}"

    @property
    def open_after(self) -> date:
        return self.until + timedelta(days=1)

    @property
    def next_window(self) -> "Window":
        next_since = self.until + timedelta(days=1)
        return Window(next_since, next_since + timedelta(days=13))


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    pr_number: int


THEMES: list[tuple[str, tuple[str, ...], str]] = [
    (
        "Issue-fix workflow",
        ("issue-fix", "issue fix", "github issue", "review packet"),
        "Issue-fix and PR review flows became more repeatable.",
    ),
    (
        "Benchmark workflow",
        ("skillsbench", "terminal-bench", "benchmark", "harbor", "verifier"),
        "Benchmark and runner contracts became easier to validate and explain.",
    ),
    (
        "Control plane reliability",
        ("quota", "scheduler", "gate", "todo", "task graph", "refresh-state"),
        "Quota, gates, todos, and scheduler behavior became more explicit.",
    ),
    (
        "Host and slash commands",
        ("slash", "host", "codex", "/loopx", "command", "launch"),
        "Host entry points and command contracts became clearer.",
    ),
    (
        "Evented state and read paths",
        ("event", "read path", "projection", "history", "cold-path"),
        "State projection and history read paths moved toward durable contracts.",
    ),
    (
        "Docs and public surface",
        ("docs", "readme", "showcase", "frontstage", "update note", "catalog"),
        "Public docs, showcases, and release communication improved.",
    ),
]


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def note_windows() -> list[Window]:
    windows: list[Window] = []
    for path in NOTES_DIR.glob("*.md"):
        match = WINDOW_RE.match(path.name)
        if not match:
            continue
        windows.append(Window(parse_date(match.group("since")), parse_date(match.group("until"))))
    return sorted(windows, key=lambda item: item.since)


def infer_next_window() -> Window:
    windows = note_windows()
    if not windows:
        anchor = date(2026, 5, 31)
        return Window(anchor, anchor + timedelta(days=13))
    return windows[-1].next_window


def collect_commits(window: Window) -> list[Commit]:
    output = run_git(
        [
            "log",
            "HEAD",
            "--first-parent",
            f"--since={window.since.isoformat()}T00:00:00Z",
            f"--until={window.until.isoformat()}T23:59:59Z",
            "--pretty=format:%h%x1f%s%x1f%b%x1e",
        ]
    )
    commits: list[Commit] = []
    seen_prs: set[int] = set()
    for record in output.split(RECORD_SEPARATOR):
        record = record.strip("\r\n")
        if not record:
            continue
        fields = record.split(FIELD_SEPARATOR, 2)
        if len(fields) != 3:
            continue
        sha, subject, body = fields
        subject = subject.strip()
        squash_match = SQUASH_PR_RE.match(subject)
        merge_match = MERGE_PR_RE.match(subject)
        if squash_match:
            pr_number = int(squash_match.group("pr"))
            subject = squash_match.group("title").strip()
        elif merge_match:
            pr_number = int(merge_match.group("pr"))
            body_lines = [line.strip() for line in body.splitlines() if line.strip()]
            if not body_lines:
                continue
            subject = body_lines[0]
            nested_match = SQUASH_PR_RE.match(subject)
            if nested_match:
                subject = nested_match.group("title").strip()
        else:
            # Direct and branch-merge commits are useful in git history but are
            # noisy evidence for a public update-note summary.
            continue
        if pr_number in seen_prs:
            continue
        seen_prs.add(pr_number)
        commits.append(Commit(sha=sha, subject=subject, pr_number=pr_number))
    return commits


def commit_rank(commit: Commit) -> tuple[int, int]:
    lowered = commit.subject.lower()
    rank_groups = [
        (40, ("feat", "add", "introduce", "support", "enable", "ship", "release", "expose")),
        (30, ("fix", "harden", "guard", "preserve", "stabilize", "repair", "prevent")),
        (20, ("refactor", "move", "extract", "split", "retire", "simplify")),
        (10, ("docs", "test", "ci", "chore", "bump", "refresh")),
    ]
    for score, signals in rank_groups:
        if any(
            re.search(rf"(?<![a-z0-9-]){re.escape(signal)}(?![a-z0-9-])", lowered)
            for signal in signals
        ):
            return score, commit.pr_number
    return 15, commit.pr_number


def classify_commits(commits: list[Commit]) -> dict[str, list[Commit]]:
    grouped: dict[str, list[Commit]] = {theme: [] for theme, _, _ in THEMES}
    grouped["Other public changes"] = []
    for commit in commits:
        lowered = commit.subject.lower()
        for theme, needles, _summary in THEMES:
            if any(needle in lowered for needle in needles):
                grouped[theme].append(commit)
                break
        else:
            grouped["Other public changes"].append(commit)
    return {
        theme: sorted(values, key=commit_rank, reverse=True)
        for theme, values in grouped.items()
        if values
    }


def bulletize(commits: list[Commit], limit: int = 4) -> list[str]:
    bullets = [
        f"- [#{commit.pr_number}]({PULL_URL}/{commit.pr_number}) {commit.subject}"
        for commit in commits[:limit]
    ]
    remaining = len(commits) - limit
    if remaining > 0:
        noun = "PR" if remaining == 1 else "PRs"
        bullets.append(f"- ...and {remaining} more merged {noun} in this theme.")
    return bullets


def focus_for(grouped: dict[str, list[Commit]]) -> str:
    themes = [theme for theme in grouped if theme != "Other public changes"]
    if not themes:
        return "Public repository maintenance and documentation."
    return ", ".join(themes[:4]) + "."


def render_note(window: Window, commits: list[Commit]) -> str:
    grouped = classify_commits(commits)
    product_groups = {
        theme: values for theme, values in grouped.items() if theme != "Other public changes"
    }
    highlight_lines = []
    for theme, _needles, summary in THEMES:
        if theme in product_groups:
            highlight_lines.append(f"- {summary}")
    if not highlight_lines:
        highlight_lines.append("- No merged PR evidence was found for this window; review the draft before publishing.")

    sections: list[str] = []
    for theme, values in product_groups.items():
        sections.append(f"### {theme}\n\n" + "\n".join(bulletize(values)))
    if not sections:
        sections.append("### Public change review\n\n- No merged PR evidence was collected for this window.")
    matched_count = sum(len(values) for values in product_groups.values())

    return "\n".join(
        [
            f"# Biweekly Update Note: {window.label}",
            "",
            "## Source Boundary",
            "",
            f"This note summarizes public repository history from {window.since.isoformat()} "
            f"through {window.until.isoformat()}. It uses public commit history, shipped docs, "
            "examples, and smoke tests. It does not include private operator state, raw "
            "benchmark evidence, private links, local paths, or credentials.",
            "",
            "## Highlights",
            "",
            "\n".join(highlight_lines),
            "",
            "## What Shipped",
            "",
            f"The generator reviewed {len(commits)} merged PRs and selected the highest-ranked "
            f"evidence from {matched_count} PRs across stable product themes. Unclassified "
            "maintenance remains available in git history instead of being copied into this draft.",
            "",
            "\n\n".join(sections),
            "",
            "## Validation And Public Boundary",
            "",
            "This source draft is generated by `scripts/update_notes_release_job.py` from "
            "public git history. The generator does not use an LLM; treat the output as a "
            "factual packet for maintainer or LoopX-agent editing before publication. Before "
            "merging the generated PR, run the update-note smoke, `git diff --check`, and "
            "LoopX public boundary scan over the touched files.",
            "",
            "## Next Window",
            "",
            f"The next expected window is {window.next_window.label}.",
            "",
        ]
    )


def section_bounds(index_text: str, heading: str) -> tuple[int, int]:
    marker = f"{heading}\n"
    start = index_text.find(marker)
    if start < 0:
        raise SystemExit(f"docs/update-notes/README.md missing {heading} section")
    content_start = start + len(marker)
    next_heading = re.search(r"^## ", index_text[content_start:], re.MULTILINE)
    end = content_start + next_heading.start() if next_heading else len(index_text)
    return content_start, end


def replace_latest(index_text: str, window: Window) -> str:
    latest_line = f"- [{window.label}]({window.filename})"
    start, end = section_bounds(index_text, "## Latest")
    section = index_text[start:end]
    pattern = re.compile(r"^- \[[^\n]+\]\([^)]+\)$", re.MULTILINE)
    if pattern.search(section):
        section = pattern.sub(latest_line, section, count=1)
    else:
        section = f"\n{latest_line}\n" + section.lstrip("\n")
    return index_text[:start] + section + index_text[end:]


def update_archive(index_text: str, window: Window, focus: str) -> str:
    row = f"| {window.label} | [Read note]({window.filename}) | {focus} |"
    start, end = section_bounds(index_text, "## Archive")
    section = index_text[start:end]
    if row in section or window.filename in section:
        return index_text
    marker = "| Window | Note | Focus |\n| --- | --- | --- |\n"
    if marker not in section:
        raise SystemExit("docs/update-notes/README.md archive table marker not found")
    section = section.replace(marker, marker + row + "\n", 1)
    return index_text[:start] + section + index_text[end:]


def update_next_expected(index_text: str, window: Window) -> str:
    next_label = window.next_window.label
    pattern = re.compile(r"Next expected window: \d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}\.")
    replacement = f"Next expected window: {next_label}."
    if pattern.search(index_text):
        return pattern.sub(replacement, index_text)
    return index_text


def write_github_output(values: dict[str, str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="Window start date, YYYY-MM-DD.")
    parser.add_argument("--until", help="Window end date, YYYY-MM-DD.")
    parser.add_argument("--today", help="Current UTC date for due checks, YYYY-MM-DD.")
    parser.add_argument("--force", action="store_true", help="Write even before the inferred open-after date.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing note file.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing files.")
    args = parser.parse_args()

    if bool(args.since) != bool(args.until):
        raise SystemExit("--since and --until must be provided together")

    window = (
        Window(parse_date(args.since), parse_date(args.until))
        if args.since and args.until
        else infer_next_window()
    )
    today = parse_date(args.today) if args.today else datetime.now(timezone.utc).date()
    note_path = NOTES_DIR / window.filename

    if today < window.open_after and not args.force:
        print(f"update notes: window {window.label} is not due until {window.open_after}")
        write_github_output({"changed": "false", "window": window.label, "window_slug": window.slug})
        return

    if note_path.exists() and not args.overwrite:
        print(f"update notes: {note_path.relative_to(ROOT)} already exists")
        write_github_output({"changed": "false", "window": window.label, "window_slug": window.slug})
        return

    commits = collect_commits(window)
    grouped = classify_commits(commits)
    note = render_note(window, commits)
    focus = focus_for(grouped)
    index_text = NOTES_INDEX.read_text(encoding="utf-8")
    index_text = replace_latest(index_text, window)
    index_text = update_archive(index_text, window, focus)
    index_text = update_next_expected(index_text, window)

    print(
        f"update notes: window={window.label} merged_prs={len(commits)} "
        f"file={note_path.relative_to(ROOT)}"
    )
    if args.dry_run:
        print(note)
        write_github_output({"changed": "false", "window": window.label, "window_slug": window.slug})
        return

    note_path.write_text(note, encoding="utf-8")
    NOTES_INDEX.write_text(index_text, encoding="utf-8")
    write_github_output(
        {
            "changed": "true",
            "window": window.label,
            "window_slug": window.slug,
            "note_file": str(note_path.relative_to(ROOT)),
        }
    )


if __name__ == "__main__":
    main()
