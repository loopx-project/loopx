from __future__ import annotations

import pytest

from loopx.control_plane.work_items.progress_observation import (
    build_replan_action_packet,
    build_replan_context,
    normalize_progress_observation,
    semantic_delta_from_writeback,
    semantic_progress_delta,
    typed_progress_repeat_trigger,
)

AGENT_ID = "codex-progress-agent"


def _observation(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "typed_progress_observation_v0",
        "work_item_id": "todo-progress",
        "surface_id": "surface-auth",
        "hypothesis_id": "hypothesis-boundary",
        "probe_kind": "probe-route-map",
        "result_class": "unchanged",
        "evidence_ids": ["evidence-route-map"],
    }
    value.update(overrides)
    return value


def _run(
    generated_at: str,
    observation: dict[str, object],
    *,
    turn_instance_id: str | None = None,
) -> dict[str, object]:
    run: dict[str, object] = {
        "generated_at": generated_at,
        "agent_id": AGENT_ID,
        "progress_observation": normalize_progress_observation(observation),
    }
    if turn_instance_id:
        run["turn_instance_id"] = turn_instance_id
    return run


def test_normalization_rejects_prose_in_semantic_identifiers() -> None:
    with pytest.raises(ValueError, match="opaque"):
        normalize_progress_observation(
            _observation(surface_id="look at the same route again")
        )


def test_two_equivalent_typed_observations_trigger_replan_without_text() -> None:
    runs = [
        _run("2026-08-13T01:01:00Z", _observation()),
        _run("2026-08-13T01:00:00Z", _observation()),
    ]

    trigger = typed_progress_repeat_trigger(runs, agent_id=AGENT_ID)

    assert trigger is not None
    assert trigger["kind"] == "typed_progress_repeat"
    assert trigger["run_count"] == 2
    assert trigger["progress_baseline"]["surface_id"] == "surface-auth"


def test_same_turn_retry_does_not_trigger_replan() -> None:
    runs = [
        _run(
            "2026-08-13T01:01:01Z",
            _observation(),
            turn_instance_id="turn-retry",
        ),
        _run(
            "2026-08-13T01:01:00Z",
            _observation(),
            turn_instance_id="turn-retry",
        ),
    ]

    assert typed_progress_repeat_trigger(runs, agent_id=AGENT_ID) is None


def test_same_turn_retry_and_prior_distinct_turn_trigger_replan() -> None:
    runs = [
        _run(
            "2026-08-13T01:02:01Z",
            _observation(),
            turn_instance_id="turn-current",
        ),
        _run(
            "2026-08-13T01:02:00Z",
            _observation(),
            turn_instance_id="turn-current",
        ),
        _run(
            "2026-08-13T01:00:00Z",
            _observation(),
            turn_instance_id="turn-prior",
        ),
    ]

    trigger = typed_progress_repeat_trigger(runs, agent_id=AGENT_ID)

    assert trigger is not None
    assert trigger["latest_generated_at"] == "2026-08-13T01:02:01Z"
    assert trigger["oldest_counted_generated_at"] == "2026-08-13T01:00:00Z"


def test_settlement_identity_turn_id_deduplicates_retries() -> None:
    runs = [
        {
            **_run("2026-08-13T01:01:01Z", _observation()),
            "settlement_identity": {"turn_instance_id": "turn-retry"},
        },
        {
            **_run("2026-08-13T01:01:00Z", _observation()),
            "settlement_identity": {"turn_instance_id": "turn-retry"},
        },
    ]

    assert typed_progress_repeat_trigger(runs, agent_id=AGENT_ID) is None


def test_conflicting_turn_id_sources_do_not_deduplicate_retries() -> None:
    runs = [
        {
            **_run(
                "2026-08-13T01:01:01Z",
                _observation(),
                turn_instance_id="turn-direct",
            ),
            "settlement_identity": {"turn_instance_id": "turn-settled"},
        },
        {
            **_run(
                "2026-08-13T01:01:00Z",
                _observation(),
                turn_instance_id="turn-direct",
            ),
            "settlement_identity": {"turn_instance_id": "turn-settled"},
        },
    ]

    assert typed_progress_repeat_trigger(runs, agent_id=AGENT_ID) is not None


