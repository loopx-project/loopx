# Default cutover: reconciled implementation frontier

- Baseline: `41ba6f4d9` on `main`, 2026-09-25; open PR states are a snapshot, not merge promises.
- Owners: overall roadmap #4574 R5/G2; shared authority L2–L9/D1–D3; TS migration T1–T4.
- Delivered #5040: current registration admission, complete saved migration intent and truthful fence recovery.
- Current increment: long-history closeout reuse and TS-owned monitor evidence; the migration packages below remain open.
- This checkpoint supersedes numerical remaining-PR estimates in earlier delivery entries.

## Correct the accounting

The previous “5–8”, “6–8” and “7–9” numbers counted broad work packages as
remaining PRs, then retained the estimate after parts landed and additional
prerequisites appeared. They are not an audited PR backlog and are withdrawn.
A code gap, an open PR, integration acceptance, an elapsed-time qualification
and a maintainer cutover decision are different units. Do not add or decrement
them as though they were interchangeable PRs.

| Evidence at this baseline | Current disposition |
| --- | --- |
| #4870 claim-preserving writes; #4888 reviewed cutover; #4920 drain planning | Implemented. Exercise their combined head; do not commission replacements. |
| #4922 complete canonical snapshot pagination; #4960 qualified SQLite runtime admission; #4961 display refresh recovery; #4964 shared source summaries | Implemented. Consumer and packaged-client acceptance still needs integration evidence; a whole new pagination/recovery implementation is not pending. |
| #4967 typed complete-source assembly; #4968 native outbox delivery/recovery | Implemented. Complete-source transport is also merged in #5013; capture assembly is not missing. |
| #5003 atomic event-owned completion | Merged. Solves batch publication/retry, **not** the event writer's shadow-capture binding. |
| #4994 explicit leased Agent handoff; #4995 generated Monitor proof; #4991 rejected poll reservation; #4992 deferred receipt-bound Turn | Merged. Audit the integrated callers before deciding what remains; do not recreate them under a new caller-refactor PR. |
| #4931 retained SQLite proof encoding, contributor #4224 | Open optimization plus incomplete D2 qualification. A speedup is not capacity/recovery/soak acceptance. |
| #4915 default `.loopx` filesystem placement | Separate configuration migration; does not select File/SQLite authority. |

Only #4931 remains open among those implementation PRs; #4915 is separate
filesystem migration. #5011/#5012/#5013/#5014/#5016 are also merged; reuse their
transaction, complete-source and source-witness owners. The latest formal #4224
1 MiB report still fails receipt p95 (269.03 ms versus 50 ms) and scan-100 p95
(801.81 ms versus 250 ms). #4931 has not supplied a formal exact-head rerun.
Reaching the planned ten-day soak end date is not a passing report.

## Three concrete next code boundaries

This delivery repairs integrated migration admission: stale registry snapshots
could bootstrap a shadow and saved execution dropped migration policy. It does
not implement another store or close the whole migration package or D2 gate.

| Delivery boundary | Observable result and owner | Exit |
| --- | --- | --- |
| 1. External-effect execution fencing | Lease/effect owners protect the actual execution interval, takeover, timeout, exit and uncertain completion. Reuse merged #4994/#4995. | Stale executors cannot continue or settle; real executor and receipt recovery matrix passes. A point-in-time proof check is insufficient. |
| 2. Whole-Goal migration/return qualification | Managed event capture now joins the existing outbox, frozen mixed projection and native markerless recovery; real File/SQLite saved cutover, native reads/writes and isolated archive recovery are covered. | Finish the complete caller inventory and D3 cohort, including a fenced return after canonical writes. An archive restore creates an isolated copy; it does not reactivate legacy Markdown. |
| 3. Default entrypoints and bounded Python retirement | New Goals, settings, installation and packaged frontend/Lark/CLI select a qualified profile consistently; existing Goals have explicit migration/disable flows. | 1/2 and applicable D1–D3 pass; user entrypoints work; delete business writers only after their last callers migrate. Retain rendering, host IO and lawful import/export. |

**Two full implementation boundaries and the remaining integration/return work
of boundary 2 remain, plus #4931 and qualification evidence.** Newly discovered defects must
name their own repair and evidence, not reset an unchanged “5–8” estimate.
Bounded File opt-in, qualified SQLite default and all-existing-Goal migration
are separate acceptance scopes.

D2 capacity, crash/restore/upgrade/runtime coverage and **at least ten days of
natural elapsed soak** are evidence gates on an exact SQLite profile, not an
assumed one- or two-PR allocation. #4224 retains ownership. D3 integration and
owner-approved cohort cutover are also not automatically new PRs. No fixed
completion date or exact total PR count is defensible while these are open.
A File-only bounded cutover, a qualified SQLite default and migration of every
existing Goal have distinct acceptance scopes; none proves the other two.

PostgreSQL reuses the typed commands and AuthorityStore, while deployed
transport, authentication/tenant policy, restore identity, operations and
capacity qualification remain its separate medium-term path. Local default
does not wait for PostgreSQL deployment; a passing conformance suite does not
establish production service readiness.

## Long-history closeout: this repair and its remaining boundary

A live long-running lane lost the response to the five-second
`quota.prior_host_turn_closeout.preflight` query; later read-only inspection and
same-Turn retry recovered. Per-request indexing already exists. This repair
removes repeated JSON decoding across reads: always read fresh bytes and hash
the entire retained newline-terminated prefix before reuse; decode only appended
lines when it matches. Rewrites, truncation, replacement, malformed rows and
unfinished tails remain visible, as do conflicts in old Turns. Retain at most
four logs and prefixes representing 128 MiB of source bytes; oversized histories
use uncached parsing. This bounds retained source volume, not exact JS heap size.
The cache is disposable and introduces no durable index, format or authority.

