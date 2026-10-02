# 如何使用本书

本书面向已经会使用 Git、终端和至少一种 Agent 开发工具的外部开发者。你不需要先读
LoopX Kernel 源码，也不需要理解所有 CLI 子命令。

## 你会完成什么

全书先用八个编号章节和一张状态机地图建立架构模型，再进入项目接入与开发者贡献两条实践主线：

```text
控制面基础
├── 接入现有 Git 项目
└── 开发者贡献
    ├── Control Plane、Capability 与 Domain State
    ├── Provider、Host/Runner、Projection、Docs 与 fixtures
    └── Extension 与独立 package lifecycle
```

基础篇依次覆盖：

1. [从一次会话到长程任务](01-from-session-to-loop.md)：为什么需要会话外的控制信息；
2. [会话、Host Goal 与 LoopX](02-session-goal-loopx.md)：各自拥有的状态与任务资格；
3. [长程运行提出的四个要求](02b-long-horizon-requirements.md)：整本书的问题框架；
4. [持久状态与只读投影](state-substrate.md)：事实源、历史与当前视图；
5. [工作图、权限与 Peer 协作](work-graph-and-authority.md)：工作归属与提交边界；
6. [一轮受治理的工作](03-one-turn.md)：执行、验证、写回与可恢复结算；
7. [恢复、自修复与运行边界](04-runtime-boundaries.md)：多轮推进、重新规划与停止；
8. [预算、准入与观察](04b-budget-and-admission.md)：何时运行、等待和再次观察。

[主要状态机与状态流转](core-state-machines.md)是一张可回查的专题地图。可以先读完一次 Turn，再回来把各机制连成整体，不必首次阅读就记住全部枚举。

## 用同一项任务贯穿全书 {#running-example}

后文沿用一个合成任务：给现有 CLI 增加兼容的 JSON 输出，补文档，等待 CI 与 schema 决定后交付。以下代号只用于教学，不是可直接导入的 Todo id 或 API payload。

| 代号 | 工作与责任 | 接受条件 |
| --- | --- | --- |
| T1 / Agent A | 实现 JSON 输出，保留默认文本行为 | 对 commit C1 的本地兼容测试与受控写回 |
| T2 / Agent B | 补使用文档，可与 T1 并行 | 示例与约定一致，产物可审阅 |
| M1 | 观察 C1 的远端 CI | 有来源与 revision 的实际读回 |
| G1 | 维护者决定 schema / 发布范围 | 对象与 scope 明确的批准 |
| T3 | 发布或交付结果 | T1/T2、CI 与 G1 等当前条件均满足 |

T1 的一次写回可留下回执 R1，但它不自动关闭 G1 或接受整个 Goal。若代码变为 C2，M1 对 C1 的旧观察不能验收新提交；新工作或恢复动作都要重新判断当前条件。

