# RFC：自动执行准入（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** Partial，候选实现，尚未推广
- **维护边界：** quota、scheduler、host runtime
- **创建 / 规范修订：** 2026-09-23
- **实现基线：** `79241d7ef`
- **语言镜像：** 本文与 [English](automatic-execution-admission-v0.md) 互为语义镜像。
- **相关契约：** [路线图](loopx-overall-roadmap-v0.zh-CN.md)、[quota](../../quota-allocation.md)、[节奏提示](../../operations/long-task-cadence-policy.md)、[执行模式](agent-session-execution-modes-v0.md)

第 1–12 节定义设计与验收契约；附录记录交付边界。实现某个阶段不等于 RFC 已获批准。

## 1. 决策摘要

用户设定的自动执行最短间隔，由 **TypeScript quota 准入边界**保存并执行。
scheduler 退避消费这一约束，不能改写它。配额槽、定时器唤醒和模型调用是三个事件。
目标设计要求受控执行器启动每次新的 host 调用前，同时满足时间准入、预算、权限、绑定和工作门禁。

未配置时保持原行为。M1 修改 Codex App 的调度建议、重置与退避。M2 为 managed
`turn run-once` 增加 host 启动前准入；App 定时器和其他 launcher 仍需单独验收。
每次新的 managed host 调用及失败结果重试重新准入；缓存结果和结算不重复消耗准入。
本 RFC 不授权修改现有自动化、模型选择、配额分配、远程服务或公开发布。

## 2. 问题与不变量

用户要求每天最多自动执行一次，但新 Todo、失败或重新规划把退避重置为三分钟；宿主适配器
又将一天的间隔压成一小时。通知冷却只能减少消息，不能阻止模型消费。

- 用户约束跨重置、重规划、重试、重启和模型变更保留。
- 并发 tick 共享持久准入；错过的 tick 合并，不补跑一串任务。
- 准入后启动失败也消耗该时间间隔；启动结果不明不自动退还，避免重复消费。
- 时间准入不授予预算、权限、任务认领或结算资格。
- 手动执行需要显式意图，只绕过时间下限。
- 没有模型启动前钩子的宿主，不能宣称阻止了唤醒 token 消费。

## 3. 范围

覆盖单 runtime root 内 Goal / agent / automation 的间隔策略、受控启动、调度投影与能力回读。
低成本采集和付费模型决策分离。不做分布式预留、精确 token 硬上限、新 daemon、第二套配额账本、
通用工作流重写或自动激活宿主。

## 4. 基线事实

Quota 管工作资格与验证后的记账；scheduler hint 管退避和宿主 ACK，App 存在 60 分钟的旧上限。
`loopx turn run-once` 管 host 启动、恢复与结算。Codex App 自己管理定时器，模型内 LoopX guard 在模型启动后运行；runtime hook 则是另一条
潜在的启动前路径，调研结论见附录。现有 compute budget / replan 设置没有表示执行最短间隔。
long-task cadence 是建议，不是用户约束。

## 5. 架构与状态

| 边界 | 职责 | 不代表 |
| --- | --- | --- |
| Quota 配置 | 用户下限、修订版本、作用域继承 | 实际消费或权限 |
| Quota 准入 | 受控调用前原子预留启动 | 工作成功或槽位记账 |
| Scheduler | 下一次可执行时间与退避 | 降低下限的权限 |
| Turn executor | 启动、有界调用、恢复与结算 | 宿主不具备的保障 |
| Host adapter | 应用并读回真实定时器，公开能力限制 | 没有启动前准入时的零成本唤醒 |
| 用量与通知 | 观测消费、管理注意力 | 执行许可 |

时间准入与现有门禁取交集。最短间隔按两次**开始时间**计算。Goal 默认值分别作用于每个 agent，
不是所有 agent 争抢每天一个全局槽。agent 规则约束它的所有 automation，automation 规则进一步收紧
指定通道。有效下限是所有适用规则的最大值；子作用域不能削弱父规则，需要显式修改规则所属层级。

目标准入方案通过现有原子文件写入与锁，将策略和启动记录保存在同一事务里。M1 只持久化配置，
TypeScript 管校验、继承和 CAS，Python 只传输；M2 再向同一 owner 加入启动记录。这是 quota 子域，不创建新 capability/provider；
App、Turn、前端、Lark 不得另存一套策略。共享 authority provider 只有实现等价原子语义后，
才能宣称覆盖跨主机执行。

配置包含 Goal/agent/automation 身份、最短分钟数、配置版本和用户指令引用。修改必须匹配版本；
降低或关闭需要显式批准。零只解除当前作用域约束，不清空启动记录。本机 CLI 记录调用者声明的
用户意图，**不构成对同一 OS 用户进程的身份鉴权沙箱**。

