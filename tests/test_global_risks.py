from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import loopx.cli_commands.summary_all as manager_cli
import loopx.global_risks as global_risks
from loopx.cli import build_parser


FROZEN_TIME = "2026-08-11T12:00:00Z"


def build_payload(
	tmp_path: Path,
	*,
	agent_id: str | None = None,
	limit: int = 8,
	time_range: str = "24h",
) -> dict[str, object]:
	return global_risks.build_global_risks(
		registry_path=tmp_path / "registry.json",
		runtime_root_override=None,
		scan_roots=[],
		agent_id=agent_id,
		time_range=time_range,
		limit=limit,
	)


def status_payload(
	*,
	diagnostics: list[dict[str, Any]] | None = None,
	findings: list[dict[str, Any]] | None = None,
	items: list[dict[str, Any]] | None = None,
	history_goals: list[dict[str, Any]] | None = None,
	global_registry_available: bool = True,
	ok: bool = True,
) -> dict[str, Any]:
	payload: dict[str, Any] = {
		"ok": ok,
		"contract": {
			"ok": not diagnostics,
			"error_diagnostics": diagnostics or [],
			"warnings": [],
			"checks": [],
		},
		"global_registry": {
			"available": global_registry_available,
			"ok": True,
			"findings": findings or [],
		},
		"attention_queue": {
			"available": True,
			"items": items or [],
			"item_count": len(items or []),
		},
	}
	if history_goals is not None:
		payload["run_history"] = {"goals": history_goals}
	return payload


def patch_status(monkeypatch: pytest.MonkeyPatch, payload: object) -> list[dict[str, Any]]:
	calls: list[dict[str, Any]] = []

	def collect_status(**kwargs: Any) -> object:
		calls.append(kwargs)
		return deepcopy(payload)

	monkeypatch.setattr(global_risks, "collect_status", collect_status)
	monkeypatch.setattr(global_risks, "now_utc_iso", lambda: FROZEN_TIME)
	return calls


def contract_diagnostic(
	code: str,
	*,
	message: str = "Structured contract check failed.",
	severity: str = "error",
	scope: str = "global",
	goal_id: str | None = None,
	goal_ids: list[str] | None = None,
) -> dict[str, Any]:
	diagnostic: dict[str, Any] = {
		"code": code,
		"message": message,
		"severity": severity,
		"scope": scope,
	}
	if goal_id is not None:
		diagnostic["goal_id"] = goal_id
	if goal_ids is not None:
		diagnostic["goal_ids"] = goal_ids
	return diagnostic


def registry_finding(
	kind: str,
	*,
	severity: str = "high",
	goal_id: str | None = None,
	message: str = "Registry health check failed.",
	recommended_action: str = "Inspect the structured registry finding.",
) -> dict[str, Any]:
	finding: dict[str, Any] = {
		"kind": kind,
		"severity": severity,
		"message": message,
		"recommended_action": recommended_action,
	}
	if goal_id is not None:
		finding["goal_id"] = goal_id
	return finding


def stale_item(
	goal_id: str = "goal-stale",
	*,
	latest_run_generated_at: str = "2026-08-11T00:00:00Z",
	active_state_updated_at: str = "2026-08-11T01:00:00Z",
) -> dict[str, Any]:
	return {
		"goal_id": goal_id,
		"stale_latest_run_warning": {
			"kind": "stale_latest_run_projection",
			"severity": "warning",
			"reason": "active_state_updated_after_latest_run",
			"latest_run_generated_at": latest_run_generated_at,
			"active_state_updated_at": active_state_updated_at,
			"recommended_action": (
				"run refresh-state before trusting latest_run routing"
			),
		},
	}


def history_goal(goal_id: str, agents: object) -> dict[str, Any]:
	return {
		"id": goal_id,
		"coordination": {"registered_agents": agents},
	}


def assert_complete_risk(row: dict[str, Any]) -> None:
	assert row["occurrence_id"]
	assert re.fullmatch(r"[0-9a-f]{16}", str(row["occurrence_id"]))
	assert row["occurrence_count"] >= 1
	assert row["evidence_refs"]
	assert row["next_safe_action"]
	assert row["requires_user_approval"] is False


