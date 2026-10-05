from __future__ import annotations

from datetime import datetime
from typing import Any

# Refs #4447: one definition for this vocabulary. The supplement builds and
# validates these fields and carries the schema name this projection compares
# against, so it owns both; `repository_snapshot` already imports the schema
# name from there rather than restating it. `PROJECTION_SCHEMA_VERSION` below is
# a different document's version that happens to share a name prefix, so it stays.
# The same rule runs the other way for the snapshot name: `repository_snapshot`
# builds and stamps those payloads and this file only compares against them, so
# the value is imported rather than restated here.
from .metrics_supplement import (
    _SUPPLEMENT_FIELDS,
    SUPPLEMENT_SCHEMA_VERSION,
)
from .repository_snapshot import SNAPSHOT_SCHEMA_VERSION

PROJECTION_SCHEMA_VERSION = "issue_fix_metrics_projection_v0"

_FLOW_FIELDS = (
    "issues_opened",
    "issues_closed",
    "pull_requests_opened",
    "pull_requests_closed",
    "pull_requests_merged",
)


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _timestamp(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from None
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return text


def _optional_https_url(value: Any, *, field: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith("https://") or any(char.isspace() for char in text):
        raise ValueError(f"{field} must be an https URL")
    return text


def _snapshot(
    value: dict[str, Any],
    *,
    expected_repo: str,
    role: str,
) -> dict[str, Any]:
    if value.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"{role} snapshot must use {SNAPSHOT_SCHEMA_VERSION}")
    repo = str(value.get("repo") or "").strip()
    if repo != expected_repo:
        raise ValueError(f"{role} snapshot repo must match --repo")
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "repo": repo,
        "captured_at": _timestamp(
            value.get("captured_at"), field=f"{role}.captured_at"
        ),
        "open_issues": _nonnegative_int(
            value.get("open_issues"), field=f"{role}.open_issues"
        ),
        "open_pull_requests": _nonnegative_int(
            value.get("open_pull_requests"),
            field=f"{role}.open_pull_requests",
        ),
    }
    source_url = _optional_https_url(
        value.get("source_url"), field=f"{role}.source_url"
    )
    if source_url:
        snapshot["source_url"] = source_url
    health = value.get("health")
    if health is not None:
        if not isinstance(health, dict):
            raise ValueError(f"{role}.health must be an object")
        snapshot["health"] = {
            str(key): _nonnegative_int(item, field=f"{role}.health.{key}")
            for key, item in sorted(health.items())
            if str(key).strip()
        }
    flow = value.get("flow_since_baseline")
    if role == "current":
        if not isinstance(flow, dict):
            raise ValueError("current.flow_since_baseline must be an object")
        snapshot["flow_since_baseline"] = {
            field: _nonnegative_int(
                flow.get(field), field=f"current.flow_since_baseline.{field}"
            )
            for field in _FLOW_FIELDS
        }
    elif flow:
        raise ValueError("baseline snapshot must not include flow_since_baseline")
    issue_states = value.get("issue_states")
    if issue_states is not None:
        if not isinstance(issue_states, list):
            raise ValueError(f"{role}.issue_states must be a list")
        compact_states: list[dict[str, Any]] = []
        for index, item in enumerate(issue_states):
            if not isinstance(item, dict):
                raise ValueError(f"{role}.issue_states[{index}] must be an object")
            issue_ref = str(item.get("issue_ref") or "").strip()
            state = str(item.get("state") or "").strip().upper()
            if not issue_ref or state not in {"OPEN", "CLOSED"}:
                raise ValueError(
                    f"{role}.issue_states[{index}] requires issue_ref and OPEN/CLOSED"
                )
            compact = {"issue_ref": issue_ref, "state": state}
            if item.get("closed_at"):
                compact["closed_at"] = _timestamp(
                    item.get("closed_at"),
                    field=f"{role}.issue_states[{index}].closed_at",
                )
            compact_states.append(compact)
        snapshot["issue_states"] = compact_states
    pull_request_states = value.get("pull_request_states")
    if pull_request_states is not None:
        if not isinstance(pull_request_states, list):
            raise ValueError(f"{role}.pull_request_states must be a list")
        compact_pr_states: list[dict[str, Any]] = []
        for index, item in enumerate(pull_request_states):
            if not isinstance(item, dict):
                raise ValueError(
                    f"{role}.pull_request_states[{index}] must be an object"
                )
            pr_ref = str(item.get("pr_ref") or "").strip()
            state = str(item.get("state") or "").strip().upper()
            ci = str(item.get("ci") or "UNKNOWN").strip().upper()
            review = str(item.get("review") or "UNKNOWN").strip().upper()
            if not pr_ref or state not in {"OPEN", "MERGED", "CLOSED"}:
                raise ValueError(
                    f"{role}.pull_request_states[{index}] requires pr_ref and "
                    "OPEN/MERGED/CLOSED"
                )
            if ci not in {"PASSING", "FAILING", "PENDING", "UNKNOWN"}:
                raise ValueError(f"{role}.pull_request_states[{index}].ci is invalid")
            if review not in {
                "APPROVED",
                "CHANGES_REQUESTED",
                "REVIEW_REQUIRED",
                "UNKNOWN",
            }:
                raise ValueError(
                    f"{role}.pull_request_states[{index}].review is invalid"
                )
            compact_pr = {
                "pr_ref": pr_ref,
                "state": state,
                "ci": ci,
                "review": review,
            }
            if item.get("created_at"):
                compact_pr["created_at"] = _timestamp(
                    item.get("created_at"),
                    field=f"{role}.pull_request_states[{index}].created_at",
                )
            if item.get("updated_at"):
                compact_pr["updated_at"] = _timestamp(
                    item.get("updated_at"),
                    field=f"{role}.pull_request_states[{index}].updated_at",
                )
            compact_pr_states.append(compact_pr)
        snapshot["pull_request_states"] = compact_pr_states
    return snapshot


