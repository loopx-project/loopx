# Shared-goal-authority E2E stage ladder

One incremental end-to-end ladder for
[RFC: LoopX shared control-plane authority and pluggable state providers v0](../../docs/architecture/rfcs/shared-goal-authority-state-provider-v0.md).
Every completed RFC stage claim is one row; every row drives the product
through the real `python -m loopx.cli` (`real_cli`) or runs a retained
store-level probe (`store_direct`). The ladder adds no product path, reads the
candidate only through the production TypeScript `FileAuthorityStore`, and
never reports green while a selected row is unverified.

```bash
python examples/shared-goal-authority-e2e/ladder.py            # exit 1 here: live rows unverified, the soak row pending
python examples/shared-goal-authority-e2e/ladder.py --allow-unverified --allow-pending
python examples/shared-goal-authority-e2e/ladder.py --stage 2c1 --report-json ladder-report.json
python examples/shared-goal-authority-e2e/ladder.py --stage 2c2 --allow-pending --report-json ladder-report.json
python examples/shared-goal-authority-e2e/ladder.py --list
```

The pytest projection is `tests/control_plane/test_shared_goal_authority_e2e.py`;
there, an unverified row skips as `unverified: <reason>` and a POSIX-only row
skips on Windows. The `s2c1.retired_observation_upgrade` row is also covered by
`tests/control_plane/test_local_authority_shadow_cli_e2e.py` and is skipped in
the default pytest projection; `LOOPX_LADDER_FULL=1` runs it there too. The
standalone example always runs selected rows. The eleven `s2c2.*` rows run in
the stage2c correctness job alongside real-CLI process-death and recovery suites.

The seven former `s2c1.*` observation-writer rows are retired with that writer.
Historical reports retain their meaning; current runs validate explicit upgrade
and the transaction-bound outbox instead of requiring a second writable history.

## Rows

