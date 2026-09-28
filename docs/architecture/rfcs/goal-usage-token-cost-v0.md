# RFC: Per-Goal Usage, Token, and Cost Surfacing v0

- Status: Accepted
- Supersedes / closes: none
- Scope: core `usage_summary` token / cost / duration capture and existing-dashboard surfacing
- Decision type: bounded public-contract change plus a provider-neutral capture layer

## Summary

This RFC proposes capturing per-goal LLM usage — input/output/cache tokens,
estimated cost, and wall-time duration — into the existing core `usage_summary`
aggregate, and surfacing it in the existing dashboard. The work introduces one
provider-neutral capture seam; it does not add a second usage store, a new
dashboard, or cost-aware runtime routing.

The intent is to let an operator see, per goal, what a long-running run
actually consumed — not only whether it was allowed to run (the current quota
slot accounting) but what it spent. This closes the gap left open in
`docs/quota-allocation.md`, which notes that later versions may "replace slots
with real runtime, token spend, or cost."

## Problem

`usage_summary` is explicitly a run-history proxy that excludes token counts.
Its proxy note states it is a "run-history proxy; excludes token counts and raw
thread logs," and the public `docs/status-data-contract.md` repeats the same
exclusion. As a result:

- The dashboard can show how many runs happened and how many quota slots were
  spent, but not how many tokens a goal burned or what it cost.
- Token and cost capture already exists, but only on the benchmark path, wired
  to one runtime (Codex) and to an external pricing table. It is not reachable
  from the goal runtime's main path.
- No goal runtime reports LLM usage today. The claude, opencode, and pi goal
  modes carry no token-telemetry code; "token" identifiers in opencode and pi
  refer to scheduler identity tokens, not LLM consumption.

The primitives needed to fix this are mostly present (a usage aggregate, a JSON
status API, a mature dashboard). What is missing is the capture seam that turns
each runtime's usage into one normalized shape, plus the contract change that
admits token/cost data into the public aggregate.

## Goals

- Capture input, output, and cache tokens, an estimated cost, and wall-time
  duration per goal run, in the core `usage_summary` aggregate.
- Keep ingestion provider-neutral: one schema, with per-runtime adapters.
- Surface the captured data in the existing dashboard without adding a new
  dashboard or a second usage store.
- Preserve the existing privacy boundary: raw thread logs, prompts, and
  completions never enter `usage_summary`; only aggregate numeric usage does.

## Non-Goals

- Cost-aware runtime routing or selection (a follow-up; depends on this work).
- Storing raw prompts, completions, tool output, or transcripts.
- Building a new dashboard or a parallel usage store.
- Capturing per-turn fine-grained telemetry; this RFC targets run-level
  aggregates first.
- Weakening any existing gate, validation, or quota invariant.

## Decision Boundary

This RFC changes one public contract and leaves the rest intact.

**Changed.** `usage_summary` becomes a run-history proxy that *includes*
aggregate token counts, estimated cost, and duration. The proxy note and
`docs/status-data-contract.md` are updated to reflect this; the historical
"excludes token counts" wording is withdrawn. Token/cost/duration become
first-class fields of `blank_usage_goal` and the `totals` block.

**Unchanged.** Raw thread logs, prompts, and completions remain excluded from
`usage_summary`. The exclusion of raw content is a privacy invariant, not a
historical accident, and is preserved.

Because this alters a documented public contract, the implementation must
update `docs/status-data-contract.md` in the same change set and add focused
tests for the new fields (none exist today for `usage_summary`).

## Capture Contract

A provider-neutral capture seam is added under `control_plane/quota/`, modeled
on the spend and slot accounting already living there.

The core of the seam is one function:

```python
def collect_usage_for_run(run) -> UsageSample | None:
    """Return normalized usage for a run, or None if the runtime reports none."""
```

`UsageSample` is a normalized, public-safe shape:

```json
{
  "input_tokens": 12000,
  "output_tokens": 3400,
  "cache_tokens": 8000,
  "cost_usd": 0.21,
  "duration_ms": 184000,
  "provider": "codex",
  "model": "<model id>",
  "measured_at": "2026-08-11T14:55:00Z"
}
```

