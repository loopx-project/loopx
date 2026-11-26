from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
from typing import cast

import pytest

from loopx.control_plane.todos import todo_item_semantics, todo_semantics, todo_summary_semantics
from loopx.control_plane.todos.todo_semantics import (
    TODO_LEGACY_ITEM_SCHEMA,
    TODO_MISSING_INDEX,
    TODO_MISSING_PRIORITY_RANK,
    TODO_NATIVE_ITEM_SCHEMA,
    TODO_PRESENTATION_METADATA_SCHEMA,
    TODO_STATUS_BLOCKED,
    TODO_STATUS_DEFERRED,
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_BLOCKER,
    TODO_TASK_CLASS_MONITOR,
    agent_scoped_selectable_advancement_todo_ids,
    todo_advancement_frontier_counts,
    todo_advancement_frontier_items,
    todo_blocker_reason,
    todo_index_rank,
    todo_item_claimed_by_agent_or_unclaimed,
    todo_item_excludes_agent,
    todo_item_expires_at,
    todo_item_has_removed_continuation_policy,
    todo_item_is_actionable_open,
    todo_item_is_deferred,
    todo_item_is_due_monitor,
    todo_item_is_expired_monitor,
    todo_item_is_watch_only_monitor,
    todo_item_missing_monitor_schedule,
    todo_item_next_due_at,
    todo_item_task_class,
    todo_item_task_text,
    todo_presentation_metadata,
    todo_presentation_sort_key,
    todo_priority_label,
    todo_priority_parts,
    todo_priority_rank,
    todo_projection_sort_key,
    todo_scoped_runnable_advancement_count,
    todo_summary_claim_scope_agent_id,
    todo_summary_first_executable_item,
    todo_summary_has_only_future_scoped_monitor_work,
    todo_summary_monitor_due_count,
    todo_summary_monitor_due_items,
    todo_summary_monitor_items,
    todo_summary_monitor_schedule_gap_count,
    todo_summary_monitor_schedule_gap_items,
    todo_summary_monitor_writeback_contract,
    todo_summary_monitor_writeback_supported,
    todo_summary_non_watch_only_monitor_due_items,
    todo_summary_open_count,
    todo_summary_open_task_counts,
    todo_summary_watch_only_monitor_due_items,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_IGNORE_MARKER = "type" + ": ignore"
_PINNED_EXPORTS = (
    "TODO_MISSING_PRIORITY_RANK",
    "TODO_MISSING_INDEX",
    "TODO_PRIORITY_PREFIX_PATTERN",
    "TODO_PRIORITY_LABEL_PATTERN",
    "TODO_PRESENTATION_METADATA_SCHEMA",
    "TODO_LEGACY_ITEM_SCHEMA",
    "TODO_NATIVE_ITEM_SCHEMA",
    "TODO_STATUS_BLOCKED",
    "TODO_STATUS_DEFERRED",
    "TODO_TASK_CLASS_ADVANCEMENT",
    "TODO_TASK_CLASS_BLOCKER",
    "TODO_TASK_CLASS_MONITOR",
    "todo_blocker_reason",
    "todo_item_is_watch_only_monitor",
    "todo_priority_parts",
    "todo_priority_label",
    "todo_priority_rank",
    "todo_index_rank",
    "todo_presentation_metadata",
    "todo_presentation_sort_key",
    "todo_projection_sort_key",
    "todo_item_task_text",
    "todo_item_task_class",
    "todo_item_is_actionable_open",
    "todo_item_is_deferred",
    "todo_item_next_due_at",
    "todo_item_expires_at",
    "todo_item_is_expired_monitor",
    "todo_item_is_due_monitor",
    "todo_item_missing_monitor_schedule",
    "todo_item_claimed_by_agent_or_unclaimed",
    "_advancement_frontier_projection",
    "todo_advancement_frontier_items",
    "agent_scoped_selectable_advancement_todo_ids",
    "todo_scoped_runnable_advancement_count",
    "todo_advancement_frontier_counts",
    "todo_item_has_removed_continuation_policy",
    "todo_item_excludes_agent",
    "todo_summary_claim_scope_agent_id",
    "todo_summary_monitor_writeback_contract",
    "todo_summary_monitor_writeback_supported",
    "todo_summary_monitor_items",
    "_summary_monitor_items",
    "todo_summary_monitor_due_items",
    "todo_summary_watch_only_monitor_due_items",
    "todo_summary_non_watch_only_monitor_due_items",
    "todo_summary_monitor_due_count",
    "todo_summary_monitor_schedule_gap_items",
    "todo_summary_monitor_schedule_gap_count",
    "todo_summary_open_count",
    "todo_summary_open_task_counts",
    "todo_summary_has_only_future_scoped_monitor_work",
    "_positive_int",
    "todo_summary_first_executable_item",
)


def _any_ast_refs(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            count += 1
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            count += 1
        elif isinstance(node, ast.alias) and node.name == "Any":
            count += 1
    return count


def test_todo_semantics_split_zero_any_ceilings_and_exports() -> None:
    paths = [
        REPO_ROOT / "loopx/control_plane/todos/todo_item_semantics.py",
        REPO_ROOT / "loopx/control_plane/todos/todo_summary_semantics.py",
        REPO_ROOT / "loopx/control_plane/todos/todo_semantics.py",
        Path(__file__).resolve(),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert _any_ast_refs(path) == 0, path
        assert _IGNORE_MARKER not in text, path
        assert len(text.splitlines()) < 750, path

    main_lines = (REPO_ROOT / "loopx/control_plane/todos/todo_semantics.py").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(main_lines) <= 550

    for symbol in _PINNED_EXPORTS:
        assert hasattr(todo_semantics, symbol), symbol

    assert todo_semantics.todo_priority_label is todo_item_semantics.todo_priority_label
    assert (
        todo_semantics.todo_summary_open_task_counts
        is todo_summary_semantics.todo_summary_open_task_counts
    )
    assert TODO_STATUS_BLOCKED == "blocked"
    assert TODO_STATUS_DEFERRED == "deferred"
    assert TODO_TASK_CLASS_BLOCKER == "blocker"


def test_priority_key_presence_blocker_reason_and_presentation_boundaries() -> None:
    assert todo_priority_parts("[p1] Ship fix") == ("P1", "Ship fix")
    assert todo_priority_parts("No priority") == (None, "No priority")
    assert todo_priority_label({"priority": None, "text": "[P0] Urgent"}) is None
    assert todo_priority_label({"text": "[P0] Urgent"}) == "P0"
    assert todo_priority_label({"title": "[P2] Title fallback"}) == "P2"
    assert todo_priority_rank({"priority": None, "text": "[P0] Urgent"}) == TODO_MISSING_PRIORITY_RANK
    assert todo_priority_rank("p3") == 3
    assert todo_priority_rank(MappingProxyType({"priority": "P1"})) == 1

    assert todo_index_rank({"index": "12"}) == 12
    assert todo_index_rank({"index": None}) == TODO_MISSING_INDEX
    assert todo_index_rank({"index": "not-an-int"}) == TODO_MISSING_INDEX

    assert (
        todo_blocker_reason(
            {
                "status": "blocked",
                "reason": "Blocked on local file ~/workspace/secret/run.json failure",
            }
        )
        is None
    )
    assert (
        todo_blocker_reason(
            {
                "status": "blocked",
                "reason": "Blocked on missing peer receipt",
            }
        )
        == "Blocked on missing peer receipt"
    )
    assert (
        todo_blocker_reason({"status": "open", "task_class": "advancement_task", "reason": "skip"})
        is None
    )

    with pytest.raises(ValueError, match="must be an object"):
        todo_presentation_metadata(cast(dict[str, object], "bad"))
    with pytest.raises(ValueError, match="unsupported Todo presentation schema"):
        todo_presentation_metadata({"schema_version": "unknown_v99"})

    native = {
        "schema_version": TODO_NATIVE_ITEM_SCHEMA,
        "todo_id": "t_native",
        "role": "user",
        "index": 1,
        "updated_at": "2026-01-01T00:00:00Z",
    }
    assert todo_presentation_metadata(native) == {
        "schema_version": TODO_PRESENTATION_METADATA_SCHEMA,
        "todo_id": "t_native",
        "display_section": "User Todo",
        "display_order": None,
        "order_source": "todo_id",
    }
    assert todo_presentation_sort_key(native)[1:] == (
        TODO_MISSING_INDEX,
        "2026-01-01T00:00:00Z",
        "t_native",
    )
    legacy = {
        "schema_version": TODO_LEGACY_ITEM_SCHEMA,
        "todo_id": "t_legacy",
        "index": 4,
    }
    assert todo_presentation_sort_key(legacy)[1:] == (4, "", "")
    assert todo_presentation_sort_key({"schema_version": "outer_envelope_v1", "index": True})[1] == TODO_MISSING_INDEX
    assert todo_presentation_sort_key({"schema_version": "outer_envelope_v1", "index": 3})[1] == 3
    assert todo_projection_sort_key({"priority": "P1", "index": 5}) == (1, 5)


def test_item_schedule_claim_and_actionable_predicates() -> None:
    due_item = {
        "todo_id": "m1",
        "status": "open",
        "title": "Observe peer status",
        "next_due_at": "2025-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
        "watch_only": True,
    }
    assert todo_item_task_text({"title": "A", "text": "B"}) == "A B"
    assert todo_item_task_class(due_item) == TODO_TASK_CLASS_MONITOR
    assert todo_item_is_watch_only_monitor(due_item) is True
    assert todo_item_is_actionable_open(due_item) is True
    assert todo_item_is_actionable_open(
        {**due_item, "goal_acceptance_guard": {"allowed": False}}
    ) is False
    assert todo_item_is_deferred({"status": "deferred"}) is True
    assert todo_item_next_due_at(due_item) is not None
    assert todo_item_expires_at(due_item) is not None
    assert todo_item_is_expired_monitor(due_item) is False
    assert todo_item_is_due_monitor(due_item) is True
    assert todo_item_missing_monitor_schedule({"status": "open", "task_class": TODO_TASK_CLASS_MONITOR}) is True
    assert todo_item_has_removed_continuation_policy({"removed_continuation_policy": "primary_review"}) is True
    assert todo_item_has_removed_continuation_policy({"removed_continuation_policy": "retired"}) is False
    assert todo_item_excludes_agent({"excluded_agents": ["agent-a"]}, agent_id="agent-a") is True
    assert todo_item_claimed_by_agent_or_unclaimed(
        {"removed_continuation_policy": "primary_review"}, agent_id="agent-a"
    ) is False


def test_summary_writeback_precedence_and_work_count_validation() -> None:
    due_monitor = {
        "todo_id": "m_due",
        "status": "open",
        "task_class": TODO_TASK_CLASS_MONITOR,
        "next_due_at": "2020-01-01T00:00:00Z",
    }
    watch_due = {**due_monitor, "todo_id": "m_watch", "watch_only": True}
    gap_monitor = {
        "todo_id": "m_gap",
        "status": "open",
        "task_class": TODO_TASK_CLASS_MONITOR,
        "claimed_by": "agent-a",
    }
    disabled: dict[str, object] = {
        "monitor_writeback": {"supported": False, "source": "  legacy_ro  "},
        "monitor_due_items": [due_monitor],
        "monitor_open_items": [due_monitor, gap_monitor],
    }
    assert todo_summary_monitor_writeback_contract(disabled) == {
        "supported": False,
        "source": "legacy_ro",
    }
    assert todo_summary_monitor_writeback_supported(disabled) is False
    assert todo_summary_monitor_due_items(disabled) == []
    assert todo_summary_monitor_due_count(disabled) == 0
    assert todo_summary_monitor_schedule_gap_items(disabled) == []
    assert todo_summary_monitor_schedule_gap_count(disabled) == 0

    precedence: dict[str, object] = {
        "claim_scope": {"agent_id": "agent-a"},
        "monitor_due_count": 7,
        "monitor_schedule_gap_count": 9,
        "monitor_open_items": [due_monitor, gap_monitor],
    }
    assert todo_summary_monitor_due_count(precedence) == 7
    assert todo_summary_monitor_schedule_gap_count(precedence) == 1
    assert todo_summary_watch_only_monitor_due_items(
        {"monitor_open_items": [due_monitor, watch_due]}
    ) == [watch_due]
    assert todo_summary_non_watch_only_monitor_due_items(
        {"monitor_open_items": [due_monitor, watch_due]}
    ) == [due_monitor]
    assert todo_summary_open_count({"open_count": "-4"}) == 0
    assert todo_summary_open_count({"open_count": "15"}) == 15
    assert todo_summary_first_executable_item(
        {
            "first_executable_items": [
                due_monitor,
                {"todo_id": "a1", "status": "open", "task_class": TODO_TASK_CLASS_ADVANCEMENT},
            ]
        }
    ) == {"todo_id": "a1", "status": "open", "task_class": TODO_TASK_CLASS_ADVANCEMENT}

    assert todo_summary_claim_scope_agent_id(None) is None
    assert todo_summary_claim_scope_agent_id({"claim_scope": {"agent_id": " Agent A "}}) == "agent-a"
    assert todo_summary_monitor_items(
        {"monitor_due_items": [due_monitor], "monitor_open_items": [due_monitor, gap_monitor]}
    ) == [due_monitor, gap_monitor]
    assert todo_advancement_frontier_items(None, agent_id="agent-a") == {
        "current_agent_claimed_items": [],
        "unclaimed_items": [],
        "other_agent_claimed_items": [],
    }
    assert todo_advancement_frontier_counts(None, agent_id="agent-a") == {
        "current_agent_claimed_advancement_count": 0,
        "unclaimed_advancement_count": 0,
        "other_agent_claimed_advancement_count": 0,
    }
    assert agent_scoped_selectable_advancement_todo_ids(
        {"executable_backlog_items": [{"todo_id": "todo_sample_a1", "status": "open", "task_class": TODO_TASK_CLASS_ADVANCEMENT}]},
        agent_id="agent-a",
    ) == {"todo_sample_a1"}
    assert (
        todo_scoped_runnable_advancement_count(
            {"work_counts": {"schema_version": "todo_work_counts_v0", "complete": True, "agent_id": "agent-a", "advancement": 3}},
            agent_id="agent-a",
        )
        == 3
    )
    assert (
        todo_scoped_runnable_advancement_count(
            {"work_counts": {"schema_version": "todo_work_counts_v0", "complete": True, "agent_id": "agent-a", "advancement": True}},
            agent_id="agent-a",
        )
        is None
    )

    for bad_counts in (
        {"schema_version": "wrong", "complete": True, "open": 0, "advancement": 0, "monitor": 0, "hidden": 0},
        {"schema_version": "todo_work_counts_v0", "complete": True, "open": True, "advancement": 0, "monitor": 0, "hidden": 0},
        {"schema_version": "todo_work_counts_v0", "complete": True, "open": 1, "advancement": 0, "monitor": 0, "hidden": 1},
        {"schema_version": "todo_work_counts_v0", "complete": False, "open": 1, "advancement": 1, "monitor": 1, "hidden": 0},
    ):
        with pytest.raises(ValueError, match="Todo work count"):
            todo_summary_open_task_counts({"work_counts": bad_counts})


def test_hot_path_avoids_sort_dict_allocations_and_duplicate_monitor_count_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_metadata(_item: object) -> dict[str, object]:
        raise AssertionError("todo_presentation_sort_key allocated full metadata dict")

    monkeypatch.setattr(todo_item_semantics, "todo_presentation_metadata", fail_metadata)
    rows: list[dict[str, object]] = [
        {"schema_version": TODO_NATIVE_ITEM_SCHEMA, "priority": "P2", "todo_id": "b", "updated_at": "2"},
        {"schema_version": TODO_LEGACY_ITEM_SCHEMA, "priority": "P2", "todo_id": "a", "index": 1},
    ]
    assert [row["todo_id"] for row in sorted(rows, key=todo_presentation_sort_key)] == ["a", "b"]

    due_calls = 0
    gap_calls = 0
    orig_due = todo_summary_semantics.todo_summary_monitor_due_count
    orig_gap = todo_summary_semantics.todo_summary_monitor_schedule_gap_count

    def count_due(*args: object, **kwargs: object) -> int:
        nonlocal due_calls
        due_calls += 1
        return orig_due(*cast(tuple[dict[str, object] | None], args))

    def count_gap(*args: object, **kwargs: object) -> int:
        nonlocal gap_calls
        gap_calls += 1
        return orig_gap(*cast(tuple[dict[str, object] | None], args))

    def fail_monitor_list(_summary: object) -> list[dict[str, object]]:
        raise AssertionError("future-scoped check materialized full monitor list")

    monkeypatch.setattr(todo_summary_semantics, "todo_summary_monitor_due_count", count_due)
    monkeypatch.setattr(todo_summary_semantics, "todo_summary_monitor_schedule_gap_count", count_gap)
    monkeypatch.setattr(todo_summary_semantics, "todo_summary_monitor_items", fail_monitor_list)

    future_monitor = {
        "todo_id": "m_future",
        "status": "open",
        "task_class": TODO_TASK_CLASS_MONITOR,
        "claimed_by": "agent-a",
        "next_due_at": "2099-01-01T00:00:00Z",
    }
    summary: dict[str, object] = {
        "claim_scope": {"agent_id": "agent-a"},
        "monitor_open_items": [future_monitor],
        "work_counts": {
            "schema_version": "todo_work_counts_v0",
            "agent_id": "agent-a",
            "complete": True,
            "open": 1,
            "advancement": 0,
            "monitor": 1,
            "hidden": 0,
        },
    }
    assert todo_summary_has_only_future_scoped_monitor_work(summary) is True
    assert due_calls == 1
    assert gap_calls == 1


def test_paired_origin_main_behavioral_parity_across_item_frontier_and_summary(
    tmp_path: Path,
) -> None:
    rev = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "origin/main"],
        text=True,
        capture_output=True,
    )
    if rev.returncode != 0:
        pytest.skip("origin/main ref unavailable in shallow checkout")

    baseline_code = subprocess.check_output(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "show",
            "origin/main:loopx/control_plane/todos/todo_semantics.py",
        ],
        text=True,
    )
    script = """
import sys, types
from loopx.control_plane.todos import todo_semantics as head_mod
spec_mod = types.ModuleType("baseline_todo_semantics")
spec_mod.__package__ = "loopx.control_plane.todos"
baseline_code = open(sys.argv[1], encoding="utf-8").read()
exec(compile(baseline_code, "origin_main_todo_semantics.py", "exec"), spec_mod.__dict__)

future_m = {"todo_id": "m_future", "status": "open", "task_class": "continuous_monitor",
            "claimed_by": "agent-a", "next_due_at": "2099-01-01T00:00:00Z"}
due_m = {"todo_id": "m_due", "status": "open", "task_class": "continuous_monitor",
         "next_due_at": "2020-01-01T00:00:00Z", "watch_only": True}
gap_m = {"todo_id": "m_gap", "status": "open", "task_class": "continuous_monitor", "claimed_by": "agent-a"}
adv_a = {"todo_id": "a_own", "status": "open", "task_class": "advancement_task", "claimed_by": "agent-a", "priority": "P1", "index": 2}
adv_free = {"todo_id": "a_free", "status": "open", "task_class": "advancement_task", "text": "[P0] First", "index": 5}
adv_null_prio = {"todo_id": "a_null", "status": "open", "task_class": "advancement_task", "priority": None, "text": "[P0] Ignored"}

items = [
    adv_a, adv_free, adv_null_prio, future_m, due_m, gap_m,
    {"schema_version": "todo_domain_record_v0", "todo_id": "t_dom", "role": "agent", "archive_state": "archive", "index": 0, "completed_at": "2025-01-01T00:00:00Z"},
    {"schema_version": "todo_item_v0", "todo_id": "t_v0", "source_section": "User Todo", "index": 3},
    {"schema_version": "outer_custom_v1", "todo_id": "t_outer", "index": 2, "priority": "P1"},
    {"status": "blocked", "reason": "Blocked near ~/example/a/b"},
]
summaries = [
    None,
    {},
    {"claim_scope": {"agent_id": "agent-a"}, "monitor_open_items": [future_m],
     "work_counts": {"schema_version": "todo_work_counts_v0", "agent_id": "agent-a", "complete": True, "open": 1, "advancement": 0, "monitor": 1, "hidden": 0}},
    {"claim_scope": {"agent_id": "agent-a"}, "monitor_due_items": [], "monitor_open_items": [due_m, gap_m],
     "executable_backlog_items": [adv_a, adv_free], "current_agent_claimed_advancement_count": 9007199254740993,
     "work_counts": {"schema_version": "todo_work_counts_v0", "agent_id": "agent-a", "complete": True, "open": 4, "advancement": 2, "monitor": 2, "hidden": 0}},
    {"unclaimed_priority_open_items": [adv_free], "claimed_advancement_open_items": [adv_a], "monitor_due_count": 4, "monitor_schedule_gap_count": 3},
    {"monitor_writeback": {"supported": False, "source": "ro"}, "monitor_due_items": [due_m]},
]
for item in items:
    for fn_name in ("todo_blocker_reason", "todo_item_is_watch_only_monitor", "todo_priority_label", "todo_priority_rank", "todo_index_rank", "todo_presentation_sort_key", "todo_projection_sort_key", "todo_item_task_text", "todo_item_task_class", "todo_item_is_actionable_open", "todo_item_is_deferred", "todo_item_is_expired_monitor", "todo_item_is_due_monitor", "todo_item_missing_monitor_schedule"):
        assert getattr(head_mod, fn_name)(item) == getattr(spec_mod, fn_name)(item), (fn_name, item)
    assert head_mod.todo_item_claimed_by_agent_or_unclaimed(item, agent_id="agent-a") == spec_mod.todo_item_claimed_by_agent_or_unclaimed(item, agent_id="agent-a")
for summary in summaries:
    for fn_name in ("todo_summary_claim_scope_agent_id", "todo_summary_monitor_writeback_contract", "todo_summary_monitor_writeback_supported", "todo_summary_monitor_items", "todo_summary_monitor_due_items", "todo_summary_watch_only_monitor_due_items", "todo_summary_non_watch_only_monitor_due_items", "todo_summary_monitor_due_count", "todo_summary_monitor_schedule_gap_items", "todo_summary_monitor_schedule_gap_count", "todo_summary_open_count", "todo_summary_open_task_counts", "todo_summary_has_only_future_scoped_monitor_work", "todo_summary_first_executable_item"):
        assert getattr(head_mod, fn_name)(summary) == getattr(spec_mod, fn_name)(summary), (fn_name, summary)
    for agent in (None, "agent-a"):
        assert head_mod.todo_advancement_frontier_items(summary, agent_id=agent) == spec_mod.todo_advancement_frontier_items(summary, agent_id=agent)
        assert head_mod.todo_advancement_frontier_counts(summary, agent_id=agent) == spec_mod.todo_advancement_frontier_counts(summary, agent_id=agent)
        assert head_mod.agent_scoped_selectable_advancement_todo_ids(summary, agent_id=agent) == spec_mod.agent_scoped_selectable_advancement_todo_ids(summary, agent_id=agent)
        assert head_mod.todo_scoped_runnable_advancement_count(summary, agent_id=agent) == spec_mod.todo_scoped_runnable_advancement_count(summary, agent_id=agent)
print("PARITY_OK")
"""
    baseline_file = tmp_path / "origin_main_todo_semantics.py"
    baseline_file.write_text(baseline_code, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", script, str(baseline_file)],
        cwd=str(REPO_ROOT),
        text=True,
        capture_output=True,
        check=True,
    )
    assert proc.stdout.strip() == "PARITY_OK"
