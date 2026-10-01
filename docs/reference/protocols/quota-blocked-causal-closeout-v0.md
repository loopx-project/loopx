# Blocked causal Turn closeout / 因果等待的阻塞 Turn 结算

## English

An admitted advancement Turn can discover a real dependency and register a
`monitor_changed:<todo_id>` or `todo_done:<todo_id>` wait with an independently
runnable successor. Previously, blocked no-spend closeout accepted only a
1–30-minute `resume_at` retry. A valid causal wait could therefore leave the
original Turn unsettled and prevent independent work.

The existing TypeScript quota settlement owner now accepts either the bounded
retry or a `quota_blocked_causal_wait_v0` proof. Python transports current Todo
facts; it does not implement a second wait decision. The existing
`quota.settlement.read` method distinguishes the preflight request schema
`loopx_quota_blocked_wait_request_v0` from ordinary durable readback.

- Preflight recomputes the existing Todo resume condition from the current
  waiting Todo and its unique registered dependency. The waiting Agent Todo
  must remain open or deferred, active and pending. A monitor must remain open, have a
  captured non-negative generation, and still match that baseline exactly.
  A completed, archived, missing, self-referential, malformed or stale target
  is not proof; the monitor's current generation must be explicit.
- The writeback freezes only those dependency facts and an observation clock.
  Exact Goal/Agent/Todo/Turn identity, the admitted guard, a typed blocked
  observation and the durable writeback receipt remain mandatory. Historical
  readback uses the frozen facts, not today's dependency state.
- Closeout returns `typed_blocked_writeback_no_spend`, with validation and
  durable-writeback receipts, no debit and no delivery credit. Exact refresh
  retry replays the same result. A later spend request is a no-op for this
  already-closed identity; an existing debit is never erased.
- The Todo remains unfinished with its original completion validator and canonical
  wait. A fresh Turn can select independent work; existing Todo resume semantics
  still decide when this Todo is ready. Do not replace a causal dependency with
  a short timer, force an early monitor poll, or treat closeout as completion.
- Legacy `quota_blocked_retry_v0` and the promoted Turn-owned five-minute retry
  retain their previous behavior. Old runtimes cannot recognize causal proofs;
  finish/reconcile them with a compatible runtime before rollback. Do not remove
  a writer fence or rewrite historical receipts.

The affected user path is CLI/managed-Turn blocked writeback and settlement
readback, not a new configuration. Dashboard already reads canonical
`resume_when`, `resume_ready` and resume receipts; Chat/Lark Todo actions use
the same Todo update owner. These projections do not change, so this slice
adds no frontend control, Lark command or separate UI authority. File and SQLite
CLI/provider acceptance checks both dependency kinds, replay, no debit,
original validator preservation and independent next-Turn selection. It does
not claim live research adoption or PostgreSQL qualification.

### Suspending leased work

For an open, leased Agent Todo, the current claim/lease owner can use the
existing `todo update` command with `--status deferred --resume-when
 todo_done:todo_dependency --reason "Dependency pending"` and the current
`--task-lease-idempotency-key` / `--task-lease-expected-version`. Submit only
those lifecycle fields. Todo deferral and lease release commit in one provider
CAS, in legacy as well as hard-lease mode when retained lease lineage exists.
Dry-run changes nothing; retry replays the receipt. Ownership, scope, work
requirements and completion validation cannot be amended through this path.
Monitor-driven automatic waiting retains `status=open`; its existing authoring
contract rejects deferral so a generation change can make it runnable again.
Reopen explicitly deferred work with `--status open --clear-resume-when` and acquire a fresh execution
lease before work; the old proof remains invalid.

A `pr_merged` condition is still a valid Todo scheduling condition, but a PR
number alone is not a qualified blocked-closeout proof. Register a real
monitor or dependency Todo and use `monitor_changed` / `todo_done` for causal
closeout. Unsupported PR waits now name this recovery route explicitly.

## 中文

已准入的 advancement Turn 可以发现真实依赖，以
`monitor_changed:<todo_id>` 或 `todo_done:<todo_id>` 登记等待，并保留独立可执行
的 successor。过去无扣额阻塞结算只支持 1–30 分钟 `resume_at`，合法因果等待
反而会卡住旧 Turn 和独立工作。

现有 TS quota settlement owner 新增 `quota_blocked_causal_wait_v0` 核验，Python
只传当前 Todo 事实，不另建判断源。`quota.settlement.read` 依据
`loopx_quota_blocked_wait_request_v0` 区分预检与原持久结算读回。

- 复用 Todo resume owner，以当前 open 或 deferred、active、pending 的 Agent Todo 与唯一注册
  依赖重算条件。Monitor 须仍开放，非负 generation 与登记基线精确相等；目标
  已完成、归档、缺失、自引用、格式错误、代际推进／倒退或陈旧投影均不算等待
  证明；Monitor 当前 generation 必须显式存在。
- 写回冻结必要依赖事实与观察时间；仍要求精确 Goal/Agent/Todo/Turn、准入 guard、
  typed blocked observation 及持久回执。历史重放不按今天的依赖状态重开旧 Turn。
- `typed_blocked_writeback_no_spend` 仅含 validation 与 durable-writeback 回执，
  不扣额、不计交付进展。精确刷新幂等重放；已关闭身份的 spend 请求不再追加，
  已有真实扣额不会被抹去。
- Todo 保持未完成、原验收器和 canonical 等待不变。新 Turn 可选独立工作；何时恢复
  仍由原 Todo resume 语义判断。不得用短定时器替换依赖、强迫提前 poll，或将
  Turn 结算当成 Todo 完成。
- 保留旧 v0 有界等待与 promoted Turn 自有五分钟重试。降级前须用兼容运行时
  完成或核对因果回执；不删除 writer fence，不改写历史。

产品入口变化是 CLI／managed Turn 的阻塞写回和结算读回，不是新增配置。
Dashboard 已消费 canonical 等待与回执，Chat／Lark 仍复用 Todo update owner；
不新增前端控件、Lark 命令或独立 UI 权威。File、SQLite 的真实 CLI／provider
验收覆盖两种依赖、重放、零扣额、原验收器保留与下一 Turn 独立选择；不据此
宣称投研真实采用或 PostgreSQL 资格已通过。

### 有租约任务的延期

当前 claim／lease 持有者可沿用 `todo update`，只提交 `--status deferred`、
`--resume-when todo_done:todo_dependency`、`--reason`，并带当前
`--task-lease-idempotency-key` 和 `--task-lease-expected-version`。
TS 在一个 provider CAS 内同时延期 Todo、释放租约；保留租约历史的 legacy
模式也适用。Dry-run 不写入，重试重放原回执，不允许夹带任务内容、权限或验收修改。
`monitor_changed` 的自动等待仍须保持 open，代际变化后才能自动进入可执行队列；
其原有 authoring 规则继续拒绝延期。显式延期的普通依赖任务恢复时用
`--status open --clear-resume-when`，执行前重新获取租约，旧证明仍失效。

`pr_merged` 仍是合法的调度等待条件，但 PR 编号本身不能证明阻塞结算所需的
真实依赖。应登记实际 monitor／依赖 Todo，以 `monitor_changed`／`todo_done`
完成因果结算；错误信息明确给出此恢复路径。
