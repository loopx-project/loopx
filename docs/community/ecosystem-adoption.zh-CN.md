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
  其中 seed routing helper [PR #4724](https://github.com/loopx-project/loopx/pull/4724)
  已合并，配套 ladder [PR #4726](https://github.com/loopx-project/loopx/pull/4726)
  仍开放；helper 合并本身不关闭该升级门禁。
  **状态：资格验证工具已合并，独立验收尚未关闭**。这比下文 README 中的合作声明
  多了具体集成产物，不代表 NoKV 在生产中运行 LoopX。NoKV 的
  [当前 README 证据边界](https://github.com/NoKV-Lab/NoKV/blob/b8d59c4ff30f2cdfcd8eb4a70cc6d3ec8ab9c420/README.md#evidence-and-qualification)
  另将完整原生 CLI 和已安装 Python wheel 对真实服务的验收标为尚未资格化。
  历史单节点测试环境的结果不证明这些当前、更广范围的验收成立。
  核对日期：2026-10-04。
- **GoTry**（Danceiny）——2026-08-28 创建的
  [issue #18](https://github.com/Danceiny/gotry/issues/18) 自述已注册 LoopX goals、绑定
  Codex 任务并完成首次心跳 ACK；[9 月 19 日后续评论](https://github.com/Danceiny/gotry/issues/18#issuecomment-5740371337)
  报告继续使用工程自动化及独立 worktree 交付。
  [PR #187](https://github.com/Danceiny/gotry/pull/187) 于 9 月 7 日合并，其本地检查自述
  与[精确 head 的成功 CI](https://github.com/Danceiny/gotry/actions/runs/34110550580)
  对应一项产品交付切片；正文仍列出未完成的真实库存 UAT。合并或 CI 通过不关闭该验收。
  [10 月 2 日检查点](https://github.com/Danceiny/gotry/issues/18#issuecomment-5950941798)
  继续保留真实用户证据门槛；复核时 [#20](https://github.com/Danceiny/gotry/issues/20)、
  [#22](https://github.com/Danceiny/gotry/issues/22)、
  [#142](https://github.com/Danceiny/gotry/issues/142) 均开放。
  **状态：有日期的作者自述开发工作流使用**；不证明旅行应用运行时依赖 LoopX、
  调度器已被独立复现或产品验收完成。核对日期：2026-10-05。
- **mimofan**（XiaomingX）——通过 LoopX Todo 组织 UI/引擎修复。
  [PR #738](https://github.com/XiaomingX/mimofan/pull/738) 明确提及该工作流并已合并。
  **状态：开发工作流证据**。
- **Meta-RLR**（hk20013106）——已合并的
  [PR #17](https://github.com/hk20013106/RLR/pull/17) 增加 CLI/JSON 维护边界。
  LoopX 管理维护目标、待办、证据、监视和重规划，科研状态仍由 `research_loop` 所有。
  **状态：维护集成已合并**；后续自动唤醒
  [PR #26](https://github.com/hk20013106/RLR/pull/26) 已关闭、未合并。
- **LoopX Console**（xielixing）——用于修复 GitHub Issue 的第三方 BitFun MiniApp。
  独立版本 [v3.9.29](https://github.com/xielixing/loopx-console/releases/tag/v3.9.29)
  于 2026-08-18 发布，附有 MiniApp ZIP。
  [核对的 main worker](https://github.com/xielixing/loopx-console/blob/dca7883ea5d4e2a0d48c0af5a1764c51a5023b53/source/worker.js)
  调用本机 LoopX CLI，以 `outer_controller` profile 执行 `quota should-run`；
  回退的源码安装固定为 LoopX **v0.2.13**。
  [同一快照的 README](https://github.com/xielixing/loopx-console/blob/dca7883ea5d4e2a0d48c0af5a1764c51a5023b53/README.md)
  将调度和执行归于 MiniApp/BitFun 宿主，并描述发布 PR 前需人工批准。
  **状态：独立发布，已观察到历史 CLI 集成**。发布元数据与当前源码是不同证据；
  ZIP、真实宿主执行、当前 LoopX 兼容和持续使用未独立验证。是否进入 OpenBitFun
  上游仍是下文的另一项提案。核对日期：2026-10-05。
- **zyra**（BingruL）——核对时默认分支为
  `fix/execution-timeouts-and-diagnostics`；此处固定到 2026-09-19 的
  [源码快照](https://github.com/BingruL/zyra/blob/3e20698e0182ec291cbdd87e7206fbae87706004/pyproject.toml)。打包配置包含内嵌 LoopX 模块和 CLI 入口；
  [源码清单](https://github.com/BingruL/zyra/blob/3e20698e0182ec291cbdd87e7206fbae87706004/packages/integrations/loopx_runtime/SOURCE-MANIFEST.json)
  固定 **LoopX v0.2.13**，所列源文件与上游该 tag 的源码树一致。
  [运行时解析器](https://github.com/BingruL/zyra/blob/3e20698e0182ec291cbdd87e7206fbae87706004/packages/integrations/zyra_integrations/loopx/runtime/resolver.py)
  要求使用内嵌源码或归属 Zyra 的已安装发行包，不回退到归档包。
  **状态：已观察到固定版本的源码与打包集成**。复核时未列出 GitHub release 或 tag，
  这不说明是否曾在其他渠道发布。wheel 安装、真实 Web/API 执行、当前 LoopX 兼容
  和持续使用均未独立验证。核对日期：2026-10-05。
- **Hufu**（Blicae8917）——已合并的
  [PR #70](https://github.com/Blicae8917/hufu/pull/70) 于 2026-08-23 增加显式启用的
  LoopX v0.5.2 RunOnce Consumer。核对的 main 快照已
  [导出 Consumer 及其 ports](https://github.com/Blicae8917/hufu/blob/51adc0918c6e904ce904f2be93a4d1a76fcef502/src/hufu/index.ts)；
  [兼容记录](https://github.com/Blicae8917/hufu/blob/51adc0918c6e904ce904f2be93a4d1a76fcef502/docs/COMPATIBILITY.md)
  保留 v0.5.2 精确基线，不内置 LoopX 依赖。真实 transport 和 Host 调用由部署
  Provider 提供。[issue #76](https://github.com/Blicae8917/hufu/issues/76) 报告了
  owner-local 试点的 RunOnce committed/replay 回执，但通用状态投影仍不完整，
  issue 仍开放。**状态：限定范围集成已合并、本地试点由作者报告、配套投影开放**。
  本清单未独立复现该试点，不据此声明当前版本兼容或持续部署。
  核对日期：2026-10-04。
- **benjamin-plugins**（Yidada）——已合并的
  [PR #1](https://github.com/Yidada/benjamin-plugins/pull/1) 于 2026-09-05 增加调用
  官方 LoopX 内核的 Codex 插件。核对的 main
  [marketplace 条目](https://github.com/Yidada/benjamin-plugins/blob/2baf35b4dcc64190e8012daf39054d83c46e6f22/.agents/plugins/marketplace.json)
  仍注册该插件；[来源记录](https://github.com/Yidada/benjamin-plugins/blob/2baf35b4dcc64190e8012daf39054d83c46e6f22/plugins/loopx/SOURCE.md)
  将 CLI 资格固定到 LoopX 0.5.4 源码 checkout。PR 报告一个独立模型 status 场景，
  其余场景仅通过结构校验。
  [preflight 实现](https://github.com/Yidada/benjamin-plugins/blob/2baf35b4dcc64190e8012daf39054d83c46e6f22/plugins/loopx/skills/loopx/scripts/preflight.py)
  只查找可执行文件和读取 registry 形状，不执行 LoopX，明确保留 runtime 和 driver
  未验证。**状态：插件已合并，源码 checkout 验证由作者报告**。PyPI 安装、真实后台
  执行和当前版本兼容未验证，调度仍归宿主。核对日期：2026-10-04。
- **Adaptive-Agent-Orchestration-Protocol**（YuemingHub）——已合并的
  [PR #41](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/pull/41)
  于 2026-08-11 将 LoopX 注册为可选执行连续性 Provider。
  [固定版本的试点报告](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/blob/baa3f7805cc391ada34feb50707a2c1d3c151b54/docs/LOOPX_PILOT_EVIDENCE.md)
  记录了针对 LoopX v0.4.3 的 Linux direct-CLI/custom-runner 测试：新进程恢复、
  验证与人工门禁、有界交接、记账及回滚。
  [消费者 Actions](https://github.com/YuemingHub/mingos-foundation/actions/runs/31465474613)
  在报告所记的消费者 head 上成功，但不构成对全部报告断言的独立复现。
  [8 月 14 日后续回读](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/issues/42#issuecomment-5287847666)
  明确保留 LoopX 未采用，未将试点提升为持续采用。AAOP 仓库现已归档；
  [退役记录](https://github.com/YuemingHub/Adaptive-Agent-Orchestration-Protocol/blob/baa3f7805cc391ada34feb50707a2c1d3c151b54/RETIREMENT.md)
  自 2026-09-25 生效，保留冻结的研究和历史发布，不承诺持续兼容或支持。
  **状态：已退役项目中的历史可选集成与有界试点**。当前 LoopX 兼容、Windows/WSL
  和生产宿主/会话重启仍未资格化；AAOP 退役不构成 LoopX 失败的实证。
  核对日期：2026-10-05。

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
  验收和 Docker 构建仍未完成。
  提案的[固定版本打包配置](https://github.com/volcengine/OpenViking/blob/406827d73594fda9def251912be64bf06b013773/pyproject.toml)
  将 `loopx==1.0.5` 放在可选 `longtask` extra 中；
  [当前 main 打包配置](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/pyproject.toml)
  未声明该 extra。这是提案与 main 的快照对照，不是安装未发布 extra 的指引。
  **状态：运行时集成提案，尚未合并或发布**。
  核对日期：2026-10-04。
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
  独立发布和作者报告的本地检查均不构成上游合并或发布。核对日期：2026-10-05。
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
- **OpenViking / NoKV**——[OpenViking README](https://github.com/volcengine/OpenViking/blob/9d9bc85e1f6a15afa7f23b0d7bf114a7c61cad14/README.md)
  列出 LoopX；[NoKV README](https://github.com/NoKV-Lab/NoKV/blob/b8d59c4ff30f2cdfcd8eb4a70cc6d3ec8ab9c420/README.md)
  将其列为活跃开源合作。**状态：公开项目关系**；这些条目本身不证明运行时依赖。
  核对日期：2026-10-04。
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
- 局部更新：**2026-10-04–05**，按当前公开 PR/Issue 状态和固定版本源文件复核
  OpenViking、NoKV、Hufu 和 benjamin-plugins；10 月 5 日对齐 AAOP 历史试点/退役、
  LoopX Console 独立发布与历史 CLI pin、zyra 的默认分支与内嵌源码 pin，
  GoTry 有日期的工作流自述及开放产品验收，以及单独的 OpenBitFun 提案。
  区分提案打包配置、历史工具和试点、插件注册及当前验收限制。
  未独立复现真实集成，其余条目未重新验真。