def test_unhealthy_status_is_reportable_data(monkeypatch, tmp_path) -> None:
	diagnostic = contract_diagnostic(
		"registry_goal_missing_domain",
		scope="goals",
		goal_id="goal-1",
	)
	calls = patch_status(
		monkeypatch,
		status_payload(diagnostics=[diagnostic], ok=False),
	)

	payload = build_payload(tmp_path)

	assert payload["ok"] is True
	assert payload["generated_at"] == FROZEN_TIME
	assert payload["summary"]["source_health_ok"] is False
	assert payload["summary"]["matched_risk_count"] == 1
	assert payload["summary"]["returned_risk_count"] == 1
	assert payload["groups"]["failing_checks"][0]["kind"] == diagnostic["code"]
	assert "error" not in payload
	assert len(calls) == 1
	assert calls[0]["limit"] == 40
	assert not hasattr(global_risks, "build_quota_should_run")
	assert not hasattr(global_risks, "collect_history")


@pytest.mark.parametrize(
	("container", "list_field"),
	[
		("contract", "error_diagnostics"),
		("global_registry", "findings"),
		("attention_queue", "items"),
	],
)
def test_omitted_empty_source_lists_are_valid(
	monkeypatch,
	tmp_path,
	container,
	list_field,
) -> None:
	status = status_payload()
	del status[container][list_field]
	patch_status(monkeypatch, status)

	payload = build_payload(tmp_path)

	assert payload["ok"] is True
	assert payload["risks"] == []


@pytest.mark.parametrize(
	("container", "list_field"),
	[
		("contract", "error_diagnostics"),
		("global_registry", "findings"),
		("attention_queue", "items"),
	],
)
def test_present_non_list_source_fields_fail_closed(
	monkeypatch,
	tmp_path,
	container,
	list_field,
) -> None:
	status = status_payload()
	status[container][list_field] = {"unexpected": "object"}
	patch_status(monkeypatch, status)

	payload = build_payload(tmp_path)

	assert payload["ok"] is False
	assert payload["error_code"] == "malformed_status_projection"
	assert "risks" not in payload
	assert "groups" not in payload


@pytest.mark.parametrize(
	("container", "value"),
	[
		("contract", None),
		("contract", []),
		("global_registry", None),
		("global_registry", "unavailable"),
		("attention_queue", None),
		("attention_queue", []),
	],
)
def test_missing_or_malformed_required_containers_fail_closed(
	monkeypatch,
	tmp_path,
	container,
	value,
) -> None:
	status = status_payload()
	if value is None:
		del status[container]
	else:
		status[container] = value
	patch_status(monkeypatch, status)

	payload = build_payload(tmp_path)

	assert payload["ok"] is False
	assert payload["error_code"] == "malformed_status_projection"


def test_non_object_status_payload_fails_closed(monkeypatch, tmp_path) -> None:
	patch_status(monkeypatch, ["unexpected"])

	payload = build_payload(tmp_path)

	assert payload["ok"] is False
	assert payload["error_code"] == "malformed_status_projection"


@pytest.mark.parametrize(
	("code", "expected_action"),
	[
		(
			"public_boundary_violation",
			"Inspect and remove the boundary violation before delivery.",
		),
		(
			"registry_boundary_risk",
			"Inspect and repair the registry boundary projection.",
		),
	],
)
def test_boundary_diagnostics_never_infer_rollback_candidates(
	monkeypatch,
	tmp_path,
	code,
	expected_action,
) -> None:
	private_path = str(tmp_path / "private" / "state.json")
	patch_status(
		monkeypatch,
		status_payload(
			diagnostics=[
				contract_diagnostic(code, message=f"Found private value at {private_path}")
			]
		),
	)

	payload = build_payload(tmp_path)
	row = payload["risks"][0]

	assert row["category"] == "boundary_warning"
	assert row["severity"] == "high"
	assert row["next_safe_action"] == expected_action
	assert row not in payload["groups"]["rollback_candidates"]
	assert row in payload["groups"]["boundary_warnings"]
	assert "remediation_kind" not in row
	assert private_path not in str(payload)
	assert "<local-path-redacted>" in row["summary"]
	assert payload["summary"]["rollback_candidate_count"] == 0
	assert payload["summary"]["rollback_candidates_overlap_risks"] is False
	assert payload["omissions"] == [
		{
			"kind": "rollback_candidate_source_unavailable",
			"reason": (
				"Current projections do not carry rollback trigger and causal linkage."
			),
		}
	]
	assert_complete_risk(row)


