"""Initial Goal planning and unbound read-only Host paths stay distinct."""
from __future__ import annotations

import json
import pytest

from tests.control_plane.test_replan_successor_guard_reentry import AGENT, GOAL, _fixture
from tests.control_plane.test_host_research_recovery_cli import ROOT, _spends, _worker_args
from tests.test_external_scheduler_worker import worker


_PLANNING_HOST = r'''import json,sys,uuid
from hashlib import sha256
from pathlib import Path
import subprocess
from loopx.goal_mode_mcp import GoalModeMCPConfig,GoalModeMCPControlPlane
request=json.loads(Path(sys.argv[1]).read_text())
def failed(kind,error,tb):
    import traceback
    Path(request["result"]).write_text(json.dumps({"error_kind":kind.__name__,"error":str(error),
        "traceback":traceback.format_exception(kind,error,tb,limit=8)}))
    sys.__excepthook__(kind,error,tb)
sys.excepthook=failed
def cli(*args):
    result=subprocess.run([sys.executable,"-m","loopx.cli","--registry",request["registry"],
        "--runtime-root",request["runtime"],"--format","json",*args],capture_output=True,text=True,timeout=60)
    payload=json.loads(result.stdout)
    if result.returncode:
        Path(request["result"]).write_text(json.dumps({"failed_command":args,"result":payload}))
    assert result.returncode==0,payload
    return payload
turn="initial-host-"+uuid.uuid4().hex
common=["--goal-id",request["goal"],"--agent-id",request["agent"]]
guard=cli("quota","should-run","--runtime-profile","generic_cli",*common)
assert guard["should_run"] is True,guard
todo=guard.get("selected_todo")
role="work" if todo else "plan"
with Path(request["counts"]).open("a") as stream:
    stream.write(json.dumps({"role":role,"D":1,"P":1})+"\n")
if role=="plan":
    admitted=cli("quota","should-run","--runtime-profile","generic_cli",*common,"--turn-instance-id",turn)
    assert admitted["should_run"] and admitted.get("selected_todo") is None,admitted
    artifact=Path(request["plan"])
    artifact.write_text(json.dumps({"objective":guard["goal_id"],"probe":"Verify the retained two-number sum.",
                                    "inputs":[2,3],"expected":5})+"\n")
    added=cli("todo","add","--goal-id",request["goal"],"--role","agent","--claimed-by",request["agent"],
        "--text","Verify the retained two-number sum from the accepted initial plan.",
        "--task-class","advancement_task","--action-kind","validate","--operation-id","accepted-initial-plan")
    selected=cli("quota","should-run","--runtime-profile","generic_cli",*common,"--turn-instance-id",turn)
    assert selected["selected_todo"]["todo_id"]==added["todo_id"],selected
    binding=[*common,"--turn-instance-id",turn,"--todo-id",added["todo_id"]]
    refreshed=cli("refresh-state",*binding,"--classification","initial_planning_completed",
        "--delivery-batch-scale","implementation","--delivery-outcome","outcome_progress",
        "--no-global-sync","--suppress-external-sinks")
    spent=cli("quota","spend-slot",*binding,"--slots","1","--source","heartbeat","--execute")
    assert spent["appended"] is True,spent
    Path(request["result"]).write_text(json.dumps({"planning_turn":turn,"todo_id":added["todo_id"]}))
else:
    plan=json.loads(Path(request["plan"]).read_text())
    assert "accepted initial plan" in todo["text"],todo
    result={"sum":sum(plan["inputs"]),"expected":plan["expected"]}
    assert result["sum"]==result["expected"]
    artifact=Path(request["work"])
    artifact.write_text(json.dumps(result)+"\n")
    evidence="fixture:initial-plan-adoption:"+sha256(artifact.read_bytes()).hexdigest()
    vision={"schema_version":"goal_vision_replan_contract_v0","state":"no_followup",
      "vision_patch":{"vision_summary":"Verify the finite two-number sum.",
                      "acceptance_summary":"The plan was adopted and its explicit sum was verified."},
      "path_delta":{"schema_version":"goal_path_delta_v0","outcome":"no_change",
        "prior_assumption":"The first planned probe remained unexecuted.",
        "observed_reality":"The explicitly planned sum matched its declared expected value.",
        "stopped":["No additional work belongs to this finite probe."],"evidence_refs":[evidence]}}
    control=GoalModeMCPControlPlane(GoalModeMCPConfig(server_name="initial-plan",runtime_profile="generic_cli",
        legacy_host_surface="claude_code"),lambda:{"goal_id":request["goal"],"agent_id":request["agent"],"registry":request["registry"]})
    control.command_prefix=lambda:[sys.executable,"-m","loopx.cli","--runtime-root",request["runtime"]]
    completed=json.loads(control.complete_task(todo["todo_id"],request["agent"],evidence,no_follow_up=True,agent_vision=vision))
    Path(request["result"]).write_text(json.dumps({"completion":completed}))
    assert completed["ok"] is True,completed
'''


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_fresh_goal_without_todos_can_admit_its_first_planning(tmp_path, monkeypatch, provider):
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    # Remove the helper's prior history before the new Goal's first observation.
    # The accepted objective and empty canonical Todo source remain real.
    index.write_text("")
    packet = call("quota", "should-run", "--runtime-profile", "generic_cli",
                  "--goal-id", GOAL, "--agent-id", AGENT)
    assert packet.get("selected_todo") is None
    assert packet["should_run"] is True, json.dumps(packet)
    project, registry = tmp_path / "project", tmp_path / "registry.json"
    # The synthetic Host produces its plan and work in this Goal project.
    # Keep the real delivery-workspace guard independent of pytest's checkout.
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    host, request, result = project / "planning-host.py", project / "request.json", project / "result.json"
    counts, plan, work = project / "counts.jsonl", project / "plan.json", project / "work.json"
    host.write_text(_PLANNING_HOST)
    request.write_text(json.dumps({"registry": str(registry), "runtime": str(runtime), "goal": GOAL,
        "agent": AGENT, "counts": str(counts), "plan": str(plan), "work": str(work), "result": str(result)}))
    args = _worker_args(project, runtime, registry, host, request)
    args.goal_id, args.agent_id = GOAL, AGENT
    # Observation never adopts the otherwise runnable first planning request.
    wake_cmd, args.wake_cmd = args.wake_cmd, None
    assert worker.run_worker(args) == 0
    assert not counts.exists() and not plan.exists()
    args.wake_cmd = wake_cmd
    assert worker.run_worker(args) == 0, result.read_text() if result.exists() else "Host failed before planning"
    initial = json.loads(result.read_text())
    todo = call("todo", "list", "--goal-id", GOAL, "--todo-id", initial["todo_id"])
    assert todo["todo"]["status"] == "open"
    assert len(_spends(index)) == 1
    original = call("quota", "spend-slot", "--goal-id", GOAL, "--agent-id", AGENT,
        "--turn-instance-id", initial["planning_turn"], "--todo-id", initial["todo_id"],
        "--slots", "1", "--source", "heartbeat", "--execute")
    assert original["appended"] is False and original["idempotent_replay"] is True
    assert len(_spends(index)) == 1
    # A fresh worker now consumes the concrete planned task, not initialization again.
    assert worker.run_worker(args) == 0, result.read_text()
    assert json.loads(work.read_text()) == {"sum": 5, "expected": 5}
    assert worker.run_worker(args) == 0
    assert [json.loads(line)["role"] for line in counts.read_text().splitlines()] == ["plan", "work"]
    assert call("todo", "list", "--goal-id", GOAL, "--todo-id", initial["todo_id"])["todo"]["status"] == "done"
    assert len(_spends(index)) == 2


def test_unbound_chat_adapter_does_not_start_goal_research(monkeypatch):
    from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(server_name="ordinary-chat", runtime_profile="generic_cli", legacy_host_surface="claude_code"),
        lambda: {},
    )
    monkeypatch.setattr(control, "run_cli", lambda *_a, **_kw: pytest.fail("unbound chat must not execute Goal commands"))
    assert json.loads(control.should_run())["ok"] is False
    assert json.loads(control.complete_task("unbound-task", "unbound-agent", "no Goal authority"))["ok"] is False
