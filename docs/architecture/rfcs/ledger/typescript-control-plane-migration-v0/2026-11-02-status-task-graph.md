# Status task-graph composition (2026-11-02)

## Owner boundary

This checkpoint moves the complete optional status task-graph decision from Python
`work_items/task_graph.py` into the existing TypeScript `work_items/task_graph.ts`.
One `work_item.task_graph.status` operation owns selected work, claim display,
user-gate selection/count/cap/overflow, bounded predecessor traversal, run
selection and lineage joins, evidence, handoff, repair, deduplication, dangling
edge rejection, timestamps and completeness/limits. It calls existing topology,
Todo summary, classification and handoff owners in process. The standalone
`work_item.task_graph.topology` handler is retired; topology remains an internal
composition function used by status and its semantic tests.

Python supplies source-shaped status facts, latest-run facts and fallback time.
It retains public-text redaction/normalization facts. Status SHA-256 node/edge
identity materialization moves into the composer with byte-identical Unicode
and `None` encoding, so deduplication compares actual historical identities
rather than unevaluated descriptors. Goal-map uses the same native identity,
state and topology-row functions; the three Python rule helpers are deleted.
Source fields are restricted to existing Todo read fields and the consumed
status lanes. Derived display text uses the qualified composition-only
compaction boundary described below. There is no Python selection, traversal, gate, evidence,
handoff, lineage, repair or limit fallback.

`build_goal_task_map` retains its public signature and byte-identical output for
`chat_status_api.py`. It sends source/public-text facts to `projectGoalTaskMap`,
which now materializes node/edge identities, state and metadata. Native topology
fact requests retain their existing compatibility contract. Omitted opaque
metadata is not stringified; unsupported non-JSON public-text values are marked
unavailable at the codec edge and rejected only if consumed. Uncalled status
builders, attachers, source collectors, ref helpers and constants are deleted.
No new Python/TypeScript basename pair is added.

Physical product diff: +865/−1030, net −165 lines against the archived base.
`task_graph.py` falls from 1,044 to 255 lines: one module-level recursive public
text codec and two source/transport facades, with no graph decision rules.
Tests, fixtures and documentation are counted separately.

## Public text and remaining duplication

Choice (a): there is no second TS redaction rule. Source strings must have
Python public-text facts; a missing fact throws. The local fallback only calls
`compactTodoReadText` on registered public fact outputs or explicitly composed
strings. Interpolated components are asserted to be registered public strings
or numeric counts. Native tests enumerate composed titles, identities,
references and reasons, and reject unqualified source text. Constructors are
claim/lease, evidence prefix and public evidence prefix-slice, handoff identity/
title/reasons, missing-ID gate, gate overflow, latest-run title, repair defaults,
and joins of generated node identities. Fixed reason prose comes from native
graph construction; generated identities contain only ASCII identifier/hash
characters. Goal scope is already public-normalized by the status caller.
No `INTERNAL_STATE_SHAPE_PATTERNS` redaction copy remains in this module.

`rg` found no equivalent TS owner of Python `open_todo_items`: `read_codec.ts`
provides compaction/fields, `context_projection.ts` provides planning/display
sources, and `summary_lanes.ts` selects normalized summary lanes, not the
historical source-key, dedup-before-sort, bounded-open display selection.
Python still serves status attention/monitor projections, project-asset
readbacks and Todo summary renderers. `statusOpenItems` therefore retains that
bounded duplication. Exit: migrate those callers to one native Todo open-display
selection owner, qualify ordering/handoff/limit parity, then remove the Python
selection and delegate the graph to that owner. Do not substitute normalized
summary-lane selection with different admission semantics.

The codec caches repeated source strings and computes only field-used caps;
no source redaction facts are inferred from truncated text. Display caps and the
unsupported-text marker are local transport facts, not a new shared decision
vocabulary. Counts below include
the initial Goal public-text call; “before” means the reviewed encoder draft,
which called the public codec six times per source string (full safety plus
five display caps), not the original all-Python graph:

