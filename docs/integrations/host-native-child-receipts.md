# Host-native child receipts / 宿主原生子代理回执

## Contract / 契约

When an enabled `multi_subagent` coordinator works in an admitted Turn, it can
write a bounded `decision → result → parent review` record for a child created
through any host's native tools. The record uses the existing Goal rollout event
log, the Turn instance ID, a stable operation ID, and an opaque `entrypoint_id`.
It does not launch children, schedule another Turn, grant write authority, or
spend quota. The configured `max_children` is an upper bound, never an observed
live capacity or a required launch count.

启用 `multi_subagent` 的主 Agent 在已准入的 Turn 内，可以为任意宿主原生工具
创建的子代理记录有界的“决策 → 结果 → 主 Agent 验收”回执。回执复用现有
Goal 事件流，以 Turn ID、稳定操作 ID 和不限定宿主的 `entrypoint_id` 关联。
记录动作不会启动子代理、调度新 Turn、授予写入权限或消耗配额。
`max_children` 只是配置上限，不代表当前可用槽位，也不是必须启动的数量。

`native-child record` accepts a coordinator report and cannot select host
provenance. Managed Codex CLI and operation-equipped app-server Turns also
observe native collaboration items directly on their owned connection. A
successful spawn, failed call and observed child completion become durable
`host_observed` records before Turn settlement. A parent review is a separate
explicit record; a host completion does not adopt the evidence.

The shared projection distinguishes `host_observed`, `coordinator_reported`,
`mixed` and `unknown`. `host_attested` is true only when every decision in the
Turn came from the host. Configured capacity remains an upper bound. Failed
Codex collaboration items do not carry a typed capacity error, so the adapter
records `host_failed` and forbids report-based same-Turn retry rather than
classifying provider prose. Missing native events stay unknown. Persisted Codex
history is not used to reconstruct native activity because supported host
versions may omit collaboration items from that history.

`native-child record` 仍是主 Agent 上报入口，不能指定宿主来源。托管 Codex CLI
与启用操作工具的 app-server Turn 会在自身连接上直接观察原生协作事件，
把实际启动、宿主失败和观察到的结果写入同一事件流。结果完成之后仍须由主
Agent 明确记录验收；宿主完成不代表证据已被采纳。

共享投影区分宿主观察、主 Agent 上报、混合来源和未知；仅全部决策均来自
宿主时 `host_attested` 为真。Codex 的失败协作项没有容量错误码，适配器保留
通用 `host_failed` 和禁止同 Turn 上报重试的规则，不从错误文字推断容量。
缺少原生事件仍是未知。部分宿主版本的持久化历史会遗漏协作项，因此不用于
重建活动。第三方宿主上报仍不会自动获得宿主核验标记。

## Lifecycle / 生命周期

1. The admitted Turn guard must already have a settlement binding. The existing
   TS settlement readback verifies its exact original identity and reporting
   admission. Committed work-admission facts also admit lawful replan Turns;
   the recorder does not whitelist their status labels. Old ordinary guards
   without work-projection fields retain bounded compatibility, never a fallback
   for partial or negative facts. `record` rejects unregistered coordinators and
   disabled policy before writing.
2. Use `stage=decision` with a stable `operation-id` for `spawn`, `followup`,
   or a bounded `skip` reason. A host capacity rejection maps to the generic
   `host_capacity_exhausted` reason. Capacity rejection and typed host failure
   stop same-Turn spawn/followup retries while parent work may continue.
3. A started operation may get a typed `result` (`completed`, `failed` or
   `cancelled`). Any terminal result may receive a `deferred` or `rejected`
   parent review; `accepted` still requires a completed result and public-safe
   evidence and validation references. Result and nonadoption reason codes are
   optional compact opaque diagnostics, not a provider-specific vocabulary or
   authority. Omit them when unknown. Matching `operation` and `entrypoint-id`
   echoes are optional for result/review; conflicting echoes are rejected,
   including on replay. Raw prompts, host errors, transcripts, local paths and
   credentials are excluded.
4. Replay with the same identity and payload is idempotent; a conflicting
   payload is rejected. `read` and `agent-context --phase after_delegate_result
   --turn-instance-id ...` expose the same Turn read model. Goal status (JSON and
   Markdown) exposes
   the latest reported Turn only when the capability is enabled and a decision
   exists. The dashboard uses that status projection, and omits the activity
   line for unconfigured or unrelated Goals.
5. A new decision requires an open, work-admitted Turn and no begun closeout.
   Once closeout is pending or settled, an already-recorded started operation
   may still receive its result and parent review. This records late facts; it
   does not reopen the Turn. An absent decision cannot be backfilled through a
   closed Turn. Exact duplicates remain reads; conflicts remain errors. For a
   new append the readback is checked inside the existing event-log lock, with
   one coarse TS call per report, not a second Python phase rule.

