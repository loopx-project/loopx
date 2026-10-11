"""Observable update semantics shared by the legacy lifecycle writers."""

from copy import deepcopy

import pytest

from loopx.control_plane.todos.active_state_editing import find_todo_block
from loopx.control_plane.todos.line_update import apply_todo_update_to_lines
from loopx.control_plane.todos.contract import normalize_todo_watch_only

AT = "2026-09-01T00:00:00Z"


def _lines(metadata=""):
    return [
        "## Agent Todo",
        "",
        "- [ ] Preserve this task",
        "  Detail stays unless text changes.",
        "  <!-- loopx:todo todo_id=todo_field_test status=open task_class=advancement_task "
        + metadata
        + " -->",
        "",
    ]


@pytest.mark.parametrize(
    "intent, initial, expected",
    [
        ({"clear_claim": True}, "claimed_by=agent-a", {"claimed_by": None}),
        ({"claimed_by": "agent-b"}, "claimed_by=agent-a", {"claimed_by": "agent-b"}),
        (
            {"clear_user_binding": True},
            "bound_agent=agent-a",
            {"bound_agent": None, "goal_bound": None},
        ),
        (
            {"goal_bound": True},
            "bound_agent=agent-a",
            {"bound_agent": None, "goal_bound": True},
        ),
        ({"excluded_agents": []}, "excluded_agents=agent-a", {"excluded_agents": []}),
        ({"clear_blocks_agent": True}, "blocks_agent=agent-a", {"blocks_agent": None}),
        ({"clear_global_gate": True}, "global_gate=true", {"global_gate": None}),
        (
            {"clear_resume_when": True},
            "resume_when=monitor_changed:todo_monitor resume_monitor_generation=4",
            {"resume_when": None, "resume_monitor_generation": None},
        ),
        (
            {"resume_when": "todo_done:todo_dependency"},
            "resume_monitor_generation=4",
            {
                "resume_when": "todo_done:todo_dependency",
                "resume_monitor_generation": None,
            },
        ),
        (
            {
                "resume_when": "monitor_changed:todo_monitor",
                "resume_monitor_generation": 0,
            },
            "",
            {
                "resume_when": "monitor_changed:todo_monitor",
                "resume_monitor_generation": 0,
            },
        ),
        (
            {"status": "done", "no_followup": True},
            "",
            {
                "status": "done",
                "no_followup": True,
                "completion_continuation": "no_followup",
            },
        ),
        (
            {"status": "done", "successor_todo_ids": ["todo_successor"]},
            "",
            {"completion_continuation": "successor"},
        ),
        (
            {"status": "open"},
            "completed_at=2026-08-01T00%3A00%3A00Z",
            {"status": "open"},
        ),
        (
            {"note": "", "required_capabilities": []},
            "note=keep required_capabilities=network",
            {"required_capabilities": []},
        ),
        (
            {"monitor_metadata": {"consecutive_no_change": 0, "watch_only": False}},
            "watch_only=true",
            {"watch_only": False},
        ),
    ],
)
def test_field_omission_clear_and_completion_semantics(intent, initial, expected):
    lines = _lines(initial)
    result = apply_todo_update_to_lines(
        lines, todo_id="todo_field_test", updated_at=AT, **intent
    )
    for key, value in expected.items():
        # Legacy result fields retain the Markdown codec's scalar representation.
        actual = (
            normalize_todo_watch_only(result[key])
            if key == "watch_only"
            else result[key]
        )
        assert actual == value
    block = find_todo_block(lines, todo_id="todo_field_test")[4]
    assert block["todo_id"] == "todo_field_test"
    if intent.get("status") == "done":
        assert block["completed_at"] == AT
    if intent.get("status") == "open":
        assert not block.get("completed_at")
    if intent.get("note") == "":
        assert block["note"] == "keep"  # Empty note historically means omit here.


def test_noop_does_not_advance_updated_at_or_touch_unrelated_lines():
    lines = _lines("updated_at=2026-08-01T00%3A00%3A00Z")
    # Stabilize representation once; the next call must be an actual no-op.
    apply_todo_update_to_lines(lines, todo_id="todo_field_test", updated_at=AT)
    before = deepcopy(lines)
    result = apply_todo_update_to_lines(
        lines, todo_id="todo_field_test", updated_at="2026-09-02T00:00:00Z"
    )
    assert not result["changed"]
    assert lines == before


@pytest.mark.parametrize(
    "intent, initial, error",
    [
        ({"status": "invalid"}, "", "todo status"),
        ({"claim_only": True}, "claimed_by=agent-a", None),
        (
            {"claim_only": True, "claimed_by": "agent-b"},
            "claimed_by=agent-a",
            "already claimed_by",
        ),
        (
            {"status": "deferred", "clear_resume_when": True},
            "",
            "cannot clear resume_when",
        ),
        (
            {
                "status": "done",
                "no_followup": True,
                "successor_todo_ids": ["todo_successor"],
            },
            "",
            "both no_followup",
        ),
    ],
)
def test_invalid_lifecycle_field_combinations_are_rejected(intent, initial, error):
    lines = _lines(initial)
    if error is None:
        apply_todo_update_to_lines(
            lines, todo_id="todo_field_test", updated_at=AT, **intent
        )
    else:
        before = deepcopy(lines)
        with pytest.raises(ValueError, match=error):
            apply_todo_update_to_lines(
                lines, todo_id="todo_field_test", updated_at=AT, **intent
            )
        assert lines == before


