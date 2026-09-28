"""Capture capacity must isolate producers without inventing review authority."""

import json
import sqlite3

import pytest

from loopx.capabilities.decision_context.capture import capture_profile_sources
from loopx.capabilities.decision_context.providers import (
    LocalFileDecisionSourceProvider,
)
from loopx.capabilities.decision_context.profile import (
    resolve_decision_context_activation,
)
from test_decision_context_capture import setup as capture_setup, settle_batch


@pytest.fixture
def pair(tmp_path):
    args, payload, busy = capture_setup.__wrapped__(tmp_path)
    quiet = tmp_path / "quiet.txt"
    quiet.write_text("quiet baseline")
    payload["sources"].append(
        dict(payload["sources"][0], source_id="quiet", private_locator=str(quiet))
    )
    payload["automation"].update(
        source_ids=[s["source_id"] for s in payload["sources"]], max_pending_batches=6
    )
    args["profile_path"].write_text(json.dumps(payload))
    return args, payload, busy, quiet


def make_due(args):
    with sqlite3.connect(args["spool_path"]) as db:
        db.execute("UPDATE sources SET checked_at=NULL")


def batches(args):
    with sqlite3.connect(args["spool_path"]) as db:
        return db.execute("SELECT * FROM batches ORDER BY id").fetchall()


def test_busy_source_cannot_borrow_quiet_sources_future_capacity(pair):
    args, _, busy, quiet = pair
    initial = capture_profile_sources(**args, execute=True)
    initial_rows = batches(args)
    for revision in range(8):
        busy.write_text(f"busy revision {revision}")
        make_due(args)
        result = capture_profile_sources(**args, execute=True)
    assert result["sources"][0]["status"] == "source_backpressure"
    assert result["sources"][0]["pending_batch_count"] == 3
    assert result["sources"][1]["status"] == "no_change"
    assert result["pending_batch_count"] == 4
    assert result["source_freshness"]["all_fresh"] is False
    assert result["sources"][0]["recovery_diagnosis_required"]
    last_read = result["sources"][0]["last_read_at"]

    quiet.write_text("late independent decision evidence")
    make_due(args)
    result = capture_profile_sources(**args, execute=True)
    assert result["sources"][1]["pending_batch_count"] == 2
    assert result["sources"][0]["last_read_at"] == last_read
    assert result["pending_batch_count"] == 5
    assert set(initial_rows).issubset(set(batches(args)))
    assert not args["cursor_path"].exists()
    assert result["held_batch_count"] == initial["held_batch_count"] == 0


def test_legacy_overfull_source_preserved_without_provider_reads(pair):
    args, payload, busy, _ = pair
    # A one-source profile can legitimately predate enrollment of another.
    original_sources = payload["automation"]["source_ids"]
    payload["automation"]["source_ids"] = original_sources[:1]
    args["profile_path"].write_text(json.dumps(payload))
    for revision in range(4):
        busy.write_text(f"historical revision {revision}")
        if args["spool_path"].exists():
            make_due(args)
        capture_profile_sources(**args, execute=True)
    original_rows = batches(args)
    payload["automation"]["source_ids"] = original_sources
    args["profile_path"].write_text(json.dumps(payload))
    make_due(args)
    calls = []

    class ObservedProvider(LocalFileDecisionSourceProvider):
        def scan(self, **kwargs):
            calls.append(kwargs["source"].source_id)
            return super().scan(**kwargs)

    before = args["spool_path"].read_bytes()
    preview = capture_profile_sources(**args)
    assert args["spool_path"].read_bytes() == before
    assert preview["capacity_policy"]["max_pending_batches_per_source"] == 3
    result = capture_profile_sources(
        **args,
        execute=True,
        source_provider_overrides={
            "local-authority": ObservedProvider(
                provider_id="local-authority", max_bytes=4096
            )
        },
    )
    assert calls == ["quiet"]
    assert result["sources"][0]["pending_batch_count"] == 4
    assert result["sources"][0]["status"] == "source_backpressure"
    assert result["sources"][1]["status"] == "completed"
    assert set(original_rows).issubset(set(batches(args)))
    assert not args["cursor_path"].exists()


def test_review_releases_source_window_without_additional_interval(pair):
    args, payload, _, _ = pair
    payload["automation"]["max_pending_batches"] = 2
    args["profile_path"].write_text(json.dumps(payload))
    first = capture_profile_sources(**args, execute=True)
    # Settle only quiet's batch, so busy hits its source window with global
    # room left. The busy batch is still exact-readable.
    settle_batch(args, first["sources"][1]["next_batch_id"])
    make_due(args)
    capture_profile_sources(**args, execute=True)
    # Quiet was retired later in source order; the next tick sees global room.
    pressure = capture_profile_sources(**args, execute=True)
    assert pressure["sources"][0]["status"] == "source_backpressure"
    settle_batch(args, first["sources"][0]["next_batch_id"])
    result = capture_profile_sources(**args, execute=True)
    assert result["sources"][0]["status"] == "no_change"
    assert result["pending_batch_count"] == 0


