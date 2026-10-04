import json
import threading
from urllib.request import urlopen
from urllib.request import Request
from urllib.error import HTTPError

import pytest

from loopx.chat_completed_todos import CompletedTodoPages, _verify_goal_result_page
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.todos.contract import encode_metadata_value


def test_history_uses_the_same_readback_owner_through_the_compatibility_facade():
    from loopx.control_plane.todos.list_readback import list_goal_todos as readback
    from loopx.todos import list_goal_todos as facade

    assert facade is readback


def test_snapshot_pagination_is_bounded_and_stable():
    pages = CompletedTodoPages()
    rows = [{"todo_id": f"todo_{index}"} for index in range(4087)]
    calls = []
    def load():
        calls.append(True)
        return list(rows)
    result = pages.page(scope=("goal", "all"), cursor="", load=load)
    found = list(result["items"])
    rows.insert(0, {"todo_id": "todo_new"})
    while result["next_cursor"]:
        assert len(result["items"]) <= 40
        result = pages.page(scope=("goal", "all"), cursor=result["next_cursor"], load=load)
        found.extend(result["items"])
    assert len(found) == len({row["todo_id"] for row in found}) == 4087
    assert len(calls) == 1


def test_cursor_scope_expiry_and_capacity():
    pages = CompletedTodoPages()

    def load():
        return [{}] * 41

    cursor = pages.page(scope="a", cursor="", load=load)["next_cursor"]
    with pytest.raises(ValueError, match="expired"):
        pages.page(scope="b", cursor=cursor, load=load)
    for index in range(9):
        pages.page(scope=index, cursor="", load=load)
    assert len(pages._snapshots) == 8
    with pytest.raises(ValueError, match="expired"):
        pages.page(scope="a", cursor=cursor, load=load)
    cursor = pages.page(scope="a", cursor="", load=load)["next_cursor"]
    pages.ttl_seconds = 0
    with pytest.raises(ValueError, match="expired"):
        pages.page(scope="a", cursor=cursor, load=load)


def test_snapshot_byte_budget_is_enforced():
    pages = CompletedTodoPages()
    pages.max_cache_bytes = 10
    with pytest.raises(ValueError, match="too_large"):
        pages.page(scope="a", cursor="", load=lambda: [{"text": "x" * 100}])
    assert not pages._snapshots


def test_goal_report_verification_is_bounded_by_requested_page(monkeypatch):
    calls = []
    def read(**kwargs):
        calls.append(kwargs["todo_id"])
        return {"result": {"sha256": "a" * 64, "producer_agent_id": "lead",
                           "content_type": "text/markdown", "size_bytes": 12}}
    monkeypatch.setattr("loopx.control_plane.todos.completion_result.read_completion_result", read)
    pages = CompletedTodoPages()
    rows = [{"todo_id": f"todo_report_{index}", "title": "Report",
             "sha256": "a" * 64, "producer_agent_id": "lead", "completed_at": None}
            for index in range(85)]
    first = pages.page(scope=("accepted_goal_results", "goal"), cursor="", load=lambda: rows)
    verified = _verify_goal_result_page(page=first, registry_path=None, runtime_root=None, goal_id="goal")
    assert len(verified["items"]) == len(calls) == 40
    assert verified["next_cursor"]
    assert verified["unavailable_count"] == 0
    assert verified["unavailable_todo_ids"] == []


def test_goal_report_page_names_unavailable_rows_instead_of_hiding_the_rest(monkeypatch):
    def read(**kwargs):
        if kwargs["todo_id"] == "todo_report_stale":
            raise ValueError("completion result acceptance basis is stale")
        return {"result": {"sha256": "a" * 64, "producer_agent_id": "lead",
                           "content_type": "text/markdown", "size_bytes": 12}}
    monkeypatch.setattr("loopx.control_plane.todos.completion_result.read_completion_result", read)
    pages = CompletedTodoPages()
    rows = [{"todo_id": todo_id, "title": "Report", "sha256": "a" * 64,
             "producer_agent_id": "lead", "completed_at": None}
            for todo_id in ("todo_report_stale", "todo_report_planned")]
    page = pages.page(scope=("accepted_goal_results", "goal"), cursor="", load=lambda: rows)
    verified = _verify_goal_result_page(page=page, registry_path=None, runtime_root=None, goal_id="goal")
    # An unrelated unreadable report is named, not turned into a whole-page failure,
    # so a reader that only needs its own Todo ids can still resolve them.
    assert verified["unavailable_count"] == 1
    assert verified["unavailable_todo_ids"] == ["todo_report_stale"]
    assert [row["todo_id"] for row in verified["items"]] == ["todo_report_planned"]


