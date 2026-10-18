"""Reviewed original work survives waiting and a local Chat Host restart.

Use disposable File/SQLite owners, real HTTP processes and real Codex execution
against deterministic local inference. Expected report bytes and original Task
acceptance are declared before preparation; no paid provider or active Goal.
"""
from __future__ import annotations

import http.client
from contextlib import ExitStack
import json
import os
import shlex
import sys
import time

import pytest

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.goals import planned_transfer_execution as execution
from loopx.control_plane.goals.planned_transfer_io import (
    control_transferred_goal, inspect_registered_goal_transfer, local_goal_transfer_cutover,
    read_goal_transfer_host_work,
)
from loopx.history import collect_history
from tests.control_plane.codex_checkpoint_fixture import real_native_source
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_execution import (
    _declare_goal_result_acceptance, _declare_original_checks, _prepare,
)
from tests.control_plane.test_planned_transfer_operation import _http
from tests.control_plane.test_planned_transfer_product import _goal_document
from tests.control_plane.test_planned_transfer_service_recovery import _service

source = source_fixture


def _admit(source, destination, review, *, host_service):
    return local_goal_transfer_cutover(registry_path=source["registry"], goal_id="report", operation_id="move-a",
        destination_registry=destination / "workspace/.loopx/registry.json", continuation="reviewed_context",
        continue_todo_id=source["todo"], continue_with_host_service=host_service,
        expected_preview_sha256=review["preview_sha256"], reviewed_preview_sha256=review["preview_sha256"])


@pytest.mark.parametrize("host_service", [False, True])
def test_host_service_requires_explicit_immutable_review_and_current_activation(source, tmp_path, host_service):
    _goal_document(source, absolute=True)
    _declare_original_checks(source)
    destination, prepared = _prepare(source, tmp_path, host_service=host_service)
    review = prepared["prepared_cutover"]
    assert review["continue_work"].get("continue_with_host_service", False) is host_service
    _admit(source, destination, review, host_service=host_service)
    target = destination / "workspace/.loopx/registry.json"
    before = read_goal_transfer_host_work(registry_path=target, goal_id="report")
    if not host_service:
        assert before is None  # Existing reviewed moves never become background work.
        assert read_goal_transfer_host_work(registry_path=source["registry"], goal_id="report") is None
        return
    assert before is not None and before["todo_id"] == source["todo"]
    assert before["continuation_budget"] == {"max_turns": 10, "timeout_seconds": 90}
    deadline = before["continuation_deadline_at_ms"]
    _admit(source, destination, review, host_service=True)
    assert read_goal_transfer_host_work(registry_path=target, goal_id="report")["continuation_deadline_at_ms"] == deadline
    with pytest.raises(RuntimeError, match="Host service continuation changed"):
        _admit(source, destination, review, host_service=False)
    current = inspect_registered_goal_transfer(registry_path=source["registry"], goal_id="report")
    control_transferred_goal(registry_path=source["registry"], goal_id="report", operation_id="move-a",
        operation="stop", expected_state_fingerprint=current["destination_control"]["expected_state_fingerprint"])
    assert read_goal_transfer_host_work(registry_path=target, goal_id="report") is None


def test_host_service_without_original_task_refuses_before_source_fencing(source, tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    before = source["registry"].read_bytes(), source["fence"].read_bytes()
    result = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report", "--operation-id", "move-a",
        "--destination-root", str(destination), "--continue-with-host-service", "--execute", expected_code=1)
    assert "requires its original Task" in result["error"]
    assert (source["registry"].read_bytes(), source["fence"].read_bytes()) == before
    assert list(destination.iterdir()) == []


