# RFC：用户确认的垂域操作（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** 提案
- **作者 / 负责人：** LoopX 维护者与可选垂域 provider 维护者
- **创建日期：** 2026-09-12
- **最近规范修订：** 2026-09-30
- **实现基线：** `72e557586`
- **相关契约：** [扩展](../../reference/extensions.md)、
  [Effect interpreter](agent-loop-effect-interpreter-v0.md)
- **语言镜像：** [English](human-confirmed-domain-operations-v0.md)

中英文版本互为语义镜像。修改契约时须同步更新两版；要求或边界不一致即为缺陷。

第 1–11 节定义拟议契约，并非已发布的命令。第 12 节记录尚未解决的实现选择。
本文不修改运行时、默认权限、配置或用户入口。
第 13 节记录来源上下文/已准入执行者续接的实现切片；部署与真实验收独立于本地验证。

## 1. 决策摘要

按语义划分三层，不要求拆成三个仓库：

| 层 | 职责 | 建议代码归属 |
| --- | --- | --- |
| LoopX 交互层 | 经认证的用户操作、不可变请求绑定、执行认领、原会话回传与共享展示 | 现有公开 typed-action、chat 和 Lark 模块 |
| 金融垂域层 | 订单意图、金融校验、总敞口额度预留、订单/成交核算与金融展示 | 可选的公开 finance 执行包 |
| 交易平台适配层 | 平台产品、精度、账户模式、API 认证、订单提交与平台对账 | 独立安装的 Aqua/Hyperliquid、Futu 或其他 adapter |

研究方法仍属于研究 provider。策略可以提出订单，但不能因通过研究门禁而获得执行权限。
开启研究 capability 不得安装凭据或开启交易。

公开 finance 执行包拟作为 `packages/` 下的独立同级发行包，而不是扩展现有
value-discovery evaluator 的权限。本文不注册新的内置 finance capability 或包名。
先实现第一个真实消费者，再提取通用插件框架。

## 2. 问题与不变量

用户应收到内容明确的操作卡，点击一次后，在该会话自动收到结果。
“已转交 Agent”不能证明用户已确认、操作已执行或平台已有成交。

必须保持以下不变量：

- 只有可信交互入口能证明用户点击。Agent 不能通过工具参数或模型输出伪造确认。
- 确认摘要绑定所有影响实际操作的条款、条款版本、展示投影、执行器版本及目标账户引用。
- 重要条款变化必须创建新请求。执行只能在已确认边界明确允许的值中选择。
- 消息投递重试不能提交操作。提交结果不明确时必须对账，不能创建新请求或盲目重试。
- 前端和飞书读取同一份请求与结果投影。
- Core 仍是 Goal/Todo 状态的权威来源，平台证据仍是订单和成交的权威来源。
  聊天文字和本地执行认领都不能证明金融操作实际发生。

## 3. 范围与非目标

首批范围：一个不可变、经用户明确确认的操作，一个已注册执行器，以及自动回执和
终态或不确定结果的投递。在配置真实凭据之前，先用模拟金融 adapter 演示完整流程。

这不会把普通 Goal steering 改成逐次确认流程。现有读取、协调和汇报的持久权限保持独立。
不支持任意工具名/kwargs 执行、卡片内 shell 命令、全局工作流 DAG、自主交易循环、
划转服务或跨平台组合引擎。

## 4. 已核查的现有职责归属

| 当前代码 | 已核实边界 |
| --- | --- |
| `loopx/chat_action_store.py` | 请求摘要、幂等、带状态检查的提案；仅支持内置 Goal/Todo 动作；当前 `failed` 可重试 |
| `loopx/chat_actions.py` | 校验和应用内置动作；调用本地 API 不能证明用户确认了金融操作 |
| `loopx/extensions/lark/event_collector.py` | 当前 collector 选择 `im.message.receive_v1`；文字监听就绪不等于卡片回调就绪 |
| `loopx/capabilities/manager_context/roundtrip.py` | 原会话回传、回执持久化与传输重试；上下文委托不授予交易权限 |
| `apps/presentation/dashboard/src/data/chat.ts` | 与现有聊天动作详情和控件配套的 typed-action schema |
| `packages/loopx-finance-value-discovery/extension.toml` | 可选公开研究 evaluator，权限为空；提供研究展示面，没有执行契约 |

