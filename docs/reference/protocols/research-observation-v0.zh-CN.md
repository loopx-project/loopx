# Research observation v0

Explore 拥有这一可选证据契约。第一个写入入口是 `loopx explore observe`；
`loopx explore summary` 与现有 Lark Explore 节点摘要显示同一派生事实。
本批交付 M1 证据基础和 M2 **只读 shadow**，不实现 M3 的 obligation/writeback 门禁。

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
的日志。既有 `#3173` composition/quota 行为不变；此命令不启用 research policy、
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
node observation 检查；本批不创建 obligation，因此不会丢失义务。Dismissal、
deferral、observation/Todo lineage、shared write gate 和 hot status adoption
仍属 M3。

## 停用与权限边界

没有新增自动激活。停止发出 `explore observe` 即停止新证据写入；已有 observation
读取是只读操作。需要使输入 research eligibility 失效时，先使用普通 `explore node`
revision，再开展新调查。保留 append-only 日志，不通过删除证据回滚。适用时通过
既有 Goal 配置停用 harness；此命令不会将其启用。

Receipt 证明 typed attribution 和 revision 一致性，不证明外部实验实际执行，也不
证明科学结论为真。真实模型和研究资格仍由 RFC 的独立 gate 决定。Lark 仍为可选
extension；projection 不授权远程同步、凭据访问或网络执行。
