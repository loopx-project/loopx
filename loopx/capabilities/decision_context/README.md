# Decision Context

[中文](README.zh-CN.md) | [Architecture contract](../../../docs/reference/protocols/decision-context-architecture-v0.md)

Status: experimental, built in, default off, goal scoped.

Decision Context helps a long-running LoopX agent rebuild **what is currently
true for one decision** before it acts. It combines revisioned authority
sources, bounded provider recall, exact reads, freshness checks, and conflict
handling into an auditable evidence packet. The agent may then produce a
proposal, while LoopX Core remains the only lifecycle and action authority.

It is useful when a goal spans days or weeks and the answer cannot safely come
from the current prompt or model memory alone.

## The Problem It Solves

A long-running agent often has more context than it can keep in one session:

- project state and source documents change independently;
- previous judgments can become stale;
- semantic recall can find useful clues but cannot prove current truth;
- recommendations are easily mistaken for facts;
- a decision is hard to improve if its later outcome is not linked back to the
  evidence that produced it.

Decision Context turns that loose context into a bounded decision cycle:

```mermaid
flowchart LR
    SOURCES["Authority sources<br/>documents · repositories · messages · state"]
    RECALL["Advisory recall<br/>OpenViking · local search · other providers"]
    READ["Bounded scan + exact read<br/>freshness · revision · conflicts"]
    EVIDENCE["Evidence packet<br/>accepted · rejected · stale · conflicting"]
    PROPOSAL["Decision proposal<br/>recommendation · alternatives · stop list"]
    REVIEW["Review settlement<br/>approve · reject · defer · no change"]
    CORE["LoopX lifecycle<br/>todo · user gate · event"]
    OUTCOME["Outcome receipt<br/>later observed result"]
    MEMORY["Reward Memory<br/>reviewed reusable experience"]

    SOURCES --> READ
    RECALL --> READ
    READ --> EVIDENCE
    EVIDENCE --> PROPOSAL
    PROPOSAL --> REVIEW
    REVIEW --> CORE
    CORE --> OUTCOME
    OUTCOME -. "verified outcome only" .-> MEMORY
```

## What It Owns

Decision Context owns the decision-quality layer:

1. **Incremental source profiles** declare which source classes matter, their
   freshness policy, scan mode, and evidence weight.
2. **Bounded scan and exact read** discover changes without copying raw source
   bodies into LoopX packets.
3. **Evidence rebase** promotes current facts and explicitly records stale,
   rejected, or conflicting claims.
4. **Decision proposals** keep recommendations, alternatives, next actions,
   and stop lists separate from evidence.
5. **Review receipts** record owner `approve`, `reject`, or `defer` through the
   existing user gate, or one explicit semantic `no_change` result without a
   gate.
6. **Cursor commit** advances private source cursors after the review settlement
   and lifecycle writeback have been validated. It does not wait for a future
   real-world outcome.
7. **Outcome receipts** later link an accepted decision to observed outcomes
   and invalidated assumptions.

## What It Does Not Own

Decision Context does not:

- replace LoopX Core todo, gate, quota, event, or authority semantics;
- turn provider recall into trusted truth;
- automatically persist chat bodies, tool output, credentials, or raw provider
  payloads;
- grant permission to execute a recommendation;
- automatically activate a Reward Memory candidate;
- require OpenViking or any other single provider.

If a provider is unavailable, the capability fails open to the remaining
authority sources and records provider health. It does not block the Core
lifecycle or silently advance source cursors.

The assembly also emits `decision_source_coverage_v0`. This public-safe receipt
summarizes scan status, exact-read completeness, and uncovered P0 sources by
priority. Incomplete P0 coverage does not block safe LoopX lifecycle work, but
the caller must label the conclusion as partial or exact-read the missing
authority through another path. Fail-open must not masquerade as complete
context coverage.

## Four Auditable Outputs

