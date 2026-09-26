# Local State Path Migration

New LoopX installations use `$HOME/.loopx/registry.global.json` and
`<project>/.loopx/goals/<goal-id>/ACTIVE_GOAL_STATE.md`. An existing installation
with only `$HOME/.codex/loopx/registry.global.json` keeps using that single
legacy runtime route. Registered project Goals keep their declared `state_file`
until explicitly migrated. `loopx doctor --format json` reports the selected,
legacy, and target routes under `local_state_route`.

This migration is distinct from `migrate-state`, which imports the older Goal
Harness product state. It moves only the LoopX runtime root and Goal directories
declared by project registries. Other files under `.codex` remain in place.

## Preview and execute

Stop LoopX workers, heartbeats, status servers, and other writers on this machine
before executing. Older versions do not honor a migration-wide writer fence.
Keep them stopped until status and project readback succeed. Run the preview:

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
backup next to the legacy runtime root. The successful response includes the
receipt under `backup_dir/migration-receipt.json`. Keep this backup private and
outside Git. If a write fails, LoopX attempts to restore the original routes
and reports the backup path if manual recovery is needed.

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

If both default global registries exist, implicit CLI selection fails. Pass
explicit `--registry` and `--runtime-root` for read-only diagnosis, then resolve
the conflicting route before ordinary work. LoopX does not copy on read or keep
two writable defaults in sync.