def test_same_redacted_boundary_code_keeps_distinct_source_occurrences(
	monkeypatch,
	tmp_path,
) -> None:
	diagnostics = [
		contract_diagnostic(
			"public_boundary_violation",
			message=f"Found value at {tmp_path}/first/private.json",
		),
		contract_diagnostic(
			"public_boundary_violation",
			message=f"Found value at {tmp_path}/second/private.json",
		),
	]
	patch_status(monkeypatch, status_payload(diagnostics=diagnostics))

	payload = build_payload(tmp_path)
	rows = payload["risks"]

	assert len(rows) == 2
	assert rows[0]["summary"] == rows[1]["summary"]
	assert rows[0]["occurrence_id"] != rows[1]["occurrence_id"]
	assert str(tmp_path) not in str(payload)


@pytest.mark.parametrize("time_range", ["24h", "1h"])
def test_current_stale_run_is_never_aged_out(
	monkeypatch,
	tmp_path,
	time_range,
) -> None:
	patch_status(monkeypatch, status_payload(items=[stale_item()]))

	payload = build_payload(tmp_path, time_range=time_range)
	row = payload["groups"]["stale_runs"][0]

	assert payload["request"]["time_range"] == time_range
	assert row["category"] == "stale_run"
	assert row["kind"] == "stale_latest_run_projection"
	assert row["latest_run_generated_at"] == "2026-08-11T00:00:00Z"
	assert row["active_state_updated_at"] == "2026-08-11T01:00:00Z"
	assert row["next_safe_action"] == (
		"Run refresh-state before trusting latest-run routing."
	)
	assert_complete_risk(row)


def test_malformed_stale_timestamp_warns_but_keeps_risk(monkeypatch, tmp_path) -> None:
	patch_status(
		monkeypatch,
		status_payload(
			items=[stale_item(latest_run_generated_at="not-a-timestamp")]
		),
	)

	payload = build_payload(tmp_path)
	row = payload["risks"][0]

	assert row["category"] == "stale_run"
	assert "latest_run_generated_at" not in row
	assert row["active_state_updated_at"] == "2026-08-11T01:00:00Z"
	assert payload["summary"]["source_warning_count"] == 1
	assert payload["source_warnings"][0]["reason_code"] == "invalid_stale_timestamp"


def test_registry_findings_use_structured_severity_and_actions(
	monkeypatch,
	tmp_path,
) -> None:
	findings = [
		registry_finding("registry_high", severity="high", goal_id="goal-high"),
		registry_finding(
			"registry_action",
			severity="action",
			goal_id="goal-action",
			recommended_action="",
		),
		registry_finding("registry_info", severity="info", goal_id="goal-info"),
	]
	patch_status(monkeypatch, status_payload(findings=findings))

	payload = build_payload(tmp_path)
	rows = payload["groups"]["failing_checks"]

	assert [row["kind"] for row in rows] == ["registry_high", "registry_action"]
	assert rows[0]["severity"] == "high"
	assert rows[1]["severity"] == "action"
	assert rows[1]["next_safe_action"] == (
		"Inspect and resolve global registry finding registry_action."
	)
	assert rows[0]["evidence_refs"] == [
		f"status.global_registry.findings:registry_high:{rows[0]['occurrence_id']}"
	]
	for row in rows:
		assert_complete_risk(row)


def test_unavailable_registry_is_valid_empty_source(monkeypatch, tmp_path) -> None:
	patch_status(
		monkeypatch,
		status_payload(
			findings=[registry_finding("must-not-be-read")],
			global_registry_available=False,
		),
	)

	payload = build_payload(tmp_path)

	assert payload["ok"] is True
	assert payload["risks"] == []
	assert payload["summary"]["source_warning_count"] == 1
	assert payload["source_warnings"][0]["reason_code"] == (
		"global_registry_unavailable"
	)


def test_agent_filter_keeps_global_and_exact_registered_goal_rows(
	monkeypatch,
	tmp_path,
) -> None:
	diagnostics = [
		contract_diagnostic("global_failure"),
		contract_diagnostic(
			"goal_failure",
			scope="goals",
			goal_ids=["goal-match", "goal-other"],
		),
	]
	history = [
		history_goal("goal-match", ["agent-a"]),
		history_goal("goal-other", ["agent-b"]),
	]
	calls = patch_status(
		monkeypatch,
		status_payload(diagnostics=diagnostics, history_goals=history),
	)

	payload = build_payload(tmp_path, agent_id="agent-a")

	assert payload["ok"] is True
	assert [row.get("goal_id") for row in payload["risks"]] == [None, "goal-match"]
	assert len(calls) == 1


