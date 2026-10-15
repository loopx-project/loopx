from __future__ import annotations

import json
import subprocess
import sys

import pytest

from test_goal_amendment_authority import source_goal as source_goal
from test_goal_amendment_authority import _call as goal_call, _root as goal_root, _basis as goal_basis
from loopx.control_plane.collaboration.peers import request as peer_request
from loopx.control_plane.collaboration.request_control_source import execute_request_control
from loopx.control_plane.collaboration.request_amendment import recover_request_amendment, control_request
from loopx.control_plane.collaboration.inbox import _root, acknowledge
from loopx.cli import main
from loopx.capabilities.todo_replan_cadence.machine_defaults import resolve_todo_replan_cadence_goal
from loopx.collaboration_mcp import register_collaboration_tools
from loopx.control_plane.projects.registry_codec import source_session_registry_transaction


BRIEF = {"schema_version": "collaboration_brief_v0", "purpose": "Validate this contract",
    "context": "Keep the original Goal constraints", "constraints": ["No remote writes"], "inputs": [],
    "acceptance": ["Independent check passes"], "return_requirement": "Return the check result"}


def _request(f):
    return peer_request(f["runtime"], f["registry"], f["ref"]["goal_id"], "agent-b", "agent-a", "request-check", BRIEF,
        caller_goal_ref=f["ref"])


def _control(f, request_id, operation, parameters=None, *, actor="agent-b", execute=True):
    return execute_request_control(registry_path=f["registry"], runtime_root=f["runtime"], goal_ref=f["ref"],
        receiver_agent_id="agent-a", request_id=request_id, actor_agent_id=actor, operation=operation,
        parameters=parameters, execute=execute)


def _basis(view):
    return {"expected_revision": view["revision"], "expected_digest": view["revision_digest"]}


def _inbox(f, capsys, action, *args, actor="agent-a"):
    command = ["--registry", str(f["registry"]), "--runtime-root", str(f["runtime"]), "--format", "json",
        "manager-inbox", action, "--goal-id", f["ref"]["goal_id"], "--agent-id", actor, *args]
    code = main(command)
    return code, json.loads(capsys.readouterr().out)


def _proposal(f, tmp_path, capsys):
    delivered = _request(f)
    rid = delivered["request_id"]
    acknowledge(f["runtime"], f["ref"]["goal_id"], "agent-a", rid, "adopt", "Check the current source",
        registry=f["registry"], caller_goal_ref=f["ref"])
    view = _control(f, rid, "inspect", actor="agent-a")["view"]
    source = {"request_id": rid, "request_revision": view["revision"], "request_digest": view["revision_digest"]}
    code, goal = goal_call(f, tmp_path, capsys, "inspect", agent=None)
    assert code == 0
    assert goal_call(f, tmp_path, capsys, "configure", goal_root(goal), agent=None, operation_id="root", execute=True)[0] == 0
    _, goal = goal_call(f, tmp_path, capsys, "inspect", agent=None)
    assert goal_call(f, tmp_path, capsys, "adopt", goal_basis(goal), operation_id="adopt-root", execute=True)[0] == 0
    code, proposed = goal_call(f, tmp_path, capsys, "propose", {**goal_basis(goal), "proposal_id": "request-proposal",
        "template_id": "check", "evidence_refs": ["evidence:request-gap"], "replan_obligation_id": f["obligation"],
        "source_request": source}, operation_id="propose-source", execute=True)
    assert code == 0, proposed
    proposal = proposed["proposal"]
    return rid, {"source_request": source, "proposal_id": proposal["proposal_id"], "proposal_digest": proposal["digest"]}


