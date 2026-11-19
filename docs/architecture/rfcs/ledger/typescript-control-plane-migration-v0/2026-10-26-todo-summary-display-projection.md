# Todo summary-display projection retirement (2026-10-26)

| Boundary | Delivered evidence |
| --- | --- |
| Goal/source | Existing TypeScript migration T3/T4 transaction payoff; baseline `c6462f902`. One read-only summary-display slice, with no new capability, RPC operation, provider or runtime. |
| Observable gap | Python still selected summary/planning sources, normalized display declarations and assembled handoff displays before asking the typed quota owner to select lanes. |
| Single owner | `todos/context_projection.ts` now owns standalone summary compaction, source order/deduplication, ready-successor overlays and terminal-inclusive frontier preservation through the existing `todo.context.page`. `todos/read_codec.ts` reuses `handoffSource`; quota planning composes the display in process before lane decisions. Both complete planning decisions remain independent. |
| Retired symbols/callers | Python `_compact_todo_summary_item_record`, `_refs`, `_TEXT_FIELDS` and `handoff_context_source` are deleted. The bodies of `compact_todo_summary_items`, `todo_summary_source_items` and `todo_planning_source_items` become transport facades. `todo_index.py` sends raw record facts for both handoff stages. Existing `todo_summary.py` compact/open callers reach the same raw-fact adapter through `attach_todo_handoff_notes`; they require no new decision branch. `quota_selection.py` removes its display-source join and sends raw display facts. |
| Retained Python placement | `summary_item.py` retains public import signatures, source-key compatibility exports, empty/bounded transport, response validation and `todo_text_content_revision`. `handoff_note.py` retains generic JSON conversion with Python string/repr facts for tuples, sets, floats and other non-JSON scalars, batching/private snapshots, rejection propagation and mutable-row attachment identity. Existing handoff-gate source IO/compatibility remains. No Python decision fallback or per-field RPC is added. |
| Facade deletion condition | Delete the summary/handoff transport facades and their raw-fact request carriers after the last Python display, index, quota, management and planning callers consume the existing typed owners in process. Delete `todo_text_content_revision` only after its last compatibility caller exits. This does not retire the other context-page callers, delegation facts, Turn recovery or monitor-poll facades. |
| Formats/rollback | No persisted format, authority, CAS, writer fence, lease, receipt, provider selection or default changes. Rollback is a code-only revert; no data migration. |

## Cost and real-path evidence

Production source: `summary_item.py` 329 → 100 lines, `handoff_note.py` 150 →
112 lines; Python −273 net, TypeScript +157 net, production −116 net. The seven
production files total 242 added / 358 deleted lines. The related refactoring
pass reuses the existing handoff/read codecs rather than introducing another
owner or RPC leaf. Larger caller retirement is deferred to the existing exits.

One real-CLI trace per command and fixture size (N=1/N=3) gives:

| Command | Crossings base → candidate | Stdout bytes N=1 | Stdout bytes N=3 |
| --- | --- | --- | --- |
| `agent-context --phase before_plan` | 1 → 1 | 1885 → 1885 | 2247 → 2247 |
| `agent-context --phase after_delegate_result` | 3 → 3 | 1612 → 1612 | 1612 → 1612 |
| Same result phase with a Turn identity | 3 → 3 | 2594 → 2594 | 2594 → 2594 |
| `quota should-run` | 75 → 74 | 25520 → 25520 | 25904 → 25904 |

The estimated 73-crossing target is **not met**: the first (empty user) planning
call has no display rows and therefore no preceding display RPC to remove.
Only the nonempty Agent planning display crossing is retired. Do not remove a
second planning decision or absorb an unrelated caller to manufacture the
estimate. Fewer crossings do not certify latency improvement; the matched
25-run performance comparison remains external qualification.

The pre-cutover synthetic quota Todo rows and their Python display results are
pinned in `tests/control_plane_ts/fixtures/todo_summary_display.json`. An external isolated-runtime replay with Python/Node wall clocks pinned matches
all eight complete CLI outputs byte for byte, including `context_id`, against
the base Python owners. Independent TS cases cover nested-field precedence,
legacy identity, Unicode and credential
exclusion before truncation, bounded/public-safe blocked causes, open-advancement
hint visibility, normalized declarations, last-outcome precedence, ordered source
deduplication, explicit false readiness and terminal frontier fields. Python
retains real File/SQLite CLI recovery, mutation-identity, large/oversize transport
and fail-closed adapter tests, including tuple/set/float facts.

Focused validation: 89 TS tests pass; typecheck, changed-Python ruff, configured
mypy (17 files), diff whitespace and the semantic advisory pass. The requested
Python set reports 467 passed and one temporary-path depth skip, with four
existing legacy-path failures (three quota legacy-provider
cases and `test_absent_fence_preserves_legacy_path_without_starting_typescript`),
reproduced with the base Python owners in the same environment. These remain
failures, not passing skips. The configured mypy list does not cover the changed
Python modules. Real installed-wheel qualification, the minimum Node lane and
isolated PostgreSQL CI are unverified here; no PostgreSQL code is changed. This
source checkpoint does not certify full CI, installed delivery or a latency gain.

Review correction: summary display now preserves the canonical `decision_scope_outcomes[*].schema_version` as `todo_decision_scope_outcome_v0` by reusing the completion writer’s shared constant, replacing the unregistered `decision_scope_outcome_v0`; TS regression and File/SQLite `quota should-run` coverage were added.
