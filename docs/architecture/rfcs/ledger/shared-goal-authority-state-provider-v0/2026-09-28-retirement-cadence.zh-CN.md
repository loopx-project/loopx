# 合并后的本地权威退役节奏

- 当前计划：2026-10-02，`9b0486dc1`；历史核对基线：`ce3862e33`；采用后续核对：`71525ab90`，2026-09-28；[English](2026-09-28-retirement-cadence.md)。
- Owner：总 roadmap R3/R4/R5/R6、shared authority D1–D3、TS 迁移 T0–T4。
- 本记录替代 9 月 27 日 recovery、Host supervision 记录的**当前清单和估算**，
  不替代其历史验证结果。

## 重新核对的基线

| 已合并 | 不再计入剩余工作的内容 |
| --- | --- |
| #5054 | 旧 Todo events 投影、回填、completion 已退役；supervisor 日志已分离 |
| #5102 / #5105 | File 格式升级／备份、原生验证及 Python 原型删除 |
| #5140 / #5156 | Archive 恢复／审计与共享 runtime 读取公平性 |
| #5144 | Managed command／Codex CLI 进程监督；**不代表** attached Host 取消闭环 |
| #5173 | 已晋升且静止 Goal 的 reviewed File↔SQLite 切换 |
| #5175 | 完整 outbox drain 由 TS 拥有；Python 编排及旧逐条规划 RPC 已删除 |
| #5169 | File/SQLite 完整意图与历史证明匹配的操作重放 |
| #5170 | App 委派结果连续性；不代表全部 Turn／实例消费者完成 |
| #4931 | TS 私有状态重放降低 SQLite／archive 历史重建成本；未切默认、未完成 D2 |

采用后续核对时，#5106（collaboration GoalRef）、#5130
（session GoalRef）、#5139（App Turn 接受恢复）、#4915（本地状态路径迁移）仍开放。
复用和推进这些 owner，不重复实现；只对确实受影响的调用方建立依赖，本地默认切换
不等待无关云端或百 Agent 工作。

**File 是默认 store factory，不等于所有新旧 Goal 默认以 File 为权威。**
未晋升的 Markdown writer 仍可达。两个 Goal 迁移成功、或 File↔SQLite 传输成功，
都不能证明旧 writer 已没有消费者。本轮不再沿用“还剩 5–8 个 PR”；以下列出的是
交付和验证边界，不承诺缺陷数量或合并数量。

## 什么时候删、怎么删

| 边界 | 真实可达的代码／调用方 | 最早删除条件及保留义务 |
| --- | --- | --- |
| 重复决策／废弃内部 RPC | 逐项检查 TS owner 与 Python caller；#5175 已删 drain 编排 | 切走**最后调用方**且独立语义验证通过的同一个 PR，连同 handler、注册、helper 和仅服务旧实现的测试一起删。不新增 shadow／bridge 层。本规划未认证额外某个模块已死。 |
| 旧 Todo 写入 | `loopx/todos.py` 仍导入 `line_update.py` 和 `provider_create.py`、`provider_update.py`、`provider_terminal_lifecycle.py` | 新 Goal／升级路径选择 canonical authority，目标存量 cohort 完成迁移，未升级调用方有明确升级／恢复路线后，按调用家族删 Markdown 可写分支。保留人工叙述渲染和合格 import/export；provider 缺失不能悄悄恢复旧 writer。 |
| Shadow 捕获／drain | `runtime_shadow_writer_adapter.py`、`local_authority_shadow_outbox.py`、`runtime_shadow.py`；configure／CLI 和旧 writer 仍调用 | 最后受支持的源 writer 退出后删 producer／hook；prepared／committed outbox 已对账或明确处置前，保留迁移 owner 内的 reader／reconciler。一个本机 Goal 无积压不足以删除。 |
| Python 命令 facade | `authority_core.py`、canonical Todo adapter、`quota/monitor_poll.py` 仍有运行时调用方 | 完整 native 入口接管后逐组删除，包括私有 validator／Host 效果、输出投影及错误／重试行为。纯策略进入 TS 不代表输入／IO adapter 已死；不能按语言或行数整文件删。 |
| 历史格式／回执 | File/SQLite 迁移 codec、逻辑 archive、command receipt recovery | 退役旧正常写路径，但保留受支持升级边界的显式迁移、备份恢复和原回执读取。将来删 reader 须另有格式支持决策和转换验证，不能搭业务 writer 删除顺风车。 |

实施 PR 维护一份退役清单：symbol/path、生产调用方（含动态 handler／打包）、替代
owner、持久兼容义务、正反例证据及回退方式，和不可变基线比较。零 import 搜索对
内部删除必要但不充分，不能忽略公开 CLI/import 和序列化契约。保留公共行为测试，
只删没有消费者的旧实现专属 characterization。删的是代码，不是用户状态、回执和备份。

### 已合入的 T4 切片：已无调用方的 Python lease／handoff facade

在 `e240730ec` 核对调用方后，#5395 已于 `8474c8d86` 合入，退役了下列
无生产消费者的内部跨界。原生决策和事务 owner 保留；这项删除不等待 D2 资格或
默认入口接入，也不宣称完成它们。

