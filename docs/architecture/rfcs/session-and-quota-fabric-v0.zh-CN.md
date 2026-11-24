# RFC：面向单一工作台收敛的 L0 会话与额度底座（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** Partial（M0 L0 契约增量）。本 RFC 改动前基线（`45c015ffd9`）已包含 `ssh_host_catalog_v0`、`CHAT_SESSION_MODES`（`managed_runtime`、`attached_host`）与 `GoalAgentWorkPage`；本次 M0 增量引入 `session_quota_fabric_contract.ts`、`session_quota_fabric.ts`、`effect_runtime_handlers.ts` 处理器注册、可选 `ssh_host_catalog_v0` 观测透传及 `vocabulary_v0.json` 词表登记。宿主运行时适配器（M1）与主工作台 UI 嵌入（M2）保留为后续分阶段切片。
- **作者 / 负责人：** 架构收敛工作组；控制面归属、包边界及合并授权归属 LoopX 维护者
- **创建日期：** 2026-11-17
- **最近规范性修订：** 2026-11-24
- **实现基线：** `45c015ffd9`（引入本 M0 L0 契约增量前已审计的基线提交）
- **相关契约：**
  [Agent 会话执行模式](agent-session-execution-modes-v0.zh-CN.md)、
  [桌面执行前端](desktop-execution-frontends-v0.zh-CN.md)、
  [单属主本地守护进程](single-owner-local-daemon-v0.md)、
  [Monorepo 分发拆分](monorepo-distribution-split-v0.zh-CN.md)、
  [整体路线图](loopx-overall-roadmap-v0.zh-CN.md)、
  [状态数据契约](../../status-data-contract.md)、
  [计算额度分配](../../quota-allocation.md)、
  [设计系统](../../development/design.md)
- **语言镜像：** [English](session-and-quota-fabric-v0.md)

## 文档地图与维护契约

第 1–10 节定义稳定的设计与验收契约，第 11 节定义规范性交付计划，第 12 节记录待决问题（推荐方案在获维护者批准前不具备约束力）。附录之前不允许出现带日期的交付标题；历史检查点统一记入 [`ledger/`](ledger/README.zh-CN.md)。本中文版与 [英文原版](./session-and-quota-fabric-v0.md) 针对全部规范性条款保持严格语义镜像。

---

## 1. 决策摘要

1. **新增或收敛的权威边界。** 将多主机 SSH 就绪观测（`session_fabric_ssh_observation_v0`）、不透明 profile 额度窗口观测（`session_quota_window_observation_v0`）及围栏约束的交接评估（`governed_session_handover_proposal_v0`）抽离为独立的 L0 控制面契约（实现位于 [`session_quota_fabric.ts`](../../../loopx/control_plane/collaboration/session_quota_fabric.ts)），无需等待新建整套前端即可独立交付。
2. **保持不变的边界。** `loopx-core` 继续唯一拥有 Goal、Todo、claim、lease、quota、Turn 与 Gate 状态写入权；`ssh_host_catalog_v0` 继续是权威 SSH 主机别名目录；`apps/presentation/dashboard` 中的单一 Goal 工作台继续是官方前端入口。
3. **默认与开启边界。** 未启用 L0 观测时保持逐字节对等：`ssh_host_catalog_v0` 仍仅输出 `{ alias }`；额度状态为 `unknown`、或未通过 Turn/租约围栏及共享 Gate 确认时绝不自动切换执行者。
4. **核心安全与前端收敛约束。** 阻止平行引入 4 万–19.2 万行规模的第二套前端应用（`apps/presentation/calli-workbench/`、`apps/workbench/`）及平行守护进程任务表；所有会话、机器、额度与分诊能力统一嵌入既有单一 Goal 工作台视图。
5. **本 RFC 暂未批准的事项。** 不批准自动迁移凭据、隐式切换 `attached_host` <-> `managed_runtime` 模式、独立 Node Todo 守护进程表或未知额度状态触发扣额/切换。

## 2. 问题与动机

Calli/LooChat 系列 PR（#8778、#8782–#8784、#8843）反映出统一接入本地与 SSH 远端多 CLI 会话及额度感知切换的真实需求。但通过独立 4 万行乃至 19.2 万行（#8843 共 881 个文件）的新前端与独立 Node 守护进程（`apps/presentation/calli-workbench/`、`apps/workbench/`）推进，会直接造成产品与状态面分裂：

