"""Evidence changes actual selected programs under real CLI/worker/acceptance.

The local Host performs finite symbolic search, not a live provider call. The
retained route, parameter neighbourhood and structural grammar have the same
scoring budget. The oracle independently executes generated source on withheld
inputs and never supplies those inputs to the search or choice actor.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shlex
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as fixture
from test_external_scheduler_worker import _args, worker


HOST = Path(__file__).with_name("research_comparison_fixture.py")
ROLES = ("retained", "parameter", "structural")
OBJECTIVE = "Deploy a program that explains the observed relation and passes independent withheld cases under the original bounded study."

# A separate evaluator: it compiles the generated module and reconstructs the
# recorded expressions with Python's AST, without importing the search actor.
ORACLE = r'''import ast,hashlib,importlib.util,json
from pathlib import Path

def expression(program):
    if type(program) is int:
        return ast.Constant(program)
    if program in ("x","y"):
        return ast.Name(id=program,ctx=ast.Load())
    assert isinstance(program,list) and len(program)==3
    operation={"+":ast.Add,"-":ast.Sub,"*":ast.Mult}[program[0]]
    return ast.BinOp(left=expression(program[1]),op=operation(),right=expression(program[2]))

def compiled(program):
    return compile(ast.fix_missing_locations(ast.Expression(body=expression(program))),"recorded-expression","eval")

training=json.loads(Path("training.json").read_text())
study=json.loads(Path("study.json").read_text())
deployed=json.loads(Path("deployed.json").read_text())
spec=importlib.util.spec_from_file_location("actual_deployment",Path("predictor.py"))
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert deployed["module_sha256"]==hashlib.sha256(Path("predictor.py").read_bytes()).hexdigest()
assert all(module.predict(row["x"],row["y"])==row["observed"] for row in study["withheld"])
assert deployed["training_predictions"]==[module.predict(row["x"],row["y"]) for row in training]
candidate=compiled(deployed["program"])
assert all(eval(candidate,{"__builtins__":{}},{"x":row["x"],"y":row["y"]})==module.predict(row["x"],row["y"])
    for row in study["withheld"])
measured=[]
for role in ("retained","parameter","structural"):
    path=Path("experiment-"+role+".json")
    result=json.loads(path.read_text())
    assert result["training_sha256"]==hashlib.sha256(Path("training.json").read_bytes()).hexdigest()
    assert result["score_calls"]==len(result["trace"])==study["score_budget"]
    assert result["sample_evaluations"]==study["score_budget"]*len(training)
    for row in result["trace"]:
        executable=compiled(row["program"])
        predicted=[eval(executable,{"__builtins__":{}},{"x":case["x"],"y":case["y"]}) for case in training]
        assert row["predicted"]==predicted
        assert row["squared_error"]==sum((value-case["observed"])**2 for value,case in zip(predicted,training))
    assert result["best"] in result["trace"]
    assert result["best"]["squared_error"]==min(row["squared_error"] for row in result["trace"])
    measured.append(result["best"]["squared_error"])
    if role==deployed["role"]:
        assert deployed["experiment_sha256"]==hashlib.sha256(path.read_bytes()).hexdigest()
        assert deployed["program"]==result["best"]["program"]
        selected_loss=result["best"]["squared_error"]
assert selected_loss==min(measured)
with Path("oracle-executions.jsonl").open("a") as stream:
    stream.write(json.dumps({"withheld_cases":len(study["withheld"]),"module_sha256":deployed["module_sha256"]})+"\n")
'''


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("relation", ["interaction", "additive"])
def test_measured_comparison_generates_and_adopts_a_program_with_original_acceptance(tmp_path, monkeypatch, provider, relation):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    monkeypatch.setenv("PYTHONPATH", str(fixture.REPO_ROOT))
    project, runtime, registry = fixture._write_fixture(tmp_path)
    monkeypatch.chdir(project)
    config = json.loads(registry.read_text())
    config["goals"][0]["quota"]["allowed_slots"] = 16
    registry.write_text(json.dumps(config))
    fixture._initialize_fixture_authority(project, runtime, provider=provider)

    def cli(*arguments, code=0):
        observed, packet = fixture._run_cli(registry, runtime, *arguments, cwd=project)
        assert observed == code, json.dumps(packet)
        return packet

    cli("configure-goal", "--goal-id", fixture.GOAL_ID, "--explore-mode", "planning",
        "--execution-replan-after-turns", "3", "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")
    # The owner defines three search families and a complete implementation
    # task, not a winning expression or a prewritten next-route Todo. Links are
    # part of that initial work declaration; later result capture adds evidence.
    cli("todo", "update", "--goal-id", fixture.GOAL_ID, "--todo-id", fixture.TODO_ID,
        "--agent-id", fixture.AGENT_ID, "--text", OBJECTIVE,
        *[value for role in ROLES for value in ("--explore-result-node-ref", "comparison-" + role)])
    # Both relations use the same search/data order for a provider. The second
    # provider independently reverses training order and changes grammar order;
    # neither relation nor withheld outcomes enter the actor's route choice.
    seed = 17 if provider == "file" else 18
    pairs = [(-2, -1), (-1, 2), (0, 3), (1, -2), (2, 1), (3, 0)]
    if seed % 2:
        pairs.reverse()
    operation = (lambda x, y: x * y) if relation == "interaction" else (lambda x, y: x + y)
    training = [{"x": x, "y": y, "observed": operation(x, y)} for x, y in pairs]
    withheld = [{"x": x, "y": y, "observed": operation(x, y)} for x, y in [(-7, 4), (5, -6), (11, 13), (0, -9)]]
    (project / "training.json").write_text(json.dumps(training))
    (project / "study.json").write_text(json.dumps({"score_budget": 125, "withheld": withheld}))
    (project / "verify.py").write_text(ORACLE)
    document = {"scope": {"kind": "all_advancement"}, "objective": OBJECTIVE,
        "non_goals": ["No claim about live model creativity or performance outside this finite arithmetic study."],
        "criteria": [{"id": "transfer", "description": "Actual deployed source passes withheld inputs and preserves the bounded comparison evidence.",
            "validation_argv": [sys.executable, "verify.py"], "validation_timeout_seconds": 10,
            "validation_files": [{"path": name, "sha256": sha256((project / name).read_bytes()).hexdigest()}
                for name in ("verify.py", "study.json", "training.json")]}],
        "bindings": [{"todo_id": fixture.TODO_ID, "criterion_ids": ["transfer"]}]}
    document_path = project / "requirements.json"
    document_path.write_text(json.dumps(document))
    initial = cli("goal-acceptance", "inspect", "--goal-id", fixture.GOAL_ID)
    configured = cli("goal-acceptance", "configure", "--goal-id", fixture.GOAL_ID, "--document", str(document_path),
        "--expected-provider-revision", initial["provider_revision"], "--operation-id", "owner-frozen-study", "--execute")["goal_acceptance_contract"]
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=tmp_path / "worker.json",
        quota_timeout_seconds=90, wake_timeout_seconds=180)
    args.registry, args.runtime_root = str(registry), str(runtime)
    args.goal_id, args.agent_id = fixture.GOAL_ID, fixture.AGENT_ID
    request = {"project": str(project), "registry": str(registry), "runtime": str(runtime),
        "goal_id": fixture.GOAL_ID, "agent_id": fixture.AGENT_ID, "todo_id": fixture.TODO_ID,
        "objective": OBJECTIVE, "training": str(project / "training.json"), "budget": 125, "seed": seed}

    def run_host(stage, role=""):
        key = stage + ("-" + role if role else "")
        response, path = project / (key + "-response.json"), project / (key + "-request.json")
        path.write_text(json.dumps({**request, "stage": stage, "role": role, "response": str(response)}))
        args.wake_cmd = shlex.join([sys.executable, str(HOST), str(path)])
        code = worker.run_worker(args)
        assert code == 0, response.read_text() if response.exists() else key
        assert response.exists(), "The authorized worker did not execute the controlled Host."
        return json.loads(response.read_text())

    results = {role: run_host("probe", role)["result"] for role in ROLES}
    assert {row["score_calls"] for row in results.values()} == {125}
    assert {row["sample_evaluations"] for row in results.values()} == {750}
    assert len({json.dumps(row["program"]) for row in results["structural"]["trace"]}) == 125
    assert not (project / "deployed.json").exists() and not (project / "oracle-executions.jsonl").exists()
    if relation == "interaction":
        assert results["retained"]["best"]["squared_error"] > 0
        assert results["parameter"]["best"]["squared_error"] > 0
    else:
        assert results["retained"]["best"]["squared_error"] == 0
    adopted = run_host("adopt")
    deployed = adopted["deployment"]
    expected_role = "structural" if relation == "interaction" else "retained"
    assert deployed["role"] == expected_role
    # Neither the Todo nor the Host request contains the expected winning
    # program; the actual evaluated source selected from current cards does.
    assert deployed["program"] == results[expected_role]["best"]["program"]
    assert deployed["selected_candidate"] == adopted["proposal"]["vision"]["stride_decision"]["explore_review"]["selected_candidate"]
    assert adopted["review"]["autonomous_replan_ack"]["semantic_delta"]["accepted"]
    final = cli("goal-acceptance", "inspect", "--goal-id", fixture.GOAL_ID)["goal_acceptance_contract"]
    assert final["status"] == "accepted" and final["revision"] == configured["revision"]
    assert final["digest"] == configured["digest"]
    # The existing adapter's completion/terminal readback runs two validation
    # checks; the explicit original Goal verification runs the third. Count
    # all of them instead of hiding validation cost behind one completion.
    assert len((project / "oracle-executions.jsonl").read_text().splitlines()) == 3
    current = cli("todo", "list", "--goal-id", fixture.GOAL_ID, "--todo-id", fixture.TODO_ID)["todo"]
    assert current["status"] == "done" and current["no_followup"] is True
    before = (runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl").read_bytes()
    rows = [json.loads(line) for line in before.splitlines()]
    spent = [row for row in rows if row.get("quota_spend_commit")]
    work = [row for row in rows if not row.get("quota_spend_commit")]
    expected_turns = {"comparison-review", *["comparison-probe-" + role for role in ROLES],
        adopted["completion"]["settlement_identity"]["turn_instance_id"]}
    assert len(rows) == 10 and len(work) == len(spent) == len(expected_turns) == 5
    assert {row["turn_instance_id"] for row in work} == expected_turns
    assert {row["turn_instance_id"] for row in spent} == expected_turns
    assert {row["todo_id"] for row in rows} == {fixture.TODO_ID}
    for _ in range(2):
        assert worker.run_worker(args) == 0
    after = (runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl").read_bytes()
    assert after == before, "A completed finite comparison must not dispatch or settle another research Turn."
    final_guard = cli("quota", "should-run", "--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)
    assert final_guard["should_run"] is False
