# 第 4 讲：状态底座与可重放事实

> Update (2026-09-25): the Todo `events.jsonl` API, replay, backfill and completion
> examples below describe a retired experiment. Current Todos use legacy Markdown
> or the selected File/SQLite authority. See the
> [retirement contract](../../reference/protocols/event-sourced-state-contract-v0.md).


> **本讲结论：** Canonical event/state contract 拥有长期事实；active state、status 和
> dashboard 是可重建 read model；session context 不能替代 project memory。

建议时长：90 分钟。讲解 55 分钟、状态追踪 20 分钟、实验 15 分钟。

## 学习目标

完成本讲后，开发者应该能够：

1. 区分 goal、executor、user/operator 和 dashboard 四类 actor。
2. 区分 registry、event ledger、active state、run history 和 status projection。
3. 解释为什么 projection 不能成为写 API。
4. 沿一次 todo transition 找到它的事实源和 read model。
5. 判断一个新字段应该属于配置、事件、工作台还是展示层。
6. 区分 session context、project memory、Domain State 和私有 runtime artifact。

## 本讲技术契约

| 边界 | LoopX 中的答案 |
| --- | --- |
| State owner | Registry 拥有长期配置；canonical todo/event/state contract 拥有已提交事实 |
| Turn snapshot | Status、todo projection 和 fresh environment observation 组成本轮只读输入 |
| Write boundary | 只有经过 authority 与 transition validation 的 CLI/API 可以提交状态 |
| Replay boundary | 从 durable state 重建 read model，再重新探测当前环境；不重放旧推理文本 |
| Forbidden | Session、dashboard、sink 和 host 不能直接成为 goal truth |

## 从三个 Showcase 找事实归属

先不背状态文件。遇到一条信息时，先问“谁能证明它是真的”：

| 信息 | Issue-Fix owner | Single-Agent Auto ML owner | Auto Research owner | LoopX 怎样保存 |
| --- | --- | --- | --- | --- |
| 外部对象当前状态 | Repository / GitHub | training/evaluation provider | evaluator / artifact store / public source | fresh observation，不复制成第二权威源 |
| 可跨 turn 复用的领域事实 | feasibility、PR lifecycle | experiment contract/result、hypothesis ledger | hypothesis、evidence event、metric result | stable key + compact fingerprint + lineage |
| 当前必须做的工作 | Kernel todo/gate/monitor | Kernel candidate todo/task monitor/promotion gate | Kernel role todo/promotion gate | canonical lifecycle transition |
| 本轮临时推理和工具输出 | coding session / workspace | model session / experiment workspace | worker session / experiment workspace | 不作为长期事实；只提取 validated ref |
| 给人和 host 看的当前视图 | status / PR report | experiment board / Explore Graph | frontier / evidence graph | 从 canonical state 重建 projection |

同一句“PR checks 仍 pending”可能同时出现在 session、Domain State 和 status 中，但它们的
权威不同：GitHub 证明当前事实，Domain State 证明某轮已观察到什么，status 只是把已提交
状态展示出来。判断 owner 后，再决定它应进入 registry、event、Domain State 还是只留在
runtime artifact。

Auto ML 里最容易混淆的是三种“图或表”：

| 表面 | 拥有什么 | 不能证明什么 |
| --- | --- | --- |
| External task/metric source | task 当前状态、原始 artifact 与 provider readback | LoopX 已接受结果、候选值得 promotion |
| ML Experiment Domain State | matched contract、紧凑结果、归因、promotion/no-promote proposal | 通用 todo 已关闭、资源或发布已获授权 |
| Explore Graph result log/view | hypothesis、experiment、finding 与 supports/refutes lineage | graph edge 已执行、Harness 建议已 claim |

恢复时先重新观察外部 task，再按稳定 identity 更新 Domain State；material finding 才追加到
Explore result log。Explore Harness 读取这些 projection 做 planning，但它不写回 external
truth，也不成为第四个状态源。

## 看板列是多轴状态的 Projection

面向操作者的界面可以把 todo 渲染成 Kanban，但 canonical state 不能退化成一个
`column` 字段。同一张卡片至少同时具有四组状态：

