# 第 7 讲：Host、Heartbeat 与 Stateful Backoff

> **本讲结论：** Host 拥有唤醒和外部 effect，LoopX 拥有 cadence proposal 与验证规则；
> 只有绑定 proposal identity 的 host readback 才能形成 durable scheduler ACK。

建议时长：90 分钟。讲解 55 分钟、时序推演 20 分钟、实验 15 分钟。

## 学习目标

完成本讲后，开发者应该能够：

1. 区分 host trigger、heartbeat task body 和 LoopX decision kernel。
2. 解释 thin prompt 为什么比项目专属大 prompt 更可靠。
3. 读懂 scheduler hint、stateful backoff 和 bound ACK。
4. 区分 quiet skip、monitor poll、wait、run-now 和 terminal stop。
5. 解释为什么 monitor-poll 的 before/after decision 必须共享同一 execution context。
6. 设计一个不会重复 spend、不会无限刷盘的 host adapter。

## 本讲技术契约

| 边界 | LoopX 中的答案 |
| --- | --- |
| Decision owner | LoopX 从 quota decision 投影 cadence、backoff 与 apply/ACK obligation |
| Execution context | `host_surface + scheduler_owner + execution_mode` 共同限定 effect authority |
| Effect owner | Host 创建 session、触发 turn、修改 RRULE 或调用外部 runtime |
| Commit receipt | ACK 必须绑定 state key、reset token、identity signature 与实际 host readback |
| Recovery | Quiet 保持 automation；未结算 proposal 继续 apply/ACK，不伪装成 delivery |

## 三个 Showcase 的 Cadence 不同，ACK 合同相同

Scheduler 不懂 GitHub 或研究指标，但它能根据 Kernel 已分类的 action 调整唤醒节奏：

| 场景 | Kernel 已知事实 | Cadence 倾向 | 何时重置 backoff |
| --- | --- | --- | --- |
| PR 有 failing checks successor | runnable advancement | 较快继续 | successor 被领取或 material transition |
| PR checks pending 且连续无变化 | monitor wait | 逐步放慢 | checks/review fingerprint 改变 |
| Auto ML 候选正在实现或 preflight | runnable advancement | 较快继续 | validated launch proposal 写回 |
| 外部训练/评估任务仍运行 | task monitor wait | 按任务阶段逐步放慢 | task state、artifact 或 metric fingerprint 改变 |
| 资源槽已满，候选 deferred | capacity wait | 较慢观察资源 source | capacity readback 使 `resume_when` 成立 |
| 研究 lane 有 holdout todo | runnable frontier | 较快继续 | evidence packet 写回 |
| 研究暂无可执行 frontier，等待外部证据 | fresh-evidence wait | 较慢轮询 | 新 evidence event 或 user decision |

无论哪种场景，LoopX 只产生 cadence proposal；host 应用 RRULE 或下一次触发时间后，必须
回写绑定 `goal + agent + action class + proposal revision` 的 readback。只看到宿主当前值
“碰巧相同”不能结算一条新 proposal，因为那无法证明 host 应用了本次决定。

Explore Graph 的 material finding 会改变下一轮 evidence frontier，Explore Harness 的新
portfolio 可能改变 advisory ranking；二者本身都不应创建额外 heartbeat。只有它们经
Kernel 形成 runnable todo、due monitor、replan obligation 或明确 wait condition 后，
scheduler hint 才改变 cadence。这样一张展示图刷新失败不会让昂贵实验提前启动，planner
每次重新排序也不会造成高频空转。

## Host 是触发器，不是第二控制面

LoopX 支持多种 host：

| Host surface | 如何开始 | 谁持续触发 |
| --- | --- | --- |
| Codex App | `$loopx <task>` | App heartbeat automation |
| Codex CLI | 设置稳定的 thin re-entry body 与可见 goal/session | 用户或 CLI 可见循环 |
| Claude Code | `/loopx <task>`，可选 native loop adapter | Claude native loop 或用户 |
| 其他 shell/agent | `start-goal --guided` | 外部 runner 或人工触发 |

Host 负责：

