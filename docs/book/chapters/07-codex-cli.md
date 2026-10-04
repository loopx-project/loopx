# 从 Codex CLI 可见 TUI 启动

可见 TUI 与持续推进可以同时存在。LoopX 为 Codex native Goal 生成稳定 task body，由当前 decision 指定工作、结算和等待方式。理解这条路径，需要区分正在运行、被阻塞和进程已退出三个状态。

## 为什么 active Goal 与后台唤醒不同

假设修复还未完成，你结束了当前 Codex 进程。再次打开终端时，工作没有继续。这说明执行环境已经离开，不代表 native Goal 在进程存活时每轮都需要用户发消息。

| Host 状态 | 当前路径的行为 | 用户需要判断什么 |
| --- | --- | --- |
| TUI 与 native Goal 正在运行 | 按 task body 读 quota、执行允许的工作，结算后重新判断并继续 | 当前 Todo、Gate 与 authority 是否允许下一步 |
| native Goal 已 blocked | 按 Host 的 blocked/resume 合同停止自动推进 | 阻塞是否解除，是否需要显式 `/goal resume` |
| 承载执行的进程已退出 | 不能仅靠之前设置 `/goal` 就产生新的定时唤醒 | 重启并恢复原 Goal，或选择已验证的外部调度路径 |

因此，活跃 Goal 的 continuation 和周期性启动新工作是两个能力。App heartbeat 适合需要定时唤醒的场景；当前 CLI 可见路径不默认创建 App automation 或隐藏 worker。

## 边界：Host 持续执行，LoopX 持续重新判断

稳定 body 不复制动态 Todo、Gate 或 monitor 状态。每轮读取完整 current decision，按 `interaction_contract` 工作与结算；之后重查 quota，继续允许的工作，或遵循当前等待/阻塞指引。

这个分工让 native Goal 保持可见、可中断，也让持久控制信息留在 LoopX。用户不必逐轮发送新任务；但 native Goal 可继续，并不意味着旧 selected Todo 或授权仍然有效。

## 启动可见 TUI

```bash
cd /path/to/your-project
codex
```

在 TUI 中发送：

```text
连接当前项目到 LoopX。先运行 loopx doctor，复用已有 active state，
确认 .loopx/、.loopx/goals/ 和 .local/ 已被 Git 忽略。不要使用隐藏的
headless execution。连接完成后，生成 thin heartbeat task body，并把当前
Codex CLI task 设置为可见的 /goal <task_body>。最后报告 active state id、
当前 user gate、top agent todo 和 next safe action。
```

setup Turn 的任务是建立连接和可见 continuation，不应顺手开始一大段未经规划的交付。如果你的第一轮
就产出了大段改动，说明 setup 和 delivery 被混在了一起，后续的每一步都会站在一个没人审过的计划上。

## 用 `$loopx` 开始具体目标

安装 command facade 后，可以在 TUI 中使用：

```text
$loopx 为这个 CLI 增加兼容的 JSON 输出，补充测试并等待维护者确认 schema
```

Host 应保留 task text，规划 Todo，并生成适合 Codex CLI 的 Goal body。若 command skill 不可用，
CLI fallback 是：

```bash
loopx start-goal --guided --project . \
  --goal-text "为这个 CLI 增加兼容的 JSON 输出，补充测试并等待维护者确认 schema" \
  --host-surface codex-cli-tui
```

输出是 guided packet，不会替你在另一个终端偷偷启动 Agent。它应该包含或指向可粘贴的
`/goal <task_body>`。这一条值得单独确认：如果 guided start 在后台把 Agent 跑起来了，你就失去了这条
路径唯一的优势，而且很难发现，因为表面上看一切正常。

## Native Goal 与 LoopX 的组合

Codex CLI native Goal 拥有同一 TUI 内的 continuation。LoopX 拥有项目级 frontier：

```text
Visible Codex /goal
  -> run LoopX quota decision
  -> execute selected bounded Todo
  -> validate
  -> write LoopX state
  -> continue, wait, block, or complete
```

当 LoopX 返回 Gate 时，Goal 可以进入 blocked 状态；当用户处理 Gate 后，再通过 Host 的 Goal 恢复
表面继续。不要通过创建第二个 Goal 绕过原 Gate：第二个 Goal 会拿到同一个 frontier，两个 Goal 于是
争抢同一批 Todo。

## 每一轮仍然要过 Quota Gate

从另一个 shell 读取状态不会改变 TUI：

```bash
loopx status --goal-id <goal-id>
loopx history --goal-id <goal-id> --limit 10
loopx quota should-run \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --runtime-profile codex_cli
```

你应该看到 Host runtime 指向 `codex_cli`，scheduler owner 属于 Goal/agent loop，而不是 Codex App
heartbeat。这个区别决定了"该唤醒时谁负责唤醒"：如果 packet 报告 scheduler context 缺失，先修复
runtime profile，不要忽略 warning。scheduler context 缺失或矛盾时，`scheduler_hint` 返回
`repair_scheduler_execution_context`，并把 unchanged poll 设为 `stop_until_context_repaired`：在修复
runtime profile 之前，它不会提出新的 cadence，也不会替你安排下一次唤醒。

## 保持身份与 Todo 归属

新的 argument-bearing guided start 在 Goal 已有已注册身份（哪怕只有一个）时不会默认注册 fresh
Agent：当前 host thread 尚未绑定时，它返回 identity gate 要求选择其中一个 lane；thread 已绑定某个
lane 时，沿用该绑定。只有 Goal 没有任何已注册 lane 或显式 `--new-peer` 时才默认 fresh。已有 id
只在用户明确要求 takeover 那个 peer 时复用。

