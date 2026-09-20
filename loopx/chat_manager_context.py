"""Per-turn evidence from Core, scoped before any Goal is read."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

from .chat_manager import manager_model_config
from .capabilities.steward_executor import load_effective_steward_executor_defaults
from .chat_manager_details import read_manager_goal_details
from .chat_manager_history import read_manager_delivery_history
from .goal_portfolio import build_goal_portfolio, lifecycle_readback_unavailable
from .chat import redact_local_paths
from .control_plane.collaboration import conversation_scope


# A progress question needs a bounded window, not a single day. One day of
# receipts cannot answer "what happened this week", and an unbounded window
# would grow the prompt-only managed transport without limit. These bounds are
# declared in the context so the answer states what it actually read.
MANAGER_EVIDENCE_WINDOW_DAYS = 7
MANAGER_EVIDENCE_PER_DAY_LIMIT = 8
MANAGER_EVIDENCE_TOTAL_LIMIT = 48
MANAGER_RECEIPT_DETAIL_POLICY = "latest_full_per_goal"
MANAGER_EVIDENCE_WINDOW_SCHEMA = "manager_evidence_window_v0"
MANAGER_EVIDENCE_MAX_WINDOW_DAYS = 30
# The window is an explicit operator decision, not a discovered fact: a wider
# window is a deliberate trade of prompt size for reach, so it is selected,
# bounded, and declared with its source instead of following an environment
# fact the operator never chose.
MANAGER_EVIDENCE_WINDOW_ENV_VAR = "LOOPX_MANAGER_EVIDENCE_WINDOW_DAYS"
MANAGER_EVIDENCE_WINDOW_SOURCE_PRODUCT_DEFAULT = "product_default"
MANAGER_EVIDENCE_WINDOW_SOURCE_EXPLICIT_CONFIG = "explicit_config"
MANAGER_EVIDENCE_WINDOW_SOURCE_EXPLICIT_ARGUMENT = "explicit_argument"
MANAGER_EVIDENCE_WINDOW_REASON_INVALID_EXPLICIT = "explicit_window_out_of_bounds"
# How the declared sources reach the model: an interactive endpoint reads them
# on demand through the read tool, a prompt-only segment can only receive them.
MANAGER_REMOTE_READ_INLINE = "inline_in_prompt"
MANAGER_REMOTE_READ_ON_DEMAND = "on_demand_tool"


def resolve_evidence_window_days(environ=None) -> tuple[int, str, str]:
    """Return the selected window, its source and a typed rejection reason.

    An explicit in-bounds value selects the window. A missing value keeps the
    shipped default, and an out-of-bounds or unreadable explicit value keeps the
    shipped default too while naming the reason, so a bad setting can never
    widen the prompt or silently answer a narrower question than declared.
    """

    values = os.environ if environ is None else environ
    raw = str(values.get(MANAGER_EVIDENCE_WINDOW_ENV_VAR, "") or "").strip()
    if not raw:
        return (
            MANAGER_EVIDENCE_WINDOW_DAYS,
            MANAGER_EVIDENCE_WINDOW_SOURCE_PRODUCT_DEFAULT,
            "",
        )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = None
    if value is None or not 1 <= value <= MANAGER_EVIDENCE_MAX_WINDOW_DAYS:
        return (
            MANAGER_EVIDENCE_WINDOW_DAYS,
            MANAGER_EVIDENCE_WINDOW_SOURCE_PRODUCT_DEFAULT,
            MANAGER_EVIDENCE_WINDOW_REASON_INVALID_EXPLICIT,
        )
    return (
        value,
        MANAGER_EVIDENCE_WINDOW_SOURCE_EXPLICIT_CONFIG,
        "",
    )


def _declared_sources(
    runtime_root, channel_id, owner_scope, config_path=None
) -> list[dict[str, Any]]:
    """Declare the evidence sources; declaring a source never reads it.

    The declaration reads the same SSH config the Turn-time source read uses,
    so one packet cannot declare a host unconfigured and read it in the same
    Turn.
    """
    local = [{"source_id": "local", "source_host": "local", "status": "available"}]
    try:
        from .capabilities.manager_context.ssh_evidence import sources

        declared = sources(runtime_root, channel_id, owner_scope, config_path)
    except (OSError, ValueError, TypeError, RuntimeError, ImportError):
        return local
    return declared or local


def _bounded_receipts(history: dict[str, Any]) -> dict[str, Any]:
    """Keep each Goal's newest receipt whole and compact the older ones."""
    deliveries = history.get("deliveries")
    if history.get("status") != "read" or not isinstance(deliveries, list):
        return history
    bounded = []
    for index, row in enumerate(deliveries):
        if not isinstance(row, dict):
            continue
        if index == 0:
            bounded.append({**row, "receipt_detail": "full"})
            continue
        details = row.get("recorded_details")
        details = details if isinstance(details, dict) else {}
        bounded.append(
            {
                "recorded_at": row.get("recorded_at"),
                "goal_id": row.get("goal_id"),
                "agent_id": row.get("agent_id"),
                "todo_id": row.get("todo_id"),
                "classification": row.get("classification"),
                "reported_follow_up": row.get("reported_follow_up"),
                "outcome": row.get("outcome"),
                "verification": row.get("verification"),
                "result_class": details.get("result_class"),
                "probe_kind": details.get("probe_kind"),
                "surface_id": details.get("surface_id"),
                "evidence_count": row.get("evidence_count"),
                "receipt_detail": "compact",
            }
        )
    return {**history, "deliveries": bounded}