- 创建或恢复 executor turn；
- 传入 LoopX 生成的稳定 re-entry body；
- 唤醒后调用 CLI，取得当前 Turn 的动态 packet；
- 声明 host capability；
- 应用 scheduler cadence；
- 返回真实 effect receipt。

Host 不负责：

- 自己解析 todo 选择 runnable；
- 自己决定 user gate 是否阻塞；
- 把 model output 当 execution receipt；
- 在本地 prompt 中复制 quota 状态机。

## Thin Heartbeat Prompt

命令：

```bash
loopx heartbeat-prompt \
  --thin \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --agent-scope "<registered host lane>"
```

实现入口是 `loopx/heartbeat_prompt.py::build_heartbeat_prompt`。Thin body 只保留生命周期不变量：

```text
CLI is source of truth
inspect registry / status / history / repo
run exact quota should-run
follow interaction_contract
apply scheduler_hint
write plans/done into LoopX state
spend only after validated writeback
safe P1/P2 may continue when permitted
monitor quiet skips do not spend
stop at private/credential/destructive/unauthorized boundaries
```

项目专属 watch 不应写进 generic heartbeat prompt。例如“每轮看 PR #123”应建模成 `continuous_monitor` todo，带 target、cadence 和 due time。

## 一次 Heartbeat Turn 的 Host 时序

```text
Codex App automation fires
  -> opens/resumes target thread
  -> executor reads thin task body
  -> executor runs quota should-run with same agent identity
  -> interaction_contract determines this turn
  -> agent performs bounded work or quiet action
  -> durable writeback and validation
  -> spend once if allowed
  -> host applies scheduler hint
  -> host records bound ACK when required
```

Agent identity 必须从 prompt、quota、todo writeback 到 spend 保持一致。不能用 agent-a 的 quota，写 agent-b 的 todo，再用无 agent id 的 spend。

## Scheduler Hint 是派生协议

Scheduler 根据已经解析的 lifecycle 状态决定下一次 cadence，不应独立做业务推理。

典型 action class：

| 状态 | 建议初始/最大 cadence | 含义 |
| --- | --- | --- |
| runnable active work | 约 3m / 10m | 尽快继续，但允许渐进退避 |
| user wait | 约 30m / 120m | 等用户，不高频打扰 |
| reassigned/handoff | 约 10m / 60m | 给新 peer 反应时间 |
| material monitor | 约 15m / 60m | 按外部证据节奏检查 |
| fresh evidence wait | 约 60m / 240m | 没有新证据前低频 |
| quiet wait | 约 30m / 120m | 保持 alive，不制造噪声 |
| terminal no-followup | stop | 完整 closure 后停止 |

具体值由当前 schema 和配置决定，上表用于理解类别，不应复制到另一个 hard-coded scheduler。

## Stateful Backoff

固定 2 分钟 heartbeat 会产生两个问题：

- 有工作时可能太慢；
- 等用户或外部证据时会反复空转、刷日志和占用模型。

Stateful backoff 保存同一等待身份的连续轮次，逐步增加间隔。当状态变化时 reset。

可以把 identity key 理解成：

```text
goal + agent + lifecycle reason + selected work/wait target
```

当 key 不变：

```text
3m -> 5m -> 10m
30m -> 60m -> 120m
```

当 todo、gate、evidence、monitor due state 或 agent lane 变化时，backoff 应重置到新类别的初始 cadence。

## 为什么需要 Scheduler ACK

模型输出“已将 automation 改成 30 分钟”不是执行证据。App 必须实际更新 RRULE，随后运行 CLI 给出的 ACK：

```bash
loopx quota scheduler-ack-current \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --surface codex_app \
  --state-key scheduler_hint.app_automation.stateful_backoff \
  --reset-token <proposal-reset-token> \
  --identity-signature <proposal-identity-signature> \
  --applied-rrule 'FREQ=MINUTELY;INTERVAL=30' \
  --execute
```

这段命令展示稳定的 public shape。持续运行的 host 应原样执行当前
`ack_hint.cli_args`，不要自行拼参数。生成的命令还带有内部
`--scheduler-host-facts-chunk` 与 `--turn-instance-id`。前者封装有版本且有大小上限的
proposal/host facts，后者绑定产生该 hint 的 `quota should-run` receipt。Unix、Windows
和 wheel console launcher 只有同时看到这两项绑定，才会把精确 ACK/failure command
直接交给 native TypeScript transaction。手工输入且没有 receipt binding 的命令继续走
Python compatibility path。

