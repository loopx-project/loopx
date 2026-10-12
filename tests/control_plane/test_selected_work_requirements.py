"""Default heartbeat delivers current work and preserves progressive full Goal reads."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
from loopx.control_plane.work_items.context_readback import _source_content
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.todos.goal_todo_projection import retained_todo_summary_fields
from loopx.control_plane.turn_driver.codex_cli import _prompt
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt


def test_legacy_selected_todo_requires_same_goal_state_revision(tmp_path: Path) -> None:
    state = tmp_path / "state.md"
    state.write_text(
        "---\nstatus: active\n---\n# Goal\n## Agent Todo\n"
        "- [ ] Keep the selected task current.\n"
        "  <!-- loopx:todo todo_id=todo_current status=open task_class=advancement_task -->\n"
    )

    with pytest.raises(ValueError, match="changed after the Goal state read"):
        _source_content(
            {"source": "selected_todo"},
            registry_path=tmp_path / "registry.json",
            runtime_root=tmp_path / "runtime",
            goal_id="goal-current",
            todo_id="todo_current",
            state_file=state,
            expected_state_revision="sha256:stale",
        )


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_inline_user_context_preserves_full_scoped_gates_without_granting_delivery(tmp_path, monkeypatch, provider):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    gate_text = "Review the publication evidence. " * 35 + "Approval is required before publication."
    state_text = (
        "---\nstatus: active\n---\n# Goal\n## Objective\nInspect then publish an approved report.\n## Agent Todo\n"
        "- [ ] [P1] Inspect the report\n"
        "  <!-- loopx:todo todo_id=todo_inspect status=open task_class=advancement_task claimed_by=agent-a action_kind=inspect_report -->\n"
        "- [ ] [P0] Publish the report\n"
        "  <!-- loopx:todo todo_id=todo_publish status=open task_class=advancement_task claimed_by=agent-a action_kind=publish_report -->\n"
        "## User Todo\n- [ ] " + gate_text + "\n"
        "  <!-- loopx:todo todo_id=todo_gate role=user status=open task_class=user_gate blocks_agent=agent-a unblocks_todo_id=todo_publish -->\n"
        "- [ ] Decide the report format\n"
        "  <!-- loopx:todo todo_id=todo_user_action role=user status=open task_class=user_action bound_agent=agent-a -->\n"
        "- [ ] Peer-only approval\n"
        "  <!-- loopx:todo todo_id=todo_peer_gate role=user status=open task_class=user_gate blocks_agent=agent-b -->\n"
        "- [ ] Peer-only decision\n"
        "  <!-- loopx:todo todo_id=todo_peer_action role=user status=open task_class=user_action bound_agent=agent-b -->\n"
        "- [ ] Global evidence decision\n"
        "  <!-- loopx:todo todo_id=todo_global_action role=user status=open task_class=user_action goal_bound=true -->\n"
        "- [x] Completed approval\n"
        "  <!-- loopx:todo todo_id=todo_closed_gate role=user status=done task_class=user_gate blocks_agent=agent-a -->\n"
    )
    state.write_text(state_text)
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="requirements-goal", domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    if provider != "legacy":
        goal = json.loads(registry.read_text())["goals"][0]
        fields, _, _ = parse_todo_source(state_text, goal=goal, state_path=state)
        items = (retained_todo_summary_fields(fields["agent"], rollout_events=[])["agent_todos"]["items"]
            + retained_todo_summary_fields(fields["user"], rollout_events=[])["user_todos"]["items"])
        initialize_canonical_authority(runtime, "requirements-goal",
            build_todo_runtime_shadow_projection(goal_id="requirements-goal", todos=items, handoff_mode="soft_claim"),
            state_path=state, provider=provider)

    def guard(*extra):
        code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "requirements-goal",
            "--agent-id", "agent-a", "--scan-path", str(tmp_path), *extra,
            registry_path=registry, runtime_root=runtime)
        assert code == 0, json.dumps(packet, indent=2)
        return packet

    packet = guard()
    channel = packet["interaction_contract"]["agent_channel"]
    users = channel["work_context"]["user_todos"]["todos"]
    assert {row["todo_id"] for row in users} == {"todo_gate", "todo_user_action", "todo_global_action"}
    gate = next(row for row in users if row["todo_id"] == "todo_gate")
    assert gate["text"] == gate_text
    assert gate["blocks_agent"] == "agent-a" and gate["unblocks_todo_id"] == "todo_publish"
    assert packet["requires_user_action"] is True
    assert packet["scoped_user_gate_fallback"]["selected_executable"]["todo_id"] == "todo_inspect"
    assert channel["work_context"]["complete"] is True
    envelope = guard("--turn-envelope")
    assert envelope["work_context"]["user_todos"] == channel["work_context"]["user_todos"]
    assert state.read_text() == state_text
    if provider != "legacy":
        # Canonical tasks still exist: the full Goal source must fail closed,
        # rather than silently substituting a Todo summary for missing intent.
        state.unlink()
        failed = guard()
        failed_channel = failed["interaction_contract"]["agent_channel"]
        assert failed_channel["delivery_allowed"] is False
        assert failed_channel["work_context"]["complete"] is False
        assert any(read["source"] == "goal_state" for read in failed_channel["required_reads"])
        state.write_text(state_text)
        assert guard()["interaction_contract"]["agent_channel"]["work_context"]["complete"] is True


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_current_work_requirements_reach_real_guard_and_host_without_display_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state's requirements.md"
    work = ("Repair the cursor boundary. " + "Preserve the existing query contract. " * 24 +
            "Acceptance: no missing or repeated boundary records; retain cancellation and add regression coverage.")
    goal_requirements = ("---\nstatus: active\n---\n# Goal\n## Objective\nDeliver a compatible query service.\n"
        "## Acceptance\n" + "Retain all query guarantees. " * 24 + "Do not omit records in any query mode.\n"
        "## Non-goals\nNo unrelated migration.\n## Stop Conditions\nStop before an unauthorized deployment.\n\n")
    state.write_text(goal_requirements +
        "## Agent Todo\n- [ ] [P2] Unrelated peer work must not replace the selected requirements.\n"
        "  <!-- loopx:todo todo_id=todo_peer_work status=open task_class=advancement_task claimed_by=agent-b -->\n"
        f"- [ ] [P1] {work}\n"
        "  <!-- loopx:todo todo_id=todo_cursor_work status=open task_class=advancement_task claimed_by=agent-a -->\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="requirements-goal", domain="requirements", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    expected = "[P1] " + work
    if provider != "legacy":
        goal = json.loads(registry.read_text())["goals"][0]
        fields, _archived, _sections = parse_todo_source(state.read_text(), goal=goal, state_path=state)
        full_items = retained_todo_summary_fields(fields["agent"], rollout_events=[])["agent_todos"]["items"]
        snapshot = build_todo_runtime_shadow_projection(
            goal_id="requirements-goal", todos=full_items, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, "requirements-goal", snapshot,
            state_path=state, provider=provider)
        state.write_text(goal_requirements + "## Agent Todo\n- [ ] Stale display: do only an easier check.\n")

    def guard(*extra: str) -> dict:
        code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "requirements-goal",
            "--agent-id", "agent-a", "--scan-path", str(tmp_path), *extra,
            registry_path=registry, runtime_root=runtime)
        assert code == 0, json.dumps(packet, indent=2)
        assert packet["should_run"] is True, packet.get("reason")
        return packet

    envelope = guard("--turn-envelope")
    before = state.read_bytes()
    # Run the actual default product prompt's guard: no capture/envelope opt-in.
    code, generated = run_json_cli_result("heartbeat-prompt", "--goal-id", "requirements-goal",
        "--agent-id", "agent-a", "--codex-app", "--cli-bin", str(Path(sys.executable).parent / "loopx"),
        registry_path=registry, runtime_root=runtime)
    assert code == 0 and generated["ok"], generated
    body = generated["task_body"]
    assert "work_context" in body
    script = re.search(r"```sh\n(.*?)\n```", body, re.S).group(1)
    assert "--turn-envelope" not in script and "--decision-output-root" not in script
    env = {**os.environ, "LOOPX_REGISTRY": str(registry)}
    result = subprocess.run(["sh", "-c", script.replace("<current_time_iso>", "requirements-default-wake")],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    full = json.loads(result.stdout)
    assert full["should_run"] is True, full.get("reason")
    channel = full["interaction_contract"]["agent_channel"]
    assert [read["source"] for read in channel["required_reads"]] == ["goal_state"]
    goal_read = channel["required_reads"][0]
    assert "required_reads" not in full["interaction_contract"]["cli_channel"]
    context = channel["work_context"]
    assert context["complete"] and not context.get("failures", [])
    reads = context["sources"]
    assert [read["source"] for read in reads] == ["selected_todo"]
    assert not any(read["source"] == "goal_state" for read in reads)
    goal_result = subprocess.run(shlex.split(goal_read["command"]), capture_output=True, text=True, check=True)
    assert goal_requirements in goal_result.stdout
    assert "Stop before an unauthorized deployment." in goal_result.stdout
    assert full["selected_todo"]["todo_id"] == "todo_cursor_work"
    assert full["selected_todo"]["text"] != expected  # Deliberately bounded hot view.
    assert "_context_text_sha256" not in full["selected_todo"]
    assert reads[-1]["content"]["todo"]["text"] == expected
    assert json.dumps(context).count(expected) == 1
    assert envelope["required_reads"] == channel["required_reads"]
    assert envelope["work_context"] == context
    selected = envelope["action"]["selected_todo"]
    assert selected["todo_id"] == "todo_cursor_work"
    read = reads[-1]
    tokens = shlex.split(read["command"])
    assert tokens[tokens.index("--registry") + 1] == str(registry)
    assert tokens[tokens.index("--runtime-root") + 1] == str(runtime)
    assert tokens[tokens.index("--todo-id") + 1] == "todo_cursor_work"

    def read_detail() -> dict:
        result = subprocess.run([sys.executable, "-m", "loopx.cli", *tokens[1:]],
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    detail = read_detail()
    assert detail["ok"] and detail["matched"] and detail["todo_count"] == 1
    assert detail["todo"]["text"] == expected
    assert not {"todos", "agent_todos", "user_todos"}.intersection(detail)
    assert json.dumps(detail).count(expected) == 1
    assert detail["todo"]["claimed_by"] == "agent-a"
    assert envelope["action_signature"]["matches"] is True
    assert state.read_bytes() == before

    plan = build_loopx_turn_plan(envelope, host="codex-cli", execution_mode="isolated-headless",
        turn_instance_id="requirements-synthetic-turn")
    request = build_loopx_turn_host_request(plan)
    assert read["command"] in _prompt(request)
    assert "work_context" in _prompt(request)
    authority = extract_turn_authority(request)
    assert authority["selected_todo"]["todo_id"] == "todo_cursor_work"
    assert authority["required_reads"] == channel["required_reads"]
    assert authority["work_context"] == context
    assert read["command"] in render_prompt(authority)

    # A fresh CLI process must recover current requirements from the same owner,
    # including a changed last condition, rather than reusing a cached summary.
    replacement = expected.replace("retain cancellation", "retain cancellation and timeout behavior")
    code, update = run_json_cli_result("todo", "update", "--goal-id", "requirements-goal",
        "--todo-id", "todo_cursor_work", "--agent-id", "agent-a", "--text", replacement,
        registry_path=registry, runtime_root=runtime)
    assert code == 0, json.dumps(update)
    assert read_detail()["todo"]["text"] == replacement
    fresh = guard()
    assert fresh["selected_todo"]["todo_id"] == "todo_cursor_work"
    fresh_reads = fresh["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    assert fresh_reads[-1]["content"]["todo"]["text"] == replacement
    missing_tokens = ["todo_missing" if token == "todo_cursor_work" else token for token in tokens]
    missing = subprocess.run([sys.executable, "-m", "loopx.cli", *missing_tokens[1:]],
        capture_output=True, text=True, check=True)
    assert json.loads(missing.stdout)["not_found"] is True
    # Source reads are fresh, not snapshots of the earlier guard. Failure is
    # visible to the host and must not be mistaken for successful consumption.
    state.write_text(state.read_text().replace("Do not omit records in any query mode.",
        "Do not omit records in any query mode, including archived queries."))
    current_goal = subprocess.run(shlex.split(goal_read["command"]), capture_output=True, text=True, check=True)
    assert "including archived queries." in current_goal.stdout
    saved = state.with_suffix(".saved")
    state.rename(saved)
    try:
        failed = subprocess.run(shlex.split(goal_read["command"]), capture_output=True, text=True)
        assert failed.returncode != 0 and not failed.stdout
    finally:
        saved.rename(state)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_selected_todo_body_change_between_selection_and_readback_blocks_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    """The exact selected-Todo read must reject a body changed after selection."""
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    original_text = "[P1] Preserve the selected query contract and its cancellation behavior."
    changed_text = original_text + " Do not run the stale selection."
    state.write_text(
        "---\nstatus: active\n---\n# Goal\n## Objective\nMaintain a query service.\n"
        "## Agent Todo\n- [ ] " + original_text + "\n"
        "  <!-- loopx:todo todo_id=todo_selected_work status=open task_class=advancement_task claimed_by=agent-a -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="selected-race-goal", domain="requirements",
        adapter_kind="generic_project_goal_v0", state_file=str(state),
        registered_agents=["agent-a"], quota_allowed_slots=None,
    )
    goal = json.loads(registry.read_text())["goals"][0]
    fields, _archived, _sections = parse_todo_source(state.read_text(), goal=goal, state_path=state)
    full_items = retained_todo_summary_fields(fields["agent"], rollout_events=[])["agent_todos"]["items"]
    initialize_canonical_authority(
        runtime, "selected-race-goal",
        build_todo_runtime_shadow_projection(
            goal_id="selected-race-goal", todos=full_items, handoff_mode="soft_claim",
        ),
        state_path=state, provider=provider,
    )
    from loopx.control_plane.work_items import context_readback

    read_source = context_readback._source_content
    run_effect = context_readback.effect_runtime_result
    projected_request = {}
    changed = False

    def mutate_after_selection(read, **kwargs):
        nonlocal changed
        if read.get("source") == "selected_todo" and not changed:
            update_code, update = run_json_cli_result(
                "todo", "update", "--goal-id", "selected-race-goal",
                "--todo-id", "todo_selected_work", "--agent-id", "agent-a",
                "--text", changed_text, registry_path=registry, runtime_root=runtime,
            )
            assert update_code == 0, update
            changed = True
        return read_source(read, **kwargs)

    def capture_projection(effect, request, **kwargs):
        if effect == "work_item.context.project":
            projected_request.update(request)
        return run_effect(effect, request, **kwargs)

    monkeypatch.setattr(context_readback, "_source_content", mutate_after_selection)
    monkeypatch.setattr(context_readback, "effect_runtime_result", capture_projection)
    from loopx.cli import build_parser
    from loopx.cli_commands.quota import handle_quota_command
    from loopx.cli_rollout import append_cli_rollout_event

    args = build_parser().parse_args([
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "quota", "should-run", "--goal-id", "selected-race-goal", "--agent-id", "agent-a",
        "--scan-path", str(tmp_path),
    ])
    packet: dict[str, object] = {}

    def capture_payload(payload, *_args):
        packet.update(payload)

    code = handle_quota_command(
        args, registry_path=registry, runtime_root_arg=str(runtime),
        print_payload=capture_payload, append_cli_rollout_event=append_cli_rollout_event,
    )
    assert code == 0, json.dumps(packet, indent=2)
    assert changed
    assert packet["selected_todo"]["todo_id"] == "todo_selected_work"
    selected_revision = projected_request["selected_todo"]["content_revision"]
    readback = next(
        result for result in projected_request["source_results"]
        if result.get("content", {}).get("todo", {}).get("todo_id") == "todo_selected_work"
    )
    assert selected_revision
    assert readback["content"]["todo"]["content_revision"] != selected_revision
    channel = packet["interaction_contract"]["agent_channel"]
    assert channel["work_context"]["complete"] is False
    assert channel["delivery_allowed"] is False

@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_short_selected_todo_deduplicates_body_but_keeps_canonical_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    text = "Keep the release note aligned with the verified behavior."
    note = "Stop before publication until the evidence is approved."
    state.write_text(
        "---\nstatus: active\n---\n# Goal\n## Objective\nVerify the release note.\n"
        "## Agent Todo\n- [ ] [P1] " + text + "\n"
        "  <!-- loopx:todo todo_id=todo_release_note status=open task_class=advancement_task claimed_by=agent-a -->\n"
    )
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="requirements-goal", domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a"], quota_allowed_slots=None)
    goal = json.loads(registry.read_text())["goals"][0]
    fields, _archived, _sections = parse_todo_source(state.read_text(), goal=goal, state_path=state)
    todos = retained_todo_summary_fields(fields["agent"], rollout_events=[])["agent_todos"]["items"]
    target = next(item for item in todos if item.get("todo_id") == "todo_release_note")
    target["note"] = note
    snapshot = build_todo_runtime_shadow_projection(
        goal_id="requirements-goal", todos=todos, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "requirements-goal", snapshot,
        state_path=state, provider=provider)

    code, generated = run_json_cli_result("heartbeat-prompt", "--goal-id", "requirements-goal",
        "--agent-id", "agent-a", "--codex-app", "--cli-bin", str(Path(sys.executable).parent / "loopx"),
        registry_path=registry, runtime_root=runtime)
    assert code == 0 and generated["ok"], generated
    script = re.search(r"```sh\n(.*?)\n```", generated["task_body"], re.S).group(1)
    env = {**os.environ, "LOOPX_REGISTRY": str(registry)}
    result = subprocess.run(["sh", "-c", script.replace("<current_time_iso>", "short-note-wake")],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    full = json.loads(result.stdout)
    channel = full["interaction_contract"]["agent_channel"]
    context = channel["work_context"]
    assert context["complete"] is True
    assert context["selected_todo_ref"] == "selected_todo"
    assert [read["source"] for read in channel["required_reads"]] == ["goal_state"]
    selected_source = next(source for source in context["sources"] if source["source"] == "selected_todo")
    source_todo = selected_source["content"]["todo"]
    assert source_todo.get("text") is None
    assert source_todo["note"] == note
    assert selected_source["content"]["authority_read"]["provider_revision"]
    assert json.dumps(context).count(note) == 1

    code, turn = run_json_cli_result("quota", "should-run", "--goal-id", "requirements-goal",
        "--agent-id", "agent-a", "--scan-path", str(tmp_path), "--turn-envelope",
        registry_path=registry, runtime_root=runtime)
    assert code == 0 and turn["ok"], turn
    turn_context = turn["work_context"]
    assert turn_context["complete"] is True
    assert turn_context["selected_todo_ref"] == "selected_todo"
    turn_source = next(source for source in turn_context["sources"] if source["source"] == "selected_todo")
    assert turn_source["content"]["todo"]["note"] == note
    assert turn_source["content"]["authority_read"]["provider_revision"]
    assert json.dumps(turn_context).count(note) == 1
