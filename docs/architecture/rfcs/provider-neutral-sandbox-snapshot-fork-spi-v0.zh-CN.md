# RFC：面向模型与沙箱双中立的快照与分叉 SPI（v0）

- **RFC status:** Accepted (合并后生效；本变更仍为提案)
- **替代 / 关闭：** 无
- **Delivery maturity:** Proposal；含 M0 契约一致性测试切片，尚无微虚拟机 provider 集成或实测收益声明。
- **Authors / owners:** LoopX 控制面、探索与 benchmark 维护者
- **Created:** 2026-11-22
- **Last normative revision:** 2026-11-22
- **Implementation baseline:** `dc79482c88dd75ea59785d165fd3050d9d5508c3`
- **Language mirror:** [English](provider-neutral-sandbox-snapshot-fork-spi-v0.md)
- **Related contracts:** [研究状态分叉](hierarchical-agent-stride-control-v0.zh-CN.md#77-研究状态分支与匹配接续)、[预算内研究规划](../../research/budgeted-research-planning.zh-CN.md)、[基准漏斗验证与测量模型](long-horizon-harness-benchmark-research-program-v0.zh-CN.md#55-四层漏斗验证协议)、[LayerFS 探索评测](layerfs-engineering-exploration-evaluation-v0.zh-CN.md)、[小内核分发拆分](monorepo-distribution-split-v0.zh-CN.md)、[完整状态恢复](complete-state-recovery-v0.zh-CN.md)、[扩展放置规则](../../reference/extensions.md#placement-decision-for-agents)。

## 文档地图与维护契约

第 1–3 节与第 5–11 节定义拟议的双中立沙箱快照与分叉 SPI 契约及权威边界；
第 4 节记录基于 `dc79482c88dd75ea59785d165fd3050d9d5508c3` 及已合入主干的
研究/评测权威契约（`#8461`、`#9209`、`#9211`；早先未合入提案 `#8516` 已拆解由
上述主干契约承接）的盘点事实；第 12 节列出待定决策。中文文件是英文文件的语义镜像，
规范性章节必须保持同步。后续带日期的执行记录统一放入
[RFC ledger](ledger/README.zh-CN.md)，不得在附录 A 之前添加日期标题。
合入本 RFC 表示接受设计基础并交付确定性契约测试，不构成默认开启、正式支持某
provider 或 benchmark 提分声明。

---

## 1. 决策摘要

1. 定义一个**可选、默认关闭、由扩展承载**的 4 原语沙箱快照与分叉 SPI
   （`pause`、`resume`、`snapshot`、`fork` 及显式 `destroy`），将 E2B 兼容微虚拟机、
   Firecracker/Cloud Hypervisor、LayerFS CoW、容器与 Git worktree 后端接入 LoopX
   现有的 [Explore 回放](../../../loopx/capabilities/explore/replay_runtime.py) 与
   [反事实后缀](../../../loopx/capabilities/explore/counterfactual_runtime.py) 契约。
2. **控制面权威外置：** Goal、Todo、认领租约、预算额度、结算回执与宿主游标严格
   保存在沙箱外部；沙箱快照或分叉绝不复制执行租约或结算权限。
3. **保持最小内核无虚拟化依赖：** `loopx/` 核心包不引入任何 hypervisor SDK、特定
   守护进程或投机性运行时枚举；具体 provider 以扩展形态存在（`capability_id: none`，
   `origin: extension`）。
4. **严格区分 1:1 挂起恢复与 1:N 反事实分叉：** `pause`/`resume` 在单一 `sandbox_id`
   上保留挂起与恢复；`snapshot`/`fork` 生成不可变检查点与彼此隔离的
   `branch_isolation_id` 子分支，用于同状态反事实对照（$\tau_B(s; a, a_0, \pi)$）。
5. **不越界宣称线上效果：** 本 RFC 仅附带 [`test_sandbox_snapshot_fork_spi_conformance.py`](../../../tests/test_sandbox_snapshot_fork_spi_conformance.py)
   中的 M0 确定性契约测试；真实 Linux/E2B 集成与实测评估留待 `M1`–`M3`。

---

## 2. 问题、动机与不变量

长程反事实接续与多分支工程探索需要从同一五元组 Checkpoint
$s = (c_{\text{host}}, \sigma_{\text{loopx}}, w_{\text{ws}}, \phi_{\text{env}}, b_{\text{eval}})$
出发，在相同预算下比较 treatment 与 control 后缀的终局收益。现有纯目录拷贝路径
存在四类结构性缺口：

1. **符号链接拒绝与容量/时间上限：** [`checkpoint_artifacts.ts`](../../../loopx/control_plane/runtime/checkpoint_artifacts.ts)
   出于一致性安全拒绝 symlink 并设定了 `MAX_LOCAL_SNAPSHOT_BYTES`（64 MiB）本地快照上限，
   而 [`checkpoint.py`](../../../benchmark/runtime/checkpoint.py) 设定了 30s/90s
   （`CHECKPOINT_CAPTURE_SECONDS = 90`）捕获窗口；现代 `.pnpm`、`.venv`、Cargo
   构建缓存高度依赖符号链接与大体积产物树。
2. **进程与内存现场丢失：** 目录拷贝会丢弃常驻 LSP、构建守护进程、本地数据库、
   打开的文件描述符与 GPU/内存现场，使本可精确分叉的试验退化为冷启动重建。
3. **跨分支污染与租约脑裂风险：** 若将 `.loopx/` 控制面库、`claim_operation_id`
   或宿主机环境凭据一并打入 VM 快照，分叉出的多个子分支会并发写入同一外部状态或
   重复消耗原 Turn 权威。
4. **Provider 绑定风险：** 若将 E2B 专有接口或 Firecracker 通信直接写死在
   `loopx/` 核心包，将违背 LoopX 模型与沙箱双中立的[最小内核分发契约](monorepo-distribution-split-v0.zh-CN.md)。

### 不变量

- **I1（精确捕获前的静止屏障 Quiescence Barrier）：** 凡宣称 `process_tree_quiesced`
  或 `microvm_memory_exact` 的 `snapshot`，必须在已结算或显式 `paused` 的静止屏障下
  刷盘捕获；对仍在写入的运行态内存直接做精确快照必须 fail-closed 拒绝。
- **I2（控制面权威外置）：** SQLite 状态库、注册表、写租约与结算回执严禁作为
  guest 状态被快照复制。`snapshot`/`fork` 必须擦除继承的租约证明与凭据；每个子分支
  获得独立的 `branch_id` / `branch_isolation_id`，并经由 `RiskAwareReplayChildAdapter`
  接受意图与结果风控审查。
- **I3（1:1 挂起与 1:N 非干扰分叉）：** `pause` -> `resume` 严格保留同一 `sandbox_id`
  与存活进程表，且禁止对已运行实例重复 `resume`。`snapshot` -> `fork` 对同一不可变
  快照扇出多个子分支；分支 A 的内存、套接字、tmpfs 或 symlink 修改不得污染分支 B 或
  母快照。
- **I4（保真度诚实映射到 `ReplayFidelity`）：** Provider 声明其达到的
  `SandboxFidelityTier` 并单调映射到 `ReplayFidelity`。当请求 `EXACT` 恢复而仅存在
  低保真快照时，评估结论必须降级为 `OBSERVED_ONLY`，禁止伪造 `PROMOTED`。
- **I5（出网隔离与凭据擦除）：** 子分支运行在显式声明的网络出站与凭据信封内；封存前
  必须擦除宿主环境秘密，跨分支共享外部副作用需被 `assess_replay_child_intent` 拦截。
- **I6（最小内核零膨胀与纯运行时句柄）：** 底层 `sandbox_id`、快照 URI 与套接字仅
  存于运行态 `AdapterStateLease.binding_key` / `ReplayChildLease.child_key`，绝不进入
  Trace/Todo/Explore 公开载荷，也不为未接入调用方的设计提案提前向核心写入枚举。

---

## 3. 范围与非目标

### 范围内

- 盘点五元组 Checkpoint 契约（[`#8461`](https://github.com/loopx-project/loopx/pull/8461) / [`#9209`](https://github.com/loopx-project/loopx/pull/9209) / [`#9211`](https://github.com/loopx-project/loopx/pull/9211) 与 [`replay_runtime.py`](../../../loopx/capabilities/explore/replay_runtime.py)）与 E2B 兼容微虚拟机 `pause` / `resume` / `snapshot` / `fork` / `destroy` 语义之间的接口缺口。
- 定义双中立 SPI 记录结构、生命周期状态机、`ReplayFidelity` 映射表及 `RiskAwareReplayChildAdapter` 桥接契约。
- 提供基于 `ReplayPointRegistry` 与 `run_counterfactual_child_group` 的 M0 确定性 pytest 契约测试切片。

放置理由：`capability_id: none`（将既有 `explore` 回放/反事实契约桥接至外部可选沙箱扩展），`origin: extension`；未来通过 `M1` 验证的适配包归入 `packages/` 或独立扩展包，不在本 RFC 中向 `loopx/` 核心新增包或语义词表。

### 非目标

- 在 `loopx/` 核心直接内置 E2B/Firecracker 客户端或依赖。
- 用微虚拟机快照替代常规 CI/PR 维护 Turn 中的 Git worktree 或签名更新链路。
- 声称沙箱分叉本身具备多租户安全隔离，或未跑 M2/M3 对照试验前宣称 $\Delta J(B \mid s_0) > 0$。

---

## 4. 基线系统盘点与五元组接口缺口矩阵

基于基线 `dc79482c88` 与已合入主干的 Explore/Benchmark 契约（[`#8461`](https://github.com/loopx-project/loopx/pull/8461)、[`#9209`](https://github.com/loopx-project/loopx/pull/9209)、[`#9211`](https://github.com/loopx-project/loopx/pull/9211)；早先未合入提案 `#8516` 已拆解由上述权威 owner 承接），五元组 Checkpoint
$s = (c_{\text{host}}, \sigma_{\text{loopx}}, w_{\text{ws}}, \phi_{\text{env}}, b_{\text{eval}})$
与微虚拟机沙箱语义的现状及缺口如下：

| 五元组要素 | 当前 LoopX 权威 owner | 已有能力边界 | 面向工作区与进程级分叉的接口缺口 |
| --- | --- | --- | --- |
| $c_{\text{host}}$ 宿主会话游标 | [`research_checkpoint.py`](../../../loopx/hosts/research_checkpoint.py)、[Stride §7.7](hierarchical-agent-stride-control-v0.zh-CN.md#77-研究状态分支与匹配接续) | 可导出/分叉已完成 Codex turn 前缀与不可变验证单元 | 缺少对沙箱 guest PID/守护进程游标及挂起工具 PTY 的绑定 |
| $\sigma_{\text{loopx}}$ 控制面权威状态 | `authority_store` SQLite、[`replay_runtime.py`](../../../loopx/capabilities/explore/replay_runtime.py) | `ReplayPointRegistry` 原子绑定 Trace 游标与不透明 `AdapterStateLease` | 已清晰外置，但缺少防止调用方将 `.loopx/` 挂入快照及自动擦除的标准门禁 |
| $w_{\text{ws}}$ 工作区文件系统 | [`checkpoint_artifacts.ts`](../../../loopx/control_plane/runtime/checkpoint_artifacts.ts)、[LayerFS RFC](layerfs-engineering-exploration-evaluation-v0.zh-CN.md) | 字节校验目录归档（64 MiB 上限；拒绝 symlink） | 无法容纳 `.pnpm`/`.venv` 等 symlink 密集环境；LayerFS 仅覆盖文件系统 |
| $\phi_{\text{env}}$ 进程、内存与网络信封 | [`checkpoint.py`](../../../benchmark/runtime/checkpoint.py)、[`counterfactual_runtime.py`](../../../loopx/capabilities/explore/counterfactual_runtime.py) | `IsolatedReplayChildAdapter` 已定义 `PROCESS_ISOLATED`、`worker_identity` 与风控钩子 | 缺乏串联 CoW rootfs、VM 内存页与网络隔离的 `pause`/`resume`/`snapshot`/`fork` SPI |
| $b_{\text{eval}}$ 预算与评测谱系 | [预算内规划](../../research/budgeted-research-planning.zh-CN.md)（`#8461`）、[基准计划 §5.5 与 §6.2](long-horizon-harness-benchmark-research-program-v0.zh-CN.md#62-效率)（`#9211`） | Tier-2 静止检查点校准、完整成本核算（`ReplayPointMetricsSnapshot`）与隔离 `worker_identity` 守卫 | 缺乏挂起/快照/恢复的资源开销拆分及分支隔离 ID 来源映射 |

---

## 5. 拟议架构与 SPI 契约

### 三方职责与权威边界

```text
+-----------------------------------+        纯运行时不透明绑定        +------------------------------------+
|        LoopX 核心控制面            |<-------------------------------->|     Sandbox SPI 扩展（默认关闭）     |
| - TraceLog & ReplayPointRegistry  |  AdapterStateLease / ChildLease  | - pause / resume（1:1 同实例恢复）  |
| - Run / 租约 / Quota / CQR        |  SandboxFidelityTier -> Fidelity | - snapshot / fork（1:N 写时复制）  |
| - RiskAwareReplayChildAdapter     |<================================>| - 秘密/租约擦除与出网防火墙          |
|   双端风控审查与推广判定           |    公开摘要与 5 维资源成本回执    | - destroy 与快照引用计数保护         |
+-----------------------------------+                                  +------------------------------------+
                 ^                                                                        ^
                 | 控制面权威严格外置（不进 guest 镜像）                                    | guest 内执行
                 v                                                                        v
+------------------------------------------------------------------------------------------------------------+
| 宿主会话 Owner（Codex / DSH / Pi） + 隔离微虚拟机 / CoW 工作区（独立 branch_isolation_id）                 |
+------------------------------------------------------------------------------------------------------------+
```

### 核心 SPI 记录与保真度映射

1. **`SandboxHandle`：** `{ provider_id, sandbox_id, state: creating|running|paused|destroyed, rootfs_kind, guest_arch }`
2. **`SandboxPauseReceipt`：** `{ sandbox_id, paused_at, quiesced_fs: bool, active_pid_digest, dirty_page_flush_ok: bool }`
3. **`SandboxSnapshotDescriptor`：** `{ snapshot_id, parent_sandbox_id, fidelity_tier, rootfs_digest, env_manifest_digest, memory_digest|null, scrubbed_keys: tuple[str, ...], byte_size: int }`
4. **`SandboxBranchHandle`：** `{ branch_id, branch_isolation_id, source_snapshot_id, fork_sandbox_id, egress_policy_id, achieved_fidelity: SandboxFidelityTier }`

| `SandboxFidelityTier` | 保留范围 | 映射 `ReplayFidelity` | 必需等价检查（`EquivalenceCheck`） |
| --- | --- | --- | --- |
| `filesystem_only` | 纯净源码/CoW 目录树，无进程现场 | `BEST_EFFORT`（若校验锁定依赖可为 `SEMANTIC_EQUIVALENT`） | rootfs 摘要 + symlink 保留校验 |
| `filesystem_and_env` | CoW rootfs（`.pnpm`/`.venv`、构建缓存）与固定环境清单 | `SEMANTIC_EQUIVALENT` | rootfs/symlink 摘要 + 工具链 ABI 校验 |
| `process_tree_quiesced` | CoW rootfs + 环境清单 + 优雅停止并按需冷重启的确定性守护进程 | `SEMANTIC_EQUIVALENT` | rootfs 与环境摘要 + 守护进程存活/端口就绪证明（缺证 fail-closed 拒绝提升） |
| `microvm_memory_exact` | 暂停态微虚拟机 vCPU/内存页 + 块设备 CoW + 打开的 FD/LSP | `EXACT` | rootfs 与内存状态 `expected_digest == observed_digest` |

### 生命周期、幂等性与 Provider 能力矩阵

- **`pause(sandbox_id, quiesce=True)`：** 状态从 `running -> paused`，执行 guest 刷盘并返回 `SandboxPauseReceipt`；同一 `operation_id` 对已暂停实例幂等。
- **`resume(sandbox_id)`：** 在**同一** `sandbox_id` 上从 `paused -> running`；对已在运行或已销毁实例调用 `resume` 必须 fail-closed 报错。
- **`snapshot(sandbox_id, *, target_tier, scrub_keys)`：** 当 `target_tier` 为 `process_tree_quiesced` 或 `microvm_memory_exact` 时强制要求处于 `paused` 屏障（**I1**）；擦除租约/密钥后封存不可变 `SandboxSnapshotDescriptor`，签发显式父持有令牌（holder token）并保持引用计数 > 0。
- **`fork(snapshot_id, *, child_id, target_branch_id, egress_policy_id)`：** 基于不可变快照克隆写时复制存储（`process_tree_quiesced` / `microvm_memory_exact` 下分别保留守护进程就绪清单或内存页），为每个子分支签发独立快照持有令牌与 `branch_isolation_id`，映射到 [`replay_runtime.py`](../../../loopx/capabilities/explore/replay_runtime.py) 的 `ReplayChildLease(concurrency=PROCESS_ISOLATED, worker_identity=...)`。**单分支反事实后缀回放**（`run_from` / `RestoreReplayStateRequest(replay_point_id, target_branch_id, minimum_fidelity)`）必须复用该 `fork` 与目标分支绑定 owner 获取并核验**隔离目标沙箱**，严禁原地回写或覆盖仍在推进的源沙箱状态（遵循 [Stride §7.7](hierarchical-agent-stride-control-v0.zh-CN.md#77-研究状态分支与匹配接续) 第 4 项）；仅实现 5 个规范 SPI 原语的 provider 无需提供会改写源实例的就地恢复方法（缺失 `fork` 时返回类型化拒绝 `fork_unsupported`），`validate_replay_equivalence` 核验隔离目标句柄，并在后缀结束后由 `finalize_replay_suffix` 经 `destroy` 回收临时目标分支。
- **`destroy(sandbox_or_snapshot_id, *, holder_id=None)`：** 回收子分支句柄或幂等地释放调用方自身持有的快照令牌（或可重读释放回执），严禁盲目按整数裸减 `refcount -= 1`；当父节点释放（`ReplayPointRegistry.release`）在服务端提交后丢失返回确认并触发重试时，重放仅对已释放的父令牌幂等返回，绝不误消耗仍在运行子分支的活体引用，直至子分支终止并释放其自身持有令牌后才回收快照。

| Provider profile | 支持原语 | 最高保真等级 | 限制与约束说明 |
| --- | --- | --- | --- |
| `local_worktree` | `fork`, `destroy` | `filesystem_only` | 现有 Git 基线；不保留内存状态 |
| `layerfs_cow` | `snapshot`, `fork`, `destroy` | `filesystem_and_env` | 优先 Linux ext4/xfs/btrfs；完整保留 symlink |
| `docker_oci` | `pause`, `resume`, `snapshot`, `fork`, `destroy` | `process_tree_quiesced` | 依赖宿主 cgroup/CRIU 能力 |
| `e2b_microvm` | `pause`, `resume`, `snapshot`, `fork`, `destroy` | `microvm_memory_exact` | E2B 托管或自建 Firecracker；须启用密钥擦除与出网隔离 |
| `firecracker_local` | `pause`, `resume`, `snapshot`, `fork`, `destroy` | `microvm_memory_exact` | 需 Linux KVM 宿主机；本地 M1/M2 对照基线 |

---

## 6. 备选方案与设计取舍

1. **直接在 `loopx/` 核心集成 E2B SDK：** 拒绝。会导致小内核强耦合特定微虚拟机传输与依赖，违背[分发拆分原则](monorepo-distribution-split-v0.zh-CN.md)。
2. **把 `.loopx/` SQLite 状态打入 VM 快照：** 拒绝（**I2**）。会诱发多分支并发持有旧租约、重复结算以及反事实独立性破坏。
3. **合并 `pause`/`resume` 与 `snapshot`/`fork`：** 拒绝（**I3**）。前者服务于单实例人机停等挂起，后者要求快照不可变性、凭据擦除、新隔离 ID 与多臂成本独立归因。

---

## 7. 安全、隐私与兼容性

- **默认关闭扩展与 M0 共享回放加固：** 所有沙箱 SPI provider 默认保持关闭且零激活开销，`research-checkpoint` 与常规 Git worktree 均不新增字段或外部依赖；M0 同步加固共享 `ReplayPointRegistry.run_from` 在恢复、等价校验、Agent 状态恢复与后缀执行各阶段的清理边界、`FAILED` 隔离及 `BaseException` 异常优先级（前置 `Exception`/`BaseException` 中断均会调用 `finalize_replay_suffix(..., suffix_succeeded=False)`，非 `Exception` 的清理 `BaseException` 优先于普通阶段 `Exception` 抛出，同时保持原有 `suffix_minutes` 统计窗口）。
- **凭据与租约擦除：** 母沙箱内不得持久化保存 LoopX 写入租约凭据；`snapshot()` 封存前擦除声明密钥路径，子分支仅在通过 `assess_replay_child_intent` 后按需注入短期受限令牌。
- **公开载荷零句柄泄露：** 所有 `sandbox_id`、快照路径与连接串严格保留在 `AdapterStateLease.binding_key` / `ReplayChildLease.child_key`，`ReplayPoint.to_record()` 与 `CounterfactualReplayResult.to_record()` 保持完全公开安全。
- **低保真降级保底：** 当候选仅提供 `filesystem_only`（`SEMANTIC_EQUIVALENT` / `BEST_EFFORT`）快照而假设要求 `ReplayFidelity.EXACT` 时，必须诚实判为 `OBSERVED_ONLY`，禁止按 `PROMOTED` 合入。

---

## 8. 迁移与回滚

1. **前置检查与准入：** 校验 provider 可用性、本地 KVM/内核条件、guest OS/架构匹配及出网策略。
2. **静止切入：** 先进入 quiesced 或 `paused` 状态再调用 `snapshot()`；冷启动 `local_worktree` 对照组保持平行可用。
3. **回滚路径：** 关闭扩展 profile 或停止传入 SPI adapter 即可立即回退；销毁未固定的子分支不会破坏 LoopX SQLite/Trace 主账本，无不可逆迁移。

---

## 9. 验证与验收标准

| 声明 | 验证命令 / 测试用例 | 验收判据 | 边界与未覆盖说明 |
| --- | --- | --- | --- |
| V1：精确内存快照前置静止屏障 | `test_exact_memory_snapshot_requires_paused_quiescence_barrier` | `running` 态精确快照 fail-closed 拒绝；`paused` 后成功生成 `EXACT` 点 | 基于内存级参考 SPI 模型，尚未在真实 Firecracker/KVM 上实测 |
| V2：1:1 挂起与 1:N / 单分支隔离分叉、不可变性及 symlink 保留 | `test_pause_resume_preserves_single_sandbox_identity_and_live_process_table`、`test_snapshot_fork_isolates_concurrent_counterfactual_branches_and_symlinks`、`test_single_branch_replay_forks_isolated_target_without_overwriting_live_source`、`test_sealed_snapshot_rejects_descriptor_mutation_and_isolates_readback_and_forks` | 同 `sandbox_id`/PID 无损恢复；并行分叉保留 `.pnpm`/`.venv` 且互不污染；单分支 `run_counterfactual_suffix_replay` 通过 `fork` 创建隔离目标验证后缀并保持原运行中源沙箱不被回写（兼容仅暴露 RFC 规范原语、无 `restore_snapshot` 的 provider，缺失 `fork` 返回 `fork_unsupported`）；对外描述符与封存快照严格隔离 | 不代表生产级冷/热延迟与内存去重实测数据 |
| V3：控制面租约/密钥擦除与公开载荷防泄 | `test_authority_and_secret_scrubbing_blocks_guest_lease_cloning_and_public_key_leak` | 擦除母快照敏感键；子分支复用父租约或 `shared_prod` 出网被风控拒绝；公开记录无运行时句柄 | 真机部署前需对特定云镜像做实测擦除审计 |
| V4：保真度过滤降级、隔离恢复等价校验与持有令牌化快照寿命 | `test_fidelity_degradation_filters_filesystem_only_provider_when_exact_process_state_required`、`test_single_branch_restore_verifies_live_state_and_rejects_unrestored_or_incomplete_exact_evidence`、`test_process_tree_quiesced_requires_live_daemon_proof_and_refuses_lost_daemon`、`test_process_tree_quiesced_promotes_healthy_cold_restart_with_changed_pid`、`test_microvm_memory_exact_requires_independent_memory_pages_capture`、`test_child_branch_release_preserves_pinned_snapshot_until_explicit_destroy`、`test_parent_release_retry_after_lost_ack_preserves_live_child_holder_reference` | 非 EXACT、未恢复或缺失 `memory_pages` 捕获证据时拒绝或降级 `EXACT` `PROMOTED`，真实隔离恢复保留 `EXACT` 资格；`process_tree_quiesced` 强制验证存活守护进程健康、允许健康冷重启改变 PID 后按 `SEMANTIC_EQUIVALENT` 提升并在丢失/不健康时拒绝；子分支释放以及父释放提交后丢 ack 重试均幂等保留其余活体持有者引用直至全部释放 | 真实 E2B/Firecracker 接入与 UI 回读属 M1–M3 范围 |

---

## 10. 运行与运维契约

实现本 SPI 的扩展必须回吐与[基准计划 §6.2](long-horizon-harness-benchmark-research-program-v0.zh-CN.md#62-效率)及 [`ReplayPointMetricsSnapshot`](../../../loopx/capabilities/explore/replay_runtime.py) 对齐的 5 维成本指标（`wall_seconds`、`total_tokens`、`evaluator_runs`、`control_plane_requests`、`recovery_events`）与类型化失败码（`quiescence_required`、`daemon_health_required`、`memory_capture_required`、`fork_unsupported`、`snapshot_missing`、`egress_violation`、`scrub_incomplete`、`capacity_exceeded`），受按持有令牌核算的快照引用计数与 TTL 约束，并通过既有 Explore/Benchmark 历史面展示只读状态。

---

## 11. 规范性交付计划

| 里程碑 | 交付行为 | 进入门禁 | 退出证据 | 回退方式 |
| --- | --- | --- | --- | --- |
| M0 | 双语 SPI RFC + 17 项契约一致性测试（`test_sandbox_snapshot_fork_spi_conformance.py`）+ 共享 `ReplayPointRegistry.run_from` 清理与异常优先级加固 | 完成 `#8461` / `#9209` / `#9211` 与 `replay_runtime.py` 盘点 | V1–V4 pytest、`check_rfc_bilingual`、`docs-governance-smoke` 与 `canary premerge` 全绿 | 撤销文档、测试及 `replay_runtime.py` 中 `run_from` 收口片段；SPI provider 默认关闭 |
| M1 | 首个可选扩展实现（`firecracker_local` 或 `e2b_microvm`） | M0 合入；固定 Linux/E2B 验证镜像 | 真机 `pause`/`resume`/`snapshot`/`fork`、密钥擦除、symlink 与关闭态等价性 | 停用扩展 profile |
| M2 | 与热 `git worktree` / `layerfs_cow` 的配对反事实对比 | M1 通过；冻结题目、预算与评估器 | 基于 `counterfactual_runtime.py` 与 `#9211` §§5.5/6.2 输出完整 5 维成本与 Tier-2 $\tau_B(s; a, a_0, \pi)$ 对照结论 | 终止试验，如实保留负结果/平手数据 |
| M3 | 可选产品/评测推广评审 | M2 复现净收益（$\Delta J(B \mid s_0) > 0$）且 UI 回读齐备 | 维护者批准与打包态不可用/恢复闭环验收 | 维持默认关闭或退役扩展 |

---

## 12. 待定决策

1. **OD1（M1 首选 Provider 顺序 — Benchmark / 运行时维护者）：** 依据 KVM 宿主可用性与离线 CI 复现成本，决定 M1 先验证 `firecracker_local` 还是 `e2b_microvm`；默认持续关闭。
2. **OD2（进程内存等价摘要粒度 — Explore / Replay 维护者）：** 在 M1 退出前明确 `microvm_memory_exact` 的 `observed_digest` 采用工作区与进程表摘要还是全页块摘要。
3. **OD3（多臂母快照成本分摊口径 — Benchmark 度量维护者）：** 确认父母快照成本按 $K$ 条并行分叉均摊还是按[基准计划 §6.2](long-horizon-harness-benchmark-research-program-v0.zh-CN.md#62-效率)对每个对照分支单独保留未分摊原值。

---

## 附录 A：执行台账（非规范）

后续实测切片的带日期记录统一归档至 [`ledger/README.zh-CN.md`](ledger/README.zh-CN.md)（配对 `.md` / `.zh-CN.md`）。

## 附录 B：决策日志

| 日期 | 决策 | 负责人 / 批准 | 备选方案 | 变更章节 |
| --- | --- | --- | --- | --- |
| 2026-11-22 | 提出基于既有 `replay_runtime` 契约的可选扩展 SPI 设计与 M0 契约测试（OPEN 提案待审） | 待维护者评审 | 内核直连 E2B SDK；guest 内存/镜像内含 SQLite | §§1–12 |

## 附录 C：证据登记

| 证据 ID | 声明 | 基线 / 环境 | 产物或命令 | 结果 | 隐私 / 有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | 五元组 Checkpoint 与 `checkpoint_artifacts.ts` / `replay_runtime.py` 盘点 | `dc79482c88dd75ea59785d165fd3050d9d5508c3` | 源码审计与 [#8461](https://github.com/loopx-project/loopx/pull/8461)、[#9209](https://github.com/loopx-project/loopx/pull/9209)、[#9211](https://github.com/loopx-project/loopx/pull/9211) | 已在主干权威 owner 核实 | 只读盘点；早先原型 `#8516` 已关闭未合并 |
| E2 | M0 V1–V4 SPI 一致性测试（基于真实 `ReplayPointRegistry`、`run_counterfactual_suffix_replay` 与 `run_counterfactual_child_group`） | 独立 worktree `python -m pytest` | [`test_sandbox_snapshot_fork_spi_conformance.py`](../../../tests/test_sandbox_snapshot_fork_spi_conformance.py) | PR 门禁通过（17/17 M0 测试） | 使用参考 SPI 模型 fixture，未连接真实 hypervisor |

## 附录 D：已否决替代方案

拒绝内核内置 E2B SDK、镜像内克隆 `.loopx/` 状态以及合并 `pause`/`resume` 与 `snapshot`/`fork` 的理由见第 6 节；仅当宿主隔离模型出现根本变化时再重评。