首次阅读可以走“[四个问题](02b-long-horizon-requirements.md) → [完整一轮](03-one-turn.md#running-turn) → [状态](state-substrate.md#design-choice) → [权限](work-graph-and-authority.md#design-choice) → [恢复](04-runtime-boundaries.md#receipt-recovery) → [等待](04b-budget-and-admission.md#running-wait)”这条路线，再用状态机专题回查细节。

## Dev Book 与 Control-Plane Course 如何配合

本书与仓库中的
[Control-Plane Developer Course](/loopx/docs/development/control-plane-course/)
服务不同的阅读任务：

- **Dev Book** 面向外部开发者，先建立足够完整的机制模型，再帮助你接入项目或完成一次公开贡献；
- **Control-Plane Course** 面向准备深入 Kernel、CLI、状态投影和调度实现的开发者，提供
  Showcase 推导、decision table、源码领读、实验与 review 问题。

二者共享官方协议与源码事实，但不维护两份完整课程。Dev Book 会把预测行为所需的机制讲完整；
当你需要判断规则优先级、定位具体 bounded context 或修改实现时，再沿章节末尾的入口进入课程。
准备深入 Kernel 的开发者也可以直接进入
[Control-Plane Course 独立章节](./12-control-plane-course.md)。

- **多个短 Session 怎样组成长程任务？** 先读[会话到长程任务](01-from-session-to-loop.md)与[任务资格](02-session-goal-loopx.md)，再下钻
  [概念导读](/loopx/docs/development/control-plane-course/00-concept-primer/)、
  [第 1 讲：Harness 是 effectful program](/loopx/docs/development/control-plane-course/01-agent-loop-effectful-program/)与
  [第 2 讲](/loopx/docs/development/control-plane-course/02-goal-control-plane-architecture/)，
  再用[第 3 讲](/loopx/docs/development/control-plane-course/03-first-real-loop/)走一遍真实 Loop。
- **状态、工作图与权限分别由谁拥有？** 先读[状态底座](state-substrate.md)、[工作图](work-graph-and-authority.md)和
  [主要状态机与状态流转](./core-state-machines.md)，再下钻
  [第 4 讲](/loopx/docs/development/control-plane-course/04-state-substrate/)与
  [第 5 讲](/loopx/docs/development/control-plane-course/05-work-graph-and-peers/)。
- **Gate、Monitor、Replan 同时出现时哪条规则优先？** 先读
  [主要状态机与状态流转](./core-state-machines.md)和[一轮受治理的工作](03-one-turn.md)，再下钻
  [第 6 讲](/loopx/docs/development/control-plane-course/06-quota-decision-kernel/)与
  [第 7 讲](/loopx/docs/development/control-plane-course/07-host-scheduler-and-heartbeat/)。
- **长程任务怎样防止目标漂移与局部空转？** 先读[恢复与运行边界](04-runtime-boundaries.md)，再下钻
  [长程收敛专题](/loopx/docs/development/control-plane-course/topic-long-horizon-convergence/)与
  [第 8 讲](/loopx/docs/development/control-plane-course/08-evidence-refresh-and-self-repair/)。
- **怎样修改规则并证明它可交付？** 先读[贡献地图](source-protocol-map.md)、[规则修改](source-change-control-plane-rule.md)与[验证到 PR](source-validation-to-pr.md)，再下钻
  [第 9 讲](/loopx/docs/development/control-plane-course/09-engineering-a-control-plane-rule/)与
  [第 10 讲](/loopx/docs/development/control-plane-course/10-autonomous-agent-quality-gates/)。
- **Extension、领域能力与 Kernel 怎样组合？** 先读[放置位置](08-extension-placement.md)、[scaffold](09-extension-scaffold.md)与[生命周期](10-extension-lifecycle.md)，再下钻
  [第 11 讲](/loopx/docs/development/control-plane-course/11-extension-layer/)。

完成基础篇后：

- 想先掌握 1.0 的日常操作面，从[操作 LoopX 1.0 Workspace](./workspace-v1.md)开始；
- 只想管理自己的项目，从[连接你的 Git 项目](./05-connect-existing-project.md)开始；
- 想给 LoopX 做任何公开贡献，从[开发者贡献地图与协议入口](./source-protocol-map.md)开始；
- 已经确定需要独立安装、启停和升级的 Provider/package，再进入
  [选择正确的放置位置](./08-extension-placement.md)。

项目接入与开发者贡献共享基础模型，但没有先后依赖。Extension 是开发者贡献中的一种交付和
lifecycle 选择，不是所有贡献的默认终点。

## 这本书的主线

正文围绕“LoopX 为什么这样设计”展开，落点是如何使用、何时等待、怎样恢复及边界在哪里。四个问题帮助串联架构：

| 问题 | 架构解释 | 使用落点 |
| --- | --- | --- |
| 上下文中断后凭什么接着做？ | 持久状态与投影 | 找当前 authority 与有效 evidence |
| 半轮工作如何处理？ | journal、回执、恢复判定 | 复用已提交结果，确认未知副作用 |
| 多个执行者怎样协作？ | 工作图、scope、lease 与围栏 | 核对当前 writer 的模式与权限 |
| 没有变化时如何少消耗？ | 预算、准入、观察与退避 | 检查等待对象、due 与唤醒能力 |

每章从一个具体问题进入设计，再说明成立条件、实际代价和下一步操作。源码与 RFC 用来验证这些解释；深入实现时再进入贡献路线和 Course。

## 按一本书，而不是命令清单来读 {#book-journey}

先看清完整工作，再逐步进入它的内部。这里的学习路线不要求先记住全部协议名：

| 学习阶段 | 继续阅读 | 这一段结束时，应能完成什么 |
| --- | --- | --- |
| 判断是否需要控制面 | 会话到长程任务、Host/Goal、四个要求 | 说明哪些事实需要跨会话，哪些短任务不值得增加治理成本 |
| 理解一次正常交付 | 一轮受治理的工作，再回看状态与权限 | 从当前事实走到受控动作、验证与接受，分清各层 owner |
| 理解工作如何延续 | 恢复、观察与状态机专题 | 区分未知结果、合法等待、漂移和停止，不以重启代替判断 |
| 实际接入并返回结果 | 项目接入、App/CLI、Workspace、适用边界 | 选定真实运行路径，检查第一份产物，说明剩余责任和下一入口 |
| 安全地改变系统 | 贡献地图、协议链、规则修改、验证到 PR、Extension 与工程边界 | 从一个真实问题找到 owner，完成必要实现、反例、兼容与交付证据 |

概念章解释因果和设计取舍；实践章给出前提、动作、读回和失败出口；贡献章沿协议与实现寻找
第一处需要改变的边界。它们不必都写成同一张四列表，但不能把关键推理留给读者猜。
状态机专题和附录供回查，不是要求第一次接入前全部背完的考试范围。

[项目接入章](05-connect-existing-project.md#three-completions)区分安装、接入与工作交付，
其[第一份交付](05-connect-existing-project.md#first-delivery)把 T1/T2、CI、决定与最终返回接在一起。
按实际 Host 完成激活后回到这条主线。只操作项目时不必先运行源码测试；准备贡献时，再用
[课程练习](12-control-plane-course.md#reader-checkpoints)检验自己能否预测行为。

命令片段会标明其性质：**可直接运行**仍须满足标注版本、输入与授权；**基于官方 scaffold**
沿已有教学包练习，不另造 runtime；**为解释而简化**的表格、代号和数据形状不应直接写入生产配置。
预览、实际执行、读回和测试结果分别记录，不能把“已经给出命令”写成“已经运行成功”。

## 用四个问题验收理解 {#judgment-standard}

前面的架构问题解释机制为何存在；这里的四问检查一个具体结论是否已经讲完整：

| 问题 | 合格答案应包含 | 不足以作为答案 |
| --- | --- | --- |
| 它依据什么事实？ | 对象 identity、当前来源、适用模式和版本/时间；区分历史与当前 | “页面这样写”“上次成功过” |
| 为什么允许或拒绝？ | 哪个 owner 的规则、哪些条件成立或缺失，以及结论的作用范围 | “有余额”“没有报错”“系统不让” |
| 哪项证据支持结论？ | 绑定正确对象的读回、验证或回执，以及它没有证明什么 | 只引用测试标题、构建成功或执行者自述 |
| 下一步应该找哪个入口？ | 合法读取/操作入口、执行条件、操作后的读回，以及何时停止并求助 | “重新跑一遍”“先关掉检查” |

例如，C1 的测试通过、T1 已结算，但当前代码已是 C2 且 G1 尚未批准发布。完整解释不仅是
“不能发布”，还要说明旧证据绑定 C1、结算不授予发布权、哪些 C2 检查与决定仍需取得，
以及独立的 T2 是否仍有合法工作。这条推理在[接入与交付](05-connect-existing-project.md#first-delivery)
中展开，在[综合练习](12-control-plane-course.md#integrated-judgment)中由读者自己完成。

这四问是教学与修订标准，不新增 schema、运行时义务或审批门禁。资料不足时，应明确
“尚不能判断什么、缺少哪项证据、由谁确认”；不能为了填满四格而猜测。
拒绝也不是最终目标：条件补齐后要重新判断，保留已经发生的工作，并走向下一次合法行动。

## 怎样理解书中的证据

| 文字类型 | 应怎样阅读 |
| --- | --- |
| 当前实现的行为 | 对照本书版本、实际入口、authority mode 与回执；不要扩展到所有 Host/provider |
| RFC 的设计目标 | 查看 milestone、ledger 与交付边界；Accepted 不表示每项能力已发布 |
| 教学情境或使用建议 | 用来解释取舍，不作为真实事故或机器强制规则 |
| 测试与 smoke | 只证明实际覆盖的输入和断言；标题/表格对齐、路径存在与构建通过不证明语义正确 |

修订章节时，优先核对会影响操作的强断言与中英义务句。没有证据支持的保证应收窄，已退役的模型应退出当前路径；不能为了保留原文字数继续教授旧行为。

## 权威来源

本书拥有教学顺序和解释，不拥有 LoopX 的版本化行为：
中英文版本共享同一组产品事实。中文根版本是编辑事实源；英文版为英文外部开发者组织，不作为
独立的产品规格维护。中文与英文版本是语义镜像；版本事实、状态、命令、边界或结论出现实质差异都属于文档缺陷。

| 内容 | 权威来源 |
| --- | --- |
| CLI 参数、协议和 runtime 行为 | LoopX 发布物、`--help` 与官方仓库 |
| 学习路径、scaffold 导读、概念解释与取舍建议 | 本书 |
| Kernel 源码领读、组合 case、decision table 与实验路线 | Control-Plane Developer Course |
| 你的项目事实 | Git、CI、外部服务和项目自己的事实源 |

当本书与当前发布版本冲突时，先以发布物为准，再提交文档修正。不要为了让教程“跑通”而绕过
新版本的权限或生命周期检查。

## 版本基线

当前内容面向 LoopX release `v1.2.4`；本地命令示例已按 `loopx 1.2.4` 的
公开 CLI 与协议表面复核。该版本要求 Python 3.11+ 与 Node.js 22.22.3+。LoopX 会自动管理
空闲后退出的 TypeScript Effect runtime，用户不需要手工维护 daemon。

发布标签、已安装 CLI 与源码 checkout 可能处于不同 revision，因此以下表面尤其需要按实际环境复核：

- 安装与升级；
- Host 启动方式；
- `start-goal` guided packet；
- Codex App heartbeat、Codex CLI visible Goal 和其他可选 Host；
- TypeScript Effect runtime readiness；
- Extension manifest 与生命周期命令。

运行书中命令前先执行：

```bash
loopx --version
loopx doctor
node --version
```

如果版本不同，先查看当前命令帮助和官方 release notes，再判断差异是文档漂移、发布差异还是
产品行为变化。本书不猜测不同版本标识之间的发布含义。

### 如何理解 TypeScript 改造的历史基线

本节解释 `v0.5.4` 的历史迁移背景，不把它当作当前安装版本。当前版本仍以上一节的 release 基线
和实际 `--version` 为准；下列阶段说明不能反推所有当前 Host/provider 的交付状态。
`v0.5.4` 不是“已经把 LoopX 全部重写成 TypeScript”：

- TypeScript 已拥有 Effect Program、Turn/Host Todo settlement、Todo completion、quota
  delivery/spend/void/monitor-poll、本地 task-lease lifecycle、Vision refresh、governed capability
  validation，以及 scheduler heartbeat/state 等迁移切片的 canonical semantics；
- Python CLI 仍负责迁移期 transport、legacy response projection、明确的外部 Provider 调用和部分
  Markdown/event 写回；
- 同一条迁移后的规则只能有一个 semantic owner。Python facade 适配 TypeScript transaction，
  不能再实现第二套独立 decision；
- 当前 `main` 已进入 transaction-payoff phase：后续以完整 transaction cutover 和删除旧语义
  为进展单位，不继续堆 leaf helper 与 bridge。

核对这一历史里程碑时，以 `v0.5.4` tag 和 release notes 为准。历史记录中的
Stage 3/Stage 4 是阶段边界，不是当前支持矩阵；后续 cutover 与 CLI/App 收敛以
[TypeScript Control-Plane Migration RFC](/loopx/docs/architecture/rfcs/typescript-control-plane-migration-v0/)
的当前状态为准。`v0.5.4` 只交付了 Stage 3 的第一个 receipt-bound scheduler follow-up 切片；更广的
CLI/App convergence 与 Stage 4 distribution cleanup 仍是后续方向。

### 从 `v0.4.4` 阅读基线升级到 `v0.5.4`

如果你读过旧版 Dev Book，优先校准五个变化：

| 主题 | `v0.5.4` 已发布事实 | 深入入口 |
| --- | --- | --- |
| Control Plane | Turn/Host Todo settlement、quota commit、task-lease lifecycle、Vision refresh 与 scheduler heartbeat 等完整事务已迁入 typed TypeScript owner；Python facade 仍承担迁移期边界 | [迁移 RFC](/loopx/docs/architecture/rfcs/typescript-control-plane-migration-v0/) |
| Operator surface | Personal Workspace 已提供 Goal、Task、Chat 和 read-only status source 入口；界面不是新的事实源 | [Dashboard README](https://github.com/huangruiteng/loopx/blob/v0.5.4/apps/presentation/dashboard/README.md) |
| Host runtime | Codex、Claude Code、OpenCode、Pi、KunlunCode、DeepSeek Harness 与 custom runner 具有不同 activation/stop contract | [Runtime Connector Catalog](/loopx/docs/integrations/runtime-connector-catalog/) |
| Capability / Provider | Capability 由 package-owned catalog entry、真实 command 和 durable validation 共同定义；Provider/Extension 不继承 Kernel authority | [Capability Catalog](/loopx/docs/capabilities/) |
| Shared authority | file、NoKV 与 PostgreSQL provider 仍是 staged candidate；安装 Provider 不会改变默认本地 authority | [Shared Authority RFC](/loopx/docs/architecture/rfcs/shared-goal-authority-state-provider-v0/) |

这张表是阅读导航，不是 release notes 的副本。某个 surface 是否可用，仍应从当前安装版本的
`doctor`、`capability show`、对应 Host readback 和 versioned 文档判断。

### 从 `v0.5.4` 进入 `v1.0.0` Workspace

`v1.0.0` 的产品里程碑是 Personal Workspace，而不是一次对所有 staged authority 或可选 Provider
的整体提升。它把跨 Goal 总览、Agent lane、已完成任务、Capability 设置、verified reports、
Goal Channel 与桌面恢复汇集到一个 operator surface，同时保留 CLI、typed Kernel 与项目状态的
事实所有权。沿[1.0 Workspace 操作章](./workspace-v1.md)完成启动、readback、preview/apply/receipt、
配置与停用验收，再进入项目接入或开发者贡献主线。

## 本书的边界

开发者贡献部分覆盖外部贡献者需要的 placement、协议地图、规则修改、Capability/Provider、
Host/Runner、Projection/Docs/fixtures、Extension lifecycle、验证与 PR，不复制完整的核心
维护者课程，也不提供完整 CLI reference。生产级 effectful provider、企业内部案例和 benchmark
live operation 不进入当前主线。需要这些能力时，应回到官方源码、协议和具体项目的事实源。

接入主线只以 Codex App 与 Codex CLI 为例，因为这两条路径可以端到端复现。LoopX 的 agent type
覆盖面更宽：`loopx/host_loop_activation.py` 的 `SUPPORTED_AGENT_TYPES` 列出 21 个类型，其中 9 个
另有独立的 goal-mode 适配包（`claude_goal_mode`、`kunluncode_goal_mode`、`opencode_goal_mode`、
`pi_goal_mode`、`zcode_goal_mode`、`agy_goal_mode`、`dsh_goal_mode`、`kiro_cli_goal_mode` 等）。
路线选择是教学取舍，不是能力边界：其余 harness 的接入差异，以各自适配包和
[协议文档](/loopx/docs/reference/protocols/) 为准。
