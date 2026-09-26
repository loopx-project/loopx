# RFC：Agent 会话执行模式（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** Partial。挂接宿主（attached host）的绑定、broker 与运行时围栏已在
  `main` 上交付；下文中的跨宿主接入契约仍是提案，且只被部分强制。
- **作者 / 负责人：** 由维护者指示整理的契约。它把桌面端提案中已有的会话归属决策
  抽取并规范化。会话归属与宿主接入决策需要维护者接受。
- **创建日期：** 2026-09-15
- **最近规范性修订：** 2026-09-15
- **实现基线：** `6c3da75ca`
- **相关契约：** [桌面执行前端](desktop-execution-frontends-v0.zh-CN.md)、
  [单属主本地守护进程](single-owner-local-daemon-v0.md)、
  [有能力的管家与语义交接](capable-manager-semantic-handoff-v0.zh-CN.md)、
  [受治理的 Turn](../../reference/protocols/loopx-turn-v0.md)、
  [宿主模式规划](../../reference/protocols/host-mode-plan-v0.md)、
  [挂接 Agent 会话 broker](../../integrations/attached-agent-session-broker.md)
- **语言镜像：** [English](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/agent-session-execution-modes-v0.md)

语言说明：本中文版本与
[英文版本](./agent-session-execution-modes-v0.md) 互为语义镜像。两者出现差异即为缺陷。

## 文档地图与维护契约

第 1-11 节是稳定的设计与验收契约。第 6 节是规范性的归属地图，是"哪份文档拥有哪个
边界"的权威答案；发现矛盾应当作为缺陷上报，而不是在别处用散文另行裁定。第 12 节是
规范性交付计划。第 13 节列出仍需批准的决策，其中的建议不构成已接受的决策。附录 A-E
是非规范性的。

本 RFC 本身不改变运行时行为。它命名了现有代码已经部分实现的归属契约，使新的宿主
前端可以在不另造 Goal、Todo、会话或执行权威的前提下被接入。

## 1. 决策摘要

1. 每个 LoopX Agent 会话绑定都携带且仅携带一个显式执行模式，取值来自封闭集合：
   - `managed_runtime`：由 LoopX 拥有的宿主创建、启动、监督、中断、停止并替换运行时
     会话；
   - `attached_host`：一个已经在运行的外部宿主会话被绑定到 LoopX，该外部宿主保留
     进程、对话、中断、恢复和执行循环的归属。
2. 模式在创建绑定时选择，随绑定持久化，在回读中可见，并且绝不隐式改变。重连可以
   恢复相同的模式与会话身份，但不得切换模式。
3. 两种模式下 LoopX 都独占工作事实。Goal、Todo、claim、gate、quota、evidence、
   已接受的进展与终态都是 LoopX 状态。对话——包括语气笃定的聊天消息——不是写入回执。
4. 一个 Agent 级绑定至多有一个活跃执行器。输入通过一条有序会话队列串行化，重复或
   冲突的执行尝试以类型化错误失败关闭，而不是竞态执行。
5. 模式与传输、事件源、用户可见的宿主模式选择、以及入口/投递模式相互正交。绑定没有
   声明的投递能力不可用，并且不可用必须失败关闭，而不是静默改派到另一个执行器。

保持不变的部分：桌面端产品流程、连接器模型、Web/Lark 收敛与 computer use 范围仍属于
[桌面执行前端 RFC](desktop-execution-frontends-v0.zh-CN.md)；本地服务身份与监督仍属于
[单属主守护进程 RFC](single-owner-local-daemon-v0.md)；有界 Turn 事务仍属于
[LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md)；面向用户的宿主选择仍属于
[宿主模式规划 v0](../../reference/protocols/host-mode-plan-v0.md)；管家语义与延续路径
仍属于 [管家 RFC](capable-manager-semantic-handoff-v0.zh-CN.md)。

默认与 opt-in 边界：新建的 LoopX Chat 会话默认是 `managed_runtime`。挂接外部宿主会话
需要显式绑定命令 opt-in，托管的宿主只在操作者启动时才运行。任何会话都不会被自动
挂接、恢复、替换或迁移，且任何模式都不授予 Goal、quota 或权限权威。

本 RFC 不批准以下事项：新的常驻服务、自动恢复会话、跨宿主会话接管、由消息或探测
副作用触发的模式变更、把任何可选预览宿主提升为受支持的产品面，以及任何新的凭据或
权限边界。

## 2. 问题与动机

操作者以两种形态运行 Agent 会话，它们在界面上看起来相似，底层却截然不同：一种由
LoopX 启动，另一种已经属于其他宿主。当绑定没有说明自己属于哪种形态时，投影与真实
执行器就会分叉。已观察到的失败形态：

- 用户有一个带宝贵上下文的长时可见宿主会话。"继续这个 Goal" 的请求启动了第二个
  运行时，于是两个执行器推进同一个 Todo。
- 宿主会话结束、崩溃或被替换，而它的 LoopX 绑定仍显示 `ready`。界面报告有工作正在
  进行，但背后没有任何可以执行的东西。
- 对话中就某计划达成一致，这一致被当作已接受状态：Todo 或进展在没有经过 LoopX 验证
  与回写契约的情况下出现。
- 新增了一个传输或连接器，其存在被当作"已有工作会话挂接到该 Agent"的证据。
- 某个可选宿主集成在 LoopX 之外定义自己的 Goal、成员或执行记录，成为第二个、更安静
  的权威。

现有属主无法各自解决这个问题。LoopX Chat store 知道绑定，broker 知道 claim 与完成
回执，桌面前端知道自己的产品流程，而每个外部宿主知道自己的进程语义。缺少一份统一的
接入契约时，每个新宿主都会重新裁定会话归属，而每一次重新裁定都是一次产生第二权威的
机会。这一风险是具体的而非假设的：一个可选的本地宿主原型为 AI 主导的团队工作引入
了对话式建档和自己的一套有界执行，它必须被接入同一份契约，而不是另立一份。

### 不变量

每个实现都必须保持以下性质。

1. **LoopX 拥有工作事实。** 两种模式下 Goal、Todo、claim、gate、quota、evidence、
   已接受的进展和终态都是 LoopX 状态。
2. **宿主拥有执行机制。** 进程生命周期、模型与工具循环、沙箱、中断、恢复、原始
   transcript 与不透明上游会话句柄属于执行宿主，不属于 LoopX。
