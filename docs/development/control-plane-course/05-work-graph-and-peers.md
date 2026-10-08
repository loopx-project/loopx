# 第 5 讲：Todo 工作图与 Peer 协作

> **本讲结论：** Todo 定义工作生命周期；claim、lease、capability、workspace 和 gate 是五种
> 不同约束；handoff 传递可恢复 frontier，不创造永久 leader。

建议时长：100 分钟。讲解 60 分钟、状态机推演 20 分钟、实验 20 分钟。

## 学习目标

完成本讲后，开发者应该能够：

1. 区分 agent todo、user gate、user action、monitor 和 blocker。
2. 解释 claim、task lease、workspace guard 和 capability gate 的不同职责。
3. 正确建模 blocked P0 与可执行 P1/P2 的并存。
4. 使用 successor、supersede、continuation policy 和 no-followup 关闭工作图。
5. 解释为什么当前运行时只有 equal peer，没有 primary/side 概念。
6. 区分普通 claim、显式 lifecycle authority 和 bounded material handoff。

## 本讲技术契约

| 边界 | LoopX 中的答案 |
| --- | --- |
| State owner | Todo lifecycle contract 拥有 work item、gate、successor 与 closure |
| Turn snapshot | 每个 peer 读取按 agent、capability、workspace 和 gate 过滤后的 frontier |
| Effect owner | 获得合法 route 的 peer 在允许的 workspace 执行 bounded work |
| Commit receipt | Completion 必须绑定 evidence，并产生 successor、handoff 或 `no_followup` |
| Recovery | Handoff 保存 material frontier 和 lineage，不复制完整 transcript |

## 从三个 Showcase 看同一类工作图

Issue-Fix、Single-Agent Auto ML 和 Auto Research 的 todo 文本不同，但都不是线性 checklist：

```text
Issue-Fix
  feasibility
    -> fix_pr
    -> pr_monitor
         -> ci_failure_replan | review_changes_replan
         -> branch_or_merge_blocker_replan | no_followup

Single-Agent Auto ML
  experiment_contract
    -> candidate_preflight
    -> launch
    -> task_monitor
         -> evaluate
         -> promote | no_promote | retry | replan

Auto Research
  research_contract
    -> hypothesis
    -> dev_experiment
         -> holdout | retry | retirement | promotion_gate
```

图中的箭头不是模型随口建议的“下一步”，而是带 lineage 的 successor transition。三条图
都需要回答相同问题：

| 问题 | Issue-Fix | Single-Agent Auto ML | Auto Research |
| --- | --- | --- | --- |
| 谁可领取？ | 具备 repo/write scope 的 peer | 具备实验 capability、resource route 的同一 peer | 具备对应 role/capability 的 peer |
| 什么只是等待？ | checks pending 的 PR monitor | 外部 task monitor 或容量恢复条件 | 等待新证据或未到期 evaluator poll |
| 什么会派生 successor？ | CI failure、changes requested、branch/merge blocker | terminal metric、infra diagnosis、no-promote result | dev lift、negative evidence、retryable attempt |
| 什么需要 gate？ | 发布、权限或风险决定 | provider launch、promotion、online activation | promotion、protected evaluator 或边界改变 |
| 什么才算终局？ | merged/closed 且 no-followup 成立 | acceptance 满足且没有候选、运行、monitor 或 replan frontier | acceptance 满足且没有 runnable/retry frontier |

有了这张图，后面的 claim、lease、gate 和 handoff 就不再是五组独立名词，而是筛选和推进
同一个 frontier 的不同约束。

Explore Graph 与 Harness 不会给这张工作图增加一种 lifecycle。Graph edge 解释某个候选为何
来自或反驳另一个候选；Harness branch 解释哪些 todo 可以在 scope 和容量上组成更有价值的
portfolio。只有显式 `todo add/claim/defer/complete/supersede` 才改变工作图。单 Agent 场景
同样需要这条边界，否则 planner 排名变化会被误当成任务已经重排或启动。

## 从 Todo 列表升级为工作图

普通 checklist 只有文本和完成状态。LoopX todo 还承载路由语义：

```text
identity + role + priority + task_class + action_kind
+ claimed_by / lease
+ required_capabilities
+ required_write_scopes / task_repository
+ decision scopes / gate links
+ successor / supersede / resume condition
+ evidence / completion rationale
```

