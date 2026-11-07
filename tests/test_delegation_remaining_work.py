"""A returned partial result retains its request without creating work authority."""

import json
import subprocess
import sys

import pytest

from loopx.capabilities.manager_context import _root, acknowledge
from loopx.capabilities.manager_context.roundtrip import drain, report
from loopx.control_plane.collaboration.peers import consume_return, read_inbox, request
from loopx.control_plane.collaboration.turn_context_observation import read_delegation_turn_context
import test_manager_context_roundtrip as roundtrip_tests


REMAINDER = {"summary": "Validate the installed repair", "resume_when": "The isolated package is available"}


@pytest.fixture
def flow(tmp_path):
    return roundtrip_tests.flow.__wrapped__(tmp_path)


def agenda(root, registry, agent="worker"):
    return read_delegation_turn_context(root, registry, "research", agent)["unfinished_delegations"]


@pytest.mark.parametrize("external", [False, True], ids=["app", "provider"])
def test_partial_return_keeps_its_scope_after_delivery_and_restart(flow, external):
    root, registry, store, create = flow
    session, _, receipt = create(external=external)
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Repair and validate the original request")
    report(root, "research", "worker", rid, "conclusion", "Implementation is ready.", remaining_work=REMAINDER)
    original = (_root(root) / "replies" / rid / "conclusion.json").read_bytes()
    sent = []

    def sender(_route, _session, _turn, text):
        sent.append(text)
        return {"reply_verified": True, "external_write_performed": True}

    drain(root, registry, store, sender)
    # A fresh native read, independently of a model or a Todo, retains the work.
    for _ in range(2):
        item = agenda(root, registry)["items"][0]
        assert item["request_id"] == rid and item["step"] == "review_request_work"
        assert item["remaining_work"] == REMAINDER
        assert item["pending_return_count"] == 0
    returned = [m for m in store.messages(session["session_id"]) if m.get("origin") == "manager_followup"]
    assert len(returned) == 1 and REMAINDER["resume_when"] in returned[0]["text"]
    if external:
        assert len(sent) == 1 and REMAINDER["summary"] in sent[0]
    report(root, "research", "worker", rid, "conclusion", "The isolated repair passed the agreed validation.", update_id="qualified-result")
    assert agenda(root, registry)["items"][0]["step"] == "await_return_delivery"
    drain(root, registry, store, sender)
    assert agenda(root, registry)["items"] == []
    assert (_root(root) / "replies" / rid / "conclusion.json").read_bytes() == original
    assert len([m for m in store.messages(session["session_id"]) if m.get("origin") == "manager_followup"]) == 2


def test_partial_peer_result_is_consumed_once_while_work_stays_owed(tmp_path):
    root, registry, _, _ = roundtrip_tests.flow.__wrapped__(tmp_path)
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("reviewer")
    registry.write_text(json.dumps(data))
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Repair and independently validate",
        "context": "Preserve the acceptance requirement", "constraints": [], "inputs": [],
        "acceptance": ["The installed repair works"], "return_requirement": "Return the checked repair"}
    rid = request(root, registry, "research", "worker", "reviewer", "repair", brief)["request_id"]
    acknowledge(root, "research", "reviewer", rid, "adopt", "Own the repair and validation")
    report(root, "research", "reviewer", rid, "conclusion", "The implementation is ready.", remaining_work=REMAINDER)
    assert agenda(root, registry)["items"][0]["step"] == "consume_peer_result"
    result = read_inbox(root, registry, "research", "worker")["peer_returns"]["items"][0]
    assert result["remaining_work"] == REMAINDER
    consume_return(root, "research", "worker", rid, registry=registry)
    assert agenda(root, registry)["items"][0]["step"] == "await_peer_result"
    assert agenda(root, registry, "reviewer")["items"][0]["step"] == "review_request_work"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_partial_request_and_shared_todo_have_independent_lifetimes(tmp_path, monkeypatch, provider):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
    from loopx.capabilities.manager_context import deliver

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, root, _ = promoted_create_fixture(tmp_path, provider=provider)

    def cli(*args, ok=True):
        process = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(root), *args], capture_output=True, text=True, timeout=45)
        assert process.returncode == (0 if ok else 1), (process.stdout, process.stderr)
        result = json.loads(process.stdout)
        assert result["ok"] is ok
        return result

    work = cli("todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Ongoing umbrella work", "--claimed-by", "agent-a")
    tid = work["todo_id"]
    request_ids = []
    for index in range(2):
        rid = deliver(root, registry, session={"session_id": "original", "channel_id": "manager"},
            turn={"client_turn_id": f"request-{index}", "origin": "web", "message": "Return this request's actual result"},
            request={"goal_id": "goal-a", "agent_id": "agent-a"})["request_id"]
        request_ids.append(rid)
        scope = ("--goal-id", "goal-a", "--agent-id", "agent-a", "--request-id", rid)
        cli("manager-inbox", "acknowledge", *scope, "--decision", "adopt", "--reason", "Keep each request independent")
        cli("manager-inbox", "link", *scope, "--related-todo-id", tid)
        cli("manager-inbox", "report", *scope, "--reply-text", "A partial result", "--remaining-work", REMAINDER["summary"], "--resume-when", REMAINDER["resume_when"])
    cli("todo", "complete", "--goal-id", "goal-a", "--todo-id", tid, "--agent-id", "agent-a", "--evidence", "The umbrella's separate scope passed", "--no-follow-up")
    pending = cli("manager-inbox", "read", "--goal-id", "goal-a", "--agent-id", "agent-a")["items"]
    assert {item["request_id"] for item in pending} == set(request_ids)
    assert all(item["remaining_work"] == REMAINDER and item["receiver_followthrough"]["step"] == "review_request_work" for item in pending)
    scope = ("--goal-id", "goal-a", "--agent-id", "agent-a", "--request-id", request_ids[0])
    cli("manager-inbox", "report", *scope, "--reply-text", "First request's checked result", "--update-id", "verified-first")
    pending = cli("manager-inbox", "read", "--goal-id", "goal-a", "--agent-id", "agent-a")["items"]
    assert [item["request_id"] for item in pending] == [request_ids[1]]
    assert cli("todo", "list", "--goal-id", "goal-a", "--todo-id", tid)["todo"]["status"] == "done"
    failure = cli("manager-inbox", "report", *scope, "--reply-text", "Invalid", "--remaining-work", "Unpaired", ok=False)
    assert "require" in failure["error"]


def test_ordinary_short_answer_needs_no_work_record_and_external_remainder_is_scanned(flow):
    root, registry, store, create = flow
    _, _, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "no_change", "A direct answer is sufficient")
    report(root, "research", "worker", rid, "conclusion", "The current public PR is merged.")
    drain(root, registry, store, lambda *_: pytest.fail("local return"))
    assert agenda(root, registry)["items"] == []
    assert not (_root(root) / "links" / f"{rid}.json").exists()
    _, _, external = create(external=True)
    rid = external["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "An audience-safe return")
    with pytest.raises(ValueError, match="private boundary"):
        report(root, "research", "worker", rid, "conclusion", "Safe visible answer", remaining_work={
            "summary": "/Users/private/customer/raw.txt", "resume_when": "Ready"})
    assert not (_root(root) / "replies" / rid / "conclusion.json").exists()
