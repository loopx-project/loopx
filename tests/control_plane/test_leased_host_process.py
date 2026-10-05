"""Real process and canonical provider qualification; no model or live Goal."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION, TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.turn_driver.host_process_transport import (
    HOST_PROCESS_RECORD_ENV, execution_host_drain, host_process_supervisor_record, run_host_process,
)
from tests.control_plane.host_process_fixture import COUNTER_PROCESS_SOURCE


@pytest.fixture(params=["file", "sqlite"])
def canonical_execution(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    state, registry, runtime = tmp_path / "state.md", tmp_path / "registry.json", tmp_path / "runtime"
    state.write_text("# Synthetic leased Host\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "host-goal", "repo": str(tmp_path), "state_file": "state.md",
        "coordination": {"registered_agents": ["worker"]},
    }]}))
    now = datetime.now(UTC).replace(microsecond=0)
    def iso(value):
        return value.isoformat().replace("+00:00", "Z")
    lease = {"schema_version": "task_lease_v0", "goal_id": "host-goal", "todo_id": "todo_host",
        "owner": "worker", "idempotency_key": "original-execution", "version": 1, "lease_epoch": 1,
        "status": "active", "write_scopes": [], "acquire_ttl_seconds": 30,
        "acquired_at": iso(now), "updated_at": iso(now), "expires_at": iso(now + timedelta(seconds=30))}
    projection = build_todo_runtime_shadow_projection(goal_id="host-goal", handoff_mode="hard_lease",
        leases=[lease], todos=[{"schema_version": "todo_item_v0", "todo_id": "todo_host", "role": "agent",
            "status": "open", "done": False, "archive_state": "active", "text": "Execute synthetic work",
            "claimed_by": "worker", "task_class": "advancement_task", "index": 1, "source_section": "Agent Todo"}])
    for todo in projection["todos"]:
        todo["schema_version"] = "todo_domain_record_v0"
        todo.pop("index")
        todo.pop("source_section")
    projection["todo_read_model"] = {"schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS), "todo_count": 1,
        "records_sha256": hashlib.sha256(canonical_bytes(projection["todos"])).hexdigest()}
    initialize_canonical_authority(runtime, "host-goal", projection, state_path=state, provider=request.param)
    cli = [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json"]
    selected = ["--goal-id", "host-goal", "--todo-id", "todo_host"]
    context = {"lease": lease, "ttl_seconds": 30,
        "renew_argv": [*cli, "task-lease", "renew", *selected, "--owner", "worker", "--idempotency-key", "original-execution"],
        "read_argv": [*cli, "task-lease", "inspect", *selected]}
    def command(*arguments):
        result = subprocess.run([*cli, *arguments], capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)
    yield context, command, selected
    subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result;effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
        capture_output=True, text=True, timeout=30, check=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX owned process-group qualification")
@pytest.mark.parametrize("fault", ["rejected", "hung", "lost_reply", "late_reply"])
def test_real_renewal_faults_keep_original_deadline_and_identity(canonical_execution, tmp_path, fault, record_property):
    context, command, selected = canonical_execution
    if fault == "rejected":
        context["renew_argv"] = [sys.executable, "-c", "import json;print(json.dumps({'ok':False}))"]
    elif fault == "hung":
        context["renew_argv"] = [sys.executable, "-c", "import time;time.sleep(60)"]
    else:
        marker = tmp_path / "lost-reply"
        trace = tmp_path / "lease-commands.jsonl"
        relay = """import json,subprocess,sys,time
from pathlib import Path
marker,trace,phase=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
hold_until=float(sys.argv[4])
def record(event,**values):
    with trace.open('a') as stream:
        stream.write(json.dumps({'event':event,'phase':phase,'at':time.time(),**values})+'\\n')
