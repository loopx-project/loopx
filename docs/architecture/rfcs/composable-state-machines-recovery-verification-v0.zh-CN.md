# RFC：可组合状态机与恢复验证（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** 部分实现；已有有界 Turn 恢复验证，完整 M2 仍开放
- **作者 / 负责人：** 控制面领域维护者与测试维护者
- **创建：** 2026-10-01
- **最近规范修订：** 2026-10-08
- **实现基线：** `98acf52e7e41c193959bd45db622c65298273714`
- **相关契约：** [Effect Interpreter](agent-loop-effect-interpreter-v0.zh-CN.md)、[TS 迁移](typescript-control-plane-migration-v0.zh-CN.md)、[共享权威](shared-goal-authority-state-provider-v0.zh-CN.md)、[质量分层](../../development/testing-and-quality.md)、[总路线](loopx-overall-roadmap-v0.zh-CN.md)
- **语言镜像：** [English](composable-state-machines-recovery-verification-v0.md)

## 文档地图与维护契约

第 1–10 节定义设计与验收合同，第 11 节定义交付，第 12 节记录未决事项。
接受设计不等于实现合格、改变默认值或授权晋升。两种语言互为语义镜像，同步维护。切片交付后的
日期证据写入现有领域 RFC ledger，不创建第二份任务账本。

## 1. 决策摘要

沿已有领域 owner 验收一条完整恢复旅程。纯 TypeScript 决策、typed effect 与持久
receipt 是实现接缝；有界可执行模型提供独立预期。模型只用于测试，不成为第二个
生产 scheduler 或状态 writer。

从一个工作项的 ownership、执行、writeback 与 settlement 开始；有真实 caller 后，
再扩展 successor 调度和结果投递。复用 conformance fixture、quality catalog 与
PR 证据。本 RFC 不增加产品 capability、provider、wire schema、运行开关或审批流程。

## 2. 问题与动机

一个合法 Todo 转换可能留下未结算的 Turn；writeback 提交后丢失响应可能诱发重复
effect；gate 解除后 successor 可能一直 quiet。逐个 owner 的局部测试不能证明这些
边界之间的因果关系。

合法 Host result 也可能携带错误产物。失败证据须在更多工作依赖它之前到达所属
准入和恢复路径。进程恢复、journal 阶段推进或接管成功，都不能单独证明业务错误
已被修正。

### 不变量

- 每个领域转换只有一个 owner；projection 和 receipt 不能铸造新权限。
- Todo 完成、Turn 结算、Goal 完成和结果投递含义独立。In-flight writeback 可以
  结算 Turn 而不完成 Todo；Monitor closeout 可以不扣 quota。
- 重试保留逻辑 operation 与 payload 绑定；新 intent 不继承历史 receipt 的权限。
- 过期或已转移的 executor 不能提交新的受保护工作；历史回读与当前执行权限分开。
- 提交不确定性保持显式，直到所属 readback 能够解决。
- 投影失败不撤销已提交事实，也不授权重复执行其外部 effect。

## 3. 范围与非目标

范围包括领域组合合同、有界序列探索、故障注入、独立 oracle 和生产入口一致性
验证。它归属总路线 S2/S3/S10 与既有 R1 恢复工作。

不包含全局状态枚举、通用 workflow DSL、通用 effect monad、完整模型推理验证、
全局 event sourcing、新 authority store、活跃 Goal 迁移、自动启用 provider 或
无条件活性保证。

## 4. 当前系统契约

`effect_program.ts` 拥有 settlement identity、有序 reduction 与 receipt；
`turn_driver/settlement.ts` 拥有 Turn settlement interpretation。Coordination 拥有
Todo/lease 事务；quota、scheduler 与 delivery 保留各自账本。共享权威 RFC 已明确
这些账本的组合不等于一张 coordination aggregate。

现有 `tests/control_plane_ts` conformance 测试族与质量指南已覆盖有价值的失败、
重放和真实后端。它们可被复用，但不能证明全部交错执行或端到端活性。语义词表
注册表检查有界源码性质，声明不变量不等于执行时序证明。

