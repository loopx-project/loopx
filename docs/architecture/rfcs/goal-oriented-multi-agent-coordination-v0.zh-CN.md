# RFC：面向目标的多 Agent 自主协同协议（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** Partial（M0 只读同目标 Peer 前沿与协同机会投影 + M1 Turn 信封条件式默认摘要已实现；M2–M3 留待后续切片）
- **作者 / 负责人：** 既有控制面 alignment、collaboration 与 directory owners
- **创建：** 2026-11-27
- **最近规范性修订：** 2026-11-27
- **实现基线：** `50b4cc9de4`
- **相关契约：** [共享 Goal 对齐与受治理 Amendment 协议](shared-goal-alignment-and-governed-amendment-v0.zh-CN.md)、[Peer Agent Directory 与有界观测](../../reference/protocols/peer-agent-directory-and-observation-v0.md)、[有能力的 Agent Manager 与语义工作交接](capable-manager-semantic-handoff-v0.zh-CN.md)、[实时团队工作空间](live-team-workspace-v0.zh-CN.md)
- **语言镜像：** [English](goal-oriented-multi-agent-coordination-v0.md)

## 文档结构与维护约定

第 1–10 节为持久的设计、安全与验收契约；第 11 节为规范性交付计划（起点为基于 `50b4cc9de4` 落地的 M0 切片）；第 12 节记录 M1–M3 的未决决策；附录保留非规范性演进记录与证据索引。本中文版本与[英文版](goal-oriented-multi-agent-coordination-v0.md)互为语义镜像（semantic mirror），二者存在实质差异即为缺陷。

---

## 1. 决策摘要

1. **在不新增数据库或平行 authority 的前提下，让同一 Goal 下的 Peer 前沿结构化可发现。** 对 `shared_goal_alignment_v0` 做向后兼容的增量扩展，新增有界字段 `peer_frontiers` 与 `collaboration_opportunities`，由单一 TypeScript 语义所有者（`loopx/control_plane/goals/peer_coordination.ts`）基于现有 canonical Todo 与任务租约确定性计算。
2. **严格守住“零授权只读投影”（`grants_authority: false`）。** 发现冲突风险、独立验证需求、高优无租约通道或同域协同机会，本身绝不转移 claim、越过租约、修改 Todo 或直接触发外部投递。
3. **从“人肉传话 + 被动推信”演进为“面向目标的自主感知与双向配合”。** 保留现有 `shared_goal_alignment_v0`、`peer_agent_directory_v0` 与 `manager-inbox` 各自的所有权，同时落地有界拉取式发现（M0）与 Turn 信封条件式默认摘要（`peer_coordination_digest`，包含字段键在内的序列化增量严格 `<=300 B`，约 `75` tokens / 不足 200K 上下文窗口的 `0.04%`；非激活或游标未变时省略为 `0 B`），使同 Goal 下异构专长 Agent 无需手动拉取 CLI 或人类中转即可及时感知冲突、独立验收需求或空闲可接续通道。
4. **将已落地的 M0+M1 只读发现与后续 M2–M3 写入/宿主扩展解耦。** M0（`loopx shared-goal-alignment` / `goal-alignment`）与 M1 条件式默认 Turn 信封摘要（`loopx quota should-run` -> `TurnEnvelope`，同时保留面向冻结评测基线的显式 `off` 关闭开关）在普通产品路径上零手动开启成本；带冷却窗的双向握手（M2）与宿主中立的 Antigravity/非 Codex 路由观测配置（M3）仍各自受其退出验收门禁约束。注意无信号时的 `0 B` 仅描述序列化省略增量，不等价于 `<5%` 控制面上下文开销红线的运行时实测认证。
5. **本 RFC 明确不批准**自主多 Agent 自合并 PR、跨所有者自动改派 Todo 或无界膨胀 Turn 上下文信封。

## 2. 问题与动机

