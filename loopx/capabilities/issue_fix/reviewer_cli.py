from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...control_plane.reward_memory import reward_memory_goal_policy
from ...domain_packs.issue_fix import (
    default_issue_fix_domain_state_ledger_path,
    persist_issue_fix_reviewer_notification_state,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from ..reward_memory.experiment import (
    resolve_reward_memory_experiment,
    resolve_reward_memory_surface_config,
)
from .cli_input import load_json_object, load_jsonl_row
from .metadata_preview import normalise_github_issue_reference
from .pr_lifecycle import build_issue_fix_pr_lifecycle_monitor_packet
from .provider_hooks import (
    IssueFixReviewerProviderHooks,
    IssueFixReviewerProviderHooksFactory,
)
from .reviewer_notification import (
    load_goal_reviewer_notification_sinks_input,
    reviewer_notification_legacy_queue_from_state,
    reviewer_notification_queue_from_state,
    reviewer_notification_receipts_from_state,
    with_reviewer_notification_state,
)
from .reviewer_notification_drain import (
    drain_issue_fix_reviewer_notification_queue,
)
from .reviewer_recommendation import (
    build_issue_fix_reviewer_recommendation_packet,
    render_issue_fix_reviewer_recommendation_markdown,
)
from .reviewer_request import (
    build_issue_fix_reviewer_request_packet,
    render_issue_fix_reviewer_request_markdown,
)
from ...control_plane.runtime.goal_project_route import resolve_goal_project_route


AddFormat = Callable[[argparse.ArgumentParser], None]
AddGeneratedAt = Callable[..., None]
Renderer = Callable[[dict[str, object]], str]
REVIEWER_COMMANDS = frozenset(
    {
        "reviewer-plan",
        "reviewer-request",
        "reviewer-notification-drain",
        "reviewer-feedback-inbox",
    }
)


def register_issue_fix_reviewer_commands(
    issue_fix_sub: argparse._SubParsersAction,
    *,
    add_subcommand_format: AddFormat,
    add_generated_at_arg: AddGeneratedAt,
) -> None:
    reviewer_parser = issue_fix_sub.add_parser(
        "reviewer-plan",
        help=(
            "Recommend reviewers from caller-approved repository ownership "
            "evidence without requesting external review."
        ),
    )
    add_subcommand_format(reviewer_parser)
    reviewer_parser.add_argument(
        "--repo-path",
        required=True,
        help=(
            "Caller-approved local git repository. The public packet never "
            "records this path."
        ),
    )
    reviewer_parser.add_argument(
        "--repo",
        default="approved_local_repo",
        help="Public-safe repository label stored in the recommendation packet.",
    )
    reviewer_parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help=(
            "Repo-relative changed file. Repeat for multiple files. When omitted "
            "with --execute, derive files from --base-ref...HEAD."
        ),
    )
    reviewer_parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="Base git ref used to derive changed files when none are explicit.",
    )
    reviewer_parser.add_argument(
        "--history-limit",
        type=int,
        default=40,
        help="Maximum changed-path git history rows to inspect per file.",
    )
    reviewer_parser.add_argument(
        "--max-candidates",
        type=int,
        default=5,
        help="Maximum ranked reviewer candidates to return.",
    )
    reviewer_parser.add_argument(
        "--exclude-reviewer",
        action="append",
        default=[],
        help=(
            "GitHub handle to exclude, normally the PR author or unavailable "
            "reviewer. Repeat for multiple handles."
        ),
    )
    reviewer_parser.add_argument(
        "--exclude-author-name",
        action="append",
        default=[],
        help=(
            "Git author display-name alias to exclude when it cannot be resolved "
            "to the PR author handle. Repeat for multiple aliases."
        ),
    )
    reviewer_parser.add_argument(
        "--identity-map-json",
        help=(
            "Optional public-safe JSON object mapping verified git display names "
            "to GitHub handles; raw mapping is not copied into output."
        ),
    )
    reviewer_parser.add_argument(
        "--reviewer-sources-json",
        help=(
            "Optional public-safe issue_fix_reviewer_sources_input_v0 JSON with "
            "repository-declared path routes, primary/fallback handles, trust, "
            "freshness, observation time, and source references."
        ),
    )
    reviewer_parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Inspect only the caller-approved local repository. This still "
            "does not request review or perform any external write."
        ),
    )
    add_generated_at_arg(reviewer_parser, artifact="the recommendation packet")

    reviewer_request_parser = issue_fix_sub.add_parser(
        "reviewer-request",
        help=(
            "Select the top requestable non-author reviewer and, with explicit "
            "external-write authority, verify a formal request or its "
            "permission-only comment fallback."
        ),
    )
    add_subcommand_format(reviewer_request_parser)
    reviewer_request_parser.add_argument(
        "--url",
        required=True,
        help="Canonical public GitHub pull request URL.",
    )
    reviewer_request_parser.add_argument(
        "--repo-path",
        required=True,
        help="Caller-approved local git repository; never copied into output.",
    )
    reviewer_request_parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Repo-relative changed file; repeat or derive from --base-ref...HEAD.",
    )
    reviewer_request_parser.add_argument("--base-ref", default="origin/main")
    reviewer_request_parser.add_argument("--history-limit", type=int, default=40)
    reviewer_request_parser.add_argument("--max-candidates", type=int, default=5)
    reviewer_request_parser.add_argument(
        "--max-reviewers",
        type=int,
        default=1,
        help="Bounded reviewer request count; default one, maximum three.",
    )
    reviewer_request_parser.add_argument(
        "--exclude-reviewer", action="append", default=[]
    )
    reviewer_request_parser.add_argument(
        "--exclude-author-name", action="append", default=[]
    )
    reviewer_request_parser.add_argument(
        "--identity-map-json",
        help=(
            "Optional public-safe JSON object mapping human-verified git display "
            "names to GitHub handles."
        ),
    )
    reviewer_request_parser.add_argument(
        "--reviewer-sources-json",
        help=(
            "Optional public-safe issue_fix_reviewer_sources_input_v0 JSON; "
            "the selected candidate still requires live author exclusion and "
            "external-write authority. Source freshness must include observation time."
        ),
    )
    reviewer_request_parser.add_argument(
        "--metadata-json",
        help=(
            "Optional compact PR metadata as an inline JSON object, file path, or "
            "'-' for stdin in a no-write preview. Live execute mode fetches and "
            "verifies GitHub state instead."
        ),
    )
    reviewer_request_parser.add_argument(
        "--pr-description-build-json",
        help=(
            "Compact issue_fix_pr_description_build_v0 JSON proving the PR "
            "description recall path executed before notification writes."
        ),
    )
    reviewer_request_parser.add_argument(
        "--notification-sinks-json",
        help=(
            "Optional local-private issue_fix_reviewer_notification_sinks_input_v0 "
            "JSON. Destination, bot profile, and member IDs are consumed locally "
            "and never copied into the public result."
        ),
    )
    reviewer_request_parser.add_argument(
        "--goal-id",
        help=(
            "Optional connected goal whose registered local-private default sink "
            "and existing PR lifecycle receipts should be reused."
        ),
    )
    reviewer_request_parser.add_argument(
        "--agent-id",
        help=(
            "Registered caller agent used to resolve an agent-scoped Reward "
            "Memory experiment; never inferred from another peer."
        ),
    )
    reviewer_request_parser.add_argument(
        "--reviewer-summary",
        help=(
            "Caller/model-authored concise Chinese PR summary. A configured "
            "Reward Memory reviewer-artifact gate verifies it before secondary send."
        ),
    )
    reviewer_request_parser.add_argument(
        "--reviewer-summary-reasoning",
        help=(
            "Compact caller/model reasoning for applying the recalled reviewer "
            "artifact policy to the current PR."
        ),
    )
    reviewer_request_parser.add_argument(
        "--project",
        default=None,
        help="Project root used for goal-default sink config and issue_fix domain state.",
    )
    reviewer_request_parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Assert external-review-request authority, try formal GitHub review, "
            "fall back to one reviewer-tagging comment only on permission denial, "
            "and verify the result."
        ),
    )
    add_generated_at_arg(reviewer_request_parser, artifact="the request packet")

    reviewer_drain_parser = issue_fix_sub.add_parser(
        "reviewer-notification-drain",
        help=(
            "Drain one bounded batch of due reviewer notifications from the "
            "grouped review-required state bucket, one PR per message."
        ),
    )
    add_subcommand_format(reviewer_drain_parser)
    reviewer_drain_parser.add_argument("--goal-id", required=True)
    reviewer_drain_parser.add_argument("--project")
    reviewer_drain_parser.add_argument("--limit", type=int, default=20)
    reviewer_drain_parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Verify live PR state, deliver due messages, and persist semantic "
            "receipts or stale-queue cancellation."
        ),
    )
    add_generated_at_arg(
        reviewer_drain_parser,
        artifact="the grouped reviewer notification drain packet",
    )

    reviewer_feedback_parser = issue_fix_sub.add_parser(
        "reviewer-feedback-inbox",
        help=(
            "Drain or acknowledge the generic Lark event inbox bound to a "
            "configured issue-fix reviewer group."
        ),
    )
    add_subcommand_format(reviewer_feedback_parser)
    reviewer_feedback_parser.add_argument("--goal-id", required=True)
    reviewer_feedback_parser.add_argument("--project")
    reviewer_feedback_parser.add_argument("--limit", type=int, default=20)
    reviewer_feedback_parser.add_argument(
        "--message-id",
        action="append",
        default=[],
        help="Acknowledge one drained message after its feedback is written back.",
    )
    reviewer_feedback_parser.add_argument(
        "--execute",
        action="store_true",
        help="Persist acknowledgement; draining remains read-only.",
    )
    add_generated_at_arg(
        reviewer_feedback_parser,
        artifact="the reviewer feedback inbox projection",
    )


