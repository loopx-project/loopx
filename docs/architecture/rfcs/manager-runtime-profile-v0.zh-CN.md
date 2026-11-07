# Manager runtime profile v0 / 管家运行模式 v0

- **RFC 状态：** 已接受
  强能力管家 RFC 下的 M1 实现候选）
- **替代 / 关闭：** 无

> 语言说明：本文与
> [英文版](./manager-runtime-profile-v0.md)
> 互为语义镜像；两者存在语义差异即为缺陷。

### 问题

LoopX 管家最初只有受限的规划会话：它可以读取 LoopX 提供的结构化 Goal
上下文，却不能使用安装宿主已有的文件、Shell、Git、Web 或已配置连接器。此前的
受限默认会把已经由 Owner 授权的常规工程工作再次退化为转交、等待或人工搬运。

单纯删除提示词限制不够。宿主 sandbox、提示词、工作区说明、持久配置和会话回读
必须表达同一个有效模式，否则 UI 显示“已开放”时，旧会话仍可能运行在只读线程中。

### 契约

`manager_runtime_profile_v0` 是机器级宿主工具策略。本人私有管家默认使用本机工具，
显式机器选择优先。Codex 管家默认模型为 `gpt-6.1-sol`、推理为 `xhigh`；机器配置与服务
覆盖仍保留原优先级。这不改变普通 Goal 对话模型或其他 Agent 的工具权限：

- 显式选择 `restricted` 时，管家保留作用域化的 LoopX 读取，Codex sandbox 为
  `read-only`。
- 默认 `trusted_owner` 允许 Codex 管家使用宿主正常提供的文件、Shell、Git、Web 和已配置
  连接器，Codex sandbox 为 `danger-full-access`。
  已授权的普通请求现在在当前对话中按相关项目的指令与 skills 执行；文件改动以读回
  核验，持久 LoopX 状态改动仍以既有类型化回执核验。
  仅有职责相近的已注册 Agent 不再强制转交 Inbox。用户明确指定的负责人、既有 Goal
  工作，以及需要其他 Agent 上下文或执行授权的任务仍走交办。缺少 worker binding
  既不授予启动 worker 的权限，也不撤销管家本身的宿主授权；本地完成不创建替代
  Goal 或 Todo。
- `trusted_owner` 不是通用提权。用户请求与既有 standing grant 仍限定工作范围；
  merge、release、deploy、delete、payment 等受保护操作继续走各自的 typed contract；
  外部 provider 权限、受众边界和 LoopX durable state owner 不被改写。
- Codex 与本地 Antigravity Language Server 支持 `trusted_owner`。Antigravity 沿用已登录
  宿主的现有工具权限和原生审批；LoopX 不会自动接受这些审批。等待交互时返回可恢复的
  gate。选择其他 endpoint 时必须返回可恢复的 typed error，不能把受限执行伪装成已开放。
