# RFC: Complete State Recovery and Controlled Reactivation (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Partial: bounded M1 full-archive verification and inert File/SQLite inspection implemented; controlled reactivation remains open
- **Authors / owners:** Control-plane state, reliability, and affected domain maintainers
- **Created:** 2026-10-08
- **Last normative revision:** 2026-10-08
- **Implementation baseline:** `82d1b837479dd5eb64b581bb0ca36d8c1ff1f0cc`
- **Related contracts:** [Issue #5940](https://github.com/loopx-project/loopx/issues/5940), [Goal instance identity and orphan recovery](goal-instance-identity-and-orphan-recovery-v0.md), [shared authority](shared-goal-authority-state-provider-v0.md), [composable recovery verification](composable-state-machines-recovery-verification-v0.md), [Effect Interpreter](agent-loop-effect-interpreter-v0.md), [configuration backup](../../reference/configuration-backup.md)
- **Language mirror:** [Chinese semantic mirror](complete-state-recovery-v0.zh-CN.md)

## Document map and maintenance contract

Sections 1-10 define the durable design and acceptance contract. Section 11 is
the normative delivery plan. Section 12 records decisions that may remain open
without weakening the first implementation slice. Appendices contain
non-normative history and evidence.

The English and Chinese documents are semantic mirrors. A normative change must
update both in the same pull request. Acceptance of this RFC authorizes bounded
implementation slices, not live recovery, provider promotion, credential
transfer, or a default change.

## 1. Decision summary

### Current implementation checkpoint

The bounded M1 path now has additive `backup-state verify`, loopback HTTP upload
and immutable report readback, and a packaged Settings/Capability Center journey.
It verifies real producer archives and reads captured configuration plus selected
File/SQLite Todo/history through existing owners after original sources become
unavailable. Every v0 audit remains held for incomplete capture/owner metadata;
unknown state is not relabeled recoverable, and execution authority is always
false. Limits, rollback and reproduction are in the [backup guide](../../reference/configuration-backup.md#inspect-a-complete-backup-without-activating-it).
Reports retain measured archive/regular-file bytes and archive-processing and
owner-read durations; historical readback does not retime them. The backup guide
records individual native capacity cases, including exact member/history
boundaries and 128 File/SQLite Goals. History overflow refuses publication.
The Section 12 capacity-default decision is recorded; operational qualification
remains partial for compound worst cases and broader platform/provider profiles.
This checkpoint does not qualify M2/M3, live adoption, remote providers or the G1
team journey. No original identity or receipt is rewritten.

### First supported user outcome

The first bounded user promise is recovery of one local Goal after its source
workspace is unavailable or no longer trusted. The qualified profile is a
packaged local LoopX environment on a qualified POSIX host, with File/SQLite
authority and a backup plus external manifest available to the operator.

At M1, the operator can verify that backup and inspect the result from the
existing Settings/Capability Center entry. The view distinguishes durable Goal
outcomes and progress that were found and remain readable, historical-only
state, and work that is unknown or cannot be recovered. Each blocked item names
its existing decision owner and exactly one next action. Byte verification never
means that the Goal can execute.

At M3, the operator can choose the verified backup, preview impact, confirm
controlled adoption into a replacement environment of the same profile, resolve
owner holds, and return to the same product entry to see the result. After the
existing Goal, authority, session, and effect owners admit the destination, the
Goal can continue unfinished work and produce a separately accepted result.
Work after the captured owner checkpoints, including volatile in-flight work,
may be reported lost or unknown; recovery never guesses it back.

The required human actions are to supply the backup and manifest, choose a new
empty destination, reauthenticate or rebind credentials when owners require it,
resolve visible holds, and confirm adoption. Ordinary restart or session
continuation while the original workspace and authority remain available stays
with the existing session and execution owners. This RFC owns disaster recovery
from backup when that ordinary path is unavailable or unsafe. The profile's RPO
and RTO targets, baselines, and stop conditions must be frozen before the M3
drill; this RFC does not invent them in advance.

This RFC makes seven decisions:

1. A `backup-state` archive is one immutable **verification unit**, called a
   recovery set. It is not a cross-owner transaction or an activation unit.
2. Every recovery starts in a new **inert recovery workspace**. Extraction,
   verification, and audit never register a runtime, start a Host, acquire a
   lease, dispatch an effect, install an automation, or select an authority.
3. Capture declares one of three consistency profiles: online,
   quiescent, or crash-consistent. Every component records its actual owner
   boundary and source identity/revision; an undeclared boundary is unqualified.
4. Recovery coordination owns only a digest-bound plan and owner receipts.
   Existing registry, Goal lifecycle, authority store, session, quota, delivery,
   automation, and effect owners retain their decisions and commit fences.
5. Restored claims, leases, sessions, timers, and provider revisions grant no
   current authority. A controlled reactivation must mint or rotate the
   destination identities required by each existing owner and fence old writers.
6. Pending external effects and deliveries are reconciled from their original
   operation identities and receipts. Unknown outcomes block replay and may
   block activation; copied queue or journal bytes never authorize a fresh send.
7. The first implementation is read-only with respect to live state:
   `backup-state verify` may extract to an operator-selected new directory and
   emit an isolated audit, but every result states
   `execution_authority_granted=false`.

Existing backup flags, output paths and the v0 format remain supported. The
non-overwriting publication/retry boundary is described in Section 7. No command
in this RFC may turn byte integrity into execution permission.

## 2. Problem and motivation

`backup-state --execute` can capture a runtime root, project-local Goal state,
registry-discovered projects and Goal routes, SQLite snapshots, configuration,
automations, and skills in one archive. Those paths have different owners,
revision domains, credentials, and commit points. The archive manifest reports
what was copied, but does not prove that the copied facts formed one coherent
runtime state.

For example, a Todo commit may be present while its projection delivery is
pending; a Host session may reference a lease that was replaced after its
journal was copied; an external effect may have committed while its local
receipt was not yet durable; and a restored PostgreSQL snapshot may contain a
store identity still known to an old process. Extracting those bytes into a
runtime root can revive stale authority or repeat an effect even when every
file checksum is correct.

Configuration-only recovery already avoids this error: it restores to an
isolated checkpoint and explicitly grants no live authority. Complete state
needs the same default plus cross-owner inventory, fencing, reconciliation, and
readback. No individual owner can safely infer the other owners' state, and a
new monolithic restore owner would become a second authority.

### Invariants

1. Verification proves bytes and declared structure, not semantic continuity or
   permission to execute.
2. Restore never targets a registered, active, or non-empty runtime directory.
3. Every component has exactly one existing decision owner. The recovery
   coordinator cannot edit owner state except through that owner's reviewed API.
4. The recovery set is immutable and content-addressed. Plans bind its archive,
   external manifest, extracted inventory, destination facts, and owner
   revisions.
5. Cross-owner skew remains explicit. Missing revisions, cursors, dependencies,
   or capture profiles cannot be guessed from timestamps or path proximity.
6. A restored provider identity, Goal reference, lease epoch, session
   generation, or operation receipt remains historical until its owner accepts
   it under the destination's current fence.
7. An old source process and a destination process cannot both retain supported
   write authority after controlled reactivation.
8. Exact historical receipts remain readable. Recovery never relabels them as
   destination receipts or changes their operation identity.
9. An unknown external effect is never retried as a new operation. Reconciliation
   reaches committed, absent, or a visible hold.
10. Partial adoption cannot silently fall back to extracted files or a legacy
    writer. Required-owner failure leaves admission closed.
11. A projection or notification failure cannot roll back a committed owner or
    authorize repeating its business effect.
12. Recovery and audit output excludes secrets, raw private payloads, and local
    absolute paths from public artifacts.

## 3. Scope and non-goals

### In scope

- Physical and logical verification of complete `backup-state` archives.
- A recovery-set manifest and isolated audit that name component owners,
  identities, revisions, dependencies, and consistency profiles.
- Safe extraction into a new inert workspace.
- Online, quiescent, and crash-consistent capture semantics.
- Reconciliation of Goal references, authority stores, sessions, claims,
  leases, Turn journals, effects, inboxes, outboxes, automations, skills, and
  projections through their existing owners.
- Provider and credential rebinding without copying execution authority.
- Destination and old-writer fencing, partial failure, idempotent retry,
  rollback limits, and independent readback.
- File/SQLite, service-owned PostgreSQL, and future provider profiles behind one
  provider-neutral logical contract.
- The first qualified end-to-end profile: one local Goal in a packaged local
  LoopX environment on a qualified POSIX host with File/SQLite authority.

### Non-goals

- A generic tar extraction command or in-place overwrite of a runtime root.
- One transaction spanning every LoopX state owner.
- A second Goal, Todo, configuration, provider, session, quota, or effect
  authority.
- Automatic credential transfer, login, secret validation, or key rotation.
- Blind replay of external effects, deliveries, timers, or automations.
- Importing arbitrary Host caches, model context, or third-party provider state.
- Live restore or automatic activation in the RFC pull request.
- Declaring a platform, filesystem, provider, RPO, or RTO qualified without its
  own measured acceptance evidence.
- Qualifying multi-Goal recovery, service providers, cross-machine transport, or
  unattended activation as part of the first end-to-end profile.

### Relationship to existing recovery contracts

This RFC composes existing owners; it does not replace them:

| Existing contract | This RFC's boundary |
| --- | --- |
| [Goal instance identity](goal-instance-identity-and-orphan-recovery-v0.md) | Decides whether a restored Goal remains historical, is imported under a new lifetime, or may continue only after exclusive replacement fencing. Recovery cannot mint or reuse a Goal instance on its own. |
| [Shared authority](shared-goal-authority-state-provider-v0.md) | Owns authority export/import, operation receipts, provider lineage, store-incarnation rotation, writer fencing, and source selection. An extracted authority database is not selected authority. |
| [Composable recovery](composable-state-machines-recovery-verification-v0.md) | Owns bounded cross-domain recovery verification and independent oracles. This RFC supplies the backup/restore lifecycle to exercise. |
| [Effect Interpreter](agent-loop-effect-interpreter-v0.md) | Owns effect identity, settlement, unknown-outcome readback, and replay decisions. Recovery only routes original evidence to it. |
| [Configuration backup](../../reference/configuration-backup.md) | Keeps configuration in an isolated checkpoint. Adoption continues through machine and Goal configuration owners. |

### Backup inspection and research checkpoint reuse

The merged [research checkpoint contract](../../reference/research-checkpoint.md)
serves a distinct research branch with an independently admitted Goal/session.
Complete-backup inspection serves an operator whose source may be unavailable.
Neither format is an input to the other's verifier today. Both retain original
owner authority; research branching may retain a live source, while disaster
replacement must exclude old writers before admitting new work.

| Boundary | Existing owner and reuse | Deliberate separation |
| --- | --- | --- |
| State inventory | `state_backup.build_state_backup_plan`; checkpoint `_loopx_state_components` already calls it for a scoped single Goal | Full archives can contain multiple Goals; checkpoint rejects incomplete or moving scoped inventory |
| SQLite capture | Both creation paths use the existing `snapshotSqliteDatabase` owner | M1 reads captured databases offline; it must not snapshot an untrusted restored database into current authority |
| Digests | Both typed owners reuse `canonicalAuthoritySha256` for structured identity; byte digests bind files | Archive/external/embedded manifests and checkpoint component witnesses have different schemas; digest equality is not cross-format compatibility |
| Isolation and publication | Existing archive adapter plus typed recovery owner; checkpoint `checkpoint_artifacts.ts` owns its private staging and manifest verification | Untrusted tar links, headers and expansion require archive-specific checks; checkpoint directory capture rejects symlinks and checks source movement. Similar filesystem operations do not establish duplicate decision rules |
| Historical readback | M1 uses canonical configuration/File/SQLite/Todo/history readers and immutable saved audits | Checkpoint uses revision-bound Todo context, Explore event codec and native session history; an M1 readability report is not an adoptable session |
| Session, knowledge and work adoption | Existing Goal lifetime, Codex session, Todo continuation, Explore and effect owners | Checkpoint admission/adoption is explicit; M1 invokes none of it. Leases, quota, timers, credentials and effects remain historical |

The bounded refactor assessment finds the shared inventory, snapshot and digest
rules already reused. No new common restore service or format converter is
needed for M1; merging the two extraction paths would obscure their different
input checks. Python remains the archive/Host adapter; typed owners retain
shared decisions. This assessment records source contracts, not a newly executed
joint-recovery result.

**M2 checkpoint:** extend the existing component/profile acceptance with explicit
format/version, complete component inventory, captured owner revisions and
capture boundary, and provider qualification. A future bridge must first verify
these facts in isolation. Missing v0 facts stay unknown; never infer them from
file timestamps or successful SQLite reads. Keep existing v0 historical readers.

**M3 checkpoint:** only a qualified profile can proceed from isolated verification
to current Goal/session admission, knowledge adoption and current work admission.
Reuse the existing owners above; retain source digest, destination identity and
operation identity across retries. A partial interruption requires original-owner
readback/compensation, and an unknown external result requires reconciliation
under its original identity. Branching does not certify exclusive replacement.
No format bridge or automatic activation is shipped by M1.

The existing M3 drill remains the joint acceptance: an ordinary Goal completes
some work and leaves a Todo unfinished; after its source becomes unavailable,
the packaged journey previews recovered/historical/unknown work, obtains owner
admission, continues the unfinished work and independently checks its result.
Include missing/corrupt components, version/provider refusal, capture movement,
stale identity, retry and interrupted adoption; prove no old authority revival
or private-data disclosure. This is M2/M3 evidence, not a new full-recovery M1
prerequisite. M1's existing Settings entry reports readable tasks/history and
holds; it cannot offer a working continuation action before those stages qualify.

## 4. Current-system contract

At the named baseline, `loopx/state_backup.py` discovers the runtime root,
project `.loopx` state, host-specific Goal directories, registry-declared
project and Goal routes, optional automations, and optional `loopx-*` skills.
It writes `loopx_state_backup_v0` as a private tar archive plus external JSON
manifest. It embeds a second manifest and a verified
`configuration-backup.json`.

SQLite members receive online snapshots through the existing effect runtime.
The snapshot digest and size are recorded and WAL/SHM sidecars are excluded.
Other files and directories are traversed while the source may remain live.
The command therefore has component-local SQLite snapshot semantics and
best-effort file capture, not a cross-owner point-in-time guarantee.

At the design baseline, `loopx backup-state` supported plan/create only, and
tests manually extracted archives. The current bounded M1 route in Section 1
adds verified inert extraction and existing-owner readback through CLI and the
packaged UI. It does not adopt identity, reconcile pending effects or reactivate
a runtime; those later owner boundaries remain open.

Configuration backup separately supports export, verify, and isolated restore.
Its checkpoint is inert: it creates no live registry, provider selection,
writer fence, lease, Host session, grant, or timer. Canonical authority exports
likewise preserve their own lineage and receipts without selecting themselves
as live authority. These are component contracts, not complete-state recovery.

The current full archive may contain private paths, identities, credentials
already present under captured roots, and third-party state. It is owner-only
on POSIX but not encrypted. `privacy_certified=false` remains false after
checksum verification.

## 5. Proposed architecture

### Ownership and authority

The recovery coordinator is a control-plane workflow, not a state provider. It
owns these facts only:

- recovery-set and destination digests;
- a component/dependency inventory;
- verification and reconciliation findings;
- a phase journal for its own idempotent orchestration;
- immutable receipts returned by existing owners.

It cannot decide Goal identity, canonical authority, lease validity, session
resumption, quota settlement, effect replay, delivery, credential validity, or
automation admission. Each action calls the existing owner with a
recovery-scoped idempotency key and exact expected revision. Owner readback,
not coordinator memory, establishes success.

The recovery-set archive is the verification unit. Controlled adoption uses
smaller owner-defined units, usually one destination runtime configuration, one
Goal lifetime and authority lineage, or one Host/session binding. A selected
unit must include its required dependency closure. This prevents an archive
that spans several projects from becoming an accidental global activation
transaction.

### State model and schema

`loopx_state_backup_v0` remains readable. A future additive manifest revision
must describe each component with:

| Field | Meaning |
| --- | --- |
| `component_id` | Stable identifier within one recovery set; not a live owner identity |
| `owner_kind` and `logical_scope` | Existing owner and machine/project/Goal/session/effect scope |
| `archive_members` | Exact normalized archive paths covered by the component |
| `required` | Whether the declared recovery-set profile is incomplete without it |
| `schema_version` and `producer_version` | Decoder and compatibility boundary |
| `capture_profile` | `online`, `quiescent`, or `crash_consistent` |
| `source_identity` | Owner-defined Goal, provider, store, session, or execution lineage |
| `source_revision` and `cursor` | Owner-defined snapshot/readback position when available |
| `content_digest` | Digest over canonical component bytes or owner export |
| `dependency_ids` | Components required before audit or adoption |
| `verification_profile` | Named owner verifier and qualification version |

Private source locators may exist in the private external manifest. Public-safe
receipts use component IDs and redacted owner/scope labels, never absolute paths
or raw payloads.

The first implementation emits `loopx_state_recovery_audit_v0` without changing
the backup producer:

```json
{
  "schema_version": "loopx_state_recovery_audit_v0",
  "recovery_id": "recovery-content-address",
  "backup": {
    "schema_version": "loopx_state_backup_v0",
    "archive_sha256": "sha256",
    "external_manifest_sha256": "sha256"
  },
  "workspace": {
    "isolation": "inert",
    "registered": false
  },
  "components": [],
  "findings": [],
  "activation_eligible": false,
  "execution_authority_granted": false
}
```

For a v0 archive, missing owner revisions and capture profiles are reported as
`legacy_manifest_incomplete`; they are not fabricated. The audit may still
prove archive integrity, safe extraction, SQLite integrity, configuration
verification, and recognizable owner-local exports.

The orchestration lifecycle is:

`planned -> verified -> restored_inert -> audited -> adoption_prepared ->
applying -> complete`

`held` may be entered from any post-verification phase with a typed finding and
one next action. `abandoned` is legal before the first live-owner mutation.
After a live-owner mutation, cancellation becomes owner-specific compensation
or forward repair; it cannot simply delete the journal.

The M1 verify/audit slice stops at `audited` or `held`. Neither state grants
execution authority.

The packaged M1 surface renders this audit through the existing
Settings/Capability Center entry rather than adding a new top-level workflow.
For each discovered asset, it shows one of: recovered and readable,
historical-only, or unknown/unrecoverable. It also shows the responsible
existing owner and exactly one next action. A valid digest, SQLite check, or
configuration check may make an asset readable; none of them makes it
executable.

### Capture consistency profiles

Profiles describe evidence, not marketing tiers:

| Profile | Required source condition | Guarantee | Explicit limit |
| --- | --- | --- | --- |
| `online` | Runtime may remain active; every mutable required component uses an owner snapshot/export and records its revision interval | Component-local atomic snapshots with a complete skew inventory | No global point in time; ordinary directory traversal alone is unqualified |
| `quiescent` | New admission is closed; supported writers are drained or fenced; pending effects reach a recorded state; owner snapshots finish before reopening | Required components are captured against one declared quiescence epoch and checked dependency revisions | External systems are not rolled back; an unknown effect remains a hold |
| `crash_consistent` | Source is already stopped or failed; no cleanup mutates it before capture | Exact durable bytes plus owner journal/replay inputs from one declared failure observation | Lost in-memory work is outside RPO; every owner must prove crash recovery before adoption |

The recovery-set manifest records capture start/end, the profile requested, the
profile actually achieved per component, and downgrade findings. The set-level
profile is no stronger than its weakest required component. A warning cannot
silently upgrade a profile.

Current `backup-state` is classified as legacy best-effort online capture until
each required mutable component has an owner snapshot or declared immutable
boundary. SQLite members may independently report their stronger snapshot fact.

### Verification and inert restore lifecycle

The first command surface is:

```console
loopx backup-state verify \
  --archive /operator-owned/loopx-state-example.tar.gz \
  --manifest /operator-owned/loopx-state-example.manifest.json \
  --destination /operator-owned/new-recovery-workspace

loopx backup-state verify \
  --archive /operator-owned/loopx-state-example.tar.gz \
  --manifest /operator-owned/loopx-state-example.manifest.json \
  --destination /operator-owned/new-recovery-workspace \
  --execute
```

Without `--execute`, it validates inputs and returns a plan. With `--execute`,
it may create only the new inert destination and audit files. It performs no
live-state mutation. Existing `loopx backup-state [options]` plan/create syntax
remains valid; `verify` is an optional action, not a reinterpretation of current
flags.

Verification is ordered:

1. Require a regular archive and external manifest, verify the archive digest,
   manifest schema, backup identity, and embedded/external manifest agreement.
2. Preflight destination non-existence, physical parent, symlink ancestors,
   owner-only permissions, available space, entry count, logical byte budget,
   and expansion ratio.
3. Reject absolute paths, `..`, duplicate normalized names, case or Unicode
   collisions for the qualified platform, device/FIFO/socket members, hard-link
   ambiguity, and links escaping the workspace.
4. Extract without preserving source UID/GID, setuid/setgid bits, ACL authority,
   or process state. Internal symlinks remain inert and are reported.
5. Recompute member and component digests. Run SQLite integrity checks against
   copies, configuration verification, and registered owner-local verifiers.
6. Build the owner/dependency graph and report missing, extra, ambiguous,
   unknown-schema, and cross-owner reference findings.
7. Write an owner-only immutable audit receipt. Never load plugins, execute
   restored code, import credentials, register the workspace, or start a Host.

A verifier failure does not replace an earlier successful audit. Temporary
files stay under the destination's private staging area and are removed after
failure where safe.

### Reconciliation and controlled reactivation

Reconciliation follows dependencies rather than tar order:

| Order | Owner boundary | Required disposition |
| ---: | --- | --- |
| 1 | Destination/runtime lifecycle | Prove a new empty destination or an exclusively fenced replacement; mint a recovery operation identity |
| 2 | Credentials and external dependencies | Report references only; reauthenticate or rebind through the current owner; never adopt copied secret authority automatically |
| 3 | Project registry and Goal lifecycle | Classify each Goal as historical, new-lifetime import, or exact replacement continuity admitted by the Goal owner |
| 4 | Canonical authority provider | Verify export/import and receipts; bind the chosen source; rotate or mint provider/store incarnation before writes |
| 5 | Claims, leases, quota, and scheduler state | Preserve history; retire or invalidate restored execution generations; reacquire through current admission |
| 6 | Host sessions and Turn journals | Preserve readback; resume only an exact admitted lineage after effect reconciliation and a new current execution fence |
| 7 | Effect journals, inboxes, and outboxes | Reconcile original operation receipts; redeliver only through owner idempotency; unknown outcomes remain held |
| 8 | Automations, skills, and optional code | Restore as disabled data; review compatibility and explicitly install/enable through current owners |
| 9 | Derived projections and UI state | Rebuild from selected canonical sources; stale copied projections never decide authority |

An owner disposition is one of `retain_historical`, `import_inert`,
`rebind`, `reconcile`, `rebuild`, or `discard`. Only the owner can map a
disposition to effects. Unknown owner kinds or unsupported versions remain
inert and block any profile that declares them required.

External-effect reconciliation uses the original operation identity:

- `committed`: retain the receipt and continue only the owner's post-commit
  delivery path;
- `absent`: the owner may reissue the same admitted operation if its current
  authority and retry contract allow it;
- `unknown`: query the provider or require operator resolution; do not dispatch;
- `rejected` or `superseded`: retain history and never replay.

Controlled reactivation is a separate, later command and review boundary. It
requires a digest-bound adoption plan, unchanged destination revisions,
source/destination quiescence as applicable, owner compatibility, and no
required unresolved finding. Before the first live mutation, abort leaves only
the inert workspace. Each mutation persists intent, uses an idempotency key
derived from recovery/component/action digests, and records independent
readback.

Old-writer exclusion is profile-specific. It may require Goal retirement or
continuity fencing, provider-incarnation rotation, authority-source writer
fences, session/execution generation changes, lease epochs, service tenant and
principal checks, and revocation of old credentials. Stopping a PID, renaming a
directory, or copying a store identity is insufficient.

Recovery receipts always report `execution_authority_granted=false`. When all
required adoption and fencing steps pass, existing runtime/executor admission
owners may separately issue their normal grants. The coordinator records those
receipts but cannot mint or broaden them.

### Provider and extension contract

Every provider profile implements:

- immutable export or snapshot and canonical digest;
- offline verify with version/capacity limits;
- import into a non-authoritative destination;
- lineage/incarnation handling that invalidates stale writers;
- exact operation-receipt and cursor readback;
- fenced source selection and rollback/export of later writes;
- typed missing, mismatch, unknown, ambiguous, unsupported, and capacity
  failures.

File/SQLite profiles use their existing authority export/import and writer-fence
contracts. Service-owned PostgreSQL uses administrative restore plus mandatory
store-identity rotation before admission; Agents never receive database access.
NoKV or another provider is unsupported until its profile independently proves
the same logical contract. A generic filesystem copy cannot stand in for a
provider profile.

### Capture-owner inventory for the first local profile

The source inventory below targets the first single-Goal File/SQLite profile.
It identifies where M2 must obtain evidence; it does **not** certify that a
particular backup contains every required component. An actual capture must
resolve the selected Goal's routes and dependency closure, distinguish an
owner-confirmed absence from an unreadable or unrecognized source, and retain
unresolved required owners as holds. Optional installation does not make an
active Goal dependency optional.

| Mutable boundary and existing owner | Current capture/readback | Remaining capture or adoption evidence |
| --- | --- | --- |
| Registry routing and machine/Goal configuration: [configuration capture adapter](../../../loopx/capabilities/configuration_backup.py), [typed checkpoint envelope](../../../loopx/control_plane/configuration_backup.ts), [machine configuration store](../../../loopx/capabilities/machine_configuration/store.py) | Full backups compare the registry and stored machine-configuration bytes actually decoded by their owners with mapped copied-byte witnesses. Missing/differing copies, A → B → A and a captured absence beside an earlier copied file refuse publication. The private manifest records matching revisions and the configuration capture interval. | Bind authority and every other required owner to their own intervals and dependencies. Configuration-source agreement and a valid envelope do not prove cross-owner consistency. |
| Goal narrative, vision and authority material references: registry-resolved active state, [vision checkpoint owner](../../../loopx/control_plane/goals/vision_checkpoint.ts) | Discovered active-state paths and declared local `kind=goal_doc` documents are copied through the existing material-root boundary. Hash-only registrations and document links are not recursively resolved. | Preserve the full Goal and revision-bound required artifacts, classify unavailable dependencies, and keep derived Markdown projections subordinate to their owners. |
| Canonical work, leases and retained operation history: [local provider selection](../../../loopx/control_plane/coordination/local_authority_provider.ts), [authority archive owner](../../../loopx/control_plane/coordination/authority_archive.ts), [legacy writer fence](../../../loopx/control_plane/coordination/legacy_writer_fence.ts) | File head/history share an atomic envelope; SQLite uses the [existing physical snapshot owner](../../../loopx/control_plane/coordination/sqlite_backup.ts). M1 opens existing stores and verifies head/history without selecting a live destination. | Bind provider/store identity, selector, fence, revision and cursor; obtain an owner export or qualified snapshot. Component-local consistency cannot establish cross-owner consistency or make historical leases valid. |
| Quota accounting and Turn settlement: [accounting artifact transaction](../../../loopx/control_plane/quota/accounting_artifact_transaction.ts), [spend owner](../../../loopx/control_plane/quota/spend_commit.ts), [Turn journal commit](../../../loopx/control_plane/turn_driver/turn_journal_effects.ts) | Files under included runtime/project roots are retained; M1 does not reconcile their transaction lineage. | Capture committed receipts, indexes and in-flight journal inputs together with their exact settlement identity; let the original owner resolve replay or ambiguity without a second debit. |
| Goal lifetime and session/execution binding: [source-session lifetime owner](../../../loopx/control_plane/goals/source_session_lifetime.ts), [Chat session lifecycle](../../../loopx/control_plane/goals/chat_session_lifecycle.ts), [execution identity](../../../loopx/control_plane/runtime/execution_identity.ts) | Existing Goal-scoped registration, recreation and Turn-settlement directories beside the actual registry are retained through their existing path owners. Native Host session stores outside those paths are not implicitly captured or adopted. | Resolve the actual entrypoint's owner, lifetime and generation. Prove stale executor exclusion and a qualified session recovery path before the original entrypoint can continue. |
| Pending effects, delivery and derived projection outboxes: [source Turn effect gate](../../../loopx/control_plane/goals/source_session_lifetime.ts), [return-delivery classifier](../../../loopx/control_plane/collaboration/return_delivery.ts), [authority shadow outbox](../../../loopx/control_plane/coordination/local_authority_shadow_outbox.ts) | Included journals/outboxes are retained as bytes; neither their external outcome nor their replay eligibility is inferred. | Each owning effect/transport resolves committed, absent or unknown outcomes and exact receipts. Distinguish a derived projection from an external effect; an unknown external result stays held. |
| Scheduler and optional Host automation: [scheduler state owner](../../../loopx/control_plane/scheduler/state_store.ts), [backup discovery](../../../loopx/state_backup.py) | Runtime scheduler files and explicitly included Codex automation/skill directories can be copied. A saved timer is not an observed active Host schedule. | Bind cadence/reset identity, reconcile actual Host state and exclude duplicate admission. Other Host/provider state needs its own explicit capture contract. |
| Knowledge/artifacts, extension state and credentials: the selected Goal's material and provider owners, including [decision-context recovery](../../../loopx/capabilities/decision_context/capture_recovery.py) where applicable | Only discovered roots are included; a physical SQLite snapshot proves no semantic provider completeness. Credentials may be present in private raw bytes. | Enumerate actual required sources and provider versions; obtain their own export/verify evidence. Credential custody, rotation/revocation and external access remain with their owners; copied bytes grant no access. |

The [source-session lifecycle path
owner](../../../loopx/control_plane/goals/source_session_registry_state.py) now
supplies the same registration, recreation and Turn-settlement directories to
the writers and full-backup discovery. This closes the omission beside an
external source registry, without sweeping other Goal directories or treating
journal bytes as reconciled effects. Source-loss File/SQLite producer/verifier
coverage retains those bytes and the declared local Goal document while keeping
the audit inert and held. M2 still needs the complete required-owner closure,
independent intervals/dependencies and actual material/provider exports; a
hash-only registration is not captured source content.

**Observed ordering counterexample and bounded refusal.** The earlier producer
could copy raw registry A and later encode configuration B while every byte and
configuration digest passed. The current full producer retains the exact source
snapshots used for decoding, compares all mapped regular copies, and refuses
publication when they differ or lack a witness. File/SQLite tests cover both a
persistent change and A → B → A; a later live-path reread cannot prove this case.
Routed sources and missing witnesses have separate negative coverage.

The same rule covers the existing stored machine-configuration owner. Its exact
read supplies the checkpoint and witness together, including confirmed absence;
changed, newly created or removed configuration cannot contradict the raw copy.
Unusable machine sources refuse capture rather than become an absent envelope.
Self-contained local aliases reuse the included regular target witness; a retained
tar link cannot masquerade as an owner-confirmed absence. Repointed aliases,
including A → B → A, are compared through the saved route.
This closes the configuration-source IO boundary, not the complete recovery
set's owner closure or consistency profile.

Historical v0 archives can still contain that combination. A legacy-archive
fixture in [the recovery suite](../../../tests/test_state_recovery.py) deliberately
retains earlier raw registry/work and later owner-verified configuration, without
new capture claims. After the original sources disappear, M1 reads the earlier
work and retains `held`, `legacy_best_effort`, `legacy_manifest_incomplete` and
no execution authority. This preserves historical readback while tightening new
publication; it does not qualify an `online` profile or the full M2 inventory.
Retaining the lifecycle journals above does not authorize generic reads of the
lifecycle-only `source_session_v1` profile, which remains refused.

The next implementation slice belongs to the existing backup/recovery workflow:
resolve one Goal's required owner closure, capture configuration and canonical
authority through those owners, and record their source intervals/dependencies
before any profile can be qualified. Reuse the typed authority export/snapshot
and configuration envelope; Python remains the filesystem/transport adapter.
Exercise concurrent writes, a missing required owner and moved dependencies
through the real producer and inert verifier. Other rows remain explicit holds
until their owner evidence exists. M2's complete-inventory entry and
profile-specific exits, M3 admission, and the frozen RPO/RTO criteria stay open;
this inventory neither adds a parallel coordinator nor changes their criteria.

### Implementation ownership and reuse evidence

M3 implementation records a decision-owner matrix before code is admitted. For
each Goal identity, authority, lease/session, and effect disposition, the matrix
names:

- the existing TypeScript decision owner and reviewed API that remains
  authoritative;
- the backward-compatible reader retained for existing v0 archives and
  historical receipts;
- any duplicate decision rule or migrated caller that can be deleted;
- the real CLI or packaged-product consumer that exercises the owner and reads
  its result back.

Implementation progress is measured by fewer locations that independently make
the same decision, a shorter trace from each real consumer to its owner, lower
caller-location and verification cost, and behavior-preservation evidence for
positive, rejection, retry, and stale-generation cases. Counts of enums, RPCs,
files, or newly introduced types are inventory, not progress evidence.

## 6. Alternatives and design choices

### Raw extraction

Rejected. It proves only that bytes can be materialized and makes stale state
look runnable. It has no owner, identity, effect, or fence semantics.

### One monolithic restore transaction

Rejected. LoopX owners and external providers do not share one commit protocol.
A central transaction would duplicate authority, hide partial outcomes, and be
unable to roll back external effects.

### Owner-by-owner manual runbook only

Rejected as the primary contract. Owner tools remain decisive, but an
unversioned checklist cannot bind one archive, destination, dependency graph,
or retry identity. The coordinator provides those facts without taking over
owner decisions.

### VM or filesystem snapshot as the recovery contract

Rejected. Such snapshots can improve capture consistency but do not reconcile
provider identities, Goal lifetimes, external effects, credentials, or delayed
delivery. They may be one capture mechanism under a declared profile.

### Restore everything disabled, then enable it all at once

Rejected. Disabled data is the correct initial state, but one global enable
cannot prove each owner's revisions and fences. Adoption must follow dependency
order and stop on required ambiguity.

## 7. Safety, privacy, and compatibility

Backups and recovery workspaces are private by default. They may contain source
code, private paths, identities, prompts, session bodies, credentials, and
provider handles. Commands print bounded counts, component IDs, digests, typed
findings, and relative workspace references. They do not print member contents,
secrets, raw errors from private providers, or absolute paths in public evidence.

The M1 implementation performs no network call except explicitly selected
owner-local verification that is documented as offline. It loads no restored
plugin, skill, Python module, Node package, shell profile, database extension,
or executable. Archive names and metadata are untrusted input.

Owner-only POSIX modes are required. Native Windows and filesystem ACL behavior
remain unqualified until tested. Case-insensitive and Unicode-normalizing
filesystems require collision checks before extraction. Unsupported hard links,
special files, sparse expansion, oversized members, or excessive entry counts
fail before publication.

Existing v0 archives remain verifiable with reduced assurance. Existing backup
creation flags and output paths do not change. Unknown fields are preserved in
private evidence where safe; unknown required semantics fail closed. Older
binaries do not gain a restore path and cannot open an inert workspace through
fallback discovery.

Full-backup publication now refuses occupied archive or manifest names instead
of replacing them independently. It prefers exclusive filesystem links, manifest
before archive, and flushes directory entries on POSIX. When linking is unsupported,
exclusive private-file copy preserves non-overwrite behavior, at the cost of another
copy and possible visibility of an unfinished file. There is no overwrite fallback. An interrupted
attempt can leave a partial pair; a lost response can leave a complete pair.
Neither is silently overwritten or deleted by retry. Verify both files and their
checksum relation, and choose a fresh backup id/output directory for a new capture.
This preserves earlier verification units; it does not qualify an M2 capture
profile or establish cross-owner consistency. See the
[backup publication guide](../../reference/configuration-backup.md#full-backup-publication-and-retries).

Credentials present in an archive remain private historical bytes. Recovery
does not claim they are current, copy them into a live credential store, or use
them for provider probes. Cross-machine transfer, encryption at rest, revocation,
and destination authentication remain explicit operator/security decisions.

## 8. Migration and rollback

M1 is additive: verify v0 archives and produce an audit beside a new inert
workspace. It changes no producer schema and no live state. Removing that
workspace rolls back the operation.

A later manifest revision is introduced through dual-read/new-write behavior.
The verifier reads v0 with `legacy_manifest_incomplete` findings and reads the
new revision exhaustively. The backup producer writes the new revision only
after fixture, size, privacy, and downgrade tests. It never rewrites an existing
archive or silently upgrades its claimed capture profile.

Controlled adoption requires:

1. Reverify the immutable recovery set and audit.
2. Rebuild and bind the destination/owner inventory and plan digest.
3. Quiesce or fence source and destination writers required by the profile.
4. Prepare every required owner action before the first live mutation.
5. Apply in dependency order with durable intent and exact readback.
6. Reconcile effects and deliveries before opening ordinary admission.
7. Publish the selected source and only then rebuild derived projections.

Before the first live mutation, abort deletes only the inert candidate. During
adoption, retry the same recovery and operation identities. A completed owner
is not rolled back by restoring old bytes. Compensation uses that owner's
reviewed reverse operation.

After ordinary destination work begins, generic rollback is forbidden. Returning
to another provider or source requires a fresh fenced export/import that includes
all later writes. If an owner has an unknown external outcome, forward
reconciliation is mandatory.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| Archive and manifest bind exactly | Change archive, external manifest, embedded manifest, backup ID, or digest | Verification rejects before extraction publication | Does not prove semantic consistency |
| Extraction is confined | Absolute/traversal paths, escaping links, duplicate/case/Unicode collisions, special files, expansion limits | No write outside staging; typed rejection; prior audit unchanged | Platform matrix is explicit |
| M1 stays inert | Verify/extract while monitoring registries, processes, providers, network, leases, timers, and effects | Only new workspace/audit bytes; `execution_authority_granted=false` | No activation claim |
| M1 audit is understandable in product | Open a verified or held audit through the packaged Settings/Capability Center entry | Found assets are classified as recovered/readable, historical-only, or unknown/unrecoverable; every blocked item has its owner and exactly one next action; byte verification is not shown as execution permission | Read-only; no restore or activation claim |
| SQLite and configuration remain valid | Corrupt snapshots and configuration; valid WAL-backed fixture | Independent integrity and owner verification; corruption rejects | Other owners need their own verifier |
| v0 uncertainty is honest | Verify current archives with missing component revisions/profiles | Useful byte audit plus `legacy_manifest_incomplete`; activation ineligible | No inferred timestamp consistency |
| Capture profiles are truthful | Concurrent writers, quiescence failure, crash journals, required-component downgrade | Achieved profile is no stronger than evidence; incomplete required component fails | External system state stays external |
| Dependency graph is complete | Missing/duplicate/cyclic/unknown owner and cross-Goal references | Typed finding and deterministic ordering; required ambiguity blocks | Optional historical data may remain inert |
| Goal lifetime is not resurrected | Restore deleted/recreated same-alias Goal and stale bindings | No old binding writes, spends, delivers, or settles successor state | Historical readback retained |
| Provider incarnation is fenced | Snapshot/restore with an old live writer; File/SQLite and real PostgreSQL profiles | Old revision token/writer rejects; imported head and receipts read back exactly | Each provider qualified separately |
| Claims and sessions remain historical | Restore active lease/session/automation and attempt resume | No execution until current owner reacquires/rebinds; stale generation rejects | Does not promise Host resumability |
| Effects do not duplicate | Crash before/after external commit and local receipt; restore every point | Same operation reconciles to committed/absent/unknown; no blind new dispatch | Provider readback may require operator action |
| Delivery is recoverable but not authoritative | Committed source with missing outbox ACK, stale inbox cursor, unavailable sink | Owner idempotently resumes or reports hold; source commit is not repeated | Transport qualification remains separate |
| Partial adoption is fail-closed | Fail each owner before/after effect and acknowledgement | Retry converges through owner readback; required failure keeps admission closed | No global rollback claim |
| New live work blocks byte rollback | Complete adoption, perform a new write, request rollback | Generic rollback rejects; fenced forward export/import required | Pre-mutation inert workspace remains removable |
| Privacy boundary holds | Archive with credentials, private paths, session content, and provider errors | Public output contains only bounded redacted facts | Private audit remains private |
| M3 restores one Goal end to end | In the first supported profile, complete part of one Goal, leave a task unfinished, inject failures at each critical owner commit point, then recover through the packaged journey | The operator selects a backup, previews impact, confirms, resolves holds, and retries; original owners admit the destination; the unfinished task continues to a separately accepted result visible at the original entry | Source workspace is unavailable; one local Goal, qualified POSIX host, File/SQLite authority |
| M3 recovery outcome and cost are measured | Record captured checkpoints, recovered and lost/unknown work, elapsed time, human interventions, holds, and protected external operations; freeze targets before the drill | Protected duplicate operations equal zero; repeat failure or unknown outcome visibly holds with owner and continuation condition; measured RPO/RTO is compared with the pre-frozen target | No target is invented after results are known |
| M3 reuses existing decision owners | Review the decision-owner matrix and trace real CLI/product consumers through positive, rejection, retry, and stale-generation cases | Compatible historical readers remain; duplicate decision rules and migrated callers are deleted where proven redundant; independent readback preserves behavior and reduces repeated decisions and caller-location/verification cost | Enum, RPC, type, and file counts do not establish progress |

Acceptance records passed, failed, skipped, and untested rows separately.
Provider or platform skips are not green. Fault tests use disposable synthetic
state and real affected storage boundaries; they never restore an active user
runtime.

## 10. Operational contract

Stable operator states are `verifying`, `restored_inert`, `audit_held`,
`adoption_prepared`, `applying`, `reconciliation_held`, `complete`, and
`abandoned`. They describe recovery workflow, not business execution state.

Every status exposes:

- recovery ID, backup ID, schema and digest;
- requested and achieved capture profile;
- required/optional component counts by owner;
- verified, invalid, missing, ambiguous, unsupported, and unknown counts;
- recovered/readable, historical-only, and unknown/unrecoverable asset counts,
  with the responsible owner for every blocked item;
- current phase, last durable owner receipt, retryability, and one next action;
- whether source and destination writer fences are established;
- `execution_authority_granted`, which remains false for verification/recovery.

Raw local paths, member names containing private data, credentials, and payloads
remain in private audit storage. Public diagnostics use redacted component IDs.

Capacity limits cover archive bytes, expanded bytes, member count, path length,
component count, graph edges, SQLite verification time, and audit size. Defaults
must be measured and versioned before M1 ships. Limit failures occur before
destination publication and leave prior evidence intact.

The recovery journal and owner receipts are retained at least as long as the
restored state and according to existing backup/authority retention rules.
Incomplete live adoption is never garbage-collected automatically. An inert,
never-adopted workspace may be removed after digest-bound operator confirmation.

RPO and RTO are profile- and provider-specific. M1 reports verification duration
and data size but makes no recovery-time claim. Alerts distinguish corrupt input,
unsupported schema, capacity, missing dependency, fence failure, unknown effect,
and pending delivery.

M3 drill receipts additionally report captured checkpoints, recovered work,
lost or unknown work, elapsed time, human interventions, and protected duplicate
operations. A repeated failure or unknown outcome remains visibly held with its
owner, continuation condition, and one next action.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M1: verify and readable inert audit | Existing v0 archive verification, safe extraction to a new workspace, SQLite/configuration checks, owner/dependency inventory, and a read-only Settings/Capability Center view of recovered/readable, historical-only, and unknown/unrecoverable assets with owner and one next action; `execution_authority_granted=false` | Accepted RFC; frozen limits and audit schema | CLI dry-run/execute, packaged view, malicious archive negatives, v0 fixtures, no-live-mutation proof, docs/public-private checks | Remove never-adopted workspace and audit |
| M2: declared capture profiles | Component manifest revision; online/quiescent/crash-consistent capture facts and downgrade findings | M1 plus complete mutable-owner inventory | Concurrent/quiescence/crash fixtures on qualified platforms; schema size/compatibility evidence | Continue v0 creation; verifier dual-reads |
| M3: first packaged local Goal recovery | In the first supported profile, select a backup, preview impact, confirm, restore one Goal into the same kind of packaged local environment, resolve or retry holds, obtain original-owner admission, continue unfinished work, and read a separately accepted result at the original entry | M1; relevant M2 profile; qualified POSIX/File/SQLite profile; Goal, authority, session, and effect owner support; decision-owner matrix; pre-frozen drill targets | Complete some work and leave a task unfinished before capture; fail every critical owner commit point; record recovered and lost/unknown work, elapsed time, human interventions, visible hold continuation conditions, behavior-preserving owner reuse, and zero protected duplicate operations; prove old writers reject | Before new work, owner compensation; afterward fenced forward export/import |
| M4: service/provider profiles | Service-owned PostgreSQL and separately admitted providers with restore-incarnation, tenant, credential-rebind, capacity, and availability evidence | M3 semantic contract and provider-specific operations review | Real backup/restore, ambiguous commit, old-service writer, failover and receipt/cursor readback | Provider's reviewed export/source-selection workflow |
| M5: expanded operational qualification | Additional transports and platforms, retention policy, recurring drills, measured RPO/RTO qualification, and release operating eligibility | At least one M3/M4 profile approved for the target release | Transport/platform-specific drills, interrupted recovery, human takeover, retention proof, accessibility/audience review, and release runbook | Disable activation entry; retain verify/audit and historical receipts |

Each milestone is independently useful. M1 makes recovered evidence readable in
the existing packaged product without activation. M2 improves future backups
without activating them. M3 fulfills the first end-to-end user promise for one
local Goal. M4 adds provider profiles, while M5 expands transport, platform,
retention, and operating qualification; neither defers the basic M1/M3 product
journey or makes an earlier profile retroactively safe.

The RFC pull request implements none of these runtime milestones.

## 12. Open decisions

1. **M1 capacity defaults.** The backup/reliability maintainers must freeze
   archive, expansion, entry, graph, and verification-time defaults before M1
   merge. Recommendation: derive them from current backup-size telemetry and
   fail closed with explicit overrides. This does not block the RFC. The
   [recorded decision below](#m1-capacity-default-decision) freezes only the
   existing local inert-inspection profile; broader qualification remains open.
2. **Exact replacement continuity.** The Goal lifecycle and provider owners must
   decide before M3 whether any local disaster-recovery profile may retain a
   source `goal_instance_id`. Recommendation: mint a new lifetime unless the old
   authority is provably and irreversibly fenced and the existing lifecycle
   owner issues a continuity receipt.
3. **Archive encryption and credential retention.** Security/release owners must
   decide before portable or cross-machine recovery is advertised. Recommendation:
   keep M1 local/private, never auto-adopt credentials, and require an explicit
   encrypted export profile rather than silently changing the current archive.
4. **Windows and non-POSIX filesystems.** Platform owners must qualify ACL,
   link, case, Unicode, sparse-file, and atomic-publication behavior before M3/M5
   claims support. M1 may report the platform as unqualified and stop before
   extraction.

<a id="pending-m1-capacity-default-decision"></a>
### M1 capacity-default decision

**Status: approved for the existing local POSIX File/SQLite inert-inspection
profile.** On 2026-10-15, maintainer @huangruiteng [approved the capacity
proposal](https://github.com/loopx-project/loopx/pull/6279#issuecomment-6186241464) presented for revision
`5751b050f616f156fa591aac5aa82627d8396fe0`. The limits below remain unchanged at
`7f62b602019429c05a03254ab2f30a3fafa32c6e`. This closes only Section 12 item 1;
it does not grant merge authority or approve compound maximum loads, a production
SLO, another platform/provider, capture consistency, identity adoption or M2/M3
reactivation. The same comment requests checkpoint reuse assessment, recorded
in Section 3 below; that requirement is independent of the capacity consent.

The archive adapter, typed audit owner and disposable worker enforce the
current bounds; the [backup guide](../../reference/configuration-backup.md#measured-local-capacity-cases)
retains the original source, environment, measurements and reproduction command.
Those measurements are historical evidence, not measurements of a later merge.

| Approved existing value for this profile | What it protects | Available evidence and limit of that evidence |
| --- | --- | --- |
| Archive 128 MiB; expanded tar stream and regular-file total 512 MiB; expansion ratio 2,000 | Bound compressed input, copying and decompression amplification before publication | Guide: near-limit native inputs and over-limit refusals. Ratio is source-enforced; an independent exact-ratio boundary measurement is not recorded. |
| External and embedded manifest 16 MiB each; extended tar header 128 KiB each and 16 MiB cumulative; JSON depth 64 | Bound metadata decoding and traversal | Guide: external 16 MiB / one-byte-over cases; recovery tests reject oversized PAX variants and sparse records before decoding. Maximum structured metadata, cumulative-header and depth combinations remain unqualified. |
| 20,000 members; paths at most 64 components / 1,024 characters / 255 UTF-8 bytes per component; link resolution at most 40 hops | Bound inventory/path work and reject ambiguous or escaping extraction | Guide: exact 20,000 / 20,001 members; recovery tests cover path, link, collision and occupied-target refusals. Exact limits for every path shape and maximum link chains are not measured. |
| 128 Goals; 128 SQLite files; 20,000 committed transactions per Goal | Bound owner inventory and complete historical reads, without truncating history | Guide: native 128 / 129 inventories. `test_real_sqlite_history_limit_is_complete_and_overflow_refuses_publication` covers 20,000 / 20,001. This is not a guarantee that 128 maximum-history Goals fit the other bounds or deadline. |
| At most 258 emitted components and 128 dependency links in the current audit shape | Bound this profile's graph through the above inventories | Derived from one configuration component, up to 128 database components, up to 128 Goal components each linked to configuration, and one unknown remainder. This is not a general graph limit or a complete mutable-owner inventory; generic graph profiles remain outside this proposal. |
| Disposable owner verifier: 90 seconds; V8 old-space 512 MiB; SQLite process-wide heap at most 64 MiB | Contain database/owner inspection outside the shared daemon | Timeout cleanup and SQLite-view refusal tests exercise containment. Heap values are source-enforced; process-tree peak memory and worst-case timing are not qualified. The timeout is not an end-to-end archive-processing deadline or RTO. |
| Existing transport limits: 64 MiB private snapshot only for the two manifest checks; normal RPC and worker input 2 MiB; owner stdout 2 Mi characters; saved-audit input 16 MiB | Bound exchanges and historical report decoding without widening ordinary transport | Large-manifest transport and audit-integrity tests cover the path. These independent limits are not additive capacity or proof that every maximum-size audit is publishable. |
| Free destination space at least copied regular-file bytes plus 16 MiB before copying | Refuse a known-insufficient destination before publication | Source check plus failure-cleanup tests; concurrent disk consumption and a successful worst-case space reservation are not guaranteed. |

Source owners: [`state_recovery_io.py`](../../../loopx/control_plane/state_recovery_io.py),
[`state_recovery.ts`](../../../loopx/control_plane/state_recovery.ts), and
[`state_recovery_worker.ts`](../../../loopx/control_plane/state_recovery_worker.ts).
Existing [Python recovery cases](../../../tests/test_state_recovery.py) and
[typed audit cases](../../../tests/control_plane_ts/state_recovery.test.ts)
separate readability, rejection, cleanup and historical readback. No test count
substitutes for the maintainer decision. Actual production backup-size telemetry
and compound maximum-load qualification are not supplied by the synthetic guide.

The approved values retain the evidence limits above. Readable copied state
remains historical data: capacity consent cannot activate a Goal, confer a
current Goal-basis writer, restore a lease or authorize pending-effect replay.

---

## Appendix A: Execution ledger (non-normative)

No implementation entry exists yet. When a measured slice ships, add bilingual
entries under `ledger/complete-state-recovery-v0/` following the
[ledger contract](ledger/README.md).

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-10-08 | Propose inert-first, owner-composed complete state recovery | RFC review through issue #5940 | Raw extraction, monolithic transaction, manual-only runbook | Initial document |
| 2026-10-15 | Freeze unchanged M1 local POSIX File/SQLite inert-inspection capacities; reviewed proposal `5751b050f`, unchanged at `7f62b6020` | [@huangruiteng capacity consent](https://github.com/loopx-project/loopx/pull/6279#issuecomment-6186241464) | Raise limits, add overrides, or defer the local profile | §12.1; broader qualification and merge authority excluded |

The initial row records proposal history. The scoped capacity decision above
does not qualify the remaining recovery milestones.

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | Current full backup has plan/create but no complete restore | `82d1b837479dd5eb64b581bb0ca36d8c1ff1f0cc` | `loopx/state_backup.py`; `loopx/cli_commands/support_control_backup.py` | confirmed | Source audit only |
| E2 | Current restore tests prove extraction and SQLite readability, not reactivation | same baseline | `tests/test_state_backup.py` | confirmed | Synthetic local fixtures |
| E3 | Configuration recovery is intentionally isolated and inert | same baseline | `docs/reference/configuration-backup.md` | confirmed | Configuration owner only |
| E4 | Existing Goal/provider contracts require lifetime and incarnation fencing | same baseline | Related RFCs in the header | confirmed | Does not prove complete implementation |

## Appendix D: Rejected or superseded alternatives

The durable rejected alternatives are in section 6. Reopen one only with
evidence that it preserves every invariant in section 2 without creating a
second authority.

## Appendix E: Incident and review lessons

- A readable database is not a recoverable system.
- Byte equality cannot restore execution authority.
- Cross-owner recovery needs one bound plan and many owner receipts, not one
  guessed global transaction.
- Unknown external outcomes are a normal recovery state, not permission to
  retry under a fresh identity.
