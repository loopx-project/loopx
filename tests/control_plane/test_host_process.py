"""Real managed Host boundaries: no paid model, no external side effects."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx import collaboration_mcp as delegation_module
from loopx.control_plane.turn_driver.executor import _run_host
from loopx.control_plane.turn_driver.host_process_transport import (
    HOST_PROCESS_RECORD_ENV,
    HostOutputLines,
    run_host_process,
)
from tests.control_plane.host_process_fixture import COUNTER_PROCESS_SOURCE


def test_host_output_lines_bound_storage_and_use_lf() -> None:
    rows: list[str] = []
    lines = HostOutputLines(rows.append, max_chars=20)
    lines.feed("one\u2028two\n" + "x" * 100)
    assert lines.pending == ""
    lines.feed("tail\nlast")
    lines.finish()
    assert rows == ["one\u2028two", "last"]
    assert not lines.complete


def test_real_generic_host_roundtrip_and_stream_budget(tmp_path: Path) -> None:
    request = {"message": "one private local request"}
    result = _run_host(
        request,
        argv=[
            sys.executable,
            "-c",
            "import sys,json;print(json.dumps(json.load(sys.stdin)))",
        ],
        project=tmp_path,
        timeout_seconds=5,
    )
    assert result == {"ok": True, "value": request, "returncode": 0}
    overflow = _run_host(
        request,
        argv=[
            sys.executable,
            "-c",
            "import sys,time;sys.stdout.write('x'*1000000);sys.stdout.flush();time.sleep(30)",
        ],
        project=tmp_path,
        timeout_seconds=5,
    )
    assert overflow["ok"] is False
    assert overflow["reason"] == "host stdout exceeded the result budget"


@pytest.mark.parametrize("deadline", [None, 20])
def test_callback_failure_waits_for_owned_host_cleanup(tmp_path: Path, deadline) -> None:
    def reject(_text: str) -> None:
        raise ValueError("consumer stopped")

    started = time.monotonic()
    with pytest.raises(ValueError, match="consumer stopped"):
        run_host_process(
            [
                sys.executable,
                "-c",
                "import time;print('ready',flush=True);time.sleep(30)",
            ],
            project=tmp_path,
            input_text="",
            timeout_seconds=deadline,
            on_stdout=reject,
        )
    assert time.monotonic() - started < 8


@pytest.mark.skipif(os.name == "nt", reason="POSIX process interruption contract")
def test_counter_process_fixture_publishes_atomically(tmp_path: Path) -> None:
    marker = tmp_path / "counter"
    pid_path = tmp_path / "pid"
    pause_path = tmp_path / "before-publish"
    marker.write_text("published", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            COUNTER_PROCESS_SOURCE,
            str(marker),
            str(pid_path),
            "0.02",
            str(pause_path),
        ]
    )
    try:
        deadline = time.monotonic() + 5
        while not pause_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pause_path.exists(), "Counter process never reached the publication fence"
        process.kill()
        process.wait(timeout=5)
        assert marker.read_text(encoding="utf-8") == "published"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cancellation contract")
def test_disappearing_python_owner_cancels_real_host(tmp_path: Path) -> None:
    marker = tmp_path / "counter"
    pid_path = tmp_path / "pid"
    host_argv = [
        sys.executable,
        "-c",
        COUNTER_PROCESS_SOURCE,
        str(marker),
        str(pid_path),
        "0.02",
    ]
    launcher = f"""
