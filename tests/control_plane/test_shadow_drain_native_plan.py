"""The Python entry transports one batch, with no source/history decision owner.

Filesystem mutation and failure-window cases moved to shadow_drain.test.ts and
real TS-process SIGKILL tests; patching retired Python internals proves nothing.
"""
from __future__ import annotations

from pathlib import Path
import json
from typing import Any

import pytest

from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter
from test_local_authority_shadow_drain import _drain, _fixture, _record_todo_write


def test_disabled_without_capture_state_does_not_start_native_runtime(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "goal-e2e"}]}))

    def unexpected(*args, **kwargs):
        raise AssertionError("feature-off drain must not invoke the runtime")

    monkeypatch.setattr(adapter, "effect_runtime_result", unexpected)
    result = _drain(registry, tmp_path / "runtime")
    assert result.ok and result.outcome == "nothing_pending"
    assert not (tmp_path / "runtime").exists()


def test_adapter_transports_one_batch_without_per_entry_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state, runtime = _fixture(tmp_path)
    for i in range(3):
        _record_todo_write(registry, state, runtime, f"Private source text {i}")
    actual = adapter.effect_runtime_result
    seen = []

    def rpc(method: str, request: dict, **kwargs: Any) -> dict:
        result = actual(method, request, **kwargs)
        seen.append((method, request, result, kwargs))
        return result

    monkeypatch.setattr(adapter, "effect_runtime_result", rpc)
    result = _drain(registry, runtime)
    assert result.ok and result.delivered == 3
    assert len(seen) == 1
    method, request, response, kwargs = seen[0]
    assert method == "coordination.runtime_shadow.drain"
    assert kwargs["retry_safe"] is False
    assert "projection" not in request and "entries" not in request
    assert "proof" not in response and "transactions" not in response and "head" not in response


def test_lost_batch_response_reports_unknown_and_explicit_retry_reads_durable_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, state, runtime = _fixture(tmp_path)
    _record_todo_write(registry, state, runtime, "Retain this committed write")
    actual = adapter.effect_runtime_result

    def lose_response(method: str, request: dict, **kwargs: Any) -> dict:
        actual(method, request, **kwargs)
        raise TimeoutError("lost response after successful native drain")

    monkeypatch.setattr(adapter, "effect_runtime_result", lose_response)
    failed = _drain(registry, runtime)
    assert not failed.ok and failed.reason_code == "shadow_drain_outcome_unknown"
    monkeypatch.setattr(adapter, "effect_runtime_result", actual)
    recovered = _drain(registry, runtime)
    assert recovered.ok and recovered.outcome == "nothing_pending"
    view = adapter.read_local_authority_shadow(runtime_root=runtime, goal_id="goal-e2e", scan_limit=10)
    assert len(view["proof"]["transactions"]) == 2