当多个分工不同的 Agent 围绕同一个 LoopX Goal 协作时（例如质量/精简重构 Agent 持续产出聚焦 PR，而验证/基准 Agent 负责独立评测、捕捉回归与效率归因），当前仍经常需要人类显式传话才能建立配合：

- 用户不得不手动交代 Agent B：“Agent A 有一批简化 PR，你们协同验证和打磨一下”；
- 同时，各个 Peer 自身也缺乏“拉取式”的结构化视图来回答：谁和我碰到了同一个 `target_key` 或重叠 `required_write_scopes`、谁正在等待 `completion_validation_required` 独立验收、谁的高优通道已失去活跃租约。

根本原因在于五处结构性缺口：

1. **纯标量 Peer 计数黑盒：** 现有 `shared_goal_alignment_v0` 仅向调用方返回 `other_agent_claimed_advancement_count`（一个整数）以及 `peer_claimed_bound_todo_ids`（仅限 `bound_agent === caller`）。其他 Peer 的优先级、`task_domain`、`task_repository`、`target_key`、`required_write_scopes` 与 `completion_validation_required` 完全不可见。
2. **只有推式 `manager-inbox`，缺少拉式协同匹配：** `manager-inbox` 在“发送方已经明确知道找谁、要做什么”时有效，但无法在协同发起前回答“当前 Goal 下哪条 Peer 通道与我的域匹配或急需独立核验”。
3. **写域/目标冲突后置暴露：** 两位 Agent 在相同 `target_key` 或重叠 `required_write_scopes` 上并行工作时，往往要到尝试抢锁或构建 worktree 冲突后才被动发现，缺少无副作用的事前避让提示。
4. **上下文预算风险：** 直接把完整 Peer 工作树塞入每次 Turn 会击穿控制面 `<5%` 上下文预算红线；协同发现必须坚持按需拉取、严格封顶（`MAX_PEER_FRONTIER_ITEMS = 12`、`MAX_COLLABORATION_OPPORTUNITIES = 8`）并靠确定性 `opportunity_id` 去重。
5. **异构宿主路由不对称：** 非 Codex 宿主（如 Antigravity 桌面会话）在未接入观测适配器时，`resolve-peer-route` 返回 `unavailable (host_observer_unavailable)`，发送方只能退化为离线收件箱写入或依赖人类中转。

### 不变量

- **单一状态来源：** 不新增第二套 Todo 表、影子 claim 文件或并行 intent 存储；一切投影严格源于 canonical Todo/lease 快照（`file_v0` / `sqlite_v0`）。
- **单一语义归约器：** 仅由 `peer_coordination.ts` 一个 TypeScript 模块定义资格判定、写域与目标重叠计算、排序优先级、去重与截断上限；Python 与 CLI 只作参数传递与展示适配。
- **确定性与零授权：** 相同输入快照必然输出完全一致的有序列表与 `opportunity_id`，每条结果均显式标注 `grants_authority: false` 与 `read_only: true`。
- **自身失败闭环、同伴脏数据有界降级：** 调用方自身的租约损坏仍严格 fail-closed；Peer 的损坏租约记录安全降级为 `lease_active: null`，避免拖垮调用方的只读对齐视图。
- **公开安全边界：** 不向 `peer_frontiers` 或 `collaboration_opportunities` 暴露本地私有路径、原始聊天记录或凭据。

## 3. 范围与非目标

### 范围内

- 对 `shared_goal_alignment_v0` 做增量 M0 扩展（`peer_frontiers`、`collaboration_opportunities`），由 TypeScript 计算并通过 `loopx shared-goal-alignment` / `goal-alignment` 输出。
- 规范拉取式协同匹配规则、确定性 `opportunity_id` 去重、M1 Turn 信封条件式默认紧凑摘要（`<=300 B`）、M2 冷却窗握手及 M3 宿主中立路由观测契约。

### 非目标

- 替代 `shared_goal_intent_v0`、`goal_amendment_proposal_v0`、`task_lease` 或 `peer_agent_directory_v0`。
- 授予 Agent 跨所有者抢占 Todo、篡改优先级或未经授权自合并 PR 的权限。
- 引入宿主特定聊天轮询或抓取未绑定会话原文。

