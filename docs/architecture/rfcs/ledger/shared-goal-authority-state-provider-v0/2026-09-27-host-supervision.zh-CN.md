# 本地默认切换：受管 Host 进程监督与交付重估

- 基线：`fd96e5e25`，2026-09-27 核对。
- 目标：总路线 S2/S4/R5、shared authority 外部执行闭环、TS 替换式迁移。
  不新增 provider 或 capability。
- 本文替换 9 月 24 日清单的**剩余交付估算**，不改写历史证据。
  [#5140](https://github.com/loopx-project/loopx/pull/5140) 的恢复切片仍未合入。
- [English](2026-09-27-host-supervision.md)。

## 数交付，不数架构标题

完整来源传输与组装、事务捕获、canonical 分页、File v1 自动备份升级、Python
原型退役已在 main。#5013、#5063、#5102、#5105 不再计入待开发。两个已晋升 Goal
证明的是那两次切换，不代表全部执行、迁移、恢复边界已完成。

旧的“三个大包”以及 #5140 的“之后三个 PR”对执行保护估得过粗。真实子进程复现
发现前置缺口：通用 Host 超时只杀主进程；Codex 清理在主进程退出后直接返回；
两者都可能遗留继续工作的后代。删除租约或拒绝最终结果不能让这些进程停止。

当前规划的**四个新增交付包含本 PR**：

| 交付 | 可观察退出条件与 Python 退役 |
| --- | --- |
| **1. 受管子进程监督（本 PR）** | 通用命令与 Codex CLI 共用 TS 生命周期，覆盖超时、调用方消失、管道排空和进程组终止；删除各自的 Python 终止及读线程实现。完成进程部分，不冒称下行租约部分已完成。 |
| **2. 权威约束的执行区间** | 把现有 provider-neutral 租约 owner 接到实际执行：启动前当前证明、执行中有界续约、到期/回收/撤权取消、不确定效果恢复。回收不能静默重叠旧执行器。真实进程与 File/SQLite 验证；附着式 Host 无取消能力时明确支持边界。替换 Python 决策，不另建租约存储。 |
| **3. 整 Goal 迁移与带 fence 的恢复闭环** | 接入 #5140 恢复与 #5054 来源退役；覆盖来源排空、已审切换、保留命令消费者、投影读回及存在后续写入时的回退。先盘点 caller，再决定是否需要 writer；只有真实 caller 已迁移才删除旧决策。 |
| **4. 默认入口与有界 Python 清理** | 新 Goal、设置、CLI、打包前端、Lark 一致选择合格的本地 profile；旧 Goal 有显式升级、备份、恢复。删除已替代 Python 业务 writer，保留必要渲染与 Host IO adapter。 |

**本 PR 之后仍规划三个新增 PR。** 这是有具体边界的计划，不是保证总数，也不代表
租约监督已经交付。相比 #5140 的提案，明确多拆一个进程切片，原因是上面的复现；
不能用本 PR 抵扣未完成的租约行。后续若再拆，必须修改具体行并给出证据。

既有在途 PR 另计：#5140 恢复/审计、#5054 旧 Todo 事件退役与 supervisor 日志、
#4931 SQLite 回执证明编码。因此 **File 路线的已知合入清单为六项**（本次 + 三项
待开发 + #5140 + #5054），**SQLite 路线加入 #4931 后为七项**。这里包含已经实现
但没合入的 PR，绝不是还要新写六七个。#5140 的 SQLite 批量证明读取与 #4931
互补；小规模测试通过不能代替 D2。资格验证仍可能发现需修改的缺陷，因此不能
承诺无条件总 PR 数。

D1 消费者一致性、各 profile 的 D2 容量/恢复/soak、D3 cohort 切换是验收工作，
不能凭空折算 PR。核对的 #4224 1 MiB 报告仍有 receipt p95 269.03ms / 50ms、
scan-100 p95 801.81ms / 250ms 未通过，本次进程改造不能修复或认证这些指标。
PostgreSQL 的认证传输、tenant/identity、跨 Host 执行、连接池/故障切换、运维
资格仍是独立中期路径。本地进程清理不读取 provider 物理布局、不产生权威，
因此各 provider 可以复用。

## 归属与邻近工作

`control_plane/turn_driver/host_process.ts` 拥有进程生命周期，私有 bridge 将 Python
调用方控制管道 EOF 视为取消。Python 适配瞬时输出、Codex 会话、typed result。
现有 `turn run-once` 自动接入，无新 CLI 参数、配置编辑器、capability、前端或
Lark 界面。附着式 App 会话与进程内 DSH adapter 不经过此子进程 owner，不宣称
它们因此得到保护。

#5141 用 GoalRef 约束 Host 状态；#5142 保留 Turn 错误读回中的效果不确定性。
两者不能替代进程监督。集成时要保留启动前准入和恢复观察，本 PR 不修改 GoalRef
权限或 settlement 语义。

[操作行为和限制](../../../../reference/protocols/loopx-turn-v0.md#managed-host-process-lifetime)。
