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
from loopx.control_plane.turn_driver.host_process_transport import run_host_process
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
@pytest.mark.parametrize("fault", ["rejected", "hung", "lost_reply"])
def test_real_renewal_faults_keep_original_deadline_and_identity(canonical_execution, tmp_path, fault):
    context, command, selected = canonical_execution
    if fault == "rejected":
        context["renew_argv"] = [sys.executable, "-c", "import json;print(json.dumps({'ok':False}))"]
    elif fault == "hung":
        context["renew_argv"] = [sys.executable, "-c", "import time;time.sleep(60)"]
    else:
        marker = tmp_path / "lost-reply"
        relay = """import subprocess,sys
from pathlib import Path
marker=Path(sys.argv[1])
result=subprocess.run(sys.argv[2:],capture_output=True,text=True,check=True)
if marker.exists(): print(result.stdout,end='')
else: marker.touch()
"""
        context["renew_argv"] = [sys.executable, "-c", relay, str(marker), *context["renew_argv"]]
    observed = run_host_process([sys.executable, "-c", "import time;time.sleep(35);print('finished')"],
        project=tmp_path, input_text="", timeout_seconds=45, delegated_lease=context)
    current = command("task-lease", "inspect", *selected)
    assert current["lease"]["lease_epoch"] == context["lease"]["lease_epoch"]
    assert current["lease"]["idempotency_key"] == context["lease"]["idempotency_key"]
    if fault == "lost_reply":
        assert observed["outcome"] == "exited" and observed["output_complete"] is True
        assert current["lease"]["version"] > context["lease"]["version"]
    else:
        assert observed["outcome"] == "cancelled" and observed["output_complete"] is False
        assert current["lease"]["version"] == context["lease"]["version"]


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
