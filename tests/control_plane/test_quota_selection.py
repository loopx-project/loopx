"""Quota scope is distinct from execution ownership; all data are synthetic."""
import pytest
import json
from copy import deepcopy
from pathlib import Path

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, empty_todo_display
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.machine_section_projection import render_canonical_todo_sections
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

from loopx.control_plane.todos.quota_summary import (
    summarize_user_todos_for_quota, summarize_project_asset_todos_for_quota,
)
from loopx.control_plane.todos.todo_summary import compact_todo_group


def summary(items):
    return {"schema_version": "todo_summary_v0", "source_section": "User Todo",
            "items": items, "first_open_items": items, "total_count": len(items),
            "open_count": len(items), "done_count": 0, "deferred_count": 0}


def item(todo_id, **fields):
    return {"todo_id": todo_id, "text": "Synthetic scoped work", "index": 1,
            "priority": "P1", "status": "open", "task_class": "user_gate", **fields}


@pytest.mark.parametrize("scope", [{"global_gate": True}, {"blocks_agent": "agent-b"}])
@pytest.mark.parametrize("exclusions", [[], ["agent-b"]])
def test_explicit_user_gate_scope_is_not_erased_by_executor_claim(scope, exclusions):
    gate = item("todo_gate", claimed_by="agent-a", excluded_agents=exclusions, **scope)
    result = summarize_user_todos_for_quota(summary([gate]),
        agent_identity={"agent_id": "agent-b"}, filter_user_gate_blocks_agent=True)
    assert [row["todo_id"] for row in result["gate_open_items"]] == ["todo_gate"]
    assert result["open_count"] == 1
    assert result["first_executable_items"] == []


def test_scope_precedence_keeps_other_lane_gate_diagnostic():
    gate = item("todo_gate", claimed_by="agent-b", blocks_agent="agent-a")
    result = summarize_user_todos_for_quota(summary([gate]),
        agent_identity={"agent_id": "agent-b"}, filter_user_gate_blocks_agent=True)
    assert result["gate_open_items"] == []
    assert result["other_agent_scoped_open_count"] == 1
    assert result["open_count"] == 0


def test_agent_execution_still_respects_claim_and_exclusion():
    rows = [item("todo_peer", task_class="advancement_task", claimed_by="agent-a"),
            item("todo_excluded", task_class="advancement_task", excluded_agents=["agent-b"]),
            item("todo_free", task_class="advancement_task"),
            item("todo_owned", task_class="advancement_task", claimed_by="agent-b")]
    result = summarize_user_todos_for_quota(summary(rows), agent_identity={"agent_id": "agent-b"})
    assert [row["todo_id"] for row in result["first_executable_items"]] == ["todo_owned", "todo_free"]
    assert result["claim_scope"]["executor_excluded_self_count"] == 1
    assert result["claim_scope"]["other_agent_claimed_open_count"] == 1


@pytest.mark.parametrize("identity_field", ["target_key", "action_kind", "title"])
def test_quota_frontier_keeps_identity_without_a_typed_todo_id(identity_field):
    row = item(None, task_class="advancement_task", resume_when="resume_at:2099-01-01T00:00:00Z",
               resume_ready=False, next_due_at="2099-01-01T00:00:00Z",
               **{identity_field: "synthetic-frontier-identity"})
    result = summarize_user_todos_for_quota(summary([row]),
        agent_identity={"agent_id": "agent-b"})
    assert result["frontier_deadline"]["identity"] == "synthetic-frontier-identity"


