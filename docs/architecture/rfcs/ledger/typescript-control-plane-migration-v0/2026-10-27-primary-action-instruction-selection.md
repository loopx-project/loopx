# Primary-action instruction selection

[中文镜像](2026-10-27-primary-action-instruction-selection.zh-CN.md)

## Boundary and ownership

This Stage 2B slice removes the Python instruction decision owner while keeping
caller-visible prose and rendering stable. `projectInteractionExecution` in
`work_items/interaction_contract.ts` selects the existing mode and its primary
instruction in the same `work_item.interaction_execution.project` request.
There is no new operation, handler registration, runtime, provider or store.
Qualification remains incomplete until the validation holds below are resolved.

Retired Python symbols in `work_items/primary_action.py`:

- `protocol_first_candidate_action`: source precedence, addressed labels,
  capability candidate count, scoped successor and advancement fallbacks.
- `protocol_replan_action_packet_instruction`: settlement-only, successor and
  generic typed-outcome wording.
- `resolve_canonical_primary_action`: exact mode prose, fallbacks and child/peer
  lane counts, including the empty-child-list precedence over peer lanes.

`interaction_contract.py::_project_interaction_execution` sends a single optional
facts/views carrier and the existing `action_selection_context` projection.
The final contract builder requests and threads the returned instruction through
`_build_interaction_agent_channel` into `build_primary_action_projection`.
`unadmitted_action_selection` retains its mode-only preflight and exact result
shape. The TS owner composes `actionSelectionNeedsRecovery` before reading
instruction facts; rejected/deferred recovery does not construct executable
instructions. The existing Python recovery alternative, selection/capability
channel overrides, blocked-successor override and blocked-wait override remain.

Python retains `protocol_action_text` and `protocol_action_label`: Python `str`
and nested repr, truthiness, whitespace, label prefixes and code-point limits
are compatibility codecs, including `...` rather than the trace codec's `…`.
`primary_action_instruction_facts` transports those views and existing Todo
eligibility/lifecycle source facts; it does not select an instruction or retry a
Python fallback. Python transports at most the first selectable row per list
source, evaluated from the same coerced views; the typed owner applies the same
predicates and source precedence. Request size is independent of backlog/Todo
list length. Registered effective actions and agent-scope values come from
their generated TS exports. Other existing local rule strings keep their exact
spellings; no shared vocabulary or persisted schema is added.

Python also retains `protocol_monitor_action` and the resolution trace through
`state_projection.next_action_resolution_trace`. `primary_action` still precedes
optional `resolution_trace`; channel insertion order and final Python JSON
serialization are unchanged. Delete the trace/monitor facade only when their
remaining consumers adopt a complete typed action/trace projection. Delete the
scalar/label codecs and facts carrier only after the last Python text/label
consumer exits and a complete typed compatibility codec proves Python coercion,
Unicode, truncation and trace parity. This slice does not retire those owners.

## Vocabulary budget

The registry's `meaning` says "module_budget counts modules under loopx/ whose text carries the standalone field token" and "the token count stays until Q11 decides whether the syntactic metric replaces it"; this deliberately conservative lexical proxy protects against unexplained growth in potential field readers.
On the same tracked-source workload, base `be1dc37a0` → `this commit` moves `goal_boundary` lexical counts from Python/TypeScript 30/2 → 29/3 (total 32 unchanged), originally failing `legacy field goal_boundary grew to 3 .ts modules; budget is 2` and retrodiction `(13, 15)` versus frozen `(12, 15)`.
The moved sentence, `repair goal_boundary.write_scope projection before attempting the selected write`, is byte-identical agent-facing prose, adding no reader, writer or binder; the smoke's prose-versus-reader distinction keeps migration surfaces at Python 16 / TypeScript 1.
Retaining that instruction, the registry tightens the Python token budget to its measured 29 and increases TypeScript 2 → 3, with zero lexical headroom; migration-surface budgets and their anchor remain unchanged at 16/1, while the registry and `RETIREMENT_ANCHOR` move together in this same commit, as the anchor mechanism requires; the retrodiction baseline is unchanged and the measured result returns to `(12, 15)` (`would_have_caught=12/15`, `caught_baseline=12/15`) once the budget accepts the measured counts.

## Economics and parity

Against the immutable base, the three retired function spans contain 267 physical
lines (269 including separating lines). Production Python is 185 lines smaller;
TS grows by 109; net production is 76 lines smaller. The primary-action module
shrinks 347 → 152 lines. The new Python facts codec is 77 lines plus its
separators; remaining Python changes are transport/result plumbing. The TS
helper contains 14 lines of local view decoders (`facts`, `object`, `text`,
`rows`, `count`), not another RPC surface. The bounded
companion refactor reuses `action_selection_context` rather than a second
admission-fact encoder. Broader admission, monitor and trace migration is deferred.

