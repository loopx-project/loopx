"""Declared Goal content survives the real status/HTTP path without a contract."""

from __future__ import annotations

import http.client
import json
import runpy
import threading
from pathlib import Path

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.status import active_state_todo_fields

# Reuse the provider fixture across test directories without duplicating a
# File/SQLite initializer or depending on pytest's collection/import order.
_providers = runpy.run_path(
    str(Path(__file__).parents[1] / "control_plane/canonical_authority_fixture.py")
)


@pytest.fixture(params=["file", "sqlite"])
def objective_source(tmp_path, monkeypatch, request):
    _providers["isolate_sqlite_runtime"](tmp_path, monkeypatch)
    state = tmp_path / "state.md"
    state.write_text(
        '---\nobjective: "Answer the original research question."\n---\n\n# Goal\n'
    )
    runtime = tmp_path / "runtime"
    goal = {
        "id": "research",
        "display_name": "A different display title",
        "repo": str(tmp_path),
        "state_file": "state.md",
        "adapter": {"kind": "read_only_project_map_v0"},
    }
    projection = build_todo_runtime_shadow_projection(goal_id="research", todos=[])
    _providers["initialize_canonical_authority"](
        runtime, "research", projection, state_path=state, provider=request.param
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {"schema_version": 1, "common_runtime_root": str(runtime), "goals": [goal]}
        )
    )
    return goal, registry, runtime, state


def test_declared_objective_is_not_an_acceptance_contract_or_task(objective_source):
    goal, _, runtime, state = objective_source
    assert (
        active_state_todo_fields(goal, runtime_root=runtime)["goal_objective"]
        == "Answer the original research question."
    )
    for content in [
        "# Goal\n## Next Action\nInfer nothing from this progress.\n",
        '---\nobjective: ""\n---\n',
    ]:
        state.write_text(content)
        assert (
            active_state_todo_fields(goal, runtime_root=runtime)["goal_objective"]
            is None
        )
    state.write_text('---\nobjective: "' + "Declared text. " * 200 + '"\n---\n')
    assert (
        len(active_state_todo_fields(goal, runtime_root=runtime)["goal_objective"])
        <= 1000
    )
    state.unlink()
    assert (
        active_state_todo_fields(goal, runtime_root=runtime)["goal_objective"] is None
    )


def test_real_status_http_keeps_an_empty_goals_declared_objective(objective_source):
    _, registry, runtime, state = objective_source
    before = registry.read_bytes(), state.read_bytes()
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.runtime_root_override = (
        registry,
        runtime,
        str(runtime),
    )
    server.selected_goal_id, server.scan_roots, server.limit, server.verbose = (
        None,
        [],
        10,
        False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=45)
        connection.request("GET", "/status.json?goal_id=research")
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        assert response.status == 200, payload
        assert (
            payload["run_history"]["goals"][0]["goal_objective"]
            == "Answer the original research question."
        )
        assert (registry.read_bytes(), state.read_bytes()) == before
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
