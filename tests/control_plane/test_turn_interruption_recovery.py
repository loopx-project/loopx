"""Synthetic export repair through the real CLI and providers, with SIGKILL/restart.

The Codex executable is an injected deterministic recorder, not a real model.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import hashlib
import platform
import subprocess
import sys
import time

import pytest

from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from tests.control_plane.turn_interruption_oracle import (
    INPUT_CSV,
    REQUIREMENT,
    assert_export,
    assert_host_stopped,
    spend_records,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

ROOT = Path(__file__).resolve().parents[2]
BOUNDARIES = (
    "intent", "tool", "result", "writeback", "spend", "receipt", "revoked",
    "retired_receipt",
)

# Independent downstream: append EVERY received operation, deliberately no dedup.
SERVICE = """import json,os,sqlite3,sys
from pathlib import Path
db,fifo,ready=sys.argv[1:]
with sqlite3.connect(db) as c: c.execute("CREATE TABLE operations(sequence INTEGER PRIMARY KEY, body TEXT)")
os.mkfifo(fifo)
stream=os.fdopen(os.open(fifo,os.O_RDWR),'r')
Path(ready).touch()
for line in stream:
 request=json.loads(line)
 with sqlite3.connect(db) as c: c.execute("INSERT INTO operations(body) VALUES(?)",(request['body'],))
 Path(request['ack']).touch()
"""

# Pauses the real CLI at an exact journal boundary. No production decision is replaced.
DRIVER = """import os,signal,sys
from pathlib import Path
from loopx.control_plane.turn_driver import executor
from loopx.cli import main
original=executor._write_journal
boundary=os.environ.get('FAULT_BOUNDARY','')
root=Path(os.environ['FAULT_ROOT'])
def checkpoint(path,journal,**kwargs):
 phases=journal.get('completed_phases',[])
 before=((boundary=='writeback' and phases==['host_execute','typed_result','validation','durable_writeback'])
   or (boundary=='spend' and phases==['host_execute','typed_result','validation','durable_writeback','quota_spend']))
 if not before: original(path,journal,**kwargs)
 match=(before or (boundary=='intent' and journal.get('host_attempt_count')==1 and not phases)
   or (boundary=='result' and phases==['host_execute','typed_result'])
   or (boundary=='receipt' and journal.get('status')=='committed'))
 if match:
  (root/'paused').write_text(boundary)
  os.kill(os.getpid(),signal.SIGSTOP)
 if before: original(path,journal,**kwargs)
executor._write_journal=checkpoint
raise SystemExit(main(sys.argv[1:]))
"""


def wait_for(path: Path, process: subprocess.Popen, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert process.poll() is None, (
            f"process exited before {path.name}: {process.returncode}"
        )
        assert time.monotonic() < deadline, f"no {path.name} within {seconds}s"
        time.sleep(0.02)


def fixture_module():
    path = ROOT / "examples/loopx-turn-codex-cli-e2e-smoke.py"
    spec = importlib.util.spec_from_file_location("interruption_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_export(tmp_path, monkeypatch, provider, fifo):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    f = fixture_module()
    project, runtime, workspace, registry = f._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / f.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        state.read_text().replace(
            "Name the completed and next", REQUIREMENT + " Name the completed and next"
        )
    )
    summary = parse_active_state_todos(
        state.read_text(), state_path=state, item_limit=None
    )
    initialize_canonical_authority(
        runtime,
        f.GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=f.GOAL_ID,
            handoff_mode="soft_claim",
            todos=summary["agent_todos"]["items"],
        ),
        state_path=state,
        provider=provider,
    )
    (workspace / "docs/input.csv").write_text(INPUT_CSV)
    (workspace / "docs/requirements.txt").write_text(REQUIREMENT)
    # The injected Host repairs this deliberate test defect before invoking it.
    (workspace / "docs/export.py").write_text(
        "import csv,io,json,time,uuid\nfrom pathlib import Path\n"
        "rows=list(csv.reader(Path('docs/input.csv').open()))\n"
        "out=io.StringIO(); writer=csv.writer(out,lineterminator='\\n')\n"
        "for row in sorted(rows): writer.writerow(row)\n"
        "body=out.getvalue(); Path('docs/export.csv').write_text(body)\n"
        f"fifo=Path({str(fifo)!r}); ack=fifo.parent / (uuid.uuid4().hex + '.ack')\n"
        "with fifo.open('w') as service: service.write(json.dumps({'body':body,'ack':str(ack)})+'\\n')\n"
        "deadline=time.monotonic()+5\nwhile not ack.exists():\n assert time.monotonic()<deadline\n time.sleep(0.01)\n"
    )
    executable = f._write_fake_codex(tmp_path)
    injected = """
