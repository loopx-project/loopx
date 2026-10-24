"""Manager reads retain Core truth, pagination and audience boundaries."""

import io
import json
import queue
from datetime import datetime, timezone

import pytest

from loopx.capabilities.manager_context.inspection import (
    CONTEXT_TOOL_NAME,
    ManagerInspection,
    READ_ARGUMENT_NAMES,
    READ_TOOL,
    READ_VIEWS,
    TOOL_NAME,
    agent_work_summary,
    manager_index,
)
from loopx.chat_agent import CodexChatAgentSession, CodexChatAgentError
from loopx.chat_manager_history import read_manager_delivery_history
import loopx.chat_manager_details as details
from tests.control_plane.host_process_fixture import write_python_host_fixture


def inspector(tmp_path, scope=lambda: True):
    records = []
    (tmp_path / "registry.json").write_text(json.dumps({"goals": [{"id": "alpha"}]}))
    tool = ManagerInspection(
        context={"snapshot_id": "fixture", "goals": [{"goal_id": "alpha"}]},
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
        owner_scope=False,
        scope_valid=scope,
        record=records.append,
    )
    return tool, records


def test_todo_pages_reach_beyond_previous_cap_and_keep_revision(monkeypatch, tmp_path):
    monkeypatch.setattr(
        details,
        "list_goal_todos",
        lambda **_: {
            "ok": True,
            "source": "file_authority",
            "todos": [
                {"todo_id": f"todo_{i}", "text": f"Check result {i}", "status": "open"}
                for i in range(55)
            ],
        },
    )
    tool, records = inspector(tmp_path)
    offset, ids, revisions = 0, [], set()
    while offset is not None:
        page = tool.read(
            TOOL_NAME, {"view": "todos", "goal_id": "alpha", "offset": offset}
        )
        ids.extend(r["todo_id"] for r in page["rows"])
        revisions.add(page["source"]["source_revision"])
        offset = page["next_offset"]
    assert len(ids) == len(set(ids)) == 55
    assert len(revisions) == 1 and len(records) == 7
    assert records[-1]["matched"] == 55


@pytest.mark.parametrize(
    "args",
    [
        {"view": "todos", "goal_id": "outside"},
        {"view": "shell"},
        {"view": "portfolio", "path": "/unknown"},
        {"view": "portfolio", "offset": True},
        {"view": "portfolio", "limit": 13},
    ],
)
def test_invalid_or_out_of_scope_reads_do_not_touch_core(monkeypatch, tmp_path, args):
    def forbidden(**_):
        pytest.fail("Core must not be read")

    monkeypatch.setattr(details, "list_goal_todos", forbidden)
    tool, records = inspector(tmp_path)
    assert tool.read(TOOL_NAME, args)["ok"] is False
    assert not records


@pytest.mark.parametrize(
    "args, expected",
    [
        ({"view": "portfolio", "path": "/unknown"}, ["unknown_argument:path"]),
        (
            {"view": "shell"},
            ["view:must_be_one_of_sources,portfolio,todos,deliveries,handoffs,agents,agent_route,report_source"],
        ),
        ({"view": "portfolio", "offset": True}, ["offset:must_be_an_integer_at_least_0"]),
        (
            {"view": "portfolio", "limit": 13},
            ["limit:must_be_an_integer_between_1_and_12"],
        ),
        (
            {"view": "todos", "goal_id": "alpha", "request_id": "a" * 64},
            ["request_id:only_for_view_handoffs"],
        ),
        (
            {"view": "portfolio", "include_stopped": 1},
            ["include_stopped:must_be_a_boolean"],
        ),
        (
            {"view": "todos", "goal_id": "alpha", "include_stopped": True},
            ["include_stopped:only_for_view_portfolio_or_agents"],
        ),
        ({"view": "todos", "goal_id": "alpha", "days": 7}, ["days:only_for_view_deliveries"]),
        (
            {"view": "deliveries", "goal_id": "alpha", "days": 91},
            ["days:must_be_an_integer_between_1_and_90"],
        ),
        (
            {"view": "todos", "goal_id": "alpha", "source_id": 3},
            ["source_id:must_be_a_string"],
        ),
        ({"goal_id": "alpha"}, ["view:must_be_one_of_sources,portfolio,todos,deliveries,handoffs,agents,agent_route,report_source"]),
    ],
)
def test_a_refused_read_names_the_argument_that_must_change(tmp_path, args, expected):
    """A rejected tool call must be repairable without guessing.

    The caller is a model, not a human reading a stack trace: a bare
    ``invalid_arguments`` makes it retry blind, while this payload names the
    offending argument and what the published schema accepts.
    """

    tool, records = inspector(tmp_path)
    result = tool.read(TOOL_NAME, args)
    assert result["ok"] is False and result["error"] == "invalid_arguments"
    assert result["rejected_arguments"] == expected
    # The offer is the published schema, so the correction cannot drift from
    # what the caller was actually handed.
    assert result["allowed_arguments"] == list(READ_ARGUMENT_NAMES)
    assert result["allowed_views"] == list(READ_VIEWS)
    assert result["allowed_arguments"] == list(READ_TOOL["inputSchema"]["properties"])
    assert not records


