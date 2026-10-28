#!/usr/bin/env python3
"""Smoke-test reading auto-research rollout events back into live evidence graphs."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.auto_research.evidence_packet import _compact_public_text  # noqa: E402
from demo.auto_research.research_state import (  # noqa: E402
    _compact_optional_text,
    build_auto_research_completion_status,
    build_live_auto_research_projection,
    build_research_decision_candidates,
    build_research_evidence_graph_from_records,
    build_research_failure_continuation_resolution,
    build_research_evidence_graph_from_rollout_events,
)
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path  # noqa: E402

from examples.auto_research_lightweight_fixture import (  # noqa: E402
    AGENT_ID,
    GOAL_ID,
    GROUNDING_REF,
    HYPOTHESIS_ID,
    HYPOTHESIS_TEXT,
    MECHANISM_FAMILY,
    METRIC_NAME,
    TODO_ID,
    write_contract_and_results,
)


def assert_public_safe(payload: Any) -> None:
    text = json.dumps(payload, sort_keys=True) if not isinstance(payload, str) else payload
    forbidden = [
        "/" + "Users/",
        "/" + "private/",
        "/" + "tmp/",
        "lark" + "office",
        "byte" + "dance",
        "http://",
        "https://",
        "api" + "_key",
        "pass" + "word",
        "sec" + "ret",
    ]
    leaked = [needle for needle in forbidden if needle.lower() in text.lower()]
    assert not leaked, leaked


def run_json(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert_public_safe(result.stdout)
    return json.loads(result.stdout)


def evidence_packet(contract: Path, dev: Path, holdout: Path) -> dict[str, Any]:
    return run_json(
        [
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "auto-research",
            "evidence",
            "--contract",
            str(contract),
            "--eval-result",
            str(dev),
            "--eval-result",
            str(holdout),
            "--hypothesis-id",
            HYPOTHESIS_ID,
            "--todo-id",
            TODO_ID,
            "--agent-id",
            AGENT_ID,
            "--claimed-by",
            AGENT_ID,
            "--mechanism-family",
            MECHANISM_FAMILY,
            "--hypothesis",
            HYPOTHESIS_TEXT,
            "--grounding-ref",
            GROUNDING_REF,
            "--branch-ref",
            "codex/auto-research-rollout-readpath-smoke",
        ]
    )


def append_packet(registry: Path, packet: Path) -> dict[str, Any]:
    return run_json(
        [
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "json",
            "auto-research",
            "append-evidence",
            "--packet",
            str(packet),
        ]
    )


def frontier_projection(registry: Path) -> dict[str, Any]:
    return run_json(
        [
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "json",
            "auto-research",
            "frontier",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
        ]
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        runtime_root = temp / "runtime"
        project = temp / "project"
        project.mkdir()
        (project / "ACTIVE_GOAL_STATE.md").write_text(
            "\n".join(
                [
                    "# Active Goal State",
                    "",
                    "## Next Action",
                    "Run one lightweight state-mediated research round.",
                    "",
                    "## Agent Todo",
                    "",
                    f"- [ ] [P0-auto-research] Run one lightweight state-mediated research round. <!-- todo_id={TODO_ID} claimed_by={AGENT_ID} task_class=advancement_task action_kind=run_dev_attempt -->",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        registry = temp / "registry.json"
        registry.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "common_runtime_root": str(runtime_root),
                    "goals": [
                        {
                            "id": GOAL_ID,
                            "domain": "auto_research_smoke",
                            "status": "active",
                            "repo": str(project),
                            "state_file": "ACTIVE_GOAL_STATE.md",
                            "adapter": {"kind": "fixture", "status": "connected-read-only"},
                            "coordination": {
                                "agent_model": "peer_v1",
                                "registered_agents": ["research-curator", AGENT_ID],
                            },
                            "authority_sources": [],
                        }
                    ],
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        from loopx.control_plane.testing.canary_harness import import_fixture_authority
        import_fixture_authority(registry, GOAL_ID, provider="sqlite")

        packet_path = temp / "packet.json"
        contract, dev, holdout = write_contract_and_results(temp)
        packet_path.write_text(
            json.dumps(evidence_packet(contract, dev, holdout), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        append_payload = append_packet(registry, packet_path)
        assert append_payload["appended_count"] == 3, append_payload

        rollout_events = load_rollout_events(rollout_event_log_path(runtime_root, GOAL_ID))
        graph = build_research_evidence_graph_from_rollout_events(
            goal_id=GOAL_ID,
            rollout_events=rollout_events,
        )
        assert graph["schema_version"] == "research_evidence_graph_v0", graph
        assert graph["source_kind"] == "loopx_rollout_event_log", graph
        assert graph["hypothesis_count"] == 1, graph
        assert graph["evidence_event_count"] == 2, graph
        assert graph["metric"]["name"] == METRIC_NAME, graph
        assert graph["baseline_metric"] == 1.0, graph
        assert graph["best_dev_metric"] and graph["best_dev_metric"] > 1.0, graph
        assert graph["best_holdout_metric"] and graph["best_holdout_metric"] > 1.0, graph
        assert graph["holdout_improved"] is True, graph
        assert graph["nodes"][0]["hypothesis_id"] == HYPOTHESIS_ID, graph
        assert graph["nodes"][0]["evidence_event_count"] == 2, graph
        assert graph["nodes"][0]["best_dev_metric"] == graph["best_dev_metric"], graph
        assert graph["nodes"][0]["best_holdout_metric"] == graph["best_holdout_metric"], graph
        assert graph["nodes"][0]["holdout_improved"] is True, graph
        graph_decisions = build_research_decision_candidates(graph)
        assert graph_decisions["retirement_candidates"] == [], graph_decisions
        assert graph_decisions["promotion_candidates"], graph_decisions
        assert graph_decisions["promotion_candidates"][0]["hypothesis_id"] == (
            HYPOTHESIS_ID
        ), graph_decisions
        assert graph_decisions["promotion_candidates"][0]["requires"] == [
            "boundary_scan",
            "promotion_decision",
        ], graph_decisions
        assert_public_safe(graph)

        negative_graph = build_research_evidence_graph_from_records(
            goal_id=GOAL_ID,
            hypotheses=[
                {
                    "schema_version": "research_hypothesis_v0",
                    "hypothesis_id": "hyp_bad_distance_cache",
                    "parent_hypothesis_id": None,
                    "todo_id": "todo_auto_research_bad_001",
                    "claimed_by": AGENT_ID,
                    "mechanism_family": "distance_cache",
                    "hypothesis": "Cache distances before normalization.",
                    "status": "contradicted",
                    "grounding_refs": [GROUNDING_REF],
                    "blocked_by": ["evidence_or_boundary_guardrail_failed"],
                }
            ],
            evidence_events=[
                {
                    "schema_version": "research_evidence_event_v0",
                    "hypothesis_id": "hyp_bad_distance_cache",
                    "todo_id": "todo_auto_research_bad_001",
                    "agent_id": AGENT_ID,
                    "attempt": 1,
                    "split": "dev",
                    "metric": {
                        "name": METRIC_NAME,
                        "value": 0.74,
                        "direction": "maximize",
                    },
                    "baseline_metric": 1.0,
                    "eval_status": "scored",
                    "primary_metric_status": "regressed",
                    "artifact_refs": ["public_eval_summary:bad_distance_cache_dev"],
                    "protected_scope_clean": True,
                }
            ],
            metric_name=METRIC_NAME,
            metric_direction="maximize",
            baseline_metric=1.0,
            source_kind="public_records",
        )
        negative_decisions = build_research_decision_candidates(negative_graph)
        assert negative_decisions["promotion_candidates"] == [], negative_decisions
        assert negative_decisions["retirement_candidates"], negative_decisions
        assert negative_decisions["retirement_candidates"][0]["hypothesis_id"] == (
            "hyp_bad_distance_cache"
        ), negative_decisions
        assert negative_decisions["retirement_candidates"][0]["negative_evidence_count"] == 1
        assert negative_decisions["retirement_candidates"][0]["failure_kind"] == (
            "mechanism_contradicted"
        ), negative_decisions
        assert negative_decisions["retirement_candidates"][0]["next_outcome"] == (
            "propose_failure_successor"
        ), negative_decisions
        negative_completion = build_auto_research_completion_status(
            negative_graph,
            negative_decisions,
        )
        assert negative_completion["status"] == "failure_successor_required", negative_completion
        assert negative_completion["next_action"] == "propose_failure_successor", negative_completion
        assert negative_completion["failure_continuation"]["monitor_allowed"] is False, negative_completion
        assert_public_safe(negative_graph)
        assert_public_safe(negative_decisions)
        assert_public_safe(negative_completion)

        ordinary_retry_graph = dict(negative_graph)
        ordinary_retry_node = dict(negative_graph["nodes"][0])
        ordinary_retry_node.update(
            {
                "hypothesis_id": "hyp_retry_still_available",
                "todo_id": "todo_auto_research_retry_001",
                "status": "needs_retry",
                "negative_evidence_count": 0,
                "failure_kind": None,
            }
        )
        ordinary_retry_graph["nodes"] = [ordinary_retry_node]
        ordinary_retry_decisions = build_research_decision_candidates(ordinary_retry_graph)
        assert ordinary_retry_decisions["retirement_candidates"] == [], ordinary_retry_decisions
        ordinary_retry_completion = build_auto_research_completion_status(
            ordinary_retry_graph,
            ordinary_retry_decisions,
        )
        assert ordinary_retry_completion["failure_continuation"] is None, ordinary_retry_completion
        assert ordinary_retry_completion["status"] == "active", ordinary_retry_completion
        assert_public_safe(ordinary_retry_decisions)
        assert_public_safe(ordinary_retry_completion)

        ambiguous_failure_graph = dict(negative_graph)
        second_failure_node = dict(negative_graph["nodes"][0])
        second_failure_node.update(
            {
                "hypothesis_id": "hyp_bad_distance_cache_second",
                "todo_id": "todo_auto_research_bad_002",
            }
        )
        ambiguous_failure_graph["nodes"] = [
            dict(negative_graph["nodes"][0]),
            second_failure_node,
        ]
        ambiguous_failure_decisions = build_research_decision_candidates(ambiguous_failure_graph)
        ambiguous_failure_completion = build_auto_research_completion_status(
            ambiguous_failure_graph,
            ambiguous_failure_decisions,
        )
        assert ambiguous_failure_completion["status"] == "failure_successor_required", ambiguous_failure_completion
        assert ambiguous_failure_completion["failure_continuation"] is None, ambiguous_failure_completion
        assert ambiguous_failure_completion["failure_continuation_ambiguous"] is True, (
            ambiguous_failure_completion
        )
        assert_public_safe(ambiguous_failure_decisions)
        assert_public_safe(ambiguous_failure_completion)

        missing_lineage_resolution = build_research_failure_continuation_resolution(
            {
                "retirement_candidates": [
                    {
                        "hypothesis_id": "hyp_other_failure",
                        "todo_id": "todo_other",
                        "failure_kind": "mechanism_contradicted",
                        "measurement_scope": None,
                        "remediation_attempt_count": 0,
                        "remediation_attempt_limit": 1,
                        "next_outcome": "propose_failure_successor",
                    }
                ]
            },
            selected_todo_id="todo_selected_summary",
            selected_lineage_todo_id="todo_expected",
            require_selected_failure_match=True,
        )
        assert missing_lineage_resolution["failure_continuation"] is None, (
            missing_lineage_resolution
        )
        assert missing_lineage_resolution["matched_candidate_count"] == 0, (
            missing_lineage_resolution
        )
        assert missing_lineage_resolution["unresolved"] is True, missing_lineage_resolution
        assert_public_safe(missing_lineage_resolution)

        selected_todo_fallback_resolution = build_research_failure_continuation_resolution(
            {
                "retirement_candidates": [
                    {
                        "hypothesis_id": "hyp_selected_summary_failure",
                        "todo_id": "todo_selected_summary",
                        "failure_kind": "mechanism_contradicted",
                        "measurement_scope": None,
                        "remediation_attempt_count": 0,
                        "remediation_attempt_limit": 1,
                        "next_outcome": "propose_failure_successor",
                    }
                ]
            },
            selected_todo_id="todo_selected_summary",
            require_selected_failure_match=True,
        )
        assert selected_todo_fallback_resolution["failure_continuation"] is None, (
            selected_todo_fallback_resolution
        )
        assert selected_todo_fallback_resolution["matched_candidate_count"] == 0, (
            selected_todo_fallback_resolution
        )
        assert selected_todo_fallback_resolution["unresolved"] is True, (
            selected_todo_fallback_resolution
        )
        assert_public_safe(selected_todo_fallback_resolution)

        data_gap_graph = build_research_evidence_graph_from_records(
            goal_id=GOAL_ID,
            hypotheses=[
                {
                    "schema_version": "research_hypothesis_v0",
                    "hypothesis_id": "hyp_data_gap",
                    "parent_hypothesis_id": None,
                    "todo_id": "todo_auto_research_data_gap",
                    "claimed_by": AGENT_ID,
                    "mechanism_family": "price_normalization",
                    "hypothesis": "Normalize the declared price field before scoring.",
                    "status": "contradicted",
                    "grounding_refs": [GROUNDING_REF],
                    "blocked_by": ["evidence_or_boundary_guardrail_failed"],
                }
            ],
            evidence_events=[
                {
                    "schema_version": "research_evidence_event_v0",
                    "hypothesis_id": "hyp_data_gap",
                    "todo_id": "todo_auto_research_data_gap",
                    "agent_id": AGENT_ID,
                    "attempt": 1,
                    "split": "dev",
                    "metric": {
                        "name": METRIC_NAME,
                        "value": 0.8,
                        "direction": "maximize",
                    },
                    "baseline_metric": 1.0,
                    "eval_status": "scored",
                    "primary_metric_status": "regressed",
                    "failure_kind": "data_or_measurement_gap",
                    "measurement_scope": "adjusted_price_field",
                    "remediation_attempt": False,
                    "artifact_refs": ["public_eval_summary:data_gap_initial"],
                    "protected_scope_clean": True,
                },
                {
                    "schema_version": "research_evidence_event_v0",
                    "hypothesis_id": "hyp_data_gap",
                    "todo_id": "todo_auto_research_data_gap",
                    "agent_id": AGENT_ID,
                    "attempt": 2,
                    "split": "holdout",
                    "metric": {
                        "name": METRIC_NAME,
                        "value": 0.7,
                        "direction": "maximize",
                    },
                    "baseline_metric": 1.0,
                    "eval_status": "scored",
                    "primary_metric_status": "regressed",
                    "failure_kind": "data_or_measurement_gap",
                    "measurement_scope": "adjusted_price_field",
                    "remediation_attempt": True,
                    "artifact_refs": ["public_eval_summary:data_gap_remediation"],
                    "protected_scope_clean": True,
                },
            ],
            metric_name=METRIC_NAME,
            metric_direction="maximize",
            baseline_metric=1.0,
            source_kind="public_records",
        )
        data_gap_decisions = build_research_decision_candidates(data_gap_graph)
        data_gap_candidate = data_gap_decisions["retirement_candidates"][0]
        assert data_gap_candidate["remediation_attempt_count"] == 1, data_gap_candidate
        assert data_gap_candidate["remediation_allowed"] is False, data_gap_candidate
        assert data_gap_candidate["next_outcome"] == "propose_failure_successor", data_gap_candidate
        assert_public_safe(data_gap_graph)
        assert_public_safe(data_gap_decisions)

        live_payload = build_live_auto_research_projection(
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            quota_payload={
                "ok": True,
                "agent_lane_next_action": {
                    "todo_id": TODO_ID,
                    "title": "Run one lightweight state-mediated research round",
                    "claimed_by": AGENT_ID,
                    "status": "open",
                    "task_class": "advancement_task",
                    "action_kind": "run_dev_attempt",
                },
                "capability_gate": {
                    "runnable_candidates": [
                        {
                            "todo_id": TODO_ID,
                            "title": "Run one lightweight state-mediated research round",
                            "claimed_by": AGENT_ID,
                            "status": "open",
                            "task_class": "advancement_task",
                            "action_kind": "run_dev_attempt",
                        }
                    ],
                    "blocked_candidates": [],
                },
            },
            rollout_events=rollout_events,
        )
        assert live_payload["evidence_graph"]["source_kind"] == "loopx_rollout_event_log", live_payload
        assert live_payload["evidence_graph"]["evidence_event_count"] == 2, live_payload
        assert live_payload["frontier"]["promotion_candidates"], live_payload
        assert live_payload["frontier"]["promotion_candidates"][0]["hypothesis_id"] == (
            HYPOTHESIS_ID
        ), live_payload
        assert live_payload["frontier"]["retirement_candidates"] == [], live_payload
        assert "showcase_projection" not in live_payload, live_payload
        assert "artifact_packet" not in live_payload, live_payload
        assert live_payload["public_boundary"]["source"] == "live_quota_status_and_rollout_event_log", live_payload
        assert_public_safe(live_payload)

        cli_payload = frontier_projection(registry)
        assert cli_payload["ok"], cli_payload
        assert cli_payload["evidence_graph"]["source_kind"] == "loopx_rollout_event_log", cli_payload
        assert cli_payload["evidence_graph"]["evidence_event_count"] == 2, cli_payload
        assert cli_payload["frontier"]["promotion_candidates"], cli_payload
        assert cli_payload["frontier"]["promotion_candidates"][0]["requires"] == [
            "boundary_scan",
            "promotion_decision",
        ], cli_payload
        assert "showcase_projection" not in cli_payload, cli_payload
        assert "artifact_packet" not in cli_payload, cli_payload
        assert_public_safe(cli_payload)

    print("auto-research-rollout-readpath-smoke ok")


def _test_compact_optional_text_dot_guard() -> None:
    """Regression: compacting long text must not produce '..' (parent-directory marker)."""
    long_text = "a" * 219 + "b."
    result = _compact_optional_text(long_text, field="live.title", default="fallback", max_len=220)
    assert ".." not in result, f"unexpected .. in compacted text: {result!r}"
    _compact_public_text(result, field="live.title", max_len=220)
    long_text_no_dot = "a" * 219 + "c"
    result2 = _compact_optional_text(long_text_no_dot, field="live.title", default="fallback", max_len=220)
    assert ".." not in result2, f"unexpected .. in compacted text: {result2!r}"
    _compact_public_text(result2, field="live.title", max_len=220)
    print("compact-optional-text-dot-guard ok")


if __name__ == "__main__":
    _test_compact_optional_text_dot_guard()
    main()