def test_completion_field_plan_has_one_crossing_and_retires_metadata_rpc(monkeypatch):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.todos import line_update

    actual = effect_runtime.effect_runtime_result
    calls = []

    def traced(method, params):
        calls.append(method)
        return actual(method, params)

    monkeypatch.setattr(line_update, "effect_runtime_result", traced)
    result = apply_todo_update_to_lines(
        _lines(),
        todo_id="todo_field_test",
        updated_at=AT,
        status="done",
        no_followup=True,
    )
    assert result["completion_continuation"] == "no_followup"
    assert calls == ["todo.field_update.plan"]
    with pytest.raises(
        effect_runtime.EffectRuntimeRejected, match="[Uu]nknown|[Uu]nsupported"
    ):
        actual("todo.completion_state.metadata_updates", {})


def test_rejected_plan_does_not_partially_change_text_or_checkbox():
    lines = _lines()
    before = deepcopy(lines)
    with pytest.raises(ValueError, match="both no_followup"):
        apply_todo_update_to_lines(
            lines,
            todo_id="todo_field_test",
            updated_at=AT,
            text="Do not commit",
            status="done",
            no_followup=True,
            successor_todo_ids=["todo_next"],
        )
    assert lines == before


@pytest.mark.parametrize(
    "resume, clear, expected, error",
    [
        (None, False, "todo_done:todo_old", None),
        ("", False, "todo_done:todo_old", None),
        ("\u001c\t \u2003", False, "todo_done:todo_old", None),
        ("\u2003TODO_DONE:todo_dependency\u2003", False, "todo_done:todo_dependency", None),
        ("resume_at:2026-09-01T08:00:00+08:00", False, "resume_at:2026-09-01T00:00:00Z", None),
        ("", True, None, None),
        ("todo_done:todo_dependency", True, None, "either resume_when"),
        ("unknown:todo_dependency", False, None, "supported"),
        ("resume_at:2026-09-01T08:00:00", False, None, "supported"),
    ],
)
def test_compatibility_field_update_preserves_resume_authoring(resume, clear, expected, error):
    lines = _lines("resume_when=todo_done:todo_old")
    before = deepcopy(lines)
    kwargs = dict(todo_id="todo_field_test", updated_at=AT,
                  resume_when=resume, clear_resume_when=clear)
    if error:
        with pytest.raises(ValueError, match=error):
            apply_todo_update_to_lines(lines, text="Do not commit", **kwargs)
        assert lines == before
    else:
        result = apply_todo_update_to_lines(lines, **kwargs)
        assert result["resume_when"] == expected


def test_compatibility_resume_update_uses_the_whole_field_owner(monkeypatch):
    from loopx.control_plane.todos import line_update, resume_condition

    actual = line_update.effect_runtime_result
    calls = []

    def traced(method, params):
        calls.append(method)
        return actual(method, params)

    for module in (line_update, resume_condition):
        monkeypatch.setattr(module, "effect_runtime_result", traced)
    result = apply_todo_update_to_lines(
        _lines(), todo_id="todo_field_test", updated_at=AT,
        resume_when="TODO_DONE:todo_dependency",
    )
    assert result["resume_when"] == "todo_done:todo_dependency"
    assert calls == ["todo.field_update.plan"]


@pytest.mark.parametrize("exclusions", [[], ["!!!"]])
def test_removed_policy_repair_requires_a_valid_exclusion(exclusions):
    lines = _lines("continuation_policy=primary_review")
    before = deepcopy(lines)
    with pytest.raises(ValueError, match="repair it explicitly"):
        apply_todo_update_to_lines(
            lines,
            todo_id="todo_field_test",
            updated_at=AT,
            continuation_policy="independent_handoff",
            excluded_agents=exclusions,
        )
    assert lines == before


def test_field_planner_covers_the_existing_monitor_codec_fields():
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.control_plane.todos.contract import TODO_MONITOR_METADATA_FIELDS

    clears = dict.fromkeys(TODO_MONITOR_METADATA_FIELDS)
    plan = effect_runtime_result(
        "todo.field_update.plan",
        {
            "schema_version": "loopx_todo_field_update_request_v0",
            "todo": {"todo_id": "todo_field_test", "status": "open"},
            "intent": {"monitor_metadata": clears},
            "updated_at": AT,
        },
    )
    assert plan["metadata_updates"] == {
        "todo_id": "todo_field_test",
        "status": "open",
        **clears,
    }
