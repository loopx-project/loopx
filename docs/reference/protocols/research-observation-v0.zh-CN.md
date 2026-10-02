# Research observation v0

Explore 拥有这一可选证据契约。第一个写入入口是 `loopx explore observe`；
`loopx explore summary` 与现有 Lark Explore 节点摘要显示同一派生事实。
本批交付 M1 证据基础和 M2 **只读 shadow**。M3 开发边界增加可选执行 attribution
与 explicit-only live replan 门禁，以及 legacy/native File/SQLite Todo closeout
校验、来源限定的义务退役、lease-fenced 恢复和共享 status/Explore 展示。
Maintainer review 集成与现场模型/科研、远端 Lark qualification 仍待完成。

## 录入与读回

先通过现有 `explore node` 创建 public-safe 输入节点，再将 JSON envelope
录入已有节点：

```sh
loopx explore observe --goal-id research-demo --observation-json observation.json
loopx explore summary --goal-id research-demo --format json
```

```json
{
  "schema_version": "typed_research_observation_v0",
  "explore_node_id": "node-a",
  "progress": {
    "schema_version": "typed_progress_observation_v0",
    "work_item_id": "todo-a",
    "result_class": "exploration_exhausted",
    "coverage_scope_id": "scope-a",
    "coverage_complete": true,
    "evidence_ids": ["ev-a"]
  },
  "closure_basis": {
    "schema_version": "research_closure_basis_v0",
    "disposition": "bounded",
    "constraints": [{"kind": "invariant", "id": "boundary", "role": "decisive"}],
    "evidence_ids": ["ev-a"]
  },
  "composition_candidates": [{
    "target_node_id": "node-b",
    "basis": "explicit",
    "interaction_kind": "state_interference",
    "evidence_ids": ["ev-a", "ev-b"]
  }]
}
```

录入 candidate 前，同一 Goal 的 `node-b` 必须已存在，并通过节点 evidence 或
research observation 归属 `ev-b`。成为 pending gap 时，两个输入必须当前为
`resolved` 或 `dead_end`，并各有带覆盖证据的 terminal observation。
节点关闭状态、finding 数量、ACK 或一次读取本身不能建立这一证据。

## Wire 与兼容

Envelope 组合既有 generic progress codec，要求 `work_item_id`，不修改通用
progress 语义。未知 research 字段拒绝。节点/证据标识是 1–128 字符的 opaque
token：字母或数字开头，仅含字母、数字、`.`、`_`、`:`、`-`。不得放入原始文本、
本地路径、凭据或 source body；同时复用 shared public-safety validator。

`closure_basis` 使用 `research_closure_basis_v0`。Disposition 为 `bounded`、
`exhausted`、`no_followup`、`blocked`。Constraints 保留路径顺序，但匹配使用精确
`(kind, id)` 身份。Kind 为 `stage`、`decision`、`invariant`、`dependency`、
`resource`、`policy`；role 为 `decisive` 或 `supporting`。重复身份拒绝。
Constraint/evidence id 各至多十二项。Closure evidence 必须是本 observation
progress evidence 的非空子集。

`exploration_exhausted` 和 `no_followup` 要求完整 coverage、scope、evidence，以及
至少一个 decisive constraint；blocked disposition 不得断言 terminal coverage。
`blocked`、`unchanged`、`advanced` 可以记录，但不能建立 terminal composition
input eligibility。

Candidate 必须 `basis=explicit`，引用不同的已知 target，且 evidence 归属于两个
输入。Interaction kind 为 `shared_constraint`、`producer_consumer`、
`state_interference`、`order_dependency`、`resource_coupling`、`unknown_interaction`。
每次 observation 至多三项，超出拒绝，不截断。Candidate 身份是 Goal 与排序后的
二元输入集合摘要；反向声明合并，共同 constraint 本身不产生新配对。

接受的 envelope 获得内容 fingerprint，作为 append-only 节点 revision 的可选
`research_observation` 存储。没有该字段的旧 event 保持 projection 形状，无需调用
research runtime。严格校验节点字段的旧 reader 必须升级后才能读取含新 envelope
的日志。没有新策略时，既有 `#3173` composition/quota 行为不变；此命令不启用 research policy、
scheduler、claim、lease、quota、generic settlement 或 Goal acceptance。

