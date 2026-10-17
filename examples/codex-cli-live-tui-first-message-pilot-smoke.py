#!/usr/bin/env python3
"""Validate the Codex CLI live TUI first-message pilot record."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "product" / "runtimes" / "codex-cli" / "codex-cli-live-tui-first-message-pilot.md"
FIRST_RUN = REPO_ROOT / "docs" / "product" / "runtimes" / "codex-cli" / "codex-cli-first-run-rehearsal.md"
PRODUCT_README = (
    REPO_ROOT / "docs" / "product" / "runtimes" / "codex-cli" / "README.md"
)
GOAL_ID = "public-live-tui-pilot-goal"
AGENT_ID = "codex-side-bypass"


def normalize(text: str) -> str:
    return " ".join(text.split())


def run_cli(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def assert_doc() -> None:
    text = DOC.read_text(encoding="utf-8")
    normalized = normalize(text)

    must_have = (
        "Codex CLI Live TUI First-Message Pilot",
        "Status: blocker recorded; manual TUI bootstrap remains primary.",
        "Recorded: 2026-06-21.",
        "disposable public-safe repo",
        "codex [OPTIONS] [PROMPT]",
        "--no-alt-screen",
        "--cd <DIR>",
        "resume",
        "remote-control",
        "codex doctor",
        "did not produce bounded output before manual interrupt",
        "no bounded first-response or completion signal",
        "capture output exceeded the automation budget",
        "process remained active",
        "process command line",
        "live_tui_first_message_blocked_by_bounded_visible_completion_missing",
        "manual TUI bootstrap remains primary",
        "the user pastes one LoopX start message",
        "without leaking project-specific prompt text through process arguments",
        "raw transcript or session-file reads",
        "Codex CLI bounded visible pilot adapter",
    )
    for phrase in must_have:
        assert phrase in normalized, phrase

    assert "LoopX should not advertise automated `codex [PROMPT]` launch" in text
    assert "raw TUI output, Codex transcripts, session files" in normalized, text
    assert "without raw transcript or session-file reads" in normalized, text


def assert_indexes() -> None:
    product = PRODUCT_README.read_text(encoding="utf-8")
    first_run = FIRST_RUN.read_text(encoding="utf-8")
    assert "codex-cli-live-tui-first-message-pilot.md" in product, product
    assert "Codex CLI live TUI first-message pilot" in product, product
    assert "Live TUI Pilot Note" in first_run, first_run
    assert (
        "Automated live launch needs a bounded visible completion proof first." in first_run
    ), first_run


def assert_bootstrap_message_still_copy_first() -> None:
    message = run_cli(
        "codex-cli-bootstrap-message",
        "--project",
        "/tmp/loopx-live-tui-pilot.public",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--message-only",
    )
    normalized = normalize(message)
    assert message.startswith("Install and connect LoopX for this repo"), message
    assert not message.startswith("/goal "), message
    assert "Codex CLI TUI" in normalized, message
    assert "setup/bootstrap instruction" in normalized, message
    assert "/goal <loader task_body>" in normalized, message
    assert "raw Codex transcripts" in normalized, message
    assert "watch, steer, review, and take over" in normalized, message


def main() -> int:
    assert_doc()
    assert_indexes()
    assert_bootstrap_message_still_copy_first()
    print("codex-cli-live-tui-first-message-pilot-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
