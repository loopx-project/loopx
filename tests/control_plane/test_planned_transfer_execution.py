"""One move confirmation continues the original Task through actual owners.

Expected output is declared before transfer. These isolated File/SQLite fixtures
use a real Codex process with deterministic local inference, never a paid model.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

import pytest

from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.todos.completion_validation_store import completion_validation_declaration_path, read_completion_validation_declaration
from loopx.control_plane.turn_driver.codex_sessions import select_codex_cli_session
from loopx.history import collect_history
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_operation import _http
from tests.control_plane.test_planned_transfer_product import _goal_document

source = source_fixture


def _prepare(source, tmp_path):
    destination = tmp_path / "destination"
    destination.mkdir()
    return destination, source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report",
        "--operation-id", "move-a", "--destination-root", str(destination),
        "--continue-todo-id", source["todo"], "--execute")


def _declare_original_checks(source):
    # Validator is original Task authority, not a browser field or model claim.
    preparation_lease = source["acquire"]("prepare-original-validation")["lease"]
    record = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])
    validator = [sys.executable, "-c", "from pathlib import Path; assert Path('report.txt').read_bytes()==b'21\\n'"]
    source["cli"]("todo", "update", "--goal-id", "report", "--todo-id", source["todo"],
        "--agent-id", "agent-a", "--update-operation-id", "original-independent-validator",
        "--update-expected-provider-revision", record["authority_read"]["provider_revision"],
        "--validation-command-json", json.dumps(validator),
        "--task-lease-idempotency-key", preparation_lease["idempotency_key"],
        "--task-lease-expected-version", str(preparation_lease["version"]), "--execute")
    source["release"](preparation_lease)
    original_task = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    return original_task, validator


def test_automatic_continuation_requires_original_independent_acceptance_before_fencing(source, tmp_path):
    before = source["registry"].read_bytes(), source["fence"].read_bytes()
    destination = tmp_path / "destination"
    destination.mkdir()
    result = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report",
        "--operation-id", "move-a", "--destination-root", str(destination),
        "--continue-todo-id", source["todo"], "--execute", expected_code=1)
    assert "independent" in result["error"]
    assert before == (source["registry"].read_bytes(), source["fence"].read_bytes())
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("native_outcome", ["accepted", "rejected"])
@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="requires the isolated real Codex fixture")
def test_confirmation_runs_registered_original_agent_and_independently_returns_result_once(source, tmp_path, monkeypatch, native_outcome):
    from tests.control_plane.codex_checkpoint_fixture import real_native_source

    _goal_document(source, absolute=True)
    original_task, validator = _declare_original_checks(source)
    actual = source["project"].parent / "agent"
    (actual / "input.txt").write_text("5\n7\n9\n")
    (actual / "report.txt").write_text("unfinished\n")
    destination, prepared = _prepare(source, tmp_path)
    assert prepared["prepared_cutover"]["continue_work"]["todo_id"] == source["todo"]
    assert prepared["prepared_cutover"]["continue_work"]["agent_id"] == "agent-a"
    requests = []

    def response(body, number, message):
        if number == 1:
            return message
        for item in body.get("input", []):
            for part in item.get("content", []) if isinstance(item, dict) else []:
                text = part.get("text", "") if isinstance(part, dict) else ""
                if "Turn request:\n" in text and not requests:
                    requests.append(json.loads(text.split("Turn request:\n", 1)[1]))
        assert requests and requests[0]["transfer_context"]["selected_todo"]["todo_id"] == source["todo"]
        if number == 2:
            script = "from pathlib import Path; assert Path('report.txt').read_text()=='unfinished\\n'; Path('report.txt').write_text(str(sum(map(int,Path('input.txt').read_text().split())))+'\\n')"
            if native_outcome == "rejected":
                script = script.replace("str(sum(map(int,Path('input.txt').read_text().split())))", "'20'")
            return {"type": "function_call", "id": "fc_report", "call_id": "call_report", "name": "exec_command",
                "arguments": json.dumps({"cmd": shlex.quote(sys.executable) + " -c " + shlex.quote(script), "max_output_tokens": 1000})}
        result = {"schema_version": "loopx_turn_result_v0", "turn_key": requests[0]["turn_key"],
            "result_kind": "validated_completion", "completed_phases": ["host_execute", "typed_result"],
            "classification": "original_report_complete", "recommended_action": "Read the original result.",
            "next_action": "Original report independently accepted as 21.", "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress", "vision_unchanged_reason": "Original independent report criterion remains unchanged.",
            "path_delta_mode": "unchanged", "agent_vision_json": "", "summary": "Continued the original unfinished report."}
        return {**message, "content": [{"type": "output_text", "text": json.dumps(result)}]}

    with real_native_source(tmp_path / "native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], actual,
            close_source=True, response_builder=response, sandbox="workspace-write") as (env, unused, _, calls):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        server, thread, call = _http(source)
        try:
            request = {"action": "confirm", "goal_id": "report", "operation_id": "move-a",
                "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]}
            status, result = call(request)
            assert status == 200 and result["recorded"] is True
            if native_outcome == "accepted":
                assert result["execution"]["ok"] is True, json.dumps(result["execution"])
            work = next(row for row in result["readback"]["work"]["todos"] if row["todo_id"] == source["todo"])
            assert work["done"] is (native_outcome == "accepted")
            if native_outcome == "accepted":
                assert "21" in work["note"]
            for field in ("text", "claimed_by", "task_repository", "required_write_scopes"):
                assert work.get(field) == original_task.get(field)
            assert work.get("required_capabilities", []) == original_task.get("required_capabilities", [])
            assert work["completion_validation_required"] is True
            assert work["completion_validation_sha256"] == original_task["completion_validation_sha256"]
            assert requests[0]["transfer_context"]["agent_id"] == "agent-a"
            binding = select_codex_cli_session(destination / "runtime",
                lineage={"goal_id": "report", "agent_id": "agent-a"}, session_scope="agent")
            assert binding["session_id"] not in ("original-context", unused)
            count = len(calls)
            assert count == 3
            target_work = destination / "agent-workspace"
            assert (target_work / "report.txt").read_bytes() == (b"21\n" if native_outcome == "accepted" else b"20\n")
            (target_work / "later.txt").write_text("Keep later destination work\n")
            quota_before_replay = collect_history(registry_path=destination / "workspace/.loopx/registry.json",
                runtime_root=destination / "runtime", goal_id="report", limit=50)["goals"][0]["quota"]["spent_slots"]
            restart_effect_runtime(wait_for_exit=True)
            replay = call(request)[1]
            assert replay["execution"]["ok"] == result["execution"]["ok"] and len(calls) == count
            assert replay["readback"]["work"] == result["readback"]["work"]
            assert (target_work / "later.txt").read_text() == "Keep later destination work\n"
            history = collect_history(registry_path=destination / "workspace/.loopx/registry.json",
                runtime_root=destination / "runtime", goal_id="report", limit=50)["goals"][0]
            assert history["quota"]["spent_slots"] == quota_before_replay
            assert quota_before_replay == (2 if native_outcome == "accepted" else 1)
            if native_outcome == "accepted":
                assert quota_before_replay == 2
                forged = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
                    "--registry", str(destination / "workspace/.loopx/registry.json"), "--runtime-root", str(destination / "runtime"),
                    "turn", "run-once", "--goal-id", "report", "--agent-id", "agent-a", "--todo-id", source["todo"],
                    "--turn-instance-id", result["execution"]["turn_instance_id"], "--project", str(target_work),
                    "--host", "codex-cli", "--task-lease-idempotency-key", "forged-new-proof", "--task-lease-expected-version", "1",
                    "--execute"], cwd=target_work, capture_output=True, text=True)
                assert forged.returncode == 1 and "completion lease proof changed" in json.loads(forged.stdout)["error"]
                assert len(calls) == count
            assert (actual / "report.txt").read_text() == "unfinished\n"
            assert not (source["runtime"] / "chat").exists()
            target_registry = destination / "workspace/.loopx/registry.json"
            data = json.loads(target_registry.read_text())
            assert data["goals"][0]["coordination"]["registered_agents"] == ["agent-a"]
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()


def test_default_move_restores_original_checks_without_launching_a_host(source, tmp_path, monkeypatch):
    _goal_document(source, absolute=True)
    original, validator = _declare_original_checks(source)
    destination = tmp_path / "destination"
    destination.mkdir()
    prepared = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report", "--operation-id", "move-a",
        "--destination-root", str(destination), "--execute")
    assert "continue_work" not in prepared["prepared_cutover"]
    monkeypatch.setenv("CODEX_BIN", "/fixture/unavailable-codex")
    server, thread, call = _http(source)
    try:
        status, result = call({"action": "confirm", "goal_id": "report", "operation_id": "move-a",
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]})
        assert status == 200 and result["recorded"] is True and "execution" not in result
        assert next(row for row in result["readback"]["work"]["todos"] if row["todo_id"] == source["todo"])["done"] is False
        declaration = read_completion_validation_declaration(runtime_root=destination / "runtime", goal_id="report",
            todo_id=source["todo"], expected_digest=original["completion_validation_sha256"])
        assert declaration["validation_command_argv"] == validator
        assert not (destination / "runtime/goals/report/turn-sessions").exists()
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


@pytest.mark.parametrize("damage", ["missing", "changed", "linked"])
def test_changed_target_validation_keeps_source_cancel_available(source, tmp_path, damage):
    _goal_document(source, absolute=True)
    original, _ = _declare_original_checks(source)
    destination, prepared = _prepare(source, tmp_path)
    blob = completion_validation_declaration_path(runtime_root=destination / "runtime", goal_id="report",
        todo_id=source["todo"]).parent / "blobs" / f"{original['completion_validation_sha256']}.json"
    if damage == "changed":
        blob.write_text("{}")
    else:
        blob.unlink()
        if damage == "linked":
            source_blob = completion_validation_declaration_path(runtime_root=source["runtime"], goal_id="report",
                todo_id=source["todo"]).parent / "blobs" / blob.name
            blob.symlink_to(source_blob)
    server, thread, call = _http(source)
    try:
        status, _ = call({"action": "confirm", "goal_id": "report", "operation_id": "move-a",
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]})
        assert status == 400
        assert call({"action": "inspect", "goal_id": "report"})[1]["source_state"] == "prepared"
        assert call({"action": "cancel", "goal_id": "report", "operation_id": "move-a"})[1]["readback"]["original_writer_fence_restored"] is True
        lease = source["acquire"]("continue-source-after-check-failure")["lease"]
        source["release"](lease)
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_missing_original_validation_content_refuses_before_fence(source, tmp_path):
    original, _ = _declare_original_checks(source)
    blob = completion_validation_declaration_path(runtime_root=source["runtime"], goal_id="report",
        todo_id=source["todo"]).parent / "blobs" / f"{original['completion_validation_sha256']}.json"
    blob.unlink()
    # The existing declaration owner also supports its legacy per-Task cache.
    # Removing only the immutable blob is not loss of the selected content.
    completion_validation_declaration_path(runtime_root=source["runtime"], goal_id="report", todo_id=source["todo"]).unlink()
    before = source["fence"].read_bytes(), source["registry"].read_bytes()
    destination = tmp_path / "destination"
    destination.mkdir()
    result = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report", "--operation-id", "move-a",
        "--destination-root", str(destination), "--continue-todo-id", source["todo"], "--execute", expected_code=1)
    assert result["ok"] is False and before == (source["fence"].read_bytes(), source["registry"].read_bytes())
    assert list(destination.iterdir()) == []
