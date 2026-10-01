"""Handoff states, durable receipts and audience separation through real stores."""

import json
import subprocess
import sys
from argparse import Namespace

import pytest

import test_manager_context_handoff as handoff_tests
from loopx.capabilities.manager_context import (
    _hash,
    _root,
    _read,
    _write,
    deliver,
    acknowledge,
    pending,
    turn_start_hook,
    register_ingress,
    POLICY_SCHEMA,
)
from loopx.capabilities.manager_context.tracking import query, record_read, link
from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME
from loopx.cli_commands.manager_inbox import handle_manager_inbox


@pytest.fixture
def fixture(tmp_path):
    return handoff_tests.fixture.__wrapped__(tmp_path)


def test_delivery_read_decision_are_separate_and_restart_safe(fixture, capsys):
    root, registry, session, turn, target = fixture
    rid = deliver(root, registry, session=session, turn=turn, request=target)[
        "request_id"
    ]

    def status():
        return query(root, registry, goal_ids=["research"], owner_scope=True)["rows"][0]

    first = status()
    assert first["delivery"]["at"] and first["read"]["status"] == "not_recorded"
    turn_start_hook(root, registry, "research", "worker").producer()
    assert status()["read"]["status"] == "not_recorded"
    args = Namespace(manager_inbox_action="read", goal_id="research", agent_id="worker")
    assert handle_manager_inbox(args, registry, root) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["message"] == turn["message"]
    read_at = status()["read"]["at"]
    record_read(root, pending(root, "research", "worker")["items"])
    assert status()["read"]["at"] == read_at
    acknowledge(root, "research", "worker", rid, "adopt", "Investigate independently")
    at = status()["decision"]["at"]
    acknowledge(root, "research", "worker", rid, "adopt", "Investigate independently")
    assert status()["decision"]["at"] == at
    assert status()["decision"]["status"] == "adopt"
    assert status()["linked_todos"] == []
    assert deliver(root, registry, session=session, turn=turn, request=target)[
        "replayed"
    ]
    assert status()["delivery"]["at"] == first["delivery"]["at"]


def test_legacy_decision_is_not_a_fabricated_read(fixture):
    root, registry, session, turn, target = fixture
    rid = deliver(root, registry, session=session, turn=turn, request=target)[
        "request_id"
    ]
    path = _root(root) / "entries" / _hash(target) / (rid + ".json")
    old = _read(path)
    old.pop("delivered_at")
    old.pop("source_channel")
    _write(path, old)
    _write(
        _root(root) / "decisions" / (rid + ".json"),
        dict(request_id=rid, **target, decision="adopt", reason="Legacy"),
    )
    assert deliver(root, registry, session=session, turn=turn, request=target)[
        "replayed"
    ]
    row = query(root, registry, goal_ids=["research"], owner_scope=True)["rows"][0]
    assert row["delivery"]["at"] is None and row["decision"]["at"] is None
    assert row["read"]["status"] == "decision_exists_read_receipt_missing"
    assert row["read"]["at"] is None


def test_external_query_is_exact_audience_not_just_goal(fixture):
    root, registry, session, turn, target = fixture
    web = deliver(root, registry, session=session, turn=turn, request=target)[
        "request_id"
    ]
    ids = {}
    for channel in ["manager.external.one", "manager.external.two"]:
        external = dict(session, channel_id=channel)
        incoming = dict(turn, origin="lark", client_turn_id=channel)
        _write(
            _root(root) / "policy.json",
            {
                "schema_version": POLICY_SCHEMA,
                "sources": {channel: {"sender_ids": ["owner"], "targets": [target]}},
            },
        )
        register_ingress(
            root,
            session_id=external["session_id"],
            client_turn_id=channel,
            channel=channel,
            sender_id="owner",
            message=turn["message"],
            source_id="lark:" + channel,
        )
        rid = deliver(root, registry, session=external, turn=incoming, request=target)[
            "request_id"
        ]
        acknowledge(
            root, "research", "worker", rid, "adopt", "Private receiver rationale"
        )
        ids[channel] = rid

    def tool(scope=lambda: True):
        return ManagerInspection(
            context={"goals": [{"goal_id": "research"}]},
            registry_path=registry,
            runtime_root=root,
            owner_scope=False,
            scope_valid=scope,
            record=lambda _: None,
            channel_id="manager.external.one",
        )

    rows = tool().read(TOOL_NAME, {"view": "handoffs"})["rows"]
    assert [r["request_id"] for r in rows] == [ids["manager.external.one"]]
    assert "Private receiver rationale" not in json.dumps(rows)
    assert turn["message"] not in json.dumps(rows)
    assert tool().read(TOOL_NAME, {"view": "handoffs", "request_id": web})["rows"] == []
    revoked = iter([True, False])
    assert (
        tool(lambda: next(revoked)).read(TOOL_NAME, {"view": "handoffs"})["error"]
        == "authorization_changed"
    )
    # Legacy same-source provenance still resolves; changing audience is not inferred.
    path = (
        _root(root)
        / "entries"
        / _hash(target)
        / (ids["manager.external.one"] + ".json")
    )
    old = _read(path)
    old.pop("source_channel")
    _write(path, old)
    assert tool().read(TOOL_NAME, {"view": "handoffs"})["included"] == 1


