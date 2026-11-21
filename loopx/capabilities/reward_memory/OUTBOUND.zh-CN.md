# 外发消息前的指导召回

[English](OUTBOUND.md)

已启用 Agent 的配置缺失、无效或 corpus 作用域不一致时，现在返回
`configuration_error`，在调用记忆 provider 前停止发送。此前这些错误会静默移除
hook。urgent 和旧确认摘要都不能豁免；需修正配置并通过
`reward-memory experiment-status` 核验，不能通过丢弃无效必读字段或自动关闭能力来修复。
明确关闭的 Agent、有效的 `automatic_recall: false` 配置、有效但未包含此 surface
的配置仍保留原发送行为。

这个可选的 Reward Memory surface 会在 Agent 发送消息前召回已经审阅过的
操作偏好。首个正式调用方是绑定
Goal/Agent 的 `loopx lark-inbox send` 和 `reply`；本次集成不拦截其他工具，
也不拦截 Goal Topic 自动回复。

## 启用与验证

使用现有 Agent 级 Reward Memory 实验配置：把
`outbound_message.before_send` 加入 corpus、standing policy 与 `surfaces`，
adapter 使用 `scoped_feedback`，`peer_ref` 必须精确为
`agent:<agent-id>`，并启用 `automation.automatic_recall`。建议采用一次查询、
小结果上限的 function-boundary profile。只写入明确审阅过且可公开的
`soft_preference`；不要上传消息草稿或私有事故记录。

```sh
loopx configure-goal --goal-id <goal-id> \
  --reward-memory-config .loopx/config/reward-memory.json \
  --reward-memory-agent <agent-id> --execute
loopx reward-memory experiment-status --goal-id <goal-id> --agent-id <agent-id>
loopx lark-inbox send --goal-id <goal-id> --agent-id <agent-id> \
  --route-key <configured-route> --text '<message>' \
  --message-purpose help --provider-preflight --format json
```

Provider preflight 先校验身份、群成员、mention 和 provider dry-run，再执行
召回；不带 `--provider-preflight` 的普通预览不会调用任何 provider。启用后，
结果中会包含 `outbound_guidance`，但不会发消息。有相关指导时，即使第一次
带了 `--execute`，也会返回 `agent_review_required` 且写入次数为零。

执行 Agent 需要阅读指导，核对当前事实、替代方案、收件方和重复消息；仍然
应该发送时，用同一条 send/reply 命令加上 `--execute` 和返回的
`--reviewed-guidance-digest`。这是 Agent 的审视步骤。
摘要绑定了指导、用途、scope、发送方、目标、placement 和消息；任一变化都会
让旧摘要失效。它只能证明 Agent 确认过指导。原发送器继续
负责幂等和 readback。

## 目标群召回与必读记录

启用后，send/reply 根据已验证的实际群 ID 分别执行“目标群经验”和“消息用途”
两路召回，每路沿用一次查询的 profile，合并时按 candidate 引用去重。
这是启用状态下从一次到两次查询的行为变化；未启用时保持原行为。

在 outbound surface 中可配置 `destinations` 数组，每项包含：

- `destination_digest`：实际群 ID 的 SHA-256，小写十六进制 64 位。
- `query_label`：适合交给当前 provider 检索的群名或别名，最多 120 字符。
- `required_candidate_refs`：最多三个必须读取的已审核 candidate 引用。

群名只辅助检索，实际绑定依赖发送器验证过的群 ID。改名后身份不变，同名群
不共享必读配置；改名时应更新标签。未配置标签时仍以目标摘要查询。相关经验
需要包含该标签或摘要，才能支持目标检索。具体 JSON 见英文文档。

每条必读记录另有一次精确引用查询，必须通过 active、scope 和正文校验并进入
最终指导列表。缺失时返回 `required_guidance_missing` 并停止发送，urgent 或
复用旧 review digest 也不能跳过；完整时 urgent 也必须审阅。这是明确配置的
必读契约。Agent 仍需遵守记忆中的禁发要求，
确认摘要不授予发送权，也不拦截其他发送工具。总计最多五路有界召回。

OpenViking 的 Reward Memory 查询只取 L2 正文，最多多取 32 个候选；先规范化
chunk URI、排除摘要和不可读或格式错误的记录，再填充结果上限。应用层按 scope、
有效期和 candidate 去重后截取。其他 namespace 的查询保持原行为；这不是全量扫描。

验收应使用真实目标和消息用途的只读 preflight，检查必读记录实际出现、
`required_guidance_complete=true` 且无外部写入；不要在消息中塞入待召回答案。
写入后按自身内容验证能搜到，不能代替这项验收。

用途包括 `help`、`progress`、`urgent` 和默认的 `unspecified`，不根据消息
文本猜测用途。未配置必读记录时，紧急通知会召回指导但不会等待复审；普通指导为空或 provider 不可用
时保留既有发送路径，不会生成用户卡点。原有权限或发送方校验仍会正常阻断。
本适配器不把 hard-policy 记忆解释成软性指导。

通用实现位于 `reward_memory.outbound`，Lark 适配器只收到不透明的意图摘要。
原始文本、群 ID 和发送 profile 不进入召回查询；指导只出现在调用方私有结果，
不会被发送给收件方，也不会写入公开 registry。

## 关闭与覆盖范围

将 `automation.automatic_recall` 设为 `false` 可关闭该实验的召回；仅移除
这个 surface 可单独关闭外发召回。未配置的 Agent 与现有直接 provider 调用
保持原行为。启用不会授予 provider 凭证或外部写权限。

测试覆盖真实召回核心、readback、Agent scope、意图变化、provider 不可用、
紧急通知及两个真实发送入口的合成传输。实时验证只能做只读 provider 检查；
测试不得以 smoke 的名义向群里发送消息。