@pytest.mark.parametrize("entrypoint", ["summary", "planning"])
def test_quota_returned_records_do_not_alias_source_or_other_lanes(entrypoint):
    from loopx.control_plane.todos.quota_selection import project_quota_planning

    row = item("todo_wait", task_class="advancement_task", required_capabilities=["shell"],
        resume_when="todo_done:todo_dependency", resume_ready=False,
        resume_condition={"kind": "todo_done", "satisfied": False,
            "target_todo_id": "todo_dependency", "target_status": "open",
            "target_task_class": "advancement_task"})
    source = summary([row])
    before = deepcopy(source)
    if entrypoint == "summary":
        result = summarize_user_todos_for_quota(source, agent_identity={"agent_id": "agent-b"})
        output = result["resume_blocked_items"][0]
        other = result["first_open_items"][0]
    else:
        result = project_quota_planning(source, all_open_items=[row], source_open_count=1,
            agent_identity={"agent_id": "agent-b"}, filter_user_gate_blocks_agent=False,
            available_capabilities=[], current_time="2026-10-01T00:00:00Z")
        resume = result["resume_planning"]
        output = resume["resume_blocked_lanes"]["resume_blocked_items"][0]
        other = resume["blocked_successor_items"][0]
    output["required_capabilities"].append("synthetic-change")
    output["resume_condition"]["target_status"] = "done"
    assert source == before
    assert other["required_capabilities"] == ["shell"]
    assert other["resume_condition"]["target_status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_large_history_completes_public_quota_through_vision_wait(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    records = [item(f"todo_history_{index}", role="agent", source_section="Agent Todo",
        schema_version="todo_item_v0", archive_state="active", done=True, status="done",
        task_class="advancement_task", text=f"Completed synthetic work {index}. " * 60,
        index=index + 1) for index in range(2500)]
    records.append(item("todo_tail", role="agent", source_section="Agent Todo",
        schema_version="todo_item_v0", archive_state="active", done=False,
        task_class="advancement_task", claimed_by="agent-b", index=2501))
    state.write_text("---\nstatus: active\n---\n# Goal\n\n## Objective\n"
                     "Deliver a checked change.\n\n## Next Action\nComplete the remaining work.\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="goal-history", domain="quota-history", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-b"], quota_allowed_slots=None)
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-history", todos=records, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "goal-history", projection, state_path=state, provider=provider)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-history")
    narrative = state.read_bytes()
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "goal-history",
        "--agent-id", "agent-b", registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    assert packet["agent_todo_summary"]["total_count"] == 2501
    assert packet["selected_todo"]["todo_id"] == "todo_tail"
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-history") == before
    assert state.read_bytes() == narrative


@pytest.mark.parametrize("gate_scope", [False, True])
def test_large_quota_history_uses_snapshot_without_losing_scope(
    monkeypatch, gate_scope,
):
    from loopx.control_plane import effect_runtime

    observed_sizes = []
    original = effect_runtime._request_with_info

    def observe(info, **kwargs):
        if kwargs["method"] == "todo.quota_planning.project":
            observed_sizes.append(len(json.dumps(
                kwargs["params"], separators=(",", ":")
            ).encode()))
        return original(info, **kwargs)

    monkeypatch.setattr(effect_runtime, "_request_with_info", observe)
    history = [
        item(
            f"todo_history_{index}",
            status="done",
            task_class="advancement_task",
            note="Synthetic historical evidence. " * 180,
            index=index + 1,
        )
        for index in range(2500)
    ]
    tail = [
        item("todo_tail_gate", blocks_agent="agent-b", index=2501),
        item(
            "todo_peer", task_class="advancement_task",
            claimed_by="agent-a", index=2502,
        ),
        item(
            "todo_owned", task_class="advancement_task",
            claimed_by="agent-b", index=2503,
        ),
    ]
    source = summary(history + tail)
    source.update(
        done_count=len(history), open_count=len(tail), first_open_items=tail,
    )

    result = summarize_user_todos_for_quota(
        source,
        agent_identity={"agent_id": "agent-b"},
        filter_user_gate_blocks_agent=gate_scope,
    )

    # Unique historical payloads cannot be reduced below the inline wire limit
    # without dropping facts. Use the existing digest-verified local snapshot
    # transport rather than raising or truncating.
    assert observed_sizes and max(observed_sizes) > effect_runtime.MAX_REQUEST_BYTES
    assert [row["todo_id"] for row in result["gate_open_items"]] == [
        "todo_tail_gate"
    ]
    assert [row["todo_id"] for row in result["first_executable_items"]] == (
        [] if gate_scope else ["todo_owned"]
    )
    if not gate_scope:
        assert result["claim_scope"]["other_agent_claimed_open_count"] == 1


