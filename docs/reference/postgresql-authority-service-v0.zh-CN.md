# PostgreSQL authority service 准入 v0

本文定义可切换 PostgreSQL authority provider 的服务侧准入边界。它是
Stage 2B 的 opt-in seam：不会把 PostgreSQL 变成默认 provider，不会改变
file/SQLite 选择逻辑，不会增加 runtime caller，也不表示已经满足 promotion
条件。

## 边界

`PostgreSqlAuthorityService` 接收不透明的传输凭证、tenant、goal 和期望的
数据库 incarnation identity，然后先调用注入的认证函数和 tenant 授权函数，
再打开 provider。凭证不会持久化、不会传给 PostgreSQL，也不会进入
provider-neutral 的 `AuthorityStore` 合同。principal 被拒绝或无法验证时，
服务会在建立数据库连接前 fail closed。

服务返回以下类型化结果之一：

| 结果 | 含义 |
| --- | --- |
| `opened` | principal 已认证、被授权使用该 tenant，且数据库 identity 与请求绑定一致。 |
| `principal_unauthenticated` | 凭证被拒绝，或认证函数返回了非法 principal。 |
| `principal_verification_unavailable` | 认证无法完成。 |
| `tenant_unauthorized` | 已认证 principal 无权使用该 tenant。 |
| `tenant_authorization_unavailable` | tenant policy 无法评估。 |
| `store_identity_unavailable` | 无法读取 provider metadata。 |
| `store_identity_mismatch` | 请求的 incarnation 不是数据库当前 incarnation。 |

该模块刻意保持为进程内边界。部署方负责传输、凭证校验、tenant policy、连接池
和 secret 处理；本模块不授予网络访问、actor ownership、lease ownership、跨主机
同步或 promotion 权限。

## restore-incarnation 轮换

`rotatePostgreSqlAuthorityStoreIdentity` 是认证服务部署拥有的管理操作。它锁定
singleton metadata 行，校验旧 identity，并原子写入新生成的
`postgresql:<32 位小写十六进制>` identity。它不会重写 head、commit、event、
receipt、goal 或 operation ID。

provider revision token 包含数据库 identity。因此 restore 轮换前签发的 token
会变成 stale 并产生 conflict；使用新 identity 打开的 store 仍能读取持久化的
head 和 receipt 历史。期望 identity 错误时不会写入。如果 PostgreSQL 在
`COMMIT` 后丢失响应，结果为 `ambiguous`；服务必须先重新读取 metadata 再重试，
不能猜测轮换是否已经生效。

## 历史保留与合成种子

PostgreSQL schema v1 在游标 1、65、129 等位置追加检查点，其余新提交保留增量。
历史扫描从覆盖请求游标的检查点或保留的 v0 完整投影开始，校验游标连续性和投影
摘要。事件与回执继续绑定原提交。

由管理员执行 `loopx postgres setup --execute`。从 v0 升级时传入数据库现有的
`--store-identity`。安装保留完整投影历史，将原 journal 的表级 `SELECT` 和
`INSERT` 授权延续到检查点，继承这些授权的组成员也可继续使用。同一数据库事务完成
显式 `--runtime-role` 配置与格式切换。使用自定义列级授权的部署应显式配置运行角色，
已建成的 v1 库也可用原 identity 和 `--runtime-role` 配置角色。升级前验证备份并停止
旧调用方，在隔离恢复库以受限角色读取历史并验证新检查点追加后再决定是否启用。
回退到 v0 需要使用经验证的 v0 备份。

`loopx postgres local-to-pg-seed --goal-id <synthetic-goal>` 从合成 File 或 SQLite
状态预览脱敏种子，预览期间不连接目标。可通过 `--output` 创建私有文件或复用内容
完全相同的普通文件。内容不同或路径是符号链接时，命令在连接目标前失败。使用
`--execute` 时通过 `LOOPX_POSTGRES_URL` 传入已配置的受限连接，可选
`--store-identity` 固定目标库身份。种子写入空目标 Goal，原样重试（含同一
`--output`）可读回原提交回执。种子从游标 1 开始，仅用于合成数据。完整历史的
导出、核验或恢复使用 journal 归档。

## 验证

公开 synthetic fixture 是
[`postgresql_authority_service_v0.json`](../../tests/fixtures/control_plane/postgresql_authority_service_v0.json)，
覆盖认证准入、认证拒绝、tenant 拒绝、identity 漂移和 restore 轮换，不包含凭证
或私有数据库信息。

确定性 seam 测试：

```sh
npm run typecheck:control-plane
npm run test:postgresql-authority-service
```

真实 PostgreSQL 路径需要将 `LOOPX_TEST_POSTGRES_SERVICE_URL` 指向隔离的临时
数据库，再执行同一命令。该数据库必须与活动 goal 及其他 PostgreSQL 测试 schema
分离；集成测试只修改自身的 tenant/goal 和 metadata。
