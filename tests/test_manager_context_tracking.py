"""Handoff states, durable receipts and audience separation through real stores."""

import json
import shlex
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
from loopx.capabilities.manager_context.tracking import query, record_read
from loopx.control_plane.collaboration.links import link
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
    hook = turn_start_hook(root, registry, "research", "worker")
    assert hook.producer()["agent_read_required"] is True
    argv = shlex.split(hook.required_read["command"])
    assert argv[argv.index("manager-inbox") + 1] == "read"
    assert status()["read"]["status"] == "not_recorded"
    args = Namespace(manager_inbox_action="read", goal_id="research", agent_id="worker")
    assert handle_manager_inbox(args, registry, root) == 0
    read = json.loads(capsys.readouterr().out)
    assert read["items"][0]["message"] == turn["message"]
    assert "Explain unfamiliar terms on first use" in read["instruction"]
    assert "Preserve the original author, audience and facts" in read["instruction"]
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


def test_next_turn_requires_reading_a_durable_native_child_result(fixture, capsys):
    from loopx.capabilities.multi_subagent.native_child_receipts import record_native_child
    from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path

    root, registry, _session, _turn, _target = fixture
    turn = "turn-native-result"
    append_rollout_event(rollout_event_log_path(root, "research"), build_rollout_event(
        goal_id="research", event_kind="quota_should_run", agent_id="worker",
        run_id=turn, todo_id="todo_native_1", status="normal_run",
        details={"todo_id": "todo_native_1", "settlement_effect_id": f"research:worker:todo_native_1:{turn}"},
    ))
    identity = dict(runtime_root=root, goal_id="research", agent_id="worker",
                    turn_instance_id=turn, operation_id="operation-native-result",
                    configured_limit=3, execute=True, _host_observed=True)
    record_native_child(**identity, stage="decision", operation="spawn",
                        outcome="started", entrypoint_id="codex_native_tools")
    record_native_child(**identity, stage="result", outcome="completed",
                        _host_result_text="The independent check found one actionable boundary.")
    hook = turn_start_hook(root, registry, "research", "worker")
    observed = hook.producer()
    assert observed["status"] == "observed"
    assert observed["agent_read_required"] is True
    assert observed["observation_count"] == 1

    args = Namespace(manager_inbox_action="read", goal_id="research", agent_id="worker")
    assert handle_manager_inbox(args, registry, root) == 0
    read = json.loads(capsys.readouterr().out)
    [result] = read["native_child_results"]["items"]
    assert result["operation_id"] == identity["operation_id"]
    assert result["text"] == "The independent check found one actionable boundary."
    assert "native-child acknowledge-result" in result["acknowledge_command"]

    # Delivery does not consume the result; without acknowledgement, restart
    # projects the same stable identity again on the next Turn.
    replay = turn_start_hook(root, registry, "research", "worker").producer()
    assert replay["agent_read_required"] is True
    assert replay["observation_count"] == 1