One untimed base/head execution and one logical-request trace per command,
with isolated synthetic SQLite fixtures and one or three delegation bindings:

| Command | Bindings | Crossings base → head | Stdout bytes base → head |
| --- | --- | --- | --- |
| `agent-context --phase before_plan` | 1 | 1 → 1 | 1885 → 1885 |
| `agent-context --phase after_delegate_result` | 1 | 3 → 3 | 1612 → 1612 |
| Same result phase with a Turn instance | 1 | 3 → 3 | 2594 → 2594 |
| `quota should-run` | 1 | 75 → 75 | 25520 → 25520 |
| `agent-context --phase before_plan` | 3 | 1 → 1 | 2247 → 2247 |
| `agent-context --phase after_delegate_result` | 3 | 3 → 3 | 1612 → 1612 |
| Same result phase with a Turn instance | 3 | 3 → 3 | 2594 → 2594 |
| `quota should-run` | 3 | 75 → 75 | 25904 → 25904 |

All eight normalized JSON outputs match after excluding timestamp keys ending
in `_at`, `context_id`, `event_id` and `staleness_seconds`. This is equal byte
count and normalized-content evidence, not literal equality of volatile fields
or a latency improvement claim. No persisted-format change or data migration.

## Validation and remaining gates

A pre-change synthetic fixture pins 182 cases: all modes, candidate-source
precedence, fallback and lane-count branches, empty/invalid source shapes,
booleans, numbers, nested values, whitespace, Unicode/non-BMP and truncation.
Native tests check those independently pinned instructions and the recovery
getter sentinel; Python checks codec views, non-JSON `str` and projection order.
Existing Python CLI/Host integration assertions are retained. Retire the pinned
Python payload/view fixture when its last codec consumer exits and native
invariant tests independently cover the same wording/source branches.

The oversized-backlog regression pins 22,000 rows through the public Python
projection: the lane instruction matches BASE, and rejected selection preserves
the BASE recovery command with `must_attempt` and `delivery_allowed` both false.
Encoded instruction facts stay below 16 KiB.

<!-- validation-results -->
Independent review's rule table found EQUIVALENT selection semantics with one
DIVERGENT row for oversized eager transport; the bounded first-selectable-row
carrier fixes it: facts for 22,000 rows shrink 2,354,852 → 959 bytes, with lane
and rejected-selection recovery outputs byte-identical to base and pinned in
tests. The fix passes 38 arbitration tests, 37 native tests and 309 Python
adapter tests, including 182-case typed-instruction parity. The `goal_boundary`
vocabulary budget decision moves Python/TypeScript 30/2 → 29/3 together with
`RETIREMENT_ANCHOR` in this commit; the smoke reports
`semantic-vocabulary-drift-smoke: ok`, architecture tests pass 128/128, and
retrodiction returns to the unchanged 12/15 baseline. The full TS suite passes
5,528 tests with 0 failures and 75 PostgreSQL skips. The original full Python
suite records 27,169 passed, 908 failed, 234 skipped and 8 errors; base replay
of its 147 failing files records 2,353 passed, 905 failed, 18 skipped and
8 errors, reproducing 913/916 distinct failure/error IDs under the same guard,
whose restrictions induced some failures. Of the three head-only failures,
the two architecture failures are repaired above; the third is
environment-induced because dashboard dependencies were installed mid-validation,
while base skips that test. The final-code 44-module sweep has identical sets
of 84 failures on base and head, zero new failures, 1,871 → 1,880 passes and
20 skips on each; these scoped runs do not establish a green full Python suite.
The final canary reports status: `passed`, ok: `true`, merge_gate_passed: `true`,
manual_holds: `0`, with 0 failures. The four interleaved benchmark runs
(25 timed runs and 3 warmups per command) retain equal bytes on all eight
commands and unchanged 1/3/3/75 crossings for both binding counts; every median
regression is within the stated 5% rule, so byte, crossing and median checks
pass without a latency-improvement claim. CI-only limits remain a real
PostgreSQL server, Node 22.22.3, Windows and browser lanes; PostgreSQL skips
are not passes. Main's inherited failures remain tracked by #7433–#7436 and
#7389; this validation does not resolve them.

Installed wheel, minimum Node, Windows, real PostgreSQL and full CI are not
qualified locally; PostgreSQL skips are not passes. The committed-head bilingual
check and premerge canary remain orchestrator/validation responsibilities. These
holds do not authorize broader authority/provider changes or weaker acceptance.
Rollback is a code-only revert; no state rewrite is needed.