def test_a_refused_read_names_every_bad_argument_and_the_called_tool(tmp_path):
    tool, records = inspector(tmp_path)
    result = tool.read(
        CONTEXT_TOOL_NAME,
        {"view": "shell", "limit": 99, "path": "/x", "days": 0},
    )
    assert result["rejected_arguments"] == [
        "unknown_argument:path",
        "view:must_be_one_of_sources,portfolio,todos,deliveries,handoffs,agents,agent_route,report_source",
        "limit:must_be_an_integer_between_1_and_12",
        "days:only_for_view_deliveries",
    ]
    # The repair instruction names the tool the caller actually used.
    assert CONTEXT_TOOL_NAME in result["detail"]
    assert TOOL_NAME not in result["detail"]
    assert not records



def _todo_read_fixture(records):
    def read(**arguments):
        if arguments.get("todo_id"):
            matches = [r for r in records if r["todo_id"] == arguments["todo_id"]]
            return {"ok": True, "todo": matches[0] if len(matches) == 1 else None}
        return {"ok": True, "todos": records}
    return read

def test_a_valid_read_keeps_its_existing_shape(tmp_path):
    """The refusal payload is additive: legal reads are unchanged."""

    tool, records = inspector(tmp_path)
    result = tool.read(TOOL_NAME, {"view": "portfolio", "limit": 12})
    assert result["ok"] is True
    assert "rejected_arguments" not in result and "allowed_arguments" not in result
    assert records


def test_revocation_during_read_suppresses_result(monkeypatch, tmp_path):
    grants = iter([True, False])
    monkeypatch.setattr(
        details, "list_goal_todos", _todo_read_fixture([])
    )
    tool, records = inspector(tmp_path, lambda: next(grants))
    assert tool.read(TOOL_NAME, {"view": "todos", "goal_id": "alpha"}) == {
        "ok": False,
        "error": "authorization_changed",
    }
    assert not records


@pytest.mark.parametrize('name', [TOOL_NAME, CONTEXT_TOOL_NAME])
def test_exact_todo_recovers_compacted_context_without_widening_scope(monkeypatch, tmp_path, name):
    monkeypatch.setattr(details, 'list_goal_todos', lambda **_: {'ok': True, 'todo':
        {'todo_id': 'todo_report', 'status': 'done', 'text': 'Research report',
         'note': 'Context. ' * 60 + 'Do not publish.', 'resume_ready': False},
    })
    tool, records = inspector(tmp_path)
    tool.owner_scope = True
    result = tool.read(name, {'view': 'todos', 'goal_id': 'alpha', 'todo_id': 'todo_report'})
    assert result['matched'] == 1 and result['next_offset'] is None
    assert result['rows'][0]['continuation'].endswith('Do not publish.')
    assert result['rows'][0]['status'] == 'done'
    assert result['rows'][0]['resume_ready'] is False
    assert len(records) == 1
    assert tool.read(name, {'view': 'todos', 'goal_id': 'outside', 'todo_id': 'todo_report'})['ok'] is False
    assert len(records) == 1


@pytest.mark.parametrize('arguments', [
    {'view': 'portfolio', 'todo_id': 'todo_report'},
    {'view': 'todos', 'todo_id': 'todo_report'},
    {'view': 'todos', 'goal_id': 'alpha', 'todo_id': ''},
    {'view': 'todos', 'goal_id': 'alpha', 'todo_id': 3},
])
def test_invalid_exact_todo_read_does_not_touch_core(monkeypatch, tmp_path, arguments):
    monkeypatch.setattr(details, 'list_goal_todos', lambda **_: pytest.fail('No source read'))
    tool, records = inspector(tmp_path)
    assert tool.read(TOOL_NAME, arguments)['error'] == 'invalid_arguments'
    assert not records


