"""Read compatibility at the live Goal-frontier import boundary."""

import pytest
import json
import subprocess
import sys

from loopx.control_plane.todos.todo_semantics import (
    agent_scoped_selectable_advancement_todo_ids,
    todo_advancement_frontier_counts,
    todo_advancement_frontier_items,
)


def item(identity, **fields):
    return {"todo_id": identity, "status": "open", "task_class": "advancement_task", **fields}


@pytest.mark.parametrize("agent", ["worker-a", None])
def test_executable_slot_wins_and_peer_visibility_never_grants_selection(agent):
    source = [item("todo_own", claimed_by="worker-a"), item("todo_free"),
              item("todo_peer", claimed_by="worker-b"),
              item("todo_excluded", excluded_agents=["worker-a"]),
              item("todo_closed", status="done"),
              item("todo_monitor", task_class="continuous_monitor"),
              item("todo_blocked", goal_acceptance_guard={"allowed": False})]
    summary = {"executable_backlog_items": source,
               "unclaimed_priority_open_items": [item("todo_stale")],
               "current_agent_claimed_advancement_count": 9,
               "claim_scope": {"other_agent_claimed_items": [item("todo_peer_1"), item("todo_peer_2")]}}
    projected = todo_advancement_frontier_items(summary, agent_id=agent)
    assert projected["current_agent_claimed_items"] == ([source[0]] if agent else [source[0], source[2]])
    assert projected["unclaimed_items"] == ([source[1]] if agent else [source[1], source[3]])
    assert projected["other_agent_claimed_items"] == ([source[2]] if agent else [])
    assert agent_scoped_selectable_advancement_todo_ids(summary, agent_id=agent) == (
        {"todo_own", "todo_free"} if agent else {"todo_own", "todo_free", "todo_peer", "todo_excluded"})
    assert todo_advancement_frontier_counts(summary, agent_id=agent) == {
        "current_agent_claimed_advancement_count": 9,
        "unclaimed_advancement_count": 1 if agent else 2,
        "other_agent_claimed_advancement_count": 2,
    }


def test_empty_executable_slot_is_authoritative_and_missing_slot_uses_legacy_views():
    summary = {"unclaimed_priority_open_items": [item("todo_free"), item("todo_excluded", excluded_agents=["worker-a"])],
               "claimed_advancement_open_items": [item("todo_own", claimed_by="worker-a"),
                    item("todo_excluded_own", claimed_by="worker-a", excluded_agents=["worker-a"]),
                    item("todo_peer", claimed_by="worker-b")]}
    assert agent_scoped_selectable_advancement_todo_ids(summary, agent_id="worker-a") == {"todo_free", "todo_own"}
    assert todo_advancement_frontier_items(summary, agent_id="worker-a")["other_agent_claimed_items"] == [summary["claimed_advancement_open_items"][2]]
    assert agent_scoped_selectable_advancement_todo_ids({**summary, "executable_backlog_items": []}, agent_id="worker-a") == set()
    assert todo_advancement_frontier_counts(None, agent_id="worker-a") == {
        "current_agent_claimed_advancement_count": 0, "unclaimed_advancement_count": 0,
        "other_agent_claimed_advancement_count": 0,
    }


def test_resume_readiness_is_an_observation_not_a_raw_condition_guess():
    source = [item("todo_closed", status="done"),
              item("todo_wait", resume_when="todo_done:todo_prior", resume_ready=False),
              item("todo_ready", resume_when="todo_done:todo_prior", resume_ready=True)]
    assert agent_scoped_selectable_advancement_todo_ids({"executable_backlog_items": source}, agent_id="worker-a") == {"todo_ready"}


@pytest.mark.parametrize("floor, expected", [(0, 0), ("12", 12), (9007199254740993, 9007199254740993)])
def test_diagnostic_floor_preserves_python_integer_precision_without_selecting_work(floor, expected):
    summary = {"executable_backlog_items": [], "current_agent_claimed_advancement_count": floor}
    assert todo_advancement_frontier_counts(summary, agent_id="worker-a")["current_agent_claimed_advancement_count"] == expected
    assert agent_scoped_selectable_advancement_todo_ids(summary, agent_id="worker-a") == set()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_quota_frontier_reads_canonical_claims(tmp_path, monkeypatch, provider):
    from canonical_authority_fixture import promoted_create_fixture, isolate_sqlite_runtime
    from loopx.todos import add_goal_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    configuration = json.loads(registry.read_text())
    configuration["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    configuration["goals"][0].update({"domain": "control-plane-read-path", "status": "active",
        "adapter": {"kind": "smoke_v0", "status": "connected-read-only"}})
    registry.write_text(json.dumps(configuration))
    for name, fields in [
        ("Own direction", {"claimed_by": "agent-a"}),
        ("Free direction", {}),
        ("Peer direction", {"claimed_by": "agent-b"}),
        ("Excluded direction", {"excluded_agents": ["agent-a"]}),
        ("Deferred direction", {"status": "deferred", "resume_when": "resume_at:2030-01-01T00:00:00Z"}),
    ]:
        created = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
            text=name, task_class="advancement_task", action_kind="implement",
            operation_id=name.lower().replace(" ", "-"), **fields)
        assert created["source_authority"] == f"{provider}_v0"
    # Keep the permanent narrative projection: canonical authority does not
    # retire document IO, and status still reads the Goal's narrative state.
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--format", "json", "quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
        "--runtime-profile", "generic_cli"],
        text=True, capture_output=True, timeout=60)
    packet = json.loads(result.stdout)
    assert result.returncode == 0, (packet.get("error"), packet.get("reason"), packet.get("status_health_ok"))
    assert packet["goal_frontier_projection"]["remaining_advancement_frontier"] == {
        "current_agent_claimed_advancement_count": 1, "unclaimed_advancement_count": 1,
        "other_agent_claimed_advancement_count": 1,
    }
    assert packet["selected_todo"]["text"] == "Own direction"
    assert state.exists()


