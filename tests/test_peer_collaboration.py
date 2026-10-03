"""Semantic delegation, two review rounds and recovery over the real CLI/store."""

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from loopx.capabilities.manager_context import (
    POLICY_SCHEMA,
    _root,
    _write,
    deliver,
    pending,
    normalize_request,
    register_ingress,
    turn_start_hook,
)
from loopx.control_plane.collaboration.peers import (
    request,
    returns,
    consume_return,
    input_readiness,
)
from loopx.capabilities.manager_context.roundtrip import (
    drain,
    project_chat_session_snapshot,
)
from loopx.capabilities.manager_context.tracking import query
from loopx.chat_store import ChatSessionStore


@pytest.fixture
def scenario(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "delivery",
                        "repo": str(tmp_path),
                        "coordination": {
                            "registered_agents": ["builder", "reviewer", "analyst"]
                        },
                    }
                ]
            }
        )
    )
    (tmp_path / "inputs").mkdir()
    artifact = tmp_path / "inputs" / "demand.csv"
    artifact.write_text("sku,demand\na,15\nb,0\n")
    brief = {
        "schema_version": "collaboration_brief_v0",
        "purpose": "Produce a stock plan",
        "context": "The owner rejected using an average. Latest correction: reserve two units.",
        "constraints": ["No external orders", "Never allocate negative quantities"],
        "inputs": [
            {
                "ref": "inputs/demand.csv",
                "description": "Demand fixture",
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        ],
        "acceptance": [
            "Total allocation is within available stock",
            "Zero-demand items receive zero",
        ],
        "return_requirement": "Return the plan and independent review findings, including unresolved gaps.",
    }
    store = ChatSessionStore(tmp_path)
    session = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="fixture",
        channel_id="manager",
    )
    turn, _ = store.create_turn(
        session["session_id"],
        client_turn_id="owner-correction",
        message="Use the correction above and have a peer check it.",
        origin="web",
    )
    receipt = deliver(
        tmp_path,
        registry,
        session=session,
        turn=turn,
        request={"goal_id": "delivery", "agent_id": "builder", "brief": brief},
    )
    store.update_turn(
        session["session_id"],
        turn["turn_id"],
        status="completing",
        response={"message": "Delegated", "context_handoff_receipt": receipt},
    )
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    return tmp_path, registry, brief, store, session, turn, receipt["request_id"]


def cli(root, registry, agent, action, *args, ok=True):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--runtime-root",
            str(root),
            "--registry",
            str(registry),
            "manager-inbox",
            action,
            "--goal-id",
            "delivery",
            "--agent-id",
            agent,
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=registry.parent,
    )
    result = json.loads(proc.stdout)
    assert proc.returncode == (0 if ok else 1), (proc.stdout, proc.stderr)
    return result


@pytest.fixture
def external_scenario(scenario):
    root, registry, brief, store, _, _, _ = scenario
    session = store.create_session(
        goal_id="loopx-manager", agent_id="codex", adapter_kind="codex_app_server",
        upstream_thread_id="external-fixture", channel_id="manager.external.fixture",
    )
    turn, _ = store.create_turn(
        session["session_id"], client_turn_id="external-request",
        message="Have the stock-plan owner handle this and ask for an independent check.",
        origin="lark",
    )
    policy_path = _root(root) / "policy.json"
    _write(policy_path, {
        "schema_version": POLICY_SCHEMA,
        "sources": {session["channel_id"]: {
            "sender_ids": ["fixture-owner"],
            "targets": [{"goal_id": "delivery", "agent_id": agent}
                        for agent in ("builder", "reviewer")],
        }},
    })
    register_ingress(
        root, session_id=session["session_id"], client_turn_id=turn["client_turn_id"],
        channel=session["channel_id"], sender_id="fixture-owner",
        message=turn["message"], source_id="lark:fixture-request",
    )
    receipt = deliver(root, registry, session=session, turn=turn,
                      request={"goal_id": "delivery", "agent_id": "builder", "brief": brief})
    store.update_turn(session["session_id"], turn["turn_id"], status="completing",
                      response={"message": "Delivered", "context_handoff_receipt": receipt})
    store.finalize_managed_turn_completion(session["session_id"], turn["turn_id"])
    return root, registry, brief, store, session, turn, receipt["request_id"]