@pytest.mark.parametrize("tid", [
    "todo_123456789abc", "todo_123456789abcdef0123456789",
    "todo_work-item_123", "todo_abc", "todo_" + "a" * 64,
])
def test_links_use_core_state_and_do_not_copy_progress(fixture, monkeypatch, tid):
    import loopx.capabilities.manager_context.tracking as tracking

    root, registry, session, turn, target = fixture
    rid = deliver(root, registry, session=session, turn=turn, request=target)[
        "request_id"
    ]
    core = {
        "ok": True,
        "todos": [
            {
                "todo_id": tid,
                "claimed_by": "worker",
                "text": "Evaluate hypothesis",
                "status": "open",
            }
        ],
    }
    monkeypatch.setattr(tracking, "list_goal_todos", lambda **_: core)
    ref = "sha256:" + "a" * 64
    link(root, registry, "research", "worker", rid, [tid], [ref])
    path = _root(root) / "links" / (rid + ".json")
    original = path.read_bytes()
    link(root, registry, "research", "worker", rid, [tid], [ref])
    assert path.read_bytes() == original
    core["todos"][0]["status"] = "done"
    row = query(root, registry, goal_ids=["research"], owner_scope=True)["rows"][0]
    assert row["linked_todos"][0]["status"] == "done"
    assert row["decision"]["status"] == "not_recorded"
    assert row["evidence_refs"] == [ref]
    with pytest.raises(ValueError):
        link(root, registry, "research", "worker", rid, ["todo_ffffffffffff"], [])
    with pytest.raises(ValueError):
        link(root, registry, "research", "worker", rid, [], ["/private/raw.txt"])
    assert "Evaluate hypothesis" not in path.read_text()


