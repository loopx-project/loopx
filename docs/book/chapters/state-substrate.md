# 持久状态与只读投影

接手一项任务时，最先遇到的问题通常是：哪一份记录代表当前状态？Todo、dashboard、聊天和历史回执可能各自描述不同时间点。能读到旧信息，并不等于能够安全继续。

## 同一条 Todo，为什么会看到两个状态

考虑一个教学情境：Agent A 已完成修复，当前 Todo source 记录了完成及证据。Agent B 接手时读到较早缓存的 dashboard，卡片仍显示 open，于是准备重做。

提高刷新频率可以缩短这个窗口，却不能决定发生分歧时信谁。LoopX 的选择是为每类事实保留明确 owner，再生成面向不同读者的投影。恢复从当前 source 与外部读回开始，显示面负责帮助定位。

这会增加状态维护、读取和投影更新成本，但让 session、Host 和界面可以更换，同时保留可核对的工作记录。

## 为什么当前状态之外还要保留历史回执 {#design-choice}

T1 已提交 R1，随后其他工作把 source 推到新 revision。A 重试原操作时，有两个问题：当前状态是什么，原操作是否已经接受。只有最新状态，可能无法回答第二个问题；只有旧回执，又不足以判断下一次写入。

| 可选做法 | 适用之处 | 长程工作中的代价 |
| --- | --- | --- |
| 从聊天重建进展 | 解释动机与人工接手的小任务 | 需要重新解释身份、版本和外部 freshness |
| 只保存最新快照 | 快速读取当前工作 | 响应丢失后，未必能识别原操作已提交 |
| 当前 source + identity-bound receipt | 分开读取当前 head 与历史操作结果 | 需要维护身份、回执保留和恢复合同 |

LoopX 在相应 authority 路径上采用最后一种分工。历史 R1 可以被恢复，当前 head 仍可包含后来提交的 R2；重放 R1 不应把状态退回过去。这是 source 与 receipt 各有职责的理由，而非选择某一种文件格式就自动获得的性质。

对应实现是 `CoordinationCommandReceipt` 与 [operation replay 合同](/loopx/docs/reference/authority-operation-replay/)。它们仍受 provider 与命令各自的恢复边界约束。

## 先确定 Goal 与当前 authority

持久边界是 Goal。它包含目标、Todo、Gate、Agent 身份和运行路由；某个 Host thread 只是执行上下文。结束 session 不会自动删除 Goal，读到 Goal 也不会授予写权限。

### 精确复用 Goal，不靠文本猜测

复用依赖 stable `goal_id` 与 registry 路由。多个 Goal 存在时，`start-goal --guided` 提供只读选择，再按精确 id 重跑；相似目标文本不构成静默合并的依据。

复用 Goal 和接管 Agent 是不同操作。新会话可以读取原 Goal，但 fresh `agent_id`、已有 lane 的明确复用以及执行 lease 仍有各自的检查。旧聊天或 receipt 不授予新执行者写入权。

### Todo 存储取决于选定的事实源

当前实现区分 legacy Markdown 与选定的 File/SQLite authority。先确认该 Goal 实际使用哪条路径，不能仅凭文件存在或 provider 安装状态判断。

| 路径 | Todo 从哪里读取 | Markdown 的角色 | 操作注意 |
| --- | --- | --- | --- |
| 尚未迁移的 legacy 路径 | active-state 的 Todo 段落及元数据 | 仍可能是源状态 | 手改可能改变读取结果，也可能绕过验收与回执 |
| 已选择 canonical provider 的路径 | 对应 authority 的 Todo snapshot | 兼容工作台或投影 | 以 provider revision 和生命周期读回为准 |
| 旧 Todo `events.jsonl` 实验 | 已退役，不是当前恢复入口 | 不能据旧示例恢复为 authority | 非空旧源需按退役合同处理，不能忽略或清空来过检查 |

旧 Todo event API、replay、backfill 和 completion 示例已退役，见[退役合同](/loopx/docs/reference/protocols/event-sourced-state-contract-v0/)。这不等于 run history、quota events 或 `rollout_event_log` 也被退役；它们仍有各自的记录职责。

## 五类状态表面

### 1. Registry：身份、连接与策略

Registry 保存 Goal id、repository、active-state/runtime 路由、registered Agent 与运行策略。它回答“连接到哪里、有哪些边界”，不能证明某个 Host 已启动或某次交付已完成。

Goal 选择、Agent 身份与 authority 配置应按各自的公开入口读回。迁移 provider 要走对应切换协议，不能改一个展示字段就宣称迁移完成。

### 2. Todo 与 Goal state：当前工作事实

