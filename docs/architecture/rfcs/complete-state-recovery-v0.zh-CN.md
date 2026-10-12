# RFC：完整状态恢复与受控重新激活（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** 提案；备份创建和仅配置的隔离恢复已存在，完整状态验证与重新激活尚不存在
- **作者 / 负责人：** 控制面状态、可靠性及受影响领域的维护者
- **创建：** 2026-10-08
- **最近规范修订：** 2026-10-08
- **实现基线：** `82d1b837479dd5eb64b581bb0ca36d8c1ff1f0cc`
- **相关契约：** [Issue #5940](https://github.com/loopx-project/loopx/issues/5940)、[Goal 实例身份与孤儿恢复](goal-instance-identity-and-orphan-recovery-v0.zh-CN.md)、[共享权威](shared-goal-authority-state-provider-v0.zh-CN.md)、[可组合恢复验证](composable-state-machines-recovery-verification-v0.zh-CN.md)、[Effect Interpreter](agent-loop-effect-interpreter-v0.zh-CN.md)、[配置备份](../../reference/configuration-backup.md)
- **语言镜像：** [English](complete-state-recovery-v0.md)

## 文档地图与维护契约

第 1–10 节定义持久设计与验收契约，第 11 节是规范性交付计划，第 12 节记录不削弱
第一实现切片、因而可以暂时未决的事项。附录只包含非规范性的历史与证据。

中英文文档互为语义镜像。规范变更必须在同一个拉取请求中同时更新两者。接受本
RFC 只授权有界实现切片，不授权真实恢复、provider 晋升、凭据转移或默认值变更。

## 1. 决策摘要

### 首个支持的用户结果

首个有界用户承诺是：一个本地 Goal 的来源工作区不可用或不再可信后，用户可以恢复
该 Goal。取得资格的 profile 是运行在合格 POSIX host 上的 packaged local LoopX
环境，使用文件／SQLite authority，且 operator 持有 backup 与 external manifest。

在 M1，operator 可以验证该备份，并从既有 Settings/Capability Center 入口查看结果。
该视图区分：已经找回并保持可读的 durable Goal outcome 与进度、仅可作为历史读取的
状态，以及未知或不可恢复的工作。每个阻塞项都标明其既有 decision owner 和唯一
next action。字节验证通过绝不表示 Goal 可以执行。

在 M3，operator 可以选择已验证备份、预览影响、确认受控 adoption 到同 profile 的
替代环境、处理 owner hold，并回到同一产品入口查看结果。既有 Goal、authority、
session 和 effect owner 准入目标后，该 Goal 可以继续未完成工作并产出一个独立验收
通过的结果。Owner checkpoint 之后的工作，包括易失的进行中工作，可以报告为丢失
或未知；恢复流程绝不猜测性补回它们。

必要人工动作是提供 backup 与 manifest、选择新的空目标、按 owner 要求重新认证或
绑定 credential、处理可见 hold，并确认 adoption。当原工作区与 authority 仍可用
时，日常重启或 session 接续仍归既有 session 与 execution owner。本 RFC 负责的是
该普通路径不可用或不安全时从备份进行灾难恢复。该 profile 的 RPO/RTO 目标、基线
与停止条件必须在 M3 演练前冻结；本 RFC 不预先编造这些数值。

本 RFC 作出七项决策：

1. 一个 `backup-state` 归档是一个不可变的**验证单元**，称为恢复集。它不是跨
   owner 事务，也不是激活单元。
2. 每次恢复都从新的**惰性恢复工作区**开始。提取、验证和审计绝不注册 runtime、
   启动 Host、获取 lease、派发 effect、安装 automation 或选择 authority。
3. 捕获声明在线、静止或崩溃一致性三种 profile 之一。每个组件记录真实 owner
   边界以及来源身份／修订；未声明的边界不具备资格。
4. 恢复协调器只拥有与 digest 绑定的计划和 owner receipt。现有 registry、Goal
   生命周期、authority store、session、quota、delivery、automation 和 effect
   owner 保留各自决策与提交 fence。
5. 恢复出的 claim、lease、session、timer 和 provider revision 不授予当前
   authority。受控重新激活必须铸造或轮换每个既有 owner 所要求的目标身份，并
   fence 旧 writer。
6. 待定的外部 effect 和 delivery 通过其原始 operation identity 与 receipt 完成
   reconcile。结果未知会阻止重放，并可能阻止激活；复制出的 queue 或 journal
   字节绝不授权一次全新发送。
7. 第一版实现对 live state 只读：`backup-state verify` 可以提取到 operator 选定的
   新目录并生成隔离审计，但每个结果都必须声明
   `execution_authority_granted=false`。

当前备份创建行为保持不变。本 RFC 中没有任何命令可以把字节完整性转换为执行权限。

## 2. 问题与动机

`backup-state --execute` 可以在一个归档中捕获 runtime root、项目本地 Goal 状态、
registry 发现的项目与 Goal route、SQLite snapshot、配置、automation 和 skill。
这些路径具有不同的 owner、修订域、凭据和提交点。归档 manifest 会报告复制了
什么，但不能证明这些事实共同构成某一个一致的 runtime 状态。

例如，Todo commit 可能已经存在，而其 projection delivery 仍在等待；Host session
可能引用了一个在其 journal 被复制后才替换的 lease；外部 effect 可能已提交，但
本地 receipt 尚未 durable；恢复的 PostgreSQL snapshot 也可能包含旧进程仍然知道
的 store identity。即使每个文件 checksum 都正确，把这些字节直接提取到 runtime
root 仍可能复活过期 authority 或重复执行 effect。

仅配置恢复已经避免了这类错误：它恢复到隔离 checkpoint，并明确不授予 live
authority。完整状态恢复需要同样的默认行为，外加跨 owner inventory、fencing、
reconciliation 和 readback。任何单独 owner 都不能安全推断其他 owner 的状态，而
新建一个单体 restore owner 会成为第二个 authority。

### 不变量

1. 验证证明字节和已声明结构，不证明语义连续性或执行权限。
2. 恢复绝不以已注册、活跃或非空的 runtime 目录为目标。
3. 每个组件恰好只有一个既有决策 owner。恢复协调器只能通过该 owner 经评审的
   API 修改其状态。
4. 恢复集不可变且内容寻址。计划绑定其归档、外部 manifest、提取后的 inventory、
   目标事实和 owner revision。
5. 跨 owner 偏差保持显式。不能从时间戳或路径接近程度猜测缺失的 revision、
   cursor、dependency 或 capture profile。
6. 恢复出的 provider identity、Goal reference、lease epoch、session generation
   或 operation receipt 始终是历史事实，直到其 owner 在目标的当前 fence 下接纳。
7. 受控重新激活后，旧来源进程和目标进程不能同时保有受支持的写 authority。
8. 精确历史 receipt 保持可读。恢复绝不把它们重新标为目标 receipt，也不改变
   其 operation identity。
9. 结果未知的外部 effect 绝不以新 operation 重试。Reconciliation 必须收敛为
   committed、absent 或可见 hold。
10. 局部 adoption 不能静默回退到提取文件或 legacy writer。任一必要 owner 失败
    都保持 admission 关闭。
11. Projection 或 notification 失败不能回滚已提交 owner，也不能授权重复其业务
    effect。
12. 恢复与审计输出从公共产物中排除 secret、原始私有 payload 和本地绝对路径。

## 3. 范围与非目标

### 范围内

- 完整 `backup-state` 归档的物理与逻辑验证。
- 恢复集 manifest 和隔离审计，其中标明组件 owner、identity、revision、
  dependency 与 consistency profile。
- 安全提取到新的惰性工作区。
- 在线、静止和崩溃一致性捕获语义。
- 通过既有 owner reconcile Goal reference、authority store、session、claim、
  lease、Turn journal、effect、inbox、outbox、automation、skill 和 projection。
- 在不复制执行 authority 的前提下重新绑定 provider 与 credential。
- 目标和旧 writer fencing、局部失败、幂等重试、回滚限制及独立 readback。
- 在同一个 provider-neutral 逻辑契约后支持文件／SQLite、服务自有 PostgreSQL
  和未来 provider profile。
- 首个取得端到端资格的 profile：合格 POSIX host 上 packaged local LoopX 环境中的
  一个本地 Goal，并使用文件／SQLite authority。

### 非目标

- 通用 tar 提取命令，或原地覆盖 runtime root。
- 跨越所有 LoopX 状态 owner 的单一事务。
- 第二个 Goal、Todo、配置、provider、session、quota 或 effect authority。
- 自动转移 credential、登录、验证 secret 或轮换 key。
- 盲目重放外部 effect、delivery、timer 或 automation。
- 导入任意 Host cache、模型 context 或第三方 provider state。
- 在本 RFC 拉取请求中实现 live restore 或自动 activation。
- 在没有自身实测验收证据时，宣称某个 platform、filesystem、provider、RPO 或
  RTO 已具备资格。
- 把多 Goal 恢复、服务 provider、跨机器传输或无人值守 activation 纳入首个端到端
  profile 的资格范围。

### 与既有恢复契约的关系

本 RFC 组合既有 owner，而不替代它们：

| 既有契约 | 本 RFC 的边界 |
| --- | --- |
| [Goal 实例身份](goal-instance-identity-and-orphan-recovery-v0.zh-CN.md) | 决定恢复的 Goal 保持历史、以新 lifetime 导入，或仅在排他 replacement fencing 后继续。本恢复流程不能自行铸造或复用 Goal instance。 |
| [共享权威](shared-goal-authority-state-provider-v0.zh-CN.md) | 拥有 authority export/import、operation receipt、provider lineage、store-incarnation 轮换、writer fencing 和 source selection。提取出的 authority database 不是已选择 authority。 |
| [可组合恢复](composable-state-machines-recovery-verification-v0.zh-CN.md) | 拥有有界跨领域恢复验证与独立 oracle。本 RFC 提供其要执行的 backup/restore 生命周期。 |
| [Effect Interpreter](agent-loop-effect-interpreter-v0.zh-CN.md) | 拥有 effect identity、settlement、未知结果 readback 和 replay 决策。恢复流程只向其路由原始证据。 |
| [配置备份](../../reference/configuration-backup.md) | 保持配置处于隔离 checkpoint。Adoption 继续通过 machine 与 Goal configuration owner 完成。 |

## 4. 当前系统契约

在指定基线上，`loopx/state_backup.py` 会发现 runtime root、项目 `.loopx` 状态、
Host 专属 Goal 目录、registry 声明的项目与 Goal route、可选 automation 以及可选
`loopx-*` skill。它把 `loopx_state_backup_v0` 写成私有 tar 归档及外部 JSON
manifest，并内嵌第二份 manifest 和经验证的 `configuration-backup.json`。

SQLite member 通过既有 effect runtime 获得在线 snapshot。系统记录 snapshot 的
digest 和大小，并排除 WAL/SHM sidecar。其他文件和目录可能在来源仍活跃时遍历。
因此，该命令只具备组件本地 SQLite snapshot 语义和 best-effort 文件捕获，不具备
跨 owner point-in-time 保证。

`loopx backup-state` 支持 plan 和 create。它不能验证已有归档、安全物化归档、
分类状态 owner、reconcile reference 或重新激活 runtime。当前测试用 Python tar
API 提取，验证 SQLite integrity 与内容，并检查发布／权限行为；它们没有建立
restore 契约。

配置备份另行支持 export、verify 和 isolated restore。其 checkpoint 是惰性的：
它不会创建 live registry、provider selection、writer fence、lease、Host session、
grant 或 timer。Canonical authority export 同样保留自身 lineage 与 receipt，
而不会把自己选为 live authority。这些都是组件契约，不是完整状态恢复。

当前完整归档可能包含私有路径、捕获 root 中已有的 identity 与 credential，以及
第三方状态。它在 POSIX 上仅 owner 可读，但未加密。Checksum 验证后，
`privacy_certified=false` 仍保持 false。

## 5. 提议架构

### 归属与权威

恢复协调器是控制面 workflow，不是状态 provider。它只拥有以下事实：

- 恢复集与目标 digest；
- 组件／dependency inventory；
- verification 与 reconciliation finding；
- 仅用于自身幂等编排的 phase journal；
- 既有 owner 返回的不可变 receipt。

它不能决定 Goal identity、canonical authority、lease validity、session resume、
quota settlement、effect replay、delivery、credential validity 或 automation
admission。每个 action 都以 recovery-scoped idempotency key 和精确 expected
revision 调用既有 owner。成功由 owner readback 证明，而不是 coordinator memory。

恢复集归档是验证单元。受控 adoption 使用更小的 owner-defined unit，通常是一个
目标 runtime 配置、一个 Goal lifetime 与 authority lineage，或一个 Host/session
binding。选定单元必须包含必要 dependency closure。这样可以防止跨多个项目的归档
意外变成全局 activation transaction。

### 状态模型与 schema

`loopx_state_backup_v0` 保持可读。未来的 additive manifest revision 必须为每个
组件描述：

| 字段 | 含义 |
| --- | --- |
| `component_id` | 一个恢复集内的稳定标识；不是 live owner identity |
| `owner_kind` 与 `logical_scope` | 既有 owner，以及 machine/project/Goal/session/effect scope |
| `archive_members` | 组件覆盖的精确规范化归档路径 |
| `required` | 所声明恢复集 profile 缺少它时是否不完整 |
| `schema_version` 与 `producer_version` | Decoder 与兼容性边界 |
| `capture_profile` | `online`、`quiescent` 或 `crash_consistent` |
| `source_identity` | Owner 定义的 Goal、provider、store、session 或 execution lineage |
| `source_revision` 与 `cursor` | 可用时由 owner 定义的 snapshot/readback 位置 |
| `content_digest` | Canonical 组件字节或 owner export 的 digest |
| `dependency_ids` | 审计或 adoption 前所需的组件 |
| `verification_profile` | 具名 owner verifier 及 qualification version |

私有 source locator 可以存在于私有外部 manifest。Public-safe receipt 使用
component ID 和经脱敏的 owner/scope label，绝不包含绝对路径或原始 payload。

第一版实现不修改 backup producer，而是生成 `loopx_state_recovery_audit_v0`：

```json
{
  "schema_version": "loopx_state_recovery_audit_v0",
  "recovery_id": "recovery-content-address",
  "backup": {
    "schema_version": "loopx_state_backup_v0",
    "archive_sha256": "sha256",
    "external_manifest_sha256": "sha256"
  },
  "workspace": {
    "isolation": "inert",
    "registered": false
  },
  "components": [],
  "findings": [],
  "activation_eligible": false,
  "execution_authority_granted": false
}
```

对于 v0 归档，缺失的 owner revision 与 capture profile 记为
`legacy_manifest_incomplete`，不得伪造。审计仍可以证明归档完整性、安全提取、
SQLite integrity、配置验证及可识别的 owner-local export。

编排生命周期为：

`planned -> verified -> restored_inert -> audited -> adoption_prepared ->
applying -> complete`

任何验证后的 phase 都可以因 typed finding 和唯一 next action 进入 `held`。
`abandoned` 只在第一次 live-owner mutation 前合法。发生 live-owner mutation 后，
取消必须变成 owner-specific compensation 或 forward repair，不能简单删除 journal。

M1 verify/audit 切片终止在 `audited` 或 `held`。两种状态都不授予 execution
authority。

Packaged M1 界面通过既有 Settings/Capability Center 入口呈现该 audit，而不新增
顶层 workflow。每个发现的资产显示为以下一种：已找回且可读、仅历史可读，或未知／
不可恢复。界面同时显示负责的既有 owner 和唯一 next action。有效 digest、SQLite
check 或 configuration check 可以使资产可读，但都不能使其可执行。

### 捕获一致性 profile

Profile 描述证据，不是市场等级：

| Profile | 必要来源条件 | 保证 | 显式限制 |
| --- | --- | --- | --- |
| `online` | Runtime 可以继续活跃；每个可变必要组件使用 owner snapshot/export 并记录其 revision interval | 组件本地原子 snapshot 及完整 skew inventory | 没有全局时点；仅普通目录遍历不具备资格 |
| `quiescent` | 新 admission 关闭；受支持 writer 已 drain 或 fence；待定 effect 到达已记录状态；所有 owner snapshot 完成后才重新开放 | 必要组件针对同一个声明的 quiescence epoch 捕获，并检查 dependency revision | 外部系统不回滚；未知 effect 仍是 hold |
| `crash_consistent` | 来源已停止或失败；捕获前没有 cleanup 修改来源 | 同一个声明 failure observation 下的精确 durable 字节及 owner journal/replay input | 丢失的内存工作不在 RPO 内；每个 owner 必须在 adoption 前证明 crash recovery |

恢复集 manifest 记录 capture start/end、请求的 profile、每组件实际达成的 profile
以及 downgrade finding。集合级 profile 不得强于最弱的必要组件。Warning 不能静默
升级 profile。

在每个必要可变组件拥有 owner snapshot 或已声明 immutable boundary 之前，当前
`backup-state` 归类为 legacy best-effort online capture。SQLite member 可以独立
报告其更强的 snapshot 事实。

### 验证与惰性恢复生命周期

第一版命令面为：

```console
loopx backup-state verify \
  --archive /operator-owned/loopx-state-example.tar.gz \
  --manifest /operator-owned/loopx-state-example.manifest.json \
  --destination /operator-owned/new-recovery-workspace

loopx backup-state verify \
  --archive /operator-owned/loopx-state-example.tar.gz \
  --manifest /operator-owned/loopx-state-example.manifest.json \
  --destination /operator-owned/new-recovery-workspace \
  --execute
```

不带 `--execute` 时，命令验证 input 并返回 plan。带 `--execute` 时，它只能创建新的
惰性目标和审计文件，不执行 live-state mutation。现有
`loopx backup-state [options]` plan/create 语法保持有效；`verify` 是可选 action，
而不是对当前 flag 的重新解释。

验证顺序如下：

1. 要求常规 archive 与 external manifest，验证 archive digest、manifest schema、
   backup identity 及 embedded/external manifest 一致性。
2. 预检 destination 不存在、物理 parent、symlink ancestor、仅 owner 权限、可用
   空间、entry count、logical byte budget 及 expansion ratio。
3. 拒绝绝对路径、`..`、重复规范化名称、合格平台上的大小写或 Unicode collision、
   device/FIFO/socket member、hard-link 歧义以及逃逸工作区的 link。
4. 提取时不保留来源 UID/GID、setuid/setgid bit、ACL authority 或 process state。
   内部 symlink 保持惰性并被报告。
5. 重新计算 member 与 component digest。对副本运行 SQLite integrity check、
   configuration verification 及注册的 owner-local verifier。
6. 构建 owner/dependency graph，并报告 missing、extra、ambiguous、unknown-schema
   及 cross-owner reference finding。
7. 写入仅 owner 可读的不可变 audit receipt。绝不加载 plugin、执行恢复出的 code、
   导入 credential、注册 workspace 或启动 Host。

Verifier 失败不会替换先前成功的 audit。临时文件保留在目标的私有 staging area，
失败后在安全时清理。

### Reconciliation 与受控重新激活

Reconciliation 按 dependency 而非 tar 顺序执行：

| 顺序 | Owner 边界 | 必要处置 |
| ---: | --- | --- |
| 1 | Destination/runtime 生命周期 | 证明新的空目标或排他 fenced replacement；铸造 recovery operation identity |
| 2 | Credential 与外部 dependency | 只报告 reference；通过当前 owner 重新认证或绑定；绝不自动采用复制出的 secret authority |
| 3 | Project registry 与 Goal 生命周期 | 把每个 Goal 分类为历史、新 lifetime import，或由 Goal owner 准入的 exact replacement continuity |
| 4 | Canonical authority provider | 验证 export/import 与 receipt；绑定选定 source；写入前轮换或铸造 provider/store incarnation |
| 5 | Claim、lease、quota 与 scheduler state | 保留历史；退役或使恢复出的 execution generation 失效；通过当前 admission 重新获取 |
| 6 | Host session 与 Turn journal | 保留 readback；仅在 effect reconciliation 及新的当前 execution fence 后恢复精确已准入 lineage |
| 7 | Effect journal、inbox 与 outbox | Reconcile 原始 operation receipt；只通过 owner idempotency 重新投递；未知结果保持 held |
| 8 | Automation、skill 与可选 code | 以 disabled data 恢复；检查 compatibility，并通过当前 owner 显式 install/enable |
| 9 | Derived projection 与 UI state | 从选定 canonical source 重建；复制出的 stale projection 绝不决定 authority |

Owner disposition 只能是 `retain_historical`、`import_inert`、`rebind`、
`reconcile`、`rebuild` 或 `discard`。只有 owner 可以把 disposition 映射为 effect。
未知 owner kind 或不受支持版本保持惰性，并阻断任何把它声明为 required 的 profile。

外部 effect reconciliation 使用原始 operation identity：

- `committed`：保留 receipt，并且只继续 owner 的 post-commit delivery path；
- `absent`：如果当前 authority 与 retry contract 允许，owner 可以重发同一个已准入
  operation；
- `unknown`：查询 provider 或要求 operator 解决；不得 dispatch；
- `rejected` 或 `superseded`：保留历史，绝不 replay。

受控重新激活是独立的后续命令与评审边界。它要求 digest-bound adoption plan、
未变化的目标 revision、适用时 source/destination 均 quiescent、owner
compatibility 且没有 required unresolved finding。第一次 live mutation 前，中止只
留下惰性工作区。每次 mutation 都持久化 intent，使用由
recovery/component/action digest 派生的 idempotency key，并记录独立 readback。

旧 writer 排除规则取决于 profile。它可能要求 Goal retirement 或 continuity
fencing、provider-incarnation rotation、authority-source writer fence、
session/execution generation 变更、lease epoch、service tenant 与 principal 检查，
以及撤销旧 credential。停止 PID、重命名目录或复制 store identity 都不充分。

Recovery receipt 始终报告 `execution_authority_granted=false`。所有必要 adoption
与 fencing 步骤通过后，既有 runtime/executor admission owner 可以另行发出其正常
grant。协调器记录这些 receipt，但不能铸造或扩大它们。

### Provider 与扩展契约

每个 provider profile 都必须实现：

- 不可变 export 或 snapshot 及 canonical digest；
- 具备 version/capacity limit 的离线 verify；
- 导入到 non-authoritative destination；
- 使 stale writer 失效的 lineage/incarnation 处理；
- 精确 operation-receipt 与 cursor readback；
- fenced source selection 及后来写入的 rollback/export；
- typed missing、mismatch、unknown、ambiguous、unsupported 和 capacity failure。

文件／SQLite profile 使用其既有 authority export/import 与 writer-fence 契约。
服务自有 PostgreSQL 使用 administrative restore，并且 admission 前必须轮换 store
identity；Agent 永远不获得数据库访问权。NoKV 或其他 provider 在独立证明同一
逻辑契约前不受支持。通用 filesystem copy 不能代替 provider profile。

### 实现归属与复用证据

M3 实现必须在代码准入前记录 decision-owner matrix。对于每个 Goal identity、
authority、lease/session 和 effect disposition，该矩阵必须标明：

- 保持 authoritative 的既有 TypeScript decision owner 及其经评审 API；
- 为既有 v0 archive 与历史 receipt 保留的向后兼容 reader；
- 可以删除的重复决策规则或已迁移 caller；
- 实际调用该 owner 并回读结果的 CLI 或 packaged-product consumer。

实现进展以这些证据衡量：独立作出同一决策的位置减少；真实 consumer 到 owner 的
trace 缩短；caller 定位与验证成本降低；positive、rejection、retry 和
stale-generation 场景下的行为保持不变。Enum、RPC、文件或新增类型的数量只是
inventory，不是进展证据。

## 6. 替代方案与设计选择

### 原始提取

拒绝。它只证明字节能够物化，却让 stale state 看起来可运行；它没有 owner、
identity、effect 或 fence 语义。

### 单体恢复事务

拒绝。LoopX owner 与外部 provider 不共享一个 commit protocol。中央事务会复制
authority、隐藏局部 outcome，而且无法回滚外部 effect。

### 仅靠逐 owner 人工 runbook

拒绝作为主契约。Owner tool 仍具有决定权，但未版本化 checklist 不能绑定一个
archive、destination、dependency graph 或 retry identity。协调器提供这些事实，
但不接管 owner 决策。

### 把 VM 或 filesystem snapshot 作为恢复契约

拒绝。这些 snapshot 可以提高捕获一致性，但不能 reconcile provider identity、
Goal lifetime、external effect、credential 或 delayed delivery。它们可以是某一
已声明 profile 下的一种 capture mechanism。

### 全部以 disabled 恢复，再一次性全部 enable

拒绝。Disabled data 是正确初始状态，但一个 global enable 不能证明每个 owner 的
revision 与 fence。Adoption 必须遵循 dependency 顺序，并在必要歧义处停止。

## 7. 安全、隐私与兼容性

Backup 与 recovery workspace 默认私有。它们可能包含 source code、private path、
identity、prompt、session body、credential 和 provider handle。命令只打印有界
count、component ID、digest、typed finding 和相对 workspace reference；不会打印
member content、secret、private provider 的 raw error，或公共证据中的 absolute
path。

M1 实现不执行 network call，只有显式选中的 owner-local verification 可例外，且
必须被记录为 offline。它不加载恢复出的 plugin、skill、Python module、Node
package、shell profile、database extension 或 executable。Archive name 与
metadata 都是不可信输入。

必须采用仅 owner 可读的 POSIX mode。Native Windows 与 filesystem ACL 行为在
测试前不具备资格。大小写不敏感或 Unicode normalization filesystem 必须在提取前
检查 collision。不受支持的 hard link、special file、sparse expansion、超大 member
或过多 entry 都在发布前失败。

既有 v0 archive 保持可验证，但 assurance 较低。既有 backup create flag 和 output
path 不变。安全时未知字段保留在私有证据中；未知 required semantic fail closed。
旧 binary 不会获得 restore path，也不能通过 fallback discovery 打开 inert
workspace。

Archive 中的 credential 仍是私有历史字节。恢复不宣称它们当前有效，不把它们
复制到 live credential store，也不使用它们探测 provider。跨机器转移、静态加密、
撤销及目标认证仍是显式 operator/security 决策。

## 8. 迁移与回滚

M1 是 additive：验证 v0 archive，并在新的 inert workspace 旁生成 audit。它不改变
producer schema 或 live state。删除该 workspace 即回滚本次操作。

后续 manifest revision 通过 dual-read/new-write 引入。Verifier 读取 v0 时生成
`legacy_manifest_incomplete` finding，并完整读取新 revision。只有 fixture、size、
privacy 和 downgrade 测试通过后，backup producer 才写新 revision。它绝不重写
已有 archive，也不静默升级所声明的 capture profile。

受控 adoption 要求：

1. 重新验证不可变 recovery set 与 audit。
2. 重建并绑定 destination/owner inventory 与 plan digest。
3. 按 profile 要求 quiesce 或 fence source 与 destination writer。
4. 第一次 live mutation 前 prepare 每个 required owner action。
5. 按 dependency 顺序 apply，同时持久化 intent 并精确 readback。
6. 开放普通 admission 前 reconcile effect 与 delivery。
7. 发布选定 source，之后才重建 derived projection。

第一次 live mutation 前，中止只删除 inert candidate。Adoption 期间以相同 recovery
和 operation identity 重试。已完成 owner 不能通过恢复旧字节来回滚；
compensation 使用该 owner 经评审的 reverse operation。

目标开始普通 live work 后，禁止通用 rollback。返回其他 provider 或 source 需要
一次新的 fenced export/import，且必须包含所有后续写入。若 owner 存在未知 external
outcome，必须 forward reconcile。

## 9. 验证与验收

| 声明 | 测试或证据 | 必要结果 | 边界／排除项 |
| --- | --- | --- | --- |
| Archive 与 manifest 精确绑定 | 修改 archive、external manifest、embedded manifest、backup ID 或 digest | 发布提取前拒绝验证 | 不证明语义一致性 |
| 提取被限制在工作区内 | Absolute/traversal path、escaping link、duplicate/case/Unicode collision、special file、expansion limit | Staging 外零写入；typed rejection；先前 audit 不变 | 明确列出 platform matrix |
| M1 保持惰性 | Verify/extract 时监控 registry、process、provider、network、lease、timer 与 effect | 只有新 workspace/audit 字节；`execution_authority_granted=false` | 不声明 activation |
| M1 audit 在产品内可理解 | 通过 packaged Settings/Capability Center 入口打开 verified 或 held audit | 找回的资产被分类为已恢复／可读、仅历史可读或未知／不可恢复；每个阻塞项都有 owner 和唯一 next action；字节验证不会显示为执行权限 | 只读；不声明 restore 或 activation |
| SQLite 与配置保持有效 | 损坏 snapshot 与配置；有效 WAL-backed fixture | 独立 integrity 与 owner verification；损坏时拒绝 | 其他 owner 需要自己的 verifier |
| 如实表达 v0 不确定性 | 验证缺少 component revision/profile 的当前 archive | 有用的字节审计加 `legacy_manifest_incomplete`；不具备 activation 资格 | 不推断 timestamp consistency |
| Capture profile 真实 | Concurrent writer、quiescence failure、crash journal、required-component downgrade | Achieved profile 不强于证据；必要组件不完整时失败 | 外部系统状态仍在外部 |
| Dependency graph 完整 | Missing/duplicate/cyclic/unknown owner 和 cross-Goal reference | Typed finding 与确定性顺序；required ambiguity 阻断 | 可选历史数据可以保持惰性 |
| Goal lifetime 不会复活 | 恢复删除后用同 alias 重建的 Goal 及 stale binding | 旧 binding 不能写、消费、投递或结算 successor state | 保留历史 readback |
| Provider incarnation 已 fence | Snapshot/restore 时保留旧 live writer；File/SQLite 与真实 PostgreSQL profile | 旧 revision token/writer 拒绝；精确 readback imported head 与 receipt | 每个 provider 单独取得资格 |
| Claim 与 session 保持历史 | 恢复 active lease/session/automation 并尝试 resume | 当前 owner reacquire/rebind 前不执行；stale generation 拒绝 | 不承诺 Host 可恢复性 |
| Effect 不重复 | 在 external commit 与 local receipt 前后崩溃；恢复每个时点 | 同一 operation reconcile 到 committed/absent/unknown；不盲目新 dispatch | Provider readback 可能需要 operator |
| Delivery 可恢复但不是 authority | Source 已提交但缺 outbox ACK、inbox cursor stale、sink 不可用 | Owner 幂等恢复或报告 hold；不重复 source commit | Transport qualification 独立 |
| Partial adoption fail closed | 每个 owner 在 effect 和 acknowledgement 前后失败 | 通过 owner readback 重试收敛；必要失败保持 admission closed | 不声明 global rollback |
| 新 live work 阻止字节回滚 | 完成 adoption，执行新写入，再请求 rollback | 拒绝 generic rollback；要求 fenced forward export/import | Mutation 前 inert workspace 仍可删除 |
| 隐私边界成立 | 含 credential、private path、session content 和 provider error 的 archive | Public output 只含有界脱敏事实 | Private audit 仍私有 |
| M3 端到端恢复一个 Goal | 在首个支持 profile 中先完成一个 Goal 的部分工作并保留未完成任务，在每个关键 owner commit point 注入故障，再通过 packaged journey 恢复 | Operator 选择备份、预览影响、确认、处理 hold 并重试；原 owner 准入目标；未完成任务继续产出独立验收通过且可在原入口读取的结果 | 来源工作区不可用；一个本地 Goal、合格 POSIX host、文件／SQLite authority |
| M3 恢复结果与成本可测量 | 记录捕获 checkpoint、找回及丢失／未知工作、耗时、人工介入、hold 和受保护外部 operation；演练前冻结目标 | 受保护重复 operation 数为零；再次失败或未知结果以可见 hold 显示 owner 与继续条件；将实测 RPO/RTO 与预先冻结的目标比较 | 不能在结果已知后编造目标 |
| M3 复用既有 decision owner | 评审 decision-owner matrix，并通过真实 CLI／产品 consumer 跟踪 positive、rejection、retry 与 stale-generation 场景 | 保留兼容历史 reader；删除已证明冗余的重复决策规则与迁移后 caller；独立 readback 在保持行为的同时减少重复决策及 caller 定位／验证成本 | Enum、RPC、类型与文件数量不证明进展 |

验收记录必须分别列出 passed、failed、skipped 和 untested。Provider 或 platform skip
不算绿色。故障测试使用 disposable synthetic state 与真实受影响存储边界，绝不恢复
活跃用户 runtime。

## 10. 运行契约

稳定 operator 状态为 `verifying`、`restored_inert`、`audit_held`、
`adoption_prepared`、`applying`、`reconciliation_held`、`complete` 和
`abandoned`。它们描述 recovery workflow，而非业务 execution state。

每个状态都暴露：

- recovery ID、backup ID、schema 与 digest；
- 请求和达成的 capture profile；
- 按 owner 分类的 required/optional component count；
- verified、invalid、missing、ambiguous、unsupported 和 unknown count；
- 已恢复／可读、仅历史可读及未知／不可恢复的资产数量，并为每个阻塞项标明负责
  owner；
- 当前 phase、最后一个 durable owner receipt、retryability 与唯一 next action；
- source 与 destination writer fence 是否建立；
- `execution_authority_granted`，对 verification/recovery 始终为 false。

原始本地 path、含私有数据的 member name、credential 和 payload 保留在 private
audit storage。Public diagnostic 使用脱敏 component ID。

Capacity limit 覆盖 archive byte、expanded byte、member count、path length、
component count、graph edge、SQLite verification time 与 audit size。M1 交付前必须
实测并版本化默认值。Limit failure 在 destination 发布前发生，并保持先前证据不变。

Recovery journal 与 owner receipt 至少保留到 restored state 不再保留，并遵循既有
backup/authority retention rule。未完成的 live adoption 绝不自动 garbage collect。
从未 adoption 的 inert workspace 可以在 digest-bound operator confirmation 后删除。

RPO 与 RTO 取决于 profile 和 provider。M1 报告 verification duration 与 data size，
但不作 recovery-time 声明。Alert 区分 corrupt input、unsupported schema、capacity、
missing dependency、fence failure、unknown effect 和 pending delivery。

M3 演练 receipt 还必须报告捕获 checkpoint、找回的工作、丢失或未知工作、耗时、
人工介入及受保护的重复 operation。再次失败或结果未知时保持可见 held 状态，并
显示其 owner、继续条件和唯一 next action。

## 11. 规范性交付计划

| 里程碑 | 交付行为 | 入口门槛 | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| M1：verify 与可读 inert audit | 验证既有 v0 archive、安全提取到新 workspace、SQLite/configuration check、owner/dependency inventory，并在只读 Settings/Capability Center 视图中显示已恢复／可读、仅历史可读及未知／不可恢复的资产及其 owner 和唯一 next action；`execution_authority_granted=false` | 已接受 RFC；冻结 limit 与 audit schema | CLI dry-run/execute、packaged view、恶意 archive 负例、v0 fixture、无 live mutation 证明、docs/public-private check | 删除从未 adoption 的 workspace 与 audit |
| M2：声明 capture profile | Component manifest revision；online/quiescent/crash-consistent 捕获事实与 downgrade finding | M1 加完整 mutable-owner inventory | Qualified platform 上的 concurrent/quiescence/crash fixture；schema size/compatibility 证据 | 继续创建 v0；verifier dual-read |
| M3：首次 packaged local Goal 恢复 | 在首个支持 profile 中选择备份、预览影响、确认、把一个 Goal 恢复到同类 packaged local 环境、处理或重试 hold、取得原 owner 准入、继续未完成工作，并在原入口读取独立验收通过的结果 | M1；相关 M2 profile；合格 POSIX／文件／SQLite profile；Goal、authority、session 与 effect owner 支持；decision-owner matrix；预先冻结的演练目标 | 捕获前先完成部分工作并留下未完成任务；使每个关键 owner commit point 失败；记录找回及丢失／未知工作、耗时、人工介入、可见 hold 的继续条件、保持行为的 owner 复用及零受保护重复 operation；证明旧 writer 拒绝 | 新工作前 owner compensation；之后 fenced forward export/import |
| M4：service/provider profile | 服务自有 PostgreSQL 与单独准入 provider，包含 restore-incarnation、tenant、credential-rebind、capacity 与 availability 证据 | M3 语义契约与 provider-specific operations review | 真实 backup/restore、ambiguous commit、old-service writer、failover 及 receipt/cursor readback | Provider 经评审的 export/source-selection workflow |
| M5：扩展运行资格 | 新增 transport 与 platform、retention policy、定期 drill、实测 RPO/RTO 资格及 release 运行准入 | 至少一个 M3/M4 profile 获目标 release 批准 | Transport/platform 专项 drill、interrupted recovery、human takeover、retention proof、accessibility/audience review 与 release runbook | 禁用 activation 入口；保留 verify/audit 与历史 receipt |

每个 milestone 都独立有用。M1 在不 activation 的情况下，让恢复证据可以从既有
packaged product 中读取。M2 改进未来 backup，但不激活。M3 为一个本地 Goal 完成
首个端到端用户承诺。M4 增加 provider profile；M5 扩展 transport、platform、
retention 与运行资格。后二者都不会推迟基本的 M1/M3 产品旅程，也不会追溯性地让
更早的 profile 变安全。

本 RFC 拉取请求不实现任何上述 runtime milestone。

## 12. 未决事项

1. **M1 capacity 默认值。** Backup/reliability maintainer 必须在 M1 合并前冻结
   archive、expansion、entry、graph 与 verification-time 默认值。建议从当前
   backup-size telemetry 推导，并以显式 override fail closed。这不阻塞 RFC。
2. **精确 replacement continuity。** Goal lifecycle 与 provider owner 必须在 M3
   前决定是否允许任一本地 disaster-recovery profile 保留来源
   `goal_instance_id`。建议铸造新 lifetime，除非旧 authority 可证明已不可逆 fence，
   且既有 lifecycle owner 发出 continuity receipt。
3. **Archive encryption 与 credential retention。** Security/release owner 必须在
   宣传 portable 或 cross-machine recovery 前决策。建议保持 M1 local/private，
   永不自动采用 credential，并要求显式 encrypted export profile，而不是静默改变
   当前 archive。
4. **Windows 与非 POSIX filesystem。** Platform owner 必须在声明 M3/M5 支持前
   验证 ACL、link、case、Unicode、sparse-file 和 atomic-publication 行为。M1 可以
   报告 platform unqualified，并在提取前停止。

---

## 附录 A：执行台账（非规范性）

尚无实现条目。当实测切片交付后，按
[ledger 契约](ledger/README.md)在
`ledger/complete-state-recovery-v0/` 下添加双语条目。

## 附录 B：决策日志

| 日期 | 决策 | Owner／批准 | 替代方案 | 变更的规范章节 |
| --- | --- | --- | --- | --- |
| 2026-10-08 | 提议以惰性优先、由既有 owner 组合的完整状态恢复 | 通过 issue #5940 评审 RFC | 原始提取、单体事务、仅人工 runbook | 初始文档 |

只有 RFC 拉取请求合并后，设计才被接受。上表记录提案历史，不表示先前已有
maintainer approval。

## 附录 C：证据注册表

| 证据 id | 声明 | 基线／环境 | 产物或命令 | 结果 | 隐私／有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | 当前完整备份只有 plan/create，没有完整 restore | `82d1b837479dd5eb64b581bb0ca36d8c1ff1f0cc` | `loopx/state_backup.py`；`loopx/cli_commands/support_control_backup.py` | 已确认 | 仅源码审计 |
| E2 | 当前 restore 测试只证明提取与 SQLite 可读，不证明 reactivation | 同一基线 | `tests/test_state_backup.py` | 已确认 | 合成本地 fixture |
| E3 | 配置恢复被有意设计为隔离且惰性 | 同一基线 | `docs/reference/configuration-backup.md` | 已确认 | 仅 configuration owner |
| E4 | 既有 Goal/provider 契约要求 lifetime 与 incarnation fencing | 同一基线 | Header 中相关 RFC | 已确认 | 不证明完整实现 |

## 附录 D：拒绝或被替代的方案

持久有效的拒绝方案在第 6 节。只有新证据证明某方案既保持第 2 节全部不变量，又不
创建第二个 authority 时，才重新讨论。

## 附录 E：事故与评审经验

- 可读数据库不等于可恢复系统。
- 字节相等不能恢复执行 authority。
- 跨 owner 恢复需要一个绑定计划和多个 owner receipt，而不是一个猜测出来的
  global transaction。
- 未知 external outcome 是正常恢复状态，不是以新 identity 重试的权限。
