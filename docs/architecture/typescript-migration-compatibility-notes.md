# TypeScript Control-Plane Migration: Compatibility Notes

- Status: review notes against `main` at `bf217e1e` (2026-09-06)
- Source RFC: [TypeScript Control-Plane Migration Direction
  v0](./rfcs/typescript-control-plane-migration-v0.md) (#3225, #3226)
- Purpose: a compatibility-completeness review of the RFC's four contract
  surfaces — typed state rules, domain neutrality, behavior-change disclosure,
  and the public/private boundary — checked against merged reality. These notes
  do not amend the RFC and do not start new migration work.

## How to read these notes

The RFC now carries three unsynchronized time layers: the top
"Current implementation checkpoint", the Stage 2B status lists in Section 4,
and the Section 3.1 baseline table. Merged reality has moved fastest. These
notes are the single cross-checked snapshot; each claim below cites the RFC
section and the merged commit or PR it was verified against.

## 1. Typed state rules: status and constraints

RFC Section 2.5 requires that every trust boundary (RPC payloads, parsed JSON,
persisted state, extension input, adapter responses) decode through a typed
decoder before a domain handler consumes it, and that `as unknown as T` is
permitted only as a named migration seam with visible removal ownership
(RFC Section 6, correctness gates).

Verified status on `main`:

- The shared primitive runtime decoders shipped first (#3443) and remain one
  small module; domain decoders stay local, honoring the Section 5 constraint
  against a generic schema framework.
- A checked-in generator now validates the language-neutral contracts and
  emits deeply immutable Python/TypeScript bindings — including the native
  domain and projection sections — with CI rejecting stale generated files.
  The coordination path uses one versioned
  `coordination_state_contract_v0.json`; its `TodoDomainRecord` and
  `TodoProjectionMetadata` keep task semantics (`archive_state`,
  `source_section`, optional `index`).
- A product-source scan of all 70 `loopx/**/*.ts` files found zero
  `as unknown as` casts and one `JSON.parse(...) as T` assertion:
  `loopx/control_plane/quota/void_commit.ts:259` — the internal
  `cloneObject` helper, which round-trips a value that is already a
  validated `JsonObject` through `JSON.parse(JSON.stringify(value))` to
  get a deep copy and casts the result back. This is not an external
  trust boundary: the input has already passed the module's decoders, so
  the assertion covers an internal clone rather than untrusted input.
  It stays in the seam inventory below with a removal owner; any new
  occurrence must re-enter the same inventory with negative coverage
  and a removal owner.

  Reproducible scan (run from the repository root against a pinned
  snapshot):

  ```sh
  fd -e ts . loopx -x rg -n "as unknown as" {}
  fd -e ts . loopx -x rg -n "JSON\.parse\(.*\) as " {}
  ```

  Named seam inventory (against `main@bf217e1e`):
  `quota/void_commit.ts:259` — internal clone assertion inside the
  void-commit runtime, merged in #3832; negative coverage via the
  module's own decoder tests on the pre-clone value; removal owner is
  the quota/void-commit migration slice (replace the cast with a typed
  clone or generator-emitted copy helper).

Constraints these notes restate as invariants for later slices: decoding
happens once at the boundary (not repeated ad hoc inside the domain), the
schema-split is not permission to drop v0 provenance or change legacy
ordering, and no domain passes its promotion gate while public, persisted,
RPC, or extension input reaches its semantic core through an unvalidated
assertion.

## 2. Domain neutrality boundary

RFC Section 2.3 keeps external authorities — model calls, human gates, host
schedulers, credentials, third-party mutations — as explicit adapters whose
receipts return to the Effect Program, and forbids hiding them behind a
universal executor. Section 3.2 caps a migrating transaction at two
cross-runtime calls while a real provider remains Python.

Verified status: every merged cutover holds this shape. Turn settlement
(#3464) uses one fail-closed preflight plus one final reduction around the
Python provider, and one reduction on replay. Quota monitor-poll (#3715)
keeps the Todo writeback as an explicit idempotent Python provider between
one preflight and one final reduction. Task-lease acquire (#3702) leaves the
per-goal lock, owner eligibility, and lease-file durability with the atomic
Python provider. The host automation adapter and its TOML/SQLite writes
intentionally remain Python and external to the heartbeat/state transaction.
Vision refresh (#3720) stays a separate refresh/writeback transaction because
it does not share the delivery-selection lifecycle phase. The
provider-neutral coordination executor reaches the same pure TypeScript
decisions through typed Python adapters rather than a provider-specific path.

No merged slice turned the transport shell into a second business owner:
Python facades retained after cutover are transport, compact fact projection,
and explicit adapters only, each with a named facade-exit condition.

## 3. Disclosed behavior changes: RFC promises vs merged reality

RFC Section 6 requires that a pinned behavior contradicting an independently
reviewed invariant be disclosed and separately approved. The review found no
undisclosed behavior change. The disclosed list, as merged:

| Behavior change | Where disclosed | Merged as |
| --- | --- | --- |
| Delivery semantics become typed-only; keyword inference deleted; status, handoff/review, and quota decisions previously derived from untyped labels intentionally change; narrative non-interference is the acceptance invariant | RFC "Delivery semantics: correctness before migration" | #3957 (typed delivery semantics, 2026-09-05) |
| Malformed quota run-index rows fail closed instead of being skipped; void artifact names include an effect digest | RFC Section 4, void-commit economics | #3832 |
| Claim retry identity: exact operation retries recover the original claim receipt before current-state eligibility; a structurally valid empty registration list permits historical replay but never a fresh claim; successful non-preview `no_change` persists a terminal receipt under head CAS (storage revision may advance; Todo state, `updated_at`, and domain events do not); replaying a receipt renews no lease | RFC "Current implementation checkpoint" | #3945, #3972 |
| Promoted `todo add` is a native create transaction: new claims require active, open Todos and current actor/lease checks; promotion cannot silently regain a Markdown write path (tested after deleting the Markdown state file); terminal status becomes authority and terminal semantic duplicates are ignored | RFC checkpoint, next replacement slice | #3973 (plus follow-up fixes at `b65ca1f1`, `617b7dbb`) |
| Promoted `todo` text/note edit routes through the unified provider CAS | RFC Section 4 transaction family | #3963 |
| Void full-CLI p95 +27.81 ms (+3.36%), disclosed as the measured cost of one new managed-runtime fingerprint plus prepared-receipt durability, inside the 5% material-regression gate | RFC void-commit economics | #3832 |
| Monitor-poll earlier owner-review hold resolved: Todo-write p95 −93.31 ms (−9.61%) after replacing the prepared-plus-staged receipt sequence with one conservative prepared WAL | RFC monitor-poll economics | #3715 |

The performance gates themselves remain as promised: warm non-durable internal
transitions below 2 ms p95, durable transactions compared against a matched
durability baseline, and any greater-than-5% or unexplained 25 ms additive
full-CLI p95 regression is an owner review gate rather than a silent pass.

## 4. Public/private boundary evolution

RFC Section 7 projects only `running`/`stopped`/`unavailable` plus a stable
diagnostic code; raw stderr, tokens, local paths, and private runtime metadata
must not be projected. Node.js 22.22.3+ detection with exact remediation is
required before normal control-plane work.

Verified status: `loopx check` runs a public-boundary scan over the RFC and
the contributor board and reports clean. The per-transaction migration
economics receipts (LOC deltas, crossing counts, p50/p95 latency, daemon RSS)
demonstrate the public-safe evidence pattern: measurements without private
paths, task ids, or raw transcripts. The [public/private boundary
contract](../public-private-boundary.md) remains the fixture-level rule, and
the shared-authority RFC's non-normative Section 1.3 license split keeps
schemas, typed commands, receipts, provider-neutral decisions, conformance
fixtures, and examples in the Apache-2.0 open core; nothing in the merged
TypeScript work changes that distribution boundary.

The RFC's bilingual mirror clause (a difference between the English and
Chinese versions is a defect) was spot-checked: both revisions carry the same
2026-09-05 date, the Stage 2B cutover list, and all migration-economics
tables including monitor-poll.

## 5. Stage status ledger (RFC vs `main`)

| Stage | RFC statement | Verified on `main` |
| --- | --- | --- |
| 0 — pin behavior/authority | complete, repeated per transaction | Holds; each economics receipt restates the pinned baseline |
| 1 — Effect Program, managed runtime (#3416) | shipped | Confirmed; Stage 1 settlement-facade cleanup complete |
| 2A — bounded rule-owner proofs (#3431–#3434, #3440) | shipped, pattern not to be repeated | Confirmed |
| 2B — complete transaction cutovers | active | Sixteen merged families (below) |
| 3 — CLI/App convergence | first bounded slice (receipt-bound scheduler ACK/failure native route) | Confirmed as `heartbeat_followup_cli.ts`; broader Stage 3 pending |
| 4 — distribution cleanup | not started | Confirmed |

Merged Stage 2B transaction families verified in history: Turn settlement
(#3464), quota delivery routing (#3481), Todo completion (#3530), scheduler
heartbeat/state, quota spend commit, quota void commit (#3832), quota
monitor-poll (#3715), task-lease acquire (#3702), task-lease lifecycle
(#3806), governed capability lifecycle validation (#3706), post-writeback
hooks (#3847), host Todo settlement (#3724), vision refresh (#3720),
promoted-authority Todo claim (#3945, #3972), promoted Todo text/note edit
(#3963), and promoted native create (#3973, `main` HEAD). The adjacent
shared-authority track (Stage 2C promotion design #3909, transaction-bound
shadow outbox, alignment projection stage 1 #3874, NoKV AuthorityStore
candidate #3819) proceeds on its own RFC and is not counted here.

In flight and not yet on `main`: the provider-first native update cutover
(#3974) was queued with green CI at review time.

## 6. Compatibility gap ledger

1. RFC Section 4's Stage 2B opening sentence names seven cutovers; its
   "Current implementation status" list names nine; Section 3.1's baseline
   table is an older third layer. Sixteen families are merged. The RFC is
   accurate per layer but no single layer is complete — readers must
   cross-check as these notes do.
2. Vision refresh (#3720) and host Todo settlement (#3724) are merged but are
   not named in any RFC status list; vision appears only as a "separate
   transaction" exclusion note.
3. The checkpoint section (claim, create) is the freshest layer and correctly
   states that this is a contract checkpoint, not a completed CLI lifecycle
   cutover: default unpromoted goals still use the legacy Markdown
   transaction until explicit promotion.
4. No undisclosed behavior change and no domain-neutrality violation were
   found; the two standing risks are the accumulation of facade-exit
   conditions (each named, none yet fully due) and the bilingual mirror
   obligation as checkpoint edits accelerate.

## 7. Checklist for the next migration slice

- Delete the replaced Python semantic path in the same PR and name the
  facade-exit condition (RFC Sections 3.2, 5).
- Decode at the boundary; keep `as unknown as` at zero or inventory it.
- Keep external providers as explicit adapters; at most one preflight plus
  one final reduction while a real provider remains Python.
- Disclose any behavior change against a stated invariant in the PR and the
  RFC checkpoint; report the economics receipt with the Section 5 fields.
- Run the public-boundary scan over touched docs and keep economics evidence
  public-safe.
- Update the English and Chinese RFC revisions together.

## References

- [Migration RFC v0 (English)](./rfcs/typescript-control-plane-migration-v0.md)
- [Migration RFC v0 (Chinese mirror)](./rfcs/typescript-control-plane-migration-v0.zh-CN.md)
- [Shared goal authority and state provider RFC](./rfcs/shared-goal-authority-state-provider-v0.md)
- [Public/private boundary](../public-private-boundary.md)
- [Documentation layout policy](../development/documentation-layout.md)
- [Contributor task board](../development/contributor-tasks.md)
