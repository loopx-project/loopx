from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import loopx.goal_portfolio as portfolio
from loopx.control_plane.runtime.run_context_retention import (
    compact_goal_semantic_history,
    goal_semantic_history_from_runs,
)
from loopx.status import compact_run

NOW = datetime(2026, 9, 10, 6, tzinfo=timezone.utc)


def registry(tmp_path, ids=("alpha", "beta", "gamma")):
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "goals": [
                    {"id": i, "coordination": {"registered_agents": ["worker"]}}
                    for i in ids
                ]
            }
        )
    )
    return path


def status(goal_id, *, stamp="2026-09-10T05:00:00Z"):
    delivery = {
        "goal_id": goal_id,
        "agent_id": "worker",
        "todo_id": "todo-delivery",
        "generated_at": stamp,
        "delivery_outcome": "outcome_progress",
        "progress_observation": {
            "schema_version": "typed_progress_observation_v0",
            "result_class": "advanced",
            "work_item_id": "todo-delivery",
            "evidence_ids": ["private-source-body-is-not-for-export"],
        },
    }
    runs = [
        {
            "goal_id": goal_id,
            "agent_id": "worker",
            "generated_at": stamp,
            "classification": "quota_slot_spent",
        }
        for _ in range(1000)
    ] + [delivery]
    history = compact_goal_semantic_history(
        goal_semantic_history_from_runs(runs), compact_run=compact_run
    )
    return {
        "ok": True,
        "run_history": {
            "goals": [
                {
                    "id": goal_id,
                    "latest_status_run": delivery,
                    "latest_runs": runs[:2],
                    "semantic_history": history,
                }
            ]
        },
    }


@pytest.fixture
def reads(monkeypatch):
    calls = []

    def read(**kwargs):
        selected = kwargs.get("goal_id")
        calls.append(selected)
        if selected is None:
            ids = [
                g["id"]
                for g in json.loads(kwargs["registry_path"].read_text())["goals"]
            ]
            return {
                "ok": True,
                "run_history": {
                    "goals": [status(g)["run_history"]["goals"][0] for g in ids]
                },
            }
        return status(selected)

    monkeypatch.setattr(portfolio, "collect_status", read)
    monkeypatch.setattr(
        portfolio,
        "build_quota_should_run",
        lambda *a, **k: {
            "ok": True,
            "agent_identity": {"agent_id": "worker"},
            "waiting_on": "codex",
            "normal_delivery_allowed": True,
            "selected_todo": {
                "todo_id": "todo-run",
                "goal_id": k["goal_id"],
                "status": "open",
                "role": "agent",
            },
        },
    )
    return calls


def test_inventory_scope_and_limits_are_not_an_attention_queue(tmp_path, reads):
    path = registry(tmp_path)
    before = path.read_bytes()
    result = portfolio.build_goal_portfolio(registry_path=path, limit=1, now=NOW)
    assert reads == ["alpha"]
    assert result["coverage"]["discovered"] == 3
    assert result["coverage"]["verified"] == 1
    assert result["coverage"]["omitted"] == 2
    assert not result["coverage"]["complete"]
    assert path.read_bytes() == before
    reads.clear()
    scoped = portfolio.build_goal_portfolio(
        registry_path=path, goal_ids=["beta"], now=NOW
    )
    assert reads == ["beta"]
    assert scoped["coverage"]["complete"]
    assert "private-source-body" not in json.dumps(scoped)
    assert scoped["goals"][0]["host_id"] is None


def test_quota_noise_does_not_evict_recorded_delivery(tmp_path, reads):
    result = portfolio.build_goal_portfolio(
        registry_path=registry(tmp_path, ("alpha",)), now=NOW
    )
    row = result["goals"][0]
    assert row["deliveries"][0]["todo_id"] == "todo-delivery"
    assert row["deliveries"][0]["evidence_refs"]
    assert row["deliveries"][0]["verification"] == "core_recorded_evidence_refs"
    assert row["agents"][0]["todos"][0]["readiness"] == "runnable"


LIFECYCLE_FIXTURE = {
    "schema_version": "goal_artifact_lifecycle_projection_v0",
    "goal_id": "alpha",
    "lifecycle_phase": "qualifying",
    "milestones": [
        {
            "id": "outcome_progress",
            "label": "advance the Goal",
            "reached": True,
            "reached_evidence_refs": ["sha256:fixture"],
            "source": "evidence",
        }
    ],
    "guards": [],
    "next_transitions": [],
}


