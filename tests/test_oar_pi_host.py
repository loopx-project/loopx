"""Signed Pi host boundary and the actual bundled OAR/Pi provider path."""
from __future__ import annotations

from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
from loopx.control_plane.turn_driver.host_failure import BuiltInHostError
from loopx.hosts.oar import __main__ as cli
from loopx.hosts.oar import installation, turn


def signed_request() -> dict:
    envelope = {
        "schema_version": "loopx_turn_envelope_v0",
        "goal_id": "fixture-goal",
        "agent_id": "fixture-agent",
        "action": {"primary_action": "Inspect the fixture without changing any files.", "must_attempt": True},
        "required_reads": [],
        "boundary": {"write_scope": [], "workspace_guard": {"status": "ready", "action": "continue"}},
        "action_signature": {"schema_version": "loopx_action_signature_v0", "matches": True},
    }
    digest = "sha256:" + sha256(json.dumps(
        turn_envelope_action_signature_document(envelope), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    envelope["action_signature"].update(source_hash=digest, envelope_hash=digest)
    return {
        "schema_version": "loopx_turn_host_request_v0",
        "turn_key": "sha256:" + "1" * 64,
        "route": "primary_delivery",
        "session": {"goal_id": "fixture-goal", "agent_id": "fixture-agent"},
        "turn_envelope": envelope,
        "result_contract": {"schema_version": "loopx_turn_result_v0", "completed_phases": ["host_execute", "typed_result"]},
    }


def candidate() -> dict:
    return {
        "result_kind": "wait", "classification": "fixture_observed",
        "next_action": "Wait for the owner's next instruction.",
        "vision_unchanged_reason": "The fixture requires no change.",
    }


def successful_observation(text: str) -> dict:
    return {
        "outcome": "exited", "returncode": 0, "output_complete": True,
        "turn_outcome": {"kind": "completed"}, "final_response": text,
    }


def test_signed_host_preserves_authority_and_uses_native_pi_options(tmp_path, monkeypatch):
    calls = []

    def run(request, **options):
        calls.append((request, options))
        return successful_observation(json.dumps(candidate()))

    monkeypatch.setattr(turn, "run_oar_turn", run)
    request = signed_request()
    result = turn.run_oar_host(request, config=turn.OarTurnHostConfig(
        cwd=tmp_path, model="fixture/text", effort="off", timeout_seconds=12,
    ))
    assert result["schema_version"] == "loopx_turn_result_v0"
    assert result["turn_key"] == request["turn_key"]
    assert result["result_kind"] == "wait"
    native, options = calls[0]
    assert native["runtime"] == "pi"
    assert native["model"] == "fixture/text"
    assert native["effort"] == "off"
    assert "Inspect the fixture without changing any files." in native["prompt"]
    assert '"write_scope":[]' in native["prompt"]
    assert native["env"]["LOOPX_TURN_GOAL_ID"] == "fixture-goal"
    assert options["project"] == tmp_path
    assert options["timeout_seconds"] == 12
    assert "outputSchema" not in native, "Pi refuses schema enforcement; the shared host parser owns its candidate"
    assert "command" not in native
    assert "sandbox" not in native, "this explicit native-tool host makes no OS sandbox claim"


def test_tampered_or_unsupported_requests_never_launch_pi(tmp_path, monkeypatch):
    monkeypatch.setattr(turn, "run_oar_turn", lambda *_args, **_kwargs: pytest.fail("must not launch"))
    request = signed_request()
    request["turn_envelope"]["action"]["primary_action"] = "tampered action"
    with pytest.raises(BuiltInHostError, match="authority_rejected"):
        turn.run_oar_host(request, config=turn.OarTurnHostConfig(cwd=tmp_path))
    with pytest.raises(BuiltInHostError, match="schema_mismatch"):
        turn.run_oar_host([], config=turn.OarTurnHostConfig(cwd=tmp_path))
    with pytest.raises(BuiltInHostError, match="runtime_rejected"):
        turn.run_oar_host(signed_request(), config=turn.OarTurnHostConfig(cwd=tmp_path, runtime="codex"))


@pytest.mark.parametrize("outcome,complete,kind", [
    ({"kind": "failed", "failure": "auth", "reason": "credential-private"}, True, "auth_failed"),
    ({"kind": "aborted"}, True, "unknown"),
    ({"kind": "completed"}, False, "transport_lost"),
])
def test_native_failure_or_incomplete_output_cannot_become_material_success(tmp_path, monkeypatch, outcome, complete, kind):
    observed = {
        **successful_observation(json.dumps({"result_kind": "validated_progress", "classification": "untrusted"})),
        "turn_outcome": outcome, "output_complete": complete,
    }
    monkeypatch.setattr(turn, "run_oar_turn", lambda *_args, **_kwargs: observed)
    with pytest.raises(BuiltInHostError) as failure:
        turn.run_oar_host(signed_request(), config=turn.OarTurnHostConfig(cwd=tmp_path))
    assert failure.value.failure_kind == kind
    assert "credential-private" not in str(failure.value)


def test_untyped_final_answer_uses_shared_nonmaterial_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(turn, "run_oar_turn", lambda *_args, **_kwargs: successful_observation("plain text"))
    result = turn.run_oar_host(signed_request(), config=turn.OarTurnHostConfig(cwd=tmp_path))
    assert result["result_kind"] == "wait"
    assert result["classification"] == "no_typed_host_result"


def test_cli_missing_dependency_does_not_echo_native_credentials(tmp_path, monkeypatch, capsys):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Missing module, provider supplied credential-private")

    monkeypatch.setattr(turn, "run_oar_turn", unavailable)
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(signed_request())))
    assert cli.main(["turn", "--runtime", "pi", "--cwd", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "credential-private" not in captured.err
    assert "loopx.hosts.oar status" in captured.err


def test_cli_status_and_install_dispatch_are_explicit(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(installation, "inspect_installation", lambda: {"ready": False, "reason": "not installed"})
    monkeypatch.setattr(installation, "install", lambda: calls.append("install") or {"ready": True})
    assert cli.main(["status"]) == 1
    assert calls == []
    assert json.loads(capsys.readouterr().out)["ready"] is False
    assert cli.main(["install"]) == 0
    assert calls == ["install"]
    assert json.loads(capsys.readouterr().out)["ready"] is True


@pytest.mark.parametrize("response_status", [200, 401])
def test_real_pi_sdk_and_packaged_host_cli_against_local_provider(tmp_path, response_status):
    """Exercise Python -> Node -> OAR -> native Pi RPC -> HTTP and back."""
    try:
        installation.node_executable()
        installation.runtime_directory()
    except RuntimeError as exc:
        pytest.skip(f"Optional pinned OAR runtime is required for this integration: {exc}")
    seen = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(payload)
            self.send_response(response_status)
            if response_status != 200:
                body = json.dumps({"error": {"message": "synthetic-key-refused", "type": "invalid_api_key"}}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for choices, usage in [
                ([{"index": 0, "delta": {"role": "assistant", "content": json.dumps(candidate())}, "finish_reason": None}], None),
                ([{"index": 0, "delta": {}, "finish_reason": "stop"}], {"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20}),
            ]:
                chunk = {"id": "fixture-response", "object": "chat.completion.chunk", "created": 1,
                         "model": "fixture-text", "choices": choices}
                if usage is not None:
                    chunk["usage"] = usage
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = Thread(target=server.serve_forever, daemon=True)
    serving.start()
    agent_dir = tmp_path / "pi-agent"
    agent_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (agent_dir / "models.json").write_text(json.dumps({"providers": {"loopx-fixture": {
        "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "api": "openai-completions",
        "apiKey": "synthetic-key-refused", "models": [{
            "id": "fixture-text", "name": "Fixture Text", "reasoning": False, "input": ["text"],
            "contextWindow": 128_000, "maxTokens": 2048,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        }],
    }}}), encoding="utf-8")
    (agent_dir / "settings.json").write_text(json.dumps({"retry": {"enabled": False}}), encoding="utf-8")
    import os

    environment = {**os.environ, "PI_CODING_AGENT_DIR": str(agent_dir), "OAR_PI_AGENT_DIR": str(agent_dir)}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "loopx.hosts.oar", "turn", "--runtime", "pi",
             "--model", "loopx-fixture/fixture-text", "--effort", "off", "--cwd", str(workspace), "--timeout-seconds", "30"],
            input=json.dumps(signed_request()), text=True, encoding="utf-8", capture_output=True,
            env=environment, cwd=Path(__file__).resolve().parents[1], timeout=40, check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
    assert seen, completed.stderr
    assert seen[0]["model"] == "fixture-text"
    assert "Inspect the fixture without changing any files." in json.dumps(seen[0]["messages"])
    assert "synthetic-key-refused" not in completed.stdout + completed.stderr
    if response_status == 200:
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["schema_version"] == "loopx_turn_result_v0"
        assert result["turn_key"] == signed_request()["turn_key"]
        assert result["classification"] == "fixture_observed"
        assert result["result_kind"] == "wait"
    else:
        assert completed.returncode == 1
        assert completed.stdout == ""
        # OAR owns the native failure classification; custom providers can
        # report unknown. The host must stop without guessing from error prose.
        assert "oar_pi_execution_failed" in completed.stderr
