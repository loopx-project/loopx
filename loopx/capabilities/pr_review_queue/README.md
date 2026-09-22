# Pull Request Review

The `pull-request-review` capability helps a user review open and recently
merged pull requests one by one by turning public GitHub PR metadata into a
guided review queue. Its command packet remains versioned as
`pr_review_command_v0` and is exposed through `/loopx-pr-review` and
`loopx pr-review`.

The reviewed repository is the caller's current GitHub project by default, as
resolved by `gh`, or the explicit `--repo owner/repo` target. LoopX's own
repository may be used for dogfood and public fixtures, but the command is not
LoopX-repo-specific.

The default command is read-only. An explicit `--observation-state-file`
writes only its local public-safe checkpoint. Neither mode approves reviews,
posts PR comments, merges, pushes, spends LoopX quota, or completes LoopX todos.

The built-in `pull-request-review` capability adds an optional autonomous
observation to this same command. It reuses the existing GitHub scan and
normalized review queue; it does not introduce a second crawler or a new write
authority.

The capability is also registered with the standard machine configuration
surface. Open Dashboard → machine settings (or use `loopx machine-config
describe`) and edit the `Pull-request review` capability. The select field is
stored under the typed `pull_request_review` namespace and is read by
`loopx pr-review` whenever `--review-priority` is omitted. Preview/apply is
revision-locked and readback-verified like every other machine capability;
removing the namespace returns to the default `other-developers-first` mode.
This setting changes queue order only and never grants review, comment, Todo,
push, or merge authority.

The capability also owns the review-depth contract. The shared
`agent_response_contract.review_execution_contract` defines required evidence,
completion, freshness, finding, and verdict rules. Each actionable PR carries a
compact `review_plan` that binds those rules to one exact head and marks
code-symbol and negative-walkthrough applicability. Inventory-only rows expose
no executable review artifacts. Host skills route and publish this packet; they
must not maintain a second explanation checklist.

Codex agents should use the dedicated `loopx-pr-review` skill for this slash
command. Do not route `/loopx-pr-review` through the broader `loopx-project`
workflow or the merge-focused `loopx-pr-merge` skill.

## Command

| Command | CLI reference | Intent |
| --- | --- | --- |
| `/loopx-pr-review` | `loopx pr-review [--repo owner/repo] [--target-exact-head NUMBER@HEAD_OID] [--state open\|merged\|all] [--review-priority other-developers-first\|owner-first] [--since ISO] [--fresh-audit-exact-head NUMBER@HEAD_OID]` | Review a small explicit batch with repeatable `--target-exact-head`, or list a lifecycle queue when no target is supplied. Both paths provide concrete main-regression analysis and the five-block review contract. The default queue prioritizes non-owner developer PRs; `owner-first` opts into owner priority. `--fresh-audit-exact-head` separately forces new evidence for an unchanged concluded head. |
| pre-merge readback | `loopx pr-review --goal-id GOAL --repo owner/repo --check-merge-readiness NUMBER@HEAD_OID` | Immediately before merge, fail closed unless the remote PR is still open at the reviewed head, its standalone conclusion approves that head, all checks are successful or skipped, review-thread pagination is complete with no unresolved thread, and merge state is compatible. The Goal-scoped command records a compact public-safe readiness observation; this read grants no merge authority. |

The slash command must run the CLI first. Agentloop must not reconstruct the
review window by manually calling `gh pr view` / `gh pr list` for every PR. The
CLI packet's `review_groups.unmerged`, `review_groups.merged`, and
`review_sequence` are the authoritative executable queue. Non-null
`pull_requests[].review_template` and `evidence_commands` are for the second
step: reading one selected actionable PR deeply.
Use the JSON form for the first pass so the response contract and per-PR blank
templates enter the model context:

```bash
loopx --format json pr-review [--repo owner/repo] [--since ISO]
```

The ordinary queue defaults to open PRs. Use `--state merged` or `--state all`
explicitly only for a lifecycle or post-merge audit; named exact targets remain
lifecycle-neutral when `--state` is omitted.

When the user explicitly names one or a few PRs, resolve each current head and
request only those exact heads. This direct path is complete for the named
targets and must not be expanded into a historical queue merely to satisfy
queue completeness:

```bash
loopx --format json pr-review --repo owner/repo \
  --target-exact-head 4868@0123456789abcdef0123456789abcdef01234567
```

The option is repeatable. A remote head mismatch fails closed. Use
`--fresh-audit-exact-head` in addition only when an unchanged target already has
a valid conclusion and the user explicitly requests new evidence.

The live source scan keeps the list query lightweight and enriches each PR's
nested commits, reviews, and checks with a bounded pool of concurrent
`gh pr view` reads (at most eight at a time). Results are reassembled in list
order, and a failed detail read still remains visible as an incomplete source
scan, so the latency improvement does not change queue ordering or freshness
semantics. The scan does not use a stale cache: rerunning the command always
re-reads the requested GitHub window.

For an autonomous maintainer monitor, request the complete open queue while
persisting its compact cursor in an ignored local checkpoint:

```bash
loopx --format json pr-review --repo owner/repo --state open \
  --autonomous-observation \
  --observation-state-file .local/pr-review-monitor.json
```

The same command reuses that checkpoint on later Codex tasks. Its atomic local
write grants no GitHub, Todo, push, or merge authority.
`--previous-observation-json` remains available for stateless callers and is
mutually exclusive with `--observation-state-file`:

```bash
loopx --format json pr-review --repo owner/repo --state open \
  --autonomous-observation \
  --previous-observation-json previous.json
```

After the selected candidate has been durably materialized as a Todo,
acknowledge that projection explicitly:

```bash
loopx --format json pr-review --repo owner/repo --state open \
  --autonomous-observation \
  --observation-state-file .local/pr-review-monitor.json \
  --projected-exact-head 2768@0123456789abcdef0123456789abcdef01234567
```

Supply `--projected-exact-head` only after the candidate's exact target key has
been durably materialized as a Todo. Candidate emission is a preview, not a
projection acknowledgement. Without that explicit ACK, repeated complete polls
replay the same candidate so a failed or interrupted Todo write cannot strand
the PR. The option is repeatable and may only acknowledge the prior candidate
or an already persisted projection cursor.

