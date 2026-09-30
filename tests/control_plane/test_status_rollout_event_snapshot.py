from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import loopx.rollout_event_log as rollout_event_log_module
import loopx.status as status_module
from loopx.control_plane import effect_runtime
from loopx.control_plane.todos.todo_index import build_todo_index
from loopx.rollout_event_log import (
    ROLLOUT_EVENT_SCHEMA_VERSION,
    RolloutEventSnapshot,
    append_rollout_event,
    build_rollout_event,
    rollout_event_log_path,
)


GOAL_ID = "status-rollout-snapshot"
REPOSITORY = "example/project"


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    project_root = tmp_path / "project"
    runtime_root = tmp_path / "runtime"
    state_path = project_root / "ACTIVE_GOAL_STATE.md"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        "---\nstatus: active\n---\n\n"
        "# Snapshot fixture\n\n"
        "## Agent Todo\n\n"
        "- [-] Resume after PR 41.\n"
        "  <!-- loopx:todo todo_id=todo_wait_pr_41 status=deferred "
        "task_class=advancement_task "
        f"task_repository=git:github.com/{REPOSITORY} "
        "resume_when=pr_merged:#41 -->\n"
        "- [-] Resume after PR 42.\n"
        "  <!-- loopx:todo todo_id=todo_wait_pr_42 status=deferred "
        "task_class=advancement_task "
        f"task_repository=git:github.com/{REPOSITORY} "
        "resume_when=pr_merged:#42 -->\n",
        encoding="utf-8",
    )
    goal = {
        "id": GOAL_ID,
        "domain": "status-rollout-snapshot",
        "status": "active",
        "repo": str(project_root),
        "state_file": state_path.name,
        "adapter": {"kind": "test_v0", "status": "connected-read-only"},
        "authority_sources": [],
    }
    registry_path = project_root / ".loopx" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime_root),
                "goals": [goal],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, runtime_root, goal


def _append_event(runtime_root: Path, event: dict[str, Any]) -> None:
    append_rollout_event(rollout_event_log_path(runtime_root, GOAL_ID), event)


def _merge_event(number: int, *, recorded_at: str) -> dict[str, Any]:
    return build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="pr_merge",
        pr_ref=f"{REPOSITORY}#{number}",
        status="ready",
        summary=f"PR #{number} merged.",
        recorded_at=recorded_at,
    )


def _read_event(label: str, *, recorded_at: str) -> dict[str, Any]:
    return build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="evidence_log_read",
        agent_id="agent-reader",
        status="completed",
        summary=f"Read {label}.",
        details={
            "command": f"loopx evidence-log --label {label}",
            "mode": "thin",
            "limit": 20,
        },
        recorded_at=recorded_at,
    )


def _todo_event(label: str, *, recorded_at: str) -> dict[str, Any]:
    return build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="todo_add",
        todo_id=f"todo_event_{label}",
        status="open",
        summary=f"Event Todo {label}.",
        details={"role": "agent"},
        recorded_at=recorded_at,
    )


def _goal_queue_item(payload: dict[str, Any]) -> dict[str, Any]:
    return next(
        item
        for item in payload["attention_queue"]["items"]
        if item["goal_id"] == GOAL_ID
    )


def _deferred_todo(payload: dict[str, Any], todo_id: str) -> dict[str, Any]:
    return next(
        item
        for item in _goal_queue_item(payload)["agent_todos"]["deferred_items"]
        if item["todo_id"] == todo_id
    )


def _indexed_todo_ids(payload: dict[str, Any]) -> set[str]:
    return {
        str(item.get("todo_id") or "")
        for item in payload["todo_index"]["items"]
    }


def test_collect_status_reuses_one_goal_snapshot_and_next_request_is_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, runtime_root, _goal = _write_fixture(tmp_path)
    initial_events = (
        _merge_event(41, recorded_at="2026-09-18T01:00:00Z"),
        _read_event("initial", recorded_at="2026-09-18T01:01:00Z"),
        _todo_event("initial", recorded_at="2026-09-18T01:02:00Z"),
    )
    appended_events = (
        _merge_event(42, recorded_at="2026-09-18T02:00:00Z"),
        _read_event("appended", recorded_at="2026-09-18T02:01:00Z"),
        _todo_event("appended", recorded_at="2026-09-18T02:02:00Z"),
    )
    for event in initial_events:
        _append_event(runtime_root, event)

    loads: list[tuple[str, int | None]] = []
    original_load = rollout_event_log_module.load_rollout_events

    def load_and_append(path: Path, *, limit: int | None = None):
        events = original_load(path, limit=limit)
        loads.append((path.parent.name, limit))
        if len(loads) == 1:
            for event in appended_events:
                _append_event(runtime_root, event)
        return events

    monkeypatch.setattr(
        rollout_event_log_module,
        "load_rollout_events",
        load_and_append,
    )

    first = status_module.collect_status(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[tmp_path],
        limit=20,
        goal_id=GOAL_ID,
        include_public_boundary_scan=False,
    )

    assert loads == [(GOAL_ID, 500)]
    assert _deferred_todo(first, "todo_wait_pr_41")["resume_ready"] is True
    assert _deferred_todo(first, "todo_wait_pr_42")["resume_ready"] is False
    assert [
        receipt["event_id"]
        for receipt in _goal_queue_item(first)["evidence_log_read_receipts"]
    ] == [initial_events[1]["event_id"]]
    assert first["todo_index"]["rollout_event_count"] == 1
    assert "todo_event_initial" in _indexed_todo_ids(first)
    assert "todo_event_appended" not in _indexed_todo_ids(first)

    second = status_module.collect_status(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[tmp_path],
        limit=20,
        goal_id=GOAL_ID,
        include_public_boundary_scan=False,
    )

    assert loads == [(GOAL_ID, 500), (GOAL_ID, 500)]
    assert _deferred_todo(second, "todo_wait_pr_42")["resume_ready"] is True
    assert [
        receipt["event_id"]
        for receipt in _goal_queue_item(second)["evidence_log_read_receipts"]
    ] == [appended_events[1]["event_id"], initial_events[1]["event_id"]]
    assert second["todo_index"]["rollout_event_count"] == 2
    assert "todo_event_appended" in _indexed_todo_ids(second)


