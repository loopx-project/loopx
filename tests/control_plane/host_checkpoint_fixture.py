"""A deterministic current-Agent response after a real checkpoint tool read."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def checkpoint_decision(context):
    basis = context["basis"]
    assert context["read_context_id"]
    todo = basis.get("todo")
    identity = context["settlement_identity"]
    deferred_terminal = bool(todo and todo.get("status") == "done"
        and todo.get("completion_continuation") == "active_goal"
        and todo.get("completion_requested_continuation") == "no_followup"
        and todo.get("completion_turn_key") in {identity["effect_id"], identity["turn_instance_id"]})
    terminal = todo is None or todo.get("no_followup") is True or deferred_terminal
    assert basis["frontier"]["scope"] == ("goal" if terminal else "agent_lane")
    decision = {"read_context_id": context["read_context_id"], "agent_vision_json": "",
        "vision_unchanged_reason": "", "stop_reason": ""}
    if basis["agent_vision"]:
        decision["vision_unchanged_reason"] = "The observed fixture obligations preserve the scoped route."
    else:
        decision["agent_vision_json"] = json.dumps({"schema_version": "goal_vision_replan_contract_v0",
            "state": "vision_patch_proposed", "vision_patch": {"vision_summary": "Verify the scoped fixture.",
            "acceptance_summary": "Fixture evidence has been verified."}, "path_delta": {
                "schema_version": "goal_path_delta_v0", "outcome": "no_change",
                "prior_assumption": "Scoped fixture route.", "observed_reality": "Fixture evidence verified.",
                "retained": ["Scoped route"], "evidence_refs": ["evidence:fixture"]}})
    if decision["agent_vision_json"]:
        decision["agent_vision"] = json.loads(decision.pop("agent_vision_json"))
    return decision


def reply_to_checkpoint(request):
    if request.get("phase") != "checkpoint_direction":
        return False
    json.dump(checkpoint_decision(request["checkpoint_context"]), sys.stdout)
    return True


def checkpoint_fixture_script(script):
    marker = 'from tests.control_plane.host_checkpoint_fixture import reply_to_checkpoint'
    return script.replace(marker, f'sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n' + marker)


def checkpoint_codex_fixture_response(request, runtime_root):
    from loopx.control_plane.turn_driver.codex_sessions import _lineage, _store_codex_cli_session
    if request.get("phase") == "checkpoint_direction":
        assert request["session"]["action"] == "resume"
        return checkpoint_decision(request["checkpoint_context"])
    _store_codex_cli_session(runtime_root, lineage=_lineage(request), session_scope="agent",
        session_id="owned-fixture-session")
    return None