def test_exact_todo_retains_existing_encoded_row_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(details, 'list_goal_todos', lambda **_: {'ok': True, 'todo':
        {'todo_id': 'todo_report', 'status': 'open', 'text': 'Research', 'note': 'x' * 25000},
    })
    tool, _ = inspector(tmp_path)
    tool.owner_scope = True
    result = tool.read(TOOL_NAME, {'view': 'todos', 'goal_id': 'alpha', 'todo_id': 'todo_report'})
    assert result['oversized_rows'] == [0]
    assert result['rows'][0]['status'] == 'oversized_record'
    assert 'x' * 25000 not in json.dumps(result)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_attention_reference_recovers_canonical_long_todo(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import (
        isolate_sqlite_runtime, promoted_create_fixture,
    )
    from loopx.chat_manager_context import manager_turn_context
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    from loopx.todos import add_goal_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    full_text = "[P0] Compare the evidence. " + "背景😀。" * 180 + "Only publish after owner acceptance."
    full_note = "Original constraints. " * 50 + "Keep the original audience."
    created = add_goal_todo(
        registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a",
        role="user", text=full_text, note=full_note, task_class="user_gate",
        operation_id="long-owner-request-fixture",
    )
    assert created["ok"], created
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    todo_id = before["todos"][0]["todo_id"]
    native = details.list_goal_todos(
        registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a", todo_id=todo_id,
    )
    assert native["authority_read"]["decision_read_from_provider"] is True
    assert native["authority_read"]["legacy_fallback_used"] is False
    assert native["todo"]["text"] == full_text
    # The native read also supplies a derived summary; exact inspection must not prefer it.
    assert native["todo"]["title"] != full_text
    grants = {"current": True}
    for channel in ("manager", "goal.goal-a"):
        context = manager_turn_context(
            registry, {"channel_id": channel, "goal_id": "goal-a"}, runtime, include_details=False,
        )
        item = manager_index(context)["goals"][0]["attention"]["items"][0]
        assert item["details_omitted"] is True
        reference = item["read_reference"]
        assert reference == {"view": "todos", "goal_id": "goal-a", "todo_id": todo_id}
        records = []
        tool = ManagerInspection(
            context=context, registry_path=registry, runtime_root=runtime, owner_scope=True,
            scope_valid=lambda: grants["current"], record=records.append,
        )
        result = tool.read(TOOL_NAME, reference)
        assert result["ok"] is True and result["matched"] == 1
        assert result["rows"][0]["title"] == full_text
        assert result["rows"][0]["continuation"] == full_note
        assert result["rows"][0]["content_truncated"] is False
        tool.owner_scope = False
        external = tool.read(CONTEXT_TOOL_NAME, reference)
        assert external["rows"][0]["title"] == full_text
        assert "continuation" not in external["rows"][0]
        grants["current"] = False
        assert tool.read(TOOL_NAME, reference)["error"] == "authorization_changed"
        assert len(records) == 2
        grants["current"] = True
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before


def test_unavailable_is_unknown_and_large_portfolio_is_disclosed(tmp_path, monkeypatch):
    tool, records = inspector(tmp_path)
    result = tool.read(TOOL_NAME, {"view": "deliveries", "goal_id": "alpha"})
    assert result["unknown"] and result["matched"] is None
    assert result["source"]["status"] == "unavailable"
    tool.context["goals"][0]["current_todos"] = {"body": "x" * 40000}
    index = manager_index(tool.context)
    assert len(json.dumps(index)) < 2000
    # The detailed portfolio is now a fresh read, not the Turn's compact index.
    monkeypatch.setattr("loopx.chat_manager_context.manager_turn_context", lambda *a, **kw: {
        "goals": tool.context["goals"], "coverage": {"complete": True},
    })
    result = tool.read(TOOL_NAME, {"view": "portfolio"})
    assert result["oversized_rows"] == [0]
    assert len(json.dumps(result)) < 2000


def test_agent_work_summary_bounds_content_without_inventing_execution():
    summary = agent_work_summary({"agent_id": "worker", "source_verified": True,
                                  "todos": [{"todo_id": "todo_1", "title": "x" * 25000,
                                             "status": "blocked", "readiness": "blocked",
                                             "raw_private_body": "excluded"}]})
    assert len(summary["todos"][0]["title"]) == 240
    assert summary["todos"][0]["content_truncated"] is True
    assert summary["todos"][0]["status"] == "blocked"
    assert agent_work_summary(summary) == summary
    assert summary["todos_omitted"] == 0 and "raw_private_body" not in json.dumps(summary)
    tool_index = manager_index({"goals": [{"goal_id": "stopped", "activation_state": "stopped",
                                           "agents": [summary]}]})
    assert tool_index["goals"] == [] and tool_index["stopped_goals_excluded"] == 1


def test_current_work_portfolio_keeps_authorization_fence(tmp_path):
    tool, records = inspector(tmp_path, scope=lambda: False)
    tool.context["goals"][0]["agents"] = [{"agent_id": "worker", "todos": [{"title": "private"}]}]
    result = tool.read(TOOL_NAME, {"view": "portfolio"})
    assert result == {"ok": False, "error": "authorization_changed"}
    assert not records and "private" not in json.dumps(result)


def test_directory_attention_preview_keeps_all_workers_and_exact_scoped_read(monkeypatch, tmp_path):
    text = "Review the complete plan. " * 50 + "Only proceed after owner acceptance."
    task = {"todo_id": "todo_gate", "role": "user", "task_class": "user_gate",
            "status": "blocked", "text": text, "note": "Do not bypass acceptance"}
    monkeypatch.setattr(details, "list_goal_todos", _todo_read_fixture([task]))
    tool, _ = inspector(tmp_path)
    tool.owner_scope = True
    row = tool.context["goals"][0]
    row.update(quality="stale", source={"revision": "revision-original"},
               agents=[{"agent_id": f"worker_{i}", "source_verified": False} for i in range(40)],
               attention={"status": "read", "coverage": {"known": 1, "included": 1, "omitted": 0},
                          "items": [{"todo_id": task["todo_id"], "owner_must_act": True,
                                     "blocker": {"blocker_identity": "todo:todo_gate", "task": task,
                                                 "cause": text, "delivery": {"receipt": "x" * 20000}},
                                     "request": {"request_id": task["todo_id"], "text": text}}]})
    before = json.dumps(tool.context, sort_keys=True)
    index = manager_index(tool.context)
    preview = index["goals"][0]
    assert [a["agent_id"] for a in preview["agents"]] == [f"worker_{i}" for i in range(40)]
    assert preview["quality"] == "stale" and preview["source"]["revision"] == "revision-original"
    item = preview["attention"]["items"][0]
    assert item["request"]["content_truncated"] is True and item["details_omitted"] is True
    assert "receipt" not in json.dumps(item) and len(json.dumps(item)) < 1400
    assert json.dumps(tool.context, sort_keys=True) == before
    result = tool.read(TOOL_NAME, item["read_reference"])
    assert result["ok"] and result["rows"][0]["title"] == text
    assert result["rows"][0]["continuation"] == task["note"]
    assert result["rows"][0]["content_truncated"] is False
    tool.scope_valid = lambda: False
    assert tool.read(TOOL_NAME, item["read_reference"])["error"] == "authorization_changed"


def test_delivery_pages_cross_day_without_duplication(tmp_path):
    path = tmp_path / "goals" / "alpha" / "runs" / "index.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "generated_at": at,
            "delivery_outcome": "outcome_progress",
            "todo_id": f"todo_{i}",
        }
        for i, at in enumerate(
            [
                "2026-01-01T12:00:00+00:00",
                "2026-01-02T01:00:00+00:00",
                "2026-01-02T02:00:00+00:00",
            ]
        )
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows))
    pages = [
        read_manager_delivery_history(
            tmp_path,
            "alpha",
            limit=1,
            offset=i,
            now=datetime(2026, 1, 2, 3, tzinfo=timezone.utc),
        )
        for i in range(3)
    ]
    assert {p["deliveries"][0]["todo_id"] for p in pages} == {
        "todo_0",
        "todo_1",
        "todo_2",
    }


