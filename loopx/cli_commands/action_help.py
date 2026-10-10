"""Show an action's grammar without changing the parser or reading state."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping


class ActionHelp(argparse.Action):
    def __init__(self, option_strings, dest, *, command_dest, action_fields,
                 common_fields, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)
        self.command_dest = command_dest
        self.action_fields = action_fields
        self.common_fields = common_fields

    def __call__(self, parser, namespace, values, option_string=None):
        command = getattr(namespace, self.command_dest, None)
        allowed = self.action_fields.get(command)
        if allowed is None:
            parser.print_help()
        else:
            fields = set(allowed) | set(self.common_fields) | {self.dest}
            actions = [action for action in parser._actions if action.dest in fields]
            formatter = parser.formatter_class(prog=f"{parser.prog} {command}")
            formatter.add_usage(None, actions, [])
            formatter.add_text("Options for this action. Conditional requirements and "
                               "state/authority checks still apply. Global --registry "
                               "and --runtime-root go before the command.")
            for group in parser._action_groups:
                selected = [action for action in group._group_actions if action in actions]
                if selected:
                    formatter.start_section(group.title)
                    formatter.add_arguments(selected)
                    formatter.end_section()
            parser._print_message(formatter.format_help(), sys.stdout)
        parser.exit()


def install_action_help(parser: argparse.ArgumentParser, *, command_dest: str,
                        action_fields: Mapping[str, frozenset[str]],
                        common_fields: frozenset[str]) -> None:
    # Replace only argparse's presentation action; the actual grammar stays intact.
    old = parser._option_string_actions.get("--help")
    if old is not None:
        parser._remove_action(old)
        for group in parser._action_groups:
            if old in group._group_actions:
                group._group_actions.remove(old)
        for flag in old.option_strings:
            parser._option_string_actions.pop(flag, None)
    parser.add_argument("-h", "--help", action=ActionHelp, command_dest=command_dest,
                        action_fields=action_fields, common_fields=common_fields,
                        help="Show help for the selected action and exit.")
