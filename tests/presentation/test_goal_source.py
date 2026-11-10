"""Goal access composes transports without gaining execution authority."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from hashlib import sha256

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


def test_document_attachment_does_not_rebind_existing_source_identity():
    raw = manifest()
    source = GoalSource.parse(raw)
    legacy_identity = dict(id='source', label='Source', registry='/registry', runtime_root='/runtime',
        cli='/bin/loopx', python='/bin/python', path='/bin', connection='local', environment='host',
        goal_id='original', host_alias=None, container_id=None)
    assert source.namespace == sha256(json.dumps(legacy_identity, sort_keys=True).encode()).hexdigest()
    binding = dict(declared_path='/source/task.md', path='/capture/task.md', sha256='a' * 64)
    assert GoalSource.parse({**raw, 'goal_document_snapshot': binding}).namespace == source.namespace
    for invalid in [{**binding, 'sha256': 'unknown'}, {**binding, 'path': 'relative.md'},
                    {**binding, 'extra': True}]:
        with pytest.raises(ValueError):
            GoalSource.parse({**raw, 'goal_document_snapshot': invalid})
    with pytest.raises(ValueError):
        GoalSource.parse({**raw, 'scope': {'all_registered': True}, 'goal_document_snapshot': binding})


def test_document_relative_location_never_falls_back_to_reader_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    document = tmp_path / 'task.md'
    document.write_text('# A file in the reader directory is not the original declaration\n')
    registry = tmp_path / 'registry.json'
    goal = {'id': 'original', 'authority_sources': [
        {'kind': 'goal_doc', 'role': 'primary_goal_document', 'path': 'task.md'}]}
    registry.write_text(json.dumps({'goals': [goal]}))
    source = GoalSource.parse({**manifest(), 'registry': str(registry), 'python': sys.executable})
    monkeypatch.setattr(GoalSource, 'read_status', lambda self, goal_id=None:
        {'ok': True, 'run_history': {'goals': [{'id': 'original', 'registry_member': True}]}})
    monkeypatch.setattr(GoalSource, 'read_registry_goal', lambda self, goal_id:
        json.loads(registry.read_text()))
    reader = GoalSourceReader(source, history_projector=explore_history_rows)
    with pytest.raises(subprocess.CalledProcessError):
        reader.document('original')
    # An absolute declared file needs no guessed repository or process cwd.
    goal['authority_sources'][0]['path'] = str(document)
    registry.write_text(json.dumps({'goals': [goal]}))
    assert reader.document('original')['document']['text'] == document.read_text()


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
        return json.dumps({"ok": True, "run_history": {"goals": [{"id": "original"}]},
            "goals": [{"id": "original", "repo": "/source", "unused": "not projected", "authority_sources": [
                {"kind": "goal_doc", "role": "primary_goal_document", "path": "task.md", "unused": "not projected"}]}]})

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
    declaration = source.read_registry_goal("original")
    assert declaration == {"goals": [{"id": "original", "repo": "/source", "authority_sources": [
        {"kind": "goal_doc", "role": "primary_goal_document", "path": "task.md"}]}]}
    registry_argv = commands[-1][0]
    if connection == "ssh":
        registry_argv = shlex.split(registry_argv[-1])
    assert registry_argv[-1] == "registry", "Use the native registry owner, not a parallel wire decoder"


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
        (project / f"{goal_id}.md").write_text(
            "---\nstatus: active\n---\n\n# Research\n\n## Agent Todo\n")
        goals.append(dict(id=goal_id, status="active", domain="source-read", repo=str(project),
            state_file=f"{goal_id}.md", adapter={"kind": "test_v0", "status": "connected-read-only"},
            authority_sources=[]))
    registry.write_text(json.dumps(dict(schema_version=1, common_runtime_root=str(runtime), goals=goals)))
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    for goal_id in ("first", "second"):
        initialize_canonical_authority(runtime, goal_id,
            build_todo_runtime_shadow_projection(goal_id=goal_id, todos=[], handoff_mode="soft_claim"),
            state_path=project / f"{goal_id}.md", provider="sqlite")
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
