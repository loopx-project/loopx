# Controller 相关精简与退役交接

- 上位文档：[controller/executor RFC 贡献者交接](../../controller-executor-unified-contract-v0.zh-CN.md#职责收敛与贡献者交接)。
- 源码核对：`27991e27ba998439d7faf2fc113efcfe82882144`，2026-10-27。
- 状态：有源码依据的工作拆解；未交付实现、runtime 资格、安装或默认值变更。
- [English mirror](2026-10-27-refactor-handoff.md)。

## 认领前阅读

目标是减少独立决策、减少同一规则的修改位置，并删除被替代的生产路径。把 Python
搬到 TypeScript、改文件名或新增共享类，本身不能交付这些收益。本次核对限定于
controller 既有执行/会话/结算调用方及相邻退役边界，不是全仓穷尽审计。

这是候选盘点，不是排好优先级的队列。下一成果应对照整体 roadmap、当前用户方向、
实际瓶颈、活跃 PR 和 canonical 工作选择。不能从编号、行序或贡献者交接推断全局
优先级，也不把 Agent 固定在一条永久工作线上。当前正确性或契约/authority 交付缺口
可以优先于任何精简项。在既有任务中解释所选成果的价值与替代项，交付或新证据出现后
重新评估，而不是以做完清单为目标。

若选定 C3.1，且独立拆分只会留下 facade，C3.2 可以同 PR。C4.2 需要适用且已合格的
C4.1 边界，不需要其他所有工作包。C4.3、C7.1、C8.1、C9.1 需要各自的比较或退役证明；
若职责不同就拒绝或缩小候选。C9.2 随所属实现交付，不是新框架或前置项目。工作包不等于
PR 数量；优先级建议不新增准入或批准 gate。

认领前刷新目标 main、相关 PR 与 canonical Todo，实际归属记录在那里。本文不授予
代其他人分配任务、合并或模型执行权限。核对时的开放 PR 范围指出以下接缝：

| 既有工作 | 协调要求 |
| --- | --- |
| [#7430](https://github.com/loopx-project/loopx/pull/7430)、[#7457](https://github.com/loopx-project/loopx/pull/7457)、[#7461](https://github.com/loopx-project/loopx/pull/7461) | 对齐活跃 C1/C2 契约组合/切换、上下文引用和 Host 输入工作。不另建 composer，不重写已合格 capture/context 交付；这是范围协调，不是 Agent 永久分工。 |
| [#7456](https://github.com/loopx-project/loopx/pull/7456) | 采用其中独立取得资格的恢复保护；不把有界修复扩成全部生命周期收敛，也不当作自动核对能力。 |
| [#7462](https://github.com/loopx-project/loopx/pull/7462)、[#7411](https://github.com/loopx-project/loopx/pull/7411)、[#7298](https://github.com/loopx-project/loopx/pull/7298) | 对齐已有 primary-action、Todo 摘要、Vision 等待退役提案，不平行做 Python/TS 迁移。 |
| [#3412](https://github.com/loopx-project/loopx/pull/3412)、[#5677](https://github.com/loopx-project/loopx/pull/5677) | 结算 readback、Host 首次交付语义与 C4.3 边界相邻；替换前检查当前 diff。 |
| [#6838](https://github.com/loopx-project/loopx/pull/6838)、[#6900](https://github.com/loopx-project/loopx/pull/6900)、[#7252](https://github.com/loopx-project/loopx/pull/7252) | 研究 worker launch/admission 与租约范围修复保留原 owner；只协调实际触及的机制。 |

PR 范围只是发现依据，不是批准或实时状态库。实现前再次核对，不能推断作者已采用
或取得资格。

## C3.1 — 公共受管操作协议

**已确认重叠 / 共享代码归属不当。** `loopx/control_plane/turn_driver/` 下的
`codex_operation_host.py::run_codex_operation_host` 与
`dsh_operation_host.py::_run_dsh_operation_host` 都核对计划会话与 binding/profile、
复查已确认 operation 范围、获取 pending handoff，并回报原生执行开始。DSH 已从
Codex 模块导入通用 `operation_tool_handler`：handler 已共享，不应重写两份。

- **目标与删除：** 为既有协议整理 Host 中立的归属并迁移两个调用方；删除重复公共
  编排，以及内部 Codex 专属 handler 入口/导入。复用 collaboration、Goal admission
  的当前裁决；adapter 提供原生事实，不能生成 actor 身份或批准。不建 `BaseHost` 层级。
- **保留：** Codex 原生接受元数据、DSH 连接 pin/restore 限制、初始化竞态、工具隔离
  与原生资源归属。相似 callback 不证明权限等价。
- **决定性验收：** 两个真实 operation 入口保留原授权 session/operation、pending 项
  过滤与一次 start；profile/Todo/Goal 变化或缺失原生接受，在 effect dispatch 前拒绝。
  扩展 `tests/test_codex_operation_host.py`、
  `tests/control_plane/test_dsh_operation_entry.py`，保留 SDK/connection 案例。
- **依赖 / 回退：** 现有单会话行为可先于 C1 重构，不改其 schema；调用方采用与公共
  抽取一并回退，无需状态格式迁移或新 runtime 开关。

## C3.2 — 完整的会话绑定调用边界

**持久化已共享，调用方组合分散。** `codex_sessions.py` 已通过 host 参数支持 Codex、
DSH schema。Operation adapter 仍使用私有读写 helper，并在周边组装 binding/profile
校验。`driver.py::reconcile_failed_turn_session_request`、`session_recovery.py`
消费同一 managed-session 身份。

- **目标与删除：** 复用这个持久化 owner，仅提供真实调用方需要的完整 select/verify/
  store 操作；删除被替代的调用方重建与跨 Host 私有导入。单纯改模块名不算此工作包；
  无独立收益时与 C3.1 合并。
- **决定性验收：** new/resume/fresh、profile 变化、并发 binding 替换、checkpoint
  来源与既有失败恢复均保留精确 session、Goal instance。验证两个原生 adapter 及原
  持久化文档，不引入第二个 session 数据库。
- **依赖 / 回退：** 行为保持切片保留当前磁盘路径/wire 版本。后续 C3 role/generation
  扩展需要 C1 稳定及显式迁移/replay/rollback；不能覆盖旧 native ID，不能跨 Host/role
  借用连续性。回退前读回已接受回执。

## C4.1 — Typed 回执到继续决策边界

**仍存跨语言决策归属。** `loop_controller.py` 拥有 `ValidatedTurnReceipt`、
`BoundedTurnBudget` 与确定性 disposition 规则；`managed_step.py` 从 journal 重建
execution 形状的回执，同时调用 `turn_journal.ts` 检查一致性。`recovery.py` 已包装
该 TS journal 裁决。Journal 恢复和决定下一项已准入动作是两个问题，位置相邻不证明
应该合成一个状态。

- **目标与删除：** 演进既有 typed Turn/effect 领域，以一个完整事实请求组合回执
  资格和确定性继续决策。必要时保留不同领域裁决，但删除被替代的 Python 重建、准入
  分区和策略求值。保留已注册 controller contract，不加 workflow DSL，也不为每个
  字段新增 runtime operation。
- **决定性验收：** `tests/test_loop_turn_loop_controller.py`、
  `tests/test_loopx_turn_managed_step.py` 与 journal 测试中的独立语义保留因果前驱、
  选定工作、预算、重试上限和提前返回顺序。某分支本可提前返回，后续事实不能因此变成
  必填。File/SQLite 真实 run-once/managed-step readback 保留原回执、缓存结果不重新
  派发、结算一次。
- **依赖 / 回退：** 整合 C1 稳定契约与已合格恢复修复。特征化及已证实局部删除不必
  等待完整 C1 收益实验。在交付切片中替换真实消费者；代码回退时保留历史解码与原
  effect readback。

## C4.2 — 外层 worker 的薄语义采用

**有界策略收敛候选。** `scripts/external_scheduler_worker.py` 的 `_host_attempt`、
`_host_result`、`_run_worker_locked` 混合本地 IO、Host retry、pending-replan 和
continuation 解释；它们已经复用 Host failure helper。不能把已有复用算作缺口，也
不能把外层唤醒等同于受治理 Turn。

- **目标与删除：** C4.1 后，将一条支持观察的 Host 路径迁到已合格语义观察/裁决；
  只删除被替代的公共策略分支。Polling/backoff 的实际执行、命令 transport、wake
  身份和原 Host 结算仍归真实 scheduler owner。
- **决定性验收：** `tests/test_external_scheduler_worker.py` 加真实产品入口覆盖
  已存失败、原 replan、丢响应、重复 wake、stop、重试耗尽。Shell 成功/ACK 不能变成
  语义结果，unknown/不透明 effect 仍是 unknown。替代路径覆盖同一崩溃窗口前，不能
  删除 pending-intent witness。
- **依赖 / 回退：** C4.1 和能观察原 attempt 的 Host。不透明命令保留明确的支持/降级
  边界，不能盲目自动重试。回退前核对原未结工作。

## C4.3 — 基于既有原语的结算 adapter

**需要比较，不是已证明重复的事务。** `host_adapter_settlement.py` 解码并执行有序
CLI plan 及条件；`host_todo_completion.ts` 拥有其 prepare/finalize 语义。
`turn_driver/settlement.py` 则执行既有 TS settlement reducer 与 provider readback。
两者复用 `SettlementIdentity`，但入口义务不同。
`extensions/governed_capability_execution.py` 已调用 `execute_turn_driver_settlement`。

- **目标与删除：** 先比较 identity、validation、guard/vision、completion、持久写回、
  spend、closeout、recovery 的义务；在既有 owner 复用相同 effect/receipt 原语。
  调用方采用等价 typed 边界后才能删除第二套 condition/step 解释器；保留真实的 Host
  入口特定义务。
- **决定性验收：** commit 前后失败、丢响应、重复完成、无 Todo replan 中，原 effect
  ID 与分阶段持久 readback 不变。使用既有 MCP、Turn settlement、external-capability
  集成案例；不同事务边界保持显式。
- **依赖 / 回退：** 对齐 #3412/#5677 及稳定契约输入。比较完整路径 runtime crossing/
  成本；把微小条件搬成每步新 RPC 不自动带来改进。回退前读回原 prepared effect，
  不能丢弃 journal。

## C7.1 — 既有 runtime 边界内的 worker/进程清理

**机制重叠，语义待资格。** `benchmark/runtime/worker.py` 包含 `child_process`、
`_reap_child_process`、`interrupt_checkpoint_process`；产品
`host_process_transport.py` 拥有 supervisor record、drain 观察和受保护进程执行。
都使用 POSIX 调用，不代表 stop/归属契约可互换。

- **目标与删除：** 复用适用的既有进程原语，使 benchmark 入口 adapter 变薄。匹配
  策略后才删除重复 launch/cleanup；保留 task environment、checkpoint 行为、原生
  事件解码和外层 wake 归属。
- **决定性验收：** 真实临时进程组覆盖正常退出、spawn 失败、timeout、后代进程、
  interruption、unknown drain；ACK 不等于退出。保留信号顺序、截止时间、descriptor/
  environment 处理和平台支持报告。Native/plain 对照组不能仅因复用而新增 LoopX
  admission 依赖、改变 scoring、budget 或 instrumentation。
- **依赖 / 回退：** 对齐既有 worker/admission 工作；本文不授权模型或 benchmark job。
  策略匹配失败则保留独立 owner。代码回退保留已准入进程和回执归属。

## C8.1 — 一个运行事实来源，沿用 readback 页面

**投影候选，不替代 authority。** `control_plane/runtime/session_runtime.py`、
`status/lifecycle_projection.py`、dashboard
`features/personal-workspace/goal-activity.ts` 适配 activity/work 事实；部分已委托共享
owner。显示分组、session transport 状态与工作准入不是可互换的状态机。

- **目标与删除：** role-aware session 事实可用后追踪到 App/Chat/CLI。只删除证实重复
  的语义分类，保留呈现分组/本地化。不建另一个 controller console、provider 设置或
  状态库，也不因名称相似而合并旧材料摘要分类与原生 session 持久化。
- **决定性验收：** 既有打包旅程从同一 typed 事实展示真实工作、Host 不可用、恢复/
  unknown、stop 范围和迟到结果。包括有内容的桌面/窄屏首屏；unknown 不等于 idle 或
  零，受众/脱敏边界保持有效。
- **依赖 / 回退：** C3/C6 及已证实分类重叠。旧消费者仅因明确 wire/support 窗口
  保留；回退投影消费者不授予执行权限，也不改变 runtime 设置。

## C9.1 — 有证据地退役旧租约/来源执行路径

**有具体保留路径，不能按名称判断死代码。**
`work_items/task_lease_lifecycle.ts::executeTaskLeaseLifecycle` 经
`canonical_task_lease_lifecycle.ts` 路由 canonical 操作，同时保留 source-backed
release、fence-close、历史回执恢复。Fresh source-backed 写入及独立验证操作已拒绝。
Canonical coordination executor 与旧 adapter 已共享生命周期裁决。

- **目标与删除：** 用 shared-authority、TS 退役盘点将剩余正常调用方迁到 canonical
  事务，再删过时 writer 路由、不可达 mutation 与 wrapper。旧格式解析隔离在所需
  迁移/恢复边界；格式变化先备份迁移。不能仅因名字含 `legacy` 就删活跃 fence 或
  receipt reader。
- **决定性验收：** 枚举 import、dispatch registration、wire caller、保留 journal/
  receipt。在临时 runtime 演练迁移/恢复，包括 release、fence-close、stale owner、
  丢响应 replay、迁移后新写入。按既有 gate 验证受影响受支持的 File/SQLite、真实
  PostgreSQL 事务变化。Markdown 保留为可读投影，不是第二真相，也不是本任务待删文件。
- **依赖 / 回退：** 具名退役/支持条件和所属 shared-authority 检查点。活跃恢复保留
  到替代或显式支持窗口关闭；本机无样本不证明全局不存在。此跨 RFC 工作不是 controller
  启动前置。

## C9.2 — 随实现精简契约测试

- **来源：** 上述 Codex operation、DSH entry/SDK/connection、managed-step、journal、
  settlement 测试集合。
- **目标与删除：** 共享有用的既有 setup 和不变量案例，保留薄真实 adapter 及独有
  集成/负例。映射断言后才删除重复 fixture builder、完整流程测试。不新建通用测试
  框架或整份输出 snapshot 配额。
- **验收 / 回退：** oracle 来自独立语义，保留 native acceptance、丢响应、权限竞态
  覆盖，以及能发现保护被破坏的代表性断言。测试函数变少本身不是成功。测试与所属
  生产替换一同修改；保留每个 Host 的失败，不能通过平均掩盖。

## 已共享或刻意不同：不要制造重复工作

| 源码事实 | 处理 |
| --- | --- |
| `decision_capture.py`、context-delivery owner、#7430/#7457 消费者 | 复用既有 capture/full/delta；只剩已合格 C1/C2 调用方替换。 |
| `benchmark/runtime/sessions.py::BenchmarkSessionWake` | 已是薄 `CodexAgentSession` adapter，没有第二套 benchmark session owner 要抽取。 |
| `recovery.py`、TS journal inspection、`execute_turn_driver_settlement` | 已有共享 owner，不是缺框架。C4 只处理剩余调用方/决策重复。 |
| `coordination/task_lease_lifecycle.ts`、`work_items/task_lease_lifecycle_decision.ts` | 事务执行与共享纯裁决分工；相似名称不足以要求合并。 |
| 根 `execution_profile.py`、Turn `execution_profile.py`、manager `machine_profile.py` | 工作粒度、provider/model 选择、权限是不同维度。Chat 已复用 managed profile，不能按文件名合并或新增重复默认值。 |
| App conversation claim、native session binding、Turn journal、外层 wake state | Authority、生命周期、原子性不同。通过精确身份/回执关联，不合成通用 status 或新数据库。 |

每个实现报告采用/延后/拒绝的候选、实际退役的调用方/规则、剩余受支持恢复，以及下一项
代表性修改的局部性。保留 RFC 原有完整成本/成果及默认采用验收。本源码核对没有运行
上述 runtime suites，不证明净删代码、等价性或性能收益。