After the selected candidate has an externally verifiable review or
merge-readiness result at that exact head, advance the queue with an explicit
handled cursor:

```bash
loopx --format json pr-review --repo owner/repo --state open \
  --autonomous-observation \
  --observation-state-file .local/pr-review-monitor.json \
  --handled-exact-head 2768@0123456789abcdef0123456789abcdef01234567
```

`--handled-exact-head` is repeatable and uses `NUMBER@HEAD_OID`. The observation
persists these public-safe cursors in `handled_exact_heads`. Candidate emission
alone is not a completion receipt: callers must add the cursor only after
review-result readback proves that exact head was handled. A newly supplied
cursor must match the prior packet's candidate or one of its
`projected_candidate_exact_heads`; a caller cannot skip an unselected PR by
naming it handled. A new head is a new candidate even when the prior head was
handled.

`pending_candidate_exact_head` preserves the last selected but unhandled exact
head across unchanged and incomplete polls. It is a scheduling cursor only;
callers still deduplicate Todo creation by exact target key and must not treat
the cursor as evidence that a review happened.

`projected_candidate_exact_heads` persists every candidate whose durable Todo
projection has been explicitly acknowledged but not yet completed. An unchanged
poll skips those acknowledged exact heads and selects the next unprojected,
unhandled PR in the capability-ranked review sequence. Legacy v0 observations treated
emission as projection; v1 deliberately replays their candidates so stale
emission cursors cannot strand unreviewed PRs. Todo target-key deduplication
keeps this recovery idempotent.
When every actionable PR has already been projected, `candidate` is `None` and
`pending_candidate_exact_head` remains the last pending cursor. A material
transition on an already projected exact head still re-selects that head.

`review_backlog` gives the monitor a compact workload cadence hint. It counts
open, non-draft PRs whose exact head is actionable and not yet recorded in
`handled_exact_heads`, and returns `recommended_poll_interval_minutes`. While at
least one unhandled PR remains, the recommendation is `3`; once the actionable
backlog is empty, it drops to `15`. The hint is scheduling evidence only: it
does not grant Todo, review, comment, or merge authority, and callers still
advance the queue with an explicit handled cursor after exact-head review
readback.

`pull_request_review_queue_observation_v1` has exactly three observation
states:

- `not_observed`: the source or packet slice was incomplete. Preserve the
  previous baseline and do not claim the queue is unchanged.
- `observed_unchanged`: a complete observation has the same queue fingerprint.
  An unacknowledged packet candidate is replayed. After the caller supplies its
  projection ACK, the packet selects the next unprojected, unhandled backlog PR
  so the queue keeps rotating. An acknowledged unhandled candidate remains in
  `projected_candidate_exact_heads` until the caller supplies its completion
  cursor.
- `material_transition`: a complete observation changed an exact head, review
  conclusion, check state, draft state, mergeability, or open-queue membership.
  A new head following `REQUEST_CHANGES` may use one fast-feedback slot;
  check-only activity does not preempt older review-ready work.

The repository-scoped fingerprint contains only compact public PR metadata.
Persisted `items` carry the PR number, fingerprint, exact head, decision, and
next action; they never carry review bodies.
`pull_request_review_scheduling_policy_v1` owns the stable queue order. The
`--review-priority` switch selects the actionable ordering:

- `other-developers-first` (the default) reviews actionable PRs whose author
  differs from `request.reviewer_login` before the authenticated developer's
  own PRs;
- `owner-first` restores the authenticated developer's own PRs before other
  developers' PRs.

The capability does not infer organization membership or trust from GitHub
metadata; “other developer” is strictly an author-identity comparison. The
selected mode is carried in `request.review_priority`,
`scheduling_policy.review_priority`, and autonomous observations, so changing
the switch is an explicit queue transition rather than hidden local state.

Within either mode, the queue order is:

1. the mode-selected actionable author group;
2. the other actionable author group, with community response heads pushed
   after an independent `REQUEST_CHANGES` review and community exact heads
   waiting at least 24 hours;
3. remaining actionable work in current-head `review_ready_at`, creation-time,
   and PR-number order;
4. current heads that already have a conclusion, followed by merged, draft,
   and closed rows.

Community feedback and aged backlog share one age-fair tier. On a material
transition, at most one newly pushed community response head may take a bounded
fast-feedback slot after the mode-selected actionable group. `updatedAt` does
not define readiness because comments and checks must not make old code look
new. Only an explicit PR selection in the current user request may override the
next item's ordering for that request; it does not override the selected row's
`review_action_kind` or exact-head idempotency. Todo text, monitor notes, and
one-off author filters must not replace the capability policy. Projected
candidates remain skipped until handled or their exact head materially changes.

The command packet keeps inventory and execution queues distinct.
`pull_requests` and each group's `pr_numbers` retain every row in the requested
window for compact conclusion readback. Top-level and group `review_sequence`
contain only rows whose `review_action_kind` is non-null. A merged exact head
without a valid conclusion receives `audit_merged_pull_request_exact_head`; a
merged or open exact head whose valid conclusion is not an approval remains
inventory-only and cannot become the recommended first PR. An open exact head
with a valid approval owes `qualify_pull_request_merge_readiness` until the Goal
has observed its current readiness material state. An unchanged observation
suppresses duplicate qualification work; exact-head, base, review conclusion,
configured CI, review-thread, merge-state, or draft-state changes reopen it.
This preserves the typed readiness verdict rather than relying on GitHub's
review state: the platform blocks self-approval, so an author-owned approval is
recorded as `COMMENTED` and a state-based rule could otherwise count a
still-unmerged head as concluded even after it goes behind, conflicts, loses
its checks or is blocked. The summary's attention counts are derived from this
same actionable set. Inventory-only rows set `review_plan` and
`review_template` to null and `evidence_commands` to an empty list so hosts
cannot mistake readback metadata for execution authority.

