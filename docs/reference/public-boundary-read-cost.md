# Public boundary scan: bounded I/O / 公共边界扫描：有界 I/O

## Contract / 契约

Ordinary `status`, quota decisions, initial planning and native Turn/heartbeat
launch or checkpoint continuation **defer** repository public-boundary scanning.
They still re-read current execution authority: Stop, pause, compute, Goal/Agent,
source, claim/lease and the original receipt/action/settlement binding. Deferral
does not certify public safety. The existing status checks explicitly say
`public boundary scan deferred`; no execution setting or permission bypass is
introduced.

普通 `status`、quota 决策、首次规划、原生 Turn/heartbeat 启动及 checkpoint 接续
默认**推迟**仓库公共边界扫描，仍复查 Stop、暂停、compute、Goal/Agent、source、
claim/lease 和原 receipt/action/settlement 绑定。推迟不等于公开安全；既有状态检查
明确显示 `public boundary scan deferred`，没有新增执行配置或权限绕过。

Explicit `loopx check --scan-path <outgoing-file-or-directory>` and the existing
premerge check scan selected outgoing content. Repeat `--scan-path` for multiple
files, including a saved diff, attachment or draft body. Premerge selects existing
changed files. This is not a universal outbound interceptor: generated-text
presentation guards retain their separate existing owner and policy; arbitrary
uploads/messages are not automatically covered by a successful file check.

显式 `loopx check --scan-path <拟外发文件或目录>` 与既有 premerge 检查选定外发内容。
多个文件可重复传 `--scan-path`，也可选择已保存的 diff、附件或正文草稿；premerge
选择当前存在的变更文件。这不是统一外发拦截器：生成文本的 presentation guard
保留既有独立 owner 和策略，任意上传或消息不因文件检查成功而自动获得覆盖。

The existing scanner retains all rule locations in `hits`. Literal credentials,
private source/path content and non-public registries remain blockers in
`blocking_hits`. A private IP alone is ambiguous: it remains a located warning,
as do existing credential environment/example references. These warnings do not
block the check and are labelled **completed with warnings**, never clean. This
is heuristic assistance, not proof that arbitrary content is safe or secret.

既有扫描器在 `hits` 保留全部规则定位，`blocking_hits` 保留字面凭据、私有来源/
路径内容及非公开 registry 等阻断项。单独内网 IP 属于含糊信号，仍显示带位置的
提示；原有凭据环境变量/示例引用同样提示。提示不阻断检查，显示为
**completed with warnings**，不称 clean。规则是辅助检查，不能证明任意内容安全
或含有秘密。

Missing roots, unreadable directories/files and undecodable UTF-8 fail the check.
Explicit private-file/directory selection cannot hide behind Git ignore or
directory pruning; tracked private state is also blocked. The existing opt-in
tracked-document-URL policy still applies to otherwise public files, and is
re-read on each check; it does not authorize private-state export. Untargeted,
untracked private state stays excluded **before** file reads.

缺失根目录、不可列目录、不可读文件及无法解码的 UTF-8 均使检查失败。显式选择
私有文件/目录不能靠 Git ignore 或目录剪枝得到空扫描成功；已跟踪私有状态也阻断。
原有显式 tracked-document-URL 策略仍适用于其他公开文件，每次检查重新读取；它
不授权私有状态导出。未被选作外发目标的未跟踪私有状态仍在读取**之前**排除。

`scan_public_boundary` enumerates the full selected tree, including Git-owned
files inside otherwise pruned directories. Eligible files are reopened on each
explicit check. The filesystem adapter overlaps at most eight reads and returns
them in sorted order; it caches no content or result.

`scan_public_boundary` 遍历完整指定树，包括通常剪枝目录中的 Git 文件。显式检查
每次重新打开符合条件的文件；文件系统适配器最多并发读取八个文件，按排序返回，
不缓存内容或结果。

The bound is a file-count bound, not a byte-budget or atomic filesystem snapshot.
An unreadable or undecodable input is not downgraded to a clean scan. Workers
only read UTF-8 bytes: traversal, policy, classification and state writes are not
delegated to them.

限制针对待处理文件数量，不是字节预算，也不承诺原子文件系统快照。
不可读文件、Unicode 解码错误及意外读取异常不会被当成扫描通过。
工作线程只读取 UTF-8 内容，不承担目录遍历、策略判断、分类或状态写入。

## Placement / 实现边界

Filesystem classification and explicit check reporting remain in the existing
Python scan/check owner and bounded reader. There is no second policy store or
control-plane decision source. Existing TypeScript admission, transaction,
completion/deadline and settlement owners remain unchanged.

文件系统分类及显式检查报告保留在既有 Python scan/check owner 和有界 reader，
没有第二份策略存储或平行控制面决策源；既有 TypeScript 准入、事务、完成、
时限和结算 owner 不变。

## User paths / 用户入口

CLI checks run the scanner; operational CLI, managed hosts and Lark use the same
status owner with scanning deferred by default. The frontend status path already
defers it. There is no new setting, CLI flag, frontend control or Lark command.
The compact status projection keeps the deferred check text rather than adding
repository scan bodies to every ordinary response.

CLI 显式检查执行扫描，运行用 CLI、managed host 与 Lark 复用既有状态 owner，
默认推迟扫描；前端状态路径此前已推迟。没有新增配置、CLI 参数、前端控件或
Lark 命令。紧凑状态投影保留 deferred 检查文字，不向每次普通响应加入仓库扫描正文。

The operational `status`, `diagnose`, `review-packet`, `quota`, readiness, agent
directory and global-summary commands, plus Turn `plan`/`managed-step`/`run-once`, do not accept `--scan-root` or
`--scan-path`. These retired options are rejected rather than silently ignored.
The status collector and its cache no longer accept or key on scan scope or an
optional scan switch. Use explicit `check` for outgoing selections.

运行用 `status`、`diagnose`、`review-packet`、`quota`、readiness、Agent 目录、
全局汇总及 Turn 的 `plan`/`managed-step`/`run-once` 不再接受 `--scan-root` 或 `--scan-path`，
旧参数会被拒绝，不会静默忽略。状态收集器及缓存不再接收扫描范围或可选扫描开关，
也不以扫描范围分隔缓存；外发内容选取使用显式 `check`。

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
  tests/test_public_check_disclosure.py \
  tests/canary/test_premerge_public_boundary_run.py \
  tests/control_plane/test_benchmark_current_admission.py \
  tests/control_plane/test_benchmark_checkpoint_admission.py \
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
