#!/usr/bin/env python3
"""Validate the public core-control-plane diagram docs stay linked and safe."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = ROOT / "docs" / "product" / "core-control-plane"

FILES = [
    DOC_DIR / "README.md",
    DOC_DIR / "interaction-catalog.md",
    DOC_DIR / "state-definitions.md",
    DOC_DIR / "state-machine.md",
]

FORBIDDEN_PUBLIC_STRINGS = [
    "bytedance",
    "la" + "rk" + "office",
    "EAAt" + "dvU1bokXbyxXdv7cDAAbnSb",
    "/Users/",
    "/private/tmp",
    "127.0.0.1",
    "localhost",
    "appSecret",
    "accessToken",
]


def read(path: Path) -> str:
    assert path.exists(), f"missing doc: {path}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    docs = {path.name: read(path) for path in FILES}
    combined = "\n".join(docs.values())

    for text in FORBIDDEN_PUBLIC_STRINGS:
        assert text.lower() not in combined.lower(), f"private marker leaked: {text}"

    readme = docs["README.md"]
    for filename in ("interaction-catalog.md", "state-definitions.md", "state-machine.md"):
        assert f"]({filename})" in readme, f"README does not link {filename}"

    assert "interaction_pattern_lens_v0" in docs["interaction-catalog.md"]
    assert "Core Pattern Map" in docs["interaction-catalog.md"]
    assert "State Definitions" in docs["state-definitions.md"]
    assert "Canonical State Bodies" in docs["state-definitions.md"]
    assert "Runtime Decisions, Phases, And Read Conditions" in docs["state-definitions.md"]
    assert "State Machines" in docs["state-machine.md"]
    for section in (
        "Todo Lifecycle Machine",
        "Quota / Runtime Machine",
        "Gate Decision Scope Machine",
        "Owner Route / Multi-Agent Handoff Machine",
        "Evidence / Rollout / Rollback Machine",
        "Scheduler / Heartbeat Machine",
        "Projection Sink Machine",
        "Agent Onboarding / Automation Enablement Machine",
        "Agent Vision / Replan Machine",
    ):
        assert section in docs["state-machine.md"], f"missing state machine: {section}"
    assert "```mermaid" in combined


if __name__ == "__main__":
    main()
