"""Reviewed App creates must retain their revision through the actual write."""
import pytest

import loopx.chat as chat
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.mutation_api import add_goal_todo


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("timing", ["before-confirmation", "between-preview-reads", "after-final-preview"])
def test_concurrent_create_refuses_reviewed_intent(tmp_path, monkeypatch, provider, timing):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    preview = chat.build_todo_review_preview(
        registry_path=registry, goal_id="goal-a", text="Reviewed request")

    def independent_write():
        add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
            text="Independent accepted request")

    if timing == "before-confirmation":
        independent_write()
    else:
        original = chat._add_review_todo

        def interleaved(**kwargs):
            if (timing == "after-final-preview" and not kwargs["dry_run"]) or (
                timing == "between-preview-reads" and kwargs["dry_run"] and kwargs.get("operation_id")
            ):
                independent_write()
            return original(**kwargs)

        monkeypatch.setattr(chat, "_add_review_todo", interleaved)
    with pytest.raises(chat.TodoReviewPreviewConflict) as conflict:
        chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
            text="Reviewed request", preview_id=preview["preview_id"])
    assert conflict.value.receipt["status"] == "not_applied"
    assert conflict.value.receipt["write_attempted"] is (timing == "after-final-preview")
    assert ("current_preview_id" in conflict.value.receipt) is (timing == "before-confirmation")
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert [todo["text"] for todo in snapshot["todos"]] == ["[P1] Independent accepted request"]
    if timing != "before-confirmation":
        monkeypatch.setattr(chat, "_add_review_todo", original)
    refreshed = chat.build_todo_review_preview(
        registry_path=registry, goal_id="goal-a", text="Reviewed request")
    applied = chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
        text="Reviewed request", preview_id=refreshed["preview_id"])
    assert applied["todo"]["todo_id"] == refreshed["todo"]["todo_id"]
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert {todo["text"] for todo in snapshot["todos"]} == {"[P1] Independent accepted request", "[P1] Reviewed request"}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_review_creates_once_and_duplicate_remains_noop(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    for _ in range(2):
        before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
        preview = chat.build_todo_review_preview(
            registry_path=registry, goal_id="goal-a", text="Reviewed request")
        assert chat.build_todo_review_preview(
            registry_path=registry, goal_id="goal-a", text="Reviewed request") == preview
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
        result = chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
            text="Reviewed request", preview_id=preview["preview_id"])
        assert result["applied"] is True
        assert result["todo"]["todo_id"] == preview["todo"]["todo_id"]
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert [todo["text"] for todo in snapshot["todos"]] == ["[P1] Reviewed request"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_changed_intent_cannot_apply_another_preview(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    preview = chat.build_todo_review_preview(
        registry_path=registry, goal_id="goal-a", text="Reviewed request")
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    for changed in ({"text": "Different request"}, {"text": "Reviewed request", "priority": "P0"}):
        with pytest.raises(chat.TodoReviewPreviewConflict):
            chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
                preview_id=preview["preview_id"], **changed)
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