## 结果、失效与回放

二元实验复用 `experiment` 节点与恰好两条出向 `depends_on` 边。录入前，从当前
`research_frontier.gaps[]` card 取出 `input_observations`，包含在 experiment
envelope 中；每项为 `node_id` 与 `fingerprint`。Writer 要求精确匹配当前输入
observation。具有证据、terminal 结果和匹配 lineage 的实验使 shadow gap 成为
`observed`；结论正负不影响资格。

Shadow 状态为 `pending`、`ineligible`、`observed`。活跃实验身份会显示，但安排实验
不能证明已有结果。无类型实验关闭、不同输入集合的结果、过期 input fingerprint
均不能关闭 gap。通过 `explore node` 更新输入使当前 observation 失效，须录入新
证据才能重新获得资格；历史 explicit claim 保留为 ineligible candidate。

精确 observation 回放返回 `replayed=true`、`written=false`，即使输入已经变化也不
重新绑定或追加 revision。新写入在既有日志锁内校验 attribution 并追加；现有 node
writer 和 batch writer 同样校验 typed envelope 的 attribution。

Cold projection 返回总数和至多三张 card，并提供 `projected_count`、`omitted_count`。
不声明排序质量，不把完整 candidate 列表加入 quota packet。遗漏项可从 canonical
node observation 检查。Cold card 不选择 obligation；下述 live policy 使用完整
内部候选集合，显示截断不能抹去 enforceable gap。

## 执行 attribution

实验 envelope 可额外包含 `execution_lineage`：

```json
{
  "schema_version": "research_execution_lineage_v0",
  "goal_id": "research-demo",
  "gap_id": "research-composition-0123456789abcdef",
  "replan_obligation_id": "replan-0123456789abcdef",
  "successor_todo_id": "todo_joint",
  "agent_id": "research-agent"
}
```

使用当前工作契约提供的真实 gap、obligation 和 Todo 身份；示例 token 不产生
义务。将该对象放入 experiment envelope，并用同一 actor 发出
`explore observe --agent-id`。`progress.work_item_id` 必须等于
`successor_todo_id`。源 registry 必须包含该 Goal。Writer 通过既有 Todo reader
读取精确任务，包括已配置的 promoted canonical authority，不接受调用者自报的
Todo 快照。

新写入要求当前可执行、归属同一 Agent 的 `advancement_task`，其
`action_kind=joint_probe`、`replan_obligation_id` 精确匹配，且
`explore_result_node_refs` 仅包含本实验。声明的 `target_key` 也必须等于实验 id。
延期、阻塞、被既有 acceptance guard 拒绝、归档、executor 被排除、其他 Agent
或无关任务均不能提供执行 attribution。实验必须对应这个 pending 二元 gap，
精确匹配当前输入 observation。Unchanged/read/ACK 或无 evidence 的结果被拒绝。

Fingerprint 包含该对象、research schema、实验身份、输入 fingerprint 和 generic
progress/evidence。Node 和 batch writer 拒绝新的 execution-lineage 写入，须使用
`explore observe` 的任务读取路径。精确历史回放始终不写入，即使任务或输入已经
改变，也不刷新当前任务权限。显示 card 的截断不限制内部 attribution 候选集合。

这是 Explore 日志锁内的任务快照 attribution 校验，不是原子的任务 lease/effect
授权，也不是 Todo/Goal 完成收据。后续 shared settlement 必须独立校验当前权限和
研究证据。不带 `execution_lineage` 的 envelope 保持诊断行为，不能经读取或回放
升级为执行 lineage。下述 live replan 门禁采用这些事实；相同 typed rule
也控制当前 legacy/native File/SQLite Todo closeout。

## Explicit-only live replan 门禁（M3 开发中）

通过既有 Goal 配置 owner 预览、应用策略：

