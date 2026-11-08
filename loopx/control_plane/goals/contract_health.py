from __future__ import annotations

from typing import Any, Iterable, cast


CONTRACT_ERROR_DIAGNOSTIC_SCHEMA_VERSION = "loopx_contract_error_diagnostic_v0"
CONTRACT_ERROR_SCOPE_GLOBAL = "global"
CONTRACT_ERROR_SCOPE_GOAL = "goal"
CONTRACT_ERROR_SCOPE_GOALS = "goals"


def contract_error_diagnostic(
    *,
    code: str,
    message: str,
    goal_id: str | None = None,
    goal_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    safe_goal_id = str(goal_id or "").strip()
    safe_goal_ids = sorted(
        {
            str(item or "").strip()
            for item in goal_ids or []
            if str(item or "").strip()
        }
    )
    diagnostic: dict[str, Any] = {
        "schema_version": CONTRACT_ERROR_DIAGNOSTIC_SCHEMA_VERSION,
        "severity": "error",
        "scope": (
            CONTRACT_ERROR_SCOPE_GOAL
            if safe_goal_id
            else CONTRACT_ERROR_SCOPE_GOALS
            if safe_goal_ids
            else CONTRACT_ERROR_SCOPE_GLOBAL
        ),
        "code": str(code or "contract_error").strip() or "contract_error",
        "message": str(message or "").strip(),
    }
    if safe_goal_id:
        diagnostic["goal_id"] = safe_goal_id
    elif safe_goal_ids:
        diagnostic["goal_ids"] = safe_goal_ids
    return diagnostic


def contract_error_views(
    diagnostics: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    global_errors: list[str] = []
    goal_errors: dict[str, list[str]] = {}
    normalized: list[dict[str, Any]] = []
    for raw in diagnostics:
        diagnostic = dict(raw)
        message = str(diagnostic.get("message") or "").strip()
        if not message:
            continue
        normalized.append(diagnostic)
        errors.append(message)
        goal_id = str(diagnostic.get("goal_id") or "").strip()
        if (
            diagnostic.get("scope") == CONTRACT_ERROR_SCOPE_GOAL
            and goal_id
        ):
            goal_errors.setdefault(goal_id, []).append(message)
        elif diagnostic.get("scope") == CONTRACT_ERROR_SCOPE_GOALS:
            goal_ids = [
                str(item or "").strip()
                for item in diagnostic.get("goal_ids") or []
                if str(item or "").strip()
            ]
            if goal_ids:
                for affected_goal_id in goal_ids:
                    goal_errors.setdefault(affected_goal_id, []).append(message)
            else:
                global_errors.append(message)
        else:
            global_errors.append(message)
    return {
        "error_diagnostics": normalized,
        "errors": errors,
        "global_errors": global_errors,
        "goal_errors": goal_errors,
    }


def project_contract_health_for_goal(
    contract: dict[str, Any],
    *,
    goal_id: str | None,
) -> dict[str, Any]:
    safe_goal_id = str(goal_id or "").strip()
    if not safe_goal_id or "error_diagnostics" not in contract:
        return dict(contract)

    diagnostics = contract.get("error_diagnostics")
    if not isinstance(diagnostics, list):
        return dict(contract)
    selected_diagnostics = [
        item
        for item in diagnostics
        if isinstance(item, dict)
        and (
            item.get("scope") == CONTRACT_ERROR_SCOPE_GLOBAL
            or (
                item.get("scope") == CONTRACT_ERROR_SCOPE_GOAL
                and str(item.get("goal_id") or "").strip() == safe_goal_id
            )
            or (
                item.get("scope") == CONTRACT_ERROR_SCOPE_GOALS
                and safe_goal_id
                in {
                    str(goal_id or "").strip()
                    for goal_id in item.get("goal_ids") or []
                }
            )
        )
    ]
    error_views = contract_error_views(selected_diagnostics)
    summary = (
        dict(cast(dict[str, Any], contract.get("summary")))
        if isinstance(contract.get("summary"), dict)
        else {}
    )
    summary["errors"] = len(error_views["errors"])
    return {
        **contract,
        **error_views,
        "ok": not error_views["errors"],
        "summary": summary,
    }