## 5. 提议架构

### 归属与权威

每个选中领域在现有合同与验证矩阵中指明状态／决策 owner、接受的命令或观察、
前置条件、effect、提交点、receipt 和下一消费者。不得把生产转换实现复制进模型
作为预期真相。

实现分三种责任：

1. 将外部和历史数据解码为合法领域值。
2. 从不可变 snapshot、command 与显式的 authority 事实进行决策，返回 typed
   decision 或 effect 描述，不执行 IO。
3. 在既有权威边界执行，重验相关前置条件，持久提交，再把 receipt 交给下一决策。

Schema 合法和纯决策都不能证明 snapshot 当前有效。锁、CAS、source witness 和
provider acceptance 继续由各自执行边界负责。

### 状态模型与 schema

使用[状态分类](../../product/core-control-plane/state-definitions.md)：持久领域状态、
派生决策、执行／结算阶段及读取投影。独立维度用积类型，受约束的互斥分支用和
类型。不得为了测试方便而持久化可推导 flag。

测试模型只抽象所选不变量需要的身份、状态与依赖，声明有限 actor／resource 数量、
轨迹上限、故障和省略的行为。模型动作绑定公开命令或生产事务入口，转换后独立
回读。本 RFC 不要求增加生产记录或持久字段。

### 命令与恢复生命周期

在所选旅程支持的范围内，至少覆盖：dispatch 前拒绝、commit 前中断、提交后丢
响应、精确重试、冲突重试、lease 转移／过期、effect 执行中取消、重启与展示延迟。

取消按领域合同阻止后续获准 dispatch，不抹掉可能已提交的外部结果。恢复沿用
同一 operation identity。Unknown 必须 reconcile，不能换身份或盲目重试。
纯决策回放、receipt 恢复和假设模拟遵循
[Effect Interpreter](agent-loop-effect-interpreter-v0.zh-CN.md) 的独立合同。

### 检测、控制与恢复证据