```sh
loopx configure-goal --goal-id research-demo --explore-harness-enabled \
  --explore-composition-mode explicit_only --explore-composition-scope-id joint-scope
loopx configure-goal --goal-id research-demo --explore-harness-enabled \
  --explore-composition-mode explicit_only --explore-composition-scope-id joint-scope --execute
loopx quota should-run --goal-id research-demo --agent-id research-agent --turn-instance-id research-turn
```

Goal 能力编辑器通过既有 revision-checked preview/apply API 展示相同的启用、策略
与范围字段。Harness 启用与 `composition_mode=explicit_only` 必须同时满足。
不含私有内容的 `composition_scope_id` 指定实验覆盖范围；不替代 Goal vision，
不授予 claim、lease、effect、provider 或外部执行权限。

Quota 与 `refresh-state` 读取同一 live Explore/Todo frontier。Compact capability
guard 将所选 gap 与 input/policy revision 固定在原 Turn；既有 common replan
owner 计算 obligation id。User/handoff gate、可执行工作、vision acceptance 和
普通 succession/review duty 保持更高优先级，之后 capability gap 才进入 monitor fallback。

仅当前同一 Agent、精确 obligation、单个当前二元实验的可执行 `joint_probe`
successor 能抑制重复规划。`todo add --replan-obligation-id` 拒绝无关或延期的
successor。安排工作不等于 gap 已 observed。Terminal 实验结果须包含 execution
lineage、当前 input fingerprint 和配置的 coverage scope，才能成为 live observed
evidence。完整 generic progress 与 research fingerprint 绑定 writeback；丢失语义、
无关 progress、read/ACK、过期输入及回放不能解除所选义务。

此边界支持可执行实验 successor、其 typed observed result、有证据的 candidate
dismissal 或带 typed resume condition 的新精确 blocker。科学结论为负也是有效
证据。Generic blocked/terminal claim 不证明研究 closure。当前 legacy/native
File/SQLite closeout 需要该任务的精确 terminal 实验或 dismissal 证据；失效的已
准入 duty 使用下述 lifecycle retirement。Todo done 始终不证明 Goal closure。

Observation 可含 schema 为 `research_composition_resolution_v0` 的
`composition_resolution`。它需要 execution lineage 和非空 `evidence_ids`，
且证据必须归属于同一 progress observation：

- `disposition=dismissed` 要求 coverage-backed `no_followup`、匹配的
  `no_followup` closure basis、`dead_end` 实验节点与 typed `basis`
  （`duplicate`、`invalid`、`unsafe` 或 `outside_scope`）。它计为 `dismissed`，
  不计为 observed experiment。精确证据允许正常 Todo completion 或 supersession，
  不暗示实验已运行。
- `disposition=deferred` 要求 `blocked` progress 与 canonical `blocker_id`，
  不得断言完整 terminal coverage。实验节点为 blocked；当前实验 Todo 为
  blocked/deferred，声明 `resume_when=todo_done:<blocker_id>`；同一 Agent 的
  活动 blocker Todo 通过 `unblocks_todo_id` 指向实验 Todo。通用 Todo resume
  owner 必须证明该前置任务仍 pending。缺失、无关、归档或已解决的 blocker
  不能压制 gap；已在 writeback 中使用过的 blocker 不能再作为新进展。

这些是调用者声明的证据 disposition，不是独立科学验证或执行许可。前置任务
解决后 gap 重新 pending；重新打开任务和实验仅使其 scheduled，不是 observed。
Native hard-lease 调用者必须先释放 active lease，既有 blocked lifecycle 才接受
typed wait。恢复清除 wait 后仍需新的执行 lease；任意 deferred successor 仍被拒绝。

从 canonical experiment observation 写回 replan-bound Turn 时，用
`--progress-work-item-id` 指定其精确源 Todo，并重述完整 generic progress 字段。
它不把 settlement 从原 obligation 重新绑定到其他工作。共享门禁仍校验真实
Todo、当前输入、scope、evidence 与 fingerprint；修改或不完整 observation 被拒绝。
新 concrete blocker 是显式 replan progress exit，不使用 Todo-bound
`outcome_gap` 无支出 closeout 契约。