def _responses(source, *, staged, requests):
    def response(body, number, message):
        if number == 1:
            return message  # The fixture's original source session.
        for item in body.get("input", []):
            for part in item.get("content", []) if isinstance(item, dict) else []:
                text = part.get("text", "") if isinstance(part, dict) else ""
                if "Turn request:\n" in text:
                    request = json.loads(text.split("Turn request:\n", 1)[1])
                    if not any(previous["turn_key"] == request["turn_key"] for previous in requests):
                        requests.append(request)
        request = requests[-1]
        assert request["transfer_context"]["selected_todo"]["todo_id"] == source["todo"]
        if number in (2, 4):
            expected = "unfinished" if number == 2 else "12"
            terms = "[:2]" if staged and number == 2 else ""
            script = (f"from pathlib import Path; assert Path('report.txt').read_text()=={expected!r}+'\\n'; "
                f"Path('report.txt').write_text(str(sum(map(int,Path('input.txt').read_text().split(){terms})))+'\\n')")
            return {"type": "function_call", "id": f"fc_{number}", "call_id": f"call_{number}", "name": "exec_command",
                "arguments": json.dumps({"cmd": shlex.quote(sys.executable) + " -c " + shlex.quote(script), "max_output_tokens": 1000})}
        result = {"schema_version": "loopx_turn_result_v0", "turn_key": request["turn_key"],
            "result_kind": "validated_progress" if staged and number == 3 else "validated_completion",
            "completed_phases": ["host_execute", "typed_result"], "classification": "original_report_stage",
            "recommended_action": "Continue the original Task until independently accepted as 21.",
            "next_action": "Complete and independently validate the original report.",
            "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "Original Task and Goal acceptance remain unchanged.",
            "path_delta_mode": "unchanged", "agent_vision_json": "", "summary": "Original report advanced."}
        return {**message, "content": [{"type": "output_text", "text": json.dumps(result)}]}
    return response


@pytest.mark.parametrize("boundary", ["admitted", "wait_after_progress"])
@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="requires the isolated real Codex fixture")
def test_local_host_reopens_and_continues_only_original_reviewed_work(source, tmp_path, monkeypatch, boundary):
    staged = boundary == "wait_after_progress"
    _goal_document(source, absolute=True)
    original, _ = _declare_original_checks(source, staged=staged)
    _declare_goal_result_acceptance(source, tmp_path)
    workspace = source["project"].parent / "agent"
    (workspace / "input.txt").write_text("5\n7\n9\n")
    (workspace / "report.txt").write_text("unfinished\n")
    destination, prepared = _prepare(source, tmp_path, result_file="report.txt", host_service=True)
    requests = []
    with real_native_source(tmp_path / "native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], workspace,
            close_source=True, response_builder=_responses(source, staged=staged, requests=requests),
            sandbox="workspace-write") as (env, _, _, calls):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        confirm = {"action": "confirm", "goal_id": "report", "operation_id": "move-a",
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]}
        if staged:
            native_effect, reads = execution.effect_runtime_result, 0

            def stop_after_committed_progress(method, value, **kwargs):
                nonlocal reads
                if method == "coordination.planned_transfer.execution_plan":
                    reads += 1
                    if reads == 2:
                        current = inspect_registered_goal_transfer(registry_path=source["registry"], goal_id="report")
                        control_transferred_goal(registry_path=source["registry"], goal_id="report", operation_id="move-a",
                            operation="stop", expected_state_fingerprint=current["destination_control"]["expected_state_fingerprint"])
                return native_effect(method, value, **kwargs)

            monkeypatch.setattr(execution, "effect_runtime_result", stop_after_committed_progress)
            server, thread, call = _http(source)
            try:
                status, first = call(confirm)
                # Stopping is an ordinary admission refusal for the request's
                # next dispatch, not a failure of the already committed stage.
                assert status == 200, first
                assert first["readback"]["local_cutover"]["target_activation_state"] == "stopped"
                assert not next(row for row in first["readback"]["work"]["todos"] if row["todo_id"] == source["todo"])["done"]
                assert (destination / "agent-workspace/report.txt").read_bytes() == b"12\n"
                assert len(calls) == 3
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)
            monkeypatch.setattr(execution, "effect_runtime_result", native_effect)
        else:
            with _service(source, tmp_path, "admitted") as (process, call):
                with pytest.raises(http.client.RemoteDisconnected):
                    call(confirm)
                assert process.wait(10) == 73 and len(calls) == 1
        source_bytes = source["registry"].read_bytes(), source["fence"].read_bytes()
        with ExitStack() as hosts:
            _, call = hosts.enter_context(_service(source, tmp_path, "host"))
            if not staged:
                # Two listeners observe the same reviewed operation. Its
                # original cross-runtime dispatch lock admits only one worker.
                peer = tmp_path / "second-host"
                peer.mkdir()
                hosts.enter_context(_service(source, peer, "host"))
            if staged:
                status, stopped = call({"action": "inspect", "goal_id": "report"})
                assert status == 200 and stopped["local_cutover"]["target_activation_state"] == "stopped"
                time.sleep(4)  # At least one ordinary Host tick while stopped.
                assert len(calls) == 3
                status, resumed = call({"action": "resume", "goal_id": "report", "operation_id": "move-a",
                    "expected_state_fingerprint": stopped["destination_control"]["expected_state_fingerprint"]})
                assert status == 200 and resumed["ok"] is True
            deadline = time.monotonic() + 100
            while True:
                status, current = call({"action": "inspect", "goal_id": "report"})
                task = next(row for row in current["work"]["todos"] if row["todo_id"] == source["todo"])
                if task["done"]:
                    break
                assert time.monotonic() < deadline, current
                time.sleep(1)
            assert status == 200
            for field in ("text", "claimed_by", "required_write_scopes", "completion_validation_sha256"):
                assert task[field] == original[field]
            status, output = call(path=f"/api/chat/goal-results/{source['todo']}?goal_id=report")
            assert status == 200 and output["text"] == "21\n"
            assert output["result"]["todo_id"] == source["todo"]
            assert len(calls) == (5 if staged else 3)
            spent = collect_history(registry_path=destination / "workspace/.loopx/registry.json",
                runtime_root=destination / "runtime", goal_id="report", limit=50)["goals"][0]["quota"]["spent_slots"]
            assert spent == (3 if staged else 2)
        assert len(requests) == (2 if staged else 1)
        assert (workspace / "report.txt").read_bytes() == b"unfinished\n"
        assert source_bytes == (source["registry"].read_bytes(), source["fence"].read_bytes())