def _load_goal_for_project(
    *,
    registry_path: Path | None,
    goal_id: str,
    project: str | Path | None,
) -> tuple[dict[str, Any], Path]:
    if registry_path is None:
        raise ValueError("goal-scoped reviewer notification requires a registry")
    goal, requested_project, _ = resolve_goal_project_route(
        registry_path=registry_path,
        goal_id=goal_id,
        project_override=project,
    )
    return goal, requested_project


def _materialize_goal_reviewer_notification_lifecycle(
    *,
    ledger_path: str | Path,
    url: str,
    generated_at: str | None = None,
    provider_payload: dict[str, Any] | None = None,
    fetch_metadata: bool = False,
) -> dict[str, Any]:
    packet = build_issue_fix_pr_lifecycle_monitor_packet(
        url=url,
        provider_payload=provider_payload,
        fetch_metadata=fetch_metadata,
        generated_at=generated_at,
    )
    upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, packet)
    return packet


def reviewer_renderer(command: str | None) -> Renderer:
    if command == "reviewer-plan":
        return render_issue_fix_reviewer_recommendation_markdown
    if command == "reviewer-feedback-inbox":
        return render_issue_fix_reviewer_feedback_inbox_markdown
    if command == "reviewer-notification-drain":
        return render_issue_fix_reviewer_notification_drain_markdown
    return render_issue_fix_reviewer_request_markdown