def test_empty_registered_agents_is_verified_absence(monkeypatch, tmp_path) -> None:
	diagnostic = contract_diagnostic(
		"goal_failure",
		scope="goals",
		goal_id="goal-empty",
	)
	patch_status(
		monkeypatch,
		status_payload(
			diagnostics=[diagnostic],
			history_goals=[history_goal("goal-empty", [])],
		),
	)

	payload = build_payload(tmp_path, agent_id="agent-a")

	assert payload["ok"] is True
	assert payload["risks"] == []


@pytest.mark.parametrize(
	"history_goals",
	[
		[],
		[{"id": "goal-1"}],
		[{"id": "goal-1", "coordination": []}],
		[{"id": "goal-1", "coordination": {}}],
		[history_goal("goal-1", "agent-a")],
	],
)
def test_unverifiable_agent_scope_fails_closed(
	monkeypatch,
	tmp_path,
	history_goals,
) -> None:
	diagnostic = contract_diagnostic(
		"goal_failure",
		scope="goals",
		goal_id="goal-1",
	)
	patch_status(
		monkeypatch,
		status_payload(diagnostics=[diagnostic], history_goals=history_goals),
	)

	payload = build_payload(tmp_path, agent_id="agent-a")

	assert payload["ok"] is False
	assert payload["error_code"] == "agent_scope_unavailable"
	assert "risks" not in payload


@pytest.mark.parametrize("run_history", [None, [], {}, {"goals": {}}])
def test_malformed_history_container_fails_only_with_agent_filter(
	monkeypatch,
	tmp_path,
	run_history,
) -> None:
	diagnostic = contract_diagnostic(
		"goal_failure",
		scope="goals",
		goal_id="goal-1",
	)
	status = status_payload(diagnostics=[diagnostic])
	if run_history is not None:
		status["run_history"] = run_history
	patch_status(monkeypatch, status)

	filtered = build_payload(tmp_path, agent_id="agent-a")
	assert filtered["ok"] is False
	assert filtered["error_code"] == "agent_scope_unavailable"

	patch_status(monkeypatch, status)
	unfiltered = build_payload(tmp_path)
	assert unfiltered["ok"] is True
	assert len(unfiltered["risks"]) == 1


def test_aggregation_merges_only_identical_occurrences() -> None:
	first = {
		"goal_id": "goal-1",
		"category": "failing_check",
		"kind": "same-check",
		"severity": "warning",
		"summary": "First copy.",
		"occurrence_id": "0123456789abcdef",
		"occurrence_count": 1,
		"evidence_refs": ["status.contract:first"],
		"next_safe_action": "Inspect the check.",
		"requires_user_approval": False,
	}
	duplicate = {
		**first,
		"severity": "high",
		"occurrence_count": 1,
		"evidence_refs": ["status.contract:second"],
	}
	distinct = {**first, "occurrence_id": "fedcba9876543210"}

	aggregated = global_risks._aggregate_risks([first, duplicate, distinct])

	assert len(aggregated) == 2
	merged = next(
		row for row in aggregated if row["occurrence_id"] == "0123456789abcdef"
	)
	assert merged["occurrence_count"] == 2
	assert merged["severity"] == "high"
	assert merged["evidence_refs"] == [
		"status.contract:first",
		"status.contract:second",
	]
	assert sum(row["occurrence_count"] for row in aggregated) == 3


def test_counts_and_groups_are_computed_before_and_after_limit(
	monkeypatch,
	tmp_path,
) -> None:
	diagnostics = [
		contract_diagnostic("boundary-check", severity="warning"),
		contract_diagnostic("failing-high", severity="error"),
	]
	diagnostics[0]["code"] = "public_boundary_violation"
	patch_status(
		monkeypatch,
		status_payload(diagnostics=diagnostics, items=[stale_item()]),
	)

	payload = build_payload(tmp_path, limit=2)

	assert payload["summary"]["matched_risk_count"] == 3
	assert payload["summary"]["matched_occurrence_count"] == 3
	assert payload["summary"]["returned_risk_count"] == 2
	assert payload["summary"]["boundary_warning_count"] == 1
	assert payload["summary"]["failing_check_count"] == 1
	assert payload["summary"]["stale_run_count"] == 1
	assert payload["summary"]["truncated"] is True
	assert len(payload["risks"]) == 2
	assert payload["groups"]["stale_runs"] == []
	assert payload["groups"]["failing_checks"] == [payload["risks"][0]]
	assert payload["groups"]["boundary_warnings"] == [payload["risks"][1]]


