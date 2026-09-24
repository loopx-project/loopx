# 如何使用本书

本书面向已经会使用 Git、终端和至少一种 Agent 开发工具的外部开发者。你不需要先读
LoopX Kernel 源码，也不需要理解所有 CLI 子命令。

## 你会完成什么

全书先用六个编号章节和一张状态机地图建立控制面心智模型，再进入两条实践主线：

```text
控制面基础
├── 接入现有 Git 项目
└── 开发者贡献
    ├── Control Plane、Capability 与 Domain State
    ├── Provider、Host/Runner、Projection、Docs 与 fixtures
    └── Extension 与独立 package lifecycle
```

基础篇依次覆盖：

1. 为什么一次 Session 不足以承载长程任务；
2. 普通会话、Host Goal 与 LoopX 分别拥有哪一层状态；
3. canonical state、workbench、event 与 read-only projection；
4. Todo 工作图、Gate、claim、lease、authority 与 Peer 协作；
   读完后用[主要状态机与状态流转](./core-state-machines.md)把 source state、derived decision、
   projection 与九组状态机连成一张图；
5. 一轮受治理的 Turn 如何决定、执行、验证和写回；
6. retry、replan、self-repair、terminal closure 与运行边界。

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

- **多个短 Session 怎样组成长程任务？** 先读第 1、2 章，再下钻
  [概念导读](/loopx/docs/development/control-plane-course/00-concept-primer/)、
  [第 1 讲：Harness 是 effectful program](/loopx/docs/development/control-plane-course/01-agent-loop-effectful-program/)与
  [第 2 讲](/loopx/docs/development/control-plane-course/02-goal-control-plane-architecture/)，
  再用[第 3 讲](/loopx/docs/development/control-plane-course/03-first-real-loop/)走一遍真实 Loop。
- **状态、工作图与权限分别由谁拥有？** 先读第 3、4 章和
  [主要状态机与状态流转](./core-state-machines.md)，再下钻
  [第 4 讲](/loopx/docs/development/control-plane-course/04-state-substrate/)与
  [第 5 讲](/loopx/docs/development/control-plane-course/05-work-graph-and-peers/)。
- **Gate、Monitor、Replan 同时出现时哪条规则优先？** 先读
  [主要状态机与状态流转](./core-state-machines.md)和第 5 章，再下钻
  [第 6 讲](/loopx/docs/development/control-plane-course/06-quota-decision-kernel/)与
  [第 7 讲](/loopx/docs/development/control-plane-course/07-host-scheduler-and-heartbeat/)。
- **长程任务怎样防止目标漂移与局部空转？** 先读第 6 章，再下钻
  [长程收敛专题](/loopx/docs/development/control-plane-course/topic-long-horizon-convergence/)与
  [第 8 讲](/loopx/docs/development/control-plane-course/08-evidence-refresh-and-self-repair/)。
- **怎样修改规则并证明它可交付？** 先读第 10 至 13 章，再下钻
  [第 9 讲](/loopx/docs/development/control-plane-course/09-engineering-a-control-plane-rule/)与
  [第 10 讲](/loopx/docs/development/control-plane-course/10-autonomous-agent-quality-gates/)。
- **Extension、领域能力与 Kernel 怎样组合？** 先读第 14 至 16 章，再下钻
  [第 11 讲](/loopx/docs/development/control-plane-course/11-extension-layer/)。

完成基础篇后：

- 想先掌握 1.0 的日常操作面，从[操作 LoopX 1.0 Workspace](./workspace-v1.md)开始；
- 只想管理自己的项目，从[连接你的 Git 项目](./05-connect-existing-project.md)开始；
- 想给 LoopX 做任何公开贡献，从[开发者贡献地图与协议入口](./source-protocol-map.md)开始；
- 已经确定需要独立安装、启停和升级的 Provider/package，再进入
  [选择正确的放置位置](./08-extension-placement.md)。

项目接入与开发者贡献共享基础模型，但没有先后依赖。Extension 是开发者贡献中的一种交付和
lifecycle 选择，不是所有贡献的默认终点。

## 章节如何组织

每章优先回答四个问题：

1. 读者此时要解决什么问题；
2. 成功后能观察到什么；
3. 哪些概念足以解释这些行为；
4. 正常路径失败时从哪里恢复。

命令片段会标明其性质：

- **可直接运行：** 已在标注的 LoopX 版本上核对命令表面；
- **基于官方 scaffold：** 示例只给出完成当前任务所需的领域改动、协议和验证，不依赖单独的
  配套练习仓库；
- **为解释而简化：** 用于说明状态关系，不应直接写入生产配置。

## 权威来源

本书拥有教学顺序和解释，不拥有 LoopX 的版本化行为：
中文与英文版本是语义镜像；版本事实、状态、命令、边界或结论出现实质差异都属于文档缺陷。

| 内容 | 权威来源 |
| --- | --- |
| CLI 参数、协议和 runtime 行为 | LoopX 发布物、`--help` 与官方仓库 |
| 学习路径、scaffold 导读、概念解释与取舍建议 | 本书 |
| Kernel 源码领读、组合 case、decision table 与实验路线 | Control-Plane Developer Course |
| 你的项目事实 | Git、CI、外部服务和项目自己的事实源 |

当本书与当前发布版本冲突时，先以发布物为准，再提交文档修正。不要为了让教程“跑通”而绕过
新版本的权限或生命周期检查。

## 版本基线

当前内容以 LoopX GitHub release `v1.1.0` 为发布锚点；本地命令示例已按 `loopx 1.1.0` 的
公开 CLI 与协议表面复核。该历史版本要求 Python 3.11+ 与 Node.js 22.18.0+；此源码版本要求
Node.js 22.22.3+。后者运行由 LoopX 自动管理、
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

### 如何理解当前 TypeScript 改造

`v0.5.4` 不是“已经把 LoopX 全部重写成 TypeScript”。当前发布基线是：

- TypeScript 已拥有 Effect Program、Turn/Host Todo settlement、Todo completion、quota
  delivery/spend/void/monitor-poll、本地 task-lease lifecycle、Vision refresh、governed capability
  validation，以及 scheduler heartbeat/state 等迁移切片的 canonical semantics；
- Python CLI 仍负责迁移期 transport、legacy response projection、明确的外部 Provider 调用和部分
  Markdown/event 写回；
- 同一条迁移后的规则只能有一个 semantic owner。Python facade 适配 TypeScript transaction，
  不能再实现第二套独立 decision；
- 当前 `main` 已进入 transaction-payoff phase：后续以完整 transaction cutover 和删除旧语义
  为进展单位，不继续堆 leaf helper 与 bridge。

发布态以 `v0.5.4` tag 和 release notes 为准；迁移阶段、下一批 cutover 与最终 CLI/App 收敛以
[TypeScript Control-Plane Migration RFC](/loopx/docs/architecture/rfcs/typescript-control-plane-migration-v0/)
的当前状态为准。`v0.5.4` 只交付了 Stage 3 的第一个 receipt-bound scheduler follow-up 切片；更广的
CLI/App convergence 与 Stage 4 distribution cleanup 仍是后续方向。

### 从 `v0.4.4` 阅读基线升级到 `v0.5.4`

如果你读过旧版 Dev Book，优先校准四个变化：

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
