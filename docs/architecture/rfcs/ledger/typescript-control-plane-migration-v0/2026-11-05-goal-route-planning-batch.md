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

The verified integration base is `d39662d79784e7afa9dbc844f61452a64cb92c1c`.
The earlier review base was `2c24d5d102dc35c417b3ce49e25096aa37e1642b`;
acceptance must use the integration base and immutable candidate revision.

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

At this checkpoint, generated-contract freshness, 29 contract/catalog Python
tests and native-path selection of both affected canary profiles are **passed**.
The working-tree bilingual mirror advisory passes; the staged/exact-head gate
remains pending. Final static
checks, native rules and v0/v1 negatives, independent mutations, public CLI
cleanup regressions, matched immutable baseline/head File and default-SQLite
readbacks, clean wheel/sdist installs, and costs are **not_yet_proven** until
recorded against the final candidate. Record p50/p95, cold starts, logical IPC,
wire/output bytes and persisted-state readback separately; record Python rules
removed, compatibility code added and net production changes separately from
tests, generated files and docs. PostgreSQL and Host qualification are not
claimed. Source implementation alone does not certify end-to-end acceptance,
performance, exact-head review or maintainer merge authority.
