# 第 11 讲：扩展层、Explore 与 Agent 协作

> **本讲结论：** Extension 交付可选 provider、domain facts、capability 或 presentation；
> 它复用同一份 goal、todo、quota、scheduler、evidence 与 handoff contract，不创建第二个
> kernel。

建议时长：150 分钟。扩展地图与 governed external execution 50 分钟、Explore 30 分钟、
Single-Agent Auto ML 25 分钟、Agent 协作 30 分钟、实验 15 分钟。

一小时专题[长程任务如何收敛](topic-long-horizon-convergence.md)从“如何退出局部循环”解释
Graph、Harness 与 Kernel 的关系；本讲继续展开它们的扩展边界和产品组合方式。

## 学习目标

完成本讲后，开发者应该能够：

1. 使用 `configure-goal` 预览和开启 default-off feature。
2. 准确区分 Explore Graph 和 Explore Harness。
3. 解释 Harness 为什么只是 analysis-only planner，而不是执行授权。
4. 解释领域配置与通用协作控制面的边界。
5. 判断 supervisor、multi-subagent、reward memory、connector 各自扩展哪个边界。
6. 区分 Provider 的运行责任与 Extension 的交付生命周期。
7. 用 Single-Agent Auto ML 解释 Explore Graph、Harness、Domain Pack 与 Kernel 怎样组合。
8. 用 governed external execution 解释外部 effect 怎样安全 start、reconcile、writeback 和 spend。

本讲采用分层阅读：所有开发者先读 Extension Contract、分层原则和 Feature Catalog；
做探索产品再读 Graph/Harness 与 Single-Agent Auto ML；做 Agent 协作再读
Multi-Subagent；只有接入对应 surface 时，才需要继续读 Supervisor、Reward
Memory 或 Lark Event Inbox。

## 从研究协作反推 Extension Contract

第 2 讲用研究团队说明协作边界；本讲按可复用 extension 拆分责任：

| Surface | 研究场景提供 | 继续由通用层拥有 |
| --- | --- | --- |
| User entry | open question、少量 preset 选项 | goal identity、preview/execute boundary |
| Provider | evaluator、artifact source 与可选 sink 的 observation/readback | transition 与 goal lifecycle |
| Capability Pack | role defaults、evidence adapter、decision candidates | todo/gate/quota/handoff/terminal semantics |
| Domain State | hypothesis、experiment、dev/holdout evidence | claim、permission、scheduler、spend |
| Host integration | local/cloud workers、isolated executor turn | session lifecycle、workspace guard、effect receipt |
| Projection | evidence graph、research frontier、showcase | canonical state 与 transition authority |

如果一个新 extension 能按这些边界回答，通常不需要复制 runner。若它必须自己维护 agent
身份、runnable queue、retry、gate、completion 和 cadence，它已经不是薄 capability，而是
第二个 control plane。

## 扩展层的总原则

LoopX 扩展不应创建第二套：

- goal truth；
- todo lifecycle；
- quota decision；
- scheduler；
- peer identity；
- evidence ledger；
- user gate；
- spend accounting。

一个扩展应该提供：

```text
provider implementation / domain evidence / role defaults / presentation sink
```

然后复用：

```text
registry -> status -> quota -> interaction contract -> todo/evidence -> refresh/spend
```

## 运行责任与扩展交付

Extension 是安装、启停、升级和分发边界，不是运行时的第五个 owner。运行时仍按四种责任
分工：

| 角色 | Owns | Must not own |
| --- | --- | --- |
| Agent | 通过 host/runtime 完成方案、分析、工具与一次有界执行 | durable lifecycle 或未授权 effect |
| Provider | 外部调用、observation、effect result 与 readback | transition policy 或 todo state |
| Capability Pack | 领域 route、归一化、validator、typed transition 与 preset | 绕过 Kernel 的 permission 或 lifecycle |
| Kernel | vision、goal、todo、gate、monitor、quota、writeback、scheduler、evidence、handoff | Issue-Fix/Explore/ML 的专用判断 |

Domain State 保存 feasibility、PR lifecycle、experiment result、checkpoint 等紧凑连续性，
但它是 Capability 与 Kernel 使用的工件，不是另一个 actor。Extension 可以交付 Provider，
也可以只携带自己的 command 或 presentation；只有调用者需要稳定、provider-neutral 的结果
合同时，才新增公共 Capability。

这套分工让领域能力增加专属事实，同时复用 Kernel 的权限和生命周期模型。
当前 Issue-Fix pack 直接复用通用 Domain State seam：

```python
from ..domain_state import default_domain_state_file_path, upsert_domain_state_jsonl

def _upsert_issue_fix_payload(ledger_path, payload, *, key, ...):
    projection = payload.get("domain_state_projection")
    if not isinstance(projection, dict):
        raise ValueError("issue-fix payload must include domain_state_projection")

    projection["write_performed"] = True
    try:
        result = upsert_domain_state_jsonl(
            ledger_path,
            payload,
            key=key,
            existing_key_fn=existing_key_fn,
            unchanged_fn=unchanged_fn,
            merge_existing_fn=merge_existing_fn,
        )
    except Exception:
        projection["write_performed"] = False
        raise
    projection["write_result"] = result
    return result
```

ML Experiment 走相同 seam，只替换自己的稳定 key 和 payload validator：

```python
return upsert_domain_state_jsonl(
    ledger_path,
    payload,
    key=ml_experiment_ledger_key(payload),
    existing_key_fn=ml_experiment_ledger_key,
)
```

