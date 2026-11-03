"""Host source acquisition and Markdown codec for the typed global-risk read.

The facade exits when the CLI acquires status and project receipt sources in
TypeScript. Receipt files retain their Python persisted-format decoder.
"""
from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

from .control_plane.effect_runtime import effect_runtime_result
from .control_plane.projection_envelope_facts import render_projection_envelope_markdown

class _CollectStatus(Protocol):
	def __call__(
		self,
		*,
		registry_path: Path,
		runtime_root_override: str | None,
		limit: int,
	) -> dict[str, Any]: ...


class _ReadHostPollReceiptObservation(Protocol):
	def __call__(
		self,
		path: Path | None,
		*,
		expected_goal_id: str | None = None,
	) -> dict[str, Any]: ...


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
read_host_poll_receipt_observation = cast(
	_ReadHostPollReceiptObservation,
	getattr(
		import_module("loopx.control_plane.quota.host_poll_receipts"),
		"read_host_poll_receipt_observation",
	),
)
def as_dict(value: object) -> dict[str, Any]:
	return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
	return value if isinstance(value, list) else []


COMMAND = "/loopx-global-risks"


def _redact_text(value: object, *, limit: int = 260) -> str:
    return redact_public_text(value, limit=limit,
        replacements={"/loop-global-risks": COMMAND}, truncation_marker="…")


def _read_host_poll_sources(registry_path: Path, scan_limit: int) -> dict[str, Any]:
    try:
        registry = load_registry(registry_path)
    except FileNotFoundError:
        return {"read_status": "missing"}
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}
    goals = registry.get("goals") if isinstance(registry, dict) else None
    if not isinstance(goals, list):
        return {"read_status": "unreadable"}
    rows = []
    for goal in goals[:scan_limit]:
        observation = {"read_status": "missing", "receipt": None}
        if isinstance(goal, dict) and goal.get("id"):
            state_path = resolve_goal_local_path(goal.get("state_file"), goal,
                                                fallback_base=registry_path.parent)
            observation = read_host_poll_receipt_observation(
                receipt_path_for_goal(goal, state_path),
                expected_goal_id=str(goal.get("id") or ""))
        rows.append({"goal": goal, "observation": observation})
    return {"read_status": "read", "goals": rows, "goal_count": len(goals)}


def _transport_error_time_range(value: str) -> str:
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


def _transport_error_request(time_range: str) -> dict[str, Any]:
	return {
		"schema_version": "global_manager_command_request_v0",
		"command": COMMAND,
		"legacy_aliases": ["/loop-global-risks"],
		"cli_command": "loopx global-risks",
		"time_range": _transport_error_time_range(time_range),
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
    error: object, *, time_range: str = "24h",
    error_code: str = "global_risks_unavailable",
    status_payload: dict[str, Any] | None = None,
    status_read_at: str | None = None,
) -> dict[str, Any]:
    observed_at = now_utc_iso()
    try:
        return effect_runtime_result("presentation.global_risks.project", {
            "schema_version": "loopx_global_risks_read_v0", "observed_at": observed_at,
            "limit": 8, "agent_id": None, "time_range": time_range,
            "status": status_payload, "status_read_at": status_read_at,
            "error": _redact_text(error), "error_code": error_code,
        })
    except Exception:
        # Node failure cannot render its own error; retain the public wire codec.
        return {
            "ok": False, "schema_version": "global_manager_command_response_v0",
            "generated_at": observed_at, "request": _transport_error_request(time_range),
            "error_code": _redact_text(error_code, limit=120), "error": _redact_text(error),
            "omissions": ["Raw/private failure details and local paths were intentionally omitted."],
            "boundary": public_safe_boundary(),
        }



def build_global_risks(
    *, registry_path: Path, runtime_root_override: str | None,
    agent_id: str | None, time_range: str, limit: int,
) -> dict[str, Any]:
    # This bounds host IO to the existing source contract; TS owns result limits.
    scan_limit = min(max(min(max(1, limit), 100) * 4, 40), 400)
    try:
        status = collect_status(registry_path=registry_path,
            runtime_root_override=runtime_root_override, limit=scan_limit)
    except Exception as exc:
        return build_global_risks_error(exc, time_range=time_range,
                                      error_code="status_collection_unavailable")
    return effect_runtime_result("presentation.global_risks.project", {
        "schema_version": "loopx_global_risks_read_v0", "observed_at": now_utc_iso(),
        "limit": limit, "agent_id": agent_id, "time_range": time_range,
        "status": status, "host_poll": _read_host_poll_sources(registry_path, scan_limit),
    })


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
