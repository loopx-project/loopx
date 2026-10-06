# Task-progress observation: design decisions and evidence

[中文](DESIGN_DECISIONS.zh-CN.md) · [Operation guide](DRIFT_SHADOW.md) · [Research RFC](../../docs/architecture/rfcs/optional-semantic-assistance-jev-v0.md)

Current implementation review: [PR #4854](https://github.com/loopx-project/loopx/pull/4854).

**Current proposal:** ship task-progress observation as an explicitly installed,
default-off tool, plus a default-off core policy (`progress_review`) that can
record its typed receipts (`shadow`) or let consecutive completed drift receipts
raise the **existing** autonomous replan obligation (`assist`). No pause, gate or
acceptance authority is added. The decision requested is whether to accept this
bounded closed loop and its recorded differential, not whether Jev has proved
useful enough to control an Agent on its own.

## Implemented functionality and observed effect

The current delivery is a usable **capture → assess → inspect** path, enabled
at an explicitly wrapped refresh call. It does not automatically observe every
native Agent session.

| Implemented functionality | Concrete effect and verification boundary |
| --- | --- |
| `drift init` binds a Goal contract, exact files and an initial checkpoint | Subsequent wrapped refreshes collect actual before/after material without hand-written artifact summaries; scope and contract still need operator selection. |
| `drift refresh` captures around the real core command | Net file/evidence changes are associated with a durable run. Original stdout and exit code are retained; all 10 live-check run records remained unchanged. Collection adds measured overhead. |
| Separate `drift drain` consumer | Jev evaluates Goal relation and evidence increment outside core transactions. All 10 requests in this run returned, but the decorative-work case was not detected. |
| Durable deduplication, request budget and revocation checks | Repeated events are not additional evidence; saved answers can survive a consumer restart without a new request. Offline tests cover duplicates, unresolved sends, changed contracts/configuration and failures; this is not full long-horizon recovery qualification. |
| Local off/shadow settings and environment-only key | Operators can enable, disable and read back the observer. Missing credentials, denied egress and request failure leave the existing Agent workflow in place. No automatic fallback judge or control action is added. |
| `drift status` and phase timings | Operators can inspect judgments, unknowns, failures and capture/assessment time. No raw private source is printed by this status surface; the record supports review, not acceptance certification. |

Validation covers 61 package tests plus 35 related core regressions (96 passing,
no skips), strict source typing and lint, documentation checks, and a built-wheel
CLI journey in an independent environment. **The observation workflow is
implemented; reliable drift detection and reduced wasted work are not proven.**

In practical terms, the tool removes the need to hand-write the selected diff
packet and makes an additional assessment inspectable; the amount of operator
time saved has not been measured. In current checks it recognized the retry
implementation and necessary failing test as related to the Goal. It did not
identify decorative renaming as drift, and it cannot certify a missing helper's
behavior. No Agent was redirected or stopped, so these runs do not measure
correction success, earlier intervention or final task-completion improvement.

This is a public-safe decision record, not a transcript or an approval receipt.
[RFC PR #4749](https://github.com/loopx-project/loopx/pull/4749) and
[Discussion #4838](https://github.com/loopx-project/loopx/discussions/4838)
preserve the public discussion. Earlier claims in that discussion are historical;
the limitations below are essential to interpreting the current proposal.

## How the proposal changed

| Question / earlier claim | Challenge or observation | Retained decision |
| --- | --- | --- |
| Can semantics detect busy work that the repeat fuse misses? | An `advanced` self-report or changed fingerprint can evade that specific repeat condition. This does not prove that the entire Agent/review/acceptance system is blind. | Investigate earlier evidence-based observation; retain existing acceptance and control authority. |
| Is Jev a strict superset of the rule? | A few constructed cases, including hand-written artifact descriptions, cannot establish that claim or a production error rate. | Drop the strict-superset claim; collect attributable before/after artifacts and preserve unknowns. |
| Should Jev replace the working Agent's judgment? | An independent read-only Agent can assess the same material too. Role separation, evidence preparation and provider choice are different treatments. | Keep the existing Agent workflow; no fallback judge is implicitly launched by this package. |
| Should every explored direction ship? | Candidate ranking experiments also depended on reducers, contexts and different Agent entrypoints. Their results do not qualify drift detection. | Only task-progress observation ships in this proposal. Other direction code, ranking reducers, selector changes and unrelated workflows are excluded. |
| Is the change just three model questions inside refresh? | Refresh has its own state-write transactions. Network failures must not interrupt those writes; repeated polling must not create repeated drift evidence. | Bounded capture around the command, inference in another process, durable event/request deduplication and historical-only results. |
| Is a delta sufficient evidence? | A new test or probe may be uninterpretable without unchanged surrounding code. | Supply both scoped checkpoints plus the delta. Do not silently truncate required context to fit a request. |
| Does a fast response justify automatic correction? | Later checks still abstained on decorative changes and disagreed on evidence increment. Capture itself also adds latency. | Keep off/shadow. High probability is not a correctness guarantee; no-new-evidence is not itself drift. |

## Current implementation: execution results

The following checks exercised this task-progress observation implementation at source revision
`2f4783bdd`. They used the installed `drift init/refresh/drain/status` entrypoints,
real Git/files, an isolated Goal fixture and real Jev API calls. They are
implementation checks on small constructed tasks, not independent production
qualification or native long-running Agent sessions. Private workspaces, raw
model traffic and credentials are not part of the public record.

Five scenarios were each run twice: implement a retry, rename an unrelated
constant, add a necessary failing test, produce a negative probe result, and
change a call whose external helper implementation is absent. The failing test
and probe actually ran. Expected labels were fixed before requests. The model
was pinned to `jev-1.13.0`, selected-label probability threshold to 0.6 and request
deadline to the default 5 seconds; input included both scoped checkpoints and
the delta. Credentials came from the consumer environment.

| Measurement | Result |
| --- | ---: |
| New requests / parseable responses | 10 / 10 |
| Timeouts / full abstentions | 0 / 0 |
| Cases with matching classifications across both repeats | 5/5 |
| Exact two-label match to fixed expectations | 4/10 |
| Client assessment median | 746 ms |
| Request-to-headers median | 646 ms |
| Synchronous capture overhead median | 334 ms |
| Whole consumer process median | 824 ms |
| Input tokens median | 1217 |

| Scenario (two equal results each) | Goal relation | Evidence increment |
| --- | --- | --- |
| Retry implementation | on_goal | new_evidence |
| Decorative renaming | unknown | new_evidence |
| Necessary failing test | on_goal | new_evidence |
| Negative probe | unknown | new_evidence |
| Missing external helper implementation | on_goal | new_evidence |

**The intended decorative-work drift case was not detected.** Four responses
left Goal relation unknown; all ten selected new evidence. The latter does not
establish verified progress: the increment dimension did not distinguish the
intended counterexamples in this batch. Results do not support automatic
correction. Stable responses and repeated agreement are not correctness proof.

Exact-label match is not production accuracy. Goal relevance is different from
verified acceptance; new code is not necessarily new verification evidence.
The retry implementation's independent passing check was outside the model's
observed packet. Labels need independent agreement before a quality study;
this run does not justify retagging outcomes after seeing the answers.

No automatic retries or local cache replays were counted as new model calls;
all ten original run records remained byte-identical. Timing phases overlap
and must not be summed. Request-to-headers includes network/server waiting,
not server-only inference time. Capture still adds synchronous overhead even
though inference runs separately. Billing, production error rates and Agent
time saved were not established. Earlier design alternatives are recorded
qualitatively above; this table reports only the current implementation run.

## Judge-method comparison: separate research evidence

An earlier controlled check compared Jev and Codex on the same ten small diff
inputs; Claude was later added to **that same set**, not a second independent
ten-case dataset. The stored results were rechecked against the evaluation
scripts. This comparison helps choose a future evaluator, but it is not a
measurement of the current `drift` CLI or its two Choice questions.

| Evaluator | Final alert matched expectation | Median measured client time | CLI-reported API duration median | Mean reported/estimated cost per case |
| --- | ---: | ---: | ---: | ---: |
| Jev `jev-1.13.0`, three Noul questions | 10/10 | 645 ms, HTTP request interval | — | about $0.00003, historical estimate only |
| Codex, requested `gpt-6-astra` / medium | 10/10 | 9.236 s, CLI process wall | — | not reported |
| Claude Haiku / medium | 10/10 | 10.187 s, CLI process wall | 9.191 s | $0.00844, CLI-reported |
| Claude Sonnet / medium | 10/10 | 5.402 s, CLI process wall | 4.191 s | $0.00271, CLI-reported |
| Claude Opus / medium | 10/10 | 8.054 s, CLI process wall | 7.019 s | $0.00528, CLI-reported |

The recorded Claude models were `claude-haiku-4-5-20251001`, `claude-sonnet-5`
and `claude-opus-5[1m]`; Jev and Codex names above are the requests in the scripts.
Claude costs are mean `total_cost_usd`, including cache accounting, not a
comparison of only `usage.input_tokens`. Jev's approximate cost used the
then-recorded input price, not a verified bill or a current price quote.

Important corrections to the original interpretation:

- **What 10/10 scores:** Jev flagged when `behavior_change < 0.5` **or**
  `serves_acceptance < 0.5`; Codex/Claude used the corresponding two booleans.
  The score compares this final flag with a predeclared expected flag. It does
  not establish that every subjudgment or probability is correct or calibrated.
- **What the third question was:** `summary_supported`, not evidence increment.
  It was not used to compute the 10/10 score. The inputs included the same
  declared summary and `tests_pass` field alongside acceptance and diff, so this
  was not a no-self-report experiment. Codex/Claude shared prompt text/schema;
  Jev used a different typed question representation.
- **Harness failures:** the initial one-turn limit produced 6 invalid Haiku
  outputs and 3 invalid Opus outputs due to the structured-output turn being
  cut off. Those are not reasoning errors. The table uses the corrected
  three-turn runs, with ten valid outputs for each Claude variant.
- **Comparability limits:** ten curated diffs, 424–1095 bytes, single runs and
  labels from the same designer; Codex ran at an earlier time. Codex was
  instructed not to use tools; Claude disabled tools. Jev's HTTP interval and
  full CLI wall time are different measurements, not a pure inference-speed
  ratio. CLI API duration is also not a server-only inference measurement.

The evidence supports **testing** a low-overhead first assessment and an
independent Agent review, not declaring Sonnet the best judge or Jev the only
model that can run frequently. Agent booleans can feed deterministic repeat
rules too; neither their shape nor a provider probability guarantees correctness.
The current implementation does not use this Noul reducer or launch a Claude/
Codex review stage. Applying its rule directly would also risk flagging useful
tests, documentation or prerequisites that do not change runtime behavior.
Before choosing it, compare candidate methods on the current evidence and
independent labels, then measure end-to-end review cost and false interruptions.

## Closed loop and recorded differential (2026-09-21, revised 2026-09-22)

The loop closes through existing LoopX contracts. The observer writes one typed
receipt per queued event (pending first, evaluated later) under the Goal
runtime; the core capability
[`progress_review`](../../loopx/capabilities/progress_review/README.md) reads
receipts through one strict schema, recomputes their drift booleans from the
typed judgments, joins them to run rows by turn identity with Agent/Todo
agreement, and in `assist` turns N consecutive completed drift receipts bound to
the **pinned** goal contract revision into the existing
`autonomous_replan_obligation` (`kind: external_progress_review_drift`). The
refresh-state writeback judges an acknowledgement against the same obligation,
so an accepted replan re-arms the trigger. The typed repeat fuse keeps
precedence; unevaluated receipts (pending, failed, abstained, stale, undecided,
ambiguous, missing or bound to another revision) break a streak that has not
formed and never dissolve one that has; unpinned `assist` raises nothing and
says so in status. Discharge follows the shared TypeScript outcome owner, which
gives this source its own policy: renamed identifiers discharge only with
evidence ids absent from every claim in the obligation window, a replayed claim
never discharges, and an evidence-linked vision path is a legal exit.

An external review of the first closed-loop version (2026-09-21) found four
defects that this revision fixes deterministically rather than by model tuning:

| Finding | Fix |
| --- | --- |
| The harness passed a `goal_id` derived from the case name into the model state, so a label could leak into the input | The model receives only operator basis fields; a test pins byte-identical requests across goal identities; the harness uses hashed goal ids |
| The `noul` rule gated on behaviour change, so an unrelated feature passed and a negative experiment was flagged | Rule v1 gates on `serves_acceptance` and `evidence_increment`, both asked about the change between checkpoints; the core recomputes the booleans and rejects inconsistent receipts |
| Receipts were required to agree with each other, not with the current goal contract | `assist` requires a pinned `contract_revision`; other revisions are stale and never counted |
| A receipt found by turn id was not checked against Agent/Todo; an unevaluated newest run dissolved the streak | Identity agreement is required, ambiguous fallbacks are unattributed, pending receipts are skipped within a bound |
| (maintainer exact-head review) The obligation carried no `progress_baseline`, so re-submitting the evaluated observation was accepted as `new_surface`/`new_hypothesis` and discharged it | The trigger binds the newest counted run's typed observation as `progress_baseline` and fires only when one exists; the real writeback now rejects the identical observation and the same hypothesis with fresh evidence ids, and accepts a new hypothesis or blocker (closed-loop regression) |
| (maintainer design review) Neutral bookkeeping rows broke the streak | Neutral classifications are skipped exactly as in the existing replan policy |
| (maintainer second exact-head review, P1) Renaming `hypothesis_id` over the same evidence ids discharged the obligation as `new_hypothesis`, and the README promised an evidence-linked vision exit that `replan_semantics.ts` did not grant to this source | The outcome owner gives `external_progress_review_drift` its own policy: `new_surface`/`new_hypothesis`/`new_probe_family` discharge only when the codec's `evidence_novel` fact is true (`progress_identity_without_new_evidence` otherwise), `fresh_vision_path_outcome` is required-any-of, and the requirements projection names both exits; the real writeback refuses the rename and accepts a `continue` vision path (closed-loop regression) |
| (maintainer second exact-head review, P1) A third pending, a failed or abstained receipt, or a run without a receipt above two drift receipts made the derived obligation disappear | Formation and persistence are separate rules over one scan: a streak forms only from gap-free evaluated drift; once formed, unevaluated transitions neither extend nor dissolve it and are reported as `unevaluated_transitions`; the baseline binds the newest typed claim in the window, so a pending claim cannot be re-submitted as the acknowledgement |
| (maintainer second exact-head review) The pin read as automatic invalidation on contract change | Documented as a manual pin; status reports `rebind_hint: newer_receipts_under_unpinned_revision` when the newest receipt is bound elsewhere; "0/7 false flags" restated as 0/6 evaluated plus one failed-closed round with no verdict |
| (incremental review of `c98a00be0`, A) Novelty was judged against the single baseline, so replaying the older complete claim of the window (or, with a pending newest claim, the previous one) discharged as `new_hypothesis` | The trigger carries every distinct typed claim of the window as `progress_window`; the codec reports `evidence_novel` against the union of their evidence ids and `observation_repeated` against their fingerprints; the outcome owner refuses a replay for this source (`progress_observation_replayed`); the real writeback refuses replaying round 1. Returning to an earlier hypothesis on genuinely new evidence stays a typed pivot |
| (incremental review of `c98a00be0`, B) `sequence` is an observer-local counter that restarts at zero for a new observer state, so `max(sequence)` chose the wrong newest receipt for `rebind_hint` and the load limit could drop the newest transitions | Receipts are ordered by the run's `generated_at`, then `recorded_at`, then `sequence` (`progress_review_receipt_order_key`) in the loader, the context and the same-transition join; sequences are never compared across observers |

`packages/loopx-jev/tests/test_closed_loop.py` runs one real `refresh-state`
sequence four ways: default `off` produces no signal; `shadow` shows receipts
and no obligation; `assist` without a pin is blocked and reports
`contract_revision_unpinned`; pinned `assist` raises the obligation, `loopx
status` shows it, a real acknowledged replan re-arms it, and one more drift
round is not enough.

The comparison harness replays a frozen matrix of 16 sequences. On the
committed v2 recording the typed fuse fired on 0/16 sequences; the `noul` signal
flagged 9/9 drift sequences at their gold round and reached the obligation on
all nine, with 0/6 false flags on the real upstream commits that completed
evaluation (the seventh failed closed and has no verdict) and no premature flags;
`choice` flagged 5/9. A second independent live run reproduced every outcome.
The earlier v1 recording flagged 6/9 and missed post-implementation churn; the
v2 wording was revised after seeing those misses on these constructed cases, so
they are not held-out evidence for the wording. Client latency was 0.74–1.49 s
median across recordings and up to 2.9 s p95; input tokens median 1890. The
[operation guide](DRIFT_SHADOW.md) tabulates these results and their limits.

## Engineering choices and alternatives

- **Optional package plus one typed core seam:** the observer, provider call and
  recordings stay in the package. The core adds a default-off capability that
  owns the policy, the receipt schema and the trigger, imports nothing from the
  package, and reads only normalized receipts. Scheduling, Todo, acceptance and
  L1 reliability-diagnostics contracts are unchanged; L1's no-outbound-endpoint
  receipt cannot certify a Jev request.
- **Environment credentials, separate opt-in:** only `TYPESAFE_API_KEY` supplies
  the live key. Having a key does not select a mode or permit egress. Missing key,
  invalid authentication, timeout, stale input and unknown answers never become
  evidence of healthy progress. The normal Agent workflow continues.
- **Two configuration layers:** the observer's local config (model, egress,
  limits, off/shadow) and the Goal's registry policy (off/shadow/assist, signal,
  threshold) editable through `configure-goal`, the chat API and the Dashboard.
  Native host hooks and Lark remain separate work. A hand-maintained contract is
  explicitly an operator export, not an assertion of canonical approval.
- **Narrow snapshots:** exact files, bounded material, explicit missing context,
  and single-writer use. No repository-wide completeness, atomic filesystem
  snapshot or author-attribution claim. Equal patches with different context are
  different evidence; unchanged observation material is not another warning.
- **Historical record, not a trigger:** preserve separate relation/increment
  labels, invalid/unknown states and currentness checks. Do not turn `on_goal`
  into acceptance or `no_new_evidence` into a fuse. Invalidated or failed
  observations do not accumulate a consecutive-anomaly count.

## Readiness ladder and merge status

The capability README's [readiness ladder](../../loopx/capabilities/progress_review/README.md#readiness-ladder)
is the operative statement. This pull request asks only for **stage 0**: a
default-off, registered capability whose `shadow` records receipts and whose
`assist` requires a pinned contract revision. The author-side conditions for
leaving Draft are met on the current head: green CI, the external review's
findings fixed deterministically, a differential that replays from committed
recordings, bilingual documentation and no new authority. Whether to merge a
control-plane change is the maintainer's decision; it is never self-merged.

Stages 1 and 2 are operator studies made with the tool itself: shadow with
labels on real Goals, then a single pinned `assist` Goal whose acknowledgements
are read. Only a separately authorized intervention study can establish reduced
wasted work or safe escalation and pause, which this capability does not provide.

Stopping or retaining the existing workflow is a valid result. The original M0
RFC intake remains discussion intake; no research, provider, spend or control
approval is inferred from that earlier decision.

## Q1–Q4 study-selection brief (2026-10-06)

**Contributor recommendation: defer M1, while keeping D7 as the only candidate
for the next owner decision.** The RFC places same-priority Todo ordering first
in its qualitative investigation order, and current contracts expose a
bounded, non-binding choice. Neither fact demonstrates that an independent
assessment improves accepted outcomes over the normal Agent workflow. The
recorded observer runs and judge comparison are constructed, small, and not a
held-out D7 evaluation. This is a recommendation, not an owner decision or
authorization to run a study.

| Decision | Proposed scope | Evidence or unresolved gate |
| --- | --- | --- |
| **Q1 — caller** | D7 only: an Agent or steward choosing the next Todo from one current, policy-equivalent, same-priority eligible cohort. Do not include D8 in the same study. | The RFC's §3.1 ranks D7 first. The integrated start contract keeps the same-priority tie-break at planner order then Todo write order; action-selection recommendations are non-binding. There is no measured D7 outcome set or evidence that the normal Agent misses valuable choices. |
| **Q2 — finite question and checkpoint** | For one fixed decision window, does adding a read-only assessment before dispatch improve independently verified accepted-goal contribution from the normally selected eligible cohort, compared with normal Agent selection? Freeze the cohort, goal/dependency evidence, selection checkpoint and work window before assignment. | Current evidence identifies the selector and legal cohort, but does not define a public, independently labeled D7 dataset or an agreed number of decisions. The owner must select that finite sample before any result is observed. |
| **Q3 — comparator and rubric** | Preserve the normal Agent, tools, reviewer and deterministic rules in every arm. Compare normal selection with the same workflow plus (a) an existing-model read-only assessment and (b) Jev, using identical candidate facts, question semantics and effort limits; blind independent adjudication to the arm. Primary outcome: independently accepted goal contribution within the fixed work window and total budget. Report false-positive selections, missed higher-value selections, latency and total assessment cost separately. | Freeze eligibility/authority violations as hard failures: no arm may add an ineligible Todo, cross priority, bypass dependencies/holds/claims/quota, change canonical order, or make the recommendation binding. Do not collapse false positives and false negatives into a weighted score until the product/domain owner records their relative cost. Require independent labels before any result is revealed. |
| **Q4 — data, destination and spend** | For this decision brief: public source and synthetic evidence only; zero new provider calls and zero external-study spend. | No selected data owner, destination, pinned live model, call cap, dollar cap or retention decision is recorded. M1 remains deferred until the responsible owners approve those exact terms. Do not send private Goal or user-session material. |

**Stop rule:** do not enter M1 unless the domain/evaluation owner accepts the D7 question, finite sample and rubric, and the data/operations owner records the permitted data, destination, model, hard spend/call caps and retention. Stop without a result if those gates cannot be met. This does not reject Jev or approve product adoption; off/shadow and the existing authority boundary remain unchanged.

No runtime, configuration, receipt, replay or removal behavior changes in this
brief. Any later D8 study must keep its research portfolio and spend fixed; no
ranking may override that budget or existing authority. Advice remains
non-binding and cannot execute, settle or grant eligibility.

**Evidence boundary:** this brief is based on integrated main
`42e55a809eb94f13443d303d76118735d4112182`, the current RFC §§2–3.3 and 12,
`loopx/control_plane/goals/start_contract.py`,
`loopx/control_plane/work_items/action_selection_contract.py`, and the dated
recordings already summarized above. It is source and public-record review
only. No new test, provider call, live task, cost measurement or outcome
comparison was run. The earlier 96-test implementation check and recorded
provider comparisons remain attached to their original revisions and do not
qualify this proposed D7 study.
