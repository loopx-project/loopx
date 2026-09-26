# Team Workspace (local preview)

An optional local host for AI-led Goals. LoopX owns Goal/Todo/Monitor state;
this package owns human capability profiles, requests, evidence, scoped
corrections, and host scheduling. It does not replace LoopX's authority store.

Run with Python 3.11+, Node 22.6+, a source checkout of LoopX and an authenticated
Codex CLI:

```sh
python packages/team-workspace/server.py --data /path/to/private/runtime --codex-bin /path/to/codex
```

Open `http://127.0.0.1:8778`. Each Goal has an isolated execution directory.
Add `--native-dashboard-port 8779` to expose the original LoopX Goal chat using
the same registry, with a link from each Goal detail page.
The server must remain running for automatic advancement and monitor wakeups.
Only localhost is supported; this is not a multi-user authentication server.

## Ownership and placement

Provider: `team-workspace`, optional co-located host application. No new built-in
capability is registered. LoopX's shipped CLI owns all Todo lifecycle mutations,
quota decisions and monitor observations. SQLite contains only host configuration,
interaction records and replayable result journals, never a competing Todo status.
The UI adapts the team-task team/needs-me/member workflow. Optional AirJelly reads
use its selected local instance and the authorized `listEvents` interface.

## Behavior

- Goal and member creation start as natural-language conversations. Each model
  turn returns an editable draft, the smallest remaining information gap, and
  up to five contextual GenUI options. Options are never preselected or treated
  as confirmed facts; a separate user click creates the Goal or saves the member.
- Goals have an objective, acceptance criteria, horizon and boundaries. The AI
  keeps a short rolling frontier and reviews progress after each verified task.
- The host checks LoopX quota before executing; task result validation runs in a
  separate read-only Codex invocation. Replies alone do not complete requests.
- Human requests have `supplement`, `execute`, or `judge` semantics. Only their
  linked task waits. Judgment requires an explicit human decision, not activity.
- Human corrections are scoped by Goal, member and skill, remain editable and
  are supplied to subsequent planning/routing. This is retrieval-based learning,
  not model fine-tuning.
- Monitors use canonical LoopX cadence/due/expiry metadata. File observations are
  verified locally; model interpretation can trigger a new task. HTTP/news/social
  providers can be added later. Unchanged observations do not invoke a model.
- AirJelly is opt-in in Settings. Reads are incremental, overlap recent windows
  to discover edits, and deduplicate by event identity and content revision.
- Pause prevents new model work. Interrupted executor runs are verified before
  being retried. Validated results are journaled before lifecycle writeback.

## Validation

```sh
python packages/team-workspace/test_workspace.py
python packages/team-workspace/test_http.py
```

The integration suite uses disposable real LoopX registries. Live Codex and
AirJelly qualification are separate from deterministic tests; report unavailable
services explicitly. Shutdown with Ctrl-C, then restart with the same `--data`.
Delete only the chosen private data directory to reset this preview.
