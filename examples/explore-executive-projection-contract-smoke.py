#!/usr/bin/env python3
"""Guard owner-facing Explore views against default cardinality caps."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATHS = (
    ROOT / "skills" / "loopx-project" / "references" / "materials-explore-quality.md",
    ROOT / "loopx" / "capabilities" / "explore" / "README.md",
)

REQUIRED_FRAGMENTS = (
    "default cardinality policy is graph growth",
    "material decision and evidence nodes",
    "semantic sections or linked subgraphs",
    "20 nodes",
    "explicit opt-in presentation policy",
    "overlap and text-overflow checks",
)

FORBIDDEN_DEFAULTS = (
    "display contract with node/edge budgets",
    "give the projection explicit node and edge budgets",
)


def main() -> int:
    skill = (ROOT / "skills/loopx-project/SKILL.md").read_text(encoding="utf-8")
    assert "references/materials-explore-quality.md" in skill
    assert "Read the matching procedure" in skill
    for path in CONTRACT_PATHS:
        text = path.read_text(encoding="utf-8")
        normalized = " ".join(text.lower().split())
        for fragment in REQUIRED_FRAGMENTS:
            assert fragment in normalized, f"{path}: missing {fragment!r}"
        for fragment in FORBIDDEN_DEFAULTS:
            assert fragment not in normalized, f"{path}: retained hard-cap default {fragment!r}"
        assert "`max_nodes`" in text and "`max_edges`" in text, (
            f"{path}: explicit hard-cap field contract missing"
        )

    print("explore-executive-projection-contract-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
