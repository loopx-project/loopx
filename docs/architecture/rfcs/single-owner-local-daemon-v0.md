# RFC: Single-Owner Local Daemon (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal
- **Authors / owners:** Contributor proposal; lifecycle decisions require maintainer acceptance.
- **Created:** 2026-09-07
- **Last normative revision:** 2026-09-07
- **Implementation baseline:** `b8670eb5`
- **Related issue:** [#3930](https://github.com/huangruiteng/loopx/issues/3930)
- **Related contracts:** [Desktop execution frontends](desktop-execution-frontends-v0.md),
  [current Desktop runtime](../../../apps/desktop/loopx-control-plane/README.md)

## Document map and maintenance contract

Sections 1-11 propose the design and acceptance contract. Section 4 records
current implementation facts. Section 12 lists decisions requiring approval;
recommendations are not accepted decisions. The appendices record evidence and
approval independently. No endpoint or configuration in this proposal is a
shipped interface, and merging this document does not enable a daemon.

## 1. Decision summary

Give each explicitly configured local service profile one durable lifecycle
owner: the OS user-service manager. Desktop and CLI request operations through
a common lifecycle boundary and attach only after identity and readiness checks.

Recommend a first `loopxd` implementation that supervises the existing status
and Chat components as bounded children. Preserve their existing loopback ports
and API semantics while qualifying failure isolation and update recovery.
In-process composition is a later decision, not a prerequisite for one owner.

Activation is explicit per profile. Existing installations retain their current
behavior until migration succeeds. Daemon residency grants no Goal, Todo,
quota, gate, evidence, session, or execution authority.

## 2. Problem and motivation

A package update can succeed while a stale listener still owns the Chat port.
Likewise, an open Desktop window does not establish that its services use the
selected registry or host context. Independently managed services allow runtime
identity, configuration, and readiness to diverge.

Required invariants:

- At most one managed service composition may own a profile at a time.
- A port, PID, HTTP success, or release label alone cannot establish identity.
- A lifecycle operation reports success only after the requested state is read back.
- A failed Chat component cannot make status falsely appear unavailable or healthy.
- Recovery cannot silently attach to another profile or take over a foreign listener.
- Closing Desktop does not stop a configured managed service.

## 3. Scope and non-goals

This proposal covers service identity, discovery, readiness, lifecycle receipts,
component composition, and migration of the two local services. The nearest
owner is local service lifecycle, currently adapted by Desktop; no new product
capability or extension is proposed. Platform service managers are lifecycle
adapters, not work providers.

It does not introduce an executor, move domain truth into daemon memory, merge
host session stores, expose non-loopback APIs, or authorize arbitrary commands.
Config/update APIs and managed Agent runtimes require separate accepted slices.

## 4. Current-system contract

At the baseline, Desktop's
[`services.rs`](../../../apps/desktop/loopx-control-plane/src-tauri/src/services.rs)
maps status and Chat to `com.loopx.status` and `com.loopx.chat`. On macOS,
`request_platform_managed_start` checks the relevant LaunchAgent and requests
`launchctl kickstart` when it is loaded. Desktop tracks separately owned
children and stops those children on exit.

The [Desktop runtime documentation](../../../apps/desktop/loopx-control-plane/README.md)
describes release fingerprints, foreign-listener rejection, and ports 8766 and
8767. [#3931](https://github.com/huangruiteng/loopx/pull/3931) repaired the
immediate startup/double-owner path while explicitly preserving two services.
These facts do not establish the profile or unified-daemon contract below.

## 5. Proposed architecture

### Ownership and identity

```text
OS user-service manager
  loopxd (one configured profile, one composition generation)
    status component -> existing status API
    Chat component   -> existing Chat API
Desktop / CLI -> authorized lifecycle request + identity/readiness readback
```

A durable, owner-controlled profile has an opaque `profile_id` and a versioned
configuration binding registry identity, runtime-root identity, host-context
identity, release channel, and configured loopback endpoints. Local canonical
paths are resolved in protected configuration; public diagnostics use opaque
references, not path hashes advertised as anonymization. Aliases of the same
resolved resources must not acquire independent ownership locks.

Keep stable profile identity separate from deployment identity. An upgrade
changes `release_id`, `config_generation`, and `instance_id` as applicable, not
the profile id. An immutable release digest identifies installed artifacts;
`instance_id` is fresh for each daemon start. Discovery compares the expected
profile/configuration and release against the running instance.

The service manager owns process lifetime; a lifetime-held OS lock on the
canonical profile prevents duplicate daemon instances, including manual starts.
Profile provisioning must also reject overlapping registry/runtime ownership
under different profile ids. Port binding remains a separate exclusion check.
Neither a lock-file's existence nor stale PID metadata proves a live owner.

Children inherit explicit profile and release bindings. They have no independent
LaunchAgent and cannot detach or self-relaunch. The platform adapter must prove
child containment after supervisor death before promotion; a process group alone
is not sufficient proof on every platform.

Managed team supervision must also respect the
[session continuation owner](agent-session-execution-modes-v0.md#reusable-agent-operations-and-continuation-ownership).
One service-profile process owner does not prove one executor per Agent binding;
the session/work owners still enforce their own identity, admission and fences.
A daemon may deliver admitted work, wake an eligible session, drain queues and
recover receipts. It must not encode a team's business phases or compete with
an active native Goal/same-session driver for the next execution opportunity.
Local and cloud host adapters reuse this composition rather than installing a
manager-specific daemon. Readiness must distinguish service health from a
worker's launchability, pending tool, deferred work and terminal result.

### Read contract

The proposed control routes are served on the configured status origin, initially
8766. The supervisor must own that listener for readiness to remain available
when the status worker fails; a later implementation must route existing status
handlers through that owner without changing their public responses. It must
not bind a second listener to the same port.

| Route | Proposed semantics |
| --- | --- |
| `GET /health` | Supervisor liveness only; not readiness or identity acceptance. |
| `GET /capabilities` | Versioned profile, release, instance and configuration identities plus supported surfaces. |
| `GET /ready` | One snapshot with the same identities and per-component readiness. |

These are proposed additive routes, subject to route-collision review in M1.
The versioned envelope rejects unsupported versions and missing required fields;
unknown additive fields may be ignored, unknown state values may not.

Each component reports `starting`, `ready`, `degraded`, `failed`, or `disabled`,
with a bounded reason code and its observed release identity. Overall readiness
is true only when every profile-required component is ready and identity-matched.
Disabled required components are configuration errors. Optional components may
be degraded without blocking an unrelated surface. Clients select their required
surfaces explicitly: Chat failure must not prevent a healthy status view.

Readiness observations expire under a bounded client deadline and must be
re-probed after reconnect or an instance/configuration change. Missing delivery
does not mean that a mutation failed. Existing reads retain their authorization
and projection filtering; an identity response is not authentication.

### Lifecycle operations and recovery

Use one versioned local operation contract for install/start/reload/stop/rollback.
An authenticated local adapter checks explicit profile authorization and an
expected `config_generation` before effects. It serializes mutations per profile.
Desktop cannot substitute direct spawning when this adapter is unavailable.

A durable intent contains `operation_id`, request digest, profile id, expected
generation, target release, previous deployment reference, and operation kind.
Record it before changing service-manager configuration. Same-id/same-request
replay returns the recorded operation; same-id/different-request fails with
`operation_conflict`. Concurrent different operations use generation fencing.

Legal phases are `prepared -> applying -> verifying -> succeeded`, with
`failed` or `reconciliation_required` for unsuccessful/ambiguous outcomes.
Success includes observed instance, release and configuration identity. An
intent journal stores lifecycle facts only, not work-domain truth.

After a crash, reconcile service-manager registration, actual listener ownership
and runtime identity before retrying an effect. Never infer that the effect did
not happen from an absent success record. Terminal receipts are immutable;
rollback is a separately identified operation linked to the failed update.
For stop, readback means manager restart is disabled for that composition,
owned children have exited, and its listeners are gone.

## 6. Alternatives and design choices

| Option | Benefit | Cost / recommendation |
| --- | --- | --- |
| Directly embed both APIs | Fewer processes and no worker proxy | Couples server shutdown and failure domains; defer until measured parity. |
| Thin supervised composition | Reuses existing components and isolates failures | Requires proven orphan cleanup and status routing; recommended first milestone. |
| Keep two independent jobs | Lowest immediate migration cost | Does not establish one composition/update owner; current compatibility state only. |

The supervisor does not independently restart itself; the OS manager does.
Worker restart budgets are bounded per component with backoff. Exhaustion leaves
an observable failed component instead of an unbounded restart loop.

## 7. Safety, privacy, and compatibility

Loopback binding does not authorize browser-origin writes. Preserve existing
read/write capability checks, origin restrictions and explicit write opt-ins.
The bootstrap lifecycle adapter must authenticate the local caller even when
the daemon is absent; no general shell-command HTTP endpoint is introduced.

The discovery/configuration store and operation journal require owner-only
access. Public receipts exclude credentials, prompts, session content, raw
paths and provider-private payloads. Config remains durable machine policy or
Goal-scoped domain state; the daemon only resolves the existing effective view.

Mixed-version clients reject unsupported contracts without taking over ports.
Unmanaged source launches retain their explicit development mode. They cannot
be used as an automatic fallback for a selected managed profile.

## 8. Migration and rollback

1. Preflight the selected profile, exact old/new releases, manager permissions,
   endpoint ownership, child-containment support and rollback compatibility.
2. Persist migration intent and previous service definitions. Serialize concurrent
   Desktop/update requests; recheck generation immediately before cutover.
3. Quiesce incoming mutations through existing admission/drain semantics. If a
   safe bounded drain cannot be demonstrated, abort before disabling old jobs.
4. Disable old automatic restarts, stop verified old components and read back
   their exit. Never start the new composition while the old owner may restart.
5. Register/start the new composition through the OS manager. Verify exact
   release/profile/configuration and required-component readiness before success.
6. On failure, reconcile any ambiguous effect, then stop and verify the new
   composition before restoring the previous definitions and pinned release.
   Read back the old composition before reporting rollback success.

Retain rollback definitions until qualification completes. A foreign listener,
unknown owner or incompatible persistent schema requires operator action; never
kill by port number or overwrite state to force progress. First composition
delivery must not migrate Goal/Todo schemas. Later irreversible schema changes
need a separate export/migration gate and cannot claim automatic downgrade.

## 9. Validation and acceptance

All rows below are required future evidence, currently **unverified**. Use
synthetic profiles and disposable service-manager registrations, not live Goals.

| Claim | Test / evidence | Required result | Boundary |
| --- | --- | --- | --- |
| Identity isolation | Same port, wrong profile/context/release and alias-path cases | Typed mismatch; zero takeover | Contract tests plus real listeners |
| Single owner | Concurrent Desktop launches and direct duplicate daemon start | One composition; shared readback | Real manager and OS lock |
| Ready attach | Cold boot, existing ready instance, close/reopen | Bounded readiness; managed service survives UI exit | Packaged and source install |
| Partial failure | Kill Chat worker while querying status | Chat failure visible; status remains usable | Real component failure |
| Crash containment | Kill supervisor during worker execution | No orphan listener; replacement has one owner | Each supported OS adapter |
| Update atomicity | Interrupt every intent/cutover/readback phase | New exact release or verified rollback; no double owner | Disposable old/new releases |
| Replay and concurrency | Lost responses, repeated operation ids, stale generation | No duplicate effect; mismatch conflicts | Durable journal restart |
| Foreign owner | Bind unrelated process to configured endpoint | Actionable error; process untouched | Real listener |
| Authorization | Unauthorized lifecycle/config requests and browser-origin writes | No effect or sensitive projection | Existing permission fences |
| Compatibility | Existing status/Chat consumers, feature absent, legacy install | Existing API/default behavior preserved | No daemon promotion implied |

Deterministic tests alone do not qualify install/update behavior. macOS promotion
requires actual launchd tests; other platforms remain explicitly unsupported
for managed mode until equivalent adapter evidence exists. No performance gain
is claimed without matched boot/attach measurements.

## 10. Operational contract

Bound component startup, shutdown, probe deadlines, restart budgets and journal
retention through versioned profile policy. Report stable reasons including
`profile_mismatch`, `release_mismatch`, `foreign_listener`, `manager_unavailable`,
`component_failed`, `operation_conflict`, and `reconciliation_required`.

Desktop shows progress during bounded repair and an actionable error on timeout.
CLI update distinguishes package installation from runtime activation. Readiness
and operation receipts are independently inspectable without a working Chat UI.
Retention must not silently erase replay protection for accepted operations;
expired operation identities are rejected or retained as bounded tombstones
under a documented policy before journal compaction is enabled.

## 11. Normative delivery plan

| Milestone | Deliverable | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | This RFC and explicit owner decisions | #3930 | Accepted identity/ownership/migration design | Documentation revert |
| M1 | Identity/readiness on a real existing consumer path | Accepted route and profile contracts | Identity, partial-readiness and off-parity tests | Remove opt-in integration |
| M2 | Opt-in composition and migration | Accepted containment/bootstrap design | Real manager, crash, concurrent launch, update/rollback rows | Verified old jobs and pinned release |
| M3 | Typed config/update coordination | M2 operational evidence and authorization review | Authorized effects with exact readback | Versioned adapter rollback |
| M4 | Retire dual jobs; evaluate in-process APIs/runtime supervision | All relevant platform/install rows qualified | Removal and compatibility evidence | Explicit supported migration boundary |

Do not add uncalled runtime schema builders in M0. M1 must bind a real consumer;
its final module placement follows that existing owner. M2 must preserve current
domain authority and may not launch managed Agent runtimes as a side effect.

## 12. Open decisions

1. **Composition:** lifecycle maintainer chooses embedded APIs or contained
   children before M2. Recommend children; require failure-injection and status
   routing evidence before approval.
2. **Profile/bootstrap contract:** lifecycle and configuration owners choose
   canonical identity storage, authenticated local transport and endpoint
   compatibility before M1. Require alias/overlap and wrong-context fixtures.
3. **Platform qualification:** platform owners select containment and service
   registration primitives before M2. Recommend macOS first, with explicit
   unsupported managed-mode results elsewhere until separately qualified.
4. **Operational limits:** lifecycle maintainer approves concrete timeout,
   restart and replay-retention bounds before M2; require measured cold boot,
   unavailable-provider and prolonged-retry cases.

## Appendix A: Execution ledger (non-normative)

2026-09-07: design-only proposal grounded in `b8670eb5` and #3931. No daemon,
service migration, route, or operational qualification is delivered here.

## Appendix B: Decision log

No maintainer approval recorded. Recommendations remain proposals.

## Appendix C: Evidence registry

| Evidence | Claim | Baseline / artifact | Result | Boundary |
| --- | --- | --- | --- | --- |
| E1 | Two current LaunchAgent labels and managed wake path | `b8670eb5`, Desktop `services.rs` | Source inspected | Not unified-daemon qualification |
| E2 | Immediate startup repair preserves two services | PR #3931 | Merged implementation description | Does not satisfy section 9 |

## Appendix D: Deferred alternatives

In-process composition remains open after worker-boundary measurements. Keeping
two independent restart owners is not a valid final migration outcome.

## Appendix E: Generalized lesson

Successful installation and a responding port are insufficient evidence of
successful activation. Lifecycle success must bind the observed runtime to the
requested profile, configuration and release.