这两个调用点给出一条可操作的评审规则：如果新 pack 只需要紧凑领域事实，就复用
Domain State；如果它需要改变谁可执行、何时 spend、哪个 gate 生效，就必须回到
Kernel 提出通用规则并接受第 7、8 讲的语义与质量门禁。

同时保留三条数据边界：raw log、凭据和本地路径留在获批的私有 artifact；Domain
State 只保存可重放的 compact facts；外部 sink 只消费 public-safe projection。
`examples/issue-fix-feasibility-smoke.py` 与
`tests/test_ml_experiment_volc_packet.py` 是这条分层的代表性回归入口。

## Governed External Execution：把外部副作用变成可恢复事务

外部 capability 最危险的时刻，不是 API 明确返回失败，而是调用方不知道它到底有没有成功：

```text
provider 已经创建任务
  -> 本地进程在保存结果前崩溃
  -> Agent 只看到“本轮失败”
  -> 盲目重试又创建一个任务
  -> Goal state、外部事实和 quota 从此分叉
```

`capability invoke` 可以直接执行 read-only operation，因为它不会产生需要结算的物质效果。
`external_write` 则必须进入 governed external execution。它不是给 provider 套一层普通 retry，
而是把一次外部写入拆成两段可重放事务：

```text
Goal-bound capability + exact provider revision
  -> selected Todo + quota should-run
  -> start(turn_instance_id, provider input)
       1. derive one settlement identity
       2. validate Todo contract and authority
       3. write mode-0600 journal
       4. dispatch provider with effect_id as idempotency key
       5. persist running or terminal effect receipt
  -> ready_to_settle
  -> reconcile(invocation_id)
       6. poll the same provider run when still running
       7. validate the terminal receipt
       8. durable writeback of the receipt digest
       9. spend exactly one quota slot
  -> committed
```

这条路径复用了 Kernel 的 selected-Todo admission、Turn transaction、settlement driver 与 quota，
所以外置仓库提供的领域 capability 不需要再复制一套 lifecycle。Extension/Provider 仍拥有远端
调用、异步 job 和 domain result；LoopX 拥有“谁被允许执行哪一个 effect、如何恢复、何时才算
结算完成”。

### Goal capability 与 material Turn 分别回答什么

Goal enablement 与 Turn admission 不是二选一，而是两层不同粒度的授权：

| 层 | 回答的问题 | 不代表什么 |
| --- | --- | --- |
| Goal binding | 这个 Goal 可使用哪个 capability、provider revision 和 operation profile？ | 不代表任意消息都可执行外部写入 |
| Todo contract | 当前 operation 能否服务 selected Todo 的 `action_kind` 与 `target_key`？ | 不代表 Todo 已经 runnable |
| Turn admission | 这个 Agent 是否可在这个 `turn_instance_id` 下执行一次 material attempt？ | 不扩大 Goal capability 或远端权限 |
| Provider auth | 外部服务是否接受这次具体调用？ | 不替代 LoopX writeback 与 quota settlement |

因此 capability 以 Goal 为单位保持可用，真正可能产生副作用的 attempt 再以 Turn 为单位获得稳定
事务身份。普通解释、观察和 readback 不必伪装成 material Turn；它们继续走 read-only capability，
不写 LoopX state，也不消耗 governed Turn quota。

### 五个身份把三本账对齐

一次 governed execution 不依赖“这段文本看起来像同一件事”，而是显式绑定：

```text
goal_id
agent_id
todo_id
turn_instance_id
effect_id
```

`effect_id` 来自 Turn settlement identity，并成为 provider lifecycle request 的稳定
`idempotency_key`。Capability binding、provider revision、request digest 与 `invocation_id` 又把
“调用了谁、调用什么输入”固定下来。它们共同连接三本账：

| Ledger | 保存什么 | 用来恢复什么 |
| --- | --- | --- |
| Governed journal | exact request、binding、phase、provider result、writeback/spend receipt | 本地进程中断后的事务 phase |
| Provider effect receipt | external ref、terminal status、evidence digest、idempotency key | 外部 effect 是否已提交 |
| Goal state + quota ledger | receipt digest、Todo/evidence transition、quota spend | LoopX 的 canonical lifecycle 与 accounting |

三本账不能互相冒充。HTTP 200 只证明服务响应；provider receipt 才证明远端 effect；receipt digest
成功写回后，LoopX 才允许 spend。Dashboard 或聊天摘要只是 projection，也不能替代其中任一本账。

### 为什么 `start` 与 `reconcile` 必须分开

同步函数常把“发请求、拿响应、写状态、扣 quota”塞进一次调用。这在网络和本地状态之间制造了
一个无法原子提交的窗口。Governed API 接受这个现实，并把窗口变成显式状态：

| Journal 状态 | 已知事实 | 合法下一步 |
| --- | --- | --- |
| 无 journal | Governed path 尚未 dispatch provider | 修复 preflight 后可用同一 Turn identity 重试 |
| `starting` | intent 已持久化；provider effect 可能已发生 | 用同一 idempotency key replay，不得生成新 identity |
| `running` | provider 已接受异步工作，但没有 terminal receipt | `reconcile` 同一 invocation，不得 spend |
| `ready_to_settle` | terminal effect receipt 已验证并持久化 | 只执行 writeback / spend settlement，不得重发 effect |
| `settlement_failed` | provider effect 已知；部分本地 phase 可能已提交 | 从 journal checkpoint 恢复剩余 phase |
| `committed` | writeback 与 quota receipt 均已提交 | replay 返回同一结果，不产生新 effect |