@pytest.mark.parametrize("agent", [None, "agent-b"])
def test_route_replan_visibility_counts_before_budget_without_granting_execution(agent):
    own = [item(f"todo_route_{i}", task_class="advancement_task", claimed_by="agent-b",
                index=i + 1, status="done", route_continuation_replan_required=True)
           for i in range(10)]
    free = item("todo_free", task_class="advancement_task", index=12)
    peer = item("todo_peer", task_class="advancement_task", claimed_by="agent-a", index=13)
    excluded = item("todo_excluded", task_class="advancement_task", excluded_agents=["agent-b"], priority="P2", index=14)
    value = summary([])
    value["route_continuation_replan_candidates"] = own + [free, peer, excluded]
    # Explicitly-disabled duplicates must not hide an enabled row.
    value["route_continuation_candidates"] = [
        {**free, "route_continuation_replan_required": False}, peer,
        item("todo_monitor", task_class="continuous_monitor"),
    ]
    result = summarize_user_todos_for_quota(value,
        agent_identity={"agent_id": agent} if agent else None)
    assert result["route_continuation_replan_count"] == 13
    assert len(result["route_continuation_replan_candidates"]) == 8
    assert result["open_count"] == 0 and result["first_executable_items"] == []
    if agent:
        assert result["current_agent_route_continuation_replan_count"] == 11
        assert result["unclaimed_route_continuation_replan_count"] == 2
        assert result["other_agent_route_continuation_replan_count"] == 2
        assert [row["todo_id"] for row in result["other_agent_route_continuation_replan_candidates"]] == ["todo_peer", "todo_excluded"]
    else:
        assert "current_agent_route_continuation_replan_count" not in result


@pytest.mark.parametrize("agent", [None, "agent-b"])
@pytest.mark.parametrize("asset", [False, True])
def test_handoff_visibility_addresses_excluded_review_lane_without_executing_it(agent, asset):
    states = ["blocking", "cleared_without_successor", "cleared_with_successor",
              "cleared_no_followup", "superseded", "deferred", "historical_unknown"]
    gates = [{"todo_id": f"todo_gate_{i}", "gate_state": state,
              "excluded_agents": " AGENT-B ", "claimed_by": "agent-a", "index": 20 - i}
             for i, state in enumerate(states)]
    gates += [dict(gates[1]), dict(gates[1]),
              {"todo_id": "todo_other", "gate_state": "cleared_without_successor",
               "excluded_agents": ["agent-c"], "claimed_by": "agent-b"}]
    value = {**summary([]), "handoff_gates": [None, *gates]}
    if asset:
        value.pop("items")
        value.pop("first_open_items")
    project = summarize_project_asset_todos_for_quota if asset else summarize_user_todos_for_quota
    result = project(value,
        agent_identity={"agent_id": agent} if agent else None)
    assert result["handoff_gate_count"] == 10
    assert result["handoff_gates"] == gates[:8]  # Source order and duplicates survive.
    assert result["open_count"] == 0 and result["first_executable_items"] == []
    if agent:
        assert result["current_agent_handoff_gate_count"] == 9
        assert result["current_agent_handoff_gates"] == gates[:8]
        assert result["current_agent_cleared_without_successor_handoff_count"] == 3
        assert result["current_agent_cleared_without_successor_handoff_gates"] == [gates[1]] * 3
    else:
        assert "current_agent_handoff_gate_count" not in result


