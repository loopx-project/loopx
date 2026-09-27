# Goal Channel post-writeback diagnostics / 写回后通知诊断

## Boundary / 边界

`refresh-state` may commit its primary writeback before automatic Goal Channel
notification fails. `goal_channel_gate_sync.failure` adds transport diagnostics;
it does not alter the TS-owned settlement, admission, pause/resume or replay
decision. Python remains the existing Lark I/O adapter, not a second policy owner.

`refresh-state` 的主写回可能已经提交，而随后自动群通知失败。
`goal_channel_gate_sync.failure` 只补充传输诊断，不改变 TS 权威的结算、
准入、暂停/恢复或重放决定。Python 保留为现有 Lark I/O 适配器，不新增决策源。

## Additive packet / 增量字段

```json
{
  "schema_version": "loopx_goal_channel_gate_failure_v0",
  "stage": "provider_send",
  "reason_code": "timeout",
  "external_write_status": "unknown"
}
```

Stages distinguish `lifecycle` (unknown origin), `extension_activation`,
`binding_resolution`, `gate_selection`, `provider_preflight`, `provider_send`,
`provider_readback` and `receipt_write`. Reasons are fixed codes, never exception
messages, command arguments, provider output, paths or channel identifiers.
`failure_summary` supplies the same bounded diagnosis to CLI error rendering.

阶段区分来源未知的 `lifecycle`、扩展激活、绑定解析、门禁选择、发送前检查、
发送、读回和本地回执持久化。原因只用固定代码，不携带异常正文、命令参数、
provider 原始输出、路径或群身份。`failure_summary` 为 CLI 错误显示提供同一安全诊断。

Write status is `not_attempted`, `not_performed`, `performed` or `unknown`.
A send timeout or missing usable provider response is **unknown**, not proof
that nothing was sent. A successful send followed by readback or receipt-write
failure remains **performed**. The legacy boolean alone cannot express unknown.
An escaped exception with unknown lifecycle origin also reports **unknown**.
The adapter observes the existing command once and never initiates a retry.

写入状态为未尝试、已知未写入、已写入或结果未知。发送超时或缺少可用回执为
**未知**，不能据此断言未发送。发送成功后读回或本地回执失败仍保留**已写入**事实。
旧布尔字段无法表达未知；新增字段补足它。适配器只观察原命令一次，不主动重试。
异常逃逸到来源未知的生命周期兜底层时，同样报告**未知**，不能推断未发送。

## Recovery and entry points / 恢复与用户入口

Keep the returned primary settlement and original Turn identity; follow its
existing recovery actions rather than replay business work or debit again.
Notification retries retain existing semantic/provider idempotency. Disabled,
suppressed, unselected and cooldown paths remain unchanged and perform no extra
provider operations. This is not a repair of the existing unverified-receipt
retry policy and does not certify a notification as verified.

保留主结算回执和原 Turn 身份，按已有恢复指令处理，不重跑业务或重复扣额。
通知重试保持现有语义/provider 幂等身份。关闭、抑制、未选中与冷却路径不增加
provider 操作。本切片不修复现有未验证回执的重试策略，也不冒充通知已经验收。

The affected journey is CLI/managed `refresh-state` and its Lark sink adapter.
No configuration field or editor is added, and no second UI state owner is
created. The packet/error can be read by existing CLI consumers; direct display
on the manager frontend is not delivered by this adapter-only change. Tests use
the real CLI/TS settlement plus an isolated provider fixture, not a live chat.

受影响入口为 CLI/managed `refresh-state` 及其 Lark sink 适配器。不新增配置字段、
编辑控件或 UI 状态权威。现有 CLI 消费方可读取诊断/错误；管家前端直接展示不属于
本适配器切片的交付。测试覆盖真实 CLI/TS 结算及隔离 provider，并非实群发送验收。
