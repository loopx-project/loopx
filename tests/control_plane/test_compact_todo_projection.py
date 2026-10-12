"""The three public read projections share codecs, not field visibility policy."""

from copy import deepcopy

import pytest

from loopx.control_plane.todos.resume_planning import project_todo_resume_planning
from loopx.control_plane.todos.quota_selection import project_quota_planning
from loopx.control_plane.todos.succession_warning import build_todo_succession_warning_lanes


def projected(item: dict) -> list[dict]:
    return [
        project_todo_resume_planning({"deferred_items": [item]})["deferred_items"][0],
        project_quota_planning({"route_continuation_candidates": [item]},
            all_open_items=[], source_open_count=0, agent_identity=None,
            filter_user_gate_blocks_agent=False, available_capabilities=[])
        ["route_lanes"]["route_continuation_replan_candidates"][0],
        build_todo_succession_warning_lanes(
            {"completed_without_successor_items": [item]}, item_limit=5,
        )["completed_without_successor_items"][0],
    ]


@pytest.mark.parametrize("scope", [None, "", [], ["../invalid"]])
def test_empty_scope_omission_and_domain_extension_visibility(scope) -> None:
    item = {
        "todo_id": "todo_example", "index": 3, "text": "  Build change  ",
        "status": "deferred", "required_write_scopes": scope,
        "decision_scope": None, "required_decision_scopes": [],
        "resume_ready": False, "no_followup": False, "claimed_by": "agent-a",
        "completion_continuation": {}, "completion_recovery": "", "done": False,
        "completion_turn_key": "turn-a", "succession_tracked": False,
        "recommended_action": "Review successor", "unregistered_field": "must not leak",
    }
    before = deepcopy(item)
    resume, route, succession = projected(item)
    for result in (resume, route, succession):
        for omitted in ("required_write_scopes", "decision_scope", "required_decision_scopes",
                        "unregistered_field"):
            assert omitted not in result
        assert result["claimed_by"] == "agent-a"
        assert result["resume_ready"] is False
        assert result["no_followup"] is False
    for key in ("completion_continuation", "completion_recovery", "completion_turn_key",
                "done", "succession_tracked", "recommended_action"):
        assert key not in resume and key not in route
        assert succession[key] == item[key]
    assert resume["text"] == route["text"] == "Build change"
    assert succession["text"] == item["text"]
    assert item == before


def test_scope_normalization_is_shared_without_normalizing_unowned_fields() -> None:
    for result in projected({
        "text": "Build", "status": "deferred", "required_write_scopes": "src/**,src/**;tests/**",
        "required_capabilities": "compiler", "note": "not in this display contract",
    }):
        assert result["required_write_scopes"] == ["src/**", "tests/**"]
        assert result["required_capabilities"] == "compiler"
        assert "note" not in result


def test_legacy_task_class_text_inputs_remain_consumer_owned() -> None:
    resume, route, succession = projected({
        "text": "Build change", "title": "Do not execute until approved",
        "status": "deferred",
    })
    assert resume["task_class"] == route["task_class"] == "continuous_monitor"
    assert succession["task_class"] == "advancement_task"
