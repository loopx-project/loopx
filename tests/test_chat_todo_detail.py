"""Exact Task reading, from native CLI through the real loopback HTTP owner."""

import json
from pathlib import Path
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_exact_task_cli_http_preserve_full_current_and_retained_request(tmp_path, monkeypatch, provider):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "control_plane"))
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    tail = " FINAL_ACCEPTANCE: read every requirement and preserve the original opener."
    text = "Inspect the complete current requirements. " + "A" * 880 + tail
    records = [{
        "schema_version": "todo_item_v0", "todo_id": todo_id, "index": index + 1,
        "role": "agent", "status": "done" if archived else "open", "done": archived,
        "archive_state": "archive" if archived else "active", "text": text,
        "source_section": "Completed Work Archive" if archived else "Agent Todo",
        "claimed_by": "agent-a", "task_class": "advancement_task", "priority": "P0",
    } for index, (todo_id, archived) in enumerate([("todo_current", False), ("todo_retained", True)])]
    state, runtime, registry = tmp_path / "state.md", tmp_path / "runtime", tmp_path / "registry.json"
    state.write_text("# Synthetic Goal\n\n## User Todo / Owner Review Reading Queue\n\n## Agent Todo\n\n## Completed Work Archive\n")
    projection = build_todo_runtime_shadow_projection(goal_id="reading-goal", todos=records, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "reading-goal", projection, state_path=state, provider=provider)
    # The authoritative reader must work without a stale display file.
    state.unlink()
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "reading-goal", "repo": str(tmp_path), "state_file": "state.md",
         "coordination": {"registered_agents": ["agent-a"], "agent_model": "peer_v1"}}]}))
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="reading-goal")

    def cli(*args, expected_code=0):
        process = subprocess.run([sys.executable, "-c", "from loopx.cli import main; raise SystemExit(main())",
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "todo", "list", "--goal-id", "reading-goal", *args], capture_output=True, text=True, timeout=60)
        assert process.returncode == expected_code, process.stdout + process.stderr
        return json.loads(process.stdout)

    hot = cli()
    assert len(hot["todos"]) == 1 and len(hot["todos"][0]["text"]) == 500
    assert tail not in hot["todos"][0]["text"]
    for todo_id in ("todo_current", "todo_retained"):
        exact = cli("--todo-id", todo_id)
        assert exact["matched"] and exact["todo"]["text"] == text
        assert tail in exact["todo"]["text"]
        rejected = cli("--todo-id", todo_id, "--thin", expected_code=1)
        assert rejected["ok"] is False
        assert "Exact Todo reads return full requirements" in rejected["error"]
        assert "remove --thin" in rejected["error"]
        assert "FINAL_ACCEPTANCE" not in rejected["error"]

    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = registry, str(runtime), False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}/api/chat/todo/detail"
    try:
        for todo_id, archived in [("todo_current", False), ("todo_retained", True)]:
            with urlopen(f"{base}?goal_id=reading-goal&todo_id={todo_id}") as response:
                detail = json.load(response)
            assert detail["goal_id"] == "reading-goal" and detail["todo_id"] == todo_id
            assert detail["text"] == text and detail["archive_state"] == ("archive" if archived else "active")
            assert "state_file" not in detail and "required_capabilities" not in detail and "authority_read" not in detail
            assert detail["review_basis"]["provider_revision"] == before["provider_revision"]
        for query, expected in [("goal_id=other-goal&todo_id=todo_current", 400),
                                ("goal_id=reading-goal&todo_id=todo_missing", 404),
                                ("goal_id=reading-goal&todo_id=../state.md", 400),
                                ("goal_id=reading-goal&todo_id=todo_current&todo_id=todo_retained", 400)]:
            with pytest.raises(HTTPError) as failure:
                urlopen(f"{base}?{query}")
            assert failure.value.code == expected
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(f"{base}?goal_id=reading-goal&todo_id=todo_current", headers={"Origin": "https://unrelated.example"}))
        assert denied.value.code == 403
        saved = registry.with_suffix(".saved")
        registry.rename(saved)
        with pytest.raises(HTTPError) as unavailable:
            urlopen(f"{base}?goal_id=reading-goal&todo_id=todo_current")
        assert unavailable.value.code == 503
        saved.rename(registry)
        with urlopen(f"{base}?goal_id=reading-goal&todo_id=todo_current") as response:
            assert json.load(response)["text"] == text
        after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="reading-goal")
        assert after["provider_revision"] == before["provider_revision"] and after["todos"] == before["todos"]
        assert not state.exists()

    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_source_bound_requirements_use_real_canonical_writer(tmp_path, monkeypatch, provider):
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "control_plane"))
    from canonical_authority_fixture import promoted_create_fixture, isolate_sqlite_runtime
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService
    from loopx.todos import add_goal_todo
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider, goal_id="reading-goal")
    tail = " FINAL_ACCEPTANCE: preserve every original requirement."
    text = "[P1] Complete current requirements. " + "A" * 900 + tail
    todo_id = add_goal_todo(registry_path=registry, goal_id="reading-goal", role="agent",
        text=text, claimed_by="agent-a", agent_id="agent-a", task_class="advancement_task")["todo_id"]
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="reading-goal")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.action_service = ChatActionService(store=ChatActionStore(runtime / "chat/actions"), registry_path=registry, runtime_root_override=str(runtime))
    server.registry_path, server.runtime_root_override, server.verbose = registry, str(runtime), False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}/api/chat/todo/detail"
    try:
        # The source read, not the bounded list, supplies both text and CAS.
        with urlopen(f"{base}?goal_id=reading-goal&todo_id={todo_id}") as response:
            detail = json.load(response)
        updated_text = text + " Preserve this additional acceptance requirement."
        service = server.action_service
        request = {"action_kind": "todo.update", "summary": "Save task requirements",
            "context": {"kind": "todo", "goal_id": "reading-goal", "todo_id": todo_id},
            "idempotency_key": "save-requirements", "normalized_parameters": {
                "goal_id": "reading-goal", "todo_id": todo_id, "agent_id": "agent-a",
                "operation": "edit", "text": updated_text, "review_basis": detail["review_basis"]}}
        proposal = service.preview(request)
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="reading-goal")["provider_revision"] == before["provider_revision"]
        applied = service.apply(proposal["proposal_id"])["proposal"]
        assert applied["status"] == "applied", json.dumps(applied, ensure_ascii=False)
        with urlopen(f"{base}?goal_id=reading-goal&todo_id={todo_id}") as response:
            saved = json.load(response)
        assert saved["text"] == updated_text and tail in saved["text"]
        assert service.apply(proposal["proposal_id"])["proposal"] == applied
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="reading-goal")["provider_revision"] == saved["review_basis"]["provider_revision"]
        with pytest.raises(ValueError, match="changed"):
            service.preview({**request, "idempotency_key": "stale-source"})
        with pytest.raises(ValueError, match="complete current source basis"):
            service.preview({**request, "idempotency_key": "partial-basis",
                "normalized_parameters": {**request["normalized_parameters"], "review_basis": {"provider_revision": "unknown"}}})
        with pytest.raises(ValueError, match="bounded visible text"):
            service.preview({**request, "idempotency_key": "old-quick-edit-bound",
                "normalized_parameters": {key: value for key, value in request["normalized_parameters"].items() if key != "review_basis"}})
        with pytest.raises(ValueError, match="bounded visible text"):
            service.preview({**request, "idempotency_key": "no-source-truncation",
                "normalized_parameters": {**request["normalized_parameters"],
                    "review_basis": saved["review_basis"], "text": "A" * 8001}})
        current_request = {**request, "idempotency_key": "intervening-writer",
            "normalized_parameters": {**request["normalized_parameters"], "text": updated_text + " A new intent.",
                "review_basis": saved["review_basis"]}}
        pending = service.preview(current_request)
        from loopx.todos import update_goal_todo
        update_goal_todo(registry_path=registry, goal_id="reading-goal", todo_id=todo_id,
            agent_id="agent-a", note="Independent progress")
        assert service.apply(pending["proposal_id"])["proposal"]["status"] == "stale"
        assert service.apply(proposal["proposal_id"])["proposal"] == applied

        with urlopen(f"{base}?goal_id=reading-goal&todo_id={todo_id}") as response:
            assert json.load(response)["text"] == updated_text
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
