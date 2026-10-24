"""One original scoped reader supplies complete, fixed-window report evidence."""
import json
from copy import deepcopy

import pytest

from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME
from loopx.capabilities.manager_context.report_source import (
    collect_manager_report_source, manager_portfolio_report_source_adapter,
)
from loopx.capabilities.periodic_report.adapters import PeriodicReportAdapterRegistry

WINDOW = {"window_start": "2026-01-01T09:00:00+08:00", "window_end": "2026-01-02T09:00:00+08:00"}
REVISION = "sha256:" + "1" * 64


def source_fixture(goals=15):
    calls = []

    def read(args):
        calls.append(deepcopy(args))
        view = args["view"]
        if view == "portfolio":
            assert args["include_stopped"] is True
            rows = [{"goal_id": f"goal-{i}", "activation_state": "stopped" if i == 14 else "active"}
                    for i in range(goals)]
        elif view == "todos":
            if args.get("todo_id"):
                rows = [{"todo_id": args["todo_id"], "title": 'Full task "😀"  ' * 600,
                         "status": "done" if args["todo_id"] == "completed" else "open", "content_truncated": False}]
            else:
                rows = [{"todo_id": f"todo-{i}", "title": f"Current task {i}",
                         "status": "open", "content_truncated": i == 0} for i in range(13)]
        elif view == "deliveries":
            assert args["include_outcome_gaps"] is True
            assert args["window_start"] == "2026-01-01T01:00:00+00:00"
            assert args["window_end"] == "2026-01-02T01:00:00+00:00"
            if args.get("offset") or args.get("delivery_ref"):
                assert args["expected_source_revision"] == REVISION
            rows = [{"todo_id": "completed", "source_ref": "sha256:" + f"{i:064x}",
                     "outcome": "outcome_gap" if i == 12 else "outcome_progress",
                     "recorded_details": {"verification": "recorded_claim_not_independent_verification",
                        "observed_reality": "Complete recorded detail" if args.get("delivery_ref") else "Excerpt"}}
                    for i in range(13)]
            if args.get("delivery_ref"):
                rows = [row for row in rows if row["source_ref"] == args["delivery_ref"]]
        else:
            assert view == "handoffs"
            rows = [{"request_id": "original-request", "status": "awaiting_conclusion"}]
        offset, limit = args.get("offset", 0), args.get("limit", 12)
        return {"ok": True, "rows": rows[offset:offset + limit], "matched": len(rows),
            "source": {"source_revision": REVISION, "status": "read"},
            "next_offset": offset + limit if offset + limit < len(rows) else None}

    return read, calls


def test_adapter_and_source_preserve_all_pages_full_evidence_and_stopped_goals():
    read, calls = source_fixture()
    snapshot = collect_manager_report_source(read, WINDOW)
    assert snapshot["coverage"]["complete"]
    assert len(snapshot["goal_ids"]) == 15 and snapshot["goal_ids"][-1] == "goal-14"
    rows = snapshot["rows"]
    assert sum(row["kind"] == "todos" for row in rows) == 15 * 13
    assert sum(row["kind"] == "deliveries" for row in rows) == 15 * 13
    assert sum(row["kind"] == "delivery_task" for row in rows) == 15
    assert all(row["evidence"]["recorded_details"]["observed_reality"] == "Complete recorded detail"
               for row in rows if row["kind"] == "deliveries")
    assert sum(row["evidence"]["outcome"] == "outcome_gap" for row in rows if row["kind"] == "deliveries") == 15
    assert not snapshot["coverage"]["referenced_artifacts_read"]
    assert any(call["view"] == "portfolio" and call.get("offset") == 12 for call in calls)
    registry = PeriodicReportAdapterRegistry()
    registry.register_source(manager_portfolio_report_source_adapter(read))
    source = registry.collect("manager_portfolio", WINDOW)
    chunks = {}
    for item in source["sections"][0]["items"][1:]:
        chunks.setdefault(item["source_ref"], []).append(json.loads(item["summary"]))
    # Reassemble the normal adapter result, including quotes, unicode and
    # whitespace at part boundaries; per-item normalization loses no evidence.
    restored = {ref: json.loads("".join(parts)) for ref, parts in chunks.items()}
    assert restored == {row["source_ref"]: row for row in rows}