@pytest.mark.parametrize("status", ["running", "failed"])
def test_reopening_host_does_not_retry_an_unsettled_native_effect(monkeypatch, tmp_path, status):
    monkeypatch.setattr(execution, "find_loopx_turn_key_by_settlement_identity", lambda *_, **__: "sha256:" + "a" * 64)
    monkeypatch.setattr(execution, "load_turn_journal", lambda *_: {"status": status})

    def invoke(*_):
        pytest.fail("A listener restart cannot dispatch, repair or release an unsettled Turn")

    result = execution._run_original_turn({"goal_id": "report", "agent_id": "agent-a", "todo_id": "todo_original"},
        "original", invoke, tmp_path, "unused", 90, host_service=True)
    assert result == {"ok": False, "error_code": "original_work_turn_requires_recovery"}


@pytest.mark.parametrize("source", ["sqlite"], indirect=True)
def test_expired_host_window_never_replenishes_on_reopen(source, tmp_path):
    _goal_document(source, absolute=True)
    _declare_original_checks(source)
    destination, prepared = _prepare(source, tmp_path, host_service=True)
    _admit(source, destination, prepared["prepared_cutover"], host_service=True)
    target = destination / "workspace/.loopx/registry.json"
    plan = read_goal_transfer_host_work(registry_path=target, goal_id="report")
    remaining = plan["continuation_deadline_at_ms"] / 1000 - time.time()
    time.sleep(max(0, remaining) + 0.05)
    assert read_goal_transfer_host_work(registry_path=target, goal_id="report") is None
    # Explicit recovery can still read original journals, with the same expired
    # deadline. It cannot obtain a new execution window by reconfirming.
    _admit(source, destination, prepared["prepared_cutover"], host_service=True)
    replay = effect_runtime_result("coordination.planned_transfer.execution_plan", {
        "runtime_root": str(destination / "runtime"), "goal_id": "report", "operation_id": "move-a"})["plan"]
    assert replay["continuation_deadline_at_ms"] == plan["continuation_deadline_at_ms"]
    assert read_goal_transfer_host_work(registry_path=target, goal_id="report") is None
