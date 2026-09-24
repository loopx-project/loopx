# Welcome Wagon：30 分钟从读者到参与者

<!-- welcome-wagon:small-first-outcome -->

> **先完成一件真实的小事。** 你不需要读完 Dev Book、理解全部 CLI，或先修改
> Kernel。选择下面一条路线，留下一个可验证结果，再决定是否继续深入。

中文与英文页面是语义镜像；章节、行动、命令、链接目标或边界出现实质差异都属于文档缺陷。

<!-- welcome-wagon:four-routes -->

<div class="grid cards" markdown>

-   :material-play-circle-outline: **跑通一次**

    在自己的项目里完成安装、连接和第一次状态回读。

    **约 15 分钟 · 产物：可恢复的 Goal**

-   :material-message-text-outline: **反馈一次**

    提交脱敏的 first-run、问题或长期使用记录。

    **约 10 分钟 · 产物：可行动的公开反馈**

-   :material-source-pull: **贡献一次**

    认领一个有边界的任务，完成最小改动与验证。

    **约 45–60 分钟 · 产物：可审阅的 PR**

-   :material-comment-check-outline: **评审一次**

    用证据评论一个 RFC、Issue 或 PR，不急着写实现。

    **约 30 分钟 · 产物：一条推进决策的 review**

</div>

## 先选你的完成线 {#choose-finish-line}

