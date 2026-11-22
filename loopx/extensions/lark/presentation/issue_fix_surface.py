"""Issue-fix fields and views for the generic Lark Kanban sink."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any


DEFAULT_ISSUE_FIX_GRID_VIEW = "Issue Fix Outcomes"
DEFAULT_ISSUE_FIX_KANBAN_VIEW = "Issue Fix Kanban"
DEFAULT_ISSUE_FIX_METRICS_VIEW = "Monthly Impact"

ISSUE_FIX_CARD_FIELDS = [
    "Task",
    "Repository",
    "Issue",
    "Pull Request",
    "Route",
    "Stage",
    "Validation",
    "Outcome",
    "Context Tags",
    "Status",
]

ISSUE_FIX_METRIC_FIELDS = [
    "Task",
    "Metric Group",
    "Metric",
    "Baseline",
    "Current",
    "Delta",
    "Numerator",
    "Denominator",
    "Metric Source",
    "Metric Updated At",
    "Missing Data",
]

ISSUE_FIX_STAGE_OPTIONS = [
    "reproduction_planned",
    "fix_in_progress",
    "fix_review_ready",
    "ci_pending",
    "ci_failed",
    "review_wait",
    "changes_requested",
    "merge_ready",
    "reproduction_blocked",
    "fix_blocked",
    "delivery_blocked",
    "draft_pr",
    "branch_stale_or_conflicted",
    "pr_open",
    "merged",
    "closed_without_merge",
    "comment_packet",
    "comment_blocked",
    "comment_published",
    "triage_complete",
]

ISSUE_FIX_CONTEXT_TAG_OPTIONS = list(
    dict.fromkeys(
        [
            "fix_pr",
            "comment_only",
            "triage_only",
            *ISSUE_FIX_STAGE_OPTIONS,
            "reproduction_confirmed",
            "reproduction_missing",
            "validation_passed",
            "validation_failed",
            "validation_partial",
            "validation_not_run",
            "validation_declared",
            "validation_unknown",
            "tests_changed",
            "multi_file",
            "repository_context_grounded",
        ]
    )
)


def issue_fix_field_definitions(
    select_options: Callable[[list[str]], list[dict[str, str]]],
) -> list[dict[str, Any]]:
    return [
        {
            "name": "Work Item Type",
            "type": "select",
            "multiple": False,
            "options": select_options(["Todo", "Issue Fix", "Issue Fix Metric"]),
        },
        {"name": "Repository", "type": "text", "style": {"type": "plain"}},
        {"name": "Issue", "type": "text", "style": {"type": "url"}},
        {"name": "Pull Request", "type": "text", "style": {"type": "url"}},
        {
            "name": "Route",
            "type": "select",
            "multiple": False,
            "options": select_options(["fix_pr", "comment_only", "triage_only"]),
        },
        {
            "name": "Stage",
            "type": "select",
            "multiple": False,
            "options": select_options(ISSUE_FIX_STAGE_OPTIONS),
        },
        {"name": "Validation", "type": "text", "style": {"type": "plain"}},
        {"name": "Outcome", "type": "text", "style": {"type": "plain"}},
        {
            "name": "Context Tags",
            "type": "select",
            "multiple": True,
            "options": select_options(ISSUE_FIX_CONTEXT_TAG_OPTIONS),
        },
        {
            "name": "Metric Group",
            "type": "select",
            "multiple": False,
            "options": select_options(
                [
                    "Repository",
                    "Delivery",
                    "Quality",
                    "Autonomy",
                    "Capability",
                    "Memory",
                ]
            ),
        },
        {"name": "Metric", "type": "text", "style": {"type": "plain"}},
        *[
            {
                "name": name,
                "type": "number",
                "style": {
                    "type": "plain",
                    "precision": 4,
                    "percentage": False,
                    "thousands_separator": False,
                },
            }
            for name in ("Baseline", "Current", "Delta", "Numerator", "Denominator")
        ],
        {"name": "Metric Source", "type": "text", "style": {"type": "url"}},
        {
            "name": "Metric Updated At",
            "type": "datetime",
            "style": {"format": "yyyy-MM-dd HH:mm"},
        },
        {"name": "Missing Data", "type": "text", "style": {"type": "plain"}},
    ]


def issue_fix_views() -> list[dict[str, str]]:
    return [
        {"name": DEFAULT_ISSUE_FIX_GRID_VIEW, "type": "grid"},
        {"name": DEFAULT_ISSUE_FIX_KANBAN_VIEW, "type": "kanban"},
        {"name": DEFAULT_ISSUE_FIX_METRICS_VIEW, "type": "grid"},
    ]


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _datetime_millis(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return int(parsed.timestamp() * 1000)


def issue_fix_record_values(
    block: Mapping[str, Any],
    *,
    compact_text: Callable[..., str],
) -> dict[str, Any]:
    action_kind = str(block.get("action_kind") or "")
    if action_kind == "issue_fix_metric":
        return {
            "Work Item Type": "Issue Fix Metric",
            "Repository": compact_text(block.get("repo"), limit=160),
            "Issue": "",
            "Pull Request": "",
            "Route": "",
            "Stage": "",
            "Validation": "",
            "Outcome": "",
            "Context Tags": [],
            "Metric Group": compact_text(block.get("metric_group"), limit=80),
            "Metric": compact_text(block.get("metric"), limit=180),
            "Baseline": _number_or_none(block.get("baseline")),
            "Current": _number_or_none(block.get("current")),
            "Delta": _number_or_none(block.get("delta")),
            "Numerator": _number_or_none(block.get("numerator")),
            "Denominator": _number_or_none(block.get("denominator")),
            "Metric Source": compact_text(block.get("source_url"), limit=320),
            "Metric Updated At": _datetime_millis(block.get("updated_at")),
            "Missing Data": compact_text(block.get("missing_reason"), limit=300),
        }
    if action_kind != "issue_fix_outcome":
        return {}
    issue = block.get("issue") if isinstance(block.get("issue"), Mapping) else {}
    pull_request = (
        block.get("pull_request")
        if isinstance(block.get("pull_request"), Mapping)
        else {}
    )
    validation = (
        block.get("validation") if isinstance(block.get("validation"), Mapping) else {}
    )
    result = block.get("result") if isinstance(block.get("result"), Mapping) else {}
    issue_number = issue.get("number")
    pr_number = pull_request.get("number")
    issue_url = compact_text(issue.get("url"), limit=320)
    pr_url = compact_text(pull_request.get("url"), limit=320)
    validation_status = compact_text(validation.get("status"), limit=80)
    validation_label = compact_text(validation.get("label"), limit=220)
    return {
        "Work Item Type": "Issue Fix",
        "Repository": compact_text(block.get("repo"), limit=160),
        "Issue": issue_url
        or (
            f"#{issue_number}"
            if issue_number is not None
            else compact_text(block.get("issue_ref"), limit=80)
        ),
        "Pull Request": pr_url
        or (
            f"#{pr_number}"
            if pr_number is not None
            else compact_text(pull_request.get("ref"), limit=80)
        ),
        "Route": compact_text(block.get("route"), limit=80),
        "Stage": compact_text(block.get("stage"), limit=80),
        "Validation": compact_text(
            ": ".join(part for part in (validation_status, validation_label) if part),
            limit=300,
        ),
        "Outcome": compact_text(result.get("kind"), limit=120),
        "Context Tags": [
            safe_tag
            for tag in block.get("context_tags") or []
            if (safe_tag := compact_text(tag, limit=80))
        ],
        "Metric Group": "",
        "Metric": "",
        "Baseline": None,
        "Current": None,
        "Delta": None,
        "Numerator": None,
        "Denominator": None,
        "Metric Source": "",
        "Metric Updated At": None,
        "Missing Data": "",
    }


def todo_record_values() -> dict[str, Any]:
    return {
        "Work Item Type": "Todo",
        "Repository": "",
        "Issue": "",
        "Pull Request": "",
        "Route": "",
        "Stage": "",
        "Validation": "",
        "Outcome": "",
        "Context Tags": [],
        "Metric Group": "",
        "Metric": "",
        "Baseline": None,
        "Current": None,
        "Delta": None,
        "Numerator": None,
        "Denominator": None,
        "Metric Source": "",
        "Metric Updated At": None,
        "Missing Data": "",
    }


def extract_field_names(parsed: Any) -> set[str]:
    if isinstance(parsed, dict):
        fields = parsed.get("fields")
        if isinstance(fields, list):
            return {
                str(item.get("name") or "").strip()
                for item in fields
                if isinstance(item, Mapping) and str(item.get("name") or "").strip()
            }
        for value in parsed.values():
            names = extract_field_names(value)
            if names:
                return names
    if isinstance(parsed, list):
        for value in parsed:
            names = extract_field_names(value)
            if names:
                return names
    return set()


def extract_fields(parsed: Any) -> dict[str, Mapping[str, Any]]:
    if isinstance(parsed, dict):
        fields = parsed.get("fields")
        if isinstance(fields, list):
            return {
                str(item.get("name") or "").strip(): item
                for item in fields
                if isinstance(item, Mapping) and str(item.get("name") or "").strip()
            }
        for value in parsed.values():
            extracted = extract_fields(value)
            if extracted:
                return extracted
    if isinstance(parsed, list):
        for value in parsed:
            extracted = extract_fields(value)
            if extracted:
                return extracted
    return {}


def missing_field_definitions(
    parsed: Any, definitions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    existing = extract_field_names(parsed)
    return [
        item
        for item in definitions
        if str(item.get("name") or "").strip() not in existing
    ]


def field_definition_migrations(
    parsed: Any, definitions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    existing = extract_fields(parsed)
    migrations: list[dict[str, Any]] = []
    for definition in definitions:
        name = str(definition.get("name") or "").strip()
        if (
            name
            not in {
                "Work Item Type",
                "Issue",
                "Pull Request",
                "Stage",
                "Context Tags",
            }
            or name not in existing
        ):
            continue
        current = existing[name]
        matches = current.get("type") == definition.get("type")
        if name in {"Issue", "Pull Request"}:
            current_style = (
                current.get("style")
                if isinstance(current.get("style"), Mapping)
                else {}
            )
            desired_style = (
                definition.get("style")
                if isinstance(definition.get("style"), Mapping)
                else {}
            )
            matches = matches and current_style.get("type") == desired_style.get("type")
        else:
            current_options = (
                current.get("options")
                if isinstance(current.get("options"), list)
                else []
            )
            desired_options = (
                definition.get("options")
                if isinstance(definition.get("options"), list)
                else []
            )
            matches = (
                matches
                and current.get("multiple") is definition.get("multiple")
                and [
                    item.get("name")
                    for item in current_options
                    if isinstance(item, Mapping)
                ]
                == [
                    item.get("name")
                    for item in desired_options
                    if isinstance(item, Mapping)
                ]
            )
        if not matches:
            migrations.append(
                {
                    "field_id": str(current.get("id") or name),
                    "name": name,
                    "definition": definition,
                }
            )
    return migrations


def issue_fix_view_configuration_commands(
    *,
    cli_bin: str,
    identity: str,
    base_token: str,
    table_id: str,
    view_ids: dict[str, str],
) -> list[list[str]]:
    issue_grid_view = (
        view_ids.get(DEFAULT_ISSUE_FIX_GRID_VIEW) or DEFAULT_ISSUE_FIX_GRID_VIEW
    )
    issue_kanban_view = (
        view_ids.get(DEFAULT_ISSUE_FIX_KANBAN_VIEW) or DEFAULT_ISSUE_FIX_KANBAN_VIEW
    )
    metrics_view = (
        view_ids.get(DEFAULT_ISSUE_FIX_METRICS_VIEW) or DEFAULT_ISSUE_FIX_METRICS_VIEW
    )
    common = [
        cli_bin,
        "base",
        "--as",
        identity,
        "--base-token",
        base_token,
        "--table-id",
        table_id,
    ]
    commands = [
        [
            *common[:2],
            "+view-set-filter",
            *common[2:],
            "--view-id",
            view_id,
            "--json",
            json.dumps(
                {
                    "logic": "and",
                    "conditions": [["Work Item Type", "intersects", ["Issue Fix"]]],
                },
                ensure_ascii=False,
            ),
        ]
        for view_id in (issue_grid_view, issue_kanban_view)
    ]
    commands.append(
        [
            *common[:2],
            "+view-set-filter",
            *common[2:],
            "--view-id",
            metrics_view,
            "--json",
            json.dumps(
                {
                    "logic": "and",
                    "conditions": [
                        ["Work Item Type", "intersects", ["Issue Fix Metric"]]
                    ],
                },
                ensure_ascii=False,
            ),
        ]
    )
    commands.append(
        [
            *common[:2],
            "+view-set-group",
            *common[2:],
            "--view-id",
            issue_kanban_view,
            "--json",
            json.dumps(
                {"group_config": [{"field": "Stage", "desc": False}]},
                ensure_ascii=False,
            ),
        ]
    )
    commands.extend(
        [
            *common[:2],
            "+view-set-visible-fields",
            *common[2:],
            "--view-id",
            view_id,
            "--json",
            json.dumps({"visible_fields": ISSUE_FIX_CARD_FIELDS}, ensure_ascii=False),
        ]
        for view_id in (issue_grid_view, issue_kanban_view)
    )
    commands.append(
        [
            *common[:2],
            "+view-set-visible-fields",
            *common[2:],
            "--view-id",
            metrics_view,
            "--json",
            json.dumps({"visible_fields": ISSUE_FIX_METRIC_FIELDS}, ensure_ascii=False),
        ]
    )
    return commands


def view_configuration_commands(
    *,
    cli_bin: str,
    identity: str,
    base_token: str,
    table_id: str,
    view_ids: dict[str, str],
    worker_view_name: str,
    todo_statuses: list[str],
    user_gate_status: str,
    operator_card_fields: list[str],
) -> list[list[str]]:
    worker_view = view_ids.get(worker_view_name) or worker_view_name
    user_gate_view = view_ids.get("User Gates") or "User Gates"
    kanban_view = view_ids.get("Kanban") or "Kanban"
    common = [
        "--as",
        identity,
        "--base-token",
        base_token,
        "--table-id",
        table_id,
    ]
    commands = [
        [
            cli_bin,
            "base",
            "+view-set-filter",
            *common,
            "--view-id",
            worker_view,
            "--json",
            json.dumps(
                {
                    "logic": "and",
                    "conditions": [["Status", "intersects", todo_statuses]],
                },
                ensure_ascii=False,
            ),
        ],
        [
            cli_bin,
            "base",
            "+view-set-filter",
            *common,
            "--view-id",
            user_gate_view,
            "--json",
            json.dumps(
                {
                    "logic": "and",
                    "conditions": [["Status", "intersects", [user_gate_status]]],
                },
                ensure_ascii=False,
            ),
        ],
        [
            cli_bin,
            "base",
            "+view-set-group",
            *common,
            "--view-id",
            kanban_view,
            "--json",
            json.dumps(
                {"group_config": [{"field": "Status", "desc": False}]},
                ensure_ascii=False,
            ),
        ],
        [
            cli_bin,
            "base",
            "+view-set-visible-fields",
            *common,
            "--view-id",
            kanban_view,
            "--json",
            json.dumps({"visible_fields": operator_card_fields}, ensure_ascii=False),
        ],
    ]
    commands.extend(
        issue_fix_view_configuration_commands(
            cli_bin=cli_bin,
            identity=identity,
            base_token=base_token,
            table_id=table_id,
            view_ids=view_ids,
        )
    )
    return commands
