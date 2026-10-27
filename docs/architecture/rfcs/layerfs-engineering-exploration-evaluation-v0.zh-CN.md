# RFC：LoopX × LayerFS——复杂工程探索与效果评测（v0）

- **RFC status:** Accepted (合并后生效；本变更仍为提案)
- **替代 / 关闭：** 无
- **Delivery maturity:** Proposal；尚无集成实现、实验结果或产品推广结论。
- **Authors / owners:** LoopX 探索与 benchmark 维护者；邀请 LayerFS 维护者评审 provider 边界，不替对方分配工作。
- **Created:** 2026-10-26
- **Last normative revision:** 2026-10-26
- **Implementation baseline:** LoopX `15803cd7dcc2fc517049809bbe58998f04f5acac`；LayerFS 源码 `ad78914e36b258b360daf13aec164b5655814689`。
- **Tracking:** [LoopX #7406](https://github.com/loopx-project/loopx/issues/7406) · [LayerFS #461](https://github.com/Ephemeral-AI-Lab/layerfs/issues/461)。
- **Language mirror:** [English](layerfs-engineering-exploration-evaluation-v0.md)
- **Related contracts:** [Explore](research-exploration-control-plane-v0.zh-CN.md)、[研究状态分叉](hierarchical-agent-stride-control-v0.zh-CN.md)、[度量契约](long-horizon-harness-benchmark-research-program-v0.zh-CN.md)、[整体路线](loopx-overall-roadmap-v0.zh-CN.md)。

## 文档地图与维护契约

第 1–3、5–11 节规定拟议实验与集成边界；第 4 节记录源码观察，不代表真实运行资格。
第 12 节列出执行前待定事项。附录 A 保存来源。中英文保持语义同步。
合并接受的是设计基础，不是运行时默认值、benchmark 结果、支出承诺或上游实施承诺。
后续实测 checkpoint 使用现有 [RFC ledger](ledger/README.zh-CN.md)。

## 1. 决策摘要

将 LayerFS 作为**多 Agent 工程探索的可选执行工作区 provider**进行评估。
LoopX 保持问题、假设、证据、工作归属、预算与接续的权威；LayerFS 保存可分叉的
文件系统状态；Host 保持模型会话与工具执行；Git 保持源码评审交付边界。
本提案不新增通用 capability 或 scheduler。

第一个实验检验：多个 Agent 能否在复杂修复中复用已准备好的环境，排除一条路线，
把有潜力的 checkpoint 交给接手者，并以更低总时间或总成本交付独立验证的补丁。
更多分支、更快 mount、更多 checkpoint 本身不算进展。

从单 Host、三个 worker 开始，与预热 Git worktree 使用相同模型和总预算。
先验证工作区正确性，再运行模型。联合收益归因保留 native/passive/governed 对照；
没有收益也是有效结果。集成默认关闭。

## 2. 问题与假设

多包工程修复中的不同解释常常需要不同依赖版本、生成代码、构建产物和测试状态。
Git 能很好地保存跟踪源码；但短分支的环境重建、有效未跟踪状态的交接可能占据大量时间。
完整复制环境消耗磁盘，共享可写缓存又可能污染对比。另一方面，长期保留的预热
worktree 已经避免了很多重复准备，不能忽略这条强基线。

| 假设 | 可观察证据 | 反证或适用边界 |
| --- | --- | --- |
| H1：分叉完整预热环境可降低探索开销 | 恢复状态等价时，计入初始化摊销后的 fork→可测试、capture、handoff 成本更低 | mount 虽快，commit、I/O、清理或首次导入抵消收益 |
| H2：结构化证据结合低成本环境分叉，改善工程成果 | 固定总预算下独立验收通过的修复更多，或同等成果的完整成本更低 | 尝试次数和历史展示更丰富，成果却不变或变差 |
| H3：接手者能复用有效候选，减少重复准备和调查 | 接手者验证精确候选，采用相关 finding，执行下一项有效工作 | 只有读取回执、文件分叉或复制摘要，没有实际采用 |

最可能适合的是频繁分叉、环境准备昂贵且可复用的任务。
只读代码搜索、少量常驻 checkout、大文件顺序 I/O，或主要时间都花在模型思考上的任务，
可能收益很小甚至退化。文件系统去重不等于模型推理或上下文 token 去重。

## 3. 范围与非目标

本切片包含公开工程 fixture、隔离的一次性工作区、候选与结果谱系、一条端到端 demo
和可复现的比较研究。对应路线 **S4/S8/S11**，继承 S3/S7 的权限和预算边界；
不替代 R2/R3 团队资格验证。

不包含：替换 Goal store、全局记忆、新 Agent 框架、生产级持久性、不可信多租户隔离、
跨机器共享 Store 写入、自动 Git 发布、进程/VM 完整快照、自主 MCTS 策略，
或把 LayerFS 2.0 路线项当作当前支持。

## 4. 已审阅的当前系统契约

### LoopX

- [`explore`](../../../loopx/capabilities/explore/README.md) 已拥有节点关联 finding、
  candidate/input 身份、有界度量反馈和 execution observation。
  度量和观察只是生产方证据，不直接证明收益；复用其 TypeScript 决策 owner。
- [Stride §7.7](hierarchical-agent-stride-control-v0.zh-CN.md) 的完整研究点涵盖对话、
  证据、产物与评测机会。现有 [capture adapter](../../../benchmark/runtime/checkpoint.py)
  和 [artifact owner](../../../loopx/control_plane/runtime/checkpoint_artifacts.ts)
  尚不接受任意 LayerFS 引用。当前文件 capture 路径拒绝 symlink，不能直接把 pnpm
  环境树塞入现有路径。
- [DSH plugin](../../../packages/dsh-loopx-plugin/README.md) 提供原生绑定、接续和
  Goal 回读，可作为组合 Host，但当前不等于已有 LayerFS 集成。

### LayerFS

以下事实针对上述精确源码；1.1.0 release 与持续变化的网站文档不能替代该 commit。
源码来源见附录 A。

| 机制 | 对集成的影响 |
| --- | --- |
| SQLite 内容寻址 Store；fork 共享已提交对象；Workspace 挂载可写视图 | 候选可以保留源码、依赖和生成产物，避免完整 payload 复制；初始化和后续写入仍有成本 |
| `@layerfs/agent`、`@layerfs/backend`、`@layerfs/runtime` 分层 | 使用同一精确版本的公开 API，不依赖 `@layerfs/agent/internal` |
| 文件变更调用会提交；`workspace.exec`、`workspace.commit`、`workspace.unmount` 是独立调用 | 集成方负责每工具调用的生命周期；一次 Bash 不自动成为持久 checkpoint |
| 直接打开 Store 要求同一 Linux kernel、本地 ext4/xfs/btrfs；macOS/Windows 有 worker 路由 | 先验证 Linux 或单 Linux VM；单独验证 macOS worker 配置，包括上游 [#456](https://github.com/Ephemeral-AI-Lab/layerfs/issues/456) |
| Disposable profile、WAL、synchronous OFF；不保证崩溃/掉电持久性 | LoopX 权威状态与源码/结果的独立副本留在 Store 外；不宣传为备份，不把不同持久性的 fsync 速度直接比较 |
| 共享发布通道；冲突、`.lww`、有界诊断、显式未知结果 | 记录竞争与拒绝；源码、lockfile、评测输入不能自动 last-writer-wins；回复丢失不能触发自动重试 |
| 追加历史；尚无 branch retirement / 不可达历史 GC（[#411](https://github.com/Ephemeral-AI-Lab/layerfs/issues/411)） | 丢弃 Workspace 不回收已提交分支历史；计入所有失败候选，限定每次实验 Store 生命周期 |
| 命令可写 Workspace 外部；进程内存、网络效果、外部服务不在快照内 | 声明执行环境和一次性外部 fixture；文件系统回退不等于实验回退 |

公开 v1.0.0 [去重研究](https://layerfs.ai/benchmarks/dsh-dedup/) 在特定整文件重写负载下，
报告 157 个历史树约占 100 MB，Btrfs+zstd 约 1,995 MB；
[fio 研究](https://layerfs.ai/benchmarks/fio/) 同时报告顺序读写、随机写的退化。
这些是上游 `e5c7aaae05` 的测量，本次未复现，不能当作 1.1.0 结果，也不证明工程任务
成功率或 Agent 完整成本改善。

## 5. 拟议架构与 demo

### Owner 与放置

| Owner | 职责 |
| --- | --- |
| LoopX 现有 Explore / Todo / quota owner | 假设与 finding 谱系、claim/lease、工作准入、结果采用、剩余预算和 replan |
| Host/runtime | 原生会话、模型/工具路由、取消、已结算 capture 边界与执行隔离 |
| 可选 LayerFS adapter | 候选关联精确 Store/project/Commit/root 与 image 身份；fork、mount、inspect、commit、返回证据 |
| 现有 benchmark toolkit / 任务 evaluator | 冻结工作负载、独立 oracle、完整成本收集和比较分析 |
| 现有 Git/review 流程 | 导出选定源码补丁，在干净 checkout 验证并提交评审 |

放置理由：`capability_id: explore`，当前内置 provider 为 `loopx-core`；LayerFS 是
候选的可选 provider 实现，初期放在 benchmark 实验工作区。只有真实调用方完成验证后，
才按 [extension 放置规则](../../reference/extensions.md#placement-decision-for-agents)
讨论产品 distribution。不要仅为此设计注册 provider id、新共享 enum 或 package。
新的 provider-neutral 决策继续归现有 TypeScript owner，专用 capture 留在 adapter。

保留两张图：Explore 图解释问题和发现；LayerFS 图描述文件系统祖先关系。
二者关联而非一一对应。多个工具 Commit 可实现一次实验，同一个文件 checkpoint
也可支持多个 finding。LayerFS Commit 不等于 Git commit，也不等于已验收的 LoopX 成果。

实验 manifest 将现有 task/experiment/result id 关联 provider 源码版本、Store/project
身份、父/结果 Commit/root、runtime image digest、环境 manifest、evaluator 版本与
产物 digest。可变 branch 名仅作为展示句柄，不能独自充当证据身份。
原始 manifest/trace 留在模型 packet 外；使用现有 Explore 字段挂有界摘要和 opaque
evidence ref。这是研究局部记录，不是悄悄新增的共享运行时 schema。

### 一条完整的小 demo

第一项**候选 fixture**使用公开 [LoopX #6869](https://github.com/loopx-project/loopx/issues/6869)，
从源码 `907ede6e1faff1fab0f2f765dde4358c69c7b7ff` 开始：不可读 spend artifact
可能静默降低已计入的消耗。源码确认了 missing-event 路径；第一个里程碑仍须通过真实
CLI 复现才可纳入 fixture。[PR #6875](https://github.com/loopx-project/loopx/pull/6875)
是已有修复工作，不是已验收 oracle，也不是本提案要重复交付的工作。
这是隔离的历史修复练习，不代表认领该 issue。

1. 准备固定 base 的独立 clone，含锁定依赖、已构建 TypeScript runtime、合成的
   File/SQLite 任务数据。solver 修改的源码仓库与已安装 LoopX controller 及其 runtime
   状态分离。导入前移除凭据和机器本地资料；Git worktree 的 `.git` 指针不是可移植完整仓库。
2. 三个 worker 获得相同任务和声明的工具/反馈权限，自主提出、记录假设，分享发现，
   拒绝或继续路线。不脚本化结论，也不强迫某个分支获胜。
3. 从精确预热 checkpoint 建立分支。先用确定性 conformance 执行竞争编辑、负结果和
   receiver handoff；另行运行模型实验，判断这些机制是否被实际使用。
4. 每个 probe 保存代码/环境身份、测试结果和成本。独立 evaluator 验证 missing、
   malformed、truncated spend 证据不会静默增加权限；有效 spend/void、history、
   admission 保持一致，并覆盖时间窗口边界。不能用永久全量 throttling 或某份补丁
   预期输出代替正确性 oracle。
5. 接手者从选定不可变候选 fork，验证 manifest、读取相关 finding，执行下一项测试
   或修复。最终仅导出选定源码补丁到新 checkout，以干净依赖重跑 evaluator。
   只有分支或缓存中的通过结果不够。
6. 展示补丁、失败路线、接手者的实际工作、held-out 结果与完整成本分解。
   录制 UI 回放须标注；脚本 conformance 不算自主模型采用。

这个公开历史任务可能被模型预先见过，只能验证 demo 路径。效果研究还须加入此前未见、
独立准备的任务，至少覆盖 TypeScript monorepo 与 Rust workspace，包括依赖/生成代码
变更，以及低分叉负对照。Fixture 保留真实 package graph、lockfile、symlink、
ignored dependencies、构建产物和代表性错误日志。人为注入故障须标明，不能用重复
字符串或空文件代替这些状态形态。

### 执行与失败转换

首个 Host 优先调查两个现有 DSH plugin 在一次性 profile 中共存；固定 DSH、双方
plugin 和 LayerFS 的组合版本。共存、工具路由、session 继承目前**未经验证**。
不能因 DSH 可用就静默改变研究所选 Host。

每个文件操作前，由现有 Host 准入工作、adapter 绑定候选。直接文件调用经 SDK，
shell 经 mount → exec → 显式 commit/readback → clean unmount。
非零退出作为实验证据，不能当修复成功；不要意外丢弃有价值的失败测试状态。
决定 discard 时显式记录。同一 Workspace 的操作保持串行，worker 各有独立 Workspace。

冲突后保留原输入，检查全部相关路径，包括分页或截断的诊断。在新的工作 fork 中
解决并重新评测。Merge 前冻结输入 head；原分支的测试通过不覆盖新合并 root。
回复丢失时，先对照精确 history 与 Workspace status 再执行新操作；无法确认成功就
保持 unknown、停止该分支，不能盲目重放命令或 force commit。
清理也不能把未决操作改记为成功。

仅文件系统分叉时，两组都以相同声明上下文开启新会话。后续如研究同对话接续，
必须满足 Stride §7.7 的 host cursor、证据、产物与机会 barrier。
LayerFS 的 provider 契约通过验证后，只能替换 artifact participant；不能使当前
file-only capture 自动接受 symlink，也不能通过复制文件恢复 lease、预算、
evaluator 次数或外部效果。

## 6. 替代方案与比较

| 方案 | 保留它的理由 |
| --- | --- |
| 预热 Git worktree + 锁定依赖缓存 | 主要实用对照；允许合理缓存，其 checkpoint 路径也须保存必要未跟踪产物 |
| 合适 Linux Host 上的 Btrfs/reflink 或 overlay snapshot | 完整环境低成本分叉的强替代；可用时比较，不可用时披露覆盖缺口 |
| LayerFS workspace provider | 测完整树共享历史、增量 capture、分支交接，也计入 FUSE 与发布成本 |
| 普通常驻单 worker checkout | 分叉很少时的负对照，同时判断并行协调本身是否有益 |

不能只用已预热 LayerFS 对比每次冷下载依赖的对照。分别测冷启动和多次热复用，要求
可恢复状态与保留历史等价。依赖缓存只读共享或按分支隔离。原生 snapshot 仍可能更优。

每次 attempt 在环境准备前启动 wall 时钟，计入终局验证/导出。
Provider 的一次性 build/image 准备单列，并计入已声明摊销口径的总成本；
不能隐去，也不能只计给对照。稳态视图补充冷启动总量，而不替代它。

## 7. 安全、隐私与兼容性

使用同一 Linux kernel 内的一次性公开 fixture，固定 CPU/RAM/磁盘上限，单独控制网络。
不要导入开发者正在使用的目录：LayerFS 会有意保存 ignored files。
凭据、模型历史、真实 LoopX state、evaluator 内部资料、daemon endpoint 与 Docker
访问权限放在 Agent 可写树之外。首个研究使用固定空 `.lww`，源码、lockfile、测试不能
自动 newer-wins；改变策略即改变 treatment。

检查可执行 mode、symlink、hardlink、绝对路径、native dependency ABI、写 root 外的
package。导入环境必须匹配 guest OS/架构/image；arm64 macOS 依赖树不是 Linux 环境。
LayerFS 隔离本身不证明租户安全。

Feature-off 保持已有执行与状态。Adapter 失败要可见；fallback 是单独标记的新 attempt，
不能静默换 arm。模型提示词不能替代对真实工具读写位置的验证。
LoopX 控制调用留在其 owning Host，不放进正在修复的候选树。

## 8. 迁移与回退

不迁移已有 Store 或 Goal。从独立保存的 fixture 初始化新研究 Store。
选定源码补丁和验证证据存放在外部；宣告成功前验证 clean-checkout replay。
停止新准入、核对在途操作、导出合格结果、关闭本实验的 Workspace/Sandbox，再移除
研究 Store。不要把 Workspace discard 描述为历史 GC。
关闭可选 adapter 即恢复原工作区路径。源码格式升级开启新的研究 generation，
不原地改造历史证据。

## 9. 评测与验收

### 区分底层机制收益与 Agent 收益

首先在两种 workspace provider 上重放同一预定文件/构建/测试序列，在资源允许时
覆盖 1/3/8 个 worker 与 1/4/16 个分支，运行前记录排除的单元。
保持拓扑、保留状态和命令语义一致；它只估计底层开销。

随后使用以下矩阵，固定 Host/model/reasoning、任务划分、测试可见范围、总资源预算
和最多三个 worker：

| 控制策略 | 预热 worktree（W） | LayerFS（F） |
| --- | --- | --- |
| Native Host（N） | N-W | N-F |
| Passive LoopX（P） | P-W | P-F |
| Governed LoopX（G） | G-W | G-F |

第一轮小规模模型 pilot 可只运行 **G-W vs G-F**，只能估计该 LoopX 策略下的 workspace
效果，不能估计 LoopX 收益或联合协同。完整矩阵区分观察开销、治理与 workspace 作用。
协同声明需要在共同 outcome 标尺上预先声明 difference-of-differences，并给出不确定性；
仅 G-F 获胜不足以证明协同。

模型评测前冻结 task id/base commit、mutation generator/evaluator hash、模型版本、
SDK/image/plugin pin、branch/depth 上限、反馈时序、美元/token/compute/wall 上限与
缺失数据规则。协调者与所有 worker 共用**一个总预算**。Agent 研究允许自主改变
分叉策略，但不能突破该预算；不能给某 provider 额外时间、worker 槽位或 token
补偿其开销。

使用配对任务/seed、交错 arm 顺序，按现有研究契约，每个拟推广比较单元至少五次重复；
五次不是统计功效依据。任务选择和 sequential stop 规则须先于比较结果确定。
所有 solver 可访问文件和网络均不得泄漏 held-out task/evaluation。
开发反馈、计划中的评测和终局评分作为独立机会处理。
准备失败、取消、timeout、treatment 未交付、无效评测均保留在分母并单独分类。
广泛结论之前冻结功效/精度计划。

### 度量

主要度量是固定总预算的独立验收通过率，以及随时间变化的最佳有效保留成果。
同等成果再比较时间和完整成本，计入所有 attempt。
根据实测冷准备与边际复用成本估算盈亏平衡分支数，不能只看 fork latency。
解释性指标包括：

- 初始化、依赖获取、mount/fork、工具执行、capture/commit、冲突解决、handoff、
  验证、导出、清理的 wall/CPU 时间；
- 未缓存/缓存输入与输出 token、真实模型账单、控制与 evaluator 工作、人工介入次数
  及已测注意力时间；
- 物理分配 Store 与 WAL、overlay、container scratch、cache、独立导出和峰值
  disk/RSS，另列 logical history bytes；
- 竞争、拒绝/未知操作、恢复工作、重复 probe，以及证据→决策→receiver 实际工作，
  不奖励分支计数。

LayerFS trace/backend counter 只作诊断，同时使用独立 runner 时钟、OS 资源测量与
provider usage。Trace 写入可能丢失，缺记录不是零成本。
遵循现有度量契约：packet byte 不是模型 token，去重比不是总磁盘节省；账单/注意力
未知就不能声明完整成本。重叠 wall 区间不能相加当 elapsed duration，同时报告总
worker compute time。

### 验收行

| 声明 | 所需证据 | 通过边界 |
| --- | --- | --- |
| A1：真实环境分叉保真 | fresh remount 后独立树/内容/mode/link 检查、相同测试，覆盖 ignored output 和 symlink 密集 package tree | 每个选定 checkpoint 的声明状态一致；显式归一化元数据差异，不整体忽略 |
| A2：隔离与冲突完整性 | 并行独立编辑、同文件冲突、merge root 重测、外部路径 sentinel、no-op/read-only 调用 | 无候选污染、静默覆盖；未变状态不伪造 Commit |
| A3：如实处理失败/恢复 | 非零工具退出、回复丢失、Workspace 中断、worker 缺失、磁盘上限、证据缺失 | Unknown 保持 unknown；无重复执行、假成功或自动权限恢复 |
| A4：有效交接与产物 | Receiver 验证候选、采用 scoped finding、执行新工作；导出补丁通过独立 clean-checkout 评测 | 证明完整脚本路径；模型实际采用须单独运行验证 |
| A5：因果效率证据 | 冻结 matched cell、完整 attempt 统计、配对不确定性和原生结果 | 结论限于对比支持的范围；demo/microbenchmark 不能直接 promotion |
| A6：可用的可选旅程 | 打包 Host 可发现可用性、执行授权运行、展示 conflict/unavailable、读取结果和关闭 provider | 产品推广前必须满足；CLI-only 实验标为局部交付 |

确认性研究前，依据 pilot 方差和使用方成本，预注册主要对比、最小实用效果和
可接受成果损失界限。不能看完确认性结果再选这些值。
置信区间同时覆盖实质伤害与收益时，结论保持不确定。
正确性/隔离失败无论多快都不合格。收益仅在存储时，就只报告存储收益。

## 10. 运行与产品契约

复用现有 Goal/Explore research history 与 DSH conversation 回读任务和结果。
LayerFS Work/History 可显示文件谱系，但不能替代保留候选的理由。
实验不需要新增顶层页面。产品 provider 仍须在现有设置/capability 中提供 readiness、
授权启动、unavailable/error 恢复，以及 App/CLI 和任何宣称支持的 Lark 入口的关闭/回读覆盖。

实验 runner 限制分支数、Store 总增长、并发 worker 与命令时长。
按成功和失败分支共同预测保留成本，只清理本研究资源。
展示 stale/unknown checkpoint 与不可用观察。不要每轮向模型灌入原始日志和不断增长的
历史；按不可变引用获取详情，度量实际消费上下文。本 RFC 不改变 runtime packet
或 compute quota。

## 11. 规范性交付计划

| 里程碑 | 进入条件 | 退出证据 | 回退 / owner |
| --- | --- | --- | --- |
| M0：冻结 demo | 本提案与公开候选 fixture | 真实复现 base 失败；评审独立 oracle；固定 Host/provider manifest 与明确有界预算 | 不改活跃状态；LoopX 实验 owner |
| M1：一条可靠集成路径 | M0；隔离 runtime；兼容 SDK/native/image | 真实 LayerFS、LoopX 回读、干净导出补丁通过 A1–A4 确定性 conformance；关闭路径 parity | 移除一次性 profile/provider；LoopX adapter owner，上游就 API 缺口提供意见 |
| M2：比较 demo 与 pilot | M1 | G-W/G-F 配对 pilot、失败路线、完整成本覆盖和如实效果/限制报告；较大研究先预注册 | 保留证据、不改默认；benchmark owner |
| M3：归因与可选采用决策 | 充分 pilot 证据与预声明研究 | Native/passive/governed 矩阵、held-out 任务分层、A5 与独立复现；产品声明前满足 A6 | 保持实验性质或拒绝 provider；现有维护者决策 |

一条 LoopX issue 承接上述序列；一条 LayerFS issue 请求 API/评测反馈。
复用上游 #411/#456 和 [#446 路线](https://github.com/Ephemeral-AI-Lab/layerfs/issues/446)，
不重复创建 GC、worker 或通用 worktree benchmark 任务。
可移植底层测量可贡献至 `layerfs-benchmark`；治理与任务结果实验由 LoopX 负责。
上游意见有价值，但不能阻塞通过现有接口可独立推进的 fixture/oracle 准备。

## 12. 待定事项

| 决策 / owner | 建议 | 所需证据 / 截止边界 |
| --- | --- | --- |
| Host profile / LoopX Host 维护者 | 优先 DSH 组合，设计保持 runtime-neutral | M1 前核验 plugin 版本、control-call 路由、worker 继承、取消与无双重 commit |
| Fixture 与预算 / 实验 owner | #6869 公开 demo，held-out TS/Rust 分层研究效果 | M0 退出前完成 base 复现、license/privacy 审阅、oracle、cost cap |
| Provider API / LayerFS 维护者反馈 | 同一精确 commit 的公开 1.1 时代 packages；不改 internal API 或持久性 | 宣称 provider 支持前确认 worker 配置、exact-root 比较、未知操作回读与清理范围 |
| Promotion / LoopX 现有维护者 | 重复收益和 A6 满足前保持可选 | M3 关闭前具备预声明实用效果阈值、功效/精度和真实旅程证据 |

## 附录 A：证据登记

检查日期为 2026-10-26。源码审阅不等于 benchmark 复现或集成实测。
本 RFC 未运行 LayerFS build、模型实验或 runtime 安装；本地 Docker 可用性检查仅用于
判断可行性。

| 证据 | 发现 / 限制 |
| --- | --- |
| [LayerFS 固定 README](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/README.md) | 发布源码/API 分层、Disposable 限制、有明确范围的 v1.0 测量 |
| [Agent SDK guide](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/api/how-to-use/agent.md) 与 [exec 实现](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/api/agent/sdk/src/workspace/exec.js) | 公开调用、显式 commit 生命周期、guest 设置、流式输出、有界读取/merge 诊断 |
| [Runtime guide](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/api/how-to-use/runtime.md) | Host 所有的初始化、macOS/Windows worker、资源清理；不证明 plugin 共存 |
| [Backend/trace guide](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/api/how-to-use/backend.md) | Root 比较限制、存储口径和可能丢失的诊断 trace |
| [Store 架构](https://github.com/Ephemeral-AI-Lab/layerfs/blob/ad78914e36b258b360daf13aec164b5655814689/core/docs/architecture/84-global-sqlite-store.md) | 单 kernel 边界、发布语义、无不可达历史 GC |
| [DSH 集成](https://github.com/Ephemeral-AI-Lab/dsh-layerfs/blob/baac1b92b97f0bd492002c285f6a13401b4f4c0b/README.md) | 现有 Bash/SDK 路由、Work/History UI；执行前需核对其 LayerFS pin 与提议源码 pin |
| [LoopX 历史 readback](https://github.com/loopx-project/loopx/blob/907ede6e1faff1fab0f2f765dde4358c69c7b7ff/loopx/control_plane/quota/ledger_readback.py) | Missing/unreadable event 不贡献消耗；真实入口复现和正确修复 oracle 仍属于 M0 |

网站广义共享存储描述不能替代受支持的单 kernel Store 契约。
旧概念页和 1.1 之前的 SDK 名称不作为本研究实现指导。
Open issue 报告用于识别风险，不代表本次独立复现的缺陷或承诺修复。
