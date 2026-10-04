# Quota Monitor Observation Receipt v0 / 配额监控观察回执 v0

## English

### Problem

A heartbeat Turn has exactly one quota settlement identity. When an
`advancement_task` is already bound to that identity, a newly due
`continuous_monitor` must not replace it. The monitor still needs a durable
observation receipt so that its cadence does not starve while long-running
advancement work remains active.

### Contract

- The heartbeat receipt's `todo_id` remains the only settlement Todo. Only
  that Todo may be used by `refresh-state` and `quota spend-slot`.
- `quota monitor-poll --todo-id <monitor>` may record auxiliary, no-spend
  observations in the same Turn only when each requested Todo is a due
  `continuous_monitor` visible to the same Agent. Admission checks the Todo
  authority as well as the bounded decision projection, so a due monitor is
  not rejected merely because it falls outside the compact list.
- Under a scoped User gate, an auxiliary observation is admitted only when the
  existing typed dependency owner proves every addressed live gate independent
  of that exact Monitor. Overlapping, global, conflicting or unknown scopes
  fail closed. The CLI projection and effect admission use the same rule; the
  effect rechecks the current complete Todo snapshot, so a cached command cannot
  bypass a newly blocking gate. New auxiliary provider plans also require a
  commit-head dependency guard: canonical providers evaluate the same typed rule
  on the head used by the existing projection revision CAS; legacy observations
  evaluate it inside the existing Todo mutation lock. A blocking gate committed
  first prevents observation writeback, including changes after preflight.
  This dependency result grants no approval or lease. Exact committed receipts
  remain historical replay. Frozen plans predating the guard retain their
  intended-effect identity, while any still-uncommitted auxiliary provider
  dispatch is upgraded to the current guard without rewriting its WAL. A receipt
  proves a historical outcome, never permission for a new mutation. Guarded canonical
  requests use `loopx_coordination_monitor_poll_request_v3`: an older receiver
  rejects that schema instead of silently omitting the required gate check.
  Unguarded callers retain their existing request and receipt identities.
- The offered auxiliary command includes its registry/runtime route, exact
  Monitor target and original Agent/Turn identity. It can be executed as shown
  from either the source or global registry without reconstructing a route.
- The monitor receipt records both `settlement_todo_id` and the observed
  monitor `todo_id`. They may differ; this never grants a second delivery or
  quota-spend identity.
- Each auxiliary observation has an operation identity scoped by the monitor
  Todo, or by a digest of `target_key` when there is no Todo id. Exact retries
  replay that observation; changed content conflicts only with the same monitor
  identity; another due monitor receives an independent no-spend receipt.
  Shipped turn-only receipts remain replayable for their original monitor.
- A receipt-bound monitor remains strict: another monitor cannot be substituted
  for the Turn's settlement Todo. Multiple auxiliary receipts never change the
  already-bound settlement identity.
- After an unchanged auxiliary observation, the original advancement Todo
  remains selected. A material observation may create its independently routed
  successor through the existing monitor contract, but it still does not
  replace the Turn's settlement identity.
- An executed turn-scoped poll returns `turn_continuation`. An exact match
  between `settlement_todo_id` and the observed `todo_id` closes the no-spend
  monitor Turn and requires a fresh `--turn-instance-id`. A different admitted
  monitor Todo is auxiliary: it records the observation independently of the
  original advancement's closeout. If closeout is pending, the Turn remains open
  for its durable writeback and single spend. If it is already settled, even a
  **first** due observation is allowed, and continuation reports
  `current_turn_settled=true`, `next_turn_required=true`; independent advancement
  still needs a fresh Turn. Replays read current verified closeout rather than
  reopening it from the receipt's historical continuation. Without an
  exact or typed auxiliary binding the response fails closed from claiming the
  Turn settled.

