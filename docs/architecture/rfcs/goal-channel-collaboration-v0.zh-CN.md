# RFC: Goal Channel 协作模型 v0

- 状态：已接受
- 替代 / 关闭：无
- 范围：绑定到单个 LoopX goal 的 provider-backed 外部协作通道
- 决策类型：产品架构与分阶段集成契约

## 摘要

本文引入 **Goal Channel** 作为 LoopX 拥有的核心抽象：一个绑定到唯一
goal 的外部协作通道。这个 channel 可以由 Lark/飞书群、Slack channel 或
thread、GitHub issue、Linear thread，或其他 provider surface 承载。
provider 负责消息投递和 UI 原语；LoopX 负责 goal 状态、todos、human
gates、quota、evidence、receipts，以及被接受的状态迁移。

第一阶段 provider 目标是 Lark/飞书：

- 为一个 goal 创建或复用一个群聊；
- 创建或复用一个 Lark Base Kanban 投影；
- 在群里 pin 一条紧凑的控制消息和 Kanban 链接；
- 发送有界 human-gate 通知；
- 将已被 LoopX 接受的状态同步回 Kanban 投影。

channel 不是事实源。它是一个 LoopX goal 的可见协作入口和反馈 surface。

## 问题

LoopX 已经有稳定状态和一些 Lark 专属能力：

- `lark-kanban` 可以把 LoopX todos 和状态投影到 Lark Base；
- Lark 通知代码路径已经在更窄的领域里验证过 send、readback、
  idempotency 和 profile check。

这些能力还没有组合成用户期待的 Claude Tag 类工作流：

1. 在协作 surface 里 mention 一个 bot。
2. 为该目标获取或创建一个隔离的协作通道。
3. 在同一个地方看到进展和 gate。
4. 不离开协作 surface 就能收到 human gate 提问。
5. 仍然由 LoopX 保持权威的 goal、todo、gate 和 evidence 状态。

如果没有一等的 Goal Channel 抽象，Lark 群、Base 看板、消息 thread、
pinned status 和 notification receipt 很容易彼此漂移。

## 目标

- 定义一个 provider-neutral 的 LoopX 概念，表示“这个 goal 的外部协作通道”。
- 让 Lark 群聊、Kanban、pinned message 和 gate notification 都绑定到同一个
  `goal_id`。
- 保持 LoopX 是 canonical goal、todo、gate、evidence 和 quota 状态的唯一写入者。
- 让 provider 写操作显式、可预览、幂等，并经过 readback 验证。
- 允许人在 channel 里看到并回答 gate，但不因此授予 channel 宽泛写权限。
- 后续可以增加 Slack、GitHub 或 Linear 等 provider adapter，而不需要重命名核心概念。

## 非目标

- 替代 `lark-kanban`；Goal Channel 只是组合它。
- 让 Lark、Slack 或任何外部工具成为事实源。
- 在开源 CLI 中内置一个 LoopX 托管的全局 Lark app。
- 要求所有用户或租户共用一个固定 bot 身份。
- 将任意聊天文本直接视为已接受的状态迁移。
- 把原始聊天历史、私有 message id、本地路径、凭据或 raw provider
  payload 复制进公开 packet。
- 在本 RFC 中解决完整远程 runner 编排。

## 命名

核心抽象使用 **Goal Channel**。

不要把 `room` 作为主名称。群聊可以是一个实现细节，但 Goal Channel 可以同时包含
chat、pinned status、Kanban、notification receipts 和 provider-specific metadata。

建议命令面：

```bash
loopx goal-channel setup --provider lark --goal-id <goal-id>
loopx goal-channel target add --name <target> --provider lark ...
loopx goal-channel setup --goal-id <goal-id> --target <target>
loopx goal-channel attach --target <target> --goal-id <goal-a> --goal-id <goal-b>
loopx goal-channel configure --goal-id <goal-id> --auto-notify-human-gates
loopx goal-channel doctor --goal-id <goal-id>
loopx goal-channel sync --goal-id <goal-id>
loopx goal-channel notify-gate --goal-id <goal-id>
```

