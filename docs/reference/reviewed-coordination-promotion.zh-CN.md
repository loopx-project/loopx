# 审核后的协调状态晋升与恢复

晋升会把 Goal 的 Todo／lease 权威来源从旧路径切换到选定的 canonical provider。
预览、封住旧写者、提交新存储、收到成功响应是不同的步骤。预览成功并不代表已经切换。

现在可以保存完整预览，执行这份计划，再用同一份计划恢复原事务。计划校验、准入、
fence 与 receipt 证明由 TypeScript 协调边界负责；Python 只读文件、传输请求。

## 操作

### 盘点冷旧源

未晋升的 Markdown Goal 可以先盘点，不需要启用 shadow 或让旧 writer 制造捕获历史：

```bash
loopx --format json coordination-shadow inspect-source \
  --goal-id example-goal > old-source-inventory.json
```

读取 `source_inventory` 中的 active/archive Todo 数量、完整支持格式的记录、
`retained_leases` 和 `leases_requiring_settlement`。全部归档均进入这次盘点，
不只包含当前图需要的归档依赖；长正文和已支持 metadata 不按注意力摘要截断。
原源字节、registry 和全部 lease 文件绑定到同一次来源见证，TS 在既有锁下复核。
历史 lease 单独返回，不变成当前执行 grant。仍标记 `active` 的租约均需结算，
即使已过期或所属 Todo 已删除；过期不能证明 Host 已停止。
缺少归档角色、重复身份、非法历史 lease 和源变化会拒绝；已有 canonical selector、
文档或 writer fence 时拒绝把 Markdown 投影当作冷旧导入源，provider 不可用也不回退。

`import_ready`、`writer_stop_verified`、`outbox_reconciliation_verified` 始终为 false。
此命令没有 `--execute`，不启用配置、创建 shadow/provider/fence、授予 lease 或制造捕获回执。
它只交付操作员 CLI 盘点前置；Goal storage owner 仍须完成共享 frontend 盘点、明确停
writer/Host、原 outbox 对账、备份、审核目标、确认和同操作导入恢复。下方 shadow
晋升仍遵守原资格，不因盘点通过而放宽。

JSON 含完整正文、身份与本机路径，应留在操作员私有存储。它是观察结果，不是完整
历史备份；原始来源和受支持回执仍按[配置备份](configuration-backup.md)保留。
没有能力设置被启用，停止盘点无需回退；不再需要时删除本人的输出文件即可。

### 合格 shadow 的晋升

先显式启用并 bootstrap runtime shadow，让它捕获真实变更并通过资格校验。
新 CLI 预览默认 `preserve`，只切换存储权威、保留当前所有权策略；显式
`--handoff-mode-migration hard_lease` 才审核策略升级及现有 claim/lease。
旧 v0 hard-only 保存计划仍按原合同恢复，执行时不能覆盖计划选择；两者都不降低
捕获、来源与事务资格条件。已 canonical 的 Goal 无需重新 promote，可以使用独立
备份的 [handoff-mode 迁移](handoff-mode.md)。

```bash
loopx --format json coordination-shadow promote \
  --goal-id example-goal --minimum-operations 3 \
  --require-event-kind todo_update > reviewed-promotion.json

loopx --format json coordination-shadow promote \
  --goal-id example-goal --reviewed-plan reviewed-promotion.json

loopx --format json coordination-shadow promote \
  --goal-id example-goal --reviewed-plan reviewed-promotion.json --execute
```

执行前审核 `ok`、`promotion.status`、目标 provider、源 revision、projection digest
和资格策略。文件可以是完整 CLI 成功预览，也可以是其中的
`promotion.plan.reviewed_plan`。它包含 runtime 路径与 Goal 身份，应保存在本地，
不要贴到公开 PR。

保存的计划决定 operation id 和资格策略，不能再叠加 `--minimum-operations` 或
`--require-event-kind`。不传计划文件的新 CLI 预览默认保留所有权策略。

执行会在现有锁内重新捕获、校验源状态。计划变了，就在 fencing 前返回
`local_authority_reviewed_plan_changed`。此时重新预览并审核；不要修改旧 digest
来强行通过。digest 说明“执行的是哪份意图”，持久 fence 和 provider 状态说明
“这份意图现在能否执行”。

## 注册变化与重试

Python 来源适配器将当前 Goal、路径和已注册 Agent 事实绑定到一次 registry 字节摘要；
TS 在现有跨 runtime 锁内再次核对，并持锁到准入/提交结束。来源已失效返回
`source_registry_changed_retry`，保存计划的 Agent 事实改变返回
`promotion_registration_changed_retry`。重新读取来源与审核预览，不要修改摘要。
注册修改正在持锁时返回 `source_registry_busy_retry`；释放来源锁后可重试，避免与
先锁 registry 再锁状态的配置命令互相等待。普通 Todo 写入不新增锁。
项目和全局 registry 的修改共用既有 marker + kernel 组合锁，包括全局同步、
Goal 启停及删除；这一 registry 互操作协议也适用于未启用 shadow 的场景，只读
预览仍不取写锁。已进入受保护操作后发生的其他锁超时保留原始原因，不冒充 registry 竞争。

