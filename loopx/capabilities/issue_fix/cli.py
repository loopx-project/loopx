from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from ...agent_registry import load_goal_from_registry
from ...boundary_authority import checkpointed_boundary_authority_summary
from ...control_plane.runtime.time import now_utc_iso
from ...control_plane.todos.mutation_api import add_goal_todo
from ...domain_packs.issue_fix import (
    default_issue_fix_candidate_preflight_ledger_path,
    default_issue_fix_domain_state_ledger_path,
    default_issue_fix_feasibility_ledger_path,
    default_issue_fix_repository_snapshot_ledger_path,
    issue_fix_maintainer_correction_entries,
    read_issue_fix_pr_lifecycle_ledger_row,
    retain_issue_fix_repository_snapshot_jsonl,
    upsert_issue_fix_candidate_preflight_ledger_jsonl,
    upsert_issue_fix_feasibility_ledger_jsonl,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from . import pr_gate_reconcile_cli
from .acceptance_loop import (
    build_issue_fix_acceptance_fixture_packet,
    build_issue_fix_caller_repo_branch_packet,
    build_issue_fix_repo_branch_fixture_packet,
    render_issue_fix_acceptance_loop_markdown,
)
from .candidate_evidence import collect_public_github_candidate_preflight_input
from .cli_input import (
    _MAX_INLINE_JSON_CHARS as _MAX_INLINE_JSON_CHARS,
    load_json_object as _load_json_object,
    load_jsonl_row as _load_jsonl_row,
    load_jsonl_rows as _load_jsonl_rows,
)
from .correction_settlement import (
    _correction_todo_readback_receipt,
    _require_origin_registry,
    _resolve_origin_request_identity,
    _settle_maintainer_correction_entries,
)
from .discovered_issue_promotion import (
    build_discovered_issue_promotion_from_cli_args,
    register_discovered_issue_promotion_command,
    render_issue_fix_discovered_issue_promotion_markdown,
)
from .feasibility import (
    build_issue_fix_feasibility_packet,
    render_issue_fix_feasibility_markdown,
)
from .metadata_preview import normalise_github_issue_reference
from .metrics_projection import (
    build_issue_fix_metrics_projection,
    render_issue_fix_metrics_projection_markdown,
)
from .metrics_supplement import render_issue_fix_metrics_supplement_markdown
from .metrics_supplement_cli import (
    build_issue_fix_metrics_supplement_from_args,
    register_issue_fix_metrics_supplement_command,
)
from .outcome_projection import (
    build_issue_fix_outcome_projection,
    compact_issue_fix_delivery_evidence,
    render_issue_fix_outcome_projection_markdown,
)
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
from .repository_commit_evidence import verify_issue_fix_repository_commit_evidence
from .repository_memory import SUPPORT_ASPECTS
from .repository_memory_provider import (
    default_repository_memory_provider_config_path,
    render_issue_fix_repository_memory_sync_markdown,
    retrieve_issue_fix_repository_memory,
    sync_issue_fix_repository_memory,
    write_issue_fix_validated_outcome_memory,
)
from .repository_snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    collect_public_github_repository_snapshot,
    render_repository_snapshot_markdown,
    repository_snapshot_record,
)
from .reviewer_cli import (
    REVIEWER_COMMANDS,
    _materialize_goal_reviewer_notification_lifecycle as _materialize_goal_reviewer_notification_lifecycle,
    handle_issue_fix_reviewer_command,
    register_issue_fix_reviewer_commands,
    reviewer_renderer,
)
from .workflow_plan import (
    build_issue_fix_workflow_plan_packet,
    render_issue_fix_workflow_plan_markdown,
)


Payload = dict[str, object]
Renderer = Callable[[Payload], str]
PrintPayload = Callable[[Payload, str, Renderer], None]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def _comparable_delivery_evidence(value: Mapping[str, object] | None) -> Payload | None:
    if value is None:
        return None
    comparable: Payload = {key: item for key, item in value.items() if key != "recorded_at"}
    repository_evidence = comparable.get("repository_commit_evidence")
    if isinstance(repository_evidence, Mapping):
        comparable["repository_commit_evidence"] = {
            key: item for key, item in repository_evidence.items() if key != "verified_at"
        }
    return comparable


def _optional_json_object(value: str | None) -> Payload | None:
    return _load_json_object(value) if value else None


def _ensure_single_stdin(*values: str | None, scope: str = "JSON") -> None:
    if sum(value == "-" for value in values if value is not None) > 1:
        raise ValueError(f"only one {scope} input may read from stdin")


def _resolve_provider_path(args: argparse.Namespace) -> str | None:
    return args.repository_memory_provider_json or (
        None if args.repository_memory_json else default_repository_memory_provider_config_path()
    )


def _resolve_ledger_path(
    explicit_path: str | None,
    default_builder: Callable[..., Path],
    *,
    project: str | Path,
    goal_id: str | None,
) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()
    return default_builder(project=project, goal_id=goal_id)


def _set_write_skipped_reason(container: Payload, reason: str) -> None:
    domain_state = container.get("domain_state_projection")
    if isinstance(domain_state, dict):
        domain_state["write_skipped_reason"] = reason


def _set_writeback_performed(payload: Payload, performed: bool) -> None:
    writeback_contract = payload.get("writeback_contract")
    if isinstance(writeback_contract, dict):
        writeback_contract["todo_write_performed"] = performed


def _configured_repository_memory_input(
    *,
    provider_path: str | None,
    raw_memory_path: str | None,
    repo_path: str | None,
    repository_context_input: Mapping[str, object] | None,
    repo: str,
    issue_ref: str,
    query: str | None,
    supports: Sequence[str],
    validation_label: str | None,
    observed_at: str,
) -> Mapping[str, object] | None:
    if raw_memory_path and provider_path:
        raise ValueError("--repository-memory-json cannot be combined with a configured provider")
    if raw_memory_path:
        return _load_json_object(raw_memory_path)
    if not provider_path or not repo_path or not repository_context_input:
        return None
    revision = str(repository_context_input.get("repository_revision") or "").strip()
    if not revision:
        return None
    retrieval_query = query or " ".join(
        value
        for value in (repo, issue_ref, validation_label or "repository architecture reproduction validation")
        if value
    )
    query_summary = query or f"Public repository context for {repo} {issue_ref} before patch planning."
    result = retrieve_issue_fix_repository_memory(
        config=_load_json_object(provider_path),
        repo_path=repo_path,
        repository_revision=revision,
        query=retrieval_query,
        query_summary=query_summary,
        supports=supports or ["architecture", "change_scope", "reproduction", "validation"],
        observed_at=observed_at,
    )
    memory_input = result.get("memory_input")
    if isinstance(memory_input, Mapping):
        typed_memory: Payload = {str(key): value for key, value in memory_input.items()}
        return typed_memory
    return None


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


