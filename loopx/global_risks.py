from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from .control_plane.projection_envelope_facts import (
	render_projection_envelope_markdown,
	seal_projection_envelope,
	source_fact,
)

class _CollectStatus(Protocol):
	def __call__(
		self,
		*,
		registry_path: Path,
		runtime_root_override: str | None,
		scan_roots: list[Path],
		limit: int,
	) -> dict[str, Any]: ...


normalize_registered_agents = cast(
	Callable[[Any], list[str]],
	getattr(import_module("loopx.agent_registry"), "normalize_registered_agents"),
)
now_utc_iso = cast(
	Callable[[], str],
	getattr(import_module("loopx.control_plane.runtime.time"), "now_utc_iso"),
)
public_safe_boundary = cast(
	Callable[[], dict[str, bool]],
	getattr(
		import_module("loopx.presentation.public_safety"),
		"public_safe_boundary",
	),
)
load_registry = cast(
	Callable[[Path], dict[str, Any]],
	getattr(import_module("loopx.history"), "load_registry"),
)
redact_public_text = cast(
	Callable[..., str],
	getattr(
		import_module("loopx.presentation.public_safety"),
		"redact_public_text",
	),
)
collect_status = cast(
	_CollectStatus,
	getattr(import_module("loopx.status"), "collect_status"),
)
resolve_goal_local_path = cast(
	Callable[..., Path | None],
	getattr(
		import_module("loopx.control_plane.goals.path_resolution"),
		"resolve_goal_local_path",
	),
)
receipt_path_for_goal = cast(
	Callable[[dict[str, Any], Path | None], Path | None],
	getattr(
		import_module("loopx.control_plane.quota.host_poll_receipts"),
		"receipt_path_for_goal",
	),
)
read_host_poll_receipt = cast(
	Callable[[Path | None], dict[str, Any] | None],
	getattr(
		import_module("loopx.control_plane.quota.host_poll_receipts"),
		"read_host_poll_receipt",
	),
)
stale_host_poll_risk = cast(
	Callable[..., dict[str, Any] | None],
	getattr(
		import_module("loopx.control_plane.quota.host_poll_receipts"),
		"stale_host_poll_risk",
	),
)


def as_dict(value: object) -> dict[str, Any]:
	return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
	return value if isinstance(value, list) else []


COMMAND = "/loopx-global-risks"
SCHEMA_VERSION = "global_manager_command_response_v0"
SOURCE_WARNING_LIMIT = 8
MAX_RESULT_LIMIT = 100
MAX_SCAN_LIMIT = 400
BOUNDARY_CODES = {"public_boundary_violation", "registry_boundary_risk"}
SEVERITY_RANK = {"high": 0, "action": 1, "warning": 2, "info": 3}
CATEGORY_RANK = {"boundary_warning": 0, "failing_check": 1, "stale_run": 2}
SOURCE_SURFACES = [
	"status contract diagnostics",
	"global registry health findings",
	"status attention queue stale-run warnings",
	"status compact run-history coordination",
]

_CONTRACT_SOURCE = "status.contract.error_diagnostics"
_REGISTRY_SOURCE = "status.global_registry.findings"
_STALE_SOURCE = "status.attention_queue.items.stale_latest_run_warning"
_HOST_POLL_SOURCE = "project.host_poll_receipts"
_ROLLBACK_OMISSION = {
	"kind": "rollback_candidate_source_unavailable",
	"reason": "Current projections do not carry rollback trigger and causal linkage.",
}


def _redact_text(value: object, *, limit: int = 260) -> str:
	return redact_public_text(
		value,
		limit=limit,
		replacements={"/loop-global-risks": COMMAND},
		truncation_marker="…",
	)


def _normalize_time_range(value: str) -> str:
	normalized = str(value or "24h").strip().lower()
	if normalized.endswith("h") and normalized[:-1].isdigit():
		hours = int(normalized[:-1])
		if hours > 0:
			return normalized
	if normalized.endswith("d") and normalized[:-1].isdigit():
		days = int(normalized[:-1])
		if days > 0:
			return normalized
	return "24h"


def _request(time_range: str) -> dict[str, Any]:
	return {
		"schema_version": "global_manager_command_request_v0",
		"command": COMMAND,
		"legacy_aliases": ["/loop-global-risks"],
		"cli_command": "loopx global-risks",
		"time_range": _normalize_time_range(time_range),
		"include": [
			"stale_runs",
			"boundary_warnings",
			"failing_checks",
			"rollback_candidates",
		],
		"privacy_mode": "public_safe_summary",
		"dry_run": True,
	}