def test_actual_source_request_correction_cancel_and_same_operation_recovery(source_goal):
    f = source_goal
    delivered = _request(f)
    rid = delivered["request_id"]
    entry_path = next(_root(f["runtime"]).glob("entries/*/" + rid + ".json"))
    original_bytes = entry_path.read_bytes()
    original = json.loads(original_bytes)
    view = _control(f, rid, "inspect")["view"]
    assert entry_path.read_bytes() == original_bytes
    params = {"operation_id": "correct-1", **_basis(view), "brief": {**BRIEF, "context": "The input has changed"},
        "reason": "Read the corrected input"}
    preview = _control(f, rid, "revise", params, execute=False)
    assert preview["planned"] is True and preview["executed"] is False
    assert entry_path.read_bytes() == original_bytes
    applied = _control(f, rid, "revise", params)
    assert applied["view"]["revision"] == 2
    saved = entry_path.read_bytes()
    assert _control(f, rid, "revise", params)["replayed"] is True
    assert entry_path.read_bytes() == saved
    stored = json.loads(saved)
    assert {k: v for k, v in stored.items() if k != "request_control"} == original
    assert _request(f) == {**delivered, "replayed": True}
    assert entry_path.read_bytes() == saved
    with pytest.raises(ValueError, match="basis changed"):
        _control(f, rid, "revise", {**params, "operation_id": "stale-correction"})
    with pytest.raises(ValueError, match="authenticated source"):
        _control(f, rid, "cancel", {"operation_id": "receiver-cancel", **_basis(applied["view"]), "reason": "Cancel"}, actor="agent-a")
    cancelled = _control(f, rid, "cancel", {"operation_id": "source-cancel", **_basis(applied["view"]), "reason": "Stop"})
    assert cancelled["view"]["control_state"] == "cancelled"
    assert cancelled["view"]["terminal_effects"] == []


def test_request_control_refuses_wrong_actor_namespace_and_untrusted_effect_facts(source_goal, tmp_path):
    f = source_goal
    rid = _request(f)["request_id"]
    with pytest.raises(ValueError, match="original requester or receiver"):
        _control(f, rid, "inspect", actor="not-the-sender")
    with pytest.raises(ValueError, match="runtime does not match"):
        execute_request_control(registry_path=f["registry"], runtime_root=tmp_path / "unrelated", goal_ref=f["ref"],
            receiver_agent_id="agent-a", request_id=rid, actor_agent_id="agent-b", operation="inspect")
    with pytest.raises(ValueError, match="source-owned"):
        _control(f, rid, "inspect", {"terminal": {"outcome": "committed"}})
    with pytest.raises(ValueError, match="unavailable at this entrypoint"):
        _control(f, rid, "settle", {"reservation_digest": "a" * 64})
    with pytest.raises(ValueError, match="invalid controlled request"):
        _control(f, "../not-a-request", "inspect")


def test_real_request_reservation_goal_commit_and_terminal_linkage(source_goal, tmp_path, capsys, monkeypatch):
    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    before = {p.relative_to(_root(f["runtime"])): p.read_bytes() for p in _root(f["runtime"]).rglob("*.json")}
    # The current Goal configuration reader must pass through both the public
    # request wrapper and its reserved-effect caller after integration.
    from loopx.capabilities.todo_replan_cadence import machine_defaults

    def unavailable(*_args, **_kwargs):
        raise OSError("synthetic current configuration unavailable")

    with monkeypatch.context() as missing_config:
        missing_config.setattr(machine_defaults, "resolve_todo_replan_cadence_goal", unavailable)
        code, refused = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=False)
        assert code == 1 and "configuration unavailable" in str(refused), refused
    assert {p.relative_to(_root(f["runtime"])): p.read_bytes() for p in _root(f["runtime"]).rglob("*.json")} == before
    code, preview = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=False)
    assert code == 0, preview
    assert preview["goal_changed"] is False and preview["terminal_recorded"] is False
    assert {p.relative_to(_root(f["runtime"])): p.read_bytes() for p in _root(f["runtime"]).rglob("*.json")} == before
    code, applied = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=True)
    assert code == 0, applied
    assert applied["effect"]["outcome"] == "committed"
    assert applied["effect"]["goal_revision"] == 2
    assert applied["request_linked"] is True and applied["request_control"]["active_reservation"] is None
    _, goal = goal_call(f, tmp_path, capsys, "inspect", agent=None)
    assert goal["intent"]["created_todo_ids"] == [applied["effect"]["todo_id"]]
    saved = {p.relative_to(_root(f["runtime"])): p.read_bytes() for p in _root(f["runtime"]).rglob("*.json")}
    # Already committed history remains recoverable even if current proposal
    # configuration is unavailable; it cannot authorize a new effect.
    with monkeypatch.context() as missing_config:
        missing_config.setattr(machine_defaults, "resolve_todo_replan_cadence_goal", unavailable)
        code, replayed = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=True)
    assert code == 0 and replayed["effect"] == applied["effect"]
    assert {p.relative_to(_root(f["runtime"])): p.read_bytes() for p in _root(f["runtime"]).rglob("*.json")} == saved
    assert goal_call(f, tmp_path, capsys, "inspect", agent=None)[1]["provider_revision"] == goal["provider_revision"]
    assert _control(f, rid, "inspect")["view"]["terminal_effects"][0]["terminal"] == applied["effect"]