It emits a
`pull_request_review_todo_preview_v0` bound to its exact head. The preview may
route to initial review, re-review after changes, or merge-readiness
qualification. It grants no Todo write, GitHub review/comment, push, or merge
authority; callers must use normal LoopX Todo authority, `loopx-pr-review`, and
`loopx-pr-merge` policy for those actions.

Do not pipe that first packet through `jq` or another projection that only
keeps `.summary` and `.review_sequence`; that drops
`agent_response_contract`, `scheduling_policy`, `review_groups`, and the
non-null `pull_requests[].review_template`, `pull_requests[].review_plan`, and
`pull_requests[].evidence_commands`, which are the fields that make an
actionable row a guided review instead of a statistics table.

## Capability-Owned Review Execution

`pull_request_review_execution_contract_v2` is shared once per packet to avoid
duplicating a large prompt for every PR in a 100-item queue. It requires these
typed evidence groups before a verdict:

- problem context and active caller;
- architecture and ownership flow;
- repository reuse for behavior-bearing changes: search base and exact-head
  code, including unchanged siblings, by caller outcome/resource rather than
  only new filenames. Record revisions, queries, paths and candidate callers;
  compare scope/filters, ordering/paging, authority/sanitization and state/retry
  ownership. Prefer the nearest existing owner; justify independent boundaries
  with evidence, not green CI or conflict-free coexistence. For alternative
  views of one resource, validate consumer switching and concurrent updates
  where relevant. Before accepting introduced or newly enforced state, classify
  it as an authoritative fact, irreducible intent, derived projection or
  diagnostic hint. Trace whether existing canonical state can derive the
  outcome before adding another declaration to maintain. Require the actual
  producer, triggering workflow, authoring discovery and update/retirement
  owner; a generic JSON writer plus a hand-filled fixture proves transport,
  not an ordinary workflow. Intent such as consent or an alternative relation
  cannot be invented from unrelated records. Repeat the comparison after base
  integration or head changes;
- caller-observable semantic parity for every behavior-bearing change, whether
  or not its title says refactor or migration. Inventory legacy caller branches,
  then run the same synthetic fixture through the public entrypoint and affected
  backend at an immutable baseline and the exact head. Record replayable
  commands, revisions, fixture and observation fingerprints, exit status, full
  diagnostics, persisted readback, authority/effect outcomes, and only the
  normalization rules needed for documented nondeterminism. The sensitivity
  case must make the real path fail on the historical defect or a deliberate
  dropped-field/detail or stronger-precondition mutation, then pass at the fixed
  head. New-rule provider conformance and prose-only claims do not establish
  before/after compatibility. When state/projections drive behavior, vary only
  redundant annotations, display order/pagination and unrelated-item count
  beyond display caps, holding authoritative facts fixed. Admission must not
  change merely because a record falls off a diagnostic page. Check completed,
  superseded and archived references against the actual acceptance/lifecycle
  contract; source incompleteness is not proven absence. Do not confuse a real
  intent or priority change with a presentation-only counterfactual;
- exact changed-line classification across production, tests/fixtures, docs,
  generated output, and mechanical moves;
- a 2-5 item exact-head symbol map for code-changing PRs, including caller,
  state, branch, side effect, consumer, and failure ownership;
- positive and applicable negative execution walkthroughs;
- validation tied to changed invariants and failure cases;
- strongest regression path, blast radius, recovery, minimum repair, and
  regression test;
- code-volume necessity and the highest-value behavior-preserving
  simplification;
- change proportionality: compare the verified frequency, severity, blast
  radius, and recovery cost of the original problem with the production
  mechanism, new state/contracts/CLI/callers, migration, and long-term
  maintenance surface. Correctness, green CI, and resolution of earlier
  findings do not override a `disproportionate` or `not_yet_proven` blocker;
- default-off isolation: for an opt-in change, trace every shared schema,
  prompt, accepted-input, projection, scheduling, and effect surface. Include
  installed or automatically loaded skills, agent instructions, prompt
  templates, help, schemas, install bundles, and provider setup guidance:
  runtime `default=false` is insufficient when one of those baseline surfaces
  already changes model or user behavior. Separate availability signals such
  as installation, discovery, provider readiness, accepted input, and resolver
  success from activation authority. For scoped capabilities, prove that the
  intended scope and every required subject are enabled before projecting
  capability-specific guidance or effects, then run a paired counterfactual
  proving that disabled behavior still matches the pre-change contract;
- authority semantics: make public protocol ids and symbols match the real
  actor lifecycle and authority, distinguishing ephemeral sub-agents from
  registered peers and durable multi-agent coordination.

Every materially expanded re-review resets proportionality from the original
problem and evaluates the full exact head. Reviewer-requested additions are not
progress toward approval by themselves; the reviewer should request the
smallest viable fix, deletion, split, or hold when the benefit does not justify
the accumulated mechanism.

### Goal-oriented delivery judgment (policy revision 6)

Every actionable review now extends the existing `problem_context` evidence
with `goal_basis` and a typed `verdict`. This applies to code, docs, maintenance
and test-only changes. Resolve the current user request, issue/task, accepted
contract or demonstrated regression before accepting the author's narrowed
scope. A public roadmap id is optional; do not copy private goal content or
require another repository to use LoopX's S/G/R identifiers.

| Delivery verdict | Meaning and additional evidence | Approval effect |
| --- | --- | --- |
| `goal_achieved` | Existing before/after, observable outcome and non-goals prove the named task's acceptance; no invented successor required | May approve within that scope, without claiming the parent program complete |
| `justified_increment` | Useful delivered delta, plus `remaining_gap`, `next_step` (owner/dependency) and `boundary_reason` for independent review, verification and rollback | May approve a prerequisite, research, docs or maintenance increment without shipping the entire feature |
| `off_goal` / `fragmented` / `not_yet_proven` | `reason` and `minimum_repair` explain the mismatch, avoidable premature stop or missing evidence | Blocks APPROVE even when other evidence and checks pass |

Reuse `problem_context.observable_outcome`, `walkthroughs` and
`validation_matrix` instead of writing another evidence report. Small diffs are
not fragmentation; a new field or sent message is not proof of a promised
end-to-end user journey. For collaboration, follow the required dependency,
receiver adoption, artifact acceptance and return through real callers.
Independent prerequisites and characterization remain valid when their boundary
and real successor are justified. Do not reward larger diffs or fabricated
follow-ups. The checker validates declared evidence consistency, not whether a
reviewer's semantic judgment is true, and it never settles a Goal.

