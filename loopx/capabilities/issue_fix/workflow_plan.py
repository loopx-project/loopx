from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .command_templates import (
    ISSUE_FIX_GOAL_CANDIDATE_DISCOVERY_COMMAND_TEMPLATE as ISSUE_FIX_GOAL_CANDIDATE_DISCOVERY_COMMAND_TEMPLATE,
    build_issue_fix_goal_command_templates as build_issue_fix_goal_command_templates,
    build_issue_fix_pr_lifecycle_command as build_issue_fix_pr_lifecycle_command,
)
from .acceptance_loop import build_issue_fix_caller_repo_branch_packet
from .candidate_preflight import build_issue_fix_candidate_preflight_packet
from .intake_surface import build_content_ops_issue_fix_metadata_preview_packet
from .repository_context import (
    ISSUE_FIX_REPOSITORY_CONTEXT_INPUT_SCHEMA_VERSION,
    build_issue_fix_repository_context_packet,
    repository_context_input_contract,
)

ISSUE_FIX_WORKFLOW_PLAN_PACKET_SCHEMA_VERSION = "issue_fix_workflow_plan_packet_v0"


def _todo_preview(
    *,
    planner_order: int,
    role: str,
    priority: str,
    task_class: str,
    action_kind: str,
    text: str,
    depends_on: Sequence[str],
    blocks: Sequence[str] | None = None,
    target_key: str | None = None,
    next_command_preview: str | None = None,
) -> dict[str, Any]:
    preview = {
        "schema_version": "loopx_todo_writeback_preview_v0",
        "planner_order": planner_order,
        "command_preview": "loopx todo add",
        "role": role,
        "priority": priority,
        "status": "preview_only",
        "task_class": task_class,
        "action_kind": action_kind,
        "text": text,
        "depends_on": list(depends_on),
        "blocks": list(blocks or []),
        "would_write": False,
        "requires_execute_flag": True,
    }
    if target_key:
        preview["target_key"] = target_key
    if next_command_preview:
        preview["next_command_preview"] = next_command_preview
    return preview


def _resolution_route_candidates(
    *,
    repo_label: str,
    issue_label: str,
) -> list[dict[str, Any]]:
    return [
        {
            "schema_version": "issue_fix_resolution_route_candidate_v0",
            "route": "fix_pr",
            "priority": "P0",
            "when": [
                "public metadata suggests a bounded bug",
                "caller-approved repo context is available",
                "a focused repro or validation label can be named",
            ],
            "next_action_kind": "issue_fix_branch_validation",
            "external_issue_comment_performed": False,
            "external_pr_created": False,
            "requires_user_gate_before_external_write": True,
            "summary": (
                f"Prepare a small fix branch for {repo_label} {issue_label}, "
                "validate it, and emit a PR review packet."
            ),
        },
        {
            "schema_version": "issue_fix_resolution_route_candidate_v0",
            "route": "comment_only",
            "priority": "P1",
            "when": [
                "the issue needs missing repro detail",
                "the safe answer is maintainer-facing clarification",
                "patching would require private material or oversized design work",
            ],
            "next_action_kind": "issue_fix_external_comment_packet",
            "external_issue_comment_performed": False,
            "external_pr_created": False,
            "requires_user_gate_before_external_write": True,
            "summary": (
                "Draft a public-safe maintainer comment packet, but require an "
                "explicit gate before posting it."
            ),
        },
        {
            "schema_version": "issue_fix_resolution_route_candidate_v0",
            "route": "triage_only",
            "priority": "P2",
            "when": [
                "public metadata is too weak for repro or a useful comment",
                "the issue is likely policy, product, or environment specific",
            ],
            "next_action_kind": "issue_fix_no_followup_or_owner_gate",
            "external_issue_comment_performed": False,
            "external_pr_created": False,
            "requires_user_gate_before_external_write": True,
            "summary": (
                "Record a blocker or no-follow-up decision rather than opening "
                "an ungrounded patch loop."
            ),
        },
    ]


def _post_pr_lifecycle_monitor_plan() -> dict[str, Any]:
    return {
        "schema_version": "issue_fix_post_pr_lifecycle_monitor_plan_v1",
        "command_preview": build_issue_fix_pr_lifecycle_command(
            cli_bin="loopx",
            goal_id="<goal-id>",
            agent_id="<agent-id>",
            project="<approved-repo>",
        ),
        "creates_per_pr_continuous_monitor_todo": False,
        "monitor_scope": "lifecycle_state_bucket",
        "monitor_action_kind_template": "issue_fix_pr_state_<state_bucket>_monitor",
        "bucket_projection_source": "pr-lifecycle.grouped_monitor_projection",
        "materializes_nonempty_buckets_only": True,
        "terminal_members_removed": True,
        "per_pr_material_actions_are_one_shot": True,
        "external_notification_granularity": "one_pr_per_message",
        "decisions": [
            "runnable_successor",
            "monitor_continuation",
            "user_gate",
            "no_followup",
        ],
        "terminal_state_precedence": (
            "PR terminal states such as MERGED or CLOSED win over stale review "
            "metadata and must close the monitor with no-follow-up."
        ),
        "external_writes_performed": False,
        "raw_check_logs_captured": False,
    }


