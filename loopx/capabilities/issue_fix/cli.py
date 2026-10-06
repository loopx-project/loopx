from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...agent_registry import load_goal_from_registry
from ...boundary_authority import checkpointed_boundary_authority_summary
from ...control_plane.runtime.time import now_utc_iso
from ...domain_packs.issue_fix import (
    default_issue_fix_candidate_preflight_ledger_path,
    default_issue_fix_domain_state_ledger_path,
    default_issue_fix_feasibility_ledger_path,
    default_issue_fix_repository_snapshot_ledger_path,
    retain_issue_fix_repository_snapshot_jsonl,
    upsert_issue_fix_candidate_preflight_ledger_jsonl,
    upsert_issue_fix_feasibility_ledger_jsonl,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from ...todos import add_goal_todo
from .acceptance_loop import (
    build_issue_fix_acceptance_fixture_packet,
    build_issue_fix_caller_repo_branch_packet,
    build_issue_fix_repo_branch_fixture_packet,
    render_issue_fix_acceptance_loop_markdown,
)
from .candidate_evidence import collect_public_github_candidate_preflight_input
from .feasibility import (
    build_issue_fix_feasibility_packet,
    render_issue_fix_feasibility_markdown,
)
from .discovered_issue_promotion import (
    build_discovered_issue_promotion_from_cli_args,
    register_discovered_issue_promotion_command,
    render_issue_fix_discovered_issue_promotion_markdown,
)
from .cli_input import (
    _MAX_INLINE_JSON_CHARS as _MAX_INLINE_JSON_CHARS,
    load_json_object as _load_json_object,
    load_jsonl_row as _load_jsonl_row,
    load_jsonl_rows as _load_jsonl_rows,
)
from .outcome_projection import (
    build_issue_fix_outcome_projection,
    compact_issue_fix_delivery_evidence,
    render_issue_fix_outcome_projection_markdown,
)
from .metrics_projection import (
    build_issue_fix_metrics_projection,
    render_issue_fix_metrics_projection_markdown,
)
from .metrics_supplement import (
    render_issue_fix_metrics_supplement_markdown,
)
from .metrics_supplement_cli import (
    build_issue_fix_metrics_supplement_from_args,
    register_issue_fix_metrics_supplement_command,
)
from .metadata_preview import normalise_github_issue_reference
from .pr_lifecycle import (
    build_issue_fix_pr_lifecycle_monitor_packet,
    render_issue_fix_pr_lifecycle_monitor_markdown,
    validate_issue_fix_pr_lifecycle_monitor_packet,
)
from .pr_lifecycle_rollout import append_pr_merge_rollout_event
from .pr_monitor_materialization import (
    DEFAULT_ISSUE_FIX_MONITOR_CADENCE,
    materialize_issue_fix_grouped_monitors,
)
from .provider_hooks import IssueFixReviewerProviderHooksFactory
from . import pr_gate_reconcile_cli
from .reviewer_cli import (
    REVIEWER_COMMANDS,
    _materialize_goal_reviewer_notification_lifecycle as _materialize_goal_reviewer_notification_lifecycle,
    handle_issue_fix_reviewer_command,
    register_issue_fix_reviewer_commands,
    reviewer_renderer,
)
from .repository_memory import SUPPORT_ASPECTS
from .repository_memory_provider import (
    default_repository_memory_provider_config_path,
    render_issue_fix_repository_memory_sync_markdown,
    retrieve_issue_fix_repository_memory,
    sync_issue_fix_repository_memory,
    write_issue_fix_validated_outcome_memory,
)
from .repository_commit_evidence import verify_issue_fix_repository_commit_evidence
from .repository_snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    collect_public_github_repository_snapshot,
    render_repository_snapshot_markdown,
    repository_snapshot_record,
)
from .workflow_plan import (
    build_issue_fix_workflow_plan_packet,
    render_issue_fix_workflow_plan_markdown,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def _comparable_delivery_evidence(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    comparable = {key: item for key, item in value.items() if key != "recorded_at"}
    repository_evidence = comparable.get("repository_commit_evidence")
    if isinstance(repository_evidence, dict):
        comparable["repository_commit_evidence"] = {
            key: item
            for key, item in repository_evidence.items()
            if key != "verified_at"
        }
    return comparable


def _configured_repository_memory_input(
    *,
    provider_path: str | None,
    raw_memory_path: str | None,
    repo_path: str | None,
    repository_context_input: dict[str, Any] | None,
    repo: str,
    issue_ref: str,
    query: str | None,
    supports: list[str],
    validation_label: str | None,
    observed_at: str,
) -> dict[str, Any] | None:
    if raw_memory_path and provider_path:
        raise ValueError(
            "--repository-memory-json cannot be combined with a configured provider"
        )
    if raw_memory_path:
        return _load_json_object(raw_memory_path)
    if not provider_path:
        return None
    if not repo_path or not repository_context_input:
        return None
    revision = str(repository_context_input.get("repository_revision") or "").strip()
    if not revision:
        return None
    retrieval_query = query or " ".join(
        value
        for value in (
            repo,
            issue_ref,
            validation_label or "repository architecture reproduction validation",
        )
        if value
    )
    query_summary = (
        query
        or f"Public repository context for {repo} {issue_ref} before patch planning."
    )
    result = retrieve_issue_fix_repository_memory(
        config=_load_json_object(provider_path),
        repo_path=repo_path,
        repository_revision=revision,
        query=retrieval_query,
        query_summary=query_summary,
        supports=(
            supports or ["architecture", "change_scope", "reproduction", "validation"]
        ),
        observed_at=observed_at,
    )
    return result["memory_input"]


def _add_repository_memory_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repository-memory-provider-json",
        default=None,
        help=(
            "Optional local-private issue_fix_repository_memory_provider_config_v0. "
            "Defaults to LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG. "
            "The path, provider command, credentials, and raw payloads are never kept."
        ),
    )
    parser.add_argument(
        "--repository-memory-query",
        default=None,
        help=(
            "Optional compact public query for configured provider search/read. "
            "A bounded issue/ref/validation query is derived when omitted."
        ),
    )
    parser.add_argument(
        "--repository-memory-support",
        action="append",
        default=[],
        choices=sorted(SUPPORT_ASPECTS),
        help="Repository aspect supported by retrieved context. Repeat as needed.",
    )


def _add_generated_at_arg(
    parser: argparse.ArgumentParser,
    *,
    artifact: str,
) -> None:
    parser.add_argument(
        "--generated-at",
        default=None,
        help=(
            f"Public-safe generated_at timestamp for {artifact}; "
            "defaults to current UTC invocation time."
        ),
    )


def _goal_boundary_authority_projection(
    *,
    registry_path: Path | None,
    goal_id: str | None,
) -> tuple[list[str], bool]:
    if registry_path is None or not goal_id:
        return [], False
    goal = load_goal_from_registry(registry_path, goal_id)
    if not isinstance(goal, dict):
        return [], False
    coordination = (
        goal.get("coordination") if isinstance(goal.get("coordination"), dict) else {}
    )
    summary = checkpointed_boundary_authority_summary(coordination)
    scopes = (
        list(summary.get("active_write_scope") or [])
        if isinstance(summary, dict)
        else []
    )
    return [str(scope) for scope in scopes], True


def register_issue_fix_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    issue_fix_parser = subparsers.add_parser(
        "issue-fix",
        help="Run public-safe issue fix acceptance loops.",
    )
    issue_fix_sub = issue_fix_parser.add_subparsers(
        dest="issue_fix_command",
        required=True,
    )
    memory_sync_parser = issue_fix_sub.add_parser(
        "repository-memory-sync",
        help=(
            "Plan or explicitly execute a bounded public resource sync through "
            "the reusable context-provider module."
        ),
    )
    add_subcommand_format(memory_sync_parser)
    memory_sync_parser.add_argument("--repo-path", required=True)
    memory_sync_parser.add_argument("--repository-context-json", required=True)
    memory_sync_parser.add_argument(
        "--repository-memory-provider-json",
        default=None,
        help=(
            "Local-private provider config; defaults to "
            "LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG."
        ),
    )
    memory_sync_parser.add_argument(
        "--resource-reference",
        action="append",
        default=[],
        required=True,
        help="Repo-relative public file to index. Repeat up to the bounded cap.",
    )
    memory_sync_parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the provider resource write. Without this flag, return a plan.",
    )
    _add_generated_at_arg(memory_sync_parser, artifact="the resource sync")
    register_discovered_issue_promotion_command(
        issue_fix_sub,
        add_subcommand_format=add_subcommand_format,
        add_generated_at_arg=_add_generated_at_arg,
    )
    workflow_parser = issue_fix_sub.add_parser(
        "workflow-plan",
        help=(
            "Plan the full issue-fix workflow from public metadata to ordered "
            "LoopX todos, validation, and PR review packet readiness without writes."
        ),
    )
    add_subcommand_format(workflow_parser)
    workflow_parser.add_argument(
        "--repo",
        default="public_repo_fixture",
        help="Public-safe repository label for the issue metadata.",
    )
    workflow_parser.add_argument(
        "--issue-ref",
        default="issue_123_public_metadata_fixture",
        help="Public-safe issue or PR reference label.",
    )
    workflow_parser.add_argument(
        "--url",
        default=None,
        help="Optional https://github.com/owner/repo/issues/123 or /pull/123 URL.",
    )
    workflow_parser.add_argument(
        "--metadata-json",
        default=None,
        help=(
            "Path to mocked provider JSON metadata, or '-' for stdin. "
            "Body/comment fields stay gated and are not copied."
        ),
    )
    workflow_parser.add_argument(
        "--fetch-metadata",
        action="store_true",
        help=(
            "Explicitly fetch public GitHub issue/PR metadata with gh api --jq. "
            "Only body-free metadata fields are captured."
        ),
    )
    workflow_parser.add_argument(
        "--fetch-timeout-seconds",
        type=int,
        default=10,
        help="Timeout for --fetch-metadata.",
    )
    workflow_parser.add_argument(
        "--repo-path",
        default=None,
        help=(
            "Optional caller-approved local git repository path. Planning keeps "
            "this as a dry-run and never records the path."
        ),
    )
    workflow_parser.add_argument(
        "--base-branch",
        default="main",
        help="Approved local base branch for the eventual issue branch.",
    )
    workflow_parser.add_argument(
        "--issue-branch",
        default=None,
        help="Optional issue branch for the eventual caller-repo execution.",
    )
    workflow_parser.add_argument(
        "--validation-label",
        default="caller-declared validation",
        help="Public-safe validation label stored in the workflow plan.",
    )
    workflow_parser.add_argument(
        "--repository-context-json",
        default=None,
        help=(
            "Optional issue_fix_repository_context_input_v0 JSON path, or '-' for "
            "stdin. Only compact source refs, trust, freshness, and coverage are kept."
        ),
    )
    workflow_parser.add_argument(
        "--candidate-preflight-json",
        default=None,
        help=(
            "Optional issue_fix_candidate_preflight_input_v0 with compact prior "
            "domain state, all-state numeric PR references, verified semantic "
            "implementation candidates, and an existing recall receipt. The "
            "workflow performs no provider or memory calls."
        ),
    )
    workflow_parser.add_argument(
        "--fetch-candidate-evidence",
        action="store_true",
        help=(
            "Collect complete issue-specific GitHub closing-PR, cross-reference, "
            "and maintainer-disposition receipts before planning."
        ),
    )
    workflow_parser.add_argument(
        "--candidate-evidence-timeout-seconds",
        type=int,
        default=30,
        help="Timeout for --fetch-candidate-evidence.",
    )
    workflow_parser.add_argument(
        "--candidate-resolution-json",
        default=None,
        help=(
            "Optional issue_fix_candidate_resolution_v0 that resolves source-backed "
            "PR revisions, closed PRs, or maintainer comments. Requires "
            "--fetch-candidate-evidence so stale source bindings fail closed."
        ),
    )
    workflow_parser.add_argument(
        "--goal-id",
        default=None,
        help=(
            "Goal id used to persist the source-qualified candidate admission "
            "receipt before any feasibility or implementation decision."
        ),
    )
    workflow_parser.add_argument(
        "--project",
        default=".",
        help="Project root for the default issue_fix candidate preflight ledger.",
    )
    workflow_parser.add_argument(
        "--candidate-preflight-ledger-path",
        default=None,
        help="Optional candidate preflight JSONL path. Overrides the default path.",
    )
    workflow_parser.add_argument(
        "--no-write-domain-state",
        action="store_true",
        help=(
            "Keep candidate preflight preview-only even when --goal-id or an "
            "explicit ledger path is present."
        ),
    )
    workflow_parser.add_argument(
        "--repository-memory-json",
        default=None,
        help=(
            "Optional issue_fix_repository_memory_read_result_v0 JSON path, or '-' "
            "for stdin. The host must perform explicit public-namespace search/read; "
            "LoopX keeps only compact advisory refs and checkout verification."
        ),
    )
    _add_repository_memory_provider_args(workflow_parser)
    _add_generated_at_arg(workflow_parser, artifact="the workflow plan")
    feasibility_parser = issue_fix_sub.add_parser(
        "feasibility",
        help=(
            "Select exactly one fix_pr, comment_only, or triage_only route "
            "from compact public-safe agent observations."
        ),
    )
    add_subcommand_format(feasibility_parser)
    feasibility_parser.add_argument(
        "--repo",
        default="public_repo_fixture",
        help="Public-safe repository label for the issue.",
    )
    feasibility_parser.add_argument(
        "--issue-ref",
        default="issue_123_public_metadata_fixture",
        help="Public-safe issue reference label.",
    )
    feasibility_parser.add_argument(
        "--url",
        default=None,
        help="Optional https://github.com/owner/repo/issues/123 URL.",
    )
    feasibility_parser.add_argument(
        "--reproduction-status",
        required=True,
        choices=("confirmed", "planned", "missing", "blocked"),
        help="Compact agent observation of reproduction readiness.",
    )
    feasibility_parser.add_argument(
        "--scope-class",
        required=True,
        choices=("bounded", "uncertain", "oversized"),
        help="Compact agent observation of expected change scope.",
    )
    feasibility_parser.add_argument(
        "--reproduction-label",
        default=None,
        help="Compact public-safe repro or repro-plan label; never raw logs.",
    )
    feasibility_parser.add_argument(
        "--validation-label",
        default=None,
        help="Compact public-safe validation surface label.",
    )
    feasibility_parser.add_argument(
        "--comment-value",
        default="none",
        choices=("none", "clarification", "diagnosis"),
        help="Whether a maintainer-facing comment would add public value.",
    )
    feasibility_parser.add_argument(
        "--repository-context-json",
        default=None,
        help=(
            "Optional issue_fix_repository_context_input_v0 JSON path, or '-' for "
            "stdin. The compact projection is persisted with feasibility domain state."
        ),
    )
    feasibility_parser.add_argument(
        "--repository-memory-json",
        default=None,
        help=(
            "Optional issue_fix_repository_memory_read_result_v0 JSON path, or '-' "
            "for stdin. Provider failures stay fail-open and no writeback is allowed."
        ),
    )
    feasibility_parser.add_argument(
        "--repo-path",
        default=None,
        help=(
            "Optional caller-approved current checkout used only to verify provider "
            "content. The path is never retained."
        ),
    )
    _add_repository_memory_provider_args(feasibility_parser)
    _add_generated_at_arg(feasibility_parser, artifact="the feasibility decision")
    feasibility_parser.add_argument(
        "--goal-id",
        default=None,
        help="Goal id used for the default issue_fix feasibility ledger path.",
    )
    feasibility_parser.add_argument(
        "--project",
        default=".",
        help="Project root for the default issue_fix feasibility ledger path.",
    )
    feasibility_parser.add_argument(
        "--ledger-path",
        default=None,
        help="Optional local JSONL ledger path. Overrides the default path.",
    )
    feasibility_parser.add_argument(
        "--no-write-domain-state",
        action="store_true",
        help=(
            "Keep feasibility preview-only even when --goal-id or --ledger-path "
            "is present."
        ),
    )
    pr_lifecycle_parser = issue_fix_sub.add_parser(
        "pr-lifecycle",
        help=(
            "Project a public PR lifecycle observation into a successor, "
            "monitor-continuation, user-gate, or no-follow-up transition."
        ),
    )
    add_subcommand_format(pr_lifecycle_parser)
    pr_lifecycle_parser.add_argument(
        "--repo",
        default="public_repo_fixture",
        help="Public-safe repository label for the PR metadata.",
    )
    pr_lifecycle_parser.add_argument(
        "--pr-ref",
        default="pull_123_public_metadata_fixture",
        help="Public-safe PR reference label.",
    )
    pr_lifecycle_parser.add_argument(
        "--issue-ref",
        default=None,
        help=(
            "Optional public-safe issue reference linked to this PR. Persisting "
            "the explicit link lets default Kanban sync compose the issue outcome "
            "without guessing from titles or branch names. Numeric aliases such "
            "as #123 and issue_123 are stored as issues_123."
        ),
    )
    pr_lifecycle_parser.add_argument(
        "--url",
        default=None,
        help="Optional https://github.com/owner/repo/pull/123 URL.",
    )
    pr_lifecycle_parser.add_argument(
        "--metadata-json",
        default=None,
        help=(
            "Path to mocked gh pr view JSON metadata, or '-' for stdin. "
            "Bodies, comments, raw logs, and provider responses stay out."
        ),
    )
    pr_lifecycle_parser.add_argument(
        "--maintainer-correction-json",
        default=None,
        help=(
            "Optional issue_fix_maintainer_correction_input_v0 JSON object. "
            "It contains only a compact public correction, source reference, "
            "verification plan/update path, ambiguity question, or missing scopes."
        ),
    )
    pr_lifecycle_parser.add_argument(
        "--fetch-metadata",
        action="store_true",
        help=(
            "Explicitly fetch public GitHub PR state with gh pr view. "
            "Only compact lifecycle fields are captured."
        ),
    )
    pr_lifecycle_parser.add_argument(
        "--fetch-timeout-seconds",
        type=int,
        default=10,
        help="Timeout for --fetch-metadata.",
    )
    _add_generated_at_arg(pr_lifecycle_parser, artifact="the lifecycle projection")
    pr_lifecycle_parser.add_argument(
        "--goal-id",
        default=None,
        help="Goal id used for the default issue_fix domain-state ledger path.",
    )
    pr_lifecycle_parser.add_argument(
        "--project",
        default=".",
        help="Project root for the default issue_fix domain-state ledger path.",
    )
    pr_lifecycle_parser.add_argument(
        "--ledger-path",
        default=None,
        help="Optional local JSONL ledger path. Overrides the default domain-state path.",
    )
    pr_lifecycle_parser.add_argument(
        "--no-write-domain-state",
        action="store_true",
        help=(
            "Keep the PR lifecycle command preview-only even when --goal-id or "
            "--ledger-path is present."
        ),
    )
    pr_lifecycle_parser.add_argument(
        "--execute-transition",
        action="store_true",
        help=(
            "Write the lifecycle transition and reconcile grouped monitors in "
            "the existing LoopX todo state. Requires --goal-id, --claimed-by, "
            "and a registry."
        ),
    )
    pr_lifecycle_parser.add_argument(
        "--claimed-by",
        default=None,
        help=(
            "Registered agent that claims an actionable patch successor or is blocked "
            "by the generated concrete user gate."
        ),
    )
    pr_lifecycle_parser.add_argument(
        "--monitor-cadence",
        default=DEFAULT_ISSUE_FIX_MONITOR_CADENCE,
        help=(
            "Cadence for grouped PR lifecycle continuous monitors materialized "
            "by --execute-transition (default: 30m)."
        ),
    )
    pr_gate_reconcile_cli.register_pr_gate_reconciliation_command(issue_fix_sub)
    outcome_parser = issue_fix_sub.add_parser(
        "outcome",
        help=(
            "Compose one public-safe issue-fix status/output projection from "
            "existing feasibility and optional PR lifecycle state."
        ),
    )
    add_subcommand_format(outcome_parser)
    outcome_parser.add_argument("--goal-id", required=True)
    outcome_parser.add_argument("--project", default=".")
    outcome_parser.add_argument("--repo", required=True)
    outcome_parser.add_argument("--issue-ref", required=True)
    outcome_parser.add_argument(
        "--pr-ref",
        help="Optional PR ref linked to this issue, such as pull_456.",
    )
    outcome_parser.add_argument(
        "--feasibility-json",
        help="Optional issue_fix_feasibility_v0 JSON object; defaults to domain state.",
    )
    outcome_parser.add_argument(
        "--pr-lifecycle-json",
        help=(
            "Optional issue_fix_pr_lifecycle_monitor_v0 JSON object; defaults "
            "to domain state when --pr-ref is present."
        ),
    )
    outcome_parser.add_argument(
        "--delivery-evidence-json",
        help=(
            "Optional issue_fix_delivery_evidence_input_v0 JSON with explicit "
            "validation status, repo-relative changed files, commit ref, outputs, and risks."
        ),
    )
    outcome_parser.add_argument(
        "--write-delivery-evidence",
        action="store_true",
        help=(
            "Validate and write compact delivery evidence into the selected "
            "existing feasibility row so default outcome and Kanban sync retain it."
        ),
    )
    outcome_parser.add_argument(
        "--repository-memory-provider-json",
        default=None,
        help=(
            "Local-private issue_fix_repository_memory_provider_config_v0 used "
            "only when --write-repository-memory is explicit. Defaults to "
            "LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG."
        ),
    )
    outcome_parser.add_argument(
        "--write-repository-memory",
        action="store_true",
        help=(
            "After passed validation and completed delivery, write one distilled "
            "public outcome through an owner-enabled, revision-scoped provider. "
            "No transcript or tool-result capture is performed."
        ),
    )
    outcome_parser.add_argument(
        "--repo-path",
        default=None,
        help=(
            "Caller-approved git checkout used to prove delivery commit identity "
            "before evidence writeback or repository-memory publication. "
            "The path and raw git output are never recorded."
        ),
    )
    outcome_parser.add_argument(
        "--repository-ref",
        default=None,
        help=(
            "Full recoverable git ref (refs/heads, refs/remotes, or refs/tags) that "
            "must resolve exactly to the pinned repository revision when recording "
            "passed or completed commit evidence."
        ),
    )
    outcome_parser.add_argument(
        "--agent-id",
        help="Optional registered agent id projected as the case owner.",
    )
    _add_generated_at_arg(outcome_parser, artifact="the read model")
    metrics_parser = issue_fix_sub.add_parser(
        "metrics",
        help=(
            "Compose a read-only baseline, attributable output inventory, repository "
            "delta, and missing-data projection from existing issue-fix domain state."
        ),
    )
    add_subcommand_format(metrics_parser)
    metrics_parser.add_argument("--goal-id", required=True)
    metrics_parser.add_argument("--project", default=".")
    metrics_parser.add_argument("--repo", required=True)
    metrics_parser.add_argument(
        "--repository-baseline-json",
        required=True,
        help=f"Public-safe {SNAPSHOT_SCHEMA_VERSION} at period start.",
    )
    metrics_parser.add_argument(
        "--repository-current-json",
        required=True,
        help=(
            f"Public-safe {SNAPSHOT_SCHEMA_VERSION} at current time, "
            "including flow_since_baseline."
        ),
    )
    metrics_parser.add_argument(
        "--supplement-json",
        default=None,
        help=(
            "Optional public-safe issue_fix_metrics_supplement_v0 for autonomy, "
            "first-push CI, memory, and capability-delta counts."
        ),
    )
    metrics_parser.add_argument(
        "--feasibility-ledger",
        default=None,
        help="Optional feasibility JSONL override; defaults to goal domain state.",
    )
    metrics_parser.add_argument(
        "--pr-lifecycle-ledger",
        default=None,
        help="Optional PR lifecycle JSONL override; defaults to goal domain state.",
    )
    _add_generated_at_arg(metrics_parser, artifact="the metrics projection")
    register_issue_fix_metrics_supplement_command(
        issue_fix_sub,
        add_subcommand_format=add_subcommand_format,
        add_generated_at_arg=_add_generated_at_arg,
    )
    snapshot_parser = issue_fix_sub.add_parser(
        "repository-snapshot",
        help=(
            "Collect a compact public GitHub repository snapshot for issue-fix "
            "metrics and optionally retain one material snapshot per day."
        ),
    )
    add_subcommand_format(snapshot_parser)
    snapshot_parser.add_argument("--goal-id", required=True)
    snapshot_parser.add_argument("--project", default=".")
    snapshot_parser.add_argument("--repo", required=True)
    snapshot_parser.add_argument("--repository-baseline-json", required=True)
    snapshot_parser.add_argument(
        "--supplement-json",
        default=None,
        help=(
            "Optional public-safe issue_fix_metrics_supplement_v0 whose "
            "issue-close activity refs must be included in current-state collection."
        ),
    )
    snapshot_parser.add_argument(
        "--fetch-public-github",
        action="store_true",
        help="Explicitly read bounded public repository metadata through gh.",
    )
    snapshot_parser.add_argument(
        "--retain-material-snapshot",
        action="store_true",
        help=(
            "Write only a material daily change into the existing issue_fix "
            "domain-state stream."
        ),
    )
    snapshot_parser.add_argument("--fetch-timeout-seconds", type=int, default=30)
    _add_generated_at_arg(snapshot_parser, artifact="the repository snapshot")
    register_issue_fix_reviewer_commands(
        issue_fix_sub,
        add_subcommand_format=add_subcommand_format,
        add_generated_at_arg=_add_generated_at_arg,
    )
    acceptance_parser = issue_fix_sub.add_parser(
        "acceptance-fixture",
        help=(
            "Run a deterministic fix loop: failing repro, minimal patch, "
            "focused validation, and PR-review-ready artifact."
        ),
    )
    add_subcommand_format(acceptance_parser)
    acceptance_parser.add_argument(
        "--repo",
        default="public_repo_fixture",
        help="Public-safe repository label for the issue metadata fixture.",
    )
    acceptance_parser.add_argument(
        "--issue-ref",
        default="issue_123_public_metadata_fixture",
        help="Public-safe issue or PR reference label for the fixture.",
    )
    acceptance_parser.add_argument(
        "--url",
        default=None,
        help="Optional public GitHub issue or PR URL for metadata parsing.",
    )
    _add_generated_at_arg(acceptance_parser, artifact="the fixture artifact")
    branch_parser = issue_fix_sub.add_parser(
        "repo-branch-fixture",
        help=(
            "Run the fix loop through a temporary git repo issue branch: "
            "branch, repro, patch, validation, and PR evidence."
        ),
    )
    add_subcommand_format(branch_parser)
    branch_parser.add_argument(
        "--repo",
        default="public_repo_fixture",
        help="Public-safe repository label for the issue metadata fixture.",
    )
    branch_parser.add_argument(
        "--issue-ref",
        default="issue_123_public_metadata_fixture",
        help="Public-safe issue or PR reference label for the fixture.",
    )
    branch_parser.add_argument(
        "--url",
        default=None,
        help="Optional public GitHub issue or PR URL for metadata parsing.",
    )
    _add_generated_at_arg(branch_parser, artifact="the fixture artifact")
    caller_branch_parser = issue_fix_sub.add_parser(
        "caller-repo-branch",
        help=(
            "Prepare or execute an explicitly approved local repo issue branch "
            "workflow without external comments, PR creation, or merge."
        ),
    )
    add_subcommand_format(caller_branch_parser)
    caller_branch_parser.add_argument(
        "--repo-path",
        required=True,
        help=(
            "Caller-approved local git repository path. The public artifact records "
            "only a repo label, never this path."
        ),
    )
    caller_branch_parser.add_argument(
        "--repo",
        default="approved_local_repo",
        help="Public-safe repository label when no URL-derived repo is provided.",
    )
    caller_branch_parser.add_argument(
        "--issue-ref",
        default="issue_123_public_metadata",
        help="Public-safe issue or PR reference label.",
    )
    caller_branch_parser.add_argument(
        "--url",
        default=None,
        help="Optional public GitHub issue or PR URL used only for compact metadata.",
    )
    caller_branch_parser.add_argument(
        "--base-branch",
        default="main",
        help="Approved local base branch used when creating a new issue branch.",
    )
    caller_branch_parser.add_argument(
        "--issue-branch",
        default=None,
        help="Optional issue branch to create or claim. Defaults to codex/<issue>-fix.",
    )
    caller_branch_parser.add_argument(
        "--validation-command",
        default=None,
        help="Caller-declared validation command. Required with --execute.",
    )
    caller_branch_parser.add_argument(
        "--validation-label",
        default="caller-declared validation",
        help="Public-safe validation label stored in the artifact instead of raw command text.",
    )
    caller_branch_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=60,
        help="Validation timeout in seconds.",
    )
    caller_branch_parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually inspect the approved repo, create or claim the issue branch, "
            "and run the caller-declared validation."
        ),
    )
    _add_generated_at_arg(caller_branch_parser, artifact="the artifact")