- Replaying `quota should-run` for an exact committed Monitor Turn must preserve
  its settled phase after user notifications, scoped gate fallback and other
  projections. Work-lane, execution obligation, interaction commands and
  scheduler view agree: no new poll, delivery, replan, refresh or spend in that
  Turn. Pending gates and independent work remain diagnostic facts; the next
  Turn recomputes them. The automation stays active and quiet between Turns.
  Completing, superseding or archiving the Monitor cannot reopen its committed
  Turn. The settlement reader reports `replay_phase=settled`; CLI replay retains
  the original identity in `heartbeat_receipt.settlement_identity`. The same
  receipt-bound identity is also projected as `selected_todo` and, when its
  recorded monitor item is available, `agent_lane_next_action` with
  `receipt_bound_monitor_phase=settled`. These are historical readback, not
  executable selection: `should_run=false`, `must_attempt_work=false` and
  `effective_action=heartbeat_settled_skip` remain authoritative. Consumers
  must not require the Monitor to remain in the open frontier or infer a new
  poll, delivery or spend from the presence of these identity fields.
  Uncommitted observation rows and auxiliary polls for another Todo do not
  qualify this closeout.

### Acceptance

The CLI path must prove that multiple due monitors can each update their cadence
and replay idempotently in one settlement Turn without spending quota, while a
guard replay continues to select the original advancement Todo. Existing
wrong-Todo tests for receipt-bound monitor Turns must remain passing.

Scoped-gate fallback now permits observations of independently scoped due
Monitors instead of rejecting all auxiliary polls whenever any User action is
pending. The blocked primary-Task lifecycle fence remains in force. With no
User gate, admission and no-spend settlement retain their existing behavior.

This corrects the previous default rejection of first auxiliary observations
after advancement settlement. Both call orders must retain one primary debit,
one receipt per observation identity and current due/actor/lease admission. It
does not add a configuration switch, relax Monitor authority or retry external
observations. Existing CLI/managed-Turn receipts provide the readback; no new
frontend or Lark command/settings owner is introduced.

### Canonical leased observations and recovery

For a promoted Goal, an existing Monitor execution can supply its current lease
key and version. Read the existing lease first; this command neither acquires
nor renews/releases it:

```bash
loopx task-lease inspect --goal-id "$GOAL" --todo-id "$MONITOR"
loopx quota monitor-poll --goal-id "$GOAL" --agent-id "$AGENT" \
  --todo-id "$MONITOR" --result-hash "$OBSERVED_HASH" \
  --task-lease-idempotency-key "$LEASE_KEY" --task-lease-expected-version "$VERSION" \
  --turn-instance-id "$TURN" --execute
loopx todo list --goal-id "$GOAL"
```

The caller must already have an admitted Monitor Turn and any required host
capabilities. External observation remains the caller's effect; `--result-hash`
does not perform a network poll. Add `--material-change --next-agent-todo ...
--next-action-kind ...` only for changed evidence that needs independent work.

- The registered actor, claim, binding, exclusions and current lease are checked
  against the same canonical revision as observation/generation/successor writes.
  A stale key/version, expired lease, soft-claim lease request or missing hard-lease
  proof rejects the complete mutation. Observation timestamps cannot revive a
  lease. The existing lease remains byte-for-byte unchanged.
  This closes a previous hard-mode hole: a Monitor with no lease could write
  merely because the old implementation only rejected retained leases.
- Canonical due monitors no longer carry the blanket unsupported-writeback
  marker. The scheduler can select them; selection itself is not a lease grant.
- New quota pending receipts use `quota_monitor_poll_pending_admission_v1` to
  freeze the original admitted decision and provider plan before business writes.
  After process loss, retry the **same Turn, observation, intent and original
  proof**. Its durable business receipt can settle after lease release/expiry,
  with the original decision as the event's `before` state. A new observation
  still needs current authority. Quota settlement never spends a delivery slot.
  The existing quota-index CAS remains enforced: an intervening index write
  causes an explicit conflict, not an unconditional settlement append.
- Lease-bearing quota/provider requests and provider plans use v1; proof-less
  requests retain v0, including their original digests. Completed v0 settlement
  receipts remain readable. Old v0 *pending* receipts have no frozen admission:
  they recover if current admission still holds, otherwise report
  `legacy_monitor_admission_unavailable` and preserve evidence for reconciliation.
  Do not fabricate a historical decision, delete the receipt or repeat a known
  committed effect under a fresh identity to bypass that condition.

