# C1 来源收敛与历史集成检查点

- **日期：** 2026-10-30
- **状态：** 未合入的集成证据；C1 仍未完成。
- **来源：** 原候选 `648104978fdd67176cccdbb765c5bc3f36bf2ca3`，集成目标 `e8f4c6f8125e3cc0ef912afac653d5542671392f`。
- **语言镜像：** [English](2026-10-30-c1-source-convergence.md)

集成保留 main 已抽取的 quota-presentation owner 与稀疏原生 journal 运输。
候选中的重复数值 collector／validator 由既有实现替代。验收、summary lane、
authoring 和 Vision 共享既有来源身份及编辑 owner；普通 JSON 相等不能证明跨
Python／JavaScript 边界的原数值身份。正文大小判断仍比较序列化正文，不比较身份 framing。

独立的 24 项 Python 到 TypeScript 回归先得到 16 项失败、8 项对照通过。修复身份／编辑
后 24 项全部通过，覆盖整数形浮点、带符号零、不安全整数、稍后的精确 authoring owner
及相同 audit 对照。扩大集成验证最初还暴露旧 fixture 的 hash 引用和一项 authoring
缺失的 CLI 用例；这些失败保留为证据，须另行完成修正后的验证。这是来源／运输证据，
不证明 installed 采用、模型验收成果或完整成本资格。

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
