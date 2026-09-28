# App 会话与可复用的异步工作投递

- 状态：Accepted；设计和工作可认领。会话入口修复已交付，更多连续性和异步收件箱验收仍未完成。不新增 provider、调度器或权限。
- **替代 / 关闭：** none
- 基线：`27f0fc93b`，检查于 2026-09-25。实现与真实运行验收分别记录。
- 归属：[总体路线图](loopx-overall-roadmap-v0.md) R1–R3/G0–G2；
  [语义交接](capable-manager-semantic-handoff-v0.md) M1–M3；
  [会话界面](intelligent-review-presentation-surfaces-v0.md#88-reusable-conversation-work-surface)；
  [TS 迁移](typescript-control-plane-migration-v0.md) T0–T4。
- 评估：[steward 黄金查询](../../product/use-cases/steward/golden-queries.md)。
- 语言：[英文语义镜像](app-conversation-and-async-inbox-v0.md)。

## 决策：让 App 成为工作会话持续进行的地方

用户应能在 LoopX 中说“接着做，结果给我” / “Keep going and bring me the result”，
无需寻找另一个终端、复制上下文或在 Agent 之间搬运答案。优先完成安装后的 App 使用路径。
共享语义和证据，各 transport 独立验收。Lark 保留为受支持且有回归覆盖的 adapter，
App 改进无需等待 Lark。

这不只是显示远端对话记录。LoopX 必须接受请求，将其关联到现有负责工作和执行者，
投递纠偏、观察真实进度，并把可读结果返回同一会话。runtime 仍拥有执行责任。
Goal/Todo、lease、quota、验收和 effect 各自保留原有权限归属。加入会话不产生权限。

体验围绕五个问题组织：请求还在吗；谁确实在工作；纠偏或停止生效了吗；
核验过的结果在哪里；失败后如何回来且不重新启动工作？

### Managed 与 attached 是不同的执行关系

| 关系 | App 承诺 | 必需证据与限制 |
| --- | --- | --- |
| Managed | 从会话启动或继续已授权的 LoopX worker，观察并控制其实际 Turn | 稳定的请求→工作→session/Turn 关系、实际 model/effort、准入、受支持的中断、结果和重启读回。每个 binding 只有一个执行 driver |
| Attached | 连接现有已注册工作与 session，在保留上下文和原生入口的同时把后续互动移入 LoopX | 核验 host/session binding 与 adapter 能力。经受支持的 live control 或 inbox/next-Turn 路径投递；排队不等于采纳。不静默创建第二个 session 或启动竞争 driver |
| 未绑定或不可用 | 保留请求，解释缺失的连接、认证、权限或容量 | 提供现有受支持的连接、创建或修复路径；目录中存在不等于可用。只有实际歧义或缺失授权才需要询问 |

“迁移会话”指后续请求、结果和稳定链接的连续性，不授权复制原始 host rollout 数据库、
重绑定另一个身份或导入隐藏、私有历史。使用已授权的摘要和 artifact 引用。
原生 runtime 可以保持打开，但不能成为第二个不同步的工作事实源。
关闭 App 只停止观察，不停止执行。停止会话不隐式停止委派工作。

## 产品表达：借鉴什么，哪些尚未证实

[Lorca 发布帖](https://x.com/localhost_4173/status/2103454978220470708)
检查于 2026-09-25，内容是**静态截图**，没有已核验的互动或恢复演示。
截图展示具名会话列表、一个主会话、简洁的委派与返回记录，以及易读的回答块。
[创作者产品页](https://lorca.app/zh)描述单人和群组聊天、本地执行、具名 Agent 和跨设备继续。
这些属于创作者陈述；本次调研没有安装 Lorca，也没有验收其 runtime、隐私、正确性或长期运行保证。

[创作者会话文档](https://lorca.app/zh/docs/chats)描述流式回复、当前模型或工具步骤之后的方向更新，
以及桌面中断。提及对象交给 Agent 理解。文档还描述跨 Agent 接收者在各自会话回答：
LoopX 还必须核验综合结果返回原请求。这些是文档陈述，非观察到的执行。
不因匹配参考而引入固定转发次数限制或隐藏工具活动。

借鉴信息层次，不复制素材或未验证的承诺：

- 稳定、易读的角色和会话身份应比 runtime id 更易扫读。实际 model、host 和连接细节放在一步可达的位置。
- 活跃会话和结果获得最大的有效阅读空间。简洁的委派记录说明谁负责什么并链接实际工作；
  返回记录指向当前结果。不要让用户到另一会话寻找已请求的答案。
- 把一个有用的下一步放在对应失败或决定旁。折叠常规活动，保留缺失授权、过期信息和失败。
- 使用现有设计系统的字体、间距和克制的状态强调。动效解释已核验的转换，不虚构忙碌 worker。
- 保持创建、连接、直接与 owner 对话以及团队工作可发现。更简洁的界面不能隐藏未解决工作或减少授权范围内的 owner 发现。

预览使用公开或合成数据。检查有内容、安静、阻塞、不可用的桌面和窄屏视图。
保留键盘访问、阅读位置和返回上下文。首屏改动仍须遵循仓库预览门禁。
本设计不重新分发外部截图、私有事故记录或专有素材。

## 当前归属与缺口

| 已检查边界 | 现有实现 | 经现有 owner 解决的缺口 |
| --- | --- | --- |
| App 自由文本 | `personal-workspace-page.tsx`、`workspace-action-form.tsx` | 退役浏览器对 Goal 创建、Todo 改动、分配和调度的意图分类。所有自由文本完整到达 Chat；显式控件打开 typed form 与需审阅的 preview |
| Chat 请求与观察 | `chat_ingress.py`、`chat_store.py`、dashboard `data/chat.ts` | 现有 client ingress/Turn 身份与事件 cursor 可复用。证明响应丢失、同 key 不同 payload 和 reload 恢复后，才能宣称持久端到端入口成立 |
| 协作 | `control_plane/collaboration/inbox.py`、typed collaboration rules 与 return-delivery owner | 虽有历史存储名称，现有语义已对 Agent 中立。保留 decision/read/return 区别，不另建 manager 专用 inbox |
| Operator inbox | `control_plane/work_items/operator_inbox.py` | 已有 source contract 与共享 urgency projection；检查并迁移真实 pending/read/ack 生命周期，不新增泛化 wrapper |
| Lark transport | `extensions/lark/event_inbox.py`、`routed_inbox.py`、`inbox_reply.py` 与 reaction adapter | Provider 归一化、幂等 capture、read/processed 记录、reply recovery 与 Lark id/policy 共存。只提取已有证据的可复用语义；认证、寻址、provider id、reaction 和消息限制留在 adapter |
| 执行与控制 | 现有 managed Turn、attached-session/host binding、Chat steering/interrupt | 验收精确受支持的 profile。已注册或 inbox acknowledged 都不等于正在运行；原生 steering、next-Turn queue 和 unsupported 必须分别表达 |
| 结果 | Answer-report、artifact/revision、review/adoption 与 return owners | 读取已存版本，保留来源与独立审阅。report 读取失败重试读取，不重新运行模型 |

首个修复不需要新 capability：它属于现有 App 会话与 action 边界，内置 Chat/runtime provider 不变。
共享 inbox 工作属于现有 coordination/collaboration owners；Lark 仍是 extension 提供的 provider。
只有真实 provider-neutral 调用结果无法容纳于这些 owner 时，才重新考虑公开 capability。

## 分阶段交付与决定性查询

顺序以证据为依据，不承诺所有阶段在一个 PR 交付。每阶段完成一条有用的用户路径，
包括反例和读回。

| 优先级 / 阶段 | 自然查询 | 有用的出口、现有 owner 与下一依赖 |
| --- | --- | --- |
| P0 / 会话入口 | “解释一下 monitor 的工作原理。” / “Explain how a monitor works.” | 所选会话收到完整问题并返回答案；不产生无关 monitor/heartbeat preview 或调度写入。显式调度控件仍可用。R1 下首个有界 App 修复 |
| P0 / 连接并继续 | “用已经在跑的那个，接着做。” / “Continue with the one already running.” | GQ02 的 managed 与 attached 变体：一个已核验 owner/binding，保留上下文，实际 dispatch 或如实 queued 状态，结果返回原会话。确属新工作时遵循 GQ01；不强迫每个问题进入 Goal |
| P0 / 持久互动 | “先只看微软。” / “Focus on Microsoft.” | 同一工作上的 GQ07–09：steering 被采纳或明确 next-Turn queue、范围内停止、重新连接/reload/restart 恢复，不重复执行或丢失结果 |
| P0 / 小队 | “组个小队，把分歧查清楚。” / “Get a small team to resolve the disagreement.” | GQ05/GQ11–13：2–3 个真实 worker、两个周期、依赖消费、独立审阅、revision 采纳与原路综合返回；不需手工复制 |
| P1 / 注意力与移交 | “这周先做什么？” / “What comes first this week?” | GQ06/GQ10/GQ14–15：基于证据的优先级、材料与范围内重规划，保留 model/cost 限制；常规进度安静，已请求结果返回 |
| P2 / 扩展 | “本机安排，云上跑。” / “Plan here and run in the cloud.” | GQ16/GQ17 保留独立的跨 host 和规模验收。仅在有界本地路径通过后开始 |

首个修复不代表完整 GQ01/GQ02 或自主小队验收。
继续既有创建、连接、会话可靠性、affinity handoff 与小队 Todos，不创建重复计划队列。
每个可用阶段都运行打包和首次使用检查，不等到架构重写结束。

### 最近的可感知交付：一句话交办，过程可控，结果回来

先验收一条 G0/R3 真实路径，再扩充功能清单。用户说：**“给 LoopX 做份社区问卷，先给我草稿。”**
App 找到合格的已有负责人，保留请求，展示对方真实判断，在原对话返回带来源的可读 Markdown 草稿。
补充 **“先做中文，别发布。”** 后，实际接收方必须采用纠偏；发布不属于本草稿试点。

以 GQ02/GQ04/GQ08/GQ09 冻结验收：

- 用户不提供 Agent ID、旧会话链接，不重复背景，不催办、不搬运结果；接收方和任务目的清晰。
- 接收、延期、拒绝、执行、回传保持区分；延期回复送达不等于任务完成。
  延期原因在请求旁可见，接收方的私有判断不得自动泄露到外部受众。
- 一个获准驱动实际干活；纠偏和定向停止分别有接收方或运行时读回，传输 ACK 不够。
- 原对话可打开草稿，刷新或更换会话后仍在；源码、打包前端、真实原生执行分别记录通过、失败、阻塞或未运行。

随后验收已有 G1：**“组个小队，核对现金流，把分歧查清楚。”**
2–3 个真实 worker 消费版本化材料，独立指出期间或单位错误，采用修订后回传综合结论；
第二轮修改输入，必须改变实际消费依据。对应 GQ05/GQ11–13，不新增里程碑或任务队列。

在制工作集中于第一条路径及其实际阻碍，复用请求恢复、GoalRef 和延迟回传工作。
TS 重构随受影响事务推进；全量迁移、Lark 视觉对齐、规模化与宣传片不阻塞试点。
组件 PR 合并不代表试点已经通过。

## TS 与通用异步 inbox：随用户路径迁移

### 语义边界

复用现有持久身份和合同。概念关系是来源请求 → 具名接收者/工作 → 准入执行 → 结果 → 原路返回。
这些是各现有 owner 之间的链接，不要求把所有 message、Todo 和 artifact 合并到一张数据库表。

| 事实 | 含义 | 绝不隐含 |
| --- | --- | --- |
| Accepted/queued | 持久 owner 接受范围内请求或将其排队 | Worker 已启动或消息已采纳 |
| Supplied/read | 请求经受支持的接收路径暴露或读取 | 同意、责任移交或权限 |
| Adopted/declined/deferred | 接收者记录有依据的实际处置 | Artifact 独立验收或任务完成 |
| Executing | 当前 owner 提供真实执行证据与观察时间 | 可用 quota、打开的 Todo、在线注册或 ACK |
| Result ready | 有版本化结果；验证状态独立保留 | 原请求者已收到 |
| Returned | 原路径存在适用的投递/读回事实 | 用户已阅读、批准或下游已消费 |

工作、执行、transport、freshness 与验收是正交事实。
显示其有用组合，不虚构通用 `isActive`，也不强迫每个简单答案进入 adoption 流程。

### 替换节奏

1. **App 调用者优先（R1/T0）。** 在现有 Chat 路径复现输入、message 身份与 scope 竞态。
   在任何 store 迁移前修复所选的完整会话。操作归属现有语义执行者或显式控件时，
   删除浏览器 effect heuristic；不换成更大的关键词黑名单，也不在每条消息前加入付费 classifier。
2. **完整异步生命周期（R3/T1–T2）。** 清点 Chat、collaboration inbox 与 Lark 的 producer、
   持久记录、consumer、retry、return 和 cleanup。先刻画当前合法、非法转换。
   将一个内聚的 accept→pending→consume/disposition→return-recovery 生命周期迁入最近的 typed owner，
   source IO/provider adapter 放在其周围。App 与一个 Lark adapter 经此 owner 准入，证明 adapter-off isolation。
   不引入逐字段 RPC、dual write 或第二个持久队列。
3. **恢复与退役（T3–T4）。** 以明确 schema/read 兼容和原 id 迁移现有 pending 记录。
   在 acceptance 与 dispatch 之间、provider acceptance 与 reply 记录之间及 restart 中注入失败。
   只有取得真实路径 parity 和 schema-aware rollback 证据后，才退役旧 transition logic/writers。
   保留合法历史事实；rollback 不能重激活旧 executor 或重发已提交 effect。
4. **扩展。** App 与 Lark 各自验收后，在真实调用者需要时把共享合同应用于另一已授权入口。
   不只为给抽象命名而新增 broker、调度引擎、provider marketplace 或第三本任务账本。
   更广泛的持久化切换保留 D1–D3。

Provider 认证、签名、外部事件解码、寻址、chat membership、rate limit、附件和渲染留在 Lark。
通用 pending 选择、稳定请求身份、replay/disposition 与恢复规则不能依赖 `oc_`/`om_` id 或 bot reaction。
Notification/attention、Todo/lease、model admission 和 artifact acceptance 保留现有 owners。
Dispatch 事件在准入范围内唤醒现有 driver，polling 修补缺口。Inbox 不授权启动另一个 automation。

每次提取前报告 base/head 的真实调用 latency、边界穿越次数、bytes、删除或保留的 owners 与兼容调用者。
产品交付无需等待 Python 完全退役。Python 可保留 IO，TS 对已迁移 transition 和 effect 拥有唯一责任。
适用时复用现有 TS receipt 和 CAS 机制，不把 Chat 记录伪装成 Todo command。

## 验收与失败矩阵

运行前冻结 source/package/runtime/profile、公开输入、budget、timeout 与独立预期结果。
分别记录通过、失败和未测试；浏览器 fixture 不能验收真实 attached host。

| 边界 | 必需反例与可观察结果 |
| --- | --- |
| 含义 | 解释、引用、否定、混合语言和将来条件句提到调度时仍为会话。显式 UI 调度仍到达需审阅的 typed 路径 |
| Acceptance | 持久接受后丢失响应：以同一范围内身份恢复，只有一个逻辑 Turn/provider 调用。同 id 不同文本冲突；有意重复的新请求仍可提交 |
| Dispatch | 接受后、启动前失败：现有恢复继续请求。没有执行的 ACK 标为 queued 并给出下一 trigger，不标 running |
| Scope | 导航 A→B→A、迟到响应、旧 subscription 终态事件：只更新原 source/session/Turn。完整 snapshot 与 delta stream 采用不同合并规则 |
| Stream | 重复或迟到事件与 hydrate 重叠保留一个逻辑答案。用户阅读历史时新事件不强制滚动 |
| 纠偏/停止 | 工具执行中和完成时：实际接收者采纳最新 scope，或报告 queued/unsupported。停止针对原 Turn，不隐式停止其后继或所有 peer |
| 结果 | 文件缺失仅重试读取；v1 review 不认证 v2；打开 report 不等于 adoption。Return ACK 丢失先 reconcile 再再次发送 |
| Attached | 原生 host 离线、stale binding、不支持 steering、只支持 next-Turn 的 adapter 与 restart：请求保持可见；不猜测成功或引入竞争 driver |
| Managed | Runtime 启动失败、quota 拒绝、缺失登录、stop/restart：实际 profile 和条件可读；不静默替换 model/account |
| 权限 | 撤销访问或 source 改变拒绝 stale effect；无关且被允许的分支继续。私有历史不进入共享 audience |
| 打包 | 受支持的本地安装 bundle 完成首个请求与返回；窄屏、键盘、reload、不可用和安静情况仍可用 |

测量首个有用结果耗时、恢复、定位答案、不必要的人工转发、状态误读与每个验收结果的成本。
分别保留 setup、首结果与 recovery 耗时。保留黄金查询集冻结的注意力比较；本设计不宣称已测得改进。

## 交付边界

当前会话入口修复移除 App 中所有浏览器自由文本 action 分类，检查普通 Chat 路径及显式调度控件。
不改变权限或已存 message schema。Managed/attached 会话连续性、通用 TS inbox 提取和真实运行的两周期小队验收，
在各自证据记录前仍属计划。该入口修复可按 App routing 改动回滚；后续持久合同迁移需要各自兼容计划。

### 入口行为兼容

所有普通输入现经所选会话处理，包括创建或改变工作的请求。
Runtime 工具支持、授权和现有 action review 仍决定实际执行内容；移除浏览器 classifier 不认证模型完成 GQ01–GQ17 的能力。
显式创建和调度控件打开基于字段的 form，且只创建需审阅的 preview。
Goal 权限在 form 中显式选择，保留既有“确认后允许修改工作区”默认值；权限不再从边界描述推断。
下游须同时尊重权限选择与书面执行边界。
显式 status-only profile 返回有标签的 snapshot，不返回以关键词构造的答案。
把回复转为任务时打开完整可编辑文本，不猜测其中的下一步句子。
保留结构化 ID、date 与 resume-condition 验证。Lark routing 不变。

### 对话式目标准备：吸收团队工作台提案

[KashiwaByte](https://github.com/KashiwaByte) 的 [PR #4376](https://github.com/loopx-project/loopx/pull/4376)
带来了值得吸收的交互：每次提出一个关键问题，用贴合上下文的建议回复帮助用户理清目标。
将它接入现有 App 对话和 Goal 操作路径；不采用独立工作台、JSON 存储、子进程执行器和调度器。
未发布原型从本 PR 的最终产品差异中移除，贡献与原始实现保留在提交历史中。

| 思想 | 现有归属与验收 |
| --- | --- |
| 对话式目标草稿与情境选项 | R1 / GQ01：共享类型化 `goal_draft`、可复用 App 卡片与可编辑创建表单。选项只填入输入框，用户发送后才继续；未知要求留空，不新增正则意图分类器或自动创建 |
| 请人补材料、执行工作或判断结果 | 既有 operator inbox、user gate 与复核/采用契约。区分三类决定；回复不等于授予委派权限。完整验收仍开放 |
| 记住纠偏与协作者所长 | 既有作用域 brief/context 与能力记忆 owner。纠偏可影响后续工作，但不产生权限、不证明能力，也不隐式改变执行绑定 |
| 滚动计划与独立检查 | R2/R3 工作图、managed/attached Turn 与独立验收 owner。仍须证明真实依赖采用、纠偏、停止和结果回传；目标草稿不代表团队已验收 |
| 可选远程执行器 | 既有扩展与执行 profile 契约。没有真实调用者、显式绑定与生命周期验收，不引入新 provider |

本次实现是 provider 响应中的建议，不是第二个规划器或 Goal 真相源。
现有 collaboration 中的 TypeScript owner 校验结构，Python 负责传输脱敏，草稿随完成消息持久化。
App 历史与重连读取同一条消息；managed 和 attached 完成存储均保留该可选字段。
普通回答与非法草稿保持原响应契约。Provider 必须实际返回结构化建议；存储/UI 验收不代表模型意图质量或真实 attached-host 工作链已验证。

准备新目标之前，先根据当前对话和可见工作识别已有任务。继续或纠偏沿用合格的原负责人和既有委派；当前 Goal Chat 的追问保留在原对话。比较所有合理候选；身份有歧义才询问，权限缺失或目标停止不能变成新建替代目标的理由。用户明确要求独立目标时可以主题重叠。这是模型结合宿主证据的语义选择，不是关键词路由或第二套发现服务。TypeScript 保证委派、受保护操作、提案或 gate 不能同时携带新目标草稿；宿主仍验证接收权限。

完整草稿直接进入类型化 `goal.create` 预览，只需一次显式应用；修改是可选入口，复用同一请求构造器和既有表单。信息不全的草稿可继续补充。同一来源消息与草稿重开预览保持操作身份，另一条消息则是另一请求。草稿默认只读且不启用 heartbeat，原显式创建入口保持默认行为。工作区、负责人、权限检查继续生效；创建不代表执行或交付。Lark 共享语义回答与委派行为，草稿卡片和直接预览仅在 App 提供，不增加 capability、provider 或调度器。

[公开模型评测](../../../examples/evaluations/chat-intake.py) 仅在 release 候选版本验收时显式启用付费调用，默认及新增宣传的模型配置分别至少重复两次。平时 PR、每次提交和 heartbeat 只跑离线回归及受影响的浏览器场景，不运行付费评测。记录候选版本，保留失败；凭证不可用记为跳过，不视为通过。评测使用生产 prompt/parser 和固定公开上下文，覆盖新建、已有负责人、多候选、停止/无授权、纠偏、当前 Goal 追问、引用、否定、普通问答与中英文表达。冲突操作、协议缺失、生成截断均记录为失败。记录模型、prompt/用例哈希、请求参数、token 用量和重复次数，不泄露凭证。该层只验证模型解释已提供证据，不证明真实发现、派发、停止执行或完整 GQ01/GQ02；打包浏览器和实际委派传输测试分别覆盖其边界。
