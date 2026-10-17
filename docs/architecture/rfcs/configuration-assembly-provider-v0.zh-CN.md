# RFC：配置装配 Provider（v0）

- **RFC 状态：** 草案
- **交付成熟度：** 部分实现
- **作者 / Owner：** LoopX maintainers
- **创建日期：** 2026-09-14
- **最后规范修订：** 2026-09-14
- **实现基线：** issue #3800
- **相关契约：** [Host Integration Surface v0](../../reference/protocols/host-integration-surface-v0.md)、[TypeScript control-plane migration v0](typescript-control-plane-migration-v0.zh-CN.md)
- **语言镜像：** [English](configuration-assembly-provider-v0.md)

## 1. 决策摘要

LoopX 可经版本化 JSON 进程边界调用独立安装的配置装配 provider。v0 默认关闭且只读，仅有 `probe`、`plan`。goal、Todo、gate、quota、recovery 与 accepted writeback 仍由 LoopX 掌权。provider 的 plan、status 或退出码 0 均不得验证任务完成，亦不得授予启动、确认或写权限。

## 2. 放置

此 provider 是可选扩展边界，不是新的 outcome capability，也不是 host runtime。host adapter 仍遵守 Host Integration Surface v0 的薄适配原则。进程契约遵守 TypeScript 迁移规则：粗粒度版本化 JSON 调用，不作进程内 import，不复制数据库。

## 3. 协议

请求含 `schema_version`、`operation`、`operation_id` 与 public-safe `request`。响应必须回显 schema、operation、operation id。`plan` 另含 `plan_id`、规范 JSON 的 SHA-256 `plan_digest` 及 JSON plan。status 仅可为 `ready`、`unknown`、`incomplete`；LoopX 原样保留 `unknown`、`incomplete`，不得提升。

仅白名单响应字段进入 LoopX readback。provider stderr 与未声明字段一律丢弃。输出上限 64 KiB，执行有超时。可执行文件缺失、JSON 畸形或截断、输出超限、版本漂移、身份或 digest 不符、超时、非零退出均按 public-safe 只读失败关闭。

## 4. 关闭态等价

provider 缺失、禁用、不兼容或失败时，仅返回 unavailable/unknown readback，不改变既有 lifecycle。此协议任何结果均不得设置 completion、gate、validation、launch 或 writeback 状态。

## 5. CLI fallback

稳定 fallback 是：向配置的可执行文件 stdin 发送单个 JSON 请求，从 stdout 接收单个 JSON 响应。host 可薄封装同一调用，但不获得额外权限。

## 6. 非目标

v0 不批准 `apply`、`observe`、`recover`、客户端启动、Skills/workflow-kit 集成、provider 管理确认，亦不批准 provider 持有 LoopX 状态。

## 7. 验证

聚焦测试覆盖关闭态等价、公开字段过滤、incomplete 保留、可执行文件缺失、畸形 JSON、schema/operation/身份/digest fence、超时，以及不得产生完成或验证权限。

## 8. 最小交付

`loopx.configuration_assembly_provider.invoke_configuration_provider` 提供 provider-neutral 只读边界。合成可执行 fixture 通过同一 stdin/stdout 契约验证，不把协议绑定到某个 provider。