@pytest.mark.parametrize("count", [85, 4087])
@pytest.mark.parametrize("archived", [False, True])
def test_http_history_reads_real_markdown_without_writes(tmp_path, count, archived):
    state = tmp_path / "active.md"
    text = f"Inspect {tmp_path}/results.txt " + " ".join(["complete task description"] * 25)
    evidence = f"Verified output at {tmp_path}/results.txt"
    state.write_text("# Synthetic Goal\n\n## Agent Todo\n" + "\n".join(
        f"- [x] {text}{index}\n  <!-- loopx:todo todo_id=todo_history_{index} status=done task_class=advancement_task note={encode_metadata_value(evidence)} -->" for index in range(count)
    ) + "\n\n## User Todo\n", encoding="utf-8")
    if archived:
        from loopx.control_plane.todos.completed_archive import archive_completed_todo_lines
        if count == 85:
            result = archive_completed_todo_lines(state.read_text().splitlines(), max_active_done=1)
            assert result["moved_count"] == count - 1
            state.write_text("\n".join(result["lines"]) + "\n", encoding="utf-8")
        else:
            # A pre-existing large archive tests history reads independently of
            # the archive writer's per-request batch budget.
            lines = state.read_text().splitlines()
            first = next(index for index, line in enumerate(lines) if line.startswith("- [x]"))
            lines.insert(first + 2, "\n## Completed Work Archive\n")
            state.write_text("\n".join(lines).replace("status=done ", "role=agent status=done "))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"runtime_root": str(tmp_path / "runtime"), "goals": [{"id": "history-goal", "repo": str(tmp_path), "state_file": "active.md"}]}))
    before = state.read_bytes()
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root_override = None
    server.verbose = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/chat/completed-todos?goal_id=history-goal"
        with urlopen(url) as response:
            page = json.load(response)
        assert page["total"] == count
        assert len(page["items"]) == 40
        assert page["next_cursor"]
        assert page["items"][0]["text"].startswith(text)
        assert len(page["items"][0]["text"]) > 600
        assert page["items"][0]["evidence"] == evidence
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(url, headers={"Origin": "https://unrelated.example"}))
        assert denied.value.code == 403
        ids = [row["todo_id"] for row in page["items"]]
        assert all(row["text"] == text + row["todo_id"].removeprefix("todo_history_") for row in page["items"])
        from urllib.parse import quote
        while page["next_cursor"]:
            with urlopen(url + "&cursor=" + quote(page["next_cursor"])) as response:
                page = json.load(response)
            ids.extend(row["todo_id"] for row in page["items"])
            assert all(row["text"] == text + row["todo_id"].removeprefix("todo_history_") for row in page["items"])
        assert len(ids) == len(set(ids)) == count
        from loopx.todos import list_goal_todos
        active = list_goal_todos(registry_path=registry, goal_id="history-goal", role="agent", status="done")
        assert active["todo_count"] == (1 if archived else count)
        assert state.read_bytes() == before
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_history_includes_archives_without_widening_active_lists(tmp_path, monkeypatch, provider):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "control_plane"))
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    from loopx.todos import list_goal_todos

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    state = tmp_path / "state.md"
    state.write_text("# Synthetic Goal\n\n## Agent Todo\n\n## Completed Work Archive\n")
    runtime = tmp_path / "runtime"
    records = [
        {"schema_version": "todo_item_v0", "todo_id": f"todo_history_{index}",
         "index": index + 1, "role": "agent", "status": "done", "done": True,
         "text": f"Completed task {index}: " + " ".join(["Full retained task description"] * 20),
         "archive_state": "archive" if index < 84 else "active",
         "source_section": "Completed Work Archive" if index < 84 else "Agent Todo",
         "task_class": "advancement_task", "claimed_by": "agent-a" if index % 2 else "agent-b",
         "priority": "P2", "required_capabilities": ["local-fixture-capability"],
         "note": "Retain full evidence " + "verified " * 50,
         "completed_at": f"2026-01-{index % 28 + 1:02d}T00:00:00Z"}
        for index in range(85)
    ]
    records[0].update({
        "task_domain": "quality",
        "resume_when": "todo_done:todo_history_84",
        "completion_validation_sha256": "a" * 64,
        "completion_validation_revision": 3,
        "completion_validation_revision_history": [{
            "revision": 3, "previous_declaration_sha256": "b" * 64,
            "declaration_sha256": "a" * 64, "actor_agent_id": "agent-a",
            "revised_at": "2026-01-01T00:00:00Z",
        }],
    })
    records.extend([
        {**records[-1], "todo_id": "todo_monitor", "task_class": "continuous_monitor"},
        {**records[-1], "todo_id": "todo_open", "status": "open", "done": False},
        {**records[-1], "todo_id": "todo_deferred", "status": "deferred"},
        {**records[-1], "todo_id": "todo_user", "role": "user", "task_class": "user_action"},
    ])
    projection = build_todo_runtime_shadow_projection(goal_id="history-goal", todos=records, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "history-goal", projection, state_path=state, provider=provider)
    # A canonical history reader must not fall back to a stale display file.
    state.unlink()
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "history-goal", "repo": str(tmp_path), "state_file": "state.md"}]}))
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="history-goal")
    active = list_goal_todos(registry_path=registry, goal_id="history-goal", role="agent", status="done")
    assert {row["todo_id"] for row in active["todos"]} == {"todo_history_84", "todo_monitor"}
    assert all(len(row["text"]) <= 500 and row["text"].endswith("…") for row in active["todos"])
    exact = list_goal_todos(registry_path=registry, goal_id="history-goal", todo_id="todo_history_0")
    assert exact["todos"][0]["archive_state"] == "archive"
    with pytest.raises(ValueError, match="requires role=agent and status=done"):
        list_goal_todos(registry_path=registry, goal_id="history-goal", read_scope="completed_history")
    with pytest.raises(ValueError, match="read_scope must be"):
        list_goal_todos(registry_path=registry, goal_id="history-goal", read_scope="unknown")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = registry, runtime, False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        from urllib.parse import quote
        base = f"http://127.0.0.1:{server.server_port}/api/chat/completed-todos?goal_id=history-goal"
        for suffix, expected in [("", 85), ("&agent_id=agent-a", 42)]:
            cursor, rows = "", []
            while True:
                with urlopen(base + suffix + "&cursor=" + quote(cursor)) as response:
                    page = json.load(response)
                assert page["total"] == expected
                assert len(page["items"]) <= 40
                rows.extend(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    break
            assert len(rows) == len({row["todo_id"] for row in rows}) == expected
            assert all(row["evidence"] == records[0]["note"] for row in rows)
            assert all(row["priority"] == "P2" for row in rows)
            source_by_id = {record["todo_id"]: record for record in records}
            assert all(row["text"] == source_by_id[row["todo_id"]]["text"] for row in rows)
            assert all(row["completed_at"] == source_by_id[row["todo_id"]]["completed_at"] for row in rows)
            assert all(row["done"] is True and row["status"] == "done" for row in rows)
            # Existing inspector facts travel with their source, while private
            # execution declarations and capability metadata remain excluded.
            assert all("required_capabilities" not in row and "completion_validation" not in row for row in rows)
            if not suffix:
                detail = next(row for row in rows if row["todo_id"] == "todo_history_0")
                assert detail["task_domain"] == "quality"
                assert detail["resume_when"] == "todo_done:todo_history_84"
                assert detail["resume_ready"] is True
                assert detail["completion_validation_sha256"] == "a" * 64
                assert detail["completion_validation_revision"] == 3
                assert detail["completion_validation_revision_history"] == records[0]["completion_validation_revision_history"]
            if suffix:
                assert all(row["claimed_by"] == "agent-a" for row in rows)
        after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="history-goal")
        assert after["provider_revision"] == before["provider_revision"]
        assert after["todos"] == before["todos"]
        assert not state.exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