File, SQLite and service-opened PostgreSQL share these transaction semantics.
This is not provider activation, new-Goal default selection, cross-host service
qualification or whole-Goal promotion. Unpromoted legacy Goals reject explicit
lease proof. Before downgrading to a version without this protocol, finish v1
pending settlements; older binaries cannot interpret them. Never disable a
writer fence to recover by writing an older Markdown projection.

The bounded real-source rehearsal clones read-only source state and adds only
synthetic records in disposable runtimes. It requires an isolated PostgreSQL URL:

```bash
LOOPX_TEST_POSTGRES_URL="$DISPOSABLE_POSTGRES_URL" \
uv run --extra test python examples/control_plane/authority-monitor-poll-rehearsal.py \
  --registry "$REGISTRY" --goal-id "$GOAL" --baseline-repo "$FROZEN_BASELINE" \
  --execute-isolated-postgresql
```

### Observation updates and reactivation

The issue-fix lifecycle caller also maintains grouped Monitor membership through
`update_goal_todo(..., monitor_metadata=MonitorPollObservation(...))`. Promoted
Goals now carry this input in the existing Todo update transaction's **v4**
request. The observation namespace accepts evidence/time/schedule input, never
raw counters or execution ownership. Only an optional reason and explicit
`status=open, no_followup=false` may accompany it; copy edits, configuration,
decision effects and completion remain with their existing commands.

For the existing public workflow:

```bash
loopx issue-fix pr-lifecycle --url "$PR_URL" --goal-id "$GOAL" \
  --claimed-by "$AGENT" --execute-transition
loopx todo list --goal-id "$GOAL" --role agent
```

The caller owns fetching PR metadata and grouping; the generic TS transaction
knows only Monitor facts. No frontend or Lark configuration/command is added.

- An ordinary observation requires an open, active Agent Monitor and the
  registered owner/binding/exclusion checks. A held lease additionally requires
  its current key/version; the observation never renews or changes it.
- Reactivation requires an unarchived, non-superseded completed Monitor and
  material evidence strictly newer than completion and not older than its last
  observation. It is admitted by the registered actor's current claim, binding
  and exclusions, without a delegated override. A supplied execution proof is
  rejected: a completed cycle cannot lend its grant to the next one.
- The same provider CAS reopens the Monitor, advances its observation generation,
  clears terminal markers and releases any retained active/expired lease.
  Already-released history remains byte-identical. Release preserves version and
  epoch; subsequent explicit acquire uses a **new key** and advances both. Old
  renewal/proof and old acquisition receipts cannot restore execution rights.
  Hard-lease mode also permits reactivation when no prior lease exists, but the
  next ordinary observation still requires a fresh lease.
- Both observation entry points share `todo_monitor_cycle.ts` admission. In
  `soft_claim`, a released leftover does not become execution authority: polling
  without proof uses current claim admission. A retained active lease or supplied
  execution proof is rejected. This fixes the former update/poll disagreement.
  Reactivation returns `monitor_lifecycle_transition`, including
  `execution_authority_granted=false`, the historical retirement and next
  admission requirement. A replay reports historical facts, not present rights.
- A stable observation effect ID is also the canonical operation ID unless
  the caller supplies an explicit update operation ID. Exact retry returns the
  original transition; a later completion remains completed. Changed intent
  under that operation ID fails. Replay grants no current write permission.
- Generation, status, lease retirement and receipt share one provider CAS. Existing quota poll
  and successor transactions continue using the same Monitor planner. Neither
  observation updates nor grouped reconciliation spend quota or replace Turn
  settlement. Reconciliation is per Todo, not an atomic batch across buckets.
- Grouped reconciliation reports `projection_delivery`; a later unchanged
  group drains the current projection without repeating business writes.
  Native priority-prefixed text can render without persisted derived title/
  priority fields; explicit disagreements still fail parity validation.

Legacy Goals retain their writer and the shared observation/time planner; atomic retained-lease retirement is canonical-only. This
intentionally rejects stale observations that previously reopened completed
work, and removes stale terminal markers from a new cycle. v0–v3 update request
identities and receipts remain unchanged; old runtimes reject v4 instead of
partially applying it. Keep compatible code for pending retries when rolling
back; never remove a writer fence. Provider defaults and promotion are unchanged.

