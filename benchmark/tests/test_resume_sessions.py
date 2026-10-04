"""Conversation continuity contracts, including the native exec transport."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from benchmark.runtime.worker import run_once
from benchmark.tests.test_shared_codex_runtime import worker_env
from loopx.control_plane.turn_driver.codex_sessions import load_codex_cli_session


def resume_env(tmp_path, monkeypatch):
    env = worker_env(tmp_path)
    skills = tmp_path / "skills"
    skills.mkdir()
    env.update(
        LOOPX_EXECUTION_MODE="heartbeat",
        LOOPX_ITERATION_CONTEXT="resume",
        LOOPX_RUNTIME_ROOT=str(tmp_path / "runtime"),
        LOOPX_REGISTRY=str(tmp_path / "registry.json"),
        LOOPX_GOAL_ID="fixture-goal",
        LOOPX_AGENT_ID="fixture-agent",
        LOOPX_SHARED_SKILLS=str(skills),
        LOOPX_CLI="loopx",
    )
    monkeypatch.setattr(
        "benchmark.runtime.worker.heartbeat_body",
        lambda *a, **kw: "Current Todo input.",
    )
    Path(env["CODEX_BIN"]).write_text(
        f"#!{__import__('sys').executable}\n"
        + """
import json, os, pathlib, sys, time, uuid
args = sys.argv[1:]
body = sys.stdin.read()
session = args[-2] if "resume" in args else str(uuid.uuid4())
if os.environ.get("FORK_SESSION"):
    session = str(uuid.uuid4())
home = pathlib.Path(os.environ["CODEX_HOME"]) / "sessions"
home.mkdir(exist_ok=True)
with (home / (session + ".jsonl")).open("a") as stream:
    stream.write(json.dumps({"body": body}) + "\\n")
print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
print(json.dumps({"argv": args}), flush=True)
if os.environ.get("HOLD_SESSION"):
    time.sleep(60)
if "--output-last-message" in args:
    pathlib.Path(args[args.index("--output-last-message") + 1]).write_text('{"marker":"planning-ack"}')
