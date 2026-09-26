# RFC：结果后记忆效用归因 v0

- 状态：草案；Stage 1 已交付，Stage 2 已在后续 issue #3824 实现，Stage 3+ 仍为提案
- 日期：2026-08-15
- 跟踪 issue：[#3214](https://github.com/huangruiteng/loopx/issues/3214)、[#3824](https://github.com/huangruiteng/loopx/issues/3824)
- 决策边界：LoopX 如何在工作结果可验证之后，把结果归因到此前召回的记忆，并产出有界的效用投影
- 能力所有者：现有 `reward_memory`
- Provider 边界：可选 evaluator provider 与 context-provider adapter，包括 OpenViking

## 0. 摘要

被召回，不等于有用。Agent 说自己使用了某条记忆，也不等于这条记忆让结果变好。一次成功任务还可能同时使用多条记忆，最终奖励无法公平地复制给每一条。

本 RFC 建议在现有 reward-memory 协议后面增加一条“结果后归因”sidecar：

```text
recall receipt
  -> application receipt
  -> verified work outcome
  -> optional utility evaluator
  -> append-only utility observation
  -> deterministic scoped projection
  -> optional provider rank-prior effect + readback receipt
```

Sidecar 默认关闭、fail-open，不进入主工作路径。Evaluator 只能提出类型化 observation，不能选择工作、修改记忆、消耗其他 lane 的 quota，也不能直接修改 provider 排序。

这里的“全局”最多表示：多个已注册 lane 共享同一套 evaluator 协议与评估口径。它不表示一个全知 supervisor，更不表示跨 scope 共享一池效用分数。

## 1. 问题

现有 reward-memory 架构已经把召回与使用分开。Stage 3 会记录 `applied`、`ignored` 或 `refuted`，精确 provider ref 留在进程内，公共结果只暴露 opaque ref。Stage 5 目前再把 `applied` 映射为 dogfood `hit`，把 `refuted` 映射为 `refute`，其余映射为 `miss`。

这个映射能提供执行 lineage，但混在了一起的其实是四件不同的事：

1. **召回相关性**：语义或结构化召回是否为当前上下文选中了这条记忆；
2. **使用处置**：工作 agent 声称使用、忽略还是拒绝了它；
3. **工作结果**：目标 effect 或任务最终是否成功；
4. **记忆效用**：相对合理替代路径，这条记忆究竟改善还是伤害了结果。

第五件事——记忆生命周期与权限——也必须独立。高效用记忆仍然只是 observation，不能授予权限，不能替代当前仓库、goal 或用户给出的 authority。

如果不拆开，系统很容易奖励“经常被召回”“碰巧和简单任务一起出现”或“被模型自信引用”的记忆；当一条 trajectory 使用多条记忆时，也可能把同一个终局奖励粗暴地复制给所有记忆。

## 2. 决策

LoopX 应在现有 `reward_memory` 能力中增加 provider-neutral 的结果后记忆效用归因协议。

协议新增三个逻辑 surface：

- `memory_utility_observation_v0`：把一个 application receipt、一个后续 outcome 与一次归因判断绑定成 append-only 记录；
- `memory_utility_projection_v0`：从已接受 observation 确定性归约出的 scope 内状态；
- 可选的 provider effect/readback seam：用于应用有界 rank prior。

这里不新增内建 `supervisor` capability。Recall receipt、application receipt 与 dogfood settlement 原本就属于 reward memory，归因也属于同一个变化原因和生命周期。`memory_utility_evaluator` 只是可选 provider 角色。OpenViking 仍然是可选 context provider，它的 URI、lineage 与 policy snapshot 机制留在 adapter 边界。

## 3. 必须保持独立的语义

以下字段不得被压成一个分数或状态：

| 关注点 | 示例值 | 所有者 |
| --- | --- | --- |
| 召回 | score、rank、response digest | context provider |
| 使用 | `applied`、`ignored`、`refuted` | 工作 agent receipt |
| 结果 | effect status、rubric score、task result | LoopX effect/outcome evidence |
| 归因 | `helpful`、`harmful`、`neutral`、`unknown` | evaluator observation |
| 效用状态 | 有界 prior、support、uncertainty | LoopX deterministic reducer |
| 生命周期 | retain、edit、retire、quarantine | 现有 owner-authorized memory lifecycle |

尤其需要明确：

- `applied + success` 不足以证明 `helpful`；
- `refuted` 本身也不足以证明 `harmful`；
- 召回频次与新鲜度不等于效用；
- 模型置信度不是结果证据；
- 负效用 observation 不授权删除记忆；
- 排名更高不会把记忆变成指令或 authority。

## 4. Utility observation 协议

一个 public-safe 的 `memory_utility_observation_v0` 应只保留 replay 与 audit 必需的字段：

```json
{
  "schema_version": "memory_utility_observation_v0",
  "observation_id": "muo_<stable_digest>",
  "scope": {
    "agent_id": "agent_opaque",
    "project_id": "project_opaque",
    "corpus_id": "corpus_opaque",
    "surface_id": "reward_memory"
  },
  "application_receipt_id": "rma_<opaque>",
  "memory_ref_digests": ["sha256:<digest>"],
  "retrieval_snapshot_ref": "snapshot_opaque",
  "policy_snapshot_ref": "policy_opaque",
  "outcome_ref": "effect_opaque",
  "utility_label": "unknown",
  "attribution_level": "set",
  "evidence_basis": "evaluator_inference",
  "confidence": 0.42,
  "reason_codes": ["multiple_memories_not_disambiguated"],
  "evidence_refs": ["evidence_opaque"],
  "evaluator_ref": "evaluator_opaque",
  "evaluation_version": "evaluation_v0",
  "created_at": "2026-08-15T00:00:00Z",
  "grants_new_action_authority": false,
  "provider_write_performed": false,
  "external_writes_performed": false,
  "raw_content_captured": false
}
```

协议不得包含原始记忆正文、原始 trajectory、provider credential、私有路径或未脱敏 transcript。精确 provider ref 可以留在所属进程或私有 adapter 内；公共投影只使用 opaque digest。

`observation_id` 必须由归因对象与 evaluator 版本稳定生成，使重试具备幂等性。Evaluator 版本变化或出现新证据时，应追加新 observation，不能静默覆盖历史。

### 4.1 归因粒度

`attribution_level` 取值为：

- `item`：证据能区分单条记忆的贡献；
- `set`：召回或使用集合共享一个结果，但单条 credit 尚未解开；
- `none`：连集合层面的相关性都无法建立。

多条记忆同时被使用时，默认是 `set`。Reducer 不得把 set-level reward 复制进每条记忆的 utility state。

### 4.2 证据类型

证据强度必须类型化，不能从自然语言里猜：

1. `owner_correction`：明确、限定 scope 的人类反馈；可以覆盖较弱的历史推断，但不授予更广执行权限；
2. `controlled_replay`：从相同相关状态出发的有界反事实或 local rerollout；
3. `deterministic_effect`：能区分记忆贡献的 artifact、test 或 effect 证据；
4. `evaluator_inference`：模型基于 public-safe receipt 与 outcome 作出的判断；
5. `insufficient`：lineage 存在，但无法归因。

前三类较强证据必须在 proposal 中至少带一个 opaque `evidence_ref`；
`insufficient` 可以不带 evidence reference。即使 label 是 `unknown`，也不能绕过这条 provenance 要求。

仅有模型推断属于弱证据。它可以留下来做校准与 review，但 profile 必须能禁止它移动一个已经由强证据建立的 rank prior。

## 5. Evaluator 角色与权限

可选 evaluator 只能读取显式注册、public-safe 或 owner-scoped 的投影：

- recall 与 application receipt；
- verified outcome 或 compact rubric；
- 被允许的 artifact evidence；
- 相同 scope 内的 utility history；
- evaluator 与 policy snapshot 标识。

它只输出 observation proposal，不得：

- 选择、取消或 promote todo；
- 把不确定性自动升级成 user gate；
- 阻塞主工作结果或 settlement；
- 使用其他 lane 的 quota；
- 编辑、删除或发布记忆；
- 直接改变 provider score；
- 合并未显式注册的用户、项目或 corpus scope；
- 把召回内容重新解释为 authority。

Evaluator 在运维层面可以共享，但每条 observation 与 projection 都必须按 scope 分区。这与 [Peer Supervisor v0](../../reference/protocols/peer-supervisor-v0.md) 的 equal-peer 边界一致。

## 6. Deterministic reducer

Utility attribution Stage 2 已在
`loopx.capabilities.reward_memory.utility_reducer` 中实现，并通过
`loopx reward-memory utility-project` 暴露。Reducer 只接受 schema 合法且 scope
匹配的 observation，输出版本化、只读的 `memory_utility_projection_v0`。对每个
合格 memory 或 set，维护：

- 有界 utility estimate；
- positive、negative、neutral 与 unknown support count；
- evidence-strength 分布；
- uncertainty 与 last-observed time；
- 最近接受的 observation id 与 reducer version；
- quarantine 或 review proposal，而不是隐式删除。

v0 使用有界、按 evidence tier 分层的归约，不宣称是 learned value function：

- `owner_correction` > `controlled_replay` > `deterministic_effect` >
  `evaluator_inference` > `insufficient`；
- 最高 evidence tier 决定 effective direction；该 tier 的 `unknown` 会阻断较弱的
  有方向证据，否则只有最高强度的有方向证据贡献 effective label 与 utility estimate，
  较弱 observation 仍保留在 support 与 history 中；
- 同一 tier 的方向冲突归为 `unknown`，并产生 review proposal；
- `item`、`set` 与 `none` subject 分开维护，set-level credit 不复制到每条记忆。
- 冲突 delivery 排除在 subject state 之外，并在 rejection 记录上产生
  `quarantine_proposed` proposal，但不授予任何修改权限。

实现满足以下不变量：

- 单次 observation 不能让 utility 越过配置边界；
- 重复的弱推断不能淹没更强的 correction 或 replay；
- `unknown` 可以提高 lineage coverage，但不改变 utility 方向；
- 历史 evidence 只追加不覆盖；未来如需时间衰减，必须引入单独版本化的 reducer 合同；
- scope mismatch 对 observation fail-closed，主 lane 仍然 fail-open；
- 重放相同 observation 是 no-op；
- reducer version 变化必须显式且可复现。

Projection 还包含有界的 accepted/duplicate/conflicting/rejected 计数、label 与
evidence-tier support 计数、最近接受的 observation identity/time、有界的
public-safe history 以及 review proposal。相同 `observation_id` 下、具有相同
semantic fingerprint 的重试只计一次；仅 `created_at` 不同不会增加 support。
Duplicate 计数只统计相同 fingerprint 的额外 delivery；conflicting delivery
单独计数，而 rejected 计数覆盖冲突 identity 的全部 delivery。同一 identity
下的不同 payload 会被排除在 effective state 之外。Scope、snapshot、malformed
observation 或 reducer identity 不匹配时，返回没有 subjects 的 fail-closed
rejected packet；主工作流仍保持 fail-open。

未来如果让 utility 参与召回排序，语义相关性必须仍是 anchor。一种允许的形态是：

```text
rank_score = semantic_score * bounded_utility_modifier
```

Modifier 有明确上下界，不能让没通过语义 candidate stage 的无关记忆“复活”。Freshness、lifecycle、permission 与 authority 继续是独立过滤条件。

## 7. 调度与成本

归因在合格 outcome settlement 之后运行，不进入关键路径。Profile 可以采样、批处理或按 cadence 执行，评估使用独立的有界 quota 与 dedupe key。

Scheduler 不得在每次 heartbeat 上递归创建 supervisor 工作。Evaluator 不可用时不产生 utility update，也不改变已经产出的工作结果。成本高的 controlled replay 只用于被明确策略选中的高影响、强歧义案例。

## 8. 与 OpenViking 的结合分析

OpenViking 很适合提供记忆 identity 与 lineage，但当前接口不应被误当成因果效用服务。

在 OpenViking main commit [`eeff5a4`](https://github.com/volcengine/OpenViking/commit/eeff5a497360aa4481cf32e18a0d9376f4412f4c) 上：

- search/context result 暴露 URI、category、retrieval score、detail level、origin 与 response digest；
- [Agent Evolution](https://github.com/volcengine/OpenViking/blob/eeff5a497360aa4481cf32e18a0d9376f4412f4c/docs/en/api/19-agent-evolution.md) 能列出与一条 Experience 关联的 trajectory，并聚合终局 outcome tag；
- [experience lineage](https://github.com/volcengine/OpenViking/blob/eeff5a497360aa4481cf32e18a0d9376f4412f4c/openviking/session/memory/experience_lineage.py) 识别的是已经完成的显式 read tool part，因此自动 context injection 仍可能需要 LoopX 自己的 recall receipt 补齐；
- training domain 已有 `Rollout`、`RubricEvaluation`、trajectory outcome 与 `policy_snapshot_id`；
- [hotness](https://github.com/volcengine/OpenViking/blob/eeff5a497360aa4481cf32e18a0d9376f4412f4c/openviking/retrieve/memory_lifecycle.py) 来自 access count 与 recency，thinking-mode retrieval 可以混入它，但它不是 outcome utility；
- 当前没有 first-class 的外部 utility 或 Q-value update API。

OpenViking trajectory outcome 可以作为“这组 Experience 被消费过”的结果证据，但不能证明每条 Experience 的边际贡献，因为所有被消费项可能继承同一个 final outcome tag。

### 8.1 所有权拆分

| Surface | LoopX | OpenViking | Evaluator provider |
| --- | --- | --- | --- |
| Goal、quota、authority | 拥有 | 不推断 | 不改变 |
| Recall identity 与 content access | 记录 opaque receipt | 拥有 URI/search/read | 观察获准 receipt |
| 精确 application | 拥有 receipt | 可提供 read lineage | 不编造 |
| Verified work outcome | 拥有 reference | 可提供 trajectory outcome | 解释获准 evidence |
| Utility ledger 与 reducer | 拥有 | v0 不写入 | 只提 observation |
| Memory content/lifecycle | 通过现有 effect 请求 | 拥有 provider operation | 不直接写入 |
| 可选 rank-prior effect | 授权并记录 receipt | 未来 adapter contract | 不直接写入 |

### 8.2 最小 OpenViking 切片

第一阶段只读：

1. 在 LoopX recall receipt 中保留 OpenViking result digest 与 opaque URI digest；
2. 把精确 LoopX application receipt 绑定到后续 verified outcome；
3. 可选查询 Agent Evolution，作为辅助 lineage 与 outcome evidence；
4. 如果存在，保留 `policy_snapshot_id` 或等价 retrieval snapshot，保证之后基于执行时可见状态评估；
5. Utility 先存放在 LoopX sidecar projection。

不要把 utility 写进 memory prose、`active_count` 或 hotness。只有当 OpenViking 暴露清晰的外部 rank-prior/utility 协议，并能返回 effect 与 readback receipt，才考虑 provider writeback。通过训练更新 Experience Policy Set 是另一种需要 owner review 的内容变更，不是排名权重捷径。

## 9. 最小可用实现切片

Stage 0 只有本 RFC 与跟踪 issue。

Stage 1 修复语义 seam，但不改变召回：

- 定义并校验 `memory_utility_observation_v0`；
- 把现有 Stage 3 application receipt 绑定到 verified outcome ref；
- Stage 5 不再把 `applied` 当作充分效用证据；
- 证据不足时输出 `unknown`；
- 保持现有主 lane 行为不变。

Stage 2 增加幂等 reducer 与只读 utility projection，不改变 provider ranking。

Stage 3 增加 OpenViking readback；只有 provider 协议真实存在时，才增加有界 rank-prior effect 与 readback receipt。

Stage 4 先做有界 pilot、held-out 或 counterfactual evaluation，再考虑让排序影响 default-on。

## 10. 验证标准

Focused fixture 必须证明：

- 记忆即使被 `applied`，在 artifact evidence 反驳时仍可判为 `harmful`；
- 没有归因证据时，`applied + success` 仍是 `unknown`；
- 多记忆 trajectory 有歧义时保持 set-level；
- 用户 correction 可以在 projection 中压过较弱 inference，但不删除历史；
- 过期 policy/retrieval snapshot 与 scope mismatch 会拒绝 observation；
- 重复 delivery 幂等；
- evaluator timeout、malformed output 或缺失不改变主输出与 settlement；
- utility 不能绕开 semantic candidate filtering；
- 公共 packet 不包含原始 memory、transcript、本地路径或精确私有 provider ref；
- 负效用提出 attenuation/review，不直接删除；
- provider writeback 必须有显式 effect 与 readback receipt。

在 utility 影响排序之前，至少比较：

- 纯 semantic retrieval；
- semantic retrieval 加 access hotness；
- semantic retrieval 加 bounded utility；
- 去掉 evaluator inference 后的同组条件。

评估需要报告质量、task cost、false attenuation、scope leak 与 evaluator disagreement。仅仅“与终局成功率相关性更高”，不足以证明因果效用。

## 11. 非目标

- 一个全知的 global leader agent；
- 新的执行或审批 authority；
- 默认跨用户、项目或 corpus 转移 utility；
- 自动改写、删除或发布记忆；
- 把访问频次当作 utility proxy；
- 每次 heartbeat 都 review 每条 trajectory；
- 在受控 qualification 前宣称 online reinforcement learning；
- 替代 OpenViking 对 retrieval 与 memory lifecycle 的所有权。

## 12. 备选方案

### 直接把 `applied` 映射为正效用

拒绝。它奖励的是自我报告，识别不了“自信使用了错误记忆”。

### 让一个 LLM supervisor 直接更新分数

拒绝。它把判断与 mutation 混在一起，难以 replay，也制造了 authority 升级路径。

### 复用 OpenViking hotness

拒绝。Hotness 衡量访问与新鲜度；一条频繁被召回的坏记忆反而可能越来越热。

### 把 trajectory final reward 分给所有召回记忆

拒绝。多记忆、多 policy state 的场景会产生高噪声 credit。

### 负面判断后直接改写或删除记忆

拒绝。Utility weighting 与 owner-authorized memory lifecycle 是两种 evidence 和回滚要求都不同的 effect。

### 每个 outcome 都做 counterfactual replay

v0 拒绝。成本过高，而且 replay 本身可能偏离原始 policy state。它只保留为高影响案例的强证据层。

## 13. 研究依据

- [OpenViking Experience Policy Set training RFC](https://github.com/volcengine/OpenViking/discussions/2533) 已提供 rollout、evaluation、gradient、optimizer、updater 与并发 policy update seam；
- [OpenViking on-policy 与 memory versioning RFC](https://github.com/volcengine/OpenViking/discussions/2277) 说明归因应基于执行时使用的 policy view；
- [MemRL](https://arxiv.org/abs/2601.03192) 把 semantic candidate retrieval 与 bounded utility-guided selection 分开，并利用环境反馈更新 utility；
- [Memory-R2](https://arxiv.org/abs/2605.21768) 指出 trajectory-level reward 会产生不公平 credit，并研究从相同中间 memory state 出发的 local rerollout；
- [Mem-π](https://arxiv.org/abs/2605.21463) 把任务执行与可选择是否提供 memory guidance 的模型分开。

这些工作支持“语义拆分与验证计划”，但不代表本 RFC 已经具备 default-on 资格。

## 14. 与现有协议的关系

- [Reward Memory Architecture v0](../../../loopx/capabilities/reward_memory/README.md) 仍是稳定的 recall、application 与 lifecycle owner；本 RFC 定义 post-outcome attribution seam，其中 Stage 1 observation 合同与 Stage 2 只读 reducer 已实现，后续 provider effect 仍为提案。
- [Peer Supervisor v0](../../reference/protocols/peer-supervisor-v0.md) 提供 equal-peer、public-safe、proposal-only 的 authority 边界。
- [Agent IM, LoopX, and OpenViking collaboration v0](agent-im-openviking-collaboration-v0.md) 保持 LoopX 对 goal、authority 与 evidence 负责，OpenViking 对 context 与 recall 负责。
- [Human Attention Wishlist v0](human-attention-wishlist-v0.md) 仍然是对可选人类增量帮助的 non-blocking request。只有当人类输入确实有增量价值时，utility uncertainty 才成为 wish；它不会自动变成 gate。

Stage 1 observation 合同与 Stage 2 reducer/projection 已有稳定 reference contract 和
focused validation。它们保持只读，不改变默认 retrieval、ranking、provider state、
authority 或主工作流。OpenViking writeback、ranking influence 与 qualification 仍属于
后续 Stage 3/4，需要单独的版本化合同与 review。
