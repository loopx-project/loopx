# C1 来源收敛与历史集成检查点

- **日期：** 2026-10-30
- **状态：** 未合入的集成证据；C1 仍未完成。
- **来源：** 原候选 `648104978fdd67176cccdbb765c5bc3f36bf2ca3`，集成目标 `e8f4c6f8125e3cc0ef912afac653d5542671392f`。
- **语言镜像：** [English](2026-10-30-c1-source-convergence.md)

## 当前修复：按依赖读取 capture 并迁移消费者

C1 集成 main `3a0c9608cc06`。共享 thin heartbeat renderer 曾把完整 capture 的
可用性变成行动前无条件通读整份 capture 的义务。普通／App × 默认／显式 context-off
四种同输入真实 CLI 对照实际 main 与修复前 C1 集成 `c6d7d0a573f0`：main 未新增此
义务，修复前 C1 四种入口均新增。
修复后仍要求当前工作上下文、必读材料及适用目标、验收、约束和停止条件；原 capture
按决策依赖读取。显式全读、所供 flags 以及完整原来源／身份恢复义务继续有效。
四种入口在读取条款之外的文字修复前后逐字相同，指导文字增加 347 个 UTF-8 字节。
这是语义纠正，不是测得的 token 或完整任务节省。

冻结 C1 `c37bd8d0d9` 的 selected-work 因导入已删除 helper 无法收集；native-child
与独立 delegation 记录 62 通过、八个失败。既有夹具现采用当前 context reader 和
v1 request／work-contract owner；仍故意注入矛盾的第二 carrier 和损坏的 owner
身份，要求副作用前拒绝。原材料、off 隔离、响应丢失恢复、重放及一次结算断言保留。
读取范围／来源预检 141 通过、四个跳过，native／delegation 70 通过。扩展运行先记
104 通过、28 失败，迁移后的完整 delegation／示例运行记录 139 通过、两个示例旧断言
失败；迁移显式选择和 typed 副作用前拒绝后，两个完整 canonical-delivery 用例在源码
及安装包均通过。拒绝断言核对原 subject，以及 Host、状态、scheduler 和扣额均无效果。
这 28 项没有全部在未改动 `c37` 上独立复现，不能统称为该冻结基线的回归。

首次安装 wheel 运行记录 88 通过、32 个 setup error：示例初始化器沿 checkout
相对路径导入 TypeScript。既有 Python launcher 现将实际加载包的根目录传给同一
初始化器；canonical authority、独占创建可丢弃 runtime 和拒绝已有状态保持。
harness 不复制 Core 源码。修复后安装运行记录 148 通过及两个残留旧结算字段断言，
修正后新的一次完整运行 150 项通过。包内 1,815 个 Python／TypeScript 源
与实测候选一致，374 个已观测 Python 进程仅从隔离安装包导入。
原失败与局部重放均保留各自证据。

配置内空缓存 Mypy（17 入口）、全控制面 TypeScript 及改动路径 Ruff 通过，未放宽
配置。精确 head 的 premerge 及评审／readiness 仍须独立取得，本文不声明已通过。
本片不建立父 PR 已显著缩小、完整 provider、独立评审、自然采用、模型费用或整体
C1 资格。实际 main 已有预期普通读取范围，不为 C1 此回归重复修改 main。
下方历史实验及冻结 base/head 成本分母继续保留。

## 上一次依赖修复：来源传输位于 driver 下层

C1 集成 main `5a58635028fb`，包含已合入的独立提案 #8192 和 #8186。
配置内 Python 检查原先报 463 文件中的 4,562 条错误：
低层捕获和 journal 读取器为薄的来源与身份 helper 导入了 driver／Host 结果转换器。
这七个 helper 现直接使用既有 `quota.turn_envelope` 传输层；删除三个旧私有实现，
已发布导出保留别名。TypeScript 决策 owner、wire 版本、来源拒绝、精确数字、
历史恢复和只结算一次保持不变。

同一配置的 17 个入口在禁用增量检查、隔离空缓存后通过，未放宽严格配置或 import
跟随。完整控制面 TypeScript 检查也通过。这是依赖边界修复，不表示修完此前可达的
全部模块类型，也不宣称运行性能收益。较广源码运行记录 627 通过、六个夹具失败、
四个跳过；干净集成基线复现相同六个失败，均为夹具访问已退役的第二份
`turn_envelope` carrier。修正后真正检验当前 producer 的缺 witness 恢复和故意注入
混合 authority，模块 36 项全部通过。重建的独立安装 wheel 在捕获／Host、会话／journal、
authoring 和完成路径共 136 项通过。包内 1,815 个 Python／TypeScript 源与候选一致，
141 个已观测 Python 进程全部从安装包导入，没有借用 checkout 源码。
较早的 100 项安装包运行及原六个夹具失败保留各自证据身份。

本依赖修复前，集成已使父 PR 从 201 文件（`49a621c9b8` 对 main `546c08c0d3`，
+18,429／−2,042）降到 197 文件（`2fe98521f1` 对 main `5a58635028`，
+17,928／−1,989）。这是有限缩小，显著降低评审范围的交付仍待继续。

独立评审、质量／readiness、完整 provider、自然采用和整体 C1 验收仍开放。
本修复不建立新的效率结果，也不资格整个父 PR。历史双语实验继续整组保留；
旧成本数据保持冻结 base/head，不改记为本次集成的测量。

## 上一次拆分：完整重规划 authoring

