# RFC：LoopX 共享控制面权威与可插拔状态 Provider（v0）

- 状态：Draft，正在接受 maintainer review
- 最初提案方：NoKV Lab
- 扩展修订方：LoopX maintainer
- 日期：2026-08-05；修订于 2026-09-13
- 范围：一个 provider-neutral 的 LoopX 权威合同，支持内置 file、可选 NoKV
  与可选 PostgreSQL provider profile，用来补充
  [`host-integration-surface-v0`](../../reference/protocols/host-integration-surface-v0.md)
- 源码基线：LoopX `a0c20f1779d273e7aaa4bd3ea166d145d466e6d5`
- Provider API 基线：NoKV `7bb3ffd6512fd57d9c0f193aa6d9c5b935d77f30`
  （release 0.11.0、Python API 1、Holt 固定为 0.8.6）。Stage 2A 的可执行资格
  验证只接受这份 SDK 合同与本 checkout 的 helper；它仍是候选证据，不是合并门槛
  或 authority promotion
- PostgreSQL 基线：TypeScript Stage 2B candidate 已实现 store contract、
  transaction-local tenant context、forced row-level security 与有界 canonical
  commit admission，且已通过真实 PostgreSQL 16 transaction matrix。后续已交付进程内 service admission、principal/tenant 校验注入点
  与 restore-incarnation rotation；真实认证传输部署、跨主机 runtime caller、
  实测 capacity/retention profile 与 authority promotion 仍未由这些接缝证明
- 语言说明：[英文版](./shared-goal-authority-state-provider-v0.md)与本中文版互为
  语义镜像；两者不一致属于缺陷

## Todo 事件路径退役（2026-09-25）

PR #5054 将原先的事件 writer 捕获方案改为删除这条实验性 Todo 来源。
`events.jsonl` 不再参与投影、叠加、回填或 completion；默认路径及显式别名指向的
非空文件会被明确拒绝使用，原字节保留。空文件或缺失文件允许 Markdown 路径继续。
已晋升 Goal 仍以选定 provider 为准，不受遗留文件影响。Supervisor 改用自己拥有的
本地私有实验日志。

这是通过删除关闭旧来源分支，不是宣称事件 writer 已通过捕获资格验证。
整 Goal 迁移／回退、默认启用仍须满足现有验收；不能据此宣称所有 Python writer
已经退役或 PostgreSQL 已可部署。剩余工作不再新增“补事件捕获”PR。
[决策与退出条件](ledger/shared-goal-authority-state-provider-v0/2026-09-25-todo-event-retirement.zh-CN.md)。

## 旧观测退役检查点（2026-09-24）

[当前交付清单](ledger/shared-goal-authority-state-provider-v0/2026-09-24-observation-retirement.zh-CN.md)
区分已合入、在途 PR 与资格证据。本次删除旧 Python observer 和 TS observation 提交链，
没有将其冒充 executor 存活保护或 event writer 绑定。只保留一个可写 shadow lineage，
仍然默认关闭，且必须显式 bootstrap。

## 管家规模化的持久化路线（2026-09-16）

[统一路线](loopx-overall-roadmap-v0.zh-CN.md) R5 复用本 RFC 的 D1 投影、D2 真实 backend/容量/适用十日 soak、D3 fenced cutover；R6 再把所选 shared profile 接入认证的本地/云端路径。R1–R3 可在已支持 profile 上前进，不等待 PostgreSQL 或整 Goal 默认晋升。

`e94759d88` 已增加 [PostgreSQL service admission](../../reference/postgresql-authority-service-v0.zh-CN.md)，包含认证/tenant 验证注入与 identity rotation；它是进程内服务边界，不是已部署网络服务。后续 P lane 复用它，补 transport、真实身份策略、pool/cancellation/failover 与运维资格，不能从零重造 admission。R7 百 Agent 资格需独立报告注册数、活跃执行器和实测容量；目录、presence、管家计划或一次 source read 不授予 shared authority。既有 source-failure fail-closed、receipt/replay 和 rollback 合同保持。

## 当前实现检查点