“无 journal”与“`starting` journal”尤其不能混淆。前者说明 provider dispatch 尚未从 governed API
发生；后者只说明 provider result 还没被本地保存，远端可能已经成功。正因为 intent 在 dispatch
前先落盘，恢复逻辑才知道应该复用原 effect identity，而不是猜测是否需要创建第二次调用。

### Exactly-once 的准确边界

Governed external execution 提供两种不同强度的保证：

1. **外部 invocation 是 idempotent replay。** LoopX 对同一 settlement effect 固定 request digest
   与 idempotency key；provider 必须让重复 start/reconcile 回到同一外部 effect。
2. **LoopX settlement 是 exactly once。** Writeback 与 quota spend 按有序 phase checkpoint；如果
   spend 首次失败，下一次 reconcile 只重试 spend，不重复已成功的 writeback。

它不可能单方面保证任意第三方网络服务“物理上只收到一次请求”。如果 provider 忽略
idempotency key，或者无法 read back 同一个 external ref，LoopX 会 fail closed，不能把这种服务
宣传成 exactly-once external effect。

同一个 `turn_instance_id` 也只能拥有一个 exact request。若调用方换了 operation 或 input，却想
借用原 Turn receipt，request digest mismatch 会在 provider dispatch 前失败。这防止“重试”暗中
变成另一项工作。

### 一个脱敏的长 Session 回放

设想一个远端 assistant service 支持图片输入和 resumable Session。我们要验证它既能理解第一轮
图片，又能在第二轮纯文本请求中引用上一轮上下文：

```text
Turn A (material)
  -> start: image + text + existing/new session reference
  -> provider terminal receipt
  -> reconcile: receipt writeback + one quota spend
  -> read-only observe: response marker appears in Session event stream

Turn B (material, same external Session, new Turn identity)
  -> start: text-only follow-up
  -> provider terminal receipt
  -> reconcile: second receipt writeback + one quota spend
  -> read-only observe: second marker appears and references prior context

Independent evidence refresh
  -> correlate Session readback, request trace, runtime revision and typed metadata receipt
  -> only then promote “resumable multimodal context works” from hypothesis to evidence
```

这里“同一 Session”不意味着复用同一 Turn。Session 拥有远端对话连续性；每个 material attempt
仍有自己的 Turn/effect identity、receipt 和 quota settlement。Read-only observe 则负责验证后置
条件，不为了凑治理形式额外扣一次 quota。

这条链路的威力在于，最终结论不是“Agent 说它测过”，而是四种独立证据可以互相校验：

- governed journal 证明哪一个 exact request 被允许并执行；
- Session readback 证明用户可见事件确实出现；
- trace/runtime evidence 证明请求经过哪个实际运行版本；
- typed domain receipt 证明被测能力对应哪个结构化配置或 metadata 决策。

任何一层冲突都应保持 `Unknown` 或让 Todo 继续 open，而不是用一个成功 marker 覆盖其余证据。

### API 组合而不是万能 Executor

Host adapter 的核心控制流可以压成下面的伪代码：

```python
started = start_governed_external_capability(
    goal_id=goal_id,
    agent_id=agent_id,
    todo_id=todo_id,
    turn_instance_id=turn_instance_id,
    capability_id=capability_id,
    operation=operation,
    provider_input=bounded_input,
    admission=quota_admission,
    execute=True,
)

settled = reconcile_governed_external_capability(
    invocation_id=started["invocation_id"],
    writeback=durably_record_effect_receipt_digest,
    spend=spend_one_exact_turn_slot,
)
```

真实 adapter 还应在 `running` 时按 provider follow-up contract 继续 reconcile，并通过 read-only
operation 验证 postcondition。LoopX 不理解图片、部署、issue 或文档评论等垂域 payload；它只验证
bounded request、binding、authority、receipt 和 settlement。领域逻辑因此可以留在外部仓库，
同时仍让 LoopX state 成为任务 lifecycle 的 ground truth。

### 审查 governed adapter 的失败矩阵

| 故障 | 错误做法 | 正确恢复 |
| --- | --- | --- |
| Binding/permission/Todo contract 在 dispatch 前失败 | 换一个随机 Turn 重试 | 修复配置，确认无 journal，再复用原稳定 identity |
| Provider timeout，journal 为 `starting` | 假设失败并创建新 job | 用相同 idempotency key replay，并 read back external ref |
| Provider 返回 `running` | 提前完成 Todo 或 spend | 保存 follow-up，继续 reconcile |
| Receipt 已有，writeback 失败 | 重发 provider operation | 只重试 settlement |
| Writeback 成功，spend 失败 | 再写一遍 Goal state | 从 checkpoint 只重试 spend |
| Receipt/readback/trace 冲突 | 选一个看起来成功的证据 | 保持 Todo open，刷新证据或 self-repair adapter |
| 相同 Turn 携带不同 request digest | 把它当普通 retry | fail closed，创建新的合法 Todo/Turn identity |

因此，governed execution 的核心不是“所有调用都经过 LoopX”这一句口号，而是让每个 material
external effect 都能回答：它属于哪个 Goal/Todo/Turn、provider 是否真的提交、LoopX 是否已写回、
quota 是否只结算一次，以及崩溃后下一步究竟是 retry provider 还是只 reconcile ledger。

## 先读 Feature Catalog

不要从 README 猜当前可配置能力。对已注册 goal 运行：

