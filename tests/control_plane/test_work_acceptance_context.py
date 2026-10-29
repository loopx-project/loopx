"""Selected work retains the complete Goal basis without a second task inventory."""
from __future__ import annotations

from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.goals.acceptance import inspect_goal_acceptance
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.work_items.context_readback import _source_content


def _recording_host(tmp_path):
    runner = tmp_path / "runner.py"
    runner.write_text("import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(*, prompt, workspace, **kwargs):\n"
        "    Path(workspace, 'received.txt').write_text(prompt)\n"
        "    return json.dumps({'result_kind':'wait','summary':'Protocol input recorded.'})\n")
    return [sys.executable, "-m", "loopx.dsh_goal_mode", "--workspace", str(tmp_path),
        "--dsh-home", str(tmp_path / "host-home"), "--dsh-runner", str(runner)]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("checks", [False, True])
@pytest.mark.parametrize("material", [False, True])
def test_selected_acceptance_read_keeps_global_requirements_and_exact_binding(
    tmp_path, monkeypatch, provider, checks, material,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    goal, selected = "acceptance-context", "todo_selected"
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    body = "Retain every accepted export guarantee. " * 30 + "Stop before publication."
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nDeliver a compatible export.\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id=goal, domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    todos = [{"schema_version": "todo_item_v0", "todo_id": selected, "role": "agent",
        "text": body, "status": "open", "done": False, "task_class": "advancement_task",
        "action_kind": "implement", "index": 1, "source_section": "Agent Todo",
        "archive_state": "active", "claimed_by": "agent-a"}]
    todos += [{**todos[0], "todo_id": f"todo_peer_{index}", "text": "Independent peer work.",
        "index": index + 2, "claimed_by": "agent-b"} for index in range(32)]
    initialize_canonical_authority(runtime, goal,
        build_todo_runtime_shadow_projection(goal_id=goal, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider=provider)

    def cli(*arguments):
        code, result = run_json_cli_result(*arguments, registry_path=registry, runtime_root=runtime)
        assert code == 0, result
        result.pop("_returncode", None)  # harness metadata is not captured source content
        return result

    # Two owners' cumulative outcomes remain Goal requirements. Selection must
    # not silently narrow those requirements or their work/evidence references.
    owners = [("agent-a", selected), ("agent-b", "todo_peer_0")]
    for agent, todo in reversed(owners) if material else owners:
        document = tmp_path / f"{agent}.json"
        declaration = {"outcome_id": f"export-{agent}", "owner_agent_id": agent,
            "objective": f"Deliver the accepted {agent} export.", "todo_ids": [todo],
            "requirements": [{"id": "content", "description": "Retain the complete export content."},
                {"id": "recovery", "description": "Retain cancellation and recovery at the requirement tail."}],
            "source": {"ref": f"message:{agent}", "summary": "The owner requested a compatible export."}}
        for revision in range(8 if material else 1):
            declaration["source"] = {"ref": f"message:{agent}:{revision}", "summary":
                "The owner requested a compatible export." +
                (" Preserve this historical supporting evidence." * 9 if material else "")}
            document.write_text(json.dumps(declaration))
            before = cli("goal-acceptance", "inspect", "--goal-id", goal)
            cli("goal-acceptance", "refine", "--goal-id", goal, "--agent-id", agent,
                "--document", str(document), "--expected-provider-revision", before["provider_revision"],
                "--operation-id", f"outcome-{agent}-{revision}", "--execute")
    if checks:
        document = tmp_path / "checks.json"
        document.write_text(json.dumps({"scope": {"kind": "selected_work", "todo_ids": [selected]},
            "objective": "Deliver a compatible export.", "non_goals": ["No publication."],
            "criteria": [{"id": "content", "description": "Retain the complete export content.",
                "validation_argv": [sys.executable, "-c", "pass"], "validation_timeout_seconds": 5}],
            "bindings": [{"todo_id": selected, "criterion_ids": ["content"]}]}))
        before = cli("goal-acceptance", "inspect", "--goal-id", goal)
        cli("goal-acceptance", "configure", "--goal-id", goal, "--document", str(document),
            "--expected-provider-revision", before["provider_revision"],
            "--operation-id", "configure-export", "--execute")

    full = inspect_goal_acceptance(registry_path=registry, runtime_root=str(runtime), goal_id=goal)
    read = {"source": "goal_acceptance"}
    exact = _source_content(read, registry_path=registry, runtime_root=runtime,
        goal_id=goal, todo_id=selected)
    # The canonical acceptance owner, rather than another prose/role filter,
    # determines which binding belongs to this selected work.
    assert [row["todo_id"] for row in exact["tasks"]] == [selected]
    assert exact["tasks"] == [row for row in full["tasks"] if row["todo_id"] == selected]
    assert exact == {**full, "tasks": exact["tasks"]}
    assert "todo" not in exact and "completion_requirements" not in exact
    assert exact["goal_acceptance_contract"]["outcome_requirements"] == full["goal_acceptance_contract"]["outcome_requirements"]
    if checks:
        assert exact["contract"]["non_goals"] == ["No publication."]
        assert exact["tasks"][0]["state"] == "ready"
    else:
        assert exact["tasks"][0]["state"] == "unbound"
    # A Todo-less planning read still observes the whole existing inventory.
    assert _source_content(read, registry_path=registry, runtime_root=runtime,
        goal_id=goal, todo_id=None) == full
    missing = _source_content(read, registry_path=registry, runtime_root=runtime,
        goal_id=goal, todo_id="todo_missing")
    assert missing == {**full, "tasks": []}, "missing work must not fall back to another binding"

    packet = cli("quota", "should-run", "--goal-id", goal, "--agent-id", "agent-a",
        )
    channel = packet["interaction_contract"]["agent_channel"]
    assert channel["delivery_allowed"] is True and channel["work_context"]["complete"] is True
    # The public canonical reader retains its full format metadata. Host context
    # needs the same complete scoped records and source proof, not the dictionary.
    users = cli("todo", "list", "--goal-id", goal, "--role", "user",
        "--status", "open", "--agent-id", "agent-a")
    proof = deepcopy(users["authority_read"])
    assert proof["todo_read_model"].pop("contract_fields")
    assert channel["work_context"]["user_todos"] == {
        "source": users["source"], "authority_read": proof, "todos": users["todos"],
    }
    observed = next(row["content"] for row in channel["work_context"]["sources"]
        if row.get("source") == "goal_acceptance")
    scoped = _source_content(read, registry_path=registry, runtime_root=runtime,
        goal_id=goal, todo_id=selected, agent_id="agent-a")
    assert observed == scoped
    assert scoped["goal_acceptance_contract"] == exact["goal_acceptance_contract"], "reading plans never filter canonical requirements"
    assert scoped["outcome_context"]["required_outcome_ids"] == ["export-agent-a"]
    assert scoped["outcome_context"]["complete"] is True
    assert scoped["outcome_context"]["provider_revision"] == scoped["provider_revision"]
    unknown = _source_content(read, registry_path=registry, runtime_root=runtime,
        goal_id=goal, todo_id=selected, agent_id="not-registered")
    assert unknown["status"] == "loaded" and unknown["goal_acceptance_contract"] == exact["goal_acceptance_contract"]
    assert unknown["outcome_context"]["complete"] is False
    assert set(unknown["outcome_context"]["required_outcome_ids"]) == {"export-agent-a", "export-agent-b"}
    with pytest.raises(ValueError, match="registered Agent"):
        inspect_goal_acceptance(registry_path=registry, runtime_root=str(runtime),
            goal_id=goal, agent_id="not-registered", todo_id=selected, include_work_details=False)
    # The actual original-capture reader uses that plan without another guard
    # or provider read. Default delivery still retains both complete outcomes.
    from loopx.control_plane.turn_driver.decision_capture import write_decision_capture
    import hashlib

    capture = tmp_path / "original-decision.json"
    write_decision_capture(capture, packet)
    captured = capture.read_bytes()
    index = cli("quota", "context-read", "--capture-file", str(capture),
        "--capture-sha256", hashlib.sha256(captured).hexdigest(), "--outcome-ref",
        "--page-bytes", "8192")
    assert index["eof"] is True
    indexed = json.loads(index["content"])
    assert indexed["read_plan"]["complete"] is True
    assert indexed["read_plan"]["scope_applied"] is False
    assert {row["id"]: row["required_in_scope"] for row in indexed["outcomes"]} == {
        "export-agent-a": True, "export-agent-b": False,
    }
    assert capture.read_bytes() == captured
    assert any(row.get("source") == "goal_state" for row in channel["required_reads"])

    # The native presentation, its retained baseline and same-capture recovery
    # all use the actual required input. The canonical capture stays complete.
    from loopx.cli import build_parser
    from loopx.cli_commands.quota_capture import native_context_capture_view, read_context_capture
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook, project_codex_context

    with monkeypatch.context() as native:
        native.setenv("CODEX_THREAD_ID", "outcome-scope-session")
        native.chdir(tmp_path)
        native_root = tmp_path / "native-context"
        native_root.mkdir(mode=0o700)
        event = {"session_id": "outcome-scope-session", "cwd": str(tmp_path)}
        codex_context_hook(str(native_root), {**event, "hook_event_name": "SessionStart", "source": "startup"})

        def consume(page):
            observed = bytearray()
            while True:
                assert page["offset"] == len(observed)
                observed.extend(page["content"].encode())
                codex_context_hook(str(native_root), {**event, "hook_event_name": "PostToolUse",
                    "tool_response": page})
                if page["eof"]:
                    break
                page = read_context_capture(build_parser().parse_args(shlex.split(page["next_command"])[1:]))
                assert page["ok"]
            return json.loads(observed)

        source_before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
        assert project_codex_context(packet, directory=None, capture_directory=tmp_path / "disabled",
            goal_id=goal, agent_id="agent-a", surface="complete_turn", capture_id="disabled") is packet
        assert not (tmp_path / "disabled").exists()
        first = tmp_path / "native-first"
        first.mkdir(mode=0o700)
        projected = project_codex_context(packet, directory=str(native_root), capture_directory=first,
            goal_id=goal, agent_id="agent-a", surface="complete_turn", capture_id=hashlib.sha256(captured).hexdigest())
        assert projected["mode"] == "full"
        view = native_context_capture_view(projected, first, context_directory=str(native_root),
            metadata={"recovery": {"path": str(capture), "sha256": hashlib.sha256(captured).hexdigest()}})
        assert view["recovery"]["path"] == projected["recovery"]["path"]
        assert view["recovery"]["presentation_schema"] == "outcome_context_projection_v0"
        delivered = consume(view)
        assert delivered["schema_version"] == "outcome_context_projection_v0"
        assert delivered["read_scope"]["scope_applied"] is True
        assert {row["id"]: row["required_in_scope"] for row in delivered["outcome_index"]} == {
            "export-agent-a": True, "export-agent-b": False,
        }
        basis = next(row["content"] for row in delivered["context"]["interaction_contract"]["agent_channel"]["work_context"]["sources"]
            if row.get("source") == "goal_acceptance")
        reason = next(row["reason"] for row in delivered["context"]["interaction_contract"]["agent_channel"]["work_context"]["sources"]
            if row.get("source") == "goal_acceptance")
        assert "every outcome required by this capture's read_scope" in reason
        assert "complete bodies before reliance" in reason
        assert "Preserve the original Goal and earlier requirements" in reason
        assert [row["id"] for row in basis["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]] == ["export-agent-a"]
        own = basis["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]
        if material:
            assert "sources" not in own and "on demand" in reason
            material_ref = delivered["context_details"]
            detail = cli("quota", "context-read", "--capture-file", material_ref["path"],
                "--capture-sha256", material_ref["sha256"], "--select", own["supporting_material_pointer"] + "/sources",
                "--page-bytes", "16000", "--max-page-output-bytes", "20000")
            assert json.loads(detail["content"])["selections"][0]["value"] == next(
                row for row in observed["goal_acceptance_contract"]["outcome_requirements"]["outcomes"]
                if row["id"] == "export-agent-a")["sources"]
        else:
            assert "sources" in own and "context_details" not in delivered
        assert json.loads(Path(delivered["source_capture"]["path"]).read_bytes()) == packet
        # Bounded diagnostics reference the original immutable decision, never
        # a new guard. Read every byte through the public recovery command.
        diagnostics = delivered["context"]["agent_todo_summary"]["payload_compaction"]
        assert diagnostics["full_detail_capture_ref"] == "$.source_capture"
        source_ref = delivered["source_capture"]
        original_page = read_context_capture(build_parser().parse_args(["--format", "json", "quota", "context-read",
            "--capture-file", source_ref["path"], "--capture-sha256", source_ref["sha256"]]))
        assert consume(original_page) == packet
        for key in ("selected_todo", "planning_horizon", "capability_gate", "action_portfolio"):
            if key in packet:
                assert delivered["context"][key] == packet[key]
        candidate = json.loads((native_root / "session.json").read_text())["candidate"]
        assert candidate["capture_file"] == projected["recovery"]["path"]
        assert json.loads(Path(candidate["capture_file"]).read_bytes()) == delivered

        optional_index = cli("quota", "context-read", "--capture-file", view["path"],
            "--capture-sha256", view["sha256"], "--outcome-ref", "--page-bytes", "8192")
        indexed = json.loads(optional_index["content"])
        assert indexed["read_plan"]["scope_applied"] is True
        peer = next(row for row in indexed["outcomes"] if row["id"] == "export-agent-b")
        detail = cli("quota", "context-read", "--capture-file", view["path"],
            "--capture-sha256", view["sha256"], "--outcome-ref", peer["outcome_ref"], "--page-bytes", "8192")
        assert json.loads(detail["content"])["outcome"]["id"] == "export-agent-b"
        # A wrapper's own hash cannot substitute a missing/corrupt original.
        broken = deepcopy(delivered)
        broken["source_capture"]["sha256"] = "0" * 64
        invalid = tmp_path / "invalid-required-context.json"
        write_decision_capture(invalid, broken)
        rejected = read_context_capture(build_parser().parse_args(["--format", "json", "quota", "context-read",
            "--capture-file", str(invalid), "--capture-sha256", hashlib.sha256(invalid.read_bytes()).hexdigest(),
            "--outcome-ref", peer["outcome_ref"]]))
        assert rejected["ok"] is False and "digest mismatch" in rejected["reason"]

        second = tmp_path / "native-second"
        second.mkdir(mode=0o700)
        warm = project_codex_context(packet, directory=str(native_root), capture_directory=second,
            goal_id=goal, agent_id="agent-a", surface="complete_turn", capture_id=hashlib.sha256(captured).hexdigest())
        assert warm["mode"] in {"full", "delta"}
        warm_view = native_context_capture_view(warm, second, context_directory=str(native_root))
        consume(warm_view)
        codex_context_hook(str(native_root), {**event, "hook_event_name": "PreCompact"})
        assert "candidate" not in json.loads((native_root / "session.json").read_text())
        recovery = read_context_capture(build_parser().parse_args(shlex.split(warm["recovery"]["command"])[1:]))
        restored = consume(recovery)
        assert restored["read_scope"]["required_outcome_refs"] == delivered["read_scope"]["required_outcome_refs"]
        assert restored == json.loads(Path(warm["recovery"]["path"]).read_bytes())
        restored_diagnostics = restored["context"]["agent_todo_summary"]["payload_compaction"]
        assert restored_diagnostics["full_detail_capture_ref"] == "$.source_capture"
        source_ref = restored["source_capture"]
        assert consume(read_context_capture(build_parser().parse_args(["--format", "json", "quota", "context-read",
            "--capture-file", source_ref["path"], "--capture-sha256", source_ref["sha256"]]))) == packet
        assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == source_before
        assert capture.read_bytes() == captured
        # Optional scope/codec failure recovers the complete original source,
        # without weakening admission or inventing another user gate.
        from loopx.control_plane.quota import turn_envelope

        original_operation = turn_envelope._turn_envelope_result

        def unavailable(operation, **request):
            if operation == "native_context_delivery":
                raise ValueError("synthetic optional scope projection failure")
            return original_operation(operation, **request)

        with native.context() as failed:
            failed.setattr(turn_envelope, "_turn_envelope_result", unavailable)
            codex_context_hook(str(native_root), {**event, "hook_event_name": "PreCompact"})
            fallback_dir = tmp_path / "native-fallback"
            fallback_dir.mkdir(mode=0o700)
            fallback = project_codex_context(packet, directory=str(native_root), capture_directory=fallback_dir,
                goal_id=goal, agent_id="agent-a", surface="complete_turn", capture_id="fallback")
            assert fallback["mode"] == "full"
            assert consume(native_context_capture_view(fallback, fallback_dir, context_directory=str(native_root))) == packet
        assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == source_before
        assert capture.read_bytes() == captured
    # The normal guard's complete captured basis is the envelope input; no
    # second guard may substitute a different observation for the host.
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope

    envelope = build_turn_envelope(packet)
    assert channel["work_context"]["selected_todo_text_verified"] is True
    assert envelope["work_context"] == {
        key: value
        for key, value in channel["work_context"].items()
        if key != "selected_todo_text_verified"
    }
    assert envelope["action"]["selected_todo"]["text_source_verified"] is True
    request = build_loopx_turn_host_request(build_loopx_turn_plan(envelope,
        host="codex-cli", execution_mode="isolated-headless", turn_instance_id="export-context-turn"))
    before = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    command = _recording_host(tmp_path)
    result = subprocess.run(command,
        input=json.dumps(request), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    prompt = (tmp_path / "received.txt").read_text()
    authority = json.loads(prompt.split("Turn authority JSON:\n", 1)[1].split("\n\nWhen finished", 1)[0])
    assert authority["work_context"] == envelope["work_context"]
    assert authority["selected_todo"]["text_source_verified"] is True
    changed = deepcopy(request)
    acceptance = next(row["content"] for row in changed["turn_envelope"]["work_context"]["sources"]
        if row.get("source") == "goal_acceptance")
    acceptance["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][-1]["requirements"][-1]["description"] = "Omit recovery."
    rejected = subprocess.run(command, input=json.dumps(changed), capture_output=True, text=True, timeout=30)
    assert rejected.returncode != 0 and "action signature" in rejected.stderr
    assert (tmp_path / "received.txt").read_text() == prompt
    assert {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()} == before

    # A failed exact acceptance read is still a pending obligation, never an
    # empty/successful basis. Exercise the real CLI with only that IO fault.
    from loopx.cli import main
    from loopx.control_plane.work_items import context_readback

    def unavailable(**_kwargs):
        raise OSError("acceptance source unavailable")

    with monkeypatch.context() as fault:
        fault.setattr(context_readback, "inspect_goal_acceptance", unavailable)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
                "quota", "should-run", "--goal-id", goal, "--agent-id", "agent-a", ])
        failed = json.loads(output.getvalue())["interaction_contract"]["agent_channel"]
        assert code == 0 and failed["delivery_allowed"] is False
        assert failed["work_context"]["complete"] is False
        assert any(row.get("source") == "goal_acceptance" for row in failed["required_reads"])
    recovered = cli("quota", "should-run", "--goal-id", goal, "--agent-id", "agent-a", )
    assert recovered["interaction_contract"]["agent_channel"]["work_context"]["complete"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_acceptance_observation_does_not_transport_the_large_selected_body(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nRetain complete work requirements.\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="large-acceptance-context", domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a"], quota_allowed_slots=None)
    todo = {"schema_version": "todo_item_v0", "todo_id": "todo_large", "role": "agent",
        "text": "Retain this complete requirement. " * 100_000 + "Do not omit this tail.",
        "status": "open", "done": False, "task_class": "advancement_task", "action_kind": "implement",
        "index": 1, "source_section": "Agent Todo", "archive_state": "active", "claimed_by": "agent-a"}
    from loopx.control_plane.effect_runtime import MAX_REQUEST_BYTES

    assert len(todo["text"].encode()) > MAX_REQUEST_BYTES
    initialize_canonical_authority(runtime, "large-acceptance-context",
        build_todo_runtime_shadow_projection(goal_id="large-acceptance-context", todos=[todo], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    basis = _source_content({"source": "goal_acceptance"}, registry_path=registry,
        runtime_root=runtime, goal_id="large-acceptance-context", todo_id="todo_large")
    assert basis["status"] == "loaded" and [row["todo_id"] for row in basis["tasks"]] == ["todo_large"]
    assert "todo" not in basis and "completion_requirements" not in basis


@pytest.mark.parametrize("change", ["work_added", "work_changed", "timer_wait", "checks_removed"])
def test_captured_acceptance_does_not_replace_current_work_admission(acceptance_goal, tmp_path, change):
    project, document, acceptance_cli, run = acceptance_goal
    goal, agent, selected = "goal-acceptance", "agent-a", "todo_export"
    from canonical_authority_fixture import empty_todo_display

    # Dispatch needs the complete non-Todo Goal, while the canonical provider
    # retains this work. The old unmarked display left a pending Goal read.
    empty_todo_display(project / "state.md")

    def cli(*arguments):
        code, result = run(*arguments)
        assert code == 0, result
        result.pop("_returncode", None)
        return result

    def configure(*arguments):
        code, result = acceptance_cli(*arguments)
        assert code == 0, result
        return result

    requirements = tmp_path / "requirements.json"
    requirements.write_text(json.dumps({"outcome_id": "export", "owner_agent_id": agent,
        "objective": "Retain the complete export", "todo_ids": [selected], "requirements": [
            {"id": "content", "description": "Retain every original section."},
            {"id": "recovery", "description": "Stop before publication; preserve the recovery tail."}],
        "source": {"ref": "message:original", "summary": "The owner requested a complete export."}}))
    configure("refine", "--agent-id", agent, "--document", str(requirements),
        "--expected-provider-revision", configure("inspect")["provider_revision"],
        "--operation-id", "original-requirements", "--execute")
    configure("configure", "--document", str(document),
        "--expected-provider-revision", configure("inspect")["provider_revision"],
        "--operation-id", "original-checks", "--execute")
    original = configure("inspect")["goal_acceptance_contract"]["outcome_requirements"]
    capture = tmp_path / "decision"
    guard = ("quota", "should-run", "--goal-id", goal, "--agent-id", agent, "--codex-app")
    cli(*guard, "--todo-id", selected, "--turn-instance-id", "original-delivery",
        "--turn-envelope", "--decision-output-dir", str(capture))
    saved = (capture / "decision.json").read_bytes()
    full = json.loads(saved)
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from loopx.control_plane.scheduler.execution_context import scheduler_execution_context_for_runtime_profile

    first = build_turn_envelope(full, captured_decision_path=str(capture / "decision.json"),
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile("codex_app_heartbeat"))
    assert not any(row.get("source") == "goal_state" for row in full["interaction_contract"]["agent_channel"]["required_reads"])
    assert any(row.get("source") == "goal_state" for row in first["work_context"]["sources"])
    basis = next(row["content"] for row in first["work_context"]["sources"]
        if row.get("source") == "goal_acceptance")
    assert first["action"]["delivery_allowed"] is True
    assert basis["tasks"][0]["state"] == "ready"
    assert basis["goal_acceptance_contract"]["outcome_requirements"] == original
    request = build_loopx_turn_host_request(build_loopx_turn_plan(first,
        host="codex-cli", execution_mode="isolated-headless", turn_instance_id="original-delivery", work_contract=full))
    result = subprocess.run(_recording_host(project), input=json.dumps(request),
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    prompt = (project / "received.txt").read_text()
    authority = json.loads(prompt.split("Turn request:\n", 1)[1].split("\n\nWhen finished", 1)[0])
    assert authority["work_contract"] == full

    # Finish the original synthetic work before attempting another managed
    # Turn. An unsettled receipt is its own legitimate recovery boundary.
    (project / "artifact.txt").write_text("accepted")
    check = json.loads(document.read_text())["criteria"][0]["validation_argv"]
    subprocess.run(check, cwd=project, check=True)
    identity = ("--goal-id", goal, "--agent-id", agent, "--todo-id", selected,
        "--turn-instance-id", "original-delivery")
    cli("refresh-state", *identity, "--classification", "validated_progress",
        "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
        "--delivery-boundary", "in_flight_continuation", "--no-global-sync", "--suppress-external-sinks")
    for appended in (True, False):
        spent = cli("quota", "spend-slot", *identity, "--slots", "1", "--source", "heartbeat", "--execute")
        assert spent["appended"] is appended

    target = selected
    if change == "work_added":
        target = cli("todo", "add", "--goal-id", goal, "--role", "agent", "--claimed-by", agent,
            "--text", "Deliver a newly requested export", "--task-class", "advancement_task",
            "--action-kind", "implement")["todo_id"]
    elif change == "work_changed":
        cli("todo", "update", "--goal-id", goal, "--todo-id", selected, "--agent-id", agent,
            "--text", "Deliver the export and a newly required checksum")
    elif change == "timer_wait":
        cli("todo", "update", "--goal-id", goal, "--todo-id", selected, "--agent-id", agent,
            "--status", "deferred", "--resume-when", "resume_at:2099-01-01T00:00:00Z")
    else:
        configure("clear-checks", "--expected-provider-revision", configure("inspect")["provider_revision"],
            "--operation-id", "remove-checks", "--execute")

    # A capture is an immutable observation. Reading its detail cannot admit
    # changed work, remove a wait or silently rebind the owner's checks.
    code, current = run(*guard, "--todo-id", target, "--turn-instance-id", "current-delivery")
    assert code == (0 if change == "checks_removed" else 1), current
    channel = current["interaction_contract"]["agent_channel"]
    assert channel["delivery_allowed"] is (change == "checks_removed")
    if change != "checks_removed":
        assert current["error_code"] == "quota_action_selection_rejected"
    cold = configure("inspect")
    # Work status and its derived fingerprint may change; every original
    # objective, requirement, source and work link must remain intact.
    requirements_now = cold["goal_acceptance_contract"]["outcome_requirements"]
    assert {k: v for k, v in requirements_now.items() if k != "outcomes"} == {
        k: v for k, v in original.items() if k != "outcomes"}
    old_outcome, = original["outcomes"]
    new_outcome, = requirements_now["outcomes"]
    assert {k: v for k, v in new_outcome.items() if k not in {"work", "basis_digest"}} == {
        k: v for k, v in old_outcome.items() if k not in {"work", "basis_digest"}}
    assert (new_outcome["basis_digest"] != old_outcome["basis_digest"]) is (
        change in {"work_changed", "timer_wait"})
    assert new_outcome["work"] == [
        {**old_outcome["work"][0], "status": "deferred" if change == "timer_wait" else "open"}]
    if change == "work_added":
        assert {row["todo_id"]: row["state"] for row in cold["goal_acceptance_contract"]["tasks"]} == {
            selected: "ready", target: "unbound"}
    if change == "work_changed":
        assert cold["goal_acceptance_contract"]["tasks"][0]["state"] == "stale"
    if change == "checks_removed":
        assert current["interaction_contract"]["user_channel"]["action_required"] is False
    # The unselected normal/replan/wait view follows the same current owner,
    # including all cumulative requirements whenever execution needs a read.
    inspection = cli(*guard[:-1])
    current_context = inspection["interaction_contract"]["agent_channel"].get("work_context", {})
    for source in current_context.get("sources", []):
        if source.get("source") == "goal_acceptance":
            assert source["content"]["goal_acceptance_contract"] == cold["goal_acceptance_contract"]
    before_read = {p: p.read_bytes() for p in capture.rglob("*") if p.is_file()}
    detail = subprocess.run(shlex.split(first["detail_ref"]["full_decision"]),
        capture_output=True, check=True).stdout
    assert detail == saved and json.loads(detail) == full
    assert {p: p.read_bytes() for p in capture.rglob("*") if p.is_file()} == before_read
    assert configure("inspect") == cold
    assert (project / "received.txt").read_text() == prompt
