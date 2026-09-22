---
name: loopx-pr-review
description: Use for `/loopx-pr-review` or evidence-backed PR queue review. Run `loopx pr-review` first, execute the capability-owned review plan for each selected exact head, then publish full bilingual PR reviews (complete Chinese five-block review plus one concise English verdict) that match the verified findings. Use `loopx-pr-merge` for approval or merge actions.
---

# LoopX PR Review

This skill is a thin host adapter. The built-in `pull-request-review`
capability owns review depth, evidence requirements, completeness, and verdict
policy through the CLI packet. Do not copy those rules into this skill or
replace them with a host-specific checklist.

## Route

Use this skill for `/loopx-pr-review`, explicit PR reviews, or review queues by
state or time window. Route approval, merge, self-merge, and admin bypass to
`loopx-pr-merge` (optional repo-kept workflow, not installed by default) after
the evidence review is complete; it never replaces this skill's exact-head gate.

For named PRs, resolve heads and run repeatable `--target-exact-head NUMBER@HEAD_OID`. An omitted `--state` keeps ordinary queue discovery open-only while exact targets remain lifecycle-neutral; use explicit `--state merged|all` only for deliberate history or post-merge audit.

Translate only explicit filters:

- `--repo owner/repo`
- `--since ISO`
- `--state open|merged|all`
- `--limit N`; `--target-exact-head NUMBER@HEAD_OID` (repeatable direct read)
- `--review-priority other-developers-first|owner-first` (default `other-developers-first`; use `owner-first` to opt into owner priority)
When omitted, the CLI resolves `pull_request_review` from the standard machine
capability editor; an absent namespace keeps the default `other-developers-first`.
Words such as `today`, `open`, or `merged` are filters, not permission to return a
table only; stats-only output needs an explicit opt-out such as `只统计` or `stats only`.

## Preserve The Packet

Save the full first JSON packet before printing a compact projection. Keep all
paths named by `agent_response_contract.required_packet_fields_to_preserve`:

- `agent_response_contract.review_execution_contract`
- `result_completeness` and `scheduling_policy`
- `review_groups`
- `pull_requests[review_action_kind!=null].review_plan`
- `pull_requests[review_action_kind!=null].review_template`
- `pull_requests[review_action_kind!=null].evidence_commands`

Do not pipe the only copy through `jq`. When an exhaustive queue request has
`result_completeness.complete=false`, rerun with its `recommended_limit` before
reviewing; `limit_scope=exact_targets` is already complete for the named targets.

Require the revision the installed capability declares, not a literal this file pins: read
`review_execution_contract.policy_revision` from the packet and require the result's `review_policy_revision` to equal it.
A schema name alone is insufficient. If missing or unequal, do not publish APPROVE; a conservative REQUEST_CHANGES
is allowed only when it names the incompatible-policy gap. Do not retain expired temporary worktree overrides.
Honor explicit runtime pins, but report incompatible policy rather than silently downgrading the review.

## Read The Review Frame First

Read the PR's existing comments and cited documents first: a maintainer comment
names the contract the change is judged against.

- Review inside that frame; cite the document and check its rows, gates, or exit criteria one by one at the exact head, attaching each finding to its row.
- Name unsatisfied rows as missing frame items with their own evidence, and keep the declared boundary (preview versus promotion, milestone entry versus merge).
- Publish the frame-aligned conclusion as a PR comment citing that guidance, since a published review body cannot be edited.

## Execute One Review Plan

Follow `scheduling_policy` and its ranked actionable `review_sequence`; explicit current-request PR selection may override ordering only, never `pull_requests[].review_action_kind` or exact-head idempotency. Generic `re-review`, `重新review`, and `复审` wording selects the named PR; it is not a force-refresh token. Todo/monitor prose may not select work.
When `review_action_kind` is null, the row stays in `pull_requests` inventory but must not appear in `review_sequence`; its `review_plan` and `review_template` are null and `evidence_commands` is empty. Do one compact exact-head conclusion readback and report the existing verdict or bounded invalid/missing reason. Run a fresh audit only when the user explicitly requests fresh evidence despite that no-action result, or supplies a concrete new concern/evidence invalidation; regenerate with `--fresh-audit-exact-head NUMBER@HEAD_OID`, then execute the complete current plan and never inherit the earlier approval. For every actionable PR:

1. Record the packet's exact head. Follow `review_execution_contract.decision_procedure`, starting with the current goal
   and its delivery judgment in `problem_context`, including on re-review;
   then run `evidence_commands` and relevant repository-native validation.
2. Fill `review_plan.result_template` from the shared execution contract;
   preserve missing evidence as `unverified`. Execute its repository-reuse,
   default-off, authority and real-path counterfactual requirements rather than
   repeating them as prose. Never infer `verified` from metadata or CI.
3. Apply `completion_gate` literally. Save the filled result and check it before
   publication:

   ```bash
   loopx --format json pr-review --check-result review-result.json --packet review-packet.json
   ```

   Fix contradictory verdicts, not evidence labels to obtain a pass. This local
   check cannot verify evidence truth, architecture judgment, remote freshness, or
   an old result relabeled without executing the current plan. Verified rows fill
   their declared fields; validation rows bind typed `case_id` coverage, and
   missing material evidence needs a concrete request-changes reason.