Todo status、dependency、Gate、claim、acceptance 与 continuation 决定下一步工作。当前值来自选定 source；状态修改通过相应生命周期 writer 完成，并保留绑定身份、revision 与所需 evidence。

在已迁移事务中，provider 的状态、receipt 和 revision 共同约束写入。在 legacy 路径中，需要遵守该 writer 已实现的锁和校验边界，不能推定它已获得其他 provider 的全部保证。

### 3. Active-state workbench：人可读工作台

`ACTIVE_GOAL_STATE.md` 让人和 Agent 阅读 Objective、Next Action、User Todo、Agent Todo 与 Progress。它在不同 authority 路径下可能承担不同职责，因此不能统一叫作“纯投影”，也不能叫作“所有事实”。

[结构化投影协议](/loopx/docs/reference/protocols/active-state-structured-projection-v0/)描述从工作台读取 typed view 的边界：投影可重算，不能自行授权；generated compatibility id 不等于 migration-ready canonical id；duplicate id 和缺失段落应暴露诊断。

### 4. Run history 与 rollout events：历史证据

Run history 记录一次工作的观察、交付、blocker、validation、outcome 与后续条件。Rollout events 为相关转换提供紧凑时间线和 join key。它们帮助解释历史，并给 replan、handoff 和 review 提供线索。

历史记录有各自的追加、隐私和幂等合同，不能把它们合称为一个可重放所有 Todo 的通用 event store。Raw transcript、详细日志和私有材料留在相应私有存储，公开投影只携带允许披露的摘要与引用。

### 5. Status 等 projection：面向消费者的读模型

`loopx status`、quota packet、dashboard、review packet 和 task graph 汇总 source facts，并按人、Agent 或调度器的任务裁剪信息。

它们可以摘要、排序和压缩，但不能发明 source 中没有的工作项、把显示顺序改作优先级，或通过改卡片绕过 write API。投影反映它所读取的输入，使用时需要核对 freshness 与 scope。

## 三种记录：Turn Journal、Goal State、Run History

| 记录 | 回答的问题 | 恢复时的用途 | 不能替代什么 |
| --- | --- | --- | --- |
| Turn journal | 原 Turn 已记录哪些阶段、结果、意图与回执？ | 区分可复用结果、待执行步骤和未决 effect | 整个 Goal 的验收与后续工作判断 |
| Goal state | 当前 Todo、Gate、Vision 与 acceptance 是什么？ | 重建工作 frontier 和授权条件 | 外部服务的实时读回 |
| Run history | 某轮发生了什么，有什么 evidence？ | 解释进展、复审与交接 | 当前状态或新的写入许可 |

Turn journal 是持久恢复记录，既可能包含 prepared 意图，也可能包含已提交 effect 的 checkpoint。不能把它全部称为临时意图，更不能看到一个阶段名就推断 Goal 已完成。

Goal state 保留当前生命周期事实。一次 run 说测试通过，需要检查它绑定的 commit、Todo 和 acceptance；当前状态也不能凭旧 run 断言远端 CI 仍然通过。

使用这些记录时，先确定问题属于哪一层：恢复原 Turn、判断下一项工作，还是解释历史。再读相关 source，并核对它们之间的身份和版本绑定。

## Projection truth contract 表达什么

[长程状态协议](/loopx/docs/reference/protocols/long-horizon-agent-state-protocol-v0/)把读写边界写成公开合同：

```json
{
  "schema_version": "long_horizon_agent_state_protocol_v0",
  "projection_is_writable": false,
  "source_of_truth": [
    "registry", "active_state", "todo_item_v0", "run_history",
    "rollout_event_log", "operator_gate", "human_reward"
  ],
  "write_apis": [
    "loopx todo", "loopx refresh-state", "loopx operator-gate",
    "loopx reward", "loopx quota spend-slot"
  ]
}
```

这里列的是事实类别与受控写入入口，不是声明所有状态都存进同一种介质。每个字段仍需回到其 owner 和当前 authority 路径解释。

Task graph、Agent management 等投影也声明 `projection_is_writable: false`、`write_api: false`。这些字段描述消费者合同；真正的权限仍由 lifecycle writer 检查，不能仅靠 JSON 中一条声明证明所有入口安全。

## 存储介质与写入合同

文件、SQLite 和其他 provider 决定状态如何存储；revision、幂等标识、lease 与 receipt 决定一次转换何时有效。把文件改成数据库不会自动解决身份、并发与副作用恢复。

[本地写正确性协议](/loopx/docs/reference/protocols/local-state-write-correctness-v0/)用 `prepare -> preview -> apply -> record -> project` 描述目标边界：检查预期 revision，保护相应 lease，提交可读回结果，再更新展示。

