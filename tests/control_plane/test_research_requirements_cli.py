"""Current owner requirements govern Goal closure through the real local CLI.

The validators describe these synthetic tasks. They are not a universal research
judge: LoopX preserves their owner, revision and actual execution evidence.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

import test_goal_acceptance_cli as acceptance_fixture
from test_goal_acceptance_cli import _configure
from test_external_scheduler_worker import _args, worker

acceptance_goal = acceptance_fixture.acceptance_goal


def _quota(run):
    code, packet = run("quota", "should-run", "--goal-id", "goal-acceptance",
                       "--agent-id", "agent-a")
    assert code == 0, packet
    return packet


def _assert_no_new_host(project):
    marker = project / "unexpected-host-start"
    args = _args(fake_cli=Path(sys.executable).parent / "loopx",
        state_file=project.parent / "scheduler.json", quota_timeout_seconds=90, wake_timeout_seconds=10,
        wake_cmd=shlex.join([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)]))
    args.registry, args.runtime_root = str(project.parent / "registry.json"), str(project.parent / "runtime")
    args.goal_id, args.agent_id = "goal-acceptance", "agent-a"
    for _ in range(2):
        assert worker.run_worker(args) == 0
    assert not marker.exists()  # D=0 and P=0 after the accepted stop/pause.


def test_todo_no_followup_cannot_replace_current_goal_verification(acceptance_goal):
    project, document, cli, run = acceptance_goal
    configured = _configure(cli, document)
    (project / "artifact.txt").write_text("accepted")
    code, completed = run("todo", "complete", "--goal-id", "goal-acceptance",
        "--todo-id", "todo_export", "--agent-id", "agent-a", "--no-follow-up",
        "--evidence", "artifact.txt verified by the owner-bound export criterion")
    assert code == 0, json.dumps(completed, ensure_ascii=True)
    assert completed["goal_acceptance_completion"]["results"] == [
        {"criterion_id": "export", "exit_code": 0, "passed": True}]
    assert cli("inspect")[1]["goal_acceptance_contract"]["status"] == "unverified"

    before = _quota(run)
    # A task's successful criterion does not create the independent Goal-level
    # verification receipt; no-follow-up describes this task's continuation.
    assert before["goal_frontier_projection"].get("terminal_state") is None
    assert before["interaction_contract"]["mode"] != "terminal_no_followup"
    assert before["should_run"] is False
    assert "goal-acceptance inspect" in before["reason"]

    code, verified = cli("verify", "--execute")
    assert code == 0 and verified["checks_passed"] is True, verified
    current = verified["goal_acceptance_contract"]
    assert current["revision"] == configured["goal_acceptance_contract"]["revision"]
    assert current["status"] == "accepted" and current["verification"]["todo_id"] is None
    after = _quota(run)
    assert after["should_run"] is False
    assert after["interaction_contract"]["mode"] == "terminal_no_followup"
    assert after["goal_frontier_projection"]["terminal_state"]["kind"] == "no_followup"
    _assert_no_new_host(project)


_RESEARCH_VALIDATOR = '''import json,sys
from pathlib import Path
report=json.loads(Path("research.json").read_text())
expected=[{"n":n,"value":n*n} for n in range(-3,4)]
assert report["tested"]==expected
if sys.argv[1]=="bounded":
    assert report["unproved_claims"]==["Nonnegativity outside the tested finite range"]
    assert report["stop_basis"]=="The owner-authorized finite range is exhausted"
else:
    # This task's exact algebraic oracle checks a certificate, not a label or
    # a large finite sample. Squaring the declared linear polynomial must equal
    # n**2 identically. No general research/proof heuristic enters LoopX Core.
    certificate=report["certificate"]
    a,b=certificate["linear_factor"]
    assert [b*b,2*a*b,a*a]==[0,0,1]
'''


@pytest.mark.parametrize("requirement", ["bounded", "proof"])
def test_bounded_research_stop_preserves_unproved_claims_and_cannot_certify_proof(acceptance_goal, requirement):
    project, document_path, cli, run = acceptance_goal
    code, updated = run("todo", "update", "--goal-id", "goal-acceptance", "--todo-id", "todo_export", "--agent-id", "agent-a",
        "--text", "Evaluate the declared integer range and retain what it does not establish")
    assert code == 0, json.dumps(updated)
    verifier = project / "verify_research.py"
    verifier.write_text(_RESEARCH_VALIDATOR)
    document = {
        "scope": {"kind": "all_advancement"},
        "objective": "Report a justified finite-range stopping point" if requirement == "bounded" else "Prove n squared is nonnegative for every integer",
        "non_goals": ["A global proof is outside this bounded study"] if requirement == "bounded" else [],
        "criteria": [{"id": "research", "description": "Preserve the explored range and the exact current requirement",
            "validation_argv": [sys.executable, "verify_research.py", requirement], "validation_timeout_seconds": 5,
            "validation_files": [{"path": "verify_research.py", "sha256": sha256(verifier.read_bytes()).hexdigest()}]}],
        "bindings": [{"todo_id": "todo_export", "criterion_ids": ["research"]}],
    }
    document_path.write_text(json.dumps(document))
    configured = _configure(cli, document_path)["goal_acceptance_contract"]
    # An actual controlled Host computation creates the evidence. Counts below
    # start after its result; repeated end observations must not run it again.
    host = project / "finite_research_host.py"
    host.write_text("import json\nfrom pathlib import Path\n"
        "report={'tested':[{'n':n,'value':n*n} for n in range(-3,4)],"
        "'unproved_claims':['Nonnegativity outside the tested finite range'],"
        "'stop_basis':'The owner-authorized finite range is exhausted'}\n"
        "Path('research.json').write_text(json.dumps(report,sort_keys=True))\n")
    subprocess.run([sys.executable, str(host)], cwd=project, check=True)
    artifact = project / "research.json"
    evidence = "research-sha256:" + sha256(artifact.read_bytes()).hexdigest()
    complete = ("todo", "complete", "--goal-id", "goal-acceptance", "--todo-id", "todo_export",
                "--agent-id", "agent-a", "--no-follow-up", "--evidence", evidence)
    code, completed = run(*complete)
    if requirement == "bounded":
        assert code == 0, completed
        assert completed["goal_acceptance_completion"]["results"][0]["passed"] is True
    else:
        assert code == 1 and completed["reason_code"] == "goal_acceptance_validation_rejected", completed
        # The failed proof cannot be relabelled as a successful completion.
        # Preserve the current operation as explicitly blocked with its actual
        # evidence gap instead of inventing a new review or a user permission.
        code, blocked = run("todo", "update", "--goal-id", "goal-acceptance", "--todo-id", "todo_export",
            "--agent-id", "agent-a", "--status", "blocked", "--reason", "Only finite observations exist; the required proof certificate is missing.",
            "--evidence", evidence)
        assert code == 0, blocked
    code, verified = cli("verify", "--execute")
    contract = verified["goal_acceptance_contract"]
    assert contract["revision"] == configured["revision"] and contract["digest"] == configured["digest"]
    after = _quota(run)
    if requirement == "bounded":
        assert code == 0 and contract["status"] == "accepted", verified
        assert after["interaction_contract"]["mode"] == "terminal_no_followup"
        for _ in range(2):
            code, replay = run(*complete)
            assert code == 0 and replay["changed"] is False, replay
    else:
        assert code == 1 and contract["status"] == "failed", verified
        assert after["goal_frontier_projection"].get("terminal_state") is None
        assert after["interaction_contract"]["mode"] != "terminal_no_followup"
    assert after["should_run"] is False, json.dumps({key: after.get(key) for key in ("should_run", "effective_action", "reason", "selected_todo", "autonomous_replan_obligation")})
    _assert_no_new_host(project)
    assert "certificate" not in json.loads(artifact.read_text())
    assert json.loads(artifact.read_text())["unproved_claims"] == ["Nonnegativity outside the tested finite range"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("failed_preflight", [False, True])
def test_empty_current_owner_contract_preserves_initial_planning(tmp_path, monkeypatch, provider, failed_preflight):
    from test_replan_successor_guard_reentry import AGENT, GOAL, ROOT, _fixture

    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    # This fixture represents a new Goal; its imported history belongs only to
    # the helper's other tests and is not part of the initial source state.
    index.write_text("")
    project, registry = tmp_path / "project", tmp_path / "registry.json"
    document = {"scope": {"kind": "all_advancement"},
        "objective": "Evaluate n squared for every integer in the declared finite range -3 to 3.",
        "non_goals": ["A statement about integers outside this range is not a proven result."],
        "criteria": [{"id": "finite-range", "description": "Retain all seven actual finite-range observations.",
            "validation_argv": [sys.executable, "-c", "import json; from pathlib import Path; "
                "d=json.loads(Path('research.json').read_text()); assert d['tested']==[{'n':n,'value':n*n} for n in range(-3,4)]"],
            "validation_timeout_seconds": 5}], "bindings": []}
    document_path = project / "initial-owner-requirements.json"
    document_path.write_text(json.dumps(document))
    basis = call("goal-acceptance", "inspect", "--goal-id", GOAL)["provider_revision"]
    configured = call("goal-acceptance", "configure", "--goal-id", GOAL,
        "--document", str(document_path), "--expected-provider-revision", basis,
        "--operation-id", "initial-authorized-requirements", "--execute")["goal_acceptance_contract"]
    assert [criterion["id"] for criterion in configured["criteria"]] == ["finite-range"]
    assert configured["tasks"] == [] and configured["verification"] is None
    if failed_preflight:
        prefix = [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                  "--runtime-root", str(runtime), "--format", "json"]
        result = subprocess.run([*prefix, "goal-acceptance", "verify", "--goal-id", GOAL, "--execute"],
            capture_output=True, text=True, cwd=ROOT)
        assert result.returncode == 1, result.stdout + result.stderr
        verified = json.loads(result.stdout)["goal_acceptance_contract"]
        assert verified["status"] == "failed" and verified["tasks"] == []
    guard = call("quota", "should-run", "--runtime-profile", "generic_cli", "--goal-id", GOAL, "--agent-id", AGENT)
    assert guard.get("selected_todo") is None
    assert guard["should_run"] is True, {key: guard.get(key) for key in ("should_run", "effective_action", "reason")}
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    request, host, artifact = (project / name for name in ("request.json", "initial-planning-host.py", "initial-plan.json"))
    counts, result = project / "counts.jsonl", project / "planned-todo.json"
    request.write_text(json.dumps({"document": str(document_path), "artifact": str(artifact),
        "counts": str(counts), "result": str(result), "goal": GOAL, "agent": AGENT,
        "prefix": [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                   "--runtime-root", str(runtime), "--format", "json"]}))
    host.write_text(r'''import json,subprocess,sys
from hashlib import sha256
from pathlib import Path
request=json.loads(Path(sys.argv[1]).read_text())
document=Path(request["document"]).read_bytes()
requirements=json.loads(document)
assert requirements["bindings"]==[]
plan={"requirement_sha256":sha256(document).hexdigest(),"objective":requirements["objective"],
      "criterion_ids":[criterion["id"] for criterion in requirements["criteria"]],"probe_inputs":list(range(-3,4))}
Path(request["counts"]).write_text(json.dumps({"D":1,"P":1})+"\n")
Path(request["artifact"]).write_text(json.dumps(plan,sort_keys=True))
added=subprocess.run(request["prefix"]+["todo","add","--goal-id",request["goal"],"--role","agent",
    "--claimed-by",request["agent"],"--text","Evaluate the seven integer inputs retained in initial-plan.json.",
    "--task-class","advancement_task","--action-kind","validate","--target-key","finite-range:-3:3",
    "--operation-id","adopt-original-initial-plan"],capture_output=True,text=True,check=True)
Path(request["result"]).write_text(added.stdout)
''')
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=tmp_path / "scheduler.json",
        quota_timeout_seconds=90, wake_timeout_seconds=60,
        wake_cmd=shlex.join([sys.executable, str(host), str(request)]))
    args.registry, args.runtime_root = str(registry), str(runtime)
    args.goal_id, args.agent_id = GOAL, AGENT
    assert worker.run_worker(args) == 0
    planned = json.loads(artifact.read_text())
    assert planned["requirement_sha256"] == sha256(document_path.read_bytes()).hexdigest()
    assert planned["probe_inputs"] == list(range(-3, 4)) and planned["criterion_ids"] == ["finite-range"]
    todo_id = json.loads(result.read_text())["todo_id"]
    todo = call("todo", "list", "--goal-id", GOAL, "--todo-id", todo_id)["todo"]
    assert todo["status"] == "open" and todo["target_key"] == "finite-range:-3:3"
    current = call("goal-acceptance", "inspect", "--goal-id", GOAL)["goal_acceptance_contract"]
    assert current["revision"] == configured["revision"] and current["digest"] == configured["digest"]
    assert current["held_todo_ids"] == [todo_id]
    # Planning creates real work without granting its still-missing association
    # or inventing a completion/spend. The original owner can now review it.
    assert [json.loads(line) for line in counts.read_text().splitlines()] == [{"D": 1, "P": 1}]
