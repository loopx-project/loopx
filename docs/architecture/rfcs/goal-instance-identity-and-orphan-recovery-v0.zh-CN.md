# Goal 实例身份与孤儿状态恢复（v0）

- **RFC 状态：** Draft
- **交付成熟度：** Identity/recovery 提案；codec 前置已由 #4917 交付
- **作者／Owner：** LoopX contributors
- **创建日期：** 2026-09-23
- **最后规范修订：** 2026-09-23
- **实现基线：** `23edcb19c70394480e3a9ebe8a960f5a320c5342`
- **相关契约：** [Issue #4801](https://github.com/loopx-project/loopx/issues/4801)、[orphan fence 切片 #4808](https://github.com/loopx-project/loopx/pull/4808)
- **语言镜像：** [英文语义镜像](goal-instance-identity-and-orphan-recovery-v0.md)

## 文档地图与维护契约

第 1-10 节是长期设计与验收契约，第 11 节是规范性交付计划，第 12
节记录未决事项；其中的建议不等同于批准。附录只记录非规范性证据和决策历史。

中英文文档互为语义镜像。任何规范性变更都必须在同一个 PR 中同步修改两份文档。

---

## 1. 决策摘要

本 RFC 共同作出五项决策：

1. `goal_id` 保留为面向人的别名。由项目 registry 拥有且不可变的
   `goal_instance_id` 标识一次 Goal 生命周期。
2. 每个持久 host、session、channel、automation、Turn 和 quota binding
   都携带精确二元组。只有源项目 registry 可以授权执行；全局投影和 binding
   store 都不能授权。
3. 实例身份需要不兼容的 activation format 加提交时 fence。已交付的 M0 codec
   不代表 identity enforcement；拒绝一种旧 decoder 不能排除所有旧 effect 路径。
4. 孤儿恢复是一个 preview-first、有 journal 的生命周期操作。操作者必须对每个
   candidate 明确选择 `adopt`、`migrate`、`archive` 或 `delete`，系统不得猜测
   或合并。
5. Activation 必须显式、以整个项目 registry 为范围。已激活项目内 unstamped
   binding 只读；未激活项目保留 legacy 行为且不提供 ABA 保证。全局默认切换另行决策。

现有 state-file 位置、host 专属 runtime 目录布局、provider revision、
Lease 和全局 registry 的角色都不会成为新的 Goal authority。本 RFC 不批准
自动清理所有外部 provider、自动选择 candidate，也不允许普通 bootstrap
直接修改孤儿状态。

## 2. 问题与动机

当前 `goal_id` 同时承担展示名称和持久身份。Goal 可能已从项目 registry
消失，但 active-state 文件、session、host binding、Goal Channel 和 heartbeat
automation 仍然存在。之后创建同名 Goal 时，这些旧记录可能被误认为当前记录。
这是典型的 ABA 问题：

1. 名为 `release` 的 Goal A 创建了持久 attachment。
2. Goal A 被删除，但部分 attachment 仍存在。
3. 系统创建了同名 Goal B。
4. 只保存 `release` 的 attachment 无法区分 A 和 B。

#4808 已交付的 fence 会在 registry 中不存在 Goal、但项目中仍有状态 candidate
时阻止 guided bootstrap。它仍无法区分 Goal 生命周期、解决 orphan，也无法在
删除后同名重建时让旧 attachment 失效。

任何单独 host 或 session store 都无法局部解决该问题。若 host 自行生成身份，
它就成为第二个 Goal authority；若删除流程同步理解所有 provider，它就变成跨
owner 的分布式清理事务。LoopX 需要源拥有的生命周期身份，并让 admission/commit 与 retirement 串行化。
身份匹配是必要条件，不是授权凭证。

### 不变量

1. 只有源项目 registry 可以创建或解析 Goal instance。
2. Goal 终局删除后重新创建时必须获得新的随机 instance ID，即使复用了
   `goal_id`、路径、objective 或配置。
3. 配置更新、provider 重连、Lease 续期、新 Turn 或 binding 改写都不能轮换
   instance ID。
4. stale、缺失、畸形或 legacy binding 不能为 instance-aware Goal 授权执行。
5. stale 全局投影可以辅助路由，但不能授权写入。
6. Source safety 完成前，resolution journal 阻止该 Goal 的普通激活／修改，
   即使 registry 已发布；精确 recovery 保持可用，source 完成后的投影 retry 单独处理。
7. resolution 最终只能留下一个被选择的可写状态 authority，或保持 Goal
   缺失且没有可写 candidate。
8. 破坏性 resolution 必须具备已验证的定向备份、精确 plan digest、显式 Goal
   确认和写后回读。
9. crash retry 复用 prepared identity 和 owner idempotency key；包括只做
   archive/delete 的所有 plan，都必须在首次修改前写 journal。
10. 已发布的旧 writer 不能静默打开或改写 instance-aware 项目 registry。

## 3. 范围与非目标

### 范围内

- 项目 registry 所有的 Goal 生命周期身份和精确 binding 比较。
- 面向旧 reader/writer 的 registry 级兼容边界。
- 在所有第一方持久 binding owner 中传播 instance。
- 在激活、恢复、投递、状态修改和 quota 授权处做最终检查。
- 项目本地 orphan state 的 preview、backup、apply、resume 和 rollback。
- orphan state、legacy identity、stale binding、stale 全局投影、未完成 journal
  和人工清理的诊断。
- 将 observation 与 enforcement 分离的分阶段迁移。

### 非目标

- 改变面向人的 `goal_id`，或把 instance ID 放进 state path。
- 迁移 Codex、Pi、OpenCode 或其他 host 的底层 runtime 目录。
- 合并多个 active-state candidate。
- 将 timestamp、路径、内容相等、全局投影或 host 记录当作身份依据。
- 把外部 provider 的物理删除作为安全边界。
- 引入公开、通用的 binding-provider 框架。
- 改变 Claim、Lease、authority revision、Turn 或 provider idempotency
  语义；它们只新增 Goal instance 绑定。
- 允许旧二进制降级或编辑严格 registry。

### Roadmap 定位与契约协同

[整体 roadmap](loopx-overall-roadmap-v0.zh-CN.md) 以验收结果、人的注意力成本和
恢复能力衡量产品。本 RFC 提供 R5 的有界 lifetime fence，供 R2/R3 消费，
不据此完成 R1–R7 或晋升 provider。

| 既有 owner／契约 | 本 RFC 的贡献与边界 |
| --- | --- |
| [TS 收敛](typescript-control-plane-migration-v0.zh-CN.md)、R5 | Match、admission、retirement、recovery transition 归 typed kernel；Python 保留 codec、文件和 host adapter。不得新增第二套 Python policy engine 或逐字段 RPC。 |
| [Shared authority](shared-goal-authority-state-provider-v0.zh-CN.md)、D1–D3／R6 | 将已有 head、operation receipt、authorization projection 绑定 lifetime。Provider incarnation、revision、lease epoch 与 Goal instance 相互独立；不新增 store、不切换默认。 |
| [Shared alignment](shared-goal-alignment-and-governed-amendment-v0.zh-CN.md)、R4 | Identity 不等于 intent、permission 或 work revision。保留 `intent_basis` 的 source-facts 语义；amendment 仍归原 owner。 |
| [语义 handoff](capable-manager-semantic-handoff-v0.zh-CN.md)、[协作](agent-im-openviking-collaboration-v0.md)、R2/R3 | Request、adoption、result 和原会话 return 绑定 instance。A 的迟到结果不能结算 B；transport 成功和 registration 不证明验收。 |
| [单 owner daemon](single-owner-local-daemon-v0.md) | 复用 profile quiescence 和真实 executor drain；PID、package version 或停止 heartbeat 不证明旧 writer 不会回来。Daemon identity 不授予 Goal authority。 |

[#4808](https://github.com/loopx-project/loopx/pull/4808) 已交付 guided fence；
[#4912](https://github.com/loopx-project/loopx/pull/4912) 提议 diagnosis；
[#4915](https://github.com/loopx-project/loopx/pull/4915) 提议 host-neutral paths。
复用这些 owner，但不能将 open PR 视作已交付。Canonical Todo 拥有执行状态，
本文件拥有验收与依赖边界。

## 4. 当前系统契约

在上述 main 基线上，[#4917](https://github.com/loopx-project/loopx/pull/4917)
已交付 `control_plane/projects/registry_codec.py`、保留格式的 transaction 和
生产 I/O census。它读取 legacy object 及 `loopx_project_registry_envelope_v1`
array，并接受 writer protocol `goal_instance_v1`；它**不**生成、比较或强制
Goal instance。因此，懂 codec 但不懂 identity 的版本也是旧 writer，原先
“旧 loader 只接受 object 根”的论证不充分。

Project registry 拥有配置和源 identity；global registry 是 host-local route
projection。Canonical coordination 已有 typed TS transaction owner、独立的
provider incarnation 和 receipt。`coordination/authority_source.ts` 与
`authority_source_capture.py` 见证 registry facts，且明确不构成 registry/provider
之间的 transaction；digest recheck 不能冒充 retirement serialization。

`orphaned_goal_state.py` 发现 current/legacy candidate 并阻止 guided bootstrap；
`state_backup.py` 拥有 backup/verification。Bootstrap update、Goal deletion、
session、channel 和 quota 仍是不同 caller，alias/revision binding 不证明
lifetime continuity。本文所述 instance activation 和 orphan mutation command
在该基线尚未交付。

## 5. 提议架构

### 5.1 Ownership 与 authority

放置决策：既有 built-in control-plane Goal/project lifecycle，不新增 public
capability id、provider id 或 extension distribution。File、SQLite、PostgreSQL
保留各自 provider contract 和 qualification gate。

只有 source authority 可以分配、发布 `goal_instance_id`。Prepared resolution
可持久预留一个 ID，但直到 lifecycle commit 才具备执行效力。Global registry
只复制。纯 match、lifecycle transition 归最近的 typed TS Goal boundary，
通过一次粗粒度 transaction 消费 source facts。Python 适配 CLI、codec、文件、
backup、host effect，执行 typed plan，不复制状态规则。

本地 registry 继续拥有源 identity。未来 R6 service profile 由经过认证的
authority service 准入版本化 authorization projection 和 instance。远端 worker
不能打开笔记本路径、从本地副本分配身份，或把 global projection 当 authority。
该路径完成资格化前，instance enforcement 明确只覆盖 local profile。

任何 binding、path、timestamp、content hash 都不得推断身份。匹配后仍必须执行
既有 grant、claim、lease、quota、stop 和 intent 检查。

### 5.2 Goal identity 模型

`goal_instance_id` 是 opaque、immutable、public-safe 的非凭证字段。规范格式
为 `ginst_` 加 32 个小写十六进制字符，包含 128 个随机 bit。

Typed contract 区分 `GoalRef { goal_id, goal_instance_id }` 与
`LegacyGoalRef { goal_id }`。`BindingMatch` 是穷尽结果，包含 `current`、
`legacy_read_only`、`missing_goal_instance_id`、`goal_instance_mismatch`、
`goal_not_registered`、`resolution_in_progress`、`invalid`。这些是 kernel
决策；生成／解码得到的 Python representation 只作 wire adapter，不另实现 match。

Instance-aware Goal 记录包含：

```json
{
  "id": "release-2026",
  "goal_instance_id": "ginst_6ff38d6d143d4b72a6ff894b95f067c1",
  "status": "active",
  "repo": "/project",
  "state_file": ".codex/goals/release-2026/ACTIVE_GOAL_STATE.md"
}
```

上面的绝对路径只作说明，并非公开证据。持久路径继续遵守现有隐私和可移植性规则。

每个持久 attachment 保存：

```json
{
  "goal_id": "release-2026",
  "goal_instance_id": "ginst_6ff38d6d143d4b72a6ff894b95f067c1"
}
```

匹配矩阵必须穷尽：

| Registry 状态 | Binding 状态 | 只读检查 | 执行或修改 |
| --- | --- | ---: | ---: |
| 精确 instance | 完全相同的 instance | 允许 | 在 commit fence 和既有 grants 下允许 |
| 精确 instance | 缺失 instance | 带 finding 允许 | 拒绝 |
| 精确 instance | 不同 instance | 带 finding 允许 | 拒绝 |
| Legacy Goal | Legacy binding | 带 finding 允许 | 已激活 registry 中拒绝 |
| Legacy Goal | Instance binding | 带 finding 允许 | 拒绝 |
| Goal 缺失 | 任意 binding | 作为历史记录允许 | 拒绝 |
| Resolution source safety 未完成 | 任意 binding | 带 finding 允许 | 拒绝普通执行；仅允许精确 recovery |
| 记录非法 | 任意 | fail closed | 拒绝 |

Observation 和未激活项目保留 legacy execution，但不能声称 ABA 保证。已激活项目
没有 `legacy_compatible` execution 分支。精确匹配也必须同时满足 active
lifecycle state 和既有 grants。

### 5.3 身份独立性

以下 revision domain 相互独立：

| 事件或身份 | 是否轮换 `goal_instance_id` | 原因 |
| --- | ---: | --- |
| 终局退休后重新创建 | 是 | 开始新的 Goal 生命周期 |
| 创建 Goal 的 orphan `adopt` 或 `migrate` | 是 | 建立新的 authority |
| 强制 bootstrap 或配置更新 | 否 | 修改同一生命周期内的配置 |
| Provider 重连或 provider revision | 否 | 修改 attachment 或 backend generation |
| Authority revision 或 migration receipt | 否 | 版本化 authority state，而非 Goal 存在性 |
| Claim 或 Lease epoch | 否 | 协调同一生命周期内的工作 |
| 新 Turn 或 session | 否 | 在同一生命周期中创建执行 lineage |
| Binding revision | 否 | 版本化单个 owner 的 attachment |
| 全局 registry 同步 | 否 | 复制源拥有的 identity |
| 回滚到 orphan bytes | 永不恢复 | 恢复的 bytes 没有执行 authority |

`--force` 不能替换 instance ID。导入到新的项目 authority 时必须生成新 identity，
不得复制由另一个 registry 拥有的 identity。

恢复 authority 到替代 deployment 必须遵守既有 incarnation/restore contract；
复制 registry backup 不能复活 stale writer 或创建第二个可写 authority。

### 5.4 Activation format 与 mixed-writer 门禁

Identity 以整个 project registry 激活，不按单 Goal 激活。复用 M0 codec 和
I/O census，不另建 loader。M0 v1 format/protocol 只代表 codec。提议 activation
使用不兼容的 `loopx_project_registry_envelope_v2` header schema 和
`minimum_writer_protocol: goal_instance_v2`，保留 `[header, payload]` 与
payload digest。最终标识须在发布前通过 M2 compatibility matrix。

只修改 writer string 不够：M0 reader 可以解码未知 writer protocol，而状态写入
可能根本不改 registry。新 schema 必须拒绝不懂 identity 的 **read-to-effect**
路径和 registry write。旧 global-object route、已加载进程还需单独排除；header
不能追溯约束缓存中的旧代码。

```console
loopx activate-goal-instance-identity --project . --format json
loopx activate-goal-instance-identity --project . --execute \
  --plan-revision sha256:31ab... --confirm-registry-path .loopx/registry.json
```

Activation 必须：

1. 经既有 lifecycle/host owner 停止新 admission，持久 quiesce registry 中全部
   Goal；drain 或明确 reconcile 已准入 effect。盘点 direct CLI、attached/managed
   host、global route、automation 和 provider writer。失联或无法说明的 writer
   阻塞 activation。
2. 验证定向 backup、host protocol support 和精确 preview digest。Capability
   advertisement 是待验证证据，不是写权限。
3. Prepare durable activation journal 和每个 Goal 的一个 ID，在 registry
   transaction 下原子发布不兼容 envelope。Retry 复用 ID；普通 M0/M1 不生成 ID。
4. 通过显式、审阅过的迁移／重连绑定 owner state。缺 stamp 不得按 alias 继承
   authority。必要 commit fence 和 readback 完成后才重新准入；投影待交付不能
   授权或重复 effect。

发布前可 abort 回 legacy object；发布后只能向前修复，禁止解包给旧 writer。
新项目可 opt into 完整 profile；安装和 codec 可用不等于 activation。全局关闭
legacy execution 是独立 release 决策。

验证 object-only、codec-only M0/M1、enforcement 三类版本，包括 direct source、
global route、warm process、state write、resume、quota、delivery。拒绝后必须
business state 和 receipt 不变，不能只检查 registry bytes。本地 drain 加拒绝
unsupported source decode 必须覆盖受支持版本；若旧路径同时绕过两者，保持
activation 不可用，直到既有 owner 能机械排除。相同 OS principal 故意篡改文件
不属于 mixed-version 安全模型。

### 5.5 Binding owner 与提交 fence

| 既有 owner | Instance-bound state 与关键边界 |
| --- | --- |
| Registry／attached host／Chat | Session/thread、activation binding；route selection、resume、executor admission |
| Turn／scheduler／quota | Work selection、lineage、settlement、debit/void、replay identity |
| Todo／coordination／lease | Head/provider binding、work mutation、claim/renew/reclaim、receipt |
| Handoff／inbox／outbox | Request、receiver adoption、accepted artifact、result delivery、historical readback |
| Goal Channel／Lark／heartbeat | Connection、迟到 inbound/outbound event、wake admission |
| Pi／OpenCode／其他第一方 host | Host action、execution binding、quota/state writeback |

维护一个 private 穷尽 owner inventory：稳定 locator、revision、content digest、
observed typed reference、cleanup support。排序后的 inventory 和 owner revision
进入 plan。Registry access 复用 M0 I/O census；binding/effect coverage 必须另外
盘点，writer 数量不构成证明。

Resolve route → 校验 source/授权 projection → 比较精确 reference 与 lifecycle →
既有 permission → operation admission → 在 owner fence 下提交。最后一刻读取
仍有 check/use race：A 通过检查后暂停，删除／重建发布 B，随后 A 写入复用路径或
消费 quota。因此必须明确 linearization boundary：

- 本地 retirement 与每笔短 protected commit 共用 instance admission guard，
  固定 lock order；不跨 model call、network request 或 user validation 持有
  registry lock。
- Canonical transaction 将原 source witness 和 instance 传给 TS owner，在
  lifecycle guard 下于 commit 边界检查当前 instance。既有 CAS、lease fence
  仍必要；witness hash 或 Goal ID 不等于该 guard。
- Retirement 先关闭 admission，drain 已准入 commit，并经既有 journal reconcile
  pending external effect，再发布 retirement。不确定的外部 effect 阻止宣告
  retirement/reuse 完成；无 fencing 的外部 provider 不能被追溯取消。
- Shared service 必须在服务端串行化 lifetime/mutation admission，保留 tenant、
  actor、restore incarnation 校验。本地文件锁不满足 R6，也不声称跨 ledger 原子性。

即使物理路径仍用 alias，operation/replay key、canonical state selection、late
result 都必须按 instance 隔离。旧 receipt 仍可作为 A 的历史读取，但不能授予 B
lease、满足 B acceptance 或给 B 扣费；不得静默重标历史 receipt。B 的 provider
state 必须新建，或经既有审阅过的 lifecycle 显式导入。A 在校验后暂停、同名重建后
恢复的反例，必须不产生任何 B-side write、delivery、debit 或 ownership change。

### 5.6 Orphan resolution 命令

除非带 `--execute`，命令只做 dry run。每个发现的 candidate 都必须有明确
disposition：

```console
loopx resolve-orphaned-goal-state \
  --project . \
  --goal-id release-2026 \
  --disposition .codex/goals/release-2026/ACTIVE_GOAL_STATE.md=adopt \
  --disposition .claude/goals/release-2026/ACTIVE_GOAL_STATE.md=archive \
  --objective "Ship the release" \
  --domain engineering \
  --role owner \
  --format json
```

- `adopt` 在 canonical 位置保留选中 state content，以新 Goal instance 注册；
  不恢复旧 claim、grant 或 execution binding。
- `migrate` 通过 canonical state-path helper 移动一个选中 candidate，在相同
  human Goal ID 下注册新 instance。
- `archive` 把 candidate 移出所有 active Goal root。
- `delete` 只有在定向 backup 验证后才删除 candidate。

最多一个 candidate 可以是 `adopt` 或 `migrate`；其余 candidate 必须是
`archive` 或 `delete`。只有 archive/delete 的计划让 Goal 保持缺失。系统不提供
隐式 winner、内容合并、任意 destination path 或重命名 target Goal。

首个 resolution profile 仅覆盖项目本地 legacy file state。若 candidate 路由到
已晋升 canonical/provider state，必须 fail closed 并指向既有 authority
migration/import route；移动 Markdown 不能接管 File/SQLite/PostgreSQL head。
后续 provider 支持必须保持 source selection、incarnation、历史 receipt 和 D1–D3。

Preview 在计算 digest 前验证：

- project 和 registry 位于声明的 authority boundary 内；
- 每个 candidate 是 allowed Goal root 内可读的 regular file；
- candidate 和 parent 都不是 symlink；
- resolved path 不 escape、alias 或互相重复；
- 请求的 Goal 不存在于项目 registry；
- 同一 Goal 不存在其他 resolution；
- 所有 candidate tree digest 和 binding-owner revision 稳定；
- migration destination 由 canonical path helper 选择；
- 需要创建 Goal 时，其 specification 完整。

执行必须绑定 preview 和人工意图：

```console
loopx resolve-orphaned-goal-state \
  --project . \
  --goal-id release-2026 \
  --disposition .codex/goals/release-2026/ACTIVE_GOAL_STATE.md=adopt \
  --disposition .claude/goals/release-2026/ACTIVE_GOAL_STATE.md=archive \
  --objective "Ship the release" \
  --domain engineering \
  --role owner \
  --execute \
  --plan-revision sha256:82e3... \
  --confirm-goal-id release-2026
```

### 5.7 Resolution transaction 与 journal

Typed lifecycle 包含 `prepared`、`applying`、`source_committed`、`complete`、
`rollback_prepared`、`rolled_back`。Journal 位于
`.loopx/lifecycle/orphaned-goal-state/<goal-id>/<plan-revision>.json`，是恢复
metadata，不是另一份 registry。TS 拥有 transition/allowed effect；既有
Python/host adapter 执行并返回 observation。

1. 按固定顺序获取 lifecycle/registry/affected-owner guard。已完成 exact replay
   返回历史 receipt，不重新激活 Goal。
2. Prepare 前重建并比较完整 registry、candidate-tree、inventory、creation-spec、
   plan digest；不匹配则无 effect。
3. 验证定向 backup，所有 plan 在首次 move/delete 前持久 prepare，**包括只做
   archive/delete** 的 plan。仅创建 Goal 时预留 ID；记录 candidate preimage、
   planned postimage 和 owner operation key。
4. 在 guard 下应用 disposition；effect 前写 intent，crash-before-ack 后核对真实
   pre/post state。已匹配 post-state 不重复删除；两者均不匹配时不得猜测。
5. 选中内容已 canonical、竞争 candidate 已失活后，只发布一次新 Goal。Prepared
   plan 从已记录 phase 恢复，不要求部分成功后仍符合原 preview 的 pre-mutation digest。
6. 收集必要 local invalidation receipt、验证 source 和 safety fence，再完成 lifecycle。
   有其他 legacy Goal 时先完成 registry-wide activation；其他 Goal 不属于本次
   resolution guard 范围。
7. 通过既有 retry owner 投递 global projection 和 operator result。投影失败是
   typed `pending_sync`，不是另一份 authority，也不能重复 source commit。

Source safety 成立前，journal 阻止该 Goal 的普通 activation/mutation，即使已发布
Goal record；只有精确 resolution/resume 可以执行其声明的恢复 effect。Source commit
及必要 invalidation 完成后，投影 lag 仍需可见，但不能无限阻塞无关 Goal 或已有
source 授权的本地工作。Event-driven wake 只作提示，source check 决定 admission。

Receipt 保存 plan/resolution/terminal-journal digest、candidate pre/post digest、
精确 source Goal reference、backup manifest verification、各 owner observation、
revision、operation、readback、local removal receipt，以及 typed projection/manual
cleanup finding；排除 private raw content。关闭新 lifecycle admission 时仍保留恢复。

### 5.8 逻辑 revocation 与 legacy cleanup

精确 instance mismatch 加 commit fence 构成安全边界；外部物理删除是 hygiene。
Typed cleanup selector 区分：

- `instance`：精确 retired `(goal_id, goal_instance_id)`；
- `legacy_observation`：精确 owner locator + observed revision + content digest，
  仅在 lifecycle guard 证明 Goal absent/inactive 时准入。

Legacy orphan 没有可凭空生成的 retired instance。Owner 支持条件删除时，在其 guard
下重新校验 observation，只删除该 record；revision 改变则 stop/replan。Provider
无法证明精确删除时，保留 inert record 并返回 `manual_cleanup_required`。不得按
Goal ID 批量删除，也不能先给 legacy binding stamp 身份以便删除。

必要 local logical invalidation receipt 阻塞 source completion；可选物理清理和
external manual cleanup 使用独立 retry state。另一个 lifetime 创建的新 binding
不能匹配旧 legacy selector。分别验证 legacy/stamped orphan 和 revision race。

### 5.9 Rollback

Rollback 同样采用 preview-first，并绑定 digest：

```console
loopx rollback-orphaned-goal-resolution \
  --project . \
  --goal-id release-2026 \
  --resolution-id ores_... \
  --format json

loopx rollback-orphaned-goal-resolution \
  --project . \
  --goal-id release-2026 \
  --resolution-id ores_... \
  --execute \
  --plan-revision sha256:... \
  --confirm-goal-id release-2026
```

Rollback 只能把已验证 bytes 恢复到 orphaned、fenced 状态，不能恢复 retired
Goal instance ID 或 binding authority。在任何 resolution 后 Goal write、Todo
mutation、quota spend、session/channel 创建、automation 安装或 binding revision
发生后，rollback 必须拒绝。Rollback 自身也须在首次修改前写 journal，并使用同一 retirement/commit guard，
避免资格检查与新业务写入竞争。成功 rollback 通过 lifecycle owner 删除新 Goal，
恢复 candidate bytes，验证 digest，并让 diagnose 重新以
`orphaned_goal_state` 报告 unhealthy。

若 rollback 已不合法，恢复只能向前执行：完成 journal，通过普通 lifecycle
退休新 Goal，再创建新的 resolution plan。

### 5.10 有界重构与交付 owner

| 既有边界 | 改动与删除要求 |
| --- | --- |
| TS Goal lifecycle + `coordination/authority_source.ts` | 一套 typed reference/match/admission/recovery 规则；扩展完整 transaction，删除 host 重复判断。Witness 是证据，不是跨 store 锁。 |
| `control_plane/projects/registry_codec.py` | 扩展已交付 codec/protocol check 和 transaction；保留 v1 compatibility，不赋予 v2 enforcement。 |
| `bootstrap.py`／Goal deletion | 消费 lifecycle transaction；配置更新保留 identity，退休／重建受 fence 保护。 |
| `orphaned_goal_state.py`／`state_backup.py` | 保留 discovery/backup effect；薄 resolution adapter 驱动 typed plan，不新增 Python 状态机。 |
| 既有 binding／delivery owner | 持久 exact ref 和 commit fence；复用 owner-local journal/idempotency，不新增中央 provider cleanup framework。 |
| Diagnosis／CLI／Chat／frontend／Lark | 带 audience filtering 呈现同一 lifecycle result/recovery action，不新增 detector 或独立 repair state。 |

重构前刻画普通 bootstrap/update/deletion 和 default-off 路径。以真实 consumer
交付完整本地 retirement/recreation 路径；不增加未使用 Goal abstraction、逐字段
RPC 或 scheduler。更大的 cross-host/provider import 保留给 R6/D1–D3，不阻塞
本地 legacy-file recovery 的有用交付。

## 6. 替代方案与设计选择

### Generation counter

`goal_id` 下的 counter 需要在删除后永久保留 tombstone，或依赖第二个全局
allocator。源 registry 生成随机 identity 不需要这两项，也不要求 ID 有序。

### 在目录路径中加入 instance ID

Instance-segment path 可以隔离文件，却不能标识 session、channel、automation
或 quota call；同时会迫使所有面向人的路径 consumer 参与大范围迁移。记录级
identity 无需引入该无关布局变更即可修复已报告 ABA 问题。

### 删除 Goal 时清理所有 binding

跨 provider 删除不具备原子性，还会迫使 Goal lifecycle 理解所有 storage
实现。精确匹配先让 stale record 失活；owner 本地清理只改善卫生，不承担
authority。

### 给当前 registry object 增加 capability 字段

已发布的旧 loader 接受未知 object 字段和 schema value，因此仍可能静默改写
instance-aware registry，不满足 mixed-writer 不变量。

### 新 registry 路径加 redirect object

已发布的旧二进制可能忽略 redirect，继续写旧路径并形成 split authority。同一路径
上的不兼容根形状会产生确定性 decode failure。

### 自动选择或合并 candidate

路径优先级、修改时间和内容相等都不能证明 authority。多个 candidate 可能代表
分叉历史，必须由操作者逐个分类。

### 一个模块同时负责 discovery 和 mutation

把小型只读 fence 与 backup、journaling、cleanup、rollback 和 registry
publication 合并，会让稳定 detector 混入大型事务。独立 resolution owner
保留深的 plan/apply 接口，又不会把 fence 模块变成 lifecycle service。

## 7. 安全、隐私与兼容性

Identity 和 digest 是 public-safe metadata，不是凭证。Backup、candidate 内容、
session payload、本地绝对路径、provider handle 和原始状态属于 private runtime
data，不得进入公开 receipt、log、fixture 或文档。

Format gate 与 effect-owner fence 共同排除受支持的 mixed writer。Activation
必须通过 object-only、codec-only、enforcement 三类版本的 source/global/warm-process
negative test。只使用隔离 synthetic state，不在活动 Goal 上验证拒绝。

未激活 legacy 项目保留原行为。已激活项目允许 legacy inspect、backup、migration
preview，但拒绝 unstamped execution。Observation finding 不能声称 ABA 保障。
Protocol 名称必须代表完整 enforcement，而非 decoder 可用性。

已激活 registry 不可降级。Unsupported source schema/protocol 必须在业务 effect
前失败，包括不修改 registry bytes 的 effect。恢复只能使用兼容版本，不解包 payload。

在已激活 profile 内，源不可用、digest mismatch、envelope 畸形、instance 缺失、
source safety 未完成或 owner inventory 歧义都必须 fail closed。全局投影不可用可能延迟可见性，
但不能授权执行。

## 8. 迁移与回滚

迁移有两个范围：

1. **显式 opt-in 的新项目。** 创建 activation envelope，并在首次 Goal 的 committed registry
   transaction 中生成 identity。
2. **已有项目。** Preview registry-wide activation，quiesce 每个 Goal，备份
   registry 和本地 binding，为所有当前 Goal 生成新 instance，原子写入严格
   envelope，并要求所有 attachment 显式重连。

不得自动认可任何现有 attachment。系统没有证据证明一条 unstamped record
属于当前生命周期，而不是更早的同名 Goal。

已验证不兼容 envelope write 是 activation cutover point；必要 owner fence 和
readback 完成前保持 admission 关闭：

- 写入前，abort 让 legacy object 保持 authority。
- 写入后，旧二进制和 unstamped binding 均不受支持并 fail closed。
- 后续步骤失败时，当前代码恢复 activation journal。
- 恢复 legacy execution 不属于 rollback。

Orphan archive/delete 不创建 Goal，因此无需 identity activation。Orphan
adopt/migrate 必须使用已激活 registry、新建已激活 registry，或由同一 plan
准入有 journal 的 registry-wide activation。

Resolution prepare 前 abort 不改变任何状态。Prepare 后必须重试同一个 plan。
独立 rollback 命令仅在没有任何 resolution 后业务写入时合法，并只能恢复
orphaned、non-executable bytes。

## 9. 验证与验收

| 声明 | 测试或证据 | 必须结果 | 边界／排除项 |
| --- | --- | --- | --- |
| 同名重建被 fence | 删除 A，保留全部 attachment，以相同 alias 创建 B，遍历所有 owner | 所有旧路径在 effect 或 quota 前拒绝 | 不要求 provider 物理删除 |
| 更新保留生命周期 | 强制更新配置、provider 重连、Lease 续期、新 Turn | Registry 和新记录使用相同 instance ID | 不要求 binding revision 不变 |
| 已激活范围内 unstamped execution 只读 | 执行 status、backup、migration preview、resume、delivery、write、spend | 读取带 finding 成功；拒绝普通业务 effect；显式迁移仍可用 | 未激活 legacy 行为不变 |
| 旧 writer 被机械拒绝 | Object-only、codec-only、enforcement 版本；source/global/warm-process | 业务 effect 前拒绝，state/receipt 不变 | Decoder rejection 本身不充分 |
| Strict codec 穷尽 | 静态 writer inventory 加 Python/TS conformance | 不存在项目 registry 直接写绕过 | 测试 helper 可使用隔离 fixture |
| 源 authority 胜过全局投影 | 制造 stale 全局条目，调用所有 executable route | 源 mismatch 拒绝 | 全局 routing availability 独立 |
| Preview 纯只读 | Preview 1、2、4 个 candidate | 不写 file、registry、binding 或 journal | 允许读取 filesystem metadata |
| Plan 绑定完整状态 | Preview 后修改 candidate、owner revision、registry 或 Goal spec | Apply 在 backup/mutation 前拒绝 | 无 |
| 路径校验 fail closed | Symlink、escape、重复路径、非 regular 或不可读 candidate | Preview/apply 拒绝 | 合法 destination 由 canonical helper 拥有 |
| Crash retry 幂等 | 所有 plan；每个 effect 和 journal ack 前后 crash | 需要时只有一个 prepared ID；精确 reconcile，无重复 effect | 只做 archive/delete 也有 journal |
| 已发布但不安全仍被 fence | 发布后、必要 invalidation/source readback 前 crash | Activation/mutation/quota 拒绝；精确 recovery 可用 | Source 完成后仅投影 lag 单列 pending-sync |
| Cleanup 失败仍安全 | Stamped/legacy binding；legacy revision 改变；local invalidation 失败 | 只精确 cleanup；external record inert；必要 local invalidation 重试 | 不生成虚假 retired identity |
| Receipt 完整 | 对照 owner 表和全部 readback | 每个 owner/candidate/registry/global/backup/fence row 都存在 | 排除原始 private 内容 |
| 破坏前已有 backup | 在 backup 验证前后注入失败 | Proof 前不破坏；已验证 restore 可用 | Archive-only 仍做定向 backup |
| Rollback 被 fence | 分别在 resolution 后写入前后 rollback | 早期恢复 orphan fence；晚期拒绝 | 永不恢复旧 authority |
| 诊断持续 unhealthy | 构造 orphan、legacy、mismatch、stale global 和 incomplete journal | Typed finding 和精确 next action | Diagnose 不做 repair |
| Commit race 被 fence | A match 后暂停，退休／重建 B，再恢复 A；rollback 与 write 竞争 | 无 B-side write/debit/delivery/ownership change | 真实本地入口；涉及 canonical path 时用真实隔离 provider |
| 历史保持历史 | 不同 lifetime 复用 alias/operation key；迟到 accepted result | A receipt/result 不能授权或结算 B | A 历史 readback 仍可用 |
| Default-off parity | 同输入 off/on 对比 immutable pre-change baseline | Off 保持 schema/guidance/state/effect；on 拒绝 stale ref | 不迁移活动项目 |
| Provider 边界真实 | Native authority candidate 交给 file-only resolution | 不按文件接管；指明既有 import route | 不声称 D1–D3/R6 资格 |
| 产品恢复完整 | CLI、packaged frontend、已资格化 Lark | Preview→确认→crash/resume→source 与原入口 readback | 未测 transport 明确不具备资格 |

实现还必须运行现有 bootstrap、registry、deletion、session、host、Goal Channel、
heartbeat、quota、backup、docs-governance 和 public-safety suite。跳过任何 owner
row 都不算验收。

## 10. 运维契约

稳定 finding 包括：

- `orphaned_goal_state`
- `orphan_resolution_in_progress`
- `legacy_goal_identity`
- `goal_instance_id_missing`
- `goal_instance_mismatch`
- `goal_not_registered`
- `stale_global_goal_instance`
- `unsupported_registry_writer_protocol`
- `binding_owner_inventory_changed`
- `manual_cleanup_required`

每个 rejection 都应在 public-safe 前提下标识 source registry、Goal alias、
expected/observed instance、owner、retryability 和一个 lifecycle next action，
不得暴露 raw state、credential、本地 session 内容或 provider secret。

Status 和 diagnose 只读。它们可以检查 legacy registry、historical binding、
strict envelope 和 incomplete journal，不能 stamp identity、修复全局投影、选择
candidate 或完成 cleanup。

CLI、packaged frontend、Lark 消费同一 preview/confirm/resume result。展示选中及
竞争 candidate、execution block、backup proof、pending sync 和精确 next action。
Stale session 应解释旧 lifetime，并提供显式授权的新 binding，不得静默 resume。
完成结果返回原始会话。复用已有 settings/action editor；CLI-first 切片须标出
未资格化 companion surface，不能声称完整产品闭环。本 RFC 自身不改 UI。

Lifecycle 按现有 backup retention policy 为每次 resolution 保留一个 verified
backup 和一个 terminal receipt。Incomplete journal 不得自动 GC。本地 cleanup
retry 必须幂等。External manual cleanup 只作提示，因为 logical revocation
已经阻止执行。

## 11. 规范性交付计划

| 里程碑 | Outcome 与前置 | 决定性退出／回滚 |
| --- | --- | --- |
| M0：已交付 codec 前置 | 复用 #4917 codec/transaction/I/O census；无 lifetime enforcement | v1 read/write 已交付，不证明 old-writer exclusion；不重建或删除已在使用的 codec。 |
| M1：owner 刻画与 typed rule | 盘点 binding/effect producer/consumer、刻画基线；只实现选定 lifecycle 使用的规则 | Default-off parity、typed negative matrix、source/global/warm-process compatibility census；不 mint、不自动 activate。 |
| M2：opt-in 本地 lifetime transaction | 不兼容 activation、retirement/recreation guard、选定本地 owner enforcement 作为完整可用切片 | 真实 CLI local ABA/concurrency/crash/replay；全部受支持 effect owner 有 fence 前保留 M2/M3 activation hold。Cutover 前 abort，之后 forward repair。 |
| M3：受支持 owner 资格化 | 完成已激活 profile 的 host、quota、Todo/lease、handoff、channel、automation 路径 | 全 inventory row，包括 late result/unsupported binary；已激活项目无 legacy fallback，未激活行为不变。 |
| M4：orphan recovery | 复用 fence/diagnosis/path/backup owner；journaled file-state resolution 与精确 legacy cleanup | Preview、destructive/retry/rollback negative、原入口 readback；archive/delete 可提前交付但不创建身份，native-provider adoption 仍阻塞。 |
| M5：迁移与产品验收 | M2–M4、多 Goal quiescence/reconnection、packaged frontend、已资格化 Lark | 2–3 worker、一条产物依赖、中断 A、重建 B、迟到 A return；无关 Goal 继续，B 独立验收；无重复 protected effect。 |

M0 是前置 checkpoint，不是下一个尚未开始的任务。下一切片承担 M1 compatibility/
consumer 刻画及 M2 本地 lifecycle seam，不能把加字段当作 ABA outcome。M2/M3
描述实现顺序，不授权激活部分有 fence 的系统。R2/R3 消费完成的本地切片；R6
service adoption、D1–D3 provider promotion 保留各自验收。不授权付费 cohort/soak。

## 12. 未决事项与 hold

1. **受支持 package/profile matrix：** release/host owner 在 M2 activation 前固定
   object-only、codec-only、enforcement 版本及排除证据。v2 名称是提案，support
   必须包含语义。
2. **精确 commit guard：** M2 source-session 候选在 project-registry transaction
   外使用 alias-scoped cross-runtime lock。M3 仍须在 activation 前证明
   external-effect drain contract；digest recheck 不能解除该 hold。
3. **Canonical destination/provider import：** 复用当前 path owner，跟随 #4915
   但不预设已合并。Provider-state adoption 要有独立审阅的 import contract；首个
   file-only 切片拒绝它。
4. **Service profile：** R6 决定 authenticated identity projection 与服务端串行化；
   不要求远端访问本地路径，不复制 writable registry。
5. **Retention/default rollout：** 既有 backup/receipt owner 决定 retention；
   incomplete journal 不自动 GC。全局 enforcement/default activation 需独立、
   已披露的 migration/release 决策。

---

## 附录 A：执行记录（非规范性）

### 2026-09-23 — 设计综合

- **基线：** `23edcb19c70394480e3a9ebe8a960f5a320c5342`
- **已交付：** 修订 RFC 提案；M0 codec 前置已单独交付。
- **证据：** 已检查当前 registry、bootstrap、deletion、binding、orphan fence、
  backup、全局投影和文档契约。
- **已知缺口：** 尚无 instance enforcement、activation 或 resolution fixture。
- **对规范设计的影响：** 对齐 roadmap/TS/shared authority；分开 codec compatibility
  与 enforcement，明确 commit fence、legacy cleanup。

### 2026-09-23：M2 source-session 实现候选

- **基线：** `cbbdd837f65c8ba28161115cc4ce39093bfa2951`
- **候选实现：** 仅支持新项目的 `source_session_v1` profile、精确 bind/unbind
  receipt、带 journal 的 A-to-B recreation，以及只读精确 resolution。
- **证据：** 真实 CLI 测试覆盖 ABA 顺序、publication 前重试、publication 后前向
  修复、replacement 原字节回滚、operation ID 冲突、容量拒绝和满容量 replay。
- **剩余 hold：** 所有结果均为 `execution_authority: false`。M3 必须先完成其余
  effect owner 资格化，才能开放既有项目 activation 或 global routing。

### 2026-09-26：M3 attached-host Chat 候选

- **基线：** `9849366c6`。
- **候选实现：** attached Chat Session 绑定当前精确 GoalRef；enqueue、resume
  与新 claim 在 M2 lifetime guard 内重新校验。迟到结果只有在持久化 claim
  admission 仍与历史 Session 一致时才能完成。
- **兼容性：** 非 source profile 保留既有 writer、lookup、broker payload 与
  序列化字节；客户端不提交 `goal_instance_id`。
- **剩余 hold：** 本切片只资格化 `attached_host_chat_session`。Managed
  provider 启动和下游 host effect 仍受独立 `first_party_host_runtime` 行与
  M3 总 activation hold 阻断。

## 附录 B：决策日志

| 日期 | 决策 | Owner／批准 | 替代方案 | 变更的规范章节 |
| --- | --- | --- | --- | --- |
| 2026-09-23 | 提议 source-owned 随机 Goal instance identity 与 strict registry envelope | 提案；等待 maintainer 批准 | Counter、path identity、附加 capability marker | 初始 RFC |
| 2026-09-23 | 基于当前 main 修订 compatibility、typed lifecycle ownership、commit fence、legacy cleanup | 设计提案；实现 hold 见第 12 节 | Decoder-only exclusion、重复 Python 状态机、猜测 legacy identity | 第 3–5、7–12 节 |

## 附录 C：证据登记

| 证据 ID | 声明 | 基线／环境 | Artifact 或命令 | 结果 | 隐私／有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | M0 读写 v1，但不 enforce lifetime | 指定基线／#4917 | Registry codec 与 bootstrap 检查 | observed | Codec compatibility 不等于 identity admission |
| E2 | 已交付 fence 阻止 orphan guided bootstrap，但不执行 resolution | 指定基线 | Issue #4801 和 PR #4808 | observed | 公开 issue 和代码范围 |
| E3 | 全局条目是 source projection | 指定基线 | 检查 `loopx/global_registry.py` | observed | 静态当前代码事实 |
| E4 | Activation 排除所有受支持旧 effect 路径 | 未来 M1–M3 fixture | Object-only/codec-only/enforcement matrix | unverified | Activation 前需覆盖 source/global/warm-process |
| E5 | 每个持久 binding owner 验证精确 identity | 未来 M1-M3 fixture | Owner inventory conformance suite | unverified | Enforcement 前必须完成 |
| E6 | Resolution 可在 crash 后安全恢复 | 未来 M4 fixture | Fault-injection 和 restore matrix | unverified | 破坏性使用前必须完成 |

## 附录 D：已拒绝或被替代的方案

- 只有 warning 的 writer capability marker 无法限制不理解 marker 的旧代码。
- Redirect object 让旧路径继续可写，允许 split authority。
- 全局 counter 会让 projection 成为 allocator，或要求永久 tombstone。
- 自动 binding upgrade 只能按 alias 猜测历史，因此禁止。
- 把全 provider cleanup 当作 commit precondition 会让 availability 耦合所有
  integration，且仍不能证明 external deletion。
- Runtime-directory migration 无法解决非文件 binding，并且超出 issue #4801。

## 附录 E：事故与评审经验

- 当前状态 healthy 不能证明它与更早的同名 Goal 连续。
- 若旧代码会忽略 compatibility check，advisory check 就不是 writer fence。
- Crash safety 要求所有 plan 在首次 mutation 前写 journal，需要时预留 identity，
  并 reconcile 丢失 acknowledgement 的 effect。
- Cleanup 与 authorization 是两个问题：stale record 可以保留用于 audit，同时
  由精确 identity matching 保持 inert。