通过明确的类型化扩展复用上述模块。不要在通用本地 HTTP 入口偷偷增加金融 `apply`
分支，也不要让结果未知的外部提交继承 `failed` 可重试的行为。

## 5. 架构与唯一权威

### LoopX 交互层

Core 管理操作信封：操作 ID、schema 版本、垂域/adapter 绑定、不可变载荷引用及摘要、
展示投影摘要、有效期、授权受众、来源会话/消息/请求、已核验确认回执、派发认领及结果引用。
Core 不解释金融载荷语义；哈希是用于绑定的元数据，不能替代向用户展示的条款。

扩展现有 typed-action 权威模块，不另建提案数据库。如果实现时其权威已经迁移，
应使用届时的规范权威来源，不要在旁边再建一套 Python 权威。
信封、身份和日志原语的生命周期适用时应予以复用。Goal quota/Todo 准入绝不能授予
执行金融操作的权限。

Core 校验已注册的垂域契约，仅调用已准入、操作类型和 schema 版本固定的执行器。
模型可以准备提案和读取回执。产生实际操作的入口只接受服务端签发并被消费的确认回执，
不提供 `confirmed: true` 这样的绕过参数。

飞书优先复用现有机器人传输与 `card.action.trigger`。传输层先核验应用/租户来源，
再核验操作者、群、原消息、动作、摘要及有效期。Web 入口使用已认证的 owner 身份与
origin/CSRF 防护，并访问同一个权威来源。只有不透明 ID、回环地址、转发卡片或相同
显示名称，都不足以建立确认。

### 金融垂域层

可选 finance 包负责规范金融载荷和金融结果 reducer，不另建竞争性的确认账本。
垂域记录以 Core 操作 ID 和不可变金融载荷摘要为键。

订单条款包括平台/产品/资产标识、账户引用、方向、数量及单位、订单类型、价格边界、
订单有效方式（time-in-force）、只减仓（reduce-only）、保证金/杠杆设置、费用上限及单位、
可选保护单、有效期和证据时间。十进制数量必须保留精度。

账户级约束要求该账户的所有操作共用一个额度预留权威。单独校验每张卡不够：
并发确认可能共同突破同一份剩余额度。Core 完成认领后、提交订单前，垂域原子地预留额度，
计入已有持仓和未完成订单，并拒绝过期或覆盖不完整的账户信息。
结果未知的提交保留其额度，直至对账完成。进程重启不能重置预算。
账户通过其他客户端发生交易时，需要刷新账户观察；本地账本不能阻止其他客户端的操作。

提交前检查使用最新平台观察校验金融不变量。如果精度调整、费用、账户模式或其他重要
条款将超出已确认边界，则拒绝操作并要求新卡片，而不是静默编辑已确认载荷。
未经新确认，不得为满足平台最低订单要求而增加数量。

垂域结果区分已受理、挂单中、部分成交、全部成交、拒单、已撤销和未知。
入场单与保护单分别记录结果。费用、资金费、资金流动、返佣和 PnL 分别核算；
没有费用证据表示未知，不表示零。研究证据可以解释订单提案，但不能替代执行证据。

### 交易平台适配层

每个 adapter 声明支持的产品/订单类型、最低要求/精度、费用语义、账户模式、认证要求、
客户端订单 ID 支持和对账边界。它使用平台官方 API 或 SDK，执行有界的发现、读取、
提交前检查、提交与对账操作，不得静默切换到另一平台或产品。

