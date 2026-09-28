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
  must remain open, active and pending. A monitor must remain open, have a
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
- The Todo remains open with its original completion validator and canonical
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

## 中文

已准入的 advancement Turn 可以发现真实依赖，以
`monitor_changed:<todo_id>` 或 `todo_done:<todo_id>` 登记等待，并保留独立可执行
的 successor。过去无扣额阻塞结算只支持 1–30 分钟 `resume_at`，合法因果等待
反而会卡住旧 Turn 和独立工作。

现有 TS quota settlement owner 新增 `quota_blocked_causal_wait_v0` 核验，Python
只传当前 Todo 事实，不另建判断源。`quota.settlement.read` 依据
`loopx_quota_blocked_wait_request_v0` 区分预检与原持久结算读回。

- 复用 Todo resume owner，以当前开放、active、pending 的 Agent Todo 与唯一注册
  依赖重算条件。Monitor 须仍开放，非负 generation 与登记基线精确相等；目标
  已完成、归档、缺失、自引用、格式错误、代际推进／倒退或陈旧投影均不算等待
  证明；Monitor 当前 generation 必须显式存在。
- 写回冻结必要依赖事实与观察时间；仍要求精确 Goal/Agent/Todo/Turn、准入 guard、
  typed blocked observation 及持久回执。历史重放不按今天的依赖状态重开旧 Turn。
- `typed_blocked_writeback_no_spend` 仅含 validation 与 durable-writeback 回执，
  不扣额、不计交付进展。精确刷新幂等重放；已关闭身份的 spend 请求不再追加，
  已有真实扣额不会被抹去。
- Todo 保持开放、原验收器和 canonical 等待不变。新 Turn 可选独立工作；何时恢复
  仍由原 Todo resume 语义判断。不得用短定时器替换依赖、强迫提前 poll，或将
  Turn 结算当成 Todo 完成。
- 保留旧 v0 有界等待与 promoted Turn 自有五分钟重试。降级前须用兼容运行时
  完成或核对因果回执；不删除 writer fence，不改写历史。

产品入口变化是 CLI／managed Turn 的阻塞写回和结算读回，不是新增配置。
Dashboard 已消费 canonical 等待与回执，Chat／Lark 仍复用 Todo update owner；
不新增前端控件、Lark 命令或独立 UI 权威。File、SQLite 的真实 CLI／provider
验收覆盖两种依赖、重放、零扣额、原验收器保留与下一 Turn 独立选择；不据此
宣称投研真实采用或 PostgreSQL 资格已通过。