def lifecycle_reader(projection: dict | None):
    """A collector stand-in that attaches, or withholds, the lifecycle key.

    It answers the shared all-Goals read too, because a two-Goal portfolio
    inside the collection limit is collected once.
    """

    def record(goal_id):
        entry = status(goal_id)["run_history"]["goals"][0]
        if projection is not None:
            entry["artifact_lifecycle"] = {**deepcopy(projection), "goal_id": goal_id}
        return entry

    def read(**kwargs):
        selected = kwargs.get("goal_id")
        ids = [selected] if selected else [
            g["id"] for g in json.loads(kwargs["registry_path"].read_text())["goals"]
        ]
        return {"ok": True, "run_history": {"goals": [record(i) for i in ids]}}

    return read


def test_lifecycle_readback_is_opt_in_and_never_a_silent_gap(
    tmp_path, reads, monkeypatch
):
    """The manager cannot read a missing projection as "no milestones".

    Default callers stay byte-for-byte unaffected; a caller that asks for the
    lifecycle either receives the collector's own projection or a typed gap
    naming why, including for a Goal this reader excluded before any read.
    """

    path = registry(tmp_path, ("alpha", "beta"))
    monkeypatch.setattr(
        portfolio, "collect_status", lifecycle_reader(LIFECYCLE_FIXTURE)
    )
    default = portfolio.build_goal_portfolio(registry_path=path, now=NOW)
    assert all("goal_lifecycle" not in row for row in default["goals"]), default
    assert "Goal lifecycle readback" not in json.dumps(default)

    requested = portfolio.build_goal_portfolio(
        registry_path=path, now=NOW, include_goal_lifecycle=True
    )
    assert requested["goals"][0]["goal_lifecycle"] == {
        **LIFECYCLE_FIXTURE,
        "goal_id": "alpha",
    }
    assert "Goal lifecycle readback" in requested["limitations"][-1]
    assert "goal_lifecycle" not in default["limitations"][-1]


def test_lifecycle_readback_names_every_unavailable_reason(
    tmp_path, reads, monkeypatch
):
    path = registry(tmp_path, ("alpha", "beta"))
    monkeypatch.setattr(portfolio, "collect_status", lifecycle_reader(None))
    derived_absent = portfolio.build_goal_portfolio(
        registry_path=path, now=NOW, include_goal_lifecycle=True
    )
    reasons = {
        row["goal_lifecycle"]["reason"] for row in derived_absent["goals"]
    }
    assert reasons == {"projection_not_derived"}
    assert all(
        row["goal_lifecycle"]["schema_version"]
        == portfolio.GOAL_LIFECYCLE_READBACK_SCHEMA_VERSION
        and row["goal_lifecycle"]["status"] == "unavailable"
        for row in derived_absent["goals"]
    )

    limited = portfolio.build_goal_portfolio(
        registry_path=path, limit=1, now=NOW, include_goal_lifecycle=True
    )
    assert [row["goal_lifecycle"]["reason"] for row in limited["goals"]] == [
        "projection_not_derived",
        "goal_not_read",
    ]

    attach = lifecycle_reader(LIFECYCLE_FIXTURE)

    def changed_read(**kwargs):
        path.write_text(path.read_text() + "\n")
        return attach(**kwargs)

    monkeypatch.setattr(portfolio, "collect_status", changed_read)
    changed = portfolio.build_goal_portfolio(
        registry_path=path, now=NOW, include_goal_lifecycle=True
    )
    assert [row["goal_lifecycle"]["reason"] for row in changed["goals"]] == [
        "inventory_changed_during_collection"
    ] * 2
    # A projection derived from a source that changed mid-read must not survive
    # as a phase the manager would report as current.
    assert "lifecycle_phase" not in json.dumps(changed)

    monkeypatch.setattr(
        portfolio, "collect_status", lifecycle_reader(LIFECYCLE_FIXTURE)
    )
    for bad in (1, "yes", None):
        with pytest.raises(ValueError):
            portfolio.build_goal_portfolio(
                registry_path=path, now=NOW, include_goal_lifecycle=bad
            )