## 4. 当前系统契约

基于基线 `50b4cc9de4`：

- **Peer 目录（`loopx/control_plane/agents/directory.py` / `agent-directory`）：** 聚合展示已注册 Agent、当前 Todo 概要、陈旧提示与路由候选，不计算成对写域冲突、域协同或验收需求匹配。
- **共享 Goal 对齐（`shared_goal_alignment.ts` / `shared_goal_work.ts` / `shared_goal_work_source.py`）：** 读取单一 `canonical_todo_snapshot` 并产出计数字段、caller 租约冲突与 `peer_claimed_bound_todo_ids`，目前丢弃 Peer 的 `priority`、`task_domain`、`task_repository`、`target_key`、`required_write_scopes`、`completion_validation_required` 与租约活跃度。
- **协调激活投影（`peer_orchestration.ts`）：** 处理具备 `peer_agent_activation` 能力的协调者对 `bound`/`executing` 子通道的激活准入，不解决同级 `peer_v1` 之间的横向自主配合。
- **写域重叠与仓库标识（`task_lease_acquire_decision.ts`、`task_lease_repository.ts`、`work_requirements.ts`）：** TS 侧已有成熟的仓库规范化与写域重叠函数（`evaluateQuotaSchedulingWriteScopesOverlap`、`repositoryScopesMayOverlap`）可直接复用。

## 5. 提案架构

### 所有权与权威边界

`loopx/control_plane/goals/peer_coordination.ts` 是面向目标 Peer 协同匹配投影的唯一语义所有者；`shared_goal_work.ts` 与 `shared_goal_alignment.ts` 仅负责组合调用，不在 Python 侧复制任何重叠或匹配规则。实际写入继续严格由既有 owner（`todo claim`、`task-lease`、`manager-inbox`）校验执行。

### 状态模型与模式

`shared_goal_alignment_v0` 保留全部既有顶层字段不变，增量新增两个只读数组（当旧调用方传入未携带新输入字段的历史请求时安全默认为空数组 `[]`）：

1. **`peer_frontiers`**（按 `P0..P4` 优先级、`agent_id`、`todo_id` 确定性排序，上限 `12` 条）：
   - `todo_id`、`agent_id`、`priority`、`action_kind`、`task_domain`、`task_repository`、`target_key`、`required_write_scopes`、`completion_validation_required`、`lease_active`（`true | false | null`）。
2. **`collaboration_opportunities`**（按严重度等级 -> Peer 优先级 -> `peer_todo_id` -> `caller_todo_id` -> `opportunity_id` 排序，上限 `8` 条）：
   - `opportunity_id`：`"opp_" + sha256(canonical_json([kind, peer_agent_id, peer_todo_id, caller_todo_id ?? "", ...matched_on]))[0..16]`
   - `kind`（四类闭合枚举）：
     - `shared_target_collision_risk`（`advisory_action: "deconflict_before_edit"`）：仓库兼容前提下存在相同非空 `target_key` 或重叠 `required_write_scopes`。
     - `peer_verification_needed`（`advisory_action: "offer_independent_verification"`）：Peer 设置了 `completion_validation_required === true`，且调用方具备验证类 `action_kind`（`verify`/`review`/`benchmark`/`evaluate`/`audit`）、同域/同库上下文，或处于空闲状态（`caller_lanes` 为空）发现待验证需求。
     - `peer_unleased_lane_attention`（`advisory_action: "inspect_peer_status_before_duplicating"`）：高优（`P0`/`P1`）Peer 通道 `lease_active === false` 且与调用方存在同域/同目标交集（或空闲发现）。
     - `shared_domain_synergy`（`advisory_action: "align_with_peer_deliverable"`）：相同非空 `task_domain` 且仓库兼容，但未在同一 `(caller_todo_id, peer_todo_id)` 对上触发碰撞预警。
   - `peer_agent_id`、`peer_todo_id`、`caller_todo_id`（`string | null`）、`matched_on`（`string[]`）、`advisory_action`、`grants_authority: false`。