完成选择后，visible Goal、quota、refresh 与 writeback 都应显式传入同一个 `--agent-id`。不要依赖
回退：不带任务文本的 host-loop activation 在 Goal 只有一个已注册 lane 时会自动选中它
（`single_registered_agent_selected`）。"用它就行"看起来无害，但如果那个身份属于另一个 Host 或另一个
lane，工作归属就被静默改写了。显式传入后，未注册的 id 会被拒绝；与 thread 绑定不同的已注册 id
会被当作明确的覆盖选择，所以只在确实要换 lane 时这样做。

Agent identity 表达 LoopX 工作 lane，不证明具体 Host。判断工作是否真的在 Codex CLI 运行，要看
`host_surface`、runtime profile 或对应 run metadata。

交接时的正确顺序是：

1. 当前 Agent 写回验证结果；
2. 更新或完成 Todo；
3. 新 Agent 以 fresh id 预览并完成原子注册；
4. 新 Agent claim 未完成 Todo；
5. 新 Host 读取同一 registry 与 Goal；
6. 再启动 visible Goal。

## 代价与边界

**持续性依赖 Host 仍具备执行条件。** 活跃 native Goal 可以自主推进，退出进程或进入 blocked 后的行为另有边界。需要跨这些边界自动唤醒时，应选择相应的调度集成并核对其 readback。

**可见性不替代验证。** TUI 能展示活动，成功还需要 validation、writeback 与 settlement。是否有人盯着屏幕，不会改变这些条件。

**接管需要重新核对 authority。** App 与 CLI 可以读取同一 Goal；变更执行者时要检查当前 claim、lease、worktree 和 writer fence 的适用模式，避免并发提交同一项有副作用的工作。

## 何时选 CLI，何时选 App

| 需求 | 可选路径 | 需要核对的条件 |
| --- | --- | --- |
| 当前执行会话内持续推进，同时观察或介入 | CLI 可见 native Goal | Goal active，Host 存活，当前准入允许 |
| 长时间等待后按 cadence 再检查 | App heartbeat 或已验证的 scheduler 集成 | 实际 automation、cadence 与 ACK/readback |
| 进程退出后继续 | 恢复原 Goal，或使用支持该边界的调度路径 | 不能将旧 prompt 当作新执行环境 |

依据实际需要选择唤醒方式，不将“可见”误解为“每轮手动触发”。两条路径都必须读取当前 LoopX decision。

## 恢复路径

### TUI 关闭

重新从同一项目根目录启动 `codex`，读取 `loopx status`，再恢复原 Goal。不要重新 bootstrap
一个相同 objective，那会给你两个指向同一目标的 Goal。

### `/goal` body 过期

稳定 body 不复制动态 Todo，但协议或 CLI 版本可能变化。重新生成当前 thin task body，并让 Host
替换 visible Goal；不要手改内部字段来"兼容"旧 prompt。

### 误用了隐藏 worker

停止该 worker，检查它是否写回了新 evidence 或 lease。先恢复 Todo ownership，再回到 visible
TUI；不要让两个执行者并发修改同一工作树。检查这件事时要看 claim 与 lease 的实际归属，而不是看你
记忆中谁在跑。

### Goal 无变化轮询

达到 unchanged limit 后，Goal 应阻塞或安静等待。外部状态观察应转成 monitor Todo；用户通过
Host 的 Goal resume 表面恢复，而不是反复重发完整任务。反复重发会让同一轮工作看起来像多轮进展。

### App 与 CLI 同时激活

检查旧实例活动、claim、lease、scheduler ownership 与当前 writer 的围栏模式。两种 Host 可以读同一 Goal，接管写入仍需满足对应 lifecycle 条件。

## 不变式

1. **区分 active、blocked 与进程退出。** 活跃 native Goal 可以继续；blocked 后按 Host resume 合同处理，退出后不能假定存在定时唤醒。
2. **setup Turn 只建立连接。** 连接和交付混在一轮里，后续每一步都建立在一个未审的计划上。
3. **`/goal` body 保持稳定。** 动态 Todo、Gate 和能力来自当次 decision packet，不来自 prompt。
4. **visible Goal 与 selected Todo 是两件事。** Goal 能继续，不代表这个 Todo 就该做。
5. **identity 一律显式传入。** 唯一已注册 lane 可能被自动选中；显式 `--agent-id` 让未注册的身份被拒绝，也让换 lane 成为看得见的选择。
6. **两个 Host 并存需要明确执行归属。** 复核当前 mode 下的 claim、lease 与围栏，不能凭同一 Goal 可读就并发写同一项工作。

## 完成项目接入之后

到这里，你已经可以在不修改 LoopX core 的情况下：

- 让现有 Git 项目拥有可恢复的 Goal、Todo、Gate 与 evidence；
- 从 Codex App 或 visible Codex CLI TUI 启动同一套项目状态；
- 在 Host 切换时保留 authority、identity 与 workspace boundary；
- 用 status、history 与 quota 检查真实 continuation。

接下来按目标选择：

- 要给 LoopX core 提交协议级改动，进入[协议地图与贡献入口](./source-protocol-map.md);
- 要交付独立安装的 Provider，进入[选择正确的放置位置](./08-extension-placement.md);
- 只使用 LoopX 管理项目，可以直接把本章模式应用到自己的 repository。
