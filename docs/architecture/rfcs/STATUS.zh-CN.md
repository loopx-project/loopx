# RFC 状态索引

<!-- 由 scripts/generate_rfc_status_index.py 生成；不要手工编辑。 -->

本索引从本目录每个 RFC 自己的状态头生成。改变一个 RFC 的状态只需要改它的头部，
及其中文镜像的头部；README 不缓存生命周期状态。
然后运行 `python3 scripts/generate_rfc_status_index.py --write`；`--check` 在索引过期时失败，`examples/docs-governance-smoke.py` 会调用它。

合入的有效 RFC 即 **已接受**（Accepted），设计合格、可认领；
**已被替代**（Superseded，必须写明 `Superseded by`）、**已退役**（Retired、Rejected）。
合入不证明实现、真实资格验证或晋升完成；交付成熟度见 [README 索引](README.md)。
新 RFC 必须在头部声明 `**替代 / 关闭：**`（`无` 或所替代 / 关闭的旧 RFC 链接）。
带日期的交付记录写进 [ledger/](ledger/README.zh-CN.md)；附录里可以留历史，
但附录之前的正文不允许再出现带日期的记录标题。

[English](STATUS.md) 与本文互为语义镜像。

## 已接受 (37)

| RFC | 头部状态 | 替代 / 关闭 | Ledger |
| --- | --- | --- | --- |
| [RFC: Agent IM, LoopX, And OpenViking Collaboration v0](agent-im-openviking-collaboration-v0.md) | 已接受 | none | — |
| [RFC：Agent Loop Effect Interpreter（v0）](agent-loop-effect-interpreter-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：Agent 会话执行模式（v0）](agent-session-execution-modes-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：自动执行准入（v0）](automatic-execution-admission-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC: Benchmark Study Upload and Dashboard Projection v0](benchmark-study-upload-dashboard-v0.md) | 已接受 | none | — |
| [RFC：强能力 Agent 管家与语义工作交接（v0）](capable-manager-semantic-handoff-v0.zh-CN.md) | 已接受 | 无 | [1 条](ledger/capable-manager-semantic-handoff-v0/) |
| [显式 Todo 接续：阶段 A](cross-session-memory-substrate-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：LoopX 桌面执行前端 v0](desktop-execution-frontends-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：外部证据研究能力 v0](external-evidence-research-capability-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：前沿科学研究计划 v0](frontier-science-research-program-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：Goal Artifact 生命周期投影（milestone / guard / next-transition）v0](goal-artifact-lifecycle-projection-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC: Goal Channel 协作模型 v0](goal-channel-collaboration-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：Goal Direction Baseline（目标方向基线）v0](goal-direction-baseline-v0.zh-CN.md) | 已接受 | 无 | — |
| [Goal 实例身份与孤儿状态恢复（v0）](goal-instance-identity-and-orphan-recovery-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：Goal 级能力组合与 Connector 生命周期（v0）](goal-scoped-capability-portfolio-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC: Per-Goal Usage, Token, and Cost Surfacing v0](goal-usage-token-cost-v0.md) | 已接受 | none | — |
| [DSH / Pi：L1 观察与 Managed Runtime 选型](harness-selection-dsh-pi-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：长程 Agent 分层步幅控制 v0](hierarchical-agent-stride-control-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：Human Attention Wishlist v0](human-attention-wishlist-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：用户确认的垂域操作（v0）](human-confirmed-domain-operations-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：智能化审阅与动态展示面 v0](intelligent-review-presentation-surfaces-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：团队实时工作区 v0](live-team-workspace-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：长程 Harness Benchmark 与研究计划 v0](long-horizon-harness-benchmark-research-program-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：长程 Agent 可靠性诊断与治理交付 v0](long-running-agent-reliability-diagnostics-governed-delivery-v0.zh-CN.md) | 已接受 | 无 | — |
| [LoopX 整体路线总纲 v0：产品、协作、技术与交付](loopx-overall-roadmap-v0.zh-CN.md) | 已接受 | 无 | — |
| [Manager runtime profile v0 / 管家运行模式 v0](manager-runtime-profile-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：Obelisk Session Evidence Provider v0](obelisk-session-evidence-provider-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：Agent 判断与可选独立评估——以 Jev 为候选方案（v0）](optional-semantic-assistance-jev-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：结果后记忆效用归因 v0](post-outcome-memory-utility-attribution-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：Provider-Neutral Post-Writeback Capability Hooks v0](provider-neutral-post-writeback-capability-hooks-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC: Provider-Neutral Turn-Start Inbox Hook v0](provider-neutral-turn-start-inbox-hook-v0.md) | 已接受 | none | — |
| [RFC：研究型探索控制面 v0](research-exploration-control-plane-v0.zh-CN.md) | 已接受 | 无 | — |
| [RFC：语义词表收敛与提交期漂移检查（v0）](semantic-vocabulary-convergence-v0.zh-CN.md) | 已接受 | 无 | [5 条](ledger/semantic-vocabulary-convergence-v0/) |
| [RFC：共享 Goal 对齐与受治理 Amendment 协议（v0）](shared-goal-alignment-and-governed-amendment-v0.zh-CN.md) | 已接受 | 无 | [2 条](ledger/shared-goal-alignment-and-governed-amendment-v0/) |
| [RFC：LoopX 共享控制面权威与可插拔状态 Provider（v0）](shared-goal-authority-state-provider-v0.zh-CN.md) | 已接受 | 无 | [19 条](ledger/shared-goal-authority-state-provider-v0/) |
| [RFC: Single-Owner Local Daemon (v0)](single-owner-local-daemon-v0.md) | 已接受 | none | — |
| [RFC：LoopX 控制面 TypeScript 渐进迁移方向 v0](typescript-control-plane-migration-v0.zh-CN.md) | 已接受 | 无 | [12 条](ledger/typescript-control-plane-migration-v0/) |

## 已被替代 (0)

_无_

## 已退役（Retired 或 Rejected） (0)

_无_
