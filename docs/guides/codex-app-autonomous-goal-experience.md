# Codex App 自动续跑体验指南

本指南用一个可丢弃的本地 Git 试验仓库，完整体验 LoopX 的长程工作闭环：

```text
$loopx 任务
  → Goal、Agent 身份和 Todo
  → Codex App Heartbeat 自动化
  → Codex Agent 周期性唤醒
  → quota should-run 决定本轮是否可执行
  → 有边界的工作、证据和状态写回
  → Dashboard 展示进度、等待和完成状态
```

这不是让一个模型会话无限运行。每次 Heartbeat 都先读取当前 LoopX
状态；只有配额、Todo、能力和 Gate 都允许时，Codex Agent 才会执行一个
有边界的工作片段。

## 完成这个体验后你应确认的事

| 要确认的能力 | 成功信号 |
| --- | --- |
| Goal 已持久化 | CLI 能读取相同的 `goal_id`、Todo 和状态。 |
| Codex App 已成为执行宿主 | 已回读到绑定当前 Goal、Agent 和线程的 Heartbeat 自动化。 |
| 自动唤醒受 LoopX 管理 | 首次以 3 分钟为建议节奏；之后遵从 `scheduler_hint` 继续、退避或停止。 |
| Dashboard 是观察面 | 看板展示 Todo、Run、证据和 Gate；打开或关闭页面都不创建 Agent Turn。 |
| 可以安全停止 | Goal 暂停后，下一次配额检查会要求宿主暂停或删除 Heartbeat。 |

## 开始前

适用范围是 **Codex App**，不是 Codex CLI、Codex App over SSH 或其他宿主。
这些宿主的循环驱动不同；本路径依赖 Codex App 提供的
`automation_update`。

准备以下条件：

- 已安装 `loopx`，且当前终端可执行它；
- 一个可写、非生产、可丢弃的 Git 仓库；
- 一个能在该仓库中操作的 Codex App 会话；
- 你理解启用 Heartbeat 后会周期性消耗 Agent 计算。首次安装前必须审阅
  预览并明确确认。

不要把 `.loopx/`、`.codex/goals/`、运行记录、自动化数据库、凭证或原始
Agent 日志提交到仓库。

## 第 1 步：创建隔离的试验仓库

**在哪里操作：** 终端。

**输入：**

```bash
LOOPX_LAB_DIR="$(mktemp -d)"
cd "$LOOPX_LAB_DIR"
git init
loopx doctor --agent-type codex-app
```

**预期效果：**

- `git init` 成功，当前目录是独立的空试验仓库；
- `loopx doctor` 报告 `ok: true`，并确认 Codex App 所需的安装和运行时检查；
- 这一步尚未创建 Goal，也没有安排自动执行。

如果 `loopx` 不存在，先按[安装指南](installing-loopx.md)安装，再重试本步。

## 第 2 步：在 Codex App 发起长程任务

**在哪里操作：** 打开上述试验仓库的 Codex App 会话，在输入框中操作。

**输入：**

```text
$loopx --fine-grained 在当前试验仓库中完成一份 LoopX 自动续跑体验报告。

完成条件：
1. 创建一份 Markdown 报告，说明 Goal、Todo、Heartbeat、quota 和 Dashboard 的关系；
2. 报告包含至少一个经过验证的本地检查结果；
3. 每个 Todo 完成后写回证据和下一步；
4. 达到上述完成条件后关闭 Goal，不再继续执行。

先只完成 LoopX 初始化、Goal/Todo/Agent 身份创建和 Codex App Heartbeat 安装。
展示所有预览；在我确认后才启用自动化。
```

**预期效果：**

- Codex 识别当前宿主为 `codex-app`；
- LoopX 先检查是否已有活跃 Goal。若有，复用同一 Goal/Agent 身份；若没有，
  生成新的受控启动事务；
- 你会看到 Goal、Agent 身份、Todo、状态写回和宿主激活的预览或
  `ordered_steps`；
- 此时 **不应** 有自动化已启动或业务文件已修改的结论。

`$loopx` 是 Codex 中的显式 LoopX skill 入口。只运行
`loopx start-goal --guided` 同样会得到启动方案，但它本身只是引导预览，
不等于 Heartbeat 已安装。

## 第 3 步：确认受控启动并安装 Heartbeat

**在哪里操作：** 仍在同一个 Codex App 会话中，确认第 2 步产生的预览后输入。

**输入：**

```text
确认执行上述 LoopX 启动事务。只执行返回的 ordered_steps；安装 Codex App
Heartbeat 后，回读 Goal、Agent、自动化绑定和 quota should-run，不要在本次
设置回合开始报告正文工作。
```

**预期效果：**

1. LoopX 创建或复用 Goal，并注册一个精确的 `agent_id`；
2. 至少一个可执行的 Agent Todo 被写入并可读回；
3. 生成 Heartbeat 的 JSON 激活包，结果必须包含 `ok=true`；
4. Codex App 用 `automation_update` 保存其中的
   `LoopX managed heartbeat bootstrap v2` `task_body`；
5. 新 Heartbeat 建议从 3 分钟开始，并回读其 Goal、Agent、任务绑定和调度；
6. 后续唤醒加载当前 thin contract，而不是把一份旧的 thin、compact 或 full
   执行正文永久保存为自动化提示词。

这一步才是“启动 Codex Agent 自动续跑”的边界。若会话没有
`automation_update`，正确结果是一个可粘贴的宿主激活 Gate；不要手改数据库、
TOML 或声称自动化已经启用。

记录启动回读中的 `<goal-id>` 和 `<agent-id>`，供后续命令使用。

## 第 4 步：从终端验证控制面

**在哪里操作：** 试验仓库的终端。

**输入：** 将尖括号替换成第 3 步的实际值。