@pytest.mark.parametrize("whole_goal", [False, True])
def test_granted_external_peer_request_returns_through_original_conversation(external_scenario, whole_goal):
    root, registry, brief, store, session, turn, parent = external_scenario
    if whole_goal:
        policy_path = _root(root) / "policy.json"
        policy = json.loads(policy_path.read_text())
        policy["sources"][session["channel_id"]]["targets"] = [{"goal_id": "delivery"}]
        _write(policy_path, policy)
    path = root / "external-review.json"
    path.write_text(json.dumps(brief))
    args = ("--peer-agent-id", "reviewer", "--operation-id", "external-review",
            "--brief-file", str(path), "--parent-request-id", parent)
    first = cli(root, registry, "builder", "request", *args)
    assert not first["todo_created"] and not first["execution_interrupted"]
    assert cli(root, registry, "builder", "request", *args)["replayed"]
    rid = first["request_id"]
    received = next(item for item in cli(root, registry, "reviewer", "read")["items"]
                    if item["request_id"] == rid)
    assert received["inherited_context"]["message"] == turn["message"]
    cli(root, registry, "reviewer", "acknowledge", "--request-id", rid,
        "--decision", "adopt", "--reason", "Will check the corrected stock plan.")
    cli(root, registry, "reviewer", "report", "--request-id", rid,
        "--reply-text", "Checked the stock constraint and zero-demand item; both pass.")
    returned = cli(root, registry, "builder", "read")["peer_returns"]["items"][0]
    assert returned["parent_request_id"] == parent
    cli(root, registry, "builder", "acknowledge-return", "--request-id", rid)
    cli(root, registry, "builder", "acknowledge", "--request-id", parent,
        "--decision", "adopt", "--reason", "Incorporated the independent check.")
    cli(root, registry, "builder", "report", "--request-id", parent,
        "--reply-text", "Stock plan delivered with the independent check.")
    sent = []

    def sender(route, source_session, source_turn, text):
        sent.append((route["channel_id"], source_session["session_id"], source_turn["turn_id"], text))
        return {"ok": True, "reply_verified": True}

    for _ in range(2):
        drain(root, registry, ChatSessionStore(root), sender)
    assert len(sent) == 1
    assert sent[0][:3] == (session["channel_id"], session["session_id"], turn["turn_id"])
    assert sent[0][3].endswith("Stock plan delivered with the independent check.")
    assert not query(root, registry, goal_ids=["delivery"], owner_scope=False,
                     channel_id="manager.external.unrelated")["rows"]


@pytest.mark.parametrize("change", [
    "receiver_revoked", "source_agent_revoked", "sender_revoked", "source_changed",
    "route_missing", "route_changed", "session_closed",
])
def test_external_forwarding_rechecks_provenance_before_any_peer_write(external_scenario, change):
    root, registry, brief, store, session, turn, parent = external_scenario
    folder = _root(root)
    if change.endswith("revoked"):
        policy = json.loads((folder / "policy.json").read_text())
        source = policy["sources"][session["channel_id"]]
        if change == "sender_revoked":
            source["sender_ids"] = []
        else:
            agent = "reviewer" if change == "receiver_revoked" else "builder"
            source["targets"] = [item for item in source["targets"] if item["agent_id"] != agent]
        _write(folder / "policy.json", policy)
    elif change == "source_changed":
        path = next((folder / "ingress").glob("*.json"))
        _write(path, {**json.loads(path.read_text()), "source_id": "lark:another-request"})
    elif change.startswith("route_"):
        path = folder / "roundtrips" / (parent + ".json")
        if change == "route_missing":
            path.unlink()
        else:
            _write(path, {**json.loads(path.read_text()), "goal_ref": {"goal_id": "other"}})
    else:
        assert store.close_managed_session(session["session_id"])
    before = {path: path.read_bytes() for path in folder.rglob("*.json")}
    with pytest.raises(ValueError, match="(not authorized|authorization unavailable|conversation unavailable|identity mismatch)"):
        request(root, registry, "delivery", "builder", "reviewer", "forbidden", brief, parent)
    assert {path: path.read_bytes() for path in folder.rglob("*.json")} == before


