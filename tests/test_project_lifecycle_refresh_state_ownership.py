"""Refs GH-C06: the `refresh-state` group still belongs to the project lifecycle commands.

`refresh-state` was extracted from `cli_commands/project_lifecycle.py` into
`cli_commands/project_lifecycle_refresh_state.py` to bring that module back under
the 1000-line default budget. The extraction must not change the public
invocation, so these cases pin the two things that could silently break it: the
command must still be part of the project lifecycle set, and it must still be
registered exactly once.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from loopx.cli_commands import project_lifecycle
from loopx.cli_commands import project_lifecycle_refresh_state as refresh_module

COMMANDS_DIR = Path(refresh_module.__file__).resolve().parent
ADD_PARSER_RE = re.compile(
    r"subparsers\.add_parser\(\s*(?:\n\s*)?[\"'](?P<command>[^\"']+)[\"']",
    re.MULTILINE,
)


def registered_commands() -> dict[str, list[str]]:
    registrations: dict[str, list[str]] = {}
    for path in sorted(COMMANDS_DIR.glob("*.py")):
        for match in ADD_PARSER_RE.finditer(path.read_text(encoding="utf-8")):
            registrations.setdefault(match.group("command"), []).append(path.name)
    return registrations


def test_refresh_state_is_still_a_project_lifecycle_command() -> None:
    assert "refresh-state" in project_lifecycle.PROJECT_LIFECYCLE_COMMANDS


def test_refresh_state_is_registered_exactly_once() -> None:
    registrations = registered_commands()
    assert registrations["refresh-state"] == ["project_lifecycle_refresh_state.py"]


def test_owner_module_exposes_both_halves() -> None:
    """Registration and dispatch moved together, so both live in the new module."""
    assert callable(refresh_module.register_refresh_state_command)
    assert callable(refresh_module.handle_refresh_state_command)


def test_dispatch_ignores_other_commands() -> None:
    """A non-refresh-state command must fall through untouched, before any work."""
    args = argparse.Namespace(command="reward")
    assert (
        refresh_module.handle_refresh_state_command(
            args,
            registry_path=Path("/nonexistent-registry"),
            print_payload=lambda *_args: None,
            output_format=lambda *_args: "json",
            append_cli_rollout_event=lambda *_args, **_kwargs: {},
        )
        is None
    )


def test_authoring_help_matches_existing_validator_boundaries() -> None:
    """The discoverable CLI constraints must agree with the admission owners."""
    from loopx.control_plane.work_items.progress_observation import replan_writeback_requirements
    from loopx.control_plane.work_items.progress_result import normalize_progress_identifier

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    refresh_module.register_refresh_state_command(subparsers, lambda _: None)
    actions = {action.dest: action for action in subparsers.choices["refresh-state"]._actions}
    evidence_help = actions["progress_evidence_ids"].help
    assert "1-128" in evidence_help
    assert "leading-dot file path" in evidence_help
    assert normalize_progress_identifier("evidence:validation-1") == "evidence:validation-1"
    assert normalize_progress_identifier(".local/validation.json") is None
    assert normalize_progress_identifier("x" * 128) is not None
    assert normalize_progress_identifier("x" * 129) is None

    # Call the TypeScript authoring owner, rather than adding a Python budget.
    contract = replan_writeback_requirements({
        "satisfying_semantic_outcomes": ["fresh_vision_path_outcome"],
    })["writeback_contract"]["vision_authoring"]
    limits = contract["fields"]["todo_delta"]
    delta_help = actions["vision_todo_delta"].help
    assert f"at most {limits['max_item_chars']} characters" in delta_help
    assert f"first {limits['max_retained_items']} are retained" in delta_help

    recovery_help = " ".join(subparsers.choices["checkpoint-context"].format_help().split())
    assert "requires the original committed refresh-state writeback" in recovery_help
    assert "For the first writeback" in recovery_help

    assert "at most 1200 characters after trimming" in actions["next_action"].help
    boundary_help = actions["delivery_boundary"].help
    assert "within-Todo --next-action is allowed" in boundary_help
    assert "--autonomous-replan-recorded require semantic_closeout" in boundary_help
    assert "vision checkpoint" in boundary_help
    assert "do not add this ACK to an ordinary in_flight_continuation" in actions[
        "autonomous_replan_recorded"
    ].help
