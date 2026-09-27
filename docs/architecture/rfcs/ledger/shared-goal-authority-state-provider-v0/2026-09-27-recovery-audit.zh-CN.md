# 本地默认切换：恢复审计与剩余交付范围

- 最新核对基线：`76ff7c73c`；下方历史段落保留其当时基线。
- 当前数量以末尾“本地 provider 切换”表为准，历史规划不是剩余 PR 倒计时。
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

## 当时的恢复交付拆分（历史记录）

| 交付 | 可观察结果及剩余边界 |
| --- | --- |
| **1. 本次：审核输入恢复与独立历史审计** | 恢复始终消费私有、已验证的输入副本。File/SQLite 往返保留逐笔逻辑状态和原回执。审计独立核对历史与回执查询，明确 exact 与 retained-prefix 两种语义。检查点提交后进程被杀可续传，不重复执行已提交的命令。不修改线上 selector/fence。 |
| **2. 外部执行区间保护** | 既有 lease owner 覆盖真实 Host 执行、续约、权限丢失、取消及不确定副作用。必须测试旧 executor 尚在运行时的过期/接管；结束后拒绝写回不够。不可取消的 attached Host 需明确支持边界。 |
| **3. 整 Goal 激活及回退集成** | 对齐 #5054 的保留来源清单，联合验证来源 drain、保存的 reviewed cutover、全部保留命令消费者及 fenced recovery/rollback。通过明确转换绑定恢复副本，不复活旧租约，不覆盖后来写入。仅删除 caller 已迁走的 Python 决策。 |
| **4. 默认入口与最后一批有界退役** | 新建 Goal、settings、安装及打包 frontend/Lark/CLI 一致使用合格本地 profile；存量 Goal 有明确迁移及停用/恢复路径。caller 清单与回退约束通过后，删除最后的旧业务 writer，保留渲染及 Host IO。 |

当时的恢复检查点规划为**包含恢复 PR 在内四个新 PR，交付后剩三个**；不保证验收不会再发现需要
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

## 当前交付清单与原生 drain（`70b3cca01`）

当前规划有 **7 个交付槽位，包含本次：4 个已有开放 PR，加 3 个交付范围**。
这不意味着还要新开 7 个 PR，也不能保证合并 7 个就足够。此前把整 Goal 整合
当成一个 PR，但恢复与执行边界尚未拆清；那是架构工作包，不能作为准确倒计时。

| 项目 | 已有工作与完成标准 |
| --- | --- |
| 1 | **#5173**：受审 File↔SQLite selector/fence 切换、备份、完整历史核对、恢复和重试。整合已有实现，不重写。 |
| 2 | **#5144**：受管 Host 执行生命周期与租约监督；attached Host 仍需明确取消能力边界。 |
| 3 | **#5054**：退役旧 Todo 事件投影、回填与 completion 分支，独立实验 supervisor 日志。 |
| 4 | **#4931**：SQLite 历史证明编码与读取成本；需正式 D2 工作负载复验，优化代码不等于资格通过。 |
| 5 | **本次**：完整有界 source-outbox drain 归 TS，删除 Python 的顺序、证明、清理编排和无人调用的逐条规划 RPC；保留持久格式、回执证明、内核锁适配。 |
| 6 | **整 Goal 整合**：接通已交付切片，验证保留消费者、中断切换/回退及切换后的新写入。本次 drain 只完成其中一个子项。 |
| 7 | **默认入口与有界 Python 退役**：创建、设置、安装、前端、Lark、CLI 一致；已有 Goal 明确迁移；真实调用者迁走后再删业务 writer。 |

#5169 的内容感知幂等是相邻工作，应避免重写，不把它悄悄加成另一个默认切换必需 PR。
D1 消费者覆盖、D2 容量与至少十天自然 soak、D3 队列与维护者晋升仍是独立证据门。
PostgreSQL 服务身份、部署和运维资格属于中期范围。

TS 统一读取来源文件见证、调用既有回执 planner 与事务 owner，在证明后重新检查
单调时钟预算，并在 M → primary marker → kernel lock 的顺序下更新游标和清理。
Python 只发送一次有界请求，不搬完整 projection/history，也不自动重试响应丢失的
批次。`shadow_drain_outcome_unknown` 表示必须由下次显式 drain 读取回执恢复，
不能解释为“没有提交”。未启用且不存在捕获状态的 Goal 仍不产生 drain RPC。

真正杀进程的测试发现共享锁缺陷：零等待获取已经回收死进程锁，却立即报超时。
现在仅在确认回收后立即再尝试一次；活进程持锁时仍马上退出。这是共享锁修复，
没有为 drain 增加 sleep 或放宽超时。两套崩溃 harness 也共用一个调度 fixture，
在持久化边界杀掉并回收真正执行决策的 TS 进程。