两个作者提案均集成 main `d0985c35aa`。[PR #8192](https://github.com/loopx-project/loopx/pull/8192)
把 C1 既有的同响应 authoring owner 独立提到 main，删除 Python 重复比较，恢复普通、
staged 及真实 CLI 读取器的 authoring。三个完整正文断言在原 main `a9485073f2` 均失败，
在修复后通过。缺少 stride 声明仍拒绝语义写回；Host 读取实际交付契约后可声明决策，
后续任务消费经过独立验证的计划。C1 在读取捕获内容前保留现代源版本与绑定校验。

main 修复的集成源码 30 项、隔离 wheel 30 项通过；C1 定向源码 180 项、TypeScript
96 项、隔离 wheel 30 项通过。Canonical fixture 从实际加载的包定位 TypeScript；
C1 fixture 使用实际结构化 Host prompt 和版本化 capture 绑定，不再假定正文内联或
沿用历史 digest。先前夹具失败继续保留。main 修复的冻结 standard 机械检查通过，
但严格质量收据仍缺失；独立评审及整体 C1 仍开放。此修复在冻结夹具中增加一次父
RPC（2 到 3，未计 CLI 子进程），不是净效率结果。两个独立提案均未落地，不宣称父
PR 已缩小。

## 最新字段复用：main 独立提案与 C1 同步

[PR #8186](https://github.com/loopx-project/loopx/pull/8186) 将原生渐进 reader 修复
独立提出：`9df99f042b5f` 基于 main `62e234c93999`。C1 在 `2f1c91085` 集成该 main，
在 `dde48f084b` 同步同一修复。既有 TypeScript owner 依次考虑精确要求列表、大条目
及其大正文；已改状态和未知控制条件使相应记录失效，当前修订号及完整恢复仍可读取。
完整当前返页与已认证的精确继承字段可合成为下一次 section coverage，不变成完整
模型输入基线。Native hook 使用已匹配的事件会话，而不假定继承 shell 会话环境。
没有新增准入、模型调用、配置或存储 schema。

下表是 **main base/head** 对照，不是 C1 的独有增量。File／SQLite、base/head、冷／温／
恢复／canonical 修订各一例，共 16 场景、40 Turn；12 个完成原三步合成 Task，4 个冷态
只执行第一步。核验当前／此前实际交付的要求、完整 UTF-8 返页、独立产物、原身份、
三回执、一次扣额和重放；准备、故意重试分列，完成子进程只计一次。提交 head 的产品
字节与实测源一致。

| SQLite 普通完整任务 | main 基线 | main 提案 | 变化 |
| --- | ---: | ---: | ---: |
| 温态三步 BODY | 147,059 B | 109,016 B | −25.87% |
| 修订后三步 BODY | 147,793 B | 113,819 B | −22.99% |
| 温态三步 stdout | 268,953 B | 230,730 B | −14.21% |
| 温态／修订 RPC | 863／867 | 863／867 | 不变 |
| 温态／修订 framed RPC 字节 | 基线 | +2.42%／+0.98% | 额外成本 |

File 同方向。冷态及本组恢复 BODY 不变。SQLite 温态观测的 Python 普通文件读／写增加
11.94%／19.06%，修订增加 5.73%／3.31%；不含 Node、内核、provider 和 CLI 外 fixture IO。
单次插桩样本不资格稳定延迟、模型保留、自然采用、token/cache、账单或整体净收益。
只拆正文的原型没有减少代表性任务 BODY，积极索引小条目的原型显著增加 IO；这些
负结果继续保留，不改记为最终修复通过。

源码反例覆盖已改正文／状态／未知控制、低收益拆分、部分与伪造观察、压缩、off 和
精确恢复；同一新增用例在未改 main 上失败。两条源码与隔离安装包的原生路径使用
同一 owner；真实 Codex 静态回环旅程各十次请求，覆盖下一次整块复用及未验证 resume
后的完整恢复，没有收费模型。记录的 `dde48f084b` 集成点 C1 配置内 mypy 有 4,818 条诊断，main 配置内 17 个
源文件检查通过；先前另一集成 head 的 C1 为 4,814，不豁免也不归因新增四条。
main standard premerge 的六项直接和十四项选中检查机械通过，但严格 Goal quality
receipt 缺失。独立评审、CQR、精确 head readiness、维护者合并权限及整体 C1 验收仍开。
提案尚未让 main 上的父 PR 变小，仍需维护者采用独立可评审切片；早期双语实验继续
整组保留在下方。

后续作者集成包含 main `910ff3bca5`，产品头分别为 main `a8902b05b`、C1 `7e52f3a1e`。
上表固定成本不改标为这个后续组合的实测。真实入口定向检查 main 102 项、C1 104 项
通过，两端 typed reader／报告投影各 66 项及 typecheck 通过。main 配置内 mypy 通过；
C1 当前为 485 文件中的 4,831 条诊断，新增诊断不归因、不豁免。standard premerge 的
直接／选中机械检查分别为 main 6／14、C1 7／19 项通过；两者仍缺严格 quality receipt，
C1 的人工评审门保留。

## 上一集成：字段复用进入主干后的增量成本

实测产品版本 `b90e711cdb81` 已集成 main `d602248c5329`，包括 #8124 字段复用、
Vision 审计引用、首读消费者和 Chat 观察更新。C1 在共享 TypeScript owner 中保留完整源
校验，原生导航与历史来源拒绝仍走同一路径；此检查点的后续修改只有验证和文档。
下方早期 cohort 保持原版本的历史证据身份。

当前工程对照使用原三步合成 Task 和两组 canonical 验收。File／SQLite × 冷／温／恢复
× 三次重复，共 36 场景、84 个已结算 Turn：12 个冷态场景只走一步，另 24 个完成原
Task，独立校验的产物一致。实际返回 BODY 连续重建到 EOF；每个省略 outcome 同时与
当前不可变来源、此前完整交付逐字核对。核验原身份、三回执、一次扣额及产品源稳定；
准备和故意重试探针单列，完成子进程成本只计一次。

| SQLite 普通路径，三次均值 | 已获字段能力的 main | C1 | 变化 |
| --- | ---: | ---: | ---: |
| 温态第二步 BODY | 27,874 B | 27,159 B | −2.57% |
| 完整三步 BODY | 102,836 B | 100,691 B | −2.09% |
| 完整三步 stdout | 225,841 B | 184,316 B | −18.39% |
| 完整三步 RPC | 863 | 866 | +3 |
| 完整三步 framed RPC 字节 | 11,754,471 B | 11,852,055 B | +0.83% |
| 已观测 Python 普通文件读／写 | 基线 | −10.39%／−13.83% | 有限 IO 边界 |

File 同方向。恢复时先完整重送当前要求，含回退的三步 BODY 仅少 1.78%；不能把相对
字段复用落地前主干的大幅下降，继续当作 C1 独有增量。

另有经真实 refine 追加一项条件的 canonical 修订探针，保留原条件。四个 File／SQLite
用例完成 12 Turn、四个 Task。main 保守重读两块正文；C1 第二步复用一块未变 outcome，
第三步复用两块，新条件完整显示。SQLite 第二步 BODY 为 52,169→32,905 B（−36.93%），
完整 Task 为 149,223→106,688 B（−28.50%），RPC 867→870、framed 字节 −0.23%。
每个 provider／arm 只有一个合成修订案例，不代表自然反馈采用或科学成果收益。

最终源码按唯一用例对账为 1,065 通过／四项跳过，包含实际 Codex 的静态回环。112 项
原生用例经过定向复验：修正输出预算缩页后仍按预设偏移读取、未读选择项就选择的 fixture，并将
八页压力循环与静态恢复资格隔离。原失败保留，不冒充一次全原生套件通过。全量 TS 为
6,161 通过／77 跳过，控制面类型检查通过。隔离 wheel 的 1,809 个 Python／TS 打包源
逐字匹配，24 项保存源／默认／off／恢复通过，实际客户端默认 Progressive 与独立
Delta 验证通过，无源码导入污染。

配置内 Python typing 仍有 4,814 项诊断，同样 17 根在 main 通过；不能以历史扩展范围
或传输验证豁免。PostgreSQL／完整受影响 provider 资格、独立评审、精确 head readiness、
安装态采用及完整 C1 验收仍开放。BODY、stdout 与 Python IO 分开；Node／kernel／provider
IO、模型输入／缓存／输出 token、账单和持续延迟未资格。本工程 cohort 未启动模型／
科学运行、活动安装、CI 查询／手动触发或合并。

## 当前私有 plan 边界

新的完整源 Turn 写 `loopx_turn_plan_v1`：一份原始 `work_contract`、源摘要与 capture
header。完整 planner 不再生成 v0 executor adapter。`planning_facts`、Host 输入与
`turnPlanWorkSource` 复用既有 TypeScript 来源／投影、路由和结算 owner；Python
保留已持久化 action hash 的数值编码。

| 生产链路 | 替换／保留的兼容路径 |
| --- | --- |
| Turn CLI 与当前 launch decision → plan | 直接传完整源。私有 planner 不再接收完整源与 v0 executor adapter 的组合。 |
| 默认 CLI plan inspection | 保留一份完整源及 portfolio、writeback；即使恢复 transport 元数据，inspection 也不可执行。 |
| 不可变 capture → Host | 绑定原来源与 header，不重建 adapter；保留 capture 拒绝、原读取命令和 Host v1。 |
| Journal 写入、检查、结算查询与 CLI resume | v1 经共享来源 reader 校验；保留 GoalRef、Agent、Turn、scoped identity、原生 Session 及一次性 effect。 |
| 原生报告恢复、capability context、reward-memory 激活与子任务结果 context | 读取相同 work facts，恢复证明携带原稀疏数值拼写；原有 experiment／capability gate 仍必需，关闭 memory 时不读来源或 provider；Host／provider 和权限规则仍由原 owner 负责。 |
| 已记录的 v0 journal | 显式保留 transitional 来源／adapter 交叉校验及历史 adapter-only 解码；错误 v1 不降级。 |

在此私有 plan 检查点，quota presentation 与 managed controller 的 quota preview
仍是独立 v0 调用方；后续替换记在下文。回退期间，未结 v1 工作仍须保留兼容 reader；停止
新的 v1 准入不表示旧 binary 可以重写或恢复它。历史工作及受支持恢复窗口核清后，
才退役历史 reader。没有新增 capability、设置、缓存或执行授权。

同源重放在实际冻结实现 `0eb0042191d5` 与候选上运行 14 个相同的合成 File／SQLite
plan 输入及 16 个原 Host 恢复输入。路由／Session／transaction／topology 与执行事实
全部一致，其中四个 plan 用例使用已有无 Todo replan 或 request binding。Capture
发布、真实 journal 写入、load／query 和幂等重放均经过生产 owner。相等比较只替换
两个实现各自的 capture 存储目录，不归一化来源条款或值。

| 实测组件，14 个 plan 用例合计 | 修改前 | 候选 |
| --- | ---: | ---: |
| Capture 后 canonical plan JSON 字节 | 528,364 | 373,372（−29.33%） |
| 实际 journal 文件字节 | 698,776 | 500,880（−28.32%） |
| 最终 canonical Host 输入字节 | 335,446 | 335,446 |

30 项同源调用及其被测链路步骤的原生 RPC 为 162→158，canonical 参数／结果 JSON
为 4,645,847→3,536,656 字节。Planning 少 18 次调用；校验 plan load 多 14 次，该读取
步骤从 7,052 增至 340,486 字节。Capture 前，新 plan 的源摘要使未绑定 Host header
多 97 字节；capture 后两个实现已有相同绑定，最终模型正文一致。这些组件测量不含
framing、CPU、模型 token、账单、冷／温态 context 成本与验收成果，既不改善“原始
capture 到所选正文”的 control packet 主指标，也不证明整条工作流净收益。

过去默认 plan inspection 删除完整源后保留有界 legacy 视图；删除 adapter 暴露了
这一遗漏，包括 portfolio 与 writeback context。当前 inspection 保留一份完整源。
另一个真实只读 File 夹具在两个实现的独立 store 上均通过，实际 JSON stdout 从
12,388 增至 26,524 字节（+14,136）。这是明确披露的读回成本，与持久化 plan 缩小及
最终模型正文不变分别统计，不声称 CLI 读取字节减少。

新增无 adapter 用例先在冻结 writer 上失败。随后集成还发现真实无 Todo 恢复 reader
仍读取已移除 adapter；现在改用完整源的原结算身份。当前未知版本、缺失／null 来源、
混合载体和错误来源／capture 绑定均是拒绝用例，并保留显式历史／transitional 对照。
修正读回、capability 与历史夹具后，当前源码的七个相关完整 Python 模块共 403 项全部通过。
TypeScript 类型检查与全量 6,053 通过／77 跳过的测试通过。PR 记录精确 source／wheel
矩阵、保留的失败／跳过及标准 gate。扩大源码运行的 1,367 通过、37 失败、22 跳过
保留为历史：它暴露默认 inspection 遗漏，以及 reward-memory gate 仍读取已不存在
adapter 的真实断点。后者现绑定 checked work facts；当前缺源拒绝与显式历史对照仍
独立。这是私有 plan 迁移提议，不是 installed 推广、
相对原 quota 的成果／完整成本资格或 C1 完成。

## 当前 quota 与 managed-controller 切换

源码候选删除剩余普通 quota presentation builder 分支，让 managed controller 的
fresh preview 使用与执行 Turn 相同的完整源。复用 `compose_turn_decision` 与
`completePlanningFacts`；后者在既有 `turn.controller` RPC 中完成校验及 work／lineage
派生。精确数值 token 跨越此边界，不增加投影 RPC。未知支持事实保留；未知的当前执行
动作在 dispatch 或结算前拒绝。

| 替换的机制／调用方 | 共享 owner 与实际删除 | 剩余消费者与退出条件 |
| --- | --- | --- |
| Quota `_project_quota_cli_payload` 与 renderer 选择 | 删除 v0 builder import、ordinary/envelope 格式分支、软降级及闲置 scheduler/capture 参数；既有完整 composition 和 renderer 负责 should-run 输出。 | Status、plan、monitor 保持诊断投影；request owner 原本就只在 should-run 接受 `--turn-envelope`，此 alias 不选择第二份当前契约；不把删除不可达分支记为字节收益。 |
| Managed fresh quota preview → loop disposition | 携带实际 scheduler context 组合完整源；删除 `TurnControllerInput`、其私有 JSON 副本、`build_turn_controller_input` 及 TypeScript `build_controller_input` 操作；disposition 复用 checked complete planning facts。 | Managed controller 保留 observation/retry 与 journal 归属；只有历史 v0 decision 继续使用原 scalar route codec。 |
| 完整 plan/capture/journal → reader 与 resume | 保留此前单源 v1 迁移及原来源/header 校验；普通 preview 不再重建 v0 adapter。 | 已记录的 v0、transitional journal 保留显式 reader，直到未结工作及受支持恢复/回退窗口核清；错误 v1 不能降级。 |
| 显式 legacy envelope builder 与历史 renderer | Legacy builder 已无新的普通产品调用方；完整渲染仍复用既有 renderer 模块。 | `control_plane_composition_scenarios`、`model_behavior_corpus` 与原版本对照仍构造 legacy 输入；最后一个受支持 fixture/replay caller 和版本窗口迁移后，才删除对应 constructor/decoder，同时保留独立历史负例。 |

可复现维护演练在既有完整源 validator 中临时加入一项合成缺材料检查，结束后逐字节
还原 owner 文件。同样四个真实 CLI/provider 用例在集成基线 `856faed423620` 与候选运行：
File/SQLite quota 拒绝在两者均通过；两个 managed 拒绝在基线失败、候选通过。现在只改
一个生产 owner、零个 caller 即可到达两个入口，旧 managed 路径需要另行迁移。临时检查
没有作为新生产读取 gate 保留。这证明的是维护边界，不是自然模型遵循或 Goal 验收。

持久八项完整源／未知动作矩阵同样先在基线得到四项 managed 失败、四项 quota 对照通过，
候选八项全部通过。它覆盖真实隔离 provider、原支持事实及不安全整数／整数形浮点，
验证不改 journal、不扣额。相关源码矩阵有 408 项 Python、76 项聚焦 TypeScript 通过。
另有 22 项旧集成夹具失败先在未修改基线复现：retry 注入仍瞄准旧 helper 语句，完整
planning 已换操作，sender 校验提前，历史对照误用了当前 v1 producer。修正保留 receiver
拒绝、原版本和 retry 负例，不抹去这些历史失败，也不把它们误算为候选新增回归。

集成 main `d4aaaaefbd37` 后，扩大源码矩阵 427 项通过，覆盖公开 lane fence 与完整
journal 恢复。集成同时保留 main 的 typed cast 和完整源 Goal/Agent 校验；lane 检查
先暴露缺少 `cast` import，随后修复。隔离实际安装的 wheel 通过八项矩阵，并在
File/SQLite 各完成两次原生合成事务及一次性重放；生产包来源与只供测试的源码 helper
独立核验。首次 wheel 构建被过期 frontend bundle 阻止，重建既有 bundle 后恢复。

Standard premerge 仍有 maintainability hold，且缺少精确 scope 的 quality receipt。
同一 main 的真实 Git checkout 复现四项 module-metric finding；候选另有 quota emitter
超过既有 decision-function 限制，实测 62 decision points／74 statements。本次不
提高 ceiling 或 exception；原有大小限额失败同样保留，聚焦矩阵通过不等于整 PR
merge gate 通过。

这仍是未合入的源码提议，不因上述检查安装候选、改变默认、运行模型、放宽预算或关闭
C1。下述原 quota 成本对照独立于此前相邻 plan/journal 节省，也独立于仍开放的自然
验收成果门。

### 原 quota 产品路径成本观察

冻结的原 main 基线为 `5f1a9d78ba7ac`，不是相邻 private-plan 基线。候选 runtime 内容
固定在后续 mainline 集成前的 `b8c2bf1dc`，之后的源码验证不重测此 cohort。两份源码均用
Python 3.12.15、同一冻结编号产物任务、隔离 File/SQLite store、等长目录和原生
context delivery。十二个场景均读取返回入口及全部必需续页，创建相同产物，通过
独立 validator，写回、扣额一次并验证幂等扣额重放；十二份产物 SHA-256 相同。
这只验收该小型合成任务，不证明两种 packet 版本有相同自然模型成果或完全相同
决定义务。

冷态启动隔离 runtime；温态计入准备 guard/read；压缩后场景还先运行实际
PreCompact/PostCompact CLI hook，再准入下一次工作。后者测 baseline 失效，不是
恢复被中断的业务 effect。搭建成本排除；准备阶段失败单独保留，表格仅用完整跑完
的最终对照 cohort。

主指标为 `1 - 所选 BODY 文件字节 / 同次完整 capture 字节`，不混入 reader 包装或
其他输出；后者单列。

| Provider／场景 | 原 quota：capture → BODY（缩减率） | 候选：capture → BODY（缩减率） |
| --- | ---: | ---: |
| File／冷态 | 59,033 → 28,446（51.81%） | 55,416 → 55,416（0%） |
| File／温态 | 58,464 → 6,419（89.02%） | 54,849 → 9,411（82.84%） |
| File／压缩后 | 58,582 → 28,562（51.24%） | 54,963 → 54,963（0%） |
| SQLite／冷态 | 59,121 → 28,530（51.74%） | 55,500 → 55,500（0%） |
| SQLite／温态 | 58,552 → 6,344（89.17%） | 54,937 → 9,397（82.89%） |
| SQLite／压缩后 | 58,674 → 28,648（51.17%） | 55,055 → 55,055（0%） |

候选 full fallback 保留完整源；BODY 缩减为 0% 不表示净收益为零。实际 initial read
选择必需材料，不打印整份 BODY。反过来，温态 delta 较小也不保证初读便宜：候选的
当前 initial read 仍提供完整必需上下文。

| Provider／场景 | 全部 stdout 字节，原 → 候选 | RPC 次数，原 → 候选 | 含 framing 的 socket 字节，原 → 候选 |
| --- | ---: | ---: | ---: |
| File／冷态 | 55,815 → 48,709 | 236 → 237 | 1,454,525 → 1,547,159 |
| File／温态 | 67,522 → 76,353 | 325 → 327 | 2,128,926 → 2,348,959 |
| File／压缩后 | 89,968 → 75,700 | 327 → 329 | 2,132,977 → 2,343,467 |
| SQLite／冷态 | 56,212 → 49,086 | 236 → 237 | 1,456,277 → 1,548,893 |
| SQLite／温态 | 67,849 → 76,806 | 325 → 327 | 2,131,180 → 2,351,673 |
| SQLite／压缩后 | 90,461 → 76,149 | 327 → 329 | 2,135,847 → 2,346,351 |

全部 stdout 包括 guard header、必需 reader 页、准备/hook、写回及扣额/重放响应。
两份源码在冷态/温态/压缩后均用 5/7/9 个 CLI 进程及一个独立 validator；最终 cohort
内部没有 help 或失败重试。RPC 次数/字节包含 readiness ping 与 framing；两者都
没有使用 referenced RPC snapshot。

一次准备成本不能在每个稳定温态步骤重复计费。拆开已测准备后，当前温态操作的
stdout 增加 File 15,936／SQLite 16,083 字节，单个冷态操作则少 7,106／7,126 字节。
在此负载上，一次实测冷态节省不够支付一次实测温态增量。这是有条件的字节记账，
不预测真实会话事件比例、token、账单或成果价值。

Capture IO 同样增加。File 上 Python 可见 capture 读/写字节分别为：冷态
186,399/189,228 → 277,768/238,234，温态
387,693/392,774 → 664,229/495,634，压缩后
430,659/378,418 → 664,963/475,336；SQLite 方向相同。同场景的可见 journal 字节与
provider-state 留存体积一致，capture 留存体积则多 24,503–51,506 字节。这些是
Python 常规文件流量与留存大小，不是物理 IO 或写放大；Node/provider IO 与原生
SQLite page IO 仍未测。进程墙钟包含 Python 启动与 wrapper，每条件一次观察不能
资格化延迟；本次未测模型请求、cache/账单覆盖或人工注意力成本。主路径字节/RPC
回归及原大小限额失败继续保留，不能用相邻 plan/journal 节省宣告抵消。

### 初读复用已解析 cursor 的传输改进

后续 reader 增量与集成基线 `86081b142c62` 对照，不替换上文原 quota cohort。
Capture 生成本已请求 TypeScript selection owner 规划当前 initial navigation；
现用既有不可变 selection cursor 固定该 owner 解析的地址，opaque 首读直接复用。
每页仍检查完整源和所选视图 digest，原值从 Python JSON 原文取出，保留不安全整数；
未选正文不再第二次经过 runtime bridge。原始 `--initial-read` recipe、显式 reader
及完整恢复路线保留。可选 metadata 写失败时沿用原 recipe；不支持的历史 schema
走 generic discovery，多一次有界 selection RPC。启动／传输失败不重试 runtime。

独立同-capture oracle 验证所选正文及未知义务逐字相同，不为此变化记新增 BODY
压缩或模型输入节省。温态初读仍需当前必读事实；旧 delta 或保存的 cursor 不授予
retention 或 freshness。

同一编号产物夹具、Python 版本、provider、准备、压缩 hook、validator 和一次结算
在十二个隔离场景重放，全部产物一致。完整路径 socket 流量小幅减少；冷态／温态／
压缩后两版本 RPC 次数均为 237／327／329，包含 ping。

| Provider／场景 | 全部 stdout 字节，基线 → 候选 | 含 framing 的 socket 字节，基线 → 候选 |
| --- | ---: | ---: |
| File／冷态 | 48,258 → 48,293 | 1,541,169 → 1,509,780 |
| File／温态 | 75,583 → 75,664 | 2,337,709 → 2,275,365 |
| File／压缩后 | 74,963 → 75,033 | 2,333,325 → 2,270,641 |
| SQLite／冷态 | 48,635 → 48,670 | 1,542,959 → 1,511,454 |
| SQLite／温态 | 76,036 → 76,117 | 2,341,764 → 2,276,882 |
| SQLite／压缩后 | 75,412 → 75,482 | 2,336,165 → 2,273,331 |

每对当前 initial reader 的 stdout 字节完全相同，该 reader 的 socket 流量约从
60 KB 降至 30 KB。更长 cursor 路径使冷态／温态／压缩后完整流程 stdout 多
35／81／70 字节。SQLite Python 可见 capture 读/写字节为：冷态
276,540/236,976 → 280,522/237,574，温态
661,271/492,992 → 669,177/490,960，压缩后
662,043/472,836 → 669,965/474,028；File 同样增加读取成本，可见 journal 字节
一致。准备 cursor 增加本地工作和留存 metadata。原生 provider IO、模型/cache 账单
及自然成果仍未测，单次墙钟观察不能资格化延迟。该相邻传输改进不能清除原 quota
温态回归，也不清除既有大小／质量 hold。

冻结基线上的同-capture 传输回归失败，raw reader 对照通过。首次十一模块源码运行
记录 647 pass、78 skip、4 fail：两项 navigation 测试把已准备的 model cursor 当作
fresh recipe 调用，另两项历史 staged-reader 夹具在未改基线上同样失败。原 raw／
历史负例保留，最终三个受影响模块 103 项通过。实际隔离安装的 wheel 同样 103 项
通过，并检查已加载模块及 CLI 子进程的安装包来源。首次 wheel 尝试漏载仓库的
runtime 隔离夹具，该结果不采用；现 default-route 夹具在写入前要求两根目录都属于
可丢弃 test base，修正后的打包资格检查加载原隔离夹具。源码隔离模块 21 项通过，
缺隔离负例保持合成目录未动。Standard premerge 七项 direct 通过，十九项 selected
中十八项通过；相同五项 maintainability finding 仍阻止通过。不改变 ceiling、
exception、merge authority 或自然成果验收。

### 原生命令归属与已结算回执

本次普通 native caller 采用已经交付的 `refresh-state --settle-turn` adapter。
完整来源 plan 使用 `quota_settlement_plan_v2`：写回命令调用该 adapter 并明确请求
receipt 输出，quota spend 由 runtime 执行，不再无条件要求 Agent 另跑一条命令。
Completion、claim/lease、原身份、Vision authoring 与 terminal 条件保留原义务。
部分失败仍返回 canonical 原 `settlement_owed`；不增加准入、自动重试或直接扣额旁路。
Native-off/manual v1、历史 reader、replan successor 创建及 callback runner 保留原路径。

Receipt 是显式选择的操作后结果投影，不是新的准入输入，也不声称上下文保留。
精确回执、未解决／失败／未知控制仍可见；已知 supporting snapshot 完整保存在不可变
结果中，由原 CLI reader 读取。Capture 交付失败保留已提交回执，可按原身份恢复。
真实 replay 测试还发现旧 combined adapter 虽报告 settled，refresh replay 却只返回
前两步回执；现独立读回原三步回执，不再次执行 child 或扣额。Quota 展示 IO 也在
既有 owner 内分离。

三组实现使用相同冻结的编号产物任务与 Python3.12.15：原 quota `5f1a9d78ba7a`、
集成 C1 `898aaa9d0c33` 和本次 runtime 增量。十八个隔离的 File/SQLite cold、warm、
post-compaction 场景都交付相同独立校验产物，且各只扣额一次。子 CLI 的 RPC 与
Python IO 也计入；import observer 等待 pinned package 加载，不从工作目录抢先导入
LoopX。Agent CLI 调用为 5/7/9 → 4/6/8，但内部仍有一个 CLI child，因此 CLI 进程
总数保持 5/7/9。RPC 次数包含 ping 与该 child。

| Provider / 场景 | 全部 stdout B：原 quota／集成 C1／本增量 | RPC：原 quota／集成 C1／本增量 | Socket B：原 quota → 本增量 |
| --- | ---: | ---: | ---: |
| File / cold | 55,622 / 48,556 / 39,643 | 236 / 237 / 231 | 1,452,338 → 1,577,844 |
| File / warm | 67,302 / 76,098 / 66,385 | 325 / 327 / 321 | 2,125,470 → 2,350,007 |
| File / post-compaction | 89,643 / 75,454 / 65,676 | 327 / 329 / 323 | 2,129,249 → 2,345,468 |
| SQLite / cold | 56,020 / 48,933 / 39,982 | 236 / 237 / 231 | 1,454,093 → 1,579,746 |
| SQLite / warm | 67,723 / 76,551 / 66,792 | 325 / 327 / 321 | 2,128,276 → 2,352,873 |
| SQLite / post-compaction | 90,133 / 75,903 / 66,079 | 327 / 329 / 323 | 2,132,085 → 2,348,364 |

主指标仍是各完整 capture 到其所选 BODY 文件的字节缩减；与上表 initial-read 页面和
全部 stdout 的分母分别保留。

| Provider / 场景 | 原 quota F → BODY B | 本增量 F → BODY B |
| --- | ---: | ---: |
| File / cold | 58,907 → 28,329 | 54,498 → 54,498 |
| File / warm | 58,338 → 6,408 | 53,925 → 8,605 |
| File / post-compaction | 58,458 → 28,446 | 54,027 → 54,027 |
| SQLite / cold | 58,995 → 28,414 | 54,578 → 54,578 |
| SQLite / warm | 58,426 → 6,420 | 54,005 → 8,613 |
| SQLite / post-compaction | 58,546 → 28,530 | 54,107 → 54,107 |

Warm 同次 capture 的 BODY 缩减率，File／SQLite 为84.04%／84.05%，原 quota 为89.02%／
89.01%；候选的 cold 与 post-compaction 仍为0%。

Full BODY 回退仍是 0% 缩减，不代表净益为零。扣除一次准备后，当前 warm 步骤仍为
File 33,603 → 40,558 B、SQLite 33,932 → 40,901 B，比原 quota 多约7KB。
Warm 全流程 stdout 约少1.4%包含较小的一次准备，不能抵销这项重复开销。
Warm Python CLI 可见总读／写为 File 636,750/451,370 → 919,810/591,486 B，
SQLite 645,056/454,206 → 928,728/595,466 B。Node/provider/native page IO、模型
usage/cache/账单及自然成果仍未知；并行字节对照不资格化延迟。独立 validator 的调用与输出已计入，其文件 IO 未测。最终集成头重测的 warm BODY
为8,605／8,613B，前次为7,799／7,807B，而实际 stdout 相同；各自分母保留，不把
单次比例当作稳定保证。完整净收益与 C1 继续开放，下一重点是重复 warm 控制输入与重复 snapshot／transport IO，不是再造 runner
或减弱义务。

集成 runtime `5aa46dc27de8` 的十个完整相关 Python 模块为419通过、10跳过，
TypeScript typecheck 与243项 focused 测试通过。实际隔离 wheel 为245通过、10跳过；
1,181个已加载 Core module 和392条 CLI child 路由来自该安装。前次 fixture 失败保留：
旧 v0 断言、依赖 metadata 长度的 UTF-8 offset、隐含 producing cwd 已按独立的当前
语义修正。真实 CLI 覆盖结果 capture 失败、超大未知控制、错误绑定／workspace、
响应丢失与精确 replay。Standard premerge 报告7项 direct 和19项 selected checks，
其中1项 inherited maintainability advisory；raw ratchet 仍因4个未改动基线模块失败，
原 quota emitter finding 已消失。未更改 ceiling、exception 或历史失败；这不构成
exact-scope quality receipt，也不授予 merge／readiness 权限。

### 已结算快照重放与重复原 quota 对照

`4ef428767ce9` 的输出 IO 增量，让已完成 `current` Todo 投影与原本支持的
`delivered` 使用同一按需读取路径。严格已知、无待办动作的 runtime route、shared
projection 与 Explore 快照也保留在原不可变完整结果中。不增加 RPC、缓存、权限
owner 或设置；Python 只处理既有 TypeScript 事实的文件／输出 IO，结算、freshness
及权限仍归原 typed owner。未结算／错误／dry-run、未知嵌套字段、route 冲突、
投影重试、产物复制及阻塞 postcondition 保持直接可见。原身份、三项回执及未满足
Vision checkpoint 保留；读完完整结果既不证明验收，也不证明 retention。

新对照冻结 main `f04ec308f6e7` 与当前候选，使用相同 numbered-artifact Task、
必要材料、独立检查与一次扣额。File／SQLite × 冷／温／压缩恢复各重复三次，
共 36 项、252 次父／子实际源码及解释器核验。下表为三次观测的中位数，单位字节；
current 排除一次性 baseline 准备，全部 stdout 仍计入该准备成本。

| Provider／场景 | 全部 stdout B：main → 候选 | 当前 stdout B：main → 候选 | RPC | Framed socket B |
| --- | ---: | ---: | ---: | ---: |
| File / cold | 55,570 → 36,270 | 55,570 → 36,270 | 236 → 203 | 1,451,832 → 1,437,675 |
| File / warm | 67,892 → 62,991 | 34,239 → 37,183 | 326 → 284 | 2,127,817 → 2,152,912 |
| File / recovery | 89,549 → 62,276 | 55,764 → 36,392 | 328 → 286 | 2,131,374 → 2,147,978 |
| Sqlite / cold | 55,963 → 36,344 | 55,963 → 36,344 | 236 → 203 | 1,453,556 → 1,439,471 |
| Sqlite / warm | 68,313 → 63,133 | 34,568 → 37,261 | 326 → 284 | 2,130,625 → 2,155,458 |
| Sqlite / recovery | 90,038 → 62,414 | 56,165 → 36,466 | 328 → 286 | 2,134,204 → 2,150,686 |

温态重复步骤仍分别**多 8.60%／7.79%**（File／SQLite），RPC 少 12.88%，
framed 字节仍多 1.18%／1.17%。温态 Python 可观测读＋写为
787,482 → 813,035 B 与 796,984 → 822,778 B（各方向中位数相加），仍多约 3.2%。
其它验证并行期间，调用次数／字节不资格化 CPU／延迟；原生 provider／SQLite page
IO、真实模型缓存／输入／生成输出、账单及自然成果均未知。

本增量修改结算返回，不修改 guard BODY 选择。第一遍温态完整 capture F → 所选
BODY 为 File 的 58,306 → 7,082 B 对 53,899 → 8,579 B，及 SQLite 的
58,396 → 7,094 B 对 53,979 → 8,609 B；每个比例只使用其同次 capture 的 UTF-8
文件。候选冷态／恢复仍选择完整 BODY，主缩减为 0%，不表示工作流净价值为零。
三次候选温态比例分别在 82.62–84.08%／82.64–85.44% 间变化；本次仅改输出，
不把这些变化记作新增 BODY 收益。

另用旧 `9380173c` 与当前输出 IO 消费相同已捕获结算 payload。温态首次＋重放
分别少 3,347／3,612 B；若消费者实际将两份结果中新延后的字段全部补读，真实
selected reader 多返回 5,434／5,723 B、增加 2 RPC，此组件反多约 2.1 KB。
等概率需求的纯字节近似在约 62%／63% 处持平；需求频率未知，不能由近似或固定
Task 宣称模型／费用净收益；没有新增强制补读门。

七个受影响完整 Python 模块共 360 通过；实际隔离 wheel 使用原测试配置、conftest
与 default-root 隔离，241 通过。1,185 个已加载父 Core 模块、1,186 条 child CLI
route 及 1,199 个加载 Core 的 child import 审计均指向该 wheel。真实 File／SQLite
caller 验证完整结果恢复、重放仍仅一次扣额／三回执，以及未来或可执行嵌套控制不能
被隐藏。这是 source／packaged 输出资格，不是 installed 采用或完整 C1 验收；
以上历史 cohort、失败和冻结预算均保留，重复温态输入及完整成本缺口仍开放。

### 组合结算中的 Vision 预算恢复

既有 Vision prepare owner 通过共享、有界的 request-error 传输集中返回独立数值
预算诊断。C1 沿完整 capture 及其返回的组合结算命令使用同一规则。五项独立数值
错误一次拒绝；超长 enum 文本保留原预算错误，短而非法的 enum 仍拒绝。不提高限制、
不改写撰写包、不新增决策 owner。File/SQLite 测试在每次拒绝后保持来源状态及 run
index 不变；明确修正、独立 history 回读及重放保留完整包、原 Turn、三回执和一次扣额。

固定调用者比较冻结的上一 C1 head `75c05340b6ce` 与集成后的产品代码。两边只修正
错误实际点名的字段，得到同一最终包，只结算一次，不另取 guard。使用等长 namespace
和同一 Python 隔离重试片段；包含记账 child，每 provider 的 Python 进程从七个减至
三个。两边相同的 fixture 初始化、admission 与独立验证均在测量片段之外。

| Provider | CLI 尝试 | Stdout B | RPC | Framed socket B | Python 观测读 B | Python 观测写 B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| File | 6 → 2 | 11,473 → 9,269 | 137 → 133 | 822,570 → 817,482 | 131,460 → 116,843 | 39,169 → 39,193 |
| SQLite | 6 → 2 | 11,499 → 9,279 | 137 → 133 | 823,294 → 818,206 | 131,962 → 117,297 | 39,285 → 39,309 |

首条拒绝更大，但完整重试片段的 stdout 少约 19.2%、RPC 少 2.92%、framed 字节
少约 0.62%；Python 观测读少约 11.1%，写反增 24 B。未测原生 provider IO、延迟、
真实模型修正、输入／缓存／生成输出或账单。它只证明相对上一 C1 head 的确定性错误
恢复改善，不是相对最新 main 的新对照、同 capture BODY 新缩减或原 quota 完整任务
整体净收益资格；以上温态输入回归与冻结门仍开放。

### 完整契约上的原生 ready 交接

本次整合 main `bd79adc64968` 与既有 Host ready 来源交接。原交接把 ready 复查强制
设为 `context-delivery off`，在 C1 内将已启用完整 composer 的组合结算换回旧分步
计划。新的独立断言在真实 File/SQLite × App/SSH 四条路径上均先失败。薄 adapter
现保留启用的原生 profile，使用既有延后展示选项：先捕获完整 ready 来源，再仅由
既有 saved-capture owner 展示一次。不增加 CLI 开关、语义 owner、RPC 或权限。

| 实际调用方 | 替换行为 | 共享 owner 与保留边界 |
| --- | --- | --- |
| 绑定的原生执行准备 | 强制 off 的 ready 复查 | 既有完整 composer 保留必需上下文和组合结算；Python 只转交 native 目录与展示模式 |
| Host post-tool 上下文 | 模型须再取 guard 恢复缺失的 ready 契约 | 既有 capture/reader 交付验证过的同 Turn 原件与原 claim 引用 |
| 已准备的 native 收尾 | 活跃调用方收到分步 v1 计划 | 既有 v2 owner 提供组合操作与三回执；旧记录及 unbound/off 调用者保留原恢复契约 |

完整 Goal 仍对照独立原文核验，允许完整内联或真正声明的读取；off 无效果，身份错配、
来源损坏、重放和一次记账检查保留。这修复消费方采用，不代替另需验收的语义规则变更
演练或最后历史 reader 退役；原 C1 验收边界保持开放。

七个完整受影响源码模块及真正隔离安装的同一 wheel 均为 280 项通过、70 项既有
opt-in skip。1,897 个比较的打包产品文件逐字匹配；235 个完成进程、44,758 次 Core
模块观察无 checkout 来源污染，突然退出仍不在退出时审计范围。初次源码运行保留
279 通过、一项旧 reader 程序假设失败及 70 skip；复用既有 reader 程序修正，完整
20 项模块先通过，再得到最终全范围结果。caller pilot 曾误请求旧展示或重复使用
capture 目录；失败保留，不计入完整对照，也未放宽 capture 不可变规则。未活动安装
App/CLI。

新的 hard-lease cohort 使用冻结的公开编号 Task，停止于第三步。冷态完成第一步；
温态/恢复先验证并结算第一步、释放 lease，再准入和验证第二步；恢复在第二 Turn 前
执行真实 PreCompact/PostCompact hook。两端使用原 quota 入口、相同材料、等长
namespace、同一 Python 和 observer，父/child 均从各自固定产品导入。它与下方旧
soft-claim 36 条件 cohort 分开，不混合比较分母。

两后端、三场景、两端各重复三次，共 36 条件、60 个独验工作 Turn；每场景产物相同，
每 Turn 三回执 committed，含重放只扣一次。654 次父/完成 child 来源观察与
640,896 次模块观察均匹配所属端，测量期间产品摘要不变。父 RPC 已含 child，不再
重复相加。whole 包含已完成的准备工作，current 排除它但保留 compaction hook；
计入工具输出、全部必需读取、Host 执行、写回、扣额、回读和 lease 释放。下表均为
三次中位数，单位 B，main → 候选：

| Provider / scenario | Whole stdout B | Current stdout B | RPCs | Framed RPC B | Python fixture read B | Python fixture write B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| file / cold | 57,597 → 45,996 | 57,597 → 45,996 | 423 → 288 | 2,938,704 → 1,929,733 | 720,046 → 662,664 | 302,925 → 272,952 |
| file / warm | 118,430 → 95,236 | 60,833 → 49,240 | 843 → 578 | 6,271,394 → 4,130,344 | 1,926,283 → 1,700,979 | 675,761 → 593,384 |
| file / recovery | 118,202 → 94,776 | 60,269 → 48,556 | 845 → 580 | 6,259,244 → 4,112,058 | 1,942,194 → 1,715,504 | 656,353 → 573,186 |
| sqlite / cold | 58,016 → 46,112 | 58,016 → 46,112 | 423 → 288 | 2,943,792 → 1,932,217 | 737,286 → 677,202 | 304,783 → 275,281 |
| sqlite / warm | 119,272 → 95,472 | 61,256 → 49,360 | 843 → 578 | 6,281,728 → 4,136,666 | 1,965,207 → 1,734,459 | 679,477 → 598,310 |
| sqlite / recovery | 119,040 → 95,008 | 60,688 → 48,672 | 845 → 580 | 6,269,816 → 4,117,784 | 1,981,156 → 1,748,980 | 660,103 → 577,919 |

温态 whole stdout 少 19.58%/19.95%，RPC 少 31.44%，framed 字节少
34.14%/34.15%，Python 夹具观测读写合计少约 11.8%；current stdout 少
19.06%/19.42%。这是累计 C1 对照，不把全部下降归给此小集成修复。温态 wire 范围
为 file: main 6,271,290–6,271,572; candidate 4,130,308–4,130,664; sqlite: main 6,281,492–6,281,936; candidate 4,136,236–4,136,704。不声明统计显著性。

主指标以生产者原始完整 `decision.json` 为 F，不能用已经投影的 recovery 文档代替。
60 组生产者/BODY 均核对原 Turn、控制及公布的 recovery/selected 摘要：

| Provider / scenario | Main producer F → BODY B (reduction) | Candidate producer F → BODY B (reduction) |
| --- | ---: | ---: |
| file / cold | 60,084 → 27,993 (53.41%) | 53,873 → 27,291 (49.34%) |
| file / warm | 75,126 → 28,022 (62.70%) | 61,271 → 27,230 (55.56%) |
| file / recovery | 75,342 → 28,135 (62.66%) | 61,465 → 27,376 (55.46%) |
| sqlite / cold | 60,310 → 28,201 (53.24%) | 54,091 → 27,436 (49.28%) |
| sqlite / warm | 75,352 → 28,195 (62.58%) | 61,491 → 27,434 (55.39%) |
| sqlite / recovery | 75,568 → 28,319 (62.53%) | 61,683 → 27,530 (55.37%) |

候选温态 BODY 的绝对字节更少，缩减百分比却较低，因为它的完整生产来源也更小；
两种比率都不能替代完整调用对照。文件差分不证明模型保留。本 cohort 观测到的 RPC
引用快照 payload 为零；provider/native/SQLite page 及完整进程 IO 仍未测。CPU/延迟、
真实模型输入/缓存/生成输出、费用、自然采用和长程净收益仍未资格化。旧正负 cohort、
冻结预算及原成果要求保留其版本；源码/打包结果不等于 merge readiness 或完整 C1 验收。

### 可执行必读义务与原生 runner 的体积归因

当前完整来源在既有 interaction owner 旁通过 `assertInteractionRequiredReads` 校验
显式可执行的必读义务。声明 command 或 ordering 的读取必须保留非空、无 NUL 的命令；
显式顺序必须是已注册的 `before_work`。纯文字义务、未知支持元数据、带引号的中文路径
以及历史省略的 ordering 保持原样。合法未读义务仍待执行；这项校验不补造已读确认，
也不增加全部材料先读完的通用门。

| 改变的边界 | 实际 owner／调用方变化 |
| --- | --- |
| 可执行读取语义 | 一个 TypeScript interaction validator，由既有完整来源 validator 调用一次；quota、managed preview、planning 与 Host 沿原调用进入，不增加 Python 规则或 RPC。 |
| 不支持的直接完整来源 | Python 运输保留显式 `contract_binding`，包括未知版本，交 typed planning reader 拒绝，不尝试旧 adapter。 |
| 验证调用方 | 将过期 CLI 故障注入从已删除的 envelope builder 移到实际完整 composition；保留显式历史版本负例。采用真实 canonical hard-lease inventory 和实际产物工作区，代替伪造准入或偶然的 Git checkout 发现。 |

在未改的 `f1614358ac13` 上，新增八个 File/SQLite quota／managed 拒绝用例失败，八个
既有合法／未知动作对照通过。共同规则无需修改两个调用方即可到达两入口。首版实现
误拒一个合法纯文字义务，由原有完整原文测试发现；修正实现而不删除该断言。另一组
真实 Turn CLI 在 Host 启动或扣额前验证未知版本／codec 和摘要正确但顺序不支持的
读取。这是一项有界语义规则变更，不代表独立评审或模型遵从。Registry hook 准入及
原版本 reader 保留各自 owner；新增注册顺序仍须该协议自身的评审。

整合 main `92294e7245cb` 后，最终八个完整 Python 模块在源码及实际隔离 wheel 均
196 项通过；1,897 个打包来源文件逐字匹配，325 个完成进程及 103,615 次 Core 来源
观察无 checkout 污染，突然退出不在退出审计范围。六个相关 TypeScript 模块 121 项
通过，完整 control-plane
类型检查和变更文件 Ruff 通过。之前源码 178 过／7 败及 wheel 168 过／17 败均保留。
修正保留原断言：六处过期 constructor 注入改为实际 composition；frontier fixture
使用 canonical authority；wheel 明确复制测试 helper，写回指定真实产物工作区。
仓库 worker 脚本仍是显式测试调用方；来源审计资格针对打包 Core，不代表独立 scheduler
已经安装。正常重建 bundle 后打包，没有安装候选。

之前 20,533 字符的 premerge 失败，现在于不可变 main `bd79adc64968` 与候选
`f1614358ac13` 上取得匹配的原生 runner 反例。固定同一父目录，直接输出为 20,333，
原生 runner 为 20,501：隔离目录在八处 fixture 根引用中各增加 21 字符，共 168。
原 premerge 布局再增加四字符目录，两版本均为 20,533。只替换所属 fixture 根和
观测时间后，payload 事实一致。实测仍以原 20,500 上限运行全部原断言，没有缩短路径
或输出；这项字符预算诊断与完整 capture 到 BODY 的 UTF-8 主指标分开。

[既有 fixture 预算提案](https://github.com/loopx-project/loopx/pull/8069) 负责该独立问题。
这里不校准、不 skip、不豁免；原失败仍失败。前述完整成本 cohort 保持各自精确版本，
不改称新增校验后的实测。CPU、模型用量、自然采用和长程净收益仍未知。

旧 constructor 的树内调用仍是显式 composition 与 model-behavior 验证 fixture。
原版本 journal／recovery reader 仍需核清已接纳工作和支持窗口；不为增加退役数量而
删除。本切片不退役这些 reader，也不关闭整体 C1 验收。

### 最新 main 的匹配 hard-lease 对照

另一次独立重复固定 main `92294e7245cb` 与候选 `b3b3b232724d`，纳入上面的可执行
读取校验。原编号 Task、fixture、observer、共同 Python 和 cold／warm／压缩恢复协议
均保持原样。每个 arm／provider 三次重复，共 36 条件、60 个工作 Turn。每端 1,914 个
产品源码摘要未变；654 个父／完成 child 和 640,896 次 Core 来源观察均匹配原版本。
完整原 Task 出现在每次实际消费的读取正文中；每 Turn 保留原身份、三份 committed
回执和一次扣额，同场景交付同一合格产物。这是工程资格，没有调用模型或安装 App 候选。

完整流程计入已完成的准备 Turn、所有必读、原生 Host 准备、适用的压缩 hook、写回、
扣额、重放／读回和 lease 释放。父指标已含 child；当前步骤列只排除已完成的准备工作。
以下为三次中位数：

| Provider／场景 | 完整 stdout B | 当前 stdout B | RPC 次数 | Framed RPC B | Python fixture 读 B | Python fixture 写 B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| file／cold | 57,221 → 45,732 | 57,221 → 45,732 | 423 → 288 | 2,930,378 → 1,924,529 | 715,582 → 658,504 | 301,509 → 271,509 |
| file／warm | 117,662 → 94,692 | 60,441 → 48,960 | 843 → 578 | 6,254,008 → 4,120,060 | 1,916,435 → 1,691,969 | 672,788 → 590,508 |
| file／recovery | 117,450 → 94,248 | 59,893 → 48,292 | 845 → 580 | 6,241,950 → 4,101,350 | 1,932,262 → 1,706,384 | 653,451 → 570,267 |
| sqlite／cold | 57,640 → 45,848 | 57,640 → 45,848 | 423 → 288 | 2,935,504 → 1,927,053 | 732,814 → 673,042 | 303,374 → 273,849 |
| sqlite／warm | 118,504 → 94,928 | 60,864 → 49,080 | 843 → 578 | 6,264,426 → 4,125,624 | 1,955,379 → 1,725,427 | 676,528 → 595,288 |
| sqlite／recovery | 118,288 → 94,480 | 60,312 → 48,408 | 845 → 580 | 6,252,476 → 4,107,176 | 1,971,260 → 1,739,860 | 657,195 → 575,088 |

温态完整 stdout 减少 19.52%／19.89%，RPC 减少 31.44%，framed 字节减少
34.12%／34.14%，观测到的 Python fixture 读写合计减少 11.85%／11.82%；当前步骤
stdout 减少 19.00%／19.36%。温态 framed 范围：File main 6,253,744–6,254,276，
候选 4,119,902–4,120,628；SQLite main 6,264,262–6,264,682，候选
4,125,580–4,125,850。不推断统计显著性。实际温态请求包括
`quota.turn_envelope.evaluate` 20 → 10、`todo.context.page` 178 → 62、capability
hook 校验 8 → 4；这些属于累计 C1 差异，不能全部归因给新增必读 validator。

主指标仍为同一次生产者完整 capture F → 所选 BODY。60 组逐一核对公布的原生产者
capture 摘要、recovery／所选正文摘要、原身份和控制事实：

| Provider／场景 | Main F → BODY B（缩减率） | 候选 F → BODY B（缩减率） |
| --- | ---: | ---: |
| file／cold | 59,828 → 27,845（53.46%） | 53,649 → 27,120（49.45%） |
| file／warm | 74,870 → 27,797（62.87%） | 61,049 → 27,072（55.66%） |
| file／recovery | 75,086 → 27,921（62.81%） | 61,241 → 27,194（55.60%） |
| sqlite／cold | 60,054 → 28,062（53.27%） | 53,867 → 27,274（49.37%） |
| sqlite／warm | 75,096 → 28,003（62.71%） | 61,267 → 27,274（55.49%） |
| sqlite／recovery | 75,316 → 28,131（62.65%） | 61,459 → 27,403（55.41%） |

候选 BODY 绝对值更小，缩减率更低，因为完整来源也更小。文件 delta 不证明模型保留。
新 cohort 支持“同一合成产物下测得的工程开销更低”，不替换旧 cohort，也不证明模型
生成输出、cached／uncached 输入、费用、native provider／SQLite page／完整进程 IO、
CPU、延迟、自然采用或长程收益。原体积失败、历史 reader 义务及完整 C1 验收仍开放；
本次测量更新不改变门、默认、评分或运行时行为。

### 过渡期 journal 的恢复入口

canonical resume loader 原先只对当前 v1 plan 校验完整来源。已记录的 v0 plan
即使带有 `work_contract`，仍走原始 legacy 身份分支；writer、普通 inspection
和 settlement lookup 已会拒绝被改动的来源。执行路径仍在下游拒绝它，因此这是恢复
入口不一致，不能据此声称 Host 越权启动或扣额。显式 retry inspection 同样返回
blocked 观察，而非 v1 的入口错误。

loader 现在把所有显式完整来源交给既有 `turn_host_lineage`／TypeScript source
owner，包含已记录过渡格式的 source／adapter 交叉校验。当前 v1 加载本就使用该调用；
过渡格式增加这次调用，不宣称普通路径 RPC 节省。没有完整来源的历史 plan 仍保留原始
身份精确比较和原字节。plan 加载不新增整个 journal 的 settlement 一致性门，相关
诊断仍归既有 recovery inspection；普通坏 journal inspection 继续返回结构化 blocked
观察。

既有 File／SQLite journal 矩阵补入 loader 对内容改动、未知版本、null／缺失 binding、
冲突 adapter 及已重签名 interaction／action／reference 改动的拒绝。数值来源、当前
版本缺源拒绝、纯历史格式加载及字节保留继续作为控制。公开
`run-once --resume-turn-key` 与显式 retry-inspection 入口验证相同来源拒绝，不改写历史
或扣额。冻结的修复前 loader 在两个 provider 的过渡格式用例均失败；修正 CLI fixture
遗漏的必填 project 参数后，显式 retry inspection 的两个过渡用例也失败，两个当前
plan 控制通过。这些是分别记录的产品与 harness 问题，不是六个产品缺陷。

整合前的恢复 cohort 在源码和隔离实际 wheel 中均有 139 个用例通过。整合 main
`2852eb453bef` 时保留完整原生交接、两侧 lifecycle 断言及共享 TypeScript import；
随后源码 81 个、TypeScript 178 个用例通过。被中断的源码／wheel 尝试没有终态结果，
不计作通过。

整合后的 wheel 首次有 67 个用例通过、14 个 settlement 用例失败：其子进程默认在
源码 checkout 工作，恰好借用了该目录的 workspace 资格，离开 checkout 安装后便无法
验证。诊断 observer 确认实际 workspace 拒绝。既有测试现显式使用各自的真实产物
project，全部原断言、坏 workspace 负例和 once-only 恢复控制保留；完整受影响模块在
源码及字节未变的安装 wheel 中各有 23 个用例通过，另 58 个未改用例已在前一 wheel
cohort 通过。这覆盖跨 cohort 的 81 个独立用例，并非一次未中断的最终 81 用例运行。
1,898 个打包源文件逐字一致；最终 23 用例 wheel 运行观察到 125 个完成进程、64,109
次 Core 模块来源，无 checkout 污染。突然退出仍不在退出审计范围；旧失败继续保留。

本次修复实际仍受支持的恢复 consumer，不为退役数量删除 reader。legacy constructor
只剩前述具名历史／composition／model-behavior fixture；原未结工作、支持恢复窗口、
独立审阅及完整 C1 验收继续约束退役和采用。前述成本 cohort 仍固定于原 main／候选
快照，本次完整性修复没有重新测量该 cohort。

## 较早的集成证据

集成保留 main 已抽取的 quota-presentation owner 与稀疏原生 journal 运输。
候选中的重复数值 collector／validator 由既有实现替代。验收、summary lane、
authoring 和 Vision 共享既有来源身份及编辑 owner；普通 JSON 相等不能证明跨
Python／JavaScript 边界的原数值身份。正文大小判断仍比较序列化正文，不比较身份 framing。

独立的 24 项 Python 到 TypeScript 回归先得到 16 项失败、8 项对照通过。修复身份／编辑
后 24 项全部通过，覆盖整数形浮点、带符号零、不安全整数、稍后的精确 authoring owner
及相同 audit 对照。扩大集成验证最初还暴露旧 fixture 的 hash 引用和一项 authoring
缺失的 CLI 用例；这些失败保留为证据，须另行完成修正后的验证。这是来源／运输证据，
不证明 installed 采用、模型验收成果或完整成本资格。

2026-11-01 的 heartbeat 消费切片（`18b55e04d`）让 captured native Goal 输入
复用既有 heartbeat 规则 owner 的路由、安全与完整工作指引。新生成的 on/off
提示共享这段文案；历史保存正文不重写。逐条复核保留 actor、权限、必读来源、
原始／累计成果要求、顺序、证据、接续与停止条件。删除的是独立维护的文案副本，
没有新增决定或结算 owner，也不改变激活默认值或协议。

在同一 CLI 调用方、320 字符 scope、显式 CLI 和五项能力参数下，仅替换成冻结
`958eef4c` renderer 得到未绑定／绑定正文 5,041／5,080 个归一化字符；候选为
4,859／4,898，满足原 4,900 限额。两种候选正文均少 180 UTF-8 字节；更长路径
负例仍以 5,051／5,090 超限。Captured native Goal 正文反而从 3,187 增至 3,235
字符，仍低于原 4,000 宿主硬限。这些是生成输入的测量，不是整份 base 仓库
重跑、control packet 压缩、模型采用、token 节省或成果收益。

在该检查点，消费检查有 114 项提示／平台用例通过、1 项平台跳过，以及 12 项真实隔离
File／SQLite CLI 最终输入／恢复用例通过，宿主 executable 为合成实现。
输出／差分矩阵仍有 5 项失败：完整 quota
Markdown 30,462>10,000，拥挤 plan 24,044／27,508>22,500，完整 alias
24,252>13,000，另一个 multi-agent probe 25,933>13,000；其余 219 项通过、
1 项跳过。预算和样本规模均不放宽，新文案不继承历史 restoration 展示豁免。
C1 切换、剩余 reader 及原 quota 单会话成果／完整成本对照继续开放。

后续 Markdown 切片（`b852af5f7`，集成目标 main `035f7ac66`）在普通 quota
和 alias 共用的完整 Turn renderer 中移除嵌套 pretty-print 缩进。每个根字段
独占一行；全部嵌套值、来源条款和 binding 仍保存在同一个可解析 JSON 区块。
JSON 输出、capture、原生宿主输入、legacy 展示及决定／结算 owner 均未改变。
与冻结 `f3ae16362` renderer 的同源对照计入 stdout 最后一个换行：

| 合成来源 | 原 Markdown 字节 | 当前 Markdown 字节 | 减少字节 | 完整 JSON 字节 |
| --- | ---: | ---: | ---: | ---: |
| Small | 30,462 | 24,531 | 5,931 | 24,252 |
| Crowded | 47,237 | 37,846 | 9,391 | 37,555 |
| Crowded with Vision | 55,893 | 45,249 | 10,644 | 44,954 |

行数从 860／1,214／1,361 降为 69／72／73。Markdown 仍比对应的完整 compact
JSON 多 279／291／295 字节；减少的是展示缩进，不是语义 control packet 内容。
完整来源模块 63 项通过，覆盖未知事实、整数形浮点、带符号零、不安全整数及
转义指令原文；集成的有界 reader buffer 检查 2 项通过。输出／差分矩阵仍为
219 项通过、5 项失败、1 项跳过：small Markdown 当前为 24,531>10,000，
另四项失败保留上一检查点的大小和门槛。历史失败仍是失败，未改变上限、
fixture 规模或要求；不据此认证模型、费用、安装采用或长程收益。

可选 Host 切片（`8316c9fa5`，随后集成目标 main `5a9c0a612`）让 Ark、MemT
的工具身份通过 `extensions.turn_host.extract_turn_work_identity` 读取既有完整
dispatch owner。完整输入复用 selected-work owner，包含 adaptive 主任务；历史
输入保留 provider 原始声明值及严格 string／NUL 检查。Session lineage 保持原有
归一化方式。Fresh context、来源／投影完整性及 MemT 精确 Goal 生命周期拒绝门
均保留。本次退出两个 provider 自行解码身份的调用点；DSH、native-child、steward、
session-recovery 的兼容 reader 和 executor 的 v0 运输仍未退出。

MemT 候选校验现在保留原请求与完整来源。真实候选转换完成后再使来源失效，原本
仍被该本地检查接受的结果现在会被拒绝；这不证明外层 Core 结算此前会接受它。
实际 CLI 输入仍没有已准入的 MemT 生命周期，不能打开记忆；converter 测试没有
补造该身份。SDK 用例使用隔离合成协议对端，不运行 GPU 或模型服务。

同源对照在冻结 `46bdaddb3` 与候选上调用真实 Ark 身份消费函数，输入是完全相同的
合成 File／SQLite CLI 请求：

| 成本观察 | 原实现 | 候选 |
| --- | ---: | ---: |
| 每个请求的身份检查 RPC | 1 | 1 |
| Canonical JSON 参数字节，File／SQLite | 41,286／41,338 | 未改变 |
| Canonical JSON 返回字节 | 211 | 324 |
| 为读取身份进行的完整来源深拷贝 | 1 | 0 |

省去的拷贝来源为 28,432／28,458 canonical JSON 字节；这是来源大小代理，不是
内存分配测量。全部 dispatch-context 返回现在携带三个身份事实，其他调用方也
多付 113 字节。模型提示经 digest 核对仍逐字节相同，为 33,020／33,046 字节。
上述数字不含 RPC framing，不认证延迟、模型净收益、token、费用或长程收益；
完整 packet 压缩没有变化。

Ark／MemT 基线分别 39／37 项通过，修复后 41／43 项通过。新增用例先在两个
provider 的身份 decoder 及 MemT 失效来源接受行为上失败。Core capture／Host
检查 58 项通过，包含 File／SQLite 真实 CLI 输入、adaptive 身份、历史原始值及
禁止回退的拒绝路径。集成 main 后，envelope／interaction／conversation owner
的 TypeScript 用例 122 项通过。Native steward fixture 最初有 10 项启动失败，
因为继承的会话 provider 环境覆盖未被合成对端回显；只在测试子进程移除该覆盖后
10 项通过，未改变用户配置或生产 provider 检查。首轮失败保留为测试环境证据，
不算通过。初始断言曾把完整输入的 adaptive 选择强加给历史 provider
解码，已修正这两项失败并保留记录。扩大本地矩阵暴露 6 项 context-delivery、
2 项 driver-profile 失败；冻结 `46bdaddb3` 可复现全部 8 项，原有 5 项输出预算失败
也继续保留，均不算资格通过。C1 仍需解决这些交付缺口、supported reader 退出、
打包／安装资格及原 quota 成果／完整成本对照。两个可选包须使用同一份已集成的
Core 源码；版本下限不能证明已发布旧 wheel 含有所需接缝。

随包 DSH 后续切片（`e838e7594`）集成目标 main `8ac384cdc`，让完整输入的 Session／工具身份
复用同一次已校验的 selected-work 事实。此前完整契约的 adaptive 工作可能选择另一
主 Todo，DSH 却仍把工具和可复用会话绑定到旧声明 Todo。既有 dispatch 检查现在
仅为本次调用保留已经返回的私有身份事实，DSH 两处绑定复用它，不增加 RPC；该
元数据不进入最终模型输入。两个历史 DSH decoder 合并为一个 raw-value adapter，
保留 false-like 转换、组件位置、Goal-instance hash 和 fresh-iteration hash。
它只用于未携带 `work_contract` 的历史请求，待原版本恢复窗口对账后退出；已提供
但无效的完整契约不会回退。

冻结 `ae8ee460a` 与候选使用同四份真实 File／SQLite CLI plan 生成的合成输入，
通过实际 DSH adapter 对照普通与 adaptive 工作。每份仍只有一次 dispatch RPC，
canonical 参数／返回 JSON 大小均未改变；最终提示经 digest 核对逐字节相同：

| 来源 | 普通提示字节 | Adaptive 提示字节 | 内部 authority JSON 增加字节 |
| --- | ---: | ---: | ---: |
| File | 32,377 | 32,517 | 114／115 |
| SQLite | 32,425 | 32,565 | 114／115 |

最后一列是 Python 对象 JSON 大小代理，不是内存分配或 wire 流量。普通会话／工具
身份保持相同；adaptive 绑定有意从旧 Todo 改为当前所选主 Todo。探针未改状态、
未扣额；它验证该 caller 修正，不认证 packet 压缩、延迟、模型费用或同等成果／
完整成本收益。

新增回归先得到 4 项 adaptive 失败、7 项对照通过，随后 DSH 与完整来源组合有
367 项通过；另加 2 项真实 File／SQLite CLI 用例，经过随包 DSH runner、独立
校验、已提交写回及重放，runner 调用与扣额均只有一次。Provider 为合成实现。
可选 Ark／MemT 用例针对当前 Core 分别有 44／65 项通过。MemT 最初缺少已固定的
client；仅在隔离测试 overlay 安装该 client 后，5 项 CLI 用例仍从任务工作目录
导入旧 installed Core。子进程 import 探针复现缺失接缝，显式绑定测试子进程的
Python 源路径后这 5 项通过，未改变产品或用户配置；两次初始失败均保留。
切片复用既有 CLI／Chat Host owner，不增加能力、设置、用户步骤或激活默认值；
未认证实际 SDK 服务或 installed Host 采用。Native-child、steward 等历史消费者、
原 5 项输出预算门、8 项扩大矩阵失败，以及 C1 的原 quota 验收继续开放。

Native-child 元数据后续切片（`3d58796d7`）集成目标 main `07149df68`。请求构造、持久 Host
attempt 及两种 Codex transport 调用的 `configured_native_child_limit`，在存在
`work_contract` 时改读原始完整 interaction context；只有历史请求读取 v0
context。既有整数 decoder、完整来源校验、launch fence 和原生 receipt 准入
不变；这是本地运输读取，不是第二个 authority 或新默认值。共享来源校验原本就会
拒绝不一致的投影，不能据此认定此前放行过错误子任务。v0 envelope 仍保留用于
签名兼容数据，其整体运输尚未退出。

新增 on/off 回归先因读取历史字段失败。真实子进程与持久读回保留启用时的观测、
关闭时的隔离、父任务结果和私有正文排除；损坏完整输入在第二次进程或 receipt
之前被拒绝。首轮扩大验证有 195 项通过，两个新 fixture 被完整 Goal 来源门拒绝；
补齐 fixture 的真实文件及完整 revision 后修正的是夹具，产品门禁未改。
修正后的 native-child 模块 38 项通过；相同三个模块的最终完整重跑 197 项通过，
用时 149.37 秒。Owned operation Host 模块 34 项通过，
两个明确要求真实模型 opt-in 的资格用例跳过，没有启动 live Host。

冻结集成 `5d9dbb811` 与候选的八份固定请求，来自前述真实 File／SQLite
普通／adaptive CLI fixture，并通过既有 owner 重组加入合成 on/off capability
context。元数据读取仍为零 RPC，完整输入准备仍为一次 RPC。全部配对的请求
字节（40,202–40,778）、提示字节（30,744–31,030）、schema 摘要及 canonical
参数／返回大小都相同。这不是 packet 缩减、延迟、内存分配、账单或完整 Host
成本测量。原 5 项输出预算门、8 项扩大矩阵失败、其他 consumer／writer 迁移及
原 quota 成果／完整成本验收继续开放，历史失败保留。

后续在源码 `d1046499a` 完整复跑 `test_context_delivery_cli` 与
`test_loopx_turn_driver` 两个模块，得到 253 项通过，仅两个 profile alias 用例
失败。实际 CLI 返回先因 capture 缺少 Turn 身份拒绝请求，测试才无法读取
`scheduler_hint`；这不能证明调度策略失败。现为 fixture 提供原始 Turn，两个
alias 分别验证显式 delivery off 与 native delivery on。开启用例沿声明的原始
capture reader 连续读取 UTF-8 页，保留全部调度断言，并验证只有一次 admission、
恢复后没有 spend、Goal 与事件账本字节不变。

相同两个完整模块随后 257 项通过，用时 118.43 秒，无跳过或错误；聚焦的四项
矩阵也通过。本次增量仅修改测试，不引入生产设置、默认值、caller 协议或运行时
行为。原六项 context-delivery 失败保留为历史负例，本次未重新归因其逐项原因。
原五项输出预算门、其他实际 consumer／writer 迁移、supported reader 退出及原
quota 成果／完整成本资格继续开放。源码用例通过不证明 installed Host 采用，
也不证明 packet 体积、模型保留上下文或净成本收益。

后续 plan 切片仅在 `build_loopx_turn_plan` 收到完整契约时删除重复的
`consumer_version` RPC。既有 `driver_lineage` 已调用同一 TypeScript 版本、
完整性、source/projection 和 selected-work owner；历史输入仍保留独立版本诊断。
不增加校验实现、跨调用缓存、字段、协议版本、激活默认值或权限。

八份固定输入来自真实隔离 File／SQLite CLI plan，覆盖完整／历史来源及
fresh／resume 会话策略。相对冻结 `0cd09b46c`，最终 plan、Host 请求、Codex
提示和 schema 全部逐字节相同。完整 plan 的 RPC 从两次变为一次，测量的
plan／request／prompt／schema 序列从三次变为两次；规范 RPC 参数加返回 JSON
在 File 下为 92,598→81,554B，SQLite 下为 92,838→81,752B，分别少
11,044／11,086B（11.93%／11.94%）。历史对照仍为三次且字节完全相同。
四份完整模型输入都保留完整原契约和 fixture Goal；探针未扣额或调用模型。
CSV、SQLite 整数合计和 Decimal 比率独立复核八组对照一致。

六项 plan 矩阵先有五项重复调用失败、一项历史对照通过，修改后六项通过。
不支持的 adapter 版本、改动的来源正文、自洽重签却替换原动作的 adapter、显式
null 完整契约仍经同一 owner 拒绝。这只是运输组件成本，不含 framing、延迟、
内存分配、模型用量和成果；最终模型输入没有缩减。五项原输出预算门、原 quota
单会话对照和完整 C1 资格继续开放，不能把本次缩减与不同分母的物理包或选择
读取数字直接相加。

完整 Turn driver、原生 Codex CLI 和完整来源三个模块有 334 项通过，耗时
202.77 秒，无跳过或错误，包含真实 File／SQLite 交付、验证、失败 attempt
恢复和原 Turn 结算；未启动 live Host。这验证有界源码切片，不结清其余 C1
cutover，也不产生新的精确 head merge-readiness 回执。

后续 Host 运输修复保留显式提供的 `work_contract`，即便它不是对象。请求构建与原
capture 发布此前会丢弃这些值，使原生提示和共享 Host reader 将其解释成历史缺省。
两处运输现在保留原值，交既有 TypeScript 完整来源校验器拒绝；真正未提供的历史
请求仍用旧 reader。不新增平行校验器或字段。普通 executor 更早的来源检查仍在，
本次证据不表明外层结算可被绕过。

冻结 `42b842f93` 上，纠正后的 18 项矩阵有十项未拒绝失败、八项对照通过；修复后
18 项全部通过。覆盖两处运输、缺省／有效、null、false、零、列表、文本、空对象和
完整性错误，核原生／共享 reader 拒绝且 DSH runner 未加载。最初另有两项对照因
Goal 尚未完整读取而被正确拒绝；实际交付完整合成 Goal 后修正夹具，不放宽该校验。
八份固定 File／SQLite fresh／resume 输入的 plan、请求、提示、schema 仍逐字节
相同，RPC 次数和参数／返回字节也不变。这是兼容边界资格，不是新的压缩或净成本
结果；原五项输出失败和完整 C1 验收继续开放。

扩大六模块运行最初为 522 过、六项 Host-context 在 dispatch 前失败；冻结
`42b842f93` 重现相同六项。独立子进程 import 探针发现，cwd 为一次性 Goal 时
实际导入另一份已安装 Core。夹具现为子进程绑定并核对当前 checkout，且为历史
直接正文对照显式选择 delivery off。完整 Host-context 模块八项通过，产品文件
未变的另五模块保留 520 项通过；合并为 528 个不同的通过 case，来自上述两次
运行，不冒称最终一次六模块全过。原失败保留；未改变已安装 Core 或用户设置。

受管 Session 的独立验收 reader 现在复用同一个完整来源 owner。准备阶段复用
dispatch 在本次调用内刚核过的身份；模型工作后，回调在运行原独立 argv 前核验
当前完整来源。显式非法契约不能进入历史 decoder；真正缺省保留旧选择与诊断。
argv 前后的 canonical 声明复核、原 actor 准入、lease 和一次性结算仍由原 owner
负责。

冻结 `bd2ea9c58` 上，两次基线运行覆盖的八项中，File／SQLite 的四项来源改动／
置空反例仍错误执行独立 argv，四项正常／历史对照通过；修复后一次运行八项全过。
故障注入实际模型后回调边界；原生 Session、canonical Task、独立 argv 与结算
保持真实，仅模型／provider 为合成夹具。更早六项启动失败来自继承的 provider
override；共享合成夹具现隔离该环境值，不改变已安装设置。这验证回调边界，
不表明外层结算可被绕过。

原生夹具的同来源对照替换冻结基线方法，依赖和验收命令保持原样。准备阶段没有
新增 Turn-envelope RPC；模型后验收增加一次，File／SQLite 的规范参数加返回
JSON 为 42,911／42,959B。另八份固定 CLI 来源对照中，完整输入为
36,320／36,404B，历史输入没有新增 RPC；两组分母分开。均不含 framing、延迟、
分配、canonical Task 读取、argv、模型用量及恢复；额外基线 argv 仅对一次性夹具
做只读验证，不是生产重放。这是来源拒绝带来的组件成本增加，不是压缩或完整净
收益结论。原五项输出失败、剩余 C1 reader 及原 quota 单会话成果／完整成本验收
继续开放。

`9baa678db` 上的扩大运行完成五个原生／管家／日历模块，共359项通过；第六个
私聊模块在56过／37项相同provider启动失败后主动停止。这是未完成运行，不能称为
六模块全过；无名中断记录不计测试。同源码移除环境override的两项控制通过，随后
仅隔离ordinary合成夹具，原环境下同两项也通过。生产provider检查与安装配置不变。

随后集成目标main `8a9e11628`，在`4cdf977c3`适配其新增的另一处显式夹具调用；
运行时修复仍为`a49625e3a`。该集成源码的原生验收／普通会话完整模块68项通过，
335.058秒；私聊／Antigravity完整模块316项通过，644.420秒。两次运行合计384个
不同的通过case，不是一次新的六模块全跑。共享conversation-binding TypeScript
45项通过。最终检查没有跳过或错误；原失败与组件测量保留各自源码版本。这次集成
未重测完整成本，也不认证C1切换。

以下区块保存本次集成从规范 RFC 移出的候选叙述。测量、提议和现在时陈述属于各自原
检查点，不是对本次集成源码的新结论。当前行为以规范 RFC 和具名 owner 为准。尤其不能
由已退出的 scope 原型恢复强制模型阅读计划。既有负结果、原 quota 对照与 reader 退出
要求继续有效。目录内其他日期条目保持自身来源，不因这次迁移而被改写。

## 历史候选区块 1

下述按 Agent 范围交付的实现与测量仍是未合并的 C1 集成检查点，不代表默认安装行为。
已交付的验收材料切片与按候选决策阶段交付的提案在后文分别说明。

冷态交付默认展开当前 Agent 的成果。其他 Agent 的独立成果保留可发现索引，包含
身份、owner、目标、revision、工作关联、未满足要求标识、当前 review/check 状态及
精确 capture 正文引用。全局 Goal 意图、共同要求、权限与停止条件始终进入必读输入。
跨 Agent 依赖、共同验收，以及影响当前决策的阻塞或矛盾证据，须在相关决策前自动
展开，不受 owner 限制。Goal 级重规划和整体验收扩大到所有受影响成果；局部交付
无需重读无关历史证据。Owner 只是默认范围，不是权限边界或充分的相关性判断。
工作未绑定、关联未知或来源形状不受支持时，保持完整交付，直至既有 typed owner
能够证明较窄的必读范围。

Canonical 契约仍保留完整要求和证据。以现有同 capture 成果索引及 replan 分阶段
reader 为起点，合并普通/replan/detail 管线；索引须补足当前决策事实，不能只有成果
数量。必读范围保留要求全文，支持性来源和 review 细节通过绑定完整性、顺序明确的
reader 按需交付；依赖该材料的决策须先读完它。当前结论、新鲜度与未满足要求仍
内联，Agent review 不能变成独立检查。界面折叠本身不会减少模型输入；交付投影、
required-read owner 与 host 恢复契约须一起迁移，已有 capture 保留原完整读取义务。
正文缺失或保留情况不确定时，执行原 capture 支持的恢复，不能重新准入或假定已读。
不能为字节目标缩短保留要求、裁剪声明来源或靠正文关键词推断相关性。

C2 须在相同冷态、已保留温态及上下文丢失来源上验证：默认自己工作、跨 Agent
依赖/约束、未绑定工作、同伴证据变化、广域 replan 和 Goal 验收。计入实际模型输入、
分页及正文读取往返、恢复、延迟和完整成果成本；首屏变小不能抵消必要补读或漏掉
约束的成本。这是拟议交付行为，替换与回滚完成资格验证前，仍以当前完整来源 reader
为准。

当前集成证据：[导航收敛检查点](2026-10-29-navigation-convergence.zh-CN.md)。
[Journal 来源保真检查点](2026-10-29-journal-source-fidelity.zh-CN.md)
将可独立评审的持久化修复与尚未完成的数值 hash／上下文 codec 资格分开记录；既有
hash 语义保持不变。
后续[上下文来源保真检查点](2026-10-29-context-source-fidelity.zh-CN.md)
将通用冷态、差分和全量恢复修复拆为普通 #7661，C1 复用同一来源身份/修改 owner。
组件传输缩减不证明 canonical hash、安装采用或原 quota 完整成本收益；历史 hash
语义仍保持。

当前 C1 分支复用已合入 #7560 的 `outcome_context_navigation_v1` 和通用同 capture
`context-read --select`。注册关系建议读取顺序；归属不证明独立性，覆盖限制也不
新增模型全文读取门。原完整要求、当前状态、全局约束和反证仍可从原 digest 绑定的
capture 取得。

此前未发布的强制阅读计划、`outcome_context_projection_v0` wrapper、普通读取的
专用成果 reader 和融合 native projection 操作均已退役。Staged replan 原本的
decision 绑定验收引用仍归其 owner。Native IO 复用主干材料 planner 与精确恢复
codec，在可选规划前先提交当前完整恢复引用。简单 native capture 因而恢复为两次
owner 调用，而非原型的一次融合调用；旧调用数优化不构成当前资格。

已合入 [#7627](https://github.com/loopx-project/loopx/pull/7627) 提供文件优先交付及文件
差分，无需强制模型全文读取。C1 沿既有 native owner 采用其评审后的精确实现，不再
引入已退役的 scope 机制。实际安装采用、模型决定和验收成果/完整成本仍须分列。
历史 capture 保留其原身份与受支持的恢复。

以下测量和测试对应已退役原型的精确检查点，作为历史证据保留，不代表当前导航/
planner 集成的结果。

同一 24 成果/253 要求合成快照，以 61 组交替温态真实 provider owner 调用对照
旧实现：File 中位数 5.890→6.317 ms，SQLite 为 2.239→2.521 ms；p95 分别为
15.267→17.580 和 6.504→9.412 ms。原响应字段全等，计划元数据增加 405/415 个
序列化字节。此对照仅隔离同一 TypeScript 进程的 acceptance owner，不含 bridge、
CLI、冷启动或模型成本；此基础在 native 范围切换之前增加成本，不是效率提升。
在范围切换检查点，native 展示还增加精确必读产物及既有 codec 之前的一次 typed owner 调用，必须与
分页、补读和恢复成本一起计入；首包缩小不证明净收益。

native 切片通过 406 项真实入口 Python（五项显式 native/SDK 案例未资格）、169 项
TypeScript 和四项真实 Codex `0.163.0-alpha.11.1` 静态 loopback 检查。后者核实际
模型请求中的冷态全文、保留差分与同 capture 恢复字节，不证明自由生成模型行为。
四个新增 File/SQLite 范围断言在 `1ac29089f` 失败。集成中发现的恢复引用回归已修复：
只为显式 scoped 产物替换 metadata，普通恢复保留原文件字节身份。
中断回归最初因仍选中前次 capture 而失败；将当前恢复发布前移到可选 scope 调用之前
后通过。在运行源码 `e1c69289a`，标准 premerge 有两项在 `8b37000b3` 同样复现的失败：既有
`run_loopx_turn_once` 决策函数过大，以及前序 owner 收敛后 `turn_route` 的 producer
覆盖过期。本 native 增量未修改这些文件；历史结果不是通过或例外。集成已先 rebase
到 `b69021498`，再到 `f63647e2e`，保留目标主干的原生输出预算行为，并采用 [#7520](https://github.com/loopx-project/loopx/pull/7520)
对真实 TypeScript producer/scanner 的修复，并在 `fec9cd3af` 将原 capture 恢复从
run-once 编排中分离，保留既有 capture owner 与 session 恢复。大小阈值不变。当前
完整组合的资格归精确集成 head 的 PR 证据，不能沿用旧 native 切片的测试计数。

最终运行源码 `e1c69289a` 的成本含上述恢复发布顺序修复；对照不可变 `8b37000b3`（前序 C1 与相同 target-main 大页配置），24 成果/253 要求
来源上，每侧三组温态 cohort、每 provider/阶段 21 样本。温态新增真实 typed 依赖并核
先前未读正文的交付。File 冷态/温态/恢复的完整分页序列化输出为
105,380/40,473/103,372→37,908/33,495/38,594 字节。延迟中位数：

| 阶段 | File，原→新 ms | SQLite，原→新 ms |
| --- | ---: | ---: |
| 冷态全文输入 | 72.856→48.749 | 67.237→46.477 |
| 温态范围扩大 | 38.744→51.944 | 35.953→52.165 |
| 上下文丢失后全文恢复 | 109.126→61.425 | 109.725→56.777 |
| 索引加一项可选全文 | 96.918→108.557 | 92.460→109.251 |

在该次测量版本中，新增 scope RPC 仍在，温态 bridge 调用由二增三；File 温态产物文件共
131,020→160,192 个序列化字节。索引/详情仍为两调用，但增加派生展示重验；冷态和恢复由两页
变一页。测量含温态 runtime 下 Python native 投影、分页与生命周期，未控制机器负载；
不含首次验收收集、进程启动、子进程 CPU、模型/缓存 tokens、账单、人工/实施及验收
成果。冷态/恢复传输改善，温态及详情中位数退化；这不是原 quota 对照或完整成本正向结论。

在 `85c235d29`，native 调用方经同一批 owner 在一次请求中组合 scope 与编码，并删除
未被使用的全文 wire 副本；原独立 scope operation 已退役，普通 codec 和 capture
详情 reader 保留。相对 `307d000a3`，使用相同冻结 File/SQLite 观察、每侧三组交错
温态 cohort（每 provider/阶段 21 样本）测得：

| 阶段 | File 中位/p95，原→新 ms | SQLite 中位/p95，原→新 ms |
| --- | --- | --- |
| 冷态全文输入 | 37.438/60.718→28.516/38.941 | 34.263/39.924→26.966/39.054 |
| 温态范围扩大 | 42.065/58.640→34.813/45.922 | 39.869/49.047→33.856/45.593 |

计入分页生命周期观察，两阶段 bridge 调用均由三减二。File 冷态产物字节由
162,877 降至 129,299，温态仍为 160,192；完整分页输出不变。六组同路径的
普通/scoped/广域 File/SQLite 对照保留完整公开分页与恢复值，单调用回归断言在
基线失败。温态请求流量降低，但响应 payload 增加 27 字节。详情/恢复调用数不变；
SQLite 详情 p95 在未改代码路径下由 138.131 退化到 175.541 ms。机器负载仍未控制，
前述成本排除项仍适用。这去除了已证明的局部传输/IO 成本，不闭合原 quota 的成果/
完整成本缺口、短来源增长或全部温态产物开销。

小来源给出体积节省的反例：四个真实入口 File/SQLite 夹具各含两成果、四要求，
由 36,293–40,548 增至 37,948–42,203 字节，每项恰好增加 1,655 字节，索引和范围
元数据超过短可选正文。提案不从大夹具推导语义省略规则或生产体积阈值。

同一 24 成果/253 要求的合成来源，按旧的仅数量索引形状渲染分配 8,190 字节，
staged 状态分组分配 18,979 字节，原 12 KiB 索引预算失败。保留这些当前决策事实，
据此将独立索引上限调整为 20 KiB；history、horizon、完整 envelope 与生产性能
限额均不变。完全相同状态分组避免逐要求重复状态字段。这里只测序列化字节，
不是 token/延迟/成果收益或不可变 base/head 对照。上述源码范围检查和传输成本不证明
一般语义独立性或匹配验收成果/完整成本收益。

## 历史候选区块 2

External scheduler 候选在读取节奏或唤醒许可前，通过
`assert_complete_turn_decision` 复用现有 TypeScript 完整 reader owner 验证带版本输入。
这是纯读取：不重组已捕获的 host binding，也不生成旧 executor 视图。缺失 binding、
不支持的版本／必需语义、内容篡改和无效同 capture 引用均在 wake command 前拒绝。
无版本普通 quota packet 暂保留原兼容路径，退役须核对受支持的已部署 worker。
File／SQLite 源码 CLI 到 worker 验证使用本地子进程标记，不执行模型；这里只取得
该输入边界资格，不代表 attached host 任务交付、结算或验收成果／完整成本收益。

可选 Ark adapter 复用同一宿主输入 owner 的 `extract_turn_authority`／
`render_prompt`，无需在可选包里新增契约 builder。File／SQLite 的真实
`turn plan --host generic-cli --iteration-context fresh` 已将完整请求送入
真实 Ark SDK，并使用合成 HTTP transport 验证。检查对照实际 user-message
中的契约与原 CLI 来源，恢复持久化 running receipt 时不重新提交输入，重放完成
结果时不再调用 provider；来源／版本／binding 被篡改或完整输入显式为 null 时，在调用
provider 或创建 receipt 之前拒绝。Core 状态与记账保持不变，历史 Ark 请求和恢复
测试继续保留。这验证了 SDK wire 边界，尚未验证实际云端 session、模型成果或
Core 结算。Ark 与 MemT 现共用真实 File／SQLite CLI 请求夹具。MemT 在 provider IO
之前拒绝该完整输入，因为可执行的 legacy registry 不提供精确 `goal_ref`。
现有 `source_session_v1` 虽有精确生命周期，但属于 lifecycle-only；真实通用 Turn
规划入口会在 capture 或执行前拒绝它。规划后补造引用会掩盖这一生产者／消费者缺口。
完整输入损坏也会在 provider 或 receipt 创建前拒绝；历史 MemT SDK／snapshot 测试
继续保留，但不能证明端到端完整输入送达。MemT 路径的所属边界修复须将可执行 registry
准入的精确生命周期沿共享 Turn 请求与恢复 owner 传递，并保留重建及旧实例拒绝。
不能通过放松 MemT 隔离或启用 lifecycle-only profile 来绕过。这是已证实的采用缺口，
尚未取得原生记忆路径资格。operation-SDK 原生 runtime 与打包 App 采用也仍待验。
内部 effect envelope 与历史请求 decoder
仍服务结算／结果／会话 caller；只有这些 caller 和未结 receipt 已采用替代契约后
才能移除，不能仅因模型输入不再包含旧 envelope 就删除。

新请求的 Codex／DSH 共享 session lineage 已从完整契约读取，复用 TS 的
完整输入校验与自适应主任务选择 owner。模型输入校验使用同一 owner，并拒绝完整
输入与内部 effect adapter 的 Goal／Agent／Todo 不一致，即使两份摘要各自有效。
完整路径不再重复调用 Python 的版本与签名校验；历史请求继续使用原 decoder，
持久化 session schema、路径与 scope 不变。File／SQLite 真实 CLI capture、失败恢复
和一次结算重放通过；普通／自适应任务在 todo／agent scope 均能读回 Codex／DSH
原 session。相同夹具在迁移前基线保留合法路径通过，但新增身份冲突用例失败，
在候选通过。原生 operation SDK 环境仍有跳过，不能宣称其 runtime 已验证。
会话身份迁移未移除全部结果资格与结算的内部 envelope caller，尚未达到完整退役
或净成本收益结论。MemT 生命周期缺口仅阻塞
其依赖路径，不阻塞这些独立迁移。

规划与失败重试的 session 查找现在传递原始完整契约，journal inspection 也复用
同一身份 owner。缺省契约仍解码历史记录；显式 null 契约会拒绝。实时与缓存 vision
结果的归一化也复用该身份读取，不再从 effect adapter 重建 Goal／Agent。
会话 scope／生命周期门与原生 app-server callback 仍由既有 owner 负责。
本片移除这些仅用于读取身份的旧调用，尚未删除内部 adapter：执行路由、结果证据／
profile 判定与结算仍须迁移。
自适应恢复夹具在签名前构造：历史未验证选择仍按 primary Todo 写回；冲突的已验证
任务来源在 host 执行和额度支出前拒绝。既有来源校验门保持有效。
完成恢复验证读取原始持久完成及支出回执，不重新提交完成。四项过时的重复调用断言
在前一基线同样失败；修正后的验收保留真实 File／SQLite 中后续任务文本变化和历史
未带来源凭据但已经接受完成的恢复场景。

完成任务正文的解析现由既有 TypeScript interaction 来源 owner 统一负责。
同一次粗粒度读取现在向 `run-once` 提供原始选定工作、正文摘要、已准入动作、replan
与写回条件。完整来源校验在本次调用内复用已核对的 effect 投影，不新增 RPC 或面向
模型的契约。自适应 primary 与普通选择共用既有 interaction owner。CLI 不再从历史
adapter 读取这组执行事实，仍传递该 adapter 供来源／签名校验及尚未迁移的消费者使用。
缺省完整契约保留旧 journal decoder；显式 null 或无效来源不回退。Unicode／空白原始
字节、缺失／重复来源拒绝、原结算身份及已接受完成回执恢复继续受保护。
迁移前 `599cf1f72` 的 48 项 File／SQLite 捕获及完成检查通过；候选完整来源检查也
拒绝被修改的 binding 和显式 null 输入。最初正文摘要迁移将本地 Python 解析变为
一次 TypeScript 边界调用；本次扩展读取复用该边界，不抵销原新增成本。
完整 adapter 退役、其余 effect／material 消费者及原 quota 成果／完整成本资格仍开放。

结构化 Host 准备现在直接消费经过校验的完整来源，不再为新请求展开旧 action、
boundary、capsule 与 planning 载体。同一次 typed 来源校验提供可执行行动别名和
已准入 replan 事实，保留能力动作优先于 replan 的顺序，不在 Python 重写判断。
Codex prompt 的边界指导也读取完整来源，分阶段 reader 读取原始模型输入。
原 capture 元数据仍沿既有 transport 传递，来源及 adapter 完整性校验保持。
只有历史请求继续使用旧 decoder 与 renderer。这是内部消费者替换，不是另一份
面向模型的视图或跨 attempt 缓存；完整最终模型输入及其全部指令义务须保持一致。
其他 effect／session 消费者、打包采用及完整成本资格仍开放。

相对重基后的前序 `4c4e9b346`，冻结的真实 File/SQLite CLI 请求产生完全一致的
完整提示、结果契约、仅历史输入输出与无效来源诊断。Host 准备仍为两次 RPC；每组
请求合计减少五字节，响应增加 190 字节。六组顺序 cohort 使用各自经过核验的独立
runtime namespace，每个 arm／case 93 次温态采样，median/p95 毫秒值为：

| 请求来源 / Host | 前序 | 完整来源消费者 |
| --- | --- | --- |
| File / Codex | 27.544 / 32.366 | 31.258 / 40.408 |
| File / structured | 25.948 / 32.642 | 30.007 / 39.242 |
| SQLite / Codex | 25.695 / 31.070 | 31.889 / 37.696 |
| SQLite / structured | 24.409 / 30.258 | 33.242 / 42.287 |

这些是局部负向成本观察，不是加速。provider 标签只说明请求来源，计量组不执行
authority-store IO；机器及后台负载仍未控制。不含启动、其他调用、完整 Turn／模型／
缓存／账单／人工成本及验收成果。早期与验证重叠且未独立隔离的探索计时不作资格；
源码收敛和这些样本均不能证明相对原 quota 的净收益。

后续输入准备增量在同一次 Codex 启动内，由提示词和结果 schema 共用一次完整
来源校验。CLI 进程与启用 operation tools 的 app-server 都消费这一既有 adapter
边界。独立调用提示词／schema 仍各自校验，结构化结果转换在执行后重新校验来源；
不跨 attempt、模型执行或结果处理缓存校验值。typed owner 只返回行动、replan、
approval 和 profile 适用性事实，不再通过 bridge 原样回传完整来源。Python 消费
自己持有的原始已校验输入，并保留结构化 authority 值的独立副本。完整模型输入、
原 reader、启动 guard、结果验证和外层结算不变。

自有 V8 进程捕获 500 次基线来源校验，显示 canonical hash 与 Unicode key
排序占用明显 CPU；未覆盖 Python、transport 或 provider IO，inclusive 权重不是
节省时延。它支持移除同一次准备中的重复校验，不支持弱化完整性或跨执行复用。
原负面测量按历史保留；本增量不关闭 C1 相对原 quota 的成果／完整成本及采用要求。

相对重基到目标 `ff9e97f8e` 的前序 `75bd69bf6`，六组顺序交错 cohort、每 arm／case 93 次温态样本，
完整 prompt／schema／result、历史输出和无效来源诊断完全一致。Codex 输入准备从
两次 RPC 降为一次；结构化提示与执行后 candidate 转换仍保留两次。File／SQLite 的
Codex 请求字节为 86,293／86,399 → 43,144／43,197，响应为 62,554／62,606 → 211；
结构化请求各减少五字节，响应变为 422 字节。这里是内部 JSON payload，不含 framing。

| 请求来源 / Host | `75bd69bf6` median / p95 毫秒 | 输入准备增量 median / p95 毫秒 |
| --- | --- | --- |
| File / Codex | 22.147 / 26.204 | 12.003 / 13.653 |
| File / structured | 22.269 / 26.310 | 23.124 / 27.843 |
| SQLite / Codex | 21.656 / 26.511 | 11.734 / 17.183 |
| SQLite / structured | 21.324 / 23.608 | 23.716 / 26.079 |

Codex 局部准备成本改善，两个结构化时延仍是负向观察，不能被传输量下降抵销。
负载没有 store IO 或真实模型工作；各自有独立 runtime、顺序执行避免本 Agent 的
验证重叠，但后台负载仍未控制。不含启动、其他调用、整轮、tokens／缓存、账单、
实施／人工与验收成果；不是原 quota 对照，也不证明整体净收益。

Codex 结果 schema 与共享 candidate adapter 现通过同一经过校验的完整契约
profile 读取判断证据要求是否适用；新请求不再从内部 capsule 决定该要求。
该读取与模型输入校验复用既有 TypeScript 完整性／来源／身份校验器，没有新增
模型侧 packet 视图或按证据字段分别调用。缺省契约仍解码历史 capsule；
显式 null 或被修改的契约会拒绝。空／非对象 profile 保持原来的不适用行为，
停止结果仍不要求证据，已提供证据原样交给既有结果验证器。此次迁移的是适用性
判断，未改写证据内容校验，也尚未迁移全部执行路由和结算消费者。
schema／material candidate 每次读取各新增一次校验边界调用；这是语义收敛，
尚不能据此宣称性能改善。
使用同一真实 SQLite CLI host 请求（JSON 45,405 字节、合成 provider）进行
31 组交替热边界测量，schema 构造加 material 结果转换的中位耗时从
`68223e337` 的 0.013 ms 增至候选的 19.6 ms（p95 为 0.019／22.8 ms），
合法输出一致。保留这一局部新增成本的负向证据；测量不包括冷启动、模型工作或
完整 Turn，总体成果／成本资格仍开放。检查覆盖真实 File／SQLite 捕获恢复与
Codex／共享 host 消费者，不能证明打包 App 已采用或模型成果改善。

执行入口现在在 dispatch 或 journal 恢复前校验一次完整契约身份。结算计划缺失时
用该身份补建，恢复检查在同一次调用内复用它，不再读取 adapter 的 Agent。
已有结算计划与回执不重建；缺省完整契约的历史记录保留原解码与字符串处理。
该值不会跨 host 尝试、来源替换或后续调用缓存。此举同时关闭一个恢复绕过点：
此前已有结算计划会跳过来源校验，null／被修改的完整请求仍可返回已有 journal
结果。真实 File／SQLite CLI journal 的新拒绝保留 journal 字节与扣额数，
不调用 host 或结算 callback。十个定向用例在 `ce996ea0f` 的新增拒绝断言处失败；
候选 32 项捕获／恢复检查和扩大后的 605 项回归通过（相互重叠）。入口新增一次
校验边界调用，由两个消费者共用。同一份 45,405 字节真实 SQLite CLI 请求，
31 组交替热测已有结算计划读取与恢复身份，合法输出一致：中位数从 0.016 增至
12.382 ms，p95 从 0.024 增至 14.883 ms。这是新增局部校验成本，不是效率收益；
不含冷启动、完整 Turn 与模型成本。完整路径性能及其余路由／结算退役仍开放。

Codex CLI 构建提示词时，有效完整请求现在直接调用既有完整校验 owner，删除重复的
单独 envelope 版本调用。历史请求保留版本检查；完整校验失败时仍检查版本，以保留
两处同时损坏时的诊断优先级。未引入校验缓存、新 wire 字段或第二 owner，提示词正文
不变。这是调用边界合并，不代表剩余 driver 路由或结算 adapter 已退役。

对照 `e1dcd95dd`，完整与历史提示词字节一致，六类无效输入保留相同拒绝诊断。
同一份 45,429 字节真实 SQLite CLI 请求生成 34,043 字节提示词；61 组交替热测中，
提示词构建中位数从 24.229 降至 14.819 ms，p95 从 30.189 降至 17.854 ms；
桥接追踪确认两次调用变为一次。局部降幅约 39%，不含冷启动、模型执行和完整 Turn
成本，不抵销单独测得的 profile／结算开销，也不证明相对原 quota 的成果收益。
诊断补充前的较早样本为中位 21.279／12.994 ms、p95 29.729／17.321 ms；保留其
早期证据身份，不作为第二份独立的最终资格。

规划现在在构建路由、会话和事务之前接收完整契约，不再在计划完成后附加它。
规划与失败会话恢复通过一个粗粒度身份操作复用既有 TypeScript 完整来源校验；
同一次边界调用选择 Goal/Agent/工作身份并返回动作签名前像，替代原先仅取签名的
调用，不增加逐字段 RPC 或缓存。显式空值、不支持版本、被修改的完整契约均拒绝，
已处于 resume 的请求也一样；缺省完整契约的历史输入保留原 decoder。
Todo-less replan 身份仍由回执 owner 提供；下文路由与启动增量记录后续消费者迁移。

Python JSON/hash 传输编码特意保持不变。直接采用 JavaScript 哈希的试验未通过
小数／指数前像一致性检查，会改变已持久化的 Turn key；File/SQLite 特征化现在
覆盖这些数值、Unicode、自适应工作选择和重复恢复。一个真实 SQLite CLI 请求
（45,380 JSON bytes）相对 `088867421` 的 31 组交替热态采样，完整／历史计划及
失败恢复输出保持一致。规划 median/p95 从 39.632/47.964 变为 42.608/53.270 ms；
恢复从 12.330/20.858 变为 18.136/23.242 ms。这是局部增加的校验成本，不是完整
Turn 或原 quota 成果对照，也不代表模型、已安装 App 或 PostgreSQL authority
资格。剩余路由／材料 adapter 退役及端到端成本资格继续使 C1 保持开放。

Host 启动前实时复查现在将回执绑定的实时观察交给初始规划所用的同一完整契约
composition owner，再把完整契约传入共享 planner，替换原先 raw quota 直接进入历史
规划的捷径。Stop 和动作变化仍先于 composition 拒绝；实时观察不替换原始 capture
中的模型输入，也不生成另一份回执。剩余 effect／材料 adapter 仍需退役。

路由分类现在归既有 TypeScript envelope owner 所有。显式注册 action／frontier 表在
编译期检查穷尽性，历史未知 action 仍进入 Host；签名拒绝、attempt、先 replan 后
普通 delivery 门、能力 intent 身份、repair、用户行动和安静等待保留原顺序。
Python 仅保留 controller 薄传输与历史 scalar-text 编码，不再拥有第二份分类表。
完整契约校验忽略历史解码提示；已持久化动作 hash 的 Python 编码及历史工作选择
reader 均保持不变。

规划在既有粗粒度身份／签名调用中同时取得 route；完整与历史普通计划仍各需两次
桥接调用。相对旧 Python 分类器，1,152 组 action／gate 组合一致；固定真实 SQLite
CLI fixture 的完整与历史计划输出也一致。计划对照共用当前 TypeScript endpoint，
因此不是精确旧版／新版端到端延迟实验。controller 单独读取 route 现在新增一次
桥接：44,891 字节请求的 61 组交替热测，median/p95 从 0.004792/0.010125 升至
13.252792/15.855042 ms。这是该 caller 的实测回归，不是效率收益；作为 controller
调用边界合并的明确成本缺口保留，不引入缓存或重复分类器掩盖它。File/SQLite
入口、恢复、一次结算资格，剩余 adapter 退役、打包采用和原 quota 成果／完整成本
资格仍分别验收。

生产 `turn managed-step` caller 现在将 envelope 构造与同一路由求值合并在一次
TypeScript 请求中。Python adapter 返回绑定 route 的本地不可变 envelope 快照；它
不是新的 Agent wire 版式，也不拥有第二份决策规则。读取或修改返回的字典不会
改变快照，也不能把它的资格附到另一份 wire 字典。controller 保留既有用户行动
优先级、回执／前驱校验和 journal 所有的重试预算；普通历史或外部 envelope 仍走
原 route reader 与签名拒绝。构造 adapter 和普通 envelope builder 共用宿主绑定及
capture 参数准备，删除 managed caller 原本多余的中间 builder。上段独立 reader
回归保留为历史实测；只有生产中新生成与消费的调用对能够省去这次额外请求。
剩余工作选择／effect／材料解码、大型冷上下文恢复、打包与成果资格继续开放。
caller 直接消费统一 typed controller 结果后，本地快照随之退役；它不新增持久化
schema 或兼容 wire。

固定 31,088 字节合成来源的 61 组交替热测，将 `ddf96b79c` 的原 Python producer／
controller 函数与合并调用比较；共用当前 TypeScript 服务和未变的 build／route 规则。
最终源码的组件 median/p95 为 22.477/25.417 对 13.739/17.479 ms。另以真实隔离 File/SQLite
store 经公开 `managed-step` CLI，各 provider／arm 取 21 组交替热测：File median
为 135.989 对 137.316 ms，SQLite 为 149.310 对 146.081 ms；p95 分别为
209.730 对 192.544 ms、222.299 对 240.848 ms。另行传输计数探测发现每个 provider
的桥接总请求数从 70 降为 69：省去一次 route 请求，其余 live-decision 调用仍在。
完整输出一致，失败 journal 逐字节不变，两臂均不扣额。请求数改善，但整条 CLI 的
median 和尾部结果混合，尚不能证明加速；本地小
样本及共享机器资源限制尾部推断。这是具名源码边界的观察，不是整个不可变旧树
对照、冷启动、已安装 App、模型成果或原 quota 完整成本资格。

完整 reader 现在还将 adapter 的已签名动作文档，与原完整来源经过既有 producer
投影的结果比较。原先来源和 adapter 各自的 hash 只能证明各自完整：重新计算 hash
后的 adapter 可以保留 Goal/Agent/Todo，却改变执行或写回条件。共享校验复用既有
签名维度，不新增第二份字段清单、RPC 或 wire 视图。仅含历史输入时保留旧 reader。
完整请求的 profile adapter 与来源不一致时，现在明确拒绝，替代此前忽略冲突、直接
采用来源 profile 的行为；这是有意的拒绝语义变化，历史 profile 行为另行保留。

相对 `b9e2c5120`，File/SQLite 的规划拒绝断言在修复前失败。六类独立重算 hash 的
反例覆盖运行资格、有效动作、交付许可、用户介入、执行策略和扣额许可；候选在
host／写回／扣额回调前拒绝，不产生 Turn journal 或结算回执。既有 lane 锁仍可能
先分配。一个 44,891-byte 真实 SQLite CLI fixture 的 101 组交替热态进程内
TypeScript 采样保持合法身份输出一致；校验 median 从 4.864 增至 5.163 ms，p95 从
5.570 增至 6.010 ms。这是局部新增成本，不含桥接、实时 composition、冷启动、模型
或完整 Turn 成本，不证明相对原 quota 的成果收益或 C1 切换资格。

候选的普通 planner 与失败会话恢复现在也从既有 `driver_lineage` 调用消费选中工作。
共享 `selectedWorkTodo` owner 将普通或自适应主任务选择写入该调用已有的动作 hash
preimage；Python 不再重选或重写 preimage。Python 保留已持久化 JSON／数字编码，
同时运输历史 primary-ID scalar／空白编码；共享 owner 消费已解码历史文本，不再重复
执行 JS trim。完整输入忽略该提示，并保留
完整来源／投影一致性校验。不新增 bridge 调用、模型侧字段、持久化 schema 或跨
attempt 缓存；公开动作签名操作仍描述原 envelope。

这替换了实际规划和失败会话恢复 caller 中的选择解释，包括原生 CLI 使用原 capture
重试的路径。剩余 v0 Host、结算和 controller reader 仍使用历史选择 decoder，只有其
caller 与未结 receipt 已迁移且通过等价恢复资格后才可退役。本片不验收完整 adapter
退役、App／SDK／benchmark 采用或优于原 quota 的净收益。

对不可变 `334c1a772` 的资格保留了冻结 File／SQLite CLI 输入上的完整规划／恢复
输出；规划仍为两次 bridge 调用，恢复为一次，普通输入的请求／响应字节量相同。
实际 CLI／provider／Host、完成及结算模块 254 项通过；十个迁移断言在前序失败，
十二个历史编码用例在前序通过，候选全部通过。后者覆盖缺省／false／zero、空白、
数字、布尔与 collection-valued 历史 primary ID、Unicode 空白／BOM、版本／mode
适用性、原 Turn key
及恢复绑定。

临时 `ef479d2b5` 试验改变了三种 Unicode 空白／BOM 身份：语义反例在该版本失败，
在修正 owner 后通过，而原始前序保留这些身份。下述该版本测量仍是历史试验，
不作为修正源码的资格。

尚无延迟改善资格。三组平衡顺序对照使每 arm／case 有 93 个温态样本。最初以
实际 source worktree 对比归档基线，八个普通／历史规划和恢复行的候选 median
均升高，但两侧生成缓存清单不同。第二组控制使用干净同级来源副本、相同清单并在
两侧关闭 bytecode 生成，仍观察到混合或更慢结果：

| 完整来源输入／操作 | 初始基线 → 候选 median / p95，ms | 干净控制基线 → 候选 median / p95，ms |
| --- | --- | --- |
| File／规划 | 35.129 / 40.776 → 46.227 / 57.355 | 24.174 / 30.183 → 25.330 / 30.443 |
| File／失败会话恢复 | 14.941 / 17.957 → 19.440 / 24.477 | 11.259 / 13.196 → 12.582 / 15.495 |
| SQLite／规划 | 36.979 / 46.065 → 46.461 / 54.795 | 23.820 / 30.178 → 26.408 / 30.523 |
| SQLite／失败会话恢复 | 17.404 / 22.606 → 18.759 / 22.139 | 12.586 / 16.141 → 11.503 / 14.124 |

初始负结果保留；控制对照不追认历史通过，也不证明负结果的原因。这些是不含 store
IO 的直接 helper 观察，不是完整原生 CLI／Turn、冷启动、模型成本或验收成果；后台
宿主负载未控。另对自有进程取得的 V8 capture 指向既有 Unicode key ordering／hash
计算，但未证明延迟根因。可选 profiler 未安装，Python wait／IO stack 仍未观察。
归属统一和字节相同不能抵销这些成本缺口或此前 C1 的负测量。

修正后的三个源码文件另有干净来源对照，同样每 arm／case 93 个温态样本，八项
完整输出相同，RPC 与普通输入传输量不变。结果仍混合，SQLite 规划尾延迟更差；
同样保留上述未测范围和后台负载未控限制：

| 输入／操作 | 基线 median / p95，ms | 修正源码 median / p95，ms |
| --- | --- | --- |
| File／完整规划 | 38.529 / 48.381 | 38.504 / 47.134 |
| File／完整恢复 | 17.821 / 22.029 | 18.244 / 21.857 |
| File／历史规划 | 32.787 / 44.413 | 30.871 / 36.733 |
| File／历史恢复 | 10.635 / 14.620 | 10.292 / 12.201 |
| SQLite／完整规划 | 39.600 / 46.547 | 40.036 / 77.714 |
| SQLite／完整恢复 | 18.034 / 28.685 | 18.552 / 25.223 |
| SQLite／历史规划 | 32.061 / 42.789 | 32.447 / 38.962 |
| SQLite／历史恢复 | 10.444 / 14.258 | 10.270 / 12.891 |


#### 原生执行与完成来源

原生启动 guard 现在消费共享 planner 已返回的选中 Todo，不再调用历史 Python
selector 重选。既有执行上下文／任务承诺 reader 归 Turn driver 所有，同时供 CLI
准备和完成意图分类使用；CLI 副本已删除。语义 owner 仍为既有 TypeScript
`execution_context` 操作，没有新增视图、schema 或 Python 决策 owner。

Host 执行和独立任务验证之后，完整请求在判断终态或普通完成前重新检查该来源。
已保存的 writeback 或 terminal outcome 不能跳过检查。无效完整输入或
与来源冲突的 adapter 事实在写回／扣额前拒绝；原 journal 随后可以恢复已有验证结果，
不再次调用 Host，并只结算一次。省略完整输入的历史请求保留精确 decoder 和
回执恢复。完成 outcome 验证及 typed 结算／effect owner 不变；本片没有退役所有
历史完成或 controller reader。

启动 guard 不增加 bridge 调用。完整 validated-completion 分类在此后续边界增加
一次粗粒度来源检查，包括未完成 journal 恢复；历史分类不变。这是明确新增成本，
不代表提速，也不能据此跨执行、attempt 或恢复缓存执行前检查。原 quota 的实际
决策、验收成果、完整成本及剩余消费者采用仍保持开放。

四个受影响的 capture／driver／completion／settlement 模块共 264 项通过，使用
真实隔离 File／SQLite CLI 消费者及合成 Host／validator 进程；两个 typed owner
模块 79 项通过。22 个迁移断言在不可变 `2a170247ba3` 失败、候选通过。新增的
8 个原生替换反例在基线会提交完成结果，候选在写回／扣额前拒绝，随后从原
journal 恢复已有结果，不重跑 Host 并只扣额一次；覆盖两后端的普通／最终完成。
这些不证明真实模型或已安装消费者采用。

三组平衡顺序对照使用干净同级来源副本与相同夹具，每个温态 helper 行各有
93 个样本。完整 intent／recovery 分类从约 0.002 ms、零 bridge 请求变为：
File median／p95 分别 19.813／26.454 和 20.770／25.287 ms；SQLite 分别
20.243／23.964 和 19.005／22.458 ms。每次新增一个逻辑请求，File／SQLite
请求分别 43,200／43,253 字节，结果均 1,693 字节。历史分类仍零请求、结果等价。
逻辑 JSON 计量不含 framing 或物理传输重试。

同组对照还运行真实 CLI 准入、捕获、合成 Host 工作、独立验证与结算，再通过
原生入口回读 Todo 并重放已提交结果。每行每 arm 仅三个样本；下表为 median
和实际范围，不推 p95 或因果提速：

| CLI 完成 | 基线 median [范围]，ms | 候选 median [范围]，ms | 逻辑请求，基线 → 候选 |
| --- | --- | --- | --- |
| File／普通 | 2303.0 [2001.9–3646.5] | 2173.5 [2051.4–2196.6] | 282 → 283 |
| File／最终 | 2306.0 [2283.4–2389.4] | 2818.4 [2338.2–2973.9] | 294 → 295 |
| SQLite／普通 | 1888.5 [1758.5–2061.7] | 2513.5 [2086.5–2955.7] | 278 → 279 |
| SQLite／最终 | 2549.1 [2240.7–2681.2] | 2282.7 [2276.8–4064.8] | 294 → 295 |

原生结果观察一致，包括原结算身份、done 回读和一次扣额；两侧已提交重放均为
五次逻辑请求、零新增 effect。回读／重放观察与执行计时分别保留。后台宿主负载
未控，这些对照未与其他自有验证并行。新增来源检查是实测成本；完整 CLI 时延
仍混合，默认 SQLite 性能尚未资格。模型／缓存 tokens、账单、评测、实现／人工
成本、真实工作验收成果和优于原 quota 的净益仍未知，所有既有负结果保持。

## 历史候选区块 3

[数值完整性检查点](2026-10-29-complete-numeric-integrity.zh-CN.md)
让 producer 数值身份与全部完整 reader 共用一个 typed owner，并删除无用 Host echo。
后续[稀疏运输整合](2026-10-29-sparse-numeric-integration.zh-CN.md)
采用既有 context collector，不保留完整路径专用的第二规则。变化的实验 hash 身份由
codec v2 明示；旧 binding 的 capture 不变，保留对应版本固定的 reader。原 capture
字节仍单独校验。组件运输减少不认证原 quota 成果、完整成本或 live writer 切换。

## 历史候选区块 4

#7430 的验收共享增量把原 Python 展示算法移入既有 TypeScript composition owner，
在 hash/capture 前执行；旧展示 helper 只委托同一实现。完整契约 reader 与历史分阶段
读取共用一个引用解析器。相同契约保留一份完整 owner；不同 revision、JSON 类型、来源
和负面证据分别保留。已有引用随 owner 的迁移同步调整；即使完整性 hash 正确，悬空、
循环或不可用引用也会拒绝。引用比原对象更大时仍保留短对象原文。

隔离 File/SQLite 的 quota 和 native host fixture 覆盖真实合成 Codex 子进程输入中的
完整验收、失败后恢复和原身份一次性结算，也验证共享后的 full/delta/丢失基线重建。
这些证明传输语义，不证明模型遵从。旧展示 adapter 增加一次传输调用；完整契约在原有
composition 调用内部完成共享。未声称延迟改善。

固定 source 的四份相同验收 fixture 中，融合契约为 23,432 个紧凑 UTF-8 JSON 字节，
原未分阶段 CLI 为 22,237（+5.37%）。启用 capture 分阶段时，融合契约为 23,515，
原 entry/review/writeback 合计为 24,509，未计追加材料读取。对照冻结 `895f38fc5`
的原展示共享 helper；上述不含验收副本的 workload 数字不变。这把 quota 已有的效率
吸收到共同路径，不能证明优于 quota 或完整成本获益。下述 guidance 增量进一步缩小缺口；下一步先在既有 owner 验证剩余
vision 共享；标题由下述增量处理，然后再评估延后详情。冷启动与分阶段总量分别保留，
保留负结果，不能靠删除独有指令达到字节目标。

#7430 后续增量通过同一个 TypeScript composition 边界共享 replan 的 vision-authoring
指令，保留的 owner 是既有 durable-writeback 结算步骤。旧 Python 展示 helper 委托
共享判断，仅添加历史展示元数据。完整 reader 和历史未分阶段／分阶段 adapter 解析同一个
精确引用，不另建 authoring 策略。JSON 类型、版本或指令不同则保留原文，引用必须更小。
即使 hash 匹配，冲突的原文／引用、错误步骤类型、缺失目标和引用链也会拒绝。
结算计划、全部原指令和示例保留在 capture 中；历史 writeback 阶段维持原有的示例省略。

两个旧 CLI 参数写法现在暴露同一份共享完整契约；材料回读、产生效果前拒绝和 native
输入分别验证。已验证的 Native 外层 controller 输入没有内联结算 authoring owner，authoring 仍保留原文；
共享不会虚构该 owner 或转移结算权。authoring 检查点的四项旧 projection 测试期待
完整候选尚未采用的多行标题或 Explore 指导共享，在此前及 authoring head 都失败。
下述 guidance 增量解决其中两项，标题增量解决另外两项。历史失败继续保留，不豁免或改写。
这些切片不证明已安装 App 的采用。

固定的合成 authoring fixture 中，原未分阶段 CLI 为 22,062 个紧凑 UTF-8 JSON 字节，
融合契约为 23,266（+5.46%）。启用 capture 分阶段时，原 entry/review/writeback 合计
24,311，融合为 23,349。原 CLI projection 模块冻结在 `895f38fc5`，输入及投影读取阶段
保持一致。追加读取、请求包装、tokens/cache、延迟和模型成果尚未测量。共享消除了
候选中的重复实现和重复指令，但不能证明优于 quota 已有共享或完整成本 diff 正向。


Explore guidance 增量删除 Python 的精确匹配循环，完整契约与旧展示调用者复用同一个
TypeScript composition 边界。仅当指令完全一致、已有 durable-writeback Explore
附件 owner 且引用更小时，才共享同一 capture 的 owner。独有／短文本及缺失 owner
保留原文。非法、冲突或成链引用拒绝；不缩短指令，也不给可选附件新增义务。
历史分阶段及未分阶段 reader 在移动上下文前解析引用，再复用 Explore 既有领域投影。
独立 Explore 读取仍返回完整指导。兼容 helper 随旧展示调用者退役，不拥有独立决策或共享算法。

真实隔离 File／SQLite CLI capture 现在通过此前失败的两项 Explore 共享用例，
包括独立材料对照、历史回读及非法 capture 在状态／额度不变时拒绝。
既有分阶段 CLI 测试仍执行 registry 不可用时读取、篡改拒绝及实际写回／结算。
差分重建保留变化后的指令并支持 baseline 丢失恢复；不完整上下文回退完整交付。
这些证明传输及本地集成，不证明已安装 App 或模型成果。


扩大调用者验证也将旧测试假设迁移到准入后的完整契约：native 子进程 capture／重试
比较整份原始契约，生成的结算命令仍仅结算原 Turn。历史 capability reader 维持
显式初始化隔离 canonical fixture、拒绝事实及 scheduler 信息，不因此授予执行权。
capability 准入被拒时仍交付 typed diagnostic／历史投影，准入成功才交付完整契约。
这个失败路径迁移仍属于 C1 切换前缺口，不是永久第二套契约。
合成 native／stdio provider 不证明模型收益。

同样冻结 `895f38fc5` 原投影，合成 guidance fixture 的原未分阶段路径为 22,848 个
紧凑 UTF-8 JSON 字节，融合为 24,320（+6.44%）。启用 capture 后，原
entry/review/writeback 合计 26,081，融合为 24,403。不含 guidance 的 fixture
未分阶段仍为 20,543 对 21,738 字节。必需追加读取、请求包装、tokens/cache、延迟及
模型验收成果尚未测量。代码共享及恢复 quota 已有的信息效率不能证明完整成本 diff 正向；
C1 继续保持未完成。

当前 Task 标题增量删除 Python 的精确来源匹配算法，完整 composition 和旧展示委托
共用 TypeScript owner。只有与唯一、已核验、完整的当前选中 Task 的 `text` 或 `title`
精确重复时，才转换为同一 capture 的 `title_ref`；完整原始声明及来源继续内联。
来源缺失、不同、不完整、歧义、失败、已完成或已归档均不符合共享条件。历史字符阈值
（包括 Unicode 码点计数）保持不变。旧展示按 label 前缀省略标题的逻辑留在展示层，
不应用于完整契约；委托 helper 随旧展示调用者退役。

完整 reader 再次验证身份及引用归属，即使 hash 有效，也拒绝冲突原文、引用链、
非法路径及不可用目标。历史 envelope 保留完整 Task 来源，不携带移动后悬空的 lane
引用。真实 File／SQLite CLI 在差分关闭时已通过此前两项多行标题用例；capture 变体
还验证精确回读及状态／额度变化前拒绝。差分及 baseline 丢失／全量恢复保留变化后的要求；
独立 Todo detail 仍返回完整声明。这证明本地传输语义，不证明已安装 App 或模型行为。

固定合成当前 Task fixture 中，冻结 `895f38fc5` 的原未分阶段 CLI 为 28,585 个紧凑
UTF-8 JSON 字节，融合契约为 29,780（+4.18%）。启用 capture 后，原
entry/review/writeback 合计 29,906，融合为 29,863。不含当前 Task 的控制样例
未分阶段仍为 20,543 对 21,738 字节。这些总量未含请求包装、追加材料读取、tokens/cache、
延迟及验收成果；它们将 quota 已有共享恢复到共同路径，不能证明完整成本收益正向。

vision audit 增量现已将 Python 嵌套精确匹配算法迁入同一 TypeScript owner。完整契约
和历史展示只在引用更小时共享完全相同的 audit，包括未知事实、负面证据及有序义务。
不同 JSON 类型不相等：旧 Python 的 `True == 1` 比较可能抹掉 channel 差异，现保留
该差异原文；短 audit 也保持内联。根保留完整 audit 和 judge，旧展示 metadata 留在 adapter。

完整恢复即使面对重新计算的有效 hash，也拒绝缺失、非法、链式或冲突的 audit／judge
引用。历史 envelope 仍从内联根取得 judge，不产生移动后的悬空引用；其已有投影限制
不变。真实隔离 File／SQLite CLI 在含活动历史 vision、差分关闭时，验证完整 judge、
精确 capture 回读及状态／额度变化前拒绝。差分／全量及 baseline 丢失恢复保留变化的
要求。没有活动 vision 的 fixture 正确地不产生 audit，不能用来验收这条路径。

冻结原 CLI projection 于 `895f38fc5`，固定合成 audit 样例的未分阶段紧凑 UTF-8 JSON
为原 22,337 字节对融合 23,333（+4.46%）；capture 原 entry/review/writeback 合计
23,658 对融合 23,416。不含 audit 的控制样例仍为 20,543 对 21,738。它们不计
tokens/cache、延迟、追加读取、请求包装及验收成果。恢复共享、减少规则 owner 仍不
证明完整成本正向。旧 audit 委托随展示调用者退役；剩余 consumer 退役及打包／成果
资格继续使 C1 保持开放。

拒绝准入现在只要已有 interaction contract，就在不可变 capture 前使用同一完整
composer，包括 health 为 false 的暂停来源。`ok=false` 仍阻止 host handoff 和原生
context baseline 发布，保留失败退出码；组合不授予交付或结算权限。非法完整来源在
capture 前验证失败。工作契约形成前的错误则在两种 CLI flag 写法下保留原 typed
诊断及恢复事实，不补造工作契约，也不再经过历史 envelope renderer。

真实隔离 File／SQLite CLI 覆盖 App heartbeat 和 generic 两种 profile 下的工作
缺失、能力不可用及 selection-required reentry。重入保留原 receipt，不产生额度
记账、baseline 或越权状态修改。Capture consumer 正迁向完整原件精确回读，同时
显式保留历史长路径恢复覆盖。这验收本地传输和拒绝语义，不代表已安装 App 采用或
全部 provider／入口交付。剩余 consumer 退役及打包后的单会话成果／完整成本资格
继续使 C1 保持开放。

后续观察验证区分了两个问题。#7464 修复测试对两次独立实时观察时钟的误比，同时
保留其他全部事实。#7468 让 `quota status/plan` 保留既有权威 owner 的 typed 拒绝
及备份／迁移指引，修复提前返回错误时丢失这些信息的问题。按照已接受的 #7032
截止规则，未迁移 Goal 的普通读取继续拒绝；File／SQLite 保留范围隔离断言。
新的恢复断言在旧实现上失败，修复后的范围／诊断／权威集合 156 项通过。这些可独立
评审的修复已由集成候选吸收，不代表安装采用或成果／成本改善。早期失败运行仍作为
历史证据保留；这几项已归因失败不再列为未完成调查。

固定 unhealthy-paused 合成来源在原 CLI projection `895f38fc5` 下为 6,325 个
紧凑 UTF-8 JSON 字节，完整契约为 6,639（+4.96%）。新增 binding 保留统一验证，
这不是效率收益；该样例的 tokens/cache、延迟、追加读取、请求包装及验收成果仍未
测量。这些检查不代表启动过真实模型实验。

共享 reference owner 已通过 #7457 合入。Task 标题与 Explore 操作在旧投影中合用
一次粗粒度调用；完整 composition 在既有调用内部复用相同函数。集成分支吸收
前置提交，不复制一份 owner。新的 `should-run` emission 只接受已组合契约或
原始 predecision 诊断，不再回退旧 packet／envelope 投影；历史 capture 恢复
仍为显式路径。组件测量显示桥接成本比两次调用更低，但高于原 Python 投影，
不能作为完整 quota 路径净收益的证据。

[打包 heartbeat 对照](2026-10-28-packaged-heartbeat.zh-CN.md)
已使用真实隔离 File/SQLite CLI，相对原 quota 核对冷态、温态和同 capture 恢复。
首组默认上下文和 stdout 增长。后续让旧调用方和 scoped native 复用同一个 typed
诊断 owner，删除对应 Python 算法；可独立使用的旧读取方切片已在
[#7557](https://github.com/loopx-project/loopx/pull/7557) 提案，集成版本使用相同模块。
未知字段／列表、完整保留条目、候选和 portfolio 不变。重跑后冷态／恢复约少 5.3 KB，
温态仍多约 2.8 KB；全部 peer 补读仍增加 13,940 字节和十次 CLI。公开 v4 预算探针
在前一版及当前集成 wheel 上仍失败。完整成本正向、默认性能及模型收益均未资格化。
继续在原 C1 诊断温态重建／元数据成本及剩余消费者，不提升默认，也不验收独立的
controller 实验。

集成版本也在相同 context codec 内采用已合入的
[#7528](https://github.com/loopx-project/loopx/pull/7528) overlay：搬移的精确证据可共用
一份基线子树，当前及未知控制仍字面交付，包含 C1 scoped wrapper。Ledger 将局部
搬移的正向字节结果与重跑的打包对照分开：普通温态输入仍多约 2.8 KB，v4 预算仍失败。
这移除了已证实的传输冗余，尚不能证明完整周期或成果收益正向。

上述成本与预算结果仍绑定各自 ledger 版本。[后续主线集成](2026-10-28-packaged-heartbeat.zh-CN.md#主线集成与不可变-reader-身份)
修复原生投影及原 capture reader 的接线，未重跑或认证那些模型／完整成本对照。
后续[签名完整输入材料分阶段检查点](2026-10-28-packaged-heartbeat.zh-CN.md#签名完整输入的材料分阶段)
让已验证完整输入复用相同材料 owner，并在范围筛选后保留材料的原始位置。
当分阶段呈现的传输字节更少时，该消费者不再回退完整材料。这不改变 Host 默认阅读
策略，也不认证模型保留、完整成本、剩余调用方／writer 迁移或历史 reader 退役。
C1 及相对原 quota 基线的验收保持开放。

## 历史候选区块 5

### 本地注册 Goal 材料交付检查点

完整 Turn writer 和现有 native context-delivery 显式启用路径，现在在 capture 前
请求完整的注册 Goal 材料。源 adapter 为未标记或混合文件返回原始 UTF-8 全文；
现有 TypeScript context owner 核对完整文件 revision 后完成该项读取。已标记区域
的 lineage 和 canonical Todo 权威保留。普通 quota 在 delivery 关闭时继续渐进读取。
桥接要求 `complete_goal_v1` 回执；旧 runtime 不能静默忽略这个要求。

自动业务执行的 Host 上下文使用 `host_dispatch_context`，在加载 provider 前拒绝未完成或无效的
Goal 材料。历史 `host_context` 元数据读取继续用于按原版本解释与必要结果收尾；
已保存的 capture 不被重写。本项是范围有限的材料交付前置。通用 capability 必读
强制、当前条件复核、安装采用、真实模型遵守和完整成本，仍各需独立证据。C1 未获
完整资格。Replan 材料读取调用仍按原有阶段与工作限制执行。

### 当前偏好首读集成

C1 候选接入 [#7935](https://github.com/loopx-project/loopx/pull/7935) 的既有 selector
修复：首读交付偏好 owner 完整的当前作用域视图，包含纠偏、退役、空替换、来源和
advisory 权限边界。仍复用同一 selector 与来源 owner；不改变启用设置、不授予
权限，也不替代后续动作要求的精确作用域新读。

集成验证复用 native capture reader 和 executor 投影，不再要求完整来源根部携带
旧 capsule，也不把导航当作内联决策。验证保留 delivery-off 的渐进 Goal 读取，
并核 native 默认路径的完整内联 Goal、canonical Task、后续要求更新、偏好存储
损坏和签名拒绝。五处旧布局假设在集成前候选上同样失败；适配测试后，六个完整
模块 143 项通过。确定性 loopback provider 下实际 native exec、CodeMode 两项
通过，六个 TypeScript 模块 124 项通过；这些计数只对应各自范围。

完整 CLI 预算模块仍有四个测试组失败、39 项通过和一个环境相关跳过。实测小型
Markdown、拥挤 plan 和兼容视图仍超限；原五个体积门及历史测量均保留。不降低
上限、不缩 fixture 负载或生产指令来隐藏失败。本项只交付源码集成，不代表完整
C1 质量回执通过、merge readiness、安装采用、自然模型行为或完整成本收益。

### Canonical Todo 投影成本资格

在 main `f941b5939` 与 C1 `c59777597` 上做匹配观察，使用既有
`todo project-markdown` 操作及新读到的 provider revision。File/SQLite 各覆盖
一个 Todo、36 个 Todo 的无／有 Vision，以及三个 Agent 的 18 个 Todo：共 16 次
投影转换、32 个表面条件、64 份前后 quota/plan 响应。每次转换的完整 canonical
记录逐一相同；独立核对完整文件及被排除区域 hash，重建实际交付的 Goal 正文，
包括末尾停止条件。未标记来源仍保留全文；只排除正常 writer 生成的区域。不改变
source adapter、投影策略、输出阈值或 fixture 负载。

所测组件为解析后的规范 CLI argv JSON 加实际 stdout，并计入响应要求的 Goal
`cat` 命令。在 C1，投影使拥挤 plan 的一次观察在无／有 Vision 时分别少
7,698／7,697 字节，multi-agent plan 少 3,697 字节；小型 quota/plan 反而增加
364／75 字节。投影本身的所测成本为 1,667–1,700 字节；若将两次完整 canonical
读回也作为一次性审计成本，正向样本的条件回本点推迟到重复 7–9 次相关观察。
这是记账情形，不是每 Turn 重新生成的建议。

既有投影也使 main 获益。两快照均使用生成 Goal 区域后，C1 plan 仍多 23–24
个所测字节；quota 的小样本多 959，拥挤无／有 Vision 分别多 1,958／1,759。
普通 multi-agent quota 在两个快照均没有内联 Goal 或 Goal 读取，故不纳入 Goal
等价成本主张。上述结果资格化既有来源／投影路径，不是 C1 新优化、完整决策语义
相同、模型采用或整体净收益证明。RPC、CPU、账单、成果价值及自然模型使用未测。

原未标记来源的预算 case 和五项历史门保持不变。另行投影的负载越过阈值，并不
修复或改写那些失败。继续沿原 writer/reader owner 和相对原 quota 的单会话验收，
不删除未标记 Goal 正文、不放宽签名来源回退，也不为这些字节新造压缩算法。

### 通用 Host 派发与已保存结果恢复

共享 executor 现在在记录、确认或启动新 Host 尝试前，调用既有 TypeScript
`host_dispatch_context` owner。此前，一个来源承诺有效但 Goal 正文仍有未完成
读取的完整契约，可以经通用路径到达自定义回调或外部 argv；具名 adapter 已有
各自检查。修复不新增决策 owner、状态、词汇、通用 capability 必读义务或启用设置。

十四项 File/SQLite case 覆盖回调及真实 argv 的合法、Goal 待读、历史请求，以及
已保存 typed result 恢复。待读 case 没有 Host 副作用；合法与历史对照能到达 Host。
已保存结果仍经原 validator 收尾，不新开 Host 尝试。既有分阶段 replan 材料读取
例外仍由同一 TypeScript 规则管理。确定性 loopback provider 下，两条真实 native
exec／CodeMode 路径通过。这只资格化合成源码入口，不证明自然模型采用、安装行为
或完整 C1 验收。

完整 capture、Turn-driver 与 manager-context consumer 三个模块在集成源码上
共 345 项通过，已包含上述新增覆盖；未重跑所有历史 adapter、预算或 backend 资格。

匹配的组件观察显示：完整来源的新尝试多一次 `host_dispatch_context` RPC。在固定
fixture 中，规范 params JSON 加 result JSON 在 File 为 42,771 字节，SQLite 为
42,828 字节。已保存结果恢复仍为原有一次 lineage RPC，所测字节完全相同；不含
完整来源的历史请求不增加 RPC。基线函数从冻结的修复前 commit 读取，使用同一
owner 与 fixture 运行；这是派发组件隔离观察，不是完整分支重放。未测 RPC framing、
CPU、模型输入、账单、成果或整体净收益。该正确性检查增加新尝试成本，不是字节
节省、同 capture 正文体积测量，也未修复原五项体积门。共享的既有 phase／helper
已足够；另造 wrapper 或策略 owner 没有局部重构价值。writer／reader 退役，以及
相对原 quota 的单会话成果与完整成本比较保持开放。

### Goal 拒绝先于自动启动间隔预留

通用派发检查现在位于原始 capture staging 选定输入之后、自动启动间隔预留、
发布或新 journal 写入之前。有 staging 时检查其原始 plan，否则检查当前 request。
此前的顺序可以在未启动 Host 的情况下拒绝 Goal 待读，却已预留间隔并写入
journal／capture，导致后续合法 Turn 仍须等该间隔。本次只移动同一 TypeScript
规则，不新增规则、状态、配置、豁免、持久缓存或额外 RPC。

四项 File/SQLite 拒绝 case 在冻结的修复前源码复现了该等待状态，六项合法／历史
对照通过。加强后的公开矩阵覆盖回调及真实 argv、staged capture、真实 cadence
状态与已保存结果恢复，共 22 项通过。确定性 loopback provider 下，两条真实
native exec／CodeMode case 通过。已有结果仍走元数据校验与收尾，不另预留或
启动尝试。

完整 capture、Turn-driver、manager-context 与 cadence 四个模块共 357 项通过。
首次合跑的 356 过及一处旧根布局失败保留，后者也在干净的冻结修复前 checkout
复现。该 cadence 测试现在经真实不可变 capture reader 取得 scheduler 字段，
原周期下限及拒绝降低周期的断言均保持不变。

十个匹配的合成条件用冻结 executor 函数与当前函数，经同一真实 owner、固定
来源输入及等长隔离 namespace 对照。socket 观察计完整 JSON 请求／响应行，
包含错误与 framing，不保留凭证。拒绝的非 capture 路径从 5 次 RPC 降至 2 次，
capture 路径从 6 次降至 3 次；File/SQLite 所测字节分别减少 95,293／95,405 与
97,665／97,777。消除的是一次 cadence admission 和两次 journal 写入。拒绝后
不新建 journal／capture，lane 保持 eligible；四个后继合法尝试均可启动，而其
冻结对照仍等待间隔。

六个合法／历史条件的 RPC 次数及所测字节完全相同，Host 输入在只替换独立
capture namespace 后相同。完整来源派发检查的原有 RPC 成本仍在。这只资格化
暖态 RPC 组件，Host 被有意停止；不是完整分支的模型／成果实验、同 capture
正文缩减、CPU、账单或整体净收益。原五项体积门、完整 C1 writer／reader 退出
及成果验收保持开放。合并 journal 写入仍需独立的中断／恢复资格；本次没有合并
Host attempt 的持久 checkpoint 或确认边界。


### 当前 main 集成与原 quota 完整任务

完整来源路径现复用 main 的 Progressive 与 Delta owner。既有完整决策 owner 先校验
原始源，再应用展示计划。保存 capture 后选择的 transport 参数只改变派生视图中的
selection recipe，不修改签名原件，也不重新准入。共享的保源 edit transport 保留
稀疏数字身份、当前完整要求、显式关闭行为及同次 capture 恢复；被替代的 native quota
与有界结算 adapter 已退役，不保留两条竞争的生产路径。

原固定 quota 任务对照 main `24f46874bc97` 与集成运行时 `675cd9d23b5b`，在 File 和
SQLite 上各做三次冷读、温态与压缩恢复，共 36 个条件。各条件交付同一份独立校验
artifact，含重放均只扣一次额度。两边使用同一 Python 可执行文件及未修改的任务／
observer；来源检查覆盖 252 次父进程与记账子进程观察。统计包含完整调用者输出、
记账子进程双向 RPC 及可观察的 Python 普通文件 IO。并行验证使延迟／CPU 不具资格；
provider 原生 IO、模型输入／缓存／输出、账单与自然效果仍未测量。

| Provider／场景 | 完整调用者 stdout，main → 候选（字节） | RPC 次数 | RPC 线上字节 |
| --- | ---: | ---: | ---: |
| File／冷读 | 55,754 → 35,834 | 242 → 209 | 1,468,459 → 1,474,720 |
| File／温态 | 68,136 → 62,107 | 334 → 292 | 2,151,083 → 2,217,775 |
| File／恢复 | 89,861 → 61,384 | 336 → 294 | 2,156,745 → 2,214,069 |
| SQLite／冷读 | 56,155 → 35,908 | 242 → 209 | 1,470,295 → 1,476,592 |
| SQLite／温态 | 68,557 → 62,249 | 334 → 292 | 2,153,959 → 2,218,459 |
| SQLite／恢复 | 90,349 → 61,522 | 336 → 294 | 2,159,660 → 2,216,837 |

以上为中位数，不代表效率晋升。温态连同基线准备的总输出少约 9%，但排除准备后的
当前步骤输出在 File 增 7.0%、SQLite 增 6.2%；温态线上字节增约 3%，可观察的 Python
读写合计增约 4%。主指标“同次完整 capture 到所选正文文件”的缩减率在 main 温态
约 87.8%，候选 File／SQLite 分别约 82.6%／85.4%。候选冷读与恢复选择完整正文，
主指标为 0%，不等于净收益为零。RPC 次数减少及完整源校验都不能证明整体净收益；
原 quota、writer／reader 退役及长程验收保持未闭合，既有负面对照及冻结体积失败不改判。

集成源码验证通过 448 个完整调用者／结算用例、156 个 context 用例及 14 个验收绑定
用例；其中分别保留的五个、十个跳过用例不在本次通过范围内。两个相关 TypeScript
模块 67 项测试及 control-plane typecheck 通过。隔离安装的 wheel 通过 258 项，另有
一个平台跳过；476 次已完成进程与 136,450 次 Core 模块观察没有来源污染，突然退出
探针不产生该审计的退出时观察。比较的 1,897 个打包源码文件与本次测量产品源一致。
这些结果只验收上述打包路径，不代表正式安装或尚未成立的净收益门通过。


### 所选读取与已结算记录的传输

显式 reader 现在只传送所请求的字面转义地址沿途的来源容器。既有 TypeScript
selector 仍独占 RFC 6901、数组下标、整批失败与恢复父节点的校验；Python 只从
完整摘要校验的原件渲染原值。数组长度、容器类型、初读导航、完整发现、历史固定
顺序和 native section-read 核验均保留。不新增缓存、metadata 文件、pointer parser、
读取义务或 retention 声明；复用 main 的 selected-source IO 和共享 native 回放 owner。

组合 refresh 调用方也向既有 TypeScript 结算 owner 请求更小的最终读取。该 owner
核验 Turn 已结算后，显式投影保留相同身份、控制与三张回执，将六个未消费的 Run/event
正文置为 null。默认／完整读取以及全部待恢复状态保留完整原正文。这个内部 RPC
可选字段不改变 admission 或记账，也不增加 CLI 或产品流程。

未修改的原 quota 完整任务对 main `886f2cdff4b2` 重复三次：同一 Python、材料、独立
产物检查和重放，覆盖 File／SQLite 的冷读、温态与 compaction 恢复，共 36 条件。
两边均产生相同产物并只扣一次。252 次父进程／记账 child 来源观察无污染；测量期间
产品源码摘要不变。以下中位数包括双向 RPC 和全部必要读取：

| Provider／场景 | 全部调用方 stdout，main → 候选（字节） | RPC 次数 | RPC wire 字节 |
| --- | ---: | ---: | ---: |
| File／冷读 | 55,946 → 35,930 | 241 → 208 | 1,465,253 → 1,429,981 |
| File／温态 | 68,364 → 62,287 | 332 → 290 | 2,143,831 → 2,147,306 |
| File／恢复 | 90,181 → 61,556 | 334 → 292 | 2,149,657 → 2,143,784 |
| SQLite／冷读 | 56,343 → 36,004 | 241 → 208 | 1,467,027 → 1,431,735 |
| SQLite／温态 | 68,793 → 62,429 | 332 → 290 | 2,146,719 → 2,149,912 |
| SQLite／恢复 | 90,666 → 61,694 | 334 → 292 | 2,152,475 → 2,146,508 |

温态 RPC 少 12.7%，但 wire 仍增 0.16%／0.15%；不含准备的当前步骤 stdout 增
7.0%／6.2%，已观测 Python 读写合计增约 4%。主指标“同次完整 capture 到所选正文”
在候选温态约 82.6%，main 约 87.8%；候选冷读／恢复仍选完整正文，主指标 0% 不表示
净收益为零。先前仅精简结构的 cohort 也仍是负数，不能与本 cohort 相加。冷读／恢复
传输减少不构成整体净益；CPU／latency、原生 provider IO、模型输入／缓存／生成输出、
账单和自然长程成果仍未资格化。此前 cohorts、冻结体积失败与原 C1 验收继续保留。

真实公开 CLI 与 main 对照保留 15 个成功／拒绝观察，覆盖精确大整数、转义键、非法
下标及整批拒绝；同入口丢弃所选来源结构的 mutation 会使独立原值 oracle 失败。
结算套件覆盖默认完整读取及六种恢复状态的完整等价，137 个 TypeScript 测试和
control-plane typecheck 通过。最终安装 wheel 与 premerge 的读回随提交的精确 head
单独记录，不代表正式安装。

最终隔离安装 wheel 通过 325 项，另有 1 项 Linux-only skip；1,897 个已打包源码文件
与测量产品源相符。完成进程的模块来源审计无污染，突然退出不在该审计观察内。
扩大后的 source 结算运行有 274 passed、4 failed、1 项平台 skip。四项 File／SQLite
失败在 main `886f2cdff4b2` 原样复现：回执绑定任务转 deferred 后返回 `quota_skip`，
未保留原 closeout recovery；到期 successor-replan 的重放也丢失动作。正向 guard
中的 `should_run`／`delivery_allowed` 事实曾被初步解释为互相矛盾。下文修正该诊断，
保留原失败运行。更小记录投影不在这些失败路径上执行；不放宽回执规则来换取通过。

### 执行准备与原 Turn 恢复

普通 guard 可以要求领取任务，同时禁止业务交付。此时 `should_run=true` 与
`delivery_allowed=false` 一致：已有领取后置条件要求在同一 Turn 重新进入，并明确
取得 `execution_preparation.state=ready`。此前两个 File／SQLite 测试在产出 artifact
前遗漏了这一步。现在测试遵循已有调用契约，并核验同一结算身份下从负向到正向的
追加式 guard lineage；既不改写原负向事件，也不放宽 native result admission。

另外两个失败暴露了产品缺陷：selected-Todo IO adapter 对 canonical obligation 已经
禁止交付的规划操作，仍应用普通执行准备。任务为 deferred 时，到期 successor replan
因此被压制，重放也丢失原动作。adapter 现在先消费 TypeScript interaction owner
使用的同一明确 `delivery_allowed=false` 事实，再决定是否读取执行租约。规划仍不
授予普通交付或扣额；省略该事实时保留全部原执行准备检查。普通领取、冲突、回执身份
及 once-only 恢复仍由原 owner 管理，不新增决策 owner、协议字段、权限、cadence
顺序变更或 RPC。

使用同一套修正后的测试 harness，冻结的修复前产品源码通过两个普通交付用例，
仍失败于两个规划用例；候选四项全部通过，完整执行准备矩阵共 30 项通过。原四失败
cohort 保留为历史证据，调用者顺序修复与产品路由修复分别记账。这只闭合已复现的
规划路径缺陷，不代表完整 C1 验收或整体效率成立。


未修改的原 quota 任务也再次与同一 main 源码对照，File／SQLite 的冷读、温态与恢复
条件各重复三次。36 个条件均保留同一独验产物及含重放的一次扣额；252 次父进程／
记账 child 来源观察无污染，产品源摘要不变。温态 RPC 仍为 332 → 290，wire 在
File 增 0.165%、SQLite 增 0.073%；排除准备后的当前步骤 stdout 分别增 9.5% 和
6.2%，已观测 Python 读写合计增 4.1% 和 3.8%。主指标“同次完整 capture 到所选正文”
候选为 84.1%／85.5%，main 为 89.2%／87.8%；候选冷读／恢复仍选完整正文。这是
独立 cohort，不能与前一 cohort 相减作因果归因。规划路径正确性未解决普通温态路径
的成本取舍；provider 原生 IO、latency／CPU、模型／缓存／生成输出、账单及自然长程
效果仍未测量。

扩大后的 source 结算套件现有 278 项通过，另跳过一项原生 Windows PowerShell 用例。
新建的隔离安装 wheel 通过 246 项，覆盖四个修正后的 CLI 用例、普通／native 执行
准备、结算、组合收尾及 runtime 集成；原生 Windows PowerShell 与 Linux thread-group
exit 两项不适用于当前 Host。1,897 个比较的打包源码文件与本次源码一致，包括修改的
adapter；227 个已完成进程及 102,420 次 Core 模块观察无来源污染。突然退出不在退出时
审计的观察范围内。原隔离 guard 保留；这不代表活动 runtime 的安装或晋升。局部改动
复用共享 IO adapter，另造 contract projection 或 lifecycle wrapper 会增加顺序风险，
没有必要调用者。


### Native quota 的 capture 发布

已具备资格的 native quota 调用方现在复用刚发布的完整 decision，作为 source recovery
引用。此前，它在向同一个 TypeScript owner 请求可选展示前，还会保存一份逐字节相同
的完整 projection；前一轮合格工作负载的 30 对 capture 均确认字节一致。adapter 现将
生产者自有的原始引用传入既有 native preparation，外部 continuity 输入不能提供该引用。
内容确实不同时，required view 仍单独保存；独立 API 与未初始化 native fallback 保留
原发布路径。不新增 hard link、持久缓存、文件格式、语义 owner、读取义务、权限或 RPC。
历史路径仍按原身份和摘要读取。

原 quota 工作负载与 main `6a9264034605` 对照，File／SQLite 的冷读、温态和 compaction
恢复各重复三次。36 个条件均产出同一份独验 artifact，含重放只扣一次；252 次父进程／
记账 child 来源观察无污染，测量期间产品源摘要不变。30 个候选 capture 均不再保存
重复的完整 projection。这消除了重复生产写入，未省略完整来源，也不声明模型保留。

| Provider／场景 | 全部调用方 stdout，main → 候选（B） | RPC 次数 | RPC wire B | 已观测 Python 读 B | 已观测 Python 写 B |
| --- | ---: | ---: | ---: | ---: | ---: |
| File／冷读 | 55,429 → 35,654 | 241 → 208 | 1,459,302 → 1,424,420 | 336,589 → 287,832 | 127,933 → 120,437 |
| File／温态 | 67,723 → 61,768 | 332 → 290 | 2,133,937 → 2,137,552 | 552,218 → 511,741 | 238,580 → 201,925 |
| File／恢复 | 89,316 → 61,059 | 334 → 292 | 2,139,532 → 2,134,660 | 580,809 → 519,604 | 233,054 → 195,818 |
| SQLite／冷读 | 55,826 → 35,728 | 241 → 208 | 1,461,076 → 1,426,174 | 344,133 → 295,172 | 129,145 → 122,147 |
| SQLite／温态 | 68,136 → 61,910 | 332 → 290 | 2,136,689 → 2,141,039 | 560,254 → 519,489 | 240,008 → 204,526 |
| SQLite／恢复 | 89,801 → 61,197 | 334 → 292 | 2,142,354 → 2,137,296 | 588,955 → 527,354 | 234,484 → 197,664 |

温态已观测 Python 读写合计少 9.75%／9.53%，写入少 15.36%／14.78%；RPC 少
12.65%，wire 仍增 0.169%／0.204%，排除准备后的当前步骤 stdout 增 7.02%／6.24%。
主指标“同次完整 capture 到所选正文”在候选为 86.1%／84.7%，main 为 87.9%；候选
冷读／恢复仍选完整正文。工具 stdout 不代表模型生成输出；原生／provider IO、
latency／CPU、模型输入／缓存／输出、账单及自然长程成果仍未资格化。历史 cohort
保留原负数，不把不同 cohort 相减当成此次改动的因果成本收益。

同一测试 harness 在冻结的修复前 C1 源码上保留四项语义对照，并失败于四项重复存储
断言。候选完整 native／lifecycle／reader／cost 套件 265 项通过；最终按实际公布的
引用修改正文后，reader 拒绝变化字节且不改 native journal。公开 capture／model／
恢复运行 64 项通过。新隔离安装 wheel 有 493 项通过，另有一项 Linux thread-group
exit skip；1,897 个比较的打包源与实测产品相符，包括两个修改的 IO adapter。278 个
已完成进程和 45,049 次 Core 模块观察无来源污染；17 个未分发 tracked assets／dev
fixtures 另列，突然退出不在退出时审计范围内。这只资格化上述打包路径，不代表活动
安装或独立 PR 批准。有界改动复用既有 recovery 引用，没有新增 writer、helper 框架
或第二语义 owner。

### 已结算 result value 的传输

既有组合收尾调用方在 TypeScript 结算 owner 验证原身份及全部回执后，请求
`include_record_bodies=false`。此前该投影删除顶层 Run 记录，却仍在四个 result
value 中重复返回相同的写回／扣额正文；调用方并不使用这些 value。现在既有 bundle
helper 在已经请求且完全 settled 的投影中清空这些正文，身份、回执 payload、失败
及其它控制保持原值。默认完整读取和全部待恢复状态保留原正文；持久 Run、历史
reader、记账及公开 CLI 结果不变。不新增选项、schema、RPC、缓存、Python 规则
或模型读取义务。

这是既有内部 opt-in 读取投影的有意变化，默认 reader 不变。唯一生产 opt-in
消费者仍是组合 refresh／spend adapter。同一 TypeScript harness 先拒绝旧版的
正文重复，修复后 137 项全过，包含完整读取与 pending 状态等价。真实 File／SQLite
CLI 独立核对正文省略、回执不变、持久读回及重放只扣一次。新增断言通过后暴露了
测试局部变量覆盖问题；修正未改变任何断言或产品规则，原失败 cohort 保留。

较广源码运行保留 276 项通过、一项 native Windows 渲染 skip 及上述两项 fixture
失败；修正后的 File／SQLite 两例通过。隔离 wheel 保留其它 387 项通过、两项平台
skip 及相同两项失败，随后在产品不变时通过修正后的两例。其 1,897 个比较的打包源
与实测产品一致，包含修改的 TypeScript owner；17 个未分发 asset／dev fixture 另列。
两次 wheel 运行合计 1,297 次已完成进程观察、535,308 次 Core 模块观察，无来源
污染；突然退出不在退出时审计范围内。这只资格化已测安装包路径，不代表活动安装、
独立评审或合并权限。

原 quota 工作负载不变，以冻结 main `0423908e9cf4` 为基线，各 provider／场景重复
三次；候选包含该目标及此前 C1 改动。36 个条件均达到同一份独验 artifact，含重放
只扣一次。252 次父／child 来源观察匹配各自固定版本，测量期间产品摘要不变。
父进程记账已包含 child RPC，不再重复相加。

| Provider／场景 | 全部调用方 stdout，main → 候选（B） | RPC 次数 | RPC wire B | 已观测 Python 读 B | 已观测 Python 写 B |
| --- | ---: | ---: | ---: | ---: | ---: |
| File／冷读 | 55,472 → 35,678 | 241 → 208 | 1,459,815 → 1,400,685 | 336,894 → 288,097 | 128,010 → 120,512 |
| File／温态 | 67,892 → 61,813 | 332 → 290 | 2,135,559 → 2,114,908 | 552,831 → 512,164 | 238,851 → 202,786 |
| File／恢复 | 89,386 → 61,102 | 334 → 292 | 2,140,392 → 2,111,166 | 581,330 → 520,030 | 233,207 → 195,945 |
| SQLite／冷读 | 55,868 → 35,752 | 241 → 208 | 1,461,586 → 1,402,351 | 344,435 → 295,437 | 129,221 → 122,222 |
| SQLite／温态 | 67,403 → 61,955 | 332 → 290 | 2,136,570 → 2,116,685 | 559,999 → 519,924 | 239,399 → 203,919 |
| SQLite／恢复 | 89,875 → 61,240 | 334 → 292 | 2,143,241 → 2,113,734 | 589,491 → 527,788 | 234,645 → 197,795 |

以上是三次观察的中位数，不声明统计显著性。温态 wire 少 0.97%／0.93%；File 范围
为 2,134,771–2,135,559 对 2,113,851–2,115,192，SQLite 为
2,136,570–2,138,375 对 2,115,544–2,117,498。温态已观测 Python 读写合计少
9.69%／9.45%，RPC 少 12.65%；这些是累计 C1 对照，不将既有 RPC／IO 下降
归给此次小投影变化。

排除准备后的当前步骤 stdout 仍增 6.66%／8.73%。主指标“同次完整 capture 到所选
正文”中位数为 84.7%／86.1%，main 为 87.7%／89.2%；三次范围分别为候选
84.6%–86.2%／84.7%–87.6%，main 87.7%–87.9%／87.7%–89.2%。候选冷读／恢复
仍选择完整正文。本次改动减少内部传输，不等于模型生成输出下降。CPU／latency、
原生／provider IO、模型输入／缓存／输出、账单、自然采用及完整净收益仍未资格化。
历史正负 cohort 保留原版本与结果，不跨 cohort 相减作因果归因。原 C1、最后 reader
退出及整体验收保持开放。

验证期间 main 推进到 `7f550aef58a8`，修改 Chat 原会话 fallback 及两个前端 fixture。
该后续组合尚未资格化；本 cohort 和集成基线仍为 `0423908e9cf4`，不改记为移动后的
实际目标。

### Main 集成与独立上下文层

实现 `6f00db17c654` 集成 main `918844a01746`，保留调用内首读计划复用，并接入
main 的独立 Delta 路径：显式关闭 Progressive、开启 Delta auto 时，不发布渐进导航
或已见字段复用；绑定 digest 的文件差分及当前完整恢复仍可用。普通 quota presentation
与 managed preview 已消费共享完整源 owner；本次不重复其迁移，也不退役绑定原版本的
历史 journal。C1 的 RFC 增量随 main 迁到附录，两种语言的义务均保留。

实际 Codex 配合静态本地回环，在源码及隔离 wheel 中验证默认 Progressive 与独立
Delta。完整未变大验收字段的匹配温态所选 BODY 仍由 14,386 降至 2,583 字节；
未经验证的已见字段连续性恢复 14,386 字节。独立 Delta 用例重建全部原字段，并通过
实际客户端读取当前完整恢复。wheel 中的保存源身份拒绝、显式 off、前驱丢失及压缩
恢复也通过。这些是传输检查，不是新增模型实验或安装态采用。

新的匹配工程 cohort 沿 main 原 quota 产品入口使用同一合成三步骤 Task：File/SQLite、
冷/温/恢复各三次，成果由同一独立检查验证，扣额各一次。共 36 场景、60 Turn，包含
准备及子进程 RPC/IO，后者只累计一次。BODY 按返回页的字节顺序重建，不取后备文件
大小。SQLite 温态当前步骤 BODY 为 23,617 → 22,970 字节（−2.74%），CLI stdout
58,949 → 48,401（−17.89%），RPC 294 → 290，framed RPC 字节约少 3.74%。含准备时，
stdout 为 115,568 → 93,613，RPC 586 → 578，framed 流量约少 4.01%；已观测 Python
普通文件读/写约少 8.80%/26.18%。未测 Node/kernel/provider IO 与模型费用。File 温态
带观测 CLI 时间反增约 7.5%，不据此资格化延迟。此轻量 quota fixture 没有大型验收
正文，不能将其收益归因于字段复用。它只验证已测步骤，不代表原三步骤 Task 完成、
自然模型采用或整体 C1 净益；历史失败、剩余 reader、质量和独立验收义务仍开放。

### Main 采用后的首读收敛

后续集成 `339f01e08782` 接入 main `39770a960483` 及已落地的首读生产者/读取器。
最后一个 `project_codex_context` 测试 caller 改用 `prepare_codex_context`，删除未再
使用的 Python wrapper；已记录源版本与 journal 恢复 codec 仍保留各自支持义务。
benchmark 预检测试沿实际首份 BODY 读到 EOF，核 reference/source/selection 摘要及
必要字段，并保留完整原件，不再把首个回复当成导航。

以该 main 重跑的 36 场景 cohort 全程保持产品源字节不变，沿用同一合成 Task、成果、
一次扣额及成本边界。SQLite 温态 BODY 为 24,016 → 23,285 字节（−3.04%），CLI
stdout 59,898 → 49,136（−17.97%），RPC 292 → 290；framed RPC 字节反增 **2.93%**。
含准备时，stdout 117,466 → 95,041（−19.09%），RPC 582 → 578，framed 字节反增
**2.14%**，已观测 Python 读/写少 9.12%/26.28%。这些结果不改写上一 cohort，也不
替代自然模型资格。当前 main 的原生对照温态 BODY 仍为 14,386 字节，字段复用为
2,583；此大字段组件不在上述轻量 quota fixture 中。

配置内 Python 类型检查仍未通过：原 C1 为 4,809 条，本组合为 4,814 条。新增五条
位于与 main Git 对象逐一相同的模块，保留失败，不予豁免。reader 退出、独立审阅、
安装态采用及整体 C1 净益仍开放。

### 完整来源资格与 native 执行顺序

无写入行为资格工具现可在两个实验臂接收经 Core 校验的完整 v1 来源，并报告实际
packet 版本。保留材料与反事实候选默认调用既有完整 composer；已捕获的完整来源
保留原 Host 绑定与结算组合。原始 fixture 使用 Core 默认组合，不从候选元数据推断
另一绑定。版本化 v0 状态矩阵继续作为明确的历史对照，包括当前 owner 拒绝的旧
action 别名；这不退役仍受支持的历史 codec。

配对预检在任一 actor 调用前检查两臂的保密边界。完整候选继续校验来源、完整性与
精确配对出处；provider 提示与编码 scheduler 扫描依实际 packet 布局选择字段，
不再依实验臂名称推断。当前格式删字段负例在 actor 前拒绝。语义负例改动原始
source 后由 Core 组合，因此即使两侧绑定有效且彼此一致，仍须通过独立的固定
战略链 oracle；签名篡改保留独立负例。

集成后的 native Host 暴露执行顺序缺口：delivery 守卫要求 Task 租约，而模型回调
要等启动后才申请它。显式非报告 Task 现于守卫前进入既有租约生命周期，managed
回调复用并核对同一 Task。canonical 中不存在的工作仍进入 kernel 的结构化拒绝，
权限来源不可用时拒绝执行，也不借用冲突的执行租约。报告与已记录 resume 路径
保留原生命周期；未放宽守卫或独立验收，也未资格化未显式指定 Task 的自动选择。

冻结的 main 集成先记录 646 通过、22 失败；开发运行保留 292/4 和 356/1，其中
后一次发现过早申请租约的异常取代了缺失工作的原拒绝。修正后，新的一次八模块
运行全程保持源码稳定，357 项全部通过，包含 61 项 native File/SQLite、原会话、
HTTP、拒绝与独立验收。main conversation-binding TypeScript 模块通过 59 项，
五个改动资格 Python 模块通过类型检查。这是本地合成 actor/provider 检查，不是
真实模型行为或完整成本证据。

新鲜校验的 Chat bundle 与隔离 wheel 中，1,815 个 Python/TypeScript 源与实测
产品字节一致。安装环境中的完整请求、当前与显式历史配对、错源拒绝及实际适配器
配合本地 transport double 均通过；已观测 300 个 LoopX 模块，无 checkout Core
借用。native Host 仅做安装导入，未作为安装态旅程执行。首次旧/缺 bundle 构建、
harness 准备失败和历史 v0 大整数来源不匹配均保留；旧格式正向对照回到原可表示
fixture，当前格式的大整数检查仍保留。这些结果不构成活动安装、真实模型运行、
整体 C1、质量/readiness 或净收益资格。

后续直接 writer 审计发现，公开 task-orchestration smoke 仍生成 v0 输入，另有一个
场景 packet builder 已无调用方。示例现以各自原 scheduler context 组合完整来源，
并读回 Codex、Claude 的最终 Host 请求：完整来源、主 Task 选择及原有子任务
上下文与原生操作断言均保留。闲置 builder 直接删除，不再添加无人调用的替代
抽象。明确版本化的 v0 状态矩阵及受支持历史 writer/reader 继续保留；产品代码与
示例中的 Python 直接调用审计只剩该历史矩阵调用旧 writer，不声称覆盖动态调用。

公开示例在迁移前后均通过。默认配置的 Mypy 在前一 `a706379f70` 检查点及本次
改动后均通过全部 17 个入口；这更新当前类型资格，不重写较早 4,809/4,814 的
失败。这些检查没有执行真实子任务创建，不证明真实模型采用或新安装包资格。

新的一次四模块回归全程源码稳定，247 项全部通过，覆盖真实 Turn driver、活动
portfolio 来源、历史 corpus 及重规划 Host-context 投影。这是本次调用方资格，
不与较早的 357 项或安装运行合并记账。

### 集成 main 后的安装态 native 生命周期

集成 `5091fb82a9` 接入 main `9d276f6cb9` 的原上下文补答修复。native Turn Host 与
私有会话两个模块的新一轮源码回归全程源码稳定，466 项全部通过。重建并校验
Chat bundle 后，同样两个模块在隔离安装包上以原样夹具运行，覆盖真实 File/SQLite、
native 协议与 HTTP、原会话、独立 Task 验收、来源/权限拒绝及恢复。模型与外部
provider 仍为合成实现；这已超出 native 只做导入的范围，但不资格化活动 App 或
真实模型采用。

首轮安装运行保留 459 通过、7 项准备失败：明确启用账户路由的七个用例缺少单独
分发的可选包。安装同提交的 0.15.3 wheel 后，新的一次完整回归 466 项全部通过，
未将局部复跑拼接成全绿。原失败及审计 helper 的 61 条弃用警告保留；helper 改用
关键字参数，与产品代码分开处理。

Core 包内 1,815 个 Python/TS 文件及可选分发的 24 个文件，均与提交和安装字节
一致；已有私有故障进程测试 driver 不在此 wheel 资格范围。48 个已观测 Python
进程中的 1,228 个 Core 模块、7 个可选 provider 模块及 4 个已观测子进程源码
路径均来自安装分发，无 checkout Core/provider 导入。夹具与安装源保持不变。
默认配置 17 入口 Mypy、集成后的会话绑定 TS 模块 60 项通过。较早 357/247 项及
PostgreSQL 证据保留各自版本；这些合成正确性与来源检查不能证明完整成本收益。

### 可见 Goal 读取与已声明的验收审计

集成 `d80b3860ab` 接入 main `ac62962255`。独立审阅发现，可见 Goal 提示新增了
无条件完整 capture 前置读取，连普通/off 输入也受影响，并拒绝了三个原本合法的
4,000 字符配置。捕获提示现要求读取所需原始章节；普通/off 不新增 capture
前提。work_context 与 required_reads、明确完整读取义务、原身份/恢复、传输
flags、结算和停止规则均保留，4,000 字符 Host 上限不变。24 个同条件 builder
输入中，main 接受 21 个、候选接受 23 个，没有丢失 main 的合法输入；一个过大
输入在两侧仍被拒绝。

完整 structured、Codex Host 输入复用已声明审计的原触发条件：判断 Goal 验收
或提出关闭前先读取。八个 File/SQLite 反事实覆盖两种 Host 和有/无审计；缺失
审计的负例捕获并否决了首次无条件提示。十二个合成历史 structured renderer
用例保持输出逐字一致、输入不变。这是限定范围的指令资格，不证明模型记忆保留。

heartbeat 消费方现校验完整来源，通过带摘要校验的分页 reader 读取调用方原本
持有的 capture。原 scheduler、选择、回执及一次结算 oracle 保留。安装运行暴露
测试调用方隐含借用 checkout 工作目录：refresh 应在其实际 disposable project
执行。首次安装全组 60 通过、18 失败；同一 wheel 仅修正调用目录的对照通过，
没有放宽生产工作区守卫。

最终九模块源码运行全程来源稳定，记录 **855 通过、2 失败**；三个模块的完整
安装复跑记录 **80 通过、2 失败**。两侧失败均保留原始 JSON 预算：两个长 scope
后端用例 5,519 字符超过 5,400，而同条件 main 的原四个预算用例全部通过。
没有调高预算、缩小夹具或减少有用义务来隐藏回归。早期失败组及一次私有审计目录
准备失败保留。默认 17 入口 Mypy、六个改动路径的 Ruff 和有限语义 advisory
通过。1,815 个 Core wheel 源与冻结实测及安装字节一致；五个已观测 Python
进程、1,179 个模块和六处已观测子进程来源无 checkout Core 导入。部分子进程
覆盖审计环境，故不声称观测了所有进程。这些合成检查资格化修复后的 Goal/审计/
调用方切片；整体 C1、剩余预算回归、真实 App/模型采用及完整成本收益继续开放。

### 保留生产者已绑定的 heartbeat Turn 身份

运行时 `3736048363` 在既有生产者实际替换时间戳占位符后，移除不再适用的唤醒
指令。绑定后的提示已包含原具体赋值及独立 guard，再要求模型换成新唤醒时间戳
既重复又可能丢失原身份。未绑定的可复用模板保留原指引。四种提示模式共用既有
绑定 owner，不增字段、开关或 reader；历史正文、命令、传输 flags、必读内容和
恢复/停止/结算规则不变。

八个模式/宿主语法反事实在修复前失败、修复后通过；Windows 语法为模拟，不是
Windows 安装资格。32 个实际 builder 对照中，16 个未绑定的默认/off 输出逐字
一致，16 个已绑定输入只移除已履行的指令，其余生成字段和上限不变。原四个真实
CLI 预算用例的路径、scope 和断言保持：长用例从 5,519 降至 **5,379 个原始 JSON
字符**，正文少 139 字符，另少一个 JSON 转义字符；同条件 main 仍为 5,363。
这是修复候选回归，不证明相对 main 的完整流程节省。5,400/5,500/4,000 上限及
独立溢出负例保持。

新的一次九模块回归全程源码稳定，**865 项全过**；三个模块的隔离安装完整运行
**90 项全过**，保留原 worker 选择、修复、capture、重放及一次结算 oracle。
较早 855/2、80/2 保留各自原版本。默认 17 入口 Mypy 和改动 Ruff 通过。新鲜
bundle 校验及 wheel 来源检查将 1,815 个 Core 源、24 个可选 provider 文件绑定
到冻结实测字节；五个已观测 Python 进程、1,179 个 Core 模块及六处已观测子进程
来源无 checkout Core 导入。审计覆盖限制、合成 provider、真实模型采用和完整
成本尚未资格的边界沿用；独立审阅和整体 C1 验收仍开放。

### 可见宿主的调度观测

Pi、OpenCode 和 OpenCode 2 已绑定 Agent 时，共用现有调度观测。可运行状态、重置
标记、等待限制和终止来源均取自同一 Core 决定。此前三个调用方用普通模型上下文
接口做调度探测，收到渐进导航时读不到续跑所需字段。OpenCode 和 OpenCode 2 读取
当前的按需调度详情，继续接受历史调度形状。两者显式请求的宿主轮询回执每次探测
沿既有写入方记录一次。

调度观测不启动 Turn，不捕获模型上下文，不触发结算。工作交付仍按原上下文策略。
旧宿主绑定缺 Agent ID 时沿显式完整观测，绑定 Agent 后切换。升级或回退时使用
同一构建的宿主适配器和 CLI。原版本 Turn reader 在受支持的恢复窗口继续服务尚未
结清的工作。

### 恢复被拒绝的完整来源组合

组合失败时，capture 根记录既有传输失败契约，明确 `ok=false` 和
`should_run=false`。原始输入的全部事实保存在 `source_diagnostic`，同时保留
原回执及运行时的类型化组合诊断。语义拒绝和运行时启动失败共用这一路径，诊断
恢复沿现有带摘要校验的按需 reader。

当前来源经成功组合后才能发布工作或原生认领准备。这修复了被拒绝的当前输入
可能被误当成未版本化历史 capture 的路径。真实历史 capture 沿显式恢复 reader。
成功组合后才发生的交付失败，保留已验证的原 capture。

限定范围的合成 File/SQLite 用例覆盖准入后未知 action、未知交互版本和组合
不可用。独立 oracle 检查完整诊断回读、执行投影拒绝、原生 journal 与认领来源
副作用、一个原回执及零 spend。既有暂停、capture、handoff 和历史恢复覆盖共同
资格化相邻边界。整体 C1 验收及真实模型效果继续开放。

### Codex SSH 准入命令与 managed retry

新生成的 Codex SSH Goal prompt 和 onboarding guard 使用完整 Turn 来源，并沿用
原生 begin-Turn 身份。已保存的 bootstrap 若带旧显示参数调用生成器，仍可生成
当前 guard。显式关闭 context delivery 时直接返回完整来源。启用时按返回命令
读取同一 capture，并按原 Turn 的结算计划执行。标准包和独立 Core 包使用共有
的 CLI 入口和参数语法执行待结算命令。

TypeScript controller 的 managed-step 入口要求新鲜的完整决定。已保存的失败
Turn journal 仍沿原版本读取。在实时入口注入签名有效的 v0 preview 会在启动
Host、改写 journal 或扣额前拒绝。重试继续使用原 Turn。

源码和隔离安装包的 File/SQLite 用例覆盖启用、显式关闭、旧 bootstrap 重生、
同一 capture 读取和结算重放。原生 Turn 测试还覆盖丢失扣额响应。

### Benchmark 和 MCP 实时读取

新 benchmark heartbeat 在两种 context-delivery 设置下都请求完整决定。
新 checkpoint 选择要求带签名的来源。历史 checkpoint 接续沿用原捕获 reader 和
Turn 身份。Benchmark 的显式 v0 展示入口仍待退役。

共用 MCP 实时读取请求完整决定，返回原始 JSON 和 MCP 指引块。来源绑定缺失或
被改动时拒绝交付，实时无版本投影已移除。现有 `complete_task` 事务继续负责
认领校验及结算。真实 stdio、重放与历史恢复测试在隔离 authority 运行。
MCP 客户端的安装态验收仍待完成。

普通 quota、App 安装态采用、剩余结算调用方及单会话成果与完整成本仍待验证。
