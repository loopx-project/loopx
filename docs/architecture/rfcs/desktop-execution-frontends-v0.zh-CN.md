# RFC：LoopX 桌面执行前端 v0

- 状态：Draft
- 决策边界：同时支持挂接到外部拥有的 Agent 会话，以及端到端由 LoopX 托管的桌面运行时
- 初始挂接运行时：Codex App / app-server
- 初始托管运行时：Pi 与 DeepSeek Harness（`dsh`）
- 托管 provider 选择：显式配置引导与用户意图约束，Agent 在已授权可用 profile 内自主分配；火山方舟 Agent Plan 保留为可选发行预设。

## 摘要

LoopX Desktop 应支持两种显式的执行前端模式：

1. **挂接 App 会话（Attached App Session）**。操作者把 LoopX Desktop 挂接到
   一个正在运行的 Codex App 或 app-server 会话。外部宿主继续拥有进程、对话、
   中断、恢复和执行循环的所有权。
2. **托管 Agent 运行时（Managed Agent Runtime）**。LoopX Desktop 启动并
   监督 Pi 或 DeepSeek Harness，选择显式的 provider 配置，并通过有界的
   `loopx_turn_v0` 事务推进工作。发行版可提供命名的火山方舟 Agent Plan 预设，
   操作者配置与明确意图约束 Agent 的自主分配；不把任何 provider 设为统一产品
   默认。运行时与 provider 契约保持可替换。

两种模式呈现相同的 LoopX Goal、Todo、gate、quota、evidence 和状态事实。
它们不共享进程所有权。前端绝不能从聊天散文推断模式切换，也不得静默启动
第二个执行器。

执行模式独立于外部连接器。Web Chat 和 Lark Bot 可以连接到同一个
Agent 拥有的工作会话；而 Lark 群消息或文档评论可以在该会话能够接收输入前，
使用显式有序队列或异步 inbox。一个 Goal 可以包含多个 Agent；每个 Agent 拥有
自己的工作会话绑定、至多一个活跃运行时会话，以及显式的连接器绑定，且不引入
manager Agent，也不把连接器硬编码到 Codex。

并非每个连接器都是对话传输。Lark 群既可以是实时传输，也可以是异步事件源。
文档正文是已登记的权威材料，而它的评论流是独立事件源，拥有自己的 cursor、
捕获策略、回复能力和确认状态。LoopX 不得把“拉取文档正文”等同于“观察文档评论”。

托管模式不要求宿主原生的 Goal 循环。桌面拥有的运行时监督器反复询问 LoopX
下一个有界 Turn 是否有资格执行，调用所选运行时适配器，验证其结果，并提交
被接受的状态。`loopx_turn_v0` 始终是一个事务，而不是第二个常驻调度器。

## 显式配置引导、用户意图与自主分配

这里细化 provider 默认策略，属于提案，不改变已发布运行时默认值，也不认证新
适配器。复用现有 machine/Goal capability editor、provider store、会话绑定与
调度准入，不新增 capability id、provider 实现或选路服务。

- **配置让选择可用。** 展示已探测安装与登录、支持工具、模型/账户、费用或费用
  未知、宿主在线条件和权限范围。发现不等于授权。命名预设是可选方案，不能覆盖
  已有明确配置。
- **用户明确意图约束选择。** 固定模型/账户、本地限定、预算和成员限制，按用户
  声明的作用域生效。偏好不自动变成硬锁。不能把某位用户的 Codex 偏好，或某个
  发行版的 Ark 预设，推广成全产品规则。未解决的明确约束冲突需要澄清。
- **Agent 在范围内自主分配。** 按任务适配性、工具权限、成本和观测到的可用性，
  选择可用模型/运行时、复用或请求 worker、重新分配后续工作。已授权的灵活资源池
  不要求逐次确认。选择由 Agent 作语义判断；typed owner 执行资格、预算、权限和
  会话 fence。

在设置与团队详情投影有效 runtime/model/profile、分配理由和就绪状态，派发时
重新验准入。锁定路径不可用就阻塞并给修复入口；灵活池中的成员不可用，可以选择
另一条合格路径并明确回读。不能借替代扩大数据外发、凭证、费用权限或支持工具。
活跃会话保留绑定与上下文所有权；已授权自主改派走现有显式重新绑定/continuation
契约，不静默迁移会话。新增付费资源和范围变化保留原有决策边界。