runtime shadow 仍是 **File 候选存储**，不是正式 authority，也没有新增 SQLite
shadow provider。SQLite 是 canonical 晋升和归档恢复目标。本次不切换任何活跃
Goal、provider selector 或 registry。原有 CLI 与 writer 内联 drain 采用同一 owner；
没有前端/Lark 配置合同变化。

本次验证复用混合 production-scale Todo fixture；真实 CLI 的 SIGKILL、文件权限、
游标篡改、File/SQLite 受审晋升覆盖持久边界；共享存储在隔离 PostgreSQL 16 上回归。
授权隔离快照提供 1,101 个完整 Todo，三笔明确标记的合成来源写入形成新的四事务
候选历史。原始 Todo JSON 经 drain 及 SQLite/File 归档恢复仍完整相等；这不是
重放该快照的旧事务历史，也不是活跃 Goal 迁移。

本机三个事务的三次测量中位数由基线 1.34 秒降至本次 0.41 秒，facade RPC 从
11 次降为 1 次。内核锁进程按需创建、批次内复用，各临界区之间释放锁。这是小
负载热运行时测量，不是 D2 p95/容量结论。CLI 输出预算检查在 base/head 都因
拥挤 Turn JSON 为 14,514 字符、超过 14,500 上限而失败；未提高预算，合并资格
保留此失败，不能报告所有检查为绿。

## 共享运行时延迟核对（`96a3b90f4`）

上文恢复交付 #5140，以及后续共享运行时延迟修复，都已进入当前核对的 main。
下文保留当时的验证事实，不再把它们算作开放工作，也不将其当作 D2 资格。

本次不退役额外 Python owner，也不关闭 D2。隔离固定 File 快照复现了 9.9–10.5 秒
的冷历史校验，期间 ping 在原 10 秒预算内超时。热缓存掩盖问题，交替读取 Goal 又
会淘汰唯一的验证缓存。隔离 CPU 采样中，约 56% 样本落在为键比较分配码点数组。
改用不分配数组的同序比较；File 在完整事务的校验之间让出事件循环，并按路径、
store identity、精确字节摘要合并进行中的相同校验。尾部损坏仍拒绝返回早期回执，
失败证明不会成为缓存。不修改格式、revision 算法、超时或 selector。

相同快照冷读约 2.9 秒，并发轻请求约 18–52 毫秒。这是本机观察，不是正式容量、
p95 或跨平台资格。单笔巨大事务、JSON 解析、其他同步 handler 和 SQLite 重放仍
可能占用事件循环；本次没有实现通用 worker 隔离。公共比较器惠及各 provider，
让步及进行中证明的生命周期归 File；#4931 的 digest window 仍是独立优化。
回归使用私有真实 server 和既有混合 Todo/lease/decision fixture，不改生产 locator
及活跃 Goal，不发布原始证据。

## 本地 provider 切换（`76ff7c73c`）

#5140 恢复审计、#5156 共享运行时延迟修复已合入，不能重复列为未完成。
本次交付“已晋升 canonical Goal 的 File ↔ SQLite 审核切换”：备份并核对完整历史与
原回执，绑定 source revision/fence 和 target identity，串行发布 selector，支持进程
中断后的续传，以及携带最新历史的反向迁移。未结算租约（包括过期 active）拦截。
此处不自动停止 Host，不迁移 Turn/spend 的独立状态，也不等于全部旧 Goal 晋升。

| 当前交付范围 | PR / 状态 | 仍需证明的结果 |
| --- | --- | --- |
| 旧 Todo events 退役与 supervisor 日志隔离 | 已有 #5054，开放 | 消费者迁走后的旧分支删除 |
| SQLite retained proof 编码 | 已有 #4931，开放 | 在 #4224 冻结负载上的正式复测，不以小型迁移耗时替代 |
| 受管 Host 执行区间保护 | 已有 #5144，开放 | 续约/取消/旧 executor 接管；attached Host 边界另行明确 |
| 整 Goal 激活与回退集成 | 本次交付其中的本地 provider 切换子项 | 旧来源 drain、全部保留消费者和外部执行状态的组合验收仍未关闭 |
| 默认入口与有界 Python 退役 | 尚未实现的后续范围 | 新 Goal、设置、安装和各入口采用合格 profile；只删除 caller 已迁走的业务 writer |

因此当前可确定的是 **3 个已有开放 PR、当前 1 个切换 PR，以及上述剩余集成/默认
入口范围**。本次没有把宽泛的“整 Goal”行直接勾完，也没有据此将总数机械减一。
只有补齐消费者清单和 D2 实测后，才能判断剩余集成可合成一个 PR，还是需按具体
失败拆分；目前不能准确承诺“再 N 个就全量切换”。容量/平台/自然时间 soak 是独立
证据门，不是编码 PR 配额。PostgreSQL 仍复用共享逻辑历史与回执审计，但本地切换
入口明确不接受 PostgreSQL，服务认证/tenant/failover 不在此处偷换为已完成。

操作与恢复边界见[审核切换](../../../../reference/file-authority-state-log.md#reviewed-filesqlite-cutover)。
