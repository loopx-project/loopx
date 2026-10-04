# Configuration backup and recovery

`backup-state --execute` includes a first-class `configuration-backup.json`
component beside the physical archive. It captures the stored machine
configuration and complete source-owned Goal registry rows, including explicit
capability overrides, storage intent, extension fields, nulls and disabled
values. It resolves shared registry entries to their canonical project registry;
an effective/public settings projection cannot replace the stored configuration.
Unreadable or ambiguous source ownership aborts publication of the backup.
Capture compares the complete transport result with its source values; an
unrepresentable value (such as an integer rounded by JavaScript) aborts instead
of publishing a lossy checkpoint or replacing an earlier backup.

Use a configuration-only backup without copying databases, Host sessions,
credential stores, skill directories or automation state:

```sh
loopx --runtime-root /absolute/runtime --registry /absolute/registry.json \
  --format json configuration-backup export --goal-id example \
  --output /absolute/operator-owned/configuration.json
# Review scope, then repeat with --execute. Export never replaces an existing file.
loopx --runtime-root /absolute/runtime --registry /absolute/registry.json \
  --format json configuration-backup export --goal-id example \
  --output /absolute/operator-owned/configuration.json --execute
loopx --format json configuration-backup verify \
  --input /absolute/operator-owned/configuration.json
loopx --format json configuration-backup restore \
  --input /absolute/operator-owned/configuration.json \
  --expected-sha256 SHA256_FROM_VERIFY --destination /absolute/new-checkpoint
# Review, then repeat with --execute.
```

Omit `--goal-id` to include all Goals in the invoked registry; repeat it to
select several. Full `backup-state` captures its global discovery registry by
default, or the selected project registry with `--current-project-only`. The private
archive manifest reports configuration verification and presence separately.

Settings → Capability Center → **Configuration backup and recovery** offers
download, file verification and isolated recovery through the same typed owner.
Device scope includes all invoked Goals and machine settings; Goal scope
includes the selected Goal plus machine settings. Recovery reports a relative
checkpoint reference under the runtime's `backups/configuration/`. A duplicate
restore rejects the occupied destination; it does not silently replace it.
CLI destinations require an existing physical parent and a new directory.
Symlink ancestors and occupied/dangling destinations are rejected.

## Configuration is not activation

The checkpoint contains the original envelope, the machine configuration at
`machine/configuration.json`, individual Goal rows under digest-named `goals/`
files, and a verified receipt. It creates no live registry, provider selector,
writer fence, lease, Host session, grant or timer. Unknown optional configuration
is preserved as data; its original installed owner must validate it before use.
Restoring these values is not proof that an optional provider is installed.

Adopt reviewed machine namespaces through `machine-config preview/apply`
(or the existing settings editor), preserving destination siblings. Adopt Goal
overrides through `configure-goal` or the existing revision-checked Goal editor;
remap paths and identities and verify effective readback. These owners retain
their current revision, permission, global-sync and rollback contracts. This
checkpoint is not an alternate configuration authority or a batch-activation API.
Rollback live settings through their owning transactions; an unused isolated
checkpoint may be removed without changing live settings.

In particular, three facts are different: a Markdown display file exists;
`goal_storage.new_goal_provider` requests SQLite for future Goals; and the live
Todo authority actually reads from `sqlite_v0`. Missing machine configuration
retains the existing File default. Applying a default after creation does not
retarget an existing Goal. Follow [new-Goal storage and provider selection](local-authority-provider-selection.md)
and [reviewed promotion](reviewed-coordination-promotion.md), then inspect the
real Todo `authority_read` after cutover. An isolated SQLite archive or a
configuration value alone cannot satisfy live provider acceptance.

## Privacy, consistency and limits

Backups are **private by default**. Full Goal rows can contain private paths,
organization data, identity bindings and provider-specific values. Credential
files are not opened by configuration capture, but credentials already embedded
in a configuration remain private data. `privacy_certified=false` never becomes
true because verification passed. A checksum proves content integrity, not
public-safety or authorization. Review and rebuild a separate portable artifact
before transferring it across trust boundaries; retain the private original.

Capture reads each configuration owner independently, without a cross-project
atomicity promise. External files referenced by configuration are dependencies,
not inlined by this component; full state backup retains its existing discovery
scope. Inspect those dependencies and transfer/install reviewed contents
separately. Source-byte differences are not repaired by silently using a settings
summary or dropping unknown fields.

The HTTP restore path uses the existing 64 MiB local-snapshot budget, without
changing the 64 kB ordinary request budget. Oversize or invalid envelopes reject
without partial recovery; CLI/effect transport keeps its existing bounds.
Restored files use owner-only permissions. POSIX checkpoint tests and the
packaged browser journey do not qualify native Windows execution, provider
promotion, a destination machine or long-duration SQLite operation.
