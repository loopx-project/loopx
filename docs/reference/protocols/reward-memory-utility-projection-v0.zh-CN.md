# Reward Memory Utility Projection v0

`memory_utility_projection_v0` 是对已经通过 Stage 1
`memory_utility_observation_v0` 校验的追加式观察流做归约后得到的只读结果。
它是 LoopX 本地 projection，不是 provider ledger，也不是记忆生命周期记录。

```bash
loopx reward-memory utility-project \
  --input utility-observations.json --format json
```

输入对象只包含 `observations`、`scope`、`retrieval_snapshot_ref` 和
`policy_snapshot_ref`，并可选 `reducer_version` 与 `previous_projection`。
`previous_projection` 只用于校验 identity；调用方仍需提供完整观察流，以便重启后
得到可复现结果。

## 所有权与入口

Stage 1 observation 校验与 Stage 2 reducer/readback 继续由现有
`reward_memory` capability 中的同一个 Python 语义所有者负责。本阶段只有显式 CLI
调用，不新增配置、自动召回或 provider 路径。这样可让领域内 projection 留在现有边界，
避免再维护一份 TypeScript reducer。未来若出现共享 consumer，应复用此所有者，或整体迁移
该事务并删除 Python 规则。

本次 CLI-only 交付没有改变配置或用户自动化路径，因此不需要新增 frontend 或 Lark 控件。
如果后续交付启用自动使用、ranking 或更广泛采用，应在同一交付中补上现有共享 projection
及相关用户控制。Scope 与 snapshot 引用只绑定 identity，不能证明源数据的新鲜度或覆盖率；
本 packet 尚无 projection envelope，因此 consumer 必须将新鲜度与覆盖率视为未知。

## Projection identity

Reducer identity 由以下内容共同决定：

- `scope.agent_id`、`project_id`、`corpus_id`、`surface_id`；
- retrieval 与 policy snapshot 引用；
- 显式的 `reducer_version`。

任一值变化都会产生不同的 `reducer_identity`。带有不同 identity 的
`previous_projection` 会在返回 subject 状态前被拒绝。默认实现版本是
`memory_utility_reducer_v0`。

## 归约规则

Reducer 首先使用 Stage 1 validator 校验每条 observation，并要求 scope 与 snapshot
完全匹配，然后按以下规则处理：

1. 相同 `observation_id` 的语义重放只计一次；仅重试 `created_at` 不会产生新观察。
2. 同一 `observation_id` 下的不同 payload 视为冲突投递，不增加支持计数，并返回
   `review_required` 与挂在 rejection 记录上的 quarantine proposal
   （`quarantine_proposed: true`）。冲突 identity 不会进入 subject，因此 proposal
   保留在有界 rejection 记录中。
3. 证据优先级固定为：`owner_correction` > `controlled_replay` >
   `deterministic_effect` > `evaluator_inference` > `insufficient`。
4. 最高 evidence tier 决定 effective direction。如果该 tier 是 `unknown`，较弱的
   有方向观察不能凭空制造 utility direction；当存在这类较弱方向时，subject 变为
   `unknown` 并要求 review。否则只有最高强度的有方向证据会贡献 effective label 和
   有界 utility，较弱观察仍保留在支持计数和历史中。同一强度出现不同方向判断时，
   结果为 `unknown` 并要求 review。
5. `unknown` 只增加 lineage 与覆盖计数，不能单独产生 utility 方向。
6. `item` subject 必须只有一个 memory digest；`set` subject 保留完整应用集合。
   set-level 证据绝不会复制到 item subject；`none` subject 仅用于 lineage，单独保留。

Utility 限制在 `[-1.0, 1.0]`，confidence 和 uncertainty 限制在 `[0.0, 1.0]`。
支持计数保留四种 label 和五种 evidence tier。每个 subject 还保留稀疏的
`evidence_label_summary`，记录实际出现的每组 evidence-tier/utility-label 的计数与组合
confidence。Projection 记录最近接受的 observation 身份、时间以及有界的 public-safe
历史。超过 history 上限时，会先保留每个 subject 的最近一条记录，再用最新记录填满
剩余空间，保证截断后仍能审计 subject 的 latest 字段。

即使 history 被截断，readback validator 仍然 fail-closed：它会从联合
`evidence_label_summary` 推导最高 evidence tier、effective label、confidence 与 review
状态，并验证联合计数能够还原 label 和 evidence-tier 两组边际计数。因此，即使决定性
history 已被截断，也不能用较弱 support 把最高层级的 harmful 或 unknown 改成其他结果。
Projection 时间戳也必须保持 canonical，首尾空白会被拒绝。

## Review 与安全边界

负向 utility 只产生 `attenuation_proposed` review 状态，建议动作是
`attenuate_or_review`。冲突产生 `conflict`，未建立归因产生
`unresolved_attribution`。这些都只是 proposal。冲突 delivery 还会在 rejection
记录上带 `quarantine_proposed` 标记，但不会授权删除或修改。Projection 和每个
subject 都保证：

```json
{
  "read_only": true,
  "automatic_deletion": false,
  "action_authority_granted": false,
  "provider_write_performed": false,
  "external_writes_performed": false,
  "raw_content_captured": false
}
```

格式错误、scope 或 snapshot 不一致、projection identity 不一致都会返回
`status: "rejected"`，且不返回 subjects。Reducer 不调用 provider，不接入 ranking、quota
或 scheduler，也不改变主工作流结果或 settlement。调用方可以检查 rejected packet，同时
继续原本 fail-open 的工作流。

Projection 只接受 opaque 引用和 canonical memory digest，不保存原始记忆文本、prompt、
transcript、credential、本地路径或 provider 私有 URI。

## Public shape

顶层 packet 使用 `memory_utility_projection_v0`，包含 accepted、duplicate、conflicting、
rejected delivery 的有界计数、`subjects`、subject-level `review_proposals`、带
quarantine metadata 的 conflict rejection 记录以及 `observation_history`。Subject 携带 attribution level、digest 集合、effective label 与
evidence basis、有界 utility/confidence/uncertainty、支持计数、证据强度计数、稀疏的
evidence-label 联合摘要、最近观察及 review 状态。

相同的语义观察流、scope、snapshot 和 reducer version 无论输入顺序如何，都会产生相同
projection。