### 命令与协同生命周期

- **M0（已随本 PR 落地）：** 通过 `loopx shared-goal-alignment --goal-id <g> --agent-id <a>`（及 amendment/plan 快照读取）在 `file_v0`/`sqlite_v0` 上按需计算完整的 `peer_frontiers` 与 `collaboration_opportunities`（含 `shared_domain_synergy`）。
- **M1（已随本 PR 落地 — 条件式默认开启）：** 普通 `loopx quota should-run` Turn 无需手动开启，自动调用 TS `peer_coordination_digest.ts`。仅当同 Goal 注册活跃 Agent `>= 2` 且相对接收者实盘 Turn 游标（`peer-coordination-cursors/<agent_id>.json`）出现新 `opportunity_id` 时，`TurnEnvelope`（`v0`/`v1`）才会嵌入 `peer_coordination_digest`（`schema: "peer_digest_v0"`、`read_only: true`、`grants_authority: false`），含 `,"peer_coordination_digest":` 字段键在内的序列化增量严格 `<=300 B`。繁忙 Turn 仅注入 `shared_target_collision_risk`（`"collision"`）与 `peer_verification_needed`（`"verify"`）；空闲 Turn（`caller_lanes` 为空）注入 `peer_verification_needed`（`"verify"`）与高优 `peer_unleased_lane_attention`（`"unleased"`）。只读 `--dry-run` 预览在有合格信号时可显示该摘要，但绝不创建或推进持久游标；单 Agent、无合格新机会、游标未变、调度器跳过/观察状态或显式配置 `off`（`coordination.peer_coordination_digest: "off"` / `LOOPX_PEER_COORDINATION_DIGEST=off`）均整字段省略（`0 B`）。实盘非预览且未被跳过的 Turn 仅将本次**实际展示**的条目 ID 与仍然活跃的历史已读 ID 写入游标（被 `omitted` 截断的条目保留未读以便后续 Turn 继续透出，已消解条目自动清理）。
- **M2（后续可选）：** 基于 `(opportunity_id, basis_digest)` 通过 `manager-inbox` 完成带冷却窗的 `offer` / `accept` / `decline` 握手，防止对拒绝或暂不可达 Peer 重复打扰。

### 扩展与宿主契约

核心匹配语义与宿主无关；Antigravity 或其他运行时通过可选宿主观测 profile 提供路由可达性，未启用时自动保守保留 `host_observer_unavailable`，但仍可依托持久 `manager-inbox` 与 canonical Todo/lease 完成协同对齐。

## 6. 备选方案与设计取舍

1. **拒绝：独立建立“团队协同看板”表/文件。** 极易与 canonical Todo/lease 发生双写分裂；仅当未来出现跨 Goal 瞬时握手需求无法表达时再重评。
2. **采用：在既有 `shared_goal_alignment_v0` 上增量扩展，并复用 TS 既有写域重叠/仓库规范化组件。** 零重复轮子、对历史调用方完全兼容、天然受既有边界测试保护。
3. **拒绝：任何 Todo 变动即无条件推送全员提示。** 会制造提示词风暴并突破 `<5%` 控制面开销上限；M1 游标去重 + M2 冷却窗从机制上抑制噪音。

## 7. 安全性、隐私与兼容性

- **默认等价性：** 既有 `shared_goal_alignment` 调用方所有旧字段语义不变；未传入 `caller_lanes`/`peer_frontiers` 的历史请求返回 `[]`。
- **权限与写隔离：** 全程 `read_only: true` 且 `grants_authority: false`，任何外部写入或 PR 合并仍遵循原有明确授权门禁。
- **公开安全：** 仅引用规范化公共标识与相对写域路径，不出现私有目录、凭证或聊天原文。
- **版本兼容：** 增量为纯加性字段，旧读者安全忽略，TS 解码器仅在显式提供新字段时严格校验。