| 维度 | 典型字段或对象 | 回答的问题 |
| --- | --- | --- |
| Lifecycle | `open`、`blocked`、`deferred`、`done` | 工作是否仍在生命周期中？ |
| Task class | advancement、user gate/action、monitor、blocker | 这是什么类型的工作或等待？ |
| Routing and authority | claim、lease、dependency、scope、capability | 谁能执行，前置条件和权限是否满足？ |
| Proof and time | evidence、receipt、freshness、cadence、`resume_when` | 什么证明变化成立，何时应再次观察？ |

看板可以把这些事实投影成便于操作的逻辑列：

```text
Runnable | Claimed | Waiting on user | Monitoring | Blocked | Review | Done
```

这些列不是新的事实源。“Waiting on user”来自 scoped user gate，“Monitoring”来自
带 cadence 和 target key 的 monitor，“Review”可以来自领域状态与当前 acceptance，
而不是把每个展示名称写进 Kernel lifecycle enum。

看板上的移动也对应结构化算子，而不是任意拖拽：

| 操作 | 必须保持的约束 |
| --- | --- |
| Claim / release | 注册身份、executor exclusion、lease 和 workspace 仍然有效 |
| Gate / decide | decision scope、阻塞对象和授权结果明确绑定 |
| Monitor / resume | target、cadence、fresh observation 和恢复条件可重放 |
| Complete / supersede | evidence、successor 或 no-follow-up rationale 完整 |
| Writeback / replay / repair | 已提交 transition 可验证，projection 可重建，失败后可恢复 |

Capability Pack 可以在同一组通用算子上增加领域泳道。例如 Issue Fix 展示
`feasibility -> patch -> CI -> review -> merge`，Single-Agent Auto ML 展示
`candidate -> preflight -> launch -> monitor -> evaluate -> promote/no-promote`，Auto
Research 展示 `hypothesis -> execute -> evaluate -> promote/retire`。这些阶段来自 Domain
State 和 capability proposal；只要它们涉及 claim、gate、quota、permission 或 terminal
closure，最终 transition 仍由 Kernel 接受或拒绝。

## Goal 不是聊天线程

LoopX 的 durable identity 是 goal，不是某个 Codex thread：

```text
Goal: 长期存在的目标、边界和进度身份
Executor: 某一轮暂时执行 bounded transition 的模型 session
User/operator: 拥有高风险决策和目标边界
Dashboard: 只读观察和路由界面
```

一个 goal 可以先后被多个 session、模型或 host 推进。一个 session 也可能在不同时间读取多个 goal，但它不能因为读到了某个 goal 就获得写权限。

这一区分解决了两个常见问题：

- 聊天压缩或 session 结束后，长期任务仍可恢复；
- 多个 peer 可以共享事实，而不共享同一个巨大 transcript。

## Session Context 不是 Project Memory

长程任务跨 session 恢复时，下一位 peer 同时面对三类信息：当前对话留下的临时
上下文、LoopX 持久化的 project memory，以及必须重新探测的运行环境。三者拥有
不同事实：

| 表面 | 保存什么 | 可能发生什么 | 能否单独作为 authority |
| --- | --- | --- | --- |
| Session context | 当前对话、临时推理、尚未写回的观察 | 被压缩、截断、结束或换模型 | 不能 |
| Project memory | goal identity、objective/boundary、todo/gate、紧凑 evidence、run lineage | 被 replay、投影或由另一 peer 恢复 | 可以，但必须经过 canonical contract |
| Environment | 当前 checkout、host capability、外部服务与 fresh source | 在 handoff 之间变化 | 不能，必须重新探测 |

恢复的目标不是逐字重建旧 transcript，而是从持久化事实和新鲜环境重新生成可执行
决定：

```text
next decision = replay(project memory) + inspect(fresh environment)
```

Project memory 的充分性标准是：更换 session 后，新的 peer 仍能从 durable state
重建同一 objective、authority boundary、open frontier、gate、next probe 和 stop
condition。它不要求逐字复现旧推理。第 5 讲会把这个要求进一步写成 handoff 的
不动点检查。

可以把这看成长期任务的“最小可恢复状态”，而不是“最大可记忆内容”。外置状态应优先保存：

