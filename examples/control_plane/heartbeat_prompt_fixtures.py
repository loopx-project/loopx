"""Shared fixtures for heartbeat prompt contract smokes."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.heartbeat_prompt import (  # noqa: E402
    INTERFACE_BUDGET_CHARS,
    REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS,
)


DOC = REPO_ROOT / "docs" / "heartbeat-automation-prompt.md"
README = REPO_ROOT / "README.md"
GETTING_STARTED = REPO_ROOT / "docs" / "guides" / "getting-started.md"
INTEGRATION_DOC = REPO_ROOT / "docs" / "integration.md"
PROJECT_SKILL = REPO_ROOT / "skills" / "loopx-project" / "SKILL.md"
GOAL_ID = "public-heartbeat-goal"
ACTIVE_STATE = Path("/tmp/public-heartbeat-goal/ACTIVE_GOAL_STATE.md")
PROJECT_SPECIFIC_PROMPT_LEAKS = (
    "internal-side-agent",
    "internal-main-control",
    "private-runtime-delta",
    "managed private mirrors",
    "docs/TODO.md",
    "internal-product-gamma",
    "internal-team-beta",
)


def normalized(text: str) -> str:
    return " ".join(text.split())


def prompt_budget_text(text: str) -> str:
    # Normalize the complete path before its Goal substring, matching the
    # independently declared authored-guidance measurement contract.
    return text.replace(str(ACTIVE_STATE), "<ACTIVE_STATE>").replace(GOAL_ID, "<GOAL_ID>")


def assert_prompt_budget(label: str, text: str) -> None:
    budget_text = prompt_budget_text(text)
    max_chars = INTERFACE_BUDGET_CHARS[label] + (
        REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS
        if "--reward-memory-reflection-json" in text
        else 0
    )
    assert len(budget_text) <= max_chars, (
        label,
        len(budget_text),
        max_chars,
    )


def assert_interface_budget_payload(label: str, payload: dict) -> None:
    task_body = str(payload["task_body"])
    budget = payload.get("interface_budget")
    assert isinstance(budget, dict), (label, payload)
    assert budget["mode"] == label, budget
    assert budget["char_count"] == len(task_body), budget
    assert budget["line_count"] == len(task_body.splitlines()), budget
    assert budget["budget_char_count"] == len(prompt_budget_text(task_body)), budget
    expected_max_chars = INTERFACE_BUDGET_CHARS[label] + (
        REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS
        if "--reward-memory-reflection-json" in task_body
        else 0
    )
    assert budget["max_chars"] == expected_max_chars, budget
    assert budget["within_budget"] is True, budget


def assert_no_project_specific_prompt_leaks(label: str, text: str) -> None:
    for phrase in PROJECT_SPECIFIC_PROMPT_LEAKS:
        assert phrase not in text, (label, phrase)


def assert_ordered(text: str, phrases: tuple[str, ...]) -> None:
    compact = normalized(text)
    positions = []
    for phrase in phrases:
        assert phrase in compact, phrase
        positions.append(compact.index(phrase))
    assert positions == sorted(positions), positions
