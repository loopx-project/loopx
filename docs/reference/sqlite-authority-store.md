# SQLite authority provider

SQLite is an **opt-in local conformance candidate**, behind the existing
TypeScript `AuthorityStore` interface. File remains the default. This slice
does not promote a goal, run a live cutover, enable cross-host writes, or
qualify ten elapsed days of operation. It does provide the explicit
version-1 to version-2 database migration described below.

## Placement and persistence

The provider belongs to the existing shared-coordination authority boundary
(`loopx/control_plane/coordination`). It is bundled with LoopX, not a new
capability or extension. Legal transitions, actor/lease checks, operation
digests and replay decisions remain with the existing typed transaction
executors. Python only admits the corresponding `sqlite_v0` source receipt.

Each goal has a separate database under the runtime's `authority/sqlite-v0`
directory. Metadata binds the goal, schema version and random database
incarnation. Provider revisions combine that incarnation with a monotonic
integer sequence; they are not authority revisions or lease epochs.

The version-2 schema contains:

| Table | Contract |
| --- | --- |
| `metadata` | Version and database/goal identity |
| `head` | The live committed projection, its state digest and one cursor |
| `commits` | Unique operation ID, canonical commit digest, ordered cursor, original receipts and events, one exact state delta, its state digest and parent state digest |
| `checkpoints` | One full projection and its digest per bounded window |

`commits` also serves as the durable projection outbox used by
`scanCommitted`. There is no independent ACK or second receipt authority.
Existing consumers resume by cursor. A unique operation index makes receipt
lookup and cursor paging indexed.

Retention is bounded by window instead of by history: every commit keeps one
exact delta, and one full projection is retained per checkpoint window
(`authority_state_log.ts`, 64 commits per window). A live read resolves the head
from the head row, its retained transaction and the cursor bounds; a historical
read rebuilds at most one window from the covering checkpoint. Retained deltas
are therefore the only part that still grows with history, and their size is
proportional to what each commit changed. Original receipts and events are
retained without pruning, so fixed live state with large receipts still grows
with history. Growing application projections require separate
retention/compaction work.

The rehearsal profile measures this profile directly: at 1,000 commits with a
64 KiB projection, 16 checkpoints retain 1,048,576 projection bytes and 126,714
delta bytes, against 65,536,000 bytes for one full copy per retained commit.
Formal 10k/100k evidence still requires the separately authorized matched
profile.

Writes use `BEGIN IMMEDIATE`, a five-second busy timeout, WAL and
`synchronous=FULL`. The head, receipt, events and outbox row commit together.
Before-COMMIT failures roll back; a COMMIT error reports an ambiguous outcome
for receipt reconciliation. Readers use committed snapshots; no history rewrite
is needed for a new transaction. SQLite storage durability still depends on the
local filesystem and hardware honoring synchronization.

Schema changes are explicit: unknown `user_version`, foreign tables or a
different goal/incarnation fail closed. No automatic migration, identity
rotation, corruption repair, or network-filesystem sharing is supported.

## Read integrity

Authority reads share one SQLite snapshot, and writes run the same live proof
inside their transaction before publishing a new commit row. The proof is
layered so that each layer pays only for what it returns:

| Layer | Proves | Cost |
| --- | --- | --- |
| Live head (`loadAuthority`, `commitAuthority`) | Head row digest over the live projection, the retained transaction at that cursor reproducing its exact commit digest, parent linkage, `min=1`/`count=max=head` cursor continuity, and the presence of the checkpoint that covers the head | One head row, one retained row, one parent digest and index lookups; independent of retained history |
| Materialized history (`scanCommitted`, `readReceipt`) | Every row from the covering checkpoint through the requested span, including each delta, state digest and parent lineage; paged scans also prove the lookahead row used for `has_more` | At most one checkpoint window plus the requested span |
| Archive audit (`verifyAuthorityHistory`) | The complete delta chain from the empty root, every checkpoint against retained history, and the final state against the head | Linear in retained history; qualification and recovery only |

A missing head, rolled-back head, internal cursor gap, rewritten receipt/event,
orphaned parent digest or mismatched state digest is rejected as
`provider_protocol_violation` before returning authority or accepting a write.
Preparing a commit also re-applies its own delta and requires byte equality with
the committed projection before anything is written.

Two shapes are deliberately outside the live proof because the live head never
reads them: the delta of the newest retained row, and the projection of the
checkpoint the live head resumes from. Both are refused by every read that
materializes their span and by the archive audit, and neither can change the
authority value a live read returns. The commit digest is unchanged from v0
(operation ID, projection, events, receipts, expected predecessor revision), so
cursors, provider revisions and stored digests stay comparable.

