from dataclasses import replace
from pathlib import Path
import sys

from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
from loopx_memt_turn.config import digest


def turn(number=1, *, goal="public-goal", agent="analyst", instance="ginst_" + "1" * 32):
    envelope = {"schema_version": "loopx_turn_envelope_v0", "goal_id": goal, "agent_id": agent,
                "action": {"primary_action": "Record an observation, then return a candidate.",
                           "selected_todo": {"todo_id": "todo_fixture"}},
                "required_reads": [], "boundary": {"write_scope": ["observations.txt"], "workspace_guard": {}}}
    signature = "sha256:" + digest(turn_envelope_action_signature_document(envelope))
    envelope["action_signature"] = {"matches": True, "source_hash": signature, "envelope_hash": signature}
    return {"schema_version": "loopx_turn_host_request_v0", "turn_key": "sha256:" + f"{number:064x}",
            "goal_ref": {"goal_id": goal, "goal_instance_id": instance},
            "session": {"context_policy": {"mode": "fresh"}}, "turn_envelope": envelope}


def with_tools(config):
    return replace(config, mcp_command=(sys.executable, str(Path(__file__).with_name("fixture_tools.py"))),
                   tool_names=("record_observation",))
