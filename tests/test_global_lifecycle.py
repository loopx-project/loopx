from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import loopx.cli_commands.summary_all as manager_cli
import loopx.cli_commands.global_lifecycle as global_lifecycle
from loopx.cli import build_parser
from loopx.control_plane.goals.lifecycle_board import (
    GLOBAL_GOAL_LIFECYCLE_BOARD_SCHEMA_VERSION,
    build_global_goal_lifecycle_board,
)

FROZEN_TIME = "2026-11-27T09:00:00Z"
PROJECTION = "goal_artifact_lifecycle_projection_v0"


def projection(
    goal_id: str,
    *,
    phase: str = "qualifying",
    milestones: list[dict[str, Any]] | None = None,
    guards: list[dict[str, Any]] | None = None,
    transitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": PROJECTION,
        "goal_id": goal_id,
        "lifecycle_phase": phase,
        "milestones": milestones
        if milestones is not None
        else [
            {
                "id": "outcome_progress",
                "label": "Progress",
                "reached": True,
                "reached_evidence_refs": ["run-1"],
                "source": "evidence",
            }
        ],
        "guards": guards if guards is not None else [],
        "next_transitions": transitions
        if transitions is not None
        else [
            {
                "target_phase": "qualifying",
                "precondition": "reach the unreached acceptance milestones with evidence",
                "reason_codes": ["milestone_unreached"],
            }
        ],
    }


def status_payload(*goals: dict[str, Any], ok: bool = True) -> dict[str, Any]:
    return {
        "ok": ok,
        "run_history": {"goals": list(goals)},
        # A whole global estate: the local registry is also the global one.
        "global_registry": {
            "available": True,
            "global_goal_count": len(goals),
            "current_registry_excluded_goal_count": 0,
            "current_registry_excluded_goal_ids": [],
        },
        "projection_envelope": {
            "schema_version": "loopx_projection_envelope_v0",
            "sources": [
                {
                    "source_id": "registry",
                    "read_status": "read",
                    "last_read_at": "2026-11-27T08:59:00Z",
                    "required": True,
                }
            ],
        },
    }


def patch_status(monkeypatch: pytest.MonkeyPatch, payload: object) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def collect_status(**kwargs: Any) -> object:
        calls.append(kwargs)
        return deepcopy(payload)

    monkeypatch.setattr(global_lifecycle, "collect_status", collect_status)
    monkeypatch.setattr(global_lifecycle, "now_utc_iso", lambda: FROZEN_TIME)
    return calls