因此 todo 集合不是按顺序执行的脚本，而是当前 goal 的可计算 frontier。

## 两类 Owner 与五类 Task Class

### Todo role

| Role | 含义 |
| --- | --- |
| `agent` | 可由注册 peer 推进的工作 |
| `user` | 需要用户判断或行动的工作 |

### Task class

| Task class | 是否可执行 | 用途 |
| --- | --- | --- |
| `advancement_task` | 是 | 直接推进目标的实现、研究、验证、文档 |
| `user_gate` | 否，由用户解决 | 阻塞某个 scope 的方向、权限或风险决策 |
| `user_action` | 否，由用户解决，但可不阻塞 agent | 用户可见的非阻塞行动 |
| `continuous_monitor` | 仅到期时做一次只读 poll | 持续等待 PR、证据或外部状态 |
| `blocker` | 否 | 已知阻塞事实和恢复条件 |

最常见错误是把所有等待都写成 `advancement_task`，导致 quota 不断唤醒 agent 去“再看一下”。Monitor 和 blocker 必须有不同的调度语义。

## Priority 不是隐藏工作流

Todo 文本通常带 `[P0]`、`[P1]`、`[P2]`。Priority 影响候选排序，但不是唯一决策：

```text
priority
+ gate scope
+ claim/lease
+ capability availability
+ workspace compatibility
+ resume condition
+ peer exclusions
= runnable frontier
```

所以一个 P0 可以 blocked，同时一个完全独立的 P1 仍然 runnable。

### Blocked P0 的正确模型

```text
P0 agent todo requires decision scope X
  <- user_gate provides X

P1 agent todo does not depend on X
```

quota 可以同时返回：

- `user_channel.action_required=true`，列出具体问题 X；
- `agent_channel.must_attempt=true`，要求推进 P1；
- `safe_bypass_allowed=true`，说明旁路边界。

这不是矛盾，而是双通道控制。

## Equal Peer Runtime

当前 live runtime model 是 `peer_v1`：

- 所有 registered agents 是平等身份；
- 没有永久的主执行者；
- 没有永久的辅助执行者；
- todo claim 和 continuation policy 决定当前工作归属；
- task-scoped coordination 不授予 durable authority；
- 旧 hierarchy 字段只允许存在于 exactly-once migration reader。

对应实现是 `loopx/control_plane/agents/runtime_model.py`。它只暴露 `AgentRuntimeModel.PEER_V1`。

为什么这样设计？

如果把“主/副”写成长期身份，容易把 review、merge、quota 和 user gate 权限隐式绑定到某个人。Peer 模型要求每次权力都来自当前 todo、明确 policy 或用户 gate，便于审计和替换 executor。

## Claim：软所有权

```bash
loopx todo claim \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --claimed-by agent-a
```

Claim 的作用是减少重复劳动，告诉其他 peer 当前谁在推进。

它不表示：

- agent-a 获得 repo 的无限写权限；
- agent-a 仅凭 claim 就能修改另一个 peer 已 claim 的 todo；
- agent-a 可以越过 user gate；
- agent-a 永久拥有 successor；
- 其他 peer 不能做独立 todo。

Claim 可以过期、清除、被 completion/supersede 结束。Status 会投影 stale claim 提示。

## Lifecycle Authority：显式委托不等于 Leader

Equal peer 的默认规则是：一个 agent 不能修改另一个 peer 已 claim 的 todo。但
orchestration 或 meta agent 有时确实需要完成、转交或废弃停滞工作。正确做法不是恢复
永久 leader，而是在 goal 上声明动作级授权：

```yaml
coordination:
  todo_lifecycle_authority:
    - agent_id: codex-main-control
      actions: [complete, reassign, supersede]
      requires_reason: true
```

生命周期决策由
`loopx/control_plane/coordination/todo_lifecycle_decision.ts` 拥有。多 peer 路径先
检查注册、exclusion 和 binding；只有 claim owner 不一致的拒绝可由动作级 grant 处理：