def _delivery_window_fixture(root, goal_id="alpha"):
    path = root / "goals" / goal_id / "runs" / "index.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fixed daily report includes its start, excludes its end, and crosses
    # midnight. Rows just outside the window catch a calendar-day substitute.
    times = ["2026-01-01T08:59:59+08:00", "2026-01-01T09:00:00+08:00",
             "2026-01-01T23:59:59+08:00", "2026-01-02T00:00:00+08:00",
             "2026-01-02T08:59:59+08:00", "2026-01-02T09:00:00+08:00"]
    rows = [{"generated_at": at, "delivery_outcome": "outcome_progress",
             "goal_id": goal_id, "todo_id": f"todo_{i}",
             "agent_vision": {"path_delta": {"observed_reality": "Observed result. " * 45 + "Counterexample remains open."}},
             "progress_observation": {"evidence_ids": [f"evidence-{j}" for j in range(13)]}}
            for i, at in enumerate(times)]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


_FIXED_WINDOW = {"window_start": "2026-01-01T09:00:00+08:00",
                 "window_end": "2026-01-02T09:00:00+08:00"}


def test_fixed_window_pages_and_exact_receipt_recover_substantive_evidence(tmp_path, monkeypatch):
    _delivery_window_fixture(tmp_path)
    monkeypatch.setattr(details, "list_goal_todos", _todo_read_fixture([]))
    tool, _ = inspector(tmp_path)
    arguments = {"view": "deliveries", "goal_id": "alpha", "limit": 1, **_FIXED_WINDOW}
    first = tool.read(TOOL_NAME, arguments)
    revision = first["source"]["source_revision"]
    pages = [first]
    while pages[-1]["next_offset"] is not None:
        # A fresh reader models process/session recovery between pages.
        resumed, _ = inspector(tmp_path)
        pages.append(resumed.read(TOOL_NAME, {**arguments, "offset": pages[-1]["next_offset"],
                                             "expected_source_revision": revision}))
    assert [row["todo_id"] for page in pages for row in page["rows"]] == ["todo_4", "todo_3", "todo_2", "todo_1"]
    assert all(page["source"]["source_revision"] == revision for page in pages)
    assert all(page["source"]["window_start"] == "2026-01-01T01:00:00+00:00" for page in pages)
    assert first["source"]["window_end"] == "2026-01-02T01:00:00+00:00"
    assert first["rows"][0]["recorded_details"]["field_coverage"]["truncated"] == ["observed_reality"]
    exact = tool.read(TOOL_NAME, {**arguments, "delivery_ref": first["rows"][0]["source_ref"],
                                 "expected_source_revision": revision})
    row = exact["rows"][0]
    assert row["recorded_details"]["observed_reality"].endswith("Counterexample remains open.")
    assert row["recorded_details"]["field_coverage"]["truncated"] == []
    assert row["recorded_details"]["evidence_coverage"]["included"] == 13
    assert row["recorded_details"]["artifact_read_status"] == "not_read"
    assert row["recorded_details"]["verification"] == "recorded_claim_not_independent_verification"


