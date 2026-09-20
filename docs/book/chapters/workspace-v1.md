# 操作 LoopX 1.0 Workspace

LoopX 1.0 的里程碑不是“多了一个 Dashboard”，而是把跨会话、跨 Agent 的长程工作收拢到一个
可检查、可操作的 Personal Workspace。Workspace 负责呈现和发起受治理的动作；Goal、Todo、
Gate、事件、配置和回执仍由控制面事实源拥有。

本章把 1.0 的操作面接回前六章的控制面模型。完成后，你应该能够：

- 启动 Workspace，并确认页面和状态投影来自同一个 LoopX 运行时；
- 从 Manager 总览进入单个 Goal，区分正在执行、等待确认、持续观察和已完成工作；
- 解释为什么界面中的写操作必须经过 typed preview、governed apply 与 verified receipt；
- 分开判断 Capability 可见性、Goal 配置、Provider readiness 和当前 Turn 可用性；
- 理解 Goal Channel、周期报告与桌面更新各自增加了什么权限，以及如何停用。

## 1.0 到底交付了什么

`v1.0.0` 是 **Personal Workspace milestone**。它把以下入口汇集到一个本地 operator surface：

| Workspace 表面 | 回答的问题 | 事实边界 |
| --- | --- | --- |
| Manager 总览 | 哪些 Goal 需要我、正在执行、持续观察或已经安排？ | 来自状态投影，不重新决定 Todo lifecycle |
| Goal / Tasks | 当前 Agent lane、待确认项、执行中 Todo、Monitor 与完成历史是什么？ | Todo 与 Gate 仍由控制面 owner 决定 |
| Chat | 如何在当前 Goal / Agent / Session 上继续协作？ | 会话不是 durable Goal state |
| Files / Reports | 本轮交付了什么，哪些报告已经验证？ | 只展示 public-safe preview 与证据指针 |
| Context / Settings | 当前 repository、Session、Goal Channel 和可选功能如何配置？ | 写入必须 preview、apply 并 read back |

这不是一套新的事实源。浏览器不能绕开 Kernel 直接修改 registry、Todo、quota 或 Host
automation；远端 SSH 投影仍保持只读，唯一例外是通过精确匹配的已配置 Host alias
路由 Goal 的 stop/resume；手工 URL 不会获得该权限。Stage 2C authority 与其他候选
Provider 仍按阶段提升，1.0 标签不代表所有 tenant 已经迁移。