def build(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    return global_lifecycle.build_global_lifecycle(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        limit=kwargs.pop("limit", 8),
        **kwargs,
    )


def test_aggregates_phases_guards_and_milestones_across_goals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocking = [
        {
            "id": "todo-1",
            "kind": "owner_decision",
            "blocked": True,
            "owner": "user",
        }
    ]
    status = status_payload(
        {
            "id": "goal-a",
            "artifact_lifecycle": projection(
                "goal-a",
                phase="waiting_owner",
                guards=blocking,
                transitions=[
                    {
                        "target_phase": "waiting_owner",
                        "precondition": "resolve the open owner decision",
                        "reason_codes": ["guard_open"],
                    }
                ],
            ),
        },
        {
            "id": "goal-b",
            "artifact_lifecycle": projection(
                "goal-b",
                phase="closed",
                milestones=[
                    {
                        "id": "primary_goal_outcome",
                        "label": "Outcome",
                        "reached": True,
                        "reached_evidence_refs": [],
                        "source": "evidence",
                    }
                ],
                transitions=[],
            ),
        },
        {
            "id": "goal-c",
            "artifact_lifecycle": projection(
                "goal-c",
                phase="waiting_owner",
                guards=[
                    {
                        "id": "gap-evidence",
                        "kind": "evidence_precondition",
                        "blocked": True,
                        "owner": "agent",
                    }
                ],
            ),
        },
    )
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    assert payload["ok"] is True
    assert payload["board_schema_version"] == GLOBAL_GOAL_LIFECYCLE_BOARD_SCHEMA_VERSION
    summary = payload["summary"]
    assert summary["goals_inspected"] == 3
    assert summary["goals_with_projection"] == 3
    assert summary["goals_without_projection"] == 0
    assert summary["blocked_goal_count"] == 2
    assert summary["phase_counts"] == {"waiting_owner": 2, "closed": 1}
    assert payload["complete"] is True
    # Blocked Goals lead the display so the operator sees the work first.
    assert [row["goal_id"] for row in payload["goals"]] == ["goal-a", "goal-c", "goal-b"]
    assert payload["phases"][0] == {
        "lifecycle_phase": "waiting_owner",
        "goal_count": 2,
        "goal_ids": ["goal-a", "goal-c"],
    }
    assert payload["guards"] == [
        {
            "kind": "evidence_precondition",
            "owner": "agent",
            "blocked_goal_count": 1,
            "blocked_goal_ids": ["goal-c"],
        },
        {
            "kind": "owner_decision",
            "owner": "user",
            "blocked_goal_count": 1,
            "blocked_goal_ids": ["goal-a"],
        },
    ]
    assert [row["milestone_id"] for row in payload["milestones"]] == [
        "outcome_progress",
        "primary_goal_outcome",
    ]
    assert payload["goals"][2]["next_transition"] is None
    assert payload["projection_envelope"]["coverage"]["shown_count"] == 3
    markdown = global_lifecycle.render_global_lifecycle_markdown(payload)
    assert "phase=`waiting_owner`" in markdown
    assert "blocked_by owner_decision(user):todo-1" in markdown


def test_goals_without_a_projection_are_typed_gaps_not_silent_omissions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        {"id": "goal-derived", "artifact_lifecycle": projection("goal-derived")},
        {"id": "goal-legacy"},
        {
            "id": "goal-foreign",
            "artifact_lifecycle": {"schema_version": "some_other_v1", "milestones": []},
        },
    )
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    assert payload["summary"]["goals_inspected"] == 3
    assert payload["summary"]["goals_with_projection"] == 1
    assert payload["summary"]["goals_without_projection"] == 2
    assert payload["complete"] is False
    assert payload["gaps"] == [
        {
            "schema_version": "global_goal_lifecycle_gap_v0",
            "goal_id": "goal-legacy",
            "projection_status": "unavailable",
            "reason": "projection_not_derived",
        },
        {
            "schema_version": "global_goal_lifecycle_gap_v0",
            "goal_id": "goal-foreign",
            "projection_status": "unavailable",
            "reason": "projection_not_derived",
        },
    ]
    envelope = payload["projection_envelope"]
    assert {
        "reason": "projection_not_derived",
        "count": 2,
        "refs": [],
    } in envelope["coverage"]["omitted"]


def test_limit_bounds_returned_rows_but_rollups_cover_every_goal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        *[
            {
                "id": f"goal-{index}",
                "artifact_lifecycle": projection(
                    f"goal-{index}",
                    phase="waiting_owner",
                    guards=[
                        {
                            "id": f"guard-{index}",
                            "kind": "owner_decision",
                            "blocked": True,
                            "owner": "user",
                        }
                    ],
                ),
            }
            for index in range(5)
        ]
    )
    patch_status(monkeypatch, status)

    payload = build(tmp_path, limit=2)

    assert len(payload["goals"]) == 2
    assert payload["summary"]["returned_goal_count"] == 2
    assert payload["summary"]["truncated"] is True
    assert payload["summary"]["goals_with_projection"] == 5
    assert payload["guards"][0]["blocked_goal_count"] == 5
    assert payload["projection_envelope"]["coverage"]["available_count"] == 5


def test_phase_filter_narrows_rows_without_hiding_the_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        {"id": "goal-wait", "artifact_lifecycle": projection("goal-wait", phase="waiting_owner")},
        {"id": "goal-closed", "artifact_lifecycle": projection("goal-closed", phase="closed")},
    )
    patch_status(monkeypatch, status)

    payload = build(tmp_path, phases=["closed"])

    assert [row["goal_id"] for row in payload["goals"]] == ["goal-closed"]
    assert payload["summary"]["phase_filter"] == ["closed"]
    assert payload["summary"]["phase_counts"] == {"waiting_owner": 1, "closed": 1}
    assert payload["request"]["phase_filter"] == ["closed"]


def test_unknown_phase_filter_fails_without_collecting_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = patch_status(monkeypatch, status_payload())

    payload = build(tmp_path, phases=["nonsense"])

    assert payload["ok"] is False
    assert payload["error_code"] == "unsupported_phase_filter"
    assert calls == []


def test_status_failure_returns_public_safe_error_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_status(monkeypatch, status_payload(ok=False))

    payload = build(tmp_path)

    assert payload["ok"] is False
    assert payload["error_code"] == "status_collection_unavailable"
    assert payload["boundary"]["raw_logs_recorded"] is False
    assert "projection_envelope" in payload
    assert json.dumps(payload, ensure_ascii=False)