ACK 绑定：

- goal；
- agent；
- host surface；
- scheduler state key；
- proposal reset token 与 identity signature；
- 实际 applied RRULE。
- 生成这条 follow-up command 的 heartbeat Turn receipt。

这样下一轮能够区分：

```text
hint proposed
host update attempted
host update applied
CLI acknowledged exact applied state
```

Scheduler ACK 本身不构成 delivery，不 spend。

### 兼容投影不能改变状态权威 / Compatibility preserves state authority

`app_automation` 与 `codex_app` 是同一 proposal 的宿主投影，不是两个 scheduler
writer。读取优先采用 common state；仅有旧 Codex state 时，保留它经 TS 验证的
`state_key`。两份投影的 backoff、host facts 与 ACK/failure 命令都必须携带同一个键。
否则 common state 的当前 identity/index 会被误写到旧键，触发真实的初始档位冲突；
即使 CAS digest 相等也不代表 proposal 的状态归属正确。

`app_automation` and `codex_app` project one proposal, not two scheduler writers.
Reads prefer common state; a legacy-only Codex installation retains its
TS-validated legacy key. Both projections must carry that same key through
backoff, host facts and ACK/failure commands. Equal CAS digests do not prove that
a proposal belongs to the chosen state scope.

兼容 Python API 与手工 Codex CLI 未显式传 `state_key` 时，从当前 packet 取得实际键；
显式键仍须匹配，不得静默改写。Trae 仍只接受 common key。这不是隐式迁移：不能把
旧状态的非零档位当作空 common state 的首次 ACK，也不能放松 TS 的 reset/CAS 校验。

When Python compatibility APIs or manual Codex CLI calls omit `state_key`, they
use the current packet's key. Explicit keys must still match; Trae remains
common-key-only. This is not an implicit migration: a nonzero legacy stage must
not become a first ACK into missing common state, and TS reset/CAS checks remain
unchanged.

### Proposal、Host Effect 与 Durable Receipt

Scheduler 交互包含三个时间点，不能压成一个 `RRULE matches`：

```text
P: quota 产生 proposal identity 与 target RRULE
O: host 更新 automation，并 readback observed RRULE
R: LoopX 持久化 ACK，证明 O 对应 P
```

`O.target_matches=true` 只说明宿主当前值正确，不等于 `R.persisted=true`。如果 adapter
先更新 RRULE，再重新计算一份已经显示 settled 的 hint，原 proposal 的 ACK obligation
可能被跳过。完整资格测试必须保留 P 的 `state_key`、`reset_token`、
`identity_signature` 和 target RRULE，按以下顺序验证：

```text
quota proposal
  -> host update
  -> host readback
  -> ACK original proposal identity
  -> final quota guard observes persisted scheduler state
```

最后一步不能只断言 host 仍是目标 RRULE，还要断言控制面不再返回同一 ACK obligation，
并且 durable scheduler state 已记录对应 identity。这样才能区分“宿主碰巧已经正确”和
“这次控制面 transition 已经结算”。

## Monitor 的 Quiet Contract

`continuous_monitor` 是最容易产生空转的 lane。

### 未到期

```text
next_due_at > now
```

行为：不 poll，保持 automation active，按 due time/backoff 唤醒，不 spend。

### 到期且状态变化

做一次 bounded external evidence poll，写入新 evidence，并根据结果：

- 完成 monitor；
- 创建/unblock successor advancement todo；
- 更新 next due；
- 形成 blocker/user gate。

有 material writeback 时才 spend。

### 到期但状态未变

最多一次 poll，记录 compact no-change 或更新 due/backoff，然后 quiet。不要每次 heartbeat 重复写完全相同的失败日志。

### 连续无推进

连续两轮 no-progress 应触发 self-repair：

- cadence 是否过快？
- target key 是否稳定？
- 外部证据是否可订阅而非轮询？
- 是否缺少 successor/resume condition？
- projection 是否一直失败？

## 组合回放：Monitor、Gate、Replan 与 ACK

