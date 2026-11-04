# Adaptive child admission — 2026-11-02

This checkpoint moves the complete adaptive native-child admission decision from
`quota/task_orchestration_admission.py` to `quota/adaptive_children.ts`, next to
the peer orchestration owner. The existing `multi_subagent` capability and its
builtin provider remain unchanged; this is an internal quota owner cutover,
not a new capability, provider, permission or frontend setting.

One `quota.task_orchestration.admit` request owns candidate eligibility,
User-dependency exclusion, coordinator election, ordered rejection reasons,
primary selection, capacity deferral, first conflicting lane, child ordering,
brief construction and context/execution policy. Capability requirements and
quota scheduling overlap execute through their existing TypeScript owners in
process. The standalone scheduling-overlap RPC is removed. Ordinary lease
matching keeps its folded semantics; Goal allow-scope matching remains
case-sensitive. Todo actionability reuses the summary-lane owner, including
acceptance denial. Election uses the shared Python-compatible JSON codec,
Unicode code-point ordering and an unsigned 64-bit digest prefix via BigInt.

## Migration receipt

The immutable characterization fixture was captured and passed on unchanged
`fb16c6cb1` before moving rules. Its 18 cases preserve complete admission JSON,
quota contract/work-lane JSON and Markdown. Instrumentation measures admission
crossings separately from the surrounding quota command:

| Operation | Before | After |
| --- | ---: | ---: |
| Requirement-free admitted lanes | 0 | 1 |
| Missing-capability case | 3 | 1 |
| Conflict/first-conflict cases | 2 | 1 |
| Empty source or non-coordinator | 0 | 1 |
| Adaptive mode off | 0 | 0 |

Previously there were P per-pair overlap requests plus M uncached capability
requests; both owners now run in process behind the admission request. The
zero-to-one early-exit cost is intentional: Python keeps no candidate/election
rule merely to avoid that crossing. This does not claim one request for the
whole quota command, lower latency or long-horizon benefit.

The replaced Python dataclasses, rejection/selection/election composition,
brief/context policy, overlap adapter and admission constants are deleted.
Python retains a transport adapter with historical Todo/repository/resume and
Agent-id codecs, explicit source-vs-display selection and public objective text
rendering. These preserve persisted spellings and public text at the existing
Host boundary; none selects, rejects or orders a lane. Capability wrappers,
peer hashing and projection-repair scope matching retain independent production
callers; they are not admission fallbacks or unused compatibility exports.

The TypeScript `schedulingWriteScopeAllowed` intentionally duplicates Python `projection_repair.write_scope_allowed`, whose retained production caller `build_boundary_projection_repair_hint` uses it for missing-scope detection and inactive-boundary candidate matching; remove this duplication when that caller migrates to the shared typed scope owner with equivalent behavior validated.

The adapter exits when the sole production caller in `task_orchestration.py`
and its surrounding should-run transaction consume typed source facts natively.
Then that caller composes `admitAdaptiveChildren` in process and removes the
Python adapter and `quota.task_orchestration.admit` transport registration.
The frozen fixture remains until that caller cutover carries equivalent
complete-output and crossing evidence.

Product changes total +342/−461 physical lines, net −119. The Python admission
module changes from 490 to 137 lines (+97/−450); its 101-line adapter function
including signature performs only transport/compatibility work. The new typed
owner is 222 lines. Test, fixture and documentation lines are counted separately.

## Qualification boundary

The existing dashboard capability workbench and orchestration settings,
Lark/CLI consumers and quota readback retain their contracts; no visual surface
changes. The source-checkout production `quota should-run` entrypoint was
compared against a `git archive HEAD` baseline with a synthetic temporary home,
a fixed clock and adaptive admission enabled. Complete outputs match byte for
byte on File and SQLite, in JSON (35,126 / 35,160 bytes) and Markdown
(8,565 / 8,587 bytes). This qualifies source behavior, not installed adoption.

The focused Python suite passed 543 tests; the existing focused TypeScript
suite passed 100 with one unrelated PostgreSQL workspace test skipped, and
17 new typed admission tests passed. Typecheck, semantic vocabulary drift
(45/45 independent twins), maintainability ratchet, interface budgets and
integrated File/SQLite canaries passed. The diff-scoped semantic advisory found
no supported new vocabulary carriers; its limited syntax coverage is not proof
of semantic completeness. No budget, smoke anchor or gate is relaxed.

The bounded refactoring pass shares Todo actionability and extends the existing
glob engine with a case-sensitive mode rather than adding another matcher.
No storage backend, lease persistence, lease authority or PostgreSQL IO changes.
A capability recovery read with `network` available also matches complete
outputs: File JSON/Markdown 31,907/8,478 bytes, SQLite 31,937/8,500 bytes.
The first recovery comparison observed an existing capability-hook mutation:
`local_private_state_mutated` was true for the first base read and false for
the subsequent head read. A base-only repeat reproduced true → false and then
matched head byte for byte. Final comparisons settle that synthetic hook state
before both reads; no expected fixture or product rule was changed.

The synthetic CLI outputs also preserve a baseline limitation:
`interaction_contract.agent_channel.work_context.complete` is false, with
`selected_todo` / `context_source_unavailable`. All eight JSON observations
(base/head, both providers and capability states) contain that same failure.
Admission and quota-output parity are qualified; fully available selected-work
context delivery is not. This separate readback gap is outside this admission
owner cutover and must not be hidden by a parity success.

Full CI, installed packaging, cross-host operation and performance remain
unqualified by this slice.
