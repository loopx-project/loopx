# RFC：Obelisk Session Evidence Provider v0

- 状态：已接受
- 替代 / 关闭：无
- 日期：2026-09-01
- 跟踪 Issue：[#3792](https://github.com/huangruiteng/loopx/issues/3792)
- 决策边界：LoopX 是否以及如何把历史 Agent Session 作为 Replan 与 Turn Admission 的有界证据
- Capability Owner：现有 `agent-turn-recall`
- Provider ID：提议中的可选 `obelisk-session-evidence`
- 交付边界：独立安装的 Extension 或 Package，默认关闭

## 0. 摘要

LoopX 应评估把 [Obelisk](https://github.com/tommy0103/obelisk) 接成一个可选、
只读的历史证据 Provider，用于 Replan 的冷路径。

真正有价值的产品切口不是“每个 Turn 前搜索所有历史 Transcript”，而是在当前控制状态
不足时回答一个更窄的问题：

> 同一个 Agent 在同一个项目里是否已经走过这条路，当时发生了什么，拟议的下一步是否
> 真的有新意？

Provider 可以给出有界、带 Provenance 的历史观察；LoopX 继续拥有 Scope、Admission、
Replan 语义、Todo、Quota、证据接纳和动作权限。

```text
类型化 Replan Situation
  -> LoopX 检索策略与 Scope
  -> 可选 Obelisk Adapter
  -> 有界历史证据 Result
  -> public-safe 检索 Receipt
  -> 现有 Replan 推理与 Settlement
```

该能力默认关闭，对工作 Lane fail-open，且检索本身不能完成 Replan Obligation。首个 Pilot
限定为 same-agent、same-project/repository、近期或 revision-aware，并且只读。

## 1. 动机

Replan 用于阻止长程任务重复不变的计划。类型化 Contract 可以要求新的 Surface、
Hypothesis、Probe、Grounded Successor、Blocker，或有 Coverage 支撑的 Exhaustion。但当前
Turn Packet 可能只包含近期 LoopX Outcome；更早的 Codex 或其他 Agent Session 中，可能有
尚未转成 LoopX Canonical Evidence 的重要尝试。

这会产生三个实际缺口：

1. **重复走死路**：旧 Session 中的命令、文件路径或 Hypothesis 已失败，Agent 却重新尝试；
2. **假新意**：Successor 在当前 Packet 中看似新，但实际重复了历史工作；
3. **Handoff 丢失**：接续 Agent 能看到 Durable Control State，却缺少理解历史决策或失败所需
   的有界观察。

Obelisk 把本地 Claude Code、Codex、Kimi、Pi 和 DSH 历史索引到 SQLite/FTS5，并支持
Session、Message、Tool、File、Subagent 和 Workflow 查询。它适合成为缺失观察的 Provider，
但不应成为新的控制面事实源。

## 2. 实证边界

Obelisk 的公开评测支持一个受约束的产品，而不是宽泛的 Memory Injection：

- [Obelisk issue #39](https://github.com/tommy0103/obelisk/issues/39) 显示其会话与
  时间检索较强，重复死路命令从 6.7% 降到 0.5%；但每次查询取回 25–51k 字符，并且 Raw
  Coding Trajectory 对 SWE Retry 没有统计显著提升。
- [Obelisk issue #46](https://github.com/tommy0103/obelisk/issues/46) 显示 Foreign-agent
  Cross-task Archive 没有可测收益；更有希望的信号来自 Agent 自己在同一 Repository 中的
  长期历史，其效率与质量指标有所改善。

因此 Pilot 首先优化的是**减少重复与降低上下文重建成本**，而不是假设 Task Success 必然
提升。Raw History 在进入 Turn 前必须收敛。

## 3. 归属决策

本提案不新增 `obelisk`、`session-database` 或通用 `retrieval` Capability。

Capability Owner 是现有 `agent-turn-recall`，因为它已有明确 Caller Outcome：为 Autonomous
Turn 准备有界的历史 Guidance 或 Context。Replan 路径提供类型化 Situation 与 Retrieval
Policy，不新建第二套 Memory Lifecycle。

Provider ID 提议为 `obelisk-session-evidence`。它应作为可选 Extension/Package 交付，因为：

- LoopX Core 不依赖 Obelisk；
- 它有独立的 Node.js Runtime、Index、CLI、升级与失败生命周期；
- 用户必须显式同意索引本地 Session History；
- Obelisk 是 AGPL-3.0-only，而 LoopX 是 Apache-2.0。

LoopX 不得把 Obelisk 代码 Vendor、静态链接、翻译或复制进 Core。集成只能通过有文档的
协议调用独立安装、未修改的 Obelisk 进程。Packaging 与 License Review 仍是 Promotion Gate；
本 RFC 不构成法律意见。

### 3.1 与相邻 Capability 的关系

| Surface | 拥有什么 | 不会变成什么 |
| --- | --- | --- |
| Replan | Obligation、语义新意、Successor 或 Exhaustion | Transcript Search Engine |
| Agent Turn Recall | Situation、Admission、有界 Recall Context | Durable Memory Store |
| Obelisk Provider | 对已索引本地 Session 的只读检索 | 动作或证据权威 |
| Reward Memory | 经审阅的 Memory Lifecycle 与 Utility | Raw Transcript Archive |
| Explore | Research Frontier、Coverage、Closure | 隐式 Session History Importer |
| OpenViking Adapter | Scoped Context-provider Retrieval | 与未审阅 Session Evidence 等价的来源 |

Obelisk Hit 是历史观察，不是 `reward_memory_active_record_v0`，不得伪装成已审阅 Reward
Memory。未来如果 Decision Context 或 Explore 出现第二个真实 Caller，应在其拥有独立
Admission Policy 后复用 Provider-neutral Historical Evidence Contract，而不是提前抽象。

### 3.2 跨 session 续接 caller

[强能力管家/语义交接 RFC §5.7](capable-manager-semantic-handoff-v0.zh-CN.md#57-会话与产品连续性) 提议明确的 `resume_or_handoff_gap` 恢复 caller：先恢复当前工作和已有主动续接 brief（包括适用时的 [Stage A](cross-session-memory-substrate-v0.zh-CN.md) 上下文），再只查询缺失的决策证据。这是拟议集成，不代表 caller 已交付或本 provider 晋级。同 Agent 更换 session 可满足初始 scope；另一接收者不能从 handoff 继承来源 Agent 的 archive 检索权。有权限的来源摘录可随 brief 传递，不同时授予 archive 搜索。下文 provider 阶段、默认关闭、scope、私有结果/公开回执边界及验收要求不变。基础续接必须在没有 Obelisk 时成立。

## 4. Admission Policy

Provider 不在每个 Turn 调用。只有以下类型化 Situation 才允许检索：

1. `replan_repetition`：两次以上实质不变的尝试，或显式重复路线信号；
2. `hypothesis_history_gap`：当前 Hypothesis 已耗尽，但历史 Coverage 未知；
3. `resume_or_handoff_gap`：Durable State 能定位工作，但早期决策或失败证据缺失；
4. `operator_requested_history`：Operator 显式要求历史证据。

默认 Scope 为：

- 同一个 `agent_id`；
- 同一个 LoopX Project 与 Repository Identity；
- 近期 Session，或与当前 Repository Revision 相关的 Session；
- 显式 Evidence Type 和有界 Result 数量/大小。

Cross-agent、Cross-project 与 Foreign Archive 默认拒绝。只启用 Extension 不能扩大这些
Scope；每个更宽 Scope 都需要单独配置、隐私披露和 Qualification。

Provider 不可用、Index 不完整或零命中时，返回类型化空结果；不创建 User Gate，也不阻塞
Agent 使用当前证据继续工作。

## 5. 提议的类型化 Contract

TypeScript Control-plane Boundary 应拥有 Schema、Admission Reducer、Size Limit 和 Receipt
Projection。Python 可以调用或适配 Provider，但不得重建 Policy 或 Replan 语义。

### 5.1 Request

```ts
type HistoricalEvidenceRequestV0 = {
  schema_version: "historical_evidence_request_v0";
  request_id: string;
  situation_id: string;
  trigger:
    | "replan_repetition"
    | "hypothesis_history_gap"
    | "resume_or_handoff_gap"
    | "operator_requested_history";
  scope: {
    agent_id: string;
    project_id: string;
    repository_id: string;
    allow_cross_agent: false;
    allow_cross_project: false;
  };
  revision?: { current: string; merge_base?: string };
  evidence_types: Array<
    "prior_failure" | "decision" | "file_history" | "tool_outcome" | "summary"
  >;
  query_terms: string[];
  limits: {
    max_sessions: number;
    max_items: number;
    max_snippet_chars: number;
    max_total_chars: number;
  };
};
```

`query_terms` 必须从 Canonical Situation Field 中产生有界搜索词，而非 Raw Chat History。
Adapter 必须把它们作为 Data 转义，禁止从模型输出生成任意 JavaScript。

### 5.2 Private Result

```ts
type HistoricalEvidenceResultV0 = {
  schema_version: "historical_evidence_result_v0";
  request_id: string;
  provider_id: "obelisk-session-evidence";
  provider_version: string;
  index_revision: string;
  scope_applied: {
    agent_id: string;
    project_id: string;
    repository_id: string;
    cross_agent_used: false;
  };
  freshness: "fresh" | "stale" | "incomplete" | "unknown";
  items: Array<{
    session_ref: string;
    message_ref?: string;
    evidence_type:
      | "prior_failure" | "decision" | "file_history" | "tool_outcome" | "summary";
    observed_at?: string;
    revision_ref?: string;
    snippet: string;
  }>;
  truncated: boolean;
  omitted_count: number;
};
```

Private Result 只能在获准的 Turn 内消费，不能复制到 Status、Todo Metadata、Public Evidence、
PR 文本或日志。进入 Prompt 前必须限长；Truncation 必须显式。

### 5.3 Public-safe Receipt

```ts
type HistoricalEvidenceReceiptV0 = {
  schema_version: "historical_evidence_receipt_v0";
  request_id: string;
  provider_id: "obelisk-session-evidence";
  provider_version: string;
  index_revision_digest: string;
  scope_digest: string;
  query_digest: string;
  result_digest: string;
  result_count: number;
  evidence_type_counts: Record<string, number>;
  freshness: "fresh" | "stale" | "incomplete" | "unknown";
  truncated: boolean;
  omitted_count: number;
  grants_new_action_authority: false;
  external_writes_performed: false;
  raw_content_captured: false;
};
```

不透明 Session/Message Ref 可以留在 Owner-local Private State 中用于调试和 Replay；Public
Receipt 只包含 Content-free Digest 与 Count。Receipt 只证明一次 Scoped Lookup 发生过，不能
证明历史陈述为真，也不能证明 Replan Obligation 已完成。

## 6. Provider Protocol 与失败行为

Obelisk 当前暴露 Free-form JavaScript Query Script 和 Read-only SQL，而不是稳定、有界的
JSON Request Envelope。首选方案是推动上游提供 Structured JSON Query Interface，支持显式
Filter/Limit 并拒绝 Unknown Option。

MVP 最多可以使用一个固定、版本化的 Query Template，前提是所有值都作为 Data 转义、所有
Option 都在 Allowlist 内、返回 Row 的最终 Scope 被再次验证，并且解析前已经限制输出。
禁止执行 Model-authored JavaScript 或 SQL。

Adapter 对 Scope 与 Parsing fail-closed：

- Unknown Request Field 或 Provider Option：拒绝；
- 缺少 Agent/Project/Repository Filter：拒绝；
- 返回 Row 越出 Requested Scope：拒绝整个 Result；
- Index stale/incomplete：显式返回 Freshness，不能解释为“没有历史”；
- Timeout、Malformed Output、Provider 缺失：类型化空失败；
- Oversize Output：终止并返回 Truncation/Failure Metadata，不把部分 Raw Output 泄漏进
  Public State。

这些要求直接覆盖已知上游风险：CI 覆盖不足
([#34](https://github.com/tommy0103/obelisk/issues/34))、Unsupported Option fail-open
([#94](https://github.com/tommy0103/obelisk/issues/94))、Index Cost
([#75](https://github.com/tommy0103/obelisk/issues/75)、
[#105](https://github.com/tommy0103/obelisk/issues/105))、Tokenizer False Positive
([#76](https://github.com/tommy0103/obelisk/issues/76)) 和 Codex 同 mtime 更新导致 Index Stale
([#104](https://github.com/tommy0103/obelisk/issues/104))。

## 7. Replan 集成

Historical Retrieval 只改进推理，不新增 Replan Terminal State。

```text
Replan Required
  -> 分类当前 Coverage 与 History Gap
  -> 若 Admission 允许，检索有界 Same-scope History
  -> 把拟议路线与历史观察比较
  -> 产出现有合法 Replan Outcome
```

合法 Outcome 仍需满足现有语义推进之一：

- 新 Surface；
- 新增或修订 Hypothesis；
- 新的 Falsifiable Probe；
- Grounded Runnable Successor；
- 新证据支持的 Blocker；
- 有 Coverage 支撑的 Exhaustion 与 No Follow-up。

“Obelisk 返回结果”或“Obelisk 没返回结果”都不是合法 Completion Rationale。对于 Incomplete
或 Stale Index，未检索到内容绝不能作为“没有历史尝试”的证据。

## 8. 产品生命周期

以下命令是提议中的 UX，并非当前已发布 CLI：

```bash
# 安装独立 Provider Distribution。
loopx extension install obelisk-session-evidence

# 为一个 Agent/Project Surface 显式启用。
loopx capability enable agent-turn-recall \
  --provider obelisk-session-evidence \
  --surface replan-history \
  --agent-id <agent-id> \
  --project-id <project-id>

# 回读配置、Provider Version、Index Freshness 与 Allowed Scope。
loopx capability status agent-turn-recall \
  --provider obelisk-session-evidence \
  --project-id <project-id> \
  --format json

# 禁用，不删除用户的 Obelisk Index。
loopx capability disable agent-turn-recall \
  --provider obelisk-session-evidence \
  --project-id <project-id>

# 只卸载 Adapter Distribution；External Obelisk Data 仍由 Obelisk 与用户管理，
# LoopX 不得静默删除。
loopx extension uninstall obelisk-session-evidence
```

Enablement 只授予读取已配置 Historical Scope 的能力，不授予 Action Authority、External
Write、Cross-agent Access、Memory Publication、Todo Mutation 或删除 Obelisk Data 的权限。

## 9. 交付阶段

### Stage 0：离线 Qualification

- 从 Public-safe 或 Synthetic Same-repository History 构造 Matched Fixture；
- 测量重复路线识别、False Match、Result Size、Latency、Freshness 与 Scope Enforcement；
- 建立 No-Obelisk 与 Current-context Baseline。

这一阶段不注册 Product Capability，也不自动调用 Replan。

### Stage 1：Operator-invoked Read-only Adapter

- 交付可选 Provider Package，并要求显式安装和启用；
- 只支持 `operator_requested_history`；
- 输出 Bounded Private Result 与 Content-free Receipt；
- 验证 Timeout、Stale Index、Unsupported Option 与 Oversize 行为。

### Stage 2：类型化 Replan Cold-path Admission

- 只有 Matched Evidence 合格后，才允许 `replan_repetition`、
  `hypothesis_history_gap` 和 `resume_or_handoff_gap`；
- 保持 Default-off 与 Same-agent/Same-project Scope；
- 为 Replan 增加比较证据，但不新增 Settlement State。

### Stage 3：Promotion Decision

Promotion 需要可测的重复死路或上下文重建成本下降、有界 Context/Latency、Task Success 与
Evidence Quality 不回归，并且不存在 Silent Scope Broadening。Cross-agent Retrieval、Automatic
Memory Write 与其他 Caller 都需要单独 RFC 决策。

## 10. Validation 与 No-go Criteria

评测应在相同 Replan Case 上比较：

1. 只有当前 LoopX Context；
2. 由类型化 Policy Admission 的 Obelisk Retrieval；
3. 必要时使用 Full Native History 作为 Cost Ceiling，而不是产品默认值。

必须测量：

- Repeated Dead-end Action Rate；
- 到 Grounded Successor 的时间、Provider Call 与 Token；
- Task Success 与 Evidence Quality；
- Same-route/Prior-failure 问题的 Retrieval Precision；
- Private Context Size 与 Truncation Rate；
- Query 与 Indexing Latency；
- Stale/Incomplete Index Detection；
- Cross-scope Rejection 与 Unknown-option Failure。

如果没有可测的重复/效率收益、损害成功率或证据质量、无法可靠限制 Result，或可能静默扩大
Search Scope，就不得 Promotion。单个正面 Anecdote 不足以通过。

## 11. 隐私、权限与安全边界

Session History 可能包含源码、Prompt、身份、本机路径、Secret 与私有组织上下文。因此
Provider 默认保持 Owner-local。

- LoopX 不在 Canonical Public State 存储 Raw Transcript；
- Public Projection 只有 Digest、Count、Freshness、Truncation 与 Provider Metadata；
- Query Term 与 Snippet 是 Private Ephemeral Turn Context；
- Provider Subprocess Output 不得整体复制进 Log 或 Error；
- 本 Contract 不请求或传递 Credential；
- Index 删除、保留与导入仍归 Obelisk/用户管理；
- Retrieval 不能授予权限、满足 User Gate 或授权新的 External Effect。

Adapter 必须检查输出的 Boundedness 与 Scope，但 Content Filtering 不能替代正确 Authorization。
Cross-agent Retrieval 是独立的隐私能力，而非顺手打开的 Query Flag。

## 12. 非目标

本 RFC 不会：

- 把 Obelisk 变成 LoopX 必选依赖或 Built-in；
- 在每个 Turn 查询历史；
- 替代 Replan、Explore、Reward Memory、OpenViking 或 Canonical Goal State；
- 把未经审阅的 Transcript Text 推断成 Authoritative Evidence；
- 自动写入、总结或 Promotion Memory；
- 暴露通用的 Model-authored SQL/JavaScript 执行面；
- 把 Obelisk 的 AGPL 实现复制进 LoopX；
- 承诺 Cross-agent/Cross-project Learning；
- 在出现第二个真实 Caller 前定义 Universal Retrieval Abstraction。

## 13. 开放问题

1. Obelisk 能否提供稳定的 Structured Query Envelope，严格拒绝 Unknown Option，并显式返回
   Index Revision/Freshness？
2. 如何在 Worktree、Fork 与 Remote Rename 之间建立稳定 Repository Identity，同时不泄露
   Private Path？
3. 什么 Revision-window Policy 能平衡长期 Recall 与 Stale History？
4. Stage 1 应放在 LoopX Monorepo `packages/`，还是在 License/Release Lifecycle Review 后放进
   独立 Provider Repository？
5. 多大的 Matched Replan Corpus 才足以把“减少重复”与一般模型方差区分开？
