# 从 Codex CLI 可见 TUI 启动

Codex CLI 路径的关键约束是 **visible and interruptible**：工作应继续发生在用户可见的 TUI 中，
而不是为了“自动化”默认切换到隐藏的 headless worker。

## 成功标准

完成后，你应该能观察到：

- `codex` 从目标项目根目录启动；
- setup Turn 复用或连接 LoopX 状态；
- 当前 Codex task 被设置为可见 `/goal <thin task_body>`；
- 后续 Turn 在同一 TUI 中继续；
- LoopX 仍拥有 Todo、Gate、Quota 与 writeback；
- 用户可以随时查看、打断或恢复。

## 1. 启动可见 TUI

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

setup Turn 的任务是建立连接和可见 continuation，不应顺手开始一大段未经规划的交付。

## 2. 用 `$loopx` 开始具体目标

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
`/goal <task_body>`。

## 3. Native Goal 与 LoopX 的组合

Codex CLI native Goal 拥有同一 TUI 内的 continuation。LoopX 拥有项目级 frontier：

```text
Visible Codex /goal
  -> run LoopX quota decision
  -> execute selected bounded Todo
  -> validate
  -> write LoopX state
  -> continue, wait, block, or complete
```

当 LoopX 返回 Gate 时，Goal 可以进入 blocked 状态；当用户处理 Gate 后，再通过 Host 的 Goal
恢复表面继续。不要通过创建第二个 Goal 绕过原 Gate。

## 4. 验证 visible continuation

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
heartbeat。若 packet 报告 scheduler context 缺失，先修复 runtime profile，不要忽略 warning。

## 5. 保持身份与 Todo 归属

新的 argument-bearing guided start 在 Goal 已有已注册身份（哪怕只有一个）时不会默认注册 fresh
Agent，而是返回 identity gate 要求选择其中一个 lane；只有 Goal 没有任何已注册 lane 或显式
`--new-peer` 时才默认 fresh。已有 id 只在用户明确要求 takeover 那个 peer 时复用。完成选择后，
visible Goal、quota、refresh 与 writeback 都应显式保留同一个 `--agent-id`；缺失或不匹配时应
fail closed，而不是回退到“唯一身份”。

Agent identity 表达 LoopX 工作 lane，不证明具体 Host。判断工作是否真的在 Codex CLI 运行，要看
`host_surface`、runtime profile 或对应 run metadata。

交接时的正确顺序是：

1. 当前 Agent 写回验证结果；
2. 更新或完成 Todo；
3. 新 Agent 以 fresh id 预览并完成原子注册；
4. 新 Agent claim 未完成 Todo；
5. 新 Host 读取同一 registry 与 Goal；
6. 再启动 visible Goal。

## 恢复路径

### TUI 关闭

重新从同一项目根目录启动 `codex`，读取 `loopx status`，再恢复原 Goal。不要重新 bootstrap
一个相同 objective。

### `/goal` body 过期

稳定 body 不复制动态 Todo，但协议或 CLI 版本可能变化。重新生成当前 thin task body，并让 Host
替换 visible Goal；不要手改内部字段来“兼容”旧 prompt。

### 误用了隐藏 worker

停止该 worker，检查它是否写回了新 evidence 或 lease。先恢复 Todo ownership，再回到 visible
TUI；不要让两个执行者并发修改同一工作树。

### Goal 无变化轮询

达到 unchanged limit 后，Goal 应阻塞或安静等待。外部状态观察应转成 monitor Todo；用户通过
Host 的 Goal resume 表面恢复，而不是反复重发完整任务。

### App 与 CLI 同时激活

检查 claim、lease 与 scheduler ownership。两种 Host 可以读同一 Goal，但同一个有副作用的 Todo
只能有一个合法执行者。

## 完成项目接入之后

到这里，你已经可以在不修改 LoopX core 的情况下：

- 让现有 Git 项目拥有可恢复的 Goal、Todo、Gate 与 evidence；
- 从 Codex App 或 visible Codex CLI TUI 启动同一套项目状态；
- 在 Host 切换时保留 authority、identity 与 workspace boundary；
- 用 status、history 与 quota 检查真实 continuation。

接下来按目标选择：

- 要给 LoopX core 提交协议级改动，进入[协议地图与贡献入口](./source-protocol-map.md)；
- 要交付独立安装的 Provider，进入[选择正确的放置位置](./08-extension-placement.md)；
- 只使用 LoopX 管理项目，可以直接把本章模式应用到自己的 repository。