def _add_generated_at_arg(parser: argparse.ArgumentParser, *, artifact: str) -> None:
    parser.add_argument(
        "--generated-at",
        default=None,
        help=f"Public-safe generated_at timestamp for {artifact}; defaults to current UTC invocation time.",
    )


def _add_subparser(
    issue_fix_sub: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
    name: str,
    help_text: str,
) -> argparse.ArgumentParser:
    parser = issue_fix_sub.add_parser(name, help=help_text)
    add_subcommand_format(parser)
    return parser


def _add_repo_issue_url_args(
    parser: argparse.ArgumentParser,
    *,
    repo_default: str = "public_repo_fixture",
    repo_help: str,
    issue_default: str | None = "issue_123_public_metadata_fixture",
    issue_help: str,
    url_help: str,
    pr_ref_help: str | None = None,
) -> None:
    parser.add_argument("--repo", default=repo_default, help=repo_help)
    if pr_ref_help is not None:
        parser.add_argument("--pr-ref", default="pull_123_public_metadata_fixture", help=pr_ref_help)
    parser.add_argument("--issue-ref", default=issue_default, help=issue_help)
    parser.add_argument("--url", default=None, help=url_help)


def _add_fetch_metadata_args(parser: argparse.ArgumentParser, *, fetch_help: str) -> None:
    parser.add_argument("--fetch-metadata", action="store_true", help=fetch_help)
    parser.add_argument("--fetch-timeout-seconds", type=int, default=10, help="Timeout for --fetch-metadata.")


def _add_domain_state_args(
    parser: argparse.ArgumentParser,
    *,
    goal_help: str,
    project_help: str,
    ledger_flag: str = "--ledger-path",
    ledger_help: str,
    no_write_help: str,
) -> None:
    parser.add_argument("--goal-id", default=None, help=goal_help)
    parser.add_argument("--project", default=".", help=project_help)
    parser.add_argument(ledger_flag, default=None, help=ledger_help)
    parser.add_argument("--no-write-domain-state", action="store_true", help=no_write_help)


def _add_required_goal_repo_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--project", default=".")
    parser.add_argument("--repo", required=True)


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
    raw_coordination = goal.get("coordination")
    coordination = raw_coordination if isinstance(raw_coordination, dict) else {}
    summary = checkpointed_boundary_authority_summary(coordination)
    scopes = list(summary.get("active_write_scope") or []) if isinstance(summary, dict) else []
    return [str(scope) for scope in scopes], True