面向 Aqua 的 adapter 必须区分 Aqua 产品/builder 政策与底层平台协议；
“Hyperliquid 支持”不能证明 Aqua 条款、费用归属或账户初始化条件。
Futu adapter 必须区分行情访问与交易权限，并处理证券手数规则、交易时段、账户地区及
OpenD 解锁要求。共享标准化契约不意味着这些产品可以相互替换。

adapter 将凭据保存在单独配置的私有凭据设施中，返回标准化证据和指向原始平台回执的
有界引用。它不能修改 Core 授权、用户预算或策略决策。提现、入金、密钥授权以及
杠杆/账户模式修改，都不是订单 adapter 隐含允许的副作用。

## 6. 生命周期与失败语义

拟议的交互生命周期：

`prepared → awaiting_confirmation → claimed → outcome_observed`

尚未认领的请求可因取消或过期而终止。认领后，Core 分别跟踪派发和结果投递，
不把它们与垂域执行状态混为一谈。执行器在网络提交之前，持久记录稳定的客户端订单 ID
及提交尝试。派发后超时或崩溃记为 `submission_unknown`；任何后续实际操作前，
先按原 ID 对账。本地锁无法保证平台恰好成交一次。平台不能消除歧义时，保留未知状态
并自动汇报。

回执包含操作 ID、垂域/adapter 版本、已确认摘要、提交尝试/客户端/平台 ID、证据时间、
覆盖范围与标准化结果。前端和飞书消费同一份带版本投影，不各自重新计算成交状态。
中间进展可以更新同一操作卡。通过现有 manager roundtrip 回传一次有界最终结论，
不要为每次成交覆盖其不可变结论。后续监测是独立且关联的工作流。

### M1 实现接缝

首个实现必须作为一个跨入口完整切片共同审阅：

- 只在 `loopx/chat_action_store.py` 和 `loopx/chat_actions.py` 背后的规范
  typed-action 权威中扩展垂域无关的操作信封；
- 在 `loopx/extensions/lark/event_collector.py` 的文字监听路径旁增加独立、
  经认证的 `card.action.trigger` 消费者，保持现有 WebSocket 监督和回调确认契约；
- 在 `apps/presentation/dashboard/src/data/chat.ts` 及其所属视图接入操作投影，
  不另建订单存储或只在本地页面实现；
- 在可选 finance 执行发行包中放模拟金融消费者及订单/结果 schema，不调用交易平台或签名器；
- `manager_context` 只负责原会话结果回传和投递重试，不能成为确认账本或金融账本。

如果缺少前端、飞书、模拟消费者或回执恢复中的任一部分，M1 都是不完整的；
仅完成后端的 PR 必须明确标记为 partial。

## 7. 替代方案与隐私

- 只有参数化工具还不够：它缺少持久的用户身份、请求绑定、提交歧义恢复和独立于传输的
  回执。工具可以作为本契约的提案与读回接口。
- 将确认、风控和 API 逻辑都放进单个平台机器人，会在接入下一个平台时重复建设。
  应分离传输与金融语义。
- 通用 Core 金融引擎会把交易规则和金融权威放错位置。通用卡片应消费垂域拥有的字段和
  校验器。
- 独立垂域数据库可以保存载荷、额度预留与回执，但不能保存另一份可写 Goal 进度或
  独立确认记录。

可复用的协议、校验、方法和 adapter 可以公开。仓库可见性独立于语义分层。
账户 ID、真实订单、额度、个人策略、群绑定、凭据和原始私有数据留在被忽略的本地存储。
共享代码不授予账户访问权限。

## 8. 迁移与回滚

功能默认关闭。现有 Goal/Todo 动作和只读 finance 安装保留原行为及权限。
混合版本宿主明确拒绝不支持的操作/schema 版本。禁用 adapter 会阻止新提案/提交，
但保留已认领操作的回执读取与对账。卸载不得丢弃未完成订单或提交结果未知的记录。
凭据撤销仅通过用户明确配置完成；删除软件包不会撤销平台订单。

## 9. 验证与用户入口