3. **Agent 拥有工作会话路由。** 一个 Goal 可以有多个 Agent。运行时、传输与事件源
   绑定通过显式注册的 `agent_id` 解析，绝不经过 Goal 级默认值。
4. **一个绑定至多有一个活跃执行器。** 入口被串行化，重复或冲突的启动失败关闭。
5. **模式是显式的、持久化的、可回读的。** 它绝不从散文、提示词、能力探测或传输中
   推断，也绝不隐式变化。
6. **对话不是写入回执。** 实质状态变更需要相应的 LoopX 验证与回写契约。
7. **provider 拥有推理。** provider 凭据、端点、模型可用性与原始负载不是 LoopX 的
   任务状态。
8. **不可用的投递失败关闭。** 绑定只能使用它声明的投递能力；否则结果是类型化的
   不可用，或显式声明的回退，绝不改派到另一个执行器。

## 3. 范围与非目标

### 范围内

- 封闭的模式词表及其持久化与回读要求；
- 绑定身份模型：已注册 Agent、执行器端点、宿主面、有序会话队列；
- 新宿主（包括可选预览宿主）在把会话绑定到 LoopX 之前必须满足的接入契约；
- 针对能力门控投递与重复执行器的失败关闭行为；
- 覆盖会话、执行、延续与协作的 RFC 与协议之间的归属地图。

### 非目标

- 桌面端产品流程、连接器模型、Web/Lark 收敛、Bot 入口模式与可选 computer use。这些
  仍属于 [桌面执行前端 RFC](desktop-execution-frontends-v0.zh-CN.md)。
- 本地服务身份、就绪、监督与迁移。这些仍属于
  [单属主守护进程 RFC](single-owner-local-daemon-v0.md)。
- 管家语义、语义交接与会话延续路径。这些仍属于
  [管家 RFC](capable-manager-semantic-handoff-v0.zh-CN.md) 与
  [显式延续契约](cross-session-memory-substrate-v0.zh-CN.md)。
- 哪个托管运行时被提升用于生产工作。这仍属于
  [运行时选型评估](harness-selection-dsh-pi-v0.zh-CN.md)。
- 有界 Turn 事务及其处置规则。这些仍属于
  [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md) 与
  [Turn 循环控制器](../../reference/protocols/turn-loop-controller-v0.md)。
- 面向用户的宿主模式选择、连接器目录条目、provider profile、模型路由与定价。
- 新的授权、凭据、租户或常驻服务。

## 4. 当前系统契约

在 `6c3da75ca` 上审计。以下是当前事实，不是提案行为。