record('started',intent=sys.argv[5:])
result=subprocess.run(sys.argv[5:],capture_output=True,text=True)
try: reply=json.loads(result.stdout)
except ValueError: reply={}
record('returned',returncode=result.returncode,reply=reply)
if phase=='renew' and hold_until:
    time.sleep(max(0,hold_until-time.time())+2)
if phase=='read' or marker.exists(): print(result.stdout,end='')
else: marker.touch();record('reply_dropped')
sys.exit(result.returncode)
"""
        held_deadline = (datetime.fromisoformat(context["lease"]["expires_at"].replace("Z", "+00:00")).timestamp()
                         if fault == "late_reply" else 0)
        for field, phase in (("renew_argv", "renew"), ("read_argv", "read")):
            context[field] = [sys.executable, "-c", relay, str(marker), str(trace), phase,
                              str(held_deadline), *context[field]]
    observed = run_host_process([sys.executable, "-c", "import time;time.sleep(35);print('finished')"],
        project=tmp_path, input_text="", timeout_seconds=None, delegated_lease=context)
    evidence = {"original": context["lease"], "observed": observed,
                "trace": trace.read_text() if fault in {"lost_reply", "late_reply"} and trace.exists() else ""}
    # Keep the first observation in JUnit even if subsequent canonical readback
    # fails or pytest removes its temporary directory. Only synthetic fixtures.
    record_property("lease_supervision", json.dumps(evidence))
    current = command("task-lease", "inspect", *selected)
    evidence["current"] = current
    assert current["lease"]["lease_epoch"] == context["lease"]["lease_epoch"]
    assert current["lease"]["idempotency_key"] == context["lease"]["idempotency_key"]
    if fault == "lost_reply":
        assert observed["outcome"] == "exited" and observed["output_complete"] is True, evidence
        assert current["lease"]["version"] > context["lease"]["version"]
        assert "lease_failure" not in observed
    else:
        assert observed["outcome"] == "cancelled" and observed["output_complete"] is False
        if fault == "late_reply":
            # A committed renewal is not timely execution proof. The old
            # deadline still stops the Host while its reply is unavailable.
            assert current["lease"]["version"] > context["lease"]["version"], evidence
        else:
            assert current["lease"]["version"] == context["lease"]["version"], evidence
        assert observed["lease_failure"] == (
            {"reason": "renewal_rejected", "boundary": "renewal"} if fault == "rejected" else
            {"reason": "proved_deadline_elapsed", "boundary": "deadline"})


@pytest.mark.skipif(os.name == "nt", reason="POSIX owned process-group qualification")
def test_control_pipe_loss_waits_for_leased_forced_cleanup(canonical_execution, tmp_path):
    context, _, _ = canonical_execution
    marker, pid = tmp_path / "writes", tmp_path / "pid"
    source = COUNTER_PROCESS_SOURCE.replace("while True:", "print('ready', flush=True)\nwhile True:")

    def lost_reader(_text):
        raise ValueError("fixture output reader disconnected")

    with pytest.raises(ValueError, match="reader disconnected"):
        run_host_process([sys.executable, "-c", source, str(marker), str(pid), "0.02"],
            project=tmp_path, input_text="", timeout_seconds=30, delegated_lease=context,
            on_stdout=lost_reader)
    before = marker.read_bytes()
    time.sleep(0.2)
    assert marker.read_bytes() == before, "control pipe loss must finish forced cleanup before returning"


@pytest.mark.skipif(os.name == "nt", reason="POSIX owned process-group qualification")
def test_leased_supervisor_records_its_own_group_beside_the_owners_record(canonical_execution, tmp_path, monkeypatch):
    """The owner names one record; a leased supervisor leaves it to the nested Host."""
    context, _, _ = canonical_execution
    record = tmp_path / "op.host.json"
    monkeypatch.setenv(HOST_PROCESS_RECORD_ENV, str(record))
    chunks = []
    observed = run_host_process([sys.executable, "-c", "import os,sys;print(os.environ.get(sys.argv[1]))",
                                 HOST_PROCESS_RECORD_ENV], project=tmp_path, input_text="",
                                timeout_seconds=30, delegated_lease=context, on_stdout=chunks.append)
    assert observed["outcome"] == "exited", observed
    assert "".join(chunks).strip() == "None"  # the leased child never inherits the marker
    assert json.loads(record.read_text())["phase"] == "not_launched"
    supervisor = json.loads(host_process_supervisor_record(record).read_text())
    assert (supervisor["supervises"], supervisor["phase"]) == ("nested_host", "finished")
    assert execution_host_drain(record) == "drained"
    # Losing the positive never-launched proof must not retain that conclusion.
    record.unlink()
    assert execution_host_drain(record) == "unattributable"


@pytest.mark.skipif(os.name == "nt", reason="POSIX owned process-group qualification")
def test_released_initial_proof_explains_refusal_before_host_launch(canonical_execution, tmp_path):
    context, command, selected = canonical_execution
    command("task-lease", "release", *selected, "--owner", "worker",
            "--idempotency-key", "original-execution", "--expected-version", "1")
    marker = tmp_path / "must-not-launch"
    observed = run_host_process([sys.executable, "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)],
        project=tmp_path, input_text="", timeout_seconds=10, delegated_lease=context)
    assert observed["outcome"] == "cancelled" and observed["output_complete"] is False
    assert not marker.exists()
    assert observed["lease_failure"] == {"reason": "lease_inactive", "boundary": "initial_proof"}


@pytest.mark.skipif(os.name == "nt", reason="POSIX owned process-group qualification")
def test_returned_host_uses_renewed_deadline_during_final_proof(canonical_execution, tmp_path, record_property):
    """An in-flight renewal can finish after Host exit, before final readback."""
    context, _, _ = canonical_execution
    relay = """import json,os,subprocess,sys,time
