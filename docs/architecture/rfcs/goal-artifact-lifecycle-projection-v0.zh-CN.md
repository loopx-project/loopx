# RFC：Goal Artifact 生命周期投影（milestone / guard / next-transition）v0

- 状态：已接受
- 替代 / 关闭：无
- 提出方：LoopX maintainers
- 日期：2026-08-12
- 范围：一个只读的 goal 级生命周期投影，由现有 typed state 派生；全局聚合视图作为后续切片；不改变运行时状态机，不引入流程引擎
- 基线：LoopX `ef5a8acb1`
- 语言说明：[英文版](./goal-artifact-lifecycle-projection-v0.md)与本中文版互为语义镜像，差异视为缺陷。

---

## 背景：由 Artifact-Centric 业务流程管理（ABPM）衍生

本 RFC 由 **artifact-based business process management（ABPM）** 衍生而来。ABPM 是流程研究里以工件为中心的路线：把工作建模为“业务工件”而不是流程图。在 ABPM 中，业务工件是带身份和显式生命周期的实体；**guard condition（守卫条件）** 决定每个状态迁移是否合法，**milestone（里程碑）** 是 case 可以到达的、对外有意义的进度点。Guard-Stage-Milestone（GSM）是这个语义最著名的形态。

LoopX 已经部分遵循这套哲学：goal、todo、gate、evidence、lease、run history 都是 typed 的，多个 bounded context（content item、benchmark case、observable artifact handle）已经有了类似工件生命周期。缺的是 **goal 这一层本身**的工件视角。

本 RFC 只借用 artifact-lifecycle 的词汇（milestone / guard / next-transition），把它作为 goal 的只读投影。明确不采用流程引擎、流程图或统一生命周期抽象——这些是第 2 节里的非目标。

## 0. 一个帮助理解的例子

运维打开一个长跑 benchmark 认证 goal 的 dashboard。这个 goal 已经跑了几周。页面上有 todo 数量、quota 状态、reason codes、最近一次 run 的 classification。但没有一个能回答运维真正关心的三个问题：

- **这个 goal 处于生命周期的哪个阶段？**（还在启动、正在认证、等待 owner 决策、还是收尾？）
- **它达成了哪些 milestone？**（环境就绪、baseline 通过、release-candidate 证据收集完毕）
- **哪个 guard 挡住了下一步，owner 是谁？**（baseline 已达成，但“owner 批准 baseline”仍未关闭，且没有其他合法迁移）

运维只能从分散的 todo 状态、gate 列表、evidence key、run-history classification 里手动拼出答案。LoopX 已经把 goal 之下的每一层都建模成 typed state；goal 本身恰恰是 operator 最常看、却最缺少 typed 语义的一层。

## 1. 本 RFC 的选择

提出一个派生的只读投影 `goal_artifact_lifecycle_projection_v0`，把 goal 当作一个有生命周期的业务工件：

```text
goal_id
lifecycle_phase            （派生，不是新增存储状态）
milestones[]               （id, label, reached, reached_evidence_refs[]）
guards[]                   （id, kind, blocked, owner, decision_scope,
                             evidence_required）
next_transitions[]         （target_phase, precondition, reason_codes[]）
```

投影**只从 LoopX 已拥有的状态派生**：

- registry goal 字段与 active-state todo（task class、status、action kind、claimed owner）；
- user gate 与 decision scope（`blocks_agent`、`resume_when`、`required_decision_scope`，以及未来上线的 `validation_command`）；
- evidence 与 run history（compact evidence refs、delivery outcome、material milestone runs、classification）；
- goal frontier 与 work-lane reason codes（控制面认定的下一步合法动作）。

Milestone 的达成：优先取 goal 声明的 acceptance 标记；没有声明时回退到证据派生标记（带 material batch scale 的 bounded `primary_goal_outcome` 或 `compact_evidence` run）。Guard 是未关闭的 owner 决策或未满足的证据前置条件。Next transitions 复用现有 frontier/lane 推导，不新建状态机。

## 2. 非目标

