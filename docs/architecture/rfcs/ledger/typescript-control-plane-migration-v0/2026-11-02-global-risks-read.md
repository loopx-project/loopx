# Complete global-risk read ownership — 2026-11-02

The existing `loopx global-risks` CLI now sends one complete source-observation
request to `presentation.global_risks.project`, owned by
`control_plane/presentation/global_risk_read.ts`. It validates the status
projection, normalizes diagnostics and stale warnings, derives stale host-poll
risks, filters Agent scope, aggregates occurrences, escalates severity, sorts,
limits and constructs success/error results. It composes the existing projection
envelope owner in process. Rollback candidates remain deliberately empty; this
read grants no execution authority and adds no product entrypoint.

The synthetic 37-case compatibility corpus is green against the unchanged
Python owner in an immutable baseline archive. Its complete JSON and Markdown bytes and
one-crossing expectations remain unchanged. Instrumentation measures one
`projection.envelope.seal` crossing before and one
`presentation.global_risks.project` crossing after, including source errors and
Agent-scope failures. The receipt decoder still distinguishes missing and
unreadable persisted sources. Stale decisions require explicit expected
continuation and compare UTC instants at the inclusive 240-minute threshold,
including microsecond and offset cases.

Deleted Python includes diagnostic expansion/normalization, stale-warning and
stale-host-poll decisions, Agent filtering, aggregation, severity ordering,
grouping, ordinary result construction and envelope composition. The sole
production caller of `stale_host_poll_risk` moved, so that helper and its threshold
constant are removed. Its implementation-only tests move to the typed owner;
complete public-output fixtures remain compatibility evidence.

Python retains status/registry/receipt acquisition and the persisted receipt
codec because their host IO owners have not migrated. The acquisition cap mirrors
the existing bounded-source read contract; TypeScript owns result eligibility
and truncation. Python also retains Markdown rendering and its shared public
text codec. TypeScript mirrors the canonical Python path patterns from
`loopx/public_safe_text.py` under an ordered source-and-flags parity test. Normal error payloads are
constructed in TypeScript. A transport-failure-only wire codec remains in Python
because an unavailable Node runtime cannot construct its own failure response;
it reports no envelope and preserves the existing public-safe error fallback.
It does not derive risks or certify freshness.

The Python facade and bridge operation exit when the CLI's existing status and
project-receipt acquisitions plus Markdown rendering are hosted by the typed
caller, with equivalent public error and host-unavailability behavior. There is
no new leaf RPC, persisted schema, cache, provider default or Python/TypeScript
same-basename pair. The registered `SOURCE_SURFACES` context moves to the typed
owner without changing vocabulary budgets.

CLI parity uses an immutable archive of baseline `867615e2c` and a synthetic
empty registry/home, comparing actual JSON and Markdown commands. Only current
observation timestamps are normalized. This is source-checkout evidence, not
installed-artifact qualification. Full-tree vocabulary checks enumerate tracked
sources, so an unstaged new owner is invisible until normal review staging;
that limitation does not authorize staging or budget changes. Validation results
and physical line accounting belong to the delivery report.

No latency, sustained runtime or long-horizon effectiveness/efficiency benefit
is claimed. Fewer Python decision lines and a coarser request establish an owner
boundary, not performance qualification or a completed migration stage.