@pytest.mark.parametrize("failure", ["outage", "revision", "nonadvancing"])
def test_failed_goal_source_does_not_hide_later_goals_or_mix_pages(failure):
    read, _ = source_fixture(2)

    def broken(args):
        page = read(args)
        if args.get("goal_id") == "goal-0" and args["view"] == "todos" and args.get("offset", 0):
            if failure == "outage":
                return {"ok": False, "error": "fixture_source_outage"}
            if failure == "revision":
                page["source"]["source_revision"] = "sha256:" + "2" * 64
            else:
                page["next_offset"] = args["offset"]
        return page

    snapshot = collect_manager_report_source(broken, WINDOW)
    assert not snapshot["coverage"]["complete"]
    assert not [row for row in snapshot["rows"] if row["goal_id"] == "goal-0" and row["kind"] == "todos"]
    assert len([row for row in snapshot["rows"] if row["goal_id"] == "goal-1" and row["kind"] == "todos"]) == 13
    assert snapshot["source_result"]["status"] == "partial"


def test_partial_inventory_keeps_known_goals_and_reader_exception_is_local():
    read, _ = source_fixture()

    def broken(args):
        if args["view"] == "portfolio" and args.get("offset", 0):
            return {"ok": False, "error": "inventory_page_unavailable"}
        if args.get("goal_id") == "goal-0" and args["view"] == "todos":
            raise OSError("source outage")
        return read(args)

    snapshot = collect_manager_report_source(broken, WINDOW)
    assert len(snapshot["goal_ids"]) == 12 and not snapshot["coverage"]["complete"]
    assert any(row["goal_id"] == "goal-11" and row["kind"] == "todos" for row in snapshot["rows"])
    assert any(gap["view"] == "portfolio" for gap in snapshot["gaps"])


def test_budget_unknown_and_revocation_do_not_become_empty_success():
    read, _ = source_fixture(2)
    snapshot = collect_manager_report_source(read, WINDOW, max_calls=5)
    assert snapshot["coverage"]["read_calls"] == 5
    assert not snapshot["coverage"]["complete"] and snapshot["gaps"]
    assert snapshot["goal_ids"] == ["goal-0", "goal-1"]
    assert sum(row["kind"] == "goal" for row in snapshot["rows"]) == 2
    assert sum(row["kind"] == "todos" for row in snapshot["rows"]) == 13
    assert not any(row["kind"] == "deliveries" for row in snapshot["rows"])

    def revoked(args):
        return {"ok": False, "error": "authorization_changed"} if args.get("goal_id") else read(args)

    with pytest.raises(PermissionError):
        collect_manager_report_source(revoked, WINDOW)
    with pytest.raises(ValueError, match="not complete"):
        collect_manager_report_source(lambda _: pytest.fail("no reads before due"),
            {"window_start": "2099-01-01T00:00:00Z", "window_end": "2099-01-02T00:00:00Z"})


def test_original_reader_freezes_and_pages_source_but_revocation_wins(tmp_path, monkeypatch):
    import loopx.capabilities.manager_context.report_source as module
    real = module.collect_manager_report_source
    read, calls = source_fixture(2)
    monkeypatch.setattr(module, "collect_manager_report_source", lambda _, request: real(read, request))
    valid = [True]
    records = []
    tool = ManagerInspection(context={"scope": "owner_global"}, registry_path=tmp_path / "registry",
        runtime_root=tmp_path, owner_scope=True, scope_valid=lambda: valid[0], record=records.append)
    first = tool.read(TOOL_NAME, {"view": "report_source", **WINDOW})
    assert first["ok"] and first["source"]["window_bounds"] == "[start,end)"
    count = len(calls)
    next_page = tool.read(TOOL_NAME, {"view": "report_source", **WINDOW,
        "offset": first["next_offset"], "expected_source_revision": first["source"]["source_revision"]})
    assert next_page["ok"] and len(calls) == count
    assert next_page["source"] == first["source"]
    missing = tool.read(TOOL_NAME, {"view": "report_source", **WINDOW, "offset": 8})
    assert missing["error"] == "invalid_arguments"
    mismatch = tool.read(TOOL_NAME, {"view": "report_source", **WINDOW,
        "expected_source_revision": "sha256:" + "0" * 64})
    assert mismatch["error"] == "report_snapshot_revision_mismatch" and mismatch["rows"] == []
    valid[0] = False
    assert tool.read(TOOL_NAME, {"view": "report_source", **WINDOW})["error"] == "authorization_changed"
    valid[0] = True
    assert tool.read(TOOL_NAME, {"view": "report_source", **WINDOW, "offset": 8,
        "expected_source_revision": first["source"]["source_revision"]})["error"] == "report_snapshot_unavailable"
    assert len(records) == 2


