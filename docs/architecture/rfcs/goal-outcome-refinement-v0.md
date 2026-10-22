# RFC: Persistent Goal outcome refinement (v0)

- Status: Implementation proposed; installed/model adoption and comparative benefit remain unqualified.
- Owner: Core Goal acceptance and work planning (S3/R4), with S1/S5 callers.
- Language mirror: [中文](goal-outcome-refinement-v0.zh-CN.md).
- Existing contract: [shared Goal alignment](shared-goal-alignment-and-governed-amendment-v0.md#37-current-work-and-goal-requirements).

## Problem and current evidence

An important deliverable can remain a sentence inside a broad recurring Todo.
Later user corrections guide individual turns without preserving the complete
set of requirements. Reordering a presentation can then accidentally remove an
accepted introduction. Completing a task, publishing a candidate or switching
work does not establish that the deliverable meets the accumulated intent.
This is a generalized reproduction; private conversations and live state are
not part of this document or its fixtures.

Current owners solve adjacent problems:

- Todo owns executable work, claims, leases and completion. One broad recurring
  Todo does not identify every independent outcome it supports.
- Agent Vision is a bounded current route and checkpoint; it is not a durable
  catalogue of all promised deliverables and their cumulative requirements.
- Governed Goal amendment preserves root commitments but its executable class
  currently instantiates owner-authorized work templates. Ordinary refinements
  are not arbitrary new templates or permission grants.
- Goal acceptance owns configured validators, work bindings and current evidence.
  Preparing a check is not adopting it, and passing local checks is not whole-Goal
  completion. Direction materials supply source revisions, not work ownership.

## Decision

Add structured supporting outcomes inside the existing canonical Goal authority,
owned by acceptance. Each outcome has a stable ID, responsible Agent, original
objective, cumulative requirement IDs/descriptions, linked existing Todos and
bounded source references. This is a planning declaration and default evidence review, not a new independent
Goal, scheduler, capability, independent acceptance verdict or permission envelope.

The original Goal and its root commitments remain untouched. An outcome's original
objective and existing requirement descriptions cannot be overwritten by the
refinement operation. Refinement adds requirements and work links with source
provenance and an operation receipt. A conflicting correction must be reconciled
explicitly; v0 does not pretend append-only refinement implements general
supersession or governed root-intent amendment.

Use a separate child Goal only when independent ownership, budget, lifetime or
execution boundary warrants it. Importance alone does not require another runtime.
An outcome is useful when it has a separately recognizable result and criteria,
especially across multiple turns, tasks or repeated user corrections. Do not
promote every message, housekeeping action or intermediate step.

## Ordinary user messages

<a id="current-operating-objective"></a>

### Current operating objective

The original declaration can become an obsolete description of current work.
Reading the latest file bytes does not establish semantic freshness. R4's Goal
owner now proposes a bounded, owner-applied operating-objective revision in the
existing canonical authority. `goal_objective_revision_v0` is a new shared
planning contract owned by `goals/goal_objective.ts`, not a new capability or
provider. File/SQLite remain the ordinary local transports; shared lease
admission uses the same typed rule on PostgreSQL. Python adapts CLI, host and
proposal IO; it does not decide revision/adoption semantics.

The current objective, reason and opaque source reference form an ordered,
digest-linked history. The original declaration, root non-goals, acceptance,
permissions, budgets, stops, existing Todos and leases are retained. This does
not authorize a purpose outside those commitments. Changing those commitments
requires their existing governed authority or a new Goal lifetime. Outcome
refinement remains append-only; it cannot silently rewrite a requirement.

Replanning is the primary semantic review point. Its existing planning guidance
compares the current operating objective and cumulative requirements with the
original intent, explicit user direction and actual work, reusing delivered
`core_goal`/acceptance context. The Agent distinguishes a misleading objective
from a scope conflict, missing evidence or stale display text, and records the
evidence and judgment in its existing Vision/`path_delta`. A justified no-change
decision is valid; review does not require a revision, a new per-Turn receipt or
another poll. Scope conflicts do not authorize rewriting earlier commitments;
missing evidence remains unresolved, and display drift belongs to its projection
owner. This is semantic Agent guidance, not a prose classifier or a new admission
gate. The existing typed authority, stop and replan-discharge rules remain.

Ordinary conversation keeps the callable API without proactively directing a
revision. When a material user direction change makes the recorded objective
misleading, preparation retains the existing owner-review boundary. The native
`loopx_collaboration` tool exposes `prepare_goal_objective`, and fixes its
Goal/Agent/source Turn instead of accepting caller-supplied routing. Preparation
uses the existing `goal.update` review/apply channel, with no Goal effect.
**Goal settings → Task ownership → Current objective and revisions** lets the
owner review an Agent's proposal or enter a revision, apply it and read back the
actual current text and each peer's adoption. Other broad `goal.update` effects
remain unavailable. A proposal, a source label or prose in a document cannot
authorize an owner effect.

CLI preparation uses `goal-acceptance prepare-objective --goal-id GOAL
--agent-id AGENT --document revision.json --operation-id ID --execute`, where
the document contains `objective`, `reason` and `source_ref`. `--execute` here
saves the proposal only. An authorized local owner can use `revise-objective`
with the same document, a stable operation id and `--expected-provider-revision`
from `goal-acceptance inspect`; it previews unless `--execute` is supplied.
It rejects Agent actors. Never use the owner spelling to impersonate authority.

Once applied, the canonical objective is carried through normal Todo/status,
work context, planning horizon and replan reads. Team plans already bind the
canonical provider revision, so an older plan cannot survive an objective
change. Unadopted peers receive an existing frontier/replan gap; native member
starts and new canonical claims/lease acquisitions reject
unadopted execution. Old active Turn recovery, lease renewal and release remain
available. This is a next-execution boundary, not a mid-Turn interrupt or a new
scheduler/wake system.

Each peer reconciles its own work through normal Todo/Vision operations, reads
`goal-acceptance inspect --goal-id GOAL --agent-id AGENT`, then records
`adopt-objective` with `revision`, `digest`, `work_digest` (the returned
`objective_work_digest`) and `review_summary`, the current provider revision
and its own stable operation id. Native callers use `adopt_goal_objective`.
Adoption is a recorded work review, not proof of successful work or permission.
An ordinary Vision ACK cannot substitute for it. One peer can continue after
adopting while others remain pending. A later peer must adopt before acquiring
work even if it was not registered at revision time. Competing writes use CAS;
lost replies recover the original receipt, and replaying an older adoption
cannot acknowledge a later revision. Old configured verification becomes stale
against a changed objective. A successor lifetime cannot inherit its
predecessor's objective implicitly.

Revisions are exceptional direction changes, not per-Turn maintenance. Ordinary
progress, Todo/route changes, diagnostic feedback and wording polish do not
justify a new shared objective. Agents reuse a pending proposal for the same
objective rather than prompting the owner again or polling. The owner reviews
semantic necessity; the runtime rejects identical/whitespace-only objectives
but does not pretend to detect semantic equivalence. An unchanged repeated
adoption does not write a provider revision. Work changes and peer adoption
never invalidate an Agent's already-current objective adoption. Owner previews
bind the prospective objective digest, so unrelated work/peer writes need no
new review; changed objective/source/lifetime still invalidates that review.

No revision is inferred from age or installed automatically on upgrade.
Unchanged Goals retain their existing execution rules; semantic review runs at
replanning, while a revision still needs an actual material user correction. After adoption, ordinary
Turns do not require another acknowledgement. Stored revision history is bounded
and never silently pruned. Before adoption, revert the code if necessary; after
records exist, retain readers and admission fences, and roll back intent with a
new owner-reviewed revision rather than deleting history.

Validation must cover real proposal/apply, a second Agent's reads/adoption,
competing bases, old-response recovery, in-flight settlement and new-execution
refusal on the actual stores, plus the packaged UI. Scripted native tool calls
qualify the shipped tool/effect path; they do not prove a model will always
recognize stale semantics. Model adherence, installed-host adoption, live Lark
delivery and matched benchmark benefit remain separate qualification. Lark
conversation workers reuse the native tool contract; this change does not
rename topics, send messages or introduce a separate amendment authority.

The same ordinary/vision-gap host fixtures measure 2,921 guidance bytes versus
1,984 before this placement change. All earlier review/continuation clauses are
retained. The guidance-only regression ceiling is 3,000 bytes instead of 2,000;
the 8 KiB envelope warning and transport/execution budgets are unchanged. The
ordinary fixture remains within that envelope (6,842 → 7,779 bytes); the existing
oversized vision fixture remains disclosed (20,211 → 21,148). Ordinary work reads
drop the proactive revision hint, and conversation retains tool availability and
applied-revision adoption. These measurements establish delivery cost, not model
adherence or a long-horizon benefit.

The bound Agent distinguishes a direct human request from heartbeat instructions,
quoted documents, tool results and peer messages using the actual host context.
No substring classifier infers user authority or importance. Before continuing a
material new deliverable or correction, the Agent reuses inline outcome context
(or inspects it only when missing),
refines the matching outcome and links work through normal Todo operations.
It retains previously stated requirements and records the latest source reference.
If a reliable host message ID is unavailable, use a stable local opaque evidence
reference; do not manufacture verified sender provenance.

The source label is attribution, never authentication or authorization. The host
continues to own identity and scope; a registered Agent can refine its own
outcomes. Root changes, additional permissions, publishing and executable checks
retain their existing authority. Existing authorization should not cause a new
confirmation merely to record an in-scope refinement. Unclear intent can remain
in conversation until a material consequential choice is resolved.

A CLI/skill bridge makes this available in external hosts after upgrade. It is
not a universal message interception hook: host context delivery and Agent
adherence require separate qualification. App and Lark callers reuse the same
native owner when using the bridge, rather than a second parser or state file.

## State, reads and recovery

Use the promoted canonical authority, its writer fence, provider CAS, append-only
operation journal and existing Goal lifetime checks. No fallback to Markdown on
provider failure. A stable operation ID recovers a lost reply; conflicting retries,
stale revisions, another Goal instance and another Agent's mutation are rejected.
No existing Goal acquires outcomes or changed scheduling merely by upgrading.

The native chat adapter saves the original host-bound request before dispatch.
An identical operation in the same conversation can reuse that source Turn after
a later Turn or process restart; the host still rechecks current admission,
Goal instance, Agent and the original revision/document. The saved envelope is
transport provenance, not a success receipt. Operations committed before this
envelope existed cannot recover a lost source by guessing it. Outcome reads and
refinements do not create member deliveries or consume delegation wake evidence.

The 39-line envelope reservation stays in `ChatSessionStore`, which already owns
Session existence, private paths, locking and atomic writes. Moving it into
external message admission or an execution store would split that ownership
without reducing state complexity. The original slice measured 2,016 to 2,055
lines. Rebasing onto main `b5a1a816a` retains the same 39-line increment: 2,028
to 2,067 lines, which is the current reviewed ceiling with no extra headroom.
Canonical acceptance receipts and current admission remain with their existing owners.

The first exact lifetime binding retains legacy outcome declarations, whether it
arrives through refinement, check configuration or explicit source binding.
Recreation keeps them with the retired owner. A successor can declare fresh
outcomes without inheriting the predecessor's requirements, and replaying an old
operation cannot replace the current projection. A legacy registry entry with
omitted status retains its existing active meaning; explicit inactive states
still reject refinement.

The acceptance readback includes declared outcomes without requiring executable
checks. All Goals share default outcome maintenance, evidence review and gap
projection; the current state and readback have no enable switch. A refinement
cannot submit independent passing evidence, execute commands or change Goal lifecycle.

At a meaningful delivery/experiment milestone, the Agent compares the artifact
with every retained requirement and records `met`, `unmet` or `uncertain`, with
reasoning and evidence references. This default self-assessment can settle a
planning gap for an unconfigured requirement. It remains visibly separate from
independent verification and owner approval. A configured criterion cannot be
bypassed by self-assessment; matching current checks and work coverage still
supply its result. Todo closure alone supplies neither result.

Review binds the cumulative requirements and linked work declarations. Changing
these invalidates its basis. Evidence references are Agent assertions: v0 does
not resolve or fingerprint the cited artifacts, so editing an artifact requires
another review. Do not call a retained reference proof of freshness or quality.

Declaration provenance and progress evidence have separate lifetimes. Creation
and new requirements need `source`; existing-outcome reviews and work-link
maintenance may omit it, preserving all retained origins, including at the
64-source limit. New observations belong in `review[].evidence_refs`, its note,
owning material and operation receipt. Native `refine_outcome` similarly omits
`source_summary` for these updates while retaining its host-bound request/retry
envelope. Explicit sources remain additive and immutable; overflow fails with
guidance rather than dropping provenance or raising the limit. A new declaration
cannot use the review path to evade provenance, ownership or check authority.

Declarations and current evidence reviews have independent 128 KiB UTF-8 JSON
budgets in the same canonical snapshot. The declaration budget includes Goal
identity, original objectives, requirements, work links and every source; it
excludes only the optional `review` fields. The review budget counts their exact
additional serialized bytes. This explicitly raises the combined outcome-state
ceiling from 128 to 256 KiB; it is a resource tradeoff, not a historical budget
pass. Per-field/count limits, operation history and provider transport limits
remain unchanged. Neither envelope can borrow from the other.

The previous shared cap admitted a 131,000-byte declaration but prevented its
next complete evidence review. Keeping that cap would require rejecting valid
progress or moving retained records into a new reference/hydration protocol.
The bounded extra envelope keeps existing synchronous consumers, one atomic
snapshot, receipts and recovery intact. No compression, external authority,
provenance pruning or migration is introduced. Oversized reviews fail before
commit, retain the prior snapshot and direct detailed evidence to its owning
material. Existing stored states remain readable by older versions, but an
older writer may reject further refinement above its combined cap; rollback
must not delete declarations or historical receipts to make them fit.

The synthetic 20-outcome/100-requirement case grows from 131,000 to 131,753
stored bytes after review; its full outcome projection is 154,503 bytes. On the
same already-admissible 19,454-byte input, base and candidate produce identical
state/projection. An alternating 100-sample owner-refinement plus projection
measurement gives base/candidate p50 1.025/1.034 ms and p95 1.387/1.417 ms. This
is a local pure-owner cost observation, not a CLI/provider SLO or long-horizon
benefit. Real File/SQLite CLI and native conversation tests cover the crowded
case; File/SQLite/PostgreSQL authority tests cover restart, replay, stale bases
and atomic overflow rejection. No installed-state expansion is claimed here.

The existing admitted-work context carries the complete outcome declaration;
when inline content is present, no duplicate acceptance inspection is required.
Changes can accompany ordinary `refresh-state --goal-outcome-update-json`, or one
native `refine_outcome` call. Unchanged work needs no extra call, round trip or
review record. This is sequential composition: refinement commits before the
normal writeback. If writeback fails, its returned refinement receipt identifies
what committed; retry the identical operation rather than assuming rollback.

| Event | Default action | Existing mechanism |
| --- | --- | --- |
| Material direct-user deliverable/correction | Add to the matching outcome, retaining prior intent | Host context + native refinement or skill/CLI bridge |
| Resume, focus switch, replan | Reuse full requirements, then choose work | Shared work context; missing-source required read only |
| Meaningful artifact/experiment milestone | Review all retained requirements against actual evidence | One refinement, optionally batched with normal writeback |
| Unmet/unreviewed outcome without linked active/waiting work | Carry a typed gap into planning | Existing frontier replan, only when no runnable advancement wins |
| No relevant change | Continue work without another protocol exchange | Existing admission and writeback |

An open/blocked/deferred linked Todo retains its existing owner and wait rules.
Other runnable work is not preempted merely because a new outcome gap exists.
Replan should use evidence to choose the next useful action or justify continuing
it; neither a gap nor a review mandates a direction change. No new scheduler,
periodic audit, quota debit or outcome-driven root stop is introduced.

An outcome gap uses a canonical requirements/work checkpoint, not a timestamp
invented on read. An accepted, same-Agent Vision writeback bound to the original
replan obligation acknowledges only that checkpoint. Ordinary settlement and
source/review-note bookkeeping do not rearm the same obligation. New requirements,
material linked-work or completion evidence, and changed configured-check or
host-observed oracle versions do rearm it, as do changed default-review
conclusions or evidence references. The bounded checkpoint covers the
Agent's complete uncovered outcome set, including outcomes omitted from detailed
triggers, while retaining separate held-Todo evidence. This suppresses duplicate
review, not unresolved requirements, existing wait/stop rules or authorized work.
Agent-supplied artifact references remain assertions; only the existing pinned
oracle path supplies host-observed file versions.

Status and refresh observe those configured pins from the same canonical snapshot
as their Todos and checks, using the current source Goal's workspace. A stale
shared roster cannot select an older checkout. The private observation basis
must match the public contract revision and digest; it introduces no second
authority fetch, verifier execution or change to the saved verification receipt.
Refresh retains the original canonical snapshot for durable source identity.

## Simplify the default path

Every Goal uses the same default loop: retained requirements → evidence review →
replan when useful work is uncovered → subsequent execution. Delete the overall
`enabled` switch, not merely change its default or hide it in the UI. The current
storage and readback have no such field. Executable checks are one evidence path;
when no check configuration exists, the same requirements/review/replan loop runs.

Upgrade decoding maps previous state into this one model. There is no old-Goal
cohort or activation step. Existing command configuration, exact Goal lifetime,
work bindings and evidence keep their meaning; previously removed checks are
represented as absent configuration. Removing checks retains a revision fence
and immutable history, so an obsolete reviewed proposal cannot silently apply
after configuration changes. Older receipts remain replayable without executing
commands. Default maintenance adds neither command execution nor root stop rules.

An existing outcome update needs only its ID, source and changed fields. The
owner resolves its immutable objective/Agent and retained requirements/work;
creation still requires an explicit objective and owner. A milestone review can
therefore accompany the existing writeback without repeating those declarations.

For executable checks, accept `{outcome_id, criteria: [{id, validation_argv, ...}]}`.
The typed owner resolves requirement descriptions, objective and linked work from
that outcome. The prepared proposal shows the resolved snapshot and commands;
apply resolves them again and rejects changed requirements/work before execution.
The snapshot supports review and does not become a second requirement authority.
Legacy standalone check documents remain supported. There is no implicit command
execution, new permission or root completion decision.

Opaque requirement IDs, including `:` and `@`, retain exactly the same identity
through configured criteria, bindings, verification and readback. This does not
extend the separate Todo-ID, filesystem-path or command grammars.

## Open-ended research and no premature stop

Supporting outcomes are subordinate to the original research objective. A local
hypothesis review, successful experiment, all local requirements being met, or a
short plateau cannot stand in for root continuation/budget/stop criteria. This
change never writes `achieved`, `no_followup`, budget or terminal state. Existing
valid stop and wait conditions still apply; it neither forces budget exhaustion
nor invents filler work. An unresolved requirement can remain uncertain while
useful authorized research continues.

Future EdgeBench qualification follows the [existing measurement contract](long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model):
use matched task/model/reasoning, source and checkpoint, scorer, feedback access
and total budget. Preserve late-stage/common-window comparisons and valid,
invalid and missing distinctions. Compare task-native final/best outcomes,
total model/tool/CLI cost, latency, human intervention, unused budget reasons,
and premature-terminal/harness-conflict rates. Include protocol context and
review cost; a better local score cannot excuse worse final outcomes or early
stopping. Use solver-visible evidence during work and hidden evaluation only at
the permitted terminal phase. Do not launch or alter an experiment from this RFC.

The acceptance target is effectiveness and complete-cost efficiency at least
baseline, with no premature-stop regression. Deterministic continuation parity
is a prerequisite, not proof of live-model noninferiority. Actual model adoption,
artifact quality and matched benchmark benefit remain qualification work.

## Product journey and delivery slice

The Agent records the outcome from the user's request without asking the user to
retype JSON. Goal Overview shows it beside the current direction and acceptance,
with original objective, accumulated requirements, responsible Agent and work.
The local owner can add a requirement in place and read the native result. Stale
writes and unavailable authority expose recovery without blind retry. CLI and
frontend share the same typed owner. No new configuration editor or capability ID
is needed: provider is the existing promoted local authority, builtin Core origin.

Submit this design before implementation. The implementation slice includes native
refinement, required reads, preserved unresolved coverage, CLI, existing Goal UI,
external-host skill guidance, negative cases and packaged backend readback.
It does not upgrade the current installation, modify another chat's live work,
merge the PR or launch a paid model experiment.

## Cost and bounded refactoring

The heartbeat rule grows by 523 characters; every previous clause is retained.
On the same supported scoped fixture, compact guidance grows from 7,286 to
7,809 characters including the existing Reward Memory allowance; the ordinary
thin fixture in the original slice grows from 3,147 to 3,670. Compact retains
its 7,200-character presentation ceiling and separate 640-character allowance.
These are measured context costs, not an efficiency claim or a runtime budget
increase. Unchanged work adds no Agent CLI interaction, but outcome-bearing
context still has read/projection and token costs that benchmarks must include.

After integrating main `4f6e20a13`, the unchanged public CLI fixture has a
normalized thin body of 3,306 characters before outcome guidance and 3,829 after;
the supported 320-character scope measures 3,595 and 4,118. The earlier 3,700
proposal cannot fit this complete current rule. Set the thin body ceiling to
4,200 and its emitted JSON ceiling to 4,500; the final emitted JSON is 4,172
characters, or 4,461 with the supported scope. Standard Markdown is 6,076 under
its unchanged 6,200 ceiling. This leaves 82 body characters and 39 emitted JSON
characters at the scoped boundary, without shortening scope, authority or prose.

The unchanged real Linux CLI fixture with a 128-character root measures brief JSON at
10,260 characters on main `4f6e20a13` and 10,783 with outcome guidance; Markdown
measures 8,559 and 9,082. Both grow by the same 523 characters and fail the old
10,500/9,000 ceilings. Keep all instructions and full command paths. Combining
main's 10,800 native-path JSON allowance with the measured 523-character guidance
increment sets the brief ceilings to 11,400/9,600, leaving 617/518 characters on
this Linux fixture. The recorded Windows depth-0/6/12 baselines plus that increment
derive 11,005/11,125/11,245 JSON characters; these are arithmetic projections,
not native Windows qualification of the combined source. The 58/115 line limits,
strict first-transition allowance and runtime quota remain unchanged. These
measurements retain the original failures and do not establish token or model
benefit. See the [interface budget contract](../../reference/contracts/interface-budget-contract.md).

The 96-row base/head CLI matrix separately measures +523 characters on thin,
brief, compact and full heartbeat carriers. Before the body repair, thin grew
by 524 because its overflow flag changed. The existing differential observer
allows 544 characters/bytes only for the complete frozen outcome-instruction
transition on these four carriers. Missing/partial wording, an already-upgraded
base, other surfaces and excess growth keep ordinary failure behavior. No line,
semantic-key or shape allowance is added.

The existing acceptance work digest is extracted unchanged for both acceptance
and review-basis invalidation, avoiding a second work-identity policy or circular
owner import. Larger Goal migration/supersession work is deliberately deferred.
The provider is the existing canonical authority (File/SQLite qualified here),
builtin Core origin, with no new public capability or Python decision owner.

Native continuity qualification is distinct from external Host dispatch. The
native body-edit regression checks persistent required obligations, semantic ACK,
same-Turn settlement and observation without ledger writes. Durable deduplication
of unresolved external-worker attempts remains with its worker owner; it is not
delivered by this foundation. A comparison using that worker must freeze the
same dispatch behavior in both arms and retain any repeated-call costs. The
earlier mixed Host/native assertion is not qualified by a native-only pass.

## Open-work review continuity

An unfinished linked Todo covers work; it does not prove progress or satisfaction.
The existing typed-progress and effective-Turn review mechanisms can review that
same open Todo. `goal_outcome_uncovered` retains its narrower no-linked-work
meaning, including existing blocked/deferred waits. No additional timer, score
threshold, forced Todo closure or periodic audit is introduced.

The replan source codec previously omitted default outcomes and retained only
seven configured-check summary fields. Ordinary work context already carried
the complete acceptance readback; the dedicated replan `core_goal` and
Goal planning horizon did not. Both now reuse that same owner-projected readback,
including cumulative requirements, self-review provenance, linked work, check
results and lifetime/freshness facts. This removes a lossy parallel field list;
Python transports the source and existing TypeScript owners retain decisions.
No new capability, provider, persisted vocabulary or UI operation is added.

Default replan guidance asks the Agent to review unmet requirements even with
open work, including when no structured outcomes or checks exist. Strongest
first delivery remains the objective;
diagnostic validation and available feedback are means to strengthen it. An open
Todo, repeated local pass or flat metric alone proves neither progress nor
exhaustion. Useful negative findings remain valid reasons to continue. This is
Agent guidance, not a new machine-enforced success or stop rule.

Qualify this seam through File/SQLite CLI refinement → stall/cadence review →
actual artifact repair → evidence writeback → one settlement → continued open
Todo. Check the same acceptance source in TurnEnvelope work context, reject
evidence-free review, preserve retry identity and distinguish configured checks
from default self-assessment. Existing no-declaration admission, wait rules,
cadence, optional stride gates and root completion authority remain unchanged.
More complete acceptance input costs bytes when acceptance exists; the brief
default guidance also changes ordinary replan packets. Neither is
a latency, token or score improvement claim. The Agent may still miss or misjudge
progress; actual adoption and matched task outcomes remain S11 qualification.

The EdgeBench heartbeat workers use the existing default six-effective-Turn
cadence, independent of Todo completion or configured checks. They do not
automatically declare supporting outcomes or translate background native scores
into Core acceptance. Default review guidance therefore must not depend on those
optional facts. Source preservation applies when the Agent has recorded them.
Native automatic sampling, solver-visible feedback, scoring and worker stop rules
remain with the benchmark provider; no hidden feedback is made available here.

The guidance addition preserves every previous clause and adds 212 text bytes.
On the same CLI fixture, crowded quota grows by 213 JSON characters and Turn
plan by 216; both initially fail the existing growth limits. The existing
continuation observer qualifies only the complete old-to-new wording once with
224 characters/bytes, no added lines or shapes. Missing clauses, an upgraded
baseline, other carriers and excess growth retain ordinary rejection. The two
host fixtures' guidance costs 1,984 bytes instead of 1,772, so their scoped
advice ceiling becomes 2,000 instead of 1,800. That guidance-only change retains
the absolute CLI/runtime budgets; this is a measured input cost.

### Goal context before work selection

The original Goal and recorded acceptance now enter applicable preselection
horizons by default. This is a context change, not another planning round or a
new scheduling rule. The five-work-item summary and explicit completeness/detail
paths remain bounded; seeing the Goal does not make that summary exhaustive.
Flat runnable backlogs also receive the bounded work view. Missing declared
dependencies or gaps do not establish sound ordering; they must not hide that
view. The summary reports omissions and a complete-read route, not a claim that
its five items are the whole plan. TurnEnvelope references identical selected
work text when the reference is smaller, retaining other work and completeness.
The existing configuration owner retains an explicit false for rollback and
comparisons. Settings moves this control into Advanced settings, preserves
machine/Goal precedence and sparse inherited values, and does not rewrite
persisted configurations on read. EdgeBench heartbeat workers pin and record
the resolved boolean before work, including for omitted CLI options; existing
receipts remain historical. TurnEnvelope references an identical inline
acceptance source instead of repeating its body, retaining mismatched evidence.
Source/default qualification remains separate from installed/model adoption.

Matched public fixtures measure 8,077 → 9,370 characters for small Turn plan and
20,805 → 23,745 for multi-Agent quota JSON. The latter initially exceeds the
23,000-character ceiling and existing horizon migration's 2,800 compact growth
allowance. The increase includes Goal context and a previously absent bounded
work summary; discarding the latter would hide useful flat-backlog context.
Retain it and change only that absolute ceiling to 24,000 and the one-time
horizon migration's compact allowance to 3,000. Excess and same-schema growth
remain rejected; other ceilings, per-Todo growth and the 8 KiB envelope warning
are unchanged.
This qualifies the input-cost tradeoff, not token, latency or model-quality gains.

## Decisive acceptance

Use disposable public-safe File and SQLite Goals, never active operational state.

1. Record an outcome and an initial requirement; link one real existing Todo.
2. Process a second synthetic user refinement under the same outcome; preserve
   the original Goal, outcome objective and earlier requirement byte-for-byte.
3. Restart the caller and switch work. The normal admitted-work reads still show
   both requirements and the original source lineage.
4. Finish the linked Todo with one requirement unmet. Readback must not accept the
   outcome; continuation still exposes the gap. Default evidence review can settle planning only; independent acceptance
   supplies any later passing check evidence.
5. Reject lost requirements, changed text under an old ID, foreign-Agent changes,
   nonexistent work, stale CAS and conflicting retries. Exact replay writes once.
6. Compare runnable-research admission before/after missing, uncertain, unmet and
   met supporting outcomes. Keep root state, quota/stop decisions and the next
   runnable action unchanged; test blocked/deferred waits separately.
7. Exercise the packaged Goal UI against the real backend: read, refine, reload,
   stale-write recovery, desktop/narrow view. Verify absent-outcome behavior parity.
8. Accept the original replan checkpoint and settle its Turn. Repeated reads,
   source-only refinement and a changed review note must not demand that same
   review again; material work and oracle-version changes must. Include more than
   three outcomes and mixed held-Todo/outcome gaps without losing either source.
9. Lose a reply after a real native commit, restart the controller, admit another
   Turn and replay the original operation once. Reject changed request identity,
   foreign conversation/Agent/Goal instance, paused state and revoked binding.
10. Bind legacy declarations through each existing first-owner path; recreate the
    Goal and refine its successor, then replay the predecessor without replacing
    current declarations.

The same-Goal form retains unsent text and the original pending operation through
failed refreshes, including a committed write whose reply and reconciliation read
are lost. Retained data is not fresh write authority. After reading successfully,
retry reuses the complete operation and cannot append a duplicate requirement;
explicit rebase requires a current revision and preserves the requirement identity.
This also covers a newer native outcome absent from the retained delivery snapshot:
temporary delivery loading/error revokes writes without unmounting its draft or
pending request. Remote/local source changes and exact Goal lifetime changes reset
the editor instead of moving an old request to a new owner.
Run `node examples/personal-workspace-browser/goal-outcome-recovery.mjs` after the
normal Chat build for this targeted packaged regression. It uses disposable
SQLite/HTTP/CLI authority and injects transport failures, not fabricated commits.

These checks establish product semantics and recovery. Live-model recognition,
installed host adoption and comparative quality/attention gains remain unproven
until separately measured; test counts and a merged PR are not those outcomes.
