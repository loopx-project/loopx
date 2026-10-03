# 生态采用与衍生清单

> [English](ecosystem-adoption.md)

本页由维护者根据公开证据整理，记录其他项目对 LoopX 的使用、集成、研究与再实现。
具体使用见[工作流与集成](#1-工作流与集成)；尚未成为运行时采用的工作见
[提案与暂缓采用](#3-提案与暂缓采用)。

> **证据边界**：收录是事实记录，不代表背书或生产部署声明。PR 合并只证明代码或
> 文档已落地；公开发行、限定范围的试点、开放提案各自证明不同的事情。链接中的
> 验证结果是其作者的报告，不代表本清单独立复现了这些结果。

[`ADOPTERS.md`](../../ADOPTERS.md) 是项目和用户主动描述自身使用情况的独立自愿目录。
登记表为空不等于没有观察到的使用：下列公开证据不以项目所有者提交登记行为前提。

## 1. 工作流与集成

- **CGC 2046**（CodingGirlsClub）——已合并的
  [PR #838](https://github.com/CodingGirlsClub/cgc_2046/pull/838) 记录改用
  LoopX 与 Codex CLI 组织开发；已合并的
  [PR #841](https://github.com/CodingGirlsClub/cgc_2046/pull/841) 增加满足条件后
  合入 `develop` 的流程，指定敏感变更、`main` 与发布仍由人控制。
  **状态：开发工作流配置已合并**；不代表应用生产运行时依赖 LoopX。
  核对日期：2026-09-30。
- **NoKV**——已合并的 [PR #518](https://github.com/NoKV-Lab/NoKV/pull/518)
  增加用于 LoopX NoKV authority 资格验证的单节点测试环境。作者报告真实一致性
  检查通过；工具明确不证明生产可用性、故障转移或多 owner 运行。
  独立的 metadata-runtime 升级验收门禁
  [issue #511](https://github.com/NoKV-Lab/NoKV/issues/511) 仍开放。
  **状态：资格验证工具已合并，独立验收尚未关闭**。这比下文 README 中的合作声明
  多了具体集成产物，不代表 NoKV 在生产中运行 LoopX。核对日期：2026-09-30。
- **GoTry**（Danceiny）——用 LoopX goals、Codex 任务绑定和心跳管理多条开发路线。
  [issue #18](https://github.com/Danceiny/gotry/issues/18) 记录接入，已合并的
  [PR #187](https://github.com/Danceiny/gotry/pull/187) 记录交付验证。
  **状态：项目自述的开发工作流使用**；不代表旅行应用运行时依赖 LoopX。
- **mimofan**（XiaomingX）——通过 LoopX Todo 组织 UI/引擎修复。
  [PR #738](https://github.com/XiaomingX/mimofan/pull/738) 明确提及该工作流并已合并。
  **状态：开发工作流证据**。
- **Meta-RLR**（hk20013106）——已合并的
  [PR #17](https://github.com/hk20013106/RLR/pull/17) 增加 CLI/JSON 维护边界。
  LoopX 管理维护目标、待办、证据、监视和重规划，科研状态仍由 `research_loop` 所有。
  **状态：维护集成已合并**；后续自动唤醒
  [PR #26](https://github.com/hk20013106/RLR/pull/26) 已关闭、未合并。
- **LoopX Console**（xielixing）——第三方 BitFun MiniApp，通过本机 LoopX CLI、
  `quota should-run` 和宿主 Agent 修复 GitHub Issue。
  [源码与安装说明](https://github.com/xielixing/loopx-console)及
  [发行版本](https://github.com/xielixing/loopx-console/releases)已公开。
  **状态：独立发布**；是否进入 OpenBitFun 上游是下文的另一项提案。
- **zyra**（BingruL）——[打包配置](https://github.com/BingruL/zyra/blob/fix/execution-timeouts-and-diagnostics/pyproject.toml)
  内嵌 LoopX runtime 并注册 CLI 入口。
  **状态：已观察到源码与打包集成**；未验证部署和持续运行。
- **Hufu**（Blicae8917）——已合并的
  [PR #70](https://github.com/Blicae8917/hufu/pull/70) 增加显式启用的 LoopX v0.5.2
  RunOnce Consumer。真实 transport 和 Host 调用由部署 Provider 提供；状态投影
  [issue #76](https://github.com/Blicae8917/hufu/issues/76) 仍开放。
  **状态：限定范围的集成已合并，配套工作尚未完成**。
- **benjamin-plugins**（Yidada）——已合并的
  [PR #1](https://github.com/Yidada/benjamin-plugins/pull/1) 增加调用官方 LoopX 内核的
  Codex 插件。作者报告源码 checkout 上的 CLI 契约 smoke 通过；PyPI 安装未验证，
  后台调度仍归宿主。**状态：插件已合并**。
- **Adaptive-Agent-Orchestration-Protocol**（YuemingHub）——已合并的
  [PR #41](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/pull/41)
  将 LoopX 注册为可选执行连续性 Provider。
  [issue #42 的试点报告](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/issues/42#issuecomment-5249833699)
  记录了限定范围的 Linux CLI 恢复与门禁测试；
  [后续回读](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/issues/42#issuecomment-5287847666)
  未证明持续采用。**状态：协议集成与非生产试点**，不代表默认生产采用。

## 2. 机制借鉴

下列项目明确引用 LoopX 的思路。原生实现和已接受的设计文档，与依赖 LoopX 运行时
是不同的关系。

- **surogates**（invergent-ai）——[比较与采用计划](https://github.com/invergent-ai/surogates/blob/master/docs/superpowers/plans/2026-08-03-loopx-adoption.md)
  选择吸收持久授权、目标预算和评估器记忆，保留自身存储及运行时。
  [PR #188](https://github.com/invergent-ai/surogates/pull/188)、
  [#190](https://github.com/invergent-ai/surogates/pull/190)、
  [#191](https://github.com/invergent-ai/surogates/pull/191) 已合并。
  **状态：代码层借鉴**；实时 PR 状态优先于计划中的旧表格。
- **future-os**（futuregene）——已合并的
  [PR #253](https://github.com/futuregene/future-os/pull/253) 和
  [#255](https://github.com/futuregene/future-os/pull/255) 明确参考 LoopX，
  用 Rust 实现部分多 Agent 与目标推进机制。**状态：原生再实现**。
- **gptme-contrib**——已合并的
  [PR #1373](https://github.com/gptme/gptme-contrib/pull/1373) 将公开/私有证据脱敏思路
  归因于 LoopX 调研。**状态：代码层借鉴**。
- **KiroCrew**——已合并的
  [PR #3229](https://github.com/kirodotdev/KiroCrew/pull/3229) 在持续 Agent RFC 中采用
  持久 typed gate 和写回后扣预算，同时明确保留不同的唤醒与工作选择机制。
  **状态：设计采用，仅文档**。
- **multica**（LRM-Teams）——已合并的
  [PR #2174](https://github.com/LRM-Teams/multica/pull/2174) 记录五项受 LoopX 启发的
  协作原则。**状态：仅文档**，没有 LoopX 运行时变更。

## 3. 提案与暂缓采用

- **MilkSU**——[issue #189](https://github.com/MilkSU-Official/milksu/issues/189)
  评估接入 LoopX、借鉴持久 Goal/状态/重试机制，或保留现有 ACP 方案。
  **状态：开放设计评估**，尚非已接受或已实现的集成。核对于 2026 年 10 月 3 日。
- **ai-skills**——[issue #468](https://github.com/MichaelHeaton/ai-skills/issues/468)
  提议借鉴 LoopX 的 quota 门控、冷却与自唤醒检查，不引入 LoopX 运行时依赖。
  [9 月 23 日后续评论](https://github.com/MichaelHeaton/ai-skills/issues/468#issuecomment-5796952500)
  因 `/loop` 是内置命令，将 hook 或上游支持留作后续设计。
  **状态：历史开放提案**，不是已交付的 Skill 或运行时采用。
  核对于 2026 年 10 月 3 日。
- **OpenViking / VikingBot**——开放的
  [PR #5223](https://github.com/volcengine/OpenViking/pull/5223) 提议增加可选、
  默认关闭的 LoopX 后台长任务。LoopX 管 Goal/Todo 状态与执行门禁，Bot 提供
  worker、模型与工具。作者报告以模型替身完成真实 CLI 测试；真实模型端到端
  验收和 Docker 构建仍未完成。**状态：运行时集成提案，尚未合并或发布**。
  核对日期：2026-09-30。
- **Opensiro VSM harness index**——已合并的
  [PR #607](https://github.com/opensiro/vsm-harness-index/pull/607) 记录固定
  LoopX 对比实验的可辨识性阻塞；后续
  [registered-peer harness](https://github.com/opensiro/vsm-harness-index/pull/614)
  仅完成实验执行框架，未运行真实模型。已合并的
  [PR #618](https://github.com/opensiro/vsm-harness-index/pull/618) 随后按仅使用
  公开证据的研究政策停止该自行执行的实验。
  **状态：保留历史研究产物，该研究未进行且不再计划实跑**；不是新增 benchmark
  结果或运行时采用。核对日期：2026-09-30。
- **OpenBitFun**（GCWing）——内置控制台
  [PR #2836](https://github.com/GCWing/OpenBitFun/pull/2836) 仍开放，替代已关闭未合并的
  #2382。维护者[表示优先保障 beta 稳定性，之后再评估大特性](https://github.com/GCWing/OpenBitFun/pull/2836#issuecomment-5613986161)。
  **状态：上游集成提案**，与已独立发布的第三方 LoopX Console 分开记录。
- **codexia**——上游 [PR #71](https://github.com/milisp/codexia/pull/71) 已关闭未合并，
  作者解释为误投仓库；下游
  [PR #1](https://github.com/connorodea/codexia-task-management/pull/1) 仍开放。
  **状态：下游提案**；作者明确说明 CLI 契约尚未对真实安装验证。
- **spoon-core**——[issue #285](https://github.com/XSpoonAi/spoon-core/issues/285)
  提议在模型调用前提供可选、只读的 LoopX 控制上下文。**状态：开放提案**。
- **GENesis-AGI**——[issue #2123](https://github.com/WingedGuardian/GENesis-AGI/issues/2123)
  提议在自建持久目标后端前评估 LoopX。**状态：开放评估请求**。
- **Orca**——[issue #12628](https://github.com/stablyai/orca/issues/12628)
  请求类似 LoopX 的目标驱动迭代。**状态：开放用户诉求**，不代表维护者接受或已实现。
- **OpenAgentEmail**——[discussion #180](https://github.com/openagentemail/openagentemail/discussions/180)
  讨论可选控制面兼容性；
  [9 月 12 日后续评论](https://github.com/openagentemail/openagentemail/discussions/180#discussioncomment-18407019)
  建议先做小型、可回退的 Provider 试点，而非进入关键路径。该评论明确注明由 AI 撰写。
  **状态：讨论与试点提议**。
- **Quesen**——[discussion #3735](https://github.com/huangruiteng/loopx/discussions/3735)
  后形成 [prepared-Effect 风险准入方案](https://github.com/Shxnque/quesen/blob/main/docs/integrations/loopx-prepared-effect-packet.md)，
  明确先做 shadow mode，保留人的授权权威。**状态：集成方案，尚非代码提案**。
- **8x8-user-edition**——已合并的
  [PR #63](https://github.com/8x8org/8x8-user-edition/pull/63) 因现有权威与状态系统重叠，
  暂缓运行时采用，只选择部分协议思路。**状态：暂缓运行时采用**。
- **Mindthus**——对比吸收
  [issue #132](https://github.com/rv198-star/Mindthus/issues/132) 已关闭。
  **状态：已有评估记录**；Issue 关闭本身不证明运行时采用。
- **GovernLoop**——Phase 0 能力映射
  [PR #23](https://github.com/liangzhipengdamon-maker/GovernLoop/pull/23) 已关闭未合并。
  **状态：历史评估提案**。
- **hartevo-desktop**——[issue #55](https://github.com/tangpingqingwa/hartevo-desktop/issues/55)
  提议借鉴 Prime Agent 和 LoopX 构建 Mission Control 内核。**状态：开放设计议题**。
- **polyphemus**——[issue #89](https://github.com/Diekgbbtt/polyphemus/issues/89)
  研究 LoopX 基础机制。**状态：开放研究请求**。

## 4. 学习、传播与合作

- **General Loop**——[固定版本的 README 对比](https://github.com/CosmosShadow/general-loop/blob/0bd8fb3b5b5c2e8e02b35e627ad715cade23bba0/README.md)
  将 LoopX 控制面协议与现有 Agent 宿主上的 Markdown 协调方式并列。
  **状态：比较引用**；提及本身不证明 LoopX 依赖或机制采用。
  核对于 2026 年 10 月 3 日。
- **GitHub-Michelin**——已合并的
  [PR #128](https://github.com/ZhenningLang/GitHub-Michelin/pull/128) 增加双语
  [LoopX 编辑条目](https://github.com/ZhenningLang/GitHub-Michelin/blob/a75520b79cff3d9818ab6b60100677b93beaef81/categories/agent-tooling/work-state/loopx.md)。
  **状态：编辑内容收录**，不是运行时采用。条目中 9 月 27 日的评述和快照属于
  编辑当时的观察，不是对 LoopX 当前行为的实时资格验证。
  核对于 2026 年 10 月 3 日。
- **《深入理解 AI Agent》/ ai-agent-book**（bojieli）——已合并的
  [PR #614](https://github.com/bojieli/ai-agent-book/pull/614) 将 LoopX 引入为具体的
  Loop Engineering 框架；[第 10 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md)
  引用固定版本并保留实验性证据边界。**状态：教学材料**，不是读者采用统计。
- **NAVER fe-news**——[2026 年 9 月通讯](https://github.com/naver/fe-news/blob/master/issues/2026-09.md)
  用韩文介绍 LoopX 及安装路径。**状态：编辑内容收录**，不代表 NAVER 部署声明。
- **OpenViking / NoKV**——[OpenViking README](https://github.com/volcengine/OpenViking/blob/main/README.md)
  列出 LoopX；[NoKV README](https://github.com/NoKV-Lab/NoKV/blob/main/README.md)
  将其列为活跃开源合作。**状态：公开项目关系**；这些条目本身不证明运行时依赖。
- **loopx-book / loopx-book-labs**（cocolord）——双语、协议优先的
  [开发者书](https://github.com/cocolord/loopx-book)与
  [可运行实验](https://github.com/cocolord/loopx-book-labs)，覆盖项目接入、Issue 到 PR
  和独立扩展。**状态：教学资源**。

## 5. 衍生实现

- **michaelx1993/loopx → foolzzz/loopx**——同一相关 fork 家族，不计为两个独立采用者。
  [下游 Changelog](https://github.com/foolzzz/loopx/blob/6c16e4a06797f2b0a81a53827e7fd8082913b36d/CHANGELOG.md)
  记录了与上游分叉及角色式编排。已合并的
  [michaelx1993 PR #27](https://github.com/michaelx1993/loopx/pull/27)
  引入确定性的记账与有界编排摘要；已合并的
  [foolzzz PR #18](https://github.com/foolzzz/loopx/pull/18) 为该 fork 准备 2.0.0 版本。
  **状态：下游代码已合并**；这些是 fork 的变更，不是上游 LoopX 发布或独立验证的
  性能增益。核对于 2026 年 10 月 3 日。
- **loopx-HPC**（Sande33p）——独立 fork 内的
  [PR #1](https://github.com/Sande33p/loopx-HPC/pull/1) 已合并，增加可选 scientific
  campaign、PBS/Slurm 和 MLflow 集成。**状态：下游代码已合并**；作者明确说明
  真实 HPC 验收尚未完成。
- **foreman**（needware）——[PR #1](https://github.com/needware/foreman/pull/1)
  提议将固定上游提交的 LoopX 内核迁移到原生 TypeScript。
  **状态：开放提案**，不是已合并的运行时迁移。

## 证据限制

- 项目自身的开发工作流、打包集成、协议借鉴和内容收录是不同关系，不应相加为
  生产采用者数量。
- Stars、未修改的 fork、自动 Trending/digest 转载以及无关同名项目不是采用证据。
- 创作者自用与用户归因案例在[Showcase 清单](../showcases/README.md)保留各自来源边界。
  例如 [MFS 重构案例](../showcases/cases/independent-public-engine-refactor.md) 明确区分
  公开合并的 PR 与用户自述的 LoopX 归因。

## 维护方式

- 每次复核后通过 PR 同步更新中英文，检查正文、当前 PR 合并状态、替代链接与后续
  评论。记录核对日期，不把旧标签当作当前证据。
- 发现入口包括 `gh search code "huangruiteng/loopx"`、`gh search issues loopx`、
  `gh search prs loopx` 和 `gh search repos loopx`；只审阅公开证据，排除重复及无关匹配。
- 观察条目集中维护在本页；项目可在 [`ADOPTERS.md`](../../ADOPTERS.md) 自愿确认自身使用。
  不根据本清单代替项目提交自报条目。
- 最近复核：**2026-09-19**。公开来源调研于 9 月 18 日完成；链接中的 PR/Issue 状态于
  9 月 19 日刷新。
- 局部更新：**2026-09-30**，覆盖上述 CGC 2046、NoKV 资格验证工具、VikingBot
  提案及已停止的 Opensiro 研究。其余条目沿用此前的核对边界，不代表全表重新验真。
- 局部更新：**2026-10-03**，覆盖 MilkSU、ai-skills、General Loop、GitHub-Michelin
  及 michaelx1993/foolzzz fork 家族。核对了当前 Issue/PR 状态、后续评论和固定版本
  的源文件；本轮未新增可确认的运行时采用者，也不代表其余条目重新验真。