| Status case | Public-text calls before → after |
| --- | --- |
| `diamond_at_cap` | 127 → 32 |
| `cycle` | 79 → 22 |
| `missing_prerequisite` | 31 → 12 |
| `open_predecessor` | 79 → 24 |
| `condition_and_lineage` | 55 → 19 |
| `predecessor_overflow` | 193 → 47 |
| `duplicate_identity` | 97 → 19 |
| `long_public_text` | 61 → 21 |
| `redaction` | 61 → 21 |
| `handoff_evidence` | 73 → 21 |
| `unicode_ids` | 43 → 15 |
| `all_empty` | 1 → 1 |
| `gate_overflow` | 145 → 24 |
| `no_selected_run_gate` | 43 → 17 |
| `repair_evidence` | 79 → 29 |
| `stale_source_time` | 43 → 18 |
| `absent_goal` | 1 → 1 |
| `invalid_goal` | 1 → 1 |

Largest characterization request (`long_public_text`): compact JSON parameters
with the transport's ASCII escaping, 6,797 → 4,899 bytes (−27.9%); calls
61 → 21 (−65.6%). These are parameter bytes, excluding RPC framing. The maximum
call case, predecessor overflow, falls from 193 to 47. This is codec/request
cost evidence, not an end-to-end latency or effectiveness claim.

## Crossings and parity

Instrumentation counts every `effect_runtime_result` reached by the public
Python graph entrypoint, including Todo display/handoff helpers:

| Operation | Before → after |
| --- | --- |
| Selected work, including topology/error evidence cases | 4 → 1 |
| User-gate overflow | 7 → 1 |
| Run/gate nodes with no selected Todo | 4 → 1 |
| Valid Goal with all-empty facts and no graph | 0 → 1 |
| Absent or invalid Goal | 0 → 0 |
| Goal task map | 1 → 1 |

The empty valid snapshot is decided inside the native composer; only absent or
invalid Goals return before transport. A rejected native request has no Python
fallback. Large packets reuse the existing local snapshot transport.

The frozen synthetic characterization corpus was green on unchanged
`fb16c6cb1369efce52a673030c914b38e1dac166` (21 cases) before implementation;
expected JSON bytes remain unchanged. Independent native cases test semantics
rather than using that corpus as a specification. The status Markdown renderer
has no graph section. Real `status --include-task-graph` CLI output matches an
archived base byte-for-byte on isolated File and SQLite homes in JSON and
Markdown, with a fixed observation clock: respectively 33,417/33,445 and
5,120/5,126 bytes. The actual chat `_goal_task_map` entrypoint, using the same
canonical File/SQLite sources, matches at 2,310 bytes on each provider. The three
already-pinned Goal-map cases were green on the archived base before refactoring;
the fixture expected outputs have not been edited.

## Qualification and exit

Current rerun: Python 355 passed, 2 failed across the six requested suites and
all tests found by `rg build_goal_task_map / chat_status_api`; native graph/handler/
loading tests 50 passed. Ruff (two changed Python files), control-plane typecheck,
task-graph fixture, attention queue, semantic vocabulary and maintainability
smokes, six CLI/chat parity comparisons and `git diff --check` passed. Earlier
interface-budget/integrated-canary evidence remains historical, not a new rerun. The quota planning-horizon suite retains two failures
(missing `planning_horizon`, File and SQLite), reproduced with the same tests
in an untouched `git archive HEAD` baseline copy; they are not reported as passes.
No gate, budget, vocabulary anchor or baseline is relaxed.

The Python status facade exits when status callers deliver the source/public
text facts directly to the native owner and the retained public-text codec has
an independently qualified native replacement. Remove its operation only when
those callers use an in-process owner. The same codec exit applies to
the Goal-map facade. The characterization corpus
can retire once facade removal has independent public-entrypoint coverage.

The adjacent cleanup removes the obsolete topology RPC and callback surface;
no unrelated refactor or product journey is introduced. Existing CLI, attention
queue, chat and review-packet consumers keep their public payloads. No end-to-end latency,
total resource-cost or long-horizon effectiveness/efficiency benefit is claimed.