- 能改变合法 action set 的事实，例如 gate、claim、capability 与 workspace boundary；
- 能改变下一步判断的事实，例如 fresh observation、acceptance gap 与 monitor due time；
- 能避免重复 effect 的 identity、receipt 与 reconciliation key；
- 能让路线被解释和修订的 evidence ref、vision checkpoint 与 successor lineage。

原始 transcript、完整工具输出、未验证猜测和可以从 source 重建的展示内容通常不应进入
canonical state。它们会扩大上下文和隐私成本，却不一定提高恢复正确性。

状态底座与 CLI packet 也要分开：前者为 replay 保留完整的结构化事实，后者只把当前 lane
需要的 decision、proof boundary 和 writeback 命令投影进有限模型上下文。Packet 丢失可以
重建；canonical event 丢失则可能无法判断 effect 是否已提交。

## 五类状态面

### 1. Project registry：注册与策略真相

Registry 回答“这个 goal 是谁、在哪里、允许什么”。常见字段包括：

```json
{
  "id": "<goal-id>",
  "repo": "<repo>",
  "state_file": "<active-state>",
  "adapter": {"kind": "generic_project_goal_v0"},
  "coordination": {
    "agent_model": "peer_v1",
    "registered_agents": ["agent-a", "agent-b"]
  },
  "spawn_policy": {
    "spawn_allowed": false,
    "max_children": 0
  },
  "guards": {},
  "quota": {}
}
```

Registry 适合保存：

- goal identity 和 repo/state_file 连接；
- authority sources；
- registered peer 集合；
- spawn、quota、guard 等长期策略；
- default-off capability 的 opt-in 配置。

它不适合保存每一次 todo claim、每轮模型输出或临时观察。

项目 registry 和 global registry 的职责不同：

- project registry 是目标的项目内注册源；
- global registry 聚合多个项目，支持 App automation 和跨项目 status 路由；
- CLI 必须明确使用哪个 registry，不能把某个旧 checkout 的本地文件当成全局真相。

### 2. Event ledger：追加式事实

事件流保存“发生过什么”。协议见 `docs../../reference/protocols/event-sourced-state-contract-v0.md`。

代表性事件包括：

```text
todo_added
todo_claimed
todo_updated
todo_completed
todo_deferred
operator_gate_recorded
run_refreshed
quota_spent
evidence_recorded
projection_synced
```

事件模型有四个重要属性：

1. **Append-only**：新事实追加，不重写历史来伪装没有发生过。
2. **Idempotent**：相同 `event_id` 和相同 payload 重放不应重复生效。
3. **Ordered**：使用 append sequence 或确定顺序进行 replay。
4. **Partitioned**：公开安全摘要和本地私有 payload 分开，不能把 raw transcript 推进公共状态。

事件适合回答：

- 这个 todo 何时被谁 claim？
- 这个 gate 为什么被关闭？
- 这一轮为什么 spend？
- 某个 projection 是否真的完成 readback？

### 3. Active state：人可读工作台与兼容投影

`ACTIVE_GOAL_STATE.md` 保留一个人可以直接读的长期工作台，通常包含：

```markdown
## Objective
## Operating Contract
## Non-goals
## Next Action
## User Todo
## Agent Todo
## Progress
```

它很重要，但不能简单地理解成“所有 canonical truth 都在 Markdown”。当前设计正在把 todo/history 的规范事实迁移到 append-only event ledger；active state 继续承担：

- 人可读目标与边界；
- 当前工作台；
- 兼容旧状态；
- 结构化 projection 的来源之一；
- 本地恢复时的可检查表面。

协议 `active_state_structured_projection_v0` 是对 Markdown 的只读解析，不是第二个写 API。

### 4. Run history 与 agent evidence：执行证据索引

一次 run 需要留下足以恢复的紧凑记录，例如：

- classification；
- delivery scale 和 outcome；
- validation/evidence refs；
- blocker 或 successor；
- agent identity；
- spend 关系。

富 payload 可以保留在本地私有 runtime state；status 和 handoff 只读取 compact、public-safe 的索引。Supervisor 也只读取 thin agent evidence projection，不读取 raw transcript。

### 5. Status：面向操作者的 read model

`loopx status` 把多类事实折叠成一个稳定的只读合同，回答：