准入记录保留开始时间、请求身份与触发时间，不绑定 scheduler reset key、Todo 或模型。
回读包含有效下限、来源作用域与版本、下一次可执行时间和原因。重复或过期 tick 被拒绝；到期后
先持久预留再启动。损坏状态拒绝执行；时钟回退延后准入。正确性依赖可信宿主时钟，不防护时钟操纵。

### 宿主矩阵

| 入口 | 必须履行的契约 | 本阶段边界 |
| --- | --- | --- |
| Managed `turn run-once` | 每次新 host 尝试及失败重试前原子准入 | M2 候选；已做隔离 CLI、并发与跨边界崩溃恢复测试，未推广外部宿主 |
| 旧 local scheduler / 外部 launcher | 经受控 Turn 启动，或调用相同准入 owner | 尚未验收，不宣传为已强制执行 |
| Codex App automation | 应用满足下限的定时器，回读真实值，事实匹配后 ACK | M1 调度建议下限；hook 覆盖范围未验收 |
| 附着式交互 / 手动会话 | 显式手动意图；其余门禁保留 | 记录原因；自动续跑不能冒充手动 |

App 必须显示目标和实际 schedule、应用失败。不支持该间隔时，应挂起受影响自动化，不能缩短间隔。
激活路径必须独立验收后才能宣传一键安全激活。模型内 guard 只能停止后续工作，不能撤销已消费的
唤醒 token；仅修改 prompt 不等于强制执行。原生 `UserPromptSubmit` 是候选方案；自动化覆盖未验收前，
调度建议模式不能宣称阻止所有过早模型启动。

### 有界执行与低成本采集

一次准入对应一次 host 调用。managed loop 的重试和后续调用重新准入，失败不会产生免费重试风暴。
拒绝时回传 wait / next eligible，不启动 host、不记 quota spend。缓存结果的结算可继续。
已有最大 turn 数、进程或 provider timeout 仍独立有效。没有 provider 强制限制时，token 提示是软预算；
任意 callback runner 和外部 App 会话不会因此获得硬取消保证。

只读采集和缓存状态不消耗模型准入，可以合并待处理触发；不能在“低成本采集”里偷偷调用 LLM，
也不能把没有实质变化的轮询转换为付费执行。通知策略与两者独立。

## 6. 替代方案

只增大退避会被重置，也无法串行化并发启动；通知冷却不节省执行；App 专用设置重复了权威，
且漏掉 managed Turn。通用分布式预算框架会拖延真实本地修复。选择 quota 内的有界子域，
复用现有锁与 effect transport，共享 provider 留到后续独立验收。

## 7. 安全与兼容

默认不启用；没有规则时读与准入不创建 cadence 状态，保留原启动和重试行为。
未接入准入的执行器不能宣称受策略保护。automation 使用稳定身份；省略 automation id 不会继承
它的专属规则。跨入口覆盖应使用 agent 或 Goal 规则。规则不授予凭证或远程权限，owner reference
应使用简短、非敏感引用。

## 8. 迁移与回退

首次配置前暂停受影响 launcher。读取、预览精确版本修改、执行、回读，再确认入口能力后恢复。
升级保留策略与启动记录。旧版本会忽略新策略，降级前先暂停，不能静默恢复旧执行器。
关闭需用户显式批准，在每个有效作用域写零；不要通过删除状态文件回退。

## 9. 验收

| 主张 | 决定性证据 | 排除项 |
| --- | --- | --- |
| 每日间隔 | 假时钟：24h 减 1ms 拒绝，整 24h 允许 | 时钟操纵 |
| 持久单次启动 | 真临时文件上的并发与进程重启 | 分布式共享文件系统 |
| 用户控制 | 继承、版本冲突、拒绝未授权降低、显式关闭 | 同 UID 身份鉴权 |
| 重置不绕过 | replan/reset/模型变更后保留下限 | 未接入 launcher |
| 无重试风暴 | host 失败与错误结果重试被拦截，拒绝不记账 | provider 内部工具调用上限 |
| 关闭兼容 | 原 executor 测试；无规则不创建 cadence 文件 | 启用通道有意变化 |
| App 如实表述 | 目标定时器、能力回读；错误 ACK 拒绝 | 零 token 唤醒承诺 |
| 完整产品 | 设置/CLI 同源投影与打包界面交互 | 后续 companion 阶段 |

## 10. 运维

排查“卡住”时先看配置版本、来源、下次时间和原因；区分时间等待、配额耗尽、宿主不可用和等待用户。
不能通过清理退避历史解除等待。策略和启动记录一起备份；恢复旧快照可能提前放行，需要保守等待。
未来清理策略必须保留每个有效作用域最后一次启动。

## 11. 与路线图衔接的交付计划