```bash
loopx --format json configure-goal --goal-id <goal-id>
```

不带 `--execute` 时是预览。当前 catalog 的重要能力都默认关闭：

| Feature | 解决什么 | 默认不做什么 |
| --- | --- | --- |
| `multi_subagent` | 允许 bounded child-agent use | 不创建 hierarchy，不绕过 claim/quota/gate/scope |
| `explore_graph` | 持久化探索 evidence topology 并可投影 | 不自动开启 harness/spawn |
| `explore_harness` | 对 todo/worker branch 做只读分析规划 | 不 claim、lease、launch、mutate、spend |
| `reward_memory` | 实验性 per-agent reward/experience memory | 不全局安装 provider，不自动 ingest/recall |
| `lark_event_inbox` | 可选飞书事件 inbox 配置 | 不自动授权、发消息或写 goal state |
| `peer supervisor` | 观察多个 peer 并记录 typed proposals | 不成为 leader，不默认执行 host effects |

配置规则：

1. 先 preview；
2. 阅读 exact delta 和 boundary；
3. 再显式 `--execute`；
4. 关闭 feature 时保留历史 evidence，只关闭未来行为；
5. feature gate 不等于 runtime permission。

## Explore Graph：探索证据层

Explore Graph 的 canonical 数据不是一张图，而是 append-only result log：

```text
goals/<goal-id>/explore-result-log.jsonl
```

事件 schema 为 `loopx_explore_result_event_v0`，三种 kind：

| Kind | 作用 | 代表状态 |
| --- | --- | --- |
| `node` | 问题、领域、假设、实验、artifact | open/exploring/blocked/resolved/dead_end |
| `edge` | 节点关系 | subtopic_of/depends_on/answers/supports/refutes/leads_to |
| `finding` | 发现 | tentative/confirmed/refuted |

写入示例：

```bash
loopx explore node \
  --goal-id <goal-id> \
  --node-id hypothesis_cache \
  --title "Cache repeated evidence reads" \
  --status exploring

loopx explore edge \
  --goal-id <goal-id> \
  --from hypothesis_cache \
  --type supports \
  --to reduce_startup_latency

loopx explore finding \
  --goal-id <goal-id> \
  --finding-id cache_hit_result \
  --title "Repeated read latency decreased" \
  --node hypothesis_cache \
  --status confirmed
```

记录时会执行 compact text、credential marker、absolute path 等边界检查。

### Projection

```bash
loopx explore summary --goal-id <goal-id>
loopx explore graph \
  --goal-id <goal-id> \
  --graph-format mermaid \
  --out explore.mmd
```

Projection 包含：

- latest node/edge/finding state；
- status counts；
- blocked reasons；
- current frontier；
- topology tree；
- Mermaid source。

Focused graph 只是 bounded evidence view，不删除 canonical evidence：

```bash
loopx explore graph \
  --goal-id <goal-id> \
  --status exploring \
  --status blocked \
  --tag executive \
  --graph-format mermaid
```

Executive view 是 derived display projection，必须保留 source lineage。不要为了“图小于 20 个节点”删除 material evidence。

### 开启 Graph

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --explore-graph-enabled \
  --no-explore-harness-enabled
```

确认 preview 后：

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --explore-graph-enabled \
  --no-explore-harness-enabled \
  --execute
```

Graph on、Harness off 是常见模式：保留探索拓扑，但不改变工作规划。

Material `refresh-state` 会折叠 canonical Explore evidence 并运行已配置 sink。Semantic digest 未变化时应 zero-write。外部 sink 失败/readback 失败会留下 retryable postcondition。

## Explore Harness：只读分支规划器

Harness 提供两个 planner：

```text
todo-branch-plan: 一个 branch 约等于一个候选 todo
worker-branch-plan: 一个 worker lane 可以包含一小组 todo
```

它们分析：

- candidate rank；
- confidence/expected evidence；
- write-scope conflicts；
- required capabilities；
- resource lanes/capacity；
- branch width；
- typed Explore evidence links；
- monitor exclusion；
- suggested claim/lease commands。

它们不做：

- claim todo；
- acquire lease；
- launch agent；
- mutate state；
- spend quota；
- 替代 `quota should-run`。

### 独立 Gate

Harness gate 只认注册 goal 的：

```yaml
spawn_policy:
  spawn_allowed: false
  max_children: 3
  explore_harness:
    enabled: true
    profile: adaptive-resilient
```

不能从第二个 registry key 或本地 prompt 获得隐藏授权。

配置：

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --explore-harness-enabled \
  --explore-harness-profile adaptive-resilient
