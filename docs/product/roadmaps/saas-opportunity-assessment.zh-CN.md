# 商业化与 SaaS 机会评估

状态：评估目标。它讨论 LoopX 如何通过 Enterprise Harness、产品化交付、BYOC、托管运维或 SaaS，把控制面技术变成可重复的客户价值，同时不削弱 LoopX 赖以成立的 local-first、provider-neutral 契约。

## 商业判断

LoopX 最连贯的商业位置是：

> 开源并保持 local-first 的长程 Agent 语义状态契约；把它们包装成成熟的 Enterprise Agent Harness；通过产品化 FDE 交付把有边界的垂域 workflow 推到生产；收费提供私有部署、软件许可、托管运维与支持；再把跨客户反复出现的运行面扩张为 Managed Semantic Control Plane 与 SaaS。

开源层提供可迁移的 goal、authority、todo、evidence、acceptance、quota、handoff、recovery 和 replan 状态。付费层把这些契约包装成可部署产品，并让它们在团队环境中持续可用：可协作、可观测、可恢复、可治理，并有人为其稳定性负责。

Managed Semantic Control Plane 仍然是长期商业复利的核心，但不必成为第一个 SKU。客户最初可能购买的是一个真正工作的数字员工或数字团队、私有部署、系统集成、验收证据，以及把方案推到生产的人。每次交付都必须运行在同一套可复用 Harness 上，而不是形成客户专属分叉。重复价值与持续运维需求出现后，云端 recurring revenue 才成为自然扩张。

这个位置接近两种已经公开商业化的相邻模式。Letta 将持久、有状态的 Agent 包装成托管服务，按 active agent 与执行量计费；Mastra 在开源 Agent 框架之上销售托管运行、留存、团队协作和企业治理。LoopX 不需要复制二者的产品边界。它的差异化位置是跨异构 Agent runtime 的 provider-neutral 语义控制层：完备的状态管理、规划与监督，基于证据的恢复，以及能够跨 run、跨 Agent 继承的人类 authority。

这是产品定位假设。它仍然需要重复生产使用、交付复用和付费意愿验证。

## 核心张力

LoopX 的价值主张是 local-first 控制面：operator 可以拥有、检查、导出和恢复 durable state。一个天真的 SaaS 做法——“我们替你在服务器上托管 Agent 状态”——会削弱让产品可信的那个属性；一个天真的交付做法——“客户要什么就一直定制到验收”——则会把项目变成低复用的系统集成生意。

因此，商业化问题是：

> 哪些客户结果需要直接交付；哪些控制面职责由专门服务持续运行后更有价值；哪些 authority、数据与产品边界必须按设计保持可迁移、可复用？

Discovery、集成、评测与上线可能需要 FDE 进入客户 workflow。协作、长期留存、共享治理、托管恢复和运维支持适合私有、BYOC 或 hosted 服务。语义状态契约、导出路径、本地执行选项，以及客户对私有工作区内容的 authority 应当保持开放。

付费产品卖的是生产就绪的 Harness、被交付的结果、运行、可靠性和组织控制；不能把用户自己的状态格式锁住后再卖回访问权，也不能把无限工程师工时伪装成产品。

## 开源层与付费层边界

| 层次 | Community 与 local-first 契约 | Managed 产品价值 |
| --- | --- | --- |
| 语义状态 | goal、todo、gate、decision、evidence、acceptance、quota、handoff、recovery、replan 的开放 schema 与 transition | 高可用状态服务、冲突处理、备份恢复、迁移和托管升级 |
| 执行 | Codex、Claude Code、Cursor、shell agent 与自定义 worker 的 provider-neutral adapter | Agent fleet 注册、health、策略约束的 wake、Supervisor 调度、恢复和 operator 路由 |
| 观测 | 本地 projection、CLI status、导出与可自托管 dashboard surface | 共享 workspace、长期留存、跨 Agent timeline、评测、回放、告警和 review queue |
| 治理 | 可检查的本地 authority、boundary 与 approval contract | 多租户隔离、RBAC、SSO、审计、配额、数据驻留、签名导出和策略管理 |
| 交付 | 文档、pack SDK、参考 workflow 与可用的 self-host 路径 | Enterprise Harness、产品化 FDE 部署、BYOC 或 managed operation、SLA、迁移、集成、incident response 和支持 |

可迁移性是产品契约的一部分。客户应当能够导出语义状态，保留 durable identity 与 evidence lineage，并回到本地或 self-hosted 控制面，而不需要从私有日志中重新推断工作的含义。

## 可行性测试

商业产品只有在通过以下全部六条时才值得规模化：

