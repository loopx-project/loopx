"""Real receipt/store journeys: a Turn agenda does not complete or replay work."""
import json
import os
import shlex

import pytest

from loopx.capabilities.manager_context import acknowledge, deliver, turn_start_hook, _root, _write
from loopx.capabilities.manager_context.roundtrip import report, drain
from loopx.control_plane.capability_hooks import dispatch_turn_start_hooks
from loopx.control_plane.collaboration.peers import request, read_inbox, consume_return
from loopx.control_plane.collaboration.turn_context_observation import read_delegation_turn_context
import test_manager_context_roundtrip as roundtrip_tests


@pytest.fixture
def flow(tmp_path):
    return roundtrip_tests.flow.__wrapped__(tmp_path)


def agenda(root, registry, agent="worker"):
    return read_delegation_turn_context(root, registry, "research", agent)["unfinished_delegations"]


def test_two_turns_show_read_adopted_work_then_queued_return_without_mutating_receipts(flow):
    root, registry, store, create = flow
    session, turn, receipt = create()
    rid = receipt["request_id"]
    acknowledge(root, "research", "worker", rid, "adopt", "Existing scoped work")
    # Supplying the full inbox does not make the request disappear on the next Turn.
    read_inbox(root, registry, "research", "worker")
    before = {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")}
    for _ in range(2):
        dispatch = dispatch_turn_start_hooks([turn_start_hook(root, registry, "research", "worker")])
        assert dispatch["failures"] == []
        supplied = dispatch["contexts"][0]["content"]
        assert supplied["read_complete"] is False
        row = supplied["unfinished_delegations"]["items"][0]
        assert row["request_id"] == rid
        assert row["decision"] == "adopt"
        assert row["step"] == "review_request_work"
        assert "Private deliberation" not in json.dumps(supplied)
    assert {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")} == before
    report(root, "research", "worker", rid, "conclusion", "The artifact passed validation.")
    assert agenda(root, registry)["items"][0]["step"] == "await_return_delivery"
    drain(root, registry, store, lambda *_: pytest.fail("local return does not use an external sender"))
    assert agenda(root, registry)["items"] == []
    assert len([m for m in store.messages(session["session_id"]) if m.get("origin") == "manager_followup"]) == 1


def test_outgoing_request_and_return_require_explicit_consumption(tmp_path):
    root = tmp_path
    registry = root / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "research", "repo": str(root),
        "coordination": {"registered_agents": ["worker", "reviewer"]}}]}))
    brief = {"schema_version": "collaboration_brief_v0", "purpose": "Independently inspect the revised artifact",
        "context": "Keep the receiver's execution authority", "constraints": [], "inputs": [],
        "acceptance": ["Return actual findings"], "return_requirement": "Return to the original requester"}
    receipt = request(root, registry, "research", "worker", "reviewer", "independent-review", brief)
    rid = receipt["request_id"]
    assert agenda(root, registry)["items"][0]["step"] == "await_peer_result"
    acknowledge(root, "research", "reviewer", rid, "adopt", "Inspect current source")
    report(root, "research", "reviewer", rid, "conclusion", "One boundary requires correction.")
    assert agenda(root, registry)["items"][0]["step"] == "consume_peer_result"
    read_inbox(root, registry, "research", "worker")
    assert agenda(root, registry)["items"][0]["step"] == "consume_peer_result"
    assert agenda(root, registry, "reviewer")["items"][0]["step"] == "await_peer_consumption"
    consume_return(root, "research", "worker", rid, registry=registry)
    assert agenda(root, registry)["items"] == []
    assert agenda(root, registry, "reviewer")["items"] == []


def test_invalid_receipt_and_invalid_entries_are_visible_as_unavailable(flow):
    root, registry, _store, create = flow
    _session, _turn, receipt = create()
    rid = receipt["request_id"]
    _write(_root(root) / "decisions" / (rid + ".json"), {"wrong": "identity"})
    assert agenda(root, registry)["items"][0]["step"] == "recover_evidence"
    entry = next((_root(root) / "entries").glob("*/*.json"))
    entry.write_text("{broken")
    result = agenda(root, registry)
    assert result["scan_complete"] is False
    assert result["unavailable_count"] == 1
    assert result["items"] == []


def test_separate_agent_has_no_access_to_another_receivers_agenda(flow):
    root, registry, _store, create = flow
    create()
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("unrelated")
    registry.write_text(json.dumps(data))
    assert agenda(root, registry, "unrelated")["items"] == []


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("delivery", ["host_envelope", pytest.param("native_files",
    marks=pytest.mark.skipif(os.name != "posix", reason="native context delivery requires POSIX"))])