def _supplement(value: dict[str, Any] | None) -> dict[str, int | None]:
    if value is None:
        return {field: None for field in _SUPPLEMENT_FIELDS}
    if value.get("schema_version") != SUPPLEMENT_SCHEMA_VERSION:
        raise ValueError(f"supplement must use {SUPPLEMENT_SCHEMA_VERSION}")
    counts = value.get("counts")
    if not isinstance(counts, dict):
        raise ValueError("supplement.counts must be an object")
    return {
        field: (
            _nonnegative_int(counts[field], field=f"supplement.counts.{field}")
            if field in counts
            else None
        )
        for field in _SUPPLEMENT_FIELDS
    }


def _first_push_ci_coverage(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    coverage = value.get("coverage")
    first_push = coverage.get("first_push_ci") if isinstance(coverage, dict) else None
    if not isinstance(first_push, dict):
        return None
    eligible = _nonnegative_int(
        first_push.get("eligible_prs"),
        field="supplement.coverage.first_push_ci.eligible_prs",
    )
    observed = _nonnegative_int(
        first_push.get("observed_prs"),
        field="supplement.coverage.first_push_ci.observed_prs",
    )
    if observed > eligible:
        raise ValueError(
            "supplement.coverage.first_push_ci observed_prs must not exceed eligible_prs"
        )
    complete = first_push.get("complete") is True
    if complete != (eligible > 0 and observed == eligible):
        raise ValueError(
            "supplement.coverage.first_push_ci complete must match observed coverage"
        )
    return {
        "eligible_prs": eligible,
        "observed_prs": observed,
        "complete": complete,
    }


def _issue_close_activity(
    value: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not isinstance(value, dict):
        return [], None
    raw_activity = value.get("issue_close_activity")
    coverage = value.get("coverage")
    raw_coverage = (
        coverage.get("issue_close_activity") if isinstance(coverage, dict) else None
    )
    compact_coverage = None
    if isinstance(raw_coverage, dict):
        observed_issues = _nonnegative_int(
            raw_coverage.get("observed_issues"),
            field="supplement.coverage.issue_close_activity.observed_issues",
        )
        compact_coverage = {
            "source": str(raw_coverage.get("source") or "not_supplied").strip(),
            "observed_issues": observed_issues,
            "complete": raw_coverage.get("complete") is True,
        }
    if raw_activity is None:
        return [], compact_coverage
    if not isinstance(raw_activity, list):
        raise ValueError("supplement.issue_close_activity must be a list")

    known_types = {
        "issue_close_recommended",
        "issue_close_request_published",
        "issue_closed_observed",
        "issue_reopened_observed",
    }
    seen_issue_refs: set[str] = set()
    seen_event_ids: set[str] = set()
    activity: list[dict[str, Any]] = []
    for row_index, row in enumerate(raw_activity):
        if not isinstance(row, dict) or row.get("schema_version") != (
            "issue_fix_issue_close_activity_v0"
        ):
            raise ValueError(
                f"supplement.issue_close_activity[{row_index}] uses an unsupported schema"
            )
        issue_ref = str(row.get("issue_ref") or "").strip()
        events = row.get("events")
        if (
            not issue_ref
            or issue_ref in seen_issue_refs
            or not isinstance(events, list)
        ):
            raise ValueError(
                "supplement issue close activity requires unique issue_ref values and events"
            )
        seen_issue_refs.add(issue_ref)
        compact_events: list[dict[str, str]] = []
        for event_index, event in enumerate(events):
            if not isinstance(event, dict):
                raise ValueError(
                    f"issue close activity event {row_index}:{event_index} must be an object"
                )
            event_id = str(event.get("event_id") or "").strip()
            event_type = str(event.get("event_type") or "").strip()
            occurred_at = _timestamp(
                event.get("occurred_at"),
                field=f"issue_close_activity.{issue_ref}.{event_index}.occurred_at",
            )
            if (
                not event_id
                or event_id in seen_event_ids
                or event_type not in known_types
            ):
                raise ValueError(
                    "issue close activity requires unique event_id values and known event types"
                )
            seen_event_ids.add(event_id)
            evidence_url = _optional_https_url(
                event.get("evidence_url"),
                field=f"issue_close_activity.{issue_ref}.{event_index}.evidence_url",
            )
            if event_type != "issue_close_recommended" and evidence_url is None:
                raise ValueError(f"{event_type} requires an https evidence_url")
            compact_event = {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
            }
            if evidence_url:
                compact_event["evidence_url"] = evidence_url
            compact_events.append(compact_event)
        activity.append(
            {
                "schema_version": "issue_fix_issue_close_activity_v0",
                "issue_ref": issue_ref,
                "events": sorted(
                    compact_events,
                    key=lambda item: (item["occurred_at"], item["event_id"]),
                ),
            }
        )
    if compact_coverage and compact_coverage["observed_issues"] != len(activity):
        raise ValueError(
            "supplement issue close activity coverage must match activity rows"
        )
    return sorted(activity, key=lambda item: item["issue_ref"]), compact_coverage


def _issue_close_output(
    activity: list[dict[str, Any]],
    *,
    current_issue_states: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    inventory: list[dict[str, Any]] = []
    for row in activity:
        issue_ref = row["issue_ref"]
        events = list(row["events"])
        recommendations = [
            event
            for event in events
            if event["event_type"] == "issue_close_recommended"
        ]
        requests = [
            event
            for event in events
            if event["event_type"] == "issue_close_request_published"
        ]
        close_events = [
            event for event in events if event["event_type"] == "issue_closed_observed"
        ]
        reopen_events = [
            event
            for event in events
            if event["event_type"] == "issue_reopened_observed"
        ]
        attempt_times = [
            datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
            for event in [*recommendations, *requests]
        ]
        request_times = [
            datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
            for event in requests
        ]
        close_candidates = [
            (
                datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")),
                event["occurred_at"],
                "event_evidence",
            )
            for event in close_events
        ]
        current_state = current_issue_states.get(issue_ref) or {}
        if current_state.get("state") == "CLOSED" and current_state.get("closed_at"):
            closed_at = _timestamp(
                current_state["closed_at"],
                field=f"current.issue_states.{issue_ref}.closed_at",
            )
            close_candidates.append(
                (
                    datetime.fromisoformat(closed_at.replace("Z", "+00:00")),
                    closed_at,
                    "repository_current_snapshot",
                )
            )
        first_attempt = min(attempt_times) if attempt_times else None
        eligible_closes = sorted(
            candidate
            for candidate in close_candidates
            if first_attempt is not None and candidate[0] >= first_attempt
        )
        actual_close = eligible_closes[0] if eligible_closes else None
        latest_close = eligible_closes[-1] if eligible_closes else None
        attributed = bool(
            actual_close
            and any(request_time <= actual_close[0] for request_time in request_times)
        )
        reopened = bool(
            attributed
            and any(
                datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
                > latest_close[0]
                for event in reopen_events
            )
        )
        inventory.append(
            {
                "schema_version": "issue_fix_issue_close_output_v0",
                "issue_ref": issue_ref,
                "close_recommended": bool(recommendations),
                "close_request_published": bool(requests),
                "actual_close_observed": actual_close is not None,
                "actual_close_at": actual_close[1] if actual_close else None,
                "actual_close_source": actual_close[2] if actual_close else None,
                "attributed_close_conversion": attributed,
                "reopened_after_attributed_close": reopened,
                "current_state": current_state.get("state") or "UNKNOWN",
                "evidence_urls": sorted(
                    {
                        event["evidence_url"]
                        for event in events
                        if event.get("evidence_url")
                    }
                ),
            }
        )
    counts = {
        "issue_close_recommendations": sum(
            item["close_recommended"] for item in inventory
        ),
        "issue_close_requests_published": sum(
            item["close_request_published"] for item in inventory
        ),
        "issue_closes_observed": sum(
            item["actual_close_observed"] for item in inventory
        ),
        "issue_close_conversions": sum(
            item["attributed_close_conversion"] for item in inventory
        ),
        "issue_close_reversals": sum(
            item["reopened_after_attributed_close"] for item in inventory
        ),
    }
    counts["net_issue_close_conversions"] = (
        counts["issue_close_conversions"] - counts["issue_close_reversals"]
    )
    return counts, inventory


def _latest_rows(
    rows: list[dict[str, Any]],
    *,
    repo: str,
    ref_field: str,
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        observation = row.get("observation") if isinstance(row, dict) else None
        if not isinstance(observation, dict):
            continue
        if str(observation.get("repo") or "").strip() != repo:
            continue
        reference = str(observation.get(ref_field) or "").strip()
        if reference:
            latest[reference] = row
    return [latest[key] for key in sorted(latest)]


def _row_time(row: dict[str, Any], *, lifecycle: bool) -> datetime | None:
    candidates: list[datetime] = []
    observation = row.get("observation")
    if lifecycle and isinstance(observation, dict):
        for field in ("merged_at", "closed_at", "updated_at"):
            if not observation.get(field):
                continue
            candidates.append(
                datetime.fromisoformat(
                    _timestamp(
                        observation[field],
                        field=f"domain_state.observation.{field}",
                    ).replace("Z", "+00:00")
                )
            )
    if row.get("generated_at"):
        candidates.append(
            datetime.fromisoformat(
                _timestamp(
                    row["generated_at"], field="domain_state.generated_at"
                ).replace("Z", "+00:00")
            )
        )
    return max(candidates) if candidates else None


def _lifecycle_attribution_time(
    row: dict[str, Any], current_pr_states: dict[str, dict[str, Any]]
) -> datetime | None:
    observation = row.get("observation")
    pr_ref = (
        str(observation.get("pr_ref") or "").strip()
        if isinstance(observation, dict)
        else ""
    )
    created_at = current_pr_states.get(pr_ref, {}).get("created_at")
    if created_at:
        return datetime.fromisoformat(
            _timestamp(
                created_at, field=f"current.pull_request_states.{pr_ref}.created_at"
            ).replace("Z", "+00:00")
        )
    return _row_time(row, lifecycle=True)


def _ratio(numerator: int | None, denominator: int | None) -> dict[str, Any]:
    if numerator is None or denominator is None or denominator == 0:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "value": None,
            "status": "not_available",
        }
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 4),
        "status": "available",
    }


def _impact_row(
    *,
    metric_id: str,
    metric_group: str,
    metric: str,
    baseline: int | float | None,
    current: int | float | None,
    delta: int | float | None,
    updated_at: str,
    source_url: str | None,
    numerator: int | None = None,
    denominator: int | None = None,
    status: str = "available",
    missing_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "issue_fix_impact_metric_row_v0",
        "metric_id": metric_id,
        "metric_group": metric_group,
        "metric": metric,
        "baseline": baseline,
        "current": current,
        "delta": delta,
        "numerator": numerator,
        "denominator": denominator,
        "status": status,
        "source_url": source_url,
        "updated_at": updated_at,
        "missing_reason": missing_reason,
    }


def build_issue_fix_metrics_projection(
    *,
    goal_id: str,
    repo: str,
    baseline_snapshot: dict[str, Any],
    current_snapshot: dict[str, Any],
    feasibility_rows: list[dict[str, Any]],
    pr_lifecycle_rows: list[dict[str, Any]],
    supplement_input: dict[str, Any] | None = None,
    generated_at: str,
) -> dict[str, Any]:
    goal_id = str(goal_id or "").strip()
    repo = str(repo or "").strip()
    if not goal_id or not repo:
        raise ValueError("goal_id and repo are required")
    baseline = _snapshot(baseline_snapshot, expected_repo=repo, role="baseline")
    current = _snapshot(current_snapshot, expected_repo=repo, role="current")
    baseline_time = datetime.fromisoformat(
        baseline["captured_at"].replace("Z", "+00:00")
    )
    current_time = datetime.fromisoformat(current["captured_at"].replace("Z", "+00:00"))
    if current_time < baseline_time:
        raise ValueError("current snapshot must not predate baseline snapshot")

    flow = current["flow_since_baseline"]
    expected_open_issues = (
        baseline["open_issues"] + flow["issues_opened"] - flow["issues_closed"]
    )
    expected_open_prs = (
        baseline["open_pull_requests"]
        + flow["pull_requests_opened"]
        - flow["pull_requests_closed"]
    )
    if expected_open_issues != current["open_issues"]:
        raise ValueError(
            "issue flow does not reconcile baseline and current open issues"
        )
    if expected_open_prs != current["open_pull_requests"]:
        raise ValueError(
            "pull request flow does not reconcile baseline and current open PRs"
        )
    if flow["pull_requests_merged"] > flow["pull_requests_closed"]:
        raise ValueError("merged pull requests cannot exceed closed pull requests")

    current_pr_states = {
        item["pr_ref"]: item for item in current.get("pull_request_states", [])
    }
    all_lifecycles = _latest_rows(pr_lifecycle_rows, repo=repo, ref_field="pr_ref")
    lifecycles = [
        row
        for row in all_lifecycles
        if (_lifecycle_attribution_time(row, current_pr_states) or baseline_time)
        >= baseline_time
    ]
    period_issue_refs = {
        str((row.get("observation") or {}).get("issue_ref") or "").strip()
        for row in lifecycles
    }
    period_issue_refs.discard("")
    all_feasibility = _latest_rows(feasibility_rows, repo=repo, ref_field="issue_ref")
    feasibility = [
        row
        for row in all_feasibility
        if str((row.get("observation") or {}).get("issue_ref") or "").strip()
        in period_issue_refs
        or (_row_time(row, lifecycle=False) or baseline_time) >= baseline_time
    ]

    route_counts = {"fix_pr": 0, "comment_only": 0, "triage_only": 0}
    selected_issue_refs: set[str] = set()
    validation_passed = 0
    validation_missing = 0
    for row in feasibility:
        observation = row.get("observation") or {}
        decision = row.get("decision") or {}
        issue_ref = str(observation.get("issue_ref") or "").strip()
        if issue_ref:
            selected_issue_refs.add(issue_ref)
        route = str(decision.get("route") or "").strip()
        if route in route_counts:
            route_counts[route] += 1
        delivery = row.get("delivery_evidence")
        validation = (
            str(delivery.get("validation_status") or "").strip().lower()
            if isinstance(delivery, dict)
            else ""
        )
        if validation == "passed":
            validation_passed += 1
        elif route == "fix_pr":
            validation_missing += 1

    pr_inventory: list[dict[str, Any]] = []
    pr_state_counts = {"OPEN": 0, "MERGED": 0, "CLOSED": 0}
    ci_counts = {"PASSING": 0, "FAILING": 0, "PENDING": 0, "UNKNOWN": 0}
    review_counts = {
        "APPROVED": 0,
        "CHANGES_REQUESTED": 0,
        "REVIEW_REQUIRED": 0,
        "UNKNOWN": 0,
    }
    receipt_rows = 0
    snapshot_refreshes = 0
    snapshot_corrections = 0
    linked_issue_refs: set[str] = set()
    for row in lifecycles:
        observation = row.get("observation") or {}
        pr_ref = str(observation.get("pr_ref") or "").strip()
        issue_ref = str(observation.get("issue_ref") or "").strip() or None
        ledger_state = str(observation.get("state") or "").strip().upper()
        checks = observation.get("checks") or {}
        ledger_ci = str(checks.get("aggregate") or "UNKNOWN").strip().upper()
        ledger_review = (
            str(observation.get("review_decision") or "UNKNOWN").strip().upper()
        )
        current_pr = current_pr_states.get(pr_ref)
        state = str((current_pr or {}).get("state") or ledger_state).upper()
        ci_state = str((current_pr or {}).get("ci") or ledger_ci).upper()
        review_state = str((current_pr or {}).get("review") or ledger_review).upper()
        if current_pr:
            snapshot_refreshes += 1
            if (state, ci_state, review_state) != (
                ledger_state,
                ledger_ci,
                ledger_review,
            ):
                snapshot_corrections += 1
        if state not in pr_state_counts:
            state = "OPEN"
        if ci_state not in ci_counts:
            ci_state = "UNKNOWN"
        if review_state not in review_counts:
            review_state = "UNKNOWN"
        pr_state_counts[state] += 1
        ci_counts[ci_state] += 1
        review_counts[review_state] += 1
        if issue_ref:
            linked_issue_refs.add(issue_ref)
        receipts = row.get("reviewer_notification_receipts")
        if isinstance(receipts, list) and receipts:
            receipt_rows += 1
        compact = {
            "pr_ref": pr_ref,
            "issue_ref": issue_ref,
            "url": observation.get("permalink"),
            "state": state,
            "ci": ci_state,
            "review": review_state,
            "current_state_source": (
                "repository_current_snapshot" if current_pr else "pr_lifecycle"
            ),
        }
        for field in ("created_at", "updated_at", "merged_at", "closed_at"):
            if observation.get(field):
                compact[field] = observation[field]
        pr_inventory.append(compact)

    current_issue_state_rows = {
        item["issue_ref"]: item for item in current.get("issue_states", [])
    }
    issue_states = {
        issue_ref: item["state"] for issue_ref, item in current_issue_state_rows.items()
    }
    missing_issue_states = sorted(linked_issue_refs - issue_states.keys())
    issues_closed = (
        sum(issue_states.get(ref) == "CLOSED" for ref in linked_issue_refs)
        if linked_issue_refs and not missing_issue_states
        else None
    )
    supplement = _supplement(supplement_input)
    first_push_coverage = _first_push_ci_coverage(supplement_input)
    issue_close_activity, issue_close_coverage = _issue_close_activity(supplement_input)
    issue_close_counts, issue_close_inventory = _issue_close_output(
        issue_close_activity,
        current_issue_states=current_issue_state_rows,
    )
    for field in (
        "issue_close_recommendations",
        "issue_close_requests_published",
    ):
        if (
            supplement[field] is not None
            and supplement[field] != issue_close_counts[field]
        ):
            raise ValueError(
                f"supplement.counts.{field} must match compact issue close activity"
            )
    close_activity_complete = bool(
        issue_close_coverage and issue_close_coverage["complete"] is True
    )
    close_outcome_missing_refs = sorted(
        item["issue_ref"]
        for item in issue_close_inventory
        if item["current_state"] == "UNKNOWN" and item["actual_close_observed"] is False
    )
    close_outcome_complete = close_activity_complete and not close_outcome_missing_refs
    reported_issue_close_counts = {
        field: (
            value
            if (
                close_activity_complete
                if field
                in {
                    "issue_close_recommendations",
                    "issue_close_requests_published",
                }
                else close_outcome_complete
            )
            else None
        )
        for field, value in issue_close_counts.items()
    }
    terminal_outcomes = (
        pr_state_counts["MERGED"]
        + (supplement["useful_public_comments"] or 0)
        + (supplement["triage_outcomes"] or 0)
    )

    agent_output = {
        "selected_issues": len(selected_issue_refs),
        "route_counts": route_counts,
        "pull_requests": len(pr_inventory),
        "open_pull_requests": pr_state_counts["OPEN"],
        "merged_pull_requests": pr_state_counts["MERGED"],
        "closed_unmerged_pull_requests": pr_state_counts["CLOSED"],
        "linked_issues_closed": issues_closed,
        "linked_issue_state_coverage": {
            "captured": len(linked_issue_refs) - len(missing_issue_states),
            "total": len(linked_issue_refs),
            "complete": not missing_issue_states,
        },
        "issue_close_output": reported_issue_close_counts,
        "issue_close_outcome_coverage": {
            "captured": len(issue_close_inventory) - len(close_outcome_missing_refs),
            "total": len(issue_close_inventory),
            "complete": close_outcome_complete,
        },
        "validation_passed": validation_passed,
        "validation_missing": validation_missing,
        "current_ci": ci_counts,
        "current_review": review_counts,
        "reviewer_notification_receipt_rows": receipt_rows,
        "pull_requests_refreshed_from_snapshot": snapshot_refreshes,
        "stale_lifecycle_rows_corrected_by_snapshot": snapshot_corrections,
        "terminal_outcomes": terminal_outcomes,
        "supplement": supplement,
    }
    zero_agent_output = {
        "selected_issues": 0,
        "route_counts": {key: 0 for key in route_counts},
        "pull_requests": 0,
        "open_pull_requests": 0,
        "merged_pull_requests": 0,
        "closed_unmerged_pull_requests": 0,
        "linked_issues_closed": 0,
        "linked_issue_state_coverage": {
            "captured": 0,
            "total": 0,
            "complete": True,
        },
        "issue_close_output": {field: 0 for field in issue_close_counts},
        "issue_close_outcome_coverage": {
            "captured": 0,
            "total": 0,
            "complete": True,
        },
        "validation_passed": 0,
        "reviewer_notification_receipt_rows": 0,
        "pull_requests_refreshed_from_snapshot": 0,
        "stale_lifecycle_rows_corrected_by_snapshot": 0,
        "terminal_outcomes": 0,
    }

    missing_data: list[dict[str, str]] = []
    if missing_issue_states:
        missing_data.append(
            {
                "code": "linked_issue_states_not_captured",
                "impact": (
                    "pilot-linked issue closure count and share are unavailable; "
                    f"{len(missing_issue_states)} linked issue states are missing"
                ),
            }
        )
    if validation_missing:
        missing_data.append(
            {
                "code": "fix_pr_validation_not_captured",
                "impact": "validation pass coverage is incomplete",
            }
        )
    if not close_activity_complete:
        missing_data.append(
            {
                "code": "issue_close_activity_not_captured",
                "impact": (
                    "close recommendations, published requests, conversions, and "
                    "reopen reversals are unavailable"
                ),
            }
        )
    elif close_outcome_missing_refs:
        missing_data.append(
            {
                "code": "issue_close_outcome_states_not_captured",
                "impact": (
                    "close conversions and reopen reversals are unavailable; "
                    f"{len(close_outcome_missing_refs)} attempted issue states are missing"
                ),
            }
        )
    for field, impact in (
        ("human_interventions", "autonomy rate is unavailable"),
        ("first_push_ci_total", "first-push CI rate is unavailable"),
        ("loopx_capability_gaps_found", "capability delta is unavailable"),
        ("memory_retrievals", "memory leverage is unavailable"),
    ):
        if supplement[field] is None:
            if field == "first_push_ci_total" and first_push_coverage is not None:
                impact = (
                    "first-push CI coverage is incomplete "
                    f"({first_push_coverage['observed_prs']}/"
                    f"{first_push_coverage['eligible_prs']} observed)"
                )
            missing_data.append({"code": f"{field}_not_captured", "impact": impact})

    ratios = {
        "pilot_merge_rate": _ratio(pr_state_counts["MERGED"], len(pr_inventory)),
        "pilot_share_of_repository_prs_opened": _ratio(
            len(pr_inventory), flow["pull_requests_opened"]
        ),
        "pilot_share_of_repository_prs_merged": _ratio(
            pr_state_counts["MERGED"], flow["pull_requests_merged"]
        ),
        "pilot_share_of_repository_issues_closed": _ratio(
            issues_closed, flow["issues_closed"]
        ),
        "first_push_ci_pass_rate": _ratio(
            supplement["first_push_ci_passed"], supplement["first_push_ci_total"]
        ),
        "human_interventions_per_terminal_outcome": _ratio(
            supplement["human_interventions"], terminal_outcomes
        ),
        "issue_close_conversion_rate": _ratio(
            reported_issue_close_counts["issue_close_conversions"],
            reported_issue_close_counts["issue_close_requests_published"],
        ),
    }
    source_url = current.get("source_url") or baseline.get("source_url")
    updated_at = current["captured_at"]
    impact_rows = [
        _impact_row(
            metric_id="repository_open_issues",
            metric_group="Repository",
            metric="Open issues",
            baseline=baseline["open_issues"],
            current=current["open_issues"],
            delta=current["open_issues"] - baseline["open_issues"],
            updated_at=updated_at,
            source_url=source_url,
        ),
        _impact_row(
            metric_id="repository_open_pull_requests",
            metric_group="Repository",
            metric="Open pull requests",
            baseline=baseline["open_pull_requests"],
            current=current["open_pull_requests"],
            delta=current["open_pull_requests"] - baseline["open_pull_requests"],
            updated_at=updated_at,
            source_url=source_url,
        ),
        _impact_row(
            metric_id="repository_issues_closed",
            metric_group="Repository",
            metric="Issues closed since baseline",
            baseline=0,
            current=flow["issues_closed"],
            delta=flow["issues_closed"],
            updated_at=updated_at,
            source_url=source_url,
        ),
        _impact_row(
            metric_id="repository_pull_requests_merged",
            metric_group="Repository",
            metric="Pull requests merged since baseline",
            baseline=0,
            current=flow["pull_requests_merged"],
            delta=flow["pull_requests_merged"],
            updated_at=updated_at,
            source_url=source_url,
        ),
        _impact_row(
            metric_id="agent_selected_issues",
            metric_group="Delivery",
            metric="Agent-selected issues",
            baseline=0,
            current=len(selected_issue_refs),
            delta=len(selected_issue_refs),
            updated_at=updated_at,
            source_url=source_url,
        ),
        _impact_row(
            metric_id="agent_pull_requests",
            metric_group="Delivery",
            metric="Agent pull requests",
            baseline=0,
            current=len(pr_inventory),
            delta=len(pr_inventory),
            updated_at=updated_at,
            source_url=source_url,
        ),
        _impact_row(
            metric_id="agent_merged_pull_requests",
            metric_group="Delivery",
            metric="Agent pull requests merged",
            baseline=0,
            current=pr_state_counts["MERGED"],
            delta=pr_state_counts["MERGED"],
            updated_at=updated_at,
            source_url=source_url,
        ),
        _impact_row(
            metric_id="agent_linked_issues_closed",
            metric_group="Delivery",
            metric="Agent-linked issues closed",
            baseline=0,
            current=issues_closed,
            delta=issues_closed,
            updated_at=updated_at,
            source_url=source_url,
            status="available" if issues_closed is not None else "not_available",
            missing_reason=(
                None
                if issues_closed is not None
                else "linked issue states are incomplete"
            ),
        ),
        _impact_row(
            metric_id="quality_validation_passed",
            metric_group="Quality",
            metric="Validated fixes",
            baseline=0,
            current=validation_passed,
            delta=validation_passed,
            updated_at=updated_at,
            source_url=source_url,
        ),
        _impact_row(
            metric_id="quality_current_ci_passing",
            metric_group="Quality",
            metric="Current PRs with passing CI",
            baseline=0,
            current=ci_counts["PASSING"],
            delta=ci_counts["PASSING"],
            updated_at=updated_at,
            source_url=source_url,
        ),
    ]
    for metric_id, metric, field in (
        (
            "agent_issue_close_recommendations",
            "Issue close recommendations",
            "issue_close_recommendations",
        ),
        (
            "agent_issue_close_requests_published",
            "Published issue close requests",
            "issue_close_requests_published",
        ),
        (
            "agent_issue_closes_observed",
            "Agent-pursued issues closed",
            "issue_closes_observed",
        ),
        (
            "agent_issue_close_conversions",
            "Attributed issue close conversions",
            "issue_close_conversions",
        ),
        (
            "agent_issue_close_reversals",
            "Attributed issue close reopen reversals",
            "issue_close_reversals",
        ),
        (
            "agent_net_issue_close_conversions",
            "Net attributed issue close conversions",
            "net_issue_close_conversions",
        ),
    ):
        value = reported_issue_close_counts[field]
        impact_rows.append(
            _impact_row(
                metric_id=metric_id,
                metric_group="Delivery",
                metric=metric,
                baseline=0,
                current=value,
                delta=value,
                status="available" if value is not None else "not_available",
                missing_reason=(
                    None
                    if value is not None
                    else "issue close activity or outcome state coverage is incomplete"
                ),
                updated_at=updated_at,
                source_url=source_url,
            )
        )
    for metric_id, metric_group, metric, ratio_key in (
        (
            "delivery_share_repository_prs_opened",
            "Delivery",
            "Share of repository PRs opened",
            "pilot_share_of_repository_prs_opened",
        ),
        (
            "delivery_share_repository_prs_merged",
            "Delivery",
            "Share of repository PRs merged",
            "pilot_share_of_repository_prs_merged",
        ),
        (
            "delivery_share_repository_issues_closed",
            "Delivery",
            "Share of repository issues closed",
            "pilot_share_of_repository_issues_closed",
        ),
        (
            "quality_first_push_ci_pass_rate",
            "Quality",
            "First-push CI pass rate",
            "first_push_ci_pass_rate",
        ),
        (
            "autonomy_human_interventions_per_terminal",
            "Autonomy",
            "Human interventions per terminal outcome",
            "human_interventions_per_terminal_outcome",
        ),
        (
            "delivery_issue_close_conversion_rate",
            "Delivery",
            "Attributed close conversions per published close request",
            "issue_close_conversion_rate",
        ),
    ):
        ratio = ratios[ratio_key]
        impact_rows.append(
            _impact_row(
                metric_id=metric_id,
                metric_group=metric_group,
                metric=metric,
                baseline=None,
                current=ratio["value"],
                delta=None,
                numerator=ratio["numerator"],
                denominator=ratio["denominator"],
                status=ratio["status"],
                missing_reason=(
                    None
                    if ratio["status"] == "available"
                    else (
                        "first-push CI coverage is incomplete "
                        f"({first_push_coverage['observed_prs']}/"
                        f"{first_push_coverage['eligible_prs']} observed)"
                        if ratio_key == "first_push_ci_pass_rate"
                        and first_push_coverage is not None
                        else "numerator or denominator is unavailable"
                    )
                ),
                updated_at=updated_at,
                source_url=source_url,
            )
        )
    for metric_id, metric_group, metric, supplement_field in (
        (
            "capability_gaps_found",
            "Capability",
            "LoopX capability gaps found",
            "loopx_capability_gaps_found",
        ),
        (
            "capability_gaps_fixed",
            "Capability",
            "LoopX capability gaps fixed",
            "loopx_capability_gaps_fixed",
        ),
        (
            "capability_gaps_real_callsite_verified",
            "Capability",
            "LoopX capability gaps real-callsite verified",
            "loopx_capability_gaps_real_callsite_verified",
        ),
        (
            "memory_retrievals",
            "Memory",
            "Repository-memory results retrieved",
            "memory_retrievals",
        ),
        (
            "memory_verified_decision_influence",
            "Memory",
            "Memory retrievals verified to influence an issue-fix decision",
            "memory_verified_decision_influence",
        ),
        (
            "memory_verified_patch_influence",
            "Memory",
            "Memory retrievals verified to influence a patch",
            "memory_verified_patch_influence",
        ),
        (
            "memory_stale_results",
            "Memory",
            "Repository-memory results verified stale",
            "memory_stale_results",
        ),
    ):
        value = supplement[supplement_field]
        impact_rows.append(
            _impact_row(
                metric_id=metric_id,
                metric_group=metric_group,
                metric=metric,
                baseline=0,
                current=value,
                delta=value,
                status="available" if value is not None else "not_available",
                missing_reason=(
                    None if value is not None else f"{supplement_field} is not captured"
                ),
                updated_at=updated_at,
                source_url=source_url,
            )
        )
    payload = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "mode": "issue-fix-metrics",
        "generated_at": _timestamp(generated_at, field="generated_at"),
        "goal_id": goal_id,
        "repo": repo,
        "baseline": {
            "repository": baseline,
            "agent_output": zero_agent_output,
        },
        "current": {
            "repository": current,
            "agent_output": agent_output,
        },
        "delta": {
            "repository": {
                "open_issues": current["open_issues"] - baseline["open_issues"],
                "open_pull_requests": (
                    current["open_pull_requests"] - baseline["open_pull_requests"]
                ),
                "flow": flow,
            },
            "agent_output": agent_output,
        },
        "output_inventory": {
            "pull_requests": pr_inventory,
            "issue_close_activity": issue_close_inventory,
        },
        "impact_rows": impact_rows,
        "ratios": ratios,
        "missing_data": missing_data,
        "supplement_coverage": {
            "first_push_ci": first_push_coverage,
            "issue_close_activity": issue_close_coverage,
        },
        "source_summary": {
            "feasibility_rows": len(feasibility),
            "pr_lifecycle_rows": len(lifecycles),
            "repository_snapshots": 2,
            "supplement_supplied": supplement_input is not None,
            "local_paths_recorded": False,
            "raw_provider_payloads_recorded": False,
        },
        "external_reads_performed": False,
        "external_writes_performed": False,
        "ok": True,
    }
    payload["validation"] = validate_issue_fix_metrics_projection(payload)
    payload["ok"] = payload["validation"]["ok"] is True
    return payload


