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


@pytest.mark.parametrize("canonical", [False, True])
def test_ninth_link_readback_pagination_scope_failure_and_recovery(tmp_path, monkeypatch, canonical):
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
    from loopx.todos import add_goal_todo, list_goal_todos
    from loopx.control_plane.todos.contract import encode_metadata_value
    state = tmp_path / "active.md"
    state.write_text("# Synthetic Goal\n\n## Agent Todo\n")
    linked = add_goal_todo(
        registry_path=registry, goal_id="evidence-goal", role="agent",
        text="Test a uniform bound", agent_id="worker", claimed_by="worker",
        explore_result_node_refs=[*[f"prior-{i}" for i in range(8)], "question"],
    )["todo_id"]
    add_goal_todo(
        registry_path=registry, goal_id="evidence-goal", role="agent",
        text="Unrelated task", agent_id="worker", claimed_by="worker",
        explore_result_node_refs=["elsewhere"],
    )
    state.write_text(state.read_text() + (
        "\n## Completed Work Archive\n- [x] Retain the counterexample\n"
        "  <!-- loopx:todo todo_id=todo_archived role=agent status=done "
        "claimed_by=worker explore_result_node_refs=" + encode_metadata_value("question") + " -->\n"
    ))
    if canonical:
        from pathlib import Path
        monkeypatch.syspath_prepend(str(Path(__file__).parent / "control_plane"))
        from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
        from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
        isolate_sqlite_runtime(tmp_path, monkeypatch)
        rows = list_goal_todos(registry_path=registry, goal_id="evidence-goal")["todos"]
        rows += list_goal_todos(registry_path=registry, goal_id="evidence-goal", role="agent",
                               status="done", read_scope="completed_history")["todos"]
        initialize_canonical_authority(runtime, "evidence-goal",
            build_todo_runtime_shadow_projection(goal_id="evidence-goal", todos=rows, handoff_mode="soft_claim"),
            state_path=state, provider="sqlite")
        # The reader must prefer canonical association over stale presentation.
        state.write_text("# Synthetic Goal\n")
    before = log.read_bytes()
    state_before = state.read_bytes()
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root_override = None
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
        if not canonical:
            # Missing Todo authority must not masquerade as an unlinked finding.
            state.unlink()
            with pytest.raises(HTTPError) as missing:
                read(base + "evidence-goal")
            assert missing.value.code == 409
            state.write_bytes(state_before)
            assert len(read(base + "evidence-goal")["items"][0]["linked_todos"]) == 2
            state.write_text("# Synthetic Goal\n")
            assert read(base + "evidence-goal")["items"][0]["linked_todos"] == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
