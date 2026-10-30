"""Private OAR transport invariants, exercised through the existing Host owner."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.effect_runtime import _node_executable
from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
from loopx.control_plane.turn_driver.host_process_transport import run_host_process
from loopx.dsh_goal_mode import turn_host_adapter
from loopx.hosts.oar import bridge, installation


@pytest.fixture
def framed_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run actual Node/stdin/stdout/process cleanup without installing OAR."""
    monkeypatch.setattr(bridge, "node_executable", _node_executable)
    monkeypatch.setattr(bridge, "runtime_directory", lambda: tmp_path)

    def run(records: list[Any], *, exit_code: int = 0, timeout_seconds: float = 5) -> dict[str, Any]:
        (tmp_path / "records.json").write_text(json.dumps(records), encoding="utf-8")
        (tmp_path / "stdio.ts").write_text(
            "import {readFileSync} from 'node:fs';"
            "process.stdin.resume();process.stdin.on('end',()=>{"
            "const records=JSON.parse(readFileSync(new URL('./records.json',import.meta.url),'utf8'));"
            "for(const record of records) process.stdout.write(JSON.stringify(record)+'\\n');"
            f"process.exitCode={exit_code};"
            "});",
            encoding="utf-8",
        )
        return bridge.run_oar_turn(
            {"runtime": "dsh", "cwd": str(tmp_path), "prompt": "fixture", "resume": "native-session"},
            project=tmp_path, timeout_seconds=timeout_seconds,
        )

    return run