| Row | Stage | Path | Gate | Asserts |
| --- | --- | --- | --- | --- |
| `s0.file_matrix_twelve_rows` | 0 | store_direct | deterministic | `examples/nokv-shadow-provider/live_e2e.py` reports exactly the twelve known file-provider scenario rows, all true |
| `s0.nokv_live_matrix` | 0 | store_direct | env:nokv_legacy | the same twelve rows plus `restored_lineage_fails_closed` are true on a live NoKV stack and file/NoKV outcomes are identical |
| `s1.cli_document_decodes_through_ts_store` | 1 | real_cli | deterministic | Explicit bootstrap plus three CLI writes load at cursor `4`; paged `scanCommitted` returns four distinct transactions in source order and `readReceipt` finds the first source write |
| `s2a.nokv_live_qualification` | 2a | store_direct | env:nokv_authority | runs the merged `examples/nokv-authority-store/live-qualification.ts --execute-live` against an existing workbench with a fresh tenant/goal pair; requires `ok=true`, the single-node store-conformance scope, every check `passed`, NoKV SDK `0.11.1` / API `1`, the two stale-incarnation fence checks (`stale_incarnation_fence_rejected`, `stale_incarnation_fence_left_generation_unchanged`), and no promotion or availability claim; evidence carries check ids, counts, and config and workbench digest prefixes, never a configuration value or the workbench name |
| `s2b.postgresql_conformance_live` | 2b | store_direct | env:postgresql | `postgresql_authority_store.integration.test.ts` under node's TAP reporter: `# pass >= 9`, `# fail 0`, `# skipped 0` |
| `s2c1.retired_observation_upgrade` | 2c1 | real_cli | deterministic | Old enable rejects without writes; retained settings create no history; explicit clear/configure/bootstrap captures the next source transaction |
| `s2c2.outbox_prepared_then_committed_entries` | 2c2 | real_cli | deterministic | with the maintenance lock held, `todo add` (Python) and `task-lease acquire` (TypeScript) report `drain_deferred/drain_lock_busy`, `status` shows one `committed_pending` entry per partition with one prepared record and one committed marker on disk; one `drain` delivers both (`delivered=2`), history holds the bootstrap plus two committed receipts from both writer runtimes, and the next write delivers inline at cursor `4` |
| `s2c2.drain_idempotent` | 2c2 | real_cli | deterministic | three deferred entries: `drain --max-entries 1` delivers one (`pending_after=2`, `budget_exhausted`), the next `drain` delivers two, an idle `drain` reports `nothing_pending` with unchanged cursor, `head_digest` and `provider_revision`; receipts settle sequences 1..3; an idempotent same-key re-acquire carries no capture evidence and adds no transaction |
| `s2c2.sigkill_between_primary_write_and_drain` | 2c2 | real_cli | deterministic (POSIX) | `todo add` SIGKILLed at `before_replace`, `after_replace` and `before_marker` leaves one prepared-only entry each; `drain` settles it as `abandoned` (no-op, primary unchanged) or `committed_proven_by_readback`, the projection equals the primary, and `inspect` ends `matched` |
| `s2c2.sigkill_mid_drain` | 2c2 | real_cli | deterministic (POSIX) | `todo add` SIGKILLed at `before_commit`, `after_commit`, `after_cursor` and `between_unlinks`: the next `drain` delivers the uncommitted entry once or replays the committed one (`replayed=1, delivered=0`), history holds exactly one delivery, only the cursor remains, and a further drain is idle |
| `s2c2.rollback_with_pending_entries` | 2c2 | real_cli | deterministic (POSIX) | with one committed-pending and one prepared-only entry, `inspect` reports `outbox_pending` at the exact revision, a rollback preview writes nothing, `rollback --execute` applies and archives the outbox with both entries, the marker, the cursor and the manifest; capture then reports `bootstrap_required` while primary writes continue, a rebootstrap starts a new lineage from the current primary (three todos), and the historical rollback replays against it |
| `s2c2.parity_equal` | 2c2 | real_cli | deterministic | three cycles interleave Python Markdown writers (add, note update plus a no-change repeat, explicit exclusion set and clear plus a no-change repeat, complete, supersede, and a second add) with TypeScript lease writers (acquire, renew, transfer, and the fence close of a leased complete or supersede); after each cycle `inspect` is `matched`, `qualify` with every required write class is `qualified` with `operation_count` equal to the delivered mutations, `read-candidate` returns the anchor todo, and `sustained_parity_verdict` stays `not_evaluated` |
| `s2c2.parity_divergent_detects_foreign_edit` | 2c2 | real_cli | deterministic | a direct edit of the primary makes `inspect` report `drifted/shadow_projection_drift`, `qualify` and `read-candidate` reject, a later `todo add` commits but its capture holds on `source_partition_continuity_unproved`; restoring the bytes does not requalify (`outbox_pending`), `drain` stays `stopped`, and only `rollback --execute` plus a fresh bootstrap qualifies again |
| `s2c2.event_only_todo_source_holds` | 2c2 | real_cli | deterministic | an unmanaged event-only Todo appended to the goal's state event log makes `inspect`, `qualify` and `read-candidate` reject source drift, `status` stays readable, a Markdown write still commits with its capture held, the event log is untouched; removing the event source does not requalify, and rollback plus rebootstrap recovers |
| `s2c2.migration_seeds_and_drains` | 2c2 | real_cli | deterministic | `migrate-state` previews an actively captured goal without writing, refuses `--execute` with `shadow_source_replacement_requires_rebootstrap` (also when capture is merely disabled), and executes only after `rollback`; the migrated goal carries its disabled capture configuration, plans no observation seed, requires its own `bootstrap`, then captures a write to cursor `2` and qualifies on it while the legacy archive is retained |
| `s2c2.growth_measurement_gate` | 2c2 | real_cli | deterministic | ten fixed-size `todo add` writes: the cursor advances by one each time, `store_bytes` grows monotonically, the per-transaction delta accelerates by at most 2048 bytes (one live record), every retained transaction carries its complete projection, `retention_pressure` stays false; the report carries final and cumulative publication bytes and claims no capacity horizon (`capacity_verdict=not_evaluated`) |
| `s2c2.archive_after_leased_completion_parity` | 2c2 | real_cli | deterministic | a leased Todo is completed through its fence and then archived by `todo archive-completed`: the archive retires the Todo from the current graph while its released lease file stays on disk as audit history, and `inspect` still reports `matched` with `parity_matches=true`, no drift reason, a qualified bounded read of a co-resident open Todo, and a `qualify` that requires the archive event kind; the released lease file remains on disk |

Pending rows are declared in the report as `pending`, never counted as pass,
and they block a green exit unless `--allow-pending` is passed. One
declaration remains: `s2c2.sustained_parity_soak`, the >=10-day synthetic-goal
soak of the selected local profile owned by RFC Section 7.2 (lane L). Bounded
qualification reports `sustained_parity_verdict=not_evaluated`, and no
`s2c2.*` row promotes a provider or completes the Stage 2C promotion.

The former `s2c2.archive_after_leased_completion_parity` declaration is now an
executable row. The gap it recorded is closed at the fold: a Todo partition
carries the published Todo read records, and the candidate head now keeps a
lease edge only for Todos still in the current graph
(`archive_state === "active"`), matching the rule the source projection and the
TypeScript source verification already apply.

The `s2c2.*` rows use two scheduling-only seams outside every product decision:
holding the stable maintenance lock, which makes a writer report
`drain_deferred/drain_lock_busy` and leave its committed entry pending, and a
POSIX crash worker that pauses one real CLI process at a named persistence
window so the row can SIGKILL it there. Neither substitutes a result or edits a
byte; every assertion still goes through `status`, `drain`, `inspect`,
`qualify`, `read-candidate`, `rollback`, `migrate-state` and the retained
TypeScript store read.

