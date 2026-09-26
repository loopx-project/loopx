# Authority format upgrade and File retained state

The File provider retains its `AuthorityStore` contract and `file_v0` routing
identity. The physical document changes from `loopx_file_authority_store_v0` to
`loopx_file_authority_store_v1`. This does not promote a Goal or select a provider.

## Storage and semantics

| | Old File document | Current File document |
| --- | --- | --- |
| Each committed row | Complete projection | Checkpoint at cursors 1, 65, 129, …; exact delta otherwise |
| Events, operation ID, original receipts | Retained | Retained without rewriting |
| Cursor and provider revision | Logical transaction identity | Identical after physical upgrade |
| Historical reads | Full stored projection | Reconstruct the same projection from nearest checkpoint |
| Runtime acceptance | Migration input only | Normal reads and writes |

File reuses the TypeScript state-delta codec already used by SQLite. Object-key
changes and array splices preserve all JSON data, including empty and `__proto__`
keys. Writers prove reconstruction. Revisions still hash the logical full
transaction, previous revision and store identity. The ledger remains logically
append-only even though File atomically replaces its physical envelope.

Cold reads verify every retained transaction and the final head; a valid head
cannot hide a corrupt old delta or receipt. Verified pagination reconstructs at
most 63 predecessor deltas plus the requested page. The exact-byte cache remains
bounded. File still reads/hashes and rewrites one retained file: this reduces
repeated data, not asymptotic growth. Cold verification can be slower. Measure
upgrade, cold verification, warm reads and steady writes separately.

## Format recognition

```bash
loopx --format json authority-archive inspect --source /absolute/store-or-backup
```

Recognition uses JSON schema tags or the SQLite file header plus database
metadata and `user_version`, not filename extensions. It reports artifact kind,
provider, physical format, Goal/store identity and migration route. Unknown
versions and inconsistent SQLite version pairs are rejected by automatic
upgrade. A recognized provider selector is only a routing record; PostgreSQL
and NoKV still require their configured provider service for export.

`metadata_only` recognition is not full history verification. Logical archives
are verified through their complete digest/seal contract; backup packages check
source bytes and lineage. Upgrade subsequently validates the complete store
under its publication boundary. Multiple local stores are reported/upgraded
independently, never silently chosen as a new live authority.

Legacy Markdown/sidecar capture, project registry envelopes and shadow/outbox
control records have different owners. They are not alternate File database
encodings and are not rewritten by this command. Whole-Goal migration must use
its source capture and writer-fence workflow.

## Automatic upgrade and backups

Normal readers and writers **do not accept the old File format**. Old parsing
belongs only to migration. There is no migration-on-read or first-business-write
conversion. SQLite's existing v1-to-v2 converter follows the same explicit gate.

Default local installation and Windows installation run the candidate's upgrade
command before launcher activation. `loopx update apply` for pip/pipx runs it
after package installation and before host updates or service restart. Canary
installation does not migrate stores. Source checkouts and externally managed
package updates use the same explicit command:

```bash
# Preview selected runtime; global --runtime-root and --registry are supported.
loopx --format json authority-archive upgrade
# Back up and migrate known runtime roots from existing project registrations.
loopx --format json authority-archive upgrade --all-known --execute
# Read-only compatibility gate, also used before binary rollback.
loopx --format json authority-archive upgrade --all-known --require-current
```

Discovery includes File and SQLite stores in each known runtime's authority
folders, including unselected shadows and File rollback documents. It does not
scan arbitrary home directories. Disconnected custom runtime roots must be
supplied explicitly. Selectors, registries, writer fences and execution leases
are not changed by format upgrade.

Each store has its own durable publication boundary:

- File holds the ordinary writer lock, verifies the entire old history, encodes
  and decodes the target, and compares logical history digests. It saves exact
  source bytes plus store identity under `format-backups/<source-sha256>/`,
  verifies the backup and syncs directory entries before atomic replacement.
- SQLite makes an online consistent backup, including committed WAL data. A
  disposable copy proves the backup through the actual v1-to-v2 converter.
  The source migration compares that history digest under `BEGIN IMMEDIATE`
  before adopting new tables. Concurrent source advancement aborts the upgrade;
  retry takes a fresh backup. It never silently discards intervening commits.
