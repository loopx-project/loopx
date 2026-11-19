# Todo summary-display follow-up qualification (2026-10-27)

This entry supplements the [original retirement record](2026-10-26-todo-summary-display-projection.md).
The production boundary remains summary display through `todo.context.page` and
in-process quota planning; both complete planning decisions remain necessary.
There is no new provider, format, permission or RPC owner.

## Preserve the earlier evidence

The independent review of head `c242ca5f439c269c313329c883299a28d95a55dc`
against `be1dc37a008be7017b64b622f653b6707df5c544` found:

- the registered `todo_decision_scope_outcome_v0` correction passed canonical
  File/SQLite source and installed wheel/sdist checks;
- one SQLite CLI fixture used 83 → 82 requests, including 22 → 21
  `todo.context.page` calls. Its workload differs from the original N1/N3
  75 → 74 trace; neither trace permits removing another planning decision;
- a fixed 32-pair CLI cohort had base/head p50/p95 336.97/521.98 and
  345.96/836.79 ms, a 60.31% candidate p95 increase;
- the predeclared 64-pair rerun had 301.56/400.98 and 305.88/391.08 ms.
  This later result does not erase the earlier failed cohort. The difference
  did not establish a repeatable regression or explain the earlier tail;
- cold startup, warm typed requests and idle/burst daemon RSS were still missing.
  Python thread profiling did not measure Node CPU or establish tail causality.

These historical cohorts keep their original status. They are not relabelled
as passes, and the default SQLite limits remain unchanged.

## Rebase and real recovery

The follow-up baseline is `ff8d9932294e9004b9ffeb2fb473d00354397eb8`.
Six File/SQLite recovery test cases initially stopped on
`canonical_authority_required`, before their intended assertions. The fixture
now initializes the real provider from authored source facts **before** calling
the production CLI; it does not restore a legacy read fallback. All eight
receipt/next-action cases pass, including version preservation, rejection of
wrong Todo step updates, legitimate original-Turn settlement and replay.
The earlier six failures remain recorded as fixture failures, not proof of
previously passing recovery behavior.

## Final-head measurement contract

[PR #7411](https://github.com/loopx-project/loopx/pull/7411) records the immutable
final head, execution commands, fixture fingerprint, environment and results.
Its bounded offline qualification uses the same synthetic canonical SQLite
Todo and interpreter on both source revisions, with no active Goal mutation:

- 24 managed-process cold-start pairs, distinguishing warm filesystem/compile
  cache from a fresh process;
- 64 complete `quota should-run` CLI pairs with alternating AB/BA order, all
  samples retained and nearest-rank p50/p95;
- 256 warm typed requests and 256 complete quota-planning projections per
  revision, separating transport from the Python facade;
- idle, sampled burst and post-burst daemon RSS over 2,000 bounded projections;
- real CLI identity/result assertions, focused TS/Python coverage and packaged
  readback, reported separately from timing.

Do not overlap owned heavy builds/tests with timed cohorts. Keep private raw
captures outside Git and publish only synthetic aggregates. A single synthetic
workload does not establish sustained large-Goal performance. Any miss retains
an owner-review hold; a faster microbenchmark cannot waive a full-CLI failure.

## Rebase onto the subsequently merged main

The final integration base advances to
`17b2a0f9b818dc6e6fe77ccc567b20ff91b3bc40`. Main now includes #7459's
canonical fixture repair and changes to the real quota/context path. The
conflict resolution keeps that shared fixture repair instead of duplicating it;
this PR retains the File/SQLite receipt-version regression. The earlier
`ff8d9932294e9004b9ffeb2fb473d00354397eb8` comparison and
`e7a54b8f32a648124133b02e11404b75a421fdac` head remain historical evidence.
The final PR reply records fresh exact-head correctness, package and matched
performance results for the new base. No earlier cohort is rewritten.