def test_revision_pinned_read_rejects_same_size_timestamp_preserving_rewrite(tmp_path):
    import os
    path = _delivery_window_fixture(tmp_path)
    first = read_manager_delivery_history(tmp_path, "alpha", **_FIXED_WINDOW)
    previous = path.stat()
    path.write_bytes(path.read_bytes().replace(b"todo_4", b"todo_9"))
    assert path.stat().st_size == previous.st_size
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    stale = read_manager_delivery_history(tmp_path, "alpha", offset=1, limit=1,
        expected_source_revision=first["source_revision"], **_FIXED_WINDOW)
    assert stale["status"] == "conflicting" and not stale["deliveries"]
    assert stale["coverage"]["matched"] is None
    assert stale["source_revision"] != first["source_revision"]


@pytest.mark.parametrize("arguments", [
    {"window_start": _FIXED_WINDOW["window_start"]},
    {"window_start": None, "window_end": None},
    {"window_start": "2026-01-01T09:00:00", "window_end": _FIXED_WINDOW["window_end"]},
    {"window_start": _FIXED_WINDOW["window_end"], "window_end": _FIXED_WINDOW["window_start"]},
    {"window_start": "2025-01-01T00:00:00Z", "window_end": "2026-01-01T00:00:00Z"},
    {**_FIXED_WINDOW, "days": 1},
    {**_FIXED_WINDOW, "offset": 1},
    {**_FIXED_WINDOW, "delivery_ref": "sha256:" + "a" * 64},
    {**_FIXED_WINDOW, "source_id": "ssh:sample"},
    {"delivery_ref": "private/path"},
    {**_FIXED_WINDOW, "view": "todos"},
])
def test_invalid_fixed_window_does_not_read_a_source(tmp_path, arguments, monkeypatch):
    tool, records = inspector(tmp_path)
    monkeypatch.setattr(tool, "_goal_scope", lambda: pytest.fail("invalid input reached source admission"))
    result = tool.read(TOOL_NAME, {"view": "deliveries", "goal_id": "alpha", **arguments})
    assert result["error"] == "invalid_arguments" and result["rejected_arguments"]
    assert not records