def validate_issue_fix_metrics_projection(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != PROJECTION_SCHEMA_VERSION:
        errors.append("schema_version mismatch")
    baseline_output = (payload.get("baseline") or {}).get("agent_output") or {}
    if any(
        baseline_output.get(field) != 0
        for field in (
            "selected_issues",
            "pull_requests",
            "merged_pull_requests",
            "terminal_outcomes",
        )
    ):
        errors.append("baseline agent output must remain zero")
    if payload.get("external_writes_performed") is not False:
        errors.append("metrics projection must not perform external writes")
    return {
        "schema_version": "issue_fix_metrics_projection_validation_v0",
        "ok": not errors,
        "errors": errors,
    }


def render_issue_fix_metrics_projection_markdown(payload: dict[str, Any]) -> str:
    if payload.get("ok") is not True:
        return f"# Issue-fix metrics projection\n\n- Error: {payload.get('error') or 'invalid projection'}"
    current = payload["current"]
    output = current["agent_output"]
    close_output = output["issue_close_output"]
    repository = current["repository"]
    lines = [
        "# Issue-fix metrics projection",
        "",
        f"- Repository: `{payload['repo']}`",
        f"- Reporting start: `{payload['baseline']['repository']['captured_at']}`",
        f"- Selected issues: `{output['selected_issues']}`",
        f"- Pull requests: `{output['pull_requests']}` "
        f"(`{output['merged_pull_requests']}` merged, "
        f"`{output['open_pull_requests']}` open)",
        "- Issue close recommended / published / closed / attributed / reopened: "
        f"`{close_output['issue_close_recommendations']}` / "
        f"`{close_output['issue_close_requests_published']}` / "
        f"`{close_output['issue_closes_observed']}` / "
        f"`{close_output['issue_close_conversions']}` / "
        f"`{close_output['issue_close_reversals']}`",
        f"- Repository open issues / PRs: `{repository['open_issues']}` / "
        f"`{repository['open_pull_requests']}`",
        f"- Missing-data items: `{len(payload['missing_data'])}`",
        "",
        "## Output inventory",
        "",
    ]
    for item in payload["output_inventory"]["pull_requests"]:
        link = item.get("url") or item["pr_ref"]
        lines.append(
            f"- [{item['pr_ref']}]({link}) -> `{item.get('issue_ref') or 'unlinked'}`: "
            f"{item['state']} / CI {item['ci']} / review {item['review']}"
        )
    if not payload["output_inventory"]["pull_requests"]:
        lines.append("- None")
    close_inventory = payload["output_inventory"].get("issue_close_activity") or []
    if close_inventory:
        lines.extend(["", "## Issue close activity", ""])
        for item in close_inventory:
            lines.append(
                f"- `{item['issue_ref']}`: recommended="
                f"{item['close_recommended']}, published="
                f"{item['close_request_published']}, closed="
                f"{item['actual_close_observed']}, attributed="
                f"{item['attributed_close_conversion']}, reopened="
                f"{item['reopened_after_attributed_close']}"
            )
    return "\n".join(lines)