- A manifest records source/target format, identity and integrity evidence.
  File recovery uses manifest hashes and actual source/target bytes, not a
  mutable completion flag. Interrupted conversion is retriable: before publish
  the old store remains; after publish the new store is already current.

Unknown formats, corrupt history, failed backups or failed validation stop the
upgrade. A multi-store upgrade can have completed earlier stores when a later
one fails; the report retains those results. Retry resumes per store. It does
not pretend to roll back the whole runtime or overwrite later business writes.
For package-manager updates, a failed data upgrade does not undo the package
installation; service activation remains blocked until repair.

## Provider migration and recovery

Physical format upgrade preserves provider identity and old revisions. Changing
providers uses the existing portable logical archive as the interchange format:

```bash
loopx --format json authority-archive export --goal-id example --archive /absolute/history.ndjson
loopx --format json authority-archive verify --archive /absolute/history.ndjson
loopx --format json authority-archive restore --goal-id example --archive /absolute/history.ndjson \
  --destination /absolute/new-isolated-store --provider sqlite --archive-sha256 DIGEST --execute
# Independently audit the actual restored provider, not just its old success file.
loopx --format json authority-archive audit --goal-id example --archive /absolute/history.ndjson \
  --archive-sha256 DIGEST --destination /absolute/new-isolated-store
# Audit the selected runtime's retained prefix after it has received later writes.
loopx --format json authority-archive audit --goal-id example --archive /absolute/history.ndjson \
  --archive-sha256 DIGEST --allow-newer-head
```

File/SQLite exports restore into either File or SQLite. Restore creates a new
provider identity/revisions while preserving logical history and receipts; it
never selects the restored directory as live authority. This avoids one
converter for every pair of storage formats. PostgreSQL's existing archive
source contract remains unchanged; authenticated service activation, cutover
and PostgreSQL destination administration are separate work.

Restore first copies the input into a private temporary directory and verifies
that copy against the reviewed digest. All subsequent reads use that same copy.
Previously, replacing the original path between verification and restore could
write unreviewed rows into the isolated destination before the final seal check
failed. Replacement or in-place edits to the original now cannot change the
accepted restore input. A corrupt or wrong-digest copy fails before target
access. Normal completion/failure removes the temporary directory; abrupt process
death can leave private temporary files for normal system/operator cleanup.
Budget additional local disk space approximately equal to the archive size.

Resume checks the target's entire existing prefix before appending its missing
suffix. Prefix scans use at most 16 reconstructed transactions per page; each
original operation is also looked up through the provider's receipt index. The
same TS comparator serves restore and independent audit. Acknowledged new writes
also share a page proof; an uncertain write forces immediate readback before any
later write, without retrying the uncertain operation. Logical commits remain
individual CAS operations. SQLite resolves all requested operation IDs in one
read transaction and verifies each touched checkpoint window once per batch;
other providers use the same contract with scalar receipt lookup as a fallback.
This removes redundant replay without skipping old receipts, caching proof
across calls, or claiming constant memory independent of state size.
Input decoding retains one reconstructed state plus operation-ID uniqueness
tracking; target pages retain up to 16 full states. This is count-bounded paging,
not a new provider byte-budget guarantee.

`audit` is read-only and never creates a missing authority. It compares every
historical projection, event, operation ID and original receipt, including
receipt lookup cursor/version. Physical provider revisions may differ across
providers. A matching final head alone cannot pass. Reports expose a typed
reason and first failing cursor, not private state or receipt bodies.

- Default `exact` requires the target to contain exactly the archive's commits
  and remain at that head through readback. Concurrent advancement asks for a
  retry; it is not silently classified as equality.
- Explicit `--allow-newer-head` verifies only the archive's retained prefix and
  tolerates later append-only commits in the same store lineage. The report says
  `retained_prefix`; it does not certify those later commits against the backup.
- `--destination` reads the isolated restore binding to choose File or SQLite;
  without it, audit uses the selected runtime provider. A wrong binding, source
  digest, missing historical receipt or unavailable provider fails closed.

