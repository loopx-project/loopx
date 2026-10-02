# 长程运行提出的四个要求

一次修复可能经过修改代码、测试、等待 CI、回应 review、交接与交付。每步能在一个会话里完成，整件事却跨越会话、进程与人的决定。这一章提出四个阅读问题，不声称所有系统都必须采用同一机制，也不把设计目标当成 LoopX 所有路径已经具备的保证。

## 四个要求

| 问题 | 必须保存或判断什么 | LoopX 的主要处理方式 |
| --- | --- | --- |
| 换会话后怎样继续？ | 目标、工作项、证据与下一入口 | 持久状态与可重建投影 |
| 中断后怎样避免盲目重做？ | 已提交、未执行和未知的结果 | 原身份、journal、回执与 provider 读回 |
| 多个执行者怎样协作？ | 工作归属、scope 与当前执行证明 | Claim、lease、围栏与提交校验 |
| 没有变化时怎样少消耗？ | 预算、等待对象、观察与实际执行条件 | Quota、Monitor、scheduler/backoff 与 Host |

长程工作不要求进程永远存活，也不要求每次醒来都产生交付。合法等待和诚实停止也是系统需要表达的结果。

## 要求一：状态能脱离当前上下文

接手者需要知道精确 PR、commit、已经验证的行为和未解决的决定，而不是只读“我刚才修好了”。聊天可以被保存并解释历史，但它的持久性不自动产生结构、新鲜度或当前授权。

有影响的事实需要可寻址的来源、适用版本与读取入口。代价是维护这些记录；收益是换会话后可以重新判断，而不是凭记忆补齐缺失条件。读[状态章节](state-substrate.md)时，重点检查 source 与 display 在当前模式下分别是什么。

## 要求二：保留已知事实，也保留不确定性

外部操作可能成功而本地响应丢失。缺少一条本地记录，既不证明远端没发生，也不允许直接宣告完成。

受治理 Turn 用阶段和回执表达已确认进展，对未决效果按原身份读回。合法前缀缩小的是记录的解释空间，不消除外部提交与 checkpoint 之间的窗口。未知结果由原 owner 继续确认，不通过换 key 绕过；只有确认结果和当前许可后才选择复用或执行尚欠步骤。

“历史操作如何恢复”与“新的执行如何获准”不是同一个问题，见[恢复章节](04-runtime-boundaries.md#recovery-or-new-execution)。

## 要求三：工作归属与提交权限分别回答

两名 Agent 可以处理不同工作，也可能同时认为同一项工作归自己。Claim 表达责任，适用的 lease/fence 约束当前实例；二者都不能替代 Goal 边界、Gate scope、能力和工作区要求。

Default legacy、soft claim 和 hard lease 有不同实施边界，不应从一个模式推广出全局独占保证。多 peer 也不意味着全 Goal 只有一个执行者。接管和并行必须落到具体 writer 和当前输入上，见[权限分层](work-graph-and-authority.md#authority-layers)。

## 要求四：预算与观察约束无效重复

预算限制数量，但余额不能独自决定下一轮是否有意义。等待 CI 需要实际读取正确 revision；等待批准需要对应决定；没有可用 Host 的 next due 只是一个时间记录。

Monitor 保存目标与观察关系；scheduler/backoff 安排后续时机；Host 或已接入事件通道实际执行。退避减少重复读取但可能增加发现延迟；replan 重新判断路线，不是更强的退避。内部 no-spend 不表示模型、工具或网络免费，详见[观察责任](04b-budget-and-admission.md#observation-owners)。

## 四条要求怎样协同

```text
确定目标与当前工作
  → 读取来源，确认允许的动作
  → 执行、验证、由 owner 接受结果
  → 处理尚欠结算，安排下一步或等待
  → 重新观察变化，再决定继续、改路或停止
```

这是教学责任链，不是新的全局 API 顺序。单项机制不能保证目标成功；读者需要理解它们怎样为彼此提供依据，以及哪一种失败应回到哪一个 owner。

## 阅读路线与证据

**首次阅读**先进入[完整一轮](03-one-turn.md#running-turn)，看 T1 怎样形成可接受结果；再回到[状态](state-substrate.md)和[权限](work-graph-and-authority.md#design-choice)，理解支撑这轮工作的事实与写入者；随后读[恢复](04-runtime-boundaries.md)和[观察](04b-budget-and-admission.md)。这与导读中的主线相同，不要求先背状态机枚举。

**替代路线**适合已有控制面经验的读者：先状态与权限，再读 Turn。状态机专题与附录供回查，不是接入前必须全部完成的课程。

| 核对边界 | 协议入口 | 不由它单独证明 |
| --- | --- | --- |
| 来源与投影 | [长程状态协议](/loopx/docs/reference/protocols/long-horizon-agent-state-protocol-v0/) | 所有 reader 都是实时的 |
| 单轮恢复 | [LoopX Turn](/loopx/docs/reference/protocols/loopx-turn-v0/) | 任意外部效果都可自动重试 |
| 归属与接管 | [Peer runtime](/loopx/docs/reference/protocols/peer-agent-runtime-v1/) | 所有模式都有相同围栏 |
| 预算与等待 | [Quota 合同](/loopx/docs/quota-allocation/) | 未接入事件的 Host 能立刻知道外部变化 |

带着导读中的[四问标准](00-reading-guide.md#judgment-standard)往下读：当前事实、允许或拒绝的理由、支持结论的证据，以及合法下一入口。证据不够时保留未知，不为了让流程完整而猜测。
