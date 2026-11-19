# 第 9 讲：如何给 Control Plane 增加一条规则

> **本讲结论：** 一条 control-plane 规则必须闭合 source、decision、effect、receipt 与
> validation；没有真实 call site 和兼容合同的未来能力，应停留在文档或 todo。

建议时长：110 分钟。方法讲解 35 分钟、ordered-rule case 35 分钟、进阶 extension case 25 分钟、练习 15 分钟。

## 学习目标

完成本讲后，开发者应该能够：

1. 用 bounded context 和 state machine 定位一条规则的归属。
2. 写出规则的 source、projection、decision、effect、receipt 和 validation 链。
3. 区分 live runtime schema 与 migration reader。
4. 使用 characterization smoke 保护共享行为，再提取代码。
5. 判断一个未来扩展应该进入 production schema，还是只保留 design gate。
6. 把高风险隐式分支重构为有序、可审计、可反例验证的规则表。

## 本讲怎样使用三个 Showcase

新增规则前，先判断变化属于领域翻译还是通用 Kernel：

| 需求 | 应落在哪里 | 为什么 |
| --- | --- | --- |
| PR 出现 branch/merge blocker 时形成 `issue_fix_branch_or_merge_blocker_replan` successor | Issue-Fix Capability + Domain State | 这是 GitHub lifecycle observation 的领域含义 |
| 外部实验结果先检查 revision/window/baseline，再区分 model/infra outcome | ML Experiment Capability + Domain State | 可比性与 failure attribution 是领域规则 |
| Harness 根据 Graph refs 与 resource capacity 排序候选，但不得 launch | Explore Capability 的 read-only planner | planner 可以复用通用状态，不能拥有 effect authority |
| 所有普通工作关闭但 acceptance gap 仍开时触发 replan | Kernel ordered rules | 任何 capability 都需要相同 completion 语义 |
| Auto Research 增加一种 evaluator evidence | Auto Research Capability / Domain State | 新增领域事实和 validator，不改变通用 authority |
| promotion 必须经过 scoped user/reviewer decision | Kernel gate contract + preset declaration | gate、scope 与 authority 不能由 preset 私有实现 |

本讲先用 goal-frontier replan 展示怎样重构一条已经在线的 Kernel 规则，再用 Peer
Supervisor 展示怎样把新的 extension 停在最小边界。前者是主案例；不准备开发
supervisor 的读者也可以跳过第二个 case study，不影响规则工程方法。

## 先写行为链，不先写类

任何 control-plane feature 先回答：

```text
Shipped behavior / real call site 是什么？
Source of truth 在哪里？
谁有写权限？
Status 如何投影？
Quota 在什么 precedence 读取它？
Interaction contract 如何表达？
Host 是否需要新 capability？
执行成功的 receipt 是什么？
失败、重试、幂等、rollback 边界是什么？
哪个 smoke 证明真实行为？
```

如果只能回答“未来也许有用”，先放在设计文档或 todo，不要增加 public enum、CLI flag、event schema 和空 adapter。

## Bounded Context 地图

当前核心上下文包括：

```text
control_plane/agents
control_plane/goals
control_plane/handoff
control_plane/quota
control_plane/runtime
control_plane/scheduler
control_plane/todos
control_plane/work_items
```

归属原则是 change reason：

- agent identity/supervisor observation 属于 `agents`；
- todo lifecycle 属于 `todos`；
- interaction contract composition 属于 `runtime`/`quota`；
- cadence/ACK 属于 `scheduler`；
- repo/write-scope 选择属于 workspace/work item boundary。

大文件是 warning signal。先找一组已经有 parity fixture 的 cohesive rules，再移动到 bounded context。

## 新规则的 8 个层次

### 1. State definition

定义合法状态和非法状态：

```text
states
events
guards
transitions
terminal conditions
idempotency identity
```

优先使用 closed enum/schema，而不是散落 booleans。

### 2. Canonical source

配置写 registry，transition 写 event/API。不要让 status、dashboard 或 prompt 成为写源。

### 3. Read model

确定 status 是否需要投影：

- operator first screen；
- agent management；
- attention queue；
- task graph；
- compact evidence。

不要把 raw payload 直接放入公共 status。

### 4. Decision precedence

明确规则进入 quota pipeline 的位置。它是否早于 capability、晚于 gate，还是只影响 scheduler？