- 用户被割裂在官方 Goal 工作台（`apps/presentation/dashboard`）与第二套互不连通的壳层界面之间，代理、CSS 变量与路由各自维护。
- 主干既有的 `configured_ssh_host_aliases`（`ssh_host_catalog_v0`）、`/api/chat/codex-attached` 与 `GoalAgentWorkPage` 被重复造轮子，而非增量演进。
- 守护进程自带的任务表、定时调度器与局部撤销逻辑会与 Core 的单写者租约及 Turn 围栏产生双权威竞态。

将 L0 主机/会话/额度底座与上层前端解耦，使 LoopX 可以先落地可独立测试的控制面契约，再将最小交互面板直接嵌入现有单一 Goal 工作台。

### 不变量

- **I1 单一主机与任务权威。** Goal/Todo/租约/Gate 状态仅由 `loopx-core` 写入；SSH 主机身份仅归 `ssh_host_catalog_v0` 所有。针对未在 `ssh_host_catalog_v0` 注册别名的 L0 观测一律 fail-closed 拒绝。
- **I2 不透明额度隐私边界。** 额度窗口观测只承载不透明 `profile_id`，严禁包含邮箱、密钥、Bearer Token 或本机私人路径。
- **I3 共享 Gate 单一确认。** 当 `ask_before_switch=true` 时，交接请求只产出共享 Inbox/Gate 投影上的 `user_gate_required`，绝不另建弹窗权威。
- **I4 跨越交接的 Turn 与租约围栏。** 交接不得隐式改变 `attached_host` / `managed_runtime` 执行模式；必须同时满足 `current_turn_fenced=true` 与 `previous_lease_released=true` 才允许新 profile 申请租约。
- **I5 关闭态严格对等。** 无 L0 观测时 `ssh_host_catalog_v0` 输出与现有基线完全一致；`unknown` 额度窗口始终输出 `no_handover_needed`。

## 3. 范围与非目标

### 范围内

- 在 `loopx/control_plane/collaboration/session_quota_fabric.ts` 落地零 I/O 的 TypeScript L0 类型化编解码、投影与交接评估契约。
- 确立复用 `apps/presentation/dashboard` 单一 Goal 工作台、拒绝平行第二前端的架构收敛路线。

### 非目标

- 不引入独立的 `apps/workbench/`、`apps/presentation/calli-workbench/` 目录、Node SQLite 守护进程或第二套前端构建链。
- 本 RFC 切片暂不直接修改 `loopx/presentation/codex_attached_api.py` 或在包内孵化常驻 OpenSSH 子进程监督器。

## 4. 现行系统契约（基线 `45c015ffd9`）与 M0 增量

- **改动前基线（`45c015ffd9`）：**
  - [`ssh_host_catalog.py`](../../../loopx/control_plane/status/ssh_host_catalog.py) 在 `/ssh-hosts` 返回 `{ ok: true, schema_version: "ssh_host_catalog_v0", hosts: [{ alias }] }`。
  - [`ssh-host-catalog.ts`](../../../apps/presentation/dashboard/src/data/ssh-host-catalog.ts) 解析 `ssh_host_catalog_v0` 安全别名（`^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$`），并调用 `/api/ssh-source/ensure`、`/api/ssh-source/goal-lifecycle`。
  - [`codex_attached_api.py`](../../../loopx/presentation/codex_attached_api.py) 在 `bound_conversations()` 过滤 `host_surface == "codex-app"`；[`attached-agent-conversations.tsx`](../../../apps/presentation/dashboard/src/features/personal-workspace/attached-agent-conversations.tsx) 与 [`goal-agent-work-page.tsx`](../../../apps/presentation/dashboard/src/features/personal-workspace/goal-agent-work-page.tsx) 在同一 Goal 视图下承接挂接会话与托管会话。
  - [`chat_store.py`](../../../loopx/chat_store.py) 拥有权威的 `managed_runtime` 与 `attached_host` 会话执行模式。
- **M0 L0 契约增量（由本 RFC 切片新增引入）：**
  - 新增 [`session_quota_fabric_contract.ts`](../../../loopx/control_plane/collaboration/session_quota_fabric_contract.ts) 与 [`session_quota_fabric.ts`](../../../loopx/control_plane/collaboration/session_quota_fabric.ts)，提供严格解码、增量目录合并与交接守卫判断，注册至 `effect_runtime_handlers.ts`（`collaboration.session_fabric.project_ssh_host_catalog`、`collaboration.session_fabric.evaluate_governed_session_handover`）并纳入 [`vocabulary_v0.json`](../../../loopx/semantics/vocabulary_v0.json)（复用 `loopx/chat_store.py` 拥有的权威 `CHAT_SESSION_MODES`）。
  - 扩展 [`ssh_host_catalog.py`](../../../loopx/control_plane/status/ssh_host_catalog.py)（`project_ssh_host_catalog` / 可选 `observations`）与 [`ssh-host-catalog.ts`](../../../apps/presentation/dashboard/src/data/ssh-host-catalog.ts)（`isSafeHostAlias`、`decodeSshHostObservationWith`），在存在观测时保留经校验的 `observation`，在默认无观测传入时保持 `/ssh-hosts` 输出与合法别名接受范围与基线 `45c015ffd9` 逐字节一致。

