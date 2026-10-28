"""Existing read-model semantics remain unchanged across the owner migration."""
from copy import deepcopy
import json
import os
import subprocess
import sys

import pytest

from loopx.control_plane.todos.handoff_note import (
    build_todo_handoff_note,
)


def test_summary_and_index_use_batches_without_mutating_the_source(monkeypatch, tmp_path):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.todos.todo_summary import compact_todo_group
    from loopx.control_plane.todos.todo_index import build_todo_index
    original = effect_runtime.effect_runtime_result
    calls = []

    def track(method, request, **kwargs):
        calls.append(method)
        return original(method, request, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", track)
    for size in (24, 240):
        items = [{"todo_id": f"todo_batch_{i}", "role": "agent", "index": i,
                  "status": "open", "task_class": "advancement_task", "text": f"Work {i}",
                  "successor_todo_ids": ["todo_next"], "note": "Retain the original validation"}
                 for i in range(size)]
        summary = compact_todo_group(items, role="agent", source_section="Agent Todo", item_limit=None)
        before = deepcopy(summary)
        calls.clear()
        indexed = build_todo_index(queue={"items": [{"goal_id": "goal-a", "agent_todos": summary}]},
            history={"goals": []}, runtime_root=tmp_path,
            public_safe_compact_text=lambda value, limit: str(value or "").strip()[:limit] or None)
        assert calls == ["todo.context.page"]
        assert len(indexed["items"]) == size
        assert summary == before
        assert indexed["items"][0]["handoff_note"]["handoff_id"] == "handoff_batch_0"


def test_planning_source_context_is_one_batch(monkeypatch):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.work_items.planning_inventory import compact_planning_candidates
    calls, original = [], effect_runtime.effect_runtime_result

    def track(method, request, **kwargs):
        calls.append(method)
        return original(method, request, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", track)
    rows = [{"todo_id": f"todo_work_{i}", "text": "Work", "status": "open",
             "task_class": "advancement_task", "note": "Continue complete validation"} for i in range(100)]
    projected = compact_planning_candidates(rows)
    assert calls == ["todo.context.page"]
    assert len(projected) == 100
    assert all(item["continuation_hint"] == "Continue complete validation" for item in projected)


def test_runtime_rejection_does_not_fall_back_to_python(monkeypatch):
    from loopx.control_plane import effect_runtime

    def unavailable(*args, **kwargs):
        raise effect_runtime.EffectRuntimeRejected("handoff owner unavailable")

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", unavailable)
    with pytest.raises(ValueError, match="handoff owner unavailable"):
        build_todo_handoff_note({"todo_id": "todo_original", "successor_todo_ids": ["todo_next"]})


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_canonical_cli_handoff_is_read_only_and_recovers_original_source(tmp_path, monkeypatch, provider):
    from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    config = json.loads(registry.read_text())
    config["goals"][0].update({"domain": "handoff-compatibility", "status": "active",
        "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"}})
    registry.write_text(json.dumps(config))
    # An explicit normal-wheel interpreter exercises the same journey in a
    # fresh process without importing the source checkout through PYTHONPATH.
    python = os.environ.get("LOOPX_HANDOFF_TEST_PYTHON", sys.executable)
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    def cli(*args, succeeds=True):
        result = subprocess.run([python, "-c", "from loopx.entrypoint import main; raise SystemExit(main())", "--format", "json", "--registry", str(registry), *args],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45)
        assert (result.returncode == 0) == succeeds, result.stdout + result.stderr
        return json.loads(result.stdout)

    successor = cli("todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Independent successor",
                    "--operation-id", "handoff-successor")["todo_id"]
    original = cli("todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Original complete requirement",
                   "--operation-id", "handoff-original")["todo_id"]
    first = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", original)
    cli("todo", "update", "--goal-id", "goal-a", "--todo-id", original, "--agent-id", "agent-a",
        "--note", "Continue the original validation", "--successor-todo-id", successor,
        "--update-operation-id", "handoff-continuation",
        "--update-expected-provider-revision", first["authority_read"]["provider_revision"])
    detail = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", original)
    record, revision = detail["todo"], detail["authority_read"]["provider_revision"]
    assert "handoff_note" not in record
    indexed = next(row for row in cli("status", "--goal-id", "goal-a")["todo_index"]["items"] if row["todo_id"] == original)
    assert indexed["handoff_note"]["summary"] == "Continue the original validation"
    assert indexed["handoff_note"]["successor_todo_ids"] == [successor]
    assert indexed["handoff_note"]["handoff_id"] == "handoff_" + original.removeprefix("todo_")
    again = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", original)
    assert again["todo"] == record and again["authority_read"]["provider_revision"] == revision
    display = state.read_bytes()
    backend = runtime / "authority" / f"{provider}-v0"
    offline = backend.with_name(backend.name + "-offline")
    backend.rename(offline)
    try:
        failed = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", original, succeeds=False)
        assert failed["ok"] is False
        assert not backend.exists() and state.read_bytes() == display
    finally:
        offline.rename(backend)
    state.unlink()
    recovered = cli("todo", "list", "--goal-id", "goal-a", "--todo-id", original)
    assert recovered["todo"] == record and recovered["authority_read"]["provider_revision"] == revision
    assert not state.exists()


def test_large_display_context_preserves_full_source_without_expanding_transport_budget(monkeypatch):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.todos.todo_summary import compact_todo_group
    calls, original = [], effect_runtime.effect_runtime_result

    def track(method, request, **kwargs):
        calls.append(method)
        return original(method, request, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", track)
    rows = [{"todo_id": f"todo_history_{i}", "index": i, "text": f"Work {i}", "role": "agent",
             "task_class": "advancement_task", "status": "done", "no_followup": True,
             "completed_at": "2026-01-01T00:00:00Z", "note": "a" * 4000, "excluded_agents": ["agent-b"]}
            for i in range(1000)]
    result = compact_todo_group(rows, role="agent", source_section="Agent Todo", item_limit=12)
    assert result["done_count"] == 1000 and result["total_count"] == 1000
    assert len(result["items"]) == 12
    assert result["recent_completed_advancement_items"][0]["handoff_note"]["summary"] == "a" * 279 + "..."
    assert calls == ["todo.read"]


def test_byte_bounded_context_batches_retain_every_record(monkeypatch):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.todos.summary_item import compact_todo_summary_items
    calls, original = [], effect_runtime.effect_runtime_result

    def track(method, request, **kwargs):
        calls.append(method)
        return original(method, request, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", track)
    rows = [{"todo_id": f"todo_large_{i}", "text": "Work", "status": "open",
             "task_class": "advancement_task", "note": "a" * 120000} for i in range(20)]
    result = compact_todo_summary_items(rows)
    assert len(result) == 20
    assert [row["todo_id"] for row in result] == [row["todo_id"] for row in rows]
    assert all(row["continuation_hint"] == "a" * 279 + "..." for row in result)
    assert calls == ["todo.context.page", "todo.context.page"]


@pytest.mark.parametrize("credential", [False, True])
def test_oversized_single_source_reuses_private_snapshot_without_pretruncation(monkeypatch, credential):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.todos.handoff_note import todo_read_source, project_handoff_context
    calls, original = [], effect_runtime.effect_runtime_result

    def track(method, request, **kwargs):
        calls.append((method, kwargs.get("large_local_snapshot", False)))
        return original(method, request, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", track)
    text = "a" * (effect_runtime.MAX_REQUEST_BYTES + 1000)
    if credential:
        text += " " + "token" + "=" + "v"
    rows = [{"todo_id": f"todo_snapshot_{i}", "text": "Work", "successor_todo_ids": ["todo_next"],
             "note": text if i == 1 else "Continue"} for i in range(3)]
    contexts = project_handoff_context([todo_read_source(row) for row in rows])
    assert [row["note"]["todo_id"] for row in contexts] == [row["todo_id"] for row in rows]
    assert contexts[1]["continuation_hint"] == (None if credential else "a" * 279 + "...")
    assert contexts[1]["note"]["summary"] == ("Work" if credential else "a" * 279 + "...")
    assert calls == [("todo.context.page", False), ("todo.context.page", True), ("todo.context.page", False)]


def test_python_sequence_and_scalar_facts_cross_the_retained_facade():
    from loopx.control_plane.todos.summary_item import compact_todo_summary_items

    row = {"todo_id": "todo_compat", "text": ("Check", True), "status": "open",
           "note": 1.0, "required_write_scopes": (True, 1.0, "docs/**", "../bad"),
           "excluded_agents": {"Agent B"}, "successor_todo_ids": ("todo_next",),
           "decision_scope": {"kind": "other", "granularity": "action", "scope_key": 1.0}}
    result = compact_todo_summary_items([row], strip_text=True)[0]
    assert result["text"] == "('Check', True)"
    assert result["continuation_hint"] == "1.0"
    assert result["required_write_scopes"] == ["True", "1.0", "docs/**"]
    assert result["excluded_agents"] == ["agent-b"]
    assert result["decision_scope"]["scope_key"] == "1.0"
    assert result["handoff_note"]["successor_todo_ids"] == ["todo_next"]
    assert row["excluded_agents"] == {"Agent B"}
    assert row["text"] == ("Check", True)


def test_explicit_null_text_override_preserves_raw_text_even_with_strip_requested():
    from loopx.control_plane.todos.summary_item import compact_todo_summary_items

    row = {"text": "  Work  ", "status": "open"}
    assert compact_todo_summary_items([row], texts=[None], strip_text=True)[0]["text"] == "  Work  "
    with pytest.raises(ValueError, match="cardinality"):
        compact_todo_summary_items([row], texts=[])
