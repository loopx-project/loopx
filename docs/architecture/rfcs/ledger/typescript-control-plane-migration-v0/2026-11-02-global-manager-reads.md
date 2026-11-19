# Global summary and gate read transactions — 2026-11-02

The shipped `global-summary` and `global-gates` CLI commands now call
`presentation.global_summary.project` and `presentation.global_gates.project`
in `presentation/global_manager_read.ts`. These are complete read projections:
lane selection and ordering, gate questions and routing, exact-target precedence
over scope-derived blocked references, visible work verification, progress
filtering, pre-limit totals, grouping, actions and envelope composition belong
to TypeScript. Decision relations and projection-envelope sealing compose in
process through their existing owners. No standalone leaf RPC is added.

## Boundary and crossings

Frozen synthetic characterization covers complete JSON and Markdown bytes and
source-read order, including empty/unhealthy sources, mixed lanes, duplicate
Goals, unavailable quota, Agent mismatch, early termination, range and limit
normalization, exact/scope references, redaction and stale/unreadable/incomplete
upstream envelopes. It passed on the unchanged Python implementation before
production edits. Its expected outputs remain unchanged.

Instrumented projection crossings change from **1 → 1** for summary and
**R + 1 → 1** for gates, where R counts nonempty relation evaluations. The
mixed and early-stop gate cases measure **2 → 1**; empty, unhealthy and scoped
cases without relation evaluation measure **1 → 1**. These counts exclude the
unchanged status/quota source providers and their own internal requests.

Python retains source acquisition and quota-failure adaptation. Gate eligibility
and user-gate classification remain a single Python source-side predicate:
`_gate_source_facts` uses the shared `open_user_gate_todo_items` owner, which has
other Python callers. The incremental scan must decide its early stop before
further host reads. It preserves deduplication and the original stop point,
attaching the first formal gate row and eligibility only to gate samples. TS
strictly decodes these facts and composes owner selection, blocked references
and the complete gate projection; it does not repeat classification or
eligibility. This avoids per-candidate bridge chatter and reads beyond the limit.

The replaced Python lane/gate construction, relation calls, progress filtering,
ordering, counting, grouping, action/result assembly and envelope composition
are deleted. Python retains text redaction and timestamp compatibility codecs,
existing error rendering and Markdown rendering for the CLI and presentation
smoke. `SOURCE_SURFACES` remains source-acquisition metadata under its existing
registered context and is passed to the typed projection. No persisted format,
permission or state mutation changes.

## Exit and qualification

Remove the Python facade, codecs and both bridge methods when native callers
obtain these source facts through TS-owned status/quota acquisition and consume
the complete projections directly, and when the CLI and presentation smoke no
longer import the Python Markdown renderers. The source predicate can move only
when user-gate classification has a TS owner and source acquisition moves to TS;
retire its Python implementation then. Remove the frozen migration fixtures when that last Python caller contract is retired; retain
independent semantic tests.

The existing CLI surface is preserved: `summary-all` is unregistered on both
base and candidate; `global-summary` is the supported summary command. Real CLI
JSON and Markdown readbacks match byte-for-byte against an archived base on a
synthetic canonical runtime with the same clock. This is source-checkout
qualification, not wheel/sdist or installed-host qualification. No latency,
complete-cost or long-horizon benefit is claimed.

Focused Python characterization and existing summary tests pass (31), as do
38 TS owner/envelope/relation/dispatch tests, typechecking, the vocabulary gate
(45/45 independent twins), maintainability, interface budgets and Markdown
presentation smoke. File and SQLite integrated canary behavior passes; an
initial concurrent run exceeded the unchanged 60-second aggregate budget,
while a rerun passed (18.15s File, 13.44s SQLite). The broader source-checkout
Python selection has 168 passes and two failures, both reproduced on the
archived base: the quota CLI scheduler-envelope test lacks required decision
capture arguments, and the steward endpoint vocabulary test expects the old
endpoint set. No gate or expected fixture was changed to resolve these failures.

After the source-predicate repair, the unchanged characterization and summary
selection passes all 31 tests; the global-manager and handler selection passes
22 TS tests, including malformed-fact rejection and source-eligibility adoption.
Typechecking, ruff, vocabulary drift, maintainability, hot-path budget, Markdown
primitives and all six CLI parity comparisons pass. Product changes against the
baseline total +321/-629 lines (net -308). Fixtures and gates remain unchanged.