| 你现在想做什么 | 完成标准 | 从哪里开始 |
| --- | --- | --- |
| 判断 LoopX 是否适合自己的项目 | `doctor` 通过，项目状态可读，本地状态未进入 Git | [路线 A](#run-once) |
| 告诉维护者哪里好用或卡住 | 留下最小、公开安全、可复现的反馈 | [路线 B](#share-feedback) |
| 提交第一份代码或文档贡献 | 任务已认领，改动有界，验证与 DCO 完整 | [路线 C](#first-contribution) |
| 参与方向和架构讨论 | 区分已发布事实与提案，并指出证据、风险或最小切片 | [路线 D](#review-path) |

四条路线互不依赖。使用者不必成为贡献者；贡献也不只等于修改 Kernel。复现问题、改善文档、
补充 deterministic fixture、回答社区问题和评审设计都属于有效参与。

## 共同起点：只读确认环境 {#inspect-first}

<!-- welcome-wagon:inspect-before-write -->

先在目标项目根目录运行：

```bash
loopx --version
node --version
loopx doctor
git status --short --branch
```

此源码版本要求 Python 3.11+ 与 Node.js 22.22.3+。如果 `doctor` 失败，先按
[安装指南](/loopx/docs/guides/installing-loopx/)修复安装，不要在错误环境里继续写项目状态。

<!-- welcome-wagon:public-private-boundary -->

在任何公开反馈或贡献中，都不要粘贴凭据、私有项目名、内部链接、本机绝对路径、raw transcript、
`.loopx/`、`.codex/goals/` 或未脱敏日志。

## A. 跑通一次 LoopX {#run-once}

<!-- welcome-wagon:run-first-goal -->

### 1. 安装并检查

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

安装后重启 Agent Host，让它重新加载 workflow skill。需要验证 TypeScript Effect runtime 时运行
`loopx doctor --deep`；该 runtime 由 LoopX 自动管理，不需要手工启动 daemon。

### 2. 连接一个你熟悉的项目

```bash
cd /path/to/your-project
loopx connect --dry-run
loopx connect
loopx status
```

如果项目尚无可继续的 Goal，优先让当前 Agent 执行：

```text
$loopx <一个跨会话、可验证、完成条件明确的任务>
```

Host 没有原生 `/loopx` 入口时，使用
[Newcomer Command Path](/loopx/docs/guides/newcomer-command-path/) 中的 guided route。

<!-- welcome-wagon:verify-first-goal -->

### 3. 验收，而不是只看退出码

第一次成功至少应满足：

- `loopx doctor` 能检查发布物、skill 与 Effect runtime；需要 Host-specific
  检查时，先用 `loopx agent-onboard --list-agent-types` 选择准确类型，再运行
  `loopx doctor --agent-type <agent-type>`；
- `loopx status` 能看到精确 Goal、当前 Gate 和下一项 Todo；
- `.loopx/`、`.codex/goals/` 与 `.local/` 没有进入 Git；
- 当前 Host 的 loop driver 已激活，或返回了明确的人工启动步骤；
- 遇到 Goal 选择、identity takeover、凭据或外部写入时，流程停在 Gate。

完整的 Agent 接入合同见[连接你的 Git 项目](chapters/05-connect-existing-project.md)。

## B. 反馈一次真实体验 {#share-feedback}

<!-- welcome-wagon:share-feedback -->

不需要先修代码。高质量反馈本身就是贡献。

```bash
loopx first-run-report
```

这个命令只生成本地环境摘要和预填 Issue 链接，**不会发送 telemetry**。提交前仍需人工检查并
删除任何不适合公开的信息。

<!-- welcome-wagon:route-community-channel -->

| 你的情况 | 使用入口 | 应包含 |
| --- | --- | --- |
| 第一次安装或连接 | [First-run feedback](https://github.com/huangruiteng/loopx/issues/new?template=first_run.yml) | 版本、OS、Host、完成步骤 |
| 运行了数小时或数天 | [Usage story](https://github.com/huangruiteng/loopx/issues/new?template=usage_story.yml) | 时长、使用能力、恢复方式、结果 |
| 可复现错误 | [Bug report](https://github.com/huangruiteng/loopx/issues/new?template=bug_report.yml) | 最小复现、期望、实际、脱敏诊断 |
| 使用或设计问题 | [GitHub Q&A](https://github.com/huangruiteng/loopx/discussions/categories/q-a) | 目标、当前版本、已尝试路径 |
| 可公开工作流或成果 | [Show and tell](https://github.com/huangruiteng/loopx/discussions/categories/show-and-tell) | 真实运行方式、证据与限制 |

如果只是想先与其他使用者交流，可以加入
[Discord](https://discord.gg/XmGgQyCFZd)。聊天适合探索；最终 bug、决定和可复现结论应回到
Issue、Discussion、PR 或版本化文档。

## C. 完成第一次贡献 {#first-contribution}

<!-- welcome-wagon:find-current-work -->

### 1. 从当前任务开始

依次阅读：

1. [Current Technical Directions](/loopx/docs/project/technical-directions/)：方向处于
   shipped、incubating、research、draft 还是 held；
2. [Contributor Task Board](/loopx/docs/development/contributor-tasks/)：选择 `Starter / Good First`
   或已有共识的 bounded task；
3. [CONTRIBUTING](https://github.com/huangruiteng/loopx/blob/main/CONTRIBUTING.md)：安装、DCO、
   public/private boundary 和验证要求。

任务板是动态事实源。本书不会复制“当前可认领任务”列表；如果任务没有关联 Issue，先用
[Contributor task 表单](https://github.com/huangruiteng/loopx/issues/new?template=contributor-task.yml)
建立公开协作边界。

<!-- welcome-wagon:claim-bounded-slice -->

### 2. 留下一条认领评论

一条有效认领应说明：

```text
我准备处理：
- 最小结果：
- 不做什么：
- 预计文件或 owner：
- 验证命令：
- 目标 base branch：
```

`Maintainer-owned` 任务不要重复实现；可以询问是否能拆出 fixture、文档、可访问性或
public-safe replay 等独立 helper slice。

<!-- welcome-wagon:deliver-clean-loop -->

### 3. 在干净分支完成一个闭环

```text
problem
  -> canonical owner
  -> invariant
  -> smallest coherent change
  -> focused validation
  -> signed commit
  -> pull request
```

第一次贡献优先考虑：

- 文档导航、术语和中英文一致性；
- 已有 smoke 的一个遗漏反例；
- public-safe synthetic fixture；
- CLI 错误信息或输出一致性；
- 已有 Capability/Host 的 narrow parity check。

不要把“容易改”误认为“可以随便改”。即使只有几行，也要说明改变了什么用户结果，以及什么
证据能证明它。

## D. 参与一次 RFC 或 Review {#review-path}

<!-- welcome-wagon:review-by-maturity -->

先从 [RFC Index](/loopx/docs/architecture/rfcs/) 判断材料状态：

| 状态 | 可以做什么 | 不应该做什么 |
| --- | --- | --- |
| Accepted | 核对实现、补负例、修兼容与文档 | 重新发明另一套 semantic owner |
| Active research | 改进实验设计、fixture、归因和边界 | 把实验结果直接写成默认产品能力 |
| Draft | 评论问题、权威、非目标、最小切片和验证 | 未形成 bounded task 就开始大规模实现 |
| Integration proposal | 做 parity、characterization 与 promotion evidence | 把 integration branch 当作 `main` 事实 |

第一次 review 可以只回答六个问题：

1. 它解决的是哪个用户或维护者问题？
2. 当前 canonical authority 在哪里？
3. 哪些是已发布事实，哪些只是提案？
4. 默认行为、权限或 public/private boundary 是否变化？
5. 最小可验证切片和最强反例是什么？
6. 失败或回滚后，哪个状态仍然可信？

跨方向问题可以进入
[Open Strategy Review](/loopx/docs/community/open-strategy-reviews/)。它形成 disposition、owner、
下一产物和复核 trigger，但不会用一次会议投票替代 RFC、Issue 或 PR。

## 卡住时怎么求助 {#ask-for-help}

<!-- welcome-wagon:ask-with-signal -->

提问时附上：

- `loopx --version`；
- Host/runtime surface；
- 你想完成的结果；
- 最小公开复现；
- expected 与 actual；
- 已运行的只读诊断及其脱敏摘要。

不要只贴一大段日志，也不要只说“不能用”。好的问题让其他人能复现、判断 owner，并给出下一条
可执行动作。渠道与响应边界见
[LoopX Support](https://github.com/huangruiteng/loopx/blob/main/.github/SUPPORT.md)。

## 你的下一站 {#next-stop}

<!-- welcome-wagon:choose-next-depth -->

- 想理解为什么要用控制面：读[从一次会话到长程任务](chapters/01-from-session-to-loop.md)；
- 想把自己的项目接进来：读[连接你的 Git 项目](chapters/05-connect-existing-project.md)；
- 想修改 LoopX：读[开发者贡献地图](chapters/source-protocol-map.md)；
- 想深入 Kernel：进入 [Control-Plane Developer Course](chapters/12-control-plane-course.md)。

完成一条路线以后再选择下一条。Welcome Wagon 的目标不是让新人一次理解所有东西，而是让第一
次真实行动足够安全、可验证，并且能被社区接住。
