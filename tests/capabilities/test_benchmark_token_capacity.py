from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from loopx.capabilities.benchmark_toolkit import token_capacity

from loopx.capabilities.benchmark_toolkit import (
    BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION,
    admit_benchmark_token_capacity,
    build_benchmark_token_capacity_policy,
    build_benchmark_token_capacity_status,
    configure_benchmark_token_capacity,
    normalize_benchmark_resource_headroom_receipt,
    observe_benchmark_token_usage,
    read_benchmark_token_capacity_state,
    record_benchmark_token_capacity_rejection,
    release_benchmark_token_capacity,
)
from tests.source_cli import run_source_cli


def _sample(
    sample_id: str,
    observed_at: datetime,
    *,
    metering_state: str = "measured",
    requests: int = 1,
    uncached: int = 0,
    cached: int = 0,
    output: int = 0,
) -> dict[str, object]:
    return {
        "schema_version": BENCHMARK_TOKEN_USAGE_SAMPLE_SCHEMA_VERSION,
        "sample_id": sample_id,
        "observed_at": observed_at.isoformat(),
        "metering_state": metering_state,
        "request_count": requests,
        "uncached_input_tokens": uncached,
        "cached_input_tokens": cached,
        "output_tokens": output,
    }


def _configure(path: Path, *, now: datetime) -> None:
    configured = configure_benchmark_token_capacity(
        path,
        build_benchmark_token_capacity_policy(
            quota_tokens_per_minute=10_000_000,
            target_utilization_basis_points=9000,
            cold_start_reserve_tokens_per_minute=500_000,
            evaluator_reserve_tokens_per_minute=1_000_000,
            cooldown_seconds=90,
        ),
        execute=True,
        observed_at=now.isoformat(),
    )
    assert configured["write_performed"] is True


