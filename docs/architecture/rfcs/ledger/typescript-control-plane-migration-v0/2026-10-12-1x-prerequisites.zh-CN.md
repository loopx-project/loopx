# 历史 1.x 前置 checkpoint（截至 2026-10-12）

从 [TypeScript 迁移 RFC](../../typescript-control-plane-migration-v0.zh-CN.md) 的“历史 1.x 前置 checkpoint（截至 2026-10-12）”小节迁出；迁移日期 2026-10-16，RFC 基线 `b2aca67d76af45e554c620818a982a81f8d2d52b`。保留当时证据与未验收边界；英文源记录 `38cb407ed`，中文源记录 `06b6caa07`，均不是当前候选的再次验收。

[English](2026-10-12-1x-prerequisites.md)。

以下记录解释具名源码上的前置条件与验证。“writer 仍可达”及“下一次截止”
描述当时的 1.x 基线，不是上方候选仍未实现的工作。

按 main `06b6caa07` 复核，#5413/#5466/#5283，以及创建、Host 停止、UI 历史
相关 owner #5500/#5805/#5308/#5398 均已合并。复用各自已交付的边界，不重开
10 月 2 日的合并队列。
[当前验证、迁移与删除计划](../shared-goal-authority-state-provider-v0/2026-09-28-retirement-cadence.zh-CN.md#当前收尾验证迁移与删除2026-10-02)
现在优先安装态可回退验证、相关消费者采用、策略迁移及最后调用方删除，并分别
记录已合并源码和仍缺的证据。

#5500/#5805 已实现 canonical 新 Goal 创建。既有机器设置与
`coordination/local_authority_defaults.ts` 为未配置的新 Goal 选择 canonical
SQLite／`hard_lease`；CLI bootstrap 和 App 创建共享 typed 初始化及原操作恢复
owner。显式 v0 设置和 v1 `canonical_creation=false` 保留晋升后目标行为；存量
Goal 保持已记录的选择。见[配置及关闭契约](../../../../reference/local-authority-provider-selection.md#new-goal-authority-machine-setting)。
这是源码候选，不认证安装采用、发布默认、完整共享 Goal 意图或 D2/D3；不重建
创建／默认值 owner。

canonical Todo 入口隔离（T4）：五种 mutation facade 统一进入既有
`todos/mutation_api.py`；历史导入改为惰性，未迁移 writer 隔离在
`todos/legacy_mutation.py`。安装态 File/SQLite 检查可物理移除整个 writer，验证
生命周期及结算恢复。冷导入检查进一步组合审核导入、新租约下的更新／新增写入、
原操作恢复，并保持整个 writer 不存在；显示源丢失或 provider 中断不能替换
当前 authority 或抹去后续写入。原导入 cursor 仍是历史回执，当前 provider
revision 保持。这为最后调用方删除建立边界，不宣称代码已退役：bootstrap、
handoff、team-plan 和 Monitor 仍有受支持的旧源调用；capture/outbox 及历史恢复
各等自己的出口。见[调用方及回滚清单](../../../../reference/canonical-todo-completion-update.md#canonical-mutation-entrypoint-isolation)。
生产 Todo 消费者直接使用具体 owner：CLI 注册／操作、quota 与规划、
能力写回、旧源租约读取及 Lark 源投影直接导入相同的 mutation、完整读取、路径或
Markdown 显示 owner，不新增 wrapper、决策或 RPC。历史公开导入及其受支持的旧源
IO 继续保留。隔离源码／正常 wheel 同时移除 facade、整个旧 Todo writer 和四个
shadow producer，验证真实 File/SQLite CLI 生命周期、恰好一次结算、Monitor 重放
及 App 冷导入／操作／重启；缺 provider／租约及源／备份变化须拒绝。Lark dry-run
与 Explore 重放只验证本地消费者，不计 live 外部采用。
此前调用方盘点后，[#5280](https://github.com/loopx-project/loopx/pull/5280) 新增的
fresh research execution 又在函数内导入历史 facade；现直接导入既有
`todos/list_readback.py`，保持相同 typed 当前 Todo 校验以及先于读取的历史重放。
同一 facade 存在／缺席、源码／正常 wheel、File／SQLite 矩阵补 fresh 执行、
actor／可运行状态／输入拒绝、精确 lineage 读回，以及当前输入／Todo 改变后的
原观察重放。完整生产包 import 守卫解析相对导入与 alias，动态加载仍依赖真实
隔离路径验证。新能力进入时持续守卫，不把历史库存当永久出口。最后受支持的
旧源 writer、历史恢复、安装 cohort 与发布验收各自保持开放。

保留的 import-compatible field writer 在既有 typed field plan 中统一等待条件
归一化及矛盾 clear 拒绝；保持 Python 空白输入省略和锁内 IO，只退役重复的
normalization crossing，不删除仍受支持的 writer 或迁移 reader。

剩余 legacy writer 和 bootstrap 的 capture 适配器，在 runtime shadow 关闭且没有
活跃持久 binding 时，跳过 rollout 历史读取和 Todo projector 构建。原源身份仍须
解析；已有活跃 binding 即使配置关闭，也继续要求持久 prepare。普通旧源写入因此
省去可选捕获工作，输出、历史保留及 outbox 恢复保持原契约。Python 保留这层
Host IO 适配，直到最后受支持的源 writer 被替换；本批不代表整个 writer 退役或
默认 provider 资格通过。
未迁移 lease acquire 现在仅在 typed 结果带有 capture 请求时加载 capture 适配器。
真实 CLI 与正常 wheel 检查覆盖领取／重放、陈旧续约拒绝、续约、转交及释放：没有
启用 capture 或活跃 binding 时，可物理移除四个旧 shadow producer 模块。已有活跃
binding 在移除配置后仍完成 capture。这只退役默认关闭时的依赖，不删除仍受支持的
源 writer，也不结束待处理 capture／历史恢复窗口。

handoff／continuation 显示决策已归入既有 TypeScript Todo context owner。summary
在同一个 batch 复用已归一化的身份、claim、exclusion 和任务类型事实；index 在单个
原生 batch 保留先压缩源、再标注、最后合并审计事件的顺序。planning 与 quota
收集器整组投影已选记录。过大的显示上下文按字节预算分批读取，计数、依赖决策与
闭合仍由完整源 owner 处理。超大单条历史记录复用既有私有快照传输，完整文本
先进入凭证检查，再截断显示；不另加单条 frame 上限。Python facade 保留共享 metadata codec 和历史
元组／SHA-1 身份编码，没有 Python 决策回退。nested-empty 优先级、Unicode 空白／
codepoint 上限和截断前凭证拒绝都是需要保留的兼容义务。File／SQLite CLI 与正常
wheel 的恢复验收要求：Markdown 显示缺失或选定 provider 不可用时，完整源与
provider revision 仍被保留。这项读模型退役不等于受支持的 mutation／Host 调用方
已删除，也不认证已发布的 SQLite 默认值。

公开 Todo 创建现在以一次 typed creation-scope 调用组合 scope、受支持的等待条件和
有界 Monitor metadata。旧 add writer 复用同次准备结果，独立 Markdown codec 与后继
创建仍自行校验；priority 和 validation declaration 保留原错误顺序，canonical 事务
仍复核 registry、claim、lease 和 CAS。本批只退出重复创建编排，不删除仍有调用方的
Markdown writer、bootstrap、Host IO 或历史恢复路径；发布默认与整项退役验收保持独立。

canonical team-plan 只在受支持的未迁移分支加载 capture 和 Markdown 编辑依赖。
真实 File/SQLite Chat 及正常 wheel 的物理删源检查覆盖确认整批提交、后续写入后
重放、provider 恢复、投影恢复和导入前历史回执。既有 TS 事务、源锁和投影 owner
保持；旧批量 IO 的故障注入入口为当前消费方保留惰性加载。这是调用隔离出口，
不等于删除活跃旧 writer，或完成 receiver adoption、Host 执行、PostgreSQL／D2
资格。见[合同及保留义务](../../../../reference/canonical-todo-completion-update.md#team-plan-source-writer-isolation)。

公开 CLI 的 shadow 配置归入 `coordination/configuration.py`，完整源捕获归入
`coordination/authority_source_capture.py`；冷导入、源检查和历史 promotion 恢复
复用保留 owner，只有显式开启的 shadow 动作加载旧 producer。隔离源码及正常
wheel 的副本中物理删除四个 shadow producer 后，真实 CLI 仍覆盖新 Goal 默认
SQLite、租约工作、一次结算及可回退出口，以及冷 File／SQLite 导入和原操作重放。
旧模块为现有消费者保留 API 重导出；启用的 capture、outbox 和旧命令仍受支持。
精确源身份、归档依赖、安全 lease 读取和后续写入恢复均须保留。本批移除提前导入
阻塞；删除活跃 producer 仍须其最后调用方退出。App／Lark 合同、provider 事务、
cohort 采用及冻结 D2 证据保持独立。

Monitor router 同样隔离活跃旧源 writer；历史回执 codec／replay 共用 scheduler
只读 IO owner，原操作身份和决策继续归既有 TS batch。File/SQLite CLI 与正常
wheel 删源检查须保留后来写入及原 successor；晋升后重放保持旧 outbox 静止，
未晋升的 markerless batch 恢复保留原有界 drain。业务回执重放不保证 shadow
立即追平：锁延期或 proof 预算耗尽时保留待处理证据，由独立的
`authority-shadow drain` 恢复。恢复须保持原 source bytes、successor ID 和 quota
effect 恰好一次；不能为追平而重放 writer 或扩大 inline 上限。
本批只移除 canonical 导入依赖，
不删除受支持的旧 writer；缺席臂复用上述 CLI 启动隔离，同时移除四个 shadow
producer module。
见[保留义务及回滚](../../../../reference/canonical-todo-completion-update.md#monitor-source-writer-isolation)。

shadow 诊断现在有独立只读边界：历史 outbox 磁盘 codec 位于
`local_authority_shadow_history.py`，status／candidate read transport 位于
`local_authority_shadow_readback.py`。公开 status 和启用的
`coordination-shadow inspect`／`qualify`／`read-candidate` 直接调用保留 owner，
不加载源 producer。源码和正常 wheel 物理删除四个 producer 后，完整读回保持
匹配／拒绝、默认关闭、源漂移、store 缺失／损坏及恢复结果；历史 import 保留
同一函数 alias，bootstrap／rollback／promotion 的活跃 adapter 继续受支持。
既有 TS qualifier 保留 provider 读取失败，避免把损坏 journal 误称尚未
bootstrap；正常／缺失状态保留原结果。这是明确的失败诊断修复，无新 RPC 或
迁移权限；drain
仍走现有 orchestration。原 entry 身份、损坏证据、cursor 残留和缺失 store 行为
保持。Python 保留历史文件解码及展示 IO；TS 保留历史证明、投递与清理权限。
旧 producer 的已有导入为支持调用方保留 alias，不复制实现。源码及正常 wheel
的 File/SQLite 接收端检查，在 producer 存在／缺席两臂比较完整 status 响应和
原文件字节。这是历史 reader 的退役前置，不代表 writer 删除、outbox 处置、
安装 cohort 采用或发布默认资格。
见[操作读回与回滚](../../../../reference/authority-observation-retirement.md#retained-readback-without-source-producers)。

存量 Goal 迁移、两策略退役和格式升级仍各有独立回执及出口；原回执恢复不能成为
保留 legacy 活跃策略的理由，必要迁移 reader 保留。

导入后 canonical bootstrap 重连复用既有 typed authority reader，保留当前
Todo、租约及原导入回执，避开 Markdown writer。File／SQLite 真实 CLI 覆盖旧
v0 偏好指向另一 provider、显示缺失、选定权威丢失、force／改向拒绝，以及旧
producer 不存在时的后续写入重放。Python 保留 bootstrap registry／Host IO，
不新增决策 owner 或创建回执。本批关闭重连依赖，未关闭未晋升 writer、活跃
outbox、Host 或发布默认验收。

有界 cohort 在安装恢复和相关执行控制通过后可开始，不代表发布默认值或正式十天
D2 已通过；冻结的失败／缺项保持可见。T4 随实现删除已证明重复的 owner，不等 R6
或所有 Python 消失。本节替代陈旧的当前数量估算，不覆盖历史执行证据。

公共扫描隐私检查点：保留的 Python 文件系统／正则 owner 在提交 reader 前分类
canonical 输入，分类独立于显示路径。重叠扫描根不能移除私有分类；报告统一使用
最宽的请求根。窄范围显式文件／目录保留私有祖先信息。经 Git 核验的 worktree
即使位于私有父目录下，也保留自己的命名空间；仅名为 `.git` 的目录不构成例外。
worktree 外、私有状态目录下的显式输入仍须排除。tracked 文件策略、每次重新
解析的符号链接及既有错误规则均留在这个 owner，不新增第二份控制面决策源或
配置项。这次安全修复不认证扫描耗时、SQLite 发布默认值或整个 T4 退役。CLI
检查和 quota 准备复用此规则；App status 继续明确报告仓库扫描为 deferred。
