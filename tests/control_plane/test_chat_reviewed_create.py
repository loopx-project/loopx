"""Reviewed App creates must retain their revision through the actual write."""
import pytest

import loopx.chat as chat
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos.mutation_api import add_goal_todo


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("timing", ["before-confirmation", "after-final-preview"])
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
            if not kwargs["dry_run"]:
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
    assert [todo["text"] for todo in snapshot["todos"]] == ["Independent accepted request"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_review_creates_once_and_duplicate_remains_noop(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    for _ in range(2):
        preview = chat.build_todo_review_preview(
            registry_path=registry, goal_id="goal-a", text="Reviewed request")
        result = chat.apply_todo_review_preview(registry_path=registry, goal_id="goal-a",
            text="Reviewed request", preview_id=preview["preview_id"])
        assert result["applied"] is True
    snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert [todo["text"] for todo in snapshot["todos"]] == ["Reviewed request"]