`goal-channel` 同时作为持久控制面对象和用户可见 CLI。

## 所有权模型

| 能力 | LoopX | Provider channel | Provider adapter |
| --- | --- | --- | --- |
| Goal lifecycle | Owner | Projection | Calls LoopX |
| Todos、claims、gates、quota | Owner | Projection and prompts | Syncs bounded packets |
| Kanban rows | Source data owner | Display owner | Upserts rows |
| Group/chat/thread | References binding | Owner | Creates, updates, reads |
| Pinned status | Builds bounded content | Displays | Sends and pins |
| Human gate question | Owner of question and cooldown | Delivery | Sends and verifies |
| Credentials and profile | Never stores secrets | Provider auth | Uses local-private profile |
| Receipts | Owner of accepted transition receipts | Message ids are private | Records compact send/readback receipt |

provider 可以保存自己的状态。LoopX 只保存运行 channel 所需的最小本地私有绑定。

## Lark Provider 绑定

Lark Goal Channel 绑定是本地私有、项目作用域内的配置：

```json
{
  "schema_version": "loopx_goal_channel_lark_binding_v0",
  "goal_id": "loopx-goal",
  "provider": "lark",
  "enabled": true,
  "channel": {
    "chat_id": "oc_<private-chat-id>",
    "chat_name": "LoopX - loopx-goal",
    "pinned_message_id": "om_<private-message-id>"
  },
  "kanban": {
    "base_token": "<private-base-token>",
    "table_id": "tbl...",
    "view_ids": {
      "Kanban": "vew...",
      "User Gates": "vew..."
    }
  },
  "identity": {
    "mode": "project_bot",
    "sender_profile": "loopx-project-bot",
    "sender_identity": "bot",
    "bot_display_name": "LoopX Bot"
  },
  "receipts": {}
}
```

该文件应位于 `.loopx/` 或其他被忽略的本地私有路径。公开 status packet 不得暴露
chat id、member id、message id、profile name、raw Lark payload、本地文件路径或凭据。
只有当调用方明确选择展示时，公开 packet 才可以展示布尔值、计数、脱敏 provider
label 和 operator-safe URL。

### 共享 provider target

多个 Goal Channel 可以引用同一个具名、本地私有的 provider target。target 持有
可复用的 Lark 群和发送身份；每个 Goal binding 仍独立持有自己的控制消息、Kanban、
receipt 和 cooldown 状态：

```bash
loopx goal-channel target add \
  --name loopx-dev \
  --provider lark \
  --chat-id <private-chat-id> \
  --bot-app-id <private-app-id> \
  --execute

loopx goal-channel attach \
  --target loopx-dev \
  --goal-id goal-a \
  --goal-id goal-b \
  --execute
```

target store 位于解析后的 LoopX runtime root 下，绝不能成为公开或提交进仓库的配置。
引用 target 的 Goal binding 只保存 `target_ref` 和 Goal 本地状态；更新 target 会改变
所有引用 Goal 解析到的群或 sender，但不会合并这些 Goal 的状态。
target 换到另一个群后，应重新执行有界 `attach` 批次，让每个 Goal 在新群中分别建立并
回读自己的控制消息。

不同机器之间不自动同步私有 chat id 或认证 profile。若本机和开发机需要使用同一个群，
应在两台机器上分别配置同名 target。未来接收群回复时，只能接受对具体 gate 消息的回复，
或携带明确 Goal id 的操作；不得把普通群文本推断给多个 Goal 中的某一个。

## BYO Provider Identity

开源 LoopX 应默认使用 **Bring Your Own provider identity**：

- 用户在自己的租户里创建或选择 Lark app / bot；
- 用户通过 `lark-cli` 或未来 provider-specific profile manager 完成认证；
- LoopX 只保存本地 profile 引用和紧凑验证状态；
- LoopX 不把一个固定跨租户 bot 作为隐式依赖。