def _evidence_window(
    rows: list[dict[str, Any]],
    runtime_root,
    session: dict[str, Any],
    owner_scope: bool,
    days: int,
    include_details: bool,
    *,
    days_source: str = MANAGER_EVIDENCE_WINDOW_SOURCE_PRODUCT_DEFAULT,
    days_reason: str = "",
    remote_read: str = MANAGER_REMOTE_READ_ON_DEMAND,
    config_path=None,
    local_only: bool = False,
) -> dict[str, Any]:
    """Declare exactly what the per-turn evidence read covered."""
    matched_by_day: dict[str, int] = {}
    matched = included = omitted = invalid = 0
    full = compact = 0
    window_start = window_end = observed_at = None
    limitations: list[str] = []
    statuses: set[str] = set()
    for row in rows:
        history = row.get("recent_delivery_history") or {}
        if history.get("status"):
            statuses.add(str(history["status"]))
        coverage = history.get("coverage") or {}
        for day, count in (coverage.get("matched_by_day") or {}).items():
            matched_by_day[str(day)] = matched_by_day.get(str(day), 0) + int(count or 0)
        matched += int(coverage.get("matched") or 0)
        included += int(coverage.get("included") or 0)
        omitted += int(coverage.get("omitted") or 0)
        invalid += int(coverage.get("invalid_delivery_records") or 0)
        window_start = window_start or history.get("window_start")
        window_end = window_end or history.get("window_end")
        observed_at = observed_at or history.get("observed_at")
        for delivery in history.get("deliveries") or []:
            if delivery.get("receipt_detail") == "full":
                full += 1
            elif delivery.get("receipt_detail") == "compact":
                compact += 1
        for limitation in history.get("limitations") or []:
            if limitation not in limitations:
                limitations.append(str(limitation))
    if window_start is None or window_end is None:
        now = datetime.now().astimezone()
        start = (now - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        window_start, window_end = start.isoformat(), now.isoformat()
    if not include_details:
        read_status = "not_read"
    elif "read" in statuses and statuses - {"read"}:
        read_status = "partial"
    elif "read" in statuses:
        read_status = "read"
    elif statuses:
        read_status = "unavailable"
    else:
        read_status = "not_read"
    sources = [{"source_id": "local", "source_host": "local", "status": "available"}] if local_only else _declared_sources(
        runtime_root,
        str(session.get("channel_id") or "manager"),
        owner_scope,
        config_path,
    )
    return {
        "schema_version": MANAGER_EVIDENCE_WINDOW_SCHEMA,
        "days": days,
        "days_source": days_source,
        "days_default": MANAGER_EVIDENCE_WINDOW_DAYS,
        "days_env_var": MANAGER_EVIDENCE_WINDOW_ENV_VAR,
        "days_reason": days_reason,
        "days_bounds": {
            "min": 1,
            "max": MANAGER_EVIDENCE_MAX_WINDOW_DAYS,
        },
        "window_start": window_start,
        "window_end": window_end,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "applies_to": "recent_delivery_history",
        "remote_read": remote_read,
        "read_status": read_status,
        "per_day_limit": MANAGER_EVIDENCE_PER_DAY_LIMIT,
        "total_limit": MANAGER_EVIDENCE_TOTAL_LIMIT,
        "receipt_detail_policy": MANAGER_RECEIPT_DETAIL_POLICY,
        "receipts_by_detail": {"full": full, "compact": compact},
        "matched": matched,
        "included": included,
        "omitted": omitted,
        "matched_by_day": matched_by_day,
        "invalid_delivery_records": invalid,
        "sources": sources,
        "declared_unread_sources": [
            str(source.get("source_id"))
            for source in sources
            if source.get("status") != "available"
        ],
        "limitations": limitations,
    }


def _remote_evidence(
    runtime_root: Path,
    session: dict[str, Any],
    owner_scope: bool,
    window_days: int,
    *,
    runner=None,
    scope_valid: Callable[[], bool] | None = None,
    config_path=None,
) -> dict[str, Any]:
    """Read the declared sources inside one Turn budget; never fail the Turn."""

    from .capabilities.manager_context.ssh_evidence import (
        MANAGER_REMOTE_EVIDENCE_SCHEMA,
        remote_evidence,
    )

    kwargs: dict[str, Any] = {}
    if runner is not None:
        kwargs["runner"] = runner
    if config_path is not None:
        kwargs["config_path"] = config_path
    try:
        return remote_evidence(
            runtime_root,
            str(session.get("channel_id") or "manager"),
            owner_scope,
            window_days=window_days,
            scope_valid=scope_valid or (lambda: True),
            **kwargs,
        )
    except (OSError, ValueError, TypeError, RuntimeError, ImportError):
        # A failed source read is a declared coverage gap, never a Turn failure
        # and never evidence that a remote Goal made no progress.
        return {
            "schema_version": MANAGER_REMOTE_EVIDENCE_SCHEMA,
            "applies_to": "declared_ssh_sources",
            "window_days": window_days,
            "read_status": "unavailable",
            "sources": [],
            "rows": [],
            "stale_rows_included": False,
            "limitations": ["remote_source_read_failed"],
        }


def manager_authorization_scope_id(goal_ids: list[str], *, runtime_root=None, channel_id=None) -> str:
    """Opaque identity for the exact external Goal evidence scope."""
    normalized = sorted(set(goal_ids))
    if runtime_root is not None and channel_id:
        from .capabilities.manager_context.ssh_evidence import grants
        remote = grants(runtime_root, channel_id)
        if remote:
            normalized.append("ssh_evidence:" + json.dumps(remote, sort_keys=True))
    return hashlib.sha256(
        json.dumps(normalized, separators=(",", ":")).encode()
    ).hexdigest()


def manager_turn_context(
    registry_path: Path | None,
    session: dict[str, Any],
    runtime_root: Path,
    *,
    authorized_goal_ids: list[str] | None = None,
    include_details: bool = True,
    evidence_window_days: int | None = None,
    remote_evidence: bool = False,
    remote_runner=None,
    remote_scope_valid: Callable[[], bool] | None = None,
    remote_config_path=None,
) -> dict[str, Any]:
    conversation = conversation_scope(session)
    local_goal = conversation["kind"] == "owner_goal"
    if evidence_window_days is None:
        evidence_window_days, days_source, days_reason = resolve_evidence_window_days()
    elif (
        type(evidence_window_days) is int
        and 1 <= evidence_window_days <= MANAGER_EVIDENCE_MAX_WINDOW_DAYS
    ):
        days_source, days_reason = (
            MANAGER_EVIDENCE_WINDOW_SOURCE_EXPLICIT_ARGUMENT,
            "",
        )
    else:
        raise ValueError(
            f"evidence_window_days must be 1..{MANAGER_EVIDENCE_MAX_WINDOW_DAYS}"
        )
    window_kwargs = {
        "local_only": local_goal,
        "days_source": days_source,
        "days_reason": days_reason,
        "remote_read": (
            MANAGER_REMOTE_READ_INLINE if remote_evidence else MANAGER_REMOTE_READ_ON_DEMAND
        ),
        # One SSH config path for the whole packet: the declaration and the
        # source read must not disagree about which hosts exist.
        "config_path": remote_config_path,
    }
    owner_scope = conversation["private_conversation"]
    scope = conversation["goal_ids"] if owner_scope else authorized_goal_ids
    if not owner_scope and not scope:
        return unavailable_manager_context(
            "external_authorization_unavailable",
            evidence_window=_evidence_window(
                [],
                runtime_root,
                session,
                owner_scope,
                evidence_window_days,
                False,
                **window_kwargs,
            ),
        )
    if registry_path is None:
        return unavailable_manager_context(
            "registry_unavailable",
            evidence_window=_evidence_window(
                [],
                runtime_root,
                session,
                owner_scope,
                evidence_window_days,
                False,
                **window_kwargs,
            ),
        )
    portfolio = build_goal_portfolio(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_ids=scope,
        limit=128,
        include_stopped=False,
        # The steward's answer order names the Goal's milestones and baseline,
        # so the lifecycle projection the status collector already derived
        # travels with the row instead of being reconstructed from todos.
        include_goal_lifecycle=True,
    )
    labels: dict[str, str] = {}
    try:
        raw = registry_path.read_bytes()
        if (
            portfolio.get("inventory_revision")
            == "sha256:" + hashlib.sha256(raw).hexdigest()
        ):
            for goal in json.loads(raw).get("goals", []):
                if isinstance(goal, dict) and (
                    scope is None or goal.get("id") in scope
                ):
                    labels[str(goal.get("id"))] = redact_local_paths(
                        str(
                            goal.get("display_name")
                            or goal.get("domain")
                            or goal.get("id")
                            or ""
                        ),
                        protected_paths=[Path(str(goal.get("repo") or "."))],
                    )[:100]
    except (OSError, ValueError, TypeError):
        pass
    rows = []
    for row in portfolio.get("goals", []):
        read_details = include_details and row.get("activation_state") != "stopped"
        history = (
            _bounded_receipts(
                read_manager_delivery_history(
                    runtime_root,
                    row["goal_id"],
                    lookback_days=evidence_window_days,
                    limit=MANAGER_EVIDENCE_PER_DAY_LIMIT,
                    total_limit=MANAGER_EVIDENCE_TOTAL_LIMIT,
                )
            )
            if read_details
            else {"status": "not_read", "deliveries": []}
        )
        rows.append(
            {
                "goal_id": row["goal_id"],
                "activation_state": row.get("activation_state", "unknown"),
                "host_id": row.get("host_id"),
                "project_id": row.get("project_id"),
                "agent_coverage": row.get("agent_coverage"),
                "description": labels.get(row["goal_id"]),
                "quality": row.get("quality"),
                "progress": row.get("progress", "unknown"),
                "source": row.get("source"),
                "warnings": row.get("warnings", []),
                # Present for every row this turn's portfolio attempted; the
                # portfolio already substitutes a typed gap for a Goal it could
                # not read, and this floor keeps a future portfolio path from
                # turning that contract back into a silent null.
                "goal_lifecycle": row.get("goal_lifecycle")
                or lifecycle_readback_unavailable(
                    row["goal_id"], "portfolio_row_missing_lifecycle"
                ),
                "agents": [
                    {
                        "agent_id": a.get("agent_id"),
                        "source_verified": a.get("source_verified"),
                        "waiting_on": a.get("waiting_on"),
                        "owner_gate_ids": a.get("owner_gate_ids", []),
                        "todo_count_in_projection": len(a.get("todos", [])),
                    }
                    for a in row.get("agents", [])
                ],
                "deliveries": row.get("deliveries", []),
                "recent_delivery_history": history,
                "current_todos": read_manager_goal_details(
                    registry_path, runtime_root, row["goal_id"], owner_scope=owner_scope,
                    completed_todo_ids={r["todo_id"] for r in history["deliveries"]},
                ) if read_details else {"status": "not_read", "todos": []},
            }
        )
    result = {
        "schema_version": "manager_turn_context_v1",
        "scope": "owner_goal" if local_goal else "owner_global" if owner_scope else "external_goal_scope",
        **({} if local_goal else {"model_defaults": manager_model_config(
            machine_defaults=load_effective_steward_executor_defaults(runtime_root)
        )}),
        "snapshot_id": portfolio.get("snapshot_id"),
        "collected_at": portfolio.get("collected_at"),
        "collection_completed_at": portfolio.get("collection_completed_at"),
        "coverage": portfolio.get("coverage"),
        "goals": rows,
        "warnings": portfolio.get("warnings", []),
        "limitations": portfolio.get("limitations", []),
        "evidence_window": _evidence_window(
            rows,
            runtime_root,
            session,
            owner_scope,
            evidence_window_days,
            include_details,
            **window_kwargs,
        ),
    }
    if remote_evidence and not local_goal:
        result["remote_evidence"] = _remote_evidence(
            runtime_root,
            session,
            owner_scope,
            evidence_window_days,
            runner=remote_runner,
            scope_valid=remote_scope_valid,
            config_path=remote_config_path,
        )
    result["portfolio_snapshot_id"] = result["snapshot_id"]
    result["collection_completed_at"] = datetime.now(timezone.utc).isoformat()
    result["snapshot_id"] = "sha256:" + hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    if not owner_scope:
        result["authorization_scope_id"] = manager_authorization_scope_id(scope or [], runtime_root=runtime_root, channel_id=session.get("channel_id"))
    return result


def unavailable_manager_context(
    reason: str, *, evidence_window: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "manager_turn_context_v1",
        "coverage": {"discovered": None, "verified": 0, "complete": False},
        "goals": [],
        "warnings": [reason],
        **({"evidence_window": evidence_window} if evidence_window else {}),
    }


def collect_manager_turn_context(
    registry_path: Path | None,
    session: dict[str, Any],
    runtime_root: Path,
    scope_resolver: Callable[[dict[str, Any]], list[str] | None] | None = None,
    *,
    include_details: bool = True,
    remote_evidence: bool = False,
    remote_runner=None,
) -> dict[str, Any]:
    if conversation_scope(session)["private_conversation"]:
        return manager_turn_context(
            registry_path,
            session,
            runtime_root,
            include_details=include_details,
            remote_evidence=remote_evidence,
            remote_runner=remote_runner,
        )

    def resolve() -> list[str] | None:
        try:
            scope = scope_resolver(session) if scope_resolver else None
            if not isinstance(scope, list) or any(
                not isinstance(g, str) for g in scope
            ):
                return None
            return sorted(set(scope))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            return None

    before = resolve()
    context = manager_turn_context(
        registry_path,
        session,
        runtime_root,
        authorized_goal_ids=before,
        include_details=include_details,
        remote_evidence=remote_evidence,
        remote_runner=remote_runner,
        # The Turn owner already re-resolves the external scope after the local
        # collection; the source read checks the same exact scope around it.
        remote_scope_valid=lambda: before == resolve(),
    )
    if before != resolve():
        return unavailable_manager_context("external_authorization_changed")
    return context
