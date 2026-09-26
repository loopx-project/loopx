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
```

File/SQLite exports restore into either File or SQLite. Restore creates a new
provider identity/revisions while preserving logical history and receipts; it
never selects the restored directory as live authority. This avoids one
converter for every pair of storage formats. PostgreSQL's existing archive
source contract remains unchanged; authenticated service activation, cutover
and PostgreSQL destination administration are separate work.

Old raw backups can be copied into an **isolated** provider directory, with their
original identity and canonical filename, then upgraded and exported. Never
rewrite a schema label or restore an old backup over newer acknowledged writes.
Binary rollback requires the target runtime to pass `--require-current`; an old
binary without that gate is not automatically activated. Data rollback and
provider cutover require their own reviewed, fenced recovery operation.

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
