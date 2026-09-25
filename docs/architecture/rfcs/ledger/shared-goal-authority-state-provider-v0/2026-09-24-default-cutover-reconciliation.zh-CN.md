# 默认切换：按实现证据重算交付边界

- 核对基线：2026-09-25 `main` 的 `41ba6f4d9`；开放 PR 状态是快照，不是合入承诺。
- 归属：总目标 #4574 R5/G2；shared authority L2–L9/D1–D3；TS 迁移 T1–T4。
- 已交付 #5040：当前注册事实约束晋升，保存的模式意图完整执行，准确恢复 fence 状态。
- 当前增量：长历史 closeout 读取复用与 TS monitor 回执归一；没有完成下列迁移工作包。
- 本检查点取代此前交付记录中的剩余 PR 数量估算。

## 先纠正统计口径

此前“5–8”“6–8”“7–9”把宽泛工作包写成剩余 PR 数，部分实现合入、额外前置项
出现后又维持原估算。这些数字不是逐项核对过的 PR backlog，现撤回。代码缺口、
开放 PR、集成验收、自然时间资格和维护者晋升决定是不同单位，不能相加或机械扣减。

| 当前基线的事实 | 现在应如何处理 |
| --- | --- |
| #4870 保留 claim 的写入、#4888 reviewed cutover、#4920 drain 规划 | 已实现。验收组合 head，不再重新安排一套替代实现。 |
| #4922 完整 canonical 快照分页、#4960 SQLite runtime 准入、#4961 显示刷新恢复、#4964 共享来源摘要 | 已实现。消费者和打包客户端仍需组合验收，不等于还缺一个全新的分页/恢复实现。 |
| #4967 TS 完整来源组装、#4968 原生 outbox 交付/恢复 | 已实现。大型来源传输亦已通过 #5013 合入；不能再称为 capture 未做。 |
| #5003 event-owned completion 原子提交 | 已合入。解决整批发布/重试，不负责 event writer 与 shadow capture 的绑定。 |
| #4994 带 lease 的显式 Agent 交接、#4995 Monitor 命令 proof、#4991 拒绝 poll 后释放预约、#4992 延期且绑定 receipt 的 Turn | 已合入。组合现有实现盘点 caller，不能再开一个 caller 重构 PR 重做它们。 |
| #4931 SQLite retained proof 编码、contributor #4224 | 优化 PR 开放，D2 资格未闭合。提速不等于容量、恢复和 soak 验收通过。 |
| #4915 默认 `.loopx` 目录 | 独立的配置迁移，不会选择 File/SQLite authority。 |

相关在途实现中现在只剩 #4931 的 SQLite 优化；#4915 是独立目录迁移。
#5011/#5012/#5013/#5014/#5016 亦已合入，继续复用其事务、完整来源与来源见证。
#4224 最新正式 1 MiB 报告仍有两项失败（receipt p95 269.03 ms / 50 ms；
scan 100 p95 801.81 ms / 250 ms），#4931 尚未提供精确 head 的正式复测。
十日 soak 到了计划结束日期，不等于已有通过结果。

## 三个明确的后续代码边界

本次补的是整合后的真实晋升准入缺口：旧 registry 快照可初始化 shadow，以及保存
后的模式转换参数被丢失。它是迁移闭环的缺陷修复，不是新的存储引擎，也不能据此
将下表第三方资格门或整个迁移包标成完成。

| 拟议 PR | 可观察结果与 owner | 退出条件 |
| --- | --- | --- |
| 1. 外部动作执行区间保护 | lease/effect owner 将执行身份验证覆盖到实际外部动作、接管、超时、退出及不确定完成。复用已合入 #4994/#4995。 | 过期 executor 不能继续执行/结算；真实执行器及 receipt 恢复矩阵通过。执行前查一次 proof 不够。 |
| 2. 事件 writer 绑定与整 Goal 迁移/回退闭环 | 将 event writer 锁和原子发布接入现有 outbox；组合 Markdown/event/lease writer、drain、saved cutover、消费者和 fenced export/rollback，删除被 TS 替代的 Python 决策。 | 复用 #5003，绑定通过前保留 `event_log_writer_not_bound`；闭合 D1、命令清单与 D3 cohort。单个无 event overlay 的 Goal 晋升不证明本项。 |
| 3. 默认入口与有界 Python 退役 | 新 Goal、settings、安装及 packaged frontend/Lark/CLI 一致选择合格 profile；存量有显式迁移与停用流程。 | 1/2 及适用 D1–D3 通过，验证用户入口，删除最后 caller 已转走的业务 writer；保留 renderer、host IO、合法导入导出。 |

**计划是三个可命名的后续实现 PR，加已有 #4931 和未闭合证据；不是保证总计四个
PR 即可切换。** 若验收发现新缺陷，记录具体缺陷与修复 PR，不能重新报一个不变
的“5–8”。File-only 有界 opt-in、SQLite 合格默认、全部存量迁移分别验收。

D2 的容量、crash/restore/upgrade/runtime 覆盖和**至少十天自然经过时间的 soak**，
是精确 SQLite profile 的证据门，不预设为一个或两个 PR；#4224 继续拥有这项工作。
D3 集成和经 owner 批准的 cohort 切换也不自动产生新 PR。这些缺项未闭合前，不给
固定完成日期或精确总 PR 数。File-only 有界切换、合格的 SQLite 默认、所有存量
Goal 迁移是不同验收范围，不能互相证明。

