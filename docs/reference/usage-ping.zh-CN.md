# 基础使用统计

[English](usage-ping.md)

基础使用统计在**显著告知后默认开启，可关闭**。它用于决定平台支持优先级、
了解持续使用情况和发现慢命令，不代表 Goal 验收结果。本次不实现内容采集。

```bash
loopx usage-ping status      # 查看策略、接收方、待发送数据
loopx usage-ping disable     # 关闭所有通道，删除本机 ID 和待发送计数
loopx usage-ping enable      # 阅读告知后明确开启
```

设置 → 能力中心 → 此设备默认提供相同的整机开关与数据预览。打开设置和执行以上命令本身都不发送
统计。TypeScript 统一拥有策略、状态和字段校验；Python、浏览器只是适配入口。
它不依赖 Goal 的 File/SQLite/PostgreSQL provider，Lark 没有另一套开关，也不能覆盖
机器所有者的选择。

## 能回答什么

### 设备用途与安装级运行时长（告知版本 6）

一次设置即可跨 Goal 和终端持久使用，不自动加载仓库里的任意 `.env`：

```bash
loopx usage-ping context --context maintainer
loopx usage-ping status --format json
loopx usage-ping context --context unknown
```

Workspace 设备设置复用同一个 TypeScript owner。优先级是显式
`LOOPX_USAGE_CONTEXT` 环境变量 → 保存的设备标签 → `unknown`。非法环境值保持
未知，非法设置拒绝写入。设置用途不等于开启统计，不更换安装 ID、不授予工作权限。
关闭统计清除标识与测量记录，但保留自愿用途标签。

新增 `POST /v1/installation`，契约 `loopx_installation_usage_v1`。
同一个随机安装 ID 会与每日固定 CLI 功能计数、数字版本、UTC 活动日期、自愿环境
标签、已观测运行分钟、快照 revision 和截断标志关联。每天首次观察后固定该日
标签和版本，后续配置不能反向改写历史。这是**新增关联**，不是匿名汇总。
CLI 诊断及安装概要增加 `heartbeat|state|agent|memory|capability|maintenance`
固定分类；来自解析器命令名，不上传参数或自定义插件名，旧汇总契约不变。

对 `host_call`、`codex_turn`、`quota_cycle` 分别取**同一安装所有 Goal/Host**
实际区间的并集，跨 UTC 午夜拆分，每天向下取整为分钟。并行和嵌套重叠只算一次，
重放不增加时长，区间之间的空闲不增加时长。没有区间时不填 runtime 行；已有行的
零分钟表示观测不足一分钟，不是没有工作。三种口径重叠，不能相加；轮次与推进
周期可包含暂停、工具和审批等待，直接 Host 调用沿用短检查点的休眠间隔排除规则。
这不是机器在线、进程 uptime、CPU、任务完成、模型思考或计费用时。

本机最多保留八个 UTC 日期，每个日期/口径最多 1,024 个离散区间，溢出明确标记
`truncated`，不外推。只上传每日整分钟，不上传区间时间戳、Goal/Agent/Host
身份或对话。完整快照由活动触发，至少间隔 15 分钟，不增加常驻计时器；安静结束的
当天末段可能未发出，前七天的迟到区间可以修正。收集端按安装/日期只替换更新
revision，并保留首次标签和版本，传输重试或乱序不会累加。状态损坏或丢失可能
漏计；复制标识可能合并多台机器，不能作为账本。

扩大的范围必须重新告知，旧 worker 被 generation 隔离，旧缓冲丢弃，不回填历史。
CI、请勿追踪、明确关闭与需要明确同意的策略继续生效。关闭删除本机
`.installation` 区间缓冲，已经开始的网络请求不能撤回。收集端保留 30 个活动日，
和 400 天心跳保留期独立。公共接口不暴露每个 ID 的概要。

