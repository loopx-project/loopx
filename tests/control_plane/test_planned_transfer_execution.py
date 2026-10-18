"""One move confirmation continues the original Task through actual owners.

Expected output is declared before transfer. These isolated File/SQLite fixtures
use a real Codex process with deterministic local inference, never a paid model.
"""
from __future__ import annotations

import json
import http.client
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
                assert "prepared_cutover" not in result["readback"]
            else:
                # Admission alone must not hide the original UI recovery.
                assert result["readback"]["prepared_cutover"] == prepared["prepared_cutover"]
                assert call({"action": "inspect", "goal_id": "report"})[1]["prepared_cutover"] == prepared["prepared_cutover"]
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


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="requires the isolated real Codex fixture")
def test_original_task_continues_across_native_turns_with_stage_and_final_checks(source, tmp_path, monkeypatch):
    """A settled progress Turn is not Task completion or a second-Turn wake.

    The next explicit invocation uses the ordinary Turn owner, a fresh lease
    and the destination Agent's existing native thread. The original validator
    distinguishes its predeclared stage from terminal acceptance.
    """
    from loopx.control_plane.goals.planned_transfer_io import inspect_registered_goal_transfer
    from tests.control_plane.codex_checkpoint_fixture import real_native_source

    _goal_document(source, absolute=True)
    # The Task's validator is declared before moving or executing any work.
    validator = [sys.executable, "-c", "import json,sys; from pathlib import Path; "
        "r=json.loads(sys.stdin.read() or '{}'); "
        "assert Path('report.txt').read_bytes()==(b'12\\n' if r.get('result_kind')=='validated_progress' else b'21\\n')"]
    lease = source["acquire"]("declare-staged-checks")["lease"]
    original = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])
    source["cli"]("todo", "update", "--goal-id", "report", "--todo-id", source["todo"],
        "--agent-id", "agent-a", "--update-operation-id", "staged-independent-validator",
        "--update-expected-provider-revision", original["authority_read"]["provider_revision"],
        "--validation-command-json", json.dumps(validator),
        "--task-lease-idempotency-key", lease["idempotency_key"],
        "--task-lease-expected-version", str(lease["version"]), "--execute")
    source["release"](lease)
    original = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    actual = source["project"].parent / "agent"
    (actual / "input.txt").write_text("5\n7\n9\n")
    (actual / "report.txt").write_text("unfinished\n")
    destination, prepared = _prepare(source, tmp_path)
    requests = []

    def response(body, number, message):
        if number == 1:
            return message
        for item in body.get("input", []):
            for part in item.get("content", []) if isinstance(item, dict) else []:
                text = part.get("text", "") if isinstance(part, dict) else ""
                if "Turn request:\n" in text:
                    candidate = json.loads(text.split("Turn request:\n", 1)[1])
                    if not any(previous["turn_key"] == candidate["turn_key"] for previous in requests):
                        requests.append(candidate)
        request = requests[-1]
        assert request["transfer_context"]["selected_todo"]["todo_id"] == source["todo"]
        if number in (2, 4):
            script = ("from pathlib import Path; "
                + ("assert Path('report.txt').read_text()=='unfinished\\n'; "
                   "Path('report.txt').write_text(str(sum(map(int,Path('input.txt').read_text().split()[:2])))+'\\n')"
                   if number == 2 else
                   "assert Path('report.txt').read_text()=='12\\n'; "
                   "Path('report.txt').write_text(str(sum(map(int,Path('input.txt').read_text().split())))+'\\n')"))
            return {"type": "function_call", "id": f"fc_report_{number}", "call_id": f"call_report_{number}",
                "name": "exec_command", "arguments": json.dumps({
                    "cmd": shlex.quote(sys.executable) + " -c " + shlex.quote(script), "max_output_tokens": 1000})}
        result = {"schema_version": "loopx_turn_result_v0", "turn_key": request["turn_key"],
            "result_kind": "validated_progress" if number == 3 else "validated_completion",
            "completed_phases": ["host_execute", "typed_result"], "classification": "original_report_stage",
            "recommended_action": "Continue the original Task until its independent final check passes.",
            "next_action": "Sum the remaining input and independently accept 21.",
            "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
            "vision_unchanged_reason": "Original stage12 and independent terminal21 criteria remain unchanged.",
            "path_delta_mode": "unchanged", "agent_vision_json": "", "summary": "Original report advanced."}
        return {**message, "content": [{"type": "output_text", "text": json.dumps(result)}]}

    with real_native_source(tmp_path / "native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], actual,
            close_source=True, response_builder=response, sandbox="workspace-write") as (env, unused, _, calls):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        server, thread, call = _http(source)
        try:
            confirm = {"action": "confirm", "goal_id": "report", "operation_id": "move-a",
                "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]}
            status, first = call(confirm)
            assert status == 200 and first["execution"]["ok"] is True, first
            pending = next(row for row in first["readback"]["work"]["todos"] if row["todo_id"] == source["todo"])
            assert pending["status"] == "open" and pending["done"] is False
            target = destination / "agent-workspace"
            assert (target / "report.txt").read_bytes() == b"12\n"
            before = source["registry"].read_bytes(), source["fence"].read_bytes()
            lineage = {"goal_id": "report", "agent_id": "agent-a", "todo_id": source["todo"]}
            binding = select_codex_cli_session(destination / "runtime", lineage=lineage, session_scope="agent")
            assert binding["session_id"] not in ("original-context", unused)
            assert len(calls) == 3
            assert call(confirm)[1]["execution"] == first["execution"]
            assert len(calls) == 3 and (target / "report.txt").read_bytes() == b"12\n"
            prefix = [sys.executable, "-I", "-m", "loopx.cli", "--format", "json", "--registry",
                str(destination / "workspace/.loopx/registry.json"), "--runtime-root", str(destination / "runtime")]

            def invoke(*args):
                run = subprocess.run([*prefix, *args], cwd=target, capture_output=True, text=True, timeout=120)
                assert run.returncode == 0, run.stdout + run.stderr
                return json.loads(run.stdout)

            def spent():
                return invoke("history", "--goal-id", "report", "--limit", "50")["goals"][0]["quota"]["spent_slots"]

            assert spent() == 2  # one source debit, one independently checked stage
            lease = invoke("task-lease", "acquire", "--goal-id", "report", "--todo-id", source["todo"],
                "--owner", "agent-a", "--idempotency-key", "finish-original-task", "--write-scope", "report.txt",
                "--write-worktree", str(target), "--ttl-seconds", "600")["lease"]
            args = ["turn", "run-once", "--goal-id", "report", "--agent-id", "agent-a", "--todo-id", source["todo"],
                "--turn-instance-id", "finish-original-task", "--project", str(target), "--host", "codex-cli",
                "--codex-bin", env["CODEX_BIN"], "--codex-sandbox", "workspace-write", "--session-scope", "agent",
                "--validation-command-json", json.dumps(validator), "--timeout-seconds", "90", "--no-global-sync",
                "--task-lease-idempotency-key", lease["idempotency_key"],
                "--task-lease-expected-version", str(lease["version"]), "--execute"]
            for capability in ("filesystem_read", "filesystem_write", "shell"):
                args += ["--available-capability", capability]
            second = invoke(*args)
            assert second["ok"] is True and second["result_kind"] == "validated_completion", second
            assert len(calls) == 5 and len(requests) == 2
            assert requests[0]["turn_key"] != requests[1]["turn_key"]
            current_binding = select_codex_cli_session(destination / "runtime", lineage=lineage, session_scope="agent")
            assert current_binding["session_id"] == binding["session_id"]
            assert (target / "report.txt").read_bytes() == b"21\n" and spent() == 3
            work = inspect_registered_goal_transfer(registry_path=source["registry"], goal_id="report")["work"]
            completed = next(row for row in work["todos"] if row["todo_id"] == source["todo"])
            assert completed["done"] is True
            def completed_history():
                connection = http.client.HTTPConnection(*server.server_address, timeout=30)
                connection.request("GET", "/api/chat/completed-todos?goal_id=report")
                response = connection.getresponse()
                history = json.loads(response.read())
                connection.close()
                return response.status, history

            status, history = completed_history()
            assert status == 200 and history["total"] == 1
            assert history["items"][0]["todo_id"] == source["todo"]
            assert history["items"][0]["done"] is True
            target_registry = destination / "workspace/.loopx/registry.json"
            retained = target_registry.with_suffix(".offline")
            target_registry.rename(retained)
            try:
                status, unavailable = completed_history()
                assert status >= 400 and unavailable["ok"] is False
            finally:
                retained.rename(target_registry)
            assert completed_history()[1]["items"] == history["items"]
            for field in ("text", "claimed_by", "required_write_scopes", "task_repository", "completion_validation_sha256"):
                assert completed[field] == original[field]
            # Replaying either original operation cannot rerun tools or debit.
            restart_effect_runtime(wait_for_exit=True)
            assert invoke(*args)["ok"] is True
            assert call(confirm)[1]["execution"] == first["execution"]
            assert len(calls) == 5 and spent() == 3
            assert before == (source["registry"].read_bytes(), source["fence"].read_bytes())
            assert (actual / "report.txt").read_bytes() == b"unfinished\n"
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
