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
自有原生工具与 Turn 启动使用相同的精确执行主体过滤，并在分页前生效。
同一 Agent 另一 Todo/session/profile 的当前批准不能出现在本任务页面或挤占其
续接信息；游标也不能跨执行主体复用。判定仍由既有 TS Inbox owner 负责，
不新建宿主自有 Inbox。

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

启用 operation 的已准入 Turn 自动携带其精确 Goal/Agent/Todo/session/profile 的
规范确认请求定位信息，并在 Inbox 分页前过滤范围。仅在原生 `turn/start` 返回接受后，
既有 action store 才记录不可覆盖的首次启动证据：确认事件/时间、claim、LoopX Turn key、
原生 Turn 和接受时间。`host_delivery: "native_start_accepted"` 只证明该观察，
不授予消费或外部效果权限，也不声称回合仍在运行。回执失败时先停止、不分发 operation 工具；
后续回合保留首次观察。CLI/Inbox、Dashboard 和 Lark 区分原生续接接受、授权消费和真实结果。

认证确认 callback 现在可通过既有 delegation owner 请求一次续跑，前提是具有
**独立且默认关闭的 operator 启动 grant**。grant 指定已有 requester/binding，
不采用卡片提供的模型、工作树或执行者。操作 owner 核对精确
Goal/Agent/Todo/session/profile、确认期限和未消费状态；普通 Turn 仍负责 quota、
租约、验收及原生启动。原生适配器在恢复前重验完整生效配置，拒绝新建/替换会话。
用户确认不授予启动配置或垂域执行权限。

规范 operation id 固定唯一持久 delegation 身份。callback 重放只读原定位信息，
不再次 spawn 或运行产物验收；丢失启动 ACK 仍由原 journal 恢复，不在回调自动续跑
不确定的 worker。移除 operator grant 会在下一次 callback 边界读回生效，
不追溯取消已经开始的工作。`delegation_requested` 不证明原生启动、完成验收或
来源送达；原生接受前 `host_delivery: "not_attempted"` 保持真实。
启用方式见[原配置入口](../../reference/local-delegation.md#confirmed-operation-callback-continuation)。

File/SQLite 资格化使用合成认证确认夹具、真实 detached delegation worker 与
CLI/Turn 路径，以及合成原生传输；证明原会话启动，但未消费批准或执行垂域操作。
夹具故意等待，不冒充 Todo 完成。本增量未跑真实 Lark/模型或金融探针。
前端 grant 编辑及经认证的原来源受众结果回传仍为独立的**部分交付**义务；
目前只能通过 operator-owned collector 配置启用，不另建 UI 状态权威。
宣称投研最小闭环前，仍须证明安装、真实用户批准、
绑定原生消费、垂域提交前检查与原系统证据、结果验收及原卡/受众读回。
Core PR 仍须 owner review，不在合并前自行安装。

### 准备提案与规范返回受众读回

准备提案不是领域执行。已准入任务提供不可变条款时，可以在确认和消费前调用
`prepare`，其 `execution_allowed: false` 是正常结果；`context/pending/inspect`
也不要求先消费。先核对已有提案，不重复创建；准备、等待和最终答复文字都不能
证明任务完成。领域副作用仍要求原生工具连接上的首次成功消费回执。

受管执行器只有一个不同的已登记 Goal/Agent 返回受众时自动选择；存在多个历史
受众时，必须由宿主显式传入 `--codex-operation-source-route-json
'{"host_surface":"codex-app","thread_id":"REGISTERED_THREAD"}'`。
这只是已登记的回传受众，不是认证、session 替换或执行许可；模型不能改投。
受管重复绑定会去重；无登记受众保持历史 null 路由，非受管 adapter 保留既有
无 source-route 投影，不调用这条受管解析。准备落盘后，返回受众进入原确认摘要，不可修改。

源受众选择与准备提示构成有界后台切片，不是完整产品交付。下面的个人工作台
配套改动仍在同一计划内单独完成首屏评审后交付。工作台在可见时读取规范
action list，按范围隔离查询并取消旧请求，限制
单次请求时长，不在后台做间隔读取。复用管家简报将已知 Goal 的待确认操作连到
原抽屉，溢出项通过既有对话可达。抽屉按同一提案 ID 跟随投递、确认、消费、
结果和取消。没有投递回执的请求不能声称群卡已存在；取消只是行政终态，不是
执行结果。读回失败明确提示缓存可能过期并允许重试；浏览不确认也不执行操作。

配套 UI 验收通过隔离的规范后端快照、loopback HTTP 夹具和打包页面，覆盖中英文及
桌面/移动端。这些合成回执不证明真实用户批准、真实投递或确认后的即时唤醒。
真实点击到原受管 session 的派发延迟仍是独立的必需验收项，必须复用现有调度
和 session owner。
后台切片的聚焦原生/规范化测试不证明其提交包含配套 UI，也不证明源投递已经接通。

### 自动续接与原受众返回：剩余交付

当前经过认证的确认回调会登记 claim 并投影受管交接，但不会启动原宿主。
原生 `report` 将结果写回规范存储并在工具连接上返回；投递恢复更新原 Lark 卡片。
两者都不等于已向选定的源会话投递。`outcome_observed` 操作不在待办 Inbox 中，
因此读完 Inbox 也不能证明已返回原受众。

这两个缺口继续由同一个实现 Todo 和 capability owner 负责。后续有界交付按以下
顺序验收；这是规划，不是准备/读回切片已经具备或验证的功能：

| 交付 | 复用的权威与必需边界 | 退出证据 |
| --- | --- | --- |
| 确认事件触发续接 | 原 Turn/session owner 与显式的 operator-owned 启动绑定；回调只传操作定位信息，不复制私有条款或新增执行许可 | 经过正常 Goal、Todo、quota、租约及过期核对后自动启动同一个原生 session/profile；重复回调最多产生一个活动续接 |
| 重启恢复 | 既有 Turn journal 与内核 single-flight 锁；不把已终止的等待 Turn 盲目当成新调用续跑 | 覆盖启动前后崩溃与响应丢失，恢复原尝试；已消费/未知操作只核对证据、不重新提交；停止、session 替换及 profile 漂移拒绝新启动 |
| 原受众返回 | 规范操作/结果与既有 return-delivery 语义，选定受众须有合格 adapter | 先登记投递尝试，再真实读回原受众处相同 operation ID、结果阶段及 digest；未知投递不盲目重发；Lark 原卡与源会话回执分开 |
| 原消费者验收 | 已安装且固定的 runtime，以及原测试 Todo 上真实的非金融确认 | 实际确认、宿主启动、消费、结果及源投递时间；一次消费；首轮健康路径从确认到宿主启动不超过 60 秒 |

60 秒是首轮验收目标，不是已证明的延迟或调度保证。宿主启动必须由原生 provider
接受的 Turn 与匹配的连接元数据证明，仅创建进程或本地写入 `started_at` 不够。
相邻的 [delegation lead 唤醒改动](https://github.com/loopx-project/loopx/pull/5304)
面向原发起的内部 Goal Chat 会话；其判定/恢复边界可供复用，不证明受管操作的
确认唤醒或外部源受众已送达。前端刷新间隔、后续 heartbeat、
手动 resume 及工程转述消息都不算自动续接或源返回。路由登记不等于投递 adapter
已合格；无关的 BotMux 绑定或附着 Desktop 身份不能替代。新增自动启动配置必须
复用既有配置 owner，并在受影响的产品入口展示生效状态。看到上述回执前保留原
消费者 Todo 为开放状态，不把已取消测试卡或合成回执当成真实批准。