def test_exact_window_and_disjoint_cache_accounting(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    path = tmp_path / "token-capacity.json"
    _configure(path, now=now - timedelta(minutes=2))

    samples = [
        _sample(
            "expired-boundary",
            now - timedelta(seconds=60),
            uncached=9_000_000,
        ),
        _sample(
            "inside-window",
            now - timedelta(seconds=59),
            uncached=1_000_000,
            cached=2_000_000,
            output=500_000,
        ),
        _sample("coverage-heartbeat", now, requests=0),
    ]
    for sample in samples:
        result = observe_benchmark_token_usage(
            path, sample, execute=True, recorded_at=now.isoformat()
        )
        assert result["recorded"] is True

    status = build_benchmark_token_capacity_status(
        read_benchmark_token_capacity_state(path), observed_at=now.isoformat()
    )
    assert status["usage"] == {
        "window_seconds": 60,
        "window_started_at": (now - timedelta(seconds=60))
        .isoformat()
        .replace("+00:00", "Z"),
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "coverage": "complete_measured",
        "reason_codes": [],
        "sample_count": 2,
        "request_count": 1,
        "uncached_input_tokens": 1_000_000,
        "cached_input_tokens": 2_000_000,
        "output_tokens": 500_000,
        "tokens": 3_500_000,
    }


def test_partial_unmetered_traffic_fails_closed(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    path = tmp_path / "token-capacity.json"
    _configure(path, now=now - timedelta(minutes=1))
    observe_benchmark_token_usage(
        path,
        _sample("measured", now, uncached=100),
        execute=True,
        recorded_at=now.isoformat(),
    )
    observe_benchmark_token_usage(
        path,
        _sample("unknown", now, metering_state="unmetered"),
        execute=True,
        recorded_at=now.isoformat(),
    )

    decision = admit_benchmark_token_capacity(
        path,
        run_id="solver-a",
        workload_class="solver",
        tokens_per_minute=1_000_000,
        execute=True,
        admitted_at=now.isoformat(),
    )
    assert decision["admitted"] is False
    assert decision["reason_codes"] == ["token_usage_partial_unmetered"]
    receipt = normalize_benchmark_resource_headroom_receipt(
        decision["resource_headroom_receipt"]
    )
    assert receipt["checks"] == [{"kind": "provider_capacity", "state": "unresolved"}]
    assert read_benchmark_token_capacity_state(path)["reservations"] == []


def test_future_usage_sample_is_retained_and_fails_closed(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    path = tmp_path / "token-capacity.json"
    _configure(path, now=now - timedelta(minutes=1))
    observed = observe_benchmark_token_usage(
        path,
        _sample("clock-skew", now + timedelta(seconds=1), uncached=100),
        execute=True,
        recorded_at=now.isoformat(),
    )
    decision = admit_benchmark_token_capacity(
        path,
        run_id="solver-a",
        workload_class="solver",
        tokens_per_minute=1_000_000,
        execute=True,
        admitted_at=now.isoformat(),
    )

    assert observed["usage"]["coverage"] == "unresolved_future_sample"
    assert decision["reason_codes"] == ["token_usage_sample_from_future"]


def test_complete_estimated_usage_is_counted_and_visible(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    path = tmp_path / "token-capacity.json"
    _configure(path, now=now - timedelta(minutes=1))
    observe_benchmark_token_usage(
        path,
        _sample(
            "estimated",
            now,
            metering_state="estimated",
            uncached=1_000_000,
            cached=500_000,
            output=500_000,
        ),
        execute=True,
        recorded_at=now.isoformat(),
    )
    decision = admit_benchmark_token_capacity(
        path,
        run_id="solver-a",
        workload_class="solver",
        tokens_per_minute=1_000_000,
        execute=True,
        admitted_at=now.isoformat(),
    )

    assert decision["admitted"] is True
    assert decision["coverage"] == "complete_estimated"
    assert decision["rolling_usage_tokens_per_minute"] == 2_000_000


def test_429_cooldown_holds_then_recovers_without_losing_state(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    path = tmp_path / "token-capacity.json"
    _configure(path, now=now - timedelta(minutes=1))
    observe_benchmark_token_usage(
        path,
        _sample("healthy", now, requests=0),
        execute=True,
        recorded_at=now.isoformat(),
    )
    rejected = record_benchmark_token_capacity_rejection(
        path,
        retry_after_seconds=120,
        execute=True,
        observed_at=now.isoformat(),
    )
    assert rejected["cooldown"] == {
        "active": True,
        "until": (now + timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
    }

    during_cooldown = now + timedelta(seconds=60)
    observe_benchmark_token_usage(
        path,
        _sample("still-metered", during_cooldown, requests=0),
        execute=True,
        recorded_at=during_cooldown.isoformat(),
    )
    held = admit_benchmark_token_capacity(
        path,
        run_id="solver-a",
        workload_class="solver",
        tokens_per_minute=1_000_000,
        execute=True,
        admitted_at=during_cooldown.isoformat(),
    )
    assert held["reason_codes"] == ["provider_capacity_cooldown"]

    recovered_at = now + timedelta(seconds=121)
    observe_benchmark_token_usage(
        path,
        _sample("recovered", recovered_at, requests=0),
        execute=True,
        recorded_at=recovered_at.isoformat(),
    )
    admitted = admit_benchmark_token_capacity(
        path,
        run_id="solver-a",
        workload_class="solver",
        tokens_per_minute=1_000_000,
        execute=True,
        admitted_at=recovered_at.isoformat(),
    )
    assert admitted["admitted"] is True
    assert admitted["write_performed"] is True


def test_evaluator_reserve_cannot_be_consumed_by_solver(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    path = tmp_path / "token-capacity.json"
    _configure(path, now=now - timedelta(minutes=1))
    observe_benchmark_token_usage(
        path,
        _sample("healthy", now, requests=0),
        execute=True,
        recorded_at=now.isoformat(),
    )

    solver = admit_benchmark_token_capacity(
        path,
        run_id="solver-a",
        workload_class="solver",
        tokens_per_minute=7_500_000,
        execute=True,
        admitted_at=now.isoformat(),
    )
    blocked_solver = admit_benchmark_token_capacity(
        path,
        run_id="solver-b",
        workload_class="solver",
        tokens_per_minute=1,
        execute=True,
        admitted_at=now.isoformat(),
    )
    evaluator = admit_benchmark_token_capacity(
        path,
        run_id="evaluator-a",
        workload_class="evaluator",
        tokens_per_minute=1_000_000,
        execute=True,
        admitted_at=now.isoformat(),
    )

    assert solver["admitted"] is True
    assert solver["admission_limit_tokens_per_minute"] == 7_500_000
    assert blocked_solver["reason_codes"] == ["token_capacity_exhausted"]
    assert evaluator["admitted"] is True
    assert evaluator["admission_limit_tokens_per_minute"] == 8_500_000


def test_restart_preserves_reservations_and_idempotent_release(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    path = tmp_path / "token-capacity.json"
    _configure(path, now=now - timedelta(minutes=1))
    observe_benchmark_token_usage(
        path,
        _sample("healthy", now, requests=0),
        execute=True,
        recorded_at=now.isoformat(),
    )
    first = admit_benchmark_token_capacity(
        path,
        run_id="solver-a",
        workload_class="solver",
        tokens_per_minute=2_000_000,
        execute=True,
        admitted_at=now.isoformat(),
    )
    restored = read_benchmark_token_capacity_state(path)
    repeated = admit_benchmark_token_capacity(
        path,
        run_id="solver-a",
        workload_class="solver",
        tokens_per_minute=2_000_000,
        execute=True,
        admitted_at=now.isoformat(),
    )
    status = build_benchmark_token_capacity_status(
        restored, observed_at=now.isoformat()
    )

    assert first["write_performed"] is True
    assert repeated["idempotent"] is True
    assert repeated["write_performed"] is False
    assert status["reservations"]["total"] == 2_000_000
    assert status["capacity_basis_tokens_per_minute"] == 2_000_000

    released = release_benchmark_token_capacity(
        path,
        run_id="solver-a",
        execute=True,
        released_at=(now + timedelta(seconds=1)).isoformat(),
    )
    repeated_release = release_benchmark_token_capacity(
        path,
        run_id="solver-a",
        execute=True,
        released_at=(now + timedelta(seconds=2)).isoformat(),
    )
    assert released["write_performed"] is True
    assert repeated_release["idempotent"] is True


def test_parallel_admission_is_atomic_at_token_limit(tmp_path: Path) -> None:
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    path = tmp_path / "token-capacity.json"
    _configure(path, now=now - timedelta(minutes=1))
    observe_benchmark_token_usage(
        path,
        _sample("healthy", now, requests=0),
        execute=True,
        recorded_at=now.isoformat(),
    )

    def reserve(index: int) -> dict[str, object]:
        return admit_benchmark_token_capacity(
            path,
            run_id=f"solver-{index}",
            workload_class="solver",
            tokens_per_minute=3_000_000,
            execute=True,
            admitted_at=now.isoformat(),
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        decisions = list(executor.map(reserve, range(4)))

    assert sum(decision["admitted"] is True for decision in decisions) == 2
    state = read_benchmark_token_capacity_state(path)
    assert state is not None
    assert len(state["reservations"]) == 2


def _mutate(path: Path, operation: str, run_id: str, **kwargs):
    if operation == "configure":
        return configure_benchmark_token_capacity(
            path,
            build_benchmark_token_capacity_policy(quota_tokens_per_minute=10_000_000),
            **kwargs,
        )
    if operation == "reject":
        return record_benchmark_token_capacity_rejection(path, **kwargs)
    if operation == "release":
        return release_benchmark_token_capacity(path, run_id=run_id, **kwargs)
    return admit_benchmark_token_capacity(
        path, run_id=run_id, workload_class="solver", tokens_per_minute=100, **kwargs
    )


@pytest.mark.parametrize("operation", ("configure", "reject", "admit", "release"))
def test_implicit_time_follows_lock_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    path = tmp_path / "token-capacity.json"
    now = datetime.now(UTC) - timedelta(seconds=1)
    _configure(path, now=now)
    observe_benchmark_token_usage(
        path, _sample("healthy", now, requests=0), execute=True
    )
    if operation == "release":
        for run_id in ("solver-a", "solver-b"):
            _mutate(path, "admit", run_id, execute=True)

    waiting, resume = Event(), Event()
    real_lock = token_capacity.exclusive_file_lock

    @contextmanager
    def reverse_lock(*args, **kwargs):
        if kwargs.get("agent_id") == "delayed":
            waiting.set()
            assert resume.wait(10), "later caller did not finish"
        with real_lock(*args, **kwargs):
            yield

    monkeypatch.setattr(token_capacity, "exclusive_file_lock", reverse_lock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        delayed = executor.submit(
            _mutate, path, operation, "solver-a", execute=True, agent_id="delayed"
        )
        try:
            assert waiting.wait(10), "first caller did not reach the lock"
            ahead = _mutate(path, operation, "solver-b", execute=True)
            assert ahead["write_performed"] is True
        finally:
            resume.set()
        assert delayed.result(timeout=10)["write_performed"] is True

    state = read_benchmark_token_capacity_state(path)
    assert state is not None
    if operation == "admit":
        assert {item["run_id"] for item in state["reservations"]} == {
            "solver-a",
            "solver-b",
        }
    elif operation == "release":
        assert state["reservations"] == []
    elif operation == "reject":
        assert (
            build_benchmark_token_capacity_status(state)["cooldown"]["active"] is True
        )


@pytest.mark.parametrize(
    ("operation", "time_field"),
    (
        ("configure", "observed_at"),
        ("reject", "observed_at"),
        ("admit", "admitted_at"),
        ("release", "released_at"),
    ),
)
@pytest.mark.parametrize("execute", (False, True))
def test_explicit_backward_mutation_time_is_rejected(
    tmp_path: Path, operation: str, time_field: str, execute: bool
) -> None:
    path = tmp_path / "token-capacity.json"
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    _configure(path, now=now)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="cannot move state backward"):
        _mutate(
            path,
            operation,
            "solver-a",
            execute=execute,
            **{time_field: (now - timedelta(microseconds=1)).isoformat()},
        )
    assert path.read_bytes() == before


def test_cli_composes_token_receipt_with_existing_headroom_contract(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    configured = run_source_cli(
        [
            "benchmark",
            "token-capacity-configure",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--quota-tokens-per-minute",
            "10000000",
            "--target-utilization-basis-points",
            "9000",
            "--evaluator-reserve-tokens-per-minute",
            "1000000",
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )
    assert json.loads(configured.stdout)["write_performed"] is True

    sample_path = tmp_path / "sample.json"
    sample_path.write_text(
        json.dumps(_sample("cli-coverage", now, requests=0)), encoding="utf-8"
    )
    observed = run_source_cli(
        [
            "benchmark",
            "token-capacity-observe",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--sample-json",
            str(sample_path),
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )
    assert json.loads(observed.stdout)["usage"]["coverage"] == "complete_measured"

    admitted = run_source_cli(
        [
            "benchmark",
            "token-capacity-admit",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--run-id",
            "solver-cli",
            "--workload-class",
            "solver",
            "--tokens-per-minute",
            "1000000",
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )
    payload = json.loads(admitted.stdout)
    assert payload["admitted"] is True
    assert payload["resource_headroom_receipt"]["checks"] == [
        {"kind": "provider_capacity", "state": "sufficient"}
    ]
    assert str(tmp_path) not in admitted.stdout

    run_source_cli(
        [
            "benchmark",
            "concurrency-configure",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--max-active-cases",
            "1",
            "--max-baseline-cases",
            "1",
            "--max-test-cases",
            "1",
            "--require-resource-headroom-receipt",
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )
    receipt_path = tmp_path / "headroom.json"
    receipt_path.write_text(
        json.dumps(payload["resource_headroom_receipt"]), encoding="utf-8"
    )
    case_admission = run_source_cli(
        [
            "benchmark",
            "concurrency-admit",
            "--goal-id",
            "fixture-goal",
            "--project",
            str(tmp_path),
            "--run-id",
            "solver-cli",
            "--case-id",
            "case-cli",
            "--arm-role",
            "treatment",
            "--resource-headroom-json",
            str(receipt_path),
            "--execute",
            "--format",
            "json",
        ],
        check=True,
    )
    assert json.loads(case_admission.stdout)["admitted"] is True


def test_cli_without_token_policy_retains_case_admission(tmp_path: Path) -> None:
    catalog = json.loads(
        run_source_cli(
            ["capability", "show", "benchmark-toolkit", "--format", "json"]
        ).stdout
    )["capability"]["agent_usage"]
    for prefix in (
        "record_complete_or_explicitly_unmetered_token_usage",
        "atomically_reserve_token_capacity_before_case_slot_admission",
        "release_token_capacity_after_terminal_or_runner_invalid_transition",
    ):
        (step,) = [
            step for step in catalog["required_sequence"] if step.startswith(prefix)
        ]
        assert step.endswith("_when_token_capacity_is_configured")

    location = ["--goal-id", "fixture-goal", "--project", str(tmp_path)]
    execution = ["--execute", "--format", "json"]
    status = json.loads(
        run_source_cli(
            ["benchmark", "token-capacity-status", *location, "--format", "json"]
        ).stdout
    )
    assert status["configured"] is False
    run_source_cli(
        [
            "benchmark",
            "concurrency-configure",
            *location,
            "--max-active-cases",
            "1",
            "--max-baseline-cases",
            "1",
            "--max-test-cases",
            "1",
            *execution,
        ]
    )
    admitted = json.loads(
        run_source_cli(
            [
                "benchmark",
                "concurrency-admit",
                *location,
                "--run-id",
                "solver-cli",
                "--case-id",
                "case-cli",
                "--arm-role",
                "treatment",
                *execution,
            ]
        ).stdout
    )
    assert admitted["admitted"] is True
    path = token_capacity.default_benchmark_token_capacity_path(
        project=tmp_path, goal_id="fixture-goal"
    )
    assert not path.exists()
