"""Real HTTP readback of saved Explore evidence without worker authority."""
import json
import threading
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_finding_event, build_explore_node_event,
    explore_result_log_path,
)
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.todos.contract import (
    encode_metadata_value,
    format_todo_metadata_line,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("separate_chat_store", [False, True])
def test_ninth_link_readback_pagination_scope_failure_and_recovery(tmp_path, monkeypatch, provider, separate_chat_store):
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    (tmp_path / "active.md").write_text("# Synthetic Goal\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": name, "repo": str(tmp_path), "state_file": "active.md",
         "coordination": {"registered_agents": ["worker"]}}
        for name in ("evidence-goal", "other-goal")
    ]}))
    log = explore_result_log_path(runtime, "evidence-goal")
    append_explore_result_event(log, build_explore_node_event(
        goal_id="evidence-goal", node_id="question", title="Bounded input hypothesis",
        node_kind="question", summary="Inputs with a finite prefix",
    ))
    for index in range(41):
        append_explore_result_event(log, build_explore_finding_event(
            goal_id="evidence-goal", node_id="question", finding_id=f"finding_{index}",
            title=f"Observation {index}", status="refuted",
            summary="Counterexample at input revision A " + "c" * 1700 + "; do not transfer beyond the tested scope.",
            evidence_refs=["artifact:counterexample"], agent_id="worker",
        ))
    state = tmp_path / "active.md"
    linked = "todo_linked0001"
    linked_metadata = format_todo_metadata_line(
        todo_id=linked,
        status="open",
        claimed_by="worker",
        explore_result_node_refs=[*[f"prior-{i}" for i in range(8)], "question"],
    )
    unrelated_metadata = format_todo_metadata_line(
        todo_id="todo_unrelated0001",
        status="open",
        claimed_by="worker",
        explore_result_node_refs=["elsewhere"],
    )
    state.write_text(
        "# Synthetic Goal\n\n"
        "## Agent Todo\n\n"
        "- [ ] Test a uniform bound\n"
        f"{linked_metadata}\n"
        "- [ ] Unrelated task\n"
        f"{unrelated_metadata}\n\n"
        "## Completed Work Archive\n"
        "- [x] Retain the counterexample\n"
        "  <!-- loopx:todo todo_id=todo_archived role=agent status=done "
        "claimed_by=worker explore_result_node_refs="
        + encode_metadata_value("question")
        + " -->\n",
        encoding="utf-8",
    )
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    active, archived, _ = parse_todo_source(state.read_text())
    rows = active['agent'] + archived
    for row in rows:
        row['schema_version'] = 'todo_item_v0'
    for goal in ('evidence-goal', 'other-goal'):
        initialize_canonical_authority(runtime, goal,
            build_todo_runtime_shadow_projection(goal_id=goal,
                todos=rows if goal == 'evidence-goal' else [], handoff_mode='soft_claim'),
            state_path=state, provider=provider)
    # Canonical associations survive a stale presentation document.
    state.write_text("# Synthetic Goal\n")
    before = log.read_bytes()
    state_before = state.read_bytes()
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root_override = str(tmp_path / "private-chat") if separate_chat_store else None
    server.runtime_root = tmp_path / "private-chat" if separate_chat_store else runtime
    server.verbose = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}/api/chat/explore-results?goal_id="
    def read(url):
        with urlopen(url) as response:
            return json.load(response)
    try:
        page = read(base + "evidence-goal")
        assert page["total"] == 41 and len(page["items"]) == 40
        history = read(base + "evidence-goal&view=history")
        assert history["total"] == 42 and len(history["items"]) == 40
        item = page["items"][0]
        assert item["scope"] == "Inputs with a finite prefix"
        assert item["status"] == "refuted"
        assert len(item["summary"]) > 1200
        assert item["summary"].endswith("do not transfer beyond the tested scope.")
        assert item["evidence_refs"] == ["artifact:counterexample"]
        assert item["agent_id"] == "worker"
        assert {todo["todo_id"] for todo in item["linked_todos"]} == {linked, "todo_archived"}
        assert {todo["status"] for todo in item["linked_todos"]} == {"open", "done"}
        assert all(set(todo) == {"todo_id", "text", "status", "claimed_by"} for todo in item["linked_todos"])
        assert state.read_bytes() == state_before
        cursor = quote(page["next_cursor"])
        assert len(read(base + "evidence-goal&cursor=" + cursor)["items"]) == 1
        with pytest.raises(HTTPError) as denied:
            read(base + "other-goal&cursor=" + cursor)
        assert denied.value.code == 409
        with pytest.raises(HTTPError) as denied:
            read(Request(base + "evidence-goal", headers={"Origin": "https://unrelated.example"}))
        assert denied.value.code == 403
        assert read(base + "other-goal")["items"] == []
        assert log.read_bytes() == before  # No adoption/writeback invented by reads.
        log.write_bytes(before + b'{broken\n')
        with pytest.raises(HTTPError) as corrupt:
            read(base + "evidence-goal")
        assert corrupt.value.code == 409
        assert str(tmp_path).encode() not in corrupt.value.read()
        log.write_bytes(before)
        assert read(base + "evidence-goal")["total"] == 41
        state.unlink()
        assert len(read(base + "evidence-goal")["items"][0]["linked_todos"]) == 2
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("unavailable_scope", [None, "active", "completed_history"])
def test_saved_findings_survive_unavailable_task_associations(tmp_path, monkeypatch, unavailable_scope):
    from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.presentation import explore_results_api
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    state = tmp_path / "active.md"
    state.write_text("# Synthetic saved evidence\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "saved-evidence", "repo": str(tmp_path), "state_file": state.name},
    ]}))
    log = explore_result_log_path(runtime, "saved-evidence")
    append_explore_result_event(log, build_explore_node_event(
        goal_id="saved-evidence", node_id="question", title="Which bound survives?",
    ))
    append_explore_result_event(log, build_explore_finding_event(
        goal_id="saved-evidence", node_id="question", finding_id="counterexample",
        title="The proposed bound fails", status="refuted", summary="Retain the complete counterexample.",
        evidence_refs=["artifact:counterexample"],
    ))

    def migrate():
        initialize_canonical_authority(runtime, "saved-evidence", build_todo_runtime_shadow_projection(
            goal_id="saved-evidence", todos=[{
                "schema_version": "todo_item_v0", "todo_id": "todo_recheck", "role": "agent",
                "text": "Recheck the bound", "status": "open", "claimed_by": "worker",
                "done": False, "archive_state": "active", "source_section": "Agent Todo",
                "explore_result_node_refs": ["question"],
            }], handoff_mode="soft_claim"), state_path=state, provider="sqlite")

    original_read = explore_results_api.list_goal_todos
    if unavailable_scope is not None:
        migrate()

        def interrupted_read(**kwargs):
            if kwargs.get("read_scope", "active") == unavailable_scope:
                raise LocalCoordinationAuthorityUnavailable(
                    "Synthetic authority outage", code="local_authority_unavailable", payload={},
                )
            return original_read(**kwargs)

        monkeypatch.setattr(explore_results_api, "list_goal_todos", interrupted_read)
    before = {path: path.read_bytes() for path in (registry, state, log)}
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.runtime_root_override = registry, runtime, None
    server.verbose = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/api/chat/explore-results?goal_id=saved-evidence"

    def read():
        with urlopen(url, timeout=10) as response:
            return json.load(response)

    try:
        page = read()
        assert page["total"] == 1
        item = page["items"][0]
        assert item["summary"] == "Retain the complete counterexample."
        assert item["status"] == "refuted"
        assert item["evidence_refs"] == ["artifact:counterexample"]
        assert item["linked_todos_available"] is False
        assert item["linked_todos"] == []  # An incomplete association read is never an empty verified set.
        assert all(path.read_bytes() == content for path, content in before.items())
        log.write_bytes(before[log] + b"{broken\n")
        with pytest.raises(HTTPError) as corrupt:
            read()
        assert corrupt.value.code == 409  # Task availability cannot relax evidence validation.
        log.write_bytes(before[log])
        monkeypatch.setattr(explore_results_api, "list_goal_todos", original_read)
        if unavailable_scope is None:
            migrate()
        recovered = read()["items"][0]
        assert recovered.get("linked_todos_available", True) is True
        assert [todo["todo_id"] for todo in recovered["linked_todos"]] == ["todo_recheck"]
        assert all(path.read_bytes() == content for path, content in before.items())
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
