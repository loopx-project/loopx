# 委派停止替代提案：canonical lease 撤销

- 基线：`e55489c77`，2026 年 10 月 3 日测量。
- 结果：总体 roadmap S4（"restart/cancel/drain/stop 保留工作并 fence 旧执行者"）
  与 R2 的有界单操作停止；对应
  [9 月 27 日 host-supervision 计划](2026-09-27-host-supervision.zh-CN.md)
  中交付 2 的撤销部分（"到期/回收/撤销时取消"）。不引入 provider、
  capability、配置面或 lease 词表。
- 状态：替代提案，待维护者明确决定。本条目不替换 #5308 正在评审的停止契约，
  也不声称停止能力已交付。只有选定本替代方案后，下述实现验收才适用。
- [English](2026-10-03-delegation-stop-lease-fence.md)。

## 测量到什么

[#5308](https://github.com/loopx-project/loopx/pull/5308) 已重新打开。
[10 月 3 日最新评审](https://github.com/loopx-project/loopx/pull/5308#pullrequestreview-5401677786)
要求从 canonical authority 读回租约义务（R1）、由既有 Host 边界提供完整执行的
退出观察（R2），以及在类型化 owner 中区分下一步动作和最终回执（R3）。该评审
已明确取消逐次追查历史失败原因这一合入前置条件。旧失败仍是历史证据，不能据此
断言当前设计无法修复。

两份方案服务于同一调用者结果，但保证不同：

| 边界 | #5308 正在评审的实现 | 本替代提案 |
| --- | --- | --- |
| 顺序 | 先证明原执行退出，再释放其租约 | 先撤销租约，单独观察进程退出 |
| 完成反馈 | `settled` 要求 ACK、holder 释放、Host 退出和租约义务已解析 | `revoked` 证明提交权限失效；只有 `drained` 报告执行退出 |
| authority 模式 | 通过 dispatch fence 保留既有无租约路径 | 要求 canonical `hard_lease`，拒绝无租约路径 |

这是两份替代的公共契约，不是可以互换的 phase 名称，不能同时实现在同一个
`delegation stop` / `stop_delegation` 入口下。当前 #5308 的修复遵循 R1–R3。
选择本替代方案需要明确的替代决定、CLI/MCP/读回/文档的配套修改，以及下述实现
验收；仅合入设计文档不会改变运行时契约。两者均不代表团队或 Goal 已完成，
也不能用撤销权限证明物理进程已退出。

在测量基线上，`main` 已提供可复用的租约 fence 和 supervision：

- `Delegations._complete_delegated_todo` 没有本次执行已取得的 lease 就拒绝提交，
  并通过 canonical lease CAS 完成
  （[#5466](https://github.com/loopx-project/loopx/pull/5466)）。
- `runLeasedHostProcess` 以 `min(30 s, remaining/2)` 的节奏重新证明原
  owner/key/epoch，在续期被拒绝、当前证明丢失或最后已证明的 `expires_at` 到达时
  请求取消；强制进程组终止前有六秒 grace
  （[#5436](https://github.com/loopx-project/loopx/pull/5436)）。每个 lease 命令
  最长运行 60 秒，回复丢失时以同一意图重试一次，而已证明的到期计时始终有效：
  `tests/control_plane/test_leased_host_process.py::test_real_renewal_faults_keep_original_deadline_and_identity`
  在真实 File 与 SQLite authority 上证明，在该测试的监督结构内，续期挂起不会
  撤销由到期时刻触发的取消。
- `tests/test_delegation_lease_lifetime.py::test_real_revocation_or_new_execution_stops_nested_host_without_acceptance`
  在真实 File 与 SQLite authority 上证明：嵌套监督正常运行时，释放该 lease 后，
  嵌套 Host 及其子进程
  在 worker 返回前停止，Todo 保持未完成，重试该操作既不会重新取得旧执行也不会
  再次启动 Host。

旧执行 key 再次到达 authority 的唯一途径是重放已退役的 acquire 回执。acquire
回执身份由 `(goal_id, todo_id, owner, idempotency_key)` 确定性生成，而重放要求
当前证明，因此 lease 一旦 released，其 key 就永久退役，不需要新状态。

## 提议的契约

1. **范围。** 本机 authority 上一个经授权、有 binding 的委派操作。不是团队或
   Goal 停止，不是协调者暂停，不是跨宿主信号，前端只有一个记录状态标签。
2. **意图。** `stop` 是 binding 的 requester 对一个操作的明确意图。它在任何
   fence 写入之前持久化在操作记录旁，携带 requester 身份和一个稳定的
   `stop_id`，不能由信号、超时或进度推导。重复 stop 返回同一回执。
3. **Fence。** 该执行自己的 canonical hard lease（`owner`、`idempotency_key`、
   `lease_epoch`）是唯一的 fence。Delegations host 通过既有 canonical lifecycle
   以当前 version 的 CAS 释放它，只重试 version 不匹配这一种竞争。这与 host
   代表成员 claim、renew、complete 时行使的是同一份信任。释放之后，authority
   拒绝该执行的一切续期、完成 CAS 与 acquire 重放；迟到的 Todo 完成与结果
   验收在构造上不可能。文件锁、worker ACK、lane 探测、进程组记录都不是保证的
   一部分。该保证只约束经过 canonical authority 校验的写入，不能撤回 Host 已经
   发出的 shell 命令、网络请求或其他外部副作用。
4. **回执。** 类型化的 TypeScript owner 在每次读取时由当前事实推导一个
   phase；不持久化 phase。
   - `requested`：意图已持久化，执行尚未暴露可释放的 lease。再次读取；worker
     会在启动 Host 前观察到该意图。
   - `revoked`：释放已提交，或该执行已被另一个 epoch 或到期 fence。旧执行不能
     提交受 canonical authority 校验的效果；这本身不证明可以重叠执行外部工作
     或交接资源。
   - `drained`：在此之上，既有 Host owner 证明原执行及全部归属进程组已经退出，
     或证明 Host 从未启动且已不存在继续启动的可能。leased supervisor 返回、
     操作记录为 `stopped` 或 grace 已经过期，都不足以证明这一点。
   - `noop`：操作在 fence 生效前已经 `accepted` 或 `rejected`。原结论保留，
     不写任何内容。
   Drain 是观察，绝不是结算条件。worker 已死时停留在 `revoked` 且
   `host_supervision: unobserved`；只有绑定原执行的完整 Host 证据才能建立 drain。
   内层 supervisor 不可用或被中断时，即使外层已经返回，drain 仍未获证明。
5. **Worker 观察。** worker 在取得 lease 前和启动 Host 前各检查一次意图，任一
   检查点命中时释放自己的 lease；在意图存在时任何被监督执行返回后记录
   `stopped`。这只说明 worker 处理过停止，不能证明完整 drain。检查点用于避免
   浪费工作；canonical 写入保证来自 lease fence。
6. **Authority 模式。** stop 要求 Goal 的 canonical `hard_lease` 模式。在
   `legacy` 或 `soft_claim` authority 上没有执行 lease，因此没有 fence；
   `stop --execute` 在任何写入前被拒绝，原因中写明模式。提升 Goal 是启用步骤。
7. **生命周期。** 已停止的操作拒绝 `resume`；继续需要新操作，它会取得新的
   lease epoch。stop 永不完成 Todo、不结算 Goal、不改变已验收结果。
8. **Drain 延迟。** 撤销与资源退出是两个事实。release 提交后 fence 生效；既有
   leased supervisor 在续期被拒绝、当前证明失败或最后已证明的到期时刻请求取消，
   authority 回复在途时也保留这个到期计时器。这些是取消触发条件，不是所有嵌套
   进程退出的无条件期限。内层 supervisor 被中断或无法观察其清理时，外层返回和
   到期加六秒 grace 都不能证明内层 drain。约三十六秒的健康路径只是名义值，要求
   authority 及时响应、release 时没有在途续期且监督正常运行。慢响应、回复丢失或
   清理失败时，保留 `revoked` 与 drain 未证明的事实。只有真实完整的 Host 证据才
   能得到 `drained`；本提案不新增 drain 硬期限、清理服务或第二个进程生命周期 owner。
9. **入口。** CLI `delegation stop --execute` 与 MCP `stop_delegation` 共用
   `Delegations.stop`；`read`、`wait` 与 inventory 暴露回执与 `stopped` 观察。
   dashboard 展示"停止已登记"，不声称执行资源已释放。

## 本条目提议的决定

- **D1，仅限 hard lease。** 把停止保证限定在 canonical lease fence，代价是
  拒绝既有无租约路径。#5308 保留这些路径的 dispatch fence，取舍需由维护者决定。
- **D2，用 release 而非新增 `revoked` lease 状态。** 新状态会扩展被
  lifecycle、proof、retirement、migration 与 recovery 多个 owner 消费的词表；
  如测量所示，release 已经使 key 退役。
- **D3，drain 单独报告，不作为撤销的条件。** 允许在进程仍运行时报告提交权限
  已失效；这不满足 #5308 的 `settled` 承诺，也不证明可以立即交接执行资源。

## 实现 PR 必须给出的验收

实现 PR 需在 File 与 SQLite authority 上以真实进程逐项证明以下各点，记录观测到的
release 到 drain 耗时，且从不断言名义上的三十六秒：

- **健康撤销，作为正向对照。**
  `test_real_revocation_or_new_execution_stops_nested_host_without_acceptance`
  继续通过：释放 lease 后，嵌套 Host 及其子进程在 worker 返回前停止，Todo 保持
  未完成。
- **续期进行中时 release。** 使用长 TTL（例如 180 秒），在一次续期已开始、且其
  authority 回复被延迟时提交 stop 的 release。release 提交后回执即为 `revoked`，
  嵌套 Host 仍在运行时不报告 `drained`。旧执行的续期、Todo 完成与验收都不能提交；
  分别观察既有取消触发和完整 Host 退出；不能证明 drain 时保留 `revoked`。
- **authority 回复丢失。** 续期命令两次失败或在超时内始终无回复时，回执与 fence
  的性质不变，最后已证明的到期计时器仍负责触发取消。取消观察不等于完整嵌套
  进程 drain。
- **内层 supervisor 中断。** 实际 Host 启动后暂停内层 supervisor，释放原 canonical
  lease，等待外层调用返回。若独立观察到后代仍在运行，包括到期加 grace 之后，
  回执必须保持 `revoked` 且 drain 未证明。只有后续绑定原执行的完整 Host 证据才能
  报告 `drained`。保留 supervisor 正常运行的正控，并保证断言失败时 fixture 仍清理
  自己的进程组。

## 本条目不建立什么

停止能力不由本条目实现。Windows 原生 drain、PostgreSQL 重新资格化、跨宿主
停止、Lark 控件、整团队停止与安装态验收都在切片之外。lease 记录对 File、
SQLite、PostgreSQL provider 是不透明 JSON，预计不需要 provider 改动，但这是
实现 PR 需要评审的声明。
