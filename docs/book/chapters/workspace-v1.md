# 操作 LoopX 1.0 Workspace

`v1.0.0` 是 **Personal Workspace milestone**：它把跨会话、跨 Agent 的长程工作收拢到一个可检查、可操作的本地 operator surface。本章说明这个操作面和控制面事实源的关系，以及为什么界面上的动作必须走受治理的路径。

## 一个界面看起来正常的下午 {#pause-readback}

以下是合成诊断情境，不是一次已核实的生产事故。

```text
14:02  operator 打开 Workspace。Manager 总览显示 3 个 Goal，"执行中"车道有两张卡片。
14:03  他点开其中一个 Goal，看到 Todo、Agent lane 和一份报告摘要。
14:05  他点了"暂停"按钮。HTTP 返回 200。
14:06  页面上 Goal 仍然显示为活跃。
14:08  他刷新了一次。还是活跃。
14:20  他去查 CLI：`loopx quota status` 说这个 Goal 已经在 paused。
14:21  他确认两个读回不一致，开始核对 Goal、来源、读取时间和那次操作的回执。
```

这些观察尚不能证明 14:05 的操作没有 apply，也不能仅凭 CLI 的 paused 证明那次点击成功。可能是操作已提交而投影滞后，也可能 paused 来自另一项控制状态。先确认比较的是同一对象和来源，再找对应 operation receipt。

| 新取得的证据 | 可支持的结论 | 下一步 |
| --- | --- | --- |
| 原 stop 回执有效，当前 source 也已 stopped | 该操作已接受，旧 active 显示不再适用 | 修投影或读取路径，不重复 stop |
| 原入口明确拒绝或确认未提交 | 那次请求没有完成目标变化 | 修该拒绝条件，按当前 owner 入口重新预览 |
| 原结果无法读回或身份对不上 | 尚不能确认那次操作 | 保留原请求，恢复读回；不以反复点击猜结果 |

