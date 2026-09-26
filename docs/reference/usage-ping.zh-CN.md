# 基础使用统计

[English](usage-ping.md)

基础使用统计在**显著告知后默认开启，可关闭**。它用于决定平台支持优先级、
了解持续使用情况和发现慢命令，不代表 Goal 验收结果。本次不实现内容采集。

```bash
loopx usage-ping status      # 查看策略、接收方、待发送数据
loopx usage-ping disable     # 关闭两条通道，删除本机 ID 和待发送计数
loopx usage-ping enable      # 阅读告知后明确开启
```

设置 → 能力中心提供相同的整机开关与数据预览。打开设置和执行以上命令本身都不发送
统计。TypeScript 统一拥有策略、状态和字段校验；Python、浏览器只是适配入口。
它不依赖 Goal 的 File/SQLite/PostgreSQL provider，Lark 没有另一套开关，也不能覆盖
机器所有者的选择。

## 能回答什么

- 每日版本、系统、CPU 架构、Python 小版本和安装渠道：哪些环境需要优先维护。
- 随机安装 ID 的跨日心跳：有多少安装持续使用。它统计安装而非用户；重新安装或
  关闭后再开启可能计为新安装。现有统计接口提供活跃与新增；更细留存报表未实现。
- 固定 CLI 功能分类与次数：哪些入口常用。自动化轮询也会计数，不能当成用户价值。
- 命令结果、错误类别和耗时区间：哪些入口失败或慢。退出码为 0 不等于 Goal 完成；
  某些已处理的业务阻塞也可能返回 0。

第一版只计 CLI 调用，包括 Agent 发起的命令。顶层 `--help`/`--version` 快速路径、
原生 exec 替换的 scheduler followup、纯 API 操作以及 App/Lark 内的每一次交互不计数。
心跳在命令调度前由后台发送；长驻服务的命令结果只在 CLI 返回时计数，
不宣称覆盖全部产品使用或任务成功率。

## 两种数据包

每日心跳 `POST /v1/ping`：

```json
{"schema":"loopx_usage_ping_v1","install_id":"00000000-0000-4000-8000-000000000001","version":"1.2.0","os":"linux","arch":"x64","python":"3.13","channel":"pip"}
```

ID 随机生成，不绑定账号、不从硬件派生，但能跨天关联，因此不能称为完全匿名。
系统只允许 `darwin|linux|windows|other`，架构只允许 `x64|arm64|x86|other`，
安装渠道只允许 `pip|local_release|source|unknown`。版本只接受数字三段式，包含
自定义后缀的版本不会上传。

独立的 CLI 日汇总 `POST /v1/aggregate`：

```json
{"schema":"loopx_usage_aggregate_v1","counters":[{"feature":"todo","outcome":"ok","duration":"lt_1s","error":"none","count":4}]}
```

不带安装 ID、版本、时间戳、Goal 或其他关联键。接收端只添加接收日期并累加计数，
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

首次交互式 CLI 命令向 stderr 显示接收方、字段、用途和关闭方法，然后记录告知；
这一轮不计数、不发送。后续命令才有资格采集。全新无人值守安装不会静默启用，
需要所有者在 App 设置或 CLI 中明确开启。JSON stdout 保持不变。
旧版明确关闭的选择继续生效；旧版已经开启的保留随机 ID，但扩大范围前重新告知。

以下任一设置都会压过“已开启”，同时关闭两条通道：

- `LOOPX_USAGE_PING=0|false|no|off`
- `DO_NOT_TRACK` 非空且不是 `0`
- `CI` 非空且不是 `0|false`

`LOOPX_USAGE_POLICY=consent_required` 要求明确开启，单纯显示告知不够。
默认策略为 `opt_out`，未知值拒绝发送。发行方必须按实际适用要求选择策略；
该配置不自动判断法律合规，不按 IP 猜测地区，也不能替代必要的同意。

项目地址为 `https://loopx-usage-collector.huangrt01.workers.dev/v1/ping`。
可通过 `LOOPX_USAGE_PING_ENDPOINT` 设置另一个 HTTPS `/v1/ping` 地址；仅本地回环
测试允许 HTTP。拒绝用户名密码、查询参数和片段，不跟随重定向。汇总地址是同源的
`/v1/aggregate`。接收方或策略变化后，旧告知失效，需要明确开启确认；切换接收方
会清空待发送计数并更换 ID。

## 发送与关闭边界

整机状态在 `~/.codex/loopx/usage-ping.json`，权限 `0600`。它不进入 Goal 状态或
authority provider 备份、公共投影。普通命令只读取很小的本地提示；独立 Node 后台
进程负责计数、锁和网络。首次告知和设置操作可能等待本机 Node，不等待收集服务。

每天最多尝试一次心跳；本地汇总最多 128 种计数组合，每项封顶 10,000。
UTC 日期结束后的下一次合格调用发送上一日汇总，超过七天的积压丢弃；最后一天
之后不再运行的安装不会发送最后一日计数。锁竞争、进程退出和网络故障可能丢数，
不会立即重试，也没有持久网络队列。时钟回拨不重新开放当日尝试。
这是有损诊断，不能当账单或审计日志。

每个网络请求限时 3 秒，不阻塞命令完成、不改变输出和退出码。使用受支持 Node
运行时的 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY` 配置，代理地址与凭证不会进入遥测数据。
关闭删除本机 ID 和
待发送计数；旧后台进程不能恢复它们，也不能继续发送下一条通道。已经交给网络的
请求无法撤回；重新开启会生成新 ID。损坏或未知格式状态拒绝发送，可明确 disable 修复。

## 服务端与解释边界

[收集器及升级说明](../../apps/usage-collector/README.md)。心跳保存 400 天，
汇总计数保存 30 天。原 `/v0/stats` 继续提供新旧客户端的去重活跃和新增安装数。
`/v1/aggregate-stats` 分别给出功能、结果、耗时和错误总量，低于 5 的格子不公开，
不公开多维组合或逐安装行为历史。

应用代码不保存 IP、User-Agent 或 Cloudflare 请求元数据；部署模板关闭 Worker
observability。但网络服务商仍处理连接信息，分开数据包不保证绝对不可关联。
接口未认证，统计可能被灌水；缺失、抑制和采样偏差也使其不适用于计费。
部分网络无法访问该服务时可配置可达收集器，LoopX 的正常使用不受影响。