4. Render the verified result through `review_template`. The five sections are
   output structure, while the execution contract is the evidence authority.
5. Re-read the remote head immediately before verdict and publication. Restart
   the evidence pass if it changed.

Each PR gets an independent evidence pass and standalone card; a queue table is
only a preface. Finish fewer complete cards rather than metadata-only reviews.

For managed review, pass `--goal-id GOAL` and follow the packet’s resolved `wait_for_ci`: false means never fetch, poll, or wait for CI; true retains CI validation. Required local failures/skips always block. Configure one Goal with `configure-goal --goal-id GOAL --no-pr-review-wait-for-ci --execute`; clear with `--clear-pr-review-configuration --execute`.

## Publish And Read Back
For an open PR, publish validated actionable findings by default unless the user
requested local-only/dry-run output or the finding is private or security-sensitive.

- Remaining blocker: formal `REQUEST_CHANGES`; for an author-owned PR, use a
  `COMMENTED` review titled `Request changes conclusion (author-owned PR; GitHub blocks formal self-review)`.
- Non-blocking finding with no blockers: formal `APPROVE`, not a bare comment;
  when the account is the author and self-approval is rejected, record the same
  conclusion as a `COMMENTED` review titled `Approval conclusion (author-owned PR; GitHub blocks formal self-approval)`.
- Non-blocking P2 suggestions: still `APPROVE`; keep them in the body.
- Merged PR: publish a post-merge audit comment only for a new actionable
  finding; avoid duplicating an equivalent exact-head result.

Build public text from the exact reviewed head. Remove local paths, private
context, raw logs, credentials, and internal-only links. Read the published
review back, verify its state and rendered body, and return its URL. Merge
still routes through `loopx-pr-merge`; an `APPROVE` is not merge authority.
Do not leave a public blocker only in chat.

Immediately before every merge, run `loopx --format json pr-review --goal-id GOAL
--repo OWNER/REPO --check-merge-readiness NUMBER@HEAD_OID`; require `ready=true`.
Its compact Goal observation suppresses only unchanged requalification; material
change reopens it, admin bypass never overrides this gate, and author fallback needs user authority.

## Full PR Review And Bilingual Format

Every review must cover the whole PR, not only the top finding: read the full diff/local
validation, then explain motivation, architecture, changed symbols, both paths, whole-diff
risk, validation, and judgment. A findings-only or blocker-only body is incomplete.

Publish two artifacts:

1. **详细中文评审** - a standalone Chinese full-PR review with the exact head and five
   sections: `动机`, `改动思路`, `具体改动`, `对主干的风险`, `我的整体评价`. Cover every
   changed surface and key symbols, not only the main finding.
2. **英文简短结论** - a line starting with exactly `English verdict:` followed by the bare
   token `APPROVE` or `REQUEST_CHANGES`, then the head, key finding, and validation. The
   parser matches that token as a keyword, so `English verdict: Approved at ...` is invalid
   and leaves the head without a merge-ready approval; write `English verdict: APPROVE - ...`.

Do not publish before the Chinese section covers the entire PR. Read both artifacts back.

## Example / Walkthrough / Smoke-Only PRs

When the review plan marks `smoke_or_example_only`, the `durable_smoke_value`
evidence is mandatory before approval. The essence is real, durable value to
the repository and product: running, deterministic, and public-safe are
necessary but not enough.

1. Name the shipped behavior, boundary, or maintenance cost this artifact
   guards. "Demonstrates something that already works" is not durable value.
2. Scan existing coverage (`rg -l '<behavior|module>' examples tests`) and the
   same-author batch (`gh pr list ... --author <author>` / `gh search prs`);
   flag same-shape batches opened within minutes as PR farming.
3. Apply the repo smoke policy: thin + durable, guard shipped behavior or a
   real boundary, compress rather than append, consolidate same-shape
   walkthroughs into one PR or focused tests.
4. Verdict: `REQUEST_CHANGES` for duplicative, oversized, or value-less
   scaffolding; name the consolidation or thinning repair in the body.
5. Repeat offenders: after a REQUEST_CHANGES warning, further low-value
   same-shape PRs from the same author escalate to a contribution-restriction
   recommendation (owner blocks the account from further PR submissions); the
   warning must name this consequence.

## Autonomous Queue

For recurring observation, keep one ignored checkpoint and use `loopx --format
json pr-review --repo owner/repo --state open --autonomous-observation
--observation-state-file .local/pr-review-monitor.json` with the projected or handled
exact-head flags when those durable receipts exist.

Treat `candidate` as a preview, not a durable projection. Follow this order: durable
Todo target-key readback -> `--projected-exact-head` -> exact-head review/comment
readback -> `--handled-exact-head`. Never send the projection ACK before the Todo
exists, or the handled ACK before readback at that head. Observation states remain literal;
the checkpoint grants no authority. Stateless callers may use `--previous-observation-json` instead.

## Failure

If `loopx pr-review` is unavailable, repair the LoopX install or use the
intended checked-out CLI. Do not reconstruct the queue manually and call it a
successful `/loopx-pr-review` run.