def _recover(f, rid, *, execute=True):
    return recover_request_amendment(registry_path=f["registry"], runtime_root=f["runtime"], goal_ref=f["ref"],
        replan_configuration_reader=resolve_todo_replan_cadence_goal,
        receiver_agent_id="agent-a", request_id=rid, actor_agent_id="agent-b", execute=execute)


def _cancel(f, rid):
    view = _control(f, rid, "inspect")["view"]
    return _control(f, rid, "cancel", {"operation_id": "cancel-race", **_basis(view), "reason": "Stop this request"})


@pytest.mark.parametrize("winner", ["commit", "abort"])
def test_request_cancellation_competes_for_one_real_goal_terminal_record(source_goal, tmp_path, capsys, monkeypatch, winner):
    from loopx.control_plane.collaboration import request_amendment as io

    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    actual = io._goal
    observations = []

    def race(registry, root, ref, receiver, operation, parameters, **kwargs):
        if operation != "commit" or not kwargs.get("execute"):
            return actual(registry, root, ref, receiver, operation, parameters, **kwargs)
        if winner == "commit":
            response = actual(registry, root, ref, receiver, operation, parameters, **kwargs)
        assert _cancel(f, rid)["view"]["control_state"] == "cancellation_pending"
        observations.append(_recover(f, rid))
        if winner == "abort":
            response = actual(registry, root, ref, receiver, operation, parameters, **kwargs)
        return response

    monkeypatch.setattr(io, "_goal", race)
    code, result = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=True)
    assert code == 0, result
    expected = "committed" if winner == "commit" else "aborted"
    assert result["effect"]["outcome"] == expected
    assert observations[0]["effect"] == result["effect"]
    assert result["request_linked"] is True
    assert result["request_control"]["control_state"] == "cancelled"
    _, goal = goal_call(f, tmp_path, capsys, "inspect", agent=None)
    assert goal["intent"]["goal_revision"] == (2 if winner == "commit" else 1)
    assert len(goal["intent"]["created_todo_ids"]) == (1 if winner == "commit" else 0)


def test_lost_request_linkage_keeps_known_goal_effect_and_recovers_without_reapplying(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.collaboration import request_amendment as io

    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    actual = io._execute

    def lose_link(**kwargs):
        if kwargs["operation"] == "settle":
            raise OSError("request linkage unavailable")
        return actual(**kwargs)

    with monkeypatch.context() as unavailable:
        unavailable.setattr(io, "_execute", lose_link)
        code, result = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=True)
    assert code == 1 and result["request_linked"] is False
    assert result["effect"]["outcome"] == "committed" and result["effect"]["goal_changed"] is True
    assert result["request_control"]["control_state"] == "effect_pending"
    _, goal = goal_call(f, tmp_path, capsys, "inspect", agent=None)
    recovered = _recover(f, rid)
    assert recovered["ok"] and recovered["effect"] == result["effect"]
    assert goal_call(f, tmp_path, capsys, "inspect", agent=None)[1]["provider_revision"] == goal["provider_revision"]