@pytest.mark.parametrize(
    "mode,quality",
    [
        ("stale", "stale"),
        ("future", "conflicting"),
        ("unreadable", "unreadable"),
        ("changed", "conflicting"),
    ],
)
def test_bad_sources_never_claim_no_progress(
    tmp_path, reads, monkeypatch, mode, quality
):
    path = registry(tmp_path, ("alpha",))

    def read(**kwargs):
        if mode == "unreadable":
            raise OSError("private credential-bearing path")
        if mode == "changed":
            path.write_text(path.read_text() + "\n")
        stamp = (
            "2020-01-01T00:00:00Z"
            if mode == "stale"
            else "2030-01-01T00:00:00Z"
            if mode == "future"
            else "2026-09-10T05:00:00Z"
        )
        return status(kwargs["goal_id"], stamp=stamp)

    monkeypatch.setattr(portfolio, "collect_status", read)
    result = portfolio.build_goal_portfolio(registry_path=path, now=NOW)
    assert result["goals"][0]["quality"] == quality
    assert result["goals"][0]["progress"] == "unknown"
    assert not result["coverage"]["complete"]
    assert "credential-bearing" not in json.dumps(result)


def test_empty_missing_duplicate_and_malformed_inventory_are_not_healthy(
    tmp_path, reads
):
    empty = portfolio.build_goal_portfolio(
        registry_path=registry(tmp_path, ()), now=NOW
    )
    assert not empty["coverage"]["complete"]
    assert "empty_inventory" in empty["warnings"]
    result = portfolio.build_goal_portfolio(
        registry_path=registry(tmp_path, ("alpha", "alpha")),
        goal_ids=["alpha", "missing"],
        now=NOW,
    )
    assert result["coverage"]["conflicting"] == 1
    assert result["scope"]["missing_goal_ids"] == ["missing"]
    assert reads == []
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert (
        portfolio.build_goal_portfolio(registry_path=bad)["coverage"]["discovered"]
        is None
    )


def test_evidence_slot_skips_unsubstantiated_and_gap_rows():
    base = status("alpha")["run_history"]["goals"][0]["latest_status_run"]
    wrong = deepcopy(base)
    wrong["progress_observation"]["work_item_id"] = "another-todo"
    gap = {**base, "delivery_outcome": "outcome_gap"}
    value = compact_goal_semantic_history(
        goal_semantic_history_from_runs([wrong, gap, base]), compact_run=compact_run
    )
    retained = value["agents"][0]["latest_evidence_delivery_run"]
    assert retained["todo_id"] == retained["progress_observation"]["work_item_id"]
    assert retained["delivery_outcome"] == "outcome_progress"


def test_safe_input_bounds(tmp_path):
    path = registry(tmp_path)
    for kwargs in (
        {"goal_ids": ["../outside"]},
        {"limit": 0},
        {"max_age_hours": float("nan")},
    ):
        with pytest.raises(ValueError):
            portfolio.build_goal_portfolio(registry_path=path, **kwargs)


def test_shipped_cli_reports_empty_coverage_without_writing_registry(tmp_path):
    path = registry(tmp_path, ())
    before = path.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(path),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--format",
            "json",
            "goal-portfolio",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["coverage"]["discovered"] == 0
    assert payload["coverage"]["complete"] is False
    assert path.read_bytes() == before


def test_stale_projection_and_partial_agent_coverage_remain_visible(
    tmp_path, reads, monkeypatch
):
    path = registry(tmp_path, ("alpha",))
    data = json.loads(path.read_text())
    data["goals"][0]["coordination"]["registered_agents"] = [
        f"worker-{i}" for i in range(10)
    ]
    path.write_text(json.dumps(data))
    monkeypatch.setattr(
        portfolio,
        "build_quota_should_run",
        lambda *a, **k: {
            "ok": True,
            "agent_identity": {"agent_id": k["agent_id"]},
            "stale_latest_run_warning": {"requires_refresh_state": True},
        },
    )
    result = portfolio.build_goal_portfolio(registry_path=path, now=NOW)
    row = result["goals"][0]
    assert row["quality"] == "stale"
    assert row["agent_coverage"] == {"discovered": 10, "attempted": 8, "omitted": 2}
    assert "agent_coverage_incomplete" in row["warnings"]
    assert "latest_run_projection_stale" in row["warnings"]
    assert not result["coverage"]["complete"]


