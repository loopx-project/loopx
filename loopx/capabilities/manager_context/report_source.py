"""Local portfolio IO for periodic-report, through the original scoped reader.

This is an evidence source, not a scheduler, editorial agent or publication
owner. Collection is explicit. No subscription, artifact or cursor is written.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from ...chat_manager_history import delivery_history_window
from ..periodic_report.adapters import PeriodicReportSourceAdapter, build_periodic_report_source_result

SOURCE_ID = "manager_portfolio"
SOURCE_KIND = "authorized_portfolio_evidence"


class _SourceGap(ValueError):
    pass


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()


def collect_manager_report_source(
    read: Callable[[dict[str, Any]], dict[str, Any]], request: Mapping[str, Any], *,
    max_calls: int = 512, max_rows: int = 4000, max_bytes: int = 4 * 1024 * 1024,
) -> dict[str, Any]:
    """Freeze permitted read models, including gaps, in one bounded IO pass.

    The host supplies its original reader; a Goal name or profile never supplies
    authority. Index and Todo revisions are checked within each paged source.
    Current state is observed now, not reconstructed as of the window's end.
    """
    bounds = delivery_history_window(request.get("window_start"), request.get("window_end"))
    if bounds is None:
        raise ValueError("a portfolio report requires an exact evidence window")
    start, end = bounds
    observed = datetime.now(timezone.utc)
    if end > observed:
        raise ValueError("the portfolio report window is not complete")
    window = {"window_start": start.isoformat(), "window_end": end.isoformat()}
    rows, gaps, sources = [], [], []
    calls, size = 0, 0

    def invoke(arguments):
        nonlocal calls
        if calls >= max_calls:
            raise _SourceGap("read_budget_exhausted")
        calls += 1
        try:
            page = read(arguments)
        except PermissionError:
            raise
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise _SourceGap("source_read_unavailable") from exc
        if not isinstance(page, dict):
            raise _SourceGap("invalid_source_response")
        if page.get("error") == "authorization_changed":
            # Never return partial material after revocation, even if earlier
            # pages were allowed. The original host also fences every read.
            raise PermissionError("the original report scope changed")
        if page.get("ok") is not True:
            raise _SourceGap(str(page.get("error") or "source_unavailable"))
        if page.get("unknown") or page.get("oversized_rows"):
            gaps.append({"goal_id": arguments.get("goal_id"),
                "view": arguments["view"], "reason": "source_coverage_incomplete"})
        return page

    def pages(arguments):
        offset, revision, result = 0, None, []
        while True:
            query = {**arguments, "offset": offset, "limit": 12}
            if revision and arguments["view"] == "deliveries":
                query["expected_source_revision"] = revision
            try:
                page = invoke(query)
            except _SourceGap as exc:
                if arguments["view"] != "portfolio":
                    raise
                gaps.append({"view": "portfolio", "reason": str(exc)})
                return result, revision
            source = page.get("source") or {}
            current = source.get("source_revision")
            if source.get("status") in {"unavailable", "conflicting"}:
                raise _SourceGap("source_" + source["status"])
            if revision is not None and current != revision:
                raise _SourceGap("source_revision_changed")
            revision = current
            raw = page.get("rows")
            if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
                raise _SourceGap("invalid_source_page")
            result.extend(raw)
            if len(result) > max_rows or len(json.dumps(result, ensure_ascii=False).encode()) > max_bytes:
                raise _SourceGap("source_size_budget_exhausted")
            next_offset = page.get("next_offset")
            if next_offset is None:
                sources.append({"goal_id": arguments.get("goal_id"), "view": arguments["view"],
                    "revision": revision, "matched": page.get("matched"),
                    "observed_at": source.get("observed_at"), "status": source.get("status"),
                    "coverage": source.get("coverage"), "unknown": page.get("unknown", False)})
                return result, revision
            if type(next_offset) is not int or next_offset <= offset or not raw:
                raise _SourceGap("nonadvancing_source_page")
            offset = next_offset

    def retain(kind, goal_id, evidence):
        nonlocal size
        row = {"kind": kind, "goal_id": goal_id, "evidence": evidence}
        row["source_ref"] = _digest(row)
        encoded = len(json.dumps(row, ensure_ascii=False).encode())
        if len(rows) >= max_rows or size + encoded > max_bytes:
            raise _SourceGap("snapshot_size_budget_exhausted")
        rows.append(row)
        size += encoded

    try:
        goals, _ = pages({"view": "portfolio", "include_stopped": True})
    except _SourceGap as exc:
        goals = []
        gaps.append({"view": "portfolio", "reason": str(exc)})
    inventory = {}
    for goal in goals:
        gid = goal.get("goal_id")
        if not isinstance(gid, str) or not gid or gid in inventory:
            gaps.append({"view": "portfolio", "reason": "invalid_or_duplicated_goal_identity"})
        else:
            inventory[gid] = goal
    goals, ids = list(inventory.values()), list(inventory)
    for goal in goals:
        gid = goal["goal_id"]
        try:
            retain("goal", gid, goal)
            for view in ("todos", "deliveries", "handoffs"):
                begin, prior_size = len(rows), size
                arguments = {"view": view, "goal_id": gid}
                if view == "deliveries":
                    arguments.update(window, include_outcome_gaps=True)
                try:
                    evidence, revision = pages(arguments)
                    # Preserve complete allowed detail and every referenced
                    # task, including completed records outside the overview.
                    tasks = set()
                    for item in evidence:
                        if view == "deliveries":
                            full = invoke({**arguments, "delivery_ref": item["source_ref"],
                                "expected_source_revision": revision, "limit": 1})
                            if full.get("source", {}).get("status") != "read" or len(full.get("rows", [])) != 1:
                                raise _SourceGap("delivery_detail_unavailable_or_changed")
                            item = full["rows"][0]
                        elif view == "todos" and item.get("content_truncated"):
                            exact = invoke({"view": "todos", "goal_id": gid,
                                "todo_id": item["todo_id"], "limit": 1})
                            if len(exact.get("rows", [])) != 1:
                                raise _SourceGap("task_detail_unavailable")
                            item = exact["rows"][0]
                        retain(view, gid, item)
                        if view == "deliveries" and item.get("todo_id"):
                            tasks.add(item["todo_id"])
                    if view == "todos":
                        check = invoke({"view": "todos", "goal_id": gid, "limit": 1})
                        if check.get("source", {}).get("source_revision") != revision:
                            raise _SourceGap("task_source_changed_during_collection")
                    for task in sorted(tasks):
                        exact = invoke({"view": "todos", "goal_id": gid, "todo_id": task, "limit": 1})
                        if len(exact.get("rows", [])) != 1:
                            gaps.append({"goal_id": gid, "view": "todos", "reason": "delivery_task_unavailable"})
                        else:
                            retain("delivery_task", gid, exact["rows"][0])
                except _SourceGap as exc:
                    del rows[begin:]
                    size = prior_size
                    gaps.append({"goal_id": gid, "view": view, "reason": str(exc)})
        except _SourceGap as exc:
            gaps.append({"goal_id": gid, "reason": str(exc)})
    # Re-read the inventory through the same grant. Churn cannot silently hide
    # a newly registered or removed Goal behind a successful old page sequence.
    try:
        current, _ = pages({"view": "portfolio", "include_stopped": True})
        if [goal.get("goal_id") for goal in current] != ids:
            gaps.append({"view": "portfolio", "reason": "portfolio_changed_during_collection"})
    except _SourceGap as exc:
        gaps.append({"view": "portfolio", "reason": str(exc)})
    gaps = list({_digest(gap): gap for gap in gaps}.values())
    # Coverage is paged alongside evidence instead of copying an arbitrarily
    # large inventory/source/gap directory into every tool response header.
    metadata = [{"kind": "source_observation", "goal_id": item.get("goal_id"), "evidence": item}
                for item in sources]
    metadata += [{"kind": "coverage_gap", "goal_id": item.get("goal_id"), "evidence": item} for item in gaps]
    seen = set()
    for item in metadata:
        ref = _digest(item)
        if ref not in seen:
            rows.append({**item, "source_ref": ref})
            seen.add(ref)
    snapshot = {"window": window, "window_bounds": "[start,end)",
        "observed_at": observed.isoformat(), "goal_ids": ids, "rows": rows,
        "sources": sources, "gaps": gaps,
        "coverage": {"complete": not gaps, "goals_in_inventory": len(ids),
            "included_records": len(rows), "read_calls": calls,
            "referenced_artifacts_read": False, "archived_todos_read": False,
            "current_state_is_window_end_state": False}}
    snapshot["source_revision"] = _digest(snapshot)
    # This source deliberately supplies supporting evidence, not machine-
    # authored business judgments. The existing report Agent owns analysis.
    items = [{"item_id": "coverage", "title": "本期读取范围",
            "summary": f"固定窗口 {window['window_start']} 至 {window['window_end']}；读取 {len(ids)} 个获准本机 Goal（含停止项）。"
                + ("存在读取缺口，不能据空结果认定没有进展。" if gaps else "已完成本次来源分页；记录中的主张及引用产物仍须核验。"),
            "content_kind": "coverage", "visibility": "supporting", "status": "partial" if gaps else "read",
            "source_ref": snapshot["source_revision"]}]
    # The adapter contract bounds each supporting item. Split *data*, without
    # silently clipping evidence or pretending this is an authored report.
    # Consumers concatenate parts with the same source_ref to recover a row.
    for row in rows:
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True)
        ref = row.get("source_ref") or _digest(row)
        parts = [encoded[i:i + 400] for i in range(0, len(encoded), 400)]
        for i, part in enumerate(parts):
            items.append({"item_id": "evidence_" + ref.split(":")[1] + f"_{i:06d}",
                "title": f"{row.get('goal_id') or 'portfolio'} / {row['kind']} / {i + 1} of {len(parts)}"[:240],
                "summary": json.dumps(part, ensure_ascii=False), "source_ref": ref, "content_kind": "coverage",
                "visibility": "supporting", "status": "recorded"})
    sections = [{"section_id": "portfolio_evidence", "title": "授权项目证据数据（未经业务判断）", "order": 10, "items": items}]
    snapshot["source_result"] = build_periodic_report_source_result(
        source_id=SOURCE_ID, source_kind=SOURCE_KIND, status="partial" if gaps else "complete",
        observed_at=observed.isoformat(), sections=sections, snapshot_ref=snapshot["source_revision"], retryable=bool(gaps))
    return snapshot


def manager_portfolio_report_source_adapter(read) -> PeriodicReportSourceAdapter:
    """Compose with periodic-report using an already authorized host reader."""
    return PeriodicReportSourceAdapter(source_id=SOURCE_ID, source_kind=SOURCE_KIND,
        collect=lambda request: collect_manager_report_source(read, request)["source_result"])