@pytest.mark.parametrize("limit", [0, -10])
def test_invalid_result_limits_normalize_to_one(monkeypatch, tmp_path, limit) -> None:
	patch_status(
		monkeypatch,
		status_payload(
			diagnostics=[
				contract_diagnostic("failure-a"),
				contract_diagnostic("failure-b"),
			]
		),
	)

	payload = build_payload(tmp_path, limit=limit)

	assert payload["summary"]["returned_risk_count"] == 1
	assert payload["summary"]["source_scan_limit"] == 40


def test_source_warnings_are_bounded_but_total_is_preserved(
	monkeypatch,
	tmp_path,
) -> None:
	patch_status(
		monkeypatch,
		status_payload(diagnostics=[{"scope": "global"} for _ in range(10)]),
	)

	payload = build_payload(tmp_path)

	assert payload["summary"]["source_warning_count"] == 10
	assert len(payload["source_warnings"]) == 8
	assert payload["source_warnings_truncated"] is True


def test_each_risk_source_scan_is_capped_and_warns_with_counts_only(
	monkeypatch,
	tmp_path,
) -> None:
	private_path = str(tmp_path / "private" / "source.json")
	diagnostics = [
		contract_diagnostic(f"diagnostic-{index}", message=private_path)
		for index in range(41)
	]
	findings = [
		registry_finding(f"finding-{index}", message=private_path)
		for index in range(41)
	]
	items = [stale_item(f"goal-{index}") for index in range(41)]
	patch_status(
		monkeypatch,
		status_payload(diagnostics=diagnostics, findings=findings, items=items),
	)

	payload = build_payload(tmp_path, limit=1)
	truncation_warnings = [
		warning
		for warning in payload["source_warnings"]
		if warning["reason_code"] == "source_rows_truncated"
	]

	assert payload["summary"]["matched_risk_count"] == 120
	assert payload["summary"]["source_rows_truncated"] is True
	assert len(truncation_warnings) == 3
	assert all(warning["available_count"] == 41 for warning in truncation_warnings)
	assert all(warning["inspected_count"] == 40 for warning in truncation_warnings)
	assert private_path not in str(truncation_warnings)


def test_caller_limit_has_hard_result_and_source_caps(monkeypatch, tmp_path) -> None:
	diagnostics = [
		contract_diagnostic(f"diagnostic-{index}") for index in range(401)
	]
	findings = [registry_finding(f"finding-{index}") for index in range(401)]
	items = [stale_item(f"goal-{index}") for index in range(401)]
	calls = patch_status(
		monkeypatch,
		status_payload(diagnostics=diagnostics, findings=findings, items=items),
	)

	payload = build_payload(tmp_path, limit=10_000)

	assert len(calls) == 1
	assert calls[0]["limit"] == 400
	assert payload["summary"]["source_scan_limit"] == 400
	assert payload["summary"]["matched_risk_count"] == 1_200
	assert payload["summary"]["returned_risk_count"] == 100
	assert payload["summary"]["source_rows_truncated"] is True


def test_agent_history_scan_uses_same_hard_cap(monkeypatch, tmp_path) -> None:
	history = [history_goal(f"goal-{index}", ["agent-a"]) for index in range(401)]
	inside = contract_diagnostic(
		"inside",
		scope="goals",
		goal_id="goal-399",
	)
	patch_status(
		monkeypatch,
		status_payload(diagnostics=[inside], history_goals=history),
	)

	inside_payload = build_payload(tmp_path, agent_id="agent-a", limit=10_000)
	assert inside_payload["ok"] is True
	assert inside_payload["risks"][0]["goal_id"] == "goal-399"

	outside = contract_diagnostic(
		"outside",
		scope="goals",
		goal_id="goal-400",
	)
	patch_status(
		monkeypatch,
		status_payload(diagnostics=[outside], history_goals=history),
	)

	outside_payload = build_payload(tmp_path, agent_id="agent-a", limit=10_000)
	assert outside_payload["ok"] is False
	assert outside_payload["error_code"] == "agent_scope_unavailable"