def test_explicit_empty_events_do_not_fall_back_to_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registry_path, runtime_root, goal = _write_fixture(tmp_path)
    _append_event(
        runtime_root,
        _merge_event(41, recorded_at="2026-09-18T01:00:00Z"),
    )
    original_load = status_module.load_rollout_events

    def fail_load(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        pytest.fail("explicit empty rollout events must not reload from disk")

    monkeypatch.setattr(status_module, "load_rollout_events", fail_load)
    observed_empty = status_module.active_state_todo_fields(
        goal,
        runtime_root=runtime_root,
        rollout_events=(),
    )
    monkeypatch.setattr(status_module, "load_rollout_events", original_load)
    fresh = status_module.active_state_todo_fields(
        goal,
        runtime_root=runtime_root,
    )

    empty_todo = next(
        item
        for item in observed_empty["agent_todos"]["deferred_items"]
        if item["todo_id"] == "todo_wait_pr_41"
    )
    fresh_todo = next(
        item
        for item in fresh["agent_todos"]["deferred_items"]
        if item["todo_id"] == "todo_wait_pr_41"
    )
    assert empty_todo["resume_ready"] is False
    assert fresh_todo["resume_ready"] is True


def test_direct_todo_index_calls_keep_fresh_disk_reads(tmp_path: Path) -> None:
    _registry_path, runtime_root, _goal = _write_fixture(tmp_path)
    kwargs = {
        "queue": {"items": []},
        "history": {"goals": [{"id": GOAL_ID}]},
        "runtime_root": runtime_root,
        "public_safe_compact_text": status_module.public_safe_compact_text,
        "limit": 20,
    }

    before = build_todo_index(**kwargs)
    _append_event(
        runtime_root,
        _todo_event("direct", recorded_at="2026-09-18T03:00:00Z"),
    )
    after = build_todo_index(**kwargs)

    assert before["rollout_event_count"] == 0
    assert after["rollout_event_count"] == 1
    assert after["items"][0]["todo_id"] == "todo_event_direct"


def test_snapshot_inherits_tolerant_last_500_valid_event_loading(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    log_path = rollout_event_log_path(runtime_root, GOAL_ID)
    log_path.parent.mkdir(parents=True)
    lines = [
        json.dumps(
            {
                "schema_version": ROLLOUT_EVENT_SCHEMA_VERSION,
                "goal_id": GOAL_ID,
                "event_kind": "validation",
                "index": index,
            }
        )
        for index in range(503)
    ]
    lines.insert(250, "{malformed")
    lines.insert(
        400,
        json.dumps(
            {
                "schema_version": "unknown",
                "goal_id": GOAL_ID,
                "event_kind": "validation",
                "index": "invalid-schema",
            }
        ),
    )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    snapshot = RolloutEventSnapshot(runtime_root, limit=500)

    events = snapshot.events_for_goal(GOAL_ID, limit=500)

    assert isinstance(events, tuple)
    assert len(events) == 500
    assert events[0]["index"] == 3
    assert events[-1]["index"] == 502
    with pytest.raises(ValueError, match="configured 500, requested 499"):
        snapshot.events_for_goal(GOAL_ID, limit=499)


def test_snapshot_caches_an_observed_empty_goal(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    snapshot = RolloutEventSnapshot(runtime_root, limit=500)

    first = snapshot.events_for_goal(GOAL_ID, limit=500)
    _append_event(
        runtime_root,
        _todo_event("later", recorded_at="2026-09-18T04:00:00Z"),
    )

    assert first == ()
    assert snapshot.events_for_goal(GOAL_ID, limit=500) == ()
    fresh = RolloutEventSnapshot(runtime_root, limit=500)
    assert fresh.events_for_goal(GOAL_ID, limit=500)[0]["todo_id"] == (
        "todo_event_later"
    )


def test_todo_index_rejects_a_supplied_lookup_with_a_different_tail(
    tmp_path: Path,
) -> None:
    snapshot = RolloutEventSnapshot(tmp_path / "runtime", limit=499)

    with pytest.raises(ValueError, match="configured 499, requested 500"):
        build_todo_index(
            queue={"items": []},
            history={"goals": [{"id": GOAL_ID}]},
            runtime_root=tmp_path / "runtime",
            public_safe_compact_text=status_module.public_safe_compact_text,
            events_for_goal=snapshot.events_for_goal,
        )


def test_collect_status_scans_effect_runtime_sources_once_per_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, runtime_root, _goal = _write_fixture(tmp_path)
    original_scan = effect_runtime._scan_runtime_source_files
    scanned_roots: list[Path] = []

    def counted_scan(root: Path) -> tuple[str, ...]:
        scanned_roots.append(root)
        return original_scan(root)

    monkeypatch.setattr(
        effect_runtime,
        "_scan_runtime_source_files",
        counted_scan,
    )

    for _request in range(2):
        status_module.collect_status(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root),
            scan_roots=[tmp_path],
            limit=20,
            goal_id=GOAL_ID,
            include_public_boundary_scan=False,
        )

    assert scanned_roots == [
        Path(effect_runtime.__file__).resolve().parent,
        Path(effect_runtime.__file__).resolve().parent,
    ]


def _event_only_todo_event(
    todo_id: str,
    *,
    recorded_at: str,
    event_kind: str = "todo_add",
    summary: str | None = None,
) -> dict[str, Any]:
    return build_rollout_event(
        goal_id=GOAL_ID,
        event_kind=event_kind,
        todo_id=todo_id,
        status="open",
        summary=summary
        or f"todo {event_kind.removeprefix('todo_')} recorded for {todo_id}",
        details={"role": "agent"},
        recorded_at=recorded_at,
    )


def _build_event_only_index(runtime_root: Path) -> dict[str, Any]:
    return build_todo_index(
        queue={"items": []},
        history={"goals": [{"id": GOAL_ID}]},
        runtime_root=runtime_root,
        public_safe_compact_text=status_module.public_safe_compact_text,
        limit=20,
    )


@pytest.mark.parametrize(
    "audit_summary",
    [
        "todo add recorded for todo_event_only",
        "Ship the narrower fix.",
    ],
)
def test_event_only_todo_rows_never_use_the_event_summary_as_a_title(
    tmp_path: Path,
    audit_summary: str,
) -> None:
    _registry_path, runtime_root, _goal = _write_fixture(tmp_path)
    _append_event(
        runtime_root,
        _event_only_todo_event(
            "todo_event_only",
            recorded_at="2026-09-30T01:00:00Z",
            summary=audit_summary,
        ),
    )

    index = _build_event_only_index(runtime_root)

    assert index["rollout_event_count"] == 1
    row = index["items"][0]
    assert row["todo_id"] == "todo_event_only"
    assert row["source"] == "rollout_event_log"
    assert row["title_source"] == "event_audit"
    assert row["text"] == "todo todo_event_only"
    assert row["title"] == row["text"]
    assert row["latest_event_summary"] == audit_summary
    assert audit_summary not in json.dumps(
        {"text": row["text"], "title": row["title"]},
        sort_keys=True,
    )


def test_event_only_todo_rows_follow_the_newest_event_summary(
    tmp_path: Path,
) -> None:
    _registry_path, runtime_root, _goal = _write_fixture(tmp_path)
    for event in (
        _event_only_todo_event("todo_event_only", recorded_at="2026-09-30T01:00:00Z"),
        _event_only_todo_event(
            "todo_event_only",
            recorded_at="2026-09-30T02:00:00Z",
            event_kind="todo_update",
        ),
    ):
        _append_event(runtime_root, event)

    index = _build_event_only_index(runtime_root)

    row = index["items"][0]
    assert row["event_count"] == 2
    assert row["latest_event_kind"] == "todo_update"
    assert row["latest_event_summary"] == "todo update recorded for todo_event_only"
    assert row["text"] == "todo todo_event_only"


def test_attention_queue_todo_text_stays_authoritative_over_events(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, _goal = _write_fixture(tmp_path)
    _append_event(
        runtime_root,
        _event_only_todo_event(
            "todo_wait_pr_41",
            recorded_at="2026-09-30T03:00:00Z",
            event_kind="todo_update",
        ),
    )

    payload = status_module.collect_status(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[tmp_path],
        limit=20,
        goal_id=GOAL_ID,
        include_public_boundary_scan=False,
    )

    row = next(
        item
        for item in payload["todo_index"]["items"]
        if item.get("todo_id") == "todo_wait_pr_41"
    )
    assert row["source"] == "attention_queue"
    assert "title_source" not in row
    assert row["text"] == "Resume after PR 41."
    assert row["latest_event_summary"] == "todo update recorded for todo_wait_pr_41"
