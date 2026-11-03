# Complete durable-completion evidence read (2026-11-02)

The built-in `turn run-once` provider is the only production consumer of the
Python durable-completion projection. Its intent, post-completion, ordinary
recovery and terminal recovery now use the existing `todo.read` transaction.
`todos/read_transaction.ts` collects canonical source pages in process;
`todos/read_projection.ts` composes the existing completion fence and owns
continuation/recovery validation, successor existence and result construction.
Canonical refusal never falls back to stale Markdown. Fence rejection retains
precedence over continuation projection during recovery.

## Economics and retained boundary

Instrumented File/SQLite reads measure ordinary recovery **2 → 1**, terminal
recovery **2 → 1**, intent **1 → 1**, post-completion **1 → 1**, and explicit legacy
materialized reads **0 → 1** Python/TypeScript calls. Canonical collection stays
inside the request, including pagination. The legacy increase removes its Python
semantic authority; it is not a latency improvement claim.

Deleted Python authority includes `_project_durable_completion`, terminal fence
composition, the separate Turn fence call and the entire `completion_fence.py`
facade, and the obsolete public persisted-record readers. No string-dispatch consumer remains for `todo.completion_fence.evaluate`,
so its registration is removed. Native fence imports and tests remain.

Python retains registry/path resolution, legacy Markdown observation/decoding,
Python scalar/container encoding, rejection exception compatibility and transport.
The canonical source-result decoder is shared with existing authority reads to
preserve unavailable/refusal error types without an extra provider request.
Compatibility projection functions delegate to the same complete native owner;
they do not interpret continuation or recovery.

The facade exits when `turn run-once` is native and no Python caller requires
legacy persisted-format observation or the projection import contract. The
completion-evidence read kind can then become an in-process call; the ordinary
`todo.read` operation remains for other Python consumers. Characterization
fixtures may retire with that facade after independent native coverage retains
its negative cases. No long-horizon effectiveness/efficiency benefit is claimed.

## Evidence and scope

The pre-cutover corpus pins 83 complete JSON observations, exact diagnostics,
field absence and ordered missing-successor lists. Baseline instrumentation and
existing focused tests passed before authority moved; expected bytes remain
unchanged. Native fence tests remain independent of that corpus. Real CLI checks
cover File/SQLite crash-after-completion recovery and stale-display identity.
The delivery report records exact commands, results, source line deltas and any
unqualified gates. No storage format, write authority, scoring or default setting
changes are intended. This internal read refactor uses the existing CLI journey
and adds no capability or configuration surface.

Wheel and sdist were installed into separate fresh Python 3.12 environments.
Each installed package executed completion evidence, canonical refusal and a
real canonical recovery while importing solely from `site-packages`; all three
checks passed.

Matched default-SQLite qualification used the pinned base `867615e2c` and this
head with the same synthetic fixture, 32 cold starts, 128 warm typed requests,
64 complete canonical transactions, 64 full-CLI recovery transactions and a
256-request burst. The base → head results are: cold-start p50/p95
115.522/128.518 → 102.829/135.269 ms; complete transaction
68.774/103.729 → 9.806/14.320 ms; full CLI recovery
358.661/611.397 → 264.324/334.687 ms; process-tree RSS
30/30 → 32/33 MiB idle/burst. The cold-start p95 difference is +6.747 ms
(+5.25%) with a lower median, below the 25 ms additive allowance. The typed
warm request includes the persisted remote read; it is not the non-durable
in-process kernel budget. The complete transaction and operator CLI both
improve materially on the matched SQLite baseline.