| Output | Answers | Typical contents |
|---|---|---|
| `decision_evidence_packet_v0` | What should the decision trust now? | Changed facts, accepted recall, stale/rejected claims, conflicts, revisions, provider health |
| `decision_proposal_v0` | What should happen next? | Objective scores, recommendation, alternatives, actions, stop list |
| `decision_review_receipt_v0` | What did the owner decide about the proposal? | Approve/reject/defer gate evidence, or an explicit quiet no-change settlement |
| `decision_outcome_receipt_v0` | What happened after the decision? | Accepted decision, transitions, outcomes, invalidated assumptions, review time |

The evidence packet is intended to be deterministic and auditable. The
proposal is explicitly advisory. The review receipt settles whether the source
material has been consumed; it is not proof of a future outcome. The outcome
receipt is append-only evidence; only a verified outcome may later become a
Reward Memory candidate, and that candidate still follows Reward Memory review
and activation.

## Typical Uses

- Rebase a multi-week engineering or product decision against changed
  repositories, documents, and owner communication.
- Reject a previously recalled claim after an exact read shows that it is
  stale.
- Stop a planned action when the current source revision invalidates its
  premise.
- Keep a recurring decision review quiet when no material source changed.
- Provide revision-bound evidence for another capability, such as Material
  Lifecycle reranking.

This capability is not needed for a one-off answer with one stable source.

## Available Surfaces

Inspect the provider-neutral architecture:

```bash
loopx decision-context architecture --format json
```

Prove the default-off route or inspect an explicitly enabled private profile:

```bash
loopx decision-context inspect-profile \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --format json
```

Project an enabled profile without accessing providers:

```bash
loopx decision-context source-manifest \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --profile <ignored-private-profile.json> \
  --format json
```

Recall one task or other provider scope without modifying the profile or
entering the evidence-settlement workflow:

```bash
loopx decision-context recall-context \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --profile <ignored-private-profile.json> \
  --context-scope-ref 'host-session:codex:<thread-id>' \
  --query '<specific private provider query>' \
  --query-summary '<public-safe intent summary>' \
  --format json
```

The profile still gates the Goal, Agent, and provider, but the one-off scope is
not persisted. The command does not scan authority sources, read or write
cursors, create pending settlement, or grant execution authority. Its top-level
output is explicitly `local_private_transient` because it contains the recalled
text for the current agent. The nested retrieval receipt is public-safe and
retains only the query summary, provider-safe summaries, scores, and hashed
references. Each recalled item is marked `untrusted_advisory` and must never be
treated as an instruction.

Keeping this profile enabled does not make Obelisk a required LoopX
dependency. If the selected extension is not installed, is disabled, or no
longer has a current doctor proof, recall exits normally with
`status=unavailable`, a typed `provider_readiness` receipt, and no provider
scan or write. Do not remove or rewrite the profile just to recover the
provider: install it, run
`loopx extension enable <extension-id> --execute --format json`, or run
`loopx extension doctor <extension-id> --execute --format json` according to
`provider_readiness.next_action`. The next recall re-resolves lifecycle state
and resumes automatically when the provider is ready.

Run bounded scans and exact reads without committing private cursors:

```bash
loopx decision-context prepare-evidence \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --profile <ignored-private-profile.json> \
  --decision-id <stable-decision-id> \
  --format json
```

An optional extension can implement the existing advisory `ContextProvider`
port. For example, `packages/loopx-obelisk` accepts a normalized
`host-session:codex:<thread-id>` scope and retrieves bounded historical task
messages through Obelisk's public CLI. The profile selects it with
`context_provider.provider=extension`; `config.extension_id` may name the exact
provider, otherwise exactly one enabled, doctor-ready implementation must be
available. Provider failure remains fail-open, and raw recalled text never
enters the public packet.

`prepare-evidence` is deliberately read only. A domain adapter can provide a
strict semantic rebase and persist an unapplied private checkpoint:

```bash
loopx decision-context prepare-review \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --profile <ignored-private-profile.json> \
  --decision-id <stable-decision-id> \
  --rebase-json <ignored-private-rebase.json> \
  --pending-settlement <ignored-private-pending.json> \
  --execute \
  --format json
```