def test_fixed_window_retains_scope_fence_and_oversized_record_gap(tmp_path, monkeypatch):
    path = _delivery_window_fixture(tmp_path)
    monkeypatch.setattr(details, "list_goal_todos", _todo_read_fixture([]))
    tool, records = inspector(tmp_path)
    arguments = {"view": "deliveries", "goal_id": "alpha", **_FIXED_WINDOW}
    assert tool.read(TOOL_NAME, {**arguments, "goal_id": "other"})["error"] == "goal_outside_available_scope"
    assert not records
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[4]["agent_vision"]["path_delta"]["observed_reality"] = "Long finding. " * 3000
    path.write_text("\n".join(json.dumps(row) for row in rows))
    first = tool.read(TOOL_NAME, arguments)
    exact = tool.read(TOOL_NAME, {**arguments, "delivery_ref": first["rows"][0]["source_ref"],
                                 "expected_source_revision": first["source"]["source_revision"]})
    assert exact["oversized_rows"] == [0]
    assert exact["rows"][0]["status"] == "oversized_record"
    tool.scope_valid = lambda: False
    assert tool.read(TOOL_NAME, arguments)["error"] == "authorization_changed"


def test_fixed_window_does_not_claim_complete_future_or_invalid_history(tmp_path, monkeypatch):
    monkeypatch.setattr(details, "list_goal_todos", _todo_read_fixture([]))
    path = _delivery_window_fixture(tmp_path)
    tool, _ = inspector(tmp_path)
    future = tool.read(TOOL_NAME, {"view": "deliveries", "goal_id": "alpha",
        "window_start": "2090-01-01T09:00:00Z", "window_end": "2090-01-02T09:00:00Z"})
    assert future["unknown"] and future["source"]["window_complete"] is False
    assert not future["rows"]
    with path.open("a") as stream:
        stream.write(json.dumps({"generated_at": "unknown", "delivery_outcome": "outcome_progress"}) + "\n")
        stream.write('{"unfinished":\nnull\n')
    result = tool.read(TOOL_NAME, {"view": "deliveries", "goal_id": "alpha", **_FIXED_WINDOW})
    assert result["unknown"] and result["source"]["coverage"]["invalid_delivery_records"] == 1
    assert result["source"]["coverage"]["invalid_index_records"] == 2


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fixed_window_joins_exact_canonical_task_beyond_overview(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
    from loopx.todos import add_goal_todo
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    created = add_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a",
        role="agent", text="A concrete result outside the current overview", operation_id="report-task-fixture")
    assert created["ok"]
    todo_id = details.list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime),
                                    goal_id="goal-a")["todos"][0]["todo_id"]
    path = _delivery_window_fixture(runtime, "goal-a")
    path.write_text(path.read_text().replace("todo_4", todo_id))
    # The real canonical owner remains behind the exact read. Only the bounded
    # overview is empty, as when the associated task is beyond that page.
    real = details.read_manager_goal_details
    import loopx.capabilities.manager_context.inspection as module
    monkeypatch.setattr(module, "read_manager_goal_details", lambda *a, **kw:
        real(*a, **kw) if kw.get("todo_id") else {"todos": [], "completed_todos": []})
    tool = ManagerInspection(context={"scope": "owner_global"}, registry_path=registry,
        runtime_root=runtime, owner_scope=True, scope_valid=lambda: True, record=lambda _: None)
    result = tool.read(TOOL_NAME, {"view": "deliveries", "goal_id": "goal-a", **_FIXED_WINDOW})
    assert result["rows"][0]["todo_title"] == "[P1] A concrete result outside the current overview"
    assert result["source"]["coverage"]["matched"] == 4


def test_dynamic_requests_are_not_mistaken_for_client_responses(tmp_path):
    class Process:
        stdin = io.StringIO()

    messages = queue.Queue()
    session = CodexChatAgentSession(
        process=Process(),
        messages=messages,
        thread_id="thread",
        work_dir=tmp_path,
        current_turn_id="turn",
    )
    reads = []
    session.read_tool_handler = lambda tool, args: (
        reads.append((tool, args)) or {"ok": True}
    )
    # JSON-RPC request IDs belong to separate peers and may collide.
    messages.put(
        {
            "id": 1,
            "method": "item/tool/call",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "tool": TOOL_NAME,
                "arguments": {"view": "portfolio"},
            },
        }
    )
    messages.put({"id": 1, "result": {"receipt": "actual-response"}})
    assert session._request("fixture", {}, request_id=1) == {
        "receipt": "actual-response"
    }
    assert reads == [(TOOL_NAME, {"view": "portfolio"})]
    assert json.loads(Process.stdin.getvalue().splitlines()[-1])["result"]["success"]
    session._check_server_gate(
        {
            "id": 2,
            "method": "item/tool/call",
            "params": {
                "threadId": "other",
                "turnId": "turn",
                "tool": TOOL_NAME,
                "arguments": {},
            },
        }
    )
    assert len(reads) == 1
    assert not json.loads(Process.stdin.getvalue().splitlines()[-1])["result"][
        "success"
    ]
    with pytest.raises(CodexChatAgentError):
        session._check_server_gate(
            {"id": 3, "method": "item/commandExecution/requestApproval"}
        )