维护者可按**实际保留窗口**统计每个安装/口径的已观测分钟，同时分开看活跃日、
日历跨度和 CLI 功能分布，不称为终身运行时长。缺失日期不可补零，旧匿名 CLI
计数不可按安装数分摊。`maintainer` 排除只适用于以后明确标注的概要；未知不等于
外部用户或个人用户。[固定只读查询](../../apps/usage-collector/queries/installation-usage.sql)。
先在隔离数据库验证，再应用增量迁移 `0005-installation-usage.sql` 并部署 Worker，
然后发布告知版本 6 客户端；合并代码不等于已经部署，回滚保留新增表。

- 每日版本、系统、CPU 架构、Python 小版本和安装渠道：哪些环境需要优先维护。
- 随机安装 ID 跨日心跳：持久机器状态目录的活跃与成熟的 1/7/30 天回访，不是
  用户或组织数；删除状态或关闭后重开可能计为新安装。
- 固定 CLI 功能/子操作、版本和 UTC 活动日期：哪些入口常用，自动化轮询也计数。
- 类型化结果、原因和耗时区间：哪些入口失败或慢；合并条件未满足单列 `blocked`。
- 回执支持的注册、Turn 提交、Todo 完成/验证、结果回传：观察推进，不靠退出成功
  推断 Goal 完成，也不宣称独立验证了结果质量。

第一版只计 CLI 调用，包括 Agent 发起的命令。顶层 `--help`/`--version` 快速路径、
原生 exec 替换的 scheduler followup、纯 API 操作以及 App/Lark 内的每一次交互不计数。
心跳在命令调度前由后台发送；长驻服务的命令结果只在 CLI 返回时计数，
不宣称覆盖全部产品使用或任务成功率。

## 分离的数据契约

每日心跳 `POST /v1/ping`：

```json
{"schema":"loopx_usage_ping_v1","install_id":"00000000-0000-4000-8000-000000000001","version":"1.2.0","os":"linux","arch":"x64","python":"3.13","channel":"pip"}
```

ID 随机生成，属于持久机器状态目录，不绑定账号、不从硬件派生。普通 session 和
告知升级保留 ID，明确关闭删除 ID。临时 home、删/复制状态或重新开启会影响统计。
它能跨天关联，因此不能称为完全匿名。
系统只允许 `darwin|linux|windows|other`，架构只允许 `x64|arm64|x86|other`，
安装渠道只允许 `pip|local_release|source|unknown`。版本只接受数字三段式，包含
自定义后缀的版本不会上传。

无 ID CLI 诊断 `POST /v1/aggregate`（最初随告知版本 5 引入）：

```json
{"schema":"loopx_usage_diagnostics_v1","counters":[{"feature":"pr-review","operation":"merge-readiness","outcome":"blocked","error":"not_ready","duration":"lt_1s","count":4,"version":"1.2.3","activity_day":"2026-09-30","context":"unknown","signal":"none"}]}
```

默认新增数字三段版本、UTC 活动**日期**（非事件时间）、固定子操作、结果/原因及
回执支持的生命周期信号。环境类型由设备设置或优先级更高的 `LOOPX_USAGE_CONTEXT` 自愿声明：
`unknown`（默认）、`personal`、`shared_service`、`ephemeral`、`organization_managed`、
`maintainer`；无效值成为 `unknown`，不猜企业、人数或机器拓扑，不接受公司名称。
维护者可声明 `maintainer`，分开**未来诊断计数和每日安装概要**；不会标注心跳表，也无法追溯识别旧
无 ID 汇总。需要排除整机所有采集时仍用统一关闭开关。

收集器另加接收日期，仅接收此前七天至当天的活动日期，拒绝过期或未来数据。
表中没有安装 ID、Goal 或请求时间。两端共用 TS 契约；子操作来自解析器结构，
不读取参数值：

