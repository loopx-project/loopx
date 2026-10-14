# Settlement 与 receipt 的可执行来源边界

当前读回见[登记来源处理](2026-10-12-registered-source-treatment.zh-CN.md)。下文保留原基线观测，不代表当前 PR 状态。


基线 `36134771355f05c9bcc5657ce739bb86426383dc`，对应 #4447 Stage 2a。
#4789 的范围修订仍是提案。本条不改变 RFC 验收或 F1/F2 生产域：仍为
**7/26**，本组四项仍在域外。来源处理与生产扫描纳入是不同结论。

- **Settlement envelope（`settlement_step_kind`、`settlement_failure_kind`）。**
  `turn_driver/settlement.ts::reduceTurnSettlementTransaction` 调用共享
  `effect_program.ts` builder；`result.receipts[].step_kind` 与
  `result.failure.{kind,step_kind}` 经 `effect_runtime_result` 到达
  `effect_program.py::decode_settlement_result_payload`。Witness 直接执行真实
  reducer 并经 bridge 复核：已提交重放产生四种 step，不再 dispatch/checkpoint；
  identity/prefix 拒绝、provider 拒绝、terminal 拒绝及 prepared outcome unknown
  产生八种 failure。真实文件读回另证明 `writeback_missing`。
- **外部输入是独立义务。** 实际 `settlement.bind_gate` handler 先执行
  `settlementResultInput`。注入 envelope 覆盖四种 step、十一种 failure 的接纳，
  以及各 step/failure 槽位对 unknown/null/数字的 TS 与 Python 解码拒绝。这是
  输入证据，不是十一种生产证据。`permission_denied` 在
  `task_lease_acquire.ts`、`task_lease_lifecycle.ts` 有生产点，本组尚未执行；
  `cancelled` 仅证明解码接纳，尚未识别生产分支，不将其新归类为 compatibility-only。
  两项生产义务继续保留。
- **Receipt phase（`receipt_bound_monitor_phase`、`receipt_bound_replay_phase`）。**
  `quota/settlement_readback.ts::readQuotaSettlement` 从隔离合成 receipt 文件提取
  事实，调用 `quota/settlement_phase.ts`，Python `read_heartbeat_settlement`
  解码结果。精确 monitor commit、缺失或错误 commit、completion/writeback/spend
  前缀、重复读回及冲突 identity 均经过实际调用点。Monitor 无需 spend 即产生
  `poll_due` 或 `settled`；`settlement_pending` 仅以 Python work-lane 消费者的
  兼容接纳证明保留。旧 monitor effect id 与 terminal-to-replay adapter 继续保留；
  删除需要独立的历史 reader/caller 迁移证据。Replay 三种值均有产生证据；
  autonomous-replan binding 另经 phase bridge 验证，未执行完整 replan receipt 交易。

运行 `uv run --extra test python -m pytest tests/architecture/test_settlement_receipt_source_boundaries.py`：
Python 3.12.3、合格 Node 22.22.3 下 **62 passed**；连同已有 binding witness、
settlement-driver、quota-settlement 测试共 **142 passed**。两组原生 TS
settlement/readback 测试 **58 passed**，无跳过。文档治理、焦点 lint/type 检查和
语义漂移 smoke 通过；后者仍报告 F1/F2 **7/26**、19 项跨运行时未验证。薄脚本
`scripts/settlement_receipt_source_witness.mts` 导入现有 owner，不新增 runtime
规则、registry 元数据或全局词表。

边界：合成读回不证明 durable writer、所有调用点、真实 CLI/backend 资格或 F6
历史兼容。现有 budget 文本分类只被刻画，未被修复。未做生产重构，也未改变
frontend/Lark/CLI；后续由相应 owner 评审证据并补已点名的生产 witness，不自动
宣告来源全部关闭。
