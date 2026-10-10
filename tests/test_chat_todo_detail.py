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
    state.write_text("# Synthetic Goal\n\n## Agent Todo\n")
    projection = build_todo_runtime_shadow_projection(goal_id="reading-goal", todos=records, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "reading-goal", projection, state_path=state, provider=provider)
    # The authoritative reader must work without a stale display file.
    state.unlink()
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "reading-goal", "repo": str(tmp_path), "state_file": "state.md"}]}))
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="reading-goal")

    def cli(*args, expected_exit=0):
        process = subprocess.run([sys.executable, "-c", "from loopx.cli import main; raise SystemExit(main())",
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "todo", "list", "--goal-id", "reading-goal", *args], capture_output=True, text=True, timeout=60)
        assert process.returncode == expected_exit, process.stdout + process.stderr
        return json.loads(process.stdout)

    hot = cli()
    assert len(hot["todos"]) == 1 and len(hot["todos"][0]["text"]) == 500
    assert tail not in hot["todos"][0]["text"]
    for todo_id in ("todo_current", "todo_retained"):
        exact = cli("--todo-id", todo_id)
        assert exact["matched"] and exact["todo"]["text"] == text
        assert tail in exact["todo"]["text"]
        thin_exact = cli("--todo-id", todo_id, "--thin", expected_exit=1)
        assert "remove --thin" in thin_exact["error"]

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
