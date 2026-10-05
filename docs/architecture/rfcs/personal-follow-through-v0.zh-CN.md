# RFC：个人事项持续跟进（v0）

- **RFC 状态：** 已被替代
- **替代 / 关闭：** 无
- **Superseded by:** [强能力 Agent 管家与语义工作交接](capable-manager-semantic-handoff-v0.zh-CN.md)
- **交付成熟度：** 历史提案；不拥有独立验收或推广权威。
- **作者 / 责任人：** 管家、personal-workspace、work-item 与飞书扩展维护者
- **创建 / 最近规范修订：** 2026-10-01 / 2026-10-04
- **语言镜像：** [English](personal-follow-through-v0.md)

## 处置与当前设计

[English](personal-follow-through-v0.md) 与本文互为语义镜像。
原提案的独立生命周期与 M1–M3 计划由已接受的
[管家 RFC](capable-manager-semantic-handoff-v0.zh-CN.md)替代，尤其是
§5.2/§5.3/§5.11/§5.12 及其验收与依赖计划。本次归并设计，不宣称功能实现、
部署、账号授权或验收完成。

保留的[飞书个人事项跟进 profile](../../product/use-cases/office-operations/personal-follow-through.zh-CN.md)
包含来源解释、已核验责任人、个人隐私范围、TypeScript/Node 实现要求、冻结
语义评测和注意力成本试用。它映射到管家 A3/A5–A10/A13–A15/A20 与 M1–M4，
取代原独立交付计划。共享状态、授权、恢复和返回维持既有归属，不批准平行任务/
提议存储或调度器。后续工程与证据沿用这些规范 owner。

## 附录 A：基线证据

原基线 `f49b4a0` 已有飞书、Todo 与报告组件，完整个人承诺桌面流程尚未验收。
下方历史 CLI 证据使用模拟外部 provider，不能关闭管家验收。本文不宣称已分配
运行态 Todo。

## 附录 B：执行记录

[历史执行记录](ledger/personal-follow-through-v0/)保留原始证据与历史 M1 名称。
当前集成证据归[管家执行记录](ledger/capable-manager-semantic-handoff-v0/)。