```typescript
const rejection = registeredTodoMutationRejection(todo, actor, registered);
if (rejection !== null && rejection !== "claim_owner_mismatch") {
  return result("rejected", rejection);
}
if (rejection === "claim_owner_mismatch") {
  const grant = request.lifecycle_grants.find((candidate) => candidate.agent_id === actor);
  if (grant === undefined) return result("rejected", "claim_owner_mismatch");
  if (!grant.actions.includes(request.authority_action)) {
    return result("rejected", "delegation_action_not_granted");
  }
  if (grant.requires_reason && !String(request.authority_reason ?? "").trim()) {
    return result("rejected", "delegation_reason_required");
  }
  return { mode: "delegated_orchestration_override", ownershipGate: "not_required" };
}
```

这是同一 `authority` 决策中的片段，不是新的调用接口。未晋升源适配器
`mutation_authority.py::authorize_todo_lifecycle_mutation` 仍在 owner mismatch 后
读取并规范化 registry grant，再通过 `authority_core.decide` 调用同一 TS owner；
canonical 事务也复用该 owner。Python facts、grant 校验和 receipt 投影保留，
不再经由无人调用的单项 grant 查询。

这份 grant 有四个边界：

1. 只授予列出的 lifecycle action，不授予 repo、quota 或 user decision 权限；
2. actor 必须是当前 goal 的 registered agent；
3. `excluded_agents`、`bound_agent` 和精确 user-gate scope 仍独立生效；
4. reason 进入 mutation receipt，使越权操作可追溯。

因此它表达的是一项可撤销的 goal-local capability，而不是 agent 之间的持久层级。

## Lease：显式、可选的执行占用

Task lease 比 claim 更适合需要排他执行或外部资源的工作：

```text
claim: 谁打算负责这个 todo
lease: 谁在一段 TTL 内占用这个执行机会或资源
```

Lease 丢失应 fail closed。一个 worker 不应该在 lease 过期后继续静默执行，再把结果写进 canonical state。

当前 `task_lease_v0` 是显式调用方使用的可选并发原语，支持 TTL、版本 CAS、
续租、转移、释放和 write-scope 冲突检查。普通 `todo claim`、
`quota should-run` 和 agent turn 不会自动 acquire task lease。因此：

- `claimed_by` 已经是默认 todo 路由合同；
- task lease 只在真实并发冲突或排他资源需要时启用；
- status 中的 `soft_claim` 展示不能反向解释成 hard lease；
- handoff 不要求先创建 lease，lease 也不证明 handoff 已完成。

一旦启用 hard lease，执行凭据是 acquire 返回的
`(idempotency_key, version)` 完整二元组；续租、转移、释放与 terminal
writeback 都必须提交当前 version。Release 会保留 inactive terminal record，
使下一代 acquire 的 `version` 与 `lease_epoch` 单调前进，而不是回到 1。

实现边界可以直接由调用图证明：产品 CLI 在
`loopx/cli_commands/task_lease.py` 调用 native acquire transport，transaction owner
是 `loopx/control_plane/work_items/task_lease_acquire.ts`，而不是 todo claim 或 quota
pipeline。`acquire_task_lease()` 只保留 transport compatibility。生命周期与幂等合同
由 native TS tests、`tests/control_plane/test_task_lease.py` 和
`examples/control_plane/task-lease-runtime-smoke.py` 看护。

第 9 讲介绍的 supervisor 目前只产生观察和提议。若未来增加 supervisor 建议的
temporary execution branch，对应 branch lease 也只能授权该分支执行，不能继承
source todo、source quota 或 source durable memory。

## Handoff 是恢复协议，不是执行动作

Handoff 首先要回答一个可验证的问题：换掉当前 session 或 peer 后，长期任务能否
只依赖 durable state 恢复同一决策面。为了隔离恢复能力，资格测试把一次 handoff
建模成“没有新增业务证据”的恢复轮：当前 worker 停止使用临时上下文，下一 peer
重新读取项目状态和环境。

```text
P = decision-relevant project projection
H(P, fresh environment) = projection reconstructed by the next peer

no new evidence 时：distance(H(P), P) < epsilon
```

固定点检查比较 objective、authority source、validation surface、open frontier、
gate、claim boundary、next action 与 stop condition，而不比较逐字 transcript。
连续多次 handoff 仍保持这些字段等价，才说明长期状态不依赖某个 worker 的偶然
记忆。

当前实现把“状态可恢复”和“已经开始下一轮业务执行”明确分开。
`loopx/control_plane/work_items/project_asset.py::project_asset_handoff_check_projection`
先检查 handoff surface：