- 不做 BPMN/流程引擎、workflow DSL 或流程图执行。
- 不统一已有 bounded lifecycle（todo、monitor、content item、benchmark case、PR、observable artifact handle 各自保留合法迁移）。
- 暂不引入 milestone 强制语义：本 RFC 不改变“done”的定义，也不新增 gate。
- 不新增 goal 存储状态：`lifecycle_phase` 和 milestone 只是投影，不是权威字段。

## 3. 短期应用（未来 1-2 个 release）

1. 新增 `loopx/control_plane/goals/artifact_lifecycle.py`：从 status/goal payload 派生投影（纯函数，不读文件）。
2. 一个 fixture smoke：用合成 goal 证明派生规则——声明的 milestone、evidence 达成的 milestone、未关闭的 owner gate 作为 blocking guard、来自 work lane 的合法 next transition。
3. 第一个消费者：status markdown 与 dashboard 的 goal 详情（与 `goal_channel_projection_v0` 并列展示 `goal_artifact_lifecycle_projection_v0`），让运维能回答例子里的三个问题。
4. 公开边界与现有投影一致：不含 raw logs、evidence body、credentials、本地路径。

## 4. 中期应用（2-4 个 release）

1. **全局 goal board**：把 per-goal 投影跨 goal 聚合——哪些 goal 达成哪些 milestone、哪些 guard 阻塞了多少 goal、每个 goal 的 next transition 是什么。这是现有 `/loopx-global-*` 家族和 operator dashboard 的自然落点。
2. **Periodic report 摘要**：milestone 与 guard 的增量成为报告行（“milestone reached”“guard opened/closed”），替代自由文本 run 摘要。
3. **Replan novelty 底座**：把“已覆盖 milestone / 已尝试迁移”变成结构化输入，替代部分 evidence-log 字符串扫描。
4. **验证完成守卫面**：`validation_command` 上线后作为 `guards[]` 的 evidence requirement，让完成路径有一个统一的地方解释“为什么迁移被阻塞”。

## 5. 长期应用（出现真实调用方之后）

每一项都以 active production call site 为前提，通过 scope-fit review 后才实现：

1. **Milestone-based closeout 语义**：如果有调用方需要强制的完成定义，把“goal done”定义为所有声明的 milestone 均达成且带 evidence，而不是“todo 完成 + no-followup”。
2. **Case 所有权视图**：用 claim、lease、guard owner 回答多 agent goal 的“这个 case 归谁、哪个 guard 阻塞它”。
3. **Artifact history 作为 covered-state ledger**：只有当第二个消费者出现时，才提供 typed “已覆盖状态”查询（基于 run history 与 rollout events）。

## 6. 备选方案

- **统一所有 bounded lifecycle 为一个 artifact 抽象**：拒绝。各 bounded context 的合法迁移不同，合并会产生重复知识（AGENTS.md duplicate-knowledge gate）。
- **引入 workflow/流程引擎**：拒绝。LoopX 的价值是 typed state 与 evidence gate，不是编排。
- **只形式化 evidence history**：接受为底座，不作为独立主线；并入 milestone 派生。
- **维持现状**：拒绝；goal 层仍是 operator 看到的最缺 typed 语义的一层。

## 7. 最小可用实现切片

一个纯派生模块 + 一个 fixture smoke + status markdown 一处展示。不改运行时行为、不新增存储字段、不影响 quota 与 settlement。先证明词汇可用，再谈消费者或强制语义。

## 8. 验证标准

- Fixture smoke 断言合成 goal payload 的 milestone/guard/next-transition 派生，包含负例（未达成 milestone、blocking guard 且无合法迁移）。
- 投影保持 public-safe：不含 raw evidence body、credentials、本地路径或私有 payload。
- `examples/docs-governance-smoke.py` 与对新增文件的 `loopx check` 通过。
- 现有 status/quota/settlement smoke 不变。

## 9. 开放问题

- Milestone 声明来源：优先用 goal 声明的 acceptance 字段，还是 evidence 派生标记？
- `lifecycle_phase` 用固定枚举（如 `starting / qualifying / waiting_owner / closing / closed`）还是派生标签？
- Milestone 语义何时、以及是否应该变成可执行的强制门？