@pytest.fixture
def production_stdio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use the shipped writer and Python reader around a deterministic owner."""
    source = Path(bridge.__file__).with_name("stdio.ts").read_text(encoding="utf-8")
    (tmp_path / "stdio.ts").write_text(source, encoding="utf-8")
    (tmp_path / "runtime.ts").write_text(
        "import {readFileSync} from 'node:fs';"
        "export async function runOarTurn(config,prompt,{onRecord}){"
        "const records=JSON.parse(readFileSync(new URL('./records.json',import.meta.url),'utf8'));"
        "for(const record of records) onRecord(record);"
        "process.stderr.write(readFileSync(new URL('./stderr.txt',import.meta.url)));"
        "return records.findLast(record=>record.type==='result').result;}",
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "node_executable", _node_executable)
    monkeypatch.setattr(bridge, "runtime_directory", lambda: tmp_path)

    def run(records: list[dict[str, Any]], *, stderr: str = ""):
        (tmp_path / "records.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "stderr.txt").write_text(stderr, encoding="utf-8")
        received: list[dict[str, Any]] = []
        diagnostics: list[str] = []
        result = bridge.run_oar_turn(
            {"runtime": "dsh", "cwd": str(tmp_path), "prompt": "fixture", "resume": "native-session"},
            project=tmp_path, timeout_seconds=20,
            on_record=received.append, on_stderr=diagnostics.append,
        )
        return result, received, "".join(diagnostics)

    return run


def completed_records() -> list[dict[str, Any]]:
    return [
        {"type": "ready", "runtime": "dsh", "sessionId": "native-session"},
        {"type": "result", "result": {"sessionId": "native-session", "text": "answer",
            "outcome": {"kind": "completed"}, "usage": None, "contextUsage": None, "cleanupConfirmed": True}},
    ]


def test_oar_complete_result_requires_native_identity_and_clean_process(framed_runtime) -> None:
    observed = framed_runtime(completed_records())
    assert observed["outcome"] == "exited"
    assert observed["returncode"] == 0
    assert observed["output_complete"] is True
    assert observed["final_response"] == "answer"
    assert observed["session_id"] == "native-session"


def test_large_native_and_terminal_records_round_trip_identically(production_stdio) -> None:
    native = {"type": "native", "frame": {"kind": "frame", "body": {"type": "fixture", "native": "__PAYLOAD__"}}}
    offset = json.dumps(native, separators=(",", ":")).index("__PAYLOAD__")
    # The first emoji straddles the writer's 64 Ki UTF-16 boundary. Escaped
    # control characters also expand each wire fragment well beyond its data.
    payload = "a" * (65_535 - offset) + "😀" + '\0\n"\\汉字' * 100_000
    native["frame"]["body"]["native"] = payload
    records = completed_records()
    records[-1]["result"]["text"] = payload
    records[1:1] = [native, {"type": "event", "event": {"type": "text_delta", "delta": "between records"}}]
    observed, received, _ = production_stdio(records)
    assert observed["returncode"] == 0
    assert observed["output_complete"] is True
    assert observed["session_id"] == "native-session"
    assert observed["final_response"] == payload
    assert received == records, "local fragments never replace or truncate the original OAR records"


def test_explicit_owner_exit_flushes_queued_native_stderr(production_stdio) -> None:
    stderr = "d" * 1_048_577 + "\n"
    observed, _, received_stderr = production_stdio(completed_records(), stderr=stderr)
    assert observed["returncode"] == 0
    assert observed["output_complete"] is True
    assert received_stderr == stderr


def fragment_record(record: dict[str, Any], *, sequence: int = 0) -> list[dict[str, Any]]:
    serialized = json.dumps(record, separators=(",", ":"))
    width = (len(serialized) + 2) // 3
    chunks = [serialized[start:start + width] for start in range(0, len(serialized), width)]
    return [{"type": "fragment", "sequence": sequence, "index": index,
        "last": index == len(chunks) - 1, "data": chunk} for index, chunk in enumerate(chunks)]


@pytest.mark.parametrize("mutation", [
    "missing_last", "missing_middle", "out_of_order", "duplicate", "interleaved", "wrong_sequence",
    "reused_sequence", "bad_last", "bad_index", "bad_sequence", "empty_chunk", "extra_field",
    "oversized_chunk", "invalid_json", "duplicate_json_fields", "nested_fragment", "isolated_surrogate",
])
def test_malformed_or_incomplete_fragment_stream_fails_closed(framed_runtime, mutation: str) -> None:
    records = completed_records()
    fragments = fragment_record(records[-1])
    if mutation == "missing_last":
        fragments.pop()
    elif mutation == "missing_middle":
        fragments.pop(1)
    elif mutation == "out_of_order":
        fragments[0], fragments[1] = fragments[1], fragments[0]
    elif mutation == "duplicate":
        fragments.insert(1, fragments[0])
    elif mutation == "interleaved":
        fragments.insert(1, {"type": "event", "event": {"type": "text_delta", "delta": "interleaved"}})
    elif mutation == "wrong_sequence":
        fragments[1]["sequence"] = 1
    elif mutation == "reused_sequence":
        fragments[:0] = fragment_record({"type": "native", "frame": {"body": {"type": "fixture"}}})
    elif mutation == "bad_last":
        fragments[-1]["last"] = 1
    elif mutation == "bad_index":
        fragments[0]["index"] = False
    elif mutation == "bad_sequence":
        fragments[0]["sequence"] = False
    elif mutation == "empty_chunk":
        fragments[0]["data"] = ""
    elif mutation == "extra_field":
        fragments[0]["unexpected"] = True
    elif mutation == "oversized_chunk":
        fragments[0]["data"] = "x" * 65_537
    elif mutation == "invalid_json":
        fragments[-1]["data"] = "broken JSON"
    elif mutation == "duplicate_json_fields":
        fragments = [{"type": "fragment", "sequence": 0, "index": 0, "last": True,
            "data": '{"type":"native","type":"result"}'}]
    elif mutation == "nested_fragment":
        fragments = fragment_record(fragments[0])
    elif mutation == "isolated_surrogate":
        fragments[0]["data"] = "\ud800"
    observed = framed_runtime([records[0], *fragments])
    assert observed["returncode"] != 0
    assert observed["output_complete"] is False
    assert observed["final_response"] is None
    assert observed["session_id"] == "native-session"


def test_reassembled_record_cannot_exceed_32_mib(framed_runtime) -> None:
    records = completed_records()
    # Whitespace plus the valid terminal object would parse successfully if the
    # total bound were only enforced per fragment or per line.
    fragments = [{"type": "fragment", "sequence": 0, "index": index, "last": False,
        "data": " " * 65_536} for index in range(512)]
    fragments.append({"type": "fragment", "sequence": 0, "index": 512, "last": True,
        "data": json.dumps(records[-1])})
    observed = framed_runtime([records[0], *fragments], timeout_seconds=20)
    assert observed["returncode"] != 0
    assert observed["output_complete"] is False
    assert observed["final_response"] is None
    assert observed["session_id"] == "native-session"


def test_owner_refuses_oversized_record_without_truncating_it(production_stdio) -> None:
    records = completed_records()
    records[-1]["result"]["text"] = "x" * (32 * 1024 * 1024)
    observed, received, stderr = production_stdio(records)
    assert observed["returncode"] != 0
    assert observed["output_complete"] is False
    assert observed["final_response"] is None
    assert received == records[:1]
    assert "OAR runtime request failed" in stderr


@pytest.mark.parametrize("mutation", ["missing_ready", "missing_identity", "mismatch", "duplicate", "bad_frame", "no_result", "failure_exit", "cleanup_unconfirmed"])
def test_oar_incomplete_or_contradictory_transport_never_commits_an_answer(framed_runtime, mutation: str) -> None:
    records = completed_records()
    if mutation == "missing_ready":
        records.pop(0)
    elif mutation == "missing_identity":
        records[-1]["result"].pop("sessionId")
    elif mutation == "mismatch":
        records[-1]["result"]["sessionId"] = "different-session"
    elif mutation == "duplicate":
        records.append(records[-1])
    elif mutation == "bad_frame":
        records.insert(1, {"type": "not-an-oar-frame"})
    elif mutation == "no_result":
        records.pop()
    elif mutation == "cleanup_unconfirmed":
        records[-1]["result"].pop("cleanupConfirmed")
    observed = framed_runtime(records, exit_code=9 if mutation == "failure_exit" else 0)
    assert observed["returncode"] != 0
    assert observed["final_response"] is None
    if mutation != "missing_ready":
        assert observed["session_id"] == "native-session", "failed turns retain the native resume binding"


def test_oar_cancellation_retains_observed_session_and_waits_for_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "node_executable", _node_executable)
    monkeypatch.setattr(bridge, "runtime_directory", lambda: tmp_path)
    marker = tmp_path / "stopped"
    (tmp_path / "stdio.ts").write_text(
        "import {writeFileSync} from 'node:fs';"
        "process.on('SIGTERM',()=>{"
        f"writeFileSync({json.dumps(str(marker))},'stopped');"
        "process.stdout.write(JSON.stringify({type:'result',result:{sessionId:'native-session',"
        "text:'',outcome:{kind:'aborted'},usage:null,contextUsage:null,cleanupConfirmed:true}})+'\\n',()=>process.exit(0));"
        "});process.stdin.resume();process.stdin.on('end',()=>{"
        "process.stdout.write(JSON.stringify({type:'ready',runtime:'dsh',sessionId:'native-session'})+'\\n');"
        "setInterval(()=>{},1000);});",
        encoding="utf-8",
    )
    cancelled = threading.Event()
    observed = bridge.run_oar_turn(
        {"runtime": "dsh", "cwd": str(tmp_path), "prompt": "fixture", "resume": "native-session"},
        project=tmp_path, timeout_seconds=5, cancel_event=cancelled,
        on_record=lambda record: cancelled.set() if record["type"] == "ready" else None,
    )
    assert observed["outcome"] == "cancelled"
    assert observed["session_id"] == "native-session"
    assert observed["final_response"] is None
    assert marker.read_text() == "stopped"


def test_cancelled_before_launch_does_not_start_a_host(tmp_path: Path) -> None:
    cancelled = threading.Event()
    cancelled.set()
    marker = tmp_path / "unexpected-launch"
    observed = run_host_process(
        [sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).write_text('ran')"],
        project=tmp_path, input_text="", timeout_seconds=5, cancel_event=cancelled,
        track_descendants=True,
    )
    assert observed["outcome"] == "cancelled"
    assert observed["returncode"] is None
    assert not marker.exists()


def test_runtime_lookup_never_installs_dependencies_and_rejects_tampered_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for filename in ("package.json", "package-lock.json", "stdio.ts"):
        (source / filename).write_text("{}")
    monkeypatch.setattr(installation, "_SOURCE", source)
    monkeypatch.setenv("LOOPX_OAR_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(installation, "install", lambda: pytest.fail("runtime lookup must not install"))
    with pytest.raises(RuntimeError, match="dependency is unavailable"):
        installation.runtime_directory()
    cached = installation._cache_directory()
    manifest = cached / "node_modules/@botiverse/oar/package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": installation.OAR_VERSION}))
    for asset in installation._assets():
        (cached / asset.name).write_bytes(asset.read_bytes())
    assert installation.runtime_directory() == cached
    (cached / "stdio.ts").write_text("tampered source")
    with pytest.raises(RuntimeError, match="dependency is unavailable"):
        installation.runtime_directory()


def dsh_call(tmp_path: Path) -> dict[str, Any]:
    return {"prompt": "continue", "session_id": "native-session", "workspace": tmp_path,
        "session_root": tmp_path / "sessions", "provider": "fixture-provider", "model": "fixture-model",
        "reasoning_effort": "high", "max_tokens": 256, "cordis": None,
        "runtime_bin": None, "request_timeout_seconds": 30,
        "env": {"DSH_PERMISSION_MODE": "read-only"}}


@pytest.mark.parametrize("finish_reason", ["completed", "error", "max-tokens", "aborted", "unexpected"])
def test_dsh_native_terminal_facts_survive_without_accepting_aborted_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish_reason: str,
) -> None:
    captured: list[dict[str, Any]] = []
    native = {"final_response": '{"result_kind":"completed"}', "finish_reason": finish_reason,
        "events": [{"type": "turn/end", "data": {"reason": {"kind": finish_reason}}}]}

    def run(request, **kwargs):
        captured.append(request)
        kwargs["on_record"]({"type": "native", "frame": {"kind": "frame", "body": {"type": "dsh/result", "native": native}}})
        return {"outcome": "exited", "returncode": 0 if finish_reason == "completed" else 1,
            "output_complete": True, "oar_result": {},
            "turn_outcome": {"kind": "completed" if finish_reason == "completed" else "aborted" if finish_reason == "aborted" else "failed"}}

    monkeypatch.setattr(bridge, "run_oar_turn", run)
    if finish_reason in {"aborted", "unexpected"}:
        with pytest.raises((BuiltInHostError, turn_host_adapter.DshHostResultError)):
            turn_host_adapter.run_dsh_turn(**dsh_call(tmp_path))
    else:
        assert turn_host_adapter.run_dsh_turn(**dsh_call(tmp_path)) == native
    request = captured[0]
    assert request["resume"] == "native-session"
    assert request["dsh"]["python_executable"] == sys.executable
    assert Path(request["dsh"]["worker_path"]).name == "dsh_worker.py"
    assert request["env"]["DSH_PERMISSION_MODE"] == "read-only"
    assert request["effort"] == "high"