def test_error_envelopes_are_generated_and_public_safe(monkeypatch, tmp_path) -> None:
	private_path = str(tmp_path / "private" / "registry.json")

	def raise_status(**_kwargs: Any) -> object:
		raise RuntimeError(f"cannot read {private_path}")

	monkeypatch.setattr(global_risks, "collect_status", raise_status)
	monkeypatch.setattr(global_risks, "now_utc_iso", lambda: FROZEN_TIME)

	payload = build_payload(tmp_path, time_range="7d")

	assert payload["ok"] is False
	assert payload["error_code"] == "status_collection_unavailable"
	assert payload["generated_at"] == FROZEN_TIME
	assert payload["request"]["time_range"] == "7d"
	assert "risks" not in payload
	assert "groups" not in payload
	assert private_path not in str(payload)
	assert "<local-path-redacted>" in payload["error"]
	assert payload["boundary"] == global_risks.public_safe_boundary()


def test_global_risks_error_survives_projection_runtime_failure(monkeypatch) -> None:
	monkeypatch.setattr(
		global_risks,
		"seal_projection_envelope",
		lambda **_: (_ for _ in ()).throw(RuntimeError("runtime unavailable")),
	)

	payload = global_risks.build_global_risks_error("status unavailable")

	assert payload["ok"] is False
	assert "projection_envelope" not in payload
	assert "no projection envelope" in global_risks.render_global_risks_markdown(
		payload
	)


@pytest.mark.parametrize(
	("value", "expected"),
	[
		("1h", "1h"),
		(" 7D ", "7d"),
		("0h", "24h"),
		("-1d", "24h"),
		("forever", "24h"),
	],
)
def test_time_range_normalization(monkeypatch, tmp_path, value, expected) -> None:
	patch_status(monkeypatch, status_payload())

	payload = build_payload(tmp_path, time_range=value)

	assert payload["request"]["time_range"] == expected


def test_public_boundaries_are_fresh_per_payload(monkeypatch, tmp_path) -> None:
	patch_status(monkeypatch, status_payload())

	first = build_payload(tmp_path)
	second = build_payload(tmp_path)
	first["boundary"]["absolute_paths_recorded"] = True

	assert second["boundary"]["absolute_paths_recorded"] is False


def test_markdown_is_focused_complete_and_public_safe(monkeypatch, tmp_path) -> None:
	private_path = str(tmp_path / "private" / "state.json")
	patch_status(
		monkeypatch,
		status_payload(
			diagnostics=[
				contract_diagnostic(
					"public_boundary_violation",
					message=f"Unsafe value at {private_path}",
				)
			],
			findings=[registry_finding("registry-check")],
			items=[stale_item()],
		),
	)
	payload = build_payload(tmp_path, time_range="1h")

	markdown = global_risks.render_global_risks_markdown(payload)

	assert "# LoopX Global Risks" in markdown
	assert "`/loopx-global-risks`" in markdown
	assert "`1h`" in markdown
	assert "## Stale Runs" in markdown
	assert "## Boundary Warnings" in markdown
	assert "## Failing Checks" in markdown
	assert "## Rollback Candidates" in markdown
	assert (
		"No current accepted source proves a rollback candidate; "
		"the rollback group is intentionally empty."
	) in markdown
	assert (
		"This read-only report does not authorize rollback, history rewrite, "
		"external cleanup, or merge."
	) in markdown
	assert "occurrence=" in markdown
	assert "count=`1`" in markdown
	assert "evidence=" in markdown
	assert "approval=`False`" in markdown
	assert "Recent Progress" not in markdown
	assert "Runnable" not in markdown
	assert private_path not in markdown
	assert "<local-path-redacted>" in markdown


def test_markdown_redacts_hand_built_payload(tmp_path) -> None:
	private_path = str(tmp_path / "private" / "hand-built.json")
	payload: dict[str, Any] = {
		"ok": True,
		"request": {"command": "/loopx-global-risks", "time_range": "24h"},
		"summary": {
			"matched_risk_count": 1,
			"returned_risk_count": 1,
			"truncated": False,
		},
		"groups": {
			"stale_runs": [],
			"boundary_warnings": [],
			"failing_checks": [
				{
					"goal_id": "goal-1",
					"kind": "hand-built",
					"severity": "high",
					"summary": f"Unsafe {private_path}",
					"occurrence_id": "0123456789abcdef",
					"occurrence_count": 1,
					"evidence_refs": [f"unsafe:{private_path}"],
					"next_safe_action": f"Inspect {private_path}",
					"requires_user_approval": False,
				}
			],
			"rollback_candidates": [],
		},
		"source_warnings": [],
	}

	markdown = global_risks.render_global_risks_markdown(payload)

	assert private_path not in markdown
	assert "<local-path-redacted>" in markdown