def test_external_parent_lineage_cannot_launder_a_second_hop(external_scenario):
    root, registry, brief, *_rest, parent = external_scenario
    first = request(root, registry, "delivery", "builder", "reviewer", "first", brief, parent)
    with pytest.raises(ValueError, match="analyst is not authorized"):
        request(root, registry, "delivery", "reviewer", "analyst", "second", brief, first["request_id"])
    policy_path = _root(root) / "policy.json"
    policy = json.loads(policy_path.read_text())
    source = policy["sources"]["manager.external.fixture"]
    source["targets"].append({"goal_id": "delivery", "agent_id": "analyst"})
    _write(policy_path, policy)
    second = request(root, registry, "delivery", "reviewer", "analyst", "second", brief, first["request_id"])
    inherited = pending(root, "delivery", "analyst")["items"][0]["inherited_context"]
    assert inherited["request_id"] == parent
    source["targets"] = [item for item in source["targets"] if item["agent_id"] != "builder"]
    _write(policy_path, policy)
    with pytest.raises(ValueError, match="builder is not authorized"):
        request(root, registry, "delivery", "analyst", "reviewer", "third", brief, second["request_id"])


def test_two_peer_review_rounds_return_to_original_conversation_after_restart(scenario):
    root, registry, brief, store, session, turn, parent = scenario
    builder = cli(root, registry, "builder", "read")["items"][0]
    assert builder["message"] == turn["message"]
    assert builder["brief"] == brief
    assert builder["input_readiness"][0]["status"] == "available"
    cli(
        root,
        registry,
        "builder",
        "acknowledge",
        "--request-id",
        parent,
        "--decision",
        "adopt",
        "--reason",
        "Will use the corrected reserve rule.",
    )
    ids = []
    for number in (1, 2):
        review = {**brief, "purpose": f"Independently review stock plan round {number}"}
        path = root / "review.json"
        path.write_text(json.dumps(review))
        args = (
            "--peer-agent-id",
            "reviewer",
            "--operation-id",
            f"review-{number}",
            "--brief-file",
            str(path),
            "--parent-request-id",
            parent,
        )
        rid = cli(root, registry, "builder", "request", *args)["request_id"]
        ids.append(rid)
        assert cli(root, registry, "builder", "request", *args)["replayed"]
        received = cli(root, registry, "reviewer", "read")["items"][0]
        assert received["inherited_context"]["brief"] == brief
        assert received["source_agent_id"] == "builder"
        cli(
            root,
            registry,
            "reviewer",
            "acknowledge",
            "--request-id",
            rid,
            "--decision",
            "adopt",
            "--reason",
            "Independent artifact check.",
        )
        result_text = (
            "Reserve missing; revise."
            if number == 1
            else "Reserve and zero-demand checks passed."
        )
        cli(
            root,
            registry,
            "reviewer",
            "report",
            "--request-id",
            rid,
            "--reply-text",
            result_text,
        )
        # The Chat pump cannot send a peer reply to any conversation.
        assert drain(root, registry, store, None) == 0
        assert turn_start_hook(root, registry, "delivery", "builder").producer()[
            "agent_read_required"
        ]
        # New CLI processes reconstruct the work context with no source session.
        returned = cli(root, registry, "builder", "read")["peer_returns"]["items"][0]
        assert (
            returned["text"] == result_text and returned["parent_request_id"] == parent
        )
        assert (
            cli(root, registry, "builder", "read")["peer_returns"]["items"][0]
            == returned
        )
        cli(root, registry, "builder", "acknowledge-return", "--request-id", rid)
        cli(root, registry, "builder", "acknowledge-return", "--request-id", rid)
        assert not returns(root, "delivery", "builder")["items"]
    assert ids[0] != ids[1]
    cli(
        root,
        registry,
        "builder",
        "report",
        "--request-id",
        parent,
        "--reply-text",
        "Revised the reserve after peer review; the second check passed.",
    )
    assert drain(root, registry, ChatSessionStore(root), None) == 1
    assert drain(root, registry, ChatSessionStore(root), None) == 0
    snapshot = project_chat_session_snapshot(root, store, session["session_id"], registry=registry)
    assert sum(m.get("origin") == "manager_followup" for m in snapshot["messages"]) == 1
    card = next(
        m["collaboration"] for m in snapshot["messages"] if m.get("collaboration")
    )
    assert (
        card["brief"] == brief
        and card["decision"] == "adopt"
        and card["read_status"] == "supplied"
    )
    assert card["returns"][-1]["status"] == "delivered"
    assert not pending(root, "delivery", "builder")["items"]


