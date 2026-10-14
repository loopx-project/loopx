# 当前集成主线上的登记来源处理

当前运行时代码基线：`60d833b13c85135587b725013b247e827663ffb9`（2026-10-13）。
本次在结算失败、选择、旧工作基线及保留值处置合入后，更新既有 #6270 清单。
候选的 `loopx/`、`apps/`、`packages/` 生产树与此 main 完全相同，只增加既有见证和记录。
验收仍沿 [#4789 分阶段范围](https://github.com/loopx-project/loopx/pull/4789)。
**26 行清单不是 26 项正式 producer 证明，也不是 tracker 关闭。** 来源清单 PR 仍需
集成并由 owner 完成最终验收。原先三个 Host-ACK fixture 失败已由 #6344 在主干修复；
不再把另合一次 #6285 当作这些场景通过的前提。

注册表仍为 26 项，已用实际 `task_lease_lifecycle_operation` 替换退休的 `lease_action`。
F1/F2 仍是 **6/26**：五项 kernel 和 settlement-binding witness。其余二十项跨运行时
词表不会因本文件或 decoder 测试获得 F1/F2 分数。精确 owner 符号及允许值仍以
`loopx/semantics/vocabulary_v0.json` 为准；表内 owner 路径相对 `loopx/control_plane/`。

运行时见证从因果输入执行 shipped owner；注入值只证明调用方输入/decoder 边界。
混合来源保留两种义务。兼容项要有实际 reader 和退出条件；不把实际跨界的词表改叫
local-only 来避开责任。本记录不证明全程序可达性、数据流或通用 F6。

## 来源与兼容清单

| 登记词表 | Owner / 槽位 | 已执行来源处理 | 兼容及剩余边界 |
| --- | --- | --- | --- |
| turn_result_kind | `turn_driver/executor.py`, `transaction.py::_result_kind` → generated result kind | 内部选择 + Host/调用方输入。driver/executor/transaction 测试执行结果与拒绝分支；固定 decoder 对每个登记值执行输入见证。 | 接纳 Host 输入不证明真实 Host 产生过该结果；保留 journal/replay 读取。 |
| turn_route | `turn_driver/driver.py`, `loop_controller.py` → plan/envelope route | 内部 route 选择。driver/controller 覆盖可执行、门控、修复、等待和非法 route；生成投影受检查。 | 保留三种 Turn 词表及显式投影；route 不是持久回执。 |
| loop_disposition | `turn_driver/loop_controller.py::decide_loop_disposition` → disposition | 内部决策，由当前 route/result/迭代事实决定；controller 表与失败/重试测试执行 owner。 | 生成 controller 规则与 route/result 分离，不因拼写相似合并。 |
| agent_scope_frontier_action | `agents/agent_scope.py` → frontier action | 内部候选/依赖/handoff 分类；frontier 测试执行耗尽、等待、重新分配和后继分支。 | TS 生成绑定是消费者；lane 耗尽不证明整个 Goal 完成。 |
| effective_action | `quota/decision_summary.py` and registered overlays → final action | 内部 decision/overlay 输出。quota 决策测试执行当前路径，固定 producer 扫描保留明确未知站点。 | 根 decision/frontier 联合与嵌套 replay 槽位分开；不把来源处理当作全部分支可达性证明。 |
| settlement_binding_kind | `effect_program.ts::settlementIdentity` → binding_kind | 对输入身份做内部归一化；既有 binding witness 执行 Todo、replan、unbound 及矛盾组合。 | unbound 不允许扣费；精确身份仍由回执 owner 验证。 |
| task_lease_lifecycle_operation | `work_items/task_lease_lifecycle_request.ts` → operation | 调用方选择的 native 输入。lifecycle 测试执行六种操作、非法输入、CAS 与重试。 | 替换退休的私有 lease_action，不删除租约行为；保留 fence 历史字段缺省，不计入 F1/F2。 |
| settlement_step_kind | `effect_program.ts`, `turn_driver/settlement.ts` → receipts[].step_kind | 内部回执构造 + 外部 envelope 输入。2a 在无 callback 的已提交回放中产生四类 step，并拒绝非法输入。 | 保留顺序前缀、effect 身份与历史回执；注入 step 只证明解码。 |
| settlement_failure_kind | `effect_program.ts`, `quota/settlement_readback.ts`, native lease owners → failure kind | 内部失败 + 接受的 envelope 输入。2a 覆盖 12 个 wire 值、真实 unbound guard 读取及 native 权限拒绝。 | cancelled 已有下方 reader 保留范围与退出条件，不声称内部 producer 或历史使用。租约摘要使用 failure.step，Effect envelope 使用 failure.step_kind，不能伪造 wire 转换。 |
| receipt_bound_monitor_phase | `quota/settlement_readback.ts` → `settlement_phase.ts` → monitor_phase | 从精确 monitor commit 内部派生；2a 执行 poll_due/settled、错误身份和旧 monitor effect id。 | settlement_pending 仅有 consumer 兼容证据，实际 builder 不产生；迁移历史 work-lane reader 后才可删除。 |
| receipt_bound_replay_phase | `quota/settlement_readback.ts` → `settlement_phase.ts` → replay_phase | 从隔离回执文件和完成/写回/扣费前缀内部派生三种 phase；重复读取与身份负例经过 bridge。 | 保留 terminal-to-replay 适配与原绑定；phase 投影不证明所有持久 writer 正确提交。 |
| delivery_workspace_kind | `agents/workspace_guard.py::capture_delivery_workspace` → TS snapshot | 真实 Git/文件观察 + snapshot 输入；主 checkout、linked worktree、本地 Goal 与 TS normalization 覆盖三类及非法 origin。 | 保留 v0 Git snapshot；不能把 Git 缺失/读取失败转换成本地 Goal 授权。 |
| delivery_workspace_identity_kind | `agents/workspace_guard.py` → `agents/delivery_workspace.ts` → identity_kind | 观察 git_repository/local_goal 并验证 snapshot；同组真实 Git/local 测试验证身份、归属与 peer 隔离。 | local Goal 身份不能满足独立 Git 工作区要求；v0 snapshot 保留迁移读取。 |
| delivery_workspace_requirement | `quota/settlement_workspace_causality.ts` → requirement | 根据明确工作合同内部派生 required/not_required/unknown；覆盖写入、非交付、缺元数据和事件读取。 | unknown 必须修复；无工作区的旧结算例外需要精确且完整的 validation/writeback 回执，部分或错 Todo 不通过。 |
| todo_completion_continuation | `todos/completion_state.ts::selectTodoCompletionState` → continuation | 根据 intent/Todo 内部选择 active_goal/successor/no_followup，同时读取持久 metadata；完整 owner 与非法 authority 测试执行。 | 后继与 no_followup 矛盾时拒绝；保留 metadata normalization，退休的标量 RPC 仍拒绝。 |
| todo_completion_recovery | `todos/completion_state.ts` → recovery | 根据已完成 Todo、关闭 intent 和 identity source 派生两类 terminal recovery；固定及显式 lifecycle 用例覆盖。 | 不重写原完成身份、不创建后继；null 表示没有恢复，与恢复类别分开。 |
| todo_decision_scope_kind | `todos/decision_metadata.ts::normalizeTodoDecisionScope` → kind | 作者声明输入，无自动分类 producer。七类输入见证、非法值、真实 CLI 和精确 kind dominance 测试执行 reader。 | other 已按下方 custom authority 规则编写，不能作为未知输入的 fallback。接纳不授予权限；保留旧 standing-scope codec。 |
| todo_decision_scope_granularity | `todos/decision_metadata.ts` → granularity; `decision_scope.ts` → dominance | 作者声明五级输入，显式 domain 与未知/null/数字负例，加 CLI/dominance 用例。 | 更宽 scope 不能跨 kind 或覆盖精确目标/接收者冲突；不由有界视图推断缺失 scope。 |
| goal_amendment_class | `goals/goal_amendment_proposal.ts` → proposal.amendment_class | 作者按 RFC 声明四类；全类别/未知类别测试与真实 submit/list CLI 提供输入和 journal 证据。 | admission 是 canonical_effect=none 的提案事实，不是共享变更提交或 protected authority 委托。 |
| goal_amendment_proposal_admission | `goals/goal_amendment_proposal.ts::admissionOutcome` → admission | 根据当前 basis、sequence、digest 内部产生 admitted/needs_rebase；相等/过期/未来/canonical/Markdown 情况经过 TS 与 CLI journal。 | 保留过期提案供 rebase，拒绝虚构未来历史；admitted 不等于变更获准。 |
| goal_amendment_proposal_admission_fact | same admission owner → admission_facts[] | 内部产生四类 basis/sequence/digest 事实；组合条件和 basis 转换用例执行对应分支。 | 并存事实不能删减；Markdown/canonical 的零序号不是 event-log 新鲜度，不改变提案格式或提交权限。 |
| scheduler_cadence_transition | `scheduler/state_transition_rules.ts` → cadence transition | 由 identity、间隔、ACK 和失败事实内部产生六种 transition；Python 独立表与 TS coarse owner 用例执行。 | 保留旧 Python characterization 和状态读取；cadence 不授予执行/扣费权限。 |
| scheduler_host_transition | `scheduler/state_transition_rules.ts` → host transition | 内部产生 apply/host-match-ack/failure-suppressed/settled 四类；独立表与负例覆盖当前观察和历史失败。 | 投影不是实际宿主更新；ACK 仍须满足 #6259 当前宿主读回门，不从 fixture 推断宿主更新成功。 |
| handoff_mode | `coordination/handoff_mode_vocabulary.ts`, authority/mode-plan readers → mode | 配置输入 + legacy/default 兼容读取；真实 show/set、非法 frontmatter、quiescence、lease/replay 测试覆盖。 | 新配置仅 soft_claim/hard_lease，legacy 留作升级/回执读取；解析 mode 不等于租约权限。 |
| delivery_outcome | `work_items/delivery_outcome.ts`, monitor/Host/replan producers → outcome | 作者声明 + 机器选择：monitor/Host 产生 surface_only/progress，replan 可降级 outcome_gap；primary_goal_outcome 是作者主张。history/monitor/executor/replan/diagnostic 测试覆盖。 | decoder/claim 检查不独立证明主目标已实现；历史矛盾主张被诊断，不改写为成功。 |
| delivery_continuity_preemption | `quota/should_run_packet.py::_delivery_preemptions_for_route` → `turn_driver/delivery_continuity.ts` | guard 内部事实 + typed 输入：回执、阻塞 lane、replan、repair、拒绝交付；continuity route/selection 测试覆盖抢占与后续重选。 | 继续旧交付不能覆盖当前抢占；其他 qualifier 的同形字符串不自动归此词表，不改绑旧回执。 |

## 两项原未决含义的当前处置

[#6313](https://github.com/loopx-project/loopx/pull/6313) 获得 `loopx-agent` 在精确
head 上的 `APPROVED` 评审，并由 `huangruiteng` 合入
`d3f84dced555b7fa8bdffa33b750de6b1b3ea968`。下面规则已经进入 canonical registry 和
协议。这是具体的评审及 owner 集成记录，不补造人类评审、取消实现或整个 tracker
验收。[原处置记录](2026-10-12-reserved-value-disposition.zh-CN.md) 保留提案时基线。

- **`settlement_failure_kind.cancelled`：Effect/settlement owner。** 保留已接纳的
  reader-reserved 失败输入；保留此前回执/细节，短路后续 callback。它不证明在途
  provider 已停止，不抹除已提交步骤，也不授予 retry/rollback/resume/quota 权限。
  仍没有已识别的内置取消 producer 或历史实际使用证明。退出须经过 owner 评审的
  typed cancellation 合同与真实因果见证，或带 stored/caller 兼容证据的版本化 reader
  迁移；不自动过期、不虚构生产者。
- **`todo_decision_scope_kind.other`：Todo decision-scope owner。** 只在六种具名
  kind 均不能表达具体自定义审批时，由作者明确选择；不知道该选什么不是理由。
  Task 文本解释审批，稳定公开安全的 key 连接 gate 和工作。仍执行原 exact-kind、key、
  granularity、recipient 和 target 规则，`other:global:*` 也不能覆盖具名权限；未知
  输入仍拒绝。[编写合同](../../../../reference/protocols/decision-scope-v0.md#choosing-kindother)
  已提供准则和例子。

原先缺少使用规则的问题不再写成当前含义未知；输入和兼容处置仍不获得内部 producer
证明。#4447 仍须完成来源清单集成与最终版本验收，不能仅凭这份处置已合并就关闭。

## 证据与复跑

使用目标源码 checkout、`uv sync --extra test`、合格 Node 22.22.3 及
`npm ci --ignore-scripts --no-audit --no-fund`。确认 Python 导入来自该源码。
[英文同版](2026-10-12-registered-source-treatment.md#evidence-and-reproduction)
逐组列出精确测试文件：基础 Turn/frontier/quota/binding；2a settlement/readback/lease；
2b workspace/completion/decision-scope；2c proposal/scheduler/handoff/outcome/continuity；
历史 packet 及生成物。这里复用既有证据，不创建新验收数据库。

Python 命令为 `uv run --extra test python -m pytest -q <所列文件>`；TS 命令为
`node --no-warnings --experimental-sqlite --experimental-strip-types --test <所列文件>`。
2a 新增的 unbound guard 通过真实临时文件读取、TS owner 和 Python bridge；排除 owner
用例执行真实 native lease 拒绝并验证无租约写入。外部失败输入现在显式覆盖 12 值，
registry 对比只防止新值遗漏其 witness，不把枚举遍历升级为 producer 证明。
scope kind/granularity 用例是输入见证；other 的作者选择遵循当前协议。预期来自接受的合同和
独立因果场景，而非回显 owner 结果。

### 当前执行结果

在上述 main 运行时代码加来源见证上，以 Python 3.11.7、合格 Node 22.22.3 执行：

| 组 | 结果 | 含义 |
| --- | --- | --- |
| 前述 28 个 Python 领域文件：Turn/frontier/workspace/Todo/amendment/scheduler/handoff/continuity/history/local-provider | 610 passed | 真实领域 owner，含隔离 Git/File/SQLite；不证明真实模型或 Host 定时器。 |
| `tests/architecture/test_semantic*.py` 与 `test_cross_runtime_value_notes.py` | 706 passed | scanner/registry/owner/value-note/兼容契约，不是全程序生产证明。 |
| 前述 TS 组，另含 action-selection admission 与 interaction contract | 448 passed，无 skip | 类型化来源和消费边界，不是 PostgreSQL 或部署端资格。 |
| 完整 settlement CLI、decision-scope CLI/consistency/lifecycle 与 2a 见证 | 209 passed，无 skip | 三个原始 ACK 场景现已在当前主干代码通过；新增来源见证仍在本 PR。 |
| 当前主干 monitor unavailable-recovery、closeout 与 target-key replay | 52 passed | 补充近期 readback 变化的真实 owner 路径；不证明真实 Host 定时器或部署端恢复。 |

原先失败的 `test_standard_codex_app_settlement_is_receipted_and_idempotent` 和两个
`test_todoless_autonomous_replan_settles_quota_refresh_spend_chain` 变体，现在单独运行
及放入上述完整分组都通过。相关生产代码和 settlement CLI 测试与 main 相同。
`db5a298a54f47179b9be2a3b9cf2f775b71a9274`
（[#6344](https://github.com/loopx-project/loopx/pull/6344)）已补入实际合成宿主观察；
Todo-less 场景保留缺失/不匹配 manifest 的拒绝、无 scheduler 写入和一次扣费。
普通场景仍先拒绝过期 Turn，再确认当前 Turn。原 ACK、持久 scheduler 状态和字节
稳定重放断言均通过，没有放宽宿主回读规则。

[#6285](https://github.com/loopx-project/loopx/pull/6285) 仍是独立开放提案，当前 head 为
`f6c8aae7a7e8ba917861992a1ad21eb29477bf26`。其剩余差异包括共享 fixture、普通 Turn
额外缺失/不匹配反例和 applied-RRULE 显式回读；不能说它与 main 字节相同或已经合并。
这些剩余增强不会使原三个主干失败重新成立，不在本 PR 复制其补丁，也不为了给同一
fixture 修复重复记账而要求再合一次。

旧结果保留原截面：main `509c38c502d3d0b4eafb01550efcdc4b5b5a1d17` 的该组为
206 passed / 3 failed；另一个加入当时 #6285 fixture 的隔离组合中，settlement CLI
101 项通过。当前验收使用上面的新主干运行时结果，不把历史失败改写成当时已成功。

完整 drift 仍保留 F1/F2 6/26、F3 0/26、F4 4/4、F5 1/1、F6 0/0；未知 producer
站点继续显式报告：45 个未解析，其中 17 个不能提供 producer 证据；不放宽上限或扫描
边界。value-note 用例已计入上方 706 项，不重复累加；生成器、typecheck 和对应质量检查
在 PR 验证记录中披露。旧 66/564/82/706 Python、421 TS 数字属于 `43173c0b8`
历史截面，不重新贴成当前结果。

<details>
<summary>当前证据各组的精确选集</summary>

```sh
uv run --extra test python -m pytest -q \
  tests/test_loopx_turn_driver.py \
  tests/test_loopx_turn_executor.py \
  tests/test_loopx_turn_transaction.py \
  tests/test_loop_turn_loop_controller.py \
  tests/control_plane/test_agent_scope_frontier_contract.py \
  tests/control_plane/test_quota_run_decision.py \
  tests/control_plane/test_delivery_workspace.py \
  tests/control_plane/test_workspace_git_fact_reads.py \
  tests/control_plane/test_delivery_workspace_causality.py \
  tests/control_plane/test_delivery_workspace_causality_runtime.py \
  tests/control_plane/test_task_lease_cli_native.py \
  tests/control_plane/test_todo_completion_state_runtime.py \
  tests/control_plane/test_goal_amendment_proposal.py \
  tests/control_plane/test_goal_amendment_proposal_cli.py \
  tests/control_plane/test_goal_amendment_proposal_lifecycle.py \
  tests/control_plane/test_scheduler_state_transition_rules.py \
  tests/control_plane/test_scheduler_state_runtime_integration.py \
  tests/control_plane/test_goal_handoff_mode_cli.py \
  tests/control_plane/test_handoff_mode_source.py \
  tests/control_plane/test_delivery_continuity_selection.py \
  tests/control_plane/test_delivery_continuity_runtime.py \
  tests/control_plane/test_native_monitor_poll.py \
  tests/control_plane/test_replan_writeback_projection.py \
  tests/control_plane/test_refresh_state_replan_gate.py \
  tests/control_plane/test_final_outcome_diagnostics.py \
  tests/control_plane/test_protocol_packet_history.py \
  tests/control_plane/test_protocol_packet_retirement_cli.py \
  tests/control_plane/test_local_provider_settlement_journey.py

node --no-warnings --experimental-sqlite --experimental-strip-types --test --test-concurrency=1 \
  tests/control_plane_ts/turn_settlement.test.ts \
  tests/control_plane_ts/quota_settlement_readback.test.ts \
  tests/control_plane_ts/task_lease_acquire.test.ts \
  tests/control_plane_ts/task_lease_lifecycle.test.ts \
  tests/control_plane_ts/delivery_workspace.test.ts \
  tests/control_plane_ts/settlement_workspace_causality.test.ts \
  tests/control_plane_ts/todo_completion_state.test.ts \
  tests/control_plane_ts/decision_metadata.test.ts \
  tests/control_plane_ts/decision_scope.test.ts \
  tests/control_plane_ts/goal_amendment_proposal.test.ts \
  tests/control_plane_ts/scheduler_state_transition_rules.test.ts \
  tests/control_plane_ts/handoff_mode_plan.test.ts \
  tests/control_plane_ts/delivery_continuity.test.ts \
  tests/control_plane_ts/delivery_history.test.ts \
  tests/control_plane_ts/action_selection_admission.test.ts \
  tests/control_plane_ts/interaction_contract.test.ts

uv run --extra test python -m pytest -q \
  tests/control_plane/test_quota_settlement_cli.py \
  tests/control_plane/test_todo_decision_scope_cli_validation.py \
  tests/control_plane/test_todo_decision_scope_consistency.py \
  tests/control_plane/test_todo_decision_scope_lifecycle.py \
  tests/architecture/test_settlement_receipt_source_boundaries.py

uv run --extra test python -m pytest -q \
  tests/architecture/test_semantic*.py \
  tests/architecture/test_cross_runtime_value_notes.py

uv run --extra test python -m pytest -q \
  tests/control_plane/test_monitor_poll_unavailable_recovery.py \
  tests/control_plane/test_monitor_unavailable_closeout_cli.py \
  tests/control_plane/test_monitor_target_key_replay.py
```

</details>

## 集成状态与关闭边界

此主线已包含 #4772/#4777 的事实修正、#4775 live composition、#4783 settled replay、
#4788 rejected selection、#4786 inbox composition、交叠正确性 #4764、binding witness
#4747 与 2a #4795。#4794 已停止新 `protocol_action_packet` 写入，保留历史读取和签名
投影；其余五个旧字段继续可见，全字段退休不是关闭前提。#5317 已交付独立 #4743 的
开发期 advisory，因此旧“从未实现/AGENTS 没有指引”说法过期；advisory 仍不阻断或证明语义。

当前基线已包含 #6251 typed-failure、#6268 canonical-selection、#6260 shared
consistency、#6290 legacy-work-basis 及 #6313 含义处置。它们消除了具体代码/含义
缺口，不扩大 #4447 原关闭范围。原 Host-ACK 阻塞已在主干解决；#6270 新来源见证与
清单仍待集成。Stage 3 与 issue 为此集成及 owner 最终验收保持开放，验收须点名一个
revision 及其 passed/failed/untested 边界；#6285 剩余增强不是本 tracker 的新关闭条件。安装前端/Lark、真实模型、完整
Goal/团队资格和通用 F6 不属本 tracker；本次不改生产 provider/权限/持久格式，日志及
本地 Goal 状态保留在私有位置。
