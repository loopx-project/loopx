"""Harbor's converted usage must preserve unknown fields and measured zeroes."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("harbor")
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import Agent, FinalMetrics, Step, Trajectory

from benchmark.runtime.harbor import BenchmarkCodex


FIELDS = {
    "prompt": "total_prompt_tokens",
    "completion": "total_completion_tokens",
    "cached": "total_cached_tokens",
    "cost": "total_cost_usd",
}
CONTEXT_FIELDS = {
    "prompt": "n_input_tokens",
    "completion": "n_output_tokens",
    "cached": "n_cache_tokens",
    "cost": "cost_usd",
}


def trajectory(metrics, session_id="synthetic-session"):
    return Trajectory(
        schema_version="ATIF-v1.5",
        session_id=session_id,
        agent=Agent(name="synthetic-agent", version="test"),
        steps=[Step(step_id=1, source="agent", message="Synthetic result.")],
        final_metrics=metrics,
    )


def measured(**changes):
    return FinalMetrics(**({
        "total_prompt_tokens": 100,
        "total_completion_tokens": 20,
        "total_cached_tokens": 80,
        "total_cost_usd": 0.25,
    } | changes))


@pytest.fixture
def adapter(tmp_path):
    return BenchmarkCodex(
        logs_dir=tmp_path, model_name="openai/synthetic-model", execution_mode="plain"
    )


def test_complete_totals_accept_a_generator_and_do_not_double_count_cache():
    items = [trajectory(measured()), trajectory(measured(), "second-session")]
    assert BenchmarkCodex._totals(iter(items)) == {
        "prompt": 200, "completion": 40, "cached": 160, "cost": 0.5,
    }


@pytest.mark.parametrize("missing_first", [False, True])
@pytest.mark.parametrize("key", FIELDS)
def test_a_partial_metric_is_unknown_without_hiding_other_fields(key, missing_first):
    items = [trajectory(measured()), trajectory(measured(**{FIELDS[key]: None}))]
    if missing_first:
        items.reverse()
    expected = {"prompt": 200, "completion": 40, "cached": 160, "cost": 0.5}
    expected[key] = None
    assert BenchmarkCodex._totals(items) == expected


@pytest.mark.parametrize("items", [[], [None], [None, measured()], [FinalMetrics()]])
def test_missing_evidence_is_not_zero(items):
    assert BenchmarkCodex._totals(trajectory(item) for item in items) == dict.fromkeys(FIELDS)


def test_explicit_zeroes_are_known_measurements():
    assert BenchmarkCodex._totals([
        trajectory(FinalMetrics(**dict.fromkeys(FIELDS.values(), 0)))
    ]) == dict.fromkeys(FIELDS, 0)


@pytest.mark.parametrize("key", FIELDS)
def test_negative_metrics_do_not_become_totals(key):
    result = BenchmarkCodex._totals([trajectory(measured(**{FIELDS[key]: -1}))])
    assert result[key] is None
    assert all(result[other] is not None for other in FIELDS if other != key)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_cost_is_unknown(value):
    result = BenchmarkCodex._totals([trajectory(measured(total_cost_usd=value))])
    assert result == {"prompt": 100, "completion": 20, "cached": 80, "cost": None}


def test_cost_sum_overflow_is_unknown():
    assert BenchmarkCodex._totals([
        trajectory(measured(total_cost_usd=1e308)),
        trajectory(measured(total_cost_usd=1e308), "second-session"),
    ])["cost"] is None


@pytest.mark.parametrize("key", FIELDS)
@pytest.mark.parametrize("baseline", [None, "missing", "regressed"])
def test_phase_delta_requires_both_observations(adapter, monkeypatch, key, baseline):
    monkeypatch.setattr(adapter, "_session_trajectories", lambda roots: [trajectory(measured())])
    before = {"prompt": 90, "completion": 15, "cached": 70, "cost": 0.125}
    if baseline == "missing":
        before.pop(key)
    elif baseline == "regressed":
        before[key] = 200
    else:
        before[key] = None
    context = AgentContext()
    adapter._populate_context(context, before)
    expected = {"prompt": 10, "completion": 5, "cached": 10, "cost": 0.125}
    expected[key] = None
    assert {k: getattr(context, v) for k, v in CONTEXT_FIELDS.items()} == expected


def test_empty_baseline_is_not_an_absent_baseline(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_session_trajectories", lambda roots: [trajectory(measured())])
    context = AgentContext()
    adapter._populate_context(context, {})
    assert all(getattr(context, field) is None for field in CONTEXT_FIELDS.values())


def test_post_run_without_evidence_does_not_publish_zeroes(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_session_trajectories", lambda roots: [])
    context = AgentContext()
    adapter.populate_context_post_run(context)
    assert all(getattr(context, field) is None for field in CONTEXT_FIELDS.values())
    assert not (adapter.logs_dir / "trajectory.json").exists()


@pytest.mark.parametrize("unknown", [False, True])
def test_context_and_serialized_trajectory_share_one_observation(adapter, monkeypatch, unknown):
    metric = FinalMetrics(**dict.fromkeys(FIELDS.values(), 0))
    items = [trajectory(metric)]
    if unknown:
        items.append(trajectory(measured(total_cost_usd=None), "second-session"))
    calls = []

    def read_once(roots):
        calls.append(roots)
        assert len(calls) == 1, "context and aggregate must use the same parsed snapshot"
        return items

    monkeypatch.setattr(adapter, "_session_trajectories", read_once)
    context = AgentContext()
    adapter.populate_context_post_run(context)
    saved = json.loads((adapter.logs_dir / "trajectory.json").read_text(encoding="utf-8"))
    final = saved["final_metrics"]
    for key, field in FIELDS.items():
        expected = None if unknown and key == "cost" else (getattr(measured(), field) if unknown else 0)
        assert getattr(context, CONTEXT_FIELDS[key]) == expected
        assert final.get(field) == expected
        if expected == 0:
            assert field in final
    assert final["extra"]["native_sessions"] == len(items)
    assert [step["step_id"] for step in saved["steps"]] == list(range(1, len(items) + 1))
    assert all(item.steps[0].step_id == 1 for item in items)


def test_unchanged_phase_reports_known_zero_without_overwriting_cumulative_total(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_session_trajectories", lambda roots: [trajectory(measured())])
    context = AgentContext()
    adapter._populate_context(context, {"prompt": 100, "completion": 20, "cached": 80, "cost": 0.25})
    assert {k: getattr(context, v) for k, v in CONTEXT_FIELDS.items()} == dict.fromkeys(FIELDS, 0)
    final = json.loads((adapter.logs_dir / "trajectory.json").read_text(encoding="utf-8"))["final_metrics"]
    assert final["total_prompt_tokens"] == 100
    assert final["total_cost_usd"] == 0.25


@pytest.mark.parametrize("existing_rollout", [False, True])
def test_run_distinguishes_empty_start_from_an_unconverted_rollout(adapter, monkeypatch, existing_rollout):
    if existing_rollout:
        sessions = adapter.logs_dir / "sessions"
        sessions.mkdir()
        (sessions / "unconverted.jsonl").write_text("{}\n", encoding="utf-8")
    calls = []

    def snapshots(roots):
        calls.append(roots)
        return [] if len(calls) == 1 else [trajectory(measured())]

    async def pwd(*args, **kwargs):
        return SimpleNamespace(stdout="/synthetic-task", return_code=0)

    async def fail_preparation(*args, **kwargs):
        raise RuntimeError("synthetic preparation failure")

    monkeypatch.setattr(adapter, "_session_trajectories", snapshots)
    monkeypatch.setattr(adapter, "exec_as_agent", pwd)
    monkeypatch.setattr(adapter, "_write_task_document", fail_preparation)
    context = AgentContext()
    with pytest.raises(RuntimeError, match="synthetic preparation failure"):
        asyncio.run(adapter.run("Synthetic task.", SimpleNamespace(is_mounted=True), context))
    assert len(calls) == 2
    expected = dict.fromkeys(FIELDS) if existing_rollout else {
        "prompt": 100, "completion": 20, "cached": 80, "cost": 0.25,
    }
    assert {k: getattr(context, v) for k, v in CONTEXT_FIELDS.items()} == expected
