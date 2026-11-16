# C1 来源收敛与历史集成检查点

- **日期：** 2026-10-30（覆盖至 `#8266` 的滚动集成检查点）
- **状态：** 未合并的集成证据；C1 仍处于开放状态。
- **来源：** 尚未合入的 C1 统一分支及其对 `main` 各检查点的集成（另见 [打包 heartbeat 对照](2026-10-28-packaged-heartbeat.zh-CN.md)）。
- **语言镜像：** [English](2026-10-30-c1-source-convergence.md)

## 已合入与未合入的收敛边界

| 边界／Owner | 当前 C1 状态 | 保留兼容性与退出门禁 |
| --- | --- | --- |
| 完整来源 `work_contract`（`compose_turn_decision`、`completePlanningFacts`） | **C1 未合入：** 统一守卫、Host、driver、MCP、Benchmark、Kiro、DSH、SSH 及已绑定 `generic_cli`（`quota_settlement_plan_v2`、沿 `#8691` 共享 `task_context`）。 | 无 Turn 观察（`--scheduler-observation`）、显式 `--turn-envelope` v0 短视图（`EdgeBench` 及显式兼容调用方）与历史 v0 捕获保留显式读取器，直至替代路径与恢复窗口验收完毕。 |
| 文件优先渐进／差分与字段复用（`#7560`、`#7627`、`#7935`、`#8186`） | **已合入 `main` 并由 C1 采用：** 单次调用初始计划、已观测字段复用与摘要绑定文件差分替代已退役的未合入原型 `outcome_context_projection_v0` 强制阅读计划。 | 部分或截断读取永不建立基线；`context_delivery=off` 配 `delta_delivery=auto` 不发布渐进导航；普通 CLI 默认采用仍须 §9 验收。 |
| 数值保真（`#7557`、`#7633`、`#7643`、[数值检查点](2026-10-29-complete-numeric-integrity.zh-CN.md)） | **主干 owner + C1 传输：** 稀疏数值标记（`1.0` 与 `1`、`-0.0`、大整数）与 `hash_codec` v1/v2 经 TypeScript 修改计划保留原 Python JSON 值。 | 持久化 Python JSON／action-hash 原像保持不变以兼容既有 Turn key；精确字节 capture 摘要仍与 JS 归一化哈希分离。 |
| 原生选区与重放检查点（`codex_context_history.py`） | **C1 未合入：** 将 hook 重放（`native_resume`／`selected_resume`）绑定物理 inode／区间摘要与仅追加记录字节覆盖，支持连续 resume 保留已核验选区。 | 缺少输出、Todo／正文变更、非仅追加尾部或压缩强制重读；`cli_version` 仅表征原写入者版本，不证明模型保留。 |

## 当前私有 plan 边界

新完整来源 Turn 写入含单一原始 `work_contract`、来源摘要与 capture 头的
`loopx_turn_plan_v1`；完整 planner 不再生成重复的 v0 executor adapter
（`planning_facts`、Host 输入与 `turnPlanWorkSource` 共用 TypeScript owner，
Python 保留持久化 action-hash 数值编码）。

| 生产链路 | C1 行为与 Owner | 保留兼容性 |
| --- | --- | --- |
| Turn CLI 与启动决策 → plan | 直接传递完整来源；planner 拒绝同时携带完整来源和 v0 adapter 的输入。 | 默认检查保留完整来源、portfolio 与写回且不可执行（`12,388 → 26,524 B` 读回增量）。 |
| Capture → Host / `turn_driver` / MCP / DSH / Ark | `host_dispatch_context` 与 `driver_lineage` 在派发、session 谱系或完成意图前一次校验完整来源与 `selectedWorkTodo`。 | 省略合同保留历史 v0 解码；显式 `null`、非对象、畸形或与来源不一致的 `work_contract` 在 Host／写回／扣额前拒绝。 |
| Journal 写入、检查与恢复 | 通过共享 TypeScript reader 校验 v1 与过渡期 `v0+work_contract`；在 `turn/start` 前施加 hard-lease 执行键与锁共享。 | 缺来源的 v0 记录在支持恢复窗口内保留原身份校验与原字节（`14` 组 File/SQLite plan JSON `-29.33%`，journal 字节 `-28.32%`，最终 Host 字节相等）。 |

## 当前 quota 与 managed-controller 切换