"""
    )
    return env


def plan_stage(env, monkeypatch):
    from benchmark.runtime import planning

    schema = {
        "type": "object",
        "properties": {"marker": {"type": "string"}},
        "required": ["marker"],
        "additionalProperties": False,
    }
    monkeypatch.setattr(
        planning,
        "task_plan_packet",
        lambda *a: {"result_schema": schema, "marker": "planning-input"},
    )
    monkeypatch.setattr(planning, "validate_plan_readback", lambda *a: {"ok": True})
    return env | {
        "LOOPX_TASK_ENTRY": "loopx-planned",
        "LOOPX_TASK_STAGE": "plan",
        "LOOPX_PLANNING_TIMEOUT_SEC": "30",
        "LOOPX_PLANNING_RESULT": str(Path(env["LOOPX_PROJECT"]) / "planning.json"),
    }


def binding(env):
    return load_codex_cli_session(
        Path(env["LOOPX_RUNTIME_ROOT"]),
        lineage={"goal_id": env["LOOPX_GOAL_ID"], "agent_id": env["LOOPX_AGENT_ID"]},
        session_scope="agent",
    )


@pytest.mark.parametrize("planning_mode", ["heartbeat", "turn"])
def test_planning_heartbeat_and_turn_share_one_binding(
    tmp_path, monkeypatch, planning_mode
):
    env = resume_env(tmp_path, monkeypatch)
    plan_env = plan_stage(env, monkeypatch) | {"LOOPX_EXECUTION_MODE": planning_mode}
    if planning_mode == "turn":
        plan_env["LOOPX_VALIDATION_COMMAND_JSON"] = '["true"]'
    first = run_once(plan_env)
    second = run_once(env)
    third = run_once(env)
    assert first["ok"] and second["ok"] and third["ok"]
    assert first["session"]["action"] == "start_new"
    assert second["session"]["action"] == third["session"]["action"] == "resume"
    assert len({r["session"]["session_id"] for r in (first, second, third)}) == 1
    assert binding(env)["session_id"] == first["session"]["session_id"]
    assert len(list((tmp_path / "logs/sessions").glob("*.jsonl"))) == 1


def test_timeout_keeps_the_observed_session_for_next_heartbeat(tmp_path, monkeypatch):
    env = resume_env(tmp_path, monkeypatch)
    first = run_once(env | {"HOLD_SESSION": "1", "LOOPX_CODEX_TURN_TIMEOUT_SEC": "1"})
    assert first["timed_out"] and not first["ok"]
    second = run_once(env)
    assert second["ok"] and second["session"]["action"] == "resume"
    assert second["session"]["session_id"] == first["session"]["session_id"]


@pytest.mark.parametrize("mutation", ["corrupt", "home", "fork"])
def test_resume_refuses_silent_replacement(tmp_path, monkeypatch, mutation):
    env = resume_env(tmp_path, monkeypatch)
    first = run_once(env)
    if mutation == "corrupt":
        next((tmp_path / "runtime").glob("goals/*/turn-sessions/*.json")).write_text(
            "invalid"
        )
    elif mutation == "home":
        env["LOOPX_CODEX_HOME"] = str(tmp_path / "other-home")
    else:
        env["FORK_SESSION"] = "1"
    with pytest.raises((ValueError, RuntimeError)):
        run_once(env)
    receipt = json.loads(
        sorted((tmp_path / "logs/wakes").glob("*/receipt.json"))[-1].read_text()
    )
    assert not receipt["ok"]
    if mutation == "fork":
        assert binding(env)["session_id"] == first["session"]["session_id"]


@pytest.mark.parametrize("field", ["LOOPX_GOAL_ID", "LOOPX_AGENT_ID"])
def test_another_identity_starts_an_independent_conversation(
    tmp_path, monkeypatch, field
):
    env = resume_env(tmp_path, monkeypatch)
    first = run_once(env)
    second = run_once(env | {field: "another-identity"})
    assert second["session"]["action"] == "start_new"
    assert second["session"]["session_id"] != first["session"]["session_id"]


def test_explicit_fresh_replaces_corrupt_shared_binding(tmp_path, monkeypatch):
    env = resume_env(tmp_path, monkeypatch)
    first = run_once(env)
    next((tmp_path / "runtime").glob("goals/*/turn-sessions/*.json")).write_text(
        "invalid"
    )
    second = run_once(env | {"LOOPX_ITERATION_CONTEXT": "fresh"})
    third = run_once(env)
    assert second["session"]["session_id"] != first["session"]["session_id"]
    assert third["session"]["session_id"] == second["session"]["session_id"]


@pytest.mark.skipif(
    not os.environ.get("LOOPX_TEST_CODEX_BIN"), reason="native Codex binary required"
)
def test_native_codex_resume_keeps_planning_history(tmp_path, monkeypatch):
    """Exercise installed Codex against a disposable local Responses endpoint."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(json.loads(raw))
            text = (
                '{"marker":"planning-ack"}' if len(requests) == 1 else "execution-ack"
            )
            message = {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
            response = {
                "id": f"resp_{len(requests)}",
                "object": "response",
                "model": "gpt-5.4",
                "status": "completed",
                "output": [message],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
            events = [
                {
                    "type": "response.created",
                    "response": response | {"status": "in_progress", "output": []},
                },
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": message,
                },
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": message["id"],
                    "delta": text,
                },
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": message,
                },
                {"type": "response.completed", "response": response},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for event in events:
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.flush()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = resume_env(tmp_path, monkeypatch) | {
            "CODEX_BIN": os.environ["LOOPX_TEST_CODEX_BIN"],
            "MODEL_NAME": "gpt-5.4",
            "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
            "LOOPX_CODEX_TURN_TIMEOUT_SEC": "30",
        }
        first = run_once(plan_stage(env, monkeypatch))
        second = run_once(env)
        assert first["ok"] and second["ok"]
        assert first["session"]["session_id"] == second["session"]["session_id"]
        assert len(requests) == 2
        history = json.dumps(requests[1]["input"])
        assert "planning-input" in history and "planning-ack" in history
        assert "Current Todo input." in history
        assert len(list((tmp_path / "logs/sessions").rglob("*.jsonl"))) == 1
    finally:
        server.shutdown()
        server.server_close()