考虑一个外部 PR monitor。它到期后发现 review 要求改变 public contract，因此一次 poll 同时产生新证据、scoped user gate、blocked successor 和 replan trigger：

```text
monitor due
  -> one bounded poll
  -> material evidence changed
  -> user gate G1 + blocked successor T1
  -> replan obligation
  -> new interaction contract
  -> new scheduler identity and cadence
  -> host applies RRULE
  -> CLI ACKs the exact current hint
```

这条链上有四个容易混淆的边界：

1. monitor 只负责观察并写 evidence，不决定 G1 是否获批；
2. replan 负责重算 frontier，不得绕过 T1 的 decision scope；
3. scheduler hint 从新 interaction contract 派生，旧 monitor cadence 不能因为“比较省”而继续沿用；
4. host ACK 只证明某个 identity signature、reset token 和 RRULE 已应用，不证明 PR 修复已交付，因此 ACK 不 spend。

G1 未解决期间，heartbeat 仍应保持存活并按 human-gate/replan cadence 唤醒，但不得重复 poll 已结算的 M1，也不得把相同 gate 每轮重新写成 material progress。这个组合场景同时检验 todo lifecycle、quota precedence、scheduler reset 和 no-spend policy。

## Projection 失败日志去重

本地异常如果每 2 秒写一条相同日志，会带来：

- event ledger 噪声；
- status 变慢；
- 真实新错误被淹没；
- automation 被误判为“持续有进展”。

合理的错误身份应包含：

```text
projection/sink + goal + stable error family + affected revision
```

相同身份在没有状态变化时只更新 backoff 或 compact count，不反复追加等价 payload。新 source revision、error family 或修复结果出现时再记录新事件。

## Stop 的严格条件

Host 只有在 CLI 派生 `terminal_no_followup` 时才应自动停止。它至少要求以下 frontier 全部关闭：

- open agent todo；
- open user gate/action；
- due 或 active monitor；
- pending successor/handoff；
- replan obligation；
- acceptance/vision gap；
- retryable projection/sink postcondition；
- unresolved blocker/resume route。

因此：

```text
open todo count == 0
```

只是必要条件之一，不是充分条件。

`terminal_no_followup` 是 LoopX 自己派生的 runtime closure，不是用户自定义 status，也不应由 host 根据自然语言猜测。

## 原生 Codex Goal 与 LoopX Host Surface

### 原生 Goal 的持久边界

Codex 原生 Goal 不是“在 prompt 前写 `/goal`”的文本约定。自动化 surface 通过
`thread/goal/set` 把 objective、status 与可选 token budget 绑定到 thread，通过
`thread/goal/get` 读回，再用 `turn/start` 继续执行。只有普通 prompt 的 slash 前缀、
外部 polling loop 或 `codex exec` 成功，都不能单独证明 native Goal 已激活。

这层 object 解决了一个重要问题：目标和 host lifecycle 不必只存在于当前模型上下文。
LoopX 再补充项目拥有的结构化状态和过程：todo/claim/gate、领域 observation、effect receipt、
跨 host cadence、replan 与 terminal audit。两者不是竞争状态机，也不能把 LoopX packet 伪装成
native Goal baseline。

### Codex App 的 LoopX 路径

- App heartbeat automation 持有稳定的薄 task body；
- 每次唤醒时，executor 按 task body 调用 LoopX CLI，从最新 state 取得本轮 packet；
- host 可以按 `scheduler_hint` 更新 RRULE；
- scheduler hint 可要求 App update、readback 与 ACK；
- App thread/session 不成为 LoopX canonical state owner。

### Codex CLI 的 LoopX 路径

- 当前可见 session 通常由用户启动；
- LoopX 提供 bootstrap message / thin re-entry body，并可让用户设为可见 `/goal <task_body>`；
- 可见 Goal 保留 objective 与 re-entry contract；每一 Turn 的 packet 仍由 CLI 重新生成；
- 这里借用 Goal 作为持续 host surface，但长期 todo、gate、evidence 与 cadence 仍从 LoopX 读取；
- CLI 不应假设用户安装的 `/loopx` 自定义命令一定可用；
- final-check/self-stop 通过当前可用 host surface 完成。