- `turn`：`plan|run-once|status`；`quota`：`status|plan|should-run|spend-slot|monitor-poll`。
- `todo`：`list|add|claim|update|complete`。
- `project`：`register|resolve|bind-session|unbind-session`。
- `pr-review`：`merge-readiness|check-result`；其他操作归为 `default`。
- 已验证回传观测使用 `other/result-return`。

结果为 `ok|blocked|failed|cancelled`；原因固定为
`none|not_ready|invalid_input|permission|not_found|timeout|connection|interrupted|command_failed`。
合并检查正常返回 `ready=false` 时记 `blocked/not_ready`，原非零退出码不改。
错误分类依据异常类型与明确布尔字段，不解析错误文字。

信号为 `none|project_registered|managed_turn_committed|todo_completed|todo_validated|result_returned`。
需要已有 changed/committed 回执，不靠成功退出推断；dry-run、未变化的 Todo 完成
不计新转移；Turn 重放不满足既有“本次 effects 已提交”判定。完成回执确有 passed
验证才记 `todo_validated`。回传目前仅覆盖 manager-context 精确来源、provider 已
验证且本次落为 delivered 的路径，旧路径或未验证回传不覆盖，不改变工作授权。

保留的旧 CLI 汇总契约（同一接收地址）：

```json
{"schema":"loopx_usage_aggregate_v1","counters":[{"feature":"todo","outcome":"ok","duration":"lt_1s","error":"none","count":4}]}
```

这一旧契约不带安装 ID、版本、时间戳、Goal 或其他关联键。接收端只添加接收日期并累加计数，
不保存逐次请求行。发送端和收集器共用严格的 TS 白名单：

- 功能：`status|quota|todo|turn|project|connect|pr-review|version|chat|other`。
  未列出的命令统一成为 `other`，不上传原始名称。
- 结果：`ok|failed|cancelled`。
- 耗时：`lt_100ms|lt_1s|lt_10s|lt_60s|gte_60s`，分别表示低于 100ms、
  100ms–1s、1–10s、10–60s、至少 60s。测量命令调度耗时，不是完整解释器启动或 Goal 耗时。
- 错误：`none|command_failed|timeout|connection|interrupted`，从异常类型判断，
  不解析和上传错误文字。

不采集提示词、代码、路径、仓库、命令参数、工具输出、原始错误、堆栈、Goal/Todo ID
以及用户自定义 Agent/MCP 名称。额外字段或非法枚举组合会被拒绝。

## 告知、设置与升级

交互式 CLI、后台脚本/Agent 和 App 统一采用 **首次告知 → 自动开启 → 后续采集**。
首次普通 CLI 命令向 stderr 显示接收方、字段、用途、CLI 发送频率、网络时序关联
边界和关闭方法，然后记录告知；
这一轮不计数、不发送。脚本和 Agent 工具调用捕获的 stderr 也适用，JSON stdout
保持不变。stderr 指向空设备或输出失败时不记录告知。后台 `chat`/`serve-status`
服务把首次告知交给 App，不以服务日志代替 App 界面。

首次打开可操作的 App 时，页面显示统计范围、接收方及“关闭统计”“了解详情”
和“收起”入口。告知在可见标签页中呈现后自动记录，不需要先进入设置或点击
启用。隐藏标签页、只读分享页面、设置服务不可用或单纯查询状态均不记录告知。
记录告知本身不发送统计，后续受支持活动才有资格采集；App 逐次点击仍不在采集范围内。
设置 → 能力中心保留共用开关和待发送数据预览。

这是对旧默认行为的调整：默认 `opt_out` 策略下，后台 CLI 不再要求明确开启，
App 不再要求首次点击启用。环境变量覆盖和 `consent_required` 的优先级不变。
旧版明确关闭的选择继续生效；旧版已经开启的保留随机 ID，但扩大范围前重新告知。

以下任一设置都会压过“已开启”，同时关闭所有通道：

- `LOOPX_USAGE_PING=0|false|no|off`
- `DO_NOT_TRACK` 非空且不是 `0`
- `CI` 非空且不是 `0|false`