from pathlib import Path
from loopx.control_plane.turn_driver.host_process_transport import run_host_process
run_host_process({host_argv!r}, project=Path({str(tmp_path)!r}), input_text='', timeout_seconds=30)
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", launcher],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "Host never started"
        owner.kill()
        owner.wait(timeout=5)
        time.sleep(1)
        before = marker.read_text()
        time.sleep(0.15)
        assert marker.read_text() == before, "Host survived loss of its owner"
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cancellation contract")
def test_generic_host_timeout_stops_real_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "child-work"
    pid_path = tmp_path / "child-pid"
    child_argv = [
        sys.executable,
        "-c",
        COUNTER_PROCESS_SOURCE,
        str(marker),
        str(pid_path),
        "0.02",
    ]
    host = f"import subprocess,time;subprocess.Popen({child_argv!r});time.sleep(30)"
    try:
        result = _run_host(
            {}, argv=[sys.executable, "-c", host], project=tmp_path, timeout_seconds=1
        )
        assert result["ok"] is False
        assert result["reason"] == "host process timeout"
        before = marker.read_text()
        time.sleep(0.15)
        assert marker.read_text() == before
    finally:
        if pid_path.exists():
            try:
                os.kill(int(pid_path.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_windows_transport_relay_preserves_argv_and_stdin(tmp_path: Path) -> None:
    from loopx.control_plane.turn_driver.host_process_transport import _WINDOWS_COMMAND_RELAY

    # The relay is tested here on any OS; real .cmd resolution remains a Windows
    # integration obligation. All args stay argv entries, not interpolated code.
    values = ["two words", "a&b", "%NAME%", 'one"quote', "界"]
    result = _run_host(
        {},
        argv=[
            sys.executable,
            "-c",
            _WINDOWS_COMMAND_RELAY,
            sys.executable,
            "-c",
            "import json,sys;print(json.dumps({'args':sys.argv[1:],'input':json.load(sys.stdin)}))",
            *values,
        ],
        project=tmp_path,
        timeout_seconds=5,
    )
    assert result["ok"] is True
    assert result["value"] == {"args": values, "input": {}}


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group drain readback")
def test_host_process_record_names_the_owned_group_and_is_not_inherited(tmp_path: Path, monkeypatch) -> None:
    from loopx.control_plane.turn_driver.host_process_transport import (
        HOST_PROCESS_RECORD_ENV, execution_host_drain, prepare_host_process_record,
    )

    record_path = tmp_path / "op.host.json"
    assert execution_host_drain(record_path) == "unattributable"
    prepare_host_process_record(record_path)
    assert execution_host_drain(record_path) == "not_launched"
    monkeypatch.setenv(HOST_PROCESS_RECORD_ENV, str(record_path))
    host = ("import json,os,sys;print(json.dumps({'env': os.environ.get(%r), 'pid': os.getpid(),"
            " 'pgid': os.getpgid(0)}))" % HOST_PROCESS_RECORD_ENV)
    result = _run_host({}, argv=[sys.executable, "-c", host], project=tmp_path, timeout_seconds=5)
    assert result["ok"] is True
    # A nested LoopX run inside the Host cannot overwrite its parent's record.
    assert result["value"]["env"] is None
    record = json.loads(record_path.read_text())
    assert record["phase"] == "finished"
    assert record["host_pid"] == result["value"]["pid"] == record["process_group"] == result["value"]["pgid"]
    assert execution_host_drain(record_path) == "drained"
    observed = record_path.read_bytes()
    prepare_host_process_record(record_path)
    assert record_path.read_bytes() == observed, "initialization must not overwrite execution evidence"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group drain readback")
def test_host_process_drain_reads_live_groups_and_refuses_unattributable_records(tmp_path: Path) -> None:
    from loopx.control_plane.turn_driver.host_process_transport import (
        HOST_PROCESS_RECORD_SCHEMA_VERSION, execution_host_drain,
    )
    from loopx.file_lock import lock_holder_host_label

    path = tmp_path / "op.host.json"
    live = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], start_new_session=True)
    gone = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    gone.wait(timeout=10)

    def drain(**fields):
        path.write_text(json.dumps({"schema_version": HOST_PROCESS_RECORD_SCHEMA_VERSION,
                                    "host": lock_holder_host_label(), "owner_pid": 1,
                                    "supervises": "host", "supervision": "direct", **fields}))
        return execution_host_drain(path)

    try:
        # A live supervisor or a live Host group is still draining.
        assert drain(phase="launching", bridge_pid=live.pid, process_group=None) == "draining"
        assert drain(phase="spawned", bridge_pid=gone.pid, process_group=live.pid) == "draining"
        assert drain(phase="finished", bridge_pid=gone.pid, process_group=live.pid) == "draining"
        assert drain(phase="spawned", bridge_pid=gone.pid, process_group=gone.pid) == "drained"
        # A supervisor gone before it reported a group may have spawned one anyway.
        assert drain(phase="launching", bridge_pid=gone.pid, process_group=None) == "unattributable"
        assert drain(phase="finished", bridge_pid=gone.pid, process_group=None) == "drained"
        for fields in ({"phase": "invalid", "bridge_pid": gone.pid, "process_group": None},
                       {"phase": "finished", "bridge_pid": gone.pid, "process_group": gone.pid,
                        "supervision": None},
                       {"phase": "spawned", "bridge_pid": None, "process_group": gone.pid},
                       {"phase": "spawned", "bridge_pid": gone.pid, "process_group": "1"},
                       {"phase": "spawned", "bridge_pid": gone.pid, "process_group": 1},
                       {"phase": "spawned", "bridge_pid": gone.pid, "process_group": gone.pid,
                        "host": "another-machine"},
                       {"phase": "spawned", "bridge_pid": gone.pid, "process_group": gone.pid,
                        "schema_version": "other"},
                       # A record must say which group it supervises.
                       {"phase": "spawned", "bridge_pid": gone.pid, "process_group": gone.pid,
                        "supervises": None},
                       {"phase": "spawned", "bridge_pid": gone.pid, "process_group": gone.pid,
                        "supervises": "nested_host"}):
            assert drain(**fields) == "unattributable", fields
        path.write_text("{not json")
        assert execution_host_drain(path) == "unattributable"
    finally:
        live.kill()
        live.wait(timeout=10)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group transport parity")
