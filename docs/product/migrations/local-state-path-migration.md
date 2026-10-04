# Local State Path Migration

New LoopX installations use `$HOME/.loopx/registry.global.json` and
`<project>/.loopx/goals/<goal-id>/ACTIVE_GOAL_STATE.md`. An existing installation
with state only under `$HOME/.codex/loopx` keeps using that single legacy
runtime route, including extension activation or machine configuration created
before the first global Goal registry. An empty directory alone does not select
a route. If both default roots contain state, implicit selection refuses to
create or silently read a second authority. Registered project Goals keep their declared `state_file`
until explicitly migrated. `loopx doctor --format json` reports the selected,
legacy, and target routes under `local_state_route`.

When HOME is also a connected project, its validated `.loopx/registry.json`,
registry lock files and explicitly declared project Goal directories are project
state. They do not select a second machine runtime. Extension activation,
machine configuration and unclassified contents in that root still require the
normal machine-state route decision.

Host rollout observations under `runtime/goals/*/rollout-event-log.jsonl`,
their locks, machine-scoped Lark consumer locks and a lone global-registry lock
do not declare a second machine runtime. Discovery preserves these files;
unknown contents or redirected entries still require an explicit route. Real
configuration, extension activation, Goal state and registries retain the
conflict protection above.

`doctor` checks a supplied `--runtime-root` or the registry's declared
`common_runtime_root`; `local_state_route.status=configured` names that scope
and `default_status` retains the implicit discovery result. `update apply`
keeps the selected registry and runtime through authority-format qualification,
installation, doctor and extension readback. Status/Chat `--global-registry`
uses the configured registry's runtime instead of rediscovering a default.
None of these operations migrates directories or resolves a real implicit
conflict by choosing one of two authorities.

This migration is distinct from `migrate-state`, which imports the older Goal
Harness product state. It moves only the LoopX runtime root and Goal directories
declared by project registries. Other files under `.codex` remain in place.

## Preview and execute

Stop LoopX workers, heartbeats, status servers, and other writers on this machine
before executing. Older versions do not honor a migration-wide writer fence.
Keep them stopped until status and project readback succeed.

The migration CLI does not schedule optional machine usage observations:
their state is part of the runtime being moved, so background writes would
invalidate the preview or rollback receipt. This applies to preview, execution
and rollback; other writers still need to remain stopped.

Run the preview:

```bash
loopx --format json migrate-local-state
```

Review `source_runtime_root`, `target_runtime_root`, every `entries` path,
`project_count`, and `goal_directory_count`. The command validates each
registered project, state file, source registry, symlink boundary, and target
collision without writing. It stops if a registered legacy route is missing or
different from the default. Repair or retire stale registrations separately;
do not copy a guessed state directory into the target.
An explicitly configured Goal `state_file` stays where its owner placed it, but
its project `.loopx/registry.json` must still have no symlink or junction in
the directory route. Execution rechecks that route before copying, rewriting,
and restoring the registry.
The default backup parent and any explicit `--backup-dir` must have no symlink
or junction in their existing ancestors. Preview rejects such a route, and
execution checks it again before creating and copying the private backup.
Choose a real directory when a backup path is rejected.
Goal destinations use the same redirect check: preview rejects existing
symlink or Windows junction/reparse-point ancestors, and execution rechecks
the route before moving a Goal directory.
The legacy runtime tree and Goal source directories are checked before backup
and rename, and rollback rejects redirected legacy destinations or backup
snapshots. Stop other writers for the full preview, execution, and rollback.

To apply the exact preview, use its `plan_id`:

```bash
loopx --format json migrate-local-state \
  --execute --expected-plan-id <plan_id-from-preview>
```

The plan id binds the source contents and paths. A changed source requires a new
preview. Before moving anything, LoopX copies and verifies the full legacy
runtime root, affected project registries, and each Goal directory in a private
backup next to the legacy runtime root. It records the operation and expected
registry fingerprints in `backup_dir/migration-receipt.json` before the first
move. The successful response names the same receipt. Keep this backup private
and outside Git. If a write fails, LoopX attempts to restore the original routes
and reports the backup path if recovery could not finish.

After execution, run `loopx doctor --format json`, inspect the selected route,
and run `loopx --registry <project>/.loopx/registry.json --format json status`
for each affected project. Existing custom `--runtime-root` and `LOOPX_REGISTRY`
configuration should be updated explicitly by its operator; migration does not
rewrite host automation settings or historic run receipts.
If the macOS dashboard LaunchAgent is in use, regenerate its command with
`bash scripts/macos-dashboard-launchagent.sh restart` from a LoopX checkout
after the route readback. Review existing Codex App automation task bodies for
an embedded legacy registry argument before resuming them.

## Roll back

Preview a rollback using the receipt path from execution:

```bash
loopx --format json migrate-local-state \
  --rollback-receipt <backup-dir>/migration-receipt.json
```

The rollback verifies that the migrated target content and private backup still
match the receipt, and that no legacy authority has reappeared. If any state has
changed since migration, rollback refuses to overwrite it. With a clean preview
and writers stopped, run the same command with `--execute`. Then read `doctor`
and the project registries again before restarting workers.

## Recover an interrupted operation

If migration or rollback exits during a move or registry write, keep all writers
stopped. Use `backup_dir` from the original preview and run the same
`--rollback-receipt <backup-dir>/migration-receipt.json` command above, first
without `--execute`. Do not rerun a new migration preview against half-moved
directories. The recovery preview reads the existing operation, verifies its
plan and snapshots, and accepts only the operation's original or expected
updated registry bytes and unchanged Goal/runtime content.

With a valid recovery preview, add `--execute` to restore the original routes.
The receipt records `rolling_back` before recovery moves begin. An I/O failure
or forced process exit can therefore be retried with the same receipt after the
underlying filesystem problem is resolved. Already restored directories are
verified and reused; restored registry files are replaced atomically. A completed
rollback can also be checked or retried safely while its original bytes remain
unchanged. Read `doctor` and each project registry before restarting workers.

New global-registry and receipt writes use deterministic UTF-8/LF bytes on every
host, with the planned global-registry fingerprint saved before moves. Earlier
v1 receipts without that field retain explicit LF/Windows CRLF compatibility;
completed target and backup fingerprints remain enforced. A new operation does
not accept an unrecorded newline rewrite as its own completed write.

If recovery reports new content, a second directory, a redirected route or a
damaged backup, it refuses to overwrite anything. Preserve the backup and both
observed routes for diagnosis; an interrupted operation does not authorize
discarding later work. This is explicit offline process-interruption recovery,
not an online writer fence or a guarantee against filesystem/power-loss damage.
An interrupted backup before the recovery receipt is written has moved no
authoritative state; retain the partial backup and choose a new backup directory
for a fresh preview. Machine-only installations without a global registry can
continue using their legacy route; ordinary first registration stays on that
route before this registered-state migration becomes applicable.

If both default runtime roots contain state, implicit CLI selection fails. Pass
explicit `--registry` and `--runtime-root` for read-only diagnosis, then resolve
the conflicting route before ordinary work. LoopX does not copy on read or keep
two writable defaults in sync.

Host commands that must address this machine's global registry even when run
from a connected project can pass `--registry @host-global`. LoopX resolves
that selector on the executing host using the same new/legacy/conflict rule;
an explicit `--runtime-root` selects a custom root when needed. SSH lifecycle
and manager reads use this selector on the remote host.