项目 Python CI 和公共 smoke 工作流显式设置 `LOOPX_USAGE_PING=0`；pytest 与
canary smoke 执行器也在本地验证时关闭采集。Native Codex 评测 profile 的安装、
运行和 Agent 工具 shell 都强制关闭，即使最小环境丢掉了 `CI` 或父进程要求开启，
也不会恢复采集。发布资格验证同样关闭。这些合成运行不能计作真实使用安装。
遥测专项测试只能针对可丢弃的本地收集器显式开启。

`LOOPX_USAGE_POLICY=consent_required` 要求明确开启，单纯显示告知不够。
默认策略为 `opt_out`，未知值拒绝发送。发行方必须按实际适用要求选择策略；
该配置不自动判断法律合规，不按 IP 猜测地区，也不能替代必要的同意。

项目地址为 `https://loopx-usage-collector.huangrt01.workers.dev/v1/ping`。
可通过 `LOOPX_USAGE_PING_ENDPOINT` 设置另一个 HTTPS `/v1/ping` 地址；仅本地回环
测试允许 HTTP。拒绝用户名密码、查询参数和片段，不跟随重定向。汇总地址是同源的
`/v1/aggregate`。接收方或策略变化后，旧告知失效，需要明确开启确认；切换接收方
会清空待发送计数并更换 ID。

## 发送与关闭边界

新安装的整机状态在 `~/.loopx/usage-ping.json`，权限 `0600`。已有默认全局 registry
位于 `.codex/loopx` 时，统计状态继续沿旧路由，直到显式迁移；两份默认 registry
并存时拒绝隐式设置写入。整机路径独立于 Goal runtime root。它不进入 Goal 状态或
authority provider 备份、公共投影。普通命令只读取很小的本地提示；独立 Node 后台
进程负责计数、锁和网络。首次告知和设置操作可能等待本机 Node，不等待收集服务。
离线 `migrate-local-state` 命令不调度这些观察，避免后台写入改变预览和回滚收据
所绑定的整机状态字节。

每天最多尝试一次心跳。首次可采集的 CLI 命令结束后立即尝试发送汇总，包括失败结果；
后续有合格活动时，每隔至少 15 分钟发送一批新增计数，UTC 换日不重置间隔。
心跳和 CLI 汇总分别认领发送机会，心跳尝试不会压掉命令结果。单独启动不产生成功
计数，设置和 status 查询不触发发送。此行为替代已开启统计的交互式、无人值守 CLI
原有的次日发送机制；Goal 时长快照仍按天发送。

旧汇总最多 128 种组合，新诊断最多 32 种，每项封顶 10,000。诊断按活动日期过期，
旧汇总以最早积压日期计算，超过七个 UTC 日丢弃。溢出仅记有界本地
`diagnostic_dropped`，不建无限队列；旧缓冲可读。告知版本 5 要求扩大默认范围前重新告知：
确认前不发送、不消费缓存；确认后丢弃旧范围计数并更新 generation，阻止旧排队
观测补发，后续新测量才可发送。明确关闭继续生效，`consent_required` 仍须明确启用。
每批在发送前持久化认领时间、移除对应计数，并在同一短锁内发起请求，网络等待不占锁。失败或时钟回拨也不能绕过间隔。
不会立即重试，不增加后台定时器或持久网络队列。间隔内停止使用仍可能丢失未发送的
尾部计数；优化减少对次日回访的依赖，但不承诺完整采集。锁竞争、进程退出和网络
故障也可能丢数。这是有损诊断，不能当账单或审计日志。

新诊断带版本和活动日期，不带安装 ID 或事件时间；旧计数没有版本/活动日期，不能
追溯补归因。两种计数都不能除以上报安装数推算每安装用量。
更频繁的请求可能增加网络时序关联的机会；载荷不带标识不代表无法关联。