两条 LoopX 路径共享同一个 quota/status/todo state kernel，不应复制两套 lifecycle。Native
Goal objective 可以长期稳定，CLI packet 则必须随状态变化重新生成；把旧 packet 固定在 host
里，会让 host 继续执行过期 frontier。

## Host Capability 与 Effect Receipt

有些动作需要 host 才能执行，例如：

- session message injection；
- session termination；
- state fork；
- workspace transfer；
- visible TUI pane launch。

Control plane 的正确关系是：

```text
typed proposal
  -> host checks declared capability and authority
  -> host performs effect
  -> host returns typed result
  -> LoopX records capability-matched receipt
```

缺 capability 时 proposal 保持未执行。不能把 model response 当 host receipt。

## 实验：推演 Cadence，不修改 App

### 1. 生成 thin prompt

```bash
loopx heartbeat-prompt \
  --thin \
  --goal-id <lab-goal> \
  --agent-id <lab-agent> \
  --agent-scope "training heartbeat lane"
```

找出 prompt 中真正长期稳定的 6 条规则。

### 2. 读取 scheduler hint

```bash
loopx --format json quota should-run \
  --goal-id <lab-goal> \
  --agent-id <lab-agent> \
  --available-capability shell \
  | jq '{interaction_contract, scheduler_hint, automation_liveness}'
```

不要在课堂实验中实际更新个人 App automation。只回答：

- action class 是什么？
- `apply_needed` 是否为 true？
- spend policy 是什么？
- state identity 何时 reset？

### 3. 构造 monitor 状态表

手工推演四种输入：未到期、到期有变化、到期无变化、连续无变化。为每种情况写出：

```text
poll? / write? / spend? / next cadence? / automation active?
```

## 核心代码领读：LoopX 决策怎样变成 host cadence

这一讲最容易把三件事混在一起：heartbeat prompt、scheduler hint、host automation。先画清权责：

```text
heartbeat_prompt  生成稳定的“下一轮怎样重新进入 LoopX”任务体
quota              从最新 state 生成当前轮的动态 packet 与执行义务
scheduler_hint     把义务投影成 cadence/backoff 建议
host               真正修改 RRULE、触发 session
scheduler_ack      证明 host 应用了哪个 RRULE
```

### 1. Scheduler hint 是纯 projection

`loopx/control_plane/scheduler/scheduler_hint.py::build_scheduler_hint` 的 docstring 就是边界：

```python
def build_scheduler_hint(payload, *, scheduler_execution_context=None, ...):
    """Project host-runtime cadence/backoff policy from a quota decision.

    This helper is intentionally pure: callers provide the few quota-local
    classification facts it needs, and it returns the public scheduler contract
    without reading files, mutating state, or depending on the full quota module.
    """
    execution_context = resolve_scheduler_execution_context(
        scheduler_execution_context
    )
    if not execution_context.ok:
        return {
            "action": "repair_scheduler_execution_context",
            "spend_policy": "no quota spend for scheduler context repair",
            "codex_app": {"apply": "none", "host_action": "none"},
            ...,
        }
```

纯函数意味着它不能声称“RRULE 已更新”。它只能返回 `apply_needed`、推荐 interval、reset token 和 ack plan；effect 必须由 host 执行。

Execution context 也必须由调用方显式提供。缺少 context 的通用调用会返回
`repair_scheduler_execution_context`，不会再假定自己运行在 Codex App。
Codex App heartbeat 的兼容入口是明确的
`--runtime-profile codex_app_heartbeat`（生成命令使用等价的紧凑别名
`--codex-app`）；Codex CLI、Claude Code 和外部
controller 则传各自的 `host_surface`、`scheduler_owner` 和
`execution_mode`。这样测试与真实 host 使用同一个 typed contract，而不是靠一个
库级 legacy 默认碰巧命中 App 行为。

### 2. Stateful backoff 的 identity 不只是 goal id

源码构建的 identity keys 包含：

```python
base_identity_keys = [
    "goal_id",
    "agent_identity.agent_id",
    "effective_action",
    "heartbeat_recommendation.recommended_mode",
    "interaction_contract.mode",
    "recommended_action",
]
```