- 哪些 goal 需要注意？
- 哪些 user todo 是 open？
- 哪些 agent todo runnable、claimed、blocked 或 stale？
- 当前 peer lane 在做什么？
- 是否有 contract errors、projection gaps 或 repair needs？

Status 不是写入口。Dashboard、Lark kanban、review packet 都应该消费 status 或其他 public-safe projection，而不是反向解析私有源文件再写回。

## Core State、Domain State 与 Runtime Artifact

前面的五类状态面主要解释跨场景生命周期。领域扩展还需要一层紧凑事实，但不能
因此把 Issue-Fix、Explore 或 ML Experiment 的字段塞进通用 todo/quota 状态机。

| 层 | 代表内容 | 拥有什么 | 明确不拥有什么 |
| --- | --- | --- | --- |
| Core State | registry、todo、gate、quota、event、run、vision | 跨领域生命周期与权限边界 | 领域专属结果 schema |
| Domain State | issue feasibility、PR lifecycle、experiment result、checkpoint | 某个 Capability Pack 的紧凑 read model | quota、gate、claim、permission |
| Runtime Artifact | raw log、transcript、verifier tail、临时调试文件 | 私有诊断与可复现实验材料 | public control-plane truth |

`loopx/domain_state.py::default_domain_state_file_path` 把领域事实限制在稳定的
goal/pack 路径中：

```python
return (
    Path(project).expanduser()
    / ".loopx"
    / "domain-state"
    / compact_goal_id
    / compact_pack
    / compact_filename
)
```

`upsert_domain_state_jsonl` 进一步要求稳定 key，并以 lock + temporary file +
`os.replace` 完成原子 upsert。下面只省略遍历之外的分支，其余行保持源码一致：

```python
candidate = {**payload, "domain_state_key": key}
# ...
if isinstance(row, dict) and row_key == key:
    if not updated:
        merged_candidate = (
            merge_existing_fn(row, candidate)
            if merge_existing_fn is not None
            else candidate
        )
        if unchanged_fn is not None and unchanged_fn(row, merged_candidate):
            rows.append(row)
            unchanged = True
        else:
            rows.append(merged_candidate)
        updated = True

# ...
os.replace(tmp_name, path)
```

从这段代码应读出三个边界：

1. Domain State 是 project-local、按 goal 与 pack 分区的事实，不是全局 memory dump。
2. 稳定 key 和 unchanged 判定用于幂等观察，不代表领域 pack 可以决定下一轮执行。
3. 返回值显式使用 `path_recorded=False`，public receipt 不应泄露本地路径。

Issue-Fix 与 ML Experiment 的 smoke 分别验证这条通用 seam：
`examples/issue-fix-feasibility-smoke.py`、
`tests/test_ml_experiment_volc_packet.py`。如果一个新领域字段会改变 quota、gate、
claim 或 permission，它很可能属于 Core State 规则，而不是 Domain State。

## Canonical 与 Projection 的判别法

面对一个字段，依次问：

1. 它是长期策略，还是发生过的 transition？
2. 是否必须可 replay、可审计、可幂等？
3. 它只是为了某个 UI 好看，还是会改变下一轮决策？
4. 谁拥有写权限？CLI、operator、agent，还是外部 sink？

一个实用表格：

| 信息 | 推荐归属 | 理由 |
| --- | --- | --- |
| registered peers | registry | 长期配置与身份集合 |
| todo claimed by agent-a | event ledger | 可审计 transition |
| 当前 open todo 数 | status projection | 可从事实折叠得到 |
| 下一步的人类可读描述 | active state | 工作台与恢复入口 |
| Lark record id | sink-local config/projection lineage | 外部展示映射，不是 goal 事实 |
| raw model transcript | 私有 runtime artifact | 不应进入公共 control-plane 状态 |

## 一次 Todo 写入如何穿过状态层

考虑：

```bash
loopx todo claim \
  --goal-id <goal-id> \
  --todo-id <todo-id> \
  --claimed-by agent-a
```

概念路径是：

```text
CLI validation
  -> resolve registry goal and registered agent
  -> enter the canonical state transaction boundary
  -> authorize and validate the todo transition
  -> write the durable transition through its supported API
  -> rebuild the compatible read projection
  -> status later folds current claim
  -> quota sees claimed runnable frontier
```

