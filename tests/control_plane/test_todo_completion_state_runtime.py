"""Persisted completion spellings decode without a field-level runtime call."""
from __future__ import annotations

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.todos import completion_state


@pytest.mark.parametrize(("raw", "expected"), [
    ("YES", True), ("no-followup", True), ("FALSE", False), (False, False),
    (None, None), ("unrecognized", None), ("\ufeffYES\ufeff", True), ("\x1cYES\x1c", None),
])
def test_metadata_spellings_do_not_start_the_runtime(monkeypatch, raw, expected):
    def unexpected(*args, **kwargs):
        raise AssertionError("metadata codecs must not call the runtime")

    monkeypatch.setattr(effect_runtime, "effect_runtime_request", unexpected)
    assert completion_state.normalize_todo_no_followup(raw) is expected


def test_completion_metadata_rejects_unknown_continuation_without_inventing_recovery():
    assert completion_state.require_todo_completion_metadata("completion_continuation", " successor ") == "successor"
    assert completion_state.require_todo_completion_metadata("completion_recovery", "") is None
    with pytest.raises(ValueError, match="completion_continuation must be one of"):
        completion_state.require_todo_completion_metadata("completion_continuation", "implicit")