| 边界 | 当前行为 |
| --- | --- |
| 模式词表 | [`loopx/chat_store.py`](../../../loopx/chat_store.py) 定义 `managed_runtime` 与 `attached_host`，拒绝其他取值，且新会话默认 `managed_runtime`。 |
| 绑定字段 | 会话创建接受 `session_mode`、`executor_endpoint_id`、`host_surface`、`attached_capabilities`。挂接绑定要求非空 `host_surface`；托管 Codex home 不能被挂接。 |
| 能力键 | 挂接能力被过滤到封闭集合 `live_steering`、`session_queue`、`claim_wait`、`reply_readback`；未知键被丢弃。 |
| 公开投影 | `public_session()` 返回模式、执行器端点、宿主面、挂接能力与管家运行时回读。它不返回宿主会话 id 或消息体，且托管会话的挂接能力被置空。 |
| 挂接 broker | [`loopx/attached_session.py`](../../../loopx/attached_session.py) 在 `loopx_attached_agent_session_broker_v0` 下实现 bind/claim/complete，适配器类型 `attached_host_session`，上游模式 `host_broker`，claim 等待上限 1800 秒，claim 与完成回执去重，并按绑定加文件锁。 |
| 运行时围栏 | [`loopx/chat_runtime.py`](../../../loopx/chat_runtime.py) 绝不为挂接会话启动托管适配器，并以类型化错误失败关闭，例如 `attached_session_live_steering_unavailable`、`live_steering_requires_active_turn`、`live_steering_session_not_attached`。 |
| CLI 面 | `loopx worker-bridge attached-session-bind`、`-list`、`-claim`、`-complete` 存在于 [`loopx/cli_commands/worker_bridge.py`](../../../loopx/cli_commands/worker_bridge.py)，并在 [broker 指南](../../integrations/attached-agent-session-broker.md) 与 [worker-bridge 安装契约](../../integrations/worker-bridge-install-contract.md) 中记录。 |
| 原会话委派 | [`loopx delegation`](../../reference/local-delegation.md#use-an-existing-agent-conversation-through-its-shell) 让有 shell 能力的原 Agent 使用与 MCP 相同的显式执行绑定；`operations` 无需记住 ID 即可找回自身委派，重新核验 accepted，明确单条不可用及剩余分页。新挂载工具的 Goal Chat 复用同一目录，已存在的原生线程恢复时保留原工具 schema；不创建 Agent、不迁移宿主，也不安装自动唤醒策略。 |
| 聚焦测试 | [`tests/test_attached_session_cli.py`](../../../tests/test_attached_session_cli.py) 与 `tests/test_chat_codex_home.py::test_attached_session_uses_existing_host_not_managed_adapter` 覆盖 bind/claim/complete 与"不启动托管适配器"的围栏。 |
| 产品级提案 | [桌面执行前端 RFC](desktop-execution-frontends-v0.zh-CN.md) 拥有 Mode A/Mode B 的产品对比、连接器与事件源正交性，以及桌面端非目标。 |
| 宿主侧循环指引 | [Codex CLI TUI loop](../../product/runtimes/codex-cli/codex-cli-tui-loop.md) 记录了一个可见宿主的会话挂接自动化与恢复选项。 |

这些事实尚未确立的内容：没有跨宿主接入契约，没有关于轮换或替换已绑定工作会话的
已接受答案，没有关于 `live_steering` 的稳态能力策略，没有针对"以对话方式创建 Goal 或
成员草稿"的可选宿主的已接受规则，也没有跨前端的模式感知投影一致性。

## 5. 提议架构

### 归属与权威

```text
loopx_agent_session_execution_mode_v0 =
  managed_runtime   # 由 LoopX 拥有的宿主创建并监督会话
  | attached_host   # 外部宿主会话被绑定到 LoopX
```

| 边界 | `managed_runtime` | `attached_host` |
|---|---|---|
| 进程所有者 | LoopX 拥有的宿主 | 外部宿主 / 宿主应用 |
| 会话创建 | 由宿主在绑定时或绑定前创建 | 挂接前已存在；LoopX 只记录 |
| 对话传输 | 宿主适配器 | 外部宿主已有的连接 |
| 执行循环驱动 | 宿主监督器加有界 LoopX Turn | 外部宿主自己的循环或提示词 |
| 断连行为 | 先协调进程状态，再提供恢复或重启 | 报告 stale 或 disconnected；绝不重启宿主 |
| 模式回退 | 绝不挂接到无关会话 | 绝不启动托管运行时 |
| 所需权威 | 除操作者启动动作外无额外要求 | 已存在的精确宿主-Agent 绑定 |

两种模式共享：Goal 绑定、Agent 作用域、有序会话队列、公开安全的会话回读、能力声明、
claim 与完成回执，以及"只有经过验证的回写才推进工作"这一规则。

被禁止的替代权威：宿主本地存储作为 Goal、Todo、quota、monitor 或生命周期事实；
对话文本作为回执；同一绑定上的第二个调度器；Goal 级默认绑定；以及从探测、传输或
提示词推断出的模式变更。

<a id="reusable-agent-operations-and-continuation-ownership"></a>

### 可复用的 Agent 操作与续跑归属

现有前端 Goal 对话是会话型 coordinator 的基线入口。所有者也可以把 peer
任务协调职责分配给已注册 Agent；职责本身不创建 Agent、不转移工作权威，也
不激活续跑驱动。本地管家继续负责跨 Goal 接待与所有者注意力；会话和 peer
coordinator 复用限定范围的委派、独立验收与结果返回。

[Goal 对话显式续跑](../../reference/goal-chat-continuation.md)这一阶段把 Codex
原生续跑接入输入框旁的 LoopX 模式，复用宿主绑定身份的委派、queue/inbox/steer
与暂停恢复。首次开启仅升级闲置执行器的工具并保留本地历史，不替换未完成的
原生 Goal。成员 Turn 仍由 TS 验收；原生完成不结算 canonical Goal 或报告 Todo。
其他主力驱动、Lark 等价、无人值守服务和下文完整多 Agent 验收项仍分别资格化。

本提案细化 managed 团队的交付契约，不新增 CLI 参数、不晋升宿主，也不改变现有
会话/profile 默认。区分三组身份：已注册 Agent、其当前宿主会话与执行代际、每次工作
请求/尝试。创建 Agent 不等于启动进程，挂接会话不等于领取工作，返回产物不等于工作验收。

| 操作族 | 复用的现有 owner | 必须读回的事实 |
| --- | --- | --- |
| 发现/创建/复用 Agent | Registry、directory、onboarding 和配置的 execution profile | 稳定 Agent 身份、生效范围和支持能力；重复创建不产生第二身份 |
| 挂接/启动/恢复/停止 | 本 RFC 的 binding 与选定 host adapter | 精确会话/代际、实际执行或阻塞、取消和替换读回；attached host 不获得替身执行器 |
| 发送/接收/返回 | Collaboration request owner 与[入口策略](desktop-execution-frontends-v0.zh-CN.md#agent-scoped-bot-ingress-modes) | 请求/实际 inbox、queue、steer 语义；投递、消费、工作采用仍是不同事实 |
| 领取/验证/结算 | 既有 Todo、lease、acceptance、quota owner | 当前执行 proof 与独立验收；宿主不能自行证明工作完成 |

这些是语义操作族，不是新增的万能 adapter API。主 Agent 和获授权的 managed worker
都可调用；宿主生命周期差异保持显式，协调角色不增加权限。

**续跑归属是独立于 session mode 和 provider 的轴。** 每个 binding 只有一个经过
资格化的下一执行机会 owner：

- **LoopX 受控 Turn：** 既有 runtime/scheduler 准入一次完整的有界工作；宿主运行
  自己的模型/工具循环，返回 typed candidate，经独立验证后结算。本地和云端宿主可
  实现同一合同。Turn 不是一次模型调用或脚本业务 phase；Agent 可在范围内调查、
  委派和修订。
- **原生 Goal runtime：** 一次提交 Goal/task body，由该 runtime 拥有续跑；必须
  验证其处理 LoopX continue/defer/complete、取消、预算和结果读回。Prompt 本身
  不是调度器，provider Goal 自评不替代 LoopX 工作验收。
- **同会话 host driver：** 显式激活的宿主集成可取得新鲜 LoopX 准入，将下一任务排入
  原会话。它与 provider 原生 Goal evaluator 分别资格化。

禁止在同一 binding 上，一边重复外部受控 Turn，一边包裹会自行续跑的原生 Goal。
更换续跑 owner 前，停止新增准入、对账未决工具和不确定副作用、fence 旧执行器，
再读回新绑定后执行；断连不授权该切换。

首个混合 managed cohort 优先让云端 adapter 与本地 worker 通过同一 governed Turn
合同，再扩展 native Goal profile。这是交付优先级，不是默认迁移；provider 资格由
[harness 选型 RFC](harness-selection-dsh-pi-v0.zh-CN.md)负责。复用现有 profile editor
和会话投影，不新建管家专属创建服务、任务账本或调度循环。

### Agent 创建与接入中的模型选择

这是对上述可复用 Agent 操作的设计细化，尚未交付通用模型目录 API。模型选择属于
Agent 的 execution profile 与绑定，不取决于它是否担任协调员。复用现有 managed
execution profile、子 Agent 启动偏好和 profile editor；管家的机器默认值只是一个
调用入口的默认配置，不是通用配置 owner。换模型不产生新的逻辑 Agent，也不授予
创建或启动 Agent 的权限。

| 步骤 | Owner 与必须读回的事实 |
| --- | --- |
| 发现候选 | 所选 executor/provider adapter 返回模型 ID、支持参数、能力限制、发现范围与新鲜度。分别记录 provider 目录存在、managed host 兼容和账号授权；未知或发现失败不能冒充空的支持列表。 |
| 请求与解析配置 | 共享 typed TS 边界校验调用者范围、允许的 profile、预算约束和显式配置优先级。保留请求模型/参数与解析值及来源的区别。SDK/网络发现留在 adapter，不让每个 Python launcher 复制选择或准入规则。 |
| 创建或接入 | 创建经所选 adapter 使用已解析配置；接入只观察既有宿主实际配置，不能静默改模型、启动替代执行器，或把请求偏好显示为已生效。重复创建复用现有身份与绑定合同。 |
| 启动并读回 | 配置 revision 绑定执行 generation，读回 provider 报告的模型与实际参数。目录可见或 Agent 定义创建成功，都不能证明推理会话可运行。配置不符或参数不支持必须给出可操作错误，不隐式换模型。 |

参数支持由 provider 决定：相同 reasoning-effort 标签在不同宿主中未必同义，speed、
thinking mode、上下文限制和工具支持也不是通用旋钮。采用小型公共选择合同与经校验的
provider 参数，不在核心维护厂商模型名单或一个全局参数枚举。凭据留在所选 provider
的凭据作用域中，不进入 Agent profile 或公开投影。

动态模型别名需要显式读回。只有 provider 暴露解析版本时才记录具体版本；否则标明
底层版本未知，不能把别名当作可复现快照。配置变更沿现有 binding revision/generation
与 rebind 边界生效；运行中工作保留已准入的配置，直到经过已验证的迁移。父 Agent
配置变更不静默修改已有子 Agent。获授权的子协调员仅能在继承的 profile 与预算范围
内选择，使用和主 Agent 相同的操作。

下一实现切片须为一个本地和一个云端执行器打通发现、选择、创建或接入、启动及读回。
验证不支持的参数、过期目录、授权不可用、重试、动态别名和运行中配置变更。受影响的
CLI、前端和 Lark 复用现有配置 owner/投影；单有后端字段不代表用户路径完成。在明确
披露的实现交付之前，既有默认值和显式宿主选择保持不变。

### 创建时的实际上下文与可恢复驻留

在已有 profile 与 binding owner 中细化创建/接入；以下是提议验收要求，
不是新的 factory API，也不表示已经实现驻留淘汰策略。三个输入必须独立：

| 输入 | 解析与读回 |
| --- | --- |
| 上下文选择 | 有界语义 brief 或宿主支持的历史投影，携带来源版本、覆盖范围和遗漏。历史 fork 不是进程 checkpoint，不复制未决工具、claim 或授权；重建接收方指令，不把父 coordinator 角色继承为权限。 |
| 执行偏好 | 从当前实际 profile 而非初始旧配置解析 model、effort 与支持的工具。共享 resolver 拥有显式/省略/清空意图及优先级；分别记录请求值、解析值、provider 观察值及其来源。角色名或配置可解析不证明已经生效。 |
| 执行权限 | 偏好解析后重新应用当前资源、工具、环境与预算范围。角色和上下文继承不能扩大它；工作区共享或隔离是明确的宿主事实，独立模型上下文不等于独立 worktree 或沙箱。 |

就绪需要实际 binding 能力和使用权限同时成立。模型目录可选、工具 schema 暴露、
所有者启用、宿主成功读回是不同观察。child 能推理不代表能继续委派或接受 steer；
派发前发现缺失条件并明确返回，不能通过偷偷换 provider 或扩大权限解决。
管家、项目 coordinator 和嵌套成员复用同一解析路径。

不要把身份、驻留和执行压成一个 Agent 状态：

| 维度 | 已有来源与不变量 |
| --- | --- |
| 已注册身份 | Registry/directory 拥有身份与范围。live-runtime 行缺失或展示截断不证明 Agent 已删除。 |
| 已加载运行时 | 宿主 supervisor 观察驻留、卸载、不可用或未知实例。这是概念区分，不新增 registry enum。 |
| 活跃执行 | Binding/Turn owner 标识当前执行与 generation。Turn 完成或中断本身不删除身份、不释放工作 lease，也不证明所有后代效果已停止。 |

未来容量控制必须声明计数口径：活跃执行、驻留实例、待提交 reservation、是否计入
root，都不同于注册身份数和展示上限。不要只为展示这些观察引入淘汰机制；宿主今后
若支持卸载，应验证无活跃执行/待处理输入且恢复依据已耐久保存，再资格化。
容量不足返回明确处置，不承诺自动排队。

创建重试先对账原操作和 provider 身份；丢失响应不能触发第二个运行时。恢复在执行
效果前重新核对 binding 版本/generation、当前范围、环境和耐久上下文；恢复身份
名单不启动所有成员。历史不可用或不支持 resume 时保留待办并指出具体缺口，走
另行资格化的 replacement 路径，不猜测 session 或扩大策略。复用已有操作 journal
与 supervisor，不另建生命周期账本。

创建/profile 阶段须覆盖父偏好变更、child 工具不支持、过滤/压缩上下文、共享工作区
假设、创建响应不确定、卸载与身份不存在的区别，以及恢复时授权撤销。用真实本地和
云端 adapter 读回验收；名单长度和角色文案不是验收证据。

### 状态模型与 schema

绑定是模式归属的单元。其规范字段：

| 字段 | 要求 | 语义 |
|---|---|---|
| `session_id` | 必需，不透明 | LoopX 绑定身份，不是宿主会话 id |
| `goal_id`、`agent_id` | 必需，不透明 | 精确的 Goal 与已注册 Agent 作用域 |
| `session_mode` | 必需，封闭集合 | `managed_runtime` 或 `attached_host` |
| `executor_endpoint_id` | 必需 | 执行器身份，与 `agent_id` 区分 |
| `host_surface` | `attached_host` 必需 | 用于精确接入的具名宿主面 |
| `attached_capabilities` | 可选，封闭键 | 仅对 `attached_host` 有意义；`managed_runtime` 下为空 |
| `channel_id` | 必需 | 该绑定的有序对话通道 |
| `status`、`active_turn_id`、`last_error_code` | 投影 | 当前生命周期回读，包含类型化失败 |

没有 `session_mode` 的历史行按 `managed_runtime` 读取；未知取值被拒绝，不做强制转换。
新增模式、能力键或绑定字段都是版本化契约变更，移除任何一项都需要 RFC 索引所要求的
兼容性与批准证据。公开投影绝不携带宿主会话 id、transcript、消息体、凭据或本地路径。

### 命令与事件生命周期

挂接绑定：

1. **Bind。** 绑定一个精确的 `(Goal, 已注册 Agent, 宿主面, 宿主会话, 执行器端点)`
   元组。同一元组重复绑定是幂等的；同一绑定的不同元组产生冲突，而不是静默替换一条
   仍在活动的路由。
2. **Claim。** 宿主以有界等待领取最旧的排队消息（上限 1800 秒）。超时返回
   `claimed=false`；宿主自行决定是否再次订阅。claim 绝不启动、恢复或替换运行时。
3. **Complete。** 完成回执引用精确的 claim 与稳定的完成 id，随后回读通过既有的 Chat
   turn 与回复路径返回响应。迟到或重复的完成被拒绝，不会被当作新工作重放。
4. **Close。** 关闭绑定移除路由，但不删除 LoopX 工作状态。

托管绑定：宿主创建会话，启动并监督运行时，通过有界 Turn 推进工作，独立验证每个结果，
提交被接受的状态，并在断连时先协调进程状态再提供恢复或重启。宿主不得为了显得健康而
挂接到无关会话。

两种模式的失败关闭规则：

- 对活跃绑定启动第二个执行器会被类型化错误拒绝；
- 绑定未声明的投递能力会被拒绝，绝不静默改派；
- 模糊或缺失的完成回执使该 turn 保持未决，需要协调而非重放；
- 未知的模式、能力键或回执版本被拒绝，而不是被解释；
- 任何超时、缺失响应或过期回读都不意味着某个效果没有发生。

### 宿主接入契约

一个宿主（包括可选预览宿主）在把会话绑定到 LoopX 之前必须满足以下全部要求：

1. 为每个绑定声明一个模式，并随绑定持久化、可回读；
2. 在绑定前注册其 Agent（或复用精确注册的 `agent_id`），并使用与 Agent 身份区分的
   `executor_endpoint_id`；
3. 绝不为已绑定的 Agent 运行第二个执行器，包括在重启、崩溃或人工重新启动之后；
4. 把外部输入路由到 LoopX 有序会话队列或其他已声明入口路径，而不是复制 Todo 权威的
   私有 inbox；
5. 把对话输出视为提案，直到用户确认且 LoopX 写入状态；
6. 在记录日志或用作进展之前独立验证结果；
7. 保持宿主本地存储在 Goal、Todo、quota、monitor 与生命周期状态上非权威；
8. 暴露公开安全的类型化回读：模式、能力、状态与稳定错误码，不含宿主会话 id、
   transcript、凭据或路径；
9. 保持 opt-in，并可在不触发 LoopX schema 迁移的情况下卸载。

无法满足这些要求的宿主仍然可以读取 LoopX 状态，但不得声称持有一个正在执行的会话
绑定。

### 能力与投递模式的正交性

经常被混淆的六个轴，每个轴有唯一属主：

| 轴 | 取值 | 属主 |
|---|---|---|
| 执行模式 | `managed_runtime`、`attached_host` | 本 RFC |
| 续跑 owner（提案） | LoopX Turn driver、原生 Goal runtime、同会话 host driver | 本 RFC；按选定 profile 验证，不从 provider 或部署位置推断 |
| 传输 | web chat、Lark、CLI | 桌面执行前端 RFC；传输绝不改变模式 |
| 事件源 | 群消息、文档评论、monitor 观察、入站文件 | 连接器与协作契约 |
| 入口/投递模式 | `live_steering`、`session_queue`、`async_inbox` | 桌面执行前端 RFC，按绑定门控 |
| 宿主模式选择 | `visible_tui`、`isolated_headless_turn`、`im_gateway`、`shell_service`、`hybrid_handoff` | [宿主模式规划 v0](../../reference/protocols/host-mode-plan-v0.md) |

新增或移除传输、事件源不会创建、替换或迁移会话。改变模式是独立的显式操作，拥有自己的
回执。选择宿主模式不授权执行模式。

## 6. 跨 RFC 与协议的归属地图

在改动会话、执行或协作行为之前先读这张表。如果变更触及某一行的所属边界，请更新那份
文档，而不是用一条相互竞争的规则扩展本 RFC。

| 文档 | 拥有的内容 | 与本 RFC 的关系 |
|---|---|---|
| [桌面执行前端](desktop-execution-frontends-v0.zh-CN.md) | 桌面端产品形态、Mode A/Mode B 产品对比、Web/Lark 收敛、连接器与 Bot 入口模型、可选 computer use、桌面端交付切片 | 本 RFC 使用的模式对比来源。本 RFC 抽取与属主无关的会话执行契约；那份 RFC 保留前端产品流程，并应在模式接入上引用本 RFC。 |
| [单属主本地守护进程](single-owner-local-daemon-v0.md) | 服务 profile 身份、就绪、受监督组合、生命周期回执、迁移 | 拥有 LoopX 组件的*进程与服务*归属。托管宿主只有在那份 RFC 下才能作为受监督服务运行；本 RFC 不创建守护进程、监听器或端点。 |
| [有能力的管家与语义交接](capable-manager-semantic-handoff-v0.zh-CN.md) | 管家能力、语义交接、会话与产品延续（§5.7）、延续路径选择、结果返回 | 拥有跨会话延续：同会话恢复、同 Agent 替换、跨 Agent 接管。本 RFC 拥有模式标签与绑定归属；交接不得隐式改变模式。 |
| [管家运行时 profile](manager-runtime-profile-v0.zh-CN.md) | 管家的有效运行时 profile：沙箱、提示词、托管工作区指令、配置修订与回读一致性 | 托管模式的 profile 细节。本 RFC 要求模式显式且可回读；profile 内容与其批准仍在那份文档。 |
| [运行时选型：DSH 与 Pi](harness-selection-dsh-pi-v0.zh-CN.md) | 托管运行时与观察通道的证据化选型与资格认定 | 拥有*哪个*运行时具备托管执行资格。本 RFC 只拥有"托管会话由 LoopX 拥有、以及它如何被绑定"这一事实；不提升任何运行时。 |
| [显式延续（Stage A）](cross-session-memory-substrate-v0.zh-CN.md) | 已交付的延续 note CLI、其同宿主已注册 Agent 限制、以及受修订保护的归属采纳 | 延续是与模式变更不同的操作。其限制保持不变；延续 note 不是会话模式迁移。 |
| [共享 Goal 权威](shared-goal-authority-state-provider-v0.zh-CN.md) | 共享 Goal 的跨宿主权威、claim、lease、fence 与 provider 资格认定 | 拥有跨宿主工作归属。本 RFC 的"每个绑定一个活跃执行器"是绑定内的局部串行化，不产生第二个 claim 或 lease 权威。 |
| [TypeScript 控制面迁移](typescript-control-plane-migration-v0.zh-CN.md) | 迁移期间由哪个面拥有类型化状态机、effect 与结算规则 | 成为机器强制的模式转换属于那个类型化边界。Python 适配器可以桥接契约，但不得分叉会话模式权威。 |
| [Goal Channel 协作](goal-channel-collaboration-v0.zh-CN.md) | Goal 绑定的协作面、通道投递与通知 | 拥有协作投递到哪里。本 RFC 拥有投递所针对的会话绑定，以及该绑定能否接受它。 |
| [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md) | 有界受治理 Turn：decide、execute、validate、write back、spend once | 托管宿主使用的执行单元，也是返回受治理结果的挂接宿主使用的单元。本 RFC 不扩展 Turn 契约。 |
| [Turn 循环控制器](../../reference/protocols/turn-loop-controller-v0.md) | 继续 Turn 的纯处置转换及其预算语义 | 判断是否还有下一个 Turn 有资格执行。它不拥有会话、不启动进程、不调度工作。 |
| [宿主模式规划](../../reference/protocols/host-mode-plan-v0.md) | 依据意图与已声明能力做面向用户的宿主模式选择，以及它打印的预览命令 | 这是另一个轴：工作如何推进、经由哪个连接器。它不是模式权威；被选中的宿主仍须按本 RFC 声明其会话模式。 |
| [会话运行时投影](../../reference/protocols/session-runtime-loopx-projection-v0.md) | 把外部运行时会话只读投影进 LoopX，且不复制私有 trace | 与 `attached_host` 一致：运行时拥有 transcript，LoopX 拥有自己的状态。本 RFC 不新增投影字段。 |
| [会话运行时受控回写](../../reference/protocols/session-runtime-controlled-writeback-v0.md) | 在投影存在之后，把 LoopX 决策以紧凑形式回写到外部运行时元数据 | 拥有运行时元数据回写。会话绑定不是 Goal 事实的回写通道。 |
| [Codex CLI TUI loop](../../product/runtimes/codex-cli/codex-cli-tui-loop.md) | 面向某个可见运行时的宿主侧循环指引，包括会话挂接自动化与恢复选项 | 某个宿主的实现指引。它不携带模式权威，也不定义接入。 |
| [挂接 Agent 会话 broker](../../integrations/attached-agent-session-broker.md) 与 [worker-bridge 安装契约](../../integrations/worker-bridge-install-contract.md) | 已交付 broker CLI 的语义、安装与运维契约 | 本 RFC 第 4 节审计的挂接绑定的运维参考。 |

有两点值得显式说明：

- 桌面 RFC 仍然是其前端的产品级提案。本 RFC 不取代它；关于桌面界面流程、连接器或
  computer use 的冲突在那份文档解决。
- 管家、延续与交接 RFC 拥有*连续性*。本 RFC 拥有*身份*：哪个会话、以哪种模式、在哪个
  Agent 下，是当前执行器。延续操作消费该绑定；它们不重新定义它。

## 7. 备选方案与设计选择

| 方案 | 收益 | 代价 / 结论 |
|---|---|---|
| 从环境、宿主探测或对话推断模式 | 不需要显式绑定字段，设置更少 | 面对活跃外部宿主时含糊不清，允许静默第二执行器，无法诚实投影。拒绝。 |
| 单一隐式通用宿主适配器 | 对集成方概念更少 | 掩盖了导致故障的归属差异，并把宿主特有生命周期塞进一个虚假的通用层。拒绝。 |
| 宿主断连时自动把 `attached_host` 迁移为 `managed_runtime` | 看起来工作没有停下 | 在没有操作者意图的情况下创建第二个执行器和新的会话。拒绝；宿主可以报告 stale，并提供显式、有回执的替换操作。 |
| 把对话当作充分回写 | 对预览宿主更简单 | 破坏验证与回执契约，让聊天文本成为权威。拒绝。 |
| 显式模式加按绑定的能力声明 | 失败关闭、可投影、可在不新增权威的前提下接入新宿主 | 要求宿主声明并回读状态，并要求类型化的不可用处理。采纳。 |

## 8. 安全、隐私与兼容性

- **默认关闭与关闭态一致性。** 既有托管会话保持其行为。从未绑定外部宿主的安装看不到
  新增的必需端点、进程或字段。不具备模式契约的宿主被视为读者，而不是执行器。
- **授权。** 会话模式不是权限授予。它不授予 Goal、Todo、quota、gate、evidence、
  文件系统、provider 或消息能力，也不削弱操作者已有的授予。
- **公私边界。** 公开回读只携带模式、能力、状态与稳定错误码。宿主会话 id、transcript、
  消息体、提示词、凭据、provider 负载与本地路径不进入公开投影，也不进入已提交证据。
- **历史行与混合版本读取方。** 没有模式的历史行按 `managed_runtime` 读取。未知模式
  取值、能力键与回执版本被拒绝。无法表达模式的客户端不得绑定会话。
- **防脑裂。** 按绑定串行化加显式模式，防止两个执行器推进同一个 Agent。跨宿主协调
  仍属于共享权威契约；本 RFC 不新增并行 fence。
- **失败关闭取向。** 不可用投递、模糊回执与未知契约都失败关闭。有界等待超时后报告
  不可用并把控制权交还操作者或宿主；它绝不触发回退执行器。

## 9. 迁移与回滚

接入新宿主：

1. 声明模式、Agent 作用域、执行器端点与能力集合，并证明可回读；
2. 在重启、崩溃与并发启动下证明单执行器行为；
3. 证明仅靠对话输出不会改变 Goal 或 Todo 状态；
4. 证明宿主本地状态不是 Goal、Todo、quota、monitor 或生命周期状态的竞争权威；
5. 以 opt-in 方式安装，并需要显式启动动作。

挂接宿主的回滚：关闭绑定、停止宿主，并验证路由已消失。托管宿主的回滚：停止受监督
进程，并在另一个执行器启动前协调任何在途 turn。两种回滚都保留 LoopX 工作状态；两者都
不需要 LoopX schema 迁移，也都不删除已接受的工作。

不再剩余任何绑定时，可选预览宿主可以通过删除其自身包与私有数据目录来卸载。本 RFC
不授权自动模式迁移、自动会话替换，也不授权把宿主本地记录静默采纳为 LoopX 状态。

## 10. 验证与验收

标记为 shipped 的行是当前证据。标记为 unverified 的行是要求的未来证据，不得报告为
通过。

| 主张 | 测试 / 证据 | 要求结果 | 边界 |
|---|---|---|---|
| 模式显式且持久化 | 创建托管与挂接绑定，再回读投影 | 精确返回模式与宿主面；未知模式被拒绝 | 审计基线上已交付 |
| 无隐式模式变更 | 重连、宿主重启后重连、重复绑定同一元组 | 模式与绑定身份不变；不同元组冲突 | broker 接入路径上已交付 |
| 每个绑定一个执行器 | 并发 claim、重复启动、活跃 turn 期间重启 | 只有一个活跃执行器；重复者以类型化错误失败关闭 | 部分交付；托管重启路径需要显式行 |
| 能力失败关闭 | 请求绑定未声明的投递能力 | 类型化不可用，如 `attached_session_live_steering_unavailable`；无回退执行器 | `live_steering` 上已交付 |
| 有界宿主等待 | 有/无可用消息时 claim，以及超过等待上限 | 到界返回 `claimed=false`；绝不启动运行时 | 审计基线上已交付 |
| 公开安全回读 | 检查投影与回执中的宿主 id、transcript、路径 | 不含宿主会话 id、transcript、消息体、凭据或路径 | Chat 投影上已交付 |
| 对话不是回执 | 宿主回复一条表示同意的消息且不做回写 | 无 Goal 或 Todo 转换；投影显示结果未决 | 对新宿主未验证 |
| 宿主本地状态非权威 | 检查宿主存储中的 Goal、Todo、quota、monitor、生命周期副本 | 无竞争状态；日志是可重放结果而非权威 | 对新宿主未验证 |
| 重启与崩溃下的接入 | 在已绑定会话期间杀掉宿主 | 无孤儿执行器；替换绑定只有一个属主且模式正确 | 对新宿主未验证 |
| 关闭态一致性 | 在没有任何挂接绑定、也没有预览宿主的情况下运行 | 既有托管行为、路由与默认值不变 | 对新宿主未验证 |

确定性的包测试不足以支撑接入。新宿主至少需要一条真实宿主行：一个真实进程、一次真实
绑定、一次真实重启。

续跑扩展还要求：已安装的本地/云端组合使用同一准入/结果/独立验证合同；普通 managed
worker 请求并采用另一 worker 的产物；driver 切换竞态拒绝旧执行器。普通等待、未决
工具、预算耗尽、native Goal defer/终态处理分别验证。注册、HTTP ACK 或固定 phase
脚本都不能满足这些尚未资格化的项目；每个现有 profile 和入口保持 feature-off 行为。

## 11. 运维契约

- **可观测性。** 会话回读暴露模式、宿主面、执行器端点、能力、状态、活跃 turn 与稳定
  错误码。claim 与完成无需可用界面即可单独检查。
- **类型化失败。** 稳定原因包括：缺失或未知模式、缺失宿主面、不可用的投递能力、
  重复绑定冲突、过期 claim、被拒绝的重复完成。
- **边界。** claim 等待有界，绝不保持运行时敞开。宿主启动、重启与协调尝试有界并带
  退避，耗尽后留下可观测的失败状态，而不是无界重试循环。
- **操作者动作。** 绑定 stale 时，操作者要么在同一模式下恢复相同的宿主会话，要么执行
  显式、有回执的替换。任何操作者动作都不得静默改变模式或删除工作。
- **容量。** 每个绑定一条有序队列；队列深度与过期有界并上报。队列满或过期在入口侧
  失败关闭，绝不把工作转移到另一个执行器。
- **恢复。** 被中断的执行器 turn 在重试前先被协调。经过验证的结果在生命周期回写之前
  先被记录。

### 长程 managed 执行路线（2026-09-16）

[统一路线](loopx-overall-roadmap-v0.zh-CN.md) R2 先验一个管家与 2–3 个真实 managed worker 的跨 Turn 闭环；R6 再验本地/云端同 authority，R7 才扩大活跃规模。本 RFC M1–M4 继续拥有宿主接入，不用团队计划的 `ready` 取代 binding、资格、claim/lease 或实际进程读回。

目前 DSH 管家 Chat 是单段、只读、无跨 turn 宿主会话；`turn run-once` 是另一条有界执行路径。下一切片要证明 successor wake、取消/停止、崩溃恢复及旧执行器返回 fence，经 packaged frontend/CLI/Lark 回读真实状态。不能仅增加一个 executor 名称、启动一个片段或绑定若干 Agent 就声称持续 managed 模式完成。attached host 不因掉线而改为 managed，未验收宿主保持原资格边界。

显式启用的[本地委派接口](../../reference/local-delegation.md)已为有界 Turn 提供持久
操作，涵盖成员继续委派的授权及 TS 任务验收。Ark 进程中断实验在云端等待本地工具后，
以原 Session/输入接回，不重发已确认副作用、不重置期限。这验证本地执行恢复，不代表
successor wake、attached 接管或完整团队取消。Provider 文件配置保留现有模型/工具
边界；上文通用 Agent 创建与模型发现提案仍是独立后续范围。


## 12. 规范性交付计划

| 里程碑 | 交付行为 | 进入门槛 | 退出证据 | 回滚 |
|---|---|---|---|---|
| M0 | 本 RFC：规范化模式契约、接入要求与归属地图 | 审计基线与维护者评审 | 合并的 RFC；不声称运行时变更 | 文档回退 |
| M1 | Step 1 最小原型：一个可选本地宿主，声明其模式、绑定一个 Agent、保持 LoopX 权威，并以独立验证与对话式建档驱动有界执行 | M0 已合并；候选原型上未决的进程清理评审发现，通过复用既有的有界进程运行时 helper 解决 | 接入行：模式回读、单执行器、对话不是回执、宿主本地状态非权威、重启行为、关闭态一致性 | 移除可选宿主包与其私有数据目录；无 LoopX schema 迁移 |
| M2 | 在真实外部宿主会话上实现挂接宿主的适配器一致性，含能力门控投递与类型化不可用 | M0 已合并；broker 契约被接受 | 真实宿主的 bind、claim、complete、stale 与重复行 | 关闭绑定并停止宿主 |
| M3 | 跨前端的模式感知投影一致性，无模式推断、无第二执行器 | M0-M2 证据 | 跨前端回读行；重连下无隐式模式变更 | 回退投影变更；绑定不变 |
| M4 | 对原型宿主面做出提升决策，或明确决定保持为可选预览 | M1 证据与产品评审 | 记录其覆盖面与受众的决策 | 保留预览边界 |

M0 与 M1 刻意保持很小。M1 必须绑定真实 Agent 与真实进程；它不得新增未被调用的 schema
构造器、第二调度器或新的运行时权威。M2 不得改变 Goal、Todo、quota 或 claim 语义。
M3 不得成为第二个模式事实来源。

## 13. 待决事项

1. **会话轮换。** 哪个面向操作者的操作可以在保留可审计对话边界的前提下替换或轮换
   Agent 的工作会话，哪个回执可以证明它？负责人：维护者。依赖 M2 与管家 RFC 选定的
   延续路径。
2. **稳态能力策略。** 在队列式入口路径交付后，`live_steering` 是否仍是按绑定的能力，
   以及什么证据可以提升它？负责人：维护者。依赖 M3。
3. **队列存储。** 哪一份有界的属主本地存储承载有序会话队列，以及在什么条件下显式
   重新绑定可以跨被替换的宿主会话保留排队条目？负责人：存储属主。依赖 M2。
4. **预览宿主接入。** 可选预览宿主是否可以在满足全部接入行之前绑定执行会话；如果可以，
   哪些行对预览是强制的？建议：仅允许在模式回读、单执行器行为与"对话不是回执"三项
   具备时绑定；其余行保留为提升门槛。负责人：维护者。
5. **提升标准。** 哪些证据可以把可选预览宿主变为受支持的前端面，提升后由谁拥有其
   生命周期？负责人：维护者。依赖 M1 与 M4。
6. **模式感知投影一致性。** 每个前端都应向操作者显示执行模式，还是只有能改变它的面
   才显示？负责人：前端属主。依赖 M3。
7. **契约落位。** 模式转换是否应在类型化控制面中成为机器强制，以及届时哪些 Python 面
   只保留适配器角色？负责人：控制面属主。依赖
   [TypeScript 迁移 RFC](typescript-control-plane-migration-v0.zh-CN.md)。

## 附录 A：执行台账（非规范性）

### 2026-09-15 - 契约规范化为 RFC

- **基线：** `6c3da75ca`
- **交付：** 此前只描述在桌面前端提案内部的挂接/托管会话归属决策，被重述为一份独立
  契约，包含接入要求、跨 RFC 与协议的归属地图，以及以最小宿主原型为 Step 1 的交付
  计划。
- **证据：** 第 4 节在指定基线上的源码审计；其中列出的聚焦测试。
- **已知缺口：** 面向新宿主的每一条接入行都未验证；没有跨宿主接入、轮换或提升决策被
  记录。
- **对规范设计的影响：** 确立第 1-6 节；不改变任何运行时行为。

## 附录 B：决策日志

| 日期 | 决策 | 负责人 / 批准 | 备选 | 变更的规范章节 |
|---|---|---|---|---|
| 2026-09-15 | 会话执行模式是显式、持久化、按绑定的契约；桌面 RFC 保留产品流程，延续类 RFC 保留延续路径 | 仓库属主，经 RFC 评审与合并 | 隐式模式推断；通用适配器；自动迁移到托管 | 第 1-6 节 |

没有其他决策获得批准。第 13 节中的建议仍是提案。

## 附录 C：证据登记

| 证据 id | 主张 | 基线 / 环境 | 产物或命令 | 结果 | 隐私 / 有效性边界 |
|---|---|---|---|---|---|
| E1 | 模式词表是封闭的，且挂接绑定要求宿主面 | `6c3da75ca`，源码审计 | `loopx/chat_store.py` 会话创建与模式校验 | pass（源码） | 仅源码检查；不是真实宿主资格认定 |
| E2 | 公开会话投影隐藏宿主会话身份，并对托管会话置空挂接能力 | `6c3da75ca`，源码审计 | `loopx/chat_store.py` 公开会话投影 | pass（源码） | 不能证明宿主本地日志中不存在宿主 id |
| E3 | 挂接 broker 有界等待 claim，且绝不启动运行时 | `6c3da75ca`，源码审计 | `loopx/attached_session.py` 的 bind、claim、complete | pass（源码） | 边界作为常量评审；无长稳证据 |
| E4 | 挂接会话绝不启动托管适配器，并以类型化错误失败关闭 | `6c3da75ca`，聚焦测试 | `tests/test_chat_codex_home.py::test_attached_session_uses_existing_host_not_managed_adapter`、`loopx/chat_runtime.py` | pass（聚焦测试） | 覆盖所述围栏，不覆盖每条投递路径 |
| E5 | bind、claim、complete 可通过 CLI 触达 | `6c3da75ca`，聚焦测试 | `tests/test_attached_session_cli.py`、`loopx/cli_commands/worker_bridge.py` | pass（聚焦测试） | 合成宿主夹具，不是真实外部宿主 |
| E6 | 桌面前端提案已包含 Mode A/Mode B 对比及其非目标 | `6c3da75ca`，文档审计 | `docs/architecture/rfcs/desktop-execution-frontends-v0.md` | pass（文档） | 是提案，不是已交付产品行为 |
| E7 | 一个可选本地宿主原型提出带有对话式建档的宿主自持执行面 | 公开 PR #4376，评审期 head | 该 PR 上的公开评审发现 | proposed，未被接受 | 打开的 PR 是候选，不是接入证据 |

## 附录 D：被拒绝或被取代的备选

- **由环境或能力探测隐式确定模式。** 拒绝：无法区分活跃外部宿主与陈旧宿主，并掩盖了
  产生重复执行器的归属差异。
- **断连时从挂接自动迁移到托管。** 拒绝：在没有操作者意图的情况下创建第二个会话与
  执行器。
- **一个隐藏模式的通用适配器。** 拒绝：宿主生命周期语义本就不同，虚假的通用层只会把
  含糊搬到别处，而不会消除它。
- **把对话当作已接受回执。** 拒绝：绕过验证与回写，并让 transcript 成为权威。
- **把宿主本地 Goal 或 Todo 镜像作为状态来源。** 拒绝：产生第二权威与操作者无法解决
  的分叉。

## 附录 E：事故与评审教训

- 绑定存在不等于执行器存在。促成这份契约的失败形态是"投影看起来健康、背后却没有
  进程"，因此回读必须区分模式、能力、状态与错误码，而不是只报告一个成功标志。
- 预览宿主也是宿主。只评审它的用户价值是不够的；评审还必须决定它可以拥有哪些模式、
  绑定与存储，因为那决定了 LoopX 是否仍是唯一的工作权威。
- 当进程监督细节可能留下活跃写入者时，它就是契约相关的。一个有界执行 helper 若在
  报告超时之后仍留下存活子进程继续写入，那是第二执行器风险，而不只是清理缺陷。