只要工作身份发生 material 变化，backoff progression 就应 reset。否则“等待同一个外部结果的第 5 次 poll”和“刚切换到新 runnable todo 的第一次执行”会错误共享慢 cadence。

### 3. 原生 Follow-up 同时校验 Turn、Proposal 与 Host 回报

`scheduler/heartbeat_followup_cli.ts` 先解码有大小上限的 hint packet，再核对 command、
goal、agent、operation、state key、proposal identity 与 host readback。随后
`scheduler/heartbeat_followup.ts::evaluateSchedulerHeartbeatFollowup` 检查 receipt 是否仍是
当前 goal/agent 的最新 Turn。缺失 receipt 或已经出现更新 Turn 时，transaction 在写
scheduler state 前停止。

```typescript
if (request.require_heartbeat_receipt) {
  const status = await heartbeatReceiptStatus(
    request.runtime_root,
    String(facts.goal_id),
    String(facts.agent_id),
    String(request.turn_instance_id),
  );
  if (status !== "fresh") return receiptFailure(request, status);
}
const commit = await evaluateSchedulerHeartbeatHostFacts({
  ...facts,
  runtime_root: request.runtime_root,
});
```

这组检查拒绝把任意 host 值或过期 Turn 写成当前 proposal 的 receipt。TypeScript 在同一
进程内完成 ACK/host-failure validation、failure-cache transition、replay/CAS、atomic
write 和兼容旧合同的 JSON/Markdown projection。持续运行的路径没有 Python 到 Node 的
第二次 request/response。Python adapter 没有 scheduler 决策权，只给显式 in-process
caller 与 unbound manual command 保留兼容入口。

端到端测试仍需把 apply 前生成的 hint 原样带过 host effect。若 effect 后重新读取一份
hint，测试仍可能漏掉 ACK obligation 丢失。ACK 不构成 delivery，也不会 spend。缺少
durable ACK 时，下一轮不能把 proposal 或碰巧相同的 host 值当成已结算状态。

### 4. Monitor writeback 用 result hash 区分观察与推进

`loopx/control_plane/scheduler/monitor_poll_writeback.py` 是 no-change backoff 的具体落点：

```python
safe_result_hash = str(result_hash or "").strip()
if not safe_result_hash:
    raise ValueError("monitor todo writeback requires --result-hash")
if not material_change and not effective_next_due_at:
    raise ValueError(
        "unchanged monitor todo writeback requires --next-due-at or a parseable cadence"
    )

consecutive_no_change = (
    0
    if material_change or (previous_hash and previous_hash != safe_result_hash)
    else previous_no_change + 1
)
monitor_metadata = {
    "last_checked_at": generated_at,
    "result_hash": safe_result_hash,
    "consecutive_no_change": str(consecutive_no_change),
    "material_change": "true" if material_change else "false",
    "next_due_at": effective_next_due_at,
}

if material_change and next_agent_todo:
    add_goal_todo(..., task_class="advancement_task", ...)
if material_change and next_user_todo:
    add_goal_todo(..., task_class="user_gate", ...)
```

四个不变量：

1. 每次 poll 必须有稳定 `result_hash`；
2. unchanged poll 必须给出下次 due，不能形成热循环；
3. hash 改变或 material change 会把连续无变化计数清零；
4. 只有 material change 能创建 successor todo。

因此“看了一眼但没变化”通常是 no-spend observation；“新证据改变 frontier 并写回 successor”才可能成为 accountable delivery。

### 5. Monitor-poll 的前后决策必须共享 Execution Context

一次 monitor poll 会先计算 `before`，写回 observation，再用新状态计算 `after`。两次
decision 必须使用同一个 typed scheduler context：

```python
before = build_quota_should_run(
    status_payload,
    goal_id=goal_id,
    agent_id=agent_id,
    scheduler_execution_context=scheduler_execution_context,
)
return record_quota_monitor_poll_for_decision(
    before,
    status_payload,
    after_decision=lambda after_status: build_quota_should_run(
        after_status,
        goal_id=goal_id,
        agent_id=agent_id,
        scheduler_execution_context=scheduler_execution_context,
    ),
    ...,
)
```