## 5. 目标架构

### 所有权与权威分工

| 层级 | 唯一负责人 | 契约 / 接入点 |
| --- | --- | --- |
| L0 SSH 与额度观测 | 实现 `session_quota_fabric.ts` 的可选宿主 Provider | 产出 `session_fabric_ssh_observation_v0` 与 `session_quota_window_observation_v0` |
| L1 任务、额度与交接围栏 | `loopx-core`（`control_plane/collaboration/`、`quota/`） | 通过 `evaluateGovernedSessionHandover` 校验并结合租约/Turn 围栏执行 |
| L1/L2 挂接接口泛化 | [`codex_attached_api.py`](../../../loopx/presentation/codex_attached_api.py) 后续切片 | 按已注册 `host_surface` 能力泛化，替代硬编码 `"codex-app"` |
| L3 单一 Goal 工作台 | `apps/presentation/dashboard` | 在既有 Goal 视图中嵌入机器切换、会话时间线、额度指示与 Gate 分诊抽屉 |

### 状态模型与 Schema

1. `session_fabric_ssh_observation_v0`：校验 `alias`、`connection_state`（`connected | degraded | disconnected | unknown`）、`failure_code`（严格限定 Calli 已验证的 7 个失败码 `no_ssh | host_key | auth | unreachable | ssh_failed | daemon_missing | forward_denied`，仅在 `degraded | disconnected` 时必填，其余状态必须为 `null`）、`checked_at` 与 `tunnel_latency_ms`。`projectSshHostCatalogPayload` 严格沿 `isSafeHostAlias`（`SAFE_HOST_ALIAS`）校验权威 `configured_aliases` 目录条目，仅向已知别名增量附加经校验的 `observation`，无观测条目保持纯 `{ alias }`。
2. `session_quota_window_observation_v0`：校验不透明 `profile_id`（拒绝 `@`、`/`、私人路径及边界锚定凭据前缀 `sk-...`、`ghp_...`、`github_pat_...`、`Bearer ...`）、`availability`（`available | near_limit | exhausted | unknown`）、`used_ratio`（`unknown` 时强制为 `null`，其余必须在 `[0, 1]`）、`resets_at` 与 `eligible_models`，并仅对 `near_limit` 与 `exhausted` 置 `handover_eligible=true`。
3. `governed_session_handover_proposal_v0`：禁止跨执行模式切换（`from_session_mode !== to_session_mode`）与同 profile 切换；当 `!observation.handover_eligible`（`unknown` 或 `available`）或 `near_limit` 低于 `threshold_ratio` 时返回 `no_handover_needed`，`exhausted` 无论 `used_ratio` 均进入交接门控；开启询问时返回 `user_gate_required`，围栏或租约释放未完成时返回 `await_turn_fence`，仅当全部前置成立时返回 `ready_for_lease_reacquisition`。

## 6. 备选方案与架构取舍

直接回应 #8778、#8782–#8784 与 #8843 收敛评审中的路线抉择：

| 方案 | 结论 | 架构权衡 |
| --- | --- | --- |
| A. 仅在既有视图零散打补丁 | 拒绝 | SSH 错误码与额度阈值逻辑散落在各钩子中，缺乏统一契约 |
| B. **先交付独立 L0 会话与额度底座 + 嵌入既有单一 Goal 工作台** | **选定** | 不等待前端大改即可先行交付可测核心契约；避免第二套前端与守护进程裂变，单次 PR 可控在数百行内 |
| C. 平行引入 4 万–19.2 万行新前端与守护进程（`calli-workbench` / `apps/workbench`） | 拒绝 | 造成导航、样式、代理边界与状态归属分裂；重复既有 `ssh-host-catalog.ts` 与 `GoalAgentWorkPage` 且评审面失控 |

## 7. 安全性、隐私与兼容性