```python
checks = {
    "project_asset_backed": True,
    "same_source_should_run": bool(
        quota and next_action and (not item_action or item_action == next_action)
    ),
    "codex_ready": waiting_on == "codex" and quota_state == "eligible",
    "handoff_has_next_action": bool(next_action),
    "handoff_has_stop_condition": bool(stop_condition),
    "handoff_sanitized_surface": project_asset_summary_is_public_safe(project_asset),
}
```

随后 `loopx/control_plane/handoff/project_handoff.py::project_asset_handoff_state`
才区分等待与真实后续执行：

```python
if post_handoff_run:
    handoff_status = "post_handoff_run_seen"
elif ready:
    handoff_status = "ready_waiting_for_run"
else:
    handoff_status = "not_ready"
```

收到 handoff packet 只表示 agent 可以重建待接手的工作面。真正运行仍要重新通过
quota、gate、capability、claim/lease 和 workspace guard；packet 本身不触发业务
执行。`ready_waiting_for_run` 表示恢复条件已经满足，但尚未观察到后续 work run。

阅读 `examples/project/project-handoff-readmodel-smoke.py` 时重点看两个反例：

1. handoff surface 完整，但没有 post-handoff run，应保持
   `ready_waiting_for_run`；
2. status-neutral run 不得冒充业务进展，只有真实后续 work run 才切换到
   `post_handoff_run_seen`。

连续 N 次 handoff 可以作为低频状态稳定性资格测试，但不是日常执行流程，也不
能用“漂移分数低”替代任务的 artifact、validation 或 outcome evidence。

### Handoff 携带 Material Frontier，不复制材料正文

仅恢复 todo 还不够。若下一 peer 不知道当前决策依赖哪些规范、设计文档或证据，它仍会
从聊天记录里猜上下文。LoopX 先由 authority registry 推导 agent 的 material frontier，
再把它压缩成 bounded handoff projection：

```python
MAX_MATERIAL_HANDOFF_REFS = 4

def project_material_frontier_for_handoff(frontier):
    counts = {state: 0 for state in _FRONTIER_STATES}
    material_refs = []
    seen_ids = set()
    for item in frontier["items"]:
        if item["material_id"] in seen_ids:
            continue
        seen_ids.add(item["material_id"])
        counts[item["state"]] += 1
        if len(material_refs) < MAX_MATERIAL_HANDOFF_REFS:
            material_refs.append({
                "material_id": item["material_id"],
                "relation": item.get("relation"),
                "purpose": item.get("purpose"),
            })
    return {
        "summary": {
            "required_count": len(seen_ids),
            "current_count": counts["current"],
            "stale_count": counts["stale"],
            "missing_count": counts["missing"],
            "inaccessible_count": counts["inaccessible"],
            "required_unread_count": counts["required_unread"],
        },
        "material_refs": material_refs,
        "material_refs_truncated": len(seen_ids) > len(material_refs),
    }
```

投影保留 `current`、`stale`、`missing`、`inaccessible`、`required_unread` 的计数和至多
四个稳定 material id；材料正文、原始 transcript、凭证和本机路径不进入 packet。接手者
先按 id 从 canonical source 重新读取，再判断材料是否 current。这样 handoff 传递的是
“恢复决策面所需的引用与缺口”，不是一份越来越大的 session memory 副本。

## Capability Gate：能做不等于被授权

Todo 可以声明：

```bash
--required-capability shell
--required-capability filesystem_write
```

Quota 调用方声明本轮可用能力：

```bash
loopx --format json quota should-run \
  --goal-id <goal-id> \
  --agent-id agent-a \
  --available-capability shell \
  --available-capability filesystem_write
```

Capability 是 preflight 条件，不是 permission：

- 有 `network` 不代表可以上传私有材料；
- 有 `filesystem_write` 不代表可以写任意 repo；
- 有 `session_termination` 不代表 supervisor 可以随意终止 session；
- 有 `benchmark_runner` 不代表可以提交 leaderboard。

权限仍来自 goal boundary、user gate、workspace policy 和 host authority。

未知或非 owner-held 的缺失能力会作为 `repair_missing` 暴露给 agent，而不是
直接跳过。Agent 必须自己判断能否满足：例如独立 worktree/branch 可以由当前
runtime 自证，就执行 repair 并写回 `capability_gap_status=fixed`；不能证明就写
blocker。分支/worktree 的最终约束仍由 `--task-repository`、
`--required-write-scope` 和 workspace guard 执行，不要在 capability gate 里
维护能力名清单。

