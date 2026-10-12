# 探索结果层

状态：受支持的 optional capability；harness 执行契约默认关闭。

## 概览

LoopX Explore 是面向长程探索目标（软件研究、安全攻击面测绘、领域调研）的受支持、默认关闭的可选能力。它把“到处看看”变成有界、可观测、有门控的流程，包含三根支柱：

1. **Explore Graph** —— append-only、public-safe 的证据拓扑（nodes / edges / findings）+ 有界投影、Mermaid 导出、canonical/executive 双视图展示。它回答：探索过什么、循环卡在哪里以及为什么、发现了什么。
2. **Explore Harness** —— 默认拒绝、只读的分支规划器（`todo-branch-plan`、`worker-branch-plan`），对下一步进行排序与打包（DSpark 风格 confidence/prefix/load、`adaptive-resilient` 与 `moe-router` 配置档、资源感知 portfolio），不 claim、不 launch、不 spend。
3. **组合面研究** —— worker lanes 并行探索多个面；episode 分组在变体之间共享昂贵的准备阶段；replay/counterfactual/trace 运行时对比路线；typed `supports` / `refutes` / `leads_to` 边把各面发现合并回同一张证据拓扑。
   当已闭环、有证据的多个面之间存在显式理由需要组合验证时，它们会成为 **composition gap**，并衍生 joint experiment 后继 todo（见下文“Composition Frontier”）。

**何时使用：** 探索目标已经超出 todo 列表能表达的范围——需要把“试过什么、什么有效、卡在哪里”读成一张图，并且下一步需要在多条并行方向上规划。

**它不是：** 不是常驻调度器、不是 worker launcher、不是流程引擎。以下所有内容都是分析或证据，除非 operator 通过正常 LoopX 生命周期执行它。

## 快速开始

开启门控、记录证据、投影与规划：

```bash
loopx configure-goal --goal-id <id> --explore-graph-enabled \
  --explore-harness-enabled --explore-harness-profile adaptive-resilient --execute

loopx explore node --goal-id <id> --title "攻击面 A" --status exploring
loopx explore edge --goal-id <id> --from A --to B --type leads_to
loopx explore finding --goal-id <id> --title "关键发现" --node A --status confirmed

loopx explore summary --goal-id <id>
loopx explore graph --goal-id <id> --graph-format mermaid --out explore.mmd
loopx explore worker-branch-plan --goal-id <id> --harness-profile adaptive-resilient --worker-width 3
```

两个门控相互独立且默认关闭（见下文“每 goal 独立 Opt-In 门控”）。以下是详细契约。
当已闭环、有证据的多个面之间存在显式理由需要组合验证时，下一次 `quota should-run` / turn packet 会投影 composition gap，并可衍生 joint experiment 后继 todo（见下文“Composition Frontier”）。

长程探索目标（例如通过 LoopX 研究某个外部软件领域的 Codex loop）产生的结果，operator 希望读成**拓扑**，而不是 agent 动作日志：探索过什么、循环卡在哪里以及为什么、发现了什么。

## 角色边界

一句话概括：

- **Explore 能力（本层）** 拥有结构化探索**证据**：紧凑、public-safe、append-only 的 node/edge/finding/blocked-frontier 日志，以及有界读模型投影。这是研究证据，不是展示产物——下游消费者首先是 vision checkpoint、replan、后继 todo 生成和 user gate，其次才是展示。因此日志位于 `loopx/capabilities/explore/`，而不是 `loopx/presentation/`。
- **展示（Presentation）** 把 public-safe 探索投影渲染成 operator 界面（Mermaid 图、飞书/Lark Base 行、卡片）。可复用展示实现位于 `loopx.extensions.lark.presentation.explore_results`；核心不保留 Lark 能力外观。兼容 CLI 委托在调用 provider 所属展示行为前必须显式激活扩展。
- **Value connectors** 仍是外部信号输入、权限和来源权威的边界。Lark 探索 sink 仅用于展示，绝不可与 connector 混为一谈。

## 状态契约

- 读取：LoopX runtime root 下 `goals/<goal-id>/explore-result-log.jsonl`（`loopx_explore_result_event_v0` 事件，由 `loopx explore node|edge|finding` 追加）。展示 sink 还可读取本地展示配置，如 `.loopx/lark-explore.json`。
- 写入：探索结果日志（append-only）、本地 board 配置（`loopx_lark_explore_local_config_v0`，含 result-id 到 Lark record-id 的映射，由展示 sink 维护），以及仅 `--execute` 时通过 `lark-cli` 写入的 Lark Base 行。
- 写所有者：operator 触发的 CLI。agent 只追加结果事件；只有显式 `--execute` 才会触碰共享 Lark 面。
- 迁移证明：每个同步 payload 列出它实际运行或将要运行的确切 `lark-cli` 命令、逐行 record ids，以及下次同步复用的刷新 record map。

## 结果事件模型

每行一个 JSONL 事件，`loopx_explore_result_event_v0`，三种：

| 种类 | 身份 | 用途 |
| --- | --- | --- |
| `node` | `--node-id`（或由 title 派生） | 被探索的问题、区域、假设、实验或工件。状态：`open`、`exploring`、`blocked`（必须 `--blocked-reason`）、`resolved`、`dead_end`。同 id 重录即更新。 |
| `edge` | 由 `from/type/to` 派生 | typed 关系：`subtopic_of`、`depends_on`、`answers`、`supports`、`refutes`、`leads_to`。 |
| `finding` | `--finding-id`（或由 title 派生） | 发现，可挂到节点。状态：`tentative`、`confirmed`、`refuted`。 |

事件在记录时做净化：紧凑文本上限、拒绝凭据类标记、evidence refs 必须是公共相对 ref 或不透明 id（例如 `ov:doc:lustre-survey`），绝不允许本地绝对路径。

## 投影与拓扑

`loopx explore summary` 把日志折叠成 `loopx_explore_result_projection_v0`：每个 node/edge/finding 的最新状态、状态计数、带原因的 blocked 列表、exploring frontier、parent/`subtopic_of` 拓扑树和 Mermaid 流程源。`loopx explore graph --graph-format mermaid|json [--out <file>]` 导出拓扑，供飞书文档、白板或任意 Mermaid 渲染器使用。

聚焦导出是有界证据视图，默认不是 executive 决策视图。它们保留面向机器的节点身份、边语义和祖先上下文，同时减少渲染的 canonical 拓扑量：