长历史 closeout 读取复用仅缓存可重新验证的解析前缀，不新增持久 authority 或放宽
writer fence/D2。剩余三个实现边界不因该运行缺陷修复而机械减一。
[证据与边界](ledger/shared-goal-authority-state-provider-v0/2026-09-24-default-cutover-reconciliation.zh-CN.md#长历史收尾检查本次修复与剩余边界)。

晋升准入现将完整来源绑定到当前 registry witness，并在 TS 持锁范围内重新校验；
保存计划执行保留已审核的 handoff 策略，失败结果如实报告持久 fence。
已提交事务的恢复仍按原 fence/receipt，不要求失去权威的旧来源重新有效。
这关闭 L7/L8 的已复现集成缺口，不重复计算已交付 capture，也不宣称全局默认已切换。
[操作与边界](../../reference/reviewed-coordination-promotion.zh-CN.md)。

终结 caller 现将审核与验证绑定 canonical 来源，历史回执恢复不再依赖私有 argv。
Agent 完成和 Monitor 停止复用普通编辑的当前 head 显示确认。
[调用与恢复合同](../../reference/canonical-terminal-review.zh-CN.md)。此批推进 L2/L5，
未闭合 executor-held fence、D1–D3 或默认 onboarding；剩余工作使用当前核对表，下文历史批次估算不再作为当前剩余 PR 数量。

本地 registry witness 现经同一 TS owner 覆盖 canonical create/claim/update、
Monitor poll 与 terminal mutation；File、SQLite、service-injected PostgreSQL
执行相同来源检查并保留历史回执。
[Witness 合同](../../project-agent-todo-contract.md#canonical-registry-source-witnesses)
区分来源新鲜度、操作身份和 provider CAS。这是乐观来源校验，不是跨资源原子性或
provider 晋升；剩余工作仍归既有 L1–L5 资格化与默认切换 owner。


machine-owned coordination 投影现已有一份由 Python 与 TypeScript 共享的、随包
发布且 provider-neutral 的 record contract。file、NoKV 与 PostgreSQL 候选读取同一
份 canonical Todo read shape；provider-bound 投影遇到未知字段会拒绝，而不是静默
丢失。删除已声明字段必须提供显式兼容性证据并获得 maintainer 批准，即便该字段已经
存储但尚未进入决策读取路径。

新增原生 domain 选项将 Markdown 位置 metadata 分离，但保留归档语义。附录 C 的
Todo domain/projection 决策记录兼容边界与下一步 file-first 资格化计划；现有 v0
capture 与已持久化 head 不会被静默迁移。

这不晋升任何 provider，也不会把整份 active-state Markdown 变成生成文件。默认本地
模式下 Markdown 仍是 canonical；未来显式晋升 shared authority 后，也只有 typed
contract 覆盖的 section 才成为确定性的兼容投影，自由的人类叙事仍在 coordination
head 之外。

### 管家衔接检查点（2026-09-13）

在 `7eb4b7bb1661bd5eff63a8725a33169792d5964b` 源码核验 `AuthorityStore`
接缝及 #4280、#4283、#4287 的事务/展示/journal 收敛。这更新衔接基线，不改变
上方历史 provider 基线或资格证据。SQLite/PostgreSQL 候选路径、各 provider 的
保留条件和 D1–D3 计划仍在；不宣称默认来源切换或共享服务已交付。

[强能力管家与语义交接 RFC](capable-manager-semantic-handoff-v0.zh-CN.md)
消费此 authority；M1 主机工具与 M2 请求账本重构无需等待 provider 晋级。
第 1.4 节明确边界；[TS 执行卡](typescript-control-plane-migration-v0.zh-CN.md)
继续负责业务规则收敛及旧 caller 删除。

### Local provider opening 边界（2026-09-13）

Local runtime 现在通过一个 typed provider handle 解析 File、SQLite 与中期
PostgreSQL profile。没有 selector 时使用明确的 File 默认值；SQLite selector 仍是
opt-in local profile；PostgreSQL selector 只有在 service-owned factory 提供
provider-neutral `AuthorityStore` 时才接受。Selector 不包含凭据或 database client，
并在任何 command 执行前绑定所选 store identity。

这个边界删除了每个 command 各自构造 provider 的重复，并修正了注入 PostgreSQL
store 时对外 source label 的错误标记。已选择 provider 的失败保留 source 并 fail
closed，不能回退到 File 或 Markdown。本次重构为 File/SQLite 默认路径与可切换
PostgreSQL deployment 做准备，不改变 promotion、D2 soak/retention 或 D3 整 Goal
cutover hold。

## 文档地图与维护约定

本文将稳定决策与交付证据分开维护：

- 第 0-10 节定义问题、authority contract、provider 边界、迁移规则与验收条件。
- 第 11.1 节是规范性交付计划；第 11.2 节是非规范性的执行台账，只记录某个日期的
  `main` 已证明什么，不以进展记录静默修改合同；第 11.3 节列出剩余验证与晋升工作。
- 第 12 节记录尚未解决的 owner 决策。实现不得把“拟议答案”当作已经批准。
- 附录 A/B 保留证据与决策历史；附录 C 承载第 12 节提出的 Stage 2C 详细合同。实现
  变化更新台账；架构变化修改规范正文，并显式记录决策。

RFC maturity 与 delivery maturity 相互独立：实验已合入不代表 RFC 已 accepted，带日期
的状态段也不能覆盖规范性不变量。执行台账过长、影响 review 时，可整体迁移到配套的
`*-execution.md`，但不得丢失证据链接。

---

## 0. 一个用来帮助大家理解的例子

例 1：模拟一次真实的机器 -> 人 -> 机器 handoff，为什么浪费时间

比如，我在开发机上的 agent 做完了一个 Rust PR，笔记本上的 agent 负责 review。
整个交接过程按时间顺序是这样的：

- **T0**：开发机 agent 完成修改并创建 PR；
- **T1**：代码通过固定的 head SHA 交付。这一步由 Git 完成，所以代码传递本身
  不是问题；
- **T2**：我手工把 PR、源任务和 review 要求发给笔记本上的 agent，告诉它有新活；
- **T3**：笔记本 agent 接单，但如果响应刚好丢失，我只能从它后来的行为反推它
  是否真的认领成功。

真正浪费时间的是 T2 和 T3：机器已经把工作做完了，下一台机器却还在等人转发；
即使已经接单，也没有一张可以在崩溃后重新取回的凭证。Harness 再快、模型推理再
快，也补不回人离开电脑以后这段空等。

完整需求显然同时包含“怎么通知下一台机器”和“怎么确认它真的接住了”。但第一版
RFC 不应该一口吞掉消息、调度、配额、run history 和所有 LoopX 文件。本版先把最
硬的一段做对：笔记本认领 review todo 时，只有一个端能成功，并且成功后拿到一张
可重放的原始回执。通知和唤醒仍由
[`Agent IM、LoopX 与 OpenViking 协作 v0`](./agent-im-openviking-collaboration-v0.md)
里的 delivery plane 负责。

## 1. 这份 RFC 最后选择了什么

把 authority 想象成唯一的记账员。各端不直接改账，只提交“我要认领这项工作”的
申请。记账员检查目标 todo、身份、命名依赖和 gate；通过就把认领、lease 和回执
一起记下，不通过就明确告诉申请人原因。

这次只给记账员一本很小的账，而不是把 LoopX 的所有文件都搬进远端，也不是建立
三个互相竞争的语义权威：

1. 一个显式启用共享模式的 Goal，只有一份 **canonical coordination aggregate**；
2. 每条成功 operation 的状态变化和原始 receipt 必须在**同一次 CAS**里落账；
3. LoopX authority 负责判断，file、NoKV 或 PostgreSQL provider 只负责可靠持久化
   经过评审的 transaction；
4. run history、status、quota、scheduler、host session 和 evidence body 继续由各自的
   owner 管理，不塞进这本协调账。

NoKV 与 PostgreSQL 是记账员身后的可选 provider。Agent 不会为受控写入直接连接
任一 provider，两者也都不会因此变成 LoopX 的控制面权威。file provider 是第一
个 deterministic/parity backend；在另一次经过评审的权威源切换之前，当前本地
file-based control plane 仍是默认 authority path。

```text
Agent client
    |
    v
LoopX authority API / embedded authority
    |
    v
typed LoopX transactions
    |
    v
provider-neutral store contract
    |------------|-------------|
    v            v             v
   file         NoKV       PostgreSQL
```

无论如何部署，都只能有一个语义写入者。shared service 可以共同托管 LoopX
authority、认证、租户、审计，以及 PostgreSQL 或 NoKV provider；这些是部署问题。
Agent 仍只调用 LoopX API，不能通过直接写表、document 或 file 绕过 authority。

第一个能跑的切片从 `claim_work` 开始：对一个已经存在且可执行的 todo，同时写入
soft claim、lease/fence 和 receipt。Stage 3 在保持模块 coverage-only 的前提下，
把参考合同扩展到 renew、release、reclaim 与 completion。初始切片先回答两件最容易
出事故的事——多人同时抢单时谁赢，以及响应丢失后怎样找回原回执。

这里的争用单元是 `(goal_id, todo_id)` 及该 todo 实际引用的 precondition，不是整个
Goal。两个端抢同一个 todo 时只能一胜；两个端认领同一 Goal 下彼此独立、目标范围
内的 authorization、dependency 和 gate 均未变化的 todo 时，即使底层先后竞争同一
个 aggregate CAS，authority 也应 reload、重验并在内部完成重试，而不是把 provider
head 前进暴露成业务冲突。这与 LoopX 当前公开
[`architecture.md`](../../architecture.md) 中 todo-level 的并发形状保持一致。

举一个最关键的失败序列：operation A 成功拿到 lease `L1`、epoch `7`，但响应丢了；
随后同一 Goal 里另一个独立 todo 的 operation B 又把账本推进了一页；B 没有接管
A 的 todo。A 重启后再次提交同一申请，必须逐字段取回自己当时的原始 receipt。
只告诉 A “这笔账以前记过”以及 B 的当前版本不够，因为没有 `L1`、epoch `7` 和
expiry，A 仍然无法证明自己获准执行过这项工作。

### 1.1 稳定抽象：存储面、语义权威与持续协调

这里最高价值的边界，不是把“LoopX Server”和“NoKV Server”理解成两个彼此竞争的
数据库，而是三个拥有不同正确性合同、可以共同部署但必须分别归属的层次：

| 层次 | 负责什么 | 绝不能负责什么 |
| --- | --- | --- |
| 存储面 | 耐久字节与 artifact、provider generation CAS、snapshot 与 provider recovery | Goal/todo 语义、actor eligibility、lease 或 authority receipt |
| 语义权威 | normalized command、目标范围 precondition、claim、lease epoch、fencing、revision 与原始 receipt | raw artifact body、provider placement 或后台调度状态 |
| 持续协调层 | 通过同一 command contract 完成观察、过期 lease 恢复、wake request 与持续 Supervisor 决策 | 直接改写 head、绕过 provider 边界，或形成第二个 coordination 真相源 |

内置 file backend 是第一个 parity 实现；NoKV 是最初的 shared-store 候选；
PostgreSQL 是规划中的 service-provider profile。三者都必须通过下文的分阶段验证。
LoopX 的增量是语义权威：它把 opaque provider transaction 变成可信的 `claim_work`
结果，并在后续扩展成可恢复的执行权。持续协调层只是 authority 的 client，不是另
一个 writer：Supervisor 观察 projection，通过 typed command 发起 reclaim，或请求
delivery plane 执行 wake；它自己的 scan cursor 与调度状态继续留在 coordination
head 之外。Transport 与 endpoint reachability 仍由 delivery plane 负责；wake 已送达
永远不能证明 authority command 已提交。

同一套 NoKV deployment 可以服务两条刻意分开的路径：

- **coordination path** 只保存 canonical head，并且只有 authority 持有写 credential；
- **artifact path** 可以允许 runtime 在受限 scope 内发布 checkpoint 或 evidence，
  但只有 opaque pointer、digest 与 privacy class 可以进入经 review 的 coordination
  transition。

可恢复工作流先发布 immutable checkpoint 或 evidence artifact，再在相关 coordination
transition 中提交它的 pointer。若第二步之前失败，只会留下由独立 retention 或
collection 处理的无引用 artifact；绝不能让 head 指向从未耐久发布的对象。因此，
共享同一个物理 provider 不会合并两套 ownership contract，也不要求跨领域事务。

物理上允许共同部署。一套 deployment bundle 可以同时启动 NoKV 或 PostgreSQL
provider、LoopX authority endpoint 和 Supervisor worker。这里的“分开”是指 contract 与
credential 分别归属；v0 不要求它们必须拆成不同仓库、进程、binary 或 license。
可信本地验证可以使用 embedded authority。共享部署则需要在线 authority boundary，
确保不可信或过期 client 即使合法持有受限 artifact credential，也无法发布伪造的
coordination head。

### 1.2 能力展望与可部署产品边界

上述分层在不扩大 v0 ledger 的前提下形成了分阶段能力展望：

| 阶段 | 新增能力 | promotion 前必须证明什么 |
| --- | --- | --- |
| 确定性共享协调 | provider-neutral authority、state-plus-receipt CAS、replay 与目标范围 rebase | file-backed conformance 与第 10 节的 P0 检查 |
| 可恢复执行权 | renew、release、过期 lease reclaim、stale-fence rejection，以及带 continuation/evidence pointer 的原子 completion | crash 与 clock-boundary 测试证明被替代 executor 无法 write back |
| 持续协调 | Supervisor observation、reclaim、delivery-plane wake request，以及通过 authority command 编排 remote resume | restart-safe reconciliation，且没有直接 provider write 或内存正确性依赖 |
| 服务级共享控制面 | authenticated principal、tenant-to-goal 隔离、audit、有界容量、observability、service recovery，以及最终的 HA | 显式 deployment 与 migration contract，且不存在 authority bypass 或静默本地 fallback |

这份能力展望不会把 quota accounting、run history、raw evidence、delivery state 或
Supervisor runtime state 搬进 coordination aggregate。这些能力继续由第 3 节所列的
owner 与 ledger 管理，通过 typed command、projection 或 opaque pointer 连接。

给 Apache coordination core 套一层很薄的网络 wrapper，本身不足以形成新的产品边界。
只有当一个独立交付的服务真正拥有网络信任边界，以及 authentication、持续监督、
远程恢复、migration、audit、multi-tenancy 或 HA 等实质 authority/reconciliation
能力时，独立发行才有意义。若项目将来创建单独授权的 server distribution，它的
边界应沿着这一可部署的语义权威与持续协调 surface 切分，而不是沿 NoKV adapter 或
provider-neutral core 切分。本 RFC 既不要求拆仓，也不选择独立 license；当前政策
仍以 [`LoopX Licensing`](../../project/licensing.md) 为准。

### 1.3 按发行边界演进许可证的候选路径

本小节是非规范性说明，不改变任何当前 LoopX 源码或已发布版本的许可证。它记录的
是：若服务级能力未来成为独立交付产品，后续 license RFC 应沿什么边界评估。

| 发行边界 | 交付阶段 | 推荐路径 |
| --- | --- | --- |
| RFC、schema、typed command、receipt、provider-neutral decision、store contract/codec、client SDK、conformance fixture 与示例 | Stage 1-4 | 继续属于 Apache-2.0 open core，让 runtime 与 provider 无需采用 server distribution 也能实现同一协调合同 |
| embedded/local authority、file parity backend，以及 NoKV/PostgreSQL shared-store adapter | Stage 1-4 | 继续使用 Apache-2.0；adapter 不会取得 LoopX 语义权威，底层 provider 继续遵守自身许可证 |
| TEST ONLY shadow、canary、迁移 fixture 与 authority-source promotion proof | Stage 3-4 | 继续作为 Apache-2.0 资格验证材料；证明某个 deployment 正确，并不会自动形成独立授权的产品 |
| 独立版本化、真正拥有认证、租户隔离、审计、耐久回执服务、迁移/promotion、容量治理、恢复与 HA 的 shared-authority server | Stage 5 | 当它已不再只是 Apache core 的薄 wrapper 时，可以通过独立 license RFC 评估 AGPL-3.0 |
| 随该 server distribution 交付的 Persistent Supervisor/reconciliation worker | Stage 5 | 当它真正拥有 restart-safe observation、reclaim、remote-resume 编排与经 authority command 发出的 wake request 时，可以跟随 server 的 AGPL-3.0 候选条款 |
| 独立交付的 managed operations 或 enterprise-only module | 形成服务边界之后 | 若它们不包含在 Apache 或 AGPL distribution 内，且依赖边界明确，可以采用独立商业条款 |

若将来真的创建这些发行物，示意性的 package 边界可以是：

```text
loopx/control_plane/coordination/             Apache-2.0
packages/loopx-authority-client/              Apache-2.0
packages/loopx-authority-provider-*/          Apache-2.0
packages/loopx-shared-authority-server/       AGPL-3.0 candidate
packages/loopx-persistent-supervisor/         AGPL-3.0 candidate，或并入 server
```

这些名称不是 Stage 0 的目录要求。长期不变量是依赖方向：AGPL server distribution
可以消费 Apache contract、core 与 provider；Apache artifact 不得 import、bundle 或
依赖 AGPL server。LoopX adapter 不会重许可 NoKV 或 PostgreSQL server，也不应把
adapter 人为包装成许可证边界。

任何 Stage 5 许可证提案都必须先满足以下门槛，才能改变当前政策：

1. 明确一个可以独立部署、独立版本化的 server artifact；
2. 证明它拥有真实的网络信任与持续协调边界，而不只是转发 Apache core 调用；
3. 保留 Apache client、protocol、embedded mode、provider 与 conformance 路径，
   继续支持互操作采用；
4. 定义 package metadata、嵌套 LICENSE/NOTICE、SPDX 标记与构建检查，阻止跨许可证
   artifact 被意外打包；
5. 在 AGPL 组件接受贡献之前确定 inbound contribution policy，尤其是在未来可能
   需要商业双授权时；
6. 通过明确版本边界向前生效，不缩窄已经由 MIT 或 Apache 发布版授予的权利。

因此，本 RFC 继续让 Stage 1-4 遵循仓库的 Apache-2.0 政策。Stage 5 只是形成新的
决策点，不会自动触发许可证切换。源码不会仅仅因为实现 shared-authority contract
或通过远端 provider canary 就变成 AGPL-3.0。

### 1.4 管家请求与语义交接衔接

[管家/handoff RFC](capable-manager-semantic-handoff-v0.zh-CN.md) 负责用户完整
交互与通用请求/评估/结果关系；本文负责已审阅协调状态与提交证明。
semantic brief、对话、发送尝试、调度游标不进入 v0 coordination head。
相关证据由 artifact owner 发布，按确切版本和披露范围引用。共用物理 provider
不合并这些逻辑状态家族，也不合并其访问/保留契约。

每个 Goal 保持唯一所选 authority source。采纳确实修改 Todo/lease/Vision 状态时，
handoff 才调用对应现有 owner、关联真实回执；无工作效果的咨询或评估不调用它。
独立请求提交不能让工作修改跨 store 原子化：先持久化意图，
恢复原工作回执，再对账 pending 关系。跨 Goal 请求同样保留各 Goal 基线与结果，
不要求分布式 commit，也不为两者编造同一个 Goal-wide provider revision。
传输 ack 不能替代当前 claim 或 fence。

管家读取和接收方写入必须保留晋级后 canonical 空/失败语义，不回退旧 Markdown
或 lease 文件；永久 Markdown 展示保留。handoff RFC 的 A15 fixture 用 legacy
与显式配置的 canonical source 验证此衔接，不重复或替代本文 backend conformance、
保留、恢复、soak 和切换资格。

[共享目标对齐/修订 RFC](shared-goal-alignment-and-governed-amendment-v0.zh-CN.md)
负责意图改变是否合法，以及未来的受控提交；当前 proposal admission 不产生
canonical amendment。管家与 provider 都不成为该 authority。M1–M3 管家工作可与
T1–T3/D1/D2 并行；改变存储/profile/source 或退役完整 legacy Goal writer，仍遵守
适用的 D3/T4 边界。新 handoff 不构成 provider 晋级请求。

分层协调的每一跳都使用相同边界。Parent/request lineage 不是权限令牌，子协调者
也不会获得整队 quota 的新副本。准入、资源预留、claim/lease 和已验收效果仍由
既有 typed owner 负责；provider 会话状态、inbox/queue/steer 回执只是执行或传输
观察。原生 Goal evaluator 不能替这些 owner 宣布 canonical 工作完成。

总纲首个混合执行切片可以由已支持的本地 authority 经 host facade 接收有范围命令，
云端 Agent 负责执行。这不创建新的 authority provider 或独立云端 writer。故障时
保持选定来源和 stale-fence 规则，不能触发本地/云端 authority fallback。需要独立
调度的多 host 仍须认证 service transport 与适用 D1–D3 资格；进程内 admission 代码
不能证明该部署存在。

## 2. 要做的，以及不要做的

**这版要做的**

- 为一个共享 Goal 提供在线、provider-neutral 的 coordination authority；
- 让同一 todo 的并发 claim 只接受一个 owner，同时允许独立 todo 在内部 CAS rebase
  后分别成功；
- 把每个 operation identity 绑定到一份 normalized request digest，换一套语义复用
  同一个 id 时明确拒绝；
- 即使后续 operation 已推进账本，也能找回原始 receipt；
- 定义一个语义权威边界，以及位于其后的 file、NoKV、PostgreSQL provider profile；
- 保持 LoopX 默认本地模式不变；
- 把现有每一类持久状态该怎么接入说清楚。

**这版明确不做的**

- 不给所有 LoopX 状态造一个通用分布式文件系统或数据库；
- 不做离线多写者 merge，也不允许离线创建受控写；
- 不把 message delivery、wake-up、presence 或 Agent IM 协议混进存储合同；
- 不在 Stage 0 参考切片中交付生产级多租户部署、认证、HA 或 provider failover；
- 不把 quota、scheduler、run history、raw evidence、host session 或 extension ledger
  搬进 coordination head；
- 不自动 promotion 当前 event projection 或任一 provider；
- 不允许 Agent 或 extension 绕过 authority 直连存储。

## 3. LoopX 现在有哪些账，各自怎么接入

Owner 提出的关键问题是：LoopX 现有状态分散在不同文件里，不能看到一个持久化
文件就把它塞进同一个 head。比如两台机器各自记录的本地路径都是真的；status 是
算出来的；quota accounting 又是一笔笔追加的账。它们不是同一种写模型。

所以下表先把这些账摊开：今天谁在写、怎么写，以及进入共享模式后应该归到哪里。
它按逻辑状态和字段组组织，而不按固定文件数组织；一个物理文件可以混合多个
owner，新 host 或 extension 也可以新增本地 artifact，而无需修改本 RFC。

下表采用这些接入类别：

- **shared canonical**：只有显式启用 shared mode 后才是权威；
- **derived**：从所命名的 source 重算；永不接受 lifecycle 写入；
- **synchronized ledger**：依据稳定 identity 和自身 append 合同复制或求并集，
  不进入 coordination head；
- **host-local**：只对一个 host、runtime 或 checkout 有效；
- **independent ledger**：保留其 capability 或 accounting owner；
- **excluded body**：跨边界时只允许 redacted digest 或 pointer。

| 逻辑状态 / 当前 surface | 当前 owner 与写模型 | v0 接入策略 |
| --- | --- | --- |
| `ACTIVE_GOAL_STATE.md` 中的 todo lifecycle、soft claim、dependency 与 gate 字段 | Markdown active state 仍是当前真相源。Todo 命令在本地文件锁下整文替换；仅当 event log 已存在时才使用 state-event projection。 | 启用 shared mode 后，只有校验 P0 命令所需的 normalized fields 成为 **shared canonical**。默认模式下 Markdown 继续 canonical；shared mode 下，它对已迁移字段变为本地 projection。私有 prose 排除。 |
| `goals/<goal>/task-leases/` 下的可选 hard task lease | 每个 todo 的 JSON 在 goal-local lock 下原子替换；release 保留 inactive terminal record，避免 re-acquire 时 version 与 `lease_epoch` 发生 ABA。Lease 是否有效还读取 todo status、soft claim、exclusion 和 registered agents。 | 把 claim、lease、terminal generation 与 fence 折入同一个 shared aggregate 和 authority revision。Shared mode 下不保留独立可写 lease file。 |
| 已应用 operation receipt | LoopX 已有 scoped receipt 先例，包括 Turn journal 与 heartbeat receipt，但没有耐久的 shared-goal operation-to-receipt index。 | 新增可重放 receipt index 作为 **shared canonical**；它与 state transition 在同一次 CAS 中提交。 |
| Project registry 的逻辑 identity、agent profile、grant 与 policy | Project registry 是本地配置，以 JSON replacement 写入。这些字段与 route、私有 reference 混在一起。 | Authority 消费显式版本化的紧凑 authorization projection 或 digest。整个 registry 不进入 coordination head，registry mutation 也不是 P0 命令。 |
| Project/global registry route：`source_registry`、repo checkout、state file、runtime root | Global registry 是同步得到的 host-local route projection，并记录本地绝对路径。 | **Host-local**。需要时共享稳定 Goal/repository identity，绝不共享 route path。 |
| `events.jsonl` 中的候选 state event | 当前 migration bridge 仍以 Markdown 为权威。Event append 使用本地锁；多 event append 不是 transaction。 | 只作 read-only shadow/canary input。本 RFC 不 promotion 它，也不用它证明 atomic completion。 |
| Run JSON/Markdown 与 raw evidence body | Run writer 预留本地 artifact name，再写入详细 record。内容和 path 可能是私有的。 | **Excluded body** 或外部 artifact-store object。只有在后续命令需要时，aggregate 才可携带 opaque pointer、digest、privacy class 与精确 code revision。 |
| `runs/index.jsonl` run history | 混合 append index，引用 run artifact，可能包含绝对路径；也携带包括 quota accounting row 在内的多种分类。 | 未来的 **synchronized ledger** 需要稳定 identity、deduplication 与 redaction。它不进入 coordination head。 |
| `rollout-event-log.jsonl` | 混合的 public-safe diagnostic stream。核心 CLI rollout append 刻意 best-effort，发生在主命令之后；普通 todo event 不按受控 operation identity 建键。 | **Derived** observability projection。Rollout append 失败不能使 coordination commit 失效，也不能证明 commit。 |
| Status 与 attention，包括 `status-projection-cache/*.json` | Status 从 registry、active state、run history、lease 等输入派生。可选 cache 是可替换的 host-local snapshot，其 key 包含本地 route input。 | **Derived**；cache 仍是 **host-local**，可随时丢弃。 |
| Agent 运行时能力观察 | Live quota／实际执行的 Turn 记忆有限集合的工具声明；本机不可用观察可覆盖 Goal 继承值。 | **Host-local**，按 runtime、registry、Goal、注册 Agent 隔离；不写入 Goal 配置、共享 grant 或 coordination head。只读规划、显式更正／清除共用同一 typed owner。 |
| Quota policy | 本地 policy 配置在 registry 字段中。 | Head 之外的 configuration input。Receipt 可以引用本次采用的 policy revision，但 coordination provider 不拥有 policy。 |
| Quota accounting（`quota_slot_spent` / `quota_slot_voided`） | 详细 JSON/Markdown 加 `runs/index.jsonl` row 形成 append-style accounting history。当前 row 没有 shared operation identity 或跨 artifact transaction。 | **Independent ledger**。分布式实现需要 idempotent debit/void identity 与独立 retention contract。 |
| Quota enforcement 与 `should-run` decision | 从 policy、todo/status projection、run history、scheduler context 和 actor scope 计算。Heartbeat receipt 是特殊 rollout 用法。 | **Derived decision**。若未来全局 budget 要 gate claim，应签发独立 reservation/grant receipt；head 可引用它，但不能吸收 quota ledger。 |
| Scheduler state、liveness、host backoff 与 RRULE observation | Per-goal、per-agent、per-surface JSON 反映拥有该 scheduler 的 host。 | **Host-local**。不能把两个都有效的 host observation 当作冲突并用一个 global value 覆盖。 |
| Turn journal、`turn-sessions/` 与 Pi `.loopx/pi/` binding | Runtime recovery 与 session binding 为一个 host/session 写入，可能包含本地 path 或 task body。 | **Host-local**。Turn journal 是 receipt 设计先例，不是 shared coordination state。 |
| Supervisor、domain-state 与 extension runtime file | 每个 capability 定义自己的 schema、privacy、append/upsert rule 与 effect receipt。 | 依 capability contract 保持为 **independent ledger** 或 **host-local**。不得通用导入 head。 |

上述分类的源码锚点包括
[`architecture.md`](../../architecture.md)、
[`event-store-migration-bridge-v0`](../../reference/protocols/event-store-migration-bridge-v0.md)、
`loopx/control_plane/work_items/task_lease.py`、
`loopx/cli_rollout.py`、
`loopx/control_plane/runtime/status_projection_cache.py`、
`loopx/control_plane/quota/slot_accounting.py`、
`loopx/global_registry.py`，以及这些 host state：
`loopx/control_plane/scheduler/state.py`、
`loopx/control_plane/turn_driver/codex_cli.py` 和
`loopx/pi_goal_mode/pi-goal-loop-runtime.mjs`。

## 4. 这本协调账里到底放什么

一个 provider key 保存一个 Goal 的 aggregate（head schema v1）。以下形状仅作说明：

```json
{
  "schema_version": "loopx_coordination_head_v1",
  "goal_id": "shared-rust-review",
  "store_binding": "nokv:wb-goals:1f2e3d4c...",
  "authority_revision": 43,
  "coordination": {
    "todos": {
      "todo_review": {
        "todo_revision": 9,
        "status": "open",
        "claimed_by": "laptop-reviewer",
        "eligibility": {
          "authorization_projection_revision": 3,
          "authorization_projection_digest": "sha256:...",
          "allowed_agent_ids": ["laptop-reviewer"],
          "dependencies_satisfied": true,
          "dependency_revision": 12,
          "gates_open": true,
          "gate_revision": 5
        },
        "repository": "git:example/repo",
        "code_revision": "0123456789abcdef",
        "last_lease_epoch": 7
      }
    },
    "leases": {
      "todo_review": {
        "lease_id": "lease_...",
        "owner": "laptop-reviewer",
        "lease_epoch": 7,
        "expires_at": "2026-08-06T03:30:00Z",
        "write_scopes": []
      }
    }
  },
  "receipt_index": {
    "op_claim_review_01": {
      "request_digest": "sha256:...",
      "original_receipt": {
        "schema_version": "loopx_authority_receipt_v0",
        "operation_id": "op_claim_review_01",
        "request_digest": "sha256:...",
        "command": "claim_work",
        "actor": {"agent_id": "laptop-reviewer", "device_id": "laptop"},
        "todo_id": "todo_review",
        "accepted_authority_revision": 43,
        "accepted_todo_revision": 9,
        "applied_at": "2026-08-06T03:20:00Z",
        "lease_id": "lease_...",
        "lease_epoch": 7,
        "expires_at": "2026-08-06T03:30:00Z"
      }
    }
  },
  "receipt_retention": {"mode": "retain_all_v0"}
}
```

该 schema 不包含 raw todo body、transcript、credential、绝对路径或 raw evidence。
它只包含裁决这一命令切片与恢复其凭证所需的事实。与现有 LoopX 一致，claim 后
todo 仍为 `open`，不会引入一个本地状态机不存在的 `claimed` status。在目标合同里，
soft ownership 由 `claimed_by` 表示，执行权由 lease/fence 表示；默认的 `legacy`
交接模式没有做到这一点：两本账分叉时，软认领方在写路径上实际胜出。声明了
`hard_lease` 的 goal 会把分叉变成类型化错误，并在完成时要求钥匙，详见附录 B。

这里的 eligibility revision/digest 都是目标 todo 所引用的快照：authorization 只覆盖
该 todo 的 actor scope，dependency 只覆盖它的传递依赖闭包，gate 只覆盖实际约束
它的 gate。它们不是换一个名字继续使用 Goal-wide revision。参考切片固定
`write_scopes=[]`；未来接入非空 write scope 时，与其他 active lease 的 scope overlap
也是该 claim 的真实跨 todo precondition，必须在内部 rebase 后重验并在冲突时拒绝。

这些 target-scoped token 还承担 coverage 与 no-ABA 义务。目标 todo 的 claim state
或 lease epoch 发生任何语义变化，都必须推进 `todo_revision`；allowed actor、依赖
闭包或满足结论、实际约束它的 gate 集合或结论发生变化，都必须推进对应 revision，
并在存在 digest 时同时更新 digest。同一个 token 不得复用于不同快照。Authority
无法证明这些覆盖关系时，不得内部 rebase。Deterministic reference 只验证静态
bootstrap snapshot，尚未资格化动态 projection publisher。

原始 receipt 证明某 operation 在某个 authority revision 被接受；它不证明对应
lease 当前仍有效。因此 replay response 返回逐字段等价的 `original_receipt`，同时
另行命名当前 observation，例如 `observed_authority_revision` 和
`authorization_status=active|expired|superseded`。

## 5. 受控命令怎样落账，崩溃后怎样找回回执

Request envelope 使用 `operation_id`，以免与现有 CLI 中 `command_id` 的用法冲突：

```json
{
  "schema_version": "loopx_command_v0",
  "operation_id": "op_claim_review_01",
  "actor": {"agent_id": "laptop-reviewer", "device_id": "laptop"},
  "goal_id": "shared-rust-review",
  "command": {
    "type": "claim_work",
    "todo_id": "todo_review",
    "expected_todo_revision": 8,
    "expected_preconditions": {
      "authorization_projection_revision": 3,
      "authorization_projection_digest": "sha256:...",
      "dependency_revision": 12,
      "gate_revision": 5
    },
    "lease_ttl_seconds": 600
  }
}
```

Authority 规范化完整的语义 request 并计算 `request_digest`。Digest 覆盖 actor、
Goal、command type、target todo revision、命名的 authorization/dependency/gate
precondition 与 command parameter；不覆盖 transport retry metadata。Goal-wide
`authority_revision` 不属于客户端业务前置条件，也不进入 request digest。调用方如需
携带读到的 head revision，只能把它作为 transport observation；改变该观测不构成
一条新的语义 operation。

Operation identity 标识调用方的一次逻辑尝试，不是参数组合。在 transport retry 外
生成一次 id，并在该尝试内复用；UUID 可以满足这个用途。稍后的独立调用即使参数
相同，也可能需要新 id（例如其他 writer 改值后再次设置原值）。永久按参数哈希会
重放过期历史；按新读到的 provider revision 哈希，也无法在提交后丢响应时找回原 id。

当前 claim/update 合同中的 `changed=false` 描述 Todo/lease 状态，不表示存储零写入：
首次接受一个具名 no-change operation 时，会在 CAS 下保存终结 receipt。状态后来
变化，再重试该 id，必须重放原来的 no-change，不能变成新工作。空 archive selection
有另一套明确的零事务合同，不能推广到所有动词。Receipt-only history 增长确实有
存储成本，但优化时必须保留 identity consumption、重放与冲突校验。

当前本地 facade 在 managed-runtime retry 内复用生成的 id。两次独立 CLI 调用不会
自动视为同一尝试：claim 提供 `--claim-operation-id`，update 提供
`--update-operation-id`，create 目前没有等价的跨进程恢复 key。这是 caller recovery 的限制，不证明业务效果重复，
也不能宣称通用 exactly-once。扩展前应先定义重试边界、区分 retry 与新 intent，再
决定是否需要 key 或耐久 attempt tracking。用丢响应与中间插入其他写入来验证，
而不是用禁止 UUID 构造的源码扫描代替语义测试。

Canonical Todo 命令现在共用一个 TS 回执恢复 owner。本地结果合同将提交后未能
确认的回读保留为 `ambiguous`，并指出恢复所需的原 operation；不能从回执不可用
推断未写入，也不会自动重试 CAS。明确的诊断变化及完整 fixture 矩阵见
[命令恢复检查点](typescript-control-plane-migration-v0.zh-CN.md#命令回执与恢复的统一所有者)。
历史回执身份和单向 Markdown 投递保持不变。这是命令恢复验证，不代表存储保留、
服务可用性或 promotion 已获资格。

对每个 request，authority 执行以下顺序：

1. load aggregate 与 provider generation；
2. 在执行当前状态校验之前查找 `operation_id`；
3. id 已存在且 digest 相同：返回 `already_applied` 与已存原始 receipt，不写入；
4. id 已存在但 digest 不同：返回 typed `operation_identity_mismatch`，不写入；
5. 校验 actor scope、目标 todo revision、命名 precondition、eligibility、claim state
   与本 reference 切片实现的 empty-scope lease rule；
6. 在 authority 中计算 next coordination state 与 original receipt；
7. 把 transition 和 receipt-index entry 一起放进一个确定性 envelope，并提交一次
   provider CAS；
8. provider 返回 conflict 或 ambiguous 后，reload，并在分类结果前重新查 receipt；
9. receipt 不存在且 generation 未前进时，以 `provider_outcome_unproved` fail closed；
   generation 已前进时，重新校验目标 todo 与命名 precondition，相关事实未变才基于
   latest head 重试。Receipt 缺失本身绝不证明成功；最终 `applied` 必须来自一笔新的
   successful CAS；
10. CAS miss 后，只有相关事实仍允许原命令时才继续 rebase；初始无效请求仍按普通
    domain validation 拒绝。纯粹的无关 head 前进不会成为业务 conflict；持续
    contention 耗尽 retry budget 时返回 typed `failed`，且不得创建 receipt。

API result class 如下：

| Result | 含义 |
| --- | --- |
| `applied` | State 与原始 receipt 一起提交。 |
| `already_applied` | 相同 operation 与 digest 早已提交；返回已保存的原始 receipt。 |
| `conflict` | 此 operation 不存在 receipt，且目标 todo 或命名 precondition 已 stale。 |
| `rejected` | Identity、eligibility、gate 或命令校验失败，未改变状态。 |
| `failed` | 无法证明存在 accepted result，或无关 provider contention 耗尽内部 retry budget；仅可依据有界基础设施策略重试。 |

`conflict`、`rejected` 和 `failed` 都不是成功凭证，不得得到伪造的 applied receipt。

### 5.1 第一个命令：`claim_work`

- `claim_work`：在一个 transition 中校验已有 runnable todo、设置 claimant、创建
  lease、铸造下一个 lease epoch，并保存原始 receipt。它的必填字段是 `todo_id`、
  `expected_todo_revision`、`expected_preconditions` 与 `lease_ttl_seconds`。

Accepted claim 同时推进 `authority_revision` 与目标 `todo_revision`。它只能操作由
显式 bootstrap/migration 安装的 todo，绝不把未知 todo 作为副作用创建。
Deterministic reference 的 eligibility input 是紧凑元组 `allowed_agent_ids`、
`dependencies_satisfied` 与 `gates_open`，并绑定到所命名的 authorization、
dependency、gate revision 与 digest。

`authority_revision` 是每条 accepted command 的 Goal-wide commit sequence，用于
审计、read model 和 receipt ordering，不是所有命令共享的 optimistic-concurrency
前置条件。底层 aggregate 仍以 `provider_generation` 串行提交；CAS loser 必须根据
自己的 target todo 与命名 precondition 决定是否内部 rebase，而不能仅因另一个独立
todo 已提交就要求调用方重新发一条 operation。

未知 command type fail closed。transfer 或 delegated assignment、任意 todo/gate
mutation、quota reservation 与 external effect 仍需要后续 runtime 合同与
qualification；非空 write scope 与跨 todo scope-overlap 拒绝同样需要后续 command
contract 与 qualification。下面的可恢复执行动词在 #3669 历史实施序列中曾称为
Stage 3；按照第 11 节当前的交付编号，已合入的这部分属于 Stage 0 reference foundation，
不是第 11 节的 Stage 3 远端 shadow 阶段。第 5 节的步骤 1-4 与 7-10
（identity、digest、replay、CAS、reload、rebase、budget）对每个动词原样适用，只有
每动词的前置条件与迁移（步骤 5-6）不同。

### 5.2 `renew_work`

必填字段：`todo_id`、`expected_todo_revision`、`lease_id`、
`expected_lease_epoch`、`lease_ttl_seconds`。调用方出示自认持有的栅栏；缺席
lease、lease id 或 epoch 不符（typed `stale_lease_fence`）、调用方不是记录的
持有者（typed `not_lease_holder`）、以及 authority 自己的时钟已判过期的 lease
（typed `lease_not_active`，见 6.4）均被拒绝。被接受的续约用 authority 时钟延展
`expires_at`，lease id 与 epoch 不变，并同时推进 `authority_revision` 与目标
`todo_revision`：有效期是被 revision 覆盖的事实，携带续约前观测的 reclaim 会
冲突，而不是搭内部 rebase 穿过去。

### 5.3 `release_work`

必填字段：`todo_id`、`expected_todo_revision`、`lease_id`、
`expected_lease_epoch`。release 是持有者提前放弃，因此只在 lease 仍活跃时有效：
在真实 holder gate 下清除 claim，并在同一迁移里退租。被接受的迁移清空
`claimed_by`、删除 lease 条目并推进两个 revision，而 todo 的 `last_lease_epoch`
水位保持不变——在共享聚合里水位就是终结记录，下一次 claim 必须严格高于它铸造，
release 因此不可能 A/B/A。过期的 lease 不可 release，它的归宿是 reclaim。

### 5.4 `reclaim_work`

必填字段：`todo_id`、`expected_todo_revision`、`expected_preconditions`、
`lease_ttl_seconds`。reclaim 是对合格 agent 的常设委托：当 authority 自己的时钟
看到记录的 lease 已过期至少一个 reclaim 宽限窗（见 6.4），目标
`allowed_agent_ids` 里的任何 agent 都可接管。authority 裁决过期并合成委托；核心
随后经由最小特权步骤执行其余全部规则——在委托下清除陈旧 claim，然后走与首次
claim 完全相同的组合，新 lease 因此通过同一个真实 holder gate。被接受的迁移铸造
下一个 lease epoch、把接管者设为 claimant、推进两个 revision 与水位，receipt 记录
被替代的 owner 与 epoch。仍在有效期或宽限窗内的 lease 是 typed
`lease_not_reclaimable`；无人认领的 todo 是 typed `todo_not_claimed`（请用
`claim_work`）。

### 5.5 `complete_work`

必填字段：`todo_id`、`expected_todo_revision`、`lease_id`、
`expected_lease_epoch`、`no_followup`、`successor_todo_ids`、`evidence`。完成
要求活跃的 lease 栅栏：核心的 terminal gate 校验调用方是 claimant 且持有出示的
栅栏，过期或被替代的栅栏与其他陈旧写入一样 typed 拒绝。一次被接受的迁移同时落
账所有事实：todo 变为持久 `done` 并携带显式 `completion_continuation`（由记录
字段导出：`no_followup` | `successor` | `active_goal`，两者同置与本地写入一样被
拒）、lease 退役、声明的 successor 以 open、无人认领、revision 0 的形态继承父
项执行上下文被创建，可选的 evidence pointer 落在完成记录上。evidence 是一条可
携带性边界，不是自由文本：`pointer` 必须使用 provider-neutral 的
`artifact://<public|private>/<opaque-artifact-id>` URI 形状（绝不是主机文件系统
路径、provider URL、query 或 credential），URI 内的隐私 namespace 必须与同层
`privacy_class` 相等，`digest` 是 sha256 内容摘要，`privacy_class` 是闭合词表
`public | private`——按 §1.1，绝不是 body；命令边界与 head 校验通过同一个共享
oracle 执行这条合同。持久记录逐字段满足本地
durable-completion 投影 seam，两个世界读同一份真相。

### 5.6 stale-fence 规则

每个携带栅栏的动词出示 `(lease_id, expected_lease_epoch)`。与记录的 lease 不符
的栅栏是终态 typed 拒绝——`stale_lease_fence`——并且绝不被内部 rebase 重试穿越：
无论多少无关命令推进聚合，被替代执行者的写入始终被拒。这一条加上每次接管都铸
新 epoch，就是 Stage 3 horizon 证明"被替代的执行者无法写回"的机制。

## 6. 谁做判断，谁负责保存

### 6.1 Authority 是记账员

LoopX authority 负责：

- request normalization 与 digest；
- actor、todo、dependency、gate 与 authorization 校验；
- 目标 todo 与命名 precondition 的业务冲突判定，以及无关 head 前进后的有界
  CAS rebase；
- `authority_revision` commit sequence 与 todo revision transition；
- 铸造 time、lease id、lease epoch 与 expiry；
- receipt 内容与 replay classification；
- privacy filtering 与 command-specific invariant。

### 6.2 Provider 只负责把账存稳

Provider contract 刻意不含语义：

```text
load()
  -> (aggregate | none, provider_generation)

compare_and_put(
  expected_provider_generation,
  aggregate
)
  -> applied(new_provider_generation)
   | conflict(current_provider_generation)
   | ambiguous
   | failed
```

Provider 必须对完整 aggregate 做确定性序列化，并提供 atomic conditional
replacement、durable success、same-key
read-after-write reconciliation，并在无法证明写入是否提交时返回 typed ambiguous。
它不得解析 LoopX command、铸造 clock/lease、裁决 eligibility 或合成 authority
receipt。领域 `operation_id` 与 request-digest replay 合同只存在于 authority 及其
原子保存的 receipt index 中，不是 provider API 参数。Provider 可以生成私有的
publication-attempt identifier，但该 identifier 不具有 LoopX authority 语义。

调用这些方法之前，provider instance 或 handle 已绑定一个 `goal_id` 与 provider
key；动词中省略 `goal_id` 并不表示该 key 是 global。

在这个二动词合同下，receipt index 不能作为单独 document 发布。先发布 receipt
可能为从未发生的 transition 记录成功；先发布 state 则可能在 crash 后丢失唯一
凭证。将来若要拆分，必须有 provider-neutral 的 multi-record transaction 或
commit-marker protocol，并通过新的合同 review。

#### 6.2.1 三种 provider profile，一个权威

| Provider profile | 在本 RFC 中的角色 | 晋升要求 |
| --- | --- | --- |
| file | 内置 deterministic 与 parity backend。当前 Markdown、registry JSON、event JSONL、run file 与 task-lease file 在各自合同显式晋升前仍是 canonical。 | 任一 file aggregate 成为 canonical 前，必须在同一 typed transaction boundary 后证明 parity 与崩溃恢复。 |
| NoKV | 在线协调的可选共享存储 provider。它提供 generation CAS 与 store-lineage 原语，不提供 LoopX 语义。 | 关闭 recovery/availability 阻塞，验证容量与 HA，再通过 shadow parity 与有界 canary。 |
| PostgreSQL | 面向 multi-Agent 或私有部署、位于已认证 LoopX authority service 背后的可选 provider。PostgreSQL 表与 role 是 service 实现细节。 | 实现同一 conformance contract、transaction isolation、fencing、幂等、cursor、审计与网络故障 fail-closed；Agent 不得直连数据库。 |

物理数据库或 cluster 共同部署不会合并 ownership。部署应以独立 schema 与 role 隔离
LoopX 控制面记录与应用领域记录，只通过 opaque identity 或 digest 建立关联。
provider-specific payload 不进入 provider-neutral 的 LoopX schema。

#### 保留 journal 的扫描合同

`scanCommitted(after_cursor, limit)` 将 head、记录和 lookahead 绑定到同一个读取
snapshot。当前 provider 保留从 cursor 1 起的连续 journal；`null` 是唯一起点，
其他游标必须是规范的正十进制字符串。超过 head 的正数 checkpoint（包括空存储）
返回 `scan_cursor_out_of_range`，不能确认“已成功读完”；非法运行时类型在访问
存储前拒绝。

共享 TS scan owner 验证请求区间及用于证明 `has_more` 的 lookahead 行。
缺行、重复、乱序及末条 transaction 与 snapshot head 不一致均为协议错误。
PostgreSQL metadata/head/row 使用 repeatable read；File/NoKV 验证同一个保留
历史的 envelope，SQLite 保留原读事务。此合同不增加跨页 snapshot token，后续
调用可以看到后续提交；也不证明 checkpoint 之前全部历史、任意 payload 的完整性，
或未来压缩/分段历史格式。

File 与 NoKV 在既有 transaction 模块共用 journal 解码及 append 构造，各自
保留版本摘要输入、identity、CAS 与持久化副作用。验证包含四个 adapter 的复杂
fixture、真实 PostgreSQL 并发提交及一次性损坏行，以及真实来源隔离副本上的
File/PostgreSQL 分页。不迁移活跃 Goal，不切换默认 provider，不宣布 D1–D3 合格。

#### 6.2.2 参考 CAS 切片之后的目标 store contract

当前 Stage 2/3 参考实现刻意使用上面的较小 `load` / `compare_and_put` document
seam。它先证明扩大持久化边界所需的 lifecycle 与 receipt 不变量；它不声称已实现
未来 service-grade transaction API。

下一步由 LoopX 持有的 TypeScript boundary 至少应表达以下逻辑操作（精确命名与
wire shape 仍由实现阶段决定）：

```text
load_authority(goal_id)
  -> head + provider_revision + cursor

commit_authority(
  expected_provider_revision,
  operation_id,
  events,
  next_projection,
  receipts
)
  -> applied | conflict | ambiguous | failed

read_receipt(operation_id)
scan_committed(after_cursor, limit)
```

这份合同负责原子 event/projection/receipt 持久化、opaque provider revision、cursor
与耐久 readback。LoopX 仍负责 operation identity、request normalization、合法
transition、claim/lease fencing、Turn admission、quota 语义、settlement 幂等，以及
receipt 的含义。特别是，adapter 不得把 provider transaction result 重新解释为领域
判断。

### 6.3 三个版本号不是一回事

| 版本域 | Owner | 含义 | Consumer |
| --- | --- | --- | --- |
| `provider_generation` | Provider | 条件替换已存字节的 opaque token | 仅 authority/provider seam |
| `authority_revision` | LoopX authority | 接受一条 command 后的 per-goal 逻辑 commit sequence | 审计、receipt 与 read model；不作为所有业务命令的共享前置条件 |
| `lease_epoch` | LoopX authority | Ownership 的 per-todo fencing generation；新 lease generation 时推进，普通 renewal 不推进 | Executor 与获准 writeback |

Backend 常会对每条 accepted command 推进一次 generation，但数值相等永远不是合同。
Migration、repair 或 provider metadata 可以改变 provider generation，而不授予新的
LoopX authority revision。

Provider 的 document generation 或 database revision 只实现
`provider_generation`。LoopX authority 继续负责另外两个版本域与 transaction 内
的 receipt。

### 6.4 过期裁决与绑定围栏

墙钟事实只有一个裁判：正在应用命令的 authority，用它自己的时钟对照已 load head
的 `expires_at`。调用方对时间的看法只是请求动机，绝不是证据；核心保持无时钟，
只接收裁决后的 active/expired 结论。renew、release、complete 要求裁决时 lease
活跃。reclaim 额外要求 lease 已过期至少一个可配置的宽限窗，其下限是部署内端点
间的最大预期时钟偏差；生效的宽限值是 executor 声明的参数，且配置边界本身
fail-closed：只接受有限、非负的数值，因为 NaN、负数或布尔宽限会把接管提前而不
是推迟。正确性从不依赖宽限值——即使自认还活着的持有者也会被 reclaim 铸出的
epoch 栅栏挡住，偏差只可能推迟接管，绝不可能弄脏接管。又因续约推进 `todo_revision`（5.2 节），有效期同样被
revision 覆盖，reclaim 借并发续约的 rebase 穿越通道被双重关死。

store-lineage 绑定围栏关闭 Stage 2 状态中量化过的 restore 隐患：仅靠
`provider_generation` 无法承载生命周期身份。provider 合同新增一个只读动词
`store_identity() -> str`，返回该存储血统的稳定身份：NoKV adapter 绑定
`workbench` 加服务端为每个 workbench 化身铸造、永不复用的
`workspace_incarnation_id`；file provider 在跨进程锁内串行化创建，以完整临时写入、
fsync、原子 rename、父目录 fsync 发布严格格式的身份文件。
`bootstrap` 把身份嵌进 head（`store_binding`），authority 在 loaded head 的绑定
与 provider 身份不符时拒绝一切命令——typed `store_lineage_mismatch`——并在每次
reload 后复查。restore 因此保存冻结字节与血统而不授予在场权威，正如 Stage 1
边界所要求；晋升恢复态需要显式、经评审的 re-bootstrap 铸造新绑定。已知残余：
file 存储目录的逐字节拷贝会连身份文件一起拷走，故 file provider 的围栏只在拷贝
排除身份文件时才检测得到迁移；NoKV 的化身身份没有这个缺口，是共享部署的权威
围栏。

加入绑定是一次 schema 变更，不是重释义：head 携带
`loopx_coordination_head_v1`，而 legacy `loopx_coordination_head_v0` 文档
（Stage 2 的形状，没有 `store_binding`）被分类为
`head_schema_migration_required`——typed 失败，绝不是未分类的校验崩溃，也绝不
会被当作当前文档静默读取。唯一升级路径是显式的 `migrate_head_v0_to_v1` 操作：
operator 亲证经其评审的存储自身身份（对确认为权威血统的存储调用
`provider.store_identity()`），再经同一 CAS 写回迁移后的 head。绑定被刻意设计
为绝不从"head 恰好经由哪个 provider 载入"推断——自动绑定会让 v0 存储的任何恢复
拷贝为自己授权，而这正是该围栏要防止的捕获。迁移只识别 Stage 2 writer 实际可
产出的子集：精确 v0 字段集的 `open` todo 与 `claim_work` receipt。旧 v0 validator
没有钉死 todo status 词表；未知 status 或挂在 v0 token 下的 Stage 3-only 字段因此
按需人工修复的 corruption fail closed，不会被 grandfather 成新权威。

## 7. 回执先全部保留，压缩以后再谈

v0 使用 `retain_all_v0`：任何已提交 receipt-index entry 都不得 GC、过期或从
snapshot 中省略。这是 correctness-first 的证明边界，不是 production-scale retention
承诺。

若 receipt-index entry 已存在，但其原始 receipt 缺失或无效，authority 将其视为
provider-protocol violation 并 fail closed。它不能 fallback 到 provider publication
history，也不得从当前 head 重建 receipt。

有界 retention window、receipt segmentation 或 external receipt ledger 都需要后续
RFC：它必须保持 atomic proof，并定义窗口之外的行为。在此之前，compaction 可以
重写字节，但必须携带完整 receipt index。

### 7.1 拟议修正：封存回执段（owner 决策，Q5）

单 CAS 文档里的 `retain_all_v0` 撑不到多 agent canary：Stage 2 实测每 receipt
约 750 字节、120 个 receipt 内 claim 延迟涨约 7 倍、累计重发布平方增长。Stage 3
又新增四个写 receipt 的动词，增长率只会更差。上文那句已明确要求 receipt 离开单
文档前必须有经评审的修正案；本小节就是该修正案，附证据提出，**尚未生效**。

设计（在 live NoKV 栈上与两个替代方案各跑 150 笔迁移对比）：head 保留一个有界
receipt 窗口；窗口写满时，authority 把它封存为兄弟路径
（`goals/<goal>/receipts/segment-<seq>.json`）上的不可变回执段对象，经 artifact
路径 create-only 发布，**先于**将封存回执从 head 删除并把 `{path, sha256,
count}` 追加进段链的那次 CAS——即 §1.1 的 artifact-first 次序，head 绝不引用未
持久发布的字节。每条已提交 receipt 保持可验证：replay 先查窗口、再解引用段链；
段缺失或摘要不符是 provider-protocol violation 并 fail closed，即 §7 fail-closed
规则的段级形态。NoKV 的发布语义让封存免费获得 exactly-once：`operation_id` 与
`artifact_revision_id` 是 root 级唯一并带不可变输入重放，段发布与 head CAS 之间
崩溃后的重试是幂等重放（已 live 验证）——但两个 id **必须**由 workbench、path、
内容共同导出，否则第二条血统复用同一推导会被判 id 重用而拒绝。

实测裁决（debug 栈，相对数字）：封存段让 head 缩小 19 倍（150 receipt 时 5.8
KiB 对 112 KiB）、累计发布字节少 7.5 倍、每笔延迟保持平坦而 retain_all 持续增
长，代价是 +3% provider 往返；恢复已封存回执约 160 ms 对 in-head 约 43 ms。
receipt-per-object 替代方案被全面支配：每笔迁移多付一次对象发布（全程约 2 倍
延迟），head 还要经指针索引线性增长。窗口大小是部署参数；实测值为 16。

在 owner 接受本修正案（§12 Q5）之前，`retain_all_v0` 继续生效，Stage 3 动词原样
运行其上。

### 7.2 十天 Goal：本地存储资格化目标（提案）

受支持的 goal 必须能**至少持续十个自然日**，跨进程重启、主机休眠、迟到回执和
二进制升级继续执行，不依赖人工截断历史或因存储问题重建 goal。十天是最低资格化
周期，不是 receipt 过期时间或 goal 寿命上限。本修订设定产品目标，不宣称当前任何
provider 已通过；经评审切换前，已交付规则仍是 `retain_all_v0`。

#### 工作负载与成本模型

只规定时长不足以定义容量。首轮本地资格化采用以下**提议负载**，不是实测用量或承诺：

| Profile | 每个 goal 的负载 | 资格化周期 |
| --- | --- | --- |
| 最低连续性 | 三个持续持有 lease 的 Todo，TTL 600 s、每 300 s renew：其他写入之外每天 864 次 renew | 10 天；8,640 次 renew 加生命周期／回执写入 |
| 本地设计目标 | 八个注册 agent；四个并发 writer 操作不同 Todo，另加同 Todo 竞争；最多 1,000 个活跃 Todo/lease/gate record；每天 10,000 次已提交 transaction，含 renew、receipt 和 capture | 10 天共 100,000 次 commit；另跑 30 天／300,000 次余量验证 |
| Payload 与积压维度 | live projection 分别为 8 KiB、64 KiB、1 MiB；每次新增 event/receipt payload 至多 4 KiB；读写比 5:1；每秒 10 次 commit、持续 60 s 的突发；projection consumer 落后 24 h | 分别验证各维度及组合本地目标；retry/conflict 与 commit 分开计数 |

每个 fixture 声明活跃／归档记录、序列化大小、命令比例、索引大小和 capture 放大倍数；
一个模型 Turn 可能产生多次 commit。验证历史增长时固定 live state，之后单独增加
live state。不能把所有已完成 Todo 或 receipt 的无界列表藏在所谓固定的 live projection。

当前 `FileAuthorityStore` 每笔保留 P 字节 projection，N 笔约产生 P*N 最终历史字节，
累计文档发布量约 P*N*(N+1)/2，尚未计 head、event、receipt 和 envelope。普通读取还会
解码、验证完整链。P=15 KiB 时，仅 renew 的例子在第 10 天累计发布约 **534 GiB**，
第 30 天约 **4.69 TiB**。旧文中的 380 MiB 只算了第 30 天的 N*P，并非保留历次
projection 后的累计重写。这是 payload 解析估算，不是 SSD 物理写入或实测延迟；
若每个 projection 自身还包含不断增长的 receipt index，成本可能更高。

#### 本地优先方向与兼容边界

在既有 TypeScript `AuthorityStore` owner 后资格化**嵌入式事务存储，首选候选为
SQLite**。本地 goal 不应依赖 PostgreSQL 服务。file-v0 保留作 conformance/import
基线，通用十天 goal 晋升不能依靠其全历史重写。[PR #4121](https://github.com/huangruiteng/loopx/pull/4121)
在该 owner 后提供显式 opt-in 的 SQLite conformance 候选；它本身不证明长程耐久性，
也不切换默认值。依赖／打包、Windows/macOS/Linux 与受支持 Node profile 的证据仍是
显式门禁。分段文件日志作为比较候选；PostgreSQL 继续走独立的共享服务路线。

只替换数据库不够。完整切片必须：

- 原子发布 current state、operation/digest 唯一性、原始 receipt、有序 cursor，以及
  projection outbox entry。operation lookup 与 cursor 分页走索引；保留 ambiguous
  commit、CAS、lineage、domain fencing 语义，存储层不决策 Todo policy。
- live head 不随总历史增长。使用周期性校验 checkpoint 加 committed state delta 或
  immutable state block；原始 receipt 移出 hot head。replay tail 与 index/root metadata
  都要有界。head 保存全部封存段 pointer 仍然无界，扫描段链也不等于索引查询。
- 保留 `scanCommitted` 逻辑合同：它目前每笔返回完整 projection。通过 checkpoint/delta
  有界分页重建精确版本，或显式升版并迁移每个 consumer；不能静默删除历史 projection。
  尚未证明现有 event 足以重建，因此 canonical state delta 需要独立等价验证。
- authority 的 in-head receipt index 与 provider storage 一起显式升版迁移。checkpoint、
  restart 和后续无关 commit 后，request-digest 冲突检测与原始 receipt 字段仍逐字节等价。
  cache 不能成为正确性的唯一来源。

这是包含真实 CLI caller 和 consumer readback 的一个本地持久化切片，不是零散 helper
迁移或第二个语义 kernel。它覆盖 capture outbox、status/quota 所需归档 Todo/history 分页；
其他存储和原始 artifact 单独计量，不宣称随本切片一起解决。

#### 提议验收预算

在声明配置的本地 SSD 主机上，报告 OS/filesystem、CPU/RAM、Node/database 版本、耐久
配置、样本数、p50/p95/p99、lock wait、RSS、database/WAL/archive 大小与逻辑写入字节。
冷 CLI 启动与 warm store service time 分开；不得关闭 fsync/checkpoint 安全性换成绩。

64 KiB live state 下，对比 10,000 与 100,000 次历史 commit：

- warm head load 和索引原始 receipt read 的 p95 <= 50 ms；durable commit p95 <= 100 ms。
  固定 live state 与负载时，历史增长的 p95 比值 <= 2。
- warm scan 100 笔 p95 <= 250 ms；cold open 加首次权威读取 <= 2 s；有界 crash recovery
  <= 30 s。全量 archive integrity audit 可线性增长，但普通启动不能依赖它。
- 相对匹配的 10,000 次基线，完整 CLI mutation 的存储成本增量 p95 <= 200 ms。
  同时报告完整 status/quota 延迟；不得将历史扫描藏进 compatibility consumer，也不能
  用进程启动摊销掩盖成本。
- 固定 live state/delta 大小时，累计逻辑写入、保留字节、恢复工作须有明确上界。
  从 10,000 到 100,000 笔累计写入增长 <= 15 倍，不能呈平方重写的约 100 倍；纳入
  checkpoint、index、WAL、compaction 计量。稳定态 RSS 不得随历史交易数增长。
  1 MiB 与 300,000 次余量维度单独报告；失败应缩小支持范围，不能藏在平均值里。

以上是提议工程预算，不是当前实测。在启用前依据首轮匹配基线评审，不得放松正确性、
静默改变负载或宣称未资格化的连续运行周期。

#### 保留、恢复与交付门禁

十天执行不授权第十一天删历史。初始策略在 goal 全生命周期保留 operation identity、
digest 和原始 receipt，至少覆盖第 10／30 天重放第 1 天操作。只有保留的 checkpoint/log/
index 能重建全部承诺历史时，物理 compaction 才能删除冗余编码。cold archive 必须可寻址
且可验证；证明不可用时 fail closed，不能当作新操作。后续 expiry 必须有版本化 retention
合同及窗口外响应；TTL 或 tombstone 都不能授权重复 effect。

checkpoint/segment 发布需支持崩溃恢复并耐久切换 manifest/root。仅在替代数据耐久且每个
注册 consumer 的持久 cursor 已推进或显式选择 full-resync 后回收旧字节；保护落后的
projection outbox、export 和 backup。设置 byte/lag 准入预算，在磁盘耗尽前暴露 maintenance/
backpressure，并为恢复预留空间；ENOSPC 或提交中断不能抹除证明或算作成功写入。
总审计存储可以随有用历史线性增长；有界的是热路径和维护批次。

主机休眠或重启不能保留已过期 lease；恢复时必须重读 authority，并经正常 epoch fence
重新获取权限。连续性是同一个耐久 goal 安全恢复，不是进程或 lease 永远存活。

资格化有两个独立出口：加速的 100,000/300,000 次测试证明容量，真实 **>=10 天**合成
Goal soak 证明自然时间连续性。覆盖第 1 天历史 retry、同 id 同／异 digest、并发 CAS、
每日 reopen、休眠、lease expiry、24 h consumer lag、append/checkpoint/index 发布前后
crash、disk-full、backup/restore lineage，以及一次受支持 upgrade/rollback。与独立参考
对比 final state、receipt、cursor scan；不接受丢失已确认 commit 或重复 effect。只用
一次性 goal，不碰活跃用户状态。压缩时钟不证明自然时间耐久性；发布本 RFC 不启动 soak
或 monitor。

代码 PR 可在 soak 证据待补时合入，但 promotion 继续 hold。两个出口、显式 import/
fencing/export 演练与 maintainer review 都通过才可晋升。发布紧凑可复现证据，不发布
原始私有日志。

#### SQLite 替换 file 的阶段节点（提案）

拟议终点是让 SQLite 成为**已资格化本地 Goal** 的常规 primary store，而不是删除所有
文件产物或替代 PostgreSQL 的共享服务 authority。file-v0 继续作为可检查的参考、导入／
导出选项；canonical promotion 后 Markdown 仍是派生显示。交付以以下证据为节点，
不以日历日期或本 PR 是否合并作为默认切换依据。

| 节点 | 所需证据／出口 | 默认值与权限边界 |
| --- | --- | --- |
| 候选 conformance | 评审 #4121 的原子提交、原始 receipt、cursor/digest 完整性、typed provider-open 失败、真实 CLI 与 OS/runtime 测试。 | 仅候选。file 仍默认；不迁移活跃 Goal，不授予 promotion。 |
| 有界本地 profile（L） | 满足本节不变的负载与预算矩阵：64 KiB 下匹配的 10k/100k、1 MiB 与 300k 余量、冷启动、锁等待、RSS、逻辑写入增长；资格化有界 checkpoint/delta 和 receipt 查询，同时保留精确历史 scan。 | 不切默认。保留完整性校验；成本超出 profile 时修正设计或明确缩小支持范围。 |
| 带 fence 的迁移与恢复（I/F 前置） | 在一次性 Goal 上证明 file→SQLite 导入、原始 receipt/replay 等价、consumer cursor/outbox 保留、crash/disk-full 恢复和反向导出／回滚；共享路由或投影变化时纳入要求的独立 legacy/file/PostgreSQL 只读演练。 | 先评审工具与 migration manifest。当前空 Goal selector 不是已有 Goal 的迁移 API；不得用活跃用户 Goal 做测试。 |
| 自然时间资格化与 opt-in canary | 完成真实 >=10 天合成 soak，覆盖本节规定的重启、休眠、第 1 天 retry、24 h consumer lag；随后单独申请小规模 opt-in operator canary，记录停止与回滚条件。 | C/I 与所选 provider 的全部 hold 仍有效。加速容量不替代自然时间；canary 不授权通用默认。 |
| 新 Goal 默认决策（F） | 维护者接受合格 profile、canary 结果、运维诊断、backup/restore 流程、发布操作说明和关闭默认的路径；在独立且明确披露的发布改动中切默认。 | 仅适用于新建且符合条件的本地 Goal；已有显式 file 选择保持固定。不受支持的 runtime/filesystem 需显式选择支持方案，打开失败不能静默切 backend。 |
| 已有 Goal 迁移与 file 退役 | 按已评审的 fenced workflow 逐批 opt-in 迁移，每批核对 receipt、历史、投影和回滚；删除路径前列清最后的 file-primary caller 与兼容窗口。 | 每个 Goal 需要明确迁移权限；证据满足后才退役常规 primary 角色。参考／导入／导出支持保留到其 caller 与保留责任分别结束。 |

**当前证据位置（2026-09-15 复核）。** #4121 已合并为
`bde1632bb6f29aeb9a8b4ac23ead3e98ba2f2f55`，交付第一个候选节点。
Lane L 中“保留存储有界”的那一半现在有可评审候选：`loopx_sqlite_authority_store_v2`
改为每 64 次提交一个 checkpoint、每次提交一条精确 delta，不再为每一行保留一份完整
projection；活跃头由 head 行、对应保留事务和游标连续性自证；单次历史读取最多重建一个
窗口；完整 delta 链由 `verifyAuthorityHistory` 线性证明。cursor、operation id、commit
digest、provider revision、receipt、event 与 scan 页面字节均未改变，已发布的版本 1
数据库通过评审过的 `examples/coordination/sqlite-authority-migration.ts` 入口迁移。
rehearsal 证据（1000 次提交／64 KiB）现为 16 个 checkpoint、63 次 replay 预算、
单次历史读取最多重建一个 64 次提交窗口，保留 1,048,576 字节 projection 与
126,714 字节 delta，而“每提交一份完整拷贝”为 65,536,000 字节。

这仍不代表 lane L 完成。File 与 NoKV 依旧在每次 load 时保留并解码完整 journal，
因此“有界恢复”是内嵌候选 provider 的性质，不是跨 provider 等价；SQLite profile
也仍不裁剪 receipt 与 event。逻辑/WAL 流量与 <=15x 累计写入预算、1 MiB 与 300k
headroom、完整领域负载、大历史恢复、fenced backup/restore、受支持升级/回滚、
OS/runtime 覆盖和 >=10 天自然时间 soak 仍是 hold，工具跑完不能声称已满足。参见
[SQLite 验证命令](../../reference/sqlite-authority-store.md#reproduce-validation)。
公开 Node 最低版本已升至 22.22.3，适用于 File 和可选 SQLite；SQLite 仍需检查
实际内嵌驱动的同步 finalization 与 WAL-reset 修复，参考组合为 Node 22.22.3／
SQLite 3.51.3。Node 24 主 runtime 与 Node 26
非阻塞 forward probe 保持原合同。

**迁移决策点。** 首次迁移已有 Goal 前，先在 authority writer fence 下冻结精确的源
lineage/revision，导入完整权威快照与保留证明，并独立比较原始 receipt 字段、operation/
digest 冲突及有序 scan。provider revision token 是不透明值；所需转换必须写入版本化
migration manifest，不能重新解释旧 receipt。绑定目标 incarnation，只有验证与 consumer
对账完成后才耐久发布 selected-provider 切换；任意时刻只能有一个 provider 接受写入。
源端继续 fenced，仅作回滚证据，不能成为同时写入的另一份 authority。

selector 切换前中止时，源端保持权威，只能通过已评审工具丢弃未 promotion 的候选。
目标已有新 commit 后，回滚必须 fence 目标并导出／对账新增提交证明，之后才能恢复
源端 writer。删除 selector、恢复旧 file 快照或启用 fallback 都不是回滚。证明缺失、
incarnation 错误、digest/lineage 失败、不明原因的 parity 差异或恢复预算超限，都应停止
该批次并暂停扩大默认范围。本提案不创造迁移命令、不启动 soak、不授予活跃切换权限。

## 8. 默认本地模式不变，共享模式必须显式迁移

### 默认本地模式

- 现有 project registry、Markdown active state、run history、可选 task lease、
  status、quota 与 host behavior 保持不变。
- 安装 provider 不会启用 shared authority。
- 当前 event-store bridge 仍报告 Markdown 为真相源，不允许自动 promotion。

两种部署形态共享完全相同的语义：

| Deployment | Authority boundary | Provider |
| --- | --- | --- |
| embedded/local | LoopX authority 运行在可信本地进程；晋升前，现有本地 writer 仍是 canonical | file |
| shared service | Agent 调用已认证的 LoopX authority API；service 持有 provider credential、租户与审计 | NoKV 或 PostgreSQL |

在两种形态中，Agent 都只是 API client。直接访问 provider 永远不会成为另一种
shared mode。

### Shared-authority mode

Shared mode 是按 Goal 显式选择。经 review 的实现必须：

1. 钉住 source registry、active state 与 privacy boundary；
2. 迁移期间停止或 fence 本地 writer；
3. 只把 scope 内的 coordination field 规范化为初始 aggregate；
4. 校验 todo/claim/lease/gate parity 与空 receipt index；
5. 记录 shared-mode declaration 及 authority endpoint/provider binding；
6. 让所有 P0 写都经过在线 authority；
7. 对已迁移字段，仅把 Markdown、local lease view、rollout row 与 status 渲染为
   projection。

Bootstrap 是受 fence 保护、发生在受控 shared write 之前的行政迁移，不是 P0 agent
command：它可以用选定的现有 todo 和空 receipt index 创建初始 aggregate。它的
source digest 与 mode declaration 必须耐久保存，让 restart 能区分 bootstrap 与尚未
初始化的 provider。

第一次 shared write 之前，迁移可以回滚到未改动的本地 source。发生第一次 shared
write 后，禁止自动 fallback 到本地 writer，否则会产生两个 authority。恢复必须
修复 authority，或执行另行 review 的 fenced export 与 mode transition。

Provider shadowing 与 read-only canary 可以采集 evidence，但两者都不改变真相源。
Promotion 必须显式发生，并遵循现有 fail-closed migration discipline。

## 9. 断网时怎么办，哪些数据不能出机器

Shared-mode authority 不可用时：

- cached projection 只有标记 stale 后才可读取；
- 不接受新的受控写入；
- 已授权的本地计算只能在现有 lease 与 effect boundary 内继续；
- 不自动 fallback 到本地文件写入。

本 RFC 不规定 wake latency 或 heartbeat topology。Delivery 可以在 Agent IM RFC 下用
pull、push 或 IM daemon，但消息已送达永远不能证明 coordination command 已提交。

Shared aggregate 与 receipt 可以包含紧凑的 public-safe 或显式 scoped private
metadata：stable id、无 credential 的 repository identity、精确 code revision、digest、
gate/dependency ref、claim/lease field 与按 privacy class 标注的 opaque pointer。不得
包含 credential、raw evidence、raw todo prose、transcript、raw log 或本地绝对路径。

## 10. 第一阶段怎么验收

全部检查均可由机器验证：

1. 两个 actor 从同一 provider generation claim 同一 todo，恰好一个得到
   `applied`；loser 得到 target-specific `conflict`，且没有 lease；winner 的 todo
   仍为 `open`，ownership 由 `claimed_by` 与 lease 表达；
2. 两个 actor 从同一 provider generation claim 同一 Goal 下两个独立 todo；在本
   reference 中二者 `write_scopes=[]`，且目标范围内的 authorization、dependency、
   gate 均未变化。第一次 CAS 的 loser 在 reload 与相关 precondition 重验后内部
   rebase，最终两者都得到 `applied`；Goal audit sequence、两个 todo revision 与
   两条 receipt 均只推进一次；
3. 以相同 operation id 与 digest 立即 replay，返回原始 receipt 且不改变状态；
4. 历史 A/B/A replay 在同一 deterministic provider fake 保留 aggregate、重建
   authority handle 后仍成立，并在 B 推进 head 后逐字段返回 A 的原始 receipt；
5. 相同 operation id 搭配不同 normalized digest 时被拒绝，state 与 receipt index
   均不改变；
6. Provider CAS 周围的 fault injection 永远不能暴露无 receipt 的 state 或无 state
   的 receipt；
7. ambiguous provider response 通过 reload receipt index reconcile：找到 receipt
   才恢复成功；同 generation 下缺失则 failed/unproved；generation 前进后缺失也必须
   重验并由一笔新的 successful CAS 才能得到 `applied`；
8. 对未知 todo、stale target/precondition、不符合 eligibility、dependency blocked 或 gate
   blocked 的 claim，拒绝且不创建 state 或 receipt；
9. 持续无关 provider contention 耗尽内部 retry budget 时返回 typed `failed`，不生成
   当前 operation 的 receipt，也不伪装成业务 conflict；
10. 保留的 receipt 经 reload fixture 后仍存在，不发生 receipt GC；
11. 测试分别处理 provider generation、authority revision 与 lease epoch；
12. privacy scan 不得发现 credential、raw body、transcript 或绝对路径；
13. 默认本地模式的行为不变，shared mode 永不 fallback 到未 fenced 的本地 writer。

配套 provider probe 是候选实现的 evidence，不构成弱化上述 normative check 的许可。
性能测量与具体部署 topology 被刻意设为 non-normative。

## 11. 分阶段交付

### 11.1 规范性交付计划

交付计划由一条共享基础线和两条 provider-specific 线组成。下列 workstream 是职责
边界，不是额外的 authority grant：

| Workstream | 主要职责 |
| --- | --- |
| LoopX core owner | TypeScript 持有的语义 transaction boundary；file-provider parity 与晋升；migration、本地 writer fencing、projection flip、rollback，以及单一权威源决策 |
| NoKV provider owner | NoKV adapter；live recovery、容量与 HA 资格验证；在不把语义下沉到 NoKV 的前提下反馈并共同完善 shared store contract |
| PostgreSQL provider owner | 通用 PostgreSQL provider/service；transaction isolation、认证、租户、审计与运维部署合同 |
| 联合验证 | Provider conformance matrix、单向 shadow parity、一个 Goal/两个 Agent 的 TEST ONLY canary，以及晋升证据 |

Shared control plane 是多个独立 ledger 的组合，不是一张巨大的 coordination aggregate。
Stage 3/4 qualification 必须保持以下 ownership 与 proof 边界：

| Ledger / decision | Authority 与稳定 identity | 失败边界 | Stage 3/4 证明 |
| --- | --- | --- | --- |
| Coordination head、Todo/claim 与 lease fence | `AuthorityStore`；`(tenant_id, goal_id)`、`operation_id`、authority revision 与 lease epoch | provider CAS/transaction 加 operation-receipt 回读 | projection、合法 claim/lease transition、fence、receipt、head 与 cursor 的 provider parity |
| Turn admission 与 quota | 独立 Turn/quota ledger；obligation、admission、debit 与 void receipt identity | 自己的 append/幂等边界；永不吸收到 coordination commit | 端到端观察：获准工作引用已接受的 coordination head，且 quota 只记一次 |
| Delivery、inbox 与外部 effect | 独立 delivery/inbox/provider ledger；event cursor、effect identity 与 provider receipt | connector/effect ambiguity 在所属 ledger reconcile | 端到端观察：steering 改变后续决策，且 effect 不重复 |
| Settlement 与 run history | 独立 settlement journal 与 run ledger；settlement/phase receipt 和 run identity | 有序 settlement checkpoint 与幂等 replay | 端到端观察：跨重启 settlement exactly-once；只从 coordination state 引用，不存入其中 |

只有第一行用于资格化 `AuthorityStore` 实现。其余行通过 typed reference 与 receipt
资格化控制面组合；即使通过，也不得宣称这些状态由 coordination provider 额外持有或
一起 transaction。

实施顺序如下：

1. **Stage 0——合入可恢复执行参考基础。** 将 #3669 与原生 TypeScript task-lease
   acquire boundary 集成，保持 TypeScript 是 acquire transaction owner，并关闭
   file store-identity 发布问题。#3806 完成本地 lifecycle transaction cutover，并让
   renew、transfer、release 在本地 file executor 与 provider-neutral coordination
   中消费同一个纯 TypeScript decision；Python 只做 typed adapter，不形成另一套权威。
2. **Stage 1——定义 provider-neutral transaction boundary。** 在 LoopX 持有的
   TypeScript 中表达 service-grade contract，并让 file provider 成为第一个
   conformance backend；不重建第二个 Python 语义权威。
3. **Stage 2A/2B——并行实现 provider。** NoKV owner 验证 NoKV adapter 与存储
   包络；PostgreSQL owner 实现通用 service/provider。两者复用同一套 LoopX
   transition 与 receipt 语义。
4. **Stage 2C——资格化并晋升第一个 canonical profile。** 先在不读取其决策结果的
   前提下，把现有 Markdown/task-lease writer shadow 到 `FileAuthorityStore`；验证
   parity、crash recovery、migration 与一键 rollback。随后通过单独评审的 promotion，
   让该 profile 成为本地 coordination authority，并 fence legacy writer；只有晋升后
   Markdown 与 task-lease 文件才退为 projection。state machine、完整 field manifest、
   receipt 与 acceptance row 保持 provider-neutral，使 NoKV 与 PostgreSQL 可以复用
   同一条 qualification 路线。仅证明 projection 结构或 head digest 相等仍然不够；
   promotion 还必须在精确 qualified revision 上证明完整的 consumer-visible Todo 与
   lease 语义。
5. **Stage 3——远端单向 shadow parity。** 晋升后的本地 `FileAuthorityStore` 仍是
   唯一 authority；将已提交观察投影到 NoKV 或 PostgreSQL 候选。Provider parity
   只对比 Todo/claim、lease fence、
   operation receipt、projection head 与 cursor。Turn admission、quota、settlement、
   inbox 与 run history 仍是独立 ledger；shadow 只记录验证端到端组合所需的 typed
   reference。不做双向同步，也不允许 provider 回写 file。
6. **Stage 4——TEST ONLY canary。** 用一个 Goal、两个 Agent 验证：不重复 claim
   、过期与 fencing 正确、重启后继续，以及网络失败时 coordination write
   fail-closed。同一个 canary 还要分别观察：外部 effect 不重复、inbox steering
   改变后续决策、settlement 幂等且 exactly-once；这些是跨所属 ledger 的 composition
   proof，不是 `AuthorityStore` conformance claim。
7. **Stage 5——切换唯一 authority source。** 只有经过评审的晋升，才能让 shared
   LoopX service 成为唯一 writer。本地 `.loopx` 退为 cache、offline projection 与
   诊断材料。绝不长期维持 dual-write 或 dual-master。

### 11.2 当前实现与证据台账（非规范性）

下面带日期的条目保留已交付边界、实验与评审结论。它们是分阶段计划的证据，不是
额外规范。后来的条目只有在点明相关合同、精确实现边界与验证证据时，才可取代更早的
状态判断。

| 台账条目 | 记录内容 |
| --- | --- |
| Stage 2C observation foundation | 默认关闭的提交后 capture 及其 crash window |
| 实施前置 / Stage 1 Part 2 | provider-neutral decision 抽取与剩余 Python/TypeScript ownership 边界 |
| Stage 2B PostgreSQL candidate | PostgreSQL store/RLS conformance，不代表 runtime promotion |
| Stage 2C runtime shadow | parity、read-candidate、bootstrap、rollback、cutover kernel 与 writer fence |
| Stage 2 slice | reference aggregate/provider 实现与初步 NoKV 证据 |
| Stage 1 semantic transaction core (#4280) | 共享严格 transaction 解码、clone 隔离、revision 投影，以及 file/NoKV parity fixture |
| Stage 3 slice | 可恢复 lifecycle、retention 结论与 live provider 限制 |
| Stage-ladder evidence | 可执行 stage claim、环境 gate 与 pending row |

#### Stage 1 semantic transaction core（#4280）：共享 transaction 语义核心

file 与 NoKV adapter 现在共同使用
`loopx/control_plane/coordination/authority_store_transactions.ts`。该模块负责
committed transaction 的精确顶层 key 集合、严格 JSON/object-list 校验、canonicalization、
显式 structured clone，以及逻辑 `transactionForRevision` 投影。provider envelope、
storage generation、failure mapping 与 provider-specific revision salt 仍由各自 adapter
负责。这样消除了重复的语义知识，但没有增加新的 authority writer，也没有改变默认的
authority source。SQLite 与 PostgreSQL 的 row/envelope 迁移仍属于后续 provider stage。

公开 fixture 位于
`tests/control_plane_ts/authority_store_transactions.test.ts`，会把 native、reordered
legacy-compatible、unknown-key、malformed-list、malformed-nested 与 non-string identity
记录同时送入 shared decoder 以及当前两个 active provider 的 file/NoKV read path。它还
验证 scan 结果是隔离 clone，并验证 provider metadata 不会进入 logical revision projection。
这些是 Stage 1 parity 证据，不代表 provider promotion，也不代表后续 provider profile
已经完成资格化。

#### Stage 2C 旧提交后观测：已退役

旧 observer 写入已删除。旧配置可识别但不再启用，重新启用请求被拒绝；目录迁移不再
重新创建观测历史。保留文件不删除。需要捕获时显式配置并 bootstrap 现有事务绑定
runtime shadow；旧 observation 不会升级为晋升证据。
[操作与兼容](../../reference/authority-observation-retirement.md)。

#### 实施前置条件：先让本地文件模式经过同一协调合同

在接入 live NoKV 或其他远端 provider 之前，runtime 应先把当前 todo/lease 写路径中的
领域判断抽成 provider-neutral coordination core，并让一个 file-backed provider 通过
同一组 command、precondition、receipt 与 typed outcome 合同。这个重构应先以 shadow
方式对照当前 Markdown active state 和 task-lease 文件，资格化读写 parity、幂等、CAS
冲突、崩溃恢复与一键回退；只有经过单独 review 的 promotion 才能让 file aggregate
成为本地 canonical，并把 Markdown/lease 退为 projection。NoKV 随后复用同一 authority
与合同，只替换 `load` / `compare_and_put` provider。该前置条件不创建覆盖 registry、run
history、quota、scheduler 或 evidence 的通用存储抽象，这些账继续遵守第 3 节的 owner
边界。

#### Stage 1 Part 2 边界

Provider-neutral 的纯 authority core 已通过 #3410 合入 `main`。
这一切片只做行为保持的抽取：把现有 writer 已经执行的 todo lifecycle、task-lease
lifecycle 与 `handoff_mode` 规则收敛进纯决策 core。Markdown goal state 与
task-lease 文件仍是 canonical。此次抽取不会为目前没有 revision publisher 的 todo、
authorization、dependency 或 gate 域凭空制造 revision；也不会把今天相互分开的
claim 与 lease verb 偷换成上文的 atomic `claim_work`。后者属于未来的 shared
aggregate。

Task-lease TypeScript cutover 后，acquire 由 `task_lease_acquire.ts` 持有，renew、
transfer、release 则由 `task_lease_lifecycle_decision.ts` 的纯 seam 持有。Python
`authority_core` 只负责投影 normalized snapshot、调用这些 decision，再重建
provider-neutral `TransitionPlan`。因此，本地 lease-file transaction 与 coordination
executor 消费同一份 lease decision；加锁、source 重验、文件持久化、provider CAS 与
receipt 构造仍分别属于各自 execution layer。初次抽取保留的 Todo、terminal-fence
与 handoff-mode Python 决策由后续切片移入 typed owner；handoff 空闲判断现归属
`handoff_mode_policy.ts`。本地 holder／fence-close 锁仍属于 execution effect。

后续 provider 工作必须始终分开三层：

1. `DomainDecision` 只根据显式 coordination snapshot 与 command，纯函数式地给出
   apply / reject / no-change 判断。
2. Authority execution 及其 result 负责加锁、重验、提交该判断，并最终持有 durable
   semantic receipt。
3. Provider result 只报告 `loaded | missing | conflict | unavailable | failed` 等存储
   observation 与 opaque provider generation；它既不是领域判断，也不是 semantic
   receipt。

Stage 1 Part 2 不声称已经提供 durable semantic receipt 或 A/B/A replay；它们需要
Stage 2 的 aggregate 与 provider shadow。该 aggregate 必须把 `handoff_mode` 当作
真实且由 revision 覆盖的决策输入。Provider 合同绝不能把 `missing` 折叠成
`unavailable` 或 `failed`。`provider_generation`、`authority_revision` 与
`lease_epoch` 始终是三个独立版本域。同样，restore 可以保存 frozen bytes 与
lineage，却不会因此获得当前权威；把恢复状态晋升为 live authority head，必须经过
显式的 lineage 与 binding fence。

#### Stage 2B PostgreSQL candidate 状态（2026-09-02）

首个 PostgreSQL candidate 已实现由 LoopX 持有的 TypeScript store contract，而非
引入第二个语义权威。Store handle 绑定 `(tenant_id, goal_id)`，只接收 service 持有的
database pool。固定的 `loopx_control_plane` schema 将 scoped head、committed
operation、有序 event 与有序 receipt 分开存放。一笔 SQL transaction 创建或锁住
scoped head row，校验 opaque provider revision，以 unique constraint fence
`operation_id`，分配 per-goal cursor，写入 commit/event/receipt，推进 projection head，
最后提交。`COMMIT` 之前的错误会 rollback 并返回 typed `failed`；`COMMIT` 尝试开始后
的错误返回 typed `ambiguous`，只能通过 receipt readback reconcile。Database
incarnation metadata 由行政部署路径安装，不能被隐式重新绑定。

数据库 trust-boundary 切片现在会为每次 provider operation 设置 transaction-local
`loopx.tenant_id` context，并在所有 tenant-scoped table 上同时 enable 与 force
PostgreSQL row-level security。读操作使用 read-only transaction，并在返回前
rollback，因此 pooled session 不会残留上一个 tenant context。缺少 context 时看不到
任何 scoped row；`WITH CHECK` 会拒绝写入 active context 之外的 tenant。资格化所用的
restricted-role profile 只获得 schema usage、metadata read 与 scoped table 所需的最小
权限，因此不能重新绑定 database-incarnation metadata，也不能安装 schema policy。

这只是 service 内部的 defense in depth，不是 tenant authentication。Service 仍持有
database role，并且必须先认证 principal、授权其 tenant，才能选择 transaction context。
Agent 永远拿不到该 role；RLS 也不会让 caller 自报的 tenant id 自动变可信。

本切片还把 strict JSON validation 与 commit normalization 从 file 实现抽到统一的
TypeScript authority-store codec。File 与 PostgreSQL 现在运行同一套 provider-neutral
conformance suite，覆盖 projection-plus-receipt 原子提交、CAS contention、历史 receipt
replay、operation fencing、有序 cursor scan、返回值隔离，以及 malformed JSON 在写前
被拒绝。

已晋升的 `hard_lease` authority 也通过既有 Todo claim caller contract 提供一条可选的
ownership transaction。Caller 传入 task-lease idempotency key 和可选 expected version
后，TypeScript owner 从 canonical head 读取 Todo 及其 required write scopes，复用 typed
lease-acquire decision，并在一次 provider CAS 中共同提交 lease、claim 与 receipt。省略
这些字段会保持既有 claim 行为；未晋升 Goal 则拒绝 atomic-only 参数，不尝试 legacy 写入。
File、NoKV 与真实 PostgreSQL conformance 都覆盖了竞争 owner：只能有一个完整的
claim-plus-lease tuple 获胜，失败方不会得到 receipt，获胜方按精确 operation identity
重放。这是既有合同的一次内聚晋升，不是第二套 `claim_work` 抽象。

PostgreSQL adapter 还会在打开连接之前执行一项 provider-local 资源门禁：canonical commit
envelope 超过配置的 `max_commit_bytes` 时，写入以 typed
`store_capacity_exhausted` 被拒绝。默认值为 16 MiB，部署可以调低。它只是单笔原子操作的
准入上限，不是实测 throughput 证据，也不是 retention/partitioning 设计；这些晋升 hold
仍然开放。

真实 PostgreSQL qualification 从这里开始，而不是等到 shadow 或 canary。一个真实
PostgreSQL 16 实例已通过共享 conformance matrix、同一 head 的并发 CAS、不同 tenant
复用相同 goal 与 operation id、transaction rollback 后不暴露 head 或 receipt、已提交
transaction 丢失响应后的 receipt 恢复、拒绝 database incarnation rebind，以及受限
role 的双 tenant RLS matrix。最后一项证明：缺少 transaction context 时看不到 scoped
row，跨 context 写入失败，runtime role 也不能修改行政 metadata。Fake 仍可覆盖 adapter
分支，但不能证明 row lock、unique constraint、rollback、commit visibility、privilege
或 RLS；因此后续每个 PostgreSQL provider 切片都必须保留真实数据库门禁。

该 candidate 仍是 coverage-only。没有 production LoopX entry point 构造它，本地模式
保持不变，Agent 也不能获得注入的 pool。Service trust boundary 内的数据库
runtime-role/RLS 行为现已实现并完成资格化。Service API authentication、
principal-to-tenant authorization、production runtime caller、restore
incarnation rotation、pool exhaustion/cancellation/failover、retention/partitioning/实测
capacity、单向 shadow parity、TEST ONLY canary 与 authority-source promotion 仍是显式
hold。下一个 PostgreSQL 切片必须资格化 authenticated service/deployment 与 failure
boundary，不能把 database RLS 或单笔 commit 准入上限当成仍缺失的 service 与 capacity
层。

File-backed provider 合同与 executor 属于 Stage 2；其第一个切片已通过 #3529
合入 `main`，证据记录在下方的 Stage 2 状态小节。该切片证明 aggregate 与 provider
边界，但还不是 Stage 2C 的生产 runtime shadow：它没有接入 legacy Todo 或
task-lease writer。

#### Stage 2C 提交后 runtime shadow 状态（2026-09-03）

第一个接入真实写路径的 shadow 切片由以下精确配置显式启用，默认关闭：

```json
{
  "coordination": {
    "runtime_shadow": {
      "enabled": true,
      "schema_version": "loopx_coordination_runtime_shadow_config_v0",
      "provider": "file_v0"
    }
  }
}
```

三个值缺一不可；配置缺失、关闭、格式错误或 provider 不受支持时，legacy 结果保持
不变，并返回 typed disabled evidence。启用后，runtime 遵守以下边界：

- legacy Markdown Todo writer 或 task-lease writer 先成功提交且继续作为 canonical；
  只有 primary mutation 成功后才派发 shadow；
- Python adapter 通过 `todo list` 共用的 canonical read-record 合同投影已提交 Todo
  view，完整保留消费方可见的 identity、文本、优先级、过滤、continuation、resume、
  调度、归档、完成、note 与 evidence 字段；随后按稳定 Todo identity 排序，并把该
  view 与紧凑 lease 一起通过既有 TypeScript effect runtime 发送；
- TypeScript owner 在同一笔 `AuthorityStore` transaction 中写 projection 与 operation
  receipt。写前先查询既有 receipt；同一 operation id 搭配不同 normalized content
  会被拒绝；provider-revision contention 只做固定次数重试；ambiguous commit 只能
  通过读取精确 durable receipt 恢复；
- `applied` 会读回 receipt，并在 provider head 尚未被后续提交覆盖时验证当前
  projection。所有结果都声明 `decision_read_from_shadow=false`；shadow 被关闭、失败、
  冲突或 ambiguous，都不能拒绝、回滚或改写已经提交的 primary result。

跨 runtime 测试从 Todo 与 task-lease 两条 hook 验证真实的 Python -> TypeScript ->
`FileAuthorityStore` 路径，覆盖 default-off、稳定 replay、内容漂移拒绝、ambiguous
commit 恢复、projection read-back 与 shadow failure isolation。这里仅关闭第一个
runtime-shadow 切片。后续 typed inspection seam 会把当前紧凑 legacy projection 与
file head 对比，返回 `missing`、`matched` 或 `drifted` 以及两侧内容摘要。该 seam 默认
关闭、只读，并始终返回 `decision_read_from_shadow=false`；它为 migration 提供可复用
的 baseline/parity observation，但不会把 observation 升格为 authority。

同一显式 opt-in 后面现在也有了下一个 migration primitive：
`coordination.runtime_shadow.bootstrap` 只允许在 file shadow 尚未初始化时安装一份
规范化 legacy projection。第一条已提交 event 持久绑定 source version、source
projection digest 与 `legacy_canonical_shadow` mode declaration；由于尚未执行任何
Agent operation，它刻意携带空 receipt payload。精确重放从第一条 transaction 恢复，
包括提交成功但响应丢失的 ambiguous 情形；如果已有不同 lineage，则 fail closed。
这是后续管理面 migration command 所需的 provider-owned bootstrap effect，但它仍不能
promotion shadow，也不能参与协调决策。

管理面 caller 是显式且 preview-first 的：

```bash
loopx configure-goal --goal-id <goal-id> \
  --coordination-runtime-shadow-file --execute
loopx coordination-shadow inspect --goal-id <goal-id>
loopx coordination-shadow bootstrap --goal-id <goal-id>
loopx coordination-shadow bootstrap --goal-id <goal-id> --execute
loopx coordination-shadow qualify --goal-id <goal-id> \
  --minimum-operations 3 \
  --require-event-kind todo_claim \
  --require-event-kind task_lease_acquire
loopx coordination-shadow promote --goal-id <goal-id> \
  --minimum-operations 3 \
  --require-event-kind todo_claim
loopx coordination-shadow promote --goal-id <goal-id> \
  --minimum-operations 3 \
  --require-event-kind todo_claim --execute
loopx coordination-shadow promote --goal-id <goal-id> \
  --minimum-operations 3 \
  --require-event-kind todo_claim \
  --handoff-mode-migration preserve
loopx coordination-shadow promote --goal-id <goal-id> \
  --minimum-operations 3 \
  --require-event-kind todo_claim \
  --handoff-mode-migration hard_lease --execute
loopx coordination-shadow rollback --goal-id <goal-id> \
  --provider-revision <revision-from-inspect> --execute
```

它从当前 canonical Todo 与 task-lease view 派生紧凑 projection，只报告计数与摘要，
并要求 `--execute` 才调用 bootstrap 或 promotion。`promote` 未带 `--execute` 时零写入；
preview 返回精确的 qualified revision、projection digest、writer-fence identity 和
rollback identity，并返回 canonical promotion-plan digest 及资格策略。该 digest 会绑定
Goal、operation、选定 canonical provider、精确 shadow revision/projection、最小
operation 数与规范化后的必需
event kind；持久 fence、event 与 receipt 都携带同一 digest，因此 fence 已落盘而 canonical
尚未提交的中断只能由完全相同的受评审 plan 恢复。apply 会在同一段 maintenance 与 legacy source 锁生命周期内重新
验证 source snapshot、资格化精确 shadow lineage、engage 持久 writer fence、提交
canonical head，并读回 promotion receipt。未传迁移参数时，v0 继续拒绝尚未资格化为
`hard_lease` 的 Goal。显式 `preserve` 可以只切换 authority 并保留 `legacy`／
`soft_claim` policy；显式 `hard_lease` 可以在同一受评审 cutover 中执行唯一获支持的
policy 升级，并保留 claim 与安全的 lease record。它不会伪造 lease：保留 claim 的
owner 必须在下一次受保护写入前走普通原子路径取得 lease。preview 会展示保留 claim、
lease 处置、冲突与精确目标 digest；mode 意图、registered-agent 集合和目标 digest 都
进入 promotion-plan identity。其他 mode 变化仍受普通静止规则约束。写入成功后会立即
通过 typed parity inspection 读回。除非目标开启精确的 goal-level `file_v0` shadow
opt-in，否则该命令不可执行。

promotion 前 rollback 带精确 revision fence，且不删除数据。TypeScript 会把命中的
file-shadow lineage 移入持久 quarantine archive；精确重试复用 archive receipt，revision
漂移 fail closed，legacy Todo/task-lease source 全程保持 canonical。之后可以从 legacy
source 重新 bootstrap 新 shadow，而无需恢复或信任退役 lineage。

只读 `qualify` action 把单点 inspection 提升为 typed 持续 parity 报告。它采用覆盖式
策略：调用方指定至少需要多少个不同的已提交 operation，以及必须覆盖哪些 Todo/lease
mutation kind。TypeScript 会扫描完整且有界的 lineage，校验 bootstrap、每条
event/receipt/projection identity，以及当前 legacy/file head digest，并返回
`qualified`、`insufficient_evidence` 或 `drifted`。replay 不增加 operation 计数，缺少
覆盖会让 gate 失败，所有结果仍明确声明 `decision_read_from_shadow=false`。

结构 parity 不等于消费语义 parity。因此，可 promotion 的 projection 还必须携带一个
版本化 Todo read-model receipt，其中包含精确字段合同、记录数和 canonical record
digest。TypeScript 会在 qualification、promotion 以及每次 provider-first collection
read 时，对 deterministic provider records 校验该 receipt；schema 缺失、digest 过期、
字段合同被截断、记录数不符或顺序不稳定都会 fail closed。provider-first mutation 必须
在同一原子 head 更新中重算 receipt。新增 Todo 消费字段属于合同变更，必须重新
qualification，不能宽松 fallback。

任何真实 Goal promotion 前，还必须在同一个 revision 上分别让 legacy source 与
provider round-trip 经过既有消费 pipeline。语义矩阵至少覆盖：user/agent role；open、
done、blocked、deferred；优先级与排序；claim、exclusion、bound-agent、global-gate
过滤；resume condition；successor/continuation；continuous monitor 的 cadence、due、
watch-only 与 material generation；note、evidence、completion、archival；以及 Markdown
文件不可用后的 provider-only read。任一差异都阻止 promotion，并保持 legacy authority。
synthetic stub 可作为单元测试，但不能替代真实复杂 Goal qualification。真实 Goal 内容
只保留在本机；公开证据只包含脱敏 coverage、计数、精确 revision 标识与 digest。

下一个只读 seam 会演练未来 read flip 所需的 provider 读取形态，但不授予它 authority：

```bash
loopx coordination-shadow read-candidate \
  --goal-id <goal-id> \
  --todo-id <todo-id>
```

TypeScript 会读取 file head，要求其 digest 与当前完整 legacy coordination projection
一致，校验 Todo identity 唯一，再返回精确的紧凑 Todo、provider revision 与 cursor。
provider 缺失、漂移、结构非法或 Todo 重复都会 fail closed。结果刻意保持
`decision_read_from_shadow=false`：它只证明 parity 匹配的 provider 能回答一次精确
Todo 读取，尚无 lifecycle 或 settlement 调用方消费这个答案。正式 promotion 仍必须
把 provider-first read flip 与 legacy-writer fencing 作为同一个受审原子边界；flip 后
回退 Markdown 会重新制造双权威，因此禁止。

受评审的 operator path 现在把 provider-first read flip 与 legacy writer fence 收进同一
个 TypeScript-owned cutover transaction；任何 read、delegation、frontend 或 Lark 路径
都不能隐式调用它。真实 Goal 仍必须分别完成精确 qualification、静止态 `hard_lease`
切换、preview、显式 apply、重启读回和 managed-worker acceptance，之后才能宣称该 Goal
已经 promoted。因此 NoKV/PostgreSQL 远端 shadow 仍属于 Stage 3，不能把这个默认关闭的
hook 当作 authority。

只读消费者现在复用一份显式的 authority-transition 投影。managed delegation preflight
及其打包 Dashboard 展示会区分 `promotion_required`、`unavailable` 与 `promoted`，并给出
typed next action，但不会启动 Turn 或执行器。Lark 使用的 Goal Channel 投影遵守同一边界，
继续通过现有 `mode=read_only` 与 truth contract 声明
`projection_is_writable=false`、`write_authority=none`，renderer 再为 operator 派生对应的
next action。只有经评审的 TypeScript preview 才能证明 readiness。这补齐了解释链路，
同时不会在每份 status payload 重复相同的来源／权限事实，也不会把 Chat 或 Lark 变成
第二个 promotion owner。

下一块 Stage 2C 实现加入了 TypeScript cutover kernel，但尚未改变默认 runtime。
同一个纯 reducer 从一次 Todo/lease mutation 派生 projection、event 与 receipt；显式
promotion 必须同时验证 qualified shadow 的精确 provider revision、projection digest，
以及独立持久化、绑定到同一 revision 的 legacy-writer fence。该 fence 提供共享的
fail-closed write-check hook；promotion 可按 operation receipt 重放，provider-first read
与 mutation 也绝不回退到 Markdown。在 Python Todo/task-lease 入口真正调用这个 hook
并选择 promoted mode 之前，这些仍只是切换机械结构，不代表生产权威源已经翻转。
后续必须接齐所有 legacy writer、显式配置与 rollback，并证明默认 legacy 兼容性，
才能启用本地 promotion。

第一块生产 fencing 集成现在已经收口了上述工作中的写入侧：所有 Python Todo mutation
都会在持有现有 active-state mutation lock 时检查 TypeScript 权威拥有的 durable fence；
原生 TypeScript task-lease 的 acquire / renew / transfer / release 也会在持有 lease lock
时检查同一个 fence。fence 不存在时保持零 runtime 调用的默认兼容路径；fence 存在、
不可读或不合法时一律 fail closed。后续 promotion orchestrator 必须先取得这两把 legacy
lock，再 engage fence，从而保证不存在某次 legacy write 已通过检查、却在 cutover 后才
提交。持锁的 `coordination-shadow promote` 路径现在完成了这一切片。剩余验收属于逐
Goal 的 qualification 与产品投影，而不是第二套 Python promotion state machine；若
发生 fence 已写入但 canonical readback 不成立的中断，会 fail closed 等待 operator
recovery，绝不静默回退。

Todo collection-read 切片只在 durable fence 已存在时把 `loopx todo list` 路由到
`FileAuthorityStore`。它与 legacy 路径复用同一套过滤、排序、resume 和 summary
pipeline；Markdown 缺失时仍可执行 provider-only read，并在响应中携带 authority
receipt。provider 缺失、损坏、协议漂移或 Todo read-model 语义漂移都会 fail closed；
cutover 后禁止回退 Markdown。

完成续接（continuation）的持久化读回
（`durable_completion.py`：`read_persisted_todo_record` /
`project_durable_completion_outcome`）是一个 provider read point：它在完成写入之后、
settlement 之前重新读取已落盘的 lifecycle record（Markdown 优先，event projection
兜底）。落盘记录带有显式的 `completion_continuation`；done 记录缺少该字段、或该字段
与 successor / no-follow-up 字段矛盾时，seam 都 fail closed，因此持有完成状态的
provider 必须逐字节保存这个字段。一旦远端 provider 成为 canonical，这个 seam 翻转为
provider-first，且不改变下述 typed outcome 合同。

#### Stage 2 切片状态（2026-08-23）

第一个 Stage 2 切片已通过 #3529 以增量方式合入 `main`：

- `loopx.control_plane.coordination.head`：`loopx_coordination_head_v0`
  aggregate 编解码。校验对 executor 后续无条件解引用的每个字段都是封闭
  字段集且 fail-closed 的：todo、lease 记录、以及每条 receipt-index 条目
  （条目形状、digest 形式、receipt schema、operation 身份、todo 归属、
  revision/epoch、带时区的 UTC 时间戳（naive 值会随执行主机时区漂移，fail closed）），包括伪装成整数的 bool；`handoff_mode`
  是 head 的记录字段，v0 钦定为 `hard_lease`（`soft_claim` goal 在
  bootstrap 处 fail closed，而不是被静默反转其声明语义）。规范字节被定义
  为按键排序、最小分隔符、UTF-8 的 JSON，且拒绝非有限浮点数；digest 与
  provider 字节级 parity 都以这一编码为基准，"deterministic serialization"
  因此是合同条款，而非实现巧合。Markdown shadow 构造器独立为桥接模块
  （`goal_state_shadow`），使编解码模块的 import 闭包留在仓库的 strict
  类型门之内。
- `loopx.control_plane.coordination.file_provider`：一个 goal 一份文档。
  锁经由 `loopx.file_lock`——仓库唯一的跨平台锁 owner——并带其有界等待
  （超时未获锁是 typed `failed`，因为尚未尝试任何写入）。持久化是固定的
  提交序列：规范字节 write-all（短写会被继续写完，绝不忽略）、文件
  fsync、原子 rename、POSIX 上再对父目录 fsync；只有整个序列收敛后才返回
  `applied`，序列内任何存储故障都报告 `ambiguous`。无法忠实序列化为严格
  JSON 的 head 在任何字节落盘之前报告 typed `failed`。
- `loopx.control_plane.coordination.executor`：`claim_work` 的第 5 节步骤
  1-10。所有领域决策都委托给 Stage 1 core；其中 lease acquire 通过 typed Python
  adapter 到达 canonical TypeScript decision，claim decision 仍留在 Python core。
  组合顺序是先 lease acquire、再
  过 hard-lease holder gate 的 claim——claim-first 或 legacy 模式的组合会
  静默绕过附录 B 的 holder gate（测试钉住了这一点）。`lease_ttl_seconds`
  受本地 task-lease authority 自身上限约束：共享 envelope 铸不出本地合同
  会拒绝的 lease，无界的调用方数值也不可能以时间戳运算溢出的形式逃逸。
  Provider 的 `failed` 判据经重载 receipt index 核查而非盲信，误报已落盘
  写入的 provider 无法制造"调用方被告知失败"的幽灵 claim。

设计选型是对比出来的：三个 executor 候选（core 委托、内联规则参考实现、
同文档内 journal 式回执日志）跑同一场景电池。只有 core 委托的候选能在不改
本地代码的情况下跟随 core 规则翻转；journal 编码在 `retain_all_v0` 下也
省不出有意义的字节。最终交付的是 core 委托候选。

Live 资格验证在 0.11.0 标签的单节点 NoKV dev 栈上通过其 Python SDK 完成：
`examples/nokv-shadow-provider/live_e2e.py` 的八场景不变量矩阵对 file
provider 与 NoKV provider 逐行结果一致（同 todo 竞争恰一胜者、独立 todo
推进、精确重放、identity 不匹配、陈旧 revision 冲突、丢失响应经 receipt
index 恢复、回执保留、authority_revision 推进）；对 serving owner 的
SIGKILL 加运维式重开保持了 head 字节级一致，经全新 executor 精确重放原始
receipt，并在三个版本域上恢复 CAS 推进。renew/release/reclaim、retention
策略、HA 与多节点部署仍未验证，与后续资格清单一致。

Stage 3 必须尊重而非重新发现的实测边界：

- `retain_all_v0` 在单 CAS 文档内的成本约每 receipt 750 字节；dev 栈上
  claim 延迟在前 120 个 receipt 内增长约 7 倍，累计重发布字节呈平方增长
  （120 次 claim 后约为最终 head 体量的 84 倍）。第 12 节的 retention 决策
  在任何生产 canary 之前都是承重项。
- 12 个并发独立 todo claim 下，内部 rebase 预算放行 8 个，其余返回 typed
  失败。第 1 节的独立性承诺在两个端点成立、超出即退化；受支持的并发包络
  与重试预算应在多 agent canary 立项时进入验收检查。
- NoKV 文档 generation 在 remove/recreate 之后、以及 restore 进新 workbench
  lineage 之后会重新起算。携带陈旧 generation 观测的条件替换在重建路径上
  被实测成功；恢复线会重新到达 generation 数值相同、receipt 却不同于原线
  的状态。这坐实了上文边界句：仅靠 `provider_generation` 无法承载 restore
  或生命周期身份；lineage/binding fence 需要在下一阶段成为显式的 provider
  合同字段。
- `provider_outcome_unproved` 是 live 运行中真实出现的终态（无故障注入即
  观测到）。经 receipt index 的重放让重新提交幂等，因此同 generation 的
  有界重试是安全的；v0 是否采纳该活性修正是 owner 决策，在此记录而非静默
  实现。

#### Stage 3 切片状态（2026-08-26）

可恢复执行 horizon 行已以增量方式存在于本分支：§5.2-5.5 的四个动词与 5.6 的
stale-fence 规则、6.4 的过期裁决与 store-lineage 绑定围栏、按动词的 receipt
schema、head 编解码的条件完成校验（status 词表钉为 open|done，此前未校验）、
以及拒绝"正确栅栏落错人手"的 holder gate。所有领域决策仍委托 Stage 1 core；
reclaim 组合经对真实 core 的三方案 battery 选出（朴素 acquire-first 组合死于
owner_conflicts_with_claim；胜出形态先做最小特权的委托 unclaim、再走普通 claim
组合，新 lease 因此通过真实 holder gate）。

Live 资格验证（单节点 NoKV dev 栈，0.11.0 release wheel）：十二个共享场景行在
file 与 NoKV provider 上逐行一致，含 renew、带宽限窗的 reclaim（记录被替代
owner）、被替代执行者写入被终态栅栏、以及 completion 原子创建可认领
successor；一个 NoKV 专属行对**真实** commit/snapshot/restore 证明绑定围栏——
恢复出的 workbench 以 store_lineage_mismatch 拒绝一切命令，原 workbench 照常服
务。对 serving owner 的 SIGKILL 于生命周期中段注入后约 61 秒恢复（60 秒会话租
约排空）：head 字节级一致、续约 receipt 经全新 executor 精确重放、崩溃后的
reclaim/栅栏/completion 链在重开的存储上走完。时钟边界测试逐边钉住宽限窗。

实测门禁更新：

- 并发包络（较 Stage 2 数字增长）：本栈上 K 个独立 claim 到 K=8 全部成功
  （K=2/4/8 的 p50 为 2.8 / 10.2 / 50.7 秒）；K=16 放行 8 个，其余在 8 次尝试
  预算上 typed 失败，尾延迟约 150 秒。canary 的受支持包络声明为 K<=8 加上实测
  延迟曲线。
- 留存：§7.1 的封存段修正案已附 live 对比证据提出（head 缩小 19 倍、重发布少
  7.5 倍、延迟平坦、+3% 往返、已封存回执恢复约 160 ms）；切片本身在 owner 决
  定 Q5 之前运行于 retain_all_v0。
- 独立复跑暴露两个 NoKV 存储面缺陷，均如实上报而非静默绕过：(a) 重开抖动可把
  logical-shard recovery publication 楔死在死租约的 epoch 上，此后所有接管尝试
  恒以 "stale lease" 被拒，需运维介入；(b) SIGKILL 落在元数据写窗口内可损坏存
  储 manifest（FileBlobStore duplicate slot），重开永不成功——两种情况下上层协
  调语义保持正确（无假权威），但可用性在任何生产 canary 前依赖这两处修复。

评审驱动的加固（2026-08-27）：reclaim 宽限配置边界 fail-closed（§6.4；此前
NaN 或负宽限可夺走活跃 lease）、evidence pointer 收紧为带隐私 namespace 的
`artifact://` URI 加闭合词表（§5.5；此前主机路径或隐私错配可持久进共享 head）、
head schema 版本化
为 `loopx_coordination_head_v1` 并附显式 v0 迁移路径（§6.4；此前 Stage 2 head
以未分类方式失败）。
该迁移现在会用 retain-all receipts 重建 Stage 2 的单命令历史：live claim 必须与
保留的 actor、todo revision、lease id、epoch、expiry 及连续 authority revision
序列逐项一致。部分损坏或被编辑的 v0 head 因此会按 corruption fail closed，而不会
获得新 store binding、重用 epoch，或授权一个无 receipt 证明的 holder。

交付边界，明确声明且已于 2026-09-01 获 owner 接受：本切片是 RFC 的参考实现，
附确定性与 live 示例证据。尚无 LoopX 生产入口构造该 executor——这些模块在
visible governance 台账中属 coverage-only。该接受允许内聚的 reference-contract
切片在正确性、rebase 与 review 门禁通过后合入；并不把它晋升为已 ship capability。
真实 caller 依赖下文经评审的 shared-mode migration、本地 writer
围栏、authorization publisher、provider binding、projection 翻转、rollback 与
retention 决策，不能用一个会制造第二 writer 的诊断 CLI 代替。本状态节声明的是已
证明的合同，不是已 ship 的生产能力。

#### Stage ladder 端到端证据（2026-09-03）

历史交付记录：下述旧 observation writer 验收行已于 2026-09-24 退役。
[当前验收梯子](../../../examples/shared-goal-authority-e2e/README.md)改为事务捕获读回与
显式升级验收；不要求退役 writer 继续写入。

本分支上存在一条增量式端到端 "stage ladder"：它通过真实的
`python -m loopx.cli` 逐行演练本 RFC 每个已完成阶段的声明，并按行给出可机器
判定的结论：`loopx/control_plane/testing/authority_e2e_ladder.py`（行注册表、
runner、`loopx_shared_goal_authority_e2e_report_v0` JSON 报告、退出策略与隐私
扫描）、`loopx/control_plane/testing/authority_e2e_fixtures.py`（goal 工作区、
CLI runner、observation-lock 窗口、候选回读）、只读 TypeScript 探针
`tests/control_plane_ts/authority_store_readback_probe.ts`、pytest 投影
`tests/control_plane/test_shared_goal_authority_e2e.py`，以及入口
`examples/shared-goal-authority-e2e/ladder.py`。

按阶段，本增量实现：

- Stage 0：`s0.file_matrix_twelve_rows` 运行保留的 live matrix 脚本，要求 file
  provider 上恰好十二个共享场景行全为 true；`s0.nokv_live_matrix` 要求 live
  NoKV 栈上同样的行加 `restored_lineage_fails_closed` 全为 true，且 file/NoKV
  逐行结果一致。
- Stage 1：`s1.cli_document_decodes_through_ts_store` 通过产品 CLI 写入三次
  observation（`todo add`、`task-lease acquire`、`todo update`），再经
  `FileAuthorityStore` 的 `loadAuthority`、分页 `scanCommitted` 与
  `readReceipt` 回读：cursor 为 `3`、三个 operation id 按序一致、首条 receipt
  可找到。
- Stage 2A：`s2a.nokv_live_qualification` 对一个已存在的 workbench 以新铸的
  tenant/goal 运行已合并的 live 资格探针
  （`examples/nokv-authority-store/live-qualification.ts --execute-live`），要求
  `ok=true`、单节点 store conformance 范围、每项 check 通过、NoKV SDK `0.11.0`
  / API `1`，且不宣称晋升或可用性。
- Stage 2B：`s2b.postgresql_conformance_live` 在 node TAP reporter 下运行
  PostgreSQL 集成测试文件，要求至少九个 pass、零 fail、零 skip。
- Stage 2C 观察基础：七个 `s2c1.*` 行移植本地 shadow CLI E2E 与迁移断言，并钉住单一
  lineage 保证。
  configure 往返先预览、再开启、回读、最后关闭 observer；每个保留的 writer family
  （handoff-mode、todo add/update/complete/supersede/
  archive-completed、task-lease acquire/renew/transfer）都以
  `primary_writeback_preserved`、`provider_to_local_writes=false`、
  `candidate_read_for_decision=false` 完成 capture，而幂等 re-acquire 不产生
  observation；default-off goal 保持隔离；候选失败不推翻主写；POSIX SIGKILL
  落在崩溃间隙时只丢失该次 observation；`--runtime-root` 与 `common_runtime_root`
  不同时，两次 todo add、task-lease acquire、todo update 与带 lease 的
  complete 仍落入同一个 store identity，registry root 既不产生候选 lineage 也不
  产生 lease 状态；`migrate-state` 在不携带 legacy 字节的前提下建立新 lineage。
- Stage 2C parity 后半段：十个 `s2c2.*` 行只通过公开 CLI 驱动一个显式开启
  `coordination.runtime_shadow` 的 goal，并且只经 `authority-shadow status|drain`、
  `coordination-shadow bootstrap|inspect|qualify|read-candidate|rollback` 与
  `migrate-state` 断言，历史只经保留的 TypeScript store 读回。Python Todo writer
  与 TypeScript lease writer 各留下 prepared 记录与 committed 标记，一次 drain 恰好
  投递一次；有界 drain 可累积、空转 drain 不改变任何东西、writer 重放不铸造条目；
  主写 replace 前后的 SIGKILL 结算为 `abandoned` 或 `committed_proven_by_readback`，
  inline drain 内的 SIGKILL 由精确 receipt 恢复且不会二次投递；rollback 归档 pending
  条目、把 capture 置于 `bootstrap_required`、重新 bootstrap 出新 lineage 并可重放；
  三轮交错 writer（add、note update 及其无变化重复、显式 exclusion 设置与清除及其
  无变化重复、acquire、renew、transfer、带 lease 的 complete 与 supersede 及其
  fence close，以及第二次 add）让每次有界 qualification 都保持 matched，且
  `sustained_parity_verdict=not_evaluated`；
  直接改主文件会报告 `shadow_projection_drift`，其后的写入以
  `source_partition_continuity_unproved` 挂起，只有 rollback 加重新 bootstrap 才能恢复；
  event-only Todo 来源让 `inspect`、`qualify`、`read-candidate` 以
  `event_log_writer_not_bound` 失败关闭而主写继续提交；`migrate-state` 对处于
  active capture 的来源以 `shadow_source_replacement_requires_rebootstrap` 拒绝，
  直到 rollback 并关闭 capture 之后才执行，迁移后的 goal 重新 bootstrap 出新 lineage
  并完成 drain；十笔事务度量 file-v0 历史增长：完整投影全部保留、每笔增量最多增加
  一条 live 记录，不宣称任何容量水平线。

Live 行按环境门控（`LOOPX_TEST_POSTGRES_URL`；`NOKV_COORDINATION_LIVE=1` 加
`NOKV_*` 栈变量；`LOOPX_NOKV_AUTHORITY_LIVE=1` 加 `LOOPX_NOKV_AUTHORITY_*` 输入）。
没有栈时它们报告 `unverified`，除非传入 `--allow-unverified`，否则 ladder 以非零
退出；unverified 行永不计为 green。pending 行同样是未兑现的义务：选中它而不传
`--allow-pending` 就非零退出，所以一份零执行的报告不可能显示为 green。
报告绑定 LoopX commit、工作树是否 dirty、探针 digest 与经哈希的连接事实；其隐私
扫描会把任何临时根目录、home 目录、连接 URL 或配置路径的泄露改写为
`fail/privacy_violation`；仅出现在 bindings 块的泄露会被抹除并同样判定为失败，
`summary.privacy_violations` 阻止 green 退出，任何开关都不能放宽。

交付边界：test-only。没有任何生产入口构造任何 store；ladder 不新增产品路径，
只经保留的 TypeScript store 读取候选。Stage 2C parity 后半段由上述十一个
`s2c2.*` 行执行；只有一条声明保持 pending。`s2c2.archive_after_leased_completion_parity`
原先是 parity 行暴露的 capture 缺口声明，现已成为可执行的确定性行：parity 后半段
在对 Todo 分区做折叠时套用与 source 投影同一条当前图规则，因此对持有已释放 lease
记录的 Todo 执行 `todo archive-completed` 后，候选 head 仍保持 matched，而不再报告
`shadow_projection_drift`。
`s2c2.sustained_parity_soak` 是由 7.2 节与车道 L 负责的 >=10 天合成 goal soak，
有界 qualification 继续报告 `sustained_parity_verdict=not_evaluated`。本小节记录
的是上述阶段的可执行证据；它不晋升任何 provider，也不完成 Stage 2C promotion。

### 11.3 剩余验证与晋升计划

#### P0：合同与 deterministic proof

- 本 ownership matrix 与显式 shared-mode boundary；
- 确定性的 `loopx_command_v0` normalization 与 request digest；
- 显式 bootstrapped todo 上的 `claim_work` authority transition；
- 单 head、state-plus-receipt CAS；
- target/precondition-scoped conflict 与无关 head 前进后的内部 CAS rebase；
- 同一 seam 后的 deterministic 与 NoKV provider candidate；
- 在所声明证据边界内的 A/B/A、identity mismatch、crash window、eligibility、
  privacy 与 no-GC 检查。

#### 后续 runtime promotion 与需 review 的切片

- §6.2 所述由 LoopX 持有的 TypeScript transaction/store boundary 与 file-provider
  conformance；
- 在同一 boundary 后继续完成 NoKV qualification，以及 PostgreSQL 剩余的 service、
  failure 与 promotion hold；
- 单向 shadow parity、一个 Goal/两个 Agent 的 TEST ONLY canary，以及不保留长期
  dual-write/dual-master 的单一权威源切换；
- 显式 shared-mode migration 与 rollback/export、本地 writer 围栏、provider
  binding、production authorization-projection publisher，以及 provider-first 的
  status/completion projection；
- transfer 与受限 delegated assignment；
- 经 Agent IM 的 delivery/wake integration；
- 独立 run-history synchronization 与 artifact storage；
- distributed quota reservation/accounting；
- provider promotion、authentication、service recovery、HA 与 multi-tenancy；
- `retain_all_v0` 之后的 receipt retention 或 segmentation。

#### TypeScript 优先的减负顺序

如果一项前置 TypeScript 工作能消除下一阶段 shared authority 在 provider 压力下还要
迁移的重复权威，就应优先做，但顺序必须保持收敛：

1. 先刻画 Python 路径对 caller 可观察的行为与非法 transition；
2. 每次只把一个已交付的 ownership transaction 移入既有 TypeScript boundary，先做
   atomic claim-plus-lease，再做 completion-plus-successor，最后收敛剩余 lease
   lifecycle decision；
3. 改变 provider selection 之前，让该精确 transaction 同时通过 file、NoKV 与真实隔离
   PostgreSQL server 的 qualification；
4. 最后才推进 binding、migration、canary 与 production promotion。

这不是 broad framework rewrite 的许可。只有下一阶段 RFC 会直接消费、能够移除重复
decision authority，并且 caller-visible parity 与 rollback 能在同一有界切片内完成 review
的前置重构，才进入这条顺序。

## 12. 还需要 Owner 决定什么

1. 下一个 runtime slice 是否先闭合 renew/release/reclaim 与 stale fencing，还是与
   atomic complete-with-successor 一起 qualification？*拟议答案（Stage 3 切片）：
   一起交付、内部有序——lifecycle 动词先落进命令面，completion 原样复用其栅栏机
   制；完成记录必须满足本地 durable-completion 投影 seam，而后者在本地侧已是经
   评审的合同。*
2. 哪些紧凑的 project-registry authorization field 构成 versioned authority input，
   谁可以发布新的 authorization projection？
3. 第一次 shared-mode write 后，reviewed rollback/export procedure 是什么？
4. 哪个 provider 与 deployment 为第一次 bounded shared-mode canary 提供资格？
   Provider 选择不改变 authority contract。
5. Production 使用前，什么 retention 与 capacity policy 可以替代或落实
   `retain_all_v0`，且不丢失历史凭证？
6. Stage 4 canary 在准入前是否要求 Host lease liveness，还是把它留作 Stage 5
   promotion hold？第 11 节要求 canary 观察"外部 effect 不重复"，但运行时间超过
   lease TTL 的 Host 会在另一个 Agent reclaim 该 Todo 之后继续产生 effect，事后
   拒绝 settlement 也无法撤销它们。#3820 的评审把这一点当作 Stage 4 前置条件；
   RFC 必须写明要求哪种机制以及落在哪一层：Host 运行期间续约并在 fence 丢失时取
   消 Host、可恢复协议内的 effect-owning fenced commit，或把 Host 时长硬性限制在
   TTL 之下。*拟议答案：任何运行真实 Host 的 canary 都必须在 Host 执行期间续约并
   在 fence 丢失时取消，其验收必须包含长 Host 过期/reclaim 负例；有界的 fake Host
   不能作为这一行的证据。*
7. canary 的 authority provider 如何绑定到 Goal？用 CLI 参数指定任意 guard 命令只
   是测试 harness 的便利，不是产品级 authority selector。*拟议答案：绑定是一条
   goal 级 registry 记录，由问题 2 中发布 authorization projection 的同一 owner 发
   布，记录 provider 种类、store identity 或 lineage，以及显式的 TEST ONLY canary
   标记；没有该标记的 Goal 不得被 shared-authority guard 准入，runtime 从该记录而
   非 argv 解析 provider。*
8. promotion 提交哪种 head 形状，aggregate 拥有哪些字段？`main` 已将
   `loopx_todo_canonical_read_record_v0` 定义为带版本的完整 Todo read-record manifest，
   TypeScript projection 也会拒绝通过 upsert omission 丢掉既有字段。*拟议答案：晋升
   head 保存完整、规范化的 Todo 与 lease record，并打平到 provider 无关的 readback
   形状。任何已合法进入 canonical record 的字段都继续保留，包括 `updated_at`，以及
   routing、capability、decision、dependency、resume、monitor、completion、
   note/evidence 与 archival 字段。遗漏字段不等于删除；mutation 必须按 schema 规则
   显式 clear。新字段必须先进入带版本 manifest，未进入则 qualification/promotion
   fail closed。“完整”不等于复制 raw Markdown、host-local path、credential、整个
   registry，或其他 ledger 持有的数据。*

   *后续任何字段删减都是受治理的 schema change，即使该字段已经存储但暂未发现 runtime
   reader。对应 PR 必须提供字段 inventory、producer/reader/writer 与静态引用调研、
   历史和外部兼容性结论、migration/rollback，以及行为等价证明；maintainer 必须在 RFC
   decision log 或 PR review 中对点名字段显式批准。没有发现 consumer 不等于批准删除。*
9. v0 promotion 是否只覆盖 `hard_lease` goal？*已决议：向后兼容的默认路径仍要求
   源端已资格化为 `hard_lease`。受评审的 operator 可以显式选择 `preserve`，在不改变
   policy 的情况下 canonicalize `legacy`／`soft_claim` Goal；也可以显式选择
   `hard_lease`，在 fenced cutover 内完成唯一获支持的 claim-preserving 升级。该路径
   不伪造 lease；其他 mode 变化继续使用附录 B 的静止规则。*
10. provider-first read flip 后，Markdown 与 lease 文件成为投影，kernel 禁止回退。
    哪些数据进入 head，兼容视图如何渲染？*拟议答案：canonical Todo/lease manifest
    中的每个字段都持久化在 head，包括 monitor、dependency、resume、decision、
    completion、text、note、evidence reference，以及 manifest 已准入的 feedback 字段。
    事务绑定的 projection outbox 只负责渲染 Markdown 与 lease-file 兼容视图，不是
    遗漏 authority 字段的第二持久化路径。reader 用 projection watermark 对比 head
    revision，落后则回放
    outbox。渲染延迟可以让视图暂时陈旧，但不得改变决策。*
11. 什么声明一个 goal 已晋升，谁可以写这条声明？已合并的 TypeScript-owned file
    fence 是第一个本地实现，绑定 fence id、源版本与已资格化的 shadow revision；当前
    Todo 与 task-lease writer 在各自 mutation lock 内检查它。*拟议答案：定义一份
    provider-neutral authority binding，包含 provider profile、store identity 与
    lineage、schema manifest、`promoted_at`、promotion operation id、源 digest 和可选
    `rolled_back_from`。只有 promotion orchestrator 与 rollback operation 可以修改。
    file profile 用持久本地 fence 和 registry discovery copy 实现；NoKV 与 PostgreSQL
    必须在各自通过资格验证的 store contract 中实现相同的逻辑 fence、CAS/transaction
    precondition 与 readback receipt。provider-specific path 或 table name 不属于
    authority protocol。`configure-goal` 拒绝编辑 binding，bootstrap 拒绝已晋升 goal。
    无法验证 active binding 的端点 fail closed，不得写 legacy projection。*
12. 哪些 retention、fast-path 与 capacity 规则是 promotion 前置？已保留 transaction
    能否把 projection 替换为 digest？*拟议答案：file、NoKV、PostgreSQL 共用逻辑合同：
    在声明的 retention version 下，最新完整 head、ordered cursor、原始 operation
    receipt、segment 或 row-chain integrity、确定性 scan 与 recovery readback 始终可用。
    只有在完整 canonical head 仍可读取，且 replay、audit、parity、migration 所需字段
    全部可重建，并由 conformance matrix 证明等价时，旧 transaction 的重复 projection
    才可替换为 digest。物理策略按 provider 实现：第 7.2 节优先嵌入式事务本地存储，
    分段文件作为比较候选；NoKV 使用经过验证的 document/segment 策略；PostgreSQL
    使用 append row 和经评审的 index/partition。最低十天资格门也由第 7.2 节维护，
    先前 file-v0 证明不足以满足。每个 profile 都声明实测上限，并以 `store_capacity_exhausted` fail
    closed；单一 file size 常量不是跨 provider 合同。Host renewal 仍必须是 authority
    transaction，因为它的频率决定所有 profile 的 retention envelope。*
13. Python 参考执行器（`executor.py`、`file_provider.py`、`head.py`、
    `goal_state_shadow.py`）如何处置？*拟议答案：在 kernel 的 mutation 路径接到
    CLI 之前保持 coverage-only，先把它们的场景电池移植为 TypeScript 测试，再在
    promotion PR 中删除；两种本地 aggregate 格式不能同时为准。file profile 的
    `qualification_holds` 翻为 `[]` 与 `stage` 字面量的改变只在该 PR 内发生。*
14. 哪条 shadow lineage 保持可写？*本次退役收敛：唯一可写 lineage 为
    `coordination.runtime_shadow`，复用已有的来源锁内 prepare 与稳定 entry identity
    投递。删除旧 `coordination.authority_shadow` writer；历史记录仍可读，但不能证明
    捕获或晋升资格。尚未绑定事务边界的事件 writer 继续拒绝，删除观察器不等于关闭
    事件捕获缺口。*

---

## 附录 A：这版证据能证明什么

Reference provider 与 probe 位于
`examples/nokv-shadow-provider/`，并有
[配套证据文档](./shared-goal-authority-state-provider-v0-evidence.zh-CN.md)。本 PR 的
deterministic candidate 证明 claim/receipt core 与 Stage 3 reference lifecycle：
state 与 receipt 的 same-CAS、并发 claim、A/B/A 原始 receipt 重放、
renew/release/过期 reclaim、stale-fence writeback rejection、原子
completion/continuation、request-digest mismatch、时钟边界、血统绑定，以及版本域
相互独立。声明的单节点 live 范围还资格化 file/NoKV parity、restart receipt replay
与真实 restore-lineage fence。它不实现或认证 production authorization-projection
publisher、保留 receipt 的 compaction、默认模式 parity、产品 shared-mode
migration/promotion、service recovery 或 HA。
因此，
`python3 examples/nokv-shadow-provider/probes.py contract` 通过并不表示上面的完整 P0
验收门通过。历史 latency 或 fault 结果只具有参考意义，不构成 durability、recovery、
HA 或 production qualification 声明。

`examples/nokv-authority-store/` 还包含一个 TEST ONLY 的 Stage 2A probe：它会
打开三个相互独立的 SDK helper 进程，验证 fresh create、精确 generation update、
一次 CAS 已落盘但响应被刻意丢弃后的回读 reconciliation、两个竞争者恰一胜出的
CAS、胜负双方的 receipt 行为，以及新进程对 receipt 与完整 history 的回读。该可
执行入口把 argv 固定为一个绝对 Python executable、解释器隔离标志 `-I`，加本
checkout 中经过评审的 helper，因此 `PYTHONPATH` 无法替换 `nokv` 模块；helper 只
接受 NoKV 0.11.0 / Python API 1，report 中重复的是这两个准入常量，而非从服务端读
回的值。它把 read metadata 与当前
workbench incarnation 对照，并针对请求的 workbench、path、operation、revision、
generation 校验 publish 回包。即使 publish 回包报告成功，AuthorityStore 也必须
重新读取并证明当前 workbench incarnation 下持久化了完全相同的 transaction，才会
接受成功。这在 LoopX 边界消除了错误成功，但 NoKV 当前 Python API 尚不能把
expected workspace incarnation 原子绑定到 `publish_bytes`；阻止 concurrent
remove/recreate 后的写入仍是明确的 provider-contract hold。只有成功的 live JSON report 才是该次单节点 Stage 2A store-conformance
运行的证据；确定性测试只证明场景序列。这个纯 LoopX 候选既不修改 NoKV 源码，也
不改变其 workbench/artifact 数据模型；它不证明 runtime shadow parity、multi-Agent
canary、authority-source promotion、HA、重启恢复、容量或生产路由。

## 附录 B：交接模式决策记录（2026-08-10）

本附录把 PR #2787 评审中已同意的方向落成文字，作为实施前置条件的一部分。
只改文档，不改任何运行时行为。

### 起因

用真实 CLI 验证时复现了一个事实：领任务的两本账可以各说各话。一个 agent 先拿到
某个 todo 的硬租约，另一个 agent 之后仍能软认领同一个 todo，两边都成功。分叉之后
的实际行为更关键：

- 后来的软认领会让先前的活租约直接失效：持约人续租、重新领取都被拒绝；
- 完成栅栏恰好在这种矛盾状态下自动失效：软认领方不带钥匙就能完成；
- 持约人反而完不成：授权检查先看软认领，租约凭证根本轮不到被检查。

也就是说，今天两本账一旦分叉，软认领方全胜，租约不单独构成执行权。第 4 节中
"执行权由 lease/fence 表示"描述的是目标合同，正文已随本修订改口。

### 决定

不做两本账之间的互相同步，改为每个 goal 声明一种交接模式（`handoff_mode`），
用模式把两种领法隔开。字段暂放在 goal 状态文件头部（front-matter），跟着文件
跨端走；共享模式上线后由共享权威托管。三个取值：

- 不填或 `legacy`：与今天完全一样，两种领法都开放。分叉的口子在此模式下仍然
  存在；这是有意保留的默认值，不是疏漏。
- `soft_claim`：该 goal 只用软认领。acquire/renew/transfer 被拒绝；release 与
  inspect 保留，用于清理和查看遗留租约。
- `hard_lease`：改动已有 todo 的认领关系必须持有它的活租约；所有把已有 todo
  置为 done 的转移（`complete` 与 `supersede`）都必须带钥匙；"矛盾态自动失效"
  改为响亮报错。新建 todo 时顺手指定认领人仍然允许——新 todo 不可能已有租约。
  有一个由 harness 自己持有的例外保住"带钥匙"这个不变量、又不逼 presenter 在
  人工等待期间一直持约：完成 user 角色的 `user_gate` todo 且没有显式租约凭证时，
  完成栅栏会在同一把 per-goal 租约锁下自己铸出钥匙、验证、并在提交后释放；
  已存在的时间有效租约永远不会被顶掉。

配套约定：

1. 默认 `legacy`，存量 goal 行为零变化；洞还在，实现 PR 必须明说。
2. 字段落点如上；registry 与租约文件都是单机的，只有 goal 状态文件跨端。
3. 跨端窗口：模式靠文件同步传播，两端可能短暂看到不同的值。现在只能收敛、
   不能消灭，如实声明；共享模式才能关死。
4. `hard_lease` 留一扇门：既有的委托授权（todo_lifecycle_authority，附说明
   理由）可以不持租约改认领，结果里带明确的越门标记，可审计。不新增任何
   绕过开关。
5. 切换模式要求静止：goal 内仍有未完成的认领或活租约时拒绝切换，并列出
   阻挡者。v0 不提供强制切换。

### 现状（2026-08-18）

门禁已经落地：状态文件前置的 `handoff_mode` 字段与上述 `legacy | soft_claim |
hard_lease` 语义、只允许静止态切换的 `loopx handoff-mode show|set`、以及带
`handoff_gate_overridden` 标记的委托授权门；上面的 user gate 自动铸钥匙作为后续
补充落地。`legacy` 仍是默认值，分叉洞按设计保留。门禁落地后发现的两扇侧门随本次
修订一并关上：`supersede` 与 `complete` 过同一道租约栅栏；强制重建状态
（`bootstrap --force`）保留已声明的模式而不是把它重置回 `legacy`。此后 Stage 2
provider 合同与 file backend 已落地，第一个 Stage 2C 提交后 runtime shadow 也已在
显式、默认关闭的配置后实现。本地 canonical promotion 尚未开始：runtime 仍不从
shadow 读取决策，上述 migration、rollback、parity、read flip 与 legacy-writer fencing
门禁仍然开放。

### 与分阶段交付的关系

对应 #2787 评审结论中的五阶段计划：先在 characterization 阶段把今天的真实行为
（含上面三条分叉事实）做成用例入册；门禁本身是一次显式声明的行为变更，作为
独立 PR 在 file provider shadow 之前落地，避免分叉数据污染 shadow 对账基线；
本地 canonical promotion 把 claim 与 lease 收进同一本账后，这类分叉从结构上
不再可能，门禁自然被吸收。读侧与卫生修复（status 显示真实租约、完成后销掉已
验证的租约）不涉及判断语义，已先行提交。

characterization 阶段的负向用例在原清单上补充：软认领盖掉活租约、完成栅栏在
矛盾态失效、授权检查先于租约栅栏、尚未设防的认领变更入口、租约获取读取投影
与状态文件锁之间的窗口。


## 附录 C：Stage 2C promotion 设计（提案，2026-09-04）

本附录将第 12 节问题 8 到 14 收敛为 provider-neutral promotion contract，并分开逻辑
authority 语义与各 provider 的物理 retention 策略。本文档不实现以下内容；未关闭的门禁
仍是任何真实 promotion 的前置。

### 决策摘要

1. Stage 2C 晋升的是一份 canonical coordination head，不是某种文件格式。file、NoKV、
   PostgreSQL 实现同一套 `AuthorityStore` 语义。
2. Canonical Todo 与 lease state 默认完整。不得按“当前已知 consumer 使用的字段”缩减。
3. 后续任何字段删除都需要字段级兼容性调研、migration/rollback 证据与 maintainer
   显式批准。
4. read flip 后 Markdown 与 lease 文件是渲染投影，绝不补充缺失的 decision state。
5. retention 的逻辑合同一致，物理实现按 provider 区分；分段或规范化历史不得改变
   head/readback 语义。

### `main` 已交付（2026-09-03）

- 默认关闭的 runtime shadow：每次已提交的 Todo 或 task-lease mutation 对应一笔
  `AuthorityStore` 事务，以该 mutation 的 rollout event id 与 `updated_at` 为键，带
  receipt 重放、内容漂移拒绝、ambiguous commit 调和与回读。
- `loopx coordination-shadow inspect | qualify | read-candidate | bootstrap |
  rollback`：带双 digest 的单点 parity（`missing | matched | drifted`）、基于覆盖
  的持续 parity 报告、provider-first 读形状（仍 `decision_read_from_shadow=false`）、
  从 legacy 投影引导空 shadow、以及按 revision 围栏的晋升前 lineage 隔离式 rollback。
- cutover kernel：从 mutation 到投影、事件与 receipt 的纯 reducer；要求 shadow 在
  一个精确 revision 与 digest 上已资格化、并带独立持久化且绑定该 revision 的 legacy
  writer fence 的 `coordination.local_authority.promote`；永不回退到 Markdown 的
  provider-first `mutate` 与 `todo_read`。
- fence 集成：每个 Python Todo mutation 与每个 native task-lease
  acquire/renew/transfer/release/verify 以及已提交的释放型 fence-close 都在自己的锁内
  检查持久 fence；fence 不存在时零运行时调用；fence 存在但不可读或无效时 fail closed。
  被 fence 拒绝的写在第一个副作用之前以 validation 阶段的 `permission_denied`
  返回且不产生回执；共享检查只拥有类型化原因与 fence 绑定事实，各调用方 adapter
  据此渲染同一条 provider 中立的 remediation，并把检查结果放在 `write_check` 下携带。
- 完整 Todo read model：`loopx_todo_canonical_read_record_v0` 发布带版本字段 manifest；
  TypeScript projection 拒绝 replacement 丢弃既有记录中已经存在的字段。

### 规范性 promotion contract

promotion state machine 与 provider kind 无关：

1. 从经过评审的 goal-level authority binding 解析已资格化的 `AuthorityStore` profile
   与精确 store lineage。
2. 在 legacy Todo 与 lease lock 内，验证同一个 source revision、projection digest、
   field-manifest version 与 provider cursor 上的持续 parity。
3. engage 该 provider profile 的持久 writer fence，再以 compare-and-set 或 transaction
   precondition 提交完整 canonical head 与 promotion receipt。file marker、NoKV document
   identity、PostgreSQL row/table layout 都只是实现细节。
4. 允许 provider-first 决策前，回读 binding、head、receipt、cursor、manifest 与 digest；
   任一不一致都 fail closed。
5. 通过 transaction-bound outbox 渲染兼容投影。陈旧投影从 head 修复，绝不用于填补
   canonical field。

canonical head 保留 normalized Todo 与 lease record 中每个已经合法存在的字段；field
manifest 属于 qualification 与 parity 合同。upsert 要么提交完整 replacement，要么使用
clear operation 显式的 typed patch。未知新增字段在 manifest 升版前 fail closed。删除
提案必须逐字段枚举 producer、reader、writer、persisted fixture、静态引用、历史版本与
已知外部 consumer；写清 migration、downgrade、rollback 与 semantic-equivalence 论证；
并获得 maintainer 显式批准。code search 没有发现当前 reader，不构成删除理由。

完整性边界不包含 raw Markdown formatting、credential、host-local path、整个 registry、
raw evidence body，也不包含 quota、run history、settlement、inbox、scheduler 或其他
ledger 拥有的 state；这里只保存受各自合同治理的 reference。

共同 retention contract 保留最新完整 head、ordered cursor、原始 operation receipt、
integrity chain、确定性 scan 与 recovery readback。物理 profile 可以不同：

- **file：**create-only sealed history segment 加有界 head document；
- **NoKV：**经过资格验证、绑定 lineage 的 document/segment conditional publication
  与 recovery readback；
- **PostgreSQL：**事务追加的 history/receipt row 加 current head，并配套经过评审的
  index、partition、RLS 与 tenant context。

所有 profile 暴露相同逻辑结果与 field manifest。每个 profile 发布实测容量限制，并在
无法保持合同的写入发生前返回 typed `store_capacity_exhausted`。

### 晋升前仍缺

- provider-first CLI 路由与持锁的 promotion orchestrator（kernel 自己声明的下一切
  片）：取 Todo 与 lease 两把 legacy 锁，要求 `qualify` 在当前 revision 与 digest 上
  为 `qualified`，engage fence，执行 `promote`，渲染投影，写入问题 11 的声明；源
  digest 已变时拒绝重跑，除非显式丢弃被放弃的 store。
- 完成事务捕获资格验证（问题 14）：Todo add、update、complete、supersede、archive，
  以及 native lease acquire、renew、transfer、release、auto-acquire、fence-close，现已在
  主写前后生成 prepared/committed outbox entry。有界 drain 把完整、带版本的 record
  提交到既有 `coordination.runtime_shadow` file-v0 lineage，不再创建第二套 local-shadow
  candidate。晋升前仍需补持续 mixed-writer parity、event-only Todo 覆盖和所选 provider
  profile 的 recovery/capacity 证据。
- 补齐兼容投影 outbox 与 file、NoKV、PostgreSQL 的 conformance row。Provider-first
  create、claim、窄 update、complete、supersede 与按 role 执行的 archive 已把 committed
  authority journal 复用为持久 intent，再以幂等 replay 把 native active/archive record
  渲染到机器所有的 Markdown region。lease-file 投影、backlog/status 回读、
  provider-neutral authority binding 与剩余 command inventory 仍需落实同一合同。
  三个 provider 不必同时晋升，但每个 profile 都必须先通过该合同才具备资格。
- 首个晋升 profile 的 retention、fast path 与实测 capacity；参考执行器的删除与
  status flip（问题 13）。
- 晋升后的 rollback：已交付的 rollback 隔离的是晋升前 lineage。首次权威写
  （`authority_revision > 0`）之后的返回路径是问题 3 要求的经评审的 fenced export：
  静止、空 projection outbox、把 head 导出到 Markdown 协调字段与 lease 终态记录、
  `equal` 校验、去水印与 fence、退役 lineage；永不自动，永不在活跃 lease 期间。

### 增长是晋升前置

最低支持周期是十个自然日。负载、修正后的全历史成本模型、本地存储方向和资格化预算
统一由[第 7.2 节](#72-十天-goal本地存储资格化目标提案)维护。file-v0 仍是有界
conformance/bootstrap profile；bootstrap 成功或短时微实验都不证明长程容量。本地首次
晋升必须具备已资格化的历史有界热路径和自然时间 soak，不等待 PostgreSQL service 就绪。

### Todo domain / projection 决策（2026-09-05）

长期分层依据字段语义，而不是它最早从哪里解析。`TodoDomainRecord` 持有 identity、
role、status、text、task semantics，以及 **`archive_state: active | archive`**。
归档独立于完成：handoff gate 与 succession-tracked completion 检查会排除已归档
记录。`deferred` 是 status，不是 archive state。renderer 不得通过移动标题来制造
或改变这一决策。

`TodoProjectionMetadata` 持有 `source_section` 和可选 `index`；Markdown adapter
渲染原生记录时生成这些字段。导入的 section 名称可保留为 compatibility provenance，
但不得成为 provider-origin creation 的必填输入。一个重要约束是：旧 `index` 还在
consumer pipeline 中作为相同 priority 的排序依据。迁移必须通过显式 domain 排序
规则或已资格化的 compatibility provenance 保持顺序；删除它并静默改为 identity
排序不算 parity。全新原生 collection 先按 Todo id 确定顺序，再走既有 priority
projection；展示 index 由 adapter 分配。

当前实现检查点新增独立的 `loopx_todo_domain_read_record_v0` manifest 与
`todo_domain_record_v0` item。现有 reducer、file-store mutation path 和 collection
reader 可以验证不含 Markdown 字段的该形状。测试覆盖原生插入、归档、exact replay、
reopen，以及拒绝 renderer metadata 和不完整 replacement。这证明 storage/read
边界，不等于已经完成有授权检查的 CLI creation 或完整 lifecycle transaction。

兼容性是显式的：`loopx_todo_canonical_read_record_v0` 及其 `todo_item_v0` 记录保持
不变。已有 head、receipt、field manifest 与默认 Markdown capture 保留全部合法
v0 字段。read、mutation 和 startup 不隐式转换。同一 manifest 混入 native/legacy
记录会 fail closed。原生 head 降级到旧 binary 时因未知 manifest 而 fail closed；
rollback 必须经过下述已评审 export，不能只安装旧 binary。schema upgrade 不得重写
历史 operation receipt。

本次分层的字段 inventory：`active_state_todo_parser.py` 产生 section/index metadata，
并将 section membership 映射成 archival state；`todo_summary.py`、`handoff_gate.py`
及 resume/continuation projection 消费 archival state；`todos/projection.py` 使用
index 排序。现有 runtime-shadow fixture 和旧 TS insert fixture 保留 v0 provenance。
原生 contract 是新增选项，不删除已持久化 v0 字段，也不声称未知 external consumer
已经迁移。后续 v0 import 必须盘点这些 consumer，并在同一 revision 证明
render/export/rollback 与 selection parity，再改变 binding 或 manifest。问题 8、10
的完整性要求覆盖 domain fact 与保留的 compatibility provenance，但不要求原生
caller 伪造 Markdown 地址。

### Provider-first terminal lifecycle 检查点（2026-09-07）

Promotion 后的 `complete`、`supersede` 与按 role 执行的 `archive`，现在在 file、
NoKV、PostgreSQL 上使用同一笔 TypeScript 原生事务。authority owner 决定
actor/claim/lease admission，从 typed caller intent 推导 successor 的 priority、capability
与 Agent binding、exclusion、continuation 和 predecessor relation，reduce completion
policy，以 CAS 提交 Todo/lease/head/outbox write set，并持久化 replay receipt。Python
只保留 registry fact、caller-approved validation effect、intent/result transport 与兼容投影
drain 的 adapter 职责，不再针对不同 provider 选择另一种 terminal 或 successor outcome。
Legacy Markdown 与 event writer 在物化 record 前复用同一个纯 TypeScript successor
decision。

Validation declaration 只以 required marker 与 SHA-256 digest 跨越 canonical 边界。
raw argv 留在权限为 0600 的 host-local sidecar，恢复时必须先证明 digest 匹配才可执行。
这使 provider head 保持可移植、public-safe，同时不会让 recovery 静默绕过 validation。
导入 v0 的 `index` 继续作为归档顺序兼容事实；native record 回退到持久
completion/update 时间与 Todo identity。Todo 已不在当前 canonical collection 中的
legacy lease file 继续作为历史审计材料保留，但不进入 live projection。

资格验证使用同一份只读、生产复杂度快照做三臂对照：不可变 legacy baseline clone、
隔离 file store、隔离的真实 PostgreSQL tenant。两个 provider head 精确比较；legacy
结果按显式 compatibility projection 比较。归档时仅从 legacy hot view 排除 provider
保留的 archive 记录及其历史 lease，并且只有先证明每个 role 的相对顺序完全一致，才可
忽略导入 `index` 的绝对值；domain 字段、归档选择、active lease 与非目标记录不得归一化，
源快照必须不变。可执行演练为
`examples/control_plane/authority-three-arm-rehearsal.py`。受检入的确定性 public-safe
规模 fixture 在每个 provider conformance suite 中制造同样的分布、压力与 hard-lease
fence。它不能替代只读三臂演练，因为所有 provider 共享新的 semantic owner，可能同时
同意同一个回归。

凡声称推进本 RFC 的 PR，都必须遵守
[production-scale fixture 维护契约](../../development/testing-and-quality.md#production-scale-fixture-stewardship--生产规模-fixture-维护契约)：
声明 fixture 影响、覆盖所有受影响的 provider arm，并把只读三臂演练保留为独立的
promotion gate。

Legacy lifecycle 的字段组装现在调用唯一 TS field planner，详见
[TS 退役检查点](typescript-control-plane-migration-v0.zh-CN.md#legacy-字段规则退役检查点)。
它删除 Python decision，但不改变逐 goal 的 authority 阶段：未 promotion 的 goal
仍由持锁 Markdown writer 提交，promoted goal 仍使用既有 provider transaction 与
unsupported-field fence。planner 不读取 provider，也不授予 lease、CAS receipt 或
写权限。该检查点闭合的是一个规则 owner，不是剩余 mutation inventory 或本地
store／promotion 资格化。

### 跨 RFC 的语义与展示 conformance 检查点（2026-09-12）

TypeScript 重构 RFC 与本 provider RFC 现在共享一个显式的 Todo 语义边界。
Python 生产 caller 直接从 `todos/todo_semantics.py` 导入；`todos/projection.py`
只作为外部集成所需的 import 兼容 facade 保留，不再是第二个 kernel。这是 owner
收敛，不是新增一套规则。TypeScript 的 typed `projection_delivery` union 也明确区分
mutation intent（`pending`/`not_required`）与 provider readback（`delivered`/`current`）；
未知状态在 acknowledgement 之前 fail closed。

优先级意图现接入 File、SQLite、PostgreSQL 既有的准入 create/update 事务。
显式设置/清除、参数缺省及与旧文字前缀的冲突由 `todos/priority.ts` 处理，Python
读取共享生成的语法。Markdown 保留兼容展示，native record 保存一致的 priority/title。
CLI 与经过审阅的 Chat 编辑保留 CAS 和历史重试身份。真实后端回读及长期本地 Goal 的
一次性隔离副本验证这条边界，见[调用合同](../../project-agent-todo-contract.md#priority-intent)。
这不改变 provider 默认，也不关闭其余 promotion 门禁。

展示语义属于 projection 层，而不是 domain record。`source_section` 与 `index` 是 v0
wire shape 的展示坐标；native record 根据 role/archive state 推导相同的展示 section，
并以时间戳和 Todo identity 做确定性回退，不制造假的持久 index。因此即使 wire shape
不同，normalized presentation metadata 仍只有一份 contract。同一规则由
production-scale fixture 以及 File、SQLite、NoKV conformance arm 共同覆盖。Provider
自己的 revision token 仍由各自 provider 管理，只用于 provider-specific replay 规则，
不被归一成 Todo 语义。

本检查点只改变 read/ordering 与兼容 adapter 语义：不晋升 provider、不增加 writer，
不改动 #4280 交付的 transaction decoder，也不把 Markdown 变成第二权威。共享 RFC
继续负责 durable truth、恢复、cutover 与 projection delivery；TS RFC 负责业务规则
owner 与 caller 删除。

### 下一步交付与并行 provider 工作

Markdown 是**长期保留的一等可读投影**。退役的是它的数据库及业务 writer 权威，
不是面向 human/agent 的展示。[TS RFC 交付顺序](typescript-control-plane-migration-v0.zh-CN.md#下一步交付顺序)
负责业务规则统一和 caller 删除；本 RFC 负责唯一 durable truth、恢复和 cutover。
CLI 原生 TS 化与 daemon 不是前提，PostgreSQL 部署不能阻塞本地采用。

交付历史现已由 status 与 quota 共用一份 TS outcome/scale/follow-through 读投影，
删除被替代的 Python decision。这独立推进 TS RFC，不改变 provider、持久历史、
writer fence 或 promotion 资格。Markdown 仍作为可读投影；它和历史叙述标签都
不能成为额外的交付权威。

```text
CLI / Agent / Dashboard → 唯一 TS Todo 事务 owner → canonical authority
                                                   ├ structured consumers
                                                   └ Markdown 投影
```

每个 goal 只有两个 authority 阶段：cutover 前，Markdown 向已资格化 shadow capture
供数；cutover 后，所选 canonical provider 向单向投影供数。不增加第三种 TS-Markdown
backend、实时双向同步或按命令拆开的权威；晋升后不支持的命令 fail closed，不能回退旧 writer。

2026-09-19 命令退役检查点删除了无人消费的
`coordination.local_authority.mutate` 与 Todo compatibility-edit 执行包装，同时保留
实际领域事务仍复用的 `prepareCoordinationProjectionCommit` 与共享 reducer；另行删除
无关的公开产品命令 `todo capture-followups`。后一个命令的退役不删除、不削弱、也不
重命名本 RFC 的 runtime shadow-capture 机制。独立 prompt 命令 `todo suggest` 也退役；
候选分析复用当前 Agent 与既有 Todo 读取/写入路径，不新增 discovery 包装或 provider
promotion 前置条件。两个公开命令的退役都不删除已保存的 Todo 或 authority 历史。

#### 重构主线总览

Monitor 状态 owner 现位于 TS，并与 authoring scope、external-wait 校验及字段更新
组合为一次公开 update 规划。Python 输送锁内完整紧凑快照，不再逐个编排 leaf RPC；
局部拓扑修改不能破坏保留的等待条件。准入、持锁及持久化仍由 legacy writer 负责。
Typed plan 不是 authority receipt；Monitor/successor 原子性、原生 metadata update、
provider 默认值及 D1–D3 仍是独立、未完成的门禁。永久 Markdown 投影仍属于终态架构。

Todo authoring scope 已与 terminal successor 共用 TS 最终绑定不变量；仅显式
`global_gate` 可以扩大阻塞到全部注册 agent，`goal_bound` 不授予全局 gate 语义。
这是 T1 准入规则收拢；不扩张 native update 字段权限、不改变 provider/profile 默认值，
也不解除 D1–D3 的投影、真实 backend、soak 或 promotion 条件。

Shared-goal alignment 与 amendment admission 在 promotion 后共用同一个 canonical
Todo/lease revision，包括权威空集合；旧展示／lease 文件读取仅保留在 promotion 前。
Proposal source digest 包含该 revision，但不将它冒充 Goal intent revision 或
amendment commit receipt。这是有边界的 T3 consumer 闭合；默认 provider、永久投影、
D1–D3 资格化和 T1/T2 条件保持不变。

Standing decision 同样从完整 canonical Todo 快照派生，先于展示 index 和仅活动项
过滤；归档保留规则与读取共用一个 TS owner。已归档的撤销仍是决策历史，矛盾且无法
定序的历史不能靠存储顺序选出批准。这是 T3 读取修正，不是新的持久化权限账本或
commit receipt。各 provider 的 CAS/replay 边界、legacy 源顺序兼容、永久 Markdown
投影与 D1–D3 条件不变。验证须覆盖真实 CLI、真实 provider 的 archive/replay 和
超出展示条数的数据，不能只测内存中的排序数组。

完整来源与列表过滤的边界现保留已求值的 resume 事实。Bootstrap 与 writer outbox
共用 typed 归档依赖 selector，把被引用的实际完成记录纳入 canonical，使 reader 能
独立重算。新 legacy Agent 归档保留 role 和既有读取分类；旧 Agent 专属 class 记录
可有界还原 Agent 身份；明确记录 Agent role 而未记录 class 的历史记录，可保留
legacy 读取分类。TS 选择器校验这项独立的 codec 事实，闭包遍历与物化使用同一分类，
绝不推测用户批准权限。重复／矛盾 identity 明确拒绝，历史节点／lease 不重新进入
活动 lane。三臂演练用真实 provider 检查此闭合；派生 readiness 不充当证据。通用历史
导入、剩余 D3 资格化与显式 cutover 批准仍是后续工作。

Runtime-shadow parity 与 source-partition continuity 的语义 digest 都只排除
`resume_condition.evaluated_at`。该字段是查询时钟 observation；再次读取未变化的
持久来源不应凭空制造 drift，也不应破坏后续 writer 的连续性证明。已求值的决策和
其余 resume 事实仍全部参与比较：readiness、reason、generation、target 或任何其他
Todo／lease 字段变化时，仍须先被 capture，否则 parity 或 continuity 必须失败。
Prepared outbox 字节和传入 projection 仍做完整校验；完整记录（包括 observation 时间）
也仍保留给 reader 和候选快照。

Quota scope/claim 选择与 resume planning 现共用一个 TS 只读边界，消费既有
legacy/canonical summary，不分叉 provider 专用规则。User gate 作用域与 Agent
执行归属分开解释，active-next-action 也遵守此区分；有意语义变化与删除的 Python
selector 见 TS RFC 的 T3 卡。真实 FileAuthorityStore CLI 测试覆盖展示缺失／陈旧且
不写回。这是 consumer 规则收拢，不是 transaction/store 改造、provider 资格化或
整 Goal cutover。

长链 checkpoint 读取现由同一 typed frontier revision/ACK 策略处理 legacy 与 canonical
来源（TS RFC T3）。Index 在展示限制之前生成；被排除工作不能误触发该 Agent，
不完整或有歧义的 checkpoint 不能确认长链已处理。Python 保留持久 v0 codec，
不再持有第二套 revision/threshold 策略。这是 consumer 改造，不新增 provider、
commit receipt、promotion 路由或 Markdown writer。既有 CAS/replay、永久投影与
D1–D3 资格化要求保持不变。

以下规划保留原有方向；执行卡是它们的展开，不是替代或取消：

1. **闭合 TS 事务与 consumer。** 按 [T0–T3](typescript-control-plane-migration-v0.zh-CN.md#当前-stack-合入后的执行卡) 收口规则并删除重复决策。
2. **永久投影闭合。** 见下方 D1：Markdown 长期保留为单向展示，不恢复为业务权威。
3. **一个已资格化的本地 profile 与 fenced cutover。** 见 D2、D3：真实 backend、容量、soak 与显式 promotion 批准缺一不可。
4. **列明 caller 后退役。** 按 T4 删除无调用者的旧业务 writer；永久 renderer 和必要 import/export 保留。

#### 持久化执行卡

Task graph 的 T3 topology consumer 现共用 inventory/horizon 关系目录，消费
一次已提供的 status 快照。缺失/截断指标描述读取完整度，不代表 canonical
有效性或 promotion 资格。File/SQLite 在 Markdown 展示缺失时的 reader 回放
必须只读：图不修复展示，也不改变 authority。本批删除 Python 重复关系与
遍历知识，不改变以下 D1–D3 门禁。

T3 lease inspect 已将 Todo、lease 与 handoff mode 绑定到同一 provider revision，
promotion 后不再读取本地旧 lease 文件；canonical 空租约集合保持为空。资格策略与
当前 acquire/lifecycle 共用 TS owner，包含 claim 分歧和 exclusion；读取结果不是
租约授权，也不是 commit receipt。两条来源路径的时间／资格解释现收敛到 TS，
覆盖归档 open 历史与损坏到期时间；注册来源及晋升 fence 变化须重新校验并有界重试。
Python 不再为检查重建 canonical head 或诊断规则，见[读取合同](../../reference/canonical-lease-renew.md#what-inspection-proves)。
该 reader 闭合和重复规则删除不代表 provider
资格化，不改变 CAS/replay 或 D1–D3；永久 Markdown 展示与后续规划继续保留。
ownership 编辑在 promotion 后现在与现有 update transaction 共用 typed authoring
和 lifecycle 边界。claim/exclusion 门禁保留，带 lease 的 ownership 重写继续拒绝；
promotion 前仍保留 Markdown writer 兼容路径。经过审阅的 Chat Todo/Monitor 非终态
编辑现通过该 owner 传递已验证的 lifecycle grant/reason，不再落入被 fence 的旧
writer。Preview 绑定 provider revision 与 registry 见证；apply 通过 CAS 提交新
操作，或恢复原不可变回执。当前 head 的展示恢复、Dashboard 重载后可发现的重试
闭合这一 L2/L5 链路；exclusion/binding 与 lease 限制不因 grant 而消失。这删除了
重复决策路径，但不代表 provider 已资格化、不改变默认值，也不放宽 D1–D3。
PostgreSQL 经隔离 service/store factory 验证同一事务，不代表 Python 生产路由或
默认 provider 已完成切换。

命令清单、update/monitor 事务和 consumer 删除统一按
[TS 执行卡](typescript-control-plane-migration-v0.zh-CN.md#当前-stack-合入后的执行卡)
推进，不在这里复制第二套实现路线，也不把 read-policy PR 合并视为存储就绪。
进入本节前先完成 T0 基线核对。

Lifecycle 准入由 legacy writer 与 native transaction 共用 TS owner。Native
text/note 更新通过有界 planning intent，在同一 canonical head 上组合公共 TS update
owner 后再进行 CAS；terminal transition 与 planning update 在进程内复用预授权 lease
fence。清除 resume 时原子删除 generation fence，重试保持 intent identity。删除对应
Python 规则、合成 Markdown editor、事务前目标读取和无调用方的独立 effect-runtime
wire，不改变 provider 默认或 promotion。这仍是有界的非 terminal planning transaction，
不是通用 native metadata 支持；Active lease 下的状态变化及 Monitor 规划/effect 仍不
支持。准入结果和 lease-fence 结果都不是 commit receipt；兑现删除收益时，provider
CAS/replay 与既有 writer 持锁生命周期不变。
同一事务现通过共享公开 TS planner 接受有界工作要求声明，字段清单和有意拒绝变化见
T1。File、NoKV、SQLite 与 PostgreSQL conformance 覆盖别名、显式清空、后续编辑后
的旧操作重放、非法输入原子性和 lease 拒绝；复杂容量 fixture 携带工作要求验证其他
lifecycle 操作不会丢字段。这不资格化新 profile、不扩大 execution grant，也不改变
D1–D3／promotion hold；Markdown 继续作为独立的永久投影。
等待/恢复 lane 选择现由 quota、vision-wait、agent-scope、replan 共用一个 TS 读取
策略 owner，删除旧 Python selector 模块。适配层在 promotion 后消费同一 canonical
summary，之前消费 legacy summary；真实 CLI 覆盖容量变化和 promoted display
缺失且不写回的场景。这不代表所有 quota source 路径已闭合，不授予 monitor 写回
权限，也不改变 provider 默认与 promotion hold。

T3 decision-dependency 读取策略现由同一 TS owner 解释 scope coverage、精确链接和
一致性诊断。显式 gate 接收者独立于 claim 归属，精确目标矛盾要求修复而非授予批准。
这删除的是重复的 consumer 知识，不是 provider transaction；不代表 D1/D2 已资格化，
不改变默认 provider 或放宽 D3 promotion hold。Markdown 继续作为永久单向展示，
后续执行卡与退役条件保留。

Scoped fallback 的选择与门禁关系也已复用同一 TS decision owner，删除 Python
词语重合匹配和选择循环。显式依赖及 global gate 保留，旧完整 action key 相同仅
保留阻塞兼容；键不同或缺少事实不能证明独立性。这是披露语义变化的 T3 consumer 闭合，
不是新 provider，也不代表 D1–D3 已资格化。来源适配、永久投影和 promotion hold
不变；具体规则见 TS 执行卡及 decision-scope 协议。

**D1 — 资格化永久投影交付，可与 T1/T2 重叠推进。**

Todo 摘要现由一个 TS 批次决定范围、lanes、计数、claim 展示分配和收尾证明，删除
Python 的重复汇总分支及分离的 lane／closure 内部调用。最近完成按真实完成时刻排序，
不把编辑时间或 ISO 字符串顺序当作完成顺序；partial 来源不能在再次筛选后重新取得
整源收尾证明。公开摘要／持久记录合同与展示预算保持不变。
见[计数与时间语义](../../reference/todo-work-counts.md)。这关闭共享摘要决策，不宣称
全部消费者、永久展示新鲜度或 D1–D3 已通过。


摘要与 work-lane 计数已独立于展示上限，并在 Agent 筛选后保留来源不完整状态；canonical 列表的 acceptance 限制与 status 一致。这只闭合 L5 的一个读取消费者，不代表永久投影新鲜度或 D1–D3 完成。见[计数语义](../../reference/todo-work-counts.md)。

Goal Channel 所有权观察先读取完整 provider revision，再限制展示；不修复 Markdown、不复活旧本地 lease，明确披露失败与截断。这是共用 TS 解释规则的 T3 读链路闭合，不完成 D1/D2 或 D3 切换，见 [coordination observation](../../reference/coordination-observation.md)。

D1 的文档归属切片把读取、编辑与投影放到同一可见区域／Todo 行解码边界，修复
fenced 示例被当成真实任务、归档 end marker 后叙述进入历史、稀疏历史行号及归档
优先级阻塞读回的问题。投影复用普通状态的耐久原子写入；相同字节的重试仍完成
文件／目录同步，之后才报告 `current`。区域外正文和 canonical record 不被改写。
这是永久 Python 展示／legacy 输入适配层的收敛：TS authority transaction、provider
默认值、SQLite D2 与 D3 promotion 合同不变，不增加 RPC 或另一份业务状态机。
Canonical handoff-mode show/set 不再依赖 Markdown frontmatter 或本地 lease；一笔 TS 事务把空闲检查、mode 与耐久操作回执绑定到同一 revision，包括未改值请求的回执。该命令边界不切换默认 provider、不晋升整 Goal；frontmatter 仍不属于 Todo-section renderer。操作与恢复见 [handoff-mode](../../reference/handoff-mode.md)。
两个文档生成入口现在隔离 Objective 示例，展示读回复用 Goal codec。Canonical Todo
读取仍独立于畸形展示；Objective 叙述不进入 Todo store，也不扩大其恢复范围。
这是有界 D1 适配层修正，不代表 D1–D3 资格化。
见[文档边界](../../reference/protocols/active-state-structured-projection-v0.md#markdown-ownership-boundary)。

能力缺口 consumer 在 legacy/canonical 输入上共用 TS requirement/resolution owner，
包括 quota 的 Monitor 能力分流。删除 Python missing-set 与 owner/repair 决策 builder，
保留来源适配和只读候选排序。这是披露修复优先级、空来源和 identity 修正的 T3 读取
规则收拢，不是能力启用、持久权限回执或 D1–D3 资格化。永久 Markdown 投影与 cutover
门禁不变。

T2 的无 lease 原生 Monitor 观察与独立后继现由同一 canonical CAS／receipt 提交；
route planner 本身仍不授予权限。CLI 将已提交回执交给既有 journal/outbox renderer，
展示失败标为 pending，不回滚提交、不重新生成后继；quota 继续消费同一 v0 业务回执
完成独立记账。验证覆盖真实 CLI 的缺失 display、renderer 失败后的 operation 重放，
及 File／NoKV／真实隔离 PostgreSQL 的复杂数据、并发和丢回执恢复。
下述 L4 扩展闭合了 Monitor lease proof 与崩溃后结算；跨 owner claim 仍明确拒绝。
此切片不改变 provider
默认、writer fence 或 promotion 审批，也不替代三臂 legacy 对照及 D2 soak。

- 从 `loopx/control_plane/todos/provider_projection.py`、既有 Todo-section renderer、
  canonical journal/outbox 入手。复用 #4097 已有的缺失 Todo section 恢复及
  `recovery_scope=todo_sections_only`；它不能恢复丢失的独立 Goal 正文。
  已交付边界以 [active-state projection contract](../../reference/protocols/active-state-structured-projection-v0.md) 为准。
  直接编辑 Markdown 不得自动导入 authority；显式验证的 edit/import 工具另提方案，不能隐藏在 renderer 中成为第二个 writer。
- 按 #4101 实际合入 head 评估 receipt-retention 候选，不再建一条 delivery queue。
  业务已提交与投影尚 pending 必须分别可观测。
- 验证 crash/retry、并发 revision、缺失／陈旧／非法 display、交付前 receipt
  生命周期、非托管正文保留和私有字段边界；恢复不得重新执行业务操作。
- caller 迁走后才删除旧 projection repair/receipt 路径。退出条件是可复核的
  freshness/readback 和可操作修复路径，不能只证明成功渲染过一次。

D1 的最新／固定版本意图和三次追赶决定收口到 TS；Python 保留文件锁、耐久落盘与
渲染。已提交的 `refresh-state` 及同 Turn 重试通过现有投影路径恢复显示，不重做 Todo
或 quota 变更。规划、缺失工作诊断及首次渲染复用完整 canonical 快照，耐久写后仍读
provider 确认；权威空集合不回退到陈旧 Markdown。Legacy 与预览行为保持不变。
这关闭刷新恢复／诊断调用方，不代表全部 L5、后台 drain 或 D2/D3 完成；
见[投影合同](../../reference/protocols/active-state-structured-projection-v0.md)。

**D2 — 资格化一个本地 profile，不等待 PostgreSQL 部署。**

- 写代码前对齐 #4121 SQLite 候选与第 7.2 节；资格化及批准前保持 opt-in。
  不符合合同则记录具体缺口，不另建第二套 store 或修改默认值。
  File/NoKV 继续作为既有 conformance 参照。
- 使用一次性真实 backend、共享复杂 fixture 和加速增长样例，验证 head/event/
  receipt 原子提交、并发、历史 cursor/replay、有界 live head/index、crash/restore
  和公开 CLI readback。
- 另外取得既有 >=10 天 synthetic-goal soak 证据；加速数据量不能替代真实经过时间。
  代码可以先合并，promotion 保持 hold。启动定时 soak 或操作活跃 Goal 另需授权。
- 退出时指定唯一 provider/profile revision，列明 passed、failed、missing evidence。
  NoKV/PostgreSQL 各自保留资格要求，SQLite 通过不能豁免其他受影响 provider。

**D3 — 集成并申请整 Goal cutover。**

- 前提为 T1–T3、D1/D2 和 capture 资格化；生产 cutover 另需显式批准。从已合并 #3870 的 transaction-
  bound capture 出发，不重新建设；对照最终命令表验证 sustained mixed-writer 与
  event-only 覆盖，任何未闭合行都阻塞 promotion。
- 绑定唯一 lineage、source revision、field manifest、digest、cursor，排空 capture，
  fence 旧 writer，读回 canonical state 与投影。一次性输入上演练 legacy/canonical/
  所选 provider parity，覆盖空集合、排序、archive、lease、receipt。
- 证明 fenced export/rollback；canonical 写入后，不能简单关闭 fence 并复活旧
  Markdown 快照。失败时保留唯一已知 authority 或明确 blocked 状态，不能产生双写库。
- 仅晋升批准的 profile/cohort；不支持的命令保持 fail closed。声明的迁移窗口结束、
  最后 legacy caller 退出后，把精确退役路径交给 T4；永久 renderer 和合法 import/
  export 保留。

#### 执行交接与汇合顺序

**本地默认化交付计划（2026-09-14）。** 目标是新建本地 Goal 后，日常 CLI、Turn 和
操作者动作都通过 TS 拥有的 canonical 事务运行，Markdown 永久作为展示投影。
“未指定 selector 时选择 File”不等于达成目标：已有 Goal 在明确的整 Goal cutover
之前，仍有 legacy writer。

长程默认应选定**一个**合格本地 profile。SQLite 是当前 D2 候选；File 保留为真实
对照、显式可选 profile 和迁移演练后端。不能发布两个含混的默认项，不能把现有 File
历史布局直接称为长程合格，也不能从选定 SQLite 静默回退。最终选择必须引用 D2
证据。PostgreSQL 复用 TS 语义合同，但 service、tenant、restore 和 capacity 单独
资格化；其部署不阻塞本地路线。

核对基线：#4286（命令回执／归档）、#4289（typed 工作／归属 intent）、#4292
（声明式 decision metadata）、#4304（canonical handoff mode）、#4316
（Goal Channel observation）、#4317（provider opening）、#4348（canonical renew）、
#4328（SQLite D2 首批测量／恢复）、#4334（PostgreSQL service admission）均已合并；
这些已合并切片仍不等于完整执行卡合格。#4732 Monitor、#4754 关联 User completion
也已合并，#4224 继续由 contributor 负责 D2。组合前重读实际 head，不能把已合并
祖先再次算成新变化。

下表编号保留**领域归属和验收边界**，不表示剩余 PR 或预留 GitHub 编号。
新增实现以当前核对表为准；仅换语言或移动 helper 不构成退出条件。

| 波次／PR 包 | 完整交付内容与 TS 归属收益 | 依赖与退出证据 |
| --- | --- | --- |
| A／L1：Monitor 配置（本切片） | 现有 `todo update` 配置进入 TS planner／CAS／receipt，删除 Python 重复 intent 字段表；区分配置与观察 hash、时间、代数。 | 普通 CLI/API、清除／省略、active lease proof、no-op／replay、展示失败恢复、完整 fixture 和真实 provider。不宣称完成委托 Chat 或 leased polling。 |
| A／L2：公共 mutation admission 闭合 | 用户 completion update 已共用 TS 编辑／terminal 事务与 Chat 审阅后恢复；继续盘点剩余 effect-owned 决策、委托 owner 动作和 Monitor lifecycle 的 CLI／Turn／Chat caller。见[调用合同](../../reference/canonical-todo-completion-update.md)。 | 复用已合并 T1 owner，不开通通用 raw patch；验证权限拒绝和 caller 响应，删除替代的 Python admission，列全未支持命令。 |
| A／L3：canonical lease 生命周期 | 独立 acquire/接管、原子 claim 的 lease 准入及维护复用 TS facts/decision/materializer 与同一 provider opening fence。显式联合交接由源持有者授权，一次提交 Todo 归属与新租约 generation；canonical 请求类型不再携带 legacy 持锁字段。Acquire 成功必须校验当前执行 proof；canonical 完成可恢复缺失展示。 | 已覆盖完整 head scope 冲突、归档/失效 holder、创建 CAS 原样重试、旧执行、进程中断、真实 CLI 与四臂演练。[操作及剩余 caller](../../reference/canonical-lease-renew.md)。跨外部 effect 的 executor 持锁 fence 仍为明确工作；保留 D1–D3/default hold。 |
| B／L4：leased Monitor poll 与 settlement | 当前 execution proof 贯穿 CLI intent、观察／generation／独立 successor CAS 和历史业务回执；业务写入前冻结 quota 准入，租约结束后仍按原决策恢复结算。 | 既有 L3 lease lifecycle、真实 File／SQLite／PostgreSQL、混合 fixture、业务与 quota 间真实进程退出、并发 renewal 和 unchanged poll；见[操作与快照演练](../../reference/protocols/quota-monitor-observation-receipt-v0.md)。普通 poll 不修改 lease、不消耗 quota，不把两个 authority 假装成同一事务。保留的分组 Monitor 观察／再激活 caller 现使用 Todo update v4 与共享 Monitor planner，无变化重试也能恢复显示。Canonical 再激活现将保留执行权退役与观察周期重开在同一 CAS 提交，和轮询共用 typed 准入；下一次执行仍须显式 acquire。分组维护的 executor acquire、更广 L2 准入及 D1–D3/default 仍开放。 |
| B／L5：consumer 与展示闭合 | 核对 #4316，审计 Turn／quota／Dashboard／Chat 的来源，复用 projection outbox 完成 D1 新鲜度和恢复。 | 验证 CLI、Lark／Chat、打包 frontend 的受影响交互；缺失／陈旧展示、权威空状态、pending 投影及超过 UI 上限的数据。逐个删除晋升后的 legacy fallback。 |
| A–C／L6：本地持久化资格 | 延续 contributor 认领的 #4224／#4328，在选定 SQLite profile 上补齐第 7.2 节 ledger，复用 File／NoKV 对照。 | capacity、真实进程／crash／restore／upgrade、历史 receipt／scan、consumer lag、支持的 runtime／OS，以及另行授权的 >=10 天合成 soak。缺项继续 hold。 |
| A–C／L7：capture 连续性 | 核对已合入的 #4315 归档／lease membership 修复，完成对应 ladder row／mutant 与持续 mixed-writer／event-source 矩阵；不重复实现已关闭缺陷。 | 真实 CLI／File capture、保留历史、半完成 drain 不合格、crash／replay，以及归档／rebootstrap 后再申请 lease。不能借 T4 跳过迁移窗口证明。 |
| C／L8：整 Goal 演练与分组迁移 | L2–L7 后汇合一个精确 revision／profile；drain capture、fence 旧 writer、回读 canonical 与投影、演练 fenced export／rollback。 | D3 包绑定 lineage、cursor、source digest、命令覆盖和 profile；已有 Goal 分组迁移需明确批准，不能按命令拆 authority 或复活旧 Markdown。 |
| D／L9：新 Goal 默认与有界退役 | 单独 default-change PR 让新建／onboarding 选择合格本地 profile，配齐 settings／readback、installer 和打包客户端；最后 caller 与迁移窗口退出才删除旧业务 writer。 | L8 整体产品／回滚资格；区分新 Goal 默认和已有 Goal 迁移。发布兼容／停用说明，保留显式 provider、永久 renderer 和合法 import/export。T4 可在默认启用后继续收尾。 |

**2026-09-24 基线核对。** 保留 claim 的 #4870、reviewed cutover #4888、shadow drain
规划 #4920 已合并，快照分页 #4922、SQLite runtime 准入 #4960 与刷新显示恢复 #4961 也已
合并，后续应验收组合 head，而不是继续沿用旧的 PR hold；SQLite 读取证明优化 #4931 仍在
评审。#4224 实测 1 MiB receipt/scan 超预算，恢复和自然时间资格仍有缺项，不能将优化 PR
当成 D2 通过。摘要规则收口推进投影恢复边界，但没有把其他调用方或默认切换标记完成；
剩余工作按当前核对表归类，不再按 helper 迁移数量机械扣减。

**当前开发节奏。** 上述 L1–L9 是领域分工，不是剩余 PR 数。原七行 PR 计划
已被[逐项核对表](ledger/shared-goal-authority-state-provider-v0/2026-09-24-default-cutover-reconciliation.zh-CN.md)取代：已合入的分页与恢复不再列为新 PR，
caller 在途工作先整合，D2 自然时间资格单列。新增代码依次按完整来源、executor fence、
event writer/整 Goal 迁移、默认/有界 Python 退役四个边界组织。

本批的[分页合同](../../reference/canonical-snapshot-pagination.md)共用既有 TS
collection validation 与 acceptance owner；Python 只校验并组装传输，保持公开返回
形状。每页重新读取 provider head，因此限制的是传输大小，不是数据库内存或总 IO；
并发 writer 可导致调用方完整重读。D1–D3 仍保留。同一 transaction owner 避免并发
重写，先共享 fixture/合同，owner 合入后再 rebase。

L2/L3 命令盘点与 L6 缺失证据未闭合前，不给虚假的日历承诺。>=10 天 soak 是
**被测 profile 就绪之后**的真实时间下限，不是从写计划当天计时；明确授权后可与
兼容工作重叠，涉及持久化语义的后续变化须按影响重新验证。加速 fixture 不能替代
真实经过时间。本地默认不依赖完整 TS CLI／distribution 清理、删除所有 Python
adapter，也不依赖 PostgreSQL service 部署。

每包记录实际 caller／owner 删除、bridge LOC 与退出条件、跨运行时次数、真实后端、
基线 parity 和公开的语义纠正。单元测试绿、canonical selector 或新增配置字段，均
不能单独代表默认化就绪。计划中的 integration、soak、release、merge 和生产晋升
仍分别保留授权边界。

### 并行交付计划

| Lane | 何时开始 | 范围与退出条件 | 依赖 |
| --- | --- | --- | --- |
| L. 长程本地持久化 | 现在，与 Todo caller 同期 | 第 7.2 节：嵌入式候选、有界 live head/receipt index、历史 scan 兼容、crash-safe checkpoint、真实 CLI readback、加速容量与 >=10 天 soak。 | 复用 TS authority owner；本地长程晋升必需，不依赖 P。 |
| P. PostgreSQL provider plane | 现在，从当前 `main` 开始 | 保持既有 `AuthorityStore` 合同；完成 schema migration/install ownership、authenticated service 与 tenant authorization、restore-incarnation rotation、pool/cancellation/failover 行为，以及经评审的 index、partition、retention 与实测 capacity。真实 PostgreSQL conformance 始终是强制门禁。 | 不依赖 #3870，也不得叠在其分支上。仅完成本 lane 不产生 runtime caller 或 promotion 声明。 |
| C. Canonical transaction capture | 资格化 #3870 已合入实现 | transaction-bound outbox 已指向唯一 `coordination.runtime_shadow` lineage，并保留完整带版本的 Todo/lease record；继续完成 sustained mixed-writer parity、explicit-clear/omission 与 event-only Todo recovery 证据。 | 可与 P 并行；但 C 与选定 provider profile 都完成后，才能进入 parity 或 promotion 集成。 |
| I. Binding 与资格集成 | C 与选定 profile 的资格化完成后 | 绑定一个精确 provider lineage、field manifest、source revision、digest 与 cursor；资格化显式 v0 import、排序/归档/consumer parity 与 recovery/capacity；缺字段时不得查询 legacy state 补齐。 | 长程本地集成需要 L，不等待 P；PostgreSQL 仅在自己的 P hold 全通过后汇合。 |
| F. Promotion 与清理 | I 完成且 maintainer 显式批准后 | 完成 provider-first CLI routing、持锁 promotion orchestrator、兼容投影 outbox、晋升后 fenced export/rollback；随后删除重复 reference aggregate，并翻转经评审的 stage/hold 声明。 | 每个 profile 必须通过 C、I 与自身 provider 资格化；长程本地晋升还需 L，PostgreSQL 还需 P。 |

Agent 定向读取检查点：Todo list 筛选已进入现有 TS summary 批次，与 quota 共用
User gate/action 及 Agent claim 范围规则；legacy 和 canonical 消费者中的 Python
列表谓词已删除。完整来源上的 resume/succession 与筛选后的计数不受展示上限影响。
这只闭合 L5 的一个消费者，不代表 D1 永久新鲜度或 provider 晋升。见[读取合同](../../reference/todo-work-counts.md)。
剩余 caller/executor、consumer recovery、contributor D2、capture/整 Goal 演练和默认
onboarding 按当前核对表分别归为代码、在途 PR 和验收活动，不再混算 PR 数。

## 附录 D：执行账本

本 RFC 的交付记录是 [`ledger/shared-goal-authority-state-provider-v0/`](ledger/shared-goal-authority-state-provider-v0/) 下的文件，
一次改动一条带日期的条目，命名与镜像配对遵循
[账本约定](ledger/README.zh-CN.md)。一条条目写清这次改动测到了什么、改了什么、以及没有确立什么。

新记录写在这里，而不再写进附录 C 的那些带日期小节。附录 C 里已有的内容原样保留：它们是没人再编辑的只追加历史，
把它们改写成文件只会产生一次很大的机械 diff，逼那些在途分支重做一遍合并——而这正是本改动想帮的对象，且什么都没能修好。
理由是量出来的，不是猜的，见
[`2026-09-19-shared-goal-authority-entries-get-a-ledger.zh-CN.md`](ledger/shared-goal-authority-state-provider-v0/2026-09-19-shared-goal-authority-entries-get-a-ledger.zh-CN.md)。

`examples/docs-governance-smoke.py` 校验条目的命名、每条旁边的中文镜像，以及账本目录所指向的这份附录确实存在。

2026-09-24：[完整源捕获的 TS 组装与剩余交付包](ledger/shared-goal-authority-state-provider-v0/2026-09-24-source-capture.zh-CN.md)统一源构造、身份拒绝和当前图成员规则；不关闭 L7/D2/D3 或启用默认 provider。

2026-09-24: [带租约接力与剩余本地默认交付包](ledger/shared-goal-authority-state-provider-v0/2026-09-24-leased-continuation.zh-CN.md).

事件重放与剩余切换清单见 [2026-09-25](ledger/shared-goal-authority-state-provider-v0/2026-09-25-event-replay.zh-CN.md).