Execution context 不是渲染选项，而是 decision input。若 CLI 入口识别出 Codex App，
但 wrapper 只把 context 传给 `before`，`after` 会误以为自己处在未知 host，返回
`repair_scheduler_execution_context`。这不是 monitor 产生了新阻塞，而是调用链丢失
了 authority context。

对应回归应同时证明：

1. App context 在 before/after 都保持 App scheduler contract；
2. generic CLI context 在 before/after 都保持 external-controller contract；
3. 非法或缺失 context 两边一致 fail closed，不能由 wrapper 静默补默认值。

### 6. 一次真实 host 循环

```text
App tick
  -> heartbeat prompt 要求先 quota should-run
  -> quota 返回 interaction_contract + scheduler_hint
  -> 若 apply_needed: host 更新 RRULE
  -> host 原样执行 hint 中 receipt-bound scheduler-ack-current 写回实际 RRULE
  -> 若 agent.must_attempt: 执行 bounded work
  -> refresh-state 写回 outcome
  -> validation 通过后 spend 一次
```

在课堂上故意制造一个 unchanged monitor，确认链路在 monitor writeback 后结束，没有虚假的 refresh/spend。

### 断点建议

- `scheduler_hint.py:457`：观察非法 execution context 如何 fail closed；
- `scheduler/heartbeat_followup_cli.ts`：依次改错 facts packet、Turn、state key、reset token、identity signature；
- `scheduler/heartbeat_followup.ts`：观察 missing/stale receipt 如何在 state mutation 前停止；
- `write_monitor_poll_todo_state:98`：比较 hash 相同、hash 变化、显式 material change；
- `record_quota_monitor_poll`：确认 before/after 收到同一个 scheduler context；
- host adapter：确认只有 host 层真正调用 automation update。

### 读完这一段应能回答

1. 为什么 scheduler hint 不能证明 RRULE 已应用？
2. 为什么 ACK 不消耗 delivery slot？
3. 哪些字段变化应 reset stateful backoff？
4. unchanged monitor 为什么必须写 `next_due_at`？
5. 为什么 monitor writeback 不能重新猜测 host surface？
6. automation 为什么在 quiet wait 时仍可能保持 active？

## 代码阅读路线

1. `docs/heartbeat-automation-prompt.md`
2. `docs/operations/long-task-cadence-policy.md`
3. `loopx/heartbeat_prompt.py`
4. `loopx/control_plane/scheduler/`
5. `loopx/control_plane/quota/should_run_packet.py` 的 final composition：interaction contract、scheduler hint、protocol action packet
6. `docs/product/core-control-plane/state-machine.md` 的 Scheduler/Heartbeat 状态机

## 代表性 Smoke

- `examples/control_plane/heartbeat-quota-flow-smoke.py`
- `examples/control_plane/quota-scheduler-state-ack-smoke.py`
- `examples/control_plane/interaction-scheduler-authority-smoke.py`
- `examples/control_plane/monitor-scheduler-contract-smoke.py`
- `examples/control_plane/monitor-poll-writeback-smoke.py`
- `examples/control_plane/quota-terminal-no-followup-smoke.py`

## 常见实现错误

### Automation prompt 携带项目专属 watch

把 watch 写成 monitor todo，让 heartbeat 保持通用。

### 更新 RRULE 后不 ACK

下一轮无法证明 host 实际应用了哪个 cadence。

### Quiet no-op 也 spend

会让配额账和真实推进失去关系。

### `should_run=false` 就停 automation

等待用户、未到期 monitor 和 evidence backoff 都需要继续存活。

### 把 launcher 称为 supervisor agent

Auto Research 的 tmux launcher 只是 host launcher，不是拥有 todo 决策权的 leader agent。可选 peer supervisor 是另一套 proposal-only overlay。

## 课后检查

1. Thin prompt 为什么不能替代 quota decision？
2. Stateful backoff 的 reset key 应包含哪些信息？
3. 为什么 scheduler ACK 不 spend？
4. Monitor no-change 时怎样避免刷盘？
5. Host 自动停止前必须检查哪些 closure 面？

下一讲关注一个长期系统最难的部分：如何证明真正推进过，以及状态漂移后如何自修复。