@pytest.mark.parametrize("extra", [
    {"include_outcome_gaps": 1}, {"include_outcome_gaps": "true"},
    {"include_outcome_gaps": True, "source_id": "ssh:fixture"},
])
def test_counterevidence_selection_rejects_invalid_or_remote_arguments(tmp_path, extra):
    tool = ManagerInspection(context={}, registry_path=tmp_path / "unreadable",
        runtime_root=tmp_path, owner_scope=False, scope_valid=lambda: pytest.fail("invalid input reached the reader"),
        record=lambda _: pytest.fail("invalid input produced evidence"))
    result = tool.read(TOOL_NAME, {"view": "deliveries", "goal_id": "goal-a", **extra})
    assert result["error"] == "invalid_arguments"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_actual_canonical_sources_join_a_completed_task_and_fixed_boundaries(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_source_fixture, isolate_sqlite_runtime,
    )
    from loopx.control_plane.todos.list_readback import list_goal_todos
    from loopx.control_plane.todos.contract import format_todo_metadata_line
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, project = tmp_path / "runtime", tmp_path / "project"
    state = project / ".loopx/goals/goal-a/ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text("# Goal\n\n## Agent Todo\n\n- [x] A recorded report result\n" +
        format_todo_metadata_line(todo_id="todo_report", status="done", task_class="advancement_task") + "\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "repo": str(project), "state_file": str(state.relative_to(project)),
        "coordination": {"registered_agents": ["agent-a"]}}, {"id": "outside", "repo": str(tmp_path / "outside-project")}]}))
    initialize_canonical_source_fixture(runtime, "goal-a", state_path=state, provider=provider)
    todo = list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a")["todos"][0]
    assert todo["status"] == "done"
    path = runtime / "goals/goal-a/runs/index.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    run = {"goal_id": "goal-a", "todo_id": todo["todo_id"], "delivery_outcome": "outcome_progress"}
    path.write_text("".join(json.dumps({**run, "generated_at": timestamp,
        "delivery_outcome": "outcome_gap" if i == 0 else "outcome_progress"}) + "\n" for i, timestamp in enumerate((
        "2026-01-01T09:00:00+08:00", "2026-01-02T08:59:00+08:00", "2026-01-02T09:00:00+08:00"))))
    tool = ManagerInspection(context={"scope": "owner_goal", "goals": [{"goal_id": "goal-a"}]},
        registry_path=registry, runtime_root=runtime, owner_scope=True, scope_valid=lambda: True,
        discovery_scope=lambda: ["goal-a"], record=lambda _: None)
    snapshot = collect_manager_report_source(lambda args: tool.read(TOOL_NAME, args), WINDOW)
    assert snapshot["goal_ids"] == ["goal-a"]
    deliveries = [row["evidence"] for row in snapshot["rows"] if row["kind"] == "deliveries"]
    assert len(deliveries) == 2 and {row["outcome"] for row in deliveries} == {"outcome_gap", "outcome_progress"}
    assert deliveries[-1]["recorded_at"].startswith("2026-01-01T09:")
    linked = [row["evidence"] for row in snapshot["rows"] if row["kind"] == "delivery_task"]
    assert len(linked) == 1 and linked[0]["todo_id"] == todo["todo_id"] and linked[0]["status"] == "done"
    assert deliveries[0]["recorded_details"]["verification"] == "recorded_claim_not_independent_verification"
    ordinary = tool.read(TOOL_NAME, {"view": "deliveries", "goal_id": "goal-a", **WINDOW})
    assert ordinary["matched"] == 1 and ordinary["rows"][0]["outcome"] == "outcome_progress"
    assert list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a")["todos"][0] == todo