Revision 6 changes review requirements, not queue selection, scheduler, runtime
permissions or the wire schema. Revision-5 results must be regenerated and
reviewed under the new policy before current approval. The five public review
sections remain, but `word_hint` no longer suggests fixed lengths: scale prose
to the change, reuse evidence, and do not pad simple reviews. This capability
is not behind a new feature flag; invoking review uses the installed policy.

### Semantic alignment and CI constraint recovery

The semantic triage introduced in policy revision 5 replaces universal detailed
semantic review with bounded triage for code and behavior-bearing policy changes. The required row starts
with `checked_scope`, `impact_reason`, and `verdict` (plus the standard evidence
`status`). Review the full diff and relevant definitions/callers, then stop at
`not_applicable` if no shared contract is affected. No candidate value, separate
report subsection, full RFC read, or hypothetical repair is needed on this path.
A changed-file preview or an unchanged registry alone cannot establish no impact.

| Verdict | Additional evidence | Approval effect |
| --- | --- | --- |
| `not_applicable` | None; scope and reason explain the absence of shared contract impact | No semantic blocker |
| `aligned` / `new_semantics_justified` | `candidate_decision`, `affected_contract`, `evidence_refs` to existing review evidence | No semantic blocker |
| `advisory` | `candidate_decision`, `analysis_limit`; the impact reason explains why no affected current obligation lacks required evidence | Reports a bounded-analysis gap, without claiming safety or blocking on that gap alone |
| `not_yet_proven` / `violated` | `candidate_decision`, `affected_contract`, `trigger`, `observed_evidence`, `minimum_repair`, `validation_commands` | Blocks approval for an affected current obligation with missing required evidence or a concrete violation |

For shared states, owners, consumer domains, projections or persistence changes,
read only the affected base/head contracts. Reuse `repository_reuse`,
`observable_semantics`, and `validation_matrix` evidence instead of repeating
it. Docs-only reviews may supply the same row when they find contract impact;
a supplied row is checked even when not required by the plan. A generated
inventory change alone does not require a detailed semantic review.

The generic capability does not infer impact from repository-specific paths or
inject a repository's check names into another repository's packet. Resolve
obligations from the target repository's current policy and use the existing
validation matrix. Observed check runs do not establish which checks are required.
Green CI proves only the checks that ran, not whole-program convergence.

The repair map below is a **LoopX repository example**, not a policy for every
`--repo`. In LoopX, verify required checks against `.github/GOVERNANCE.md` and the
current CI configuration: `Sign-off` and `merge-gate` are the documented checks;
the semantic smoke runs through Python tests. Full Public Smokes is a
post-merge/scheduled surface. These facts are not copied into generic packets.

Use this repair map when a check fails:

| Failure family | What it means | Minimum repair | Do not repair by |
| --- | --- | --- | --- |
| `Sign-off` | One commit in the PR range lacks a valid DCO trailer | Add `Signed-off-by` to every affected commit with `git commit --amend -s` or an equivalent history repair; verify the full range | Signing only the newest commit |
| semantic smoke: unregistered value | A recognised carrier/field form introduced a value outside the registry | Reuse the existing owner value, or add the value with its owner, slot, scope, tests, and RFC evidence | Registering an unrelated string to silence the error |
| optional inventory report: stale or missing | A developer-requested export differs from the current tree; semantic CI does not read it | Rerun `uv run python scripts/generate_semantic_inventory.py --output <path>` if the report is needed; semantic failures require fixing the reported rule | Committing the census, editing counts by hand, or replacing the full-tree scan with a diff-only scan |
| semantic smoke: owner/parity | A defining symbol or Python/TypeScript value set diverged | Restore the single owner or deliberately update both runtime owners with parity evidence | Adding a second silent authority |
| semantic smoke: projection | A source value is unmapped, mapped to the wrong target, or should be rejected explicitly | Update the declared mapping and executable owner together, then test the boundary case | Deleting a source value without compatibility analysis |
| semantic smoke: budget/anchor | Measured debt grew or the guard was weakened | Fix the underlying duplicate/coverage issue and lower a budget only when the measured debt really fell | Raising the budget, narrowing the scan root, or renaming to hide drift |
| `merge-gate` | A required upstream CI job failed, was skipped unexpectedly, or has incomplete qualification | Inspect `needs` and the failing job, fix the owning path, and rerun at the same head | Treating a local smoke as proof that the remote gate is complete |

A blocker must connect the exact-head change to an existing obligation and a
replayable failure or missing required validation. An untraceable dynamic value
alone is an `advisory`, not `not_yet_proven` and not proof of safety. Removing a
persisted value without required old-state readback is `not_yet_proven`; a failed
required projection check is `violated`. Neither can be downgraded to advisory
to bypass CI or a concrete blocking finding. Do not turn future or advisory RFC
properties into current merge obligations. Result checking validates declared
consistency, not the truth of a reviewer's classification.

**中文边界：** 普通改动只填检查范围、影响理由与结论，无共享契约影响时用
`not_applicable` 结束，无需虚构候选值或补齐整张证据表。影响共享状态、owner、
消费者、投影或持久化时，按受影响契约检查并引用已有证据。扫描器能力不足用
`advisory` 报告；本次修改缺少现行契约要求的验证用 `not_yet_proven`，明确违规用
`violated`，后二者阻断批准并给出契约、触发修改、观察证据、最小修复与复验命令。
其他仓库使用自己的 CI 与契约规则，不能继承 LoopX 的检查名。

A matched model evaluation should preselect ordinary edits, legal contract
extensions, and real contract defects; fix tasks, model/version, tools, seeds
where supported, and total budgets across policies. Count review and repair
within that budget. Compare independently accepted completions, tokens, elapsed
time, false blocks and missed defects; report uncertainty and repeat stochastic
runs. Deterministic consistency fixtures establish the boundary, not model
benchmark uplift or non-regression.