Each runtime registers an adapter that reads its own usage source and returns a
`UsageSample` (or `None`). The aggregate loop in `build_usage_summary` asks the
seam for each run and accumulates the result into the new fields. Runtimes that
report nothing contribute nothing; the aggregate degrades gracefully to the
current behavior for them.

The Codex adapter can reuse the extraction approach already proven on the
benchmark path (scan the session's token-count events for the final cumulative
figure), factored out of its current external coupling.

## Cost Computation

Cost is the one field that is not a direct measurement; it is derived from
tokens and a price. Two options:

1. **Runtime-reported cost (recommended).** Each adapter computes cost from the
   tokens it measured and a price it knows (it already holds the model id), and
   returns `cost_usd` in the `UsageSample`. Core stores and aggregates it.
2. **Core-computed cost.** Core holds a price table and derives cost from
   tokens and model. This keeps computation centralized but introduces a price
   table that can drift against provider changes.

This RFC recommends option 1, because a core-maintained price table would
duplicate and drift against the very external table the benchmark path already
depends on, and because each runtime already holds the model id needed to price
its own usage. The final choice is an open question for review.

Regardless of option, `usage_summary` exposes only aggregate cost. Per-unit
price detail is not surfaced, to avoid leaking provider-specific commercial
terms.

## Data Classification

Following the issue's guidance, token counts, estimated cost, and duration are
classified **public-safe** and may enter the public `usage_summary` contract:

- Aggregate token counts and cost reveal operational spend, not content.
- Wall-time duration is operational metadata.
- Provider and model identifiers are public product names.

They do **not** carry prompt content, completion text, tool output, credentials,
or anything that reconstructs a conversation. The raw-thread-log exclusion is
preserved as a separate, stricter invariant.

## Smallest Useful Slice

The first slice proves the seam end-to-end on one runtime:

1. Add the `UsageSample` shape and `collect_usage_for_run` seam under
   `control_plane/quota/`.
2. Implement the Codex adapter (reuse the benchmark extraction approach).
3. Extend `blank_usage_goal`, the `totals` block, and the
   `build_usage_summary` loop with token/cost/duration fields; update the proxy
   note.
4. Update `docs/status-data-contract.md` in the same change set.
5. Surface the new fields in the existing dashboard (no new dashboard).
6. Add `tests/control_plane/quota/test_usage_summary.py`.

Adapters for the claude, opencode, and pi goal modes are explicit follow-ups
and are out of scope for this slice, because none of them report usage today
and each needs its own ingestion mechanism (statusline hook, stdout parsing, or
bridge IPC). The seam is designed so that adding an adapter does not revisit the
aggregate or the contract.

## Validation

The slice must prove:

- after a Codex goal run, `usage_summary` carries non-zero token/cost/duration
  for that goal;
- the dashboard renders the new fields;
- a runtime that reports nothing leaves the aggregate at the current behavior;
- raw thread logs, prompts, and completions do not appear in `usage_summary`
  (privacy regression check);
- the public contract in `docs/status-data-contract.md` matches the
  implementation;
- `loopx check --scan-path` reports no private-data leakage.

## Open Questions

1. Cost computation location — runtime-reported (recommended) vs core-computed.
2. Whether model identifiers need any redaction in public surfaces, or whether
   aggregate cost alone is sufficient and model names are treated as public.
3. Ingestion mechanism and ordering for the claude, opencode, and pi runtimes.
4. Whether to add a dedicated `/usage.json` endpoint or continue surfacing
   through the existing status payload (this RFC assumes the latter).
5. The duration capture anchor — which run boundary events delimit wall-time.

## Public References

- [LoopX quota allocation](../../quota-allocation.md) — notes that later versions may replace slots with real token spend or cost.
- [LoopX status data contract](../../status-data-contract.md) — the public contract this RFC changes.
- Issue #3085 — the feature request and maintainer guidance this RFC follows.

This RFC excludes private conversations, internal links, local filesystem paths,
credentials, and raw transcripts.
