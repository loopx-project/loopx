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
Reopen explicitly deferred work with `--status open --clear-resume-when`.
The next edit follows the current mode's admission; the old proof remains invalid.
Submit this resume separately from note/evidence or work-requirement edits,
using a fresh `--update-operation-id` and current
`--update-expected-provider-revision`. Review the wait's resolution first;
the diagnostic retry is not evidence that it resolved. The existing lifecycle
retires inactive lease lineage atomically while preserving its version/epoch.
A live lease must be released first. A bundled edit's recovery names this
separate resume only when the current lifecycle fence permits it; explicit old
proof and foreign/excluded/bound actors remain rejected. After resuming, acquire
a fresh lease and use its returned proof for the pending edit when the mode
requires execution fencing. Under `soft_claim`, a permitted owner copy edit uses
no old proof or replacement lease; other edits retain their own admission.

A `pr_merged` condition is still a valid Todo scheduling condition, but a PR
number alone is not a qualified blocked-closeout proof. Register a real
monitor or dependency Todo and use `monitor_changed` / `todo_done` for causal
closeout. Unsupported PR waits now name this recovery route explicitly.

### Unavailable Monitor observation

An admitted open Agent `continuous_monitor` on canonical authority may discover
that its observation cannot be obtained. Close the original Goal/Agent/Todo/Turn
with `refresh-state --delivery-outcome outcome_gap --progress-result-class
blocked`, a stable blocker ID, evidence IDs and a normal vision decision. Do not
invent a `monitor-poll` hash, record control metadata as an account observation,
or spend. The existing settlement plan exposes this path.

The same typed quota owner freezes `quota_monitor_unavailable_v0` in the existing
`blocked_retry` receipt slot, with `observation_available=false` and the Monitor
scope. Exact admission, identity, evidence and durable writeback still gate
`typed_blocked_writeback_no_spend`. This closes only the attempted Turn: the Todo
stays open; `last_checked_at`, `next_due_at`, result hash, material generation,
cadence and expiry remain unchanged. It creates neither a retry clock nor an
advancement wait, material successor, completion or delivery claim. Existing
advancement retry/causal-wait semantics and genuine committed polls retain their
behavior. A committed poll cannot be rewritten as an unavailable attempt.

Exact replay returns the frozen receipt, with no duplicate writeback or debit.
After closing the original effect, reenter the current recovery host Turn to
select independently eligible work; do not create a third identity or rebind the
old one. Missing capability admission or evidence and wrong bindings still fail.
File and SQLite CLI acceptance covers this recovery, preserved observation
fields, capability restoration, committed-poll rejection and zero debit.
PostgreSQL and live host adoption remain separate qualifications. Before rollback,
reconcile these receipts using a runtime that recognizes the new proof.

This is a bounded R1/S2/S10 recovery slice in the existing quota owner. The legacy
`isBoundedBlockedRetry` entry point remains for active callers, but now qualifies
Monitor unavailable proofs as well; only the advancement schema projects a
bounded retry. No Python decision owner, new switch, frontend control or Lark
authority is added. App/Chat consume the same CLI settlement guidance; their
packaged surfaces are not qualified by the CLI tests.

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
`--status open --clear-resume-when`；剩余编辑遵循当前模式的准入，旧证明仍失效。
恢复应与 note/evidence、工作要求编辑分开，使用新的
`--update-operation-id` 和当前 `--update-expected-provider-revision`。
先核实等待条件已解决；诊断中的重试入口不证明条件已满足。既有生命周期以同一
CAS 结清非活跃租约，并保留 version/epoch；活跃租约须先释放。只有当前生命周期
允许时，捆绑编辑的拒绝结果才指出单独恢复路径；显式旧证明、外来 claimant、
excluded/bound 限制仍拒绝。恢复后，模式要求执行 fencing 时获取新租约，以返回的新证明提交剩余编辑。
`soft_claim` 中允许的 owner 文案编辑不使用旧证明，也不获取替代租约；其他编辑仍按原准入规则。

`pr_merged` 仍是合法的调度等待条件，但 PR 编号本身不能证明阻塞结算所需的
真实依赖。应登记实际 monitor／依赖 Todo，以 `monitor_changed`／`todo_done`
完成因果结算；错误信息明确给出此恢复路径。

### Monitor 观察不可取得

canonical authority 上已准入、开放的 Agent `continuous_monitor`，可能在尝试中
发现无法取得观察。用原 Goal/Agent/Todo/Turn 调用 `refresh-state`，提供
`outcome_gap`、typed `blocked`、稳定 blocker、证据 ID 与正常 vision 决策，沿既有
计划关闭本次尝试。不得编造 poll hash、把控制元数据记成账户观察或扣额。

同一 TS quota owner 在既有 `blocked_retry` 回执槽冻结
`quota_monitor_unavailable_v0`、`observation_available=false` 与 Monitor 范围；
精确准入、身份、证据和持久写回仍是无扣额结算门槛。只关闭原 Turn，Todo 仍开放，
检查/到期时钟、hash、material generation、cadence 与 expiry 不变。不生成重试时钟、
advancement 等待、material successor、完成或投递声明。旧 advancement 重试/因果等待
与真实 poll 保持原行为；已提交 poll 不能改写为不可观察尝试。

精确重放返回冻结回执，不重复写回或扣额；原 effect 关闭后，重新进入当前 recovery
host Turn 可选择独立合法任务，不新造第三身份或重绑旧 Turn。缺准入能力、缺证或错绑
仍拒绝。File/SQLite 的真实 CLI 验收覆盖恢复、观察字段保留、能力恢复、已 poll 拒绝与
零扣额；PostgreSQL、本机真实采用及 App/Lark 打包入口仍分阶段验收。降级前用支持新
证明的运行时核对回执。

这是既有 quota owner 的 R1/S2/S10 有界修复，兼容保留活动调用者使用的
`isBoundedBlockedRetry` 名称；只有 advancement schema 产生有界重试投影。
不新增 Python 判断源、开关、前端控件或 Lark 权威；CLI 计划提供同源说明。
