# Public boundary scan: bounded I/O / 公共边界扫描：有界 I/O

## Contract / 契约

`scan_public_boundary` still enumerates the full requested tree, including Git-owned
files inside otherwise pruned directories. Its existing owner decides which
local-private files to exclude **before** submitting any reads. Eligible files are
reopened on every request. The filesystem adapter overlaps at most eight reads and
returns them in the original sorted order; it does not cache scan results or
change leak rules, policy, Git ownership, counts, or error handling.

`scan_public_boundary` 仍扫描完整的指定目录，包括通常会被剪枝、但属于 Git
版本控制的文件。既有扫描 owner 在提交读取之前排除未跟踪的本地私有文件。
每次请求重新打开所有符合条件的文件；文件系统适配器最多并发读取八个文件，
按原来的排序返回结果，不缓存扫描结果，不改变泄漏规则、策略、Git 所有权、
计数及错误处理。

The bound is a file-count bound, not a byte-budget or atomic filesystem snapshot.
Unreadable-file observations and Unicode decoding behavior retain their existing
semantics. An unexpected reader failure is not downgraded to a clean scan. Workers
only read UTF-8 bytes: traversal, policy, classification and state writes are not
delegated to them.

限制针对待处理文件数量，不是字节预算，也不承诺原子文件系统快照。
不可读文件和 Unicode 解码错误保留既有语义；意外读取异常不会被当成扫描通过。
工作线程只读取 UTF-8 内容，不承担目录遍历、策略判断、分类或状态写入。

## Placement / 实现边界

The change is a Python filesystem adapter beneath the existing Python scan owner.
It does not add a second control-plane decision source. Moving the scan policy or
completion/deadline authority to a new owner is out of scope; the existing
TypeScript transaction and settlement owners remain unchanged. This bounded I/O
repair need not wait for the larger TypeScript migration.

这次改动是既有 Python 扫描 owner 下面的文件系统适配器，不新增平行控制面决策源。
不迁移扫描策略，也不改变完成、时限或结算权威；既有 TypeScript 事务及结算
owner 保持不变。这个有界 I/O 修复不以前置完成整个 TS 重构为条件。

## User paths / 用户入口

CLI checks and quota preparation reuse the same scan and response projection.
There is no new setting, CLI flag, frontend control or Lark command. The existing
frontend status hot path still explicitly defers the repository scan; it must not
be reported as a completed public-boundary check. Managed/Lark consumers keep the
same quota gate rather than a separate optimization-specific result.

CLI 检查及 quota 准备继续复用同一扫描和响应投影，没有新增配置、CLI 参数、
前端控件或 Lark 命令。既有前端状态热路径仍明确推迟仓库扫描，不能冒充已完成
公共边界检查。Managed/Lark 消费者使用原有 quota 门禁，不另建优化专用结果。

Therefore no packaged frontend rebuild or layout change is required. Existing
status-hot-path regression checks must continue to prove the explicit deferred
state. Full scan and real CLI tests cover the changed entry path.

因此不需要重建前端包或更改布局；既有状态热路径回归必须继续验证明确的 deferred
状态。完整扫描及真实 CLI 测试覆盖本次改变的入口。

## Qualification / 验证

Run the unchanged scan regressions together with the bounded-reader tests:

把既有扫描回归与有界读取测试一起运行：

```sh
uv run --extra test python -m pytest \
  tests/control_plane/test_public_boundary_parallel_reads.py \
  tests/test_contract_public_boundary_prefilter.py \
  tests/test_contract_scan_unreadable_files.py \
  tests/test_contract_credential_pattern_precision.py \
  tests/test_public_package_lock_boundary.py -q
```

Coverage includes actual overlapping reads, bounded input consumption, stable
output order, one read per eligible input, private exclusion before submission,
tracked-file policy, package-lock registries, Unicode matching, read errors and
fresh observations after file edits/additions/deletions. The unchanged real CLI
scheduler matrix retains every assertion and caller.

覆盖真实读取重叠、输入消费有界、结果排序稳定、每个符合条件的输入仅读取一次、
提交前私有文件排除、跟踪文件策略、package-lock 仓库来源、Unicode 匹配、读取
错误及修改／新增／删除文件后的新观察。既有真实 CLI 调度矩阵保留全部断言和 caller。

Record same-input base/head timings, source revisions and cold/warm conditions in
the PR qualification evidence. An I/O improvement does not establish that a whole
multi-command completion batch meets its frozen synchronous deadline. Do not
relax validation declarations, assertions, source fences, lease witnesses or
debit rules to report a latency repair as complete.

在 PR 验证证据中记录同输入 base/head 耗时、源码版本及冷／暖态条件。
I/O 改善不证明整个多命令完成验收已满足冻结同步时限；不得通过放松验收声明、
断言、source fence、租约凭证或扣额规则来宣布耗时修复已全部完成。