- Antigravity 的 `restricted` Chat 仅处理文本：每次调用显式禁用原生工具调用并使用只读
  planner 模式，不继承上一回合的工具配置。这是 provider 工具限制，不是 Codex 或操作
  系统 sandbox。强能力模式仍限于既有核验过的本人受众。受治理 Task 执行、工作区文件
  系统授权和原生受管理管家身份准备仍由既有 Codex owner 承接；选择 Antigravity 不扩展
  这些契约。Argon 档位必须由本地账号提供，并与请求的思考深度一致。原始 cascade 丢失
  时续接失败，不静默创建替代会话。停止等待原生 cascade 回到 idle；超时或部分输出
  不代表成功完成。
  执行时，原生 `IDLE` 本身不代表完成：输入、工具与 planner 步骤之间也可能出现此状态。
  LoopX 继续观察原调用，不重发输入；最终答案须带正常停止标记，且本次覆盖的全部步骤均
  已完成才能收尾，与原生结果恢复的完成要求一致。短暂 idle 不延长既有无进展或硬截止
  时间；权限等待、原生错误、未核验的停止原因及另一个输入仍沿既有拒绝路径处理。
  只读轮询的传输中断不会重发输入，也不会重置 idle 或 hard deadline。运行中的原生
  cascade 继续拥有重试与错误步骤；接入层仅在原生终态错误后失败，并保留结构化原始
  原因。宿主心跳与有界 Stop-hook 恢复遵循独立的
  [Antigravity 宿主流程](../../../skills/loopx-project/references/heartbeat-hosts.md#antigravity-app)。
  这些源码边界不证明上游网络已修复，也不证明持续长程可靠性。
- Antigravity 管家输入先呈现完整用户原话，再附上冻结的 Core 证据；用户原话按 JSON
  字符串编码，保留空白和被引用的传输标记。原始结果恢复同时识别新格式与历史上的
  证据在前格式，沿用同一来源、输入时间、prompt hash 和原生终止检查。
- 强能力 Antigravity 管家可通过既有私有 capture/JSON Pointer CLI 渐进读取支撑用的
  交付历史。当前 Goal、Todo、attention、权限、来源质量及未知字段仍内联保留；只将已知
  `recent_delivery_history.deliveries` 正文移到按需读取，其窗口、覆盖及来源元数据仍可见，
  同时明确 `status=not_read` 并给出可执行读取引用。原先供应的完整 Core 快照仍可恢复。
  例如 `/goals/0/recent_delivery_history` 是首个 Goal 的已捕获历史地址：知道地址时直接
  执行供应的选择命令，不知道时再查目录。这是冻结快照读取，不是 Codex 专属的实时
  `loopx_manager_read` 工具或差分基线。依赖该历史前，应读完所需片段的全部页；上下文
  丢失后从同一 capture 恢复，不重新发送请求或重复副作用。
- 管家依据已授权的读取能力自动选择历史交付方式。既有显式 `context_delivery.antigravity_mode=off` 偏好
  或宿主环境变量 `LOOPX_CONTEXT_DELIVERY=off` 使后续原生管家输入保持完整内联。
  受限/文本宿主、配置损坏或私有 capture IO 不可用时
  也保留原输入，不改变宿主权限或审批。引用只保留原已授权快照，不是新鲜观察或动作
  授权；capture 私有保存于 Chat 存储，不写入所选项目的检出目录。RPC 与真实 CLI fixture
  验证输入、恢复及拒绝行为；真实模型读取、已安装宿主限制、完整成本和长程收益仍需
  独立资格。Codex 差分激活与 native-hook 连续性不变。
- `trusted_owner` 适用于本地私有 Owner 管家和由既有 Core conversation owner 重新核验的
  原生本人管家私聊。Core 核验 App、Owner 身份、私聊来源、当前资源授权和冻结的 Session
  身份；受众证明本身不授予工具，仍按实际机器策略执行，包括显式受限选择。其他外部
  受众（包括 Lark 群）和 `workspace_only` 资源继续受限；个人助手保留独立的工作区授权。

机器配置沿用现有 capability workbench 的 `preview -> apply -> readback` 流程，不建立第二份
配置源。配置缺失时本人私有管家使用 `trusted_owner`；配置损坏时回退 `restricted`，
并在能力投影中显示
`configuration_invalid` 与修复入口。

### 会话一致性

每个 manager Session 保存实际启动时的 profile、sandbox、standing grant、tool classes、
配置 revision 和状态。有效 manager namespace 发生变化时，LoopX 关闭旧上游线程，并用
可见历史启动新线程，避免旧 sandbox 或旧提示词继续生效。其他机器能力的修改不会旋转
健康的管家会话。旧版、与显式受限策略相同的健康会话只补齐 readback，不做无意义重启。

既有迁移需要新建 Antigravity 线程时，首次输入以 JSON 数据携带所提供的完整可见
user/assistant 历史，保留早期约束、消息顺序与有意义的空白，不能用最后 16 条消息
替代。后续 Turn 和成功续接原生线程时不重放这份历史。原 Chat store、Session、
来源权限和原生工具审批仍各有归属；这不跨不同 Session 转移历史，仅加载历史也不派工。
完整迁移输入可能增大；源码/RPC 验证不证明真实模型采用、token 节省或净成本改善。

Dashboard 同时显示机器配置和当前会话 readback。CLI/managed Turn、Dashboard 与 Lark
继续调用同一个 manager runtime controller；Lark 是同源会话的入口和投影，不拥有独立
profile 或权限状态。原生本人私聊在启动或复用宿主前重新核验 Core 的受众与资源授权；
不依据 channel 名字、持久化证明或新管家 ACL 推断权限。绑定撤销或 App/Owner 改变时
停止准入。

本切片只实现 [capable-manager-semantic-handoff-v0](capable-manager-semantic-handoff-v0.zh-CN.md)
的 M1 私有 Owner 旅程，目标验收为 A1–A3/A12。它不实现 M2 collaboration request、M3
outbox，也不把管家 session 字段当成工作、请求或送达权威。

### 实现与后继

`43d362532` 引入机器模式。原生本人管家私聊在打开、续接和授权范围刷新时，将 Core
重新核验的 Owner 受众证明传给既有模式。本人私有管家的安装默认为 `trusted_owner`。
选择 DSH 不会使该 endpoint 获得强能力模式支持，仍保留既有明确、可恢复的拒绝。
源码与 RPC 测试不证明已部署的真实模型工作或桌面通知送达；按
[统一路线](loopx-overall-roadmap-v0.zh-CN.md) R2 完成这些剩余验收。

### 验收

1. 默认本人私有管家启动 `trusted_owner`、`gpt-6.1-sol` 与 `xhigh`。显式受限选择和
   损坏配置回退仍为只读，其他受众不继承默认工具权限。
2. 机器配置 preview/apply/readback 可把 profile 持久设置为 `trusted_owner`。
3. 新 Codex manager thread 的 app-server 请求携带 `danger-full-access`，Turn 提示词和托管
   `AGENTS.md` 不再包含只读限制。
4. profile 改变会旋转上游 thread，但保留 LoopX Session 和可见历史。
5. 不相关机器配置变化不会旋转 thread。
6. 非 Codex endpoint 对 `trusted_owner` fail closed，并给出切换 endpoint 或恢复
   `restricted` 的动作提示。
7. 桌面和移动 Dashboard 显示有效 profile；配置损坏时显示回退状态。
8. 外部 audience 在没有既有 scoped grant 时继续 `read-only`，并显示真实降级状态。
9. 已核验的原生本人管家私聊沿用实际机器策略，模式改变或服务重启保留 Session 与历史；
   复用健康宿主前重新核验绑定和 Provider 身份。其他 App、群聊和 `workspace_only` 资源
   不继承该授权。