@pytest.mark.parametrize("partial", [False, True], ids=["unanswered", "partial"])
def test_actual_cli_guards_and_host_envelopes_preserve_the_agenda(tmp_path, monkeypatch, provider, delivery, partial):
    from control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
    from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
    from loopx.control_plane.todos.goal_todo_projection import retained_todo_summary_fields
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    root, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    text = ("---\nstatus: active\n---\n# Goal\n## Objective\nQualify the report.\n## Agent Todo\n"
        "- [ ] [P1] Inspect the current report and return findings.\n"
        "  <!-- loopx:todo todo_id=todo_inspect status=open task_class=advancement_task claimed_by=worker action_kind=inspect_report -->\n"
        "- [x] Artifact implementation finished, but its request still needs a returned answer.\n"
        "  <!-- loopx:todo todo_id=todo_artifact status=done task_class=advancement_task claimed_by=worker action_kind=implement -->\n")
    state.write_text(text)
    write_fixture_registry(project=tmp_path, runtime_root=root, registry_path=registry,
        goal_id="research", domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["worker", "reviewer"], quota_allowed_slots=None)
    goal = json.loads(registry.read_text())["goals"][0]
    fields, _, _ = parse_todo_source(text, goal=goal, state_path=state)
    todos = retained_todo_summary_fields(fields["agent"], rollout_events=[])["agent_todos"]["items"]
    initialize_canonical_authority(root, "research", build_todo_runtime_shadow_projection(
        goal_id="research", todos=todos, handoff_mode="soft_claim"), state_path=state, provider=provider)
    receipt = deliver(root, registry, session={"session_id": "source", "channel_id": "manager"},
        turn={"client_turn_id": "request", "origin": "web", "message": "Verify the artifact before adopting it."},
        request={"goal_id": "research", "agent_id": "worker"})
    acknowledge(root, "research", "worker", receipt["request_id"], "adopt", "Continue the linked work", registry=registry)
    from loopx.control_plane.collaboration.links import link
    link(root, registry, "research", "worker", receipt["request_id"], ["todo_artifact"], [])
    remaining_work = {"summary": "Validate the packaged repair", "resume_when": "The isolated package is available"}
    if partial:
        report(root, "research", "worker", receipt["request_id"], "conclusion", "The implementation is ready.",
            remaining_work=remaining_work, registry=registry)
    before = {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")}
    if delivery == "native_files":
        from loopx.control_plane.turn_driver.codex_context import codex_context_directory, codex_context_hook
        (tmp_path / "captures").mkdir()
        (tmp_path / "context").mkdir(mode=0o700)
        monkeypatch.setenv("CODEX_THREAD_ID", "agenda-session")
        directory = codex_context_directory(root=str(tmp_path / "context"), create=True)
        codex_context_hook(directory, {"session_id": "agenda-session", "cwd": str(tmp_path),
            "hook_event_name": "SessionStart", "source": "startup"})
    for turn_id in ("agenda-turn-one", "agenda-turn-two"):
        presentation = ["--turn-envelope"] if delivery == "host_envelope" else [
            "--codex-app", "--context-delivery", "on", "--decision-output-root", str(tmp_path / "captures"),
            "--codex-context-root", str(tmp_path / "context")]
        code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "research",
            "--agent-id", "worker",  "--turn-instance-id", turn_id,
            *presentation, registry_path=registry, runtime_root=root)
        assert code == 0, (packet.get("error_code"), packet.get("reason"), packet.get("recovery"))
        if delivery == "native_files":
            if turn_id == "agenda-turn-one":
                assert packet["mode"] == "full" and "first_read_command" in packet
                command = packet["first_read_command"]
            else:
                # Warm delivery retains its delta reader; recover this Turn's
                # complete current capture, never prefetch an older baseline.
                assert packet["mode"] == "delta"
                command = packet["recovery"]["command"] + " --initial-read"
            words = shlex.split(command)
            assert words[0] == "loopx"
            code, page = run_json_cli_result(*words[1:], registry_path=registry, runtime_root=root)
            assert code == 0 and page["ok"] and page["eof"], page
            values = {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]}
            pointer = next(key for key in values if key.endswith("/content/unfinished_delegations"))
            content = {"unfinished_delegations": values[pointer],
                "read_complete": values[pointer.removesuffix("unfinished_delegations") + "read_complete"]}
            required_reads = values["/interaction_contract/agent_channel/required_reads"]
        else:
            source = next(row for row in packet["work_context"]["sources"] if row.get("hook_id") == "manager.context_inbox")
            content, required_reads = source["content"], packet["required_reads"]
            assert packet["work_context"]["complete"] is True
        assert content["read_complete"] is False
        row = content["unfinished_delegations"]["items"][0]
        assert row["request_id"] == receipt["request_id"]
        assert row["todo_ids"] == ["todo_artifact"] and row["step"] == "review_request_work"
        assert row.get("remaining_work") == (remaining_work if partial else None)
        assert any("manager-inbox read" in item["command"] for item in required_reads)
        assert "Continue the linked work" not in json.dumps(content)
    assert {p.relative_to(root): p.read_bytes() for p in _root(root).rglob("*.json")} == before


def test_real_overflow_keeps_total_and_full_read_route(flow):
    root, registry, _store, create = flow
    for _ in range(26):
        create()
    result = agenda(root, registry)
    assert result["incoming_count"] == 26
    assert len(result["items"]) == 20
    assert result["omitted_count"] == 6
    assert result["scan_complete"] is True
    assert "manager-inbox read" in result["read_command"]
