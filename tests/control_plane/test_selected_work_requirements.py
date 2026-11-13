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
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.todos.goal_todo_projection import retained_todo_summary_fields
from loopx.control_plane.turn_driver.codex_cli import _prompt
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt
from tests.control_plane.cli_output_fixture import read_cli_context_for_oracle
from tests.control_plane.host_process_fixture import assert_host_task_context

REPO = Path(__file__).resolve().parents[2]


def test_selected_todo_refuses_unmigrated_source(tmp_path: Path) -> None:
    state = tmp_path / "state.md"
    state.write_text(
        "---\nstatus: active\n---\n# Goal\n## Agent Todo\n"
        "- [ ] Keep the selected task current.\n"
        "  <!-- loopx:todo todo_id=todo_current status=open task_class=advancement_task -->\n"
    )

    original = state.read_bytes()
    result = effect_runtime_result("work_item.context.project", {
        "required_reads": [{"source": "selected_todo", "command": "read-selected"}],
        "source_results": [], "selected_todo": {"todo_id": "todo_current"},
        "source_context": {"runtime_root": str(tmp_path / "runtime"), "goal_id": "goal-current"},
    })
    context = result["work_context"]
    assert context["complete"] is False
    assert context["failures"][0]["error_code"] == "context_source_unavailable"
    assert state.read_bytes() == original


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

    def guard(*extra, expected_exit=0):
        code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "requirements-goal",
            "--agent-id", "agent-a", "--context-delivery", "off", *extra,
            registry_path=registry, runtime_root=runtime)
        assert code == expected_exit, json.dumps(packet, indent=2)
        return json.loads(read_cli_context_for_oracle(json.dumps(packet))[0])

    if provider == "legacy":
        refused = guard(expected_exit=1)
        assert refused["error_code"] == "canonical_authority_required"
        assert refused["decision_read_from_provider"] is False
        assert refused["should_run"] is False
        assert state.read_text() == state_text
        return

    # The old implementation performs inventory plus one whole-source read per
    # scoped User Todo. The optimized path must return identical exact bodies
    # from one read, without changing scope or canonical authority.
    from loopx.control_plane.work_items import context_readback
    original_read = context_readback.list_goal_todos
    expected_users = [original_read(registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="requirements-goal", role="user", status="open", agent_id="agent-a",
        todo_id=todo)["todo"] for todo in ("todo_gate", "todo_user_action", "todo_global_action")]
    bounded = original_read(registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="requirements-goal", role="user", status="open", agent_id="agent-a")
    assert bounded["todos"][0]["text"] != gate_text, "ordinary inventories remain bounded"
    reads = []

    def counted_read(**options):
        reads.append(options)
        return original_read(**options)

    monkeypatch.setattr(context_readback, "list_goal_todos", counted_read)
    direct = context_readback._user_context(registry_path=registry, runtime_root=runtime,
        goal_id="requirements-goal", agent_id="agent-a")
    assert direct["todos"] == expected_users
    assert len(reads) == 1, "scoped full User bodies need one canonical query, not N+1"
    assert direct["authority_read"]["decision_read_from_provider"] is True
    from loopx.control_plane.todos import goal_todo_projection
    typed_read = goal_todo_projection.project_todo_read

    def old_delivery(options):
        result = typed_read(options)
        if "payload" in result:
            result["payload"].pop("full_text", None)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(goal_todo_projection, "project_todo_read", old_delivery)
        with pytest.raises(ValueError, match="unsupported by this runtime"):
            context_readback._user_context(registry_path=registry, runtime_root=runtime,
                goal_id="requirements-goal", agent_id="agent-a")

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
    envelope = guard("--complete-turn")
    assert envelope["interaction_contract"]["agent_channel"]["work_context"]["user_todos"] == channel["work_context"]["user_todos"]
    assert state.read_text() == state_text
    # Canonical tasks still exist: the full Goal source must fail closed,
    # rather than silently substituting a Todo summary for missing intent.
    state.unlink()
    failed = guard(expected_exit=1)
    assert failed["ok"] is False and failed["status_health_ok"] is False
    failed_channel = failed["interaction_contract"]["agent_channel"]
    assert failed_channel["delivery_allowed"] is False
    assert failed_channel["work_context"]["complete"] is False
    assert any(read["source"] == "goal_state" for read in failed_channel["required_reads"])
    state.write_text(state_text)
    assert guard()["interaction_contract"]["agent_channel"]["work_context"]["complete"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
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
    goal = json.loads(registry.read_text())["goals"][0]
    fields, _archived, _sections = parse_todo_source(
        state.read_text(), goal=goal, state_path=state,
    )
    full_items = retained_todo_summary_fields(
        fields["agent"], rollout_events=[],
    )["agent_todos"]["items"]
    snapshot = build_todo_runtime_shadow_projection(
        goal_id="requirements-goal", todos=full_items, handoff_mode="soft_claim",
    )
    initialize_canonical_authority(
        runtime, "requirements-goal", snapshot, state_path=state, provider=provider,
    )
    state.write_text(
        goal_requirements + "## Agent Todo\n- [ ] Stale display: do only an easier check.\n",
    )
    cli_bin = Path(sys.executable).parent / "loopx"

    def guard(*extra: str) -> dict:
        code, packet = run_json_cli_result("--cli-bin", str(cli_bin),
            "quota", "should-run", "--goal-id", "requirements-goal",
            "--agent-id", "agent-a", "--context-delivery", "off", *extra,
            registry_path=registry, runtime_root=runtime)
        assert code == 0, json.dumps(packet, indent=2)
        packet = json.loads(read_cli_context_for_oracle(json.dumps(packet))[0])
        assert packet["should_run"] is True, packet.get("reason")
        # The harness adds transport metadata outside the signed CLI stdout.
        packet.pop("_returncode", None)
        return packet

    envelope = guard("--complete-turn")
    before = state.read_bytes()
    # Run the actual default product prompt, including its native capture
    # transport. Read only this invocation's complete current source.
    code, generated = run_json_cli_result("heartbeat-prompt", "--goal-id", "requirements-goal",
        "--agent-id", "agent-a", "--codex-app", "--cli-bin", str(cli_bin),
        "--context-delivery", "auto",
        registry_path=registry, runtime_root=runtime)
    assert code == 0 and generated["ok"], generated
    body = generated["task_body"]
    assert "work_context" in body
    script = re.search(r"```sh\n(.*?)\n```", body, re.S).group(1)
    assert "--turn-envelope" not in script and "--complete-turn" not in script
    assert "--context-delivery on" not in script and "--decision-output-root" in script
    env = {
        **os.environ,
        "LOOPX_REGISTRY": str(registry),
        "PYTHONPATH": str(REPO),
    }
    result = subprocess.run(["sh", "-c", script.replace("<current_time_iso>", "requirements-default-wake")],
        cwd=REPO, env=env, capture_output=True, text=True, check=True)
    full = json.loads(read_cli_context_for_oracle(result.stdout)[0])
    assert full["should_run"] is True, full.get("reason")
    channel = full["interaction_contract"]["agent_channel"]
    assert channel["required_reads"] == []
    assert "required_reads" not in full["interaction_contract"]["cli_channel"]
    context = channel["work_context"]
    assert context["complete"] and not context.get("failures", [])
    assert "progress/change, current blockers, next work, acceptance and stops" in context["instruction"]
    assert "unknown, not unchanged" in context["instruction"]
    assert "summaries cannot replace full requirements" in context["instruction"]
    reads = context["sources"]
    assert [read["source"] for read in reads] == ["goal_state", "selected_todo"]
    goal_read = reads[0]
    assert goal_read["content"]["full_source"] is True
    assert goal_read["content"]["text"] == before.decode()
    assert goal_requirements in goal_read["content"]["text"]
    assert "Stop before an unauthorized deployment." in goal_read["content"]["text"]
    assert full["selected_todo"]["todo_id"] == "todo_cursor_work"
    assert full["selected_todo"]["text"] != expected  # Deliberately bounded hot view.
    assert "_context_text_sha256" not in full["selected_todo"]
    assert reads[-1]["content"]["todo"]["text"] == expected
    assert json.dumps(context).count(expected) == 1
    envelope_channel = envelope["interaction_contract"]["agent_channel"]
    # Ordinary delivery-off quota retains the original progressive Goal read;
    # native default has fulfilled that same source before capture.
    assert [row["source"] for row in envelope_channel["required_reads"]] == ["goal_state"]
    assert envelope_channel["required_reads"][0]["command"] == goal_read["command"]
    assert context["selected_todo_text_verified"] is True
    assert envelope_channel["work_context"] == {
        **context, "sources": [row for row in reads if row["source"] != "goal_state"],
    }
    assert envelope_channel["work_context"]["selected_todo_text_verified"] is True
    selected = envelope["selected_todo"]
    assert selected["todo_id"] == "todo_cursor_work"
    read = reads[-1]
    tokens = shlex.split(read["command"])
    assert tokens[:3] == [str(cli_bin), "--cli-bin", str(cli_bin)]
    assert tokens[tokens.index("--registry") + 1] == str(registry)
    assert tokens[tokens.index("--runtime-root") + 1] == str(runtime)
    assert tokens[tokens.index("--todo-id") + 1] == "todo_cursor_work"

    def read_detail() -> dict:
        result = subprocess.run(tokens, cwd=REPO, env=env,
            capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    detail = read_detail()
    assert detail["ok"] and detail["matched"] and detail["todo_count"] == 1
    assert detail["todo"]["text"] == expected
    assert not {"todos", "agent_todos", "user_todos"}.intersection(detail)
    assert json.dumps(detail).count(expected) == 1
    assert detail["todo"]["claimed_by"] == "agent-a"
    assert state.read_bytes() == before

    def native_plan(turn):
        # Use the production native entrypoint and its own host binding rather
        # than silently rebinding an App heartbeat capture to a Codex executor.
        code, plan = run_json_cli_result("--cli-bin", str(cli_bin), "turn", "plan",
            "--goal-id", "requirements-goal", "--agent-id", "agent-a",
            "--host", "codex-cli", "--execution-mode", "isolated-headless",
            "--turn-instance-id", turn, "--include-transaction-detail",
            registry_path=registry, runtime_root=runtime)
        assert code == 0 and plan["ok"], plan
        assert plan["route"]["would_invoke_host"] is True
        assert plan["route"]["host_invocation_allowed"] is False
        return plan

    plan = native_plan("requirements-synthetic-turn")
    request = build_loopx_turn_host_request(plan)
    assert read["command"] in _prompt(request)
    assert "work_context" in _prompt(request)
    model_input = json.loads(_prompt(request).split("Turn request:\n", 1)[1])
    assert request["work_contract"] == plan["work_contract"]
    assert_host_task_context(model_input, plan["work_contract"])
    assert expected in _prompt(request)
    authority = extract_turn_authority(request)
    assert authority["selected_todo"]["todo_id"] == "todo_cursor_work"
    authority_channel = authority["interaction_contract"]["agent_channel"]
    assert authority_channel["required_reads"] == []
    native_context = authority_channel["work_context"]
    delivered_goal = [row for row in native_context["sources"] if row["source"] == "goal_state"]
    assert len(delivered_goal) == 1 and delivered_goal[0]["command"] == goal_read["command"]
    assert delivered_goal[0]["content"]["full_source"] is True
    assert delivered_goal[0]["content"]["text"] == before.decode()
    assert json.dumps(before.decode(), ensure_ascii=False) in render_prompt(authority)
    assert {key: value for key, value in native_context.items() if key != "sources"} == {
        key: value for key, value in context.items() if key != "sources"
    }
    assert [row for row in native_context["sources"] if row["source"] != "goal_state"] == [
        row for row in context["sources"] if row["source"] != "goal_state"
    ]
    assert native_context["selected_todo_text_verified"] is True
    assert read["command"] in render_prompt(authority)
    # Keep the old layout's independent requirement assertions while the
    # complete source uses its canonical channel instead of duplicate aliases.
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    historical = extract_turn_authority({"turn_envelope": build_turn_envelope(envelope)})
    assert historical["required_reads"] == envelope_channel["required_reads"]
    assert historical["work_context"] == {
        key: value for key, value in envelope_channel["work_context"].items()
        if key != "selected_todo_text_verified"
    }
    assert historical["selected_todo"]["text_source_verified"] is True
    assert read["command"] in render_prompt(historical)

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
    fresh_envelope = guard("--complete-turn")
    assert fresh_envelope["interaction_contract"]["agent_channel"]["work_context"]["sources"][-1]["content"]["todo"]["text"] == replacement
    fresh_plan = native_plan("requirements-next-turn")
    fresh_prompt = _prompt(build_loopx_turn_host_request(fresh_plan))
    assert replacement in fresh_prompt
    assert "unknown, not unchanged" in fresh_prompt
    # The old observation remains an observation, not a rewritten current brief.
    assert reads[-1]["content"]["todo"]["text"] == expected
    missing_tokens = ["todo_missing" if token == "todo_cursor_work" else token for token in tokens]
    missing = subprocess.run(missing_tokens, cwd=REPO, env=env,
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

    run_effect = context_readback.effect_runtime_result
    projected_request = {}
    changed = False

    def capture_projection(effect, request, **kwargs):
        nonlocal changed
        if effect == "work_item.context.project" and not changed:
            update_code, update = run_json_cli_result(
                "todo", "update", "--goal-id", "selected-race-goal",
                "--todo-id", "todo_selected_work", "--agent-id", "agent-a",
                "--text", changed_text, registry_path=registry, runtime_root=runtime,
            )
            assert update_code == 0, update
            changed = True
        if effect == "work_item.context.project":
            projected_request.update(request)
        return run_effect(effect, request, **kwargs)

    monkeypatch.setattr(context_readback, "effect_runtime_result", capture_projection)
    from loopx.cli import build_parser
    from loopx.cli_commands.quota import handle_quota_command
    from loopx.cli_rollout import append_cli_rollout_event

    args = build_parser().parse_args([
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "quota", "should-run", "--context-delivery", "off",
        "--goal-id", "selected-race-goal", "--agent-id", "agent-a",
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
    packet = json.loads(read_cli_context_for_oracle(json.dumps(packet))[0])
    assert packet["selected_todo"]["todo_id"] == "todo_selected_work"
    selected_revision = projected_request["selected_todo"]["content_revision"]
    assert selected_revision
    current_code, current = run_json_cli_result(
        "todo", "list", "--goal-id", "selected-race-goal", "--todo-id", "todo_selected_work",
        registry_path=registry, runtime_root=runtime,
    )
    assert current_code == 0 and current["todo"]["text"] == changed_text, current
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
        "--context-delivery", "auto",
        registry_path=registry, runtime_root=runtime)
    assert code == 0 and generated["ok"], generated
    script = re.search(r"```sh\n(.*?)\n```", generated["task_body"], re.S).group(1)
    env = {
        **os.environ,
        "LOOPX_REGISTRY": str(registry),
        "PYTHONPATH": str(REPO),
    }
    result = subprocess.run(["sh", "-c", script.replace("<current_time_iso>", "short-note-wake")],
        cwd=REPO, env=env, capture_output=True, text=True, check=True)
    full = json.loads(read_cli_context_for_oracle(result.stdout)[0])
    channel = full["interaction_contract"]["agent_channel"]
    context = channel["work_context"]
    assert context["complete"] is True
    assert context["selected_todo_ref"] == "selected_todo"
    assert channel["required_reads"] == []
    goal_source = next(source for source in context["sources"] if source["source"] == "goal_state")
    assert goal_source["content"]["full_source"] is True
    assert goal_source["content"]["text"] == state.read_text()
    selected_source = next(source for source in context["sources"] if source["source"] == "selected_todo")
    source_todo = selected_source["content"]["todo"]
    assert source_todo.get("text") is None
    assert source_todo["note"] == note
    assert selected_source["content"]["authority_read"]["provider_revision"]
    assert json.dumps(context).count(note) == 1

    code, turn = run_json_cli_result("quota", "should-run", "--goal-id", "requirements-goal",
        "--agent-id", "agent-a", "--complete-turn", "--context-delivery", "off",
        registry_path=registry, runtime_root=runtime)
    assert code == 0 and turn["ok"], turn
    turn = json.loads(read_cli_context_for_oracle(json.dumps(turn))[0])
    turn_context = turn["interaction_contract"]["agent_channel"]["work_context"]
    assert turn_context["complete"] is True
    assert turn_context["selected_todo_ref"] == "selected_todo"
    turn_source = next(source for source in turn_context["sources"] if source["source"] == "selected_todo")
    assert turn_source["content"]["todo"]["note"] == note
    assert turn_source["content"]["authority_read"]["provider_revision"]
    assert json.dumps(turn_context).count(note) == 1
