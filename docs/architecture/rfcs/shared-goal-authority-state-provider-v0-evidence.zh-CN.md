# 验证说明：NoKV canonical coordination provider（v0）

> 历史证据存档。此处 `nokv-shadow-provider` 和 Python head/executor 原型已于
> 2026-09-26 退役；命令和路径仅描述当时基线，不再是当前运行指南。当前 File/SQLite
> 资格及 NoKV Stage 2A 入口见 [E2E ladder](../../../examples/shared-goal-authority-e2e/README.md)。


- 配套 RFC：[LoopX 共享控制面权威与可插拔状态 Provider (v0)](./shared-goal-authority-state-provider-v0.zh-CN.md)
- 参考实现与探针：`examples/nokv-shadow-provider/`
- 证据范围：canonical coordination aggregate、target-scoped conflict、内部 CAS
  rebase 与历史 operation receipt 重放

## 1. 当前合并证据回答什么

本轮 requested changes 针对的核心问题不是“同一命令有没有重复写入”，
而是：命令 A 成功后，即使命令 B 已推进当前 head，A 的调用方是否仍能
取回当时由 authority 签发的原始回执。

修订后的参考模型把以下两部分放进同一次 per-goal head CAS：

1. 当前 canonical coordination state；
2. `operation identity -> request digest + original receipt` 的可重放索引。

因此，当前候选的合并门只接受能够复跑并机器判定的确定性回归：

| 场景 | 必须满足的不变量 |
|---|---|
| A 首次提交 | coordination state 与 A 的 receipt-index entry 同一次 CAS 生效 |
| B 推进独立 todo | 当前 authority revision 前进，A 的历史回执仍可定位；B 不接管 A 的 todo |
| 重建 authority 后重放 A | 返回 `already_applied` 与 A 的原始回执；逐字段相同 |
| 重放后的 head | revision、coordination state 与 receipt index 均不发生第二次变化 |
| 相同 operation identity、不同请求 | request digest 不匹配，显式 fail closed |
| 两个独立 todo 并发 claim | 在 `write_scopes=[]` 且目标 precondition 未变的边界内，第一次 CAS 的 loser reload 并重验，内部 rebase 后两者都 `applied` |
| 同一 todo 并发 claim | 恰好一胜；loser 得到 target-specific conflict 且没有 receipt |
| 无关 contention 耗尽预算 | 返回 `failed/provider_contention_exhausted`，不生成当前 operation receipt |

本轮候选已从仓库根目录复跑：

```bash
python3 examples/nokv-shadow-provider/probes.py contract
```

命令退出码为 `0`，并逐项报告以下九个通过的合同探针。自 Stage 2 切片合并
后，claim/CAS 探针全部由生产 `CoordinationAuthorityExecutor` 与生产 head
编解码驱动（不再存在第二套参考 authority），且每个探针的持久 head 都经
生产 `validated_head` 回环校验：

- `contract.bootstrap_and_preconditions`；
- `contract.a_success_b_advance_replay_a`；
- `contract.operation_identity`；
- `contract.competing_claims`；
- `contract.crash_windows_and_ambiguity`；
- `contract.version_domains_and_retain_all`；
- `contract.nokv_adapter_exception_mapping`（NoKV adapter 仅按异常类分类：
  `FileNotFoundError` 是唯一的 missing 信号；其余客户端失败在读路径抛类型化
  `ProviderUnavailableError`、在 CAS 前置为类型化 `failed`，携带 not-found
  文案的路由故障不再被误判为未初始化）；
- `contract.durable_completion_projection`；
- `contract.durable_completion_fail_closed`（含显式 `completion_continuation`
  缺失或矛盾时的 fail-closed）。

最终机器判定为
`{"ok":true,"probe":"contract.summary","probes":9}`。这是使用确定性
provider fake 验证 authority/provider 合同的结果；它没有启动或验证真实
NoKV 服务，不能替代 live-stack 证据。该命令由
`tests/test_nokv_shadow_provider_probes.py` 作为常规 pytest 门槛守住，
LoopX 生命周期合同的变化会在这里立刻显形，而不是让证据静默变红。