```bash
export GOAL_ID="<goal-id>"
export AGENT_ID="<agent-id>"

loopx status --goal-id "$GOAL_ID"
loopx quota should-run \
  --goal-id "$GOAL_ID" \
  --agent-id "$AGENT_ID" \
  --runtime-profile codex_app_heartbeat
```

**预期效果：**

- `status` 显示相同的 Goal、当前 Todo、运行状态和可能的用户 Gate；
- `quota should-run` 返回当前机器契约，包括 `should_run`、`waiting_on`、
  下一条工作通道和 `scheduler_hint`；
- `should_run=true` 表示下一次 Heartbeat 可以尝试有边界的工作；
- `should_run=false` 不代表故障。先依据 `waiting_on`、Gate、缺失能力或配额
  原因处理，不要为了制造活动而绕过限制。

这两个命令用于观察。真正的自动 Turn 由已安装的 Codex App Heartbeat 进入，
并在每次唤醒时读取更新后的同类契约。

## 第 5 步：启动 Dashboard

**在哪里操作：** 终端；随后在浏览器操作。

**输入：**

```bash
loopx dashboard --global-registry --goal-id "$GOAL_ID"
```

**预期效果：**

- 浏览器打开个人工作区；默认地址是 `http://127.0.0.1:8767/chat/`；
- 当前 Goal 被选中，或可在左侧 Goal 列表中选中它；
- Tasks 展示“待确认”“待执行 / 进行中”“定时与持续”和“已完成”四类工作；
- Goal 详情和 Run 抽屉展示公开安全的状态、进度、证据、产出和下一步。

Dashboard 读取 LoopX 状态并轮询刷新。它不是 Heartbeat 的执行器：关闭浏览器
不会停止已安装的 Codex App 自动化，打开浏览器也不会额外触发一次工作。

## 第 6 步：观察首次自动唤醒

**在哪里操作：** 无需输入；保持 Codex App 自动化处于启用状态，在 Dashboard
和终端观察。

**预期效果：**

1. Heartbeat 在初始建议节奏上唤醒 Codex Agent；
2. Agent 先读取 `quota should-run`，再选择当前允许的 Todo；
3. 若允许执行，Agent 完成一个有边界的片段，验证结果，并写回 Todo、证据和
   下一步；
4. Dashboard 出现新的 Run 或更新后的 Todo 状态；
5. 随后的 `scheduler_hint` 指示维持频率、退避、等待或停止。

在终端重新执行第 4 步的两个命令，并补充查看历史：

```bash
loopx history --goal-id "$GOAL_ID"
```

**预期效果：** 能看到压缩的运行历史和状态写回。Dashboard 只展示公开安全的
结果、摘要和证据，不应把原始工具调用、命令日志、凭证或本地绝对路径投影出来。

## 第 7 步：验证暂停与恢复

### 暂停

**在哪里操作：** 试验仓库的终端，或 Dashboard 的 Goal 暂停操作。

**输入：**

```bash
loopx goal-lifecycle --goal-id "$GOAL_ID" --operation stop
loopx goal-lifecycle --goal-id "$GOAL_ID" --operation stop --actor-kind owner --execute
```

**预期效果：**

- 第一条命令只预览，不写入；
- 第二条命令暂停 Goal，但不删除 Todo、历史或证据；
- 下一次 `quota should-run` 通知宿主暂停或删除当前 Heartbeat，避免下一轮自动
  工作；
- 已经开始的工具调用不会被强制终止；Dashboard 会将 Goal 放入已停止视图。

### 恢复

**在哪里操作：** 同一终端或 Dashboard。

**输入：**

```bash
loopx goal-lifecycle --goal-id "$GOAL_ID" --operation resume --actor-kind owner --execute
loopx quota should-run \
  --goal-id "$GOAL_ID" \
  --agent-id "$AGENT_ID" \
  --runtime-profile codex_app_heartbeat
```

**预期效果：** Goal 恢复调度资格；是否真的继续，仍取决于 Todo、Gate、能力和
配额。恢复不会绕过这些控制条件。

## 常见观察结果与下一步

| 看到的结果 | 表示什么 | 下一步 |
| --- | --- | --- |
| `automation_update` 不可用 | 当前会话不能创建 Codex App 自动化。 | 使用返回的宿主激活 Gate，在具备该工具的 Codex App 会话中完成；不要自行编辑自动化存储。 |
| `should_run=false` | Goal 正在等待用户、证据、能力、配额或暂停状态。 | 读取 `waiting_on`、Gate 和 `scheduler_hint`，处理真实阻塞项。 |
| Dashboard 没有新 Run | 可能尚未到唤醒时间、自动化未回读，或当前工作不被允许。 | 先用第 4 步验证状态和配额；再在同一 Codex App 线程要求回读 Heartbeat。 |
| Goal 完成后不再继续 | 这是预期行为。 | 让最终配额检查指导宿主停止 Heartbeat；需要新工作时，在同一 Goal 中创建后续 Todo，或明确启动一个新 Goal。 |
| Dashboard 显示等待你的确认 | 用户 Gate 优先于自动推进。 | 在 Dashboard 或受控 CLI 入口完成决定；不要通过修改 Todo 文本伪造批准。 |

## 进一步阅读

- [Getting started](getting-started.md)：安装、连接、Todo、配额和命令全览。
- [Newcomer command path](newcomer-command-path.md)：最短的宿主选择与 `$loopx` 路径。
- [Heartbeat automation prompt](../heartbeat-automation-prompt.md)：Codex App
  Heartbeat 的 bootstrap、调度回读和停止契约。
- [Dashboard guide](../../apps/presentation/dashboard/README.md)：个人工作区、
  状态服务和 Goal 生命周期操作。