def test_invalid_turn_ids_do_not_deduplicate_retries() -> None:
    runs = [
        _run(
            "2026-08-13T01:01:01Z",
            _observation(),
            turn_instance_id="turn with prose",
        ),
        _run(
            "2026-08-13T01:01:00Z",
            _observation(),
            turn_instance_id="turn with prose",
        ),
    ]

    assert typed_progress_repeat_trigger(runs, agent_id=AGENT_ID) is not None


def test_text_changes_cannot_hide_an_equivalent_typed_repeat() -> None:
    runs = [
        {
            **_run("2026-08-13T01:01:00Z", _observation()),
            "classification": "completely_different_words",
            "recommended_action": "Use new wording.",
        },
        {
            **_run("2026-08-13T01:00:00Z", _observation()),
            "classification": "source_audit_progress",
            "recommended_action": "Repeat old wording.",
        },
    ]

    assert typed_progress_repeat_trigger(runs, agent_id=AGENT_ID) is not None


def test_new_probe_family_is_a_semantic_delta_but_new_evidence_alone_is_not() -> None:
    baseline = normalize_progress_observation(_observation())
    new_probe = normalize_progress_observation(
        _observation(result_class="advanced", probe_kind="probe-permission-graph")
    )
    evidence_only = normalize_progress_observation(
        _observation(
            result_class="advanced",
            evidence_ids=["evidence-new-output"],
        )
    )

    assert semantic_progress_delta(new_probe, baseline=baseline)["delta_kinds"] == [
        "new_probe_family"
    ]
    assert semantic_progress_delta(evidence_only, baseline=baseline)["accepted"] is False
    # The codec also states whether any evidence id is absent from the baseline;
    # obligation sources that require it behind a renamed identifier read it.
    assert semantic_progress_delta(new_probe, baseline=baseline)["evidence_novel"] is False
    assert semantic_progress_delta(evidence_only, baseline=baseline)["evidence_novel"] is True
    assert semantic_progress_delta(new_probe, baseline=None)["evidence_novel"] is True
    # With an obligation window, novelty is judged against every claim in it
    # and a replayed claim is reported as such.
    windowed = semantic_progress_delta(evidence_only, baseline=baseline, window=[evidence_only, new_probe])
    assert windowed["evidence_novel"] is False
    assert windowed["observation_repeated"] is True
    assert windowed["window_size"] == 2
    fresh = semantic_progress_delta(
        normalize_progress_observation(_observation(result_class="advanced", evidence_ids=["evidence-unseen"])),
        baseline=baseline, window=[evidence_only, new_probe],
    )
    assert fresh["evidence_novel"] is True and fresh["observation_repeated"] is False
    # Malformed window entries are ignored rather than failing the writeback.
    assert semantic_progress_delta(new_probe, baseline=baseline, window=[{"bogus": True}, "text"])["window_size"] == 0


def test_repeated_blocker_cannot_close_replan() -> None:
    baseline = normalize_progress_observation(
        _observation(
            result_class="blocked",
            blocker_id="blocker-permission",
        )
    )
    repeated = normalize_progress_observation(
        _observation(
            result_class="blocked",
            blocker_id="blocker-permission",
        )
    )
    novel = normalize_progress_observation(
        _observation(
            result_class="blocked",
            blocker_id="blocker-missing-runtime",
        )
    )

    assert semantic_progress_delta(repeated, baseline=baseline)["accepted"] is False
    assert semantic_progress_delta(novel, baseline=baseline)["delta_kinds"] == [
        "new_concrete_blocker"
    ]


