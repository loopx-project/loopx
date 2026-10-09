# Local Authority Provider 选择

LoopX 现在为所有 provider-first coordination command 提供一个 typed 的
local-provider 边界。Goal 没有 selector 时，边界解析为 `file` profile
（`source_authority=file_v0`）。这使 File/SQLite provider 语义成为默认的
local contract，同时不会静默晋升已有的 Markdown Goal，也不会改变其 writer
fence。

## 选择 contract

`openLocalAuthorityStoreHandle(runtime_root, goal_id)` 返回一个 handle：

| 字段 | 含义 |
| --- | --- |
| `store` | 与 provider 无关的 `AuthorityStore` 实现 |
| `provider` | `file`、`sqlite` 或 `postgresql` |
| `sourceAuthority` | provider 证据标签（`*_v0`） |

没有 selector 时使用显式的默认 File profile。SQLite selector 继续使用已有
的 `loopx_local_authority_provider_v0` marker 及其 database incarnation。
PostgreSQL selector 使用同一 marker schema，并额外绑定 `tenant_id` 与
`postgresql:<32 位小写十六进制>` store identity。

PostgreSQL marker 不包含 URL、凭据或 database client。打开它必须提供由
service 持有的 `openPostgresqlStore` factory。factory 只接收经过校验的公开
binding facts，并且必须返回带 PostgreSQL 标签、且 identity 与 selector 一致
的 `AuthorityStore`。这是中期可切换 PostgreSQL profile 的 runtime seam；它
不包含 authenticated service，也不会向 Agent 授予数据库访问权。

## 失败与兼容规则

- 已选择的 provider 在 selector、数据库、factory、identity 或 metadata 不可用
  时，绝不回退到 File。
- `source_authority` 即使在打开失败时也标识被选择的 provider；选择未解析或
  格式错误时返回 `null`。
- 选择/打开失败时 `decision_read_from_provider` 为 false，
  `legacy_fallback_used` 始终为 false。
- 旧的 `openLocalAuthorityStore` 函数仍只返回 store，已有 caller 保持源码兼容。
  runtime entrypoint 统一使用一个 opening seam，不再重复构造 provider。
- provider identity 只是可观测 metadata，不负责决定 Todo eligibility、claim、
  lease、receipt 或 promotion。

默认 profile 是路由决策，不是迁移。已有 Markdown state、writer fence、
qualification gate 以及 File/SQLite 显式 promotion hold 均保持不变。在 shared-
authority RFC 的 D2 证据和 owner approval 完成前，SQLite 仍是 opt-in 的
qualified candidate；PostgreSQL 仍是独立的 service-provider qualification 路径。

## 验证

provider selection matrix 使用 production-scale synthetic coordination fixture
验证。测试覆盖默认 File handle、SQLite 持久化、selected provider 失败时不回退、
PostgreSQL factory identity fencing，以及 factory 返回其他 provider 时的拒绝。
File、SQLite 和 PostgreSQL 继续共享 provider-neutral transaction conformance
contract；PostgreSQL 的真实服务器 qualification 仍是独立 gate。

保存计划、执行和断点恢复的操作见[审核后的晋升与恢复](reviewed-coordination-promotion.zh-CN.md)。

## 正常写入退役与大版本升级提案

完整双语支持矩阵与 PR 顺序见
[provider reference 的兼容截止提案](local-authority-provider-selection.md#proposed-compatibility-cutoff-and-release-sequence)。
这是一份提案，未改变当前安装行为或宣布发布。

当前未晋升 Markdown Goal、v0 target-only 设置和显式关闭 canonical creation
仍受支持。建议在 **2.0** 统一要求正常写入前具备 canonical authority；已有
canonical File 不必改为 SQLite，明确 provider 和两种 ownership policy 保持。
已有 canonical store 若仍用 `legacy` policy，先审核保留 claim 的 policy 迁移，
不重复晋升或强制切换 provider。
只读检查、审核迁移、受支持旧备份/格式/原回执恢复与永久 Markdown 展示保留。
旧配置升级须明确预览确认，不能删除偏好或暗中把关闭解释为开启。

当前晋升依赖真实源写入的 capture 资格。冷启动、未资格化的旧 Goal 若先禁写，
就无法产生这份资格。选定主路径是独立验收的**旧源审核导入**，无需先在兼容
1.x writer 上新增 capture；空库创建、isolated archive restore、降低计数或伪造
历史不能替代该旅程。

CLI 与 App 的既有 Goal 存储入口应复用同一 TS coordination owner：完整盘点和
备份活跃/归档 Todo、metadata、身份、ownership、capture 与原回执；不支持或歧义
内容明确拒绝，不能静默省略。停止 writer/Host、结算 active lease 并逐项对账
outbox 后，预览来源/目标绑定的不可变计划、明确确认、fence 旧 writer、导入并
完整读回。旧 lease/回执不转成新授权，导入新回执与历史原回执分别保存。中断、
失响应、重启须恢复同一操作，不重复副作用或抹掉后续 canonical 新写入。
File/SQLite 都须在旧正常 writer 物理缺席时验证失败、来源变化与恢复。

这是未晋升 Goal 的导入，与既有 canonical provider 切换和新身份 archive restore
分别验收；实现尚未完成。历史支持矩阵验收前保留兼容安装包用于恢复；可在兼容
1.x 先发布新增 importer 和弃用提示，但不把旧 writer 使用设为导入前置。

交付按完整包推进：CLI/App 直接导入与支持边界 → 同调用族旧 writer/私有 dispatch
删除 → 逐项对账 outbox 后删除无调用 producer/重复决策。2.0 beta、rc、final
按声明支持矩阵验收；不等待所有外部 Goal 已迁移。二进制更新不自动切 authority，
反向迁移保留当前 head 的新写入。D2 失败/缺失如实保留，不作为所有独立内部
退役的统一前置，也不能凭删除完成就认证发布默认。