def test_unknown_goal_outcome_survives_new_process_and_late_commit_after_abort(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.collaboration import request_amendment as io

    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    actual = io._goal
    queued = []

    def stop_before_effect(*args, **kwargs):
        if args[4] == "commit" and kwargs.get("execute"):
            queued.append((args, kwargs))
            raise OSError("worker exited before a known Goal outcome")
        return actual(*args, **kwargs)

    with monkeypatch.context() as crashed:
        crashed.setattr(io, "_goal", stop_before_effect)
        code, pending = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=True)
    assert code == 1 and pending["effect"] is None and pending["recovery_required"] is True
    assert _control(f, rid, "inspect")["view"]["active_reservation"] is not None
    replacement_code, replacement = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="new-identity-cannot-reassign", execute=True)
    assert replacement_code == 1 and "cannot reserve another effect" in str(replacement)
    assert _cancel(f, rid)["view"]["control_state"] == "cancellation_pending"
    cancelled = _recover(f, rid)
    assert cancelled["effect"]["outcome"] == "aborted"
    delayed = actual(*queued[0][0], **queued[0][1])
    assert delayed["request_outcome"] == "aborted" and delayed["goal_changed"] is False
    request_file = tmp_path / "late-commit.json"
    request_file.write_text(json.dumps(commit))
    restarted = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(f["registry"]),
        "--runtime-root", str(f["runtime"]), "--format", "json", "goal-amendment-proposal", "--governed", "commit",
        "--goal-id", f["ref"]["goal_id"], "--goal-instance-id", f["ref"]["goal_instance_id"], "--agent-id", "agent-a",
        "--operation-id", "commit-request", "--request-json", str(request_file), "--execute"],
        cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert restarted.returncode == 0, restarted.stderr + restarted.stdout
    assert json.loads(restarted.stdout)["effect"] == cancelled["effect"]
    stripped = {key: commit[key] for key in ("proposal_id", "proposal_digest")}
    code, refusal = goal_call(f, tmp_path, capsys, "commit", stripped, operation_id="source-context-cannot-be-dropped", execute=True)
    assert code == 1 and "source context cannot be discarded" in str(refusal)
    assert goal_call(f, tmp_path, capsys, "inspect", agent=None)[1]["intent"]["created_todo_ids"] == []


def test_reserved_basis_rejection_keeps_independent_goal_work(source_goal, tmp_path, capsys):
    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    _, before = goal_call(f, tmp_path, capsys, "inspect", agent=None)
    code, independent = goal_call(f, tmp_path, capsys, "propose", {**goal_basis(before), "proposal_id": "independent-work",
        "template_id": "check", "evidence_refs": ["evidence:independent-work"], "replan_obligation_id": f["obligation"]},
        operation_id="independent-proposal", execute=True)
    assert code == 0, independent
    code, changed = goal_call(f, tmp_path, capsys, "commit", {"proposal_id": "independent-work",
        "proposal_digest": independent["proposal"]["digest"]}, operation_id="independent-commit", execute=True)
    assert code == 0, changed
    _, canonical = goal_call(f, tmp_path, capsys, "inspect", agent=None)
    code, rejected = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="obsolete-request-commit", execute=True)
    assert code == 0 and rejected["request_linked"] is True, rejected
    assert rejected["effect"]["outcome"] == "rejected" and rejected["effect"]["goal_changed"] is False
    after = goal_call(f, tmp_path, capsys, "inspect", agent=None)[1]
    assert after["intent"] == canonical["intent"] and after["work_items"] == canonical["work_items"]
    assert _control(f, rid, "inspect")["view"]["active_reservation"] is None


