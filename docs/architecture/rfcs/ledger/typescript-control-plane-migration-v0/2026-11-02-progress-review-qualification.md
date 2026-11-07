# Progress-review receipt qualification (2026-11-02)

## Owner boundary

This checkpoint moves the complete loaded-receipt qualification decision from
`capabilities/progress_review/context.py` to
`control_plane/work_items/progress_review_evidence.ts`, exposed through one
`progress_review.context.qualify` request. The existing built-in progress-review
capability retains its provider boundary; this adds no capability or provider.
The TS owner composes `inspectLocalGoalAcceptance` and
`progressReviewCriterionBasis` in process, caches each Todo inspection for the
batch, compares canonical bindings, withdraws obsolete signals, selects the
newest revision from the first qualified receipt, and applies the manual pin.
The Python loader's `progress_review_receipt_order_key` is the single ordering
owner: its stable newest-first output also preserves first-maximal tie behavior.
TS preserves that input order rather than sorting again.
These are successive reads, **not an atomic snapshot**. One unavailable Todo
cannot suppress other Todos. Exact Goal-instance refusal remains with the
acceptance owner. Operator studies and legacy receipts retain their semantics.

## Economics and retained Python

Instrumentation on both File and SQLite measures context-read crossings:

| Scenario | Before | After |
| --- | ---: | ---: |
| Off / no receipts / unavailable receipt store | 0 | 0 |
| Repeated canonical receipts for one Todo | 5 | 3 |
| Canonical receipts for two Todos | 6 | 3 |
| Removed criterion / newer receipt outside pin | 3 | 2 |
| Unavailable Todo plus current Todo | 5 | 3 |
| Wrong Goal instance | 2 | 2 |
| Operator study plus legacy receipt | 1 | 2 |

The general qualification boundary changes E + T + R to E + 1; E still
counts per-receipt evidence-scope decoding during receipt IO. Off and empty
paths do not call the batch. Manual/legacy-only batches and refused-instance
batches do not necessarily save crossings. No latency or long-horizon benefit
is claimed. The manual/legacy case rises from 1 to 2 because evidence-scope
IO still costs one call and every nonempty batch adds qualification. With no
`goal_acceptance` binding and no pin, no TS qualification call is semantically
needed; this slice reports that waste rather than duplicating the TS rule in
Python to bypass it. Product code changes by +111/−53 physical lines, net +58.
The Python context adapter adds 32 lines of transport/codec composition while
removing 50; handler registration adds one bridge line. The bounded net
increase buys one complete decision owner rather than another leaf facade.

Python deletes the Todo cache, acceptance inspection orchestration, criterion
comparison, stale rewriting, newest selection and pin filtering. It retains
receipt file IO and persisted-format/signal codecs, policy decoding, read-error
projection and summary composition for existing status, attention, refresh and
Chat callers. Selected source indices let the transport retain historical
numeric facts (integral floats and large integers) without repeating decisions.
The response omits the unused qualified array and computes selected source
indices directly. Sequence tokens and BigInt ordering are removed; arbitrary
integer ordering remains in the Python loader's single ordering owner. The acceptance
inspection facade still serves CLI, Chat and delegation; the criterion-basis
operation still serves the optional observer's canonical-basis reads. Neither
is an unused compatibility export.

## Qualification and exit

Before product changes, the test setup pins a synthetic relative acceptance
argv before contract hashing. File and SQLite pass the unchanged characterization
three consecutive times; the complete per-scenario context and status rendering
fixture is then frozen at 841 lines, with no output normalizer. It covers off,
empty, repeated/multiple Todos, wrong instance, removed criterion, unavailable
inspection/store, manual/legacy observations and pin drift. Separate numeric IO
and run-order tests protect the transport and selection boundaries.

The existing Python context adapter and this bridge operation exit when status,
attention, refresh-state and Chat consume the native receipt read/summary owner
without Python receipt IO or composition. Retire the temporary characterization
and crossing fixtures at that same verified caller cutover, preserving semantic
negative tests. This slice neither changes a public journey nor qualifies an
installed distribution, provider performance or long-horizon outcomes.

## Review repair validation

The fake loader now uses the real loader's ordering helper; both frozen JSON
fixtures remain byte-identical (SHA-256 checked). A TS test proves that newest
follows input order even when run metadata suggests another order. Existing
Python run-order and pin characterization remains green.

- Ruff: 2 changed Python files pass; pytest: 77 passed, 0 failed.
- Requested Node tests: 39 passed, 0 failed; control-plane typecheck passes.
- Semantic vocabulary smoke passes (29/29 vocabularies, 56/56 owner symbols).
- CLI base/head parity: File and SQLite, JSON and Markdown (4 comparisons).
  Markdown is byte-identical; JSON differs only in source read clocks/ages,
  which also vary on base-to-base reads. Progress-review payloads are unchanged.
- `git diff --check` passes. No generated `uv.lock` retained. All changes remain
  unstaged; no commit, push or PR update performed.

Validation uses this checkout's installed `.venv` after verifying import
provenance; `uv run` could not acquire its default cache lock in the sandbox.
