"""State refresh repairs canonical display without another business mutation."""

import json

import pytest
from tests.control_plane import test_refresh_external_delivery as refresh_fixtures
from loopx.control_plane.coordination.local_authority import (
    read_canonical_todos_if_promoted,
)
from loopx.state_refresh import refresh_state_run
from tests.control_plane import test_todo_projection_concurrency as projection_fixtures

canonical_projection = projection_fixtures.canonical_projection
session = refresh_fixtures.session


def test_refresh_delivers_committed_todos_without_a_new_mutation(canonical_projection):
    args, state, first, _ = canonical_projection
    before = read_canonical_todos_if_promoted(
        runtime_root=args["runtime_root"], goal_id=args["goal_id"]
    )
    result = refresh_state_run(
        registry_path=args["registry_path"],
        runtime_root_override=str(args["runtime_root"]),
        goal_id=args["goal_id"],
        project=None,
        state_file=None,
        classification="validated_change",
        recommended_action="Inspect the next bounded task.",
        dry_run=False,
        sync_global=False,
    )
    assert result["ok"] and result["appended"]
    assert result["projection_delivery"] == "delivered"
    assert "Canonical work" in state.read_text()
    assert "Human narrative." in state.read_text()
    after = read_canonical_todos_if_promoted(
        runtime_root=args["runtime_root"], goal_id=args["goal_id"]
    )
    assert (before["provider_revision"], before["cursor"], before["todos"]) == (
        after["provider_revision"],
        after["cursor"],
        after["todos"],
    )
    assert after["provider_revision"] == first["provider_revision"]


def _refresh(args, **overrides):
    return refresh_state_run(
        registry_path=args["registry_path"],
        runtime_root_override=str(args["runtime_root"]),
        goal_id=args["goal_id"],
        project=None,
        state_file=None,
        classification="validated_change",
        recommended_action="Inspect the next bounded task.",
        dry_run=overrides.pop("dry_run", False),
        sync_global=False,
        **overrides,
    )


def test_refresh_gap_uses_canonical_work_even_if_display_delivery_fails(
    canonical_projection, monkeypatch
):
    from loopx.control_plane.todos import provider_projection
    from loopx.state_refresh import render_state_refresh_markdown

    args, state, _, _ = canonical_projection
    state.write_text("## Next Action\n- Implement the next task.\n\n## Agent Todo\n")

    def fail(*a, **kw):
        raise OSError("display unavailable")

    monkeypatch.setattr(provider_projection, "atomic_write_state_text", fail)
    result = _refresh(args)
    assert result["ok"] and result["appended"]
    assert "state_projection_gap" not in result
    assert result["projection_delivery"] == "pending"
    assert result["projection_outbox"]["retry_business_mutation"] is False
    assert "pending" in render_state_refresh_markdown(result)
    assert "without repeating" in render_state_refresh_markdown(result)


def test_refresh_empty_authority_cannot_be_hidden_by_stale_display():
    from pathlib import Path
    from loopx.state_refresh import build_state_refresh_record

    source = "## Next Action\n- Implement the next task.\n\n## Agent Todo\n- [ ] Obsolete work\n"
    args = dict(
        goal_id="g",
        state_file=Path("state.md"),
        state_text=source,
        classification="validated_change",
        recommended_action="Inspect.",
        recommended_action_source="explicit_arg",
        generated_at="2026-01-01T00:00:00Z",
        registry_goal={},
    )
    assert "state_projection_gap" not in build_state_refresh_record(**args)
    authoritative_empty = build_state_refresh_record(**args, todo_fields={})
    assert authoritative_empty["state_projection_gap"]["agent_open_count"] == 0
    assert (
        authoritative_empty["state_projection_gap"]["requires_todo_expansion"] is True
    )


def test_refresh_dry_run_does_not_deliver_display_or_append_history(
    canonical_projection,
):
    args, state, _, _ = canonical_projection
    before = state.read_bytes()
    result = _refresh(args, dry_run=True)
    assert result["dry_run"] and not result["appended"]
    assert "projection_delivery" not in result
    assert state.read_bytes() == before
    assert not (
        args["runtime_root"] / "goals" / args["goal_id"] / "runs/index.jsonl"
    ).exists()


def test_refresh_missing_display_is_recovered_only_as_todo_sections(
    canonical_projection,
):
    args, state, _, _ = canonical_projection
    state.unlink()
    result = _refresh(args)
    assert result["ok"] and result["projection_delivery"] == "delivered"
    assert result["projection_outbox"]["recovery_scope"] == "todo_sections_only"
    assert result["projection_outbox"]["narrative_preserved"] is False
    assert "Canonical work" in state.read_text()