```

确认后加 `--execute`。

行为表：

| Harness | Spawn | Planner 输出 |
| --- | --- | --- |
| disabled | 任意 | disabled packet，无 branches |
| enabled | false | analysis-only，suggested commands 为空 |
| enabled | true，max_children>0 | 仍为 dry-run，但可给 suggested claim/lease commands |
| enabled | true，max_children=0 | contradiction，退化为 analysis-only |

即使有 suggested commands，真正执行仍需普通 quota、claim、lease、workspace、gate 和 spend 生命周期。

### Profiles

#### `generic`

基础优先级/置信度和 scope-safe branch planning。

#### `adaptive-resilient`

增加：

- independent-lane admission；
- value-first packing；
- start staggering guidance；
- retry/backoff 和 infra-family cooldown hints；
- A/B metadata。

这些是 planner metadata，generic runtime 不会因为 profile 名称自动执行 retry loop。

#### `moe-router`

把 task family 当 expert、todo 当 token、worker lane 当 serving slot：

- per-family value/acceptance/infra EMA；
- UCB/coverage/bias 用于 routing order；
- confidence 和 novelty 用于 admission，避免 bias 污染价值估计；
- serial todo bundle 使用 confident-prefix；
- load profile 校准并行干扰；
- 有价值下限的 opportunistic expansion。

Router state 由 runner 在 epoch boundary 持久化。Harness 仍然只生成计划。

## Explore Graph 与 Harness 的组合

| Graph | Harness | 场景 |
| --- | --- | --- |
| off | off | 普通 LoopX goal |
| on | off | 记录和展示探索结果，不改变规划 |
| off | on | 临时分析 todo/worker portfolio，不保存 topology |
| on | on | evidence topology + advisory branch planning |

两者独立是重要安全边界：presentation 需要图，不应顺带允许更多 agent；分析 branch，不应自动写图或外部 sink。

## Single-Agent Auto ML：Graph、Harness 与 Domain Pack 怎样合起来

Single-Agent Auto ML 不是缩小版 Auto Research。它只有一个长期 agent lane，却要同时处理：

- 候选实现与代码 revision；
- 训练/评估 provider 的异步 task；
- short/long 等资源容量；
- matched baseline、数据窗口、primary metric 与 guardrail；
- 模型失败、基础设施失败和不可比结果的归因；
- promote、no-promote、retry、repair 与下一批探索。

这类系统的稳定性来自四个彼此独立的合同：

| 合同 | 负责什么 | 在当前公开实现中的位置 |
| --- | --- | --- |
| ML Experiment Domain Pack | metric/window/result/hypothesis/replan 的 typed advisory contract | `loopx/domain_packs/ml_experiment.py` |
| Explore Graph | 持久化 hypothesis、experiment、finding 与 evidence edge | `loopx/capabilities/explore/result_log.py` |
| Explore Harness | 基于 todo、Graph refs、scope、expected evidence 和 resource capacity 规划 portfolio | `loopx/capabilities/explore/worker_branch_plan.py` |
| Kernel | todo、claim、defer/resume、monitor、quota、gate、writeback、spend、scheduler | `loopx/control_plane/` 与 quota/status 入口 |

当前公开 ML Experiment pack 默认是 `suggest_only`，preview 明确返回
`launch_actions_enabled=false` 和 `production_actions_enabled=false`。真实 launch/poll/readback
由获得显式 goal boundary 与 effect authority 的 provider/extension 实现；课程案例不把任何
特定训练平台的 adapter 误写成 Kernel 能力。

### 一条端到端控制链

```mermaid
sequenceDiagram
  autonumber
  participant A as Single Agent
  participant H as Explore Harness
  participant K as Kernel
  participant P as Experiment Provider
  participant V as Validator
  participant G as Explore Graph

  A->>H: todos + graph refs + resource snapshot
  H-->>A: analysis-only portfolio + hazards
  A->>K: claim one candidate todo
  K-->>A: bounded delivery contract
  A->>P: authorized launch request + exact revision/window
  P-->>A: effect receipt + observable task handle
  A->>K: validated launch writeback
  K->>K: launch todo -> continuous monitor
  K-->>A: due monitor contract
  A->>P: one bounded poll
  P-->>A: terminal readback + compact artifacts
  A->>V: matched result request
  V-->>K: promote / no-promote / retry / repair proposal
  K->>G: append accepted finding + lineage
  G-->>A: updated evidence topology + refs
```

图中 `K->>G` 表示 material refresh 后追加已接受的 public-safe evidence，不表示 Kernel
把所有私有结果复制到 Graph。原始日志、凭据、内部 URL 和本机路径继续留在 provider
边界；Graph 只保存稳定 alias、结果分类和 typed relation。

### Harness 如何在单 Agent 场景发挥价值

单 Agent 不需要 Harness 启动 child。它最有价值的模式恰好是 `analysis_only`：

```text
candidate todos
  + Graph 中的 supports/refutes/depends_on
  + near-neighbor exclusions
  + required capabilities/write scopes
  + resource capacity and active usage
  -> ranked portfolio + blocked reasons + expected evidence
