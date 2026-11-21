"""Protected GitHub PR merge preview, execution, and Steward private-chat handlers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ...control_plane.effect_runtime import effect_runtime_result
from .github_pr_evidence import (
    inspect_github_pull_request,
    is_steward_repository_authorized,
    parse_github_pr_target,
    repository_slugs_for_goals,
)


def pr_merge_preview(
    normalized: Mapping[str, Any], *, digest_fn: Callable[[Any], str], runner: Any
) -> tuple[dict[str, Any], list[str], str]:
    """Validate an open exact-head APPROVE PR and freeze preview state."""
    repository = str(normalized["repository"])
    pr_number = int(normalized["pr_number"])
    live = inspect_github_pull_request(repository, pr_number, runner=runner)
    if live is None:
        raise ValueError("GitHub PR details are unavailable")
    if live.get("state") != "OPEN":
        raise ValueError(f"PR #{pr_number} is not open ({live.get('state')})")
    if live.get("isDraft") is True:
        raise ValueError(f"PR #{pr_number} is still a Draft")
    if live.get("mergeable") == "CONFLICTING":
        raise ValueError(f"PR #{pr_number} has merge conflicts")
    review_summary = live.get("review_summary") or {}
    if review_summary.get("has_review_conflict"):
        raise ValueError(f"PR #{pr_number} has conflicting exact-head reviews")
    if review_summary.get("effective_verdict") != "APPROVE":
        raise ValueError(f"PR #{pr_number} lacks a verified exact-head APPROVE review")
    best_review = next(
        (
            review
            for review in reversed(review_summary.get("exact_head_reviews") or [])
            if (review.get("valid_verdict_review") and review.get("verdict") == "APPROVE")
            or review.get("formal_github_verdict") == "APPROVE"
        ),
        None,
    )
    if best_review is None:
        raise ValueError(f"PR #{pr_number} has no identifiable exact-head APPROVE review")
    head_oid = str(live.get("headRefOid") or "").strip()
    base_ref = str(live.get("baseRefName") or "").strip()
    if not head_oid or not base_ref:
        raise ValueError(f"PR #{pr_number} is missing head commit or base branch")
    pr_url = str(live.get("url") or f"https://github.com/{repository}/pull/{pr_number}")
    bypassed_gates = [str(item) for item in (live.get("bypassed_gates") or []) if item]
    approval_review = {
        key: best_review.get(key)
        for key in (
            "id", "url", "author_login", "reviewer_declaration",
            "state", "author_owned_fallback", "submitted_at",
        )
        if best_review.get(key) is not None
    }
    frozen = {
        **dict(normalized),
        "head_oid": head_oid,
        "base_ref": base_ref,
        "pr_url": pr_url,
        "approval_review": approval_review,
        "bypassed_gates": bypassed_gates,
        "admin_bypass": bool(normalized.get("admin_bypass")) or bool(bypassed_gates),
    }
    fingerprint = digest_fn({
        "repository": repository,
        "pr_number": pr_number,
        "head_oid": head_oid,
        "base_ref": base_ref,
        "approval_review_id": approval_review.get("id"),
    })
    evidence = [
        f"GitHub PR {repository}#{pr_number} verified open and APPROVE-reviewed at exact head {head_oid[:10]}.",
        *(
            [f"Admin Bypass gates to override: {', '.join(bypassed_gates)}."]
            if bypassed_gates
            else ["No branch-protection gates require bypass on this PR."]
        ),
    ]
    return frozen, evidence, fingerprint


def apply_pr_merge(
    service: Any,
    proposal_id: str,
    proposal: dict[str, Any],
    parameters: dict[str, Any],
    *,
    digest_fn: Callable[[Any], str],
    gate_cls: type[Exception],
    runner: Any,
) -> dict[str, Any]:
    """Execute or recover an exact-head GitHub PR merge with post-merge verification."""
    repository = str(parameters["repository"])
    pr_number = int(parameters["pr_number"])
    method = str(parameters.get("merge_method") or "squash")
    admin_bypass = bool(parameters.get("admin_bypass"))
    steps = ((proposal.get("checkpoint") or {}).get("steps") or {})
    live = inspect_github_pull_request(repository, pr_number, runner=runner)
    if live is None:
        gate = {
            "kind": "github_pr_unavailable",
            "summary": f"Could not inspect {repository}#{pr_number} before merge.",
            "next_action": "Verify gh CLI connectivity and retry confirmation.",
        }
        service.store.mark_gated(proposal_id, gate=gate)
        raise gate_cls("pr.merge", gate=gate)

    def commit_receipt(after: Mapping[str, Any], *, outcome: str) -> dict[str, Any]:
        merge_commit_oid = str(after.get("merge_commit_oid") or "").strip()
        bypassed = [
            str(item)
            for item in (parameters.get("bypassed_gates") or live.get("bypassed_gates") or [])
            if item
        ]
        receipt = {
            "receipt_id": digest_fn({
                "proposal_id": proposal_id,
                "repository": repository,
                "pr_number": pr_number,
                "merge_commit_oid": merge_commit_oid,
            })[:32],
            "outcome": outcome,
            "projection_verified": True,
            "resource_ids": {
                "repository": repository,
                "pr_number": str(pr_number),
                "head_oid": str(parameters.get("head_oid") or ""),
                "merge_commit_oid": merge_commit_oid,
            },
            "pr_url": str(
                parameters.get("pr_url") or after.get("url") or f"https://github.com/{repository}/pull/{pr_number}"
            ),
            "base_ref": str(parameters.get("base_ref") or after.get("baseRefName") or ""),
            "approval_review": dict(parameters.get("approval_review") or {}),
            "bypassed_gates": bypassed,
            "admin_bypass_used": admin_bypass,
        }
        stored = service.store.apply(
            proposal_id,
            current_state_fingerprint=str(proposal["expected_state_fingerprint"]),
            receipt=receipt,
        )
        return {"proposal": stored, "turn": None}

    if live.get("state") == "MERGED" and "merge_attempted" in steps:
        if not str(live.get("merge_commit_oid") or "").strip():
            gate = {
                "kind": "merge_verification_pending",
                "summary": f"PR #{pr_number} shows merged without a readable merge commit OID yet.",
                "next_action": "Retry confirmation once GitHub reports the merge commit OID.",
            }
            service.store.mark_gated(proposal_id, gate=gate)
            raise gate_cls("pr.merge", gate=gate)
        return commit_receipt(live, outcome="pr_merged_recovered")

    review_summary = live.get("review_summary") or {}
    exact_ids = {r.get("id") for r in (review_summary.get("exact_head_reviews") or []) if r.get("id")}
    expected_review_id = (parameters.get("approval_review") or {}).get("id")
    still_matching = (
        live.get("state") == "OPEN"
        and live.get("isDraft") is not True
        and live.get("mergeable") != "CONFLICTING"
        and live.get("headRefOid") == parameters.get("head_oid")
        and live.get("baseRefName") == parameters.get("base_ref")
        and not review_summary.get("has_review_conflict")
        and review_summary.get("effective_verdict") == "APPROVE"
        and (not expected_review_id or expected_review_id in exact_ids)
    )
    if not still_matching:
        mismatch_fp = digest_fn({
            "repository": repository,
            "pr_number": pr_number,
            "state": live.get("state"),
            "head_oid": live.get("headRefOid"),
            "base_ref": live.get("baseRefName"),
            "verdict": review_summary.get("effective_verdict"),
            "conflict": review_summary.get("has_review_conflict"),
            "mergeable": live.get("mergeable"),
        })
        if mismatch_fp == proposal.get("expected_state_fingerprint"):
            mismatch_fp = mismatch_fp + ":stale"
        stale = service.store.apply(proposal_id, current_state_fingerprint=mismatch_fp, receipt={})
        return {"proposal": stale, "turn": None}

    service.store.save_checkpoint(
        proposal_id,
        step="merge_attempted",
        receipt={
            "repository": repository,
            "pr_number": pr_number,
            "head_oid": str(parameters["head_oid"]),
            "admin_bypass": admin_bypass,
        },
    )
    cmd = [
        "gh", "pr", "merge", str(pr_number), "--repo", repository,
        f"--{method}", "--delete-branch", "--match-head-commit", str(parameters["head_oid"]),
    ]
    if admin_bypass:
        cmd.append("--admin")
    completed = runner(cmd, capture_output=True, text=True, check=False, timeout=30)
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout or "").split())[:240]
        gate = {
            "kind": "github_merge_failed",
            "summary": f"GitHub PR merge failed: {detail or 'gh pr merge returned non-zero status'}",
            "next_action": "Verify repository admin permissions and PR head status before preparing a new preview.",
        }
        service.store.mark_gated(proposal_id, gate=gate)
        raise gate_cls("pr.merge", gate=gate)

    after = inspect_github_pull_request(repository, pr_number, runner=runner)
    if not after or after.get("state") != "MERGED" or not str(after.get("merge_commit_oid") or "").strip():
        gate = {
            "kind": "merge_verification_pending",
            "summary": f"gh pr merge exited 0 for {repository}#{pr_number}, but merged commit readback is not confirmed yet.",
            "next_action": "Retry confirmation once GitHub reflects the merged commit.",
        }
        service.store.mark_gated(proposal_id, gate=gate)
        raise gate_cls("pr.merge", gate=gate)
    return commit_receipt(after, outcome="pr_merged")


def steward_pr_merge_extra_auth(
    registry_path: Path,
    runtime_root: Path | None,
    steward_context: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    """Supply `repository_authorized` for `pr.merge` proposals without duplicating policy."""
    if proposal.get("action_kind") != "pr.merge":
        return {}
    repo = str((proposal.get("normalized_parameters") or {}).get("repository") or "")
    return {
        "repository_authorized": is_steward_repository_authorized(
            registry_path, steward_context, repo, runtime_root=runtime_root
        )
    }


def merge_protected_action(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    raw = response.get("protected_action")
    return raw if isinstance(raw, Mapping) and raw.get("operation") == "merge" else None


def preview_reply_block(
    conversations: Any,
    *,
    request_ref: str,
    context: Mapping[str, Any],
    audience: Mapping[str, Any],
    created: str,
    expiry: str,
    protected: Mapping[str, Any],
) -> str:
    """Create and format a `pr.merge` confirmation preview for the owner private chat."""
    runtime_root = getattr(conversations.controller, "coordination_runtime_root", None)
    goal_ids = [str(gid) for gid in (context.get("goal_ids") or []) if gid]
    authorized_repos = repository_slugs_for_goals(
        conversations.actions.registry_path, goal_ids, runtime_root=runtime_root
    )
    default_repo = next(iter(authorized_repos)) if len(authorized_repos) == 1 else None
    try:
        repository, pr_number = parse_github_pr_target(
            protected.get("target"), default_repository=default_repo
        )
        repo_ok = is_steward_repository_authorized(
            conversations.actions.registry_path, context, repository, runtime_root=runtime_root
        )
        request = {
            "action_kind": "pr.merge",
            "summary": str(protected.get("summary") or f"合并 {repository}#{pr_number}")[:500],
            "idempotency_key": f"external-pr-merge-{request_ref}",
            "context": {"kind": "manager", "goal_id": "loopx-manager", **audience, "expires_at": expiry},
            "normalized_parameters": {
                "repository": repository,
                "pr_number": pr_number,
                "merge_method": "squash",
                "admin_bypass": True,
            },
        }
        conversations.bindings._core("collaboration.steward.authorize_creation", {
            "context": context,
            "proposal": {**request, "created_at": created},
            "now": datetime.now(timezone.utc).isoformat(),
            "operation": "preview",
            "repository_authorized": repo_ok,
        })
        proposal = conversations.actions.preview(request)
    except (ValueError, KeyError, RuntimeError) as exc:
        return f"受保护合并尚未执行：{exc}"
    params = proposal["normalized_parameters"]
    review = params.get("approval_review") or {}
    reviewer_label = str(
        review.get("reviewer_declaration") or review.get("author_login") or "GitHub reviewer"
    )
    review_ref = str(review.get("url") or review.get("id") or "exact-head")
    gates = params.get("bypassed_gates") or []
    gates_text = "、".join(gates) if gates else "无（常规门槛已满足）"
    return (
        f"待确认受保护合并：{params['pr_url']}（目标分支 {params['base_ref']}，待合入提交 {params['head_oid']}）\n"
        f"批准依据：{reviewer_label}（{review_ref}）\n"
        f"预计绕过门槛：{gates_text}\n"
        f"尚未执行合并。请在本私聊于预览生成后 15 分钟内发送 /confirm {proposal['proposal_id']}；"
        f"取消：/cancel {proposal['proposal_id']}。"
    )


def run_steward_pr_merge_command(
    conversations: Any,
    *,
    path: Path,
    row: dict[str, Any],
    context: Mapping[str, Any],
    proposal: dict[str, Any],
    write_json: Callable[[Path, Any], None],
    gate_cls: type[Exception],
) -> None:
    """Execute `/confirm` or `/cancel` for a `pr.merge` preview and format private-chat report."""
    argument = str(row["command_argument"]["argument"])
    try:
        runtime_root = getattr(conversations.controller, "coordination_runtime_root", None)
        auth_extra = steward_pr_merge_extra_auth(
            conversations.actions.registry_path, runtime_root, context, proposal
        )
        effect_runtime_result("collaboration.steward.authorize_creation", {
            "context": context,
            "proposal": proposal,
            "now": row["created_at"],
            "operation": "confirm",
            **auth_extra,
        })
        row.update(proposal_id=argument, confirmation_recorded=True)
        write_json(path, row)
        if row["command"] == "cancel_commission":
            conversations.actions.cancel(argument)
            row["response"] = "已取消受保护合并预览；没有执行 GitHub 合并。"
        else:
            result = conversations.actions.apply(
                argument, steward_context=context, steward_confirmed_at=row["created_at"]
            )
            applied = result["proposal"]
            if applied.get("status") == "stale":
                row.update(
                    status="command_completed",
                    failure_kind="StalePreview",
                    response="受保护合并预览已失效：PR 最新提交、评审结论或可合并状态已变化，未执行合并；请重新核验并生成预览。",
                )
                return
            if applied.get("status") != "applied":
                raise ValueError("the protected merge preview could not be applied")
            receipt = applied["receipt"]
            resources = receipt["resource_ids"]
            review = receipt.get("approval_review") or {}
            reviewer_label = str(
                review.get("reviewer_declaration") or review.get("author_login") or "GitHub reviewer"
            )
            review_ref = str(review.get("url") or review.get("id") or "exact-head")
            gates = receipt.get("bypassed_gates") or []
            gates_text = (
                "、".join(gates)
                if gates and receipt.get("admin_bypass_used")
                else "无（常规合并）"
            )
            row.update(
                merge_receipt=receipt,
                response=(
                    f"已核实合入 PR：{receipt.get('pr_url')}\n"
                    f"目标分支：{receipt.get('base_ref') or '-'}；合入提交：{resources.get('merge_commit_oid')}\n"
                    f"批准评审：{reviewer_label}（{review_ref}）\n"
                    f"实际绕过门槛：{gates_text}"
                ),
            )
        row["status"] = "command_completed"
    except gate_cls as exc:
        summary = exc.gate.get("summary") if hasattr(exc, "gate") else str(exc)
        row.update(
            status="command_completed",
            failure_kind=type(exc).__name__,
            response=f"受保护操作未完成：{summary or str(exc)}",
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        row.update(
            status="command_completed",
            failure_kind=type(exc).__name__,
            response="管家操作未完成：原工作区、授权、预览有效期或原生执行入口没有通过核验。原操作已保留；请在本机核对回执后再决定是否重试。",
        )
    finally:
        write_json(path, row)
