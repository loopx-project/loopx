"""A lost App response never becomes a second transfer or model execution.

Use the production HTTP entry in a separate process. Exit that process after
destination admission or after accepted work, then recover through the same
original entry and operation. This qualifies explicit retry, not automatic wake.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import sys

import pytest

from loopx.history import collect_history
from tests.control_plane.codex_checkpoint_fixture import real_native_source
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_execution import (
    _declare_goal_result_acceptance,
    _declare_original_checks,
    _prepare,
)
from tests.control_plane.test_planned_transfer_product import _goal_document

source = source_fixture

_SERVICE = """
import json, os, sys
from pathlib import Path
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.goals import planned_transfer_execution

registry, runtime, fault = sys.argv[1:]
if fault == 'admitted':
    # The typed cutover already committed; no native Turn has started.
    planned_transfer_execution.continue_transferred_work = lambda **_: os._exit(73)
elif fault == 'accepted':
    send = ChatRequestHandler._send_json
    def lose_response(self, payload, **kwargs):
        if payload.get('execution', {}).get('ok') is True:
            os._exit(73)
        return send(self, payload, **kwargs)
    ChatRequestHandler._send_json = lose_response
server = ChatHTTPServer(('127.0.0.1', 0), ChatRequestHandler)
server.registry_path = Path(registry)
server.runtime_root = Path(runtime)
server.runtime_root_override = runtime
server.verbose = False
print(json.dumps({'port': server.server_port}), flush=True)
server.serve_forever()
"""


@contextmanager
def _service(source, directory: Path, fault: str = "none"):
    with (directory / f"http-{fault}.log").open("wb") as errors:
        process = subprocess.Popen([sys.executable, "-I", "-c", _SERVICE,
            str(source["registry"]), str(source["runtime"]), fault],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
            cwd=source["project"])
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                assert ready.select(30), "isolated HTTP listener did not start"
            port = json.loads(process.stdout.readline())["port"]

            def request(body=None, *, path="/api/chat/goal-transfer"):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
                try:
                    connection.request("GET" if body is None else "POST", path,
                        None if body is None else json.dumps(body),
                        {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()

            yield process, request
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(10)
            if process.stdout is not None:
                process.stdout.close()


@pytest.mark.parametrize("exit_after", ["admitted", "accepted"])
@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
    reason="requires the isolated real Codex fixture")
def test_service_exit_preserves_original_operation_and_single_execution(source, tmp_path, monkeypatch, exit_after):
    _goal_document(source, absolute=True)
    original, _ = _declare_original_checks(source)
    _declare_goal_result_acceptance(source, tmp_path)
    workspace = source["project"].parent / "agent"
    (workspace / "input.txt").write_text("5\n7\n9\n")
    (workspace / "report.txt").write_text("unfinished\n")
    destination, prepared = _prepare(source, tmp_path, result_file="report.txt")
    source_bytes = source["registry"].read_bytes(), source["fence"].read_bytes()
    requests = []

    def response(body, number, message):
        if number == 1:
            return message
        for item in body.get("input", []):
            for part in item.get("content", []) if isinstance(item, dict) else []:
                text = part.get("text", "") if isinstance(part, dict) else ""
                if "Turn request:\n" in text and not requests:
                    requests.append(json.loads(text.split("Turn request:\n", 1)[1]))
        assert requests[0]["transfer_context"]["selected_todo"]["todo_id"] == source["todo"]
        if number == 2:
            script = ("from pathlib import Path; assert Path('report.txt').read_text()=='unfinished\\n'; "
                "Path('report.txt').write_text(str(sum(map(int,Path('input.txt').read_text().split())))+'\\n')")
            return {"type": "function_call", "id": "fc_report", "call_id": "call_report", "name": "exec_command",
                "arguments": json.dumps({"cmd": shlex.quote(sys.executable) + " -c " + shlex.quote(script),
                    "max_output_tokens": 1000})}
        result = {"schema_version": "loopx_turn_result_v0", "turn_key": requests[0]["turn_key"],
            "result_kind": "validated_completion", "completed_phases": ["host_execute", "typed_result"],
            "classification": "original_report_complete", "recommended_action": "Read the original result.",
            "next_action": "Original report independently accepted as 21.", "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress", "vision_unchanged_reason": "Original independent criterion remains unchanged.",
            "path_delta_mode": "unchanged", "agent_vision_json": "", "summary": "Original report accepted as 21."}
        return {**message, "content": [{"type": "output_text", "text": json.dumps(result)}]}

    with real_native_source(tmp_path / "native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], workspace,
            close_source=True, response_builder=response, sandbox="workspace-write") as (env, _, _, calls):
        # The fixture's original source session made one response before the
        # move. Only the two destination responses below are new work.
        assert len(calls) == 1
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        confirmation = {"action": "confirm", "goal_id": "report", "operation_id": "move-a",
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]}

        def spent():
            return collect_history(registry_path=destination / "workspace/.loopx/registry.json",
                runtime_root=destination / "runtime", goal_id="report", limit=50)["goals"][0]["quota"]["spent_slots"]

        with _service(source, tmp_path, exit_after) as (process, request):
            with pytest.raises(http.client.RemoteDisconnected):
                request(confirmation)
            assert process.wait(10) == 73
        before_calls = 1 if exit_after == "admitted" else 3
        assert len(calls) == before_calls and spent() == (1 if exit_after == "admitted" else 2)
        with _service(source, tmp_path) as (_, request):
            status, recovered = request({"action": "inspect", "goal_id": "report"})
            assert status == 200 and recovered["operation_id"] == "move-a"
            pending = next(row for row in recovered["work"]["todos"] if row["todo_id"] == source["todo"])
            assert pending["done"] is (exit_after == "accepted")
            assert len(calls) == before_calls  # Listener restart/inspection grants no model replay.
            for _ in range(2):
                status, finished = request(confirmation)
                assert status == 200 and finished["execution"]["ok"] is True, finished
                assert len(calls) == 3 and spent() == 2
            status, result = request(path=f"/api/chat/goal-results/{source['todo']}?goal_id=report")
            assert status == 200 and result["text"] == "21\n"
            assert result["result"]["todo_id"] == source["todo"]
            completed = next(row for row in finished["readback"]["work"]["todos"] if row["todo_id"] == source["todo"])
            for field in ("text", "claimed_by", "required_write_scopes", "task_repository", "completion_validation_sha256"):
                assert completed[field] == original[field]
        assert len(requests) == 1
        assert (destination / "agent-workspace/report.txt").read_bytes() == b"21\n"
        assert (workspace / "report.txt").read_bytes() == b"unfinished\n"
        assert source_bytes == (source["registry"].read_bytes(), source["fence"].read_bytes())
