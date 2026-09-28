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

消息包含：

- goal label 和短 objective；
- 具体 gate question；
- 最多三条 user-gate 或 user-action todo；
- 期望回复格式；
- Kanban 链接或 channel control 链接；
- 等待期间的 next safe action（如果存在）。

消息不包含本地路径、raw active state、私有日志、凭据、message id 或 raw provider
payload。

自动投递默认关闭。完成 Goal Channel setup 后，先预览，再显式启用：

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
- 自动投递默认关闭，并且可按单次 refresh 临时抑制；
- 自动投递读取 canonical quota，非 gate 状态不发送；
- doctor 能用类型化 blocker 报告缺 bot auth、缺 channel、缺 Kanban 或 stale
  extension activation；
- 本地私有 binding 文件保持 ignored 且 untracked；
- 公开 packet 不包含 chat id、member id、message id、profile name、本地路径、
  raw provider payload 或凭据。
