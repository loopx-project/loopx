from copy import deepcopy

import pytest

from loopx.control_plane.todos import quota_selection, quota_summary


def source(todo_id, *, canonical=False, empty=False):
    return {
        "schema_version": "todo_summary_v0" if canonical else "legacy_fixture",
        "items": [] if empty else [{
            "todo_id": todo_id,
            "role": "agent",
            "status": "open",
            "task_class": "advancement_task",
            "text": "Advance the checked work.",
            "claimed_by": "agent-a",
        }],
        "total_count": 0 if empty else 1,
        "open_count": 0 if empty else 1,
        "done_count": 0,
    }


@pytest.mark.parametrize("canonical,project,expected", [
    (source("todo_canonical", canonical=True), source("todo_project"), "todo_canonical"),
    (source("todo_legacy"), source("todo_project"), "todo_project"),
    (source("todo_empty", canonical=True, empty=True), source("todo_project"), None),
    (source("todo_legacy"), source("todo_empty", empty=True), None),
    (None, source("todo_project"), "todo_project"),
    (source("todo_legacy"), None, "todo_legacy"),
])
def test_only_preferred_available_source_is_projected(
    monkeypatch, canonical, project, expected,
):
    before = deepcopy((canonical, project))
    calls = []
    original = quota_selection.effect_runtime_result

    def record(method, params):
        calls.append(method)
        return original(method, params)

    monkeypatch.setattr(quota_selection, "effect_runtime_result", record)
    summary = quota_summary.select_quota_todo_summary(
        canonical, project, agent_identity={"agent_id": "agent-a"},
        available_capabilities=["shell"],
    )

    assert [row["todo_id"] for row in summary["first_executable_items"]] == (
        [expected] if expected else []
    )
    # An authoritative empty inventory is still a summary, not a request to
    # adopt tasks from the other source. The real typed owner runs once.
    assert calls == ["todo.quota_planning.project"]
    assert (canonical, project) == before


def test_absent_sources_do_not_invoke_planning(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("absent Todo sources have no planning input")

    monkeypatch.setattr(quota_selection, "effect_runtime_result", unexpected)
    assert quota_summary.select_quota_todo_summary(None, None) is None


@pytest.mark.parametrize("canonical", [True, False])
def test_selected_owner_failure_is_not_replaced_by_fallback(monkeypatch, canonical):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("selected typed owner unavailable")

    monkeypatch.setattr(quota_selection, "effect_runtime_result", unavailable)
    with pytest.raises(RuntimeError, match="selected typed owner unavailable"):
        quota_summary.select_quota_todo_summary(
            source("todo_primary", canonical=canonical), source("todo_fallback"),
        )