def render_issue_fix_reviewer_feedback_inbox_markdown(
    packet: dict[str, object],
) -> str:
    return (
        "\n".join(
            [
                "# Issue-fix Reviewer Feedback Inbox",
                "",
                f"- enabled: {packet.get('enabled')}",
                f"- pending_count: {packet.get('pending_count')}",
                f"- write_performed: {packet.get('write_performed')}",
            ]
        ).rstrip()
        + "\n"
    )


def render_issue_fix_reviewer_notification_drain_markdown(
    packet: dict[str, object],
) -> str:
    return (
        "\n".join(
            [
                "# Issue-fix Reviewer Notification Drain",
                "",
                f"- status: {packet.get('status')}",
                f"- due_pr_count: {packet.get('due_pr_count')}",
                f"- verified_pr_count: {packet.get('verified_pr_count')}",
                f"- cancelled_pr_count: {packet.get('cancelled_pr_count')}",
                f"- blocked_pr_count: {packet.get('blocked_pr_count')}",
                f"- remaining_due_pr_count: {packet.get('remaining_due_pr_count')}",
            ]
        ).rstrip()
        + "\n"
    )


def handle_issue_fix_reviewer_command(
    args: argparse.Namespace,
    *,
    registry_path: Path | None,
    generated_at: str,
    delivery_observed_at: str,
    reviewer_provider_hooks_factory: (
        IssueFixReviewerProviderHooksFactory | None
    ) = None,
) -> tuple[dict[str, Any], Renderer] | None:
    reviewer_provider_hooks: IssueFixReviewerProviderHooks | None = None

    def require_reviewer_provider_hooks() -> IssueFixReviewerProviderHooks:
        nonlocal reviewer_provider_hooks
        if reviewer_provider_hooks is None:
            if reviewer_provider_hooks_factory is None:
                raise ValueError(
                    "configured reviewer workflow requires an enabled provider"
                )
            reviewer_provider_hooks = reviewer_provider_hooks_factory()
        return reviewer_provider_hooks

    def lark_sink_configured(value: Mapping[str, Any] | None) -> bool:
        return bool(
            value
            and any(
                isinstance(sink, Mapping) and sink.get("sink_kind") == "lark_chat"
                for sink in (value.get("sinks") or [])
            )
        )

    if args.issue_fix_command == "reviewer-plan":
        payload = build_issue_fix_reviewer_recommendation_packet(
            repo_path=args.repo_path,
            repo=args.repo,
            changed_files=args.changed_file,
            base_ref=args.base_ref,
            history_limit=args.history_limit,
            max_candidates=args.max_candidates,
            exclude_reviewers=args.exclude_reviewer,
            exclude_author_names=args.exclude_author_name,
            resolved_identities=(
                load_json_object(args.identity_map_json)
                if args.identity_map_json
                else None
            ),
            reviewer_sources_input=(
                load_json_object(args.reviewer_sources_json)
                if args.reviewer_sources_json
                else None
            ),
            execute=args.execute,
            generated_at=generated_at,
        )
        return payload, render_issue_fix_reviewer_recommendation_markdown

    if args.issue_fix_command == "reviewer-feedback-inbox":
        goal, requested_project = _load_goal_for_project(
            registry_path=registry_path,
            goal_id=args.goal_id,
            project=args.project,
        )
        sinks_input = load_goal_reviewer_notification_sinks_input(
            goal=goal,
            project=requested_project,
        )
        lark_group_configured = lark_sink_configured(sinks_input)
        if not lark_group_configured:
            payload = {
                "ok": True,
                "schema_version": "issue_fix_reviewer_feedback_inbox_v0",
                "mode": "issue-fix-reviewer-feedback-inbox",
                "enabled": False,
                "configured_reviewer_group": False,
                "pending_count": 0,
                "items": [],
                "external_reads_performed": False,
            }
        else:
            inbox_hooks = require_reviewer_provider_hooks()
            config_ref = str(
                sinks_input.get("feedback_inbox_config")
                or ".loopx/config/lark/event-inbox.json"
            )
            if args.message_id:
                payload = inbox_hooks.acknowledge(
                    project=requested_project,
                    config_path=config_ref,
                    message_ids=args.message_id,
                    execute=args.execute,
                )
            else:
                payload = inbox_hooks.inspect(
                    project=requested_project,
                    config_path=config_ref,
                    limit=args.limit,
                )
            payload = {
                **payload,
                "mode": "issue-fix-reviewer-feedback-inbox",
                "configured_reviewer_group": True,
                "source": "goal_default_lark_event_inbox",
                "extension_activation": dict(inbox_hooks.activation),
            }
        return payload, render_issue_fix_reviewer_feedback_inbox_markdown

    if args.issue_fix_command == "reviewer-notification-drain":
        goal, requested_project = _load_goal_for_project(
            registry_path=registry_path,
            goal_id=args.goal_id,
            project=args.project,
        )
        sinks_input = load_goal_reviewer_notification_sinks_input(
            goal=goal,
            project=requested_project,
        )
        if sinks_input is None:
            payload = {
                "ok": True,
                "schema_version": "issue_fix_reviewer_notification_drain_v0",
                "mode": "issue-fix-reviewer-notification-drain",
                "status": "not_configured",
                "due_pr_count": 0,
                "verified_pr_count": 0,
                "cancelled_pr_count": 0,
                "blocked_pr_count": 0,
                "remaining_due_pr_count": 0,
                "has_more_due": False,
                "external_reads_performed": False,
                "external_writes_performed": False,
                "state_write_performed": False,
            }
        else:
            lifecycle_path = default_issue_fix_domain_state_ledger_path(
                project=requested_project,
                goal_id=args.goal_id,
            )
            lark_group_configured = lark_sink_configured(sinks_input)
            semantic_history_matcher = None
            if lark_group_configured:
                inbox_hooks = require_reviewer_provider_hooks()
                default_config_ref = str(
                    sinks_input.get("feedback_inbox_config")
                    or ".loopx/config/lark/event-inbox.json"
                )
                lark_sink_count = sum(
                    1
                    for sink in (sinks_input.get("sinks") or [])
                    if isinstance(sink, Mapping)
                    and sink.get("sink_kind") == "lark_chat"
                )

                def semantic_history_matcher(
                    permalink: str, sink: Mapping[str, Any]
                ) -> bool | None:
                    sink_config_ref = str(
                        sink.get("feedback_inbox_config") or ""
                    ).strip()
                    if not sink_config_ref and lark_sink_count != 1:
                        return None
                    return inbox_hooks.contains_text(
                        project=requested_project,
                        config_path=sink_config_ref or default_config_ref,
                        text=permalink,
                    )

            payload = drain_issue_fix_reviewer_notification_queue(
                ledger_path=lifecycle_path,
                sinks_input=sinks_input,
                execute=args.execute,
                delivery_observed_at=delivery_observed_at,
                limit=args.limit,
                semantic_history_matcher=semantic_history_matcher,
                sink_adapters=(
                    {"lark_chat": inbox_hooks.notification_adapter}
                    if lark_group_configured
                    else None
                ),
            )
            if lark_group_configured:
                payload["extension_activation"] = dict(inbox_hooks.activation)
            payload["notification_source"] = "goal_default"
        return payload, render_issue_fix_reviewer_notification_drain_markdown

    if args.issue_fix_command != "reviewer-request":
        return None
    if args.execute and args.metadata_json:
        raise ValueError(
            "reviewer-request execute mode must fetch live PR metadata; "
            "--metadata-json is preview-only"
        )
    notification_sinks_input = (
        load_json_object(args.notification_sinks_json)
        if args.notification_sinks_json
        else None
    )
    notification_sinks_source = (
        "explicit" if notification_sinks_input is not None else "not_configured"
    )
    notification_lifecycle_packet: dict[str, Any] | None = None
    notification_lifecycle_path: Path | None = None
    notification_lifecycle_materialized = False
    existing_queued_receipts: list[dict[str, Any]] = []
    reviewer_artifact_required = False
    reviewer_artifact_reward_memory: dict[str, Any] | None = None
    reviewer_notification_reward_memory: dict[str, Any] | None = None
    reward_memory_experiment_status = "not_configured"
    semantic_history_status = "not_checked"
    legacy_queue_blocker: str | None = None
    if args.goal_id:
        goal, requested_project = _load_goal_for_project(
            registry_path=registry_path,
            goal_id=args.goal_id,
            project=args.project,
        )
        if notification_sinks_input is None:
            notification_sinks_input = load_goal_reviewer_notification_sinks_input(
                goal=goal,
                project=requested_project,
            )
        if notification_sinks_input is not None:
            if notification_sinks_source == "not_configured":
                notification_sinks_source = "goal_default"
            reference = normalise_github_issue_reference(
                repo="public_repo_fixture",
                issue_ref="pull_request_fixture",
                url=args.url,
            )
            if reference.get("kind") != "pull_request":
                raise ValueError(
                    "goal-scoped reviewer notification requires a GitHub PR"
                )
            notification_lifecycle_path = default_issue_fix_domain_state_ledger_path(
                project=requested_project,
                goal_id=args.goal_id,
            )
            try:
                notification_lifecycle_packet = load_jsonl_row(
                    notification_lifecycle_path,
                    repo=str(reference["repo"]),
                    ref_field="pr_ref",
                    ref_value=f"pull_{int(reference['number'])}",
                )
            except ValueError:
                if args.execute:
                    try:
                        notification_lifecycle_packet = (
                            _materialize_goal_reviewer_notification_lifecycle(
                                ledger_path=notification_lifecycle_path,
                                url=args.url,
                                generated_at=generated_at,
                                fetch_metadata=True,
                            )
                        )
                    except (OSError, RuntimeError, ValueError):
                        raise ValueError(
                            "goal-scoped reviewer notification could not "
                            "materialize a verified PR lifecycle row before "
                            "external send"
                        ) from None
                    notification_lifecycle_materialized = True
            if reviewer_notification_legacy_queue_from_state(
                notification_lifecycle_packet
            ):
                legacy_queue_blocker = (
                    "reviewer_notification_queue_v1_migration_required"
                )
                notification_sinks_input = None
                semantic_history_status = "blocked_v1_migration_required"
            else:
                notification_sinks_input = with_reviewer_notification_state(
                    notification_sinks_input,
                    reviewer_notification_receipts_from_state(
                        notification_lifecycle_packet
                    ),
                    reviewer_notification_queue_from_state(
                        notification_lifecycle_packet
                    ),
                )
                existing_queued_receipts = reviewer_notification_queue_from_state(
                    notification_lifecycle_packet
                )
                lark_group_configured = lark_sink_configured(
                    notification_sinks_input
                )
                if lark_group_configured:
                    inbox_hooks = require_reviewer_provider_hooks()
                    config_ref = str(
                        notification_sinks_input.get("feedback_inbox_config")
                        or ".loopx/config/lark/event-inbox.json"
                    )
                    try:
                        history_match = inbox_hooks.contains_text(
                            project=requested_project,
                            config_path=config_ref,
                            text=str(reference["permalink"]),
                        )
                    except (OSError, ValueError):
                        semantic_history_status = "unavailable"
                    else:
                        semantic_history_status = (
                            "matched" if history_match else "no_match"
                        )
                        if history_match:
                            notification_sinks_input = {
                                **notification_sinks_input,
                                "_semantic_history_pr_refs": [
                                    str(reference["permalink"])
                                ],
                            }
                else:
                    semantic_history_status = "not_applicable"
        reward_policy = reward_memory_goal_policy(goal, agent_id=args.agent_id)
        reviewer_artifact_required = bool(notification_sinks_input) and (
            reward_policy["enabled"] is True
        )
        if reward_policy["enabled"] is True:
            reward_memory_experiment_status = "agent_id_required"
            if args.agent_id:
                candidate_registries = list(
                    dict.fromkeys(
                        path
                        for path in (
                            registry_path,
                            requested_project / ".loopx" / "registry.json",
                        )
                        if path is not None
                    )
                )
                experiment_status: dict[str, Any] | None = None
                experiment_config: dict[str, Any] | None = None
                for candidate_registry in candidate_registries:
                    try:
                        experiment_status, experiment_config = (
                            resolve_reward_memory_experiment(
                                registry_path=candidate_registry,
                                goal_id=args.goal_id,
                                agent_id=args.agent_id,
                            )
                        )
                    except (OSError, ValueError):
                        continue
                    break
                reward_memory_experiment_status = str(
                    (experiment_status or {}).get("status") or "unavailable"
                )
                if experiment_config is not None:
                    try:
                        reviewer_route = resolve_reward_memory_surface_config(
                            experiment_config,
                            "reviewer_artifact.summary",
                        )
                    except ValueError:
                        reviewer_route = None
                    if reviewer_route is not None:
                        reviewer_artifact_reward_memory = {
                            "config": experiment_config,
                            "reviewer_summary": args.reviewer_summary,
                            "reasoning_summary": args.reviewer_summary_reasoning,
                            "observed_at": generated_at,
                        }
                    try:
                        notification_route = resolve_reward_memory_surface_config(
                            experiment_config,
                            "reviewer_notification.before_send",
                        )
                    except ValueError:
                        notification_route = None
                    if notification_route is not None:
                        reviewer_notification_reward_memory = {
                            "config": experiment_config,
                            "observed_at": generated_at,
                        }
    notification_sink_adapters = None
    if lark_sink_configured(notification_sinks_input):
        notification_sink_adapters = {
            "lark_chat": require_reviewer_provider_hooks().notification_adapter
        }
    payload = build_issue_fix_reviewer_request_packet(
        repo_path=args.repo_path,
        url=args.url,
        changed_files=args.changed_file,
        base_ref=args.base_ref,
        history_limit=args.history_limit,
        max_candidates=args.max_candidates,
        max_reviewers=args.max_reviewers,
        exclude_reviewers=args.exclude_reviewer,
        exclude_author_names=args.exclude_author_name,
        resolved_identities=(
            load_json_object(args.identity_map_json) if args.identity_map_json else None
        ),
        reviewer_sources_input=(
            load_json_object(args.reviewer_sources_json)
            if args.reviewer_sources_json
            else None
        ),
        notification_sinks_input=notification_sinks_input,
        notification_sink_adapters=notification_sink_adapters,
        reviewer_artifact_reward_memory=reviewer_artifact_reward_memory,
        reviewer_notification_reward_memory=reviewer_notification_reward_memory,
        reviewer_artifact_required=reviewer_artifact_required,
        pr_description_build=(
            load_json_object(args.pr_description_build_json)
            if getattr(args, "pr_description_build_json", None)
            else None
        ),
        provider_payload=(
            load_json_object(args.metadata_json) if args.metadata_json else None
        ),
        execute=args.execute,
        generated_at=generated_at,
        notification_delivery_observed_at=delivery_observed_at,
    )
    payload["secondary_notification_source"] = notification_sinks_source
    payload["reward_memory_reviewer_artifact_required"] = reviewer_artifact_required
    payload["reward_memory_experiment_status"] = reward_memory_experiment_status
    payload["secondary_notification_lifecycle_materialized"] = (
        notification_lifecycle_materialized
    )
    payload["secondary_notification_semantic_history_status"] = semantic_history_status
    if reviewer_provider_hooks is not None:
        payload["extension_activation"] = dict(reviewer_provider_hooks.activation)
    if legacy_queue_blocker is not None:
        payload["secondary_notification_blocker"] = legacy_queue_blocker
    payload["secondary_notification_receipts_persisted"] = False
    payload["secondary_notification_queue_persisted"] = False
    payload["secondary_notification_queue_reconciled"] = False
    payload["secondary_notification_queue_cancelled_count"] = 0
    payload["secondary_notification_state_persisted"] = False
    secondary = payload.get("secondary_notifications")
    secondary_receipts = (
        secondary.get("receipts") if isinstance(secondary, dict) else []
    )
    new_receipts: list[str] = [
        str(value)
        for value in (
            secondary_receipts if isinstance(secondary_receipts, list) else []
        )
    ]
    secondary_queue = (
        secondary.get("queued_receipts") if isinstance(secondary, dict) else []
    )
    queued_receipts: list[dict[str, Any]] = [
        dict(value)
        for value in (secondary_queue if isinstance(secondary_queue, list) else [])
        if isinstance(value, Mapping)
    ]
    if (
        args.execute
        and notification_lifecycle_packet is not None
        and notification_lifecycle_path is not None
        and (new_receipts or queued_receipts or existing_queued_receipts)
    ):
        try:
            write_result = persist_issue_fix_reviewer_notification_state(
                notification_lifecycle_path,
                notification_lifecycle_packet,
                receipts=new_receipts,
                queued_receipts=queued_receipts,
                replace_queued_receipts=True,
            )
        except (OSError, ValueError):
            payload["ok"] = False
            payload["secondary_notification_state_blocker"] = (
                "reviewer_notification_state_persistence_failed"
            )
            if new_receipts:
                payload["secondary_notification_receipt_blocker"] = (
                    "reviewer_notification_receipt_persistence_failed"
                )
            if queued_receipts:
                payload["secondary_notification_queue_blocker"] = (
                    "reviewer_notification_queue_persistence_failed"
                )
        else:
            write_summary = {
                "schema_version": "issue_fix_reviewer_notification_state_write_v0",
                "domain_pack": "issue_fix",
                "stream": "pr_lifecycle",
                "write_performed": write_result.get("write_performed") is True,
                "status": write_result.get("status"),
                "path_recorded": False,
            }
            payload["secondary_notification_state_write"] = write_summary
            if new_receipts:
                payload["secondary_notification_receipt_write"] = {
                    **write_summary,
                    "schema_version": (
                        "issue_fix_reviewer_notification_receipt_write_v0"
                    ),
                }
            if queued_receipts:
                payload["secondary_notification_queue_write"] = {
                    **write_summary,
                    "schema_version": (
                        "issue_fix_reviewer_notification_queue_write_v0"
                    ),
                }
            payload["secondary_notification_receipts_persisted"] = bool(new_receipts)
            payload["secondary_notification_queue_persisted"] = bool(queued_receipts)
            queue_reconciliation = write_result.get("queue_reconciliation")
            if isinstance(queue_reconciliation, Mapping):
                payload["secondary_notification_queue_reconciled"] = True
                payload["secondary_notification_queue_cancelled_count"] = int(
                    queue_reconciliation.get("cancelled_count") or 0
                )
            payload["secondary_notification_state_persisted"] = True
    return payload, render_issue_fix_reviewer_request_markdown
