# 从 Codex App 启动

周一下午，你给 Codex App 装好了 heartbeat automation。RRULE 写着每 20 分钟一次。周二早上你打开
App，想看看那个"重写发布流程"的目标推进到哪了。

日志显示：automation 从昨天下午起触发了 47 次，每次都正常退出。Todo 一条没动，evidence 一条没
多，重写流程还停在周一你写下的第一步。系统没有报错，它只是每次唤醒后都读到一个等待条件，然后
安静地回去睡觉。你昨天整晚都以为它在工作。

这段 47 次里没有一次是错误行为。monitor 确实还没到期，外部状态确实还没变，`should_run=false`
确实是当时的正确答案。问题出在**你把"被唤醒"当成了"在工作"**，而这两个状态在任何日志里看起来
都很像。

## 为什么"那就改成手动调用"接不住

自然的反应是关掉 automation，改成需要的时候手动跑一次。这样确实不会空转，但它换来了更贵的问题：
长程任务的整个前提是你不需要一直守着。如果你必须记得在周二早上手动调用，那这个目标就只能在你
醒着并且想起来的时候前进。

另一条直觉是让 heartbeat 每次都做点事，"既然醒了就推进一点"。这条更危险，它会把一个等待条件
变成一次真实的 spend：外部状态没变的情况下动手，产出的只是需要事后清理的副作用，进展无从谈起。

所以这里有一个必须同时满足的矛盾：**要有定时唤醒去跨越你不看屏幕的时间，又要让每次唤醒都能证明
自己没白花**。这两条合起来，要求唤醒的动作和"该不该工作"的判断必须分开放在两个地方。

## 边界：Host 拥有唤醒，LoopX decision 拥有下一步

分工是这样的：

```text
Codex App  —— 创建 session、按 RRULE 触发、应用 cadence、返回真实 readback
LoopX      —— 决定这次唤醒该不该工作、做哪一个 Todo、何时退避或停止
```

App 是 Host scheduler。它决定**什么时候**有人看一眼，不决定**这一眼该不该动手**。第二件事只有
当前 decision packet 能回答，因为只有它知道 Gate、claim、lease 和 monitor 到期时间。

这条边界有一个直接推论：**App 里没有任何一份可以拿来判断下一步的本地状态副本**。automation
prompt 保持 thin，只写协议；动态的 Todo、Gate 和能力由每次唤醒时读到的 packet 注入。

```text
Codex App automation fires
          |
          v
LoopX quota should-run
  | run       | wait / gate / stop
  v           v
Agent Turn    no delivery
  |
validate -> writeback -> optional spend
```

右分支是合法结局，而且是经常发生的那一种。它只说明**这一次**没有该做的工作，不等于 Goal
frontier 已经收尾：已结算 Turn 的回放会返回 `should_run=false`，同时保留当前 cadence，等新的
Turn identity 再评估。所以频繁走到右分支并不说明 cadence 该放慢；只有当前 `scheduler_hint`
提出调整（`apply_needed=true`）时，才按下面的四步执行。

## 从正确的项目根目录开始

App 的 workspace 应该是你要接入的那个 Git 根目录。Host 不应扫描无关的 home 目录去猜项目，也不应
把另一个 worktree 的 registry 当成当前 delivery workspace。

先在 App 中让 Agent 执行只读检查：

```text
检查当前项目的 LoopX 连接状态。先运行 loopx doctor、loopx registry 和
loopx status。复用已有 active state，不要覆盖现有目标。确认 .loopx/、
.loopx/goals/ 和 .local/ 已被 Git 忽略。
```

如果 LoopX command facade 已安装，可以在 Codex surface 中选择 `LoopX` skill，或使用：

```text
$loopx 检查并完善这个项目的发布流程，要求每一步都有可验证证据
```

Codex 当前通过 command-facade skill 暴露 LoopX；不要假设用户自定义的原生顶层 `/loopx` 在所有
版本中都可用。`loopx slash-commands` 会打印当前版本的 canonical 入口。

## 先规划，再写 Todo

明确任务的正常顺序是：

1. 保留用户的 task text；
2. 读取或连接项目状态，并通过 selection gate 选择精确 `goal_id`；
3. 为新接入注册 fresh `agent_id`，或按用户明确指令 takeover 已有 identity；
4. 先形成有序 P0/P1/P2 计划；
5. 按计划顺序写入 Todo；
6. refresh state；
7. 激活 App heartbeat；
8. 运行 agent-scoped `quota should-run`；
9. 只在 contract 允许时交付一个有界 segment。