`target_capability` 则表示 todo 正在构建或修复什么能力，不是执行这个 todo 的硬前提。

## Workspace Guard：把写入位置变成状态

对于 repo 写入，todo 应声明：

```bash
--task-repository git:github.com/example/project
--required-write-scope 'src/**'
```

工作 peer 需要使用与 task repository 匹配的独立 worktree/branch。共享 base checkout 可以作为只读输入，但多个 agent 不应在同一个脏工作树中并发写。

Workspace guard 与 capability gate 的分工：

| Guard | 回答的问题 |
| --- | --- |
| Capability | 当前 executor 是否具备需要的工具能力？ |
| Workspace | 当前执行边界是否匹配 repo 和写 scope？ |
| User gate | 是否获得了需要的决策或风险授权？ |

## Gate 的 Scope

一个 `user_gate` 应明确：

- 具体问题；
- decision scope；
- 阻塞哪个 agent 或 todo；
- 是否真的是 global gate；
- 完成后 unblocks 哪个 todo。

示例：

```bash
loopx todo add \
  --goal-id <goal-id> \
  --role user \
  --text "确认是否允许修改 public API" \
  --task-class user_gate \
  --decision-scope direction:action:public_api_change \
  --blocks-agent agent-a \
  --unblocks-todo-id <todo-id>
```

Agent todo 则声明需要这个 scope：

```bash
loopx todo update \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --required-decision-scope direction:action:public_api_change
```

只有真正影响所有 peer 的 gate 才使用 `--global-gate`。不要因为不确定就扩大阻塞面。

在 multi-agent goal 中，`blocks_agent` 和 `global_gate=true` 都缺失时，语义不是
“默认全局阻塞”，而是“authority scope 不完整”。把不确定性扩大成全局 gate 会让一个
局部决策冻结所有独立 lane，也无法从状态解释用户当时究竟授权了什么。Quota 因此把它
投影为有界修复：

```python
if len(registered_agents) > 1:
    for gate in gates:
        if normalize_todo_global_gate(gate.get("global_gate")):
            continue
        if normalize_todo_blocks_agent(gate.get("blocks_agent")):
            continue
        errors.append({
            "reason_code": "multi_agent_user_gate_missing_scope",
            "user_todo_id": normalize_todo_id(gate.get("todo_id")),
        })
```

`build_required_decision_scope_repair_hint` 再把错误编译成
`todo_decision_scope_projection_repair`。修复只能选择三种明确结果：绑定一个 registered
agent、显式设为 `global_gate=true`，或把它降级为不阻塞的 `user_action`。修复完成前关闭
normal delivery，但不伪造一项新的用户决策。

这条规则至少需要两个相邻反例共同看护：

```text
unscoped gate in multi-agent goal -> control_plane_self_repair
explicit global_gate=true         -> user_gate，所有 peer 被阻塞
```

只保留第一条测试会把“修复歧义”误实现成“永不支持全局 gate”；只保留第二条又可能让
缺失 scope 静默扩大权限。公开回放位于
`examples/control_plane/quota-agent-scoped-user-gate-smoke.py`。

## Resume Condition

Deferred todo 必须说明何时恢复：

```bash
--resume-when todo_done:<todo-id>
--resume-when pr_merged:<pr-ref>
--resume-when capacity_available:<pool>
```

一个没有 machine-readable resume condition 的 deferred todo 容易永久丢失。Monitor 可以在外部事实改变后创建或 unblock successor advancement todo。

## Completion 不是勾选框

LoopX completion 需要回答：

1. 结果证据是什么？
2. 后续是否存在？
3. 后续由同一 agent 继续，还是独立 handoff？
4. 如果没有后续，为什么可以关闭？

### Same-agent continuation

适合一个非交付大段中的连续分析：

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --claimed-by agent-a \
  --evidence "contract map completed" \
  --next-agent-todo "[P1] Draft the implementation plan" \
  --next-continuation-policy same_agent_non_delivery
```

### Independent handoff

适合希望任一 eligible peer 接手的 successor：

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --claimed-by agent-a \
  --evidence "implementation landed on review branch" \
  --next-agent-todo "[P1] Independently review the implementation" \
  --next-continuation-policy independent_handoff
```

