# Global Todo readiness owner cutover — 2026-11-02

## Boundary and economics

Baseline: `fb16c6cb1369efce52a673030c914b38e1dac166`. This bounded slice
moves the complete Global Todo readiness decision from `global_todos.py` to
`projectGlobalTodoReadiness` in the existing `todos/decision_scope.ts` owner,
registered as `todo.global_readiness.project`. Both `loopx global-todos` and
`loopx goal-portfolio` use it through their shared display adapter; portfolio
classifies each registered Agent independently.

TypeScript owns ordered selected/lane/summary/frontier traversal, first-source
merge with missing-field fill, source/status provenance, in-process `todoGateRelation`, blocked/deferred/runnable
precedence, and warning ordering. `normal_delivery_allowed` remains a readiness
projection signal and grants no execution permission. Existing readiness values
are reused; the new versioned request/result describe this bounded projection,
not a new lifecycle or authority vocabulary.

Instrumentation pins all cross-runtime requests, including envelope sealing:

| Operation | Baseline | Candidate |
| --- | --- | --- |
| One classification, gates and candidates present | 1 relation request | 1 complete readiness request |
| One classification, no gates | 0 | 1 |
| One classification, no candidates (including empty payload) | 0 | 1 |
| Global command, one classified Goal with gates | 2 including envelope | 2 including envelope |
| Global command, gate-free or empty classification | 1 envelope | 2 including envelope |
| Portfolio, two Agents with gates | 2 | 2 |
| Portfolio, two gate-free/empty Agent classifications | 0 | 2 |

The gate-free/empty increase is intentional and disclosed. No latency or
long-horizon effectiveness/efficiency benefit is claimed.

Product physical diff: +163/−166 lines, net −3. Python removes source
traversal/merge helpers, blocked/deferred classification, readiness precedence
composition and its separate safety-rank table. The uncalled Python scalar
`todo_gate_relation` bridge and its TS scalar dispatch are deleted. Its synthetic
smoke and decoder tests use the production batched bridge retained for summary.

Python retains `open_user_gate_todo_items` as the single gate-selection owner:
summary, stall repair, Todo summary and agent scope still call it. Its ordered
selection, done filtering and Python-equality deduplication must not be copied
into TypeScript. Selected gates cross as decoded facts with target text.
Shared `_facts` retains persisted ID/scope normalization and legacy task-class
interpretation. The bounded request carries only selected/lane records, delivery
signal, and list-valued summary/frontier fields. Python decodes dict elements of
every such list generically; TypeScript alone owns candidate list-key traversal.
There is no recursive payload walk. The largest frozen case's compact UTF-8 JSON
request shrinks from 9,749 to 6,447 bytes (33.9%); outputs remain byte-identical.
Python also retains status/quota IO, public text redaction, `_work_kind` display
interpretation, final grouping/deduplication/display sorting, envelope sealing
and Markdown. These compatibility and presentation boundaries are not a second
candidate-readiness owner.

Facade exit: delete `project_global_todo_readiness` and its decoded-field
sidecar once both global and portfolio host/read models consume the typed Todo
snapshot and projection directly, with native legacy metadata decoding and
public-safe rendering at their owning boundaries. Delete the shared display
adapter when neither consumer needs Python normalization. Retire the temporary
characterization corpus/archive comparison at that exit after durable semantic
and public-entrypoint tests cover the retained contracts.

## Qualification and limits

Seven synthetic characterization cases passed on unchanged Python before the
cutover; their complete JSON/Markdown bytes and baseline/candidate crossing
lists were then frozen. The fixture SHA-256 is
`eaf17921f81357329ba7bc3c7dcb34253eed83b53d31c31d6dba0fdb5c547641`.
Cases cover complementary duplicate fields, conflicting statuses, delivery
denial, exact/scope conflict, unrelated gates, blocked/deferred overlap,
missing identities, two Agents, warning order, empty inputs and public-text
redaction. Independent TS tests assert precedence, denial and exact/scope
conflict semantics, plus malformed protocol rejection. Python decoder tests
reject malformed results without fallback.

`tests/test_global_todo_readiness_characterization.py::test_production_cli_archive_parity`
archives `HEAD` without stash and compares both public commands, JSON and
Markdown, on the same synthetic temporary home with real File and SQLite
providers. Only clocks are frozen before importing the production CLI; field
presence, diagnostics, source versions and hashes are compared unchanged.
All eight command/provider/format pairs match and require populated Todo and
Agent results. This qualifies a source checkout, not installed packaging or
frontend behavior; no caller capability or user journey was added.

The focused scheduler-state readback test
`test_real_cli_quota_should_run_discloses_unreadable_scheduler_state` fails on
both the changed checkout and the archived baseline: its quota command omits
Turn identity and returns `QUOTA_VALIDATION_FAILED` with the requirement for
`--turn-instance-id` or `--begin-turn`. This is a pre-existing caller/validation
mismatch, outside readiness ownership. Preserve that failure; do not relax the
argument gate or rewrite this migration's expected outputs to conceal it.

Semantic drift remains `independently_maintained=45/45`; there are no new
control-plane source files or same-basename pairs. Maintainability and interface
budgets pass unchanged. The integrated canary passes File and SQLite. The
diff-scoped advisory detects no supported new vocabulary carriers across four
changed product source files; its supported scope excludes test sources.

## Review correction validation

After removing duplicate gate selection and bounding the request: requested
Python suite 151 passed / 1 failed (the scheduler caller mismatch above);
native Node tests 19 passed / 0 failed; boundary suite 28 passed / 0 failed.
Ruff on all four changed Python files, control-plane typecheck, both requested
smokes and diff whitespace checks pass. Seven frozen characterization cases
remain byte-identical, with the fixture hash unchanged. File/SQLite production
CLI parity covers all eight command/format/provider pairs. A focused boundary
regression proves generic decoding of unknown list fields, omission of unrelated
objects and reuse of shared gate deduplication. No commit, staging or push was
performed; generated uv.lock was removed.
