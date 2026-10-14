# Long-running execution, exploration and correction

[中文版](long-running-execution-exploration-and-correction.zh-CN.md)

Long-running work needs both continuity and opportunities to change direction.
A useful integration lets a Host execute coherent work, brings new evidence
back to the existing LoopX decision boundary, and verifies the resulting work
without turning every tool result into another planning cycle.

This is an implementation map and integration guide, based on LoopX source
`3dd3c550afecfa0c525a34e8744a3cf06774ac94`, refreshed on 2026-10-14 from the
initial comparison at `b77f63495f2619237380755178c0780ae10a0f26`. External
snapshots remain pinned to the versions inspected on 2026-10-13, not each
project's latest branch. It connects existing RFCs; it creates no policy,
schema, scheduler, default change or execution grant. Source presence,
installed behavior, model adoption and measured benefit remain separate.

The cadence wording is additionally reconciled with main
`3300dfdd29fe6e53e1aa4a0282ca3952db6e9e8f`: enabling the Goal strategy inherits
the live review interval unless a Goal interval is explicitly pinned. The
implementation proposals below are separate from this merged-source snapshot.

## Comparison method and evidence scope

The comparison follows the same questions in each repository: who admits work,
what continues the model/tool loop, when a correction becomes input, who decides
to investigate or finish, what survives interruption, and what the next decision
actually consumes. A feature name or README statement is insufficient: each
claim below follows a caller into its state owner and, where available, an
existing failure-case test. Interpretations and proposed transfers are stated
separately from source behavior.

| Source | Fixed revision | Inspected scope |
| --- | --- | --- |
| LoopX | `3dd3c550afecfa0c525a34e8744a3cf06774ac94` | Quota/replan, monitor, Explore writeback, Turn recovery, workspace steering, managed ZCode native Goal and direction M1 builder |
| Grok Build | `2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8` | Public Rust CLI/runtime Goal controller, interjections and snapshot recovery |
| Codex | `9167a0b2d4a74d44a4d388057d304682e47313c6` | Public Rust turn loop, Goal extension, compaction and Agent communication |
| OpenCode | `be674e3158abb5f1b0c16b2bdeb3a9ba2b072774` | Session admission/inbox, execution coordinator, restart and child-result delivery |
| Pi | `e6220e1316f86af818a0ccbfe1c09d7e918b41cf` | Agent core and coding-agent queues, context refresh, session persistence and extensions |

External tests cited below were inspected, not executed in this comparison.
LoopX validation executed for the documentation change is disclosed in the PR.
No live-model or matched-cost experiment is implied. Provider model calls, Host
turns and LoopX's accountable Turns are different units; counts across these
projects are not directly comparable.

The main-branch refresh changes the implementation map, not the external
comparison or a deployment claim. It adds a concrete native-Goal provider and
source-replayed Explore pages. The direction M1 builder is also present, but
has no production consumer or receipt writer. Current acceptance and explicit
result-use already provide version checks; subsequent qualification should
exercise those owners rather than introduce replacements.

## Existing owners and the connected path

The [overall roadmap](rfcs/loopx-overall-roadmap-v0.md) retains priorities:
S3 planning/collaboration, S4 Hosts, S6 evidence, S7 scheduling/cost and S10–S11
reliability/evaluation. The following documents own the decisions:

