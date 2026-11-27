"""Real HTTP readback of saved Explore evidence without worker authority."""
import json
from pathlib import Path
from runpy import run_path
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
        node_kind="question", summary="Inputs with a finite prefix", recorded_at="2026-01-02T08:00:00+08:00",
    ))
    for index in range(41):
        append_explore_result_event(log, build_explore_finding_event(
            goal_id="evidence-goal", node_id="question", finding_id=f"finding_{index}",
            title=f"Observation {index}", status="refuted",
            recorded_at=(
                "2026-02-01T12:00:00" if index == 0
                else "2026-01-03T00:45:00Z" if index == 40 else "2026-01-02T00:30:00Z"
            ),
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
        timing_url = base + "evidence-goal&view=history-timing"
        assert read(timing_url) == {
            "ok": True, "goal_id": "evidence-goal", "source_namespace": None,
            "first_recorded_at": "2026-01-02T00:00:00Z",
            "last_recorded_at": "2026-01-03T00:45:00Z",
            "untimed_record_count": 1, "strategy_recorded_at": None,
        }
        empty = read(base + "other-goal&view=history-timing")
        assert empty["first_recorded_at"] is None and empty["last_recorded_at"] is None
        assert empty["untimed_record_count"] == 0
        with pytest.raises(HTTPError) as no_pages:
            read(timing_url + "&cursor=stale")
        assert no_pages.value.code == 400
        with pytest.raises(HTTPError) as denied_timing:
            read(Request(timing_url, headers={"Origin": "https://unrelated.example"}))
        assert denied_timing.value.code == 403
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
        with pytest.raises(HTTPError) as corrupt_timing:
            read(timing_url)
        assert corrupt_timing.value.code == 409
        log.write_bytes(before)
        assert read(base + "evidence-goal")["total"] == 41
        assert read(timing_url)["last_recorded_at"] == "2026-01-03T00:45:00Z"
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


@pytest.mark.parametrize("use_local_snapshot", [False, True], ids=["inline", "local-snapshot"])
def test_populated_history_preserves_pages_and_original_comparisons(tmp_path, monkeypatch, use_local_snapshot):
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.work_items import yardstick_decision as transport

    fixture_path = Path(__file__).resolve().parents[1] / "apps/presentation/dashboard/smoke/research-verification-fixture.py"
    fixture = run_path(str(fixture_path))
    goal_id = fixture["GOAL"]
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = (tmp_path / name for name in ("runtime", "registry.json", "active.md"))
    state.write_text("# Synthetic research history\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": goal_id, "repo": str(tmp_path), "state_file": state.name,
         "coordination": {"registered_agents": ["worker"]}},
    ]}))
    initialize_canonical_authority(runtime, goal_id,
        build_todo_runtime_shadow_projection(goal_id=goal_id, todos=[], handoff_mode="soft_claim"),
        state_path=state, provider="sqlite")
    events = fixture["populated_history"]()
    log = explore_result_log_path(runtime, goal_id)
    before = b"".join(json.dumps(event).encode() + b"\n" for event in events)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(before)

    invoke, bytes_sent = transport.effect_runtime_projection_result, []

    def observe(operation, params):
        bytes_sent.append(len(json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode()))
        return invoke(operation, params)

    monkeypatch.setattr(transport, "effect_runtime_projection_result", observe)
    invocations = []
    if use_local_snapshot:
        invoke_effect = effect_runtime.effect_runtime_result

        def observe_effect(operation, params, **kwargs):
            if operation == "work_item.yardstick.presentation":
                invocations.append(kwargs.get("large_local_snapshot", False))
            return invoke_effect(operation, params, **kwargs)

        monkeypatch.setattr(effect_runtime, "MAX_REQUEST_BYTES", 128 * 1024)
        monkeypatch.setattr(effect_runtime, "effect_runtime_result", observe_effect)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.runtime_root = registry, str(runtime), runtime
    server.verbose = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/api/chat/explore-results?goal_id={goal_id}&view=history"
    try:
        cursor, items = "", []
        for size in (40, 40, 40, 31):
            with urlopen(url + "&cursor=" + quote(cursor), timeout=20) as response:
                page = json.load(response)
            assert page["total"] == 151 and len(page["items"]) == size
            items.extend(page["items"])
            cursor = page["next_cursor"]
            assert bool(cursor) == (len(items) < 151)
        assert [row["event_id"] for row in items] == [event["event_id"] for event in events]
        assert len(bytes_sent) == 1 and bytes_sent[0] < 1024 * 1024
        if use_local_snapshot:
            assert invocations == [False, True]
        candidates = [row for row in items if "work_yardstick" in row]
        assert len(candidates) == 75
        for attempt, row in enumerate(candidates):
            assert set(row["work_yardstick"]) == {"decision", "binding"}
            if attempt == 31:
                assert "yardstick_measurements" not in row
                continue
            pair = row["yardstick_measurements"]
            assert [item["value"] for item in pair] == [9, 5, 4, 8]
            assert pair[0]["evidence_ref"] == pair[2]["evidence_ref"] == f"artifact:baseline-{attempt}"
        assert log.read_bytes() == before
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_server_source_snapshot_keeps_legacy_capture_wire_shape(tmp_path, provider, monkeypatch):
    from tests.control_plane.test_work_yardstick_cli import (
        setup, attachment, refresh, GOAL_ID, AGENT_ID, TODO_ID,
    )
    project, runtime, registry = setup(tmp_path, active=True, provider=provider)
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(attachment()))
    receipt = refresh(project, runtime, registry, "presentation-source", source)
    assert receipt["explore_result_delivery"]["ok"]
    log = explore_result_log_path(runtime, GOAL_ID)
    original = log.read_bytes()
    records = [json.loads(line) for line in original.splitlines()]
    capture = next(row["work_yardstick"] for row in records if "work_yardstick" in row)
    assert "source_snapshot" in capture
    append_explore_result_event(log, build_explore_finding_event(goal_id=GOAL_ID, agent_id=AGENT_ID,
        node_id=receipt["explore_result_delivery"]["node_id"], finding_id="finding-presentation-2",
        title="Another captured declaration", evidence_refs=attachment()["evidence_refs"], work_yardstick=capture))
    original = log.read_bytes()
    import loopx.control_plane.work_items.yardstick_decision as transport
    invoke, batches = transport.effect_runtime_projection_result, []
    def observe(operation, params):
        if operation == "work_item.yardstick.presentation":
            batches.append(len(params["captures"]))
        return invoke(operation, params)
    monkeypatch.setattr(transport, "effect_runtime_projection_result", observe)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.runtime_root = registry, str(runtime), runtime
    server.verbose = False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}/api/chat/explore-results?goal_id={GOAL_ID}"
    try:
        for suffix in ("", "&view=history"):
            previous = len(batches)
            with urlopen(base + suffix) as response:
                page = json.load(response)
            assert batches[previous:] == [2]
            public = next(row for row in page["items"] if "work_yardstick" in row)
            assert all(set(row["work_yardstick"]) == {"decision", "binding"}
                for row in page["items"] if "work_yardstick" in row)
            # Installed readers decode precisely these two capture fields.
            assert set(public["work_yardstick"]) == {"decision", "binding"}
            assert public["work_yardstick"] == {key: capture[key] for key in ("decision", "binding")}
            assert public["work_yardstick"]["binding"]["todo_id"] == TODO_ID
            assert public["work_yardstick"]["binding"]["agent_id"] == AGENT_ID
            assert public["evidence_refs"] == attachment()["evidence_refs"]
        assert log.read_bytes() == original
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_execution_feedback_survives_http_and_lark_readback_after_disable(tmp_path, monkeypatch, provider):
    """Recorded method deviations remain visible without certifying benefit."""
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.extensions.lark.presentation.explore_record_values import _finding_record_values

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    state = tmp_path / "active.md"
    state.write_text("# Read-only feedback\n")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "feedback", "repo": str(tmp_path), "state_file": state.name},
    ]}))
    initialize_canonical_authority(runtime, "feedback", build_todo_runtime_shadow_projection(
        goal_id="feedback", todos=[], handoff_mode="soft_claim"), state_path=state, provider=provider)
    observation = {
        "schema_version": "explore_execution_observation_v0", "candidate_sha256": "a" * 64,
        "input_revision": "input-v1", "planned_method_id": "broad-search",
        "executed_method_id": "targeted-check", "change_reason": "A counterexample ruled out broad search.",
        "elapsed_seconds": 12.5, "limit_seconds": 60, "search_iterations": None, "stop_reason": None,
        "evidence_refs": ["artifact:negative-result"], "result_ref": "artifact:negative-result",
        "result_candidate_sha256": "a" * 64,
    }
    log = explore_result_log_path(runtime, "feedback")
    append_explore_result_event(log, build_explore_node_event(
        goal_id="feedback", node_id="choice", title="Method choice"))
    for finding_id, observed in (("plain", None), ("changed", observation)):
        append_explore_result_event(log, build_explore_finding_event(
            goal_id="feedback", node_id="choice", finding_id=finding_id,
            title="Recorded negative result", summary="No measured improvement.", status="refuted",
            execution_observation=observed, evidence_refs=["artifact:negative-result"]))
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected
    with pytest.raises(EffectRuntimeRejected, match="executed candidate"):
        build_explore_finding_event(goal_id="feedback", node_id="choice", finding_id="bad",
            title="Wrong candidate", execution_observation={**observation, "result_candidate_sha256": "b" * 64})
    before = {path: path.read_bytes() for path in (log, state, registry)}
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root_override, server.verbose = registry, None, False
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        # Explore is never activated: already saved evidence remains readable.
        with urlopen(f"http://127.0.0.1:{server.server_port}/api/chat/explore-results?goal_id=feedback&view=findings") as response:
            page = json.load(response)
        changed = next(row for row in page["items"] if row["finding_id"] == "changed")
        plain = next(row for row in page["items"] if row["finding_id"] == "plain")
        assert changed["execution_observation"] == observation
        assert changed["status"] == "refuted"
        assert "execution_observation" not in plain
        for row in (plain, changed):
            lark = _finding_record_values(row, goal_id="feedback", source_id="fixture")
            assert lark["Status"] == "refuted"
            assert lark["LoopX Result ID"] == row["finding_id"]
            if row is plain:
                assert lark["Summary"] == "No measured improvement."
            else:
                assert "broad-search -> targeted-check" in lark["Summary"]
                assert "12.5 / 60" in lark["Summary"]
                assert "Search iterations: not measured" in lark["Summary"]
                assert "Termination reason: not measured" in lark["Summary"]
                assert "artifact:negative-result" in lark["Summary"]
                assert "independently assessed" in lark["Summary"]
        assert all(path.read_bytes() == contents for path, contents in before.items())
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
