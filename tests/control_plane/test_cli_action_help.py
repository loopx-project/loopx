"""Actual help entrypoints expose one grammar, without touching Goal state."""
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.cli import build_parser

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("command,expected,absent", [
    (["task-lease", "inspect"], ["--goal-id", "--todo-id"], ["--owner", "--expected-version"]),
    (["task-lease", "renew"], ["--owner", "--expected-version", "--ttl-seconds"], ["--new-owner", "--write-scope"]),
    (["task-lease", "transfer"], ["--new-owner", "--transfer-claim"], ["--write-worktree"]),
    (["task-lease", "acquire"], ["--write-worktree", "--write-scope"], ["--new-owner"]),
    (["task-lease", "release"], ["--expected-version"], ["--ttl-seconds"]),
    (["todo", "list"], ["--todo-id", "--thin", "--limit"], ["--claimed-by", "--next-agent-todo"]),
    (["todo", "claim"], ["--claimed-by", "--claim-operation-id"],
     ["--note", "--next-agent-todo", "--task-lease-expected-version", "--task-lease-idempotency-key"]),
    (["todo", "update"], ["--note", "--update-operation-id", "--validation-command", "--resume-when"],
     ["--next-agent-todo", "--decision-outcome", "--result-file", "--task-lease-expected-version"]),
    (["todo", "receipt"], ["--operation-id"], ["--todo-id", "--claimed-by"]),
    (["todo", "result-read"], ["--todo-id"], ["--text", "--claimed-by"]),
    (["todo", "plan"], ["--agent-id", "--text"], ["--claimed-by", "--todo-id"]),
    (["todo", "project-markdown"], ["--provider-revision", "--execute"], ["--claimed-by"]),
])
def test_action_help_from_real_cli_with_unavailable_registry(tmp_path, command, expected, absent):
    registry = tmp_path / "does-not-exist.json"
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                             *command, "--help"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert " ".join(command) in result.stdout.splitlines()[0]
    # Check option declarations, not mentions inside conditional guidance.
    declared = [line.strip().split()[0] for line in result.stdout.splitlines()
                if line.startswith("  --")]
    assert all(flag in declared for flag in expected)
    assert all(flag not in declared for flag in absent)
    assert not registry.exists()


def test_action_help_keeps_required_usage_and_lists_optional_grammar_once(capsys):
    parser = build_parser()
    command = ["todo", "update", "--goal-id", "fixture", "--todo-id", "todo_a", "--note", "progress"]
    before = vars(parser.parse_args(command))
    with pytest.raises(SystemExit) as exited:
        parser.parse_args(["todo", "update", "--help"])
    assert exited.value.code == 0
    output = capsys.readouterr().out
    usage = output.split("\n\n", 1)[0]
    assert "--goal-id" in usage and "[options]" in usage
    assert "--note" not in usage and "--note NOTE" in output
    assert vars(parser.parse_args(command)) == before


def test_help_does_not_mutate_reused_parser_or_change_claim_input(capsys):
    parser = build_parser()
    command = ["todo", "claim", "--goal-id", "fixture", "--todo-id", "todo_a",
               "--claimed-by", "peer", "--task-lease-expected-version", "0"]
    before = vars(parser.parse_args(command))
    with pytest.raises(SystemExit) as exited:
        parser.parse_args(["todo", "list", "--help"])
    assert exited.value.code == 0
    assert vars(parser.parse_args(command)) == before
    with pytest.raises(SystemExit):
        parser.parse_args(["todo", "--help"])
    assert "--next-agent-todo" in capsys.readouterr().out
