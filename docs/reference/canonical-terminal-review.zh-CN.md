# Canonical 终结操作的审核与验证

在已显式晋升的本地 Goal 上，Agent 完成与 Monitor 停止现在复用 Todo 编辑、User
完成已有的审核恢复路径：预览绑定完整 provider revision 和注册摘要，保留同一个
operation identity，只有永久投影 outbox 确认当前显示后，Chat 才生成成功显示回执。

## 操作与恢复

CLI 完成后若响应丢失，重试时保留原完成标识：

```bash
loopx todo complete --goal-id example-goal --todo-id todo_work \
  --agent-id agent-a --completion-identity-key reviewed-result
loopx todo list --goal-id example-goal --todo-id todo_work
loopx todo project-markdown --goal-id example-goal --execute
```

确实没有后继时才使用 `--no-follow-up`。有租约的工作还需要当前
`--task-lease-idempotency-key` 和 `--task-lease-expected-version`；用户确认不提供
租约或 lifecycle grant。Chat 重试同一个失败 proposal；stale proposal 需要重新预览，
不能换一个 operation id 绕过审核。

| 边界 | 可观察结果 |
| --- | --- |
| 新审核操作执行前 provider 或注册发生变化 | 私有验证执行前拒绝，Chat 标为 stale |
| 验证期间 provider 发生变化 | 拒绝旧验证结果，Todo 保持未完成 |
| 验证期间租约过期 | 以新的运行时时间重新检查执行证明并拒绝 |
| Canonical 提交成功，但显示失败 | 业务保持提交，Chat 返回可恢复失败，不生成成功显示回执 |
| 提交后的响应或 Chat 回执丢失 | 先恢复原业务回执，再处理当前显示，不用新状态否定历史提交 |
| 相同 operation 改了已审核的 note/evidence/reason/basis | 拒绝 identity 复用，不假装已接受新意图 |
| 完成后私有声明丢失 | 公开摘要足以恢复业务回执；无损显示恢复仍需找回原声明 |

TS terminal owner 统一决定准入、来源新鲜度、验证计划、租约退休、关联效果、CAS 与
回执恢复。Python 传事实，收到请求后解析私有 argv，执行已声明验证并交付投影，
不再决定旧验证是否可以完成当前工作。预览不执行 validator。User 的组合编辑/完成
语义以及已有 Chat proposal 协议保持兼容。

## 协议与迁移边界

随包发布的 Python adapter 和 TypeScript runtime 统一使用
`loopx_local_coordination_todo_terminal_lifecycle_request_v3`，完成和 supersede
都通过 `operation_identity` 明确表达操作意图：

- `{kind: "explicit", operation_id: "..."}`：执行或恢复指定操作，包括旧 runtime
  已写入的历史回执。
- `{kind: "completion_turn"}`：完成带 key 的 `turn_settlement` 交付，或终结已经接受的
  普通完成。TS 推导两个稳定阶段标识；调用者不能另外传 operation id。
- `{kind: "current_monitor_cycle"}`：完成没有显式 completion turn key 的
  `continuous_monitor` 当前轮次。TS owner 根据 Goal id、Todo id 和权威状态中的
  `material_change_generation` 推导操作标识。

Monitor 重开时 generation 递增，因此旧周期回执不能完成当前 open 周期。
如果显式操作已完成当前周期，核心写入该 generation 的 no-change 回执，不会猜测
legacy unscoped 回执属于该周期。三种意图共用同一个终结事务；无 scoped Turn 的完成和 supersede
使用 explicit identity。

同一 terminal method 还接受以下受限字段：

- `review_basis`：若提供，精确包含 `provider_revision` 和 `registry_sha256`，绑定
  已审核意图并进入回执 identity。
- `validation_source_provider_revision`：发出 effect 前为 null，继续执行时传回发出的
  revision。它约束新鲜度，不创建新 operation identity；当前协议的普通验证和 Goal acceptance
  验证都要求它。
- `validation_declaration_sha256`：canonical 的公开声明摘要。先查历史回执，再获取私有
  声明；新执行仍须验证原声明和当前授权。

同一 method 可先返回 `resolve_validation`，再返回 `execute_validation`。两者均绑定
来源 revision，均不提交业务。有验证的新完成从原先两次跨 runtime 请求变为三次
（解析声明、规划 effect、提交）；无验证完成和历史回放仍是一次 terminal 请求。
这次额外调用让恢复不依赖本机 argv，未来原生 host 同时拥有声明解析与 effect 执行后可删除。

删除 v0/v1/v2 请求解码分支。这是随包共同发布的 adapter/runtime 内部请求，
不是持久化操作：两端一起升级，由当前 runtime 重新生成请求。旧版本和顶层
`operation_id` 写法在访问 provider 前即被拒绝。已有回执 schema、operation id 和
请求 fingerprint 不变；读取旧回执不需要保留旧请求解码器。
带审核 basis 的请求若遇到已回退
的 legacy authority，公共 facade 拒绝落入旧写路径。

本次不改变 provider 默认值、晋升、权限、保留策略或存储格式。回滚保留 provider
数据、回执和 writer fence，恢复兼容代码；无法识别请求版本的代码不能执行该请求，
应重新生成兼容预览，不能剥掉审核字段，也不能剥掉 operation identity。Markdown
仍是永久显示。此批闭合终结审核/恢复调用族，不等于
全部 leased metadata、executor-held effect fence、D1–D3 或整 Goal 切换完成。

共享 provider conformance 使用完整复杂 fixture、native/imported 两种记录，覆盖审核/
验证过期、租约过期、提交响应丢失和非目标记录不变。真实 File/SQLite Chat HTTP 测试
检查打包入口与重试反馈。前端运行时 decoder 与共享 action-review plan 现在为 Agent 完成和 Monitor
停止识别 terminal basis，打包 Chat 同步包含原操作重试路径，并区分显示待交付与
已验证完成；无需新增配置项或视觉控件。本次未新增 Lark 命令或传输。

## 受配额约束的 CLI 完成

交付通过原 validator，不等于该 Turn 已结算。共享 TS settlement plan 依次引导普通
Todo 完成、永久 `refresh-state` 写回、一次 `quota spend-slot` 扣额，以及最终
`todo complete --no-follow-up`；全部绑定原 Goal、Agent、Todo 和 Turn。普通完成
保留 `active_goal` continuation，不制造虚假后继。该命令的执行条件是
`todo_deliverable_complete`，必需的验证不会随条件省略。符合准入的
`in_flight_continuation` 保留未交付 Todo 为 open，不会伪造通过验证的完成。

若提前终结、缺少写回或扣额，CLI JSON 和 Markdown 返回同一恢复计划；原 registry/runtime、
project/state 路由和租约证明保留在对应命令支持的参数中。命令模板只是引导，不提供
租约、验证回执或额外执行权限。

`completion_turn` 意图保留旧普通完成的 operation id，同时为最终关闭推导独立、
确定的阶段 id，完成 key 不变。`active_goal` 晋级为 `no_followup` 必须恢复精确匹配
原 actor、租约和声明承诺的普通完成回执，不重跑原 caller validator，也不重新获取
已退休租约。revision 为零的旧回执通过原请求承诺保持可恢复；新声明版本仍必须有
匹配的回执摘要。已有终结回执在读取当前 authority 前恢复；当前 Goal Acceptance
准则仍遵守原新鲜度约束。验证和扣额门禁不放宽。

此补充改变 CLI/managed 引导和 canonical 响应读回，不改变配置。已审核 Chat 操作
继续使用 explicit operation identity 和已有共享投影；无需新增前端设置、视觉控件
或 Lark 传输。