这里有三个不能省略的性质：

- `agent-a` 必须是注册 peer；
- claim 是 soft ownership，不是永久权限；
- status 看到 claim 不等于 status 自己写了 claim。

## 为什么不能直接编辑 Projection

假设 dashboard 显示 todo `done=false`，然后 UI 直接把它改成 `true`，但没有写 todo completion event：

```text
Dashboard says done
Event ledger says open
Quota sees runnable
Active state may still show open
```

系统出现多真相。正确路径应是：

```text
UI action
  -> LoopX todo complete API
  -> transition validation
  -> durable event/state write
  -> refreshed projections
  -> UI readback
```

同理，Lark Base、web dashboard 和 supervisor observation 都是 sink/read model，不应该自己成为 control-plane author。

## Replay 与恢复

一个可恢复的状态内核至少需要：

```text
registry policy
+ ordered events
+ active-state objective/boundary
+ compact evidence refs
= current status projection
```

Replay 不是简单地重放聊天。聊天里可能有：

- 未执行的建议；
- 失败的命令；
- 过期推理；
- 私有文本；
- 被后续状态 supersede 的结论。

事件则应明确区分 proposal、attempt、executed receipt 和 validation。例如 supervisor 的 `inject` proposal 不是 session 已被注入的证明；只有 capability-matched host receipt 才是执行事实。

## Projection Gap：控制面常见故障

最典型的 gap 是：人类可读文本说“请用户确认 X”，但结构化 `user_todo_summary.open_count=0`。

这时不能只回复“owner gate”。正确诊断是：

```text
具体 user todo 未投影，需修复 LoopX 状态投影
```

修复方式是通过 LoopX user todo / operator gate API 写入具体问题、decision scope 和 blocking scope，而不是让 agent 从 prose 猜测。

另一个常见 gap 是：agent 声称交付完成，但没有 successor，也没有结构化 `no_followup` rationale。此时 terminal closure 不应成立。

## 状态所有权边界

| Actor | 可以做 | 不可以做 |
| --- | --- | --- |
| User/operator | 决定目标边界、高风险操作、公开声明、凭据和生产动作 | 通过随口一句话绕过 durable gate 记录 |
| Agent | 执行一个允许的 transition，写 todo/evidence/refresh | 领取别人的 todo、伪造 host receipt、重写用户决策 |
| CLI | 校验 schema、transition、projection、quota | 猜测缺失的业务事实 |
| Dashboard/sink | 读取并显示 public-safe projection | 直接改 canonical state |
| Host | 唤醒 turn、传 task body、应用调度 | 自己重写 quota 或 todo 规则 |

## 实验：沿一次状态变化追踪事实

只在专门的实验 goal 中执行写操作。

### 1. 先读 registry 与 status

```bash
loopx --format json registry
loopx --format json status --goal-id <lab-goal-id>
```

只保留字段结构，不复制私有 objective 或路径到课堂作业。

### 2. 增加并领取一个 todo

```bash
loopx todo add \
  --goal-id <lab-goal-id> \
  --role agent \
  --text "[P1] Trace one state transition" \
  --task-class advancement_task \
  --action-kind trace_state \
  --required-capability shell

loopx todo list --goal-id <lab-goal-id>

loopx todo claim \
  --goal-id <lab-goal-id> \
  --todo-id <todo-id> \
  --claimed-by <lab-agent-id>
```

### 3. 比较三种读面

```bash
loopx todo list --goal-id <lab-goal-id>
loopx --format json status --goal-id <lab-goal-id>
loopx history --goal-id <lab-goal-id>
```

回答：

- 哪个输出是 lifecycle API 的直接投影？
- 哪个是 operator read model？
- 哪个保存时间顺序？

### 4. 完成并写 successor

```bash
loopx todo complete \
  --goal-id <lab-goal-id> \
  --todo-id <todo-id> \
  --claimed-by <lab-agent-id> \
  --evidence "state transition traced in lab" \
  --next-agent-todo "[P1] Explain the resulting status projection" \
  --next-continuation-policy same_agent_non_delivery
```

观察 completion 和 successor 是否在同一个 transaction 中可见。