def test_operation_replay_conflicts_and_independent_requests(scenario):
    root, registry, brief, _, _, _, parent = scenario

    def send():
        return request(
            root, registry, "delivery", "builder", "reviewer", "review-1", brief, parent
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: send(), range(4)))
    assert sum(not row["replayed"] for row in receipts) == 1
    with pytest.raises(ValueError, match="identity conflict"):
        request(
            root, registry, "delivery", "builder", "analyst", "review-1", brief, parent
        )
    with pytest.raises(ValueError, match="identity conflict"):
        request(
            root,
            registry,
            "delivery",
            "builder",
            "reviewer",
            "review-1",
            {**brief, "purpose": "Changed"},
            parent,
        )
    rid = receipts[0]["request_id"]
    with pytest.raises(ValueError, match="scope mismatch"):
        consume_return(root, "delivery", "analyst", rid)
    with pytest.raises(ValueError, match="read the peer conclusion"):
        consume_return(root, "delivery", "builder", rid)
    with pytest.raises(ValueError, match="different"):
        request(root, registry, "delivery", "builder", "builder", "self", brief)
    with pytest.raises(ValueError, match="registered"):
        request(root, registry, "delivery", "builder", "unknown", "other", brief)
    with pytest.raises((ValueError, OSError)):
        request(
            root,
            registry,
            "delivery",
            "analyst",
            "reviewer",
            "stolen-parent",
            brief,
            parent,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"priority": "P0"},
        {"acceptance": []},
        {"constraints": "No orders"},
        {"inputs": [{"ref": "../private.txt", "description": "Invalid"}]},
        {"inputs": [{"ref": "/etc/passwd", "description": "Invalid"}]},
        {
            "inputs": [
                {"ref": "inputs/x", "description": "Invalid", "sha256": "guessed"}
            ]
        },
        {"context": "字" * 6000},
    ],
)
def test_semantic_contract_rejects_operational_keys_and_invalid_inputs(
    scenario, change
):
    _, _, brief, *_ = scenario
    with pytest.raises(ValueError):
        normalize_request(
            {"goal_id": "delivery", "agent_id": "builder", "brief": {**brief, **change}}
        )


def test_changed_missing_and_escaping_artifacts_are_explicit(scenario, tmp_path):
    root, registry, brief, *_ = scenario
    (root / "inputs/demand.csv").write_text("changed")
    assert input_readiness(registry, "delivery", brief)[0]["status"] == "changed"
    (root / "inputs/demand.csv").unlink()
    assert input_readiness(registry, "delivery", brief)[0]["status"] == "unavailable"
    try:
        (root / "inputs/demand.csv").symlink_to(root.parent / "outside.csv")
    except OSError as exc:
        if sys.platform == "win32" and exc.winerror == 1314:
            pytest.skip("Windows symlink fixture requires privileges")
        raise
    assert (
        input_readiness(registry, "delivery", brief)[0]["status"] == "outside_workspace"
    )