def register_issue_fix_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> None:
    issue_fix_parser = subparsers.add_parser("issue-fix", help="Run public-safe issue fix acceptance loops.")
    issue_fix_sub = issue_fix_parser.add_subparsers(dest="issue_fix_command", required=True)

    memory_sync_parser = _add_subparser(
        issue_fix_sub,
        add_subcommand_format,
        "repository-memory-sync",
        "Plan or explicitly execute a bounded public resource sync through the reusable context-provider module.",
    )
    memory_sync_parser.add_argument("--repo-path", required=True)
    memory_sync_parser.add_argument("--repository-context-json", required=True)
    memory_sync_parser.add_argument(
        "--repository-memory-provider-json",
        default=None,
        help="Local-private provider config; defaults to LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG.",
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

    workflow_parser = _add_subparser(
        issue_fix_sub,
        add_subcommand_format,
        "workflow-plan",
        (
            "Plan the full issue-fix workflow from public metadata to ordered "
            "LoopX todos, validation, and PR review packet readiness without writes."
        ),
    )
    _add_repo_issue_url_args(
        workflow_parser,
        repo_help="Public-safe repository label for the issue metadata.",
        issue_help="Public-safe issue or PR reference label.",
        url_help="Optional https://github.com/owner/repo/issues/123 or /pull/123 URL.",
    )
    workflow_parser.add_argument(
        "--metadata-json",
        default=None,
        help="Path to mocked provider JSON metadata, or '-' for stdin. Body/comment fields stay gated and are not copied.",
    )
    _add_fetch_metadata_args(
        workflow_parser,
        fetch_help="Explicitly fetch public GitHub issue/PR metadata with gh api --jq. Only body-free metadata fields are captured.",
    )
    workflow_parser.add_argument(
        "--repo-path",
        default=None,
        help="Optional caller-approved local git repository path. Planning keeps this as a dry-run and never records the path.",
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
        help="Collect complete issue-specific GitHub closing-PR, cross-reference, and maintainer-disposition receipts before planning.",
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
    _add_domain_state_args(
        workflow_parser,
        goal_help=(
            "Goal id used to persist the source-qualified candidate admission "
            "receipt before any feasibility or implementation decision."
        ),
        project_help="Project root for the default issue_fix candidate preflight ledger.",
        ledger_flag="--candidate-preflight-ledger-path",
        ledger_help="Optional candidate preflight JSONL path. Overrides the default path.",
        no_write_help="Keep candidate preflight preview-only even when --goal-id or an explicit ledger path is present.",
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

    feasibility_parser = _add_subparser(
        issue_fix_sub,
        add_subcommand_format,
        "feasibility",
        "Select exactly one fix_pr, comment_only, or triage_only route from compact public-safe agent observations.",
    )
    _add_repo_issue_url_args(
        feasibility_parser,
        repo_help="Public-safe repository label for the issue.",
        issue_help="Public-safe issue reference label.",
        url_help="Optional https://github.com/owner/repo/issues/123 URL.",
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
        help="Optional caller-approved current checkout used only to verify provider content. The path is never retained.",
    )
    _add_repository_memory_provider_args(feasibility_parser)
    _add_generated_at_arg(feasibility_parser, artifact="the feasibility decision")
    _add_domain_state_args(
        feasibility_parser,
        goal_help="Goal id used for the default issue_fix feasibility ledger path.",
        project_help="Project root for the default issue_fix feasibility ledger path.",
        ledger_help="Optional local JSONL ledger path. Overrides the default path.",
        no_write_help="Keep feasibility preview-only even when --goal-id or --ledger-path is present.",
    )

    pr_lifecycle_parser = _add_subparser(
        issue_fix_sub,
        add_subcommand_format,
        "pr-lifecycle",
        "Project a public PR lifecycle observation into a successor, monitor-continuation, user-gate, or no-follow-up transition.",
    )
    _add_repo_issue_url_args(
        pr_lifecycle_parser,
        repo_help="Public-safe repository label for the PR metadata.",
        pr_ref_help="Public-safe PR reference label.",
        issue_default=None,
        issue_help=(
            "Optional public-safe issue reference linked to this PR. Persisting "
            "the explicit link lets default Kanban sync compose the issue outcome "
            "without guessing from titles or branch names. Numeric aliases such "
            "as #123 and issue_123 are stored as issues_123."
        ),
        url_help="Optional https://github.com/owner/repo/pull/123 URL.",
    )
    pr_lifecycle_parser.add_argument(
        "--metadata-json",
        default=None,
        help="Path to mocked gh pr view JSON metadata, or '-' for stdin. Bodies, comments, raw logs, and provider responses stay out.",
    )
    pr_lifecycle_parser.add_argument("--origin-registry", help="Origin lifecycle registry path.")
    pr_lifecycle_parser.add_argument("--origin-runtime-root", help="Origin runtime root path.")
    pr_lifecycle_parser.add_argument(
        "--origin-request-id",
        help="Bind this PR row to the originating steward request (requires Goal and agent).",
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
    _add_fetch_metadata_args(
        pr_lifecycle_parser,
        fetch_help="Explicitly fetch public GitHub PR state with gh pr view. Only compact lifecycle fields are captured.",
    )
    _add_generated_at_arg(pr_lifecycle_parser, artifact="the lifecycle projection")
    _add_domain_state_args(
        pr_lifecycle_parser,
        goal_help="Goal id used for the default issue_fix domain-state ledger path.",
        project_help="Project root for the default issue_fix domain-state ledger path.",
        ledger_help="Optional local JSONL ledger path. Overrides the default domain-state path.",
        no_write_help="Keep the PR lifecycle command preview-only even when --goal-id or --ledger-path is present.",
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
        help="Registered agent that claims an actionable patch successor or is blocked by the generated concrete user gate.",
    )
    pr_lifecycle_parser.add_argument(
        "--monitor-cadence",
        default=DEFAULT_ISSUE_FIX_MONITOR_CADENCE,
        help="Cadence for grouped PR lifecycle continuous monitors materialized by --execute-transition (default: 30m).",
    )

    pr_gate_reconcile_cli.register_pr_gate_reconciliation_command(issue_fix_sub)

    outcome_parser = _add_subparser(
        issue_fix_sub,
        add_subcommand_format,
        "outcome",
        "Compose one public-safe issue-fix status/output projection from existing feasibility and optional PR lifecycle state.",
    )
    _add_required_goal_repo_args(outcome_parser)
    outcome_parser.add_argument("--issue-ref", required=True)
    outcome_parser.add_argument("--pr-ref", help="Optional PR ref linked to this issue, such as pull_456.")
    outcome_parser.add_argument(
        "--feasibility-json",
        help="Optional issue_fix_feasibility_v0 JSON object; defaults to domain state.",
    )
    outcome_parser.add_argument(
        "--pr-lifecycle-json",
        help="Optional issue_fix_pr_lifecycle_monitor_v0 JSON object; defaults to domain state when --pr-ref is present.",
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
        help="Validate and write compact delivery evidence into the selected existing feasibility row so default outcome and Kanban sync retain it.",
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
    outcome_parser.add_argument("--agent-id", help="Optional registered agent id projected as the case owner.")
    _add_generated_at_arg(outcome_parser, artifact="the read model")

    metrics_parser = _add_subparser(
        issue_fix_sub,
        add_subcommand_format,
        "metrics",
        "Compose a read-only baseline, attributable output inventory, repository delta, and missing-data projection from existing issue-fix domain state.",
    )
    _add_required_goal_repo_args(metrics_parser)
    metrics_parser.add_argument(
        "--repository-baseline-json",
        required=True,
        help=f"Public-safe {SNAPSHOT_SCHEMA_VERSION} at period start.",
    )
    metrics_parser.add_argument(
        "--repository-current-json",
        required=True,
        help=f"Public-safe {SNAPSHOT_SCHEMA_VERSION} at current time, including flow_since_baseline.",
    )
    metrics_parser.add_argument(
        "--supplement-json",
        default=None,
        help="Optional public-safe issue_fix_metrics_supplement_v0 for autonomy, first-push CI, memory, and capability-delta counts.",
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

    snapshot_parser = _add_subparser(
        issue_fix_sub,
        add_subcommand_format,
        "repository-snapshot",
        "Collect a compact public GitHub repository snapshot for issue-fix metrics and optionally retain one material snapshot per day.",
    )
    _add_required_goal_repo_args(snapshot_parser)
    snapshot_parser.add_argument("--repository-baseline-json", required=True)
    snapshot_parser.add_argument(
        "--supplement-json",
        default=None,
        help="Optional public-safe issue_fix_metrics_supplement_v0 whose issue-close activity refs must be included in current-state collection.",
    )
    snapshot_parser.add_argument(
        "--fetch-public-github",
        action="store_true",
        help="Explicitly read bounded public repository metadata through gh.",
    )
    snapshot_parser.add_argument(
        "--retain-material-snapshot",
        action="store_true",
        help="Write only a material daily change into the existing issue_fix domain-state stream.",
    )
    snapshot_parser.add_argument("--fetch-timeout-seconds", type=int, default=30)
    _add_generated_at_arg(snapshot_parser, artifact="the repository snapshot")

    register_issue_fix_reviewer_commands(
        issue_fix_sub,
        add_subcommand_format=add_subcommand_format,
        add_generated_at_arg=_add_generated_at_arg,
    )

    for name, help_text in (
        (
            "acceptance-fixture",
            "Run a deterministic fix loop: failing repro, minimal patch, focused validation, and PR-review-ready artifact.",
        ),
        (
            "repo-branch-fixture",
            "Run the fix loop through a temporary git repo issue branch: branch, repro, patch, validation, and PR evidence.",
        ),
    ):
        fixture_parser = _add_subparser(issue_fix_sub, add_subcommand_format, name, help_text)
        _add_repo_issue_url_args(
            fixture_parser,
            repo_help="Public-safe repository label for the issue metadata fixture.",
            issue_help="Public-safe issue or PR reference label for the fixture.",
            url_help="Optional public GitHub issue or PR URL for metadata parsing.",
        )
        _add_generated_at_arg(fixture_parser, artifact="the fixture artifact")

    caller_parser = _add_subparser(
        issue_fix_sub,
        add_subcommand_format,
        "caller-repo-branch",
        "Prepare or execute an explicitly approved local repo issue branch workflow without external comments, PR creation, or merge.",
    )
    caller_parser.add_argument(
        "--repo-path",
        required=True,
        help="Caller-approved local git repository path. The public artifact records only a repo label, never this path.",
    )
    _add_repo_issue_url_args(
        caller_parser,
        repo_default="approved_local_repo",
        repo_help="Public-safe repository label when no URL-derived repo is provided.",
        issue_default="issue_123_public_metadata",
        issue_help="Public-safe issue or PR reference label.",
        url_help="Optional public GitHub issue or PR URL used only for compact metadata.",
    )
    caller_parser.add_argument(
        "--base-branch",
        default="main",
        help="Approved local base branch used when creating a new issue branch.",
    )
    caller_parser.add_argument(
        "--issue-branch",
        default=None,
        help="Optional issue branch to create or claim. Defaults to codex/<issue>-fix.",
    )
    caller_parser.add_argument(
        "--validation-command",
        default=None,
        help="Caller-declared validation command. Required with --execute.",
    )
    caller_parser.add_argument(
        "--validation-label",
        default="caller-declared validation",
        help="Public-safe validation label stored in the artifact instead of raw command text.",
    )
    caller_parser.add_argument("--timeout-seconds", type=int, default=60, help="Validation timeout in seconds.")
    caller_parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually inspect the approved repo, create or claim the issue branch, and run the caller-declared validation.",
    )
    _add_generated_at_arg(caller_parser, artifact="the artifact")


def _handle_repository_memory_sync(args: argparse.Namespace, *, generated_at: str) -> tuple[Payload, Renderer]:
    provider_path = args.repository_memory_provider_json or default_repository_memory_provider_config_path()
    if not provider_path:
        raise ValueError("repository memory provider config is required")
    _ensure_single_stdin(provider_path, args.repository_context_json)
    repository_context_input = _load_json_object(args.repository_context_json)
    revision = str(repository_context_input.get("repository_revision") or "").strip()
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
    return payload, render_issue_fix_repository_memory_sync_markdown


def _handle_workflow_plan(args: argparse.Namespace, *, generated_at: str) -> tuple[Payload, Renderer]:
    if args.fetch_metadata and args.metadata_json:
        raise ValueError("--fetch-metadata cannot be combined with --metadata-json")
    if args.fetch_candidate_evidence and args.candidate_preflight_json:
        raise ValueError("--fetch-candidate-evidence cannot be combined with --candidate-preflight-json")
    if args.candidate_resolution_json and not args.fetch_candidate_evidence:
        raise ValueError("--candidate-resolution-json requires --fetch-candidate-evidence")
    provider_path = _resolve_provider_path(args)
    _ensure_single_stdin(
        args.metadata_json,
        args.repository_context_json,
        args.repository_memory_json,
        args.candidate_preflight_json,
        args.candidate_resolution_json,
        provider_path,
    )
    repository_context_input = _optional_json_object(args.repository_context_json)
    candidate_preflight_input: Payload | None
    if args.fetch_candidate_evidence:
        reference = normalise_github_issue_reference(repo=args.repo, issue_ref=args.issue_ref, url=args.url)
        if reference["kind"] != "issue":
            raise ValueError("--fetch-candidate-evidence requires a GitHub issue")
        candidate_preflight_input = collect_public_github_candidate_preflight_input(
            repo=str(reference["repo"]),
            issue_ref=str(reference["issue_ref"]),
            generated_at=generated_at,
            timeout_seconds=args.candidate_evidence_timeout_seconds,
        )
        if args.candidate_resolution_json:
            candidate_preflight_input["candidate_resolution"] = _load_json_object(args.candidate_resolution_json)
    else:
        candidate_preflight_input = _optional_json_object(args.candidate_preflight_json)
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
    payload: Payload = build_issue_fix_workflow_plan_packet(
        repo=args.repo,
        issue_ref=args.issue_ref,
        url=args.url,
        provider_payload=_optional_json_object(args.metadata_json),
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
    should_write = bool(not args.no_write_domain_state and (args.goal_id or args.candidate_preflight_ledger_path))
    if should_write:
        if not isinstance(candidate_preflight, dict):
            raise ValueError("workflow plan omitted the candidate preflight receipt")
        ledger_path = _resolve_ledger_path(
            args.candidate_preflight_ledger_path,
            default_issue_fix_candidate_preflight_ledger_path,
            project=args.project,
            goal_id=args.goal_id,
        )
        upsert_issue_fix_candidate_preflight_ledger_jsonl(ledger_path, candidate_preflight)
    elif isinstance(candidate_preflight, dict):
        _set_write_skipped_reason(
            candidate_preflight,
            "explicitly_disabled" if args.no_write_domain_state else "goal_id_or_ledger_path_missing",
        )
    return payload, render_issue_fix_workflow_plan_markdown


def _handle_feasibility(
    args: argparse.Namespace,
    *,
    registry_path: Path | None,
    generated_at: str,
) -> tuple[Payload, Renderer]:
    provider_path = _resolve_provider_path(args)
    _ensure_single_stdin(args.repository_context_json, args.repository_memory_json, provider_path)
    repository_context_input = _optional_json_object(args.repository_context_json)
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
    scopes, resolved = _goal_boundary_authority_projection(registry_path=registry_path, goal_id=args.goal_id)
    payload: Payload = build_issue_fix_feasibility_packet(
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
        boundary_authority_scopes=scopes,
        boundary_authority_resolved=resolved,
        generated_at=generated_at,
    )
    should_write = bool(not args.no_write_domain_state and (args.goal_id or args.ledger_path))
    if should_write:
        ledger_path = _resolve_ledger_path(
            args.ledger_path,
            default_issue_fix_feasibility_ledger_path,
            project=args.project,
            goal_id=args.goal_id,
        )
        upsert_issue_fix_feasibility_ledger_jsonl(ledger_path, payload)
    elif not args.no_write_domain_state:
        _set_write_skipped_reason(payload, "goal_id_or_ledger_path_missing")
    return payload, render_issue_fix_feasibility_markdown


def _resolve_origin_request(
    args: argparse.Namespace,
    *,
    prior_row: Mapping[str, object] | None,
    origin_registry: Path | None,
    origin_runtime: Path | None,
    generated_at: str,
) -> Payload | None:
    raw_origin = (prior_row or {}).get("origin_request")
    origin: Payload | None = raw_origin if isinstance(raw_origin, dict) else None
    if not args.origin_request_id or origin_registry is None or origin_runtime is None:
        return origin
    if origin is not None and (
        origin["request_id"] != args.origin_request_id
        or origin["registry_path"] != str(origin_registry)
        or origin["runtime_root"] != str(origin_runtime)
    ):
        raise ValueError("origin_request_conflict")
    if not origin_registry.is_file() or not origin_runtime.is_dir():
        raise ValueError("origin registry and runtime root must exist")
    _require_origin_registry(origin_registry)
    candidate: Payload = {
        "request_id": args.origin_request_id,
        "registry_path": str(origin_registry),
        "runtime_root": str(origin_runtime),
        **_resolve_origin_request_identity(origin_runtime, args.origin_request_id),
        "recorded_at": origin["recorded_at"] if origin is not None else generated_at,
    }
    if origin is not None and origin != candidate:
        raise ValueError("origin_request_conflict")
    return candidate


def _execute_pr_lifecycle_transition(
    args: argparse.Namespace,
    *,
    payload: Payload,
    registry_path: Path,
    runtime_root_arg: str | None,
    ledger_path: Path,
    pending_source_events: object,
    generated_at: str,
) -> None:
    transition = payload.get("transition")
    if not isinstance(transition, dict):
        raise ValueError("PR lifecycle transition is missing")
    project = Path(args.project).expanduser()
    grouped_monitor_writeback = materialize_issue_fix_grouped_monitors(
        registry_path=registry_path,
        runtime_root=Path(runtime_root_arg).expanduser() if runtime_root_arg else None,
        goal_id=args.goal_id,
        project=project,
        ledger_path=ledger_path,
        claimed_by=args.claimed_by,
        cadence=args.monitor_cadence,
        generated_at=str(payload.get("generated_at") or generated_at),
    )
    payload["grouped_monitor_writeback"] = grouped_monitor_writeback
    grouped_projection = payload.get("grouped_monitor_projection")
    if isinstance(grouped_projection, dict):
        grouped_projection["todo_write_performed"] = bool(grouped_monitor_writeback.get("write_performed"))
    decision = str(transition.get("decision") or "")
    correction_admission = payload.get("maintainer_correction_admission")
    settlement = _settle_maintainer_correction_entries(
        payload=payload,
        ledger_path=ledger_path,
        registry_path=registry_path,
        goal_id=args.goal_id,
        claimed_by=args.claimed_by,
        project=project,
        source_events=pending_source_events if isinstance(pending_source_events, dict) else None,
    )
    if settlement.get("settlements") or isinstance(correction_admission, dict):
        payload["maintainer_correction_settlement"] = settlement
    if isinstance(correction_admission, dict):
        settled_write = settlement.get("todo_write")
        todo_write_receipt = settled_write if isinstance(settled_write, dict) else {}
        todo_changed = bool(todo_write_receipt.get("write_performed"))
        if not todo_write_receipt:
            todo_write_receipt = _correction_todo_readback_receipt(skip_reason="terminal_or_no_followup")
        if (
            not todo_changed
            and "skip_reason" in todo_write_receipt
            and decision == "monitor_continuation"
            and correction_admission.get("disposition") == "accepted"
        ):
            todo_write_receipt["skip_reason"] = "unchanged_monitor_quiet"
        payload["todo_write"] = todo_write_receipt
        payload["todo_write_performed"] = todo_changed
        _set_writeback_performed(payload, todo_changed)
    elif decision in {"runnable_successor", "user_gate"}:
        role = str(transition.get("role") or "agent")
        todo_write = add_goal_todo(
            registry_path=registry_path,
            goal_id=args.goal_id,
            role=role,
            text=str(transition.get("text") or ""),
            task_class=str(transition.get("task_class") or "advancement_task"),
            action_kind=str(transition.get("action_kind") or ""),
            required_write_scopes=list(transition.get("required_write_scopes") or []) if role == "agent" else None,
            claimed_by=args.claimed_by if role == "agent" else None,
            agent_id=args.claimed_by if role == "user" else None,
            project=project,
        )
        todo_changed = bool(
            todo_write.get("added") or todo_write.get("metadata_updated") or todo_write.get("status_changed")
        )
        created_todo = todo_write["todo"]
        payload["todo_write"] = {
            "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
            "write_performed": todo_changed,
            "already_exists": bool(todo_write.get("already_exists")),
            "todo_id": todo_write.get("todo_id"),
            "role": todo_write.get("role"),
            "claimed_by": created_todo.get("claimed_by"),
            "blocks_agent": created_todo.get("blocks_agent"),
            "path_recorded": False,
        }
        payload["todo_write_performed"] = todo_changed
        _set_writeback_performed(payload, todo_changed)
    else:
        payload["todo_write"] = {
            "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
            "write_performed": False,
            "already_exists": False,
            "skip_reason": "unchanged_monitor_quiet" if decision == "monitor_continuation" else "terminal_or_no_followup",
            "path_recorded": False,
        }
    grouped_write_performed = bool(grouped_monitor_writeback.get("write_performed"))
    transition_write_performed = bool(payload.get("todo_write_performed"))
    payload["todo_write_performed"] = bool(grouped_write_performed or transition_write_performed)
    if grouped_write_performed and not transition_write_performed:
        payload["todo_write"] = {
            "schema_version": "issue_fix_pr_lifecycle_todo_write_v1",
            "write_performed": True,
            "grouped_monitor_write_performed": True,
            "transition_write_performed": False,
            "path_recorded": False,
        }
    _set_writeback_performed(payload, bool(payload["todo_write_performed"]))
    validation = validate_issue_fix_pr_lifecycle_monitor_packet(payload)
    payload["validation"] = validation
    payload["ok"] = bool(validation["ok"])


def _handle_pr_lifecycle(
    args: argparse.Namespace,
    *,
    registry_path: Path | None,
    runtime_root_arg: str | None,
    generated_at: str,
) -> tuple[Payload, Renderer]:
    if args.fetch_metadata and args.metadata_json:
        raise ValueError("--fetch-metadata cannot be combined with --metadata-json")
    fetch_metadata = bool(args.fetch_metadata or (args.execute_transition and args.url and not args.metadata_json))
    if args.execute_transition:
        if args.no_write_domain_state:
            raise ValueError("--execute-transition cannot be combined with --no-write-domain-state")
        if registry_path is None:
            raise ValueError("--execute-transition requires a LoopX registry")
        if not args.goal_id:
            raise ValueError("--execute-transition requires --goal-id")
        if not args.claimed_by:
            raise ValueError("--execute-transition requires --claimed-by")
        if args.maintainer_correction_json and load_goal_from_registry(registry_path, args.goal_id) is None:
            # Resolve the Goal before any ledger write so a missing Goal
            # cannot leave a pending correction entry behind.
            raise ValueError(f"goal {args.goal_id} is not resolvable from registry {registry_path}")
    should_write = bool(not args.no_write_domain_state and (args.goal_id or args.ledger_path))
    origin_fields = (args.origin_request_id, args.origin_registry, args.origin_runtime_root)
    if any(origin_fields) and not all(origin_fields):
        raise ValueError("--origin-request-id requires --origin-registry and --origin-runtime-root")
    origin_registry: Path | None = None
    origin_runtime: Path | None = None
    if args.origin_request_id:
        from ...control_plane.content_digest import BARE_SHA256_PATTERN

        if not BARE_SHA256_PATTERN.fullmatch(args.origin_request_id):
            raise ValueError("invalid origin request id")
        origin_registry = Path(args.origin_registry).expanduser().resolve()
        origin_runtime = Path(args.origin_runtime_root).expanduser().resolve()
        if not args.goal_id or not args.claimed_by or not should_write:
            raise ValueError("--origin-request-id requires --goal-id and --claimed-by and ledger writes")
    ledger_path: Path | None = None
    prior_maintainer_corrections = None
    prior_row = None
    if should_write:
        ledger_path = _resolve_ledger_path(
            args.ledger_path,
            default_issue_fix_domain_state_ledger_path,
            project=args.project,
            goal_id=args.goal_id,
        )
        prior_reference = normalise_github_issue_reference(repo=args.repo, issue_ref=args.pr_ref, url=args.url)
        prior_row = read_issue_fix_pr_lifecycle_ledger_row(
            ledger_path,
            repo=str(prior_reference["repo"]),
            pr_ref=str(prior_reference["issue_ref"]),
        )
        prior_maintainer_corrections = issue_fix_maintainer_correction_entries(prior_row)
    origin = _resolve_origin_request(
        args,
        prior_row=prior_row,
        origin_registry=origin_registry,
        origin_runtime=origin_runtime,
        generated_at=generated_at,
    )
    payload: Payload = build_issue_fix_pr_lifecycle_monitor_packet(
        repo=args.repo,
        pr_ref=args.pr_ref,
        issue_ref=args.issue_ref,
        url=args.url,
        provider_payload=_optional_json_object(args.metadata_json),
        fetch_metadata=fetch_metadata,
        fetch_timeout_seconds=args.fetch_timeout_seconds,
        maintainer_correction_input=_optional_json_object(args.maintainer_correction_json),
        prior_maintainer_corrections=prior_maintainer_corrections,
        generated_at=generated_at,
    )
    pending_source_events = payload.pop("_maintainer_correction_source_events", None)
    if (
        fetch_metadata
        and payload.get("external_reads_performed") is True
        and args.goal_id
        and registry_path is not None
    ):
        from ...control_plane.agents.capability_memory import (
            record_verified_agent_capability_observation,
        )
        from ...history import load_registry
        from ...paths import resolve_runtime_root

        try:
            resolved_runtime = resolve_runtime_root(
                load_registry(registry_path),
                runtime_root_arg,
                registry_path=registry_path,
            )
            record_verified_agent_capability_observation(
                registry_path=registry_path,
                runtime_root=resolved_runtime,
                goal_id=args.goal_id,
                agent_id=args.claimed_by,
                available=("network", "external_evidence_poll"),
            )
        except Exception:  # noqa: BLE001 - optional host observation stays fail-open
            pass
    if origin is not None:
        payload["origin_request"] = origin
    if should_write and ledger_path is not None:
        upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
        observation = payload.get("observation")
        if args.goal_id and isinstance(observation, dict) and str(observation.get("state") or "").upper() == "MERGED":
            payload["rollout_event"] = append_pr_merge_rollout_event(
                payload=payload,
                goal_id=args.goal_id,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
            )
    elif not args.no_write_domain_state:
        _set_write_skipped_reason(payload, "goal_id_or_ledger_path_missing")
    if args.execute_transition:
        if registry_path is None or ledger_path is None:
            raise ValueError("--execute-transition requires a LoopX registry")
        _execute_pr_lifecycle_transition(
            args,
            payload=payload,
            registry_path=registry_path,
            runtime_root_arg=runtime_root_arg,
            ledger_path=ledger_path,
            pending_source_events=pending_source_events,
            generated_at=generated_at,
        )
    return payload, render_issue_fix_pr_lifecycle_monitor_markdown


def _resolve_outcome_delivery_evidence(
    args: argparse.Namespace,
    *,
    project: Path,
    feasibility_packet: Payload,
    feasibility_observation: Mapping[str, object],
    generated_at: str,
) -> tuple[Payload | None, Payload | None]:
    explicit_delivery = _optional_json_object(args.delivery_evidence_json)
    delivery_evidence: Payload | None
    if explicit_delivery is not None:
        compact_delivery: Payload = compact_issue_fix_delivery_evidence(explicit_delivery)
        requires_commit_verification = bool(
            compact_delivery.get("commit_ref")
            and (
                compact_delivery.get("validation_status") == "passed"
                or compact_delivery.get("outcome_status") == "completed"
            )
        )
        if requires_commit_verification and (args.repo_path or args.repository_ref or args.write_delivery_evidence):
            if not args.repo_path or not args.repository_ref:
                raise ValueError("passed or completed commit evidence writeback requires --repo-path and --repository-ref")
            repository_context = feasibility_observation.get("repository_context")
            revision = (
                str(repository_context.get("repository_revision") or "").strip()
                if isinstance(repository_context, dict)
                else ""
            )
            if not revision:
                raise ValueError(
                    "passed or completed commit evidence requires a pinned repository_revision in feasibility context"
                )
            repository_commit_evidence = verify_issue_fix_repository_commit_evidence(
                repo_path=args.repo_path,
                repo=args.repo,
                repository_revision=revision,
                commit_ref=str(compact_delivery["commit_ref"]),
                recovery_ref=args.repository_ref,
                verified_at=generated_at,
            )
            compact_delivery = compact_issue_fix_delivery_evidence(
                {**compact_delivery, "repository_commit_evidence": repository_commit_evidence}
            )
        delivery_evidence = compact_delivery
    else:
        existing_raw = feasibility_packet.get("delivery_evidence")
        delivery_evidence = existing_raw if isinstance(existing_raw, dict) else None

    if not args.write_delivery_evidence or delivery_evidence is None:
        return delivery_evidence, None

    existing_delivery = feasibility_packet.get("delivery_evidence")
    existing_compact = (
        compact_issue_fix_delivery_evidence(
            existing_delivery, allow_legacy_empty_containers=True
        )
        if isinstance(existing_delivery, dict)
        else None
    )
    if _comparable_delivery_evidence(existing_compact) == _comparable_delivery_evidence(delivery_evidence):
        if isinstance(existing_delivery, dict):
            delivery_evidence = existing_delivery
        write_result: Mapping[str, object] = {"write_performed": False, "status": "unchanged", "row_count": None}
    else:
        delivery_evidence = {**delivery_evidence, "recorded_at": generated_at}
        feasibility_packet["delivery_evidence"] = delivery_evidence
        write_result = upsert_issue_fix_feasibility_ledger_jsonl(
            default_issue_fix_feasibility_ledger_path(project=project, goal_id=args.goal_id),
            feasibility_packet,
        )
    domain_state_write: Payload = {
        "schema_version": "issue_fix_delivery_evidence_writeback_v0",
        "domain_pack": "issue_fix",
        "stream": "feasibility",
        "key": {"repo": args.repo, "issue_ref": args.issue_ref},
        "write_performed": write_result.get("write_performed") is True,
        "status": write_result.get("status"),
        "row_count": write_result.get("row_count"),
        "path_recorded": False,
    }
    return delivery_evidence, domain_state_write


def _apply_outcome_repository_memory_writeback(
    args: argparse.Namespace,
    *,
    payload: Payload,
    provider_path: str,
    generated_at: str,
) -> None:
    if not args.repo_path:
        raise ValueError("--write-repository-memory requires --repo-path for commit ancestry verification")
    raw_outcomes = payload.get("issue_fix_outcomes")
    outcome_case = raw_outcomes[0] if isinstance(raw_outcomes, list) and raw_outcomes else {}
    repository_context = outcome_case.get("repository_context") if isinstance(outcome_case, dict) else None
    revision = str(repository_context.get("revision") or "").strip() if isinstance(repository_context, dict) else ""
    if not revision:
        raise ValueError("--write-repository-memory requires a revision-pinned outcome")
    writeback = write_issue_fix_validated_outcome_memory(
        config=_load_json_object(provider_path),
        outcome_packet=payload,
        repository_revision=revision,
        repo_path=args.repo_path,
        observed_at=generated_at,
        execute=True,
    )
    payload["repository_memory_writeback"] = writeback
    payload["external_writes_performed"] = bool(writeback.get("external_writes_performed"))
    payload["ok"] = writeback.get("ok") is True
    source_contract = payload.get("source_contract")
    if isinstance(source_contract, dict):
        source_contract["repository_memory_writeback"] = "issue_fix_validated_outcome_memory_writeback_v0"
        source_contract["writes_external_provider"] = bool(writeback.get("external_writes_performed"))
    if payload["ok"] is not True:
        payload["error"] = (
            f"repository memory writeback {writeback.get('status')}: "
            f"{writeback.get('reason_code') or 'provider write failed'}"
        )


def _handle_outcome(args: argparse.Namespace, *, generated_at: str) -> tuple[Payload, Renderer]:
    if args.write_delivery_evidence and not args.delivery_evidence_json:
        raise ValueError("--write-delivery-evidence requires --delivery-evidence-json")
    if args.write_delivery_evidence and args.feasibility_json:
        raise ValueError(
            "--write-delivery-evidence uses the default feasibility domain-state row; do not combine it with --feasibility-json"
        )
    provider_path = args.repository_memory_provider_json or (
        default_repository_memory_provider_config_path() if args.write_repository_memory else None
    )
    if args.write_repository_memory and not provider_path:
        raise ValueError(
            "--write-repository-memory requires an explicit provider config "
            "or LOOPX_ISSUE_FIX_REPOSITORY_MEMORY_PROVIDER_CONFIG"
        )
    _ensure_single_stdin(
        args.feasibility_json,
        args.pr_lifecycle_json,
        args.delivery_evidence_json,
        scope="outcome JSON",
    )
    project = Path(args.project).expanduser()
    feasibility_packet: Payload = (
        _load_json_object(args.feasibility_json)
        if args.feasibility_json
        else _load_jsonl_row(
            default_issue_fix_feasibility_ledger_path(project=project, goal_id=args.goal_id),
            repo=args.repo,
            ref_field="issue_ref",
            ref_value=args.issue_ref,
        )
    )
    feasibility_observation = feasibility_packet.get("observation")
    if not isinstance(feasibility_observation, dict) or (
        str(feasibility_observation.get("repo") or "").strip() != args.repo
        or str(feasibility_observation.get("issue_ref") or "").strip() != args.issue_ref
    ):
        raise ValueError("--repo/--issue-ref must match the selected feasibility packet")
    pr_lifecycle_packet: Payload | None = None
    if args.pr_lifecycle_json:
        pr_lifecycle_packet = _load_json_object(args.pr_lifecycle_json)
    elif args.pr_ref:
        pr_lifecycle_packet = _load_jsonl_row(
            default_issue_fix_domain_state_ledger_path(project=project, goal_id=args.goal_id),
            repo=args.repo,
            ref_field="pr_ref",
            ref_value=args.pr_ref,
        )
    if pr_lifecycle_packet is not None and args.pr_ref:
        lifecycle_observation = pr_lifecycle_packet.get("observation")
        if not isinstance(lifecycle_observation, dict) or str(lifecycle_observation.get("pr_ref") or "").strip() != args.pr_ref:
            raise ValueError("--pr-ref must match the selected PR lifecycle packet")
    delivery_evidence, domain_state_write = _resolve_outcome_delivery_evidence(
        args,
        project=project,
        feasibility_packet=feasibility_packet,
        feasibility_observation=feasibility_observation,
        generated_at=generated_at,
    )
    allow_persisted_empty = not args.delivery_evidence_json or (
        isinstance(domain_state_write, dict)
        and domain_state_write.get("status") == "unchanged"
    )
    payload: Payload = build_issue_fix_outcome_projection(
        goal_id=args.goal_id,
        feasibility_packet=feasibility_packet,
        pr_lifecycle_packet=pr_lifecycle_packet,
        delivery_evidence_input=delivery_evidence,
        agent_id=args.agent_id,
        generated_at=generated_at,
        allow_legacy_empty_containers=allow_persisted_empty,
    )
    if args.write_repository_memory and provider_path is not None:
        _apply_outcome_repository_memory_writeback(
            args,
            payload=payload,
            provider_path=str(provider_path),
            generated_at=generated_at,
        )
    if domain_state_write is not None:
        payload["domain_state_write"] = domain_state_write
        source_contract = payload.get("source_contract")
        if isinstance(source_contract, dict):
            source_contract["writes_source_state"] = True
    return payload, render_issue_fix_outcome_projection_markdown


def _handle_metrics(args: argparse.Namespace, *, generated_at: str) -> tuple[Payload, Renderer]:
    _ensure_single_stdin(
        args.repository_baseline_json,
        args.repository_current_json,
        args.supplement_json,
        scope="metrics JSON",
    )
    project = Path(args.project).expanduser()
    feasibility_path = _resolve_ledger_path(
        args.feasibility_ledger,
        default_issue_fix_feasibility_ledger_path,
        project=project,
        goal_id=args.goal_id,
    )
    lifecycle_path = _resolve_ledger_path(
        args.pr_lifecycle_ledger,
        default_issue_fix_domain_state_ledger_path,
        project=project,
        goal_id=args.goal_id,
    )
    payload = build_issue_fix_metrics_projection(
        goal_id=args.goal_id,
        repo=args.repo,
        baseline_snapshot=_load_json_object(args.repository_baseline_json),
        current_snapshot=_load_json_object(args.repository_current_json),
        feasibility_rows=_load_jsonl_rows(feasibility_path),
        pr_lifecycle_rows=_load_jsonl_rows(lifecycle_path),
        supplement_input=_optional_json_object(args.supplement_json),
        generated_at=generated_at,
    )
    return payload, render_issue_fix_metrics_projection_markdown


def _handle_repository_snapshot(args: argparse.Namespace, *, generated_at: str) -> tuple[Payload, Renderer]:
    if not args.fetch_public_github:
        raise ValueError("repository-snapshot requires --fetch-public-github")
    _ensure_single_stdin(args.repository_baseline_json, args.supplement_json, scope="repository-snapshot JSON")
    project = Path(args.project).expanduser()
    payload: Payload = collect_public_github_repository_snapshot(
        repo=args.repo,
        baseline_snapshot=_load_json_object(args.repository_baseline_json),
        feasibility_rows=_load_jsonl_rows(
            default_issue_fix_feasibility_ledger_path(project=project, goal_id=args.goal_id)
        ),
        pr_lifecycle_rows=_load_jsonl_rows(
            default_issue_fix_domain_state_ledger_path(project=project, goal_id=args.goal_id)
        ),
        metrics_supplement=_optional_json_object(args.supplement_json),
        generated_at=generated_at,
        timeout_seconds=args.fetch_timeout_seconds,
    )
    if args.retain_material_snapshot:
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("repository snapshot payload is missing snapshot")
        write_result = retain_issue_fix_repository_snapshot_jsonl(
            default_issue_fix_repository_snapshot_ledger_path(project=project, goal_id=args.goal_id),
            repository_snapshot_record(snapshot),
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
        source_contract = payload.get("source_contract")
        if isinstance(source_contract, dict):
            source_contract["writes_source_state"] = True
    return payload, render_repository_snapshot_markdown


_PR_GATE_RECONCILE_COMMANDS: dict[str, tuple[Callable[..., Payload], Renderer]] = {
    "pr-gate-reconcile": (
        pr_gate_reconcile_cli.build_pr_gate_reconciliation_from_args,
        pr_gate_reconcile_cli.render_issue_fix_pr_gate_reconciliation_markdown,
    ),
    "pr-review-ack": (
        pr_gate_reconcile_cli.build_pr_review_ack_from_args,
        pr_gate_reconcile_cli.render_issue_fix_pr_review_ack_markdown,
    ),
    "pr-review-reconcile": (
        pr_gate_reconcile_cli.build_pr_review_reconciliation_from_args,
        pr_gate_reconcile_cli.render_issue_fix_pr_review_reconciliation_markdown,
    ),
    "pr-review-reconcile-acked": (
        pr_gate_reconcile_cli.build_acked_pr_review_reconciliation_from_args,
        pr_gate_reconcile_cli.render_issue_fix_acked_pr_review_reconciliation_markdown,
    ),
}

_ERROR_RENDERERS: dict[str, Renderer] = {
    "repository-memory-sync": render_issue_fix_repository_memory_sync_markdown,
    "promote-discovered-issue": render_issue_fix_discovered_issue_promotion_markdown,
    "workflow-plan": render_issue_fix_workflow_plan_markdown,
    "feasibility": render_issue_fix_feasibility_markdown,
    "pr-lifecycle": render_issue_fix_pr_lifecycle_monitor_markdown,
    "pr-gate-reconcile": render_issue_fix_pr_lifecycle_monitor_markdown,
    "outcome": render_issue_fix_outcome_projection_markdown,
    "metrics": render_issue_fix_metrics_projection_markdown,
    "metrics-supplement": render_issue_fix_metrics_supplement_markdown,
    "repository-snapshot": render_repository_snapshot_markdown,
}


def handle_issue_fix_command(
    args: argparse.Namespace,
    *,
    registry_path: Path | None = None,
    runtime_root_arg: str | None = None,
    reviewer_provider_hooks_factory: IssueFixReviewerProviderHooksFactory | None = None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int:
    command = getattr(args, "issue_fix_command", None)
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
        elif command == "repository-memory-sync":
            payload, renderer = _handle_repository_memory_sync(args, generated_at=generated_at)
        elif command == "promote-discovered-issue":
            scopes, resolved = _goal_boundary_authority_projection(
                registry_path=registry_path,
                goal_id=args.goal_id,
            )
            payload = build_discovered_issue_promotion_from_cli_args(
                args,
                load_json_object=_load_json_object,
                boundary_authority_scopes=scopes,
                boundary_authority_resolved=resolved,
                generated_at=generated_at,
            )
            renderer = render_issue_fix_discovered_issue_promotion_markdown
        elif command == "workflow-plan":
            payload, renderer = _handle_workflow_plan(args, generated_at=generated_at)
        elif command == "feasibility":
            payload, renderer = _handle_feasibility(args, registry_path=registry_path, generated_at=generated_at)
        elif command in _PR_GATE_RECONCILE_COMMANDS:
            builder, renderer = _PR_GATE_RECONCILE_COMMANDS[command]
            payload = builder(args, registry_path, runtime_root_arg, generated_at)
        elif command == "pr-lifecycle":
            payload, renderer = _handle_pr_lifecycle(
                args,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                generated_at=generated_at,
            )
        elif command == "outcome":
            payload, renderer = _handle_outcome(args, generated_at=generated_at)
        elif command == "metrics":
            payload, renderer = _handle_metrics(args, generated_at=generated_at)
        elif command == "metrics-supplement":
            payload = build_issue_fix_metrics_supplement_from_args(
                args,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                generated_at=generated_at,
                load_json_object=_load_json_object,
                load_jsonl_rows=_load_jsonl_rows,
            )
            renderer = render_issue_fix_metrics_supplement_markdown
        elif command == "repository-snapshot":
            payload, renderer = _handle_repository_snapshot(args, generated_at=generated_at)
        elif command == "acceptance-fixture":
            payload = build_issue_fix_acceptance_fixture_packet(
                repo=args.repo,
                issue_ref=args.issue_ref,
                url=args.url,
                generated_at=generated_at,
            )
            renderer = render_issue_fix_acceptance_loop_markdown
        elif command == "repo-branch-fixture":
            payload = build_issue_fix_repo_branch_fixture_packet(
                repo=args.repo,
                issue_ref=args.issue_ref,
                url=args.url,
                generated_at=generated_at,
            )
            renderer = render_issue_fix_acceptance_loop_markdown
        elif command == "caller-repo-branch":
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
        payload = {"ok": False, "mode": "issue-fix", "error": str(exc)}
        renderer = (
            reviewer_renderer(command)
            if command in REVIEWER_COMMANDS
            else _ERROR_RENDERERS.get(str(command or ""), render_issue_fix_acceptance_loop_markdown)
        )
    payload.pop("origin_request", None)
    print_payload(payload, output_format(args), renderer)
    return 0 if payload.get("ok") else 1