## 8. 迁移与回滚

M0 仅扩展只读投影输出，不改动磁盘持久化 schema 或迁移流程，回滚单笔提交即可完全恢复先前输出形态，无需导出迁移。

## 9. 验证与验收

| 声明 | 测试或证据 | 预期结果 | 边界 / 排除项 |
| --- | --- | --- | --- |
| TS 归约器准确产出 4 类协同机会、冲突抑制、空闲发现、写域/仓库判定与上限控制 | `node --experimental-strip-types --test tests/control_plane_ts/shared_goal_alignment.test.ts` | 新增与既有 `28` 项 TS 断言全数通过 | 内存合成载荷，不测磁盘 I/O |
| `file_v0` 与 `sqlite_v0` 真实快照、CLI 与 M1 Turn 信封摘要一致且单 Agent 输出零回归 | `uv run --extra test python -m pytest tests/control_plane/test_shared_goal_alignment.py tests/control_plane/test_shared_goal_alignment_cli.py tests/control_plane/test_peer_coordination_digest.py tests/control_plane/test_cli_output_budget.py` | 全部 File/SQLite 参数化、CLI、TurnEnvelope、游标去重与 `0 B` 基线预算用例通过 | 单机 canonical 快照，不含 M2 握手或 M3 宿主守护进程 |
| 双语 RFC 索引与校验规则通过 | `python3 scripts/generate_rfc_status_index.py --check` 与 `python3 scripts/check_rfc_bilingual.py --base origin/main --head HEAD` | 退出码 `0` | 校验双语结构成对变更，不代替人工译文审查 |

## 10. 运维契约

- **输出有界：** 单次投影 `peer_frontiers <= 12`、`collaboration_opportunities <= 8`，CLI Markdown 按严重度有序渲染。
- **可诊断降级：** 调用方租约报错照常 fail-closed；Peer 损坏租约降级为 `lease_active: null`，防止单个损坏条目阻断全组视角。

## 11. 规范性交付计划

| 里程碑 | 交付行为 | 准入条件 | 退出证据 | 回滚路径 |
| --- | --- | --- | --- | --- |
| **M0** | TS `peer_coordination.ts`、Python `shared_goal_work_source.py` 透传及 `shared_goal_alignment` / CLI 输出 | `origin/main` 基线干净 | TS + File/SQLite pytest + CLI + `canary premerge` 全过 | 回退本 PR |
| **M1** | Turn 信封条件式默认 `peer_coordination_digest`（含键 `<=300 B`，无新信号/单 Agent 省略 `0 B`，繁/闲分层过滤，实盘游标提交与清理，支持显式 `off`） | `file_v0`/`sqlite_v0` canonical 快照 + `TurnEnvelope` v0/v1 集成 | TS `peer_coordination_digest.test.ts` + File/SQLite `test_peer_coordination_digest.py` + `cli-output-budget` 零回归（`canary premerge`） | 配置 `coordination.peer_coordination_digest="off"` 或回退 M1 接线 |
| **M2** | 基于 `(opportunity_id, basis_digest)` 与冷却窗的双向 `manager-inbox` 握手 | M1 + 幂等评审通过 | 双端收件箱握手集成测试通过 | 关闭自动 offer 开关 |
| **M3** | 宿主中立路由观测适配器（覆盖 Antigravity 等非 Codex 宿主）与自主协同指标 | M2 + 宿主适配器开关 | 双 Agent 无人类中转协同实测通过 | 退回 `host_observer_unavailable` |

## 12. 未决决策

