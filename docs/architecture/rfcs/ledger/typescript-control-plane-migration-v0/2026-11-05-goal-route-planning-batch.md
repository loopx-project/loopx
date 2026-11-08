# Goal-route hints in the existing planning batch

## Boundary and compatibility

This slice moves the complete read-only Goal-route projection from Python into
`work_items/goal_route_hint.ts`, consumed by the existing
`work_item.action_portfolio.project` batch. It retires the Python route builder
and task-override composer together with the production caller switch. The
existing `quota.run_decision` owner still produces task-orchestration overrides;
this slice consumes that result rather than implementing its policy again.

The internal `quota_planning_packet_request_v1` carries independently nullable
planning and route contexts. Its result is `quota_planning_packet_v1`. The
coordination contract JSON registers both identities, and the existing generator
owns Python/TypeScript constants. Existing v0 requirements and result shape keep
their meaning. The external nested `goal_route_hint_v0` does not change.

Python captures route facts at the former builder position, after Agent lane,
frontier and recommendation selection. The existing planning call projects the
hint before monitor-only, unadmitted-selection, settlement and cadence cleanup.
There is no later scheduler/serialization refill. Source list absence remains
separate from an authoritative empty list; an empty frontier object remains a
frontier. Candidate order, selection-before-display truncation, local-source
counts, current-action priority, canonical frontier action priority and task
patch omission semantics retain their previous contracts.

Python retains existing Todo/text codecs and the shared
`actions_are_projection_aligned` owner as named compatibility facts. It does not
select route candidates, deduplicate, count or decide routes. Retire these
compatibility facts only after their shared source owners and final Python
quota assembly callers have native replacements. No provider reads, synthetic
inventory or Goal document scanning are introduced for route-only requests.

The hint remains advice nested inside the existing quota read model and its
`projection_envelope`, not a new authoritative read API. It grants no execution,
claim, lease or write permission, preserves the durable Goal Next Action and
introduces no persisted-state migration. No leaf RPC or Python fallback owner is
added. Rollback restores the preceding code/artifact and fingerprint; it does
not rewrite Goal state.

## Costs and qualification

Initial implementation used `d39662d79784e7afa9dbc844f61452a64cb92c1c`; the
earlier review base was `2c24d5d102dc35c417b3ce49e25096aa37e1642b`. Final
matched CLI evidence uses integration base
`2ce6a5dcd8a5396f5d60312f37958e1b05d529ca` and candidate
`db7f364385bb4418ea223623f0d1ffbd9745a1bd`.

| Local planning/route segment | Before | Candidate design |
| --- | --- | --- |
| Planning already required | One RPC plus Python route rules | One existing batch RPC |
| Route only | No planning RPC plus Python route rules | One route-only batch RPC |
| Neither required | No RPC | No RPC |

This is an ownership consolidation, not a claimed latency improvement. Measure
both execution and route-only wait/read branches. Keep the existing warm
non-persistent p95 target below 2 ms and complete-CLI owner-review gates for
p95 regression over 5% or unexplained added 25 ms. Preserve wire, coverage,
fixture and performance budgets; use the complete-snapshot transport rather
than truncating source rows. Do not retain a production dual-owner fallback if
performance fails: optimize the batch or hold/revert the cutover.

### Verified results and remaining holds

- **Passed:** generated-contract freshness, 29 contract/catalog tests, both
  native-path canary selections, 214 focused Python tests, and all 12 matched
  real CLI parity cases across SQLite/File at the revisions above.
- **Passed, focused semantic evidence:** 1,200 deterministic synthetic
  Python-baseline versus codec/TS cases; all three independent mutations were
  killed (nonempty-list fallback for executable candidates, current/frontier
  priority reversal, and nonempty-list fallback for handoff gates). This is not
  additional CLI, backend or performance evidence.
- **Passed:** premerge, with an inherited complexity advisory. The full native
  suite recorded 6,105 passed, 30 failed and 77 skipped. All 30 failures passed
  individually with isolation or a larger temporary filesystem after
  runtime/filesystem failures; this does not recast the original aggregate run
  as a clean pass.

The 20-sample full-command measurements retain these threshold exceedances:

| Case | Base p95 | Candidate p95 | Change |
| --- | --- | --- | --- |
| SQLite current JSON | 879.85 ms | 975.16 ms | +10.83%, +95.31 ms |
| SQLite current Markdown | 851.66 ms | 877.88 ms | +26.23 ms |
| File durable Markdown | 583.29 ms | 618.80 ms | +6.09%, +35.51 ms |

The other nine cases improved. A separate instrumented 20-pair SQLite current
JSON cohort measured full-command p95 723.73→694.55 ms and planning-RPC p95
1.294→1.797 ms. These distinct observations do not cancel the original
threshold exceedances or prove their cause. **Performance qualification remains
held for owner review**, not passed; no speedup is claimed.

Cold-start qualification, clean wheel/sdist installation, PostgreSQL and Host
coverage remain **not_yet_proven**. Keep full-command p50/p95, logical IPC,
wire/output bytes and persisted-state readback distinct from projection timing.
Record Python rules removed, compatibility code added and net production changes
separately from tests, generated files and docs. Later source changes need fresh
exact-head verification. These results grant neither merge nor deployment
authority.