在 `docs/product/core-control-plane/rule-seam-map.md` 中记录输入、输出和相邻规则。

### 5. Interaction contract

把规则转换成 user/agent/CLI channel，不给 host 另一套自定义字段。

### 6. Host effect contract

如果需要外部动作，定义：

- required capability；
- explicit authority；
- typed request/result；
- dry-run；
- idempotency；
- receipt；
- rollback/compensation boundary。

### 7. Validation

Smoke 应验证 durable behavior，而不是临时 builder 的所有字段。

### 8. Documentation and migration

文档说明 live model、default、opt-in、failure behavior。Migration reader 保留旧输入的 exactly-once 转换，但不能让旧字段继续出现在 canonical output。

## Formal 在这里是什么意思

Control plane 中的 “formal” 不一定意味着马上写数学证明。它首先表示把自然语言意图变成可检查对象：

```text
intent
  -> typed state
  -> invariant
  -> guard
  -> transition
  -> proof obligation / receipt
```

例如：“只有所有后续都关闭才能停”可以写成不变量：

```text
terminal_no_followup =>
  open_todos = 0
  and due_monitors = 0
  and pending_successors = 0
  and replan_obligations = 0
  and acceptance_gaps = 0
  and retryable_postconditions = 0
```

普通 Python smoke 可以检查有限案例；property-based test 可以搜索更多组合；TLA+/Alloy 可以检查状态机；Lean 可以把状态、transition 和 theorem 写进依赖类型系统，并由 kernel 检查 proof term。

Lean 的价值是对少量高价值 invariants 给出机器证明，例如：

- 执行过的 supervisor action 必然有 capability-matched receipt；
- terminal state 不可能同时有 runnable successor；
- lease loss 后 transition 不能提交；
- proposal 不可被误投影为 executed。

不应尝试把整个 Python CLI 一次性翻译成 Lean。先让 schema、enum、transition helper 和 smoke 足够清晰，再选择最关键的不变量形式化。

## Case Study：从隐式 `if` 链到可审计 Ordered Rules

规则工程不只发生在“增加新行为”时。一个高风险 reducer 可能已经正确运行，却把优先级
散落在长函数的 early return 中。此时目标是**保持 public behavior 不变，同时让政策顺序
成为可检查对象**。