这是本机来源一致性，不是授权授予，也不是跨主机数据库事务。完整 registry 的无关
修改也可能要求重试，优先保证来源证据明确。旧持久回执和 fence 的恢复不要求新增
registry witness；新的 bootstrap、inspect、qualify 和 promote 必须重新捕获它。
pre-promotion rollback 仍可隔离损坏来源对应的 shadow，不要求当前注册恢复正常。

晋升失败时 `legacy_writer_fenced=true` 表示实际存在持久 fence，不表示本次创建了它；
无法确定时为 `null`，不能当成旧路径可写。使用原计划恢复，不能删除 fence 逃过拒绝。

## 断点恢复

```bash
loopx --format json coordination-shadow recover-promotion \
  --goal-id example-goal --reviewed-plan reviewed-promotion.json

loopx --format json coordination-shadow recover-promotion \
  --goal-id example-goal --reviewed-plan reviewed-promotion.json --execute
```

恢复仍要找到注册的 Goal 与 runtime，但不读取旧 Markdown，也不依赖临时 shadow
开关。它必须看到完全相同的持久 writer fence，不能创建缺失的 fence、换 provider，
也不能回退到旧来源。

| 状态 | 仅预览 | 加 `--execute` |
| --- | --- | --- |
| 没有匹配的 fence | 拒绝 | 拒绝 |
| 有 fence、尚未提交、保留的 shadow 仍完全合格 | `recovery_ready` | 提交并读回 |
| 原晋升已提交，甚至 canonical 已继续变更 | `replayed` | `replayed`，不重写 |
| 已被其他事务初始化，或 receipt 与事务链矛盾 | 拒绝 | 拒绝 |
| provider 不可用 | 报告 provider 错误 | 报告 provider 错误 |

尚未提交的恢复仍核对原 shadow revision、projection、capture binding、完整事务链、
outbox 是否结清、操作次数和事件覆盖；校验与提交共用 canonical writer 的维护锁。
它不会把已经失去权威地位的旧文件当作必须重新观察的来源。

提交调用抛错，也可能是“数据已落盘，但响应丢了”。两条晋升路径现在共用一次提交、
持久读回的实现，不盲目重做业务事务。receipt 与第一笔事务的 operation id、cursor、
provider revision、receipt 内容和初始 projection 必须全部一致。

返回的 revision 与 cursor 指向原晋升事务，不一定是当前最新 head。重放返回
`executed=false`，表示本次没有业务写入。失败时还要看 `legacy_writer_fenced` 和
恢复提示；失败不等于旧写者一定还能继续工作。

## 大 Goal 的捕获证明

序号恢复以前会返回完整 head 和历史事务的完整 projection，大 Goal 可能因此超过
现有 2 MiB RPC 响应上限，甚至不能产生晋升所需的 shadow 变更。
现在 `outbox_read` 的 proof read model 在 TS 内完整校验事务链，只传回进度、receipt、
projection digest 和 partition marker；分配序号不返回事务行，drain 使用紧凑行。
原有完整诊断读取保持默认合同，不提高传输上限、不减少持久数据，也不省略链校验。
若 outbox 尚未结清，晋升仍会拒绝。先执行并检查既有的有界 drain：
`authority-shadow drain --goal-id example-goal --budget-seconds 60`。

## 交付边界

这是运维 CLI 的显式管理操作，没有新增 Dashboard／飞书自动迁移按钮、设置项或
capability grant。它们在获授权的切换后继续使用既有 canonical 路由和展示合同。
PostgreSQL 仍需要服务持有的 factory 与租户权限，不能仅靠本地 selector 接通数据库。

保存计划与 claim-preserving 转换组合验证：File/SQLite 的历史 v0 计划及新 CLI
preserve/hard 路径使用相同资格与恢复 owner。
默认切换、SQLite 长时资格、晋升后导出／回退、剩余 Python 删除仍遵守 RFC 独立门槛。

恢复用于向前补齐或确认原切换，不是 rollback。不要删除活跃 fence、重置 canonical
存储或替换源文件来绕过拒绝。执行前放弃一份预览，只需停止使用该文件。

验证包括真实 File／SQLite CLI、完整合成状态的多 provider conformance、receipt
索引与事务链分别被破坏的负例，以及提交前中断、提交后丢响应的故障注入。故障注入
不等于任意进程崩溃或长时 soak 已验证。真实项目演练只在只读快照的可丢弃副本上执行。