The per-actionable-PR `pull_request_review_plan_v1` records the exact target,
applicability, required evidence ids, and an initially `unverified`
`pull_request_review_result_v1` skeleton. Metadata, labels, file counts, risk
hints, and green CI cannot upgrade evidence to `verified`. A stale-head verdict
is prohibited. Missing evidence remains `unverified` with a reason instead of
being replaced by confident prose.

`repository_reuse` starts unverified in every applicable plan. Its conclusions
are `reused`, `separation_justified`, `no_existing_candidate`,
`unjustified_duplication`, or `not_yet_proven`. A negative search must name its
scope and limitations; an empty candidate list is not proof of absence.
Unjustified duplication or missing evidence requires a request-changes
conclusion, including missing state derivation, producer/trigger or completeness
proof in `state_model_assessment`. The same existing gate applies; there is no
second semantic classifier. Similar-looking code with distinct invariants or
compatibility needs may legitimately remain separate. Ordinary docs retain their existing
review path; smoke-only changes retain `durable_smoke_value` coverage review.
This is a reviewer-executed contract projected by the packet, not an automatic
repository search or a semantic validator of published prose. Tests establish
packet applicability and verdict policy, not guaranteed model compliance.

When `--state all` is used, the command must preserve both lifecycle groups.
The `--limit` value is applied per group so a busy open queue cannot consume the
whole packet and make `review_groups.merged` empty while merged PRs exist in the
window. The default is 100 PRs per selected group. Every packet carries
`result_completeness`; exhaustive requests must require `complete=true` and
rerun with its `recommended_limit` when the source scan or packet slice was
truncated. Live GitHub reads should fetch open and closed/merged windows
separately before constructing the grouped packet.

The agent response must not stop at a queue table. For `/loopx-pr-review`, the
queue is only the preface; the final answer should review selected PRs one by
one with five sections: `动机`, `改动思路`, `具体改动`, `对主干的风险`, and
`我的整体评价`. A stats/list-only response is valid only when the user
explicitly asks for stats or a list without review. When the visible message
starts with `/loopx-pr-review`, words such as `open`, `closed`, `merged`,
`today`, or a time window are filters on the review queue, not permission to
skip the review. Downgrade only for explicit opt-out phrases such as `只统计`,
`只列出`, `stats only`, `list only`, `不要 review`, or `不用分析`.

The published review is a full-PR bilingual review: one complete Chinese
five-block review covering every changed surface, key symbol, positive and
negative path, and validation, plus one concise English machine verdict
(`APPROVE`, `REQUEST_CHANGES`, or the author-owned `COMMENTED` fallback). The
Chinese review carries the depth and evidence; the English verdict carries the
machine-readable state and validation summary. A findings-only or blocker-only
body is not a complete PR review.

Each complete PR review must also include whole-PR interpretation depth:
per-file responsibility mapping, 2-5 key symbol explanations with exact-head
references, one positive runtime walkthrough, one negative/fail-closed
walkthrough, per-surface validation, and an overall judgment for the entire PR.

## Source Reads

Implementations may read compact public PR surfaces:

- pull request title, number, URL, branch, author, lifecycle state, merge time,
  and review decision;
- PR body summary;
- changed-file list and diff scale;
- status-check rollup;
- merge-state metadata;
- current-head commit timestamps and review metadata used to derive
  `review_ready_at` and validate a standalone exact-head conclusion.

Raw review bodies are used only for the format/exact-head decision and are not
returned or persisted. A valid latest conclusion names the exact head, contains
all five Chinese sections plus a line-starting `English verdict: APPROVE` or
`English verdict: REQUEST_CHANGES`, and keeps that verdict aligned with formal
`APPROVED`/`CHANGES_REQUESTED` state. Because GitHub blocks every self-review
state transition, author-owned conclusions use `COMMENTED` plus one exact title:
`Approval conclusion (author-owned PR; GitHub blocks formal self-approval)` or
`Request changes conclusion (author-owned PR; GitHub blocks formal self-review)`.
The compact result is versioned as `pull_request_review_conclusion_v0` and
reports a typed verdict and invalid-reason codes.

`pull_request_merge_readiness_v0` is a separate, read-only last-mile gate. It
re-reads the named PR instead of trusting a saved review packet. In particular,
GitHub may retain or reassociate an approval after an update-from-base commit;
the gate still requires the public review body to name the observed exact head.
For check-runs with a reliable workflow/job identity and start time, it evaluates
only the latest attempt and reports raw and superseded counts; ambiguous rows are
retained so the gate fails closed. It also rejects missing, pending, failed, or
unknown effective checks and incomplete or unresolved review threads. An admin
bypass may satisfy GitHub's author-owned
self-review limitation, but it never overrides this capability gate or supplies
user merge authority.

`pull_request_merge_readiness_observation_v0` is the Goal-scoped scheduling
receipt for that gate. It persists only the exact-head material fingerprint and
compact public-safe readiness result under the local Goal runtime. It excludes
review bodies, raw logs, credentials, private payloads, and local paths. Queue
construction consumes the observation only when every readiness input still
matches; a changed head, base, review conclusion, CI policy/result, review
thread, draft flag, merge state, or PR state fails open to a fresh
qualification.

They must not include raw logs, private connector payloads, credentials, local
absolute paths, private source bodies, or hidden CI artifacts.

## Response Shape

`loopx_pr_review_command_response_v0`:

