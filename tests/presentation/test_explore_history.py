"""Real loopback reads: unfolded history, stable pagination and no writes."""
import hashlib
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import pytest

from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_node_event, build_explore_finding_event,
    explore_result_log_path,
)
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler


@pytest.mark.parametrize("name,digest,size", [
    ("edgebench-research-history.json", "b876dd7660e0aab19db717f60e86865494a74b8fa27b42a5a67ee295ef524397", 104922),
    ("edgebench-graph-checkpoint-history.json", "b105de360ed7d015ae5d7bf96b545879e12507b1fc97eaeb5fb6fa2c0bbc00ab", 200333),
    ("edgebench-ad-checkpoint-history.json", "de04b679e9f382b650e891e5c7dd7146bc690500435c511f107c9d24da4f4efa", 145296),
])
def test_checkpoint_fixture_reconstructs_original_captured_log_bytes(name, digest, size):
    fixture = Path(__file__).resolve().parents[2] / "apps/presentation/dashboard/src/data/fixtures" / name
    capture = json.loads(fixture.read_text())
    original = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        for event in capture["events"]).encode()
    # Frozen independently from the source JSONL, not generated from the fixture.
    assert hashlib.sha256(original).hexdigest() == digest
    assert len(original) == size
    assert capture["provenance"]["redactions"] == []


def test_history_keeps_updates_in_snapshot_isolates_goal_and_view_and_never_writes(tmp_path):
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    state = tmp_path / "active.md"
    state.write_text("# Synthetic read-only research\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": name, "repo": str(tmp_path), "state_file": "active.md"}
        for name in ("history-fixture", "other-goal")
    ]}))
    log = explore_result_log_path(runtime, "history-fixture")
    for hour in range(19):
        append_explore_result_event(log, build_explore_node_event(
            goal_id="history-fixture", node_id="route", title="Recorded route",
            node_kind="question", status="dead_end" if hour == 8 else "exploring",
            summary=f"Scoped observation {hour}", evidence_refs=[f"artifact:input-v{hour}"],
            recorded_at=f"2026-01-01T{hour:02d}:00:00Z",
        ))
        for index in range(3):
            append_explore_result_event(log, build_explore_finding_event(
                goal_id="history-fixture", node_id="route", finding_id=f"finding-{hour}-{index}",
                title="No native improvement", status="refuted", summary="A negative result remains negative.",
                recorded_at=f"2026-01-01T{hour:02d}:30:00Z",
            ))
    before = {file: file.read_bytes() for file in (registry, state, log)}
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = registry, None, False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}/api/chat/explore-results?goal_id="
    def read(query):
        with urlopen(base + query) as response:
            return json.load(response)
    try:
        first = read("history-fixture&view=history")
        assert first["total"] == 76 and len(first["items"]) == 40
        cursor = quote(first["next_cursor"])
        for query in (f"other-goal&view=history&cursor={cursor}", f"history-fixture&cursor={cursor}"):
            with pytest.raises(HTTPError) as caught:
                read(query)
            assert caught.value.code == 409
        assert read("other-goal&view=history")["items"] == []
        second = read(f"history-fixture&view=history&cursor={cursor}")
        rows = first["items"] + second["items"]
        assert second["next_cursor"] is None and len(rows) == 76
        assert rows[0]["recorded_at"] == "2026-01-01T00:00:00Z"
        assert rows[-1]["recorded_at"] == "2026-01-01T18:30:00Z"
        assert len([row for row in rows if row["event_kind"] == "node"]) == 19
        assert any(row.get("status") == "dead_end" for row in rows)
        assert all("research_observation" not in row and "boundary" not in row for row in rows)
        # Existing callers still receive folded findings, never history rows.
        assert read("history-fixture")["total"] == 57
        assert all(row["finding_id"] for row in read("history-fixture")["items"])
        for file, content in before.items():
            assert file.read_bytes() == content
        with pytest.raises(HTTPError) as caught:
            read("history-fixture&view=unknown")
        assert caught.value.code == 400
        # Frozen next-page reads survive source growth; a fresh read sees it.
        append_explore_result_event(log, build_explore_node_event(
            goal_id="history-fixture", node_id="tail", title="Late record", recorded_at="2026-01-01T20:00:00Z"))
        assert read(f"history-fixture&view=history&cursor={cursor}")["total"] == 76
        assert read("history-fixture&view=history")["total"] == 77
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