PostgreSQL 复用 typed command 和 AuthorityStore，部署 transport、认证/tenant
策略、restore identity、运维和 capacity 资格仍是独立中期路线。本地默认不等待
PostgreSQL 部署，conformance 通过也不等于生产服务已合格。

## 长历史收尾检查：本次修复与剩余边界

真实长期运行暴露了 `quota.prior_host_turn_closeout.preflight` 的 5 秒超时；
后续只读检查和同 Turn 重试恢复。历史已经在单次请求内建索引，不能把该优化当作
未做。本次去除跨请求重复 JSON 解码：每次仍读取并 SHA-256 核验完整既有前缀，
只复用字节相同且以换行结尾的解析结果，追加只解析新行。截断、同长度改写、替换、
损坏、未完成尾行均重新验证；旧 Turn 冲突仍阻止推进。缓存最多保留四份日志、
128 MiB 原始输入对应的解析前缀，超限回到普通读取。它不是持久索引或新 authority，
不改变日志格式；原始字节预算不等于 JS heap 的硬上限。

冷解析按数据批次让出事件循环，避免长历史独占共享 runtime。仍需读取全部字节，
因此不宣称任意历史长度恒定耗时，也不替代 retention、D2 容量/恢复资格。5 秒预算
保持不变，原事故的瞬时进程/机器调度原因未能稳定重现；回归证明的是重复工作降低
以及长历史/并发下的可用余量，不声称消灭所有环境超时。

Monitor 的精确已提交回执复用 TS settlement 的同一个查询规则；Python 删除第二遍
run log 扫描，只适配当前 Todo 事实。后来的未提交观察不再遮住较早的精确提交证据；
跨 Goal/Agent/Turn/Todo 或错误 effect 仍不能结算。只读 preflight 丢失响应报告
`closeout_query_unavailable`，不会建议寻找不存在的 preflight 写回执；查询失败依旧
阻止推断准入，不自动重试 mutation 或重启共享进程。

这是 R1/R5/S7 的实测阻塞修复与有界 Python 退役，不是上表第 2 项整体完成。
三个后续实现边界和 #4931/D2 的独立证据门不变。CLI/heartbeat 收益来自原有配额
入口；没有新增设置，frontend/Lark 也无需各维护一套策略。

## 已交付 #5013：完整来源传输与预算决定

在 #5013 之前，`test_canonical_snapshot_integration` 曾在 provider 准入之前失败：完整
来源投影超过 2 MiB request 上限。canonical 读取分页已实现，但 source capture
及管理命令仍传完整投影。裁剪来源记录会破坏 digest/parity；扩大通用 RPC 上限会
影响所有方法。

协议名称和字节上限由既有 coordination 合同统一生成给 Python/TS。Python 文件交换
留在现有来源投影适配器，不新增独立维护的同名 Python/TS 模块对。
仅携带来源的 handler 接受本机私有文件 envelope。Python 写临时 request；TS 核验
method、字节数、SHA-256、普通文件身份和私有目录，然后调用原有 handler。TS 排他
创建结果文件并返回紧凑的绑定回执；Python 校验结果字节，在成功和失败时均清理临时
目录。inline 调用继续兼容。这是临时传输，不是第二套 authority store 或持久业务 receipt。

RPC 保持 2 MiB。**新增 artifact 对单次 request/result 分别限制为 16 MiB**：
这是单独的明确容量边界，不是无限流式，也不保证任意 Goal 均可容纳。超大输入在执行前
拒绝；结果交付失败可能发生在业务提交之后，调用方必须按原 operation identity 恢复，
不能把 RPC 失败当成未提交。本次不添加自动 mutation 重试。内存仍包含完整解析对象，
不解决任意大的 provider 历史或容量资格。

16 MiB 容纳多 MiB 完整来源 fixture 及管理请求中的重复表示，同时限制分配规模。
同机、同小型来源、32 对交错 warm 调用：inline/artifact 中位数为 9.15/11.62 ms，
p95 为 11.60/15.93 ms。本次为完整来源调用接受该实测本地 IO 成本，不宣称全局延迟
结论。后续调整大小仍需实测 workload 和既有预算审查。

验证使用真实 File/SQLite 及一次性 PostgreSQL 16 server。大型 CLI 回归仅对合成隔离
Goal 进行资格验证和晋升。本机活跃来源的一次演练检测到并发变化，已丢弃证据；被接受
的真实来源演练先按 capture witness 验证隔离副本一致性，再仅在副本执行变更。私有
原文、标识和原始输出不进入公开产物，没有晋升活跃 Goal。

无需新增前端设置或改变 API 形状：既有 CLI/Python 管理适配器仍调用同一领域 handler、
返回同一结果。公开变化是完整来源不再仅因越过 RPC envelope 而失败。默认配置、权限、
source freshness、event writer hold 和 provider 晋升标准保持原有语义。

相邻 runtime 修复处理客户端未读完超大响应就断连时的 socket 错误，避免一个断连
导致共享 runtime 退出。回归验证后续分页请求仍使用同一进程；不取消或重试业务操作。
