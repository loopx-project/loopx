# Lark Kanban Control-Plane Adapter

Status: prototype adapter contract v0.

This adapter models a LoopX long-task control plane in a Feishu/Lark Base
Kanban board. It is intentionally a thin projection over the same LoopX ideas:
todos, claims, user gates, handoff, evidence, and run history. It does not
replace the executor runtime, quota guard, or future daemon lease model.

The board is a status tracker and claim surface, not the task-planning engine.
It carries critical coordination facts so a long-running Codex session can see
available work, claim one row, resume its own row, and write back evidence. New
work still belongs to the LoopX todo lifecycle: split, successor, superseding,
and newly discovered tasks should be created with `loopx todo add`,
`loopx todo complete --next-*`, `loopx todo supersede`, or a typed planning
intake, then projected back to Lark with `sync-loopx-todos`.

## Authentication Boundary

The adapter passes Lark resource identifiers such as `base_token`, `table_id`,
`view_id`, and `record_id` to `lark-cli`. These identifiers remain visible in
command evidence because they are useful for operating and auditing the board;
the adapter does not treat them as AK/SK secrets.

Authentication stays inside the selected `lark-cli` identity and its local auth
store. Adapter-generated commands must never contain application auth material
such as AK/SK or app-secret arguments. The command runner rejects those inline
secret-bearing options before a subprocess starts. Use `lark-cli auth
login/status` to manage authentication instead of extending the Kanban config
or command payload with auth material.

## Mapping

| LoopX concept | Lark Base field |
| --- | --- |
| Todo | One task row. `Task` is the visible title. |
| Todo status | `Status` single select: `Todo`, `Claimed`, `Running`, `User Gate`, `Blocked`, `Review`, `Done`. |
| Claim | `Claim` single select plus `Claimed By` text. |
| User gate | `Status=User Gate` plus concrete question in `User Gate`. |
| Handoff | `Handoff` text. |
| Evidence | `Evidence` text. |
| Run history | `Run History` compact append-style text. |
| Scope | `Scope` text, advisory in v0. |
| Quota | Omitted in v0; the prototype assumes no quota limit. |
| Worker launch | `Worker Command` and `Workdir`, consumed by heartbeat. |
| Issue-fix outcome | One stable `Work Item Type=Issue Fix` row with `Repository`, `Issue`, `Pull Request`, `Route`, `Stage`, `Validation`, `Outcome`, and bounded multi-select `Context Tags`. |

The Kanban view groups by `Status`, so the board is the operator-facing
control surface. Agent workers use the filtered `Worker Queue` view.

Issue-fix execution todos and issue outcomes deliberately remain different
rows. Todos answer what should happen next. The outcome row answers what has
happened to one issue and stays keyed by repository plus issue across PR
lifecycle changes. It is a read model derived from existing LoopX issue-fix
state, not a second workflow ledger.

## Operator View

The human-facing Kanban card should stay deliberately small. Keep only these
fields visible on the card:

- `Task`
- `Claim`
- `Priority`
- `User Gate`
- `Evidence`
- `Status`

All other fields remain in the record detail and `All Tasks` grid. This keeps
the first page light enough to scan while preserving complete task context for
agents, handoff, audit, and recovery.

Issue work has two dedicated views so it does not disappear inside the todo
queue:

- `Issue Fix Outcomes`: grid view filtered to `Work Item Type=Issue Fix`;
- `Issue Fix Kanban`: Kanban view with the same filter, grouped by `Stage`.

Their visible fields are `Task`, `Repository`, `Issue`, `Pull Request`,
`Route`, `Stage`, `Validation`, `Outcome`, `Context Tags`, and `Status`. Context
tags expose stable route, stage, reproduction, validation, and focused-change
signals without copying free-form evidence. This keeps the existing todo Kanban
compact while making per-issue state and output directly scannable.

`lark-cli` 1.0.56 exposes `base +view-set-visible-fields`, so
`lark-kanban setup` writes this compact Kanban card field list directly. Verify
with:

```bash
lark-cli base +record-list \
  --base-token <base> \
  --table-id <table> \
  --view-id Kanban \
  --offset 0 \
  --limit 10
```

The returned `fields` array should be the compact operator set above. The
record detail still contains the full schema.

## Trigger Model

Direct Base-to-local-agent triggering requires a reachable callback, local
daemon bridge, or product-side event subscription that can wake an edge worker.
The current v0 prototype therefore uses a heartbeat:

1. Worker polls `Worker Queue`.
2. Worker chooses one `Todo` row whose `Claim=Unclaimed`, or resumes its own
   existing `Claimed`/`Running` row.
3. Worker writes `Status=Claimed`, `Claim=Agent`, `Claimed By=<agent_id>`.
4. Worker optionally executes the row's `Worker Command`.
5. Worker writes compact `Evidence`, `Run History`, `Handoff`, `Last Error`,
   `Last Result Code`, and final `Status`.