| 阶段 | 结果 | 退出 / 回退 |
| --- | --- | --- |
| M1 · S7/S2/S4 | Codex App 优先：用户 CLI、持久继承下限、重置/退避后的建议、激活指导与精简回读 | 真文件/CLI 与 App 投影负向验证；显式开启，回退前暂停 |
| M2 · S4/R2 | managed Turn 原子准入及 App 定向 hook 验收；定时器真实回读与旧 launcher 覆盖 | 隔离宿主、手动/自动身份、hook 信任/失败、不支持时挂起与有界续跑验收 |
| M3 · S5/S7 | 现有设置编辑器呈现继承下限与下次时间；前端/Lark/CLI 统一 quota 等待反馈 | 打包交互与读回一致，不另建策略源 |
| M4 · S7/R6 | 真实跨主机调用需要时才扩展共享预留 | provider 并发、fence、恢复验证与显式推广 |

M1 对 App 调度管理有独立价值，但不代表多宿主产品旅程完成。M2/M3 未验收前，整体产品目标保持开放。

## 12. 未决问题

1. Host 维护者：验收 App heartbeat 是否经过 `UserPromptSubmit`，以及失败/信任与自动/手动身份；
   零 token 唤醒主张必须等这些证据通过。
2. Quota/runtime 维护者：按调用还是按有界工作段准入；先采用保守的按调用规则，真实续跑证据
   支持显式时间/步骤限制后，再评估工作段。
3. 设置维护者：M3 的 agent/automation 编辑入口；复用现有 quota/configuration 投影，不另存偏好。

## 附录：实现记录

`23edcb19c` 基线没有持久用户最短间隔。M1 已由
[#4921](https://github.com/loopx-project/loopx/pull/4921) 合入，受管 Turn 准入已由
[#4929](https://github.com/loopx-project/loopx/pull/4929) 合入。M2 候选在
同一 quota 策略文件与锁中预留 managed Turn 的启动位，再调用 host。拒绝时返回下次
可执行时间，不调用 host、不写回、不花 quota；失败的 host 消耗已预留间隔，结算回放
跳过准入。Goal 下限按 agent 生效；automation 下限要求显式稳定的 `--automation-id`。
手动启动要求 `--manual-interval-bypass-reason`，记录这次启动，只绕过时间下限。
本地 CLI 仍以相同 OS 用户为信任边界。

managed 启动在同一 store 内分两步：准入预留间隔位，Turn executor 只在该 host 尝试
已写入 Turn journal 之后确认该预留。两步之间进程退出时，同一 Turn 身份在满足时间
下限后仍可恢复，因此"已预留但未启动"不会永久卡住 Turn；已确认的启动对同一身份保持
fail-closed，显式手动理由也无法绕过。缺少阶段字段的旧记录按"已尝试启动"读取，
旧版或手工改写的文件因此 fail-closed，而不是被当作可恢复预留。

M3 设置页阶段成果复用 quota 权威，提供 Goal／Agent／Automation 作用域的修订号锁定
预览、应用与读回，并把过期的配置意图作为 typed conflict 报出，而不是解析错误文案。
它不修改已有 Codex App 定时器；下次可运行时间及 Lark／CLI 等待反馈一致性仍未完成。
App 定时器到 hook、非 Turn launcher 及真实模型宿主推广仍未验收，M4 仍是设计选项。
本提案不激活、不改绑任何已有自动化；测试和 PR 必须区分确定性验证与宿主推广。

M1 的 v1 策略文件在首次配置写入或获准启动时原地升级为 v2，文件路径保持不变。
旧版程序会拒绝 v2 schema，避免静默丢弃启动记录；降级前必须暂停 launcher。

### Hook 调研 — 2026-09-23

[官方 Hooks 文档](https://learn.chatgpt.com/docs/hooks) 已定义 `UserPromptSubmit` 阻断与 hook 信任审核。
已公开输入有 prompt、turn/session id，没有结构化 automation id 或 trigger origin。
[定时任务文档](https://learn.chatgpt.com/docs/automations) 描述分钟间隔及每日/每周调度，
但两份文档都没有证明 heartbeat 全路径覆盖 hook 或专属启动 SLA。

隔离实验使用 App 自带内核 `0.155.0-alpha.9`、合成 heartbeat prompt、临时配置及本地 HTTP 计数端点。
hook 阻断时 provider 请求为零、用量回执为零；允许通过的对照组到达本地端点，共六次请求（含重试）。
未使用真实模型服务或凭证。仅在临时实验里，对已审阅 fixture 使用单次调用的 hook trust bypass。
这验证了 runtime hook 机制，**没有**验证 App 定时器到 hook 的路径、已安装信任、重连/恢复或可靠的
自动/手动分类；没有安装生产 hook。原始 prompt 标记不能充当经过身份认证的触发来源。