def build_global_risks_error(
	error: object,
	*,
	time_range: str = "24h",
	error_code: str = "global_risks_unavailable",
	status_payload: dict[str, Any] | None = None,
	status_read_at: str | None = None,
) -> dict[str, Any]:
	return {
		"ok": False,
		"schema_version": SCHEMA_VERSION,
		"generated_at": now_utc_iso(),
		"request": _request(time_range),
		"error_code": _redact_text(error_code, limit=120),
		"error": _redact_text(error),
		"omissions": [
			"Raw/private failure details and local paths were intentionally omitted."
		],
		"projection_envelope": _risks_envelope(
			status_payload or {}, status_read_at=status_read_at,
			host_poll_status="not_read", host_poll_read_at=None,
			host_poll_goal_count=None, source_rows_omitted=0,
			shown_count=0, available_count=0, agent_id=None,
		),
		"boundary": public_safe_boundary(),
	}


def _warning(
	reason_code: str,
	*,
	source: str | None = None,
	goal_id: str | None = None,
	detail: object | None = None,
	available_count: int | None = None,
	inspected_count: int | None = None,
) -> dict[str, Any]:
	warning: dict[str, Any] = {
		"reason_code": _redact_text(reason_code, limit=120),
	}
	if source:
		warning["source"] = _redact_text(source, limit=160)
	if goal_id:
		warning["goal_id"] = _redact_text(goal_id, limit=120)
	if detail:
		warning["detail"] = _redact_text(detail)
	if available_count is not None:
		warning["available_count"] = available_count
	if inspected_count is not None:
		warning["inspected_count"] = inspected_count
	return warning


def _occurrence_id(
	*,
	source_surface: str,
	source_index: int,
	kind: str,
	scope: str,
	goal_id: str | None,
) -> str:
	identity = {
		"goal_id": goal_id,
		"kind": kind,
		"scope": scope,
		"source_index": source_index,
		"source_surface": source_surface,
	}
	encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
	return hashlib.sha256(encoded).hexdigest()[:16]


def _severity(value: object, *, default: str = "warning") -> str:
	normalized = str(value or "").strip()
	if normalized == "error":
		return "high"
	if normalized in SEVERITY_RANK:
		return normalized
	return default


def _goal_ids_for_diagnostic(
	diagnostic: dict[str, Any],
	*,
	scope: str,
	warnings: list[dict[str, Any]],
) -> list[str | None]:
	goal_id = str(diagnostic.get("goal_id") or "").strip()
	if scope != "goals":
		return [goal_id or None]

	goal_ids: list[str] = []
	raw_goal_ids = diagnostic.get("goal_ids")
	if raw_goal_ids is not None and not isinstance(raw_goal_ids, list):
		warnings.append(
			_warning(
				"malformed_contract_goal_ids",
				source=_CONTRACT_SOURCE,
			)
		)
	elif isinstance(raw_goal_ids, list):
		for raw_goal_id in raw_goal_ids:
			normalized = str(raw_goal_id or "").strip()
			if normalized and normalized not in goal_ids:
				goal_ids.append(normalized)
	if goal_id and goal_id not in goal_ids:
		goal_ids.append(goal_id)
	if not goal_ids:
		warnings.append(
			_warning(
				"contract_goal_identity_missing",
				source=_CONTRACT_SOURCE,
			)
		)
	return list(goal_ids)


def _contract_action(code: str) -> str:
	if code == "public_boundary_violation":
		return "Inspect and remove the boundary violation before delivery."
	if code == "registry_boundary_risk":
		return "Inspect and repair the registry boundary projection."
	return f"Inspect and resolve contract check {code} before relying on this goal."


