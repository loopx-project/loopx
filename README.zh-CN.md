<div align="center">

<h1 align="center">LoopX</h1>

<img src="docs/assets/loopx-social-preview.png" alt="LoopX Loop Engineering 展示图" width="420">

**面向长程 Agent 的开放、有状态、Provider-neutral 控制面。**

<sub>在 Codex、Claude Code、Cursor 等 agent harness 之上，持久保存目标、gate、todo、证据、quota 与交接状态。LoopX 负责跨轮次的状态与执行边界，harness 负责有界执行。</sub>

<a href="https://trendshift.io/repositories/102379?utm_source=repository-badge&amp;utm_medium=badge&amp;utm_campaign=badge-repository-102379"><img src="https://trendshift.io/api/badge/repositories/102379" alt="loopx-project/loopx 在 Trendshift 的趋势排名" width="220" height="48"></a>

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE) [![Release](https://img.shields.io/github/v/release/loopx-project/loopx?filter=v*&display_name=tag)](https://github.com/loopx-project/loopx/releases/latest) [![Discord](https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white)](https://discord.gg/XmGgQyCFZd) [![TypeScript core](https://img.shields.io/badge/core-TypeScript-3178C6?logo=typescript&logoColor=white)](docs/architecture/rfcs/typescript-control-plane-migration-v0.zh-CN.md) [![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml) [![Local first](https://img.shields.io/badge/control--plane-local--first-brightgreen.svg)](docs/public-private-boundary.md) [![Loop Agents](https://img.shields.io/badge/status-loop%20agents%20active-brightgreen.svg)](docs/product/release-readiness.md)

[产品首页](https://loopx-project.github.io/loopx/) · [博客](https://loopx-project.github.io/loopx/blog/zh/) · [文档](https://loopx-project.github.io/loopx/docs/) · [开发者手册](https://loopx-project.github.io/loopx/docs/book/) · [试用 LoopX](#试用-loopx) · [查看真实 Loop](#证据) · [理解工作原理](#为什么需要-loopx) · [English](README.md)

</div>

---

LoopX 是开放且 Provider-neutral 的轻量 state kernel，也是 local-first
的 Loop Engineering 控制面。它运行在不同 agent harness 之上，而不是替代
它们：LoopX 提供长程状态、语义决策、治理、恢复与人机协同，让跨轮次、
跨工具、跨 agent 的工作可审阅、可恢复、可接力。

> 让 Loop 持续向前，让关键判断留在人手里。

## 学习 LoopX

- **开发者手册** - 从控制面基础到项目接入和开发者贡献的双语学习路径。[简体中文](https://loopx-project.github.io/loopx/docs/book/) · [English](https://loopx-project.github.io/loopx/docs/book/en/)
- **快速开始** - 安装、连接项目并运行第一个受治理的 Loop。[指南](docs/guides/getting-started.md)
- **文档站** - 完整参考与运维文档。[LoopX Docs](https://loopx-project.github.io/loopx/docs/)

## 认识个人 Agent 工作区

把长程目标收进同一个 local-first 工作区。Goal、待关注事项、对话、任务、
文件、定时计划与恢复状态跨天数、跨重启、跨 harness 保持持久。重新打开项目时，
可以检查上一轮的状态与证据，再继续下一项允许执行的工作。

<a href="docs/assets/personal-workspace/loopx-dashboard-launch.mp4">
  <img src="docs/assets/personal-workspace/workspace-1.0.webp" alt="LoopX 工作区：用户决策、Agent 任务、持续监控与完成记录" width="960">
</a>

LoopX 1.0 将这些长程控制状态汇入 Personal Workspace。你可以在一个页面中：

- 看清哪些事项正在等你、执行中、观察中、已安排或已停止；
- 配置 Goal 的 Capability，区分机器默认值与 Goal 覆盖，预览变更后再应用；
- 从关联的飞书会话进行实时 steering、消息排队或异步收件，不丢失 Goal/Agent/Session 路由；
- 查看交付文件、周期报告与关联证据；
- 在 Codex、Claude Code、direct-model 等已注册 Agent 会话之间延续工作，
  不丢失 Goal 状态与证据；
- 通过 typed preview、显式确认与 receipt 审阅受保护变更；浏览器只负责投影，
  LoopX state 始终是权威事实源。

```bash
loopx dashboard
```

`loopx dashboard` 是受支持的浏览器 / PWA 启动方式。也可从
[1.0 Release](https://github.com/loopx-project/loopx/releases/tag/v1.0.0) 下载桌面预览版，
复用同一组 loopback 服务与 Goal 状态。Apple Silicon macOS 支持签名 App 更新，
将桌面壳与内置运行时配套升级，并提供修复与恢复入口；需要 Python 3.11+，
App 为 ad-hoc 签名，尚未 notarize。Windows 预览版目前手动更新，需单独安装 CLI。
[桌面安装、更新与源码开发指南](apps/desktop/loopx-control-plane/README.md)。

<img src="docs/assets/personal-workspace/capability-1.0.webp" alt="工作区实录：配置子任务数量上限与允许的职责范围" width="960">

在源码 checkout 中运行 `python -m demo.workspace serve`，可探索社区活动、家庭能源比较
和社区网站发布三个复杂项目；每个项目有四个工作角色、18 项任务、两项决策与两项观察。
上图来自这份可复现工作区。[场景与回放说明](demo/workspace/README.md)。

[观看 32 秒完整演示](docs/assets/personal-workspace/loopx-dashboard-launch.mp4)
· [阅读工作区指南](docs/guides/personal-workspace-user-guide.md)
· [开始五分钟体验](docs/guides/personal-workspace-trial-guide.md)

## 为什么需要 LoopX

一个 agent 可以在单次会话里完成任务。长程工作更难：目标会变化，用户决策会出现，
证据会过期，平级 agent 会交接，scheduler 也可能在已经没有有效状态迁移时继续消耗。
聊天记忆和定时器不足以治理这些问题。

LoopX 把长期控制状态留在同一层紧凑状态里：

```text
目标 / issue / project
   │
   ▼
LoopX state：objective + gate + todo + scope + evidence + quota
   │
   ├─ 需要人类判断？ ── 是 ─▶ 提出具体问题并等待
   │
   ├─ 有安全侧路？ ─────────▶ 执行一个有界 agent slice
   │
   ▼
Codex / Claude Code / Cursor / shell agent 执行一轮
   │
   ▼
写回证据 + handoff + next todo ─▶ quota 决定下一次 tick
```

Agent runtime 负责执行，LoopX 负责治理跨运行延续的控制状态，让工程、
研究、discovery 和运营 Loop 能持续推进。它不是又一个 agent framework，也不是
绑定某一 Provider 的编排 runtime。

![LoopX control-plane board](docs/assets/control-plane-board.svg)

一个形象化理解是：LoopX 是
**[面向长程 Agent 的可执行看板](docs/development/control-plane-course/00-concept-primer.md)**。
卡片带有稳定身份、权限、证据和 continuation；移动卡片要经过 claim、gate、
monitor、validate、writeback 等 typed operator。看板是 projection，LoopX state
才是事实源。

注册 agent 彼此平级。todo claim、lease、任务边界、能力门和 typed continuation
共同决定下一步谁执行，不需要一个长期拥有全局权限的 leader agent。

LoopX 适合：

- 多天或多周的工程、研究、benchmark、实验目标；
- 需要跨轮保留 scope、证据和 review 状态的 issue / PR Loop；
- recurring heartbeat 或 monitor-style agent 工作；
- 带 owner、安全、发布或私有数据 gate 的项目；
- 需要 ownership、lease 和 handoff 的平级 agent team；
- 需要把进展、阻塞和反馈入口清晰呈现给非技术用户的创作、研究或运营工作。

LoopX 不是生产自动化控制器。危险权限、生产写入、公开发布和最终 ownership
仍由人类负责。

<a id="看几个例子"></a>

## 证据

OpenViking 的公开贡献序列与脱敏的 owner-run Auto ML showcase 各自跨越
**200+ 小时自然时长**，保留多轮 Todo、决策和证据更新。这里衡量的是项目经过的
wall-clock 时间，不是连续模型执行时长或无人值守的生产自治。点击原图查看
公开安全的任务图、证据分支和跨轮决策；各案例分别说明来源与可复现边界。

### 开源 Issue Fix

**超过 200 小时的公开贡献轨迹：Focused PR 交付与可复用修复知识互相反哺。**

<a href="docs/assets/long-running-loop-openviking-trajectory.png">
  <img src="docs/assets/long-running-loop-openviking-trajectory.png" alt="开源 Issue Fix 轨迹：连接 Focused PR 交付与 LoopX 通用能力沉淀" width="760">
</a>

LoopX 的创建者以
[OpenViking contributor](https://github.com/volcengine/OpenViking/pulls?q=is%3Apr+author%3Ahuangruiteng)
身份把这条路径用于持续的 issue-to-PR 修复。图中公开贡献序列从首个 PR 创建到
最后一次所示 review 或 update，跨越 200+ 小时。
[Issue-Fix 能力说明](loopx/capabilities/issue_fix/README.zh-CN.md)把 rolling
repository context、带 revision 的修复知识和 reviewer-facing preference
分开管理；所链接 PR 与当前 checkout 的源码、测试始终具有最高权威。

### Auto ML Experiment

**经过脱敏的 owner-run showcase：超过 200 小时的实验轨迹把假设、matched
evidence、无效谱系、运行中复现和 promote / stop gate 留在同一张图中。**

<a href="docs/assets/long-running-loop-ml-experiment-trajectory.png">
  <img src="docs/assets/long-running-loop-ml-experiment-trajectory.png" alt="Auto ML Experiment 轨迹：实验谱系、证据门和晋级决策" width="760">
</a>

这张 public-safe graph 保留了该 200+ 小时自然时间窗口中的决策谱系。它是
owner-run showcase，不代表连续算力执行、独立复现、生产结果，也不代表公司或
雇主背书；脱敏后的图片本身不足以让第三方独立复现实验。

### Auto Research

**可复现的公开 KNN demo：Proposer、executor、evaluator/promoter 并行迭代，
todo、quota、证据与 targeted wake 同屏可见。**

<a href="docs/assets/auto-research-multi-agent-showcase.png">
  <img src="docs/assets/auto-research-multi-agent-showcase.png" alt="Auto Research 多 Agent 工作区：proposer、executor、evaluator/promoter、todo、quota、证据与 targeted wake 同屏推进">
</a>

这张截图来自 LoopX 内置的 exact-KNN demo。公开 task、可编辑与受保护文件、
deterministic CPU evaluator、dev / held-out 命令均在仓库内。可按
[showcase walkthrough](docs/product/use-cases/auto-research/decentralized-auto-research-showcase.md)
或 [demo 命令路径](demo/auto_research/README.md)复现工作流；它是 demo
结果，不是生产研究结论。

### 真实项目中的使用

- **外部独立用户 · `>13h` C++ 精度修复。** 用户报告多阶段任务持续对齐目标，
  触发 public research 后采用[公开代码记忆工具](https://github.com/DeusData/codebase-memory-mcp)，
  最终精度明显提升。[查看证据边界](docs/showcases/cases/independent-cpp-accuracy-long-run.md)。
- **外部独立用户 · `4d` 无人干预运行。** 用户报告 Agent 连续四天无需人工
  干预，持续处理有价值的工作，并提供周期报告入口。
  [查看脱敏案例](docs/showcases/cases/independent-four-day-unattended-agent.md)。
- **外部独立用户 · `7` 个合并 PR。** 一次归因于 LoopX 的 Engine 重构可由
  [公开 Issue](https://github.com/zilliztech/mfs/issues/166)和七个合并 PR 检查；
  LoopX 归因与用户报告的 `10 亿+` token 规模仍按用户自述标记。
  [检查完整案例](docs/showcases/cases/independent-public-engine-refactor.md)。

这里长期只维护当前最强的三个案例，不复制全量清单。完整的 contributor case、
creator dogfooding、reproducible demo 和证据强度标签见
[Showcase 全量目录](docs/showcases/README.md)。

### 探索性 Benchmark 研究

- **[SWE-Marathon](https://loopx-project.github.io/loopx/benchmarks/swe-marathon/?lang=zh)**：
  在相同的 15 个任务上对照 5 种执行模式，比较自验证行为、得分与成本。
  更多自验证并未稳定转化为更高得分。
- **[LHTB × LoopX](https://loopx-project.github.io/loopx/benchmarks/lhtb/?lang=zh)**：
  在 46 个长程终端任务上对比 5 种执行机制，研究持久状态、Todo、replan
  与 fresh executor session。
- **[DeepSWE 行为分析](https://loopx-project.github.io/loopx/benchmarks/deepswe/behavior-discovery/)**：
  通过精选案例观察领域提示、需求保留与验证选择之间的关系，提出有待复验的机制假设。

SWE-Marathon 与 LHTB 每个任务、每种模式仅保留一条有效轨迹；DeepSWE 包含精选案例与
事后分析。目前三者均不足以证明普遍的性能提升。

更多可检查入口：

- [产品首页](https://loopx-project.github.io/loopx/)：查看产品叙事、快速开始和长程证据；
- [Showcase 全量目录](docs/showcases/README.md)和
  [中英双语托管索引](docs/showcases/index.html)；
- [跨 runtime 实现审阅演示](docs/product/use-cases/cross-runtime/cross-runtime-impl-review-demo.md)；
- 公开[用户手册](https://my.feishu.cn/wiki/CaL5wMk9ui17ngkWzeUcMlAYnZg)。

<a id="快速开始"></a>

## 试用 LoopX

要求：Python 3.11+ 与 Node.js 22.18.0+，推荐使用 Node.js 24 LTS。使用 console
scripts 已加入 `PATH` 的 Python 环境；macOS 和 Linux 使用 POSIX shell，原生
Windows 使用 PowerShell 7。Node.js 运行 LoopX 自动启动、空闲退出的 TypeScript
Effect core，无需手工维护 daemon。Git 仅用于源码贡献与 clone/canary 工作流。

无需 clone，直接从 PyPI 安装：

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

已有安装可使用 `loopx update plan` 与 `loopx update apply`；LoopX 会保留检测到
的 pip、pipx 或 archive owner，不会在升级时暗中切换安装渠道。

原生 Windows PowerShell 7 可直接使用同一 PyPI release，不需要 POSIX 兼容层：

```powershell
py -3.11 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

首次安装后重启 Agent host，使其重新加载 workflow skills。`pipx`、host command
surfaces、原生 Windows checkout 安装、升级、回滚、卸载与 archive fallback 见
[Installing LoopX](docs/guides/installing-loopx.md)。

然后在项目根目录连接：

```bash
cd /path/to/your-project
loopx connect
loopx status
```

如果项目尚未初始化，且 `connect` 明确提示缺少状态，可以走 guided path：

```bash
loopx start-goal --guided --project . --goal-text "你的长程目标"
```

已有 LoopX state 应复用，不要覆盖。确保 `.loopx/`、`.codex/goals/`、`.local/`
不会被提交。

### 从你已经在用的 Agent 启动

| Host | 推荐入口 | Loop driver |
| --- | --- | --- |
| Codex App | 让 agent 在当前项目里连接 LoopX、运行 `loopx doctor`、保留已有状态，并汇报当前 gate 和下一条 todo；然后用 `$loopx <复杂任务>` 或 `/skills` 里的 `loopx`。 | Codex App heartbeat；cadence 跟随 `quota should-run.scheduler_hint` |
| Codex App over SSH | `loopx agent-onboard --agent-type codex-app-ssh --project .` | 返回的可见 `/goal <task_body>` |
| Codex CLI | 在项目里启动 `codex`，让它连接并诊断 LoopX，然后用 `$loopx <复杂任务>` 或 `/skills`。 | 可见 `/goal <task_body>`；默认不走隐藏 headless 执行 |
| Claude Code | 安装 opt-in adapter，然后运行 `/loopx <任务>`，再运行 `/loop`。 | 由 LoopX gate 的原生 Claude Code `/loop` |
| KunlunCode | 运行 `loopx-kunluncode connect --project . --goal-id <goal-id> --agent-id <registered-agent-id>`，添加一条有边界的 todo，再运行 `loopx-kunluncode run --project .`。 | 经 app-server 驱动原生 Goal Pro；只有 strict verification 通过后，LoopX 才写回完成与 quota |
| OpenCode | 安装静态 command facade；recurring goal 显式 opt in `--with-goal-bridge`。 | OpenCode command facade 与显式 goal bridge |
| Pi | 用 `loopx slash-commands --install --surface pi` 安装 opt-in goal extension，然后在受信任的 Pi 会话里用 `/loopx <任务>`。 | 由 LoopX quota gate 的可见 Pi goal extension（`loopx_goal_activate` + `agent_settled` 续跑） |
| ZCode | 用 `loopx slash-commands --install --surface zcode` 安装 skill facade，然后在项目里的 ZCode 会话中调用 `$loopx` skill（或 `/loopx <复杂任务>`）。 | ZCode 会话自身的 turn loop；每次续跑都从 `quota should-run` 进入 |
| Antigravity CLI（agy） | 用 `loopx slash-commands --install --surface agy` 安装 skill facade，然后在项目里的 `agy` 会话中调用 `loopx` skill（或 `/loopx <复杂任务>`）。 | 会话原生 `/goal` 循环（审计至 `<!-- GOAL_COMPLETE -->`）加 `schedule` 自唤醒，随会话存活；facade 指示每次 turn/唤醒都先过 `quota should-run`——advisory 节流，非宿主强制 gate |
| Kiro CLI | 用 `loopx slash-commands --install --surface kiro-cli` 安装 skill facade，然后在项目里的 `kiro-cli` 会话中执行 `/loopx <复杂任务>`。 | 会话原生 `/goal --max <N> <任务> Done when: <验收条件>` 循环，验收条件写入目标语句本身（宿主由该语句推导验收标准），由宿主自己的迭代预算兜底（默认 5），并通过内置 `goal` 完成契约收口；facade 指示每次 turn 与迭代都先过 `quota should-run`——advisory 节流，非宿主强制 gate |
| DeepSeek Harness（dsh） | 安装 [DSH 原生 Plugin](packages/dsh-loopx-plugin/README.md)，在技能选择器中点 `loopx`，然后直接描述任务；[dsh goal-mode adapter](loopx/dsh_goal_mode/README.md) 继续支持 headless turn。 | 原生同会话续跑与 GoalBar，或 headless dsh 工作段；两条路径都遵守 LoopX authority |
| Cursor、shell、自有 runner | 使用同一 installer 和 `loopx doctor`，再手动连接或由 runner 调用。 | 你的 shell、scheduler 或 runner |

可直接粘贴的完整 setup message、host-specific 路由和故障恢复见
[Getting Started](docs/guides/getting-started.md)。Host 集成还可以查看
[Codex App host command registry](docs/reference/protocols/codex-app-host-command-registry-v0.md)、
[Codex CLI packaged install](docs/product/runtimes/codex-cli/codex-cli-packaged-install.md)和
[Claude Code adapter](loopx/claude_goal_mode/README.md)、
[KunlunCode 原生 Goal adapter](docs/guides/kunluncode-adapter.zh-CN.md)、
[Kiro CLI goal-mode adapter](loopx/kiro_cli_goal_mode/README.md)，以及
[DeepSeek Harness turn adapter](loopx/dsh_goal_mode/README.md)。

可查看 [60 秒 DSH × LoopX Replan 真实录屏和可复现
fixture](docs/showcases/cases/dsh-loopx-replan-demo.md)：安装 Plugin、选择一个
skill、加入新的关键约束，并检查完整保留的决策证据链。

自有 runner 请先看
[最小自定义 Runtime 示例](docs/guides/minimal-custom-runtime-example.zh-CN.md)
（`python3 examples/custom-runtime-minimal-cli-turn-smoke.py`），再读完整的
[把 LoopX 嵌入你的 Agent Runner](docs/guides/custom-agent-runner-integration.zh-CN.md)
与 [Worker Bridge Install Contract](docs/integrations/worker-bridge-install-contract.md)。
核心 tick 很小：

```text
loopx quota should-run      # 当前注册 agent 是否应该执行？
loopx todo claim            # 谁拥有这个 slice？
loopx todo update           # 发生了什么？
loopx refresh-state         # 下一轮应该看到什么？
loopx quota spend-slot      # 为完成并验证的 slice 记账
```

### 首次运行反馈

如果 LoopX 帮你跑通了第一个任务，欢迎用一分钟提交一条公开反馈（可选，无任何
遥测；不要粘贴日志、路径、凭据、内部项目名或 goal 内容）：

- [首次运行反馈](https://github.com/loopx-project/loopx/issues/new?template=first_run.yml)
- [长程使用案例](https://github.com/loopx-project/loopx/issues/new?template=usage_story.yml)

`loopx first-run-report` 会在本地打印同样的预填链接，不会发送任何数据。

成功连接后应该满足：

- `loopx doctor` 通过；
- 项目具有 `.loopx/registry.json` 和 active goal projection；
- `loopx status` 能显示当前目标、具体 user gate 和下一条 agent todo；
- 有可见 Loop driver，或 agent 给出精确 activation 指令；
- 本地 runtime state 被 ignore，而不是提交。

Clone 安装只面向需要 live canary wrapper 的贡献者：

```bash
git clone https://github.com/loopx-project/loopx ~/loopx
~/loopx/scripts/install-local.sh
loopx doctor
```

## 能力

LoopX 显式保留架构边界，使同一个受治理结果在更换 Agent harness 或外部 Provider
后仍然成立。以下术语描述的是不同边界，而不是几种可以互换的插件：

| 边界 | 含义 | 深入阅读 |
| --- | --- | --- |
| **Kernel** | 持有持久化 goal、todo、gate、evidence、quota、恢复和调度事实。 | [核心架构](docs/architecture.md) |
| **Capability** | 定义稳定、provider-neutral 的合同，从 LoopX 状态交付一个有界、可验证的调用者结果。 | [Capability 目录](loopx/capabilities/README.md) |
| **Provider** | 调用外部系统或本地实现，返回有界 observation、effect result 和 readback。 | [Provider 运行责任](docs/reference/extensions.md#runtime-responsibilities) |
| **Extension** | 通过显式安装、readiness、启用、升级、停用和回滚生命周期交付可选 Provider。 | [Extension 生命周期](docs/reference/extensions.md#runtime-lifecycle) |

`--available-capability shell` 等 host 声明描述的是已观测到的执行支持；在这张
产品图里属于 runtime capacity，不是产品 Capability，也不授予权限。真正执行前，
Capability 仍须应用自己的 policy 和 authority check，再提出 typed transition。

### 控制面核心承诺

Kernel 把控制面归结为五个用户可以直接行动的问题。每个问题对应一个产品
承诺：目标 → 长程状态；下一步 → 语义决策；人类判断 → 人机协同；
证据 → 恢复；能否继续 → 治理。

| 问题 | LoopX 保持可见的状态 |
| --- | --- |
| 当前目标是什么？ | Active goal、明确 scope 和当前 authority。 |
| 下一步是什么？ | 有序 user / agent todo、ownership、claim 和 lease。 |
| 哪一步需要人判断？ | 具体 user gate，而不是模糊的“等待 owner”。 |
| 证据发生了什么变化？ | 紧凑 run history、验证、blocker 和已接受 writeback。 |
| Loop 是否可以继续？ | Quota、capability、安全侧路、scheduler hint 和停止条件。 |

### 控制面能力

| Surface | 作用 | 从这里开始 |
| --- | --- | --- |
| Goal state 与 status | 跟踪 active state、todo、claim、gate、evidence、run history 和首屏关注点。 | `loopx status`、`loopx diagnose`、`loopx review-packet` |
| Quota 与 interaction contract | 决定一轮应该执行、提问、等待、自修复还是静默。 | `loopx quota should-run`、[Quota Allocation](docs/quota-allocation.md) |
| Agent runtime bridge | 让 Codex App、Codex CLI、Claude Code 和 generic worker 服从同一 guard。 | `loopx heartbeat-prompt`、`loopx codex-cli-bootstrap-message`、`loopx worker-bridge` |
| Operator surface | 呈现紧凑状态，但不让浏览器成为状态事实源。 | `loopx serve-status`、[Dashboard](apps/presentation/dashboard/README.md) |
| External projection | 把 todo / gate 投影到协作表面，同时保持 LoopX 权威。 | `loopx lark-kanban`、[Lark Kanban adapter](docs/integrations/lark-kanban-control-plane-adapter.md) |
| Domain capability | 打包 Issue Fix、内容运营、value connector、ML 实验、benchmark 与 Explore 等可重复泳道。 | `loopx issue-fix`、`loopx content-ops`、`loopx value-connectors`、`loopx ml-experiment`、`loopx benchmark`、[Explore](loopx/capabilities/explore/README.md) |
| 实验性上下文学习 | 通过 ignored、默认关闭的项目配置，为明确注册的 agent 试用 provider-neutral Reward Memory；OpenViking 是 provider 之一，不是全局依赖。 | `loopx reward-memory experiment-status`、[Reward Memory 中文架构](loopx/capabilities/reward_memory/README.zh-CN.md) |
| Governance pattern | 沉淀可复用的 routing、gate、evidence、projection 和 planning 形状。 | [Interaction Pattern Catalog](docs/concepts/interaction-pattern-catalog.md)、[State Model](docs/state-interaction-model.md) |

这些能力共同提供 lifetime goal、具体 user gate、经过审计的安全侧路、平级 todo
ownership、quota 与 steering、紧凑 run history、证据化 handoff、read-first 管理面、
项目级价值信号和 public/private boundary check。

### 产品 Capability 路径

Capability 把上述通用原语组成 outcome-owned 工作泳道。先按结果选择，再检查当前
已注册实现及其写入边界：

| 你需要…… | Capability | 从这里开始 |
| --- | --- | --- |
| 把公开 issue 推进为可审查、有证据的变更 | [Issue Fix](loopx/capabilities/issue_fix/README.zh-CN.md) | `loopx capability show issue-fix --format json` |
| 在交付前对精确 final diff 做质量验收 | [Change Quality](loopx/capabilities/change_quality/README.md) | `loopx capability show change-quality-qualification --format json` |
| 维护由多个已审查分支组成、持续变化的集成栈 | [Integration Branch](loopx/capabilities/integration_branch/README.md) | `loopx capability show integration-branch-reconcile --format json` |
| 在不丢失假设和发现的前提下探索不确定研究问题 | [Explore](loopx/capabilities/explore/README.zh-CN.md) | `loopx capability show explore --format json` |
| 基于当前证据和已验证结果重新建立决策上下文 | [Decision Context](loopx/capabilities/decision_context/README.zh-CN.md) | `loopx capability show decision-context --format json` |
| 生成带 receipt 的定时或进展触发报告 | [Periodic Report](loopx/capabilities/periodic_report/README.md) | `loopx capability show periodic-report --format json` |

运行 `loopx capability list --format json`，读取当前安装版本的权威目录。Capability
详情包含用户价值、成熟度、Provider readiness、入口命令、写入边界、协议和持久
验证。可从[人类可读 Capability 索引](loopx/capabilities/README.md)按结果选择；安装或
开发 Provider 时，再进入[Extension 与 Capability](docs/reference/extensions.md)。

### 四种运行责任

| 角色 | 负责什么 |
| --- | --- |
| **Agent** | 通过 host/runtime 完成方案、分析、工具使用和一次有界执行。 |
| **Provider** | 调用外部系统，返回 observation、effect result 与 readback。 |
| **Capability** | 定义调用者结果，归一化并验证 provider 输出，提出 typed transition。 |
| **Kernel** | 持久化 todo、gate、monitor、已接受 writeback、quota、恢复与调度。 |

执行路径是 `Agent -> Capability -> Provider`，控制结果沿
`Provider readback -> Capability transition -> Kernel` 返回。Extension 负责可选
provider 的打包和生命周期，不是另一个控制面 owner。详见
[核心架构](docs/architecture.md)与
[Extension / Capability 参考](docs/reference/extensions.md)。

## 进阶路径

第一次有用的 Loop 不依赖全部可选能力。只有工作真正需要时再开启这些路径。

启用进阶能力前，先只读查看当前目标的能力目录：

```bash
loopx configure-goal --goal-id <goal-id>
```

不带 `--execute` 时，它只报告当前/默认状态、适用条件、边界和可复制命令，
不会修改项目状态。

### Preset 与 Auto Research

安全 preset 覆盖 Daily Triage、Changelog Draft 和 PR Watch。更高级的 CI /
Dependency Sweeper 需要明确授权、隔离 worktree、verifier、quota/cost gate 和人工
review。Auto Research 通过 proposer、executor、evaluator/promoter 协作，同时保持
quota 和证据可见。详见
[入门 Preset 指南](docs/product/foundations/beginner-loop-presets.md)和
[Auto Research Demo Path](demo/auto_research/README.md)。

```bash
loopx preset list
loopx preset show daily-triage
```

查看 preset 是只读操作。对已连接的周期性目标，可运行
`loopx ready-score --goal-id <goal-id> --agent-id <agent-id>`，检查它是否适合重复运行。

### Governed Turn

LoopX 可以根据 validated receipt、fresh quota state 和 provider-neutral budget
生成一次纯函数、有界的 turn decision。当前 Codex CLI 启动路径和 activation contract
见 [LoopX Turn Codex CLI Quickstart](docs/product/runtimes/codex-cli/loopx-turn-codex-cli-quickstart.md)。

### Explore Graph / Harness

Explore 正式支持、可选、默认关闭。它适合具有可量化 offline eval、baseline、
treatment 和 guardrail 的任务，不替代生产审批。先读
[Explore Capability](loopx/capabilities/explore/README.md)及其
[Lark Presentation Mapping](loopx/capabilities/explore/README.md#presentation-sink-lark-mapping)。

### 审阅 Agent 工作

`loopx review-packet` 提供 owner-facing 的紧凑视图：决策、证据、验证和未解决 gate。
[Intelligent Management Surface](docs/product/surfaces/intelligent-management-surface.md)
解释 operator model；[Project-Level Reward Model](docs/product/foundations/project-level-reward-model.md)
定义产出数量、质量、token cost 和 user attention cost 的保守价值信号。

### App 与 Projection

- 本地 read-first UI：[Dashboard Guide](apps/presentation/dashboard/README.md)
- 公开产品概览：[产品首页](https://loopx-project.github.io/loopx/)
- 文档门户：[线上文档](https://loopx-project.github.io/loopx/docs/)
- 飞书投影：[Lark Kanban Adapter](docs/integrations/lark-kanban-control-plane-adapter.md)
- 通用 host 集成：[Integration Guide](docs/integration.md)
- 自有 multi-agent runner：
  [最小自定义 Runtime 示例](docs/guides/minimal-custom-runtime-example.zh-CN.md)，
  再看 [Custom Runner 中文指南](docs/guides/custom-agent-runner-integration.zh-CN.md)

可选 projection 让状态更易检查，但不会成为新的事实源。

### 日常操作与恢复

日常检查从这三个命令开始：

```bash
loopx status
loopx history --goal-id your-project-goal
loopx quota should-run --goal-id your-project-goal
```

自动轮次必须先检查 quota，只有完成验证与 writeback 后才记录 spend。静默 skip、
preflight failure 和 dry-run preview 不消耗 quota。一个 lane 被 user gate 阻塞时，
独立审计过的安全侧路可以继续，但不能绕过 gate。

平级 agent 在执行前使用 `loopx todo claim`，验证后使用 `loopx todo update`，
让 ownership 与证据持续可见。

Scheduler cadence 跟随 `quota should-run.scheduler_hint`；Codex App automation
通过 payload 返回的 `ack_hint.cli_args` 确认当前 hint。Collision recovery、monitor、
self-repair 和精确 operator 命令统一维护在
[Getting Started](docs/guides/getting-started.md)、
[Quota Allocation](docs/quota-allocation.md)和
[Long-Task Cadence Policy](docs/operations/long-task-cadence-policy.md)。

公开发布前运行：

```bash
loopx check \
  --scan-path README.md \
  --scan-path docs/ \
  --scan-path examples/
```

## 当前技术方向

LoopX 当前有三个活跃战略计划和一个架构与研究孵化器。这些内容用于表达方向，
不是交付承诺；`main`、已发布 artifact 和 stable reference contract 仍然定义真实
已交付行为。

- **长程 Benchmark 与证据：**在互补 benchmark 环境中建立可复现的能力证据，
  并开展受控的机制研究。
  [方向 Tracker](https://github.com/loopx-project/loopx/issues/3243)
- **Operator Surface 与 IM Integration：**建设 operator workspace、session
  record 与有界协作表面；当前在专用 integration branch 孵化，由 `@maxliux5`
  作为 implementation lead。
  [方向 Tracker](https://github.com/loopx-project/loopx/issues/3244)
- **Shared Goal Authority 与跨 Host 协作：**为显式共享 goal 提供
  provider-neutral 协调；NoKV 是尚未晋级的 provider candidate，而不是新的控制面
  权威。
  [方向 Tracker](https://github.com/loopx-project/loopx/issues/3245)
- **架构与研究孵化器：**以明确不同的成熟度推进 Effect Program hardening、
  TypeScript parity migration、hierarchical stride、research exploration、human
  attention、artifact lifecycle 与 memory utility。
  [方向 Tracker](https://github.com/loopx-project/loopx/issues/3246)

完整阶段、promotion gate、贡献者安全切片和 ownership 边界见
[当前技术方向地图](docs/project/technical-directions.zh-CN.md)；社区讨论使用置顶的
[GitHub Discussion](https://github.com/loopx-project/loopx/discussions/2851)。核心控制面
可靠性继续作为这些计划共同的底座。

## 进阶文档

按当前任务选择入口；[线上文档](https://loopx-project.github.io/loopx/docs/)
提供发布后的浏览入口，[完整文档索引](docs/README.md)仍是权威地图。这里仅保留
精选入口；每个分类索引负责承接更深层的文档和版本化协议。

### 使用与运维

- [Getting Started](docs/guides/getting-started.md)：安装、连接、诊断、heartbeat、
  dashboard、开发和命令参考。
- [用户手册](https://my.feishu.cn/wiki/CaL5wMk9ui17ngkWzeUcMlAYnZg)：
  公开 onboarding、概念、FAQ 和案例。
- [Operations](docs/operations/README.md)：goal continuation、todo、cadence、
  attention 和 authority 工作流。
- [Quota Allocation](docs/quota-allocation.md)与
  [Heartbeat Automation Prompt](docs/heartbeat-automation-prompt.md)：scheduler
  eligibility、spend 和定时续跑。
- [Dashboard](apps/presentation/dashboard/README.md)与
  [Status Data Contract](docs/status-data-contract.md)：面向操作者的状态与投影契约。
- [Release Readiness](docs/product/release-readiness.md)：安装升级、兼容性 gate、
  release note 和稳定表面。

### 理解控制面

- [Architecture](docs/architecture.md)：lifetime-goal invariant 与 kernel。
- [State Interaction Model](docs/state-interaction-model.md)：actor、store、
  interaction contract 与 writeback。
- [Concepts](docs/concepts/README.md)：可复用的 routing、gate、evidence、
  projection 与 planning pattern。
- [Product Foundations](docs/product/foundations/README.md)：Loop Engineering
  原则、project-level reward 和 reward-style replanning。
- [Product Vision](docs/product/vision.md)：更广义的 Loop Agent 产品方向。

### 集成与扩展

- [Integration Guide](docs/integration.md)
- [最小自定义 Runtime 示例](docs/guides/minimal-custom-runtime-example.zh-CN.md)
- [Custom Agent Runner 中文指南](docs/guides/custom-agent-runner-integration.zh-CN.md)
- [Integrations](docs/integrations/README.md)：runtime、host、协作和外部系统 adapter，
  包括 worker bridge 与 Lark。
- [Extensions and Capabilities](docs/reference/extensions.md)

### 构建与评审 LoopX

- [Developer Guide](docs/development/README.md)：贡献者工作流、benchmark 开发、
  文档布局和质量 gate。
- [Reference and Protocols](docs/reference/README.md)：稳定契约和版本化实现协议，
  包括 host command 与 reward memory architecture。
- [控制面开发者 9 讲](docs/development/control-plane-course/README.md)。
- [Testing and Quality](docs/development/testing-and-quality.md)：分层验证与风险检查。
- [Public/Private Boundary](docs/public-private-boundary.md)：安全的 fixture、示例、
  evidence 与发布边界。

### 查看结果与证据

- [Showcase Catalog](docs/showcases/README.md)：public-safe 案例和 evidence label。
- [Research and Evidence](docs/research/README.md)：benchmark 调查和有来源的结论。
- [Update Notes](docs/update-notes/README.md)：公开安全的进展记录。

### 项目与社区

- [当前技术方向](docs/project/technical-directions.zh-CN.md)
- [Project Governance](.github/GOVERNANCE.md)
- [Contributing](CONTRIBUTING.md)与[Contributor Tasks](docs/development/contributor-tasks.md)
- [Authors and Contributors](docs/project/authors.md)
- [Project History](docs/project/history.md)
- [Name and Marks](docs/project/trademarks.md)
- [对外品牌使用指南](docs/project/brand-guide.zh-CN.md)：开源项目、公司和用户如何引用 LoopX、描述集成并使用图形素材
- [ADOPTERS](ADOPTERS.md)：项目与用户自愿维护的采用目录
- [生态采用清单](docs/community/ecosystem-adoption.zh-CN.md) - 我们观察并持续追踪的
  真实集成、采样借鉴与衍生周边

## 合作伙伴项目

LoopX 欢迎与其他开源项目协作，共建长程 Agent 生态。已确认的合作伙伴包括：

- [OpenViking](https://github.com/volcengine/OpenViking) - 面向 AI Agent 的自进化
  上下文数据库
- [NoKV](https://github.com/NoKV-Lab/NoKV) - AI 原生分布式文件系统

## 用户群与反馈

LoopX 已在真实长程 agent 目标上持续运行，并处于活跃迭代期。最需要真实长程
agent 项目里的反馈：控制面帮到了哪里、哪里太重，哪些 gate、handoff 或 scope
仍然不够清楚。

- 可复现 bug、安装问题、功能建议：请提
  [GitHub Issue](https://github.com/loopx-project/loopx/issues)。
- 文档修正、showcase 补充、小型 public-safe 示例：欢迎开 PR。
- 参与社区讨论：可加入 [Discord 社区](https://discord.gg/XmGgQyCFZd)，也可在
  下方直接加入飞书群或通过微信申请入群。

渠道分工、支持边界和官方发布源见 [Support](.github/SUPPORT.md)。

<p align="center">
  <a href="docs/assets/loopx-lark-developer-group.png"><img src="docs/assets/loopx-lark-developer-group.png" alt="LoopX 飞书开发群二维码" width="280"></a>
  <a href="docs/assets/loopx-wechat-contact.png"><img src="docs/assets/loopx-wechat-contact.png" alt="LoopX 微信联系人二维码" width="220"></a>
</p>
<p align="center">
  <sub><strong>飞书：</strong>扫码直接加入<br><strong>微信：<code>huangrt00</code></strong> · 好友申请备注 LoopX</sub>
</p>

## 贡献

公开、可认领的任务见 [Contributor Tasks](docs/development/contributor-tasks.md)。贡献前请读
[Contributing](CONTRIBUTING.md)，尤其是 public/private 边界、smoke 保留规则和
benchmark 证据边界。

项目角色与维护权限见 [Governance](.github/GOVERNANCE.md)，创建者与贡献者归属见
[Authors and Contributors](docs/project/authors.md)，关键公开演进见
[Project History](docs/project/history.md)，名称与标识使用见
[Name and Marks](docs/project/trademarks.md)。

不要提交 `.loopx/`、`.codex/goals/`、live `ACTIVE_GOAL_STATE.md`、内部链接、
raw benchmark task/log/trajectory/verifier output、credentials、token、私有路径或
未脱敏的用户与团队信息。

## 当前状态

LoopX 1.0 已经是一套可用的长程 Agent 本地控制面，正在进入更广泛的采用阶段。
LoopX 不是完整 agent platform，不是 agent runtime，也不是自治生产控制器。

目前 LoopX 已交付围绕 goal、typed todo / decision scope、平级 claim / lease、
evidence / writeback、quota-aware scheduling 和跨轮 continuation 的 durable state
kernel。在这套共享控制状态之上，已经提供 guided start、recurring heartbeat、
隔离 Codex CLI Turn、evidence-backed Issue-Fix admission、可选 Explore / auto
research 路径、公开 validation canary，以及多项目 Personal Workspace。

不同表面的支持等级仍需明确区分：state 与 CLI contract 是稳定核心；部分 host
integration 和进阶路径仍是 optional、default-off 或 experimental。LoopX 不会自行
获得 credential，不会替用户批准 destructive / production action，不会在未授权时
公开发布，也不会把未经验证的 run 当成成功证据。

当前投入按[技术方向地图](docs/project/technical-directions.zh-CN.md)组织：长程
benchmark 证据、operator surface 与 IM integration、shared-goal 跨 host 协作，以及
明确分阶段的架构与研究孵化器。

## Star 趋势

<p align="center">
  <a href="https://github.com/loopx-project/loopx/stargazers"><img src="https://loopx-project.github.io/loopx/site-assets/star-history.svg" alt="LoopX GitHub Star 历史趋势，来自已校验快照" width="800"></a><br>
  <sub>由仓库授权的 workflow 每 6 小时基于 GitHub 官方 stargazer 时间戳生成；仅当拉取条数与 GitHub 当前 Star 总数一致时发布。GitHub 图片缓存可能延迟刷新。</sub>
</p>

## License

从 `v0.4.8` 起采用 Apache License 2.0，见 [LICENSE](LICENSE) 与
[NOTICE](NOTICE)。`v0.4.7` 及更早版本永久保留原 MIT 许可；历史许可证全文与
notice 保存在 [LICENSE-MIT](LICENSE-MIT)。[许可证政策](docs/project/licensing.md)
说明版本、贡献、专利授权与 open-core 边界。