After a proposal is decided through an existing `user_gate`, settle it with
the exact gate event. The gate must use
`decision_scope=direction:action:<proposal-packet-ref>`:

```bash
loopx decision-context settle-review \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --profile <ignored-private-profile.json> \
  --cursor-state <ignored-private-cursors.json> \
  --pending-settlement <ignored-private-pending.json> \
  --event-log <ignored-private-rollout-events.jsonl> \
  --proposal-json <public-safe-proposal.json> \
  --source-event-id <exact-user-gate-event-id> \
  --actor-ref <owner-ref> \
  --reason-code <reason-code> \
  --summary <compact-public-safe-summary> \
  --execute \
  --format json
```

For an explicit semantic `no_change`, omit `--proposal-json` and
`--source-event-id`; no user gate is created. Preview by omitting `--execute`.
These commands write only the caller-selected private pending/cursor state and
the existing local rollout event log. They grant no trading, external action,
or other irreversible authority. Removing the private profile disables the
route; removing the pending checkpoint abandons an unsettled review without
changing active cursors.

### Opt-in Source-Reference Capture

Automatic capture is **default off**. Earlier profiles rejected every
`automatic_capture=true`; it now means explicitly allowlisted **reference
capture**, not automatic semantic review, raw-content archiving, or memory sync.
Add these fields to an existing private profile's `automation` object:

```json
{
  "automatic_capture": true,
  "fail_open": true,
  "source_ids": ["source:authority:baseline"],
  "interval_seconds": 900,
  "max_pending_batches": 1000
}
```

Every listed source must already be enabled, incremental, and exact-readable.
On-demand sources are never enrolled implicitly. The same goal/agent activation
checks apply. Preview does not call providers or create the spool:

```bash
loopx decision-context capture --goal-id <goal-id> --agent-id <agent-id> \
  --profile <private-profile.json> --spool <private-capture.sqlite> \
  --cursor-state <reviewed-cursors.json> --format json
```

Add `--execute` for one tick. Use `capture-status` with the same arguments
and without `--execute` for readback. Configure a host scheduler to invoke the
tick; the capability enforces `interval_seconds`, while the host owns process
startup, an outer process timeout, and stop/uninstall. No model heartbeat is
created. Private integrations use `capture_profile_sources` from
`loopx.capabilities.decision_context.capture` with existing
`source_provider_overrides`; generic queue/configuration rules remain here.

The mode-0600 SQLite spool binds to one goal/agent and records bounded public-safe
scan receipts plus **private replay cursors**. It contains no source bodies.
Ticks are serialized; batch insertion and capture cursor advancement commit
together. Failed scans keep their cursor, and capacity exhaustion reports
`backpressure` without dropping pending batches. A changed source binding reports
`binding_changed`, requiring an explicit rebase or a separately scoped new spool.
Do not store the spool or its journal in a public repository.

Consume each source's `next_batch_id` through a fresh bounded scan and exact read:

```bash
loopx decision-context prepare-captured --goal-id <goal-id> --agent-id <agent-id> \
  --profile <private-profile.json> --spool <private-capture.sqlite> \
  --cursor-state <reviewed-cursors.json> --batch-id <batch-id> \
  --decision-id <decision-id> --rebase-json <private-rebase.json> \
  --pending-settlement <private-pending.json> --execute --format json
```

A domain host can instead use `assemble_captured_decision_evidence` with a
`rebase` callback to inspect transient exact content. Preparing evidence does
not acknowledge a batch. Use the existing `settle-review` path above; a later
capture tick retires only the oldest batch whose before/after cursors match a
newly observed transition in the **settlement-owned reviewed cursor file**.
Unchanged reviewed cursors never acknowledge later batches, including A→B→A
source changes. Capture-owned observations are not review authority. Never
substitute capture cursors for that file or manually manufacture reviewed cursors.

