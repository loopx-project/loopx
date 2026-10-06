from __future__ import annotations

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.todos import completion_state


def _result(**values: object) -> dict[str, object]:
    return {
        "schema_version": completion_state.TODO_COMPLETION_STATE_RESULT_SCHEMA,
        **values,
    }


def test_scalar_normalization_uses_bounded_process_cache(monkeypatch) -> None:
    calls = 0

    def call(_method: str, params: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _result(kind=params["kind"], value=True)

    completion_state._normalize_cached.cache_clear()
    monkeypatch.setattr(completion_state, "effect_runtime_result", call)

    assert completion_state.normalize_todo_no_followup("YES") is True
    assert completion_state.normalize_todo_no_followup("YES") is True
    assert calls == 1
    completion_state._normalize_cached.cache_clear()


def test_python_facade_preserves_typed_rejection_as_value_error(monkeypatch) -> None:
    def reject(_method: str, _params: dict[str, object]) -> object:
        raise effect_runtime.EffectRuntimeRejected("typed state rejected")

    monkeypatch.setattr(completion_state, "effect_runtime_result", reject)

    with pytest.raises(ValueError, match="typed state rejected"):
        completion_state.require_todo_completion_metadata(
            "completion_continuation", "implicit",
        )