def test_status_collection_exception_is_reported_without_leaking_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def collect_status(**_kwargs: Any) -> object:
        raise RuntimeError(f"cannot read {tmp_path}/private/goal.json")

    monkeypatch.setattr(global_lifecycle, "collect_status", collect_status)
    monkeypatch.setattr(global_lifecycle, "now_utc_iso", lambda: FROZEN_TIME)

    payload = build(tmp_path)

    assert payload["ok"] is False
    assert str(tmp_path) not in json.dumps(payload)
    assert "<local-path-redacted>" in payload["error"]


def test_board_rejects_a_projection_that_breaks_the_public_boundary() -> None:
    status = status_payload(
        {
            "id": "goal-leak",
            "artifact_lifecycle": {
                **projection("goal-leak"),
                "guards": [
                    {
                        "id": "/Users/someone/private/notes.md",
                        "kind": "owner_decision",
                        "blocked": True,
                        "owner": "user",
                    }
                ],
            },
        }
    )

    board = build_global_goal_lifecycle_board(status)

    # A rejected label is bounded to a stable placeholder, never republished.
    assert board["goals"][0]["guards_open"][0]["id"] == "unnamed_guard"


def test_cli_registers_global_lifecycle_and_handles_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "--registry",
            str(tmp_path / "registry.json"),
            "--format",
            "json",
            "global-lifecycle",
            "--limit",
            "3",
        ]
    )
    assert args.command == "global-lifecycle"
    assert args.limit == 3
    patch_status(
        monkeypatch,
        status_payload({"id": "goal-a", "artifact_lifecycle": projection("goal-a")}),
    )

    def print_payload(payload: dict[str, Any], fmt: str, renderer: Any) -> None:
        print(json.dumps({"format": fmt, "ok": payload.get("ok")}))

    code = manager_cli.handle_summary_all_command(
        args,
        registry_path=tmp_path / "registry.json",
        runtime_root_arg=None,
        output_format=lambda _args: "json",
        print_payload=print_payload,
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"format": "json", "ok": True}


def test_goal_without_a_publishable_identity_is_counted_not_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        {"id": "goal-known", "artifact_lifecycle": projection("goal-known")},
        {"artifact_lifecycle": projection("goal-nameless")},
    )
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    summary = payload["summary"]
    assert summary["goals_inspected"] == 2
    assert summary["goals_with_projection"] == 1
    assert summary["goals_without_identity"] == 1
    # An unnamed Goal still makes the answer partial.
    assert payload["complete"] is False
    assert {
        "reason": "goal_identity_unavailable",
        "count": 1,
        "refs": [],
    } in payload["projection_envelope"]["coverage"]["omitted"]
    assert [row["goal_id"] for row in payload["goals"]] == ["goal-known"]


def test_rollups_count_a_goal_once_per_guard_and_milestone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    guard = {
        "id": "gate-1",
        "kind": "owner_decision",
        "blocked": True,
        "owner": "user",
    }
    status = status_payload(
        {
            "id": "goal-a",
            "artifact_lifecycle": projection("goal-a", guards=[guard, {**guard, "id": "gate-2"}]),
        }
    )
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    # Two guards of one kind/owner block one Goal once, not twice.
    assert payload["guards"] == [
        {
            "kind": "owner_decision",
            "owner": "user",
            "blocked_goal_count": 1,
            "blocked_goal_ids": ["goal-a"],
        }
    ]
    assert payload["summary"]["blocked_goal_count"] == 1
    assert payload["milestones"][0]["reached_goal_count"] == 1
    assert payload["milestones"][0]["reached_goal_ids"] == ["goal-a"]


def test_rollup_rows_are_deterministic_across_goal_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    goals = [
        {
            "id": goal_id,
            "artifact_lifecycle": projection(
                goal_id,
                guards=[
                    {
                        "id": f"gate-{goal_id}",
                        "kind": "owner_decision",
                        "blocked": True,
                        "owner": "user",
                    }
                ],
            ),
        }
        for goal_id in ("goal-c", "goal-a", "goal-b")
    ]
    patch_status(monkeypatch, status_payload(*goals))

    payload = build(tmp_path)

    assert payload["guards"][0]["blocked_goal_ids"] == ["goal-a", "goal-b", "goal-c"]
    assert payload["phases"][0]["goal_ids"] == ["goal-a", "goal-b", "goal-c"]