支持的身份模式：

| 模式 | 适用场景 | 取舍 |
| --- | --- | --- |
| `local_user` | 由用户创建并持有群聊和 Base | 资源归属清晰；消息仍必须由 bot 发送 |
| `project_bot` | 使用项目专属 bot profile 发送 channel 消息 | 需要配置 app/bot，但消息身份稳定 |
| `managed_app` | 未来托管产品 | 体验最好，但需要租户安装、合规和运维 |

第一版实现使用本地 user identity 操作群聊和 Base；Goal Control message、pin
和 gate notification 始终使用已配置的 bot identity，不请求也不依赖
`im:message.send_as_user`。

直接执行 setup 时必须显式传入 `--bot-app-id cli_...`；target 模式则从本地私有
target 取得这项明确选择。LoopX 会验证该 app id
与所选 `lark-cli` profile 一致，再允许加 bot 或发消息。省略该参数只适用于
preview，不代表可以静默选择默认 profile 的 bot。

## 生命周期

### Setup

`goal-channel setup --provider lark --goal-id <goal-id>` 应该：

1. 解析并校验 goal。
2. 加载或创建本地私有 Lark channel 绑定。
3. 验证 `loopx-lark` extension activation 和所需权限。
4. 验证本地 user resource identity 和已配置的 bot sender。
5. 创建或复用一个 Lark 群聊，并验证 bot 已加入群聊。
6. 通过 `lark-kanban` 创建或复用 Lark Kanban Base。
7. 回读并保存 canonical Base URL。
8. 发送一条包含 Kanban 链接的紧凑 Goal Control message。
9. pin 这条已验证的控制消息。
10. 保存本地私有绑定和紧凑 receipt。

默认是 dry-run。外部写操作必须要求 `--execute`。

### Sync

`goal-channel sync` 组合现有投影：

- 用 `lark-kanban sync-loopx-todos` 同步 active user/agent todos 和派生领域 outcome；
- 当 channel 可见摘要发生实质变化时，更新或追加紧凑 status/control message；
- 只有在单独配置后，才启用 periodic report 或 explore projection sink。

sync 命令不得从远端 row 创建新的 canonical todo。

### Human Gate Notification

当 LoopX 已经判定某个 human gate 或 user todo 需要关注时，
`goal-channel notify-gate` 发送有界消息。触发输入来自现有 quota 和
interaction-contract surface：

- `state=operator_gate`；
- `notify_user_on_gate=true`；
- `notify_user_on_open_todo=true`；
- `gate_prompt`；
- `operator_question`；
- `open_todo_notify_reason`；
- `user_todo_summary`；
- `user_gate_notification_cooldown`。

管家说明所选决策、对 Goal 的影响与有用的下一步。最多三条完整请求保留精确引用、
范围与证据；不完整请求明确保留缺口。相关阻塞与决策可以合并表达，不扩大批准范围；
必要时保留 Kanban 链接。这替换固定的 `Action required / Decision requests` 模板，
选择、冷却与 provider 权限仍由各自 owner 持有。

消息不包含本地路径、raw active state、私有日志、凭据、message id 或 raw provider
payload。

新建 Goal Channel 默认开启 human gate 投递，已有 binding 保留原设置；blocked notice
默认关闭。可以预览或显式启用：

```bash
loopx goal-channel configure --goal-id <goal-id> --auto-notify-human-gates
loopx goal-channel configure --goal-id <goal-id> --auto-notify-human-gates --execute
```

启用后，每次成功且非 dry-run 的 `refresh-state` 都会根据 LoopX canonical state
重新计算 quota。只有 quota 选中 human gate 时才发送，并复用 `notify-gate`
已有的 bot 身份校验、语义幂等、冷却、provider idempotency key 和消息回读。