The rehearsal above requires a frozen baseline with the already shipped leased
poll protocol. It proves old poll parity and the new observation-update delta,
using a complete read-only snapshot with disposable File/SQLite/PostgreSQL arms.

## 中文

### 问题

一次 heartbeat Turn 只有一个配额结算身份。当 `advancement_task` 已绑定该身份时，
新到期的 `continuous_monitor` 不得替换它；但监控仍需形成持久观察回执，否则长期
推进任务存在时，监控周期会永久饥饿。

### 契约

- heartbeat 回执中的 `todo_id` 始终是唯一结算 Todo；只有它可用于
  `refresh-state` 与 `quota spend-slot`。
- 仅当每个请求对象都是同一 Agent 可见且已到期的 `continuous_monitor` 时，
  `quota monitor-poll --todo-id <monitor>` 才可在同一 Turn 写入辅助、不计费
  的观察回执。准入同时检查 Todo 权威源与有界决策投影，不能仅因到期 monitor
  位于精简列表之外就拒绝它。
- 存在 scoped User gate 时，仅当既有 typed 依赖 owner 证明所有面向该 Agent
  的有效 gate 都与该精确 Monitor 独立，才允许辅助观察。重叠、全局、冲突或未知
  scope 均失败关闭。CLI 投影与 effect 准入复用同一规则；effect 按当前完整 Todo
  快照重新核验，旧命令不能绕过新出现的阻塞 gate。新的辅助 provider plan 还
  强制提交时的依赖 guard：canonical provider 在既有 projection revision CAS
  所使用的同一个 head 上执行 typed 规则；legacy 观察在既有 Todo 写锁内核验。
  阻塞 gate 先提交时，包括 preflight 后的变更，不得写入观察。依赖结论不授予
  批准或租约。已提交回执仍按历史结果精确重放；引入 guard 前的冻结 plan 保留
  原 effect 身份，仍未提交的辅助 provider dispatch 则升级到当前 guard，不改写
  其 WAL。回执证明历史结果，不授权新 mutation。带 guard 的 canonical 请求使用
  `loopx_coordination_monitor_poll_request_v3`；旧 receiver 拒绝该 schema，不能静默
  忽略必要的 gate 校验。不带 guard 的既有 caller 保持原请求与回执身份。
- 提供的辅助命令包含 registry／runtime 路由、精确 Monitor target 及原
  Agent／Turn 身份，从 source 或 global registry 均可直接执行，无须重建路由。
- 监控回执同时记录 `settlement_todo_id` 与被观察的 monitor `todo_id`。
  二者允许不同，但不会因此产生第二个交付或配额结算身份。
- 每个辅助观察按 monitor Todo 建立操作身份；没有 Todo id 时，按
  `target_key` 摘要建立身份。精确重试只重放该观察；同一 monitor 下内容变化
  只与该 monitor 冲突；另一个到期 monitor 获得独立的不计费回执。已发布的
  Turn-only 旧回执仍可对原 monitor 重放。
- 若 heartbeat 本身绑定的是 monitor，仍保持严格身份，不能把另一个 monitor
  替换为本 Turn 的结算 Todo；多个辅助回执也绝不改变既有结算身份。
- 辅助观察无变化后，原 advancement Todo 继续保持选中；若观察发生重大变化，
  可按既有 monitor 契约创建独立路由的 successor，但仍不替换本 Turn 的结算身份。
- 执行成功的 turn-scoped poll 会返回 `turn_continuation`。仅当
  `settlement_todo_id` 与被观察的 `todo_id` 精确一致时，才完成该 monitor Turn 的
  不计费结算，并要求后续使用新的 `--turn-instance-id`。不同但已准入的 monitor
  Todo 属于辅助观察：观察回执与原 advancement 的结算独立。若尚未结算，仍需完成
  durable writeback 与唯一一次 spend；若已经结算，**首次**到期观察同样可以登记，
  continuation 返回 `current_turn_settled=true`、`next_turn_required=true`，新的独立
  推进仍须新 Turn。重放按当前已核验结算读回，不用历史 continuation 重开旧 Turn。
  既非精确匹配、也无 typed auxiliary binding 时，响应
  必须失败关闭，不能宣称 Turn 已结算。