1. Turn 须先有已提交的结算绑定；既有 TS 结算读回核验精确原身份及报告准入。
   已提交的工作准入事实同样覆盖合法重规划，报告入口不按状态名称建立白名单。
   没有工作投影字段的旧普通 guard 保留有界兼容，不能用来绕过不完整或否定的
   准入事实。未注册主 Agent 或未启用策略不能写入。
2. 用稳定 `operation-id` 写 `decision`，区分 `spawn`、`followup` 和有界理由的
   `skip`。宿主容量拒绝映射为通用 `host_capacity_exhausted`；容量拒绝和
   类型化宿主失败都停止同一 Turn 的启动或跟进重试，主 Agent 仍可继续工作。
3. 已启动操作可记录 `completed`、`failed` 或 `cancelled` 类型化结果。所有
   终态都可登记 `deferred` 或 `rejected` 评审；`accepted` 仍要求完成结果及
   公开安全的证据与验证引用。结果和不采用评审的原因码可选，只需是紧凑的
   不透明诊断标识，不限定供应商词表，也不授予权限；未知时省略。结果与评审
   可重复携带一致的 `operation`、`entrypoint-id`，冲突值包括重放时仍被拒绝。
   原始提示、宿主错误、对话、本地路径和凭据不进入回执。
4. 同一身份和内容重放幂等，内容冲突会被拒绝。`read` 与带 Turn ID 的
   `agent-context` 读取同一模型。Goal 状态的 JSON 与 Markdown 只在能力启用且确有决策时投影
   最近一轮；仪表板读取该投影，未配置或无关 Goal 不显示活动行。
5. 新决策要求 Turn 已准入、仍开放且尚未开始结算。开始结算或结清后，已经登记
   的 started 操作仍可接收结果及主 Agent 验收；这是迟到事实登记，不会重开 Turn。
   不能借已关闭的 Turn 首次补建缺失决策。精确重复仍是读取，内容冲突仍拒绝。
   新写入在既有事件流锁内重新核对读回，每次报告只调用一个粗粒度 TS 边界，
   不增加第二套 Python 阶段判断。

After a lawful deferred-Todo recovery, rerun `quota should-run` with the same
explicit Turn and Todo. If the original bound guard denied delivery and the
current guard explicitly admits work, quota appends a work-qualification receipt
under the event-log lock. The original event remains intact; settlement identity,
workspace causality and semantic guards stay on the original binding. Replays
reuse that receipt. Partial or negative facts, identity conflicts and begun
closeout cannot create a qualification. This grants no execution lease, child
host invocation or additional quota slot: acquire the required fresh lease and
keep the normal native-child policy and closeout steps.

合法恢复 deferred Todo 后，以同一显式 Turn 和 Todo 重跑 `quota should-run`。
原绑定 guard 曾拒绝交付、当前 guard 明确准入工作时，quota 在事件流锁内追加
工作资格回执，保留原事件、结算身份、工作区因果和语义门禁；再次进入复用该回执。
不完整或否定事实、身份冲突和已开始结算均不能升级资格。这不授予执行 lease、
宿主 child 调用或额外 quota；仍须取得要求的 fresh lease，遵守原生 child 策略和结算步骤。

Example / 示例：

```sh
loopx --format json --registry REGISTRY native-child record \
  --goal-id GOAL --agent-id COORDINATOR --turn-instance-id TURN \
  --operation-id OPERATION --stage decision --operation spawn \
  --outcome started --entrypoint-id HOST_ENTRYPOINT --execute
loopx --format json --registry REGISTRY native-child record \
  --goal-id GOAL --agent-id COORDINATOR --turn-instance-id TURN \
  --operation-id OPERATION --stage result --outcome completed --execute
loopx --format json --registry REGISTRY native-child record \
  --goal-id GOAL --agent-id COORDINATOR --turn-instance-id TURN \
  --operation-id OPERATION --stage review --outcome accepted \
  --evidence-ref EVIDENCE_ID --validation-ref VALIDATION_ID --execute
loopx --format json --registry REGISTRY native-child read \
  --goal-id GOAL --agent-id COORDINATOR --turn-instance-id TURN
```

Cancellation / 取消后登记不采用：

```sh
loopx --format json --registry REGISTRY native-child record \
  --goal-id GOAL --agent-id COORDINATOR --turn-instance-id TURN \
  --operation-id OPERATION --stage result --outcome cancelled \
  --reason-code bounded_result_unavailable --execute
loopx --format json --registry REGISTRY native-child record \
  --goal-id GOAL --agent-id COORDINATOR --turn-instance-id TURN \
  --operation-id OPERATION --stage review --outcome rejected --execute
```

