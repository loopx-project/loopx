"""A reviewed context continues original work under new destination authority.

The Task-only profile remains covered by test_planned_transfer_cutover. These
fixtures never activate a real Goal, borrow a native session or contact a paid model.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from time import perf_counter
from pathlib import Path

import pytest

from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, restart_effect_runtime
from loopx.control_plane.projects.registry_codec import project_registry_transaction
from loopx.control_plane.turn_driver.codex_sessions import select_codex_cli_session
from loopx.history import collect_history
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_cutover import OPERATION, _staged, _target_worktree
from tests.control_plane.test_planned_transfer_operation import _http
from tests.control_plane.test_planned_transfer_product import _goal_document, _prepared

source = source_fixture


def test_reviewed_context_admits_a_new_runtime_without_copying_a_session(source: dict, tmp_path: Path):
    target = _staged(source, tmp_path, source["provider"])
    arguments = ("--goal-id", "report", "--operation-id", OPERATION,
        "--destination-registry", str(target["registry"]), "--continuation", "reviewed_context")
    preview = source["cli"]("goal-transfer", "cutover-preview", *arguments)
    assert preview["decision"]["continuation"]["agent_id"] == "agent-a"
    assert preview["decision"]["admitted_goal"]["planned_transfer"]["identity_continuity"] == "same_goal_new_runtime"
    assert source["cli"]("goal-transfer", "cutover", *arguments, "--expected-preview-sha256",
        preview["preview_sha256"], "--execute")["target_runtime_admitted"] is True
    admission = FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None)
    admission.require_current()
    assert select_codex_cli_session(target["runtime"], lineage=source["lineage"], goal_admission=admission) is None
    context = target["cli"]("goal-transfer", "context", "--goal-id", "report", "--agent-id", "agent-a", "--todo-id", source["todo"])
    assert context["context"]["selected_todo"]["todo_id"] == source["todo"]
    assert context["context"]["selected_todo"]["done"] is False
    assert context["context"]["source_sessions_adopted"] is False
    assert context["context"]["credentials_adopted"] is False
    assert context["context"]["materials"][0]["relative_path"] == "input.txt"
    assert "Produce report.txt from input.txt" in context["context"]["requirements"]


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"), reason="requires the isolated real Codex fixture")
def test_real_fresh_native_turn_finishes_original_work_and_recovers_without_reexecution(source: dict, tmp_path: Path, monkeypatch):
    from tests.control_plane.codex_checkpoint_fixture import real_native_source

    _goal_document(source, absolute=True)
    # Original accepted context and its relation survive; no replacement Todo
    # is invented after transfer. Expected 21 is fixed before any model output.
    accepted = source["cli"]("todo", "add", "--goal-id", "report", "--role", "user", "--task-class", "user_action",
        "--text", "Accept exactly 21 from the unchanged three input values", "--operation-id", "original-criteria")["todo_id"]
    source["cli"]("todo", "complete", "--goal-id", "report", "--role", "user", "--todo-id", accepted,
        "--agent-id", "agent-a", "--evidence", "input.txt", "--successor-todo-id", source["todo"])
    source["cli"]("todo", "archive-completed", "--goal-id", "report", "--role", "user", "--max-active-done", "0", "--execute")
    original = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    started = perf_counter()
    target, prepared = _prepared(source, tmp_path)
    server, thread, call = _http(source)
    try:
        review = prepared["prepared_cutover"]
        status, confirmed = call({"action": "confirm", "goal_id": "report", "operation_id": OPERATION,
            "preview_sha256": review["preview_sha256"]})
        assert status == 200 and confirmed["readback"]["local_cutover"]["target_runtime_admitted"] is True
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    worktree = _target_worktree(target, material_paths=("input.txt", "requirements/GOAL.md"))
    lease = target["cli"]("task-lease", "acquire", "--goal-id", "report", "--todo-id", source["todo"],
        "--owner", "agent-a", "--idempotency-key", "new-target-native-work", "--write-scope", "report.txt",
        "--write-worktree", str(worktree))["lease"]
    assert lease["lease_epoch"] > 1 and lease["idempotency_key"] != "original-work"
    script = "from pathlib import Path; assert 'produce exactly 21' in Path('requirements/GOAL.md').read_text(); p=Path('report.txt'); assert not p.exists(); p.write_text(str(sum(map(int,Path('input.txt').read_text().split())))+'\\n')"
    requests = []

    def response(body, number, message):
        if number == 1:  # The reusable fixture's independent native source.
            return message
        texts = []
        for item in body.get("input", []):
            for part in item.get("content", []) if isinstance(item, dict) else []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        packet = next((text.split("Turn request:\n", 1)[1] for text in texts if "Turn request:\n" in text), None)
        if packet is not None:
            request = json.loads(packet)
            if not requests:
                requests.append(request)
        assert requests, "The ordinary native driver must supply the complete current request"
        context = requests[0]["transfer_context"]
        assert context["selected_todo"]["todo_id"] == source["todo"]
        assert {"kind": "goal_doc", "path": "requirements/GOAL.md", "role": "primary_goal_document"} in context["current_goal"]["authority_sources"]
        assert any(row["relative_path"] == "requirements/GOAL.md" for row in context["materials"])
        assert context["requirements"].find("Produce report.txt from input.txt") >= 0
        archived = next(row for row in context["todos"] if row["todo_id"] == accepted)
        assert archived["archive_state"] == "archive" and archived["successor_todo_ids"] == [source["todo"]]
        if number == 2:
            return {"type": "function_call", "id": "fc_original_report", "call_id": "call_original_report",
                "name": "exec_command", "arguments": json.dumps({"cmd": shlex.quote(sys.executable) + " -c " + shlex.quote(script), "max_output_tokens": 1000})}
        result = {"schema_version": "loopx_turn_result_v0", "turn_key": requests[0]["turn_key"],
            "result_kind": "validated_progress", "completed_phases": ["host_execute", "typed_result"],
            "classification": "original_work_continued", "recommended_action": "Read the independently verified original report.",
            "next_action": "Accept the original work through its existing Task.", "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress", "vision_unchanged_reason": "Continue the original report objective.",
            "path_delta_mode": "unchanged", "agent_vision_json": "", "summary": "Created the original report from transferred input."}
        return {**message, "content": [{"type": "output_text", "text": json.dumps(result)}]}

    with real_native_source(tmp_path / "fresh-native", os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"], worktree,
            close_source=True, response_builder=response, sandbox="danger-full-access") as (env, unused_session, _, calls):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        validator = [sys.executable, "-c", "from pathlib import Path; assert Path('report.txt').read_bytes()==b'21\\n'"]
        arguments = ("turn", "run-once", "--goal-id", "report", "--agent-id", "agent-a", "--todo-id", source["todo"],
            "--turn-instance-id", "continue-original-work", "--project", str(worktree), "--host", "codex-cli",
            "--codex-bin", env["CODEX_BIN"], "--codex-sandbox", "danger-full-access", "--codex-model", "fixture",
            "--codex-reasoning-effort", "high", "--session-scope", "agent", "--validation-command-json", json.dumps(validator),
            "--available-capability", "filesystem_read", "--available-capability", "filesystem_write", "--available-capability", "shell",
            "--timeout-seconds", "60", "--no-global-sync", "--execute")
        result = target["cli"](*arguments)
        assert result["ok"] is True, result
        assert (worktree / "report.txt").read_bytes() == b"21\n"
        binding = select_codex_cli_session(target["runtime"], lineage={"goal_id": "report", "agent_id": "agent-a"}, session_scope="agent")
        assert binding["session_id"] not in ("original-context", unused_session)
        # Read the same committed Turn; a second tool execution would fail the
        # independent not-exists assertion and must never happen.
        count = len(calls)
        restart_effect_runtime(wait_for_exit=True)
        replay = target["cli"](*arguments)
        assert replay["ok"] is True and len(calls) == count
        assert (worktree / "report.txt").read_bytes() == b"21\n"
        current = target["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
        assert current["text"] == original["text"] and current["required_write_scopes"] == original["required_write_scopes"]
        target["cli"]("todo", "complete", "--goal-id", "report", "--todo-id", source["todo"], "--agent-id", "agent-a",
            "--task-lease-idempotency-key", lease["idempotency_key"], "--task-lease-expected-version", str(lease["version"]),
            "--evidence", "report.txt", "--note", "Original result independently accepted as 21")
        observed = source["cli"]("goal-transfer", "inspect", "--goal-id", "report")
        assert next(row for row in observed["work"]["todos"] if row["todo_id"] == source["todo"])["done"] is True
        assert observed["continuation_available"] is False  # Full installed journey remains unqualified.
        assert observed["local_cutover"]["target_runtime_admitted"] is True
        assert target["cli"](*arguments)["ok"] is True and len(calls) == count
        history = collect_history(registry_path=target["registry"], runtime_root=target["runtime"], goal_id="report", limit=50)["goals"][0]
        assert history["quota"]["spent_slots"] == 2
        with pytest.raises((ValueError, EffectRuntimeRemoteError), match="prepared for transfer"):
            source["admission"].require_current()
        source["runtime"].rename(tmp_path / "closed-original-runtime")
        source["project"].rename(tmp_path / "closed-original-project")
        destination = target["cli"]("goal-transfer", "inspect", "--goal-id", "report")
        assert destination["work"] == observed["work"] and destination["local_cutover"]["target_runtime_admitted"] is True
        server, thread, call = _http(target)
        try:
            assert call({"action": "inspect", "goal_id": "report"}) == (200, destination)
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
        assert not source["runtime"].exists() and not source["project"].exists()
        (tmp_path / "product-journey-timing.json").write_text(json.dumps({"seconds": perf_counter() - started,
            "boundary": "Source-checkout isolated native tools and one real HTTP confirmation; not installed App or a controlled latency qualification."}))


@pytest.mark.parametrize("fault", ["context", "materials", "mode"])
def test_changed_review_refuses_before_source_final(source: dict, tmp_path: Path, fault: str):
    target = _staged(source, tmp_path, source["provider"])
    args = ("--goal-id", "report", "--operation-id", OPERATION, "--destination-registry", str(target["registry"]))
    preview = source["cli"]("goal-transfer", "cutover-preview", *args, "--continuation", "reviewed_context")
    if fault != "mode":
        path = target["project"] / ".loopx/goals/report" / ("TRANSFER_CONTEXT.json" if fault == "context" else "TRANSFER_MATERIALS.json")
        path.write_text("{}")
    result = source["cli"]("goal-transfer", "cutover", *args,
        "--continuation", "task_only" if fault == "mode" else "reviewed_context",
        "--expected-preview-sha256", preview["preview_sha256"], "--execute", expected_code=1)
    assert result["ok"] is False
    assert "context changed" in result["error"] if fault != "mode" else "preview changed" in result["error"]
    assert (target["project"] / ".loopx/goals/report/ACTIVE_GOAL_STATE.md").is_file()
    assert not (target["runtime"] / "goals/report/turn-sessions").exists()
    assert source["cli"]("goal-transfer", "inspect", "--goal-id", "report")["source_state"] == "prepared"


@pytest.mark.parametrize("fault", ["agent", "todo", "context", "registration"])
def test_current_binding_or_context_failure_cannot_start_a_host(source: dict, tmp_path: Path, fault: str):
    target = _staged(source, tmp_path, source["provider"])
    args = ("--goal-id", "report", "--operation-id", OPERATION, "--destination-registry", str(target["registry"]),
        "--continuation", "reviewed_context")
    preview = source["cli"]("goal-transfer", "cutover-preview", *args)
    source["cli"]("goal-transfer", "cutover", *args, "--expected-preview-sha256", preview["preview_sha256"], "--execute")
    if fault == "context":
        (target["project"] / ".loopx/goals/report/TRANSFER_CONTEXT.json").write_text("{}")
    elif fault == "registration":
        with project_registry_transaction(target["registry"], operation="stop-current-Goal") as transaction:
            payload = transaction.payload_copy()
            payload["goals"][0]["activation"]["state"] = "stopped"
            transaction.commit(payload)
    failed = target["cli"]("goal-transfer", "context", "--goal-id", "report",
        "--agent-id", "foreign" if fault == "agent" else "agent-a", "--todo-id",
        "todo_foreign" if fault == "todo" else source["todo"], expected_code=1)
    assert failed["ok"] is False
    if fault == "context":
        assert target["cli"]("goal-transfer", "inspect", "--goal-id", "report", expected_code=1)["ok"] is False
        assert source["cli"]("goal-transfer", "inspect", "--goal-id", "report", expected_code=1)["ok"] is False
    assert not (target["runtime"] / "goals/report/turn-sessions").exists()
    assert not (target["project"] / "report.txt").exists()
    if fault == "registration":
        # Stopping execution must not hide original work or its result. The
        # same current registration/context checks still guard this read.
        restart_effect_runtime(wait_for_exit=True)
        observed = target["cli"]("goal-transfer", "inspect", "--goal-id", "report")
        assert observed["local_cutover"]["target_activation_state"] == "stopped"
        assert any(row["todo_id"] == source["todo"] for row in observed["work"]["todos"])
        assert source["cli"]("goal-transfer", "inspect", "--goal-id", "report")["work"] == observed["work"]
        server, thread, call = _http(target)
        try:
            assert call({"action": "inspect", "goal_id": "report"}) == (200, observed)
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()
        with pytest.raises((ValueError, EffectRuntimeRemoteError)):
            FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None).require_current()
        assert not (target["runtime"] / "goals/report/turn-sessions").exists()
        # A stop denies Host work but cannot lock the ordinary Goal owner out
        # of resume. A new explanatory reason is metadata, not a new grant.
        with project_registry_transaction(target["registry"], operation="resume-current-Goal") as transaction:
            payload = transaction.payload_copy()
            payload["goals"][0]["activation"].update(state="active", reason="Owner resumed this Goal")
            transaction.commit(payload)
        FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None).require_current()
        assert target["cli"]("goal-transfer", "inspect", "--goal-id", "report")["local_cutover"]["target_activation_state"] == "active"
