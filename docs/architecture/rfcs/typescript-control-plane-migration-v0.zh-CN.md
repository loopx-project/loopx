# RFC：LoopX 控制面 TypeScript 渐进迁移方向 v0

- Status：Accepted，transaction-payoff 阶段进行中
- Proposed by：LoopX maintainers
- Date：2026-08-15
- Last revised：2026-09-13
- Scope：LoopX 控制面核心从 Python 到 TypeScript 的增量、replacement-first
  迁移；不长期维护两份语义实现
- Tracking issue：[#3225](https://github.com/huangruiteng/loopx/issues/3225)
- Language note：本中文版与
  [英文版](./typescript-control-plane-migration-v0.md) 为语义镜像；
  两者不一致视为缺陷。

---


## 当前交付边界（2026-09-25）

按 `37bbaec79` 与当前 PR 状态核对：完整来源传输、事务捕获、来源组装及此前五个
在途 caller/event 修复都已合入，不再计入待开发。当前晋升准入修复之后，规划三个
明确代码边界：外部动作执行区间保护、事件 writer 绑定与整 Goal 迁移/回退闭环、
默认启用与最后一批有界 Python 退役。#4931 与 D2 的剩余资格证据单列；三个是
可命名的开发批次，不是保证总 PR 数。[唯一当前清单与退出条件](ledger/shared-goal-authority-state-provider-v0/2026-09-24-default-cutover-reconciliation.zh-CN.md)。

## 旧观测写入退役（2026-09-24）

同时删除旧 Python 提交后 observer 与 TS observation 提交链，保留现有事务 outbox
作为唯一捕获 owner；source adapter 不再二次采样生成另一份历史。旧配置可识别、
不生效、可显式清理。这是删除已被替代的路径，不代表其余 Python 业务 writer 或
reference executor 已退役。[交付清单与操作](ledger/shared-goal-authority-state-provider-v0/2026-09-24-observation-retirement.zh-CN.md)。

## canonical collection 分页检查点（2026-09-23）

canonical collection 跨语言传输改为 TS 一致性分页：旧 direct list 和分页共用
`canonicalTodoCollection` 规则 owner，Python 校验并组装完整分页，保持调用方形状。
不提高 2 MiB RPC 上限，不在 Python 重建 Todo/acceptance 规则；并发版本变化导致
整份读取失败，File 只读打开不创建缺失 authority。限制与开销见[分页合同](../../reference/canonical-snapshot-pagination.md)。
shared-authority 的当前核对表区分已合入实现、在途 PR、新代码边界和 D1–D3 证据，不再以粗粒度包数代替剩余 PR。

## 跨 RFC 的执行优先级（2026-09-16）

[统一路线](loopx-overall-roadmap-v0.zh-CN.md) 的 R1–R5 是 T0–T4 的当前产品消费者，不另设一套迁移阶段。团队确认路径现在由 `work_items/team_plan.ts` 负责预览、整批规划及不可变操作身份，复用现有 AuthorityStore 回执/CAS 边界。Python 保留公开安全校验与 legacy Markdown IO adapter，逐 lane 写入循环已移除。R1 检查点区分已交付的分配/重试结果与尚未验收的接收者/执行边界。

保留 T0 caller/parity 盘点、T1/T2 事务与 effect 收敛、T3 完整来源消费、T4 删除条件。#4472 已合入，执行前核验 `todos/public_update.ts` 和实际 caller，不能重建 Todo update。新增团队领域规则应在现有 typed work-items/collaboration 归属中收敛；Python 保留输入/IO adapter。R1 的独立反例与 real-path 验证是交付条件；不以更多 leaf RPC、enum 或文件数量记迁移收益。D1–D3 仍由 shared-authority RFC 拥有。

## 当前实现检查点

长历史 closeout 现在复用经原始字节校验的 TS 日志前缀与统一 monitor 提交规则，
Python 删除重复 run log 读取，只适配 Todo 事实。只读失败与提交不确定性分开报告。
这是收尾边界内的有界退役，不是全量 Python 移除；[当前交付与限制](ledger/shared-goal-authority-state-provider-v0/2026-09-24-default-cutover-reconciliation.zh-CN.md#长历史收尾检查本次修复与剩余边界)。

晋升准入现将完整来源绑定到当前 registry witness，并在 TS 持锁范围内重新校验；
保存计划执行保留已审核的 handoff 策略，失败结果如实报告持久 fence。
已提交事务的恢复仍按原 fence/receipt，不要求失去权威的旧来源重新有效。
这关闭 L7/L8 的已复现集成缺口，不重复计算已交付 capture，也不宣称全局默认已切换。
[操作与边界](../../reference/reviewed-coordination-promotion.zh-CN.md)。

Canonical command 的 receipt/head 观察顺序统一归属 TS：团队规划、Todo 创建/
修改/领取/终态/归档、Monitor、lease 维护和 Goal acceptance 在读 head 后复查原
receipt，再执行新准入。这修复同 operation 并发竞争，不扩展 provider API、不
自动重试写入，也不在 Python 复制决定。独立 acquire 与原子 claim/acquire 共用
当前 lease 证明：续租返回新 proof 而保留原 receipt，退役执行或当前 authority
不可读均不能返回旧成功。它只关闭 L2/L3 的并发和当前证明缺口，不代表全 Goal
迁移、默认启用、contributor 的 SQLite D2 或 T4 Python 退役完成。见
[操作和恢复合同](../../reference/canonical-lease-renew.md#commit-retry-and-readback)。

模式切换的旧路径与 canonical 路径现在共用 TS 所有权事实及显式的有效/无效旧模式。
删除 Python 的阻塞分类、伪造旧模式和整篇文本重写，保留来源投影、锁与 capture IO。
旧扫描补齐事件独有 claim，canonical 回执复用 command recovery 并严格校验历史决策。
完整快照和真实 provider 验证覆盖这一 T1/T2 替换；它关闭一处规则/调用差异，
不代表关闭整项默认切换交付包，剩余工作以当前核对表为准。
[行为变化与恢复](../../reference/handoff-mode.md)。

终结审核与验证已收敛到既有 TS terminal owner：Agent 完成、Monitor 停止复用 Chat
先恢复 canonical 回执再确认显示的路径；v2 把验证 continuation 绑定来源 revision，
准入/回放之后才请求私有声明。删除 Python 的终结操作审核分流和提前解析声明编排。
这闭合 T1/T2/L2 的一组真实终结 caller，剩余 leased metadata、executor fence 和 T4
仍未完成。[语义、调用次数与回滚](../../reference/canonical-terminal-review.zh-CN.md)。

Canonical create/claim/update/Monitor poll/terminal 事务现共用
`coordination/authority_source.ts`；Python adapter 经 `authority_registry_source`
在注册/grant 投影前后校验来源。外部验证结束后保留原 witness，在新 effect/提交前
复核；历史回执保留原身份，claim 回放仍另行校验当前 acceptance。见
[版本化 witness 合同](../../project-agent-todo-contract.md#canonical-registry-source-witnesses)。
这闭合上述 T1/T2 caller 的 registry 事实边界，不等于完整迁移、默认切换、
task-lease source 协议迁移或共享网络服务。


投影交付阶段现已闭合跨语言边界：typed TypeScript mutation 结果与 Python
兼容 provider 共用四态契约（`pending`、`delivered`、`current`、`not_required`）。
Provider readback 在 acknowledgement 决策前进行校验，端到端因果链由共享组合
fixture 覆盖。这是一个已完成的交付阶段，不代表 Markdown 晋升，也不声称其余
lifecycle writer 已全部迁移。

同一阶段也删除了该边界周围重复的 Python read policy。task-class 解析、识别 title
的 actionable 判断、依赖就绪、Agent eligibility 和 canonical Todo read record
只有一个 Python 语义 owner，TypeScript 仍是事务 owner。优先级写入意图与排序现共用
`todos/priority.ts`；Python 兼容读取使用生成的词表和旧格式语法，不再独立维护模式。旧
projection 模块只保留 import-only 兼容 facade。这样继续遵守 replacement-first：
兼容路径仍可用，但不能静默形成第二份语义实现。

优先级意图这一批将 CLI add/update/clear、经过审阅的 Chat 编辑和 Dashboard 选择器
接入既有 typed Todo 事务。仅改文字保留优先级，冲突声明在写入前拒绝。P3/P4 排序、
历史装饰标签和 successor 继承使用同一 owner。见
[调用合同](../../project-agent-todo-contract.md#priority-intent)。真实 CLI File/SQLite
回读、隔离 PostgreSQL 和共享复杂 fixture 验证这条边界。本批删除重复的优先级知识，
不代表其余 T1/T3 caller、Python 兼容 IO 或 D1–D3 默认切换门禁已经完成。

Native update 通过 `todos/public_update.ts` 组合非终态 planning intent，在同一份
完整 canonical head 上校验权限并 CAS。独立 intent 命名空间保留 text/note 限制
及旧回执指纹。v2 transport 携带 lifecycle grant、authority reason、registry 来源
见证及可选的审阅 provider revision；v0/v1 保留旧身份，不能夹带新约束。
`todo_update_admission.ts` 组合既有 lifecycle/lease 规则；Python 投影 registry
事实、传递意图并排空展示 outbox。
Chat Todo/Monitor 非终态预览与应用执行同一更新，绑定 canonical revision 和注册
事实，不再依赖 Markdown。响应丢失后重用原 operation ID，先恢复历史回执，再对新
写入检查当前权限；投影恢复读取当前 head。展示失败的提案在 Dashboard 重载后仍可
发现并重试。频率对应时间由 TS 事务派生，不再由 Chat 每次重试重算。Monitor 预览
不再未经 dry-run 就宣称已校验，Chat 也不把 pending outbox 标为展示已验证。
Lease 的 ownership/requirements/status 转换、terminal 与 observation effect
保留各自 owner。本批闭合一个经过审阅的编辑 T1/L5 链路，不代表全部 T1、provider
默认或 promotion。Registry 见证是乐观来源检查，不是配置与 provider 的跨资源原子事务。

Provider-first text/note 更新现可携带当前执行 key 和租约版本，复用 terminal fence，
禁用自动获取及委托覆盖。修改和回执受同一个 provider revision 保护，租约不变。
显式 `--update-operation-id` 支持同凭证、同内容的 CLI 重试，过期或转交后仍可回放
历史回执。缺失／陈旧凭证及历史非活跃租约拒绝；无凭证的旧回执指纹保持兼容。
这是 #4152 的租约 fence 切片，不是完整 T1 metadata 或 T2 effect 闭合；不带新选项
的 legacy 更新不变。用法见 [Todo 合同](../../project-agent-todo-contract.md#lease-fenced-canonical-textnote-updates)。

Monitor metadata authoring 与 poll transition 现共用 `todos/monitor_metadata.ts`。
公开 update 在已有 field-plan 请求内组合该 owner；cadence 在进程内计算，不再额外
调用两次 scheduler RPC。删除 Python 的 observation/replay/counter/scope/boundedness
规则。Create 与低层 Markdown add codec 仍保留 metadata-plan adapter；这不是完整
T1 事务，也不是 T2 的 Monitor 与 successor 原子提交。

有意修正：不再因任一 effect ID 缺失而允许旧 observation 倒退状态；issue-fix 分组
成员更新使用持锁 observation 路径，在 material result hash 改变时递增 generation。
新计数拒绝负数及不安全整数。ISO 日期进行日历校验，codec 保留 Python 的紧凑日期、
周日期、时区偏移秒数及微秒排序，不改写历史。
Lifecycle/ownership 准入现在先于 poll 诊断，未授权请求不能靠非法 metadata 回避
权限拒绝。精确 replay、同秒无 ID 轮询、显式清空及 legacy boundedness 豁免保持。
Plan 不授予权限、receipt 或 promotion；该切片不开放 native Monitor 规划更新。

公开 Todo add/update 现通过 `todos/authoring_scope.ts` 统一解析角色、continuation
绑定、gate 作用域与 deferred 条件要求。删除 Python `write_policy.py` 及 `todos.py`
重复的 scope 选择；Markdown codec 只保留早期 class 检查的适配调用。已物化的 terminal
successor 共用最终 scope 不变量，不执行草稿默认值推断。
有意修正：显式全局／单 lane 作用域优先于作者默认值；显式绑定冲突拒绝而非静默覆盖；
不得从 actor 或 `goal_bound` 推断全局 gate。省略 scope 的更新、历史已完成记录修复、
lifecycle／lease 权限边界保持。

这是 T1 的 authoring-scope 前置闭合，不是整个 update 事务完成。其余 metadata 扩展及
validation／effect 闭合仍属于 T1/T2。Native update 保留原 text/note patch allowlist，
另接有界 planning intent；legacy codec／lock／writer 仍有实际 caller，本批不退役。

受检入的 generator 校验语言中立 contract，并生成深度不可变的 Python/TypeScript
binding，覆盖原生 domain 与 projection section。两端 runtime 直接 import 生成物；
CI 检查源数据一致性并拒绝陈旧生成物。这删除了重复 contract loader，但不改变
Todo 语义或 promotion policy。

coordination 路径使用同一份语言中立的 `coordination_state_contract_v0.json`。
原生 `TodoDomainRecord` 持有任务语义，包括 `archive_state`；
`TodoProjectionMetadata` 包含 `source_section` 和可选 `index`。TypeScript reducer
与 provider-first collection reader 接受独立版本的原生 domain manifest；测试证明
原生创建、归档、receipt replay 与 store reopen 不需要 Markdown metadata。Python
仅将 typed read result 适配为兼容 summary。这是 contract 检查点，不是已经完成的
CLI lifecycle cutover。

### Lease 领取与生命周期收敛（2026-09-18）

独立 acquire/接管和维护共用 local provider/source fence。`task_lease_acquire_decision.ts`
拥有领取准入和 materializer，legacy acquire 与 canonical 原子 Todo claim 复用；
`task_lease_state.ts` 解释完整 canonical facts，归档 holder 不再阻塞 scope。Python
只通过一次 native 请求传注册事实，不重建 canonical Todo/lease head；generation
耗尽明确拒绝。

Acquire receipt 本身不证明当前执行权：创建 CAS 的原样重试恢复原决定，再检查
当前 owner/key/epoch；续约后返回当前 proof，过期/释放/转交不会复活旧执行。
Canonical 完成可经既有 outbox 重建缺失的 Markdown 展示。真实 CLI、规模及
native/imported fixture、进程中断和只读四臂演练覆盖此边界。见[操作与兼容](../../reference/canonical-lease-renew.md)。
跨外部 effect 的 executor 持锁、剩余 L2/L4/L5 caller、D2/D3 和新 Goal 默认化仍
独立验收；本批不是全部 L3 或 T4 retirement。

### Local provider opening 边界（2026-09-13）

Provider-first runtime 现在只有一个 typed local opening seam。没有 selector 时
明确解析为 File profile（`source_authority=file_v0`）；存在经过资格验证的 local
selector 时，同一个 handle 报告 SQLite；只有通过 service-owned factory 才能报告
PostgreSQL。runtime command 不再重复构造 provider，也不会因为某个
`AuthorityStore` 实现而把 PostgreSQL 误报成 File。

Selector 只携带 provider、goal、tenant 和 store-incarnation facts，不携带凭据或
database client。已选择 provider 的失败保留其 provider source 并 fail closed，绝不
静默回退到 File 或 Markdown。这是默认 provider 边界与 TypeScript ownership 的重构，
不是 SQLite promotion、整 Goal cutover 或 PostgreSQL service 已交付的声明。现有
promotion、soak、retention 与 writer-fence hold 均保持不变。

Provider-first `todo update --text/--note` 保留不改变认领关系的文案修正：
已注册、未被排除且符合 agent binding 的 actor，可以编辑未认领、active 且未完成的
agent Todo，不得因此写入 `claimed_by`；其他 claim owner 的 Todo 仍拒绝修改。
只允许 patch/clear text 与 note，不授予治理字段修改权或 hard-lease 执行权。
资格检查、CAS 与 receipt replay 由 TS 事务持有；promotion 不应把文案修正变成认领。
Provider conformance 同时覆盖原生与 v0 记录，真实 CLI 在无 Markdown 时验证该行为。

默认 Markdown 与显式 promotion 两条 `todo claim` 路径现在都由同一个 TS claim
decision 处理 actor、registration、role、status、archive、exclusion 与现有 owner
检查；Python legacy writer 只在持锁后提交该 decision。显式 promotion 后，claim
只跨一次 runtime 边界，由同一 TS 事务同时处理原生
和 v0 记录。新 claim 要求 active、open Todo，并检查当前 actor/lease；同一 operation
的重试先恢复原 claim receipt，再考虑当前资格。观测时间和当前注册信息不属于请求
身份。回放 receipt 不续租，也不表示当前仍持有任务。非 preview 的成功 `no_change`
同样在 head CAS 下持久化终态 receipt：存储 revision 可以前进，但 Todo 状态、
`updated_at` 和 domain events 不变。结构合法的空注册名单允许历史回放，不能发起
新 claim；非法名单仍失败。preview 保持零写入，非法 preview boolean 在访问 provider
前失败。CLI 默认仍为每次调用生成新 operation id。在已经 promotion 的 canonical
authority 上，可显式使用
`loopx todo claim --goal-id <goal> --todo-id <todo> --claimed-by <agent> --agent-id <agent> --claim-operation-id <public-safe-id>`
进行跨进程重试：响应丢失后复用相同 id 和请求意图；同 id 搭配不同意图会失败。
preview 不消耗该 id。legacy 模式会拒绝此选项，不写入也不自动 promotion；省略
选项即可保持默认行为。历史 replay 不授予 lease 或当前所有权，claim/lease 联合
获取仍是后续工作。

Promotion 后的 `todo add` 已成为同一 authority owner 上的
原生 create transaction。Python 只校验既有 CLI 参数并一次性适配为带版本的 domain
record；语义重复、replay、actor/owner 资格、CAS、receipt 和 projection-outbox
mutation 都由 TypeScript 持有。preview 与真实 subprocess CLI 路径会先删除 Markdown
state file 再验证，因此 promotion 不会悄悄恢复 Markdown 写入。completion-validation
argv 保持 typed data，不退回 shell 编码的兼容字段。未 promotion 的默认 goal 在显式
promotion 边界前继续使用既有 Markdown transaction。

terminal-lifecycle stage package 将该边界扩展到 promotion 后的 `complete`、
`supersede` 与按 role 执行的 `archive`。TypeScript 持有 admission、claim/lease fence、
successor 校验、completion-policy reduction、CAS、receipt、projection intent 与归档
选择；Python 只投影 registry fact，在两次 typed reduction 之间执行显式声明的
validation effect，并 drain 兼容投影，不再为 promoted goal 重建 terminal state
machine。canonical Todo 只保存 validation-required marker 与声明摘要；raw argv 留在
权限为 0600 的 host-local sidecar，恢复时必须先匹配摘要，才允许执行 effect。导入的
v0 Todo 继续按旧 `index` 归档；provider-native record 按持久 completion/update 时间
和 Todo identity 排序。当前 Todo graph 中已经不存在的历史 lease file 继续作为审计
历史保留，不再投影回 canonical live head。

所有非 preview 的 terminal/archive 入口都会取得 bootstrap 与 rollback 共用的逐 goal
shadow-maintenance mutex，并在打开 canonical provider 之前重新检查持久 management
state。因此 lifecycle write 不能与 bootstrap/rollback transition 重叠，也不能绕过其
write hold。durable promotion fence 已存在但 canonical head 缺失时，调用方收到 typed
canonical-authority outage 和明确的“恢复后再重试”动作；该错误不会被重新包装成 legacy
writer fence，也绝不授权回退 Markdown。

该阶段使用同一份只读、生产复杂度快照做三臂资格验证：不可变 legacy baseline clone、
隔离 file provider、隔离的真实 PostgreSQL provider。两个 provider head 必须精确相等；
legacy 臂按显式 compatibility projection 比较。归档时仅从 legacy hot view 排除
provider 保留的 archive 记录及其历史 lease，并且只有先证明每个 role 的相对顺序完全
一致，才可忽略导入 `index` 的绝对值；domain 字段、归档选择、active lease 与非目标
记录不得归一化，源快照必须不变。可复现命令位于
`examples/control_plane/authority-three-arm-rehearsal.py`。同时提供确定性、public-safe
的规模 fixture，让每个 provider suite
覆盖相同的 status 组合、当前／已退役 lease、standing decision、validation、successor、
replay、concurrency、归档压力与 hard-lease fence。该 fixture 是持久回归覆盖，不能替代
对当前状态的只读三臂演练。

从该 checkpoint 起，凡声称推进本 RFC 的 PR，都必须遵守
[production-scale fixture 维护契约](../../development/testing-and-quality.md#production-scale-fixture-stewardship--生产规模-fixture-维护契约)：
声明 fixture 影响、覆盖所有受影响的 provider arm，并把只读三臂演练保留为独立的
promotion gate。

### Provider-neutral projection conformance 检查点（2026-09-12）

conformance 边界现在为 legacy v0 与 native Todo record 共用一个 projection-fixture
builder。它统一负责确定性的 Unicode 排序、read-model digest/field 构造，以及仅限
兼容层的转换；provider 测试不再手工重建这些字段。规模 envelope 显式声明 status
顺序并校验计数，因此 JSON key 顺序变化不会静默改变哪个 Todo 获得 lease、successor
或 archive 角色。

File、SQLite 与 NoKV suite 现在会在两种 record shape 上执行同一组生产规模 terminal
case。另有独立 parity harness，使用三个隔离 provider 重放同一条 seed、observation、
lease 序列，并在忽略 provider-specific revision token 后比较 logical head 以及已提交
的 event/projection/receipt trace。这是 conformance 证据，不是新的 authority writer、
provider 默认值或 promotion 声明；PostgreSQL 仍受现有真实服务资格化 gate 约束。

旧 v0 consumer manifest 继续可读，并保留所有已有字段。默认 Markdown capture 仍
输出 v0；本 PR 不改写已存 head，也不自动晋升 goal。schema 分层不等于允许后续迁移
丢失 v0 provenance 或改变旧排序。

### Canonical Todo 展示检查点（2026-09-12）

authority 边界现在把 presentation 作为一等 projection contract，而不再把它命名为
`legacy_projection`。共享的 TS presentation normalizer 会把 v0 wire shape 的
`source_section`／`index` 映射为 `display_section`／`display_order`；native record
则根据 domain 的 role／archive state 推导展示 section，绝不伪造持久化 index。两种
wire shape 共用同一份 normalized presentation contract，wire 坐标不构成第二套 Todo
state machine。

Todo creation、terminal successor materialization、projection validation、
standing-decision ordering 与 archive ordering 现在共用同一个 presentation owner。
两种 wire shape 共用 canonical domain validator，v0 record 只是从已校验 domain
record 经过 adapter 生成。这统一了语义 owner，但不重写 v0 head 或 receipt。

Python read caller 现在直接导入语义 owner；兼容 facade 不再是内部依赖。Python 的
展示排序在存在 source `index` 时保持其顺序，在 native record 上使用完成／更新时间
加 Todo identity 做确定性排序，因此兼容 shape 不会泄漏进业务 eligibility 或 lifecycle
decision。

后续迁移可以持久化可选的 canonical `presentation` object，但必须先证明导入的
section 到底是 provenance 还是当前 display intent，并资格化稳定的 display-order
策略。在此之前，native display position 仍在 renderer 边界派生，不能参与 authority
lifecycle decision。

### 长程持久化也是迁移收益的一部分

产品目标是单个 goal 至少持续十个自然日。shared-authority RFC 的
[第 7.2 节](./shared-goal-authority-state-provider-v0.zh-CN.md#72-十天-goal本地存储资格化目标提案)
统一维护负载、性能预算、保留策略和真实 soak 验收；变化的容量数字不在此重复维护。

与 provider-first Todo caller 同期推进完整本地持久化切片：资格化嵌入式事务存储
（SQLite 为首选候选）、有界 live head/receipt lookup、crash-safe checkpoint 与精确
历史 readback。file-v0 保留作 conformance/import 基线。只把 Python 改为 TypeScript、
换数据库但保留不断增长的 head，或只通过加速容量测试，都不证明十天连续性。
本地晋升等待容量与自然时间双重资格化，不等待 PostgreSQL 服务，也不在第十天使
receipt 过期。

### 交付语义：先修正规则，再迁移

Replan 的义务结果规则现收敛到 `work_items/replan_semantics.ts`：接受结果选择、
vision path／terminal 一致性校验与对应 refresh 输入投影共用同一 owner。
Python 保留 progress 归一化／新颖性与持久化适配，不再重复义务匹配规则。
这是有界规则收敛，不是 settlement writer 或存储迁移。先刻画既有接受语义，
再修正所有 vision trigger 的可执行写入投影，并验证真实绑定 CLI 闭环、回读及
资格范围错配反例。Checkpoint 恢复与 in-flight 规则仍由既有边界负责，
不新增 capability、provider 或设置。

交付历史边界将 `classification`、`health_check` 与 `recommended_action` 视为
叙述文本。它们不能生成或解除 follow-through obligation，不能证明 outcome，也
不能判定交付规模。例如，`unblocked after dependency update` 不构成 blocker
receipt，`implemented network protocol parser` 不构成仅完成准备工作的证据。

`control_plane/work_items/delivery_history.ts` 现在持有完整的交付历史到后续义务
读投影：outcome、turn kind、scale、连续计数及 follow-through。Status 先选出一批
有界历史，再调用一次 `work_item.delivery_history.project`；quota 的 latest-run
消费者以单行调用同一投影。相比此前 Python 本地判断，新增 managed-runtime crossing，
但不是每个字段或每条历史各跨一次。Python bridge 只发送紧凑 typed facts，不发送
叙述或证据正文；classification 在决策之后作为展示标签附加。
删除被替代的 `delivery_signals.py`、`outcome_followthrough.py`、turn-kind 推断
及 status 连续计数 wrapper，复用已有 TS blocker 绑定规则。Python enum codec 和
settlement writer predicate 仍有真实 caller，因此保留；本批不是 writer/事务或
provider 迁移。

验收不变量是**叙述非干涉**：固定 typed fields 与配置，改写叙述或增加未经验证的
`compact_evidence` / `case_result` 对象，都不能改变交付语义与后续执行义务。
classification 保留为历史标签；没有明确展示消费者时，不保留旧预测逻辑。

- 合法的显式 outcome、turn kind 和 scale 保持原有语义。显式 blocker kind
  继续可读。带作用域的 typed blocked observation 必须通过既有 work-item/evidence
  绑定检查，才能将 gap 判定为 blocker writeback；只有 `outcome_gap` 不够。
- 历史字段缺失或不受支持时保持 unknown。unknown 中断连续小规模／outcome-gap
  证据计数，不视为成功或推断出的失败。未配置 floor 且没有 outcome 时，保留
  `not_configured` 展示哨兵值。
- 后续声明校验与历史投影共用一份 TS 诊断：新写入拒绝“进展 + 仅准备”、
  “主要成果 + blocker／typed blocked observation”和“主要成果 + 显式继续义务”
  的矛盾组合。历史冲突以 `unknown` 和 `delivery_claim_conflicts` 保持可读，
  不改写持久化记录或已结算 receipt；合法局部进展和 blocker 写回不受影响。
  这是有意的写入／读取行为修正，不调整小步交付策略，也不是新的证据验证器。
  Refresh 先按既有顺序校验各字段，再以归一化结果检查组合语义，之后才读取
  registry 和创建锁。因此非法输入优先于存储错误返回，dry-run 也一致；依赖
  当前状态的准入与写回仍在同一 runtime 锁内完成。
- delivery response 是 quota、handoff 和 work-lane 共用的 TS 只读决策：只有
  绑定的 blocked observation 与当前 canonical Todo 的明确、合法等待条件一致，
  才不施加历史 outcome floor。来源缺失／非法、其他 actor claim、exclusion 和
  无绑定的旧 blocker 标签不能建立该例外；其他可执行工作仍由 canonical planner
  选择。unknown 刷新中断统计，不清除 Todo/replan 义务，不新增持久化交付账本。
  连续表层交付监督与独立的小规模交付规则保持不变。
  该例外必须匹配解析后的 target identity 和合法 task class；monitor baseline、
  capability、PR repository/number 也绑定当前 Todo。缺失 actor 或陈旧／错配的
  condition 不能解除监督。旧式不完整条件仍可读取，但不构成正向等待证明。
- 新交付声明通过现有 writer API 写显式 enum，例如
  `refresh-state --delivery-outcome ... --delivery-batch-scale ...`。
  纯状态刷新仍可不声明交付；本批不强迫每次刷新声明进展。既有写入 enum 校验、
  settlement evidence、quota 和 gate 检查继续有效。
- 旧 outcome-marker/hint 配置继续可读，并保留 floor 是否配置的含义；配置中的
  词语不再分类 run。不改写持久历史，也不新增开关恢复错误行为。此前由未结构化
  历史标签推导的 status、handoff/review 和 quota 决策会发生明确的行为变化。

迁移保留独立刻画的合法 typed 行为，并验证真实 refresh/history/status/quota 入口、
批次基数与叙述非干涉。有一项有意修正单独披露，不能混称 parity：两个非法 work-item
identifier 不能仅因都归一化为缺失值而被视为相等；此类 observation 不能推断出
blocker writeback 或解除后续义务。仍在使用的 Python writer predicate 同样拒绝该
情况，不改写任何活跃历史。

下一步另行盘点仍缺少 material-result 字段的 writer，并用明确兼容计划退役旧
marker/hint 配置。精确的旧 lifecycle classification code、历史选取与其他 cadence
policy 不在本批范围内，不能宣称所有 writer 已迁移或全局已无文本规则。这一读策略
闭合不改变下文 provider-first Todo 顺序，也不等待 provider cutover。

### Legacy 字段规则退役检查点

`todos/field_update.ts` 现在持有 legacy `update`、`claim`、`complete`、`supersede`
line writer 共用的完整 metadata intent 组装：status 与 completion 时间、未传与显式
清空、binding 优先级、已移除 policy 的修复、resume-generation 配对及 completion
metadata。它直接组合已有 TS completion rule。被替代的 Python decision 分支，以及
失去最后调用者的 `todo.completion_state.metadata_updates` RPC/facade 一起删除，
不保留为 fallback。

这是一份纯 plan，不是 admission 或 provider commit。Python 仍保留 Markdown 定位／
编码、字节级 no-op 检查、锁与外部 effect；本批不宣称迁完公共 role/binding admission
或 event writer。Native planning 现按 T1 所述组合此 owner；不支持的字段不扩权、
不 promotion goal、不增加第三条存储路径。plan 拒绝时，现在连调用方的内存行缓冲也保持不变；公共
事务在拒绝时原本就不会提交。

每次 legacy line write 有一次 field-plan crossing：普通编辑替代原 metadata RPC；
已经 finalization、携带 override 的 completion 会增加一次 planning crossing。
带缓存的 codec normalization 调用仍在。这兑现的是语义代码删除，不宣称每个命令
都减少 round trip。完整 goal cutover 后随最后 legacy lifecycle caller 删除 adapter，
或者迁移该 caller 时将 plan 折叠进其粗粒度事务；不得继续扩张逐字段 RPC。
Markdown renderer 长期保留。

### 下一步交付顺序

终态长期保留 Markdown 作为**可读投影**，不是第二个数据库。本 RFC 负责唯一 typed
业务规则/事务 owner 及删除收益；[shared-authority RFC](shared-goal-authority-state-provider-v0.zh-CN.md#下一步交付与并行-provider-工作)
负责 durable truth、恢复、cutover 与投影交付。删除 Python decision 不以前端 CLI
全部改成 TypeScript 或 `loopxd` 落地为前提；输入适配和外部 effect 执行可以保留 Python。

Objective 编码与读回复用既有 Goal metadata／section owner；旧注册按解码后的值与
精确叙述正文比较。这是展示边界闭合，不增加 TS transport，也不迁移业务权威。
见[文档边界](../../reference/protocols/active-state-structured-projection-v0.md#markdown-ownership-boundary)。

本次 lifecycle-admission 切片将 legacy claim/update 准入、委托 action/reason 检查、
ownership-holder 路由及 native complete/supersede 统一到
`todo_lifecycle_decision.ts`。Native text/note 编辑与 terminal transition 在进程内
复用预授权 lease fence；`authority_core.py` 只投影仍有真实调用方的准入和 terminal
决策，不再暴露独立 Python command 或 effect-runtime handler。Mutation admission
不能完成 Todo，进程内 fence 不能授予 actor 权限或提交变更。这立即删除重复规则，
**不等于删除完整 legacy update writer**。字段 patch、省略/清空、monitor/resume effect 和 validation
仍需收口为完整 update transaction。Legacy 准入及持锁 gate 仍跨 runtime；本次减少
语义 owner，不宣称减少 crossings，native transaction 仍进程内调用。下一步将这些
crossing 一起折叠进完整事务，不能沿着 adapter 逐字段继续加桥。

等待/恢复规划现由 `todos/resume_planning.ts` 一次完成 deferred、resume-blocked、
monitor-repair 和 blocked-successor 选择。Quota 为每个 source summary 将容量条件与这些 lane 合为一个请求，
在 TS 进程内复用既有 resume evaluator；vision-wait、agent-scope、frontier、replan
共用此投影。删除旧 `deferred_resume.py` 规则 owner，不保留第二份实现。Python 适配层
只保留 reader 兼容边界，不再决定 claim/exclusion 选择或等待路由。Resume、
route-continuation、succession-warning 共用 `compact_projection.py` 的字段省略与 scope
归一化；各 caller 的文本推断差异及 succession 独有字段显式保留，priority rank
归一化仍在 resume adapter。这闭合一个读取策略族，不是整个 quota reducer，也未
迁移 monitor/lease writer。相同公开排序键保持 source 顺序；完整计数先于展示截断；
`monitor_changed` 不进入旧 `todo_done:<monitor>` 修复路径。只读结果不授予执行权限，
也不是生命周期 receipt；caller 在进程内消费 typed Todo record 后可删除此适配层。

条件 evaluator 与规划 owner 现在共用恢复条件诊断；agent-scope 消费已选好的修复 lane，
不再重新解释 target 类型/状态。旧 compact 输入缺少 kind/class 时，只从 typed
`resume_when` 和同一快照的 Monitor 记录补足，不从叙述猜测。本次 refinement 包含
明确行为修正：自依赖，以及对未完成 Monitor 的 `todo_done` 依赖，被诊断为
`resume_condition_invalid`，不再当作普通 pending wait。历史已完成 Monitor 依赖仍可
满足；完成依赖的目标缺失仍为 pending，因局部快照中的缺失不能证明依赖非法。合法的
generation fence、claim/exclusion、capacity 和 PR 等待语义保持。非法条件不进入
精确 blocked-successor 等待；Monitor 完成依赖的修复仍可见，且仅在合法执行者范围内
可选。此诊断不自动改写为 `monitor_changed`、重置 baseline、重写持久状态或增加写入
准入。普通 add/update 准入及覆盖全部非法条件的通用修复动作仍是独立范围；不能宣称
全量零行为变化或全部 Todo writer 已闭合。

#### 命令回执与恢复的统一所有者

在基线 `bfd1ec8db`，create、claim、update、complete/supersede、archive 与
Monitor poll 分别重复 envelope 匹配、结果投影和 CAS 后回读。
现在由 `coordination/command_receipt.ts` 统一这些语义；各命令继续拥有请求
规范化／摘要、准入、回执业务载荷校验及状态变更。
`coordination/todo_archive.ts` 单独拥有归档保留事务，与终态校验和 lease
释放分离。内部调用方直接导入新 owner，旧模块不保留无实际用途的 re-export。

这些 canonical 命令路径有以下明确的可观察变化：

- 提交已 applied 或 ambiguous、但回执不可读时，结果保留为 `ambiguous`，
  携带 `recovery.operation_id` 和 `retry_with_same_operation_id=true`。
  读取失败不能抹掉可能已经持久化的事实。提交响应抛异常后只查一次回执，
  不自动再次写入。
- 明确的 CAS conflict 或提交失败，不再被随后的诊断读取失败覆盖。精确的
  历史回执仍优先返回；applied 响应却缺少回执，仍然是协议失败。
- create／Monitor 结果对象、update／terminal／archive 的变更判定损坏时，返回
  `invalid_coordination_command_receipt`，不能通过隐式转换变成成功 replay／
  no-op，也不能直接逸出为未处理的解码异常。Claim 保留原回执错误码和历史
  省略 changed 字段的兼容解析。
- 读取失败统一携带 `changed=false`；当 status 为 `ambiguous` 时，它表示
  尚无成功结果证明，**不表示**已证明没有写入。身份冲突与回执缺失的错误消息
  采用统一 coordination 措辞，原 reason code 保持不变。

原请求摘要、回执 schema、成功载荷、no-op 身份消耗、lease／grant 校验、永久
Markdown 投递和默认 provider 保持兼容。完整生产规模 fixture 现在覆盖七种
命令的正常提交、响应丢失、回读不可用、响应抛异常，以及插入其他提交后的
历史重放。真实 File／SQLite／PostgreSQL 和 NoKV transport conformance
共用该矩阵。三路只读源演练还在真实 File／PostgreSQL 归档提交后丢弃响应。

本次删除重复的 TS 事务权威，不宣称删除 Python 业务 writer；没有新增 bridge
或 RPC，跨运行时调用数不变。T1 的 metadata／effect 闭合、T2 的带 lease
Monitor 和 D1–D3 资格验证仍待后续；兼容编辑器及其他命令保留各自的回执合同。
本次不代表 Goal promotion。

#### 当前 stack 合入后的执行卡

这是**条件式执行规划**，不是所有阶段已完成的声明。2026-09-09 核查时，#4053、#4117、
#4129、#4122（resume 诊断／规划）、#4134（交付历史）、#4136（声明诊断）均已合并；
canonical delivery-response 后续批次基于这些已合入的 main。执行前核验实际 merge
commit。#4121（SQLite 候选）和 #4101（投影 receipt 保留）是独立候选，不自动成为
依赖或已批准的默认配置。

只执行下面第一个未闭合阶段，不同时启动所有阶段，不重建已完成事务。具体任务
写入 LoopX Todo；本节作为共享路线，不再维护另一份 per-agent 状态账本。

**T0 — 在下一实现 PR 内对齐已合入基线。**

- Fetch 目标 remote base，记录 SHA 和每项依赖的实际合并状态。核对代码而非 PR
  标题；依赖未合并时，使用明确选定的 stacked base，或暂停该依赖单元。
- 从 `loopx/control_plane/` 下的 `coordination/todo_update.ts`、
  `todos/field_update.ts`、`todos/provider_update.py`、`todos/native_update_plan.ts`、
  `todos/line_update.py`、`scheduler/monitor_poll_writeback.py` 及公开 caller
  入手。符号移动后重新定位，不恢复已删除 wrapper。
- 形成紧凑 caller 表：公开操作、promotion 前后来源、TS owner、外部 effect、
  保留的 legacy caller、精确删除条件。随实现更新完成事实，不单独交付 inventory
  framework PR。
- #4122 与 delivery response 汇合时，在既有 resume owner 对齐 pending/invalid
  诊断。目标缺失不证明合法等待；历史 pending 可读不等于可放宽监督。
  两个合同都验证后，再删除重复检查。

**T1 — 闭合公开 Todo update 事务。**

用户 Todo 的 completion update 现组合 canonical 编辑 planner 与 terminal
事务，覆盖原始／编辑后权限、绑定来源的验证、租约释放及 Chat 审阅后恢复。
Python 传输层共用效果执行和失败投影；这闭合一个剩余公共 caller，不代表
所有 T1 caller 或旧 writer 已退出。见[操作与兼容边界](../../reference/canonical-todo-completion-update.md)。


当前 ownership slice 已将 promoted 路径的 claim 转交、清除和执行排除编辑接入
typed update planner。规范化参与请求身份，因此重放不能恢复已被后续操作取代的
claim。带 lease 的 ownership 变化仍必须走 lifecycle，不是 metadata 授权；未
promotion 的 Goal 继续使用旧 writer。这是有边界的 T1 闭合，不代表所有 Todo
字段或 Goal promotion 已完成。

已闭合的前置项：`todos/public_update.ts` 在同一锁内快照上组合 authoring scope、
external-wait 拓扑和 Monitor/field 规划。公开 Python writer 不再逐个调用这些
leaf RPC，也不推导 Monitor 等待基线。`update_source.py` 只输送完整、紧凑的
active/archive 事实，不使用受展示条数限制的 inventory。局部拓扑修改必须验证
保留的等待条件；纯文案修改保留原 fence，不重新设置等待。显式清除条件后，仍可
修改原来的拓扑。锁内 completion proof 先于纯规划检查，因此 proof 已过期时，
优先返回该失败而非其他非法字段诊断；两种失败均不写入。
这里删除的是编排而非持久化：lifecycle/lease 准入、completion effect、writer
lock、capture、provider CAS/replay 仍由既有 owner 负责。内部 terminal/import
field codec 仍有真实 caller，不引入公开 update 限制。Native metadata 扩展和
T2 原子后续动作尚未全部闭合。Lease-edit PR #4152 已合入；有界规划更新复用该
fence 及既有 CAS/receipt 事务。下一步继续剩余字段/effect 清单，不另建 update engine。

工作要求编辑首先闭合于没有保留 lease 的非 Monitor Agent Todo，可通过既有 v1
planning 事务更新 `action_kind`、`task_domain`、`task_repository`、
`required_write_scopes`、`required_capabilities`、`target_capabilities` 和
`explore_result_node_refs`。公开 legacy 编辑与 native planning 共用
`todos/work_requirements.ts`；Monitor successor authoring 与 receipt verification
复用其仓库／capability codec，删除 scheduler 私有副本，不增加 RPC 或 store。
省略／空白标量保留原值，显式空集合清除要求。有意修正：非法成员、不安全仓库和超出
容量的 Explore 引用使整笔公开更新拒绝，不再静默丢掉要求或截断引用；纯文案编辑不会
重新审查无关历史字段。SCP 风格的含密码 userinfo 同样拒绝，包括 Monitor 后继路由；
仅带用户名的 Git transport 仍合法。仓库／capability 别名保持同一规范化 replay identity。
要求不是授权：ownership、决策结果、任意 raw patch、Monitor 编辑及带 lease 的要求
变化仍受限。Python 读取／bootstrap codec 与 legacy writer 仍有真实调用者，本批
不退役它们，也不宣称完整 T1。下一步结合 lifecycle admission 与 validation effect
闭合 ownership／decision metadata，再推进 T2 剩余带 lease Monitor 事务。

声明式决策元数据现已进入同一个 v1 planning 事务：`decision_scope` 只能写入
`user_gate`，`required_decision_scopes` 只能写入 Agent Todo；两者统一归一化为公开的
`decision_scope_v0` 形状，按首次出现顺序去重，格式错误或角色不匹配时整笔原子拒绝。
显式空的 `required_decision_scopes` 会清除旧依赖。`decision_outcome` 与
`decision_scope_outcomes` 仍属于 effect-owned terminal state，native planning 边界会
拒绝它们。公开 planner 也保留 scope 字段的省略语义，不再把省略物化成 null，因而无关
metadata 修正不会擦掉保留的 user-gate scope。这闭合的是 T1 的声明式 metadata 部分，
不授予批准、lease、完成或 promotion 权限。

- 复用现有 provider text/note 事务、lifecycle 准入、field-plan 和 completion
  规则。先枚举公开 metadata 编辑与显式 clear，不把 `UPDATE_FIELDS` 扩成所有存储
  字段，也不让 generic patch 获得 terminal transition 权限。
- 一次粗粒度 TS 事务覆盖合法 intent、actor/claim/exclusion/lease、字段语义、
  最终验证、CAS 与 replay；外部执行和 checkpoint 留在 effect adapter。
  无法安全一起闭合的 monitor effect 留到 T2，并显式列为不支持。
- 同 PR 删除被替代的 Python update decision 与 leaf-RPC 编排；未 promotion
  caller 仍需要的 codec、lock 和 compatibility writer 保留，不宣称完整 writer 退役。
- 通过公开命令及受影响真实 provider 验证：省略／清空、unclaimed 文案修正与受限
  metadata 的差异、other-owner/lease 拒绝、no-op、非法输入无写入、竞争 revision、
  retry 和丢响应恢复。

Monitor 配置现通过既有 native planning transaction 和 public legacy planner
共享 typed authoring codec：target／cadence／due／expiry／watch-only 属于配置，
观察 hash、时间、effect identity 和代数仍属于 polling lifecycle。删除 Python
重复字段 allowlist 和 native 对 Monitor 的整体拒绝。配置保留观察历史，已观察的
Monitor 不允许换 target；底层 import／observation codec 保留真实 caller，不作为
raw update 开放。普通 CLI/API、显式清除、回执恢复和既有 active lease proof 已覆盖；
owner-confirmed Chat 委托和 leased Monitor polling 仍是独立未闭合路径，配置文本
不授予权限。

本地默认化计划统一维护在 shared RFC 的
[执行顺序](shared-goal-authority-state-provider-v0.zh-CN.md#执行交接与汇合顺序)：
L1–L4 闭合 mutation 语义，L5 汇合 consumer，L6/L7 完成存储与 capture，L8 验证整
Goal 迁移，L9 修改新 Goal 默认。每包用新的 owner 删除重复决策。无需等待完整 TS
launcher：一个粗粒度 TS 请求拥有完整事务时，有限的 Python 输入／外部 effect
adapter 可保留；不能把执行卡拆成不断新增 leaf RPC，也不能绕过仍在使用的 caller。

Canonical 租约请求解码通过判别联合区分 provider mutation 与 legacy 持锁请求；既有租约 owner 组合显式 claim 交接与原 lease transition，在一次 CAS/receipt 中提交。Python 只传递 opt-in 并投递原 Markdown projection；双方资格复用注册 Todo 的限制规则。Canonical 命令不再携带 lock/PID/terminal-release 参数，已认领任务的原子交接由此闭合；自动上下文交付与跨外部 effect 的 executor fence 仍独立验收。见[操作与恢复](../../reference/canonical-lease-renew.md#atomically-hand-over-claimed-work)。

**T2 — 闭合 monitor 写回及原子后续动作。**

已交付有边界前置项：`scheduler/monitor_successor.ts` 统一 quota preflight、legacy
writeback 与 receipt verification 的后续路由校验和规范化，删除 Python route
guard/resolver 及 TS 回执端独立的默认值／capability 解释。非法 capability 项、非法
后续 claim 和未声明 material change 的 follow-up 在 observation 写入前拒绝；合法
action/claim/capability 别名和 Git transport 在回执核对时指向同一路由。v0 replay
digest 仍绑定原始 wire observation，不能因规范化而悄悄使 pending receipt 失效。
无需 Node 的 repository/bootstrap codec 暂留并做跨运行时对照，不引入启动依赖。
原生 `coordination.local_authority.monitor_poll` 现将无 lease Monitor 的观察及请求的
独立后继，绑定同一个 canonical revision，以一次 CAS 和持久 operation receipt
提交。它组合已有 generation、successor route、User authoring scope 和 Todo create
planner；单项 create 与 Monitor 批次共用创建准入／语义去重，legacy preflight 与
native commit 共用目标选择。Python 只路由意图并交付既有 projection outbox。

明确的语义修正：拒绝已完成／归档的 Monitor；target-key 选择排除结束的历史项，
但多个活跃匹配仍要求显式 id；创建后继必须实际推进 material-change
generation，不能对相同证据重复声明 `material_change=true` 就继续生成任务。
原 operation 重试恢复原后继，不创建新工作；不附带后继的新 observation 仍可接受。
User gate 复用既有 actor-bound scope，不推导全局 gate。

带 lease Monitor 现与 Todo metadata update 共用当前非终结 lease fence。公开
`quota monitor-poll` 将 execution key/version 贯穿 pending plan、canonical transaction
和业务回执；观察、generation 与独立后继在同一 CAS 提交，lease 保持不变。
Canonical 到期 Monitor 恢复可选，但调度不授予写权限；租约是否有效取 runtime
当前时间，不取调用方提交的观察时间。

Quota preflight 将原始准入决策冻结到版本化 pending receipt；即使 Monitor 已不再
到期或 lease 已释放，恢复仍可凭原业务回执结算，不替换租约、不重做业务。
无 proof 的 v0 request identity 和已完成回执保持兼容；无原准入依据的旧 pending
沿用当前准入，无法证明历史恢复时明确报错。见[观察与恢复协议](../../reference/protocols/quota-monitor-observation-receipt-v0.md)。

尚未闭合：不隐式授权跨 owner successor claim；未晋升 Goal 保留旧 writer，并拒绝
显式 lease proof。业务与 quota 仍是分别可恢复的事务，canonical 成功独立于 Markdown
delivery pending；这不代表全部 T2 命令或整 Goal promotion 已完成。

- 保留的 issue-fix 分组 Monitor caller 现通过既有 Todo update 事务（request v4）
  传递观察意图；观察、显式无 lease 再激活、终结标记清理、generation 和 receipt
  一次提交。Legacy 与 canonical update 共用字段／Monitor planner，不新增 RPC、
  raw patch 权限或轮询引擎。完成后的新观察即使 hash 相同也推进新一代；历史重放
  不会重开当前任务。无变化的分组也能恢复显示，包括带优先级前缀的 native 文本。
  见[观察更新与再激活](../../reference/protocols/quota-monitor-observation-receipt-v0.md#observation-updates-and-reactivation)。
  再激活已由既有 TS owner 原子退役旧 execution；分组对账的完整桶集合决策现由
  `capabilities/issue_fix_monitor_reconciliation.ts` 负责，Python 保留 ledger IO、
  公开 writer 调用和展示交付。hard-lease 观察／结束先领取自己的有限期 execution，
  领取后重新核对计划，只释放本次执行。观察提交后进程退出，原样重试可清理残留
  lease，不重复 Todo 业务写入；再激活本身仍不授予执行权。
  缺失／损坏 ledger、重复活动 target、旧的空组观察现在明确拒绝；成员 hash 保留
  Python 原有 Unicode 排序及 ASCII 转义合同。显式 runtime-root 贯穿读取和写回。
  这是 issue-fix 调用链闭合，不是所有桶的一笔原子事务：后续桶失败不回滚之前已
  提交的桶。无变化重试可以清理自己的中断 execution 并恢复展示。Python 适配器仍
  有真实调用方，不能直接删除。其他 lifecycle caller、跨外部 effect 的围栏、旧持久化／
  capture 和整 Goal 资格仍独立。见[操作合同](../../../loopx/capabilities/issue_fix/README.zh-CN.md#pr-lifecycle-monitor)。
- 保持 unchanged poll/reschedule、generation fence、material-change successor
  去重和可归属 settlement。Monitor 不是 delivery 执行任务；独立 advancement Todo
  不能被 monitor 自身替代。
- 删除被替代的 Python transition decision，外部轮询保留 effect adapter。
  验证重复 poll、阶段间 crash、race、effect 失败、其他 actor claim 和 no-change
  不形成交付。必要命令 effect 尚不支持时暂停整 Goal promotion，不能回退 Markdown 写入。

**T3 — 闭合剩余 structured consumer，删除各自旧读路径。**

Periodic-report 的阶段判断、实时编辑输入回退与审批重试现共用 canonical-first
Todo 来源；frontier 和报告事实复用同一完整已求值快照。展示缺失、过期或损坏不再
隐藏／复活工作。`capabilities/periodic_report_progress.ts` 拥有报告选择及拒绝重试
排序，删除 Python 对应循环；时间按带偏移的实际时刻比较并保留微秒，canonical
归档拒绝记录仍有效，显式 runtime-root 同时约束 intent 和 Todo IO。已冻结的编辑
请求沿用原始依据，不因重试刷新。见[操作边界](../../../loopx/capabilities/periodic_report/README.md#todo-authority-and-report-retries)。
这闭合一组 T3/L5 消费者，不代表 D1 永久展示新鲜度、D2 耐久性、D3 整 Goal
资格或默认 provider 已完成；剩余工作以当前核对表为准。

Todo 摘要 lane 与裁剪前工作计数现共用 `todos/summary_lanes.ts`，删除 Python 的
lane 分类和隐藏任务推断循环。quota 在作用域筛选后重新计数，不完整来源状态贯穿
压缩与重复投影；公开 canonical Todo 列表保留同版本 acceptance 限制。见
[计数语义](../../reference/todo-work-counts.md)。本切片闭合摘要到 work-lane 的计数
消费者，不代表所有 T3 来源或 D1 展示交付完成；旧格式解码、renderer 及其他摘要策略仍保留。


Goal Channel 所有权观察现从完整 canonical Todo／lease revision 读取，并与 legacy adapter 共用 TS 批量规则；删除展示层的时间／代数／冲突判断和晋升后的本地文件读路径。空值、不可用与截断分别披露，见 [coordination observation](../../reference/coordination-observation.md)。这只闭合所有权观察 reader，不宣称其余面板或整 Goal 晋升完成。

D1 的文档归属切片把读取、编辑与投影放到同一可见区域／Todo 行解码边界，修复
fenced 示例被当成真实任务、归档 end marker 后叙述进入历史、稀疏历史行号及归档
优先级阻塞读回的问题。投影复用普通状态的耐久原子写入；相同字节的重试仍完成
文件／目录同步，之后才报告 `current`。区域外正文和 canonical record 不被改写。
这是永久 Python 展示／legacy 输入适配层的收敛：TS authority transaction、provider
默认值、SQLite D2 与 D3 promotion 合同不变，不增加 RPC 或另一份业务状态机。
Handoff mode 的 legacy adapter 与原生 CAS／receipt 事务现共用 TS 空闲判断；晋升后的 show/set 使用 canonical mode 和完整 Todo／lease 快照，删除 Python 切换决策。旧 state／lease 锁仍服务未晋升 writer，不能提前删除。操作与回放合同见 [handoff-mode](../../reference/handoff-mode.md)。

Task graph topology 与 inventory/horizon 共用 `work_items/planning_relations.ts`。
一轮纯 TS 请求拥有关系发现、稳定有界遍历、边去重与缺失/截断完整度；删除
Python 的前驱索引、条件拆解和遍历。Python 保留 status 来源适配及节点、
evidence/handoff 的脱敏展示。明确的语义修正：successor 谱系不再冒充完成
依赖，unblocks 方向修正，补 Monitor generation 条件，上限处保留平行关系
和菱形汇合边。详见[图协议](../../reference/protocols/task-graph-projection-v0.md#typed-todo-topology)。
不改变生命周期准入、claim/lease 或默认 provider。来源仍可能不完整：本批
闭合一个 T3 解释边界，不宣称所有图来源交付或 T1–T4 已完成。

Lease inspect 在 promotion 后从同一 canonical revision 读取 Todo、lease 与
handoff mode；canonical 无租约不复活本地旧文件，provider 失败不回退 Markdown。
结果携带 provider revision，读取不修复展示、不修改租约。两条路径现由
`task_lease_inspection.ts` 统一时间与资格解释，Python 仅投递绑定来源的注册／legacy
事实和响应；诊断字段复用 TS 拒绝规则。归档 Todo 不产生有效租约，active 到期时间
损坏明确报错，来源变化有界重试。未晋升存储保持原状，错误语义变更见
[检查合同](../../reference/canonical-lease-renew.md#what-inspection-proves)。
`task_lease_eligibility.ts` 同时替代 Python authority core 和三处 TS owner 资格判断，
供 acquire、lifecycle 与终态 fence 复用。当前租约是否有效由 acquire 内部根据同一输入
的 owner/claim/exclusion/注册事实推导，不再由旧 `effective` 派生提示覆盖。
其他 Todo 的 scope 冲突仍消费现有完整执行快照；release 保留独立的 key/version
清理门禁。这是一个 T3 reader 与共享规则边界的闭合，不代表 T1/T2 全部事务或
promotion 已完成；Goal Channel 所有权展示由独立的 observation 切片闭合。

Quota 的 scope/claim 消费者现通过每个 source 一次 `todo.quota_planning.project`，
组合选择、有限展示与既有 resume planner。`quota_selection.ts` 替代 Python
claim-visibility 模块及 Agent-scope 中独立的 User gate/action 过滤器。Python
保留旧输入 codec、时钟与 capability/profile 适配；TS 拥有 lane 选择与排序。
本批有意修正两处语义：显式适用的 User gate 不再被他人 claim 或 executor exclusion
抵消；active-next-action 与普通行遵守相同作用域和已移除 continuation 限制。
User action 按 `bound_agent` 路由（兼容旧 claim 回退），不是执行归属；User summary
不再暴露 Agent 执行 `claim_scope`。计数先于展示限流；claim 优先级、Monitor
写回/capability fence 与 resume 义务不变。这些只读判断不授予写权限。
本批不替换 source adapter、不新增 inventory，也不宣称整个 T3 完成；继续按下文
审计剩余消费者。独立 Todo summary 的展示 codec 保留至其真实调用者迁移。

当前有边界交付：shared-goal alignment 与 amendment admission 每次决策共用一份
`shared_goal_work_source.py` 快照，promotion 后复用 canonical Todo summary；同一次
provider 读取可返回同 revision 的 lease。缺失／空／陈旧展示及旧 lease 文件不再是
fallback authority。`shared_goal_work.ts` 统一这两个消费者的开放工作、claim 和
exclusion 筛选，删除旧 Python selector 与 amendment 的第二次 Markdown 解析。
被排除的工作不推荐给该 Agent，但仍可作为 amendment 的影响对象。Source digest
绑定 canonical revision；无事件时 `canonical_todo_snapshot` 的事件序号为 0，不能
冒充 Goal intent revision，digest 变化仍要求 proposal rebase。活动 lease 的非法
到期时间复用现有 TS lease 规则拒绝。本批不依赖仍开放的 #4142，不表示 T1/T2 或全部
T3 完成，也不授予 amendment commit／整 Goal promotion 权限。

Standing decision consumer 收口：`todos/standing_decision.ts` 统一可复用决策的
资格与先后关系，供 status/quota 读取和 archive selection 共用。Python 只解码旧
metadata 并批量调用，删除旧 receipt selector 与 TS archive 内的重复资格判断。
Canonical 读取在生成展示 index 前使用完整 Todo 快照，包括保留的归档决策。
后续拒绝／取消按决策时间覆盖旧批准，不再依赖 Todo ID；矛盾历史无法定序时给出
诊断且不提供 active receipt。此授权面必须有显式 user-gate metadata，不再借用
通知文案启发式。上述有意语义修正见
[decision-scope 协议](../../reference/protocols/decision-scope-v0.md#decision-chronology-not-display-order)。
全部无时间的 legacy 决策保留源顺序兼容，native 展示顺序不充当授权证据；本批不迁移
scope coverage 和 open-gate routing。

后续 decision dependency consumer 闭合：`todos/decision_scope.ts` 统一作用域覆盖、
精确目标关系、standing receipt 的 Agent 作用域与一致性诊断。Quota selection 共用
显式 gate 接收者规则：`global_gate` / `blocks_agent` 优先于 claim 归属。精确链接指向
别的 Todo 时，不能用宽 scope 静默满足当前依赖；输出修复诊断，不产生批准或自动改绑。
Python 保留 legacy 解码和修复展示，删除第二套规则。Agent fallback、global Todo、
summary 对候选关系批量调用，避免每对 Todo 一次 RPC；legacy completion 也复用覆盖规则。
验证覆盖复杂容量 fixture、展示上限之外的完整 provider 来源、陈旧／缺失展示和隔离真实
状态快照 parity。Scoped fallback 的资格、优先级、去重和门禁关系现已收拢到同一 TS
owner，删除 Python action-token 门禁匹配和选择循环。显式依赖及 global gate 优先；
旧 action_kind 相同仅保留阻塞兼容，不再以词语重合推断依赖。键不同或缺少依赖事实时，
不能证明候选是安全 fallback。这有意移除词语推断和无证据的安全绕行，详见
[fallback 协议](../../reference/protocols/decision-scope-v0.md#scoped-fallback-selection)。
Python 保留 lane 来源适配和展示压缩，不增加 provider 读取或 resume 重算。
T3 仍需处理从压缩 summary 重建诊断的
消费者，不把它们列为已迁移；不宣称 T1/T2、全部 T3 或持久化／promotion 完成。

能力缺口与修复路由现由 `agents/capability_gate.ts` 统一解释执行前提、修复产出、
owner/Agent 责任和受阻 Todo 绑定。Quota planning v1 传归一化的要求，而不是 Python
算好的 missing；Monitor 分流在 TS 进程内复用同一规则。公共 gate 一次批处理，精确目标
恢复调用保留有界、只缓存归一化值的桥接，不保留第二套判断。Python 继续负责 legacy
codec、候选来源／资格和共享 profile/rank 适配。明确修正：共享缺口绑定最高优先级受阻
Todo，同一 Todo 的不同展示不重复计算，权威空 backlog 不再复活陈旧 first-item。
target capability 是修复产出，不是安装或授权。没有新 provider／inventory／enablement／
promotion；压缩候选来源的上限和其余 T3 consumer 仍需分别闭合。

运行时能力重入现在复用同一个 TS owner：验证目标选择、owner 权限排除、推荐与已绑定
Turn 的区分，以及无持久授权的恢复合同由 `agents/capability_gate.ts` 负责。Python 删除
旧目标查找与过滤规则，仅适配 host/scheduler 参数、调用一次 typed reducer 并渲染 shell
argv；同一 interaction packet 复用结果，重入投影本身在健康路径不增加 runtime 调用。修正行为是：显式
选择前，可执行的低优先级推荐不能隐藏受阻任务的真实能力验证；验证成功在原 Turn 重入，
失败后仍可显式选择其他工作。已提交 receipt 的 Todo 不变。

`agents/capability_memory.ts` 现在持有本机 Agent 运行时能力声明、校验与幂等合并，
复用现有文件锁及 durable JSON writer；`capability_gate.ts` 统一 Goal、Agent、本次
调用的继承规则及不可用覆盖，删除 Python 的重复 union 规则。Live quota 与实际执行
的 Turn 通过已准入的 turn-start capability hook 自动记忆五种显式类型化的运行时能力；
quota core 保持只读，hook 失败独立隔离，规划也仍只读。Quota preparation 即使
使用缓存 status 也读取当前 Agent 记录，选择与结算重算共用这条路径。每个决策增加一次 availability reducer 调用，有注册
身份的决策另增加一次记录读取；显式 live 声明增加一次
observation 调用。Python 只适配 registry、宿主、hook 组合和 CLI，不增加第二套状态 reducer。

记录按本机 runtime、registry、Goal、注册 Agent 隔离，不升级为 Goal 公共声明，
不授予凭证、生产访问或可选功能启用，不进入 shared-authority head／grant／lease。
通过 `agent-capabilities` 查看、更正、清除；生成的 `/loopx` skill 指导记录失败及恢复。
现有前端 capability editor 管理可选功能配置，保持原配置 owner，不把本机工具观察
混入功能开关。详见[操作语义](../../quota-allocation.md)。


Advancement-frontier checkpoint 闭合：`todos/frontier_revision.ts` 统一 Agent
选择、完整度、实质内容哈希、长链阈值、checkpoint 构造与 ACK/rearm 分类。
Observation、语义写回和 runnable-successor 回执现在共用一个 typed checkpoint
构造器。后继路径在一次请求内解析完整来源、owned identity 和替换后的 checkpoint
列表；Python 不再自行拼装回执，也不为每个身份字段重复读取同一 frontier。
Python 保留 v0 字段清单、legacy JSON/metadata codec、后继资格与既有 obligation-id
推导。TS 统一时间顺序，并仅对唯一新鲜后继插入从完整当前来源重建前置 revision；
Python 验证前置 obligation id。压缩保留实质字段 `done`，历史保留后继来源关系。
多后继歧义、过期、来源截断或无关实质变化都不能关闭当前 obligation。
这闭合一个 T3 规则组，不代表其余 consumer 或 T1/T2/D1–D3 完成。

长链口径修正（#4667、#5001）：Agent lane 仅在已认领的开放 advancement 达到 15 项时触发。
持续监控和共享候选均不计入该阈值；共享候选仍可选。新义务不再使用原 20 项已认领
open Todo 的触发分支，历史 checkpoint 的读取与前置义务恢复保持兼容；无 Agent 的
Goal 总览保留原可选池口径。
完整实质 revision 包含终态 advancement；仅更新时间不重新触发。完整的 Agent-owned
identity 还能在同伴改变共享 unclaimed 工作时保持既有 long-chain ACK 有效。
自己的实质工作变化仍重新触发；没有认领工作的 lane 不产生长链义务。
历史 revision-only ACK 仍按精确 revision 匹配。明确修正：语义写回不再丢失
owned identity；只有 identity 而没有 revision、或明确不完整的 checkpoint 不能
压制 replan；其他 trigger kind 不能借用长链身份匹配。同一 TS owner 现在提供
基于 owned 实质内容的 `obligation_identity_revision`，供既有 Python 身份 codec
及 predecessor 校验使用；同伴修改共享池不能在 ACK 前让本 Turn 的义务换 ID。
监控到期选择、无变化重规划规则和写权限不变。`replan_semantics.ts` 为长链 review 接受并投影带证据的
vision path，保留既有 progress 出口和严格 vision 义务。真实 CLI 回归沿投影绑定
验证持久 ACK、checkpoint、一次 spend 和下一 Turn 回读；维护不触发，自己任务的
实质修改重新触发。本次推进总路线 S2/S3 已有 T3 owner，不新增 provider、迁移存储
或前端设置，也不宣称整个 RFC 验收完成。

Canonical index 仍在展示截断前生成。Exclusion、重复 ID/index lane、不完整时间与
权威 index 不完整时均保持 fail-closed。真实 CLI 验证两条 ACK 路径经过运行记录及
历史回读后，同伴 claim 不重新触发、自己的实质修改重新触发；复杂 fixture 还通过
真实 File provider，在展示陈旧／缺失时覆盖 revision-only 与 owned ACK。
前端／Lark 配置未改变：这是共享 quota/recovery checkpoint 路径，没有新增控制项
或用户确认，也不代表 provider promotion。

来源 facts 超过 512 KiB 时使用无损 deflate/base64 传输，保留精确 v0 内容和共享
2 MiB 请求边界。TS 拒绝畸形载荷及解压超过 64 MiB 的输入，不截断 Todo，也不
静默退回 Python 决策。真实 completed-history HTTP 和完整 checkpoint 尾项变更
回归保护传输容量语义。

列表过滤现改用 `compact_evaluated_todo_group`，不再用仅活动项重算 resume。
初始解析／canonical 读取仍通过 TS owner 在完整来源上求值；过滤要求匹配的已求值
条件，不能把归档中的已完成依赖变成丢失。共享合成 fixture 增补“有 scope 无 outcome”
和精确关联批准，另用包含数千归档项的 CLI 回归覆盖长历史。

Bootstrap 与后续 writer outbox 现捕获被引用的归档 resume 目标及其传递依赖。
`archive_capture.ts` 选择实际记录，拒绝重复 identity 和矛盾 role/class，不把已保存的
readiness 当作证据。Legacy 归档移动保留源 role，不重序列化原 receipt。旧记录缺少
role 时，仅显式 agent-only task class 可还原 agent；用户决策权限始终要求已记录的
user role。历史节点不会进入活动工作或 lease lane。无法识别的被引用历史仍须明确修复，
不得恢复 promotion 后的 Markdown fallback。本批闭合已复现的依赖遗漏，不代表所有
历史导入、provider 资格化、soak 或 D3 cutover 条件均完成。

- 分别审计 Turn/quota、Dashboard、standing decision、shared-goal alignment、
  amendment revision 输入。复用 #4117 canonical source adapter，一次决策传递一份
  snapshot，不新增 Todo inventory。
- 每迁完 caller，就在该 PR 删除其 promotion 后的 Markdown/event fallback。验证缺失／陈旧／
  非法 display、canonical 空集合、provider 不可用、terminal/archive 排序、
  claim scope 和超过 UI limit 的数据。来源为空不能复活 legacy 数据或视为任务完成。
- 区分历史监督、canonical 义务与 settlement 权威；unknown 不能结清 Todo/replan。
  有意语义修正单独披露，不标成全量 parity。

保留 journal 的读取边界现由同一个 TS owner 负责扫描参数、checkpoint 范围、
分页连续性、lookahead 和末行/head 一致性。File 与 NoKV 同时共用历史校验及
append 构造，版本哈希、物理锁/CAS 和后端头字段仍归各 provider。这删除了重复
存储协议知识，没有新增 RPC、Python bridge、capability 或 provider；既有
coordination 内部 owner 足够，File 内置及 NoKV/SQLite/PostgreSQL 可选部署边界不变。

明确修正：空存储上的正数 checkpoint 返回 `scan_cursor_out_of_range`，非字符串
游标返回 `invalid_scan_request`；历史缺行、乱序或末行/head 矛盾不能返回成功分页。
PostgreSQL 读取使用同一个 repeatable-read snapshot，并发提交在下一次调用可见，
不会将较新的行混入较旧 head。扫描只证明请求区间，不审计 checkpoint 之前的全部
历史。合法结果 schema、File/NoKV 持久字节、请求身份及版本算法保持兼容。这支持
T3/D1 reader，未完成全部 Todo writer、retention/compaction 或 promotion。

配额准入与结算消费者现在从统一 Todo reader 读取完整来源，在显示压缩前解析显式 Todo 选择。它删除直接追加 Markdown 候选的路径，保留 promote 前的事件适配；promote 后权威为空或不可读都不能复活展示行。结算进度由现有 TS 回执链归约，Python 负责完整身份命令及 JSON/Markdown 展示。现有幂等 writer 可补齐缺失的 spend 回执而不再次扣款。这关闭已复现的 T3 消费者缺口，不代表 D1–D3、provider promotion 或剩余 Python 事务适配已完成。操作语义见[结算进度契约](../../quota-allocation.md#receipt-backed-settlement-progress)。

**长历史传输边界。** Replan 历史仍由一个 TS owner 决策。小请求保留 inline
codec；较大的完整事实快照通过私有临时文件和摘要绑定的引用传递。同一 reducer
校验全部记录、agent 作用域内的 ACK 及重试身份；RPC 预算和展示窗口都不允许截断
历史。快照缺失、改变或无效时在决策前拒绝，适配器在返回或失败后清理临时文件。
这修复 T3/S2 的结算阻塞并补充 S7/R7 的传输增长证据，不代表全历史解析已经恒定
内存，也不证明分布式执行。游标/checkpoint 归约保留为以测量驱动、完整源语义一致
为前提的后续工作，不再造 Python 规则。见[历史决策证据](ledger/typescript-control-plane-migration-v0/2026-09-22-replan-history-policy.zh-CN.md)。

**恢复边界（2026-09-22）。** [authority archive 命令](../../reference/authority-archive.md)
由现有 TS coordination owner 负责历史校验、状态 delta 重建和可重入恢复；Python
只解析 CLI 路径、传递请求并展示紧凑结果。复用 state-log codec，避免各 provider
分别实现导出格式或在 Python 再写一份状态规则。交付的是 D3/L8 的隔离恢复副本；
正式接管 authority、执行器围栏和旧 Python writer 退役仍有独立验收条件。

**T4 — durable cutover 后兑现完整 writer 删除。**

- 2026-09-19 命令审计退役两条已经 typed、但没有实际消费者的执行面：
  `coordination.local_authority.todo_compatibility_edit` 与
  `coordination.local_authority.mutate`。claim、lease、update、archive、monitor
  和 team-plan 事务仍复用 projection reduction 与 commit preparation，因此这些
  公共内核保留。同一审计直接退役无实际调用的公开批量命令
  `todo capture-followups`，不再为它继续迁移；普通 `todo add` 仍可用，但不宣称保留
  已退役命令的批量原子性、去重或 replay 合同。独立 prompt 命令 `todo suggest`
  也直接退役；候选分析由当前 Agent 结合既有 Todo 读取和写入路径完成，不增加改名命令
  或包装协议。见[发现与兼容边界](../../reference/protocols/long-horizon-agent-state-protocol-v0.md#candidate-discovery-and-command-retirement)。
- 前提是 T1–T3 和 shared RFC 的 [D1–D3](shared-goal-authority-state-provider-v0.zh-CN.md#持久化执行卡)，包括 owner 批准及明确的 legacy 迁移窗口。
  搜索剩余 import 和公开路由后，删除旧 Markdown 业务 writer、capture-only adapter、
  重复 reference aggregate。
- 保留永久 Markdown renderer、已验证 import/export 和外部 effect adapter。
  每条保留 bridge 标明真实 caller 与退出条件；不等待全 TS CLI、daemon 或远端服务。

**每张执行卡的验证与停止规则**

涉及对应语义时，复用 `tests/fixtures/control_plane/coordination_production_scale_v0.json`、
`tests/control_plane/canonical_authority_fixture.py` 和既有 provider conformance；
先核验当前 schema，不能为过测试缩减复杂 fixture。运行
`npm run typecheck:control-plane`、`npm run test:control-plane`、受影响公开 CLI
测试和按风险选择的 canary。共享事务改动须覆盖受影响 File/NoKV 及真实隔离 PostgreSQL；
本地 store 声明须验证实际 backend，内存替身不能替代。

移动代码前独立定义合法／非法行为；移动后分别报告 baseline/head parity、有意差异、
product/bridge LOC 和 crossings，不混入 test/generated LOC。发现未知 writer、
真实环境缺失、未解释差异、私有数据依赖或必要门禁失败时停止，不降低 authority、
证据、fixture 或 payload 预算。允许只读快照和一次性 synthetic Goal；活跃 Goal
promotion、启动模型／任务、soak automation、发布或合并仍需各自明确授权。

stack 中的 schema identifier 清理是独立维护，不是上述路线的前置条件。只吸收所选
完整事务确实依赖的下游改动；base 合并后，其余工作再 rebase。

### 管家 collaboration 衔接检查点（2026-09-13）

在 `7eb4b7bb1661bd5eff63a8725a33169792d5964b`，#4152 是已合并的
lease-fenced text/note update 切片；#4121 SQLite 候选也已合并，但未晋级 provider。
实际 head 更新早期执行卡暗示的代码待合并状态，不解除其资格保留条件。

[管家/handoff RFC](capable-manager-semantic-handoff-v0.zh-CN.md) 遵循本文完整
事务收益规则：拟议 collaboration owner 替换一个完整请求事务与旧语义 caller，
不按字段增加 leaf RPC、不新增 TS daemon、不另造 Todo/Vision/lease authority。
已有 `coordination/todo_continuation.ts` 仅支持 promoted-local、同机、已注册
Agent、无 lease Todo，不是通用 pre-Todo/cross-Goal handoff；集成时保留其真实
兼容语义。M2 提供迁移收益回执及跨提交恢复证据；M1 普通主机工具无需等待全部 TS
或 provider 迁移。共享 Goal amendment 保留独立 proposal/commit 边界；受影响的
存储或完整 writer 退役，继续遵守 shared-authority D1–D3/T4 条件。此说明不交付
新的 runtime 行为。

## 0. 用一个例子说明决策

迁移期间，Python `loopx` CLI 向 LoopX 托管的 TypeScript runtime 发送一笔
粗粒度 typed transaction。例如，Turn settlement 先由 TypeScript 验证 journal，
并授权仍由 Python 承载的 provider；Python checkpoint 这些外部结果后，再由
TypeScript 完成最终 reduction 并返回 typed result。没有待执行 provider 的 replay
只需一次 reduction。Python 只把结果投影为旧 CLI shape，不再串行调用一组
TypeScript leaf helper，也不保留平行的 enum 和 reducer。

同一 PR 必须删除被它替代的 Python 语义路径。仅新增 TypeScript module 不等于取得
迁移进展；真正的兑现是语义 owner 更少、跨 runtime round trip 更少，并且 facade
有可信的删除条件。

CLI 自身迁到 TypeScript 后，CLI-only 使用方式会在进程内直接 import 同一份
kernel，Python 到 TypeScript 的桥随之删除。当 App、CLI、scheduler 或多个
host 需要一个共享 writer 时，同一 kernel 可以运行在一个可选的 managed
daemon 内。这是一份 kernel 的两种部署形态，不是每个控制面状态族一个
server。

## 1. 问题

LoopX 已有 TypeScript host 与 dashboard 表面。Effect Program、Turn-journal effect、
若干 Todo/quota decision 和 scheduler state 已有 TypeScript owner，但大量 CLI
composition 与兼容表面仍在 Python。一次性重写风险过高；然而继续逐个翻译 leaf
helper 会留下 chatty bridge 和重复 DTO 知识：代码位置变了，产品并没有简化。

因此，中间迁移节点必须同时满足：

- 每条已迁规则只有一个语义 owner；
- 用户看不到 CLI 分叉，也无需手动管理 daemon；
- 可以迁移真实副作用，而不只迁纯投影；
- 基于 pinned 迁移前基线和独立定义的不变量验证正确性；
- 每次 cutover 都测量 latency、packaging、upgrade、rollback 与 crash recovery；
- 每个 PR 都是完整、可评审的 replacement slice；
- 迁移经济性必须改善：旧语义代码和临时 scaffolding 的退出速度要快于 bridge
  代码的累积速度。

## 2. 架构决策

### 2.1 一份 TypeScript kernel

`@loopx/control-plane` 是目标语义 kernel。Domain module 拥有 typed state、
解释、transition rule 和属于这些规则的内部 effect。Transport shell 不能成为
第二个业务 owner。

```text
迁移期 Python CLI ─────────┐
LoopX App / scheduler ─────┼─> 一个 typed runtime boundary ─> TS kernel
未来 TS CLI ───────────────┘
```

边界传递“结算这个 Turn”“提交这个 journal”这类粗粒度、版本化请求，而不是
频繁的属性 getter。Runtime 只有一个静态 typed handler registry；新增 domain
handler 不会新增 server。

### 2.2 两种部署形态，一份实现

| 产品拓扑 | 执行形态 |
| --- | --- |
| TS CLI cutover 后的 CLI-only | CLI 进程内 import 并执行 TS kernel；没有 daemon |
| 仅 App | App runtime 内嵌同一 kernel |
| App + CLI + scheduler，或多个并发 client | 一个 managed local authority daemon；client 连接当前 writer |
| Python 仍是 CLI 的迁移期 | 一个 idle-exiting loopback runtime 把 Python 桥接到已迁 TS kernel |

如果 authority daemon 已拥有某个 registry/workspace，CLI 必须连接它，而不能
绕过它再打开第二个直接 writer。Runtime discovery 与启动全自动；用户无需配置
端口或守护进程。

### 2.3 TypeScript 拥有已迁 effect

目标不是“TypeScript 决策、Python 永远执行”。TypeScript 可以拥有 atomic
state checkpoint、event append、receipt commit、幂等 reducer write 等 LoopX
内部 effect。每个 effect 都有 typed request、稳定 idempotency identity、typed
receipt 与 retry policy。

异步执行不会削弱 settlement ordering：只有被 `await` 的 durability boundary
成功后，才能发出 effect receipt。但异步允许请求并发，因此拥有已迁写入 authority
的一方也必须拥有按 key 串行化或 compare-and-swap 合同。Caller-side lock 只能作为
明确的迁移期 guard；native TypeScript caller 在 cutover 后不得绕过这个 invariant。
Retry identity 必须绑定具体 operation：当一个 Turn effect 连续 checkpoint 多个
journal 状态时，仅凭宽粒度 Turn effect id 不能证明两次写入 payload 是同一 operation。

外部 authority 仍是显式 adapter：model call、human gate、host scheduler、
credential 和第三方 mutation 不会藏到一个万能 executor 后面。它们的 receipt
回到 Effect Program 完成 settlement。

### 2.4 替换，而不是生产双跑

Characterization 可以离线让新旧实现运行同一份 pinned corpus。生产环境不保留
两个 rule engine，也不 dual-write semantic state。一个 slice 通过门禁后，caller
翻到 TypeScript，并删除被替代的 Python 规则。只有真实 public import、持久化
schema 或未迁 callback 需要时，才保留窄 compatibility facade。

### 2.5 在每个信任边界只验证一次

TypeScript 类型在运行时会被擦除。因此 network/RPC payload、解析后的 JSON、
持久化状态、extension 输入与 adapter response 都必须以 `unknown` 进入系统；
静态类型标注或 `as T` 断言不能证明这些字节满足合同。每个已迁 domain 都必须先
通过 typed decoder 或显式的版本化 schema parser 解码，再交给 domain handler
或 Effect interpreter 消费。

解码成功后，TypeScript kernel 拥有这个 typed value，domain 内部可以依赖编译器，
而不必在每层重复临时字段检查。Framing、authentication、size limit 等 transport
检查与 schema validation、semantic invariant 分层负责。未经检查的
`JSON.parse(...) as T` 不能建立控制面 authority。

`as unknown as T` 只允许作为具名迁移缝：cutover PR 必须明确其调用点、上游
validator、负向边界覆盖和移除 owner。只要 public、持久化、RPC 或 extension
输入仍通过未经验证的断言进入已迁 domain 的 semantic core，该 domain 就不能
通过 promotion gate。TypeScript 补充运行时验证，而不是替代它。

## 3. 当前基线与阶段转换

Effect Program 先迁，是因为它连接 ordered step、identity、short-circuit failure、
replay、receipt 与 settlement。这个架构选择已经落地，不再是假设。

### 3.1 已交付基线

| 切片 | 已交付的 TypeScript 权威能力 | 剩余迁移债务 |
| --- | --- | --- |
| Effect runtime 与 Turn journal（[#3416](https://github.com/huangruiteng/loopx/pull/3416)） | Effect algebra、settlement rule、runtime lifecycle、typed Turn-journal interpretation 与 durable checkpoint effect | Python settlement facade 仍暴露细粒度调用，并重复 DTO/enum shape |
| Todo、quota 与 scheduler 证明切片（[#3431](https://github.com/huangruiteng/loopx/pull/3431)–[#3434](https://github.com/huangruiteng/loopx/pull/3434)） | Completion fence/state、workspace causality 与 scheduler transition 各有一个 TS rule owner | 切口大多仍是 leaf-shaped；Python 继续组合多个产品 transaction |
| Scheduler durable state（[#3440](https://github.com/huangruiteng/loopx/pull/3440)） | State normalization、persistence、replay 与一笔粗粒度 transition 由 TS 拥有 | Python compatibility path 仍承担跨 runtime transport 税 |
| Scheduler heartbeat/state transaction | TypeScript 拥有 receipt freshness、ACK 与 host-failure validation、state construction、failure-cache transition、replay/CAS fencing、atomic write，以及 public JSON/Markdown projection | 生成的 receipt-bound host follow-up 直接进入 native TS CLI；Python 只处理 unbound/manual compatibility call 与 external host mutation |
| Quota spend commit transaction | TypeScript 拥有最终 spend transition 校验、typed event 构造、effect replay/CAS fencing、crash repair，以及 JSON/Markdown/index write set | Python 仍投影 `should-run` 与 settlement readback facts，并在 CLI/index writer 进程内迁移前持有 legacy cross-writer index lock |
| Quota void commit transaction | TypeScript 拥有 spend-target resolution、before/after reduction、canonical correction 构造、effect replay/index CAS、prepared-receipt repair，以及 JSON/Markdown/index write set | Python 保留 `should-run` facts、clock/effect identity、legacy cross-writer index lock、一次 transport 与 compatibility entrypoint |
| Quota monitor-poll commit transaction | TypeScript 拥有 monitor admission 复核、target/event/result 构造、effect replay/index CAS、provider intent，以及可修复的 JSON/Markdown/index persistence | Python 投影 compact `should-run` facts，在最多两次 reduction 之间调用真实 Todo provider，刷新 legacy status，并持有 cross-writer index lock |
| Runtime decoder（[#3443](https://github.com/huangruiteng/loopx/pull/3443)） | 稳定 primitive decoding 进入一个很小的共享模块；domain decoder 仍留在本地 | 没有理由建设更大的 schema framework |
| Transaction 兑现（[#3464](https://github.com/huangruiteng/loopx/pull/3464)、[#3481](https://github.com/huangruiteng/loopx/pull/3481) 与 Todo completion） | Turn settlement、quota delivery routing 与 Todo completion 均只跨一个粗粒度 TS boundary；Todo transaction 拥有 identity、replay fence、validation planning/result reduction、continuation/recovery 与 completion metadata | Python 仍执行显式 external provider，并物化 legacy Markdown/event result；其他 domain 仍需各自的 bounded cutover |

Scheduler facade exit 已交付第一段有边界的 Stage 3 路径。带版本的
`heartbeat_followup_cli.ts` 从生成的 ACK/failure hint 接收有大小上限的 compact host
facts，校验原始 heartbeat receipt，并在一个 Node 进程内完成 state validation、
replay/CAS fencing、锁内写入，以及 public JSON/Markdown projection。Unix、Windows
和 wheel 安装后的 console launcher 只为精确匹配的 receipt-bound command 选择这条
路径。持续运行的 host path 因此不再启动 Python，也没有 Python 到 Node 的
request/response。旧 Python ACK rule 与只服务 adapter 的测试已经删除，不再形成第二个
semantic owner。无决策权的 Python compatibility adapter 暂时服务显式 in-process call
与手工构造的 unbound call，等这些 caller 改用生成的 receipt-bound hint 后即可删除。
Host automation adapter 及其 TOML/SQLite 写入仍有意留在 Python，并处在这笔
transaction 之外。

这些切片已经证明 correctness、packaging、Windows lifecycle、crash recovery、真实
TS-owned write 和可接受的 warm primitive-call latency。它们也暴露了迁移边界：
逐 leaf 翻译会先增加 TypeScript、facade、parity fixture 与 bridge traffic，尚未删除
足够多的 Python composition。

### 3.2 兑现阶段决策

迁移因此进入 **transaction-payoff 阶段**。后续 leaf migration 默认拒绝；只有它
能在同一 PR 或明确的紧邻 bounded follow-up 中直接解锁完整 transaction cutover
与删除时才例外。进展单位改为 operator 可感知的 transaction，而不是 helper、
enum、dataclass 或源文件。

一笔 transaction cutover 必须：

1. 把 validation、state transition、已迁 internal effect 和 result construction
   放到一个 domain-owned TS request/response boundary 后；
2. 删除被替代的 Python rule composition、细粒度 API、重复 enum/dataclass 和
   implementation-specific test；
3. 让 Python 只保留 transport、legacy response projection，以及仍属于外部
   authority 的显式 adapter；
4. 不允许 leaf-level bridge chatter。Effect provider 已迁入 TypeScript，或没有待执行
   provider 的 replay，只使用一次 request/response。真实 provider 仍在 Python 时，
   最多使用两次：一次 fail-closed preflight 授权具名 effect，一次基于已 checkpoint
   outcome 的最终 reduction。Model call、human gate 或第三方 mutation 会开启一笔
   新的、带 receipt 的 transaction，而不是隐式 callback tunnel；
5. 写明 Python facade 与 bridge operation 的精确删除条件。

Domain invariant 仍归各自 bounded owner。“更粗粒度”不等于建立一个万能控制面
command 或 mega-reducer。

## 4. 迁移顺序

### Stage 0 — 固定行为与 authority（已完成；每笔 transaction 重复执行）

每个选中的 transaction 都要记录权威 schema、经独立 review 的合法/非法
transition、生产 caller 与 side effect、matched latency/install baseline，以及
rollback/state-compatibility boundary。Characterization fixture 是临时迁移证据，
不是永久 specification。

### Stage 1 — Effect Program 与 managed runtime 基础（已交付）

TypeScript Effect algebra、settlement 语义、Turn-journal interpretation、durable
checkpoint effect、runtime lifecycle、packaging、upgrade fingerprint 与 boundary
decoder 基础都已进入 `main`。Stage 1 的 settlement facade 清理已完成：Python
细粒度 settlement reader 已移除，coarse readback/projection 留作有界的 Stage 2B 工作。

### Stage 2A — Bounded rule-owner 证明（已交付；不再复制该模式）

Todo completion、quota workspace causality、scheduler transition 与 scheduler
durable state 已证明 Python caller 可以安全切换到唯一 TS semantic owner。它们的
characterization 与 facade layer 是合适的迁移证据，但继续在更多 domain 平铺相同
leaf pattern 会增加总复杂度。

### Stage 2B — 完整 transaction cutover（进行中）

按删除杠杆与 runtime traffic 选切口，而不是按翻译难度选。已经交付的 Turn
settlement、quota delivery routing、Todo completion、scheduler heartbeat、quota
spend commit、quota void commit 与 task-lease acquire cutover 建立了这一模式。后续候选必须明确剩余
transaction 及其删除杠杆；剩余 quota settlement readback 只有在能退出或显著收窄
facade，而不是再增加 leaf handler 时才适合迁移。

每完成一笔 transaction，就用 native TS semantic/invariant test 加一个持久的
end-to-end adapter contract，替换 migration-only characterization worker 与 Python
implementation fixture。只有旧 authority 仍可执行，或 versioned compatibility
window 仍需 differential proof 时才保留 characterization corpus；引入时必须记录
删除触发条件。

Prior-host-Turn recovery 保留为完整事务的后续迁移：receipt 选择、精确 Todo
lifecycle 读取、settlement 验证与 recovery/continuation 决策需要一起迁移，才能
退出 Python coordinator。本次读取边界修复复用 `todo list --todo-id` 获取生命周期
证据，避免展示截断让已关闭的 Turn 持续进入 recovery；关闭规则保持不变，不增加
leaf RPC，也不将其计为已完成的 Stage 2B cutover。后续迁移需要保留大量无关 Todo、
provider 失败、身份冲突及同 Turn 无扣额恢复的验证。

当前实现状态：Stage 1、bounded Stage 2A proof 与已交付的 Stage 2B cutover 已就位：

- Turn settlement/commit：TypeScript 拥有 preflight authorization、ordered-prefix
  与 replay validation、provider failure classification、receipt construction、
  terminal closeout joining 和 canonical result。真实 Python provider 使用两次
  coarse reduction；完成态 replay 使用一次。
- Quota delivery routing：TypeScript 拥有 continuity 与 fallback 的选择，以及
  selected Todo 的 settlement boundary。In-flight 路径从两次跨 runtime 调用降到
  一次；空 candidate 的 short circuit 仍为零次。
- Todo completion：TypeScript 在一笔 transaction 中拥有 completion identity、
  terminal replay fence、validation declaration/effect planning、validation receipt
  reduction、continuation/recovery、completion metadata、registered-agent admission、
  successor ownership/exclusion 与 existing-successor selection。Python 只投影 registry
  和 Todo source facts，不再决定 policy。没有声明 validation 的 Todo（包括 replay）使用
  一次 reduction；真实 caller-approved validation command 作为显式 Python provider，
  位于两次 reduction 之间。取得 mutation lock 后会同时比较 Todo 与 policy-source
  snapshot，确保一份 declaration 或 agent registry 的 receipt 不能授权已经变化的事实。
  Materialized 与 event-projected 写入消费同一 typed result。
- Scheduler heartbeat/state 由 TypeScript 拥有 receipt freshness、ACK 与
  host-failure validation、带 identity 的 progression、failure-cache
  retention/counting、replay 与 CAS fencing、preview reduction、锁内 atomic write，
  以及兼容旧合同的 JSON/Markdown result。生成的 receipt-bound ACK/failure hint 携带
  一份有版本且有大小上限的 facts packet，随后通过 native CLI 直接进入这笔
  transaction。持续运行的路径不再经过 Python。无决策权的 compatibility adapter
  只服务显式 in-process caller 与 unbound manual caller，等这些 caller 改用生成路径
  后即可退出。Host automation mutation 继续作为 Python 拥有的 external effect。
- Quota spend commit：TypeScript 重新校验 compact before/after transition，构造
  canonical public-safe spend event，以带锁 index CAS fence effect，并把 JSON、
  Markdown、index 与 transaction receipt 作为一笔可修复操作提交。同一 effect retry
  幂等，跨 effect 漂移冲突，prepared transaction 可修复 partial artifact set。
  receipt 绑定 append 前的 index digest 与字节偏移，因此 retry 只会修复属于本事务的
  截断 JSONL 尾行，其他损坏仍然 fail closed。
  Python 只保留 `should-run`/settlement fact projection、一次 coarse transport call 与
  legacy kernel index lock；它不再构造或写入 spend event。
- Quota void commit：TypeScript 在 mutation lock 内定位被引用的 spend，归约
  before/after accounting decision，构造 canonical correction，并通过闭合的
  spend/void accounting-artifact kernel 提交 JSON、Markdown、index row 与 prepared
  receipt。同一 effect 的 retry 会 replay 或修复同一 transaction；新的 CLI invocation
  仍是新的 effect，因此保留对同一 spend target 再追加 correction 的既有行为。
  Malformed index row 现在由静默跳过改为 fail closed。Void artifact 文件名加入
  effect digest，JSONL row 改用 compact JSON；public payload 语义保持稳定。共享 kernel
  同时加固既有 spend recovery 的持久化 receipt/path identity。Python 只保留
  `should-run` facts、UUID/clock、一次 coarse transport call 与 legacy cross-writer
  index lock。
- 本地 task-lease lifecycle：native TypeScript transaction 现在拥有 acquire、renew、
  transfer、release、terminal verification、holder verification 与 fence close。它们拥有
  boundary decode、handoff 与 owner/Todo eligibility、同 Todo 与重叠 write scope
  conflict、compare-and-swap、generation/idempotency rule、operation/fence receipt、
  per-goal mutation lock、atomic lease persistence 及 canonical result。Python 只投影带有
  前后 source digest 的 compact registry、active-state、event-log 与 rollout-log facts，
  然后执行一次 native transaction call。TypeScript 在 lease lock 内、decision 前和
  write 紧前重验 source。Closed fence replay 与 generation 绑定：non-required receipt
  仅在 lease record 仍不存在时可重放；已提交 release 必须仍匹配同一 retired
  generation；aborted close 只能在新锁下重验同一 active generation。
  Provider-neutral coordination executor 通过 typed Python adapter，对 acquire、renew、
  transfer、release 到达同一份纯 TypeScript decision；#3669 跟踪的 shared provider
  execution、CAS 与 authority receipt 仍不属于本次 cutover。
- Quota monitor-poll commit：TypeScript 复核 quiet、due、external 与
  exact-blocked-wait admission，构造 canonical monitor target/event，在 mutation
  前记录 Todo-provider intent，并拥有 effect replay、index CAS、artifact path
  fence 与 prepared/committed repair。无 Todo 的 poll 和所有已完成 replay 都只用
  一次 reduction。真实 Todo writeback 仍是显式幂等 Python provider，位于一次
  preflight 与一次 final reduction 之间。Provider retry 绑定到持久化 monitor
  effect identity，较旧 effect 不能覆盖更新 observation。
- Task-lease acquire：TypeScript 拥有 identity normalization、settlement plan
  projection、provider failure classification、ordered receipt construction 与
  canonical result。Python 在一次 preflight 与一次 final reduction 之间调用现有
  atomic provider；provider 继续拥有 per-goal lock、owner eligibility、conflict、
  compare-and-swap、idempotency 与 lease-file durability check。无效 identity 会在
  provider 前停止；provider 后发生 crash/retry 时则重入同 key 的幂等路径。

Quota-accounting cutover 删除了 Python spend/void event builder 与三文件 writer。
当 quota decision 与顶层 CLI 在进程内执行 TypeScript、全部 run-index writer 改用
native lock，并且 legacy Python void API compatibility window 结束时，它们的 bounded
facade 即可退出。在此之前，Python 只提供 compact projection facts、clock/effect
identity、result validation 与共享 legacy index lock。Todo cutover 删除了 Python
state-evaluation dataclass、local identity projection、
replay helper，以及这些 implementation leaf 的 public runtime handler。剩余 Python
Todo facade 只拥有 fact projection、transport、external command execution、source
compare-and-swap、legacy response projection 与实际 Markdown/event write；当 writer 与
CLI 进入 native TS transaction 后即可退出。剩余细粒度 Turn facade 则在 quota 与
host-adapter caller 进入各自 coarse transaction 后退出。Task-lease semantic facade、Python atomic
provider、settlement bridge operation 与 lifecycle rule engine 已经删除。Python 只保留
compact source projection、一次 process transport、携带 opaque fence token/receipt id
的 context-manager plumbing、legacy response projection，以及现有 Python caller 所需的
compatibility import。顶层 LoopX CLI、Todo writer 与 authority-source adapter 在进程内
调用 TypeScript transaction 后，这些 surface 即可退出。Python/TypeScript 共享锁协议
仍服务于 Python handoff-mode transition 与其他跨 runtime holder；当不再有 Python
writer 获取 per-goal lease lock 时即可删除。Vision checkpointing 属于不同的
refresh/writeback 生命周期阶段，因此继续作为独立 transaction。

Lifecycle receipt 可以在 transport response 丢失或 owner caller 退出后，恢复已经完成
的 mutation 或 held/closed fence。长生命周期 fence lock 记录 Python caller PID，而不是
managed Node server PID；stale reclaim 会先取得 token claim，并用抗路径替换的文件身份
核验后再退役 lock。这不构成“同一 Node 进程内 handler 超时后仍并行执行时”的
exactly-once 保证；原 handler 可能仍存活时，caller 不得启动第二笔独立 operation。

#### Quota void commit 迁移经济账

| 字段 | 回执 |
| --- | --- |
| Canonical owner | 迁移前由 Python `slot_accounting.py` 拥有 spend-target lookup、correction reduction、event/result 构造、artifact 分配及 JSON/Markdown/index persistence。迁移后由版本化 TypeScript `quota.void.commit` 拥有这些语义，并通过闭合的 spend/void accounting kernel 拥有 effect fence、index CAS、receipt、replay 与 repair。 |
| 删除的旧语义代码 | 删除 212 行 Python 产品代码，包括原 void lookup、transition、event/projection、path allocation 与 JSON/Markdown/index writer 路径。 |
| 新增的 bridge 代码 | 新增 263 行 Python diff LOC，其中 243 行是有界的 `void_commit.py` transport/compatibility facade，另有 `loopx/quota.py` 与 legacy `slot_accounting.py` surface 中 20 行 import、re-export、normalization 与 route wiring。 |
| 跨 runtime 调用 | 公开 execute 与 dry-run 路径从零次 crossing 变为一次 coarse request/response。Exact-effect replay 或 repair 也使用一次。不同 CLI invocation 仍是不同 effect；legacy preview 加 record 两步 compatibility surface 的每个 entrypoint 各调用一次。 |
| 产品代码净增减 | 产品代码新增 2,210 行、删除 898 行，净增 1,312 行。Test/example 另计新增 1,416 行、删除 3 行，净增 1,413 行；build configuration 为 +3，docs 不计入。生产共享 kernel 已同时服务 spend 与 void，并替换 `spend_commit.ts` 中 671 行逻辑，不是预留的 speculative framework。 |
| 迁移 scaffolding | 没有新增 migration-only worker、parity corpus 或临时 schema framework。保留 native boundary/invariant/replay/CAS/repair 测试作为已交付和持久化 contract；Python bridge 测试随 compatibility facade 一起退出。 |
| Facade 退出 | 当 quota decision 与顶层 CLI 在进程内执行 TypeScript、全部 run-index writer 使用 native lock，并且 legacy `build_*void*`/`record_*void*` Python API compatibility window 结束时，删除 Python void facade。 |
| 正确性与性能 | Typed-decoder 负例、legacy target compatibility、effect isolation、index CAS、malformed receipt/path、exact index-row identity、受支持的 duplicate-index repair、concurrent mutation、truncated-tail repair、公开 CLI 行为，以及干净 wheel/sdist semantic probe 均通过。16 次 cold start 的 p50/p95 为 230.88/260.92 ms；128 次 warm typed ping 为 1.07/1.29 ms，warm void preview 为 1.93/2.34 ms。64 次 durable facade transaction 中，commit 为 30.64/37.49 ms，exact-effect replay 为 8.05/9.86 ms。Daemon RSS 在 idle 时为 108.38 MiB，256 次请求后为 109.80 MiB。64 对交错 full-CLI 样本中，baseline/candidate p50/p95 为 736.51/828.68 与 779.52/856.49 ms，p95 增量为 27.81 ms（3.36%）。这个绝对增量来自新增的一次 managed-runtime fingerprint/request 与 prepared-receipt durability；百分比低于 5% 物质回退门槛，Stage 3 会删除这次 crossing。 |

#### Task-lease acquire 迁移经济账

| 字段 | 回执 |
| --- | --- |
| Canonical owner | 迁移前由 Python 拥有 atomic acquire provider，TypeScript 在外层做 settlement reduction。迁移后由 `task_lease_acquire.ts` 拥有完整带锁 transaction 与 canonical result。 |
| 删除的旧语义代码 | 973 行产品代码，包括 Python provider/acquire 组合与 conflict 路径、Python↔TS settlement bridge/reducer 及 handler，以及 legacy CLI settlement projection。 |
| 新增的 bridge 代码 | 约 641 行 gross、有界的 compatibility 产品代码，包括 compact Python authority projection 加一次 managed-runtime request、compatibility import、Python/TypeScript 共享锁协议，以及 typed NoKV/coordination decision adapter。顶层 CLI 进入 Node 后删除本地 projection 与 import；其余 lease writer 与 fence 迁移后删除 dual lock；coordination executor 进入 native runtime 后删除该 adapter。 |
| 跨 runtime 调用 | 公开 acquire 与 replay 路径从两次 request/response reduction 降为一次 native transaction request/response。 |
| 产品代码净增减 | 产品代码 +2,130/−1,122 行，净增 1,008 行。Test 与 fixture 单独计为 +898/−1,081，build configuration 为 +4。 |
| 迁移 scaffolding | 删除 task-lease settlement characterization、fault-matrix、incident-replay 及其 fixture 切片。以 native invariant、crash/retry、direct-CLI、adapter 与 cross-runtime lock 测试取代；不再保留 migration-only worker。 |
| Facade 退出 | 本次删除 semantic facade、atomic provider、settlement operation 与 legacy CLI projection。仅保留 source/transport compatibility 与 cross-runtime serialization，删除条件如上。 |
| 正确性与性能 | 公开 CLI 在 5 个 acquire/replay/failure 场景与旧实现精确匹配；20 个 focused native test、207 个 Node test、4,615 个 Python test（12 个 skip）、crash/retry 与 packaged-wheel smoke 通过。在匹配的 16 样本 full-CLI 测试中，happy-path p95 从 1,593.7 ms 变为 1,167.8 ms，replay p95 从 513.3 ms 变为 445.4 ms；中位数分别为 364.6→425.6 ms 与 343.3→351.9 ms。 |

#### Task-lease lifecycle 迁移经济账

| 字段 | 回执 |
| --- | --- |
| Canonical owner | 迁移前由 Python 围绕 native acquire transaction 拥有 renew、transfer、release、terminal/holder verification 与 fence close。迁移后由 `task_lease_lifecycle.ts` 拥有全部六个 operation、锁内持久化及 canonical receipt/result。 |
| 删除的旧语义代码 | 删除 Python lifecycle decision、CAS、lease write 与进程内 fence rule 路径；Python 只保留 authority/source projection、managed-runtime transport、context-manager adaptation 与 legacy public payload projection。 |
| 跨 runtime 调用 | 每个 lifecycle verb 使用一次 coarse native request/response。Held fence 有意跨 verify 和 close 两次调用，因为 caller 的 Todo mutation 位于两者之间，并持续由同一个 lock token 授权。 |
| 恢复契约 | Operation receipt 绑定 retry identity 与 expected generation。Fence receipt 区分 acquired、held、closed；返回幂等结果前会重验当前 authority 以及当前或 retired lease generation。 |
| 锁迁移债务 | PID liveness、token claim、stale reclaim 与抗替换文件身份使 Python/Node 共享锁可安全恢复。handoff-mode transition 与所有剩余 Python lease-lock holder 进程内迁移后，删除这层有界协议。 |
| 非目标 | 本次 cutover 共享 ordinary lifecycle decision，但不实现 #3669 的 shared-provider execution、CAS 或 authority receipt；也不承诺 client timeout 后原 Node handler 仍运行时，第二个请求具备 exactly-once execution。 |

#### Todo terminal lifecycle 迁移经济账

| 字段 | 回执 |
| --- | --- |
| Canonical owner | 迁移前，Python 持有 terminal admission、successor derivation 与 archive retention，completion reduction 和 lease operation 则跨越更窄的 TS 边界。迁移后，`todo_lifecycle_decision.ts`、`todo_successor_derivation.ts`、`todo_terminal_lifecycle.ts` 与 `todo_archive_selection.ts` 成为 terminal admission、successor 默认值／继承／绑定、lease release、completion reduction、CAS、receipt replay 与 archive selection 的 typed owner。Terminal transaction 直接 import successor 与 archive owner；legacy Markdown/event writer 只调用其严格 wire handler 并物化返回 proposal。 |
| 删除的旧语义代码 | 从 Python 语义 ownership 删除 284 行产品代码：74 行 terminal decision 与 archive eligibility/order/standing-receipt selection，加上 Markdown complete/supersede 和 event completion 三条路径中重复的 210 行 successor priority、capability/binding、exclusion、continuation 与 predecessor-link derivation。其余 Python complete/supersede body 是未 promotion 路径的 compatibility writer，不是第二个 terminal decision owner。其他删除属于 adapter reshaping 或搬移，不计为 payoff。 |
| 新增的 bridge 代码 | 有界 transport/compatibility 共 937 行 gross 产品代码：538 行 `provider_terminal_lifecycle.py`、135 行 successor intent/result adapter、173 行 local TS request decoder/router 增量、33 行 legacy archive result adapter、10 行 handler registration、6 行 projection settlement，以及 42 行 promotion 后将 Turn durable readback 指向 canonical authority 的路由。29 行 `resolve_todo_state_path` extraction 是搬移，不是收益。Host-local validation declaration 的存储与执行是保留的 external effect，不冒充已删除 bridge。 |
| Successor ownership | Public caller 持有请求的 successor text 与 option。Python 仅序列化 intent，并把 typed proposal 适配给 legacy writer。只有 TypeScript 推导继承 priority、默认 task class、capability binding、user binding、exclusion、same-agent continuity 与 `unblocks_todo_id`；promotion 后 lifecycle 在同一 provider transaction 内完成推导和校验，再原子提交 target、successor、lease 与 receipt。Legacy 与 event 路径通过一次 effect-runtime 调用复用同一纯 TS 决策。 |
| 跨 runtime 调用 | 从 public facade 实测：promotion 后无 validation 的 complete、supersede 与 archive 均为三次 request/response（`todo_list`、terminal/archive transaction、projection readback）。带 validation 的 complete 为四次（`todo_list`、terminal preflight、terminal finalization、projection readback），另执行一次声明式 host-local validation effect。注入 post-commit projection crash 后，首次尝试为两次调用，receipt replay 为三次。该 cutover 前不存在合法 promoted happy path；legacy terminal 调用沿用既有 TS admission decision，仅在存在 generated successor intent 时增加一次 coarse successor-derivation 调用。 |
| 产品代码净增减 | 最终 merge-base 分类为产品代码 +4,051/−364，净增 3,687；test/fixture/example 为 +3,594/−156，净增 3,438；generated contract 为 +3/−0，docs 不计入。该增长交付完整 provider-neutral transaction、单一 successor semantic owner、真实 File/PostgreSQL conformance、public facade parity 与持久 mutation gate，不记作 deletion payoff。 |
| 迁移 scaffolding | Production-scale fixture、三臂演练、provider conformance、public legacy/promoted parity matrix 与 mutation case 因表达持久迁移 contract 而保留。Compatibility facade 及其 call-count assertion 随 facade 退出；provider-neutral transaction 与 archive-order mutation coverage 保留。 |
| Facade 退出 | 最后一个 Markdown/event 业务 writer 迁移后，退役 legacy successor-derivation/archive-selection crossing 及其 call-count test。随 registry/lifecycle 输入与 journal consumer 收敛，逐段删除 terminal facade；native CLI 允许全部移除 transport，但不是删除重复 decision 的前提。只保留实际 caller 需要的输入、私有 validation 执行和投影交付 adapter，即使它们仍是 Python。`resolve_todo_state_path` 的具体路径 consumer 消失后再删除；永久 Markdown renderer 与已资格化 import/export 保留。 |
| 正确性证据 | 独立 archive order/standing receipt 语义可以 kill oldest-selection mutant；可选 `note`/`evidence`/`reason` 覆盖 promotion 前后的 `None`、empty、ordinary、Python Unicode 纯空白与空白压缩。Public-entry 测试证明 canonical commit 后 Turn-journal 崩溃重试只结算一次；logical retry 允许说明文本变化但拒绝不同 successor intent；validated create 在拒绝、并发与 canonical commit 后崩溃恢复时只发布 accepted digest sidecar；非法 actor 在 legacy/promoted 下都保持领域拒绝分类。独立 successor 测试钉住 priority、binding、exclusion、continuation 与 predecessor-link inheritance。Stage 2C 证明 provider-first fence routing、live-lease import、orphan-history filtering、management-lock exclusion、replay 与 zero-write preview。File 与真实 PostgreSQL provider 执行同一 terminal conformance，独立 legacy 臂仍是强制 compatibility evidence。 |

Monitor-poll cutover 删除了 Python admission-policy、monitor-target module，以及
Python event/replay/artifact writer。它的 bounded facade 会在 quota `should-run`、
Todo monitor persistence、status projection 与剩余 run-index writer 都进入原生
TypeScript 进程后退出；在此之前只承载 compact facts、具名 Todo provider、legacy
after-projection 与共享 Python index lock。

本次 cutover 以最终 merge-base 计算的 migration economics receipt 如下：

| 字段 | 证据 |
| --- | --- |
| Canonical owner | 变更前由 Python `monitor_poll.py`、`monitor_poll_policy.py` 与 `monitor_target.py` 拥有。变更后，版本化 TypeScript `quota.monitor_poll.commit` transaction 拥有 admission、target/event/result 构造、replay/CAS、provider intent 与 durable artifact；Python 只保留 compact fact projection、具名 Todo provider、transport 与 legacy after-projection。 |
| 删除的旧语义代码 | 删除 826 行 Python 产品代码，包括 `monitor_poll.py` 中被替换的 601 行、161 行 policy module 与 64 行 target module。 |
| 新增的 bridge 代码 | 有界 bridge 新增 495 行 Python diff LOC，其中 455 行位于 `_NativeMonitorPollRejected`、`_mapping`、`_monitor_candidate`、`_due_monitor_candidates`、`_vision_wait_state`、`_registry_due_monitor`、`_decision_packet`、`_observation_packet`、`_index_digest`、`_native_result`、`_request`、`build_quota_monitor_poll_event`、`find_quota_monitor_poll_turn`、`_status_with_monitor_poll`、`_reload_status_after_monitor_writeback`、`_monitor_poll_failure`、`_capability_declaration_retry` 与 `record_quota_monitor_poll_for_decision`，另有 40 行 import/schema wiring。34 行 `_provider_writeback` 是真实保留 provider 的 adapter，不计入 bridge。 |
| 跨 runtime 调用 | 变更前整条路径由 Python 拥有，因此为零。变更后，无 Todo 写入、exact replay 或 recovery 使用一次 request/response；真实 Todo provider 运行时使用一次 preflight 与一次 final reduction。 |
| 产品代码净增减 | 产品代码新增 2,743 行、删除 831 行，净增 1,912 行；test/example 另计新增 1,045 行、删除 242 行，净增 803 行，docs 不计入。该临时增长交付一笔完整 transaction，不能连续复制；当 quota decision、Todo persistence、status projection 与剩余 index writer 原生化后，下一项删除是 495 行 bridge。 |
| 迁移 scaffolding | 删除 218 行 implementation-specific policy smoke 与 18 行 target-helper assertion。没有提交临时 parity harness；保留 typed boundary、public CLI、replay/CAS、malformed input、provider 与 repair 测试，因为它们表达已交付或持久化 contract。 |
| Facade 退出 | Python facade 只剩 compact source facts、Todo provider、一个共享 cross-writer lock、transport 与 legacy result projection。当 `should-run`、Todo monitor persistence、status projection 与全部 run-index writer 在原生 TypeScript 进程执行时删除。 |
| 正确性与性能 | Identity/admission、effect isolation、provider fence、malformed receipt、concurrent CAS、crash repair、packaging 与 launcher coverage 均通过。Managed runtime 的 cold start p50/p95 为 274.35/450.44 ms，warm event 为 1.13/1.72 ms，durable commit 为 2.06/2.27 ms，idle/burst memory 均为 126.0 MiB。在把 prepared-plus-staged receipt 序列收敛为一份保守的 prepared WAL、继续以 index 作为 commit proof，并且只在 registry 可证明位于 Git worktree 之外时跳过 Git subprocess 后，最终 64 对交错 full-CLI 样本中，Todo write 的 baseline/candidate p50/p95 为 663.34/971.40 与 631.96/878.10 ms，candidate p95 增量为 -93.31 ms（-9.61%）；replay 为 598.23/910.75 与 580.13/900.69 ms，candidate p95 增量为 -10.06 ms（-1.10%）。两条路径的 p95 增量均同时落在完整 CLI 的 5% 与 25 ms 门槛内，因此此前的 owner-review hold 已解除。 |

### Stage 3 — CLI 与 App 汇合

交付 native TS CLI，并在进程内 import kernel。只保留一个自动选择的 authority
路径：CLI-only 时进程内直接执行；App/scheduler 已拥有 workspace 时连接 managed
daemon。所有生产 caller 不再需要 Python bridge 后，删除 bridge 与协议。

Receipt-bound scheduler ACK/failure 是本阶段第一段有边界的 native CLI 切片。它只做
精确 launcher dispatch，没有引入通用 Node router。`quota should-run`、host automation
mutation 与更广的 quota policy 继续由原 owner 负责。

### Stage 4 — 清理分发

通过 npm 与 LoopX release artifact 分发 kernel，删除 Python runtime 依赖，并
决定可选 daemon 使用普通 Node entry point 还是 LoopX 自建 single executable。
不要静默依赖非官方第三方 Node wheel。

## 5. 兑现阶段 PR 合同

后续每个迁移 PR 都要在描述与 validation comment 中附一份 **migration economics
receipt**：

| 字段 | 必需证据 |
| --- | --- |
| Canonical owner | Cutover 前后分别由谁拥有；不得存在模糊双 authority |
| 删除的旧语义代码 | 删除的 Python rule、细粒度 API、enum/dataclass 与 implementation-only adapter 的产品 LOC |
| 新增的 bridge 代码 | 仅为 Python↔TS transport 或 compatibility 新增的产品 LOC |
| 跨 runtime 调用 | Happy path 与 recovery path 在变更前后的 request/response 次数；effect 已由 TS 拥有或没有待执行 provider 时目标为一次，否则真实 Python provider 尚存期间最多一次 preflight 加一次最终 reduction |
| 产品代码净增减 | 产品 LOC 的新增减去删除；与 test、fixture、generated file 和 docs 分开报告 |
| 迁移 scaffolding | 新增、保留或删除的 characterization/parity helper，以及具体删除触发条件 |
| Facade 退出 | 本次已删除，或列出精确剩余 caller/compatibility contract 和删除条件 |
| 正确性与性能 | 与变更 transaction 相关的 invariant、负例、matched end-to-end baseline、packaging、crash/retry 与 host coverage |

LOC 以最终 merge-base diff 为准，并把 production code 与 test、fixture、generated
file、docs 分开分类。搬移代码按删除加新增计算；bridge LOC 必须列出那些唯一职责是
跨 runtime transport 或 compatibility 的函数。Round trip 要在一条具名 public
happy path 及其 retry/recovery path 上实测，不能由 handler 数量推断。

只搬动代码、只新增 handler，或扩大 bridge 却不删除 authority 的 PR 不能通过这一
阶段。一笔 cohesive transaction 可以暂时净增代码，但 receipt 必须说明 bridge
为何有界，以及下一次哪项删除会兑现收益；这个例外不能被串成无限 leaf migration。

稳定 primitive decoder 可以复用现有的小型 runtime decoder module。Domain decoder
仍留在各自 bounded context；本 RFC 不授权 generic schema framework。

## 6. 正确性与性能门禁

### 正确性

- 独立定义 algebra properties：identity、适用场景下的 associativity、ordering、
  short-circuit、replay 与 effect-id isolation。
- pinned characterization corpus 输出精确一致。
- malformed state、cross-effect overwrite、partial commit、cancellation、
  permission denial 与 budget rejection 的负例。
- 边界 decoder 必须在 domain dispatch 前拒绝缺失字段、错误类型、不支持的 schema
  版本，以及 oversized 或 malformed payload。Cutover inventory 必须列出仍存在的
  `as unknown as T` 迁移缝并证明其已受保护；promotion 要求移除已迁 domain
  authority 输入上的未经验证断言。
- 被 `await` 的写入只有在其声明的 durability point 成功后才能发出 receipt；同 key
  并发 mutation 必须串行化或使用经过测试的 CAS 合同，retry identity 必须区分同一
  Turn 内连续发生的 checkpoint。
- 进程 crash 与 retry 不得重复已经提交的内部 effect。
- wheel 与 sdist 安装到全新环境后，从打包文件执行 deep semantic probe。

#### Caller 可观测语义是 promotion 门禁

每笔 Python 到 TypeScript cutover 在实现前都要盘点所有生产 caller 分支的行为。盘点
包括：可接受输入与默认归一化；已传入、未传入、空值与显式清除参数；资格与重叠拒绝
的优先级；完整诊断与修复建议；从 dispatch 到持久化后独立 readback；authority、
ownership、receipt 与 no-effect 结果；以及 transaction 支持时的 replay 或并发更新。
只有相同 reason code，或 provider conformance 通过，不足以证明 parity。

Cutover PR 必须分别为不可变基线 revision 和精确审查 head 记录机器可重放的执行
receipt。除非声明且独立批准有意差异，两次运行必须使用同一有界脚本、合成 fixture
指纹、公开生产入口与真实受影响 backend。每个 receipt 都要写明 revision、命令、
backend、退出状态、归一化观测指纹，以及公开安全的证据指针或内联观测。归一化可以
消除临时路径、时间戳等已记录的非确定性，但不得消除诊断、字段存在性、优先级、
持久状态、identity、ownership 或 effect 差异。

同一 harness 还必须证明回归敏感性：它要在历史缺陷或一个故意注入的语义 mutation
上使独立定义的 invariant 失败，并在修复 head 上通过。Mutation 例如丢弃字段或诊断
细节，或引入更强前置条件。绕过生产入口的单测，或所有 provider 都已共享候选规则
的测试集，只能算辅助覆盖，不是 baseline/head 证明。如果无法安全运行真实 backend
或不可变基线，promotion 必须以 `not_yet_proven` 暂停；文字说明不能豁免该缺口。

这项验证是离线证据，不是第二份 authority。生产环境不同时运行 Python 与 TypeScript，
不从候选实现推导期望结果，cutover 后不保留 legacy rule。有意行为变更必须与 parity row
分开，根据公开 contract 说明理由并显式批准。Promotion 后只保留表达持久公开或
持久化语义的 fixture。

Characterization output 是证据，不是 specification。Pinned 行为若与独立 review 的
invariant 冲突，PR 必须披露，并把行为变更单独批准。旧 authority 删除后，promotion
还要求删除只服务这次实现对比的 characterization machinery；当 fixture 表达 public
或 persisted compatibility contract 时，可以保留为持久 regression test。

### 性能

Cold startup 与 steady-state 分开测量。每笔 transaction cutover 必须报告：

- managed runtime cold-start p50/p95；
- warm typed request p50/p95；
- representative complete transaction p50/p95 与跨 runtime round trip 次数；
- 相比 pinned Python baseline 的完整 CLI p50/p95；
- idle 后和 bounded request burst 下的 daemon 内存。

默认验收目标仍是 warm、non-durable internal transition p95 低于 2 ms，完整 CLI
不出现物质回退（p95 超过 5% 或出现无法解释的 25 ms 额外开销）。Durable
transaction 要和 matched durability baseline 比较，而不是套用 2 ms kernel budget。
不达标，或用更快的 microbenchmark 隐藏 tail regression，都是 owner review gate，
不能静默放宽。

## 7. 安装、升级与回滚

迁移不能要求用户管理服务。Python 过渡版本要求 Node.js 22.18.0 或更新版本，
但 installer 与 `loopx doctor` 必须在正常控制面工作前检测，并给出精确修复方式。
当前源码版本已将最低要求升至 Node.js 22.22.3，以覆盖内嵌 SQLite 的 WAL-reset 修复。
Wheel 与 sdist 携带 TS source 和版本化 schema。

Runtime 因 idle 退出时仍是健康状态：`stopped` 表示下一次控制面请求会自动拉起，
不表示用户需要手工执行 daemon 命令。CLI 与 App 消费同一个 lifecycle projection
（`running`、`stopped` 或 `unavailable`）和稳定 diagnostic code；raw stderr、token、
本地路径和私有 runtime metadata 不进入投影。

Runtime fingerprint 包含每个实际执行的 TS module 与 contract。升级会启动新
fingerprint 的 runtime；旧进程可完成 in-flight work，并在 idle 后退出。Request
携带稳定 effect identity；只有显式幂等的 handler 才允许 transport retry。

Rollback 恢复上一版本 artifact 与 fingerprint。在单独通过 state-schema cutover
前，不把持久化状态改写为 TS-only 格式。

## 8. 非目标与停止条件

- 不永久维护 Python/TS 语义双胞胎。
- 不为每个 domain 建 server，也不建 arbitrary-command 通用 executor。
- 不 big-bang 重写 CLI。
- 不以 dual-write production semantic state 作为迁移策略。
- 不只凭 microbenchmark 声称性能。
- 不因 bridge 已存在就继续平铺迁更多 leaf helper。
- 除非存在具名 public import、persisted wire contract 或未迁 caller，不保留重复的
  Python enum/dataclass。
- 不为已经不存在的实现永久保留 characterization harness。

如果 bridge 需要用户手动管理、已迁规则仍有 Python 语义 owner、handler boundary
变得 chatty、连续两个 PR 增加 bridge/scaffolding 却没有退出 facade，或一笔
transaction 只能靠削弱既有行为才能通过 invariant/recovery/performance 门禁，
就停止或 replan。

## 附录 A：执行记录

实测交付记录存于[逐条 ledger](ledger/typescript-control-plane-migration-v0/)。
每条记录说明已交付边界及剩余验收缺口；上方 T1–T4 检查点仍是当前迁移计划。

### Canonical 显示确认与刷新恢复

TS 拥有 canonical revision 比较、最新／固定版本意图及三次重试上限；Python 继续执行
文件锁、耐久落盘和 Markdown 渲染。已提交刷新与同 Turn 重试复用这一恢复路径，规划
快照同时供应缺失工作诊断和初始显示，删除 Python 的重试决策及晋升后从旧 Markdown
再判断 Todo 数量的路径。没有增加 RPC 方法或持久 ACK，正常恢复增加一次确认读取。
这是 T3／D1 的刷新调用方闭合；其他 consumer、D2 与整 Goal 切换仍需独立资格。
见[投影合同](../../reference/protocols/active-state-structured-projection-v0.md)。

### Reviewed coordination cutover ownership

Saved-plan execution and fenced recovery now share the TypeScript promotion
owner. Fresh-source qualification wraps durable lineage qualification; recovery
uses that same lineage rule after exact fence verification. The Python CLI loads
a reviewed JSON carrier and transports fresh observations, without recreating
plan hashes, recovery decisions or receipt proof. Both commit paths share one
receipt/first-transaction readback contract.

This is a migration orchestration checkpoint, not completion of Stage 3 or a
default-provider flip. Integrate claim-preserving migration separately, retain
real-backend and captured-source qualification, and retire Python only where its
actual callers have moved. [Operator contract](../../reference/reviewed-coordination-promotion.md).

### Todo 摘要决策收口

已选来源的计数、展示分配、最近完成时间顺序、编排候选位置与收尾证明，收口到一个
TS 摘要批次；Python 保留旧格式解码、公开字段筛选及渲染。删除旧 Python claim 分配
算法和汇总分支，用一个内部入口替换 lane／closure 两次调用，不保留无调用方的旧 wire。
公开 `todo_summary_v0` 和持久记录不变；完整来源的关系求值先于筛选，来源完整性不被
查询命中情况覆盖。见[语义及回滚](../../reference/todo-work-counts.md)。这是 T3/L5 的
共享读取边界推进，不替代 D2/D3 或 provider 默认切换。

2026-09-24：[完整源捕获的 TS 组装与剩余交付包](ledger/shared-goal-authority-state-provider-v0/2026-09-24-source-capture.zh-CN.md)统一源构造、身份拒绝和当前图成员规则；不关闭 L7/D2/D3 或启用默认 provider。

2026-09-24: [带租约接力与剩余本地默认交付包](ledger/shared-goal-authority-state-provider-v0/2026-09-24-leased-continuation.zh-CN.md).

事件重放与剩余切换清单见 [2026-09-25](ledger/shared-goal-authority-state-provider-v0/2026-09-25-event-replay.zh-CN.md).
