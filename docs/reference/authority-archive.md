# Canonical authority archive and isolated recovery

`loopx authority-archive` exports a pinned prefix of the selected coordination
AuthorityStore and restores that history into a separate File or SQLite store.
It preserves the full committed projections, events, operation ids and receipts,
including archived Todos and retained leases. It does not export the registry,
quota ledger, external artifacts, host sessions or the rest of a Goal's runtime.

This is the recovery-artifact part of shared-authority D3/L8. It does not promote
a Goal, switch providers, release a writer fence, register a restored runtime,
or qualify the default local profile. The recovered lease records are historical
state, not a new execution grant. Canonical writes made after the captured cursor
are outside that archive and must be accounted for by a separate fenced cutover.
Never reactivate an older Markdown state as a rollback after canonical writes.

## Export and verify

Use the registry and runtime of the source Goal. Export is explicit and reads
the selected provider; a missing or invalid selector does not fall back to a
legacy display. The archive's parent directory must exist. An existing output
file is never replaced.

```bash
loopx --registry ./registry.json --format json authority-archive export \
  --goal-id example-goal --archive ./authority.ndjson
loopx --format json authority-archive verify --archive ./authority.ndjson
```

Save `archive.archive_sha256` from the compact response for recovery review.
It identifies the canonical record hash chain and terminal seal, not the raw
file bytes: use `verify`, rather than `sha256sum` of the NDJSON file, for this value.
The archive is private state, written with mode `0600`; do not publish it as a
fixture or attach it to a public PR. Checksums detect corruption and bind the
reviewed content; they do not authenticate its author. Protect the digest and
archive together under the existing local filesystem trust boundary.

The export pins the initial head cursor, provider revision and store identity.
It reads contiguous pages only through that cursor, independently reconstructs
the archive, checks the terminal head and identity, then publishes the completed
file without overwriting another output. Concurrent appends do not force a
restart. Missing history, source replacement and a rewritten captured head fail.
A process killed before publication may leave a private `.partial` sibling;
verify any completed output before deciding whether an interrupted export needs
to be repeated.

## Restore an isolated copy

The destination is a **new directory**, not a runtime root or a provider selector.
Preview validates the complete archive and reviewed goal/digest without creating
the destination. Execution creates a private binding manifest and a `store/`
subdirectory, then writes and reads back every retained transaction.

```bash
loopx --format json authority-archive restore \
  --goal-id example-goal --archive ./authority.ndjson \
  --archive-sha256 <verified-digest> --provider sqlite \
  --destination ./recovered-authority
# Repeat the same command with --execute to restore.
```

Use `--provider file` for an isolated File copy. The CLI deliberately does not
accept database credentials. The same TS archive/restore contract supports
PostgreSQL through a service-owned `AuthorityStore`; authentication, tenant
scope and a separate database incarnation remain the service's responsibility.
This is portable recovery, not a deployed PostgreSQL provider-switch feature.

An interrupted restore can be repeated with the same archive, digest, goal,
provider and destination. Its existing prefix must match every operation,
event, receipt and historical projection. Extra or conflicting target commits
reject; recovery never overwrites them. A lost commit response is resolved by
exact journal and receipt readback. If directory creation was interrupted before
its binding manifest was durable, use a fresh destination; an unbound occupied
directory is intentionally not adopted.

`verified-restore.json` records the archive digest, target store identity and
final target provider revision only after full readback. It is a historical
verification receipt, not a permanent claim that nobody changed the copy later.
The source physical provider revisions remain in the archive for provenance;
restored transactions receive the destination provider's own revision tokens.
Business operation identities and receipt payloads remain unchanged.

The full archive is validated before restore writes begin and again during
replay. Modifying the input during recovery fails verification; any partial
result stays isolated and must not be adopted. A separate authority transition
must own executor fencing, target adoption and the accounting for later source
writes. There is no automatic selection or Markdown rollback here.

## Format and validation scope

The versioned NDJSON stream contains a header, ordered transaction records and
a terminal seal. A transaction stores an exact state delta using the same
`authority_state_log` codec as SQLite, plus events and receipts. The initial
delta reconstructs from the empty object; later records avoid repeating the
whole graph. Each record is hash-linked to its predecessor. Verification checks
strict fields, goal identity, positive contiguous cursors, unique operation ids,
state digests, source head revision and a seal followed by EOF. A valid checksum
alone cannot make a missing transaction or reordered history valid.

Processing retains one reconstructed projection and the operation-id inventory,
not every full historical projection. Individual encoded records are limited to
64 MiB; this is an explicit archive format bound, not provider capacity evidence.
Supported-runtime, retention, crash/restore and elapsed-soak qualification for a
production profile remain separate.

Public tests cover real File/SQLite round trips, PostgreSQL in both directions,
complex native/imported graphs, checkpoint-window history, corrupted/re-signed
invalid archives, concurrent source appends and interrupted recovery. CLI tests
also prove preview, repeated recovery, independent-process readback and rejection
of an occupied active-runtime destination. Frontend and Lark settings are
unchanged: this administrative command never changes their authority selection,
configuration owner or state projection.