def test_native_result_continuation_runs_via_manager_inbox_cli(fixture, capsys):
    from loopx.capabilities.multi_subagent.native_child_receipts import record_native_child
    from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path

    root, registry, _session, _turn, _target = fixture
    turn = "turn-native-result-continuation"
    append_rollout_event(rollout_event_log_path(root, "research"), build_rollout_event(
        goal_id="research", event_kind="quota_should_run", agent_id="worker",
        run_id=turn, todo_id="todo_native_1", status="normal_run",
        details={"todo_id": "todo_native_1", "settlement_effect_id": f"research:worker:todo_native_1:{turn}"},
    ))
    identity = dict(runtime_root=root, goal_id="research", agent_id="worker",
                    turn_instance_id=turn, operation_id="operation-native-result",
                    configured_limit=3, execute=True, _host_observed=True)
    record_native_child(**identity, stage="decision", operation="spawn",
                        outcome="started", entrypoint_id="codex_native_tools")
    record_native_child(**identity, stage="result", outcome="completed",
                        _host_result_text="Continuation preserves the original result.")
    metadata, = (root / ".local" / "native-child-result-inbox").glob("*/*.json")
    from hashlib import sha256
    scope = sha256(json.dumps(["native-child-result", str(metadata.parent.resolve())],
                              separators=(",", ":")).encode()).hexdigest()
    args = Namespace(manager_inbox_action="read", goal_id="research", agent_id="worker",
                     cursor=f"native-child:1:{scope}:ncr_{'0' * 32}")
    assert handle_manager_inbox(args, registry, root) == 0
    result = json.loads(capsys.readouterr().out)
    [native] = result["native_child_results"]["items"]
    assert native["result_id"] == metadata.stem
    assert result["items"] == [] and "unfinished_delegations" not in result
    assert result["native_child_results"]["next_cursor"] is None
    readback = json.loads(metadata.read_bytes())
    assert readback["delivery"]["delivery_count"] == 1


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
                "sources": {channel: {"local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [target]}},
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
    import loopx.control_plane.collaboration.links as tracking

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
    import loopx.control_plane.collaboration.links as tracking

    root, registry, session, turn, target = fixture
    rid = deliver(root, registry, session=session, turn=turn, request=target)["request_id"]
    monkeypatch.setattr(tracking, "list_goal_todos", lambda **_: pytest.fail("invalid id read Core"))
    with pytest.raises(ValueError, match="invalid Core Todo id"):
        link(root, registry, "research", "worker", rid, [tid], [])
    assert not (_root(root) / "links" / (rid + ".json")).exists()


def test_external_handoff_keeps_links_without_reading_receiver_private_work(fixture, monkeypatch):
    import loopx.control_plane.collaboration.links as links

    root, registry, session, turn, target = fixture
    channel = "manager.external.one"
    external = dict(session, channel_id=channel)
    incoming = dict(turn, origin="lark")
    _write(_root(root) / "policy.json", {
        "schema_version": POLICY_SCHEMA,
        "sources": {channel: {"local_delivery_scope": "selected", "sender_ids": ["owner"], "targets": [target]}},
    })
    register_ingress(root, session_id=external["session_id"],
                     client_turn_id=turn["client_turn_id"], channel=channel,
                     sender_id="owner", message=turn["message"], source_id="lark:" + channel)
    rid = deliver(root, registry, session=external, turn=incoming, request=target)["request_id"]
    monkeypatch.setattr(links, "list_goal_todos", lambda **_: {
        "ok": True, "todos": [{"todo_id": "todo_private", "claimed_by": "worker",
                                "status": "blocked", "text": "Confidential acquisition decision"}],
    })
    ref = "sha256:" + "b" * 64
    link(root, registry, "research", "worker", rid, ["todo_private"], [ref])
    owner = query(root, registry, goal_ids=["research"], owner_scope=True)["rows"][0]
    assert owner["linked_todos"][0]["title"] == "Confidential acquisition decision"
    assert owner["linked_todos"][0]["status"] == "blocked"
    policy = _read(_root(root) / "policy.json")
    policy["sources"][channel]["evidence_goal_ids"] = ["research"]
    _write(_root(root) / "policy.json", policy)
    # An owner-authorized group inherits its existing Core read grant. Being
    # external must not hide facts that its operator already authorized.
    granted = query(root, registry, goal_ids=["research"], owner_scope=False,
                    channel_id=channel)["rows"][0]
    assert granted["linked_todos"] == owner["linked_todos"]
    policy["sources"][channel]["evidence_goal_ids"] = []
    _write(_root(root) / "policy.json", policy)
    monkeypatch.setattr(links, "list_goal_todos", lambda **_: pytest.fail("external audience read private Core"))
    external = query(root, registry, goal_ids=["research"], owner_scope=False,
                     channel_id=channel)["rows"][0]
    assert external["linked_todos"] == [{"todo_id": "todo_private"}]
    assert external["evidence_refs"] == [ref]
    assert "Confidential acquisition decision" not in json.dumps(external)


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
    # The receiver has existing work. Merely reading this request cannot adopt
    # it or associate that work by title, presence, or common ownership.
    first = cli("manager-inbox", "read", "--goal-id", "goal-a", "--agent-id", "agent-a")["items"][0]
    assert first["receiver_followthrough"]["assessment_required"] is True
    assert first["receiver_followthrough"]["linked_todos"] == []
    assert cli("manager-inbox", "status", *inbox)["rows"][0]["decision"]["status"] == "not_recorded"
    cli("manager-inbox", "acknowledge", *inbox, "--decision", "adopt", "--reason", "Resume this existing work")
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
        (tid, "[P1] Verified current work", "open")
    ]
    assert "Verified current work" not in path.read_text()
    resumed = cli("manager-inbox", "read", "--goal-id", "goal-a", "--agent-id", "agent-a")["items"][0]
    follow = resumed["receiver_followthrough"]
    assert follow["recorded_decision"] == "adopt" and follow["step"] == "review_request_work"
    assert [(t["todo_id"], t["title"], t["status"]) for t in follow["linked_todos"]] == [
        (tid, "[P1] Verified current work", "open")
    ]
    assert follow["answer_owed"] is True
    # Evidence-only linking must survive the next ordinary CLI receiver read.
    ref = "sha256:" + "c" * 64
    cli("manager-inbox", "link", *inbox, "--evidence-id", ref)
    follow = cli("manager-inbox", "read", "--goal-id", "goal-a", "--agent-id", "agent-a")["items"][0]["receiver_followthrough"]
    assert follow["evidence_refs"] == [ref]

    # Archiving completed work changes its storage lane, not the request's
    # explicit link or the receiver's need to return an answer.
    link_bytes = path.read_bytes()
    cli("todo", "complete", "--goal-id", "goal-a", "--todo-id", tid,
        "--agent-id", "agent-a", "--evidence", "Verified isolated result", "--no-follow-up")
    cli("todo", "archive-completed", "--goal-id", "goal-a",
        "--max-active-done", "0", "--execute")
    archived = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", tid)
    assert archived["todo"]["archive_state"] == "archive"
    assert archived["todo"]["status"] == "done"
    follow = cli("manager-inbox", "read", "--goal-id", "goal-a", "--agent-id", "agent-a")["items"][0]["receiver_followthrough"]
    assert follow["step"] == "review_request_work"
    assert [(t["todo_id"], t["title"], t["status"]) for t in follow["linked_todos"]] == [
        (tid, "[P1] Verified current work", "done")
    ]
    assert follow["recorded_decision"] == "adopt" and follow["answer_owed"] is True
    assert cli("manager-inbox", "status", *inbox)["rows"][0]["linked_todos"] == follow["linked_todos"]
    assert path.read_bytes() == link_bytes
    assert cli("todo", "list", "--goal-id", "goal-a", "--todo-id", tid)["authority_read"]["provider_revision"] == archived["authority_read"]["provider_revision"]
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
    from loopx.control_plane.collaboration.peers import read_inbox
    receiver = next(item for item in read_inbox(root, registry, "research", "worker")["items"]
                    if item["request_id"] == rid)["receiver_followthrough"]
    assert receiver["evidence_refs"] == []
    assert receiver["step"] == "recover_evidence"
    with pytest.raises(ValueError, match="links_unreadable_or_conflicting"):
        link(root, registry, "research", "worker", rid, [], ["sha256:" + "b" * 64])
    assert (
        query(root, registry, goal_ids=["research"], owner_scope=True, agent_id="peer")[
            "rows"
        ]
        == []
    )


def test_receiver_keeps_unavailable_work_unknown_without_replaying_or_hiding_other_requests(fixture, monkeypatch):
    from loopx.control_plane.collaboration.peers import read_inbox
    import loopx.control_plane.collaboration.links as links

    root, registry, session, turn, target = fixture
    rid = deliver(root, registry, session=session, turn=turn, request=target)["request_id"]
    other = deliver(root, registry, session=session,
                    turn=dict(turn, client_turn_id="other-request"), request=target)["request_id"]
    core = {"ok": True, "todos": [{"todo_id": "todo_current", "claimed_by": "worker",
                                       "status": "open", "text": "Current work"}]}
    reads = []
    def core_read(**kwargs):
        reads.append(kwargs["goal_id"])
        return core
    monkeypatch.setattr(links, "list_goal_todos", core_read)
    acknowledge(root, "research", "worker", rid, "adopt", "Use current work")
    link(root, registry, "research", "worker", rid, ["todo_current"], [])
    path = _root(root) / "links" / (rid + ".json")
    original = path.read_bytes()
    core["todos"][0].update(claimed_by="peer", text="Other owner's private work")
    reads.clear()
    page = {item["request_id"]: item for item in read_inbox(root, registry, "research", "worker")["items"]}
    unavailable = page[rid]["receiver_followthrough"]
    assert unavailable["step"] == "recover_evidence"
    assert unavailable["linked_todos"][0]["status"] == "unknown"
    assert "Other owner's private work" not in json.dumps(page)
    assert page[other]["receiver_followthrough"]["step"] == "assess_request"
    assert reads == ["research"]
    assert path.read_bytes() == original
    row = query(root, registry, goal_ids=["research"], owner_scope=True, request_id=rid)["rows"][0]
    assert row["linked_todos"][0]["status"] == "unknown"
    core["todos"][0].update(claimed_by="worker", text="api_key=synthetic-secret")
    page = {item["request_id"]: item for item in read_inbox(root, registry, "research", "worker")["items"]}
    assert page[rid]["receiver_followthrough"]["linked_todos"][0]["title"] is None
    core["todos"][0]["text"] = "😀" * 2000
    page = {item["request_id"]: item for item in read_inbox(root, registry, "research", "worker")["items"]}
    assert len(page[rid]["receiver_followthrough"]["linked_todos"][0]["title"]) < 430
    path.write_text("{damaged")
    page = {item["request_id"]: item for item in read_inbox(root, registry, "research", "worker")["items"]}
    assert page[rid]["receiver_followthrough"]["step"] == "recover_evidence"
    assert page[rid]["warnings"] == ["links_unreadable_or_conflicting"]
    assert page[other]["receiver_followthrough"]["step"] == "assess_request"
    assert path.read_text() == "{damaged"


def test_history_links_are_scoped_and_do_not_hide_other_work(fixture, monkeypatch):
    from loopx.control_plane.collaboration.peers import read_inbox
    import loopx.control_plane.collaboration.links as links

    root, registry, session, turn, target = fixture
    active = {"todo_id": "todo_active", "claimed_by": "worker", "status": "open", "text": "Current work"}
    history = {"todo_id": "todo_history", "claimed_by": "worker", "status": "done", "text": "Verified history"}
    reads, unavailable = [], False

    def core_read(**kwargs):
        tid = kwargs.get("todo_id")
        reads.append(tid)
        if tid is None:
            return {"ok": True, "todos": [active]}
        assert tid == "todo_history"
        if unavailable:
            raise ValueError("Exact source unavailable")
        return {"ok": True, "todo": history}

    monkeypatch.setattr(links, "list_goal_todos", core_read)
    ids = []
    for key, tid in [("active", "todo_active"), ("history-1", "todo_history"), ("history-2", "todo_history")]:
        rid = deliver(root, registry, session=session,
                      turn=dict(turn, client_turn_id=key), request=target)["request_id"]
        acknowledge(root, "research", "worker", rid, "adopt", "Use existing work")
        link(root, registry, "research", "worker", rid, [tid], [])
        ids.append(rid)
    items = {item["request_id"]: item for item in read_inbox(root, registry, "research", "worker")["items"]}

    def observe():
        reads.clear()
        rows = links.receiver_work_observations(root, registry, [items[rid] for rid in ids])
        # A later request can need history after the Goal queue is cached;
        # repeated links need no second exact lookup or complete history export.
        assert reads == [None, "todo_history"]
        assert rows[0]["linked_todos"][0]["status"] == "open"
        assert rows[0]["evidence_unavailable"] is False
        return rows

    assert [row["linked_todos"][0]["status"] for row in observe()] == ["open", "done", "done"]
    unavailable = True
    missing = observe()[1:]
    assert all(row["evidence_unavailable"] for row in missing)
    assert all(row["linked_todos"][0]["status"] == "unknown" for row in missing)
    assert all(row["linked_todos"][0]["title"] is None for row in missing)
    unavailable = False
    assert [row["linked_todos"][0]["status"] for row in observe()] == ["open", "done", "done"]
    history["claimed_by"] = "peer"
    history["text"] = "Other receiver's private work"
    foreign = observe()[1:]
    assert all(row["linked_todos"][0]["status"] == "unknown" for row in foreign)
    assert "Other receiver's private work" not in json.dumps(foreign)
    with pytest.raises(ValueError, match="must belong"):
        link(root, registry, "research", "worker", ids[1], ["todo_history"], [])



def test_handoff_query_batches_goal_lifetime_scope_per_goal(fixture, monkeypatch):
    import loopx.capabilities.manager_context.tracking as tracking

    root, registry, session, turn, target = fixture
    expected_ids = []
    for idx in range(3):
        rid = deliver(
            root,
            registry,
            session=session,
            turn=dict(turn, client_turn_id=f"batch-{idx}"),
            request=target,
        )["request_id"]
        expected_ids.append(rid)
    target_dir = next((_root(root) / "entries").iterdir())
    (target_dir / "corrupt.json").write_text("{damaged", encoding="utf-8")

    real_scope = tracking.collaboration_goal_scope
    scope_calls = []

    def spy_scope(reg_path, *, goal_id, agents, caller_goal_ref=None, **kwargs):
        scope_calls.append(goal_id)
        return real_scope(
            reg_path,
            goal_id=goal_id,
            agents=agents,
            caller_goal_ref=caller_goal_ref,
            **kwargs,
        )

    monkeypatch.setattr(tracking, "collaboration_goal_scope", spy_scope)
    result = query(root, registry, goal_ids=["research"], owner_scope=True)

    assert scope_calls == ["research"]
    assert len(result["rows"]) == 3
    assert {row["request_id"] for row in result["rows"]} == set(expected_ids)
    assert result["coverage"]["unreadable"] == 1


def test_handoff_query_fails_fast_per_goal_on_lock_timeout(tmp_path, monkeypatch):
    import loopx.capabilities.manager_context.tracking as tracking
    from loopx.file_lock import LockAcquireTimeoutError

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "contended",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["worker"]},
                    },
                    {
                        "id": "healthy",
                        "repo": str(tmp_path),
                        "coordination": {"registered_agents": ["worker"]},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    session = {"session_id": "manager-session", "channel_id": "manager"}
    for idx in range(5):
        deliver(
            tmp_path,
            registry,
            session=session,
            turn={"client_turn_id": f"contended-{idx}", "origin": "web", "message": "Check lock."},
            request={"goal_id": "contended", "agent_id": "worker"},
        )
    healthy_ids = []
    for idx in range(2):
        healthy_ids.append(
            deliver(
                tmp_path,
                registry,
                session=session,
                turn={"client_turn_id": f"healthy-{idx}", "origin": "web", "message": "Check healthy."},
                request={"goal_id": "healthy", "agent_id": "worker"},
            )["request_id"]
        )

    real_scope = tracking.collaboration_goal_scope
    calls = []

    def contended_scope(reg_path, *, goal_id, agents, caller_goal_ref=None, **kwargs):
        calls.append(goal_id)
        if goal_id == "contended":
            raise LockAcquireTimeoutError(
                incident={"holder": {}, "operator_action": {"action": "inspect_lock_holder"}},
                incident_recorded=True,
                incident_channel="test",
            )
        return real_scope(
            reg_path,
            goal_id=goal_id,
            agents=agents,
            caller_goal_ref=caller_goal_ref,
            **kwargs,
        )

    monkeypatch.setattr(tracking, "collaboration_goal_scope", contended_scope)
    result = query(tmp_path, registry, goal_ids=["contended", "healthy"], owner_scope=True)

    assert calls.count("contended") == 1
    assert calls.count("healthy") == 1
    assert {row["request_id"] for row in result["rows"]} == set(healthy_ids)
    assert result["coverage"]["unreadable"] == 5


def test_handoff_query_batched_source_session_validates_per_row_goal_ref(tmp_path, monkeypatch):
    import loopx.capabilities.manager_context.tracking as tracking
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    instance_a = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    instance_b = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    registry = tmp_path / ".loopx" / "registry.json"
    with source_session_registry_transaction(
        registry,
        operation="test_create_source_registry",
        create=lambda: {
            "profile_id": "source_session_v1",
            "goals": [
                {
                    "id": "delivery",
                    "repo": str(tmp_path),
                    "status": "active",
                    "goal_instance_id": instance_a,
                    "coordination": {"registered_agents": ["worker"]},
                }
            ],
            "session_bindings": [],
            "session_receipts": [],
            "lifetime_receipts": [],
        },
    ) as transaction:
        transaction.commit(transaction.payload_copy())

    session = {"session_id": "manager-session", "channel_id": "manager"}
    valid_ids = []
    for idx in range(2):
        valid_ids.append(
            deliver(
                tmp_path,
                registry,
                session=session,
                turn={"client_turn_id": f"valid-{idx}", "origin": "web", "message": "Valid instance."},
                request={"goal_id": "delivery", "agent_id": "worker"},
            )["request_id"]
        )
    stale_id = deliver(
        tmp_path,
        registry,
        session=session,
        turn={"client_turn_id": "stale", "origin": "web", "message": "Stale instance."},
        request={"goal_id": "delivery", "agent_id": "worker"},
    )["request_id"]
    stale_path = next((_root(tmp_path) / "entries").glob(f"*/{stale_id}.json"))
    stale_row = _read(stale_path)
    stale_row["goal_ref"] = {"goal_id": "delivery", "goal_instance_id": instance_b}
    _write(stale_path, stale_row)

    malformed_id = deliver(
        tmp_path,
        registry,
        session=session,
        turn={"client_turn_id": "malformed", "origin": "web", "message": "Malformed ref."},
        request={"goal_id": "delivery", "agent_id": "worker"},
    )["request_id"]
    malformed_path = next((_root(tmp_path) / "entries").glob(f"*/{malformed_id}.json"))
    malformed_row = _read(malformed_path)
    malformed_row["goal_ref"] = "bad-ref"
    _write(malformed_path, malformed_row)

    real_scope = tracking.collaboration_goal_scope
    scope_calls = []

    def spy_scope(reg_path, *, goal_id, agents, caller_goal_ref=None, **kwargs):
        scope_calls.append(goal_id)
        return real_scope(
            reg_path,
            goal_id=goal_id,
            agents=agents,
            caller_goal_ref=caller_goal_ref,
            **kwargs,
        )

    monkeypatch.setattr(tracking, "collaboration_goal_scope", spy_scope)
    result = query(tmp_path, registry, goal_ids=["delivery"], owner_scope=True)

    assert scope_calls == ["delivery"]
    assert {row["request_id"] for row in result["rows"]} == set(valid_ids)
    assert result["coverage"]["unreadable"] == 2


def _write_source_registry(registry_path, goals, *, runtime_root=None):
    from loopx.control_plane.projects.registry_codec import source_session_registry_transaction

    payload = {
        "profile_id": "source_session_v1",
        **({"common_runtime_root": str(runtime_root)} if runtime_root is not None else {}),
        "goals": [
            {
                "id": gid,
                "repo": str(registry_path.parent),
                "activation": {"schema_version": "loopx_goal_activation_v1", "state": "active"},
                "goal_instance_id": inst_id,
                "coordination": {"registered_agents": ["worker"]},
            }
            for gid, inst_id in goals
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
    }
    with source_session_registry_transaction(
        registry_path, operation="test_create_tracking_registry", create=lambda: payload
    ) as tx:
        tx.commit(tx.payload_copy())


def _write_tracking_entry(
    root,
    *,
    gid,
    inst_id,
    idx,
    channel="manager",
    delivered_at=None,
    include_goal_ref=True,
    dir_key=None,
):
    rid = f"{idx:064x}"
    goal_ref = {"goal_id": gid, "goal_instance_id": inst_id}
    target = (
        {"goal_ref": goal_ref, "agent_id": "worker"}
        if include_goal_ref
        else {"goal_id": gid, "agent_id": "worker"}
    )
    row = {
        "schema_version": "loopx_manager_context_entry_v2" if include_goal_ref else "loopx_manager_context_entry_v1",
        "request_id": rid,
        "goal_id": gid,
        "agent_id": "worker",
        **({"goal_ref": goal_ref} if include_goal_ref else {}),
        "source_id": f"web:{gid}:{idx}",
        "message": f"Tracking request {idx}",
        "instruction": "Tracking instruction",
        "delivered_at": delivered_at or f"2026-11-19T{(idx // 3600) % 24:02d}:{(idx // 60) % 60:02d}:{idx % 60:02d}+00:00",
        **({"source_channel": channel} if channel is not None else {}),
    }
    folder = _root(root) / "entries" / (dir_key or _hash(target))
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / f"{rid}.json", row)
    return rid, row


def test_query_bounds_registry_loads_and_lifecycle_rpcs_across_many_entries(tmp_path, monkeypatch):
    from loopx.capabilities.manager_context import tracking
    from loopx.control_plane.collaboration import goal_instance_scope as gis

    inst_a = "ginst_" + "a" * 32
    inst_b = "ginst_" + "b" * 32
    inst_hist = "ginst_" + "c" * 32
    inst_ignored = "ginst_" + "d" * 32
    root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    _write_source_registry(
        registry,
        [("goal-a", inst_a), ("goal-b", inst_b), ("goal-ignored", inst_ignored)],
    )

    expected_rows = []
    for i in range(50):
        channel = "manager.external.one" if i < 10 else "manager.external.two"
        _, row = _write_tracking_entry(root, gid="goal-a", inst_id=inst_a, idx=i, channel=channel)
        expected_rows.append(row)
    for i in range(50, 80):
        if i < 55:
            channel = None
            _write(
                _root(root) / "ingress" / f"{i:064x}.json",
                {"source_id": f"web:goal-a:{i}", "channel": "manager.external.one"},
            )
        else:
            channel = "manager.external.two"
        _, row = _write_tracking_entry(root, gid="goal-a", inst_id=inst_hist, idx=i, channel=channel)
        expected_rows.append(row)
    for i in range(80, 100):
        _, row = _write_tracking_entry(root, gid="goal-b", inst_id=inst_b, idx=i, channel="manager.external.two")
        expected_rows.append(row)
    for i in range(100, 120):
        _write_tracking_entry(root, gid="goal-ignored", inst_id=inst_ignored, idx=i, channel="manager.external.one")

    orig_load = gis.load_project_registry
    orig_scope = tracking.collaboration_goal_scope
    orig_rpc = gis.effect_runtime_result
    counts = {"load": 0, "scope": 0, "decide_rpc": 0}

    def counted_load(path):
        counts["load"] += 1
        return orig_load(path)

    def counted_scope(*args, **kwargs):
        counts["scope"] += 1
        return orig_scope(*args, **kwargs)

    def counted_rpc(method, params, **kwargs):
        if method == "collaboration.goal_instance.decide":
            counts["decide_rpc"] += 1
        return orig_rpc(method, params, **kwargs)

    monkeypatch.setattr(gis, "load_project_registry", counted_load)
    monkeypatch.setattr(tracking, "collaboration_goal_scope", counted_scope)
    monkeypatch.setattr(gis, "effect_runtime_result", counted_rpc)

    result = query(root, registry, goal_ids=["goal-a", "goal-b"], owner_scope=True, offset=5, limit=8)
    expected_rows.sort(key=lambda r: (r["delivered_at"], r["request_id"]), reverse=True)
    assert result["matched"] == 100
    assert result["coverage"] == {
        "scan_complete": True,
        "legacy_audience_scan_complete": True,
        "unreadable": 0,
    }
    assert [r["request_id"] for r in result["rows"]] == [r["request_id"] for r in expected_rows[5:13]]
    assert counts == {"load": 2, "scope": 2, "decide_rpc": 3}

    # Audience pre-filtering skips goal-b entirely before locks, registry, or RPCs.
    counts.update(load=0, scope=0, decide_rpc=0)
    external = query(
        root,
        registry,
        goal_ids=["goal-a", "goal-b"],
        owner_scope=False,
        channel_id="manager.external.one",
        offset=0,
        limit=12,
    )
    assert external["matched"] == 15
    assert len(external["rows"]) == 12
    assert counts == {"load": 1, "scope": 1, "decide_rpc": 2}

    # Legacy registry (profile_id != source_session_v1) makes zero lifecycle RPCs.
    legacy_root = tmp_path / "legacy-runtime"
    legacy_registry = tmp_path / "legacy-registry.json"
    legacy_registry.write_text(
        json.dumps(
            {
                "goals": [
                    {"id": "goal-a", "repo": str(tmp_path), "coordination": {"registered_agents": ["worker"]}},
                    {"id": "goal-b", "repo": str(tmp_path), "coordination": {"registered_agents": ["worker"]}},
                ]
            }
        )
    )
    for i in range(40):
        gid = "goal-a" if i < 20 else "goal-b"
        _write_tracking_entry(legacy_root, gid=gid, inst_id=inst_a, idx=i, include_goal_ref=False)
    counts.update(load=0, scope=0, decide_rpc=0)
    legacy_result = query(legacy_root, legacy_registry, goal_ids=["goal-a", "goal-b"], owner_scope=True, limit=10)
    assert legacy_result["matched"] == 40
    assert len(legacy_result["rows"]) == 10
    assert counts == {"load": 2, "scope": 2, "decide_rpc": 0}


def test_query_preserves_unreadable_and_lifecycle_rejection_parity_in_batches(tmp_path, monkeypatch):
    from loopx.capabilities.manager_context import tracking
    from loopx.capabilities.manager_context.roundtrip import report
    from loopx.chat_store import ChatSessionStore
    from loopx.control_plane.collaboration import goal_instance_scope as gis
    from loopx.control_plane.collaboration.request_control_source import execute_request_control

    inst_a = "ginst_" + "a" * 32
    inst_hist = "ginst_" + "b" * 32
    inst_mismatch = "ginst_" + "c" * 32
    inst_other = "ginst_" + "d" * 32
    root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    _write_source_registry(registry, [("goal-a", inst_a)], runtime_root=root)

    store = ChatSessionStore(root)
    session = store.create_session(
        goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="fixture", channel_id="manager",
    )
    turn, _ = store.create_turn(session["session_id"], client_turn_id="ctrl-1", message="Check controlled", origin="web")
    brief = {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Verify controlled readback",
        "context": "Keep current instance",
        "constraints": [],
        "inputs": [],
        "acceptance": ["Readback matches"],
        "return_requirement": "Return findings",
    }
    ctrl_receipt = deliver(
        root, registry, session=session, turn=turn,
        request={"goal_id": "goal-a", "agent_id": "worker", "brief": brief},
        source_store=store,
    )
    ctrl_rid = ctrl_receipt["request_id"]
    ctrl_view = execute_request_control(
        registry_path=registry, runtime_root=root, goal_ref=ctrl_receipt["goal_ref"],
        receiver_agent_id="worker", request_id=ctrl_rid, actor_agent_id=None,
        operation="inspect", execute=False,
    )["view"]
    revised = execute_request_control(
        registry_path=registry, runtime_root=root, goal_ref=ctrl_receipt["goal_ref"],
        receiver_agent_id="worker", request_id=ctrl_rid, actor_agent_id=None,
        operation="revise",
        parameters={
            "operation_id": "rev-1",
            "expected_revision": ctrl_view["revision"],
            "expected_digest": ctrl_view["revision_digest"],
            "brief": {**brief, "purpose": "Revised purpose"},
            "reason": "Updated requirement",
        },
        execute=True,
    )["view"]
    acknowledge(
        root, "goal-a", "worker", ctrl_rid, "adopt", "Accepted",
        registry=registry, caller_goal_ref=ctrl_receipt["goal_ref"],
        request_revision=2, request_revision_digest=revised["revision_digest"],
    )
    report(
        root, "goal-a", "worker", ctrl_rid, "conclusion", "Checked.",
        registry=registry, caller_goal_ref=ctrl_receipt["goal_ref"],
        request_revision=2,
    )

    valid_curr_id, _ = _write_tracking_entry(root, gid="goal-a", inst_id=inst_a, idx=1, delivered_at="2026-11-19T00:01:00+00:00")
    valid_hist_id, _ = _write_tracking_entry(root, gid="goal-a", inst_id=inst_hist, idx=2, delivered_at="2026-11-19T00:02:00+00:00")

    corrupt_dir = _root(root) / "entries" / _hash({"goal_ref": {"goal_id": "goal-a", "goal_instance_id": inst_a}, "agent_id": "worker"})
    (corrupt_dir / f"{'e' * 64}.json").write_text("{damaged", encoding="utf-8")
    _write(corrupt_dir / f"{'f' * 64}.json", {"goal_id": "goal-a"})
    _write(corrupt_dir / f"{'1' * 63}a.json", {"goal_id": "goal-a", "agent_id": "worker", "request_id": f"{'1' * 63}a", "goal_ref": "bad"})
    _write(corrupt_dir / f"{'1' * 63}b.json", {"goal_id": "goal-a", "agent_id": "worker", "request_id": f"{'1' * 63}b", "goal_ref": {"goal_id": "goal-a", "goal_instance_id": "invalid"}})
    _write_tracking_entry(root, gid="goal-a", inst_id=inst_a, idx=3, include_goal_ref=False)
    _write_tracking_entry(root, gid="goal-a", inst_id=inst_a, idx=4, dir_key="wrongdir")
    _write_tracking_entry(root, gid="unregistered-goal", inst_id=inst_a, idx=5)
    _write_tracking_entry(root, gid="unregistered-goal", inst_id=inst_a, idx=6)

    mismatch_ids = set()
    for i in range(10, 15):
        rid, _ = _write_tracking_entry(root, gid="goal-a", inst_id=inst_mismatch, idx=i)
        mismatch_ids.add(rid)

    orig_entry = tracking._entry
    orig_rpc = gis.effect_runtime_result
    decide_calls = 0

    def wrapped_entry(entry_root, goal_id, agent_id, req_id, *, scope=None):
        row = orig_entry(entry_root, goal_id, agent_id, req_id, scope=scope)
        if req_id in mismatch_ids:
            return {**row, "goal_ref": {"goal_id": "goal-a", "goal_instance_id": inst_other}}
        return row

    def counted_rpc(method, params, **kwargs):
        nonlocal decide_calls
        if method == "collaboration.goal_instance.decide":
            decide_calls += 1
        return orig_rpc(method, params, **kwargs)

    monkeypatch.setattr(tracking, "_entry", wrapped_entry)
    monkeypatch.setattr(gis, "effect_runtime_result", counted_rpc)

    result = query(root, registry, goal_ids=["goal-a", "unregistered-goal"], owner_scope=True, limit=12)
    assert result["matched"] == 3
    assert result["coverage"]["unreadable"] == 13
    assert {r["request_id"] for r in result["rows"]} == {ctrl_rid, valid_curr_id, valid_hist_id}
    assert decide_calls == 3

    by_id = {r["request_id"]: r for r in result["rows"]}
    assert by_id[ctrl_rid]["request_revision"] == 2
    assert isinstance(by_id[ctrl_rid]["request_control"], dict)
    assert by_id[ctrl_rid]["request_control"]["control_state"]
    assert by_id[ctrl_rid]["brief"]["purpose"] == "Revised purpose"
    assert len(by_id[ctrl_rid]["return_replies"]) == 1
    assert by_id[ctrl_rid]["return_replies"][0]["request_revision"] == 2
    assert by_id[valid_hist_id]["goal_ref"] == {"goal_id": "goal-a", "goal_instance_id": inst_hist}