Native 事务在 actor/lease admission 后、caller validation effect 前，使用实际
provider Todo 校验能力资格。固定的 source-selected Python adapter 持有 canonical
Explore 日志锁直到 native CAS 返回；它只读取图，observation/task/input lineage
是否合法由 TypeScript 判断。模型不能自报 approval flag、snapshot 或 executable
绕过门禁。接受的证据保留在 terminal operation receipt 中。后续输入失效后，精确
receipt 回放仍只报告历史，不重新写状态或证据。换用其他 terminal verb 也不能避开
相同校验；上述 typed candidate dismissal 是没有实验结果证据时的合法 retirement。

Writer 在校验、持久化 research writeback 时持有 Explore 日志锁。持久化 guard
使用有界 rollout 标量字段；TS 与 Python receipt adapter 保留相同所选事实。
Public quota card 不携带内部 task join、transition candidate 或完整 result
observation，并提供遗漏 card 的数量。

已完成 Todo 在归档或清除 claim 后保留证据 lineage。投影读取 canonical 保留
历史，而不是压缩的活动 Todo 列表。归档的 open/deferred 行不能调度或提供新
observation attribution；缺失或不匹配的历史、失效输入仍不能证明当前覆盖。
历史证据不授予当前 claim、lease 或执行权限。

`status --goal-id research-demo --agent-id research-agent` 在
`project_asset.bounded_research_frontier` 展示同一 live count 与有界 gap。
`explore summary --goal-id research-demo --agent-id research-agent` 提供
`research_execution_frontier`，并注释既有 node summary；相同 summary 进入 Lark
投影字段。`feishu-sync` 与 `feishu-card` 支持同一 Agent selector。多个已注册
Agent 的 live-policy Goal 需要显式 selector；单个 Agent 可自动用于展示。
这些读取不开始 Turn、不修改 Todo、不授予远程写权限；未激活策略保留原投影。

只关闭新策略、保留既有 planner：

```sh
loopx configure-goal --goal-id research-demo --explore-composition-mode disabled --execute
```

移除策略字段也恢复既有行为。范围、输入或激活变化不能追溯抹去已准入 Turn 的
guard；过期 writeback 仍被拒绝。当前 source fact 证明 invalidation 时，拒绝结果
提供精确 `retirement_contract`。在原 `--replan-obligation-id` 和
`--turn-instance-id` 下重用其 blocked progress 字段，并指定
`--delivery-outcome outcome_gap`。Capability owner 重新校验当前源并生成
`capability_obligation_retirement_v0`；调用者自报 approval flag 或任意
blocker/evidence id 不能代替此证明。仍有绑定该 duty 的 runnable Todo 时不能
retire，须先通过该任务自己的 lifecycle 暂停。无效、缺失或不可用的 evidence
source 不能冒充 invalidation。

通用 settlement owner 校验原 guard、durable writeback、当前 revision 与精确
progress fingerprint，返回 `closeout_kind=capability_duty_retired_no_spend`。
它仅关闭该已准入 Turn，不完成 Todo/Goal，也不花费 quota；已提交 debit 始终
保留为普通历史 debit。旧 Turn reentry 与 spend 请求复用同一无支出 closeout，
不追加记账。下一轮重新判断当前 frontier，保留未完成 acceptance 与暂停任务。
保留原 receipt 与证据，不通过删除它们制造结算。
任何策略设置都不授权 Goal termination、真实模型 qualification、benchmark launch、
部署或发布。

## 停用与权限边界

没有新增自动激活。停止发出 `explore observe` 即停止新证据写入；已有 observation
读取是只读操作。需要使输入 research eligibility 失效时，先使用普通 `explore node`
revision，再开展新调查。保留 append-only 日志，不通过删除证据回滚。适用时通过
既有 Goal 配置停用 harness；此命令不会将其启用。

Receipt 证明 typed attribution 和 revision 一致性，不证明外部实验实际执行，也不
证明科学结论为真。真实模型和研究资格仍由 RFC 的独立 gate 决定。Lark 仍为可选
extension；projection 不授权远程同步、凭据访问或网络执行。
