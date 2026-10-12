# 保留的 settlement 输入与自定义决策 scope 处置

源码基线：`323c3053128e41e16df27e6b6498f83c37e81f5e`。
关联 [#4447](https://github.com/loopx-project/loopx/issues/4447) 原 Stage 2a/2b
来源处置与 Stage 3 owner 决定。这是待评审、待集成的具体提案，不是先前维护者批准的
记录；不关闭 issue，也不替代 [#6270](https://github.com/loopx-project/loopx/pull/6270)
中的 26 项来源清单。

## `settlement_failure_kind.cancelled`

将 `cancelled` 保留为既有词表中 reader-reserved 的 wire 值。Python 与 TypeScript
解码器已接纳它，当前内置 settlement 分支不产生它。Host、chat、lease 或 usage
契约中的取消不能算作它的 producer；不会为了让值看起来存活而新增取消机制。

既有 `compatibility_only` 元数据现在说明保留这一输入的理由与退出条件。这是兼容
承诺，不证明历史上真的有 producer 发出过该值。传入 `cancelled` envelope 仍是失败：
bind 在下一 callback 前停止，保留之前的 receipt 和 failure details。它不抹除已提交
步骤，也不证明在途 provider effect 已停止，不授予自动 retry、rollback、resume 或
quota 权限。实际结果未知的 provider effect 仍由原 observation/reconciliation owner
处理，不能通过改名为 `cancelled` 消除未知。

只有经过 owner 评审的以下变更可以替代该保留处置：

- 真实类型化取消契约，区分 dispatch 前取消与 dispatch 后不确定效果，提供 producer、
  真实 caller 见证并同步来源说明；或
- 明确的版本化 reader 迁移/移除，具备 caller 与 stored-envelope 兼容证据。

没有自动到期、虚构的外部 canceller、解码收窄或强制词表退休。F1/F2 保持既有六项域。

## `todo_decision_scope_kind.other`

当六个命名 kind 均不能描述真实审批时，作者可显式选择 `other` 自定义决策 authority。
命名 kind 适用时优先使用它；不清楚要决定什么时应澄清，不能猜测 `other`。既有任务
文本说明 owner 要决定的事项，稳定 public-safe scope key 供 gate 和依赖工作共同使用。
详见[编写规则与示例](../../../../reference/protocols/decision-scope-v0.md#choosing-kindother)。

例如 `other:action:study_conflict_review` 表示 owner 要求的利益冲突声明审核。
读取私有数据、资源消耗、方向改变和公开发布分别保留它们的命名 kind，不被这一审批
覆盖。`other:action:study_method_review` 是另一个自定义决定，不是同义项或 fallback。

匹配仍由与其他 kind 相同的类型化 owner 决定，始终要求 kind 相同，所以
`other:global:*` 也不能覆盖命名 kind。key、granularity、recipient 与准确 Todo target
规则不变。未知或拼错的 kind 仍拒绝，parser 不会把它们转成 `other`。声明 kind 不是
批准：既有 linked-gate completion 或 standing authority 只消费实际覆盖的 scope。

## 证据与剩余验收

复用已有 settlement 全值 decoder、未知输入与 bind 短路见证；扩展实际 IPC/decoder
路径，验证保留失败及之前已提交 receipt 和 details。这是 reader 覆盖，不是内部生产
证据。复用既有 Todo lifecycle helper 与原生 TypeScript 匹配，在同一工作上同时放置
指定 custom 审批、另一 custom requirement 和命名权限 requirement。只允许消费
匹配的 custom scope。宽 custom scope 不能覆盖任何命名 kind，非法输入也不能变成
自定义 authority。

registry 仍为 26 项，values/owners 不变；预算、形式证明域、wire schema、runtime
默认行为和存储格式均不变。测试不能批准这些处置。#4447 最终验收仍需 owner 评审并
集成本提案、当前来源清单，以及最终集成 revision 上明确 passed/failed/untested 的
证据。旧的 unresolved 记录保留历史意义，不追改成当时已有本决定。
