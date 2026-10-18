"""Real HTTP boundary: source identity, read-only access and full history."""

from contextlib import contextmanager
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from loopx.capabilities.explore.result_log import build_explore_node_event
from loopx.presentation.runtime_workspace import ContainerSource, RuntimeWorkspaceServer
from loopx.presentation.runtime_workspace import main as runtime_workspace_main


class Source:
    goal_id = "shared-goal"

    def __init__(self, identity):
        self.id = identity
        self.label = identity
        self.failed = False
        self.calls = 0

    def read(self):
        self.calls += 1
        if self.failed:
            raise OSError("private diagnostic must not reach the browser")
        return {"ok": True, "run_history": {"goals": [{"id": self.goal_id,
            "display_name": self.id, "registry_goal_digest": self.id}], "run_count": 17}}, [
            {"event_id": f"{self.id}-{i}", "title": self.id} for i in range(91)]


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


def get(server, path="/status.json", *, method="GET", headers=None):
    request = Request(server.status_url.replace("/status.json", path), method=method, headers=headers or {})
    try:
        response = urlopen(request, timeout=5)
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
        assert source.calls == 0


def test_failed_refresh_does_not_serve_old_success_and_recovers(tmp_path):
    source = Source("original")
    with serving(source, tmp_path) as server:
        assert get(server)[0] == 200
        server._read_at = 0
        source.failed = True
        code, error = get(server)
        assert code == 503 and "private diagnostic" not in json.dumps(error)
        source.failed = False
        assert get(server)[0] == 200
        assert source.calls == 3


def test_native_transport_preserves_valid_events_and_rejects_wrong_goal(monkeypatch):
    source = ContainerSource("native", "Native", "a" * 64, "shared-goal", "/registry.json",
        "/runtime", "/bin/loopx", "/bin/python", "/bin")
    node = build_explore_node_event(goal_id="shared-goal", node_id="question", title="Actual question")
    commands = []

    def run(argv):
        commands.append(argv)
        if source.cli in argv:
            return json.dumps({"ok": True, "run_history": {"goals": [{"id": source.goal_id}]}})
        return json.dumps(node)

    monkeypatch.setattr("loopx.presentation.runtime_workspace._run", run)
    _, events = source.read()
    assert events[0]["event_id"] == node["event_id"]
    assert events[0]["title"] == "Actual question"
    assert all(source.container_id in argv for argv in commands)
    node["goal_id"] = "different"
    with pytest.raises(ValueError):
        source.read()


def test_manifest_requires_physical_identity():
    raw = dict(id="source", label="Source", container_id="alias", goal_id="goal",
        registry="/registry", runtime_root="/runtime", cli="/cli", python="/python", path="/bin")
    with pytest.raises(ValueError, match="pinned container"):
        ContainerSource.parse(raw)


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