Cold parsing yields between data batches to share the runtime event loop. Full
byte reads remain necessary: this is not constant-time arbitrary-history support
or D2 retention/capacity qualification. The five-second budget is unchanged. The
incident's transient process/host scheduling cause was not reproduced reliably;
validation establishes reduced duplicate work and concurrency headroom, not the
absence of every possible environmental timeout.

Monitor closeout now consumes the existing TS settlement rule for an exact
committed poll. Python no longer scans the run log a second time and adapts only
current Todo facts. Later uncommitted observations cannot hide earlier exact
commit evidence; foreign identities and wrong effects cannot settle a Turn.
A lost read-only preflight response reports `closeout_query_unavailable`, without
asking for a nonexistent preflight write receipt. Unknown queries remain closed;
there is no automatic mutation replay or shared-runtime restart.

This is an evidenced R1/R5/S7 liveness repair and bounded Python retirement, not
completion of implementation package 2. The three named boundaries above and
separate #4931/D2 evidence gates remain. Existing quota CLI/heartbeat entrypoints
adopt the change; no new setting or separate frontend/Lark policy is needed.

## Delivered #5013: complete-source transport and budget decision

Before #5013, `test_canonical_snapshot_integration` failed before provider
admission: complete source projection exceeds the 2 MiB request limit. Paging
canonical reads already exists, but source capture and management still send
whole projections. Trimming source records would invalidate digests and parity;
increasing the generic RPC budget would enlarge every method's exposure.

The existing coordination contract generates both schema names and the byte cap
for Python and TS. Python file exchange stays in its existing source projection
adapter; there is no independently maintained same-name Python/TS module pair.
Only source-bearing handlers accept a private host-local transfer envelope.
Python writes a temporary request; TS verifies its method, byte length, SHA-256,
regular-file identity and private directory, then invokes the same handler.
TS writes an exclusively created result and returns a small bound receipt;
Python verifies the response bytes and cleans up temporary files on both success
and failure. Inline callers remain compatible. These artifacts are transient
transport, not another authority store or durable business receipt.

The RPC limit remains 2 MiB. **The new artifact limit is 16 MiB per request or
result**, a separate explicit bound, not unlimited streaming or an assertion
that all goals fit. Oversize input rejects before execution. Result delivery can
fail after a mutation; callers must recover using the existing operation identity,
never infer that an RPC error means no commit. No automatic mutation retry is
added. Memory still includes complete parsed objects; this does not solve
arbitrarily large provider history or capacity qualification.

The cap accommodates the multi-megabyte complete-source fixture and source
management's repeated representation while keeping allocation bounded. In 32
interleaved warm calls of the same small source on the same host, inline versus
artifact median was 9.15/11.62 ms and p95 11.60/15.93 ms. This measured local IO
cost is accepted for complete-source calls; it is not a global latency claim.
Future size changes require a measured workload and the existing budget review.

Validation uses real File/SQLite and a disposable PostgreSQL 16 server. The
large CLI regression qualifies and promotes only a synthetic isolated Goal.
A live source rehearsal that detected concurrent changes was discarded; the
accepted real-source rehearsal verifies a detached copy against its capture
witness and exercises all mutations there. Private sources, identifiers and
raw output are excluded from public artifacts. No active Goal is promoted.

No frontend settings or API shape changes are needed: the same CLI and Python
management adapters invoke the same domain handlers and return the same results.
The public change is that supported complete-source operations no longer fail
solely because their source crosses the RPC envelope. Defaults, authorization,
source freshness, event-writer holds and provider promotion criteria are unchanged.

A related runtime repair handles socket errors when a caller closes an oversized
response before draining it. One disconnected caller no longer crashes the
shared runtime; the regression asserts that subsequent paged reads retain the
same process identity. It neither cancels nor retries the business operation.

## Managed event capture and its remaining boundary

`StateEventWriteContext` binds Goal event writes to the registry, source paths
and existing Todo/state/event locks. Completion and supervisor CLI source writes
use it; standalone `AppendOnlyStateEventStore(path)` remains an unmanaged codec
and IO API, like an external Markdown edit. Such writes cannot certify a Goal
transaction and invalidate candidate parity if they change the projection.
A supervisor log outside the Goal source candidates remains independent.

The existing immutable bootstrap manifest now records all event candidates,
including absent ones. A pre-upgrade binding must be explicitly rolled back and
bootstrapped again before capturing events; changing an alias requires the same
reviewed rebootstrap. A durable prepare failure preserves primary bytes. A
markerless transaction must be drained before later writes, including semantic
no-ops; recovery holds the actual event source lock and establishes durability.

The same snapshot projector serves bootstrap and capture instead of a separate
Markdown-only assembly. The source kind is a TypeScript discriminated union:
an event source requires its bound log path; Markdown/lease sources cannot carry
one. Persisted Python event bytes/fingerprints remain unchanged.

Validation uses real File/SQLite CLI migration, process death before and after
publication, exact replay, mixed sources, source drift and isolated recovery.
No provider default changes, PostgreSQL behavior, active Goal promotion or
external-effect execution fencing are included. CLI and its shared Todo backend
change; no settings or frontend configuration is introduced. The UI continues
to consume the existing Todo result and projection contracts.
