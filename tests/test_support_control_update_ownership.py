"""Refs GH-C06: the update command still belongs to support control.

`update` was extracted from `cli_commands/support_control.py` into
`cli_commands/support_control_update.py` so the shared support-control seam
stops owning unrelated command groups at once. The extraction must not change
the public invocation, so these cases pin the two things that could silently
break it: the command must still be part of the support control set, and it
must still be registered exactly once.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from loopx.cli_commands import support_control
from loopx.cli_commands import support_control_update as update_module

COMMANDS_DIR = Path(update_module.__file__).resolve().parent
ADD_PARSER_RE = re.compile(
    r"subparsers\.add_parser\(\s*(?:\n\s*)?[\"'](?P<command>[^\"']+)[\"']",
    re.MULTILINE,
)

UPDATE_COMMANDS = ("update",)


def registered_commands() -> dict[str, list[str]]:
    registrations: dict[str, list[str]] = {}
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        for match in ADD_PARSER_RE.finditer(path.read_text(encoding="utf-8")):
            registrations.setdefault(match.group("command"), []).append(path.name)
    return registrations


def test_update_is_still_a_support_control_command() -> None:
    for command in UPDATE_COMMANDS:
        assert command in support_control.SUPPORT_CONTROL_COMMANDS


def test_update_is_registered_exactly_once() -> None:
    registrations = registered_commands()
    for command in UPDATE_COMMANDS:
        assert registrations[command] == ["support_control_update.py"]


def test_owner_module_exposes_both_halves() -> None:
    """Registration and dispatch moved together, so both live in the new module."""
    assert callable(update_module.register_update_command)
    assert callable(update_module.handle_update_command)


def test_owner_module_owns_exactly_its_group() -> None:
    assert update_module.UPDATE_CONTROL_COMMANDS == {*UPDATE_COMMANDS, "upgrade"}


def test_upgrade_reuses_update_owner_and_parser() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    update_module.register_update_command(subparsers, lambda _parser: None)
    assert subparsers.choices["upgrade"] is subparsers.choices["update"]
    assert "upgrade" in support_control.SUPPORT_CONTROL_COMMANDS


def test_dispatch_ignores_other_commands() -> None:
    """A non-update command must fall through untouched, before any work."""
    args = argparse.Namespace(command="registry")
    assert (
        update_module.handle_update_command(
            args,
            registry_path=Path("/nonexistent-registry"),
            registry_was_supplied=False,
            print_payload=lambda *_args: None,
            output_format=lambda *_args: "json",
        )
        is None
    )