def _feasibility_checkpoint_plan() -> dict[str, Any]:
    return {
        "schema_version": "issue_fix_feasibility_checkpoint_plan_v0",
        "command_preview": (
            "loopx issue-fix feasibility --url <github-issue-url> "
            "--reproduction-status <confirmed|planned|missing|blocked> "
            "--scope-class <bounded|uncertain|oversized> "
            "--repository-context-json <compact-context.json> "
            "--goal-id <goal-id> --format json"
        ),
        "input_contract": "compact_public_safe_agent_observation",
        "selects_exactly_one_route": True,
        "required_when_candidate_preflight_route": "proceed",
        "non_proceed_receipt_stream": "candidate-preflight",
        "routes": ["fix_pr", "comment_only", "triage_only"],
        "fix_pr_requires": [
            "bounded_scope",
            "named_reproduction_or_plan",
            "named_validation_surface",
        ],
        "writes_domain_state_by_default_with_goal_id": True,
        "persists_repository_context_with_feasibility": True,
        "writes_loopx_todo": False,
        "raw_issue_or_log_material_captured": False,
    }


def _branch_plan_from_dry_run(
    *,
    repo_path: str | None,
    repo: str,
    issue_ref: str,
    url: str | None,
    base_branch: str,
    issue_branch: str | None,
    validation_label: str,
    generated_at: str | None,
) -> dict[str, Any]:
    if not repo_path:
        return {
            "schema_version": "issue_fix_workflow_branch_plan_v0",
            "status": "needs_approved_repo_context",
            "repo_label": repo,
            "base_branch": base_branch,
            "issue_branch": issue_branch,
            "branch_ready": False,
            "repo_path_captured": False,
            "private_repo_state_read": False,
            "execute_required_for_branch_claim": True,
        }

    dry_run = build_issue_fix_caller_repo_branch_packet(
        repo_path=repo_path,
        repo=repo,
        issue_ref=issue_ref,
        url=url,
        base_branch=base_branch,
        issue_branch=issue_branch,
        validation_label=validation_label,
        execute=False,
        generated_at=generated_at,
    )
    branch = dry_run.get("caller_repo_branch")
    if not isinstance(branch, Mapping):
        raise ValueError("caller repo branch dry-run did not return branch metadata")
    return {
        "schema_version": "issue_fix_workflow_branch_plan_v0",
        "status": "approved_repo_dry_run",
        "repo_label": branch.get("repo_label"),
        "base_branch": branch.get("base_branch"),
        "issue_branch": branch.get("issue_branch"),
        "branch_action": branch.get("branch_action"),
        "branch_ready": False,
        "repo_path_captured": False,
        "private_repo_state_read": False,
        "execute_required_for_branch_claim": True,
        "dry_run_schema_version": dry_run.get("schema_version"),
    }


