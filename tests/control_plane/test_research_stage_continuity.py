"""A completed stage cannot hide an already requested, executable next stage.

The existing alias-Goal CLI and owner-configured requirements are exercised
with both canonical Todo providers. Only the model boundary is a deterministic
local program: the scheduler, subprocess Host, Todo writes, actual acceptance
validators and quota settlement are production paths.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shlex
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as fixture
from loopx.control_plane.quota.effect_program import ReceiptBoundReplayPhase
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from tests.test_external_scheduler_worker import worker


def _vision(evidence: str) -> dict:
    return {
        "schema_version": "goal_vision_replan_contract_v0", "state": "no_followup",
        "vision_patch": {
            "vision_summary": "Deliver route A and compare the already requested route B.",
            "acceptance_summary": "Retain each route's local result and the explicit comparison.",
            "last_patch_summary": "This completed stage has no additional local follow-up.",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "no_change",
            "prior_assumption": "The selected stage still needed its local result.",
            "observed_reality": "The selected stage's result is verified; other Todo requirements retain their own scope.",
            "evidence_refs": [evidence],
            "stopped": ["No extra work is requested for this completed stage."],
        },
    }


def _control(registry: Path, runtime: Path) -> GoalModeMCPControlPlane:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(server_name="stage-fixture", runtime_profile="generic_cli",
                          legacy_host_surface="claude_code"),
        lambda: {"goal_id": fixture.GOAL_ID, "agent_id": fixture.AGENT_ID,
                 "registry": str(registry)},
    )
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli", "--runtime-root", str(runtime)]
    return control


_HOST = '''import json,sys,subprocess
from hashlib import sha256
from pathlib import Path
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
request=json.loads(Path(sys.argv[1]).read_text())
def count(kind):
    with Path(request["counts"]).open("a") as stream:
        stream.write(json.dumps({"kind":kind})+"\\n")
count("dispatch")
control=GoalModeMCPControlPlane(
    GoalModeMCPConfig(server_name="stage-fixture",runtime_profile="generic_cli",legacy_host_surface="claude_code"),
    lambda:{"goal_id":request["goal_id"],"agent_id":request["agent_id"],"registry":request["registry"]})
control.command_prefix=lambda:[sys.executable,"-m","loopx.cli","--runtime-root",request["runtime"]]
guard=json.loads(control.should_run())
assert guard["should_run"] is True,guard
assert guard["selected_todo"]["todo_id"]==request["todo_id"],guard
count("model_boundary")
# The controlled model boundary executes an independent, inspectable comparison.
a=json.loads(Path(request["route_a"]).read_text())
b={"route":["start","finish"]}
result={"a_edges":len(a["route"])-1,"b_edges":len(b["route"])-1,"compared":True}
result["b_has_fewer_edges"]=result["b_edges"]<result["a_edges"]
data=(json.dumps(result,sort_keys=True)+"\\n").encode()
Path(request["artifact"]).write_bytes(data)
evidence="fixture:route-b-sha256:"+sha256(data).hexdigest()
vision=request["vision"]
vision["path_delta"]["evidence_refs"]=[evidence]
completed=json.loads(control.complete_task(request["todo_id"],request["agent_id"],evidence,
    no_follow_up=True,agent_vision=vision))
assert completed["ok"] is True,completed
verification=None
if request.get("verify_goal"):
    verified=subprocess.run(control.command_prefix()+["--registry",request["registry"],"--format","json",
        "goal-acceptance","verify","--goal-id",request["goal_id"],"--execute"],
        capture_output=True,text=True,check=True)
    verification=json.loads(verified.stdout)
    assert verification["goal_acceptance_contract"]["status"]=="accepted",verification
Path(request["result"]).write_text(json.dumps({"completion":completed,"evidence":evidence,"vision":vision,
    "verification":verification}))
print(json.dumps({"ok":True,"completed_todo_id":request["todo_id"]}))
'''


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("owner_revises_requirements", [False, True])
def test_stage_no_followup_runs_existing_next_stage_once(tmp_path, monkeypatch, provider, owner_revises_requirements):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYTHONPATH", str(fixture.REPO_ROOT))
    state = project / f".codex/goals/{fixture.GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace("Settle one standard Codex App delivery.",
        "Deliver feasible route A and compare the explicitly requested route B."))
    fixture._initialize_fixture_authority(project, runtime, provider=provider)

    def cli(*args):
        code, packet = fixture._run_cli(registry, runtime, *args, cwd=project)
        assert code == 0, json.dumps(packet)
        return packet

    # Establish B through the real accepted Todo writer before A is completed.
    add = ("todo", "add", "--goal-id", fixture.GOAL_ID, "--role", "agent",
           "--text", "Compare requested route B with the feasible route A.", "--priority", "P1",
           "--task-class", "advancement_task", "--action-kind", "validate",
           "--claimed-by", fixture.AGENT_ID, "--operation-id", "owner-request-route-b")
    todo_b = cli(*add)["todo_id"]
    assert cli(*add)["todo_id"] == todo_b
    acceptance_document = {
        "scope": {"kind": "all_advancement"},
        "objective": "Deliver a feasible route A and independently compare the requested route B.",
        "non_goals": ["No proof of a globally optimal route is requested."],
        "criteria": [
            {"id": "route-a", "description": "A is a feasible path through the fixture graph.",
             "validation_argv": [sys.executable, "-c",
                "import json; from pathlib import Path; a=json.loads(Path('route-a.json').read_text()); "
                "assert a['route']==['start','middle','finish']"], "validation_timeout_seconds": 5},
            {"id": "route-b-comparison", "description": "The second route is explicitly compared with A.",
             "validation_argv": [sys.executable, "-c",
                "import json; from pathlib import Path; c=json.loads(Path('comparison.json').read_text()); "
                "assert c=={'a_edges':2,'b_edges':1,'compared':True,'b_has_fewer_edges':True}"],
             "validation_timeout_seconds": 5},
        ],
        "bindings": [
            {"todo_id": fixture.TODO_ID, "criterion_ids": ["route-a"]},
            {"todo_id": todo_b, "criterion_ids": ["route-b-comparison"]},
        ],
    }
    document_path = project / "owner-requirements.json"

    def configure_requirements(document, operation):
        document_path.write_text(json.dumps(document))
        basis = cli("goal-acceptance", "inspect", "--goal-id", fixture.GOAL_ID)["provider_revision"]
        return cli("goal-acceptance", "configure", "--goal-id", fixture.GOAL_ID,
            "--document", str(document_path), "--expected-provider-revision", basis,
            "--operation-id", operation, "--execute")["goal_acceptance_contract"]

    original_requirements = configure_requirements(acceptance_document, "owner-original-a-and-b")
    route_a = project / "route-a.json"
    route_a.write_text(json.dumps({"route": ["start", "middle", "finish"]}))
    control = _control(registry, runtime)
    evidence_a = "fixture:route-a-sha256:" + sha256(route_a.read_bytes()).hexdigest()
    first = json.loads(control.complete_task(fixture.TODO_ID, fixture.AGENT_ID, evidence_a,
        no_follow_up=True, agent_vision=_vision(evidence_a)))
    assert first["ok"] is True, first
    assert cli("goal-acceptance", "inspect", "--goal-id", fixture.GOAL_ID)["goal_acceptance_contract"]["revision"] == original_requirements["revision"]
    before = json.loads(control.should_run())
    assert before["should_run"] is True, before
    assert before["selected_todo"]["todo_id"] == todo_b
    assert before["interaction_contract"]["mode"] != "terminal_no_followup"
    assert before["goal_frontier_projection"].get("terminal_state") is None
    pending_b = cli("todo", "list", "--goal-id", fixture.GOAL_ID, "--todo-id", todo_b)
    assert pending_b["todo"]["status"] == "open"
    assert pending_b["todo"]["text"] == "[P1] Compare requested route B with the feasible route A."

    index = runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl"
    def spends():
        return [row for line in index.read_text().splitlines()
                if (row := json.loads(line)).get("classification") == "quota_slot_spent"]
    assert len(spends()) == 1
    baseline_a = [row for row in spends() if row.get("todo_id") == fixture.TODO_ID]
    repeated_a = json.loads(control.complete_task(fixture.TODO_ID, fixture.AGENT_ID, evidence_a,
        no_follow_up=True, agent_vision=_vision(evidence_a)))
    assert repeated_a["ok"] is True, repeated_a
    assert spends() == baseline_a

    counts, artifact, result = (project / name for name in ("counts.jsonl", "comparison.json", "host-result.json"))
    host = project / "fake-model-host.py"
    host.write_text(_HOST)
    request = project / "host-request.json"
    request.write_text(json.dumps({"goal_id": fixture.GOAL_ID, "agent_id": fixture.AGENT_ID,
        "registry": str(registry), "runtime": str(runtime), "todo_id": todo_b,
        "route_a": str(route_a), "artifact": str(artifact), "counts": str(counts),
        "result": str(result), "vision": _vision("fixture:route-b-result"), "verify_goal": True}))
    launcher = project / "loopx-cli"
    launcher.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, "-m", "loopx.cli"]) + ' "$@"\n')
    launcher.chmod(0o700)
    args = argparse.Namespace(cli_bin=str(launcher), registry=str(registry), runtime_root=str(runtime),
        runtime_profile="generic_cli", goal_id=fixture.GOAL_ID, agent_id=fixture.AGENT_ID,
        state_file=str(tmp_path / "worker-state.json"), wake_cmd=shlex.join([sys.executable, str(host), str(request)]),
        once=True, error_backoff_seconds=5, quota_timeout_seconds=30, wake_timeout_seconds=120)

    if owner_revises_requirements:
        # The trusted owner selects the already validated A in place of B and
        # records the replacement in the existing Todo lifecycle. A-stage prose
        # and no-follow-up alone leave the original requirement intact.
        revised_document = {**acceptance_document, "scope": {"kind": "selected_work", "todo_ids": [fixture.TODO_ID]},
            "objective": "The owner accepts route A alone in place of B; the B comparison is withdrawn.",
            "criteria": acceptance_document["criteria"][:1], "bindings": acceptance_document["bindings"][:1]}
        revised = configure_requirements(revised_document, "owner-withdraws-b-requirement")
        assert revised["revision"] == original_requirements["revision"] + 1
        cli("todo", "supersede", "--goal-id", fixture.GOAL_ID, "--todo-id", todo_b,
            "--agent-id", fixture.AGENT_ID,
            "--successor-todo-id", fixture.TODO_ID,
            "--reason", "The owner accepts the already validated route A in place of B in acceptance contract revision 2.",
            "--evidence", "owner-withdraws-b-requirement")
        replaced_b = cli("todo", "list", "--goal-id", fixture.GOAL_ID, "--todo-id", todo_b)["todo"]
        assert replaced_b["status"] == "done"
        assert replaced_b["superseded_by"] == fixture.TODO_ID
        assert replaced_b["successor_todo_ids"] == [fixture.TODO_ID]
        assert replaced_b["completion_continuation"] == "active_goal"
        assert not replaced_b.get("completion_receipt_id")
        verified = cli("goal-acceptance", "verify", "--goal-id", fixture.GOAL_ID, "--execute")
        assert verified["goal_acceptance_contract"]["status"] == "accepted"
        for _ in range(2):
            assert worker.run_worker(args) == 0
        after = json.loads(control.should_run())
        assert after["should_run"] is False and after["interaction_contract"]["mode"] == "terminal_no_followup"
        assert not counts.exists() and not artifact.exists()  # D=0, P=0 after the accepted owner revision.
        assert spends() == baseline_a
        return

    # This is the existing external scheduler and real subprocess wake, not a
    # test-selected action. D/P start here; A's preparation is outside the count.
    assert worker.run_worker(args) == 0
    assert json.loads(artifact.read_text()) == {
        "a_edges": 2, "b_edges": 1, "compared": True, "b_has_fewer_edges": True,
    }
    adopted = json.loads(result.read_text())
    assert adopted["verification"]["goal_acceptance_contract"]["status"] == "accepted"
    assert adopted["verification"]["goal_acceptance_contract"]["revision"] == original_requirements["revision"]
    finished = cli("todo", "list", "--goal-id", fixture.GOAL_ID, "--todo-id", todo_b)
    assert finished["todo"]["status"] == "done"
    assert finished["authority_read"]["source_authority"] == f"{provider}_v0"
    b_spends = [row for row in spends() if row.get("todo_id") == todo_b]
    assert len(b_spends) == 1
    original = read_heartbeat_settlement(runtime, goal_id=fixture.GOAL_ID, agent_id=fixture.AGENT_ID,
        todo_id=todo_b, turn_instance_id=b_spends[0]["turn_instance_id"], registry_path=registry)
    assert original is not None and original.settlement.failure is None
    assert original.replay_phase is ReceiptBoundReplayPhase.SETTLED
    assert original.completion_event is not None

    for _ in range(2):
        # Duplicate stage-end delivery and a new worker invocation must retain
        # the same completion and spend; no new model program may be started.
        replay = json.loads(control.complete_task(todo_b, fixture.AGENT_ID, adopted["evidence"],
            no_follow_up=True, agent_vision=adopted["vision"]))
        assert replay["ok"] is True, replay
        assert worker.run_worker(args) == 0
    after = json.loads(control.should_run())
    assert after["should_run"] is False and after["interaction_contract"]["mode"] == "terminal_no_followup"
    retained = cli("todo", "list", "--goal-id", fixture.GOAL_ID, "--todo-id", todo_b)
    assert retained["todo"] == finished["todo"]
    assert retained["authority_read"]["provider_revision"] == finished["authority_read"]["provider_revision"]
    assert [row for row in spends() if row.get("todo_id") == fixture.TODO_ID] == baseline_a
    assert [row for row in spends() if row.get("todo_id") == todo_b] == b_spends
    observed = [json.loads(line)["kind"] for line in counts.read_text().splitlines()]
    assert observed == ["dispatch", "model_boundary"]  # D=1, P=1, B E=1, B Q=1.