```

例如两个短实验槽已经占满时，Harness 可以继续比较下一批候选，却不能 claim 或 launch。
Kernel 把候选保留为 `deferred`，用 `resume_when=capacity_available:<lane>` 等待权威容量
readback。一个 external task 进入运行态后，普通 `continuous_monitor` 负责按 cadence
观察；monitor 本身不被误算为新的实验槽或新候选。

Harness 输出的三个层次也要区分：

| 输出 | 含义 | 后续 |
| --- | --- | --- |
| rank / expected evidence | 候选价值的 advisory estimate | Agent 可用于解释选择 |
| hazard / blocked reason | scope、capability、capacity 或 evidence 缺口 | Kernel todo/gate/defer 承接 |
| suggested command | 满足同一 goal boundary 时可显示的下一步命令 | 仍需普通 claim/lease/quota 执行 |

### Graph 如何让负向实验产生长期价值

只记录“当前最好分数”会让 Agent 重复尝试已失败的近邻方案。Explore Graph 应同时保留：

```text
hypothesis --leads_to--> experiment
experiment --supports/refutes--> finding
finding --depends_on--> matched contract
negative finding --rules_out--> near-neighbor family
diagnostic finding --depends_on--> provider/runtime condition
```

Comparable no-promote 是模型证据，可以收缩候选空间；infra failure 是诊断证据，只能形成
repair/retry，不应 `refutes` 模型假设。Harness 下一轮消费这些边界后，才能减少重复试验，
把资源留给真正增加信息量的候选。

### Promotion、Reward Memory 与系统能力演进

Graph finding 和 Harness ranking 都不是 promotion authority。Promotion 至少需要：

```text
exact candidate/revision/window
+ matched baseline
+ primary metric and guardrails
+ independent result attribution
+ applicable owner/release gate
+ activation readback and rollback path
```

Reward Memory 可以在相同 scope 内提示“某类方案过去常因哪个 guardrail 失败”或“评审者偏好
哪种证据表达”，但必须让当前 Graph/source state 胜出。它不能把旧 reward 变成 task
readback，也不能扩大 launch 或 promotion 权限。

若实验暴露的是系统能力缺口，例如需要新 reader、feature operator 或 evaluator，下一步
应创建独立 capability todo，通过代码验证、兼容性检查、版本化离线/在线 artifact、
release gate 与 rollback 后再激活。实验 todo 不能顺手改写并发布运行系统。这是
“算法路线 replan”和“系统能力演进”共享证据、分开 authority 的关键。

### Public-safe 最小实验

先运行默认关闭的 advisory preview：

```bash
loopx ml-experiment preview --format json \
  --experiment-id exp_preview_v1 \
  --primary-metric offline_metric \
  --baseline-value 0.421 \
  --candidate-value 0.437 \
  --guardrail-status clean \
  --train-window train_window_v1 \
  --eval-window eval_window_v1 \
  --hypothesis-id h_route_mix_v1 \
  --mechanism-family "candidate route mix" \
  --route route_mix \
  --positive-evidence offline_eval_delta_positive \
  --next-candidate holdout_eval
```

验证 packet 没有 launch/production authority。再对一个临时 goal preview Graph on 与 Harness
analysis-only，确认 planner boundary 中 `writes_state`、`claims_todos`、`acquires_leases`、
`starts_agents` 和 `changes_quota` 均为 false。这个实验验证组合边界，不需要真实训练任务。

## Multi-Subagent

`multi_subagent` 允许某个 executor 在 bounded todo 内调用 child agents，适合并行只读调查或隔离子问题。

它不应：

- 注册永久 hierarchy；
- 让 child 继承 parent 的全部 authority；
- 绕过 todo claim；
- 共享一个可写 worktree；
- 替 parent 伪造 aggregate evidence。

Parent 仍负责把 child result 压缩为 public-safe evidence，并完成当前 todo 的 validation/writeback。

## Peer Supervisor

开启方式：

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --supervisor-agent <registered-peer> \
  --supervised-agent <peer-a> \
  --supervised-agent <peer-b>
```

先 preview，再 `--execute`。Supervisor 提供 synthesis channel 和 typed proposals，不替代 decentralized todo/quota lifecycle。

适合：

- 比较多个 peer 的 evidence freshness；
- 发现 scope overlap 或失败分支；
- 提议 inject/handoff/discard；
- 给用户一个首选综合对话入口。

不适合：

- 成为永久 leader；
- 自己 launch/fork session；
- 直接完成其他 peer 的 todo；
- 把自然语言判断当 executed effect。

## Reward Memory

Reward memory 是 per-agent、default-off 的实验能力，目标是让经过验证的人类评价和工程经验
在后续 run 中被作用域化复用。它不是 LoopX canonical state 的替代品，也不是把聊天记录长期
注入模型。

| Memory class | 与 LoopX 当前状态的关系 | 允许产生的影响 |
| --- | --- | --- |
| `working_context` | 复用 registry、todo/quota、checkout 等 fresh context | 只服务当前执行或 session continuation |
| `run_bound_reward` | 绑定 exact goal/run 的评价 overlay | 作为候选证据，不直接改变 action set |
| `soft_preference` | 经 review 的 project/surface 偏好 | advisory ranking 或 rewrite |
| `procedural_experience` | 带 revision、provenance 和适用范围的经验 | 经当前 artifact 验证后影响诊断或验证计划 |
| `hard_policy` | 策略内容与独立验证的 authority scope 绑定 | 仅在已有 authority 范围内约束或否决 |

这里最重要的分离是：memory 可以影响模型怎样选择合法动作，不能决定哪些动作原本合法。
Gate/authority 先给出 action set，fresh state 给出当前事实，Reward Memory 再在匹配 scope 内
提供偏好或经验。Application receipt 记录哪条记忆被怎样使用，但不能冒充 delivery receipt。

安全边界：

- config pointer 放 ignored local state；
- 不自动安装全局 provider/dependency；
- 不自动 ingest 所有 transcript；
- 不自动把记忆注入 executor；
- 经验记录不能覆盖当前 todo/gate/evidence truth。

它可以帮助选择策略，但不能成为 authority source。

## Lark Event Inbox 与 Presentation Sink

Lark event inbox 是外部输入 connector 配置；Explore Lark Base 是展示 sink。两者不能混淆。

```text
connector: 外部事件、权限、source authority
projection sink: 把 public-safe LoopX read model 显示到 Lark
```

开启 inbox 不等于允许发送消息或自动写 goal。外部消息必须先经过身份、reply/mention relation、source authority 和 gate 检查，再转换为 bounded LoopX event/todo。

Sink 应保留 lineage：

```text
source_id
row_lifecycle
supersedes
superseded_by
record_id map
readback digest
```

## 实验：只读比较 Graph 与 Harness

