# RFC：面向独立验证的 Hook 协同契约（v0）

- **RFC 状态：** Accepted (upon merge)
- **替代 / 关闭：** 无
- **交付成熟度：** TypeScript 接口及可执行契约验证样例。历史工作集接入和宿主资格待验证。
- **关联契约：** [控制器与执行器](controller-executor-unified-contract-v0.zh-CN.md)、[历史工作集](history-working-set-context-policy-v0.zh-CN.md)、[Target、Yardstick 与 Stride](target-yardstick-stride-decisions-v0.zh-CN.md)、[能力组合](goal-scoped-capability-portfolio-v0.zh-CN.md)
- **语言镜像：** [English（语义镜像）](verifier-hook-co-design-v0.md)

## 1. 权限与求解视图

通过公开宿主 Hook 和会话分支，在四个边界提高可独立验收的成果并降低完整成本。C1 继续持有 Goal、Todo、验收、证据和结算。求解器消费目标、累积验收及硬约束、Verifier Profile、已验历史最优和证伪引用的投影。启动和恢复视图通过原 C1 捕获散列和 `/goal_boundary` 指针读回同一套写入范围与停止条件。控制器保留完整决策并走既有准入与结算。模型成功响应、Hook 触发和成果通过验收是独立事实。

Goal Verifier Profile 绑定外部权威验证器（Oracle）的版本，可引用现有快尺配置。快尺为探索提供方向，独立 Oracle 按当前验收核验确切候选。缺证保持未知。验证器在求解器可写工作区之外运行，由独立进程和文件权限保护。外部行动沿现有控制。结算时区分候选和验证器改动。Diff 审计发现偏离后可暂缓验收。文件系统隔离和外部授权在副作用之前实施保护。

采用官方扩展和未修改的宿主内核，各宿主走各自支持的身份认证，凭据限于原客户端。适配器按版本与入口报告观察到的行为。关闭功能时保持现有执行、C1 传输和结算行为。

## 2. 能力协商与边界

功能关闭时没有验证 Hook 参与。启用后，L0 以有界启动和停止保留独立 Oracle 与结算语义。L1 额外要求观察到会话血统及有界上下文交付。L2 再要求可观测里程碑、隔离子工作和经验证的回传。按已支持的最高档执行。可选前置扩展须显式选择且存在已观察匹配的 Hook。每一档常规求解都默认关闭此扩展。能力和档位本身不授予权限。

| 边界 | 行为 | 回退 |
| --- | --- | --- |
| 启动或恢复 | 实际恢复或阶段变化时交付有界求解视图或精确引用。安静轮次省去重复记账上下文。 | 在有界启动交付同一视图。实际缓存命中与前缀字节一致性分别测量。 |
| 里程碑采样 | 将已配置且有预算的观测排至同步工具路径之外。在下一可用反馈边界带回有证据的平台期、回退或排序反转。 | 下一有界回合读回，无法观测保持未知。 |
| 压缩或子作用域 | 边界前固化未决约束及证伪引用，之后恢复核验过的投影。子工作经过验证后才回传精简结果。 | 保留原始输入，在下一支持的边界回传。 |
| 停止或成果声明 | 将确切候选交独立 Oracle。有界失败回传可复现反例，昂贵或未结束的验证归控制器推进。 | 宿主无法原位继续时，通过普通结算保留未验状态。 |

`completion_claim` 提交 Oracle。 `yardstick_plateau_or_regression` 交给 Stride。 `proxy_order_reversed` 由快尺 owner 核查或换尺。 `protected_boundary_touch` 保留隔离拒绝或审计证据，停止受影响尝试并按原权限重新进入。观测不能直接选换 Todo、激活能力、发布或记账。常规工作避免逐工具同步验证。

| 宿主 | 启动或恢复 | 里程碑 | 压缩或子作用域 | 停止 |
| --- | --- | --- | --- | --- |
| 官方 Codex | `SessionStart`、受支持的 `UserPromptSubmit` 上下文、原生血统与 Turn 绑定 MCP 探针 | 选定 `PostToolUse` 或工具完成事件入队后尽快返回 | `PreCompact` / `PostCompact` 观察压缩边界，经受支持的 SessionStart 上下文或绑定读取恢复、`SubagentStart` / `SubagentStop` 生命周期、App Server `thread/fork` 分支 | 支持时由 `Stop` 反馈阻断，否则控制器结算 |
| DSH Cordis | 插件观察会话及首个绑定回合 | `session/event` 的 `step/end` | 观察受支持的会话事件，具体分支、恢复和验证回传通过核验才能声明 L2 | `session/event` 的 `turn/end` 进入控制器验收 |
| Claude Code | `SessionStart` 与受支持的 `UserPromptSubmit` | 选定 `PostToolUse` | 受支持的 `PreCompact` / `PostCompact` 与 `SubagentStart` / `SubagentStop`，在已安装版本验证压缩后输出，否则下次启动恢复 | 受支持的 `Stop` 反馈 |
| Google Antigravity | `SessionStart` 或下一有界 `PreInvocation` | 选定 `PostToolUse` 或有实质进展的 `PostInvocation` | 保留原内容并在下一可用启动时恢复 | `Stop`，控制器独立验收 |
| 通用 CLI 或 Pi 兼容层 | 控制器供有界启动 | 在回合边界读回 | 保留原内容，按适配器核验增强行为 | 外层 Stop-Hook 或有界退出 |