Goal-frontier replan 就是一个例子（见
[PR #2320](https://github.com/huangruiteng/loopx/pull/2320)）。它需要同时回答：已有 obligation 是否继续有效、handoff
gate 是否占据下一 transition、是否已有可运行 successor、当前 agent 的 vision 是否缺少
满足项、monitor-only frontier 是否耗尽。重构后的核心先冻结输入 facts，再执行 first-match：

```python
class GoalFrontierReplanRule(str, Enum):
    EXISTING_OBLIGATION = "existing_obligation"
    BLOCKING_HANDOFF_GATE = "blocking_handoff_gate"
    READY_DEFERRED_SUCCESSOR = "ready_deferred_successor"
    OPEN_USER_TODO = "open_user_todo"
    TODO_SUCCESSION_GAP = "todo_succession_gap"
    VISION_ACCEPTANCE_GAP = "vision_acceptance_gap"
    LONG_TODO_CHAIN = "long_todo_chain"
    CURRENT_AGENT_BLOCKER = "current_agent_blocker"
    MONITOR_NO_CHANGE_STREAK = "monitor_no_change_streak"
    NOT_MONITOR_ONLY = "not_monitor_only"
    NO_OPEN_MONITOR = "no_open_monitor"
    ADVANCEMENT_REMAINS = "advancement_remains"
    MONITOR_FRONTIER_EXHAUSTED = "monitor_frontier_exhausted"

@dataclass(frozen=True)
class GoalFrontierReplanFacts:
    blocking_handoff_gate_count: int = 0
    ready_deferred_successor_count: int = 0
    acceptance_gap_count: int = 0
    agent_advancement_count: int = 0
    monitor_only_lane: bool = False
    monitor_count: int = 0

def select_goal_frontier_replan_rule(facts):
    ordered_rules = (
        (GoalFrontierReplanRule.EXISTING_OBLIGATION,
         facts.existing_replan_required, False, "existing obligation"),
        (GoalFrontierReplanRule.BLOCKING_HANDOFF_GATE,
         facts.blocking_handoff_gate_count > 0, False, "handoff owns transition"),
        (GoalFrontierReplanRule.READY_DEFERRED_SUCCESSOR,
         facts.ready_deferred_successor_count > 0
         and not facts.successor_vision_required, False, "successor is runnable"),
        (GoalFrontierReplanRule.OPEN_USER_TODO,
         facts.user_open_count > 0, False, "user work owns frontier"),
        # succession gap, vision gap, long-chain, blocker and monitor-streak rules
        (GoalFrontierReplanRule.ADVANCEMENT_REMAINS,
         facts.agent_advancement_count > 0
         or facts.total_frontier_advancement > 0, False, "advancement remains"),
        (GoalFrontierReplanRule.MONITOR_FRONTIER_EXHAUSTED,
         True, True, "only monitor work remains"),
    )
    for rule, matches, derives_obligation, reason in ordered_rules:
        if matches:
            return GoalFrontierReplanRuleDecision(
                rule=rule,
                derives_obligation=derives_obligation,
                reason=reason,
            )
```

这里 `False` 决策和 `True` 决策同样重要。`BLOCKING_HANDOFF_GATE`、
`READY_DEFERRED_SUCCESSOR`、`OPEN_USER_TODO` 和 `ADVANCEMENT_REMAINS` 都是负向规则：
它们证明已有 transition owner 或可执行工作时，不应凭空创建 replan obligation。若只提取
“哪些情况要 replan”，会丢失这些抑制条件，改变 precedence。

重构按四层验证：

1. decision table 让每条 rule 都成为 first match，并故意叠加低优先级事实；
2. metamorphic test 把其他 agent 的 backlog 从 1 改成 2、8，当前 agent 的 vision gap
   仍不能被满足；加入当前 agent 自己的 runnable advancement 后，replan 必须消失；
3. quota smoke 串起 peer-only、own-frontier、monitor-only 三条真实路由；
4. quality surface catalog 将它标为 high risk，要求 unit、smoke、canary 和 release gate；
   模型行为层记为 `not_applicable`，因为确定性 precedence 不应交给模型裁判。

这个案例的评审重点是：facts 是否来自同一 canonical
projection、rule order 是否完整、每条负向规则是否有反例、runtime payload builder 是否
仍复用原实现。只有这四点成立，代码移动才是行为保持的重构。

## 进阶 Case Study：Peer Supervisor v0

### 研究来源

Shepherd 和 CooperBench 的公开研究给出一个直接启发：多个 worker 并行时，较强 supervisor 可以读取 effect stream，并可能执行以下动作。LoopX 对这组研究的产品化边界记录在 `docs/reference/protocols/peer-supervisor-v0.md`：

- `inject`：给现有 session 注入有界信息；
- `handoff`：让另一个执行者从某个版本化状态继续；
- `discard`：终止失败分支并保留证据。

LoopX 面临额外约束：

- peer 是 equal identity，不能恢复 primary/side hierarchy；
- LoopX 有 registry、todo、quota、gate、evidence 等丰富状态；
- host 能力不统一；
- user 可以和 supervisor 交互，也必须能直接和任一 peer 交互；
- supervisor transcript 不能成为隐藏 authority source。

### 最小 live feature

配置必须显式、default-off，supervisor 本身必须已经是 registered peer：

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --supervisor-agent <registered-agent-id> \
  --supervised-agent <peer-a> \
  --supervised-agent <peer-b> \
  --execute
```

Registry 保存：

```json
{
  "coordination": {
    "agent_model": "peer_v1",
    "supervisor": {
      "schema_version": "peer_supervisor_v0",
      "enabled": true,
      "agent_id": "<peer>",
      "supervised_agents": ["<peer-a>", "<peer-b>"],
      "execution_mode": "proposal_only"
    }
  }
}
```

关键是 `proposal_only`。

### Observation

```bash
loopx supervisor-prompt \
  --goal-id <goal-id> \
  --agent-id <supervisor-agent>

loopx supervisor-observe \
  --goal-id <goal-id> \
  --agent-id <supervisor-agent>
```

Observation 只折叠现有 public-safe projection：

- goal status / user gates；
- peer current todo、state、next action；
- workspace/handoff refs；
- thin evidence rows；
- compact effect refs。

它不运行别的 peer 的 quota，不读 raw transcript，不获得写权限。缺 peer status/evidence 时：

```text
decision_input_complete=false
warnings=[...]
```

### Closed decision set

当前 `SupervisorDecisionKind`：

| Kind | Required host capability | v0 状态 |
| --- | --- | --- |
| `observe` | none | 可记录 proposal |
| `inject` | `session_message_injection` | 有 opt-in Python host adapter canary |
| `handoff` | `session_state_fork` + `workspace_state_transfer` | proposal-only |
| `discard` | `session_termination` | proposal-only |

Decision 必须带 stable id、reason codes、evidence refs 和 conditional fields。

### Proposal 与 Receipt

```bash
loopx supervisor-event propose \
  --goal-id <goal-id> \
  --agent-id <supervisor-agent> \
  --decision-json <decision.json> \
  --execute
```

这只证明 supervisor 提出了建议。

Executed receipt 必须由 host adapter API 提供：

- verified capabilities，不从可编辑 JSON 自报；
- authority ref；
- compact evidence refs；
- rollback boundary；
- idempotent receipt id。

普通 CLI 只能记录 rejected/failed attempt，不能把 proposal 提升成 executed。

### User 与谁交互

Supervisor contract 推荐把 supervisor task 作为 synthesis channel，因为用户只需看一个综合视图。但：

- 用户仍可和任一 peer 直接交互；
- 所有改变 authority 的决定仍写成 LoopX user todo/gate；
- supervisor 不能 claim 其他 peer 的 todo；
- supervisor 不能花其他 peer 的 quota；

### Inject 为什么先有 canary

`loopx/control_plane/agents/supervisor_inject.py` 提供一个很窄的 Python seam：

```text
recorded proposal
  -> validate kind=inject
  -> validate host capability
  -> validate authority and rollback policy
  -> dry-run request
  -> adapter.inject(request)
  -> typed result
  -> durable executed receipt
```

重复执行先检查已有 receipt，避免 host 被调用两次。

它没有默认 adapter，也没有打开 CLI 自动注入开关。

## 为什么 Fork 还不是 Live Enum

Shepherd-style fork 合理，但当前只落在 future extension gate，因为没有完整 host/scheduler call site。

Fork 与 handoff 不同：

| Operation | Source 是否继续 | 资源影响 |
| --- | --- | --- |
| handoff | 通常完成或 yield | 转移 continuation，不默认增加 branch 数 |
| fork | 继续 | 新增临时 execution branch，需要 capacity reservation |

正确未来模型是：

```text
source_agent_id
source_state_ref (immutable)
execution_branch_id
executor_agent_id (existing idle registered peer)
branch_lease_id
```

调度器像 Raft 所描述的 persistent-agent workspace 一样，选择一个空闲、capability-matched 的现有 agent 执行 branch。它不能注册“clone-agent-2”，也不能复制 durable memory 成新身份。

最低 lifecycle：

```text
proposal_only -> admitted -> leased -> running -> held_result
                                                -> failed
                                                -> expired
held_result -> selected | discarded
```

Branch 完成不自动合并。`selected` 仍要经过普通 todo ownership、validation、review、merge 和 gate。

只有真实 scheduler/host adapter 能证明以下能力后，才应把 `fork` 加进 public enum：

- versioned execution state；
- copy-on-write workspace；
- idle peer selection；
- capacity reservation；
- branch lease；
- held result settlement；
- expiry/cancel/idempotency/partial failure recovery。

这就是 right-sized scope：文档把未来 invariant 讲清楚，但 production schema 不为没有 call site 的能力提前膨胀。

## Migration Reader 与 Live Model

升级兼容需要同时满足两个要求：旧版主/辅层级配置仍能被读取和迁移，迁移完成后的
live model 不再暴露这些层级字段。

正确分层：

```text
legacy input
  -> exactly-once migration detection/reader
  -> canonical peer_v1 config
  -> live output never emits hierarchy fields
```

错误方案：

- 删除 migration reader，导致旧用户无法升级；
- 在 live validation 中继续接受 hierarchy 字段，导致旧模型永久存活；
- 把 migration detection 的字段名误当作当前配置 UI。

Smoke 应同时证明：

- legacy fixture 能迁移；
- canonical registry 没有旧字段；
- runtime selection 始终是 `peer_v1`；
- supervisor overlay 不引入 hierarchy。

## 测试策略

本讲只保留规则开发的入口；完整分层、频率、模型行为门和发布门见[第 10 讲](10-autonomous-agent-quality-gates.md)。

测试先回答“这个状态和规则合理吗”，再验证实现是否符合。共享 quota/status/todo 行为可以先加 parity fixture 描述现状；如果现状与 independently reviewed invariant 冲突，应修规则并增加负向或 mutation coverage，不能刷新 golden 把错误合法化。

例如 gate lifecycle 至少要先写出这张语义表，再决定 schema 和 transition：

| 用户结果 | gate 问题是否结束 | required decision scope 是否满足 | blocked delivery |
| --- | --- | --- | --- |
| granted | 是 | 是 | 可按其余 guard 重算 |
| denied | 是 | 否 | 保持 blocked，或显式 supersede |
| cancelled | 是，原问题撤回 | 否 | 保持 blocked，或显式 supersede/defer |
| pending / undecided | 否 | 否 | 保持 blocked |

如果当前状态只能表达 `done=true`，却无法区分 granted 与 denied，那么缺的是状态模型；录制当前 `todo complete` 输出只会把歧义固化成金标。

一条跨层规则通常至少需要 unit/contract、focused smoke、public-safe decision replay 和 catalog-informed canary。Smoke 应覆盖交叉规则，而不只覆盖单概念 happy path。典型组合包括：

- due monitor 产生 gate 后又触发 replan，最终 scheduler identity 必须变化；
- non-blocking `user_action` 经过 compact reducer 后仍不能满足 `required_decision_scopes`；
- capability matched 但 workspace 不匹配时，只允许 workspace repair，不允许 normal delivery；
- todos 全 done 但 acceptance gap 或 active monitor 尚存时，terminal closure 必须为 false。

非平凡 LoopX 变更还需要：

```bash
loopx canary premerge --from-git-diff
```

一个 hand-picked smoke 不足以覆盖 runtime/quota/scheduler/todo/install/dashboard 边界。是否增加 output budget、actual-default model qualification、full-public 或 outcome baseline，由第 10 讲的风险门禁决定，不能让每个小 patch 都等待最高成本矩阵。

## 练习：设计一个 `pause_branch` Proposal

不要写代码，先写 contract：

1. 它与 `discard` 的语义差别是什么？
2. 需要什么 host capability？
3. Source state 和 branch identity 是什么？
4. Pause 后 lease 如何处理？
5. Resume 是否需要新 authority？
6. Proposal、attempt、executed receipt 如何区分？
7. 哪些状态下 fail closed？
8. 是否已有真实 call site？如果没有，应停在文档还是进入 enum？

最后用 scope-fit review 决定是否实现。合理答案很可能是先保持设计 proposal，不扩 public schema。

## 核心代码领读：以 peer supervisor 为例增加一条 control-plane 规则

这一节既是 supervisor 实现领读，也是 contributor 模板。先把一条危险规则拆成六层：

```text
config
  -> read-only observation
  -> normalized decision
  -> durable proposal event
  -> explicit host adapter effect
  -> durable receipt / projection
```

只要中间缺一层，就不能把 proposal 写成 executed。

### 1. 配置层只增加 opt-in overlay，不创造 leader 身份

`loopx/control_plane/agents/supervisor.py` 的 live enum 是：

```python
class SupervisorDecisionKind(str, Enum):
    OBSERVE = "observe"
    INJECT = "inject"
    HANDOFF = "handoff"
    DISCARD = "discard"

HOST_CAPABILITIES_BY_DECISION = {
    "observe": [],
    "inject": ["session_message_injection"],
    "handoff": ["session_state_fork", "workspace_state_transfer"],
    "discard": ["session_termination"],
}
```

`normalize_peer_supervisor` 的关键是身份约束：

```python
if raw in (None, {}) or raw.get("enabled") is False:
    return None
if agent_id not in registered_agents:
    raise ValueError("supervisor ... is not registered")
if agent_id in supervised_agents:
    raise ValueError("a supervisor cannot supervise its own agent session")
if not supervised_agents:
    raise ValueError("a supervisor requires at least one other supervised peer")

return {
    "enabled": True,
    "agent_id": agent_id,
    "supervised_agents": supervised_agents,
    "execution_mode": "proposal_only",
}
```

`supervisor` 是某个已注册 peer 的额外职责。默认空配置直接返回 `None`，所以实验功能不会默认开启。

### 2. Contract 明确 user channel 与 authority 边界

`build_peer_supervisor_contract` 里最重要的字段是权力模型：

```python
{
    "peer_authority": "equal_identity_authority",
    "supervisor_authority": "proposal_only",
    "user_interaction": {
        "recommended_channel": supervisor_agent_id,
        "user_may_interact_with_any_peer": True,
        "user_gates_remain_loopx_state": True,
    },
    "execution_policy": {
        "mode": "proposal_only",
        "missing_capability_behavior": "leave proposal unexecuted",
        "destructive_actions_require_explicit_host_authority": True,
        "durable_proposal_required": True,
        "host_receipt_required_for_executed_status": True,
        "proposal_is_execution_evidence": False,
    },
}
```

用户可以把 supervisor 当作首选汇总入口，但真正 gate 必须写回 LoopX state，其他 peer 才能看到同一 authority。

### 3. Observation packet 只消费 public-safe read models

`build_supervisor_observation_packet` 不读取原始 transcript：

```python
contract = build_peer_supervisor_contract(...)
attention = _goal_attention_item(status_payload, goal_id)
members = _agent_management_items(status_payload)

for agent_id in contract["supervised_agents"]:
    member = members.get(agent_id, {})
    evidence = evidence_logs.get(agent_id, {})
    peer_rows.append({
        "agent_id": agent_id,
        "state": member.get("state") or "unknown",
        "current_todo": member.get("current_todo"),
        "workspace_ref": member.get("workspace_ref"),
        "handoff_refs": list(member.get("handoff_refs") or [])[:4],
        "evidence": {
            "latest_rows": ledger[:3],
            "effect_refs": _evidence_refs(member, evidence),
        },
    })

return {
    "mode": "read_only",
    "decision_input_complete": not warnings,
    "peers": peer_rows,
    "boundary": {
        "raw_logs_included": False,
        "raw_trajectories_included": False,
        "raw_transcripts_included": False,
        "write_authority": "none",
    },
}
```

状态或 evidence 缺失时产生 warning，并把 `decision_input_complete` 置为 false。supervisor 不能用“我大概知道另一个 session 在干什么”补齐缺口。

### 4. Decision normalization 把四种行为变成可验证 schema

```python
kind = SupervisorDecisionKind(str(raw.get("kind") or ""))
decision_id = normalize_todo_claimed_by(raw.get("decision_id"))
if not decision_id:
    raise ValueError("decision_id must be a public-safe token")
supervised = normalize_registered_agents(supervisor.get("supervised_agents"))
result = {
    "decision_id": decision_id,
    "kind": kind.value,
    "reason_codes": _normalized_tokens(raw.get("reason_codes"), field="reason_codes"),
    "evidence_refs": _normalized_tokens(raw.get("evidence_refs"), field="evidence_refs"),
    "execution_status": "proposal_only",
    "required_host_capabilities": HOST_CAPABILITIES_BY_DECISION[kind.value],
}
if kind is SupervisorDecisionKind.OBSERVE:
    return result

target_agent_id = normalize_todo_claimed_by(raw.get("target_agent_id"))
if target_agent_id not in supervised:
    raise ValueError("target_agent_id must be one of the configured supervised peers")
result["target_agent_id"] = target_agent_id
if kind is SupervisorDecisionKind.INJECT:
    result["message"] = _required_text(raw, "message")
elif kind is SupervisorDecisionKind.HANDOFF:
    source_agent_id = normalize_todo_claimed_by(raw.get("source_agent_id"))
    if source_agent_id not in supervised or source_agent_id == target_agent_id:
        raise ValueError("handoff source and target must be different supervised peers")
    result["source_agent_id"] = source_agent_id
    result["state_ref"] = _required_text(raw, "state_ref", limit=240)
elif kind is SupervisorDecisionKind.DISCARD:
    result["state_ref"] = _required_text(raw, "state_ref", limit=240)
```

这里的 `handoff` 是“等待 source 形成可引用状态，再建议 target 从该 state 继续”；`discard` 是“建议 host 终止失败分支”。二者都要求具体 state ref，不能只凭模型印象操作 session。

### 5. Proposal 与 receipt 是两种不同事件

`supervisor_events.py` 先把 decision 写成 durable proposal：

```python
normalized = normalize_supervisor_decision(decision, supervisor=supervisor)
return make_state_event(
    event_id=f"supervisor-proposal-{decision_id}",
    event_type=SUPERVISOR_PROPOSED,
    payload={"decision": normalized},
    privacy=LOCAL_PRIVATE_PRIVACY,
)
```

host receipt 则必须引用 proposal，并在 `outcome=executed` 时证明 capability、authority 与 rollback boundary：

```python
missing = set(required_capabilities) - set(host_capabilities)
if outcome is EXECUTED:
    if missing:
        raise ValueError("executed receipt is missing required host capabilities")
    if not authority_ref:
        raise ValueError("executed receipt requires authority_ref")
    if rollback_boundary is None:
        raise ValueError("executed receipt requires rollback_boundary")
```

同一个 `decision_id` 的 proposal 可幂等重放；receipt 不能在没有 proposal 的情况下出现。这是 effect attribution 的最小 proof boundary。

### 6. 当前真正落地的 effect 只有 inject adapter

`loopx/control_plane/agents/supervisor_inject.py::execute_supervisor_inject` 展示了完整 effect 路径：

```python
proposal_event = _proposal(log_path, goal_id=goal_id, decision_id=decision_id)
decision = proposal_event["payload"]["decision"]
if decision.get("kind") != "inject":
    raise ValueError("... only accepts inject proposals")
if decision.get("required_host_capabilities") != ["session_message_injection"]:
    raise ValueError("inject proposal capability contract is not canonical")

if prior is not None:
    return {"host_called": False, "already_executed": True, ...}
if not execute:
    return {"host_called": False, "would_execute": True, ...}

result = adapter.inject(request)
receipt = record_supervisor_receipt(
    receipt={
        "decision_id": decision_id,
        "authority_ref": authority_ref,
        "rollback_boundary": adapter_contract["rollback_boundary"],
        ...,
    },
    host_capabilities=adapter.capabilities,
    execute=True,
)
```

截至本讲对应源码版本：`inject` 有专用 host adapter；`handoff` 与 `discard` 已有 proposal schema 和 capability contract，但没有等价的通用执行 adapter。因此讲义只能说“可提出 handoff/discard”，不能声称 LoopX 已能跨任意 host 自动 fork 或 kill session。

### 7. 用这套顺序评审任何新规则

```text
先定义 source -> projection -> decision -> effect -> receipt
再问 active call site 在哪里
再写 decision table / focused smoke
最后才扩 enum、CLI 或 public protocol
```

若想加入 Shepherd 风格 `fork`，当前更合理的语义是让调度器把临时 execution branch 分配给空闲 peer；它不创建新的长期 agent identity。没有真实 allocator/lease/cleanup call site 前，应停在设计或 todo，不扩一个看起来已经可执行的 public enum。

### 断点与检查问题

- `normalize_peer_supervisor:72`：未注册/self-supervision/default-off；
- `build_supervisor_observation_packet:250`：缺 status/evidence 的 warning；
- `normalize_supervisor_decision:366`：四种 kind 的 conditional fields；
- `normalize_supervisor_receipt:153`：executed proof 三件套；
- `execute_supervisor_inject:145`：幂等、dry-run、真实 host call 三条路。

读完应能回答：proposal 为什么不是 effect、receipt 为什么需要 rollback boundary、handoff 为什么仍是 proposal-only、fork 为什么应复用空闲 peer 而不是复制身份。

## 代码阅读路线

1. `docs/product/core-control-plane/bounded-context-layout.md`
2. `docs/product/core-control-plane/rule-seam-map.md`
3. `docs/product/core-control-plane/state-machine.md`
4. `docs/reference/protocols/peer-supervisor-v0.md`
5. `loopx/control_plane/agents/supervisor.py`
6. `loopx/control_plane/agents/supervisor_events.py`
7. `loopx/control_plane/agents/supervisor_inject.py`
8. `loopx/control_plane/agents/runtime_model.py`
9. `loopx/control_plane/goals/goal_frontier/replan_rules.py`
10. `tests/control_plane/test_goal_frontier_replan_rules.py`
11. `examples/control_plane/goal-frontier-replan-rules-smoke.py`

## 课后检查

1. 新规则为什么要先定义 source 和 receipt？
2. Proposal 与 executed effect 的 proof boundary 是什么？
3. Fork 为什么不能复制成新的长期 peer identity？
4. Migration reader 为什么应保留，但旧字段不能继续出现在 live config？
5. 哪类 invariant 值得未来用 Lean/TLA+，哪类只需 focused smoke？

下一讲把这套工程顺序扩成自主交付门禁：什么必须进入 PR 快速门，什么适合 canary、低频模型行为验证或 release gate，以及 agent 何时必须停止自动合并。扩展层顺延到第 11 讲。