def _normalize_contract_diagnostic(
	raw_diagnostic: object,
	*,
	source_index: int,
	warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
	if not isinstance(raw_diagnostic, dict):
		warnings.append(
			_warning("malformed_contract_diagnostic", source=_CONTRACT_SOURCE)
		)
		return []
	diagnostic: dict[str, Any] = raw_diagnostic
	code = str(diagnostic.get("code") or "").strip()
	if not code:
		warnings.append(
			_warning("contract_code_missing", source=_CONTRACT_SOURCE)
		)
		return []
	scope = str(diagnostic.get("scope") or "global").strip() or "global"
	category = "boundary_warning" if code in BOUNDARY_CODES else "failing_check"
	rows: list[dict[str, Any]] = []
	for goal_id in _goal_ids_for_diagnostic(
		diagnostic,
		scope=scope,
		warnings=warnings,
	):
		occurrence_id = _occurrence_id(
			source_surface=_CONTRACT_SOURCE,
			source_index=source_index,
			kind=code,
			scope=scope,
			goal_id=goal_id,
		)
		rows.append(
			{
				"goal_id": _redact_text(goal_id, limit=120) or None,
				"scope": _redact_text(scope, limit=120),
				"category": category,
				"kind": _redact_text(code, limit=120),
				"severity": _severity(diagnostic.get("severity"), default="high"),
				"summary": _redact_text(diagnostic.get("message"))
				or f"Contract check {code} failed.",
				"occurrence_id": occurrence_id,
				"occurrence_count": 1,
				"evidence_refs": [
					f"{_CONTRACT_SOURCE}:{code}:{occurrence_id}"
				],
				"next_safe_action": _contract_action(code),
				"requires_user_approval": False,
			}
		)
	return rows


def _normalize_registry_finding(
	raw_finding: object,
	*,
	source_index: int,
	warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
	if not isinstance(raw_finding, dict):
		warnings.append(
			_warning("malformed_registry_finding", source=_REGISTRY_SOURCE)
		)
		return []
	finding: dict[str, Any] = raw_finding
	severity = str(finding.get("severity") or "").strip()
	if severity not in {"high", "action"}:
		return []
	kind = str(finding.get("kind") or "").strip()
	if not kind:
		warnings.append(_warning("registry_kind_missing", source=_REGISTRY_SOURCE))
		return []
	goal_id = str(finding.get("goal_id") or "").strip() or None
	scope = "goal" if goal_id else "global"
	occurrence_id = _occurrence_id(
		source_surface=_REGISTRY_SOURCE,
		source_index=source_index,
		kind=kind,
		scope=scope,
		goal_id=goal_id,
	)
	next_safe_action = _redact_text(finding.get("recommended_action"))
	if not next_safe_action:
		next_safe_action = f"Inspect and resolve global registry finding {kind}."
	return [
		{
			"goal_id": _redact_text(goal_id, limit=120) or None,
			"scope": scope,
			"category": "failing_check",
			"kind": _redact_text(kind, limit=120),
			"severity": severity,
			"summary": _redact_text(finding.get("message"))
			or f"Global registry finding {kind} requires attention.",
			"occurrence_id": occurrence_id,
			"occurrence_count": 1,
			"evidence_refs": [
				f"{_REGISTRY_SOURCE}:{kind}:{occurrence_id}"
			],
			"next_safe_action": next_safe_action,
			"requires_user_approval": False,
		}
	]


def _valid_timestamp(value: object) -> str | None:
	text = str(value or "").strip()
	if not text:
		return None
	try:
		datetime.fromisoformat(text.replace("Z", "+00:00"))
	except ValueError:
		return None
	return text


def _collect_stale_host_poll_risks(
	*,
	registry_path: Path,
	scan_limit: int,
	warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str, int | None, int]:
	"""Scan project-local host poll receipts for loops that died mid-wait."""

	risks: list[dict[str, Any]] = []
	try:
		registry_payload = load_registry(registry_path)
	except FileNotFoundError:
		return risks, "missing", None, 0
	except (OSError, ValueError) as exc:
		warnings.append(
			_warning(
				"host_poll_registry_unreadable",
				source=_HOST_POLL_SOURCE,
				detail=str(exc)[:200],
			)
		)
		return risks, "unreadable", None, 0
	goals = registry_payload.get("goals") if isinstance(registry_payload, dict) else None
	if not isinstance(goals, list):
		return risks, "unreadable", None, 0
	inspected_count = 0
	for source_index, goal in enumerate(goals):
		if len(risks) >= scan_limit:
			break
		inspected_count += 1
		if not isinstance(goal, dict) or not goal.get("id"):
			continue
		state_path = resolve_goal_local_path(
			goal.get("state_file"),
			goal,
			fallback_base=registry_path.parent,
		)
		receipt_path = receipt_path_for_goal(goal, state_path)
		receipt = read_host_poll_receipt(receipt_path)
		if receipt is None:
			continue
		raw_risk = stale_host_poll_risk(receipt)
		if raw_risk is None:
			continue
		kind = "stale_host_poll"
		goal_id = _redact_text(str(goal.get("id") or ""), limit=120) or None
		reason = _redact_text(str(raw_risk.get("reason") or ""), limit=200)
		risks.append(
			{
				"goal_id": goal_id,
				"scope": "goal",
				"category": "stale_run",
				"kind": kind,
				"severity": _severity("warning"),
				"summary": (
					reason
					or "Host polling went quiet while the loop expected continuation."
				),
				"reason": reason or None,
				"occurrence_id": _occurrence_id(
					source_surface=_HOST_POLL_SOURCE,
					source_index=source_index,
					kind=kind,
					scope="goal",
					goal_id=goal_id,
				),
				"occurrence_count": 1,
				"evidence_refs": [
					f"{_HOST_POLL_SOURCE}:{kind}:{str(raw_risk.get('last_poll_at') or '')}"
				],
				"next_safe_action": (
					"Check the host session and LoopX gates; restart the goal "
					"worker or resume the bridge when the loop should continue."
				),
				"requires_user_approval": True,
			}
		)
	return risks, "read", len(goals), len(goals) - inspected_count


def _normalize_stale_warning(
	raw_item: object,
	*,
	source_index: int,
	warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
	if not isinstance(raw_item, dict):
		warnings.append(_warning("malformed_attention_item", source=_STALE_SOURCE))
		return []
	item: dict[str, Any] = raw_item
	raw_warning = item.get("stale_latest_run_warning")
	if raw_warning is None:
		return []
	if not isinstance(raw_warning, dict):
		warnings.append(_warning("malformed_stale_warning", source=_STALE_SOURCE))
		return []
	stale_warning: dict[str, Any] = raw_warning
	kind = str(stale_warning.get("kind") or "").strip()
	if kind != "stale_latest_run_projection":
		warnings.append(_warning("invalid_stale_warning_kind", source=_STALE_SOURCE))
		return []
	goal_id = str(item.get("goal_id") or "").strip() or None
	scope = "goal" if goal_id else "global"
	occurrence_id = _occurrence_id(
		source_surface=_STALE_SOURCE,
		source_index=source_index,
		kind=kind,
		scope=scope,
		goal_id=goal_id,
	)
	reason = _redact_text(stale_warning.get("reason"), limit=160)
	row: dict[str, Any] = {
		"goal_id": _redact_text(goal_id, limit=120) or None,
		"scope": scope,
		"category": "stale_run",
		"kind": kind,
		"severity": _severity(stale_warning.get("severity")),
		"summary": reason or "Latest-run routing is stale relative to active state.",
		"reason": reason or None,
		"occurrence_id": occurrence_id,
		"occurrence_count": 1,
		"evidence_refs": [f"{_STALE_SOURCE}:{kind}:{occurrence_id}"],
		"next_safe_action": "Run refresh-state before trusting latest-run routing.",
		"requires_user_approval": False,
	}
	for field in ("latest_run_generated_at", "active_state_updated_at"):
		timestamp = _valid_timestamp(stale_warning.get(field))
		if timestamp is None:
			warnings.append(
				_warning(
					"invalid_stale_timestamp",
					source=_STALE_SOURCE,
					goal_id=goal_id,
					detail=f"Structured stale warning has no valid {field}.",
				)
			)
		else:
			row[field] = timestamp
	return [row]


def _bounded_rows(
	rows: list[Any],
	*,
	source: str,
	scan_limit: int,
	warnings: list[dict[str, Any]],
) -> tuple[list[Any], bool]:
	truncated = len(rows) > scan_limit
	if truncated:
		warnings.append(
			_warning(
				"source_rows_truncated",
				source=source,
				available_count=len(rows),
				inspected_count=scan_limit,
			)
		)
	return rows[:scan_limit], truncated


def _required_container(
	status_payload: dict[str, Any],
	key: str,
) -> dict[str, Any]:
	container = status_payload.get(key)
	if not isinstance(container, dict):
		raise ValueError(f"Required status projection {key} is missing or malformed.")
	return container


def _optional_source_list(container: dict[str, Any], key: str) -> list[Any]:
	if key not in container:
		return []
	value = container.get(key)
	if not isinstance(value, list):
		raise ValueError(f"Required status source list {key} is malformed.")
	return value


def _goal_matches_agent(goal: dict[str, Any], *, agent_id: str) -> bool:
	coordination = as_dict(goal.get("coordination"))
	registered_agents = coordination.get("registered_agents")
	if not isinstance(registered_agents, list):
		raise ValueError("agent scope unavailable for goal")
	return agent_id in normalize_registered_agents(registered_agents)


def _filter_for_agent(
	risks: list[dict[str, Any]],
	*,
	status_payload: dict[str, Any],
	agent_id: str | None,
	scan_limit: int,
	warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
	if not agent_id:
		return risks, False
	run_history = status_payload.get("run_history")
	if not isinstance(run_history, dict):
		raise ValueError("compact run-history projection is missing or malformed")
	history_goals = run_history.get("goals")
	if not isinstance(history_goals, list):
		raise ValueError("compact run-history goal list is missing or malformed")
	bounded_goals, truncated = _bounded_rows(
		history_goals,
		source="status.run_history.goals",
		scan_limit=scan_limit,
		warnings=warnings,
	)
	goals_by_id: dict[str, dict[str, Any]] = {}
	for raw_goal in bounded_goals:
		if not isinstance(raw_goal, dict):
			continue
		goal_id = str(raw_goal.get("id") or "").strip()
		if goal_id and goal_id not in goals_by_id:
			goals_by_id[goal_id] = raw_goal

	filtered: list[dict[str, Any]] = []
	for risk in risks:
		goal_id = str(risk.get("goal_id") or "").strip()
		if not goal_id:
			filtered.append(risk)
			continue
		goal = goals_by_id.get(goal_id)
		if goal is None:
			raise ValueError(f"agent scope unavailable for goal {goal_id}")
		if _goal_matches_agent(goal, agent_id=agent_id):
			filtered.append(risk)
	return filtered, truncated


def _positive_occurrence_count(value: object) -> int:
	if isinstance(value, int) and not isinstance(value, bool) and value > 0:
		return value
	return 1


def _aggregate_risks(risks: list[dict[str, Any]]) -> list[dict[str, Any]]:
	aggregated: dict[tuple[str, str, str, str], dict[str, Any]] = {}
	for risk in risks:
		key = (
			str(risk.get("category") or ""),
			str(risk.get("kind") or ""),
			str(risk.get("goal_id") or ""),
			str(risk.get("occurrence_id") or ""),
		)
		existing = aggregated.get(key)
		if existing is None:
			row = dict(risk)
			row["occurrence_count"] = _positive_occurrence_count(
				risk.get("occurrence_count")
			)
			row["evidence_refs"] = [
				str(item)
				for item in as_list(risk.get("evidence_refs"))
				if str(item)
			]
			aggregated[key] = row
			continue
		existing["occurrence_count"] = _positive_occurrence_count(
			existing.get("occurrence_count")
		) + _positive_occurrence_count(risk.get("occurrence_count"))
		incoming_severity = str(risk.get("severity") or "")
		existing_severity = str(existing.get("severity") or "")
		if SEVERITY_RANK.get(incoming_severity, 4) < SEVERITY_RANK.get(
			existing_severity,
			4,
		):
			existing["severity"] = incoming_severity
		evidence_refs = as_list(existing.get("evidence_refs"))
		for evidence_ref in as_list(risk.get("evidence_refs")):
			if evidence_ref not in evidence_refs:
				evidence_refs.append(evidence_ref)
		existing["evidence_refs"] = evidence_refs
	return list(aggregated.values())


def _risk_sort_key(risk: dict[str, Any]) -> tuple[int, int, int, str, str, str]:
	goal_id = str(risk.get("goal_id") or "")
	evidence_refs = as_list(risk.get("evidence_refs"))
	first_evidence = str(evidence_refs[0]) if evidence_refs else ""
	return (
		SEVERITY_RANK.get(str(risk.get("severity") or ""), 4),
		CATEGORY_RANK.get(str(risk.get("category") or ""), 3),
		0 if not goal_id else 1,
		goal_id,
		str(risk.get("kind") or ""),
		first_evidence,
	)


def _groups(risks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
	return {
		"stale_runs": [risk for risk in risks if risk.get("category") == "stale_run"],
		"boundary_warnings": [
			risk for risk in risks if risk.get("category") == "boundary_warning"
		],
		"failing_checks": [
			risk for risk in risks if risk.get("category") == "failing_check"
		],
		"rollback_candidates": [],
	}


def _risks_envelope(
	status_payload: dict[str, Any],
	*,
	status_read_at: str | None,
	host_poll_status: str,
	host_poll_read_at: str | None,
	host_poll_goal_count: int | None,
	source_rows_omitted: int,
	host_poll_goals_omitted: int = 0,
	shown_count: int,
	available_count: int,
	agent_id: str | None,
) -> dict[str, Any]:
	global_registry = as_dict(status_payload.get("global_registry"))
	status_envelope = as_dict(status_payload.get("projection_envelope"))
	global_source = next(
		(row for row in as_list(status_envelope.get("sources"))
		 if isinstance(row, dict) and row.get("source_id") == "global_registry"),
		{},
	)
	available = global_registry.get("available") is True
	count = global_registry.get("global_goal_count")
	expected = count if available and isinstance(count, int) and not isinstance(count, bool) else None
	excluded = int(global_registry.get("current_registry_excluded_goal_count") or 0) if available else 0
	omitted = []
	if excluded:
		omitted.append({
			"reason": "outside_current_registry", "count": excluded,
			"refs": [str(ref) for ref in as_list(global_registry.get("current_registry_excluded_goal_ids"))[:8]],
		})
	if source_rows_omitted:
		omitted.append({"reason": "source_rows_not_scanned", "count": source_rows_omitted})
	if host_poll_goals_omitted:
		omitted.append({"reason": "host_poll_goals_not_scanned", "count": host_poll_goals_omitted})
	if status_payload and status_payload.get("ok") is not True:
		omitted.append({"reason": "status_unavailable", "count": 1})
	contract = as_dict(status_payload.get("contract"))
	queue = as_dict(status_payload.get("attention_queue"))
	history = as_dict(status_payload.get("run_history"))
	contract_read = isinstance(status_payload.get("contract"), dict) and isinstance(contract.get("error_diagnostics", []), list)
	queue_read = isinstance(status_payload.get("attention_queue"), dict) and isinstance(queue.get("items", []), list)
	sources = [
		source_fact(
			"global_registry",
			read_status=(
				str(global_source.get("read_status") or "read") if available
				else str(global_registry.get("read_status") or ("missing" if status_read_at else "not_read"))
			),
			last_read_at=global_source.get("last_read_at") or (status_read_at if available else None),
			item_count=expected,
		),
		source_fact(
			"status_contract",
			read_status="read" if contract_read else "not_read",
			last_read_at=status_read_at if contract_read else None,
			item_count=len(as_list(contract.get("error_diagnostics"))) if contract_read else None,
		),
		source_fact(
			"attention_queue",
			read_status="read" if queue_read else "not_read",
			last_read_at=status_read_at if queue_read else None,
			item_count=len(as_list(queue.get("items"))) if queue_read else None,
		),
		source_fact(
			"host_poll_receipts", read_status=host_poll_status,
			last_read_at=host_poll_read_at, item_count=host_poll_goal_count,
		),
		*([] if status_envelope else [source_fact("status", read_status="not_read")]),
	]
	if agent_id:
		sources.append(source_fact(
			"agent_scope",
			read_status="read" if isinstance(history.get("goals"), list) else "not_read",
			last_read_at=status_read_at if isinstance(history.get("goals"), list) else None,
			item_count=len(as_list(history.get("goals"))) if history else None,
		))
	return seal_projection_envelope(
		projection="global_risks", observed_at=now_utc_iso(),
		sources=sources,
		coverage={
			"scope": "global", "expected_count": expected,
			"included_count": max(0, expected - excluded) if expected is not None else 0,
			"omitted": omitted,
			"shown_count": shown_count, "available_count": available_count,
		},
		upstream=[status_envelope] if status_envelope else [],
	)


def _malformed_projection_error(
	error: object,
	*,
	time_range: str,
	status_payload: dict[str, Any] | None = None,
	status_read_at: str | None = None,
) -> dict[str, Any]:
	return build_global_risks_error(
		error,
		time_range=time_range,
		error_code="malformed_status_projection",
		status_payload=status_payload,
		status_read_at=status_read_at,
	)


def build_global_risks(
	*,
	registry_path: Path,
	runtime_root_override: str | None,
	scan_roots: list[Path],
	agent_id: str | None,
	time_range: str,
	limit: int,
) -> dict[str, Any]:
	normalized_time_range = _normalize_time_range(time_range)
	normalized_limit = min(max(1, limit), MAX_RESULT_LIMIT)
	scan_limit = min(max(normalized_limit * 4, 40), MAX_SCAN_LIMIT)
	try:
		status_result: object = collect_status(
			registry_path=registry_path,
			runtime_root_override=runtime_root_override,
			scan_roots=scan_roots,
			limit=scan_limit,
		)
	except Exception as exc:
		return build_global_risks_error(
			exc,
			time_range=normalized_time_range,
			error_code="status_collection_unavailable",
		)
	if not isinstance(status_result, dict):
		return _malformed_projection_error(
			"Global status payload is not an object.",
			time_range=normalized_time_range,
		)
	status_payload: dict[str, Any] = status_result
	status_read_at = now_utc_iso()
	try:
		contract = _required_container(status_payload, "contract")
		global_registry = _required_container(status_payload, "global_registry")
		attention_queue = _required_container(status_payload, "attention_queue")
		diagnostics = _optional_source_list(contract, "error_diagnostics")
		findings = _optional_source_list(global_registry, "findings")
		items = _optional_source_list(attention_queue, "items")
	except ValueError as exc:
		return _malformed_projection_error(
			exc, time_range=normalized_time_range,
			status_payload=status_payload, status_read_at=status_read_at,
		)

	warnings: list[dict[str, Any]] = []
	risks: list[dict[str, Any]] = []
	source_rows_truncated = False
	bounded_diagnostics, truncated = _bounded_rows(
		diagnostics,
		source=_CONTRACT_SOURCE,
		scan_limit=scan_limit,
		warnings=warnings,
	)
	source_rows_truncated = source_rows_truncated or truncated
	for source_index, diagnostic in enumerate(bounded_diagnostics):
		risks.extend(
			_normalize_contract_diagnostic(
				diagnostic,
				source_index=source_index,
				warnings=warnings,
			)
		)

	if global_registry.get("available") is False:
		warnings.append(
			_warning(
				"global_registry_unavailable",
				source="status.global_registry",
			)
		)
	else:
		bounded_findings, truncated = _bounded_rows(
			findings,
			source=_REGISTRY_SOURCE,
			scan_limit=scan_limit,
			warnings=warnings,
		)
		source_rows_truncated = source_rows_truncated or truncated
		for source_index, finding in enumerate(bounded_findings):
			risks.extend(
				_normalize_registry_finding(
					finding,
					source_index=source_index,
					warnings=warnings,
				)
			)

	bounded_items, truncated = _bounded_rows(
		items,
		source=_STALE_SOURCE,
		scan_limit=scan_limit,
		warnings=warnings,
	)
	source_rows_truncated = source_rows_truncated or truncated
	for source_index, item in enumerate(bounded_items):
		risks.extend(
			_normalize_stale_warning(
				item,
				source_index=source_index,
				warnings=warnings,
			)
		)

	host_poll_risks, host_poll_status, host_poll_goal_count, host_poll_goals_omitted = _collect_stale_host_poll_risks(
		registry_path=registry_path,
		scan_limit=scan_limit,
		warnings=warnings,
	)
	host_poll_read_at = now_utc_iso() if host_poll_status == "read" else None
	risks.extend(host_poll_risks)
	source_rows_truncated = source_rows_truncated or host_poll_goals_omitted > 0

	try:
		risks, history_truncated = _filter_for_agent(
			risks,
			status_payload=status_payload,
			agent_id=agent_id,
			scan_limit=scan_limit,
			warnings=warnings,
		)
	except ValueError as exc:
		return build_global_risks_error(
			exc,
			time_range=normalized_time_range,
			error_code="agent_scope_unavailable",
			status_payload=status_payload,
			status_read_at=status_read_at,
		)
	source_rows_truncated = source_rows_truncated or history_truncated

	aggregated = _aggregate_risks(risks)
	aggregated.sort(key=_risk_sort_key)
	retained = aggregated[:normalized_limit]
	full_groups = _groups(aggregated)
	retained_groups = _groups(retained)
	matched_risk_count = len(aggregated)
	returned_risk_count = len(retained)
	warning_count = len(warnings)
	return {
		"ok": True,
		"schema_version": SCHEMA_VERSION,
		"generated_at": now_utc_iso(),
		"request": _request(normalized_time_range),
		"summary": {
			"source_health_ok": status_payload.get("ok") is True,
			"matched_risk_count": matched_risk_count,
			"matched_occurrence_count": sum(
				_positive_occurrence_count(risk.get("occurrence_count"))
				for risk in aggregated
			),
			"returned_risk_count": returned_risk_count,
			"source_scan_limit": scan_limit,
			"source_rows_truncated": source_rows_truncated,
			"stale_run_count": len(full_groups["stale_runs"]),
			"boundary_warning_count": len(full_groups["boundary_warnings"]),
			"failing_check_count": len(full_groups["failing_checks"]),
			"rollback_candidate_count": 0,
			"rollback_candidates_overlap_risks": False,
			"truncated": matched_risk_count > returned_risk_count,
			"source_warning_count": warning_count,
			"source_surfaces": SOURCE_SURFACES,
		},
		"groups": retained_groups,
		"risks": retained,
		"source_warnings": warnings[:SOURCE_WARNING_LIMIT],
		"source_warnings_truncated": warning_count > SOURCE_WARNING_LIMIT,
		"projection_envelope": _risks_envelope(
			status_payload, status_read_at=status_read_at,
			host_poll_status=host_poll_status,
			host_poll_read_at=host_poll_read_at,
			host_poll_goal_count=host_poll_goal_count,
			source_rows_omitted=sum(
				max(0, int(warning.get("available_count") or 0) - int(warning.get("inspected_count") or 0))
				for warning in warnings if warning.get("reason_code") == "source_rows_truncated"
			),
			host_poll_goals_omitted=host_poll_goals_omitted,
			shown_count=returned_risk_count, available_count=matched_risk_count,
			agent_id=agent_id,
		),
		"omissions": [dict(_ROLLBACK_OMISSION)],
		"boundary": public_safe_boundary(),
	}


def _render_risk_line(risk: dict[str, Any]) -> str:
	goal_id = _redact_text(risk.get("goal_id"), limit=120) or "global"
	evidence = ", ".join(
		_redact_text(item, limit=180)
		for item in as_list(risk.get("evidence_refs"))
		if _redact_text(item, limit=180)
	)
	line = (
		f"- severity=`{_redact_text(risk.get('severity'), limit=40)}` "
		f"goal=`{goal_id}` kind=`{_redact_text(risk.get('kind'), limit=120)}` "
		f"occurrence=`{_redact_text(risk.get('occurrence_id'), limit=40)}` "
		f"count=`{_redact_text(risk.get('occurrence_count'), limit=20)}` "
		f"approval=`{bool(risk.get('requires_user_approval'))}`: "
		f"{_redact_text(risk.get('summary')) or 'Structured risk requires attention.'}"
	)
	if evidence:
		line += f" evidence={evidence}."
	next_safe_action = _redact_text(risk.get("next_safe_action"))
	if next_safe_action:
		line += f" Next: {next_safe_action}"
	return line


def _render_warning_line(warning: dict[str, Any]) -> str:
	line = f"- `{_redact_text(warning.get('reason_code'), limit=120)}`"
	source = _redact_text(warning.get("source"), limit=160)
	if source:
		line += f" source=`{source}`"
	goal_id = _redact_text(warning.get("goal_id"), limit=120)
	if goal_id:
		line += f" goal=`{goal_id}`"
	if warning.get("available_count") is not None:
		line += f" available=`{warning.get('available_count')}`"
	if warning.get("inspected_count") is not None:
		line += f" inspected=`{warning.get('inspected_count')}`"
	detail = _redact_text(warning.get("detail"))
	return f"{line}: {detail}" if detail else line


def render_global_risks_markdown(payload: dict[str, Any]) -> str:
	if not payload.get("ok"):
		lines = [
			"# LoopX Global Risks Unavailable",
			"",
			"- ok: `False`",
			f"- error_code: `{_redact_text(payload.get('error_code'), limit=120)}`",
			f"- error: {_redact_text(payload.get('error'))}",
			*render_projection_envelope_markdown(payload.get("projection_envelope")),
		]
		omissions = [
			_redact_text(item)
			for item in as_list(payload.get("omissions"))
			if _redact_text(item)
		]
		if omissions:
			lines.extend(["", "## Omissions"])
			lines.extend(f"- {item}" for item in omissions)
		return "\n".join(lines)

	request = as_dict(payload.get("request"))
	summary = as_dict(payload.get("summary"))
	lines = [
		"# LoopX Global Risks",
		"",
		f"- command: `{_redact_text(request.get('command'), limit=120)}`",
		f"- time_range: `{_redact_text(request.get('time_range'), limit=40)}`",
		f"- matched: `{summary.get('matched_risk_count')}`",
		f"- returned: `{summary.get('returned_risk_count')}`",
		f"- truncated: `{bool(summary.get('truncated'))}`",
		*render_projection_envelope_markdown(payload.get("projection_envelope")),
		"",
		(
			"No current accepted source proves a rollback candidate; "
			"the rollback group is intentionally empty."
		),
		(
			"This read-only report does not authorize rollback, history rewrite, "
			"external cleanup, or merge."
		),
	]
	groups = as_dict(payload.get("groups"))
	for key, heading in (
		("stale_runs", "Stale Runs"),
		("boundary_warnings", "Boundary Warnings"),
		("failing_checks", "Failing Checks"),
		("rollback_candidates", "Rollback Candidates"),
	):
		lines.extend(["", f"## {heading}"])
		items = [item for item in as_list(groups.get(key)) if isinstance(item, dict)]
		if items:
			lines.extend(_render_risk_line(item) for item in items)
		else:
			lines.append("- None.")

	warnings = [
		item
		for item in as_list(payload.get("source_warnings"))
		if isinstance(item, dict)
	]
	if warnings:
		lines.extend(["", "## Source Warnings"])
		lines.extend(_render_warning_line(item) for item in warnings)
	lines.extend(
		[
			"",
			"## Boundary",
			"- Raw/private material omitted; local paths are not recorded.",
		]
	)
	return "\n".join(lines)
