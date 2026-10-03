# 停止委派操作：canonical lease 是唯一的 fence

- 基线：`e55489c77`，2026 年 10 月 3 日测量。
- 结果：总体 roadmap S4（"restart/cancel/drain/stop 保留工作并 fence 旧执行者"）
  与 R2 的有界单操作停止；对应
  [9 月 27 日 host-supervision 计划](2026-09-27-host-supervision.zh-CN.md)
  中交付 2 的撤销部分（"到期/回收/撤销时取消"）。不引入 provider、
  capability、配置面或 lease 词表。
- 本条目是后续实现 PR 所依据的规格。它记录一个设计决定及其背后的测量，
  不声称停止能力已经交付。
- [English](2026-10-03-delegation-stop-lease-fence.md)。

## 测量到什么

已关闭的 [#5308](https://github.com/loopx-project/loopx/pull/5308) 试图交付同一
用户结果，四天内收到十六份维护者评审，其中十五份为 `REQUEST_CHANGES`。
它的九个阻塞发现属于同一个结构性性质：回执的终态 `settled` 是对六个独立写者
（stop sidecar 的 ACK、operation lock 探测、Turn lane holder 记录、内层 Host
进程组记录、外层 CLI 进程组记录、canonical lease）所写事实的读侧合取，而任意
两个写者之间都存在交错窗口。每次修复再加一个事实或一把锁，每轮评审再找到一对。
该分支合并了十五次 `main`，同期 `main` 上同一 lease owner 改动了十二次。最终
head 通过了 147 项选定的真实进程测试，并在一个无法闭合的"历史归因"hold 下关闭。

`main` 上已经存在那个 PR 用文件锁模拟的 fence：

- `Delegations._complete_delegated_todo` 没有本次执行已取得的 lease 就拒绝提交，
  并通过 canonical lease CAS 完成
  （[#5466](https://github.com/loopx-project/loopx/pull/5466)）。
- `runLeasedHostProcess` 以 `min(30 s, remaining/2)` 的节奏重新证明原
  owner/key/epoch，证明丢失时取消委派 CLI 及其嵌套 Host；强制进程组终止前有
  六秒 grace（[#5436](https://github.com/loopx-project/loopx/pull/5436)）。
- `tests/test_delegation_lease_lifetime.py::test_real_revocation_or_new_execution_stops_nested_host_without_acceptance`
  在真实 File 与 SQLite authority 上证明：释放该 lease 后，嵌套 Host 及其子进程
  在 worker 返回前停止，Todo 保持未完成，重试该操作既不会重新取得旧执行也不会
  再次启动 Host。

旧执行 key 再次到达 authority 的唯一途径是重放已退役的 acquire 回执。acquire
回执身份由 `(goal_id, todo_id, owner, idempotency_key)` 确定性生成，而重放要求
当前证明，因此 lease 一旦 released，其 key 就永久退役，不需要新状态。

## 契约

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
   一部分。
4. **回执。** 类型化的 TypeScript owner 在每次读取时由当前事实推导一个
   phase；不持久化 phase。
   - `requested`：意图已持久化，执行尚未暴露可释放的 lease。再次读取；worker
     会在启动 Host 前观察到该意图。
   - `revoked`：释放已提交，或该执行已被另一个 epoch 或到期 fence。可以用新
     操作安全继续该 Todo；旧执行无法提交任何 canonical 效果。
   - `drained`：在此之上，操作记录了 `stopped` 观察，且 `host_supervision` 为
     `returned`（leased supervisor 在证明丢失后返回）或 `not_launched`（未
     启动 Host）。
   - `noop`：操作在 fence 生效前已经 `accepted` 或 `rejected`。原结论保留，
     不写任何内容。
   Drain 是观察，绝不是结算条件。worker 已死时停留在 `revoked` 且
   `host_supervision: unobserved`；之后的绿色读取不会升级它。
5. **Worker 观察。** worker 在取得 lease 前和启动 Host 前各检查一次意图，任一
   检查点命中时释放自己的 lease；在意图存在时任何被监督执行返回后记录
   `stopped`。这些检查点避免浪费工作；保证来自 fence，不来自检查点。
6. **Authority 模式。** stop 要求 Goal 的 canonical `hard_lease` 模式。在
   `legacy` 或 `soft_claim` authority 上没有执行 lease，因此没有 fence；
   `stop --execute` 在任何写入前被拒绝，原因中写明模式。提升 Goal 是启用步骤。
7. **生命周期。** 已停止的操作拒绝 `resume`；继续需要新操作，它会取得新的
   lease epoch。stop 永不完成 Todo、不结算 Goal、不改变已验收结果。
8. **Drain 延迟。** 由 supervisor 的续期节奏加 grace 界定：在既有 supervisor
   下，释放提交后最多约三十六秒。本切片不增加信号加速路径。强制进程组终止后
   的嵌套 Host 清理仍是 #5436 的既有 supervisor 边界，此处不重新证明。
9. **入口。** CLI `delegation stop --execute` 与 MCP `stop_delegation` 共用
   `Delegations.stop`；`read`、`wait` 与 inventory 暴露回执与 `stopped` 观察。
   dashboard 展示"停止已登记"，不声称执行资源已释放。

## 此处作出的决定

- **D1，仅限 hard lease。** 替代方案是为无 lease 路由再建一套线性化机制，
  正是上面失败的设计。
- **D2，用 release 而非新增 `revoked` lease 状态。** 新状态会扩展被
  lifecycle、proof、retirement、migration 与 recovery 多个 owner 消费的词表；
  如测量所示，release 已经使 key 退役。
- **D3，drain 只报告，不要求。** 要求它会重现 #5308 中的每一个进程归属窗口。

## 本条目不建立什么

停止能力不由本条目实现。Windows 原生 drain、PostgreSQL 重新资格化、跨宿主
停止、Lark 控件、整团队停止与安装态验收都在切片之外。lease 记录对 File、
SQLite、PostgreSQL provider 是不透明 JSON，预计不需要 provider 改动，但这是
实现 PR 需要评审的声明。