If several settlements occur between ticks, their intermediate transitions may
be unobservable; ambiguous batches are retained, not inferred to be reviewed.
An older spool without review observations is baselined without retiring rows.
For either hold, reconcile against actual review evidence explicitly, or use the
guarded recovery below. This conservative protocol does not promise automatic
queue drainage after skipped review transitions.

This is a change-reference spool, **not a lossless source archive**. First-scan
history, pagination, late edits, deletion visibility and deadlines remain provider
contracts. Providers must support deterministic bounded replay; an unavailable
captured revision raises a hold rather than reviewing substituted content. Use
an explicit current-source rebase and ordinary reviewed settlement when historical
replay is impossible. Capture health is not proof of complete decision coverage.

To stop collection, set `automatic_capture=false` and unload the host scheduler.
Existing reviewable batches remain private and can still be prepared. To roll
back to an older release, also remove the three new automation fields; retain
the spool as a private checkpoint rather than deleting unreviewed work.

#### Recover an unreplayable source without discarding history

Recovery belongs to this capability's existing private SQLite spool, not Core
Goal lifecycle. The local host/CLI is the operator surface; no provider, model,
remote write permission, scheduler or dashboard setting is added. A trusted
host may call `diagnose_capture_source` / `recover_capture_source` from
`loopx.capabilities.decision_context.capture_recovery` with its existing
`source_provider_overrides`.

1. Run `capture-diagnose` with the same `--goal-id`, `--agent-id`, `--profile`,
   `--spool`, `--cursor-state` arguments and an explicit `--source-id`.
   Metadata-only diagnostics never contact providers. Add `--probe` to perform
   one bounded transient replay/exact-read check, without semantic review or
   pending settlement. Results distinguish `replay_not_checked`, `replayable`,
   `revision_unavailable`, `binding_changed`, `cursor_diverged`,
   `probe_unavailable`, `state_changed`, `empty` and `acquisition_held`.
2. Preview `capture-recovery --action hold` with that same scope. It shows
   affected counts and an opaque `preview_token`. Apply only with explicit
   operator authorization, `--execute --expected-token <preview_token>`.
   All pending references for that source move atomically to **held, unresolved
   history**, byte-for-byte, and acquisition for that source pauses. They are
   not marked reviewed. Other sources can use the released active capacity.
3. When ready to read current material, preview and apply `--action restart`
   with a fresh token. This rebinds acquisition to the current profile and the
   **unchanged settlement-owned reviewed cursor**, clears the source's interval
   wait, and removes its acquisition hold. The next ordinary capture produces
   a fresh batch for `prepare-captured` and normal `settle-review`. Older held
   references remain unresolved, even after the new batch is reviewed.
4. `--action rollback --recovery-id <applied-recovery-id>` also requires preview
   and explicit apply. It restores that operation's prior source state only if
   the source scope and profile/reviewed files still match its receipt and the
   active capacity permits restoration. After new capture/review, it fails
   closed rather than overwriting progress. The applied/rolled-back receipts
   remain in the private spool; copy/export the spool securely for inspection.

All apply tokens bind action, source, profile/binding, spool identity and full
queue frontier, reviewed-file digest/identity and audit state. A concurrent
capture or review, duplicate apply, rebind or file ABA requires a fresh preview.
SQLite serializes capture/recovery; apply uses the settlement cursor lock.
Arbitrary manual edits are unsupported. Recovery never writes reviewed cursors,
so it does not invalidate or supersede a legitimate separate review settlement.
It is an acquisition restart, **not a claim to have created a new review epoch**.

The existing `max_pending_batches=N` still bounds active batches. Held history
has a separate cap of N; new hold/restart operations stop after 2N audit records
(at most one rollback per applicable receipt). No automatic eviction, compaction
or repeated capacity increase is performed. At that bound, preserve/export the
private spool and make an explicit retention decision; increasing the limit is
not evidence consumption. A hold is explicit, not an automatic fairness policy.
It can isolate a noisy source, but exhaustion can recur if other sources are
not reviewed. Backpressured sources now retry on the next tick when capacity is
available instead of waiting an additional scan interval.