```bash
loopx explore graph \
  --goal-id <id> \
  --status exploring \
  --status blocked \
  --tag executive \
  --graph-format mermaid \
  --out explore-focused-evidence.mmd
```

重复 status 匹配任意一个请求状态，重复 tag 匹配任意一个精确请求 tag，status 组与 tag 组之间是 AND。命中的节点默认保留祖先以保留解释上下文；传 `--no-include-ancestors` 可只留叶子。过滤只影响图导出，不改变完整结果投影或 Lark 的 node/edge/finding 表。

面向 owner 的 executive 图是基于这份 canonical 证据的独立展示投影，不是第二份证据源。不要因为 canonical 导出更小就直接把它同步进 executive 白板。投影应把证据压缩成 operator 需要看到的决策角色：

- 决策契约与主指标；
- 基线与当前 incumbent；
- 决定性负向或已退休证据；
- 活跃工作或容量槽位；
- 重大风险与护栏；
- 终态决策门；
- 下一个决策或证据缺口。

默认基数策略是图增长。保留有实质意义的决策与证据节点及其关系；语义压缩指收紧标签、在保留 lineage 的前提下去掉真正的重复、按语义分组或链接子图。它不意味着因为图跨过“20 节点”之类的通用阈值就丢弃实质节点。稳定 canonical id 必须在重排和移动后依然存活，以便 owner 能追溯每个展示的决策和证据项到源头。

硬性 `max_nodes` / `max_edges` 上限只允许出现在显式 opt-in 的展示策略里。该策略必须写明范围、理由、溢出或链接子图行为、实质节点保留规则。没有这样的策略时，两个上限都视为无界；绝不能从渲染器便利、早期图大小或通用 executive 视图约定推断硬上限。

保持一个 fail-fast 守卫，拒绝与 canonical 导出产生意外同一性。同步前先用目标渲染器渲染，运行重叠与文本溢出检查，并目视检查实际预览。通过重排、更短标签、更大画布或更多语义子图修复可读性，而不是删除实质证据。同步后验证远端来源或 digest 与已验证投影一致。canonical JSON 与 Nodes/Edges/Findings 表在整个展示步骤中保持完整且权威。

`loopx explore presentation --goal-id <id>` 从一份 canonical 结果投影构建展示包。它始终包含完整的 `canonical` 视图和带源节点 lineage 的派生 `executive` 视图。两个视图携带相同的无时间戳 `source_digest` 和基于事件的 `source_revision`。executive 视图选择活跃与决策标记节点、代表性反证邻域、实质性一跳关系与祖先；它不独立存储事实。

展示包基于多个咨询信号推荐 `presentation_mode=canonical_only|dual_view`，而不是单一节点数阈值。当前 reason codes：`low_decision_density`、`excessive_terminal_branches`、`deep_decision_path`、`readability_check_failed`。静态图形形状可估算可读性风险（包括过度扁平的根拓扑）；调用方可额外提供重叠、文本溢出或异常画布扩张的渲染器观测。canonical 与 executive 视图都使用自上而下的证据时间线：稳定源顺序从顶部开始，有界 epoch 增加导航，后续证据向下扩展 board 而不是加宽第一层。每个原始 canonical 节点与边仍然存在。这些信号与布局选择只控制展示，绝不允许 canonical 截断。

## 普通工作结果写回

在 evidence 或 planning 模式，已绑定 Todo 的普通写回步骤通过能力 hook 提示
可选 `explore_result` 附件。把验证后的结构化结果放入现有
`--agent-vision-json` 的顶层 `explore_result` 字段，或使用
`--explore-result-json <result.json>`。捕获可复用证据是可选行为，不是结算义务；
普通工作无需附件，off 模式不增加附件提示或证据要求。

对于 **`hard_lease` 下仍开放的 Todo**，让任务租约保持有效，直到
`refresh-state` 与图/Todo 关联交付返回 `explore_result_delivery.ok=true` 后再释放。
提前释放会让附件预检在主写回提交前拒绝；重试前重新进入正常的
准入/claim/lease 路径。`hard_lease` 下已完成 Todo 仅保留增量关联证据的既有例外：原 owner
可使用保留的已释放租约 key 和当前版本，但不因此获得重新执行或其他编辑权限。
已成功交付的精确重放只做读回，无需新租约；未完成交付仍需适用的 claim/lease
证明。这些规则不会让捕获变成必做步骤，也不会自动获取 claim 或续租。

