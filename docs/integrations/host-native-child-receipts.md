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

`native-child record` currently accepts the coordinator's typed report. Its
`observation` is `coordinator_reported` and `host_attested` is always `false`.
LoopX cannot intercept an arbitrary external host's native tool call. A future
host adapter must observe that call at its own boundary and extend this event
and read-model contract with a separately verified provenance variant; this
v0 recorder cannot claim host attestation. Missing records remain
`unknown`; neither a missing record nor `max_children > 0` proves that a child
was created or deliberately skipped.

目前 `native-child record` 接受主 Agent 的类型化上报，因此 `observation` 为
`coordinator_reported`，`host_attested` 始终为 `false`。LoopX 无法拦截任意
外部宿主的原生工具调用。后续宿主适配器须在自己的边界观察调用，给事件与
读模型扩展单独核验的来源类型；当前 v0 上报器不能声称宿主核验。缺少回执
就是 `unknown`；没有回执或配置上限大于零，都不能证明已启动或主动跳过。

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
3. A started operation may get a typed `result`. Only a completed result may
   receive `parent review`; `accepted` requires public-safe evidence and
   validation references. Raw prompts, host errors, transcripts, local paths,
   and credentials are excluded.
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
3. 已启动操作可记录类型化 `result`；只有完成结果才能进入主 Agent `review`。
   `accepted` 必须有公开安全的证据与验证引用。原始提示、宿主错误、对话、
   本地路径和凭据不进入回执。
4. 同一身份和内容重放幂等，内容冲突会被拒绝。`read` 与带 Turn ID 的
   `agent-context` 读取同一模型。Goal 状态的 JSON 与 Markdown 只在能力启用且确有决策时投影
   最近一轮；仪表板读取该投影，未配置或无关 Goal 不显示活动行。
5. 新决策要求 Turn 已准入、仍开放且尚未开始结算。开始结算或结清后，已经登记
   的 started 操作仍可接收结果及主 Agent 验收；这是迟到事实登记，不会重开 Turn。
   不能借已关闭的 Turn 首次补建缺失决策。精确重复仍是读取，内容冲突仍拒绝。
   新写入在既有事件流锁内重新核对读回，每次报告只调用一个粗粒度 TS 边界，
   不增加第二套 Python 阶段判断。

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

The source of truth for a bound LoopX delegation remains its delegation
operation receipt. A native child report never substitutes for that receipt or
for the parent validation of the underlying work.

已绑定的 LoopX delegation 仍以自身操作回执为权威。原生子代理上报不能代替
delegation 回执，也不能代替主 Agent 对工作结果的实际核验。