默认 successor 不被 claim。只有显式 `--next-claimed-by` 才把它交给特定 peer。

### Existing successor

如果 successor 已经存在，使用稳定 id：

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --claimed-by agent-a \
  --evidence "source evidence ready" \
  --successor-todo-id <existing-successor-id>
```

### No follow-up

只有工作确实闭合时：

```bash
loopx todo complete \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --claimed-by agent-a \
  --evidence "acceptance checks passed" \
  --no-follow-up
```

`no_followup` 不是“我不想继续”。它必须与 acceptance evidence、空 frontier、已关闭 monitor/successor/replan gap 一致。`terminal_no_followup` 是 CLI 派生的 closure 状态，不是用户可以随意定义的 todo status。

## Supersede：改变方向而不伪造完成

如果原 todo 已不再正确，不要把它标 done。使用 supersede 记录新方向和因果关系：

```bash
loopx todo supersede \
  --goal-id <goal-id> \
  --todo-id <old-id> \
  --reason "new evidence invalidated the original approach" \
  --next-agent-todo "[P0] Implement the evidence-backed alternative"
```

这使 replay 能区分：

- 工作完成；
- 工作失败；
- 工作被新事实取代。

## Review 不是特殊身份

Review 是普通 todo 的 `action_kind=review` 和 continuation policy，不是一个隐藏的 primary reviewer 身份。

例如作者完成实现后创建：

```text
task_class=advancement_task
action_kind=review
continuation_policy=independent_handoff
excluded_agents=[author]
```

任何未被排除且满足 capability/workspace 条件的 peer 都可以 claim。

## 工作图推演

考虑以下状态：

```text
T1 [P0] 修改 public API
  requires decision D1
  claimed_by agent-a

U1 user_gate D1
  blocks agent-a

T2 [P1] 增加不改变 API 的 characterization smoke
  unclaimed
  required_capability shell

M1 monitor external PR
  next_due_at tomorrow
```

当前有 `agent-b`，能力为 `shell`。合理结果：

- U1 投影到 user channel；
- T1 对 agent-a blocked；
- T2 唤醒未被 excluded 的 peer，agent-b 可 claim；
- M1 未到期，不消耗 branch width，也不反复唤醒；
- goal 不应进入 terminal closure。

这里特别说明一个调度边界：即使另一个 claimed todo 被 gate，未认领的 advancement todo 仍应唤醒所有 eligible peer，而不是因为 goal 中存在 `claimed_by` 就饿死。

## 实验：构造一个可计算 Frontier

只在专门实验 goal 中写状态。

### 1. 添加一个 blocked P0

```bash
loopx todo add \
  --goal-id <lab-goal> \
  --role agent \
  --text "[P0] Change the public response schema" \
  --task-class advancement_task \
  --action-kind change_public_schema \
  --required-decision-scope direction:action:public_schema
```

### 2. 添加精确 user gate

```bash
loopx todo add \
  --goal-id <lab-goal> \
  --role user \
  --text "是否允许改变 public response schema？" \
  --task-class user_gate \
  --decision-scope direction:action:public_schema \
  --blocks-agent <agent-a> \
  --unblocks-todo-id <p0-id>
```

### 3. 添加独立 P1

```bash
loopx todo add \
  --goal-id <lab-goal> \
  --role agent \
  --text "[P1] Add a behavior-preserving characterization smoke" \
  --task-class advancement_task \
  --action-kind add_characterization \
  --required-capability shell
```

### 4. 比较两个 peer 的 quota

```bash
loopx --format json quota should-run \
  --goal-id <lab-goal> \
  --agent-id <agent-a> \
  --available-capability shell

loopx --format json quota should-run \
  --goal-id <lab-goal> \
  --agent-id <agent-b> \
  --available-capability shell
```

观察 user channel、selected todo、safe bypass 和 excluded/claim 结果。

## 核心代码领读：一个 todo 怎样成为某个 peer 的合法工作

这一讲的主线不是“找到最高优先级 todo”，而是逐层缩小候选集合：

```text
todo contract
  -> lifecycle / continuation
  -> peer-visible candidates
  -> capability gate
  -> claim / exclusion / successor constraints
  -> workspace guard
  -> agent 在 runnable set 中做最终选择