def test_error_markdown_stays_public_safe(tmp_path, monkeypatch) -> None:
	monkeypatch.setattr(global_risks, "now_utc_iso", lambda: FROZEN_TIME)
	private_path = str(tmp_path / "private" / "failure.json")
	payload = global_risks.build_global_risks_error(
		RuntimeError(f"cannot read {private_path}")
	)

	markdown = global_risks.render_global_risks_markdown(payload)

	assert "Global Risks Unavailable" in markdown
	assert private_path not in markdown
	assert "<local-path-redacted>" in markdown


def test_global_risks_cli_registers_only_the_canonical_command(
	capsys,
	tmp_path,
) -> None:
	args = build_parser().parse_args(
		[
			"global-risks",
			"--time-range",
			"1h",
			"--agent-id",
			"agent-a",
			"--limit",
			"5",
			"--scan-root",
			str(tmp_path),
			"--scan-path",
			"first.md",
			"--scan-path",
			"second.md",
		]
	)

	assert args.command == "global-risks"
	assert args.time_range == "1h"
	assert args.agent_id == "agent-a"
	assert args.limit == 5
	assert args.scan_root == str(tmp_path)
	assert args.scan_path == ["first.md", "second.md"]

	with pytest.raises(SystemExit) as help_exit:
		build_parser().parse_args(["global-risks", "--help"])
	assert help_exit.value.code == 0
	help_text = capsys.readouterr().out
	for flag in ("--time-range", "--agent-id", "--limit", "--scan-root", "--scan-path"):
		assert flag in help_text

	with pytest.raises(SystemExit) as alias_exit:
		build_parser().parse_args(["/loop-global-risks"])
	assert alias_exit.value.code != 0


def test_global_risks_cli_reports_unhealthy_status_with_zero_exit(
	monkeypatch,
	tmp_path,
) -> None:
	printed: list[tuple[dict[str, object], str, object]] = []
	payload: dict[str, object] = {
		"ok": True,
		"summary": {"source_health_ok": False},
	}
	build_calls: list[dict[str, object]] = []

	def build_global_risks(**kwargs: object) -> dict[str, object]:
		build_calls.append(kwargs)
		return payload

	def print_payload(
		value: dict[str, object],
		format_name: str,
		renderer: object,
	) -> None:
		printed.append((value, format_name, renderer))

	monkeypatch.setattr(manager_cli, "build_global_risks", build_global_risks)
	args = argparse.Namespace(
		command="global-risks",
		agent_id="agent-a",
		limit=5,
		scan_root=str(tmp_path),
		scan_path=[str(tmp_path / "risk.md")],
		time_range="1h",
		format="json",
	)

	exit_code = manager_cli.handle_summary_all_command(
		args,
		registry_path=tmp_path / "registry.json",
		runtime_root_arg=None,
		output_format=lambda value: value.format,
		print_payload=print_payload,
	)

	assert exit_code == 0
	assert build_calls == [
		{
			"registry_path": tmp_path / "registry.json",
			"runtime_root_override": None,
			"scan_roots": [tmp_path / "risk.md"],
			"agent_id": "agent-a",
			"time_range": "1h",
			"limit": 5,
		}
	]
	assert printed == [(payload, "json", global_risks.render_global_risks_markdown)]
	assert not hasattr(manager_cli, "build_quota_should_run")
	assert not hasattr(manager_cli, "collect_history")


@pytest.mark.parametrize("format_name", ["json", "markdown"])
def test_global_risks_cli_returns_nonzero_for_compact_errors(
	monkeypatch,
	tmp_path,
	format_name,
) -> None:
	payload: dict[str, object] = {
		"ok": False,
		"error_code": "agent_scope_unavailable",
	}
	printed: list[tuple[dict[str, object], str, object]] = []
	monkeypatch.setattr(manager_cli, "build_global_risks", lambda **_kwargs: payload)
	args = argparse.Namespace(
		command="global-risks",
		agent_id="agent-a",
		limit=5,
		scan_root=str(tmp_path),
		scan_path=[],
		time_range="24h",
		format=format_name,
	)

	exit_code = manager_cli.handle_summary_all_command(
		args,
		registry_path=tmp_path / "registry.json",
		runtime_root_arg=None,
		output_format=lambda value: value.format,
		print_payload=lambda value, selected, renderer: printed.append(
			(value, selected, renderer)
		),
	)

	assert exit_code == 1
	assert printed == [
		(payload, format_name, global_risks.render_global_risks_markdown)
	]