@pytest.mark.parametrize("history_source", ["coverage", "window", "trigger_window"])
@pytest.mark.parametrize("evidence_id", ["evidence-route-map", "evidence-readback"])
def test_known_blocker_does_not_become_new_under_another_work_item(
    history_source: str, evidence_id: str,
) -> None:
    prior = normalize_progress_observation(
        _observation(result_class="blocked", blocker_id="blocker-permission")
    )
    obligation = {
        "obligation_id": "replan-0123456789abcdef",
        "triggers": [{"kind": "vision_acceptance_gap"}],
    }
    if history_source == "coverage":
        obligation["replan_context"] = build_replan_context(
            obligation, goal_id="goal-fixture", agent_id=AGENT_ID,
            newest_first_runs=[_run("2026-08-13T01:00:00Z", prior)],
        )
    elif history_source == "window":
        obligation["progress_window"] = [prior]
    else:
        obligation["triggers"][0]["progress_window"] = [prior]
    current = normalize_progress_observation(_observation(
        result_class="blocked", blocker_id="blocker-permission",
        work_item_id="replan-0123456789abcdef", evidence_ids=[evidence_id],
    ))
    result = semantic_delta_from_writeback(
        obligation=obligation, progress_observation=current,
    )
    assert result["accepted"] is False
    assert "new_concrete_blocker" not in result["satisfying_outcomes"]

    # A genuinely different blocker can be established by the same evidence.
    novel = semantic_delta_from_writeback(
        obligation=obligation,
        progress_observation={**current, "blocker_id": "blocker-runtime"},
    )
    assert novel["satisfying_outcomes"] == ["new_concrete_blocker"]


def test_exhaustion_requires_coverage_proof() -> None:
    incomplete = normalize_progress_observation(
        _observation(
            result_class="exploration_exhausted",
            coverage_scope_id="scope-public-entrypoints",
            coverage_complete=False,
        )
    )
    complete = normalize_progress_observation(
        _observation(
            result_class="exploration_exhausted",
            coverage_scope_id="scope-public-entrypoints",
            coverage_complete=True,
        )
    )

    assert semantic_progress_delta(incomplete, baseline=None)["accepted"] is False
    assert semantic_progress_delta(complete, baseline=None)["delta_kinds"] == [
        "coverage_backed_exploration_exhausted"
    ]
    assert semantic_progress_delta(complete, baseline=incomplete)["delta_kinds"] == [
        "coverage_backed_exploration_exhausted"
    ]


@pytest.mark.parametrize(
    ("result_class", "terminal_fields", "delta_kind"),
    [
        (
            "exploration_exhausted",
            {"coverage_complete": True},
            "coverage_backed_exploration_exhausted",
        ),
        (
            "no_followup",
            {},
            "coverage_backed_no_followup",
        ),
    ],
)
def test_terminal_coverage_requires_a_semantically_new_scope(
    result_class: str,
    terminal_fields: dict[str, object],
    delta_kind: str,
) -> None:
    baseline = normalize_progress_observation(
        _observation(
            result_class=result_class,
            coverage_scope_id="scope-public-entrypoints",
            **terminal_fields,
        )
    )
    repeated = normalize_progress_observation(dict(baseline))
    evidence_only = normalize_progress_observation(
        _observation(
            result_class=result_class,
            coverage_scope_id="scope-public-entrypoints",
            evidence_ids=["evidence-new-output"],
            **terminal_fields,
        )
    )
    new_scope = normalize_progress_observation(
        _observation(
            result_class=result_class,
            coverage_scope_id="scope-public-cli",
            **terminal_fields,
        )
    )

    assert baseline["fingerprint"] == repeated["fingerprint"]
    assert baseline["fingerprint"] != evidence_only["fingerprint"]
    assert semantic_progress_delta(repeated, baseline=baseline)["accepted"] is False
    assert (
        semantic_progress_delta(evidence_only, baseline=baseline)["accepted"] is False
    )
    assert semantic_progress_delta(new_scope, baseline=baseline)["delta_kinds"] == [
        delta_kind
    ]


def test_terminal_result_class_change_is_a_semantic_delta() -> None:
    baseline = normalize_progress_observation(
        _observation(
            result_class="exploration_exhausted",
            coverage_scope_id="scope-public-entrypoints",
            coverage_complete=True,
        )
    )
    no_followup = normalize_progress_observation(
        _observation(
            result_class="no_followup",
            coverage_scope_id="scope-public-entrypoints",
        )
    )

    assert semantic_progress_delta(no_followup, baseline=baseline)["delta_kinds"] == [
        "coverage_backed_no_followup"
    ]


