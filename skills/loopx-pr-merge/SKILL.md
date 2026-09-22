---
name: loopx-pr-merge
description: Optional maintainer workflow for the approval, merge-readiness, or authorized self-merge decision on a LoopX pull request. Requires the exact-head review evidence from `loopx pr-review`. Review depth, verdicts, review comments, and the published review format belong to loopx-pr-review.
---

# LoopX PR Merge

Optional maintainer workflow. It lives in the repository and stays outside the
default installed skill set, so a host that never merges LoopX pull requests is
not affected by it. Install or copy it deliberately when a host needs to make
merge decisions.

The built-in `pull-request-review` capability owns review depth, evidence
requirements, completeness, and verdict policy. This skill owns one narrower
decision: whether this pull request may be approved or merged now, under which
authority, and with what recorded validation.

## Decision Precondition

A merge, approval, self-merge, or admin-bypass decision requires review evidence
for the exact head being merged. A merge decision without this evidence is not
authorized: a diff read, green CI, or pull-request metadata is not a substitute.

1. Run `loopx --format json pr-review --state all`, or use the installed
   `loopx-pr-review` skill, and preserve
   `agent_response_contract.review_execution_contract`. Execute the review plan,
   `review_template`, and `evidence_commands` it names for every changed
   surface.
2. Apply `completion_gate` literally, and re-read the remote head immediately
   before the decision.
3. Immediately before the merge, run `loopx --format json pr-review --goal-id
   GOAL --repo OWNER/REPO --check-merge-readiness NUMBER@HEAD_OID`. Merge only
   when it returns `ready=true` for that unchanged head; this records the
   compact Goal readiness observation consumed by future review queues.

A rebase or head update restarts review, and admin bypass never overrides this
gate: it needs explicit owner authorization and never substitutes for the
evidence.

## Configured CI Waiting

Pass `--goal-id GOAL` for managed merges and follow resolved `wait_for_ci`.
The default is true. When false, do not query, poll, or wait for CI; complete
required local validation and exact-head review/thread checks. GitHub `BLOCKED`
then reports separately authorized admin bypass, never permission to merge.

## Decision Workflow

1. Read the repository `AGENTS.md`, the pull-request diff, the changed paths, the
   local validation results, and the latest comments.
2. Confirm that the repository's own self-merge policy covers the changed
   surfaces. The usual LoopX shape is single-purpose and validated work with no
   private state, no public evidence-policy change, no destructive git action,
   and no unreviewed runtime, permission, benchmark, or storage-authority
   behavior.
3. Write the product and architecture judgment below, then produce the
   exact-scope change-quality receipt when the goal enables that policy.
4. Decide: approve, self-merge with owner authorization, request changes, or
   hold for the maintainer path. Record the decision on the pull request with
   the changed surfaces, the checks that ran, failures and skips, manual holds,
   and the reason that coverage is enough. Record it as a published review on
   the exact head, not as a summary in another channel: on an author-owned PR
   GitHub blocks formal self-approval, so the record is the `COMMENTED` review
   carrying the approval conclusion and the English verdict. A self-merge whose
   head carries no such record is a process gap to repair, not an authorized
   merge.
5. After the merge, sync the local default branch, leave unrelated dirty
   worktree state alone, and update LoopX todo or evidence when the work is
   tracked.

## Product And Architecture Judgment

- **Motivation:** which product or kernel problem the change solves.
- **Solved or not:** whether the diff plus validation prove it, and what
  coverage stays partial.
- **User and operator impact:** which bad case becomes safer, clearer, or
  faster.
- **Main risk:** compatibility, hot path, migration, public and private
  boundary, permission, benchmark validity, or installed-user risk.
- **Design judgment:** reusable contract or ad hoc patch, and the cleaner
  general alternative when one exists.

Findings still lead. This layer decides whether a broad change should be split,
held, self-merged with owner authorization, or followed by a focused cleanup.

## Validation Checklist

Use the narrowest meaningful validation, but do not skip it:

- a syntax or compile check for each changed language;
- the capability-owned smoke or test suite for each changed surface;
- a CLI or schema smoke when a command, packet, or schema changed;
- diff hygiene: `git diff --check <base>...HEAD` or the local equivalent;
- a public and private boundary scan when public docs, fixtures, evidence, or
  examples changed;
- the repository's risk-based pre-merge gate, when it provides one;
- a real-path check against the production entrypoint for runtime, storage, or
  authority changes, with a stated evidence gap when that environment is
  unavailable.

When validation cannot run, say exactly why in both the pull-request decision
comment and the chat summary.

## Literal-Safe GitHub Text

Treat PR bodies, review comments, issue comments, and release notes as data,
not as shell source.

- Never pass Markdown-rich text through a double-quoted shell `--body`
  argument. Backticks trigger command substitution before `gh` receives the
  text.
- Write multiline text into an ignored temporary body file, scan that file for
  public and private boundary violations, and use `--body-file` or pipe a JSON
  body into `gh api --input -`.
- For an existing release body, avoid partial byte or character-index slicing,
  especially with non-ASCII text. Build the complete replacement body, update
  it atomically, then compare the remote body hash with the reviewed file.
- After every public text mutation, read the rendered body back and scan for
  local paths, command output, interpolation artifacts, and damaged adjacent
  sections before proceeding.

## Review Handoff

Use `loopx-pr-review` for review evidence, key-code explanations, and the
published review format; if it is unavailable, repair the LoopX install instead
of reconstructing the review manually. This skill adds the decision and its
authorization and validation evidence, and never defines a second review format.