def test_agent_id_narrows_the_single_status_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = patch_status(
        monkeypatch,
        status_payload({"id": "goal-a", "artifact_lifecycle": projection("goal-a")}),
    )

    payload = build(tmp_path, agent_id="agent-7")

    assert payload["ok"] is True
    assert len(calls) == 1
    assert calls[0]["agent_lane_id"] == "agent-7"
    assert payload["request"]["limit"] == 8


def test_phase_a_future_producer_adds_still_appears_in_the_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        {"id": "goal-future", "artifact_lifecycle": projection("goal-future", phase="archived")},
        {"id": "goal-now", "artifact_lifecycle": projection("goal-now", phase="closed")},
    )
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    # A phase a future producer adds keeps its own row after the known phases,
    # so the rollup still sums to every projected Goal instead of hiding it.
    phases = [row["lifecycle_phase"] for row in payload["phases"]]
    assert phases == ["closed", "archived"]
    assert sum(row["goal_count"] for row in payload["phases"]) == 2
    assert payload["summary"]["phase_counts"] == {"archived": 1, "closed": 1}


def test_markdown_render_covers_an_unavailable_read_and_an_empty_board(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_status(monkeypatch, status_payload(ok=False))
    failed = build(tmp_path)
    unavailable = global_lifecycle.render_global_lifecycle_markdown(failed)
    assert unavailable.startswith("# LoopX Global Lifecycle Unavailable")
    assert "status_collection_unavailable" in unavailable

    patch_status(monkeypatch, status_payload())
    empty = build(tmp_path)
    markdown = global_lifecycle.render_global_lifecycle_markdown(empty)
    assert "# LoopX Global Lifecycle" in markdown
    assert "- No Goal carries a derived lifecycle projection." in markdown
    # Empty rollup sections stay explicit instead of disappearing.
    assert markdown.count("- None.") == 3


def test_global_membership_sets_the_denominator_and_names_exclusions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        {"id": "goal-a", "artifact_lifecycle": projection("goal-a")},
        {"id": "goal-b", "artifact_lifecycle": projection("goal-b")},
        {"id": "goal-c", "artifact_lifecycle": projection("goal-c")},
    )
    status["global_registry"] = {
        **status["global_registry"],
        "global_goal_count": 5,
        "current_registry_excluded_goal_count": 2,
        "current_registry_excluded_goal_ids": ["goal-x", "goal-y"],
    }
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    coverage = payload["projection_envelope"]["coverage"]
    # Global scope is denominated by global membership, never by the local
    # registry this process happened to read.
    assert coverage["expected_count"] == 5
    assert coverage["included_count"] == 3
    assert coverage["complete"] is False
    assert {
        "reason": "outside_current_registry",
        "count": 2,
        "refs": ["goal-x", "goal-y"],
    } in coverage["omitted"]
    assert payload["projection_envelope"]["complete"] is False


def test_unknown_global_membership_never_certifies_the_board(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        {"id": "goal-a", "artifact_lifecycle": projection("goal-a")}
    )
    status["global_registry"] = {"available": False, "read_status": "missing"}
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    envelope = payload["projection_envelope"]
    # The board is whole for the Goals status returned, but a global board
    # cannot certify an estate whose membership it never read.
    assert payload["complete"] is False
    assert envelope["coverage"]["expected_count"] is None
    assert envelope["coverage"]["included_count"] == 0
    assert envelope["complete"] is False
    assert "missing_required_sources" in envelope["alert_reasons"]
    # The status read happened and is reported as such; only the membership
    # denominator is unknown, which is what keeps the envelope incomplete.
    own = [row for row in envelope["sources"] if "via" not in row]
    assert [(row["source_id"], row["read_status"]) for row in own] == [
        ("global_registry", "missing"),
        ("status", "read"),
    ]


def test_a_bounded_status_read_reports_the_goals_it_never_scanned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        {"id": "goal-a", "artifact_lifecycle": projection("goal-a")}
    )
    status["global_registry"] = {**status["global_registry"], "global_goal_count": 4}
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    coverage = payload["projection_envelope"]["coverage"]
    # One status read is bounded: three in-scope Goals were never inspected, so
    # the answer cannot describe itself as a whole estate.
    assert coverage["expected_count"] == 4
    assert coverage["included_count"] == 1
    assert {"reason": "goal_scan_omitted", "count": 3, "refs": []} in coverage["omitted"]
    assert coverage["complete"] is False
    assert payload["projection_envelope"]["alert"] is True


