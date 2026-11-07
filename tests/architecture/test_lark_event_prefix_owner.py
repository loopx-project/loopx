"""The Lark CLI event-line prefixes have one stating module.

``event_collector_runtime`` already read the provider's stdout lines by their
prefix. ``goal_topic_runtime`` and ``team_plan_confirmation`` compiled the same
two bodies locally, so a change to the wire format had three sites to find.

The bodies are stated once, in the owner. A consumer that holds the owner's
object is fine; a consumer that states the body again fails the census, whether
it names it ``_EVENT_DIAGNOSTIC_PREFIX`` or anything else.
"""

from __future__ import annotations

import pathlib

from loopx.extensions.lark import (
    event_collector_runtime,
    goal_topic_runtime,
    team_plan_confirmation,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER_PATH = "loopx/extensions/lark/event_collector_runtime.py"
STATED_LITERALS = ('"[event] ready "', '"[event] "')


def _module_sources() -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "loopx").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        sources[path.relative_to(REPO_ROOT).as_posix()] = path.read_text(
            encoding="utf-8"
        )
    return sources


def test_only_the_owner_states_each_event_line_prefix() -> None:
    sources = _module_sources()
    stated = {
        literal: sorted(rel for rel, text in sources.items() if literal in text)
        for literal in STATED_LITERALS
    }
    assert stated == {literal: [OWNER_PATH] for literal in STATED_LITERALS}


def test_every_consumer_holds_the_owners_object() -> None:
    assert (
        goal_topic_runtime.EVENT_READY_PREFIX
        is event_collector_runtime.EVENT_READY_PREFIX
    )
    assert (
        goal_topic_runtime.EVENT_DIAGNOSTIC_PREFIX
        is event_collector_runtime.EVENT_DIAGNOSTIC_PREFIX
    )
    assert (
        team_plan_confirmation.EVENT_DIAGNOSTIC_PREFIX
        is event_collector_runtime.EVENT_DIAGNOSTIC_PREFIX
    )