1. **结果证明**：一个有边界的 workflow 达到客户验收，并在周期、质量、产能、恢复或合规成本上有可测改善。
2. **持续使用**：operator 与 Agent team 在一周工作过程中持续使用，而不是装一次就结束。
3. **托管优势**：集成、协作、可用性、恢复、留存或治理让产品显著优于单机脚本与文件。
4. **交付复用**：客户工作沉淀为 adapter、pack、eval 或核心改进，降低下一次部署成本。
5. **自然扩张**：收入随授权环境、workspace、active managed agent、留存、Supervisor work 或企业控制扩张，而不只随工程师天数增长。
6. **控制面效果**：客户可以测量人工协调减少、恢复加快、非法 continuation 下降，或 review 与 audit 成本下降。

结果证明与交付复用应当早于 SaaS 形态。无法改善长程工作的 dashboard 只是界面 feature；没有产生可复用资产的成功部署只是服务项目。

## 商业对标：看价值捕获，不只看 Star

截至 2026-08-15 的公开证据表明，这四个项目代表四种不同的价值捕获模式，并不是四家可直接横向比较的创业公司。GitHub Star 能证明分发和类别关注。

| 项目 | 公开商业证据 | 价值捕获路径 | 当前判断 | 对 LoopX 的启示 |
| --- | --- | --- | --- | --- |
| Letta | 公司在 2024 年[完成 1000 万美元种子轮](https://www.prnewswire.com/news-releases/berkeley-ai-research-lab-spinout-letta-raises-10m-seed-financing-led-by-felicis-to-build-ai-with-memory-302257004.html)。当前 [API 方案](https://docs.letta.com/pricing)包含基础订阅、active agent、tool execution 与模型用量计费；team / enterprise 档增加共享、访问控制、SSO 和支持。[厂商案例](https://www.letta.com/case-studies/bilt/)称 Bilt 已运行超过 100 万个 Agent。 | 托管 stateful agent、执行、协作与企业控制 | Persistent agent state 已出现真实定价与生产信号；但没有公开审计 ARR，Bilt 数据来自厂商案例。 | Durable state 在被持续运行、并绑定生产 workload 后，可以成为计费 primitive。 |
| Mastra | Mastra 于 2026 年 4 月[宣布 2200 万美元 A 轮、累计融资 3500 万美元](https://mastra.ai/blog/series-a)。其[定价](https://mastra.ai/pricing)包括 250 美元/月 team 档、观测/算力/memory/storage/retention 用量，以及按年收费的 self-hosted enterprise 方案。 | 开源框架 + 托管平台、运维与企业部署 | 这组对标中最强的独立平台商业信号。融资、客户案例与 packaging 有意义，但不等于已披露收入。 | 开源开发框架可以扩张到 Managed Operations，前提是付费层真正承担可靠性、留存、评测与交付。 |
| AgentScope | AgentScope 是[阿里通义实验室 SysML 团队](https://github.com/agentscope-ai/agentscope/blob/main/pyproject.toml)维护的 Apache-2.0 项目，不是一家单独披露的创业公司。它可部署到阿里云体系；[AgentRun](https://www.alibabacloud.com/help/en/functioncompute/what-is-agentrun)销售 serverless runtime、sandbox、模型治理、观测和成本管理，并明确集成 AgentScope。 | 云消费、生态拉动与平台留存 | 在阿里云体系内可能有很高战略价值，但不存在有意义的独立 AgentScope 估值或收入单元。 | OSS 框架可以创造可观的平台价值，而直接经济回报主要被外围云平台捕获。 |
| CAMEL / Eigent | [CAMEL-AI](https://www.camel-ai.org/about)建立多 Agent 研究与类别心智，关联产品 Eigent [自报上线不到三个月收入超过 25 万美元](https://www.eigent.ai/about)，并提供[年付折算 19.90 / 99.99 美元月费与 enterprise 部署](https://www.eigent.ai/pricing)；条款中还包含[商业生产许可与专业服务](https://www.eigent.ai/terms-of-use)。 | C 端/个人订阅、企业许可、私有化与服务 | 已有早期、具体的应用变现，但收入是公司自报的短窗口数据，不能证明稳定 recurring revenue。 | 研究与 OSS 热度可以通过有明确主张的应用转化，但它与基础设施的销售和毛利结构不同。 |

由此可以得出三个结论。

第一，商业价值并不按 Star 排名。Mastra 当前拥有最强的资本与平台 packaging 信号；Letta 拿出了最清楚的 stateful agent 生产案例；AgentScope 可能创造很大的云平台内嵌价值，却不必成为独立公司；CAMEL 则通过独立应用承接研究影响力。

第二，反复出现的路径是“开源分发 + 稀缺付费面”：有人卖托管状态，有人卖观测与部署，有人拉动云消费，也有人销售垂域应用和私有化。开源并不妨碍收入，前提是付费产品消除真实的运行与组织负担。

第三，LoopX 的架构位置是前两种模式的上层组合：Letta 式 durable state + Mastra 式 managed operations，并把它泛化到异构 runtime。差异化产品是让 goal、authority、规划、监督、evidence、recovery 与 handoff 跨 Agent、跨 run 保持一致的语义控制面。这些对标验证了变现形态，但 LoopX 仍需要验证客户是否会为这一独立层持续付费。

## 中国市场：先产品化交付，再验证纯 SaaS

“SaaS 在国内难卖”太粗，不足以直接指导战略。中国信通院的[《企业级 SaaS 市场发展研究报告（2024 年）》](https://www.caict.ac.cn/kxyj/qwfb/ztbg/202408/P020240815374016912879.pdf)估算，2023 年国内 SaaS 市场规模为 581 亿元，同比增长 23.1%。市场是真实增长的；但同一报告也指出，大客户推动厂商增加私有化、混合部署和定制开发，订阅制采用仍不均衡，项目费、咨询、培训、硬件等收入并存。

报告同时点出了反面：重定制会推高交付成本、拖慢标准产品迭代，并让研发投入无法跨客户摊销。因此 LoopX 不该选择两个极端：

- 不等一个低接触、横向 SaaS dashboard 自动创造仍处在早期的类别需求；
- 不成为“恰好使用 LoopX”的无限定制项目公司；
- 用直接交付发现并证明高价值 workflow，同时以统一版本 Harness、扩展边界和 evidence contract，把客户工作强制沉淀成可复用产品资产。

不同客户应当使用不同的商业动作：

| 客户 | 首个产品 | 交付方式 | 经济逻辑 |
| --- | --- | --- | --- |
| AI-native 创业公司或技术团队 | Enterprise Harness 许可与支持；可选 Team Cloud | 自助接入或短期 enablement | 客户能自行集成 runtime，重视速度、可迁移和多 Agent 连续性；低交付负担可以形成软件 recurring revenue |
| 科研组或实验室 | Community、赞助支持或共享科研 Harness | 模板与 enablement；只有有经费的机构 workflow 才做 FDE | 可复现、实验监督与恢复很适合 LoopX，但多数科研组承受不了企业销售和定制成本 |
| 中型企业 | 付费 discovery + 一次有边界的 FDE 上线，之后转年度私有/BYOC 许可 | 明确 outcome、验收、集成与交接 | 客户愿意为 workflow 改造付费，但会先要求直接价值，而不是购买抽象平台 |
| 大型或强监管企业 | Enterprise Harness、FDE、托管运维、治理与 SLA | 私有/BYOC，包含安全、审计和采购工作 | 高客单价可以覆盖集成与控制要求，但销售周期和服务负担明显更高 |
| 海外开发者团队 | Team Cloud 或 Managed Control Plane | 产品试用 + 远程 solution engineering | 对公有云和软件订阅的接受度更高，纯 SaaS 路径更成立 |

这是一种先后顺序。国内的第一收入形态更可能是“软件许可 + 有边界交付 + 年度托管运维”；SaaS 更适合低摩擦团队、海外客户，以及多次交付后被证明共性的运行面。

### 成熟 Harness 是第一个付费产品

成熟 Agent harness 的公开实践说明，客户购买的是一套完整可运行的产品。OpenAI 称 Codex 已有超过 200 万周活开发者，并通过[按用量计费](https://openai.com/index/codex-flexible-pricing-for-teams/)服务团队；Anthropic 为 Claude Code 打包了[统一账单、支出控制、用量分析、tool / MCP 策略和 Compliance API](https://www.anthropic.com/news/claude-code-on-team-and-enterprise)，也[支持通过现有 Bedrock 或 Vertex AI 基础设施做企业部署](https://docs.anthropic.com/en/docs/claude-code/getting-started)。这些数据不证明 LoopX 已经有需求，但证明了企业对 harness 的完整度预期：安装、执行、策略、观测、管理与支持必须组成一个可运维产品。

LoopX 的第一个付费产品因此应当是 **LoopX Enterprise Agent Harness**，而不是一组 schema，也不是另一个模型或 IDE。它应当包含：

- 有版本的 Kernel 与 state service，支持 local、private 与 BYOC profile；
- Codex、Claude Code、Cursor、shell agent 与客户 worker 的受支持 adapter；
- Supervisor 调度、恢复、handoff、quota 与 acceptance；
- 面向 goal、evidence、review、replay 与 fleet health 的本地或私有 console；
- 部署自动化、升级、备份恢复、默认 policy 与诊断支持包；
- 承载 tool、eval、role contract 与客户系统集成的 domain-pack 边界，不分叉 Kernel。

客户购买的是一套能把一个 workflow 推到验收的系统。Semantic Control Plane 是其中的产品脊柱。

### FDE 是产品发现与生产化机制

FDE 的价值，是补齐成熟 Harness 与混乱生产 workflow 之间的最后一公里。OpenAI 当前的 [FDE 岗位](https://openai.com/careers/forward-deployed-engineer-%28fde%29-sf-san-francisco/)覆盖 discovery、技术定界、系统设计、开发、上线、workflow 效果衡量，以及把成功模式沉淀为工具、playbook 与可复用 building block。Palantir 的 [2025 Form 10-K](https://www.sec.gov/Archives/edgar/data/1321655/000132165526000011/pltr-20251231.htm)披露 45 亿美元收入与 954 家客户，说明复杂部署和持续扩张可以支撑大型软件生意；同一文件也把高安装成本、长销售周期、昂贵 pilot、培训与持续服务列为明确风险。因此 FDE 必须是产品反馈环，而不是按人天出售的 staff augmentation。

一次 LoopX FDE 交付应当有五个有边界的产物：

1. workflow baseline、明确的 outcome owner、authority map 与付费范围；
2. 基于当前 Enterprise Harness 和受支持扩展点的生产路径；
3. eval set、验收标准与前后效果证据；
4. 部署、operator 培训、runbook、rollback 与交接；
5. 至少一个回流产品的 pack、adapter、eval、playbook 或核心改进。

规则必须严格：不做无限期免费 PoC，不做客户专属 Kernel 分叉，不在没有客户 authority 时执行生产写入，也不在结果归因不可审计时承诺 outcome pricing。持续跟踪到验收的工程师月数、可复用与客户专属工作的比例、同一 pack 第二次部署耗时、软件与托管收入相对人力收入的占比，以及续费与扩张。如果这些指标不随交付改善，FDE 不是在形成壁垒，而是在掩盖服务生意。

## 垂域数字员工与数字团队

这个应用类别已经不再是假设，但也还没有成熟为“自治数字劳动力市场”。

- BNY 在 [2025 年报](https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/annual-report-2025.pdf)中披露 160 个生产中的企业 AI 方案和 134 个“数字员工”，并将后者定义为与人类同事一起自主工作的 multi-agent system。这证明强监管企业已经可以把数字员工变成组织单元，而不只是 demo。
- Microsoft 的 [2025 Work Trend Index](https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/microsoft/final/en-us/microsoft-product-and-services/ai/pdf/executive-summary-work-trend-index-annual-report.pdf)覆盖 31 个国家的 3.1 万名工作者：45% 的领导者把数字劳动力扩充团队容量视为近期重点，46% 称公司已用 Agent 自动化 workflow 或 process。这反映意愿与自报采用，不等于已验证的生产 ROI。
- McKinsey [2025 全球调查](https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai?lang=en)提供了必要的反面约束：23% 的受访者称公司已经在某处 scale agentic system，另有 39% 正在试验；但没有任何单一职能的 scale 比例超过 10%，企业级 EBIT 影响仍不普遍。缺口不在认知，而在生产化与 workflow 重构。
- 一项覆盖 5179 名客服人员的实地研究发现，生成式 AI assistant 带来[平均 14% 的生产率提升](https://www.nber.org/papers/w31161)，对低经验员工提升更大。这验证了任务级经济价值，但研究对象是 assistant，不是自治数字员工。
- AWS 在 2025 年将 [multi-agent collaboration 正式 GA](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-announces-general-availability-of-multi-agent-collaboration/)，并展示金融、零售、反欺诈、客服、医疗与农业中的 Supervisor 编排。这说明多 Agent 协作正在成为平台 primitive，但厂商案例不能证明所有 workflow 都需要多个 Agent。
- Gartner 的[失败预测](https://www.gartner.com/en/newsroom/press-releases/2025-06-25-gartner-predicts-over-40-percent-of-agentic-ai-projects-will-be-canceled-by-end-of-2027)认为，到 2027 年底超过 40% 的 agentic 项目会因成本、价值不清或风险控制不足而取消；同一份判断又预测 2028 年 33% 的企业软件将包含 agentic 能力。市场快速增长与大量同质项目失败可以同时发生。

### 产品单元是什么

垂域数字员工不能只是“带职位名的 prompt”。它应当是一份 governed role contract，至少包含：

- durable identity，以及一个明确的人类 outcome owner；
- 有边界的 tool、data、authority、quota 与 escalation policy；
- 明确的 goal、plan、acceptance criteria 与服务预期；
- 可供事后 review 的 evidence、decision 与 action history；
- run、模型、机器或 operator 变化后的 recovery 与 handoff 行为。

数字团队则是多个这样的角色契约：共享 goal 与 evidence，但各自保留独立 authority；它进一步需要显式 claim / handoff、团队级 budget / acceptance，以及 Supervisor 的调度、恢复与 replan。Supervisor 只能协调已记录的 authority，不能成为隐藏的管理者。

这一定义与 LoopX 的语义契约直接对应。Domain pack 可以包装领域工具、评测、review 和 escalation；开源 Kernel 保持 state 与 authority protocol 可迁移；付费 Managed Semantic Control Plane 负责让数字员工或数字团队持续在线、可恢复、可观测、可治理。

### 应用前景与切入顺序

下表是基于上述公开证据得出的战略判断。

| Workflow | 市场成熟度 | LoopX 适配度 | 付费逻辑 | 主要门槛 | 建议位置 |
| --- | --- | --- | --- | --- | --- |
| 客服与员工服务 | 高 | 中 | 任务量大，resolution、latency、deflection 与质量指标清楚 | 实时延迟、安全转人工、成熟平台竞争 | 作为长程 evidence、policy 与 recovery 层接入，不先做 contact-center runtime |
| 软件工程、SRE 与 IT 运维 | 高 | 很高 | 工作跨 repo、incident、review、机器和多天周期，错误 continuation 代价高 | acceptance 可靠性、环境隔离、merge / 生产 authority | Team Cloud 与 Managed Control Plane 的第一批 design partner |
| 科研、实验与实验室运营 | 中高 | 很高 | hypothesis、negative result、evidence lineage、quota 与重复实验天然需要 durable semantic state | 领域评测，以及对仪器、数据集或算力的连接 | 面向科研组和技术创业公司的首批场景，销售可复现、监督与恢复 |
| 财务、采购、order-to-cash 等后台运营 | 中高 | 高 | 跨系统 workflow 有明确 cycle time、exception rate 与 audit cost | ERP 集成、权限、隐私和审批边界 | 通过 BYOC design partner，从有明确人类 gate 的窄 workflow 进入 |
| 法律、医疗等强监管专业工作 | 中 | 长期高 | review 与合规成本高，evidence 和 authority 更值钱 | 责任、领域准确性、数据驻留与专业签字 | 先做 human-led 数字团队，以治理、review 和 evidence pack 进入，不让 Agent 自主做最终决定 |
| 跨职能数字团队 | 早期 | 终局最高 | 多个专职 Agent 可以持续拥有一个 outcome，而不只完成孤立任务 | 跨角色 authority、冲突、共享 acceptance 与可问责 escalation | 单角色复用与恢复得到证明后的长期产品终局 |

因此，近期销售话术应当是**为一个高价值 workflow 提供可治理的新增产能**，而不是“用 AI 替换一个部门”。初始合同可以组合 workspace / workflow 费用、active managed employee、evidence retention、Supervisor work 与 enterprise delivery；只有当结果可归因、可审计时，才适合增加 outcome-linked pricing。

顺序同样重要。软件、SRE、科研与技术运营最接近 LoopX 当前社区，也最直接暴露控制面要解决的长程失败。后台与强监管 workflow 可能带来更高客单价，但应在 authority、evidence、deletion 与 recovery 得到证明后，通过 BYOC 和领域伙伴进入。数字团队是扩张路径。

## 产品阶梯

下面是产品与收入阶梯。每一步都必须留下可复用产品，并让下一步更容易出售。

### 1. Enterprise Agent Harness

第一个可销售产品，是一套支持 local、private 与 BYOC profile 的统一版本 LoopX 发行物。它打包 runtime adapter、语义状态、Supervisor、恢复、验收、评测、部署升级、备份诊断与 operator console。验收单位是一个有边界 workflow 到达生产。

### 2. FDE 驱动的 Design Partner 交付

付费 discovery 与有边界的生产交付，把 Harness 接入一个高价值客户 workflow。交付包括集成、评测、验收证据、运行手册、rollback 与交接。FDE 不是独立咨询 SKU：每个项目都必须运行在受支持 Harness 上，并沉淀 pack、adapter、eval、playbook 或核心改进。

### 3. Team Evidence And Governance Plane

当一个 workflow 开始反复运行后，LoopX 可以销售其组织控制面：共享 goal board、不可变 evidence 留存、回放、review-ready handoff、审批、quota、评测历史、RBAC、SSO、audit 与 policy administration。它应先采用 private、BYOC 或 read-mostly 形态，通过明确 projection 消费数据，而不是默认读取私有 workspace。

### 4. Managed Semantic Control Plane

长期终局是一个持续运行的控制面：本地或第三方 runtime 执行 bounded work，Managed Semantic Control Plane 维护完备且一致的 semantic execution state。它负责权威且可识别冲突的状态、Supervisor 调度、stalled-loop 检测、恢复、handoff、governed replan，以及跨 runtime 的 identity、claim、quota、evidence 与 acceptance 连续性。

BYOC 与 managed private 是风险更低的第一形态；完整多租户托管要等隔离、删除、备份、支持和 on-call 经济性得到证明。

### 5. Domain Packs 与伙伴生态

Domain capability packs（`docs/product/domain-capability-packs.md`）包装 tool、role contract、eval、review rule 与集成，同时保持 Kernel 通用。LoopX 或认证伙伴可以交付它们。Marketplace 是更晚的分发与收入面；领域 authority 仍然不能进入通用 Kernel。

## 计费单位与产品分层

计费应当跟随被交付和被托管的价值，而不是转售模型 token 或无限工程师天数。

| 价值面 | 候选计费单位 | 扩张逻辑 |
| --- | --- | --- |
| 付费 discovery 与部署 | 固定范围、milestone 与验收 | 客户为一个明确 workflow 到达生产付费，而不是为无限期 PoC 付费 |
| Harness 许可 | 年度 environment / workspace 许可 + maintenance | FDE 离场后产品仍能独立运行，并跨 workflow 与团队扩张 |
| FDE 生产化 | 有边界的集成与部署费 | 为最后一公里提供资金，同时明确 scope、交接与复用产物 |
| 团队控制面 | workspace + collaborator seat | 更多团队与 operator 共享同一份 governed state |
| Agent 连续性 | 月 active managed agent 或 active governed goal | 更多长程 worker 依赖 identity、state、quota 与 recovery |
| Evidence 运维 | retained event/evidence volume + retention window | 更长周期或强监管 workflow 需要更持久的历史 |
| Managed supervision | 策略约束的 wake、recovery、replay 或 eval execution | 客户为持续运行的 continuation 付费，而不是为原始模型调用付费 |
| 托管运维 | deployment environment + 治理与支持档位 | BYOC、SSO、RBAC、audit、residency、SLA、迁移和 incident response 形成组织价值 |

一个可行的产品阶梯是：

- **Community**：local-first Kernel、protocol、CLI、export 和可自托管 projection；
- **Enterprise Harness**：受支持的私有发行物、runtime adapter、console、部署自动化、升级、备份、诊断与年度维护；
- **Design Partner Deployment**：带 outcome、验收、复用和交接门槛的付费 FDE；
- **Managed / BYOC**：durable semantic state、Supervisor 运维、恢复、治理、审计、数据驻留、迁移、SLA 与支持；
- **Team Cloud**：在多租户经济性得到证明后，面向低摩擦或海外团队提供共享 workspace、留存、审批、告警和 review。

这是 packaging model。定价前需要先获得 active agent、event volume、retention、Supervisor execution、交付工作量和支持成本的真实分布。合同应当区分软件许可、有边界交付和 recurring managed operation。FDE 离场后 Harness 必须仍有独立价值；Agent 与其 goal 不能作为同一活动被重复计费。

## 什么不该成为生意

- **封闭的语义状态格式**：goal、evidence、authority 和 handoff 必须保持可检查、可导出。产品粘性应来自运行质量。
- **把通用执行托管当核心产品**：LoopX 可以编排外部 runtime，也可以运行 bounded Supervisor work；但转售模型 token 与 sandbox 会让项目在算力毛利上竞争，并模糊“控制面不拥有 domain 行为”的边界。
- **把托管 CLI 文件当产品**：没人会为把本地文件搬到别人的磁盘付费。Managed 层必须增加协作、可靠性、恢复或治理价值。
- **客户专属 Kernel 分叉或无限 FDE 驻场**：客户差异必须进入受支持扩展点和有边界交付。永久分叉与工程师天数依赖会摧毁复用。
- **无限期免费 PoC**：discovery 可以短，但生产工作必须有 owner、付费范围、验收标准和交接计划。
- **默认获得云端 authority**：托管基础设施不会自动授予读取私有 workspace、通过 gate、发布或执行生产写入的权限。
- **替代部门或不可审计的结果承诺**：销售的是一个有边界 workflow 的 governed capacity。Outcome-linked pricing 必须建立在可测 baseline 与可审计归因上。

## 诚实的约束

- **采用与证据缺口**：公开长程 demo 能证明技术可行性，不能证明 recurring demand 或客户结果。外部生产 workload 必须同时验证二者。
- **国内采购与回款**：私有部署、安全评估、集成、采购与付款周期，可能让收入比公有云订阅更慢、更难复用。
- **FDE 与服务陷阱**：直接交付可以创建类别，也会消耗创始人注意力、造成客户集中，并在复用和 recurring 软件收入不改善时掩盖弱产品需求。
- **云端冷启动**：共享观测或控制面需要足够多反复运行的团队与 workflow。不能只为造出 SaaS 形态而建设。
- **品牌张力**：local-first 与 SaaS 可能方向相反。可迁移、self-host、明确 opt-in 和收窄的 managed boundary 必须是产品行为，不能只停留在营销表述。
- **信任与安全面**：托管 evidence 与 authority state，会带来比 OSS CLI 更强的隔离、删除、备份、incident response 和合规责任。
- **运营能力**：托管产品包含 on-call、升级、迁移、incident response 和客户支持；FDE 还增加交付人员与伙伴质量风险。第一个付费范围应当刻意收窄。
- **单位经济性未验证**：交付人力、Supervisor execution、留存、支持和长销售周期都可能吞掉毛利。软件许可、交付与托管运维的收入质量必须分开衡量。

## 规模化商业交付或 SaaS 前的证据门槛

在扩大 FDE 团队或让 Managed 服务接管客户权威状态之前，LoopX 至少应当证明：

1. 多个客户为有明确 outcome owner 与验收标准的有边界 workflow 付费；
2. 周期、质量、产能、恢复、review 或合规成本相对 baseline 有可测改善；
3. 部署使用同一版本 Harness 与受支持扩展点，同一 pack 第二次部署明显减少 FDE 工作量；
4. 初次交付后仍然存在许可、续费、托管运维或扩张收入
5. 独立团队在数周级工作中反复使用 state、supervision、recovery、evidence 与 handoff；
6. Managed authority 扩张前，export、restore、deletion、tenancy、backup 与 public/private boundary 行为得到验证；
7. 交付、留存、Supervisor work、支持、销售周期与客户集中度可以维持可接受的单位经济性。

这些门槛将技术期权与已经兑现的商业价值分开。

## 建议路径

Phase 0 —— 为本地产品补充度量，并选择两个参考 workflow。在默认不收集私有内容的前提下，记录 baseline、验收、active agent / goal、event / evidence volume、recovery、review、operator attention 与交付工作量。

Phase 1 —— 打包 Enterprise Agent Harness。交付统一受支持发行物、private / BYOC profile、runtime adapter、console、部署升级、备份恢复、诊断，以及付费 discovery 与验收模板。

Phase 2 —— 只做少量付费 design partner。用有边界 FDE 推到生产、衡量结果、完成交接，并记录可复用与客户专属工作；不规模化长期免费 pilot。

Phase 3 —— 把重复工作抽成 domain pack 与 Team Evidence And Governance Plane。让第二次部署更快、让伙伴可交付，并围绕已证明的 workflow 增加 recurring license 或 managed-operation 收入。

Phase 4 —— 通过 BYOC 或 managed private 运行 Managed Semantic Control Plane。只有隔离、支持负担、复用频率和单位经济性得到证明后，才为低摩擦和海外客户增加多租户 Team Cloud。

每个阶段都可以独立交付，并为下一阶段产生证据；任何阶段都不需要让开源项目提前押注完整 hosted 终态，也不需要把公司变成通用系统集成商。

## 证据边界

本文刻意区分不同类型的公开证据：融资只能证明投资判断与 runway；公开价格只能证明存在变现面；厂商案例证明的是厂商与客户共同披露的部署；企业调查证明关注和自报采用，不能证明客户已经需要 LoopX 独特的 semantic control-plane contract。

现有证据足以支持 design partner 与有节制的商业假设，但不足以跳过 LoopX 自身对复用频率、业务结果、付费意愿和单位经济性的验证。

## 开闭源商业协同与 Plan B 物理分界

为了消除“开源协议扩散”与“商业 SaaS 变现”之间的结构张力，LoopX 将商业化路径直接锚定到四态全局指挥官评估体系（[evaluations/horizon.md](../releases/evaluations/horizon.md)）与仓库许可边界（[licensing.md](../../project/licensing.md)）之上，执行明确的 **Plan B Open-Core 物理拆分**：

- **开源主仓（`loopx-project/loopx`，Apache-2.0）**：永久拥有全部语义状态契约、单机 File / SQLite `AuthorityStore`、宿主/模型中立适配器、全部验证门禁、`openPostgresqlStore` SPI 与线级中立的出站 `RemoteAuthority` 客户端（已由 PR `#9223` 推进解耦，不在 OSS 仓内保留半托管服务端代码）。
- **私有商业仓（`loopx-project/loopx-cloud`）**：承载多租户 PostgreSQL RLS 服务、托管 `RemoteAuthority` 服务端、组织治理、计量计费与弹性编队调度。

### 四项永久 Apache-2.0 开源基线

1. **单机持久控制面内核**：File / SQLite `AuthorityStore`、`EffectTurn`、条件 CAS、硬租约与代数围栏、无损 `authority-archive` 导入导出（保证用户语义状态永久可迁移、无锁定）。
2. **运行时 / 模型 / 沙箱双中立与 `L1-L4` 渐进接入**：覆盖主流编码宿主的 CLI / ACP 适配器、`dsh-loopx-plugin` 与本地 worktree 沙箱隔离。
3. **跨主机与验证开放协议族（`open_protocol_core`）**：`StorageConfig` + `openPostgresqlStore` SPI、出站 `RemoteAuthority` 线级协议、管家移交/租约契约、`tau_B` + Stop-Hook 反混杂基准套件、`pr-review` / `canary` 及 `a+b` 发版评估门禁。
4. **本地全渠道工作台与社区领域包契约**：CLI、本地 Desktop / LooChat 工作台与 `domain_pack_contract_v0` 人工确认门模板。

### 六项闭源商业 Cloud / BYOC 护城河

1. **托管多租户 PostgreSQL 与 `RemoteAuthority` 服务端**：连接池、租户 RLS 隔离、高可用 WAL/PITR 容灾与跨区状态复制。
2. **企业级身份认证、组织治理与合规平面**：SSO / SCIM、细粒度 RBAC、集中凭证代理与不可篡改审计存证。
3. **跨机弹性智能体编队与百 Agent 调度器**：托管 10→100+ 协作编队、跨团队预算围栏、公平队列与防死循环 fleet 调度（承接 `4.5` / `4.6` 企业运行时）。
4. **企业级基准回放集群与回归智能中心**：机密代码库隔离回放舱、车队遥测聚合及组织级 Kendall `tau_b` 发布看板。
5. **托管全渠道网关与企业连接器枢纽**：多工作区飞书/Slack/Webhook 桥接与私有 VPC 中继通道。
6. **用量计量、计费、SLA 与行业认证商业包**：席位 + 控制面执行计量账单、合同级 SLA 保障与合规垂直行业能力包。

### 三层变现飞轮与代际演化联动

| 交付形态 | 控制面托管方 | 代码与执行位置 | 核心收入与增长飞轮 |
|---|---|---|---|
| **Tier 1 — Team Cloud（托管 SaaS）** | 托管 `loopx-cloud` | 云端托管工作区 + GitHub / 飞书协同 | 零运维团队开箱即用；席位费 + 控制面用量计费 |
| **Tier 2 — Hybrid BYOC（控制面托管 / 数据面留客户 VPC）** | 托管控制面或自建企业服务端 | 客户 VPC 内通过 SPI 与出站 `RemoteAuthority` 执行 | 满足合规与代码不出域诉求，同时保留云端协同、审计与回归遥测 |
| **Tier 3 — Enterprise Harness + 有界产品化 FDE** | 专属 `loopx-cloud` 集群 | 客户私有网络与内部 CI | 有界前线部署工程（FDE）：通用内核改进回流 OSS，客户专有连接器与高 SLA 转为年度平台经常性收入（ARR） |

所有来自 Tier 2-3 的通用状态机与协议改进均回流 Apache-2.0 `open_protocol_core` 战线（对应 [horizon.md](../releases/evaluations/horizon.md) 中的 `1.5`、`4.5`、`4.6`、`7.3`、`8.4`），形成 `2.x -> 3.0 -> 4.0` “开源协议建立标准 + Cloud/BYOC 承接规模组织护城河”的正向飞轮。

## 与现有文档的关系

- `../foundations/server-client-product-shape.md` 定义了本评估要商业化的 durable control-plane server、client 与 executor 角色。
- `../surfaces/README.md` 与 frontstage 笔记覆盖 hosted workspace 将扩展的 public presentation surface。
- `../domain-capability-packs.md` 定义了 marketplace 或企业集成可能商业化的 pack 边界。
- `../../reference/protocols/event-sourced-state-contract-v0.md` 以及 decision、goal、evidence、quota、handoff contract 定义了不能变成私有锁定的 portable semantic state。

本文刻意不给出定价承诺、发布日期和容量承诺。它定义 recurring value 可能落在哪里，以及 LoopX 在把这项技术期权视作生意之前还需要哪些证据。