这里的“逐字段相同”至少覆盖原始 `accepted_authority_revision`、
`accepted_todo_revision`、`lease_id`、`lease_epoch`、`applied_at` 与
`expires_at`。新的 `observed_authority_revision` 和 `authorization_status`
可以作为当前观测返回，但不能替代或改写原始回执。

## 2. 为什么旧 P3 不构成这项证据

初版 reference provider 只在 head 中保存最后一条 envelope。旧 P3 在 A
已经被后续命令超越后，只检查：

- 返回值被归类为 `already_applied`；
- generation 没有继续前进；
- 没有产生第二次状态效果。

它没有断言 A 的原始 `lease_id / epoch / expires_at_unix`（旧实现字段名），
所以即使这些 authority proof 已经丢失，探针仍会通过。旧 P3b 虽然检查
lease id 稳定，
测试的却是仍位于当前 head 的最近赢家，不是 A → B → replay A 的历史序列。

这两项旧结果只能说明“没有明显 double apply”，不能说明“原始 receipt
可恢复”。修订后的 deterministic regression 必须在 B 已推进 head、并重建
authority 后比较完整原始回执。

## 3. 证据边界

这份说明及其确定性回归可以证明：

- coordination aggregate 与 receipt index 的原子提交模型；
- 未 bootstrap、未知 todo、stale todo revision 与不合格 actor 均不能隐式建账；
- stale authorization/dependency/gate precondition 明确 conflict，不会被无关 head
  revision 混淆；
- bootstrap 只接受 allowlist 字段、portable repository identity 与 digest，
  不接收本地路径；
- historical replay 不依赖当前 head 的最后一个 command；
- 相同 operation identity 的 semantic request-digest 绑定，同时排除 transport-only
  retry metadata；
- 同一 todo 的 competing claim 只接受一个 winner；独立 todo 的并发 claim 在内部
  CAS rebase 后都能成功；这里的 independent 限定为 reference 的空 write scope；
- accepted claim 后 todo 仍为 `open`，由 `claimed_by` 与 lease 表达 ownership，和
  当前 LoopX 本地 claim 语义一致；
- `authority_revision` 只作为 Goal-wide commit/audit sequence，不作为所有客户端
  command 共用的业务前置条件；
- CAS 前后故障及 ambiguous result 不会把 receipt 缺失当作成功：同 generation 下
  缺失时 fail unproved，generation 前进后也必须重验并由新 CAS 成功才返回 applied；
- 持续无关 contention 耗尽内部 retry budget 时 fail closed 且不创建 receipt；
- replay 不产生第二次 coordination state 变更；
- reference head 在连续推进时携带 `retain_all_v0` 与既有 receipt entry。

它们不证明：

- LoopX 当前 runtime 已切换到 shared coordination provider；
- Markdown/event projection 已完成 source-of-truth 迁移；
- run history、status projection 或 quota 已进入同一个 provider；
- Agent IM、wake、presence 或 offline delivery 已实现；
- NoKV 的 HA、owner failover、receipt compaction/GC 或自动晋升；
- `renew_lease`、`release_lease` 与完整生产 lease lifecycle；
- 不同 todo 的非空 write-scope overlap 检测，以及动态 eligibility projection
  publisher 的 coverage/no-ABA 资格化；
- 生产性能、跨区域延迟、容量上限或生产可用性。

上述项目需要各自的实现、故障注入和独立 reviewed gate，不能从一个
deterministic provider regression 外推。

## 4. NoKV 静态兼容性基线