Optional diagnostics persist as `result_reason_code` / `review_reason_code` in
the shared activity read model. Existing receipts and reason codes remain
valid. Exact replay must retain the originally supplied diagnostic; adding or
changing it later is a conflicting payload, not a silent amendment. A cancelled
or failed result cannot be accepted or rewritten as completed.

可选诊断通过共享活动模型的 `result_reason_code` / `review_reason_code` 读回。
旧回执与原因码继续有效。幂等重放须保留原诊断；事后增加或改变原因属于内容
冲突，不会静默修改历史。取消或失败的结果不能被采用或改写为完成。

The source of truth for a bound LoopX delegation remains its delegation
operation receipt. A native child report never substitutes for that receipt or
for the parent validation of the underlying work.

已绑定的 LoopX delegation 仍以自身操作回执为权威。原生子代理上报不能代替
delegation 回执，也不能代替主 Agent 对工作结果的实际核验。

## Codex host qualification / Codex 宿主验证

The adapter belongs to the built-in Codex Turn host and the existing
`multi_subagent` receipt owner. It installs no scheduler and makes no additional
provider request. Feature-off Turns create no observer and retain their host
request/result contract. Ordinary CLI and Lark status use the same shared
projection as the dashboard; Lark has no native child configuration to change.

A resumed CLI invocation can receive only a new `wait` completion. The adapter
resolves its hashed child reference against the latest started host-observed
spawn or followup in the
same admitted Goal instance, coordinator and LoopX Turn, including receipts
outside the bounded status window. A spawn/followup item's terminal snapshot
belongs to its own stable decision and receivers; replaying an old spawn never
completes a later followup. A terminal wait first resolves the immutable binding
of its session, invocation, native item and child identity. Only its first
observation uses the latest child association. Each distinct wait retains a
hashed scalar reference on the existing result event; exact replay preserves
that original operation across restart, and changed outcomes or reassignment
are rejected. Multiple waits observing one result do not add operations,
launches, parent acceptance or quota. No raw host identifiers or content enter
the public activity projection.
It does not adopt coordinator reports or scan external host history. Spawn IDs
retain their existing child binding.
CLI followup and failed-call IDs use the Turn journal's durable `host_attempt`
plus the owned parent session and native item ID; app-server calls use their
native Turn ID. A real retry advances the journal attempt before launch;
replaying the same binding and item remains idempotent. Direct enabled CLI
adapter calls require that attempt; feature-off calls retain their original
request. Only compact hashed child references are retained for correlation;
they do not enter public activity rows or replace independent parent review.
No prompt or raw result is added to a receipt.

恢复 CLI 时可能只收到新的 `wait` 完成事件。适配器从同一已准入 Goal 实例、
主 Agent、LoopX Turn 的最近一次已启动宿主 spawn 或 followup 回执恢复关联，
覆盖状态窗口外的操作。spawn/followup 的完成快照只归属自身稳定决策和接收者；
重放旧启动事件不会完成后来的跟进任务。wait 首先恢复其会话、调用、原生项和
接收子 Agent 身份对应的首次结果关联；只有首次观察才使用最近操作。哈希关联
作为标量留在现有结果事件中，重启重放仍归原操作，结果冲突或重新归属会被拒绝。
多个 wait 观察同一结果不会增加操作、启动、主 Agent 验收或配额；公开活动投影
不暴露原始宿主标识或内容。
不采纳主 Agent 上报或扫描外部宿主历史。启动保留既有子代理绑定；CLI 跟进和
失败调用复用 Turn 日志持久化的 `host_attempt`、父会话和原生工具 ID，
app-server 使用其原生 Turn ID。真实重试在启动前递增尝试次数；同一绑定与
事件的重放仍幂等。直接调用已启用的 CLI 适配器须提供该尝试次数，关闭能力
时请求不变。关联只保留紧凑的子代理哈希引用，不进入公开活动行，
也不替代独立父任务验收。回执不增加原始提示或结果内容。

A live isolated Codex 0.142.5 test observed one successful spawn, a second failed
spawn at `agents.max_threads=1`, child completion and independent parent
acceptance. Durable readback preserved one launch and one accepted result. The
native failure subtype remains unqualified: Codex emitted only `failed`, not
`agent_thread_limit_reached`. Synthetic typed capacity-report tests cover the
existing no-same-Turn retry rule without pretending that this host supplies that
error code. No live production Goal or raw child output is part of this evidence.

适配器复用内置 Codex Turn 宿主与 `multi_subagent` 回执所有者，不新增调度器
或模型调用。关闭能力时不创建观察器。CLI、Lark 状态和仪表板共用同一投影。
隔离的真实 Codex 0.142.5 验证观察到了一个成功启动、上限为 1 时第二次启动
失败、首个子任务完成以及独立的父任务验收。持久回读保留一个启动和一个
采纳结果。失败子类型仍有宿主协议缺口，不能声称已核验容量错误码。