### 1. 查看配置 catalog

```bash
loopx --format json configure-goal --goal-id <lab-goal>
```

### 2. Preview Graph on / Harness off

```bash
loopx --format json configure-goal \
  --goal-id <lab-goal> \
  --explore-graph-enabled \
  --no-explore-harness-enabled
```

### 3. Preview Harness analysis-only

```bash
loopx --format json configure-goal \
  --goal-id <lab-goal> \
  --explore-harness-enabled \
  --explore-harness-profile adaptive-resilient
```

不要加 `--execute`。比较两个 delta 的 source field 和 promised behavior。

### 4. 运行 disabled planner

```bash
loopx --format json explore worker-branch-plan \
  --goal-id <lab-goal> \
  --worker-width 3
```

验证 disabled packet 是否说明 `required_contract`，且没有 claim/lease/launch side effect。

## 核心代码领读：Explore 与 Auto ML 怎样复用 kernel

扩展层最重要的判断不是“功能多不多”，而是它有没有重新发明 todo、quota、lease、scheduler 或 evidence。下面沿真实配置与执行边界读。

### 1. Feature config 默认关闭，preview 与 execute 分离

`loopx/configure_goal.py` 把 Explore Harness 放在 goal 的 `spawn_policy` 内：

```python
spawn_policy = goal.get("spawn_policy") or {}
if explore_harness_enabled is not None:
    explore_harness["enabled"] = explore_harness_enabled
if clear_explore_harness_profile:
    explore_harness.pop("profile", None)
elif explore_harness_profile is not None:
    explore_harness["profile"] = explore_harness_profile

if explore_harness:
    spawn_policy["explore_harness"] = explore_harness
else:
    spawn_policy.pop("explore_harness", None)
goal["spawn_policy"] = spawn_policy
```

返回 packet 同时保留 `before`、`after`、`changed_fields`、`written` 和 configuration catalog：

```python
return {
    "dry_run": dry_run,
    "changed": bool(changed_fields),
    "before": before,
    "after": after,
    "written": bool(execute and changed_fields),
    "feature_summary": {
        "explore_graph": ... or {"enabled": False},
        "explore_harness": ... or {"enabled": False},
        "peer_supervisor": ... or {"enabled": False},
        "default": "off",
    },
}
```

因此开启功能的正确节奏是：先运行不带 `--execute` 的 preview，确认 delta 与 boundary，再显式 execute。

### 2. Harness gate 有三态，不是一个 enabled 布尔值

`loopx/capabilities/explore/harness_gate.py` 把 goal boundary 折叠成 planner gate：

```python
enabled = bool(harness_policy.get("enabled"))
spawn_allowed = bool(compact.get("spawn_allowed"))
max_children = max(0, int(compact.get("max_children") or 0))

if not enabled:
    return {
        "state": "disabled",
        "reason": "explore_harness_opt_in_required",
        "effective_width": 0,
    }

width_caps = [
    (requested, "requested"),
    (max(1, int(max_lanes)), str(max_lanes_label)),
]
if max_children > 0:
    width_caps.append((max_children, "max_children"))
cap_priority = {"max_children": 0, str(max_lanes_label): 1, "requested": 2}
effective_width, width_cap_source = min(
    width_caps,
    key=lambda cap: (cap[0], cap_priority[cap[1]]),
)
if not spawn_allowed:
    state, reason = "analysis_only", "spawn_not_allowed_by_goal_boundary"
elif max_children <= 0:
    state, reason = "analysis_only", "spawn_allowed_without_child_capacity"
else:
    state, reason = "commands_suggested", "goal_boundary_opt_in"
```

三态语义：

- `disabled`：连 lane planning 都不产生，只返回 required contract；
- `analysis_only`：可以排序、估计、比较，但不输出 claim/lease 命令；
- `commands_suggested`：可以建议命令，仍不执行命令。

### 3. Worker branch planner 永远是 read-only planner

`build_explore_worker_branch_plan` 的 docstring 和 disabled packet 都在强调同一件事：

```python
gate = resolve_explore_harness_gate(
    orchestration,
    requested_width=requested_width,
)
if gate["state"] == "disabled":
    return {
        "enabled": False,
        "selected_worker_branches": [],
        "boundary": {
            "writes_state": False,
            "claims_todos": False,
            "acquires_leases": False,
            "starts_agents": False,
            "changes_quota": False,
        },
        "required_contract": explore_harness_required_contract(...),
    }

branch_candidates, blocked_todos = _build_worker_branch_candidates(...)
if gate["state"] == "analysis_only":
    for branch in branch_candidates:
        branch["suggested_commands"] = []
        branch["commands_suppressed_reason"] = gate["reason"]
```

Planner 可以消费 router state、load profile、resource capacity，计算 branch bundle；但真正 claim、lease、launch 仍走通用 LoopX lifecycle。这样 Auto Research、issue-fix 或未来产品可以复用 planner，而不各自拥有一套 scheduler。

### 4. Explore Graph 是 evidence graph，不是 execution graph

Explore result log 先 canonical validate，再 append：

```python
def append_explore_result_event(path, event):
    validated = validate_explore_result_event(event)
    log_path = path.expanduser()
    with exclusive_file_lock(log_path):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(validated, ensure_ascii=False, sort_keys=True) + "\n"
            )
    return {
        "event_id": validated["event_id"],
        "result_id": validated["result_id"],
        "event_kind": validated["event_kind"],
    }
```

