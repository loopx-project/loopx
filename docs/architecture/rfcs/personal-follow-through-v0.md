# RFC: Personal Follow-through (v0)

- **RFC status:** Accepted (effective on merge; this branch proposes the design)
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal
- **Authors / owners:** Personal workspace, work-item and Lark extension maintainers
- **Created:** 2026-10-01
- **Last normative revision:** 2026-10-01
- **Implementation baseline:** `f49b4a0`
- **Related contracts:** [overall roadmap](loopx-overall-roadmap-v0.md), [App continuity](app-conversation-and-async-inbox-v0.md), [extensions](../../reference/extensions.md)
- **Language mirror:** [中文版](personal-follow-through-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1–10 define design and acceptance; section 11 defines delivery order;
section 12 records open decisions. Appendix A records baseline evidence only.
Both languages change together. Merge accepts a design; it does not establish
implemented behavior, installed qualification, or permission to read accounts.
Dated execution evidence belongs in the existing RFC ledger when first available.

## 1. Decision summary

Deliver one opt-in personal responsibility: follow commitments in selected Lark
conversations, prepare authorized supporting work, and return a sourced daily
brief in the owner's desktop conversation. The owner can correct, dismiss,
pause, or inspect every item without learning Agent/Turn vocabulary.

Reuse canonical User Todos for human commitments and Agent Todos for delegated
preparation. Proposals carry source lineage; they do not introduce another task
ledger. Existing typed Todo, authority, scheduling, effect and return owners
remain authoritative. Source text and model output cannot grant permission.

This RFC approves no automatic external sends, broader account access, new
scheduler, cloud deployment, or claim of a completed personal-agent product.

## 2. Problem and motivation

A person promises a draft on Friday, receives a Monday deadline correction,
and later shares the draft. A useful assistant retains one item, updates its
basis, prepares permitted materials and distinguishes delivery from acceptance.
Today individual inbox, Todo, report and Chat components do not demonstrate
this continuous user outcome. Summarizing each message independently can create
duplicate tasks, assign other people's promises to the owner, or announce false
completion.

Invariants:

- Every proposed commitment has an authorized source, responsible identity,
  evidence and explicit uncertainty; quotations and questions are not promises.
- One source replay produces at most one corresponding canonical mutation.
- An extraction, ACK, draft or sent message alone cannot complete a commitment.
- User correction survives later model runs and stale source replays.
- Source freshness, work status and execution availability remain separate.
- Reading a group grants neither speaking for its members nor sharing their data.

## 3. Scope and non-goals

The first profile supports one owner, one local runtime, an existing or explicitly
created private Goal, selected authorized Lark groups, local draft preparation,
and an owner-only desktop brief. Bot-visible coverage must be displayed. Mapping
the owner to a verified platform identity is mandatory; display names are not ids.
Private-message or full-account coverage is not implied by group access.

Email, DingTalk, calendar writes, bidirectional external task synchronization,
cloud 24/7 service, multi-Agent teams, and a connector marketplace are deferred.
Cross-source automatic identity matching is also deferred. No new personal-memory
platform or UI redesign is required for this profile.

## 4. Current-system contract

| Existing owner | Baseline behavior | Missing outcome |
| --- | --- | --- |
| [Lark extension](../../../loopx/extensions/lark/README.md) | Message collection, bounded history and replies | Qualified personal commitment interpretation |
| [External connector runtime](../../../loopx/extensions/external_connector_runtime.py) | Scoped capture, cursor, deduplication and settlement | Admission of this profile's durable semantic effect |
| [Todo completion contract](../../reference/canonical-todo-completion-update.md) | Canonical User Todo completion and dependent-work rules | Evidence-backed commitment mutation mapping |
| [Manager context](../../../loopx/capabilities/manager_context/README.md) | Scoped delivery, recipient decisions and return | Whole personal follow-through journey |
| [Periodic report](../../../loopx/capabilities/periodic_report/README.md) | Reusable report envelope; existing preset uses public-safe progress | Separate owner-private source profile and delivery qualification |
| [Lark settings](../../../apps/presentation/dashboard/src/features/personal-workspace/lark-settings-page.tsx) | Apps, source connections, ingress and health | Profile scope, owner identity, pause and repair journey |
| [App continuity](app-conversation-and-async-inbox-v0.md) | Partial entry, late-return and recovery implementation | Installed real-executor acceptance remains open |

These are inspected code/contracts, not evidence that this feature has shipped.
The [office showcase](../../product/use-cases/office-operations/office-operations-connector-showcase.md)
is a design precursor. This RFC narrows its first concrete personal workflow.

## 5. Proposed architecture

### Placement and authority

Compose existing capabilities `lark-event-inbox`, `manager-context` and
`periodic-report`, canonical Todo operations and the existing App conversation.
Provider id is `loopx-lark`, a bundled optional extension. No new built-in
capability or installable connector is needed. Interpretation is an Agent task;
provider-neutral validation and mutation mapping belong in the existing typed
work-item boundary. Lark authentication, ids and history remain in its provider.
Implementation language is TypeScript, compiled to JavaScript and executed on
Node.js. This includes the Lark provider adapter, event normalization, semantic
proposal validation, canonical-operation integration, report composition and
recovery. Desktop UI uses TypeScript/TSX. Tests use the existing JS/TS tooling.
Do not add Python modules, Python subprocess bridges or a second Python decision
owner for this workflow. JavaScript is limited to existing tooling conventions.

Python source links in section 4 describe the audited baseline, not the target
implementation. Reuse established typed owners and supported APIs. If a required
operation is available only through Python, migrate the smallest cohesive owner
and its active consumers to TypeScript with characterization, real-path parity
and rollback evidence before claiming that slice complete. Do not route around
canonical authority with direct state writes. M1 must identify and resolve these
dependencies; the final workflow must run without a Python process. Unrelated
repository-wide migration remains out of scope. Only add a helper when its first
real consumer needs it.

### Proposal and task mapping

Use the existing typed action/proposal store and lifecycle. Define a versioned
follow-through payload there, not a separate database or competing status enum.
The payload contains source binding and opaque event/revision references,
verified responsible identity, candidate title, proposed operation, target Todo
identity and expected revision for updates, evidence references, and an optional
due date with timezone and interpretation basis. Missing dates stay absent.
Explicit clear operations differ from omitted fields. Invalid dates or identity
mismatches reject the mutation; ambiguous responsibility or dates stay proposals.

Allowed semantic outcomes are no follow-up, clarification, create, amend,
propose completion, and dismiss. Represent these as typed variants at
implementation; preserve canonical Todo transitions. Completion requires the
owner's explicit decision or a previously authorized verifiable criterion.
“I sent it” may support delivered status in evidence; acceptance by another
person requires that person's evidence when the commitment requires acceptance.

Record `source -> proposal -> canonical Todo -> preparation artifact -> evidence`
lineage through existing reference/receipt surfaces. User Todo owns the personal
commitment. A linked Agent Todo owns a bounded preparation outcome; completing
it does not complete the human commitment. Merge/split decisions preserve old
references and require scoped review when identity is ambiguous.

### Capture, interpretation and recovery

1. Validate current grant, source binding and verified owner identity; capture
   a bounded incremental page through the existing inbox.
2. Interpret messages with only authorized context and relevant open items.
   A model proposes a match; deterministic code checks scope, revision and grants.
   Text similarity alone cannot merge items or classify completion.
3. Initially persist proposals for create/amend/complete; offer batch review.
   A standing grant may later permit a narrowly defined local mutation class.
   Authorized reading and draft preparation do not prompt on every step.
4. Apply through canonical operations with stable identity derived from binding,
   source event/revision and operation. Bind payload digest; same identity with a
   different payload conflicts. Source edits produce new revisions. The same
   semantic commitment across separate messages needs an explicit existing-Todo
   match, rather than transport deduplication.
5. Persist effect/no-follow-up receipt before ACK or cursor advancement. A
   durable proposal is a captured input outcome, not commitment completion.
   After a crash, reconcile the original proposal and operation receipt before
   retrying. Do not regenerate a second operation from a lost response.
6. Prepare authorized materials through existing worker admission; return the
   versioned artifact to the originating desktop conversation. Compose the brief
   from scoped current state with source freshness and unresolved items.

Concurrent source amendments use expected Todo revisions. A newer user edit
wins over an old proposal; conflict requires reread and a revised proposal.
Deleted/edited source evidence invalidates outstanding proposals as applicable;
existing completed facts retain provenance and show unavailable evidence.

## 6. Alternatives and design choices

| Alternative | Decision and trade-off |
| --- | --- |
| One-shot message summaries | Useful baseline for comparison; lacks durable follow-up and corrections |
| Another personal-task store | Reject duplicate lifecycle and divergent completion truth |
| Convert every message into a Todo | Reject noise and responsibility misclassification; keep bounded proposals |
| Build all connectors first | Defer; qualify one source and reuse the workflow for later providers |
| Require the whole TS migration | Reject unnecessary dependency; migrate only the touched cohesive rule |

## 7. Safety, privacy, and compatibility

Default off. Disabled profile must not change existing Chat, Todo, capture,
scheduler or report behavior, initiate model calls, or create notifications.
Activation previews exact sources, history window, owner identity, allowed local
actions, budget, cadence, retention and destination. Credentials stay in the
existing provider credential boundary; private payloads never enter public
status, logs, fixtures, reports, PRs or repository state.

Owner-private brief content needs its own permission-checked profile; never feed
it into the existing public-safe periodic-report preset or shared Lark projection.
Revocation stops new reads and dependent effects immediately and blocks queued
replay at dispatch. Retention/deletion is explicit and separate from disconnect.
External text is untrusted data, including instructions claiming to be the owner.
Sending, assigning another person, or changing external tasks requires a matching
external-effect grant and provider readback; these actions are outside v0.

Schema additions preserve older stored fields. Older readers must ignore optional
metadata or reject unsupported operation versions before writing. If an existing
reader cannot safely preserve the payload, feature activation waits for its
upgrade; no dual writer or silent metadata loss is acceptable.

## 8. Migration and rollback

No automatic import of historical conversations or existing Todos. Activation
starts at an explicit cursor/time window; any catch-up previews bounded scope.
Preflight checks provider access, owner identity, private storage, executor and
version compatibility. The first profile requires no global storage cutover.

Disable stops future capture/interpretation and recurring triggers. Drain or
cancel in-flight work through the existing scoped owner, retaining receipts to
prevent duplicate effects. Keep created Todos and label their disconnected
sources. User-requested deletion removes private content and memory indexes;
retain only authorized minimal content-free dedupe/audit facts. Downgrades disable
the profile before an incompatible reader starts; export private state through
an owner-only path if restoration requires migration.

## 9. Validation and acceptance

| Claim | Evidence to implement/run | Required result | Boundary |
| --- | --- | --- | --- |
| Understand responsibility | Frozen labeled corpus: own/other person's promise, quote, negation, ambiguity, timezone, edit, deletion | Record precision and recall separately; no silent ambiguous promotion | Model evaluation, not lifecycle proof |
| Stable follow-up | Independent expected sequence: promise, changed date, duplicate, owner correction, draft, completion | One Todo; correct revisions; preparation cannot complete commitment | Typed integration using real disposable store |
| Recover effects | Crash before/after apply and ACK, response loss, replay, concurrent edit | No duplicate mutation, lost pending item or cursor skip | Isolated runtime fault injection |
| Preserve boundaries | Revoked grant, another owner, injected source instruction, disabled feature | No unauthorized read/write or off-state side effect | Every changed shared surface |
| Complete desktop journey | Released package, real authorized Lark source and real executor | Connect, capture, review, prepare, correct, reopen, inspect same returned artifact, disconnect | Browser fixtures alone cannot pass |
| JS/TS-only execution | Run the packaged workflow with Python unavailable, covering capture, mutation, preparation and return | No Python process or bridge dependency; canonical state parity holds | Whole selected workflow, not only unit tests |
| Honest operation | Sleep/network outage, expired credential, exhausted budget | Visible stale/blocked state, bounded recovery, no false always-on claim | Local-host profile only |

For model qualification, use a frozen synthetic or consented corpus of at least
100 messages including at least 30 actionable commitments; score against labels
made independently of model output. Proposal precision = correct proposed items /
all proposed items; recall = correctly found commitments / all labeled commitments.
Initial release targets are >=95% precision and >=90% recall, with zero wrong-owner
automatic mutations and zero unauthorized effects. These are proposed acceptance
thresholds, not measured performance. Preserve failed and ambiguous cases.

Then run a seven-day owner-authorized pilot. Sample all proposed/accepted items
and independently review source messages for missed commitments. Report denominator,
source window, duplicate rate, deadline accuracy, verified preparation outcomes,
model cost and daily review/correction minutes against manual baseline on comparable
work. Small samples remain exploratory. Promotion requires correctness gates and
lower total review/correction effort; publish aggregate redacted results only.

## 10. Operational contract

Reuse source health, pending counts, freshness, existing budget admission and
scoped stop. Distinguish captured, proposed, applied, preparing, returned and
verified facts through their owners; no universal active boolean.

Bound history window, page size, daily model budget, retry count and retained
proposals. On overflow preserve cursor and show backlog; do not silently discard.
Use event-driven work plus existing bounded catch-up. The existing scheduler owns
one explicit daily brief schedule with timezone/quiet hours; schedule-specific
work belongs in canonical state, not a custom generic heartbeat prompt. During
sleep the local profile pauses; on wake it reconciles backlog and emits at most
one catch-up brief per settled reporting window. No cloud availability claim.

Desktop exposes connection coverage, latest successful sync, unresolved items,
source links, artifact versions, pause/disconnect and actionable repairs. Routine
progress stays quiet; urgent exceptions follow the owner's explicit notification
policy. CLI must read the same state; Lark ingestion does not authorize Lark brief
delivery. No second settings owner or new mandatory approval layer.

## 11. Normative delivery plan

| Slice | Entry | Complete exit | Rollback |
| --- | --- | --- | --- |
| M1: read and review | Approved source/owner profile; typed proposal and canonical mutation seam | Packaged connect -> source -> proposals -> reviewed Todo -> correction -> desktop brief, with replay/revoke tests | Disable profile, retain Todos/receipts |
| M2: prepare and return | M1; qualified existing executor and original-route return | Linked preparation artifact, real source change, restart and verified outcome in same conversation | Stop preparation scope; retain reviewed artifacts |
| M3: recurring pilot | M2 correctness; explicit cadence/budget and retention | Seven-day evidence, daily brief, outage recovery and measured attention cost | Disable schedule/profile independently |

M1 includes frontend, Lark adapter and CLI readback; backend-only delivery is
partial. M2 reuses ongoing App continuity work instead of implementing another
return watcher. The overall roadmap owns S1/S5/S6/S8/S9; this RFC introduces no
parallel milestone program and closes none of its existing acceptance gates.
Before implementation, resolve the active canonical Todo and related PRs; reuse
an existing successor. This proposal does not assert an operational task claim.

Bounded refactor pass: characterize capture-to-effect settlement and proposal-to-
Todo mapping before extraction. Share only proven domain-neutral rules in their
typed owners; retain Lark-specific transport. No speculative provider framework.

## 12. Open decisions

1. Product/workspace owner: choose the first supported Lark identity profile and
   installation platform before M1. Prefer verified bot-visible selected groups;
   require real access/coverage evidence before adding user-account access.
2. Privacy/workspace owner: choose raw-source retention and deletion interval at
   activation before M1. Recommend short configurable retention; measure recovery
   needs and display the chosen policy. No indefinite retention by default.
3. Product/evaluation owner: approve frozen labels, model profile and release
   thresholds before M1 qualification. Recommendations in section 9 remain
   unmeasured; do not change them after observing a failing run without a recorded
   decision and a new held-out evaluation.

## Appendix A: Baseline evidence

At `f49b4a0`, the source owners linked in section 4 exist. This review found no
complete personal-commitment follow-through workflow. No feature code, account
connection, model evaluation, installed journey or pilot is delivered by this RFC.
Related App recovery work remains independently owned. Repository and open-PR
inspection are design evidence; an operational Todo registry was not available
in this clean checkout, so task allocation must be checked before implementation.

## Appendix B: Execution ledger

Implementation evidence and remaining acceptance live in the [execution ledger](ledger/personal-follow-through-v0/).
