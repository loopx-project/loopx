# Loop Engineering 原则与常见坑

这是一份中文短版，用来在阅读更长的产品设计文档前，先理解
LoopX 背后的工作模型。

Loop Engineering 关注的是让长期
agent work 在多轮 executor 之间保持可恢复、可审阅、可调度。短任务里，
一个 prompt 通常足够；长任务里，目标、人的判断、证据、成本和下一步会逐渐
散落到聊天、代码、文档、实验和外部反馈里。LoopX 要解决的是这些状态如何
不漂移。

## 原则

### 动态目标需要控制面

静态 prompt 适合短任务。一个长期运行的 Loop Agent 需要稳定的控制面来保存
goal state、user gate、todo、claim、scope、evidence、run history、quota
和 handoff。聊天记忆可以辅助理解现场，但不能成为长期任务的事实源。

### Human gate 是决策，不是打扰

Human-in-the-loop 不应该等价于每隔几分钟让用户确认一次。一个好的 gate
应该记录具体决策、被阻塞的路线、用户不回答时的安全默认行为，以及哪些工作
仍然可以独立推进。

这样人不需要做调度器，只在真正需要判断的位置出现。

### Safe fallback 要保持诚实

当 P0 路线等待用户判断或外部信号时，安全的 P1/P2 工作可以继续推进。
但 fallback 必须被明确标记为 fallback，不能掩盖主路线仍然 blocked，
也不能把“旁路有进展”写成“gate 已解决”。

### 流程引导要保留有边界的自主性

优先级、推荐 next action 和短候选列表应该帮助长期 loop 更容易被调度，不能把
为了可读性展示的少量建议意外变成权限白名单。只要一个行动仍满足当前 typed
claim、scope、capability、gate、quota 和安全边界，agent 就可以自主选择它，
再通过同一套 preflight、receipt binding、validation 和 writeback 流程完成审计。

真正的限制必须由 typed authority 或 transition contract 明确表达，不能从列表
顺序、展示条数或 prompt 措辞中推断。

### Feedback 不是权限

人的 reward、review 和偏好信号可以影响后续排序和规划，但不能绕过 gate、
claim、scope、capability check、public/private boundary 或 quota。

有用的 hint 仍然只是 hint。控制面的职责是把反馈转成可继承的信号。

### Evidence 要紧凑且可检查

下一轮 agent 需要足够证据恢复上下文，但 public surface 不应该复制原始日志、
transcript、benchmark trace、credential、private note 或本地路径。

更合适的做法是保留 compact artifact、验证结论和 source reference，让人和
下一轮 agent 都能知道：产物在哪里、验证了什么、还缺什么。

### Quota 保护的不只是算力，也是人的注意力

一个 loop 即使 token 很便宜，也可能持续消耗用户信任。Quota 不应该只数
agent 有没有说话，而应该看这一轮是否产生了经过验证的状态转移。

monitor-only 或 status-only 的 turn，除非发现状态变化，否则应该保持安静。
自动化的价值是减少调度负担。

### Loop Agent 需要绩效审阅

Loop Agent 的价值不能只用单任务 benchmark 分数衡量。长期项目更需要看：
产出数量、产出质量、token cost 和 user attention cost。

这四类信号让人能判断：哪个 agent lane 值得给更多信任，哪个 lane 只是忙，
但没有产生足够可复核的价值。

## 常见坑

### 把“跑更久”当成产品

没有更好状态管理的长 loop，只会制造更大的漂移。

### 用总结替代 writeback

如果路线、todo、gate、lesson 或优先级只存在于聊天里，下一轮 agent 迟早会
丢掉它。重要计划应该写成 typed todo、gate、evidence、review note 或
refresh-state record。

### 用繁忙的 fallback 遮住 blocked 主线

安全旁路有价值的前提，是 blocked primary route 仍然可见。否则用户看到的
是 activity，失去的是 control。

### 混淆 review feed 的偏好和硬策略

“这个有用”可以影响后续排序。“不要发布这个”是 boundary 或 gate。
UI 和控制面必须区分这些反馈的控制效果，否则 review feed 会变成隐式权限系统。

### 把每个 artifact 都算成 outcome progress

文档、smoke、status refresh 可能是有价值的准备工作，但只有当它直接推进了
选定 outcome，才应该被写成 primary outcome progress。

### 在前端里造第二个事实源

Dashboard 应该投影 LoopX state，并通过 typed write 写回。它不应该发明
隐藏队列、浏览器私有排序或 CLI / future agent 无法读取的控制决策。

### 过度声明 benchmark uplift

Benchmark 是证据。LoopX 当前更应该声明可展示的窄价值：长期 agent
work 更容易被检查、调度、恢复和比较。

## 最小可用 Loop

一个有用的 LoopX-managed loop，至少应该回答五个问题：

1. 当前目标和边界是什么？
2. 如果有人类决策阻塞，它阻塞的是哪条路线？
3. 上一轮 agent 实际验证了什么？
4. 如果用户什么都不做，下一步会发生什么？
5. 哪类反馈会改变后续计划？

如果这些问题可见，人就能管理一个 Loop Agent。如果不可见，系统大概率只是
在反复运行 agent。

## 相关文档

- [Intelligent management surface](../surfaces/intelligent-management-surface.md)
- [Non-technical operator status model](../surfaces/nontechnical-operator-status-model.md)
- [Project-level reward model](project-level-reward-model.md)
- [Reward-style replanning hints](reward-style-replanning.md)
- [English version](loop-engineering-principles-and-pitfalls.md)