`capture-status` separates active `pending_batch_count`, unresolved
`held_batch_count`, per-source `acquisition_held` and
`semantic_review_completion=not_inferred_from_capture`. `last_checked_at` is
the last attempt, not necessarily a successful scan. No status-only call proves
historical replay or complete decision coverage. Disable capture using the existing profile
switch; stop the scheduler before downgrading, since older runtimes do not honor
recovery holds. Retain the spool/receipts rather than treating downgrade as rollback.

#### Source freshness contract

An enabled profile, a healthy `loopx doctor` or a settled projection never
implies fresh sources. Every `prepare-evidence` / `prepare-review` assembly and
every `capture` / `capture-status` result carries `source_freshness`
(`decision_source_freshness_v0`): one row per enabled source with
`last_read_at` (last *successful* read), `staleness_seconds`, the source
`freshness_seconds` window, `status` (`fresh`, `stale`, `never_read`,
`not_scanned`), `failure_streak` and `alert_reasons`. Enabled sources outside
the current scan (for example on-demand sources) appear as `not_scanned`
instead of disappearing. Markdown output marks every alerted row with 🔴.
Consumers must disclose alerted sources before presenting a conclusion as current.

A failed provider attempt updates `last_checked_at` and increments
`failure_streak`, but never advances `last_read_at`. Existing spools migrate in
place; a legacy row whose last attempt succeeded uses that attempt as its last read.

`loopx decision-context capture --execute` records a local host health file
under `<runtime-root>/decision-context/capture-hosts/`. Private hosts calling
`capture_profile_sources` should pass `health_runtime_root` for the same effect.
`loopx doctor` reports the optional `decision_context_capture_hosts_healthy`
check without opening private spools. It alerts when a registered host has not
ticked within `max(2 × interval, interval + 600s)` (for example a scheduler still
pointing at a deleted checkout), when the last tick failed, when the spool is
gone, or when a recorded source is stale or failing. Remove the record of a
deliberately retired host.

## Relationship To Other Capabilities

| Capability | Primary question | Relationship |
|---|---|---|
| LoopX Core | What work is authorized and what is its lifecycle state? | Decision Context consumes Core truth and proposes through existing lifecycle contracts. |
| Reward Memory | What verified experience should be reusable later? | Decision Context may consume reviewed memory; verified outcomes may create review candidates. |
| Material Lifecycle | Which materials should be active, archived, rebuilt, or reranked? | Decision Context can supply revision-bound evidence; Material Lifecycle owns the material transition. |
| Context provider | What prior context may be relevant? | Advisory recall only; every promoted claim still needs authority and exact-read checks. |

## Maturity And Adoption Boundary

The public capability currently ships its packet contracts, default-off
activation profile, provider-neutral source contract, bounded evidence
assembly, public-safe projections, owner-gated or quiet review settlement,
private cursor commit, opt-in source-reference capture, and later validated
outcome feedback.

It is still marked **experimental**. A production integration must provide its
own private source adapters, profile, authority policy, proposal logic, and
validated lifecycle writeback. Public packets must never contain private
locators, source bodies, raw chats, provider payloads, or credentials.

For implementation details and invariants, read the
[Decision Context architecture contract](../../../docs/reference/protocols/decision-context-architecture-v0.md).

## Validate

```bash
python3 examples/decision-context-contract-smoke.py
python3 examples/decision-material-walkthrough-smoke.py
python3 -m pytest -q tests/test_decision_context_material.py
python3 -m pytest -q tests/capabilities/test_decision_context_capture.py
```

The contract smoke covers Decision Context packet and architecture readback.
The walkthrough smoke feeds revision-bound evidence into a Material Lifecycle
rerank preview, keeps stale/conflicting evidence visible, omits source bodies
and private locators, and leaves apply/cursor commits as separate owner-gated
actions.
