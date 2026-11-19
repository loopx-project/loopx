"""Keep the manual-visibility decision for every top-level CLI command on PRs.

The release preflight smoke (examples/cli-help-manpage-smoke.py) enforces the
same rule across the installed manual, but it only runs in the daily
full-public fleet. A command or alias added without a catalog decision
therefore stayed invisible until that fleet turned red, so the pure rule and
the checked-in rendered manual are guarded here as well.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from loopx.cli import build_parser
from loopx.help_surface import (
    MANPAGE_COMMAND_HELP_ONLY,
    manpage_top_level_commands,
    render_manpage,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MANPAGE = REPO_ROOT / "man" / "loopx.1"


def _top_level_commands() -> frozenset[str]:
    subparsers = next(
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return frozenset(subparsers.choices)


def test_every_top_level_command_declares_manual_visibility() -> None:
    commands = _top_level_commands()
    documented = manpage_top_level_commands()
    help_only = MANPAGE_COMMAND_HELP_ONLY
    assert documented.isdisjoint(help_only)
    assert commands == documented | help_only, {
        # Classify each reported command in loopx/help_surface.py: add it to
        # COMMAND_GROUPS when the manual should teach it, or to
        # MANPAGE_COMMAND_HELP_ONLY when its command help owns the guidance.
        "unclassified": sorted(commands - documented - help_only),
        "stale_manual": sorted(documented - commands),
        "stale_help_only": sorted(help_only - commands),
    }


def test_checked_in_manual_matches_the_canonical_catalog() -> None:
    assert MANPAGE.read_text(encoding="utf-8") == render_manpage(), (
        "regenerate the checked-in manual after a catalog change: "
        "uv run --extra test python scripts/render-manpage.py --output man/loopx.1"
    )