C1 删除普通 quota 展示的重复 v0 builder 分支，并使 `turn managed-step` 新鲜
预览及 `refresh-state --settle-turn` 统一走同一 TypeScript 决策、路由与
`quota_settlement_plan_v2` owner。

| 被替代调用方／界面 | 共享 Owner 与删除项 | 保留边界与义务 |
| --- | --- | --- |
| Quota `_project_quota_cli_payload` 与 `should-run` | 删除 v0 builder 回退分支；`should-run` 仅输出已组合的 `loopx_turn_envelope_v1` 或原始前置诊断。 | 组合拒绝（`quota_decision_transport_failed`）保存 `source_diagnostic`、`composition_error` 与原 `heartbeat_receipt`，保持 `ok=false`/`should_run=false`、零 handoff 与零扣额。 |
| Managed 新鲜预览与 `managed-step` | `compose_turn_decision`、`completePlanningFacts` 与合并调用移除 `TurnControllerInput`、`build_turn_controller_input` 与 `build_controller_input`。 | 失败 Turn journal 保留原 v0 解码；现场边界注入签名 v0 预览在 Host／journal／扣额前拒绝。 |
| 绑定 CLI、Kiro、DSH、SSH、EdgeBench 与 Windows 有界恢复 | 显式 `--complete-turn`／绑定 Agent+Turn `should-run`、onboarding 启动器与 Windows/PowerShell 私有目录预留（`1,700 B` 上限通过；`96 B` 准入前拒绝）。 | `--turn-envelope` 配 `--context-delivery off` 仍输出链接不可变 v1 捕获的有界 v0；普通未绑定／无 Turn 默认采用仍待 §9 审批。 |

### 可执行必读义务与原生 runner 的体积归因

`assertInteractionRequiredReads`（由完整来源校验器在 quota、预览、规划与
Host 边界统一调用）要求声明命令或顺序的可执行必读项保持非空、无 NUL 且
`ordering == "before_work"`，同时完整保留纯文本义务、带引号 Unicode 命令与未读
挂起状态，不新增泛化材料读取门禁。完整来源校验还在自动启动间隔预留前执行 Goal
正文就绪拒绝（拒绝场景 RPC 由 `5→2` / `6→3`，避免误伤后续合法 Turn），并在
结构化与 Codex 提示中统一核验已声明的 Vision 审计触发条件。

在冻结的 premerge 预算反例中，直接输出为 `20,333` 字符而原生 runner 为 `20,501`
字符（premerge 布局为 `20,533`，超过未改动的 `20,500` 上限），唯一原因是在
`bd79adc64968` 主干和 C1 `f1614358ac13` 上隔离组件均会向 8 处夹具根路径各追加
`21` 字符（共 `168` 字符）。该预算问题归属 `#8069`，未提高任何阈值。

### 保留的消费者与恢复义务

- **首读 Goal 修订与内联诊断：** 已绑定首读先交付权威存储的 Goal 目标、修订号与摘要，
  再交付原 Goal 正文（首读 `+552 B`，读完相同事实少 `1` 次调用与 `629 B`）；已绑定
  内联 `generic_cli` JSON 复用紧凑投影行与 `<lane>_ref`（显式 Off `55.7–55.8 KB →
  54.3–54.4 KB`，初次 replan `47.4 → 45.5 KB`），篡改紧凑捕获即使重算摘要也拒绝。
- **有界 Windows / PowerShell 与 Benchmark 恢复：** Windows 有界重启与带上限 JSON
  守卫（`1,700 B` 通过；`96 B` 在捕获或回执前拒绝）共用私有目录分配器，并接受无需
  POSIX 可执行位的显式 PowerShell 启动脚本（已在 macOS PowerShell 7 验证；原生
  Windows 宿主 DACL 与 Python 执行仍待验证）。非原生 Benchmark 预检提示保留保存动作
  及 replan／写回必读（读全文 `47.4–47.5 KB` 对按字段选题 `18.8 KB`），同时保留
  EdgeBench 显式 `--turn-envelope` 兼容开关与外部 worker 零扣额取消读回。
- **搬迁检查点扣额读回：** `a1f34097805a` 在移走来源 runs 目录后仍按原索引行、已提交
  回执与工件名校验本地扣额副本，拒绝符号链接或缺失／篡改的工件及回执。

## 匹配工程 Cohort 与负向／混合成本证据

下列量化观测均来自隔离合成 File/SQLite 任务，**不测量**真实模型 token、缓存、
账单、人工注意力、受控延迟或 RFC §9 单会话成果与完整成本收益。