def handle_issue_fix_command(
    args: argparse.Namespace,
    *,
    registry_path: Path | None = None,
    runtime_root_arg: str | None = None,
    reviewer_provider_hooks_factory: (
        IssueFixReviewerProviderHooksFactory | None
    ) = None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int:
    try:
        invocation_at = now_utc_iso()
        generated_at = str(args.generated_at or invocation_at).strip()
        reviewer_result = handle_issue_fix_reviewer_command(
            args,
            registry_path=registry_path,
            generated_at=generated_at,
            delivery_observed_at=invocation_at,
            reviewer_provider_hooks_factory=reviewer_provider_hooks_factory,
        )
        if reviewer_result is not None:
            payload, renderer = reviewer_result
        elif args.issue_fix_command == "repository-memory-sync":
            provider_path = (
                args.repository_memory_provider_json
                or default_repository_memory_provider_config_path()
            )
            if not provider_path:
                raise ValueError("repository memory provider config is required")
            if provider_path == "-" and args.repository_context_json == "-":
                raise ValueError("only one JSON input may read from stdin")
            repository_context_input = _load_json_object(args.repository_context_json)
            revision = str(
                repository_context_input.get("repository_revision") or ""
            ).strip()
            if not revision:
                raise ValueError("repository context must pin repository_revision")
            payload = sync_issue_fix_repository_memory(
                config=_load_json_object(provider_path),
                repo_path=args.repo_path,
                repository_revision=revision,
                references=args.resource_reference,
                observed_at=generated_at,
                execute=args.execute,
            )
            renderer = render_issue_fix_repository_memory_sync_markdown
        elif args.issue_fix_command == "promote-discovered-issue":
            boundary_authority_scopes, boundary_authority_resolved = (
                _goal_boundary_authority_projection(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                )
            )
            payload = build_discovered_issue_promotion_from_cli_args(
                args,
                load_json_object=_load_json_object,
                boundary_authority_scopes=boundary_authority_scopes,
                boundary_authority_resolved=boundary_authority_resolved,
                generated_at=generated_at,
            )
            renderer = render_issue_fix_discovered_issue_promotion_markdown
        elif args.issue_fix_command == "workflow-plan":
            if args.fetch_metadata and args.metadata_json:
                raise ValueError(
                    "--fetch-metadata cannot be combined with --metadata-json"
                )
            if args.fetch_candidate_evidence and args.candidate_preflight_json:
                raise ValueError(
                    "--fetch-candidate-evidence cannot be combined with "
                    "--candidate-preflight-json"
                )
            if args.candidate_resolution_json and not args.fetch_candidate_evidence:
                raise ValueError(
                    "--candidate-resolution-json requires --fetch-candidate-evidence"
                )
            provider_path = args.repository_memory_provider_json or (
                None
                if args.repository_memory_json
                else default_repository_memory_provider_config_path()
            )
            stdin_inputs = [
                value
                for value in (
                    args.metadata_json,
                    args.repository_context_json,
                    args.repository_memory_json,
                    args.candidate_preflight_json,
                    args.candidate_resolution_json,
                    provider_path,
                )
                if value == "-"
            ]
            if len(stdin_inputs) > 1:
                raise ValueError("only one JSON input may read from stdin")
            repository_context_input = (
                _load_json_object(args.repository_context_json)
                if args.repository_context_json
                else None
            )
            if args.fetch_candidate_evidence:
                reference = normalise_github_issue_reference(
                    repo=args.repo,
                    issue_ref=args.issue_ref,
                    url=args.url,
                )
                if reference["kind"] != "issue":
                    raise ValueError(
                        "--fetch-candidate-evidence requires a GitHub issue"
                    )
                candidate_preflight_input = (
                    collect_public_github_candidate_preflight_input(
                        repo=str(reference["repo"]),
                        issue_ref=str(reference["issue_ref"]),
                        generated_at=generated_at,
                        timeout_seconds=args.candidate_evidence_timeout_seconds,
                    )
                )
                if args.candidate_resolution_json:
                    candidate_preflight_input["candidate_resolution"] = (
                        _load_json_object(args.candidate_resolution_json)
                    )
            else:
                candidate_preflight_input = (
                    _load_json_object(args.candidate_preflight_json)
                    if args.candidate_preflight_json
                    else None
                )
            repository_memory_input = _configured_repository_memory_input(
                provider_path=provider_path,
                raw_memory_path=args.repository_memory_json,
                repo_path=args.repo_path,
                repository_context_input=repository_context_input,
                repo=args.repo,
                issue_ref=args.issue_ref,
                query=args.repository_memory_query,
                supports=args.repository_memory_support,
                validation_label=args.validation_label,
                observed_at=generated_at,
            )
            payload = build_issue_fix_workflow_plan_packet(
                repo=args.repo,
                issue_ref=args.issue_ref,
                url=args.url,
                provider_payload=_load_json_object(args.metadata_json)
                if args.metadata_json
                else None,
                fetch_metadata=args.fetch_metadata,
                fetch_timeout_seconds=args.fetch_timeout_seconds,
                repo_path=args.repo_path,
                base_branch=args.base_branch,
                issue_branch=args.issue_branch,
                validation_label=args.validation_label,
                repository_context_input=repository_context_input,
                repository_memory_input=repository_memory_input,
                candidate_preflight_input=candidate_preflight_input,
                generated_at=generated_at,
            )
            candidate_preflight = payload.get("candidate_preflight")
            should_write_candidate_preflight = bool(
                not args.no_write_domain_state
                and (args.goal_id or args.candidate_preflight_ledger_path)
            )
            if should_write_candidate_preflight:
                if not isinstance(candidate_preflight, dict):
                    raise ValueError(
                        "workflow plan omitted the candidate preflight receipt"
                    )
                candidate_preflight_ledger_path = (
                    Path(args.candidate_preflight_ledger_path).expanduser()
                    if args.candidate_preflight_ledger_path
                    else default_issue_fix_candidate_preflight_ledger_path(
                        project=args.project,
                        goal_id=args.goal_id,
                    )
                )
                upsert_issue_fix_candidate_preflight_ledger_jsonl(
                    candidate_preflight_ledger_path,
                    candidate_preflight,
                )
            elif isinstance(candidate_preflight, dict):
                domain_state = candidate_preflight.get("domain_state_projection")
                if isinstance(domain_state, dict):
                    domain_state["write_skipped_reason"] = (
                        "explicitly_disabled"
                        if args.no_write_domain_state
                        else "goal_id_or_ledger_path_missing"
                    )
            renderer = render_issue_fix_workflow_plan_markdown
        elif args.issue_fix_command == "feasibility":
            provider_path = args.repository_memory_provider_json or (
                None
                if args.repository_memory_json
                else default_repository_memory_provider_config_path()
            )
            if (
                args.repository_context_json == "-"
                and args.repository_memory_json == "-"
            ):
                raise ValueError("only one JSON input may read from stdin")
            if provider_path == "-" and (
                args.repository_context_json == "-"
                or args.repository_memory_json == "-"
            ):
                raise ValueError("only one JSON input may read from stdin")
            repository_context_input = (
                _load_json_object(args.repository_context_json)
                if args.repository_context_json
                else None
            )
            repository_memory_input = _configured_repository_memory_input(
                provider_path=provider_path,
                raw_memory_path=args.repository_memory_json,
                repo_path=args.repo_path,
                repository_context_input=repository_context_input,
                repo=args.repo,
                issue_ref=args.issue_ref,
                query=args.repository_memory_query,
                supports=args.repository_memory_support,
                validation_label=args.validation_label,
                observed_at=generated_at,
            )
            boundary_authority_scopes, boundary_authority_resolved = (
                _goal_boundary_authority_projection(
                    registry_path=registry_path,
                    goal_id=args.goal_id,
                )
            )
            payload = build_issue_fix_feasibility_packet(
                repo=args.repo,
                issue_ref=args.issue_ref,
                url=args.url,
                reproduction_status=args.reproduction_status,
                scope_class=args.scope_class,
                reproduction_label=args.reproduction_label,
                validation_label=args.validation_label,
                comment_value=args.comment_value,
                repository_context_input=repository_context_input,
                repository_memory_input=repository_memory_input,
                boundary_authority_scopes=boundary_authority_scopes,
                boundary_authority_resolved=boundary_authority_resolved,
                generated_at=generated_at,
            )
            should_write_domain_state = bool(
                not args.no_write_domain_state and (args.goal_id or args.ledger_path)
            )
            if should_write_domain_state:
                ledger_path = (
                    Path(args.ledger_path).expanduser()
                    if args.ledger_path
                    else default_issue_fix_feasibility_ledger_path(
                        project=args.project,
                        goal_id=args.goal_id,
                    )
                )
                upsert_issue_fix_feasibility_ledger_jsonl(
                    ledger_path,
                    payload,
                )
            else:
                domain_state = payload.get("domain_state_projection")
                if isinstance(domain_state, dict) and not args.no_write_domain_state:
                    domain_state["write_skipped_reason"] = (
                        "goal_id_or_ledger_path_missing"
                    )
            renderer = render_issue_fix_feasibility_markdown
        elif args.issue_fix_command in {
            "pr-gate-reconcile",
            "pr-review-ack",
            "pr-review-reconcile",
            "pr-review-reconcile-acked",
        }:
            if args.issue_fix_command == "pr-gate-reconcile":
                payload = (
                    pr_gate_reconcile_cli.build_pr_gate_reconciliation_from_args(
                        args, registry_path, runtime_root_arg, generated_at
                    )
                )
                renderer = (
                    pr_gate_reconcile_cli.render_issue_fix_pr_gate_reconciliation_markdown
                )
            elif args.issue_fix_command == "pr-review-ack":
                payload = pr_gate_reconcile_cli.build_pr_review_ack_from_args(
                    args, registry_path, runtime_root_arg, generated_at
                )
                renderer = (
                    pr_gate_reconcile_cli.render_issue_fix_pr_review_ack_markdown
                )
            elif args.issue_fix_command == "pr-review-reconcile-acked":
                payload = (
                    pr_gate_reconcile_cli.build_acked_pr_review_reconciliation_from_args(
                        args, registry_path, runtime_root_arg, generated_at
                    )
                )
                renderer = (
                    pr_gate_reconcile_cli.render_issue_fix_acked_pr_review_reconciliation_markdown
                )
            else:
                payload = (
                    pr_gate_reconcile_cli.build_pr_review_reconciliation_from_args(
                        args, registry_path, runtime_root_arg, generated_at
                    )
                )
                renderer = (
                    pr_gate_reconcile_cli.render_issue_fix_pr_review_reconciliation_markdown
                )
        elif args.issue_fix_command == "pr-lifecycle":
            if args.fetch_metadata and args.metadata_json:
                raise ValueError(
                    "--fetch-metadata cannot be combined with --metadata-json"
                )
            fetch_metadata = bool(
                args.fetch_metadata
                or (args.execute_transition and args.url and not args.metadata_json)
            )
            if args.execute_transition and args.no_write_domain_state:
                raise ValueError(
                    "--execute-transition cannot be combined with --no-write-domain-state"
                )
            if args.execute_transition and registry_path is None:
                raise ValueError("--execute-transition requires a LoopX registry")
            if args.execute_transition and not args.goal_id:
                raise ValueError("--execute-transition requires --goal-id")
            if args.execute_transition and not args.claimed_by:
                raise ValueError("--execute-transition requires --claimed-by")
            payload = build_issue_fix_pr_lifecycle_monitor_packet(
                repo=args.repo,
                pr_ref=args.pr_ref,
                issue_ref=args.issue_ref,
                url=args.url,
                provider_payload=_load_json_object(args.metadata_json)
                if args.metadata_json
                else None,
                fetch_metadata=fetch_metadata,
                fetch_timeout_seconds=args.fetch_timeout_seconds,
                maintainer_correction_input=(
                    _load_json_object(args.maintainer_correction_json)
                    if args.maintainer_correction_json
                    else None
                ),
                generated_at=generated_at,
            )
            should_write_domain_state = bool(
                not args.no_write_domain_state and (args.goal_id or args.ledger_path)
            )
            if should_write_domain_state:
                ledger_path = (
                    Path(args.ledger_path).expanduser()
                    if args.ledger_path
                    else default_issue_fix_domain_state_ledger_path(
                        project=args.project,
                        goal_id=args.goal_id,
                    )
                )
                upsert_issue_fix_pr_lifecycle_ledger_jsonl(
                    ledger_path,
                    payload,
                )
                observation = payload.get("observation")
                if (
                    args.goal_id
                    and isinstance(observation, dict)
                    and str(observation.get("state") or "").upper() == "MERGED"
                ):
                    payload["rollout_event"] = append_pr_merge_rollout_event(
                        payload=payload,
                        goal_id=args.goal_id,
                        registry_path=registry_path,
                        runtime_root_arg=runtime_root_arg,
                    )
            else:
                domain_state = payload.get("domain_state_projection")
                if isinstance(domain_state, dict) and not args.no_write_domain_state:
                    domain_state["write_skipped_reason"] = (
                        "goal_id_or_ledger_path_missing"
                    )
            transition = payload.get("transition")
            if args.execute_transition:
                if not isinstance(transition, dict):
                    raise ValueError("PR lifecycle transition is missing")
                grouped_monitor_writeback = materialize_issue_fix_grouped_monitors(
                    registry_path=registry_path,
                    runtime_root=Path(runtime_root_arg).expanduser() if runtime_root_arg else None,
                    goal_id=args.goal_id,
                    project=Path(args.project).expanduser(),
                    ledger_path=ledger_path,
                    claimed_by=args.claimed_by,
                    cadence=args.monitor_cadence,
                    generated_at=str(payload.get("generated_at") or generated_at),
                )
                payload["grouped_monitor_writeback"] = grouped_monitor_writeback
                grouped_monitor_projection = payload.get("grouped_monitor_projection")
                if isinstance(grouped_monitor_projection, dict):
                    grouped_monitor_projection["todo_write_performed"] = bool(
                        grouped_monitor_writeback.get("write_performed")
                    )
                decision = str(transition.get("decision") or "")
                if decision in {"runnable_successor", "user_gate"}:
                    role = str(transition.get("role") or "agent")
                    todo_write = add_goal_todo(
                        registry_path=registry_path,
                        goal_id=args.goal_id,
                        role=role,
                        text=str(transition.get("text") or ""),
                        task_class=str(
                            transition.get("task_class") or "advancement_task"
                        ),
                        action_kind=str(transition.get("action_kind") or ""),
                        required_write_scopes=(
                            list(transition.get("required_write_scopes") or [])
                            if role == "agent"
                            else None
                        ),
                        claimed_by=args.claimed_by if role == "agent" else None,
                        agent_id=args.claimed_by if role == "user" else None,
                        project=Path(args.project).expanduser(),
                    )
                    todo_changed = bool(
                        todo_write.get("added")
                        or todo_write.get("metadata_updated")
                        or todo_write.get("status_changed")
                    )
                    payload["todo_write"] = {
                        "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
                        "write_performed": todo_changed,
                        "already_exists": bool(todo_write.get("already_exists")),
                        "todo_id": todo_write.get("todo_id"),
                        "role": todo_write.get("role"),
                        "claimed_by": todo_write.get("claimed_by"),
                        "blocks_agent": todo_write.get("blocks_agent"),
                        "path_recorded": False,
                    }
                    payload["todo_write_performed"] = todo_changed
                    writeback_contract = payload.get("writeback_contract")
                    if isinstance(writeback_contract, dict):
                        writeback_contract["todo_write_performed"] = bool(todo_changed)
                else:
                    payload["todo_write"] = {
                        "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
                        "write_performed": False,
                        "already_exists": False,
                        "skip_reason": (
                            "unchanged_monitor_quiet"
                            if decision == "monitor_continuation"
                            else "terminal_or_no_followup"
                        ),
                        "path_recorded": False,
                    }
                grouped_write_performed = bool(
                    grouped_monitor_writeback.get("write_performed")
                )
                transition_write_performed = bool(payload.get("todo_write_performed"))
                payload["todo_write_performed"] = bool(
                    grouped_write_performed or transition_write_performed
                )
                if grouped_write_performed and not transition_write_performed:
                    payload["todo_write"] = {
                        "schema_version": "issue_fix_pr_lifecycle_todo_write_v1",
                        "write_performed": True,
                        "grouped_monitor_write_performed": True,
                        "transition_write_performed": False,
                        "path_recorded": False,
                    }
                writeback_contract = payload.get("writeback_contract")
                if isinstance(writeback_contract, dict):
                    writeback_contract["todo_write_performed"] = bool(
                        payload["todo_write_performed"]
                    )
                validation = validate_issue_fix_pr_lifecycle_monitor_packet(payload)
                payload["validation"] = validation
                payload["ok"] = bool(validation["ok"])
            renderer = render_issue_fix_pr_lifecycle_monitor_markdown
        elif args.issue_fix_command == "outcome":
            if args.write_delivery_evidence and not args.delivery_evidence_json:
                raise ValueError(
                    "--write-delivery-evidence requires --delivery-evidence-json"
                )
            if args.write_delivery_evidence and args.feasibility_json:
                raise ValueError(
                    "--write-delivery-evidence uses the default feasibility domain-state row; "
                    "do not combine it with --feasibility-json"
                )
            provider_path = args.repository_memory_provider_json or (
                default_repository_memory_provider_config_path()
                if args.write_repository_memory
                else None
            )
            if args.write_repository_memory and not provider_path:
                raise ValueError(
                    "--write-repository-memory requires an explicit provider config "
                    "or LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG"
                )
            stdin_input_count = sum(
                value == "-"
                for value in (
                    args.feasibility_json,
                    args.pr_lifecycle_json,
                    args.delivery_evidence_json,
                )
            )
            if stdin_input_count > 1:
                raise ValueError("only one outcome JSON input may read from stdin")
            project = Path(args.project).expanduser()
            feasibility_packet = (
                _load_json_object(args.feasibility_json)
                if args.feasibility_json
                else _load_jsonl_row(
                    default_issue_fix_feasibility_ledger_path(
                        project=project,
                        goal_id=args.goal_id,
                    ),
                    repo=args.repo,
                    ref_field="issue_ref",
                    ref_value=args.issue_ref,
                )
            )
            feasibility_observation = feasibility_packet.get("observation")
            if not isinstance(feasibility_observation, dict) or (
                str(feasibility_observation.get("repo") or "").strip() != args.repo
                or str(feasibility_observation.get("issue_ref") or "").strip()
                != args.issue_ref
            ):
                raise ValueError(
                    "--repo/--issue-ref must match the selected feasibility packet"
                )
            pr_lifecycle_packet = None
            if args.pr_lifecycle_json:
                pr_lifecycle_packet = _load_json_object(args.pr_lifecycle_json)
            elif args.pr_ref:
                pr_lifecycle_packet = _load_jsonl_row(
                    default_issue_fix_domain_state_ledger_path(
                        project=project,
                        goal_id=args.goal_id,
                    ),
                    repo=args.repo,
                    ref_field="pr_ref",
                    ref_value=args.pr_ref,
                )
            if pr_lifecycle_packet is not None and args.pr_ref:
                lifecycle_observation = pr_lifecycle_packet.get("observation")
                if (
                    not isinstance(lifecycle_observation, dict)
                    or str(lifecycle_observation.get("pr_ref") or "").strip()
                    != args.pr_ref
                ):
                    raise ValueError(
                        "--pr-ref must match the selected PR lifecycle packet"
                    )
            explicit_delivery_evidence = (
                _load_json_object(args.delivery_evidence_json)
                if args.delivery_evidence_json
                else None
            )
            delivery_evidence = (
                compact_issue_fix_delivery_evidence(
                    explicit_delivery_evidence,
                )
                if explicit_delivery_evidence is not None
                else (
                    feasibility_packet.get("delivery_evidence")
                    if isinstance(feasibility_packet.get("delivery_evidence"), dict)
                    else None
                )
            )
            if explicit_delivery_evidence is not None:
                requires_commit_verification = bool(
                    delivery_evidence.get("commit_ref")
                    and (
                        delivery_evidence.get("validation_status") == "passed"
                        or delivery_evidence.get("outcome_status") == "completed"
                    )
                )
                if requires_commit_verification and (
                    args.repo_path
                    or args.repository_ref
                    or args.write_delivery_evidence
                ):
                    if not args.repo_path or not args.repository_ref:
                        raise ValueError(
                            "passed or completed commit evidence writeback requires "
                            "--repo-path and --repository-ref"
                        )
                    repository_context = feasibility_observation.get(
                        "repository_context"
                    )
                    revision = (
                        str(repository_context.get("repository_revision") or "").strip()
                        if isinstance(repository_context, dict)
                        else ""
                    )
                    if not revision:
                        raise ValueError(
                            "passed or completed commit evidence requires a pinned "
                            "repository_revision in feasibility context"
                        )
                    repository_commit_evidence = (
                        verify_issue_fix_repository_commit_evidence(
                            repo_path=args.repo_path,
                            repo=args.repo,
                            repository_revision=revision,
                            commit_ref=str(delivery_evidence["commit_ref"]),
                            recovery_ref=args.repository_ref,
                            verified_at=generated_at,
                        )
                    )
                    delivery_evidence = compact_issue_fix_delivery_evidence(
                        {
                            **delivery_evidence,
                            "repository_commit_evidence": repository_commit_evidence,
                        }
                    )
            domain_state_write: dict[str, Any] | None = None
            if args.write_delivery_evidence:
                existing_delivery = feasibility_packet.get("delivery_evidence")
                existing_compact = (
                    compact_issue_fix_delivery_evidence(existing_delivery)
                    if isinstance(existing_delivery, dict)
                    else None
                )
                comparable_existing = _comparable_delivery_evidence(existing_compact)
                comparable_delivery = _comparable_delivery_evidence(delivery_evidence)
                if comparable_existing == comparable_delivery:
                    delivery_evidence = existing_delivery
                    write_result = {
                        "write_performed": False,
                        "status": "unchanged",
                        "row_count": None,
                    }
                else:
                    delivery_evidence = {
                        **delivery_evidence,
                        "recorded_at": generated_at,
                    }
                    feasibility_packet["delivery_evidence"] = delivery_evidence
                    write_result = upsert_issue_fix_feasibility_ledger_jsonl(
                        default_issue_fix_feasibility_ledger_path(
                            project=project,
                            goal_id=args.goal_id,
                        ),
                        feasibility_packet,
                    )
                domain_state_write = {
                    "schema_version": "issue_fix_delivery_evidence_writeback_v0",
                    "domain_pack": "issue_fix",
                    "stream": "feasibility",
                    "key": {"repo": args.repo, "issue_ref": args.issue_ref},
                    "write_performed": write_result.get("write_performed") is True,
                    "status": write_result.get("status"),
                    "row_count": write_result.get("row_count"),
                    "path_recorded": False,
                }
            payload = build_issue_fix_outcome_projection(
                goal_id=args.goal_id,
                feasibility_packet=feasibility_packet,
                pr_lifecycle_packet=pr_lifecycle_packet,
                delivery_evidence_input=delivery_evidence,
                agent_id=args.agent_id,
                generated_at=generated_at,
            )
            if args.write_repository_memory:
                if not args.repo_path:
                    raise ValueError(
                        "--write-repository-memory requires --repo-path for commit ancestry verification"
                    )
                outcome_case = (payload.get("issue_fix_outcomes") or [{}])[0]
                repository_context = (
                    outcome_case.get("repository_context")
                    if isinstance(outcome_case, dict)
                    else None
                )
                revision = (
                    str(repository_context.get("revision") or "").strip()
                    if isinstance(repository_context, dict)
                    else ""
                )
                if not revision:
                    raise ValueError(
                        "--write-repository-memory requires a revision-pinned outcome"
                    )
                writeback = write_issue_fix_validated_outcome_memory(
                    config=_load_json_object(str(provider_path)),
                    outcome_packet=payload,
                    repository_revision=revision,
                    repo_path=args.repo_path,
                    observed_at=generated_at,
                    execute=True,
                )
                payload["repository_memory_writeback"] = writeback
                payload["external_writes_performed"] = bool(
                    writeback.get("external_writes_performed")
                )
                payload["ok"] = writeback.get("ok") is True
                payload["source_contract"]["repository_memory_writeback"] = (
                    "issue_fix_validated_outcome_memory_writeback_v0"
                )
                payload["source_contract"]["writes_external_provider"] = bool(
                    writeback.get("external_writes_performed")
                )
                if payload["ok"] is not True:
                    payload["error"] = (
                        "repository memory writeback "
                        f"{writeback.get('status')}: "
                        f"{writeback.get('reason_code') or 'provider write failed'}"
                    )
            if domain_state_write is not None:
                payload["domain_state_write"] = domain_state_write
                payload["source_contract"]["writes_source_state"] = True
            renderer = render_issue_fix_outcome_projection_markdown
        elif args.issue_fix_command == "metrics":
            stdin_input_count = sum(
                value == "-"
                for value in (
                    args.repository_baseline_json,
                    args.repository_current_json,
                    args.supplement_json,
                )
            )
            if stdin_input_count > 1:
                raise ValueError("only one metrics JSON input may read from stdin")
            project = Path(args.project).expanduser()
            feasibility_path = (
                Path(args.feasibility_ledger).expanduser()
                if args.feasibility_ledger
                else default_issue_fix_feasibility_ledger_path(
                    project=project,
                    goal_id=args.goal_id,
                )
            )
            lifecycle_path = (
                Path(args.pr_lifecycle_ledger).expanduser()
                if args.pr_lifecycle_ledger
                else default_issue_fix_domain_state_ledger_path(
                    project=project,
                    goal_id=args.goal_id,
                )
            )
            payload = build_issue_fix_metrics_projection(
                goal_id=args.goal_id,
                repo=args.repo,
                baseline_snapshot=_load_json_object(args.repository_baseline_json),
                current_snapshot=_load_json_object(args.repository_current_json),
                feasibility_rows=_load_jsonl_rows(feasibility_path),
                pr_lifecycle_rows=_load_jsonl_rows(lifecycle_path),
                supplement_input=(
                    _load_json_object(args.supplement_json)
                    if args.supplement_json
                    else None
                ),
                generated_at=generated_at,
            )
            renderer = render_issue_fix_metrics_projection_markdown
        elif args.issue_fix_command == "metrics-supplement":
            payload = build_issue_fix_metrics_supplement_from_args(
                args,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                generated_at=generated_at,
                load_json_object=_load_json_object,
                load_jsonl_rows=_load_jsonl_rows,
            )
            renderer = render_issue_fix_metrics_supplement_markdown
        elif args.issue_fix_command == "repository-snapshot":
            if not args.fetch_public_github:
                raise ValueError("repository-snapshot requires --fetch-public-github")
            if sum(
                value == "-"
                for value in (
                    args.repository_baseline_json,
                    args.supplement_json,
                )
                if value is not None
            ) > 1:
                raise ValueError(
                    "only one repository-snapshot JSON input may read from stdin"
                )
            project = Path(args.project).expanduser()
            payload = collect_public_github_repository_snapshot(
                repo=args.repo,
                baseline_snapshot=_load_json_object(args.repository_baseline_json),
                feasibility_rows=_load_jsonl_rows(
                    default_issue_fix_feasibility_ledger_path(
                        project=project, goal_id=args.goal_id
                    )
                ),
                pr_lifecycle_rows=_load_jsonl_rows(
                    default_issue_fix_domain_state_ledger_path(
                        project=project, goal_id=args.goal_id
                    )
                ),
                metrics_supplement=(
                    _load_json_object(args.supplement_json)
                    if args.supplement_json
                    else None
                ),
                generated_at=generated_at,
                timeout_seconds=args.fetch_timeout_seconds,
            )
            if args.retain_material_snapshot:
                write_result = retain_issue_fix_repository_snapshot_jsonl(
                    default_issue_fix_repository_snapshot_ledger_path(
                        project=project, goal_id=args.goal_id
                    ),
                    repository_snapshot_record(payload["snapshot"]),
                )
                payload["retention"] = {
                    "schema_version": "issue_fix_repository_snapshot_retention_v0",
                    "domain_pack": "issue_fix",
                    "stream": "repository_snapshots",
                    "write_performed": write_result.get("write_performed") is True,
                    "status": write_result.get("status"),
                    "row_count": write_result.get("row_count"),
                    "path_recorded": False,
                }
                payload["source_contract"]["writes_source_state"] = True
            renderer = render_repository_snapshot_markdown
        elif args.issue_fix_command == "acceptance-fixture":
            payload = build_issue_fix_acceptance_fixture_packet(
                repo=args.repo,
                issue_ref=args.issue_ref,
                url=args.url,
                generated_at=generated_at,
            )
            renderer = render_issue_fix_acceptance_loop_markdown
        elif args.issue_fix_command == "repo-branch-fixture":
            payload = build_issue_fix_repo_branch_fixture_packet(
                repo=args.repo,
                issue_ref=args.issue_ref,
                url=args.url,
                generated_at=generated_at,
            )
            renderer = render_issue_fix_acceptance_loop_markdown
        elif args.issue_fix_command == "caller-repo-branch":
            payload = build_issue_fix_caller_repo_branch_packet(
                repo_path=args.repo_path,
                repo=args.repo,
                issue_ref=args.issue_ref,
                url=args.url,
                base_branch=args.base_branch,
                issue_branch=args.issue_branch,
                validation_command=args.validation_command,
                validation_label=args.validation_label,
                execute=args.execute,
                timeout_seconds=args.timeout_seconds,
                generated_at=generated_at,
            )
            renderer = render_issue_fix_acceptance_loop_markdown
        else:
            raise ValueError(
                "issue-fix requires `repository-memory-sync`, `promote-discovered-issue`, "
                "`workflow-plan`, `feasibility`, "
                "`acceptance-fixture`, `pr-lifecycle`, `pr-gate-reconcile`, `outcome`, `metrics`, "
                "`metrics-supplement`, "
                "`repository-snapshot`, `reviewer-plan`, "
                "`reviewer-request`, `reviewer-notification-drain`, "
                "`repo-branch-fixture`, or `caller-repo-branch`"
            )
    except Exception as exc:
        payload = {
            "ok": False,
            "mode": "issue-fix",
            "error": str(exc),
        }
        renderer = (
            render_issue_fix_repository_memory_sync_markdown
            if getattr(args, "issue_fix_command", None) == "repository-memory-sync"
            else render_issue_fix_discovered_issue_promotion_markdown
            if getattr(args, "issue_fix_command", None) == "promote-discovered-issue"
            else render_issue_fix_workflow_plan_markdown
            if getattr(args, "issue_fix_command", None) == "workflow-plan"
            else render_issue_fix_feasibility_markdown
            if getattr(args, "issue_fix_command", None) == "feasibility"
            else render_issue_fix_pr_lifecycle_monitor_markdown
            if getattr(args, "issue_fix_command", None)
            in {"pr-lifecycle", "pr-gate-reconcile"}
            else render_issue_fix_outcome_projection_markdown
            if getattr(args, "issue_fix_command", None) == "outcome"
            else render_issue_fix_metrics_projection_markdown
            if getattr(args, "issue_fix_command", None) == "metrics"
            else render_issue_fix_metrics_supplement_markdown
            if getattr(args, "issue_fix_command", None) == "metrics-supplement"
            else render_repository_snapshot_markdown
            if getattr(args, "issue_fix_command", None) == "repository-snapshot"
            else reviewer_renderer(getattr(args, "issue_fix_command", None))
            if getattr(args, "issue_fix_command", None) in REVIEWER_COMMANDS
            else render_issue_fix_acceptance_loop_markdown
        )
    print_payload(payload, output_format(args), renderer)
    return 0 if payload.get("ok") else 1
