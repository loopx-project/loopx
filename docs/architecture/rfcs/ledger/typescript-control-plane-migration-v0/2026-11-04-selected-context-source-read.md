# Selected Todo source read in the existing context transaction

The accepted request is to retire Python bridge work without creating parallel semantic owners. This slice moves the complete selected-Todo source read and its source-fact construction into the existing `work_item.context.project` request. Goal document, User-scope and acceptance/oracle IO remain Python providers.

| Boundary | Result |
| --- | --- |
| Before | Python reads the complete canonical collection, selects one active Todo, adds its content revision, then sends it to the TS context projector. |
| After | `readAndProjectInteractionWorkContext` uses the existing canonical snapshot reader in-process, then invokes the unchanged context validator/projector. |
| Retirement | The Python selected-Todo branch and source-reader imports are removed. The containing adapter remains for its real Goal/acceptance IO callers. |
| Integrity | The same existing-only store route, complete snapshot validation, page/revision fencing, archived/missing refusal, raw body, normalized content revision, status and claim checks remain. There is no Markdown fallback. |
| Transport | No RPC method is added. The existing context projection receives `source_context`; the canonical read retains its existing 15-second budget. The existing context plan/project phases remain; the remaining Python providers keep their own calls. The selected-Todo snapshot no longer crosses separately. |
| Feature off | A request without a selected-Todo required read does not open a provider. Prepared-source native callers retain the existing pure projection. |
| Recovery | A changed selected body fails the public CLI on both File and SQLite. Bypassing the typed source guard makes both independent regression cases fail. |
| Authority | This read grants no claim, lease, execution, acceptance or write authority. No persisted format or live Goal data is migrated. |

## Evidence and limits

The immutable initial baseline is `ecbf905402ccfd9e4d75b2b963b52376b4ef7332`; later-main integration and final exact-head evidence belong in the PR. Existing failures in four selected-work tests reproduce on that baseline and concern the prior capture/output layout. Their repair belongs to existing PR #7430; this slice does not relax those assertions.

Durable checks live in `tests/control_plane_ts/interaction_context_readback.test.ts` and `tests/control_plane/test_selected_work_requirements.py`. They cover complete requirements/notes, source revision, changed/archived/missing/corrupt/unavailable state, no provider creation, feature-off behavior and unchanged canonical data. Wheel and sdist execution, matched full-CLI costs and actual source revision are recorded separately in the PR; test counts or fewer requests do not prove whole-Goal outcome value.

The shared normalized-text revision helper serves the existing canonical display codec and the new source read. No policy is reimplemented in Python. Retire the remaining Python context adapter only when its document, User-scope and oracle providers have real native callers; removing a file based on its language is not the exit criterion.

## Final source proposal qualification

Integration base: `d29ec8bf2a105aac24629bcbf3dfb7c97aea51fe`. Source candidate: `c137daecc56a5f1784097742b212d26b27f83892` (subsequent documentation updates do not change the four production files).

- Python adapter: +7/-35 lines, net -28. Total production diff: +63/-37, net +26. This is source/transport consolidation, not a claim that previously Python-owned domain policy moved.
- Source checks: 138 focused/related Python tests and 93 native tests pass; four older capture-layout failures remain baseline evidence. Mutation of the source guard breaks both real File/SQLite body-change regressions.
- Fresh wheel and sdist: File and SQLite command readbacks pass in four isolated installation cases, with source-file hashes checked.
- The actual `loopx` entrypoint on SQLite, with 500 historical records and 30 alternating matched pairs, has p50 647→657 ms and p95 779→799 ms. The default entrypoint stays within the declared 5%/25 ms regression bounds; no speedup is claimed.
- The compatibility `python -m loopx.cli` entrypoint has paired p95 1309→1486 ms. This remains an unresolved performance hold. The observed imports/parser hotspots do not prove the tail's cause; raw results and earlier samples are retained. Complete cost/Host adoption qualification is not certified by this proposal.
- Complete inline work context matches in all 30 default-entry pairs. Event IDs are identical with only the event-clock input fixed. Differences in collection clocks and their computed ages remain in raw observations; no ownership, body, field, refusal or identity is normalized.

**Author implementation is complete; merge qualification remains held for performance review and a fresh exact-head review.** No merge, deployment, authority migration or persistent-state deletion is performed.

### Later upstream capture integration

Candidate `ff4e94459` integrates latest base `ce4cfc1ce`. Its four changed production files remain byte-identical to `c137daecc`. Upstream now returns capture navigation; the existing product reader verifies and opens the complete current projection. Actual File/SQLite entrypoint readbacks on this base preserve complete work context and event identity; logical Effect calls are 74→73 in these newer observations.

Six historical direct-stdout assertions fail on both latest main and candidate because they still expect an inline `should_run` field. Both selected runs have 2 pass /6 fail, with the same identities and failure boundary. Those failures remain evidence. Earlier installed/cost measurements retain their stated revision; latest-main full cost and Host qualification remain held.