图或关联交付失败时，主写回可能已经提交；以返回的
`explore_result_delivery.retryable=true` 为准，重放**同一条** refresh 命令完成交付，
不要通过更改附件重写原 Turn。用 `loopx explore turn-context --goal-id <id>
--agent-id <agent>` 与 `loopx explore summary --goal-id <id>` 读回；读到证据不证明
模型已经采纳。Todo 持久关联不再受八条上限约束，增量写入与精确读取保留全部
有效且去重的节点 ID。每轮紧凑视图对每个分支只展示最多八条请求/未知 ID，
并给出明确省略数；`plan_command` 现在调用 `todo-branch-plan` 展开同一 Todo
审计，不再跳到不同的 worker 分组计划。带版本的结果分页
可读取全部作用域结论。展示预算不删除关联、不漏算负面证据风险，也不阻塞
合法写回。变化只涉及启用后的证据捕获及显式 Todo 关联编辑；普通工作与
能力关闭时的 hook 行为保持不变。完整附件与作用域规则见[英文契约](README.md#results-from-ordinary-work-writeback)。

## 可选 Todo 分支规划

`loopx explore todo-branch-plan` 是面向探索目标的窄 opt-in harness：一次尝试多个看起来都合理的下一个 todo。它使用 CPU 分支预测类比 + DSpark 启发调度器：对 open agent todos 排序，估计分支置信度与预期证据单位，选择置信度调度的验证前缀，选一个 `primary` 分支加安全的 `speculative` 分支，拒绝声明写作用域与已选分支重叠的分支。

关于 DSpark 引文（arXiv:2607.05147）的准确性说明：真实 DSpark 会在每个 per-step 置信度低于固定阈值的第一个位置截断半自回归 draft 块，并且只把 per-step 置信度累乘作为校准诊断。这里的 prefix-survival theta 模型（survival product × throughput 曲线）是 LoopX 特有的、面向**串行依赖** todo 链的扩展。绝不能用它来衡量独立并行 worker lanes 的规模——早期校准运行曾因此把 treatment arm 截到 5/10 lanes；worker 规划现在使用 `schedule_independent_lanes` 代替。

命令只读，并与 `worker-branch-plan` 一样受 per-goal opt-in 门控（见下文）：没有 `explore_harness.enabled=true` 时返回 disabled packet，`--width` 在自身上限之外还被 `max_children` 封顶。它不 claim todo、不 acquire lease、不 launch agent、不 spend quota、不改变 active state。它只输出预测 packet：

- 选中的分支、置信度、hazards 与 reason codes；
- 被排除的 `continuous_monitor` 诊断——仍可见，但绝不进入探索调度器或消耗分支宽度；
- 基线串行执行与 DSpark 风格选中前缀的 dry-run A/B 估计（`ab_result.estimated_speedup_vs_baseline`）；
- 供 operator 或注册 peer runner 显式执行的 `loopx todo claim` / `loopx task-lease acquire` 建议；
- 保持 packet 咨询性、而非替代 `quota should-run` 的安全边界。

advancement todo 可通过挂一个或多个显式 Explore node id 选择 typed 结果诊断：

```bash
loopx todo add --goal-id <id> --role agent --text "Evaluate the rejected route" \
  --task-class advancement_task --explore-result-node-ref node_rejected_route
```

`todo-branch-plan` 只解析这些显式链接。它的有界 `typed_evidence_audit` 报告链接节点的生命周期、finding 状态、相关 `supports`/`refutes` 边、未知 id、dead-end/refutation hazards。审计仅诊断（`score_delta=0`），不能 claim、lease、launch、写状态或 spend quota。未链接的 todo 保持原 planner 行为。修复过期链接：用另一个重复的 `--explore-result-node-ref` 替换，或用 `loopx todo update ... --clear-explore-result-node-refs` 全部移除。

没有声明写作用域的 todo 默认视为投机性读取或协调工作，因为许多探索任务只读。当控制器希望未知作用域折叠回单分支执行时，使用 `--no-allow-unscoped-parallel`。

作用域冲突只看可变 `required_write_scopes`。不要把共享基础 checkout 或已构建的不可变输入放进该字段，仅仅因为多个实验都会读它。用现有 public-safe 能力标签表达可复用输入，如 `shared_implementation:<name>` 或 `shared_artifact:<name>`，然后给每个实验自己的变体或 launch 输出作用域。这些 lane 可以并行。如果共享构建本身仍是可变的，把它的路径留在 `required_write_scopes` 中；planner 会正确串行化可能写入同一工件的 lane。

## 可选 Worker 分支规划

`loopx explore worker-branch-plan` 是同一实验的 worker-lane 版本。它不把一个分支当作一个 todo。一个 worker branch 是一条预测 lane，包含一小束 LoopX todos、一个目标切片、所需能力、写作用域、依赖提示、预期证据、置信度和建议的 claim/lease 命令。

共享 `shared_implementation:*` 或 `shared_artifact:*` 能力不会让 worker lanes 互斥。这支持一个共享实现/工件构建阶段，随后是写入独立变体或 launch 目录的 long/short 风格实验 lane 并行。共享输入在该执行波内必须是不可变的；进行中的共享构建仍是写作用域，因此仍是真实冲突。

`continuous_monitor` todos 是观测/控制面 lane，不是探索工作。planner 把它们放在 `rejected_worker_branches`，`selection_status=excluded_non_exploration_lane`，但绝不把它们与 advancement todos 打包，也不计入 `worker_width`。monitor 迁移可通过正常 todo 生命周期创建或解锁后继 advancement todo；该后继可参与下一次只读规划调用。

### 资源感知 Portfolio 规划

两个分支规划器都可以对声明了 `resource_lane:<key>` 能力的 advancement todos 应用独立容量上限。容量与当前占用是请求输入，不是持久化控制面状态：

```bash
loopx explore worker-branch-plan --goal-id <id> --worker-width 5 \
  --resource-capacity long_pool=2 --resource-usage long_pool=1 \
  --resource-capacity short_pool=3 --resource-usage short_pool=1
```

同样的可重复 flag 也适用于 `todo-branch-plan`；`--width` 或 `--worker-width` 仍是整体规划上限。示例中 packet 可能分配 1 个新 `long_pool` 槽和 2 个新 `short_pool` 槽。每个选中分支携带 `resource_lane` 与 `resource_assignment`，顶层 `resource_portfolio` 报告每个 lane 的 capacity、当前 usage、available、selected 与剩余槽位。

声明资源容量是显式 portfolio-fill 模式：请求的整体宽度成为选择上限，而不是旧的置信度前缀；现有分数、hazards 与 typed evidence 保持不变。可用槽位只说明候选有资格进入分析 packet，不代表候选值得执行。agent 仍必须在 launch 前应用 goal 的 evidence、serving-cost、quota、claim 与 lease 门。

当高排名候选因其依赖不在选中波内、写作用域与已选分支冲突或存在其他 planner hazard 而被拒绝时，选择继续扫描。同一资源 lane 中更靠后的安全候选可在同一次调用中回填被释放的预测槽位。`continuous_monitor` todos 保持仅诊断，即使携带 resource-lane 能力也不消耗资源槽。

资源输入可选。没有 `--resource-capacity` 时，无 lane 与 legacy todos 保留现有 width/scheduler 行为。资源感知模式下，无 tag todos 保持原来的无约束行为；带 tag lane 必须有匹配的声明容量。使用没有匹配容量的 usage 会 fail closed，以捕捉拼错的 lane key。

这仍是仅分析证据：`resource_portfolio.score_delta=0`、typed evidence 保持 `score_delta=0`、planner 只读。容量与占用不 claim todo、不 acquire lease、不 launch worker、不写状态、不授予 quota 权威。它们只约束预测 portfolio；执行仍进入下面描述的正常 LoopX 生命周期。

该命令只读、per-goal opt-in。它设计为**叠加在现有 LoopX harness 之上**，而不是并排或取代：

1. LoopX 提供 harness 输入：quota/status 上下文、open agent todo 投影、explore result 投影、所有权、能力、写作用域元数据。
2. opt-in planner 把 todos 分组为 worker-lane 候选，用 DSpark 风格 confidence/prefix/load 打分选择 worker branch 前缀。
3. 执行必须回到正常 LoopX 路径：`quota should-run`、`todo claim`、`task-lease acquire`、worker 执行、`explore node|edge|finding`、`refresh-state`、`quota spend-slot`。

因此 packet 对已 opt-in 的 goal 包含 `harness_compatibility` 与 `boundary` 字段：`replaces_loopx_runtime=false`、`launches_workers=false`、`claim_and_lease_are_suggested_only=true`；deny-by-default 的 disabled packet 携带 `boundary` 块和 opt-in `required_contract`。packet 可被控制器或 operator 用来决定启动哪些 worker，但不能自行 launch worker 或变更控制面。

### 每 Goal 独立 Opt-In 门控

Explore Graph 与 Explore Harness 是相互独立的可选能力。开启一个绝不开启另一个：

- `explore_graph.enabled` 控制持久图投影与任何已配置的展示 sink。每次成功的 material `refresh-state` 事务后，LoopX 折叠 canonical Explore 证据并运行已配置 sink。语义 digest 让未变更的 refresh 成为零写操作。已配置行 sink 只有在 row/result-id readback 验证投影后才算完成。失败同步或 readback 不推进其 digest，下次 material refresh 会重试。视觉 sink 还会预检确定性交付标记：已有标记对先前写做对账而不重复发布；有界 readback 超时在该 stage batch 停止后续调用并留下可重试 receipt，而不是盲目重复远端写。
- `spawn_policy.explore_harness.enabled` 只控制下面描述的只读分支规划器。它不创建、更新或发布图。

两个门控默认缺失/false。常见模式是 Graph on、Harness off：保持 operator 面向的拓扑最新，同时不改变工作的规划方式。

```yaml
# inside the registered goal entry
explore_graph:
  enabled: true

spawn_policy:
  explore_harness:
    enabled: false
```

通过增量配置命令而不是直接编辑 registry 来配置门控：

```bash
loopx configure-goal --goal-id <id> \
  --explore-graph-enabled \
  --no-explore-harness-enabled \
  --execute
```

用 `--no-explore-graph-enabled` 停止自动图工作。关闭门控保留已有证据与展示状态；只是阻止未来的自动投影与 sink 写入。

当单次运行可更新本地状态但无权写入任何已配置外部 sink 时，保持 graph enabled 并传 `refresh-state --suppress-external-sinks`。LoopX 仍更新 canonical 本地 Explore 投影，在 refresh packet 中报告抑制边界，并让 row/visual digest 不变，以便后续授权 refresh 可重试交付。这个 run 级边界不改变 goal 的 Graph 或 Harness opt-in 设置。

Graph-on 是 material 交付后置条件，不是尽力提醒。授权的 `refresh-state` 在已配置 sink 无法同步和 readback 时失败；调用方必须重试后才能声称交付。被抑制的运行可提交 canonical 本地状态，但其 packet 报告未满足、可重试的后置条件，并要求具体的授权同步后继。没有配置 sink 时，本地投影满足后置条件。该契约不启用 Explore Harness。

#### Explore Harness 规划门

两个 opt-in 规划器（`todo-branch-plan` 与 `worker-branch-plan`）都是 deny-by-default。门位于注册 goal 的 `spawn_policy`——这是 quota/status 管道投影进 `quota should-run` 的 `goal_boundary.orchestration` 的唯一可写来源。其他 registry key 一律不生效：第二个来源将是 quota 边界不可见的授权面。

```yaml
# inside the registered goal entry
spawn_policy:
  spawn_allowed: false    # "allowed" 是接受的别名
  max_children: 3
  explore_harness:
    enabled: false        # 默认：两个探索规划器都关闭；
                          # 必须是 boolean true —— 其它值 fail closed
    profile: generic      # 可选 pin；覆盖 CLI 请求的 profile
```

使用增量配置路径而不是编辑 registry：

```bash
loopx configure-goal \
  --goal-id <id> \
  --explore-harness-enabled \
  --explore-harness-profile adaptive-resilient \
  --execute
```

在 spawn 权限仍关闭时这仅是分析。用 `--no-explore-harness-enabled` 再次关闭门，或用 `--clear-explore-harness-profile` 让各 planner 请求自己的 profile。不带 `--execute` 的 preview 显示确切 orchestration delta，并保留无关的 `spawn_policy` keys。

Planning 模式的 `explore turn-context` 在既有 turn-start hook 中提供决策输入：
建议分支保留显式 Todo/节点关联的有界 `typed_evidence_audit`，同时返回最多三个
既有 exploring 前沿节点。先解析关联 finding，再裁剪近期历史，避免无关的新结果
遮掉旧反证。诊断不改变 planner 分数；未知关联可见，省略计数针对有界 audit，
不代表完整日志。通过 `todo update --goal-id <id> --agent-id <agent> --todo-id <todo>
--explore-result-node-ref <node>` 关联既有证据，仍遵守普通 update/lease 契约。
实验前据此解释路线和有区分力的 probe；条件变化或不确定性可以支持重试。
这些是决策指引，没有新增 adoption/writeback gate、自动 successor 或 spawn 权限。
读取上下文不证明模型已经采纳证据或取得效果提升；短视图不足时读取完整 planner/summary。

planner 把这个边界折叠进 packet 的 `orchestration_gate` 节，行为如下：

| 边界状态 | Planner 行为 |
| --- | --- |
| `enabled=false`（或 goal 未注册 / 无边界） | 显式 disabled packet，带 `required_contract`；不输出分支。 |
| `enabled=true`、`spawn_allowed=false` | 只读排序与 bundle 分析；所有 `suggested_commands` 列表被清空。 |
| `enabled=true`、`spawn_allowed=true`、`max_children>0` | 输出建议 claim/lease 命令，仍仅 dry-run。 |
| 任意 enabled 状态 | Lane 宽度（`--width` / `--worker-width`）在 planner 自身上限（`MAX_BRANCH_WIDTH` / `MAX_WORKER_LANES`）之外还被 `max_children` 封顶；绑定上限记录在 `orchestration_gate.width_cap_source`。 |

`spawn_allowed=true` 且 `max_children=0` 视为矛盾，降级为仅分析状态，而不是授予容量。

门是规划面的 defense-in-depth，不是运行时权威的替代：权限、quota、gates、claims、leases、spend 与状态投影无论门状态如何都归正常 LoopX 生命周期所有。`examples/explore-worker-plan-gate-smoke.py` 端到端覆盖两个 planner 的四种状态、`max_children` 上限与 CLI 默认关闭路径。

当实验是关于动态分支时使用 worker-lane planner：多个 Codex worker 探索不同路线，每条路线管理多个 todos，验证后的结果合并回 explore graph。较小的微内核场景（分支只是一个候选 todo）使用 `todo-branch-plan`。

## Composition Frontier（组合面实验衍生）

可通过 `loopx explore observe --goal-id <goal> --observation-json <file>` 记录
typed research evidence。[版本化协议](../../../docs/reference/protocols/research-observation-v0.zh-CN.md)
定义 closure basis、explicit binary candidate、input revision binding，以及
`explore summary` 中只读 `research_frontier` shadow；既有 Lark 节点 Summary
显示同一事实。此命令不授予 experiment、Todo、replan、quota 或 Goal acceptance
权限，M3 enforcement 尚未完成。

Harness 还会投影 **composition gaps**——显式的组合面 todo 衍生。当两个单独已覆盖的面之间存在有证据关联、需要放在一起验证时，LoopX 把这段未测试关系保留为 gap，而不是把每个面当作已完结。

组合实验是一个已存在的 open Explore `experiment` 节点，带有至少两条指向已闭环（`resolved` / `dead_end`）、有证据输入节点的 `depends_on` 出边。只有显式图边才合格；投影绝不推断任意节点对，因此运行时与 reviewer 面保持与已记录图线性相关。

`project_live_explore_composition_frontier` 在 `quota should-run` 与 turn packets 中把它折叠成 `loopx_explore_composition_frontier_v0`（当 `spawn_policy.explore_harness.enabled=true` 时）：

- `gaps[]`（`loopx_explore_composition_gap_v0`）：`gap_id`、`experiment_node_ref`、`input_node_refs`、状态 `pending|scheduled`、`required_outcome=joint_experiment_result`、`successor_summary`（“Run the bounded joint experiment: ...”），以及 `successor_binding`，其 `explore_result_node_refs` 指向实验节点。
- `selected_gap`：第一个 pending gap（pending 排在 scheduled 之前，再按输入数降序、按稳定 gap id 排序）；最多投影 3 个 gap（`MAX_PROJECTED_GAPS`）。
- Legacy 投影只考虑 open experiment；移除已关闭实验或把所有 gap 标为 scheduled 不证明 research closure。独立 typed research shadow 要求可归属 terminal evidence 与当前 input fingerprint；evidence-backed dismissal 和 shared write-time enforcement 仍为 RFC milestone。

gap 变成正常的可运行后继：一个绑定到实验节点的 todo（`--explore-result-node-ref <experiment-node>`），通过正常 LoopX 生命周期执行。概念契约见 [`research-exploration-control-plane-v0`](../../../docs/architecture/rfcs/research-exploration-control-plane-v0.zh-CN.md)。

显式创建一个：

```bash
loopx explore node --goal-id <id> --title "Combine A and B" --kind experiment --status open
loopx explore edge --goal-id <id> --from <experiment> --to A --type depends_on
loopx explore edge --goal-id <id> --from <experiment> --to B --type depends_on
```

一旦 A 与 B 为 `resolved` / `dead_end` 且带证据，下一次 quota/turn packet 就会投影 pending composition gap，并可衍生 joint experiment 后继 todo。

### Adaptive Resilient Harness 配置档

`adaptive-resilient` worker harness 配置档吸收长程探索战役的设计经验，而不复制单个实验的偶然控制。它不是任何一次校准运行的永久配置。它保留泛化良好的部分：

- 独立 lane 准入：`--worker-width` 是上限，planner 可少选 lane——但只能基于可审计原因（队列耗尽或实测干扰），每次拒绝记录在 `admission_audit`。并行 lane 的预期证据是加法；旧的跨 lane survival product 把独立 worker 进程当作串行投机链，结构性欠填宽度；
- value-first 分支打包：`--max-todos-per-branch` 是上限，不为看起来满而填充分支；
- lane 启动错峰作为 runner 指导，因为错峰降低相关基础设施压力；
- 对重复瞬时失败（如 provider 服务不可达）的 retry/backoff 与基础设施族冷却提示；
- 显式 A/B 元数据，便于未来把该配置档与 priority-order 基线对比。

它刻意不控制分段时长、不强制 N=10、不饱和每个可用分支，也不默认启用早期 coverage-floor 校准 arm。这些仍是 runner 或未来实验决策，不属于泛化 harness 设计。

Retry/backoff 与基础设施冷却是给外部 runner 的 planner 元数据；通用运行时不强制它们。运行时结果显式暴露该边界，而不是暗示选择配置档就激活隐藏重试循环。

```text
loopx explore worker-branch-plan \
  --goal-id <id> \
  --harness-profile adaptive-resilient \
  [--worker-width <ceiling>] \
  [--max-todos-per-branch <ceiling>]
```

需要同样的 no-forced-fill 行为但不带其余配置档元数据时，显式使用 `--branch-fill-policy value-first`；旧 compact 分组行为用 `bundle-by-affinity`。

### MoE Router Harness 配置档

`moe-router` 配置档把 worker-lane 规划当作固定 worker 上限下的 MoE 式路由：task families（affinity keys，如 `scope:artifacts/<task>`）是 experts，todos 是路由 token，lanes 只是 serving 槽位。它在 `adaptive-resilient` 之上扩展了一个跨 epoch 的学习路由层，通过 `--router-state` 输入：

- **Router state**（`loopx.capabilities.explore.router_state`，schema `loopx_explore_router_state_v0`）：每个 family 的 raw value rate EMA（刻意不做 novelty 折扣，让估计器测量环境而不是 router 自己的重跑策略）、probe duration、acceptance rate、infra failures，以及全局 first-seen observation-key ledger，为每个 family 提供 novelty 预测。runner 拥有持久化，并在 epoch 边界调用 `observe_epoch` / `advance_epoch`——与现有 infra cooldown 同频。
- **Routing score 与 value bookkeeping 分离**（DeepSeek-V3 aux-loss-free 不变量）：每个分支携带 `routing_score = static score x (1 + UCB + coverage bonus + bias - infra penalty)`，仅用于排序；`calibrated_confidence`（× family accept rate）与 `novelty_adjusted_evidence_units`（× 预测 novelty）用于准入并保持无偏。bias 是每个 family 的标量，由 coverage/novelty debt 与 surplus 以 +/-gamma 更新——不是负载均衡，负载均衡在此没有内在价值——带衰减与防 windup 钳制。
- **Bundle length** 是忠实的 DSpark 类比（arXiv:2607.05147）：lane 的串行 todo bundle 是 draft block，在第一个校准接受置信度低于 `bundle_confidence_threshold` 的 todo 处截断（`confident-prefix` fill policy）。wall-clock straggler guard（`bundle_straggler_factor` × 实测 median probe duration）封顶串行尾部；它只绑定实测时长，因此 cold-start 默认不会悄悄把所有 bundle 压成 1。
- **负载校准**：通过 `--load-profile` 传入上一 epoch 观测到的 `{parallel_wall_minutes, max_branch_minutes, branch_count}`，并用 `calibrate_load_factor` 给 lane 准入定价实测干扰，而不是硬编码 0.2 先验。
- **机会主义扩张**：校准显示 `moe-router` 活跃 lane 效率更好但浪费 worker 槽后，配置档保留 theta-peak 核心 lanes，再按利用率下限准入额外 positive-yield lanes。这不是饱和填充：每条额外 lane 必须通过可审计的独立 lane 价值下限，拒绝留在 `admission_audit`。

```text
loopx explore worker-branch-plan \
  --goal-id <id> \
  --harness-profile moe-router \
  --worker-width <ceiling> \
  [--router-state <router_state.json>] \
  [--load-profile <observed_profile.json>]
```

不传 `--router-state` 时该配置档仍可规划（router 禁用、冷静态打分）；把 state 传给非 router 配置档会被忽略，这保持 `adaptive-resilient` 作为 B-min 消融 arm 干净。

## 可恢复执行 Episode

budget-arm 运行时为共享昂贵 setup 前缀的实验提供可选、软件无关的执行 seam。一个 seed 及其计划变体组成一个 **episode group**：adapter 准备一次基础状态，然后从同一状态执行每个基线或变体后缀。LoopX 拥有分组、观测记账与 router 反馈；adapter 拥有所有应用特定事实，包括如何恢复隔离。

adapter 通过实现全部三个方法 opt-in：

- `prepare_episode_group(seed_item, episode_items, **context)` 返回带内存 `handle` 的 dict、一个无后缀的 `prefix_record`，以及可选的不透明、public-safe `checkpoint_ref`；
- `execute_episode(handle, item, **context)` 按需恢复或克隆已准备状态，只返回该 item 后缀产生的观测；
- `release_episode_group(handle, **context)` 释放 adapter 拥有的状态，并在任何 handle 越过边界后都会调用，包括后缀失败（若 prepare 返回缺少 `handle` 的畸形 dict，adapter 保留所有权且不会收到 release 调用）。

legacy `execute` 方法对 episode adapters 保持可选：仅当 `prepare_episode_group` 返回 `None` 时 runtime 才咨询它。

组内后缀当前是**串行**的，但 concurrency keys 互斥的不同组可在同一 adapter 实例上的不同 worker 并发运行。三个 episode 方法因此必须跨组线程安全，且并发活动组绝不能别名可变执行状态。顺序 handle 复用、不可变共享 handle 与 adapter 管理的共享资源在隔离与生命周期安全时仍有效。每次后缀调用前，包括基线后缀，adapter 必须恢复或克隆同一已准备状态；一个后缀的改动绝不能泄漏进下一个后缀。因为组把后缀串行进一个 worker lane，epoch 的并行度受组数约束：prepare 便宜的 adapter（例如单 item 无变体的组）应在该组返回 `None` 以保留 legacy 路径，避免无谓的 prepare/release。

核心没有 VM、GUI、browser、process 或工业软件类型。对于黑盒桌面应用，adapter 可用 VM 快照、应用重启加确定性动作回放、或隔离 profile 副本实现 handle。其他探索领域可用 API sandbox、文件系统快照、模拟器状态或任何其他可恢复机制，无需改变 harness runtime。

`prepare_episode_group` 可在产生副作用前返回 `None` 以请求该组 legacy、fresh `execute` 调用。这些 fallback 调用在已准入组内保持串行。prepare 异常绝不静默 fallback，因为环境可能已部分改变；它由配置的 item failure policy 处理。三方法部分实现也 fail closed。如果 prepare 在产生副作用后 raise，清理仍是 adapter 的责任，因为没有合法 handle 越过边界；LoopX 只保证不会在该不确定状态下静默运行 fresh items。

分组在 fatal-mode 规划前验证 seed 身份，并在任何 episode 生命周期调用前验证编译后的 epoch。seed 与 variant id 必须非空且全局无歧义，每个 variant 的 `seed_item_id` 必须命名该 epoch 中的合法 seed。`list_seed_items` 与（对 variant 检查）`compile_variant` 必须在对应验证前运行。在 `fatal` policy 下，结构预检在任何 prepare、suffix execute 或 release 调用前 raise `ValueError`。在默认 `record` policy 下，每个畸形 item 变成一条结构化错误记录，`episode_stage="group_validation"`，而每个合法组仍运行。因为 record 模式完成 epoch，其 checkpoint 记录 catalog 消费，resume 不会把同一畸形 spec 重新挑进崩溃循环。

失败记录如实说明失败发生在哪个 stage。组成功后的清理失败不改写历史：prefix record 保留自己的 `execution_status` 与 `accepted` flag，release 失败携带在 `episode_release_error` 加 `episode_stage="release"`（传播 `retryable_infra_error`）。fatal policy 下 suffix 错误与 release 错误同时发生时，suffix 错误传播，清理失败作为其 `__cause__` 链上——两个失败都不被吞。

记录只携带通用执行 lineage：`execution_group_id`、`record_kind=shared_prefix|episode_suffix|standalone`、`seed_item_id`、`prefix_reused`、可选 `checkpoint_ref`。novelty ledger 把共享前缀记一次、每个后缀各记一次。Router 反馈把一个组的 prefix 与 suffixes 折成一个 probe，因此兄弟分支不会伪装成独立 family runs。折叠 probe 携带整数 `accepted_count` 与 `attempt_count`；router 对同 family 组的这些计数求和，因此 acceptance sample 按 suffix 数加权，而不是给小组与小组等权。

运行时结果以两个显式复用视图报告 shared-prefix、suffix 与 standalone 算力。`avoided_recompute_minutes = prefix_minutes * (attempted_episode_count - 1)` 度量结构前缀复用（含后来失败的 attempted suffix）。`successful_avoided_recompute_minutes = prefix_minutes * (successful_episode_count - 1)` 是保守结果视图，排除 `adapter_error` suffixes；它们通过 `episode_error_count` 仍可见。

对比 episode arm 与 standalone arm 时有两条指标注意事项：`novel_value` 总量与 AUC 可比（first-seen ledger 在两种模式同样去重），但 `raw_value_total` 不可比——standalone arm 会在每条 item 记录里重复报告基态观测，episode arm 每组分一次。因为组串行化 suffixes，`requested_worker_minutes` 在组数少于 workers 时会把 scheduler 结构性无法参与的 workers 计费；`execution_unit_count` 记录每个 epoch 的真实派发宽度。`effective_compute_minutes` 汇总报告 prefix、suffix 与 standalone 时长但排除 release/cleanup；端到端计时用 `epoch_wall_minutes` 与 arm `elapsed_minutes`（包含 lifecycle 与 scheduler 开销）。标准 `aggregate_arms` 比较暴露每个 arm 的 `execution_metrics` 与 value metrics。

这个 adapter checkpoint 与下面的 harness restart manifest 刻意不同。adapter handle 是活执行状态，LoopX 绝不序列化它；epoch 边界 manifest 在进程重启后恢复 scheduler 与记账状态。

### 运行时重启与 Item 失败

`run_budget_arm` 可写原子 epoch 边界 checkpoint manifest。重启 opt-in：以 `resumable=True`（或显式 `checkpoint_path`）启动 arm，再传 `resume=True` 恢复已完成的 epochs、novelty keys、router state、catalog 消费、累计指标、coverage 时间戳与下一个 epoch。缺失、损坏或运行时不兼容的 manifest 以具体 `ValueError` fail closed；松散的滚动进度文件仅用于观测，绝不是重启权威。

Adapter 异常默认 `record` item failure policy：失败 item 变成零值结构化观测，独立队列 lanes 继续运行。Concurrency keys 在每条路径都释放。传 `item_failure_policy="fatal"`（或设置 adapter 的 `item_failure_policy` 属性为 `"fatal"`）保留异常传播。这些策略隔离工作项失败；它们不实现 planner profile 的 retry/backoff 或 cooldown 指导。

## 展示 Sink：Lark 映射

| LoopX 概念 | Lark 表面 |
| --- | --- |
| node | `Nodes` 表行，键为 `LoopX Result ID`；`Status=blocked` 行带 `Blocked Reason` |
| edge | `Edges` 表行，键为 `LoopX Result ID`；`From Node Link` / `To Node Link` 是指向 `Nodes` 的 linked-record 单元格，因此 Base 数据模型本身承载拓扑 |
| finding | `Findings` 表行，键为 `LoopX Result ID`；最新事件胜出 |
| row lineage | `Row Lifecycle`、`Supersedes`、`Superseded By`、`Source ID` 列 |
| dashboard card | 来自同一投影的免传输交互卡片内容 |

记录身份遵循 Lark Kanban adapter 契约：行按 `LoopX Goal ID` + `LoopX Result ID` 列匹配，记在本地配置的 `result_records`，执行 upsert 前从所有 goal 过滤的远端页面重建映射。执行同步对比 canonical 值与远端行，跳过未变记录。新建记录 id 立即持久化，因此被中断的大图同步可恢复，不会重建已交付行。

对 issue-fix 领域，默认 `lark-kanban sync-loopx-todos` 调用也会把 material domain-state、todo 与 rollout 迁移投影进该结果层。它仅在无时间戳语义图 digest 与上次成功 sink digest 不同时才调用远端 Explore sync。这让图持续最新而不在未变 CI/review polls 上浪费写入。它只使用结果层，不启用或依赖 Explore Harness worker 编排。

可选 owner 面向的 stage 文档单独配置，因为 linked Base 行与渲染图是不同的交付回执。用 `explore feishu-visual-configure` 配置 Docx 与其第一个白板；Docx 可以是同一 Base 内的根级资源，让图与 Kanban 共享一个 operator 入口。每个有界 Evidence Stage 拥有一个文档节与一个独立白板。sink 有 Docx token 时自动创建缺失节与空白白板。Stage 容量可配置为 10-20 个节点，默认 14。完整 Nodes、Edges 与 Findings 始终保留在 canonical Base。

`board_style` 是一等布局契约，与控制证据选择的 `projection_mode` 独立。两种样式：

| Board style | 最佳场景 | 渲染行为 |
| --- | --- | --- |
| `auto_flow` | 通用或单 lane Explore 图 | Mermaid 选择图布局，LoopX 保留 stage 顺序、lanes、statuses 与真实有向边 |
| `semantic_lane_columns` | 有意义并行 lanes 的 operator board，如 PR issue-fix 与能力工作 | LoopX 输出确定性 SVG 列，保持每个 lane 自上而下，并绘制 stage 内真实有向边 |

渲染器（`mermaid` 或 `stage_svg`）是派生自 `board_style` 的实现细节。只存 `renderer=mermaid` 的旧本地配置仍按 `auto_flow` 读取。

首次配置省略 `--board-style` 时，新视觉角色默认 `auto_flow`。后续对同一角色省略时保留已存样式及其已验证渲染器。这让 Docx 或 Evidence Stage token 维护变成 patch 操作，而不是隐式样式重置。仅在有意切换布局时传显式 `--board-style`。

material sync 独立 checkpoint `canonical_rows_semantic_digest` 与 `visual_semantic_digest`。Base 行成功后白板发布失败时，下次运行只重试视觉 sink，而不是重写未变行。因此 `status=synced` 表示每个已配置 sink 都完成；调用方可分别检查 `canonical_rows_status` 与 `visual_status`。

默认 `canonical_filtered` 投影遵守配置的 status/tag 过滤器。项目用工作节点或其祖先上的 `lane-<name>` tags 定义 lanes。每个 stage 按 lane 分组节点，并保持 board 内真实有向关系可见，包括跨 lane 边。issue-fix 投影因此同时显示 PR 交付 lane 与 LoopX 能力 lane；单 lane 项目（如 zjxmt）渲染一个 lane 而不生成合成空结构。这只改变展示，绝不改变证据状态。

同一来源双视图按角色配置 stage 文档。对已创建的 stage boards 重复 `--stage-whiteboard-token`；`--docx-token` 设置时缺失 boards 在匹配的 `Evidence Stage NN` 节下自动创建：

```bash
loopx explore feishu-visual-configure \
  --view-role canonical \
  --projection-mode canonical_full \
  --whiteboard-token <canonical-token> \
  --docx-token <canonical-doc-token> \
  --stage-capacity 14 \
  --execute
loopx explore feishu-visual-configure \
  --view-role executive \
  --projection-mode executive_auto \
  --whiteboard-token <executive-token> \
  --docx-token <executive-doc-token> \
  --stage-capacity 14 \
  --board-style semantic_lane_columns \
  --execute
```

`feishu-sync` 随后在一次本地投影步骤中生成两个视图，每个 stage 发布一个白板。它始终发布 canonical 角色，并在 bundle 推荐 `dual_view` 时发布 executive 角色。派生视图的 source revision 或 digest 与当前 canonical 投影不同时，会在任何白板命令运行前被拒绝。legacy grid/SVG renderer 配置以显式迁移消息失败，而不是悄悄发布错误视觉形式。

任一推荐角色缺失于已配置 sinks 时视觉同步不满足。其顶层回执保持 `published=false`，列出缺失角色，返回可重试配置动作；成功的 per-role 诊断仍可检查，但不能让整体 sink 显得最新或推进交付 checkpoint。

可执行同步在直接 `feishu-sync` 命令与自动 material refresh 之间按本地 board config 单飞。重叠进程在 row、visual 或 checkpoint 写入前以 `status=sync_busy`、`retryable=true`、`external_write_performed=false` 失败；dry-run 保持并发，因为它们不改变 sink。在活动进程退出后重试，而不是允许两次 upsert 扫描创建重复 Result IDs 或互相覆盖本地 checkpoint 快照。

锁在同一执行上下文内可重入，因此已拥有 board 锁的 batch 可调用直接命令或自动 material sync，而不会把自己拒绝为 `sync_busy`。复用绝不跨执行上下文或进程；独立 writer 仍快速失败。

`From Node` / `To Node` 文本列是给自动化与 review 的稳定公共 id，linked-record 列是飞书原生图基底。Base 插件、关系感知视图或飞书 dashboard 组件可直接读取这些链接；LoopX 绝不能把图降级成仅截图工件。

该 sink 是展示边界，不是 value connector。Value connectors 拥有外部信号输入、权限与来源权威；展示 sink 为 operator 渲染 public-safe 探索投影。

## CLI 表面

```text
loopx explore schema
loopx explore node --goal-id <id> --title <t> [--node-id ...] [--status ...] [--blocked-reason ...] [--parent ...]
loopx explore edge --goal-id <id> --from <node> --to <node> --type <edge-type>
loopx explore finding --goal-id <id> --title <t> [--node ...] [--status ...] [--confidence ...]
loopx explore summary --goal-id <id>
loopx explore presentation --goal-id <id>
loopx explore graph --goal-id <id> [--graph-format mermaid|json] [--out <file>]
loopx explore todo-branch-plan --goal-id <id> [--agent-id <agent>] [--width 3]
loopx explore worker-branch-plan --goal-id <id> [--agent-id <agent>] [--harness-profile generic|adaptive-resilient|moe-router] [--worker-width 3] [--max-todos-per-branch 3] [--router-state <file>] [--load-profile <file>]
loopx explore feishu-setup [--base-url ...] [--execute]
loopx explore feishu-visual-configure [--whiteboard-token <token>] --docx-token <token> [--stage-whiteboard-token <token> ...] [--stage-capacity 10..20] [--board-style auto_flow|semantic_lane_columns] [--view-role canonical|executive] [--projection-mode canonical_filtered|issue_fix_two_lane|canonical_full|executive_auto] [--tag <tag>] [--status <status>] [--execute]
loopx explore feishu-sync --goal-id <id> [--sink-visibility owner-only|shared] [--execute]
loopx explore feishu-card --goal-id <id> [--card-file <file>] [--message-id om_...]
```

`feishu-setup` 与 `feishu-sync` 默认 dry-run，除非 `--execute`；dry-run payload 含完整命令计划供 review。

## Review 边界

行与卡片刻意排除 raw agent transcripts、worker commands、credentials 与本地绝对路径。证据位于紧凑公共 ref 之后；私有材料本身留在 goal 的正常本地状态或 memory backend。`--sink-visibility shared` 在行离开机器前还会通过 shared Kanban 脱敏规则脱敏私有链接与外部 id。卡片内容仅构建：实际发送或更新 Lark 消息是获批 gateway（bot 或 lark-cli）在 operator 允许写后的事。

## 验证

```bash
python3 examples/explore-result-layer-smoke.py
python3 examples/issue-fix-explore-projection-smoke.py
python3 examples/explore-harness-runtime-resume-smoke.py
python3 -m pytest -q \
  tests/test_explore_episode_runtime.py \
  tests/test_explore_router_acceptance.py
```

smoke 证明投影契约（折叠、blocked reasons、树、Mermaid）、记录时路径拒绝、dry-run 默认、分页发现、零写幂等 resync、单行漂移修复、嵌套 create-receipt 处理、shared 可见性脱敏、免传输卡片内容、opt-in todo branch-plan packet、adaptive resilient worker harness 配置档，以及针对临时 registry 的 CLI 表面，无需 live Lark credentials。它还证明 worker-lane router 契约：请求宽度不再被静默压到 worker 上限以下、独立 lane 准入下空闲 lanes 是队列耗尽（不是上限）、routing bias 在不动 value bookkeeping 的情况下重排 lanes、confident-prefix bundles 在校准阈值截断并对 reject-heavy families 坍缩、router-state novelty ledger 跨 epochs 去重而 coverage debt 累积 bias、观测负载配置档通过 CLI flags 校准准入。

runtime smoke 与聚焦 pytest 模块还覆盖：可恢复 prefix 复用、prefix/suffix novelty 与双复用记账、按 suffix 数加权的 router acceptance、显式 legacy fallback、重启兼容、recorded 与 fatal 失败下的清理、无可变状态别名的并发组、fatal 结构预检、record-mode 畸形 item 隔离与 resume 连续性、聚合指标投影，以及无 legacy `execute` 方法的 episode-only adapters。