def test_undersized_total_is_explicit_and_never_overflows(pair):
    args, payload, _, _ = pair
    payload["automation"]["max_pending_batches"] = 1
    args["profile_path"].write_text(json.dumps(payload))
    result = capture_profile_sources(**args, execute=True)
    assert result["capacity_policy"]["reservation_capacity_sufficient"] is False
    assert result["sources"][1]["status"] == "backpressure"
    assert result["pending_batch_count"] == 1


def test_bounded_ticks_resume_deferred_sources_and_preserve_freshness(pair):
    args, payload, _, _ = pair
    payload["automation"]["max_sources_per_tick"] = 1
    args["profile_path"].write_text(json.dumps(payload))
    first = capture_profile_sources(**args, execute=True)
    assert first["scan_budget"]["attempted_source_count"] == 1
    assert first["scan_budget"]["deferred_source_ids"] == ["quiet"]
    assert first["sources"][1]["last_read_at"] is None
    assert first["sources"][1]["last_checked_at"] is None
    assert first["sources"][1]["status"] == "never_checked"
    second = capture_profile_sources(**args, execute=True)
    assert second["scan_budget"]["attempted_source_count"] == 1
    assert second["scan_budget"]["deferred_source_ids"] == []
    assert second["sources"][1]["status"] == "completed"
    for key in ("last_read_at", "last_checked_at", "status", "pending_batch_count"):
        assert first["sources"][0][key] == second["sources"][0][key]
    assert second["pending_batch_count"] == 2
    assert not args["cursor_path"].exists()


def test_failed_source_uses_budget_without_starving_oldest_source(pair):
    args, payload, _, _ = pair
    payload["automation"]["max_sources_per_tick"] = 1
    args["profile_path"].write_text(json.dumps(payload))
    calls = []

    class FailingProvider(LocalFileDecisionSourceProvider):
        def scan(self, **kwargs):
            calls.append(kwargs["source"].source_id)
            if len(calls) == 1:
                raise RuntimeError("synthetic failure")
            return super().scan(**kwargs)

    providers = {
        "local-authority": FailingProvider(
            provider_id="local-authority", max_bytes=4096
        )
    }
    first = capture_profile_sources(
        **args, execute=True, source_provider_overrides=providers
    )
    assert first["sources"][0]["status"] == "provider_failed"
    assert first["scan_budget"]["attempted_source_count"] == 1
    # Both are due, but the never-attempted source must win over the failed one.
    with sqlite3.connect(args["spool_path"]) as db:
        db.execute("UPDATE sources SET checked_at='2000-01-01T00:00:00+00:00'")
    second = capture_profile_sources(
        **args, execute=True, source_provider_overrides=providers
    )
    assert calls == [payload["sources"][0]["source_id"], "quiet"]
    assert second["sources"][0]["failure_streak"] == 1
    assert second["sources"][0]["last_read_at"] is None
    assert second["scan_budget"]["deferred_source_ids"] == [calls[0]]


@pytest.mark.parametrize(
    "disabled_scope", ["profile", "capture", "unlisted-agent", "new-agent"]
)
def test_capture_budget_metadata_stays_inside_enabled_scope(pair, disabled_scope):
    args, payload, _, _ = pair
    if disabled_scope == "profile":
        payload["enabled"] = False
    elif disabled_scope == "capture":
        payload["automation"]["automatic_capture"] = False
    else:
        args = {**args, "agent_id": disabled_scope}
    args["profile_path"].write_text(json.dumps(payload))
    activation, _ = resolve_decision_context_activation(
        goal_id=args["goal_id"],
        agent_id=args["agent_id"],
        profile_path=args["profile_path"],
    )
    assert "capture_max_sources_per_tick" not in activation
    for execute in (False, True):
        assert capture_profile_sources(**args, execute=execute) == {
            "activation": activation,
            "status": "capture_disabled",
            "executed": False,
        }
    assert not args["spool_path"].exists()
    assert not args["cursor_path"].exists()


def test_enabled_capture_projects_budget_without_changing_shared_activation(pair):
    args, payload, _, _ = pair
    payload["automation"]["max_sources_per_tick"] = 1
    args["profile_path"].write_text(json.dumps(payload))
    activation, _ = resolve_decision_context_activation(
        goal_id=args["goal_id"],
        agent_id=args["agent_id"],
        profile_path=args["profile_path"],
    )
    assert activation["available"] is True
    assert "capture_max_sources_per_tick" not in activation
    result = capture_profile_sources(**args, execute=True)
    assert result["activation"]["capture_max_sources_per_tick"] == 1
    assert result["scan_budget"]["attempted_source_count"] == 1
    before = args["spool_path"].read_bytes()
    assert capture_profile_sources(**args)["activation"] == result["activation"]
    assert args["spool_path"].read_bytes() == before


@pytest.mark.parametrize("invalid", [0, -1, 65, True, "8", 1.5])
def test_capture_tick_budget_rejects_invalid_configuration(pair, invalid):
    from loopx.capabilities.decision_context.profile import (
        normalize_decision_context_profile,
    )

    _, payload, _, _ = pair
    payload["automation"]["max_sources_per_tick"] = invalid
    with pytest.raises(ValueError, match="max_sources_per_tick"):
        normalize_decision_context_profile(payload)