- `quota should-run` 重放精确已提交的 Monitor Turn 时，用户通知、scoped gate
  fallback 和其他投影都必须保留其 settled 状态。work-lane、执行义务、interaction
  命令和 scheduler 读回一致：本 Turn 不新增 poll、delivery、replan、refresh 或 spend。
  未决 gate 和独立工作保留为诊断事实，由新 Turn 重新计算；自动化保持 active quiet。
  Monitor 完成、被替代或归档都不能重开已提交的 Turn。结算读取返回
  `replay_phase=settled`；CLI 重放通过 `heartbeat_receipt.settlement_identity`
  保留原身份，不再投影可执行的 `selected_todo`。消费者应从回执读取历史身份，
  不要求 Monitor 继续出现在未完成列表中。未提交的观察行，以及针对另一个 Todo
  的辅助 poll，均不能构成该结算依据。

### 验收

CLI 端到端测试必须证明：多个到期 monitor 能在同一结算 Turn 中分别更新周期并
幂等重放、全程不消耗配额；随后重放 guard 仍选择原 advancement Todo。同时，
receipt-bound monitor Turn 的错误 Todo 替换测试必须继续通过。

scoped-gate fallback 现在允许观察与 gate 独立的到期 Monitor，不再因存在任何
待处理 User action 就拒绝全部辅助 poll。primary Task 已 blocked 时的生命周期
拒绝保持有效。不存在 User gate 时，准入与不计费结算沿用既有行为。

这是对原默认行为的修正：不再拒绝 advancement 结算后的首次辅助观察。两种调用
顺序均须保留一次主任务扣额、每个观察身份一个回执，以及当前到期／actor／租约
准入。不新增配置开关、不放宽 Monitor 权限、不重新执行外部观察。CLI／managed
Turn 复用现有回执读回；不另建前端或 Lark 命令／配置权威。

### Canonical 带租约观察与恢复

已晋升 Goal 的 Monitor 若已有执行租约，可用上述命令读取租约，并传入当前 key 和
version。调用方须已有合法 Monitor Turn 和所需 host capabilities；外部观察由调用方
执行，`--result-hash` 不会自行发起网络轮询。只有新证据需要独立工作时才增加
`--material-change --next-agent-todo ... --next-action-kind ...`。

- 注册 actor、claim、binding、exclusion 和当前租约在同一 canonical revision 上校验；
  观察、generation 与 successor 原子提交。过期、错 key/version、soft-claim 下携带租约
  或 hard-lease 下缺少凭据均整笔拒绝。旧观察时间不能复活租约；租约内容保持不变。
  同时修复旧 hard-mode 缺口：过去仅检查“是否保留 lease”，反而让没有租约的 Monitor
  在 hard mode 下写入；现在该分支明确拒绝。
- Canonical 到期 Monitor 不再被统一标为“不支持 writeback”，可进入调度选择；被选中
  本身不授予执行租约。此默认变化只影响 canonical Monitor 的调度可见性。
- 新 pending receipt 使用 `quota_monitor_poll_pending_admission_v1`，在业务写入前
  冻结原准入决策与 provider plan。进程丢失后，用**相同 Turn、观察、intent 和原凭据**
  重试；已提交业务的回执可在租约释放／过期后补结算，event 的 `before` 仍是原决策。
  新观察依旧需要当前权限；结算不消耗 delivery slot。
  既有 quota-index CAS 继续生效；若期间有其他 index 写入，则明确冲突，不能无条件追加。
- 携带租约的 quota/provider request 和 provider plan 使用 v1；无 proof 的请求保留
  v0 及原 digest，已完成的 v0 结算继续可读。旧 v0 pending 没有冻结准入：当前准入仍
  成立时可恢复，否则报告 `legacy_monitor_admission_unavailable`，保留证据供核对。
  不得伪造历史准入、删除回执，或换新 identity 重做已知提交的业务来绕过它。

File、SQLite 与经 service 打开的 PostgreSQL 共用此事务语义；这不等于 provider
启用、新 Goal 默认切换、跨 host 资格或整 Goal 晋升。未晋升 Goal 拒绝显式租约凭据。
降级到不支持此协议的版本前，应先完成 v1 pending settlement；旧程序无法解释它们。
不得通过关闭 writer fence、恢复旧 Markdown 写入来绕过恢复要求。