def build_issue_fix_workflow_plan_packet(
    *,
    repo: str = "public_repo_fixture",
    issue_ref: str = "issue_123_public_metadata_fixture",
    url: str | None = None,
    provider_payload: Mapping[str, Any] | None = None,
    fetch_metadata: bool = False,
    fetch_timeout_seconds: int = 10,
    repo_path: str | None = None,
    base_branch: str = "main",
    issue_branch: str | None = None,
    validation_label: str = "caller-declared validation",
    repository_context_input: Mapping[str, Any] | None = None,
    repository_memory_input: Mapping[str, Any] | None = None,
    candidate_preflight_input: Mapping[str, Any] | None = None,
    generated_at: str | None = "2026-06-23T00:00:00Z",
) -> dict[str, Any]:
    """Build a public-safe issue-fix workflow plan without writing state."""

    metadata_packet = build_content_ops_issue_fix_metadata_preview_packet(
        repo=repo,
        issue_ref=issue_ref,
        url=url,
        provider_payload=provider_payload,
        fetch_metadata=fetch_metadata,
        fetch_timeout_seconds=fetch_timeout_seconds,
        generated_at=generated_at,
    )
    metadata = metadata_packet["github_metadata_preview"]
    intake = metadata_packet["issue_fix_intake"]
    adapter_preview = metadata_packet["adapter_preview"]
    gated_fields = list(adapter_preview.get("gated_provider_fields_present") or [])
    branch_plan = _branch_plan_from_dry_run(
        repo_path=repo_path,
        repo=str(metadata["repo"]),
        issue_ref=str(metadata["issue_ref"]),
        url=url,
        base_branch=base_branch,
        issue_branch=issue_branch,
        validation_label=validation_label,
        generated_at=generated_at,
    )

    repo_label = str(metadata["repo"])
    issue_label = str(metadata["issue_ref"])
    candidate_preflight = build_issue_fix_candidate_preflight_packet(
        repo=repo_label,
        issue_ref=issue_label,
        input_payload=candidate_preflight_input,
        generated_at=generated_at,
    )
    candidate_source_receipt = (
        candidate_preflight.get("evidence", {}).get("source_receipt")
        if isinstance(candidate_preflight.get("evidence"), Mapping)
        else None
    )
    repository_context = build_issue_fix_repository_context_packet(
        repo=repo_label,
        issue_ref=issue_label,
        context_input=repository_context_input,
        memory_retrieval_input=repository_memory_input,
        generated_at=generated_at,
    )
    unresolved_context = list(
        repository_context.get("unresolved_required_aspects") or []
    )
    resolution_routes = _resolution_route_candidates(
        repo_label=repo_label,
        issue_label=issue_label,
    )
    feasibility_checkpoint = _feasibility_checkpoint_plan()
    post_pr_monitor = _post_pr_lifecycle_monitor_plan()
    default_agent_todos = [
        _todo_preview(
            planner_order=1,
            role="agent",
            priority="P0",
            task_class="advancement_task",
            action_kind="issue_fix_public_metadata_classification",
            text=(
                f"[P0] Classify {repo_label} {issue_label} from body-free public "
                "metadata and compact repository context; ground any missing "
                "change-scope, reproduction, or validation evidence, or preserve it "
                "as unresolved before routing."
            ),
            depends_on=[
                "content_ops_issue_fix_metadata_preview_packet_v0",
                "issue_fix_intake_v0",
                "issue_fix_repository_context_v0",
            ],
        ),
        _todo_preview(
            planner_order=2,
            role="agent",
            priority="P0",
            task_class="advancement_task",
            action_kind="issue_fix_feasibility_decision",
            text=(
                "[P0] Record a compact feasibility observation and select exactly "
                "one fix_pr, comment_only, or triage_only route; write only the "
                "selected successor."
            ),
            depends_on=["issue_fix_public_metadata_classification"],
        ),
    ]
    preflight_decision = candidate_preflight.get("decision") or {}
    preflight_admission = candidate_preflight.get("admission") or {}
    preflight_route = preflight_decision.get("route")
    admission_state = str(preflight_admission.get("state") or "")
    successors = list(preflight_admission.get("successors") or [])
    agent_todos = default_agent_todos
    if admission_state in {"evidence_required", "verification_required"}:
        successor_text = {
            "issue_fix_collect_candidate_evidence": (
                "[P0] Collect complete source-backed closing PR, cross-reference, "
                f"and maintainer-comment metadata for {repo_label} {issue_label}; "
                "rerun candidate admission before implementation."
            ),
            "issue_fix_verify_pr_current_revision": (
                "[P0] Inspect {ref} at revision {revision} and record whether that "
                f"exact revision implements {repo_label} {issue_label}; rerun "
                "candidate admission with the compact resolution receipt."
            ),
            "issue_fix_resolve_closed_candidate": (
                "[P0] Classify why closed candidate {ref} ended and record retry, "
                f"comment-only, or skip for {repo_label} {issue_label}; do not "
                "silently reopen implementation."
            ),
            "issue_fix_read_maintainer_disposition": (
                "[P0] After the provider-content gate, read the referenced maintainer "
                f"comments for {repo_label} {issue_label} at evidence revision "
                "{revision}; retain only a compact non-blocking, comment-only, or "
                "skip outcome, and rerun admission."
            ),
        }
        collect_command = (
            "loopx issue-fix workflow-plan --url <github-issue-url> "
            "--fetch-candidate-evidence --goal-id <goal-id> --format json"
        )
        resolution_command = (
            "loopx issue-fix workflow-plan --url <github-issue-url> "
            "--fetch-candidate-evidence "
            "--candidate-resolution-json <candidate-resolution.json> "
            "--goal-id <goal-id> --format json"
        )
        agent_todos = []
        for order, successor in enumerate(successors, start=1):
            action_kind = str(successor.get("action_kind") or "")
            template = successor_text[action_kind]
            agent_todos.append(
                _todo_preview(
                    planner_order=order,
                    role="agent",
                    priority="P0",
                    task_class="advancement_task",
                    action_kind=action_kind,
                    text=template.format(
                        ref=successor.get("ref"),
                        revision=successor.get("revision"),
                    ),
                    depends_on=["issue_fix_candidate_preflight_v0"],
                    target_key=str(successor.get("target_key") or ""),
                    next_command_preview=(
                        collect_command
                        if action_kind == "issue_fix_collect_candidate_evidence"
                        else resolution_command
                    ),
                )
            )
    elif preflight_route != "proceed":
        existing_refs = list(preflight_decision.get("existing_pr_refs") or [])
        route_action = {
            "reuse_existing_pr": "issue_fix_reuse_existing_pr",
            "comment_only": "issue_fix_existing_work_disposition",
            "skip": "issue_fix_candidate_no_followup",
        }[preflight_route]
        route_text = {
            "reuse_existing_pr": (
                f"[P0] Evaluate and reuse existing implementation work "
                f"{', '.join(existing_refs)} for {repo_label} {issue_label}; "
                "do not create a competing patch or reopen agentic recall."
            ),
            "comment_only": (
                f"[P0] Preserve the existing disposition for {repo_label} "
                f"{issue_label}; assess whether a compact maintainer comment is "
                "useful before any new patch planning."
            ),
            "skip": (
                f"[P0] Record the prior-work no-follow-up disposition for "
                f"{repo_label} {issue_label}; do not make the issue runnable again."
            ),
        }[preflight_route]
        agent_todos = [
            _todo_preview(
                planner_order=1,
                role="agent",
                priority="P0",
                task_class="advancement_task",
                action_kind=route_action,
                text=route_text,
                depends_on=["issue_fix_candidate_preflight_v0"],
            )
        ]
    user_gates: list[dict[str, Any]] = []
    comment_read_required = any(
        isinstance(successor, Mapping)
        and successor.get("action_kind") == "issue_fix_read_maintainer_disposition"
        for successor in successors
    )
    if (gated_fields and preflight_route == "proceed") or comment_read_required:
        gate_fields = sorted(
            set(gated_fields) | ({"comments"} if comment_read_required else set())
        )
        user_gates.append(
            _todo_preview(
                planner_order=len(agent_todos) + 1,
                role="user",
                priority="P0",
                task_class="user_gate",
                action_kind="approve_github_issue_body_or_comment_read",
                text=(
                    "[P0] Approve a gated read before LoopX uses GitHub issue "
                    "body, comment bodies, timeline events, or raw provider payloads."
                ),
                depends_on=["content_ops_issue_fix_metadata_preview_packet_v0"],
                blocks=["private_repro_material_read", "raw_issue_body_read"],
            )
            | {"gated_fields": gate_fields},
        )
    ordered_previews = sorted(
        agent_todos + user_gates,
        key=lambda preview: int(preview.get("planner_order", 0)),
    )

    validation_plan = [
        {
            "schema_version": "issue_fix_validation_command_v0",
            "command_label": validation_label,
            "command_captured": False,
            "stdout_captured": False,
            "stderr_captured": False,
            "local_path_captured": False,
            "required_for_pr_review": True,
            "executed": False,
            "passed": False,
        }
    ]
    readiness_blockers = [
        "branch_not_executed",
        "validation_not_run",
        "repo_relative_changed_files_missing",
    ]
    if branch_plan["status"] == "needs_approved_repo_context":
        readiness_blockers.insert(0, "approved_repo_context_missing")
    review_packet_preview = {
        "schema_version": "issue_fix_pr_review_packet_v0",
        "ready": False,
        "readiness_blockers": readiness_blockers,
        "files_changed": [],
        "validation_commands": [validation_label],
        "pr_description_contract": {
            "schema_version": "issue_fix_pr_description_contract_v0",
            "source_contract": "pr_review_five_block_template_v0",
            "extension_contract": "issue_fix_reviewer_context_v0",
            "builder_contract": {
                "schema_version": "issue_fix_pr_description_build_v0",
                "surface": "issue_fix.pr_description",
                "default_enabled": False,
                "explicit_dependency_injection": True,
                "provider_call_budget": 1,
                "fail_open_preserves_base_description": True,
                "applied_preferences_require_compact_receipt": True,
            },
            "sections": [
                {
                    "label": "动机",
                    "purpose": "Explain the reproduced user or maintainer problem and why the fix is necessary.",
                },
                {
                    "label": "改动思路",
                    "purpose": "Explain the chosen fix route and the main design tradeoff.",
                },
                {
                    "label": "关键代码或伪代码",
                    "purpose": (
                        "Show the smallest code or pseudocode slice that lets a "
                        "reviewer verify the behavioral change quickly."
                    ),
                    "applicability": "code_changes",
                },
                {
                    "label": "具体改动",
                    "purpose": "Name the reviewer-relevant modules, behavior, and focused regression coverage.",
                },
                {
                    "label": "修复后复现",
                    "purpose": (
                        "Provide a post-fix reproduction path using the repository "
                        "CLI or focused code/test surface."
                    ),
                    "applicability": "reproducible_changes",
                    "accepted_surfaces": [
                        "repository_cli",
                        "focused_code_or_test",
                    ],
                },
                {
                    "label": "验证",
                    "purpose": "List repository-native commands and honest pass, fail, or skipped results.",
                },
                {
                    "label": "对主干的风险与未覆盖",
                    "purpose": "State compatibility risk, residual gaps, and checks not run.",
                },
                {
                    "label": "关联 Issue",
                    "purpose": (
                        "Use a GitHub closing keyword for each fully resolved issue; "
                        "use a non-closing related reference for partial work."
                    ),
                    "applicability": "issue_backed_changes",
                },
            ],
            "issue_reference_policy": {
                "schema_version": "issue_fix_pr_issue_reference_policy_v0",
                "default_closing_keyword": "Fixes",
                "partial_fix_prefix": "Related to",
                "closing_requires_default_branch": True,
                "full_syntax_required_per_issue": True,
                "applied_after_semantic_preferences": True,
                "verification_surface": "closingIssuesReferences",
            },
            "infographic_policy": {
                "required": False,
                "allowed_when": "complex_change",
                "must_not_replace_textual_evidence": True,
            },
            "review_only_section_excluded": "我的整体评价",
            "requires_current_diff_evidence": True,
        },
        "external_issue_comment_performed": False,
        "external_pr_created": False,
        "merge_performed": False,
    }
    preflight_next_action = {
        "reuse_existing_pr": (
            "reuse or assess the matched implementation PR; keep the candidate out "
            "of new patch planning and preserve the existing recall receipt"
        ),
        "comment_only": (
            "preserve the existing route and produce only a compact disposition or "
            "maintainer-comment plan"
        ),
        "skip": (
            "record no-follow-up from prior terminal or triage evidence; do not "
            "reopen patch planning"
        ),
    }.get(preflight_route)
    if admission_state in {"evidence_required", "verification_required"}:
        preflight_next_action = (
            "complete the projected source-bound candidate successor, then rerun "
            "candidate admission; do not enter feasibility or patch planning"
        )
    first_screen = {
        "waiting_on": "agent",
        "user_action_required": False,
        "agent_can_continue": True,
        "top_agent_todo": agent_todos[0],
        "top_gate": user_gates[0] if user_gates else None,
        "next_safe_action": preflight_next_action or (
            (
                "inspect current repository evidence for "
                f"{', '.join(unresolved_context)}; ground what is available and "
                "preserve the rest as unresolved in feasibility instead of guessing"
            )
            if unresolved_context
            else (
                "use the grounded repository context in the metadata classification "
                "and feasibility checkpoint; then let feasibility project one "
                "route-specific successor or no-follow-up"
            )
        ),
    }
    packet: dict[str, Any] = {
        "ok": True,
        "schema_version": ISSUE_FIX_WORKFLOW_PLAN_PACKET_SCHEMA_VERSION,
        "mode": "issue-fix-workflow-plan",
        "generated_at": generated_at,
        "metadata_preview_schema_version": metadata_packet["schema_version"],
        "issue_fix_intake_schema_version": intake.get("schema_version"),
        "issue_signal": {
            "repo": metadata["repo"],
            "issue_ref": metadata["issue_ref"],
            "kind": metadata["kind"],
            "state": metadata["state"],
            "labels": metadata["labels"],
            "body_captured": False,
            "comment_bodies_captured": False,
        },
        "repository_context_input_contract": repository_context_input_contract(),
        "repository_context": repository_context,
        "candidate_preflight": candidate_preflight,
        "candidate_fix_workflow_allowed": preflight_decision.get(
            "candidate_runnable"
        )
        is True,
        "first_screen": first_screen,
        "branch_plan": branch_plan,
        "resolution_route_candidates": resolution_routes,
        "feasibility_checkpoint_plan": feasibility_checkpoint,
        "post_pr_lifecycle_monitor_plan": post_pr_monitor,
        "ordered_loopx_todo_writeback_preview": ordered_previews,
        "validation_plan": validation_plan,
        "review_packet_preview": review_packet_preview,
        "external_reads_performed": bool(
            metadata_packet["external_reads_performed"]
            or (
                isinstance(candidate_source_receipt, Mapping)
                and candidate_source_receipt.get("external_reads_performed") is True
            )
        ),
        "external_writes_performed": False,
        "issue_body_captured": False,
        "comment_bodies_captured": False,
        "response_payloads_captured": False,
        "local_paths_captured": False,
        "private_repo_state_read": False,
        "todo_write_performed": False,
        "destructive_git_used": False,
        "autopublish_allowed": False,
        "automerge_allowed": False,
        "next_safe_action": first_screen["next_safe_action"],
    }
    validation = validate_issue_fix_workflow_plan_packet(packet)
    packet["ok"] = bool(metadata_packet["ok"] and validation["ok"])
    packet["validation"] = validation
    return packet