@pytest.mark.parametrize("projected", [[], [None]])
def test_empty_projected_handoffs_suppress_raw_legacy_reconstruction(projected):
    raw = item("todo_handoff", task_class="advancement_task", action_kind="handoff_review",
               excluded_agents=["agent-b"], unblocks_todo_id="todo_target")
    result = summarize_user_todos_for_quota({**summary([raw]), "handoff_gates": projected},
        agent_identity={"agent_id": "agent-b"})
    assert "handoff_gate_count" not in result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_quota_route_read_keeps_claim_exclusion_and_provider_unchanged(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    records = [{"schema_version": "todo_item_v0", "todo_id": f"todo_route_{name}",
                "role": "agent", "source_section": "Agent Todo", "archive_state": "active",
                "text": "[P1] Repair stale handoff closeout", "task_class": "advancement_task",
                "action_kind": "handoff_review", "status": "open", "done": False,
                "priority": "P1", "unblocks_todo_id": "todo_target",
                "excluded_agents": ["agent-c"], **scope}
               for name, scope in [("own", {"claimed_by": "agent-b"}), ("free", {}),
                                   ("peer", {"claimed_by": "agent-a"}),
                                   ("excluded", {"excluded_agents": ["agent-b"]})]]
    state.write_text(render_canonical_todo_sections("# Goal\n\n## Agent Todo\n", records,
        provider_revision="fixture-source").markdown)
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="goal-route", domain="quota-route", adapter_kind="generic_project_goal_v0",
        state_file=str(state), extra_goal_fields={"coordination": {
            "registered_agents": ["agent-a", "agent-b", "agent-c"], "handoff_mode": "soft_claim"}})
    if provider != "legacy":
        projection = build_todo_runtime_shadow_projection(goal_id="goal-route", todos=records, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, "goal-route", projection, state_path=state, provider=provider)
        empty_todo_display(state)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-route")
    narrative = state.read_bytes() if state.exists() else None
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "goal-route", "--agent-id", "agent-b",
        "--include-detail", "agent-todos",  registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    routes = packet["agent_todo_summary"]
    assert routes["route_continuation_replan_count"] == 4
    assert routes["current_agent_route_continuation_replan_count"] == 2
    assert routes["unclaimed_route_continuation_replan_count"] == 2
    assert routes["other_agent_route_continuation_replan_count"] == 2
    assert all(row.get("claimed_by") != "agent-a" and "agent-b" not in row.get("excluded_agents", [])
               for row in routes["current_agent_route_continuation_replan_candidates"])
    assert routes["handoff_gate_count"] == 4
    assert routes["current_agent_handoff_gate_count"] == 1
    assert [row["todo_id"] for row in routes["current_agent_handoff_gates"]] == ["todo_route_excluded"]
    assert routes["current_agent_cleared_without_successor_handoff_count"] == 0
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-route") == before
    assert (state.read_bytes() if state.exists() else None) == narrative


@pytest.mark.parametrize("reverse", [False, True])
def test_quota_uses_complete_owned_commitment_count_before_display_cap(reverse):
    own = [item(f"todo_own_{i}", task_class="advancement_task", claimed_by="agent-b", done=False)
           for i in range(15)]
    peers = [item(f"todo_peer_{i}", task_class="advancement_task", claimed_by="agent-a", done=False)
             for i in range(20)]
    rows = own + peers
    source = compact_todo_group(list(reversed(rows)) if reverse else rows,
        role="agent", source_section="Agent Todo", item_limit=10)
    result = summarize_user_todos_for_quota(source, agent_identity={"agent_id": "agent-b"})
    assert result["current_agent_claimed_advancement_count"] == 15
    assert len(result["current_agent_claimed_advancement_items"]) < 15
    assert all(row["claimed_by"] == "agent-b" for row in result["first_executable_items"])


@pytest.mark.parametrize("display", ["current", "empty", "stale"])
@pytest.mark.parametrize("scope", ["global_gate=true", "blocks_agent=agent-b"])
def test_public_quota_keeps_gate_from_real_canonical_provider(tmp_path: Path, display: str, scope: str):
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    history = "\n".join(f"- [x] [P2] Completed synthetic work {i}.\n"
        f"  <!-- loopx:todo todo_id=todo_history_{i} status=done task_class=advancement_task -->" for i in range(12))
    # A goal-wide gate cannot also bind continuation through a legacy agent claim.
    claim = " claimed_by=agent-a" if scope == "blocks_agent=agent-b" else ""
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nDeliver a checked change.\n\n"
        "## Agent Todo\n" + history + "\n\n## User Todo\n"
        "- [ ] [P0] Owner approval is required.\n"
        f"  <!-- loopx:todo todo_id=todo_gate task_class=user_gate status=open{claim} {scope} -->\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="goal-scope", domain="quota-scope", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    goal = json.loads(registry.read_text())["goals"][0]
    fields = parse_active_state_todos(state.read_text(), goal=goal, item_limit=None)
    projection = build_todo_runtime_shadow_projection(goal_id="goal-scope",
        todos=fields["agent_todos"]["items"] + fields["user_todos"]["items"], handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "goal-scope", projection, state_path=state)
    if display == "empty":
        empty_todo_display(state)
    elif display == "stale":
        state.write_text("# Stale projection\n## User Todo\n- [x] Old approval.\n")
    before = state.read_bytes() if state.exists() else None
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "goal-scope",
        "--agent-id", "agent-b",  registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    assert packet["requires_user_action"] is True
    gates = packet["user_todo_summary"]["gate_open_items"]
    assert [row["todo_id"] for row in gates] == ["todo_gate"]
    assert not packet["user_todo_summary"].get("claim_scope")
    assert (state.read_bytes() if state.exists() else None) == before