| Boundary | Existing owner | Integration question |
| --- | --- | --- |
| Tool feedback, delivery settlement, authority changes | [Hierarchical stride](rfcs/hierarchical-agent-stride-control-v0.md), §§5–7 | How much coherent work can continue before relevant feedback or an authority boundary? |
| Long-job result and wakeup | Stride §7.5; existing Host/provider and monitor owners | Which exact result revision justifies another decision, and how is a lost notification recovered? |
| Exploration and route selection | [Research exploration](rfcs/research-exploration-control-plane-v0.md), §§11.5–11.6; [Explore](../../loopx/capabilities/explore/README.md) | What uncertainty can change the next action, and what evidence distinguishes alternatives? |
| Original requirements and source changes | [Shared Goal alignment](rfcs/shared-goal-alignment-and-governed-amendment-v0.md), §§3.3, 3.7–3.8 | Does the revised path retain current requirements, evidence scope and other Agents' work? |
| Corrections, handoff and original-route return | [Manager operational contract](rfcs/capable-manager-semantic-handoff-v0.md), §10; [live team workspace](rfcs/live-team-workspace-v0.md), §9 | Was a correction received, adopted, executed, independently checked and returned? |
| Recovery and performance | [Failure containment](agent-failure-containment-and-recovery.md); [optimization guide](../development/testing-and-quality.md#roadmap-aligned-optimization) | Which original operation can resume, and where is the demonstrated cost? |

The source has the following control path. Branches are conditional; they are
not a mandatory sequence of extra model calls:

```text
Goal identity, intent and applicable acceptance contract
  -> Agent Vision / retained requirements / route
  -> Todo / explicit dependencies / current work boundary
  -> Core quota, claim, lease and action admission
  -> accountable LoopX Turn or qualified native-Goal integration
  -> selected Host session / native target / tool batch
  -> original-operation journal, effects and artifacts
  -> current source-use checks, independent validation and owed settlement
       -> eligible continuation: keep useful work moving
       -> relevant unknown or counterevidence: explore, then reconsider
       -> admitted review: satisfy its existing source-bound obligation
       -> candidate completion: run the owning acceptance checks
       -> wait / interruption / stop: use the existing lifecycle
  -> accepted decision and later work feed the next review
```

This is an ownership map, not a claim that every Host traverses an identical
transaction. A Goal may span sessions; a session can carry several Turns; native
Goal completion is a Host observation until the applicable LoopX acceptance
path succeeds. Ask separately what is executing, why it may continue, when new
input is consumed, whether its evidence is still usable, and which owner may
close the Goal. One `ready` or `complete` flag cannot answer all five questions.

### LoopX: from a detected gap to the next selected work

The purpose is to change an ineffective route without losing the original
Turn's settlement or another Agent's work. This is a real caller chain:

```text
canonical run/Todo facts
  -> projectReplanHistory + Goal-frontier rules
  -> build_replan_context -> projectReplanContext
  -> build_replan_action_packet -> CLI / heartbeat / TurnEnvelope
  -> Agent path/progress/successor writeback
  -> replan_semantics qualification -> durable Vision/ACK
  -> settle original Turn
  -> fresh quota: canonical successor + current eligibility
  -> evaluateDeliveryRoute -> next admitted Host execution
```

[`replan_history.ts`](../../loopx/control_plane/work_items/replan_history.ts)
scopes history to the Agent, stops at its accepted ACK and deduplicates counted
Turn identities. `periodicTrigger` counts qualified settled work; a monitor
liveness record is not work unless its transition is material. `progressTrigger`
uses repeated typed observations, while `futureBlockingMonitor` reuses the
existing resume planner to avoid classifying a valid external wait as a stall.
These are detector rules, not a model's estimate of scientific usefulness.

The [Goal frontier caller](../../loopx/control_plane/goals/goal_frontier/__init__.py)
actually constructs the context. [Quota](../../loopx/control_plane/quota/should_run_packet.py)
projects the action packet and exact commands; the
[TurnEnvelope](../../loopx/control_plane/quota/turn_envelope.ts) preserves the
structured review and its obligations. On writeback,
[`replan_semantics`](../../loopx/control_plane/work_items/replan_semantics.ts)
checks source-specific outcomes. `strideReviewMismatch` in
[`stride_policy.ts`](../../loopx/control_plane/work_items/stride_policy.ts)
rejects a missing delivered previous-decision review, a foreign evidence
reference or an incompatible Explore basis. It checks declared provenance and
shape, not the truth of the Agent's interpretation.

Fresh quota reads the ACK's exact successor from canonical Todos, checks current
actionability/capabilities, and asks
[`evaluateDeliveryRoute`](../../loopx/control_plane/turn_driver/delivery_continuity.ts).
`prefersReplanSuccessor` requires the same Agent, accepted ACK, exact origin
obligation and a currently legal delivery boundary. This can change the next
primary task, but does not grant a lease or execute it during the old Turn.
The [successor reentry tests](../../tests/control_plane/test_replan_successor_guard_reentry.py)
exercise real File/SQLite CLI settlement and a fresh Turn selecting that
successor, including invalidation. Selection is narrower than adopting a method.

### LoopX: from an exploration result to a later decision

The purpose is to keep a reusable result available without making a skill
report another authority source or repeating completed work after a link fails.

```text
ordinary admitted work + explicit Explore attachment
  -> validate scope/input before primary writeback
  -> committed record -> deliver_result_attachment
  -> idempotent graph event -> canonical Todo evidence link
  -> explore_turn_context / enabled replan Explore read
  -> evidence-bound explore_review and next task decision
```

[`deliver_result_attachment`](../../loopx/capabilities/explore/result_writeback.py)
consumes a committed primary record and a pure post-writeback intent. If graph
delivery or the Todo link fails, it retains `primary_committed` and a retryable
delivery result. Exact replay repairs the missing association; already-complete
delivery is readback-only. Claim/lease rules still apply to unfinished delivery.
The [linked capture tests](../../tests/control_plane/test_explore_linked_capture.py)
check current canonical scope rather than stale shared projections, and reject
invalid attachments before primary commit. The
[result-writeback tests](../../tests/control_plane/test_explore_result_writeback.py)
also describe partial-link replay and preserving a linked refutation under newer
positive observations; these are different from model-use evidence.

[`explore_turn_context`](../../loopx/capabilities/explore/turn_context.py) validates
the complete evidence log before returning bounded, relevant details and a
revision-bound detail route. Replan can bind those findings through
`explore_review`. Separately,
[`decisionFollowthrough`](../../loopx/control_plane/work_items/replan_context.ts)
uses qualified append order to pair the latest accepted decision with later
progress, keeping omitted counts and `execution_and_goal_gain=unknown`.
The [three-Turn CLI test](../../tests/control_plane/test_long_chain_projected_closeout.py)
performs review, test-driven synthetic work and a subsequent review that refuses
a missing `previous_review`. It proves that the wire/settlement chain is connected;
it does not prove a live model chose or executed a better experiment.

### LoopX: correction transport, recovery and visible evidence

Current workspace steering already persists the exact Session/Turn/text/request
identity before dispatch. [`retainSteeringRequest`](../../apps/presentation/dashboard/src/features/personal-workspace/steering-recovery.ts)
checks persistent readback; the [composer](../../apps/presentation/dashboard/src/features/personal-workspace/personal-workspace-page.tsx)
does not send if that fails, and an uncertain retry retains the original Turn
even after completion. A confirmed non-delivery has a different recovery path.
The [storage tests](../../apps/presentation/dashboard/src/features/personal-workspace/steering-recovery.test.mjs)
cover reload, a silently dropped write and another conversation's unsaved draft.
This closes a transport-identity gap, not evidence that the model changed its plan.

[`recoveryDecision`](../../loopx/control_plane/turn_driver/turn_journal.ts) checks
journal consistency, returns retained terminal results, and distinguishes an
explicit failed retry from phase recovery. Relevant Host session bindings must
still qualify; unknown prepared effects use their original readback path.
[`continuation_adoption.ts`](../../loopx/control_plane/coordination/continuation_adoption.ts)
keeps context adoption separate from lease transfer and dependency eligibility.
Resuming context is not authorization to repeat an ambiguous external effect.

The [team correction view](../../apps/presentation/dashboard/src/features/personal-workspace/goal-team-episode.tsx)
follows explicit `revises`, `responds_to` and downstream `uses` relationships,
checks artifact versions on demand and keeps missing adoption visible. It is a
real companion surface, but does not consume every `stride_decision` as a
universal causal timeline. Source wiring, transport qualification, actual model
adoption, independent acceptance and readable product return remain distinct.

The [Goal Vision/replan contract](../reference/protocols/goal-vision-replan-contract-v0.md)
defines activation and current settlement rules. `evidence_conditioned_v1` is
optional and default-off; ordinary review remains available when it is off.
At this baseline, every accepted exit of an enabled periodic review also
requires a current `stride_decision` bound to its obligation and delivered
context. Progress, successor, blocker and terminal outcomes keep their own
evidence rules; other replan sources retain their existing exits. This enforced
recording obligation does not prove actual adoption. Efficiency work must not
bypass it, quota settlement, permission, lease or revision checks.

### LoopX: an existing native-Goal provider and its recovery boundary

The [managed ZCode provider](../../loopx/zcode_goal_mode/README.md) is a concrete
example inside LoopX, not a fifth independent upstream runtime audit. Its
current call chain separates binding, starting and resuming:

```text
CLI / Goal drawer -> bridge validates canonical Goal/Agent/project
  bind  -> create or restore native session -> confirm paused -> persist binding
  start -> current Core heartbeat task -> validate/quota/validate admission
        -> journal start intent -> native setGoal -> persist observed target
  resume -> restore same session/target if needed -> current admission
         -> native resume (not another setGoal)
  monitor/status -> observe native state + applicable Core checks
                 -> denial/unavailable authority: pause and confirm drain
  cold recovery -> reconcile original intent/target -> confirm paused
  native completed -> observed only; no automatic Core completion or credit write
```

[`zcode_goal_operation`](../../loopx/zcode_goal_mode/bridge.py) generates a new
canonical heartbeat task for bind/start. [`NativeGoalController`](../../loopx/zcode_goal_mode/runtime.ts)
serializes operations and preserves the native session/target on resume. Its
admission is validation → quota → validation. A lost `setGoal` reply is
recoverable only when a durable admitted start intent and objective hash match;
cold recovery pauses the recovered target rather than silently continuing it.
The [controller tests](../../tests/control_plane_ts/zcode_native_goal.test.ts)
cover denial, replaced identity, persistence failures, exact resume and native
completion not writing Core state, using a controlled Host double. The
[bridge tests](../../tests/test_zcode_goal_bridge.py) cover the separate Core/CLI
boundary; neither test source nor a model list certifies paid-provider readiness.

Keeping the same target proves execution-container continuity, not that a
changed criterion was consumed after resume. Conversely, not issuing a new
`setGoal` is not proof of stale model behavior: the retained heartbeat task can
direct the Agent to obtain current Core state. The missing combined qualification
changes criteria or explicit source applicability within the same Goal instance,
then observes the first new protected action. It must use current owning
revisions, or remain pending/paused at an unsupported refresh boundary. Do not
replace that evidence with a hash of the entire prompt or rewrite the old receipt.

The [broker](../../loopx/zcode_goal_mode/cli.ts) schedules another monitor about
two seconds after a serialized check completes; its admission subprocesses each
have a ten-second timeout. Online status can recheck quota and pause a target,
so it is not a purely observational operation. Pause/stop also contend with the
controller's serial queue. The one-hour per-execution safety deadline is not a
whole-Goal token budget or a hard real-time stop guarantee. Native usage remains
unknown. These are measurable cost/latency boundaries, not a diagnosed bottleneck:
measure check duration, subprocess/IO cost, stop queueing and actual drain before
coalescing work. A native failure or unverified result never waives Core's owed
settlement or permits reporting the work as accepted.

### LoopX: contract acceptance and explicit result use already exist

The [acceptance adapter](../../loopx/control_plane/goals/acceptance.py) obtains
current owner criteria, runs their validators and checks the basis again.
[`acceptance_authority.ts`](../../loopx/control_plane/goals/acceptance_authority.ts)
binds verification to contract revision/digest, work basis and provider CAS;
[`projectGoalAcceptance`](../../loopx/control_plane/goals/acceptance_contract.ts)
distinguishes partial verification, accepted scope, stale evidence and held work.
Verifying one Todo cannot become full-contract accepted merely because its own
tests pass. A full verification of a `selected_work` contract still covers only
that selected scope, not every requirement of the wider Goal.

Similarly, [`qualifyDelegationResultUse`](../../loopx/control_plane/collaboration/delegation_result_use.ts)
checks explicit `uses` ancestry, exact artifact versions, accepted sources,
input availability, cycles and bounded collection. A → B → C may become
unavailable for current use even when C's own output still passes its local
validator. Historical accepted/done records remain intact. The
[result-use integration tests](../../tests/test_delegation_result_use.py) already
exercise version-bound replay, reconnect and transitive invalidation without
redispatching the original task. These are existing mechanisms to compose,
not missing adoption or dependency ledgers.

Whole-Goal closeout still needs evidence that the current contract covers its
declared mandatory requirements, required work across Agents has a legal
disposition, and relevant amendments cannot invalidate the conclusion. Optional
research branches need not all finish. A recurring Goal's waiting or terminal
condition comes from its declared contract; one successful observation is not
permanent completion. Keep scoped acceptance, original receipts, current
source eligibility and global closeout as separate facts.

### LoopX: direction observation and progressive evidence have different reach

[`buildGoalDirectionBaseline`](../../loopx/control_plane/goals/direction_baseline.ts)
is an opt-in M1 pure builder over supplied declarations, material metadata and
same-Agent usage receipts. No declaration preserves the absent projection.
Its digest describes the declared material basis; it is neither a canonical
Goal-intent revision nor an action grant. The [direction RFC](rfcs/goal-direction-baseline-v0.md)
still records entry/receipt-writer/consumer qualification as incomplete. A
schema-valid caller-supplied receipt does not authenticate real material use.

Explore goes further along an existing production read/write path:

```text
turn-context shows bounded findings + revision-bound next_command
  -> Agent reads an omitted result page
  -> stride_decision.explore_review includes bounded page descriptors
  -> state_refresh obtains current Explore source
  -> projectExploreTurnContext replays the declared slices
  -> strideReviewMismatch checks current citation membership
  -> same-basis citation expansion preserves admission IDs -> owning writeback
```

[`explore_result_page.ts`](../../loopx/control_plane/capabilities/explore_result_page.ts)
bounds descriptors; [`explore_turn_context.ts`](../../loopx/control_plane/capabilities/explore_turn_context.ts)
replays source pages rather than trusting caller-provided finding lists.
[`stride_explore_pagination.test.ts`](../../tests/control_plane_ts/stride_explore_pagination.test.ts)
checks stale/forged/foreign pages and source availability distinctions. This
qualifies citations, not understanding or adoption. Page and bounded-basis
revisions have different scopes: source drift requires a current read in the
original Turn, which may refresh its context/basis without replacing the original
obligation or settlement identity. Current construction still
processes the full source/result rows: bounded output and fewer emitted hashes
do not establish constant-time source IO. Preserve omitted-result access and
scoped refutations when measuring large histories; missing source is not refuted
evidence and changed applicability must invalidate the corresponding old basis.

## Transfer mechanisms, with their limits

These are implementation references, not comparative performance results.
Their checked versions do not identify an installed release. Adapt ownership,
trigger conditions and failure cases rather than copying whole runtimes.

| Public source snapshot | Mechanism worth transferring | LoopX placement and limit |
| --- | --- | --- |
| Grok Build `2bdd1d6a`: [Goal loop](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/goal.rs), [strategist](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_strategist.rs) | The Host-owned Goal path evaluates round results, verifies candidate completion, and invokes advisory strategy investigation after repeated non-achievement | Separate continuation, acceptance and method investigation through existing owners. Do not copy a fixed reviewer count or assume extra model calls improve quality/cost |
| Codex `9167a0b2`: [tool/input loop](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/turn.rs), [Goal continuation](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/goal/src/runtime.rs) | Tool results and pending input continue the current turn; a lifecycle extension starts eligible Goal work when idle | Reuse Host execution and context management. Make the continuation owner explicit so native Goal and external heartbeat do not independently launch the same attempt |
| OpenCode `be674e31`: [inbox](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/inbox.ts), [run coordinator](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/run-coordinator.ts), [child return](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/subagent-completion.ts) | Current-work steering and queued work differ; a pending-wake signal coalesces notifications across the drain-to-idle race; child completion returns as input | Extend existing inbox/session/Turn delivery. A process-local single executor cannot replace distributed claims, leases or adoption evidence |
| Pi `e6220e13`: [agent loop](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/agent/src/agent-loop.ts), [skills](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/docs/skills.md), [subagent extension example](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/examples/extensions/subagent/README.md) | Inner tool/steering and outer follow-up loops; skills and richer workflows load when needed | Keep ordinary work independent of a full research workflow. An extension example is not default product support, isolation or cross-Host qualification |

### Grok Build: separate continuing, candidate review and method investigation

**Question followed through the code:** when a worker appears finished or keeps
failing, what actually causes it to continue, obtain criticism or change method?

```text
handle_turn_input_inner finishes a work round
  -> runnable user input takes precedence
  -> run_goal_round_end -> evaluate_goal_round
       continue           -> next-step directive -> worker continues
       blocked            -> stable blocker key -> repeated-blocker policy
       candidate_complete -> verify_goal_candidate
           Achieved       -> local Goal completion
           NotAchieved    -> preserve concrete gaps -> worker continues
               strategy cadence reached -> advisory strategist
           review infrastructure unavailable -> new path pauses
```

The [work-round caller](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs)
does this after normal model/tool work, not before every tool. The
[evaluator](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_evaluator.rs)
receives a bounded transcript, objective and plan with no tools. It must return
evidence and a next step; a stable key distinguishes a recurring external
blocker from ordinary errors the worker can investigate. This is another model
judgment, not a cheap deterministic completion oracle.

Only candidate completion starts verifier work. The
[classifier](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_classifier.rs)
defaults to three skeptic roles, but a decisive initial refutation can avoid
starting the rest. Cold-review voting does not let the resumed gap reviewer
supply the decisive approval. Role count is not API-call count: each role can
run tools and multiple samples. The [verifier instructions](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/templates/goal_verifier_prompt.md)
primarily audit the implementer's evidence and permit spot checks; they do not
independently establish every business postcondition.

`strategist_should_fire` triggers on accumulated non-achievement rather than
running at every step. Its [strategy instructions](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/templates/goal_strategist_prompt.md)
ask for a structural change grounded in trace, diff and tests. `PlanGuard`
restores changed plan bytes so method investigation does not quietly weaken
acceptance. This is bounded protection, not a universal permissions system.

**Follow the failure path as well.** Corrections are drained at Host safe points
under `RestoreOnCancel`; cancellation before context submission restores queue
order. A correction arriving after the final drain has a fallback prompt path
that retains the plan gate. The [interjection implementation](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/interjection.rs)
and [actor tests](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_tests/interjection_actor_tests.rs)
cover canceled drains and late messages. LoopX can borrow those race scenarios,
but its exact-Turn steer must not silently inherit Grok's fallback policy.

Compaction reseeds current Goal state without counting another work round.
Process restart is different: [GoalTracker::from_snapshot](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_tracker.rs)
clears in-flight child state and restores an active Goal as user-paused.
[Snapshot persistence](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/persistence.rs)
is reached through asynchronous notification; writing a snapshot file atomically
does not make all Goal accounting and external effects one acknowledged transaction.

**Comparison result.** Borrow staged escalation and current-Goal reconstruction.
Keep LoopX's acceptance, original-operation recovery and configured review
obligations. Do not import the panel size, counter resets or paused-on-restart
policy without qualifying that Host/profile. The inspected source defaults to
the new workflow branch, subject to configuration and a remote kill switch;
[configuration](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/agent/config.rs)
does not prove installed behavior. Its
[compaction tests](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_tests/goal/goal_compaction_reseed_tests.rs)
and [snapshot tests](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_tracker_tests.rs)
provide counterexamples to reuse, not passing evidence from this investigation.

### Codex: response completion, turn termination and Goal continuation differ

**Question:** what continues work without making every tool result a Goal-level
planning event, and what happens if a correction races that continuation?

```text
accepted input -> run_turn -> sampling request
  -> model output + tools -> drain tool results
  -> model needs follow-up OR pending input?
       yes -> prepare current context -> next sample
       no  -> stop hooks -> end turn or a bounded continuation
  -> Goal lifecycle accounts the completed turn
  -> on_thread_idle -> continue_if_idle
       read persistent active Goal under Goal-state permit
       -> start_turn_if_idle performs Core admission again
```

The same step context supplies the model request, advertised tools and tool
execution. Queued input is consumed at defined boundaries. Thus final-looking
text, completion of a sampling request and termination of the enclosing turn
are different events. [Core input admission](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/turn_input.rs)
also binds steering to the expected active Turn.

The native Goal path has two protections: a Goal-state permit covers its
read/start window, and Core independently reserves an idle turn. Pending
triggering messages, Plan mode, draining or an already active turn can reject
automatic work. Rejection does not silently turn it into queued work or steering
of a newer turn. The [input-submission tests](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/tests/suite/turn_input_submission.rs)
exercise atomic Plan-mode rejection. [Goal lifecycle tests](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/goal/tests/goal_extension_backend.rs)
cover resume/accounting and forbidden status changes. The
[`update_goal` tool](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/goal/src/tool.rs)
records a permitted completion status and usage; that write is not an independent
test of the objective's business requirements.

**A second race appears during compaction.** A prepared summary may be obsolete
by installation time. The optional [async path](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/async_compaction.rs)
is feature/provider-gated and tracks history, model and authentication versions.
[`replace_compacted_history`](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/mod.rs)
checks the captured basis at mutation time, retains the live suffix and preserves
Goal edits accepted after a foreground compaction snapshot. [Rollout reconstruction](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/rollout_reconstruction.rs)
restores history together with attribution/settings metadata, rather than
reusing whichever older summary happens to be readable. Speculative requests
can still consume billable usage even when their result is discarded.

Skills use a [budgeted metadata catalog](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/skills/src/render.rs)
and [explicit resource reads](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/skills/src/tools/read.rs).
Exploration can move into child contexts, but the return boundary remains
limited: [completion routing](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/agent/control/completion.rs)
is best-effort mailbox delivery with `trigger_turn=false` in the inspected V2
path. `wait_agent` reports waiting activity, not adopted research content.
The [multi-Agent tests](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/tools/handlers/multi_agents_tests.rs)
explicitly distinguish completed content, waiting and interruption.

**Comparison result.** LoopX should reuse the Host's inner loop and revalidate a
prepared continuation at its own current authority boundary. Native Goal state,
a prepared summary and a queued child result cannot replace LoopX acceptance,
lease fences or result adoption. This is a division of responsibilities, not a
claim that a thin Host has solved the full cross-Agent lifecycle.

### OpenCode: durable admitted work and a coalesced execution wake

**Question:** how can work arriving at the busy-to-idle boundary survive without
starting duplicate runners or forcing the model to poll every child?

```text
Session.prompt: reconcile identity -> prepare -> admit
  -> durable inbox -> SessionExecution.wake
  -> per-session coordinator -> drain
  -> runner advanceToStep -> promote input -> runStep
       model/tool attempt -> persist tool/step terminal events
       -> retry / compact / continue / complete
  -> pendingWake? drain again : finish execution
```

[`Session.prompt`](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/session.ts)
admits the input before waking execution. The
[runner](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/runner/llm.ts)
allows queued input when entering work, then only steering while continuing
that work. A provider retry refreshes context within the same logical step,
without repeatedly promoting more inbox input. The coordinator's in-memory
`pendingWake` closes the final eligibility-check/idle race; it coalesces wake
intent while the durable inbox retains the actual work. [Coordinator tests](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/test/session-run-coordinator.test.ts)
exercise active, finalizing and cleanup interleavings with controlled scheduling.

Persistence is more than that in-memory map. [Execution](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/execution.ts)
commits an execution-start event with a durable claim; normal terminal/cancel
releases it while shutdown retains recovery evidence. [Restart](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/execution/restart.ts)
scans claims and persists a bounded resume count. Its stated recovery assumption
is at-least-once, under a managed-server handover. A tool may have changed the
external world before its result was committed; the claim cannot resolve that
ambiguity. [Execution tests](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/test/session-execution.test.ts)
cover claim cleanup, cancellation and the persisted resume limit.

Background exploration returns through [SubagentJob](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/subagent-job.ts):
the child/job generation owns a completion observer, which submits a stable-ID
synthetic input to the parent's existing inbox and wake path. The observer
outlives a local view's eviction; [lifetime tests](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/test/subagent-lifetime.test.ts)
move/evict views before child completion and check the return notice. This proves
the intended routing/recovery boundary, not that a real parent model used the
finding. Skills remain [permission-checked, on-demand reads](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/tool/plugin/skill.ts).

**Comparison result.** Reuse the distinction between durable work and a cheap
wake signal. LoopX already has a scoped accepted-result wake path in Goal Chat;
qualify its last-check, restart and stop races before generalizing to provider
checkpoints. Do not substitute OpenCode's process/server assumptions for the
existing LoopX claim, lease and ambiguous-effect owners.

### Pi: batch-boundary steering and context refresh as separate operations

**Question:** where can a correction enter a running tool workflow, and how can
context maintenance avoid delaying it by one more model request?

```text
AgentSession prompt / steer / followUp
  -> Agent pending queues -> runLoop
  -> prepareNextTurn -> pick up steering received during preparation
  -> prepareRequest -> model response -> execute the whole tool batch
  -> finishTurn -> next tool/steering cycle
  -> only when that cycle would stop: consume follow-up
  -> event listener appends completed messages to session history
```

`finishTurn` is also a control extension point, not only a notification:

```text
finishTurn -> end? -> agent_end
  otherwise -> natural tool/steering work? -> continue that work
            -> follow-up input? -> continue with that input
            -> explicit continue? -> one context-only turn
            -> otherwise agent_end
```

Natural continuation consumes the explicit request instead of duplicating it;
another context-only turn needs another continuation decision. This enables an
optional long-work controller without making persistent business Goals,
independent acceptance or cross-process recovery part of the base loop.

At this version, steering is consumed **after the entire tool-call batch**.
Sequential mode also finishes the batch before injection; parallel execution
can complete out of order while results enter context in call order. Steering
does not abort a submitted tool. Follow-up waits until the inner tool/steering
loop would stop. The [loop tests](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/agent/test/agent-loop.test.ts)
assert that both tool outputs precede queued steering. This granularity matters
when specifying correction latency for a long tool operation.

[`AgentSession`](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/src/core/agent-session.ts)
installs context refresh in `prepareNextTurn`. A large tool result can require
compaction before the next sample; `runLoop` checks for new steering after this
preparation without consuming a second message in one-at-a-time mode. The
[compaction suite](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/test/suite/agent-session-compaction.test.ts)
blocks a compaction hook, queues a correction, then checks the next request
contains it. Faux provider responses establish ordering, not model compliance.

[`SessionManager`](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/src/core/session-manager.ts)
keeps an append-only JSONL tree with parent IDs. Compaction appends a summary
entry and retained-history boundary; rebuilding the model projection does not
erase the original history. This can restore context, but neither a summary nor
an append-only record establishes task acceptance or external-effect idempotency.

The coding-agent's plan/subagent workflows are optional extensions; the linked
subagent implementation is an example, not a mandatory default. The
[skill formatter](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/src/core/skills.ts)
initially supplies metadata and a read route. Executable extensions can change
the lifecycle and have process permissions, whereas skill text only guides the
selected work. **LoopX's adaptation** is to keep those costs conditional and
declare the actual Host consumption boundary; it must preserve its own required
reads, independent acceptance and continuation authority.

Grok Build's new and legacy Goal paths have different failure behavior; its
legacy path includes infrastructure fail-open completion. That behavior is not
a candidate for LoopX acceptance. Public Goal/turn similarities are not
independent experimental evidence: Grok's
[third-party notice](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/THIRD_PARTY_NOTICES.md)
also identifies tool code ported from Codex/OpenCode. This does not establish
shared Goal-loop history. None of these client sources proves how every hosted
service operates, or that an external effect executes exactly once.

## What the comparison changes in the existing RFCs

The review used the following sequence, rather than treating external features
as missing LoopX features:

1. Trace each external caller to its real loop, state write and return path.
   This exposed different units: a Pi tool batch, a Grok work round and a LoopX
   settled Turn cannot share one universal intervention count.
2. Inspect cancellation, late-input and restart tests. OpenCode's durable
   recovery and Grok's paused restoration prevent describing either as a simple
   stateless loop, while neither proves external effects exactly once.
3. Locate the corresponding LoopX caller and reference contract. Existing
   steering receipts, accepted-result wakes and Explore capture were found;
   they should be qualified or composed, not reimplemented under new names.
4. Separate connected source from model behavior. The prior-decision projection
   and CLI chain exist, but result delivery does not prove a better next action.
5. Compare the current RFC clauses with those facts. Revise stale next-step
   descriptions and ambiguous obligations in their owning sections, then add
   failure scenarios that distinguish the proposed behavior from the old reading.

| Owning section | Ambiguity or stale definition | Revision and decisive acceptance |
| --- | --- | --- |
| Stride §§6.4, 7.5–7.7, 11–12 | Event-driven review could be read as skipping periodic duty; feedback projection was still described as the next implementation | Retain configured obligations; separate candidate review from acceptance; make the next slice a real result→adopted work→next-review episode, with context recovery and full cost |
| Research Exploration §§5.1, 11.5 | Generic result capture and the proposed scientific decision/adoption contract were insufficiently separated; bounded output implied no full source read | Reuse existing attachment/replay; identify only additional missing validity/adoption facts; measure source IO; qualify capture, use and outcome separately |
| Agent Session §§1, 4–5, 10–11 | Blanket no-resume wording and a generic successor-wake gap obscured existing scoped continuation; same native identity could imply current semantics | Record the managed ZCode path; preserve one continuation owner; qualify current criteria/source consumption after same-instance resume, finalization and reconnect per Host |
| Manager A23 and §10 | “Steer or queue” could silently retarget a correction; a delivery ACK could imply later adoption | Retain original ingress/Turn identity and permitted fallback; derive queued, delivered, consumed, adopted and verified facts from their respective owners |
| Shared Goal Alignment §§3.7–3.8 | Source readability, scoped acceptance, note adoption and journal continuation could be collapsed into whole-Goal readiness | Preserve accepted scope and mandatory-requirement coverage; reconnect current criteria/explicit sources before new work; treat Direction M1 as a candidate observation, retaining original-operation recovery |

The revised text refines qualification and clarifies existing authority. It
does not install an evaluator, change the current cadence or invent a new
protocol vocabulary. An additional machine-enforced requirement still needs
its owning implementation, compatibility and default-off validation.

### Implementation maturity at the inspected LoopX revision

| Area | Evidence available | Remaining completion boundary |
| --- | --- | --- |
| Replan transport and successor selection | Actual callers; typed provenance checks; multi-Turn CLI tests | Natural-model method adoption and useful outcomes |
| Structured stride strategy | Opt-in proposal shape, previous/Explore review, exact evidence binding and an enforced decision at every enabled periodic-review exit | A valid declaration is not truth; actual method adoption and gain still need evidence |
| Explore capture and explicit M3 composition | Existing ordinary attachments and File/SQLite composition gate; see Research RFC §5 | Single-experiment result/adoption gate in §11.5, full remote/installed qualification and scientific utility |
| Corrections and accepted-result wake | Browser retry identity, exact-Turn ingress and scoped Goal Chat wake | Declared consumption boundary and live adoption per Host/channel; generic job-result integration |
| Recovery and required reads | Typed original-operation recovery, current Todo/acceptance/lease checks, source read paths | Full Goal requirement coverage across compaction/handoff; no inferred whole-Goal readiness |
| Managed native Goal | Opt-in ZCode bind/start/pause/resume/stop, exact native identity, cold-recovery pause and Core admission | Same-instance/current-criterion consumption after resume, live usage/cost and general all-Host qualification |
| Acceptance and result-use | Versioned contract verification/CAS and current explicit dependency ancestry | Accepted contract scope is not arbitrary Goal-intent completeness; no inferred global dependency graph |
| Direction and omitted Explore evidence | Pure direction M1 builder; production source-replayed result pages | Direction production receipt/consumer still unqualified; citation eligibility does not prove model adoption or constant-time reads |
| Product explanation | Explicit artifact/review/revision/adoption view | Universal stride-decision→artifact story and complete Explore attachment/retry interaction |
| Runtime efficiency | Specific owners and measurable candidate costs | Controlled workload, model and history-size measurements; source size or successful tests are not latency evidence |

### Coordinate existing implementation proposals

The source map must not turn work already present in a candidate PR into a
request for another implementation. These proposals join different parts of
the same path; their own current-head reviews and acceptance evidence still
control readiness:

| Existing proposal | Owned contribution to the path | Required handoff and boundary |
| --- | --- | --- |
| [Request correction and Goal effects, #6357](https://github.com/loopx-project/loopx/pull/6357) | Original conversation → revised request → reserved effect → authentic Goal terminal receipt → original-route return | Persist and read back the exact retry identity before dispatch. Keep committed history, current request revision and receiver adoption separate; a retained Goal change is not completed work. |
| [Material reads and direction recovery, #6338](https://github.com/loopx-project/loopx/pull/6338) | Owner declaration → actual bounded source read → same-Agent acknowledgement → current basis and recovery | A failed current read must withdraw old currency even at the history limit. Preserve history and bounded owner recovery; read acknowledgement does not prove model understanding, changed work or acceptance. |
| [Inert backup inspection, #6279](https://github.com/loopx-project/loopx/pull/6279) | Archive/manifest validation → isolated owner reads → readable historical evidence | Inspection retains the existing capacity and owner-decision gates. Activation, fresh authority, continuation under current requirements and independently accepted new work remain with the recovery RFC's later stages. |

Each PR can deliver its own bounded outcome. A joined journey must observe the
consumer using the upstream result; shared terminology or merging all three is
not that evidence. Reconcile overlapping callers against current main and
regenerate derived manifests from the resulting source. Keep qualification in
the existing PR/RFC owners rather than creating a new dependency ledger or
requiring unrelated recovery activation before ordinary corrections can ship.

## Integrate exploration without making every task research

An initial unknown, a new opportunity, user direction or a counterexample may
justify exploration. Repeated failure is one signal, not a prerequisite or an
automatic command to switch methods. Ask what decision the investigation can
change before choosing a skill or parallel work.

| Unknown | Suitable work | Useful return |
| --- | --- | --- |
| Existing implementation and constraints | Focused source-reading skill | Proven behavior, reusable seams and limitations |
| Alternative methods or external evidence | Research/search skill | Candidates, primary sources, applicability and counterevidence |
| Whether a candidate fits the project | Combined source/research analysis or a bounded experiment | A discriminating result, integration cost and adoption criterion |

These are workflow categories, not required skill packages or a new routing
enum. Choose the smallest method that answers the question. Independent
subagents are useful when their hypotheses or information sources differ;
their summaries retain source references and unresolved points. A research
report can itself satisfy a research task. It need not manufacture code work.

Use Explore's existing `evidence` mode to retain findings; use `planning` when
several candidate branches need its read-only selection support. These modes
grant no claim, spawn or execution authority. Validated reusable findings enter
the existing `explore_result` attachment or `--explore-result-json` writeback,
with tested input revision, applicability, observation, interpretation and
evidence. A compatible `path_delta` can supply the existing attachment form
instead of duplicating the same narrative. Routine work needs no empty finding.

Next-turn context and `explore_review` let the Agent explain which finding
supports changing the route, continuing it or performing a justified
replication. Keep negative evidence scoped to its tested conditions and retain
the best qualified artifact. Unavailable input and an inconclusive measurement
do not establish refutation. Actual task steps, artifact versions and independent
validation must support a claim of adoption; source bindings alone cannot.

## Keep ordinary operation bounded

Apply the existing [optimization evidence guide](../development/testing-and-quality.md#roadmap-aligned-optimization)
to the real consumer before selecting an abstraction:

- Measure repeated calls, history reads, serialization/bridge work, provider IO
  and model work separately. A short rendered response can still require a
  full source scan. Explore's bounded turn context does not claim bounded
  history IO.
- Reuse facts within a proved snapshot boundary; refresh authority and revision
  at the owning admission/write boundary. A cache cannot become another writer.
  Prefer removing a demonstrated duplicate read/copy before adding cache services.
- Keep decision-essential facts and detail routes in default context. Load
  skill instructions, verbose evidence and diagnostics when relevant. Preserve
  ordering, provenance, acceptance and stop obligations when shortening prose.
- Handle transient transport retry, context compaction and method revision at
  their respective owners. Neither elapsed time nor a fresh identifier proves
  progress. Resume from the original operation; query unknown effects before retry.
- Count exploration, evaluators, reviewers, compaction and observer CPU/IO in
  total cost. Faster visible responses can still cost more overall.

For a supported long-job profile, Stride §7.5 proposes observing without a
reasoning call, then advancing the existing monitor generation only for an
exact validated result. A deadline catches missed events. Unsupported Hosts
retain their current path with an explicit limitation; this guide does not
silently suppress model admission or change heartbeat defaults.

## One connected qualification episode

Use one supported Host/profile and an isolated repository task: improve an
implementation while retaining a declared command and compatibility contract.
Preserve that acceptance contract throughout this episode:

1. Begin useful work, then introduce a method correction without weakening the
   original requirement. Verify reception separately from its application.
2. Investigate one relevant unknown, return evidence through ordinary writeback,
   and let the next admitted work adopt a candidate or explain why the incumbent
   remains appropriate. Check the actual artifact and result.
3. Return an exact background result through the existing monitor/resume owner.
   Deliver duplicate and out-of-order notifications, including one arriving
   just as the consumer becomes idle.
4. Interrupt after a committed result but before acknowledgement. Recover the
   same operation and current Goal basis. Test source revision changes, missing
   evidence and owner stop; stale results cannot relaunch stopped work.
5. Independently verify the result and return it to the original conversation.
   Packaged frontend and CLI should distinguish queued, delivered, adopted and
   validated facts, and expose missing/stale evidence without a success badge.

The existing [correction evidence view](../../apps/presentation/dashboard/src/features/personal-workspace/goal-team-episode.tsx)
can inspect explicit artifact/review/revision/adoption relationships. It is not
a universal visual history of every stride decision. A missing frontend
attachment/retry or decision-to-artifact connection remains companion work in
the owning slice; a CLI-only prerequisite should be described as partial.

### Recovery and negative-case matrix

The following combines existing regression seams with **proposed qualification
of the connected episode**. It is not a report that all combinations passed.
Existing test links identify reproducible starting points; the PR separately
records which tests ran, their backend and skipped cases. Use the original
operation and current source revisions to join observations, not a new ledger.

| Injection or variant | Required observation | Existing seam and remaining qualification |
| --- | --- | --- |
| Pause, change criteria/source scope without replacing the Goal instance, then resume; also deliver an amendment during restore | Keep native identity and original receipts; the first new protected action consumes current applicable criteria/sources or remains pending at an unsupported boundary | [Native controller tests](../../tests/control_plane_ts/zcode_native_goal.test.ts) cover identity/recovery and now the basis gate: a changed or unreadable acceptance/source revision basis refuses a plain resume and keeps the target paused; cross-owner resume-to-model behavior remains to qualify |
| Invalidate an ancestor in explicit A → B → C while C still passes its own validator | Preserve historical accepted/done facts; reject current use where ancestry is no longer eligible, without redispatching the original task | [Result-use integration](../../tests/test_delegation_result_use.py) covers this with a controlled worker; carry it through the selected Host entry |
| Native reports completed but acceptance fails; selected-work contract passes while mandatory Goal work remains | Preserve provider completion and scoped acceptance without promoting either to whole-Goal achieved; execute owed settlement; optional research alone need not prevent closeout | [Acceptance authority tests](../../tests/control_plane_ts/goal_acceptance_authority.test.ts) and native completion tests cover separate owners; combined final readback remains |
| Commit a result between final drain and idle; deliver duplicates and old wakes after stop, then a newly authorized input | Durable work remains discoverable; prior wakes cannot revive stopped execution; new input re-enters current admission through the same continuation owner | Existing [accepted-result wake](../../loopx/chat_loopx_mode.py) and Host continuation paths; qualify both no duplicate start and no lost work, not just a lock |
| Deliver/retry an exact-Turn steer during tools, context preparation or finalization | Preserve text, ingress and target; show the actual pending/consumed/refused fact and permitted fallback; steering does not imply tool cancellation | [Steering tests](../../tests/test_chat_turn_steering.py) plus each Host's declared consumption boundary; no all-Host inference |
| Lose the reply after native set; replay an old operation, then admit new recovery work | Recover only the target matching durable start intent; cold recovery pauses; old replay is idempotent while a new accountable Turn retains its settlement obligation | Native controller and [Turn recovery](../../loopx/control_plane/turn_driver/turn_journal.ts) are reused; inspect original-operation and new-Turn receipts separately |
| Delay validation/quota while requesting stop; reach the execution deadline and explicitly resume repeatedly | Measure request queueing, native cancellation and confirmed drain; preserve configured cumulative budgets and unknown usage across per-execution timers | [ZCode runtime/broker](../../loopx/zcode_goal_mode/cli.ts) supplies a measurement candidate, not a proved stop SLO or token-usage bound |
| Cite an omitted result via valid, forged, stale or foreign pages; add a positive finding under different conditions from an old refutation | Replay current source; keep counterevidence scoped and reachable; distinguish bad descriptors from unavailable source. Same-basis pages preserve admission identity; source drift can refresh basis within the original Turn | [Pagination tests](../../tests/control_plane_ts/stride_explore_pagination.test.ts) and Stride §7.6 own these rules; qualify actual omitted-evidence use |
| Commit primary work, then lose Explore attachment/link confirmation | Preserve successful primary work; retry the attachment under the original operation without repeating work or declaring adoption | [Explore writeback tests](../../tests/control_plane/test_explore_result_writeback.py) supply the recovery seam; verify product retry/readback |
| Exercise every enabled periodic-review exit, then disable strategy or omit direction declarations | Progress/successor/blocker/terminal exits retain their current duty; disabling preserves ordinary behavior/history; absent Direction M1 adds no projection or permission | [Stride tests](../../tests/control_plane_ts/stride_policy.test.ts) and [direction tests](../../tests/control_plane_ts/direction_baseline.test.ts); compaction, polling and UI refresh cannot discharge work obligations |
| Provide a discriminating result, an unavailable source, an inapplicable finding and a result supporting the incumbent | Inspect actual next work, artifact and independent outcome; a justified no-change choice is valid. Repeating the finding or declaring use alone is insufficient | Research §11.5 and existing capture/result-use owners; real-model adoption is a separate M3 qualification, comparative gain needs matched evidence |
| Grow history while holding rendered context bounds constant | Omitted counterevidence remains recoverable; report scan/sort/hash/serialization/context cost separately and preserve applicability revisions | Current full-source construction is visible; measure cold/warm workloads before claiming scalable IO or net savings |

The first row is an explicitly authorized requirement-change variant of the
unchanged-contract episode above. It does not authorize an Agent to weaken
acceptance during ordinary optimization. Run fault cases at their existing
owners before composing the supported product journey; add live-model and
matched studies only for the claims that need them.

### Delivery and benefit qualification

| Delivery slice | Existing owner and decisive evidence |
| --- | --- |
| Remove one measured overhead | S2/S7/S10 and optimization guide: same-workload base/head comparison with required semantic, replay and real-backend checks |
| One exploration affects subsequent work | Explore and Stride §7.6: actual model decision, current evidence, attributable artifact and independent outcome; preserve an explicit no-change choice |
| Recover one provider-result/wakeup path | Stride §7.5, Host and monitor owners: duplicate/lost/late event, restart, stop, source-change and original-operation readback through the supported product entry |

These slices reuse the existing roadmap and RFC milestones. They are not a
new task ledger or a requirement to finish unrelated migrations first. Review
current related changes before implementing: cadence inheritance and review
enforcement, correction retry identity, and existing codec/Explore performance
repairs may already cover parts of the intended slice.

Measure accepted-outcome quality, omitted requirements, total time/tokens/cost,
correction-to-action delay, recovery loss, duplicate effects and user attention.
Pin source, Host/model, task/input, authority, evaluator and budget; retain
failed and incomplete samples and distinguish cold/warm paths and history sizes.
Choose evaluation thresholds before assessing a candidate. Synthetic fixtures
qualify mechanical behavior; live adoption and comparative benefit need their
own evidence. Lower overhead with worse acceptance or recovery is not success.

Ordinary behavior-preserving cost repairs can proceed under existing contracts.
Changes to default admission, review, retention, authority or settlement belong
in the owning RFC/contract with compatibility and rollback evidence. Keep optional
strategies scoped until qualified; disabling a strategy preserves historical
evidence and does not cancel admitted work or erase recovery obligations.