def _frontier_context(summary, *, agent="worker-a", receipt_bound=None):
    from loopx.control_plane.goals.goal_frontier import build_goal_frontier_projection_context_from_status

    return build_goal_frontier_projection_context_from_status(
        goal_id="goal-a", agent_id=agent,
        status_payload={"run_history": {"goals": []}}, item={}, project_asset=None,
        user_todo_summary={"open_count": 0}, agent_todo_summary=summary,
        work_lane_contract={"lane": "advancement_task", "must_attempt_work": True},
        neutral_replan_ack_classifications=set(),
        receipt_bound_replan_obligation_id=receipt_bound,
    )


@pytest.mark.parametrize("agent, claimed, free, peer", [
    ("worker-a", 1, 1, 1), ("worker-b", 1, 2, 1), (None, 2, 2, 0),
])
def test_context_keeps_claim_scope_and_observes_next_source(agent, claimed, free, peer):
    from copy import deepcopy

    summary = {"executable_backlog_items": [
        item("todo_own", claimed_by="worker-a"), item("todo_free"),
        item("todo_peer", claimed_by="worker-b"),
        item("todo_excluded", excluded_agents=["worker-a"]),
    ]}
    before = deepcopy(summary)
    context = _frontier_context(summary, agent=agent)
    assert context["goal_frontier_projection"]["remaining_advancement_frontier"] == {
        "current_agent_claimed_advancement_count": claimed,
        "unclaimed_advancement_count": free,
        "other_agent_claimed_advancement_count": peer,
    }
    assert context["replan_obligation"] is None
    assert summary == before
    # Reusing this exact Python object on a later call cannot reuse its old view.
    summary["executable_backlog_items"] = []
    summary["current_agent_claimed_advancement_count"] = 9007199254740993
    refreshed = _frontier_context(summary, agent=agent)
    assert refreshed["goal_frontier_projection"]["remaining_advancement_frontier"] == {
        "current_agent_claimed_advancement_count": 9007199254740993,
        "unclaimed_advancement_count": 0,
        "other_agent_claimed_advancement_count": 0,
    }


def test_standalone_frontier_helpers_read_each_current_summary(monkeypatch):
    from loopx.control_plane.goals import goal_frontier

    original = goal_frontier._frontier_advancement_counts
    reads = []

    def observe(**kwargs):
        reads.append(kwargs["agent_todo_summary"])
        return original(**kwargs)

    monkeypatch.setattr(goal_frontier, "_frontier_advancement_counts", observe)
    for summary in (None, {}, {"executable_backlog_items": []},
                    {"executable_backlog_items": [item("todo_free")]}):
        common = dict(user_todo_summary=None, agent_todo_summary=summary,
                      work_lane_contract=None, agent_id="worker-a")
        goal_frontier.derive_goal_frontier_replan_obligation_from_summaries(
            **common, existing_replan_obligation=None)
        projection = goal_frontier.build_goal_frontier_projection_from_summaries(
            **common, goal_id="goal-a", replan_obligation=None)
        expected = 1 if summary and summary.get("executable_backlog_items") else 0
        assert projection["remaining_advancement_frontier"]["unclaimed_advancement_count"] == expected
        assert reads[-2:] == [summary, summary]
    assert len(reads) == 8


@pytest.mark.parametrize("receipt_bound", [None, "prior-obligation"])
def test_one_context_classifies_its_fresh_counts_once(monkeypatch, receipt_bound):
    from loopx.control_plane.goals import goal_frontier

    original = goal_frontier._frontier_advancement_counts
    reads = []

    def observe(**kwargs):
        reads.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(goal_frontier, "_frontier_advancement_counts", observe)
    summary = {"executable_backlog_items": [item("todo_free")]}
    context = _frontier_context(summary, receipt_bound=receipt_bound)
    assert context["replan_obligation"] is None
    assert context["goal_frontier_projection"]["remaining_advancement_frontier"]["unclaimed_advancement_count"] == 1
    assert reads == [{"agent_todo_summary": summary, "agent_id": "worker-a"}]