## 核心代码领读：从一条事件到 status read model

这一讲不要把 registry、Markdown 和 JSONL 当成三个互相竞争的数据库。按下面的所有权链阅读：

```text
registry entry
  -> resolve state_file / runtime root
  -> append-only event log
  -> active-state todo projection
  -> attention queue / status payload
  -> quota 的只读输入
```

### 1. Registry 只解析连接边界

`loopx/registry.py` 的入口刻意很薄：

```python
def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("registry root must be a JSON object")
    return payload

def resolve_state_file(repo: Path, state_file: str | None) -> Path | None:
    if not state_file:
        return None
    path = Path(state_file).expanduser()
    return path if path.is_absolute() else repo / path
```

这层只回答“goal 在哪里、状态文件在哪里、边界是什么”。它不拥有 todo transition，也不把 arbitrary JSON 当成有效 registry。

### 2. Event store 用锁、sequence 和 fingerprint 保证可 replay

`loopx/event_sourced_state.py` 先规范化事件，再在 append 时重新读取锁内真相：

```python
def make_state_event(*, event_id, goal_id, event_type,
                     refs=None, payload=None, recorded_at=None, ...):
    return normalize_state_event({
        "schema_version": STATE_EVENT_SCHEMA_VERSION,
        "event_id": event_id,
        "goal_id": goal_id,
        "event_type": event_type,
        "recorded_at": recorded_at or now_utc_iso(),
        "refs": refs or {},
        "payload": payload or {},
        # ... producer/privacy/projection_version
    })

def append(self, event):
    with exclusive_file_lock(self.path):
        self._loaded = False
        events = self.load()
        next_sequence = max(
            (int(item["append_sequence"]) for item in events), default=0
        ) + 1
        normalized = normalize_state_event(event, append_sequence=next_sequence)
        prior = {item["event_id"]: item for item in events}.get(normalized["event_id"])
        if prior is not None:
            if event_fingerprint(prior) != event_fingerprint(normalized):
                raise StateEventConflictError(...)
            return prior
        # ... append one canonical JSONL row
```

逐分支理解：

- 同一个 `event_id`、同一个 fingerprint：幂等重试，返回 prior；
- 同一个 `event_id`、不同 fingerprint：冲突，拒绝覆盖历史；
- 新事件：在锁内分配单调 `append_sequence` 后追加。

这就是“append-only”比“只用文件追加模式打开”更强的地方。

建议断点：`AppendOnlyStateEventStore.append:511`。用同一事件 append 两次，再修改 payload append 第三次，比较两个结果。

### 3. Todo projection 先信结构化事件，缺失时才兼容 Markdown

`loopx/control_plane/todos/active_state_todos.py` 展示了 migration reader 与 live truth 如何共存：

```python
state_text = state_path.read_text(encoding="utf-8")
next_action_entries = active_state_next_action_entries(state_text, limit=3)
rollout_events = load_rollout_events(...) if runtime_root is not None else []

event_fields = active_state_event_projection_fields(
    goal,
    state_path=state_path,
    preferred_todo_ids=preferred_todo_ids,
    rollout_events=rollout_events,
)
if event_fields.get("user_todos") or event_fields.get("agent_todos"):
    fields = event_fields
    monitor_writeback_contract_writer(
        fields, supported=False, source="event_projection_read_model"
    )
else:
    fields = parse_active_state_todos(...)
    monitor_writeback_contract_writer(
        fields, supported=True, source="markdown_active_state"
    )

projection_gap = state_projection_gap_warning(state_text, ...)
if projection_gap:
    fields["state_projection_gap"] = projection_gap
return redacted_status_todo_fields(fields)
```

关键点有三个：

1. 新路径优先消费 event projection；旧 Markdown reader 仍保留，负责迁移窗口。
2. event projection 是 read model，不能伪装成 monitor 的直接写 API。
3. prose 中有可执行 Next Action、结构化 todo 却为空时，必须投影 `state_projection_gap`，不能安静地当成无工作。

### 4. Status collection 是聚合器，不是第二套状态机

`loopx/control_plane/status/collection.py::collect_status` 的调用顺序很值得照抄到白板：

