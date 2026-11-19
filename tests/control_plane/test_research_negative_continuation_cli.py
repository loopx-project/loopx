"""A useful negative experiment hands off to one actual long-running operation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import shlex
import sys
import time

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as fixture
from test_research_stage_continuity import _control, _vision
from test_external_scheduler_worker import _args, worker


_LONG_HOST = '''import json,sys,time,subprocess
from hashlib import sha256
from pathlib import Path
from loopx.goal_mode_mcp import GoalModeMCPConfig,GoalModeMCPControlPlane
request=json.loads(Path(sys.argv[1]).read_text())
control=GoalModeMCPControlPlane(GoalModeMCPConfig(server_name="negative-research-fixture",
    runtime_profile="generic_cli",legacy_host_surface="claude_code"),
    lambda:{"goal_id":request["goal_id"],"agent_id":request["agent_id"],"registry":request["registry"]})
control.command_prefix=lambda:[sys.executable,"-m","loopx.cli","--runtime-root",request["runtime"]]
guard=json.loads(control.should_run())
assert guard["should_run"] and guard["selected_todo"]["todo_id"]==request["todo_id"],guard
with Path(request["counts"]).open("a") as stream:
    stream.write(json.dumps({"operation":"O3","todo_id":request["todo_id"],"dispatches":1,"provider_requests":1})+"\\n")
Path(request["started"]).write_text(json.dumps({"operation":"O3","todo_id":request["todo_id"]}))
deadline=time.monotonic()+90
while not Path(request["release"]).exists():
    assert time.monotonic()<deadline,"test host was not released"
    time.sleep(0.05)
# O2's negative result is adopted as data: exclude the disproved linear model,
# then execute the already-authorized quadratic comparison.
negative=json.loads(Path(request["negative"]).read_text())
assert negative["hypothesis_supported"] is False
cases=negative["cases"]
result={"adopted_negative_sha256":sha256(Path(request["negative"]).read_bytes()).hexdigest(),
    "excluded_candidate":"twice-x", "candidate":"x-squared",
    "case_count":len(cases), "total_absolute_error":sum(abs(row["x"]**2-row["observed"]) for row in cases)}
artifact=Path(request["artifact"])
artifact.write_text(json.dumps(result,sort_keys=True))
evidence="quadratic-result-sha256:"+sha256(artifact.read_bytes()).hexdigest()
vision=request["vision"]
vision["path_delta"]["evidence_refs"]=[evidence]
completed=json.loads(control.complete_task(request["todo_id"],request["agent_id"],evidence,
    no_follow_up=True,agent_vision=vision))
assert completed["ok"],completed
verified=subprocess.run(control.command_prefix()+["--registry",request["registry"],"--format","json",
    "goal-acceptance","verify","--goal-id",request["goal_id"],"--execute"],capture_output=True,text=True,check=True)
assert json.loads(verified.stdout)["goal_acceptance_contract"]["status"]=="accepted"
Path(request["result"]).write_text(json.dumps({"completion":completed,"evidence":evidence,"vision":vision}))
'''


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_negative_experiment_adopts_evidence_and_runs_long_successor_once(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    monkeypatch.chdir(project)
    monkeypatch.setenv("PYTHONPATH", str(fixture.REPO_ROOT))
    fixture._initialize_fixture_authority(project, runtime, provider=provider)

    def cli(*args):
        code, packet = fixture._run_cli(registry, runtime, *args, cwd=project)
        assert code == 0, json.dumps(packet)
        return packet

    long_id = cli("todo", "add", "--goal-id", fixture.GOAL_ID, "--role", "agent",
        "--text", "Compare the authorized quadratic model after the linear hypothesis experiment",
        "--claimed-by", fixture.AGENT_ID, "--task-class", "advancement_task", "--action-kind", "validate",
        "--operation-id", "owner-authorized-quadratic-comparison")["todo_id"]
    negative = project / "negative.json"
    cases = [{"x": x, "predicted": 2*x, "observed": x*x} for x in range(1, 6)]
    negative.write_text(json.dumps({"hypothesis": "twice-x", "hypothesis_supported": all(row["predicted"] == row["observed"] for row in cases),
        "cases": cases}, sort_keys=True))
    document = project / "requirements.json"
    document.write_text(json.dumps({"scope": {"kind": "all_advancement"},
        "objective": "Test the linear hypothesis, retain its negative evidence, and compare the authorized quadratic model",
        "non_goals": ["A failed hypothesis need not improve a score to be useful"],
        "criteria": [
            {"id": "negative", "description": "The linear hypothesis was tested and refuted by actual cases",
             "validation_argv": [sys.executable, "-c", "import json; from pathlib import Path; r=json.loads(Path('negative.json').read_text()); assert r['hypothesis_supported'] is False and any(c['predicted']!=c['observed'] for c in r['cases'])"]},
            {"id": "quadratic", "description": "The second operation adopted O2's negative evidence and tested the next candidate",
             "validation_argv": [sys.executable, "-c", "import json,hashlib; from pathlib import Path; r=json.loads(Path('quadratic.json').read_text()); assert r['adopted_negative_sha256']==hashlib.sha256(Path('negative.json').read_bytes()).hexdigest() and r['case_count']==5 and r['total_absolute_error']==0"]},
        ],
        "bindings": [{"todo_id": fixture.TODO_ID, "criterion_ids": ["negative"]}, {"todo_id": long_id, "criterion_ids": ["quadratic"]}]}))
    before = cli("goal-acceptance", "inspect", "--goal-id", fixture.GOAL_ID)
    cli("goal-acceptance", "configure", "--goal-id", fixture.GOAL_ID, "--document", str(document),
        "--expected-provider-revision", before["provider_revision"], "--operation-id", "owner-linear-and-quadratic", "--execute")
    control = _control(registry, runtime)
    evidence = "negative-result-sha256:" + sha256(negative.read_bytes()).hexdigest()
    negative_vision = _vision(evidence)
    negative_vision["path_delta"]["observed_reality"] = "The linear hypothesis is refuted; its cases justify the already requested quadratic comparison."
    settled = json.loads(control.complete_task(fixture.TODO_ID, fixture.AGENT_ID, evidence,
        no_follow_up=True, agent_vision=negative_vision))
    assert settled["ok"] is True, settled
    selected = json.loads(control.should_run())
    assert selected["should_run"] is True and selected["selected_todo"]["todo_id"] == long_id
    assert not selected.get("autonomous_replan_obligation")

    paths = {key: project / f"{key}.json" for key in ("counts", "started", "release", "result")}
    artifact = project / "quadratic.json"
    host, request = project / "long_host.py", project / "long_request.json"
    host.write_text(_LONG_HOST)
    request.write_text(json.dumps({"goal_id": fixture.GOAL_ID, "agent_id": fixture.AGENT_ID,
        "todo_id": long_id, "registry": str(registry), "runtime": str(runtime), "negative": str(negative),
        "artifact": str(artifact), "vision": _vision("quadratic-result"), **{key: str(path) for key, path in paths.items()}}))
    args = _args(fake_cli=Path(sys.executable).parent / "loopx", state_file=tmp_path / "scheduler.json",
        wake_cmd=shlex.join([sys.executable, str(host), str(request)]), quota_timeout_seconds=90, wake_timeout_seconds=120)
    args.registry, args.runtime_root = str(registry), str(runtime)
    args.goal_id, args.agent_id = fixture.GOAL_ID, fixture.AGENT_ID
    with ThreadPoolExecutor(max_workers=1) as executor:
        operation = executor.submit(worker.run_worker, args)
        try:
            deadline = time.monotonic() + 60
            while not paths["started"].exists():
                assert not operation.done(), operation.result() if operation.done() else ""
                assert time.monotonic() < deadline, "the actual Host did not start"
                time.sleep(0.05)
            for _ in range(3):
                assert worker.run_worker(args) == 0  # The existing state-route lock retains the in-flight owner.
            in_flight = json.loads(control.should_run())
            assert in_flight["selected_todo"]["todo_id"] == long_id
            assert len(paths["counts"].read_text().splitlines()) == 1 and not artifact.exists()
        finally:
            paths["release"].touch()
        assert operation.result(timeout=120) == 0
    result = json.loads(artifact.read_text())
    assert result["excluded_candidate"] == "twice-x" and result["total_absolute_error"] == 0
    assert result["adopted_negative_sha256"] == sha256(negative.read_bytes()).hexdigest()
    adopted = json.loads(paths["result"].read_text())
    for _ in range(2):
        replay = json.loads(control.complete_task(long_id, fixture.AGENT_ID, adopted["evidence"],
            no_follow_up=True, agent_vision=adopted["vision"]))
        assert replay["ok"] is True, replay
        assert worker.run_worker(args) == 0
    final = json.loads(control.should_run())
    assert final["should_run"] is False and final["interaction_contract"]["mode"] == "terminal_no_followup"
    rows = [json.loads(line) for line in (runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" and row.get("todo_id") == long_id for row in rows) == 1
    assert len(paths["counts"].read_text().splitlines()) == 1  # O3 D=1, P=1, E=1, Q=1.
    assert json.loads(negative.read_text())["hypothesis_supported"] is False
