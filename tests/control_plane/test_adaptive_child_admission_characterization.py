"""Frozen public outputs from the Python admission owner before its cutover."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.agents import capability_gate
from loopx.control_plane.quota import task_orchestration_admission as admission
from loopx.control_plane.quota.task_orchestration import apply_task_orchestration_contract
from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown

FIXTURE = Path(__file__).with_name("adaptive_child_admission_characterization.json")


def lane(name, **fields):
    return {"todo_id": f"todo_{name}", "text": f"Inspect synthetic {name}",
            "status": "open", "task_class": "advancement_task", "action_kind": "inspect",
            "task_domain": "code", **fields}


def scenarios():
    base = {"agent_id": "agent-a", "agent_identity": {"registered_agents": ["agent-a"]},
            "goal_boundary": {"task_repository": "https://example.org/sample/project.git",
                              "write_scope": ["src/**", "tests/**", "docs/**"]},
            "orchestration": {"allowed_domains": ["code", "docs"]},
            "raw_agent_todo_summary": {"items": [lane("primary"), lane("child")]},
            "raw_user_todo_summary": {"items": []}, "agent_todo_source_items": None,
            "user_todo_source_items": None,
            "available_capabilities": ["subagent_spawn", "subagent_context_fork", "subagent_resume"],
            "parent_goal_id": "goal_synthetic", "max_children": 2}
    cases = {"unclaimed": deepcopy(base)}
    def add(name, items=None, **updates):
        case = deepcopy(base)
        if items is not None:
            case["raw_agent_todo_summary"] = {"items": items}
        case.update(updates)
        cases[name] = case
    add("claimed", [lane("primary", claimed_by="agent-a"), lane("child", claimed_by="agent-a"),
                    lane("other", claimed_by="agent-b")])
    add("excluded_and_ordered_reasons", [lane("primary"), lane("child"), lane("blocked",
        excluded_agents=["agent-a"], resume_when="todo_done:todo_dependency", resume_ready=False,
        task_domain="research", required_capabilities=["network"],
        task_repository="https://example.org/other/project", action_kind="implement")])
    add("user_dependency", [lane("primary"), lane("child"), lane("waiting")],
        raw_user_todo_summary={"items": [{"unblocks_todo_id": "todo_waiting", "status": "open"}]})
    add("mixed_repository", [lane("primary"), lane("child"),
        lane("other", task_repository="https://example.org/other/project"),
        lane("invalid", task_repository="not a repository")])
    add("empty_write_scope", [lane("primary"), lane("child"), lane("writer", action_kind="implement")])
    add("scope_conflicts_capacity", [lane("primary", required_write_scopes=["src/core"]),
        lane("glob", required_write_scopes=["src/core/*.py"]),
        lane("child", required_write_scopes=["tests/**"]),
        lane("docs", required_write_scopes=["docs/**"]), lane("overflow")], max_children=1)
    add("first_conflict", [lane("primary", required_write_scopes=["src/a/**"]),
        lane("child", required_write_scopes=["src/b/**"]),
        lane("both", required_write_scopes=["src/**"])])
    add("case_sensitive_scope", [lane("primary"), lane("child"),
        lane("case", required_write_scopes=["SRC/file.py"])])
    add("missing_capabilities", [lane("primary", required_capabilities=["shell"]),
        lane("child", required_capabilities=["network"], target_capabilities=["network"]),
        lane("blocked", required_capabilities=["production_access", "network"])])
    add("brief", [lane("primary"), lane("child", title="  " + "示例 😀 " * 100,
        action_kind=" inspect_detail ", continuation_policy=" bounded ", target_key=" sample ",
        validation_command_argv=[], validation_label=" synthetic check ")])
    add("acceptance_guard", [lane("primary"), lane("child"),
        lane("guarded", goal_acceptance_guard={"allowed": False})])
    add("empty_canonical", agent_todo_source_items=[])
    add("empty_user_canonical", raw_user_todo_summary={"items": [{"unblocks_todo_id": "todo_child"}]},
        user_todo_source_items=[])
    add("non_coordinator", [lane("primary", claimed_by="agent-b"), lane("child", claimed_by="agent-b")])
    add("unicode_election", [lane("示例"), lane("😀child")],
        agent_identity={"registered_agents": ["agent-a", "équipe", "代理", "agent-z"]})
    add("empty", [])
    add("mode_off")
    return cases


def observe(case, name):
    counts = Counter()
    original = effect_runtime.effect_runtime_result
    def counted(method, *args, **kwargs):
        counts[method] += 1
        return original(method, *args, **kwargs)
    if hasattr(capability_gate, "_missing"):
        capability_gate._missing.cache_clear()
    with patch.object(effect_runtime, "effect_runtime_result", counted), patch.object(admission, "effect_runtime_result", counted):
        direct = None if name == "mode_off" else admission.build_adaptive_task_orchestration_contract(**case)
        direct_counts = dict(counts)
        counts.clear()
        boundary = {**case["goal_boundary"], "orchestration": {
            **case["orchestration"], "mode": "off" if name == "mode_off" else "multi_subagent",
            "spawn_allowed": True, "max_children": case["max_children"]}}
        contract, work_lane = apply_task_orchestration_contract(
            fallback_work_lane_contract={"lane": "advancement_task"}, goal_boundary=boundary,
            agent_identity={**case["agent_identity"], "agent_id": case["agent_id"]},
            agent_todo_summary=case["raw_agent_todo_summary"],
            **{key: case[key] for key in ("raw_agent_todo_summary", "raw_user_todo_summary",
                "agent_todo_source_items", "user_todo_source_items", "available_capabilities", "parent_goal_id")})
        assert dict(counts) == ({} if name == "mode_off" else {"quota.task_orchestration.admit": 1})
    payload = {"task_orchestration_contract": contract, "work_lane_contract": work_lane}
    return {"admission_json": json.dumps(direct, ensure_ascii=True, separators=(",", ":")),
            "quota_fields_json": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            "markdown": render_quota_should_run_markdown(payload),
            "crossings": direct_counts}


@pytest.mark.parametrize("name", scenarios())
def test_complete_admission_parity(name):
    expected = json.loads(FIXTURE.read_text())[name]
    observed = observe(scenarios()[name], name)
    counts = observed.pop("crossings")
    expected.pop("crossings")  # Frozen pre-cutover instrumentation remains in the fixture.
    assert observed == expected
    assert counts == ({} if name == "mode_off" else {"quota.task_orchestration.admit": 1})
