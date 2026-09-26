# 本地默认切换：恢复审计与剩余交付范围

- 核对基线：2026-09-27 `157ab7b11`，加本次交付。
- 归属：总目标 #4574 R5/G2；shared authority D2/D3；TS T3/T4。
- 取代[九月二十四日清单](2026-09-24-default-cutover-reconciliation.zh-CN.md)的
  **当前数量口径**，不覆盖历史证据。

## 已交付的部分

完整来源传输/组装、事务 outbox 捕获、reviewed promotion、canonical 分页、File
checkpoint/delta 格式升级和有界 Python 原型退役已在 main。尤其 #5013、#5063、
#5102、#5105 不能重复安排。两个 Goal 已晋升，不代表所有保留来源、消费者、回退
和执行生命周期都通过验收。

此前三行计划把“迁移/回退”合得太宽，不能据此承诺三个可审查 PR。本次将恢复前置
与正式激活分开，依据是实际缺陷：restore 验证归档后重新打开可变路径，被替换的
内容可能先写入隔离目标，直到最终摘要不符才失败。此外缺少独立只读 CLI，证明恢复
目标的完整历史及回执查询仍正确。这是恢复缺口，不是尚未实现事件捕获。

## 从本次开始的四个交付范围

| 交付 | 可观察结果及剩余边界 |
| --- | --- |
| **1. 本次：审核输入恢复与独立历史审计** | 恢复始终消费私有、已验证的输入副本。File/SQLite 往返保留逐笔逻辑状态和原回执。审计独立核对历史与回执查询，明确 exact 与 retained-prefix 两种语义。检查点提交后进程被杀可续传，不重复执行已提交的命令。不修改线上 selector/fence。 |
| **2. 外部执行区间保护** | 既有 lease owner 覆盖真实 Host 执行、续约、权限丢失、取消及不确定副作用。必须测试旧 executor 尚在运行时的过期/接管；结束后拒绝写回不够。不可取消的 attached Host 需明确支持边界。 |
| **3. 整 Goal 激活及回退集成** | 对齐 #5054 的保留来源清单，联合验证来源 drain、保存的 reviewed cutover、全部保留命令消费者及 fenced recovery/rollback。通过明确转换绑定恢复副本，不复活旧租约，不覆盖后来写入。仅删除 caller 已迁走的 Python 决策。 |
| **4. 默认入口与最后一批有界退役** | 新建 Goal、settings、安装及打包 frontend/Lark/CLI 一致使用合格本地 profile；存量 Goal 有明确迁移及停用/恢复路径。caller 清单与回退约束通过后，删除最后的旧业务 writer，保留渲染及 Host IO。 |

这是**包含本次在内四个规划新 PR，本次交付后剩三个**；不保证验收不会再发现需要
修复的缺陷。原来的三个“架构工作包”不是倒计时 PR 数。本次关闭第 2 包中的一个
具名恢复切片，没有把整个第 2 包标为完成。后续必须指出哪行真正交付，不能再重复
一个不变的“5–8”。

已有 PR 单列：#5054 退役旧 Todo event 路径并隔离 supervisor 日志，#4931 优化
SQLite retained proof 读取。二者在本次核对时仍开放，不重复实现，也不为将退役的
来源新增捕获 writer。#4915 属于目录布局，不能算 authority 默认切换。后续 SQLite 工作复用 #4224 的资格合同及证据，与 #4931 的重叠实现协调。

## 证据门不是 PR 配额

#4224 最新正式 1 MiB 报告仍有 receipt p95 269.03 ms / 50 ms 和 scan-100 p95
801.81 ms / 250 ms 两项失败。本次核对未发现 #4931 精确 head 的正式复测或完整
D2 通过报告。计划 soak 结束日期不等于实测通过。domain workload、steady-state
RSS、大历史恢复、consumer lag、升级/回退及平台覆盖是各自独立的项目。本次小型
检查点/进程崩溃矩阵不证明正式 100k/300k 负载，也不替代十天自然时间 soak。

D1 消费者一致性、D3 经审核的 cohort 激活及维护者默认值选择同样需要实证。
File opt-in、合格 SQLite 默认和全部存量 Goal 迁移是不同主张；目前不能诚实地给出
无条件的 PR 总数或完成日期。

PostgreSQL 复用同一归档审计和逻辑事务，本次运行真实隔离 store 集成；认证传输、
tenant 策略、恢复 incarnation、failover/pool 及容量仍属于独立中期路线。本地默认
不必等待 PostgreSQL 服务部署。

## 本次交付合同

现有 authority-archive CLI 拥有此管理旅程；TS 负责格式验证、副本生命周期、历史
比对、续传判断及 provider 回读，Python 仅适配 CLI 输入输出。不新增 capability、
provider 注册、磁盘格式或 settings。frontend/Lark 业务读取仍走同一 provider 接口，
无需新增配置编辑器。

负例覆盖输入替换/损坏、最终状态相同但旧历史不同、回执查询丢失/不可用、分页不
完整、incarnation 改变及并发追加。File/SQLite 进程崩溃测试与真实 PostgreSQL
跨 provider 测试使用实际存储。本机来源演练使用独立且核对字节的快照，不修改活跃
Goal。公开材料排除私有 Goal 内容及原始日志。

[操作、语义变化及限制](../../../../reference/file-authority-state-log.md#provider-migration-and-recovery)。

隔离真实快照演练暴露了恢复 RPC 的 300 秒超时：逐笔回执回读反复重放同一个 SQLite
检查点窗口。本次在现有 provider 接口增加有界批量查询，标量查询委托给同一个证明
实现；恢复和审计共享分页比对，遇到不确定写入立即回读。此处减少重复重放，与
#4931 的证明编码优化互补；不提高 RPC 预算，也不替代 D2 的独立性能验收。

本次交付验证：336 项原生归档/SQLite 一致性及崩溃检查、4 项 CLI 检查、真实隔离
PostgreSQL 16 的 4 项跨 provider 检查全部通过，这些套件没有跳过项。129 笔历史的
独立真实来源快照通过 File、SQLite 恢复及独立审计；此前超时的 SQLite 目标也成功
恢复，没有重发已提交操作。这些结果不表示已经完成活跃 Goal 切换。