```python
registry = context.load_registry(registry_path)
runtime_root = context.resolve_runtime_root(registry, runtime_root_override, ...)
global_registry = context.collect_global_registry_health(...)
history = context.collect_history(...)
contract = context.check_contract(...)
queue = context.build_attention_queue(
    contract=contract,
    history=history,
    global_registry=global_registry,
    runtime_root=runtime_root,
)
runtime_summaries = context.build_runtime_summaries(...)

payload = {
    "ok": bool(contract.get("ok")) and bool(global_registry.get("ok", True)),
    "attention_queue": queue,
    **runtime_summaries,
}
payload["agent_management_projection"] = (
    context.build_agent_management_projection(payload)
)
```

它通过 callback context 组合已有 read models。`collect_status` 不应自行重写 todo terminal 规则；否则 status 与 quota 会形成两套真相。

### 数据流练习

选一个 todo id，沿下面四处追同一个稳定标识：

```bash
rg -n "<todo-id>" .codex/goals .loopx "$HOME/.codex/loopx" 2>/dev/null
loopx --format json todo list --goal-id <goal-id>
loopx --format json status --goal-id <goal-id>
loopx --format json quota should-run --goal-id <goal-id> --agent-id <agent-id>
```

不要比较整份 JSON。只比较 `todo_id`、`status`、`claimed_by`、`task_class` 和 projection source。

### 读完这一段应能回答

1. Registry 为什么不是 todo truth？
2. 为什么 event id 冲突必须 fail closed？
3. Markdown fallback 为什么应保留，但不能继续定义新 runtime 语义？
4. `state_projection_gap` 在哪一层产生，又在哪一层改变执行选择？
5. Status collector 为什么适合依赖注入式聚合，而不适合拥有 transition？

## 代码阅读路线

| 顺序 | 文件或协议 | 关注点 |
| --- | --- | --- |
| 1 | `examples/registry.example.json` | 注册配置的边界 |
| 2 | `examples/active-goal-state.example.md` | 人可读工作台 |
| 3 | `docs../../reference/protocols/event-sourced-state-contract-v0.md` | 事件与 replay |
| 4 | `docs/reference/protocols/active-state-structured-projection-v0.md` | projection 非写 API |
| 5 | `docs/status-data-contract.md` | status read model |
| 6 | `loopx/control_plane/todos/`、`loopx/control_plane/work_items/lifecycle.py` | todo contract 与 transition lifecycle |
| 7 | `loopx/status.py` | 聚合 read model，先搜 builder 入口 |

## 代表性 Smoke

- `tests/control_plane/test_retired_todo_event_source.py` (retirement regression)
- `examples/control_plane/todo-lifecycle-cli-smoke.py`
- `examples/state-projection-gap-smoke.py`
- `examples/control_plane/todo-projection-shared-helper-smoke.py`

Smoke 的目的不是验证每个 incidental JSON 字段，而是守住 durable behavior：可 replay、幂等、合法 transition、projection gap 不被静默忽略。

## 常见设计错误

### 把项目私有文件当成通用 sink 数据源

通用 dashboard 应消费 LoopX projection，不应解析某个项目的会议文档、原始 transcript 或本地路径。

### 为每个 host 复制一份状态机

Codex App、CLI、Claude Code 应共享 LoopX CLI/state kernel。host adapter 只做触发和能力执行，不 fork truth。

### 把兼容 migration 当作当前模型

旧 hierarchy 字段可以保留 exactly-once migration reader，但当前 runtime model 只有 `peer_v1`。迁移逻辑的存在不表示 primary/side 仍是实时概念。

### 只追加文档，不退休旧真相

Canonical 文档和 projection 应压缩、替换或标记 superseded，避免同时保留多个互相冲突的“当前版本”。

## 课后检查

1. Registry 和 event ledger 分别回答什么问题？
2. Active state 为什么既重要，又不能被当成唯一 canonical event store？
3. 为什么 supervisor observation 必须是 read-only projection？
4. 一个 Lark sink 的 record id 应该放在哪里？
5. 如果 user gate 只存在于 prose，下一轮 quota 应如何处理？

下一讲将在这个状态底座上建立工作图：todo 类型、peer claim、lease、capability、gate、successor 和 handoff。
