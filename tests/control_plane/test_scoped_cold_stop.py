"""A protected source Turn can cold-resume to a typed stop before a candidate."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.projects.registry_codec import source_session_registry_transaction
from loopx.control_plane.turn_driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.journal_store import turn_journal_path, write_turn_journal_checkpoint
from tests.control_plane.test_checkpoint_provider_fence import (
    INSTANCE_B, _historical_source_context, _replace_source_goal,
)
from tests.control_plane.test_quota_settlement_cli import _spend_run_count
from tests.test_loopx_turn_driver import _envelope


COLD_STOP = """
import json, sys
from pathlib import Path
from loopx.control_plane.goals.checkpoint_context_io import TurnResultWork
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.control_plane.turn_driver import run_loopx_turn_once, load_loopx_turn_plan_from_journal
runtime, registry, goal, key = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
plan = load_loopx_turn_plan_from_journal(runtime, goal_id=goal, turn_key=key)
identity = SettlementIdentity.from_runtime_payload(plan['transaction']['settlement_plan']['identity'])
def host(request):
    assert request['delivery_result_context']['basis']['source']['goal_ref'] == plan['goal_ref']
    assert request['delivery_result_context']['settlement_identity'] == identity.as_dict()
    with (registry.parent / 'host-calls').open('a') as stream:
        stream.write('stop-before-candidate\\n')
    return {'schema_version':'loopx_turn_result_v0', 'turn_key':request['turn_key'],
        'result_kind':'user_action_required', 'completed_phases':['host_execute','typed_result'],
        'summary':'Stop before creating a candidate; retain the original work.'}
def forbidden(*args):
    raise AssertionError('A pre-candidate stop cannot validate, write, spend or schedule')
try:
    result = run_loopx_turn_once(plan, host_runner=host, project=registry.parent,
        runtime_root=runtime, goal_id=goal, timeout_seconds=10, execute=True,
        task_validator=forbidden, writeback=forbidden, spend=forbidden, scheduler=forbidden,
        completion_writeback=forbidden, completion_intent=forbidden, terminal_closeout=forbidden,
        result_work=TurnResultWork(registry, runtime, identity, plan['goal_ref']),
        goal_admission=FirstPartyHostGoalAdmission.for_plan(registry_path=registry,
            goal_id=goal, planned_goal_ref=plan['goal_ref']))
except Exception as error:
    result = {'ok':False,'error_code':getattr(error,'diagnostic_code',None),'error':str(error)}
json.dump(result,sys.stdout)
"""


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("drift", [None, "replacement", "stopped"])
def test_original_precandidate_source_turn_cold_stop_keeps_marker_and_effects(
    tmp_path, monkeypatch, provider, drift,
):
    runtime, registry, ref, identity, _ = _historical_source_context(
        tmp_path, monkeypatch, provider, "delivery_result")
    envelope = _envelope()
    envelope.update(goal_id=identity.goal_id, agent_id=identity.agent_id)
    envelope["action"]["selected_todo"]["todo_id"] = identity.todo_id
    plan = build_loopx_turn_plan(envelope, host="generic-cli", execution_mode="isolated-headless",
        turn_instance_id=identity.turn_instance_id, goal_ref=ref)
    key = plan["transaction"]["turn_key"]
    journal = turn_journal_path(runtime, goal_id=identity.goal_id, turn_key=key)
    admission = FirstPartyHostGoalAdmission.for_plan(
        registry_path=registry, goal_id=identity.goal_id, planned_goal_ref=ref)
    with admission.source_journal_admission(runtime_root=runtime) as source:
        write_turn_journal_checkpoint(journal, {"schema_version":"loopx_turn_journal_v0",
            "goal_id":identity.goal_id, "turn_key":key, "status":"in_progress",
            "completed_phases":[], "host_attempt_count":0, "plan":plan}, source_admission=source)
    if drift == "replacement":
        _replace_source_goal(registry, INSTANCE_B)
    elif drift == "stopped":
        with source_session_registry_transaction(registry, operation="fixture_source_stop") as transaction:
            data = transaction.payload_copy()
            data["goals"][0]["activation"] = {"schema_version":"loopx_goal_activation_v1",
                "state":"stopped", "updated_at":"2026-10-10T12:00:00Z", "reason":"Owner stopped the Goal"}
            transaction.commit(data)
    marker = runtime / "goals" / identity.goal_id / "rollout-event-log.jsonl"
    index = runtime / "goals" / identity.goal_id / "runs/index.jsonl"
    frozen_index = index.read_bytes() if index.exists() else None
    frozen_marker, initial_journal, frozen_registry = marker.read_bytes(), journal.read_bytes(), registry.read_bytes()
    def cold():
        child = subprocess.run([sys.executable, "-c", COLD_STOP, str(runtime), str(registry),
            identity.goal_id, key], cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "PYTHONPATH":str(Path(__file__).resolve().parents[2])},
            text=True, capture_output=True, timeout=60)
        assert child.returncode == 0, child.stderr
        return json.loads(child.stdout)
    result = cold()
    calls = registry.parent / "host-calls"
    if drift:
        assert result["ok"] is False, result
        assert not calls.exists() and journal.read_bytes() == initial_journal
    else:
        assert result["status"] == "stopped" and result["result_kind"] == "user_action_required", result
        assert result["effects"]["host_invoked"] and not result["effects"]["state_written"]
        assert not result["effects"]["quota_spent"] and result["receipt"]["settlement_effect_id"] == identity.effect_id
        stopped_journal = journal.read_bytes()
        replay = cold()
        assert replay["replayed"] and not any(replay["effects"].values()), replay
        assert journal.read_bytes() == stopped_journal and calls.read_text() == "stop-before-candidate\n"
    assert marker.read_bytes() == frozen_marker and registry.read_bytes() == frozen_registry
    assert (index.read_bytes() if index.exists() else None) == frozen_index
    assert _spend_run_count(runtime) == 0