| 验证主张 | 所需证据 | 边界 |
| --- | --- | --- |
| 精确操作绑定 | 逐一修改影响操作的字段、摘要、有效期和执行器版本 | 变化后的请求不能复用确认 |
| 点击身份认证 | 错误应用/租户/操作者/群/消息；转发卡片；伪造本地请求 | 不执行，并提供可采取行动的反馈 |
| 唯一认领 | Web/飞书并发点击与重启重放 | 一次认领；同一个原始客户端订单 ID |
| 总额度约束 | 两个订单单独都符合额度，同时执行却超过总额度 | 提交量不超过有效预留额度 |
| 提交歧义 | 平台受理、响应丢失、进程重启 | 只读对账，不盲目重提 |
| 金融结果 | 部分成交、保护失败、币种不匹配、重复成交、历史缺失 | 明确不确定性，正确核算 |
| 自动回传 | 投递丢失后重启 | 原请求收到结果，不产生新执行 |
| 产品完整性 | 现有聊天详情与飞书卡接同一后端，覆盖桌面/移动端及打包前端 | 状态、条款、禁用控件和失败反馈一致 |
| 功能关闭 | 现有动作与两个研究 provider | 行为和权限不变 |

模拟结果必须在每个展示面明显标注。模拟通过不能证明平台回调已配置、凭据已就绪或
实盘交易已验收。实盘验收需要真实用户点击与匹配的平台证据；开发 Agent 不得伪造任一项。

## 10. 运行要求

卡片回调健康与文字监听健康分别展示。区分待确认、已过期、派发结果未知、回执待到达和
投递失败。不要在群里暴露回调 token、签名器数据或原始敏感错误。
提供可执行确认之前，应展示不支持的产品/模式和账户覆盖不完整的问题。
保留有界的对账与投递 worker；复用现有进程监督机制，而非专用业务 automation 提示词。

## 11. 交付计划

| 里程碑 | 完整交付内容 | 出口证据 |
| --- | --- | --- |
| M1 | 扩展通用 typed-action 权威、经认证的飞书回调和现有前端详情，并接入一个模拟金融消费者 | 同一请求跨两个入口只确认一次；自动回传结果；功能关闭时行为一致 |
| M2 | 公开金融契约/reducer/额度预留权威及隔离的 Aqua adapter；实盘仍关闭 | 精度/成本/模式与并发预算检查；安装后的契约一致性验证及只读账户资格核验 |
| M3 | 用户配置凭据并确认一笔精确订单 | 匹配的真实提交/成交/对账/回传证据；不把 mock 声称为实盘 |
| M4 | 只有真实调用方需要时才接入第二个平台 adapter | 同一契约一致性验证，记录不支持的功能和平台特定检查 |

M1 同时涵盖 UI 与后端，不要拆成“后端 PR 已完成”而遗忘前端配套。
明确草稿跨仓库 PR 的依赖。先提取实际共用的接缝，不构建推测性的 adapter 框架。

## 12. 待定实现决策

1. **规范动作权威：** 实现时重新核查类型化权威，扩展其现有存储/事务模块。
   由维护者在 M1 决定。
2. **公开 finance 包身份：** 建议独立可选的执行发行包，保持研究安装只读。
   结合 M1/M2 的真实调用方确认命名和准入；本文不新增注册项。
3. **平台发行方式：** 协作仓库可用独立包托管 adapter。核验许可证、权限隔离和安装方式，
   不把签名加入现有行情采集器。由 adapter 维护者在 M2 决定。
4. **部署资格：** 核实真实飞书应用回调和 Web owner 身份认证机制。
   事件进程健康本身不能证明任一用户路径可用。M1 验收前必须完成。

## 13. 用户确认后的 Agent 执行：来源与执行者分离

