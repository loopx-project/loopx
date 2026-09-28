# 跨 RFC 实现检查点

于 2026-09-26 从 [capable-manager-semantic-handoff-v0.zh-CN.md](../../capable-manager-semantic-handoff-v0.zh-CN.md)（原小节“4.1 跨 RFC 实现检查点”）原文移入，内容未改；RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

2026-09-13 重新核对 `origin/main`，版本为上述实现基线。以下是源码/历史事实，不是新一轮部署验收。RFC 成熟度和已落地切片分开报告；旧目录或已合并重构标题不足以证明完成。

| 契约 | 此基线已核验的基础 | 仍不属于本文的交付声明 |
| --- | --- | --- |
| **TS 迁移** | Accepted；Stage 1/2A 基础与进行中的 Stage 2B 事务切换。`todos/public_update.ts`、`coordination/todo_update.ts`、`todo_monitor_poll.ts` 和结构化消费者已拥有大量语义规则。 | T0–T4 是持续执行路线，不是全部完成。原生字段/lease/monitor 支持仍有边界。planner 搬到 TS 不等于 commit 也搬了。 |
| **共享权威** | Draft，已有实现基础。`AuthorityStore` 定义条件式持久 state/events/receipt commit、读回和 scan。[#4280](https://github.com/huangruiteng/loopx/pull/4280)、[#4283](https://github.com/huangruiteng/loopx/pull/4283)、[#4287](https://github.com/huangruiteng/loopx/pull/4287) 收敛事务、展示和 retained-journal 语义；已有 File/NoKV 及 SQLite/PostgreSQL 候选路径。 | provider 实现不代表默认晋级或共享服务。D1 投影、D2 profile 资格/soak、D3 有写入隔离的切换，保留各自证据与批准条件。 |
| **共享目标对齐/修订** | Draft，已有 Stage 1/2 基础：`goals/shared_goal_alignment.{py,ts}` 读取当前工作基线；`goal_amendment_proposal.{py,ts}` 校验/保留提案，没有 canonical effect。晋级后 source basis 包含 canonical Todo/lease revision。 | 完整 Goal-intent 版本、Stage 3 受控提交、verifier/lease 影响处理及验收，不由提案准入提供。事件序号或 Todo provider revision 不是完整 Goal revision。 |
| **管家与交接（#4330）** | 现有 manager inbox/context/tracking/return 是迁移来源；第 4 节已列 owner。 | 强能力 profile 晋级、通用 collaboration 事务、A1–A16 仍为提案。本文消费其他 owner，不重新实现它们。 |

补充核验至 `6b337bcbde8457bc3268ec7d2780367ace3c6147`：#4286 已于 `c0b572d2d508bb10c32701057dd71ff4f8eb663c` 合并，`coordination/command_receipt.ts` 成为 canonical Todo 命令共享 recovery owner，归档事务拆至 `todo_archive.ts`。其 File/SQLite/PostgreSQL/NoKV conformance matrix 是 M2 可复用基础，不重新设计。请求/工作提交对账复用原 operation 的 ambiguous recovery 语义。此项删除重复 TS 事务规则，不代表 Python writer、T1/T2 缺口或 D1–D3 晋级条件已完成。
