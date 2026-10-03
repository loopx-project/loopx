# 消耗的上限与外部观察

T1 完成后，M1 仍在等待 C1 的 CI，G1 仍在等待发布决定。预算还有余额，不代表应该反复调用模型；没有新消息，也不证明外部条件没有变化。本章把“是否行动”“何时再看”“谁去观察”与“何时改路”分开，再让它们共同支持一次可解释的等待。

## 先区分五种责任 {#observation-owners}

| 问题 | 负责的事实或规则 | 不能替代的责任 |
| --- | --- | --- |
| 当前可以推进哪项工作？ | quota 与当前 interaction contract | 不授予额外外部权限 |
| 在观察什么，最近看到了什么？ | Monitor 身份、目标与 observation metadata | 不凭空访问 CI 或创建运行进程 |
| 什么时候再看？ | cadence、next due 与 scheduler/backoff | 不保证届时一定有可用 Host |
| 谁真正执行观察或唤醒？ | 已接入且可用的 Host、运行器或事件通道 | 配置存在不证明运行健康 |
| 现有等待或路线是否值得继续？ | frontier、Vision 与 replan policy | 不是简单缩短或拉长 cadence |

实际资源成本还有自己的来源：供应商账单、工具使用和机器时间。LoopX 的内部 quota 结算不能代替这些数据。一次合法 no-spend 观察仍可能有真实成本。

## 一个错误计数策略会怎样误导判断

以下是**假设性的错误策略**，不是声称当前实现仍有此缺陷：把“最近连续没有变化”只记在一个全局目标上，每次换目标就清零。

| 时间 | 到期工作 | 正确的工作项事实 | 假设的错误全局判断 |
| --- | --- | --- | --- |
| 10:30 | M1，cadence 30m | M1 第一次无变化 | 刚看到 M1 |
| 10:45 | M2，cadence 45m | M2 第一次无变化 | 换成 M2，清掉前一目标计数 |
| 11:00 | M1 | M1 第二次无变化 | 又换目标，重新计数 |
| 11:30 | M1、M2 各自到期 | 分别更新自己的观察 | 结果取决于处理顺序，而非目标真正停滞多久 |

错误不在阈值太高，而在于把不同目标的观察混成了一段历史。修正应保留每个 Monitor 自己的身份和计数，而不是不断降低阈值。相反，假如根本没有可用 Host 去执行 due observation，计数器再正确也只是一份静态记录：这是执行活性缺口，不是 no-change。

## 等 CI 时，观察频率怎样选择 {#running-wait}

| 方案 | 适合的条件 | 成本与限制 |
| --- | --- | --- |
| 人工回来查看 | 低频、短期、允许较长延迟 | 依赖人的注意力，要保留接手信息 |
| 固定 cadence | 变化节奏可预计 | 长时间等待会产生重复观察 |
| 有上限的退避 | 长时间无变化 | 减少重复读取，但增加发现变化的延迟 |
| 真实事件通道加必要核验 | 已有可用通知和身份绑定 | 收到事件仍须核对目标、revision 和当前状态 |

M1 与 G1 不要求同一种动作。CI 需要外部结果读回，发布决定需要有权决定的人；不能以一次 CI passed 替代批准，也不应把用户待决定当成所有独立工作的全局冻结。

| M1 的实际观察 | 可保存的事实 | 合法后续 |
| --- | --- | --- |
| C1 的 CI pending，结果未变 | 有来源与时间的无变化观察 | 更新观察状态，按当前策略等待 |
| C1 的 CI passed | 绑定 C1 的新证据 | 重新检查 T3，包括 G1 |
| 当前代码已是 C2 | 旧绿色结果只支持 C1 | 获取适用于 C2 的验证，不删除 C1 历史 |
| 读取失败或无权访问 | 无法确认当前 CI | 报告读取缺口，不记成 unchanged 或 passed |

Monitor 保存调用者完成的观察。本身不访问 CI，`next_due_at` 也不创建一个执行进程。等待健康需要目标、观察规则和实际执行面同时存在。

## 预算参与准入，但不独自授权

正常交付仍受预算限制：当前窗口 `spent_slots >= allowed_slots` 时会进入相应 throttling。余额不足不能通过换 Todo 或把交付叫作观察绕过；余额充足也不能绕过 Gate、当前身份、能力、工作区和依赖。