```json
{
  "schema_version": "loopx_pr_review_command_response_v0",
  "request": {
    "schema_version": "loopx_pr_review_command_request_v0",
    "command": "/loopx-pr-review",
    "cli_command": "loopx pr-review [--repo owner/repo] [--state open|merged|all] [--review-priority other-developers-first|owner-first] [--since ISO]",
    "repository": "owner/repo",
    "limit": 100,
    "state_filter": "all",
    "since": "2026-06-28T00:00:00Z",
    "review_priority": "other-developers-first",
    "window": {"state_filter": "all", "since": "2026-06-28T00:00:00Z"},
    "source": "github_cli",
    "privacy_mode": "public_safe_github_metadata",
    "dry_run": true
  },
  "result_completeness": {
    "schema_version": "pr_review_result_completeness_v0",
    "complete": true,
    "truncated": false,
    "limit": 100,
    "source_scan_complete": true,
    "recommended_limit": null,
    "rerun_cli_args": []
  },
  "scheduling_policy": {
    "schema_version": "pull_request_review_scheduling_policy_v1",
    "identity_basis": "request.reviewer_login",
    "review_priority": "other-developers-first",
    "owner_first_active": false,
    "other_developers_first_active": true,
    "community_backlog_age_hours": 24.0,
    "ordered_tiers": [
      {"tier": 0, "id": "other_developer_feedback_and_aged_backlog"},
      {"tier": 1, "id": "other_developer_remaining"},
      {"tier": 2, "id": "authenticated_developer_owned"}
    ]
  },
  "summary": {
    "headline": "8 PR(s) in review window: 3 open, 5 merged; 8 need review attention.",
    "total_pr_count": 8,
    "open_pr_count": 3,
    "merged_pr_count": 5,
    "review_attention_count": 8,
    "post_merge_review_count": 5,
    "draft_count": 0,
    "recommended_first_pr": {
      "rank": 1,
      "number": 773,
      "review_depth": "docs_and_smoke_review"
    }
  },
  "review_sequence": [
    {
      "rank": 1,
      "number": 773,
      "title": "docs: add newcomer command path",
      "url": "https://github.com/owner/repo/pull/773",
      "state": "OPEN",
      "review_depth": "docs_and_smoke_review",
      "risk_hint_level": "low",
      "main_risk_level": "low",
      "scheduling_lane": "authenticated_developer_owned",
      "scheduling_tier": 0,
      "why_now": "Open and awaiting reviewer decision."
    }
  ],
  "review_groups": {
    "unmerged": {
      "schema_version": "pr_review_group_v0",
      "group_id": "unmerged",
      "title": "Unmerged PRs",
      "intent": "Review before merge: decide approve, request changes, defer, or wait for checks.",
      "count": 3,
      "pr_numbers": [773, 775, 771],
      "review_sequence": []
    },
    "merged": {
      "schema_version": "pr_review_group_v0",
      "group_id": "merged",
      "title": "Merged PRs",
      "intent": "Post-merge audit: check outcome, regression risk, and follow-up quality without blocking already-merged work.",
      "count": 5,
      "pr_numbers": [770],
      "review_sequence": []
    }
  },
  "pull_requests": [
    {
      "number": 773,
      "head_oid": "0123456789abcdef0123456789abcdef01234567",
      "review_template": {
        "schema_version": "pr_review_five_block_template_v0",
        "purpose": "Empty scaffold only; agentloop fills it after reading PR body and diff.",
        "sections": [
          {
            "label": "动机",
            "word_hint": "按证据需要；无最低字数",
            "content": "",
            "agent_instruction": "解释旧行为、具体痛点、受影响的用户或调用方、目标结果与必要性；说明不合并会继续付出什么代价，以及需求来自活跃调用方还是未来设想。"
          },
          {
            "label": "改动思路",
            "word_hint": "按证据需要；无最低字数",
            "content": "",
            "agent_instruction": "解释所选架构、改动前后的控制流或数据流、所有权边界、关键不变量和替代方案取舍；为不熟悉子系统的读者给出一条正向运行链路。"
          },
          {
            "label": "具体改动",
            "word_hint": "按证据需要；无最低字数",
            "content": "",
            "agent_instruction": "把关键文件和符号映射到行为，覆盖接口、配置或状态、兼容路径、测试与文档；说明各部分如何协作，并给出一个具体输入到输出的例子。"
          },
          {
            "label": "对主干的风险",
            "word_hint": "按证据需要；无最低字数",
            "content": "",
            "agent_instruction": "按严重度列出有文件或符号证据的发现，评估爆炸半径、兼容性、权限、默认副作用、失败与回滚、可观测性和缺失覆盖；策略或生命周期改动必须解释一条负向链路。"
          },
          {
            "label": "我的整体评价",
            "word_hint": "按证据需要；无最低字数",
            "content": "",
            "agent_instruction": "权衡价值与复杂度，列出实际检查或运行的验证，注明审阅的 head SHA，并给出精确结论；若阻塞，说明最小修复和复审所需证据。"
          }
        ],
        "review_order": ["docs/guides/newcomer-command-path.md", "docs/README.md"],
        "output_hint": "Write for a reader unfamiliar with the PR: explain context, architecture, implementation, validation, necessity, and risk with concrete evidence. Follow each section's range as a depth signal, not filler."
      },
      "motivation": "Adds a newcomer command path...",
      "scale": {"changed_files": 3, "additions": 90, "deletions": 4},
      "areas": {"public_docs": 3},
      "checks": {"summary": "2 successful check(s)."},
      "metadata_risk_hint": {
        "schema_version": "pr_metadata_risk_hint_v0",
        "level": "low",
        "basis": ["areas=公开文档 3", "scale=3 files +90/-4", "checks=2 pass"],
        "disclaimer": "Metadata-only hint for queue ordering; agentloop must read the PR diff before judging main risk."
      },
      "main_regression_analysis": {
        "schema_version": "main_regression_analysis_v0",
        "risk_level": "low",
        "risk_summary": "低 main regression risk across 公开文档 3; 3 file(s), +90/-4; checks=2 pass.",
        "potential_regressions": [
          "Runtime regression risk is low, but public guidance or smoke expectations can drift from shipped behavior."
        ],
        "bug_risks": [
          "Docs-only or smoke-only changes can bless stale contracts if examples no longer match the real command path."
        ],
        "verification_focus": [
          "Run `git diff --check` and the touched smoke; compare command examples with current CLI help when syntax is involved."
        ],
        "post_merge_review": false
      },
      "risk_notes": [],
      "evidence_commands": [
        "gh pr view 773 --json title,body,files,commits,headRefOid,updatedAt",
        "gh pr diff 773 --name-only",
        "gh pr diff 773 --patch",
        "gh pr view 773 --json headRefOid,updatedAt"
      ]
    }
  ],
  "agent_response_contract": {
    "schema_version": "pr_review_agent_response_contract_v0",
    "table_only_response_allowed": false,
    "slash_prefix_dominates_intent": true,
    "stats_only_requires_explicit_opt_out": true,
    "queue_table_role": "preface_only",
    "required_packet_fields_to_preserve": [
      "agent_response_contract",
      "agent_response_contract.review_execution_contract",
      "result_completeness",
      "review_groups",
      "pull_requests[review_action_kind!=null].review_plan",
      "pull_requests[review_action_kind!=null].review_template",
      "pull_requests[review_action_kind!=null].evidence_commands"
    ],
    "required_final_sections": [
      "动机",
      "改动思路",
      "具体改动",
      "对主干的风险",
      "我的整体评价"
    ],
    "explanation_depth_contract": {
      "schema_version": "pr_review_explanation_depth_v0",
      "reader_profile": "A technically curious reader who may not know this PR or subsystem.",
      "evidence_layers": ["problem", "architecture", "implementation", "validation"],
      "freshness": "Record and recheck the remote head SHA before the verdict."
    }
  },
  "boundary": {
    "raw_logs_recorded": false,
    "credential_values_recorded": false,
    "absolute_paths_recorded": false
  }
}
```