- **关闭态对等：** 无 L0 观测传入时，`ssh_host_catalog_v0` 输出与 `ssh_host_catalog.py` 完全一致。
- **凭据与路径 fail-closed：** `session_quota_fabric_contract.ts` / `session_quota_fabric.ts` 按既有 `isSafeHostAlias`（`SAFE_HOST_ALIAS`）边界保留权威 `ssh_host_catalog_v0` 目录条目，并对新增观测、额度与交接字段应用 `assertPublicSafeFabricText`——在解码边界拒绝私人路径（`/Users/`、`/home/`、`/private/`、`~/`、`.ssh/`）及标识符边界锚定的凭据前缀（`sk-proj-...`、`sk-ant-...`、`sk_live_...`、`ghp_...`、`github_pat_...`、`Bearer ...`），同时正常放行 `desk-box`、`task-worker`、`disk-01`、`kiosk-2`、`lab.sk-integration`、`sk-integration` 等合法 OpenSSH 别名与标识。
- **未知与可用额度保护：** `unknown` 与 `available` 窗口强制 `handover_eligible=false` 与 `no_handover_needed`，而 `exhausted` 即使 `used_ratio < threshold_ratio` 仍保持交接资格。

## 8. 迁移与回滚

既有 `apps/presentation/dashboard` 流程零破坏。每个 L0 切片（`session_quota_fabric.ts`、可选后端路由、可选工作台抽屉）均属纯增量改动，无持久化迁移依赖，直接 `git revert` 即可完整回滚。

## 9. 验证与验收

| 声明 | 验证命令 / 证据 | 验收标准 | 适用边界与未覆盖项 |
| --- | --- | --- | --- |
| 双语与 RFC 索引一致性 | `python3 scripts/check_rfc_bilingual.py` 与 `python3 scripts/generate_rfc_status_index.py --check` | 退出码 `0` | 不代表运行时逻辑 |
| L0 TypeScript 类型与摘要边界 | `npm run typecheck:control-plane` 与 `node --test tests/control_plane_ts/content_digest_single_owner.test.ts` | 退出码 `0` 且全通过 | 静态检查范围 |
| L0 编解码、关闭态对等、围栏与隐私反例 | `node --test tests/control_plane_ts/session_quota_fabric.test.ts` | 全部用例通过 | 单元测试范围；真实 SSH 进程监督归 M1/M2 |
| 语义词表无漂移与公开边界扫描 | `python3 examples/semantic-vocabulary-drift-smoke.py` 与 `loopx check` | 返回 `ok` | 不替代下游真实后端联调 |

## 10. 运维契约

SSH 或额度状态未知时绝不提升权限或触发后台切换；所有状态通过 `ssh_host_catalog_v0` 与 Goal 作用域投影只读暴露，非法输入稳定抛出类型化错误。

## 11. 规范性交付计划

| 里程碑 | 交付内容 | 进入门禁 | 退出证据 | 回滚方式 |
| --- | --- | --- | --- | --- |
| **M0 收敛 RFC + L0 契约底座** | 双语 RFC（`session-and-quota-fabric-v0`）+ `session_quota_fabric_contract.ts` / `session_quota_fabric.ts` + `effect_runtime_handlers.ts` 接入 + `ssh_host_catalog.py` / `ssh-host-catalog.ts` 透传 + `vocabulary_v0.json` 词表登记 | 已接受 M0 RFC + 改动前基线 `45c015ffd9` | `session_quota_fabric.test.ts`、`status-source-catalog-smoke.ts`、`test_status_server_ssh_hosts.py`、`docs-governance-smoke.py`、`test_semantic_vocabulary_drift.py` | 回滚 RFC、登记与 L0 处理器 |
| **M1 挂接 API 泛化与主机 Provider** | 将 `codex_attached_api.py` `host_surface` 路由能力化并可选接入 `ssh_host_catalog_v0` 探活 | M0 合入 | 关闭态对等测试 + 7 种 SSH 失败码夹具测试 | 关闭 Provider 开关 |
| **M2 单一工作台 UI 嵌入与交接联调** | 在 `apps/presentation/dashboard` `GoalAgentWorkPage` 嵌入主机状态、额度徽标与 Gate 交接入口 | M1 合入 | Playwright 桌面/窄屏冒烟、视觉截图及 Turn 围栏交接集成测试 | 回滚 UI 面板切片 |

## 12. 待决问题

| 编号 | 问题 | 负责人 | 推荐方案 | 依赖里程碑 |
| --- | --- | --- | --- | --- |
| D1 | `codex_attached_api.py` 对非 Codex `host_surface` 列表的泛化方式 | 维护者 | 从 Goal 已注册 `agent_capabilities` 的宿主面集合动态解析 | M1 |
| D2 | 额度交接 `ask_before_switch` 的默认策略 | 维护者 | 默认 `true`（走共享 Gate `user_gate_required`），经用户显式授权才开启围栏自动切换 | M2 |