def test_a_milestone_whose_id_cannot_be_published_is_not_counted_as_reached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status = status_payload(
        {
            "id": "goal-a",
            "artifact_lifecycle": projection(
                "goal-a",
                milestones=[
                    {
                        "id": "/Users/someone/private/notes.md",
                        "label": "Progress",
                        "reached": True,
                        "reached_evidence_refs": ["run-1"],
                        "source": "evidence",
                    },
                    {
                        "id": "outcome_progress",
                        "label": "Progress",
                        "reached": True,
                        "reached_evidence_refs": ["run-2"],
                        "source": "evidence",
                    },
                ],
            ),
        }
    )
    patch_status(monkeypatch, status)

    payload = build(tmp_path)

    row = payload["goals"][0]
    # The row states what it publishes: one reached milestone id, not two.
    assert row["milestones_reached"] == ["outcome_progress"]
    assert row["milestones_reached_count"] == 1
    assert row["milestones_total_count"] == 2
    assert payload["milestones"] == [
        {
            "milestone_id": "outcome_progress",
            "reached_goal_count": 1,
            "reached_goal_ids": ["goal-a"],
        }
    ]


def _agent_goal(goal_id: str, *, phase: str = "qualifying", agents: list[str]) -> dict[str, Any]:
    return {
        "id": goal_id,
        "artifact_lifecycle": projection(goal_id, phase=phase),
        "coordination": {"agent_model": "peer_v1", "registered_agents": agents},
    }


def test_agent_filter_lists_only_goals_that_register_the_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_status(
        monkeypatch,
        status_payload(
            _agent_goal("goal-a", phase="waiting_owner", agents=["agent-one"]),
            _agent_goal("goal-b", agents=["agent-one", "agent-two"]),
            _agent_goal("goal-c", phase="closed", agents=["agent-two"]),
        ),
    )

    scoped = build(tmp_path, agent_id="agent-one")

    assert [row["goal_id"] for row in scoped["goals"]] == ["goal-a", "goal-b"]
    assert scoped["request"]["agent_filter"] == ["agent-one"]
    assert scoped["summary"]["agent_filter"] == ["agent-one"]
    assert scoped["summary"]["filtered_goal_count"] == 2
    assert scoped["summary"]["agent_scope_goal_count"] == 2
    assert scoped["summary"]["truncated"] is False
    # Only the page narrows: the estate rollups still describe every Goal the
    # single status read inspected.
    assert scoped["summary"]["goals_inspected"] == 3
    assert scoped["summary"]["phase_counts"] == {
        "waiting_owner": 1,
        "qualifying": 1,
        "closed": 1,
    }
    assert scoped["projection_envelope"]["coverage"]["available_count"] == 2
    assert scoped["projection_envelope"]["coverage"]["shown_count"] == 2
    assert scoped["projection_envelope"]["coverage"]["truncated"] is False

    unknown = build(tmp_path, agent_id="ghost-agent")

    assert unknown["ok"] is True
    assert unknown["goals"] == []
    assert unknown["request"]["agent_filter"] == ["ghost-agent"]
    assert unknown["summary"]["filtered_goal_count"] == 0
    assert unknown["summary"]["agent_scope_goal_count"] == 0
    assert unknown["summary"]["truncated"] is False
    # An unknown identity matches nothing; it never widens back to the estate.
    assert unknown["projection_envelope"]["coverage"]["shown_count"] == 0
    assert unknown["projection_envelope"]["coverage"]["available_count"] == 0
    assert unknown["projection_envelope"]["coverage"]["truncated"] is False


def test_agent_and_phase_filters_page_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_status(
        monkeypatch,
        status_payload(
            _agent_goal("goal-a", agents=["agent-one"]),
            _agent_goal("goal-b", phase="closed", agents=["agent-one"]),
            _agent_goal("goal-c", phase="closed", agents=["agent-one"]),
            _agent_goal("goal-d", phase="closed", agents=["agent-two"]),
        ),
    )

    payload = build(tmp_path, agent_id="agent-one", phases=["closed"], limit=1)

    assert [row["goal_id"] for row in payload["goals"]] == ["goal-b"]
    assert payload["summary"]["filtered_goal_count"] == 2
    assert payload["summary"]["returned_goal_count"] == 1
    assert payload["summary"]["truncated"] is True
    assert payload["request"]["agent_filter"] == ["agent-one"]
    assert payload["request"]["phase_filter"] == ["closed"]
    coverage = payload["projection_envelope"]["coverage"]
    assert coverage["shown_count"] == 1
    assert coverage["available_count"] == 2
    assert coverage["truncated"] is True
    # Coverage still describes the global estate read, not the filtered page.
    assert payload["summary"]["goals_inspected"] == 4
    assert payload["summary"]["phase_counts"] == {"qualifying": 1, "closed": 3}