通过打包引导、Chat/管家和独立 CLI 验收：固定选择优先于预设；灵活分配不反复
审批；锁定路径失效时保持阻塞；预算耗尽或未授权回退不产生执行；重启保持有效
配置。可选 Lark 投影同一选择及自己的受众约束。用例通过前，这是分配设计而非
运行保证。见[近期发布路线](loopx-overall-roadmap-v0.zh-CN.md#近期本地-agent-产品与发布路线)。

## 问题

LoopX 已经拥有两个不同产品的零件：

- 一个控制平面内核，拥有权威的 Goal 和 Todo 状态、gates、quota、验证、
  回写和调度提示；
- 一个桌面前端和本地应用壳；
- 可见宿主集成，包括可选加入的 Pi Goal 扩展；
- 一个宿主中立的、有界的 Turn 协议；以及
- 一个可以通过 `dsh` 执行 Turn 的 DeepSeek Harness 适配器。

缺少的是一个被接受的桌面所有权模型，能够把这些零件连接起来，同时不把两个
有效的工作流压成一个。

一些用户已经有一个长期运行的 App 会话，带有宝贵的上下文和已安装的 LoopX
automation prompt。用新启动的 CLI 替换该会话会产生两段对话、改变运行时策略，
并且有风险让两个执行器推进同一个 Goal。

另一些用户想要一个完整的桌面产品：选择一个 Goal、配置 provider、启动 Agent、
与它对话、中断它、关闭应用，然后稍后恢复同一个工作会话。要求他们启动一个
单独的 App 或安装宿主原生的 Goal 循环，会破坏这种产品形态。

因此前端需要两种显式模式，共享同一个投影，并有独立的生命周期契约。

## 决策

LoopX Desktop 暴露一个带标签的执行前端：

```text
desktop_execution_frontend_v0 =
  attached_app_session
  | managed_agent_runtime
```

模式在创建前端绑定时被显式选择。它随绑定持久化，并在状态中可见。重连可以
恢复相同的模式和会话身份，但绝不能隐式改变模式。

对话传输是第二个正交标签，协作事件源是第三个：

```text
execution mode: attached_app_session | managed_agent_runtime
transport:      web_chat | lark_bot
event source:   lark_group_message | lark_document_comment | ...
ownership:      one Goal -> many Agents -> at most one active session per Agent
```

添加或移除传输或事件源不会创建、替换或迁移执行会话。改变执行模式是另一个
独立的显式操作。

### 公共不变量

两种模式都保持以下边界：

- **LoopX 拥有工作事实。** Goal、Todo、claim、gate、quota、evidence、已接受
  的进展和终态在 LoopX 中保持权威。
- **运行时拥有执行机制。** Codex App、Pi 或 `dsh` 拥有其模型/工具循环和不透明
  的上游会话状态。
- **Agent 拥有工作会话路由。** 一个 Goal 可以有多个 Agent。运行时、传输和
  事件源绑定通过显式的 Agent id 解析，绝不经由模糊的 Goal 级默认值。
- **provider 拥有推理。** provider 凭据、端点、模型可用性和原始负载不是
  LoopX 的任务状态。
- **Desktop 拥有呈现和监督。** 它投影工作和运行时状态、路由用户输入，并且
  仅在托管模式下启动和监督运行时进程。
- **一个绑定至多有一个活跃执行器。** 入口被串行化，重复启动失败关闭。
- **对话不是写入回执。** 实质状态变更需要相应的 LoopX 验证和回写契约。

### 模式对比

| 边界 | 挂接 App 会话 | 托管 Agent 运行时 |
|---|---|---|
| 进程所有者 | 外部 App / app-server | LoopX Desktop 运行时监督器 |
| 初始运行时 | Codex App / app-server | Pi 或 `dsh` |
| 会话创建 | 在挂接前 | 由 Desktop 创建 |
| 对话传输 | 已有 app-server 连接 | 托管运行时适配器 |
| 执行循环驱动 | 已有 automation prompt 或可见宿主循环 | Desktop 外层循环加有界 LoopX Turn |
| 是否要求宿主原生 Goal | 允许，但不由 Desktop 强加 | 否 |
| Provider 配置 | 继承自外部会话 | 显式托管 provider 配置 |
| 断连行为 | 报告 stale/disconnected | 协调进程并提供恢复/重启 |
| 模式回退 | 绝不自动 | 绝不挂接到无关会话 |

## Agent 级 Web 与 Lark 收敛

短期协作产品不是一个独立的状态 Bot。它是 Agent 真实工作会话的第二个前端
传输：

```text
LoopX Goal
  -> Agent A
       -> working session A (attached or managed)
            -> Web Chat
            -> Lark Bot connection A
  -> Agent B
       -> working session B (attached or managed)
            -> Web Chat
            -> Lark Bot connection B
```

在 v0 中，每个 Agent 至多有一个活跃的 `lark_bot` 连接。这是一个逻辑上的
Agent 到连接绑定；它不要求每个 Agent 都有唯一的 Lark 应用凭据。如果本地
broker 保留显式的 Agent 与频道路由，一个 Bot 应用可以为多个连接服务。

### 一个有序的工作对话

项目 coordinator 的基线入口就是现有的 **Goal → 对话**。已注册 peer 也可以
承担这份职责；两种选择都不另建 coordinator 对话，也不改变管家的跨 Goal
职责。[Codex 显式续跑](../../reference/goal-chat-continuation.md)复用输入框、
旁的开启/暂停/恢复、流式运行与原本地历史，接入共享委派和独立验收的成员
返回。queue/inbox/steer 保留不同回执；首次配置选择现有执行绑定，不从注册
推导授权。Lark、其他主力驱动等价和无人值守运行仍需分别资格化。

在实时操控和队列会话模式下，Web 与 Lark 消息进入所选 Agent 会话的同一个
串行化入口流。每条消息记录 public-safe 的传输元数据，例如 `origin=web` 或
`origin=lark`，但 origin 不会选择不同的 Agent、对话历史、执行器或 LoopX
状态机。

会话路由器在投递给运行时之前分配顺序。同时到达的 Web 与 Lark 消息可以等待、
通过显式控制动作中断，或按会话策略失败关闭；它们绝不能产生两个并发的 Agent
尝试。响应可以根据连接策略投影到两个 surface，同时保留一个规范序列。

异步 inbox 事件则不同：在被选中的 Agent 排空并解释之前，它仍是 owner-private
的外部输入。只有被接受的、面向 Agent 的消息或由此产生的持久效果才加入
工作会话序列。仅做 provider 采集不会创建对话历史、任务权威、Turn 或 quota
消耗。

### Agent 绑定，而非 Goal 级或运行时专属绑定

Lark 连接绑定到 Goal 内的具体 Agent。如果 Goal 有多个 Agent 而连接没有指明
其中一个，路由失败关闭。Bot 直接与该工作 Agent 对话；它不会先请 manager
Agent 分类或转发消息。绑定不硬编码到 Codex：该 Agent 的执行会话今天可以是
挂接的 Codex App，以后也可以是托管的 Pi/`dsh` 会话。

### 产品投影

所选 Agent 的 Goal Chat 头部应显示有界的连接状态，例如 `Lark Bot · connected`、
`listening`、`stale` 或 `disconnected`，并提供直接的管理入口。管理视图拥有
显式的 attach、detach、频道选择、新鲜度和重连动作。

`loopx_collaboration_status_v0` 可以提供有用的只读卡片，但它不是核心抽象。
核心对象是 Agent 级的前端连接和收敛后的工作会话。

## Agent 级外部连接器模型

Lark 群入口和 Lark 文档评论是一个 provider-neutral 连接器边界的两个实例。
连接器把外部源绑定到一个已登记的 Agent，并且只宣称它能实际执行的操作：

```text
agent_external_connector_v0 = {
  goal_ref,
  agent_ref,
  provider_kind,
  source_kind,
  source_ref,             // opaque owner-local reference
  capture_policy,
  ingress_policy,
  response_policy,
  cursor_ref,
  lifecycle,
  capabilities[]
}
```

同一个 provider 可以暴露多种 source kind。例如，Lark 群源可以宣称实时投递、
历史追补、thread 回复和 ACK；而文档评论源可以宣称增量列举、锚点与回复链
读回、评论回复和已解决状态观察。缺失的能力保持不可用；LoopX 不会通过抓取
无关 surface 来模拟它们。

Connector capability 还可以暴露类型化的 `permission_requirements`，包含 provider
身份、精确 scope、发布要求，以及绑定所选 App 的官方修复入口。这些事实由
provider 扩展拥有；LoopX 内核只负责渲染类型化指引。实时接收、响应写入和历史
追补是彼此独立的能力，不得压缩成一个笼统的“消息权限”标志。

### 权威材料与协作事件

持久文档与其评论具有不同的权威语义：

- 文档正文被登记为 Goal 权威材料，带有新鲜度、修订、所有者状态和冲突策略；
- 评论是面向 Agent 的 owner-private 外部输入，它本身不是被接受的需求、Todo
  变更或仓库事实；以及
- 纳入一条评论需要一个显式的持久效果，例如 Todo 更新、被接受的设计修订、
  不跟进理由或 owner gate。

读取正文不会推进评论 cursor。列举评论不会让文档变得权威。与已接受状态冲突的
评论被记录为待决决策或证据缺口，而不是静默改变 Goal 事实。

### 捕获、重放与确认

每个事件源连接器拥有稳定的 provider 事件 id、增量 cursor 或等价检查点、
有界的追补策略和幂等键。实时订阅和历史追补进入同一个去重后的 inbox，因此
在挂接前或停机期间创建的事件不会静默丢失。源可以按 mention、作者、文档、
评论状态、锚点或已配置的源范围过滤，而不改变其投递模式。

Agent 按以下顺序处理一个被接受的事件：

```text
capture and deduplicate
  -> mark processing
  -> read fresh Goal and authority state
  -> record durable effect or explicit no-follow-up rationale
  -> send an optional response through a declared Connector capability
  -> verify provider readback
  -> ACK and advance the source cursor
```

任何 ACK 或 cursor 推进都不得先于持久效果和必需的已验证响应。崩溃会幂等地
重放同一事件。私有正文、作者、provider id、源引用和评论文本保留在
owner-local inbox 存储中；状态和 quota 只看到无内容的紧迫性。

### 投递到工作 Agent

连接器捕获与 Agent 投递保持正交。实时群消息可以操控当前工作会话、在其有序
队列中等待，或唤醒异步 Agent inbox。文档评论通常通过 `async_inbox` 进入，
但当显式策略允许时，同一事件也可以提交到已验证的实时会话。在所有情况下，
它都指向已有绑定 Agent，绝不静默启动影子 manager 或全新对话。

### 短期 Goal Channel 桥

现有 Goal Channel 传输可以提供第一条 Lark 投递路径，前提是其 Goal 级连接被
细化为显式目标 Agent，并路由到该 Agent 已有有序会话。这个桥是增量实现路径，
不是保留第二个仅 IM 对话生命周期的许可。

如果本 RFC 被接受，它将取代 Goal Channel 草案中“一个 Goal 对应一个 Lark
绑定”的交互式聊天约束。Goal 级 Kanban、生命周期通知和共享协作工件可以保持
Goal 级；入站工作对话是 Agent 级的。

<a id="agent-scoped-bot-ingress-modes"></a>

## Agent 级 Bot 入口模式

Agent 到 Bot 的连接与 peer 协作需要同样的三种显式入口语义。用户侧简称
**inbox**、**queue**、**steer**，保留下述现有词汇。它们表达同一已绑定 Agent 的
投递意图，不是三个 Agent 或自然语言分类器。本提案将共同策略扩展到 peer 入口，
不因重命名输入就声称新增 API 或改变已有 adapter：

```text
agent_bot_ingress_mode_v0 =
  live_steering
  | session_queue
  | async_inbox
```

三种策略解决不同的可用性条件：

| 模式 | 投递目标 | 可用性模型 | 持久边界 |
|---|---|---|---|
| `live_steering` | 已绑定工作会话中指定的当前执行 | 宿主能在声明的安全点采用输入 | 现有会话/事件存储及消费回执；无第二执行器 |
| `session_queue` | 同一已绑定会话中的后续工作输入 | 当前工作结束或明确交还执行权后再投递 | 按 Agent 与会话键控的 owner-local 持久有序入口队列 |
| `async_inbox` | 显式排空后的下一个合格 LoopX Agent Turn | 无需 Agent 进程保持存活 | 现有 provider 拥有的事件 inbox 加无内容 quota 紧迫性 |

这细化了此前 queue 的“下次接受输入”表述：把 pending 输入合入当前工作的宿主，
并不因此实现拟议 queue 语义。变更必须显式资格化，在 opt-in 实现和兼容测试通过前
保持旧 profile 行为。

沿现有入口身份和接收者范围持久化请求模式、允许的 fallback 和实际投递处置。读回
区分耐久收件、排队派发、宿主消费和 steer 采用；工作采用/验收仍属于 collaboration/work
owner。模型正文或 HTTP 成功不是消费回执；未知能力明确失败。前端、CLI、Lark 在
原工作/对话面展示实际模式、等待原因及结果，不另造一块团队看板。

### 投递意图不决定唤醒策略

Mode 决定输入可以在哪里被消费；binding 已有的续跑 owner 决定是否接纳下一次
执行机会。以下提议矩阵用于资格化 adapter，不增加第四种入口模式：

| 接收方状态 | 要求行为 |
| --- | --- |
| 活跃 turn 或工具未决 | Inbox 等待显式 drain，queue 等待后续 turn；steer 指向精确活跃 generation 和已声明的安全输入边界。收件成功不能证明已提交的模型/工具请求被抢占。 |
| 空闲或 turn 完成 | 保存符合范围的 inbox/queue 输入；只有配置的续跑 owner 在范围/预算检查后才可接纳新 turn，没有该策略就显示待处理。无活跃目标时 steer 不可用。 |
| 正在收尾或已中断 | 保留迟到输入/结果身份，不重新打开收尾 turn；收尾后重查。通知不能撤销显式中断，恢复遵循已有 owner 和暂停策略。 |
| 未加载或断线 | 保存成功不证明 session 在线；仅经资格化的 binding 路径恢复，重验范围与 generation，不支持恢复时保留可行动的待处理/不可用观察。 |

排队不是启动 turn 的承诺，provider trigger 标志也不是 LoopX 准入。多条已收件
消息可以进入同一合格 turn，但独立工作请求仍保留各自身份和返回义务；这些关系
由[交接契约](capable-manager-semantic-handoff-v0.zh-CN.md#团队中的请求身份与结果路由)
拥有，不能把一条传输回执当作 join。

分别投影 ingress 收件回执、实际执行/唤醒观察和工作结果/验收；不能把“消息已存”
显示成“Agent 正在工作”，也不能把唤醒通知显示成“结果已收到”。通知丢失后，保存的
输入/结果仍须可经读回发现；重放或重连须按 ingress/result 身份去重应用，不能启动第二个执行器。在已有
修订输入 fixture 中增加无唤醒策略的空闲输入、收尾竞态、通知合并，以及结果提交到
通知之间重启。这是设计要求，每种宿主仍需独立资格化。

### 捕获、入口与回复正交

provider 选择与 Agent 投递不得复用同一个过载标志。初始 Lark 群形态是：

```text
capture_scope: mentions | configured_chat_all
ingress_mode: live_steering | session_queue | async_inbox
reply_mode: source_thread | topic_reply | configured_mirror
```

`capture_scope` 回答哪些 provider 事件合格。`ingress_mode` 回答一个合格事件
如何到达 Agent。`reply_mode` 回答已验证响应投递到哪里。现有
`incoming_mode=mentions|all` 只表达捕获范围；它不是会话挂接的证据。

持久 Inbox 范围必须与实际 provider 路由范围一致。即使启用了精确源消息回复，
`addressed_only` 流也不得投影成 `thread_complete`。`configured_chat_all` 仍是 owner
的显式选择：它会为领域解释保存已配置会话，但只有 typed question、mention 或
已验证的 Bot reply 才会激活 `reply_due`。

mention 准入同时绑定已验证 provider profile 返回的 App id 与 Bot open id；渲染后
的 display name 只是兼容信号，不能成为唯一身份依据。每个被拒绝的 provider event
都要在 listener health 中保留 `not_addressed`、`topic_mismatch` 等无内容决策原因，
让“已经看到但未持久化”的事件不再隐藏在一个裸 `ignored` 状态后面。

回退是显式的，默认失败关闭。`live_steering` 连接在会话不可用时可以选择加入
`session_queue` 或 `async_inbox`，但不得静默启动另一个运行时，也不得把同一
事件写入多个模式。所选模式、回退决策和去重键产生一个无内容的入口回执。

### 实时操控

`live_steering` 提交到已验证的 Agent 工作会话绑定。它共享 Web 入口串行器、
上游恢复身份、中断策略、工作区、运行时、信任和能力边界。如果该绑定陈旧、
模糊、终态或属于另一个 Agent，投递失败关闭。

操控是传输，不是任务权威。当前会话可以消费只读输入而不领取新工作。实质效果仍然需要与
所采用执行模式相称的最新 LoopX 决策、验证、回写和结算。

Steer 面向当前执行代际及其下一个支持的安全输入点，不等于 interrupt/restart。
外部工具未返回时，宿主可以耐久接收 pending correction，但不能声称已经采用。
无法安全注入时明确报告，仅按请求显式 fallback 处理；绝不伪造工具结果来投递纠正。
失效工具调用需要明确取消处置，其迟到结果对照当前输入版本和执行 fence 对账。
消息本身不取消所有 peer，也不撤销其权限。

### 会话队列

`session_queue` 是已知 Agent 工作会话的 broker 拥有的缓冲。它保留稳定的事件
去重、按会话排序、有界大小、过期、背压、取消和崩溃安全派发。它不是 LoopX
Todo 队列，不得改变 Goal 优先级、认领工作或授予能力。

当前工作结束或明确交还执行权后，broker 通过正常串行化入口提交最旧的合格条目；
仅有 pending-tool idle 观察不能证明这个边界。缺失
或被替换的会话需要显式重新绑定或死信决策；它不会把条目静默路由到全新 Agent
历史。

### 异步 inbox

`async_inbox` 复用现有 Lark 事件 inbox 和 collector，而不是让 Agent 进程保持
存活。collector 写入 owner-private 的有界事件。LoopX 只投影
`operator_inbox_urgency_v0`：pending/question/mention/reply 计数、最旧年龄和
`reply_due`，绝不投影消息正文、发送者、provider id、私有路径或 chat id。

当 `reply_due=true` 时，inbox 通道在下次合格准入时抢占普通推进和 monitor 工作，
不打断当前执行。被选中的 Agent 排空有界内容，对照最新 Goal 状态解释它，先写入
任何持久效果，然后发送至多
一条带 provider readback 的幂等 source-thread 回复，最后才 ACK。仅排空是
只读的；采集或 ACK 永远不是语义权威。

Goal Topic 兼容运行时目前把 provider 采集、Inbox 文件、Goal Chat 回答、回复
和 ACK 内联组合在一起。该路径是有用证据，但当它打开通用 Agent 会话或未能
在绑定 Goal 上登记 inbox 紧迫性时，它不是 Agent 级收敛。实现必须把 provider
采集与入口策略分开、要求已登记的 Agent id，并且要么通过已验证的工作会话
绑定提交，要么把 inbox 指针发布到规范 quota 路径。

用同一修订输入 fixture 验证三模式：未决工具、接收方忙碌/离线、消息过期、满队列、
重复/冲突身份、会话替换、发送方撤权及迟到工具结果。断言实际消费边界和 fallback，
不能只看消息存在。Inbox drain 不证明工作验收；queue 不改当前工作；steer 不能先于
宿主回执声称已采用。这些是拟议验收要求，不是所有宿主已支持三模式的证据。

### 初始产品排序

第一次集成应针对已运行的 App 会话尚不能接受 broker 输入的环境启用
`async_inbox`。这提供了一条重启安全、Agent 拥有的路径，而不假装挂接已经存在。
`live_steering` 随后随挂接 App 会话 broker 跟进。`session_queue` 然后为挂接和
托管运行时补齐忙碌/离线排序与背压。产品可以同时暴露全部三个选项，但每个
入站事件只选择一个有效模式。

## 模式 A：挂接 App 会话

### 发现与挂接

宿主本地 broker 把可挂接会话列为有界描述符。公开描述符可以包含：

- 一个 public-safe 会话引用；
- 宿主 kind 与生命周期状态；
- 已知时的 Goal 和 Agent 绑定；
- 作为不透明或被脱敏引用的工作区身份；
- 消息、流式、中断和恢复能力；以及
- 新鲜度和最近活动时间戳。

操作者显式选择一个描述符。broker 验证会话仍然存活，且其 Goal、Agent、工作区
和信任边界与请求的前端上下文匹配。成功挂接创建一个前端绑定；它不会创建
Agent 进程或第二个上游会话。

### 交互

所有用户消息继续经过 app-server。前端不维护“普通聊天 vs 材料聊天”的分类器。
工作 Agent 及其已安装的 LoopX 交互契约决定需要哪些规范命令或 typed actions。

现有 automation prompt 或可见宿主循环仍然是驱动者。它可以通过正常的 LoopX
命令 surface 读取最新 LoopX 状态、选择 Todo、执行有界段落、验证结果、回写
状态并结算 quota。前端投影该状态；它不会把每条聊天消息包进 `turn run-once`。

### 解除挂接

解除挂接只移除前端绑定。它不会终止 App 会话、删除其 automation、完成 Todo、
消耗 quota 或改变 Goal 状态。如果挂接的会话消失，Desktop 将其报告为 stale
或 disconnected，并且不会静默启动托管运行时。

## 模式 B：托管 Agent 运行时

[DSH/Pi 评估](./harness-selection-dsh-pi-v0.zh-CN.md) 区分首个被动事件源与 managed runtime
选型，列出生命周期、读取及测量门槛。诊断 CLI 已支持组合读取，但这个增量不交付
managed 面板或 supervisor。

### 产品流程

托管桌面路径是端到端的：

1. 选择或创建 LoopX Goal 和工作 Agent 绑定；
2. 选择 Pi 或 `dsh` 作为运行时；
3. 引导配置 provider 与用户约束，由 Agent 在已授权可用 profile 内选择；提供命名的 Ark Agent Plan 预设；
4. 验证运行时安装、provider 认证和已宣称能力；
5. 启动一个运行时并创建一个不透明可恢复会话；
6. 向同一会话发送用户输入；
7. 通过有界 LoopX Turn 推进实质工作；
8. 在一个视图中投影对话、运行时存活、Goal/Todo 状态、验证、quota 和下一个
   调度动作；以及
9. 中断、关闭、重新打开和恢复，而不静默创建新会话。

### 托管循环控制器

托管模式使用 Desktop 拥有的运行时监督器作为外层循环：

```text
fresh LoopX state
  -> gate, quota, and selected Todo decision
  -> create one idempotent loopx_turn_v0 envelope
  -> Pi or dsh executes one bounded attempt
  -> independent validation
  -> canonical LoopX writeback
  -> quota spend only after accepted writeback
  -> scheduler hint: continue, wait, replan, or stop
  -> Desktop supervisor decides whether to request another Turn
```

`loopx_turn_v0` 保持为有界事务。它不会变成永恒循环或第二个调度器。监督器负责
进程存活、一次一个 Turn 的串行化、取消、退避、唤醒、崩溃恢复和会话恢复。
LoopX 仍然负责工作是否有资格执行、结果是否被接受。

该模式不依赖运行时的原生 Goal 抽象。现有 Pi Goal 扩展仍然是受支持的
visible-host 集成，但托管 Pi 可以复用 Pi 的 Agent/session/tool surface，
而不用该扩展充当桌面调度器。同样，现有 `dsh` Turn 连接器是很好的起点；
桌面契约不得依赖一个未被接受的本地插件实现。

### 运行时适配器契约

Pi 和 `dsh` 实现同一个窄托管运行时契约，而不假装其内部循环相同。至少它提供：

- 安装与版本探测；
- 能力发现；
- 创建、恢复、中断和关闭会话；
- 提交一个有界宿主请求；
- 流式输出 public-safe 进度和最终结果事件；
- 返回不透明 owner-local 会话引用；以及
- 把运行时失败映射为稳定的 LoopX/Desktop 错误类别。

适配器可以在自己的 owner-local 存储中保留本地转录、检查点和工具日志。LoopX
只存储协调、验证和恢复所需的标识符与回执。

### Provider 配置契约

运行时选择与 provider 选择正交。Ark Agent Plan 是命名的托管 provider 预设。
显式配置引导、作用域内的用户意图与 Agent 自主分配共同选择合格 profile，
不把 provider 规则散落在 LoopX 内核各处。

一个 provider 配置必须暴露或解析：

- provider 与路由标识符；
- owner-local 凭据引用；
- 受支持的模型发现；
- API surface 与流式支持；
- 输入/输出模态与工具调用支持；
- 已宣称时的推理/思考模式；
- 已宣称时的上下文与输出限制；
- 可用时的用量与限流遥测；以及
- 一个脱敏的健康检查结果。

能力发现是有版本的证据。未知或冲突的 provider 能力在显式探测解决前保持未知。
Desktop 不得静默回退到不同的模型、路由、provider 或计费方案。

Ark Agent Plan 有自己的受支持模型、凭据和用量边界。适配器因此必须验证 Plan
路由，而不是假设标准 Ark 端点支持的模型会自动通过 Plan 配置可用。凭据和
原始 provider 响应保持 owner-local。

### 两种模式中由 Goal 绑定的外部能力

运行时和 provider 选择并不能定义 Agent 的完整工具集。挂接与托管工作会话
必须从所选 Agent 的 Goal 绑定投影相同的外部能力。本 RFC 不新增
`external toolkit` 运行时对象，也不新增另一套 capability-pack 契约。现有
边界已经足够：

- extension 拥有 provider 打包、安装、revision、启用、doctor 与回滚；
- external capability 拥有 caller-outcome 契约、operation、权限、schema、
  验证、readback 与回执；以及
- 可选的 domain capability pack 只在确实需要领域语义时拥有领域策略、状态
  与投影。

一个恰好包含多个 skill、命令和服务的仓库只是一份源分发。它的所有者可以
维护源清单，但每个可执行 operation 都必须通过现有 extension 与 capability
契约进入 LoopX。安装仓库、发现 prompt skill 或观察到同名 provider 都不会
授予权威。除非 Goal 绑定选择了一个确切且 ready 的 revision，否则重复
provider 失败关闭。

只读 operation 可以复用持久 Goal 绑定，而不创建工作事实。物质 operation
必须绑定当前获准的工作尝试；托管模式使用受治理的 Turn 事务，挂接模式则保留
等价的宿主或 automation 决策与结算证据。两条路径都不能允许 capability
provider 直接变更 Goal 或 Todo 状态。

私有 provider coordinates、凭据、日志、trace、数据库行和文档内容保持
owner-local。LoopX 持久状态只保存 public-safe 的 capability 与 operation
身份、provider revision 与 profile digest、有界证据引用和已准入回执。

## 管家工作区中的可选 Computer Use

实施跟踪：[#4114](https://github.com/huangruiteng/loopx/issues/4114)。

### 产品决策与归属

在现有管家工作区增加显式启用的桌面操作入口，处理缺少合适 API 或 CLI 的有界任务。
当声明过的 API/CLI 可以完成并验证同一授权效果时，优先使用它。首个场景是：在指定应用
准备草稿，停在提交前。派发前必须确定 Goal、Todo、Agent 和已有工作会话；不增加中转
管家 Agent，也不增加第三种执行模式。挂接模式只使用宿主声明的工具；托管模式可以
显式启用可选 provider。

归属：首个草稿场景由 `content-ops` 拥有；Computer Use 沿用
[`computer_use_runtime_v0`](../../reference/protocols/computer-use-runtime-v0.md)
的 provider 执行面定位，不新增内置 capability。候选 provider id 为 `maka-cu`，若被
选用则通过可选 extension/package 交付，负责安装、doctor、禁用和兼容性；现有 Desktop
broker 负责呈现。Capability 解释回执并提出状态迁移，Kernel 保留 Todo、gate、evidence
和 quota 权威。复用现有 schema 和契约校验器，不再平行增加一套 builder。

### 实现证据与采用边界

参考 Apache Maka 的固定提交
[`87797378`](https://github.com/apache/maka/tree/87797378cec89e2a31351f64656dc8d26a10e73d)。
以下是源码核查结果，不代表已通过 LoopX 真机资格验证：

- [模型工具](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/packages/runtime/src/computer-use-tools.ts)
  提供应用发现、观察、语义元素操作、键盘输入和窗口操作。观察默认返回元素树，截图显式
  请求；生产模型动作面排除裸坐标输入，有序元素操作在每一步重新观察。
- [宿主适配层](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/packages/computer-use/README.md)
  通过 stdio 上的 `maka.cu/2` JSON-RPC 连接 Runtime 与受监督的 native 子进程，包含
  摘要校验、握手、取消和 generation 失效处理。Desktop 的光标/PiP 是下游呈现，不拥有
  动作权威。
- [执行器清单](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/apps/desktop/bundled-tools.json)
  固定 native 提交 `4a9787d2`，标记 ad-hoc 签名、缺少 notarization、
  `distributionReady: false`。产品选择器仅在 macOS 且提供执行器与摘要时启用。
  Desktop 可下载，不等于 CU 执行器可用或已支持跨平台。
- [宿主事件边界](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/docs/computer-use-host-events-contract.md)
  区分 typed intervention 与 UI 内容变化，并明确全局物理输入归因、更强的进程实例身份
  仍有缺口。[证据分级](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/docs/computer-use-evidence-classes.md)
  区分 real-runtime、故障注入、协议测试和静态检查，各类证据不能互相替代。

采用其观察、动作、回读与接管设计；provider 选型待隔离真机切片验证。
[原生实现](https://github.com/maka-agent/maka-cu/tree/4a9787d2c7f2fbc6a29b33d691916c6b84543661/packages/OpenComputerUseKit/Sources/OpenComputerUseKit)
使用 Swift、macOS Accessibility 和私有 SkyLight 输入路径。
[来源记录](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/docs/computer-use-provenance.md)
区分 MIT 派生代码与专有二进制行为观察。若分发执行器，需要单独完成来源、notice、签名
和兼容性审查；本 RFC 不选用整套 Maka runtime，也不复制其光标实现。

### 必需行为

1. **范围可见。** 启用前展示目标应用/窗口、有界目标、允许的效果、停止条件、provider
   就绪状态和截图/model 路由。启用不授予登录账户访问或新的外部写权限；复用有效的
   范围授权，安装和 OS 权限本身不构成任务授权。
2. **目标新鲜度。** 每次动作绑定当前 observation 和目标进程/窗口 generation，拒绝
   过期或歧义目标，不回退到前台或全局输入。动作后重新观察，完成声明必须有对应效果
   的回读；传输成功不能直接完成 Todo。
3. **人可接管。** 在现有工作区与 Goal Chat 展示简洁进度和即时 Stop/Take over 入口。
   停止撤销待执行输入的权限；已派发但效果未知的动作需要核对结果。锁屏、断连、目标
   重启或可归因的用户介入使旧观察失效，恢复前重新观察并核对范围。未知弹窗与新权限
   决策进入具体的人类 gate。
4. **执行有界。** 对不同 Agent 竞争同一目标的操作串行化，限制动作数、时间和重试预算，
   服务丢失后不盲目重放写动作。Provider 细节保留为本地 typed 状态，回执映射到现有
   封闭 stop-reason 枚举，不随意扩展字符串。持久化未知效果的事实用于恢复核对，不从
   自然语言推断重试指令。
5. **证据私有。** 截图、AX 文本、输入值、窗口标题和 replay 数据留在宿主私有边界。
   发给模型须满足所选 provider 的用户同意与模态策略。共享投影仅包含脱敏事实/句柄，
   查看私有证据仍需要 owner 访问权限。
6. **默认关闭隔离。** 禁用或不可用时不暴露 CU 工具、不启动执行器、不请求 OS 权限。
   禁用撤销会话并停止子进程；卸载移除受管包并说明如何撤销 OS 权限。两者均不改变
   Goal 状态。

### 首个交付切片与验收

通过可选 provider 和已有工作会话完成一个 `content-ops` 草稿至复核流程，将回执与
人类 gate 投影到管家/Goal Chat。第二个真实消费者证明需求前，不增加共享 session/handoff
协议。这是待实现切片，不代表已有可运行命令，也不承诺将 Maka 作为 runtime。

验收必须经过生产入口、真实模型与 native executor，操作隔离的合成应用，独立检查字段值
与 Submit 控件未被触发。另须覆盖干扰窗口、过期观察、应用重启、并发目标占用、用户接管、
锁屏、派发结果未知、禁用再启用；真实宿主事件与故障注入分别记账。记录 provider/model/
executor 精确版本、完成情况、延迟、动作数、人工介入、禁止效果和隐私检查。录制 fixture
或 schema smoke 不足以证明通过。两种前端模式均须证明关闭时的工具暴露、进程启动、入口
路由、投影和 writeback 对等；实现可见 UI 变化时，先提供预览并取得 owner 批准再提交。

## 状态与身份边界

前端存储一个 Agent 级的工作会话绑定，其公开投影足以重连并解释所有权。传输
连接引用该绑定，而不是拥有另一段对话：

```json
{
  "schema_version": "desktop_execution_session_v0",
  "mode": "attached_app_session | managed_agent_runtime",
  "goal_ref": "public-safe LoopX goal reference",
  "agent_ref": "public-safe LoopX agent reference",
  "runtime_kind": "codex_app | pi | dsh",
  "runtime_session_ref": "opaque owner-local reference",
  "provider_profile_ref": "managed mode only",
  "lifecycle": "starting | ready | running | waiting | interrupted | stale | terminal",
  "capability_snapshot_ref": "versioned public-safe projection",
  "frontend_connections": [
    {
      "kind": "web_chat | lark_bot | lark_document_comments",
      "surface_role": "conversation_transport | collaboration_event_source",
      "connection_ref": "public-safe broker reference",
      "capture_scope": "provider-specific typed policy",
      "ingress_mode": "live_steering | session_queue | async_inbox",
      "reply_mode": "none | source_thread | source_comment | configured_mirror",
      "cursor_ref": "owner-local event-source cursor",
      "state": "connected | listening | stale | disconnected"
    }
  ]
}
```

该 schema 是说明性的，不是向浏览器暴露不透明值的承诺。至少这些身份保持
不同：

| 身份 | 所有者 | 用途 |
|---|---|---|
| Goal/Todo | LoopX 控制平面 | 工作选择、权威、gates、记账、终止 |
| Agent 工作会话绑定 | LoopX 控制平面与 Desktop broker | 限定一个 Agent 在 Goal 内的运行时与有序对话 |
| 上游运行时会话 | Codex App、Pi 或 `dsh` 适配器 | 对话、模型/工具执行、原生恢复 |
| Provider 配置 | owner-local provider 存储 | 认证、路由、模型、能力与用量边界 |
| 前端连接 | LoopX Desktop broker | 把 Web 或 Lark 传输挂到同一个 Agent 工作会话 |
| Turn 日志 | LoopX Turn | 幂等有界执行、验证、回写与结算证据 |

挂接描述符或托管会话引用不能授予新的 Goal 权威。陈旧或不匹配的 Goal、Agent、
工作区、运行时、provider 或信任绑定失败关闭。

## 安全与隐私

- 把不透明会话句柄、凭据、环境值、进程元数据、原始转录、provider 负载、
  工具日志和本地路径保留在 owner-local 存储中。
- 对发现、挂接、运行时启动、会话控制和 provider 配置，要求经过认证的本地
  broker 边界。
- 认证 Lark 回调，把每个频道映射到显式 Agent 绑定，并在模糊或重放的入口到达
  Agent 会话前拒绝它们。
- 保持文档权威登记与文档评论事件捕获分离，并为两者要求显式源和 Agent 绑定。
- 在两种模式中保留有效的 sandbox、工作区、审批、网络和能力策略；托管模式
  必须在启动前让该策略可见。
- 在控制动作前和每次托管 Turn 回写前重新检查绑定新鲜度。
- 使用幂等 Turn 键，并允许托管会话至多一个在途 Turn。
- 只在独立验证和被接受的状态回写之后消耗 quota。
- 不把私有部署或协作上下文复制到公共 fixtures、截图、示例或文档中。
- 已提交测试使用合成 provider fixtures，并把真实 provider 测试设为显式可选。

### 团队协调的产品闭环（2026-09-16）

#4547/#4548/#4552 已交付团队预览确认 UI、打包资源及 browser fixture，不再把确认入口列为未实现。按[统一路线](loopx-overall-roadmap-v0.zh-CN.md) R1 补语义一致的 partial/gap/stale 回读，R2 验 worker 实际执行，R3 验自动回报和重启恢复。现有确认 fixture 不能证明 Lark 端完整闭环。

前端/Lark 消费同一 proposal、receipt 和 audience projection；运行状态不能由计划已确认推断。每个实现批次含 packaged frontend 的实际交互和 Lark 适用路径验收，或明确说明未验收。涉及首屏/主 CTA 的实现继续先展示具体预览并获批准；本次仅更新 RFC，不修改 UI。

## 交付切片

### 切片 A：Agent 级 Lark 连接

1. 在 Goal 内建模显式的 Agent 工作会话绑定；
2. 把现有 Goal Channel 连接细化为必填目标 Agent；
3. 在连接中分离捕获范围、入口模式和回复模式；
4. 首先启用 `async_inbox`：登记其 Goal 级配置指针、投影 `reply_due`，并要求
   drain/writeback/reply/readback/ACK；
5. 只通过已验证的工作会话绑定和 Web 使用的同一串行化入口添加
   `live_steering`；
6. 添加 `session_queue`，带稳定去重、排序、有界背压和显式陈旧会话处理；
7. 让每个 Agent 至多挂接一个活跃 Lark Bot 连接；
8. 在所选 Agent 的 Goal Chat 头部显示连接、捕获、入口、回退、监听和待处理
   状态，并提供直接管理入口；以及
9. 直接使用现有工作 Agent，不引入 manager Agent 或独立 IM 对话生命周期。

这是第一个协作切片。它让当前 Goal Channel 立即有用，同时建立两种执行模式
都需要的会话收敛。

### 切片 A2：文档评论感知

1. 定义 provider-neutral 的 Agent 连接器和事件 inbox 契约；
2. 把文档正文登记为脱敏 Goal 权威材料，独立于其评论流；
3. 把一个或多个已配置的文档评论源绑定到已登记 Agent；
4. 支持有界初始追补加增量 cursor 读取，不丢失挂接前或停机期间创建的评论；
5. 在 owner-local 存储中保留评论锚点和回复链上下文；
6. 让可操作评论走与群 inbox 事件相同的持久效果-先于-响应-先于-ACK 生命周期；
7. 仅在连接器宣称时暴露评论回复和 provider readback；以及
8. 只向 LoopX 状态和 quota 投影无内容的待处理、年龄、失败和新鲜度状态。

该切片在切片 A 证明 Agent 绑定和确认生命周期后，把群专属 inbox 泛化。它不会
让外部评论变得权威，也不要求文档 provider 变成任务数据库。

### 切片 B：挂接 Codex App

1. 一个宿主本地 app-server 会话描述符源；
2. 显式 attach 和 detach 动作；
3. 复用现有消息、流式、中断和恢复传输；
4. 在会话旁放置有界 LoopX Goal/状态投影；以及
5. 不启动第二个 Agent 进程或托管回退。

这是围绕已在运行的工作采用 Desktop 的短路径。

第一阶段的可执行 broker 契约沉淀在 Chat session/store 与通用
`worker-bridge attached-session-*` 命令中，而不是 Lark provider 内部：

- `agent_id` 始终表示 Goal 内已登记的 LoopX Agent；
- `executor_endpoint_id` 单独表示 `codex`、`claude-code` 或其他执行端；
- attach 只有在 `(host_surface, host_session_id)` 已精确绑定该 Agent 时才成立；
- attached session 的 Web 与 Lark 消息进入同一有界 FIFO，并保留
  `origin=web|lark`；
- 已有宿主可以保持一个最长 30 分钟的 claim 等待，并在队列出现消息时立即
  获得旧项优先、幂等 claim；超时只返回空结果，不启动或恢复任何运行时；
- 原宿主通过稳定 claim/completion id 领取消息并回写，重复调用不会重复生成
  Agent 回复；以及
- 任何 attached session 路径都不得调用 managed runtime 的启动/恢复函数。

该阶段已经能支撑 `session_queue` 与回复 readback。`live_steering` 仍要求宿主提供
对正在运行 Turn 的 push transport；在此之前 capability 明确为 false，入口失败
关闭，不能用新启动的 app-server 或静默改投另一个模式冒充完成。Desktop 下一步
需要把 bind/list/claim 状态投影成首屏 attach 管理交互，并由 Codex App 等宿主
自动完成 claim/complete，而不是要求人手工运行 CLI。

### 切片 C：托管参考垂直

1. 一个 provider-neutral 的托管运行时与托管 provider 接口；
2. 一个 Desktop 运行时监督器，支持 start、interrupt、close、reconcile 和
   resume；
3. `dsh` 作为第一个参考运行时，复用其已被接受的 Turn 适配器；
4. Ark Agent Plan 作为一个显式选择的 provider 预设；
5. 一个可恢复对话和一次一个的有界 Turn 执行；以及
6. 在 Desktop 中联合展示运行时、Turn 和 LoopX 状态。

`dsh` 是参考顺序，因为它已有有界 Turn 连接器；该顺序不会让它成为永久默认
运行时。

### 切片 D：Pi 对等

1. 一个使用 Pi Agent/session/tool surface 的 Pi 托管运行时适配器；
2. 相同的 Ark Agent Plan provider 配置和能力握手；
3. launch、对话、流式、中断、恢复和 Turn 结果的对等；
4. 证明托管 Pi 无需安装 Pi Goal 扩展即可推进；以及
5. 为 visible-host 使用保留现有可选 Pi Goal 扩展。

只有切片 C 和切片 D 都通过共享一致性测试套件后，双运行时托管模式才算完整。

## 验证标准

### 公共

- 一个绑定至多有一个活跃执行器，用户入口串行化；
- 一个 Goal 可以把不同 Lark 连接路由到不同 Agent，而不会跨会话投递；
- 在每种前端模式下，LoopX 对 Goal/Todo 生命周期保持权威；
- 只读交流不产生任务转换或 quota 消耗；
- 两种模式只暴露同一 Goal 绑定在确切 provider revision 下准入的 external
  capability operation；
- external capability 的物质 operation 不能绕过活跃工作尝试权威、验证、
  provider readback 或结算回执；
- 陈旧或不匹配的身份与能力绑定失败关闭；以及
- 已提交 packets 不包含凭据、原始转录、provider 负载、不透明句柄或真实
  本地路径。

### 挂接模式

- 挂接到运行中的会话不会启动第二个 Agent 进程；
- 连续三条用户消息使用同一个上游 App 会话；
- 中断和恢复保留该会话身份；
- automation-prompt 驱动的工作更新 LoopX 状态并被投影，而不启动托管 Turn；
- 解除挂接后底层会话和 Goal 不变；以及
- App 会话丢失绝不触发静默托管回退。

### Web 与 Lark 收敛

- 一个 Goal 中的两个 Agent 可以挂接独立 Lark 连接，每条消息只到达显式绑定的
  Agent；
- 捕获范围、入口模式和回复模式独立配置，一个事件恰好产生一个有效入口回执；
- 三条交错的 Web 和 Lark 消息在 `live_steering`/`session_queue` 中进入一个
  确定性工作会话顺序，并保留 origin 元数据；
- Web 和 Lark 恢复同一个 Agent 会话，而不是创建并行历史或执行器；
- 不可用的操控会话失败关闭，除非配置了显式队列或 inbox 回退；回退绝不重复
  投递；
- 排队入口有序、有界、重启安全，并保持绑定到同一 Agent/会话，而不会变成
  LoopX Todo；
- 真实 inbox mention 或直接问题投影无内容的 `reply_due`，抢占普通工作，并且
  只在持久效果加已验证 source-thread 回复后 ACK；重复 drain/reply/ACK 幂等；
- Goal Chat 头部投影连接新鲜度并链接到显式管理动作；
- 模糊的 Goal-only 路由和重放 Lark 回调失败关闭；以及
- 挂接或解除挂接 Lark 不改变 Agent 的执行模式或 LoopX Goal/Todo 状态。

### 外部连接器感知

- 绑定到同一 Agent 的群事件和文档评论作为不同 provider 事件类型被捕获，并
  独立去重；
- 初始追补发现挂接前创建的可操作事件，然后实时投递或轮询从已提交 cursor
  继续；
- 拉取文档正文既不确认评论，也不把它们标记为已纳入；
- 纳入评论在任何回复和 cursor 推进前创建可审计的 Todo/设计/不跟进效果；
- 评论断言在满足其配置的证据或 owner 边界前，不会成为被接受的能力或需求
  事实；
- 支持回复的连接器验证 provider readback，而只读连接器记录显式“无回复”
  结果；以及
- 状态、quota 和公共 fixtures 不包含评论正文、作者 id、私有源引用或 provider
  cursor 值。

### 托管模式

- Desktop 恰好启动一个所选运行时，并在三条用户消息和多个有界 Turn 中复用
  同一个不透明会话；
- 运行时在未安装宿主原生 Goal 循环的情况下推进；
- 每次实质尝试都有所选 Todo、幂等 Turn 身份、独立验证、被接受的回写和回写后
  结算；
- 中断和应用重启保留或显式协调会话，而不是静默创建另一个；
- 崩溃重放不重复状态回写或 quota 消耗；
- Pi 和 `dsh` 通过相同的生命周期和 Turn 结果一致性套件；
- Ark Agent Plan 配置验证自己的受支持模型与用量边界，并在缺少认证或不支持
  能力时失败关闭；以及
- provider 或模型变更是显式重新绑定操作，绝不是静默回退。

## 非目标

- 用新的模型/工具执行内核替换 Pi 或 `dsh`。
- 移除现有 automation-prompt、原生 Goal 或 visible-host 模式。
- 把每条挂接聊天消息包进托管 Turn。
- 把 `turn run-once` 变成永恒调度器或桌面进程监督器。
- 构建超出 Pi 与 `dsh` 所需行为的通用运行时抽象。
- 把企业 toolkit、provider coordinates、凭据或私有操作流程 vendoring 到
  LoopX core。
- 把 toolkit 仓库、安装脚本或 prompt skill 当作一个隐式受信的通用
  capability。
- 在 LoopX 中硬编码永久的模型能力表。
- 把标准 Ark 路由与 Ark Agent Plan 路由视为全局可互换。
- 让转录、Desktop 存储或 provider 响应对 Goal/Todo 生命周期拥有权威。
- 自动把挂接会话迁移到托管模式。
- 在 Lark 与绑定工作 Agent 之间引入 manager Agent。
- 要求每个 Agent 有一个物理 Lark 应用凭据；v0 要求逻辑 Agent 级连接和显式
  路由。
- 从消息散文推断入口模式，或把 `mentions|all` 当作活跃工作会话挂接的证据。
- 把会话入口队列当作 LoopX Todo 队列，或在 quota/status 中存储原始 inbox
  内容。
- 把文档正文拉取当作评论感知，把文档评论当作被接受的 Goal 事实，或在持久
  效果和必需响应被验证前推进评论 cursor。

## 相关 surface 与提案

- [Runtime connector catalog](../../integrations/runtime-connector-catalog.md)
- [Domain capability packs](../../product/domain-capability-packs.md)
- [Extensions](../../reference/extensions.md)
- [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md)
- [DeepSeek Harness connector](../../integrations/deepseek-harness-connector.md)
- [Pi Goal mode](../../../loopx/pi_goal_mode/README.md)
- [Goal Channel collaboration v0](goal-channel-collaboration-v0.md)
- [Volcengine Ark Agent Plan documentation](https://www.volcengine.com/docs/82379/1928262)
- [Volcengine Ark API overview](https://api.volcengine.com/api-docs/view?serviceCode=ark&version=2024-01-01)

## 开放问题

1. 哪个现有宿主本地 registry 应该拥有可挂接 App 描述符和托管运行时会话记录？
2. 第一个托管 Pi 适配器应该内嵌 Pi 的 Agent API，还是监督其 CLI 协议？
3. Pi 和 `dsh` 能够在不泄漏本地转录的前提下暴露的最小公共流式与工具事件
   surface 是什么？
4. 哪个 Ark Agent Plan 推理 API surface 应该是第一个受支持的 provider 适配器，
   哪些能力探测在启动前是必填的？
5. 哪个 Agent 生命周期操作显式轮换或替换其工作运行时会话，同时保留可审计的
   对话边界？
6. Desktop 应如何安装和升级可选的 Pi 与 `dsh` 运行时依赖？
7. v0 之后，Agent 是否应支持多个 Lark 频道连接，什么投影策略控制哪些响应
   镜像到每个传输？
8. 哪个有界 owner-local 存储应该支撑 `session_queue`，显式重新绑定何时可以
   在替换运行时会话后保留排队条目？
9. `async_inbox` 在 `live_steering` 上线后是应保持可选的稳态模式，还是主要
   服务离线和非常驻 Agent？
10. 哪个 provider-neutral cursor 契约可以覆盖 webhook 投递、增量评论列举和
    有界初始追补，而不把 provider 标识符泄漏到公共状态？
11. 文档评论连接器在第一版中应支持自动“已解决”状态转换，还是要求显式的
    人工或能力拥有动作？