```

### 1. Todo 的类型和状态是两条独立轴

`loopx/control_plane/todos/contract.py` 把 routing lane 与 lifecycle 分开定义：

```python
TODO_TASK_CLASS_VALUES = {
    "advancement_task",
    "continuous_monitor",
    "user_gate",
    "user_action",
    "blocker",
}

TODO_STATUS_VALUES = {"open", "done", "blocked", "deferred"}
TODO_TERMINAL_STATUS_VALUES = {"done", "deferred"}

class TodoContinuationPolicy(str, Enum):
    INDEPENDENT_HANDOFF = "independent_handoff"
    SAME_AGENT_NON_DELIVERY = "same_agent_non_delivery"
```

因此 `open` 不等于“可执行”：一个 open `continuous_monitor` 可能尚未 due，一个 open `user_gate` 等待的是用户，一个 open `advancement_task` 还可能被 capability 或 workspace 卡住。

再看 `loopx/control_plane/todos/completion_policy.ts` 中 continuation 的默认值。
私有 `continuationPolicy` 在 `resolveTodoCompletionPolicy` 整项完成策略内使用：

```typescript
function continuationPolicy(
  value: string | null,
): typeof CONTINUATION_POLICIES[number] {
  const candidate = stripPythonWhitespace(String(value ?? "")).toLowerCase();
  return CONTINUATION_POLICIES.includes(
      candidate as typeof CONTINUATION_POLICIES[number],
    )
    ? candidate as typeof CONTINUATION_POLICIES[number]
    : "independent_handoff";
}
```

默认 `independent_handoff` 是刻意的：完成者不会因为“刚做完上一项”就自动拥有下一项。
仅在提供新 Agent Todo、未显式指定其 owner 且选择 `same_agent_non_delivery` 时，
完成策略才继承当前 `claimed_by`。显式 successor owner 仍受注册和 exclusion 校验。
Python continuation 枚举和 metadata 规范化保留用于编解码；无生产调用的旧策略查询已退役。

### 2. Live runtime model 只有 peer；旧 hierarchy 只是 migration input

`loopx/control_plane/agents/runtime_model.py` 是理解“没有 primary/side”最直接的代码：

```python
class AgentRuntimeModel(str, Enum):
    PEER_V1 = "peer_v1"

def agent_runtime_model_for_goal(goal):
    if isinstance(goal, Mapping):
        coordination = goal.get("coordination")
        raw = coordination.get("agent_model") if isinstance(coordination, Mapping) else None
        raw = raw or goal.get("agent_model")
        if raw not in {None, "", "peer_v1", "legacy_hierarchy"}:
            raise ValueError("coordination.agent_model must be peer_v1")
    return AgentRuntimeModel.PEER_V1
```

`legacy_hierarchy` 被允许读入，是为了 exactly-once migration；函数返回值仍永远是 `peer_v1`。兼容 reader 的存在不能被解释为旧角色仍参与 live scheduling。

对于尚未 claim 的工作，稳定分配也不产生 leader：

```python
def select_peer_for_work(registered_agents, *, work_key):
    agents = normalized_peer_agent_ids(registered_agents)
    if not agents:
        return None
    digest = hashlib.sha256(str(work_key).encode("utf-8")).digest()
    return agents[int.from_bytes(digest[:8], "big") % len(agents)]
```

这是 deterministic routing helper，不是 durable ownership。真正 ownership 仍要经过 todo claim/lease 写回。

### 3. Capability gate 输出候选集，不替 agent 领取 todo

`loopx/control_plane/agents/capability_gate.py::build_capability_gate` 先过滤合法 advancement/due monitor，再把候选分成 runnable 与 blocked：

```python
candidates = [
    item for item in raw_items
    if isinstance(item, dict)
    and todo_item_is_actionable_open(item)
    and (
        todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
        or _capability_item_identity(item) in blocked_monitor_identities
    )
]

for item in candidates:
    missing = missing_required_capabilities(item, available_capabilities=available)
    if missing:
        blocked.append(_capability_candidate_item(item, missing=missing))
    else:
        runnable.append(_capability_candidate_item(item, missing=[]))

if runnable:
    return {
        "action": "run",
        "decision_owner": "agent",
        "selection_policy": "agent_steering_audit_over_runnable_candidates",
        "runnable_candidates": runnable,
        "blocked_candidates": blocked,
    }