def test_explicit_environment_reaches_the_host_and_stays_out_of_the_record(tmp_path: Path) -> None:
    """A caller-supplied environment is used, and the record marker is not inherited.

    The bridge runs on the caller's mapping: it may pin the release the Host must
    run, and it names the record the supervisor owns. Replacing that mapping with
    the ambient one would silently change which code the Host runs and lose the
    record; passing it through unchanged would leak the record marker into a
    nested run that must not overwrite its parent's record.
    """
    from loopx.control_plane.turn_driver.host_process_transport import (
        HOST_PROCESS_RECORD_ENV, execution_host_drain, run_host_process,
    )

    record_path = tmp_path / "op.host.json"
    excluded = "LOOPX_TRANSPORT_PARITY_EXCLUDED"
    selected = "LOOPX_TRANSPORT_PARITY_SELECTED"
    host = ("import json,os,sys;print(json.dumps({'selected': os.environ.get(%r),"
            " 'excluded': os.environ.get(%r), 'record': os.environ.get(%r),"
            " 'pgid': os.getpgid(0), 'pid': os.getpid()}))" % (selected, excluded, HOST_PROCESS_RECORD_ENV))
    environment = {**os.environ, selected: "caller-selected", HOST_PROCESS_RECORD_ENV: str(record_path)}
    environment.pop(excluded, None)
    chunks: list[str] = []
    observation = run_host_process([sys.executable, "-c", host], project=tmp_path, input_text="",
                                   timeout_seconds=15, environment=environment,
                                   on_stdout=chunks.append)
    assert observation["outcome"] == "exited", observation
    value = json.loads("".join(chunks))
    assert value["selected"] == "caller-selected"
    assert value["excluded"] is None
    # The record is the supervisor's, and it is not a Host input.
    assert value["record"] is None
    record = json.loads(record_path.read_text())
    assert record["phase"] == "finished"
    assert record["host_pid"] == value["pid"] == record["process_group"] == value["pgid"]
    assert execution_host_drain(record_path) == "drained"
    # The caller's mapping is the caller's.
    assert environment[HOST_PROCESS_RECORD_ENV] == str(record_path)
    assert environment[selected] == "caller-selected"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group transport parity")
def test_hard_leased_cli_pins_the_release_and_the_record_through_the_transport(tmp_path, monkeypatch) -> None:
    """`_cli`'s pinned release and Host record only reach the CLI through the transport.

    The managed CLI runs under the bridge, so the environment `_cli` builds is
    only effective if the transport consumes it. Capture what `_cli` hands the
    transport instead of restating the native propagation the parity test covers.
    """
    from loopx.collaboration_mcp import Delegations

    runner = Delegations(tmp_path, tmp_path / "registry.json", "goal", "lead", tmp_path / "config.json")
    monkeypatch.setenv("PYTHONPATH", "/ambient")
    handed = {}

    def transport(*args, **kwargs):
        handed.update(kwargs)
        handed["argv"] = args[0]
        kwargs["on_stdout"]("{}")
        return {"outcome": "exited", "output_complete": True, "returncode": 0}

    monkeypatch.setattr("loopx.control_plane.turn_driver.host_process_transport.run_host_process", transport)
    record = tmp_path / "op.host.json"
    runner._cli({"agent_id": "analyst", "todo_id": "todo", "workspace": str(tmp_path)}, "todo", "claim",
                host_record=record, delegated_lease={"lease": {}, "ttl_seconds": None,
                                                     "renew_argv": [], "read_argv": []})
    assert handed["environment"]["PYTHONPATH"].split(os.pathsep)[0] == str(delegation_module._release_root())
    # One record names the execution; the transport places its leased supervisor's record.
    assert handed["environment"][HOST_PROCESS_RECORD_ENV] == str(record)
    index = handed["argv"].index("--host-process-record")
    assert handed["argv"][index + 1] == str(record)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group drain readback")