def test_source_file_change_is_detected_without_registry_change(
    tmp_path, reads, monkeypatch
):
    path = registry(tmp_path, ("alpha",))
    state = tmp_path / "state.md"
    state.write_text("before")
    data = json.loads(path.read_text())
    data["goals"][0].update(repo=str(tmp_path), state_file="state.md")
    path.write_text(json.dumps(data))
    before_registry = path.read_bytes()

    def read(**kwargs):
        state.write_text("changed during collection")
        return status(kwargs["goal_id"])

    monkeypatch.setattr(portfolio, "collect_status", read)
    result = portfolio.build_goal_portfolio(registry_path=path, now=NOW)
    assert path.read_bytes() == before_registry
    assert result["goals"][0]["quality"] == "conflicting"
    assert "source_changed_during_collection" in result["goals"][0]["warnings"]


def test_mismatched_delivery_identity_is_not_accepted(tmp_path, reads, monkeypatch):
    def read(**kwargs):
        payload = status(kwargs["goal_id"])
        payload["run_history"]["goals"][0]["semantic_history"]["agents"][0][
            "latest_evidence_delivery_run"
        ]["goal_id"] = "another-goal"
        return payload

    monkeypatch.setattr(portfolio, "collect_status", read)
    result = portfolio.build_goal_portfolio(
        registry_path=registry(tmp_path, ("alpha",)), now=NOW
    )
    assert result["goals"][0]["quality"] == "conflicting"
    assert result["goals"][0]["deliveries"] == []


def test_full_inventory_reuses_one_authoritative_collection(tmp_path, reads):
    ids = tuple(f"goal-{i:02}" for i in range(45))
    result = portfolio.build_goal_portfolio(registry_path=registry(tmp_path, ids), limit=128, now=NOW)
    assert reads == [None]
    assert result['coverage']['discovered'] == 45
    assert result['coverage']['verified'] == 45
    assert result['coverage']['omitted'] == 0
    assert {g['goal_id'] for g in result['goals']} == set(ids)


def test_stopped_goals_skip_core_reads_without_hiding_stale_active(monkeypatch, tmp_path):
    path = registry(tmp_path, ('a-stopped', 'b-stale', 'c-active'))
    data = json.loads(path.read_text())
    data['goals'][0]['activation'] = {'state': 'stopped'}
    path.write_text(json.dumps(data))
    calls, reads = [], []
    monkeypatch.setattr(portfolio, 'collect_status', lambda **kw: calls.append(kw) or {})
    def read(goal, **kwargs):
        reads.append(goal['id'])
        return {'goal_id': goal['id'], 'quality': 'stale', 'progress': 'unknown', 'warnings': [], 'source': {}}
    monkeypatch.setattr(portfolio, '_read_goal', read)
    result = portfolio.build_goal_portfolio(registry_path=path, include_stopped=False)
    assert reads == ['b-stale', 'c-active']
    assert calls[0]['activation_state_filter'] == 'active'
    assert result['coverage']['stopped_excluded'] == 1
    assert result['coverage']['attempted'] == 2
    assert result['goals'][0]['activation_state'] == 'stopped'
    assert result['goals'][1]['quality'] == 'stale'
    reads.clear()
    # Stopped entries do not consume the active read limit.
    portfolio.build_goal_portfolio(registry_path=path, include_stopped=False, limit=1)
    assert reads == ['b-stale']
    reads.clear()
    portfolio.build_goal_portfolio(registry_path=path, include_stopped=True)
    assert 'a-stopped' in reads


def test_activation_change_during_collection_is_not_silently_hidden(monkeypatch, tmp_path):
    path = registry(tmp_path, ('alpha', 'beta'))
    data = json.loads(path.read_text())
    data['goals'][0]['activation'] = {'state': 'stopped'}
    path.write_text(json.dumps(data))
    def read(goal, **kwargs):
        data['goals'][0]['activation']['state'] = 'active'
        path.write_text(json.dumps(data))
        return {'goal_id': goal['id'], 'quality': 'stale', 'progress': 'unknown', 'warnings': []}
    monkeypatch.setattr(portfolio, '_read_goal', read)
    result = portfolio.build_goal_portfolio(registry_path=path, include_stopped=False)
    assert result['coverage']['stopped_excluded'] == 0
    assert result['goals'][0]['activation_state'] == 'unknown'
    assert result['goals'][0]['quality'] == 'conflicting'
