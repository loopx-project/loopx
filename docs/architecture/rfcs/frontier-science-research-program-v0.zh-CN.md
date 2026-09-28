# RFC：前沿科学研究计划 v0

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** Proposal；没有研究 treatment 获得晋升
- **作者 / owner：** LoopX maintainers；实验 owner 单独确定
- **创建日期：** 2026-09-15
- **最近规范修订：** 2026-09-15
- **实现基线：** `71dbaee69b528274438295c75d39967150e7c26e`（文档检查，并非全量运行时审计）
- **语言镜像：** [English](frontier-science-research-program-v0.md)
- **研究跟踪：** [#4391](https://github.com/huangruiteng/loopx/issues/4391)
- **社区讨论：** [#4392](https://github.com/huangruiteng/loopx/discussions/4392)

## 文档地图与维护契约

本文与英文版是语义镜像。第 1–10 节定义待评审的研究与验收契约，第 11 节定义阶段计划，第 12 节列出未决事项；附录保存非规范性的执行、决策与证据。提案合并不等于运行时晋升。研究发现、实现成熟度、维护者批准相互独立，进展记录不修改设计。

## 1. 决策摘要

LoopX 应将十个科学方向组织成一个研究组合，每个实验归入已有 capability 或状态 owner。近期建议依次验证实验证据、决策保真接续和元决策 shadow policy。十个方向全部保留，但纳入组合不承诺全部实现。

本 RFC 不新增权威状态、命令、provider、调度策略、权限、模型调用或默认行为，仅提出有界实验契约与评审标准。任何主动 treatment 都需要单独评审的切片、owner、预算、benchmark 使用权和回滚边界。参与 Discussion 不授予执行或晋升权限。

战略假设是：provider-neutral、与真实 outcome 关联的控制能力，可在模型更替时持续改善长程工作的成本、连续性与可靠性。该假设需要产品证据。

## 2. 问题与动机

长程工作会反复查看证据、压缩历史、选择是否继续，并从选择性 outcome 中学习。因此，它可能积累假发现、丢失决策关键差异、过度验证或强化有害经验，同时呈现持续推进的表象。

例如，两份 handoff 都写“修复完成”，却只有一份有当前版本证据和下一步权限；高频引用的记忆也可能只是与简单任务相关，而未导致更好的结果。

### 不变量

- 当前 goal、gate、lease、quota 和 effect owner 保持权威。
- observation、prediction、真实 outcome 与因果 claim 分开。
- 对照计入 controller、回查、评估和人类成本。
- 隐藏评估内容不得进入可复用记忆或选择器输入。
- 失败、零收益与失效实验保留可见。
- claim 标明假设、版本、读取深度与迁移边界。
- 每个新抽象都要证明相对简单基线值得其成本。

## 3. 范围与非目标

覆盖数理统计、信息论、认知科学、控制论、因果推断、形式化方法、神经科学、进化计算、统计物理和科学实验。

短期约 2–8 周形成有界原型；中期约 2–9 月；长期约 6–24 月。区间重叠是有意的：短期原型的迁移证据可能需要中期才能形成。估计假设有 1–2 名熟悉 LoopX 的工程师及可用评估环境；样本积累与领域伙伴可能决定实际周期。这些是计划估计，不是发布日期。

非目标包括通用 supervisor、第二状态库、自动修改权限内核、无约束实验、基模训练、无条件统计安全保证或自主生产实验室。不为匹配论文而重命名或重复已有 capability/RFC。

## 4. 当前系统契约

针对所列基线检查了公开文档和相关 RFC 章节。以下为文档记载的边界，不是新的实现审计。

| 已有 owner / 契约 | 文档记载的基础 | 本计划的增量 |
| --- | --- | --- |
| [分层步幅](hierarchical-agent-stride-control-v0.md) | Effect/delivery/authority 分层；M1 observation | 计入成本并校准的 shadow 选择 |
| [研究探索](research-exploration-control-plane-v0.md) | Typed frontier、composition gap、有界执行交接 | 决策相关实验选择 |
| [记忆效用](post-outcome-memory-utility-attribution-v0.md) | Outcome binding；应用不等于因果效用 | 受控归因与迁移条件 |
| [Benchmark 计划](long-horizon-harness-benchmark-research-program-v0.md) | Matched evidence 与 capability evolution sandbox | 连续有效推断与多样候选档案 |
| [Continuation](cross-session-memory-substrate-v0.md) | 带 revision guard 的明确本地接续 | 可测的决策保真投影 |
| [Effect interpreter](agent-loop-effect-interpreter-v0.md) / [shared authority](shared-goal-authority-state-provider-v0.md) | Typed effect 与有界 transaction | 每次形式化一个稳定契约 |
| [Dreaming 路线](../../product/roadmaps/dreaming-exploration-lane.md) | 建议性的巩固与探索 | 课程与负迁移评估 |

## 5. 研究组合与架构提案

### 5.1 Ownership、记录与生命周期

本计划不新增 built-in capability id 或 provider id。实验记账归 benchmark_toolkit 与已有 evidence owner；reward-memory、context、host、Turn、Explore、authority 保持各自语义。可选 evaluator 或领域集成只有在真实 caller 和契约明确后，才考虑 extension package。

每个实验提案必须说明假设、工作类型、任务/模型/环境版本、候选与基线、实验单位、分配与重复计划、outcome verifier、成本预算、分析假设、来源、停止规则及回滚。这是设计义务，不是新注册的运行时 schema。已有字段不删除、不重新解释。

研究生命周期为 proposal → protocol review → offline/shadow qualification → held-out validation → maintainer disposition。重复记录复用 observation identity，不能重复计数；新 treatment 或分析版本不能静默继承不兼容证据。`insufficient`、`supported`、`refuted`、`invalidated` 仅表示提议中的证据结果，不是新增 Goal 状态。运行时采用需要独立评审的显式转移。缺少 evaluator 数据时按现有策略工作，不虚构成功或 owner gate。

### 5.2 方向总表

| 方向 | 主题 | 首个有用证据窗口 | 落点 |
| --- | --- | --- | --- |
| T01 | 连续有效的实验证据 | 2–4 周验证有界原型 | benchmark_toolkit；扩展长期 benchmark RFC，diagnostics 消费证据。 |
| T02 | 保留决策差异的记忆与接续 | 3–6 周 | 现有 continuation/context provider 边界，与 memory utility 协作。 |
| T03 | 元决策与事件触发步幅 | 4–8 周 shadow 验证 | 扩展 Hierarchical Agent Stride RFC；保留 host、Turn 与 authority owner。 |
| T04 | 因果记忆效用与迁移条件 | 4–8 周离线原型；2–6 月验证迁移 | reward_memory；扩展 Post-Outcome Memory Utility Attribution RFC。 |
| T05 | 形式化权威小核与可检查证书 | 2–6 周验证一个契约；扩大范围属中长期 | 现有 typed Effect Interpreter 与 authority transaction 边界。 |
| T06 | 结构化课程与快慢学习 | 4–8 周原型；2–6 月验证 | 扩展已有 dreaming/exploration 路线与 reward-memory 生命周期。 |
| T07 | 保留多样性的能力进化 | 2–6 月 | 长期 benchmark 研究计划中的现有 capability evolution sandbox。 |
| T08 | 主动信息获取与实验设计 | 3–9 月 | Research Exploration Control Plane；保留 Explore/frontier 与执行 owner。 |
| T09 | 多尺度预测控制状态 | 6–18 月 | 与 stride/context owner 合作研究，不替换权威状态。 |
| T10 | 科学实验 campaign 基础设施 | 3–6 月模拟器合作；12–24 月垂直验证 | 有设计伙伴的可选领域 package，复用 Explore、quota 与 evidence。 |

### T01. 连续有效的实验证据

**科学基础：** 数理统计：e-process、confidence sequence 与适应性实验。

反复查看固定样本检验、择优停止和晋升可能放大假发现。SAVI 在明确假设下支持可选停止；轨迹校准还能识别最终答案掩盖的中间失败。这是成熟数学基础与新兴 agent 应用的结合。 来源：[SAVI](https://arxiv.org/html/2210.01948v2); [ToolChain-CRC, 2026](https://arxiv.org/html/2606.18467v1).

**LoopX 实验：** 固定 treatment 版本，以完整且可比较的任务 episode 为单位，记录分配、缺失、有界 outcome、分析身份和多重比较处理。比较连续有效证据、预注册固定样本方法与现有启发式。

**验收与停止边界：** 测零效应下的误晋升率、检出力和样本成本。相关工具调用不是独立样本；e-value 不是正确概率。假设无法得到支持时只保留描述性证据，不提供统计保证。

### T02. 保留决策差异的记忆与接续

**科学基础：** 信息论：决策导向 rate–distortion 与状态抽象。

DeMem 用决策质量损失衡量记忆压缩。表述相似的历史可能要求不同动作。“修复完成”的摘要必须保留证据是否匹配当前版本、下一步是否获授权等差异。 来源：[DeMem, 2026, §§3–5 and Appendices D/F](https://arxiv.org/html/2605.10870v1).

**LoopX 实验：** 在相同实际 token 预算下比较普通摘要、结构化摘要和决策保真 packet，计入回查成本。先接一条明确 continuation 路径，构建过期证据、目标变化和任务身份混淆的对照样例。

**验收与停止边界：** 测接续成功、约束漏失、未授权决策和总成本。抽象模型的遗憾界不自动迁移到真实 agent。若只改善摘要评分，或投影成为第二权威源，则停止扩大。

### T03. 元决策与事件触发步幅

**科学基础：** 认知科学、控制论与运筹学：计算价值和约束策略优化。

CCPO 研究可靠性约束下的成本优化，DOLORES 探索测试时构造推理结构。LoopX 应验证下一次继续、验证、回查或 replan 的预期价值是否值得成本。 来源：[CCPO, AAAI 2026](https://ojs.aaai.org/index.php/AAAI/article/view/39739); [DOLORES, 2026](https://arxiv.org/html/2605.11388v1).

**LoopX 实验：** 行动前记录预测，再绑定结果。将 shadow policy 与现有规则、便宜阈值对照；行动效果使用前瞻受控实验，被动日志不能独立证明反事实策略价值。

**验收与停止边界：** 计入控制器 token 与延迟，测验收 outcome 成本、错误停止、无效验证和人类注意力。控制器开销吞掉收益时停止。步幅增大不扩大权限。

### T04. 因果记忆效用与迁移条件

**科学基础：** 因果推断与受控干预。

CMI 比较无记忆、带记忆和扰动记忆，但实验选择器使用目标答案评分与记忆角色标签。因此只作为干预设计线索，不能视为无标签部署证据；扰动评分的稳健性含义也需独立核验。 来源：[CMI, 2026, §3](https://arxiv.org/html/2605.17641v1).

**LoopX 实验：** 固定 checkpoint、模型、任务、工具版本，对有无整组记忆做重复重跑；必要时再测 A/B/AB/no-memory 交互。把条件化效用、支持数和不确定性绑定到现有 outcome receipt。

**验收与停止边界：** 隐藏答案不得进入选择器或可复用记忆。日志回放不等于重跑不可重建的世界。先做集合归因，成本或混杂过大时推迟单条归因。

### T05. 形式化权威小核与可检查证书

**科学基础：** 数理逻辑、模型检查、SMT 与证明助手。

HERMES 演示工具化数学验证并区分正确、错误、无法判断；AXLE 处理证明工具隔离、版本与规模。LoopX 可借鉴可检查小核，不声称自然语言意图和真实世界效果均已证明。 来源：[HERMES README](https://github.com/aziksh-ospanov/HERMES/blob/main/README.md); [AXLE, 2026](https://arxiv.org/abs/2606.26442).

**LoopX 实验：** 形式化一个 revision/lease/receipt 状态机并对照实现。候选证书绑定状态版本、动作摘要、权限范围、前后条件和检查器版本；相关状态变化后证书失效。

**验收与停止边界：** 覆盖旧 owner、重复 receipt、中断和原子性反例。hash 证明完整性，不证明正确性；证明只覆盖规范。规范维护成本超过已验证收益时保持窄范围。

### T06. 结构化课程与快慢学习

**科学基础：** 神经科学：经验结构、组合学习与互补时间尺度。

2026-09-03 Nature Neuroscience 研究结合小鼠、RNN 和内嗅皮层记录，分析早期经验结构对后续策略灵活性的影响。软件 agent 迁移是待验证假设，并非生物结果的已证推论。 来源：[Structured experience shapes strategy learning, 2026](https://www.nature.com/articles/s41593-026-02409-7).

**LoopX 实验：** 快通道保存事件证据，慢通道巩固带适用条件、例外、来源和版本的程序知识。用同样经验和预算，对照随机、时间顺序和对照课程，在新组合任务上验证。

**验收与停止边界：** 测前向迁移、保持率和负迁移。外部记忆不等于生物权重学习。若巩固只是重复成功案例，或依赖额外隐含上下文，则停止扩大。

### T07. 保留多样性的能力进化

**科学基础：** 进化计算、进化生物学与 quality diversity。

DGM 从 agent archive 分叉，AlphaEvolve 结合程序变异与 evaluator，Imbue 报告演化式代码优化。非冠军祖先可能产生后续突破；多样性应由行为或适用条件定义，而非不同命名的 prompt。 来源：[DGM](https://arxiv.org/html/2505.22954v1); [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/); [Imbue, 2026](https://imbue.com/blog/2026-02-27-darwinian-evolver).

**LoopX 实验：** 只演化一个有界 adapter、记忆规则或 planner proposal，等总预算比较多样性 archive 与单冠军爬山法。保留谱系和负结果，冻结 evaluator、权限与 held-out 任务。

**验收与停止边界：** 要求迁移、尾部质量和可复现收益。DGM 也报告 objective hacking，因此必须保护 evaluator 并独立验证。档案维护成本超过迁移收益时停止。

### T08. 主动信息获取与实验设计

**科学基础：** Bayesian 实验设计、active inference 与 dual control。

行动可以同时推进工作与消除不确定性。Active Inference as Context Acquisition 研究 token 预算下的信息增益，但固定属性表实验关闭工具，未证明开放工具环境收益。 来源：[Active Inference as Context Acquisition, 2026, §9](https://arxiv.org/html/2608.19202v1).

**LoopX 实验：** 保留有界 competing hypotheses；为每个 probe 写明可能观察及其改变的决定。将决策相关信息价值与简单启发式或 Bayesian optimization 比较；负证据可以关闭假设。

**验收与停止边界：** 测被改变的决定、假设排除质量、outcome 和成本。纯熵下降可能奖励无关好奇心。完整 free-energy 架构只有优于简单局部机制后才考虑。

### T09. 多尺度预测控制状态

**科学基础：** 统计物理、计算力学、粗粒化与因果涌现。

Software in the natural world 研究宏观过程何时具备信息、干预与计算自包含性。LoopX 的 effect/delivery/authority 分层可成为抽象假设，并非已获得物理学验证的架构。 来源：[Software in the natural world](https://arxiv.org/html/2402.09090v1).

**LoopX 实验：** 检验紧凑状态能否跨 provider 预测交付、阻塞与恢复，以及完整微观历史是否仍增加重要预测信息。预测之后还需受控干预，才能讨论行动等价。

**验收与停止边界：** 先对照简单可观察特征，再考虑神经世界模型。无论预测如何，精确权限与证据状态均保留。迁移或状态充分性无法优于现有 typed 摘要时停止扩大。

### T10. 科学实验 campaign 基础设施

**科学基础：** AI for Science：数学发现、物理/材料模拟与生物计算。

Co-Scientist 探索假设生成与迭代；2026 材料实验室 Perspective 讨论跨实验与资源的 campaign 管理。这些是需求与架构信号，不证明 LoopX 已被采用或商业适配。 来源：[Co-Scientist, 2026](https://deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/); [Materials-lab Perspective, 2026](https://www.nature.com/articles/s43246-026-01219-5).

**LoopX 实验：** 从计算模拟器与领域 owner 的 evaluator 开始，跨中断追踪实验意图、资源预约、数据/样品谱系、测量、复现与决策；对照伙伴原流程的恢复、可复现性和资源浪费。

**验收与停止边界：** 要求真实伙伴、可访问接口和可测失败成本。湿实验与硬件需领域安全控制器和明确权限。有界合作验证需求前，不建设通用 autonomous-lab 平台。


## 6. 替代方案与设计选择

Stride、exploration、memory utility、dreaming、capability evolution 优先扩展已有 RFC。连续实验证据和决策保真接续只有在 caller、验收单位清楚后，才适合独立子契约。

有效的固定样本预注册优于无效的连续推断；简单阈值优于无收益的元控制器；结构化摘要优于未经验证的记忆学习器；单状态机模型检查优于全仓证明义务。

量子计算/量子认知、神经形态硬件、可编程生物系统、通用临界性/熵评分保留观察。本轮没有找到它们的有界 LoopX caller 和可测近期优势。出现领域伙伴、可执行问题与可靠对照后重新评估。整体 free-energy 架构和通用世界模型，等待局部机制先证明优于便宜替代方案。

## 7. 安全、隐私与兼容性

研究在显式选择和评审 treatment 前保持建议性。共享运行时变更必须证明 feature-off parity。校准和数学证明不授予权限，学习状态不能覆盖当前 goal intent、authority、精确证据或租户边界。

敏感轨迹与来源保留在授权范围内，公开产物使用合成或许可兼容的证据。跨项目学习需要明确数据使用权，共用 provider 不构成授权。隐藏 verifier、目标答案和任务正文不得用于优化被评估选择器或可复用记忆。

Conformal coverage 依赖假设，分布漂移可能使校准失效；连续证据要求适当条件构造与多重比较处理。形式化证明只覆盖规范，完整性 hash 不证明真值。硬件与湿实验需要领域 owner 的安全控制与授权。

## 8. 迁移与回滚

本提案不迁移状态、不删除字段。每个实现切片在改变运行时前，必须说明 opt-in admission、版本固定、preflight、outcome readback 与回滚。

先运行 offline/shadow reader。回滚禁用候选策略或 provider，恢复现有策略，同时保留不可变证据与候选来源。Provider 排序变化、记忆编辑和在线状态切换需通过其 owner 生命周期的评审迁移，本总纲不授予这些权限。科学上的负结果是合法结算，不意味着可以一直重跑到有利结果。

## 9. 验证与验收

| Claim / 方向 | 测试或证据 | 要求结果 | 边界 |
| --- | --- | --- | --- |
| T01：停止后证据仍可解释 | 零效应/植入效应模拟及 matched task trial | 报告预声明错误控制、检出力和成本 | 明确假设与多重比较 |
| T02：压缩保留决策 | 等预算对照接续任务 | 在声明成本下改善 outcome/约束保持 | 计入回查和原状态读取 |
| T03：控制收益覆盖开销 | 与便宜启发式的前瞻对照 | 改善质量/成本/注意力权衡 | 无充分支持时不做回溯因果 claim |
| T04：记忆收益有条件 | 重复有无记忆及必要的交互实验 | 测得效用不确定性与适用性 | 无隐藏答案选择器 |
| T05：形式化转移匹配实现 | 独立状态机 oracle 与反例 | 所述模型中的目标不变量成立 | 不证明模型外世界效果 |
| T06：课程改善迁移 | 同数据同预算的新组合任务 | 改善保持和迁移，不增加过量伤害 | 生物到 agent 迁移最初未证实 |
| T07：多样性产生可复用能力 | Archive 对照单冠军 | Held-out/尾部收益超过搜索成本 | 固定 evaluator 与权限 |
| T08：Probe 获取有用信息 | Competing hypotheses 与改变动作的 observation | 每单位总成本带来更好决定 | 熵下降本身不够 |
| T09：宏观状态可迁移 | 跨 provider 预测与干预 | 优于简单 typed 特征 | 预测不等于因果充分性 |
| T10：Campaign 支持真实需要 | 伙伴基线与中断模拟器流程 | 改善恢复、复现或资源成本 | 不晋升湿实验/生产执行 |
| 所有主动 treatment | 确定性 conformance 与 feature-off parity | 关闭时无额外权限、spend 或调用 | 运行时准入前必须满足 |

实验公开版本、协议、聚合证据与局限前，研究测量都属于未验证。文档检查只证明提案文档质量。预先声明效应或非劣效界限和样本设计，不能把任意涨分阈值当统计证明。

## 10. 运行契约

本文不能影响运行系统，不新增 CLI、dashboard、Lark、daemon 或通知路径。后续获准实验必须通过已有 owner 展示负责人、预算、treatment 版本、证据有效性、失败和禁用方法。证据缺口、实验失败和任务失败保持区分。

战略数据单元为：范围内状态与条件 → 候选行动 → 分配/选择 → 实际行动 → 真实 outcome → 成本与人类注意力 → 版本和有效范围。普通日志不会自动产生因果优势，复用需要可比较工作、干预覆盖与合法数据访问。

## 11. 规范性交付计划

| 里程碑 | 提议产物 | 进入条件 | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| M0 | 中英文总纲、tracker、Discussion | 公开来源与重叠检查 | 十方向、owner、来源边界和后续决策可见 | 撤回提案，保留讨论历史 |
| M1-A | T01 有界证据契约 | 具名 owner、固定 treatment 与 outcome 单位 | 零效应/植入效应检查及对照协议 | 禁用 observer，保留证据 |
| M1-B | T02 接续投影实验 | 一条真实接续路径与反例 | 等预算 outcome/约束结果 | 恢复当前投影 |
| M1-C | T03 步幅 shadow 实验 | 成本数据与准入的对照设计 | 预测/结果和完整成本基线 | 禁用 shadow policy |
| M2 | 选择 T04–T08 的实验，不同时全开 | M1 证据或明确独立理由 | 迁移、失败案例和成本记账 | 经 owner 生命周期移除 treatment |
| M3 | T09 研究或 T10 伙伴试点 | 具名研究/伙伴 owner 与有界环境 | 跨 provider 或领域 outcome 证据 | 结束试点，保留来源 |

建议优先 M1-A、M1-B、M1-C。T05 若已有稳定 authority 契约及明确验证 owner，可独立推进。第 3 节时间不构成 deadline 或贡献者指派。RFC 合并或 drafting PR 关闭不完成研究 tracker。

## 12. 未决事项

| ID | 决策 owner | 选项 / 建议 | 所需证据 | 何时决定 |
| --- | --- | --- | --- | --- |
| D1 | Maintainers + 实验 owner | 首场景为接续、benchmark 或其他真实 caller；建议小型可复现流程 | Outcome verifier 与运行授权 | M1 协议批准前 |
| D2 | 统计 reviewer + toolkit owner | 固定样本或连续构造；先做固定 treatment 和有界 episode outcome | 依赖、分配、停止、多重比较假设 | T01 实现前 |
| D3 | Context + stride owner | 必须保留的差异和非劣效界限 | 真实 failure case 与成本基线 | T02/T03 trial 前 |
| D4 | Maintainers | 单契约或大范围证明；建议单状态机 | 稳定规范与独立 oracle | T05 前 |
| D5 | 研究/领域 owner | 宏观状态研究或模拟器伙伴；均保留，按证据投入 | 数据集或领域伙伴与成功标准 | M3 前 |
| D6 | Maintainers + 贡献者 | 人力/预算；优先三项 M1 与有界长期探索 | 可用 owner 与成本 | 资源承诺前 |

## 附录 A：执行记录

2026-09-15：公开来源综合与文档基线检查形成此提案。未交付新运行时、模型实验或 benchmark 结果。来源读取范围见后文，文档检查记入 PR。不改变规范批准状态。

## 附录 B：决策记录

没有实现或晋升决策获批准。公开评审不批准科学 claim、资源分配或运行时变更。后续批准需记录公开评审链接与影响章节。

## 附录 C：证据登记与读取范围

T01–T10 来源为原始论文、会议论文、官方研究文章或项目 README，支持机制及有界外部发现，不支持 LoopX uplift。本轮是选择性扫描，不是穷尽综述。

| 方向 | 读取范围 | 结果 / 局限 |
| --- | --- | --- |
| T01 | SAVI 框架；ToolChain-CRC 设定、方法和假设 | 审阅机制，未独立复核证明与复现实验 |
| T02 | DeMem 设定/方法及理论到实践边界 | 抽象保证与实现分开 |
| T03 | CCPO 官方摘要；DOLORES 方法和局限 | 无 LoopX 成本/可靠性结果 |
| T04 | CMI §3、评分器与标签依赖 | 部署 claim 需无标签证据 |
| T05 | HERMES README 工具契约；AXLE 摘要 | 无 LoopX 状态机证明 |
| T06 | 神经科学论文日期、摘要与 Main | 特定小鼠/RNN 任务；软件迁移未验证 |
| T07 | DGM archive 机制与 objective-hacking；AlphaEvolve/Imbue 官方文章 | 未复现或晋升 capability |
| T08 | 上下文获取方法范围与 §9 局限 | 有限属性；所引实验关闭工具 |
| T09 | 原始多尺度闭合框架 | 理论启发，不是 agent 系统保证 |
| T10 | Co-Scientist 官方研究文章与材料实验室 Perspective | Perspective 为建议，不证明部署或商业成功 |

## 附录 D：推迟的替代方案

量子/神经形态/生物 backend、通用熵评分、通用世界模型和整体内核自修改，按第 6 节理由与重启条件推迟。负结果和过时设计应连同违反的不变量保留。

## 附录 E：评审经验

科学新颖性、实现可得性、部署证据是不同事实。目标答案访问、标签辅助选择、未计入的 controller 成本、反复检验与弱迁移，都可能使一篇有吸引力的论文暂不适合晋升。