def test_phase_filter_with_no_match_is_an_empty_page_not_a_truncated_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_status(
        monkeypatch,
        status_payload(
            {"id": "goal-a", "artifact_lifecycle": projection("goal-a")},
            {"id": "goal-b", "artifact_lifecycle": projection("goal-b", phase="waiting_owner")},
            {"id": "goal-c", "artifact_lifecycle": projection("goal-c")},
        ),
    )

    payload = build(tmp_path, phases=["closed"])

    assert payload["goals"] == []
    assert payload["summary"]["filtered_goal_count"] == 0
    assert payload["summary"]["truncated"] is False
    coverage = payload["projection_envelope"]["coverage"]
    assert coverage["shown_count"] == 0
    # A page limit can only page through matching rows: zero matches cannot
    # claim three Goals are waiting behind the limit.
    assert coverage["available_count"] == 0
    assert coverage["truncated"] is False
    assert coverage["expected_count"] == 3


def test_an_unusable_agent_identity_selects_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_status(
        monkeypatch,
        status_payload(
            _agent_goal("goal-a", agents=["agent-one"]),
            _agent_goal("goal-b", agents=["agent-two"]),
        ),
    )

    payload = build(tmp_path, agent_id="not a usable/identity")

    # An identity the roster shape rejects cannot match anything; it must not
    # fall back to an unfiltered estate.
    assert payload["goals"] == []
    assert payload["summary"]["agent_scope_goal_count"] == 0
    assert payload["summary"]["filtered_goal_count"] == 0
    assert payload["request"]["agent_filter"] == ["not a usable/identity"]
    assert payload["projection_envelope"]["coverage"]["available_count"] == 0


def test_an_explicitly_blank_agent_arms_the_scope_instead_of_widening_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_status(
        monkeypatch,
        status_payload(
            _agent_goal("goal-a", agents=["agent-one"]),
            _agent_goal("goal-b", agents=["agent-two"]),
        ),
    )

    for blank in ("", "   "):
        payload = build(tmp_path, agent_id=blank)

        # The caller passed the flag, so the filter is armed: a value that
        # normalizes to nothing matches no roster.
        assert payload["goals"] == [], (blank, payload)
        assert payload["request"]["agent_filter"] == [""], (blank, payload)
        assert payload["summary"]["agent_scope_requested"] is True, (blank, payload)
        assert payload["summary"]["filtered_goal_count"] == 0, (blank, payload)
        assert payload["summary"]["agent_scope_goal_count"] == 0, (blank, payload)
        coverage = payload["projection_envelope"]["coverage"]
        assert coverage["shown_count"] == 0, (blank, coverage)
        assert coverage["available_count"] == 0, (blank, coverage)
        assert coverage["truncated"] is False, (blank, coverage)


def test_long_goal_ids_sharing_a_display_prefix_keep_their_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shared = "goal-" + "x" * 140
    registered = shared + "-registered"
    other = shared + "-other"
    patch_status(
        monkeypatch,
        status_payload(
            _agent_goal(registered, agents=["agent-one"]),
            _agent_goal(other, agents=["agent-two"]),
        ),
    )

    payload = build(tmp_path, agent_id="agent-one")

    # Both ids collapse to the same display prefix, so matching on the shortened
    # label would leak the other Goal into the Agent's page.
    assert [row["goal_id"] for row in payload["goals"]] == [registered]
    assert payload["summary"]["agent_scope_goal_count"] == 1
    assert payload["summary"]["filtered_goal_count"] == 1
    coverage = payload["projection_envelope"]["coverage"]
    assert coverage["shown_count"] == 1
    assert coverage["available_count"] == 1

    unfiltered = build(tmp_path)

    # Identity stays distinct everywhere a consumer joins on it.
    assert [row["goal_id"] for row in unfiltered["goals"]] == sorted(
        [registered, other]
    )
    ids = [goal_id for phase in unfiltered["phases"] for goal_id in phase["goal_ids"]]
    assert sorted(ids) == sorted([registered, other])