官方 Codex 和 DSH 是互补的参考适配器。官方 Codex 负责同宿主强模型对照、关键候选和验证器设计。DSH 承接获准的高吞吐筛选和插件试验。两边复用统一产物身份，跨宿主成果单独报告。Pi 可按能力接入。可选前置接口包括 Codex、Claude 和 Antigravity 的 `PreToolUse`，以及粒度不同的 DSH `agent/pre-step`。注册 Hook 本身无法保证文件系统隔离。 [Codex Hooks](https://learn.chatgpt.com/docs/hooks)、[Codex App Server](https://learn.chatgpt.com/docs/app-server)、[Claude Code Hooks](https://code.claude.com/docs/en/hooks)、[Antigravity Hooks](https://antigravity.google/docs/hooks)、[DSH 适配器](../../../packages/dsh-loopx-plugin/src/driver.ts)。

## 3. VerifiedClose

1. 以公开宿主或控制器操作，从已识别会话及父纪元派生分支，隔离候选工作区。复制会话不授予写租约、验证权限或凭据，也不证明缓存命中。
2. 捕获确切候选交给独立 Oracle。其不可变回执绑定 Goal、Todo、父纪元、子作用域、`candidate_sha` 和 `verifier_version`。`candidate_sha` 使用提交给 Oracle 的候选内容摘要，格式为 `sha256:<64 位小写十六进制字符>`。读取回执前先检查摘要格式。分支、标签和其他格式都保留原始作用域。回执记录可用快慢尺读数、裁决及证伪条目，包含原始观测或轨迹引用。身份匹配的失败回执提供可复现反例，缺失读数保持未知。
3. 通过受信任原验证器解析回执。作用域标识都非空、回执身份一致且通过验证、无未决义务并有证据支持低重叠时，才能提议 `FoldToReceipt`。权威读取方为 Oracle 证据、原始来源和摘要返回各自登记的角色与作用域。每项都须匹配所请求的引用、对应的角色和确切回执身份。任一引用为空、错绑或无法读取时维持 `KeepRaw`。文件系统报告记录缺失、拒绝访问或路径类型不符时，读取边界将其视为不可用。解析错误及意外读取故障保持可见。在受支持的父会话边界追加精简证据，并保留可独立重读的原件。回执缺失、不匹配、失败或重叠高或未知时保持 `KeepRaw`。有角色和作用域都匹配的反例则回传。

按下一步工作的证据依赖判断重叠。下一探针仍需中间候选状态、未决反例或原始比较历史时为高重叠。下一作用域凭已核验结果及有界证据引用即可推进时为低重叠。下一步未绑定或依赖未读全时保持未知。结合节省的上下文字节、冷前缀重建和预期恢复读取，沿既有策略判断重置能否回本。

现有历史策略持有状态跃迁。仅允许该策略按收支平衡及精确源检查显式执行 `EpochReset` 重建前缀。子分支回传不自动开启新纪元。用压缩事件标记边界。失效后沿模型可见的 Hook 或绑定读取恢复。Codex 压缩事件本身无法注入任意模型上下文或替换采样编译器。Spine 说明结构化子工作可以保护父上下文，但其内核编译及论文结果不构成 Hook 路线验收。缓存命中、完整成本及成果质量须独立测量。 [Spine](https://arxiv.org/abs/2610.37224)、[历史工作集](history-working-set-context-policy-v0.zh-CN.md)。

## 4. 经 Verifier 准入的领域插件

求解器可暂存按内容寻址的快尺、诊断探针和验证配方。独立 trial 测试留出的失败样例、正常样例、开销及新快尺对 Oracle 的排序保真。试验回执绑定确切产物、验证器和配置作用域。试验通过后，沿现有 Goal 配置和能力组合决定是否采用。通过试验不自动激活产物、不增加权限、不替换 Oracle。采用后观测效用，在成本或保真退化时使用现有降级或退役入口。保留历史版本及负结果。DSH 是其中一种交付适配器。 [能力组合](goal-scoped-capability-portfolio-v0.zh-CN.md)。

## 5. 验收

- 可执行契约验证覆盖功能关闭、L0/L1/L2 协商、显式前置扩展、确切已验回传、拒绝其他作用域的回执以及真实的文件缺失、权限拒绝和错型路径读取。运行双语和基于 diff 的 premerge 检查。
- 首个正式接入切片使用既有历史工作集读取方处理一次已验证的子工作回传。在同一 Goal/Todo 上覆盖普通回合、验证失败或缺证、压缩或分支后的原回执读回及确切 C1 结算重放。在实际安装的宿主版本和入口验证 Hook 输出。
- 匹配模型、任务、访问权限和预算，对照同宿主 Stop-Hook。记录验收成果、错误完成、耗时、缓存和未缓存 token、验证调用、重试、存储及人工介入。覆盖高低重叠工作。跨宿主结果不计入同宿主效果归因。
