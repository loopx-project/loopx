# Goal Instance Identity and Orphan Recovery (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Identity/recovery proposal; codec prerequisite shipped in #4917
- **Authors / owners:** LoopX contributors
- **Created:** 2026-09-23
- **Last normative revision:** 2026-09-23
- **Implementation baseline:** `23edcb19c70394480e3a9ebe8a960f5a320c5342`
- **Related contracts:** [Issue #4801](https://github.com/loopx-project/loopx/issues/4801), [orphan fence slice #4808](https://github.com/loopx-project/loopx/pull/4808)
- **Language mirror:** [Chinese semantic mirror](goal-instance-identity-and-orphan-recovery-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1-10 are the durable design and acceptance contract. Section 11 is
the normative delivery plan. Section 12 contains unresolved decisions;
recommendations there are not approval. Appendices are non-normative evidence
and decision history.

The English and Chinese documents are semantic mirrors. Any normative change
must update both in the same pull request.

---

## 1. Decision summary

This RFC makes five linked decisions:

1. `goal_id` remains the human-readable alias. A project-registry-owned,
   immutable `goal_instance_id` identifies one Goal lifetime.
2. Every durable host, session, channel, automation, Turn, and quota binding
   carries the exact pair. The source project registry, not a global
   projection or a binding store, authorizes execution.
3. Instance identity needs an incompatible activation format plus commit-time
   fencing. The shipped M0 codec is not an identity-enforcement protocol;
   rejecting one old decoder alone does not exclude every old effect path.
4. Orphan recovery is one preview-first, journaled lifecycle operation with
   explicit per-candidate `adopt`, `migrate`, `archive`, or `delete`
   dispositions. It never guesses or merges.
5. Activation is explicit and registry-wide. Within an activated registry,
   unstamped bindings are read-only. Non-activated projects retain their legacy
   behavior and have no ABA guarantee; a fleet-wide default change is separate.

The existing state-file locations, host-specific runtime directory layouts,
provider revision domains, leases, and global registry role do not become new
Goal authorities. This RFC does not approve automatic cleanup of every
external provider, automatic candidate selection, or direct mutation of
orphan state by ordinary bootstrap.

## 2. Problem and motivation

Today one `goal_id` serves as both a display name and a durable identity. A
Goal can disappear from the project registry while active-state files,
sessions, host bindings, Goal Channels, and heartbeat automations survive.
Creating a new Goal with the same `goal_id` can make those old records appear
current. This is an ABA failure:

1. Goal A named `release` creates durable attachments.
2. Goal A is removed, but some attachments remain.
3. Goal B is created with the same name.
4. An attachment that only stores `release` cannot distinguish A from B.

The fence shipped by #4808 correctly blocks guided bootstrap when a registry
entry is absent but a project state candidate remains. It does not distinguish
Goal lifetimes, resolve the orphan, or invalidate attachments after
delete-and-recreate.

No individual host or session store can solve this locally. If a host invents
identity, it becomes a second Goal authority. If deletion synchronously knows
every provider, it becomes a cross-owner distributed cleanup transaction.
LoopX needs one source-owned lifetime identity, with admission and commit fenced
against retirement. An identity match is necessary, not an authorization grant.

### Invariants

1. The source project registry is the only authority that creates or resolves
   a Goal instance.
2. A new Goal after terminal removal receives a fresh random instance ID,
   including when its `goal_id`, path, objective, or configuration is reused.
3. Updating configuration, reconnecting a provider, renewing a lease, opening
   a Turn, or rewriting a binding never rotates the instance ID.
4. A stale, missing, malformed, or legacy binding cannot authorize execution
   for an instance-aware Goal.
5. A stale global projection may route a lookup but cannot authorize a write.
6. Before source safety is complete, a resolution journal blocks ordinary
   activation/mutation of that Goal, including after registry publication. Exact
   recovery remains available; completed-source projection retry is separate.
7. Resolution leaves exactly one selected writable state authority or leaves
   the Goal absent with no writable candidate.
8. Destructive resolution requires a verified targeted backup, exact plan
   digest, explicit Goal confirmation, and post-write readback.
9. Crash retry reuses prepared identity and owner idempotency keys; every plan,
   including archive/delete-only plans, journals before its first mutation.
10. A released old writer cannot silently open or rewrite an instance-aware
    project registry.

## 3. Scope and non-goals

### In scope

- Project-registry Goal lifetime identity and exact binding comparison.
- A registry-wide compatibility boundary for old readers and writers.
- Instance propagation through first-party durable binding owners.
- Final checks at activation, resume, delivery, state mutation, and quota
  authorization.
- Preview, backup, apply, resume, and rollback for project-local orphan state.
- Diagnostics for orphan state, legacy identity, stale bindings, stale global
  projections, incomplete journals, and manual cleanup.
- A staged migration that separates observation from enforcement.

### Non-goals

- Changing the human-facing `goal_id` or placing instance IDs in state paths.
- Migrating low-level Codex, Pi, OpenCode, or other host runtime directories.
- Merging multiple active-state candidates.
- Treating timestamps, paths, content equality, global projections, or host
  records as identity evidence.
- Making physical deletion from an external provider the safety boundary.
- Introducing a public generic binding-provider framework.
- Changing Claim, Lease, authority revision, Turn, or provider idempotency
  semantics beyond binding them to a Goal instance.
- Allowing an old binary to downgrade or edit a strict registry.

### Roadmap placement and contract cooperation

The [overall roadmap](loopx-overall-roadmap-v0.md) measures accepted outcomes,
human attention and recovery. This RFC contributes a bounded R5 lifetime fence
that R2/R3 can consume; it does not complete R1–R7 or promote a provider.

| Existing owner / contract | Contribution and boundary |
| --- | --- |
| [TS convergence](typescript-control-plane-migration-v0.md), R5 | Put match, admission, retirement and recovery transitions in the typed kernel; keep Python codec, filesystem and host adapters. Do not add a second Python policy engine or per-field RPCs. |
| [Shared authority](shared-goal-authority-state-provider-v0.md), D1–D3 / R6 | Bind the existing head, operation receipts and authorization projection to lifetime. Provider incarnation, revision, lease epoch and Goal instance remain different domains; no default cutover or new store. |
| [Shared alignment](shared-goal-alignment-and-governed-amendment-v0.md), R4 | Identity is not intent, permission or work revision. Preserve `intent_basis` source-facts semantics; amendments remain with their existing owner. |
| [Semantic handoff](capable-manager-semantic-handoff-v0.md), [collaboration](agent-im-openviking-collaboration-v0.md), R2/R3 | Bind request, adoption, result and original-conversation return to the instance. Late A results cannot settle B; transport success and registration do not prove acceptance. |
| [Single-owner daemon](single-owner-local-daemon-v0.md) | Reuse profile quiescence and actual executor drain; a PID, package version or stopped heartbeat is not proof that old writers cannot return. Daemon identity grants no Goal authority. |

[#4808](https://github.com/loopx-project/loopx/pull/4808) owns the shipped guided
fence; [#4912](https://github.com/loopx-project/loopx/pull/4912) proposes diagnosis;
[#4915](https://github.com/loopx-project/loopx/pull/4915) proposes host-neutral
paths. Reuse those owners without treating open PRs as shipped. Canonical Todos
own execution status; this document owns acceptance and dependency boundaries.

## 4. Current-system contract

At the named main baseline, [#4917](https://github.com/loopx-project/loopx/pull/4917)
has shipped `control_plane/projects/registry_codec.py`, format-preserving
transactions and a production I/O census. It reads legacy objects and strict
`loopx_project_registry_envelope_v1` arrays and accepts writer protocol
`goal_instance_v1`. It does **not** mint, compare or enforce Goal instances.
Therefore a codec-capable but identity-unaware binary is part of the old-writer
population; the original object-root-only argument is insufficient.

The project registry owns configuration and source identity; the global registry
is a host-local route projection. Canonical coordination already has typed TS
transaction owners, independent provider incarnations and receipts.
`coordination/authority_source.ts` and `authority_source_capture.py` witness
registry facts, explicitly without a transaction spanning registry and provider.
Their digest recheck must not be advertised as retirement serialization.

`orphaned_goal_state.py` discovers current/legacy candidates and fences guided
bootstrap. `state_backup.py` owns backup/verification. Bootstrap updates, Goal
deletion, sessions, channels and quota remain distinct callers. Their current
alias/revision bindings do not prove lifetime continuity. No instance activation
or orphan mutation command described below has shipped at this baseline.

## 5. Proposed architecture

### 5.1 Ownership and authority

Placement: existing Goal/project lifecycle in the built-in control plane; no
new public capability id, provider id or extension distribution. File, SQLite
and PostgreSQL keep their existing provider contracts and qualification gates.

The source authority alone allocates and publishes `goal_instance_id`. A prepared
resolution may reserve one ID durably; it has no execution authority until the
lifecycle commit. The global registry only copies it. Pure match and lifecycle
transitions belong in the nearest typed TS Goal boundary, consuming source facts
through one coarse transaction. Python adapts CLI, codec, filesystem, backup and
host effects, and executes typed plans without duplicating transition rules.

The project registry remains the local source of identity. For a future R6
service profile, its versioned authorization projection and instance are admitted
by the authenticated authority service. Remote workers do not open a laptop's
path, allocate from a local clone or use a global projection as authority. Until
that path is qualified, instance enforcement is explicitly local-profile only.

No binding, path, timestamp or content hash may infer identity. Existing grant,
claim, lease, quota, stop and intent checks still apply after an exact match.

### 5.2 Goal identity model

`goal_instance_id` is opaque, immutable, public-safe, and not a credential. Its
canonical form is `ginst_` followed by 32 lowercase hexadecimal characters,
providing 128 random bits.

The typed contract distinguishes `GoalRef { goal_id, goal_instance_id }` from
`LegacyGoalRef { goal_id }`. `BindingMatch` is an exhaustive result with
`current`, `legacy_read_only`, `missing_goal_instance_id`,
`goal_instance_mismatch`, `goal_not_registered`, `resolution_in_progress`, and
`invalid` variants. These are kernel decisions; generated/decoded Python
representations are wire adapters, not a second matching implementation.

An instance-aware Goal record contains:

```json
{
  "id": "release-2026",
  "goal_instance_id": "ginst_6ff38d6d143d4b72a6ff894b95f067c1",
  "status": "active",
  "repo": "/project",
  "state_file": ".codex/goals/release-2026/ACTIVE_GOAL_STATE.md"
}
```

The absolute path above is illustrative, not public evidence. Stored paths
continue to follow existing privacy and portability rules.

Every durable attachment stores:

```json
{
  "goal_id": "release-2026",
  "goal_instance_id": "ginst_6ff38d6d143d4b72a6ff894b95f067c1"
}
```

The matching matrix is exhaustive:

| Registry state | Binding state | Read-only inspection | Execution or mutation |
| --- | --- | ---: | ---: |
| Exact instance | Exact same instance | allow | allow under commit fence and existing grants |
| Exact instance | Missing instance | allow with finding | reject |
| Exact instance | Different instance | allow with finding | reject |
| Legacy Goal | Legacy binding | allow with finding | reject in activated registry |
| Legacy Goal | Instance binding | allow with finding | reject |
| Goal absent | Any binding | allow as historical | reject |
| Resolution source safety incomplete | Any binding | allow with finding | reject ordinary execution; exact recovery only |
| Invalid record | Any | fail closed | reject |

Observation and non-activated projects retain legacy execution without an ABA
guarantee. An activated project has no `legacy_compatible` execution branch.
The exact-match row also requires active lifecycle state and existing grants.

### 5.3 Identity independence

The following revision domains remain independent:

| Event or identity | Rotates `goal_instance_id`? | Reason |
| --- | ---: | --- |
| Fresh creation after terminal retirement | yes | Starts a new Goal lifetime |
| Orphan `adopt` or `migrate` that creates a Goal | yes | Establishes a new authority |
| Forced bootstrap or configuration update | no | Changes configuration within one lifetime |
| Provider reconnect or provider revision | no | Changes an attachment or backend generation |
| Authority revision or migration receipt | no | Versions authority state, not Goal existence |
| Claim or Lease epoch | no | Coordinates work inside one lifetime |
| New Turn or session | no | Creates execution lineage within one lifetime |
| Binding revision | no | Versions one owner's attachment record |
| Global registry synchronization | no | Copies a source-owned identity |
| Rollback to orphaned bytes | never restores one | Restored bytes have no execution authority |

`--force` cannot replace an instance ID. Imports into a new project authority
mint a new identity rather than copying an identity owned by another registry.
Restoring an authority into a replacement deployment follows the existing
incarnation/restore contract; copying a registry backup must not resurrect stale
writers or create a second writable authority.

### 5.4 Activation format and mixed-writer gate

Identity activates per project registry, not per Goal. Reuse the M0 codec and
I/O census; do not build another registry loader. M0's v1 format/protocol remains
codec-only. Proposed activation uses an incompatible header schema
`loopx_project_registry_envelope_v2` and `minimum_writer_protocol: goal_instance_v2`
with the existing two-element `[header, payload]` shape and payload digest.
The final identifiers require the M2 compatibility matrix before release.

Changing only the minimum writer string is insufficient: M0 readers can decode
an unknown writer protocol, and a state mutation may never rewrite the registry.
The new schema must reject identity-unaware **read-to-effect** paths as well as
registry writes. Old global-object routes and already-loaded processes need
separate exclusion; no header can retrospectively constrain cached code.

```console
loopx activate-goal-instance-identity --project . --format json
loopx activate-goal-instance-identity --project . --execute \
  --plan-revision sha256:31ab... --confirm-registry-path .loopx/registry.json
```

Activation must:

1. Stop new admissions and durably quiesce all Goals in the registry through
   existing lifecycle/host owners; drain or explicitly reconcile admitted effects.
   Inventory direct CLI, attached/managed hosts, global routes, automation and
   provider writers. A disconnected or unaccounted writer blocks activation.
2. Verify a targeted backup, host protocol support and the exact preview digest.
   A capability advertisement is evidence to inspect, not write authority.
3. Prepare a durable activation journal and one ID per current Goal, then publish
   the incompatible envelope atomically under the registry transaction. The
   same IDs survive retry; no ID is minted in ordinary M0/M1 execution.
4. Bind owner state through explicit reviewed migration/reconnection. Missing
   stamps never inherit authority by alias. Reopen admission only after required
   commit fences and readbacks are installed; pending projection delivery alone
   must not authorize or duplicate effects.

Before publication, abort leaves the legacy object. After publication, only
forward repair is legal; never unwrap for an old writer. Fresh projects may
opt into the complete profile. Installation or codec availability does not
activate it. Fleet-wide legacy shutdown is a separate release decision.

Test all compatibility classes: object-only packages, codec-only M0/M1 packages,
and enforcement packages; test direct source and global routes, warm processes,
state writes, resume, quota and delivery. Require unchanged business state and
receipts on rejection, not only unchanged registry bytes. Local drain plus
unsupported source decoding must be proved for the supported package range;
where an old route bypasses both, activation remains unavailable until an
existing owner can mechanically exclude it. Same-principal deliberate filesystem
tampering is outside this mixed-version safety model.

### 5.5 Binding owners and commit fencing

| Existing owner | Instance-bound state and decisive boundary |
| --- | --- |
| Registry / attached host / Chat | Session/thread and activation binding; route selection, resume and executor admission |
| Turn / scheduler / quota | Work selection, lineage, settlement, debit/void and replay identity |
| Todo / coordination / lease | Head/provider binding, work mutation, claim/renew/reclaim and receipts |
| Handoff / inbox / outbox | Request, receiver adoption, accepted artifact, result delivery and historical readback |
| Goal Channel / Lark / heartbeat | Connection, delayed inbound/outbound event and wake admission |
| Pi / OpenCode / other first-party hosts | Host action, execution binding and quota/state writeback |

Maintain one private exhaustive owner inventory with stable locator, revision,
content digest, observed typed reference and cleanup support. Sorted inventory
and owner revisions enter plans. Reuse the M0 I/O census for registry access;
binding/effect coverage is a distinct inventory, not proof from the writer count.

Resolve route → validate source/authorized projection → compare exact reference
and lifecycle → check existing permissions → admit the operation → commit under
the owning fence. A last-moment read alone leaves a check/use race:
A passes the check, deletion/recreation publishes B, then A writes the reused
path or spends quota. The contract requires an explicit linearization boundary:

- Locally, lifecycle retirement and each short protected commit share the same
  instance admission guard with documented lock order. Do not hold a registry
  lock across a model call, network request or user validation.
- In canonical transactions, propagate the original source witness and instance
  to the TS owner; check the current instance at the commit boundary under the
  lifecycle guard. Existing CAS and lease fences remain required. A witness
  hash or Goal ID alone is not this guard.
- Retirement closes admission first, drains admitted commits and reconciles
  pending external effects in their existing journals, then publishes retirement.
  Uncertain external effects prevent declaring retirement/reuse complete. An
  external provider without fencing is not retroactively cancellable.
- A shared service must serialize lifetime and mutation admission server-side,
  with tenant/actor checks and restore incarnation. A local file lock does not
  qualify R6; no cross-ledger atomicity is implied.

Scope operation/replay keys, canonical state selection and late results to the
instance even when physical paths retain the alias. Old receipts remain readable
as A's history, but cannot grant B a lease, satisfy B's acceptance or debit B.
Do not silently relabel historical receipts. Selected provider state must either
be newly initialized for B or explicitly imported through its reviewed lifecycle.
A stale writer that pauses after validation and resumes after recreation must
produce no B-side write, delivery, debit or ownership change.

### 5.6 Orphan resolution command

The command is a dry run unless `--execute` is present. Every discovered
candidate requires one explicit disposition:

```console
loopx resolve-orphaned-goal-state \
  --project . \
  --goal-id release-2026 \
  --disposition .codex/goals/release-2026/ACTIVE_GOAL_STATE.md=adopt \
  --disposition .claude/goals/release-2026/ACTIVE_GOAL_STATE.md=archive \
  --objective "Ship the release" \
  --domain engineering \
  --role owner \
  --format json
```

- `adopt` retains selected state content at its canonical location under a fresh
  Goal instance; it does not revive former claims, grants or execution bindings.
- `migrate` moves one selected candidate through the canonical state-path
  helper, under the same human Goal ID, then registers a fresh instance.
- `archive` moves a candidate outside every active Goal root.
- `delete` removes a candidate only after targeted backup verification.

At most one candidate may be `adopt` or `migrate`. All other candidates must be
`archive` or `delete`. An archive/delete-only plan leaves the Goal absent.
There is no implicit winner, content merge, arbitrary destination path, or
renamed target Goal. The first resolution profile is project-local legacy file
state. If a candidate routes to promoted canonical/provider state, fail closed
with its existing authority migration/import route; moving Markdown cannot adopt
a File/SQLite/PostgreSQL head. Any later provider support must preserve source
selection, incarnation, historical receipts and D1–D3 gates.

Preview validates before computing its digest:

- the project and registry resolve inside their declared authority boundary;
- each candidate is a readable regular file inside an allowed Goal root;
- no candidate or parent is a symlink;
- resolved paths do not escape, alias, or duplicate one another;
- the requested Goal is absent from the project registry;
- no other resolution exists for the Goal;
- every candidate tree digest and binding-owner revision is stable;
- the migration destination is selected by the canonical path helper;
- the Goal creation specification is complete when required.

Execution must bind the preview and human intent:

```console
loopx resolve-orphaned-goal-state \
  --project . \
  --goal-id release-2026 \
  --disposition .codex/goals/release-2026/ACTIVE_GOAL_STATE.md=adopt \
  --disposition .claude/goals/release-2026/ACTIVE_GOAL_STATE.md=archive \
  --objective "Ship the release" \
  --domain engineering \
  --role owner \
  --execute \
  --plan-revision sha256:82e3... \
  --confirm-goal-id release-2026
```

### 5.7 Resolution transaction and journal

Use a typed lifecycle with `prepared`, `applying`, `source_committed`,
`complete`, `rollback_prepared`, and `rolled_back` states. The journal lives under
`.loopx/lifecycle/orphaned-goal-state/<goal-id>/<plan-revision>.json`; it is
recovery metadata, not another registry. TS owns transitions and allowed effects;
existing Python/host adapters execute them and return observations.

1. Acquire lifecycle/registry/affected-owner guards in documented order. Exact
   completed replay returns its historical receipt; it does not reactivate a Goal.
2. Before preparation, rebuild and compare the full registry, candidate-tree,
   inventory, creation-specification and plan digests; mismatch has no effects.
3. Verify targeted backup, then durably prepare **every** plan before any move or
   deletion, including archive/delete-only plans. Reserve a new ID only if creating
   a Goal; retain candidate preimages, planned postimages and owner operation keys.
4. Apply each disposition under its guard. Persist intent before the effect and
   reconcile actual pre/post state after crash-before-ack. Neither state matching
   requires replaying a deletion; neither matching state permits guessing.
5. Publish the new Goal only once selected content is canonical and competitors
   are inactive. Resume a prepared plan from its recorded phase; do not demand
   the original preview's pre-mutation digests after successful partial work.
6. Obtain required local invalidation receipts, verify the source and safety
   fences, and commit the lifecycle. Complete registry-wide activation first when
   other legacy Goals exist. Other Goals are outside the resolution guard's scope.
7. Deliver the global projection and operator result through existing retry
   owners. Projection failure is typed `pending_sync`, not a second authority or
   permission to repeat the source commit.

Before source safety is established, the journal blocks ordinary activation and
mutation for this Goal, even if its record is published. Only an exact
resolution/resume operation may perform its declared recovery effects. After
source commit plus required invalidation, projection lag may remain visible
without holding unrelated Goals or source-authorized local work indefinitely.
Every event-driven wake is advisory; source checks still decide admission.

The receipt records plan/resolution/terminal-journal digests; candidate pre/post
digests; exact source Goal reference; backup manifest verification; each owner
observation, revision, operation and readback; local removal receipts; and typed
projection/manual-cleanup findings. Exclude raw private content. Recovery must
remain available while new lifecycle admissions are disabled.

### 5.8 Logical revocation and legacy cleanup

Exact instance mismatch plus the commit fence is the safety boundary; physical
external deletion is hygiene. Typed cleanup selectors distinguish:

- `instance`: exact retired `(goal_id, goal_instance_id)`;
- `legacy_observation`: exact owner locator + observed revision + content digest,
  admitted only while the lifecycle guard proves the Goal absent/inactive.

A legacy orphan has no retired instance to invent. If its owner supports exact
conditional removal, revalidate the observation under the owner guard and remove
only that record. If the revision changed, stop/replan. If the provider cannot
prove exact removal, retain the now-inert record with `manual_cleanup_required`.
Never use Goal-ID-only bulk cleanup or stamp a legacy binding to make it deletable.

Required local logical invalidation receipts gate source completion; optional
physical cleanup and external manual cleanup have separate retry state. A new
binding created by a different lifetime cannot match the legacy selector. Test
legacy and stamped orphans independently, including a revision race.

### 5.9 Rollback

Rollback is also preview-first and digest-bound:

```console
loopx rollback-orphaned-goal-resolution \
  --project . \
  --goal-id release-2026 \
  --resolution-id ores_... \
  --format json

loopx rollback-orphaned-goal-resolution \
  --project . \
  --goal-id release-2026 \
  --resolution-id ores_... \
  --execute \
  --plan-revision sha256:... \
  --confirm-goal-id release-2026
```

Rollback may restore verified bytes only to an orphaned, fenced state. It
never restores a retired Goal instance ID or binding authority. It is rejected
after any post-resolution Goal write, Todo mutation, quota spend, session or
channel creation, automation installation, or binding revision. Rollback itself
journals before its first mutation and uses the same retirement/commit guard,
so checking eligibility cannot race a new business write. A successful
rollback removes the newly created Goal through the lifecycle owner, restores
candidate bytes, verifies their digests, and makes diagnosis unhealthy with
`orphaned_goal_state`.

If rollback is no longer legal, recovery is a forward operation: finish the
journal, retire the new Goal through normal lifecycle, and create another
resolution plan.

### 5.10 Bounded refactor and delivery owners

| Existing boundary | Change and deletion requirement |
| --- | --- |
| TS Goal lifecycle + `coordination/authority_source.ts` | One typed reference/match/admission/recovery rule set; extend whole transactions, remove duplicate host decisions. The witness is evidence, not a cross-store lock. |
| `control_plane/projects/registry_codec.py` | Extend shipped codec/protocol checks and existing transaction; retain v1 compatibility without granting v2 enforcement. |
| `bootstrap.py` / Goal deletion | Use the lifecycle transaction; preserve identity on config updates and fence retirement/recreation. |
| `orphaned_goal_state.py` / `state_backup.py` | Keep discovery and backup effects; a thin resolution adapter drives the typed plan instead of a new Python state machine. |
| Existing binding / delivery owners | Persist exact refs and commit fences; retain owner-local journals and idempotency instead of a central provider cleanup framework. |
| Diagnosis / CLI / Chat / frontend / Lark | Render the same lifecycle result and recovery action, with audience filtering; no second detector or independent repair state. |

Characterize ordinary bootstrap/update/deletion and default-off paths before
refactoring. Ship a whole local retirement/recreation path with actual consumers;
do not add unused Goal abstractions, per-field RPCs or a new scheduler. Larger
cross-host and provider-import work stays with R6/D1–D3, not a prerequisite for
the local legacy-file recovery outcome.

## 6. Alternatives and design choices

### Generation counter

A counter under `goal_id` needs a permanent tombstone after deletion or a
second global allocator. Random source-registry identity avoids both and does
not require ordered IDs.

### Instance ID in directory paths

Instance-segment paths separate files but do not identify sessions, channels,
automations, or quota calls. They also force every human-facing path consumer
through a broad migration. Record-level identity closes the reported ABA
failure without that unrelated layout change.

### Delete every binding during Goal deletion

Cross-provider deletion is not atomic and would make Goal lifecycle know every
storage implementation. Exact matching makes stale records inert. Owner-local
cleanup then improves hygiene without becoming authority.

### Add a capability field to the current registry object

Released old loaders accept unknown object fields and schema values. They
could silently rewrite an instance-aware registry. This fails the mixed-writer
invariant.

### A new registry path with a redirect object

Released old binaries could ignore the redirect, continue using the old path,
and create split authority. Keeping one path with an incompatible root shape
causes a deterministic decode failure instead.

### Automatic candidate selection or merge

Path preference, modification time, and content equality do not prove
authority. Multiple candidates may represent divergent histories. The
operator must classify every candidate.

### One module for discovery and mutation

Combining the small read-side fence with backup, journaling, cleanup, rollback,
and registry publication would mix stable detection with a large transaction.
The sibling resolution owner preserves a deep plan/apply interface without
making the fence module a lifecycle service.

## 7. Safety, privacy, and compatibility

Identity and digests are public-safe metadata, not credentials. Backups,
candidate contents, session payloads, local absolute paths, provider handles,
and raw state remain private runtime data and must not enter public receipts,
logs, fixtures, or documentation.

The format gate and effect-owner fences jointly exclude supported mixed writers.
Activation requires source/global/warm-process negative tests for object-only,
codec-only and enforcement binaries. Test helpers use isolated synthetic state;
never probe a live Goal to demonstrate rejection.

Non-activated legacy projects retain existing behavior. Activated projects allow
legacy inspection, backup and migration previews but reject unstamped execution.
An observation finding cannot advertise ABA protection. Protocol names must
represent the full enforced contract, not merely a decoder's availability.

An activated registry cannot be downgraded. Unsupported source schema/protocol
must fail before a business effect, including effects that do not edit registry
bytes. Recovery uses a current compatible release and never unwraps the payload.

Within the activated profile, source unavailability, digest mismatch, malformed
envelope, missing instance, incomplete source safety or owner-inventory ambiguity
fails closed.
Global projection unavailability may delay visibility but cannot grant
execution.

## 8. Migration and rollback

Migration has two scopes:

1. **Fresh opted-in project.** Create the activation envelope and mint the first Goal in
   its committed registry transaction.
2. **Existing project.** Preview registry-wide activation, quiesce every Goal,
   back up the registry and local bindings, stamp every current Goal with a new
   instance, atomically write the strict envelope, and require explicit
   reconnection of all attachments.

No existing attachment is automatically blessed. There is no evidence that an
unstamped record belongs to the current lifetime rather than a prior same-name
Goal.

The activation cutover point is the verified incompatible-envelope write;
admission remains closed until required owner fences/readbacks complete:

- Before it, abort leaves the legacy object authoritative.
- After it, old binaries and unstamped bindings are unsupported and fail
  closed.
- If later steps fail, current code resumes the activation journal.
- Returning to legacy execution is not rollback.

Orphan archive/delete may run without identity activation because it creates no
Goal. Orphan adopt/migrate requires an already activated registry, a fresh activated
registry, or a journaled registry-wide activation admitted by the same plan.

Before resolution preparation, abort changes nothing. After preparation, retry
the same plan. The separate rollback command is legal only before any
post-resolution business write and restores orphaned, non-executable bytes.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| Same-name recreation is fenced | Delete A, preserve every attachment, create B with same alias, exercise all owners | Every old path rejects before effect or quota | Does not require physical provider deletion |
| Updates preserve lifetime | Force-update config, reconnect provider, renew lease, start Turn | Same instance ID in registry and new records | Does not preserve binding revisions |
| Unstamped execution is read-only inside activated scope | Exercise status, backup, migration preview, resume, delivery, write, spend | Reads succeed with finding; ordinary business effects reject; explicit migration remains available | Non-activated legacy behavior is unchanged |
| Old writer is mechanically denied | Object-only, codec-only and enforcement packages; source/global/warm-process paths | Rejection before business effects, state/receipts unchanged | Decoder rejection alone is insufficient |
| Strict codec is exhaustive | Static writer inventory plus Python/TS conformance | No direct project-registry write bypass | Test helpers may use isolated fixtures |
| Source authority beats global projection | Make global entry stale and invoke every executable route | Source mismatch rejects | Global routing availability is separate |
| Preview is pure | Preview 1, 2, and 4 candidates | No file, registry, binding, or journal write | Read-only filesystem metadata access allowed |
| Plan binds full state | Change candidate, owner revision, registry, or Goal spec after preview | Apply rejects before backup or mutation | None |
| Path validation fails closed | Symlink, escape, duplicate path, non-regular or unreadable candidate | Preview/apply rejects | Canonical helper owns legal destination |
| Crash retry is idempotent | Every plan type; crash before/after each effect and journal acknowledgement | One prepared ID when applicable, exact reconciliation, no duplicate effect | Archive/delete-only is journaled too |
| Published-but-unsafe stays fenced | Crash after publication before required invalidation/source readback | Activation, mutation and quota reject; exact recovery works | Completed source with only projection lag is separately pending-sync |
| Cleanup failure is safe | Stamped and legacy bindings; changed legacy revision; local invalidation failure | Exact cleanup only; inert external records; required local invalidation retries | No invented retired identity |
| Receipt is complete | Compare receipt to owner table and all readbacks | Every owner/candidate/registry/global/backup/fence row present | Raw private contents excluded |
| Backup precedes destruction | Inject failure before and after backup verification | No destruction before proof; verified restore works | Archive-only still receives targeted backup |
| Rollback is fenced | Roll back before and after a post-resolution write | Early rollback restores orphan fence; late rollback rejects | Never restores old authority |
| Diagnostics remain unhealthy | Create orphan, legacy, mismatch, stale global, and incomplete journal states | Typed findings and exact next action | Diagnosis performs no repair |
| Commit race is fenced | Pause A after match, retire/recreate B, resume A; race rollback with a write | No B-side write, debit, delivery or ownership change | Real local entrypoint; real isolated provider for affected canonical paths |
| History stays historical | Same alias and operation key across lifetimes; delayed accepted result | A receipt/result cannot authorize or settle B | Historical A readback stays available |
| Default-off parity | Same input with activation off/on against immutable pre-change baseline | Off preserves schema, guidance, state and effects; on rejects stale refs | No live project migration |
| Provider boundary is honest | Native authority candidate presented to file-only resolution | No file-only adoption; existing import route identified | No D1–D3 or R6 qualification claimed |
| Product recovery is complete | CLI and packaged frontend; qualified Lark route | Preview → confirmation → crash/resume → source and original-entry readback | Untested transports explicitly remain unqualified |

The implementation must also run existing bootstrap, registry, deletion,
session, host, Goal Channel, heartbeat, quota, backup, docs-governance, and
public-safety suites. Skipped owner rows are not acceptance.

## 10. Operational contract

The stable findings are:

- `orphaned_goal_state`
- `orphan_resolution_in_progress`
- `legacy_goal_identity`
- `goal_instance_id_missing`
- `goal_instance_mismatch`
- `goal_not_registered`
- `stale_global_goal_instance`
- `unsupported_registry_writer_protocol`
- `binding_owner_inventory_changed`
- `manual_cleanup_required`

Every rejection identifies the source registry, Goal alias, expected and
observed instance when public-safe, owner, retryability, and one lifecycle next
action. It does not expose raw state, credentials, local session content, or
provider secrets.

Status and diagnosis are read-only. They can inspect a legacy registry,
historical binding, strict envelope, and incomplete journal. They cannot stamp
identity, repair a global projection, choose a candidate, or complete cleanup.

CLI, packaged frontend and Lark consume the same preview/confirm/resume result.
Show selected/competing candidates, execution block, backup proof, pending sync
and the exact next action. A stale session should explain the retired lifetime
and offer an explicitly authorized new binding, never silently resume it.
Return completion to the initiating conversation. Reuse existing settings and
action editors; a CLI-first slice must name unqualified companion surfaces and
cannot claim the complete product journey. This RFC itself changes no UI.

The lifecycle retains one verified backup and one terminal receipt per
resolution according to the existing backup retention policy. An incomplete
journal is never garbage-collected automatically. Local cleanup retry is
idempotent. External manual cleanup is advisory because logical revocation
already prevents execution.

## 11. Normative delivery plan

| Milestone | Outcome and prerequisite | Decisive exit / rollback |
| --- | --- | --- |
| M0: shipped codec prerequisite | Reuse #4917 codec/transaction/I/O census; no lifetime enforcement | v1 read/write support is shipped, not proof of old-writer exclusion. Do not rebuild or remove the now-used codec. |
| M1: characterize owners and typed rules | Inventory binding/effect producers and consumers; characterize baseline; implement only rules used by the selected lifecycle | Default-off parity, typed negative matrix, source/global/warm-process compatibility census. No minting or automatic activation. |
| M2: opt-in local lifetime transaction | Incompatible activation + retirement/recreation guard + selected local owner enforcement as one usable slice | Real CLI local ABA/concurrency/crash/replay path; retain M2/M3 activation hold until every supported effect owner is fenced. Before cutover abort; afterward forward repair. |
| M3: supported-owner qualification | Complete host, quota, Todo/lease, handoff, channel and automation paths for the activated profile | Every inventory row qualified, including delayed results and unsupported binaries; no legacy fallback inside activated projects. Non-activated behavior unchanged. |
| M4: orphan recovery | Reuse fence/diagnosis/path/backup owners; journaled file-state resolution with exact legacy cleanup | Preview, destructive/retry/rollback negatives and original-entry readback. Archive/delete can ship earlier without identity creation; native-provider adoption stays blocked. |
| M5: migration and product acceptance | M2–M4 plus multi-Goal quiescence/reconnection; packaged frontend and qualified Lark | 2–3 workers, dependency artifact, interrupted A, recreated B and late A return; unrelated Goal progresses; B accepted independently. No duplicate protected effect. |

M0 is a prerequisite checkpoint, not the next unstarted task. The next slice
owns M1 compatibility/consumer characterization and the M2 local lifecycle seam;
do not ship a field-only milestone as the ABA outcome. M2 and M3 describe
implementation order, not permission to activate a partially fenced system.
R2/R3 consume the completed local slice; R6 service adoption and D1–D3 provider
promotion retain their own acceptance. No new paid cohort or soak is authorized.

## 12. Open decisions and holds

1. **Supported package/profile matrix:** release and host owners pin all supported
   object-only, codec-only and enforcement packages and exclusion evidence before
   M2 activation. v2 names are proposed; protocol support requires semantics.
2. **Exact commit guard:** The M2 source-session candidate uses one
   alias-scoped cross-runtime lock around the project-registry transaction.
   M3 must still prove the external-effect drain contract before activation.
   A digest recheck alone cannot discharge that hold.
3. **Canonical destination/provider import:** reuse the current path owner; follow
   #4915 without assuming merge. Provider-state adoption needs its own reviewed
   import contract; the first file-only slice rejects it.
4. **Service profile:** R6 chooses authenticated identity projection and server
   serialization. No remote local-path requirement or cloned writable registry.
5. **Retention/default rollout:** existing backup/receipt owners decide retention;
   incomplete journals are never automatically collected. Fleet-wide enforcement
   or default activation requires a separate disclosed migration/release decision.

---

## Appendix A: Execution ledger (non-normative)

### 2026-09-23 — Design synthesis

- **Baseline:** `23edcb19c70394480e3a9ebe8a960f5a320c5342`
- **Delivered:** Revised RFC proposal; M0 codec prerequisite shipped separately.
- **Evidence:** Current registry, bootstrap, deletion, binding, orphan-fence,
  backup, global-projection, and documentation contracts were inspected.
- **Known gaps:** No instance enforcement, activation or resolution fixture has shipped.
- **Effect on normative design:** Align with roadmap/TS/shared authority; separate codec
  compatibility from enforcement, specify commit fencing and legacy cleanup.

### 2026-09-23: M2 source-session implementation candidate

- **Baseline:** `cbbdd837f65c8ba28161115cc4ce39093bfa2951`
- **Proposed:** A fresh-project-only `source_session_v1` profile, exact bind and
  unbind receipts, journaled A-to-B recreation, and read-only exact resolution.
- **Evidence:** Real CLI tests cover ABA ordering, pre-publication retry,
  post-publication forward repair, exact-byte replacement rollback, operation
  ID conflicts, capacity rejection, and replay at capacity.
- **Remaining hold:** Every result has `execution_authority: false`. M3 must
  qualify the remaining effect owners before existing-project activation or
  global routing can open.

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-09-23 | Propose source-owned random Goal instance identity plus strict registry envelope | Proposal; maintainer approval pending | Counter, path identity, additive capability marker | Initial RFC |
| 2026-09-23 | Refine compatibility, typed lifecycle ownership, commit fencing and legacy cleanup against current main | Design proposal; implementation holds in Section 12 | Decoder-only exclusion, duplicate Python state machine, guessed legacy identity | Sections 3–5 and 7–12 |

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | M0 reads/writes v1 without lifetime enforcement | Named baseline / #4917 | Registry codec and bootstrap inspection | observed | Codec compatibility is not identity admission |
| E2 | The shipped fence blocks orphaned guided bootstrap but does not resolve it | Named baseline | Issue #4801 and PR #4808 | observed | Public issue and code scope |
| E3 | Global entries are source projections | Named baseline | `loopx/global_registry.py` inspection | observed | Static current-code fact |
| E4 | Activation excludes every supported old effect path | future M1–M3 fixture | Object-only/codec-only/enforcement matrix | unverified | Source/global/warm-process coverage required before activation |
| E5 | Every durable binding owner validates exact identity | future M1-M3 fixtures | Owner inventory conformance suite | unverified | Required before enforcement |
| E6 | Resolution is crash-safe and recoverable | future M4 fixtures | Fault-injection and restore matrix | unverified | Required before destructive use |

## Appendix D: Rejected or superseded alternatives

- A warning-only writer capability marker cannot constrain code that does not
  understand the marker.
- A redirect object leaves the old path writable and permits split authority.
- A global counter makes the projection an allocator or requires permanent
  tombstones.
- Automatic binding upgrade guesses history from an alias and is forbidden.
- Provider-wide cleanup as a commit precondition couples availability to every
  integration and still cannot prove external deletion.
- Runtime-directory migration does not solve non-file bindings and is outside
  issue #4801.

## Appendix E: Incident and review lessons

- A healthy current state does not prove continuity from an earlier same-name
  Goal.
- An advisory compatibility check is not a writer fence when old code ignores
  it.
- Crash safety requires journaling every plan before the first mutation, reserving
  identity when applicable, and reconciling effects whose acknowledgement was lost.
- Cleanup and authorization are separate: stale records may remain for audit
  while exact identity matching keeps them inert.