def validate_issue_fix_workflow_plan_packet(
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if packet.get("schema_version") != ISSUE_FIX_WORKFLOW_PLAN_PACKET_SCHEMA_VERSION:
        errors.append("packet schema_version must be issue_fix_workflow_plan_packet_v0")
    for key in (
        "external_writes_performed",
        "issue_body_captured",
        "comment_bodies_captured",
        "response_payloads_captured",
        "local_paths_captured",
        "private_repo_state_read",
        "todo_write_performed",
        "destructive_git_used",
        "autopublish_allowed",
        "automerge_allowed",
    ):
        if packet.get(key) is not False:
            errors.append(f"packet {key} must be false")

    issue_signal = packet.get("issue_signal")
    if not isinstance(issue_signal, Mapping):
        errors.append("issue_signal is required")
        issue_signal = {}
    for key in ("repo", "issue_ref", "kind", "state"):
        if not issue_signal.get(key):
            errors.append(f"issue_signal {key} is required")
    if issue_signal.get("body_captured") is not False:
        errors.append("issue_signal body_captured must be false")
    if issue_signal.get("comment_bodies_captured") is not False:
        errors.append("issue_signal comment_bodies_captured must be false")

    branch_plan = packet.get("branch_plan")
    if not isinstance(branch_plan, Mapping):
        errors.append("branch_plan is required")
        branch_plan = {}
    if branch_plan.get("repo_path_captured") is not False:
        errors.append("branch_plan repo_path_captured must be false")
    if branch_plan.get("private_repo_state_read") is not False:
        errors.append("branch_plan private_repo_state_read must be false")

    repository_context = packet.get("repository_context")
    if repository_context is not None:
        if not isinstance(repository_context, Mapping):
            errors.append("repository_context must be an object when present")
            repository_context = {}
        if repository_context.get("ok") is not True:
            errors.append("repository_context must be valid")
        if repository_context.get("external_writes_performed") is not False:
            errors.append("repository_context must not perform external writes")
        truth_contract = repository_context.get("truth_contract")
        if (
            not isinstance(truth_contract, Mapping)
            or truth_contract.get("context_cannot_authorize_external_writes")
            is not True
        ):
            errors.append("repository_context must deny external-write authority")

    repository_context_contract = packet.get("repository_context_input_contract")
    if not isinstance(repository_context_contract, Mapping):
        errors.append("repository_context_input_contract is required")
    elif (
        repository_context_contract.get("schema_version")
        != ISSUE_FIX_REPOSITORY_CONTEXT_INPUT_SCHEMA_VERSION
    ):
        errors.append("repository_context_input_contract has wrong schema")

    candidate_preflight = packet.get("candidate_preflight")
    if not isinstance(candidate_preflight, Mapping):
        errors.append("candidate_preflight is required")
        candidate_preflight = {}
    if candidate_preflight.get("schema_version") != "issue_fix_candidate_preflight_v0":
        errors.append("candidate_preflight has wrong schema")
    if candidate_preflight.get("external_reads_performed") is not False:
        errors.append("candidate_preflight must not perform external reads")
    if candidate_preflight.get("external_writes_performed") is not False:
        errors.append("candidate_preflight must not perform external writes")
    preflight_projection = candidate_preflight.get("domain_state_projection")
    if not isinstance(preflight_projection, Mapping):
        errors.append("candidate_preflight domain_state_projection is required")
    else:
        if (
            preflight_projection.get("schema_version")
            != "issue_fix_candidate_preflight_domain_state_projection_v0"
        ):
            errors.append("candidate_preflight domain_state_projection has wrong schema")
        if preflight_projection.get("stream") != "candidate-preflight":
            errors.append("candidate_preflight domain_state_projection has wrong stream")
        if preflight_projection.get("write_performed") is not False:
            errors.append(
                "workflow-plan builder must leave candidate preflight write pending"
            )
    preflight_decision = candidate_preflight.get("decision")
    preflight_decision = (
        preflight_decision if isinstance(preflight_decision, Mapping) else {}
    )
    preflight_admission = candidate_preflight.get("admission")
    if not isinstance(preflight_admission, Mapping):
        errors.append("candidate_preflight admission is required")
        preflight_admission = {}
    admission_state = preflight_admission.get("state")
    if admission_state not in {
        "evidence_required",
        "verification_required",
        "admitted",
        "terminal",
    }:
        errors.append("candidate_preflight admission state is invalid")
    admission_successors = preflight_admission.get("successors")
    if not isinstance(admission_successors, Sequence) or isinstance(
        admission_successors, (str, bytes)
    ):
        errors.append("candidate_preflight admission successors must be a list")
        admission_successors = []
    if admission_state in {"evidence_required", "verification_required"}:
        if preflight_decision.get("route") is not None:
            errors.append("pending candidate admission must not expose a final route")
        if not admission_successors:
            errors.append("pending candidate admission requires a successor")
    elif admission_successors:
        errors.append("final candidate admission must not retain successors")
    candidate_runnable = preflight_decision.get("candidate_runnable") is True
    if candidate_runnable and (
        admission_state != "admitted" or preflight_decision.get("route") != "proceed"
    ):
        errors.append("candidate runnable requires admitted proceed")
    if packet.get("candidate_fix_workflow_allowed") is not candidate_runnable:
        errors.append("candidate workflow permission must match preflight decision")

    routes = packet.get("resolution_route_candidates")
    if not isinstance(routes, Sequence) or isinstance(routes, (str, bytes)):
        errors.append("resolution_route_candidates must be a list")
        routes = []
    route_names = {route.get("route") for route in routes if isinstance(route, Mapping)}
    for route_name in ("fix_pr", "comment_only", "triage_only"):
        if route_name not in route_names:
            errors.append(f"resolution route {route_name} is required")
    for route in routes:
        if not isinstance(route, Mapping):
            errors.append("resolution route entries must be objects")
            continue
        if route.get("external_issue_comment_performed") is not False:
            errors.append("resolution routes must not perform external comments")
        if route.get("external_pr_created") is not False:
            errors.append("resolution routes must not create PRs")
        if route.get("requires_user_gate_before_external_write") is not True:
            errors.append("resolution routes must gate external writes")

    feasibility = packet.get("feasibility_checkpoint_plan")
    if not isinstance(feasibility, Mapping):
        errors.append("feasibility_checkpoint_plan is required")
        feasibility = {}
    if feasibility.get("selects_exactly_one_route") is not True:
        errors.append("feasibility checkpoint must select exactly one route")
    if feasibility.get("required_when_candidate_preflight_route") != "proceed":
        errors.append("feasibility checkpoint must only follow a proceed preflight")
    if feasibility.get("non_proceed_receipt_stream") != "candidate-preflight":
        errors.append("non-proceed candidates must use candidate-preflight receipts")
    if feasibility.get("writes_domain_state_by_default_with_goal_id") is not True:
        errors.append("feasibility checkpoint must default-write domain state")
    if (
        packet.get("repository_context") is not None
        and feasibility.get("persists_repository_context_with_feasibility") is not True
    ):
        errors.append("feasibility checkpoint must persist repository context")
    if feasibility.get("writes_loopx_todo") is not False:
        errors.append("feasibility checkpoint must not directly write LoopX todos")
    if feasibility.get("raw_issue_or_log_material_captured") is not False:
        errors.append("feasibility checkpoint must not capture raw material")

    post_pr = packet.get("post_pr_lifecycle_monitor_plan")
    if not isinstance(post_pr, Mapping):
        errors.append("post_pr_lifecycle_monitor_plan is required")
        post_pr = {}
    if post_pr.get("schema_version") != "issue_fix_post_pr_lifecycle_monitor_plan_v1":
        errors.append("post PR lifecycle plan must use grouped monitor plan v1")
    if post_pr.get("creates_per_pr_continuous_monitor_todo") is not False:
        errors.append("post PR lifecycle plan must not create per-PR monitor todos")
    if post_pr.get("monitor_scope") != "lifecycle_state_bucket":
        errors.append("post PR lifecycle plan must group by lifecycle state")
    if post_pr.get("materializes_nonempty_buckets_only") is not True:
        errors.append("post PR lifecycle plan must materialize only nonempty buckets")
    if post_pr.get("terminal_members_removed") is not True:
        errors.append("post PR lifecycle plan must remove terminal members")
    if post_pr.get("per_pr_material_actions_are_one_shot") is not True:
        errors.append("post PR lifecycle plan must use one-shot per-PR actions")
    if post_pr.get("external_notification_granularity") != "one_pr_per_message":
        errors.append("post PR lifecycle plan must preserve one-PR messages")
    if post_pr.get("external_writes_performed") is not False:
        errors.append("post PR lifecycle plan must not perform external writes")
    if post_pr.get("raw_check_logs_captured") is not False:
        errors.append("post PR lifecycle plan must not capture raw check logs")

    previews = packet.get("ordered_loopx_todo_writeback_preview")
    if not isinstance(previews, Sequence) or isinstance(previews, (str, bytes)):
        errors.append("ordered_loopx_todo_writeback_preview must be a list")
        previews = []
    orders: list[int] = []
    roles = set()
    priorities = set()
    for preview in previews:
        if not isinstance(preview, Mapping):
            errors.append("todo preview entries must be objects")
            continue
        if preview.get("schema_version") != "loopx_todo_writeback_preview_v0":
            errors.append("todo preview has wrong schema")
        if preview.get("would_write") is not False:
            errors.append("todo preview would_write must be false")
        if preview.get("requires_execute_flag") is not True:
            errors.append("todo preview must require execute flag")
        order = preview.get("planner_order")
        if isinstance(order, int):
            orders.append(order)
        else:
            errors.append("todo preview planner_order must be an integer")
        roles.add(preview.get("role"))
        priorities.add(preview.get("priority"))
    if not candidate_runnable:
        forbidden = {
            "issue_fix_public_metadata_classification",
            "issue_fix_feasibility_decision",
            "issue_fix_branch_validation",
        }
        if any(
            isinstance(preview, Mapping)
            and preview.get("action_kind") in forbidden
            for preview in previews
        ):
            errors.append("deduped candidate must not project new patch-planning todos")
    if orders != sorted(orders):
        errors.append("todo previews must be ordered by planner_order")
    if "agent" not in roles:
        errors.append("at least one agent todo preview is required")
    if "P0" not in priorities:
        errors.append("at least one P0 todo preview is required")

    review = packet.get("review_packet_preview")
    if not isinstance(review, Mapping):
        errors.append("review_packet_preview is required")
        review = {}
    if review.get("schema_version") != "issue_fix_pr_review_packet_v0":
        errors.append("review packet preview has wrong schema")
    if review.get("ready") is not False:
        errors.append("workflow plan preview must not mark PR review ready")
    description = review.get("pr_description_contract")
    if not isinstance(description, Mapping):
        errors.append("review preview must include PR description contract")
    else:
        if description.get("schema_version") != (
            "issue_fix_pr_description_contract_v0"
        ):
            errors.append("PR description contract has wrong schema")
        if description.get("builder_contract") != {
            "schema_version": "issue_fix_pr_description_build_v0",
            "surface": "issue_fix.pr_description",
            "default_enabled": False,
            "explicit_dependency_injection": True,
            "provider_call_budget": 1,
            "fail_open_preserves_base_description": True,
            "applied_preferences_require_compact_receipt": True,
        }:
            errors.append("PR description builder contract is incomplete")
        sections = description.get("sections")
        labels = (
            [
                section.get("label")
                for section in sections
                if isinstance(section, Mapping)
            ]
            if isinstance(sections, list)
            else []
        )
        if labels != [
            "动机",
            "改动思路",
            "关键代码或伪代码",
            "具体改动",
            "修复后复现",
            "验证",
            "对主干的风险与未覆盖",
            "关联 Issue",
        ]:
            errors.append("PR description contract sections are incomplete")
        section_by_label = {
            str(section.get("label") or ""): section
            for section in sections or []
            if isinstance(section, Mapping)
        }
        if section_by_label.get("关键代码或伪代码", {}).get("applicability") != (
            "code_changes"
        ):
            errors.append("key code section must apply to code changes")
        if section_by_label.get("修复后复现", {}).get("accepted_surfaces") != [
            "repository_cli",
            "focused_code_or_test",
        ]:
            errors.append("post-fix reproduction surfaces are incomplete")
        if section_by_label.get("关联 Issue", {}).get("applicability") != (
            "issue_backed_changes"
        ):
            errors.append("issue reference section applicability is incomplete")
        if description.get("issue_reference_policy") != {
            "schema_version": "issue_fix_pr_issue_reference_policy_v0",
            "default_closing_keyword": "Fixes",
            "partial_fix_prefix": "Related to",
            "closing_requires_default_branch": True,
            "full_syntax_required_per_issue": True,
            "applied_after_semantic_preferences": True,
            "verification_surface": "closingIssuesReferences",
        }:
            errors.append("PR description issue reference policy is incomplete")
        if description.get("infographic_policy") != {
            "required": False,
            "allowed_when": "complex_change",
            "must_not_replace_textual_evidence": True,
        }:
            errors.append("PR description infographic policy is incomplete")
        if description.get("review_only_section_excluded") != "我的整体评价":
            errors.append("PR description contract must exclude reviewer verdict")
    if review.get("external_issue_comment_performed") is not False:
        errors.append("review preview must not perform external issue comments")
    if review.get("external_pr_created") is not False:
        errors.append("review preview must not create PRs")
    if review.get("merge_performed") is not False:
        errors.append("review preview must not merge")

    return {
        "ok": not errors,
        "schema_version": "issue_fix_workflow_plan_validation_v0",
        "errors": errors,
        "todo_preview_count": len(previews),
        "user_gate_preview_count": sum(
            1
            for preview in previews
            if isinstance(preview, Mapping) and preview.get("role") == "user"
        ),
        "readiness_blocker_count": len(review.get("readiness_blockers") or []),
    }


def render_issue_fix_workflow_plan_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Issue Fix Workflow Plan",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- todo_write_performed: `{payload.get('todo_write_performed')}`",
        f"- local_paths_captured: `{payload.get('local_paths_captured')}`",
        f"- private_repo_state_read: `{payload.get('private_repo_state_read')}`",
    ]
    first_screen = payload.get("first_screen")
    if isinstance(first_screen, Mapping):
        lines.extend(
            [
                "",
                "## First Screen",
                "",
                f"- waiting_on: `{first_screen.get('waiting_on')}`",
                f"- user_action_required: `{first_screen.get('user_action_required')}`",
                f"- agent_can_continue: `{first_screen.get('agent_can_continue')}`",
                f"- next_safe_action: {first_screen.get('next_safe_action')}",
            ]
        )
    issue_signal = payload.get("issue_signal")
    if isinstance(issue_signal, Mapping):
        lines.extend(
            [
                "",
                "## Issue Signal",
                "",
                f"- repo: `{issue_signal.get('repo')}`",
                f"- issue_ref: `{issue_signal.get('issue_ref')}`",
                f"- kind: `{issue_signal.get('kind')}`",
                f"- state: `{issue_signal.get('state')}`",
                f"- labels: `{issue_signal.get('labels')}`",
            ]
        )
    repository_context = payload.get("repository_context")
    if isinstance(repository_context, Mapping):
        lines.extend(
            [
                "",
                "## Repository Context",
                "",
                f"- status: `{repository_context.get('context_status')}`",
                f"- repository_revision: `{repository_context.get('repository_revision')}`",
                f"- context_fingerprint: `{repository_context.get('context_fingerprint')}`",
                f"- unresolved_required_aspects: "
                f"`{repository_context.get('unresolved_required_aspects')}`",
                f"- expert_next_action: "
                f"`{(repository_context.get('expert_consultation') or {}).get('next_action')}`",
            ]
        )
    candidate_preflight = payload.get("candidate_preflight")
    if isinstance(candidate_preflight, Mapping):
        decision = candidate_preflight.get("decision") or {}
        admission = candidate_preflight.get("admission") or {}
        evidence = candidate_preflight.get("evidence") or {}
        recall = candidate_preflight.get("agentic_recall") or {}
        lines.extend(
            [
                "",
                "## Candidate Preflight",
                "",
                f"- configured: `{candidate_preflight.get('configured')}`",
                f"- admission: `{admission.get('state')}`",
                f"- route: `{decision.get('route')}`",
                f"- candidate_runnable: `{decision.get('candidate_runnable')}`",
                f"- existing_pr_refs: `{decision.get('existing_pr_refs')}`",
                f"- successor_action_kinds: "
                f"`{[row.get('action_kind') for row in admission.get('successors', []) if isinstance(row, Mapping)]}`",
                f"- domain_state_matched: `{evidence.get('domain_state_matched')}`",
                f"- agentic_recall_action: `{recall.get('action')}`",
                f"- provider_calls_performed: `{recall.get('provider_calls_performed')}`",
            ]
        )
    branch = payload.get("branch_plan")
    if isinstance(branch, Mapping):
        lines.extend(
            [
                "",
                "## Branch Plan",
                "",
                f"- status: `{branch.get('status')}`",
                f"- base_branch: `{branch.get('base_branch')}`",
                f"- issue_branch: `{branch.get('issue_branch')}`",
                f"- repo_path_captured: `{branch.get('repo_path_captured')}`",
                f"- execute_required_for_branch_claim: "
                f"`{branch.get('execute_required_for_branch_claim')}`",
            ]
        )
    todos = payload.get("ordered_loopx_todo_writeback_preview")
    if isinstance(todos, Sequence) and not isinstance(todos, (str, bytes)):
        lines.extend(["", "## Ordered Todo Writeback Preview", ""])
        for todo in todos:
            if isinstance(todo, Mapping):
                lines.append(
                    f"- `{todo.get('planner_order')}` `{todo.get('role')}` "
                    f"`{todo.get('priority')}` `{todo.get('action_kind')}`: "
                    f"would_write=`{todo.get('would_write')}`"
                )
    feasibility = payload.get("feasibility_checkpoint_plan")
    if isinstance(feasibility, Mapping):
        lines.extend(
            [
                "",
                "## Feasibility Checkpoint",
                "",
                f"- selects_exactly_one_route: "
                f"`{feasibility.get('selects_exactly_one_route')}`",
                f"- routes: `{feasibility.get('routes')}`",
                f"- writes_domain_state_by_default_with_goal_id: "
                f"`{feasibility.get('writes_domain_state_by_default_with_goal_id')}`",
                f"- command_preview: `{feasibility.get('command_preview')}`",
            ]
        )
    routes = payload.get("resolution_route_candidates")
    if isinstance(routes, Sequence) and not isinstance(routes, (str, bytes)):
        lines.extend(["", "## Resolution Routes", ""])
        for route in routes:
            if isinstance(route, Mapping):
                lines.append(
                    f"- `{route.get('route')}` `{route.get('priority')}`: "
                    f"{route.get('summary')}"
                )
    post_pr = payload.get("post_pr_lifecycle_monitor_plan")
    if isinstance(post_pr, Mapping):
        lines.extend(
            [
                "",
                "## Post-PR Lifecycle Monitor",
                "",
                f"- creates_per_pr_continuous_monitor_todo: "
                f"`{post_pr.get('creates_per_pr_continuous_monitor_todo')}`",
                f"- monitor_scope: `{post_pr.get('monitor_scope')}`",
                f"- monitor_action_kind_template: "
                f"`{post_pr.get('monitor_action_kind_template')}`",
                f"- external_notification_granularity: "
                f"`{post_pr.get('external_notification_granularity')}`",
                f"- decisions: `{post_pr.get('decisions')}`",
            ]
        )
    review = payload.get("review_packet_preview")
    if isinstance(review, Mapping):
        lines.extend(
            [
                "",
                "## PR Review Packet Preview",
                "",
                f"- ready: `{review.get('ready')}`",
                f"- readiness_blockers: `{review.get('readiness_blockers')}`",
                f"- external_pr_created: `{review.get('external_pr_created')}`",
                f"- merge_performed: `{review.get('merge_performed')}`",
            ]
        )
        description = review.get("pr_description_contract")
        if isinstance(description, Mapping):
            lines.extend(["", "### PR Description Contract", ""])
            for section in description.get("sections") or []:
                if isinstance(section, Mapping):
                    lines.append(
                        f"- **{section.get('label')}**: {section.get('purpose')}"
                    )
            infographic = description.get("infographic_policy")
            if isinstance(infographic, Mapping):
                lines.append(
                    "- infographic: optional for complex changes; textual evidence remains required"
                )
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        errors = (
            validation.get("errors")
            if isinstance(validation.get("errors"), list)
            else []
        )
        lines.extend(
            [
                "",
                "## Validation",
                "",
                f"- validation_ok: `{validation.get('ok')}`",
                f"- todo_preview_count: `{validation.get('todo_preview_count')}`",
                f"- user_gate_preview_count: `{validation.get('user_gate_preview_count')}`",
                f"- readiness_blocker_count: `{validation.get('readiness_blocker_count')}`",
                f"- error_count: `{len(errors)}`",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"