每个网络请求限时 3 秒，不阻塞命令完成、不改变输出和退出码。使用受支持 Node
运行时的 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 配置，代理地址与凭证不会进入遥测数据。
关闭删除本机 ID 和
待发送计数；旧后台进程不能恢复它们，也不能继续发送下一条通道。已经交给网络的
请求无法撤回；重新开启会生成新 ID。损坏或未知格式状态拒绝发送，可明确 disable 修复。
status/设置另显示最多 20 条本地发送摘要：UTC 日期、通道、行数和接收/拒绝/不可达。
不记请求正文、错误或 URL，关闭清空摘要。HTTP 接收不等于收集器持久化或工作验收；
待发送预览也不是发送回执。

## 服务端与解释边界

[收集器及升级说明](../../apps/usage-collector/README.md)。心跳保存 400 天，
汇总计数保存 30 天。原 `/v0/stats` 继续提供新旧客户端的去重活跃和新增安装数。
`/v1/aggregate-stats` 分别给出功能、结果、耗时和错误总量，低于 5 的格子不公开，
不公开多维组合或逐安装行为历史。
`/v1/diagnostic-stats` 分别提供最近 30 个接收日的功能、子操作、结果、原因、耗时、
版本、环境和信号总量，低于 5 的单元不公开。仅运营侧查询可排除
`context='maintainer'`、比较活动/接收日期，仍不能关联安装。
先应用增量迁移 `0004-diagnostics.sql` 并部署 Worker，再发布告知 v5 客户端；旧 Worker
会有损地拒绝新包，回滚可保留新增表，不给历史数据虚构诊断行。

`/v1/adoption-stats` 基于已有心跳，每个 1/7/30 天 cohort 取完整观察窗口已结束的
30 个首次出现日期。回访指 N 天内任意后续日期有心跳，不是严格第 N 天留存。
另给最近 30 天活跃天数分档，抑制小样本；各窗口重叠，不能相加。安装多、调用多
不足以判断企业采用、去重人数或付费客户。

应用代码不保存 IP、User-Agent 或 Cloudflare 请求元数据；部署模板关闭 Worker
observability。但网络服务商仍处理连接信息，分开数据包不保证绝对不可关联。
接口未认证，统计可能被灌水；缺失、抑制和采样偏差也使其不适用于计费。
部分网络无法访问该服务时可配置可达收集器，LoopX 的正常使用不受影响。

## Goal 执行时长观测

用三种**独立口径**判断工作是否跨小时、跨天持续。它们会重叠，不能相加：

| measurement | 范围 | 含义 |
| --- | --- | --- |
| `quota_cycle` | 使用通用 quota CLI 的所有 Host，包括 Codex App | 首次允许推进的 `should-run` 到成功执行 `spend-slot`，包含暂停和等待 |
| `codex_turn` | 已接受的 Codex 任务绑定，当前 Codex home 内的真实时间事件 | 更细的执行区间，也包含工具和审批等待 |
| `host_call` | 受管 `turn run-once`、普通用户 Goal 对话的实际 Host 调用 | 直接打点的调用区间，包含网络与工具等待 |

重复读 quota 不重置起点；拒绝推进、spend 预览、失败结算和回执修复不会结束周期。
成功结算重放不生成新的时间标记，也不延长终点。有精确 Turn 身份时按 Turn 区分；没有时每个 Goal/agent
通道只推断一个周期，无法区分同通道并发的无身份周期。缺少 spend 不虚构结束。
它是旁观统计，不影响准入、结算或业务状态。

Codex 发现使用既有 Goal/agent/task 绑定和所选 `CODEX_HOME` 的只读元数据，
不跨 home 搜索，不按工作目录猜测归属。quota 观测启动脱离前台的进程，每次最多
读取 1 MiB 新 JSONL（首次读取尾部），本地仅保留时间游标和未结束轮次身份。
原始会话内容不上传。spend 后才写入的结束事件需要等下次观测，并非全局实时监听。
绑定缺失、歧义或文件不可用不会阻断通用周期统计；不回填历史，不外推崩溃后的时间。

