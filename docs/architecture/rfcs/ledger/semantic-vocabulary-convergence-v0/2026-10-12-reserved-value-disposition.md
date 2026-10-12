# Reserved settlement input and custom decision-scope disposition

Source baseline: `323c3053128e41e16df27e6b6498f83c37e81f5e`.
Refs [#4447](https://github.com/loopx-project/loopx/issues/4447), original
Stage 2a/2b source treatment and Stage 3 owner disposition. This is a concrete
proposal for review and integration, not a record of an earlier maintainer
approval. It does not close the issue or replace the 26-entry inventory in
[#6270](https://github.com/loopx-project/loopx/pull/6270).

## `settlement_failure_kind.cancelled`

Keep `cancelled` as a reader-reserved wire value in the existing vocabulary.
The Python and TypeScript decoders already admit it. Current built-in settlement
branches do not produce it; cancellation in Host, chat, lease or usage contracts
must not be counted as its producer. No new runtime cancellation mechanism is
introduced to make the value appear live.

The existing `compatibility_only` metadata now states why that admitted input
is retained and the exit condition. This is a compatibility commitment, not
proof that any historical producer emitted the value. A supplied `cancelled`
envelope remains a failure: bind must stop before the next callback and preserve
prior receipts and failure details. It neither erases an already committed
step nor proves an in-flight provider effect stopped. It grants no automatic
retry, rollback, resume or quota authority. A provider outcome that is actually
unknown continues to use its owning observation/reconciliation contract;
`cancelled` cannot settle that uncertainty by relabeling it.

Replace the reservation only through one of these owner-reviewed changes:

- a real typed cancellation contract that distinguishes before-dispatch
  cancellation from ambiguous after-dispatch effects, with producer and real
  caller witnesses plus updated source treatment; or
- explicit versioned reader migration/removal with caller and stored-envelope
  compatibility evidence.

There is no automatic expiry, invented external canceller, decoder narrowing,
or forced vocabulary retirement. F1/F2 remains the existing six-entry domain.

## `todo_decision_scope_kind.other`

`other` is explicitly authored custom decision authority when none of the six
named kinds describes the actual approval. Prefer a named kind whenever it
applies; uncertainty about the intended decision is not a reason to select
`other`. The existing task text explains the decision and a stable public-safe
scope key identifies it for both gate and dependent work. See the
[authoring rule and example](../../../../reference/protocols/decision-scope-v0.md#choosing-kindother).

For example, `other:action:study_conflict_review` may name an owner-required
conflict-of-interest review. It is independent of private source access,
resource spend, direction changes or public publication, which keep their
named kinds. `other:action:study_method_review` is another custom decision,
not a synonym or fallback.

Matching uses the same typed owner as every other kind. Exact kind is always
required, so even `other:global:*` cannot cover a named kind. Key, granularity,
recipient and exact Todo target rules remain intact. Unknown/misspelled kinds
still reject; no parser fallback converts them to `other`. Declaring a kind is
not approval: existing linked-gate completion or standing authority consumes
only the scopes it actually covers.

## Evidence and remaining acceptance

Reuse the existing all-value settlement decoder, unknown-input and bind
short-circuit witnesses. Extend their actual IPC/decoder path to preserve a
supplied reserved failure with prior committed receipts and details; this is
reader coverage, never internal producer evidence. Reuse existing Todo
lifecycle helpers and native TypeScript matching to exercise a custom approval
alongside a different custom requirement and a named permission requirement.
Only the selected custom scope may be consumed. A broad custom scope cannot
cover any named kind, and malformed input cannot become custom authority.

The registry still has 26 entries and the same values/owners. No budgets,
formal proof domains, wire schemas, runtime defaults or persisted formats are
changed. Tests alone do not approve these dispositions. Final #4447 acceptance
still requires owner review/integration of this proposal, the current source
inventory, and passed/failed/untested evidence on the final integrated revision.
Earlier records of unresolved meaning remain historical; they are not rewritten
as if this decision had already existed.