def test_legacy_and_rejected_refresh_do_not_open_projection(
    canonical_projection, monkeypatch
):
    from loopx.control_plane.todos import provider_projection

    args, state, _, _ = canonical_projection
    before = state.read_bytes()

    def unexpected(**kw):
        raise AssertionError("must not repair a rejected or legacy refresh")

    monkeypatch.setattr(
        provider_projection, "project_current_canonical_todos", unexpected
    )
    rejected = {"ok": False, "appended": False}
    assert (
        provider_projection.recover_refresh_todo_projection(rejected, **args)
        is rejected
    )
    monkeypatch.setattr(
        provider_projection, "local_authority_is_promoted", lambda **kw: False
    )
    legacy = {"ok": True, "appended": True}
    assert provider_projection.recover_refresh_todo_projection(legacy, **args) is legacy
    assert state.read_bytes() == before


@pytest.mark.parametrize("session", ["file", "sqlite"], indirect=True)
def test_cli_same_turn_replay_recovers_projection_without_duplicate_writeback(
    session, monkeypatch
):
    from loopx.control_plane.todos import provider_projection
    from tests.control_plane.test_quota_settlement_cli import GOAL_ID

    project, runtime, registry, args, run, journal, index = session
    initial = run(args)
    assert initial["appended"]
    data = json.loads(registry.read_text())
    goal = data["goals"][0]
    state = project / goal["state_file"]
    seeded = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    before = index.read_bytes(), journal.read_bytes()
    original = state.read_text()
    state.write_text("# Existing narrative\n\n## Agent Todo\n")

    def fail(*a, **kw):
        raise OSError("display unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(provider_projection, "atomic_write_state_text", fail)
        failed = run(args)
    assert failed["ok"] and failed["idempotent_replay"]
    assert failed["projection_delivery"] == "pending"
    assert (index.read_bytes(), journal.read_bytes()) == before
    recovered = run(args)
    assert recovered["idempotent_replay"] and not recovered["appended"]
    assert recovered["projection_delivery"] == "delivered"
    assert "Existing narrative" in state.read_text()
    assert seeded["todos"][0]["todo_id"] in state.read_text()
    stable = run(args)
    assert stable["projection_delivery"] == "current"
    assert (index.read_bytes(), journal.read_bytes()) == before
    assert (
        read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)[
            "provider_revision"
        ]
        == seeded["provider_revision"]
    )
    # The preserved narrative is the current document, not an old receipt's copy.
    assert original != state.read_text()


def test_recovery_fence_failure_cannot_hide_a_committed_refresh(
    canonical_projection, monkeypatch
):
    from loopx.control_plane.todos import provider_projection

    args, state, _, _ = canonical_projection
    before = state.read_bytes()

    def fail(**kw):
        raise PermissionError("fence stat unavailable")

    monkeypatch.setattr(provider_projection, "local_authority_is_promoted", fail)
    result = _refresh(args)
    assert result["ok"] and result["appended"]
    assert result["projection_delivery"] == "pending"
    assert result["projection_outbox"]["retry_business_mutation"] is False
    assert state.read_bytes() == before


def test_refresh_reuses_planning_snapshot_but_rechecks_a_concurrent_commit(
    canonical_projection, monkeypatch
):
    from loopx.control_plane.todos import provider_projection

    args, _, _, advance = canonical_projection
    read = provider_projection.read_canonical_todos_if_promoted
    write = provider_projection.atomic_write_state_text
    confirmations, commits = [], []

    def observe(**kwargs):
        confirmations.append(kwargs.get("projection_readback"))
        return read(**kwargs)

    def overlap(*a, **kw):
        write(*a, **kw)
        if not commits:
            commits.append(advance())

    monkeypatch.setattr(
        provider_projection, "read_canonical_todos_if_promoted", observe
    )
    monkeypatch.setattr(provider_projection, "atomic_write_state_text", overlap)
    result = _refresh(args)
    assert result["projection_delivery"] == "delivered"
    assert (
        result["projection_outbox"]["provider_revision"]
        == commits[0]["provider_revision"]
    )
    assert result["projection_outbox"]["delivery_attempts"] == 2
    assert len(confirmations) == 2 and all(confirmations)  # No repeated initial read.
