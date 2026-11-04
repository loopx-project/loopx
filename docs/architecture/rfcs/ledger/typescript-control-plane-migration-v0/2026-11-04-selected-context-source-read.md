# Selected Todo source read in the existing context transaction

The accepted request is to retire Python bridge work without creating parallel semantic owners. This slice moves the complete selected-Todo source read and its source-fact construction into the existing `work_item.context.project` request. Goal document, User-scope and acceptance/oracle IO remain Python providers.

| Boundary | Result |
| --- | --- |
| Before | Python reads the complete canonical collection, selects one active Todo, adds its content revision, then sends it to the TS context projector. |
| After | `readAndProjectInteractionWorkContext` uses the existing canonical snapshot reader in-process, then invokes the unchanged context validator/projector. |
| Retirement | The Python selected-Todo branch and source-reader imports are removed. The containing adapter remains for its real Goal/acceptance IO callers. |
| Integrity | The same existing-only store route, complete snapshot validation, page/revision fencing, archived/missing refusal, raw body, normalized content revision, status and claim checks remain. There is no Markdown fallback. |
| Transport | No RPC method is added. The existing context projection receives `source_context`; the canonical read retains its existing 15-second budget. Source delivery still uses two coarse context requests around the remaining Python providers. |
| Feature off | A request without a selected-Todo required read does not open a provider. Prepared-source native callers retain the existing pure projection. |
| Recovery | A changed selected body fails the public CLI on both File and SQLite. Bypassing the typed source guard makes both independent regression cases fail. |
| Authority | This read grants no claim, lease, execution, acceptance or write authority. No persisted format or live Goal data is migrated. |

## Evidence and limits

The immutable initial baseline is `ecbf905402ccfd9e4d75b2b963b52376b4ef7332`; later-main integration and final exact-head evidence belong in the PR. Existing failures in four selected-work tests reproduce on that baseline and concern the prior capture/output layout. Their repair belongs to existing PR #7430; this slice does not relax those assertions.

Durable checks live in `tests/control_plane_ts/interaction_context_readback.test.ts` and `tests/control_plane/test_selected_work_requirements.py`. They cover complete requirements/notes, source revision, changed/archived/missing/corrupt/unavailable state, no provider creation, feature-off behavior and unchanged canonical data. Wheel and sdist execution, matched full-CLI costs and actual source revision are recorded separately in the PR; test counts or fewer requests do not prove whole-Goal outcome value.

The shared normalized-text revision helper serves the existing canonical display codec and the new source read. No policy is reimplemented in Python. Retire the remaining Python context adapter only when its document, User-scope and oracle providers have real native callers; removing a file based on its language is not the exit criterion.
