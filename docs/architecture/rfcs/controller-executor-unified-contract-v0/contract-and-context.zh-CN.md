# 契约与上下文迁移：Controller RFC 技术附录

本文属于[主 RFC](../controller-executor-unified-contract-v0.zh-CN.md)，保留其字段、读取、历史证据和切换要求，
不是新的 RFC、任务队列或另一套架构。先阅读主文的系统图和当前交付路径，再按需进入这里。
历史实验仍绑定各自 revision；其中的“当前”描述只适用于该记录，不能覆盖主文最新核对。

### C1 源码核对：已有共享 owner 与剩余分叉

当前完整来源 validator 也将可执行必读校验交给既有 interaction owner。显式
command／ordering 不得消失或引入不支持的执行顺序；纯文字和合法待执行义务保持
完整。同一规则无需调用方各加判断即可到达 quota、managed preview 和真实 Turn／Host
planning；直接完整 carrier 即使版本不支持，也交 typed reader 处理。
[配对检查点](../ledger/controller-executor-unified-contract-v0/2026-10-30-c1-source-convergence.zh-CN.md#可执行必读义务与原生-runner-的体积归因)
记录反例、兼容、维护范围及原生 runner 体积归因；不豁免未过体积门，不退役已有 reader，
也不证明安装、模型或成果收益。

绑定 Turn 的原生执行准备保留普通 managed claim 原引用，并在 CLI 压缩前捕获同一
Turn 的 ready 决策。Host 将该原件交给既有 saved-capture 展示/reader owner，不丢弃
它再要求模型重算 guard 才能取得当前契约。发布前须核对 Goal、Agent、Todo 和 Turn。
具备资格的原生复查保留已启用的 profile，使用既有延后展示路径；上下文交付不选择
结算归属，随后仅由 saved-capture owner 展示同一规范计划。
必需材料仍保持完整：消费完整内联 Goal，未内联时执行其声明的读取；ready 回执或
已保存文件不能代替消费。可选展示失败保留原摘要绑定的恢复引用；显式 off 不产生动作，
未绑定请求只可回读，不补造 Turn 或 claim。这只是有界的 Host 交接修复，不代表 C1 切换、已安装
采用，或模型费用与长程收益已取得资格。

源码实际共享的部分比输出名称体现的更多。`_emit_quota_result` 在
`_project_quota_cli_payload` 选择普通 CLI 压缩或 `buildTurnEnvelope` **之前**，
已经捕获完整决策。后者委托 TypeScript owner，Python 模块是传输 adapter。
Quota capture 与 Turn journal capture 都调用
[`decision_capture.py`](../../../../loopx/control_plane/turn_driver/decision_capture.py)
中的 `write_decision_capture`。原生上下文交付与显式宿主 handoff 也复用该模块和既有
context-delivery owner。C1 应收敛剩余的组合逻辑与消费者假设，不另建 capture service，
也不重写已经共享的差分与 native-resume 逻辑。

可选 host poll 也是该次原始观测的一部分。来源保真切片在 capture 前仅记录一次
`--record-host-poll`，普通 CLI、envelope 与原生上下文交付复用同一回执；恢复读取不再
轮询。后续 capture/handoff 失败的错误响应仍携带已记录观测，不表示缺少准入，也不
授权重放。默认关闭行为和不可读回执诊断不变。这只修复 capture 完整性，不代表统一
writer/consumer 切换或长程成本收益已取得资格。

独立的原生展示切片复用已提交的完整恢复原件作为 reader 目标，避免再次发布未使用的
wire 副本。Delta 与未包装输入仍保留各自的不可变文件，reader/lifecycle owner 不变。
同一 107,289 字节合成来源的 capture 写入从 216,754 降为 107,289 字节；完整 reader
字节及 3,937 字节的 warm delta 均不变。这只验证重复 capture IO 的消除，不证明模型
输入减少、完整成本收益、成果验收或 C1 writer/consumer 切换。
显式小预算下，原生展示还可省略重复的目录/选读快捷命令，保留完整恢复命令；
在其后追加 `--list` 或 `--select`，无须基线即可发现或读取当前事实。
原有 4,000 字节冷态、温态及压缩恢复用例保留完整要求、Stop、原身份和完整恢复；
这属于传输验证。

Journal 持久化是独立的来源保真前置。本次提议由 Python adapter 复用 context
delivery 的稀疏数值 token 收集器；两个 TypeScript 消费者复用同一来源数值校验器。
Journal 继续传递 JSON 对象，仅为整值浮点、带符号零和非安全整数增加元数据。
Journal owner 在原子发布前恢复这些数值，并在重放与不可变 plan 比较中保留其
区别。普通原生对象保留已有编码，不改写历史回执。

中间的完整 JSON 字符串方案，在合成的 155 万字符带引号正文上超过既有 2 MiB
请求边界；原对象路径能够接收同一负载。保留这一负面结果：稀疏替代须在相同
负载与上限下比较，包括数值密集时的元数据开销。该前置或组件字节减少，都不
构成统一 writer/consumer 切换或长程净收益的资格。

本次提议的 Turn 选择 reader 切片删除 driver 中 Python 的 adaptive 选择判断。
现有 plan、Host、Session、completion 和 settlement 调用方经既有 source-edit
传输复用 interaction owner 的 `selectedWorkTodo` 规则。此选择 adapter 仅保留
Python 标量与空白解码，原记录和数字值不变。这退役的是一条重复规则，不是历史
envelope 格式；完整 writer/consumer 切换和相对原 quota 的成果／完整成本资格仍
开放。新增 bridge 调用必须计入，代码收敛本身不证明成本降低。

本次与当前 main 的集成也核查验证夹具的来源。`read_fixture_todos` 原先将 attention
摘要作为 canonical 初值，捕获前就丢掉了正文 500 字符后的要求。现在复用现有完整
source parser 与 Todo normalizer。真实 File／SQLite CLI 用例将末尾条款带过完整
capture、子进程 Host 的产物、独立验证、结算和重放。保留原来两个截断失败；修复
的是测试输入，不是 runtime policy。当前尺寸与兼容失败仍开放。确定性产物一致和
CLI／RPC 字节实测，不构成模型理解、原生验收成果、总成本或完整 C1 切换的资格。

| 当前来源 / 义务 | 当前 envelope 处理 | C1 必须保留的归属与语义 |
| --- | --- | --- |
| `interaction_contract.agent_channel.work_context` 与未完成必读 | `work_context` 加 `required_reads`；相同内联材料可改用引用 | 一份完整要求/证据来源与可解析引用；保留未完成必读及其完成条件 |
| 选定 Todo、recommended action、action portfolio 与 planning horizon | `action`；`turnActionProjection` 进行引用迁移与文本压缩 | 一份选定工作/动作身份；保留完整可执行条款，区分摘要与执行指令 |
| `interaction_contract.cli_channel.settlement_plan` | 复制至 `writeback.settlement_plan`，包含原 effect 身份与条件步骤 | 原样复用 settlement owner；controller 对话和新 capture 都不另建扣款身份 |
| User/agent interaction channel、execution policy 与 task orchestration | 分为 `user`、`execution_policy`、`boundary` 与 orchestration 分节 | 保留通知与执行义务的区别、权限和停止 gate；不从正文猜测 |
| Replan packet、必读与分阶段写回 | 专门的 replan 投影与延后写回 | 保留事实/来源访问、policy 特定触发与有序变更；不统一打开 stride 行为 |
| Scheduler context 与执行归属 | 携带调用方执行上下文的 scheduler 投影 | 保留真实宿主 owner 与 ACK 义务；统一 schema 不转移 scheduler 权限 |
| 完整已保存决策、action signature 与 journal 绑定 | 来源 hash、投影动作 hash、captured-decision 引用分别存在 | 区分语义完整性、精确传输字节与回执身份，不互换 hash |

这份表是 owner 与义务映射，**不是完整字段清单，也不是等价验证结果**。当前投影有文本
预算、字段白名单和引用迁移；仅比较 action signature，不能证明完整来源条款与未知字段
都被保留。C1 实现必须按确切 base/head 枚举字段和调用方；未分类项阻止切换，不能以
假定的兼容视图为由悄悄丢弃。

对上述基线的合成探针展示了这一限制：两段前缀相同、尾部不同的长 `recommended_action`
产生相同的 action-signature document，且均返回 `matches=true`，但各自的
`source_decision_hash` 不同。未注册的顶层来源字段也有相同区别。反之，完整的已注册
settlement plan 在投影中保留，改变其 Turn 身份会改变 action signature。这些观察
排除了直接把现有有界 envelope 原样提升成完整契约的路线，**不证明当前发生权限丢失**：
recommendation 属于诊断，未知字段是合成输入，完整来源承诺仍能区分两份输入。
没有测量模型收益。

### 差分上下文

复用现有 context-delivery owner 与不可变 capture 机制。目标包含两个正交层，
二者都不创建第二个语义 producer：

- **Layer 1 — Progressive：** 面向模型的交付默认跨 LoopX Host 生效，包括没有
  Session Hooks 的普通 CLI、MCP/HTTP 和外部 worker。当前 Goal、适用的累计验收、
  失败/未知、权限、停止条件和未履行读取/结算义务保持内联。历史、来源正文和评审证据
  等重材料，只能经既有 typed composition owner 折叠，提供精确的当前 capture 读取
  路线，并明确依赖工作前的读取义务。相关性/schema 未知时保留完整必要材料。这是
  交付默认值，不授予额外采集或持久化权限。调用方没有可用且获授权的 reader 时交付
  完整内联上下文；本地文件路径不是可用的远端读取路线。Host 硬限制使两种路线都不可用
  时，明确上下文不可用并阻止依赖工作。
- **Layer 2 — Delta：** 对组合后实例进行精确编码，仅在 consumer/session、role、
  epoch、schema 与来源绑定已资格化的 Host 自动选择。Progressive 可以用 full 编码，
  完整内联实例也可以用 Delta；连同 Progressive + Delta、二者都关闭，共同验收四种
  组合。Host 没有合格连续性时，对 Layer 1 使用当前 full 编码，不丢失 Progressive，
  也不虚构 native session。

区分两种已有复用主张。**`captured_file`** 比较已验证且兼容的不可变落盘产物；
精确重建不要求模型先读旧文件、到达 EOF 或保留其内容。**已保留模型输入的复用**
只有在精确 consumer binding 下存在可信完整送达和 retention 证据时，才能省略此前
交付的正文。文件落盘、工具输出回执和模型自我确认不能建立这种证据。Session Hooks
只观察受支持边界，不授予新权限。保留删除、数组顺序、null、空值、原始数值和完整
字符串；未知字段在注册复用语义前保持完整。先发布当前完整恢复源，再交付可选视图或
差分；验证重建，Delta 加元数据不更小时选 full。Layer 1 也将最终序列化 UTF-8 视图、
读取说明和索引，与同次 capture 的完整替代交付对比；选择更小的有效交付，不能为满足
体积预算而裁掉必需含义。

当前权限、准入、取消、未完成必读、结算与 scheduler 事实即使未变也新鲜内联交付，温态
传输不能减弱正文义务。Delta 缺失/乱序、基线损坏或未知时，选择当前 full 编码/恢复；
读取截断或丢失时，从同次 capture 恢复必要范围。上下文压缩使下个工作 Turn 在新基线
epoch 中的 retention 失效；它不使现有 Turn 失效，也不要求预读旧包或立即全量重读。
保留原准入、恢复引用和一次结算/扣额。执行仍检查来源有效性与当前权限；恢复旧 capture
不能复活已取消工作。Executor 任务对话独立消费语义材料，不是 controller Core packet
换名后的 delta。

#### 跨 Turn 未读材料

当前不可变 capture 已支持精确选读。待补设计是跨交付显式发现未读材料，不是第二个
reader，也不是先读完整基线。扩展既有 context/readback owner 与原生 `session.json`
投影；其他 Host 使用相应 consumer binding。区分产物发布、模型可见交付覆盖、
retention，以及语义采用/验收成果。

`confirmed_read_sections` 是提议的覆盖投影；若保留 `consumed_pointers` 这个名字，
它必须从同一 owner 派生，不能成为第二本台账或暗示理解。证据绑定精确 source/capture
digest、revision、consumer/session role、epoch、schema 和选读 pointer/range。
仅凭可信 Host 对实际模型可见结果的观察确认覆盖。调用 `--select`、文件存在、reader
EOF、成功工具日志或模型自我 ACK 均不足。部分/截断/丢失输出保持未确认；选择父节点/
索引不能确认隐藏子节点。没有此类 observer 时覆盖为未知，发现保持保守。这种覆盖
绝不能成为 `captured_file` Delta 的新增全量读取门。

每次 warm 交付携带 **`unread_context_index`**（提议名称）：列出符合交付范围、未内联且
当前内容未确认或已变更的材料，包括被 Delta 省略的未变正文。给出精确当前指针、来源
版本/digest 和必读状态，不能凭摘要推断相关性。已确认范围只适用于观察到的版本；
内容变化、数组重排或映射不明须重新绑定当前来源并保守发现。确认送达不证明输入已保留
或语义已采用。

每项通过既有 `quota context-read --select`，绑定精确 capture SHA，从**当前完整不可变
`projection.json`** 独立读取，不依赖上一包、基线文件、模型记忆或 patch 链。派生的
`required-context.json` 或 wire 视图保留既有 owner 对规范来源的显式映射，不能混用
根路径和 digest。历史精确版本回查仍属于单独绑定版本的产物/读取契约，不能虚构路径
别名或借此静默使用旧事实。前轮材料仍属必需却不在新 projection 中时，由原 owner
带入当前 capture，或暴露当前获授权的来源/读取义务。合法过期、撤权或移除须显式保留；
必需材料不可用则阻止依赖工作，不能静默记作已读或永久保留禁止保存的原始数据。
外部材料仍遵循自己的来源消费契约。

#### 配置与迁移

下表是提议行为，不改变当前限定 Codex 的 `context_delivery` on/off 接口。演进既有
typed 配置 owner，表达两个层的决定；由 C1 在实现前映射精确名称和版本，不新增平行
配置存储或 packet 格式开关。

| 配置 / 调用方 | 迁移与生效行为 |
| --- | --- |
| 面向模型、没有持久偏好的调用方 | 该调用方 reader/回退及成本验收后使用目标 Layer 1 默认值；Layer 2 仅在连续性已资格化的 Host auto，否则 full 编码。在 rollout 合格前，普通 CLI/其他 Host 仍保持当前 full 行为。 |
| 显式调用参数/环境 off | 保留当前优先级，本次调用 full/raw 交付，不依赖可选 codec/覆盖台账，不改写持久偏好。 |
| 已持久化 off | 保留完整 off 路径及原读取/恢复义务，不能借“无偏好”迁移把它变为 Progressive 或 Delta。 |
| 已有 on、受支持 Host | 映射为 Layer 1 + 已资格化 Layer 2 auto 时，保留合格的原生文件/恢复语义，不增加模型全量读取前置。 |
| 已有显式 on、不受支持 Host | 兼容期间保留当前校验错误；按版本发布且调用方验收后才可提供受支持的 Layer 1，不能静默重释不受支持请求。 |
| 新的独立分层配置 | 独立遵守各层，包括 Progressive off + 合格 Delta auto；显式 Delta off 仍保留 Progressive；不受支持的 auto 回退当前 full 编码。 |
| 内部 raw machine 消费者 | 保留显式 `--context-delivery off` 契约，不把机器 JSON 换为模型文件引用，不修改偏好。 |

冻结已记录版本、实验配置与历史回执。扩大默认值属于行为变更，须验证真实调用方、
显式 off 隔离与失败/恢复；设计合并本身不改变默认值。

#### 交付验收

复用 C1/C2 真实入口和第 9 节对照，覆盖以下决定性案例：

| 案例 | 必须观察到的结果 |
| --- | --- |
| 无 Hook 普通 CLI 及受支持 MCP/HTTP/worker 路线 | Layer 1 经真实获授权 reader 或完整内联回退可用，不留下仅本地可用的悬空引用，不虚构 native epoch。 |
| 未变的未读末尾在下一 Turn 成为决定性约束 | Warm Delta 可省略其字节，但当前索引须暴露完整来源，后续实际选择/工作遵守恢复的约束；仅索引可见不能算成功。 |
| 变化/未读正文、数组重排、未知字段或 scope | 绑定当前来源，保留适用跨 Agent 义务及未知范围的完整回退。 |
| 部分页、工具输出丢失/截断、EOF 但无 Host 见证 | 不确认不可见字节或隐藏子节点；恢复缺失范围，未知覆盖保守进入索引，文件 Delta 仍可用。 |
| 压缩、role/session/schema/epoch 变化 | 使不兼容 retention/复用失效；恢复当前事实，不重放原准入、effect 或扣额，不预读旧包。 |
| 来源撤权、reader 不可用、材料合法移除 | 遵守当前权限/来源 owner，暴露丢失并阻止依赖工作，或使用获授权完整回退，不泄露旧数据。 |
| Full 更便宜、off 与旧配置 | 选择有效 full 交付，保留显式 off 和冻结版本，并观察来源身份及 raw machine 消费者行为一致。 |

正文文件缩减率主指标为 **`1 - V/F`**：`F` 是完整 capture 的实际 UTF-8 文件字节，
`V` 是同次 capture 所选视图正文文件的实际字节，节省字节为 `F - V`。绑定两个产物及
digest，wire/索引开销单独报告，不能改换分母；上文交付体积保护仍计入实际呈现开销。
净收益主张还须计入后续全部读取/恢复、Host/controller/executor 调用与延迟、实际
tokenizer/cached/output 用量、人工注意力和验收成果。保留不利案例和冻结预算。
包更小本身不能证明 token 成本更低或成果更好；第 9 节原 quota 单会话对照仍为主，
双会话成本单独比较。

### 按决策阶段交付候选上下文

Canonical capture 保留完整候选上下文，由同一 C1/C2 owner 按消费者实际要做的决策
交付。本节提出替换契约，不允许跳过已有必读项。当前 capability gate 将选择交给
Agent；`selected_todo` 投影或列表顺序本身不能证明比较已经完成。在消费者与恢复义务
一起替换前，保留 [#7557](https://github.com/loopx-project/loopx/pull/7557) 的完整
上下文修复。

| 决策阶段 | 依赖决策前必须取得的输入 |
| --- | --- |
| 选工作或重新考虑选择 | 完整、可发现的候选索引：身份/版本、原目标、已声明的 Goal 贡献、优先级、资格、归属、依赖/阻塞、当前证据/不确定性和同次捕获详情引用。缺失事实保持未知。比较备选项前读其完整详情；索引不能判明某项是否相关时，先展开再选择。不能静默只保留 top-K。 |
| 继续已准入工作 | 完整当前任务、原始和累计要求、适用的全局/共享约束、依赖、反证、当前权限与停止条件。注册的继续执行契约可延后独立备选项，同时暴露可能触发重新考虑的新资格/资格变化和证据。仍被要求在全部 runnable 候选中选择的 Agent，依然处于选择阶段。 |
| 重规划或验收 Goal | 展开全部受影响工作与验收，包括跨 Agent 依赖和冲突证据。范围未知时展开完整捕获依据；归属和之前的局部完成不能证明无关。 |

这些是消费者义务，不是三套 packet schema 或新 scheduler mode。Typed owner 按明确
契约决定哪些读取可延后，Agent/controller 负责语义选择。作者提供的目的或风险摘要
帮助发现，不能证明省略正文无关。不按关键词、领取标签或模型评分推断相关性。缺少充分
typed 事实时，比较仍可能需要所有正文；冷态选择不承诺压缩比例。普通工作默认展开当前
Agent 的 outcomes、让其他成果可发现是有用的；共享要求、依赖、冲突证据和未知关系
必须突破默认范围。Outcome scope 与候选选择保留各自 owner。

扩展既有不可变 reader，从**同次 capture** 批量读取所选详情，保留各请求项的版本、
来源、完整性、覆盖与游标。同 Todo 不同版本仍分开。详情缺失、截断、不受支持或变化时
阻止依赖工作，使用该 capture 的完整恢复；fresh guard 不能充当详情 reader。一次
读取可交付多个正文，受支持的保留会话内无需重复交付已验证正文。读索引不等于确认隐藏
正文已读。已保留模型输入的复用仅覆盖实际送达且保留于该消费者的正文；文件 Delta
遵循上文独立产物证明。新成为必需的详情必须提供正文，不能因为机器已经存有它而省略。
上下文丢失恢复必需模型产物，范围未知时扩大到完整来源。
历史 capture 保留原义务。读取回执证明覆盖，不证明理解、保留、新鲜权限或验收。

单会话 Agent 消费同一管线。Controller/executor profile 中，由 controller 比较备选项
并承担 Core 记账；executor 经既有 host 通道取得完整当前工作与授权材料。它不会成为
Core 客户端，也不用接收全部备选项来弥补 brief 的缺失。复用 capability 资格、
work-context/读取履行、capture/detail 与 delta owner，不新增候选存储、Python
相关性规则或逐项审批往返。

在相同输入上，与原 quota 单会话和修正后的完整上下文对照。覆盖：初始未展开候选末尾
的决定性约束、展示上限之外的有价值备选项、peer 证据变化、阻塞项变为可执行、同标题
不同要求、缺失元数据、上下文丢失及批读失败。实际选择与后续已验证工作必须遵守约束；
可重建 JSON 或 selected ID 不变不足以通过。计入从首轮输入到验收成果的全部详情读取、
分页、重试、恢复、两个会话的调用、延迟和人工介入；保留分阶段交付更昂贵的小任务与
冷态选择反例。字节与使用真实模型 tokenizer 测得的 token 分开报告。

交付仍归 C1/C2：先由真实 caller 验证共享同 capture reader 与必读恢复，再将该 caller
的阶段交付和义务一起替换，最后验证 retained delta 与可比成果/完整成本。复用
[#7560](https://github.com/loopx-project/loopx/pull/7560) 的 outcome scope 工作，
不能将 outcome 关系当成候选选择证明。
[#7587](https://github.com/loopx-project/loopx/pull/7587) 的诊断计数折叠是独立兼容
切片，不代表本次替换。每个切片可有独立 PR 与回退，不要求通用检索框架或启用双会话。

原生 quota caller 现已实现有界的验收材料切片：已知的累计目标、完整验收要求描述和
当前评审状态保留在必读视图；来源历史与评审说明/证据可以从原始不可变 capture 按需
读取。读取义务、全量/差分基线与恢复共同切换。选读详情不能履行隐藏的必读内容，
也不能建立完整基线。未知语义和签名 envelope 保持完整，关闭路径不变。Typed 路径
计划不让原始 capture 数值经过 JavaScript 往返转换。
若必读视图加详情索引的 UTF-8 序列化字节数并未减少，则保留全量交付和原始义务。
默认详情示例选择来源、说明或证据字段，避免重放整个 outcome。

原生 exec 与 code-mode fixture 还验证了同一工作 Turn 内连续两次压缩：充分的工作
摘要可以保留原始工作/结算身份继续推进，无须重放 packet 或重新准入。基线仍失效；
尚未完成的初次读取和缺失的必要事实仍需从同一 capture 恢复，新工作 Turn 则取得
新的 guard。这是确定性 inference 下的原生协议资格，不是模型遵循性或真实成果/
成本收益的证据。C1/C2 仍未完成：候选、偏好、Explore 分阶段交付、签名 envelope
迁移，以及可比验收成果和完整成本仍须分别验证。

### C1 wire 布局建议

目标是吸收 quota packet 的必要信息量优势与 TurnEnvelope 的效率优势，一起替换
二者。“完整”指必要语义完整，不表示今天 packet 中所有重复诊断都永久内联。下述
保留根路径的无损候选只是迁移对照，用于在消费者迁移时发现遗漏与引用断裂；给它改名
或转发全部旧字段，均不能满足最终设计。

逐字段、逐条指令盘点所属规则与实际消费者。在依赖它的决策之前，必须具备当前任务、
原需求及修订、相关证据、权限、必读、执行/停止条件和原结算义务。通过 typed owner
识别并消除已证实的重复表达与无关诊断噪声。稳定材料只有在保留证据已验证，或来源
可解析且绑定完整性、读取顺序明确时，才可引用或差分交付；缺失上下文须先恢复再执行
依赖工作。引用本身不证明模型收到了约束。不能凭正文启发式把未知字段当成可选或静默删除。

在同一规范契约内吸收 envelope 的有效特性：清晰的执行身份与动作组织、单一机器
binding、减少重复指令、有界交付与可靠详情/恢复。Full/delta/detail 是同一实例的
编码，不是永久并存的不同语义视图。原始来源证据保留在既有 owner，不再输出第二份
可执行 packet。最终布局可在引用映射、消费者迁移与实测收益成立时调整；保留根路径
不是不可更改的产品约束。

| 来源或关注点 | 候选映射与资格要求 |
| --- | --- |
| 迁移中的当前 decision 字段 | 无损对照保留路径和值；最终映射前枚举各模式，区分必要语义、已证实重复与纯诊断材料，不能凭某次 packet 推断可选性。 |
| Work context、必读与结算 | 保留既有 `interaction_contract` 路径与 owner。现有根相对引用保持含义；有意搬迁须显式迁移引用与 reader，不能按模式改写正文或命令字符串。 |
| Host-operation binding | 从真实调用方 execution context 组合，验证与源权限一致；binding 冲突时拒绝，不能选择方便的表示。 |
| 内容与传输完整性 | 对整个语义实例作完整性承诺，独立于原紧凑 action digest。精确 capture 字节校验和原结算身份仍分别检查；更换传输编码不创建新 effect。 |
| 完整 capture 与 Progressive 视图 | 区分规范 `projection.json` 与派生 `required-context.json`/wire 视图，盘点精确根路径、版本、digest 和指针映射；先保存完整恢复源，再交付呈现，仅通过可用且获授权 reader 引用。 |
| 文件复用、交付覆盖与 retention | 分别盘点既有 typed owner 及证据边界。提议的 `confirmed_read_sections`/派生 `consumed_pointers` 只表示观察到的覆盖，不是产物重建证明、理解或文件 Delta 全读前置。 |
| 未读发现 | 将提议 `unread_context_index` 映射到当前完整 capture 的指针、版本和来源/读取义务，包括未变的未确认材料；变化/未知映射重新绑定，保留显式不可用状态和权限检查。 |
| 历史回执与 reader | 未结工作按记录版本解码。临时内部 adapter 可以服务既有效果解释器，但不能成为第二份输出契约，也不能用紧凑副本替代模型必需输入。 |

一个合成布局探针使用真实隔离 File/SQLite quota capture 和既有 TypeScript context
codec。保留源路径时可使用 warm delta；将全部事实搬至新 `facts` key、同时沿用旧
`quota_packet` surface 时，以 `delta_not_smaller` 回退 full。两种布局均精确重建，
epoch 变化均要求 full。Retention 为合成声明；这衡量 codec 行为，不证明宿主观察、
模型理解或成果/成本收益。它排除的是**沿用旧 surface 却未映射的根路径搬迁**，不是
一般性的嵌套布局。若映射与消费者收益足以抵偿迁移成本，仍可设计嵌套布局。

因此 C1/C2 须随 wire 迁移显式版本化 context surface 及其 fresh/reuse 路径，不能
改名冒用旧 surface 或凭 Turn 身份继承基线。迁移时先保存兼容的完整来源，面向模型
视图可以已使用 Progressive；复用须具备上文相应的文件产物或 retained-model 证明。
资格覆盖旧 action 投影之外的字段、基线缺失、
session/epoch 变化和同 capture 恢复。这里消除了具体布局风险；下一步仍是共享 writer
与最终消费者替换，不能再建设一个布局框架，也不能据此宣布 C1 交付。

尚未完成的实现候选 [#7430](https://github.com/loopx-project/loopx/pull/7430) 已在既有
TypeScript owner 中、quota 与原生 Turn 捕获之前组合完整来源，并传入实际 Codex
stdin。隔离 File/SQLite 的真实 CLI 加合成可执行程序覆盖来源完整性、失败 attempt
恢复与一次结算。这推进了两个 writer 和最终输入替换，尚未取得 C1 切换资格。
候选现已通过规范 CLI channel 路由能力重入，并在完整契约的 Codex 输入中保留原始
hash 绑定的 review/writeback reader。合成 File/SQLite 执行验证了新 obligation 绑定
和原 Todo 绑定 replan 的材料读取。原生启动现只从已记录的结算身份取得唯一因果
绑定，观察到的语义 replan obligation 不得替换原 Todo 绑定。源 quota 与健康拒绝
仍阻止宿主启动。Release fixture 现只导入一次最终来源；这些可执行检查不代表真实
模型或已安装宿主验收。结构化宿主 renderer 现与 Codex 共享完整输入传输及原 hash
绑定的材料读取器。实际隔离 File/SQLite Turn→DSH runner 接收同一捕获来源；历史
请求 prompt 正文保持精确一致。结构化 DSH 仍使用 Todo 范围的 session：只有
obligation 的 replan 在派发前被拒绝，不伪造 Todo 绑定。完整来源有意保留未激活能力的
配置和诊断事实；这些事实不会激活 recall 指令或 reflection 结果字段。这是已披露的
展示变化，激活边界与历史等价分开验证。
Markdown quota 入口现渲染同一份完整契约，不再另外挑选事实子集。真实 File/SQLite
CLI 捕获验证普通入口与原 envelope flag 均保留全部字段和指令，JSON 类型与任意
来源正文经过渲染后不丢失。外部 worker 的 File/SQLite 准入、未决 attempt 恢复和
重启检查已通过完整 producer 输出；但不透明 shell wake 仍不能证明该原始契约已
交付 executor 模型。
真实 benchmark checkpoint preflight 已验证 File/SQLite、replan/非 replan 及
context delivery 开关组合下的完整来源相等；可选 full recovery 保留同一来源，初始
准入只有一次，reader 不写状态。这只证明契约传到 checkpoint 的模型输入文件，
不代表原生 session 捕获或 benchmark/模型成果。此前只要外层 source hash 相符，
`replan-view` 就会接受不支持的版本或无效的内部绑定；现在复用既有完整契约 validator，
未版本化的历史读取保留原解码器。
successor 写入指引现已在完整 capture 前绑定到已决定的 Turn，复用历史 staged
读取的同一格式化 owner。完整契约包含原 Turn/Goal 绑定，以及结束前完成创建回执
所返原结算、不得执行 successor 的义务；后续现代材料读取不再追加第二次绑定。
真实 File/SQLite 的 App heartbeat 和 generic CLI 测试从该内联契约创建 successor，
拒绝过早 spend，原身份只结算一次并保持 successor open，不依赖再次 guard 或
staged reader。这改变了完整输入的 successor 指引；未版本化历史读取保持原行为。
仅结算、capability intent、未绑定和 context 不匹配的输入保留原写入指引。
checkpoint 的旧 reader 测试本身不能证明这条生命周期。剩余材料 reader 退役及
其他 C1 消费者不能为满足历史断言而恢复第二份精简可执行事实视图。
完整契约边界现复用既有 decision/frontier 词表，在 capture 前与后续读取时拒绝未知、
空值或类型错误的根执行 action；hash 合法不能证明消费者理解未知执行语义。可选证据
仍完整保留，包括陌生版本和形似 action 的字段名；未版本化历史 action 路由不变。
这只验证根 action 边界，不代表所有必需语义字段均已验收。真实 File/SQLite 原生 CLI
的 producer 故障在 capture、派发与 spend 前被拒绝；前一候选会到达更晚的启动拒绝，
这些探针不证明此前存在无保护执行。Selected-work 测试现读取
完整 interaction context，并通过真实原生 `turn plan` 入口验证宿主输入。真实
File/SQLite 检查保留完整任务正文、规范 note、作用域 gate、需求新鲜读取及来源丢失
恢复，不把 App capture 换绑到原生宿主。此前六项旧根布局断言失败仍作为历史失败
保留；它们后续的语义检查现在已实际执行。
此前六项 staged-reader 失败作为历史证据保留。完整 composer 现于 capture 前
复用 Explore 既有可用性修正：已证明没有 Todo 的 pure replan 不再展示必须绑定
Todo 的结果附件写法。证据、独立图命令与全部工作前义务保留；陌生/自定义指令及
歧义绑定保持原文。有 Todo 的可用附件写法仍完整内联。这是可用性修正，不改变
权限，也不新增精简事实视图。真实 File/SQLite 原生宿主输入覆盖两种绑定。
Staged 测试现消费完整 CLI 输入，并对同一不可变 capture 运行历史材料 reader
codec，覆盖恶意 PATH、不可用 registry、身份/验收篡改及实际 writer。测试比较
全部 successor 绑定指令，不再排除完整输入已拥有的字段。附件拒绝 fixture 提供
合法、显式的 finding status，确保实际到达缺少 Todo 的身份门。23 项 staged
检查与四项原生宿主用例通过；这些相互重叠的范围不能证明全部消费者退役或打包
宿主资格。
原生 context fixture 现将两种 CLI 写法都绑定到 `complete_turn`，并将重建后的
全部事实与原始 capture 比较。首次四项失败源于恢复 oracle 仍使用旧 surface；
Unix rendezvous 检查此前只比较内容，允许 full 回退掩盖同一错配。修正后要求
原 target 和实际 delta 模式。十项用例通过真实 Codex exec/app-server/code-mode
及 worker resume，使用隔离 SQLite 与确定性本地回环响应，覆盖压缩、进程恢复、
历史文件替换和未合格 replay profile。未调用付费模型或启动 benchmark 实验。
当前完整 quota-settlement CLI 模块 158 项通过，包含历史 cache parity 用例；
此前间歇性错配的原因尚未证明，不能改写为历史通过。
File/SQLite context→DSH 子进程检查现使用完整 capture 和生产 request builder，
不再把当前 CLI 响应当作旧 action projection。普通 capability 开/关输入保留全部
来源事实；已绑定 replan 使用生产 capture binder，并执行实际交付的原证据 reader。
来源事实、schema 版本或 replan 义务被修改时，在 runner 接缝前拒绝；显式历史引用
reader 覆盖仍保留。八项聚焦用例通过，使用注入的记录 runner，未调用真实 DSH 模型；
本次测试迁移没有修改 runtime 代码。
完整契约边界现进一步复用既有 interaction decoder，校验必需版本、channel 类型、
通知策略和执行一致性。四种非法语义原先能通过组合；现在即使 producer 提供正确
完整性哈希也会拒绝。保留原对象及未知可选事实，不用 decoder 规范化输出替换原文。
File/SQLite 原生输入检查确认不支持的内层版本在 Codex prompt 执行或 DSH runner
加载前拒绝，且不扣额。完整契约/context/DSH/stdio 集合 352 项通过，TypeScript
检查及聚焦 lint 通过。这只证明上述交互语义，不代表所有必需字段或打包宿主已验收。
Heartbeat 模板现统一提供按顺序结算的执行要求；精确 Turn 绑定只补充 canonical
来源定位，不再重复执行和禁止静态重建的条款。Thin 将完整 capture 与所需上下文的
行动前读取合并，复用未变的进展／outcome 条款。原完整路由 fixture（320 字符
scope、显式 CLI、五项能力）中，未绑定提示从 4,981 降至 4,895 个预算字符，通过
原 4,900 上限；精确 Turn 变体从 5,198 降至 4,987，仍超过该上限。超长路由反例
仍拒绝；没有缩短路由、能力、fixture 规模或修改上限。提示／平台集合 115 项通过、
1 项跳过。这只证明展示和条款保留，不代表模型采用或费用收益。其余原 CLI 预算
仍有四项失败。与 opaque reader reference 整合还暴露两项过时的 digest 字段断言；
File/SQLite 消费者现读取已钉住的 machine descriptor，保留原 receipt，并确认
capture 字节改变后拒绝读取，不产生新 admission 或 spend。
剩余 cache 一致性调查、其他旧 reader 断言、分阶段指令等价和实际 optional/external
消费者、版本准入及打包宿主连续性仍未完成。缺少显式原生 runtime 的 operation SDK
检查属于未测，不能算资格。默认 writer 变更尚未交付；候选以普通 PR 进入评审，
整个 C1 采用和原 quota 净成本资格仍开放，尚无模型或完整成本收益证据。历史 effect 解码只是临时迁移，
不得成为第二份永久可执行视图。

### C1 消费链替换与切换单元

切换单元是一个受支持入口**及其下游消费者**，不是一个输出开关。对
`99921829f2c5` 的源码核对确认以下活跃接缝。这补充前述基线审计；它是迁移设计，
不表示这些路径已经改变。

| 活跃接缝 | 具体替换与保留的 owner |
| --- | --- |
| `quota.py::_emit_quota_result` / `_project_quota_cli_payload` | 在 capture 前组合完整契约，替换 ordinary/envelope 执行格式分支。准入、capability hook、capture IO 与 host poll 回执保留原 owner。诊断 status 视图不是可执行契约。 |
| `turn.py` 的 `build_fresh_turn_decision_owner().resolve()` 之后 | 携带实际 scheduler execution context 调用同一组合 owner。此入口直接调用 `build_turn_envelope`；仅修改 quota CLI emission 不会迁移 Turn。保留 Turn plan、journal 和回执执行器。 |
| `heartbeat/task_body.py::render_thin_heartbeat_task_body` | 用统一契约读取指令替换 `captured_envelope` 指令分支，保留精确 selection/reentry transport、通知独立性、必读与原结算身份。保存的 bootstrap 仍在唤醒时解析当前指令。 |
| `turn_driver/codex_cli.py::_prompt` 与 `host_candidate.py::extract_turn_authority` / `render_prompt` | 在实际 prompt 边界替换格式特定输入。当前普通 Codex 接收 envelope，分阶段 replan 使用提取的 authority，结构化 host 也使用提取的 authority。共享源字节或签名不能证明这些最终输入保留相同义务。保留 provider 结果编码与外层 effect 归属。 |
| `turn_driver/decision_capture.py` 与 `cli_commands/quota_capture.py` | 复用不可变 capture、校验读取器和 full/delta 交付。首次交付和保存后恢复必须重建同一契约；不能仅因 Turn 身份相同，就把某投影的 continuity baseline 用于另一投影。 |
| `benchmark/runtime/worker.py`、`benchmark/runtime/sessions.py` 与 `scripts/external_scheduler_worker.py` | 迁移它们实际调用的产品入口。保留评估配置与外层唤醒归属，不新增 benchmark 专用转换，也不把 managed-host 测试算作 attached-host 验收。 |

本提议的[私有 plan 检查点](../ledger/controller-executor-unified-contract-v0/2026-10-30-c1-source-convergence.zh-CN.md#当前私有-plan-边界)
成套迁移完整源 Turn writer 及其 capture、Host、CLI resume、原生会话、capability、
子任务结果和 journal 消费者。随后的 [quota 与 managed-controller 切换](../ledger/controller-executor-unified-contract-v0/2026-10-30-c1-source-convergence.zh-CN.md#当前-quota-与-managed-controller-切换)
还删除普通 presentation 的 v0 builder 分支与 managed preview 的临时 v0 producer。
两条当前路径消费既有完整源 owner，历史版本 reader 显式保留；单 owner 缺材料演练
到达两个真实 CLI 入口。这是未合入的源码提议；installed 采用、剩余受支持 reader，
以及相对原 quota 的验收成果与完整成本仍使 C1 保持开放。

[显式内联消费方检查点](../../../reference/contracts/interface-budget-contract.md#canonical-cli-fixture-qualification)
按已注册的 v1 载体验证普通 quota、保留的 flag／详情拼法及完整事务检视。off／Markdown
完整来源、native／structured Host 输入不缩减；历史 v0 reader 保留原版本边界。
同源 File／SQLite 与长路径证据支持展示预算重新定标，原失败、全部行数／增长／权限
限制继续保留。这是接受新增内联成本，不是效率收益、installed 采用或 C1 关闭。

Reward Memory 在原生 quota 与完整 Host 路径注册召回与反思时，只读一次已有类型约束
的能力事实，不再为判定参与资格读取整份 action signature。交付仍受已验证的 Goal/Agent
绑定、provider 策略和原 Host 来源约束。`capability_context.ts::contextSource` 仍按当前
工作和结算为原生 quota 的贡献绑定签名；退役这条活跃路径时须同时保留注册、回读和原版本
恢复。共享能力命名空间保护将 `work_contract` 与 `source_decision_hash` 保留给核心，
覆盖注册、上下文及可选结果字段；贡献被拒绝后，原 Host 来源仍可用。

可选 Host 投影现已把有序合并、冲突拒绝和逐贡献隔离收敛到该 TypeScript owner。
Python 只提取一次当前来源并发送一个批次，provider 结果及历史 capture／journal reader
保留记录版本。[已合入的 main 切片](https://github.com/loopx-project/loopx/pull/8451)
按 File／SQLite 对照零／一／两个 provider、当前／历史来源及接近大小上限的输入，
投影建议保持一致。在这一有界步骤中，两个 provider 的完整来源 RPC 从四次降为两次。
大批次复用现有私有快照运输，增加临时 IO，且实测在接近上限时变慢；仅扩展运输可接受
范围，不提高 provider 预算。这不构成模型 token、整任务成本或已验收成果收益结论。
原生 quota 签名退役、installed 采用和相对原 quota 的资格验证仍未完成。

[可选上下文来源的 main 后续修复](https://github.com/loopx-project/loopx/pull/8455)
让仅状态回读不依赖可执行来源提取，同时使声明了完整版本的 native 来源在参与事实或
指导读取前，由已有完整契约 owner 验证。绑定损坏、版本／codec 不支持或保留字段改变，
不得回退到 raw quota 签名。召回观察与签名的治理 payload 分开传递，不能仅为附上 provider
读回而改写来源。注册、命名空间、槽位和大小检查保留；有效指导与记录中的 Host 包装仍用
原绑定和规范结算，无效来源保留状态观察但不能交付指导。这增加完整 native 来源验证。
早期仅状态路径的 RPC 减少不证明模型窗口或整任务收益，native 签名退役仍未完成。

集成 main `5988be01` 保留共享的静默完成规则，没有恢复已退役的另一套 heartbeat
正文。该检查点的长 scope thin 与完整能力 CLI 夹具超过原 5,100 展示预算，捕获长
路由的 Agent JSON 超过 5,400 上限。一次临时展示预算扩容因削弱既有
超长 scope 负例且未解决独立 JSON 失败而被放弃；完整指令和原上限均保留，继续按
同一 caller 完整修复。这些失败与被放弃的试验保留为历史证据，以下修复仍由原上限
约束；独立评审、原 quota 成果及完整成本资格继续开放。

当前 Todo 备注现已采用 [main 的字段 registry](https://github.com/loopx-project/loopx/pull/8386)，
C1 删除自身独立的备注规则和重复的原生回归。共享身份绑定 Goal、registry/runtime 路由、
canonical provider 和当前 Todo；新鲜证据更新不会使未变的备注正文失效。原生 CLI 回归
覆盖完整／部分交付，以及同一 Todo 仅修改证据。当前任务与权限事实仍由既有来源和 reader
提供；缺少已观察覆盖、正文变化、更换 Todo 或上下文丢失时，从同一 capture 返回完整备注。
C1 已校验的精确 write-scope 与 Vision 指令复用继续作为同一 registry 中的独立字段。
这一收敛不认证模型保留、installed 采用，或相对原 quota 的已验收成果与完整成本。

失败会话重试也是活跃 reader。本提案中的
`session_recovery.py::assess_failed_turn_retry_request` 为 v1 请求向绑定回调交付
校验后的完整决定；已记录的 v0 请求仍交付原 envelope，即使同时携带完整来源副本。
无效或混合的当前输入在 provider IO 前拒绝，返回的绑定仍由既有 lineage owner 检查。
File／SQLite 资格验证须使用实际读取入参的回调，确认拒绝不改 capture／journal 字节，
恢复原 Session 且不重复 Host 结果或结算效果。这一 reader 修复不证明 installed 采用或
C1 已完成退役。

替换任一接缝前，实现 PR 记录源路径、字段或指令条款、当前 reader、目标 owner、表示、
恢复与退役条件。字段搬迁须包含引用解析；删除诊断须证明其中不含动作、权限、必读或
验收义务。未知数据不得静默消失；未知的必需执行语义也不能靠转发 JSON 就变安全，
相关操作须保持不可用，直到兼容消费者能够理解。不能用子串匹配分类义务。

使用**一次明确的版本迁移**，不在每次唤醒自动协商格式。已迁移 binding 的新准入要求
组合、交付和消费 reader 兼容。不支持的消费者在 dispatch 前收到可操作的不可用结果，
不能因此创建第二个活跃 writer。已准入工作继续使用记录的版本和原回执；在这些工作及
受支持恢复窗口核清前保留历史 decoder。回退同样区分未结工作与新准入。版本迁移不能
把保存的 prompt、journal 或源 hash 改写成仿佛一直使用新契约。

退役须区分读取历史证据与恢复执行。缺少已验证 live-guard 绑定的旧原生认领前 capture，
即使原 lease 仍有效，也保持只读。先恢复原始字节、原 claim 回执和当前 lease；当前
检查取得资格，不能使旧 capture 变成新 claim 的证明。重复 Hook、重启或不同 ambient
session 都不能补出缺失绑定。已记录的 ready 结果不能续期已释放、过期或被替换的 lease。

| 保留记录 | 恢复义务 | 该记录不能证明什么 |
| --- | --- | --- |
| 不可变 capture | 按原版本和摘要读取原始字节，包括原 Turn。 | 当前执行资格、模型保留或替换来源的权限。 |
| Managed claim 回执 | 读取原 operation 及其结果，不派生另一个 claim 身份。 | 新 claim 代次或续期后的归属。 |
| 当前 lease | 通过既有 lease owner 读取实际归属、代次与有效性。 | 仅因 owner 相同就取得已验证的原生 capture 绑定。 |
| 已记录的 ready 上下文 | 保留原来源及其恢复 reader。 | 新派发、自动重新认领或另一次结算／扣额。 |

一次源码演练使用真实 producer `172d80d630f4`，再分别由 main `5f132dd66585` 和候选
`f42dfd592e4f` 读取。两种 reader 各通过 14 项 File／SQLite 用例，覆盖就绪前中断、
已记录就绪、有效／释放／过期 lease，以及 ambient／原生 session 变化。重复公开 Hook
与重启保持原 lease 代次、claim 回执、回执游标、capture 字节和 rollout log；当前
reader 可从旧 capture 恢复原 Turn 与 Todo。这是隔离合成源码旅程，不是安装态 Host
采用、模型执行或完整 journal 恢复资格。

该边界须用受支持旧版本真实生成的 capture 和回执验证，不能只用当前代码构造的旧形状
fixture。在删除 decoder 前核清剩余已准入工作、实际安装态 reader 和恢复窗口。安全
只读恢复本身不代表该执行已取得自动升级资格，也不代表最后一个 reader 已退役。

代码收敛属于每个迁移切片，不只留给最终 C9 清理。两个入口须共用 composition、引用
校验、上下文重建与结算的语义实现。真实的宿主 launch/observation 差异留在薄 adapter；
不为强行统一不同生命周期而另造通用 runner。各项决策仍由既有 typed owner 负责。

每个切片记录迁移前后调用点、共享 owner、已删除的重复算法，以及剩余兼容 caller 和
退出条件。两条路径共用独立编写的语义用例，并分别验证真实传输与恢复。仅委托调用的
wrapper 可以过渡保留；给公共模块起新名字却继续保留两套规则实现，不算收敛。下一次
修改共同规则，应只改、只测一个 owner 并传递到两条路径。代码行数下降不是验收指标，
也不能为此删除宿主独有的验证覆盖。

C1 首次跨入口切换必须覆盖上述两个生产组合调用点，并交付至少一条从生产端到最终输入的
完整旅程，同时明确剩余 reader。这个有界切片通过资格，不代表可向未验收 host 开启
新 writer，也不关闭 C1。最终切换须在全部受支持 reader 验收后删除活跃格式选择。
临时 decoder 有具名 caller 与退出条件，不是面向用户的兼容视图。

Turn 生命周期可以在 C1 父提案下单独评审，但须一起覆盖规划、最终 Host 输入、capture
发布、journal 检查、原会话恢复及结算消费者。已迁移 Turn 使用一个完整来源及其已校验
reader；这个分阶段边界保留普通 quota writer 和 thin-heartbeat 的现有契约。共享投影
改动须保留这些未迁移 caller。历史 v0 decoder 服务已记录的 plan、request 和 journal，
不能为非法的已提供 v1 来源兜底；其删除仍依赖未结工作及受支持恢复窗口核清。

这属于 C1 的部分交付，不代表跨入口切换取得资格，也不证明上下文或成本收益。父提案
仍负责普通 quota 与 heartbeat 迁移、最终输入／材料读取覆盖、全宿主配置，以及原单会话
成果与完整成本对照。评审须包含匹配基线／候选版本的真实 provider 成果、拒绝敏感性和
打包恢复。只有切片合入 main、父提案集成 main 后，父提案的差异才会减少；开 PR 不算。

对 normal、selection、replan、wait、recovery 和 capability-off 的每个 fixture，
在**三个边界**核对独立编写的义务：组合契约、重建交付、最终模型输入及必需材料读取。
包含旧 action signature 未覆盖的源变化、必需来源缺失、不兼容消费者和结算响应丢失。
仅重建相等会漏掉 prompt 层丢义务；仅 prompt 完整会漏掉过期权限或重复扣额。
这些是集成要求，不授权运行模型或启动实验。

1. 盘点实际 producer、reader、writer、已保存 prompt、capture 和外部集成。把每个旧
   字段与正文义务映射到保留的 owner/字段、权威派生或明确批准的删除。“事实相同”的
   总括声明不够；保留原失败 fixture。
2. 通过真实 quota、thin-heartbeat、Turn 入口验收统一契约的单会话执行。每个 binding
   只切一个 writer；旧 reader 是绑定版本的显式迁移 adapter，列出真实调用方与删除
   条件，不能成为长期产品视图。
3. 加入按角色区分的 session、共享生命周期复用及 opt-in 双会话 profile，复用既有
   same-session/native-resume 和 effect recovery 代码。
4. Benchmark worker 与打包 App heartbeat 采用同一产品 profile。各 host 分别证明
   观察、停止和恢复；CLI 测试或模型模拟不能证明已安装 App 合格。
5. 最后一个受支持活跃 reader 迁移后，删除旧 producer、格式选择开关、重复 renderer
   与双格式测试。只为受支持历史回执 replay 保留版本 decoder。终态验收要求只发出一份
   契约，不再选择 quota 或 Turn packet 格式。

回退先关闭受影响 profile 的新准入，drain/中断 attempt，核对回执，再在下次准入工作
恢复先前合格 profile。不能静默替换 native session 或重解释 pending 回执。删除字段/
decoder 前证明 export、旧版本 readback 与降级行为；满足受支持保留/升级边界前保留旧
reader。本文不单独授权破坏性迁移。

#### 已记录验收索引的兼容边界

分阶段 Turn 切片让已记录的未版本化／v0 replan 读取继续使用原计数索引和 12 KiB
索引预算。完整 v1 读取保留当前 owner／work 事实及按精确状态分组的验收条件，并通过
绑定摘要的明细 reader 提供完整原 outcome。分组只是投影，不是独立验收证据。此前
丰富索引的测量及其 20 KiB 候选预算继续作为完整来源路径的证据，不提高历史 reader
的预算。

#### Turn 消费方准备与指令保真

已独立合入的 Turn 切片让普通 quota producer 保持已记录契约；本 C1 父提案也迁移
quota／heartbeat producer，已记录的请求仍由原版本 reader 解读。当前 Turn 规划直接消费完整来源，
发布自己的不可变 capture；新组合来源与较早 executor envelope 错配时继续拒绝。

本 C1 切换让原生所选 Task 的准备阶段把同一经校验的完整来源直接传给
`StewardTurnHost.prepare_native_task`，并通过 `work_contract` 交给其既有验证 receiver，
不再为这个共同部署的准备调用点重新生成临时 v0 adapter。普通 lease 准备仍先于
delivery guard；准备之后重新读取同一 Turn 和 Todo，再进行规划。已记录的 v0 请求
保留原版本恢复 reader；当前 caller 的迁移不改写旧请求，也不为它们重新准入。
只读身份的准备阶段复用既有的已校验来源 lineage 操作；显式提供 adapter 或 Host
header 时仍须完整验证。读取身份本身不构造 Host 请求，也不授予执行权。

原生所选 Task 在 kernel delivery guard 之前准备普通 managed lease，并在模型后验证时
复用该 proof。claim 标签本身不足，不能借用另一次执行的 lease。来源检查先于独立
Task argv；拒绝和原结果恢复保留原会话与一次性效果。

完整 Turn 的 capability 建议及其可选 Host 结果字段绑定到经校验的来源内容哈希和
Turn key。所选 Todo 的 required capabilities 一旦变化，即使旧 action 投影相同，
建议也会失效。为同一决策发布 capture 地址时保留绑定。重试时，Session 解析、
Host 输入、缓存结果的任务校验和结算都使用日志里的原计划。日志里已校验的结果仍能按
历史绑定读取原有受限结果 schema。新调用 Host 时，建议须通过完整绑定校验。
普通 quota 与历史 v0 继续使用各自已有的 decoder。

现代 Codex 与结构化 Host 指令共用既有 audit 读取规则：在判断 Goal acceptance 或提出
closeout 前读取已声明 audit，即使 Goal 保持开放。通知策略不授予或取消执行权；所供
delivery、attempt、quiet-noop、execution 与 orchestration 约束仍须遵守。状态、quota
和 scheduler 效果仍归 Core。完整契约可以声明待执行的原材料读取：发布不等于已读，
测试须真正执行 reader，并保留原正文与停止条件。

一次性 research-team launcher 与 initializer 共同定位实际加载的 Core，包括隔离 wheel，
仍保留 runtime 独占创建。源码和打包消费者验证，分别区别于已安装 App 采用、真实可选
provider／模型行为及相对原 quota 的完整成本收益。这些修复不关闭 C1，也不授权部署或合并。

独立合入的 Turn 切片验收以下 inspection 和恢复边界；C1 的调用方测量仍按其
原版本单独记录。

默认 JSON Turn inspection 保留当前路由、权限、停止条件、所选工作与可执行的 Vision
编写指引。支撑证据与诊断正文仍可从同一完整来源的不可变、摘要绑定 capture 读取；
之后实时 Goal 改变也不能重定向该读取。私有 capture 不可用时回退到完整来源。
投影仅供 inspection，不能提升为执行或来源 admission。拥挤默认入口仍保持既有
26,000 字符上限。显式 v1 事务详情包含完整来源及事务、会话元数据；同一小型 fixture
实测 26,886 字符，保留旧 v0 15,000 上限失败记录，仅该声明的 v1 详情采用
30,000 字符上限。冻结的差分 caller 按各 checkout 支持的 Host argv 和记录的计划
schema 测量；本拆分中的普通 quota 保持不变。
同源迁移对照检查路由、actor／work、执行义务、范围／停止条件、必要读取、编写指引和
效果。旧 Goal 文件读取与新版完整内联文件必须有相同的完整文件摘要；inspection
详情的每次 reader 调用及字节另行计入。仅这一次合格的 v0→v1 迁移允许默认入口增加
2,000 字符／字节，完整事务详情增加 16,000；后续 v1 增长和绝对上限仍受约束。
这是迁移资格验证，不是上下文效率净收益结论。

只读 capture 发布复用既有跨平台私有 IO owner：新文件设置私有 POSIX 权限或受保护的
Windows DACL；不安全的既有 transport 目录直接拒绝，不自动修复。它不启用原生
连续性，也不改变原生连续性的平台资格。保留 Windows 原生产者覆盖，并与本地
POSIX 执行证据分开报告。

失败 Session 重试也在既有完整来源校验之后，按请求记录版本传入回调参数：当前请求
传入经校验的完整来源；历史 v0 仍传原 envelope，即使附带完整 sidecar。损坏或混合
输入在 provider lookup 前拒绝。真实 File/SQLite 重试测试让回调实际读取参数，覆盖
原 Session 恢复、actor／work 错配拒绝、保存来源不变及 Host／结果效果只发生一次。
这些检查不证明真实模型采用，也不证明父 C1 相对原 quota 的完整成本收益。

#### 已就绪的渐进首读只展示一份结算步骤

仅当 `next_cli_actions` 逐字重复已识别的有序结算计划，且计划与就绪 Todo 和原效果
身份相同，渐进 reader 才省略这份副本。新生成的普通、progressive 和 native 完整来源
保留同一份共享 v1 计划，包括写回／扣额的执行者、原命令、顺序和前提；交付开关不再
选择第二套结算策略。显式 `refresh-state --settle-turn --settlement-receipt` 操作仍遵守
既有权限与恢复契约，展示开关不会把它加进计划。已记录的 v2 capture 由已校验的历史
reader 保留原步骤和身份，不改写或重新生成。不同的备用命令或未知计划继续展示；
原字段仍可精确补读，直接输出不变。

这一切换删除候选专有的结算开关、能力绑定对未来结算变换的预测参数、闲置的 native
结算投影操作，以及未发布的压缩来源桥接分支。组合逻辑复用既有原文传输和 typed 结算
owner；携带任一已退役参数的旧 live adapter 明确拒绝，历史 capture 解码另行处理。
真实 File／SQLite 回归在同一原 Turn 比较 off／on／auto 与有界旧展示，检查旧 capture
不变且没有扣额；显式组合命令成功、部分失败和一次性重放仍独立验证。旧候选少一次
调用的局部节省不再适用于当前候选；模型费用和完整 C1 验收仍未取得资格。

#### 完整 quota consumer 资格与按需分节恢复

main `36289306`／C1 `94cd16bb` 的固定 98 行对照保留原 28 行失败。新 consumer
先验证完整 v1 Markdown carrier 及 19 项原始当前控制值，再给予有界、一次性的
carrier 增长余量。相同版本增长、变化／缺失控制继续受约束。新资格下没有机械门
失败，仍有 28 行显式语义／成本审阅；生产者实测 stdout 成本未变。见
[预算 owner 的决定](../../../reference/contracts/interface-budget-contract.md#complete-quota-carrier-differential-qualification)。
该控制子集不证明完整来源等价、模型采用或效率净收益，不关闭父 C1 验收。

候选现在迁移普通 Markdown `--turn-envelope` 调用者：省略 delivery 或设为 `auto`
时使用现有完整 v1 Markdown reader，与普通 JSON 及未带该标志的 Markdown 一致。
格式不再选择有界 v0 视图。`compose_quota_cli_source` 调用同一 typed composition
owner，只捕获一次结果，并原样交给现有 renderer；没有新增 writer、decoder 或结算规则。
普通 Markdown 结算检查在隔离 File 和 SQLite 上执行实际输出的命令：提前扣额及
不成立的进展被拒绝，先持久写回再扣额，重放仍只有一次扣额。原生 Codex profile
继续要求 JSON 和捕获目的地；这次普通 reader 迁移不把原生 Markdown 变成可执行传输。

| Reader 边界 | 候选行为与退役条件 |
| --- | --- |
| 普通 Markdown，省略 delivery／`auto` | 完整 v1 块，保留每个捕获字段及原绑定。File／SQLite CLI 检查将其完整解码内容与不可变 capture 对比，再恢复同一来源，不重新准入或扣额。 |
| 显式 `off` 加 `--turn-envelope` | 保留同一次组合产生的有界兼容展示。这个显式调用者仍使用 v0 展示适配器；普通 reader 迁移不删除它。 |
| Native 与 managed handoff | 保持经校验的完整来源展示和原恢复合同；Markdown 格式不降级 handoff。 |
| 已记录的 v0 capture、plan 与 request | 保留原版本完整性／恢复 reader，直到未结准入工作及受支持恢复窗口核清。无效新来源不能选择该回退。 |

这改变候选的默认 Markdown 格式合同。完整正文可能显著大于旧动作摘要；现有同源
progressive reader 仍承担有界材料交付。不能把 reader 退役说成 stdout 或模型成本
节省。安装态 Host 采用、独立 exact-head review，以及原 quota 单会话成果／完整成本
对照仍待完成。显式 off 与历史恢复是各自受支持的边界，不代表活跃的第二个决策 owner。

模型行为评测沿既有版本／完整性 owner 读取显式提供的完整来源，实验臂标签不再决定
是否使用 v0 字段指令。两臂的 schema 和保密边界均在任一 actor 调用前检查完毕。
留存捕获读回保留原绑定和数值表示，损坏的新来源不降级为历史 reader。[独立主干切片 #8468](https://github.com/loopx-project/loopx/pull/8468)
保留 main 的 raw/v0 corpus 和 actual-default portfolio；C1 候选默认值仍属于本提案。
本地 API 和脚本化 provider 验证不证明模型采用，也不证明原 quota 成果／完整成本收益。

薄启动提示现将 selection／replan 和缺失详情导向返回的 read／recovery 指令，删除
重复的字段布局与分节选择规则。既有 Core work-context 指令负责必读和新鲜度；原生
navigation 负责所需分节与同 capture 恢复；受支持的 v0 capture 引用保留完整性和恢复
指令。不新增决策 owner 或 reader 格式。绑定 Turn 时间戳仍保留共同结算指令，
两种视图均不另持结算方案。

| 义务 | 修改启动提示后的交付 |
| --- | --- |
| 依赖工作前读取适用 Goal、验收、约束与停止条件 | 启动提示保留该顺序，Core 继续交付当前来源与 pending required reads；摘要不得替代完整需求。 |
| Selection／replan、缺失或截断的正文／命令及详情引用 | 启动提示将这些需要导向返回指令。原生 navigation 选择必要分节；有界引用从原来源恢复省略内容。 |
| 丢失上下文时恢复，不重新准入或重复效果 | 启动提示保留原 capture／identity，并禁止为缺失详情重新准入；原生 navigation 保留 Turn／capture／effect 身份；历史引用要求完整性核对并禁止盲目重放 guard。权限实际变化仍遵守既有新鲜度检查。 |
| 显式全文、capture／handoff flags、有序结算与不确定效果 | 启动提示及同一返回契约保留原义务；时间戳绑定不会另换来源。 |

相同完整能力 CLI 输入现满足未改动的 5,100 字符上限：动态 Turn 从 5,293 降至
5,087，绑定 Turn 从 5,160 降至 4,954。较长路由负例仍超限；scope、能力声明、
可执行路由和其余正文均保留。File／SQLite worker 检查实际交付的 Core 指令、受支持
有界引用、选择／重入、原 capture 回读及只结算一次。Reader 检查保留摘要校验、缺失
指针、截断／分页和同 capture 恢复失败。这只验证 caller 边界，不证明自然模型理解了
引用式指令；措辞和成功 fixture 都不证明 retention，也不授权重新准入。原 quota 单会话的成果／完整
成本、真实 caller 退役、维护资格及独立审阅仍开放，之后才考虑双会话实验。

#### MCP 最终消费者边界

完整 v1 的 MCP 交付沿既有完整契约 owner 和数值传输 tokens 验证原来源，将 CLI
原始 JSON 原样放在第一段原生文本块，既有 `host_mcp_interaction_v0` channel 放在
另一段。字符串 structured result 仍承载同一原来源。Claude／Kiro／Kunlun 共用的
MCP adapter 负责这层传输，不新增决策 schema、不改变准入，也不给宿主改写后的
来源重新签名。

针对该消费者，退役完整 v1 在绑定后插入 `mcp_channel`、删除 CLI 结算字段的分支。
原始完整结算步骤与身份继续作为来源事实保留；host channel 指定由 `complete_task`
执行这些步骤，而非再次手工 refresh／spend。独立 replan 仍遵守当前原始 CLI 动作。
无版本输入和已记录 v0 保留兼容投影。显式新版本、绑定或数值表示无效时，在 MCP
成功交付前拒绝，不降级到历史分支，也不为此重新准入。

真实 stdio 检查覆盖 `should_run` 和 `list_todos`、File／SQLite authority、未绑定
和已绑定 Turn、原样来源正文、原始结算身份及无效来源拒绝。完成／恢复检查另行保护
claim、writeback 与只 spend 一次；成功读取不等于结算。复用 main 的 poll 恢复资格：
受支持的 v0 展示可不同于完整 capture，但其 reader 必须恢复同一原来源，且不再次
poll 或 dispatch Host。

这是最终消费者完整性资格。恢复完整来源内容可能比先前无效的裁剪响应增加字节；
须计入 MCP 全部 content 和 structured result，不能只计第一块。自然模型采用、
原 quota 成果／完整成本收益及已安装版本推广仍未获得资格。

### C1 基线边界与复用盘点

基线是**原路径中调用者实际收到的 quota 交付**，不是内部原始 decision。
`cli_commands/quota.py` 的原 emitter 先捕获 source；有 capture 时按条件应用
`replan_cli_view`，再应用 `compact_quota_should_run_cli_payload`，随后原生上下文
交付还有 full/delta 传输。两组须记录精确版本、host flags、capture 是否可用及 detail
选项。用更大的内部 source 对比紧凑候选会夸大节省；只用延后读取的首包对比完整候选，
则会漏算基线的后续成本。

现有路径已包含值得复用的效率行为。C1 应把已合格的语义吸收到共享 TypeScript
composition/context owner，再退役旧的 caller-specific producer。不能直接套用整个旧
compactor：精确共享、省略和延后读取的信息与失败后果各不相同。

| 现有信息／变换 | 融合处理与决定性检查 |
| --- | --- |
| 已选工作、原始要求、验收、证据、权限及停止条件 | 依赖它们的决策前可用。保留不同 revision 和矛盾证据；lane 标签或 action signature 均不能证明完整性。 |
| 相同验收副本、嵌套 vision audit、authoring 和可选指导 | 在更小时优先使用契约内一份完整 owner 加精确引用。比较 JSON 类型及每条义务；不同或缺失 owner 时分别保留。不增加补读或决策权。已有 projection 测试只提供特征验证，不代表新消费者已合格。 |
| 有限 Todo lanes、缩短的建议、省略的边界／历史项 | 视为主动省略，而非无损去重。保留完整性／计数事实和同 capture 恢复；证明被省略项不影响当前决策，或先加载再决策。旧显示上限不能自动变成语义上限。 |
| 捕获后的 replan review、验收详情和 writeback reader | 区分决策事实与后续 authoring 格式，同时保留原身份和全部必要含义。读取绑定同一 capture；计入每个必需阶段、错误和恢复。引用缺失／过期不能重新 guard 或授权工作。 |
| Full/delta/resume 交付 | 在 composition 后复用既有 continuity owner。行动前验证精确语义重建与必要材料可用；计入冷回退及丢失基线的恢复。 |
| Scheduler、准入与结算身份 | Capture 前只有一份权威实例；传输保留真实 host owner、原 Turn 与一次性 effects。重复表示不构成削弱检查的理由。 |

单独后移验收共享不自动构成有价值的前置切片。以主线
`99cfa3ac559bfa237f04f36fb7e926672fea44b2` 为基线的 source-level 受控替换，
只把该规则从本地 CLI compactor 移到已有 `reference_context` 调用。同一份含 100 条
不同要求的合成 fixture 中，序列化投影输入从 102,680 增至 344,010 字节，调用者可见
输出仍为相同的 102,189 字节。普通路径仍调用一次；全部 detail 开启时由本地共享变为
一次请求。这些是投影参数测量，不是带协议包装的流量、延迟、模型计费或完整 C1 对照。
保留此负结果，不能宣称净效率收益。

兼容 owner 也为验收、Vision audit 和 replan authoring 共享复用一份本地 JSON 序列化
身份。不能用 Python 普通值相等，把不同布尔／数值、整数／浮点表示或带符号零替换成
另一条观测的别名。完全相同的副本仍共享同一响应内的 owner，包括键顺序不同的情况；
不同事实保留全文。这修复来源保真，不增加投影 RPC；不代表统一 writer、最终消费者
或相对原 quota 的成果／完整成本收益已经合格。

先在当前兼容 owner 修复被迁移的验收引用，不引入上述额外传输。已有别名只跟随本次
投影实际迁移的内联 owner，包括先访问别名、后迁移 owner 的情况。保留完整值、原始
数值类型、carrier 顺序和现有大小判断；不推断任意引用链，不重写已存 capture。
完整 C1 composition 应在持有来源的边界吸收该规则，并随调用方退役 Python 兼容
算法，不能把所有验收副本反复传过另一条 bridge。Writer／consumer 切换和原 quota
成果／完整成本门继续保持开放。

候选 `895f38fc5` 的固定 source 合成 replan 审计说明了边界的影响。关闭 stride 且不做
capture 分阶段时，原始 source 为 21,461 个紧凑 UTF-8 JSON 字节，原 quota CLI
projection 为 20,543，完整候选为 21,738：**相对 CLI 增加 5.82%**，相对原始 source
则是 1.29%。启用 capture 分阶段后，entry/review/writeback 合计 21,882 字节，候选
为 21,821。启用 `evidence_conditioned_v1` 时，分阶段合计 28,558，候选为 33,368。
这里使用既有合成 replan fixture、生产 projection/read 函数与合成 capture 路由；
不是实际模型轨迹或安装宿主实验。追加的验收／Explore 读取、实际请求包装、缓存行为
及成果差异均未测。切换 legacy receipt 得到相同数据，不增加独立 workload。这些数字
不支持净收益结论。

下一实施切片应先在 composition、reader 和 recovery 中验证同契约精确共享，再用上述
可比负载评估延后详情的取舍。冷启动与分阶段总量分别保留，保留负结果，不能靠删除独有
指令达到字节目标。

同时报告字段/条款 diff 及其决策后果、实际模型输入、cached/uncached tokens、追加
读取/往返、重建、延迟、失败/重试、验证与人工介入。首包更小但总补读更多，可能是负向。
只有必要语义保留且同等验收成果的完整成本降低，或可比预算下验收成果改善，才判正向。
效果混合则报告取舍；只有传输通过或成果未测时结论仍不确定。按既有测量合同在可比实验
前确定数值标准，不凭空规定压缩比例。C1 切换须完成这个 quota 基线对照，不能仅凭
serializer 或字节减少结清。

使用[既有测量模型](../long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model)。
计入两个会话的输入/输出/cached tokens、工具及材料读取、planning/replan/recovery、
墙钟时间、验证、人工注意力和 provider 成本；未知用量不是零。持久化因果归因，转发
产物时不重复计算成本。比较可比预算下的已接受成果，不能只看 replan 次数或 executor
速度。另行授权实验前冻结标准，本文不预设胜出方案。

在后续双会话对照中，除 executor 工作，还要归因 controller 推理及全部交接／材料、
验证与恢复成本。收支平衡问题是：在保留可比验收成果时，这些**新增完整成本**能否被
避免的控制／记账、重复执行或人工介入所抵消。即使两会话读同一产物，也按实际重复
模型消费计量；转发同一用量回执不能把该回执重复计费。报告不同 workload 的取舍和
账单／缓存覆盖未知。本文不规定通用开销阈值、压缩比例或每条消息 token 上限；此对照
不替代前置的原 quota 与统一单会话验收。