def test_execution_drain_needs_every_group_a_leased_run_launched(tmp_path: Path) -> None:
    """The Host transport alone reads one execution's records, whatever its topology.

    A leased run's supervisor records the private CLI beside the owner's record
    and the actual Host writes that record, so an outer exit proves nothing
    about the nested Host. A record that does not say what it supervises, or
    sits where the other kind belongs, attributes nothing.
    """
    from loopx.control_plane.turn_driver.host_process_transport import (
        HOST_PROCESS_RECORD_SCHEMA_VERSION, execution_host_drain, host_process_supervisor_record,
    )
    from loopx.file_lock import lock_holder_host_label

    record = tmp_path / "op.host.json"
    supervisor = host_process_supervisor_record(record)
    live = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], start_new_session=True)
    gone = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    gone.wait(timeout=10)

    def written(path, supervises, group, supervision):
        path.write_text(json.dumps({"schema_version": HOST_PROCESS_RECORD_SCHEMA_VERSION,
                                    "host": lock_holder_host_label(), "owner_pid": 1, "supervises": supervises,
                                    "supervision": supervision,
                                    "phase": "finished", "bridge_pid": gone.pid, "process_group": group}))

    cases = [
        # (supervisor record, owner's record, observation)
        (None, None, "unattributable"),
        (None, ("host", gone.pid, "direct"), "drained"),
        (None, ("host", live.pid, "direct"), "draining"),
        (("nested_host", gone.pid, "leased"), None, "unattributable"),
        (("nested_host", gone.pid, "leased"), ("host", gone.pid, "leased"), "drained"),
        # A later direct recovery Host still reads the earlier outer group.
        (("nested_host", gone.pid, "leased"), ("host", gone.pid, "direct"), "drained"),
        # Losing either leased record leaves the surviving peer insufficient.
        (None, ("host", gone.pid, "leased"), "unattributable"),
        (None, ("host", live.pid, "leased"), "draining"),
        # The leased CLI exited while its nested Host still runs.
        (("nested_host", gone.pid, "leased"), ("host", live.pid, "leased"), "draining"),
        (("nested_host", live.pid, "leased"), ("host", gone.pid, "leased"), "draining"),
        # A group still seen running outranks a missing proof.
        (("nested_host", live.pid, "leased"), "corrupt", "draining"),
        (("nested_host", gone.pid, "leased"), "corrupt", "unattributable"),
        # Records that do not say what they supervise, or say the wrong thing.
        (None, (None, gone.pid, "leased"), "unattributable"),
        (None, ("nested_host", gone.pid, "leased"), "unattributable"),
        (("host", gone.pid, "leased"), ("host", gone.pid, "leased"), "unattributable"),
        ((None, gone.pid, "leased"), None, "unattributable"),
    ]
    try:
        for outer, owned, expected in cases:
            for path, fact in ((supervisor, outer), (record, owned)):
                path.unlink(missing_ok=True)
                if fact == "corrupt":
                    path.write_text("{corrupt")
                elif fact is not None:
                    written(path, *fact)
            assert execution_host_drain(record) == expected, (outer, owned)
    finally:
        live.kill()
        live.wait(timeout=10)


def test_default_turn_deadline_is_optional_and_real_transport_accepts_none(tmp_path):
    from loopx.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["turn", "run-once", "--goal-id", "fixture",
                              "--agent-id", "agent", "--project", str(tmp_path)])
    assert args.timeout_seconds is None
    result = run_host_process(
        [sys.executable, "-c", "import time;time.sleep(.15);print('finished')"],
        project=tmp_path, input_text="", timeout_seconds=None,
    )
    assert result["outcome"] == "exited"
    assert result["returncode"] == 0