第 5 步和第 7 步的顺序容易搞反。先激活 heartbeat 意味着你在 Todo 成型之前就开始被定时唤醒，每次
唤醒都会拿到一个还没有 frontier 的 Goal。先写 Todo 再激活，第一轮唤醒就有确定的候选。

你不需要手工执行所有内部命令，但应该能从 Agent 报告中看到这些状态转换。得到一段自然语言计划，
不等于项目状态已经建立；看到 guided packet，也不等于 heartbeat 已经安装。这两件事都要单独 readback。

## 每一轮仍然要过 Quota Gate

heartbeat 一旦装上，每轮唤醒的 thin task body 应要求：

- 读取当前 LoopX Goal；
- 运行 `quota should-run`；
- 尊重 user Gate、capability gate 和 write scope；
- 推进一个 bounded segment；
- 验证后 refresh state；
- 仅在进展写回后 spend；
- 根据 `scheduler_hint` 调整 cadence 或停止。

从另一个 shell 交叉检查：

```bash
loopx status --goal-id <goal-id>
loopx quota should-run \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --codex-app
loopx history --goal-id <goal-id> --limit 10
```

重点观察：

- `normal_delivery_allowed` 是否为 true；
- 当前 selected Todo 是否符合优先级；
- 是否有 `requires_user_action`；
- `scheduler_hint` 是否适用于 `codex_app`；
- 最新 run 是否包含验证和 writeback，而不只是 status poll。

最后一条最能区分"真的在工作"和"在醒来"。一次只读 poll 会留下历史记录，但它没有推进任何 frontier。

## ACK 收敛链：proposal、apply、readback、ACK

回到开头那个 47 次空转的场景。它的另一半是 cadence：当决策认为该放慢时，App 会不会真的放慢?

`apply_needed=true` 时，packet 会给出 `recommended_rrule`。这时有四步，缺一不可：

```text
proposal  ->  host apply  ->  host readback  ->  ACK
```

先把 RRULE 应用到 App，再读回**实际生效的值**，最后执行 packet 提供的完整
`ack_hint.cli_args`。当前这条路径通常是：

```bash
loopx quota scheduler-ack-current <packet-bound-args...>
```

这里最容易漏掉的是 readback。应用了 `recommended_rrule` 就当作完成，是一种常见做法：本地 ACK
ledger 里有了记录，cadence 看起来生效了。但 ledger 记下的是**提议**，Host 上真正生效的**实际值**要单独读回。两者
不一致时，packet 会在 `scheduler_hint.app_automation.stateful_backoff.host_observation.status`
上报 `drift_detected`，这时真实 cadence 和 ledger 已经分叉，必须按当前 hint 修复，而不是信任那份
看起来完整的记录。

反向的组合也存在。当 `apply_needed=false, ack_needed=true` 时，精确 Host readback 已经匹配目标
cadence，应当跳过这次 no-op update 并直接执行绑定 ACK。把它当成"没事可做"会留下一条未结算的
proposal，下一次唤醒会重新提出同样的要求。

apply 失败或超时则不要 ACK，执行一次 `failure_hint.cli_args`，让 proposal 停在可辨识的位置。
cadence 变化本身不记 delivery spend，所以这条链路失败不会污染配额账，但它会让系统按错误的节奏
继续唤醒。

## 代价与边界

**代价一：唤醒是定时开销，不随工作量变化。** 每 20 分钟醒一次，意味着一周 504 次唤醒，其中大部分
会走到 `wait` 分支。每次都要创建 session、读状态、编译 decision。这个成本与"这一周实际推进了几个
步骤"无关，任务变慢它也不会变小。所以 cadence 需要收敛，但收敛由 LoopX 的 `scheduler_hint` 与
stateful backoff 提出，再经四步落到 Host；不要因为 `wait` 次数多就手工改 RRULE，手工改动会在
readback 中表现为 `drift_detected`。

**代价二：收敛链条长。** 从 proposal 到最终 ACK 有四步，任何一步缺失都会在账面上看起来像是完成了。
这是一条需要 readback 才能闭合的链路，不像本地写一个变量那样即时。

**代价三：空转与工作在图上看不出区别。** 两者都留下触发记录、都以成功退出。要区分它们，必须看
writeback 和 spend 历史，而不是看 heartbeat 日志。