操作回执解释历史因果，当前 source 解释现在的状态，页面显示解释某次投影。三者相关，但不能互相替代。现场取证见[附录](appendix-reference.md#diagnostic-routing)。

## 为什么"页面显示什么就是什么"不成立 {#action-owners}

Workspace 呈现和发起受治理的动作，但 Goal、Todo、Gate、事件、配置和回执仍然由控制面事实源拥有。这带来三个不可避免的后果：

- **投影会滞后。** 页面上的状态是某个时刻的读模型；在你看到它和它被生成之间，源状态可能已经变化。
- **HTTP 成功不是写入完成。** 请求被接受和执行完成是两件事，中间可能被 Gate 拦下、被 stale 检查拒绝。
- **按钮不是独立的授权者。** Goal stop/resume 由 owner 权限与已审核 fingerprint 校验；Todo 操作由生命周期规则校验；自动 Turn 的可运行性再由 quota decision 判断。

所以本章的全部内容可以压成一句：**Workspace 是观察和发起动作的入口，不是一套新的事实源。**

## 先确认运行时，再看页面

页面出现不等于控制面健康。先核对版本和诊断，再启动或复用服务；`dashboard --no-open` 不是纯粹的状态查询：

```bash
loopx --version
loopx doctor
loopx dashboard --no-open
```

命令会打印实际 loopback URL。默认页面和状态投影可以这样读回：

```bash
curl -fsS http://127.0.0.1:8767/chat/ >/dev/null
curl -fsS http://127.0.0.1:8767/status.json
```

`loopx dashboard` 同时提供打包后的 Workspace、状态投影和 Agent Chat。若相同版本的桌面壳已经启动了服务，它会复用通过 capability fingerprint 验证的进程，而不是启动第二套事实源。**端口只是默认值**；自动化检查应读取命令输出，不要把默认 URL 当成永久合同。

打开 Workspace 后，先做三项对应检查：

1. Manager 总览中的 Goal 数量与 `loopx status` 是否一致；
2. 目标 Goal 的 Agent lane、Task 状态与 `loopx todo list --goal-id <goal-id>` 是否一致；
3. Context 中的 repository / source 是否指向当前要操作的主机与 worktree。

任何一项对不上，先恢复对应的运行时或投影，再执行写操作。

## 把页面读回控制面问题

Manager 的四条 lane 是 operator 投影，不是四种新的 Todo 状态：

| Lane | 实际含义 |
|---|---|
| 需要你 | User Todo、权限 Gate 或必须由 owner 决定的动作 |
| 执行中 | 当前可推进的 Agent Todo 与活跃 lane |
| 观察中 | 有 cadence、触发条件或外部事实等待的 Monitor |
| 已安排 | 已绑定 Host schedule，但当前没有到执行时间的工作 |

进入 Goal 后，把卡片还原成控制面问题：

```text
Goal / Acceptance
  -> selected Todo and owner
  → Gate, capability and workspace eligibility
  → current Session / Host
  → evidence, receipt and successor
```

已完成历史是只读证据，不会重新进入 frontier。Files 里的报告或产物摘要也不是完整原始文件；需要审计时，沿 `todo_id`、run identity、evidence pointer 或版本化 artifact 回到权威来源。

## 写操作：preview、apply、receipt

Workspace 中的 Goal、Todo、Heartbeat、Monitor 与设置变更遵循同一条安全链：

```text
typed preview -> human or policy review -> governed apply -> verified receipt -> refreshed projection
```

**Preview** 冻结规范化参数、影响范围和当前 revision。**Apply** 只能执行仍然匹配该 preview 的动作；状态已经变化时应返回 stale 或 Gate，而不是悄悄套用旧决定。**Receipt 与 readback** 才证明写入完成——按钮点击或 HTTP 成功本身都不够。

以暂停 Goal 为例，第一条命令只预览：

```bash
loopx goal-lifecycle --goal-id <goal-id> --operation stop
loopx goal-lifecycle --goal-id <goal-id> --operation stop --actor-kind owner --execute
loopx quota status --goal-id <goal-id>
```

执行 lifecycle transition 时必须显式传入 `--actor-kind owner` 或 `controller`；匿名预览仍保持只读。

暂停会让该 Goal 退出 active attention，并使有效自动运行 quota 投影为 0；Todo、历史、证据和配置仍保留。恢复使用显式 `resume --execute`，且不会绕过 Todo、Gate 或 quota。不要把 stop 写成"完成 Goal"，也不要用改 quota 的方式意外恢复一个被 owner 停止的 Goal。

## 四种"已启用"必须分开

1.0 Workspace 能展示 Goal capability 与 typed machine policy，但四种事实必须分开判断：

| 事实 | 读取入口 | 不代表什么 |
|---|---|---|
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

机器策略和 Goal 设置都必须先生成 delta / plan，再显式执行并读回 revision。不要从 Capability 名称猜配置 flag，也不要把"catalog 中可见"写成"已启用"。启用自适应子 Agent 等可选能力不会强制并行，也不会授予新的 Goal、repository、credential、发布或生产权限。

## Goal Channel：消息不构成隐式 authority

Workspace 的 Lark / 飞书设置可以把一个 Goal 连接到具体 Topic 和目标 Agent。Capture scope 只决定哪些消息进入连接；**它不扩大 Agent 权限**。Ingress mode 决定消息怎样进入运行时：

| Mode | 行为 |
|---|---|
| `live_steering` | 只投递给该 Agent 当前精确的活跃 Turn |
| `session_queue` | 进入同一精确 Session 的有界 FIFO，当前 Turn 后处理 |
| `async_inbox` | 进入 Agent 的本地私有 inbox，等待后续显式 drain |

配置后应读回 Goal、Agent、Topic、ingress mode、Session binding 与监听状态。验证 `async_inbox` 时，可以在自己发送一条新的测试消息后执行：

```bash
loopx lark-inbox drain --goal-id <goal-id> --agent-id <agent-id>
```

Disconnect 只移除该 Goal 的 Topic route，不删除 Goal、Session、历史或其他连接。消息到达也不代表 Agent 获得发送、仓库写入或生产权限；这些动作继续通过各自的 Gate 与 Provider readback。

## 一次生成与持续投递是两条授权链

在活跃项目会话中明确请求"生成本周项目报告"，会启用一次 provider-free 的 Markdown / HTML 生成。先用只读命令检查内置 profile：

```bash
loopx periodic-report inspect-profile --preset weekly --format json
```

回执中的 `active` 与 `generation_allowed` 应同时为 `true`。内置 weekly profile 没有 schedule，也没有 sink，因此**一次生成不会创建周期任务或发送消息**。

持续报告是另一条授权链：自定义 profile 声明 cadence，Host Automation 负责唤醒；机器或 Goal subscription 的 `enabled: true` 与显式 `route_ref` 构成持续投递授权。暂停 Automation、禁用 profile 或关闭 subscription 会停止对应路径。报告生成成功不等于外部发送成功；Provider、发送身份、route 和消息 readback 仍需分别验证。

## 代价与边界

**代价一：操作变长了。** 一次修改不再是"点一下"，而是 preview → review → apply → readback。对熟悉的操作这会显得繁琐。

**代价二：界面必须重新读，不能凭旧画面判断。** 看到的状态随时可能滞后，任何重要判断前都要重新读一次源。

**代价三：需要理解四层"已启用"的区别。** 把它们压成一个开关会误判能力可用性，而分清它们需要额外的心智负担。

**边界一：Workspace 不拥有事实。** 界面损坏不能证明控制面仍健康，也不能证明它已经停止。先区分 source、runtime 与 projection；只有源状态和运行条件确认正常时，才把故障限定为显示恢复，而不是通过界面手改状态。它也不构成 Stage 2C authority——shared-authority provider 的资格与提升由独立的 shadow 与 conformance 流程决定，不由某个操作面出现而成立。

**边界二：桌面更新的范围有限。** 更新只能来自固定官方 feed；macOS 使用 updater signature 与 ad-hoc code signing，不应描述为 notarized。回退安装也不承诺逆转未来不兼容的 Goal schema。

**边界三：CLI 更新不能修复原生壳缺陷。** 浏览器 / PWA 用户继续使用 CLI update 流程；桌面壳的启动器或 updater 问题需要桌面侧处理。

## 1.0 操作验收表

完成一次 Workspace 验收时，至少确认：

- `loopx --version` 与预期 release 一致，`loopx doctor` 的必需检查通过；
- Workspace 与 `status.json` 来自同一个已验证运行时；
- Manager 和 Goal 页面能解释为现有 Goal / Todo / Gate / Monitor 状态；
- 每个写操作都有 preview、apply、receipt 和 refreshed readback；
- Capability、Goal config、Provider readiness 与 Turn eligibility 没有混为一个"已启用"；
- Goal Channel 与报告的外部投递都有精确 route、identity 和 readback；
- staged authority、SSH source 与浏览器 presentation 没有被误写成新的写权限。

## 不变式

1. **界面是投影，不是事实源。** 每次重要判断前重新读一次源。
2. **HTTP 成功不证明写入完成。** 只有 receipt 与 readback 能证明。
3. **点击不构成授权。** Owner 操作由对应 Goal lifecycle 接受，Todo 由其生命周期校验，自动 Turn 由当前 quota contract 准入；页面不重新授予任何一种权限。
4. **四种"已启用"需要分别证明。** 它们可以互为前提，但不能把一个开关的值当成所有条件均已满足。
5. **消息到达不扩大权限。** Capture scope 与 ingress mode 都不授予新的写权限。

这五条回答同一个问题：**当界面告诉你"一切正常"时，凭什么相信它？**