@pytest.mark.parametrize("read_view", ["todos", "todos_exact", "agents", "agent_route", "deliveries_window", "report_source"])
def test_manager_runtime_installs_tool_and_records_real_subprocess_read(
    monkeypatch, tmp_path, read_view
):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    import loopx.chat_manager_context as context

    (tmp_path / "registry.json").write_text(json.dumps({"goals": [
        {"id": "alpha", "registered_agents": ["review-worker"]}
    ]}))
    fake = tmp_path / "fake-codex"
    fake.write_text("""#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    r = json.loads(line)
    m = r.get('method')
    if m == 'initialize':
        result = {}
    elif m == 'thread/start':
        assert r['params']['dynamicTools'][0]['name'] == 'loopx_manager_read'
        result = {'thread': {'id': 'fixture-thread'}}
    elif m == 'thread/name/set':
        assert r['params'] == {'threadId': 'fixture-thread', 'name': 'LoopX Chat'}
        result = {}
    elif m == 'turn/start':
        text = json.dumps(r['params']['input'])
        assert 'manager_evidence_index_v1' in text
        assert 'Required input has not arrived' in text
        assert 'owner_must_act' in text
        print(json.dumps({'id':r['id'],'result':{'turn':{'id':'fixture-turn'}}}), flush=True)
        print(json.dumps({'id':900,'method':'item/tool/call','params':{
            'threadId':'fixture-thread','turnId':'fixture-turn','tool':'loopx_manager_read',
            'arguments':{'view':'todos','goal_id':'alpha'}}}), flush=True)
        continue
    elif r.get('id') == 900:
        assert r['result']['success']
        evidence = json.loads(r['result']['contentItems'][0]['text'])
        assert evidence['rows'][0]['title'] == 'Check the sample result'
        print(json.dumps({'method':'item/agentMessage/delta','params':{
            'threadId':'fixture-thread','turnId':'fixture-turn','delta':'Read the sample task.'}}), flush=True)
        print(json.dumps({'method':'turn/completed','params':{
            'threadId':'fixture-thread','turn':{'id':'fixture-turn','status':'completed'}}}), flush=True)
        continue
    else:
        continue
    print(json.dumps({'id':r['id'],'result':result}), flush=True)
""")
    if read_view == "agents":
        fake.write_text(fake.read_text().replace(
            "'view':'todos','goal_id':'alpha'", "'view':'agents','query':'review'").replace(
            "evidence['rows'][0]['title'] == 'Check the sample result'",
            "evidence['rows'][0]['agent_id'] == 'review-worker' and evidence['rows'][0]['execution_readiness'] == 'not_checked'"))
    if read_view == "agent_route":
        fake.write_text(fake.read_text().replace(
            "'view':'todos','goal_id':'alpha'",
            "'view':'agent_route','goal_id':'alpha','agent_id':'review-worker'").replace(
            "evidence['rows'][0]['title'] == 'Check the sample result'",
            "evidence['status'] == 'unavailable' and evidence['reason'] == 'no_binding' and evidence['host_delivery'] == 'not_attempted'"))
    if read_view == "todos_exact":
        fake.write_text(fake.read_text().replace(
            "'view':'todos','goal_id':'alpha'", "'view':'todos','goal_id':'alpha','todo_id':'todo_sample'").replace(
            "evidence['rows'][0]['title'] == 'Check the sample result'",
            "evidence['rows'][0]['continuation'].endswith('Keep this a draft; do not publish.')"))
    if read_view == "deliveries_window":
        fake.write_text(fake.read_text().replace(
            "'view':'todos','goal_id':'alpha'",
            "'view':'deliveries','goal_id':'alpha','window_start':'2026-01-01T09:00:00+08:00','window_end':'2026-01-02T09:00:00+08:00'").replace(
            "evidence['rows'][0]['title'] == 'Check the sample result'",
            "evidence['rows'][0]['todo_title'] == 'Check the sample result' and evidence['source']['window_bounds'] == '[start,end)'"))
    if read_view == "report_source":
        fake.write_text(fake.read_text().replace(
            "'view':'todos','goal_id':'alpha'",
            "'view':'report_source','window_start':'2026-01-01T09:00:00+08:00','window_end':'2026-01-02T09:00:00+08:00'").replace(
            "evidence['rows'][0]['title'] == 'Check the sample result'",
            "evidence['source']['coverage']['goals_in_inventory'] == 1 and evidence['rows'][0]['kind'] == 'goal' and evidence['rows'][0]['goal_id'] == 'alpha' and evidence['report_source']['source_id'] == 'manager_portfolio'"))
    fake = write_python_host_fixture(fake, fake.read_text(encoding="utf-8"))
    collected = []

    def collect(*args, **kwargs):
        collected.append(kwargs)
        return {"scope": "owner_global", "goals": [{"goal_id": "alpha", "attention": {
            "status": "read", "items": [{"owner_must_act": False,
                "blocker": {"cause": "Required input has not arrived"}}],
        }}], "snapshot_id": "fixture"}

    monkeypatch.setattr(context, "collect_manager_turn_context", collect)
    monkeypatch.setattr(
        details,
        "list_goal_todos",
        _todo_read_fixture([
                {
                    "todo_id": "todo_sample",
                    "text": "Check the sample result",
                    "status": "open",
                    "note": "Public background. " * 30 + "Keep this a draft; do not publish.",
                },
            ]),
    )
    store = ChatSessionStore(tmp_path / "runtime" / "chat")
    runtime = ChatRuntimeController(
        store=store, codex_bin=str(fake), registry_path=tmp_path / "registry.json"
    )
    if read_view in {"deliveries_window", "report_source"}:
        path = runtime.coordination_runtime_root / "goals" / "alpha" / "runs" / "index.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"generated_at": "2026-01-02T08:59:00+08:00",
            "delivery_outcome": "outcome_progress", "todo_id": "todo_sample"}) + "\n")
    monkeypatch.setattr(
        runtime,
        "capabilities",
        lambda: [
            {
                "agent_id": "codex",
                "available": True,
                "adapter_kind": "codex_app_server",
            },
        ],
    )
    try:
        session, _ = runtime.open_session(
            goal_id="loopx-manager",
            agent_id="codex",
            work_dir=tmp_path,
            objective="manager",
            mode="new",
            channel_id="manager",
        )
        turn, _ = runtime.submit_turn(
            session_id=session["session_id"],
            client_turn_id="fixture",
            message="Inspect sample task",
            work_dir=tmp_path,
            objective="manager",
        )
        done = runtime.wait_for_turn(
            session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=10
        )
        assert done["status"] == "completed", done
        # An interactive endpoint keeps the on-demand read: no inline source read.
        assert collected == [{"include_details": False, "remote_evidence": False}]
        events = store.events_after(session["session_id"], turn["turn_id"], None)
        reads = [e for e in events if e["kind"] == "manager.evidence_read"]
        if read_view == "report_source":
            assert len(reads) > 1 and reads[-1]["payload"]["view"] == "report_source"
            assert reads[-1]["payload"]["source"]["window_bounds"] == "[start,end)"
        elif read_view == "agent_route":
            assert len(reads) == 1
            assert reads[0]["payload"]["reason"] == "no_binding"
            assert reads[0]["payload"]["authority"] == "locator_only"
        else:
            assert len(reads) == 1
            assert reads[0]["payload"]["rows"][0]["agent_id" if read_view == "agents" else "todo_id"] == ("review-worker" if read_view == "agents" else "todo_sample")
    finally:
        runtime.close()


def test_stopped_goals_are_opt_in_but_stale_active_remains_visible(tmp_path):
    tool, _ = inspector(tmp_path)
    tool.context['goals'] = [
        {'goal_id': 'alpha', 'activation_state': 'active', 'quality': 'stale'},
        {'goal_id': 'old', 'activation_state': 'stopped', 'quality': 'omitted'},
        {'goal_id': 'unknown', 'activation_state': 'unknown', 'quality': 'unreadable'},
    ]
    index = manager_index(tool.context)
    assert [r['goal_id'] for r in index['goals']] == ['alpha', 'unknown']
    assert index['stopped_goals_excluded'] == 1
    tool.registry_path.write_text(json.dumps({"goals": [
        {"id": row["goal_id"], "activation_state": row["activation_state"]}
        for row in tool.context["goals"]
    ]}))
    current = tool.read(TOOL_NAME, {'view': 'portfolio'})
    assert current['matched'] == 2
    history = tool.read(TOOL_NAME, {'view': 'portfolio', 'include_stopped': True})
    assert history['matched'] == 3
    explicit = tool.read(TOOL_NAME, {'view': 'portfolio', 'goal_id': 'old'})
    assert explicit['rows'][0]['activation_state'] == 'stopped'
