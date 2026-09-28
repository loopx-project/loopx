# 跨 RFC 的语义与展示 conformance 检查点（2026-09-12）

于 2026-09-26 从 [shared-goal-authority-state-provider-v0.zh-CN.md](../../shared-goal-authority-state-provider-v0.zh-CN.md)（原小节“跨 RFC 的语义与展示 conformance 检查点（2026-09-12）”）原文移入，内容未改；RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

TypeScript 重构 RFC 与本 provider RFC 现在共享一个显式的 Todo 语义边界。
Python 生产 caller 直接从 `todos/todo_semantics.py` 导入；`todos/projection.py`
只作为外部集成所需的 import 兼容 facade 保留，不再是第二个 kernel。这是 owner
收敛，不是新增一套规则。TypeScript 的 typed `projection_delivery` union 也明确区分
mutation intent（`pending`/`not_required`）与 provider readback（`delivered`/`current`）；
未知状态在 acknowledgement 之前 fail closed。

优先级意图现接入 File、SQLite、PostgreSQL 既有的准入 create/update 事务。
显式设置/清除、参数缺省及与旧文字前缀的冲突由 `todos/priority.ts` 处理，Python
读取共享生成的语法。Markdown 保留兼容展示，native record 保存一致的 priority/title。
CLI 与经过审阅的 Chat 编辑保留 CAS 和历史重试身份。真实后端回读及长期本地 Goal 的
一次性隔离副本验证这条边界，见[调用合同](../../../../project-agent-todo-contract.md#priority-intent)。
这不改变 provider 默认，也不关闭其余 promotion 门禁。

展示语义属于 projection 层，而不是 domain record。`source_section` 与 `index` 是 v0
wire shape 的展示坐标；native record 根据 role/archive state 推导相同的展示 section，
并以时间戳和 Todo identity 做确定性回退，不制造假的持久 index。因此即使 wire shape
不同，normalized presentation metadata 仍只有一份 contract。同一规则由
production-scale fixture 以及 File、SQLite、NoKV conformance arm 共同覆盖。Provider
自己的 revision token 仍由各自 provider 管理，只用于 provider-specific replay 规则，
不被归一成 Todo 语义。

本检查点只改变 read/ordering 与兼容 adapter 语义：不晋升 provider、不增加 writer，
不改动 #4280 交付的 transaction decoder，也不把 Markdown 变成第二权威。共享 RFC
继续负责 durable truth、恢复、cutover 与 projection delivery；TS RFC 负责业务规则
owner 与 caller 删除。