Codex 的 `started_at`/`completed_at` 支持 Unix 秒数和旧版 ISO 日期。
优先使用明确的 provider 时间；缺少起点时，结束事件必须匹配已观测的同一 Turn。
仅已结束或中止的 Turn 生成区间：`token_count` 的记录时间可能晚于实际完成，
不再用于外推未结束 Turn。本机原生会话可统计已完成的轮次，当前未结束轮次须
等结束事件被读取后计入。这修正了原来的前缀计时，不改写已发送的历史聚合。

三种口径没有强制大小关系。quota 周期通常包含多轮工作和等待；受管 Host 调用
可能比对应 Codex Turn 多出启动、收尾时间。原生会话、未结算周期和观测缺口
会改变总量关系。应在同一窗口分别核验覆盖，不能假定
`host_call <= codex_turn <= quota_cycle`，也不能将三者相加。

每个 Goal/口径/Host 分别计算 **span**（首次至最近观测活动，包含中间暂停）和
**duration**（已观测区间的并集）。同口径同 Host 内并行重叠只计一次；没有新证据
就不增长。它们不是 Goal 年龄、CPU 用时、完成证据或计费时长。Host 只允许固定枚举
`codex_app`、`codex_cli`、`claude_code`、`dsh`、`opencode`、`trae`、`kiro_cli`、
`other`、`unknown`，不发送自定义名称。新增标签只会被基于同一契约构建的接收端接受，
因此须先部署接收端，再发布会发送该标签的客户端。

每个有活动的统计序列每 UTC 日最多一份累计快照，次日发生观测或普通使用时发送。
未完成 Goal 也计入；静默 Goal 不每天重复计数。直接 Host 打点每分钟提交检查点。
单位是 **Goal/口径/Host-day 观测**，不是去重 Goal 或用户数，接收端不能跨天、
跨机器关联同一个 Goal。

这层统计独立于 File/SQLite/PostgreSQL，不改写权威状态。告知确认后才开始观测；
关闭、更换收集地址或清空本机状态会重新开始。崩溃、缺少检查点、锁竞争、网络失败
和容量限制均可能漏计。最多保留 64 条序列、每条 512 个近期不相交区间、128 个周期、
64 个 Codex 游标。已结束周期及最久未读的游标会让位给新工作；七天未读的游标过期。14 天前的区间压缩为累计数，90 天无活动的序列过期。
接纳七天内迟到观测；超过七天的周期/轮次区间、超过两分钟的直接 Host 检查点丢弃。
待发快照七天过期，不使用出站身份做重试去重。

出站样例：

```json
{"schema":"loopx_goal_usage_aggregate_v1","counters":[{"measurement":"quota_cycle","host":"codex_app","span":"lt_7d","duration":"lt_6h","count":1}]}
```

两种时长均分为小于 1 分钟、10 分钟、1 小时、6 小时、1 天、7 天、30 天及不少于
30 天八档。不发送 Goal ID、安装 ID、名称、路径、事件时间或自由文本。
`/v1/goals` 接收白名单数据；`/v1/goal-stats` 按口径分别给出最近 30 个接收日的
分布，少于 5 的单元不公开。设置与 `loopx usage-ping status` 的 `goal_preview`
是本机当前快照，不是发送回执。

沿用统一开关、环境变量和同意策略，关闭也停止本地时间读取。扩大范围需要当前第 5 版
告知，保留原有关闭选择。发布客户端前先备份 D1，依次应用 `0002-goal-usage.sql`、
`0003-goal-duration-sources.sql` 并部署 Worker。后者将旧计数转入 `host_call` /
`unknown`，保留旧表以便回滚，不影响心跳和 CLI 计数。尚未发布的 Goal v1 协议
现在要求口径和 Host 字段，并以 `duration` 替代 `execution`。