def test_peer_route_never_enters_external_audience_projection(scenario):
    root, registry, brief, store, session, turn, parent = scenario
    request(root, registry, "delivery", "builder", "reviewer", "review", brief, parent)
    result = query(
        root,
        registry,
        goal_ids=["delivery"],
        owner_scope=False,
        channel_id="manager.external.other",
    )
    assert result["rows"] == []
    external = store.create_session(
        goal_id="loopx-manager",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="external",
        channel_id="manager.external.other",
    )
    assert not any(
        m.get("collaboration")
        for m in project_chat_session_snapshot(root, store, external["session_id"], registry=registry)[
            "messages"
        ]
    )
    assert not returns(root, "delivery", "analyst")["items"]


def test_invalid_brief_retry_does_not_create_another_manager_request(scenario):
    root, registry, brief, _, session, turn, _ = scenario
    with pytest.raises(ValueError, match="identity conflict"):
        deliver(
            root,
            registry,
            session=session,
            turn=turn,
            request={
                "goal_id": "delivery",
                "agent_id": "builder",
                "brief": {**brief, "purpose": "Different"},
            },
        )
    assert len(pending(root, "delivery", "builder")["items"]) == 1


def test_input_versions_follow_receiver_worktree_and_reject_unrelated_workspace(
    scenario, tmp_path
):
    root, registry, brief, *_ = scenario

    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("add", "inputs/demand.csv")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    receiver = root.parent / (root.name + "-receiver")
    git("worktree", "add", "-b", "receiver", str(receiver))
    (receiver / "inputs/demand.csv").write_text("receiver has a different version")
    result = input_readiness(registry, "delivery", brief, workspace=receiver)[0]
    assert result["status"] == "changed" and result["basis"] == "receiver_worktree"
    unrelated = root / "unrelated"
    unrelated.mkdir()
    # A non-repository directory cannot replace the registered source root.
    outside = root.parent / (root.name + "-outside")
    outside.mkdir()
    result = input_readiness(registry, "delivery", brief, workspace=outside)[0]
    assert result["status"] == "available" and result["basis"] == "goal_workspace"
    (outside / "inputs").mkdir()
    (outside / "inputs/demand.csv").write_text("configured delegation version")
    result = input_readiness(
        registry,
        "delivery",
        brief,
        workspace=outside,
        configured_workspace=True,
    )[0]
    assert result["status"] == "changed" and result["basis"] == "receiver_worktree"


def test_unrelated_damaged_return_route_does_not_break_legacy_inbox(scenario):
    root, _, _, _, _, _, parent = scenario
    damaged = root / ".local/manager-context/roundtrips/unrelated.json"
    damaged.write_text("{broken")
    assert pending(root, "delivery", "builder")["items"][0]["request_id"] == parent


def test_consumption_rejects_corrupt_reply_and_receipt(scenario):
    from loopx.capabilities.manager_context import _root

    root, registry, brief, _, _, _, parent = scenario
    rid = request(
        root, registry, "delivery", "builder", "reviewer", "review", brief, parent
    )["request_id"]
    cli(root, registry, "reviewer", "read")
    cli(
        root,
        registry,
        "reviewer",
        "acknowledge",
        "--request-id",
        rid,
        "--decision",
        "adopt",
        "--reason",
        "Review",
    )
    cli(
        root,
        registry,
        "reviewer",
        "report",
        "--request-id",
        rid,
        "--reply-text",
        "Checked",
    )
    cli(root, registry, "builder", "read")
    folder = _root(root) / "replies" / rid
    reply = folder / "conclusion.json"
    original = reply.read_text()
    reply.write_text(json.dumps({**json.loads(original), "source_id": "other"}))
    with pytest.raises(ValueError, match="identity conflict"):
        consume_return(root, "delivery", "builder", rid)
    reply.write_text(original)
    consume_return(root, "delivery", "builder", rid)
    consumed = folder / "conclusion.consumed.json"
    consumed.write_text(
        json.dumps({**json.loads(consumed.read_text()), "agent_id": "other"})
    )
    with pytest.raises(ValueError, match="scope mismatch"):
        consume_return(root, "delivery", "builder", rid)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO fixture")
