"""Goal access composes transports without gaining execution authority."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from loopx.capabilities.explore.result_log import build_explore_node_event, explore_history_rows
from loopx.control_plane.status.goal_source import GoalSource, GoalSourceReader, SourceScopeError


def manifest(*, connection=None, environment=None, scope=None):
    return dict(id="source", label="Source", registry="/registry", runtime_root="/runtime",
        cli="/bin/loopx", python="/bin/python", path="/bin",
        connection=connection or {"kind": "local"}, environment=environment or {"kind": "host"},
        scope=scope or {"goal_id": "original"})


def test_explicit_source_scope_is_required_and_unknown_fields_are_rejected():
    current = manifest(environment={"kind": "container", "container_id": "a" * 64})
    source = GoalSource.parse(current)
    with pytest.raises(SourceScopeError):
        source.check_access("another")
    assert replace(source, runtime_root="/different").namespace != source.namespace
    with pytest.raises(ValueError):
        GoalSource.parse({**current, "scope": {}})
    with pytest.raises(ValueError):
        GoalSource.parse({**current, "unexpected": True})
    with pytest.raises(ValueError):
        GoalSource.parse({key: value for key, value in current.items() if key != "connection"})


@pytest.mark.parametrize("connection", ["local", "ssh"])
@pytest.mark.parametrize("environment", ["host", "container"])
def test_connection_and_environment_compose_with_native_read_only_commands(monkeypatch, connection, environment):
    raw = manifest(connection={"kind": connection, **({"host_alias": "lab"} if connection == "ssh" else {})},
        environment={"kind": environment, **({"container_id": "b" * 64} if environment == "container" else {})})
    raw["registry"] = "/registry with space"
    source = GoalSource.parse(raw)
    monkeypatch.setattr("loopx.control_plane.status.goal_source.configured_ssh_host_aliases", lambda: ["lab"])
    commands = []

    def run(argv, *, env=None):
        commands.append((argv, env))
        return json.dumps({"ok": True, "run_history": {"goals": [{"id": "original"}]}})

    monkeypatch.setattr("loopx.control_plane.status.goal_source._run", run)
    assert source.read_status()["ok"]
    argv, env = commands[0]
    if connection == "ssh":
        assert argv[:2] == ["ssh", "-T"] and argv[-2] == "lab"
        argv = shlex.split(argv[-1])
        assert env is None
    else:
        assert env["LOOPX_USAGE_PING"] == "0"
    if environment == "container":
        assert argv[:2] == ["docker", "exec"] and "b" * 64 in argv
    assert argv[-4:] == ["json", "status", "--goal-id", "original"]
    assert argv[argv.index("--registry") + 1] == "/registry with space"


def test_revoked_ssh_alias_invalidates_cached_status_and_pages(monkeypatch):
    source = GoalSource.parse(manifest(connection={"kind": "ssh", "host_alias": "lab"}))
    aliases = ["lab"]
    monkeypatch.setattr("loopx.control_plane.status.goal_source.configured_ssh_host_aliases", lambda: aliases)
    node = build_explore_node_event(goal_id="original", node_id="question", title="Recorded question")

    def run(argv, **_kwargs):
        if " status " in argv[-1]:
            return json.dumps({"ok": True, "run_history": {"goals": [{"id": "original"}]}})
        return json.dumps(node)

    monkeypatch.setattr("loopx.control_plane.status.goal_source._run", run)
    reader = GoalSourceReader(source, history_projector=explore_history_rows)
    assert reader.history("original")["total"] == 1
    aliases.clear()
    with pytest.raises(SourceScopeError):
        reader.status()
    assert reader._status is None and not reader._pages._snapshots


def test_registered_scope_does_not_include_runtime_only_goals(monkeypatch):
    source = GoalSource.parse(manifest(scope={"all_registered": True}))
    monkeypatch.setattr("loopx.control_plane.status.goal_source._run", lambda *args, **kwargs:
        json.dumps({"ok": True, "run_history": {"goals": [{"id": "runtime-only", "registry_member": False}]}}))
    with pytest.raises(SourceScopeError, match="registered"):
        source.read_status("runtime-only")


def test_real_native_host_lists_goals_and_reads_canonical_history(tmp_path):
    """Disposable native registry/CLI, not a mock transport or active Goal."""
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    registry = project / "registry.json"
    goals = []
    for goal_id in ("first", "second"):
        (project / f"{goal_id}.md").write_text("---\nstatus: active\n---\n\n# Research\n")
        goals.append(dict(id=goal_id, status="active", domain="source-read", repo=str(project),
            state_file=f"{goal_id}.md", adapter={"kind": "test_v0", "status": "connected-read-only"},
            authority_sources=[]))
    registry.write_text(json.dumps(dict(schema_version=1, common_runtime_root=str(runtime), goals=goals)))
    cli = Path(sys.executable).with_name("loopx")
    assert cli.is_file(), "Use the source checkout's prepared Python environment"
    source = GoalSource.parse({**manifest(scope={"all_registered": True}), "registry": str(registry),
        "runtime_root": str(runtime), "cli": str(cli), "python": sys.executable, "path": os.environ["PATH"]})
    reader = GoalSourceReader(source, history_projector=explore_history_rows)
    assert {goal["id"] for goal in reader.goals()["items"]} == {"first", "second"}
    log = runtime / "goals" / "first" / "explore-result-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    events = [build_explore_node_event(goal_id="first", node_id=f"question{i}", title=f"Question {i}")
              for i in range(43)]
    log.write_text("\n".join(json.dumps(event) for event in events))
    page = reader.history("first")
    assert page["total"] == 43
    second = reader.history("first", page["next_cursor"])
    assert len(page["items"] + second["items"]) == 43
    assert {row["event_id"] for row in page["items"] + second["items"]} == {event["event_id"] for event in events}
    assert reader.history("second")["total"] == 0
    with pytest.raises((SourceScopeError, subprocess.SubprocessError)):
        reader.status("unregistered")
    assert reader.status("first")["run_history"]["goals"][0]["id"] == "first"