1. **D1（已拍板 — 2026-11-27，M1 默认触发路径与信封暴露面）：** 普通产品路径（`loopx quota should-run` -> `TurnEnvelope`）**无需手动开启** M1。当同一 Goal 注册活跃 Agent `>= 2` 且存在相对游标的新增合格 `opportunity_id` 时自动注入 `<=300 B`（含字段键）的 `peer_coordination_digest`：繁忙 Turn 仅纳入 `shared_target_collision_risk` 与 `peer_verification_needed`；空闲 Turn 纳入 `peer_verification_needed` 与高优 `peer_unleased_lane_attention`；只读 `--dry-run` 预览可显示摘要但不写入游标，实盘非预览且非跳过 Turn 仅确认实际展示条目及仍活跃历史条目（使截断省略条目可在后续 Turn 透出并自动清理已消解条目）；单 Agent、无新合格信号、游标未变、跳过/调度观察模式与显式 `off` 关闭配置均整字段省略（`0 B`）；`shared_domain_synergy` 保留通过 `shared-goal-alignment` / `goal-alignment` 按需拉取。控制面上下文开销 `<5%` 仍以端到端实测验收为准（序列化省略 `0 B` 不替代实测认证）。
2. **D2（M2 拒绝冷却窗策略）：** `decline` 后的默认冷却时长/revision 绑定规则应如何设定以避免同质打扰？**负责人：** `manager-inbox` owner。
3. **D3（M3 Antigravity 宿主观测来源）：** 应由签名本地守护进程端点还是应用侧租约文件提供存活观测？**负责人：** desktop/host owners。

---

## 附录 A：执行台账（非规范性）

- M0+M1 已在更新后的 PR 分支一并落地（`peer_coordination.ts` + `peer_coordination_digest.ts` + `TurnEnvelope` v0/v1 接线；后续 M2–M3 检查点可于 `ledger/` 目录独立记录）。

## 附录 B：决策日志

| 日期 | 决策 | 所有者 / 批准 | 备选方案 | 变更的规范章节 |
| --- | --- | --- | --- | --- |
| 2026-11-27 | 采用基于 `shared_goal_alignment_v0` 的 M0 增量投影并复用 TS `evaluateQuotaSchedulingWriteScopesOverlap` | 待维护者 PR 审批 | 独立建表与独立 RPC | 第 1–11 节 |
| 2026-11-27 | 收口 D1 并落地 M1 条件式默认 `peer_coordination_digest`（`<=300 B`、无激活信号省略 `0 B`、繁/闲分层、游标自动清理与显式 `off` 退出通道） | 用户确认方向（“为啥仍需要主动调用……路径能足够自动和智能么”）+ 待维护者 PR 评审 | 维持默认关闭手动开启开关或仅限手动 CLI 拉取 | 第 1、3、5、9、11、12 节 |

## 附录 C：证据登记表

| 证据 ID | 声明 | 基线 / 环境 | 产物或命令 | 结果 | 隐私 / 有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | TS 与 File/SQLite 对齐一致性及 M0 协同机会单测全过 | 清洁 PR 工作树 | `tests/control_plane_ts/shared_goal_alignment.test.ts` 与 `tests/control_plane/test_shared_goal_alignment*.py` | 本地工作树全过 | 临时测试夹具，不含真实私密状态 |
| E2 | M1 Turn 信封自动摘要（`<=300 B`）、游标去重/清理、显式关闭与 `file_v0`/`sqlite_v0` 下 CLI 基线预算零回归 | 清洁 PR 工作树 | `tests/control_plane_ts/peer_coordination_digest.test.ts`、`tests/control_plane/test_peer_coordination_digest.py` 与 `test_cli_output_budget.py` | 本地工作树全过 | 临时测试夹具；多 Agent 实测 `<5%` 上下文税单独统计 |

## 附录 D：被拒绝或替代的方案

- **Python 侧自建匹配启发式逻辑：** 已拒绝。对齐投影与写域/仓库规范化已在 TypeScript 侧拥有权威 owner，在 Python 复制规则必然引发双端语义漂移。

## 附录 E：事故与评审教训

- 实际长期多 Agent 协作卡点往往不在缺乏点对点发信通道，而在于跨 Agent 互相看不见对方的领域/范围/验收需求（整数计数黑盒）与异构宿主缺少轻量路由观测，迫使人类不断充当人工路由器；通过有界只读结构化投影可从根本上消除这类人为中转负担。