An audit proves retained data, not active execution safety. It does not transfer
leases, select a provider, remove a writer fence, or authorize rollback over
newer writes. `verified-restore.json` remains a historical completion receipt;
run `audit` for current evidence rather than trusting the file's presence.

Old raw backups can be copied into an **isolated** provider directory, with their
original identity and canonical filename, then upgraded and exported. Never
rewrite a schema label or restore an old backup over newer acknowledged writes.
Binary rollback requires the target runtime to pass `--require-current`; an old
binary without that gate is not automatically activated. Data rollback and
provider cutover require their own reviewed, fenced recovery operation.

## 恢复与审计

恢复先复制到权限受限的临时目录，再核对审核摘要，后续只读这一份副本。旧实现
在校验后重开原路径，可能先将被替换的内容写入隔离目标、最后才报错；现在原路径
被替换或原地改写不会改变已接受的恢复输入。正常结束会清理副本；进程被强制杀掉
可能留下私有临时文件。需预留约一份归档大小的额外磁盘空间。

续传先核对已有前缀，再追加缺失后缀；TS 的同一比对规则同时服务恢复与独立审计。
最多每页 16 条完整历史投影，逐笔核对原始回执。新写入仍是独立 CAS，但明确成功的
提交可共享分页回读；遇到不确定写入，立即回读证明后才可继续，绝不盲目重发。
SQLite 在一个读事务内按操作 ID 查询回执，同一批只重放一次每个涉及的检查点窗口；
其他 provider 通过同一接口回退到逐笔查询。不跨请求缓存证明，不跳过旧回执，也不
声称内存与状态大小无关。

`authority-archive audit` 不写业务状态、不创建缺失存储。默认 exact 要求当前目标
与归档完整一致；显式 `--allow-newer-head` 只证明归档对应的保留历史前缀，允许随后
追加，但不把之后的提交算成已审核。`--destination` 审计隔离恢复目录，否则审计
当前选择的 runtime provider。逐笔状态、事件、操作身份及回执查询都必须相符；
最终 head 一样不足以通过。不同 provider 的物理 CAS 版本无需相同。

审计不选择 authority、不转移执行租约、不撤销 writer fence，也不授权覆盖新写入。
`verified-restore.json` 是过去的完成回执，当前完整性应重新 audit。

## Qualification and limits

Tests cover legacy rejection, original historical identity/receipts, checkpoint
pagination, malformed history, backup damage, interruptions around rename,
competing migration processes, real SQLite backup/migration, and File/SQLite
archive interchange. CLI validation uses the managed TS runtime. An authorized
detached long-history snapshot additionally checks source immutability, exact
backup bytes and every historical transaction/receipt against the parent.

The CLI/install surfaces change; frontend and Lark business commands continue
using the unchanged provider contract and need no new settings. This does not
close default-provider promotion, D1–D3, PostgreSQL production qualification or
Python business-owner retirement. Native Windows execution still requires its
platform CI evidence; POSIX validation does not substitute for it.

## 中文要点

旧 File 每次提交都复制完整状态；新格式每 64 条保留完整检查点，其余保存差量。
事件、原始回执、操作身份、版本号和历史内容不变。复用 SQLite 的 TS 编码规则，
不删除 append-only ledger 抽象，也不切换 provider。

正常读写只接受新格式。安装／更新调用统一升级入口：先验证、自动备份并核对，
再迁移和读回；源码开发也可显式调用 `authority-archive upgrade --execute`。
迁移解析旧数据属于升级工具，不是长期运行的旧格式兼容分支。

同 provider 的格式升级保持身份和版本；跨 provider 则用逻辑归档导出、验证、隔离
恢复，生成目标 provider 的新身份和版本，保留历史事实及原始回执。迁移数据不等于
获得执行权，恢复目录不会自动成为线上 authority。

升级失败时可能已有部分 store 完成，必须据实报告并重试，不能覆盖之后产生的写入。
备份仍是旧格式，恢复时应先在隔离目录升级。只回退二进制并不等于安全回退数据。
该方案减少重复存储和后续写入耗时，但冷校验仍验证全历史，可能更慢。
