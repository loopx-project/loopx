# Control-Plane Developer Course

> For developers who plan to modify LoopX Kernel, CLI, state projection,
> scheduler, or extension behavior.

## Relationship to the Dev Book

The Dev Book gives external developers a complete path from mechanism model to
project onboarding or contribution. The Control-Plane Developer Course is an
independent chapter for developers who need to enter implementation source,
judge rule precedence, locate bounded contexts, or add a new control-plane
rule.

Both share the official protocols and source as authority, but do not maintain
two copies of the full course:

- the Dev Book explains enough mechanism to predict behavior;
- the Course provides Showcase derivations, decision tables, source
  walkthroughs, experiments, and review questions.

## Course map

| Course chapter | Topic | Best entry point in the Dev Book |
|---|---|---|
| [Concept primer](/loopx/docs/development/control-plane-course/00-concept-primer/) | Limited context, externalized state, core concepts | [Four demands](02b-long-horizon-requirements.md) |
| [Long-horizon convergence](/loopx/docs/development/control-plane-course/topic-long-horizon-convergence/) | Direction, evidence, delta, liveness, terminal invariants | [Recovery and boundaries](04-runtime-boundaries.md) |
| [Lesson 1: Harness is the effectful program](/loopx/docs/development/control-plane-course/01-agent-loop-effectful-program/) | Harness as the agent-loop effect interpreter | [One governed turn](03-one-turn.md) |
| [Lesson 2: Architecture from three showcases](/loopx/docs/development/control-plane-course/02-goal-control-plane-architecture/) | Agent / Provider / Capability / Kernel ownership | [Sessions, Goals, and LoopX](02-session-goal-loopx.md) |
| [Lesson 3: First real loop](/loopx/docs/development/control-plane-course/03-first-real-loop/) | Guided start, todo, quota, refresh, spend | [Connect a project](05-connect-existing-project.md) |
| [Lesson 4: State substrate](/loopx/docs/development/control-plane-course/04-state-substrate/) | Registry, events, active state, run history, projection | [Durable state](state-substrate.md) |
| [Lesson 5: Work graph and peers](/loopx/docs/development/control-plane-course/05-work-graph-and-peers/) | Claim, lease, handoff, equal peers | [Work graphs and authority](work-graph-and-authority.md) |
| [Lesson 6: Quota kernel and interaction contract](/loopx/docs/development/control-plane-course/06-quota-decision-kernel/) | `should-run`, route, mode, interaction contract | [One governed turn](03-one-turn.md) |
| [Lesson 7: Host, heartbeat, stateful backoff](/loopx/docs/development/control-plane-course/07-host-scheduler-and-heartbeat/) | Execution context, RRULE, ACK, backoff | [Budget, admission, and observation](04b-budget-and-admission.md) |
| [Lesson 8: Evidence, refresh, self-repair](/loopx/docs/development/control-plane-course/08-evidence-refresh-and-self-repair/) | Material progress, replan, repair delta | [Recovery and boundaries](04-runtime-boundaries.md) |
| [Lesson 9: Add a control-plane rule](/loopx/docs/development/control-plane-course/09-engineering-a-control-plane-rule/) | Invariants, ordered rules, schemas, smokes | [Change a rule](source-change-control-plane-rule.md) |
| [Lesson 10: Layered quality gates](/loopx/docs/development/control-plane-course/10-autonomous-agent-quality-gates/) | Deterministic tests, canaries, model behavior, release gates | [Validation to PR](source-validation-to-pr.md) |
| [Lesson 11: Extensions, governed execution, and domain products](/loopx/docs/development/control-plane-course/11-extension-layer/) | Recoverable external-effect settlement, Explore, Graph/Harness, and domain products | [Extension lifecycle](10-extension-lifecycle.md) |

## Relationship to the Effect Interpreter RFC

Lesson 1 and the
[Agent Loop Effect Interpreter RFC](/loopx/docs/architecture/rfcs/agent-loop-effect-interpreter-v0/)
share one language: the harness is the effectful program around an agent loop,
and state machines are interpretation tables. Start with Lesson 1 before
entering Kernel implementation topics.

## From reading to making a judgment {#reader-checkpoints}