def test_special_file_read_is_bounded_and_stopped_goal_remains_readable(scenario):
    import os
    from loopx.control_plane.collaboration.peers import read_inbox

    root, registry, brief, *_ = scenario
    (root / "inputs/demand.csv").unlink()
    os.mkfifo(root / "inputs/demand.csv")
    assert input_readiness(registry, "delivery", brief)[0]["status"] == "unavailable"
    config = json.loads(registry.read_text())
    config["goals"][0]["status"] = "stopped"
    registry.write_text(json.dumps(config))
    assert read_inbox(root, registry, "delivery", "builder")["items"]
    with pytest.raises(ValueError, match="stopped"):
        request(root, registry, "delivery", "builder", "reviewer", "stopped", brief)


def test_nested_coordinators_return_to_each_immediate_requester(scenario):
    """Worker -> coordinator -> specialist works without a manager root request."""
    root, registry, brief, *_ = scenario
    outer = request(
        root, registry, "delivery", "builder", "reviewer", "delegate", brief
    )["request_id"]
    cli(root, registry, "reviewer", "read")
    cli(
        root,
        registry,
        "reviewer",
        "acknowledge",
        "--request-id",
        outer,
        "--decision",
        "adopt",
        "--reason",
        "Coordinate the specialist audit",
    )
    inner = request(
        root, registry, "delivery", "reviewer", "analyst", "specialist", brief, outer
    )["request_id"]
    received = cli(root, registry, "analyst", "read")["items"][0]
    assert received["inherited_context"]["request_id"] == outer
    assert received["source_agent_id"] == "reviewer"
    cli(
        root,
        registry,
        "analyst",
        "acknowledge",
        "--request-id",
        inner,
        "--decision",
        "adopt",
        "--reason",
        "Check specialty",
    )
    cli(
        root,
        registry,
        "analyst",
        "report",
        "--request-id",
        inner,
        "--reply-text",
        "Specialist findings",
    )
    assert not returns(root, "delivery", "builder")["items"]
    assert (
        cli(root, registry, "reviewer", "read")["peer_returns"]["items"][0][
            "request_id"
        ]
        == inner
    )
    consume_return(root, "delivery", "reviewer", inner)
    cli(
        root,
        registry,
        "reviewer",
        "report",
        "--request-id",
        outer,
        "--reply-text",
        "Specialist findings independently assessed",
    )
    assert (
        cli(root, registry, "builder", "read")["peer_returns"]["items"][0]["request_id"]
        == outer
    )
    consume_return(root, "delivery", "builder", outer)
    assert not returns(root, "delivery", "builder")["items"]


def test_peer_updates_have_independent_read_and_consumption_receipts(scenario):
    from loopx.capabilities.manager_context import acknowledge
    from loopx.control_plane.collaboration.peers import return_result

    root, registry, brief, _, _, _, parent = scenario
    peer = request(root, registry, "delivery", "builder", "reviewer", "update-review", brief, parent)
    rid = peer["request_id"]
    acknowledge(root, "delivery", "reviewer", rid, "adopt", "Review accepted")
    return_result(root, "delivery", "reviewer", rid, "Waiting for evidence.")
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as executor:
        updates = list(executor.map(lambda _: return_result(root, "delivery", "reviewer", rid, "Evidence checked; review complete.", update_id="evidence-ready"), range(4)))
    update = updates[0]
    assert {item["result_key"] for item in updates} == {update["result_key"]}
    with pytest.raises(ValueError, match="read the peer"):
        consume_return(root, "delivery", "builder", rid, result_key=update["result_key"])
    offered = returns(root, "delivery", "builder", mark_read=True)["items"]
    assert [item["text"] for item in offered] == ["Waiting for evidence.", "Evidence checked; review complete."]
    consume_return(root, "delivery", "builder", rid)
    assert [item["result_key"] for item in returns(root, "delivery", "builder")["items"]] == [update["result_key"]]
    consume_return(root, "delivery", "builder", rid, result_key=update["result_key"])
    assert returns(root, "delivery", "builder")["items"] == []
    assert return_result(root, "delivery", "reviewer", rid, "Evidence checked; review complete.", update_id="evidence-ready")["result_key"] == update["result_key"]
    assert returns(root, "delivery", "builder")["items"] == []