def test_correction_before_reservation_refuses_obsolete_source_without_goal_mutation(source_goal, tmp_path, capsys):
    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    current = _control(f, rid, "inspect")["view"]
    _control(f, rid, "revise", {"operation_id": "correction-before-commit", **_basis(current),
        "brief": {**BRIEF, "context": "The source was corrected"}, "reason": "Use new evidence"})
    code, refusal = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=True)
    assert code == 1 and "basis changed" in str(refusal)
    view = _control(f, rid, "inspect")["view"]
    assert view["active_reservation"] is None and view["terminal_effects"] == []
    assert view["revision"] == 2
    assert goal_call(f, tmp_path, capsys, "inspect", agent=None)[1]["intent"]["created_todo_ids"] == []


def test_receiver_cli_reads_assesses_and_returns_the_correct_revision(source_goal, tmp_path, capsys):
    f = source_goal
    rid = _request(f)["request_id"]
    code, original = _inbox(f, capsys, "read")
    assert code == 0 and "When request_revision is supplied" not in original["followthrough"]
    assert _inbox(f, capsys, "acknowledge", "--request-id", rid, "--decision", "adopt", "--reason", "Original assessment")[0] == 0
    assert _inbox(f, capsys, "report", "--request-id", rid, "--reply-text", "Original result")[0] == 0
    original_decision = (_root(f["runtime"]) / "decisions" / (rid + ".json")).read_bytes()
    original_result = (_root(f["runtime"]) / "replies" / rid / "conclusion.json").read_bytes()
    view = _control(f, rid, "inspect")["view"]
    _control(f, rid, "revise", {"operation_id": "correction-2", **_basis(view),
        "brief": {**BRIEF, "context": "Use the corrected source"}, "reason": "Source correction"})
    code, supplied = _inbox(f, capsys, "read")
    assert code == 0, supplied
    item = next(item for item in supplied["items"] if item["request_id"] == rid)
    assert item["request_revision"] == 2 and item["brief"]["context"] == "Use the corrected source"
    assert "When request_revision is supplied" in supplied["followthrough"]
    assert item["recorded_decision"] is None
    assert item["request_read_receipt"]["supplied_revision"] == 2
    assert "request_control" not in item
    assert _control(f, rid, "inspect")["view"]["read_at"] is not None
    code, assessed = _inbox(f, capsys, "acknowledge", "--request-id", rid, "--decision", "adopt", "--reason", "Use the correction",
        "--request-revision", "2", "--request-digest", item["request_revision_digest"])
    assert code == 0 and assessed["request_revision"] == 2, assessed
    code, answer = _inbox(f, capsys, "report", "--request-id", rid, "--reply-text", "Revised result", "--request-revision", "2")
    assert code == 0 and answer["request_revision"] == 2, answer
    assert answer["result_key"] != "conclusion"
    code, historical = _inbox(f, capsys, "report", "--request-id", rid, "--reply-text", "Original result")
    assert code == 0 and historical["request_revision"] == 1 and historical["current_request_revision"] == 2, historical
    assert (_root(f["runtime"]) / "decisions" / (rid + ".json")).read_bytes() == original_decision
    assert (_root(f["runtime"]) / "replies" / rid / "conclusion.json").read_bytes() == original_result
    assert all(item["request_id"] != rid for item in _inbox(f, capsys, "read")[1]["items"])
    current = _control(f, rid, "inspect")["view"]
    _control(f, rid, "revise", {"operation_id": "correction-3", **_basis(current),
        "brief": {**BRIEF, "context": "A further correction"}, "reason": "Preserve new context"})
    late = _inbox(f, capsys, "report", "--request-id", rid, "--reply-text", "Later evidence for original work", "--update-id", "old-evidence")
    assert late[0] == 0 and late[1]["request_revision"] == 1
    pending = next(item for item in _inbox(f, capsys, "read")[1]["items"] if item["request_id"] == rid)
    assert pending["request_revision"] == 3 and pending["recorded_decision"] is None
    assert pending["inbox_state"] == "pending"