**边界一：heartbeat 不创造 frontier。** 一个没有 Todo 的 Goal 不会因为被频繁唤醒而出现下一步。
App 能保证有人看，不能保证有东西可做；没有可运行候选时就该进入 wait 或 monitor，让 cadence 放慢。

**边界二：automation 不是第二控制面。** 不要在 automation prompt 里复制 quota 状态机，也不要让
它自己解析 Todo 是否 runnable。稳定 prompt 只负责协议，判断属于每次读到的 packet。

**边界三：切换 Host 要核对工作归属。** App 和 CLI 可以读同一 Goal；切换前应查清旧实例是否仍执行、claim/lease 由谁持有，以及当前 writer 的围栏模式。需要显式移交时走对应 lifecycle，不能仅凭新 Host 已启动就并发推进同一项工作。

## 何时选 App，何时选 CLI

两种 Host 的区别主要在运行与唤醒边界。活跃 Codex CLI native Goal 可以在可见 TUI 内继续允许的工作，不要求用户逐轮发消息。App heartbeat 额外提供经过配置和验证的定时唤醒路径。

| 需求 | 可选路径 | 核对重点 |
| --- | --- | --- |
| 等待外部条件，按 cadence 再检查 | App heartbeat | automation 存在，cadence 与 ACK/readback 一致 |
| 当前会话内持续交付，同时看中间结果 | CLI 可见 native Goal | Host 存活、Goal active、当前准入允许 |
| native Goal blocked 后继续 | Host 的显式 resume 路径 | 阻塞解除后重查 quota |
| 承载执行的进程退出后继续 | 恢复原 Goal，或已验证的外部调度 | 不从旧 Goal body 推断后台服务存在 |

选择时分别核对 continuation 和定时唤醒能力。无论用哪种 Host，新的工作都要经过当前 LoopX decision。

## 恢复路径

### 找不到 `$loopx`

在 shell 中运行：

```bash
loopx slash-commands
loopx slash-commands --install
```

重启或刷新 Host 的 skill discovery。如果仍不可用，使用 CLI fallback：

```bash
loopx start-goal --guided --project . \
  --goal-text "<task>" \
  --host-surface codex-app
```

### heartbeat 未建立

不要声称"LoopX 已自动运行"。让 Agent 输出可复制的 thin heartbeat task body 和建议 cadence，并明确
这是 Host activation gate。只有 App 中实际存在 automation，或有等价 readback，才算激活。

### heartbeat 频繁空跑

查看 `scheduler_hint`、monitor Todo 和 spend history。无变化的外部等待应转为 monitor/backoff，
而不是每次启动完整 Agent Turn。若 RRULE 已放慢但触发频率没变，检查 `host_observation.status`
是否报告 `drift_detected`，并按当前 hint 重新 apply 与 ACK。

### Goal 被 Gate 阻塞

确认 Gate scope。只阻塞一个 Todo 的决定不应冻结其他 safe frontier。若 Gate 过宽，先修复项目状态，
不要在 prompt 中要求 Agent 忽略它。Gate 长期未处理时，`human_gate` cadence 会退避，并只提示一次具体
Gate 而不重复同样的安静轮询；若 Host cadence 更新失败而留下更密的轮询，notice cooldown 还会限制重复
提醒，避免把一个等待变成高频通知。

## 不变式

1. **heartbeat 触发不等于工作发生。** 判断真实性看 writeback 与 spend，不看触发次数。
2. **每次唤醒都过一次 `quota should-run`。** 没有这层 Gate，automation 就变成绕过决策的旁路。
3. **`should_run=false` 是合法结果，但它只关闭这一个 Turn。** 已结算回放保留当前 cadence，用新的
Turn identity 重新评估；`false` 的次数或本 Turn 的 receipt 都不能当作放慢 cadence 的依据。需要
放慢时，由当前 `scheduler_hint` 提出（`apply_needed=true`），再走第 4 条的四步。
4. **cadence 变化的四步缺一不可**：proposal、host apply、host readback、ACK。
5. **本地 ACK ledger 不证明 Host 状态。** 只有 Host readback 与目标一致才闭合，不一致时按
   `drift_detected` 修复。
6. **切换 Host 需要明确接管。** 同一 Goal 可被多处读取，写入仍须满足当前 authority、claim/lease 与围栏要求。

这六条指向同一件事：定时唤醒买到的是**持续尝试的能力**，不是**持续进展的事实**。区分这两者，
就是本章要求四的全部内容。