def test_peer_update_rejects_another_results_read_and_consumption_receipts(scenario):
    from loopx.capabilities.manager_context import acknowledge
    from loopx.control_plane.collaboration.peers import return_result

    root, registry, brief, _, _, _, parent = scenario
    rid = request(root, registry, "delivery", "builder", "reviewer", "update-review", brief, parent)["request_id"]
    acknowledge(root, "delivery", "reviewer", rid, "adopt", "Accepted")
    return_result(root, "delivery", "reviewer", rid, "Draft ready.")
    returns(root, "delivery", "builder", mark_read=True)
    consume_return(root, "delivery", "builder", rid)
    update = return_result(root, "delivery", "reviewer", rid, "Review complete.", update_id="reviewed")
    folder = root / ".local/manager-context/replies" / rid
    delivery = folder / (update["result_key"] + ".delivery.json")
    delivery.write_bytes((folder / "conclusion.delivery.json").read_bytes())
    with pytest.raises(ValueError, match="read the peer"):
        consume_return(root, "delivery", "builder", rid, result_key=update["result_key"])
    consumed = folder / (update["result_key"] + ".consumed.json")
    consumed.write_bytes((folder / "conclusion.consumed.json").read_bytes())
    with pytest.raises(ValueError, match="receipt scope"):
        returns(root, "delivery", "builder")
@pytest.mark.skipif(sys.platform != "win32", reason="Win32 extended path regression")
def test_peer_exchange_survives_long_private_store_paths(scenario):
    root, registry, brief, *_ = scenario
    from loopx.control_plane.collaboration.inbox import _root

    long_root = root / ("nested-runtime-" * 7)
    packet = root / "long-path-brief.json"
    packet.write_text(json.dumps(brief))
    args = ("--peer-agent-id", "reviewer", "--operation-id", "long-path-review",
            "--brief-file", str(packet))
    sent = cli(long_root, registry, "builder", "request", *args)
    rid = sent["request_id"]
    assert cli(long_root, registry, "reviewer", "read")["items"][0]["request_id"] == rid
    cli(long_root, registry, "reviewer", "acknowledge", "--request-id", rid,
        "--decision", "adopt", "--reason", "Reviewing the pinned artifact")
    cli(long_root, registry, "reviewer", "report", "--request-id", rid,
        "--phase", "conclusion", "--reply-text", "Independent review complete")
    assert cli(long_root, registry, "builder", "read")["peer_returns"]["items"][0]["request_id"] == rid
    cli(long_root, registry, "builder", "acknowledge-return", "--request-id", rid)
    replay = cli(long_root, registry, "builder", "request", *args)
    assert replay["replayed"] and replay["request_id"] == rid
    requests = [path for path in (_root(long_root) / "entries").glob("*/*.json") if path.stem == rid]
    assert len(requests) == 1 and len(str(requests[0])) > 260


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 binary input regression")
def test_peer_binary_artifact_preserves_crlf_and_ctrl_z_digest(scenario):
    root, registry, brief, *_ = scenario
    content = b"before\r\n\x1aafter\r\n\x00\xff"
    artifact = root / "inputs" / "packet.bin"
    artifact.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    brief = {**brief, "inputs": [{"ref": "inputs/packet.bin",
        "description": "Binary fixture", "sha256": digest}]}
    packet = root / "binary-brief.json"
    packet.write_text(json.dumps(brief), encoding="utf-8")
    sent = cli(root, registry, "builder", "request", "--peer-agent-id", "reviewer",
        "--operation-id", "binary-review", "--brief-file", str(packet))
    item = cli(root, registry, "reviewer", "read")["items"][0]
    assert item["request_id"] == sent["request_id"]
    [readiness] = item["input_readiness"]
    assert readiness["status"] == "available"
    assert readiness["observed_sha256"] == readiness["expected_sha256"] == digest
    assert readiness["content_supplied"] is False