This matches the cloud-to-edge bring-up shape: the cloud board is the shared
coordination plane; a managed agent wrapper or daemon remains responsible for
starting and supervising the edge executor. The prototype command is:

```bash
python3 -m loopx.cli lark-kanban heartbeat \
  --base-token <base-token> \
  --table-id <table-id> \
  --agent-id codex-kanban-worker \
  --execute-lark \
  --execute-worker \
  --allow-command-prefix "codex exec"
```

For repeatable local verification, use a deterministic worker command instead
of `codex exec`:

```bash
python3 -m loopx.cli lark-kanban heartbeat \
  --base-token <base-token> \
  --table-id <table-id> \
  --agent-id codex-kanban-worker \
  --execute-lark \
  --execute-worker \
  --allow-command-prefix "python3"
```

## Task Spawning Model

Kanban claim loops should not invent new rows by directly editing the Base.
When a worker discovers follow-up work, it should classify the need first:

- same-slice continuation: keep evidence in the current row until review;
- real successor: complete the current LoopX todo with `--next-agent-todo` or
  `--next-user-todo` plus an explicit
  `--next-user-task-class user_action|user_gate`;
- replacement or narrower split: use `todo supersede --next-agent-todo`;
- strategy-heavy fan-out: run `complex_request_intake_v0` to create a small
  typed todo batch.

After that writeback, `sync-loopx-todos` updates the status tracker and derives
issue-fix outcome rows from existing goal domain state. This keeps task identity,
`todo_id`, gates, claims, issue/PR lifecycle state, and successor metadata in
LoopX while letting Kanban remain the operator-visible tracker for current work
and delivered outputs.

## Setup And Reuse

A saved `.loopx/lark-kanban.json` file is a reusable destination binding, not
an automation switch. Normal sync stays user-driven through
`lark-kanban sync-loopx-todos`. A goal may explicitly opt into best-effort
heartbeat refresh with:

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --lark-kanban-heartbeat-sync \
  --execute
```

That opt-in does not create a board, authenticate Lark, or turn sink delivery
into a gate. It projects one `post_writeback_actions` entry through
`quota should-run.goal_boundary` and `interaction_contract.cli_channel` only
after material state changes; failures remain nonblocking and cannot preempt
runnable P0 work. The host automation prompt contains no Kanban-specific
switch or command. Disable the behavior with
`--no-lark-kanban-heartbeat-sync`.

The recommended path is `setup`, using user identity by default. It preflights
`lark-cli`, auth, and the required Base shortcuts, then either reuses the local
`.loopx/lark-kanban.json` board config or creates a new Base/table:

```bash
python3 -m loopx.cli lark-kanban doctor
lark-cli auth login --domain base --recommend
python3 -m loopx.cli lark-kanban setup --base-name "LoopX Kanban POC" --execute
python3 -m loopx.cli lark-kanban sync-loopx-todos --goal-id <goal-id> --sink-visibility shared --execute
```

The local config stores the reusable Base token, table id, view ids, identity,
and synced `goal_id:todo_id -> record_id` mappings. The file lives under
`.loopx/`, which is gitignored.

Running `setup --execute` against an existing board is also the schema
reconciliation path. It lists current fields, creates only missing fields,
creates missing issue-fix views, and then reapplies filters, grouping, and
visible-field configuration. Existing todo rows and stable outcome record ids
are reused.

To use someone else's shared board, store its URL or IDs:

```bash
python3 -m loopx.cli lark-kanban use --base-url "<shared-base-url>"
python3 -m loopx.cli lark-kanban config
python3 -m loopx.cli lark-kanban heartbeat --execute-lark --sink-visibility shared
```

`sync-loopx-todos` reads the goal active state from the LoopX registry and
upserts open user/agent todos plus derived issue-fix outcome rows into the board.
User todos become `User Gate`
cards; claimed agent todos become `Claimed`; blocked/done todos map to
`Blocked`/`Done` when included. Synced LoopX todos intentionally leave
`Worker Command` and `Workdir` empty unless a task row was explicitly authored
as a worker-launch row; the shared board must not receive raw local checkout or
active-state paths.

`sync-loopx-todos` and `heartbeat` default to `--sink-visibility shared`. Every
record writer removes canonical provider-credential shapes before serialization,
including on an `owner-only` board. Shared visibility additionally applies the
local-path, private-link, and private-reference redaction boundary recursively to
nested record keys and values while preserving only structurally valid
record-link IDs. It recognizes Unix, drive-letter, UNC, and extended Windows
paths, plus credential assignments that use compound environment-variable names,
credential-bearing URL userinfo, and terminal control sequences around a token.
Configured Explore whiteboards use the same visibility boundary on the final
rendered source, and shared Explore stage documents sanitize lane labels before
their `docs +update` content is built. Private-key blocks are removed in full
rather than leaving their body behind. The automatic issue-fix Explore
synchronization uses the same shared boundary.

`sink_visibility` is a caller declaration; the adapter cannot verify the remote
Base's effective access control. Redaction is shape-based, not a complete
data-loss-prevention system: it does not classify every arbitrary high-entropy
value without a provider prefix or credential label. Never paste credentials
into board fields, and rotate any credential that may already have reached shared
storage rather than relying on later clean-up.

Issue outcomes are derived, not persisted separately. Every feasibility row is
projected; a PR lifecycle row enriches it only through an explicit matching
`repo` and `issue_ref`. This avoids title/branch guessing and makes the issue
grid and stage Kanban part of the default sync path. `--limit` bounds active
todo rows only; it never truncates the derived issue-outcome source projection.
The sync receipt publishes this split as `limit_policy` so a successful command
cannot silently imply completeness after dropping newer outcomes.

Normal projection sync is intentionally non-destructive: rows missing from a
filtered, limited, or newer payload are never deleted implicitly. When a
caller owns a complete stable `source_id` namespace, it may request an explicit
preview-first reconcile:

```bash
python3 -m loopx.cli lark-kanban sync-projection \
  --projection-file complete-projection.json \
  --include-done \
  --reconcile-source \
  --source-snapshot-complete