## Review Flow

Follow `review_execution_contract.decision_procedure` before writing the review:
challenge whether the design should ship, falsify its strongest material claim,
inspect the whole implementation, then reconcile the verdict. The goal is justified
acceptance, not more rejections. Read the target repository's architecture rules;
do not export LoopX-specific kernel/provider or TypeScript placement to other repos.

Before those evidence steps, apply
`pr_review_selection_execution_contract_v0`. A generic `re-review`,
`重新review`, or `复审` request selects and orders the named PR but does not force
a duplicate audit. When `review_action_kind` is null, perform only a compact
exact-head conclusion readback. A fresh audit despite a null action requires an
explicit request or concrete new concern/evidence invalidation encoded as
`--fresh-audit-exact-head NUMBER@HEAD_OID`; the regenerated actionable row must
still satisfy the complete execution contract.

A re-review has two scopes: the latest corrective diff and the complete base-to-head
PR. Reuse observations only after checking their revisions and assumptions against
changed callers, platforms, dependencies and promises. Prior approval is not reusable
evidence. In particular, replacing an OS test with a deterministic mock must not erase
the real lifecycle invariant the test was meant to prove.

After filling the existing result template, run:

```bash
loopx --format json pr-review --check-result review-result.json --packet review-packet.json
```

This opt-in local check performs no network reads, GitHub writes, state changes or
merge operations. It rebuilds requirements from the installed capability, matches
the saved exact head, and rejects an APPROVE inconsistent with required evidence or
blocking findings. A successful check is **not** proof of factual evidence, review
quality, current remote head, or merge permission. Re-read the head and publish the
human-readable evidence separately. Existing queue/monitor behavior is unchanged;
omit these two flags to use normal queue discovery. An older saved packet may need
fresh review evidence when the installed contract has advanced. The execution
contract now carries `policy_revision`, distinct from its stable wire schema;
results bind `review_policy_revision`. Missing or mismatched policy revisions
cannot certify approval. Regenerate the packet and perform the current plan,
not merely relabel old evidence. The skill rejects approval from incompatible
packets produced by expired development-runtime overrides. It may still publish
a conservative `REQUEST_CHANGES` only when that verdict explicitly identifies
the incompatible-policy evidence gap; a later approval requires regeneration.

For retained or parallel implementations, `repository_reuse.rule_ownership`
maps business rules across both reachable paths, including unchanged files.
Keeping a legacy storage writer does not justify keeping its independent
eligibility, retention, ordering or successor rules. Name actual retired rules
and justified compatibility/effect code, not a net-deletion quota. A positive
twin with shared decisions and necessary extra adapter code must remain
approvable. Verified evidence rows must fill their declared fields and structured
shapes. `symbol_map.items` obeys the declared count and item fields;
`walkthroughs.positive` plus any applicable `walkthroughs.negative` fill their
declared fields; `validation_matrix.items[].case_id` proves coverage of the
packet's typed required cases. The checker validates this completeness only,
never the truth of their contents.

Behavioral qualification lives in `tests/capabilities/test_pr_review_behavior.py`:
paired synthetic cases include valid designs as well as counterexamples. The optional
live no-tools test uses the existing Doubao transport with a runtime-injected key:

```bash
LOOPX_REVIEW_LIVE_TEST=1 python -m pytest -q tests/capabilities/test_pr_review_behavior.py -k live
```

The default model is `doubao-seed-evolving`; `LOOPX_MODEL_BEHAVIOR_MODEL` can
explicitly select another allowlisted model for comparative qualification.

It sends only public synthetic cases, never repository contents or credentials in
the prompt. Ordinary tests never contact the provider. These bounded decision tests
do not establish model-wide reliability or replace a real repository review.

The packet should let a reviewer move through PRs in order:

1. Start from `review_groups.unmerged` for PRs that can still affect merge
   decisions.
2. Then use `review_groups.merged` for post-merge audit and follow-up quality.
3. For a non-null action, use `evidence_commands`, key files, changed-file
   scale, and checks to open the actual PR body and diff. For a null action,
   stop after compact exact-head conclusion readback.
4. Execute the actionable PR's `review_plan` against
   `agent_response_contract.review_execution_contract`; keep unavailable
   evidence explicitly unverified.
5. Read `main_regression_analysis` before filling risk prose. It is the CLI's
   concrete, generated view of potential main regressions, bug risks, and
   focused validation.
6. Render the verified structured result through the blank five-block template:
   `动机`, `改动思路`, `具体改动`, `对主干的风险`, `我的整体评价`.
   Use each section's range as a depth signal for a reader unfamiliar with the
   subsystem, not as filler.
7. Treat `metadata_risk_hint` only as queue-ordering metadata. It must not be
   copied as the final risk judgement.
8. Recheck the exact head, then decide `approve`, `request changes`, `defer`, or
   `merge after checks`. Immediately before merge, require
   `--check-merge-readiness NUMBER@HEAD_OID` to return `ready=true`.