| 删除边界 | 最后调用方／替代 owner | 兼容与验证 |
| --- | --- | --- |
| `authority_core.py` 的 acquire／renew／transfer／release、owner eligibility、handoff transition command facade | 只剩旧 core 测试；真实 lease／handoff adapter 已直接使用完整 native 事务 | 不改持久化命令格式或公共 CLI schema。保留独立的原生 generation、重放、冲突、清理、静止规则测试，并走真实 File／SQLite 入口。 |
| `task_lease.acquire.decide`、`task_lease.lifecycle.decide`、`coordination.handoff_mode.plan` RPC 注册 | 只剩这些旧 facade／handler 测试；原生事务直接复用同一 TS 规则 | 废弃私有 RPC 明确拒绝；保留仍有 Python 调用方的 `task_lease.owner_eligibility` 和 write-scope overlap。 |
| `local_snapshot.py` 中仅供 lease 的规范化和错误投影 | 已无调用方；原生执行器拥有 lease 事实与错误 | 保留真实 Todo mutation authorization 使用的 `todo_snapshot_from_mapping`；不删 store、回执、备份或迁移 reader。 |

`authority_core.py` 仍是活跃 Todo bridge。`LeaseAction`、`LeaseModeGateCommand`
也保留：semantic-vocabulary 注册表明确将该输入契约保留到 M4 评审。本切片不通过
降低语义覆盖下限丢弃已有兼容义务。仅服务旧 facade 的测试随实现退役，公共／原生
行为测试保留。回退该切片可恢复内部跨界，无需转换数据。本机 CLI 已采用
`db3672f3c`，验证了干净源码清单、具备资格的 SQLite runtime、已知权威格式均为
当前版本及健康的 canonical 合同读回。这不证明所有已安装 Host 或 D2 已验收。

## 当前收尾：验证、迁移与删除（2026-10-02）

按 main `9b0486dc1` 和所列 PR head 重新核对。本节是 **R5 / D1–D3 / T0–T4**
的当前执行计划，替代旧 A–D 排期；历史测量仍只适用于原源码和负载。R6 单独推进。
存储格式、权威选择、所有权策略是三种不同迁移：有 SQLite 数据库，不代表新 Goal
已经默认使用 canonical authority，也不代表 `legacy` handoff 策略已经退役。

### 实际基线与合并队列