return {
    "action": _capability_resolution(missing_all)["action"],
    "runnable_count": 0,
    "blocks_delivery": True,
    ...,
}
```

领读时强调 `decision_owner="agent"`：kernel 负责证明哪些候选合法，agent 仍要结合上下文选择和 claim。缺少 `network` 可能要求 repair bridge，缺少 credential/production authority 则应问 owner；两者不能合并成模糊的“缺能力”。

### 4. Workspace guard 是 delivery 前的最后一道因果边界

多 peer 的写操作必须在独立 worktree。`capture_delivery_workspace` 持久化的是无凭据、无本地路径的身份：

```python
workspace_kind = (
    "independent_git_worktree"
    if current_git_dir != current_common
    else "canonical_checkout"
)
return {
    "task_repository": task_repository,
    "workspace_kind": workspace_kind,
    "peer_independent_worktree_required": bool(...),
}
```

`build_agent_workspace_guard` 只在多 peer 且当前候选需要 repository write 时生效：

```python
if len(agent_identity.get("registered_agents") or []) <= 1:
    return None
if not _peer_work_requires_isolated_workspace(...):
    return None

# classify not_git_worktree / foreign_git_worktree / canonical_checkout
if current_workspace:
    return {
        "action": "move_to_independent_worktree",
        "current_workspace": current_workspace,
        "required_workspace": "independent_git_worktree",
        "blocks_delivery": True,
    }
```

这里不是代码风格 gate，而是 attribution gate：后续 refresh 与 quota spend 要能证明交付发生在 todo 声明的 repository、正确的 peer workspace 中。

### 断点练习

1. 在 `completion_policy.ts::resolveTodoCompletionPolicy` 观察空值为何落到 independent handoff，以及 same-agent 继承需要哪些条件。
2. 在 `build_capability_gate:301` 改变 `available_capabilities`，只比较 runnable/blocked id。
3. 在 canonical checkout 与 linked worktree 各跑一次 quota，比较 `agent_workspace_guard`。
4. 给同一 todo 同时设置 `claimed_by` 与相同 agent 的 `excluded_agents`，确认 contract fail closed。

### 读完这一段应能回答

1. 为什么 `task_class` 不能由 `status` 推断？
2. `select_peer_for_work` 为什么不是 claim？
3. migration reader 与 live runtime model 的边界在哪里？
4. capability gate 为什么返回 candidate set，而不是唯一 todo？
5. worktree guard 与 spend causality 有什么关系？
6. delegated lifecycle authority 为什么没有把 peer 变成永久 leader？
7. material handoff 为什么只传引用、状态计数和截断标记？

## 代码阅读路线

1. `docs/project-agent-todo-contract.md`
2. `docs/reference/protocols/peer-agent-runtime-v1.md`
3. `loopx/control_plane/todos/contract.py`
4. `loopx/control_plane/work_items/lifecycle.py`
5. `loopx/control_plane/agents/runtime_model.py`
6. `loopx/control_plane/todos/mutation_authority.py`
   与 `loopx/control_plane/coordination/todo_lifecycle_decision.ts`：对照源适配器 facts 与共享生命周期决策。
   `loopx/control_plane/todos/completion_policy.ts`：对照完整 successor ownership 策略。
7. `loopx/control_plane/agents/material_frontier.py`
8. `loopx/control_plane/agents/material_handoff.py`
9. `loopx/control_plane/work_items/`
10. `docs/product/core-control-plane/state-machine.md` 的 Todo、Gate、Handoff 状态机

## 代表性 Smoke

- `examples/control_plane/todo-lifecycle-cli-smoke.py`
- `examples/control_plane/peer-agent-runtime-v1-smoke.py`
- `examples/control_plane/peer-agent-workspace-guard-smoke.py`
- `examples/control_plane/capability-gate-projection-smoke.py`
- `examples/control_plane/todo-projection-shared-helper-smoke.py`
- `examples/control_plane/agent-material-handoff-projection-smoke.py`
- `tests/control_plane/test_todo_mutation_authority.py`

## 课后检查

1. Claim 和 lease 的差别是什么？
2. Required capability 为什么不是权限？
3. 什么情况下 user gate 应是 global？
4. 为什么 review 应建模成普通 todo，而不是固定执行者的特权？
5. Completion 缺少 successor 和 no-followup 时，系统为什么不能终止？

下一讲把这个工作图交给决策内核，逐层拆解 `quota should-run` 如何编译出本轮 interaction contract。