A response that only lists `Open` and `Merged` PRs, scale, and recommended next
order is incomplete for `/loopx-pr-review`; it should continue into the
per-PR five-block review cards after reading evidence.

Similarly, a response that says it ran `loopx pr-review` but used a command like
`loopx --format json pr-review ... | jq '.summary, .review_sequence'` is still
incomplete: the tool call happened, but the contract/template fields were
discarded before the agent planned its answer.

## Acceptance Checks

A first implementation is acceptable when:

- `loopx slash-commands` exposes `/loopx-pr-review`;
- `loopx pr-review` returns `loopx_pr_review_command_response_v0`;
- default live reads use the caller's current `gh` repository, while
  `--repo owner/repo` can review another GitHub project;
- omitted `--state` and explicit `--state open` keep the ordinary queue
  open-only; `--state all` includes merged PRs in the same packet, applies `--limit` per
  lifecycle group, and keeps `review_groups.merged` non-empty when merged PRs
  exist in the requested window;
- `pull_requests` remains the full bounded inventory while every
  `review_sequence` contains only rows with a non-null `review_action_kind`;
  valid concluded exact heads are never recommended for duplicate work and
  carry null plan/template plus empty evidence commands;
- `--fresh-audit-exact-head NUMBER@HEAD_OID` is the only packet-level way to
  turn an unchanged valid conclusion into an actionable fresh audit, and
  malformed, absent, or already-actionable targets fail closed;
- Goal-scoped `--check-merge-readiness NUMBER@HEAD_OID` rejects head drift, stale review
  prose, non-approval conclusions, red/pending/unknown checks, incomplete or
  unresolved review-thread evidence, and incompatible merge state, then records
  the compact observation used to suppress only unchanged requalification;
- the default limit is 100, and exhaustive requests only proceed when
  `result_completeness.complete=true`; truncated packets provide a larger
  `recommended_limit` for the next read;
- `--since` can bound an overnight or release-window review without relying on
  private chat memory;
- the response includes review sequence, changed-file scope, status checks,
  key files, risk notes, metadata-only risk hints, concrete
  `main_regression_analysis`, evidence commands, explicit
  `review_groups.unmerged` / `review_groups.merged`, and a blank five-block
  review template;
- the shared `pull_request_review_execution_contract_v2` owns typed evidence,
  completion, freshness, findings-first, and verdict policy, while every
  actionable PR has a compact exact-head `pull_request_review_plan_v1` with an
  unverified result skeleton;
- the packet includes `agent_response_contract.table_only_response_allowed=false`
  and `agent_response_contract.required_packet_fields_to_preserve` so
  slash-command agents know a table-only chat answer is incomplete;
- the slash-command catalog marks `/loopx-pr-review` as `must_run_cli_first`
  and `slash_prefix_dominates_intent`, and says manual `gh` calls are only
  per-PR deep-read commands after the CLI packet selects a PR;
- each actionable PR includes `review_template.sections` for `动机`, `改动思路`,
  `具体改动`, `对主干的风险`, and `我的整体评价`; inventory-only rows do not;
- each review template section carries a section-specific depth range, and the
  packet's explanation-depth contract requires problem, architecture,
  implementation, validation, necessity, and risk evidence instead of a generic
  long answer;
- live packets expose and recheck `headRefOid` so a review verdict is bound to
  the remote revision actually inspected;
- autonomous packets honor `request.review_priority`: the default ranks
  non-owner developer actionable work first, while `owner-first` restores
  authenticated-developer-owned priority. Community response and 24-hour
  backlog retain their age ordering within the selected mode; response
  preemption is bound to one slot and check-only activity does not change
  readiness priority;
- `scheduling_policy` is preserved as packet authority; Todo/monitor prose and
  one-off author filters cannot replace it;
- `--observation-state-file` atomically carries observation and handled cursors
  across Codex tasks without returning a local path or granting external writes;
- template sections must leave `content` empty so agentloop reads the real PR
  before writing the review;
- `metadata_risk_hint` must be repository-generic and must not special-case
  LoopX files or domains;
- `main_regression_analysis` must be repository-generic, must include
  `potential_regressions`, `bug_risks`, and `verification_focus`, and must not
  be replaced by a blank template;
- live GitHub reads and fixture-based smokes share the same schema;
- no raw logs, private payloads, credentials, local paths, or private source
  bodies are recorded.

## Configure CI waiting / 配置是否等待 CI

`pull_request_review.wait_for_ci` defaults to `true`. Machine defaults use the
existing capability editor. A Goal may override the complete review namespace;
clearing that override restores live machine defaults. Local required validation
and exact-head review/thread gates apply in both modes. Disabling CI waiting
also removes CI requests and waiting instructions; legacy supplied summaries
are diagnostic only. It grants no publication, merge, or admin-bypass authority.

```bash
loopx configure-goal --goal-id GOAL --no-pr-review-wait-for-ci --execute
loopx configure-goal --goal-id GOAL
loopx pr-review --goal-id GOAL --state all --format json
loopx pr-review --goal-id GOAL --check-merge-readiness NUMBER@HEAD_OID --format json
loopx configure-goal --goal-id GOAL --clear-pr-review-configuration --execute
```

The Dashboard capability editor exposes **Wait for CI** in machine and Goal
scopes. Save a Goal override to affect only that Goal; use inherit/reset to
restore machine defaults. The CLI packet echoes the resolved configuration.
A Goal namespace is atomic (including review priority); partial updates retain
its existing values, and a new namespace uses capability defaults.

`wait_for_ci` 默认开启，保留既有 CI 验证行为。Dashboard 的机器/目标 capability
编辑器提供“等待 CI”开关。以上命令只关闭指定 Goal 的等待，读取配置和评审载荷
可确认生效；清除完整目标覆盖后恢复机器默认。目标覆盖是完整 namespace（包括
审阅优先级），部分修改保留既有目标值，新覆盖使用 capability 默认值。关闭时不
查询、轮询或等待 CI；本地必需验证、当前提交评审、评论及权限检查仍然适用。
GitHub `BLOCKED` 只提示另需管理员授权，不授予合并权限。