# Run only after reviewing source_reconcile.remote_orphans and local mappings.
python3 -m loopx.cli lark-kanban sync-projection \
  --projection-file complete-projection.json \
  --include-done \
  --reconcile-source \
  --source-snapshot-complete \
  --execute
```

Reconcile refuses agent-filtered input, a row limit that truncates the source,
source-id mismatches, omitted done rows, and incomplete remote pagination. It
deletes only remote records whose synthetic todo id belongs to that exact goal
and source namespace, then removes corresponding or already-missing stale local
`goal_id:todo_id -> record_id` mappings. An idempotent retry plans zero deletes.

## CLI Surface

```bash
python3 -m loopx.cli lark-kanban schema --format json
python3 -m loopx.cli lark-kanban doctor
python3 -m loopx.cli lark-kanban setup --base-name "LoopX Kanban POC" --execute
python3 -m loopx.cli lark-kanban use --base-url "<shared-base-url>"
python3 -m loopx.cli lark-kanban config
python3 -m loopx.cli lark-kanban sync-loopx-todos --goal-id <goal-id> --sink-visibility shared --execute
python3 -m loopx.cli lark-kanban sync-projection --projection-file <complete.json> --include-done --reconcile-source --source-snapshot-complete
python3 -m loopx.cli lark-kanban plan-create --base-name "LoopX Kanban POC"
python3 -m loopx.cli lark-kanban create-board --base-name "LoopX Kanban POC" --execute
python3 -m loopx.cli lark-kanban seed-task --base-token <base> --table-id <table> --sink-visibility shared --execute
python3 -m loopx.cli lark-kanban seed-cases --base-token <base> --table-id <table> --sink-visibility shared --execute
python3 -m loopx.cli lark-kanban heartbeat --base-token <base> --table-id <table> --execute-lark --sink-visibility shared
```

`setup`, `create-board`, `seed-task`, `seed-cases`, `sync-loopx-todos`, and
`heartbeat` are dry-run unless their explicit execute flags are set. Worker
execution has its own gate, `--execute-worker`, and an allowlist gate,
`--allow-command-prefix`.

`seed-task` and `seed-cases` default to `--sink-visibility shared`, like the
operator sync and heartbeat paths. They apply the same recursive credential and
local-path boundary before the first row is written; pass `--sink-visibility
owner-only` only when the target board's ACL is independently verified.

`seed-cases` creates one UX optimization task plus four feasibility cases:

- a `notes.zaynjarvis.com` LoopX architecture/decision-note lane;
- a P1/P2 human gate timeout lane with default fallback;
- a cross-session compact-memory lane through external memory;
- a quality-vs-token-and-attention-cost eval lane.

## Review Boundary

The prototype deliberately keeps raw agent transcripts, credentials, local
private paths, and hidden benchmark material out of Lark rows. `Evidence` and
`Run History` should contain compact public-safe summaries or artifact
pointers. Every record writer, including `seed-task`, `seed-cases`,
`sync-loopx-todos`, projection sync, and heartbeat writeback, applies
credential-shape redaction before serialization; shared visibility additionally
redacts local paths and private references from nested keys and values.
`sync-loopx-todos` must not populate `Workdir` from the local active state path.
If a real worker needs to store detailed logs or local launch context, store
them in the worker's normal trace surface and write only a compact pointer back
to Base.

## Validation

Run the fixture smoke:

```bash
python3 examples/lark-kanban-control-plane-smoke.py
```

The smoke proves the schema, board command plan, task selection, soft claim,
worker execution, evidence writeback, handoff, and CLI fixture path without
requiring live Lark credentials.