单次 refresh 可使用 `loopx refresh-state ... --suppress-external-sinks`
临时抑制投递，而无需禁用 binding。对带 Turn 绑定的恢复，工具会记住这次暂停；
再次允许外发需使用返回的 `resume_key`，提交 `--resume-external-sinks <resume_key>`。
这只确认恢复当前操作的投递，不授予新权限；无暂停记录的旧操作保留原行为。
详见 [恢复握手](../../state-interaction-model.md)。持久关闭自动投递：

```bash
loopx goal-channel configure --goal-id <goal-id> --no-auto-notify-human-gates --execute
```

受阻 Todo 通知有独立的 opt-in，默认关闭。通知包含任务、原因、影响、解除责任人、
恢复条件和下一步；安全回退继续时，主要受阻任务仍会显式呈现。通过同一个私有
Goal Channel binding 开启或关闭：

```bash
loopx goal-channel configure --goal-id <goal-id> --auto-notify-blocked-notices
loopx goal-channel configure --goal-id <goal-id> --auto-notify-blocked-notices --execute
loopx goal-channel configure --goal-id <goal-id> --no-auto-notify-blocked-notices --execute
```

实际刷新只向已授权的 Lark sink 发送。去重同时绑定阻塞身份、修订、目标群和投递世代。
明确观察到 canonical Todo 恢复为 open 时，旧阻塞回执标记为 resumed；终结及被取代
事实分别退休旧回执。缺失或分页省略的任务不能证明恢复；同一时间戳内多次恢复也会
产生不同投递世代。

每次刷新最多尝试八条待发送效果，未尝试的通知优先于重试，已核验回执不占用额度。
完整候选与延后的 pending 回执保留到后续刷新；仍有待处理通知时不能宣称全部核验。
切换目标群必须独立发送并回读，旧群历史继续保留。公开计数只针对当前目标群，已退休
回执不计入当前已送达数量。

私有 binding 保存待发送、已发送但未核验、已核验、已恢复、已解除及被取代状态。
发送失败或没有可用 sink 时保持待发送状态，安全回退不受阻。`status.json` 只公开计数
与两个 opt-in，不公开私有消息身份。群聊回复不构成 Todo 批准或解除；恢复须由
canonical Todo 状态确认。

该 opt-in 只保存在项目本地私有的 Goal Channel binding 中，不授予仓库或 LoopX
状态迁移权限。群聊回复可以补充 context，但只有经过 LoopX 校验并记录的 decision
才能改变 gate 状态。

自动生命周期投递会先解析已启用且 doctor 验证通过的 Lark extension，再读取私有
binding。启用自动投递时必须使用项目本地 canonical binding 路径；由于
`refresh-state` 没有逐次传入 binding path 的入口，自定义 `--binding-path`
会被拒绝。唯一的恢复例外是显式本地 disable 命令：即使 extension 或 binding
不完整，它也可以清除 opt-in；该路径不会进入 provider 代码，也不会执行外部写。

启用时还会写入一个 owner-only 的本地 marker，其中只包含 enabled boolean。
生命周期在 extension activation 前只允许读取这个 marker，用来区分“从未配置”
和“已配置但 extension 后续不可用”。后者会通过可重试的
`extension_unavailable` postcondition fail closed；marker 不包含 provider id、
凭据、channel metadata 或 raw payload。

### 本地管家接收与可选 Channel 投递

私有本地管家是其获准读取 Goal 的默认注意力消费者。未连接 Lark、关闭群通知或
外部投递失败，都不能隐藏 canonical 阻塞与用户请求；Goal 对话在所选 Goal 内
复用同一事实。外部受众保留精确授权，接收不授予发布或执行权限。

同一 Todo 的阻塞与用户请求合成一个对象，保留具体决策条款，分别表达“用户需要
知道”和“用户需要行动”。复用 blocked-transition builder 与共用 TS 决策/注意力
投影；provider 地址、发送/回读和私有回执留在 Lark。切群改变投递回执，不改变源
可见性；模型改写不是新修订，读取也不证明已通知用户。