批量 append 还会按 `event_id` 去重：同 id 同内容可复用，同 id 不同内容报冲突。Node、Edge、Finding 记录的是探索结果、关系与证据 lineage，不会因为图上出现一条 edge 就自动创建 todo 或启动 worker。

`sync_explore_graph_after_material_refresh` 只在 material refresh 后把 result log 投影为 canonical graph/sink；它仍必须服从 external sink authority 和 readback postcondition。Graph 是观察/决策辅助层，不转移 quota、promotion 或 launch authority。

### 5. ML Experiment Pack 只做领域判断

`loopx/domain_packs/ml_experiment.py` 的 advisory builder 生成 typed result、dataset window、
hypothesis ledger 与 replan preview。默认 packet 同时写明：

```text
pack.enabled = false
pack.autonomy = suggest_only
launch_actions_enabled = false
production_actions_enabled = false
```

这使 Graph/Harness 可以消费 compact candidate evidence，又不会让识别出 ML 项目的普通 goal
静默获得训练或生产 effect。项目 provider 要增加 delivery authority，仍须经过 registry
goal boundary、quota、preflight、effect receipt 与 writeback。

### 6. 两个 Explore 功能怎样开启

Explore Graph 与 Explore Harness 可独立开启。推荐先 preview：

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --explore-graph-enabled \
  --explore-harness-enabled \
  --explore-harness-profile adaptive-resilient
```

确认 `after`、`changed_fields`、`feature_summary` 后再追加 `--execute`。若希望 Harness 输出可执行建议，还必须让同一个 `spawn_policy` 满足：

```text
spawn_allowed=true
max_children>0
explore_harness.enabled=true
```

否则它仍会停在 `analysis_only`。开启 Graph 不会自动开启 Harness，开启 Harness 也不会自动授权 external sink 或 worker launch。

### 7. 一条 extension 的最小审查路径

```text
configure preview
  -> goal_boundary.orchestration projection
  -> feature gate
  -> read-only planner
  -> normal claim/lease/quota lifecycle
  -> compact result event
  -> graph/status projection
  -> optional authorized sink + readback
```

如果一个扩展绕过中间任一步，尤其是直接从 private source 启 worker、直接由 graph edge 改 todo、或由 planner 自己 claim，说明它已经开始复制 control plane。若这条路径包含
`external_write`，还要在 provider effect 与 compact result event 之间插入 governed journal、
terminal effect receipt、durable writeback 和 quota settlement。

### 断点与检查问题

- `configure_goal.py:783`：preview 与 execute 的 before/after；
- `resolve_explore_harness_gate:83`：disabled/analysis/commands 三态；
- `build_explore_worker_branch_plan:871`：gate 如何限制 width 与 commands；
- `append_explore_result_events:517`：幂等与冲突；
- `build_ml_experiment_advisory_packet:655`：default-off advisory 与 effect authority 的边界；

读完应能回答：Graph 和 Harness 为什么独立、analysis-only 如何帮助单 Agent 管理昂贵
实验、ML pack 为什么不带 launch authority、supervisor 为什么不能借扩展层获得 durable
leader authority。

## 代码阅读路线

1. `loopx/configure_goal.py` 和配置 catalog
2. `loopx/capabilities/explore/README.md`
3. `loopx/capabilities/explore/`
4. `docs/product/domain-capability-packs.md`
5. `loopx/domain_packs/ml_experiment.py`
6. `docs/reference/protocols/peer-supervisor-v0.md`
7. `loopx/extensions/governed_capability_execution.py`
8. `loopx/control_plane/governed_capability.ts`
9. `loopx/control_plane/turn_driver/settlement.py`
10. `docs/reference/extensions.md`

## 代表性 Smoke

- `examples/project/configure-goal-smoke.py`
- `examples/explore-configure-goal-smoke.py`
- `examples/explore-worker-plan-gate-smoke.py`
- `examples/ml-experiment-domain-pack-smoke.py`
- `examples/control_plane/peer-supervisor-smoke.py`
- `examples/showcase-catalog-smoke.py`
- `tests/extensions/test_governed_capability_execution.py`

## 最终设计检查表

为一个新的 LoopX product preset 或 extension 回答：

1. 用户只需要提供哪些意图？
2. Preset 只拥有哪部分 domain semantics？
3. 哪些 mechanics 必须留在 generic kernel？
4. Feature 是否 default-off？
5. Preview 和 execute 是否分离？
6. Gate 是否只有一个 canonical source？
7. Planner 是否被误写成 executor？
8. External effect 是否有 capability + authority + receipt？
9. Material provider 是否以稳定 idempotency key replay，并把 start 与 reconcile 分开？
10. Receipt digest 是否在 quota spend 前 durable writeback？
11. Evidence 是否 compact、可追溯、符合 public/private boundary？
12. Focused smoke 是否证明 shipped behavior，而不是临时字段？

## 课程结束后的能力标准

完成第 1 到第 11 讲后，一个新开发者不需要记住所有 CLI 参数，但应该能够独立完成三件事：

1. 从 `$loopx <task>` 追到 registry、todo、quota、interaction contract、scheduler、refresh 和 spend；
2. 遇到卡住或矛盾状态时，判断是执行失败、projection gap、host drift 还是缺少用户 authority；
3. 为一个扩展写出 source、projection、decision、effect、receipt、validation 的完整边界，并知道什么时候不该实现；
4. 遇到 material external effect 中断时，能根据 journal phase 判断应 replay provider、继续
   reconcile，还是只恢复 writeback/quota settlement。
