"""Check instruction delivery, not whether an agent obeys the delivered prose."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTRUCTIONS = ROOT / "AGENTS.md"
# The default Codex project-doc budget is 32 KiB across discovered files, while
# Antigravity caps individual rule files (AGENTS.md) at 24,000 bytes (24 KB).
# This root must fit within both host limits by itself; host overrides and
# ancestor files need their own delivery checks.
DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024
ANTIGRAVITY_RULE_FILE_MAX_BYTES = 24_000


def _heading_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
        if in_fence:
            continue
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = re.sub(r"[^\w\- ]", "", match[1].lower()).replace(" ", "-")
        suffix = counts.get(heading, 0)
        counts[heading] = suffix + 1
        anchors.add(f"{heading}-{suffix}" if suffix else heading)
    return anchors


def _local_links(path: Path) -> list[tuple[Path, str]]:
    links = []
    for target in re.findall(r"\]\(([^\s)]+)\)", path.read_text(encoding="utf-8")):
        parts = urlsplit(target)
        if parts.scheme or parts.netloc or parts.path.startswith("/"):
            continue
        resolved = (path.parent / unquote(parts.path)).resolve() if parts.path else path
        links.append((resolved, unquote(parts.fragment)))
    return links


def test_root_instructions_fit_default_project_doc_loading() -> None:
    contents = INSTRUCTIONS.read_bytes()
    for host_name, limit in (
        ("Antigravity per-file rule cap", ANTIGRAVITY_RULE_FILE_MAX_BYTES),
        ("Codex default project_doc_max_bytes", DEFAULT_PROJECT_DOC_MAX_BYTES),
    ):
        loaded = contents[:limit]
        assert loaded == contents, (
            f"Root instructions lose {len(contents) - len(loaded)} bytes at the "
            f"{host_name} ({limit} bytes) boundary; route applicable detail to its existing owner."
        )


def test_root_instruction_routes_resolve_to_existing_files_and_sections() -> None:
    links = _local_links(INSTRUCTIONS)
    assert links, "Root instructions need reachable task-specific procedures."
    for target, anchor in links:
        assert target.is_file(), f"Missing instruction route: {target.relative_to(ROOT)}"
        if anchor:
            assert anchor in _heading_anchors(target), (
                f"Missing instruction section: {target.relative_to(ROOT)}#{anchor}"
            )


@pytest.mark.parametrize(
    "consumer",
    [
        "loopx/capabilities/pr_review_queue/README.md",
        "docs/development/design.md",
        "docs/development/frontend-delivery.md",
    ],
)
def test_existing_consumers_can_follow_root_instruction_anchors(consumer: str) -> None:
    anchors = [anchor for target, anchor in _local_links(ROOT / consumer) if target == INSTRUCTIONS]
    assert anchors, f"{consumer} must retain its repository-policy route."
    for anchor in anchors:
        assert anchor in _heading_anchors(INSTRUCTIONS), f"Broken root-policy anchor: {anchor}"


def test_claude_imports_one_shared_policy_without_eager_detail_imports() -> None:
    assert (ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
    assert not re.search(r"^\s*@\S+", INSTRUCTIONS.read_text(encoding="utf-8"), re.MULTILINE)