原对话提供上下文及结果返回的受众，不必同时成为执行进程。长期契约是
**用户批准 → 受管执行准入 → 单次消费 → 原系统证据 → 核验回传**，不是“Desktop
线程 ID 就是执行令牌”。已有垂域 Agent 可复用其已授权工作流，显式配置的 delegation
也可由 LoopX 自有受管 Turn 执行；两条路径都不增加账户访问、交易或宽泛写权限。

### 单一权威与不可变执行主体

- `chat/actions/actions.json` 中原始 `operation.execute` 仍是确认、claim、
  消费、结果和对账的唯一存储。
- 原外接主体保持
  `{kind: "agent_session", host_surface, thread_id, revision: "agent-session-handoff-v0"}`；
  其公开 CLI 认证门禁仍关闭。
- 新增显式 opt-in 主体为
  `{kind: "managed_turn", todo_id, session_id, profile_digest, model, reasoning_effort, revision: "managed-turn-handoff-v0"}`。
  它指向既有 Codex Turn session owner，不另建运行时目录。准备时核对已注册
  Goal/Agent、精确 Todo/session、适用的 Goal instance、传输与固定 profile。
- `source_route` 仅从既有登记对话绑定投影，可为空；它是不可变的上下文/返回信息，
  不是调用者身份、执行许可或消息已送达证明。
- TypeScript 管理执行器归一化、绑定判断、传输配置读回、单次准入、恢复和展示；
  Python 只承载原生进程、session/存储锁及 Lark IO。不新增平行 Python 审批或通用决策源。
- 旧外接批准不转换为受管批准。session、Todo、模型、思考深度、sandbox、workspace、
  home、可执行文件或 invocation-scoped MCP 配置改变时，必须显式开启 fresh
  iteration 并重新批准。不复制轨迹、SQLite 行或凭据。

生命周期专用 `source_session_v1` registry 仍拒绝业务操作准备；本切片不启用替换
Goal instance，不改变 provider 权威，也不把旧操作移植到另一运行时。

### 既有 delegation 与 Turn 入口

启动许可仍归原 operator-owned delegation 配置。Codex binding 通过既有
`host_args` 显式启用：

```text
--host codex-cli --codex-operation-tools
--codex-model MODEL --codex-reasoning-effort EFFORT
--codex-sandbox read-only
```

相同选项可用于 `turn run-once`。复用既有 delegation inspect/preflight、start、
准入、租约、session 续接、结果验证和验收。原 host owner 读回 profile；
不新增前端配置存储或隐藏默认。不固定或不受支持的配置在预检中标为 unavailable；
有效 argv 仍是 runtime-unverified，不能证明宿主或操作已运行。仅在现有工作授权
确需时使用 `workspace-write`；此传输不接受 `danger-full-access`。

已准入宿主复用 `CodexChatAgentSession` app-server adapter，将不透明线程保存于
既有 Goal/Agent/Todo session owner，安装不可导出的 `loopx_operation` dynamic tool。
自有 stdio 连接在分发前核对原生 thread 与活跃 Turn 元数据；工具参数不能传 actor、
verified、信任密钥、签名或 bearer token。回执返回同一原生连接，不恢复或冒充
登记的来源 Desktop 线程。

`context` 读回精确受管主体但不给执行许可；`prepare` 在规范 action store 预览
不可变条款；`pending` 读取有界 Inbox；`inspect/consume/report` 复用锁定操作接缝。
保留 invocation-scoped collaboration MCP 的普通委派能力，它不是操作身份签发端。
宿主结果沿用 typed Turn result 及验证；最终答复文字不能冒充操作结果回执。

首个传输是 Codex 专用 IO，不是 Codex 专用审批模型。其他受管宿主只有在原生身份
来源、实际 profile 读回、撤销和响应路由通过验收后才能实现同一契约。外接 Desktop
仍是独立可选 adapter，**不是受管路径的前置依赖**。未来远端边界可能需要 owner
登记的认证 invocation 核验，但不应让无人消费的签名器或本地伪造凭据成为自有进程
分发的前置条件。

### 确认、单次消费与恢复