| Cohort（对应版本） | 正向／收敛观测 | 负向、混合与失败观测（均未改判） |
| --- | --- | --- |
| 原 quota 对照 C1（`5f1a9d78ba` / `b8c2bf1dc`；`f04ec308f6` / `4ef428767c`；`6a92640346`；`0423908e9c`） | 温态 RPC `-12.65%`–`-12.88%`（`332→290` / `326→284`）；消除重复捕获投影（`30/30` 次，Python 温态 IO `-9.5%`）；`refresh-state` 结算／重放全部 3 份回执。 | 温态常规步 stdout **增长 +6.66% 至 +8.73%**（不含准备约 `+7 KB`）；冷态／恢复 BODY 降幅 `0%`；早期队列温态线字节 `+1.17%`（清除冗余结果体后才降 `-0.93%`）；去重前观测 Python IO `+3.2%–4%`。 |
| 主干字段复用后三步任务（`d602248c53` / `b90e711cdb`；`918844a017` / `6f00db17c6`；`39770a9604` / `339f01e087`） | 三步总 stdout `-18.39%`（SQLite `225,841→184,316 B`），修正任务 BODY `-28.50%`（`149,223→106,688 B`），回环 Codex 大字段温态 BODY `14,386→2,583 B`。 | 温态三步 BODY 仅降 `-2.09%`（`102,836→100,691 B`）；`39770a9604` 后帧字节反增 **+0.83%** / **+2.14%–2.93%**；File 耗时 `+7.5%`；小规模 2 成果夹具每组多 `1,655 B`。 |
| Hard-lease 三步（`92294e7245cb` / `b3b3b232724d`）与直接绑定 CLI / Kiro / DSH（`f3bdad2387` / `0057a1dbd1`） | Hard-lease 温态全 stdout `-19.5%`/`-19.9%`、RPC `-31.44%`（`843→578`）、帧字节 `-34.1%`；首读 Goal 修订省 1 次调用/`629 B`；搬迁扣额读回通过。 | 直接绑定 CLI stdout 由 `51.7–52.0 KB` 增至 `64.0–64.3 KB`（显式 Off `44.0 → 54.3–55.8 KB`，约 `10.6 KB` 来自结算计划）；Kiro 纯内联 Markdown `2.4 → 48.2 KB` / JSON `23.1 → 48.5 KB`。 |
| 边界组件开销与展示预算 | Codex prompt 合并调用（中位 `24.2→14.8 ms`，`-39%`）；Vision 重试 `6→2` 次 CLI（`11,473→9,269 B`）。 | 边界校验增加局部耗时：Host 准备（`25.7→31.9 ms`）、schema／材料（`0.013→19.6 ms`）、结算恢复（`0.016→12.4 ms`）、独立路由（`0.005→13.3 ms`）、完成意图（`0.002→19.8–20.8 ms`）。预算保持失败：小 Markdown `24,531 > 10,000`、拥挤计划 `24,044`/`27,508 > 22,500`、完整别名 `24,252 > 13,000`、多 Agent `25,933 > 13,000`；薄提示 `5,519 > 5,400` 修复后为 `5,379`（主干 `5,363`）。 |

## 验证状态与未关闭门禁

- **通过（已记录检查点队列）：** 各检查点通过 `196`–`865` 项受影响 Python 用例与
  `45`–`6,161` 项 TypeScript 用例；`1,815`–`1,914` 个匹配文件隔离 wheel 无源码仓
  污染；默认 17 根严格 mypy、变更路径 Ruff 与 control-plane typecheck 通过。
- **保留的历史失败：** 历史宽范围 485 文件 mypy（`4,809`–`4,831` 条错误）、`9baa678db`
  六模块中断运行（`37` 次启动失败）、长 namespace `2,015` 字符 hook 失败及上述五项未
  放宽预算失败均保留原记录。
- **未验证／开放门禁：** MemT 端到端完整输入阻塞于缺失 `goal_ref` 生命周期（不得用
  `source_session_v1` 作旁路）；Windows 宿主原生 DACL 与 PowerShell 执行；正式 App／
  DSH／Kiro／MCP／Ark／PostgreSQL 实装验收；真实模型 token、账单及 §9 匹配单会话
  成果／完整成本比较；普通 CLI 默认采用审批；精确提交审查、维护性棘轮与合入许可。
