from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.cli import build_parser, main
from loopx.cli_commands import dash
from loopx.dash_server import (
    DEFAULT_DASH_HOST,
    DEFAULT_DASH_PORT,
    DEFAULT_DASH_REFRESH_SECONDS,
)
from loopx.entrypoint import main as entrypoint_main
from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture


@pytest.mark.parametrize(
    ("options", "field", "expected"),
    [
        (["--goal-id", "sample-goal"], "goal_id", "sample-goal"),
        (["--host", "localhost"], "host", "localhost"),
        (["--port", "19001"], "port", 19001),
        (["--refresh-seconds", "5"], "refresh_seconds", 5),
        (["--verbose"], "verbose", True),
    ],
)
def test_serve_preserves_options_before_subcommand(options, field, expected):
    args = build_parser().parse_args(["dash", *options, "serve"])
    assert getattr(args, field) == expected


@pytest.mark.parametrize("subcommand", [[], ["serve"], ["generate"]])
def test_dash_defaults_are_preserved(subcommand):
    args = build_parser().parse_args(["dash", *subcommand])
    assert args.goal_id is None
    assert args.host == DEFAULT_DASH_HOST
    assert args.port == DEFAULT_DASH_PORT
    assert args.refresh_seconds == DEFAULT_DASH_REFRESH_SECONDS
    assert args.verbose is False


def test_explicit_serve_options_override_parent_values():
    args = build_parser().parse_args(
        [
            "dash", "--goal-id", "first-goal", "--host", "127.0.0.1",
            "--port", "19001", "--refresh-seconds", "5",
            "serve", "--goal-id", "second-goal", "--host", "localhost",
            "--port", "19002", "--refresh-seconds", "7", "--verbose",
        ]
    )
    assert args.goal_id == "second-goal"
    assert args.host == "localhost"
    assert args.port == 19002
    assert args.refresh_seconds == 7
    assert args.verbose is True


@pytest.mark.parametrize(
    "options",
    [
        ["--goal-id", "sample-goal", "generate"],
        ["generate", "--goal-id", "sample-goal"],
        ["--goal-id", "first-goal", "generate", "--goal-id", "sample-goal"],
    ],
)
def test_generate_preserves_goal_selection(options):
    args = build_parser().parse_args(["dash", *options])
    assert args.goal_id == "sample-goal"


def test_cli_dispatches_parent_options_to_server(tmp_path: Path, monkeypatch):
    captured = {}
    monkeypatch.setattr(dash, "serve_dash", lambda **kwargs: captured.update(kwargs))

    result = main(
        [
            "--registry", str(tmp_path / "registry.json"),
            "--runtime-root", str(tmp_path / "runtime"),
            "dash", "--goal-id", "sample-goal", "--host", "localhost",
            "--port", "19001", "--refresh-seconds", "5", "--verbose", "serve",
        ]
    )

    assert result == 0
    assert captured["goal_id"] == "sample-goal"
    assert captured["host"] == "localhost"
    assert captured["port"] == 19001
    assert captured["refresh_seconds"] == 5
    assert captured["verbose"] is True


@pytest.mark.parametrize(
    ("options", "expected_focus", "expected_count"),
    [
        (["generate"], None, 2),
        (["--goal-id", "sample-a", "generate"], "sample-a", 1),
        (["generate", "--goal-id", "sample-a"], "sample-a", 1),
    ],
)
def test_generate_filters_real_registry(
    tmp_path: Path, capsys, options, expected_focus, expected_count
):
    goals = []
    for goal_id in ("sample-a", "sample-b"):
        project = tmp_path / goal_id
        state = project / ".codex" / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
        state.parent.mkdir(parents=True)
        state.write_text(
            "---\nstatus: active-read-only\nowner_mode: goal\n"
            'objective: "Read the synthetic sample."\n---\n\n'
            f"# {goal_id}\n\n## Agent Todo\n\n- [ ] Inspect the sample.\n",
            encoding="utf-8",
        )
        goals.append(
            {
                "id": goal_id,
                "status": "active-read-only",
                "repo": str(project),
                "state_file": str(state.relative_to(project)),
                "adapter": {
                    "kind": "read_only_project_map_v0",
                    "status": "connected-read-only",
                },
                "coordination": {
                    "registered_agents": [f"{goal_id}-agent"],
                    "agent_model": "peer_v1",
                },
                "authority_sources": [],
            }
        )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"schema_version": "0.1", "goals": goals}), encoding="utf-8"
    )
    for goal in goals:
        initialize_canonical_source_fixture(
            tmp_path / "runtime", goal["id"],
            state_path=Path(goal["repo"]) / goal["state_file"],
        )

    result = entrypoint_main(
        [
            "--registry", str(registry),
            "--runtime-root", str(tmp_path / "runtime"),
            "--format", "json", "dash", *options,
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["boundary_ok"] is True
    assert payload["focus_goal_id"] == expected_focus
    assert payload["projection"]["overview"]["goal_count"] == expected_count
    assert "sample-a" in payload["html"]
    assert ("sample-b" in payload["html"]) is (expected_focus is None)