import os,signal,subprocess
pathlib.Path('host.pid').write_text(str(os.getpid()))
pathlib.Path('received.txt').write_text(prompt)
pathlib.Path('requirements-read.txt').write_text(pathlib.Path('docs/requirements.txt').read_text())
p=pathlib.Path('docs/export.py'); p.write_text(p.read_text().replace('sorted(rows)', 'rows'))
subprocess.run([sys.executable,str(p)],check=True)
if os.environ.get('FAULT_BOUNDARY')=='tool':
 pathlib.Path(os.environ['FAULT_ROOT'],'paused').write_text('tool')
 os.kill(os.getpid(),signal.SIGSTOP)
"""
    executable.write_text(
        executable.read_text()
        .replace("#!/usr/bin/env python3", "#!" + sys.executable)
        .replace("prompt = sys.stdin.read()", "prompt = sys.stdin.read()\n" + injected)
    )
    base = f._base_argv(
        registry=registry,
        runtime=runtime,
        workspace=workspace,
        codex_bin=executable,
        model=None,
        timeout_seconds=60,
        expected_marker=f._marker_value(1),
        turn_instance_id="synthetic-export-original",
    )
    validator = (
        "import csv,io,json,sys;from pathlib import Path;json.load(sys.stdin);assert list(csv.reader(Path('docs/export.csv').open()))==list(csv.reader(io.StringIO("
        + repr(INPUT_CSV)
        + ")))"
    )
    base[base.index("--validation-command-json") + 1] = json.dumps(
        [sys.executable, "-c", validator]
    )
    return f, runtime, workspace, registry, base


@pytest.mark.skipif(
    os.name != "posix",
    reason="SIGSTOP/SIGKILL process-fault qualification is POSIX-specific",
)
@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_export_interruption_preserves_requirements_and_original_settlement(
    tmp_path, monkeypatch, provider, boundary
):
    service_file = tmp_path / "service.py"
    service_file.write_text(SERVICE)
    downstream = tmp_path / "downstream.sqlite"
    ready = tmp_path / "service-ready"
    fifo = tmp_path / "service.fifo"
    service = subprocess.Popen(
        [sys.executable, str(service_file), str(downstream), str(fifo), str(ready)]
    )
    running = None
    try:
        wait_for(ready, service)
        f, runtime, workspace, registry, base = setup_export(
            tmp_path, monkeypatch, provider, fifo
        )
        driver = tmp_path / "driver.py"
        driver.write_text(DRIVER)
        environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "FAULT_ROOT": str(tmp_path),
            "FAULT_BOUNDARY": {
                "revoked": "result", "retired_receipt": "receipt",
            }.get(boundary, boundary),
            "LOOPX_USAGE_PING": "0",
        }
        output = tmp_path / "first.stdout"
        errors = tmp_path / "first.stderr"
        with output.open("w") as out, errors.open("w") as err:
            running = subprocess.Popen(
                [sys.executable, str(driver), *base, "--execute"],
                cwd=ROOT,
                env=environment,
                stdout=out,
                stderr=err,
            )
            wait_for(tmp_path / "paused", running)
            journals = [
                p
                for p in (runtime / "goals" / f.GOAL_ID / "turns").glob("*.json")
                if not p.name.endswith(".lock.holder.json")
            ]
            assert len(journals) == 1
            before = json.loads(journals[0].read_text())
            (tmp_path / "before.json").write_text(json.dumps(before, indent=2))
            with sqlite3.connect(downstream) as database:
                (tmp_path / "downstream-before.json").write_text(
                    json.dumps(
                        database.execute(
                            "SELECT sequence,body FROM operations ORDER BY sequence"
                        ).fetchall()
                    )
                )
            capture_path = Path(
                before["plan"]["turn_envelope"]["detail_ref"]["captured_decision"][
                    "path"
                ]
            )
            capture = capture_path.read_bytes()
            assert REQUIREMENT in capture.decode()
            running.kill()
            running.wait(timeout=10)
            assert running.returncode == -signal.SIGKILL
        if boundary in {"revoked", "retired_receipt"}:
            current = json.loads(registry.read_text())
            current["goals"][0]["status"] = "retired"
            registry.write_text(json.dumps(current))
        # Let the surviving real supervisor observe EOF and drain the stopped Host.
        resume_base = list(base)
        instance_option = resume_base.index("--turn-instance-id")
        del resume_base[instance_option : instance_option + 2]
        resume = [
            *resume_base,
            "--resume-turn-key",
            before["turn_key"],
            "--retry-failed-turn",
            "--execute",
        ]
        clean_env = {k: v for k, v in environment.items() if k != "FAULT_BOUNDARY"}
        restarted = subprocess.run(
            [sys.executable, str(driver), *resume],
            cwd=ROOT,
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(restarted.stdout)
        if boundary in {"intent", "tool"}:
            assert restarted.returncode == 1, payload
            assert "recovery_decision" in payload, payload
            assert payload["recovery_decision"]["reason"] == "host_outcome_unknown", (
                payload
            )
            assert payload["recovery_decision"]["reinvoke_host"] is False
            assert len(spend_records(runtime, f.GOAL_ID)) == 0
            assert json.loads(journals[0].read_text()) == before
        elif boundary in {"revoked", "retired_receipt"}:
            assert restarted.returncode == 1, payload
            assert payload.get("ok") is False, payload
            assert "goal_not_registered" in json.dumps(payload), payload
            # Retirement has no terminal replay exception. Preserve the original
            # receipt/debit while refusing every new recovery invocation.
            assert len(spend_records(runtime, f.GOAL_ID)) == (
                1 if boundary == "retired_receipt" else 0
            )
            assert json.loads(journals[0].read_text()) == before
        else:
            assert restarted.returncode == 0 and payload["status"] == "committed", (
                payload
            )
            rows = spend_records(runtime, f.GOAL_ID)
            assert len(rows) == 1, rows
            repeated = subprocess.run(
                [sys.executable, str(driver), *resume],
                cwd=ROOT,
                env=clean_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert repeated.returncode == 0, repeated.stdout + repeated.stderr
            assert not any(json.loads(repeated.stdout)["effects"].values())
            assert len(spend_records(runtime, f.GOAL_ID)) == 1
        assert_host_stopped(workspace)
        assert capture_path.read_bytes() == capture
        assert_export(workspace, downstream, 0 if boundary == "intent" else 1)
        with sqlite3.connect(downstream) as database:
            downstream_rows = database.execute(
                "SELECT sequence,body FROM operations ORDER BY sequence"
            ).fetchall()
        evidence = os.environ.get(
            "LOOPX_RECOVERY_EVIDENCE_DIR", str(tmp_path / "evidence")
        )
        if evidence:
            target = Path(evidence)
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{provider}-{boundary}.json").write_text(
                json.dumps(
                    {
                        "synthetic": True,
                        "provider": provider,
                        "boundary": boundary,
                        "signal": "SIGKILL",
                        "before": before,
                        "recovery": payload,
                        "downstream_calls": len(downstream_rows),
                        "downstream_records": downstream_rows,
                        "artifact": (workspace / "docs/export.csv").read_text()
                        if downstream_rows
                        else None,
                        "capture_sha256": hashlib.sha256(capture).hexdigest(),
                        "python": platform.python_version(),
                        "sqlite": sqlite3.sqlite_version,
                        "stop_observation": "host_not_started"
                        if boundary == "intent"
                        else "host_stopped",
                        "settlement_records": spend_records(runtime, f.GOAL_ID),
                        "settlements": len(spend_records(runtime, f.GOAL_ID)),
                        "stderr": restarted.stderr,
                    },
                    indent=2,
                )
            )
    finally:
        if running is not None and running.poll() is None:
            running.kill()
            running.wait(timeout=10)
        service.terminate()
        service.wait(timeout=10)


@pytest.mark.skipif(
    os.name != "posix", reason="the independent service uses a POSIX FIFO"
)
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_normal_export_control_uses_the_same_requirements_and_result(
    tmp_path, monkeypatch, provider
):
    service_file = tmp_path / "service.py"
    service_file.write_text(SERVICE)
    downstream, fifo, ready = (
        tmp_path / name for name in ("downstream.sqlite", "service.fifo", "ready")
    )
    service = subprocess.Popen(
        [sys.executable, str(service_file), str(downstream), str(fifo), str(ready)]
    )
    try:
        wait_for(ready, service)
        f, runtime, workspace, _registry, base = setup_export(
            tmp_path, monkeypatch, provider, fifo
        )
        normal = subprocess.run(
            [str(Path(sys.executable).with_name("loopx")), *base, "--execute"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        payload = json.loads(normal.stdout)
        assert normal.returncode == 0 and payload["status"] == "committed", payload
        assert len(spend_records(runtime, f.GOAL_ID)) == 1
        assert_export(workspace, downstream, 1)
        assert_host_stopped(workspace)
        with sqlite3.connect(downstream) as database:
            operations = database.execute(
                "SELECT sequence,body FROM operations ORDER BY sequence"
            ).fetchall()
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        (evidence / f"{provider}-normal.json").write_text(
            json.dumps(
                {
                    "synthetic": True,
                    "provider": provider,
                    "boundary": "normal",
                    "signal": None,
                    "recovery": payload,
                    "downstream_calls": len(operations),
                    "downstream_records": operations,
                    "artifact": (workspace / "docs/export.csv").read_text(),
                    "settlements": len(spend_records(runtime, f.GOAL_ID)),
                },
                indent=2,
            )
        )
    finally:
        service.terminate()
        service.wait(timeout=10)