def test_no_followup_ignores_coverage_complete_churn() -> None:
    baseline = normalize_progress_observation(
        _observation(
            result_class="no_followup",
            coverage_scope_id="scope-public-entrypoints",
            coverage_complete=False,
        )
    )
    coverage_flag_only = normalize_progress_observation(
        _observation(
            result_class="no_followup",
            coverage_scope_id="scope-public-entrypoints",
            coverage_complete=True,
        )
    )

    assert (
        semantic_progress_delta(coverage_flag_only, baseline=baseline)["accepted"]
        is False
    )


@pytest.mark.parametrize(
    "agent_vision",
    [
        None,
        {"state": "active", "path_delta": {"outcome": "continue"}},
        {
            "state": "active",
            "vision_patch": {"acceptance_summary": "Keep exploring."},
            "path_delta": {
                "outcome": "continue",
                "evidence_refs": ["evidence-open-path"],
            },
        },
        {"state": "no_followup", "path_delta": {"outcome": "continue"}},
        {"state": "active", "path_delta": {"outcome": "stop"}},
    ],
)
def test_no_followup_writeback_requires_terminal_vision_and_path(
    agent_vision: dict[str, object] | None,
) -> None:
    delta = semantic_delta_from_writeback(
        obligation={
            "obligation_id": "replan-terminal-consistency",
            "satisfying_semantic_outcomes": ["coverage_backed_no_followup"],
        },
        progress_observation=_observation(
            result_class="no_followup",
            coverage_scope_id="scope-public-entrypoints",
        ),
        agent_vision=agent_vision,
    )

    assert delta["accepted"] is False
    assert delta["reason_code"] == "no_followup_vision_path_inconsistent"
    assert "coverage_backed_no_followup" not in delta["outcomes"]


def test_no_followup_writeback_accepts_consistent_terminal_vision_and_path() -> None:
    delta = semantic_delta_from_writeback(
        obligation={
            "obligation_id": "replan-terminal-consistency",
            "satisfying_semantic_outcomes": ["coverage_backed_no_followup"],
        },
        progress_observation=_observation(
            result_class="no_followup",
            coverage_scope_id="scope-public-entrypoints",
        ),
        agent_vision={"state": "no_followup", "path_delta": {"outcome": "stop"}},
    )

    assert delta["accepted"] is True
    assert delta["satisfying_outcomes"] == ["coverage_backed_no_followup"]


def test_host_projects_evidence_context_and_minimal_action_packet() -> None:
    runs = [
        _run("2026-08-13T01:01:00Z", _observation()),
        _run("2026-08-13T01:00:00Z", _observation()),
    ]
    trigger = typed_progress_repeat_trigger(runs, agent_id=AGENT_ID)
    assert trigger is not None
    obligation = {
        "obligation_id": "replan-0123456789abcdef",
        "triggers": [trigger],
    }

    context = build_replan_context(
        obligation,
        goal_id="goal-progress",
        agent_id=AGENT_ID,
        newest_first_runs=runs,
    )
    enriched = {**obligation, "replan_context": context}
    packet = build_replan_action_packet(enriched)

    assert context["evidence_source"] == "compact_run_history"
    assert context["delivery"] == "host_projected"
    assert context["delivery_receipt"]["status"] == "delivered"
    assert context["coverage_ledger"][0]["fingerprint"] == trigger[
        "progress_fingerprint"
    ]
    assert set(packet) == {
        "schema_version",
        "decision",
        "obligation_id",
        "uncovered_frontier",
        "required_outcome",
        "writeback_contract",
        "allowed_terminal",
        "planning_guidance",
    }
    assert len(packet["planning_guidance"]) == 5
    assert all(
        isinstance(instruction, str) and instruction
        for instruction in packet["planning_guidance"]
    )
    assert packet["writeback_contract"] == {}
    assert packet["allowed_terminal"] == [
        "exploration_exhausted",
        "blocked",
        "no_followup",
    ]