## Gates and environment variables

| Gate | Requirement | Unverified reason when absent |
| --- | --- | --- |
| `deterministic` | none (needs `node` on `PATH` for the CLI's TypeScript runtime and the read-back probe) | `node_missing` when the probe cannot run |
| `env:postgresql` | `LOOPX_TEST_POSTGRES_URL` plus `node_modules/pg` (`npm ci`) | `postgres_url_missing`, `pg_dependency_missing`, `node_missing` |
| `env:nokv_legacy` | `NOKV_COORDINATION_LIVE=1` and `NOKV_ETCD`, `NOKV_ETCD_PREFIX`, `NOKV_ROOT_ID`, `NOKV_BUCKET`, `NOKV_OBJECT_ENDPOINT`, `NOKV_OBJECT_ROOT`, `NOKV_OBJECT_KEY`, `NOKV_OBJECT_SECRET`; the `nokv` SDK importable | `nokv_live_env_missing`, `nokv_coordination_live_not_enabled`, `nokv_sdk_missing` |
| `env:nokv_authority` | `LOOPX_NOKV_AUTHORITY_LIVE=1` (the probe writes durable test data), `LOOPX_NOKV_AUTHORITY_CONFIG_JSON` (absolute path to the ignored NoKV client configuration), `LOOPX_NOKV_AUTHORITY_PYTHON` (absolute path to the Python executable that resolves NoKV SDK 0.11.1), `LOOPX_NOKV_AUTHORITY_WORKBENCH` (an existing workbench); `node` on `PATH` | `nokv_authority_env_missing`, `loopx_nokv_authority_live_not_enabled`, `nokv_authority_config_missing`, `nokv_authority_python_missing`, `node_missing` |

POSIX-only rows report `unverified/posix_only` on Windows.

## Report and exit policy

The report schema is `loopx_shared_goal_authority_e2e_report_v0`:
`rows[]` (`status in {pass, fail, unverified}`, `reason_code`, public-safe
`evidence`, `duration_ms`), `pending[]`, `summary{pass, fail, unverified,
pending, executed, privacy_violations}`, `bindings{loopx_commit, loopx_tree_dirty, probe_sha256[],
nokv_client_config_sha256, nokv_sdk_version, postgres_url_sha256_prefix,
pg_package_version}` (`null` when unknown), and `exit_policy`.

Exit code is `0` iff `fail == 0` and `privacy_violations == 0` and
(`unverified == 0` or `--allow-unverified`) and (`pending == 0` or
`--allow-pending`): a selected
row that never executed, whether gated or declared pending, is an unmet
obligation, so `--row s2c2.sustained_parity_soak` exits 1 with zero executions, and a
mixed selection exits 1 even when its executable rows pass. `--list` only
prints the registry and never claims verification. A privacy scan runs over
the finished report: any occurrence of a temporary root, the home directory,
the repository path, the PostgreSQL URL, a NoKV configuration value, or a NoKV
authority input path rewrites that row to `fail/privacy_violation`; a leak
confined to the `bindings` block nulls every binding, marks
`bindings.privacy_violation`, and still exits `1` through
`summary.privacy_violations`, which no flag relaxes. Evidence therefore
carries counters, cursors, outcome tokens, and sha256 prefixes only.

## Bounded outbox correctness and the pending ladder

The independently runnable [correctness suite](correctness.md) covers the
`file_outbox_v1` capture lineage, strict cursor recovery, mixed writers,
source fences, and recoverable bootstrap/rollback. It uses real CLI and native
processes, the production `FileAuthorityStore`, and process death at persistence
boundaries. [Installed-package E2E](installed.py) repeats the public lifecycle
outside the checkout for both wheel and sdist. [Negative controls](mutants.py)
deliberately remove correctness checks in disposable source copies.

The ten `s2c2.*` ladder rows above exercise the same lifecycle through the
public interfaces and read history only through the retained TypeScript store.
Sustained (elapsed-time) parity and promotion remain separate obligations: the
soak and the archive-after-lease capture gap stay pending declarations, and a
bounded qualification result reports `sustained_parity_verdict=not_evaluated`.

Future ladder rows must use the actual product interfaces:

- `authority-shadow drain` for receipt-verified replay and cursor recovery;
- `coordination-shadow inspect / qualify / read-candidate` for comparison,
  bounded historical qualification, and a qualified read from that same head;
- `coordination-shadow rollback` with an exact revision or unfinished bootstrap
  operation selector, followed by explicit rebootstrap;
- the stable management lock outside the goal outbox for scheduling a management
  boundary. Rollback moves the entire goal outbox, so a lock inside that directory
  cannot coordinate it. The tests retain scheduling-only barriers around real
  persistence calls rather than depending on the retired drain lock.

There is no `verify` command or generic `reset`. The independent v0 observation
store and its runtime-root override behavior retain their original scope; its
historical evidence is not an outbox qualification receipt.
