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


def inspector(tmp_path, scope=lambda: True):
    records = []
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
            ["view:must_be_one_of_sources,portfolio,todos,deliveries,handoffs,agents,agent_route"],
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
        ({"goal_id": "alpha"}, ["view:must_be_one_of_sources,portfolio,todos,deliveries,handoffs,agents,agent_route"]),
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
        "view:must_be_one_of_sources,portfolio,todos,deliveries,handoffs,agents,agent_route",
        "limit:must_be_an_integer_between_1_and_12",
        "days:only_for_view_deliveries",
    ]
    # The repair instruction names the tool the caller actually used.
    assert CONTEXT_TOOL_NAME in result["detail"]
    assert TOOL_NAME not in result["detail"]
    assert not records


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
        details, "list_goal_todos", lambda **_: {"ok": True, "todos": []}
    )
    tool, records = inspector(tmp_path, lambda: next(grants))
    assert tool.read(TOOL_NAME, {"view": "todos", "goal_id": "alpha"}) == {
        "ok": False,
        "error": "authorization_changed",
    }
    assert not records


@pytest.mark.parametrize('name', [TOOL_NAME, CONTEXT_TOOL_NAME])
def test_exact_todo_recovers_compacted_context_without_widening_scope(monkeypatch, tmp_path, name):
    monkeypatch.setattr(details, 'list_goal_todos', lambda **_: {'ok': True, 'todos': [
        {'todo_id': 'todo_report', 'status': 'done', 'text': 'Research report',
         'note': 'Context. ' * 60 + 'Do not publish.', 'resume_ready': False},
    ]})
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
    monkeypatch.setattr(details, 'list_goal_todos', lambda **_: {'ok': True, 'todos': [
        {'todo_id': 'todo_report', 'status': 'open', 'text': 'Research', 'note': 'x' * 25000},
    ]})
    tool, _ = inspector(tmp_path)
    tool.owner_scope = True
    result = tool.read(TOOL_NAME, {'view': 'todos', 'goal_id': 'alpha', 'todo_id': 'todo_report'})
    assert result['oversized_rows'] == [0]
    assert result['rows'][0]['status'] == 'oversized_record'
    assert 'x' * 25000 not in json.dumps(result)


def test_unavailable_is_unknown_and_large_portfolio_is_disclosed(tmp_path):
    tool, records = inspector(tmp_path)
    result = tool.read(TOOL_NAME, {"view": "deliveries", "goal_id": "alpha"})
    assert result["unknown"] and result["matched"] is None
    assert result["source"]["status"] == "unavailable"
    tool.context["goals"][0]["current_todos"] = {"body": "x" * 40000}
    index = manager_index(tool.context)
    assert len(json.dumps(index)) < 2000
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


@pytest.mark.parametrize("read_view", ["todos", "todos_exact", "agents", "agent_route"])
def test_manager_runtime_installs_tool_and_records_real_subprocess_read(
    monkeypatch, tmp_path, read_view
):
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_store import ChatSessionStore
    import loopx.chat_manager_context as context

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
        (tmp_path / "registry.json").write_text(json.dumps({"goals": [
            {"id": "alpha", "registered_agents": ["review-worker"]}
        ]}))
    if read_view == "agent_route":
        fake.write_text(fake.read_text().replace(
            "'view':'todos','goal_id':'alpha'",
            "'view':'agent_route','goal_id':'alpha','agent_id':'review-worker'").replace(
            "evidence['rows'][0]['title'] == 'Check the sample result'",
            "evidence['status'] == 'unavailable' and evidence['reason'] == 'no_binding' and evidence['host_delivery'] == 'not_attempted'"))
        (tmp_path / "registry.json").write_text(json.dumps({"goals": [
            {"id": "alpha", "registered_agents": ["review-worker"]}
        ]}))
    if read_view == "todos_exact":
        fake.write_text(fake.read_text().replace(
            "'view':'todos','goal_id':'alpha'", "'view':'todos','goal_id':'alpha','todo_id':'todo_sample'").replace(
            "evidence['rows'][0]['title'] == 'Check the sample result'",
            "evidence['rows'][0]['continuation'].endswith('Keep this a draft; do not publish.')"))
    fake.chmod(0o755)
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
        lambda **_: {
            "ok": True,
            "todos": [
                {
                    "todo_id": "todo_sample",
                    "text": "Check the sample result",
                    "status": "open",
                    "note": "Public background. " * 30 + "Keep this a draft; do not publish.",
                },
            ],
        },
    )
    store = ChatSessionStore(tmp_path / "runtime" / "chat")
    runtime = ChatRuntimeController(
        store=store, codex_bin=str(fake), registry_path=tmp_path / "registry.json"
    )
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
        assert len(reads) == 1
        if read_view == "agent_route":
            assert reads[0]["payload"]["reason"] == "no_binding"
            assert reads[0]["payload"]["authority"] == "locator_only"
        else:
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
    current = tool.read(TOOL_NAME, {'view': 'portfolio'})
    assert current['matched'] == 2
    history = tool.read(TOOL_NAME, {'view': 'portfolio', 'include_stopped': True})
    assert history['matched'] == 3
    explicit = tool.read(TOOL_NAME, {'view': 'portfolio', 'goal_id': 'old'})
    assert explicit['rows'][0]['activation_state'] == 'stopped'