上述 rehearsal 命令只读源数据，在临时 runtime 中增加合成记录，比较冻结基线及三个
真实后端；要求隔离 PostgreSQL，输出限于计数和摘要，不写回原 Goal。

### 观察更新与再激活

issue-fix 分组 Monitor 的成员变化仍使用现有 `update_goal_todo` 与
`MonitorPollObservation`，晋升后改走 Todo update v4 事务。上面的公开
`issue-fix pr-lifecycle` 命令负责获取／分组 PR 事实；通用 TS 边界只处理 Monitor，
不新增 frontend／Lark 配置或命令。

- 观察是证据、时间和调度输入，不是 raw counter／owner patch；只可伴随 reason
  与显式 `status=open, no_followup=false`。普通观察要求 open／active Agent Monitor
  及当前注册、claim、binding、exclusion；保留 lease 时还须当前 key/version，且不修改租约。
- 再激活要求未归档、未 supersede 的 done Monitor，material 观察严格晚于
  completed_at 且不早于 last_checked_at；由当前注册 actor、claim、binding、exclusion
  准入，不接受 delegated override 或旧 execution proof。
- 重开、generation、completion 标记清理、旧 active／expired lease 的 release 与
  receipt 在同一 CAS 提交；已 released 的历史记录保持不变。Release 保留 version／epoch，
  下次显式 acquire 必须使用新 key，并推进两者。Hard-lease 下没有历史 lease 也可重开，
  但后续普通观察仍须新租约；旧回执不会恢复执行权。
- update 与 quota poll 共用 `todo_monitor_cycle.ts`。Soft-claim 下 released 历史记录
  不构成第二个权威源，无 proof 的观察按 claim 准入；active 遗留租约或携带 proof 则拒绝。
  此处修复了两个入口过去的不一致。返回的 `monitor_lifecycle_transition` 明确标明
  `execution_authority_granted=false`；重放描述历史转换，不代表当前权限。
- 未指定显式 update operation ID 时，稳定观察 effect ID 同时作为 canonical
  operation ID。精确重试返回原 transition，不重开后来完成的任务；同 ID 不同意图
  被拒绝。历史成功不等于当前写权限。
- generation、status、receipt 在同一 CAS 提交；既有 quota poll／successor 仍复用
  同一 planner。观察／分组维护不消耗 quota，不替代 Turn settlement；多个 bucket
  仍逐 Todo 提交，不声称全局原子 batch。
- 分组结果保留 `projection_delivery`；无变化的重试也排空当前投影，不重做业务。
  带优先级前缀的 native 文本无需持久化派生 title／priority 即可显示，但显式字段
  冲突仍被 parity 校验拒绝。

Legacy 保留 writer 并共用观察／时间 planner；上述保留租约的原子退役仅在 canonical provider 实施：旧观察不再重开已完成任务，新周期不携带旧
终结标记。v0–v3 update identity／receipt 保持兼容，旧 runtime 整体拒绝 v4。
回滚须保留可恢复 pending retry 的兼容代码，不能关闭 writer fence；provider 默认
与 promotion 不变。上述演练的冻结基线须已支持 leased poll，以区分旧路径 parity
和新的 observation-update 增量。

### Qualification boundary for retained execution

The snapshot rehearsal now compares a frozen pre-fix baseline (which rejects
retained-lease reactivation) with real File, SQLite and an isolated PostgreSQL
service. It preserves the complete source Todo/lease population, adds only a
synthetic Monitor and successor, and checks equal final provider heads, fresh
execution epoch, immutable replay and unchanged unrelated records. Provider
conformance additionally covers released, expired and time-active historical
leases, both Todo wire schemas, preview, stale authority, CAS loss and response
loss. Python facade tests complete and reopen a leased Monitor with missing
Markdown, then acquire and observe through the actual CLI/runtime.

This closes the canonical Monitor cycle transition, not automatic executor
acquisition for grouped reconciliation. A grouped caller in hard-lease mode
still needs an admitted execution for later polling/completion; it must not
infer one from the reactivation receipt. No default profile, frontend setting,
Lark command, quota settlement authority or PostgreSQL deployment policy changes.