Use [the reading guide's four questions](00-reading-guide.md#judgment-standard): **what facts support
it, why it is allowed or refused, what evidence supports the conclusion, and which entrypoint comes next.**
“Try again” answers none of them. “Stop first” also needs the missing condition, who can repair it, and
when to reassess.

If you have not completed actual work yet, follow [onboarding and the first delivery](05-connect-existing-project.md#first-delivery).
These tests support explanation and contribution, not a substitute for real operation. For everyday operation, start with [reading entrypoints](#read-before-change) and
[diagnostic routing](#diagnostic-routing). Before changing implementation, work through the four exercises:
predict, inspect assertions, and give a conditional next step. Completing the whole Course, creating a
new Goal or acceptance protocol, and committing exercise notes are not prerequisites.

### Environment and evidence limits {#checkpoint-environment}

Run tests from the root of a complete LoopX source checkout, not the business project. They require
Python 3.11+, Node.js 22.22.3+, uv, and the test extra. Installing dependencies may access package indexes.
Record the actual checkout first:

```bash
git rev-parse HEAD
python --version
node --version
uv sync --extra test
```

The first three groups use real local state or CLI calls in temporary directories; the fourth uses
constructed quota inputs. They write test state and may start a local Effect runtime. Never substitute
a live Goal for fixture registries, deletion, or corruption injection. Missing dependencies, collection
failures, zero collected tests, or unavailable runtimes do not count as passing behavior.

Source links are pinned to the inspected commit; commands execute your recorded checkout. Check version
differences first. When tests move, follow the original invariant to the owner rather than copying new
output into the expected result. These tests do not qualify models, real Hosts, remote providers, or the
whole product.

### Read before deciding to change state {#read-before-change}

The complete operating commands and stopping conditions live in the [appendix field reference](appendix-reference.md#read-before-change). This chapter keeps the exercises rather than a second operating runbook. The pytest commands use isolated fixtures, not live-Goal initialization or repair procedures.

<!-- reader-checkpoint:state:start -->
### Check one: which state can admit work? {#checkpoint-state}

Predict first: T1 is done or blocked in the selected authority but open in Markdown. Can it run?
Conversely, does a missing display entry prove work does not exist?

#### Factual basis {#state-facts}

Identify the same Goal's selected authority, exact Todo state, and current Agent constraints. The role
of display and source depends on mode: promoted File/SQLite paths cannot substitute stale Markdown for
the authoritative answer, while a legacy path may still use Markdown as source.

#### Why allow or refuse? {#state-reason}

Refusal follows source state or a failed read, not page wording. Cached open status cannot readmit
done/blocked work, and unreadable authority does not prove that work is open. Display truncation, however,
is not a rejection reason: existing work that satisfies current conditions can be selected precisely.

#### Supporting evidence {#state-evidence}

```bash
uv run --extra test pytest -q \
  tests/control_plane/test_quota_authority_settlement_journey.py::test_stale_markdown_cannot_admit_terminal_or_blocked_work \
  tests/control_plane/test_quota_authority_settlement_journey.py::test_failed_canonical_read_cannot_fall_back_to_markdown \
  tests/control_plane/test_quota_authority_settlement_journey.py::test_exact_selection_reaches_work_beyond_display_limits
```

[The first test](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/tests/control_plane/test_quota_authority_settlement_journey.py)
asserts `decision=skip`, no selected Todo, and a `not_committed` receipt. The second asserts no selection
and zero receipts for this Turn. The third covers legacy/File/SQLite: exact selection returns
`decision=run`, with both selected Todo and settlement identity naming the target even beyond display limits.

These establish source/selection constraints for the tested paths, not provider recovery, projection-only
behavior for every legacy path, or permission to run merely because a Todo is open.

#### Next entrypoint and stopping condition {#state-next}

Use [exact reads](#read-before-change) to establish source and status. For done work, follow existing
continuation rather than reopening it to satisfy a tutorial. For blocked work, inspect
[dependencies and authority](work-graph-and-authority.md). With unreadable source, the
[state owner](state-substrate.md) resolves unavailability; do not let Markdown take over in the meantime.
Continue through the original Host's admission entrypoint only after the source is restored and current
conditions rechecked. A visible page is not an admission receipt.
<!-- reader-checkpoint:state:end -->

<!-- reader-checkpoint:lease:start -->
### Check two: does historical success still grant execution? {#checkpoint-lease}

Predict first: A acquires, renews, and replays the original acquire request. Should the historical
receipt and current lease be identical? What does reusing the key after release mean?

#### Factual basis {#lease-facts}

Read the current lease and original receipt separately. Check Goal/Todo, owner, mode, state, version,
and execution identity. This example covers only the File/SQLite `hard_lease` fixture. The same Agent
name or a past success is insufficient proof for the current execution instance.

#### Why allow or refuse? {#lease-reason}

Returning history must not revive old authority. Renewal requires current lease proof; after release,
the old acquire key is retired and cannot transform the same request into new execution. A new key is
not a bypass either: fresh work admission, current ownership, and version conditions must hold before
acquiring another lease.

#### Supporting evidence {#lease-evidence}

```bash
uv run --extra test pytest -q \
  tests/control_plane/test_canonical_lease_acquire.py::test_public_acquire_renew_complete_and_retired_retry
```

[The test](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/tests/control_plane/test_canonical_lease_acquire.py)
asserts that acquire readback after renewal returns the current lease and retains the original receipt.
Reusing the key after release yields `idempotency_key_reuse`. A new valid acquire advances version;
completion leaves the Todo done and lease released; acquire against completed work yields `todo_not_open`.
`state.exists()` is true after actual completion, not proof that every state file was deleted.

This is sequential execution, not evidence of strong exclusion for soft-claim, real competition after
TTL expiry, or an external service fencing an old executor's writes.

#### Next entrypoint and stopping condition {#lease-next}

Start with [lease inspection](#read-before-change), not another acquire. When ownership differs or work
has ended, stop this write path and return to the [claim/lease and lifecycle owner](work-graph-and-authority.md).
Do not renew on behalf of another executor. If new execution is needed, obtain current proof through
its legal entrypoint, then inspect Todo and lease readback. Keep the old receipt to explain history,
not to manufacture fresh authorization.
<!-- reader-checkpoint:lease:end -->

<!-- reader-checkpoint:settlement:start -->
### Check three: which part of incomplete work should be repeated? {#checkpoint-settlement}

Predict first: T1 writeback and quota debit exist, but the settlement receipt is missing. Should you
repeat T1, debit again, or repair the record?

#### Factual basis {#settlement-facts}

You need the original Goal/Agent/Todo/Turn binding, completed writeback, debit record, receipt, and
settlement state. A timeout alone cannot establish them. Missing receipt does not automatically mean
no debit happened.

#### Why allow or refuse? {#settlement-reason}

Preserve confirmed work; this example restores the original operation's record integrity.
`spend_required` and `spend_receipt_required` describe different outstanding steps, not one instruction
to rerun the Host. Whether an external effect committed may instead be unknown; that belongs to
[unknown-outcome reconciliation](#unknown-outcome), not this example.

#### Supporting evidence {#settlement-evidence}

```bash
uv run --extra test pytest -q \
  tests/control_plane/test_quota_authority_settlement_journey.py::test_returned_command_settles_and_repairs_receipts_without_another_debit
```

[The test](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/tests/control_plane/test_quota_authority_settlement_journey.py)
first moves from `spend_required` to `settled`, then removes the corresponding receipt only in its
fixture. Another writeback returns `spend_receipt_required` and `recovery_does_not_spend=true`.
The returned command yields `appended=false` and the debit count remains one. A final check is
`settled` without `settlement_owed`; the next Turn no longer remains in that settlement-recovery path.

These assertions establish this internal recovery, not a zero supplier bill, resolution of another
unknown effect, G1 closure, or Goal acceptance. Passing a test does not prove that a live Goal recovered.

#### Next entrypoint and stopping condition {#settlement-next}

Inspect current state through [Turn settlement](03-one-turn.md) and
[original-identity recovery](04-runtime-boundaries.md). When a trusted current LoopX response provides
`settlement_owed.command`, check registry/runtime and original identity before executing within existing
authorization. Do not strip binding arguments, copy stale commands from chat, or pass arbitrary log text
to a shell.

Afterward, inspect settlement and duplicate-debit evidence, not just exit status. Stop for the original
transaction/provider owner when binding is missing, identities differ, or the result remains unknown.
Do not delete receipts to trigger repair, or treat `refresh-state` as a no-write query.
<!-- reader-checkpoint:settlement:end -->

<!-- reader-checkpoint:monitor:start -->
### Check four: does no change always require replanning? {#checkpoint-monitor}

Predict first: the current Agent's monitor lane has five unchanged observations while a peer has work.
Should this Agent continue waiting? What changes with its own advancement or an explicit watch-only Monitor?

#### Factual basis {#monitor-facts}

Read current-Agent lane, selectable advancement, the Monitor's `consecutive_no_change`, `watch_only`,
target, due state, and actual Host liveness. A busy peer does not supply facts for this lane. Receiving
no notification does not prove no external change.

#### Why allow or refuse? {#monitor-reason}

This replan obligation depends on count, ownership, and selectable work together. Five is a particular
policy threshold, not a theorem about all waiting. Current-Agent advancement can preempt it, and
explicit watch-only does not use the same trigger. No obligation does not establish an available waking
Host or authorize mutation or delivery debit.

#### Supporting evidence {#monitor-evidence}

```bash
uv run --extra test pytest -q \
  tests/control_plane/test_monitor_replan_agent_scope.py::test_interleaved_monitors_keep_independent_no_change_streaks \
  tests/control_plane/test_monitor_replan_agent_scope.py::test_current_agent_advancement_still_preempts_monitor_streak_replan \
  tests/control_plane/test_monitor_replan_agent_scope.py::test_watch_only_monitor_streak_does_not_create_replan_obligation \
  tests/control_plane/test_settled_replay_construction.py::test_settled_replay_preserves_schedule_without_new_host_effects
```

[These tests](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/tests/control_plane/test_monitor_replan_agent_scope.py)
assert an obligation only for the eligible current-Agent lane, `run` without that obligation when
current advancement exists, and no such trigger for watch-only even at 50. They preset counters and
evaluate policy rather than performing interleaved remote polling. They do not prove concurrent counter
writes, real waking, or backoff timing.

A direct counterexample sits alongside them: **a settled turn's replay**. With an open advancement
successor still present it returns `should_run=false`, while `scheduler_hint.action` is
`preserve_current_schedule`, `next_trigger` is `fresh_turn_identity`, the host action is `none`, and no ACK
is required. Neither the count of `false` results nor this turn's receipt therefore implies the cadence
should slow; slowing waits until the owner reads the frontier as genuinely waiting, and then follows the
four steps in point 4.

#### Next entrypoint and stopping condition {#monitor-next}

Locate the Monitor with [an exact Todo read](#read-before-change), then check wait conditions in
[observation and scheduling](04b-budget-and-admission.md). Even when due, an available Host must reassess;
an old `should_run` is not permission to start. Return selectable work to current admission and replan
obligations to their existing route. With no observation capability or running Host, record that gap
and use [Host onboarding](05-connect-existing-project.md), not fabricated check times or ACKs.

Do not change a normal Monitor to watch-only merely to remove an alert. A changed waiting policy must
serve the actual objective and be accepted by the existing configuration entrypoint. Check its new due
state, conditions, and execution surface afterward, not just whether the alert disappeared.
<!-- reader-checkpoint:monitor:end -->

## Unknown outcomes: reconcile before choosing a retry {#unknown-outcome}

Suppose an external write was sent and its response was lost. Only a timeout and prepared intent remain;
there is no confirming readback. **Unknown occurrence** differs from **confirmed non-occurrence**.
Sending again with another operation id can create a second effect.

In the current Turn settlement adapter's
[`_resolve_prepared_effect`](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/loopx/control_plane/turn_driver/settlement.py),
a missing resolver, resolver exception, or unsupported kind does not authorize execution. A valid
committed payload can be reused; `absent` enters the not-committed branch. This local decision remains
subject to surrounding journal, identity, and recovery conditions, not a universal external-system guarantee.

| Readback under original identity | Why allow or refuse? | Next entrypoint and evidence needed |
| --- | --- | --- |
| Confirmed committed with a valid receipt | Do not repeat an accepted effect | Original settlement/recovery reuses the result; read back remaining steps |
| Confirmed absent | Possible prior commit no longer blocks by itself; other guards still apply | Recheck current authority and binding in the same recovery route before an owed step |
| Unknown, unavailable, or conflicting | Insufficient basis for safe retry or completion | Original provider/transaction owner restores readback; retain identity and name the missing facts |

This is source-based explanation, not a claim that the four exercise groups cover every unknown branch.
Human handling also needs an explicit conclusion about the original effect. A person having looked at
it does not replace readback or automatically grant publication authority.

## Route real problems with the four checks {#diagnostic-routing}

The appendix owns [symptom routing](appendix-reference.md#diagnostic-routing); operating readers need not run source tests first. The main explanations are [state](state-substrate.md), [authority](work-graph-and-authority.md#authority-layers), [settlement](03-one-turn.md#settlement-recovery) and [observation](04b-budget-and-admission.md#observation-owners).

## Integrated exercise: why is delivery still not authorized? {#integrated-judgment}

Return to [the running task](00-reading-guide.md#running-example). This is a synthetic reasoning exercise,
not a new product fixture: T1 was validated and settled at C1, then code changed to C2. M1 still holds
only green CI for C1, G1 publication approval remains open, and T2 has independently reviewable documentation
work.

| Four questions | A sufficiently concrete answer |
| --- | --- |
| Which facts? | Current artifact is C2, available test/CI evidence binds C1, publication scope is unapproved, and T2 independence still needs current verification |
| Why allow or refuse? | R1 and settlement establish history, not C2 acceptance or publication authority; they cannot admit T3. An unrelated Gate need not globally freeze T2, but T2 retains its own authority/capability constraints |
| Which evidence? | Current-revision validation and external readback, an explicit decision covering the right object/scope, and current work readback; a green screenshot alone does not compose into authorization |
| Which entrypoint next? | Obtain C2 evidence through existing validation/Monitor paths; the maintainer handles G1 through Gate/Workspace decision entrypoints; return independent work to current admission. Reassess T3 after conditions hold, not repeat confirmed C1 work |

[Authority](work-graph-and-authority.md), [recovery](04-runtime-boundaries.md), and the
[Workspace](workspace-v1.md) own the corresponding operating instructions. Owner decisions are not all
authorized by automatic-Turn quota. An internal test run also does not establish this real CI result or approval.

Replace the task with a comparison report based on two public sources and the same reasoning applies:
material versions replace commits, methods and citation checks support findings, and an acceptor and
publication scope remain explicit. Recheck affected analysis after a source update; do not turn all old
discussion into new evidence. A finished report does not authorize publication. This is conceptual transfer,
not qualification of another product journey.

### Add one condition: the original Turn is not closed

If T1 still owes blocked writeback under its original identity, independent T2 does not permit same-Turn rebinding. Explain original closeout before new admission selects T2. See [wait sequencing](03-one-turn.md#wait-closeout) for the advanced test and version boundary. The eight test functions above are the selected scope of these four exercises, not an inventory of all tests in the checkout.

Also check collaboration: A and B passing separate validation does not accept their combined result. Use [handoff through integration](work-graph-and-authority.md#handoff-to-integration) to name adopted revisions, integration validation, publication decisions and the final acceptor.

## When understanding is sufficient, and when the chapter needs repair {#judgment-exit}

Complete the exercise with a conditional conclusion, supporting evidence, and the next entrypoint.
When evidence is insufficient, state what cannot yet be determined, what is missing, and who can confirm
it. That is more useful than guessing success or asking vaguely for a rerun.

For a contribution, fit the four questions into existing issue/PR fields: current facts, rule and
counterexample, actual validation, legal continuation, and untested boundaries. Follow
[rule changes](source-change-control-plane-rule.md) and [validation to PR](source-validation-to-pr.md)
without adding an approval form or parallel task ledger. Repair the missing part of a chapter instead
of hiding the gap behind more terminology.

The maintenance test checks exercise structure, selectors, and link anchors. It cannot judge reasoning,
semantic equivalence between languages, or what a real reader learned. Those still need behavior tests,
bilingual editorial review, and actual reader feedback.
