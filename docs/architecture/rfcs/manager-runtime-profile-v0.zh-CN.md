# Manager runtime profile v0 / 管家运行模式 v0

- **RFC 状态：** 已接受
  强能力管家 RFC 下的 M1 实现候选）
- **替代 / 关闭：** 无

> 语言说明：本文与
> [英文版](./manager-runtime-profile-v0.md)
> 互为语义镜像；两者存在语义差异即为缺陷。

### 问题

LoopX 管家最初只有受限的规划会话：它可以读取 LoopX 提供的结构化 Goal
上下文，却不能使用安装宿主已有的文件、Shell、Git、Web 或已配置连接器。这适合
默认安装，但会把已经由 Owner 授权的常规工程工作再次退化为转交、等待或人工搬运。

单纯删除提示词限制不够。宿主 sandbox、提示词、工作区说明、持久配置和会话回读
必须表达同一个有效模式，否则 UI 显示“已开放”时，旧会话仍可能运行在只读线程中。

### 契约

`manager_runtime_profile_v0` 是机器级、显式且持久的授权选择：

- `restricted` 是默认值。管家保留作用域化的 LoopX 读取，Codex sandbox 为
  `read-only`。
- `trusted_owner` 允许 Codex 管家使用宿主正常提供的文件、Shell、Git、Web 和已配置
  连接器，Codex sandbox 为 `danger-full-access`。
- `trusted_owner` 不是通用提权。用户请求与既有 standing grant 仍限定工作范围；
  merge、release、deploy、delete、payment 等受保护操作继续走各自的 typed contract；
  外部 provider 权限、受众边界和 LoopX durable state owner 不被改写。
- 当前只有 Codex endpoint 能执行 `trusted_owner`。选择其他 endpoint 时必须返回可恢复的
  typed error，不能把受限执行伪装成已开放。
- `trusted_owner` 当前只对私有 Owner 管家会话生效。外部 audience（包括 Lark 群）是独立
  信任边界；在既有的 audience/resource grant 能被核验前，同一机器配置在那里仍解析为
  `restricted`，不能仅凭“同一管家”继承宿主资源权限。

机器配置沿用现有 capability workbench 的 `preview -> apply -> readback` 流程，不建立第二份
配置源。配置缺失时安全回退 `restricted`；配置损坏时也回退，并在能力投影中显示
`configuration_invalid` 与修复入口。

### 会话一致性

每个 manager Session 保存实际启动时的 profile、sandbox、standing grant、tool classes、
配置 revision 和状态。有效 manager namespace 发生变化时，LoopX 关闭旧上游线程，并用
可见历史启动新线程，避免旧 sandbox 或旧提示词继续生效。其他机器能力的修改不会旋转
健康的管家会话。旧版、默认受限的健康会话只补齐 readback，不做无意义重启。

Dashboard 同时显示机器配置和当前会话 readback。CLI/managed Turn、Dashboard 与 Lark
继续调用同一个 manager runtime controller；Lark 是同源会话的入口和投影，不拥有独立
profile 或权限状态，但当前外部 audience 会明确降级为 `restricted`。后续若开放 Lark
宿主工具，必须复用已有的 audience/resource authority，不在这里新增管家 ACL。

本切片只实现 [capable-manager-semantic-handoff-v0](capable-manager-semantic-handoff-v0.zh-CN.md)
的 M1 私有 Owner 旅程，目标验收为 A1–A3/A12。它不实现 M2 collaboration request、M3
outbox，也不把管家 session 字段当成工作、请求或送达权威。

### 实现与后继（2026-09-16）

`43d362532` 已有 machine profile、controller 集成与聚焦测试；本次复跑通过不等于本机部署或完整 M1 资格。`restricted` 默认、仅 Codex 支持私人 `trusted_owner` 及外部受众降级保持。DSH 通道选择不继承该强能力 profile。按[统一路线](loopx-overall-roadmap-v0.zh-CN.md) R2 补真实工具/会话/连续执行与设置读回；不得新建第二份机器配置。

### 验收

1. 默认安装启动 `restricted`，没有隐式授权。
2. 机器配置 preview/apply/readback 可把 profile 持久设置为 `trusted_owner`。
3. 新 Codex manager thread 的 app-server 请求携带 `danger-full-access`，Turn 提示词和托管
   `AGENTS.md` 不再包含只读限制。
4. profile 改变会旋转上游 thread，但保留 LoopX Session 和可见历史。
5. 不相关机器配置变化不会旋转 thread。
6. 非 Codex endpoint 对 `trusted_owner` fail closed，并给出切换 endpoint 或恢复
   `restricted` 的动作提示。
7. 桌面和移动 Dashboard 显示有效 profile；配置损坏时显示回退状态。
8. 外部 audience 在没有既有 scoped grant 时继续 `read-only`，并显示真实降级状态。