def test_unknown_request_effect_does_not_block_a_new_independent_request(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.collaboration import request_amendment as io

    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    original = io._goal

    def lost(*args, **kwargs):
        if args[4] == "commit" and kwargs.get("execute"):
            raise OSError("Effect outcome is unknown")
        return original(*args, **kwargs)

    with monkeypatch.context() as changed:
        changed.setattr(io, "_goal", lost)
        code, pending = goal_call(f, tmp_path, capsys, "commit", commit,
            operation_id="pending-request-effect", execute=True)
        assert code == 1 and pending["effect"] is None, pending
    second = peer_request(f["runtime"], f["registry"], f["ref"]["goal_id"], "agent-b", "agent-a",
        "independent-request", BRIEF, caller_goal_ref=f["ref"])["request_id"]
    assert second != rid
    acknowledge(f["runtime"], f["ref"]["goal_id"], "agent-a", second, "adopt", "Independent request",
        registry=f["registry"], caller_goal_ref=f["ref"])
    basis = _control(f, second, "inspect")["view"]
    source = {"request_id": second, "request_revision": basis["revision"], "request_digest": basis["revision_digest"]}
    current = goal_call(f, tmp_path, capsys, "inspect", agent=None)[1]
    code, proposed = goal_call(f, tmp_path, capsys, "propose", {**goal_basis(current), "proposal_id": "independent-proposal",
        "template_id": "check", "evidence_refs": ["evidence:independent-request"], "replan_obligation_id": f["obligation"],
        "source_request": source}, operation_id="propose-independent", execute=True)
    assert code == 0, proposed
    code, result = goal_call(f, tmp_path, capsys, "commit", {"source_request": source,
        "proposal_id": proposed["proposal"]["proposal_id"], "proposal_digest": proposed["proposal"]["digest"]},
        operation_id="commit-independent", execute=True)
    assert code == 0 and result["effect"]["outcome"] == "committed", result
    assert _control(f, rid, "inspect")["view"]["active_reservation"] is not None
    assert _control(f, second, "inspect")["view"]["active_reservation"] is None


def test_bound_mcp_consumers_keep_revision_and_return_relations(source_goal):
    f = source_goal
    rid = _request(f)["request_id"]

    class Server:
        def __init__(self):
            self.tools = {}

        def tool(self, **_kwargs):
            def register(function):
                self.tools[function.__name__] = function
                return function
            return register

    requester, receiver = Server(), Server()
    for server, actor in [(requester, "agent-b"), (receiver, "agent-a")]:
        register_collaboration_tools(server, f["runtime"], f["registry"], f["ref"]["goal_id"], actor, f["state"].parent)
    view = requester.tools["control_peer_request"](rid, "agent-a", "inspect")["view"]
    requester.tools["control_peer_request"](rid, "agent-a", "revise", {"operation_id": "mcp-revision", **_basis(view),
        "brief": {**BRIEF, "context": "MCP current source"}, "reason": "Source corrected"}, execute=True)
    current = receiver.tools["read_context"]()["items"][0]
    assert current["request_revision"] == 2
    receiver.tools["assess_request"](rid, "adopt", "Assess revision two", request_revision=2, request_digest=current["request_revision_digest"])
    receiver.tools["return_result"](rid, "Result for revision two", request_revision=2)
    result = requester.tools["read_context"]()["peer_returns"]["items"][0]
    assert result["request_revision"] == 2 and result["current_request_revision"] == 2
    assert result["brief"]["context"] == "MCP current source" and result["text"] == "Result for revision two"


@pytest.mark.parametrize("unavailable", ["assessment", "receiver"])
def test_source_can_settle_cancellation_without_reusing_lost_receiver_authority(source_goal, tmp_path, capsys, monkeypatch, unavailable):
    from loopx.control_plane.collaboration import request_amendment as io

    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    actual = io._goal

    def stop_before_effect(*args, **kwargs):
        if args[4] == "commit" and kwargs.get("execute"):
            raise OSError("unknown effect")
        return actual(*args, **kwargs)

    with monkeypatch.context() as stopped:
        stopped.setattr(io, "_goal", stop_before_effect)
        assert goal_call(f, tmp_path, capsys, "commit", commit, operation_id="commit-request", execute=True)[0] == 1
    if unavailable == "assessment":
        (_root(f["runtime"]) / "decisions" / (rid + ".json")).write_text("{unavailable")
        assert _control(f, rid, "inspect")["view"]["assessment_readback"] == "unavailable"
    else:
        with source_session_registry_transaction(f["registry"], operation="test_remove_receiver") as transaction:
            data = transaction.payload_copy()
            data["goals"][0]["coordination"]["registered_agents"] = ["agent-b"]
            transaction.commit(data)
    assert _cancel(f, rid)["view"]["control_state"] == "cancellation_pending"
    recovered = _recover(f, rid)
    assert recovered["request_linked"] is True and recovered["effect"]["outcome"] == "aborted", recovered
    assert recovered["request_control"]["control_state"] == "cancelled"
    assert goal_call(f, tmp_path, capsys, "inspect", agent=None)[1]["intent"]["created_todo_ids"] == []


def test_source_correction_aborts_unknown_effect_and_next_revision_can_continue(source_goal, tmp_path, capsys, monkeypatch):
    from loopx.control_plane.collaboration import request_amendment as io

    f = source_goal
    rid, commit = _proposal(f, tmp_path, capsys)
    actual = io._goal

    def unknown(*args, **kwargs):
        if args[4] == "commit" and kwargs.get("execute"):
            raise OSError("unknown original effect")
        return actual(*args, **kwargs)

    with monkeypatch.context() as stopped:
        stopped.setattr(io, "_goal", unknown)
        assert goal_call(f, tmp_path, capsys, "commit", commit, operation_id="old-effect", execute=True)[0] == 1
    current = _control(f, rid, "inspect")["view"]
    corrected = control_request(registry_path=f["registry"], runtime_root=f["runtime"], goal_ref=f["ref"],
        replan_configuration_reader=resolve_todo_replan_cadence_goal,
        receiver_agent_id="agent-a", request_id=rid, actor_agent_id="agent-b", operation="revise", execute=True,
        parameters={"operation_id": "correct-unknown", **_basis(current), "brief": {**BRIEF, "context": "Continue from corrected source"}, "reason": "Use the new input"})
    assert corrected["ok"] and corrected["view"]["control_state"] == "open", corrected
    assert corrected["view"]["cancellation"] is None
    assert corrected["effect_recovery"]["effect"]["outcome"] == "aborted"
    revised = corrected["view"]
    assert _inbox(f, capsys, "acknowledge", "--request-id", rid, "--decision", "adopt", "--reason", "Use revision two",
        "--request-revision", "2", "--request-digest", revised["revision_digest"])[0] == 0
    _, current_goal = goal_call(f, tmp_path, capsys, "inspect", agent=None)
    source = {"request_id": rid, "request_revision": 2, "request_digest": revised["revision_digest"]}
    code, proposal = goal_call(f, tmp_path, capsys, "propose", {**goal_basis(current_goal), "source_request": source,
        "proposal_id": "corrected-proposal", "template_id": "check", "evidence_refs": ["evidence:corrected-source"],
        "replan_obligation_id": f["obligation"]}, operation_id="propose-correction", execute=True)
    assert code == 0, proposal
    new_commit = {"source_request": source, "proposal_id": "corrected-proposal", "proposal_digest": proposal["proposal"]["digest"]}
    code, applied = goal_call(f, tmp_path, capsys, "commit", new_commit, operation_id="new-effect", execute=True)
    assert code == 0 and applied["effect"]["outcome"] == "committed", applied
    old = goal_call(f, tmp_path, capsys, "commit", commit, operation_id="old-effect", execute=True)[1]
    assert old["effect"]["outcome"] == "aborted"
    assert len(goal_call(f, tmp_path, capsys, "inspect", agent=None)[1]["intent"]["created_todo_ids"]) == 1