| 状态 | 已交付边界／下一步 |
| --- | --- |
| 已合并：#4931、#5251 | SQLite 重放／证明和分配优化；复用实现及匹配证据，合并不等于 D2 已验收。 |
| 已合并：#5395、#5417 | 无调用方的 Python lease／handoff 跨界、重复结算准入／恢复决策已退役。继续按最后调用方删除，不重复计账。 |
| 已合并：#5436 | 委派 Host 原租约续期；最终 Todo 验收和停止确认仍是不同边界。 |
| 复审中：[#5413](https://github.com/loopx-project/loopx/pull/5413)，`2c99505c7` | provider 晋升与带备份的策略迁移解耦；禁止新 legacy 配置，允许恢复历史操作。CLI 恢复修复的 99 项相关测试、File/SQLite 真实旧 CLI→新 CLI 演练通过，最终 head 独立复审待完成。没有自动迁移存量 legacy Goal。 |
| 待审：[#5466](https://github.com/loopx-project/loopx/pull/5466)，`60a052383` | 原租约保持到最终验收；独立评审、维护者合并后验证安装态执行路径。 |
| 待审：[#5283](https://github.com/loopx-project/loopx/pull/5283)，`73d1fe663` | 不缩减决策输入地降低 preflight 投影成本，末次 capture 显式报告 provider 不可用。作者报告固定源码下 96 次 File/SQLite 检查及完整投影等价；仍需独立复审和安装后读回。合成故障不证明历史瞬态打开失败的根因。 |
| 按实际路径建立依赖 | [#5308](https://github.com/loopx-project/loopx/pull/5308) 要证明子进程停止后才报告已结算；[#5398](https://github.com/loopx-project/loopx/pull/5398) 保留 UI 历史和 inspector 完整事实。只对纳入试用的相关消费者建依赖，不将其说成 SQLite 引擎前置，也不能发布已知损坏的用户路径。 |

当前优先收尾的是 **3 个已存在的开放 PR**，不等于再合 3 个就全部结束。
剩余实现包是 canonical 创建／默认接入、策略迁移与 legacy 策略删除、旧 writer／
捕获退役。仅当调用方归属和回退边界一致时才合并成同一个 PR。验证可能暴露具体修复，
不再制造固定“剩余 PR 数”，也不为维持这个数字重做已完成的工作。

### 有依赖顺序的交付包与出口

| 交付包／既有 owner | 要做什么、凭什么完成 | 依赖／删除机会／节奏 |
| --- | --- | --- |
| 现有 head 收尾；R3/R5 | 修完上述 3 个 PR 的 exact-head finding，处理相关失败与冲突，提交已评审 head 给维护者合并；区分已合并和已安装。 | 第一目标为 1–2 个工作日，取决于真实评审／修复结果；收尾前不另开无关优化。 |
| 安装态恢复候选；D1/D3、整 Goal 晋升任务 | 固定合并源码和 CLI/App/Effect 实际 Node/SQLite 身份；独立恢复并验证备份，用隔离真实快照及合成负例执行下表，完成 File→SQLite→新增写入→File。之后按授权逐 Goal 采用并日常回读。 | 相关 PR 合并后立即开始，有界矩阵目标 1–2 个工作日；保留兼容的恢复版本和 archive，不对活跃 Goal 注入崩溃／损坏。 |
| 有界自愿试用；D2/D3 | 安装态恢复及相关执行控制通过后，邀请不超过 20 位核心开发者。公开负载／平台范围、备份迁移关闭步骤、已知缺口、停止条件与反馈入口；观察真实日常使用和失败。 | 不必等待全部正式 D2 轴或一份新的十天证书；携带新写入回退未通过前不邀请。试用不认证发布默认值。 |
| Canonical 创建／默认接入；D3/T3 | 复用 `machine_configuration/goal_storage.py` 和 `local_authority_defaults.ts`。当前设置只选择**晋升后的目标**，返回 `promotion_performed: false`。补齐新建初始化／重试、升级、设置及打包 App/CLI/Lark 读回，已有显式 selector 保持固定。 | 有界候选可用后实现，发布默认启用仍服从下方决策；同包删除被替代的创建／选择决策。只把设置里的 file 改成 sqlite 不够。 |
| 两种所有权策略；R3/R5/T4 | 复用 #5413 的 backup/plan/migrate owner。清点旧／缺省 mode，结清适用的 claim／lease 和 Host 效果，逐个迁移获授权 Goal，再将正常运行类型及默认值收敛为 soft_claim / hard_lease。复用既有 Goal 设置入口和同一 owner，提供预览、获授权执行、结果及失败／恢复；只有 CLI 的迁移阶段标为部分交付。 | 与试用观察并行；不和 File↔SQLite 转换绑定。受支持升级路径和调用方通过后删除 legacy 执行，不能默默把 legacy 当成 soft。 |
| 旧 writer／跨界删除；T3/T4 | 最后真实调用方切到 TS owner 后验证下表，同时删除 Python 决策／私有 dispatch 和旧 Markdown 写入；清理 capture producer 前对账 outbox。在旧路径已不存在的包上验证 CLI。 | 已证明无消费者的内部删除现在就做；业务 writer 删除随对应迁移接入，不等 R6 全部完成或所有 Python 消失。每批有具体清单和回退方式。 |
| 发布默认决策；R5/D2/D3 | 对账受支持安装、当前 release 对照、代表性持续读写／恢复、资源增长和既有 soak 适用性；发布明确 profile、failed/missing、升级说明及关闭路径，显式披露默认变化。 | 不按测试／PR 数推算日期；正式十天／100k 资格保留各自证据要求。已有 File 选择继续受支持并固定；SQLite 不可用不能静默唤回旧 writer。 |

以上是工程目标，不是资格证书。先按源码和变化边界核对 #4224 的旧 soak，再决定
哪些证据需补跑；无关提交不抹掉自然时间。当前公开记录未证明已完成且适用于当前
候选的 soak 结果。

### 一份可复用的验证矩阵

每行记录候选／独立对照源码、实际 runtime、完整 fixture／历史摘要、命令、
通过／失败／未测和停止／回退结果。性能使用当前受支持 release 做对照，最初迁移前
基线保留为独立产品比较。同时隔离数据与 Effect 进程，不靠截断 metadata、历史或
决策输入赢指标。

| 边界 | 必做实验及不变量 | 复用的证据 owner |
| --- | --- | --- |
| 备份与完整数据 | 验证 SQLite 在线快照及逻辑 archive 恢复；比较完整 Todo JSON、缺省/null/false、未知 metadata、role/task class、归档依赖、验收合同／版本、claim／lease generation、原 events／receipt／cursor，以及受支持 Goal/source 状态。枚举全部持久状态家族，不能只比数量或最后 head hash。 | `test_authority_archive.py`、`authority_archive_audit.test.ts`、archive crash/restore 和迁移套件 |
| 正反向迁移 | File→SQLite，真正新增／修改／完成并重放一笔新操作，重启后导回 File；全部旧事实和**新增写入**都保留。丢响应与相同重试回原结果，同 operation ID 不同意图拒绝。 | `local_authority_migration.test.ts`、archive 与 reviewed-cutover CLI 套件 |
| 写入与所有权 | create/claim/update/complete/supersede/archive，quota 选择→refresh→spend，同 Todo 竞争、旧 revision/epoch、lease 续期／释放及适用策略迁移；一笔 commit/effect/settlement，不凭空造所有权。 | 真实 File/SQLite 命令套件；#5413/#5436/#5466；共享修改还须隔离真实 PostgreSQL |
| 中断与恢复 | durable commit／selector 发布前后进程中断、provider unavailable/busy、空间不足注入、投影卡住和 consumer 滞后；重启／重试只结算一次且后续合法工作可继续。子进程还活着不能报告已停止／已结算。 | 既有 crash/migration/process 套件；#5308 相关 Host 路径 |
| 安装态消费者 | CLI status/quota/Todo list/detail；打包 App 列表／inspector 和普通修改；纳入范围时验证 Lark。数量、metadata、新鲜度、错误／恢复反馈、原路返回与 canonical 事实一致，覆盖重启和旧标签页资源。 | 既有投影／消费者任务、打包前端 smoke；受影响处采用 #5398 |
| 成本与持续运行 | 相同数据／历史／durability／命令，分别测完整冷 CLI 和 warm store，报告 p50/p95/p99／样本数、RSS、DB/WAL／写增长、锁竞争及 consumer lag。正式 macOS 冷 CLI 失败及缺项保持可见，披露相对当前 release 的绝对值与相对变化。 | #4224、SQLite comparison/rehearsal runner、既有 performance-diagnosis capability |
| 删除证明 | 一次性 checkout 中删掉／禁用候选旧路径，跑真实入口与历史恢复；检查 import、动态 handler、打包和 fixture 最后调用方。不支持的旧输入提示迁移，不能退回 Markdown 写入。 | 实现 PR 的退役清单、独立语义和负例测试 |

有界试用中，显著用户路径退化或恢复失败阻止该路径采用；提议的微基准预算不构成
所有合并的否决权，实测权衡不能重写冻结报告。数据丢失、原回执改变、重复效果、
Goal 身份错误或 fence 破坏始终停止相关写入，保留只读证据并按 journal 恢复。
回退必须导出当前已提交状态，不能用迁移前快照盖掉后续写入。

### 迁移顺序与准确删除边界

1. 逐 Goal 清点 provider／格式、晋升状态、策略、runtime、未结 Turn/outbox/projection
   和真实 writer。已 canonical 的 SQLite Goal 做验证，不重复 promote；canonical
   File 按需做 provider 迁移；未晋升 Markdown 做完整捕获和 writer fence。
   数据库文件不等于已选中的 authority。
2. 迁移前备份并独立恢复验证；停止该 Goal 新准入，drain／结算真实在途工作，重新
   校验 source digest 和计划，再走已有 CAS／selector owner。lease 过期或进程退出
   均不能单独证明外部效果已停止。
3. 按授权逐个采用并回读，后续写入以 canonical 为准。source/provider/policy 迁移
   各有独立回执和恢复。计划无结果或丢响应按 operation ID 续接，不能手改 registry／
   selector，或把已发生效果当成新操作重做。
4. 按下表删除；以下是真实源码候选，不是在宣称每个模块现在就能整文件删除：

| 删除对象 | 替代和最早出口 | 保留／明确不删 |
| --- | --- | --- |
| 活跃所有权策略 legacy，以及缺 mode 的运行默认值 | `handoff_mode_policy.ts`、`handoff_mode_facts.ts` 和真实 lease/Todo/Host 调用方完成版本化升级、消费者验收，正常路径只运行两种显式策略 | 旧值只留迁移解析和原回执恢复。重放旧操作不授予新执行权、不覆盖后来的策略；没有旧回执就拒绝新 legacy 意图。 |
| `todos.py` / `todos/line_update.py` 的可写 Markdown Todo 分支 | canonical create/update/terminal owner；新建／默认与受支持存量 Goal 路径完成迁移 | 人工叙述、永久 Markdown 投影／重建、合格 import/export 与旧备份恢复；provider 缺失不回退 |
| `runtime_shadow_writer_adapter.py` 等旧捕获 producer | 最后受支持源 writer 已退役，prepared/committed outbox 均已分类和对账 | 真实恢复义务结束前，迁移内历史 outbox reader 保留；不保留第二套持续捕获权威 |
| Python 重复决策和私有 RPC facade | TS 事务拥有语义、效果与输出；最后生产／动态／打包调用方切走并完成等价／恢复验证 | 仍使用的 transport、Host IO、专门 provider；不因 Python 语言就整删 authority_core.py 或 provider adapter |
| 旧正常格式 reader/writer | 正常 runtime 打开前走版本化备份升级，日常只读写当前格式 | 支持窗口内的迁移 codec、原历史／回执。删 reader 需明确兼容决策，不能由本机升级成功推断全部用户升级。 |

策略默认值按执行责任而不是数据库品牌选择：协作式所有权、效果不需要排他执行
凭据的本地工作流候选为 `soft_claim`；共享／云端、重叠 worker 和须 fence 的外部
效果候选为 `hard_lease`，本地 managed 执行需要时同样采用 hard。未知拓扑要求
明确选择，不隐含 legacy，也不统一降到 soft。既有显式策略经 reviewed migration
才改变；最终默认值须用真实调用方验证，不能仅按“本地／云端”字面分类。

### Canonical Todo 归属与更新规则

沿用 SQLite 准入／Python 退役任务作为计划 owner，在 note 写入下一个具体交付包、
依赖、精确证据和删除出口。整 Goal 晋升、canonical 消费者清单、永久 Markdown
投影、Host lease 生命周期、完整 summary/detail 复用各自任务；旧 note 提到已合并
PR 不构成重复开工的理由。现有收尾 monitor 归并相关 head／review／merge 变化，
唤醒对应 owner；安静轮询不算推进。

实际实现缺口要有 owner 和验收：canonical 默认／升级接入、两策略迁移及 legacy
执行删除、最后 writer／capture 删除。用现有 successor／dependency 字段关联，
不另造 RFC，不给每个 PR 建一个 monitor。安装回读及约定删除完成后才结项，发布
计划／请求评审／合并均不能代替完成。私有 Goal 清单、备份路径、测量和 Todo ID
不进入公共文档。

## 删除 writer 前，本机可以积极做的验证

以下是冻结候选版本后的工程窗口，不是承诺发布日期。故障注入只用可丢弃 runtime
和经过验证的隔离副本，不能为了测试杀掉或改写活跃 Goal。

1. **现在／最初 1–2 个工作日：** 固定 binary/source 和实际 Node/SQLite driver；
   清点安装版本、caller、provider 和积压。保留独立 legacy/File/SQLite 三臂，
   验证备份可恢复、完整 Todo JSON／历史／回执一致和后续新写入。覆盖 null／缺失／
   false、归档依赖、lease、validator、in-flight Turn、pending outbox。
2. **前项通过后，接下来 2–3 个工作日：** 在隔离候选中删除拟退役分支或让它明确
   失败，走真实命令及安装后的 UI/Host 消费者。注入 commit／selector 发布前后
   进程死亡、过期实例／revision、锁竞争、runtime 不可用和投影中断。重试只能
   settlement 一次，恢复后合法工作能继续。用保留的迁移兼容 binary 验证恢复，
   不删除 selector，也不拿旧字节覆盖已确认的新写入。
3. **合格候选的连续观察：** 记录真实经过时间与负载覆盖、命令延迟、内存／磁盘／
   WAL 增长、最老积压／consumer lag、不确定结果恢复、重复效果和实例污染。
   每日读回，定期在隔离 observer／副本中验证恢复。适用的 D2 十天自然时间 soak
   不能靠循环测试或回填时间戳加速；有记录的起点才开始计时，明确重启间隙和源码变化。
4. **先 cohort 后默认：** 按 RFC 7.2 的三层决策，安装恢复及相关执行控制通过后，
   有界自愿试用可以先于正式自然时间资格开展。对有限且获授权 cohort 做 reviewed
   备份／迁移／观察，以恢复证据决定扩面。本机所有 Goal 迁移也不等于外部用户
   已升级。旧 binary/artifact 保留用于诊断，但操作／回退必须用与当前格式兼容的版本。

发生已确认数据丢失、重复效果、跨实例污染、selector／receipt 不一致或不可恢复的
不确定结果时，停止候选写入、保留只读证据，通过所属 journal 恢复。延迟／内存超过
既定预算要记录失败，不能直接提高限额。本机调查可以激进，晋升和删除证据必须可核验。

## 本轮实际验证

在 `ce3862e33` 上，本机真实 backend 的迁移／中断恢复套件通过 15 项；
真实 CLI 的 archive／升级／切换与有界源捕获测试通过 19 项。
SQLite 既有 rehearsal 完成 100 和 1,000 次提交、冷 CLI 采样及清理；报告仍为
**incomplete**，正式负载、容量、平台和 elapsed-soak 项未完成，本次没有启动 soak。

将先前捕获的真实来源隔离快照中 1,101 个完整 Todo 重建为三笔合成源事务，当前生产
CLI 全部 drain，原 Todo JSON 完整相等。所得四笔事务恢复／审计到 SQLite 后追加
第五笔已确认合成写入，再 export／restore／audit 到 File，新写入保留。没有修改
活跃 Goal。这证明有界 drain 和逻辑 archive 连续性，**不是**全部原始 224 笔历史重放、
live selector cutover、重新捕获当前生产状态或 D2 验收。私有快照和原始诊断不入库。
本规划 PR 不删除生产代码，只确定删除出口并记录实际验证边界。

## 采用后续核对与下一步决策

在 `71525ab90` 上，已安装 CLI、本地构建 App／bundled runtime 及两个服务使用同一
源码；安装 doctor 确认配对，实际 chat 页面可渲染，上一份交付的入口 JS／CSS 仍可
取回且字节相同。这是本机安装证据，不是签名／公证 release，也不代表发送消息或
settlement 链路验收。

新捕获的逻辑 archive 分别保留 379、993 笔原始事务。379 笔 archive 恢复到 File
和 SQLite 后均通过 exact audit；993 笔在 SQLite 上通过。核对包括原事务／回执证明
和完整 projection，将之前的合成 drain 证据推进到真实保留历史。本轮没有再验证
追加新写入后的反向迁移，之前的有界结果仍单独计证。私有 archive、registry 和
原始诊断不入 Git。

首轮演练隔离了数据，却复用了活跃 Effect 进程，因此排除其受污染耗时。最后一次
审计核对了独立进程；共享重型工作结束后的日常命令重新采样成功。
[验证指南](../../../../development/testing-and-quality.md#isolate-the-managed-effect-process-as-well-as-the-data)
已明确两层隔离。干净重采样不代表重型管理工作并发时的公平性已验收。

现有权威 provider 保持不变。B 复用 #4931 实测形成的 SQLite 候选决策，不重新做同一
优化。消费者优化先追踪完整命令成本与重复投影：history 的行数限制不限制 semantic
history，status／quota 仍可能生成数 MB 诊断包。在现有共享 typed owner 保留决策
完整性与 drill-down 合同，不能推断换后端就能消除这些成本。A/C 仍需执行／采用集成
证据；本轮没有启动 D2 自然时间 soak，也没有认证旧 writer 可以删除。

### 读取成本验收更新

#4931、#5215 集成后，配对的 File／SQLite 隔离副本保留 379 笔原始提交，最终
projection hash 相同。Node 24.21.0 下，每个 provider 分别启动三个新进程，
File 首次 head 读取为 5.98–6.32 秒，SQLite 为 34.5–36.0 毫秒；后续读取分别为
9.1–10.2 毫秒、25.7–28.2 毫秒。这是进程冷读，没有清空 OS 文件缓存；File
验证全部保留历史，SQLite 读取当前状态，不承担相同的全历史证明。这支持将 SQLite
作为长历史候选，但不是同等完整性工作量的吞吐比较，也不构成发布默认值验收。

交替读取两个未变化的 File 存储，暴露了单份证明缓存互相淘汰的问题：每次都要
6.30–6.49 秒。改为有总容量上限的四份缓存后，各存储首次验证仍为 6.15–6.16 秒，
后续交替读取为 9.8–11.2 毫秒，cursor／hash 相同。每次仍检查实际字节摘要和存储
身份；淘汰与损坏回归覆盖缓存边界。

Quota 观察复用既有 should-run 摘要：捕获的单 Goal 行序列化由 1,252,747 降至
78,688 UTF-8 字节，显式明细恢复原行。这是展示体积测量，未减少采集、决策输入或
首次读取的验证成本。

另一项 148 秒隔离演练通过新进程为两种 provider 各追加 12 笔提交，跨越 checkpoint，
逐轮验证原回执重放、变更意图拒绝及 projection／hash 一致性。它证明这段有界存储
流程，**不代表** Host 执行、活跃 Goal 采用或 D2 的十天 soak 已完成。本次不改变活跃
authority、发布默认值或旧 writer 删除决定。B 仍缺持续负载／平台／容量证据；C 仍需
consumer／新建入口及受支持升级验收。

### 合同健康检查的权威归属

#5222 已合并并完成本机备份、CLI／App／服务升级及实际页面读回。默认 quota
响应约 93 KB，显式全明细约 1.37 MB，Todo 计数相同；上一份交付的 13 个静态资源
字节一致。这是采用证据，不代表新一轮正式 release、provider 默认切换或 D2 完成。

后续公共 CLI 的隔离反例表明：Todo 列表已读 canonical provider，但合同健康检查
仍解析旧 Markdown Todo。仅在展示副本增加一条缺少 task_class 的旧 User Todo，
File 和 SQLite 的正常 Goal 均被判为不健康，status 退出码变成 1。
修复让晋升后的合同检查复用既有 TS canonical 快照／记录校验和 User Todo class／scope
规则及既有 Todo 元数据健康约束；Python 只分批传输必要语义字段并适配诊断。
Agent 路由、认领／排除冲突、废弃策略与旧格式非法状态仍判为不健康。结构有效不等于未完成 User Todo 健康。
provider 缺失或读模型损坏仍报 Goal 范围的错误，不回退 Markdown。未晋升 Goal
保留旧格式检查；非法 UTF-8 改为结构化读取错误，命令仍拒绝。叙述、registry、
历史及公共边界检查不因此取消。此处不新增或替代
Todo 写入时的业务校验，也不重审完成／deferred 历史的授权。真实 File／SQLite
对照覆盖两种持久记录格式、缺失展示副本、非法活跃 class／scope 与 Agent 元数据、合法历史隐式绑定、
合法执行者排除及缺 class 的完成／归档记录。正文不进入诊断 RPC，大集合使用有界分批，不放宽消息上限。

用保留历史所得的 1,109 个 Todo 当前 projection 和约 7 MB 展示文件，在隔离存储中
配对测量初版仅检查结构的合同修复。三个热样本由 0.52–0.58 秒降为 File 的
0.11–0.12 秒、SQLite 的 0.14–0.16 秒；这些数据早于活跃 User Todo 语义修正，
不用于证明修正版本的成本。此实验重新初始化当前 projection，不是完整历史重放，也不是
整个 status 延迟或跨平台容量验收。私有输入不入库。

4,101 个合成 Agent Todo 的规模对照中，File／SQLite 的基线与修复版完整 `status`
仍触及既有 `todo.succession.project` RPC 响应预算；修复后的合同 API 能读取该集合，
不代表剩余整命令包体边界已完成验收。

B 按主 RFC 7.2 区分有界 provider PR、可恢复的小范围开发者试用和发布默认值。
提议的绝对延迟预算不否决每次合入或试用：在匹配负载下比较当前受支持版本，
公开绝对增量与相对变化，并核对消费者影响。正确性、原始回执、完整 metadata 和
可恢复迁移仍是硬要求。冻结报告保留原预算及失败／缺失项，调整决策不改写旧证据。

[PR #5251](https://github.com/loopx-project/loopx/pull/5251) 在已有 strict JSON codec
owner 中优化历史数据物化，保留持久化 canonical 编码，减少历史投影中不可变值的
重复分配。旧正式报告各自绑定 source：作者报告 `d767b06f1` 为 8 通过／6 失败／10
缺失，`02d3dee83` 为 14 通过／0 失败／10 缺失；这些结果不验证后续 head，也不补齐
缺失轴。下一步核对变更路径的配对测量与真实 provider／调用方，再对齐并发、恢复、
consumer lag 及保留的自然时间 soak 适用性。
比较 runner 原先要求历史重试返回 conflict，与已合并 #5169 矛盾：相同完整意图应
返回原 applied revision／cursor。现在核对原结果，分别拒绝 projection／event／receipt
漂移，在重试前后分页验证全部历史，不保留所有预期快照。不变量失败就不发布成功
报告；这些检查放在既有计时窗口之外。这修复的是验证工具，不代表 provider 故障、
D2 通过或默认切换。#4224 已报告在 `e98191faa` 上于 9 月 14 日开始 soak，仍需最终
结果及对当前候选的适用性证据，不能称为未开始，也不能仅因无关 source 修订就重启计时。

在 source `613180ae` 上，64 KiB 匹配负载的 macOS 正式测量有 13 项通过、1 项失败、
11 项缺失：冷 CLI status p95 为 4.39 秒，超过 2 秒预算。Linux 的仅存储正式测量
有 12 项通过、13 项缺失；小规模 CLI 演练不能替代正式 CLI 轴。按需调用的
`performance-diagnosis` 已在隔离目标上采集 Python 墙钟、独立 Node CPU 和 Linux
线程栈。启动、候选扫描和等待仍是待验证假设，需复跑未插桩的原负载；不能用
profile 权重替换这项准入失败。

[#5283](https://github.com/loopx-project/loopx/pull/5283) 是相邻的只读 delegation
预检优化，不是 provider 实现。当前进程复用候选报告了 File/SQLite 配对暖调用收益，
同时披露冷启动成本；保留并推进精确 head 评审、安装读回和真实请求方采用。
重复调用消费者与上述单次冷 status 分别验收。沿原 TS／CLI owner 读取当前决策，
固定 Python worker 只作传输，不缓存权限或验收结果；TS 替代与最后调用方验证完成
后再退役 Python 规则，不能凭进程复用就宣称退役或 SQLite 准入。

已有 TS 替代且真实受影响调用方验证完成的 Python 重复决策，可以按最后调用方独立
退役；整条 Markdown writer 删除仍需 C 的新 Goal／升级／恢复出口。消费者完整
metadata、freshness 和决策输入继续验收。合同检查与 attention 现在按 runtime／Goal 共享请求内已校验的完整
canonical Todo 快照。独立检查和下一次请求重新读取；租约与投影写回读取不参与。消费者修改
不会污染保留输入，首次读取失败不会在请求中途恢复成功。这不代表 registry、Markdown、
历史或多个 Goal 之间的原子快照。集成后继续核对安装态消费者，A/C 与 D2
维持各自未完成项；只有最后受支持调用方退出且恢复验收通过，才能删除对应 writer。


在相同的隔离当前投影（保留 1,117 个 Todo）上，完整 status 组装的 Todo 读取由两次降为
一次。三个进程内热样本中位数：File 496→430 ms，SQLite 583→488 ms。base/head 输出
差异仅为观测时间与时效字段，完整 metadata 和公开响应 schema 保持不变。Tracemalloc
测得两种 provider 的 Python 峰值分配约 13.5→16.5 MB：为隔离消费者保留完整输入，
以约 3 MB 峰值换取少读一次；返回后的保留分配仍约 2.1 MB。这是当前状态
读取成本证据，不是历史重放、CLI 冷启动、D2 资格或默认 provider 对比。Python 仅管理请求
传输输入的生命周期；TS 仍拥有校验、resume、succession、验收及选择规则。
Resume 输入现在仅在分组含等待条件时准备；succession 仍读取完整 lineage，等待条件仍能
看到归档及跨角色依赖。在相同的 1,117 条隔离当前投影上，以已共享快照的版本为基线，
结构化调用由 2,687 降为 1,570；原生读取仍为一次，TS effect 调用仍为 16 次。
三个热样本中位数为 File 430→425 ms、SQLite 493→481 ms。这点延迟差异不能证明冷启动
或默认 provider 已验收。实际 Agent／整 Goal CLI 输出除观测时间和时效字段外，大小和
语义保持一致；整 Goal 输出仍约 2 MB。

以 `b9a34c3e7` 为基线继续分解发现：共有读模型校验为了检查记录顺序，在已完成唯一 ID
索引校验后仍将完整 Todo 数组序列化两遍。改为比较该索引的插入顺序和现有 Unicode
排序 ID；完整内容摘要、记录校验及 provider 读取继续保留。隔离的 1,117 条 Todo／36 条
租约当前投影中，每个 provider 取十个热 Node 样本，校验中位数由 42–43 ms 降至 27 ms。
这是共有 TS 成本，不能据此给 provider 排名或改变默认值；没有增加权威缓存、遗漏租约、
限制响应条数或改变前端合同。Unicode 顺序、重复 ID、非法 JSON、归档记录篡改及两种
记录格式的拒绝规则均有回归覆盖。
下一步以 `c57454e40` 为基线，在每次同步集合或 ownership 读取内部复用一份已校验的 Todo
身份索引，去掉重复记录复制，保留各消费者的校验顺序及 Todo-only 对租约完整性的独立性。
这尚未合并不同 RPC 的 provider 读取。在同一隔离投影上，每组交替取十个热样本，完整
Todo／租约集合中位数为 File 输入 32.4→27.5 ms、SQLite 输入 32.9→28.0 ms；包含 provider
读取的 ownership 分别为 43.2→39.8 ms、62.9→61.4 ms，后者存在离群样本。完整 CLI status
的记录和 metadata 保持不变，仅观测时间与读取时效不同；真实 File、SQLite、PostgreSQL
测试通过。这些组件结果不能证明冷启动收益、持续运行验收或默认 provider 已达标。
跨 RPC 的 ownership／status 读取及整 Goal 前端摘要／列表／详情仍未完成。Agent status
已有有界展示，仅压缩最终 JSON 不会消除完整来源计算。

### Succession 传输容量

当前 5,000 条 Todo 的摘要复现了 B 阶段另一处边界：第一次整图 succession
计算仍在预算内，但展示复核再次发送事实和评估时超过既有 2 MiB 请求限制。
共同发布的内部 RPC 改用明确、严格核对的事实列和评估列，复用 summary adapter
的列式传输模式；不丢弃记录、关系边、摘要哈希或 metadata，TS 整图与复核规则
保持不变。旧内部线格式直接替换，不保留第二套解析；持久化 Todo 格式和公共
响应不变。代表性复核请求从超过 2 MiB 降到约 0.96 MB，没有提高预算。
真实 File/SQLite CLI 验证精确计数、跨远端记录的推断后继、metadata 保留与
provider 状态不变。这只修复有界容量，不证明任意规模、稳定延迟、D2 验收，
也不授予默认 provider 切换；整 Goal summary/list/detail 消费和持续观察仍待推进。

### 打包源码指纹成本

B 阶段增量复用已有的有界、有序文件读取器，并发读取源码字节。摘要保留全部
相对文件名和原始字节、metadata 失效、请求内缓存及失败／重试行为；Python
文件系统适配层不新增状态规则或持久缓存。

当前复核对比基线 `0538bf1631a7` 与本实现，使用 macOS arm64、Python 3.13.13、
Node 24.21.0。启动预热一次后，每组交替运行九个独立 CLI 进程，读取相同的可丢弃
合成 File/SQLite fixture；Effect 进程隔离，没有清空 OS 缓存。源码快照包含
249 个 TS/JSON 文件（3,065,799 字节）。指纹阶段中位数分别为 123.5→53.8 毫秒、
111.3→48.0 毫秒。完整 `status` 中位数为 1.032→1.054 秒、1.019→1.010 秒；
样本 p95 为 1.745→1.104 秒、1.114→1.086 秒（九个样本的 p95 就是最大值）。
二十组完整响应仅明确列出的观测时间字段不同，畸形 registry 的拒绝结果不变。

这支持有界的冷调用成本改善，不证明通用 status 提速、provider 吞吐或 D2／默认项
验收。显式清除指纹缓存后，同进程热缓存微基准从 6.7 退化到 12.8 毫秒；正常未变更
请求仍复用缓存。字节已经在热缓存中时，线程调度成本更高。两种负载都不能外推为
用户群体的延迟保证。整 Goal 大包／消费者与持续运行仍待推进，本增量不授权删除
旧 writer 或裁剪 UI 数据。

### File 恢复的批量回执读取

Archive restore／audit 已使用 provider 通用的 1–64 个操作批量回执合同。File 现在
也实现该合同：每批只做一次完整字节与 store identity 证明，不再为每条回执重读
整个文件。调用顺序、重复 ID、缺失结果、原始回执内容均保留，每个返回内容独立。
非法输入或损坏历史会拒绝整批；数组空位在访问存储前被拒绝，公共 helper 入口也
遵循该规则。单条回执查询的错误投影不变。

在同一份已恢复、隔离的 1,287 笔历史上，macOS arm64／Node 24.21.0 每组九次暖读
样本，File 查询 16 条回执的中位数从 346.2 降至 21.3 毫秒；未改动的 SQLite 对照为
111.3／111.1 毫秒。每个 provider 的回执结果与权威 head 均保持一致。这是暖读组件
测量，不是相同完整性工作下的 provider 比较、整次恢复延迟、冷读或 D2／默认项
验收。File 每恢复一笔仍重写保留的文件；此前一次完整历史恢复超出调用方的
300 秒超时，随后才发布精确匹配的确认。批量回执优化没有闭合这项恢复成本。
#4224 的 soak 已启动；其最终证据和对当前候选的适用性仍待核对。
