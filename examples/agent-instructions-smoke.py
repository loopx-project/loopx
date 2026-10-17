#!/usr/bin/env python3
"""Check the shared host entry point, loading budget and workflow links."""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^\s)]+)\)")


def anchors(text: str) -> set[str]:
    headings = re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
    return {re.sub(r"[^\w -]", "", h.lower()).replace(" ", "-") for h in headings}


def check(root: Path) -> None:
    entry = root / "AGENTS.md"
    raw = entry.read_bytes()
    assert len(raw) < 12 * 1024, "AGENTS.md exceeds the repository loading budget"
    text = raw.decode("utf-8")
    assert len(text.splitlines()) < 200, "Move task details out of the root entry point"
    assert (root / "CLAUDE.md").read_text().strip() == "@AGENTS.md", "Claude must import the shared policy exactly once"
    assert not re.search(r"^\s*@\S+", text, re.MULTILINE), "Root instructions must not eagerly import details"

    pending = [entry]
    visited: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in visited:
            continue
        visited.add(source)
        for target in LINK.findall(source.read_text()):
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            path = (source.parent / unquote(url.path)).resolve() if url.path else source
            assert path.exists(), f"Missing instruction target: {source.name}: {target}"
            if path.suffix == ".md" and url.fragment:
                assert unquote(url.fragment) in anchors(path.read_text()), f"Missing instruction anchor: {target}"
            # Follow the task-detail documents, not their entire reference graph.
            if path.parent == root / "docs/development" and path.name.startswith("agent-"):
                pending.append(path)


def must_reject(root: Path) -> None:
    try:
        check(root)
    except AssertionError:
        return
    raise AssertionError("Invalid instruction fixture was accepted")


def main() -> None:
    check(ROOT)
    with tempfile.TemporaryDirectory(prefix="agent-instructions-") as directory:
        root = Path(directory)
        entry = root / "AGENTS.md"
        alias = root / "CLAUDE.md"
        alias.write_text("@AGENTS.md\n")
        entry.write_text("# Rules\n\n[Workflow](workflow.md#safe-action)\n")
        workflow = root / "workflow.md"
        workflow.write_text("# Safe Action\n")
        check(root)
        workflow.unlink()
        must_reject(root)
        workflow.write_text("# Wrong Action\n")
        must_reject(root)
        entry.write_text("x" * (12 * 1024))
        must_reject(root)
        entry.write_text("# Rules\n" * 200)
        must_reject(root)
        entry.write_text("# Rules\n")
        alias.write_text("Read AGENTS.md when needed.\n")
        must_reject(root)
    print("agent-instructions-smoke: ok (budget, shared import, links and negative cases)")


if __name__ == "__main__":
    main()