管家结合目标、已有决定与独立工作，解释实质变化的影响，合并相关原因，建议下一步。
Agent 自行恢复的工作留在后台；用户决定保留对象、条款、依据与不行动后果；未知或
脱敏事实明确为缺口。不要机械转发每条变化或把阻塞变成新审批。语义汇总复用已配置
的对话 runtime，模型不拥有通知资格、授权或回执状态迁移。

**实现检查点。** 已有管家 / Goal Turn 在没有 Channel 或 run history 时仍读取
当前 canonical 注意事实。TS owner 按 Todo 合并阻塞与决策，优先呈现 owner action，
披露遗漏。整个 Turn 最多纳入十二个对象，各 Goal 保留遗漏数量，完整剩余事实通过
已有带作用域的 Todo read 获取。Python 仅适配 canonical I/O 与公开安全字段，不建立
第二套选择规则。

已有 human gate 与 blocked notice sender 共用管家生成适配器，替换两套固定消息模板。
已准入的外部通知使用配置的管家执行器 / 模型，在独立、restricted Chat Turn 中生成
（启动最多 30 秒，推理最多 90 秒）。读取限定本 Goal 与 audience，不增加宿主、
委托或发布授权。生成前后核验选中请求内容、生命周期、blocker revision 与引用的
继续工作依据；仍完整读取 canonical 来源，无关 Todo 更新或新增不使本通知失效。正文保留精确
请求引用以保护回复路由：新生成与缓存正文均须包含每个完整编号的独立 token，周围
允许标点与 Markdown，带前缀或后缀的编号不满足该投递义务。执行 / fallback 未评估
时保持未知，建议不证明 worker 正在运行。

生成正文在发送前保存到已有私有 effect receipt；重试沿用相同正文与 provider key。
已核验 gate 消息记录覆盖的阻塞版本，blocker adapter 不再重复发送同一事实。
生成失败保留待处理，不回退机械模板；已有前端设置说明模型使用并呈现未核验投递。
历史 verified receipt 继续静默；历史不确定 blocker receipt 若未保存正文，须先核对，
不能在已尝试的 provider key 下重新生成另一段正文。

不新增 inbox、scheduler 或通知 store。默认本地接收表示下一次已有 Turn 中可读，
不等于自动呈现或已读。material-delta 唤醒、预算内主动生成、已读 / 恢复确认、跨 Goal
语义批处理及持续质量仍归 presentation Stage 2 / R3。一次真实 Codex 合成测试证明
模型路径可用，不证明长期通知质量或真实 Lark 部署。独立外部 transcript 不借用
正在运行的 owner session，也不宣称完整会话连续性。

**负责人到管家的通知验收。** 负责人 Bot 把消息发到群里，即使准确提及管家，也不证明
接收事件、接手或用户呈现已经发生。实时事件缺失与历史材料仅供参考是两种观察；先核对
真实发送方与接收方，再判断注册问题或 provider 的投递保证。历史补读经过私有 Inbox 和
对话上下文时保留有界发送者身份、消息身份及时间。这些是来源事实，不是用户授权、工作
采用，也不是重放旧指令的理由。