本轮静态核对钉在 NoKV
[`90883d13539e31185f0d78131989fb51912dbd7e`](https://github.com/NoKV-Lab/NoKV/commit/90883d13539e31185f0d78131989fb51912dbd7e)。
该基线的 Python `publish_bytes` API 支持用 `expected_generation` 表达
create-only 或 replacement CAS，并暴露可选的 publication `operation_id` 与
`artifact_revision_id`。这只证明 NoKV 源码中存在 reference provider 所需的
静态接口缝合面，不证明该映射在真实服务上的错误分类、重启恢复或耐久行为。

本次验证环境没有可用的 NoKV Python SDK 与配套服务，因此没有执行当前候选的
live NoKV 测试。确定性 fake 的通过结果与这项静态 API 核对必须分开陈述。

## 5. 2026-08-05 live-stack 数据的处置

初版提案曾在 etcd、S3 兼容存储、`nokv serve` 和 Python SDK 组成的本地
真实栈上运行并发写、最近命令重放、kill-9 探针和延迟采样。那些运行使用
的是已经被本次 review 判定不满足 historical receipt 合同的
**last-envelope adapter**。

因此，旧数据在本轮一律降格为历史调试基线：

- 不作为修订后 provider 的通过证据；
- 不作为 NoKV 晋升或生产可用性证据；
- 不沿用旧延迟数字评价新 aggregate/receipt-index 写放大；
- 不把旧 kill-9 观察表述为重启恢复或 HA 验证。

如需恢复 live-stack 证据，必须针对当前候选重新运行，并至少同时报告：

1. 精确 LoopX 与 NoKV commit；
2. 当前 provider/probe 源码 digest；
3. A → B → replay A 的完整字段断言；
4. same-id/different-request 负例；
5. 未执行或失败的 restart、retention、HA 与性能项目。

在完成该复跑之前，本 PR 不发布新的性能或生产结论。

## 6. 公共与私有边界

可提交证据只包含合成 goal/todo/operation identity、紧凑状态分类、revision
和经过 allowlist 的 receipt 字段。不得提交：

- 本地绝对路径或机器身份；
- 凭据、token、对象存储 secret 或私有端点；
- raw logs、traceback 尾部、transcript 或运行轨迹；
- 私有 repo 内容或 raw evidence 正文。

真实栈若产生原始日志，应保留在 ignored local state；公共文档只记录可复跑
命令、候选 commit、机器可判定的不变量和明确的未验证项。

## 7. Stage 2 切片复跑记录（2026-08-23）

按第 5 节的复跑要求逐项记录本轮 live 证据：

1. **精确 commit**：LoopX 侧为 Stage 2 分支（本记录随该分支评审，git 树即
   源码 digest），基于 Stage 1 Part 2 分支头；NoKV 侧为 `v0.11.0` 标签，
   经其 Python SDK wheel 驱动。
2. **探针源码**：`examples/nokv-shadow-provider/live_e2e.py`（随分支评审）。
   驱动的是生产 `CoordinationAuthorityExecutor` 与生产 head 编解码，不再是
   参考实现。
3. **A → B → replay A 断言**：八场景矩阵对 file provider 与 NoKV provider
   逐行结果一致（含精确重放行：重建 executor 后 `already_applied` 且
   receipt 逐字段相等；lost-response 行：ambiguous 后经 receipt index 收敛
   到 `already_applied`）。
4. **same-id/different-request 负例**：同 operation_id 改语义字段返回
   `rejected/operation_identity_mismatch`，且聚合与 generation 不变。
5. **未执行与限定**：renew/release/reclaim、retention 策略、HA、多节点
   未验证。SIGKILL 重启演练已在本地 dev 栈执行（owner 被杀后运维式重开：
   head 字节级一致、原 receipt 精确重放、三版本域恢复推进）；该演练脚本
   因绑定具体部署形态保留在本地忽略状态，不入公共树。回执增长、并发包络
   等数字是单节点 dev 栈的量化观测，按第 5 节规则不构成生产结论；其含义
   已写入 RFC 第 11 节 Stage 2 状态小节。

## 8. Stage ladder E2E 复跑记录（2026-09-03）

按第 5 节的复跑要求逐项记录本轮 stage ladder 证据：

1. **精确 commit**：LoopX 侧为 `test/shared-authority-e2e-ladder` 分支（基于
   PR #3818 第三轮修订头，含单一 effective runtime root 修复；ladder 报告的 `bindings.loopx_commit` 与
   `bindings.loopx_tree_dirty` 记录实际运行的树）。NoKV 侧本轮在本机单节点栈上
   执行（`nokv` 83971e62ab，Python SDK 0.11.0，`nokv serve` 以静态 etcd 路由 +
   RustFS 对象存储运行；报告只记录 `bindings.nokv_client_config_sha256` 与 SDK
   版本，不记录任何连接值）；PostgreSQL 侧无可达栈，对应行按设计报告 `unverified`。
2. **探针源码**：`loopx/control_plane/testing/authority_e2e_ladder.py`、
   `loopx/control_plane/testing/authority_e2e_fixtures.py` 与只读 TypeScript
   探针 `tests/control_plane_ts/authority_store_readback_probe.ts`（随分支评审；
   报告 `bindings.probe_sha256[]` 记录其 digest）。入口为
   `examples/shared-goal-authority-e2e/ladder.py`，pytest 投影为
   `tests/control_plane/test_shared_goal_authority_e2e.py`。各行驱动的是真实
   `python -m loopx.cli` 与生产 `FileAuthorityStore`，不是参考实现。
3. **断言**：本轮 9 个 deterministic 行全部 pass，2 个 NoKV live 行在本机栈上
   pass：`s0.nokv_live_matrix` 十三个 NoKV 场景行全为 true 且与 file provider 的
   十二个共享行逐行一致；`s2a.nokv_live_qualification` 对既有 workbench 以新铸
   tenant/goal 运行已合并的 live 资格探针，13 项 check 全部 `passed`，final
   generation `3`，SDK `0.11.0` / API `1`，且报告声明未改变 authority source、未
   证明可用性。deterministic 行：`s0.file_matrix_twelve_rows`
   十二个 file provider 场景行全为 true；`s1.cli_document_decodes_through_ts_store`
   三次 CLI 写入经 TypeScript store 回读 cursor `3`、operation id 按序一致、首条
   receipt found；七个 `s2c1.*` 行（configure 往返、12 个 writer family 全部
   captured 且候选 cursor `12`、default-off 隔离、候选失败保主写、SIGKILL 崩溃
   间隙只丢失一次 observation、`--runtime-root` 与 `common_runtime_root` 不同时
   五次写入落入单一 store identity 且候选 cursor `5`、`migrate-state` 新 lineage
   cursor `1`）。
4. **负例**：幂等 re-acquire 不携带 `authority_shadow`；default-off goal 无
   `authority-shadow/` 目录且响应字段与 observed goal 一致；候选目录被占用时
   `outcome=failed` / `reason_code=shadow_observation_failed` 但主写已提交；
   迁移后的候选序列化中不含旧 store identity、legacy revision、源路径与私有
   字节；报告隐私扫描把注入的临时路径改写为 `fail/privacy_violation`，仅泄露到
   bindings 块时 `summary.privacy_violations=1` 且退出码为 1，且 live 变量清空时
   ladder 退出码为 1（均由 pytest 钉住）。
5. **未执行与限定**：`s2b.postgresql_conformance_live`（`postgres_url_missing`）
   本轮 unverified；在没有 NoKV 与 PostgreSQL 栈的 CI 环境里三个 live 行都报告
   `unverified`。9 个 `s2c2.*` 行以 pending 声明，未宣称；选中任一 pending 行而不传
   `--allow-pending` 时 ladder 以非零退出，零执行的报告不可能显示为 green。默认
   全量运行退出码为 1，`--allow-unverified --allow-pending` 才为 0。Stage 2C
   parity、outbox、drain 与增长门槛均未验证；本记录不构成任何 provider 晋升
   或生产结论。
