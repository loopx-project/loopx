"""GitHub read adapter for the typed post-approval reconciliation read model.

No dismissal executor lives here: candidate age is not finding-resolution proof.
"""
from __future__ import annotations

from typing import Any

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result


def approval_closeout_contract() -> dict[str, Any]:
    return {
        "required_after": "published_exact_head_approve_readback",
        "readback_command": "loopx --format json pr-review --repo OWNER/REPO --check-approval-closeout NUMBER@HEAD_OID",
        "old_commit_proves_resolution": False,
        "grants_dismissal_or_merge_authority": False,
        "dismissal_requires": [
            "all_prior_findings_verified_resolved", "github_permission_and_owner_authority",
            "fresh_unchanged_head_approval_and_effective_target",
        ],
        "readback_requires": ["target_dismissed_approval_preserved_head_unchanged", "remaining_blockers_reported_truthfully"],
        "procedure": [
            "After publishing/readback of APPROVE (including the author-owned COMMENTED fallback), run readback_command. An existing exact-head APPROVE may use this compact closeout without a duplicate audit or review.",
            "For every effective blocking_reviews row, read its full review and inline comments. Independently map EVERY finding to current-head code and decisive validation. Old commit, resolved threads, another account's approval, or green CI alone never proves resolution; same-head findings may also need reconciliation.",
            "Dismiss only findings verified resolved or independently disproven, with explicit owner authorization for review reconciliation and actual repository/branch dismissal permission. Preserve unresolved or unverified reviews. A COMMENTED self-approval is not GitHub approval or authority over another reviewer.",
            "Immediately before each dismissal, re-read this plan and target review/comments; stop if the head, approval, target, or findings changed. Use GitHub's native review dismissal, never deletion: gh api --method PUT repos/OWNER/REPO/pulls/NUMBER/reviews/REVIEW_ID/dismissals -f message='PUBLIC_SAFE_FINDING_RESOLUTION_EVIDENCE'. Retain discussion and include evidence in the required dismissal message.",
            "Read the target back as DISMISSED, verify the approval remains at the unchanged head, and rerun closeout. Report remaining blockers and raw reviewDecision (null is not APPROVED). On a hold, permission failure, or unresolved finding, preserve the earned APPROVE and report the separate closeout/merge hold; do not merge or erase dissent.",
        ],
    }


def plan_approval_closeout(request: dict[str, Any]) -> dict[str, Any]:
    try:
        result = effect_runtime_result("capabilities.pr_review.approval_closeout.plan", request)
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    if not isinstance(result, dict) or result.get("schema_version") != "pull_request_review_approval_closeout_v0":
        raise TypeError("typed approval closeout result mismatch")
    result["execution_contract"] = approval_closeout_contract()
    return result


def read_github_approval_closeout(*, repository: str, exact_head: str) -> dict[str, Any]:
    from .github_source import _fetch_complete_pr_files, run_gh_json
    from ...pr_review import (
        BEHAVIORAL_POLICY_AREAS, CODE_AREAS, _files, _review_conclusion,
        resolve_current_github_login,
    )
    from .selection_execution import normalize_fresh_audit_exact_heads

    targets = normalize_fresh_audit_exact_heads([exact_head])
    exact_head = next(iter(targets))
    number = int(exact_head.split("@", 1)[0])
    login = resolve_current_github_login()
    if not login:
        raise ValueError("approval closeout requires authenticated reviewer identity")
    fields = "number,headRefOid,state,reviewDecision,author,files,changedFiles"
    args = ["pr", "view", str(number), "--repo", repository, "--json", fields]
    pr = run_gh_json(args)
    if not isinstance(pr, dict):
        raise ValueError("pull-request readback is incomplete")
    expected_files = pr.get("changedFiles")
    if not isinstance(expected_files, int) or expected_files < 0:
        raise ValueError("changed-file readback is incomplete")
    if not isinstance(pr.get("files"), list) or len(pr["files"]) != expected_files:
        pr["files"] = _fetch_complete_pr_files(repository=repository, number=str(number),
            expected_count=expected_files, cwd=None, run_gh_json=run_gh_json)
        if pr["files"] is None:
            raise ValueError("changed-file readback is incomplete")
    if any(not isinstance(row, dict) or not isinstance(row.get("path"), str)
           or not row["path"].strip() for row in pr["files"]):
        raise ValueError("changed-file readback is malformed")
    pages = run_gh_json(["api", "--paginate", "--slurp",
                         f"repos/{repository}/pulls/{number}/reviews?per_page=100"])
    if not isinstance(pages, list) or not pages or any(not isinstance(page, list) for page in pages):
        raise ValueError("paginated review readback is incomplete")
    reviews = [row for page in pages for row in page]
    if any(not isinstance(row, dict) for row in reviews):
        raise ValueError("review readback is malformed")
    own_reviews = [{"state": row.get("state"), "body": row.get("body"),
                    "author": row.get("user"), "submittedAt": row.get("submitted_at"),
                    "commit": {"oid": row.get("commit_id")}}
                   for row in reviews if isinstance(row.get("user"), dict)
                   and str(row["user"].get("login", "")).casefold() == login.casefold()]
    # Reuse the current standalone/exact-head body validator, not a new approval rule.
    approval = _review_conclusion(pr | {"reviews": own_reviews}, reviewer_login=login,
        behavior_bearing=bool({row["area"] for row in _files(pr)} & (CODE_AREAS | BEHAVIORAL_POLICY_AREAS)))
    if approval["valid"]:
        matching = [row for row in reviews if row.get("submitted_at") == approval["submitted_at"]
                    and row.get("state") == approval["state"]
                    and row.get("commit_id") == pr.get("headRefOid")
                    and str(row.get("user", {}).get("login", "")).casefold() == login.casefold()]
        if len(matching) != 1:
            raise ValueError("approval readback identity is ambiguous")
        approval["review_id"] = matching[0].get("id")
    after = run_gh_json(args)
    if not isinstance(after, dict) or after.get("number") != number or after.get("state") != pr.get("state"):
        raise ValueError("pull-request identity or lifecycle changed during readback")
    return plan_approval_closeout({"repository": repository, "expected_exact_head": exact_head,
        "pull_request": pr, "readback_head": after.get("headRefOid"), "reviews": reviews,
        "reviews_complete": True, "approval_conclusion": approval})
