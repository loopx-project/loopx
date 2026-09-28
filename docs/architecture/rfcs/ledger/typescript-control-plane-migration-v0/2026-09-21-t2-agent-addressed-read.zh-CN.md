# T2 面向 Agent 的读取检查点

于 2026-09-26 从 [typescript-control-plane-migration-v0.md](../../typescript-control-plane-migration-v0.md)（原小节 "T2 Agent-addressed read checkpoint"）移入；中文版原本没有这一小节，本文是 [英文条目](2026-09-21-t2-agent-addressed-read.md) 的语义镜像。RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

Todo 列表选择现在与既有的 typed summary-lanes 批处理组合。Python 侧的
role / status / id / Agent 谓词和独立的 User scope 规则已删除；旧路径与已晋升消费侧
共用 `todos/agent_scope.ts`，并与 quota 和决策 scope 共用范围。显式 gate scope 仍
优先于执行 claim，而保留的 User claim 现在会正确收窄有范围的列表可见性。全源
resume / succession 仍在选择之前判定；原始数组序号在过滤和展示上限之后保留。
不新增选择运行时的跨语言调用、新 capability / provider 或 Python 存储迁移。
Python 保留输入归一化和渲染，直到其实际宿主消费侧完成迁移。见
[读取合同](../../../../reference/todo-work-counts.md)；更广的 L5 / D1 与本地默认资格仍未关闭。