下表是组合情形，不是新的共享错误枚举或生产事件账本。可选评估遵循
[有界检测合同](optional-semantic-assistance-jev-v0.zh-CN.md#检测结果与所属规则的衔接)，
受影响证据的当前使用遵循[对齐 §3.8](shared-goal-alignment-and-governed-amendment-v0.zh-CN.md#38-失效证据与受影响消费者)。

| 观察与所属证据 | 可采取的控制 | 恢复需要的证据 |
| --- | --- | --- |
| 身份、权限或 lease 检查失败 | 拒绝新的受保护执行/提交；仅按已资格化 Host 合同停止所拥有进程 | 当前合法 owner 与有效前提；旧输出不授予新权限 |
| 类型化瞬时 Host 故障 | 既有有限重试政策、同一逻辑 intent 与已检查次数预算 | 当前准入与已解决的效果不确定性；退避不证明修复 |
| 独立 task/acceptance 检查发现具名 criterion 失败 | 阻断该范围的成功验收/结算；保留失败 candidate 与原身份 | 实际修复或合法重规划，再按当前依据检查 criterion 及声明输入 |
| Validator 不可用、无定论或必要输入不可读 | 保留证据缺失与既有 fail-closed gate | 取得可用证据；无法检查不等于内容为假 |
| Observer/模型怀疑偏离 | Advisory，或显式启用的既有 replan 路径 | 领域调查/当前验证；信心或未触发信号不解除已确认失败 |
| 显式消费依据失效/不可用 | 在所属依赖/验收边界拒绝新的当前使用；保留历史与无关工作 | 当前获准来源与 consumer eligibility；语义失败已确认时另需 criterion 重验 |
| Provider 效果未知 | 保留 intent，阻断后续效果 | 同 operation 的权威读回；不能生成替代身份 |

每条恢复轨迹保留：被质疑的 criterion/前提、source 与 validator basis、受影响
Goal/Todo/Turn/artifact/effect 身份、已提交或未知效果、获准下一步及解除限制所需
检查。复用已有记录与字段，不设计通用 wire packet；必要扩展归真实 producer 和
consumer 所有。

JSON 与 hash 正确的错误计算结果，仍不满足计算正确的 criterion；文件存在不能
验收更强的主张。声明 verifier 须绑定版本、范围与当前来源，悄悄放宽检查不算修复。
不可执行的 criterion 应指明获授权评审与决定性证据，不能把另一个模型的信心当
证明。在会产生后果的验收/使用前及相关依据变化后检查，并明确成本和覆盖限制；
不要求验证每个隐藏推理步骤。发现较晚时，区分被阻止、已经提交和仍未知的效果。
本地恢复不能隐式补偿其他 provider 的效果。

```text
观察 + 声明的 criterion/source basis
  → 所属检查：失败、证据不足或有界疑虑
  → 原身份下的有范围准入/验收决定
  → 获授权修复、重规划、接管或同 operation 对账
  → 当前验证 + 未解决效果读回
  → 继续合格工作，或保留可见的有范围限制
```

箭头组合既有 owner，不创建全局状态机。接管尚未完成的修复任务，不要求先通过
完成 validator；要求当前归属、如实传递失败/未知事实和既有执行准入。声明恢复
之前，接收者须独立满足受影响的完成/使用条件。

最近可信恢复点是**具名且经过验证的依据**：身份、声明产物/来源版本、适用验证与
效果 receipt。若只保存阶段 journal，就只能声明阶段恢复；产物、版本或验证范围
缺失时，应明确限制，在既有权限内重建和重验。这不等于自动回滚 workspace 或
任意外部状态。Replan 接受、context 送达、流程继续和业务恢复保持独立。

### 安全性与有条件推进

每个被探索前缀都必须满足安全性。推进断言显式写明适用前提：provider 最终可用、
当前合法 owner、足够预算、最终送达、公平获得运行机会等。用户 gate 或永久不可用
provider 会使相应前提不成立；必须暴露这个事实，不能判定为成功运行。

有界探索可以在声明的调度内验证完成或找到反例，不证明无界最终推进。形式活性
结论需要带公平性前提的模型与证明，以及明确的实现一致性论证。

### Provider 合同

相同语义场景在每个受影响且声称支持的 backend 上通过真实 durability boundary
执行。各 provider 的 crash／ambiguous 注入方式可以不同。遵循真实后端门禁；
内存 store 不资格化 PostgreSQL、SQLite 或 File。外部效果保证仍由
[provider acceptance](provider-effect-acceptance-v0.zh-CN.md) 拥有。

## 6. 备选与选择

保留案例回归，在已证实缺口附近增加序列探索。通用模型扩大状态空间并模糊 owner；
只有局部测试则会漏掉跨域义务。小模型与生产 adapter 形成可审阅的中间边界。
只有在能降低所选切片验证成本时，才引入模型测试库或形式检查器。

## 7. 安全、隐私与兼容

使用合成隔离 Goal、可丢弃 store 与受控 provider；不需修改活跃 Goal、实际对外
通知或付费模型调用。公开反例只包含有界合成事实，不含用户 transcript。保留 wire
版本、omitted/null/clear 区别、receipt identity 与 feature-off 行为。改变这些
语义仍需既有 owner review，测试模型不能授权。

## 8. 迁移与回滚

先定义独立不变量，再用同一真实入口比较 pinned base/head，切换一个完整 owner
并删除重复规则，只保留必要兼容 reader。通过切片原有协议回滚；本 RFC 不授权
数据迁移。

## 9. 验证与验收

| 主张 | 证据 | 必须结果 | 边界 |
| --- | --- | --- | --- |
| 内部状态合法 | 编译期负例与 decoder 测试 | 拒绝非法组合，保留支持的 wire 输入 | 类型不等于权限 |
| 决策确定 | 同一可信 facts/command，加输入隔离检查 | 决策相同，可观察输入不变 | 所选纯内核不 IO、不读时钟 |
| 组合安全 | 独立模型上的有界动作／故障序列 | 无 stale commit、重复扣记或虚构 receipt | 声明探索值域及省略项 |
| 恢复推进 | 依赖可用且公平的具名调度 | 原工作恢复或进入合同定义的可见 gate | 不声称无界活性 |
| 实现一致 | pinned base/head 生产入口轨迹及独立持久回读 | 保留预期语义，有意变化独立论证 | 必须真实受影响 backend |
| 测试敏感 | 历史缺陷或刻意语义 mutation | 修复前／注入后失败，修复后通过 | 不从候选输出生成预期 |
| 用户接续 | 受影响 CLI 与打包 App/Lark 旅程 | 状态、下一动作和原上下文结果真实 | 未覆盖入口明确未资格化 |
| 业务错误控制 | 身份/hash 合法，但内容违反声明 criterion | 独立失败阻断该范围新的成功验收/使用 | 仅发现报文格式非法不够 |
| 恢复依据 | 检查与恢复之间产物、verifier、criterion 或 source 改变 | 旧成功不验收新依据；未知效果仍须读回 | 不声称任意文件/外部效果回滚 |

在既有 PR 证据中记录 seed 或确定性枚举、轨迹上限、归一化观察、失败／跳过数和
最小反例。缩减不得丢掉触发故障的因果前提。只长期保留保护持久行为的反例；
一次性 base/head 比较设施在兼容价值结束后退出。

## 10. 运维契约

不增加服务或操作步骤。现有状态／投影合同暴露 pending work、unknown effect
和投递失败。资格报告绑定精确实现与 backend；模型通过不改变 runtime profile
的晋升状态。

## 11. 规范性交付计划

| 里程碑 | 交付 | 前置 | 出口 | 回滚 |
| --- | --- | --- | --- | --- |
| M1 | 一条 typed decision／settlement 边界拒绝非法状态 | 真实 caller 与独立不变量 | 类型、decoder 兼容及公开路径证据；不声称完整组合 | 撤销有界替换 |
| M2 | ownership → writeback → settlement 恢复序列 | M1、具名真实 backend 与故障接缝 | 有界轨迹、敏感性、重放／转移／崩溃回读 | 保留持久回归，撤销规则变更 |
| M3 | successor 调度与原上下文送达 | 真实 scheduler／delivery caller 与 M2 合同 | 有条件推进及受影响打包 UI/CLI/Lark 证据 | 既有 delivery 回滚 |

执行沿用领域任务与总路线 checkpoint，不要求无关迁移先于有界修复完成。

[Monitor 静默到到期 checkpoint](ledger/typescript-control-plane-migration-v0/2026-10-02-monitor-quiet-due-recovery.zh-CN.md)
记录有界 CLI 重放／successor 证据，并明确 M2／M3 尚未覆盖的部分。

### Turn 结算资格范围

Turn owner 现在先由 TypeScript 判断 provider 返回值和回读，再允许 Python
checkpoint、撤销被拒绝的尝试或重试已确认 absent 的 effect。
`settlement_provider.ts` 在既有 Turn 领域内持有纯规则；Python 保留 callback
调用和 journal IO。原先的提交／回读分类分支和 completion 结果包装器随迁移删除，不新增 capability、
provider 或通用 effect executor。

有界探索覆盖三个顺序 effect（writeback、quota spend、terminal closeout），
在 effect 提交或 checkpoint 前后中断一次，经历一次 unresolved 回读等待，
再显式重试 failed turn、解析结果并精确重放。
`tests/test_loopx_turn_settlement_recovery.py` 使用生产 Turn 入口、真实 File
journal 和持久化的**合成** provider ledger。独立预期要求每个逻辑 effect
只提交一次、阶段有序、未知期间保留 prepared、不重复运行 host，并在回读明确后
有条件推进。四个非法 completion／identity 用例在改动前的固定版本上复现了
先 checkpoint 后校验的问题。

`tests/test_loopx_turn_driver.py` 还在隔离 File 状态中使用真实 CLI writeback
和 quota provider，注入 journal checkpoint 丢失，并独立回读 run index。
TypeScript 测试按步骤枚举合法／非法证据。异常模拟进程中断，不证明断电持久性。
这些检查限定于 Turn／provider／journal；lease 转移、过期 owner 竞争、
PostgreSQL authority、successor 调度和 App/Lark 送达尚未覆盖。因此 M2 仍开放；
后续沿用现有 ownership-to-settlement 验收，在真实 lease／GoalRef fence 下
补齐，再进入 M3 的实际送达 caller。

### 基于源码的实现顺序

以下是既有 M2/M3 与路线图 R2/R3/R4 中尚未实现的组合增量，不新增里程碑。
Source owner 在 `44931b6d22a50b949d43354e6ea498fb6b68d231` 复核。

| 有界结果 | 既有入口与 owner | 当前缺口与决定性出口 |
| --- | --- | --- |
| 声明来源链不可用时，阻止新的依赖使用 | `Delegations._read_current/start`、`delegation_results.require_dependencies/adoption_evidence`、`delegation.ts` | 遵循对齐 §3.8：source → A → B 中 A input 失效，即使 A output 不变也须拒绝当前依赖使用；验证真实 read/start/adopt/settlement 和打包证据读回，保留历史完成。 |
| 独立检查失败进入原任务可操作的恢复旅程 | `executor._task_validation_stage`、`ValidatedTurnReceipt`、canonical `turn_loop_controller_contract_v0.json`、`turn_journal.ts` | 传递已资格化失败范围与 repair/replan 细节；完成有界修复或只运行 verifier 的重试、当前验证与原效果结算。Host 声明不能冒充可信验证。 |
| 可选语义审查说明证据和覆盖范围 | 既有 progress-review receipt/loader 与 canonical Goal acceptance inspect | 在 shadow 读回绑定选中 criterion 和证据覆盖；显示缺失/陈旧依据及独立判断维度。模型质量和干预另行资格化。 |

第二项中，`_task_validation_stage` 已保存独立结果并阻断结算；`ValidatedTurnReceipt`
未携带该验证的 `recovery_kind`，canonical controller 有意将 legacy
`validation_failed` 统一映射 generic repair。保留可信 validator 的 replan 请求是
须披露的合同扩展，不是当前实现违反规则。修改 canonical 合同、
`scripts/generate_turn_contract.py` 与 `loopx/semantics/vocabulary_v0.json` 中
既有 `validation_failed`/`repair` 定义，旧记录缺少 detail 时继续 generic repair；
不手改 generated code，不新增平行 Python 决策源。选择 wire 变更前审查缺失、非法、
矛盾细节以及新旧 reader 兼容。

`validation_stage=task_postcondition` 的 failed-Turn retry 已会复用缓存 Host
result，只重跑 validation，不再次调用 Host。**实际修复工作**需要自己的当前有界
执行准入，然后复验。Acceptance owner 已提供 criterion/verifier pins 时复用它们；
未保留验证依据的 generic command 不能仅因退出码为零就成为持久业务 checkpoint。
替代检查不能抹掉旧的 unknown effect。

先在既有 fixture 中建立独立反例，再用真实 validator 命令与一次性受支持 backend
验证生产入口。覆盖证据缺失、verifier/source 过期、同 operation 重放、修复再失败、
停止/接管与 effect 响应丢失。Todo/Turn 视图须显示失败检查和范围，支持获授权
修复/复验，并在打包 App 读回成功或继续失败；CLI 与受影响 Lark 入口共用 owner。
复制命令按钮或后端 receipt 不能独自完成旅程。用实际测量限制重复验证和来源链遍历
成本；经既有 owner 回退代码，同时保留 receipt、已提交效果和未解决恢复义务。

## 12. 未决事项

首个 M2 实现由测试与领域维护者选择足够小的探索方法及边界。优先复用 fixture
和确定性枚举；引入依赖要有可重放反例和成本测量支持。这里不预选通用工具，也
不要求全仓执行统一的状态空间预算。
