from copy import deepcopy
import json

import pytest

from loopx.control_plane.todos import quota_selection, quota_summary
from loopx.control_plane import effect_runtime


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


@pytest.mark.parametrize("selector", [
    quota_summary.select_quota_todo_source_items,
    quota_summary.select_planning_inventory_source_items,
])
@pytest.mark.parametrize("canonical,project,expected,excluded", [
    (source("todo_canonical", canonical=True), source("todo_project"), "todo_canonical", "todo_project"),
    (source("todo_legacy"), source("todo_project"), "todo_project", "todo_legacy"),
    (source("todo_empty", canonical=True, empty=True), source("todo_project"), None, "todo_project"),
    (source("todo_legacy"), {}, None, "todo_legacy"),
    (None, source("todo_project"), "todo_project", None),
    (source("todo_legacy"), None, "todo_legacy", None),
    (None, None, None, None),
])
def test_source_reads_preserve_precedence_without_sending_discarded_rows(
    monkeypatch, selector, canonical, project, expected, excluded,
):
    before = deepcopy((canonical, project))
    requests = []
    original = effect_runtime._request_with_info

    def record(*args, **kwargs):
        requests.append(json.dumps(kwargs["params"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(effect_runtime, "_request_with_info", record)
    rows = selector(canonical, project)

    assert [row["todo_id"] for row in rows] == ([expected] if expected else [])
    if excluded:
        assert all(excluded not in request for request in requests)
    assert (canonical, project) == before


@pytest.mark.parametrize("selector", [
    quota_summary.select_quota_todo_source_items,
    quota_summary.select_planning_inventory_source_items,
])
def test_selected_source_transport_failure_does_not_adopt_other_tasks(monkeypatch, selector):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("selected source transport unavailable")

    monkeypatch.setattr(effect_runtime, "_request_with_info", unavailable)
    with pytest.raises(RuntimeError, match="TypeScript Effect runtime request failed"):
        selector(source("todo_primary", canonical=True), source("todo_other"))


def test_terminal_planning_keeps_the_selected_source_causality():
    canonical = source("todo_terminal", canonical=True)
    canonical["items"][0].update(status="done", done=True, evidence="verified outcome")
    project = source("todo_other")

    assert quota_summary.select_planning_inventory_source_items(canonical, project) == []
    terminal = quota_summary.select_planning_inventory_source_items(
        canonical, project, include_terminal=True,
    )
    assert [row["todo_id"] for row in terminal] == ["todo_terminal"]
    assert terminal[0]["evidence"] == "verified outcome"


@pytest.mark.parametrize("malformed_selected", [False, True])
def test_malformed_handoff_display_row_only_blocks_its_selected_source(malformed_selected):
    canonical = source("todo_canonical", canonical=True)
    project = source("todo_discarded")
    malformed = canonical if malformed_selected else project
    malformed["items"][0].update(
        index="invalid-display-index", excluded_agents=["agent-b"],
        unblocks_todo_id="todo_prior",
    )

    if malformed_selected:
        with pytest.raises(ValueError):
            quota_summary.select_quota_todo_source_items(canonical, project)
    else:
        rows = quota_summary.select_quota_todo_source_items(canonical, project)

        assert [row["todo_id"] for row in rows] == ["todo_canonical"]