下一个 Stage 2 / R3 切片须把获准负责人的当前 canonical 阻塞或具体用户请求交给已有
管家注意力 / intake owner，保留来源 Goal / Agent、修订、受众与回传位置。外部 Bot 提及
不能是唯一交接。复用 runtime、预算与 effect receipt；资格判断及状态迁移归 TS，provider
I/O 留在适配器。先在 App 呈现同一个当前请求，再独立验收获准 Channel。读回分别证明
来源创建、管家接收、呈现、用户决定、负责人采用和原入口回传。呈现前重新核对 resolved /
superseded 请求，避免已经收到的决定再次催问。传输缺失或来源不可用保留明确恢复缺口，
不能虚构“没有负责人”。这些仍是计划验收；保留历史身份不证明主动接收、跨主机投递或
完整通知闭环。见 [GQ09 / GQ10 通知变体](../../product/use-cases/steward/golden-queries.md#worker-notifications--负责人主动找你)。

## 命令契约

每个 effectful command 返回紧凑 packet：

```json
{
  "schema_version": "loopx_goal_channel_operation_v0",
  "ok": true,
  "goal_id": "loopx-goal",
  "provider": "lark",
  "operation": "notify_gate",
  "execute": true,
  "external_write_performed": true,
  "readback_verified": true,
  "idempotency_key": "sha256:...",
  "receipt_id": "receipt_...",
  "public_summary": "sent one gate notification to the configured Lark channel",
  "private_provider_payload_captured": false
}
```

失败应类型化：

- `extension_unavailable`；
- `provider_identity_unverified`；
- `channel_binding_missing`；
- `channel_membership_unverified`；
- `kanban_binding_missing`；
- `notification_cooldown_active`；
- `readback_mismatch`；
- `state_transition_rejected`；
- `provider_api_failed`。

## 幂等和 Cooldown

provider 写操作使用语义 action 派生 idempotency key，而不是使用本次尝试的时间：

```text
goal_id + provider + operation + todo_id/gate_id + gate_text_hash + channel_id
```

规则：

- 重试同一次发送返回 `already_sent` 或原始 receipt；
- gate 文案变化可以生成新的 notification key；
- cooldown 抑制重复提醒，但不关闭 gate；
- stale provider event 不能覆盖更新的 LoopX revision。

## 安全与隐私

- Channel membership 不是 LoopX 写权限。
- 发送前必须验证 bot membership。
- 记录成功发送 receipt 前必须完成 message readback。
- Raw provider payload 留在本地私有状态。
- 从 shared/global registry 调用时，Goal Channel 状态必须落在所选 goal
  的 canonical `source_registry` 旁边，调用者 CWD 不能作为默认状态根目录。
- 本地私有 JSON 使用同目录、owner-only 的临时文件完成写入，再原子 replace，
  避免中断时暴露或截断旧 binding。
- 本地 checkout 路径、active-state 路径、凭据、chat id、member id、message id
  和 profile name 不进入公开 artifact。
- channel 可以展示 Kanban 链接，但 Kanban 仍然只是投影。
- destructive、credentialed、production、publish、merge 或 external-write gate
  仍然是 LoopX gate，不能被聊天文本绕过。

## 最小可用切片

第一版最小可用实现应包括：

1. 增加 `loopx goal-channel`，包含 `setup`、`configure`、`doctor`、`sync` 和
   `notify-gate`。
2. 只实现 Lark provider。
3. 复用现有 `lark-kanban` setup/sync 和 `loopx-lark` extension activation checks。
4. 为一个已有 goal 创建或复用一个 Lark 群。
5. 发送并 pin 一条紧凑 Goal Control message。
6. 发送带 idempotency 和 readback 的 human-gate notification。
7. 可选地在授权的 `refresh-state` 写回后发送 LoopX 选中的 human gate。
8. 将本地私有绑定、automation opt-in 和 receipt 保存到 `.loopx/`。

这个切片先验证外部协作入口。

## 验证

第一版切片必须证明：

- setup 默认 dry-run，只有带 `--execute` 时才执行外部写；
- 一个 goal 映射到一个本地私有 Lark binding；
- 读取私有配置前会先检查 extension activation；
- Kanban board 可以被复用或创建，然后成功同步；
- Goal Control message 可以发送、pin，并通过 readback 验证；
- human-gate notification 遵守 cooldown 和 idempotency；
- 重试通知不会产生重复可见消息；
- 新建 Channel 的 human gate 投递默认开启，blocked notice 默认关闭；均可关闭或按单次 refresh 抑制；
- 自动投递读取 canonical quota，非 gate 状态不发送；
- doctor 能用类型化 blocker 报告缺 bot auth、缺 channel、缺 Kanban 或 stale
  extension activation；
- 本地私有 binding 文件保持 ignored 且 untracked；
- 公开 packet 不包含 chat id、member id、message id、profile name、本地路径、
  raw provider payload 或凭据。
