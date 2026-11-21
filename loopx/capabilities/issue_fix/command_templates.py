"""Issue-fix command metadata without execution-provider imports."""
from __future__ import annotations

import shlex

ISSUE_FIX_GOAL_CANDIDATE_DISCOVERY_COMMAND_TEMPLATE = (
    "gh issue list --repo "
    '"$(gh repo view --json nameWithOwner --jq .nameWithOwner)" '
    "--state open --limit 20 --json number,title,url,labels"
)


def _command_arg(value: str) -> str:
    if value.startswith("<") and value.endswith(">"):
        return value
    return shlex.quote(value)


def build_issue_fix_pr_lifecycle_command(
    *,
    cli_bin: str,
    goal_id: str,
    agent_id: str,
    project: str | None = None,
) -> str:
    """Build the canonical executable PR lifecycle reconciliation command."""

    parts = [
        shlex.quote(cli_bin),
        "issue-fix",
        "pr-lifecycle",
        "--url",
        "<github-pr-url>",
    ]
    if project:
        parts.extend(["--project", _command_arg(project)])
    parts.extend(
        [
            "--goal-id",
            _command_arg(goal_id),
            "--claimed-by",
            _command_arg(agent_id),
            "--execute-transition",
            "--format",
            "json",
        ]
    )
    return " ".join(parts)


def build_issue_fix_goal_command_templates(
    *, cli_bin: str, goal_id: str, agent_id: str = "<agent-id>"
) -> dict[str, str]:
    """Return the capability-owned commands projected into goal-start packets."""

    cli = shlex.quote(cli_bin)
    goal = (
        goal_id
        if goal_id.startswith("<") and goal_id.endswith(">")
        else shlex.quote(goal_id)
    )
    return {
        "issue_fix_workflow_plan_template": (
            f"{cli} issue-fix workflow-plan "
            "--url <github-issue-or-pr-url> "
            "--repo-path <approved-repo> "
            "--repository-context-json <compact-context.json> "
            "--fetch-candidate-evidence "
            "--validation-label '<validation command>' "
            f"--goal-id {goal} "
            "--format json"
        ),
        "issue_fix_feasibility_template": (
            f"{cli} issue-fix feasibility "
            "--url <github-issue-or-pr-url> "
            "--reproduction-status <confirmed|planned|missing|blocked> "
            "--scope-class <bounded|uncertain|oversized> "
            "--repository-context-json <compact-context.json> "
            f"--goal-id {goal} "
            "--format json"
        ),
        "issue_fix_pr_lifecycle_template": build_issue_fix_pr_lifecycle_command(
            cli_bin=cli_bin,
            goal_id=goal_id,
            agent_id=agent_id,
        ),
        "issue_fix_reviewer_request_template": (
            f"{cli} issue-fix reviewer-request "
            "--url <github-pr-url> "
            "--repo-path <approved-repo> "
            "--base-ref <base-ref> "
            "--execute --format json"
        ),
    }