读取最终 contract，至少区分“允许交付”“需要恢复或修复”“观察”“等待”与“要求决定”。身份不明时拒绝交付资格，不等于整个诊断过程不消耗任何资源。`quota should-run --codex-app` 的相关准入路径可能创建 heartbeat receipt，不应当作无副作用的循环查询。现场读取见[附录](appendix-reference.md#read-before-change)。

内部记账回答已接受的工作如何归因；scheduler cadence change、Gate 通知、dry-run、未变化的 poll、重复写回不能冒充新的 delivery spend。不要用“无费轮次”描述 no-spend，否则会掩盖观察本身的资源成本。

## 登记等待之后，谁可以继续？ {#wait-and-next-turn}

T1 发现真实依赖时，等待需要有具体对象，例如 `monitor_changed:<todo_id>` 或 `todo_done:<todo_id>`。保留验收器、原工作身份和依赖关系，不能通过把 Todo 草率标 done 消除当前阻塞。

工作图中存在独立 T2，并不意味着已绑定 T1 的当前 Turn 可以直接改绑。应先完成原 Turn 按其合同要求的等待写回和收口，再由后续准入选择新工作。**Turn 收口、Todo 等待与下轮选择是三件事。**

[Turn 章的等待时序](03-one-turn.md#wait-closeout)用主线 `f49b4a00…` 的明确测试解释这条路径，并标注了它不属于旧发布版承诺。它验证不重复扣减、不强迫执行 Monitor poll、原 Todo 的等待和验证声明仍保留；不是一条适用于所有 Host 的全局调度顺序。

## Cadence backoff 与路线重规划

Backoff 问的是“相同条件下，多久以后再观察”。它从已仲裁的 decision 和运行 profile 派生调度建议，并按对应 scheduler state 的 identity / `reset_token` 保持或重置退避。具体间隔、上限与 unchanged limit 应读取当前 profile，而不是把一组示例数值当作所有 Host 的共同协议。

如果没有事件通道，外部变化只能在下一次真实观察后被发现。reset 可以改变后续间隔，但不会追回已经等待的时间。Host apply 成功或已经匹配目标 cadence 后，才按绑定 ACK 路径确认；失败或超时要记录对应失败，不能伪造 ACK。详见[调度入口](appendix-reference.md#scheduler-entry)。

Replan 问的是“继续等待是否仍符合目标，是否有更好的下一步”。当前 Monitor 策略会结合当前 Agent 的可选 advancement、Monitor 类型和无变化历史判断，不能只看一个计数。

| 当前输入 | 受对应测试支持的判断 |
| --- | --- |
| 当前 Agent 没有可选 advancement，符合条件的普通 monitor-only lane 已达阈值 | 可能形成 `monitor_no_change_streak` replan obligation |
| 当前 Agent 自己有可选 advancement | 相关测试中优先选择工作，不产生该 Monitor 派生的 obligation |
| 只有 peer 有工作 | 不以 peer 的进展抹掉当前 Agent 的停滞 |
| Monitor 明确为 `watch_only` | 相关测试中即使计数很大，也不因此触发这项 replan |

[策略测试](https://github.com/loopx-project/loopx/blob/76b7583a9f67d6090b43a8c6e58c42cb67a1f3c6/tests/control_plane/test_monitor_replan_agent_scope.py)中的阈值为 5。这是被测策略，不是所有等待必须改路的普遍定理。测试预置计数后检查 decision；它不证明真实交错 poll、并发计数写入或真实 Host 唤醒。更完整的预测练习见[Monitor 检查](12-control-plane-course.md#checkpoint-monitor)。

因此，replan 不是“更强的 backoff”。一次可以改变观察频率，另一次需要重新判断路线并形成当前协议认可的结果；仅写“重新想过了”不能替代所需的变化或明确终止依据。

## 怎样定义一项可解释的观察

创建或修改 Monitor 前，确定稳定 target、可用观察句柄、cadence/next due、相关变化判据、观察边界和无变化的记录方式。

当前 metadata 合同允许 `expires_at`、`resume_when` 或显式 `watch_only=true` 作为 boundedness 的选择。expiry 与 due 不是同一个字段：前者约束何时结束，后者安排下一次观察；没有 expiry 不能直接推导 Monitor 非法。

变化判断必须与任务有关。服务响应中的无关时间戳变化不一定意味着验收前提改变；相同文本也不一定是同一 revision。把验证对象和解释保留在证据中，不把每次取回的原始响应无限塞进热路径。

[`monitor_metadata.ts`](https://github.com/loopx-project/loopx/blob/76b7583a9f67d6090b43a8c6e58c42cb67a1f3c6/loopx/control_plane/todos/monitor_metadata.ts)处理观察元数据和重放。领域观察由调用者提供，受控写入负责验证和记录；不同 Monitor 的计数不能因别的 lane 刚运行而互相覆盖。已有 canonical observation/successor 事务的原子边界见[状态机专题](core-state-machines.md)，不能把它扩展成“网络读取、结算和所有显示一起原子完成”。

## 故障时先定位缺哪一环

| 现象 | 先取证 | 后续与停止条件 |
| --- | --- | --- |
| 没有消息 | due、真实 Host 状态、最近成功观察 | 无 Host 就报告执行缺口，不宣称外部无变化 |
| 持续重复观察 | 目标身份、fingerprint、各自计数与 cadence | 修负责该层的策略；不调低所有阈值碰运气 |
| 已有新证据却没推进 | 证据 revision、当前 Gate、依赖和准入 | 条件不足继续等待；不要只改显示状态 |
| 经常要求 replan | 当前 lane、可选工作、watch-only 与 ACK 结果 | 提交实际认可的 replan 结果；不伪造一次 ACK |
| 观察已经提交但显示仍旧 | 原操作回执、当前源与投影 | 恢复投影，不再次提交相同业务变化 |

观察频率越高，读取成本通常越高；退避越长，发现延迟可能越大。等待策略的好坏应由任务对延迟、资源和人工介入的要求判断，不以轮次数或通知数量衡量。

本章的完成标准是：能说明在等什么、谁来观察、何时再看、哪一种变化会改变下一步，以及这份等待是否还有实际运行条件。需要操作时回到[Host 章节](06-codex-app.md)或[CLI 路径](07-codex-cli.md)，需要诊断时使用[附录](appendix-reference.md#diagnostic-routing)。