def test_global_risks_cli_wraps_only_outer_exceptions(monkeypatch, tmp_path) -> None:
	private_path = str(tmp_path / "private" / "registry.json")
	printed: list[dict[str, object]] = []
	monkeypatch.setattr(global_risks, "now_utc_iso", lambda: FROZEN_TIME)

	def raise_builder(**_kwargs: object) -> dict[str, object]:
		raise RuntimeError(f"cannot read {private_path}")

	monkeypatch.setattr(manager_cli, "build_global_risks", raise_builder)
	args = argparse.Namespace(
		command="global-risks",
		agent_id=None,
		limit=5,
		scan_root=str(tmp_path),
		scan_path=[],
		time_range="7d",
		format="json",
	)

	exit_code = manager_cli.handle_summary_all_command(
		args,
		registry_path=tmp_path / "registry.json",
		runtime_root_arg=None,
		output_format=lambda value: value.format,
		print_payload=lambda value, _selected, _renderer: printed.append(value),
	)

	assert exit_code == 1
	assert printed[0]["ok"] is False
	assert printed[0]["request"]["time_range"] == "7d"
	assert private_path not in str(printed[0])
	assert "<local-path-redacted>" in str(printed[0]["error"])


def test_stale_host_poll_receipt_surfaces_as_a_stale_run_risk(
	monkeypatch,
	tmp_path,
) -> None:
	registry = {
		"goals": [
			{
				"id": "goal-poll-quiet",
				"state_file": "goals/goal-poll-quiet.md",
				"repo": str(tmp_path),
			}
		]
	}
	(tmp_path / "registry.json").write_text(
		json.dumps(registry), encoding="utf-8"
	)
	receipt_dir = tmp_path / "goals" / ".loopx-host-poll-receipts"
	receipt_dir.mkdir(parents=True)
	(receipt_dir / "goal-poll-quiet.json").write_text(
		json.dumps(
			{
				"schema_version": "loopx_host_poll_receipt_v0",
				"goal_id": "goal-poll-quiet",
				"agent_id": "agent-1",
				"poll_count": 4,
				"last_poll_at": "2026-08-01T00:00:00Z",
				"expected_continuation": True,
				"last_decision_action": "wait",
			}
		),
		encoding="utf-8",
	)
	patch_status(monkeypatch, status_payload())

	payload = build_payload(tmp_path)
	rows = [
		row for row in payload["risks"] if row.get("kind") == "stale_host_poll"
	]
	assert len(rows) == 1
	assert rows[0]["category"] == "stale_run"
	assert rows[0]["goal_id"] == "goal-poll-quiet"
	assert "died mid-wait" in str(rows[0].get("reason") or "")
	assert payload["summary"]["source_warning_count"] == 0


def test_terminal_host_poll_receipt_never_surfaces(
	monkeypatch,
	tmp_path,
) -> None:
	registry = {
		"goals": [
			{
				"id": "goal-poll-done",
				"state_file": "goals/goal-poll-done.md",
				"repo": str(tmp_path),
			}
		]
	}
	(tmp_path / "registry.json").write_text(
		json.dumps(registry), encoding="utf-8"
	)
	receipt_dir = tmp_path / "goals" / ".loopx-host-poll-receipts"
	receipt_dir.mkdir(parents=True)
	(receipt_dir / "goal-poll-done.json").write_text(
		json.dumps(
			{
				"schema_version": "loopx_host_poll_receipt_v0",
				"goal_id": "goal-poll-done",
				"agent_id": "",
				"poll_count": 2,
				"last_poll_at": "2026-08-01T00:00:00Z",
				"expected_continuation": False,
				"last_decision_action": "terminal_no_followup",
			}
		),
		encoding="utf-8",
	)
	patch_status(monkeypatch, status_payload())

	payload = build_payload(tmp_path)
	assert all(
		row.get("kind") != "stale_host_poll" for row in payload["risks"]
	)
