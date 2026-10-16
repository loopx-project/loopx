# Shared-goal-authority E2E stage ladder

This ladder qualifies the current shared-authority providers described by the
[shared control-plane authority RFC](../../docs/architecture/rfcs/shared-goal-authority-state-provider-v0.md).
Each row uses either the public LoopX CLI or a provider's production TypeScript
store. A selected row must pass, report an explicit environment gate, or make
the command fail.

```bash
python examples/shared-goal-authority-e2e/ladder.py
python examples/shared-goal-authority-e2e/ladder.py --allow-unverified
python examples/shared-goal-authority-e2e/ladder.py --stage 1 \
  --report-json ladder-report.json
python examples/shared-goal-authority-e2e/ladder.py --list
```

The pytest projection is
`tests/control_plane/test_shared_goal_authority_e2e.py`. Stage 0 runs the full
native provider suites, so the default pytest projection leaves those rows to
the TypeScript job. Set `LOOPX_LADDER_FULL=1` to run them through pytest too.
The standalone ladder always runs every selected row.

## Current rows

| Row | Stage | Path | Gate | Assertion |
| --- | --- | --- | --- | --- |
| `s0.native_file_conformance` | 0 | `store_direct` | deterministic | The current `FileAuthorityStore` passes its complete conformance suite with no skip or incomplete TAP trailer. |
| `s0.native_sqlite_conformance` | 0 | `store_direct` | deterministic | The current `SqliteAuthorityStore` passes the same complete conformance contract against real SQLite files. |
| `s1.cli_document_decodes_through_ts_store` | 1 | `real_cli` | deterministic | Public CLI bootstrap creates canonical File authority. Todo add, lease acquire, and Todo update commit at cursors 2 through 4. An independent TypeScript process reads the four ordered transactions and the first business receipt from `runtime/authority/file-v0`. |
| `s2a.nokv_live_qualification` | 2a | `store_direct` | `env:nokv_authority` | The NoKV candidate passes the live single-node qualification with SDK `0.11.1`, API `1`, and both stale-incarnation fence checks. |
| `s2b.postgresql_conformance_live` | 2b | `store_direct` | `env:postgresql` | The PostgreSQL candidate passes the provider integration suite with at least nine TAP passes and no failures or skips. |

Stage 1 checks the complete public path. It applies File creation defaults,
bootstraps a new Goal, configures its registered agents, performs three
business mutations, and verifies all responses report:

- `source_authority=file_v0`;
- `decision_read_from_provider=true`;
- `legacy_fallback_used=false`;
- no retired `coordination_runtime_shadow` write evidence.

The readback process opens the same canonical provider independently. A Python
response cannot make this row pass without the provider journal and receipt.

## Legacy capture cutoff

The 2.0 cutoff removed the Stage 2C rows that created new Markdown shadow
transactions. They were tests of a producer that no longer owns business
writes. Keeping those rows would require LoopX to restore the retired producer
only to satisfy its qualification suite.

Current Todo, handoff, Monitor, team-plan, bootstrap, project-registration, and
lease operations require canonical authority. An unpromoted Goal returns
`canonical_authority_required` with `migrate_goal_authority` and
`backup_required=true`. It does not create a Markdown mutation or a shadow
outbox entry.

Existing shadow stores, outboxes, rollback archives, and receipts remain
readable through their recovery commands. Those commands classify and settle
pre-cutoff bytes. They do not enable a new capture lineage or qualify a new
business authority. See [historical outbox recovery](correctness.md).

## Native qualification

Run Stage 0 from a prepared source checkout:

```bash
uv run --extra test python \
  examples/shared-goal-authority-e2e/ladder.py --stage 0
```

A successful process exit is necessary but insufficient. The ladder parses the
TAP trailer and requires a nonempty selection where every test passed. A
missing Node runtime or source suite reports `unverified`; it never becomes a
pass.

The retired Python `CoordinationAuthorityExecutor`, head codec, and bootstrap
bridge had no production CLI callers. Their former prototype rows are not
aliases for current File or NoKV evidence. The current TypeScript stores,
logical archives, migrations, and provider conformance remain independently
qualified.

## Environment gates

| Gate | Required input | Unverified result |
| --- | --- | --- |
| `deterministic` | Node.js on `PATH` for native execution and readback | `node_missing` |
| `env:postgresql` | `LOOPX_TEST_POSTGRES_URL` and the installed `pg` package | `postgres_url_missing`, `pg_dependency_missing`, or `node_missing` |
| `env:nokv_authority` | `LOOPX_NOKV_AUTHORITY_LIVE=1`, absolute config and Python paths, an existing workbench, and Node.js | A specific missing-input or disabled-live reason |

The NoKV probe creates a fresh tenant and Goal in the selected workbench.
Reports contain digests and counters, never configuration values, workbench
names, repository paths, temporary roots, or provider credentials.

## Report policy

The report schema is `loopx_shared_goal_authority_e2e_report_v0`. It contains
executed rows, explicit pending declarations, aggregate counts, environment
bindings, and the exit policy.

Exit code `0` requires:

- no failed row;
- no privacy violation;
- no unverified row unless `--allow-unverified` is set;
- no pending declaration unless `--allow-pending` is set.

There are no pending rows in the current registry. The generic pending
mechanism remains tested so a future obligation cannot be listed as completed
work. `--list` prints the registry and makes no verification claim.

## Distribution qualification

[installed.py](installed.py) builds an isolated environment for a wheel or
source distribution, runs outside the checkout, verifies installed resource
provenance, and exercises canonical File creation, mutations, exact replay,
history, receipts, and legacy-bootstrap rejection.

[mutants.py](mutants.py) runs negative controls in disposable source copies.
The controls prove that removing a required assertion or authority boundary
causes the qualification to fail.
