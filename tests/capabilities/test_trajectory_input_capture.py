from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit.trajectory_input_capture import (
    load_codex_input_observation,
)
from loopx.control_plane.turn_driver.codex_input_trace import record_codex_input_source
from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import (
    _captured_view, _usage_capture,
)


def native_trace(root: Path) -> tuple[Path, list[dict]]:
    bundle = root / "trace-fixture-session"
    (bundle / "payloads").mkdir(parents=True)
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": 1,
        "root_thread_id": "session", "rollout_id": "session"}))
    usage = {"input_tokens": 100, "cached_input_tokens": 60, "cache_write_input_tokens": 0,
             "output_tokens": 5, "reasoning_output_tokens": 0, "total_tokens": 105}
    request = {"model": "native-fixture", "input": [{"id": "packet-item", "type": "message",
        "role": "user", "content": [{"type": "input_text", "text": "hello world"}]}]}
    response = {"response_id": "response", "upstream_request_id": "request", "token_usage": usage}
    for name, data in (("1.json", request), ("2.json", response)):
        (bundle / "payloads" / name).write_text(json.dumps(data))
    payloads = [
        {"type": "inference_started", "inference_call_id": "inference", "thread_id": "session",
         "codex_turn_id": "turn", "model": "native-fixture", "request_payload": {"path": "payloads/1.json"}},
        {"type": "inference_completed", "inference_call_id": "inference", "response_id": "response",
         "upstream_request_id": "request", "response_payload": {"path": "payloads/2.json"}},
    ]
    events = [{"schema_version": 1, "seq": i + 1, "rollout_id": "session", "thread_id": "session",
               "codex_turn_id": "turn", "payload": p} for i, p in enumerate(payloads)]
    (bundle / "trace.jsonl").write_text("\n".join(map(json.dumps, events)))
    path = record_codex_input_source(root, session_id="session", goal_id="goal",
                                    text="hello world", prior_bundles=set())
    assert path is not None
    rows = [{"type": "session_meta", "payload": {"id": "session"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn"}},
        {"type": "token_usage_record", "payload": {"session_id": "session", "thread_id": "session",
         "turn_id": "turn", "root_turn_id": "turn", "response_id": "response", "usage": usage,
         "thread_token_usage": usage, "turn_token_usage": usage}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": usage, "last_token_usage": usage}}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn"}}]
    for i, row in enumerate(rows):
        row["timestamp"] = f"2026-01-01T00:00:0{i}Z"
    (root / "usage.jsonl").write_text("\n".join(map(json.dumps, rows)))
    return path, rows


def test_native_input_source_is_idempotent_private_and_never_adopts_old_or_other_sessions(tmp_path):
    path, _ = native_trace(tmp_path)
    assert record_codex_input_source(tmp_path, session_id="session", goal_id="goal",
        text="hello world", prior_bundles=set()) == path
    assert record_codex_input_source(tmp_path, session_id="other", goal_id="goal",
        text="hello world", prior_bundles=set()) is None
    assert record_codex_input_source(tmp_path, session_id="session", goal_id="goal",
        text="hello world", prior_bundles={"trace-fixture-session"}) is None
    assert path.stat().st_mode & 0o077 == 0
    moved = tmp_path / "original-sources"
    path.parent.rename(moved)
    outside = tmp_path.parent / "outside-sources"
    outside.mkdir()
    path.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        record_codex_input_source(tmp_path, session_id="session", goal_id="goal",
            text="hello world", prior_bundles=set())
    assert not list(outside.iterdir())


def test_real_codec_io_typed_estimate_and_source_change_readback(tmp_path):
    path, _ = native_trace(tmp_path)
    original = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    observed = load_codex_input_observation(tmp_path, str(path.relative_to(tmp_path)))
    assert observed["tokenizer"]["encoding"] == "o200k_base"
    assert observed["events"][0]["payload"]["request"]["input"][0]["content"][0]["tokens"] == 2
    assert "hello world" not in json.dumps(observed)
    usage = _usage_capture(tmp_path, "usage.jsonl", "session", input_goal_id="goal",
                           loopx_input_json=str(path.relative_to(tmp_path)))
    result = usage["projection"]["loopx_input"]
    assert result["status"] == "complete" and result["input_fraction"] == 0.02
    assert result["method"] == "tiktoken_o200k_base_input_estimate_v1"
    assert result["loopx_cached_input_tokens"] is None
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in original.items())
    record = {"source_root": str(tmp_path), "solver_usage": usage}
    assert _captured_view(record, "solver_usage")["applicability"] == "current"
    (tmp_path / "trace-fixture-session/payloads/1.json").write_text("{}")
    assert _captured_view(record, "solver_usage")["applicability"] == "historical"


def test_invalid_optional_observation_cannot_erase_native_counters_or_escape_root(tmp_path):
    path, _ = native_trace(tmp_path)
    value = json.loads(path.read_text())
    value["trace_bundle"] = "../elsewhere"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="bundle"):
        load_codex_input_observation(tmp_path, str(path.relative_to(tmp_path)))
    usage = _usage_capture(tmp_path, "usage.jsonl", "session", input_goal_id="goal",
                           loopx_input_json=str(path.relative_to(tmp_path)))
    assert usage["projection"]["input_tokens"] == 100
    assert usage["projection"]["loopx_input"]["status"] == "unavailable"


def test_real_cli_registers_recorded_component_without_model_execution(tmp_path):
    from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import inspect_pack

    source, _ = native_trace(tmp_path)
    evidence = "Synthetic fixed negative result."
    (tmp_path / "evidence.txt").write_text(evidence)
    data = {"schema_version": "trajectory_assessment_input_v1",
        "scope": {"goal_id": "goal", "case_id": "case", "run_id": "run", "revision": "fixed", "window": "fixed capture"},
        "objective": "Compare independently verified outcomes and cost",
        "selection": {"rule": "complete synthetic population", "population_episodes": 1, "exhaustive": True},
        "sources": [{"id": "source", "artifact": "evidence.txt", "artifact_sha256": hashlib.sha256(evidence.encode()).hexdigest(),
            "first_line": 1, "text": evidence}],
        "episodes": [{"id": "episode", "question": "Did the fixed test improve the original objective?",
            "source_ids": ["source"], "eligible_metrics": ["benefit_test"]}]}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(data))
    registry = tmp_path / "registry.json"
    registry.write_text('{"goals": []}')
    runtime = tmp_path / "runtime"
    result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "benchmark", "trajectory-pack-register",
        "--goal-id", "research", "--input-json", str(path), "--source-root", str(tmp_path),
        "--title", "Synthetic recorded input", "--usage-jsonl", "usage.jsonl", "--usage-session-id", "session",
        "--loopx-input-json", str(source.relative_to(tmp_path))], capture_output=True, text=True, check=True)
    registered = json.loads(result.stdout)
    view = inspect_pack(runtime_root=runtime, owner_goal_id="research", pack_id=registered["pack_id"])
    assert view["solver_usage"]["projection"]["loopx_input"]["input_fraction"] == 0.02
    assert view["job"] is None and view["result"] is None
    assert "hello world" not in json.dumps(view)
