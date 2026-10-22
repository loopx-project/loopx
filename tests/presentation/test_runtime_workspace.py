"""Real HTTP boundary: source identity, read-only access and full history."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import pytest

from loopx.capabilities.explore.result_log import build_explore_node_event, explore_history_rows
from loopx.control_plane.status.goal_source import GoalSource, GoalSourceReader, read_endpoint
from loopx.presentation.runtime_workspace import RuntimeWorkspaceServer
from loopx.presentation.runtime_workspace import main as runtime_workspace_main


class Source(GoalSource):
    def __init__(self, identity):
        super().__init__(identity, identity, "/registry", "/runtime", "/cli", "/python",
                         "/bin", "local", "host", "shared-goal")
        self.failed = False
        self.calls = 0

    def read_status(self, goal_id=None):
        self.check_access(goal_id)
        self.calls += 1
        if self.failed:
            raise OSError("private diagnostic must not reach the browser")
        return {"ok": True, "run_history": {"goals": [{"id": self.goal_id,
            "display_name": self.id, "registry_goal_digest": self.id}], "run_count": 17}}

    def read_events(self, goal_id):
        self.check_access(goal_id)
        return [build_explore_node_event(goal_id=goal_id, node_id=f"question{i}",
            title=self.id) for i in range(91)]


@contextmanager
def serving(source, assets):
    server = RuntimeWorkspaceServer(source, assets)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def get(server, path="/status.json", *, method="GET", headers=None, namespace=True, timeout=5):
    source_url, target = urlsplit(server.status_url), urlsplit(path)
    query = parse_qsl(target.query, keep_blank_values=True)
    if namespace and not any(key == "source_namespace" for key, _ in query):
        query += [("source_namespace", server.source.namespace)]
    url = urlunsplit((source_url.scheme, source_url.netloc, target.path, urlencode(query), ""))
    request = Request(url, method=method, headers=headers or {})
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


def test_sources_with_same_goal_keep_separate_status_history_and_cursors(tmp_path):
    a, b = Source("retained"), Source("fresh")
    with serving(a, tmp_path) as first, serving(b, tmp_path) as second:
        assert get(first)[1]["run_history"]["goals"][0]["display_name"] == "retained"
        assert get(second)[1]["run_history"]["goals"][0]["display_name"] == "fresh"
        assert get(first)[1]["run_history"]["run_count"] == 17
        path = "/api/chat/explore-results?view=history&goal_id=shared-goal"
        _, page = get(first, path)
        cursor = page["next_cursor"]
        assert get(second, f"{path}&cursor={cursor}")[0] == 409
        rows = page["items"]
        while page["next_cursor"]:
            _, page = get(first, f'{path}&cursor={page["next_cursor"]}')
            rows += page["items"]
        assert len(rows) == 91
        assert len({row["event_id"] for row in rows}) == 91
        assert all(row["title"] == "retained" for row in rows)
        assert get(second, path)[1]["items"][0]["title"] == "fresh"


def test_scope_origin_and_mutation_are_rejected_before_source_read(tmp_path):
    source = Source("original")
    with serving(source, tmp_path) as server:
        assert get(server, "/status.json?goal_id=another")[0] == 404
        assert get(server, headers={"Host": "external.example"})[0] == 403
        assert get(server, headers={"Origin": "https://external.example"})[0] == 403
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert get(server, method=method)[0] == 405
        assert get(server, "/unavailable")[0] == 404
        assert get(server, "/api/goal-source/status", namespace=False)[0] == 409
        assert get(server, "/api/goal-source/status?source_namespace=" + "0" * 64)[0] == 409
        assert get(server, "/status.json?goal_id=shared-goal&goal_id=another")[0] == 400
        assert get(server, "/api/goal-source")[1]["operations"]["lifecycle"] is False
        assert source.calls == 0


def test_failed_refresh_does_not_serve_old_success_and_recovers(tmp_path):
    source = Source("original")
    with serving(source, tmp_path) as server:
        assert get(server)[0] == 200
        _, page = get(server, "/api/chat/explore-results?view=history&goal_id=shared-goal")
        cursor = page["next_cursor"]
        server.reader._read_at = 0
        source.failed = True
        code, error = get(server)
        assert code == 503 and "private diagnostic" not in json.dumps(error)
        source.failed = False
        assert get(server)[0] == 200
        assert source.calls == 3
        assert get(server, f"/api/chat/explore-results?view=history&goal_id=shared-goal&cursor={cursor}")[0] == 409


@pytest.mark.parametrize("invalid_kind", ["null", "event-pairs"])
def test_invalid_native_history_clears_only_its_source_and_recovers(tmp_path, invalid_kind):
    """Exercise actual native CLI/log IO through both shared HTTP consumers."""
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    (project / "goal.md").write_text("---\nstatus: active\n---\n\n# Research\n")
    registry = project / "registry.json"
    registry.write_text(json.dumps(dict(schema_version=1, common_runtime_root=str(runtime), goals=[dict(
        id="shared-goal", status="active", domain="source-read", repo=str(project),
        state_file="goal.md", adapter={"kind": "test_v0", "status": "connected-read-only"},
        authority_sources=[])])))
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    initialize_canonical_authority(runtime, "shared-goal",
        build_todo_runtime_shadow_projection(goal_id="shared-goal", todos=[], handoff_mode="soft_claim"),
        state_path=project / "goal.md", provider="sqlite")
    cli = Path(sys.executable).with_name("loopx")
    assert cli.is_file(), "Use the source checkout's prepared Python environment"
    source = GoalSource("native", "Native", str(registry), str(runtime), str(cli),
                        sys.executable, os.environ["PATH"], "local", "host", "shared-goal")
    events = [build_explore_node_event(goal_id="shared-goal", node_id=f"question{i}",
                                     title=f"Question {i}") for i in range(43)]
    log = runtime / "goals" / "shared-goal" / "explore-result-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    valid = "\n".join(json.dumps(event) for event in events).encode()
    log.write_bytes(valid)
    routes = ("/api/goal-source/history?goal_id=shared-goal",
              "/api/chat/explore-results?view=history&goal_id=shared-goal")
    with serving(source, tmp_path) as server, serving(Source("untouched"), tmp_path) as other:
        # Match the shared client's budget for the native CLI's 25-second IO.
        code, page = get(server, routes[0], timeout=30)
        assert code == 200 and page["total"] == 43 and page["next_cursor"]
        cursor = page["next_cursor"]
        _, other_page = get(other, routes[0])
        other_status = other.reader._status
        invalid = None if invalid_kind == "null" else list(events[20].items())
        corrupted = [*events[:20], invalid, *events[21:]]
        log.write_text("\n".join(json.dumps(event) for event in corrupted))
        for route in routes:
            code, error = get(server, route, timeout=30)
            assert code == 503 and error["ok"] is False
            assert str(project) not in json.dumps(error)
            assert server.reader._status is None and not server.reader._pages._snapshots
        with pytest.raises(ValueError):
            read_endpoint(server.status_url, "history", goal_id="shared-goal")
        assert other.reader._status is other_status
        assert get(other, f'{routes[0]}&cursor={other_page["next_cursor"]}')[0] == 200
        log.write_bytes(valid)
        assert get(server, f"{routes[0]}&cursor={cursor}", timeout=30)[0] == 409
        code, restored = get(server, routes[0], timeout=30)
        assert code == 200 and restored["total"] == 43
        _, tail = get(server, f'{routes[0]}&cursor={restored["next_cursor"]}', timeout=30)
        assert {row["event_id"] for row in restored["items"] + tail["items"]} == {
            event["event_id"] for event in events}
        assert log.read_bytes() == valid


def test_cli_agent_and_app_use_the_same_reader_and_source_binding(tmp_path):
    source = Source("original")
    with serving(source, tmp_path) as server:
        description = read_endpoint(server.status_url, "describe")
        assert description["read_only"] and not description["operations"]["analysis"]
        cli = subprocess.run([sys.executable, "-m", "loopx.control_plane.status.goal_source",
                              "goals", "--endpoint", server.status_url],
                             capture_output=True, text=True, check=True, timeout=10)
        assert json.loads(cli.stdout)["source"]["namespace"] == source.namespace
        assert read_endpoint(server.status_url, "goals")["items"][0]["id"] == "shared-goal"
        native = read_endpoint(server.status_url, "status")["status"]
        assert native["run_history"] == get(server)[1]["run_history"]
        page = read_endpoint(server.status_url, "history", goal_id="shared-goal")
        assert page["total"] == 91
        assert read_endpoint(server.status_url, "history", goal_id="shared-goal",
                             cursor=page["next_cursor"])["items"]
        with pytest.raises(ValueError, match="Conflicting source"):
            read_endpoint(server.status_url, "status", source_namespace="0" * 64)
        with pytest.raises(ValueError, match="not available"):
            read_endpoint(server.status_url, "history", goal_id="another")


def test_native_transport_preserves_valid_events_and_rejects_wrong_goal(monkeypatch):
    source = GoalSource.parse(dict(id="native", label="Native", connection={"kind": "local"},
        environment={"kind": "container", "container_id": "a" * 64}, scope={"goal_id": "shared-goal"},
        registry="/registry.json", runtime_root="/runtime",
        cli="/bin/loopx", python="/bin/python", path="/bin"))
    node = build_explore_node_event(goal_id="shared-goal", node_id="question", title="Actual question")
    commands = []

    def run(argv, **_kwargs):
        commands.append(argv)
        if source.cli in argv:
            return json.dumps({"ok": True, "run_history": {"goals": [{"id": source.goal_id}]}})
        return json.dumps(node)

    monkeypatch.setattr("loopx.control_plane.status.goal_source._run", run)
    assert source.read_status()["run_history"]["goals"][0]["id"] == "shared-goal"
    events = source.read_events("shared-goal")
    assert events[0]["event_id"] == node["event_id"]
    assert events[0]["title"] == "Actual question"
    assert all(source.container_id in argv for argv in commands)
    node["goal_id"] = "different"
    with pytest.raises(ValueError):
        GoalSourceReader(source, history_projector=explore_history_rows).history("shared-goal")


def test_manifest_requires_physical_identity():
    raw = dict(id="source", label="Source", connection={"kind": "local"},
        environment={"kind": "container", "container_id": "alias"}, scope={"goal_id": "goal"},
        registry="/registry", runtime_root="/runtime", cli="/cli", python="/python", path="/bin")
    with pytest.raises(ValueError, match="pinned container"):
        GoalSource.parse(raw)


def test_default_entry_rejects_stale_bundle_before_opening_sources(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["runtime-workspace", "--sources", str(tmp_path / "unused.json")])

    def invalid_bundle(_assets, *, source_root):
        assert (source_root / "apps/presentation/dashboard/package.json").is_file()
        raise RuntimeError("stale or invalid: rebuild the chat bundle")

    monkeypatch.setattr("loopx.presentation.runtime_workspace.validate_bundle", invalid_bundle)
    with pytest.raises(SystemExit) as stopped:
        runtime_workspace_main()
    assert stopped.value.code == 2
    assert "rebuild the chat bundle" in capsys.readouterr().err