@pytest.mark.parametrize("tid", [
    "todo_ab", "todo_" + "a" * 65, "todo_../escape", "todo_ABC",
    " todo_abc", "todo_abc\n", "task_abc",
])
def test_invalid_link_ids_fail_before_core_read_or_receipt(fixture, monkeypatch, tid):
    import loopx.capabilities.manager_context.tracking as tracking

    root, registry, session, turn, target = fixture
    rid = deliver(root, registry, session=session, turn=turn, request=target)["request_id"]
    monkeypatch.setattr(tracking, "list_goal_todos", lambda **_: pytest.fail("invalid id read Core"))
    with pytest.raises(ValueError, match="invalid Core Todo id"):
        link(root, registry, "research", "worker", rid, [tid], [])
    assert not (_root(root) / "links" / (rid + ".json")).exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_links_generated_core_id_and_reads_live_state(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority, isolate_sqlite_runtime, promoted_create_fixture,
    )
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, state = promoted_create_fixture(tmp_path, provider=provider)
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    other_state = tmp_path / "other-state.md"
    other_state.write_text("# Goal\n\n## Agent Todo\n")
    data["goals"].append({
        "id": "goal-b", "repo": str(tmp_path), "state_file": str(other_state),
        "coordination": {"registered_agents": ["agent-a"]},
    })
    registry.write_text(json.dumps(data))
    initialize_canonical_authority(
        root, "goal-b", build_todo_runtime_shadow_projection(goal_id="goal-b", todos=[]),
        state_path=other_state, provider=provider,
    )

    def cli(*args, ok=True):
        proc = subprocess.run(
            [sys.executable, "-m", "loopx.entrypoint", "--format", "json",
             "--registry", str(registry), "--runtime-root", str(root), *args],
            capture_output=True, text=True, timeout=45,
        )
        assert proc.returncode == (0 if ok else 1), (proc.stdout, proc.stderr)
        result = json.loads(proc.stdout)
        assert result["ok"] is ok
        return result

    def add(owner, goal="goal-a"):
        return cli("todo", "add", "--goal-id", goal, "--role", "agent",
                   "--text", f"Verify linked work for {owner}", "--claimed-by", owner)["todo_id"]

    tid, foreign = add("agent-a"), add("agent-b")
    # The real Core creation path emits modern ids, not a hand-built legacy fixture.
    assert len(tid.removeprefix("todo_")) == 24
    rid = deliver(
        root, registry,
        session={"session_id": "manager-session", "channel_id": "manager"},
        turn={"client_turn_id": "link-request", "origin": "web", "message": "Verify work"},
        request={"goal_id": "goal-a", "agent_id": "agent-a"},
    )["request_id"]
    inbox = ("--goal-id", "goal-a", "--agent-id", "agent-a", "--request-id", rid)
    linked = cli("manager-inbox", "link", *inbox, "--related-todo-id", tid)
    assert linked["todo_ids"] == [tid]
    path = _root(root) / "links" / (rid + ".json")
    original = path.read_bytes()
    cli("manager-inbox", "link", *inbox, "--related-todo-id", tid)
    assert path.read_bytes() == original
    for rejected in (foreign, add("agent-a", "goal-b"), "todo_unknown-canonical-id"):
        failure = cli("manager-inbox", "link", *inbox, "--related-todo-id", rejected, ok=False)
        assert failure["error"] == "linked Todo must belong to the receiving Agent"
        assert path.read_bytes() == original
    cli("todo", "update", "--goal-id", "goal-a", "--todo-id", tid,
        "--agent-id", "agent-a", "--text", "Verified current work")
    row = cli("manager-inbox", "status", *inbox)["rows"][0]
    assert [(t["todo_id"], t["title"], t["status"]) for t in row["linked_todos"]] == [
        (tid, "Verified current work", "open")
    ]
    assert "Verified current work" not in path.read_text()
    assert state.exists()


def test_pagination_and_conflicts_do_not_erase_gaps(fixture):
    root, registry, session, turn, target = fixture
    for i in range(14):
        deliver(
            root,
            registry,
            session=session,
            turn=dict(turn, client_turn_id=str(i)),
            request=target,
        )
    first = query(root, registry, goal_ids=["research"], owner_scope=True, limit=12)
    second = query(
        root, registry, goal_ids=["research"], owner_scope=True, offset=12, limit=12
    )
    assert first["matched"] == 14 and len(second["rows"]) == 2
    rid = first["rows"][0]["request_id"]
    _write(
        _root(root) / "decisions" / (rid + ".json"),
        dict(request_id=rid, goal_id="other", agent_id="peer", decision="adopt"),
    )
    row = query(
        root, registry, goal_ids=["research"], owner_scope=True, request_id=rid
    )["rows"][0]
    assert row["decision"]["status"] == "not_recorded"
    assert row["warnings"] == ["decisions_unreadable_or_conflicting"]
    links_path = _root(root) / "links" / (rid + ".json")
    _write(
        links_path,
        dict(request_id=rid, **target, todo_ids=[], evidence_ids=["/private/secret"]),
    )
    row = query(
        root, registry, goal_ids=["research"], owner_scope=True, request_id=rid
    )["rows"][0]
    assert row["evidence_refs"] == []
    assert "links_unreadable_or_conflicting" in row["warnings"]
    with pytest.raises(ValueError, match="links_unreadable_or_conflicting"):
        link(root, registry, "research", "worker", rid, [], ["sha256:" + "b" * 64])
    assert (
        query(root, registry, goal_ids=["research"], owner_scope=True, agent_id="peer")[
            "rows"
        ]
        == []
    )
