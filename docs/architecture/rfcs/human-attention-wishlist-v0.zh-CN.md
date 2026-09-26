# RFC：Human Attention Wishlist v0

- 状态：已接受
- 替代 / 关闭：无
- 提出方：LoopX maintainers
- 日期：2026-08-14
- 范围：一个类型化、非阻塞的人类注意力子类型，以及有界的 agent 写入 sidecar；不新增 capability、任务存储、权限授予、调度器或 quota lane
- 基线：LoopX `4e4c03621`
- 跟踪 issue：[#3179](https://github.com/huangruiteng/loopx/issues/3179)
- 语言说明：[英文版](./human-attention-wishlist-v0.md)与本中文版互为语义镜像，差异视为缺陷。

---

## 0. 一个例子

Agent 完成并验证了当前选中的产品任务。工作期间，它发现一个高杠杆机会：用户回答一个简短偏好、引荐一位相关 maintainer，或审阅一个具体假设，都可能改善后续切片。但当前选中的任务并不依赖这项人类动作，而且 agent 还有独立工作可以继续。

目前 agent 只有三个不理想的选择：

1. 把机会变成 `user_gate`，错误地阻塞交付；
2. 记录普通 `user_action`，尽管请求是可选的，却可能立即触发通知；
3. 把观察留在聊天中或直接丢弃，后续 turn 无法使用。

期望的是第四种选择：

```text
完成并验证选中工作
  -> 可选地捕获 0 或 1 个有证据的人类 wish
  -> 作为非阻塞 sidecar 写回
  -> 保持选中工作、quota、authority 与通知行为不变
```

用户可以稍后集中查看 wish，或在原本就可见的 material 结果里顺带看到一条。Wish 的存在永远不会单独触发通知，也不会停止 agent 主流程。

## 1. 问题

LoopX 已经区分阻塞性的 `user_gate` todo 与非阻塞的 `user_action` todo，也能在 scoped gate 周围继续独立 agent lane。Heartbeat 还要求 agent 保留高价值的落选候选。缺少的是：当正常工作推进时，如何把可选的人类杠杆精确写入并投影出来。

现有接缝无法组合出这个结果：

- heartbeat 指南要求记录高价值候选，却没有定义 wishlist 写命令或生命周期；
- `todo_write_hint` 提供 gate、user-action 和 agent-todo 模板，却没有“不通知的可选人类请求”模板；
- 一个打开的 `user_action` 即使非阻塞，也可能进入用户通知通道；
- 已退役的 `todo suggest` 只生成 advisory prompt；已退役的
  `todo capture-followups` 只写 agent work。两者都没有提供持久化 human wish 路由；
- compact turn envelope 带有必须执行的动作和写回，却没有签名过的可选 sidecar 提示。

结果是一种可以避免的生产偏差：agent 要么把可选价值升级成 blocker，要么制造提醒噪音，要么遗忘它。

## 2. 决策

LoopX 把面向人的注意力建模为三种语义：

| 类型 | 存储形态 | 是否阻塞选中工作 | 是否授予权限 | 默认呈现 |
| --- | --- | --- | --- | --- |
| `gate` | `task_class=user_gate` | 仅当显式 scope 覆盖该动作 | 仅通过既有 typed decision-scope receipt | 用具体问题中断并询问 |
| `request` | `task_class=user_action`，字段缺失或为 `request` | 否 | 否 | 保持现有非阻塞通知行为 |
| `wish` | `task_class=user_action`，`human_attention_kind=wish` | 否 | 否 | 只顺带呈现或集中摘要 |

`user_gate` 仍是唯一可以携带 blocking scope 或消费 decision authority 的用户 todo 类型。Wish 是 agent 对“可选人类杠杆”的假设，不是弱批准，也不是延迟 gate。

初始实现复用 canonical user-todo store。不向 `task_class` 增加 `user_wish`，不创建另一套任务数据库，也不引入新的 built-in capability。最近的 owner 仍然是：

- `control_plane/todos`：类型化 metadata、写入、去重与投影；
- `control_plane/work_items/interaction_contract`：通知与 agent channel 语义；
- `control_plane/heartbeat` 与 `loopx-project`：活跃的 model-facing 写入规则；
- `control_plane/quota/turn_envelope`：紧凑、签名过的 sidecar 提示。

## 3. 类型化协议

### 3.1 存储字段

最小存储扩展是：

```json
{
  "task_class": "user_action",
  "human_attention_kind": "wish",
  "wish_key": "ux:onboarding-preference",
  "bound_agent": "agent-id",
  "text": "If convenient, review the proposed onboarding default.",
  "evidence": "todo_1234 or PR #123"
}
```

规则：

- `human_attention_kind` 是 `task_class=user_action` 上的 typed enum：`request|wish`。字段缺失时按 `request` 处理，保持向后兼容。
- `wish_key` 是稳定、public-safe 的去重 key。Wish 必须提供它，但它没有 authority 语义。
- 继续使用现有 multi-agent user-todo binding：当当前 user-todo contract 要求时，wish 必须声明 `bound_agent` 或 `goal_bound`。
- 复用现有 `text`、`evidence`、`updated_at`、complete、supersede 与 archive 行为承载内容和生命周期。v0 不新增第二套状态机。
- `action_kind` 仍是可扩展的 domain token。运行时不得从 `text` 或 `action_kind` 子串推断 wish。

### 3.2 非法组合

Wish 携带以下任何字段时都必须校验失败：

- `blocks_agent` 或 `global_gate`；
- `decision_scope` 或 `required_decision_scopes`；
- `decision_outcome`；
- `unblocks_todo_id`；
- `user_action` 之外的 task class。

完成或接受 wish 不会消费 authority requirement。如果后续动作需要 private access、production mutation、publication 或其他 protected action，那个精确动作仍需要普通 `user_gate` 和 decision scope。

## 4. 写入接口

第一个活跃调用点是已发布的 heartbeat 路径。在现有 todo CLI 下增加一个窄 helper，暂定为：

```bash
loopx todo capture-wishes \
  --goal-id <goal-id> \
  --agent-id <registered-agent> \
  --wish-key <public-safe-key> \
  --wish '<optional human leverage>' \
  --evidence '<public-safe pointer>'
```

该 helper 是 canonical user todo 的便利写入器，不是新 store。它必须：

- 写入 `task_class=user_action human_attention_kind=wish`；
- 除非显式提供 goal-wide binding，否则把 response continuation 绑定到写入它的 agent；
- 要求紧凑、public-safe 的 evidence pointer；
- 每个 material turn 最多新增 1 个 wish；
- 同一 `wish_key` 已打开时更新 evidence，而不是追加重复项；
- 限制每个 agent 的活跃 wish 数，并返回 typed `max_items_exceeded` 或 `duplicate_updated` 结果；
- 自身不 spend quota，也不声明 delivery progress。

精确命令形态留给实现评审。以上行为才是协议。已退役的
`todo capture-followups` 批量命令不再作为扩展点；未来实现必须使用 wish 专属的
typed helper，或为 canonical `todo add` 增加不会静默改变 role/task class 的显式选项。

## 5. Skill 与 Heartbeat 生成规则

生成的 heartbeat prompt 和 `loopx-project` skill 应在主任务 validation 之后、accountable refresh/spend 之前增加一条紧凑规则：

> 主任务优先。如果本次 material turn 发现了一个有证据、Human 具有比较优势、但当前选中动作不依赖它的机会，可以选择捕获 0 或 1 个 wish。不得为了满足协议硬造 wish，不得中断主流程询问，也不得把 permission/runtime gap 转成 wish。

符合条件的例子包括：

- 用户偏好可以改善后续切片，而 agent 当前可以安全使用有文档的默认值；
- 有明确预期价值的可选引荐、审阅或领域判断；
- 能提高置信度、但并非选中工作前置条件的有界证据请求。

不符合条件的例子包括：

- credentials、private material access、destructive action、production mutation、publication 或显式 repository review rule：当选中动作依赖它们时，它们仍然是 gate；
- runtime capability discovery 或普通 agent repair work；
- 没有 evidence 或预期价值的未排序想法；
- agent 可以直接加入自身 runnable backlog 的工作。

规则刻意写成 `0..1`，不是 `1`。Wishlist capture 不能变成新的输出 quota，也不能诱发低价值 prose。

## 6. Interaction 与通知语义

Wish 需要单独的投影 lane：

```json
{
  "user_todo_summary": {
    "wishlist_open_count": 1,
    "wishlist_items": [
      {
        "todo_id": "todo_wish_123",
        "wish_key": "ux:onboarding-preference",
        "text": "If convenient, review the proposed onboarding default."
      }
    ]
  }
}
```

它们必须从以下位置排除：

- `gate_open_items` 与 quota/interaction 的 blocking 或 action-required count；
- `user_channel.actions` 与 `user_channel.action_required`；
- 把非阻塞 `user_action` 转成即时 `user_channel.notify=NOTIFY` 的 predicate；
- `needs_user_or_controller`、selected-todo ranking、work-lane obligation、quota allocation 与 scheduler cadence。

Canonical todo-source lifecycle 的 `open_count` 仍可包含打开的 wish，以保持 source completeness 为真。Consumer 必须使用单独的 wishlist 与 blocking/action projection，不能把这个聚合 lifecycle count 当成 routing authority。

呈现策略是 `piggyback_or_digest`：

- 当一个 turn 本来就返回 material user-visible result 时，最多可附带 1 个新捕获的 wish；
- 单独一个 wish 永远不能把 `DONT_NOTIFY` 改成 `NOTIFY`；
- status 与 full review packet 可以展示有界 wishlist lane；
- 后续 digest consumer 可以总结 wish delta，但 v0 不引入 recurring wishlist scheduler。

## 7. Compact Packet 协议

完整 quota payload 应在 `todo_write_hint` 中加入精确的 wishlist writer template。Compact turn envelope 应暴露独立的 optional sidecar，而不是把它塞进必须执行的 `next_cli_actions`：

```json
{
  "writeback": {
    "optional_sidecars": {
      "wishlist_capture": {
        "allowed": true,
        "required": false,
        "max_new": 1,
        "timing": "after_primary_validation",
        "delivery": "piggyback_or_digest",
        "affects_execution": false
      }
    }
  }
}
```

该字段属于 model-facing behavior。新增它时必须升级 turn-envelope action-signature coverage，并保持 full/compact 语义一致。未签名的 prose 字段不够，因为 host 可能静默丢失它，model 也可能把它误当成 required action。

只有在 material turn 中、且 primary action 仍是选中的 LoopX work 时，sidecar 才 eligible。它不是 `should_run=false` 时的 fallback，不替代 writeback/settlement，也不是一个会 settle turn 的新 effect。

## 8. Wish 响应与 Promotion

用户可以通过现有 todo lifecycle 忽略、完成、拒绝/supersede 或接受 wish。后续 convenience command 可以原子地：

1. 完成精确 wish；
2. 创建具体 agent successor；
3. 保留 wish id 作为 lineage evidence。

这个迁移提升的是工作优先级，不是 authority。任何需要 protected decision scope 的 successor，在普通 authority receipt 出现前仍然 gated。v0 不从聊天文本或普通 completed `user_action` 推断接受。

## 9. 公共和私有边界

Wishlist generation 必须使用与现有状态相同的 public-safe todo 边界：

- 不含 credentials、raw logs、transcripts、private source body、本地绝对路径、内部链接或私有组织上下文；
- evidence 是紧凑 pointer 或可复用的 public-safe summary；
- 私有机会留在 owner 批准的 ignored local state，除非能安全泛化；
- wish 不能授权读取它所引用的 private material。

Writer 应复用现有 follow-up safety scan，只增加 typed wish validation。不得再增加一套基于 prose denylist 的路由 authority。

## 10. 最小可用实现切片

交付一个 cohesive behavior slice：

1. 在 user todo 上 normalize/validate `human_attention_kind=request|wish` 与 `wish_key`；
2. 增加一个有界、要求 evidence、支持去重的 todo writer；
3. 单独投影 `wishlist_items`，并证明 wish 不进入用户通知通道；
4. 在生成 heartbeat 和 `loopx-project` skill 中加入精确的 optional-capture 规则；
5. 通过版本化 action signature 暴露签名过的 compact optional-sidecar hint。

这个切片有真实活跃调用方：每个 eligible 的已发布 heartbeat 已经执行 primary validation 和 todo writeback。它在产生价值前不需要 dashboard、新 capability、recurring scheduler、acceptance metric 或 auto-promotion workflow。

## 11. 验证标准

第一版实现需要通过 focused tests 证明：

1. 在其他条件相同的 quota state 中增加同一个 wish，不改变 `should_run`、selected todo、`must_attempt`、delivery permission、spend policy 或 scheduler action；
2. wish 永远不会生成 `user_channel.action_required=true`，也不会把 `DONT_NOTIFY` 改成 `NOTIFY`；
3. 当精确 authority dependency 存在时，`user_gate` 仍然优先；
4. writer validation 拒绝所有非法字段组合，并要求 public-safe evidence；
5. 重复捕获同一 `wish_key` 时更新而非复制，active cap 确定且可测试；
6. 在新的 action-signature coverage version 下，full quota 与 compact turn-envelope packet 保持相同 optional-sidecar 语义；
7. 已发布 full、compact、brief、thin heartbeat prompt 都保留 `0..1`、post-validation、non-interrupting 规则；
8. 一个 model-behavior scenario 执行选中的 primary work，并可捕获合格 wish，不能替换该工作或回退到 earlier action；
9. 公私边界扫描拒绝 fixture/docs 中的本地路径、credential、raw evidence 和 private material。

Wishlist capture 本身 no-spend。Material primary turn 仍然通过现有 causal writeback contract settlement/spend。

## 12. 备选方案

### 新增 `user_wish` task class

v0 拒绝。它会扩大每个 task-class switch、CLI validator、state projection、compatibility path 和 external sink；但其存储、ownership 与 lifecycle 已经属于非阻塞 user action。

### 用普通 `user_action` 加 `action_kind` 约定

拒绝。当前 interaction behavior 可能通知每个可见 user action；substring/prose classification 还会让 routing authority 变得模糊。

### 只把 wish 留在 advisory suggestion 中

拒绝。Advisory proposal 需要后续 promotion，无法保存在普通 turn 中发现的小机会。退役独立 suggestion 命令不等于补齐这一生命周期。

### 把每个机会都写成 agent todo

拒绝。有些机会明确依赖 human preference、relationship、judgment 或 optional evidence。把它们写成 executable work 会错误表达 ownership，并污染 runnable frontier。

### 只在 prompt prose 中加入 wishlist generation

拒绝。没有 typed writer、projection 与 signed compact packet hint，行为会在 host 间漂移，并静默退化为 notification spam 或被遗忘的 chat context。

## 13. 出现证据之后的后续工作

只有在第一切片产生真实使用证据后，LoopX 才考虑：

- 用户层的 wishlist visibility/digest preference；
- accept/decline convenience command 与原子 agent-todo promotion；
- 基于 typed lifecycle event 的 value/acceptance metric；
- 渲染既有 wishlist lane 的 external projection sink。

这些都不是 v0 必需项，不应延迟非阻塞写入协议。

## 14. 开放问题

1. Helper 应命名为 `todo capture-wishes`，还是让 canonical `todo add` 接受显式 human-attention kind？
2. v0 应按 agent、按 goal，还是同时限制 active wish？
3. Piggyback 呈现应进入初始切片，还是第一版只通过 status/review packet 暴露 wish？
4. 在专用 typed outcome 出现前，哪一个 public-safe lifecycle field 最适合记录用户的显式 decline？

## 15. 与现有协议的关系

- [Decision Scope v0](../../reference/protocols/decision-scope-v0.md) 仍是 gate 的 authority 来源，也说明了为什么 wish 不能满足 protected action。
- [Interaction Pattern Catalog](../../concepts/interaction-pattern-catalog.md) 定义 scoped-gate fallback；wishlist capture 只扩展非阻塞侧，不改变 IP-003 的 gate 优先级。
- [Project agent todo contract](../../project-agent-todo-contract.md) 仍是 canonical todo ownership 与 lifecycle surface。
- [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md) 仍是 turn result 与 settlement contract；wishlist capture 是 optional sidecar，不是新的 result kind。
- [Model behavior qualification v0](../../reference/protocols/model-behavior-qualification-v0.md) 负责用真实 packet 证明 optional hint 不会挤掉 selected work，也不会把 non-blocking item 变成 gate。