from pathlib import Path
root,phase,old_expiry=Path(sys.argv[1]),sys.argv[2],float(sys.argv[3])
if phase=='read':
    count=root/'read-count'
    number=int(count.read_text())+1 if count.exists() else 1
    count.write_text(str(number))
    if number==3:
        (root/'final-proof-started').write_text(str(time.time()))
        time.sleep(max(0,old_expiry+1-time.time()))
result=subprocess.run(sys.argv[4:],capture_output=True,text=True,check=True)
if phase=='renew':
    (root/'renewed').write_text(result.stdout)
    pid=int((root/'host-pid').read_text())
    while True:
        try: os.kill(pid,0)
        except ProcessLookupError: break
        time.sleep(0.01)
    # The parent has reaped the actual Host; let its pending exit callbacks
    # finish before delivering the renewal response and fresh proof.
    time.sleep(0.2)
print(result.stdout,end='')
"""
    expiry = datetime.fromisoformat(context["lease"]["expires_at"].replace("Z", "+00:00")).timestamp()
    for field, phase in (("renew_argv", "renew"), ("read_argv", "read")):
        context[field] = [sys.executable, "-c", relay, str(tmp_path), phase, str(expiry), *context[field]]
    host = """import os,sys,time
from pathlib import Path
root=Path(sys.argv[1]);(root/'host-pid').write_text(str(os.getpid()))
while not (root/'renewed').exists(): time.sleep(0.01)
print('finished')
"""
    observed = run_host_process([sys.executable, "-c", host, str(tmp_path)], project=tmp_path,
                               input_text="", timeout_seconds=45, delegated_lease=context)
    renewal = json.loads((tmp_path / "renewed").read_text())
    evidence = {"observed": observed, "renewal": renewal, "original": context["lease"]}
    record_property("final_proof", json.dumps(evidence))
    assert float((tmp_path / "final-proof-started").read_text()) < expiry, evidence
    assert renewal["lease"]["version"] > context["lease"]["version"], evidence
    assert time.time() > expiry
    assert observed["outcome"] == "exited" and observed["output_complete"] is True, evidence
    assert "lease_failure" not in observed