既有经认证 Lark 回调记录精确用户确认并 claim 原提案，不启动浏览器或垂域 adapter。
重放、模拟及卡片投递恢复都不产生垂域效果；Dashboard 对用户操作确认仍只读。

只有首次成功原子消费返回 `execution_allowed: true`，核对经认证的确认、不可变
条款、当前执行绑定、有效 Goal 和到期时间，在 Agent 垂域执行前持久记录消费。
所有重试，包括丢失响应或重启后的同一 attempt，都不再授予执行。
它约束的是**授权消费**，不宣称能禁止可信 Agent 的所有工具调用，也不承诺平台恰好执行一次。

锁序是 Goal 生命周期 → registry → 既有 Turn session（仅受管路径）→ action store。
session 替换/丢弃与消费共用锁。先提交的撤销阻止消费，之后的撤销不能抹去已提交回执；
外部执行期间不持锁。

绑定宿主回写 `loopx_operation_outcome_v0`：精确 operation/载荷/确认摘要、claim、
executor revision、consumption ID、核验投影、`simulation: false`、
有界原系统证据引用及单独的 `external_write_performed`。结果为 `executed`、
`not_executed` 或 `submission_unknown`；未知保守披露可能的外部副作用。
原生传输成功不等于平台证据；Core 不解释价格、平台、费用、持仓或保护单。

已消费/未知义务在到期或原绑定撤销后仍保留。同 Goal/Agent 当前接手者只有在原绑定
撤销后才能检查/回写历史证据，不能消费未用票据或重写执行器。原 outcome 不可变，
对账以 `reconciles_outcome_digest` 绑定并追加实际报告者/路由来源，读回保留原始与
对账证据。已停止/历史 Goal 沿用证据专用生命周期保护，不因此获得新的执行许可。

### 共享 Inbox、前端、Lark 与真实回传

既有 Inbox 直接投影规范定位信息：恢复优先、每页 20 条、总数/类型化 overflow
及独立 operation cursor。`loopx_operation pending` 接受绑定游标；CLI 投影用
`manager-inbox read --operation-cursor CURSOR`。新增/改变工作应无游标重读，
读完一页或遍历结束不代表义务已解决。

共享 TS 操作 frame 展示执行者、固定模型/思考深度、Goal/Agent/Todo 范围、可选来源
上下文，并区分：已确认但外接认证不可用、已确认待绑定受管回合、已消费待证据、未知须
对账，以及独立核验的结果投递。CLI、Dashboard 和原 Lark 卡消费同一 frame。
结果读回必须匹配当前 `initial/reconciled` 阶段；旧未知结果的投递不能证明新对账结果。
现有投递恢复更新原卡，不重提操作。delegation 结果验收及 requester 采用仍是独立
回执，不新增自动 chat 回传协议。

### 资格化与剩余交付

本切片验证自有进程原生工具分发（含新 Turn 与同线程恢复 Turn 的有界真实 Codex
context 调用，结果均由既有 typed result validator 接受）、规范
批准/消费/结果夹具、profile/session 撤销、原路由隔离、delegation 预检和打包展示。
真实 context 探针只在当前拥有的 home 创建新的受管 GPT session，不建提案、
不造群确认、不执行金融副作用；合成批准夹具不是真实用户批准。

公开 `goal-channel inspect-operation/consume-operation/report-operation` 仍在
私有读写前拒绝，即使 `CODEX_THREAD_ID`、路由、自签 proof 完全匹配或旧运行时
意外返回 actor 成功，也没有 proof-import 捷径。

本切片不实现确认后的即时宿主唤醒，`host_delivery: "not_attempted"` 保持真实。
沿既有已准入 Turn/delegation 续接；后续持久唤醒复用其调度/session owner，
不启动平行 resumed 执行者。宣称投研最小闭环前，仍须证明安装、真实用户批准、
绑定原生消费、垂域提交前检查与原系统证据、结果验收及原卡/受众读回。
Core PR 仍须 owner review，不在合并前自行安装。