协议仍按 writer 分阶段落实。使用时应核对该命令、authority 模式和 provider 的实际验证，而不是把 draft 中的 hard idempotency、CAS 和 lease enforcement 一次性套到全部 legacy 路径。

### 历史基线与当前交付边界

`v0.5.4` 的 shared-authority 工作引入 provider-neutral TypeScript `AuthorityStore` contract 与候选 provider 验证。后来的本地切换、fencing、projection 和默认入口采用有各自的里程碑；历史版本的能力不应倒推成当前所有路径的上限，也不能把候选资格当成远端服务已交付。

File/SQLite 的当前本地 authority 路径，应按选定 Goal 的 readback 判断。NoKV/PostgreSQL 等阶段性候选不自动获得 runtime authority；安装 Provider 本身也不改变现有 Goal 的事实源。

跨设备在线 authority、离线写入和服务级恢复另有资格要求。不要用同步盘、多台机器共享目录或 IM 消息替代 authority 协议。最新阶段见[Shared Authority RFC](/loopx/docs/architecture/rfcs/shared-goal-authority-state-provider-v0/)及其 ledger。

## 历史证据怎样用于当前决策

| 层次 | 需要回答 | 核对内容 |
| --- | --- | --- |
| Lineage integrity | 谁记录了这份材料，是否被后续材料替代？ | producer、run/event id、recorded revision、supersession |
| Current applicability | 原输入和范围还适用于当前问题吗？ | commit、target、source revision、时间窗口、Gate scope |
| Fresh external observation | 外部世界现在是什么状态？ | 远端 ref、CI revision、服务状态的独立读回 |

例如修复在 `commit-a` 上通过测试，随后代码到了 `commit-b`。旧记录仍证明当时发生的事，却不能直接验收新代码。Goal 方向或授权范围变化也需要同样重审。

新投影可能包含旧观察。`generated_at` 表示投影生成时间，不会自动更新其底层 evidence；应连同 source revision 与外部事实的新鲜度一起看。

## 遇到 projection gap 时怎么办

先排除不同视图的正常裁剪：有限列表、Agent scope 或角色筛选可能隐藏条目。范围和版本一致时仍有分歧，再按下列顺序处理：

1. 确认当前 authority 和 authoritative source；
2. 定位 source 写入失败、缓存滞后、迁移不一致或外部 evidence 过期；
3. 通过原 owner 的修复/写入路径处理；
4. 重算相关投影，验证 source revision 和结果；
5. 再推进依赖这些事实的工作。

不要手工把多个显示面改成一样。那可能隐藏真正的源问题，也无法补上缺失回执。

## 代价与使用边界

**恢复需要读取和核对。** 只读旧 dashboard 或聊天通常不够；需要当前 source、原 Turn 记录或外部读回。缓存与摘要能节省成本，但必须保留足够的身份和 freshness 信息。

**投影更新可能落后于 source。** 这种分离允许不同界面使用同一事实源，也要求消费者不要把缓存当作提交授权。并非每次读取都一定滞后，关键是能够识别版本差异。

**手改 Markdown 可能有实际效果。** legacy decoder 会在没有覆盖元数据时把 `[x]` 读成 done。这样绕过 lifecycle writer，可能缺少验收与回执；在已选择 provider 的路径上，同样的编辑又可能只是改了工作台。

因此，操作 Todo 应使用当前 Goal 的生命周期入口，复核结果与 evidence。不要因为规范上不应手改，就假定字节改动不会影响源状态。

## 证据与进一步阅读

| 本章论断 | 实现或协议入口 | 覆盖边界 |
| --- | --- | --- |
| 选择 source 后再投影 | `loopx/todos.py`、`todo_block_codec.py` | legacy 与 provider 读取不同 |
| 旧 Todo events 已退役 | `legacy_event_source.py`、退役合同 | 不代表 rollout/history 被整体退役 |
| Journal 保留不确定性与已提交事实 | [LoopX Turn 协议](/loopx/docs/reference/protocols/loopx-turn-v0/) | 仍需 provider readback 与恢复判定 |
| 读模型不授予写权限 | [Task graph 协议](/loopx/docs/reference/protocols/task-graph-projection-v0/) | 声明不能替代 writer 权限检查 |

新增字段时，先找它的 owner：配置归 registry，Todo transition 归相应 lifecycle，run 观察归 history/evidence，展示派生为 projection。Git、CI 等事实继续由外部系统拥有；领域专属结果放 Domain State。

需要实现细节时，进入 [Control-Plane Course 状态底座](/loopx/docs/development/control-plane-course/04-state-substrate/)和[Agent-scoped evidence ledger](/loopx/docs/reference/protocols/agent-scoped-evidence-ledger-v0/)。下一章讨论：确定事实源之后，哪些执行者可以改变它。
