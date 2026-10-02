# 从 Codex App 启动

Codex App 的职责是提供可见交互、Agent Turn 和 heartbeat automation。LoopX 的职责是从项目状态
决定每次 heartbeat 是否应该工作、做什么，以及何时退避或停止。

## 成功标准

完成后，你应该能观察到：

- Codex App 在正确的项目根目录工作；
- `$loopx <task>` 或 `LoopX` command skill 识别了明确目标；
- Agent 复用精确 Goal 或在规划后建立新 Goal，并为新接入选择 fresh identity；
- heartbeat automation 使用 LoopX 生成的 thin task body；
- `quota should-run` 成为每一轮的执行 Gate；
- App 报告 active state id、当前 user Gate、top Todo 和 next safe action。

## 1. 从项目根目录打开 App

确认 App 当前 workspace 是要接入的 Git 根目录。Host 不应扫描无关 home 目录来猜项目，也不应把
另一个 worktree 的 registry 当作当前 delivery workspace。

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
版本中都可用。`loopx slash-commands` 会打印当前 canonical 入口。

## 2. 让 Agent 规划后再写 Todo

明确任务的正常流程是：

1. 保留用户的 task text；
2. 读取或连接项目状态，并通过 selection gate 选择精确 `goal_id`；
3. 为新接入注册 fresh `agent_id`，或按用户明确指令 takeover 已有 identity；
4. 先形成有序 P0/P1/P2 计划；
5. 按计划顺序写入 Todo；
6. refresh state；
7. 激活 App heartbeat；
8. 运行 agent-scoped `quota should-run`；
9. 只在 contract 允许时交付一个有界 segment。

你不需要手工执行所有内部命令，但应该能从 Agent 报告中看到这些状态转换。仅得到一段自然语言
计划，不等于项目状态已经建立；仅看到 guided packet，也不等于 heartbeat 已经安装。

## 3. 理解 heartbeat

Codex App heartbeat 是 Host scheduler，不是第二控制面。每次触发时，thin task body 应要求：

- 读取当前 LoopX Goal；
- 运行 `quota should-run`；
- 尊重 user Gate、capability gate 和 write scope；
- 推进一个 bounded segment；
- 验证后 refresh state；
- 仅在进展写回后 spend；
- 根据 `scheduler_hint` 调整 cadence 或停止。

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

不要把完整项目历史复制进 automation prompt。稳定 prompt 负责协议，动态 Todo、Gate 和能力通过
当前 CLI decision packet 注入。

## 4. 检查是否真的接通

在 Agent 完成 setup 后，用 CLI 交叉检查：

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

如果 `scheduler_hint.app_automation.stateful_backoff.apply_needed=true`，还要确认 App 实际应用了
`recommended_rrule`，随后执行 packet 提供的完整 `ack_hint.cli_args`。仅看到 recommendation 或
本地 ACK ledger 都不足以证明 cadence 已生效；实际 Host RRULE readback 若报告 drift，必须按当前
hint 修复。

## 5. 在 App 与 CLI 之间切换

切换 Host 不需要迁移 Goal。新的 Host 应读取同一个项目 registry 和 active state：

```text
Codex App --------┐
                  ├── .loopx registry + active goal state
Codex CLI Goal ---┘
```

不要让 App 和 CLI 各自创建同名但独立的 Goal。切换前先确认没有两个 Agent 同时 claim 同一 Todo；
有 hard lease 的工作必须等 lease 释放或按生命周期显式移交。

切换 Host 也不必更换 Agent identity：如果这是同一 peer 的连续执行，应显式携带原 `agent_id`；
如果是新的 peer，则注册 fresh id 并完成 claim/handoff。Host 变化与 Agent takeover 是两个独立
决定。

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

不要声称“LoopX 已自动运行”。让 Agent 输出可复制的 thin heartbeat task body 和建议 cadence，
并明确这是 Host activation gate。只有 App 中实际存在 automation 或有等价 readback，才算激活。

### heartbeat 频繁空跑

查看 `scheduler_hint`、monitor Todo 和 spend history。无变化的外部等待应转为 monitor/backoff，
而不是每次启动完整 Agent Turn。

### Goal 被 Gate 阻塞

确认 Gate scope。只阻塞一个 Todo 的决定不应冻结其他 safe frontier。若 Gate 过宽，先修复项目
状态，而不是在 prompt 中要求 Agent 忽略它。