发布事实以 [LoopX v1.0.0 release](https://github.com/huangruiteng/loopx/releases/tag/v1.0.0)
为准；界面细节与恢复路径见
[Personal Workspace 使用指南](/loopx/docs/guides/personal-workspace-user-guide/)。

## 启动并验证同一个运行时

先确认安装版本与环境，再启动本地 Workspace：

```bash
loopx --version
loopx doctor
loopx dashboard --no-open
```

命令会打印实际 loopback URL；默认页面和状态投影可以这样读回：

```bash
curl -fsS http://127.0.0.1:8767/chat/ >/dev/null
curl -fsS http://127.0.0.1:8767/status.json
```

`loopx dashboard` 同时提供打包后的 Workspace、状态投影和 Agent Chat。若相同版本的桌面壳已经
启动了服务，它会复用通过 capability fingerprint 验证的进程，而不是启动第二套事实源。端口只是
默认值；自动化检查应读取命令输出，不要把默认 URL 当成永久合同。

打开 Workspace 后，先做三项读回：

1. Manager 总览中的 Goal 数量和 `loopx status` 是否对应；
2. 目标 Goal 的 Agent lane、Task 状态和 `loopx todo list --goal-id <goal-id>` 是否对应；
3. Context 中的 repository / source 是否指向当前要操作的主机与 worktree。

页面出现不等于控制面健康。如果 `status.json`、Goal 详情或当前 source 显示失败，先恢复对应
运行时或投影，再执行写操作。

## 从 Workspace 读一项工作

Manager 的四条 lane 是 operator 投影，不是四种新的 Todo 状态：

- **需要你：** User Todo、权限 Gate 或必须由 owner 决定的动作；
- **执行中：** 当前可推进的 Agent Todo 与活跃 lane；
- **观察中：** 有 cadence、触发条件或外部事实等待的 Monitor；
- **已安排：** 已绑定 Host schedule，但当前没有到执行时间的工作。

进入 Goal 后，再把卡片还原成控制面问题：

```text
Goal / Acceptance
  -> selected Todo and owner
  -> Gate, capability and workspace eligibility
  -> current Session / Host
  -> evidence, receipt and successor
```

已完成历史是只读证据，不会重新进入 frontier。Files 中的报告或产物摘要也不是完整原始文件；
需要审计时，沿 `todo_id`、run identity、evidence pointer 或版本化 artifact 回到权威来源。

## 写操作：preview、apply、receipt

Workspace 中的 Goal、Todo、Heartbeat、Monitor 与设置变更遵循同一条安全链：

```text
typed preview -> human or policy review -> governed apply -> verified receipt -> refreshed projection
```

Preview 冻结规范化参数、影响范围和当前 revision。Apply 只能执行仍然匹配该 preview 的动作；
状态已经变化时应返回 stale 或 Gate，而不是悄悄套用旧决定。Receipt 与 readback 才能证明写入完成；
按钮点击或 HTTP 成功本身都不够。

以暂停 Goal 为例，第一条命令只预览：

```bash
loopx goal-lifecycle --goal-id <goal-id> --operation stop
loopx goal-lifecycle --goal-id <goal-id> --operation stop --actor-kind owner --execute
loopx quota status --goal-id <goal-id>
```

执行 lifecycle transition 时必须显式传入 `--actor-kind owner` 或 `controller`；
匿名预览仍然保持只读。

暂停会让该 Goal 退出 active attention，并使有效自动运行 quota 投影为 0；Todo、历史、证据和配置
仍保留。恢复使用显式 `resume --execute`，且不会绕过 Todo、Gate 或 quota。不要把 stop 写成
“完成 Goal”，也不要用改 quota 的方式意外恢复一个被 owner 停止的 Goal。

## 配置 Capability 与机器策略

1.0 Workspace 能展示 Goal capability 与 typed machine policy，但四种事实必须分开：

| 事实 | 读取入口 | 不代表什么 |
| --- | --- | --- |
| Capability 已发布 | `loopx capability list/show` | 不代表当前 Goal 已启用 |
| Goal 已配置 | `loopx configure-goal --goal-id <goal-id>` | 不代表 Provider ready |
| Provider ready | 对应 Extension / Provider doctor | 不代表当前 Turn 通过 Gate |
| 当前 Turn 可用 | `quota should-run` 的 capability / workspace 结果 | 不授予额外外部权限 |

先做只读发现：

```bash
loopx capability list --format json
loopx machine-config describe
loopx machine-config inspect --format json
loopx configure-goal --goal-id <goal-id>
```

机器策略和 Goal 设置都必须先生成 delta / plan，再显式执行并读回 revision。不要从 Capability 名称
猜配置 flag，也不要把“catalog 中可见”写成“已启用”。启用自适应子 Agent 等可选能力不会强制
并行，也不会授予新的 Goal、repository、credential、发布或生产权限。

## Goal Channel：消息不是隐式 authority

Workspace 的 Lark / 飞书设置可以把一个 Goal 连接到具体 Topic 和目标 Agent。Capture scope
只决定哪些消息进入连接；它不扩大 Agent 权限。Ingress mode 决定消息怎样进入运行时：

- `live_steering`：只投递给该 Agent 当前精确的活跃 Turn；
- `session_queue`：进入同一精确 Session 的有界 FIFO，当前 Turn 后处理；
- `async_inbox`：进入 Agent 的本地私有 inbox，等待后续显式 drain。

配置后应读回 Goal、Agent、Topic、ingress mode、Session binding 与监听状态。验证
`async_inbox` 时，可以在自己发送一条新的测试消息后执行：

```bash
loopx lark-inbox drain --goal-id <goal-id> --agent-id <agent-id>
```

Disconnect 只移除该 Goal 的 Topic route，不删除 Goal、Session、历史或其他连接。消息到达也不
代表 Agent 获得发送、仓库写入或生产权限；这些动作继续通过各自的 Gate 与 Provider readback。

## 报告：一次生成与持续投递分开

在活跃项目会话中明确请求“生成本周项目报告”，会启用一次 provider-free 的 Markdown / HTML
生成。先用只读命令检查内置 profile：

```bash
loopx periodic-report inspect-profile --preset weekly --format json
```

回执中的 `active` 与 `generation_allowed` 应同时为 `true`。内置 weekly profile 没有 schedule，
也没有 sink，因此一次生成不会创建周期任务或发送消息。

持续报告是另一条授权链：自定义 profile 声明 cadence，Host Automation 负责唤醒；机器或 Goal
subscription 的 `enabled: true` 与显式 `route_ref` 构成持续投递授权。暂停 Automation、禁用
profile 或关闭 subscription 会停止对应路径。报告生成成功不等于外部发送成功；Provider、发送
身份、route 和消息 readback 仍需分别验证。

## 桌面更新与恢复

1.0 的 macOS 桌面更新把 App 与内置 runtime 绑定到同一 revision。旧桌面壳需要一次手动替换；
之后在 **Recovery & updates** 中显式选择 stable 或 main、安装并重启。

- **验证：** 对照 App 版本、Workspace runtime identity、`loopx --version` 与 `loopx doctor`；
- **修复：** **Repair this version** 重装当前 App 配套 runtime；
- **回退：** 有已验证备份时使用 **Restore previous version**，重启后再次核对 identity；
- **边界：** 更新只能来自固定官方 feed；macOS 使用 updater signature 与 ad-hoc code signing，
  不应描述为 notarized。回退安装也不承诺逆转未来不兼容的 Goal schema。

浏览器 / PWA 用户继续使用 CLI update 流程。CLI 更新不能修复原生壳的启动器或 updater 缺陷。

## 1.0 操作验收表

完成一次 Workspace 验收时，至少确认：

- `loopx --version` 与预期 release 一致，`loopx doctor` 的必需检查通过；
- Workspace 与 `status.json` 来自同一个已验证运行时；
- Manager 和 Goal 页面能解释为现有 Goal / Todo / Gate / Monitor 状态；
- 每个写操作都有 preview、apply、receipt 和 refreshed readback；
- Capability、Goal config、Provider readiness 与 Turn eligibility 没有混为一个“已启用”；
- Goal Channel 与报告的外部投递都有精确 route、identity 和 readback；
- staged authority、SSH source 与浏览器 presentation 没有被误写成新的写权限。

接下来可以按目标继续：管理现有项目回到[连接你的 Git 项目](./05-connect-existing-project.md)；
修改 Workspace 或控制面实现时进入[开发者贡献地图](./source-protocol-map.md)；需要完整界面细节时
查阅 [Personal Workspace 使用指南](/loopx/docs/guides/personal-workspace-user-guide/)。