This is integrity validation of the current and accessed evidence, not a full
cryptographic audit of every historical payload on each call. Unaccessed older
row digests are checked when those rows are read. Checksums detect inconsistent
data; they do not authenticate an administrator who can rewrite both the data
and its digest. Restoration of an older, internally consistent database remains
outside this slice's qualification boundary.

## Explicit selection

Use an isolated qualification runtime and an empty, unpromoted goal. Set
`RUNTIME_ROOT` to that runtime's absolute directory. The SQLite qualification
reference is **Node 22.22.3 with SQLite 3.51.3**. The provider checks the actual
embedded SQLite version and synchronous prepared-statement finalization before
creating or opening an authority database. Record both `sqlite_version()` and
`sqlite_source_id()`; the Node version alone is insufficient.

SQLite 3.51.3 and later 3.x releases contain the
[WAL-reset concurrency fix](https://www.sqlite.org/wal.html#the_wal_reset_bug).
The fixed 3.44.x (3.44.6+) and 3.50.x (3.50.7+) backport lines are also admitted
when their driver finalizes statements on close. Unknown version strings or
unverified vendor backports fail closed. Passing these prerequisites does not
qualify the complete D2 profile.

This intentionally rejects SQLite runtimes previously accepted by the
statement-only probe, including vulnerable drivers shipped with older Node 22
releases. The public Node minimum remains 22.18 for the default File path;
SQLite requires the additional fix. No provider selection changes and no
fallback to File occur when an explicitly selected SQLite runtime is rejected.
The optional driver is still opened only after opt-in. The serving runtime
loads it in-process once at startup for the read-only identity probe described
below; that probe opens no database file and creates no authority state, and
the File path keeps working when the driver is unavailable.

From the repository checkout, preview selection:

```sh
node --experimental-sqlite --experimental-strip-types \
  loopx/control_plane/coordination/local_authority_provider.ts \
  --runtime-root "$RUNTIME_ROOT" --goal-id example
```

Apply the same command with `--execute`. It creates the empty database and a
durable per-goal selector bound to its incarnation. Repeating it is idempotent.
The command rejects an existing canonical file head or a writer fence. It does
not bootstrap or promote authority. Existing qualification/promotion gates
still govern the first canonical state, with the chosen provider used as the
destination. Do not bypass those gates to enable a live goal.

The process starting the managed Effect runtime must use the qualified Node
runtime too. Stop a previously running managed runtime normally before changing
its Node executable. Adding an experimental flag to an older Node 22 release does not fix its
statement lifecycle.

The managed Effect runtime is reused per user and source revision, so the
Node/SQLite pair serving a goal is not necessarily the one the calling process
resolves from PATH. Its info file records a `runtime_identity`
(`node_version`, `sqlite_version`, `sqlite_authority_qualified`), and
`loopx doctor` reports that identity plus a restart recommendation when the
serving runtime is not qualified. Installing the qualified Node alone does not
repair a runtime that is already running: restart it with

```sh
loopx doctor --restart-runtime
```

so the next control-plane request starts a new runtime from the current PATH.
Waiting for the runtime's idle shutdown has the same effect.
A direct CLI invocation that is not the reused runtime reports the same
qualification failure without a restart step, because rerunning it on the
qualified PATH is already sufficient there.

After separately admitted canonical initialization, ordinary `loopx todo`
commands use the persisted selector. For example:

```sh
loopx --registry "$REGISTRY_PATH" --runtime-root "$RUNTIME_ROOT" \
  --format json todo list --goal-id example
```

An unavailable database, malformed selector, changed incarnation or lost
selector fails closed; none silently falls back to file authority. Deleting
the generated Markdown does not delete the canonical Todo state.

Provider-open errors retain selection identity separately from request errors.
A validated SQLite selector produces `source_authority=sqlite_v0` even when its
database is missing or unreadable. An invalid, unavailable or missing selector
reports `source_authority=null` because selection is unresolved; it never guesses
file authority. Typed `local_authority_selector_*` and
`local_authority_provider_*` reason codes identify that boundary, with a
`provider_reason_code` when the store returned a more specific diagnostic.
These failures set both `decision_read_from_provider=false` and
`legacy_fallback_used=false`. Successful responses and unrelated request/domain
errors retain their existing contracts.

## Promotion failure evidence

`legacy_writer_fenced` reports whether this promotion invocation verified the
exact persisted fence against the request. Provider opening precedes that
verification so opening errors retain their selected-provider diagnostics.
Such early failures report `false`, even if a fence exists but was not read and
matched. This is not proof that legacy writes are allowed; callers must consult
the durable writer guard. After successful fence verification, later failures
retain `true`. Request fields alone never establish fencing evidence.

The failure-path regression matrix covers:

| Boundary | Evidence checked |
| --- | --- |
| Selector/database open | List, exact read, mutation, create, claim, native/planning update, compatibility edit, terminal, monitor poll, archive, ACK and promotion retain typed source/reason, no fallback and unchanged authority bytes. |
| Fence readback | Missing, malformed and mismatched fences do not establish verified fencing; an open failure cannot infer it from an existing marker. |
| After verified fence | Missing/invalid shadow and rejected qualification preserve verified fencing without canonical writes. |
| Existing promotion readback | Exact receipt/first-commit lineage permits replay; missing receipts or mismatched lineage reject without modifying authority. |

The matrix is typed against every exported runtime entrypoint so adding a new
entrypoint requires an explicit failure fixture.

These tests use disposable file/SQLite stores and the production runtime
entrypoints. They preserve the current qualification gate: mirrored file shadow
health alone does not authorize a new canonical cutover. Shared store conformance
separately covers transactional CAS, commit ambiguity, receipt reconciliation and
projection replay. No active Goal is needed for this validation.

## Stop and recovery boundary

To stop using the candidate, stop the owning goal/host runtime and retain its
database and selector. For a managed goal, `loopx configure-goal --goal-id
example --quota-compute 0 --execute` pauses automatic turns; separately stop
any active host process before taking an offline backup. Pausing does not
cancel a transaction already running.

There is deliberately no in-place switch back to file authority after commits:
that requires an explicit migration with receipt/lineage validation. Do not
delete the selector to disable the provider. Preserve the database together
with any `-wal`/`-shm` files when recovering an interrupted runtime; use SQLite
backup facilities or a fully stopped database for a coherent backup. Restoring
an older snapshot as concurrent live authority is not supported. Disposable
qualification runtimes may be retired as a whole after their processes stop.

Selection grants local storage use only. It grants no actor/lease ownership,
external service access, cross-host synchronization or promotion authority.

## Reproduce validation

Use the qualified Node executable on PATH, including the Python CLI's managed
Effect runtime. The runner records the actual Node/SQLite/source identity.

```sh
npm ci --ignore-scripts
npm run typecheck:control-plane
node --no-warnings --experimental-sqlite --experimental-strip-types --test \
  tests/control_plane_ts/sqlite_authority_store.test.ts \
  tests/control_plane_ts/sqlite_authority_bounded_profile.test.ts \
  tests/control_plane_ts/sqlite_authority_migration.test.ts \
  tests/control_plane_ts/authority_state_log.test.ts \
  tests/control_plane_ts/authority_provider_parity.test.ts \
  tests/control_plane_ts/local_authority_provider.test.ts \
  tests/control_plane_ts/sqlite_runtime_admission.test.ts \
  tests/control_plane_ts/sqlite_capacity.test.ts
python -m pytest -q tests/control_plane/test_sqlite_authority_cli.py
node -e "require('node:fs').mkdirSync('.local', {recursive:true})"
node --no-warnings --experimental-sqlite --experimental-strip-types \
  examples/coordination/sqlite-capacity.ts --profile rehearsal --cli \
  --output .local/sqlite-rehearsal.json
node --no-warnings --experimental-sqlite --experimental-strip-types \
  examples/coordination/sqlite-capacity.ts --profile matched-64k --cli \
  --output .local/sqlite-matched-64k.json
```

For an already promoted Goal, [canonical lease renewal](canonical-lease-renew.md)
uses the selected provider's CAS and original receipt. It is a command-coverage
slice, not D2 qualification or a provider-default change.

The no-argument default intentionally replaces the former 4 KiB/100k run with
a small `rehearsal`; full capacity now requires an explicit profile. The default
`rehearsal` creates 100/1,000 commits and checks runner execution,
independent invariants and cleanup; it cannot satisfy formal performance
budgets. The explicit `matched-64k` profile creates separate 10k/100k databases,
serially, with exactly 64 KiB native synthetic projection JSON and at most 4 KiB
of new event/receipt JSON per commit. Each fill write is followed by three head
reads and two indexed historical receipt reads. Both formal groups sample the
last 1,000 commits and their corresponding reads, plus 200 scan-100 samples.
This one-Todo storage axis isolates history growth; it is not the complete
multi-agent/lease/capture workload.

`--cli` adds 20 formal samples (three in rehearsal) for complete CLI mutation,
status and quota, using fresh Python processes and a newly started managed
Effect runtime for each sample. Shutdown occurs outside the timed interval in
the isolated fixture. `--python` chooses the Python executable. These figures
include process startup but do not drop the OS file cache. Cold Node-only load
and warm actual-provider calls are separate. The provider's normal per-call
connection open/close remains inside warm timing. CLI mutations happen after
the fixed-history measurement; CLI, traffic-window and lock-probe commits all
extend past the fill target and are counted separately, so target-state rows
keep their meaning.

Reports carry p50/p95/p99 and counts, parent-process RSS, application request
JSON bytes and separate DB/WAL/SHM sizes at the target history. Resource-usage
peak RSS is process-lifetime across both groups; sampled axis RSS is separate,
and CLI child RSS is not measured. Application bytes, final files, SQLite
logical writes, cumulative WAL traffic and physical device writes are different
metrics and are never substituted for one another. Logical write volume is
measured from the filled database as the serialized bytes each commit hands to
SQLite (commits row plus the full-projection head rewrite plus amortized
checkpoint rows); page, index and compaction overhead belong to the other
columns. Cumulative WAL traffic is measured over one bounded commit window per
axis: read marks pinned by two observer connections make every WAL reset
impossible, so frame growth over the window is exact, and the per-commit
traffic at both depths carries the <=15x cumulative-growth budget. Lock wait is
app-observed: a probe process holds the write lock for a controlled interval
and the end-to-end store commit wait is reported against the uncontended
baseline. Whole-run WAL totals, pure busy-handler time and physical device
writes remain `missing`; a final WAL size of zero still proves no
cumulative-write bound.

Each axis reserves 5 GiB free space, caps its database at 16 GiB and checks a
2,400-second fill budget. All data are generated in a new temporary directory;
there is no flag to select an existing Goal/runtime for writes. Keep generated
reports in ignored local storage. Failure results survive in the report and
exit nonzero; omitted or incomplete evidence never becomes a pass. An exit
zero with `status=incomplete` means the requested measurements ran, not that
D2 qualified. Formal budget failures must remain visible without changing the
workload or thresholds to obtain a green report.

The real-process regressions exercise SIGKILL before and after business COMMIT,
lost-response receipt readback, exact head/event/receipt/scan equivalence and
SQLite `max_page_count` exhaustion. These are small disposable-database tests,
not power-loss, operating-system ENOSPC or large-history recovery qualification.
Retention deletion, restore-incarnation change and cross-host sharing are still
absent from this slice.

## Version-1 migration

The shipped version-1 database keeps one full projection per retained row, so it
cannot be read by the version-2 provider. `sqlite_authority_migration.ts`
migrates one Goal database in place: it reads the frozen version-1 rows, proves
every stored commit digest, writes the checkpoint/delta log, proves that each
written delta reconstructs its projection, reads the not-yet-swapped tables back
and replays them through the store's own delta decoder, requires the commit
count to match, swaps tables and updates the schema version inside a single
`BEGIN IMMEDIATE` transaction. Any failure rolls back and leaves version 1
untouched; a second run reports `already_current`; a rewritten proof, a
mismatched goal/incarnation or an existing swap target fails closed. Cursors,
operation IDs, commit digests, provider revisions, receipts, events and scan
pages are byte-identical after the migration.

Retained projections keep every JSON object key the version-1 provider accepted,
including an empty key and a `__proto__` key: a database the previous provider
could read must not become one the version-2 provider cannot. The replay proof
is what keeps that promise honest, because identical identity and digest columns
alone would not show that a migrated state log is unreadable.

A version-1 database that published only its schema and metadata — the state a
goal leaves behind when it selected the provider and never committed — migrates
to an equally empty version-2 database instead of failing, so the operator is
never left with a database that neither provider accepts.

Run it from the repository checkout, with `--execute` omitted for a safe plan:

```sh
node --no-warnings --experimental-sqlite --experimental-strip-types \
  examples/coordination/sqlite-authority-migration.ts \
  --directory "$RUNTIME_ROOT/authority/sqlite-v0" --goal-id example
```

Add `--execute` (optionally with `--expected-identity`, which refuses a database
whose stored incarnation is not the one the operator names) to migrate. The
entry point rewrites only that Goal's database; it does not change provider
selection, promote a goal, or enable cross-host writes. Keep the pre-migration
database copy until the promoted Goal has been validated. A production cutover
command, migration manifest and reverse export remain separate deliverables.

### Qualification holds / 资格保留项

The report's `passed` rows apply only to their named axis and sample counts.
`failed` measurements remain failed. The split storage-write rows —
`logical_write_growth`, `wal_traffic_growth` and `lock_wait_observed` — carry
the <=15x cumulative-growth budget as per-commit traffic measured at both
depths, and an invalidated window or missing probe is missing evidence, never a
pass from the surviving columns. `missing` rows still include whole-run WAL
totals, pure busy-handler time, physical device writes, steady-state RSS proof,
the full domain profile, 1 MiB and 300k headroom, 24-hour consumer lag,
large-history recovery, fenced backup/restore, supported upgrades/rollback,
OS/runtime coverage and a real >=10-day soak. Those holds still block profile
promotion. Accelerated volume never substitutes for elapsed time, and running
this command starts no soak.

Retained state is measured where the formal profile runs: an axis reports its
checkpoint count, replay budget, recovery tail and retained projection/delta
bytes, and `bounded_retained_state` compares them against one full copy per
retained commit. That row stays `missing` for a rehearsal, exactly like every
other formal budget.

SQLite 资格参考使用 Node 22.22.3／SQLite 3.51.3；打开前同时检查实际 WAL 修复版本
和 statement 关闭行为。公开 Node 最低版本 22.18 继续用于默认 File 路径。显式
SQLite 选择遇到不合格 runtime 会拒绝，不会改默认 provider 或静默回退。

托管 Effect runtime 按用户与源码修订复用，因此真正服务某个 Goal 的 Node／SQLite
不一定等于调用进程从 PATH 解析到的那一份。runtime info 文件记录
`runtime_identity`（`node_version`、`sqlite_version`、`sqlite_authority_qualified`），
`loopx doctor` 会报告该身份；当正在服务的 runtime 不合格时，它还给出重启建议。
只安装合格 Node 不足以修复一个已经在运行的 runtime：用

```sh
loopx doctor --restart-runtime
```

结束后，下一次 control-plane 请求会按当前 PATH 启动新 runtime；等待其 idle
自动退出等效。非复用 runtime 的直接 CLI 调用只报告同一资格失败、不给重启步骤，
因为直接换成合格 PATH 重跑一次即可。

默认无参数命令从旧的 4 KiB/100k 改为小型 `rehearsal`，只验证工具和不变量；
正式 64 KiB、10k/100k 对照必须显式选择
`matched-64k`。`--cli` 分开记录完整 CLI 冷启动与 warm store，返回分位数、样本数、
RSS 和文件大小；没有量到的累计 WAL／逻辑写入和纯锁等待保持 missing。
应用 JSON 字节不能替代底层写入量，WAL 最终归零不能证明没有写入放大。

进程中断与 SQLite 容量注入在一次性合成数据库上运行，不等于断电、真实文件系统
耗尽、长期 consumer backlog 或完整恢复验证。首批测量允许保留 failed/missing；
>=10 天自然时间 soak、迁移和晋升分别评审与授权。本入口不改变持久格式、Todo
语义、默认 provider 或任何活跃 Goal。

版本 2 把“每个提交都保留一份完整投影”改成“每个窗口一个检查点 + 每提交一条精确
delta”：活跃头读取只用自己的行、对应提交和游标连续性自证，历史读取最多重建一个
窗口，完整归档由 `verifyAuthorityHistory` 线性审计。版本 1 数据库需要显式迁移
（`examples/coordination/sqlite-authority-migration.ts`，默认只做 plan，`--execute`
才写入，`--expected-identity` 可拒绝并非操作者所指的 incarnation，失败保持 v1
原样）。只发布过 schema 与 metadata、从未提交的 v1 库会迁移成同样为空的 v2 库，
不会让操作者落在两个 provider 都不接受的状态。迁移不改 cursor、operation id、
commit digest、provider revision、receipt、event 或 scan 页面字节。迁移在提交前
还会把刚写入的表读回来、用 store 自己的 delta 解码器重放一遍：只核对搬过去的
标识与摘要无法证明新的状态日志可读。v1 能接受的 JSON key（包括空字符串和
`__proto__`）在 v2 中保持同样的数据语义，迁移不会把原本可读的库变成读不出来的
状态。
