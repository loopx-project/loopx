# Public Adoption Loop

Status: docs-first product contract.

LoopX needs a public path for people to try a concrete workflow and report what
worked without requiring maintainers to install GitHub templates before the
route is proven. This note defines the template copy, triage labels, and small
metrics that can later be promoted into `.github` templates if the owner
approves that write scope.

## When To Use It

Use this loop when someone wants to try LoopX on a public repository workflow
and the feedback can be represented without private source, private logs,
credentials, raw transcripts, benchmark task text, or production evidence.

Good first workflows:

- fix or triage a public GitHub issue;
- review a PR and turn findings into bounded follow-up todos;
- run an overnight PR-sized refactor with a review packet;
- keep progress moving when a high-priority item is blocked by a human gate.

Do not use this loop for private customer work, production operations, hidden
security reports, leaderboard submissions, or any repository where the user
cannot share a public-safe summary.

## Issue Template Copy

Suggested title:

```text
Try LoopX on: <workflow name>
```

Suggested body:

```markdown
## Workflow

Which LoopX workflow did you try?

- [ ] Public issue fix or triage
- [ ] PR review-to-fix loop
- [ ] Overnight PR-sized refactor
- [ ] Blocked-P0 safe fallback
- [ ] Other:

## Starting Point

- Repository:
- Public issue or PR:
- Command or entry point used:
- Expected output:

## Result

- What did LoopX produce?
- What validation ran?
- What still needed human judgment?
- Did the loop stop, wait, or continue safely?

## Public-Safe Evidence

Link only public artifacts: PR, issue, docs, compact review packet, or public
smoke output. Do not paste private logs, credentials, raw transcripts,
benchmark task text, or local paths.

## Value Signal

- Time saved or avoided rework:
- Quality signal:
- User attention required:
- Would you run this workflow again?
```

## Discussion Template Copy

Suggested prompt:

```markdown
What workflow should LoopX make easier next?

Please include:

- the repo scenario;
- the human decision that currently blocks automation;
- the smallest public-safe artifact that would prove value;
- the command or chat entry point you expected to use;
- any privacy, permission, or publication boundary.
```

This discussion form is for product signal, not support escalation. If a user
needs a concrete fix, convert the signal into an issue with a public-safe
starting point and expected output.

## Triage Labels

Start with labels that explain the workflow and boundary instead of labels that
promise a solution:

| Label | Meaning |
| --- | --- |
| `adoption:try-loopx` | A user is trying LoopX on a public workflow. |
| `workflow:issue-fix` | Public issue triage, planning, validation, or PR review packet. |
| `workflow:pr-review` | Review findings should become todo, patch, or owner decision. |
| `workflow:overnight-refactor` | Long-running refactor that needs resumable state and review packet. |
| `workflow:blocked-fallback` | A blocked high-priority route needs safe lower-priority progress. |
| `signal:user-value` | The report contains a concrete value or attention-cost signal. |
| `gate:needs-owner` | A human decision is required before more action. |
| `privacy:public-safe` | Evidence is safe to cite publicly. |
| `privacy:needs-redaction` | Evidence must be reduced before public discussion. |
| `status:needs-repro` | The workflow is plausible but lacks a reproducible public path. |

Labels are triage hints, not permission. Posting comments, opening PRs,
publishing screenshots, or editing `.github` templates still needs the normal
owner and boundary checks.

## Lightweight Metrics

Track metrics as a compact note on the issue or discussion:

- `workflow_type`: issue_fix, pr_review, overnight_refactor,
  blocked_fallback, or other;
- `entry_point`: slash_command, codex_cli_tui, codex_app, claude_code, or
  manual_cli;
- `artifact_produced`: todo_plan, patch_pr, review_packet, validation_summary,
  blocker_packet, or none;
- `validation_state`: not_run, failed, passed, partial, or not_applicable;
- `human_gate_count`: integer count of concrete user/controller gates;
- `attention_cost`: low, medium, or high;
- `would_repeat`: yes, no, or unsure.

The metric note should stay short enough to read without opening raw logs. If a
case becomes a showcase candidate, promote it through the
[Issue/PR solver maintainer intake packet](use-cases/issue-pr/issue-pr-solver-maintainer-intake.md)
or another public-safe showcase route.

## Promotion To GitHub Templates

This docs-first contract is intentionally inside `docs/**`. To promote it into
real GitHub issue or discussion templates, first record an owner-approved
boundary decision for the exact `.github` paths, then add the smallest template
files that preserve the same public/private boundary.

Until that approval exists, this document is the canonical adoption-loop copy.

## Independent App trials

The generic workflow templates above remain proposed copy. The repository
already provides optional [First run](../../.github/ISSUE_TEMPLATE/first_run.yml)
and [Usage story](../../.github/ISSUE_TEMPLATE/usage_story.yml) feedback forms.
For the App's first-use cohort, reuse those forms with the
[frozen first-use and repeat-use protocol](use-cases/steward/first-use-evaluation.md).
Keep detailed timing, failures and evidence in a private study note; public
submission and anonymous citation remain separate opt-ins, never success gates.
The protocol is prepared, with no participant results. It requires a qualified
pinned package and consenting users before execution; it sends no invitations
and adds no telemetry or parallel issue intake.

## Related Docs

- [Release readiness](release-readiness.md)
- [Codex CLI packaged install path](runtimes/codex-cli/codex-cli-packaged-install.md)
- [Issue/PR solver maintainer intake packet](use-cases/issue-pr/issue-pr-solver-maintainer-intake.md)
- [Public/private boundary](../public-private-boundary.md)
